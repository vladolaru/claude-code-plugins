#!/usr/bin/env python3
"""PreToolUse safety hook for YOLO mode.

Evaluates tool calls against safety rules in order:
  allowlist → block (exit 2) → ask (JSON permissionDecision) → allow (exit 0)

Reads from stdin: JSON with tool_name and tool_input.
Outputs: nothing (allow), stderr message (block, exit 2), or JSON (ask).
Fails open on any error (exit 0).
"""

import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULTS = {
    "credential_patterns": [
        r"\.env(?!\.sample|\.example|\.template)",
        r"client_secret\.json",
        r"\.credentials\.json",
        r"token\.pickle",
        r"\.pem$",
        r"id_rsa",
        r"id_ed25519",
        r"\.key$",
    ],
    "credential_safe_patterns": [
        r"\.env\.sample",
        r"\.env\.example",
        r"\.env\.template",
    ],
    "zero_access_paths": [
        "~/.ssh/",
        "~/.gnupg/",
    ],
    "disable_rules": [],
}

USER_CONFIG_PATH = os.path.expanduser("~/.claude/yoloing-safe.json")


def load_config():
    """Load config: user file overrides present keys, others keep defaults."""
    config = dict(DEFAULTS)
    try:
        with open(USER_CONFIG_PATH) as f:
            user = json.load(f)
        for key in DEFAULTS:
            if key in user:
                config[key] = user[key]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return config


# ---------------------------------------------------------------------------
# Command Normalization
# ---------------------------------------------------------------------------

def normalize_command(cmd):
    """Strip absolute path prefix from command name and collapse whitespace."""
    if not cmd:
        return ""
    # Strip leading absolute path from the command binary only
    # e.g., /usr/bin/git → git, but rm /home/user/bin/rm stays unchanged
    normalized = re.sub(r"^(?:/usr/local/bin/|/usr/bin/|/bin/|/sbin/|/usr/sbin/)", "", cmd)
    # Also handle `command` builtin prefix: `command rm` → `rm`
    normalized = re.sub(r"^command\s+", "", normalized)
    # Collapse whitespace
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


# ---------------------------------------------------------------------------
# Message Catalog
# ---------------------------------------------------------------------------

BLOCK_MESSAGES = {
    "destructive_deletion": "Use targeted file removal instead of recursive force-delete. Remove specific files by name (`rm file1 file2`), or use `git clean --dry-run` to preview before cleaning. This protects against accidental data loss.",
    "chained_deletion": "Command chaining can hide destructive operations. Run each command separately so the intent is clear. Remove files by name rather than piping into `rm`.",
    "alternative_deletion": "Indirect deletion methods (`find -delete`, `xargs rm`) can affect more files than intended. Use explicit file paths for removal, or `git clean --dry-run` to preview what would be deleted.",
    "disk_formatting": "Disk formatting and raw device writes (`mkfs`, `dd`) are irreversible system-level operations. These should only be run manually with explicit user intent, never by an agent.",
    "network_exfiltration": "Piping data to external URLs can expose sensitive information. Write output to a local file instead, then review before sharing. Use `git push` for code and `gh` for GitHub interactions.",
    "credential_access": "This file may contain secrets or credentials. Use `.env.example` or `.env.template` for reference files. If you need to read configuration, ask the user to provide the specific values.",
    "package_publishing": "Publishing packages to a registry is irreversible and public. Build and test locally, then let the user publish manually or through CI/CD. Use `--dry-run` to preview what would be published.",
    "ssh_remote_destruction": "Executing destructive commands on remote hosts via SSH can cause irreversible damage to production systems. Run remote commands manually with explicit user intent.",
    "github_repo_deletion": "Deleting a GitHub repository is irreversible and destroys all issues, PRs, and history. This should only be done manually through the GitHub UI or CLI by the user.",
    "zero_access_paths": "This path contains sensitive system or security data that should not be accessed by an agent. Ask the user to provide the specific information you need.",
}

ASK_MESSAGES = {
    "git_force_push": "Force push rewrites remote history and can discard teammates' work. Use `--force-with-lease` for a safer alternative. Confirm this is intentional.",
    "git_hard_reset": "Hard reset discards uncommitted changes permanently. Use `git stash` first to preserve work. Confirm you want to proceed.",
    "git_discard_changes": "This discards uncommitted changes to working tree files. Use `git stash` first if you might need them. Confirm you want to proceed.",
    "git_destroy_stash": "Dropping or clearing stashes permanently destroys saved work. List stashes with `git stash list` first. Confirm this is intentional.",
    "git_history_rewrite": "Rewriting git history (`filter-branch`, `filter-repo`) is irreversible on shared branches. Confirm this is intentional.",
    "git_config_changes": "Global or system git config changes affect all repositories on this machine. Confirm this is intentional.",
    "git_other_dangerous": "This git operation can cause data loss or affect collaboration. Confirm you want to proceed.",
    "permission_changes": "Broad permission changes can create security vulnerabilities. Confirm this is the minimum permission needed.",
    "brew_commands": "Installing system packages changes your development environment. Confirm you want to proceed, or consider adding the dependency to your project's package manager instead.",
    "docker_destructive": "This Docker command removes containers, volumes, or cached data that may be difficult to rebuild. Confirm you want to proceed.",
    "database_destructive": "This command permanently deletes database objects or data. Use a transaction with `BEGIN`/`ROLLBACK` to preview, or run against a dev database first. Confirm this is intentional.",
    "terraform_destructive": "This infrastructure command can destroy live resources. Use `--dry-run` or `plan` first to preview changes. Confirm this is intentional.",
    "github_cicd_ops": "Deleting GitHub secrets, variables, or disabling workflows affects CI/CD for all collaborators. Confirm this is intentional.",
}

# ---------------------------------------------------------------------------
# Allowlist Patterns — checked first to prevent false positives
# Each entry: (rule_id, compiled_regex)
# rule_id ties to the category so disabling a rule also disables its allowlist
# ---------------------------------------------------------------------------

ALLOWLIST_PATTERNS = [
    # Git branch creation (not file checkout)
    ("git_discard_changes", re.compile(r"^git checkout (-b|--orphan) ")),
    # Git unstage only (--staged/-S without --worktree/-W)
    ("git_discard_changes", re.compile(r"^git restore\b.*(?:--staged|-S)(?!.*(?:--worktree|-W))")),
    # Git clean dry-run (handles combined flags like -fn, -nfd, etc.)
    ("git_other_dangerous", re.compile(r"^git clean\b.*(--dry-run|-[a-zA-Z]*n)")),
    # Git push with safe force variants
    ("git_force_push", re.compile(r"^git push\b.*--force-(with-lease|if-includes)")),
    # rm in temp directories
    ("destructive_deletion", re.compile(r"^rm\s+-[rfRF]*\s+(/tmp/|/var/tmp/|\$TMPDIR/)")),
    # chmod +x (make executable)
    ("permission_changes", re.compile(r"^chmod \+x\b")),
    # Dry-run publishing
    ("package_publishing", re.compile(r"^npm publish\b.*--dry-run")),
    ("package_publishing", re.compile(r"^twine check\b")),
]

# ---------------------------------------------------------------------------
# Detection Functions
# Each returns (detected: bool, message: str | None)
# Signature: (command, tool_name, tool_input, config) -> (bool, str|None)
# ---------------------------------------------------------------------------

# --- Block Tier: Filesystem Destruction ---

def detect_destructive_deletion(command, tool_name, tool_input, config):
    """Detect rm -rf and variants."""
    if not re.search(r"\brm\b", command):
        return False, None
    # Must have both recursive and force flags
    has_recursive = bool(re.search(r"(?:^|\s)-[a-zA-Z]*[rR]|--recursive", command))
    has_force = bool(re.search(r"(?:^|\s)-[a-zA-Z]*[fF]|--force", command))
    if has_recursive and has_force:
        return True, BLOCK_MESSAGES["destructive_deletion"]
    return False, None


def detect_chained_deletion(command, tool_name, tool_input, config):
    """Detect rm hidden in command chains (&&, ;, ||)."""
    if not re.search(r"(&&|;|\|\|)", command):
        return False, None
    # Split on chain operators and check each segment after the first
    segments = re.split(r"&&|;|\|\|", command)
    for segment in segments[1:]:
        segment = segment.strip()
        # Normalize the segment too
        segment = normalize_command(segment)
        if re.search(r"\brm\b", segment):
            return True, BLOCK_MESSAGES["chained_deletion"]
    return False, None


def detect_alternative_deletion(command, tool_name, tool_input, config):
    """Detect find -delete, find -exec rm, xargs rm, eval rm."""
    # find -delete
    if re.search(r"\bfind\b.*-delete\b", command):
        return True, BLOCK_MESSAGES["alternative_deletion"]
    # find -exec rm
    if re.search(r"\bfind\b.*-exec\s+rm\b", command):
        return True, BLOCK_MESSAGES["alternative_deletion"]
    # xargs rm
    if re.search(r"\bxargs\s+rm\b", command):
        return True, BLOCK_MESSAGES["alternative_deletion"]
    # eval with rm
    if re.search(r"\beval\b.*\brm\b", command):
        return True, BLOCK_MESSAGES["alternative_deletion"]
    return False, None


def detect_disk_formatting(command, tool_name, tool_input, config):
    """Detect mkfs, dd to device."""
    if re.search(r"\bmkfs\b", command):
        return True, BLOCK_MESSAGES["disk_formatting"]
    # dd writing to a device
    if re.search(r"\bdd\b", command) and re.search(r"of=/dev/", command):
        return True, BLOCK_MESSAGES["disk_formatting"]
    return False, None


# --- Block Tier: Network, Credentials, Publishing, SSH, GitHub, Paths ---

def detect_network_exfiltration(command, tool_name, tool_input, config):
    """Detect data exfiltration via curl, wget, nc."""
    # curl posting data (file or stdin)
    if re.search(r"\bcurl\b.*(-d\s+@|--data\s+@|-X\s+POST)", command):
        return True, BLOCK_MESSAGES["network_exfiltration"]
    # wget posting a file
    if re.search(r"\bwget\b.*--post-file", command):
        return True, BLOCK_MESSAGES["network_exfiltration"]
    # Piping to nc
    if re.search(r"\|\s*nc\b", command) or re.search(r"\bnc\b.*<", command):
        return True, BLOCK_MESSAGES["network_exfiltration"]
    # Piping curl/wget output to bash/sh (remote code execution)
    if re.search(r"\b(curl|wget)\b.*\|\s*(bash|sh|zsh)\b", command):
        return True, BLOCK_MESSAGES["network_exfiltration"]
    return False, None


def detect_credential_access(command, tool_name, tool_input, config):
    """Detect access to credential files via Read/Edit/Write tools or Bash."""
    cred_patterns = config.get("credential_patterns", DEFAULTS["credential_patterns"])
    safe_patterns = config.get("credential_safe_patterns", DEFAULTS["credential_safe_patterns"])

    # Get the file path to check
    file_path = ""
    if tool_name in ("Read", "Edit", "Write"):
        file_path = tool_input.get("file_path", "")
    elif tool_name == "Bash" and command:
        # Check if bash command accesses credential files
        file_path = command

    if not file_path:
        return False, None

    # Check safe patterns first
    for safe_pat in safe_patterns:
        if re.search(safe_pat, file_path):
            return False, None

    # Check credential patterns
    for cred_pat in cred_patterns:
        if re.search(cred_pat, file_path):
            return True, BLOCK_MESSAGES["credential_access"]

    return False, None


def detect_package_publishing(command, tool_name, tool_input, config):
    """Detect package publishing commands."""
    # npm publish (without --dry-run)
    if re.search(r"\bnpm publish\b", command) and not re.search(r"--dry-run", command):
        return True, BLOCK_MESSAGES["package_publishing"]
    # twine upload / pip upload
    if re.search(r"\b(twine|pip) upload\b", command):
        return True, BLOCK_MESSAGES["package_publishing"]
    # gem push
    if re.search(r"\bgem push\b", command):
        return True, BLOCK_MESSAGES["package_publishing"]
    return False, None


def detect_ssh_remote_destruction(command, tool_name, tool_input, config):
    """Detect destructive commands executed remotely via SSH."""
    if not re.search(r"^ssh\b", command):
        return False, None
    # Look for quoted remote commands containing destructive operations
    remote_cmd_match = re.search(r"""ssh\s+\S+\s+['"](.+?)['"]""", command)
    if not remote_cmd_match:
        return False, None
    remote_cmd = remote_cmd_match.group(1).lower()
    destructive = ["rm ", "rm\t", "drop ", "truncate ", "delete ", "mkfs", "dd "]
    for pattern in destructive:
        if pattern in remote_cmd:
            return True, BLOCK_MESSAGES["ssh_remote_destruction"]
    return False, None


def detect_github_repo_deletion(command, tool_name, tool_input, config):
    """Detect gh repo delete."""
    if re.search(r"\bgh repo delete\b", command):
        return True, BLOCK_MESSAGES["github_repo_deletion"]
    return False, None


def detect_zero_access_paths(command, tool_name, tool_input, config):
    """Detect access to zero-access paths (e.g., ~/.ssh/, ~/.gnupg/)."""
    zero_paths = config.get("zero_access_paths", DEFAULTS["zero_access_paths"])

    # Get the path to check
    paths_to_check = []
    if tool_name in ("Read", "Edit", "Write"):
        fp = tool_input.get("file_path", "")
        if fp:
            paths_to_check.append(fp)
    if tool_name == "Bash" and command:
        paths_to_check.append(command)

    for check_path in paths_to_check:
        for zero_path in zero_paths:
            if zero_path in check_path:
                return True, BLOCK_MESSAGES["zero_access_paths"]

    return False, None


# --- Ask Tier: Git Operations ---

def detect_git_force_push(command, tool_name, tool_input, config):
    """Detect git push --force (but not --force-with-lease or --force-if-includes)."""
    if not re.search(r"^git push\b", command):
        return False, None
    # Must have --force or -f, but not --force-with-lease or --force-if-includes
    if re.search(r"--force-with-lease|--force-if-includes", command):
        return False, None
    if re.search(r"(--force\b|-f\b)", command):
        return True, ASK_MESSAGES["git_force_push"]
    return False, None


def detect_git_hard_reset(command, tool_name, tool_input, config):
    """Detect git reset --hard or --merge."""
    if not re.search(r"^git reset\b", command):
        return False, None
    if re.search(r"--hard\b", command):
        return True, ASK_MESSAGES["git_hard_reset"]
    if re.search(r"--merge\b", command):
        return True, ASK_MESSAGES["git_hard_reset"]
    return False, None


def detect_git_discard_changes(command, tool_name, tool_input, config):
    """Detect git checkout -- and git restore that discards working tree changes."""
    # git checkout -- <path> or git checkout <ref> -- <path>
    if re.search(r"^git checkout\b", command) and re.search(r"\s--\s", command):
        return True, ASK_MESSAGES["git_discard_changes"]
    # git restore (without --staged/-S alone — that's allowlisted)
    if re.search(r"^git restore\b", command):
        has_staged = bool(re.search(r"(--staged|-S)\b", command))
        has_worktree = bool(re.search(r"(--worktree|-W)\b", command))
        # If only --staged (no --worktree), it's safe (allowlisted)
        if has_staged and not has_worktree:
            return False, None
        # Otherwise it touches the worktree — dangerous
        return True, ASK_MESSAGES["git_discard_changes"]
    return False, None


def detect_git_destroy_stash(command, tool_name, tool_input, config):
    """Detect git stash drop/clear."""
    if re.search(r"^git stash (drop|clear)\b", command):
        return True, ASK_MESSAGES["git_destroy_stash"]
    return False, None


def detect_git_history_rewrite(command, tool_name, tool_input, config):
    """Detect git filter-branch/filter-repo."""
    if re.search(r"^git filter-(branch|repo)\b", command):
        return True, ASK_MESSAGES["git_history_rewrite"]
    return False, None


def detect_git_config_changes(command, tool_name, tool_input, config):
    """Detect git config --global or --system."""
    if not re.search(r"^git config\b", command):
        return False, None
    if re.search(r"--(global|system)\b", command):
        return True, ASK_MESSAGES["git_config_changes"]
    return False, None


def detect_git_other_dangerous(command, tool_name, tool_input, config):
    """Detect other dangerous git ops: clean -f, branch -D, remote remove, reflog expire, gc --prune."""
    # git clean with -f (force) but without -n/--dry-run (allowlisted)
    if re.search(r"^git clean\b", command):
        if re.search(r"-[a-zA-Z]*f", command) and not re.search(r"(-[a-zA-Z]*n|--dry-run)", command):
            return True, ASK_MESSAGES["git_other_dangerous"]
    # git branch -D (force delete)
    if re.search(r"^git branch\s+-D\b", command):
        return True, ASK_MESSAGES["git_other_dangerous"]
    # git remote remove
    if re.search(r"^git remote remove\b", command):
        return True, ASK_MESSAGES["git_other_dangerous"]
    # git reflog expire
    if re.search(r"^git reflog expire\b", command):
        return True, ASK_MESSAGES["git_other_dangerous"]
    # git gc --prune=now
    if re.search(r"^git gc\b.*--prune=", command):
        return True, ASK_MESSAGES["git_other_dangerous"]
    return False, None


# Rule registry: (rule_id, tier, detect_fn)
RULE_REGISTRY = [
    ("destructive_deletion", "block", detect_destructive_deletion),
    ("chained_deletion", "block", detect_chained_deletion),
    ("alternative_deletion", "block", detect_alternative_deletion),
    ("disk_formatting", "block", detect_disk_formatting),
    ("network_exfiltration", "block", detect_network_exfiltration),
    ("credential_access", "block", detect_credential_access),
    ("package_publishing", "block", detect_package_publishing),
    ("ssh_remote_destruction", "block", detect_ssh_remote_destruction),
    ("github_repo_deletion", "block", detect_github_repo_deletion),
    ("zero_access_paths", "block", detect_zero_access_paths),
    # Ask tier — git operations
    ("git_force_push", "ask", detect_git_force_push),
    ("git_hard_reset", "ask", detect_git_hard_reset),
    ("git_discard_changes", "ask", detect_git_discard_changes),
    ("git_destroy_stash", "ask", detect_git_destroy_stash),
    ("git_history_rewrite", "ask", detect_git_history_rewrite),
    ("git_config_changes", "ask", detect_git_config_changes),
    ("git_other_dangerous", "ask", detect_git_other_dangerous),
]


def is_allowlisted(command):
    """Check if command matches any allowlist pattern."""
    for _rule_id, pattern in ALLOWLIST_PATTERNS:
        if pattern.search(command):
            return True
    return False


# ---------------------------------------------------------------------------
# Output Helpers
# ---------------------------------------------------------------------------

def block(message):
    """Block the tool call: exit 2 with message on stderr."""
    print(message, file=sys.stderr)
    sys.exit(2)


def ask(message):
    """Ask for confirmation: exit 0 with JSON on stdout."""
    output = {
        "hookSpecificOutput": {
            "permissionDecision": "ask",
            "systemMessage": message,
        }
    }
    print(json.dumps(output))
    sys.exit(0)


def allow():
    """Allow the tool call: exit 0 silently."""
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        allow()

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        allow()

    config = load_config()
    disabled = set(config.get("disable_rules", []))

    # Extract command for Bash tool
    command = ""
    if tool_name == "Bash":
        command = normalize_command(tool_input.get("command", ""))

    # 1. Allowlist — check first to prevent false positives
    if command:
        for rule_id, pattern in ALLOWLIST_PATTERNS:
            if rule_id not in disabled and pattern.search(command):
                allow()

    # 2. Block / Ask — iterate rule registry
    for rule_id, tier, detect_fn in RULE_REGISTRY:
        if rule_id in disabled:
            continue
        detected, message = detect_fn(command, tool_name, tool_input, config)
        if detected:
            if tier == "block":
                block(message)
            elif tier == "ask":
                ask(message)

    # 3. Allow — everything else
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Fail open — don't block the agent on hook bugs
        sys.exit(0)

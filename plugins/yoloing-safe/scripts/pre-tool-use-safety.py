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
        r"id_ecdsa",
        r"\.key$",
        r"\.p12$",
        r"\.pfx$",
        r"\.jks$",
        r"\.keystore$",
    ],
    "credential_safe_patterns": [
        r"\.env\.sample",
        r"\.env\.example",
        r"\.env\.template",
    ],
    "zero_access_paths": [
        "~/.ssh/",
        "~/.gnupg/",
        "~/.aws/",
        "~/.config/gcloud/",
    ],
    "disable_rules": [],
}

USER_CONFIG_PATH = os.path.expanduser("~/.claude/yoloing-safe.json")

# Self-protection: these paths cannot be modified by Write/Edit.
# NOT configurable — hardcoded to prevent the agent from disabling the hook.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.dirname(_SCRIPT_DIR)
SELF_PROTECTED_PATHS = [
    USER_CONFIG_PATH,
    _PLUGIN_ROOT,
]


def load_config():
    """Load config: user file overrides present keys, others keep defaults."""
    config = dict(DEFAULTS)
    config_path = os.environ.get("YOLOING_SAFE_CONFIG_PATH", USER_CONFIG_PATH)
    try:
        with open(config_path) as f:
            user = json.load(f)
        for key in DEFAULTS:
            if key in user:
                config[key] = user[key]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    # Expand ~ in zero_access_paths so both tilde and absolute forms match
    expanded = []
    for p in config["zero_access_paths"]:
        expanded.append(p)
        ep = os.path.expanduser(p)
        if ep != p:
            expanded.append(ep)
    config["zero_access_paths"] = expanded
    return config


# ---------------------------------------------------------------------------
# Command Normalization
# ---------------------------------------------------------------------------

_WRAPPER_RE = re.compile(
    r"^(command|env|sudo|nice|nohup|time|exec|strace|ionice|taskset)\s+"
)


def normalize_command(cmd):
    """Strip path prefixes, command wrappers, and collapse whitespace."""
    if not cmd:
        return ""
    # Strip leading absolute path from the command binary only
    # e.g., /usr/bin/git → git, but rm /home/user/bin/rm stays unchanged
    normalized = re.sub(r"^(?:/usr/local/bin/|/usr/bin/|/bin/|/sbin/|/usr/sbin/)", "", cmd)
    # Strip command wrappers, looping for nesting (sudo env rm → rm)
    prev = None
    while prev != normalized:
        prev = normalized
        normalized = _WRAPPER_RE.sub("", normalized)
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
    "self_protection": "This file is part of the safety hook infrastructure. Modifying it could disable safety protections. Ask the user to make changes manually.",
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
    "sensitive_write_target": "This file controls shell behavior, git hooks, or package manager configuration. Modifying it can have persistent side effects beyond this session. Confirm this is intentional.",
    "inline_interpreter": "Inline interpreter execution can bypass command-level safety checks. Review the code being executed and confirm this is intentional.",
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
    # Split on chain operators and check each segment
    segments = re.split(r"&&|;|\|\|", command)
    for segment in segments:
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
    """Detect data exfiltration via curl, wget, nc, scp, rsync."""
    # curl posting data (file or stdin)
    if re.search(r"\bcurl\b.*(-d\s+@|--data\s+@|-X\s+POST)", command):
        return True, BLOCK_MESSAGES["network_exfiltration"]
    # curl file upload (-F form, -T upload, --upload-file)
    if re.search(r"\bcurl\b.*(-F\s+|--form\s+|-T\s+|--upload-file\s+)", command):
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
    # scp upload: last argument is remote destination (user@host:path)
    if re.search(r"\bscp\b.*\s\S+@\S+:\S*$", command):
        return True, BLOCK_MESSAGES["network_exfiltration"]
    # rsync upload: last argument is remote destination
    if re.search(r"\brsync\b.*\s\S+@\S+:\S*$", command):
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
    # For compound commands, check each segment independently
    # (prevents "npm publish --dry-run && npm publish" from being allowed
    #  because --dry-run appears in the first segment)
    segments = [command]
    if re.search(r"(&&|;|\|\|)", command):
        segments = [s.strip() for s in re.split(r"&&|;|\|\|", command)]
    for seg in segments:
        # npm publish (without --dry-run in this segment)
        if re.search(r"\bnpm publish\b", seg) and not re.search(r"--dry-run", seg):
            return True, BLOCK_MESSAGES["package_publishing"]
        # twine upload / pip upload
        if re.search(r"\b(twine|pip) upload\b", seg):
            return True, BLOCK_MESSAGES["package_publishing"]
        # gem push
        if re.search(r"\bgem push\b", seg):
            return True, BLOCK_MESSAGES["package_publishing"]
    return False, None


def detect_ssh_remote_destruction(command, tool_name, tool_input, config):
    """Detect destructive commands executed remotely via SSH."""
    if not re.search(r"^ssh\b", command):
        return False, None
    # Try quoted remote command first
    remote_cmd_match = re.search(r"""ssh\s+\S+\s+['"](.+?)['"]""", command)
    if remote_cmd_match:
        remote_cmd = remote_cmd_match.group(1).lower()
    else:
        # Unquoted: check everything after the ssh keyword
        # Catches: ssh host rm -rf /, ssh -t host rm -rf /, etc.
        parts = command.split(None, 1)
        remote_cmd = parts[1].lower() if len(parts) > 1 else ""
    if not remote_cmd:
        return False, None
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


# --- Ask Tier: Non-Git Categories ---

def detect_permission_changes(command, tool_name, tool_input, config):
    """Detect dangerous permission changes (chmod 777, setuid, recursive chown, sudo chmod)."""
    # chmod +x is safe (allowlisted), skip it
    if re.search(r"^chmod \+x\b", command):
        return False, None
    # sudo chmod — always flag (note: sudo is stripped by normalize, but
    # the pattern stays for defense in depth if normalization changes)
    if re.search(r"\bsudo\s+chmod\b", command):
        return True, ASK_MESSAGES["permission_changes"]
    # chmod 777 or setuid/setgid bits (4-digit octal starting with 4/2/6)
    if re.search(r"\bchmod\b", command):
        # 777 — world-writable
        if re.search(r"\bchmod\s+777\b", command):
            return True, ASK_MESSAGES["permission_changes"]
        # 4-digit octal with setuid (4), setgid (2), or both (6) prefix
        if re.search(r"\bchmod\s+[4267]\d{3}\b", command):
            return True, ASK_MESSAGES["permission_changes"]
        # Symbolic setuid/setgid
        if re.search(r"\bchmod\s+[ugo]*\+s\b", command):
            return True, ASK_MESSAGES["permission_changes"]
    # chown -R (recursive ownership change)
    if re.search(r"\bchown\s+-[a-zA-Z]*R", command):
        return True, ASK_MESSAGES["permission_changes"]
    return False, None


def detect_brew_commands(command, tool_name, tool_input, config):
    """Detect brew install/uninstall/upgrade/tap/link (not list/info/search/doctor)."""
    if not re.search(r"^brew\b", command):
        return False, None
    modifying = r"^brew\s+(install|uninstall|remove|upgrade|tap|untap|link|unlink)\b"
    if re.search(modifying, command):
        return True, ASK_MESSAGES["brew_commands"]
    return False, None


def detect_docker_destructive(command, tool_name, tool_input, config):
    """Detect destructive Docker commands."""
    # docker system/volume/image prune
    if re.search(r"\bdocker\s+(system|volume|image)\s+prune\b", command):
        return True, ASK_MESSAGES["docker_destructive"]
    # docker rm -f
    if re.search(r"\bdocker\s+rm\s+-[a-zA-Z]*f", command):
        return True, ASK_MESSAGES["docker_destructive"]
    # docker-compose down -v
    if re.search(r"\bdocker-compose\s+down\b.*-v\b", command):
        return True, ASK_MESSAGES["docker_destructive"]
    return False, None


def detect_database_destructive(command, tool_name, tool_input, config):
    """Detect destructive database commands."""
    cmd_upper = command.upper()
    # DROP DATABASE/TABLE
    if re.search(r"\bDROP\s+(DATABASE|TABLE|SCHEMA)\b", cmd_upper):
        return True, ASK_MESSAGES["database_destructive"]
    # TRUNCATE
    if re.search(r"\bTRUNCATE\b", cmd_upper):
        return True, ASK_MESSAGES["database_destructive"]
    # DELETE without WHERE
    if re.search(r"\bDELETE\s+FROM\b", cmd_upper) and not re.search(r"\bWHERE\b", cmd_upper):
        return True, ASK_MESSAGES["database_destructive"]
    # dropdb/dropuser CLI tools
    if re.search(r"\b(dropdb|dropuser)\b", command):
        return True, ASK_MESSAGES["database_destructive"]
    # redis FLUSHALL/FLUSHDB
    if re.search(r"\bredis-cli\b.*\bFLUSH(ALL|DB)\b", command):
        return True, ASK_MESSAGES["database_destructive"]
    return False, None


def detect_terraform_destructive(command, tool_name, tool_input, config):
    """Detect destructive Terraform/Pulumi commands."""
    if re.search(r"\bterraform destroy\b", command):
        return True, ASK_MESSAGES["terraform_destructive"]
    if re.search(r"\bterraform apply\b.*-auto-approve\b", command):
        return True, ASK_MESSAGES["terraform_destructive"]
    if re.search(r"\bpulumi destroy\b", command):
        return True, ASK_MESSAGES["terraform_destructive"]
    return False, None


def detect_github_cicd_ops(command, tool_name, tool_input, config):
    """Detect destructive GitHub CI/CD operations."""
    if re.search(r"\bgh\s+(secret|variable)\s+delete\b", command):
        return True, ASK_MESSAGES["github_cicd_ops"]
    if re.search(r"\bgh\s+workflow\s+disable\b", command):
        return True, ASK_MESSAGES["github_cicd_ops"]
    if re.search(r"\bgh\s+release\s+delete\b", command):
        return True, ASK_MESSAGES["github_cicd_ops"]
    return False, None


def detect_sensitive_write_target(command, tool_name, tool_input, config):
    """Detect Write/Edit to shell init files, git hooks, or package config."""
    if tool_name not in ("Write", "Edit"):
        return False, None
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return False, None
    # Normalize the path for matching
    normalized_path = os.path.expanduser(file_path)
    basename = os.path.basename(normalized_path)
    # Shell init files
    shell_init = {
        ".bashrc", ".bash_profile", ".bash_login", ".profile",
        ".zshrc", ".zprofile", ".zshenv", ".zlogin",
    }
    if basename in shell_init:
        return True, ASK_MESSAGES["sensitive_write_target"]
    # Git hooks directory
    if "/.git/hooks/" in normalized_path or normalized_path.endswith("/.git/hooks"):
        return True, ASK_MESSAGES["sensitive_write_target"]
    # Package manager / tool config — only in home directory
    # (project-level .npmrc/.yarnrc/.gitconfig are routine, not risky)
    sensitive_dotfiles = {".gitconfig", ".npmrc", ".yarnrc"}
    if basename in sensitive_dotfiles:
        home = os.path.expanduser("~")
        parent = os.path.dirname(normalized_path)
        if parent == home:
            return True, ASK_MESSAGES["sensitive_write_target"]
    return False, None


def detect_inline_interpreter(command, tool_name, tool_input, config):
    """Detect shell subshell execution (bash -c, sh -c, zsh -c).

    Only flags shell subshells — the actual evasion vector for bypassing
    command-level pattern matching. python3 -c, node -e, etc. are excluded
    because agents use them constantly for legitimate one-liners (JSON
    formatting, calculations, version checks) and the noise-to-signal ratio
    is too high. Interpreter-based attacks are documented as a known limitation.
    """
    if tool_name != "Bash":
        return False, None
    # bash/sh/zsh -c (subshell execution — the evasion vector)
    if re.search(r"\b(bash|sh|zsh)\s+-c\b", command):
        return True, ASK_MESSAGES["inline_interpreter"]
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
    # Ask tier — non-git
    ("permission_changes", "ask", detect_permission_changes),
    ("brew_commands", "ask", detect_brew_commands),
    ("docker_destructive", "ask", detect_docker_destructive),
    ("database_destructive", "ask", detect_database_destructive),
    ("terraform_destructive", "ask", detect_terraform_destructive),
    ("github_cicd_ops", "ask", detect_github_cicd_ops),
    ("sensitive_write_target", "ask", detect_sensitive_write_target),
    ("inline_interpreter", "ask", detect_inline_interpreter),
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

    # Self-protection: prevent Write/Edit to hook config or plugin files.
    # NOT configurable — cannot be disabled via disable_rules.
    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if file_path:
            try:
                abs_path = os.path.abspath(os.path.expanduser(file_path))
                for protected in SELF_PROTECTED_PATHS:
                    if abs_path == protected or abs_path.startswith(protected + os.sep):
                        block(BLOCK_MESSAGES["self_protection"])
            except (ValueError, OSError):
                pass

    # 1. Allowlist — check first, but SKIP for compound commands.
    #    Without this guard, "rm -rf /tmp/build && rm -rf /home" would
    #    match the temp-directory allowlist and bypass all block checks.
    if command and not re.search(r"(&&|;|\|\|)", command):
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

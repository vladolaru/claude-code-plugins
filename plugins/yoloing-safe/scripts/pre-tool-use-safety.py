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
# Detection Functions — placeholders, implemented in Tasks 4-7
# Each returns (detected: bool, message: str | None)
# ---------------------------------------------------------------------------

# Rule registry: (rule_id, tier, detect_fn)
# detect_fn signature: (command, tool_name, tool_input, config) -> (bool, str|None)
RULE_REGISTRY = []  # Populated in Tasks 4-7


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

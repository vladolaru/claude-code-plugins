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
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Profiling (zero-cost when disabled)
# Set YOLOING_SAFE_PROFILE=1 to print timing breakpoints to stderr.
# ---------------------------------------------------------------------------

_PROFILE = os.environ.get("YOLOING_SAFE_PROFILE") == "1"
_T0 = time.monotonic() if _PROFILE else 0


def _mark(label):
    if _PROFILE:
        elapsed_ms = (time.monotonic() - _T0) * 1000
        print(f"[yoloing-safe:profile] {label} {elapsed_ms:.3f}ms", file=sys.stderr)

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

_mark("module_loaded")

# Self-protection: these paths cannot be modified by Write/Edit.
# NOT configurable — hardcoded to prevent the agent from disabling the hook.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.dirname(_SCRIPT_DIR)
SELF_PROTECTED_PATHS = [
    USER_CONFIG_PATH,
    _PLUGIN_ROOT,
]


def _is_self_protected_path(file_path):
    """Check if a file path resolves to a self-protected location.

    Uses realpath to resolve symlinks, preventing symlink-based bypasses.
    """
    try:
        real_path = os.path.realpath(os.path.expanduser(file_path))
        for protected in SELF_PROTECTED_PATHS:
            protected_real = os.path.realpath(protected)
            if real_path == protected_real or real_path.startswith(protected_real + os.sep):
                return True
    except (ValueError, OSError):
        pass
    return False


# Pre-compiled patterns for Bash self-protection detection
_RE_SHELL_REDIRECT = re.compile(r"[12]?>")
_RE_BASH_WRITE_CMDS = re.compile(
    r"\b(cp|mv|tee|install|rsync)\b"
)
_RE_SED_INPLACE = re.compile(r"\bsed\b.*\s-i")


def _bash_targets_protected_path(command):
    """Check if a Bash command writes to a self-protected path.

    Detects shell redirects (>, >>), copy/move commands, tee, and sed -i
    that reference self-protected paths.
    """
    might_write = (
        _RE_SHELL_REDIRECT.search(command)
        or _RE_BASH_WRITE_CMDS.search(command)
        or _RE_SED_INPLACE.search(command)
    )
    if not might_write:
        return False
    # Check if any protected path appears in the command
    for protected in SELF_PROTECTED_PATHS:
        protected_real = os.path.realpath(protected)
        # Check both the raw path and expanded forms
        if protected in command or protected_real in command:
            return True
        # Also check with ~ unexpanded (e.g., ~/.claude/yoloing-safe.json)
        home = os.path.expanduser("~")
        if protected_real.startswith(home):
            tilde_form = "~" + protected_real[len(home):]
            if tilde_form in command:
                return True
    return False


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
    normalized = _RE_PATH_PREFIX.sub("", cmd)
    # Strip command wrappers, looping for nesting (sudo env rm → rm)
    prev = None
    while prev != normalized:
        prev = normalized
        normalized = _WRAPPER_RE.sub("", normalized)
    # Collapse whitespace
    normalized = _RE_WHITESPACE.sub(" ", normalized).strip()
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
    "git_bare_push": "Push with an explicit branch to avoid pushing to an unexpected target. Use `git push origin HEAD` to push the current branch, or `git push origin <branch-name>` for a specific branch.",
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
# Pre-compiled Regex Patterns
# ---------------------------------------------------------------------------

# Shared patterns (used by multiple detection functions)
_RE_RM = re.compile(r"\brm\b")
_RE_CHAIN_OPS = re.compile(r"(&&|;|\|\|)")
_RE_CHAIN_SPLIT = re.compile(r"&&|;|\|\|")

# Command normalization
_RE_PATH_PREFIX = re.compile(r"^(?:/usr/local/bin/|/usr/bin/|/bin/|/sbin/|/usr/sbin/)")
_RE_WHITESPACE = re.compile(r"\s+")

# ---------------------------------------------------------------------------
# Heredoc Stripping
# ---------------------------------------------------------------------------

# Matches heredoc opened by a file-writing command (cat >, cat >>, tee).
# Group 1: the writer command + heredoc opener line (including trailing \n)
# Group 2: the delimiter word (used to find closing line)
# Group 3: the closing delimiter line (including leading \n)
# Does NOT match interpreter consumers (bash, python3, mysql, etc.) — those
# are intentionally left visible for safety rule evaluation.
_RE_WRITER_HEREDOC = re.compile(
    r"((?:cat\s+>>?\s*\S+|tee\s+(?:-a\s+)?\S+)\s*<<\s*['\"]?(\w+)['\"]?\n)"
    r".*?"
    r"(\n\2\b)",
    re.DOTALL,
)


def strip_writer_heredocs(command: str) -> str:
    """Strip heredoc bodies when the consumer is cat > or tee (file writers).

    Interpreter heredocs (bash <<, python3 <<, mysql <<, etc.) are NOT touched —
    they remain visible to all safety rules and are flagged by detect_inline_heredoc.
    """
    if "<<" not in command:
        return command
    return _RE_WRITER_HEREDOC.sub(r"\1\3", command)


# Filesystem destruction
_RE_RECURSIVE_FLAG = re.compile(r"(?:^|\s)-[a-zA-Z]*[rR]|--recursive")
_RE_FORCE_DELETE_FLAG = re.compile(r"(?:^|\s)-[a-zA-Z]*[fF]|--force")
_RE_FIND_DELETE = re.compile(r"\bfind\b.*-delete\b")
# Scoped find roots: relative dot-paths, $TMPDIR, /tmp, /var/tmp
_RE_FIND_SCOPED_ROOT = re.compile(
    r"^find\s+(?:\.[\w./\-]|\$TMPDIR\b|\$TMP\b|/tmp/|/var/tmp/)"
)
_RE_FIND_EXEC_RM = re.compile(r"\bfind\b.*-exec\s+rm\b")
_RE_XARGS_RM = re.compile(r"\bxargs\s+rm\b")
_RE_EVAL_RM = re.compile(r"\beval\b.*\brm\b")
_RE_MKFS = re.compile(r"\bmkfs\b")
_RE_DD = re.compile(r"\bdd\b")
_RE_DD_OF_DEV = re.compile(r"of=/dev/")

# Network exfiltration
_RE_CURL_POST_DATA = re.compile(r"\bcurl\b.*(-d\s+@|--data\s+@|-X\s+POST)")
_RE_CURL_UPLOAD = re.compile(r"\bcurl\b.*(-F\s+|--form\s+|-T\s+|--upload-file\s+)")
_RE_WGET_POST_FILE = re.compile(r"\bwget\b.*--post-file")
_RE_PIPE_TO_NC = re.compile(r"\|\s*nc\b")
_RE_NC_REDIRECT = re.compile(r"\bnc\b.*<")
_RE_CURL_WGET_PIPE_SHELL = re.compile(r"\b(curl|wget)\b.*\|\s*(bash|sh|zsh)\b")
_RE_SCP_UPLOAD = re.compile(r"\bscp\b.*\s\S+@\S+:\S*$")
_RE_RSYNC_UPLOAD = re.compile(r"\brsync\b.*\s\S+@\S+:\S*$")
_RE_LOOPBACK = re.compile(r"\b(localhost|127\.0\.0\.1)(:\d+)?|\[?::1\]?(:\d+)?")

# Package publishing
_RE_NPM_PUBLISH = re.compile(r"\bnpm publish\b")
_RE_DRY_RUN = re.compile(r"--dry-run")
_RE_TWINE_PIP_UPLOAD = re.compile(r"\b(twine|pip) upload\b")
_RE_GEM_PUSH = re.compile(r"\bgem push\b")

# SSH remote destruction
_RE_SSH_CMD = re.compile(r"^ssh\b")
_RE_SSH_QUOTED_CMD = re.compile(r"""ssh\s+\S+\s+['"](.+?)['"]""")

# GitHub repo deletion
_RE_GH_REPO_DELETE = re.compile(r"\bgh repo delete\b")

# Git operations
_RE_GIT_PUSH = re.compile(r"^git push\b")
# git push with explicit refspec alternatives (--tags, --all, --mirror)
_RE_GIT_PUSH_REFSPEC_ALT = re.compile(r"--(tags|all|mirror)\b")
_RE_GIT_FORCE_SAFE = re.compile(r"--force-with-lease|--force-if-includes")
_RE_GIT_FORCE_FLAG = re.compile(r"(--force\b|-f\b)")
_RE_GIT_RESET = re.compile(r"^git reset\b")
_RE_HARD_FLAG = re.compile(r"--hard\b")
_RE_MERGE_FLAG = re.compile(r"--merge\b")
_RE_GIT_CHECKOUT = re.compile(r"^git checkout\b")
_RE_DOUBLE_DASH_SEP = re.compile(r"\s--\s")
_RE_GIT_RESTORE = re.compile(r"^git restore\b")
_RE_STAGED_FLAG = re.compile(r"(--staged|-S)\b")
_RE_WORKTREE_FLAG = re.compile(r"(--worktree|-W)\b")
_RE_GIT_STASH_DROP_CLEAR = re.compile(r"^git stash (drop|clear)\b")
_RE_GIT_FILTER = re.compile(r"^git filter-(branch|repo)\b")
_RE_GIT_CONFIG = re.compile(r"^git config\b")
_RE_GLOBAL_SYSTEM_FLAG = re.compile(r"--(global|system)\b")
_RE_GIT_CLEAN = re.compile(r"^git clean\b")
_RE_CLEAN_FORCE_FLAG = re.compile(r"-[a-zA-Z]*f")
_RE_CLEAN_DRY_RUN = re.compile(r"(-[a-zA-Z]*n|--dry-run)")
_RE_GIT_BRANCH_DELETE = re.compile(r"^git branch\s+-D\b")
_RE_GIT_REMOTE_REMOVE = re.compile(r"^git remote remove\b")
_RE_GIT_REFLOG_EXPIRE = re.compile(r"^git reflog expire\b")
_RE_GIT_GC_PRUNE = re.compile(r"^git gc\b.*--prune=")

# Permission changes
_RE_CHMOD_PLUS_X = re.compile(r"^chmod \+x\b")
_RE_SUDO_CHMOD = re.compile(r"\bsudo\s+chmod\b")
_RE_CHMOD = re.compile(r"\bchmod\b")
_RE_CHMOD_777 = re.compile(r"\bchmod\s+777\b")
_RE_CHMOD_SETUID_OCTAL = re.compile(r"\bchmod\s+[4267]\d{3}\b")
_RE_CHMOD_SETUID_SYMBOLIC = re.compile(r"\bchmod\s+[ugo]*\+s\b")
_RE_CHOWN_RECURSIVE = re.compile(r"\bchown\s+-[a-zA-Z]*R")

# Brew commands
_RE_BREW = re.compile(r"^brew\b")
_RE_BREW_MODIFYING = re.compile(r"^brew\s+(install|uninstall|remove|upgrade|tap|untap|link|unlink)\b")

# Docker destructive
_RE_DOCKER_PRUNE = re.compile(r"\bdocker\s+(system|volume|image)\s+prune\b")
_RE_DOCKER_RM_FORCE = re.compile(r"\bdocker\s+rm\s+-[a-zA-Z]*f")
_RE_COMPOSE_DOWN_VOLUMES = re.compile(r"\bdocker-compose\s+down\b.*-v\b")

# Database destructive
_RE_DROP_OBJECT = re.compile(r"\bDROP\s+(DATABASE|TABLE|SCHEMA)\b")
_RE_TRUNCATE = re.compile(r"\bTRUNCATE\b")
_RE_DELETE_FROM = re.compile(r"\bDELETE\s+FROM\b")
_RE_WHERE = re.compile(r"\bWHERE\b")
_RE_DROPDB_DROPUSER = re.compile(r"\b(dropdb|dropuser)\b")
_RE_REDIS_FLUSH = re.compile(r"\bredis-cli\b.*\bFLUSH(ALL|DB)\b")

# Terraform/Pulumi destructive
_RE_TERRAFORM_DESTROY = re.compile(r"\bterraform destroy\b")
_RE_TERRAFORM_AUTO_APPROVE = re.compile(r"\bterraform apply\b.*-auto-approve\b")
_RE_PULUMI_DESTROY = re.compile(r"\bpulumi destroy\b")

# GitHub CI/CD ops
_RE_GH_SECRET_VAR_DELETE = re.compile(r"\bgh\s+(secret|variable)\s+delete\b")
_RE_GH_WORKFLOW_DISABLE = re.compile(r"\bgh\s+workflow\s+disable\b")
_RE_GH_RELEASE_DELETE = re.compile(r"\bgh\s+release\s+delete\b")

# Inline interpreter
_RE_SHELL_SUBSHELL = re.compile(r"\b(bash|sh|zsh)\s+-c\b")

# Container exec tooling: bash -c is the only way to run commands in containers
_RE_CONTAINER_EXEC = re.compile(
    r"\b(docker\s+exec\b|(?:pnpm\s+(?:exec\s+)?)?wp-env\s+run\b)"
)

# Interpreter heredocs: shell/interpreter reading from << delimiter
# Distinct from bash -c (inline_interpreter): here the code is the heredoc body.
_RE_INTERPRETER_HEREDOC = re.compile(
    r"(?<!\w)(bash|sh|zsh|python3?|node|ruby|perl|mysql|psql|sqlite3)\b[^<\n]*<<"
)

# ---------------------------------------------------------------------------
# Detection Functions
# Each returns (detected: bool, message: str | None)
# Signature: (command, tool_name, tool_input, config) -> (bool, str|None)
# ---------------------------------------------------------------------------

# --- Block Tier: Filesystem Destruction ---

def detect_destructive_deletion(command, tool_name, tool_input, config):
    """Detect rm -rf and variants."""
    if not _RE_RM.search(command):
        return False, None
    # Must have both recursive and force flags
    has_recursive = bool(_RE_RECURSIVE_FLAG.search(command))
    has_force = bool(_RE_FORCE_DELETE_FLAG.search(command))
    if has_recursive and has_force:
        return True, BLOCK_MESSAGES["destructive_deletion"]
    return False, None


def detect_chained_deletion(command, tool_name, tool_input, config):
    """Detect rm hidden in command chains (&&, ;, ||)."""
    if not _RE_CHAIN_OPS.search(command):
        return False, None
    # Split on chain operators and check each segment
    segments = _RE_CHAIN_SPLIT.split(command)
    for segment in segments:
        segment = segment.strip()
        # Normalize the segment too
        segment = normalize_command(segment)
        if _RE_RM.search(segment):
            return True, BLOCK_MESSAGES["chained_deletion"]
    return False, None


def detect_alternative_deletion(command, tool_name, tool_input, config):
    """Detect find -delete, find -exec rm, xargs rm, eval rm."""
    # find -delete
    if _RE_FIND_DELETE.search(command):
        # Allow scoped cleanup (relative dot-paths, $TMPDIR, /tmp, /var/tmp)
        if not _RE_FIND_SCOPED_ROOT.search(command):
            return True, BLOCK_MESSAGES["alternative_deletion"]
    # find -exec rm
    if _RE_FIND_EXEC_RM.search(command):
        return True, BLOCK_MESSAGES["alternative_deletion"]
    # xargs rm
    if _RE_XARGS_RM.search(command):
        return True, BLOCK_MESSAGES["alternative_deletion"]
    # eval with rm
    if _RE_EVAL_RM.search(command):
        return True, BLOCK_MESSAGES["alternative_deletion"]
    return False, None


def detect_disk_formatting(command, tool_name, tool_input, config):
    """Detect mkfs, dd to device."""
    if _RE_MKFS.search(command):
        return True, BLOCK_MESSAGES["disk_formatting"]
    # dd writing to a device
    if _RE_DD.search(command) and _RE_DD_OF_DEV.search(command):
        return True, BLOCK_MESSAGES["disk_formatting"]
    return False, None


# --- Block Tier: Network, Credentials, Publishing, SSH, GitHub, Paths ---

def detect_network_exfiltration(command, tool_name, tool_input, config):
    """Detect data exfiltration via curl, wget, nc, scp, rsync."""
    _is_loopback = bool(_RE_LOOPBACK.search(command))

    # curl posting data (file or stdin)
    if _RE_CURL_POST_DATA.search(command):
        if not _is_loopback:
            return True, BLOCK_MESSAGES["network_exfiltration"]
    # curl file upload (-F form, -T upload, --upload-file)
    if _RE_CURL_UPLOAD.search(command):
        if not _is_loopback:
            return True, BLOCK_MESSAGES["network_exfiltration"]
    # wget posting a file — loopback bypass applies here too
    if _RE_WGET_POST_FILE.search(command):
        if not _is_loopback:
            return True, BLOCK_MESSAGES["network_exfiltration"]
    # Piping to nc — loopback not a meaningful exception (nc is raw TCP)
    if _RE_PIPE_TO_NC.search(command) or _RE_NC_REDIRECT.search(command):
        return True, BLOCK_MESSAGES["network_exfiltration"]
    # Piping curl/wget output to bash/sh (remote code execution) — always block
    if _RE_CURL_WGET_PIPE_SHELL.search(command):
        return True, BLOCK_MESSAGES["network_exfiltration"]
    # scp upload — loopback not applicable (scp always remote)
    if _RE_SCP_UPLOAD.search(command):
        return True, BLOCK_MESSAGES["network_exfiltration"]
    # rsync upload — loopback not applicable
    if _RE_RSYNC_UPLOAD.search(command):
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

    # Check safe patterns first (case-insensitive for case-insensitive filesystems)
    for safe_pat in safe_patterns:
        if re.search(safe_pat, file_path, re.IGNORECASE):
            return False, None

    # Check credential patterns (case-insensitive for case-insensitive filesystems)
    for cred_pat in cred_patterns:
        if re.search(cred_pat, file_path, re.IGNORECASE):
            return True, BLOCK_MESSAGES["credential_access"]

    return False, None


def detect_package_publishing(command, tool_name, tool_input, config):
    """Detect package publishing commands."""
    # For compound commands, check each segment independently
    # (prevents "npm publish --dry-run && npm publish" from being allowed
    #  because --dry-run appears in the first segment)
    segments = [command]
    if _RE_CHAIN_OPS.search(command):
        segments = [s.strip() for s in _RE_CHAIN_SPLIT.split(command)]
    for seg in segments:
        # npm publish (without --dry-run in this segment)
        if _RE_NPM_PUBLISH.search(seg) and not _RE_DRY_RUN.search(seg):
            return True, BLOCK_MESSAGES["package_publishing"]
        # twine upload / pip upload
        if _RE_TWINE_PIP_UPLOAD.search(seg):
            return True, BLOCK_MESSAGES["package_publishing"]
        # gem push
        if _RE_GEM_PUSH.search(seg):
            return True, BLOCK_MESSAGES["package_publishing"]
    return False, None


def detect_ssh_remote_destruction(command, tool_name, tool_input, config):
    """Detect destructive commands executed remotely via SSH."""
    if not _RE_SSH_CMD.search(command):
        return False, None
    # Try quoted remote command first
    remote_cmd_match = _RE_SSH_QUOTED_CMD.search(command)
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
    if _RE_GH_REPO_DELETE.search(command):
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
        check_lower = check_path.lower()
        for zero_path in zero_paths:
            if zero_path.lower() in check_lower:
                return True, BLOCK_MESSAGES["zero_access_paths"]

    return False, None


# --- Block Tier: Git Bare Push ---

def detect_git_bare_push(command, tool_name, tool_input, config):
    """Detect git push without explicit branch specification.

    Blocks bare `git push` and `git push origin` (no refspec). Requires
    an explicit branch/ref (e.g. `git push origin HEAD`). Also allows
    --tags, --all, --mirror as refspec alternatives.
    """
    if not _RE_GIT_PUSH.search(command):
        return False, None
    # --tags, --all, --mirror are explicit refspec alternatives
    if _RE_GIT_PUSH_REFSPEC_ALT.search(command):
        return False, None
    # Count non-flag arguments after 'git push'
    parts = command.split()
    non_flag_parts = [p for p in parts[2:] if not p.startswith("-")]
    # Need at least 2 non-flag args: remote + refspec
    if len(non_flag_parts) < 2:
        return True, BLOCK_MESSAGES["git_bare_push"]
    return False, None


# --- Ask Tier: Git Operations ---

def detect_git_force_push(command, tool_name, tool_input, config):
    """Detect git push --force (but not --force-with-lease or --force-if-includes)."""
    if not _RE_GIT_PUSH.search(command):
        return False, None
    # Must have --force or -f, but not --force-with-lease or --force-if-includes
    if _RE_GIT_FORCE_SAFE.search(command):
        return False, None
    if _RE_GIT_FORCE_FLAG.search(command):
        return True, ASK_MESSAGES["git_force_push"]
    return False, None


def detect_git_hard_reset(command, tool_name, tool_input, config):
    """Detect git reset --hard or --merge."""
    if not _RE_GIT_RESET.search(command):
        return False, None
    if _RE_HARD_FLAG.search(command):
        return True, ASK_MESSAGES["git_hard_reset"]
    if _RE_MERGE_FLAG.search(command):
        return True, ASK_MESSAGES["git_hard_reset"]
    return False, None


def detect_git_discard_changes(command, tool_name, tool_input, config):
    """Detect git checkout -- and git restore that discards working tree changes."""
    # git checkout -- <path> or git checkout <ref> -- <path>
    if _RE_GIT_CHECKOUT.search(command) and _RE_DOUBLE_DASH_SEP.search(command):
        return True, ASK_MESSAGES["git_discard_changes"]
    # git restore (without --staged/-S alone — that's allowlisted)
    if _RE_GIT_RESTORE.search(command):
        has_staged = bool(_RE_STAGED_FLAG.search(command))
        has_worktree = bool(_RE_WORKTREE_FLAG.search(command))
        # If only --staged (no --worktree), it's safe (allowlisted)
        if has_staged and not has_worktree:
            return False, None
        # Otherwise it touches the worktree — dangerous
        return True, ASK_MESSAGES["git_discard_changes"]
    return False, None


def detect_git_destroy_stash(command, tool_name, tool_input, config):
    """Detect git stash drop/clear."""
    if _RE_GIT_STASH_DROP_CLEAR.search(command):
        return True, ASK_MESSAGES["git_destroy_stash"]
    return False, None


def detect_git_history_rewrite(command, tool_name, tool_input, config):
    """Detect git filter-branch/filter-repo."""
    if _RE_GIT_FILTER.search(command):
        return True, ASK_MESSAGES["git_history_rewrite"]
    return False, None


def detect_git_config_changes(command, tool_name, tool_input, config):
    """Detect git config --global or --system."""
    if not _RE_GIT_CONFIG.search(command):
        return False, None
    if _RE_GLOBAL_SYSTEM_FLAG.search(command):
        return True, ASK_MESSAGES["git_config_changes"]
    return False, None


def detect_git_other_dangerous(command, tool_name, tool_input, config):
    """Detect other dangerous git ops: clean -f, branch -D, remote remove, reflog expire, gc --prune."""
    # git clean with -f (force) but without -n/--dry-run (allowlisted)
    if _RE_GIT_CLEAN.search(command):
        if _RE_CLEAN_FORCE_FLAG.search(command) and not _RE_CLEAN_DRY_RUN.search(command):
            return True, ASK_MESSAGES["git_other_dangerous"]
    # git branch -D (force delete)
    if _RE_GIT_BRANCH_DELETE.search(command):
        return True, ASK_MESSAGES["git_other_dangerous"]
    # git remote remove
    if _RE_GIT_REMOTE_REMOVE.search(command):
        return True, ASK_MESSAGES["git_other_dangerous"]
    # git reflog expire
    if _RE_GIT_REFLOG_EXPIRE.search(command):
        return True, ASK_MESSAGES["git_other_dangerous"]
    # git gc --prune=now
    if _RE_GIT_GC_PRUNE.search(command):
        return True, ASK_MESSAGES["git_other_dangerous"]
    return False, None


# --- Ask Tier: Non-Git Categories ---

def detect_permission_changes(command, tool_name, tool_input, config):
    """Detect dangerous permission changes (chmod 777, setuid, recursive chown, sudo chmod)."""
    # chmod +x is safe (allowlisted), skip it
    if _RE_CHMOD_PLUS_X.search(command):
        return False, None
    # sudo chmod — always flag (note: sudo is stripped by normalize, but
    # the pattern stays for defense in depth if normalization changes)
    if _RE_SUDO_CHMOD.search(command):
        return True, ASK_MESSAGES["permission_changes"]
    # chmod 777 or setuid/setgid bits (4-digit octal starting with 4/2/6)
    if _RE_CHMOD.search(command):
        # 777 — world-writable
        if _RE_CHMOD_777.search(command):
            return True, ASK_MESSAGES["permission_changes"]
        # 4-digit octal with setuid (4), setgid (2), or both (6) prefix
        if _RE_CHMOD_SETUID_OCTAL.search(command):
            return True, ASK_MESSAGES["permission_changes"]
        # Symbolic setuid/setgid
        if _RE_CHMOD_SETUID_SYMBOLIC.search(command):
            return True, ASK_MESSAGES["permission_changes"]
    # chown -R (recursive ownership change)
    if _RE_CHOWN_RECURSIVE.search(command):
        return True, ASK_MESSAGES["permission_changes"]
    return False, None


def detect_brew_commands(command, tool_name, tool_input, config):
    """Detect brew install/uninstall/upgrade/tap/link (not list/info/search/doctor)."""
    if not _RE_BREW.search(command):
        return False, None
    if _RE_BREW_MODIFYING.search(command):
        return True, ASK_MESSAGES["brew_commands"]
    return False, None


def detect_docker_destructive(command, tool_name, tool_input, config):
    """Detect destructive Docker commands."""
    # docker system/volume/image prune
    if _RE_DOCKER_PRUNE.search(command):
        return True, ASK_MESSAGES["docker_destructive"]
    # docker rm -f
    if _RE_DOCKER_RM_FORCE.search(command):
        return True, ASK_MESSAGES["docker_destructive"]
    # docker-compose down -v
    if _RE_COMPOSE_DOWN_VOLUMES.search(command):
        return True, ASK_MESSAGES["docker_destructive"]
    return False, None


def detect_database_destructive(command, tool_name, tool_input, config):
    """Detect destructive database commands."""
    cmd_upper = command.upper()
    # DROP DATABASE/TABLE
    if _RE_DROP_OBJECT.search(cmd_upper):
        return True, ASK_MESSAGES["database_destructive"]
    # TRUNCATE
    if _RE_TRUNCATE.search(cmd_upper):
        return True, ASK_MESSAGES["database_destructive"]
    # DELETE without WHERE
    if _RE_DELETE_FROM.search(cmd_upper) and not _RE_WHERE.search(cmd_upper):
        return True, ASK_MESSAGES["database_destructive"]
    # dropdb/dropuser CLI tools
    if _RE_DROPDB_DROPUSER.search(command):
        return True, ASK_MESSAGES["database_destructive"]
    # redis FLUSHALL/FLUSHDB
    if _RE_REDIS_FLUSH.search(command):
        return True, ASK_MESSAGES["database_destructive"]
    return False, None


def detect_terraform_destructive(command, tool_name, tool_input, config):
    """Detect destructive Terraform/Pulumi commands."""
    if _RE_TERRAFORM_DESTROY.search(command):
        return True, ASK_MESSAGES["terraform_destructive"]
    if _RE_TERRAFORM_AUTO_APPROVE.search(command):
        return True, ASK_MESSAGES["terraform_destructive"]
    if _RE_PULUMI_DESTROY.search(command):
        return True, ASK_MESSAGES["terraform_destructive"]
    return False, None


def detect_github_cicd_ops(command, tool_name, tool_input, config):
    """Detect destructive GitHub CI/CD operations."""
    if _RE_GH_SECRET_VAR_DELETE.search(command):
        return True, ASK_MESSAGES["github_cicd_ops"]
    if _RE_GH_WORKFLOW_DISABLE.search(command):
        return True, ASK_MESSAGES["github_cicd_ops"]
    if _RE_GH_RELEASE_DELETE.search(command):
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

    Exception: container exec tooling (docker exec, wp-env run) uses bash -c
    as the only mechanism to run commands inside containers. Other rules
    (destructive_deletion, etc.) still evaluate the full command string.
    """
    if tool_name != "Bash":
        return False, None
    # bash/sh/zsh -c (subshell execution — the evasion vector)
    if _RE_SHELL_SUBSHELL.search(command):
        # Don't flag when bash -c is used for container exec
        if _RE_CONTAINER_EXEC.search(command):
            return False, None
        return True, ASK_MESSAGES["inline_interpreter"]
    return False, None


def detect_inline_heredoc(command, tool_name, tool_input, config):
    """Detect heredocs fed to shell or interpreter commands (bash <<, python3 <<, etc.).

    Writer heredocs (cat >, tee) are stripped before rules run and are NOT flagged here.
    This rule catches the cases where heredoc content is EXECUTED, not just written to a file.
    """
    if tool_name != "Bash":
        return False, None
    if _RE_INTERPRETER_HEREDOC.search(command):
        return True, ASK_MESSAGES["inline_interpreter"]
    return False, None


# Rule registry: (rule_id, tier, detect_fn, applicable_tools)
# The tool set declares which tools each rule applies to. A rule only runs
# when the current tool_name is in its set. This makes scope explicit and
# prevents Bash-only rules from running on Read/Write/Edit (and vice versa).
RULE_REGISTRY = [
    ("destructive_deletion", "block", detect_destructive_deletion, {"Bash"}),
    ("chained_deletion", "block", detect_chained_deletion, {"Bash"}),
    ("alternative_deletion", "block", detect_alternative_deletion, {"Bash"}),
    ("disk_formatting", "block", detect_disk_formatting, {"Bash"}),
    ("network_exfiltration", "block", detect_network_exfiltration, {"Bash"}),
    ("credential_access", "block", detect_credential_access, {"Bash", "Read", "Write", "Edit"}),
    ("package_publishing", "block", detect_package_publishing, {"Bash"}),
    ("ssh_remote_destruction", "block", detect_ssh_remote_destruction, {"Bash"}),
    ("github_repo_deletion", "block", detect_github_repo_deletion, {"Bash"}),
    ("zero_access_paths", "block", detect_zero_access_paths, {"Bash", "Read", "Write", "Edit"}),
    ("git_bare_push", "block", detect_git_bare_push, {"Bash"}),
    # Ask tier — git operations
    ("git_force_push", "ask", detect_git_force_push, {"Bash"}),
    ("git_hard_reset", "ask", detect_git_hard_reset, {"Bash"}),
    ("git_discard_changes", "ask", detect_git_discard_changes, {"Bash"}),
    ("git_destroy_stash", "ask", detect_git_destroy_stash, {"Bash"}),
    ("git_history_rewrite", "ask", detect_git_history_rewrite, {"Bash"}),
    ("git_config_changes", "ask", detect_git_config_changes, {"Bash"}),
    ("git_other_dangerous", "ask", detect_git_other_dangerous, {"Bash"}),
    # Ask tier — non-git
    ("permission_changes", "ask", detect_permission_changes, {"Bash"}),
    ("brew_commands", "ask", detect_brew_commands, {"Bash"}),
    ("docker_destructive", "ask", detect_docker_destructive, {"Bash"}),
    ("database_destructive", "ask", detect_database_destructive, {"Bash"}),
    ("terraform_destructive", "ask", detect_terraform_destructive, {"Bash"}),
    ("github_cicd_ops", "ask", detect_github_cicd_ops, {"Bash"}),
    ("sensitive_write_target", "ask", detect_sensitive_write_target, {"Write", "Edit"}),
    ("inline_interpreter", "ask", detect_inline_interpreter, {"Bash"}),
    ("inline_heredoc", "ask", detect_inline_heredoc, {"Bash"}),
]

# Pre-built per-tool rule lists (indexed at module load, not per-call)
RULES_BY_TOOL = {}
for _rule_id, _tier, _fn, _tools in RULE_REGISTRY:
    for _tool in _tools:
        RULES_BY_TOOL.setdefault(_tool, []).append((_rule_id, _tier, _fn))

_mark("registry_built")


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
    _mark("exit")
    print(message, file=sys.stderr)
    sys.exit(2)


def ask(message):
    """Ask for confirmation: exit 0 with JSON on stdout."""
    _mark("exit")
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
    _mark("exit")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    _mark("stdin_start")
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        allow()
    _mark("stdin_parsed")

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        allow()

    config = load_config()
    disabled = set(config.get("disable_rules", []))
    _mark("config_loaded")

    # Extract command for Bash tool
    command = ""
    if tool_name == "Bash":
        raw_command = tool_input.get("command", "")
        # Strip writer heredoc bodies (cat >, tee) BEFORE normalization so
        # the regex can match on line structure (\n delimiters). Interpreter
        # heredocs (bash <<, python3 <<) are NOT stripped.
        raw_command = strip_writer_heredocs(raw_command)
        command = normalize_command(raw_command)

    # Self-protection: prevent modification of hook config or plugin files.
    # NOT configurable — cannot be disabled via disable_rules.
    # Covers Write/Edit (file_path) and Bash (redirect/copy to protected paths).
    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if file_path:
            if _is_self_protected_path(file_path):
                block(BLOCK_MESSAGES["self_protection"])
    elif tool_name == "Bash" and command:
        if _bash_targets_protected_path(command):
            block(BLOCK_MESSAGES["self_protection"])

    # 1. Allowlist — check first, but SKIP for compound commands.
    #    Without this guard, "rm -rf /tmp/build && rm -rf /home" would
    #    match the temp-directory allowlist and bypass all block checks.
    is_compound = bool(command and _RE_CHAIN_OPS.search(command))
    if command and not is_compound:
        for rule_id, pattern in ALLOWLIST_PATTERNS:
            if rule_id not in disabled and pattern.search(command):
                _mark("allowlisted")
                allow()
    _mark("rules_start")

    # 2. Block / Ask — pass 1: evaluate full command against all rules.
    #    This catches rules that need the full chain context (e.g.
    #    detect_chained_deletion checks for rm hidden after &&).
    first_ask = None
    for rule_id, tier, detect_fn in RULES_BY_TOOL.get(tool_name, []):
        if rule_id in disabled:
            continue
        detected, message = detect_fn(command, tool_name, tool_input, config)
        if detected:
            if tier == "block":
                _mark("rules_done")
                block(message)
            elif tier == "ask" and first_ask is None:
                first_ask = message

    # 3. Chain-aware pass 2: split compound commands and re-evaluate each
    #    segment. Catches ^-anchored rules (git ops, etc.) hidden after
    #    chain operators. Only runs for compound Bash commands.
    if is_compound:
        for seg in _RE_CHAIN_SPLIT.split(command):
            seg = normalize_command(seg.strip())
            if not seg:
                continue
            # Per-segment allowlist
            seg_allowed = False
            for rule_id, pattern in ALLOWLIST_PATTERNS:
                if rule_id not in disabled and pattern.search(seg):
                    seg_allowed = True
                    break
            if seg_allowed:
                continue
            for rule_id, tier, detect_fn in RULES_BY_TOOL.get(tool_name, []):
                if rule_id in disabled:
                    continue
                detected, message = detect_fn(seg, tool_name, tool_input, config)
                if detected:
                    if tier == "block":
                        _mark("rules_done")
                        block(message)
                    elif tier == "ask" and first_ask is None:
                        first_ask = message
                    break  # first match per segment
    _mark("rules_done")

    if first_ask:
        ask(first_ask)

    # 4. Allow — everything else
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Fail open — don't block the agent on hook bugs
        sys.exit(0)

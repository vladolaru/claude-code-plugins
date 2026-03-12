"""Filesystem and path-oriented safety rules."""

from __future__ import annotations

import os
import re

from ..config import DEFAULTS
from ..paths import (
    _NON_FILE_COMMANDS,
    _collect_bash_path_candidates,
    _collect_bash_targets,
    _collect_write_targets_for_segment,
    _is_non_file_command,
    _is_sensitive_write_target_path,
)
from ..registry import ask_rule, block_rule
from ..shell import (
    RE_CHAIN_OPS,
    _segment_command_and_args,
    _tokenized_segments,
)


_RE_FIND_DELETE = re.compile(r"\bfind\b.*-delete\b")
_RE_FIND_SCOPED_ROOT = re.compile(
    r"^find\s+(?:\.(?:$|[\w./\-]|\s)|\$TMPDIR\b|\$TMP\b|/tmp/|/var/tmp/)"
)
_RE_FIND_TRAVERSAL = re.compile(r"^find\s+\S*\.\.")
_RE_FIND_EXEC_RM = re.compile(r"\bfind\b.*-exec\s+rm\b")
_RE_XARGS_RM = re.compile(r"\bxargs\s+rm\b")
_RE_EVAL_RM = re.compile(r"\beval\b.*\brm\b")
_RE_RM_RECURSIVE = re.compile(r"(?:^|\s)-[a-zA-Z]*[rR]|--recursive")
_RE_RM_FORCE = re.compile(r"(?:^|\s)-[a-zA-Z]*[fF]|--force")

_SHELL_INLINE_COMMANDS = {"bash", "sh", "zsh"}


def _rm_has_recursive_force(args):
    has_recursive = False
    has_force = False
    for arg in args:
        if arg == "--":
            break
        if arg == "--recursive":
            has_recursive = True
        elif arg == "--force":
            has_force = True
        elif arg.startswith("-") and not arg.startswith("--"):
            flags = arg[1:]
            if "r" in flags.lower():
                has_recursive = True
            if "f" in flags.lower():
                has_force = True
    return has_recursive and has_force


def _shell_payloads(command_name, args):
    payloads = []
    for index, arg in enumerate(args[:-1]):
        if arg == "-c":
            payloads.append(args[index + 1])
    if command_name in _SHELL_INLINE_COMMANDS or command_name == "su":
        return payloads

    nested_payloads = []
    for index in range(len(args) - 2):
        if args[index] in _SHELL_INLINE_COMMANDS and args[index + 1] == "-c":
            nested_payloads.append(args[index + 2])
    return nested_payloads


def _command_contains_rm_rf(command):
    for segment in _tokenized_segments(command):
        command_name, args = _segment_command_and_args(segment)
        if command_name == "rm" and _rm_has_recursive_force(args):
            return True
    return False


def detect_destructive_deletion(ctx):
    """Detect rm with recursive+force while skipping inert string mentions."""
    command = ctx.command
    segments = ctx.segments
    if not segments:
        return False

    for segment in segments:
        command_name, args = _segment_command_and_args(segment)
        if not command_name:
            continue
        if command_name in _NON_FILE_COMMANDS:
            continue
        if command_name == "rm" and _rm_has_recursive_force(args):
            return True
        for payload in _shell_payloads(command_name, args):
            if _command_contains_rm_rf(payload):
                return True

    if ("<<" in command or RE_CHAIN_OPS.search(command)) and not _is_non_file_command(command):
        if re.search(r"\brm\b", command) and _RE_RM_RECURSIVE.search(command) and _RE_RM_FORCE.search(command):
            return True
    return False


def detect_alternative_deletion(ctx):
    """Detect find -delete, find -exec rm, xargs rm, eval rm."""
    command = ctx.command
    if _RE_FIND_DELETE.search(command):
        if _RE_FIND_TRAVERSAL.search(command) or not _RE_FIND_SCOPED_ROOT.search(command):
            return True
    if _RE_FIND_EXEC_RM.search(command):
        return True
    if _RE_XARGS_RM.search(command):
        return True
    if _RE_EVAL_RM.search(command):
        return True
    return False


def detect_credential_access(ctx):
    """Detect access to credential files via Read/Edit/Write tools or Bash."""
    cred_patterns = ctx.config.get("credential_patterns", DEFAULTS["credential_patterns"])
    safe_patterns = ctx.config.get("credential_safe_patterns", DEFAULTS["credential_safe_patterns"])

    paths_to_check = []
    if ctx.tool_name in ("Read", "Edit", "Write"):
        file_path = ctx.tool_input.get("file_path", "")
        if file_path:
            paths_to_check.append(file_path)
    elif ctx.tool_name == "Bash" and ctx.command:
        paths_to_check.extend(
            _collect_bash_path_candidates(
                ctx.command,
                ctx.config,
                include_credential_names=True,
            )
        )

    if not paths_to_check:
        return False

    for file_path in paths_to_check:
        if any(re.search(safe_pat, file_path, re.IGNORECASE) for safe_pat in safe_patterns):
            continue
        if any(re.search(cred_pat, file_path, re.IGNORECASE) for cred_pat in cred_patterns):
            return True

    return False


def detect_zero_access_paths(ctx):
    """Detect access to zero-access paths (e.g., ~/.ssh/, ~/.gnupg/)."""
    zero_paths = ctx.config.get("zero_access_paths", DEFAULTS["zero_access_paths"])

    paths_to_check = []
    if ctx.tool_name in ("Read", "Edit", "Write"):
        file_path = ctx.tool_input.get("file_path", "")
        if file_path:
            paths_to_check.append(file_path)
    if ctx.tool_name == "Bash" and ctx.command:
        paths_to_check.extend(_collect_bash_path_candidates(ctx.command, ctx.config))

    for check_path in paths_to_check:
        try:
            resolved = os.path.realpath(os.path.expanduser(check_path))
        except (OSError, ValueError):
            resolved = None

        for zero_path in zero_paths:
            expanded_zero = os.path.expanduser(zero_path)
            if os.path.isabs(expanded_zero):
                if resolved:
                    norm_zero = expanded_zero.rstrip("/").lower()
                    norm_resolved = resolved.lower()
                    if norm_resolved == norm_zero or norm_resolved.startswith(norm_zero + "/"):
                        return True
            else:
                if zero_path.lower() in check_path.lower():
                    return True

    return False


def detect_sensitive_write_target(ctx):
    """Detect Write/Edit to shell init files, git hooks, or package config."""
    paths_to_check = []
    if ctx.tool_name in ("Write", "Edit"):
        file_path = ctx.tool_input.get("file_path", "")
        if file_path:
            paths_to_check.append(file_path)
    elif ctx.tool_name == "Bash" and ctx.command:
        for raw_path, resolved_path in _collect_bash_targets(
            ctx.command,
            ctx.tool_input,
            _collect_write_targets_for_segment,
        ):
            paths_to_check.append(resolved_path or raw_path)

    for file_path in paths_to_check:
        if _is_sensitive_write_target_path(file_path):
            return True
    return False


RULE_SPECS = [
    ("destructive_deletion", block_rule(
        tools={"Bash"},
        detect=detect_destructive_deletion,
        message="Use targeted file removal instead of recursive force-delete. Remove specific files by name (`rm file1 file2`), or use `git clean --dry-run` to preview before cleaning. This protects against accidental data loss.",
        examples=["rm -rf /"],
    )),
    ("alternative_deletion", block_rule(
        tools={"Bash"},
        detect=detect_alternative_deletion,
        message="Indirect deletion methods (`find -delete`, `xargs rm`) can affect more files than intended. Use explicit file paths for removal, or `git clean --dry-run` to preview what would be deleted.",
        examples=["find / -name '*.log' -delete"],
    )),
    ("disk_formatting", block_rule(
        tools={"Bash"},
        patterns=[r"\bmkfs\b"],
        pattern_groups=[[r"\bdd\b", r"of=/dev/"]],
        message="Disk formatting and raw device writes (`mkfs`, `dd`) are irreversible system-level operations. These should only be run manually with explicit user intent, never by an agent.",
        examples=["mkfs.ext4 /dev/sda1"],
    )),
    ("credential_access", block_rule(
        tools={"Bash", "Read", "Write", "Edit"},
        detect=detect_credential_access,
        message="This file may contain secrets or credentials. Use `.env.example` or `.env.template` for reference files. If you need to read configuration, ask the user to provide the specific values.",
        examples=["./.env"],
    )),
    ("zero_access_paths", block_rule(
        tools={"Bash", "Read", "Write", "Edit"},
        detect=detect_zero_access_paths,
        message="This path contains sensitive system or security data that should not be accessed by an agent. Ask the user to provide the specific information you need.",
        examples=["~/.ssh/id_rsa", "~/.aws/credentials"],
    )),
    ("sensitive_write_target", ask_rule(
        tools={"Bash", "Write", "Edit"},
        detect=detect_sensitive_write_target,
        message="This file controls shell behavior, git hooks, or package manager configuration. Modifying it can have persistent side effects beyond this session. Confirm this is intentional.",
        examples=["~/.bashrc"],
    )),
]

_TEMP_DIR_PREFIX = r"(?:/tmp/|/var/tmp/|\$TMPDIR/|\.claude/tmp/)"

ALLOWLIST_PATTERNS = [
    ("destructive_deletion", re.compile(rf"^rm\s+-[rfRF]*\s+(?:{_TEMP_DIR_PREFIX}\S*\s*)+$")),
    ("alternative_deletion", re.compile(rf"^find\s+{_TEMP_DIR_PREFIX}\S*\s+")),
]

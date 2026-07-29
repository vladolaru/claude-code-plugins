"""Path extraction and mutation-target helpers for the yoloing-safe hook."""

from __future__ import annotations

import os
import re

from .config import DEFAULTS, SELF_PROTECTED_PATHS, is_path_within_self_protected
from .shell import (
    _collect_input_redirection_sources,
    _collect_positional_args,
    _collect_redirection_targets,
    _segment_command_and_args,
    _split_shell_segments,
    _tokenize_shell,
)


_RE_INTERPRETER_WRITE_SYNTAX = re.compile(
    r"\b(?:open\(|Path\(|write(?:File|FileSync|Text|Bytes)|appendFile(?:Sync)?\(|"
    r"createWriteStream\(|File\.(?:write|binwrite|open)\()",
    re.DOTALL,
)
_GREP_LIKE_COMMANDS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
_SED_SCRIPT_OPTIONS = {"-e", "-f", "--expression", "--file"}
_AWK_SCRIPT_OPTIONS = {"-f", "--file"}
_FIND_PRE_EXPR_OPTIONS = {"-H", "-L", "-P"}
_RE_REMOTE_PATH = re.compile(r"^\S+@\S+:\S+$")
_RE_APPLY_PATCH_TARGET = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File: (?P<path>.+?)\s*$",
    re.MULTILINE,
)
_RE_APPLY_PATCH_MOVE_TARGET = re.compile(
    r"^\*\*\* Move to: (?P<path>.+?)\s*$",
    re.MULTILINE,
)

_NON_FILE_COMMANDS = frozenset({
    "echo", "printf", "export", "set", "unset", "test",
    "[", "[[", "true", "false", "alias", "type", "which",
    "declare", "local", "readonly", "return", "exit",
    "break", "continue", "shift", "trap", "read",
})

_INTERPRETER_WRITE_PATH_PATTERNS = [
    re.compile(
        r"""open\(\s*(['"])(?P<path>.+?)\1\s*,\s*(['"])[^'"]*[wax+][^'"]*\3""",
        re.DOTALL,
    ),
    re.compile(
        r"""Path\(\s*(['"])(?P<path>.+?)\1\s*\)\.(?:write_text|write_bytes)\(""",
        re.DOTALL,
    ),
    re.compile(
        r"""Path\(\s*(['"])(?P<path>.+?)\1\s*\)\.open\(\s*(['"])[^'"]*[wax+][^'"]*\3""",
        re.DOTALL,
    ),
    re.compile(
        r"""(?:writeFileSync|writeFile|appendFileSync|appendFile|openSync|createWriteStream)\(\s*(['"])(?P<path>.+?)\1""",
        re.DOTALL,
    ),
    re.compile(
        r"""File\.(?:write|binwrite)\(\s*(['"])(?P<path>.+?)\1""",
        re.DOTALL,
    ),
    re.compile(
        r"""File\.open\(\s*(['"])(?P<path>.+?)\1\s*,\s*(['"])[^'"]*[wax+][^'"]*\3""",
        re.DOTALL,
    ),
]


def extract_apply_patch_paths(patch):
    """Extract every source and destination path from a Codex patch."""
    if not isinstance(patch, str):
        return []
    paths = [
        match.group("path")
        for pattern in (_RE_APPLY_PATCH_TARGET, _RE_APPLY_PATCH_MOVE_TARGET)
        for match in pattern.finditer(patch)
    ]
    return list(dict.fromkeys(paths))


def resolve_tool_path(file_path, tool_input):
    """Resolve a tool-supplied path against its cwd when one is provided."""
    cwd = os.getcwd()
    if isinstance(tool_input, dict):
        supplied_cwd = tool_input.get("cwd")
        if isinstance(supplied_cwd, str) and supplied_cwd:
            cwd = supplied_cwd
    return _resolve_candidate_path(file_path, cwd) or file_path


def _resolve_candidate_path(path_token, cwd):
    """Resolve candidate path token to a real filesystem path."""
    if not path_token:
        return None
    expanded = os.path.expanduser(path_token)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    try:
        return os.path.realpath(expanded)
    except (OSError, ValueError):
        return None


def _dedupe(items):
    """Dedupe a sequence while preserving order."""
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _collect_write_targets_for_segment(segment):
    """Collect likely content-write or destructive file targets from a segment."""
    targets = _collect_redirection_targets(segment)
    command, args = _segment_command_and_args(segment)
    if not command:
        return targets

    positional = _collect_positional_args(args)
    if command in {"cp", "install", "rsync", "ln"} and positional:
        targets.append(positional[-1])
    elif command == "mv" and positional:
        targets.extend(positional)
    elif command == "tee" and positional:
        targets.extend(positional)
    elif command == "sed":
        has_inplace = any(
            arg == "-i"
            or arg.startswith("-i")
            or arg == "--in-place"
            or arg.startswith("--in-place=")
            for arg in args
        )
        if has_inplace and len(positional) >= 2:
            targets.extend(positional[1:])
    elif command in {"touch", "truncate", "rm", "unlink", "rmdir"}:
        targets.extend(positional)

    return _dedupe(targets)


def _collect_symlink_source_targets(segment):
    """Collect the source argument of `ln -s` commands."""
    command, args = _segment_command_and_args(segment)
    if command != "ln":
        return []
    has_symlink_flag = any(
        arg == "-s" or (arg.startswith("-") and not arg.startswith("--") and "s" in arg[1:])
        for arg in args
        if arg != "--"
    )
    if not has_symlink_flag:
        return []
    positional = _collect_positional_args(args)
    if positional:
        return [positional[0]]
    return []


def _collect_protected_mutation_targets_for_segment(segment):
    """Collect targets that would mutate or disable protected infrastructure."""
    targets = list(_collect_write_targets_for_segment(segment))
    command, args = _segment_command_and_args(segment)
    if not command:
        return targets

    positional = _collect_positional_args(args)
    if command == "mkdir":
        targets.extend(positional)
    elif command == "chmod" and len(positional) >= 2:
        targets.extend(positional[1:])
    elif command in {"chown", "chgrp"} and len(positional) >= 2:
        targets.extend(positional[1:])

    return _dedupe(targets)


def _base_cwd_for_bash(tool_input):
    """Resolve the effective cwd for Bash path handling."""
    base_cwd = os.getcwd()
    if isinstance(tool_input, dict):
        maybe_cwd = tool_input.get("cwd")
        if isinstance(maybe_cwd, str) and maybe_cwd:
            base_cwd = os.path.expanduser(maybe_cwd)
    return os.path.realpath(base_cwd)


def _update_cwd_from_cd(segment, cwd):
    """Track explicit `cd <dir>` segments for relative-path resolution."""
    command, args = _segment_command_and_args(segment)
    if command != "cd" or not args:
        return cwd
    destination = _resolve_candidate_path(args[0], cwd)
    return destination or cwd


def _collect_bash_targets(command, tool_input, target_collector):
    """Collect raw + resolved Bash command targets using the provided collector."""
    try:
        tokens = _tokenize_shell(command)
    except ValueError:
        tokens = []

    cwd = _base_cwd_for_bash(tool_input)
    targets = []
    for segment in _split_shell_segments(tokens):
        for target in target_collector(segment):
            targets.append((target, _resolve_candidate_path(target, cwd)))
        cwd = _update_cwd_from_cd(segment, cwd)
    return targets


def _collect_interpreter_write_targets(command, tool_input):
    """Collect interpreter-side write targets from inline code."""
    cwd = _base_cwd_for_bash(tool_input)
    targets = []
    for pattern in _INTERPRETER_WRITE_PATH_PATTERNS:
        for match in pattern.finditer(command):
            target = match.group("path")
            targets.append((target, _resolve_candidate_path(target, cwd)))
    return _dedupe(targets)


def _command_mentions_protected_path(command):
    """Fallback for interpreter one-liners writing to protected paths."""
    for protected in SELF_PROTECTED_PATHS:
        protected_real = os.path.realpath(protected)
        if protected in command or protected_real in command:
            return True
        home = os.path.expanduser("~")
        if protected_real.startswith(home):
            tilde_form = "~" + protected_real[len(home):]
            if tilde_form in command:
                return True
    return False


def _bash_targets_protected_path(command, tool_input):
    """Check if Bash command writes to a self-protected path."""
    for _target, resolved in _collect_bash_targets(
        command,
        tool_input,
        _collect_protected_mutation_targets_for_segment,
    ):
        if resolved and is_path_within_self_protected(resolved):
            return True

    for _target, resolved in _collect_bash_targets(
        command,
        tool_input,
        _collect_symlink_source_targets,
    ):
        if resolved and is_path_within_self_protected(resolved):
            return True

    for _target, resolved in _collect_interpreter_write_targets(command, tool_input):
        if resolved and is_path_within_self_protected(resolved):
            return True

    if _RE_INTERPRETER_WRITE_SYNTAX.search(command) and _command_mentions_protected_path(command):
        return True
    return False


def _token_matches_credential_pattern(token, config):
    """Return True when a single token looks like a credential file/path."""
    cred_patterns = config.get("credential_patterns", DEFAULTS["credential_patterns"])
    return any(re.search(cred_pat, token, re.IGNORECASE) for cred_pat in cred_patterns)


def _extract_path_candidates_from_arg(arg, config, include_credential_names=False):
    """Extract likely filesystem path candidates from one shell arg token."""
    if not arg:
        return []
    if arg.startswith(("http://", "https://", "file://")):
        return []
    if _RE_REMOTE_PATH.match(arg):
        return []

    def _candidate_from_value(value):
        if not value:
            return []
        if value.startswith(("http://", "https://", "file://")):
            return []
        if _RE_REMOTE_PATH.match(value):
            return []
        if value.startswith("@") and len(value) > 1:
            value = value[1:]
        if value.startswith(("/", "~", "./", "../", "$HOME/", "${HOME}/", "$TMPDIR/", "$TMP/", ".")):
            return [value]
        if include_credential_names and _token_matches_credential_pattern(value, config):
            return [value]
        return []

    candidates = _candidate_from_value(arg)
    if "=" in arg:
        candidates.extend(_candidate_from_value(arg.split("=", 1)[1]))
    return _dedupe(candidates)


def _collect_find_roots(args, config):
    """Collect find root paths without treating patterns like `-name '.env'` as files."""
    roots = []
    index = 0
    while index < len(args) and (
        args[index] in _FIND_PRE_EXPR_OPTIONS
        or args[index].startswith("-D")
        or args[index].startswith("-O")
    ):
        index += 1

    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            continue
        if arg.startswith("-") or arg in {"(", ")", "!", "-o", "-a", ","}:
            break
        roots.extend(_extract_path_candidates_from_arg(arg, config))
        index += 1

    return roots


def _collect_bash_path_candidates(command, config, include_credential_names=False):
    """Collect likely Bash file/path arguments without treating search patterns as files."""
    try:
        tokens = _tokenize_shell(command)
    except ValueError:
        tokens = []

    candidates = []
    for segment in _split_shell_segments(tokens):
        candidates.extend(_collect_input_redirection_sources(segment))
        cmd, args = _segment_command_and_args(segment)
        if not cmd or cmd in _NON_FILE_COMMANDS:
            continue

        positional = _collect_positional_args(args)
        skip_first_positional = False
        if cmd in _GREP_LIKE_COMMANDS:
            has_explicit_pattern = any(
                arg in {"-e", "--regexp", "-f", "--file"}
                or arg.startswith("--regexp=")
                or arg.startswith("--file=")
                for arg in args
            )
            skip_first_positional = bool(positional) and not has_explicit_pattern
        elif cmd == "sed":
            has_explicit_script = any(
                arg in _SED_SCRIPT_OPTIONS
                or arg.startswith("--expression=")
                or arg.startswith("--file=")
                for arg in args
            )
            has_inplace = any(
                arg == "-i"
                or arg.startswith("-i")
                or arg == "--in-place"
                or arg.startswith("--in-place=")
                for arg in args
            )
            skip_first_positional = bool(positional) and not has_explicit_script and not has_inplace
        elif cmd == "awk":
            has_explicit_program = any(
                arg in _AWK_SCRIPT_OPTIONS or arg.startswith("--file=")
                for arg in args
            )
            skip_first_positional = bool(positional) and not has_explicit_program
        elif cmd == "find":
            candidates.extend(_collect_find_roots(args, config))
            continue

        skipped = False
        for arg in args:
            if skip_first_positional and not skipped and positional and arg == positional[0]:
                skipped = True
                continue
            candidates.extend(
                _extract_path_candidates_from_arg(
                    arg,
                    config,
                    include_credential_names=include_credential_names,
                )
            )

    return _dedupe(candidates)


def _candidate_sensitive_paths(file_path):
    """Yield path variants for sensitive-write matching."""
    expanded = os.path.expanduser(file_path)
    candidates = [expanded]
    try:
        real = os.path.realpath(expanded)
        if real not in candidates:
            candidates.append(real)
    except (OSError, ValueError):
        pass
    return candidates


def _is_sensitive_write_target_path(file_path):
    """Check if a path should trigger the sensitive_write_target ask flow."""
    shell_init = {
        ".bashrc", ".bash_profile", ".bash_login", ".profile",
        ".zshrc", ".zprofile", ".zshenv", ".zlogin",
    }
    sensitive_dotfiles = {".gitconfig", ".npmrc", ".yarnrc"}
    hooks_dir = f"{os.sep}.git{os.sep}hooks"
    home = os.path.expanduser("~")

    for candidate in _candidate_sensitive_paths(file_path):
        normalized_path = candidate.rstrip(os.sep)
        basename = os.path.basename(normalized_path)
        if basename in shell_init:
            return True
        if hooks_dir + os.sep in normalized_path or normalized_path.endswith(hooks_dir):
            return True
        if basename in sensitive_dotfiles and os.path.dirname(normalized_path) == home:
            return True

    return False


def _is_non_file_command(command):
    """Return True if the command doesn't access its arguments as files."""
    parts = command.split(None, 1)
    return bool(parts) and parts[0] in _NON_FILE_COMMANDS

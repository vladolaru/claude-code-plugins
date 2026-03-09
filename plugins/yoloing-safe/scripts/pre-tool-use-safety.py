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
import shlex
import sys
import time
from urllib.parse import urlparse

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


def _merge_clobber_redirect_tokens(tokens):
    """Merge `>|` forms that shlex splits into redirect + pipe tokens."""
    merged = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in {">", "1>", "2>"} and i + 1 < len(tokens) and tokens[i + 1] == "|":
            merged.append(tok + "|")
            i += 2
            continue
        merged.append(tok)
        i += 1
    return merged

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULTS = {
    "credential_patterns": [
        r"\.env\b(?!\.sample|\.example|\.template)",
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
        r"\.envrc$",
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


def _is_path_within_self_protected(real_path):
    """Check whether a resolved path is in self-protected locations."""
    for protected in SELF_PROTECTED_PATHS:
        protected_real = os.path.realpath(protected)
        if real_path == protected_real or real_path.startswith(protected_real + os.sep):
            return True
    return False


def _is_self_protected_path(file_path):
    """Check if a file path resolves to a self-protected location.

    Uses realpath to resolve symlinks, preventing symlink-based bypasses.
    """
    try:
        real_path = os.path.realpath(os.path.expanduser(file_path))
        if _is_path_within_self_protected(real_path):
            return True
    except (ValueError, OSError):
        pass
    return False


_SHELL_SEPARATORS = {"&&", "||", ";", "|", "&", "\n"}
_WRAPPER_COMMANDS = {
    "command", "env", "sudo", "nice", "nohup", "time", "exec", "strace", "ionice", "taskset"
}
_REDIRECT_TOKENS = {">", ">>", ">|", "1>", "1>>", "1>|", "2>", "2>>", "2>|"}
_RE_INLINE_REDIRECT = re.compile(r"^(?:[12]?>{1,2}\|?|>{1,2}\|?)(.+)$")
_INPUT_REDIRECT_TOKENS = {"<", "0<"}
_RE_INLINE_INPUT_REDIRECT = re.compile(r"^(?:0?<)(?!<)(.+)$")
_RE_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_RE_INTERPRETER_WRITE_SYNTAX = re.compile(
    r"\b(?:open\(|Path\(|write(?:File|FileSync|Text|Bytes)|appendFile(?:Sync)?\(|"
    r"createWriteStream\(|File\.(?:write|binwrite|open)\()",
    re.DOTALL,
)
_GREP_LIKE_COMMANDS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
_SED_SCRIPT_OPTIONS = {"-e", "-f", "--expression", "--file"}
_AWK_SCRIPT_OPTIONS = {"-f", "--file"}
_FIND_PRE_EXPR_OPTIONS = {"-H", "-L", "-P"}
_SHELL_INLINE_COMMANDS = {"bash", "sh", "zsh"}


def _tokenize_shell(command):
    """Tokenize shell command with operator tokens preserved."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return _merge_clobber_redirect_tokens(list(lexer))


def _split_shell_segments(tokens):
    """Split token list into command segments on chain operators."""
    segments = []
    current = []
    for tok in tokens:
        if tok in _SHELL_SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(tok)
    if current:
        segments.append(current)
    return segments


def _tokenized_segments(command):
    """Return shell-tokenized command segments."""
    try:
        return _split_shell_segments(_tokenize_shell(command))
    except ValueError:
        return []


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


def _segment_command_and_args(segment):
    """Extract command name + args, skipping env assignments and wrappers."""
    idx = 0
    while idx < len(segment) and _RE_ASSIGNMENT.match(segment[idx]):
        idx += 1
    while idx < len(segment) and os.path.basename(segment[idx]) in _WRAPPER_COMMANDS:
        idx += 1
    if idx >= len(segment):
        return "", []
    return os.path.basename(segment[idx]), segment[idx + 1:]


def _command_and_args_from_text(command):
    """Extract command name + args from command text."""
    segments = _tokenized_segments(command)
    if not segments:
        return "", []
    return _segment_command_and_args(segments[0])


def _collect_redirection_targets(segment):
    """Collect shell redirection targets from one tokenized segment."""
    targets = []
    for i, tok in enumerate(segment):
        if tok in _REDIRECT_TOKENS and i + 1 < len(segment):
            target = segment[i + 1]
            if target and not target.startswith("&"):
                targets.append(target)
            continue
        inline = _RE_INLINE_REDIRECT.match(tok)
        if inline:
            target = inline.group(1)
            if target and not target.startswith("&"):
                targets.append(target)
    return targets


def _collect_input_redirection_sources(segment):
    """Collect shell input-redirection sources from one tokenized segment."""
    sources = []
    for i, tok in enumerate(segment):
        if tok in _INPUT_REDIRECT_TOKENS and i + 1 < len(segment):
            source = segment[i + 1]
            if source and not source.startswith("&"):
                sources.append(source)
            continue
        inline = _RE_INLINE_INPUT_REDIRECT.match(tok)
        if inline:
            source = inline.group(1)
            if source and not source.startswith("&"):
                sources.append(source)
    return sources


def _collect_positional_args(args):
    """Collect command positional args, skipping options and `--` marker."""
    positional = []
    after_double_dash = False
    for arg in args:
        if arg == "--":
            after_double_dash = True
            continue
        if not after_double_dash and arg.startswith("-"):
            continue
        positional.append(arg)
    return positional


def _collect_write_targets_for_segment(segment):
    """Collect likely content-write or destructive file targets from a segment."""
    targets = _collect_redirection_targets(segment)
    cmd, args = _segment_command_and_args(segment)
    if not cmd:
        return targets

    positional = _collect_positional_args(args)
    if cmd in {"cp", "install", "rsync", "ln"} and positional:
        targets.append(positional[-1])
    elif cmd == "mv" and positional:
        targets.extend(positional)
    elif cmd == "tee" and positional:
        targets.extend(positional)
    elif cmd == "sed":
        has_inplace = any(
            arg == "-i"
            or arg.startswith("-i")
            or arg == "--in-place"
            or arg.startswith("--in-place=")
            for arg in args
        )
        if has_inplace and len(positional) >= 2:
            # positional[0] is usually sed expression; rest are target files
            targets.extend(positional[1:])
    elif cmd in {"touch", "truncate", "rm", "unlink", "rmdir"}:
        targets.extend(positional)

    return _dedupe(targets)


def _collect_symlink_source_targets(segment):
    """Collect the SOURCE argument of `ln -s` commands.

    When creating a symlink (`ln -s target link_name`), the first positional
    arg is the TARGET (what the symlink points to). If that target is a
    self-protected path, creating the symlink could enable TOCTOU bypasses
    where a later segment writes through the symlink.
    """
    cmd, args = _segment_command_and_args(segment)
    if cmd != "ln":
        return []
    # Only applies to symbolic links (-s flag)
    has_symlink_flag = any(
        arg == "-s" or (arg.startswith("-") and not arg.startswith("--") and "s" in arg[1:])
        for arg in args
        if arg != "--"
    )
    if not has_symlink_flag:
        return []
    positional = _collect_positional_args(args)
    # First positional is the target (what the symlink points to)
    if positional:
        return [positional[0]]
    return []


def _collect_protected_mutation_targets_for_segment(segment):
    """Collect targets that would mutate or disable protected infrastructure."""
    targets = list(_collect_write_targets_for_segment(segment))
    cmd, args = _segment_command_and_args(segment)
    if not cmd:
        return targets

    positional = _collect_positional_args(args)
    if cmd == "mkdir":
        targets.extend(positional)
    elif cmd == "chmod" and len(positional) >= 2:
        targets.extend(positional[1:])
    elif cmd in {"chown", "chgrp"} and len(positional) >= 2:
        targets.extend(positional[1:])

    return _dedupe(targets)


def _update_cwd_from_cd(segment, cwd):
    """Track explicit `cd <dir>` segments for relative-path resolution."""
    cmd, args = _segment_command_and_args(segment)
    if cmd != "cd" or not args:
        return cwd
    dest = _resolve_candidate_path(args[0], cwd)
    return dest or cwd


def _dedupe(items):
    """Dedupe a sequence while preserving order."""
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _base_cwd_for_bash(tool_input):
    """Resolve the effective cwd for Bash path handling."""
    base_cwd = os.getcwd()
    if isinstance(tool_input, dict):
        maybe_cwd = tool_input.get("cwd")
        if isinstance(maybe_cwd, str) and maybe_cwd:
            base_cwd = os.path.expanduser(maybe_cwd)
    return os.path.realpath(base_cwd)


def _whole_bash_command(command, tool_input):
    """Return the normalized full Bash command when available."""
    if isinstance(tool_input, dict):
        raw_command = tool_input.get("command")
        if isinstance(raw_command, str) and raw_command:
            return normalize_command(strip_writer_heredocs(raw_command))
    return command or ""


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


def _split_bash_segments(command):
    """Split a Bash command into normalized segments using shell-aware tokenization."""
    if not command:
        return []
    try:
        tokens = _tokenize_shell(command)
    except ValueError:
        command = normalize_command(command)
        return [command] if command else []

    segments = []
    for segment in _split_shell_segments(tokens):
        seg = normalize_command(" ".join(segment).strip())
        if seg:
            segments.append(seg)
    return segments


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
        if resolved and _is_path_within_self_protected(resolved):
            return True

    # Check symlink source targets: ln -s <protected_path> <link> enables
    # TOCTOU if a later segment writes through the link.
    for _target, resolved in _collect_bash_targets(
        command,
        tool_input,
        _collect_symlink_source_targets,
    ):
        if resolved and _is_path_within_self_protected(resolved):
            return True

    for _target, resolved in _collect_interpreter_write_targets(command, tool_input):
        if resolved and _is_path_within_self_protected(resolved):
            return True

    # Keep a conservative fallback for write APIs that mention protected paths
    # literally but are not covered by the path extractors above.
    if _RE_INTERPRETER_WRITE_SYNTAX.search(command) and _command_mentions_protected_path(command):
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
    # Expand zero_access_paths so tilde, absolute, and shell variable forms match.
    # e.g., ~/.ssh/ → also /Users/user/.ssh/, $HOME/.ssh/, ${HOME}/.ssh/
    expanded = []
    for p in config["zero_access_paths"]:
        expanded.append(p)
        ep = os.path.expanduser(p)
        if ep != p:
            expanded.append(ep)
            # Add $HOME and ${HOME} forms to catch shell variable usage
            home = os.path.expanduser("~")
            if ep.startswith(home):
                suffix = ep[len(home):]
                expanded.append("$HOME" + suffix)
                expanded.append("${HOME}" + suffix)
    config["zero_access_paths"] = expanded
    return config


# ---------------------------------------------------------------------------
# Command Normalization
# ---------------------------------------------------------------------------

_WRAPPER_RE = re.compile(
    r"^(command|env|sudo|nice|nohup|time|exec|strace|ionice|taskset)\s+"
)

# ---------------------------------------------------------------------------
# Git/npm Subcommand Normalization
# ---------------------------------------------------------------------------

# Git global options that consume the next token as a value
_GIT_GLOBAL_OPTS_WITH_ARG = frozenset({
    '-C', '-c', '--git-dir', '--work-tree', '--namespace',
    '--exec-path', '--super-prefix',
})
# Git global boolean flags (no value)
_GIT_GLOBAL_OPTS_BOOL = frozenset({
    '--bare', '--no-pager', '--paginate', '-p',
    '--no-replace-objects', '-P', '--no-optional-locks',
    '--literal-pathspecs', '--glob-pathspecs',
    '--noglob-pathspecs', '--icase-pathspecs',
})


def _strip_git_global_opts(command):
    """Strip git global options to expose the subcommand.

    Git accepts options like -C <path>, -c <key=val>, --no-pager between
    'git' and the subcommand. These are stripped so anchored patterns like
    ^git push still match.

    Only known global options are stripped — unknown flags stop the scan
    so subcommand-level flags (e.g. --force) are preserved.
    """
    if not command.startswith("git "):
        return command
    parts = command.split()
    result = ["git"]
    i = 1
    while i < len(parts):
        token = parts[i]
        if not token.startswith("-"):
            # Found subcommand — keep everything from here
            result.extend(parts[i:])
            break
        # --option=value form
        if "=" in token:
            key = token.split("=", 1)[0]
            if key in _GIT_GLOBAL_OPTS_WITH_ARG:
                i += 1
                continue
        # -C <path>, -c <key=val> etc. (next token is the value)
        if token in _GIT_GLOBAL_OPTS_WITH_ARG:
            i += 2
            continue
        # Boolean global flag
        if token in _GIT_GLOBAL_OPTS_BOOL:
            i += 1
            continue
        # Unknown flag — not a global option, keep everything from here
        result.extend(parts[i:])
        break
    return " ".join(result)


# npm global options that consume the next token
_NPM_GLOBAL_OPTS_WITH_ARG = frozenset({
    '--registry', '--prefix', '--userconfig', '--globalconfig',
    '--cache', '--loglevel', '--otp', '--workspace', '-w',
})
# npm global boolean flags
_NPM_GLOBAL_OPTS_BOOL = frozenset({
    '--global', '-g', '--json', '--long', '-l',
    '--parseable', '--silent', '--quiet', '--verbose',
})


def _strip_npm_global_opts(command):
    """Strip npm global options to expose the subcommand.

    Same approach as git: only known global options are stripped.
    Subcommand-level flags like --dry-run are preserved.
    """
    if not command.startswith("npm "):
        return command
    parts = command.split()
    result = ["npm"]
    i = 1
    while i < len(parts):
        token = parts[i]
        if not token.startswith("-"):
            result.extend(parts[i:])
            break
        if "=" in token:
            key = token.split("=", 1)[0]
            if key in _NPM_GLOBAL_OPTS_WITH_ARG:
                i += 1
                continue
        if token in _NPM_GLOBAL_OPTS_WITH_ARG:
            i += 2
            continue
        if token in _NPM_GLOBAL_OPTS_BOOL:
            i += 1
            continue
        result.extend(parts[i:])
        break
    return " ".join(result)


def normalize_command(cmd):
    """Strip path prefixes, command wrappers, and collapse whitespace."""
    if not cmd:
        return ""
    stripped = cmd.lstrip()
    parts = stripped.split(None, 1)
    normalized = stripped
    # Strip leading absolute path from the command binary only
    # e.g., /opt/homebrew/bin/git → git, but rm /home/user/bin/rm stays unchanged
    if parts:
        binary = parts[0]
        if binary.startswith("/") and "/" in binary[1:]:
            remainder = parts[1] if len(parts) > 1 else ""
            normalized = f"{os.path.basename(binary)} {remainder}".strip()
    # Strip command wrappers, looping for nesting (sudo env rm → rm)
    prev = None
    while prev != normalized:
        prev = normalized
        normalized = _WRAPPER_RE.sub("", normalized)
    # Collapse horizontal whitespace but preserve newlines as command separators.
    normalized = _RE_WHITESPACE.sub(" ", normalized).strip()
    # Strip git/npm global options only for single segments. Compound commands
    # are normalized segment-by-segment later, after shell-aware splitting.
    if not _RE_CHAIN_OPS.search(normalized):
        normalized = _strip_git_global_opts(normalized)
        normalized = _strip_npm_global_opts(normalized)
    return normalized


# Self-protection message — not in RULES because it's hardcoded and non-disableable.
_SELF_PROTECTION_MESSAGE = "This file is part of the safety hook infrastructure. Modifying it could disable safety protections. Ask the user to make changes manually."

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
    # rm in temp directories — ALL targets must be temp paths (anchored with $)
    ("destructive_deletion", re.compile(r"^rm\s+-[rfRF]*\s+(?:(?:/tmp/|/var/tmp/|\$TMPDIR/)\S*\s*)+$")),
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
_RE_CHAIN_OPS = re.compile(r"(&&|\|\||[|;&]|\n)")

# Command normalization
_RE_WHITESPACE = re.compile(r"[^\S\n]+")

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
_RE_FIND_DELETE = re.compile(r"\bfind\b.*-delete\b")
# Scoped find roots: relative dot-paths, $TMPDIR, /tmp, /var/tmp
_RE_FIND_SCOPED_ROOT = re.compile(
    r"^find\s+(?:\.(?:$|[\w./\-]|\s)|\$TMPDIR\b|\$TMP\b|/tmp/|/var/tmp/)"
)
# Parent-directory traversal in find paths — blocks scoped-root allowance
_RE_FIND_TRAVERSAL = re.compile(r"^find\s+\S*\.\.")
_RE_FIND_EXEC_RM = re.compile(r"\bfind\b.*-exec\s+rm\b")
_RE_XARGS_RM = re.compile(r"\bxargs\s+rm\b")
_RE_EVAL_RM = re.compile(r"\beval\b.*\brm\b")
_RE_RM_RECURSIVE = re.compile(r"(?:^|\s)-[a-zA-Z]*[rR]|--recursive")
_RE_RM_FORCE = re.compile(r"(?:^|\s)-[a-zA-Z]*[fF]|--force")

# Network exfiltration
_RE_CURL_POST_DATA = re.compile(
    r"\bcurl\b.*("
    r"-d\s+@|"
    r"--data(?:-ascii|-binary|-raw)?(?:\s+|=)@|"
    r"--json(?:\s+|=)@"
    r")"
)
_RE_CURL_UPLOAD = re.compile(
    r"\bcurl\b.*("
    r"-F\s+|"
    r"--form(?:\s+|=)|"
    r"-T\s+|"
    r"--upload-file(?:\s+|=)"
    r")"
)
_RE_WGET_POST_FILE = re.compile(r"\bwget\b.*--post-file")
_RE_PIPE_TO_NC = re.compile(r"\|\s*nc\b")
_RE_NC_BARE = re.compile(r"^nc\b")
_RE_NC_REDIRECT = re.compile(r"\bnc\b.*<")
_RE_CURL_WGET_PIPE_SHELL = re.compile(r"\b(curl|wget)\b.*\|\s*(bash|sh|zsh)\b")
_RE_WGET_TO_STDOUT = re.compile(r"\bwget\b.*-[a-zA-Z]*O\s*-")
_RE_CURL_TO_STDOUT = re.compile(r"\bcurl\b.*(-o\s*-|--output\s*-)")
_RE_SCP_UPLOAD = re.compile(r"\bscp\b.*\s\S+@\S+:\S*$")
_RE_RSYNC_UPLOAD = re.compile(r"\brsync\b.*\s\S+@\S+:\S*$")
_RE_URL = re.compile(r"https?://[^\s'\"<>]+")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Package publishing
_RE_NPM_PUBLISH = re.compile(r"\bnpm publish\b")
_RE_DRY_RUN = re.compile(r"--dry-run")
_RE_TWINE_PIP_UPLOAD = re.compile(r"\b(twine|pip) upload\b")
_RE_GEM_PUSH = re.compile(r"\bgem push\b")

# SSH remote destruction
_RE_SSH_CMD = re.compile(r"^ssh\b")
_RE_SSH_QUOTED_CMD = re.compile(r"""ssh\s+\S+\s+['"](.+?)['"]""")

# Git operations
_RE_GIT_PUSH = re.compile(r"^git push\b")
_RE_GIT_FORCE_FLAG = re.compile(r"(--force\b|-f\b)")
# git push with explicit refspec alternatives (--tags, --all, --mirror)
_RE_GIT_PUSH_REFSPEC_ALT = re.compile(r"--(tags|all|mirror)\b")
_RE_GIT_CHECKOUT = re.compile(r"^git checkout\b")
_RE_DOUBLE_DASH_SEP = re.compile(r"\s--\s")
_RE_GIT_RESTORE = re.compile(r"^git restore\b")
_RE_STAGED_FLAG = re.compile(r"(--staged|-S)\b")
_RE_WORKTREE_FLAG = re.compile(r"(--worktree|-W)\b")
_RE_GIT_CLEAN = re.compile(r"^git clean\b")
_RE_CLEAN_FORCE_FLAG = re.compile(r"-[a-zA-Z]*f")
_RE_CLEAN_DRY_RUN = re.compile(r"(-[a-zA-Z]*n|--dry-run)")
_RE_GIT_BRANCH_DELETE = re.compile(r"^git branch\s+-D\b")
_RE_GIT_REMOTE_REMOVE = re.compile(r"^git remote remove\b")
_RE_GIT_REFLOG_EXPIRE = re.compile(r"^git reflog expire\b")
_RE_GIT_GC_PRUNE = re.compile(r"^git gc\b.*--prune=")
_RE_GIT_PUSH_DELETE = re.compile(r"^git push\b.*(?:--delete|-d)\b")
_RE_GIT_PUSH_COLON_REF = re.compile(r"^git push\b.*\s:[^-\s]")

# Permission changes
_RE_CHMOD_PLUS_X = re.compile(r"^chmod \+x\b")
_RE_SUDO_CHMOD = re.compile(r"\bsudo\s+chmod\b")
_RE_CHMOD = re.compile(r"\bchmod\b")


# Database destructive
_RE_DROP_OBJECT = re.compile(r"\bDROP\s+(DATABASE|TABLE|SCHEMA)\b")
_RE_TRUNCATE = re.compile(r"\bTRUNCATE\b")
_RE_DELETE_FROM = re.compile(r"\bDELETE\s+FROM\b")
_RE_WHERE = re.compile(r"\bWHERE\b")
_RE_DROPDB_DROPUSER = re.compile(r"\b(dropdb|dropuser)\b")
_RE_REDIS_FLUSH = re.compile(r"\bredis-cli\b.*\bFLUSH(ALL|DB)\b", re.IGNORECASE)
_RE_PIPE_TO_DB_CLIENT = re.compile(r"\|\s*(psql|mysql|sqlite3|redis-cli)\b")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Non-File Commands — skip credential/path checks for these
# ---------------------------------------------------------------------------

# Commands that do NOT access their arguments as files. When a Bash command
# starts with one of these, credential_access and zero_access_paths rules
# skip the whole-command-as-path check to avoid false positives like
# `echo .env` being blocked.
_NON_FILE_COMMANDS = frozenset({
    "echo", "printf", "export", "set", "unset", "test",
    "[", "[[", "true", "false", "alias", "type", "which",
    "declare", "local", "readonly", "return", "exit",
    "break", "continue", "shift", "trap", "read",
})

_RE_REMOTE_PATH = re.compile(r"^\S+@\S+:\S+$")


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
    """Collect find root paths without treating patterns like -name '.env' as files."""
    roots = []
    i = 0
    while i < len(args) and (
        args[i] in _FIND_PRE_EXPR_OPTIONS
        or args[i].startswith("-D")
        or args[i].startswith("-O")
    ):
        i += 1

    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            continue
        if arg.startswith("-") or arg in {"(", ")", "!", "-o", "-a", ","}:
            break
        roots.extend(_extract_path_candidates_from_arg(arg, config))
        i += 1

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


def _rm_has_recursive_force(args):
    """Return True when rm args include both recursive and force flags."""
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


def _shell_payloads(cmd, args):
    """Extract payload strings executed by shell-style `-c` invocations."""
    payloads = []
    for i, arg in enumerate(args[:-1]):
        if arg == "-c":
            payloads.append(args[i + 1])
    if cmd in _SHELL_INLINE_COMMANDS or cmd == "su":
        return payloads

    nested_payloads = []
    for i in range(len(args) - 2):
        if args[i] in _SHELL_INLINE_COMMANDS and args[i + 1] == "-c":
            nested_payloads.append(args[i + 2])
    return nested_payloads


def _command_contains_rm_rf(command):
    """Return True when command text includes an actual rm -rf style invocation."""
    for segment in _tokenized_segments(command):
        cmd, args = _segment_command_and_args(segment)
        if cmd == "rm" and _rm_has_recursive_force(args):
            return True
    return False


def _command_invokes_database_client(cmd, args):
    """Return True when the command or its wrapper args invoke a DB client."""
    database_clients = {"psql", "mysql", "sqlite3", "redis-cli", "dropdb", "dropuser"}
    if cmd in database_clients:
        return True
    return any(arg in database_clients for arg in args)


# Detection Functions (custom — complex rules that need procedural logic)
# Each returns (detected: bool, message: str | None)
# Signature: (command, tool_name, tool_input, config) -> (bool, str|None)
# ---------------------------------------------------------------------------

# --- Block Tier: Filesystem Destruction ---


def detect_destructive_deletion(command, tool_name, tool_input, config):
    """Detect rm with recursive+force while skipping inert string mentions."""
    segments = _tokenized_segments(command)
    if not segments:
        return False, None

    for segment in segments:
        cmd, args = _segment_command_and_args(segment)
        if not cmd:
            continue
        if cmd in _NON_FILE_COMMANDS:
            continue
        if cmd == "rm" and _rm_has_recursive_force(args):
            return True, RULES["destructive_deletion"]["message"]
        for payload in _shell_payloads(cmd, args):
            if _command_contains_rm_rf(payload):
                return True, RULES["destructive_deletion"]["message"]

    # Shell tokenization around heredocs can leave chained `rm -rf` text inside
    # a single `cat <<EOF ... && rm -rf` segment. Fall back only for heredoc or
    # chain contexts so inert strings like `echo 'rm -rf /'` stay allowed.
    if ("<<" in command or _RE_CHAIN_OPS.search(command)) and not _is_non_file_command(command):
        if re.search(r"\brm\b", command) and _RE_RM_RECURSIVE.search(command) and _RE_RM_FORCE.search(command):
            return True, RULES["destructive_deletion"]["message"]
    return False, None


def detect_alternative_deletion(command, tool_name, tool_input, config):
    """Detect find -delete, find -exec rm, xargs rm, eval rm."""
    # find -delete
    if _RE_FIND_DELETE.search(command):
        # Allow scoped cleanup (relative dot-paths, $TMPDIR, /tmp, /var/tmp)
        # but block parent-directory traversal (find ./../../etc -delete)
        if _RE_FIND_TRAVERSAL.search(command) or not _RE_FIND_SCOPED_ROOT.search(command):
            return True, RULES["alternative_deletion"]["message"]
    # find -exec rm
    if _RE_FIND_EXEC_RM.search(command):
        return True, RULES["alternative_deletion"]["message"]
    # xargs rm
    if _RE_XARGS_RM.search(command):
        return True, RULES["alternative_deletion"]["message"]
    # eval with rm
    if _RE_EVAL_RM.search(command):
        return True, RULES["alternative_deletion"]["message"]
    return False, None


# --- Block Tier: Network, Credentials, Publishing, SSH, GitHub, Paths ---


def _targets_only_loopback(command):
    """Return True when every parsed URL target host is loopback."""
    urls = _RE_URL.findall(command)
    if not urls:
        return False
    hosts = []
    for url in urls:
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            continue
        if host:
            hosts.append(host)
    if not hosts:
        return False
    return all(host in _LOOPBACK_HOSTS for host in hosts)


def detect_network_exfiltration(command, tool_name, tool_input, config):
    """Detect data exfiltration via curl, wget, nc, scp, rsync."""
    whole_command = _whole_bash_command(command, tool_input)
    current_cmd, _args = _command_and_args_from_text(command)
    is_loopback_target = _targets_only_loopback(command)

    # curl posting data (file or stdin)
    if _RE_CURL_POST_DATA.search(command):
        if not is_loopback_target:
            return True, RULES["network_exfiltration"]["message"]
    # curl file upload (-F form, -T upload, --upload-file)
    if _RE_CURL_UPLOAD.search(command):
        if not is_loopback_target:
            return True, RULES["network_exfiltration"]["message"]
    # wget posting a file — loopback bypass applies here too
    if _RE_WGET_POST_FILE.search(command):
        if not is_loopback_target:
            return True, RULES["network_exfiltration"]["message"]
    # nc (netcat) — loopback not a meaningful exception (nc is raw TCP)
    if _RE_PIPE_TO_NC.search(command) or _RE_NC_BARE.search(command) or _RE_NC_REDIRECT.search(command):
        return True, RULES["network_exfiltration"]["message"]
    # Piping curl/wget output to bash/sh (remote code execution) — always block
    if _RE_CURL_WGET_PIPE_SHELL.search(command):
        return True, RULES["network_exfiltration"]["message"]
    if current_cmd in {"curl", "wget"} and _RE_CURL_WGET_PIPE_SHELL.search(whole_command):
        return True, RULES["network_exfiltration"]["message"]
    # wget/curl writing to stdout from remote URL (segment of a pipe-to-shell)
    if _RE_WGET_TO_STDOUT.search(command) or _RE_CURL_TO_STDOUT.search(command):
        if not is_loopback_target:
            return True, RULES["network_exfiltration"]["message"]
    # scp upload — loopback not applicable (scp always remote)
    if _RE_SCP_UPLOAD.search(command):
        return True, RULES["network_exfiltration"]["message"]
    # rsync upload — loopback not applicable
    if _RE_RSYNC_UPLOAD.search(command):
        return True, RULES["network_exfiltration"]["message"]
    return False, None


def detect_credential_access(command, tool_name, tool_input, config):
    """Detect access to credential files via Read/Edit/Write tools or Bash."""
    cred_patterns = config.get("credential_patterns", DEFAULTS["credential_patterns"])
    safe_patterns = config.get("credential_safe_patterns", DEFAULTS["credential_safe_patterns"])

    paths_to_check = []
    if tool_name in ("Read", "Edit", "Write"):
        file_path = tool_input.get("file_path", "")
        if file_path:
            paths_to_check.append(file_path)
    elif tool_name == "Bash" and command:
        paths_to_check.extend(
            _collect_bash_path_candidates(
                command,
                config,
                include_credential_names=True,
            )
        )

    if not paths_to_check:
        return False, None

    for file_path in paths_to_check:
        # Check safe patterns first (case-insensitive for case-insensitive filesystems)
        if any(re.search(safe_pat, file_path, re.IGNORECASE) for safe_pat in safe_patterns):
            continue
        # Check credential patterns (case-insensitive for case-insensitive filesystems)
        if any(re.search(cred_pat, file_path, re.IGNORECASE) for cred_pat in cred_patterns):
            return True, RULES["credential_access"]["message"]

    return False, None


def detect_package_publishing(command, tool_name, tool_input, config):
    """Detect package publishing commands."""
    # For compound commands, check each segment independently
    # (prevents "npm publish --dry-run && npm publish" from being allowed
    #  because --dry-run appears in the first segment)
    segments = _split_bash_segments(command) or [command]
    for seg in segments:
        # npm publish (without --dry-run in this segment)
        if _RE_NPM_PUBLISH.search(seg) and not _RE_DRY_RUN.search(seg):
            return True, RULES["package_publishing"]["message"]
        # twine upload / pip upload
        if _RE_TWINE_PIP_UPLOAD.search(seg):
            return True, RULES["package_publishing"]["message"]
        # gem push
        if _RE_GEM_PUSH.search(seg):
            return True, RULES["package_publishing"]["message"]
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
        # Unquoted: take everything after 'ssh' as a single string. This is
        # intentionally naive — it includes flags and hostnames alongside the
        # actual remote command. That's acceptable because the subsequent
        # substring checks ("rm ", "drop ", etc.) are forgiving enough to
        # match within the combined string for real-world invocations.
        parts = command.split(None, 1)
        remote_cmd = parts[1].lower() if len(parts) > 1 else ""
    if not remote_cmd:
        return False, None
    destructive = ["rm ", "rm\t", "drop ", "truncate ", "delete ", "mkfs", "dd "]
    for pattern in destructive:
        if pattern in remote_cmd:
            return True, RULES["ssh_remote_destruction"]["message"]
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
        paths_to_check.extend(_collect_bash_path_candidates(command, config))

    for check_path in paths_to_check:
        check_lower = check_path.lower()
        for zero_path in zero_paths:
            if zero_path.lower() in check_lower:
                return True, RULES["zero_access_paths"]["message"]

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
    # Force-push variants are handled by git_force_push ask rule.
    if _RE_GIT_FORCE_FLAG.search(command):
        return False, None
    # --tags, --all, --mirror are explicit refspec alternatives
    if _RE_GIT_PUSH_REFSPEC_ALT.search(command):
        return False, None
    # Count non-flag arguments after 'git push'
    parts = command.split()
    non_flag_parts = [p for p in parts[2:] if not p.startswith("-")]
    # Need at least 2 non-flag args: remote + refspec
    if len(non_flag_parts) < 2:
        return True, RULES["git_bare_push"]["message"]
    return False, None


# --- Ask Tier: Git Operations ---

def detect_git_discard_changes(command, tool_name, tool_input, config):
    """Detect git checkout -- and git restore that discards working tree changes."""
    # git checkout -- <path> or git checkout <ref> -- <path>
    if _RE_GIT_CHECKOUT.search(command) and _RE_DOUBLE_DASH_SEP.search(command):
        return True, RULES["git_discard_changes"]["message"]
    # git restore (without --staged/-S alone — that's allowlisted)
    if _RE_GIT_RESTORE.search(command):
        has_staged = bool(_RE_STAGED_FLAG.search(command))
        has_worktree = bool(_RE_WORKTREE_FLAG.search(command))
        # If only --staged (no --worktree), it's safe (allowlisted)
        if has_staged and not has_worktree:
            return False, None
        # Otherwise it touches the worktree — dangerous
        return True, RULES["git_discard_changes"]["message"]
    return False, None


def detect_git_other_dangerous(command, tool_name, tool_input, config):
    """Detect other dangerous git ops: clean -f, branch -D, remote remove, reflog expire, gc --prune."""
    # git clean with -f (force) but without -n/--dry-run (allowlisted)
    if _RE_GIT_CLEAN.search(command):
        if _RE_CLEAN_FORCE_FLAG.search(command) and not _RE_CLEAN_DRY_RUN.search(command):
            return True, RULES["git_other_dangerous"]["message"]
    # git branch -D (force delete)
    if _RE_GIT_BRANCH_DELETE.search(command):
        return True, RULES["git_other_dangerous"]["message"]
    # git remote remove
    if _RE_GIT_REMOTE_REMOVE.search(command):
        return True, RULES["git_other_dangerous"]["message"]
    # git reflog expire
    if _RE_GIT_REFLOG_EXPIRE.search(command):
        return True, RULES["git_other_dangerous"]["message"]
    # git gc --prune=now
    if _RE_GIT_GC_PRUNE.search(command):
        return True, RULES["git_other_dangerous"]["message"]
    # git push --delete (remote branch/tag deletion)
    if _RE_GIT_PUSH_DELETE.search(command):
        return True, RULES["git_other_dangerous"]["message"]
    # git push origin :refs/... (colon-prefix deletion syntax)
    if _RE_GIT_PUSH_COLON_REF.search(command):
        return True, RULES["git_other_dangerous"]["message"]
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
        return True, RULES["permission_changes"]["message"]
    try:
        tokens = _tokenize_shell(command)
    except ValueError:
        tokens = []
    for segment in _split_shell_segments(tokens):
        cmd, args = _segment_command_and_args(segment)
        positional = _collect_positional_args(args)
        if cmd == "chmod" and positional:
            mode = positional[0]
            if re.fullmatch(r"0?777", mode):
                return True, RULES["permission_changes"]["message"]
            if re.fullmatch(r"0?[4267]\d{3}", mode):
                return True, RULES["permission_changes"]["message"]
            if re.fullmatch(r"[ugo]*\+s", mode):
                return True, RULES["permission_changes"]["message"]
        if cmd in {"chown", "chgrp"} and any(
            arg == "--recursive" or (arg.startswith("-") and "R" in arg[1:])
            for arg in args
        ):
            return True, RULES["permission_changes"]["message"]
    return False, None


def detect_database_destructive(command, tool_name, tool_input, config):
    """Detect destructive database commands."""
    whole_command = _whole_bash_command(command, tool_input)
    pipes_to_db = bool(_RE_PIPE_TO_DB_CLIENT.search(whole_command))

    for segment in _tokenized_segments(command):
        cmd, args = _segment_command_and_args(segment)
        if not cmd:
            continue

        segment_command = " ".join(segment)
        segment_upper = segment_command.upper()
        invokes_database = pipes_to_db or _command_invokes_database_client(cmd, args)

        if _RE_DROPDB_DROPUSER.search(segment_command) and invokes_database:
            return True, RULES["database_destructive"]["message"]
        if _RE_REDIS_FLUSH.search(segment_command) and invokes_database:
            return True, RULES["database_destructive"]["message"]
        if not invokes_database:
            continue
        if _RE_DROP_OBJECT.search(segment_upper):
            return True, RULES["database_destructive"]["message"]
        if _RE_TRUNCATE.search(segment_upper):
            return True, RULES["database_destructive"]["message"]
        if _RE_DELETE_FROM.search(segment_upper) and not _RE_WHERE.search(segment_upper):
            return True, RULES["database_destructive"]["message"]
    return False, None


def detect_sensitive_write_target(command, tool_name, tool_input, config):
    """Detect Write/Edit to shell init files, git hooks, or package config."""
    paths_to_check = []
    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if file_path:
            paths_to_check.append(file_path)
    elif tool_name == "Bash" and command:
        for raw_path, resolved_path in _collect_bash_targets(
            command,
            tool_input,
            _collect_write_targets_for_segment,
        ):
            paths_to_check.append(resolved_path or raw_path)

    for file_path in paths_to_check:
        if _is_sensitive_write_target_path(file_path):
            return True, RULES["sensitive_write_target"]["message"]
    return False, None


# ---------------------------------------------------------------------------
# Rule Definitions
#
# Each rule is a dict with:
#   tier        "block" or "ask"
#   tools       set of tool names this rule applies to
#   message     guidance message for Claude
#   examples    example commands for e2e test generation
#   patterns    (optional) regex strings — any match triggers (OR)
#   pattern_groups (optional) list of AND-groups — all patterns in group must match
#   require     (optional) additional patterns that must ALL match (AND with patterns)
#   exclude     (optional) if any match, rule does NOT trigger
#   detect      (optional) custom function (command, tool_name, tool_input, config) -> (bool, str|None)
#
# Either patterns/pattern_groups or detect must be present.
# ---------------------------------------------------------------------------

RULES = {
    # =======================================================================
    # Block Tier
    # =======================================================================

    # --- Custom: token-aware rm -rf detection with shell-payload support ---
    "destructive_deletion": {
        "tier": "block",
        "tools": {"Bash"},
        "detect": detect_destructive_deletion,
        "message": "Use targeted file removal instead of recursive force-delete. Remove specific files by name (`rm file1 file2`), or use `git clean --dry-run` to preview before cleaning. This protects against accidental data loss.",
        "examples": ["rm -rf /"],
    },

    # --- Custom: scoped-root exclusion only applies to find -delete, not find -exec rm ---
    "alternative_deletion": {
        "tier": "block",
        "tools": {"Bash"},
        "detect": detect_alternative_deletion,
        "message": "Indirect deletion methods (`find -delete`, `xargs rm`) can affect more files than intended. Use explicit file paths for removal, or `git clean --dry-run` to preview what would be deleted.",
        "examples": ["find / -name '*.log' -delete"],
    },

    # --- Simple: mkfs OR (dd AND of=/dev/) ---
    "disk_formatting": {
        "tier": "block",
        "tools": {"Bash"},
        "patterns": [r"\bmkfs\b"],
        "pattern_groups": [[r"\bdd\b", r"of=/dev/"]],
        "message": "Disk formatting and raw device writes (`mkfs`, `dd`) are irreversible system-level operations. These should only be run manually with explicit user intent, never by an agent.",
        "examples": ["mkfs.ext4 /dev/sda1"],
    },

    # --- Complex: multiple exfil vectors + loopback exclusion ---
    "network_exfiltration": {
        "tier": "block",
        "tools": {"Bash"},
        "detect": detect_network_exfiltration,
        "message": "Piping data to external URLs can expose sensitive information. Write output to a local file instead, then review before sharing. Use `git push` for code and `gh` for GitHub interactions.",
        "examples": [
            "curl -d @/tmp/results.txt http://ci.example.com/upload",
            "scp ./dist/* deploy@staging.example.com:/var/www/",
        ],
    },

    # --- Complex: config-dependent patterns + tool_input inspection ---
    "credential_access": {
        "tier": "block",
        "tools": {"Bash", "Read", "Write", "Edit"},
        "detect": detect_credential_access,
        "message": "This file may contain secrets or credentials. Use `.env.example` or `.env.template` for reference files. If you need to read configuration, ask the user to provide the specific values.",
        "examples": ["./.env"],
    },

    # --- Complex: per-segment chain analysis + dry-run exclusion ---
    "package_publishing": {
        "tier": "block",
        "tools": {"Bash"},
        "detect": detect_package_publishing,
        "message": "Publishing packages to a registry is irreversible and public. Build and test locally, then let the user publish manually or through CI/CD. Use `--dry-run` to preview what would be published.",
        "examples": ["npm publish"],
    },

    # --- Complex: SSH command extraction (quoted + unquoted) ---
    "ssh_remote_destruction": {
        "tier": "block",
        "tools": {"Bash"},
        "detect": detect_ssh_remote_destruction,
        "message": "Executing destructive commands on remote hosts via SSH can cause irreversible damage to production systems. Run remote commands manually with explicit user intent.",
        "examples": ["ssh prod-server 'rm -rf /var/www/old'"],
    },

    # --- Simple: single regex ---
    "github_repo_deletion": {
        "tier": "block",
        "tools": {"Bash"},
        "patterns": [r"\bgh repo delete\b"],
        "message": "Deleting a GitHub repository is irreversible and destroys all issues, PRs, and history. This should only be done manually through the GitHub UI or CLI by the user.",
        "examples": ["gh repo delete"],
    },

    # --- Complex: config-dependent paths + tool_input inspection ---
    "zero_access_paths": {
        "tier": "block",
        "tools": {"Bash", "Read", "Write", "Edit"},
        "detect": detect_zero_access_paths,
        "message": "This path contains sensitive system or security data that should not be accessed by an agent. Ask the user to provide the specific information you need.",
        "examples": ["~/.ssh/id_rsa", "~/.aws/credentials"],
    },

    # --- Complex: argument counting logic ---
    "git_bare_push": {
        "tier": "block",
        "tools": {"Bash"},
        "detect": detect_git_bare_push,
        "message": "Push with an explicit branch to avoid pushing to an unexpected target. Use `git push origin HEAD` to push the current branch, or `git push origin <branch-name>` for a specific branch.",
        "examples": ["git push"],
    },

    # =======================================================================
    # Ask Tier — Git Operations
    # =======================================================================

    # --- Declarative: git push + force flag, excluding safe variants ---
    "git_force_push": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [r"^git push\b"],
        "require": [r"(--force\b|-f\b)"],
        "exclude": [r"--force-with-lease", r"--force-if-includes"],
        "message": "Force push rewrites remote history and can discard teammates' work. Use `--force-with-lease` for a safer alternative. Confirm this is intentional.",
        "examples": ["git push --force origin hotfix/fix-arena"],
    },

    # --- Declarative: git reset + --hard or --merge ---
    "git_hard_reset": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [r"^git reset\b"],
        "require": [r"(--hard|--merge)\b"],
        "message": "Hard reset discards uncommitted changes permanently. Use `git stash` first to preserve work. Confirm you want to proceed.",
        "examples": ["git reset --hard main"],
    },

    # --- Custom: two paths (checkout -- vs restore), conditional staged/worktree logic ---
    "git_discard_changes": {
        "tier": "ask",
        "tools": {"Bash"},
        "detect": detect_git_discard_changes,
        "message": "This discards uncommitted changes to working tree files. Use `git stash` first if you might need them. Confirm you want to proceed.",
        "examples": ["git checkout -- ."],
    },

    # --- Simple: single regex ---
    "git_destroy_stash": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [r"^git stash (drop|clear)\b"],
        "message": "Dropping or clearing stashes permanently destroys saved work. List stashes with `git stash list` first. Confirm this is intentional.",
        "examples": ["git stash drop"],
    },

    # --- Simple: single regex ---
    "git_history_rewrite": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [r"^git filter-(branch|repo)\b"],
        "message": "Rewriting git history (`filter-branch`, `filter-repo`) is irreversible on shared branches. Confirm this is intentional.",
        "examples": ["git filter-branch --force HEAD"],
    },

    # --- Declarative: git config + --global or --system ---
    "git_config_changes": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [r"^git config\b"],
        "require": [r"--(global|system)\b"],
        "message": "Global or system git config changes affect all repositories on this machine. Confirm this is intentional.",
        "examples": ["git config --global user.email 'test@test.com'"],
    },

    # --- Custom: 7 sub-checks, git clean has conditional dry-run exclusion ---
    "git_other_dangerous": {
        "tier": "ask",
        "tools": {"Bash"},
        "detect": detect_git_other_dangerous,
        "message": "This git operation can cause data loss or affect collaboration. Confirm you want to proceed.",
        "examples": ["git branch -D feature/goat-skins"],
    },

    # =======================================================================
    # Ask Tier — Non-Git
    # =======================================================================

    # --- Custom: chmod +x exclusion only for chmod patterns, not chown -R ---
    "permission_changes": {
        "tier": "ask",
        "tools": {"Bash"},
        "detect": detect_permission_changes,
        "message": "Broad permission changes can create security vulnerabilities. Use `chmod +x` to make a file executable (always allowed), or apply the minimum permission needed. Confirm this is intentional.",
        "examples": ["chmod 777 ."],
    },

    # --- Simple: match + require ---
    "brew_commands": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [
            r"^brew\s+(install|uninstall|remove|upgrade|tap|untap|link|unlink)\b",
        ],
        "message": "Installing system packages changes your development environment. Confirm you want to proceed, or consider adding the dependency to your project's package manager instead.",
        "examples": ["brew install libvips"],
    },

    # --- Simple: multiple OR patterns ---
    "docker_destructive": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [
            r"\bdocker\s+(system|volume|image)\s+prune\b",
            r"\bdocker\s+rm\s+-[a-zA-Z]*f",
            r"\bdocker[\s-]compose\s+down\b.*-v\b",
        ],
        "message": "This Docker command removes containers, volumes, or cached data that may be difficult to rebuild. Confirm you want to proceed.",
        "examples": ["docker system prune -af"],
    },

    # --- Complex: case-insensitive + WHERE exclusion ---
    "database_destructive": {
        "tier": "ask",
        "tools": {"Bash"},
        "detect": detect_database_destructive,
        "message": "This command permanently deletes database objects or data. Use a transaction with `BEGIN`/`ROLLBACK` to preview, or run against a dev database first. Confirm this is intentional.",
        "examples": ["sqlite3 goatbomber.db 'DROP TABLE users;'"],
    },

    # --- Simple: multiple OR patterns ---
    "terraform_destructive": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [
            r"\bterraform destroy\b",
            r"\bterraform apply\b.*-auto-approve\b",
            r"\bpulumi destroy\b",
        ],
        "message": "This infrastructure command can destroy live resources. Use `--dry-run` or `plan` first to preview changes. Confirm this is intentional.",
        "examples": ["terraform destroy -auto-approve"],
    },

    # --- Simple: multiple OR patterns ---
    "github_cicd_ops": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [
            r"\bgh\s+(secret|variable)\s+delete\b",
            r"\bgh\s+workflow\s+disable\b",
            r"\bgh\s+release\s+delete\b",
        ],
        "message": "Deleting GitHub secrets, variables, or disabling workflows affects CI/CD for all collaborators. Confirm this is intentional.",
        "examples": ["gh secret delete DEPLOY_KEY"],
    },

    # --- Complex: tool_input inspection + basename matching ---
    "sensitive_write_target": {
        "tier": "ask",
        "tools": {"Bash", "Write", "Edit"},
        "detect": detect_sensitive_write_target,
        "message": "This file controls shell behavior, git hooks, or package manager configuration. Modifying it can have persistent side effects beyond this session. Confirm this is intentional.",
        "examples": ["~/.bashrc"],
    },

    # --- Declarative: shell -c, excluding container exec contexts ---
    "inline_interpreter": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [r"\b(bash|sh|zsh)\s+-c\b", r"\bsu\s+(?:\S+\s+)?-c\b"],
        "exclude": [r"\b(docker\s+exec\b|(?:pnpm\s+(?:exec\s+)?)?wp-env\s+run\b)"],
        "message": "Shell subshell execution (`bash -c`, `sh -c`, `su -c`) can bypass command-level safety checks. Write the code to a file and run it instead (e.g., `python3 script.py`), or use a non-shell interpreter directly (`python3 -c`, `node -e` are allowed). Confirm this is intentional.",
        "examples": ["bash -c 'echo hello world'", "su -c 'rm -rf /tmp/old'"],
    },

    # --- Simple: single regex ---
    "inline_heredoc": {
        "tier": "ask",
        "tools": {"Bash"},
        "patterns": [
            r"(?<!\w)(bash|sh|zsh|python3?|node|ruby|perl|mysql|psql|sqlite3)\b[^<\n]*<<",
        ],
        "message": "Heredocs fed to interpreters (`bash <<`, `python3 <<`) execute code that bypasses command-level safety checks. Write the code to a file and run it instead (e.g., `bash script.sh`). Writer heredocs (`cat > file << 'EOF'`) are not affected. Confirm this is intentional.",
        "examples": ["bash << 'EOF'\necho hello\nEOF"],
    },
}


# ---------------------------------------------------------------------------
# Registry Builder
# ---------------------------------------------------------------------------

def _compile_patterns(pattern_list):
    """Compile a list of regex strings into re.Pattern objects."""
    return [re.compile(p) for p in pattern_list]


def _make_detector(rule_id, compiled):
    """Generate a detection function from compiled pattern data."""
    patterns = compiled.get("patterns", [])
    pattern_groups = compiled.get("pattern_groups", [])
    require = compiled.get("require", [])
    exclude = compiled.get("exclude", [])
    message = compiled["message"]

    def detect(command, tool_name, tool_input, config):
        # Check exclude patterns first
        for pat in exclude:
            if pat.search(command):
                return False, None
        # Check OR patterns
        matched = False
        for pat in patterns:
            if pat.search(command):
                matched = True
                break
        # Check AND pattern groups (any group where all match)
        if not matched:
            for group in pattern_groups:
                if all(pat.search(command) for pat in group):
                    matched = True
                    break
        if not matched:
            return False, None
        # Check require patterns (all must match)
        for pat in require:
            if not pat.search(command):
                return False, None
        return True, message
    return detect


def build_registry():
    """Compile RULES into RULES_BY_TOOL index.

    For rules with patterns (no custom detect), generates a detection function.
    For rules with detect, uses the custom function directly.
    Stores the resolved detect function as _detect on each rule dict.
    Returns the RULES_BY_TOOL index.
    """
    rules_by_tool = {}
    for rule_id, rule in RULES.items():
        if "detect" in rule:
            # Custom detection function — use as-is
            detect_fn = rule["detect"]
        else:
            # Compile patterns and generate detector
            compiled = {"message": rule["message"]}
            if "patterns" in rule:
                compiled["patterns"] = _compile_patterns(rule["patterns"])
            if "pattern_groups" in rule:
                compiled["pattern_groups"] = [
                    _compile_patterns(group) for group in rule["pattern_groups"]
                ]
            if "require" in rule:
                compiled["require"] = _compile_patterns(rule["require"])
            if "exclude" in rule:
                compiled["exclude"] = _compile_patterns(rule["exclude"])
            detect_fn = _make_detector(rule_id, compiled)
        # Store resolved detect function on rule dict for test access
        rule["_detect"] = detect_fn
        # Build per-tool index
        tier = rule["tier"]
        for tool in rule["tools"]:
            rules_by_tool.setdefault(tool, []).append((rule_id, tier, detect_fn))
    return rules_by_tool


RULES_BY_TOOL = build_registry()
_mark("registry_built")


def is_allowlisted(command, disabled=None):
    """Check if command matches any allowlist pattern.

    Respects disabled rules — if a rule is disabled, its allowlist entries
    are also skipped (consistent with main() behavior).
    """
    if disabled is None:
        disabled = set()
    for rule_id, pattern in ALLOWLIST_PATTERNS:
        if rule_id not in disabled and pattern.search(command):
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
        return  # defensive: allow() calls sys.exit, but guard against unexpected continuation
    _mark("stdin_parsed")

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        allow()
        return

    config = load_config()
    disabled = set(config.get("disable_rules", []))
    _mark("config_loaded")

    # Extract command for Bash tool
    command = ""
    bash_segments = []
    if tool_name == "Bash":
        raw_command = tool_input.get("command", "")
        # Strip writer heredoc bodies (cat >, tee) BEFORE normalization so
        # the regex can match on line structure (\n delimiters). Interpreter
        # heredocs (bash <<, python3 <<) are NOT stripped.
        raw_command = strip_writer_heredocs(raw_command)
        command = normalize_command(raw_command)
        bash_segments = _split_bash_segments(command)

    # Self-protection: prevent modification of hook config or plugin files.
    # NOT configurable — cannot be disabled via disable_rules.
    # Covers Write/Edit (file_path) and Bash (redirect/copy to protected paths).
    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if file_path:
            if _is_self_protected_path(file_path):
                block(_SELF_PROTECTION_MESSAGE)
    elif tool_name == "Bash" and command:
        if _bash_targets_protected_path(command, tool_input):
            block(_SELF_PROTECTION_MESSAGE)

    # 1. Allowlist + Rules — single pass.
    #    Simple commands: check allowlist, then evaluate against rules.
    #    Compound commands: shell-aware split into segments, check each
    #    segment against allowlist then rules.
    is_compound = tool_name == "Bash" and len(bash_segments) > 1

    if command and not is_compound:
        # Simple command: allowlist check
        for rule_id, pattern in ALLOWLIST_PATTERNS:
            if rule_id not in disabled and pattern.search(command):
                _mark("allowlisted")
                allow()
    _mark("rules_start")

    first_ask = None

    if is_compound:
        # Compound command: per-segment evaluation
        for seg in bash_segments:
            # Per-segment allowlist
            seg_allowed = False
            for rule_id, pattern in ALLOWLIST_PATTERNS:
                if rule_id not in disabled and pattern.search(seg):
                    seg_allowed = True
                    break
            if seg_allowed:
                continue
            # Per-segment rules
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
    else:
        # Simple command (or non-Bash): evaluate against all rules
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

    _mark("rules_done")

    if first_ask:
        ask(first_ask)

    # 2. Allow — everything else
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Fail open — don't block the agent on hook bugs
        sys.exit(0)

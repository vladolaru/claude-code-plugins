"""Shell parsing and normalization helpers for the yoloing-safe hook."""

from __future__ import annotations

import os
import re
import shlex


_SHELL_SEPARATORS = {"&&", "||", ";", "|", "&", "\n"}
_WRAPPER_COMMANDS = {
    "command", "env", "sudo", "nice", "nohup", "time", "exec", "strace", "ionice", "taskset"
}
_REDIRECT_TOKENS = {">", ">>", ">|", "1>", "1>>", "1>|", "2>", "2>>", "2>|"}
_RE_INLINE_REDIRECT = re.compile(r"^(?:[12]?>{1,2}\|?|>{1,2}\|?)(.+)$")
_INPUT_REDIRECT_TOKENS = {"<", "0<"}
_RE_INLINE_INPUT_REDIRECT = re.compile(r"^(?:0?<)(?!<)(.+)$")
_RE_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")

RE_CHAIN_OPS = re.compile(r"(&&|\|\||[|;&]|\n)")
_RE_WHITESPACE = re.compile(r"[^\S\n]+")
_WRAPPER_RE = re.compile(
    r"^(command|env|sudo|nice|nohup|time|exec|strace|ionice|taskset)\s+"
)

_GIT_GLOBAL_OPTS_WITH_ARG = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--super-prefix",
})
_GIT_GLOBAL_OPTS_BOOL = frozenset({
    "--bare", "--no-pager", "--paginate", "-p",
    "--no-replace-objects", "-P", "--no-optional-locks",
    "--literal-pathspecs", "--glob-pathspecs",
    "--noglob-pathspecs", "--icase-pathspecs",
})
_NPM_GLOBAL_OPTS_WITH_ARG = frozenset({
    "--registry", "--prefix", "--userconfig", "--globalconfig",
    "--cache", "--loglevel", "--otp", "--workspace", "-w",
})
_NPM_GLOBAL_OPTS_BOOL = frozenset({
    "--global", "-g", "--json", "--long", "-l",
    "--parseable", "--silent", "--quiet", "--verbose",
})

_RE_WRITER_HEREDOC = re.compile(
    r"((?:cat\s+>>?\s*\S+|tee\s+(?:-a\s+)?\S+)\s*<<\s*['\"]?(\w+)['\"]?\n)"
    r".*?"
    r"(\n\2\b)",
    re.DOTALL,
)

# Matches $(cat <<'MARKER'\n...body...\nMARKER\n) — command substitution heredocs
# where cat produces text output (not executable). Used by Claude Code for commit messages.
_RE_SUBSHELL_CAT_HEREDOC = re.compile(
    r"(\$\(cat\s+<<\s*['\"]?(\w+)['\"]?\n)"
    r".*?"
    r"(\n\2\s*\))",
    re.DOTALL,
)


def _merge_clobber_redirect_tokens(tokens):
    """Merge `>|` forms that shlex splits into redirect + pipe tokens."""
    merged = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {">", "1>", "2>"} and index + 1 < len(tokens) and tokens[index + 1] == "|":
            merged.append(token + "|")
            index += 2
            continue
        merged.append(token)
        index += 1
    return merged


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
    for token in tokens:
        if token in _SHELL_SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _tokenized_segments(command):
    """Return shell-tokenized command segments."""
    try:
        return _split_shell_segments(_tokenize_shell(command))
    except ValueError:
        return []


def _segment_command_and_args(segment):
    """Extract command name + args, skipping env assignments and wrappers."""
    index = 0
    while index < len(segment) and _RE_ASSIGNMENT.match(segment[index]):
        index += 1
    while index < len(segment) and os.path.basename(segment[index]) in _WRAPPER_COMMANDS:
        index += 1
    if index >= len(segment):
        return "", []
    return os.path.basename(segment[index]), segment[index + 1:]


def _command_and_args_from_text(command):
    """Extract command name + args from command text."""
    segments = _tokenized_segments(command)
    if not segments:
        return "", []
    return _segment_command_and_args(segments[0])


def _collect_redirection_targets(segment):
    """Collect shell redirection targets from one tokenized segment."""
    targets = []
    for index, token in enumerate(segment):
        if token in _REDIRECT_TOKENS and index + 1 < len(segment):
            target = segment[index + 1]
            if target and not target.startswith("&"):
                targets.append(target)
            continue
        inline = _RE_INLINE_REDIRECT.match(token)
        if inline:
            target = inline.group(1)
            if target and not target.startswith("&"):
                targets.append(target)
    return targets


def _collect_input_redirection_sources(segment):
    """Collect shell input-redirection sources from one tokenized segment."""
    sources = []
    for index, token in enumerate(segment):
        if token in _INPUT_REDIRECT_TOKENS and index + 1 < len(segment):
            source = segment[index + 1]
            if source and not source.startswith("&"):
                sources.append(source)
            continue
        inline = _RE_INLINE_INPUT_REDIRECT.match(token)
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


def _strip_git_global_opts(command):
    """Strip git global options to expose the subcommand."""
    if not command.startswith("git "):
        return command
    parts = command.split()
    result = ["git"]
    index = 1
    while index < len(parts):
        token = parts[index]
        if not token.startswith("-"):
            result.extend(parts[index:])
            break
        if "=" in token:
            key = token.split("=", 1)[0]
            if key in _GIT_GLOBAL_OPTS_WITH_ARG:
                index += 1
                continue
        if token in _GIT_GLOBAL_OPTS_WITH_ARG:
            index += 2
            continue
        if token in _GIT_GLOBAL_OPTS_BOOL:
            index += 1
            continue
        result.extend(parts[index:])
        break
    return " ".join(result)


def _strip_npm_global_opts(command):
    """Strip npm global options to expose the subcommand."""
    if not command.startswith("npm "):
        return command
    parts = command.split()
    result = ["npm"]
    index = 1
    while index < len(parts):
        token = parts[index]
        if not token.startswith("-"):
            result.extend(parts[index:])
            break
        if "=" in token:
            key = token.split("=", 1)[0]
            if key in _NPM_GLOBAL_OPTS_WITH_ARG:
                index += 1
                continue
        if token in _NPM_GLOBAL_OPTS_WITH_ARG:
            index += 2
            continue
        if token in _NPM_GLOBAL_OPTS_BOOL:
            index += 1
            continue
        result.extend(parts[index:])
        break
    return " ".join(result)


def normalize_command(cmd):
    """Strip path prefixes, command wrappers, and collapse whitespace."""
    if not cmd:
        return ""
    stripped = cmd.lstrip()
    parts = stripped.split(None, 1)
    normalized = stripped
    if parts:
        binary = parts[0]
        if binary.startswith("/") and "/" in binary[1:]:
            remainder = parts[1] if len(parts) > 1 else ""
            normalized = f"{os.path.basename(binary)} {remainder}".strip()
    prev = None
    while prev != normalized:
        prev = normalized
        normalized = _WRAPPER_RE.sub("", normalized)
    normalized = _RE_WHITESPACE.sub(" ", normalized).strip()
    if not RE_CHAIN_OPS.search(normalized):
        normalized = _strip_git_global_opts(normalized)
        normalized = _strip_npm_global_opts(normalized)
    return normalized


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
        normalized = normalize_command(" ".join(segment).strip())
        if normalized:
            segments.append(normalized)
    return segments


def strip_writer_heredocs(command: str) -> str:
    """Strip heredoc bodies when the consumer is `cat >`, `tee`, or `$(cat ...)`."""
    if "<<" not in command:
        return command
    result = _RE_WRITER_HEREDOC.sub(r"\1\3", command)
    result = _RE_SUBSHELL_CAT_HEREDOC.sub(r"\1\3", result)
    return result


def _whole_bash_command(command, tool_input):
    """Return the normalized full Bash command when available."""
    if isinstance(tool_input, dict):
        raw_command = tool_input.get("command")
        if isinstance(raw_command, str) and raw_command:
            return normalize_command(strip_writer_heredocs(raw_command))
    return command or ""

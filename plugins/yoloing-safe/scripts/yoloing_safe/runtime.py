"""Runtime entrypoint helpers for the yoloing-safe hook."""

from __future__ import annotations

import json
import sys

from .config import (
    NON_DISABLEABLE_RULES,
    SELF_PROTECTION_MESSAGE,
    is_self_protected_path,
    load_config,
)
from .context import EvalContext
from .paths import _bash_targets_protected_path
from .registry import is_allowlisted
from .rules import ALLOWLIST_PATTERNS, RULES_BY_TOOL
from .shell import _split_bash_segments, normalize_command, strip_writer_heredocs


def block(message, mark=None):
    """Block the tool call: exit 2 with a message on stderr."""
    if mark is not None:
        mark("exit")
    print(message, file=sys.stderr)
    sys.exit(2)


def ask(message, mark=None):
    """Ask for confirmation: exit 0 with JSON on stdout."""
    if mark is not None:
        mark("exit")
    output = {
        "hookSpecificOutput": {
            "permissionDecision": "ask",
            "systemMessage": message,
        }
    }
    print(json.dumps(output))
    sys.exit(0)


def allow(mark=None):
    """Allow the tool call: exit 0 silently."""
    if mark is not None:
        mark("exit")
    sys.exit(0)


def main(mark=None):
    """Run the hook against a single Claude Code PreToolUse payload."""
    if mark is None:
        mark = lambda label: None

    mark("stdin_start")
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        allow(mark)
        return
    mark("stdin_parsed")

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        allow(mark)
        return

    config = load_config()
    disabled = set(config.get("disable_rules", []))
    disabled -= NON_DISABLEABLE_RULES
    mark("config_loaded")

    command = ""
    bash_segments = []
    if tool_name == "Bash":
        raw_command = tool_input.get("command", "")
        raw_command = strip_writer_heredocs(raw_command)
        command = normalize_command(raw_command)
        bash_segments = _split_bash_segments(command)

    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if file_path and is_self_protected_path(file_path):
            block(SELF_PROTECTION_MESSAGE, mark)
    elif tool_name == "Bash" and command:
        if _bash_targets_protected_path(command, tool_input):
            block(SELF_PROTECTION_MESSAGE, mark)

    is_compound = tool_name == "Bash" and len(bash_segments) > 1

    if command and not is_compound:
        if is_allowlisted(command, ALLOWLIST_PATTERNS, disabled):
            mark("allowlisted")
            allow(mark)
    mark("rules_start")

    ctx = EvalContext(tool_name, tool_input, config, command)
    first_ask = None
    if is_compound:
        for segment in bash_segments:
            if is_allowlisted(segment, ALLOWLIST_PATTERNS, disabled):
                continue
            seg_ctx = ctx.for_segment(segment)
            for rule_id, tier, detect_fn in RULES_BY_TOOL.get(tool_name, []):
                if rule_id in disabled:
                    continue
                detected, message = detect_fn(seg_ctx)
                if not detected:
                    continue
                if tier == "block":
                    mark("rules_done")
                    block(message, mark)
                if tier == "ask" and first_ask is None:
                    first_ask = message
                break
    else:
        for rule_id, tier, detect_fn in RULES_BY_TOOL.get(tool_name, []):
            if rule_id in disabled:
                continue
            detected, message = detect_fn(ctx)
            if not detected:
                continue
            if tier == "block":
                mark("rules_done")
                block(message, mark)
            if tier == "ask" and first_ask is None:
                first_ask = message

    mark("rules_done")
    if first_ask:
        ask(first_ask, mark)
    allow(mark)

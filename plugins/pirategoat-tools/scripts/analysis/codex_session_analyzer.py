#!/usr/bin/env python3
"""
Trace one Codex thread tree in depth.

Codex writes each thread — including every subagent — as its own rollout
file, so "what did this run actually do" means walking a tree of sibling
files. This resolves that tree and reports each thread's commands, file
changes, timing, and token use.

Only finished sessions are analyzed. A rollout still being written grows
while it is read, so its numbers cannot be trusted; those files are skipped
unless --include-active is passed.

Usage:
    # Newest thread tree for one project
    python3 codex_session_analyzer.py --cwd /path/to/project

    # A specific thread, as JSON
    python3 codex_session_analyzer.py --thread-id 01a0159b-... --format json

    # All code-reviewer threads from the last 30 days
    python3 codex_session_analyzer.py --agent code-reviewer --since 30
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_ROLLOUT_PATH = Path(__file__).resolve().parent / "codex_rollout.py"
_spec = importlib.util.spec_from_file_location("codex_rollout", str(_ROLLOUT_PATH))
codex_rollout = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass resolves its module via sys.modules during
# class creation and fails with AttributeError if the entry is missing.
sys.modules["codex_rollout"] = codex_rollout
_spec.loader.exec_module(codex_rollout)


def _thread_report(meta, scan) -> dict:
    return {
        "thread_id": meta.thread_id,
        "agent_role": meta.agent_role,
        "agent_path": meta.agent_path,
        "depth": meta.depth,
        "cwd": meta.cwd,
        "model": scan.model,
        "duration_seconds": round(scan.duration_seconds, 1),
        "total_tokens": scan.total_tokens,
        "cached_input_tokens": scan.cached_input_tokens,
        "commands": scan.commands,
        "failed_commands": scan.failed_commands,
        "files_changed": scan.files_changed,
        "messages": scan.messages,
        "compactions": scan.compactions,
        "malformed_lines": scan.malformed_lines,
        "rollout": str(meta.path),
    }


def _failed_commands(scan) -> list[dict]:
    return [
        {
            "command": item.get("command", ""),
            "exit_code": item.get("exit_code"),
            "duration": item.get("duration"),
        }
        for item in scan.items
        if item.get("type") == codex_rollout.ITEM_COMMAND and item.get("exit_code")
    ]


COMMAND_PREVIEW_CHARS = 120


def _command_preview(command) -> str:
    """One readable line for a command in text output.

    `command` is the raw argv list, and a Codex shell call routinely carries a
    whole multi-line script as its last element. Printed verbatim, a handful of
    failures buries the report. JSON output keeps the full value.
    """
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    collapsed = " ".join(str(command).split())
    if len(collapsed) <= COMMAND_PREVIEW_CHARS:
        return collapsed
    return collapsed[: COMMAND_PREVIEW_CHARS - 1] + "…"


def _render_text(report: dict) -> str:
    lines = []
    thread = report["thread"]
    lines.append(f"Thread {thread['thread_id']}  [{thread['agent_role']}]")
    lines.append(f"  cwd:       {thread['cwd']}")
    lines.append(f"  model:     {thread['model'] or 'unknown'}")
    lines.append(f"  duration:  {thread['duration_seconds']}s")
    lines.append(f"  tokens:    {thread['total_tokens']}")
    lines.append(
        f"  commands:  {thread['commands']} ({thread['failed_commands']} failed), "
        f"files changed: {thread['files_changed']}"
    )
    if report["failures"]:
        lines.append("  failed commands:")
        for failure in report["failures"]:
            lines.append(f"    exit {failure['exit_code']}: {_command_preview(failure['command'])}")

    for child in report["children"]:
        lines.append("")
        lines.append(f"  └─ {child['thread_id']}  [{child['agent_role']}]  {child['agent_path']}")
        lines.append(
            f"       {child['duration_seconds']}s, {child['total_tokens']} tokens, "
            f"{child['commands']} commands ({child['failed_commands']} failed), "
            f"{child['files_changed']} files"
        )

    if report["orphans"]:
        lines.append("")
        lines.append(f"  {len(report['orphans'])} thread(s) had a parent outside the window.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trace one Codex thread tree in depth.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--sessions-dir",
        default=str(codex_rollout.DEFAULT_SESSIONS_DIR),
        help="Codex sessions root (default: ~/.codex/sessions)",
    )
    parser.add_argument("--thread-id", default=None, help="Analyze this thread instead of the newest match")
    parser.add_argument("--cwd", default=None, help="Only threads whose working directory matches exactly")
    parser.add_argument(
        "--since",
        type=int,
        default=codex_rollout.DEFAULT_SINCE_DAYS,
        help=f"Days back to scan (default: {codex_rollout.DEFAULT_SINCE_DAYS})",
    )
    parser.add_argument("--agent", default=None, help="Comma-separated agent roles to include")
    parser.add_argument(
        "--limit",
        type=int,
        default=codex_rollout.DEFAULT_LIMIT,
        help=f"Maximum threads to consider (default: {codex_rollout.DEFAULT_LIMIT})",
    )
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format (default: text)")
    parser.add_argument("--output", default=None, help="Write output to a file instead of stdout")
    parser.add_argument(
        "--include-active",
        action="store_true",
        help="Include rollouts touched in the last 5 minutes (numbers may be inconsistent)",
    )

    args = parser.parse_args()

    sessions_dir = Path(args.sessions_dir).expanduser()
    if not sessions_dir.is_dir():
        print(f"Error: sessions directory not found: {sessions_dir}", file=sys.stderr)
        sys.exit(1)

    # The tree needs every thread in the window, not just the filtered ones,
    # so children are still reachable when --agent narrows the selection.
    everything = codex_rollout.discover_threads(
        sessions_dir,
        since_days=args.since,
        limit=None,
        include_active=args.include_active,
    )
    candidates = codex_rollout.discover_threads(
        sessions_dir,
        since_days=args.since,
        cwd=args.cwd,
        agent=args.agent,
        limit=args.limit,
        include_active=args.include_active,
    )

    if args.thread_id:
        selected = next((m for m in everything if m.thread_id == args.thread_id), None)
    else:
        # Prefer the newest root. Picking the newest thread outright often lands
        # on a subagent, and a leaf has no tree to show. When a filter such as
        # --agent excludes every root, fall back to the newest match.
        roots = [m for m in candidates if m.agent_path == codex_rollout.ROOT_AGENT_PATH]
        selected = (roots or candidates or [None])[0]

    if selected is None:
        print("Error: no threads matched the given filters", file=sys.stderr)
        sys.exit(1)

    tree = codex_rollout.build_tree(everything)
    root_scan = codex_rollout.scan_thread(selected.path, keep_items=True)

    children = []
    for child in tree.children_of(selected):
        children.append(_thread_report(child, codex_rollout.scan_thread(child.path)))

    report = {
        "thread": _thread_report(selected, root_scan),
        "children": children,
        "failures": _failed_commands(root_scan),
        "orphans": [m.thread_id for m in tree.orphans],
    }

    text = json.dumps(report, indent=2) if args.format == "json" else _render_text(report)

    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()

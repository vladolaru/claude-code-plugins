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
MAX_FAILURES_SHOWN = 10
DEFAULT_CHILDREN_SHOWN = 20


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
    lines.append(f"Session {report['session_id']}")
    if thread["agent_path"] == "/root":
        lines.append(f"  main thread: {thread['thread_id']}  [{thread['agent_role']}]")
    else:
        lines.append(f"  subagent:    {thread['thread_id']}  [{thread['agent_role']}]  {thread['agent_path']}")
        lines.append(f"  (analyze the whole session with --thread-id {report['session_id']})")
    lines.append(f"  cwd:       {thread['cwd']}")
    lines.append(f"  model:     {thread['model'] or 'unknown'}")
    lines.append(f"  duration:  {thread['duration_seconds']}s")
    lines.append(f"  tokens:    {thread['total_tokens']}")
    lines.append(
        f"  commands:  {thread['commands']} ({thread['failed_commands']} failed), "
        f"files changed: {thread['files_changed']}"
    )
    if report["failures"]:
        shown = report["failures"][:MAX_FAILURES_SHOWN]
        lines.append(f"  failed commands ({len(report['failures'])}):")
        for failure in shown:
            lines.append(f"    exit {failure['exit_code']}: {_command_preview(failure['command'])}")
        remaining = len(report["failures"]) - len(shown)
        if remaining:
            lines.append(f"    … and {remaining} more (use --format json for all of them)")

    if report["children_omitted"]:
        lines.append("")
        lines.append(
            f"  subagents: {report['children_total']} "
            f"(showing the {len(report['children'])} largest; --children 0 for all)"
        )
    elif report["children"]:
        lines.append("")
        lines.append(f"  subagents: {report['children_total']}")

    for child in report["children"]:
        lines.append("")
        lines.append(f"  └─ {child['thread_id']}  [{child['agent_role']}]  {child['agent_path']}")
        lines.append(
            f"       {child['duration_seconds']}s, {child['total_tokens']} tokens, "
            f"{child['commands']} commands ({child['failed_commands']} failed), "
            f"{child['files_changed']} files"
        )

    if report["resumes"]:
        lines.append("")
        lines.append(f"  resumed {len(report['resumes'])} time(s) — same session, continued later:")
        for resume in report["resumes"]:
            lines.append(
                f"    {resume['thread_id']}  {resume['duration_seconds']}s, "
                f"{resume['commands']} commands  (tokens not summed: a resume replays context)"
            )

    for note in report["notes"]:
        lines.append("")
        lines.append(f"  Note: {note}")
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
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Analyze the session containing this thread id (the session's own id, "
        "its root, or any subagent). Searches the whole archive; --since does not apply.",
    )
    parser.add_argument("--cwd", default=None, help="Only threads whose working directory matches exactly")
    parser.add_argument(
        "--since",
        type=int,
        default=None,
        help="Days back to scan. Required unless --thread-id is given, and never "
        "applied by default: a too-narrow window looks exactly like having done no work.",
    )
    parser.add_argument("--agent", default=None, help="Comma-separated agent roles to include")
    parser.add_argument(
        "--limit",
        type=int,
        default=codex_rollout.DEFAULT_LIMIT,
        help=f"Maximum threads to consider (default: {codex_rollout.DEFAULT_LIMIT})",
    )
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format (default: text)")
    parser.add_argument(
        "--children",
        type=int,
        default=DEFAULT_CHILDREN_SHOWN,
        help=f"How many subagents to scan and report, largest first "
        f"(default: {DEFAULT_CHILDREN_SHOWN}; 0 means all, which can be slow on big sessions)",
    )
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

    stats = codex_rollout.DiscoveryStats()

    if args.thread_id:
        # Naming a thread already says which session you want, so no window is
        # applied: bounding the answer by a date could only hide part of it.
        # The id may be the session's own, its root, or any subagent within it.
        if args.since is not None:
            print(
                f"Note: --since is ignored when --thread-id names a session.",
                file=sys.stderr,
            )
        everything = codex_rollout.discover_session(
            sessions_dir,
            args.thread_id,
            include_active=args.include_active,
            stats=stats,
        )
        if not everything:
            print(
                f"Error: no session found containing thread {args.thread_id}",
                file=sys.stderr,
            )
            sys.exit(1)
        candidates = everything
    else:
        if args.since is None:
            print(
                "Error: specify a scope — either --thread-id <id> to analyze one "
                "session, or --since <days> to search recent sessions.\n"
                "A window is never applied silently, because a too-narrow one "
                "looks identical to having done no work.",
                file=sys.stderr,
            )
            sys.exit(2)
        # The tree needs every thread in the window, not just the filtered ones,
        # so children stay reachable when --agent narrows the selection.
        everything = codex_rollout.discover_threads(
            sessions_dir,
            since_days=args.since,
            limit=None,
            include_active=args.include_active,
            stats=stats,
        )
        candidates = codex_rollout.discover_threads(
            sessions_dir,
            since_days=args.since,
            cwd=args.cwd,
            agent=args.agent,
            limit=args.limit,
            include_active=args.include_active,
        )

    tree = codex_rollout.build_tree(everything)
    tree_roots = tree.roots

    if args.thread_id:
        # Report the thread that was named, not its session root: asking for a
        # specific thread and getting a different one is surprising. The whole
        # session is still loaded, so naming the session id or its root yields
        # the full tree, and the report always carries session_id so a caller
        # holding only a subagent id can pivot to the session.
        selected = next((m for m in everything if m.thread_id == args.thread_id), None)
    else:
        # Rank sessions by most recent activity, not by when the root rollout
        # was written. A root is written at session start, so ordering by it
        # picks a session opened moments ago over one still running with
        # hundreds of subagents.
        last_activity: dict[str, float] = {}
        for meta in everything:
            last_activity[meta.session_id] = max(
                last_activity.get(meta.session_id, 0.0), meta.mtime
            )
        session_roots = {m.thread_id for m in tree_roots}
        roots = [m for m in candidates if m.thread_id in session_roots]
        roots.sort(key=lambda m: last_activity.get(m.session_id, m.mtime), reverse=True)
        selected = (roots or candidates or [None])[0]

    if selected is None:
        print("Error: no threads matched the given filters", file=sys.stderr)
        sys.exit(1)

    root_scan = codex_rollout.scan_thread(selected.path, keep_items=True)

    # Reporting a child means reading its whole rollout. One real session has
    # 621 subagents totalling 11 GB, which is 85 seconds of I/O for a list too
    # long to read anyway — so deep-scan the largest few and count the rest.
    # Size is the best cheap proxy for "this subagent did substantial work".
    all_children = sorted(
        tree.children_of(selected), key=lambda m: m.path.stat().st_size, reverse=True
    )
    shown = all_children if args.children == 0 else all_children[: args.children]
    children = [
        _thread_report(child, codex_rollout.scan_thread(child.path)) for child in shown
    ]
    children_omitted = len(all_children) - len(shown)

    # Resumes are reported, never folded into the session's totals: a resume
    # replays prior context, so its tokens are largely re-sent rather than new.
    resumes = [
        _thread_report(rollout, codex_rollout.scan_thread(rollout.path))
        for rollout in tree.resumes_of(selected)
    ]

    report = {
        "session_id": selected.session_id,
        "thread": _thread_report(selected, root_scan),
        "children": children,
        "children_total": len(all_children),
        "children_omitted": children_omitted,
        "resumes": resumes,
        "failures": _failed_commands(root_scan),
        "notes": stats.notes(),
    }

    text = json.dumps(report, indent=2) if args.format == "json" else _render_text(report)

    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()

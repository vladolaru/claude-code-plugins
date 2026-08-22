#!/usr/bin/env python3
"""
Compare operational metrics across Codex threads.

One row per thread — role, model, duration, tokens, commands, failures,
files changed — plus a roll-up by agent role, so questions like "how do my
code-reviewer runs compare" are a single invocation.

Metric names and output shapes match session_metrics.py, the Claude Code
equivalent, so figures from both tools can be read side by side.

Only finished sessions are counted; rollouts touched in the last five
minutes are skipped unless --include-active is passed.

Usage:
    # Last week, every thread in one project
    python3 codex_session_metrics.py --cwd /path/to/project

    # Reviewer roles over the last month, as JSON
    python3 codex_session_metrics.py --agent code-reviewer --since 30 --format json
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

# Built rather than written literally: a triple backtick inside this file
# would terminate the surrounding code fence wherever it is documented.
JSON_FENCE = "`" * 3 + "json"
FENCE_END = "`" * 3

COLUMNS = [
    ("thread", "thread_id"),
    ("role", "agent_role"),
    ("model", "model"),
    ("duration_s", "duration_seconds"),
    ("tokens", "total_tokens"),
    ("cached_pct", "cached_pct"),
    ("commands", "commands"),
    ("failed", "failed_commands"),
    ("files", "files_changed"),
    ("compactions", "compactions"),
]


def _row(meta, scan) -> dict:
    cached_pct = 0.0
    if scan.total_tokens:
        cached_pct = round(100.0 * scan.cached_input_tokens / scan.total_tokens, 1)
    return {
        "thread_id": meta.thread_id,
        "agent_role": meta.agent_role,
        "cwd": meta.cwd,
        "model": scan.model or "unknown",
        "duration_seconds": round(scan.duration_seconds, 1),
        "total_tokens": scan.total_tokens,
        "cached_pct": cached_pct,
        "commands": scan.commands,
        "failed_commands": scan.failed_commands,
        "files_changed": scan.files_changed,
        "compactions": scan.compactions,
    }


def _roll_up(rows: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows:
        entry = grouped.setdefault(
            row["agent_role"],
            {
                "agent_role": row["agent_role"],
                "threads": 0,
                "total_tokens": 0,
                "duration_seconds": 0.0,
                "commands": 0,
                "failed_commands": 0,
                "files_changed": 0,
            },
        )
        entry["threads"] += 1
        entry["total_tokens"] += row["total_tokens"]
        entry["duration_seconds"] = round(entry["duration_seconds"] + row["duration_seconds"], 1)
        entry["commands"] += row["commands"]
        entry["failed_commands"] += row["failed_commands"]
        entry["files_changed"] += row["files_changed"]
    return sorted(grouped.values(), key=lambda e: e["total_tokens"], reverse=True)


def _markdown(rows: list[dict], by_role: list[dict], notes: list[str] | None = None) -> str:
    if not rows:
        body = "No threads matched the given filters."
        for note in notes or []:
            body += f"\n\nNote: {note}"
        return body + "\n"

    lines = ["| " + " | ".join(name for name, _ in COLUMNS) + " |"]
    lines.append("|" + "|".join("---" for _ in COLUMNS) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(row[key]) for _, key in COLUMNS) + " |")

    lines.append("")
    lines.append("| role | threads | tokens | duration_s | commands | failed | files |")
    lines.append("|---|---|---|---|---|---|---|")
    for entry in by_role:
        lines.append(
            f"| {entry['agent_role']} | {entry['threads']} | {entry['total_tokens']} | "
            f"{entry['duration_seconds']} | {entry['commands']} | {entry['failed_commands']} | "
            f"{entry['files_changed']} |"
        )
    for note in notes or []:
        lines.append("")
        lines.append(f"> Note: {note}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare operational metrics across Codex threads.",
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
        help="Report the session containing this thread id (session, root, or subagent). "
        "Searches the whole archive: --since, --cwd, and --agent do not apply.",
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
        help=f"Maximum threads to report (default: {codex_rollout.DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--format", choices=["markdown", "json", "both"], default="both", help="Output format (default: both)"
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
        # A named thread identifies a session outright, so no window applies.
        if args.since is not None:
            print("Note: --since is ignored when --thread-id names a session.", file=sys.stderr)
        metas = codex_rollout.discover_session(
            sessions_dir,
            args.thread_id,
            include_active=args.include_active,
            stats=stats,
        )
        if not metas:
            print(
                f"Error: no session found containing thread {args.thread_id}",
                file=sys.stderr,
            )
            sys.exit(1)
        if args.limit is not None:
            metas = metas[: args.limit]
    else:
        if args.since is None:
            print(
                "Error: specify a scope — either --thread-id <id> to report one "
                "session, or --since <days> to search recent sessions.\n"
                "A window is never applied silently, because a too-narrow one "
                "looks identical to having done no work.",
                file=sys.stderr,
            )
            sys.exit(2)
        metas = codex_rollout.discover_threads(
            sessions_dir,
            since_days=args.since,
            cwd=args.cwd,
            agent=args.agent,
            limit=args.limit,
            include_active=args.include_active,
            stats=stats,
        )

    rows = [_row(meta, codex_rollout.scan_thread(meta.path)) for meta in metas]
    by_role = _roll_up(rows)
    report = {"threads": rows, "by_role": by_role, "notes": stats.notes()}

    if args.format == "json":
        text = json.dumps(report, indent=2)
    elif args.format == "markdown":
        text = _markdown(rows, by_role, stats.notes())
    else:
        text = (
            _markdown(rows, by_role, stats.notes())
            + f"\n\n{JSON_FENCE}\n"
            + json.dumps(report, indent=2)
            + f"\n{FENCE_END}"
        )

    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()

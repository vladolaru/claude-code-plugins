#!/usr/bin/env python3
"""
Check Reviewer Agent Status — deterministic status check.

Reads dispatch-plan.json + scans for {agent}.started and {agent}-review.json.
Four states per dispatched agent:
  FINISHED       — review file exists
  RUNNING        — .started marker exists, within timeout
  TIMED_OUT      — .started marker exists, exceeded timeout
  NOT_DISPATCHED — neither marker nor review file (LLM forgot to dispatch)

Exit codes:
    0  ALL_DONE: true (nothing left to wait for — all finished or timed out)
    2  ALL_DONE: false (some agents still running or not dispatched)
    1  Error (no dispatch plan, bad JSON)
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone


DEFAULT_TIMEOUT = 1200  # 20 minutes


def _reviewer_filename(agent_name: str) -> str:
    """Derive the review filename from the agent name.

    Matches bootstrap-reviewer.py's derive_reviewer_name():
    'security-reviewer' -> 'security-review.json'
    'pr-reviewer' -> 'pr-review.json'
    """
    if agent_name.endswith("-reviewer"):
        base = agent_name[: -len("-reviewer")]
    else:
        base = agent_name
    return f"{base}-review.json"


def check_status(output_dir: str, timeout_seconds: int = None) -> dict:
    """Check status of all agents in the dispatch plan."""
    plan_path = os.path.join(output_dir, "dispatch-plan.json")
    if not os.path.isfile(plan_path):
        raise FileNotFoundError(f"No dispatch plan at {plan_path}")

    # Read timeout from review-context.json, fall back to default
    if timeout_seconds is None:
        ctx_path = os.path.join(output_dir, "review-context.json")
        if os.path.isfile(ctx_path):
            with open(ctx_path) as f:
                ctx = json.load(f)
            timeout_seconds = ctx.get("review", {}).get("agent_timeout_seconds", DEFAULT_TIMEOUT)
        else:
            timeout_seconds = DEFAULT_TIMEOUT

    with open(plan_path) as f:
        plan = json.load(f)

    now = datetime.now(timezone.utc)
    agents = []
    dispatched = 0
    finished = 0
    running = 0
    timed_out = 0
    not_dispatched = 0
    skipped = 0

    for agent in plan.get("agents", []):
        name = agent["name"]
        status = agent.get("status", "SKIP")

        if status in ("SKIP", "SKIPPED", "SKIPPED_TRIAGE"):
            skipped += 1
            agents.append({
                "name": name, "status": status,
                "reason": agent.get("reason", ""),
            })
            continue

        dispatched += 1
        review_path = os.path.join(output_dir, _reviewer_filename(name))
        started_path = os.path.join(output_dir, f"{name}.started")

        if os.path.isfile(review_path):
            finished += 1
            try:
                with open(review_path) as f:
                    review = json.load(f)
                issues = review.get("issues", [])
                counts = dict(Counter(
                    f.get("severity", "medium").lower() for f in issues
                ))
                verdict = review.get("verdict", "UNKNOWN")
                agents.append({
                    "name": name, "status": "FINISHED",
                    "counts": counts, "verdict": verdict,
                })
            except (json.JSONDecodeError, KeyError):
                agents.append({
                    "name": name, "status": "FINISHED",
                    "counts": {}, "verdict": "UNKNOWN",
                    "note": "output malformed",
                })
        elif os.path.isfile(started_path):
            try:
                started_at = datetime.fromisoformat(open(started_path).read().strip())
                elapsed = int((now - started_at).total_seconds())
            except (ValueError, OSError):
                elapsed = 0

            if elapsed > timeout_seconds:
                timed_out += 1
                agents.append({
                    "name": name, "status": "TIMED_OUT",
                    "elapsed_seconds": elapsed,
                })
            else:
                running += 1
                agents.append({
                    "name": name, "status": "RUNNING",
                    "elapsed_seconds": elapsed,
                })
        else:
            not_dispatched += 1
            agents.append({"name": name, "status": "NOT_DISPATCHED"})

    # ALL_DONE = nothing left to WAIT for.
    # NOT_DISPATCHED agents will never start — don't wait for them.
    # Only RUNNING agents block completion.
    all_done = running == 0

    return {
        "all_done": all_done,
        "dispatched": dispatched,
        "finished": finished,
        "running": running,
        "timed_out": timed_out,
        "not_dispatched": not_dispatched,
        "skipped": skipped,
        "agents": agents,
    }


def _fmt_elapsed(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m}m {s}s"


def format_output(result: dict) -> str:
    """Format status check result for display."""
    lines = []
    d = result["dispatched"]
    f = result["finished"]
    r = result["running"]
    t = result["timed_out"]
    nd = result["not_dispatched"]
    lines.append(f"AGENT STATUS: {d} expected, {f} finished, {r} running, {t} timed out, {nd} never started")
    lines.append("")
    for a in result["agents"]:
        name = a["name"]
        st = a["status"]
        if st in ("SKIP", "SKIPPED", "SKIPPED_TRIAGE"):
            lines.append(f"  {name:30s} {st}  ({a.get('reason', '')})")
        elif st == "FINISHED":
            counts = ", ".join(f"{k}={v}" for k, v in sorted(a.get("counts", {}).items()))
            verdict = a.get("verdict", "")
            lines.append(f"  {name:30s} FINISHED  {counts:30s}  VERDICT={verdict}")
        elif st == "RUNNING":
            lines.append(f"  {name:30s} RUNNING   ({_fmt_elapsed(a.get('elapsed_seconds', 0))})")
        elif st == "TIMED_OUT":
            elapsed = _fmt_elapsed(a.get("elapsed_seconds", 0))
            lines.append(f"  {name:30s} TIMED_OUT ({elapsed} — exceeded timeout)")
        elif st == "NOT_DISPATCHED":
            lines.append(f"  {name:30s} NOT_DISPATCHED (never started — LLM may have failed to dispatch)")
    lines.append("")
    lines.append(f"ALL_DONE: {'true' if result['all_done'] else 'false'}")
    if result["not_dispatched"] > 0:
        names = [a["name"] for a in result["agents"] if a["status"] == "NOT_DISPATCHED"]
        lines.append(f"NOTE: {len(names)} agent(s) never started (LLM may have failed to dispatch): {', '.join(names)}")
    if result["timed_out"] > 0:
        names = [a["name"] for a in result["agents"] if a["status"] == "TIMED_OUT"]
        lines.append(f"NOTE: Timed out agents will be excluded from reconciliation: {', '.join(names)}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Check reviewer agent status")
    parser.add_argument("--output-dir", type=str, required=True)
    args = parser.parse_args()

    try:
        result = check_status(args.output_dir)
        print(format_output(result))
        sys.exit(0 if result["all_done"] else 2)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

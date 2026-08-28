#!/usr/bin/env python3
"""
Check Reviewer Agent Status — deterministic status check.

Reads dispatch-plan.json + scans for {agent}.started and {agent}-review.json.
Five states per dispatched agent:
  FINISHED       — canonical schema-2 final review exists
  INVALID_OUTPUT — final filename exists but its contents are not canonical
  RUNNING        — .started marker exists, within timeout
  TIMED_OUT      — .started marker exists, exceeded timeout
  NOT_DISPATCHED — neither marker nor review file (LLM forgot to dispatch)

Exit codes:
    0  ALL_DONE: true (nothing left to wait for, including invalid output)
    2  ALL_DONE: false (some agents still running or not dispatched)
    1  Error (no dispatch plan, bad JSON; also: --wait given without
       --max-seconds, --max-seconds <= 0, or --max-seconds given without
       --wait)
    3  --wait only: --max-seconds elapsed before ALL_DONE became true

--wait mode (script-owned polling, no model calls, no subprocesses): blocks
the calling process, re-running the exact check_status() computation used by
the no-wait path at a 1-2s grain, and returns the instant nothing is left to
wait for. --wait REQUIRES --max-seconds — this script refuses to block
unbounded. On expiry it exits 3, distinct from the no-wait path's 0/1/2, so
callers can tell "gave up after N seconds" apart from "nothing to wait for"
or "still running, check again". The no-wait path's status-check behavior
(exit codes, stdout) is unchanged from before --wait existed; only --help
text differs, since it now also documents --wait/--max-seconds.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

try:
    from .dispatch_status import (
        SKIPPED_STATUSES,
        validate_dispatch_plan_agents,
    )
    from .reviewer_names import derive_reviewer_name
    from .reviewer_lifecycle import (
        finalize_review_command,
        review_paths,
    )
    from .review_document import load_review_document, review_summary
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.dispatch_status import (
        SKIPPED_STATUSES,
        validate_dispatch_plan_agents,
    )
    from review.reviewer_names import derive_reviewer_name
    from review.reviewer_lifecycle import (
        finalize_review_command,
        review_paths,
    )
    from review.review_document import load_review_document, review_summary


DEFAULT_TIMEOUT = 1200  # 20 minutes
DEFAULT_POLL_INTERVAL_SECONDS = 1.5  # grain at which --wait re-checks status


def draft_evidence(output_dir: str, agent_name: str) -> dict:
    """Return digest-bound finalization evidence for a saved draft."""
    reviewer = derive_reviewer_name(agent_name)
    draft_path = review_paths(output_dir, reviewer).draft
    try:
        with open(draft_path, "rb") as draft_handle:
            draft_bytes = draft_handle.read()
    except OSError:
        return {}
    draft_digest = hashlib.sha256(draft_bytes).hexdigest()
    output_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "agent", "output.py"
    )
    command = finalize_review_command(
        output_script, output_dir, reviewer, draft_digest
    )
    return {
        "draft_available": True,
        "draft_digest": draft_digest,
        "finalize_review_command": command,
    }


def attach_draft_evidence(output_dir: str, result: dict) -> dict:
    """Attach finalization evidence to every unfinished agent, once.

    Digesting a draft is the most expensive thing this module does, and it
    is worth nothing to a caller that is not about to print the finalize
    command. check_status() did it inside its own loop, so the wait loop
    re-read and re-hashed every running agent's draft bytes on every 1.5s
    tick and discarded all but the last tick's answer. The CLI attaches it
    to the status it is about to render; step 8's readiness gate, which
    wants only `all_done`, now pays nothing for it.
    """
    for agent in result["agents"]:
        if agent["status"] in ("RUNNING", "TIMED_OUT"):
            agent.update(draft_evidence(output_dir, agent["name"]))
    return result


def check_status(output_dir: str, timeout_seconds: int = None) -> dict:
    """Check status of all agents in the dispatch plan.

    The result carries `dispatched_names` — this plan's dispatched agent
    identities in plan order — so a caller that already ran the gate never
    re-opens and re-validates `dispatch-plan.json` to recover them.
    """
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
    if not isinstance(plan, dict):
        raise ValueError(f"Dispatch plan must be a JSON object, got {plan!r}")
    plan_agents = validate_dispatch_plan_agents(plan.get("agents"))

    now = datetime.now(timezone.utc)
    agents = []
    dispatched_names = []

    for agent in plan_agents:
        name = agent["name"]
        status = agent["status"]

        if status in SKIPPED_STATUSES:
            agents.append({
                "name": name, "status": status,
                "reason": agent.get("reason", ""),
            })
            continue

        dispatched_names.append(name)
        reviewer = derive_reviewer_name(name)
        review_path = review_paths(output_dir, reviewer).final
        started_path = os.path.join(output_dir, f"{name}.started")

        if os.path.isfile(review_path):
            try:
                summary = review_summary(
                    load_review_document(review_path, reviewer)
                )
            except ValueError:
                agents.append({
                    "name": name,
                    "status": "INVALID_OUTPUT",
                    "output_present": True,
                    "note": "final review failed canonical validation",
                })
            else:
                agents.append({
                    "name": name, "status": "FINISHED",
                    "counts": summary["severities"],
                    "verdict": summary["verdict"],
                })
        elif os.path.isfile(started_path):
            try:
                started_at = datetime.fromisoformat(open(started_path).read().strip())
                elapsed = int((now - started_at).total_seconds())
            except (ValueError, OSError):
                elapsed = 0

            if elapsed > timeout_seconds:
                agent_state = {
                    "name": name, "status": "TIMED_OUT",
                    "elapsed_seconds": elapsed,
                }
            else:
                agent_state = {
                    "name": name, "status": "RUNNING",
                    "elapsed_seconds": elapsed,
                }
            agents.append(agent_state)
        else:
            agents.append({"name": name, "status": "NOT_DISPATCHED"})

    # Every count is a projection of the rows above, derived once here so a
    # row and its tally cannot come apart — they did once, when a malformed
    # review was counted finished before its validation ran.
    counts = Counter(entry["status"] for entry in agents)
    running = counts["RUNNING"]

    # ALL_DONE = nothing left to WAIT for.
    # NOT_DISPATCHED agents will never start — don't wait for them.
    # Only RUNNING agents block completion.
    all_done = running == 0

    return {
        "all_done": all_done,
        "timeout_seconds": timeout_seconds,
        "dispatched": len(dispatched_names),
        "dispatched_names": dispatched_names,
        "finished": counts["FINISHED"],
        "invalid": counts["INVALID_OUTPUT"],
        "running": running,
        "timed_out": counts["TIMED_OUT"],
        "not_dispatched": counts["NOT_DISPATCHED"],
        "skipped": sum(counts[status] for status in SKIPPED_STATUSES),
        "agents": agents,
    }


def wait_for_all_done(
    output_dir: str,
    max_seconds: float,
    timeout_seconds: int = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    sleep_fn=time.sleep,
    now_fn=time.monotonic,
):
    """Block until check_status() reports ALL_DONE or max_seconds elapses.

    Re-runs the exact same check_status() computation the no-wait path uses,
    at `poll_interval` grain (1-2s). No model calls, no subprocesses — this
    is script-internal polling only.

    Returns (result, expired):
        result  — the last check_status() dict observed.
        expired — True if max_seconds elapsed before ALL_DONE became true.

    Callers with an already-satisfied status get back immediately (expired
    is False, no sleep occurs) — the check happens before the first sleep.
    """
    start = now_fn()
    while True:
        result = check_status(output_dir, timeout_seconds=timeout_seconds)
        if result["all_done"]:
            return result, False
        elapsed = now_fn() - start
        remaining = max_seconds - elapsed
        if remaining <= 0:
            return result, True
        sleep_fn(min(poll_interval, remaining))


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
    invalid = result.get("invalid", 0)
    lines.append(f"AGENT STATUS: {d} expected, {f} finished, {invalid} invalid, {r} running, {t} timed out, {nd} never started")
    lines.append("")
    for a in result["agents"]:
        name = a["name"]
        st = a["status"]
        if st in SKIPPED_STATUSES:
            lines.append(f"  {name:30s} {st}  ({a.get('reason', '')})")
        elif st == "FINISHED":
            counts = ", ".join(
                f"{k}={v}"
                for k, v in sorted(a.get("counts", {}).items())
                if v
            )
            verdict = a.get("verdict", "")
            lines.append(f"  {name:30s} FINISHED  {counts:30s}  VERDICT={verdict}")
        elif st == "RUNNING":
            lines.append(f"  {name:30s} RUNNING   ({_fmt_elapsed(a.get('elapsed_seconds', 0))})")
        elif st == "TIMED_OUT":
            elapsed = _fmt_elapsed(a.get("elapsed_seconds", 0))
            lines.append(f"  {name:30s} TIMED_OUT ({elapsed} — exceeded timeout)")
        elif st == "NOT_DISPATCHED":
            lines.append(f"  {name:30s} NOT_DISPATCHED (never started — LLM may have failed to dispatch)")
        elif st == "INVALID_OUTPUT":
            lines.append(
                f"  {name:30s} INVALID_OUTPUT "
                "(final review failed canonical validation)"
            )
        if a.get("draft_available"):
            lines.append(
                f"  {'':30s} DRAFT  digest={a['draft_digest']}"
            )
            lines.append(
                "  "
                f"{'':30s} FINALIZE_REVIEW_COMMAND: "
                f"{a['finalize_review_command']}"
            )
    lines.append("")
    lines.append(f"ALL_DONE: {'true' if result['all_done'] else 'false'}")
    if result["not_dispatched"] > 0:
        names = [a["name"] for a in result["agents"] if a["status"] == "NOT_DISPATCHED"]
        lines.append(f"NOTE: {len(names)} agent(s) never started (LLM may have failed to dispatch): {', '.join(names)}")
    if result["timed_out"] > 0:
        names = [a["name"] for a in result["agents"] if a["status"] == "TIMED_OUT"]
        lines.append(f"NOTE: Timed out agents will be excluded from reconciliation: {', '.join(names)}")
    if result.get("invalid", 0) > 0:
        names = [
            a["name"] for a in result["agents"]
            if a["status"] == "INVALID_OUTPUT"
        ]
        lines.append(
            "NOTE: Invalid final reviews will be excluded from "
            f"reconciliation: {', '.join(names)}"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Check reviewer agent status. Exit codes: 0 ALL_DONE, "
        "2 still running, 1 error, 3 (--wait only) --max-seconds expired."
    )
    parser.add_argument("--output-dir", type=str, required=True)
    parser.add_argument(
        "--wait", action="store_true",
        help="Block until ALL_DONE (exit 0) or --max-seconds elapses (exit 3). "
        "Requires --max-seconds — unbounded waits are refused.",
    )
    parser.add_argument(
        "--max-seconds", type=float, default=None,
        help="Required with --wait: maximum seconds (> 0) to block before "
        "exiting 3. Rejected without --wait — it would silently do nothing.",
    )
    args = parser.parse_args()

    if args.max_seconds is not None and not args.wait:
        print(
            "ERROR: --max-seconds has no effect without --wait "
            "(did you mean to pass --wait too?)",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.wait and args.max_seconds is None:
        print(
            "ERROR: --wait requires --max-seconds (refusing to block unbounded)",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.max_seconds is not None and args.max_seconds <= 0:
        print(
            f"ERROR: --max-seconds must be > 0, got {args.max_seconds}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        if args.wait:
            result, expired = wait_for_all_done(args.output_dir, args.max_seconds)
            print(format_output(attach_draft_evidence(args.output_dir, result)))
            if expired:
                # Both streams are typically merged by the caller (e.g. a
                # Codex subprocess capture) — flush stdout first so the
                # status table above is never interleaved after this
                # stderr line in the merged read order.
                sys.stdout.flush()
                print(
                    f"EXPIRED: --max-seconds={args.max_seconds} elapsed before "
                    "ALL_DONE",
                    file=sys.stderr,
                )
                sys.exit(3)
            sys.exit(0)
        else:
            result = check_status(args.output_dir)
            print(format_output(attach_draft_evidence(args.output_dir, result)))
            sys.exit(0 if result["all_done"] else 2)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

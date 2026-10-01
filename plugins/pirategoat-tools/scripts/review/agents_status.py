#!/usr/bin/env python3
"""
Check Reviewer Agent Status — deterministic status check.

Reads the dispatch plan and checks each dispatch identity's reviewer directory.
Six states per dispatched agent:
  FINISHED        — canonical schema-2 final review exists
  INVALID_OUTPUT  — final filename exists but its contents are not canonical
  RUNNING         — fixed `started` marker exists, within timeout
  TIMED_OUT       — fixed `started` marker exists, exceeded timeout
  BOOTSTRAP_ERROR — `bootstrap-error` failure record, no started marker:
                    bootstrap exited with STATUS: ERROR; terminal, the
                    recorded error is why the reviewer stopped
  NOT_DISPATCHED  — no started marker, failure record or review file (LLM
                    forgot to dispatch, or the reviewer is queued)

The queue. When step 6 stamped a `dispatch_waves` record into the plan
(`dispatch_status.DISPATCH_WAVES_KEY`), a NOT_DISPATCHED reviewer is *queued*
when the record lists it as queued, or lists it in wave 1 and its grace
window past `stamped_at` has run out (a cap or lock-file rejection).
Wave-1 NOT_DISPATCHED reviewers still inside the grace window are *pending*:
just launched, so they occupy a slot. So are reviewers this script released
on an earlier SLOT_FREE, inside the grace window from their release.
`dispatch_status.queue_state()` is the one definition. A slot is free when
the queue is non-empty and `cap - running - pending > 0` (always, when the
cap is unbounded), and, after a release, some progress has happened since
(`_progress_since_release`). A plan without the record is a legacy plan:
nothing is queued and no slot is ever reported free. ALL_DONE is true only
when nothing is running, pending or queued, so the printed envelope never
claims completion over un-started reviewers.

Releases. On a --wait exit 4 this script stamps the names it tells the
orchestrator to launch (the first `SLOTS:` queued names, all when
unbounded, filtered against the plan re-read under the lock) into the
record's `released` map, with `last_release`, under the output-directory
lock. `QUEUED:` then prints exactly those names. The no-wait path is
read-only: its exit 4 only reports the free slot, and its NOTE tells the
orchestrator to leave the launch to the watchdog.

Abandonment. A NOT_DISPATCHED reviewer that was launched and never starts
is *abandoned* (`dispatch_status.queue_state()`) once the grace window of
its latest release has passed after `dispatch_status.MAX_RELEASES` counted
releases: releases made with nothing RUNNING or pending and no reviewer
ended since the previous release, so a refusal by a host full of this
review's RUNNING or pending reviewers (our cap, Codex's thread limit)
cannot abandon a healthy reviewer (host threads it cannot see are a known
limitation; see docs/review-pipeline.md). It prints
`ABANDONED (never started after N launch attempts)`, holds no slot and does
not block ALL_DONE, like a legacy NOT_DISPATCHED row, so --wait always
reaches exit 0.

Exit codes:
    0  ALL_DONE: true (nothing left to wait for, including invalid output)
    2  ALL_DONE: false (some agents still running or not dispatched)
    1  Error (no dispatch plan, bad JSON; also: --wait given without
       --max-seconds, --max-seconds <= 0, or --max-seconds given without
       --wait)
    3  --wait only: --max-seconds elapsed before ALL_DONE became true
    4  SLOT_FREE: a queued reviewer can be launched now; the `QUEUED:` line
       names them and `SLOTS:` says how many fit. --wait reports it only after
       the condition held for SLOT_FREE_SETTLE_SECONDS, and releases them;
       the no-wait path reports it at once and releases nothing.

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
        DISPATCH_WAVES_KEY,
        LAST_RELEASE_KEY,
        SKIPPED_STATUSES,
        load_dispatch_plan,
        queue_state,
        record_release,
        validate_dispatch_waves,
    )
    from .atomic_io import atomic_write_json, output_dir_lock
    from .reviewer_names import derive_reviewer_name
    from .reviewer_lifecycle import (
        bootstrap_error_path,
        finalize_review_command,
        read_bootstrap_error,
        review_paths,
        started_marker_path,
    )
    from .review_document import load_review_document, review_summary
    from .run_paths import artifact_path
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.dispatch_status import (
        DISPATCH_WAVES_KEY,
        LAST_RELEASE_KEY,
        SKIPPED_STATUSES,
        load_dispatch_plan,
        queue_state,
        record_release,
        validate_dispatch_waves,
    )
    from review.atomic_io import atomic_write_json, output_dir_lock
    from review.reviewer_names import derive_reviewer_name
    from review.reviewer_lifecycle import (
        bootstrap_error_path,
        finalize_review_command,
        read_bootstrap_error,
        review_paths,
        started_marker_path,
    )
    from review.review_document import load_review_document, review_summary
    from review.run_paths import artifact_path


DEFAULT_TIMEOUT = 1200  # 20 minutes
DEFAULT_POLL_INTERVAL_SECONDS = 1.5  # grain at which --wait re-checks status
# The line `format_output` always renders, and the only proof a caller has
# that this program actually reported a status. Exit 2 is not that proof:
# argparse answers an unknown flag with 2, and Python exits 2 for a script
# path that does not exist, so a run that never reached `check_status`
# looks exactly like "some agents are still running" from the outside.
# `analysis/review_transcript.py` imports this to tell them apart.
STATUS_ENVELOPE_PREFIX = "ALL_DONE: "
# Exit code for "a queued reviewer can be launched now" (see the module
# docstring). `analysis/review_transcript.py` counts it as a poll outcome.
EXIT_SLOT_FREE = 4
# Reviewer states that left their slot for good; their count growing is the
# progress a repeated SLOT_FREE waits for (see _progress_since_release).
TERMINAL_STATUSES = ("FINISHED", "INVALID_OUTPUT", "TIMED_OUT", "BOOTSTRAP_ERROR")
# How long --wait requires the slot-free condition to hold before exiting 4.
# A reviewer the orchestrator just launched has no started marker yet, so
# for a few seconds it still looks queued and its slot still looks free;
# without the settle window a fresh watchdog would exit 4 at once and the
# orchestrator would loop on the same launch.
SLOT_FREE_SETTLE_SECONDS = 30


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


def check_status(
    output_dir: str, timeout_seconds: int = None, now: datetime = None,
) -> dict:
    """Check status of all agents in the dispatch plan.

    The result carries `dispatched_names` — this plan's dispatched agent
    identities in plan order — so a caller that already ran the gate never
    re-opens and re-validates the dispatch plan to recover them.

    Queue keys (see the module docstring): `waves_recorded` (the plan has a
    `dispatch_waves` record), `cap` (int, or None when unbounded or legacy),
    `queued`, `pending` and `abandoned` (names, plan order), `slots` (free
    slots, None when unbounded) and `slot_free`. Queued NOT_DISPATCHED rows
    carry `"queued": True`, pending ones `"pending": True`, abandoned ones
    `"abandoned": True` and `"launch_attempts"`. `now` (an aware
    datetime) is injectable for tests; it defaults to the current UTC time.
    """
    plan_path = artifact_path(output_dir, "dispatch_plan")
    if not os.path.isfile(plan_path):
        raise FileNotFoundError(f"No dispatch plan at {plan_path}")

    # Read timeout from review context, falling back to the default.
    if timeout_seconds is None:
        ctx_path = artifact_path(output_dir, "review_context")
        if os.path.isfile(ctx_path):
            with open(ctx_path) as f:
                ctx = json.load(f)
            timeout_seconds = ctx.get("review", {}).get("agent_timeout_seconds", DEFAULT_TIMEOUT)
        else:
            timeout_seconds = DEFAULT_TIMEOUT

    plan = load_dispatch_plan(plan_path)
    plan_agents = plan["agents"]
    waves = plan.get(DISPATCH_WAVES_KEY)

    if now is None:
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
        started_path = started_marker_path(output_dir, reviewer)

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
        elif os.path.isfile(bootstrap_error_path(output_dir, reviewer)):
            # Read after the started marker, which supersedes the failure
            # record whichever was written first (reviewer_lifecycle.mark_started).
            agents.append({
                "name": name, "status": "BOOTSTRAP_ERROR",
                "error": read_bootstrap_error(output_dir, reviewer),
            })
        else:
            agents.append({"name": name, "status": "NOT_DISPATCHED"})

    # Every count is a projection of the rows above, derived once here so a
    # row and its tally cannot come apart — they did once, when a malformed
    # review was counted finished before its validation ran.
    counts = Counter(entry["status"] for entry in agents)
    running = counts["RUNNING"]

    queue = queue_state(
        waves,
        [entry["name"] for entry in agents if entry["status"] == "NOT_DISPATCHED"],
        now,
    )
    queued, pending = queue["queued"], queue["pending"]
    abandoned = queue["abandoned"]
    for entry in agents:
        if entry["name"] in queued:
            entry["queued"] = True
        elif entry["name"] in pending:
            entry["pending"] = True
        elif entry["name"] in abandoned:
            entry["abandoned"] = True
            entry["launch_attempts"] = queue["attempts"][entry["name"]]
    cap = waves["cap"] if waves is not None else None
    slots = None if cap is None else max(0, cap - running - len(pending))
    terminal = sum(counts[status] for status in TERMINAL_STATUSES)
    slot_free = (
        bool(queued) and (slots is None or slots > 0)
        and _progress_since_release(waves, pending, running, terminal)
    )

    # ALL_DONE = nothing left to WAIT for.
    # NOT_DISPATCHED (not queued, not pending, including abandoned) and
    # BOOTSTRAP_ERROR agents will never start on their own, so they do not
    # block. RUNNING, queued and pending agents do: a queued reviewer is
    # launched on SLOT_FREE, and a pending one was just launched and has not
    # written its marker yet. Running rows turn TIMED_OUT at the agent
    # timeout; after that a reviewer that never starts is released with
    # nothing else running, those releases count, and it is abandoned
    # (dispatch_status.queue_state), so this always turns true.
    all_done = running == 0 and not queued and not pending

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
        "bootstrap_error": counts["BOOTSTRAP_ERROR"],
        "skipped": sum(counts[status] for status in SKIPPED_STATUSES),
        "waves_recorded": waves is not None,
        "cap": cap,
        "queued": queued,
        "pending": pending,
        "abandoned": abandoned,
        "slots": slots,
        "slot_free": slot_free,
        "terminal": terminal,
        "agents": agents,
    }


def _progress_since_release(waves, pending, running, terminal):
    """Whether a SLOT_FREE may fire again after the last release.

    The first release (no `last_release`) always may. After one, a queued
    reviewer the host rejected (Codex's agent-thread limit, a Claude cap
    shared with other subagents) turns queued again once its grace runs
    out, while the slot it was given still looks free; firing on that alone
    re-launched it every settle window. So a later SLOT_FREE needs one of:

    - the terminal count (finished, invalid, timed out, bootstrap error)
      grew since the release: a reviewer really left its slot;
    - nothing running and nothing pending: no slot can ever free by
      itself, so waiting for progress would stall until step 8 escalates.

    A wave-1 rejection that crosses its grace after a release waits for
    one of these too; it never waits past the agent timeout, since every
    running reviewer then turns TIMED_OUT.
    """
    last_release = (waves or {}).get(LAST_RELEASE_KEY)
    if last_release is None:
        return True
    if terminal > last_release["terminal_count"]:
        return True
    return running == 0 and not pending


def release_queued(output_dir: str, result: dict, now: datetime = None) -> list:
    """Stamp the reviewers a SLOT_FREE tells the orchestrator to launch.

    The candidates are the first `slots` of `result["queued"]` (all of them
    when the cap is unbounded). Under the output-directory lock the plan is
    re-read, and only candidates the reloaded record still lists in
    `wave_1`/`queued` are kept: a step-6 restamp between the status check
    and here may have dropped one, and stamping it would leave a record
    that fails validation on every later read. The kept names go into the
    record's `released` (`dispatch_status.record_release()`), with
    `last_release` holding the terminal count, and the new record is
    validated before the atomic write, so the next status check counts
    them as pending (occupying their slots) for the grace window.
    `record_release()` decides from `result`'s RUNNING and pending counts
    and terminal count whether the release counts toward abandonment.

    Sets and returns `result["released"]`: `format_output()` prints exactly
    these on the `QUEUED:` line. When no candidate survives the filter,
    `result["slot_free"]` becomes False (nothing to launch; the caller
    exits 2). A plan that cannot be read or written, or a new record that
    fails validation, skips the write with a stderr WARNING and still
    returns the names, unstamped: the orchestrator must launch them either
    way.
    """
    slots = result["slots"]
    names = list(result["queued"] if slots is None else result["queued"][:slots])
    result["released"] = names
    if not names:
        return names
    if now is None:
        now = datetime.now(timezone.utc)
    plan_path = artifact_path(output_dir, "dispatch_plan")
    try:
        with output_dir_lock(output_dir):
            plan = load_dispatch_plan(plan_path)
            record = plan.get(DISPATCH_WAVES_KEY)
            planned = (
                set(record["wave_1"]) | set(record["queued"])
                if record is not None else set()
            )
            names = [name for name in names if name in planned]
            result["released"] = names
            if not names:
                result["slot_free"] = False
                return names
            released = record_release(
                record, names, result["terminal"], now.isoformat(),
                result["running"] + len(result["pending"]),
            )
            try:
                validate_dispatch_waves(released)
            except ValueError as exc:
                print(
                    f"WARNING: not recording the release, the new record "
                    f"is invalid: {exc}",
                    file=sys.stderr,
                )
                return names
            plan[DISPATCH_WAVES_KEY] = released
            atomic_write_json(plan_path, plan)
    except (OSError, ValueError) as exc:
        print(f"WARNING: could not record the release: {exc}", file=sys.stderr)
    return names


def wait_for_all_done(
    output_dir: str,
    max_seconds: float,
    timeout_seconds: int = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    sleep_fn=time.sleep,
    now_fn=time.monotonic,
    settle_seconds: float = SLOT_FREE_SETTLE_SECONDS,
):
    """Block until ALL_DONE, a settled SLOT_FREE, or max_seconds elapses.

    Re-runs the exact same check_status() computation the no-wait path uses,
    at `poll_interval` grain (1-2s). No model calls, no subprocesses — this
    is script-internal polling only.

    Returns (result, expired):
        result  — the last check_status() dict observed; `result["slot_free"]`
                  tells a SLOT_FREE return apart from ALL_DONE.
        expired — True if max_seconds elapsed before either became true.

    SLOT_FREE returns only once `result["slot_free"]` has held continuously
    for `settle_seconds` of `now_fn` time inside this call: a reviewer
    launched just before this watchdog started has no started marker yet,
    and it would otherwise read as still queued with its slot still free.
    A condition that clears before settling restarts the window.

    Callers with an already-satisfied status get back immediately (expired
    is False, no sleep occurs) — the check happens before the first sleep.
    """
    start = now_fn()
    slot_free_since = None
    while True:
        result = check_status(output_dir, timeout_seconds=timeout_seconds)
        if result["all_done"]:
            return result, False
        now = now_fn()
        if result["slot_free"]:
            if slot_free_since is None:
                slot_free_since = now
            if now - slot_free_since >= settle_seconds:
                return result, False
        else:
            slot_free_since = None
        remaining = max_seconds - (now - start)
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
    be = result.get("bootstrap_error", 0)
    lines.append(f"AGENT STATUS: {d} expected, {f} finished, {invalid} invalid, {r} running, {t} timed out, {be} bootstrap errors, {nd} never started")
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
        elif st == "BOOTSTRAP_ERROR":
            lines.append(f"  {name:30s} BOOTSTRAP_ERROR ({a.get('error', '')})")
        elif st == "NOT_DISPATCHED" and name in result.get("released", ()):
            lines.append(f"  {name:30s} QUEUED (launch now)")
        elif st == "NOT_DISPATCHED" and a.get("abandoned"):
            lines.append(
                f"  {name:30s} ABANDONED (never started after "
                f"{a.get('launch_attempts', 0)} launch attempts)"
            )
        elif st == "NOT_DISPATCHED" and a.get("queued"):
            lines.append(f"  {name:30s} QUEUED (launch when a slot frees)")
        elif st == "NOT_DISPATCHED" and a.get("pending"):
            lines.append(f"  {name:30s} PENDING (launched, not started yet; no action)")
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
    lines.append(
        f"{STATUS_ENVELOPE_PREFIX}{'true' if result['all_done'] else 'false'}"
    )
    if result.get("waves_recorded"):
        # On SLOT_FREE, `QUEUED:` is exactly the released names: the ones
        # to launch now, already counted as holding their slots.
        queued = result.get("released", result.get("queued", []))
        slots = result.get("slots")
        lines.append(f"QUEUED: {', '.join(queued) if queued else 'none'}")
        lines.append(f"SLOTS: {'all' if slots is None else slots}")
        if result.get("released"):
            lines.append(
                "NOTE: launch every QUEUED agent now, except one whose launch "
                "was already accepted. Each holds its slot until it starts; "
                "a rejected one is queued again later."
            )
        elif result.get("slot_free"):
            # The no-wait path never releases (see main()).
            lines.append(
                "NOTE: a slot is free, but do not launch from this output: "
                "the background watchdog (--wait) exits 4 with the agents to "
                "launch. End your turn; if no watchdog is running, launch one."
            )
    names = [
        a["name"] for a in result["agents"]
        if a["status"] == "NOT_DISPATCHED"
        and not a.get("queued") and not a.get("pending")
    ]
    if names:
        lines.append(f"NOTE: {len(names)} agent(s) never started (LLM may have failed to dispatch): {', '.join(names)}")
    if result.get("bootstrap_error", 0) > 0:
        names = [a["name"] for a in result["agents"] if a["status"] == "BOOTSTRAP_ERROR"]
        lines.append(
            f"NOTE: {len(names)} agent(s) failed at bootstrap and will be excluded "
            f"from reconciliation; do not dispatch them again: {', '.join(names)}"
        )
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
        "2 still running, 1 error, 3 (--wait only) --max-seconds expired, "
        "4 SLOT_FREE (a queued reviewer can be launched; see QUEUED/SLOTS)."
    )
    parser.add_argument("--output-dir", type=str, required=True)
    parser.add_argument(
        "--wait", action="store_true",
        help="Block until ALL_DONE (exit 0), a settled SLOT_FREE (exit 4) or "
        "--max-seconds elapses (exit 3). "
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
            result, expired = wait_for_all_done(
                args.output_dir, args.max_seconds,
                settle_seconds=SLOT_FREE_SETTLE_SECONDS,
            )
            if not expired and result["slot_free"]:
                # May clear slot_free: a restamp dropped every candidate.
                release_queued(args.output_dir, result)
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
            if result["slot_free"]:
                sys.exit(EXIT_SLOT_FREE)
            sys.exit(0 if result["all_done"] else 2)
        else:
            # Read-only: only --wait releases (its exit-4 rule is the one
            # that launches), so a notification-time status call cannot
            # stamp names the orchestrator will never launch.
            result = check_status(args.output_dir)
            print(format_output(attach_draft_evidence(args.output_dir, result)))
            if result["slot_free"]:
                sys.exit(EXIT_SLOT_FREE)
            sys.exit(0 if result["all_done"] else 2)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

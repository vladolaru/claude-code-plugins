#!/usr/bin/env python3
"""Lifecycle measurement for the two synthesis agents.

The reviewers are measured end to end: `agent/bootstrap.py` writes
`<agent>.started` when a reviewer boots, `ReviewOutputBuilder.save()`
logs `agent_complete`, and `agents_status.py` polls the pair. The two
SYNTHESIS agents — the review-reconciliator (step 8) and the decision
critic (step 10) — sit entirely outside that machinery: neither runs
bootstrap, neither writes a `<agent>-review.json`, and neither is ever
in `dispatch-plan.json`, which is the only list `agents_status.py`
iterates. So the critic, the longest single phase of an audited
2026-08-19 run at ~11 minutes, had no duration anywhere in the manifest,
and a hung synthesis agent blocked the orchestrator's foreground Task
call with no artifact recording why the run stalled.

This module closes that gap with the same two-fact shape the reviewers
use, minus the polling:

1. **Dispatch.** Steps 8 and 10 call `mark_dispatched()` when they
   produce the briefing that hands the agent off. The script owns that
   moment — the LLM performs the Task call, so the script cannot observe
   the agent's own boot, but it CAN stamp the instant it asked for one.
   The marker is byte-compatible with bootstrap's: `<agent>.started`,
   one UTC ISO timestamp, swept by pipeline.py's `*.started` glob.

2. **Completion.** Observed at the NEXT step the script re-enters —
   step 9 for the reconciliator, step 11 (finalize) for both — by the
   existence of the artifact that agent is contractually required to
   leave behind. That timing is why every entry carries TWO clocks:
   `completed_at` is the artifact's mtime, the closest available proxy
   for when the agent actually finished, and `observed_at` is when this
   script looked, which is strictly later and is NOT a completion time.
   `duration_ms` is measured against the first; `elapsed_ms` against the
   second. No polling daemon, no background process.

**Timeout policy is report, never kill.** Both agents run in the
orchestrator's foreground, so nothing here can interrupt one. At
finalize, a marker with no completion artifact records `stalled: true`
and how long the stall had lasted when finalize looked.

**Three timestamps, three meanings**, stated on the artifact itself in
`semantics` so no consumer has to infer them:

- `duration_ms` = completion artifact mtime - dispatch. The measured
  phase.
- `elapsed_ms` = this observation - dispatch. An upper bound on the
  phase, and on a stalled agent the length of the stall so far.
- section-level `observed_at` = when the LAST observation ran. It bounds
  the whole section's freshness and is not any agent's clock; each row
  carries its own `observed_at`, which may be older because a completed
  row is carried forward verbatim rather than re-observed.

**Availability, not zero.** A run older than this feature writes no
marker and no `synthesis-agents.json`, so the manifest section is absent
and its family reads "missing". A never-measured phase must never
project as a zero-duration one.
"""

import json
import os
from datetime import datetime, timezone

try:
    from .atomic_io import atomic_write_json
except ImportError:  # pragma: no cover - direct-path import fallback
    import sys
    from pathlib import Path

    _scripts_parent = str(Path(__file__).resolve().parent.parent)
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.atomic_io import atomic_write_json


LIFECYCLE_FILENAME = "synthesis-agents.json"
LIFECYCLE_SCHEMA = 1

# The row shape, declared ONCE. Three modules write it — this producer,
# manifest_sections.build_synthesis_agents_manifest(), and the metrics
# consumer's _sanitize_synthesis_agents() — and a key added to one of
# them alone is a measurement that silently never reaches the manifest or
# the cohort. Both projections assert parity against this tuple, so
# teaching only one of the three fails loudly instead of green.
ROW_KEYS = (
    "agent",
    "step",
    "completion_artifact",
    "verdict",
    "started_at",
    "completed_at",
    "observed_at",
    "duration_ms",
    "elapsed_ms",
    "stalled",
)

# What the numbers mean, carried ON the artifact and re-asserted by the
# metrics consumer — the same self-description discipline the coverage
# family's `semantics` key enforces. Two clocks per row plus a
# section-level stamp is exactly the kind of shape a reader guesses wrong
# about, and a guess here silently turns an upper bound into a
# measurement.
LIFECYCLE_SEMANTICS = (
    "duration_ms=artifact_mtime_minus_dispatch; "
    "elapsed_ms=observation_minus_dispatch; "
    "section observed_at=last_observation"
)

RECONCILIATOR = "review-reconciliator"
DECISION_CRITIC = "decision-reviewer"

# (agent name, dispatching step, completion artifact).
#
# The completion artifact is the one file the step's handoff gate makes
# mandatory, NOT the richest thing the agent might write:
#
# - review-findings.json is the reconciliator's ONLY artifact (step 8's
#   briefing says so in as many words, and its handoff verifies it).
# - decision-critic-verdict.json is required by step 10's handoff in BOTH
#   briefing branches, and the briefing explicitly requires it even when
#   the critic crashed or timed out (`{"verdict": "SKIPPED", ...}`).
#   decision-critic-findings.md is the critic's own richer output but only
#   exists when the critic actually produced a critique, so keying
#   completion on it would report a crashed critic as still running.
SYNTHESIS_AGENTS = (
    (RECONCILIATOR, 8, "review-findings.json"),
    (DECISION_CRITIC, 10, "decision-critic-verdict.json"),
)

_AGENT_STEPS = {name: step for name, step, _ in SYNTHESIS_AGENTS}


def marker_path(output_dir, name):
    """Path of one synthesis agent's dispatch marker."""
    return os.path.join(output_dir, f"{name}.started")


def mark_dispatched(output_dir, name, *, now=None):
    """Stamp the moment the script handed `name` off for dispatch.

    Best-effort by construction: an unwritable marker costs the run a
    measurement, never the review.

    Re-stamping on a re-entered step is deliberate — the live attempt is
    the one whose duration means anything — but it is ONLY safe because
    the caller observes first. Step 10 is genuinely re-entered after a
    completed critic (its own skip-decision comment says so: a rerun once
    the reconciled verdict escalates), and a bare re-stamp there moves
    the dispatch clock past an already-written completion artifact. The
    next observation then reads the artifact as predating its dispatch,
    discards it, and reports a finished 11-minute critique as
    `stalled: true` with `elapsed_ms: 0`. Observing before re-stamping
    carries the real completion forward and closes that window.
    """
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    try:
        with open(marker_path(output_dir, name), "w") as handle:
            handle.write(stamp)
    except OSError:
        return None
    return stamp


def _read_marker(output_dir, name):
    """Return (dispatched, started_at). Unreadable text still counts."""
    path = marker_path(output_dir, name)
    if not os.path.isfile(path):
        return False, None
    try:
        with open(path) as handle:
            raw = handle.read().strip()
    except OSError:
        # The marker exists, so the dispatch happened; only its timestamp
        # is lost. Reporting "not dispatched" here would hide a stall.
        return True, None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return True, None
    if parsed.tzinfo is None:
        return True, None
    return True, parsed


def _artifact_verdict(path):
    """The completion artifact's own `verdict` string, or None.

    Read at observation because it changes what a duration MEANS. A critic
    row carrying "SKIPPED" is not a critique that took N seconds — quick
    mode skipped it, or it crashed and the orchestrator wrote the
    handoff's fallback — so its span measures dispatch to
    orchestrator-gave-up, an upper bound on a critique that may never have
    started. Blending those into a critic duration statistic would drag
    the cohort mean toward crash-resolution latency, so the aggregate
    counts them apart. None is the honest answer for an absent,
    unreadable, non-object, or verdict-less artifact.
    """
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    verdict = data.get("verdict")
    return verdict if isinstance(verdict, str) else None


def _artifact_mtime(path):
    """UTC completion proxy for a completion artifact, or None."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    try:
        return datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _delta_ms(start, end):
    """Whole milliseconds between two aware datetimes, or None.

    A negative interval is not a measurement — it is evidence the two
    clocks disagree — so it yields None rather than a clamped zero.
    """
    if start is None or end is None:
        return None
    elapsed = end - start
    if elapsed.total_seconds() < 0:
        return None
    return (
        elapsed.days * 24 * 60 * 60 * 1000
        + elapsed.seconds * 1000
        + elapsed.microseconds // 1000
    )


def _prior_entries(output_dir):
    """Completed entries already recorded, keyed by agent name.

    Only completed ones are carried forward. An earlier observation of a
    still-running agent is stale by definition, and its `observed_at`
    would understate a stall that finalize is about to adjudicate.
    """
    path = os.path.join(output_dir, LIFECYCLE_FILENAME)
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("schema") != LIFECYCLE_SCHEMA:
        return {}
    entries = data.get("agents")
    if not isinstance(entries, list):
        return {}
    kept = {}
    for entry in entries:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("agent"), str)
            and entry.get("completed_at") is not None
        ):
            kept[entry["agent"]] = entry
    return kept


def observe(output_dir, *, finalize=False, now=None):
    """Record what is observable right now about dispatched agents.

    `finalize=True` is the only mode that adjudicates a stall: before
    finalize, an agent with no completion artifact is simply one this
    observation caught mid-flight, which says nothing about the run.

    An already-completed entry is preserved verbatim. Its observation was
    the earliest and therefore the tightest bound the run will ever have;
    re-deriving it at finalize would push `observed_at` minutes later and
    describe a phase that had already ended.

    Returns the payload written, or None when nothing could be written.
    """
    observed_at = now or datetime.now(timezone.utc)
    prior = _prior_entries(output_dir)

    entries = []
    for name, step, artifact_name in SYNTHESIS_AGENTS:
        carried = prior.get(name)
        if carried is not None:
            entries.append(carried)
            continue
        dispatched, started_at = _read_marker(output_dir, name)
        if not dispatched:
            # No marker: this agent was never dispatched by a build that
            # writes markers. That is an absence, not a zero — it earns
            # no row, and the section's presence is what tells a reader
            # the run was capable of measuring one.
            continue
        artifact_path = os.path.join(output_dir, artifact_name)
        completed_at = _artifact_mtime(artifact_path)
        if (
            completed_at is not None
            and started_at is not None
            and completed_at < started_at
        ):
            # The artifact predates the dispatch, so it cannot be this
            # dispatch's output. Treat it exactly like an absent one
            # rather than publishing a negative or borrowed duration.
            completed_at = None
        entries.append({
            "agent": name,
            "step": step,
            "completion_artifact": artifact_name,
            # What the agent concluded, which is what makes the duration
            # beside it interpretable — see _artifact_verdict(). Read only
            # from an artifact this dispatch can claim; a discarded one
            # would attach a stale conclusion to a live dispatch.
            "verdict": (
                _artifact_verdict(artifact_path)
                if completed_at is not None else None
            ),
            "started_at": started_at.isoformat() if started_at else None,
            # The completion artifact's mtime — the closest available
            # proxy for when the agent actually finished.
            "completed_at": (
                completed_at.isoformat() if completed_at else None
            ),
            # When this script looked. Strictly later than the real
            # completion, and never a substitute for it.
            "observed_at": observed_at.isoformat(),
            "duration_ms": _delta_ms(started_at, completed_at),
            "elapsed_ms": _delta_ms(started_at, observed_at),
            "stalled": finalize and completed_at is None,
        })

    payload = {
        "schema": LIFECYCLE_SCHEMA,
        "semantics": LIFECYCLE_SEMANTICS,
        # The LAST observation, bounding the whole section's freshness.
        # Not any one agent's clock: a carried-forward row keeps its own,
        # older `observed_at` because re-observing it would replace the
        # tightest bound the run has with a looser one.
        "observed_at": observed_at.isoformat(),
        "finalized": bool(finalize),
        "agents": entries,
    }
    try:
        atomic_write_json(
            os.path.join(output_dir, LIFECYCLE_FILENAME), payload
        )
    except OSError:
        return None
    return payload


def step_for(name):
    """Dispatching step for a synthesis agent, or None."""
    return _AGENT_STEPS.get(name)

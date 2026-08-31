#!/usr/bin/env python3
"""Lifecycle measurement for the two synthesis agents.

The reviewers are measured end to end: `agent/bootstrap.py` writes the fixed
`reviewers/<reviewer>/started` marker when a reviewer boots, `ReviewOutputBuilder.save()`
logs `agent_complete`, and `agents_status.py` polls the pair. The two
SYNTHESIS agents — the review-reconciliator (step 8) and the decision
critic (step 10) — sit entirely outside that machinery: neither runs
bootstrap, neither writes a reviewer-directory final, and neither is ever
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
   The marker's BODY is byte-compatible with bootstrap's — one UTC ISO
   timestamp — but its NAME deliberately is not. It carries
   MARKER_SUFFIX, which namespaces it away from the reviewer
   reviewer-directory `started` contract other tools scan; see that constant for why the
   separation has to be structural rather than a courtesy.

2. **Completion.** Observed at the NEXT step the script re-enters —
   step 9 for the reconciliator, step 11 (finalize) for both — by the
   existence of the artifact that agent is contractually required to
   leave behind. `completed_at` is that artifact's mtime and
   `duration_ms` is the span from dispatch to it. No polling daemon, no
   background process.

   One clock, deliberately. An earlier version also recorded when the
   script looked, so a reader could bound the observation lag; the run's
   own step cadence already bounds it, and the second number answered a
   question nobody asked.

**Timeout policy is report, never kill.** Both agents run in the
orchestrator's foreground, so nothing here can interrupt one. At
finalize, a marker with no completion artifact records `stalled: true`.

**Availability, not zero.** A run older than this feature writes no
marker and no `synthesis-agents.json`, so the manifest section is absent
and its family reads "missing". A never-measured phase must never
project as a zero-duration one.

Quick mode commits the pipeline's own `SKIPPED` verdict without writing a
critic dispatch marker, so it produces no critic lifecycle row. Once a
critic marker exists, a missing or unusable verdict is a dispatched failure:
finalize records it as stalled, and the pipeline reports the critic as
unavailable and degrades the run. Historical `SKIPPED` rows remain readable
for metrics compatibility, but current crash handling never manufactures one.
"""

import json
import os
from datetime import datetime, timezone

try:
    from . import critic_adjustments
    from .atomic_io import atomic_write_json
except ImportError:  # pragma: no cover - direct-path import fallback
    import sys
    from pathlib import Path

    _scripts_parent = str(Path(__file__).resolve().parent.parent)
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import critic_adjustments
    from review.atomic_io import atomic_write_json


LIFECYCLE_FILENAME = "synthesis-agents.json"
LIFECYCLE_SCHEMA = 1

# The synthesis dispatch marker's suffix and its separation from reviewer markers.
#
# These markers used to share the reviewer suffix exactly, on the theory
# that one format meant one reader. The opposite was true: pirategoat-bot's
# resume path once scanned the run directory for reviewer start markers and treated every hit
# as a REVIEWER, so it seeded both synthesis agents as permanently
# NOT_DISPATCHED rows AND renamed their markers away as orphans — erasing
# the stall signal inside the exact crash window this feature exists to
# capture. Between dispatch and the next observation the marker is the
# ONLY record that the agent was ever handed off.
#
# The bot can filter two names defensively, but a hand-maintained mirror
# of our names in another repo is a contract nobody enforces; a third
# synthesis agent would reintroduce the collision silently. The suffix
# makes it structural instead — reviewer markers now live under `reviewers/`,
# synthesis markers stay at their separate lifecycle path, and no name list
# has to stay in sync.
#
# One constant, used by BOTH the writer and the reader through
# marker_path(). A writer that spelled its own suffix would produce a
# marker its own reader could not find, which reads downstream as an
# agent that never started.
MARKER_SUFFIX = ".synthesis-started"

# The row shape, declared ONCE. Three modules write it — this producer,
# manifest_sections.build_synthesis_agents_manifest(), and the metrics
# consumer's _sanitize_synthesis_agents() — and a key added to one of
# them alone is a measurement that silently never reaches the manifest or
# the cohort. Both projections assert parity against this tuple, so
# teaching only one of the three fails loudly instead of green.
ROW_KEYS = (
    "agent",
    "verdict",
    "started_at",
    "completed_at",
    "duration_ms",
    "stalled",
)

RECONCILIATOR = "review-reconciliator"
DECISION_CRITIC = "decision-reviewer"

# (agent name, completion artifact).
#
# The completion artifact is the one file the dispatching step's handoff
# gate makes mandatory, NOT the richest thing the agent might write:
#
# - review-findings.json is the reconciliator's ONLY artifact (step 8's
#   briefing says so in as many words, and its handoff verifies it).
# - decision-critic-verdict.json is required by the branch that dispatches
#   the critic. Its mtime remains the completion signal, while the verdict
#   recorded in the lifecycle row is accepted only from a complete,
#   schema-versioned, proposal-digest-bound snapshot. The quick-skip branch
#   commits SKIPPED but dispatches no critic and writes no lifecycle marker.
SYNTHESIS_AGENTS = (
    (RECONCILIATOR, "review-findings.json"),
    (DECISION_CRITIC, "decision-critic-verdict.json"),
)


def marker_path(output_dir, name):
    """Path of one synthesis agent's dispatch marker.

    The single place MARKER_SUFFIX is applied, so the writer and the
    reader cannot disagree about what file they are talking about.
    """
    return os.path.join(output_dir, f"{name}{MARKER_SUFFIX}")


def mark_dispatched(output_dir, name, *, now=None):
    """Stamp the moment the script handed `name` off for dispatch.

    Best-effort by construction: an unwritable marker costs the run a
    measurement, never the review.

    Re-stamping on a re-entered step is deliberate — the live attempt is
    the one whose duration means anything. The caller observes first so
    the completed prior attempt is measured before its artifacts are
    retired, then this newer clock prevents that completion from being
    carried into the replacement attempt.
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

    Read at observation because it changes what a duration means. For the
    critic, the verdict is usable only when its marker schema and proposal
    digest validate against the adjacent committed proposal; the marker's
    existence and mtime remain the completion signal. None is the honest
    answer for an absent, unreadable, malformed, or unbound artifact. The
    ledger is read through `read_findings_file` like every other consumer
    — a bare `json.load` here was the fifth spelling of open-parse-use for
    this one file, and the only one that would accept a document the
    pipeline itself rejects.
    """
    if os.path.basename(path) == critic_adjustments.CRITIC_VERDICT_FILENAME:
        return critic_adjustments.read_critic_verdict(
            os.path.dirname(path) or "."
        )
    read = critic_adjustments.read_findings_file(path)
    if read.status != critic_adjustments.FINDINGS_READ_OK:
        return None
    return read.findings["verdict"]


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
    still-running agent is stale by definition, and carrying it would
    hide a stall that finalize is about to adjudicate.
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


def _completed_after(entry, started_at):
    """Whether a recorded completion belongs to this dispatch clock."""
    if started_at is None:
        return False
    try:
        completed_at = datetime.fromisoformat(entry["completed_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if completed_at.tzinfo is None:
        return False
    return completed_at >= started_at


def observe(output_dir, *, finalize=False):
    """Record what is observable right now about dispatched agents.

    `finalize=True` is the only mode that adjudicates a stall: before
    finalize, an agent with no completion artifact is simply one this
    observation caught mid-flight, which says nothing about the run.

    An already-completed entry from the current dispatch is preserved
    verbatim. Re-deriving it is not merely wasted work: finalize writes
    review-findings.json, so a later reading of that artifact's mtime would
    report the reconciliator as having finished at finalize time. A newer
    dispatch marker makes the prior entry stale instead of carrying it into
    the replacement attempt.

    Returns the payload written, or None when nothing could be written.
    """
    prior = _prior_entries(output_dir)

    entries = []
    for name, artifact_name in SYNTHESIS_AGENTS:
        dispatched, started_at = _read_marker(output_dir, name)
        if not dispatched:
            # No marker: this agent was never dispatched by a build that
            # writes markers. That is an absence, not a zero — it earns
            # no row, and the section's presence is what tells a reader
            # the run was capable of measuring one.
            continue
        carried = prior.get(name)
        if carried is not None and _completed_after(carried, started_at):
            entries.append(carried)
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
            # proxy for when the agent actually finished, and the only
            # clock recorded.
            "completed_at": (
                completed_at.isoformat() if completed_at else None
            ),
            "duration_ms": _delta_ms(started_at, completed_at),
            "stalled": finalize and completed_at is None,
        })

    payload = {
        "schema": LIFECYCLE_SCHEMA,
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

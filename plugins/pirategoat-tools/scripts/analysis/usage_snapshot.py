#!/usr/bin/env python3
"""Durable token-usage snapshot for one review run.

Run measurement normally happens long after the fact, from
``review_run_metrics.py``. That is fine for cohorts but useless to the run
itself: nothing the pipeline leaves behind says what the review cost, so a
consumer reading a finished run's artifacts has to go find the session
transcripts and correlate them again — if they still exist.

This CLI closes that gap by capturing the same measurement AT FINALIZE and
writing it into the run directory as ``usage-snapshot.json``. It is a thin
projection over the existing correlation machinery
(``review_metrics.measure.measure_run`` over ``review_transcript.py``), not
a second implementation of it.

Two facts shape the output, and both are structural rather than defensive:

* Every SUBAGENT transcript is closed by the time finalize runs — the
  reviewers, the reconciliator, and the critic have all returned — so their
  usage is completely measurable and can honestly read ``complete``.
* The ORCHESTRATOR is measuring its own STILL-OPEN session. Its number is
  partial by construction and is labelled so; only a re-run over a settled
  manifest can upgrade it.

On a host that writes no Claude-format transcripts at all (Codex) there is
nothing to correlate. That produces ``missing`` with null payloads — a
RECORDED absence, which is a different fact from an older run that never
attempted the capture and therefore has no artifact at all. This gap is
KNOWN and UNSOLVED: no re-run of this CLI can measure a host that never
wrote a Claude-format transcript in the first place, and nothing here
pretends otherwise.

Re-running over a settled manifest is what upgrades a partial orchestrator
half — but two more facts make that upgrade honest rather than merely
optimistic:

* MONOTONIC. A re-run's candidate measurement is compared, half by half,
  against whatever ``usage-snapshot.json`` is already on disk. A candidate
  that would DOWNGRADE either half (fresher evidence found LESS than a
  prior run already recorded — e.g. transcripts have since rotated out)
  is discarded; the existing artifact is left byte-for-byte untouched,
  because a re-run that could not re-measure must never cost the run its
  best evidence. This guarantee is scoped to the artifact, not to the run:
  deleting ``usage-snapshot.json`` is an explicit act, and the next
  capture over an empty slate re-measures from scratch and records
  whatever it finds — including a fresh ``missing`` — per the same
  recorded-absence doctrine as every other unmeasured state here. There is
  no prior evidence to protect once the file itself is gone.
* The manifest follows, through ``ReviewTelemetry.reproject_usage()``. The
  durable run manifest projects this artifact into its own ``usage``
  section wholesale at finalize, but a manual re-run happens out of band,
  long after finalize returned — nothing else re-visits that section
  afterward. This CLI calls into telemetry's own method after resolving
  the run's snapshot (freshly written or preserved by the guard above):
  it patches ONLY the manifest's ``usage`` key and its ``availability.usage``
  companion flag, through the SAME atomic-write primitive
  ``_materialize_manifest`` uses, gated on the manifest already reading
  ``status: "complete"`` under the CURRENT schema — never on a still-running
  manifest, which is finalize's territory alone. The manifest keeps ONE
  owning module, telemetry, even with two call sites into it; this CLI
  has no authority of its own over ``run``/``dispatch``/``coverage``/etc.

Manifest reprojection is best-effort, matching every other manifest write
telemetry performs: the outcome is reported on this CLI's stdout summary
as ``manifest_reprojection: <reason>`` (``written`` / ``absent`` /
``not_settled`` / ``unsupported_schema`` / ``io_failure`` — a reason
string, not a bool, because on a settled current-schema manifest
``io_failure`` is the one outcome a human re-running by hand needs to be
able to see) and never turns into a nonzero exit or a
stderr line, unlike a failure to write ``usage-snapshot.json`` itself —
that IS this CLI's sole reason for existing, and fails loudly. The
manifest, by contrast, is a derived surface this CLI can always
regenerate on the next re-run; losing one write to it is not silent data
loss in the way losing the snapshot artifact would be.

``pipeline-result.json``'s compact ``usage`` block is a THIRD surface,
built once at step 11 from the same ``manifest_sections.build_usage_manifest``
projection — a manual re-run of this CLI does not, and cannot, revisit
it: it is step 11's own point-in-time record, not a durable artifact this
CLI update owns.

Usage:
    python3 usage_snapshot.py --output-dir <run dir> [--sessions-root <root>]
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from review_metrics.contracts import (  # noqa: E402
    DEFAULT_REGISTRY,
    DEFAULT_SESSIONS_ROOT,
    _ATOMIC_IO_CONTRACT,
    _TELEMETRY_CONTRACT,
)
from review_metrics.measure import measure_run  # noqa: E402
from review_metrics.usage import _add_usage, _empty_usage  # noqa: E402


SNAPSHOT_FILENAME = "usage-snapshot.json"
SNAPSHOT_SCHEMA = 1
RUN_CONFIG_FILENAME = "run-config.json"

# Warning codes that speak about SUBAGENT evidence specifically. The
# enrichment's own `completeness.agent_data` cannot be used for the subagent
# label here: it is ANDed with the orchestrator's `main_data_complete`, so a
# single unresolved tool call in the still-open main session would report
# fourteen fully measured, fully closed reviewer transcripts as incomplete.
# Splitting the two halves is the entire point of this artifact, so the
# subagent half is derived from the facts that are actually about subagents.
#
# Two codes are deliberately absent, both for the same reason: they are
# about a DIFFERENT evidence channel than token usage, so letting either one
# speak here would demote a fully measured subagent half on evidence that
# never concerned it.
#
# `agent_scope_evidence_missing` — a reviewer had no authoritative scope
# mapping to classify its READS against, which says nothing about whether
# its token usage was measured.
#
# `agent_transcript_unresolved_calls` — a reviewer issued a tool call whose
# paired result could not be classified, which is TOOL evidence. Usage comes
# from the messages' own `usage` records, a separate channel with its own
# guards: `agent_transcript_usage_missing` below, plus `usage_valid` /
# `usage_observed` inside the analyzer. Including it demoted this label to
# `partial` on effectively every field run, because the analyzer counted any
# call whose result shape it did not recognize — WebSearch, WebFetch, MCP —
# as unresolved, and most reviewers use WebSearch. The classifier no longer
# does that (see `_EVIDENCE_TOOL_NAMES` in `review_transcript.py`), but the
# coupling was wrong on its own terms and stays removed either way.
_SUBAGENT_EVIDENCE_WARNINGS = frozenset({
    "expected_agents_unavailable",
    "expected_agent_identity_invalid",
    "agent_dispatch_schema_gap",
    "expected_agent_uncorrelated",
    "agent_transcript_missing",
    "duplicate_transcript_ignored",
    "agent_transcript_parse_gap",
    "agent_transcript_time_gap",
    "agent_transcript_usage_missing",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> object | None:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _manifest_path(output_dir: Path) -> Path | None:
    """Locate the run manifest through the producer's own derivation.

    ``ReviewTelemetry`` owns the marker-file → log-path → manifest-path
    chain; re-deriving it here would be a second spelling of the same
    convention, and the two would drift the moment either end moves.
    """
    try:
        raw = _TELEMETRY_CONTRACT.ReviewTelemetry(str(output_dir)).manifest_path
    except (OSError, ValueError):
        return None
    return Path(raw) if isinstance(raw, str) and raw else None


def _config_session_id(output_dir: Path) -> str | None:
    config = _read_json(output_dir / RUN_CONFIG_FILENAME)
    value = config.get("session_id") if isinstance(config, dict) else None
    return value if isinstance(value, str) and value else None


def _measurement_view(
    manifest: dict, output_dir: Path, captured_at: str
) -> tuple[dict, bool]:
    """Bound a still-running manifest to the capture instant.

    Returns the view plus whether the run's own window was already closed.

    A running manifest carries no ``ended_at``, and an unbounded window
    stops at the FIRST human turn after it opens — in an interactive review
    that is the requester's next message, which would silently truncate the
    measurement to whatever ran before it. Capture time is the honest upper
    bound for a snapshot taken now, so it stands in for the missing end.

    The substitution is confined to this view; the manifest on disk is never
    touched, and the closed/open distinction is carried into the artifact so
    a reader can see which window the numbers cover.
    """
    view = copy.deepcopy(manifest)
    run = view.get("run")
    if not isinstance(run, dict):
        run = {}
        view["run"] = run
    if not isinstance(run.get("session_id"), str) or not run["session_id"]:
        # Fall back to the run's own config — the manifest records whatever
        # the pipeline knew at start(), which on some entry paths is nothing.
        fallback = _config_session_id(output_dir)
        if fallback is not None:
            run["session_id"] = fallback
    window_closed = isinstance(run.get("ended_at"), str) and bool(
        run["ended_at"]
    )
    if not window_closed:
        run["ended_at"] = captured_at
    return view, window_closed


def _sum_usage(values) -> dict[str, int] | None:
    total = _empty_usage()
    observed = False
    for value in values:
        if not _add_usage(total, value):
            return None
        observed = True
    return total if observed else None


def _unmeasured(captured_at: str, reason: str, window: dict) -> dict:
    """A recorded absence: the capture ran and found nothing to measure."""
    return {
        "schema": SNAPSHOT_SCHEMA,
        "captured_at": captured_at,
        "window": window,
        "availability": {"subagents": "missing", "orchestrator": "missing"},
        "reason": reason,
        "agents_measured": {"measured": 0, "expected": None},
        "subagent_usage": [],
        "subagent_totals": None,
        "usage_by_model": None,
        "orchestrator_usage": None,
    }


def _subagent_availability(
    measured: int, expected: object, warnings: set
) -> str:
    if measured == 0:
        return "missing"
    if (
        not isinstance(expected, int)
        or isinstance(expected, bool)
        or measured != expected
        or warnings & _SUBAGENT_EVIDENCE_WARNINGS
    ):
        return "partial"
    return "complete"


def _orchestrator_availability(
    usage: dict | None, complete: object, window_closed: bool
) -> str:
    if usage is None or not any(usage.values()):
        return "missing"
    # `window_closed` is the structural guard, not a convenience: this half
    # is only ever "complete" for a run whose own manifest recorded an end.
    # A capture-time snapshot substitutes its window end (see
    # `_measurement_view`), and a substituted bound can never warrant a
    # completeness claim about a session that is still producing turns.
    if complete is True and window_closed:
        return "complete"
    return "partial"


def _build_snapshot(
    measured_run: dict, captured_at: str, window: dict
) -> dict:
    transcript = measured_run.get("transcript")
    transcript = transcript if isinstance(transcript, dict) else {}
    if transcript.get("available") is not True:
        reason = transcript.get("reason")
        return _unmeasured(
            captured_at,
            reason if isinstance(reason, str) else "transcript_unavailable",
            window,
        )

    rows = transcript.get("agent_usage") or []
    usable = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("available") is True
        and isinstance(row.get("usage"), dict)
    ]
    subagent_totals = _sum_usage(row["usage"] for row in usable)

    # Bucketed on the DISPATCHED model, not the per-message model inside the
    # transcript: the dispatch records `claude-opus-5[1m]` where the messages
    # record plain `claude-opus-5`, and the bracketed variant is a separately
    # priced model. Bucketing on the transcript's spelling would merge them.
    #
    # The cost of that choice: a subagent that fell back mid-run would have
    # its whole total booked to the dispatched model, because the dispatch
    # result envelope carries ONE `resolvedModel` and cannot express a
    # switch. The transcript's own `usage_by_model` can — it observed
    # exactly one model per transcript across all 14 agents of the
    # 2026-08-19 field run — but it drops the priced variant tag, so it
    # cannot be the bucket key either. Pricing correctness wins: the
    # per-agent rows below keep the dispatched model beside each total, so
    # a reader who suspects a fallback can still cross-check one agent.
    by_model: dict[str, dict[str, int]] = {}
    for row in usable:
        model = row.get("model")
        key = model if isinstance(model, str) and model else "unknown"
        _add_usage(by_model.setdefault(key, _empty_usage()), row["usage"])

    correlation = transcript.get("correlation")
    correlation = correlation if isinstance(correlation, dict) else {}
    expected = correlation.get("expected_count")
    if isinstance(expected, bool) or not isinstance(expected, int):
        expected = None
    warnings = set(transcript.get("warnings") or [])

    orchestrator_usage = _sum_usage(
        (transcript.get("orchestrator_usage_by_step") or {}).values()
    )
    completeness = transcript.get("completeness")
    completeness = completeness if isinstance(completeness, dict) else {}

    return {
        "schema": SNAPSHOT_SCHEMA,
        "captured_at": captured_at,
        "window": window,
        "availability": {
            "subagents": _subagent_availability(
                len(usable), expected, warnings
            ),
            "orchestrator": _orchestrator_availability(
                orchestrator_usage,
                completeness.get("orchestrator_data"),
                window["closed"],
            ),
        },
        "reason": None,
        "agents_measured": {"measured": len(usable), "expected": expected},
        "subagent_usage": [
            {
                "agent": row["agent"],
                "model": row.get("model"),
                "usage": row["usage"],
            }
            for row in usable
        ],
        "subagent_totals": subagent_totals,
        "usage_by_model": by_model or None,
        "orchestrator_usage": orchestrator_usage,
    }


def _write_snapshot(output_dir: Path, snapshot: dict) -> bool:
    """Atomically replace the run's snapshot; never raises."""
    try:
        _ATOMIC_IO_CONTRACT.atomic_write_json(
            str(output_dir / SNAPSHOT_FILENAME), snapshot
        )
        return True
    except (OSError, TypeError, ValueError):
        return False


# Evidence quality, worst to best. A re-run's candidate is compared against
# whatever is already on disk per half (subagents, orchestrator) — never as
# one combined score, because a run can legitimately upgrade one half while
# the other stays flat, and a combined score would let that legitimate case
# trip the same guard meant for an actual regression.
_AVAILABILITY_RANK = {"missing": 0, "partial": 1, "complete": 2}


def _availability_rank(value: object) -> int:
    return _AVAILABILITY_RANK.get(value, 0)


def _is_downgrade(existing: object, candidate: object) -> bool:
    """True when `candidate` measures either half as WORSE than `existing`.

    Only compares when both sides carry a real ``availability`` mapping —
    a foreign or unreadable existing file has no evidence to protect, so
    it never blocks the candidate from being written.
    """
    if not isinstance(existing, dict) or not isinstance(candidate, dict):
        return False
    existing_avail = existing.get("availability")
    candidate_avail = candidate.get("availability")
    if not isinstance(existing_avail, dict) or not isinstance(
        candidate_avail, dict
    ):
        return False
    return any(
        _availability_rank(candidate_avail.get(half))
        < _availability_rank(existing_avail.get(half))
        for half in ("subagents", "orchestrator")
    )


def _capture(output_dir: Path, sessions_root: Path, registry: Path) -> dict:
    """Measure the run, degrading to a recorded absence rather than raising."""
    captured_at = _now()
    window = {"started_at": None, "ended_at": captured_at, "closed": False}
    manifest_path = _manifest_path(output_dir)
    manifest = _read_json(manifest_path) if manifest_path is not None else None
    if not isinstance(manifest, dict):
        return _unmeasured(captured_at, "manifest_unavailable", window)

    view, window_closed = _measurement_view(manifest, output_dir, captured_at)
    run = view.get("run") if isinstance(view.get("run"), dict) else {}
    window = {
        "started_at": run.get("started_at"),
        "ended_at": run.get("ended_at"),
        "closed": window_closed,
    }
    try:
        measured_run = measure_run(view, sessions_root, registry)
    except Exception:
        # measure_run already contains its own failures; anything escaping it
        # is an unknown defect, and a defect must not cost the run its
        # snapshot — an absence is still evidence.
        return _unmeasured(captured_at, "measurement_failed", window)
    return _build_snapshot(measured_run, captured_at, window)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture one review run's token usage into its run dir."
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--sessions-root", default=str(DEFAULT_SESSIONS_ROOT))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write the snapshot and print one line of JSON describing it."""
    args = _parser().parse_args(argv)
    output_dir = Path(args.output_dir).expanduser()
    try:
        candidate = _capture(
            output_dir,
            Path(args.sessions_root).expanduser(),
            Path(args.registry).expanduser(),
        )
    except Exception as error:  # pragma: no cover - defence in depth
        candidate = _unmeasured(
            _now(),
            f"capture_failed:{type(error).__name__}",
            {"started_at": None, "ended_at": None, "closed": False},
        )

    # MONOTONIC: never let a re-run's candidate replace better evidence
    # already on disk. A downgrade is discarded wholesale — the existing
    # artifact is reported and left byte-for-byte untouched, never merged
    # with the weaker candidate.
    existing = _read_json(output_dir / SNAPSHOT_FILENAME)
    downgrade_avoided = _is_downgrade(existing, candidate)
    snapshot = existing if downgrade_avoided else candidate

    written = False
    if not downgrade_avoided:
        if not _write_snapshot(output_dir, snapshot):
            print(
                "usage_snapshot: unable to write "
                f"{SNAPSHOT_FILENAME} into {output_dir}",
                file=sys.stderr,
            )
            return 1
        written = True

    # Bring the durable manifest's `usage` section in sync with whatever
    # is now on disk — whether this call wrote it just now or a downgrade
    # left an earlier run's snapshot in place. Best-effort, like every
    # other manifest write telemetry performs: a `False` here (no
    # settled current-schema manifest, or an I/O failure) is reported on
    # the summary line below and never turns into a nonzero exit — unlike
    # a failure to write the snapshot artifact above, which IS this CLI's
    # sole reason for existing.
    manifest_reprojection = _TELEMETRY_CONTRACT.ReviewTelemetry(
        str(output_dir)
    ).reproject_usage()

    agents = snapshot.get("agents_measured")
    if not isinstance(agents, dict):
        agents = {"measured": 0, "expected": None}
    expected = agents.get("expected")
    print(json.dumps({
        "written": written,
        "downgrade_avoided": downgrade_avoided,
        "manifest_reprojection": manifest_reprojection,
        "path": str(output_dir / SNAPSHOT_FILENAME),
        "availability": snapshot["availability"],
        "agents_measured": (
            f"{agents.get('measured', 0)}/"
            f"{expected if expected is not None else '?'}"
        ),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

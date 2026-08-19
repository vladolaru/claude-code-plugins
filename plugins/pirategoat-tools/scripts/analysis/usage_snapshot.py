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
attempted the capture and therefore has no artifact at all.

Usage:
    python3 usage_snapshot.py --output-dir <run dir> [--sessions-root <root>]
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from review_metrics.contracts import (  # noqa: E402
    DEFAULT_REGISTRY,
    DEFAULT_SESSIONS_ROOT,
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
# `agent_scope_evidence_missing` is deliberately absent: it means a reviewer
# had no authoritative scope mapping to classify its READS against, which
# says nothing about whether its token usage was measured.
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
    "agent_transcript_unresolved_calls",
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
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=str(output_dir), encoding="utf-8"
        ) as temp_file:
            temp_path = temp_file.name
            json.dump(snapshot, temp_file, indent=2, sort_keys=True)
            temp_file.flush()
        os.replace(temp_path, str(output_dir / SNAPSHOT_FILENAME))
        return True
    except (OSError, TypeError, ValueError):
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        return False


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
        snapshot = _capture(
            output_dir,
            Path(args.sessions_root).expanduser(),
            Path(args.registry).expanduser(),
        )
    except Exception as error:  # pragma: no cover - defence in depth
        snapshot = _unmeasured(
            _now(),
            f"capture_failed:{type(error).__name__}",
            {"started_at": None, "ended_at": None, "closed": False},
        )
    if not _write_snapshot(output_dir, snapshot):
        print(
            "usage_snapshot: unable to write "
            f"{SNAPSHOT_FILENAME} into {output_dir}",
            file=sys.stderr,
        )
        return 1
    agents = snapshot["agents_measured"]
    expected = agents["expected"]
    print(json.dumps({
        "written": True,
        "path": str(output_dir / SNAPSHOT_FILENAME),
        "availability": snapshot["availability"],
        "agents_measured": (
            f"{agents['measured']}/{expected if expected is not None else '?'}"
        ),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

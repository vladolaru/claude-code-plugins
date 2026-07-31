"""Manifest and legacy-JSONL discovery, lifecycle overlay, run loading."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .contracts import (
    _SUPPORTED_MANIFEST_SCHEMA_VERSION,
    _incomplete_agent_executions,
    _parse_time,
    _project_agent_lifecycle,
)
from .sanitize import (
    _lifecycle_events_are_causal,
    _nonnegative_int,
    _safe_run_id,
    _safe_scalar_map,
    _sanitize_agent_event,
    _sanitize_manifest,
    _sanitize_steps,
    _sanitize_summary,
    _sanitize_warnings,
    _strict_lifecycle_agents,
    _strict_lifecycle_event,
    _supported_manifest_envelope,
    _valid_manifest,
)


def _read_json(path: Path) -> object | None:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    # Binary line iteration so one invalid UTF-8 byte damages only its own
    # line — text-mode decoding fails while ADVANCING the iterator, outside
    # any per-line handler, and would abort the whole cohort scan.
    events: list[dict[str, Any]] = []
    try:
        with path.open("rb") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                    continue
                if isinstance(value, dict):
                    events.append(value)
    except OSError:
        pass
    return events


def _read_jsonl_strict(path: Path) -> list[dict[str, Any]] | None:
    """Read a native sibling log without skipping malformed records."""
    events: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                    return None
                if not isinstance(value, dict):
                    return None
                events.append(value)
    except (OSError, UnicodeError):
        return None
    return events


def _privacy_reduced_lifecycle_event(
    event: dict[str, Any], *, completed: bool
) -> dict[str, Any]:
    """Project a validated raw event to lifecycle measurement evidence.

    Free-string fields (verdict, domain, model_tier) and scope paths are
    withheld — fresh JSONL events may carry prose the durable sidecar never
    retained. Validated numeric measurements (durations, issue and severity
    counts, scope sizes, budget targets) are preserved: zeroing them would
    report measured zeros for work that occurred, violating the
    missing/partial-data contract.
    """
    common = {
        "schema_version": event["schema_version"],
        "run_id": event["run_id"],
        "event": event["event"],
        "timestamp": event["timestamp"],
        "agent": event["agent"],
    }
    if completed:
        return {
            **common,
            "duration_ms": event.get("duration_ms"),
            "verdict": "unavailable",
            "issue_count": event["issue_count"],
            "severities": dict(event["severities"]),
        }
    reduced = {
        **common,
        "domain": "",
        "model_tier": "",
        "scope": {
            "files": event["scope"]["files"],
            "lines": event["scope"]["lines"],
            "paths": [],
        },
    }
    if "budget_target" in event:
        reduced["budget_target"] = event["budget_target"]
    return reduced


def _project_lifecycle_revisions(
    events: list[tuple[bool, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Project same-agent save revisions without execution IDs.

    Runs the telemetry producer's own projection (via contracts) in strict
    mode, so producer and consumer can never drift: a completion matches an
    outstanding start while any remain (overlapping executions each keep
    their completion); only afterwards does a further completion replace the
    latest one as a corrected save. A completion with no preceding start
    fails the projection.
    """
    return _project_agent_lifecycle(
        (
            (completed, event["agent"], event)
            for completed, event in events
        ),
        strict=True,
    )


def _sidecar_is_lifecycle_projection_prefix(
    events: list[tuple[bool, dict[str, Any]]],
    sidecar_agents: dict[str, Any],
) -> bool:
    """Return whether the sidecar equals one raw append-prefix projection."""
    expected_started = sidecar_agents["started"]
    expected_completed = sidecar_agents["completed"]
    expected_incomplete = Counter(sidecar_agents["incomplete"])

    for end in range(len(events) + 1):
        projected = _project_lifecycle_revisions(events[:end])
        if projected is None:
            return False
        started, completed = projected
        if (
            started == expected_started
            and completed == expected_completed
            and expected_incomplete
            == Counter(event["agent"] for event in started)
            - Counter(event["agent"] for event in completed)
        ):
            return True
    return False


def _invalid_running_lifecycle_overlay(
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Fail one attempted running-log overlay closed for lifecycle only."""
    result = copy.deepcopy(manifest)
    availability = result.get("availability")
    if not isinstance(availability, dict):
        availability = {}
        result["availability"] = availability
    availability["lifecycle"] = False
    warnings = _sanitize_warnings(result.get("warnings"))
    if "running_lifecycle_overlay_invalid" not in warnings:
        warnings.append("running_lifecycle_overlay_invalid")
    result["warnings"] = warnings
    return _sanitize_manifest(result)


def _overlay_running_lifecycle(
    manifest: dict[str, Any], sibling: Path
) -> dict[str, Any]:
    """Overlay append-only lifecycle suffixes onto a valid running sidecar."""
    if manifest.get("status") != "running" or not sibling.is_file():
        return manifest
    availability = manifest.get("availability")
    if (
        isinstance(availability, dict)
        and availability.get("lifecycle") is False
    ):
        return manifest

    run = manifest.get("run")
    run_id = run.get("id") if isinstance(run, dict) else None
    started_at = run.get("started_at") if isinstance(run, dict) else None
    sidecar_agents = _strict_lifecycle_agents(
        manifest.get("agents"), run_id=run_id, status="running"
    )
    events = _read_jsonl_strict(sibling)
    if (
        type(run_id) is not str
        or _safe_run_id(run_id) is None
        or type(started_at) is not str
        or _parse_time(started_at) is None
        or sidecar_agents is None
        or not events
    ):
        return _invalid_running_lifecycle_overlay(manifest)

    first = events[0]
    if (
        type(first.get("schema_version")) is not int
        or first.get("schema_version") != _SUPPORTED_MANIFEST_SCHEMA_VERSION
        or type(first.get("run_id")) is not str
        or first.get("run_id") != run_id
        or type(first.get("event")) is not str
        or first.get("event") != "pipeline_start"
        or type(first.get("timestamp")) is not str
        or first.get("timestamp") != started_at
    ):
        return _invalid_running_lifecycle_overlay(manifest)

    raw_lifecycle: list[tuple[bool, dict[str, Any]]] = []
    last_control_plane_time: datetime | None = None
    for index, event in enumerate(events):
        event_name = event.get("event")
        timestamp = _parse_time(event.get("timestamp"))
        if (
            type(event.get("schema_version")) is not int
            or event.get("schema_version") != _SUPPORTED_MANIFEST_SCHEMA_VERSION
            or type(event.get("run_id")) is not str
            or event.get("run_id") != run_id
            or type(event_name) is not str
            or timestamp is None
            or event_name not in {
                "pipeline_start",
                "step",
                "agent_start",
                "agent_complete",
                "pipeline_end",
            }
            or (event_name == "pipeline_start" and index != 0)
            or (event_name == "pipeline_end" and index != len(events) - 1)
        ):
            return _invalid_running_lifecycle_overlay(manifest)
        if event_name in {"pipeline_start", "step", "pipeline_end"}:
            if (
                last_control_plane_time is not None
                and timestamp < last_control_plane_time
            ):
                return _invalid_running_lifecycle_overlay(manifest)
            last_control_plane_time = timestamp
        if event_name == "agent_start":
            safe = _strict_lifecycle_event(
                event, completed=False, run_id=run_id
            )
            if safe is None:
                return _invalid_running_lifecycle_overlay(manifest)
            raw_lifecycle.append((False, safe))
        elif event_name == "agent_complete":
            safe = _strict_lifecycle_event(
                event, completed=True, run_id=run_id
            )
            if safe is None:
                return _invalid_running_lifecycle_overlay(manifest)
            raw_lifecycle.append((True, safe))

    existing_started = sidecar_agents["started"]
    existing_completed = sidecar_agents["completed"]
    projected = _project_lifecycle_revisions(raw_lifecycle)
    if (
        projected is None
        or not _sidecar_is_lifecycle_projection_prefix(
            raw_lifecycle, sidecar_agents
        )
        or not _lifecycle_events_are_causal(*projected)
        or any(
            _parse_time(event["timestamp"]) < _parse_time(started_at)
            for event in (*projected[0], *projected[1])
        )
    ):
        return _invalid_running_lifecycle_overlay(manifest)

    raw_started, raw_completed = projected
    fresh_started = [
        _privacy_reduced_lifecycle_event(event, completed=False)
        for event in raw_started[len(existing_started):]
    ]
    combined_completed = [
        existing_completed[index]
        if index < len(existing_completed)
        and event == existing_completed[index]
        else _privacy_reduced_lifecycle_event(event, completed=True)
        for index, event in enumerate(raw_completed)
    ]
    if (
        not fresh_started
        and combined_completed == existing_completed
    ):
        return manifest

    result = copy.deepcopy(manifest)
    combined_started = [*existing_started, *fresh_started]
    result["agents"] = {
        "started": combined_started,
        "completed": combined_completed,
        "incomplete": _incomplete_agent_executions(
            combined_started, combined_completed
        ),
    }
    return _sanitize_manifest(result)


def _legacy_id(start: dict[str, Any], end: dict[str, Any], steps: list[dict[str, Any]]) -> str:
    run_id = _safe_run_id(start.get("run_id"))
    if run_id:
        return run_id
    pipeline = start.get("pipeline") if isinstance(start.get("pipeline"), dict) else {}
    identity = {
        "started_at": start.get("timestamp"),
        "ended_at": end.get("timestamp"),
        "session_id": pipeline.get("session_id"),
        "mode": pipeline.get("mode"),
        "steps": [step.get("step") for step in steps],
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"legacy-{digest}"


def _legacy_manifest(path: Path, *, invalid_sidecar: bool = False) -> dict[str, Any] | None:
    events = _read_jsonl(path)
    starts = [
        index
        for index, event in enumerate(events)
        if isinstance(event, dict) and event.get("event") == "pipeline_start"
    ]
    if not starts:
        return None
    # One legacy file holds one run by construction; a second pipeline_start
    # means concatenation or damage. Combining segments would assign one run
    # ID the outcomes and lifecycle of OTHER runs — corrupt even under
    # exact --run-id filtering — so the first segment ends at whichever
    # comes first: the next pipeline_start, an event stamped with a foreign
    # run ID, or the run's own pipeline_end. Stopping at the terminal event
    # matters because the tolerant reader drops malformed lines: a damaged
    # second pipeline_start would erase the start boundary and hand the
    # first run the tail's outcomes. The foreign-run-ID cut covers the
    # remaining gap — when the first run never wrote a terminal event AND
    # the next start line was dropped, the tail's own run_id stamps (which
    # every producer event carries) are what remains to reject it. That
    # includes an UNSTAMPED first run (predating run IDs): no producer
    # version mixes stamped and unstamped events within one run, so any
    # stamped event after an unstamped start is foreign by construction.
    first = starts[0]
    first_run_id = _safe_run_id(events[first].get("run_id"))
    boundary = len(events)
    for index in range(first + 1, len(events)):
        event = events[index]
        kind = event.get("event")
        event_run_id = _safe_run_id(event.get("run_id"))
        if event_run_id is not None and event_run_id != first_run_id:
            boundary = index
            break
        if kind == "pipeline_start":
            boundary = index
            break
        if kind == "pipeline_end":
            boundary = index + 1
            break
    events = events[first:boundary]
    start = events[0]
    end = events[-1] if events[-1].get("event") == "pipeline_end" else {}
    pipeline = start.get("pipeline") if isinstance(start.get("pipeline"), dict) else {}
    # Step events only — the manifest contract this adapter reproduces
    # (telemetry materializes only event=="step" into steps), and the
    # transcript stage-timeline validator rejects any other entry. A
    # pipeline_end record here made EVERY completed legacy run's timeline
    # invalid, collapsing its usage attribution to unattributed. The end
    # record's timestamp/summary flow through `end` directly.
    steps = _sanitize_steps(
        [event for event in events if event.get("event") == "step"]
    )
    started = [
        _sanitize_agent_event(event, completed=False)
        for event in events
        if event.get("event") == "agent_start"
    ]
    completed = [
        _sanitize_agent_event(event, completed=True)
        for event in events
        if event.get("event") == "agent_complete"
    ]
    safe_pipeline = _safe_scalar_map(
        pipeline,
        ("session_id", "plugin_version", "mode", "repo_path", "output_dir"),
    )
    safe_pipeline["id"] = _legacy_id(start, end, steps)
    safe_pipeline["started_at"] = (
        start.get("timestamp") if isinstance(start.get("timestamp"), str) else None
    )
    safe_pipeline["ended_at"] = (
        end.get("timestamp") if isinstance(end.get("timestamp"), str) else None
    )
    safe_pipeline["git"] = _safe_scalar_map(
        pipeline.get("git"), ("requested_range", "base_sha", "head_sha")
    )
    warnings = ["legacy_log_no_manifest"]
    if invalid_sidecar:
        warnings.append("invalid_manifest_fallback")
    summary = end.get("summary") if isinstance(end, dict) else {}
    manifest = {
        "schema_version": _nonnegative_int(start.get("schema_version")) or 1,
        "status": "complete" if end else "running",
        "run": safe_pipeline,
        "steps": steps,
        "agents": {
            "started": [event for event in started if event],
            "completed": [event for event in completed if event],
            "incomplete": [],
        },
        "dispatch": None,
        "coverage": None,
        "outcome": {"summary": _sanitize_summary(summary)},
        "availability": {
            "pipeline": True,
            "transcript": False,
            "coverage": False,
            "lifecycle": False,
        },
        "warnings": warnings,
    }
    return _sanitize_manifest(manifest)



def _canonical_manifest(manifest: dict[str, Any]) -> str:
    canonical = _sanitize_manifest(manifest)

    warnings = canonical.get("warnings")
    if isinstance(warnings, list):
        canonical["warnings"] = sorted(set(warnings))

    agents = canonical.get("agents")
    if isinstance(agents, dict) and isinstance(agents.get("incomplete"), list):
        agents["incomplete"] = sorted(agents["incomplete"])

    coverage = canonical.get("coverage")
    if isinstance(coverage, dict):
        for name in ("changed", "reviewable", "assigned", "uncovered"):
            values = coverage.get(name)
            if isinstance(values, list):
                coverage[name] = sorted(set(values))
        by_agent = coverage.get("by_agent")
        if isinstance(by_agent, dict):
            for name, values in by_agent.items():
                if isinstance(values, list):
                    by_agent[name] = sorted(set(values))
        excluded = coverage.get("excluded")
        if isinstance(excluded, list):
            coverage["excluded"] = sorted(
                excluded,
                key=lambda item: json.dumps(
                    item, sort_keys=True, separators=(",", ":")
                ),
            )

    dispatch = canonical.get("dispatch")
    if isinstance(dispatch, dict):
        reasons = dispatch.get("invalid_reason_codes")
        if isinstance(reasons, list):
            dispatch["invalid_reason_codes"] = sorted(set(reasons))
        duplicate_names = dispatch.get("duplicate_agent_names")
        if isinstance(duplicate_names, dict):
            for name, values in duplicate_names.items():
                if isinstance(values, list):
                    duplicate_names[name] = sorted(set(values))

    return json.dumps(
        canonical,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _duplicate_conflict(
    run_id: str, manifests: list[dict[str, Any]]
) -> dict[str, Any]:
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    timestamps = [
        parsed
        for manifest in manifests
        if (
            parsed := _parse_time(manifest.get("run", {}).get("started_at"))
        )
        is not None
    ]
    started_at = max(timestamps).isoformat() if timestamps else None
    return {
        "schema_version": _SUPPORTED_MANIFEST_SCHEMA_VERSION,
        "status": "duplicate_run_id_conflict",
        "run": {
            "id": f"duplicate-{digest}",
            "started_at": started_at,
            "ended_at": None,
            "git": {},
        },
        "steps": [],
        "agents": {"started": [], "completed": [], "incomplete": []},
        "dispatch": None,
        "coverage": None,
        "outcome": {"summary": {}},
        "availability": {"pipeline": False, "transcript": False, "coverage": False},
        "warnings": ["duplicate_run_id_conflict"],
    }


def _is_duplicate_conflict(manifest: object) -> bool:
    warnings = manifest.get("warnings") if isinstance(manifest, dict) else None
    return (
        isinstance(manifest, dict)
        and manifest.get("status") == "duplicate_run_id_conflict"
        and isinstance(warnings, list)
        and "duplicate_run_id_conflict" in warnings
    )


def load_runs(
    log_dir: str | Path,
    last: int | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load recent review manifests, with reduced legacy JSONL fallback."""
    root = Path(log_dir).expanduser()
    try:
        manifests = sorted(root.glob("*.manifest.json"))
        json_logs = sorted(root.glob("*.jsonl"))
    except OSError:
        return []

    loaded: list[dict[str, Any]] = []
    handled_logs: set[Path] = set()
    invalid_sidecars: set[Path] = set()
    json_log_set = set(json_logs)
    for path in manifests:
        sibling = path.with_name(path.name[: -len(".manifest.json")] + ".jsonl")
        value = _read_json(path)
        if _valid_manifest(value):
            manifest = _sanitize_manifest(value)
            loaded.append(_overlay_running_lifecycle(manifest, sibling))
            handled_logs.add(sibling)
        else:
            invalid_sidecars.add(sibling)
            if sibling not in json_log_set and _supported_manifest_envelope(value):
                loaded.append(_sanitize_manifest(value))

    for path in json_logs:
        if path in handled_logs:
            continue
        legacy = _legacy_manifest(path, invalid_sidecar=path in invalid_sidecars)
        if legacy is not None:
            loaded.append(legacy)

    by_run_id: dict[str, list[dict[str, Any]]] = {}
    for manifest in loaded:
        identifier = manifest.get("run", {}).get("id")
        if isinstance(identifier, str):
            by_run_id.setdefault(identifier, []).append(manifest)

    resolved: list[tuple[str, dict[str, Any]]] = []
    for identifier, records in sorted(by_run_id.items()):
        canonical = {_canonical_manifest(record) for record in records}
        if len(canonical) == 1:
            record = (
                json.loads(next(iter(canonical)))
                if len(records) > 1
                else records[0]
            )
        else:
            record = _duplicate_conflict(identifier, records)
        resolved.append((identifier, record))

    if run_id is not None:
        resolved = [item for item in resolved if item[0] == run_id]

    def sort_key(manifest: dict[str, Any]) -> tuple[int, float, str]:
        started = _parse_time(manifest.get("run", {}).get("started_at"))
        identifier = str(manifest.get("run", {}).get("id") or "")
        if started is None:
            return (1, 0.0, identifier)
        return (0, -started.timestamp(), identifier)

    resolved.sort(key=lambda item: sort_key(item[1]))
    if isinstance(last, int) and not isinstance(last, bool) and last > 0:
        remaining = last
        limited: list[tuple[str, dict[str, Any]]] = []
        for item in resolved:
            if _is_duplicate_conflict(item[1]):
                limited.append(item)
            elif remaining > 0:
                limited.append(item)
                remaining -= 1
        resolved = limited
    return [record for _, record in resolved]

"""Field-level sanitizers and strict validators for manifest data."""

from __future__ import annotations

import math
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from .contracts import (
    _ASSIGNED_FILES_BY_AGENT_FIELD,
    _ASSIGNED_FILES_FIELD,
    _ASSIGNMENT_FIELDS,
    _ASSIGNMENT_PATH_LIST_FIELDS,
    _CHANGED_FILES_FIELD,
    _DEPENDENCY_REFRESH_EXIT_STATUSES,
    _DEPENDENCY_REFRESH_STATUSES,
    _DERIVED_MARKDOWN_STATUSES,
    _DISPATCHED_STATUSES,
    _FIXED_WARNING_CODES,
    _FILE_EXCLUSIONS_FIELD,
    _HISTORICAL_DEPENDENCY_REFRESH_SKIP_REASONS,
    _MAX_DEPENDENCY_REFRESH_COMMANDS,
    _MAX_DIRTY_FILES,
    _MAX_WALL_TIME_MS,
    _OPTIONAL_SECTION_AVAILABILITY_KEYS,
    _PRODUCER_AGENT_NAME_RE,
    _RECONCILIATION_AGENT_FIELDS,
    _RECONCILIATION_COUNT_FIELDS,
    _RECONCILIATION_FIELDS,
    _RETAINED_CRITIC_VALUES,
    _REVIEWABLE_FILES_FIELD,
    _SAFE_RUN_ID_RE,
    _SEVERITIES,
    _SUMMARY_FIELDS,
    _SUPPORTED_DISPATCH_STATUSES,
    _SUPPORTED_MANIFEST_SCHEMA,
    _SUPPORTED_MANIFEST_STATUSES,
    _SYNTHESIS_ROW_KEYS,
    _USAGE_FIELDS,
    _USAGE_SNAPSHOT_AVAILABILITY_STATES,
    _UNASSIGNED_REVIEWABLE_FILES_FIELD,
    _WINDOWS_DRIVE_RE,
    _WORKTREE_HYGIENE_STATUSES,
    _parse_time,
)


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if 0 <= value <= 2**63 - 1 else None
    if (
        isinstance(value, float)
        and math.isfinite(value)
        and value.is_integer()
        and 0 <= value <= 2**63 - 1
    ):
        return int(value)
    return None


def _nonnegative_exact_int(value: object) -> int | None:
    if type(value) is not int:
        return None
    return value if 0 <= value <= 2**63 - 1 else None


def _safe_wall_time_ms(value: object) -> int | None:
    parsed = _nonnegative_int(value)
    return parsed if parsed is not None and parsed <= _MAX_WALL_TIME_MS else None


def _exact_statistic(value: int | float) -> int | float:
    """Demote a whole-valued float (e.g. an even-count median) to int.

    Shared by cohort.py (wall-time mean/median) and measure.py (per-run
    budget-utilization median) — one canonical spelling for "statistics.
    median/mean of a list of ints may return a float that carries no
    fractional information", so a JSON report never shows `45.0` next to
    `45` for two runs whose numbers are equally exact.
    """
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _safe_usage_snapshot_map(value: object) -> dict[str, int] | None:
    """One complete token-usage map from the durable usage-snapshot section.

    Mirrors `manifest_sections._safe_usage_map` field-for-field and
    strictness-for-strictness: all-or-nothing, because a map missing a
    field or carrying a non-integer count cannot be summed or compared,
    and filling the hole with a zero would publish a fabricated
    measurement beside real ones — the same rule the producer applies.
    Deliberately separate from `usage._safe_usage` (the transcript-usage
    family one layer downstream), which accepts integral floats; this
    sanitizer accepts at least as strictly as what its one producer,
    `manifest_sections.build_usage_manifest`, emits — the two field
    checks read identically today, but nothing pins them to stay that
    way if one changes without the other, so this is a floor, not a
    guarantee of exact parity.
    """
    if not isinstance(value, dict):
        return None
    usage: dict[str, int] = {}
    for field in _USAGE_FIELDS:
        count = value.get(field)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return None
        usage[field] = count
    return usage


def _has_unsafe_string_characters(value: str) -> bool:
    # Block terminal escapes and zero-width formatting while retaining
    # legitimate multiline prose whitespace.
    return "\x00" in value or any(
        character not in {"\n", "\t"}
        and unicodedata.category(character) in {"Cc", "Cf"}
        for character in value
    )


def _safe_string(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or _has_unsafe_string_characters(value)
    ):
        return None
    return value if len(value) <= 4096 else None


def _safe_run_id(value: object) -> str | None:
    if not isinstance(value, str) or _SAFE_RUN_ID_RE.fullmatch(value) is None:
        return None
    return value


def _safe_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if _safe_string(item) is not None]


def _strict_safe_strings(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if any(_safe_string(item) is None for item in value):
        return None
    return list(value)


def _safe_repo_read_path(value: object) -> str | None:
    path = _safe_string(value)
    if (
        path is None
        or path.startswith("/")
        or "\\" in path
        or _WINDOWS_DRIVE_RE.match(path)
        or any(
            unicodedata.category(character) in {"Cc", "Cf"}
            for character in path
        )
    ):
        return None
    segments = path.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        return None
    return path


def _strict_repo_read_paths(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    paths: list[str] = []
    for item in value:
        path = _safe_repo_read_path(item)
        if path is None:
            return None
        paths.append(path)
    return paths


def _safe_scalar_map(value: object, names: Iterable[str]) -> dict[str, Any]:
    """Copy bounded string/null fields; numeric and boolean fields are explicit."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for name in names:
        if name not in value:
            continue
        item = value.get(name)
        if item is None or (
            isinstance(item, str)
            and not _has_unsafe_string_characters(item)
            and len(item) <= 4096
        ):
            result[name] = item
    return result


def _sanitize_warnings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        code = (
            item
            if isinstance(item, str)
            else item.get("code") if isinstance(item, dict) else None
        )
        if (
            isinstance(code, str)
            and code in _FIXED_WARNING_CODES
            and code not in result
        ):
            result.append(code)
    return result


def _sanitize_run(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = _safe_scalar_map(
        value,
        (
            "id",
            "session_id",
            "plugin_version",
            "mode",
            "repo_path",
            "output_dir",
            "started_at",
            "ended_at",
        ),
    )
    git = _safe_scalar_map(
        value.get("git"), ("requested_range", "base_sha", "head_sha")
    )
    result["git"] = git
    return result


def _sanitize_steps(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        step = _safe_scalar_map(
            item,
            (
                "run_id",
                "event",
                "timestamp",
                "phase",
                "title",
            ),
        )
        for name in ("schema", "step", "duration_since_prev_ms"):
            count = _nonnegative_int(item.get(name)) if isinstance(item, dict) else None
            if count is not None:
                step[name] = count
        if not step:
            continue
        raw_args = item.get("args") if isinstance(item, dict) else None
        args: dict[str, Any] = {}
        if isinstance(raw_args, dict):
            if isinstance(raw_args.get("bot_mode"), bool):
                args["bot_mode"] = raw_args["bot_mode"]
        raw_decisions = item.get("decisions") if isinstance(item, dict) else None
        decisions: dict[str, bool] = {}
        # Decisions change what a run reports (a critic skip turns a real
        # STAND/REVISE/ESCALATE verdict into "disabled"), so they are
        # honored only on records carrying the producer's step identity —
        # every producer step event stamps event="step" and a run_id (both
        # shipped together with critic_skipped itself). A bare
        # {"step": 10, "decisions": ...} fragment in a malformed sidecar is
        # not producer evidence.
        if (
            isinstance(item, dict)
            and item.get("event") == "step"
            and _safe_run_id(item.get("run_id")) is not None
            and isinstance(raw_decisions, dict)
            and isinstance(raw_decisions.get("critic_skipped"), bool)
        ):
            decisions["critic_skipped"] = raw_decisions["critic_skipped"]
        if args:
            step["args"] = args
        if decisions:
            step["decisions"] = decisions
        result.append(step)
    return result


def _sanitize_agent_event(value: object, *, completed: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    fields = (
        "run_id",
        "event",
        "timestamp",
        "agent",
        "verdict",
    ) if completed else (
        "run_id",
        "event",
        "timestamp",
        "agent",
        "domain",
        "model_tier",
    )
    result = _safe_scalar_map(value, fields)
    agent = result.get("agent")
    if not isinstance(agent, str) or _PRODUCER_AGENT_NAME_RE.fullmatch(agent) is None:
        result.pop("agent", None)
    schema = _nonnegative_int(value.get("schema"))
    if schema is not None:
        result["schema"] = schema
    if completed:
        for name in ("duration_ms", "finding_count"):
            count = _nonnegative_int(value.get(name))
            if count is not None:
                result[name] = count
        severities = value.get("severities")
        if isinstance(severities, dict):
            safe_severities = {
                name: count
                for name in _SEVERITIES
                if (count := _nonnegative_int(severities.get(name))) is not None
            }
            result["severities"] = safe_severities
        digest = value.get("review_digest")
        if _lowercase_sha256(digest):
            result["review_digest"] = digest
    else:
        budget_target = _nonnegative_int(value.get("budget_target"))
        if budget_target is not None:
            result["budget_target"] = budget_target
        scope = value.get("scope")
        if isinstance(scope, dict):
            safe_scope: dict[str, Any] = {}
            for name in ("files", "lines"):
                count = _nonnegative_int(scope.get(name))
                if count is not None:
                    safe_scope[name] = count
            safe_scope["paths"] = [
                path
                for item in scope.get("paths", [])
                if (path := _safe_repo_read_path(item)) is not None
            ] if isinstance(scope.get("paths"), list) else []
            result["scope"] = safe_scope
    return result


def _sanitize_agents(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or any(
        not isinstance(value.get(name), list)
        for name in ("started", "completed", "incomplete")
    ):
        return None
    return {
        "started": [
            event
            for item in value["started"]
            if (event := _sanitize_agent_event(item, completed=False))
        ],
        "completed": [
            event
            for item in value["completed"]
            if (event := _sanitize_agent_event(item, completed=True))
        ],
        "incomplete": _safe_strings(value.get("incomplete")),
    }


def _bounded_event_string(value: object) -> bool:
    return (
        isinstance(value, str)
        and "\x00" not in value
        and len(value) <= 4096
    )


def _lowercase_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_lifecycle_event(
    value: object,
    *,
    completed: bool,
    run_id: str,
) -> dict[str, Any] | None:
    expected_event = "agent_complete" if completed else "agent_start"
    if (
        not isinstance(value, dict)
        or type(value.get("schema")) is not int
        or value.get("schema") != _SUPPORTED_MANIFEST_SCHEMA
        or value.get("run_id") != run_id
        or value.get("event") != expected_event
        or _parse_time(value.get("timestamp")) is None
        or type(value.get("agent")) is not str
        or _PRODUCER_AGENT_NAME_RE.fullmatch(value["agent"]) is None
    ):
        return None

    if completed:
        review_digest = value.get("review_digest")
        if (
            "duration_ms" not in value
            or (
                value.get("duration_ms") is not None
                and _nonnegative_exact_int(value.get("duration_ms")) is None
            )
            or _nonnegative_exact_int(value.get("finding_count")) is None
            or not _bounded_event_string(value.get("verdict"))
            or not isinstance(value.get("severities"), dict)
            or not _lowercase_sha256(review_digest)
        ):
            return None
        severities: dict[str, int] = {}
        for name in _SEVERITIES:
            if name not in value["severities"]:
                continue
            count = _nonnegative_exact_int(value["severities"].get(name))
            if count is None:
                return None
            severities[name] = count
        if value["finding_count"] != sum(severities.values()):
            return None
        result = {
            "schema": value["schema"],
            "run_id": value["run_id"],
            "event": value["event"],
            "timestamp": value["timestamp"],
            "agent": value["agent"],
            "duration_ms": value.get("duration_ms"),
            "verdict": value["verdict"],
            "finding_count": value["finding_count"],
            "severities": severities,
        }
        result["review_digest"] = review_digest
        return result

    scope = value.get("scope")
    if (
        not _bounded_event_string(value.get("domain"))
        or not _bounded_event_string(value.get("model_tier"))
        or not isinstance(scope, dict)
        or _nonnegative_exact_int(scope.get("files")) is None
        or _nonnegative_exact_int(scope.get("lines")) is None
    ):
        return None
    paths = _strict_repo_read_paths(scope.get("paths", []))
    if paths is None:
        return None
    budget_target = value.get("budget_target")
    if "budget_target" in value and _nonnegative_exact_int(budget_target) is None:
        return None
    result = {
        "schema": value["schema"],
        "run_id": value["run_id"],
        "event": value["event"],
        "timestamp": value["timestamp"],
        "agent": value["agent"],
        "domain": value["domain"],
        "model_tier": value["model_tier"],
        "scope": {
            "files": scope["files"],
            "lines": scope["lines"],
            "paths": paths,
        },
    }
    if "budget_target" in value:
        result["budget_target"] = budget_target
    return result


def _lifecycle_events_are_causal(
    started: list[dict[str, Any]], completed: list[dict[str, Any]]
) -> bool:
    start_times = [_parse_time(event["timestamp"]) for event in started]
    completion_times = [_parse_time(event["timestamp"]) for event in completed]
    if any(time is None for time in (*start_times, *completion_times)):
        return False

    starts_by_agent: dict[str, list[datetime]] = {}
    for event, timestamp in zip(started, start_times):
        assert timestamp is not None
        agent_starts = starts_by_agent.setdefault(event["agent"], [])
        if agent_starts and timestamp < agent_starts[-1]:
            return False
        agent_starts.append(timestamp)
    completions_by_agent: dict[str, list[datetime]] = {}
    for event, timestamp in zip(completed, completion_times):
        assert timestamp is not None
        agent_completions = completions_by_agent.setdefault(event["agent"], [])
        if agent_completions and timestamp < agent_completions[-1]:
            return False
        agent_completions.append(timestamp)
    matched_by_agent: Counter[str] = Counter()
    for event, timestamp in zip(completed, completion_times):
        assert timestamp is not None
        agent = event["agent"]
        start_index = matched_by_agent[agent]
        available_starts = starts_by_agent.get(agent, [])
        if (
            start_index >= len(available_starts)
            or available_starts[start_index] > timestamp
        ):
            return False
        matched_by_agent[agent] += 1
    return True


def _strict_lifecycle_agents(
    value: object, *, run_id: object, status: object
) -> dict[str, Any] | None:
    if (
        not isinstance(value, dict)
        or _safe_run_id(run_id) is None
        or any(
            not isinstance(value.get(name), list)
            for name in ("started", "completed", "incomplete")
        )
    ):
        return None
    incomplete = _strict_safe_strings(value["incomplete"])
    if (
        incomplete is None
        or any(
            type(name) is not str
            or _PRODUCER_AGENT_NAME_RE.fullmatch(name) is None
            for name in incomplete
        )
    ):
        return None
    started: list[dict[str, Any]] = []
    for event in value["started"]:
        safe = _strict_lifecycle_event(event, completed=False, run_id=run_id)
        if safe is None:
            return None
        started.append(safe)
    completed: list[dict[str, Any]] = []
    for event in value["completed"]:
        safe = _strict_lifecycle_event(event, completed=True, run_id=run_id)
        if safe is None:
            return None
        completed.append(safe)

    if not _lifecycle_events_are_causal(started, completed):
        return None

    starts_by_agent = Counter(event["agent"] for event in started)
    completions_by_agent = Counter(event["agent"] for event in completed)
    # The producer derives all three lists from one event stream, so this
    # identity holds for running manifests too. A violation is damaged or
    # foreign evidence, not a valid in-progress snapshot.
    if Counter(incomplete) != (starts_by_agent - completions_by_agent):
        return None
    return {
        "started": started,
        "completed": completed,
        "incomplete": incomplete,
    }


def _is_dispatched_status(value: object) -> bool:
    return isinstance(value, str) and value in _DISPATCHED_STATUSES


def _producer_declared_unusable_dispatch(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if "plan_projections" in value:
        return False
    adjustments = value.get("adjustment_counts")
    planner_available = value.get("planner_baseline_available")
    final_available = value.get("final_plan_available")
    planner_count = value.get("planner_candidate_count")
    final_count = value.get("final_dispatch_count")
    if (
        type(planner_available) is not bool
        or type(final_available) is not bool
        or value.get("comparison_available") is not False
        or type(planner_count) is not int
        or _nonnegative_int(planner_count) is None
        or type(final_count) is not int
        or _nonnegative_int(final_count) is None
        or not isinstance(adjustments, dict)
        or set(adjustments) != {"added", "removed", "unchanged"}
        or any(
            type(adjustments[name]) is not int or adjustments[name] != 0
            for name in adjustments
        )
        or value.get("agents") != {}
    ):
        return False

    duplicate_names = value.get("duplicate_agent_names")
    if not isinstance(duplicate_names, dict) or not duplicate_names:
        return False
    allowed_keys = {"planner_baseline", "final_plan"}
    if not set(duplicate_names) <= allowed_keys:
        return False
    availability_by_name = {
        "planner_baseline": planner_available,
        "final_plan": final_available,
    }
    if any(not availability_by_name[name] for name in duplicate_names):
        return False
    for names in duplicate_names.values():
        safe_names = _strict_safe_strings(names)
        if (
            not safe_names
            or safe_names != sorted(set(safe_names))
            or any(
                _PRODUCER_AGENT_NAME_RE.fullmatch(name) is None
                for name in safe_names
            )
        ):
            return False

    reasons = _strict_safe_strings(value.get("invalid_reason_codes"))
    if reasons is None or len(reasons) != len(set(reasons)):
        return False
    expected_reasons = {
        f"{name}_unavailable"
        for name, available in availability_by_name.items()
        if not available
    }
    expected_reasons.update(
        f"{name}_duplicate_agents" for name in duplicate_names
    )
    if set(reasons) != expected_reasons:
        return False
    if final_available is False and final_count != 0:
        return False
    if planner_available is False and planner_count != final_count:
        return False
    return True


def _dispatch_projection_family_failure(value: object) -> bool:
    """Recognize raw projection evidence that must fail closed family-locally."""
    if not isinstance(value, dict):
        return False
    if "plan_projections" in value:
        return True
    reasons = value.get("invalid_reason_codes")
    return isinstance(reasons, list) and any(
        type(reason) is str and reason == "dispatch_agent_set_mismatch"
        for reason in reasons
    )


def _sanitize_dispatch(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    availability_fields = (
        "planner_baseline_available",
        "final_plan_available",
        "comparison_available",
    )
    if any(type(value.get(name)) is not bool for name in availability_fields):
        return None

    planner_available = value["planner_baseline_available"]
    final_available = value["final_plan_available"]
    comparison_available = value["comparison_available"]
    invalid_reason_codes = _strict_safe_strings(value.get("invalid_reason_codes"))
    if invalid_reason_codes is None or any(
        not code.islower() or not code.replace("_", "").isalnum()
        for code in invalid_reason_codes
    ):
        return None
    agent_set_mismatch = (
        planner_available
        and final_available
        and comparison_available is False
        and invalid_reason_codes == ["dispatch_agent_set_mismatch"]
    )
    if "plan_projections" in value and not agent_set_mismatch:
        return None
    if comparison_available != (planner_available and final_available):
        if not agent_set_mismatch:
            return None

    planner_count = _nonnegative_int(value.get("planner_candidate_count"))
    final_count = _nonnegative_int(value.get("final_dispatch_count"))
    if planner_available and planner_count is None:
        return None
    if final_available and final_count is None:
        return None

    raw_adjustments = value.get("adjustment_counts")
    raw_adjustments = raw_adjustments if isinstance(raw_adjustments, dict) else {}
    adjustment_counts = {
        name: _nonnegative_int(raw_adjustments.get(name))
        for name in ("added", "removed", "unchanged")
    }

    safe_plan_projections: dict[str, dict[str, str]] | None = None
    if agent_set_mismatch:
        raw_projections = value.get("plan_projections")
        projection_names = {"planner_baseline", "final_plan"}
        if (
            not isinstance(raw_projections, dict)
            or set(raw_projections) != projection_names
            or "duplicate_agent_names" in value
            or not isinstance(raw_adjustments, dict)
            or set(raw_adjustments) != {"added", "removed", "unchanged"}
        ):
            return None
        safe_plan_projections = {}
        for projection_name in ("planner_baseline", "final_plan"):
            projection = raw_projections.get(projection_name)
            if not isinstance(projection, dict):
                return None
            safe_projection: dict[str, str] = {}
            for name, status in projection.items():
                if (
                    type(name) is not str
                    or _PRODUCER_AGENT_NAME_RE.fullmatch(name) is None
                    or type(status) is not str
                    or status not in _SUPPORTED_DISPATCH_STATUSES
                ):
                    return None
                safe_projection[name] = status
            safe_plan_projections[projection_name] = {
                name: safe_projection[name]
                for name in sorted(safe_projection)
            }
        planner_projection = safe_plan_projections["planner_baseline"]
        final_projection = safe_plan_projections["final_plan"]
        if (
            set(planner_projection) == set(final_projection)
            or planner_count
            != sum(
                _is_dispatched_status(status)
                for status in planner_projection.values()
            )
            or final_count
            != sum(
                _is_dispatched_status(status)
                for status in final_projection.values()
            )
        ):
            return None

    if (
        planner_available
        and "planner_baseline_unavailable" in invalid_reason_codes
    ) or (
        final_available and "final_plan_unavailable" in invalid_reason_codes
    ):
        return None
    if (
        "dispatch_agent_set_mismatch" in invalid_reason_codes
        and not agent_set_mismatch
    ):
        return None
    if any(code.endswith("_duplicate_agents") for code in invalid_reason_codes):
        return None

    safe_duplicate_names: dict[str, list[str]] | None = None
    duplicate_names = value.get("duplicate_agent_names")
    if "duplicate_agent_names" in value:
        if not isinstance(duplicate_names, dict):
            return None
        safe_duplicate_names = {}
        for key in ("planner_baseline", "final_plan"):
            if key not in duplicate_names:
                continue
            names = _strict_safe_strings(duplicate_names.get(key))
            if names is None or len(names) != len(set(names)):
                return None
            safe_duplicate_names[key] = names
        if any(safe_duplicate_names.values()):
            return None

    safe_agents: dict[str, dict[str, Any]] = {}
    agents = value.get("agents")
    if not isinstance(agents, dict):
        return None
    fields = (
        "domain",
        "initial_status",
        "initial_reason",
        "final_status",
        "final_reason",
        "model_tier",
        "declared_model",
        "adjustment_reason",
        "change",
    )
    for name, decision in agents.items():
        if (
            type(name) is not str
            or _PRODUCER_AGENT_NAME_RE.fullmatch(name) is None
            or not isinstance(decision, dict)
        ):
            return None
        for status_name in ("initial_status", "final_status"):
            status = decision.get(status_name)
            if (
                status_name not in decision
                or not isinstance(status, str)
                or status not in _SUPPORTED_DISPATCH_STATUSES
            ):
                return None
        safe = _safe_scalar_map(decision, fields)
        safe["planner_signals"] = _safe_strings(decision.get("planner_signals"))
        safe["configured_planner_checks"] = _safe_strings(
            decision.get("configured_planner_checks")
        )
        safe_agents[name] = safe

    if comparison_available:
        recomputed_adjustments = Counter()
        recomputed_planner_count = 0
        recomputed_final_count = 0
        for name, decision in agents.items():
            if not {"initial_status", "final_status"} <= set(decision):
                return None
            initially_dispatched = _is_dispatched_status(
                decision.get("initial_status")
            )
            finally_dispatched = _is_dispatched_status(decision.get("final_status"))
            recomputed_planner_count += initially_dispatched
            recomputed_final_count += finally_dispatched
            if initially_dispatched == finally_dispatched:
                change = "unchanged"
            elif finally_dispatched:
                change = "added"
            else:
                change = "removed"
            if decision.get("change") != change:
                return None
            recomputed_adjustments[change] += 1

        expected_adjustments = {
            name: recomputed_adjustments[name]
            for name in ("added", "removed", "unchanged")
        }
        if (
            planner_count != recomputed_planner_count
            or final_count != recomputed_final_count
            or adjustment_counts != expected_adjustments
            or sum(adjustment_counts.values()) != len(safe_agents)
            or final_count
            != planner_count
            + adjustment_counts["added"]
            - adjustment_counts["removed"]
        ):
            return None
    else:
        zero_adjustments = {"added": 0, "removed": 0, "unchanged": 0}
        if agent_set_mismatch:
            if (
                agents
                or adjustment_counts != zero_adjustments
                or safe_plan_projections is None
            ):
                return None
        elif planner_available:
            if (
                agents
                or final_count != 0
                or adjustment_counts != zero_adjustments
            ):
                return None
        elif final_available:
            dispatched_count = 0
            for decision in agents.values():
                if not {"initial_status", "final_status"} <= set(decision):
                    return None
                if (
                    decision.get("initial_status") != decision.get("final_status")
                    or decision.get("change") != "unchanged"
                ):
                    return None
                dispatched_count += _is_dispatched_status(
                    decision.get("final_status")
                )
            expected_adjustments = {
                "added": 0,
                "removed": 0,
                "unchanged": len(agents),
            }
            if (
                planner_count != dispatched_count
                or final_count != dispatched_count
                or adjustment_counts != expected_adjustments
            ):
                return None
        elif (
            agents
            or planner_count != 0
            or final_count != 0
            or adjustment_counts != zero_adjustments
        ):
            return None

    result: dict[str, Any] = {
        "planner_baseline_available": planner_available,
        "final_plan_available": final_available,
        "comparison_available": comparison_available,
        "planner_candidate_count": planner_count,
        "final_dispatch_count": final_count,
        "adjustment_counts": adjustment_counts,
        "invalid_reason_codes": invalid_reason_codes,
        "agents": safe_agents,
    }
    if safe_duplicate_names is not None:
        result["duplicate_agent_names"] = safe_duplicate_names
    if safe_plan_projections is not None:
        result["plan_projections"] = safe_plan_projections
    return result


# The per-agent reviewed-files row shape `_sanitize_coverage` requires.
# Both populations are derived from the authoritative assignment and
# validated positive claims.
_REVIEWED_FILES_FIELDS = frozenset({
    "reviewed_file_claim_count", "unclaimed_review_file_count",
})


def _sanitize_coverage(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    required = {
        *_ASSIGNMENT_FIELDS,
        "reviewed_files_by_agent",
        "review_claimable_file_count_by_agent",
        "semantics",
    }
    if not required <= set(value):
        return None
    if value.get("semantics") != "generated_scope_not_proof_of_model_read":
        return None

    path_lists: dict[str, list[str]] = {}
    for name in _ASSIGNMENT_PATH_LIST_FIELDS:
        paths = _strict_repo_read_paths(value.get(name))
        if paths is None or len(paths) != len(set(paths)):
            return None
        path_lists[name] = paths

    by_agent = value.get(_ASSIGNED_FILES_BY_AGENT_FIELD)
    if not isinstance(by_agent, dict):
        return None
    safe_by_agent: dict[str, list[str]] = {}
    for name, raw_paths in by_agent.items():
        paths = _strict_repo_read_paths(raw_paths)
        if (
            type(name) is not str
            or _PRODUCER_AGENT_NAME_RE.fullmatch(name) is None
            or paths is None
            or len(paths) != len(set(paths))
        ):
            return None
        safe_by_agent[name] = paths

    raw_excluded = value.get(_FILE_EXCLUSIONS_FIELD)
    if not isinstance(raw_excluded, list):
        return None
    file_exclusions: list[dict[str, str]] = []
    for item in raw_excluded:
        if not isinstance(item, dict) or set(item) != {"path", "reason"}:
            return None
        path = _safe_repo_read_path(item.get("path"))
        if path is None or item.get("reason") != "noise_filtered":
            return None
        file_exclusions.append({"path": path, "reason": "noise_filtered"})

    changed = set(path_lists[_CHANGED_FILES_FIELD])
    reviewable = set(path_lists[_REVIEWABLE_FILES_FIELD])
    assigned = set(path_lists[_ASSIGNED_FILES_FIELD])
    unassigned_reviewable = set(
        path_lists[_UNASSIGNED_REVIEWABLE_FILES_FIELD]
    )
    excluded_paths = [item["path"] for item in file_exclusions]
    if (
        not reviewable <= changed
        or not assigned.isdisjoint(unassigned_reviewable)
        or assigned | unassigned_reviewable != reviewable
        or len(excluded_paths) != len(set(excluded_paths))
        or set(excluded_paths) != changed - reviewable
        or any(not set(paths) <= changed for paths in safe_by_agent.values())
    ):
        return None
    by_agent_union = {
        path for paths in safe_by_agent.values() for path in paths
    }
    if by_agent_union & reviewable != assigned:
        return None

    result: dict[str, Any] = {
        _CHANGED_FILES_FIELD: path_lists[_CHANGED_FILES_FIELD],
        _REVIEWABLE_FILES_FIELD: path_lists[_REVIEWABLE_FILES_FIELD],
        _ASSIGNED_FILES_BY_AGENT_FIELD: safe_by_agent,
        _ASSIGNED_FILES_FIELD: path_lists[_ASSIGNED_FILES_FIELD],
        _FILE_EXCLUSIONS_FIELD: file_exclusions,
        _UNASSIGNED_REVIEWABLE_FILES_FIELD: path_lists[
            _UNASSIGNED_REVIEWABLE_FILES_FIELD
        ],
        "semantics": "generated_scope_not_proof_of_model_read",
    }

    raw_reviewed_files = value.get("reviewed_files_by_agent")
    if not isinstance(raw_reviewed_files, dict):
        return None
    safe_reviewed_files: dict[str, dict[str, int]] = {}
    for name, counts in raw_reviewed_files.items():
        if (
            type(name) is not str
            or _PRODUCER_AGENT_NAME_RE.fullmatch(name) is None
            or not isinstance(counts, dict)
            or set(counts) != _REVIEWED_FILES_FIELDS
        ):
            return None
        safe_counts = {
            field: _nonnegative_int(counts.get(field))
            for field in _REVIEWED_FILES_FIELDS
        }
        if any(count is None for count in safe_counts.values()):
            return None
        safe_reviewed_files[name] = safe_counts
    result["reviewed_files_by_agent"] = safe_reviewed_files

    raw_claimable_counts = value.get("review_claimable_file_count_by_agent")
    if not isinstance(raw_claimable_counts, dict):
        return None
    safe_claimable_counts: dict[str, int] = {}
    for name, count in raw_claimable_counts.items():
        safe_count = _nonnegative_int(count)
        if (
            type(name) is not str
            or _PRODUCER_AGENT_NAME_RE.fullmatch(name) is None
            or safe_count is None
        ):
            return None
        safe_claimable_counts[name] = safe_count
    result["review_claimable_file_count_by_agent"] = safe_claimable_counts

    # Reconciliation: every measured agent row requires its independently
    # sourced review-claimable count, and its reviewed-file counts must sum
    # exactly to that denominator. A row without its denominator is missing
    # evidence, never a measured zero.
    #
    # The agent's own reviewed-file counts must sum exactly to the system's
    # independently sourced review-claimable count — the identity
    # `ReviewOutputBuilder.save()` itself enforces against the very same
    # assignment this count is read from. A mismatch means the two
    # sources disagree about a fact
    # `save()` guarantees, so the section fails closed rather than
    # publish self-contradictory numbers.
    #
    # This is a COUNT checksum, not a set identity: it proves the two
    # populations add up to the right total, not that any individual file
    # landed in the right population.
    reviewed_files_by_agent = result.get("reviewed_files_by_agent")
    claimable_count_by_agent = result.get(
        "review_claimable_file_count_by_agent"
    )
    if isinstance(reviewed_files_by_agent, dict) and reviewed_files_by_agent:
        if not isinstance(claimable_count_by_agent, dict):
            return None
        if not set(reviewed_files_by_agent) <= set(claimable_count_by_agent):
            return None
        for name in reviewed_files_by_agent:
            counts = reviewed_files_by_agent[name]
            accounted = (
                counts["reviewed_file_claim_count"]
                + counts["unclaimed_review_file_count"]
            )
            if accounted != claimable_count_by_agent[name]:
                return None

    return result


def _sanitize_synthesis_agents(value: object) -> dict[str, Any] | None:
    """Sanitize the reconciliator/critic lifecycle section, or None.

    None is the "never measured" answer and covers every run older than
    the feature: no section, no rows, no zeros. A measured run with an
    empty `agents` list is a different fact — finalize looked and found
    no dispatch markers — and survives as `{"agents": []}`.

    Durations keep their None: a stalled agent has no duration, and a
    zero here would read as a phase that finished instantly.

    Parse success is the honesty mechanism — a section this function
    cannot read at all returns None and the family reads "missing",
    which is what keeps an unreadable run from being mistaken for a
    fast one.
    """
    if not isinstance(value, dict):
        return None
    raw_agents = value.get("agents")
    if not isinstance(raw_agents, list):
        return None
    rows: list[dict[str, Any]] = []
    for row in raw_agents:
        if not isinstance(row, dict):
            return None
        agent = _safe_string(row.get("agent"))
        if agent is None:
            return None
        rows.append({
            "agent": agent,
            # Historical SKIPPED rows remain readable and are excluded from
            # critique-duration statistics. Current quick-mode skips commit
            # SKIPPED without a dispatch marker, so they create no row; a
            # dispatched crash has no usable verdict and is stalled instead.
            "verdict": _safe_string(row.get("verdict")),
            "started_at": _safe_string(row.get("started_at")),
            # Artifact mtime — the closest available proxy for when the
            # agent finished, and the only clock recorded.
            "completed_at": _safe_string(row.get("completed_at")),
            "duration_ms": _nonnegative_exact_int(row.get("duration_ms")),
            "stalled": row.get("stalled") is True,
        })
    # This projection is the third writer of the row shape (after the
    # producer and the manifest builder). It vouches for what it built
    # against the producer's single declaration, so a key taught to only
    # two of the three fails loudly rather than vanishing green.
    assert all(set(row) == set(_SYNTHESIS_ROW_KEYS) for row in rows), (
        "synthesis row sanitization drifted from "
        "synthesis_lifecycle.ROW_KEYS"
    )
    return {
        "finalized": value.get("finalized") is True,
        "agents": rows,
    }


def _sanitize_worktree_hygiene(value: object) -> dict[str, Any] | None:
    """Sanitize the step-11 worktree-hygiene section, or None.

    None means the run never measured hygiene (payload absent or the
    wrong shape) — a different fact from a measured "unknown" status,
    which survives as a section like any other outcome. Every field falls
    back to the producer's own absent-measurement value rather than a
    fabricated one, mirroring
    `manifest_sections.build_worktree_hygiene_manifest`'s own fallback
    behavior: an unrecognizable status reads as "unknown", never "clean",
    and a malformed entry list reads as empty rather than invalidating
    the whole section.

    PII: none of these fields carry user-authored text. `status` is a
    three-value enum, `baseline_captured_at` is an ISO timestamp, and the
    three entry lists are `git status --porcelain` path lines from the
    repository under review — source-tree paths, not personal data.
    """
    if not isinstance(value, dict):
        return None

    def entries(key: str) -> list[str]:
        return _safe_strings(value.get(key))

    status = value.get("status")
    captured_at = value.get("baseline_captured_at")
    return {
        "status": (
            status
            if isinstance(status, str) and status in _WORKTREE_HYGIENE_STATUSES
            else "unknown"
        ),
        "new_files": entries("new_files"),
        "changed_files": entries("changed_files"),
        "probe_residue_removed": entries("probe_residue_removed"),
        "baseline_captured_at": (
            captured_at if isinstance(captured_at, str) else None
        ),
    }


def _sanitize_usage_snapshot(value: object) -> dict[str, Any] | None:
    """Sanitize the step-11 token-usage snapshot section, or None.

    None means the run never captured a snapshot — absent, unreadable, or
    the wrong shape. That is different from a captured snapshot that found
    nothing to measure (a Codex host writes no Claude-format transcripts),
    which carries per-half `availability` of "missing" and survives as a
    section like any other outcome. Mirrors
    `manifest_sections.build_usage_manifest`'s own field-by-field
    fallback: every field reads its own absent-measurement value instead
    of invalidating the whole section, because the two halves (subagent
    vs. orchestrator) are independently partial by construction.

    Divergence from the producer: `build_usage_manifest` keeps a
    `by_agent` row whenever `agent` is any string — including an empty
    one, since its only check is `isinstance(row.get("agent"), str)`.
    This sanitizer additionally requires `_safe_string`'s non-empty,
    bounded, control-character-free shape, so a row the producer wrote
    with `agent: ""` is silently dropped here instead of kept as an
    uninformative row. Deliberate and stricter, not a bug: an unnamed
    agent contributes no attributable signal to a `by_agent` breakdown.
    Pinned by
    `test_a_row_with_an_empty_agent_name_is_dropped_even_though_the_producer_would_keep_it`.

    PII: `captured_at` and the two window timestamps are ISO instants;
    `window.closed` and the two `availability` states are fixed
    three/two-value enums; `agents_measured` and every usage map are plain
    non-negative integers (token counts); `usage_by_model` keys are
    dispatched model identifiers (a fixed, non-personal vocabulary); and
    `by_agent` rows carry only a reviewer-agent name, a model identifier,
    and a usage map. None of this is user-authored or personally
    identifying text.
    """
    if not isinstance(value, dict):
        return None

    window = value.get("window")
    window = window if isinstance(window, dict) else {}
    availability = value.get("availability")
    availability = availability if isinstance(availability, dict) else {}
    counts = value.get("agents_measured")
    counts = counts if isinstance(counts, dict) else {}

    def availability_state(name: str) -> str:
        state = availability.get(name)
        return (
            state
            if isinstance(state, str) and state in _USAGE_SNAPSHOT_AVAILABILITY_STATES
            else "missing"
        )

    def window_bound(name: str) -> str | None:
        bound = window.get(name)
        return bound if isinstance(bound, str) else None

    by_model_raw = value.get("usage_by_model")
    by_model: dict[str, dict[str, int]] = {}
    if isinstance(by_model_raw, dict):
        for model, usage in by_model_raw.items():
            if not isinstance(model, str):
                continue
            safe = _safe_usage_snapshot_map(usage)
            if safe is not None:
                by_model[model] = safe

    rows_raw = value.get("by_agent")
    rows: list[dict[str, Any]] = []
    if isinstance(rows_raw, list):
        for row in rows_raw:
            if not isinstance(row, dict):
                continue
            agent = _safe_string(row.get("agent"))
            if agent is None:
                continue
            rows.append({
                "agent": agent,
                "model": _safe_string(row.get("model")),
                "usage": _safe_usage_snapshot_map(row.get("usage")),
            })

    captured_at = value.get("captured_at")
    return {
        "captured_at": captured_at if isinstance(captured_at, str) else None,
        "window": {
            "started_at": window_bound("started_at"),
            "ended_at": window_bound("ended_at"),
            "closed": window.get("closed") is True,
        },
        "availability": {
            "subagents": availability_state("subagents"),
            "orchestrator": availability_state("orchestrator"),
        },
        "agents_measured": {
            "measured": _nonnegative_exact_int(counts.get("measured")),
            "expected": _nonnegative_exact_int(counts.get("expected")),
        },
        "subagent_totals": _safe_usage_snapshot_map(value.get("subagent_totals")),
        "orchestrator_usage": _safe_usage_snapshot_map(
            value.get("orchestrator_usage")
        ),
        "usage_by_model": by_model,
        "by_agent": rows,
    }


def _sanitize_skipped_steps(value: object) -> list[dict[str, Any]] | None:
    """Sanitize the step-router skip ledger, or None.

    None (payload absent, unreadable, or the wrong shape) is distinct
    from `[]` — a run whose router measured zero skips. Only entries
    carrying a real step number survive, mirroring
    `manifest_sections.build_skipped_steps_manifest`'s own filter: an
    entry without one has no decision to report, and a title/condition
    falls back to "" at least as strictly as the producer's own
    `or ""` does. The producer's bare `or ""` keeps any truthy value
    verbatim — unbounded length, even a non-string — while this
    sanitizer additionally requires `_safe_string`'s bounded,
    control-character-free shape, so a title the producer would have
    kept as-is can still land here as "".

    PII: `step` is an integer, and `title`/`condition` are drawn from the
    pipeline's fixed step-title and skip-condition vocabulary (e.g.
    "Decision Critic", "quick_mode_enabled") — never user-authored text.
    """
    if not isinstance(value, list):
        return None

    def bounded_or_empty(raw: object) -> str:
        # `_safe_string` already returns None for any non-string input,
        # so no isinstance guard is needed before calling it.
        return _safe_string(raw) or ""

    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        step = item.get("step")
        if not isinstance(step, int) or isinstance(step, bool):
            continue
        result.append({
            "step": step,
            "title": bounded_or_empty(item.get("title")),
            "condition": bounded_or_empty(item.get("condition")),
        })
    return result


def _sanitize_dependency_refresh(value: object) -> dict[str, Any] | None:
    """Sanitize the dependency-refresh manifest section, or None.

    Current manifests carry request/report flags, optional precheck refusal
    evidence, and the validated canonical report projection. Status and exit
    vocabularies come from the live producer contract. Bounded historical
    skip/verification branches remain reader-only so old runs stay measurable;
    no current producer emits them.

    Command strings are arbitrary orchestrator-reported evidence and may
    contain repository-relative tool invocations. They are not execution
    attestation and are retained only through the ordinary bounded string
    sanitizer. Dirty-file entries are repository paths.
    """
    if not isinstance(value, dict):
        return None
    requested = value.get("requested")
    reported = value.get("reported")
    if not isinstance(requested, bool) or not isinstance(reported, bool):
        return None
    result: dict[str, Any] = {"requested": requested, "reported": reported}

    precheck = value.get("precheck")
    if isinstance(precheck, dict):
        tracked_files_dirty = precheck.get("tracked_files_dirty")
        result["precheck"] = {
            "tracked_files_dirty": (
                tracked_files_dirty
                if isinstance(tracked_files_dirty, bool) else None
            ),
            "dirty_files": _safe_strings(
                precheck.get("dirty_files")
            )[:_MAX_DIRTY_FILES],
        }

    # Historical reader branch: schema-less manifests produced before the
    # validating save channel represented precheck refusal as `skipped`.
    if value.get("skipped") is True:
        skipped_reason = value.get("skipped_reason")
        result["skipped"] = True
        if isinstance(skipped_reason, str):
            result["skipped_reason"] = (
                skipped_reason
                if skipped_reason in _HISTORICAL_DEPENDENCY_REFRESH_SKIP_REASONS
                else "invalid"
            )
        result["dirty_files"] = _safe_strings(
            value.get("dirty_files")
        )[:_MAX_DIRTY_FILES]

    # Historical reader branch: post-hoc command-policy verification was
    # retired from the live producer, but existing manifests retain evidence.
    if isinstance(value.get("verification"), dict):
        verification = value["verification"]
        commands_allowed = verification.get("commands_allowed")
        tracked_files_dirty = verification.get("tracked_files_dirty")
        result["verification"] = {
            "report_present": verification.get("report_present") is True,
            "commands_allowed": (
                commands_allowed if isinstance(commands_allowed, bool) else None
            ),
            "disallowed_commands": _safe_strings(
                verification.get("disallowed_commands")
            )[:_MAX_DEPENDENCY_REFRESH_COMMANDS],
            "tracked_files_dirty": (
                tracked_files_dirty
                if isinstance(tracked_files_dirty, bool) else None
            ),
            "verification_failed": (
                verification.get("verification_failed") is True
            ),
        }

    if reported or any(
        key in value
        for key in ("status", "tracked_files_dirty", "commands")
    ):
        status = value.get("status")
        result["status"] = (
            status
            if isinstance(status, str) and status in _DEPENDENCY_REFRESH_STATUSES
            else "invalid"
        )
        tracked_files_dirty = value.get("tracked_files_dirty")
        result["tracked_files_dirty"] = (
            tracked_files_dirty if isinstance(tracked_files_dirty, bool) else None
        )
        result["dirty_files"] = _safe_strings(
            value.get("dirty_files")
        )[:_MAX_DIRTY_FILES]
        commands_raw = value.get("commands")
        commands: list[dict[str, Any]] = []
        if isinstance(commands_raw, list):
            for entry in commands_raw[:_MAX_DEPENDENCY_REFRESH_COMMANDS]:
                if not isinstance(entry, dict):
                    continue
                exit_status = entry.get("exit_status")
                commands.append({
                    "directory": _safe_string(entry.get("directory")),
                    "command": _safe_string(entry.get("command")),
                    "exit_status": (
                        exit_status
                        if (
                            isinstance(exit_status, str)
                            and exit_status in _DEPENDENCY_REFRESH_EXIT_STATUSES
                        )
                        else "invalid"
                    ),
                })
        result["commands"] = commands
    return result


def _sanitize_derived_markdown_outcome(value: object) -> dict[str, Any] | None:
    """Sanitize one written/expected/status derived-Markdown outcome, or
    None.

    Shared by `reviewer_markdown` (step 8's per-reviewer
    `<reviewer>-review.md`) and `findings_markdown` (steps 9/11's
    `review-findings.md`) — the same written/expected/status vocabulary
    `manifest_sections._validated_derived_markdown_outcome` validates for
    both producer-side builders, reached here via
    `contracts._DERIVED_MARKDOWN_STATUSES` rather than restated, mirroring
    `_sanitize_worktree_hygiene`'s own producer-vocabulary reuse.

    The shared vocabulary is a deliberate superset for `findings_markdown`:
    its writers emit only `not_run`/`complete`/`failed`, while `partial`
    comes solely from `reviewer_markdown`'s materialization path. One
    family, one vocabulary — accepting `partial` on both is the cost of
    the one-family design `briefings.py`'s shared status line already
    established, not a flattened distinction.

    PII: none. `ran`/`status` are booleans and a closed four-value
    vocabulary; `written`/`expected` are plain non-negative file counts.
    """
    if not isinstance(value, dict):
        return None
    ran = value.get("ran")
    written = value.get("written")
    expected = value.get("expected")
    status = value.get("status")
    if (
        not isinstance(ran, bool)
        or not isinstance(written, int)
        or isinstance(written, bool)
        or written < 0
        or not isinstance(expected, int)
        or isinstance(expected, bool)
        or expected < 0
        or status not in _DERIVED_MARKDOWN_STATUSES
        or (ran and status == "not_run")
        or (not ran and status != "not_run")
        or (status == "complete" and written != expected)
    ):
        return None
    return {
        "ran": ran,
        "written": written,
        "expected": expected,
        "status": status,
    }


def _sanitize_summary(value: object) -> dict[str, Any]:
    raw_summary = value if isinstance(value, dict) else {}
    summary = _safe_scalar_map(
        raw_summary, ("pr_size_category", "final_verdict")
    )
    if isinstance(raw_summary.get("quick_mode"), bool):
        summary["quick_mode"] = raw_summary["quick_mode"]
    for name in set(_SUMMARY_FIELDS) - {
        "quick_mode",
        "pr_size_category",
        "final_verdict",
    }:
        count = _nonnegative_int(raw_summary.get(name))
        if count is not None:
            summary[name] = count
    raw_severities = raw_summary.get("final_severities")
    if isinstance(raw_severities, dict):
        summary["final_severities"] = {
            name: count
            for name in _SEVERITIES
            if (count := _nonnegative_int(raw_severities.get(name))) is not None
        }
    return summary


def _sanitize_reconciliation(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != _RECONCILIATION_FIELDS:
        return None
    result: dict[str, Any] = {}
    for name in _RECONCILIATION_COUNT_FIELDS:
        count = _nonnegative_exact_int(value.get(name))
        if count is None:
            return None
        result[name] = count
    for name in _RECONCILIATION_AGENT_FIELDS:
        agents = value.get(name)
        if agents is None:
            result[name] = None
            continue
        if not isinstance(agents, list) or any(
            type(agent) is not str
            or _PRODUCER_AGENT_NAME_RE.fullmatch(agent) is None
            for agent in agents
        ):
            return None
        result[name] = list(agents)
    not_applicable = value.get("not_applicable_agents")
    if not isinstance(not_applicable, list):
        return None
    skipped: list[dict[str, str]] = []
    for agent in not_applicable:
        if not isinstance(agent, dict) or set(agent) != {"name", "skip_reason"}:
            return None
        safe = _safe_scalar_map(agent, ("name", "skip_reason"))
        name = safe.get("name")
        if (
            type(name) is not str
            or _PRODUCER_AGENT_NAME_RE.fullmatch(name) is None
            or type(safe.get("skip_reason")) is not str
        ):
            return None
        skipped.append(safe)
    result["not_applicable_agents"] = skipped
    return result


def _sanitize_outcome(value: object) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    summary = _sanitize_summary(value.get("summary"))
    result = {
        "summary": summary,
        "reconciliation": _sanitize_reconciliation(value.get("reconciliation")),
    }
    result.update(
        _safe_scalar_map(
            value,
            (
                "pipeline_status",
                "verdict",
                "verdict_source",
            ),
        )
    )
    critic_verdict = value.get("critic_verdict")
    if isinstance(critic_verdict, str) and critic_verdict in _RETAINED_CRITIC_VALUES:
        result["critic_verdict"] = critic_verdict
    return result


# One table-driven map from each producer-declared optional section
# (`contracts._OPTIONAL_SECTION_AVAILABILITY_KEYS`, telemetry.py's own
# `OPTIONAL_SECTION_AVAILABILITY_KEYS`) to the sanitizer that projects its
# payload. Replaces what were five near-identical per-section blocks in
# `_sanitize_manifest` — including `synthesis_agents`, whose semantics
# gate lives inside `_sanitize_synthesis_agents` itself and needs no
# special-casing here.
_OPTIONAL_SECTION_SANITIZERS: dict[str, Any] = {
    "coverage": _sanitize_coverage,
    "worktree_hygiene": _sanitize_worktree_hygiene,
    "synthesis_agents": _sanitize_synthesis_agents,
    "usage": _sanitize_usage_snapshot,
    "skipped_steps": _sanitize_skipped_steps,
    "dependency_refresh": _sanitize_dependency_refresh,
    # Same sanitizer function for both: one written/expected/status
    # vocabulary, shared by the producer's own two builders.
    "reviewer_markdown": _sanitize_derived_markdown_outcome,
    "findings_markdown": _sanitize_derived_markdown_outcome,
}


def _require_complete_optional_section_sanitizers(
    keys: tuple[str, ...], sanitizers: dict[str, Any]
) -> None:
    """Fail loudly, by name, when the two sides of the table disagree.

    Structural, not conventional: a key added to the producer's
    `OPTIONAL_SECTION_AVAILABILITY_KEYS` with no matching sanitizer here
    (or the reverse — a sanitizer for a section the producer no longer
    declares) is a wiring bug. This runs at import time, so the failure
    names exactly what is missing and where to add it, instead of a bare
    `KeyError` raised from inside the per-manifest sanitize loop three
    call frames later.
    """
    missing = sorted(set(keys) - set(sanitizers))
    if missing:
        raise AssertionError(
            f"OPTIONAL_SECTION_AVAILABILITY_KEYS declares {missing} with "
            "no matching sanitizer in review_metrics/sanitize.py's "
            "_OPTIONAL_SECTION_SANITIZERS — add one before this section "
            "can be produced."
        )
    extra = sorted(set(sanitizers) - set(keys))
    if extra:
        raise AssertionError(
            f"_OPTIONAL_SECTION_SANITIZERS declares {extra}, which "
            "telemetry.py's OPTIONAL_SECTION_AVAILABILITY_KEYS does not "
            "list — remove the stale sanitizer or add the section to "
            "the producer's declared tuple."
        )


_require_complete_optional_section_sanitizers(
    _OPTIONAL_SECTION_AVAILABILITY_KEYS, _OPTIONAL_SECTION_SANITIZERS
)


def _sanitize_optional_sections(
    value: dict[str, Any], raw_availability: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, bool]]:
    """One pass over every producer-declared optional section.

    The published availability flag is DERIVED from what the section's
    sanitizer actually parsed — never copied from the raw manifest. This
    is the same "derive from what parsed" rule `_sanitize_manifest`'s
    `lifecycle` conjunct already applies (`strict_agents is not None`,
    not a raw-bool copy). A producer bug that writes
    `availability["<name>"]: true` beside a missing or unparseable
    payload therefore republishes as `false` with no payload, rather than
    reviving the exact "measured: true, payload dropped" lie this
    function exists to close.

    An explicit producer `false` still wins outright — flag-wins, the
    same precedent `coverage` and `synthesis_agents` established before
    this consolidation: a producer that measured the section absent is
    not overruled by a stray leftover payload.

    A section this manifest never declared at all — neither an
    availability key nor a payload key present — is never added to the
    output. Pre-feature manifests stay exactly as unmeasured as they
    always were; they are never promoted to a fabricated `false`.
    """
    sections: dict[str, Any] = {}
    flags: dict[str, bool] = {}
    for name in _OPTIONAL_SECTION_AVAILABILITY_KEYS:
        if name not in raw_availability and name not in value:
            continue
        if raw_availability.get(name) is False:
            sections[name] = None
            flags[name] = False
            continue
        payload = _OPTIONAL_SECTION_SANITIZERS[name](value.get(name))
        sections[name] = payload
        flags[name] = payload is not None
    return sections, flags


def _sanitize_manifest(value: object) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    run = _sanitize_run(value.get("run"))
    status = value.get("status") if isinstance(value.get("status"), str) else None
    strict_agents = _strict_lifecycle_agents(
        value.get("agents"), run_id=run.get("id"), status=status
    )
    agents = strict_agents if strict_agents is not None else _sanitize_agents(
        value.get("agents")
    )
    availability = value.get("availability")
    raw_availability = availability if isinstance(availability, dict) else {}
    safe_availability = {
        name: item
        for name, item in raw_availability.items()
        if isinstance(name, str) and isinstance(item, bool)
    }
    safe_availability["lifecycle"] = (
        strict_agents is not None
        and safe_availability.get("lifecycle") is not False
    )
    optional_sections, optional_flags = _sanitize_optional_sections(
        value, raw_availability
    )
    # The generic bool-copy above may have carried a raw flag for one of
    # these sections through verbatim; the derived value below is what
    # actually reflects the sanitized payload and always wins.
    for name in _OPTIONAL_SECTION_AVAILABILITY_KEYS:
        safe_availability.pop(name, None)
    safe_availability.update(optional_flags)

    raw_dispatch = value.get("dispatch")
    dispatch = _sanitize_dispatch(raw_dispatch)
    warnings = _sanitize_warnings(value.get("warnings"))
    if (
        dispatch is None
        and _dispatch_projection_family_failure(raw_dispatch)
        and "invalid_dispatch_projection" not in warnings
    ):
        warnings.append("invalid_dispatch_projection")
    return {
        "schema": _nonnegative_int(value.get("schema")) or 1,
        "status": status,
        "run": run,
        "steps": _sanitize_steps(value.get("steps")),
        "agents": agents,
        "dispatch": dispatch,
        "coverage": optional_sections.get("coverage"),
        "synthesis_agents": optional_sections.get("synthesis_agents"),
        "worktree_hygiene": optional_sections.get("worktree_hygiene"),
        "usage": optional_sections.get("usage"),
        "skipped_steps": optional_sections.get("skipped_steps"),
        "dependency_refresh": optional_sections.get("dependency_refresh"),
        "reviewer_markdown": optional_sections.get("reviewer_markdown"),
        "findings_markdown": optional_sections.get("findings_markdown"),
        "outcome": _sanitize_outcome(value.get("outcome")),
        "availability": safe_availability,
        "warnings": warnings,
    }


def _supported_manifest_envelope(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "schema",
        "status",
        "run",
        "steps",
        "dispatch",
        "coverage",
        "outcome",
        "availability",
    }
    if not required <= set(value):
        return False
    if type(value.get("schema")) is not int or value.get(
        "schema"
    ) != _SUPPORTED_MANIFEST_SCHEMA:
        return False
    status = value.get("status")
    if not isinstance(status, str) or status not in _SUPPORTED_MANIFEST_STATUSES:
        return False

    run = value.get("run")
    if not isinstance(run, dict) or _safe_run_id(run.get("id")) is None:
        return False
    required_run = {
        "id",
        "session_id",
        "plugin_version",
        "mode",
        "repo_path",
        "output_dir",
        "started_at",
        "ended_at",
        "git",
    }
    if not required_run <= set(run):
        return False
    if not isinstance(run.get("git"), dict):
        return False
    for name in required_run - {"id", "git"}:
        scalar = run.get(name)
        if scalar is not None and _safe_string(scalar) is None:
            return False

    steps = value.get("steps")
    outcome = value.get("outcome")
    availability = value.get("availability")
    if (
        not isinstance(steps, list)
        or any(not isinstance(step, dict) for step in steps)
        or not isinstance(outcome, dict)
        or not isinstance(outcome.get("summary"), dict)
        or not isinstance(availability, dict)
    ):
        return False
    if not all(
        type(availability.get(name)) is bool
        for name in ("pipeline", "transcript", "coverage")
    ):
        return False
    return availability["pipeline"] is True


def _valid_manifest(value: object) -> bool:
    if not _supported_manifest_envelope(value):
        return False
    assert isinstance(value, dict)
    sanitized = _sanitize_manifest(value)
    if sanitized.get("run", {}).get("id") != value["run"].get("id"):
        return False
    if len(sanitized.get("steps", [])) != len(value["steps"]):
        return False
    raw_dispatch = value.get("dispatch")
    if (
        raw_dispatch is not None
        and sanitized.get("dispatch") is None
        and not _producer_declared_unusable_dispatch(raw_dispatch)
        and not _dispatch_projection_family_failure(raw_dispatch)
    ):
        return False
    coverage_available = value["availability"]["coverage"]
    if coverage_available != isinstance(sanitized.get("coverage"), dict):
        return False
    if coverage_available is False and value.get("coverage") is not None:
        return False
    return True

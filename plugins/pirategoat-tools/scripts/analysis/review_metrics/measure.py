"""Per-run measurement: transcript enrichment and availability."""

from __future__ import annotations

import copy
import statistics
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .contracts import (
    DEFAULT_REGISTRY,
    _AVAILABILITY_FAMILIES,
    _CRITIC_VERDICTS,
    _TRANSCRIPT_FAMILIES,
    _OBSERVED_READS_SCHEMA,
    _load_exact_path_module,
    _parse_time,
)
from .sanitize import (
    _exact_statistic,
    _nonnegative_exact_int,
    _nonnegative_int,
    _safe_scalar_map,
    _safe_string,
    _safe_wall_time_ms,
    _sanitize_manifest,
    _sanitize_warnings,
    _strict_repo_read_paths,
    _strict_safe_strings,
)
from .usage import _dispatched_model, _safe_usage
from .load import _is_duplicate_conflict, _read_json


@lru_cache(maxsize=None)
def _load_transcript_module():
    # Always load the adjacent parser by exact path, like the telemetry and
    # dispatch-status contracts. An ambient `import review_transcript`
    # would pick up whatever another checkout or version already put on
    # sys.path/sys.modules in a long-lived process — an incompatible module
    # disables transcript metrics; a compatible stale one silently measures
    # with different semantics. Cached so a cohort sweep pays the exact-path
    # module execution once, not once per run.
    path = Path(__file__).resolve().parents[1] / "review_transcript.py"
    module = _load_exact_path_module(
        "review_transcript",
        path,
        "review transcript parser unavailable",
    )
    return module.enrich_run_transcript


@lru_cache(maxsize=None)
def _recognized_agents_cached(key: tuple[str, int, int]) -> frozenset[str] | None:
    value = _read_json(Path(key[0]))
    agents = value.get("agents") if isinstance(value, dict) else None
    if not isinstance(agents, dict):
        return None
    names = {
        name
        for name in agents
        if isinstance(name, str) and name and len(name) <= 256
    }
    names.update({"review-reconciliator", "decision-reviewer", "critic"})
    return frozenset(names)


def _recognized_agents(registry_path: str | Path) -> set[str] | None:
    # A cohort sweep asks for the same registry once per run; cache the
    # parsed result keyed on (path, mtime, size) so an on-disk change is
    # still picked up, and hand out a fresh set per caller.
    path = Path(registry_path).expanduser()
    try:
        stat = path.stat()
    except OSError:
        return None
    cached = _recognized_agents_cached(
        (str(path), stat.st_mtime_ns, stat.st_size)
    )
    return set(cached) if cached is not None else None


def _unavailable_transcript(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "warnings": [],
        "orchestrator_usage_by_step": None,
        "agent_usage": None,
        "usage": None,
        "tool_failures": None,
        "artifact_writes": None,
        "observed_reads": None,
    }


def _sanitize_usage_map(value: object) -> dict[str, dict[str, int]] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, dict[str, int]] = {}
    for name, raw_usage in value.items():
        usage = _safe_usage(raw_usage)
        if _safe_string(name) is None or usage is None:
            return None
        result[name] = usage
    return result


def _sanitize_agent_usage(value: object) -> list[dict[str, Any]] | None:
    """Sanitize the per-agent usage rows the report and cohort read.

    The enrichment's own `usage_by_model` (per-MESSAGE model spellings,
    one map per agent) is deliberately not carried through. Nothing in
    this layer reads it: the cohort groups on `model`, the dispatched
    spelling, and so does `_model_usage_availability`. It stays in
    `review_transcript.py`'s output, where it has forensic value — it is
    the only surface that can show an agent switching models mid-run,
    which the dispatch envelope's single `resolvedModel` cannot express.

    Dropping it from the sanitized rows is a `_REPORT_SCHEMA` change made
    under the Artifact Schemas rule's unreleased-version carve-out:
    schema 2 was introduced in 1.114.0 and 1.114.0 is not tagged, so no
    published report ever claimed 2 with this key.
    """
    if not isinstance(value, list):
        return None
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("available"), bool):
            return None
        agent = _safe_string(item.get("agent"))
        if agent is None:
            return None
        safe: dict[str, Any] = {
            "agent": agent,
            "available": item["available"],
        }
        for name in ("agent_id", "model"):
            scalar = item.get(name)
            if scalar is None:
                safe[name] = None
            elif (clean := _safe_string(scalar)) is not None:
                safe[name] = clean
            else:
                return None
        if item["available"]:
            usage = _safe_usage(item.get("usage"))
            tool_calls = _nonnegative_exact_int(item.get("tool_calls"))
            if usage is None or tool_calls is None:
                return None
            safe["usage"] = usage
            safe["tool_calls"] = tool_calls
        else:
            safe["usage"] = None
            safe["tool_calls"] = None
        result.append(safe)
    return result


def _sanitize_tool_failures(value: object) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    fields = (
        "actor",
        "category",
        "detector",
        "tool",
        "operation_class",
        "normalized_target",
        "recovery",
    )
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("recovered"), bool):
            return None
        safe = _safe_scalar_map(item, fields)
        if any(name in item and name not in safe for name in fields):
            return None
        safe["recovered"] = item["recovered"]
        result.append(safe)
    return result


def _sanitize_artifact_agent(
    value: object,
) -> tuple[dict[str, Any], bool] | None:
    if not isinstance(value, dict):
        return None
    agent = _safe_string(value.get("agent"))
    if agent is None or not isinstance(value.get("builder_attempted"), bool):
        return None
    result: dict[str, Any] = {
        "agent": agent,
        "builder_attempted": value["builder_attempted"],
    }
    for name in (
        "builder_attempts",
        "builder_successes",
        "builder_failures",
    ):
        count = _nonnegative_exact_int(value.get(name))
        if count is None:
            return None
        result[name] = count
    first = value.get("first_builder_attempt_succeeded")
    if first is not None and not isinstance(first, bool):
        return None
    if not isinstance(value.get("recovered"), bool):
        return None
    result["first_builder_attempt_succeeded"] = first
    result["recovered"] = value["recovered"]
    if not _valid_builder_attempt_counts(result):
        return None
    return result, _builder_attempt_evidence_complete(result)


def _valid_builder_attempt_counts(value: dict[str, Any]) -> bool:
    attempted = value["builder_attempted"]
    attempts = value["builder_attempts"]
    successes = value["builder_successes"]
    failures = value["builder_failures"]
    first = value.get("first_builder_attempt_succeeded")
    recovered = value["recovered"]

    if attempted is False:
        return (
            attempts == successes == failures == 0
            and first is None
            and recovered is False
        )
    if attempts == 0:
        return False
    if successes + failures > attempts:
        return False
    if first is True and successes == 0:
        return False
    if first is False and failures == 0:
        return False
    if recovered and (successes == 0 or failures == 0):
        return False
    if first is False and successes > 0 and recovered is False:
        return False
    return True


def _builder_attempt_evidence_complete(value: dict[str, Any]) -> bool:
    if value["builder_attempted"] is False:
        return True
    return (
        isinstance(value.get("first_builder_attempt_succeeded"), bool)
        and value["builder_successes"] + value["builder_failures"]
        == value["builder_attempts"]
    )


def _sanitize_artifacts(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    for name in ("available", "complete", "recovered"):
        if not isinstance(value.get(name), bool):
            return None
    attempted = value.get("builder_attempted")
    if attempted is not None and not isinstance(attempted, bool):
        return None
    counts: dict[str, int] = {}
    for name in (
        "builder_attempts",
        "builder_successes",
        "builder_failures",
    ):
        count = _nonnegative_exact_int(value.get(name))
        if count is None:
            return None
        counts[name] = count
    raw_by_agent = value.get("by_agent")
    if not isinstance(raw_by_agent, list):
        return None
    by_agent: list[dict[str, Any]] = []
    by_agent_evidence_complete = True
    for item in raw_by_agent:
        sanitized = _sanitize_artifact_agent(item)
        if sanitized is None:
            return None
        safe, evidence_complete = sanitized
        by_agent.append(safe)
        by_agent_evidence_complete = (
            by_agent_evidence_complete and evidence_complete
        )
    first = value.get("first_builder_attempt_succeeded")
    if first is not None and not isinstance(first, bool):
        return None
    if by_agent:
        expected = {
            "builder_attempted": any(item["builder_attempted"] for item in by_agent),
            "builder_attempts": sum(
                item["builder_attempts"] for item in by_agent
            ),
            "builder_successes": sum(
                item["builder_successes"] for item in by_agent
            ),
            "builder_failures": sum(
                item["builder_failures"] for item in by_agent
            ),
            "recovered": any(item["recovered"] for item in by_agent),
        }
        attempted_matches = attempted == expected["builder_attempted"]
        if (
            attempted is None
            and value["complete"] is False
            and expected["builder_attempted"] is False
        ):
            attempted_matches = True
        if not attempted_matches or any(
            counts[name] != expected[name] for name in counts
        ) or value["recovered"] != expected["recovered"]:
            return None
        # The producer emits this list in dispatch order, not global call order.
        # Per-agent first results remain valid, but cannot establish a run-wide first.
        first = None

    aggregate_attempt = {
        "builder_attempted": attempted,
        **counts,
        "first_builder_attempt_succeeded": first,
        "recovered": value["recovered"],
    }
    if attempted is not None and not _valid_builder_attempt_counts(aggregate_attempt):
        return None
    if attempted is None and (
        any(counts.values())
        or first is not None
        or value["recovered"] is True
        or value["complete"] is True
        or any(item["builder_attempted"] for item in by_agent)
    ):
        return None

    evidence_complete = (
        by_agent_evidence_complete
        if by_agent
        else attempted is not None
        and _builder_attempt_evidence_complete(aggregate_attempt)
    )
    complete = value["complete"] and evidence_complete
    if value["complete"] and value["available"] is not True:
        return None
    if value["available"] is False and (
        attempted is not None
        or any(counts.values())
        or first is not None
        or value["recovered"] is True
        or by_agent
    ):
        return None
    return {
        "available": value["available"],
        "complete": complete,
        "builder_attempted": attempted,
        **counts,
        "first_builder_attempt_succeeded": first,
        "recovered": value["recovered"],
        "by_agent": by_agent,
    }


def _sanitize_reads(
    value: object,
    *,
    family_complete: object,
    scope_complete: object,
    non_scope_complete: object,
) -> dict[str, Any] | None:
    if not isinstance(value, dict) or any(
        type(item) is not bool
        for item in (family_complete, scope_complete, non_scope_complete)
    ) or type(value.get("schema")) is not int or value.get(
        "schema"
    ) != _OBSERVED_READS_SCHEMA:
        return None
    result: dict[str, Any] = {
        "schema": _OBSERVED_READS_SCHEMA
    }
    for name in (
        "all",
        "in_scope",
        "out_of_scope",
        "non_scope_comparable",
    ):
        paths = _strict_repo_read_paths(value.get(name))
        if paths is None or len(paths) != len(set(paths)):
            return None
        result[name] = paths
    if value.get("exhaustive") is not False:
        return None
    transcript_complete = value.get("transcript_data_complete")
    scope_transcript_complete = value.get(
        "scope_comparable_transcript_data_complete"
    )
    non_scope_transcript_complete = value.get(
        "non_scope_comparable_transcript_data_complete"
    )
    if (
        type(transcript_complete) is not bool
        or transcript_complete != family_complete
        or type(scope_transcript_complete) is not bool
        or scope_transcript_complete != scope_complete
        or type(non_scope_transcript_complete) is not bool
        or non_scope_transcript_complete != non_scope_complete
        or transcript_complete != (
            scope_transcript_complete and non_scope_transcript_complete
        )
    ):
        return None
    in_scope = set(result["in_scope"])
    out_of_scope = set(result["out_of_scope"])
    if not in_scope.isdisjoint(out_of_scope) or in_scope | out_of_scope != set(
        result["all"]
    ):
        return None
    result["exhaustive"] = False
    result["scope_comparable_transcript_data_complete"] = (
        scope_transcript_complete
    )
    result["non_scope_comparable_transcript_data_complete"] = (
        non_scope_transcript_complete
    )
    result["transcript_data_complete"] = transcript_complete
    return result


def _sanitize_count_map(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, int] = {}
    for name, raw_count in value.items():
        count = _nonnegative_int(raw_count)
        if _safe_string(name) is None or count is None:
            return None
        result[name] = count
    return result


def _sanitize_correlation(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for name in ("expected_available", "complete"):
        if isinstance(value.get(name), bool):
            result[name] = value[name]
    for name in ("expected", "correlated", "missing", "missing_transcripts"):
        items = _strict_safe_strings(value.get(name))
        if items is not None:
            result[name] = items
    for name in ("expected_by_agent", "correlated_by_agent", "missing_by_agent"):
        counts = _sanitize_count_map(value.get(name))
        if counts is not None:
            result[name] = counts
    for name in ("expected_count", "correlated_count", "missing_count"):
        count = _nonnegative_int(value.get(name))
        if count is not None:
            result[name] = count
    return result


def _sanitize_transcript(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("available") is not True:
        reason = (
            value.get("reason")
            if isinstance(value, dict) and _safe_string(value.get("reason"))
            else "transcript_unavailable"
        )
        result = _unavailable_transcript(reason)
        result["warnings"] = (
            _sanitize_warnings(value.get("warnings")) if isinstance(value, dict) else []
        )
        return result

    completeness_value = value.get("completeness")
    completeness: dict[str, bool] = {}
    if isinstance(completeness_value, dict):
        for name in (
            "orchestrator_data",
            "agent_data",
            "usage",
            "tool_failures",
            "artifact_writes",
            "scope_comparable_reads",
            "non_scope_comparable_reads",
            "observed_reads",
        ):
            if isinstance(completeness_value.get(name), bool):
                completeness[name] = completeness_value[name]

    raw_artifacts = value.get("artifact_writes")
    artifacts = _sanitize_artifacts(raw_artifacts)
    if artifacts is not None:
        raw_complete = (
            raw_artifacts.get("complete")
            if isinstance(raw_artifacts, dict)
            else None
        )
        reported_complete = completeness.get("artifact_writes")
        if reported_complete is not None and reported_complete != raw_complete:
            artifacts = None
        elif raw_complete is True and artifacts["complete"] is False:
            completeness["artifact_writes"] = False

    return {
        "available": True,
        "reason": None,
        "warnings": _sanitize_warnings(value.get("warnings")),
        "correlation": _sanitize_correlation(value.get("correlation")),
        "completeness": completeness,
        "orchestrator_usage_by_step": _sanitize_usage_map(
            value.get("orchestrator_usage_by_step")
        ),
        "agent_usage": _sanitize_agent_usage(value.get("agent_usage")),
        "usage": _safe_usage(value.get("usage")),
        "tool_failures": _sanitize_tool_failures(value.get("tool_failures")),
        "artifact_writes": artifacts,
        "observed_reads": _sanitize_reads(
            value.get("observed_reads"),
            family_complete=completeness.get("observed_reads"),
            scope_complete=completeness.get("scope_comparable_reads"),
            non_scope_complete=completeness.get(
                "non_scope_comparable_reads"
            ),
        ),
    }


def _wall_time(manifest: dict[str, Any]) -> int | None:
    run = manifest.get("run", {})
    started = _parse_time(run.get("started_at")) if isinstance(run, dict) else None
    ended = _parse_time(run.get("ended_at")) if isinstance(run, dict) else None
    if started is not None and ended is not None:
        if ended < started:
            return None
        elapsed = ended - started
        timestamp_duration = (
            elapsed.days * 24 * 60 * 60 * 1000
            + elapsed.seconds * 1000
            + elapsed.microseconds // 1000
        )
        return _safe_wall_time_ms(timestamp_duration)
    outcome = manifest.get("outcome")
    summary = outcome.get("summary") if isinstance(outcome, dict) else None
    return (
        _safe_wall_time_ms(summary.get("total_duration_ms"))
        if isinstance(summary, dict)
        else None
    )


def _lifecycle_summary(manifest: dict[str, Any]) -> dict[str, Any] | None:
    availability = manifest.get("availability")
    agents = manifest.get("agents")
    if (
        not isinstance(availability, dict)
        or availability.get("lifecycle") is not True
        or not isinstance(agents, dict)
    ):
        return None
    started = agents.get("started")
    completed = agents.get("completed")
    incomplete = agents.get("incomplete")
    if not all(isinstance(items, list) for items in (started, completed, incomplete)):
        return None
    # `started`/`completed` are the manifest's PROJECTED lifecycle arrays,
    # never the raw JSONL. Current completions mean immutable reviewer
    # finalizations. The shared projection retains last-wins support for
    # pre-finalization history, where one execution could log multiple
    # `agent_complete` save revisions; retry executions still keep one row
    # per start. Every count below is therefore an EXECUTION count.
    #
    # The `*_events` names are historical and now inaccurate in both
    # directions: as event counts they understate (re-saves are already
    # collapsed), as agent counts they overstate (retries add rows).
    # Reading them as executions is the only correct reading. Do not count
    # raw historical log lines, where save revisions inflated completions.
    starts_by_agent = Counter(event["agent"] for event in started)
    incomplete_by_agent = Counter(incomplete)
    extra_starts_by_agent = {
        name: max(count - 1, 0)
        for name, count in sorted(starts_by_agent.items())
    }
    return {
        "started_events": len(started),
        "completed_events": len(completed),
        "incomplete_identities": sorted(incomplete_by_agent),
        "incomplete_count": sum(incomplete_by_agent.values()),
        "incomplete_by_agent": dict(sorted(incomplete_by_agent.items())),
        "starts_by_agent": dict(sorted(starts_by_agent.items())),
        "extra_starts_by_agent": extra_starts_by_agent,
        "retry_overhead": sum(extra_starts_by_agent.values()),
        "completion_gap": len(started) - len(completed),
    }


def _pipeline_metric_availability(
    manifest: dict[str, Any], lifecycle: object
) -> dict[str, str]:
    dispatch = manifest.get("dispatch")
    if isinstance(dispatch, dict) and dispatch.get("comparison_available") is True:
        dispatch_state = "complete"
    elif isinstance(dispatch, dict) and (
        dispatch.get("planner_baseline_available") is True
        or dispatch.get("final_plan_available") is True
    ):
        dispatch_state = "partial"
    else:
        dispatch_state = "missing"

    coverage = manifest.get("coverage")
    manifest_availability = manifest.get("availability")
    coverage_available = isinstance(coverage, dict) and not (
        isinstance(manifest_availability, dict)
        and manifest_availability.get("coverage") is False
    )
    if coverage_available and manifest.get("status") == "complete":
        coverage_state = "complete"
    elif coverage_available and manifest.get("status") == "running":
        coverage_state = "partial"
    else:
        coverage_state = "missing"
    outcome = manifest.get("outcome")
    summary = outcome.get("summary") if isinstance(outcome, dict) else None
    # Manifest status is the single completeness authority: numeric
    # summary totals under status=running are a snapshot (or a stale
    # prior terminal summary an interactive rerun materialized over),
    # never a completed observation.
    manifest_complete = manifest.get("status") == "complete"
    raw_state = (
        ("complete" if manifest_complete else "partial")
        if isinstance(summary, dict)
        and _nonnegative_int(summary.get("total_agent_issues")) is not None
        else "missing"
    )
    final_state = (
        ("complete" if manifest_complete else "partial")
        if isinstance(summary, dict)
        and _nonnegative_int(summary.get("final_issues")) is not None
        else "missing"
    )
    if raw_state == final_state == "complete":
        outcomes_state = "complete"
    elif {"complete", "partial"} & {raw_state, final_state}:
        outcomes_state = "partial"
    else:
        outcomes_state = "missing"
    steps = manifest.get("steps")
    # The producer's skip decision is latest-wins: a step-10 rerun (after
    # the review verdict escalates past quick-mode approve/comment) clears
    # the stale decision and appends a fresh step-10 event without one, but
    # the append-only telemetry keeps both events. Only the final step-10
    # event's decision is authoritative — any() would resurrect the
    # superseded skip and report "disabled" over a real critic verdict.
    # Participation requires the producer step identity for THIS run
    # (event="step" + matching run_id): a malformed {"step": 10} fragment
    # or a foreign run's step-10 event must neither set nor RESET the
    # decision — resetting would turn a deliberate skip into missing
    # critic evidence.
    run_id = manifest.get("run", {}).get("id") if isinstance(
        manifest.get("run"), dict
    ) else None
    critic_skipped = False
    if isinstance(steps, list):
        for step in steps:
            if (
                not isinstance(step, dict)
                or step.get("step") != 10
                or step.get("event") != "step"
                or step.get("run_id") != run_id
            ):
                continue
            decisions = step.get("decisions")
            critic_skipped = (
                isinstance(decisions, dict)
                and decisions.get("critic_skipped") is True
            )
    critic_verdict = outcome.get("critic_verdict") if isinstance(outcome, dict) else None
    if critic_skipped:
        critic_state = "disabled"
    elif critic_verdict in _CRITIC_VERDICTS:
        critic_state = "complete"
    else:
        critic_state = "missing"
    wall_state = "complete" if _wall_time(manifest) is not None else "missing"
    if isinstance(lifecycle, dict):
        lifecycle_state = (
            "complete" if manifest.get("status") == "complete" else "partial"
        )
    else:
        lifecycle_state = "missing"
    # Synthesis-agent lifecycle. "missing" is the pre-feature answer and
    # the only one that must never be confused with a measured zero: a run
    # whose manifest carries no `synthesis_agents` section did not measure
    # a fast reconciliator, it measured nothing. "partial" is the measured
    # answer with a hole in it — an agent whose completion artifact never
    # appeared (a stall) or whose marker timestamp was unreadable, so its
    # phase has no duration even though the run recorded its dispatch.
    synthesis = manifest.get("synthesis_agents")
    if not isinstance(synthesis, dict):
        synthesis_state = "missing"
    else:
        rows = synthesis.get("agents")
        rows = rows if isinstance(rows, list) else []
        synthesis_state = (
            "complete"
            if all(
                isinstance(row, dict) and row.get("duration_ms") is not None
                for row in rows
            )
            else "partial"
        )
    return {
        "dispatch": dispatch_state,
        "coverage": coverage_state,
        "lifecycle": lifecycle_state,
        "synthesis_agents": synthesis_state,
        "outcomes": outcomes_state,
        "raw_findings": raw_state,
        "final_findings": final_state,
        "critic": critic_state,
        "wall_time": wall_state,
    }


def _model_usage_availability(
    completeness: dict[str, Any], agent_usage: object
) -> str:
    """Classify how much of the model-grouped view is actually attributable.

    The cohort's `by_model` grouping buckets each available agent entry's
    `usage` under that entry's `model` — the dispatch envelope's
    `resolvedModel`, the one spelling that keeps the priced
    context-window variant tag (see `cohort._group_usage`). So this gate
    must certify THAT field: an entry without a model lands in the
    grouping's explicit "unknown" bucket, contributing spend with no
    model to attribute it to.

    It previously certified a conservation identity over the per-agent
    `usage_by_model` map instead — a different field, on the bare
    per-message spelling the grouping no longer reads. That let a run
    with no `resolvedModel` anywhere read `complete` while the grouping
    it vouched for emitted `{"unknown": everything}`.

    Empty-and-complete stays complete, matching `family_state`'s rule for
    every sibling family: a run with no available agents has nothing to
    attribute, which is an authoritative zero rather than an absence.
    """
    if not isinstance(agent_usage, list):
        return "missing"
    entries = [
        item
        for item in agent_usage
        if isinstance(item, dict) and item.get("available") is True
    ]
    unattributed = [
        item for item in entries if _dispatched_model(item) is None
    ]
    if completeness.get("agent_data") is True and not unattributed:
        return "complete"
    if len(unattributed) < len(entries):
        return "partial"
    return "missing"


def _transcript_metric_availability(
    transcript: dict[str, Any], *, disabled: bool
) -> dict[str, str]:
    if disabled:
        return {name: "disabled" for name in _TRANSCRIPT_FAMILIES}
    if transcript.get("available") is not True:
        return {name: "missing" for name in _TRANSCRIPT_FAMILIES}
    completeness = transcript.get("completeness")
    completeness = completeness if isinstance(completeness, dict) else {}

    def family_state(
        flag: str, payload: object, *, observed: bool
    ) -> str:
        if completeness.get(flag) is True and payload is not None:
            return "complete"
        if completeness.get(flag) is False and payload is not None and observed:
            return "partial"
        return "missing"

    usage = transcript.get("usage")
    usage_observed = isinstance(usage, dict) and any(
        isinstance(value, int) and value > 0 for value in usage.values()
    )
    orchestrator = transcript.get("orchestrator_usage_by_step")
    orchestrator_observed = isinstance(orchestrator, dict) and bool(orchestrator)
    agent_usage = transcript.get("agent_usage")
    agent_observed = isinstance(agent_usage, list) and any(
        isinstance(item, dict) and item.get("available") is True
        for item in agent_usage
    )
    model_state = _model_usage_availability(completeness, agent_usage)
    failures = transcript.get("tool_failures")
    failures_observed = isinstance(failures, list) and bool(failures)
    artifacts = transcript.get("artifact_writes")
    artifacts_observed = isinstance(artifacts, dict) and (
        bool(artifacts.get("by_agent"))
        or isinstance(artifacts.get("builder_attempted"), bool)
        or (_nonnegative_int(artifacts.get("builder_attempts")) or 0) > 0
    )
    reads = transcript.get("observed_reads")
    scope_reads_observed = isinstance(reads, dict) and any(
        isinstance(reads.get(name), list) and bool(reads[name])
        for name in ("all", "in_scope", "out_of_scope")
    )
    non_scope_reads_observed = (
        isinstance(reads, dict)
        and isinstance(reads.get("non_scope_comparable"), list)
        and bool(reads["non_scope_comparable"])
    )
    reads_observed = scope_reads_observed or non_scope_reads_observed
    result = {
        "usage": family_state(
            "usage", usage, observed=usage_observed
        ),
        "orchestrator_usage": family_state(
            "orchestrator_data",
            orchestrator,
            observed=orchestrator_observed,
        ),
        "agent_usage": family_state(
            "agent_data", agent_usage, observed=agent_observed
        ),
        "model_usage": model_state,
        "tool_failures": family_state(
            "tool_failures", failures, observed=failures_observed
        ),
        "artifact_writes": family_state(
            "artifact_writes", artifacts, observed=artifacts_observed
        ),
        "scope_comparable_reads": family_state(
            "scope_comparable_reads",
            reads,
            observed=scope_reads_observed,
        ),
        "non_scope_comparable_reads": family_state(
            "non_scope_comparable_reads",
            reads,
            observed=non_scope_reads_observed,
        ),
        "observed_reads": family_state(
            "observed_reads", reads, observed=reads_observed
        ),
    }
    if all(state == "complete" for state in result.values()):
        result["transcript"] = "complete"
    elif any(state in {"complete", "partial"} for state in result.values()):
        result["transcript"] = "partial"
    else:
        result["transcript"] = "missing"
    return result


def _budget_target_by_agent(agents: object) -> dict[str, int]:
    """First-seen ``budget_target`` per agent from the sanitized lifecycle.

    First occurrence wins: a retried agent's budget does not move mid-run,
    and the earliest dispatch is the one execution whose target the
    reviewer actually saw in its own bootstrap prompt. `measured["agents"]`
    is already the output of `_sanitize_manifest` by the time this runs, so
    `budget_target` here is either a validated positive int or absent —
    never a value this function has to re-validate itself.
    """
    started = agents.get("started") if isinstance(agents, dict) else None
    result: dict[str, int] = {}
    if not isinstance(started, list):
        return result
    for entry in started:
        if not isinstance(entry, dict):
            continue
        agent = entry.get("agent")
        target = entry.get("budget_target")
        if (
            isinstance(agent, str)
            and agent not in result
            and isinstance(target, int)
            and not isinstance(target, bool)
            and target > 0
        ):
            result[agent] = target
    return result


def _budget_utilization(measured: dict[str, Any]) -> dict[str, Any] | None:
    """Per-agent ``tool_calls / budget_target``, plus the run's own median/range.

    Joins two facts already computed elsewhere in this same measured view
    rather than opening any new data source: `budget_target` from the
    manifest's projected agent-start lifecycle (`agent_start`'s existing
    telemetry key — see telemetry.py:139/364-388), and `tool_calls` from
    the transcript enrichment's per-agent usage rows (already counted by
    `review_transcript.py`'s `_analyze_entries`). Neither fact needed a
    new transport; this function is the first thing to read them
    together.

    None — never an empty or zeroed report — when no agent has both a
    known target and observed (`available: True`) tool-call evidence:
    disabled/missing transcripts, a pre-budget-telemetry manifest, or a
    run whose only agents are all still unmeasured on one side or the
    other. Same "absence, not a zero" doctrine as every other family in
    this module.

    First-VALID-occurrence-wins per agent name, same rule as
    `_budget_target_by_agent` and for the same reason: `agent_usage` is
    keyed per CORRELATED DISPATCH, not per agent identity —
    `review_transcript.py`'s own correlation comment says retries remain
    visible as separate entries — so a retried agent can appear twice
    with two different `tool_calls` counts. Without a dedup here, that
    agent would emit two rows and double-weight `median_pct`/`min_pct`/
    `max_pct`/`sample_count` against every agent that ran once. The
    first entry with usable evidence (`available: True` and a real
    `tool_calls` int) is kept; a later retry's entry for the same name
    is skipped even though its own tool_calls may differ.
    """
    transcript = measured.get("transcript")
    agent_usage = (
        transcript.get("agent_usage") if isinstance(transcript, dict) else None
    )
    if not isinstance(agent_usage, list) or not agent_usage:
        return None
    targets = _budget_target_by_agent(measured.get("agents"))
    if not targets:
        return None

    agents_out: list[dict[str, Any]] = []
    percentages: list[int] = []
    counted_agents: set[str] = set()
    for entry in agent_usage:
        if not isinstance(entry, dict) or entry.get("available") is not True:
            continue
        agent = entry.get("agent")
        tool_calls = entry.get("tool_calls")
        if (
            not isinstance(agent, str)
            or agent not in targets
            or agent in counted_agents
        ):
            continue
        if not isinstance(tool_calls, int) or isinstance(tool_calls, bool):
            continue
        counted_agents.add(agent)
        target = targets[agent]
        pct = round(tool_calls / target * 100)
        agents_out.append({
            "agent": agent,
            "tool_calls": tool_calls,
            "budget_target": target,
            "utilization_pct": pct,
        })
        percentages.append(pct)

    if not percentages:
        return None
    return {
        "agents": agents_out,
        "median_pct": _exact_statistic(statistics.median(percentages)),
        "min_pct": min(percentages),
        "max_pct": max(percentages),
        "sample_count": len(percentages),
    }


def measure_run(
    manifest: dict[str, Any],
    sessions_root: str | Path,
    registry_path: str | Path = DEFAULT_REGISTRY,
    *,
    include_transcripts: bool = True,
) -> dict[str, Any]:
    """Create one concise measured-run view without mutating the manifest."""
    duplicate_conflict = _is_duplicate_conflict(manifest)
    measured = _sanitize_manifest(copy.deepcopy(manifest))
    measured["wall_time_ms"] = _wall_time(measured)
    lifecycle = _lifecycle_summary(measured)
    measured["lifecycle"] = lifecycle

    if duplicate_conflict:
        measured["wall_time_ms"] = None
        measured["lifecycle"] = None
        # Same rule as the two above: a run whose id collides with another
        # cannot vouch for which run these durations belong to, so they
        # are withdrawn rather than attributed to the wrong one.
        measured["synthesis_agents"] = None
        measured["transcript"] = _sanitize_transcript(
            _unavailable_transcript("duplicate_run_id_conflict")
        )
        measured["budget_utilization"] = None
        measured["metric_availability"] = {
            family: "missing" for family in _AVAILABILITY_FAMILIES
        }
        return measured

    warnings = list(measured.get("warnings", []))
    if not include_transcripts:
        transcript = _unavailable_transcript("disabled")
    else:
        recognized = _recognized_agents(registry_path)
        if recognized is None:
            transcript = _unavailable_transcript("registry_unavailable")
            warnings.append("registry_unavailable")
        else:
            try:
                enrich = _load_transcript_module()
                transcript = enrich(measured, Path(sessions_root).expanduser(), recognized)
            except Exception:
                transcript = _unavailable_transcript("transcript_analysis_failed")

    transcript = _sanitize_transcript(transcript)
    for warning in _sanitize_warnings(transcript.get("warnings")):
        if warning not in warnings:
            warnings.append(warning)
    measured["warnings"] = _sanitize_warnings(warnings)
    measured["transcript"] = transcript
    measured["budget_utilization"] = _budget_utilization(measured)
    measured["metric_availability"] = {
        **_pipeline_metric_availability(measured, lifecycle),
        **_transcript_metric_availability(
            transcript, disabled=not include_transcripts
        ),
    }
    return measured

"""Cohort aggregation across measured runs."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any, Iterable

from .contracts import (
    _AVAILABILITY_FAMILIES,
    _AVAILABILITY_STATES,
    _CRITIC_VERDICT_SKIPPED,
    _CRITIC_VERDICTS,
)
from .sanitize import _nonnegative_int, _safe_wall_time_ms
from .usage import _add_usage, _empty_usage
from .load import _is_duplicate_conflict


def _availability_counts(runs: list[dict[str, Any]], family: str) -> dict[str, int]:
    counter = Counter()
    for run in runs:
        metrics = run.get("metric_availability")
        state = metrics.get(family) if isinstance(metrics, dict) else None
        counter[state if state in _AVAILABILITY_STATES else "missing"] += 1
    return {
        "available": counter["complete"] + counter["partial"],
        "complete": counter["complete"],
        "partial": counter["partial"],
        "missing": counter["missing"],
        "disabled": counter["disabled"],
    }



def _usage_totals_for_state(
    runs: list[dict[str, Any]], state: str
) -> dict[str, int] | None:
    total = _empty_usage()
    found = False
    for run in runs:
        if run.get("metric_availability", {}).get("usage") != state:
            continue
        transcript = run.get("transcript")
        if isinstance(transcript, dict) and _add_usage(total, transcript.get("usage")):
            found = True
    return total if found else None


# Each usage source has exactly one availability family — deriving it here
# makes a mismatched (source, family) pairing unrepresentable.
_USAGE_FAMILY_BY_SOURCE = {
    "step": "orchestrator_usage",
    "agent": "agent_usage",
    "model": "model_usage",
}


def _group_usage(
    runs: list[dict[str, Any]],
    *,
    state: str,
    source: str,
) -> dict[str, dict[str, int]] | None:
    family = _USAGE_FAMILY_BY_SOURCE[source]
    grouped: dict[str, dict[str, int]] = {}
    for run in runs:
        if run.get("metric_availability", {}).get(family) != state:
            continue
        transcript = run.get("transcript")
        if not isinstance(transcript, dict):
            continue
        if source == "step":
            by_step = transcript.get("orchestrator_usage_by_step")
            if not isinstance(by_step, dict):
                continue
            for name, usage in by_step.items():
                target = grouped.setdefault(str(name), _empty_usage())
                _add_usage(target, usage)
        else:
            entries = transcript.get("agent_usage")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("available") is not True:
                    continue
                if source == "agent":
                    name = entry.get("agent")
                    if not isinstance(name, str):
                        continue
                    target = grouped.setdefault(name, _empty_usage())
                    _add_usage(target, entry.get("usage"))
                elif source == "model":
                    # Bucket on the DISPATCHED model — `entry["model"]` is
                    # the dispatch result envelope's `resolvedModel`
                    # (`review_transcript.py` sets it there and nowhere
                    # else). That is the one canonical spelling for spend
                    # math: it keeps the priced context-window variant tag
                    # (`claude-opus-5[1m]`), where the per-message
                    # `usage_by_model` keys carry the bare API spelling
                    # (`claude-opus-5`) the tag was stripped from. Two
                    # spellings of one bucketing meant a cohort could blend
                    # differently-priced variants into one row. See
                    # `usage_snapshot.py::_build_snapshot` for the full
                    # rationale, including what this costs (a mid-run model
                    # fallback books entirely to the dispatched model; the
                    # enrichment's per-message `usage_by_model` is the
                    # forensic surface that can still show one).
                    # `measure._model_usage_availability` gates this same
                    # field, so a "complete" bucket set is one where every
                    # available entry carried a dispatched model; "unknown"
                    # therefore only ever appears in the partial view.
                    model = entry.get("model")
                    name = model if isinstance(model, str) and model else "unknown"
                    target = grouped.setdefault(name, _empty_usage())
                    _add_usage(target, entry.get("usage"))
    return dict(sorted(grouped.items())) if grouped else None


# Lifecycle fields accumulated by _aggregate_lifecycle_state and emitted by
# _lifecycle_block, with the transform applied at emission. One entry here
# covers both states (bare and ``partial_observed_``-prefixed keys).
_LIFECYCLE_FIELDS: dict[str, Any] = {
    "started_events": None,
    "completed_events": None,
    "incomplete_identities": sorted,
    "incomplete_count": None,
    "incomplete_by_agent": lambda value: dict(sorted(value.items())),
    "starts_by_agent": lambda value: dict(sorted(value.items())),
    "extra_starts_by_agent": lambda value: dict(sorted(value.items())),
    "retry_overhead": None,
    "completion_gap": None,
}


def _lifecycle_block(totals: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Emit one lifecycle state's fields, gated on that state having runs."""
    gate = totals["runs"]
    return {
        f"{prefix}{name}": (
            (transform(totals[name]) if transform else totals[name])
            if gate
            else None
        )
        for name, transform in _LIFECYCLE_FIELDS.items()
    }


def _aggregate_lifecycle_state(
    runs: Iterable[dict[str, Any]], state: str
) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "runs": 0,
        "started_events": 0,
        "completed_events": 0,
        "incomplete_identities": set(),
        "incomplete_count": 0,
        "incomplete_by_agent": Counter(),
        "starts_by_agent": Counter(),
        "extra_starts_by_agent": Counter(),
        "retry_overhead": 0,
        "completion_gap": 0,
    }
    for run in runs:
        if run.get("metric_availability", {}).get("lifecycle") != state:
            continue
        lifecycle = run.get("lifecycle")
        if not isinstance(lifecycle, dict):
            continue
        totals["runs"] += 1
        totals["started_events"] += lifecycle["started_events"]
        totals["completed_events"] += lifecycle["completed_events"]
        totals["incomplete_identities"].update(lifecycle["incomplete_identities"])
        totals["incomplete_count"] += lifecycle["incomplete_count"]
        totals["incomplete_by_agent"].update(lifecycle["incomplete_by_agent"])
        totals["starts_by_agent"].update(lifecycle["starts_by_agent"])
        totals["extra_starts_by_agent"].update(
            lifecycle["extra_starts_by_agent"]
        )
        totals["retry_overhead"] += lifecycle["retry_overhead"]
        totals["completion_gap"] += lifecycle["completion_gap"]
    return totals


def _exact_statistic(value: int | float) -> int | float:
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _aggregate_dispatch(
    runs: list[dict[str, Any]], availability: dict[str, dict[str, int]]
) -> dict[str, Any]:
    planner_total = 0
    planner_runs = 0
    actual_total = 0
    actual_runs = 0
    adjustments = Counter()
    compared_runs = 0
    compared_planner_candidates = 0
    for run in runs:
        dispatch = run.get("dispatch")
        if not isinstance(dispatch, dict):
            continue
        if dispatch.get("planner_baseline_available") is True:
            planner_total += _nonnegative_int(dispatch.get("planner_candidate_count")) or 0
            planner_runs += 1
        if dispatch.get("final_plan_available") is True:
            actual_total += _nonnegative_int(dispatch.get("final_dispatch_count")) or 0
            actual_runs += 1
        if dispatch.get("comparison_available") is True:
            counts = dispatch.get("adjustment_counts")
            if isinstance(counts, dict):
                for name in ("added", "removed", "unchanged"):
                    adjustments[name] += _nonnegative_int(counts.get(name)) or 0
                compared_planner_candidates += (
                    _nonnegative_int(dispatch.get("planner_candidate_count")) or 0
                )
                compared_runs += 1
    adjustment_denominator = sum(adjustments.values())
    adjustment_rate = (
        (adjustments["added"] + adjustments["removed"]) / adjustment_denominator
        if compared_runs and adjustment_denominator
        else 0.0 if compared_runs else None
    )
    planner_removal_rate = (
        adjustments["removed"] / compared_planner_candidates
        if compared_runs and compared_planner_candidates
        else 0.0 if compared_runs else None
    )
    return {
        "planner_candidates": planner_total if planner_runs else None,
        "planner_available_runs": planner_runs,
        "actual_dispatches": actual_total if actual_runs else None,
        "final_plan_available_runs": actual_runs,
        "adjustments": (
            {name: adjustments[name] for name in ("added", "removed", "unchanged")}
            if compared_runs else None
        ),
        "compared_runs": compared_runs,
        "adjustment_rate": adjustment_rate,
        "adjustment_rate_semantics": (
            "changed_agents_over_compared_union_agents"
        ),
        "compared_planner_candidates": compared_planner_candidates,
        "planner_removal_rate": planner_removal_rate,
        "availability": availability["dispatch"],
    }


def _aggregate_coverage(
    runs: list[dict[str, Any]], availability: dict[str, dict[str, int]]
) -> dict[str, Any]:
    coverage_counts = Counter()
    coverage_runs = 0
    for run in runs:
        if run.get("metric_availability", {}).get("coverage") != "complete":
            continue
        coverage = run.get("coverage")
        if not isinstance(coverage, dict):
            continue
        for name in ("changed", "reviewable", "assigned", "excluded", "uncovered"):
            value = coverage.get(name)
            coverage_counts[name] += len(value) if isinstance(value, list) else 0
        coverage_runs += 1
    coverage_rate = (
        coverage_counts["assigned"] / coverage_counts["reviewable"]
        if coverage_runs and coverage_counts["reviewable"]
        else None
    )
    return {
        **{
            name: coverage_counts[name] if coverage_runs else None
            for name in ("changed", "reviewable", "assigned", "excluded", "uncovered")
        },
        "assignment_rate": coverage_rate,
        "available_runs": coverage_runs,
        "semantics": "generated_scope_not_proof_of_model_read",
        "availability": availability["coverage"],
    }


_DEFERRED_HONESTY_FIELDS = ("deferred_reviewed", "declared_unreviewed", "unreviewed_autofilled")


def _aggregate_deferred_honesty(
    runs: list[dict[str, Any]], availability: dict[str, dict[str, int]]
) -> dict[str, Any]:
    """Sum the agent-vs-system NOT DIFFED honesty split across measured runs.

    Reuses the "coverage" family (the closest existing family, per its own
    availability gate) but additionally requires the run to actually carry
    `deferred_honesty_by_agent` — a run with complete coverage but no such
    key predates this feature and must not count as a measured zero.

    A run whose `deferred_honesty_by_agent` is present but EMPTY (`{}`) is
    a further distinct case: every dispatched reviewer was a legacy
    producer (no claims-capable output), so the key exists but nothing
    about the split was actually measurable. `measured_runs` only counts
    when at least one agent contributed real counts — an all-legacy run
    must not read as "measured, zero", the exact confusion this feature
    exists to eliminate one level up. `measured_agents`/`unmeasured_agents`
    make that same distinction visible at agent granularity: unmeasured
    agents are those in `deferred_total_by_agent` (the system saw a
    deferred-files sidecar for them) but absent from
    `deferred_honesty_by_agent` (their own review JSON never claimed
    anything) — derived as a set difference, counted whether or not the
    run as a whole clears the measured_runs bar.
    """
    counts = Counter()
    measured_runs = 0
    measured_agents = 0
    unmeasured_agents = 0
    for run in runs:
        if run.get("metric_availability", {}).get("coverage") != "complete":
            continue
        coverage = run.get("coverage")
        if not isinstance(coverage, dict):
            continue
        by_agent = coverage.get("deferred_honesty_by_agent")
        if not isinstance(by_agent, dict):
            continue
        total_by_agent = coverage.get("deferred_total_by_agent")
        total_by_agent = total_by_agent if isinstance(total_by_agent, dict) else {}
        unmeasured_agents += len(set(total_by_agent) - set(by_agent))
        if not by_agent:
            continue
        measured_runs += 1
        measured_agents += len(by_agent)
        for agent_counts in by_agent.values():
            if not isinstance(agent_counts, dict):
                continue
            for name in _DEFERRED_HONESTY_FIELDS:
                value = agent_counts.get(name)
                if isinstance(value, int) and not isinstance(value, bool):
                    counts[name] += value
    return {
        **{
            name: counts[name] if measured_runs else None
            for name in _DEFERRED_HONESTY_FIELDS
        },
        "measured_runs": measured_runs,
        "measured_agents": measured_agents,
        "unmeasured_agents": unmeasured_agents,
        "availability": availability["coverage"],
    }


def _aggregate_outcomes(
    runs: list[dict[str, Any]], availability: dict[str, dict[str, int]]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw_total = 0
    raw_runs = 0
    final_total = 0
    final_runs = 0
    critic_verdicts = Counter()
    wall_values: list[int] = []
    for run in runs:
        summary = run.get("outcome", {}).get("summary")
        summary = summary if isinstance(summary, dict) else {}
        if run.get("metric_availability", {}).get("raw_findings") == "complete":
            raw_total += _nonnegative_int(summary.get("total_agent_issues")) or 0
            raw_runs += 1
        if run.get("metric_availability", {}).get("final_findings") == "complete":
            final_total += _nonnegative_int(summary.get("final_issues")) or 0
            final_runs += 1
        if run.get("metric_availability", {}).get("critic") == "complete":
            verdict = run.get("outcome", {}).get("critic_verdict")
            if verdict in _CRITIC_VERDICTS:
                critic_verdicts[verdict] += 1
        if run.get("metric_availability", {}).get("wall_time") == "complete":
            wall = _safe_wall_time_ms(run.get("wall_time_ms"))
            if wall is not None:
                wall_values.append(wall)

    outcomes = {
        "raw_findings": raw_total if raw_runs else None,
        "raw_available_runs": raw_runs,
        "final_findings": final_total if final_runs else None,
        "final_available_runs": final_runs,
        "availability": availability["outcomes"],
        "raw_availability": availability["raw_findings"],
        "final_availability": availability["final_findings"],
    }
    critic = {
        "verdicts": dict(sorted(critic_verdicts.items())) if critic_verdicts else None,
        "availability": availability["critic"],
    }
    wall_time = {
        "total_ms": sum(wall_values) if wall_values else None,
        "mean_ms": (
            _exact_statistic(statistics.mean(wall_values))
            if wall_values else None
        ),
        "median_ms": (
            _exact_statistic(statistics.median(wall_values))
            if wall_values else None
        ),
        "availability": availability["wall_time"],
    }
    return outcomes, critic, wall_time


def _aggregate_synthesis_agents(
    runs: list[dict[str, Any]], availability: dict[str, dict[str, int]]
) -> dict[str, Any]:
    """Cross-run duration statistics for the two synthesis agents.

    Keyed by agent, because the reconciliator and the critic are different
    phases with different shapes — the audited 2026-08-19 run spent ~11
    minutes in the critic alone, and averaging that with a fast
    reconciliation would hide exactly the number this family exists to
    surface.

    Runs whose family is "missing" contribute nothing at all: they did not
    measure a zero, they measured nothing. "partial" runs DO contribute
    the durations they have, and their stalls are counted separately —
    dropping them would delete the only record of a hung synthesis agent.

    A "SKIPPED" row is counted but never averaged. Its span is dispatch to
    orchestrator-gave-up — quick mode skipping the critic, or the critic
    crashing and the handoff's fallback verdict being written — which is
    an upper bound on a critique that may never have started. Folding
    those into `mean_ms` would drag a critique-duration statistic toward
    crash-resolution latency, so they get their own `skipped_runs`.
    """
    durations: dict[str, list[int]] = {}
    stalled: Counter = Counter()
    skipped: Counter = Counter()
    dispatched: Counter = Counter()
    measured_runs = 0
    for run in runs:
        state = run.get("metric_availability", {}).get("synthesis_agents")
        if state not in {"complete", "partial"}:
            continue
        section = run.get("synthesis_agents")
        if not isinstance(section, dict):
            continue
        measured_runs += 1
        rows = section.get("agents")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get("agent"), str):
                continue
            name = row["agent"]
            dispatched[name] += 1
            if row.get("stalled") is True:
                stalled[name] += 1
            if row.get("verdict") == _CRITIC_VERDICT_SKIPPED:
                skipped[name] += 1
                continue
            duration = _nonnegative_int(row.get("duration_ms"))
            if duration is not None:
                durations.setdefault(name, []).append(duration)

    by_agent = {}
    for name in sorted(dispatched):
        values = durations.get(name, [])
        by_agent[name] = {
            "dispatched_runs": dispatched[name],
            # Runs contributing to the statistics below — dispatched
            # minus the stalled and the skipped.
            "measured_runs": len(values),
            "stalled_runs": stalled[name],
            "skipped_runs": skipped[name],
            "total_ms": sum(values) if values else None,
            "mean_ms": (
                _exact_statistic(statistics.mean(values)) if values else None
            ),
        }
    return {
        "by_agent": by_agent or None,
        "available_runs": measured_runs,
        "availability": availability["synthesis_agents"],
    }


def _aggregate_tool_failures(
    runs: list[dict[str, Any]], availability: dict[str, dict[str, int]]
) -> dict[str, Any]:
    failure_counts = Counter()
    failure_total = 0
    failure_recovered = 0
    partial_failure_total = 0
    for run in runs:
        state = run.get("metric_availability", {}).get("tool_failures")
        transcript = run.get("transcript")
        failures = transcript.get("tool_failures") if isinstance(transcript, dict) else None
        if not isinstance(failures, list):
            continue
        if state == "complete":
            failure_total += len(failures)
            for failure in failures:
                if not isinstance(failure, dict):
                    continue
                category = failure.get("category")
                if isinstance(category, str):
                    failure_counts[category] += 1
                if failure.get("recovered") is True:
                    failure_recovered += 1
        elif state == "partial":
            partial_failure_total += len(failures)
    return {
        "total": failure_total if availability["tool_failures"]["complete"] else None,
        "recovered": failure_recovered if availability["tool_failures"]["complete"] else None,
        "by_category": (
            dict(sorted(failure_counts.items()))
            if availability["tool_failures"]["complete"]
            else None
        ),
        "partial_observed_total": (
            partial_failure_total
            if availability["tool_failures"]["partial"]
            else None
        ),
        "availability": availability["tool_failures"],
    }


# Metrics reported for complete runs (bare keys). Partial runs report the
# superset below under a programmatic ``partial_observed_`` prefix, so adding
# a metric means one counter name here — never twin hand-written key pairs.
_ARTIFACT_COMPLETE_KEYS = (
    "first_builder_attempts",
    "first_builder_successes",
    "first_builder_failures",
    "recoveries",
    "no_builder_attempts",
    "runs_with_builder_attempts",
    "runs_without_builder_attempts",
    "top_only_runs_with_first_builder_success",
    "top_only_runs_with_first_builder_failure",
    "runs_with_builder_recovery",
)
_ARTIFACT_PARTIAL_KEYS = (
    "runs",
    "first_builder_attempts",
    "first_builder_successes",
    "first_builder_failures",
    "unknown_first_results",
    "unclassified_builder_results",
    "recoveries",
    "no_builder_attempts",
    "runs_with_builder_attempts",
    "runs_without_builder_attempts",
    "runs_with_unknown_builder_attempt_state",
    "top_only_runs_with_first_builder_success",
    "top_only_runs_with_first_builder_failure",
    "top_only_runs_with_unknown_first_builder_result",
    "runs_with_builder_recovery",
    "top_only_unclassified_builder_results",
)


def _aggregate_artifact_writes(
    runs: list[dict[str, Any]], availability: dict[str, dict[str, int]]
) -> dict[str, Any]:
    buckets: dict[str, Counter] = {"complete": Counter(), "partial": Counter()}
    for run in runs:
        state = run.get("metric_availability", {}).get("artifact_writes")
        if state not in buckets:
            continue
        transcript = run.get("transcript")
        artifacts = transcript.get("artifact_writes") if isinstance(transcript, dict) else None
        if not isinstance(artifacts, dict):
            continue
        bucket = buckets[state]
        bucket["runs"] += 1
        attempted = artifacts.get("builder_attempted")
        if attempted is True:
            bucket["runs_with_builder_attempts"] += 1
        elif attempted is False:
            bucket["runs_without_builder_attempts"] += 1
        else:
            bucket["runs_with_unknown_builder_attempt_state"] += 1
        # Complete runs count a run-level recovery only under a builder
        # attempt; partial runs count every observed recovery.
        if state == "partial" or attempted is True:
            bucket["runs_with_builder_recovery"] += int(
                artifacts.get("recovered") is True
            )

        by_agent = artifacts.get("by_agent")
        if isinstance(by_agent, list) and by_agent:
            for item in by_agent:
                if not isinstance(item, dict):
                    continue
                if item.get("builder_attempted") is True:
                    bucket["unclassified_builder_results"] += (
                        item.get("builder_attempts", 0)
                        - item.get("builder_successes", 0)
                        - item.get("builder_failures", 0)
                    )
                    first = item.get("first_builder_attempt_succeeded")
                    if isinstance(first, bool):
                        bucket["first_builder_successes"] += int(first)
                        bucket["first_builder_failures"] += int(not first)
                    else:
                        bucket["unknown_first_results"] += 1
                    bucket["recoveries"] += int(item.get("recovered") is True)
                elif item.get("builder_attempted") is False:
                    bucket["no_builder_attempts"] += 1
        elif isinstance(by_agent, list):
            bucket["top_only_unclassified_builder_results"] += (
                artifacts.get("builder_attempts", 0)
                - artifacts.get("builder_successes", 0)
                - artifacts.get("builder_failures", 0)
            )
            first = artifacts.get("first_builder_attempt_succeeded")
            if isinstance(first, bool):
                bucket["top_only_runs_with_first_builder_success"] += int(first)
                bucket["top_only_runs_with_first_builder_failure"] += int(not first)
            elif attempted is True:
                bucket["top_only_runs_with_unknown_first_builder_result"] += 1

    # First attempts are derived: complete runs classify every counted first
    # attempt as success or failure; partial runs also count unknown results.
    buckets["complete"]["first_builder_attempts"] = (
        buckets["complete"]["first_builder_successes"]
        + buckets["complete"]["first_builder_failures"]
    )
    buckets["partial"]["first_builder_attempts"] = (
        buckets["partial"]["first_builder_successes"]
        + buckets["partial"]["first_builder_failures"]
        + buckets["partial"]["unknown_first_results"]
    )

    complete_gate = availability["artifact_writes"]["complete"]
    partial_gate = availability["artifact_writes"]["partial"]
    return {
        **{
            name: buckets["complete"][name] if complete_gate else None
            for name in _ARTIFACT_COMPLETE_KEYS
        },
        **{
            f"partial_observed_{name}": (
                buckets["partial"][name] if partial_gate else None
            )
            for name in _ARTIFACT_PARTIAL_KEYS
        },
        "availability": availability["artifact_writes"],
    }


def _aggregate_observed_reads(
    runs: list[dict[str, Any]], availability: dict[str, dict[str, int]]
) -> dict[str, Any]:
    observed_paths = Counter()
    non_scope_comparable_paths = Counter()
    partial_non_scope_comparable_paths = Counter()
    partial_observed_count = 0
    for run in runs:
        scope_state = run.get("metric_availability", {}).get(
            "scope_comparable_reads"
        )
        non_scope_state = run.get("metric_availability", {}).get(
            "non_scope_comparable_reads"
        )
        transcript = run.get("transcript")
        reads = transcript.get("observed_reads") if isinstance(transcript, dict) else None
        paths = reads.get("out_of_scope") if isinstance(reads, dict) else None
        non_scope_comparable = (
            reads.get("non_scope_comparable")
            if isinstance(reads, dict)
            else None
        )
        if not isinstance(paths, list) or not isinstance(
            non_scope_comparable, list
        ):
            continue
        if scope_state == "complete":
            observed_paths.update(path for path in paths if isinstance(path, str))
        elif scope_state == "partial":
            partial_observed_count += len(paths)
        if non_scope_state == "complete":
            non_scope_comparable_paths.update(
                path for path in non_scope_comparable if isinstance(path, str)
            )
        elif non_scope_state == "partial":
            partial_non_scope_comparable_paths.update(
                path for path in non_scope_comparable if isinstance(path, str)
            )

    return {
        "out_of_scope_count": (
            sum(observed_paths.values())
            if availability["scope_comparable_reads"]["complete"]
            else None
        ),
        "by_path": (
            dict(sorted(observed_paths.items()))
            if availability["scope_comparable_reads"]["complete"]
            else None
        ),
        "partial_observed_out_of_scope_count": (
            partial_observed_count
            if availability["scope_comparable_reads"]["partial"]
            else None
        ),
        "non_scope_comparable_count": (
            sum(non_scope_comparable_paths.values())
            if availability["non_scope_comparable_reads"]["complete"]
            else None
        ),
        "non_scope_comparable_by_path": (
            dict(sorted(non_scope_comparable_paths.items()))
            if availability["non_scope_comparable_reads"]["complete"]
            else None
        ),
        "partial_observed_non_scope_comparable_count": (
            sum(partial_non_scope_comparable_paths.values())
            if availability["non_scope_comparable_reads"]["partial"]
            else None
        ),
        "partial_non_scope_comparable_by_path": (
            dict(sorted(partial_non_scope_comparable_paths.items()))
            if availability["non_scope_comparable_reads"]["partial"]
            else None
        ),
        "exhaustive": False,
        "availability": availability["scope_comparable_reads"],
        "non_scope_comparable_availability": availability[
            "non_scope_comparable_reads"
        ],
        "combined_availability": availability["observed_reads"],
    }


def aggregate_cohort(runs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a measured cohort without treating unavailable data as zero."""
    run_list = [run for run in runs if not _is_duplicate_conflict(run)]
    availability = {
        family: _availability_counts(run_list, family)
        for family in _AVAILABILITY_FAMILIES
    }

    lifecycle_complete = _aggregate_lifecycle_state(run_list, "complete")
    lifecycle_partial = _aggregate_lifecycle_state(run_list, "partial")
    complete_usage = _usage_totals_for_state(run_list, "complete")
    partial_usage = _usage_totals_for_state(run_list, "partial")

    dispatch = _aggregate_dispatch(run_list, availability)
    coverage = _aggregate_coverage(run_list, availability)
    deferred_honesty = _aggregate_deferred_honesty(run_list, availability)
    outcomes, critic, wall_time = _aggregate_outcomes(run_list, availability)
    synthesis_agents = _aggregate_synthesis_agents(run_list, availability)
    tool_failures = _aggregate_tool_failures(run_list, availability)
    artifact_writes = _aggregate_artifact_writes(run_list, availability)
    observed_reads = _aggregate_observed_reads(run_list, availability)

    aggregate = {
        "runs": len(run_list),
        "transcript_runs": availability["transcript"]["available"],
        "availability": availability,
        "dispatch": dispatch,
        "coverage": coverage,
        "deferred_honesty": deferred_honesty,
        "lifecycle": {
            **_lifecycle_block(lifecycle_complete),
            "partial_observed_runs": (
                lifecycle_partial["runs"] if lifecycle_partial["runs"] else None
            ),
            **_lifecycle_block(lifecycle_partial, "partial_observed_"),
            "availability": availability["lifecycle"],
        },
        "outcomes": outcomes,
        "critic": critic,
        "wall_time": wall_time,
        "synthesis_agents": synthesis_agents,
        "usage": {
            "complete_totals": complete_usage,
            "partial_observed_totals": partial_usage,
            "availability": availability["usage"],
        },
        "orchestrator_usage": {
            "by_step": _group_usage(run_list, state="complete", source="step"),
            "partial_observed_by_step": _group_usage(
                run_list, state="partial", source="step"
            ),
            "availability": availability["orchestrator_usage"],
        },
        "agent_usage": {
            "by_agent": _group_usage(run_list, state="complete", source="agent"),
            "partial_observed_by_agent": _group_usage(
                run_list, state="partial", source="agent"
            ),
            "availability": availability["agent_usage"],
        },
        "model_usage": {
            "by_model": _group_usage(run_list, state="complete", source="model"),
            "partial_observed_by_model": _group_usage(
                run_list, state="partial", source="model"
            ),
            "availability": availability["model_usage"],
        },
        "tool_failures": tool_failures,
        "artifact_writes": artifact_writes,
        "observed_reads": observed_reads,
    }
    return aggregate

"""Cohort aggregation across measured runs."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any, Iterable

from .contracts import _AVAILABILITY_FAMILIES, _AVAILABILITY_STATES, _CRITIC_VERDICTS
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


def _group_usage(
    runs: list[dict[str, Any]],
    *,
    state: str,
    family: str,
    source: str,
) -> dict[str, dict[str, int]] | None:
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
                    by_model = entry.get("usage_by_model")
                    if not isinstance(by_model, dict):
                        continue
                    for name, usage in by_model.items():
                        if not isinstance(name, str):
                            continue
                        target = grouped.setdefault(name, _empty_usage())
                        _add_usage(target, usage)
    return dict(sorted(grouped.items())) if grouped else None


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


def _aggregate_artifact_writes(
    runs: list[dict[str, Any]], availability: dict[str, dict[str, int]]
) -> dict[str, Any]:
    first_attempts = first_successes = first_failures = recoveries = no_attempts = 0
    runs_with_attempts = runs_without_attempts = runs_with_recovery = 0
    top_only_runs_with_first_success = top_only_runs_with_first_failure = 0
    partial_runs = 0
    partial_first_attempts = 0
    partial_first_successes = 0
    partial_first_failures = 0
    partial_unknown_first_results = 0
    partial_unclassified_builder_results = 0
    partial_recoveries = 0
    partial_no_attempts = 0
    partial_runs_with_attempts = 0
    partial_runs_without_attempts = 0
    partial_runs_with_unknown_attempt_state = 0
    partial_top_only_runs_with_first_success = 0
    partial_top_only_runs_with_first_failure = 0
    partial_top_only_runs_with_unknown_first = 0
    partial_runs_with_recovery = 0
    partial_top_only_unclassified_results = 0
    for run in runs:
        state = run.get("metric_availability", {}).get("artifact_writes")
        if state not in {"complete", "partial"}:
            continue
        transcript = run.get("transcript")
        artifacts = transcript.get("artifact_writes") if isinstance(transcript, dict) else None
        if not isinstance(artifacts, dict):
            continue
        is_partial = state == "partial"
        if is_partial:
            partial_runs += 1
            if artifacts.get("builder_attempted") is True:
                partial_runs_with_attempts += 1
            elif artifacts.get("builder_attempted") is False:
                partial_runs_without_attempts += 1
            else:
                partial_runs_with_unknown_attempt_state += 1
            partial_runs_with_recovery += int(
                artifacts.get("recovered") is True
            )
        elif artifacts.get("builder_attempted") is True:
            runs_with_attempts += 1
            runs_with_recovery += int(artifacts.get("recovered") is True)
        elif artifacts.get("builder_attempted") is False:
            runs_without_attempts += 1

        by_agent = artifacts.get("by_agent")
        if isinstance(by_agent, list) and by_agent:
            for item in by_agent:
                if not isinstance(item, dict):
                    continue
                if item.get("builder_attempted") is True:
                    first = item.get("first_builder_attempt_succeeded")
                    if is_partial:
                        partial_unclassified_builder_results += (
                            item.get("builder_attempts", 0)
                            - item.get("builder_successes", 0)
                            - item.get("builder_failures", 0)
                        )
                    if isinstance(first, bool):
                        if is_partial:
                            partial_first_attempts += 1
                            partial_first_successes += int(first)
                            partial_first_failures += int(not first)
                        else:
                            first_attempts += 1
                            first_successes += int(first)
                            first_failures += int(not first)
                    elif is_partial:
                        partial_first_attempts += 1
                        partial_unknown_first_results += 1
                    if is_partial:
                        partial_recoveries += int(item.get("recovered") is True)
                    else:
                        recoveries += int(item.get("recovered") is True)
                elif item.get("builder_attempted") is False:
                    if is_partial:
                        partial_no_attempts += 1
                    else:
                        no_attempts += 1
        elif isinstance(by_agent, list):
            first = artifacts.get("first_builder_attempt_succeeded")
            if is_partial:
                partial_top_only_unclassified_results += (
                    artifacts.get("builder_attempts", 0)
                    - artifacts.get("builder_successes", 0)
                    - artifacts.get("builder_failures", 0)
                )
                if isinstance(first, bool):
                    partial_top_only_runs_with_first_success += int(first)
                    partial_top_only_runs_with_first_failure += int(not first)
                elif artifacts.get("builder_attempted") is True:
                    partial_top_only_runs_with_unknown_first += 1
            elif isinstance(first, bool):
                top_only_runs_with_first_success += int(first)
                top_only_runs_with_first_failure += int(not first)

    return {
        "first_builder_attempts": (
            first_attempts if availability["artifact_writes"]["complete"] else None
        ),
        "first_builder_successes": (
            first_successes if availability["artifact_writes"]["complete"] else None
        ),
        "first_builder_failures": (
            first_failures if availability["artifact_writes"]["complete"] else None
        ),
        "recoveries": recoveries if availability["artifact_writes"]["complete"] else None,
        "no_builder_attempts": (
            no_attempts if availability["artifact_writes"]["complete"] else None
        ),
        "runs_with_builder_attempts": (
            runs_with_attempts
            if availability["artifact_writes"]["complete"]
            else None
        ),
        "runs_without_builder_attempts": (
            runs_without_attempts
            if availability["artifact_writes"]["complete"]
            else None
        ),
        "top_only_runs_with_first_builder_success": (
            top_only_runs_with_first_success
            if availability["artifact_writes"]["complete"]
            else None
        ),
        "top_only_runs_with_first_builder_failure": (
            top_only_runs_with_first_failure
            if availability["artifact_writes"]["complete"]
            else None
        ),
        "runs_with_builder_recovery": (
            runs_with_recovery
            if availability["artifact_writes"]["complete"]
            else None
        ),
        "partial_observed_runs": (
            partial_runs if availability["artifact_writes"]["partial"] else None
        ),
        "partial_observed_first_builder_attempts": (
            partial_first_attempts
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_first_builder_successes": (
            partial_first_successes
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_first_builder_failures": (
            partial_first_failures
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_unknown_first_results": (
            partial_unknown_first_results
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_unclassified_builder_results": (
            partial_unclassified_builder_results
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_recoveries": (
            partial_recoveries
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_no_builder_attempts": (
            partial_no_attempts
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_runs_with_builder_attempts": (
            partial_runs_with_attempts
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_runs_without_builder_attempts": (
            partial_runs_without_attempts
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_runs_with_unknown_builder_attempt_state": (
            partial_runs_with_unknown_attempt_state
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_top_only_runs_with_first_builder_success": (
            partial_top_only_runs_with_first_success
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_top_only_runs_with_first_builder_failure": (
            partial_top_only_runs_with_first_failure
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_top_only_runs_with_unknown_first_builder_result": (
            partial_top_only_runs_with_unknown_first
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_runs_with_builder_recovery": (
            partial_runs_with_recovery
            if availability["artifact_writes"]["partial"]
            else None
        ),
        "partial_observed_top_only_unclassified_builder_results": (
            partial_top_only_unclassified_results
            if availability["artifact_writes"]["partial"]
            else None
        ),
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
    outcomes, critic, wall_time = _aggregate_outcomes(run_list, availability)
    tool_failures = _aggregate_tool_failures(run_list, availability)
    artifact_writes = _aggregate_artifact_writes(run_list, availability)
    observed_reads = _aggregate_observed_reads(run_list, availability)

    aggregate = {
        "runs": len(run_list),
        "transcript_runs": availability["transcript"]["available"],
        "availability": availability,
        "dispatch": dispatch,
        "coverage": coverage,
        "lifecycle": {
            "started_events": (
                lifecycle_complete["started_events"]
                if lifecycle_complete["runs"]
                else None
            ),
            "completed_events": (
                lifecycle_complete["completed_events"]
                if lifecycle_complete["runs"]
                else None
            ),
            "incomplete_identities": (
                sorted(lifecycle_complete["incomplete_identities"])
                if lifecycle_complete["runs"]
                else None
            ),
            "incomplete_count": (
                lifecycle_complete["incomplete_count"]
                if lifecycle_complete["runs"]
                else None
            ),
            "incomplete_by_agent": (
                dict(sorted(lifecycle_complete["incomplete_by_agent"].items()))
                if lifecycle_complete["runs"]
                else None
            ),
            "starts_by_agent": (
                dict(sorted(lifecycle_complete["starts_by_agent"].items()))
                if lifecycle_complete["runs"]
                else None
            ),
            "extra_starts_by_agent": (
                dict(sorted(lifecycle_complete["extra_starts_by_agent"].items()))
                if lifecycle_complete["runs"]
                else None
            ),
            "retry_overhead": (
                lifecycle_complete["retry_overhead"]
                if lifecycle_complete["runs"]
                else None
            ),
            "completion_gap": (
                lifecycle_complete["completion_gap"]
                if lifecycle_complete["runs"]
                else None
            ),
            "partial_observed_runs": (
                lifecycle_partial["runs"] if lifecycle_partial["runs"] else None
            ),
            "partial_observed_started_events": (
                lifecycle_partial["started_events"]
                if lifecycle_partial["runs"]
                else None
            ),
            "partial_observed_completed_events": (
                lifecycle_partial["completed_events"]
                if lifecycle_partial["runs"]
                else None
            ),
            "partial_observed_incomplete_identities": (
                sorted(lifecycle_partial["incomplete_identities"])
                if lifecycle_partial["runs"]
                else None
            ),
            "partial_observed_incomplete_count": (
                lifecycle_partial["incomplete_count"]
                if lifecycle_partial["runs"]
                else None
            ),
            "partial_observed_incomplete_by_agent": (
                dict(sorted(lifecycle_partial["incomplete_by_agent"].items()))
                if lifecycle_partial["runs"]
                else None
            ),
            "partial_observed_starts_by_agent": (
                dict(sorted(lifecycle_partial["starts_by_agent"].items()))
                if lifecycle_partial["runs"]
                else None
            ),
            "partial_observed_extra_starts_by_agent": (
                dict(sorted(lifecycle_partial["extra_starts_by_agent"].items()))
                if lifecycle_partial["runs"]
                else None
            ),
            "partial_observed_retry_overhead": (
                lifecycle_partial["retry_overhead"]
                if lifecycle_partial["runs"]
                else None
            ),
            "partial_observed_completion_gap": (
                lifecycle_partial["completion_gap"]
                if lifecycle_partial["runs"]
                else None
            ),
            "availability": availability["lifecycle"],
        },
        "outcomes": outcomes,
        "critic": critic,
        "wall_time": wall_time,
        "usage": {
            "complete_totals": complete_usage,
            "partial_observed_totals": partial_usage,
            "availability": availability["usage"],
        },
        "orchestrator_usage": {
            "by_step": _group_usage(
                run_list, state="complete", family="orchestrator_usage", source="step"
            ),
            "partial_observed_by_step": _group_usage(
                run_list, state="partial", family="orchestrator_usage", source="step"
            ),
            "availability": availability["orchestrator_usage"],
        },
        "agent_usage": {
            "by_agent": _group_usage(
                run_list, state="complete", family="agent_usage", source="agent"
            ),
            "partial_observed_by_agent": _group_usage(
                run_list, state="partial", family="agent_usage", source="agent"
            ),
            "availability": availability["agent_usage"],
        },
        "model_usage": {
            "by_model": _group_usage(
                run_list, state="complete", family="model_usage", source="model"
            ),
            "partial_observed_by_model": _group_usage(
                run_list, state="partial", family="model_usage", source="model"
            ),
            "availability": availability["model_usage"],
        },
        "tool_failures": tool_failures,
        "artifact_writes": artifact_writes,
        "observed_reads": observed_reads,
    }
    return aggregate

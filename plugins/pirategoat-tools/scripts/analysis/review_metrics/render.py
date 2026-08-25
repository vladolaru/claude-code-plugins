"""Table and JSON rendering of run and cohort reports."""

from __future__ import annotations

import json
import unicodedata
from typing import Any, Iterable

from .contracts import (
    _ANSI_ESCAPE_RE,
    _CRITIC_VERDICTS,
    _REPORT_SCHEMA,
    _SYNTHESIS_DECISION_CRITIC,
    _SYNTHESIS_RECONCILIATOR,
    _TABLE_CELL_LIMIT,
)


def _format_count(value: object) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else "—"


def _table_cell(value: object) -> str:
    """Normalize one bounded Markdown-table display cell."""
    text = _ANSI_ESCAPE_RE.sub("", str(value))
    text = "".join(
        " " if unicodedata.category(character) in {"Cc", "Cf"} else character
        for character in text
    )
    text = " ".join(text.split()).replace("\\", "\\\\").replace("|", r"\|")
    if len(text) > _TABLE_CELL_LIMIT:
        text = text[: _TABLE_CELL_LIMIT - 1]
        if text.endswith("\\"):
            text = text[:-1]
        text += "…"
    return text


def _duration_cell(value: object) -> str:
    """One millisecond span as display seconds, or "—" when unmeasured.

    The bool guard matters: `True` is an `int` in Python, so without it a
    corrupted flag would render as a 0.0s phase — a fabricated
    measurement in the one column whose whole job is telling measured
    from unmeasured.
    """
    return (
        f"{value / 1000:.1f}s"
        if isinstance(value, int) and not isinstance(value, bool)
        else "—"
    )


def _synthesis_cell(section: object, state: str) -> str:
    """Render the reconciliator/critic phase durations as `recon/critic`.

    "—" is the never-measured answer and covers every run predating the
    family; a stalled agent renders "stalled" rather than a duration,
    because the phase has none. Neither may read as a fast phase.
    """
    if state == "missing" or not isinstance(section, dict):
        return "—"
    rows = section.get("agents")
    rows = rows if isinstance(rows, list) else []
    by_agent = {
        row.get("agent"): row for row in rows if isinstance(row, dict)
    }

    def cell(name: str) -> str:
        row = by_agent.get(name)
        if row is None:
            return "—"
        if row.get("stalled") is True:
            return "stalled"
        return _duration_cell(row.get("duration_ms"))

    # Identities come from the producer's own constants, never respelled
    # here: a renamed agent must break this package's tests, not silently
    # render two em-dashes forever.
    return (
        f"{cell(_SYNTHESIS_RECONCILIATOR)}/"
        f"{cell(_SYNTHESIS_DECISION_CRITIC)}"
    )


def _budget_utilization_cell(value: object) -> str:
    """One run's median tool-call utilization as `median N% (min–max%)`.

    "—" covers every unmeasured shape uniformly: disabled/missing
    transcripts, a pre-budget-telemetry manifest, or a run with no agent
    carrying both a known target and observed tool-call evidence. Never a
    fabricated 0%.
    """
    if not isinstance(value, dict):
        return "—"
    median = value.get("median_pct")
    low = value.get("min_pct")
    high = value.get("max_pct")
    if (
        not isinstance(median, (int, float))
        or isinstance(median, bool)
        or not isinstance(low, int)
        or isinstance(low, bool)
        or not isinstance(high, int)
        or isinstance(high, bool)
    ):
        return "—"
    return f"median {median}% ({low}–{high}%)"


def _table_row(run: dict[str, Any]) -> list[str]:
    identity = run.get("run") if isinstance(run.get("run"), dict) else {}
    dispatch = run.get("dispatch") if isinstance(run.get("dispatch"), dict) else None
    coverage = run.get("coverage") if isinstance(run.get("coverage"), dict) else None
    outcome = run.get("outcome") if isinstance(run.get("outcome"), dict) else {}
    summary = outcome.get("summary") if isinstance(outcome.get("summary"), dict) else {}
    transcript = run.get("transcript") if isinstance(run.get("transcript"), dict) else {}
    metrics = (
        run.get("metric_availability")
        if isinstance(run.get("metric_availability"), dict)
        else {}
    )

    if dispatch is None:
        planner_actual = "—"
        adjustments = "n/a"
    else:
        planner = (
            _format_count(dispatch.get("planner_candidate_count"))
            if dispatch.get("planner_baseline_available") is True else "—"
        )
        actual = (
            _format_count(dispatch.get("final_dispatch_count"))
            if dispatch.get("final_plan_available") is True else "—"
        )
        planner_actual = f"{planner}→{actual}"
        counts = dispatch.get("adjustment_counts")
        adjustments = (
            f"+{counts.get('added', 0)}/-{counts.get('removed', 0)}"
            if dispatch.get("comparison_available") is True and isinstance(counts, dict)
            else "n/a"
        )
    coverage_text = (
        f"{len(coverage.get('assigned_files', []))}/"
        f"{len(coverage.get('reviewable_files', []))}/"
        f"{len(coverage.get('unassigned_reviewable_files', []))}"
        if coverage is not None else "—"
    )
    if coverage is not None and metrics.get("coverage") == "partial":
        coverage_text = f"partial {coverage_text}"
    raw = _format_count(summary.get("total_agent_findings"))
    final = _format_count(summary.get("final_finding_count"))
    critic_state = metrics.get("critic", "missing")
    critic_verdict = outcome.get("critic_verdict")
    if critic_state == "complete" and critic_verdict in _CRITIC_VERDICTS:
        critic = critic_verdict
    elif critic_state == "disabled":
        critic = "n/a"
    else:
        critic = "—"
    outcome_text = f"{raw}→{final}/{critic}"
    wall_text = _duration_cell(run.get("wall_time_ms"))
    synthesis_text = _synthesis_cell(
        run.get("synthesis_agents"), metrics.get("synthesis_agents", "missing")
    )
    budget_text = _budget_utilization_cell(run.get("budget_utilization"))
    usage = transcript.get("usage") if isinstance(transcript, dict) else None
    usage_state = metrics.get("usage", "missing")
    if usage_state in {"complete", "partial"} and isinstance(usage, dict):
        tokens = (
            f"{_format_count(usage.get('effective_input_tokens'))}/"
            f"{_format_count(usage.get('output_tokens'))}"
        )
        if usage_state == "partial":
            tokens = f"partial {tokens}"
    elif usage_state == "disabled":
        tokens = "n/a"
    else:
        tokens = "—"
    transcript_state = metrics.get("transcript", "missing")
    correlation = transcript.get("correlation") if isinstance(transcript, dict) else None
    if isinstance(correlation, dict) and transcript_state == "partial":
        # Sanitization omits absent/invalid counts — defaulting to 0 would
        # render missing evidence as a measured "partial 0/0".
        transcript_state = (
            f"partial {_format_count(correlation.get('correlated_count'))}/"
            f"{_format_count(correlation.get('expected_count'))}"
        )
    return [
        str(identity.get("id") or "—"),
        f"{identity.get('plugin_version') or '—'}/{identity.get('mode') or '—'}",
        planner_actual,
        adjustments,
        coverage_text,
        outcome_text,
        wall_text,
        synthesis_text,
        tokens,
        transcript_state,
        budget_text,
    ]


def format_table(runs: list[dict[str, Any]], aggregate: dict[str, Any]) -> str:
    """Render a compact, missing-aware cohort table."""
    if not runs:
        return "No review runs found.\n"
    headers = [
        "Run ID",
        "Version/Mode",
        "Planner→Actual",
        "Adjustments",
        "Assigned/Reviewable/Unassigned",
        "Outcome/Critic",
        "Wall",
        "Recon/Critic",
        "Eff In/Out",
        "Transcript",
        "Budget util",
    ]
    headers = [_table_cell(value) for value in headers]
    rows = [
        [_table_cell(value) for value in _table_row(run)]
        for run in runs
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def render(values: list[str]) -> str:
        return "| " + " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(values)
        ) + " |"

    lines = [
        render(headers),
        "| " + " | ".join("-" * width for width in widths) + " |",
        *(render(row) for row in rows),
        "",
        f"Runs: {aggregate.get('runs', len(runs))}; "
        f"transcript data: {aggregate.get('transcript_runs', 0)} available.",
        "Generated-scope coverage is descriptive, not proof of model reads; "
        "observed reads are non-exhaustive.",
    ]
    return "\n".join(lines) + "\n"


def format_json(runs: list[dict[str, Any]], aggregate: dict[str, Any]) -> str:
    """Render the stable structured report."""
    return json.dumps(
        {
            "schema": _REPORT_SCHEMA,
            "runs": runs,
            "aggregate": aggregate,
        },
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"

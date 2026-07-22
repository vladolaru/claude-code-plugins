"""Supported review-run and cohort metrics.

Imports flow one way only:

    contracts -> sanitize -> usage -> load -> {measure, cohort} -> render -> cli

`scripts/analysis/review_run_metrics.py` is the CLI entry point and stays the
documented path (README.md, AGENTS.md, CHANGELOG.md).
"""

from __future__ import annotations

from .cli import main
from .cohort import aggregate_cohort
from .contracts import DEFAULT_LOG_DIR, DEFAULT_SESSIONS_ROOT
from .load import load_runs
from .measure import measure_run
from .render import format_json, format_table

__all__ = [
    "DEFAULT_LOG_DIR",
    "DEFAULT_SESSIONS_ROOT",
    "aggregate_cohort",
    "format_json",
    "format_table",
    "load_runs",
    "main",
    "measure_run",
]

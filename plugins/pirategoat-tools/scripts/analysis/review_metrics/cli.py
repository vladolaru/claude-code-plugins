"""Command-line entry point for review run and cohort metrics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .contracts import DEFAULT_LOG_DIR, DEFAULT_SESSIONS_ROOT
from .load import load_runs
from .measure import measure_run
from .cohort import aggregate_cohort
from .render import format_json, format_table


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure review pipeline runs and recent cohorts."
    )
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--sessions-root", default=str(DEFAULT_SESSIONS_ROOT))
    parser.add_argument("--last", type=_positive_int)
    parser.add_argument("--run-id")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--output")
    parser.add_argument("--no-transcripts", action="store_true")
    return parser


def _resolve_transcripts(args) -> bool:
    """Decide whether to enrich from transcripts.

    Enrichment costs one session discovery plus a full transcript parse per
    run, so it scales with the whole log directory when the query is
    unbounded. Rather than silently truncating the cohort — full-history
    sweeps are the point of this tool — an unbounded query reports the
    transcript family as explicitly disabled and says how to enable it.
    """
    if args.no_transcripts:
        return False
    if args.last is None and args.run_id is None:
        print(
            "review_run_metrics: unbounded cohort — transcript enrichment "
            "disabled. Pass --last N or --run-id to enable it.",
            file=sys.stderr,
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    """Run the cohort CLI; argument errors retain argparse's exit status 2."""
    args = _parser().parse_args(argv)
    try:
        include_transcripts = _resolve_transcripts(args)
        manifests = load_runs(args.log_dir, last=args.last, run_id=args.run_id)
        runs = [
            measure_run(
                manifest,
                args.sessions_root,
                include_transcripts=include_transcripts,
            )
            for manifest in manifests
        ]
        aggregate = aggregate_cohort(runs)
        rendered = (
            format_json(runs, aggregate)
            if args.format == "json"
            else format_table(runs, aggregate)
        )
        if args.output:
            output = Path(args.output).expanduser()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except Exception as error:
        print(
            "review_run_metrics: unable to produce report: "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1

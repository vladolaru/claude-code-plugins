#!/usr/bin/env python3
"""Canonical artifact paths for one durable iterative review run."""

import argparse
import sys
from pathlib import Path

# Absolute script execution puts iterative_review/ on sys.path. Add scripts/
# so the shared review path authority resolves exactly as it does for imports.
SCRIPTS_DIR = str(Path(__file__).resolve().parents[1])
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from review.run_paths import (
    PIPELINE_SUBDIR,
    REVIEWERS_SUBDIR,
    SCRATCH_SUBDIR,
    SYNTHESIS_SUBDIR,
    reviewer_dir,
)


ITERATIVE_ARTIFACTS = {
    # key -> (canonical group, filename). Iterative per-run filenames live
    # only here; all producers and consumers resolve them through this module.
    "context": (PIPELINE_SUBDIR, "review-context.md"),
    "state": (PIPELINE_SUBDIR, "review-loop-state.json"),
    "progress": (PIPELINE_SUBDIR, "review-progress.jsonl"),
    "events": (PIPELINE_SUBDIR, "pipeline-events.jsonl"),
    "pushback": (SYNTHESIS_SUBDIR, "pushback-log.md"),
    "deferred": (SYNTHESIS_SUBDIR, "deferred-items.jsonl"),
    "result": (SYNTHESIS_SUBDIR, "review-loop-result.json"),
}

ROUND_ARTIFACTS = {
    "findings": "findings.json",
    "outcomes": "outcomes.json",
    "prompt": "prompt.md",
    "codex_output": "codex-output.json",
    "codex_raw": "codex-raw.md",
    "claude_raw": "claude-raw.md",
    "analysis": "{prefix}-r{round}-analysis.md",
}


def ensure_iterative_layout(run_dir) -> None:
    """Ensure the four canonical artifact subdirectories exist for a run."""
    run_dir = Path(run_dir)
    for subdir in (
        PIPELINE_SUBDIR,
        REVIEWERS_SUBDIR,
        SYNTHESIS_SUBDIR,
        SCRATCH_SUBDIR,
    ):
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)


def iterative_artifact_path(run_dir, key: str) -> Path:
    """Resolve a run-scoped iterative pipeline or synthesis artifact."""
    subdir, filename = ITERATIVE_ARTIFACTS[key]
    return Path(run_dir) / subdir / filename


def round_dir(run_dir, round_num: int) -> Path:
    """Resolve the reviewer-owned directory for a positive round number."""
    if (
        isinstance(round_num, bool)
        or not isinstance(round_num, int)
        or round_num < 1
    ):
        raise ValueError("round number must be a positive integer")
    return reviewer_dir(run_dir, f"round-{round_num}")


def _validate_analysis_prefix(prefix: str) -> None:
    if (
        not isinstance(prefix, str)
        or not prefix
        or prefix in {".", ".."}
        or "/" in prefix
        or "\\" in prefix
        or "\x00" in prefix
    ):
        raise ValueError(f"invalid analysis prefix: {prefix!r}")


def round_artifact_path(
    run_dir,
    round_num: int,
    key: str,
    *,
    prefix: str | None = None,
) -> Path:
    """Resolve an artifact owned by one iterative round reviewer."""
    template = ROUND_ARTIFACTS[key]
    if key == "analysis":
        _validate_analysis_prefix(prefix)
        filename = template.format(prefix=prefix, round=round_num)
    else:
        filename = template
    return round_dir(run_dir, round_num) / filename


def backend_raw_path(run_dir, round_num: int, backend: str) -> Path:
    """Resolve the raw-output artifact for a supported review backend."""
    if backend not in {"codex", "claude"}:
        raise ValueError(f"unknown iterative review backend: {backend!r}")
    return round_artifact_path(run_dir, round_num, f"{backend}_raw")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve canonical iterative review artifact paths."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    artifact = subparsers.add_parser("artifact")
    artifact.add_argument("--output-dir", required=True)
    artifact.add_argument("--key", required=True, choices=ITERATIVE_ARTIFACTS)

    round_artifact = subparsers.add_parser("round")
    round_artifact.add_argument("--output-dir", required=True)
    round_artifact.add_argument("--round", required=True, type=int)
    round_artifact.add_argument("--key", required=True, choices=ROUND_ARTIFACTS)
    round_artifact.add_argument("--prefix")
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "artifact":
        path = iterative_artifact_path(args.output_dir, args.key)
    else:
        path = round_artifact_path(
            args.output_dir,
            args.round,
            args.key,
            prefix=args.prefix,
        )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

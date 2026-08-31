#!/usr/bin/env python3
"""One authority for review run directories: location, allocation, layout.

External layout (under state_root()):
    reviews/<kind>/<safe-repo>/<safe-target>/     the *target dir*, cross-run state:
        .branch-review-baseline.json              incremental baseline
        runs/<run-id>/                            one run's artifacts
                                                  (run ids sort lexically; the
                                                  newest-named dir is the latest)

Internal layout (inside a run dir): boundary files at the root, plus
pipeline/ (orchestration state), reviewers/<reviewer>/ (per-reviewer
artifacts, fixed filenames), synthesis/ (reconciliation + critic), tmp/
(sanctioned scratch).

Leaf module: stdlib only.
"""

import argparse
import json
import os
import re
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{4}$")
KEEP_RUNS = 10
KINDS = ("pr", "branch", "iterative")

PIPELINE_SUBDIR = "pipeline"
REVIEWERS_SUBDIR = "reviewers"
SYNTHESIS_SUBDIR = "synthesis"
SCRATCH_SUBDIR = "tmp"
_RUN_SUBDIRS = (PIPELINE_SUBDIR, REVIEWERS_SUBDIR, SYNTHESIS_SUBDIR, SCRATCH_SUBDIR)


def state_root() -> Path:
    override = os.environ.get("PIRATEGOAT_TOOLS_HOME", "")
    if override and os.path.isabs(override):
        return Path(override)
    return Path(os.path.expanduser("~")) / ".pirategoat-tools"


def safe_segment(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "-", text.replace("/", "-"))


def target_dir(kind: str, repo_root: str, target: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"unknown review kind: {kind!r}")
    repo_seg = safe_segment(str(repo_root).lstrip("/"))
    return state_root() / "reviews" / kind / repo_seg / safe_segment(target)


def allocate_run_dir(target: Path) -> Path:
    runs = target / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    while True:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = secrets.token_hex(2)
        same_timestamp = sorted(
            c.name for c in runs.iterdir()
            if c.is_dir() and c.name.startswith(timestamp + "-") and RUN_ID_RE.match(c.name)
        )
        if same_timestamp and suffix <= same_timestamp[-1].rsplit("-", 1)[1]:
            greatest_suffix = same_timestamp[-1].rsplit("-", 1)[1]
            if greatest_suffix == "ffff":
                continue
            suffix = f"{int(greatest_suffix, 16) + 1:04x}"
        run_id = timestamp + "-" + suffix
        run_dir = runs / run_id
        try:
            run_dir.mkdir()
        except FileExistsError:
            continue
        break
    for sub in _RUN_SUBDIRS:
        (run_dir / sub).mkdir()
    prune_runs(target)
    return run_dir


def _run_dirs(target: Path) -> list[Path]:
    runs = target / "runs"
    if not runs.is_dir():
        return []
    return sorted(c for c in runs.iterdir() if c.is_dir() and RUN_ID_RE.match(c.name))


def latest_run_dir(target: Path) -> Path | None:
    children = _run_dirs(target)
    return children[-1] if children else None


def prune_runs(target: Path, keep: int = KEEP_RUNS) -> list[Path]:
    children = _run_dirs(target)
    deleted = children[:-keep] if keep > 0 else children
    for child in deleted:
        shutil.rmtree(child)
    return deleted


def reviewer_dir(run_dir, reviewer: str) -> Path:
    if (
        not isinstance(reviewer, str)
        or not reviewer
        or reviewer in {".", ".."}
        or "/" in reviewer
        or "\\" in reviewer
        or "\x00" in reviewer
    ):
        raise ValueError(f"invalid reviewer identity: {reviewer!r}")
    return Path(run_dir) / REVIEWERS_SUBDIR / reviewer


def pipeline_path(run_dir, name: str) -> Path:
    return Path(run_dir) / PIPELINE_SUBDIR / name


def synthesis_path(run_dir, name: str) -> Path:
    return Path(run_dir) / SYNTHESIS_SUBDIR / name


def scratch_dir(run_dir) -> Path:
    return Path(run_dir) / SCRATCH_SUBDIR


def _cmd_allocate(args) -> int:
    target = target_dir(args.kind, args.repo_root, args.target)
    run_dir = allocate_run_dir(target)
    # The run dir is always freshly created — a directory is one run.
    (run_dir / "run-config.json").write_text(
        json.dumps({"target_dir": str(target)}, indent=2) + "\n"
    )
    print(run_dir)
    return 0


def _cmd_latest(args) -> int:
    run_dir = latest_run_dir(target_dir(args.kind, args.repo_root, args.target))
    if run_dir is None:
        print("NO_RUNS", file=sys.stderr)
        return 1
    print(run_dir)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("allocate", _cmd_allocate), ("latest", _cmd_latest)):
        p = sub.add_parser(name)
        p.add_argument("--kind", required=True, choices=KINDS)
        p.add_argument("--repo-root", required=True)
        p.add_argument("--target", required=True)
        p.set_defaults(handler=handler)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

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
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{4}$")
KEEP_RUNS = 10
KINDS = ("pr", "branch", "iterative")

PIPELINE_SUBDIR = "pipeline"
REVIEWERS_SUBDIR = "reviewers"
SYNTHESIS_SUBDIR = "synthesis"
SCRATCH_SUBDIR = "tmp"
_RUN_SUBDIRS = (PIPELINE_SUBDIR, REVIEWERS_SUBDIR, SYNTHESIS_SUBDIR, SCRATCH_SUBDIR)
_IDENTITY_DIGEST_LENGTH = 12
_READABLE_SEGMENT_LENGTH = 80
_RUN_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_ALLOCATION_LOCK_FILENAME = ".run-allocation.lock"
_ALLOCATION_LOCK_TIMEOUT_SECONDS = 10.0
_ALLOCATION_LOCK_POLL_SECONDS = 0.01


def state_root() -> Path:
    override = os.environ.get("PIRATEGOAT_TOOLS_HOME", "")
    if override and os.path.isabs(override):
        return Path(override)
    return Path(os.path.expanduser("~")) / ".pirategoat-tools"


def safe_segment(text: str) -> str:
    if not isinstance(text, str) or not text or not text.strip("."):
        raise ValueError("path identity must not be empty or dot-only")
    identity = unicodedata.normalize("NFC", text)
    readable = re.sub(r"[^a-zA-Z0-9._-]", "-", identity)
    readable = re.sub(r"-+", "-", readable).strip("-")
    readable = readable[:_READABLE_SEGMENT_LENGTH].rstrip("-") or "identity"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[
        :_IDENTITY_DIGEST_LENGTH
    ]
    return f"{readable}--{digest}"


def target_dir(kind: str, repo_root: str, target: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"unknown review kind: {kind!r}")
    repo_identity = str(repo_root)
    if not repo_identity or not repo_identity.strip("."):
        raise ValueError("path identity must not be empty or dot-only")
    canonical_repo = str(Path(repo_identity).expanduser().resolve())
    base = state_root() / "reviews" / kind
    candidate = base / safe_segment(canonical_repo) / safe_segment(target)
    try:
        candidate.resolve().relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError("review target escapes review state root") from exc
    return candidate


@contextmanager
def _allocation_lock(target: Path):
    target.mkdir(parents=True, exist_ok=True)
    lock_path = target / _ALLOCATION_LOCK_FILENAME
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    deadline = time.monotonic() + _ALLOCATION_LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"timed out waiting for allocator lock: {lock_path}"
                    ) from exc
                time.sleep(min(_ALLOCATION_LOCK_POLL_SECONDS, remaining))
        yield
    finally:
        if acquired:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _next_timestamp(timestamp: str) -> str:
    current = datetime.strptime(timestamp, _RUN_TIMESTAMP_FORMAT)
    return (current + timedelta(seconds=1)).strftime(_RUN_TIMESTAMP_FORMAT)


def _next_run_id(target: Path) -> str:
    children = _run_dirs(target)
    timestamp = datetime.now(timezone.utc).strftime(_RUN_TIMESTAMP_FORMAT)
    if children:
        timestamp = max(timestamp, children[-1].name[:16])
    while True:
        suffixes = [
            int(child.name[-4:], 16)
            for child in children
            if child.name.startswith(timestamp + "-")
        ]
        random_suffix = int(secrets.token_hex(2), 16)
        if not suffixes:
            return f"{timestamp}-{random_suffix:04x}"
        greatest_suffix = max(suffixes)
        if greatest_suffix < 0xFFFF:
            suffix = max(random_suffix, greatest_suffix + 1)
            return f"{timestamp}-{suffix:04x}"
        timestamp = _next_timestamp(timestamp)


def allocate_run_dir(target: Path) -> Path:
    target = Path(target)
    with _allocation_lock(target):
        runs = target / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        while True:
            run_dir = runs / _next_run_id(target)
            try:
                run_dir.mkdir()
            except FileExistsError:
                continue
            try:
                for sub in _RUN_SUBDIRS:
                    (run_dir / sub).mkdir()
                _prune_runs_unlocked(target, KEEP_RUNS)
            except Exception:
                shutil.rmtree(run_dir, ignore_errors=True)
                raise
            return run_dir


def _run_dirs(target: Path) -> list[Path]:
    runs = target / "runs"
    if not runs.is_dir():
        return []
    return sorted(c for c in runs.iterdir() if c.is_dir() and RUN_ID_RE.match(c.name))


def latest_run_dir(target: Path) -> Path | None:
    target = Path(target)
    if not (target / "runs").is_dir():
        return None
    with _allocation_lock(target):
        children = _run_dirs(target)
        return children[-1] if children else None


def _prune_runs_unlocked(target: Path, keep: int) -> list[Path]:
    children = _run_dirs(target)
    deleted = children[:-keep] if keep > 0 else children
    for child in deleted:
        shutil.rmtree(child)
    return deleted


def prune_runs(target: Path, keep: int = KEEP_RUNS) -> list[Path]:
    target = Path(target)
    if not (target / "runs").is_dir():
        return []
    with _allocation_lock(target):
        return _prune_runs_unlocked(target, keep)


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


ARTIFACTS = {
    # key -> (subdir or "" for root, filename). The ONLY place these
    # filenames exist; every consumer resolves through artifact_path.
    "run_config": ("", "run-config.json"),
    "review_context": ("", "review-context.json"),
    "pipeline_result": ("", "pipeline-result.json"),
    "review_report": ("", "review-report.md"),
    "review_record": ("", "review-record.md"),
    "review_findings_json": ("", "review-findings.json"),
    "review_findings_md": ("", "review-findings.md"),
    "pipeline_state": (PIPELINE_SUBDIR, "pipeline-state.json"),
    "review_intake": (PIPELINE_SUBDIR, "review-intake.json"),
    "dispatch_plan": (PIPELINE_SUBDIR, "dispatch-plan.json"),
    "dispatch_plan_initial": (PIPELINE_SUBDIR, "dispatch-plan.initial.json"),
    "change_purpose": (PIPELINE_SUBDIR, "change-purpose.md"),
    "dependency_refresh": (PIPELINE_SUBDIR, "dependency-refresh.json"),
    "synthesis_agents": (PIPELINE_SUBDIR, "synthesis-agents.json"),
    "usage_snapshot": (PIPELINE_SUBDIR, "usage-snapshot.json"),
    "worktree_hygiene": (PIPELINE_SUBDIR, "worktree-hygiene.json"),
    "telemetry_log_path": (PIPELINE_SUBDIR, ".telemetry-log-path"),
    "worktree_baseline": (PIPELINE_SUBDIR, ".worktree-baseline.json"),
    "reconciliation_context": (SYNTHESIS_SUBDIR, "reconciliation-context.json"),
    "critic_adjustments": (SYNTHESIS_SUBDIR, "decision-critic-adjustments.json"),
    "critic_findings": (SYNTHESIS_SUBDIR, "decision-critic-findings.md"),
    "critic_verdict": (SYNTHESIS_SUBDIR, "decision-critic-verdict.json"),
}


def artifact_path(run_dir, key: str) -> Path:
    subdir, name = ARTIFACTS[key]
    base = Path(run_dir) / subdir if subdir else Path(run_dir)
    return base / name


def synthesis_started_marker(run_dir, agent: str) -> Path:
    return Path(run_dir) / SYNTHESIS_SUBDIR / f"{agent}.synthesis-started"


def scratch_dir(run_dir) -> Path:
    return Path(run_dir) / SCRATCH_SUBDIR


def _cmd_allocate(args) -> int:
    target = target_dir(args.kind, args.repo_root, args.target)
    run_dir = allocate_run_dir(target)
    # The run dir is always freshly created — a directory is one run.
    artifact_path(run_dir, "run_config").write_text(
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

"""Layer 2: Full pipeline e2e tests — real Claude CLI.

Spawns the Claude CLI with /pr-review, monitors the JSONL stream
in real-time, fires 15 step-level checkpoints, and validates
final output state.

Each test takes 5-15 minutes and costs $2-5 in API usage.
Run manually or on a weekly schedule, not on every commit.

Prerequisites:
  - Plugin plan executed (scripts + /pr-review command exist)
  - Test repo populated (PRs exist)
  - claude CLI installed and authenticated

Usage:
  # All PRs (slow, expensive):
  pytest tests/e2e/test_pipeline.py -v --timeout=900

  # Single PR:
  pytest tests/e2e/test_pipeline.py -v -k "test_pr1" --timeout=900

  # Quick PRs only (PR1 + PR4):
  pytest tests/e2e/test_pipeline.py -v -k "test_pr1 or test_pr4" --timeout=900
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from assertions import assert_final_state
from checkpoints import build_checkpoints
from expectations import (
    PR1_CLEAN_SMALL,
    PR2_BUGGY_MEDIUM,
    PR3_LARGE,
    PR4_NON_DEFAULT_BRANCH,
)
from stream_monitor import StreamMonitor

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent.parent
PIPELINE_SCRIPT = PLUGIN_ROOT / "scripts" / "pr-review-pipeline.py"


def _skip_if_not_ready():
    """Skip if prerequisites aren't met."""
    if not PIPELINE_SCRIPT.is_file():
        pytest.skip("pr-review-pipeline.py not found — run the plugin plan first")
    result = subprocess.run(
        ["claude", "--version"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("claude CLI not available")


def _run_pr_review(test_repo: str, output_dir: str, pr_number: int, expectations):
    """Run the full /pr-review pipeline and return StreamResult."""
    checkpoints = build_checkpoints(expectations, output_dir)

    # Construct output dir with repo-scoped name.
    owner = "vladolaru"
    repo = "pirategoat-pr-review-pipeline-test-repo"
    scoped_dir = os.path.join(output_dir, f"pr-review-{owner}-{repo}-{pr_number}")
    os.makedirs(scoped_dir, exist_ok=True)

    monitor = StreamMonitor(scoped_dir, checkpoints)
    result = monitor.run(
        claude_args=[
            "claude",
            "--print",
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--model", "sonnet",
        ],
        prompt=f"/pirategoat-tools:pr-review {pr_number}",
        cwd=test_repo,
        timeout=900,
    )

    return result, scoped_dir


@pytest.fixture(autouse=True)
def _check_ready():
    _skip_if_not_ready()


class TestPR1CleanSmall:
    """Clean small PR — expects APPROVE, minimal agent dispatch."""

    def test_pr1_pipeline_completes(self, test_repo, output_dir):
        result, scoped_dir = _run_pr_review(
            test_repo, output_dir, 1, PR1_CLEAN_SMALL,
        )

        # Mid-run checkpoint results.
        for cr in result.checkpoint_results:
            assert cr.passed, (
                f"Checkpoint '{cr.name}' FAILED:\n"
                f"  Trigger: {cr.trigger_event} at T+{cr.timestamp:.1f}s\n"
                f"  Reason: {cr.reason}"
            )

        # Post-run final assertions.
        final = assert_final_state(scoped_dir, PR1_CLEAN_SMALL)
        for ar in final:
            assert ar.passed, f"Final assertion '{ar.name}' FAILED: {ar.reason}"


class TestPR2BuggyMedium:
    """Buggy medium PR — expects REQUEST_CHANGES, security findings."""

    def test_pr2_catches_planted_bugs(self, test_repo, output_dir):
        result, scoped_dir = _run_pr_review(
            test_repo, output_dir, 2, PR2_BUGGY_MEDIUM,
        )

        for cr in result.checkpoint_results:
            assert cr.passed, (
                f"Checkpoint '{cr.name}' FAILED:\n"
                f"  Trigger: {cr.trigger_event} at T+{cr.timestamp:.1f}s\n"
                f"  Reason: {cr.reason}"
            )

        final = assert_final_state(scoped_dir, PR2_BUGGY_MEDIUM)
        for ar in final:
            assert ar.passed, f"Final assertion '{ar.name}' FAILED: {ar.reason}"

        # Extra: verify security-reviewer was dispatched.
        assert "security-reviewer" in result.dispatched_agents


class TestPR3Large:
    """Large PR — expects full agent dispatch, many review files."""

    def test_pr3_full_dispatch(self, test_repo, output_dir):
        result, scoped_dir = _run_pr_review(
            test_repo, output_dir, 3, PR3_LARGE,
        )

        for cr in result.checkpoint_results:
            assert cr.passed, (
                f"Checkpoint '{cr.name}' FAILED:\n"
                f"  Trigger: {cr.trigger_event} at T+{cr.timestamp:.1f}s\n"
                f"  Reason: {cr.reason}"
            )

        final = assert_final_state(scoped_dir, PR3_LARGE)
        for ar in final:
            assert ar.passed, f"Final assertion '{ar.name}' FAILED: {ar.reason}"

        # Extra: verify broad agent dispatch.
        assert len(result.dispatched_agents) >= 8


class TestPR4NonDefaultBranch:
    """Hotfix targeting release/v1 — validates merge-base correctness."""

    def test_pr4_correct_merge_base(self, test_repo, output_dir):
        result, scoped_dir = _run_pr_review(
            test_repo, output_dir, 4, PR4_NON_DEFAULT_BRANCH,
        )

        for cr in result.checkpoint_results:
            assert cr.passed, (
                f"Checkpoint '{cr.name}' FAILED:\n"
                f"  Trigger: {cr.trigger_event} at T+{cr.timestamp:.1f}s\n"
                f"  Reason: {cr.reason}"
            )

        final = assert_final_state(scoped_dir, PR4_NON_DEFAULT_BRANCH)
        for ar in final:
            assert ar.passed, f"Final assertion '{ar.name}' FAILED: {ar.reason}"

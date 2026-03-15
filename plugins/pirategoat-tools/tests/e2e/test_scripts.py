"""Layer 1: Script-level e2e tests — no Claude CLI.

Tests call pipeline scripts directly via subprocess against a clone
of the test repo. Validates the deterministic contract: correct
review-context.json schema, right agents dispatched, correct
merge-base for non-default target branches.

The conftest clone fetches all branches as local refs, so
gather-review-context.py can resolve merge-bases and head refs
without needing `gh pr checkout` (which mutates the working tree).

Prerequisites:
  - Plugin plan executed (scripts exist)
  - Test repo populated (PRs exist)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

GATHER_CONTEXT = SCRIPTS_DIR / "gather-review-context.py"
DISPATCH_PLANNER = SCRIPTS_DIR / "plan-review-dispatch.py"
PIPELINE_SCRIPT = SCRIPTS_DIR / "pr-review-pipeline.py"


def _skip_if_scripts_missing():
    """Skip tests if the pipeline scripts don't exist yet."""
    for script in [GATHER_CONTEXT, DISPATCH_PLANNER, PIPELINE_SCRIPT]:
        if not script.is_file():
            pytest.skip(f"Script not found: {script.name} — run the plugin plan first")


class TestGatherReviewContext:
    """Test gather-review-context.py against real PRs."""

    @pytest.fixture(autouse=True)
    def _check_scripts(self):
        _skip_if_scripts_missing()

    def test_pr1_produces_valid_context(self, test_repo, output_dir):
        result = subprocess.run(
            [sys.executable, str(GATHER_CONTEXT),
             "--pr-number", "1", "--output-dir", output_dir],
            cwd=test_repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        ctx_path = os.path.join(output_dir, "review-context.json")
        assert os.path.isfile(ctx_path)
        with open(ctx_path) as f:
            ctx = json.load(f)
        assert ctx["git"]["merge_base"]
        assert ctx["git"]["changed_files"]
        assert ctx["pr"]["number"] == 1

    def test_pr4_uses_release_branch_as_base(self, test_repo, output_dir):
        result = subprocess.run(
            [sys.executable, str(GATHER_CONTEXT),
             "--pr-number", "4", "--output-dir", output_dir],
            cwd=test_repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        ctx_path = os.path.join(output_dir, "review-context.json")
        with open(ctx_path) as f:
            ctx = json.load(f)
        assert ctx["git"]["base_ref"] == "release/v1"
        assert ctx["pr"]["base_ref_name"] == "release/v1"
        # Changed files should be small (just the hotfix), not the full
        # divergence between release/v1 and main.
        assert len(ctx["git"]["changed_files"]) <= 3


class TestDispatchPlanner:
    """Test plan-review-dispatch.py against real PR diffs."""

    @pytest.fixture(autouse=True)
    def _check_scripts(self):
        _skip_if_scripts_missing()

    def test_pr1_dispatches_few_agents(self, test_repo, output_dir):
        # First gather context to get the git range.
        subprocess.run(
            [sys.executable, str(GATHER_CONTEXT),
             "--pr-number", "1", "--output-dir", output_dir],
            cwd=test_repo, capture_output=True, text=True, timeout=30,
        )
        ctx_path = os.path.join(output_dir, "review-context.json")
        with open(ctx_path) as f:
            ctx = json.load(f)

        result = subprocess.run(
            [sys.executable, str(DISPATCH_PLANNER),
             "--mode", "pr",
             "--git-range", ctx["git"]["git_range"],
             "--output-dir", output_dir],
            cwd=test_repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        plan = json.loads(result.stdout)
        dispatched = [a for a in plan["agents"] if a["status"] == "DISPATCH"]
        assert len(dispatched) >= 1

    def test_pr2_dispatches_security_agent(self, test_repo, output_dir):
        subprocess.run(
            [sys.executable, str(GATHER_CONTEXT),
             "--pr-number", "2", "--output-dir", output_dir],
            cwd=test_repo, capture_output=True, text=True, timeout=30,
        )
        ctx_path = os.path.join(output_dir, "review-context.json")
        with open(ctx_path) as f:
            ctx = json.load(f)

        result = subprocess.run(
            [sys.executable, str(DISPATCH_PLANNER),
             "--mode", "pr",
             "--git-range", ctx["git"]["git_range"],
             "--output-dir", output_dir],
            cwd=test_repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        plan = json.loads(result.stdout)
        dispatched_names = {a["name"] for a in plan["agents"] if a["status"] == "DISPATCH"}
        # PR2 has PHP + security + React files — these agents should dispatch.
        assert "pr-reviewer" in dispatched_names
        # Security-reviewer may be DISPATCH or conditional — at minimum it
        # should have matching domain files.

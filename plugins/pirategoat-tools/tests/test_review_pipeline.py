"""Tests for review-pipeline.py — unified review pipeline."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_fixtures import COMPLETE_CONTEXT

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "review-pipeline.py"
TOTAL_STEPS = 12


def _load_module():
    spec = importlib.util.spec_from_file_location("review_pipeline", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


class TestStepSequence:
    """Universal step sequence is defined correctly."""

    def test_has_12_steps(self, mod):
        assert len(mod.STEP_SEQUENCE) == 12

    def test_step_numbers_are_sequential(self, mod):
        numbers = [s["step"] for s in mod.STEP_SEQUENCE]
        assert numbers == list(range(1, 13))

    def test_all_steps_have_required_fields(self, mod):
        for s in mod.STEP_SEQUENCE:
            assert "step" in s
            assert "title" in s
            assert "phase" in s
            assert "condition" in s


class TestRouting:
    """Mode-driven step routing."""

    def _make_state(self, mode="pr", **overrides):
        state = {
            "completed_steps": [],
            "skipped_steps": [],
            "resolved_params": {
                "has_unfetched_issues": False,
            },
            "workspace": {
                "original_branch": None,
                "stash_ref": None,
            },
            "agents": {
                "dispatched": [],
                "completed": [],
                "failed": [],
            },
            "verdict": None,
        }
        # Handle flat overrides that map to nested structure
        if "has_unfetched_issues" in overrides:
            state["resolved_params"]["has_unfetched_issues"] = overrides.pop("has_unfetched_issues")
        if "original_branch" in overrides:
            state["workspace"]["original_branch"] = overrides.pop("original_branch")
        if "stash_ref" in overrides:
            state["workspace"]["stash_ref"] = overrides.pop("stash_ref")
        state.update(overrides)
        return state

    def _make_config(self, mode="pr", **overrides):
        config = {
            "mode": mode,
            "interactive": True,
        }
        config.update(overrides)
        return config

    def test_pr_mode_active_steps(self, mod):
        """PR mode with pre-computed context: 1,3,5,6,7,8,9,10,11."""
        config = self._make_config("pr")
        state = self._make_state("pr")
        ctx = {"git": {"merge_base": "abc123"}}  # pre-computed
        active = mod.get_active_steps("pr", config, state, ctx)
        # Step 2 skipped (context pre-computed), 4 skipped (no linear), 12 skipped (no workspace state)
        assert 2 not in active
        assert 4 not in active
        assert 7 in active  # baseline written for ALL modes
        assert 12 not in active

    def test_pr_mode_interactive_steps(self, mod):
        """PR mode interactive without pre-computed context: includes step 2."""
        config = self._make_config("pr", interactive=True)
        state = self._make_state("pr")
        ctx = {"git": {}}  # no merge_base = not pre-computed
        active = mod.get_active_steps("pr", config, state, ctx)
        assert 2 in active

    def test_pr_mode_non_interactive_no_context_is_error(self, mod):
        """Non-interactive PR without pre-computed context: step 2 returns hard error."""
        config = self._make_config("pr", interactive=False)
        state = self._make_state("pr")
        ctx = {"git": {}}  # no merge_base = not pre-computed
        # Step 2 should not be in active steps — it's a hard error, not a skip
        active = mod.get_active_steps("pr", config, state, ctx)
        assert 2 not in active

    def test_full_mode_active_steps(self, mod):
        """Full mode: 1,3,5,6,7,8,9,10,11."""
        config = self._make_config("full")
        state = self._make_state("full")
        ctx = {"git": {}}
        active = mod.get_active_steps("full", config, state, ctx)
        assert 2 not in active
        assert 7 in active  # baseline written for ALL modes
        assert 12 not in active

    def test_incremental_mode_has_save_baseline(self, mod):
        """Incremental mode includes step 7 (as does every mode)."""
        config = self._make_config("incremental")
        state = self._make_state("incremental")
        ctx = {"git": {}}
        active = mod.get_active_steps("incremental", config, state, ctx)
        assert 7 in active

    def test_step_7_runs_for_all_modes(self, mod):
        """Step 7 (Save Review Baseline) runs for ALL modes."""
        for mode in ("pr", "full", "incremental"):
            config = self._make_config(mode)
            state = self._make_state(mode)
            ctx = {"git": {}}
            active = mod.get_active_steps(mode, config, state, ctx)
            assert 7 in active, f"Step 7 should be active for {mode} mode"

    def test_linear_issues_activates_step_4(self, mod):
        """Step 4 activates when linear issues are detected."""
        config = self._make_config("full")
        state = self._make_state("full", has_unfetched_issues=True)
        ctx = {"git": {}}
        active = mod.get_active_steps("full", config, state, ctx)
        assert 4 in active

    def test_workspace_state_activates_cleanup_interactive(self, mod):
        """Step 12 activates when original_branch exists AND interactive."""
        config = self._make_config("pr", interactive=True)
        state = self._make_state("pr", original_branch="main")
        ctx = {"git": {}}
        active = mod.get_active_steps("pr", config, state, ctx)
        assert 12 in active

    def test_workspace_state_skips_cleanup_non_interactive(self, mod):
        """Step 12 skipped in non-interactive mode even with workspace state."""
        config = self._make_config("pr", interactive=False)
        state = self._make_state("pr", original_branch="main")
        ctx = {"git": {}}
        active = mod.get_active_steps("pr", config, state, ctx)
        assert 12 not in active


class TestNextStep:
    """Next step computation with skip explanations."""

    def test_consecutive_step(self, mod):
        """When next step is active, return it directly."""
        active = {1, 3, 5, 6, 8, 9, 10, 11}
        result = mod.compute_next_step(5, active)
        assert result["step"] == 6
        assert result.get("skip_reason") is None

    def test_non_consecutive_jump(self, mod):
        """When steps are skipped, jump and explain."""
        active = {1, 3, 5, 6, 8, 9, 10, 11}
        result = mod.compute_next_step(1, active)
        assert result["step"] == 3
        assert result["skip_reason"] is not None

    def test_final_step_returns_none(self, mod):
        """Last active step has no next."""
        active = {1, 3, 5, 6, 8, 9, 10, 11}
        result = mod.compute_next_step(11, active)
        assert result is None


class TestStateManagement:
    """Pipeline state file operations — split into run-config.json and pipeline-state.json."""

    def test_write_and_read_config(self, mod, tmp_path):
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        mod.write_config(str(tmp_path), config)
        loaded = mod.read_config(str(tmp_path))
        assert loaded["mode"] == "pr"
        assert loaded["pr_number"] == "42"

    def test_write_and_read_state(self, mod, tmp_path):
        state = {"current_step": 1, "completed_steps": [], "resolved_params": {}}
        mod.write_state(str(tmp_path), state)
        loaded = mod.read_state(str(tmp_path))
        assert loaded["completed_steps"] == []

    def test_read_missing_state_returns_default(self, mod, tmp_path):
        state = mod.read_state(str(tmp_path))
        assert state["completed_steps"] == []

    def test_read_missing_config_returns_default(self, mod, tmp_path):
        config = mod.read_config(str(tmp_path))
        assert config.get("mode") is None

    def test_state_persists_workspace_params(self, mod, tmp_path):
        state = mod.read_state(str(tmp_path))
        state["workspace"] = {"original_branch": "main", "stash_ref": "abc123"}
        mod.write_state(str(tmp_path), state)
        loaded = mod.read_state(str(tmp_path))
        assert loaded["workspace"]["original_branch"] == "main"
        assert loaded["workspace"]["stash_ref"] == "abc123"

    def test_config_is_never_overwritten(self, mod, tmp_path):
        """run-config.json fields are seeded from CLI on first call and never overwritten."""
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        mod.write_config(str(tmp_path), config)
        loaded = mod.read_config(str(tmp_path))
        assert loaded["mode"] == "pr"
        assert loaded["pr_number"] == "42"

    def test_config_is_source_of_truth_over_cli(self, mod, tmp_path):
        """When run-config.json exists, its values take precedence over CLI args."""
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        mod.write_config(str(tmp_path), config)
        resolved = mod.resolve_params(str(tmp_path), cli_mode="full", cli_pr_number=None)
        assert resolved["mode"] == "pr"  # config wins over CLI


class TestFailureRecovery:
    """Pipeline handles invalid states gracefully."""

    def test_invalid_step_number(self, mod, tmp_path):
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(99, "pr", state, ctx)
        assert g is None

    def test_corrupted_state_file(self, mod, tmp_path):
        """Pipeline survives corrupted pipeline-state.json."""
        (tmp_path / "pipeline-state.json").write_text("not json{{{")
        state = mod.read_state(str(tmp_path))
        assert state["completed_steps"] == []  # returns default

    def test_corrupted_config_file(self, mod, tmp_path):
        """Pipeline survives corrupted run-config.json."""
        (tmp_path / "run-config.json").write_text("not json{{{")
        config = mod.read_config(str(tmp_path))
        assert config.get("mode") is None  # returns default


class TestFormatOutput:
    """Output formatting follows curated-context-pipeline pattern."""

    def test_has_separator_header(self, mod):
        guidance = {
            "phase": "SETUP", "title": "Parse Input",
            "situation": [], "actions": ["Do something."],
            "handoff": None, "next_step": {"step": 3, "title": "Gather Context"},
            "skip_reason": "Step 2 skipped: PR-only (repo setup)",
        }
        output = mod.format_output(1, guidance)
        assert "═══" in output
        assert "Step 1" in output

    def test_has_next_pointer(self, mod):
        guidance = {
            "phase": "SETUP", "title": "Parse Input",
            "situation": [], "actions": ["Do something."],
            "handoff": None, "next_step": {"step": 3, "title": "Gather Context"},
            "skip_reason": None,
        }
        output = mod.format_output(1, guidance)
        assert "Step 3" in output
        assert "Gather Context" in output

    def test_skip_explanation_in_output(self, mod):
        guidance = {
            "phase": "SETUP", "title": "Parse Input",
            "situation": [], "actions": ["Do something."],
            "handoff": None, "next_step": {"step": 3, "title": "Gather Context"},
            "skip_reason": "Step 2 skipped: context already pre-computed",
        }
        output = mod.format_output(1, guidance)
        assert "pre-computed" in output

    def test_final_step_shows_complete(self, mod):
        guidance = {
            "phase": "OUTPUT", "title": "Present Results",
            "situation": [], "actions": ["Show results."],
            "handoff": None, "next_step": None,
            "skip_reason": None,
        }
        output = mod.format_output(11, guidance)
        assert "COMPLETE" in output

    def test_handoff_section(self, mod):
        guidance = {
            "phase": "SETUP", "title": "Gather Context",
            "situation": [], "actions": ["Run script."],
            "handoff": ["Write change purpose to /tmp/change-purpose.md"],
            "next_step": {"step": 5, "title": "Dispatch Plan"},
            "skip_reason": None,
        }
        output = mod.format_output(3, guidance)
        assert "HANDOFF" in output
        assert "change-purpose.md" in output


class TestCLIIntegration:
    """Subprocess tests for the CLI."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_1_pr_mode_exits_0(self, tmp_path):
        r = self._run("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path), "--pr-number", "42")
        assert r.returncode == 0

    def test_step_1_full_mode_exits_0(self, tmp_path):
        r = self._run("--step", "1", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0

    def test_step_1_incremental_mode_exits_0(self, tmp_path):
        r = self._run("--step", "1", "--mode", "incremental",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0

    def test_invalid_step_exits_1(self, tmp_path):
        r = self._run("--step", "99", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 1

    def test_missing_mode_exits_error(self, tmp_path):
        r = self._run("--step", "1", "--output-dir", str(tmp_path))
        assert r.returncode != 0

    def test_writes_run_config_and_pipeline_state(self, tmp_path):
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        config_path = tmp_path / "run-config.json"
        state_path = tmp_path / "pipeline-state.json"
        assert config_path.is_file()
        assert state_path.is_file()
        config = json.loads(config_path.read_text())
        assert config["mode"] == "pr"
        assert config["pr_number"] == "42"

    def test_workspace_params_persisted_to_state(self, tmp_path):
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42",
                   "--original-branch", "develop", "--stash-ref", "abc123")
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["workspace"]["original_branch"] == "develop"
        assert state["workspace"]["stash_ref"] == "abc123"

    def test_mode_from_config_on_subsequent_steps(self, tmp_path):
        """Step 2+ reads mode from run-config.json, not CLI."""
        # Step 1 seeds the config
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        # Step 3 passes wrong mode on CLI — config should win
        r = self._run("--step", "3", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["mode"] == "pr"  # config wins

    def test_mode_required_when_no_config(self, tmp_path):
        """First call must provide --mode."""
        r = self._run("--step", "1", "--output-dir", str(tmp_path))
        assert r.returncode != 0

    def test_step_1_clears_stale_artifacts(self, tmp_path):
        """Step 1 should clear stale artifacts from previous runs."""
        # Seed stale artifacts
        (tmp_path / "pipeline-state.json").write_text('{"stale": true}')
        (tmp_path / "dispatch-plan.json").write_text('{"stale": true}')
        (tmp_path / "pr-review.json").write_text('{"stale": true}')
        (tmp_path / "review-findings.json").write_text('{"stale": true}')
        (tmp_path / "review-findings.md").write_text("stale")
        (tmp_path / "review-report.md").write_text("stale")
        (tmp_path / "review-verdict.json").write_text('{"stale": true}')
        (tmp_path / "pipeline-result.json").write_text('{"stale": true}')
        (tmp_path / "decision-critic-findings.md").write_text("stale")
        # Seed files that should be PRESERVED
        (tmp_path / "run-config.json").write_text('{"mode": "pr", "pr_number": "42"}')
        (tmp_path / ".branch-review-baseline.json").write_text('{"preserved": true}')
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        # Stale artifacts should be gone
        assert not (tmp_path / "dispatch-plan.json").exists()
        assert not (tmp_path / "review-findings.json").exists()
        assert not (tmp_path / "review-report.md").exists()
        assert not (tmp_path / "review-verdict.json").exists()
        assert not (tmp_path / "pipeline-result.json").exists()
        # Preserved files should still exist
        assert (tmp_path / "run-config.json").is_file()
        assert (tmp_path / ".branch-review-baseline.json").is_file()

    def test_step_1_preserves_review_context(self, tmp_path):
        """Step 1 should preserve review-context.json — gather-review-context.py overwrites it at step 3."""
        (tmp_path / "review-context.json").write_text('{"output": {"directory": "/some/path"}}')
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        assert (tmp_path / "review-context.json").is_file(), "review-context.json should be preserved for incremental baseline lookup"

    def test_step_1_clears_change_purpose(self, tmp_path):
        """Step 1 should clear stale change-purpose.md from previous runs."""
        (tmp_path / "change-purpose.md").write_text("Old change purpose from previous review.")
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        assert not (tmp_path / "change-purpose.md").exists(), "Stale change-purpose.md should be cleared"

    def test_step_1_writes_run_id(self, tmp_path):
        """Step 1 should write a run_id to pipeline-state.json."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "run_id" in state
        assert len(state["run_id"]) > 0


class TestTelemetryIntegration:
    """Verify pipeline calls telemetry at each step."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_1_creates_telemetry_log(self, tmp_path):
        """Step 1 should create a telemetry log file."""
        log_dir = tmp_path / "telemetry-logs"
        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            r = self._run(
                "--step", "1", "--mode", "pr",
                "--output-dir", str(tmp_path), "--pr-number", "42",
            )
        assert r.returncode == 0
        marker = tmp_path / ".telemetry-log-path"
        assert marker.is_file()

    def test_telemetry_failure_does_not_break_pipeline(self, tmp_path):
        """Pipeline works even if telemetry log_dir is unwritable."""
        log_dir = tmp_path / "unwritable"
        log_dir.mkdir()
        log_dir.chmod(0o000)
        try:
            with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
                r = self._run(
                    "--step", "1", "--mode", "pr",
                    "--output-dir", str(tmp_path), "--pr-number", "42",
                )
            assert r.returncode == 0
            assert "Step 1" in r.stdout
        finally:
            log_dir.chmod(0o755)

    def test_step_2_appends_to_telemetry_log(self, tmp_path):
        """Subsequent steps append to the log created by step 1."""
        log_dir = tmp_path / "telemetry-logs"
        env = {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}
        with patch.dict(os.environ, env):
            self._run("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path), "--pr-number", "42")
            self._run("--step", "3", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        marker = tmp_path / ".telemetry-log-path"
        log_path = marker.read_text().strip()
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "pipeline_start"
        assert json.loads(lines[1])["event"] == "step"


class TestStep3Orchestration:
    """Step 3 main() runs gather-review-context.py and hydrates state."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_3_runs_gather_context(self, tmp_path):
        """Step 3 should invoke gather-review-context.py (may fail in test env, but state should update)."""
        # Seed step 1
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        # Run step 3
        r = self._run("--step", "3", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        # State should have completed_steps including 3
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert 3 in state["completed_steps"]

    def test_step_3_hydrates_unfetched_issues_from_context(self, tmp_path):
        """When review-context.json has has_unfetched_issues, state should reflect it."""
        # Seed step 1
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        # Pre-write review-context.json as if gather-review-context.py produced it
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
            "has_unfetched_issues": True,
            "linked_issues": ["WOOPLUG-1234"],
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        # Run step 3 — it should read the context and hydrate state
        r = self._run("--step", "3", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["resolved_params"]["has_unfetched_issues"] is True

    def test_step_3_without_context_still_succeeds(self, tmp_path):
        """Step 3 should not crash if gather-review-context.py fails (no git repo)."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        r = self._run("--step", "3", "--mode", "full",
                       "--output-dir", str(tmp_path))
        # Should succeed even without a git repo — subprocess failure is tolerated
        assert r.returncode == 0

    def test_step_3_next_step_reflects_unfetched_issues(self, tmp_path):
        """When has_unfetched_issues is True, next step after 3 should be 4 (not 5)."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
            "has_unfetched_issues": True,
            "linked_issues": ["WOOPLUG-1234"],
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "3", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        # Output should point to step 4, not step 5
        assert "Step 4" in r.stdout


class TestStep5Orchestration:
    """Step 5 main() runs plan-review-dispatch.py and stores output in state."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_5_stores_dispatch_plan_output(self, tmp_path):
        """Step 5 should store dispatch plan output in state."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "5", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert 5 in state["completed_steps"]
        # dispatch_plan_output may be empty if planner fails, but key should exist
        assert "dispatch_plan_output" in state or "dispatch_plan_summary" in state


class TestStep6Orchestration:
    """Step 6 main() reads dispatch-plan.json and populates dispatched_agents."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_6_populates_dispatched_agents(self, tmp_path):
        """Step 6 should read dispatch-plan.json and populate state.dispatched_agents."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        plan = {
            "agents": [
                {"name": "pr-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "always"},
                {"name": "go-tests-reviewer", "domain": "go-tests", "status": "SKIPPED", "reason": "no files"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "6", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        names = [a["name"] for a in state.get("dispatched_agents", [])]
        assert "pr-reviewer" in names
        assert "security-reviewer" in names
        assert "go-tests-reviewer" not in names

    def test_step_6_output_contains_bootstrap_calls(self, tmp_path):
        """Step 6 output should contain concrete bootstrap-reviewer.py calls."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        plan = {
            "agents": [
                {"name": "pr-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "6", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert "bootstrap-reviewer.py" in r.stdout
        assert "pr-reviewer" in r.stdout
        assert "abc..HEAD" in r.stdout


class TestStep7Orchestration:
    """Step 7 main() writes .branch-review-baseline.json."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_7_writes_baseline_file(self, tmp_path):
        """Step 7 should create .branch-review-baseline.json."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "7", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        baseline_path = tmp_path / ".branch-review-baseline.json"
        assert baseline_path.is_file(), "Baseline file was not created"
        baseline = json.loads(baseline_path.read_text())
        assert "last_reviewed_sha" in baseline
        assert "last_reviewed_at" in baseline
        assert "review_type" in baseline
        assert baseline["review_type"] == "full"
        assert "git_range_used" in baseline
        assert ".." in baseline["git_range_used"]

    def test_step_7_baseline_grades_clean(self, tmp_path):
        """The written baseline should pass the grader."""
        from graders import grade_review_baseline
        self._run("--step", "1", "--mode", "incremental",
                   "--output-dir", str(tmp_path))
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        self._run("--step", "7", "--mode", "incremental",
                   "--output-dir", str(tmp_path))
        baseline_path = tmp_path / ".branch-review-baseline.json"
        result = grade_review_baseline(str(baseline_path))
        assert result.passed, f"Baseline grading failed: {result.failures}"


class TestStep8Orchestration:
    """Step 8 main() reads change-purpose.md and agent completion status."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_8_reads_change_purpose(self, tmp_path):
        """Step 8 should read change-purpose.md into state."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        (tmp_path / "change-purpose.md").write_text("Adds retry logic to payment gateway.")
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "8", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "retry logic" in state.get("change_purpose", "").lower()

    def test_step_8_stores_review_file_paths(self, tmp_path):
        """Step 8 should store paths to completed review files in state."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        plan = {
            "agents": [
                {"name": "pr-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "always"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        # Simulate pr-reviewer finished, security-reviewer not
        (tmp_path / "pr-review.json").write_text('{"verdict": "approve", "issues": []}')
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "8", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        review_files = state.get("agents", {}).get("review_files", [])
        assert any("pr-review.json" in f for f in review_files)


class TestStep11Orchestration:
    """Step 11 main() reads review-verdict.json and writes pipeline-result.json."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_11_writes_pipeline_result(self, tmp_path):
        """Step 11 should write pipeline-result.json."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        (tmp_path / "review-verdict.json").write_text('{"verdict": "REQUEST_CHANGES"}')
        (tmp_path / "review-report.md").write_text("# Review Report\nFindings here.")
        (tmp_path / "review-findings.json").write_text('{"verdict": "COMMENT", "issues": []}')
        r = self._run("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        result_path = tmp_path / "pipeline-result.json"
        assert result_path.is_file(), "pipeline-result.json was not created"
        result = json.loads(result_path.read_text())
        assert result["verdict"] == "REQUEST_CHANGES"
        assert result["status"] in ("success", "degraded")
        assert "report_path" in result

    def test_step_11_updates_findings_verdict(self, tmp_path):
        """Step 11 should update review-findings.json verdict to match review-verdict.json (rule 23)."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        (tmp_path / "review-verdict.json").write_text('{"verdict": "REQUEST_CHANGES"}')
        (tmp_path / "review-report.md").write_text("# Review")
        (tmp_path / "review-findings.json").write_text('{"verdict": "COMMENT", "issues": []}')
        self._run("--step", "11", "--mode", "pr",
                   "--output-dir", str(tmp_path))
        findings = json.loads((tmp_path / "review-findings.json").read_text())
        assert findings["verdict"] == "REQUEST_CHANGES"

    def test_step_11_handles_missing_verdict(self, tmp_path):
        """Step 11 should handle missing review-verdict.json gracefully."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        r = self._run("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        result_path = tmp_path / "pipeline-result.json"
        assert result_path.is_file()
        result = json.loads(result_path.read_text())
        assert result["status"] in ("degraded", "failed")

    def test_step_11_degrades_when_findings_missing(self, tmp_path):
        """Step 11 should report degraded when review-findings.json is missing (partial run)."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        # Verdict and report exist, but findings do not (reconciliation failed)
        (tmp_path / "review-verdict.json").write_text('{"verdict": "COMMENT"}')
        (tmp_path / "review-report.md").write_text("# Review\nReport here.")
        r = self._run("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert any("review-findings.json" in n for n in result["degradation_notes"])


class TestTelemetryFinalize:
    """Telemetry finalize is called at the last active step."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_last_step_finalizes_telemetry(self, tmp_path):
        """The last active step should call telemetry.finalize()."""
        log_dir = tmp_path / "telemetry-logs"
        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            self._run("--step", "1", "--mode", "full",
                       "--output-dir", str(tmp_path))
            # Step 11 is the last active step for non-interactive full mode
            # (step 12 needs workspace + interactive)
            self._run("--step", "11", "--mode", "full",
                       "--output-dir", str(tmp_path))
        marker = tmp_path / ".telemetry-log-path"
        if marker.is_file():
            log_path = marker.read_text().strip()
            with open(log_path) as f:
                lines = f.readlines()
            events = [json.loads(l)["event"] for l in lines]
            assert "pipeline_end" in events, f"Expected pipeline_end event, got: {events}"


class TestStep8AgentPrompt:
    """Step 8 should emit a complete reconciliator Agent tool prompt (rule 15)."""

    def test_reconciliator_prompt_has_concrete_values(self, mod, tmp_path):
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": ["pr-reviewer", "security-reviewer"],
                "completed": ["pr-reviewer", "security-reviewer"],
                "failed": [],
            },
            "change_purpose": "Adds retry logic.",
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py,b.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "abc..HEAD" in text  # concrete range
        assert "a.py" in text or "changed_files_csv" in text.lower() or "dispatch-plan.json" in text


class TestStep10AgentPrompt:
    """Step 10 should emit a complete decision critic Agent tool prompt (rule 15)."""

    def test_critic_prompt_has_concrete_path(self, mod, tmp_path):
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert str(tmp_path) in text or "review-report.md" in text


class TestFullSequenceIntegration:
    """Full multi-step sequence produces pipeline-result.json."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_full_sequence_produces_pipeline_result(self, tmp_path):
        """Run steps 1,3,5,6,7,8,11 in order — pipeline-result.json should exist."""
        od = str(tmp_path)
        # Step 1: seed
        r = self._run("--step", "1", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0

        # Pre-write context as if gather-review-context.py succeeded
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "changed_files_csv": "a.py",
                    "commit_count": 1, "base_ref": "main"},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))

        # Step 3: gather context (reads the pre-written file)
        r = self._run("--step", "3", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0

        # Step 5: dispatch plan (may fail without git, but should not crash)
        r = self._run("--step", "5", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0

        # Pre-write dispatch plan as if planner succeeded
        plan = {"agents": [{"name": "pr-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"}], "git_range": "abc..HEAD"}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        # Step 6: dispatch agents
        r = self._run("--step", "6", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0

        # Step 7: save baseline
        r = self._run("--step", "7", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0
        assert (tmp_path / ".branch-review-baseline.json").is_file()

        # Step 8: reconcile (no review files exist — that's OK)
        r = self._run("--step", "8", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0

        # Pre-write verdict, report, and findings as if steps 8-10 ran
        (tmp_path / "review-verdict.json").write_text('{"verdict": "APPROVE"}')
        (tmp_path / "review-report.md").write_text("# Review\nAll clear.")
        (tmp_path / "review-findings.json").write_text('{"verdict": "APPROVE", "issues": []}')

        # Step 11: present results
        r = self._run("--step", "11", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0
        assert (tmp_path / "pipeline-result.json").is_file()
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["verdict"] == "APPROVE"
        assert result["status"] == "success"
        assert result["review_baseline_saved"] is True


# ===================================================================
# SETUP Phase Tests (Steps 1-3)
# ===================================================================


class TestStep1ParseInput:
    """Step 1: Parse Input — all modes."""

    def test_pr_mode_confirms_pr_number(self, mod, tmp_path):
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(1, "pr", state, ctx, config=config)
        text = "\n".join(g["situation"] + g["actions"])
        assert "42" in text

    def test_pr_mode_stops_when_no_pr(self, mod, tmp_path):
        config = {"mode": "pr", "pr_number": None, "interactive": True}
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(1, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "usage" in text.lower() or "required" in text.lower()

    def test_full_mode_detects_branch(self, mod, tmp_path):
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(1, "full", state, ctx)
        text = "\n".join(g["actions"])
        assert "branch" in text.lower() or "range" in text.lower()

    def test_incremental_mode_mentions_state(self, mod, tmp_path):
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(1, "incremental", state, ctx)
        text = "\n".join(g["actions"])
        assert "incremental" in text.lower()

    def test_full_mode_stops_on_default_branch(self, mod, tmp_path):
        """Step 1 should error when on the default branch (full mode)."""
        state = {"completed_steps": []}
        ctx = {"on_default_branch": True}
        g = mod.get_step_guidance(1, "full", state, ctx)
        text = "\n".join(g["actions"])
        assert "default branch" in text.lower() or "STOPPED" in text

    def test_incremental_mode_stops_on_no_new_commits(self, mod, tmp_path):
        """Step 1 should mention no-new-commits guard for incremental mode."""
        state = {"completed_steps": []}
        ctx = {"no_new_commits": True}
        g = mod.get_step_guidance(1, "incremental", state, ctx)
        text = "\n".join(g["actions"])
        assert "no new commits" in text.lower() or "STOPPED" in text


class TestStep2RepoSetup:
    """Step 2: Repo Setup — PR mode + interactive only."""

    def test_instructs_stash_and_checkout(self, mod, tmp_path):
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        state = {"completed_steps": [1]}
        ctx = {"git": {}}
        g = mod.get_step_guidance(2, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "stash" in text.lower()
        assert "checkout" in text.lower()

    def test_instructs_passing_workspace_state(self, mod, tmp_path):
        """Should tell LLM to pass --original-branch and --stash-ref."""
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        state = {"completed_steps": [1]}
        ctx = {"git": {}}
        g = mod.get_step_guidance(2, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "--original-branch" in text
        assert "--stash-ref" in text


class TestStep3GatherContext:
    """Step 3: Gather Context — all modes, curated briefing."""

    def _make_context(self):
        """Return a rich review-context.json content."""
        return COMPLETE_CONTEXT

    def test_presents_git_range(self, mod, tmp_path):
        state = {"completed_steps": [1, 2]}
        ctx = self._make_context()
        g = mod.get_step_guidance(3, "pr", state, ctx)
        text = "\n".join(g["situation"])
        assert "abc123..fix/thing" in text

    def test_presents_size(self, mod, tmp_path):
        state = {"completed_steps": [1, 2]}
        ctx = self._make_context()
        g = mod.get_step_guidance(3, "pr", state, ctx)
        text = "\n".join(g["situation"])
        assert "small" in text.lower() or "2 files" in text

    def test_presents_pr_metadata_in_pr_mode(self, mod, tmp_path):
        state = {"completed_steps": [1, 2]}
        ctx = self._make_context()
        g = mod.get_step_guidance(3, "pr", state, ctx)
        text = "\n".join(g["situation"])
        assert "Fix the thing" in text  # PR title
        assert "octocat" in text  # PR author

    def test_no_pr_metadata_in_full_mode(self, mod, tmp_path):
        state = {"completed_steps": [1]}
        ctx = {"git": {"merge_base": "abc", "git_range": "abc..HEAD",
                       "changed_files": ["a.py"], "commit_count": 3},
               "pr_size": {"files": 1, "lines": 20, "category": "tiny"}}
        g = mod.get_step_guidance(3, "full", state, ctx)
        text = "\n".join(g["situation"])
        assert "octocat" not in text

    def test_handoff_change_purpose_when_no_linear(self, mod, tmp_path):
        """When no Linear issues, step 3 requests the change-purpose handoff."""
        state = {"resolved_params": {"has_unfetched_issues": False}, "completed_steps": [1]}
        ctx = {"git": {"merge_base": "abc", "git_range": "abc..HEAD",
                       "changed_files": ["a.py"], "commit_count": 3},
               "pr_size": {"files": 1, "lines": 20, "category": "tiny"}}
        g = mod.get_step_guidance(3, "full", state, ctx)
        assert g["handoff"] is not None
        text = "\n".join(g["handoff"])
        assert "change-purpose.md" in text

    def test_no_handoff_when_linear_issues(self, mod, tmp_path):
        """When Linear issues detected, step 3 defers handoff to step 4."""
        state = {"resolved_params": {"has_unfetched_issues": True}, "completed_steps": [1, 2]}
        ctx = self._make_context()
        g = mod.get_step_guidance(3, "pr", state, ctx)
        assert g["handoff"] is None

    def test_presents_staleness(self, mod, tmp_path):
        """Step 3 should present stale branch info when present."""
        state = {"completed_steps": [1]}
        ctx = {"git": {"merge_base": "abc", "git_range": "abc..HEAD",
                       "changed_files": ["a.py"], "commit_count": 3},
               "pr_size": {"files": 1, "lines": 20, "category": "tiny"},
               "staleness": {"is_stale": True, "commits_behind": 47}}
        g = mod.get_step_guidance(3, "full", state, ctx)
        text = "\n".join(g["situation"])
        assert "47" in text or "behind" in text.lower()

    def test_presents_reviews_summary_in_pr_mode(self, mod, tmp_path):
        """PR mode should present existing review summary in situation."""
        state = {"completed_steps": [1, 2]}
        ctx = self._make_context()
        g = mod.get_step_guidance(3, "pr", state, ctx)
        text = "\n".join(g["situation"])
        assert "approved" in text.lower() or "review" in text.lower()

    def test_presents_linked_issues(self, mod, tmp_path):
        """Should present linked issue details in situation."""
        state = {"completed_steps": [1, 2]}
        ctx = self._make_context()
        g = mod.get_step_guidance(3, "pr", state, ctx)
        text = "\n".join(g["situation"])
        assert "WOOPLUG-1234" in text or "issue" in text.lower()

    def test_presents_domain_counts(self, mod, tmp_path):
        """Should present changed domain file counts in situation."""
        state = {"completed_steps": [1, 2]}
        ctx = self._make_context()
        g = mod.get_step_guidance(3, "pr", state, ctx)
        text = "\n".join(g["situation"])
        assert "domain" in text.lower() or "code" in text.lower()

    def test_presents_freshen_base_when_stale(self, mod, tmp_path):
        """Should suggest freshening base branch when stale."""
        state = {"completed_steps": [1]}
        ctx = {"git": {"merge_base": "abc", "git_range": "abc..HEAD",
                       "changed_files": ["a.py"], "commit_count": 3},
               "pr_size": {"files": 1, "lines": 20, "category": "tiny"},
               "staleness": {"is_stale": True, "commits_behind": 47}}
        g = mod.get_step_guidance(3, "full", state, ctx)
        text = "\n".join(g["situation"])
        assert "rebase" in text.lower() or "freshen" in text.lower() or "behind" in text.lower()

    def test_no_template_variables(self, mod, tmp_path):
        state = {"completed_steps": [1, 2]}
        ctx = self._make_context()
        g = mod.get_step_guidance(3, "pr", state, ctx)
        all_text = "\n".join(g["situation"] + g["actions"])
        assert "${" not in all_text
        assert "<GIT_RANGE>" not in all_text
        assert "<OUTPUT_DIR>" not in all_text


# ===================================================================
# Steps 4-6 Tests
# ===================================================================


class TestStep4FetchLinearIssues:
    """Step 4: Fetch Issue Context — data-driven condition."""

    def test_instructs_linear_mcp(self, mod, tmp_path):
        state = {"resolved_params": {"has_unfetched_issues": True}, "completed_steps": [1, 2, 3]}
        ctx = COMPLETE_CONTEXT
        g = mod.get_step_guidance(4, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "linear" in text.lower() or "Linear" in text

    def test_has_change_purpose_handoff(self, mod, tmp_path):
        """Step 4 should include the change-purpose handoff (deferred from step 3)."""
        state = {"resolved_params": {"has_unfetched_issues": True}, "completed_steps": [1, 2, 3]}
        ctx = COMPLETE_CONTEXT
        g = mod.get_step_guidance(4, "pr", state, ctx)
        assert g["handoff"] is not None
        text = "\n".join(g["handoff"])
        assert "change-purpose.md" in text


class TestStep5DispatchPlan:
    """Step 5: Dispatch Plan + Triage. main() runs planner, passes output to get_step_guidance()."""

    def _make_state_with_plan(self):
        """State with planner output pre-computed by main()."""
        return {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3],
            "dispatch_plan_output": "pr-reviewer: DISPATCH (domain: code)\nsecurity-reviewer: SKIPPED (no files in security domain)",
            "dispatch_plan_summary": {"dispatched": 7, "skipped": 3, "conditional": 2},
        }

    def test_presents_dispatch_plan_output(self, mod, tmp_path):
        state = self._make_state_with_plan()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(5, "pr", state, ctx)
        text = "\n".join(g["actions"] + g["situation"])
        # Script presents planner output in delimited section
        assert "DISPATCH PLAN" in text
        # Planner output should NOT instruct the LLM to run the command
        assert not ("python3" in text and "plan-review-dispatch.py" in text)

    def test_triage_authority(self, mod, tmp_path):
        """Triage model should be consistent: planner is authoritative."""
        state = self._make_state_with_plan()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(5, "full", state, ctx)
        text = "\n".join(g["actions"])
        assert "authoritative" in text.lower() or "override" in text.lower()
        assert "preliminary" not in text.lower()

    def test_override_writes_to_dispatch_plan(self, mod, tmp_path):
        state = self._make_state_with_plan()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(5, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "dispatch-plan.json" in text
        assert "DISPATCH_OVERRIDE" in text
        assert "SKIPPED_OVERRIDE" in text


class TestStep6DispatchAgents:
    """Step 6: Dispatch Agents. main() reads dispatch-plan.json, passes agent list."""

    def _make_state_with_agents(self, output_dir="/tmp/review-42"):
        """State with dispatched agents pre-computed by main()."""
        return {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3, 5],
            "dispatched_agents": [
                {"name": "pr-reviewer", "domain": "code"},
                {"name": "security-reviewer", "domain": "security"},
            ],
        }

    def test_parallel_dispatch(self, mod, tmp_path):
        state = self._make_state_with_agents()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(6, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "parallel" in text.lower() or "SINGLE message" in text

    def test_references_bootstrap(self, mod, tmp_path):
        state = self._make_state_with_agents()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(6, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "bootstrap-reviewer.py" in text

    def test_lists_each_agent_dispatch_call(self, mod, tmp_path):
        """Should list each agent's full dispatch call with concrete values."""
        state = self._make_state_with_agents()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(6, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "pr-reviewer" in text
        assert "security-reviewer" in text
        assert "abc..HEAD" in text  # concrete range, not template

    def test_references_agent_tool(self, mod, tmp_path):
        """Should reference Agent tool, not Task tool."""
        state = self._make_state_with_agents()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(6, "full", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "Agent tool" in text or "Agent" in text
        assert "Task tool" not in text

    def test_references_status_check(self, mod, tmp_path):
        """Should reference check-reviewer-agent-status.py for monitoring."""
        state = self._make_state_with_agents()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(6, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "check-reviewer-agent-status.py" in text


# ===================================================================
# SYNTHESIS Phase Tests (Steps 7-9)
# ===================================================================


class TestStep7SaveReviewBaseline:
    def test_confirms_baseline_saved(self, mod, tmp_path):
        """Step 7 confirms the file was written (script writes it internally). Runs for ALL modes."""
        for mode in ("pr", "full", "incremental"):
            state = {"resolved_params": {"git_range": "abc..HEAD"}, "completed_steps": []}
            ctx = {"git": {"git_range": "abc..HEAD"}}
            g = mod.get_step_guidance(7, mode, state, ctx)
            text = "\n".join(g["situation"] + g["actions"])
            assert ".branch-review-baseline.json" in text
            assert "saved" in text.lower() or "baseline" in text.lower()
            # Should NOT instruct the LLM to write the file
            assert "cat >" not in text
            assert "STATEEOF" not in text

    def test_incremental_mode_mentions_next_review(self, mod, tmp_path):
        """Incremental mode briefing mentions next review will only cover new commits."""
        state = {"resolved_params": {"git_range": "abc..HEAD"}, "completed_steps": []}
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(7, "incremental", state, ctx)
        text = "\n".join(g["situation"] + g["actions"])
        assert "new commits" in text.lower() or "only cover" in text.lower()

    def test_full_mode_mentions_baseline(self, mod, tmp_path):
        """Full mode briefing mentions baseline saved for future incremental reviews."""
        state = {"resolved_params": {"git_range": "abc..HEAD"}, "completed_steps": []}
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(7, "full", state, ctx)
        text = "\n".join(g["situation"] + g["actions"])
        assert "baseline" in text.lower()

    def test_step_7_instructs_checking_agent_status(self, mod, tmp_path):
        """Step 7 should instruct checking agent completion before proceeding."""
        state = {"completed_steps": [], "resolved_params": {"git_range": "abc..HEAD"}}
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        g = mod.get_step_guidance(7, "full", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "check-reviewer-agent-status" in text or "agent status" in text.lower()


class TestStep8Reconcile:
    """Step 8: Reconcile + Verify. main() reads dispatch-plan.json + review files, passes to get_step_guidance()."""

    def _make_state_with_agents(self, change_purpose_exists=False):
        return {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": ["pr-reviewer", "security-reviewer", "performance-reviewer"],
                "completed": ["pr-reviewer", "security-reviewer"],
                "failed": [],
            },
            "change_purpose": "Adds retry logic to the payment gateway." if change_purpose_exists else None,
            "commit_messages": ["feat: add payment retry logic", "test: add retry tests"],
        }

    def test_dispatches_reconciliator(self, mod, tmp_path):
        state = self._make_state_with_agents(change_purpose_exists=True)
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py,b.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "review-reconciliator" in text

    def test_presents_agent_completion_summary(self, mod, tmp_path):
        """Should show which agents completed, missing, failed."""
        state = self._make_state_with_agents(change_purpose_exists=True)
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["situation"])
        assert "pr-reviewer" in text
        assert "security-reviewer" in text

    def test_includes_dispatch_plan_path(self, mod, tmp_path):
        """All modes should pass dispatch plan to reconciliator."""
        for mode in ("pr", "full", "incremental"):
            state = self._make_state_with_agents(change_purpose_exists=True)
            ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
            g = mod.get_step_guidance(8, mode, state, ctx, output_dir=str(tmp_path))
            text = "\n".join(g["actions"])
            assert "dispatch-plan.json" in text

    def test_includes_change_purpose_when_available(self, mod, tmp_path):
        """Should include change purpose in reconciliator prompt."""
        state = self._make_state_with_agents(change_purpose_exists=True)
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["situation"] + g["actions"])
        assert "retry logic" in text.lower() or "change purpose" in text.lower()

    def test_change_purpose_fallback_when_missing(self, mod, tmp_path):
        """When change-purpose.md is missing, script provides fallback from commits."""
        state = self._make_state_with_agents(change_purpose_exists=False)
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["situation"] + g["actions"])
        assert "commit" in text.lower() or "derive" in text.lower()

    def test_instructs_stopping_background_agents(self, mod, tmp_path):
        """Step 8 should instruct stopping remaining background agents first."""
        state = self._make_state_with_agents(change_purpose_exists=True)
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "stop" in text.lower() or "TaskStop" in text

    def test_reconciliator_prompt_lists_review_files(self, mod, tmp_path):
        """Step 8 should list completed review file paths in the reconciliator prompt."""
        state = self._make_state_with_agents(change_purpose_exists=True)
        state["agents"]["review_files"] = [
            "/tmp/out/pr-review.json",
            "/tmp/out/security-review.json",
        ]
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "pr-review.json" in text
        assert "security-review.json" in text


class TestStep9ReviewReport:
    def test_writes_review_report(self, mod, tmp_path):
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(9, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "review-report.md" in text

    def test_references_review_findings(self, mod, tmp_path):
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(9, "full", state, ctx)
        text = "\n".join(g["actions"])
        assert "review-findings" in text

    def test_all_modes_have_this_step(self, mod, tmp_path):
        """Review report synthesis runs for ALL modes (fixes branch flow gap)."""
        for mode in ("pr", "full", "incremental"):
            state = {"completed_steps": []}
            ctx = {}
            g = mod.get_step_guidance(9, mode, state, ctx)
            assert g is not None
            text = "\n".join(g["actions"])
            assert "review-report.md" in text

    def test_includes_output_instructions_default(self, mod, tmp_path):
        """Step 9 should include default output instructions when none in config."""
        state = {"completed_steps": []}
        ctx = {"pr": {"author_name": "Maria Rodriguez"}}
        g = mod.get_step_guidance(9, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "Maria" in text  # default addresses author by name
        assert "actionable" in text.lower()

    def test_includes_output_instructions_override(self, mod, tmp_path):
        """Step 9 should use caller-provided output_instructions from run-config.json verbatim."""
        config = {"mode": "pr", "output_instructions": "Keep it brief. Bullet points only."}
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(9, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "Keep it brief" in text
        assert "Bullet points only" in text

    def test_output_instructions_override_replaces_default(self, mod, tmp_path):
        """When override is set in run-config.json, default instructions should NOT appear."""
        config = {"mode": "pr", "output_instructions": "Custom instructions only."}
        state = {"completed_steps": []}
        ctx = {"pr": {"author_name": "Maria Rodriguez"}}
        g = mod.get_step_guidance(9, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "Custom instructions only" in text
        # Default "address by name" should NOT appear when overridden
        assert "Maria" not in text

    def test_branch_mode_default_instructions(self, mod, tmp_path):
        """Branch mode default should NOT reference author by name."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(9, "full", state, ctx)
        text = "\n".join(g["actions"])
        assert "actionable" in text.lower()
        # Branch mode has no PR author
        assert "first name" not in text.lower()

    def test_report_structure_guidance(self, mod, tmp_path):
        """Briefing should mention the expected report sections."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(9, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "summary" in text.lower()
        assert "critical" in text.lower()
        assert "verdict" in text.lower()


# ===================================================================
# VALIDATION and OUTPUT Phase Tests (Steps 10-12)
# ===================================================================


class TestStep10DecisionCritic:
    def test_dispatches_decision_reviewer(self, mod, tmp_path):
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "decision-reviewer" in text

    def test_reviews_report_not_findings(self, mod, tmp_path):
        """Critic should review review-report.md (all modes now)."""
        for mode in ("pr", "full", "incremental"):
            state = {"completed_steps": []}
            ctx = {}
            g = mod.get_step_guidance(10, mode, state, ctx)
            text = "\n".join(g["actions"])
            assert "review-report.md" in text

    def test_has_verdict_handling(self, mod, tmp_path):
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "STAND" in text
        assert "REVISE" in text
        assert "ESCALATE" in text

    def test_instructs_wait_for_critic(self, mod, tmp_path):
        """Critic must NOT run in background — LLM needs the verdict."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "wait" in text.lower() or "do not" in text.lower()
        assert "background" in text.lower()

    def test_instructs_writing_review_verdict_json(self, mod, tmp_path):
        """Should instruct the LLM to write review-verdict.json after acting on verdict."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "review-verdict.json" in text
        assert "verdict" in text.lower()


class TestStep11PresentResults:
    def test_shows_verdict_interactive(self, mod, tmp_path):
        config = {"mode": "pr", "interactive": True}
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "verdict" in text.lower()
        assert "Present to the user" in text or "review-report.md" in text

    def test_non_interactive_confirms_files_only(self, mod, tmp_path):
        """Non-interactive mode lists output files, no user presentation."""
        config = {"mode": "pr", "interactive": False}
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "review-report.md" in text
        assert "pipeline-result.json" in text
        assert "Present to the user" not in text

    def test_incremental_mentions_baseline_saved(self, mod, tmp_path):
        config = {"mode": "incremental", "interactive": True}
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(11, "incremental", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "baseline saved" in text.lower() or "next" in text.lower()

    def test_interactive_has_focused_reconciliator_followup(self, mod, tmp_path):
        """Interactive mode should offer focused reconciliator for drill-down."""
        config = {"mode": "pr", "interactive": True}
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "focused" in text.lower() or "drill down" in text.lower()

    def test_incremental_mentions_next_code_review(self, mod, tmp_path):
        """Incremental should mention next /code-review scope."""
        config = {"mode": "incremental", "interactive": True}
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(11, "incremental", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "new commits" in text.lower() or "/code-review" in text


class TestStep12Cleanup:
    def test_asks_user_for_restore(self, mod, tmp_path):
        """Cleanup should ask user, not silently restore."""
        state = {"workspace": {"original_branch": "develop", "stash_ref": "abc"},
                 "completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(12, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "develop" in text  # references original branch
        assert "ask" in text.lower() or "confirm" in text.lower()

    def test_no_restore_when_no_workspace_state(self, mod, tmp_path):
        """Should be a no-op when no workspace state."""
        state = {"workspace": {"original_branch": None, "stash_ref": None},
                 "completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(12, "pr", state, ctx)
        # This step shouldn't even run — but if called, should be minimal
        assert g is not None


class TestDegradedPaths:
    """Degraded-path scenarios and pipeline-result.json contract (rule 31)."""

    def test_pipeline_result_schema(self, mod, tmp_path):
        """Step 11 output should reference all pipeline-result.json fields."""
        config = {"mode": "pr", "interactive": False}
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "pipeline-result.json" in text
        # Schema fields should be referenced or documented
        for field in ("status", "verdict", "report_path", "findings_path",
                      "critic_verdict", "degradation_notes"):
            assert field in text, f"Step 11 output missing pipeline-result.json field: {field}"

    def test_scenario_a_reconciliation_failed(self, mod, tmp_path):
        """Step 9 should run degraded when reconciliation failed."""
        state = {"completed_steps": [], "degradation": {"reconciliation_failed": True}}
        ctx = {}
        g = mod.get_step_guidance(9, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "raw agent" in text.lower() or "degraded" in text.lower()

    def test_scenario_b_report_synthesis_failed(self, mod, tmp_path):
        """Step 10 should fall back to review-findings.md when review-report.md missing."""
        state = {"completed_steps": [], "degradation": {"report_synthesis_failed": True}}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "review-findings.md" in text

    def test_scenario_c_critic_failed(self, mod, tmp_path):
        """Step 11 should show critic_verdict as unavailable when critic failed."""
        state = {"completed_steps": [], "degradation": {"critic_failed": True},
                 "critic_verdict": "unavailable"}
        ctx = {}
        config = {"mode": "pr", "interactive": True}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "unavailable" in text.lower()

    def test_scenario_d_both_failed(self, mod, tmp_path):
        """Both reconciliation and report failed: verdict forced to COMMENT."""
        state = {"completed_steps": [],
                 "degradation": {"reconciliation_failed": True, "report_synthesis_failed": True},
                 "forced_verdict": "COMMENT"}
        ctx = {}
        config = {"mode": "pr", "interactive": True}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "COMMENT" in text
        assert "failed" in text.lower() or "degraded" in text.lower()

    def test_missing_review_verdict_json(self, mod, tmp_path):
        """Step 11 should handle gracefully when review-verdict.json not written."""
        state = {"completed_steps": [], "review_verdict": None}
        ctx = {}
        config = {"mode": "pr", "interactive": False}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        # Should not crash — script handles missing verdict
        assert g is not None

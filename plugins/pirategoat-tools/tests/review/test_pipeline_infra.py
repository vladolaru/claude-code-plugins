"""Tests for review/pipeline.py — infrastructure: step sequence, routing, state, CLI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import PIPELINE_SCRIPT_PATH as SCRIPT_PATH, PIPELINE_TOTAL_STEPS as TOTAL_STEPS


@pytest.fixture(scope="module")
def mod(pipeline_mod):
    return pipeline_mod


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
        """Step 1 should preserve review-context.json — review/context.py overwrites it at step 3."""
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


class TestQuickModeConfig:
    """--quick CLI flag is stored in run-config.json and persists across steps."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_quick_flag_stored_in_config(self, tmp_path):
        """Passing --quick stores quick=true in run-config.json."""
        r = self._run("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path), "--pr-number", "42",
                       "--quick")
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is True

    def test_no_quick_flag_defaults_false(self, tmp_path):
        """Without --quick, config has quick=false."""
        r = self._run("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path), "--pr-number", "42")
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config.get("quick") is False

    def test_quick_from_config_on_subsequent_steps(self, tmp_path):
        """--quick persists in config and is readable at step 3."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42",
                   "--quick")
        # Step 3 should read config with quick=true
        r = self._run("--step", "3", "--output-dir", str(tmp_path))
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is True

    def test_quick_flag_on_rerun_overrides_existing_config(self, tmp_path):
        """Rerunning step 1 with --quick on a previously non-quick output dir
        should update run-config.json to quick=true."""
        # First run: no --quick
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config.get("quick") is False
        # Second run: with --quick (same output dir)
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42",
                   "--quick")
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is True

    def test_quick_flag_resets_on_rerun_without_flag(self, tmp_path):
        """Rerunning step 1 WITHOUT --quick after a quick run should reset to false."""
        # First run: with --quick
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42",
                   "--quick")
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is True
        # Second run: without --quick (same output dir)
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is False

    def test_bot_mode_step1_rerun_preserves_quick(self, tmp_path):
        """In bot mode (interactive=false), re-invoking step 1 without --quick
        should NOT reset the pre-written quick=true. The bot writes the correct
        value in run-config.json and may not pass --quick on subsequent calls."""
        # Step 1: with --quick
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42",
                   "--quick")
        # Simulate bot mode by setting interactive=false and providing
        # the review-context.json that bot mode requires
        config_path = tmp_path / "run-config.json"
        config = json.loads(config_path.read_text())
        config["interactive"] = False
        config_path.write_text(json.dumps(config))
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {"merge_base": "abc123"},
        }))
        assert config["quick"] is True
        # Step 1 rerun: without --quick (bot mode)
        r = self._run("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path), "--pr-number", "42")
        assert r.returncode == 0
        config = json.loads(config_path.read_text())
        assert config["quick"] is True, \
            "bot-mode step 1 rerun should not reset quick to false"

    def test_interactive_step1_rerun_still_resets_quick(self, tmp_path):
        """In interactive mode, re-invoking step 1 without --quick should still
        reset quick to false (existing behavior for human-driven reruns)."""
        # Step 1: with --quick
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42",
                   "--quick")
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is True
        assert config.get("interactive") is True  # default
        # Step 1 rerun: without --quick (interactive rerun)
        r = self._run("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path), "--pr-number", "42")
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is False, \
            "interactive step 1 rerun should reset quick to false"


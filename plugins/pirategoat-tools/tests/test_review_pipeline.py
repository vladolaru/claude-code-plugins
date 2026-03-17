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

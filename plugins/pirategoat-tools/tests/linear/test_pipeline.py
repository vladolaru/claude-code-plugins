"""Tests for linear/pipeline.py — step sequence, routing, state, CLI."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # linear/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
PIPELINE_SCRIPT = SCRIPTS_DIR / "linear" / "pipeline.py"
TOTAL_STEPS = 15


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "linear_issue_pipeline", PIPELINE_SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


# ---------------------------------------------------------------------------
# Step Sequence
# ---------------------------------------------------------------------------

class TestStepSequence:
    def test_step_numbers_are_sequential(self, mod):
        numbers = [s["step"] for s in mod.STEP_SEQUENCE]
        assert numbers == list(range(1, TOTAL_STEPS + 1))

    def test_phases_are_valid(self, mod):
        valid_phases = {"SETUP", "INVESTIGATION", "IMPLEMENTATION", "VALIDATION", "OUTPUT"}
        for s in mod.STEP_SEQUENCE:
            assert s["phase"] in valid_phases, f"Step {s['step']} has invalid phase {s['phase']}"

    def test_conditions_are_valid(self, mod):
        valid_conditions = {
            "always",
            "fix_mode_only",
            "fix_mode_and_unresolved",
            "fix_mode_and_unresolved_review_needed",
        }
        for s in mod.STEP_SEQUENCE:
            assert s["condition"] in valid_conditions, f"Step {s['step']} has invalid condition {s['condition']}"


# ---------------------------------------------------------------------------
# Condition Evaluation
# ---------------------------------------------------------------------------
#
# `_eval_condition` is private; every branch reached by a real STEP_SEQUENCE
# entry is exercised through the public get_active_steps in TestActiveSteps
# and TestClarityGateRouting: "always" (steps 1-8, 15), "fix_mode_and_unresolved"
# (steps 9-11, 14, including the mode/resolved/clarity_blocked/override
# sub-branches) and "fix_mode_and_unresolved_review_needed" (steps 12-13,
# including the small/medium/large complexity sub-branches). The unknown-
# condition default is defensive code for a STEP_SEQUENCE typo that
# test_conditions_are_valid already forbids, so it needs no test.
#
# "fix_mode_only" is a valid condition (test_conditions_are_valid accepts it)
# that no current STEP_SEQUENCE entry uses, so get_active_steps never reaches
# it. Its two rows stay here directly on _eval_condition since it is
# otherwise completely untested.

class TestConditionEvaluation:
    def test_fix_mode_only_true_in_fix(self, mod):
        assert mod._eval_condition("fix_mode_only", "fix", {}, {}, {})

    def test_fix_mode_only_false_in_investigate(self, mod):
        assert not mod._eval_condition("fix_mode_only", "investigate", {}, {}, {})


# ---------------------------------------------------------------------------
# Active Steps
# ---------------------------------------------------------------------------

class TestActiveSteps:
    def test_investigate_mode_skips_fix_steps(self, mod):
        active = mod.get_active_steps("investigate", {}, {}, {})
        # Steps 9-14 should NOT be active in investigate mode
        for step in [9, 10, 11, 12, 13, 14]:
            assert step not in active, f"Step {step} should be skipped in investigate mode"
        # Steps 1-8 and 15 should be active
        for step in [1, 2, 3, 4, 5, 6, 7, 8, 15]:
            assert step in active, f"Step {step} should be active in investigate mode"

    def test_fix_mode_includes_all_steps_when_unresolved(self, mod):
        active = mod.get_active_steps("fix", {}, {}, {})
        assert len(active) == TOTAL_STEPS
        for step in range(1, TOTAL_STEPS + 1):
            assert step in active

    def test_small_complexity_skips_iterative_review_steps(self, mod):
        state = {"complexity": {"complexity": "small"}}
        active = mod.get_active_steps("fix", {}, state, {})
        assert 12 not in active
        assert 13 not in active
        assert 14 in active

    def test_medium_complexity_keeps_iterative_review_steps(self, mod):
        """medium and large take the same != "small" branch; one row proves it."""
        state = {"complexity": {"complexity": "medium"}}
        active = mod.get_active_steps("fix", {}, state, {})
        assert 12 in active
        assert 13 in active

    def test_fix_mode_skips_impl_steps_when_resolved(self, mod):
        state = {"issue_resolved": True}
        active = mod.get_active_steps("fix", {}, state, {})
        # Steps 9-14 should be skipped when issue is resolved
        for step in [9, 10, 11, 12, 13, 14]:
            assert step not in active, f"Step {step} should be skipped when resolved"
        # Steps 1-8 and 15 should still be active
        for step in [1, 2, 3, 4, 5, 6, 7, 8, 15]:
            assert step in active


# ---------------------------------------------------------------------------
# Clarity Gate Routing
# ---------------------------------------------------------------------------

class TestClarityGateRouting:
    def test_clarity_blocked_skips_impl_steps(self, mod):
        state = {"clarity_blocked": True}
        active = mod.get_active_steps("fix", {}, state, {})
        for step in [9, 10, 11, 12, 13, 14]:
            assert step not in active, f"Step {step} should be skipped when clarity blocked"
        for step in [1, 2, 3, 4, 5, 6, 7, 8, 15]:
            assert step in active

    def test_clarity_blocked_with_override_includes_impl_steps(self, mod):
        state = {"clarity_blocked": True}
        config = {"skip_clarity_gate": True}
        active = mod.get_active_steps("fix", config, state, {})
        assert len(active) == 15
        for step in range(1, 16):
            assert step in active

    def test_investigate_mode_unaffected_by_clarity_blocked(self, mod):
        state = {"clarity_blocked": True}
        active = mod.get_active_steps("investigate", {}, state, {})
        for step in [1, 2, 3, 4, 5, 6, 7, 8, 15]:
            assert step in active

    def test_clarity_blocked_jumps_8_to_15(self, mod):
        state = {"clarity_blocked": True}
        active = mod.get_active_steps("fix", {}, state, {})
        result = mod.compute_next_step(8, active)
        assert result["step"] == 15
        assert result["skip_reason"] is not None


# ---------------------------------------------------------------------------
# Next Step Computation
# ---------------------------------------------------------------------------

class TestNextStep:
    def test_computes_next_from_active(self, mod):
        active = {1, 2, 3, 5, 6, 14}
        result = mod.compute_next_step(3, active)
        assert result["step"] == 5

    def test_skip_reason_when_steps_skipped(self, mod):
        active = {1, 2, 3, 6, 14}
        result = mod.compute_next_step(3, active)
        assert result["step"] == 6
        assert result["skip_reason"] is not None
        assert "Step 4" in result["skip_reason"]
        assert "Step 5" in result["skip_reason"]

    def test_no_skip_reason_when_sequential(self, mod):
        active = {1, 2, 3}
        result = mod.compute_next_step(1, active)
        assert result["step"] == 2
        assert result["skip_reason"] is None

    def test_returns_none_after_last(self, mod):
        active = {1, 2, 3}
        assert mod.compute_next_step(3, active) is None

    def test_returns_none_for_empty_active(self, mod):
        assert mod.compute_next_step(1, set()) is None

    def test_investigate_mode_8_to_15(self, mod):
        """The sequential 7→8 branch is pinned by test_no_skip_reason_when_sequential;
        this covers the interesting 8→15 skip."""
        active = mod.get_active_steps("investigate", {}, {}, {})
        result = mod.compute_next_step(8, active)
        assert result["step"] == 15
        assert result["skip_reason"] is not None

    def test_small_complexity_routes_11_to_14(self, mod):
        active = mod.get_active_steps("fix", {}, {"complexity": {"complexity": "small"}}, {})
        result = mod.compute_next_step(11, active)
        assert result["step"] == 14
        assert "Step 12" in result["skip_reason"]
        assert "Step 13" in result["skip_reason"]


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

class TestStateManagement:
    def test_read_state_returns_default_when_missing(self, mod, tmp_path):
        state = mod.read_state(str(tmp_path))
        assert "completed_steps" in state
        assert state["completed_steps"] == []

    def test_write_and_read_state(self, mod, tmp_path):
        state = {"completed_steps": [1, 2], "mode": "investigate"}
        mod.write_state(str(tmp_path), state)
        loaded = mod.read_state(str(tmp_path))
        assert loaded["completed_steps"] == [1, 2]

    def test_read_config_returns_default_when_missing(self, mod, tmp_path):
        config = mod.read_config(str(tmp_path))
        assert isinstance(config, dict)

    def test_write_and_read_config(self, mod, tmp_path):
        config = {"mode": "fix", "interactive": False}
        mod.write_config(str(tmp_path), config)
        loaded = mod.read_config(str(tmp_path))
        assert loaded["mode"] == "fix"
        assert loaded["interactive"] is False

    def test_read_state_handles_corrupted_json(self, mod, tmp_path):
        path = tmp_path / "pipeline-state.json"
        path.write_text("not valid json{{{")
        state = mod.read_state(str(tmp_path))
        assert "completed_steps" in state


# ---------------------------------------------------------------------------
# Context Reading
# ---------------------------------------------------------------------------

class TestContextReading:
    def test_read_issue_context_returns_empty_when_missing(self, mod, tmp_path):
        ctx = mod.read_issue_context(str(tmp_path))
        assert isinstance(ctx, dict)
        assert len(ctx) == 0

    def test_read_issue_context_returns_data(self, mod, tmp_path):
        path = tmp_path / "issue-context.json"
        data = {"issue_id": "WOOPLUG-1234", "team_prefix": "WOOPLUG"}
        path.write_text(json.dumps(data))
        ctx = mod.read_issue_context(str(tmp_path))
        assert ctx["issue_id"] == "WOOPLUG-1234"


# ---------------------------------------------------------------------------
# Repo Sanity Check
# ---------------------------------------------------------------------------

REPO_MATCH_ROWS = [
    pytest.param(
        None, 0, "", True, None, None,
        id="no-repo-slug-skips-check",
    ),
    pytest.param(
        "Automattic/woocommerce-payments", 0,
        "git@github.com:Automattic/woocommerce-payments.git",
        True, "Automattic/woocommerce-payments", None,
        id="ssh-url-matches",
    ),
    pytest.param(
        "Automattic/woocommerce-payments", 0,
        "https://github.com/Automattic/woocommerce-payments.git",
        True, None, None,
        id="https-url-matches",
    ),
    pytest.param(
        "Automattic/woocommerce-payments", 0,
        "git@github.com:automattic/WooCommerce-Payments.git",
        True, None, None,
        id="case-insensitive",
    ),
    pytest.param(
        "Automattic/woocommerce-payments", 0,
        "git@github.com:Automattic/wpcom.git",
        False, "Automattic/wpcom", "Automattic/woocommerce-payments",
        id="mismatch-detected",
    ),
    pytest.param(
        "Automattic/woocommerce-payments", 1, "",
        True, None, None,
        id="git-failure-skips-check",
    ),
]


class TestCheckRepoMatch:
    @pytest.mark.parametrize(
        "repo_slug,returncode,stdout,expected_matches,expected_actual,expected_expected",
        REPO_MATCH_ROWS,
    )
    def test_check_repo_match(self, mod, monkeypatch, repo_slug, returncode, stdout,
                               expected_matches, expected_actual, expected_expected):
        def fake_subprocess(cmd, **kwargs):
            class R:
                pass
            r = R()
            r.returncode = returncode
            r.stdout = stdout
            r.stderr = "" if returncode == 0 else "not a git repo"
            return r
        monkeypatch.setattr(subprocess, "run", fake_subprocess)
        ctx = {"repo_slug": repo_slug} if repo_slug else {}
        matches, actual, expected = mod.check_repo_match(ctx)
        assert matches is expected_matches
        if expected_actual is not None:
            assert actual == expected_actual
        if expected_expected is not None:
            assert expected == expected_expected


# ---------------------------------------------------------------------------
# Repo Mismatch Guidance
# ---------------------------------------------------------------------------

class TestRepoMismatchGuidance:
    def test_bot_mode_shows_pipeline_stopped(self, mod):
        state = {"repo_mismatch": {"actual": "Org/wrong", "expected": "Org/right"}}
        config = {"interactive": False}
        g = mod.get_step_guidance(1, "investigate", state, {}, config=config, output_dir="/tmp")
        text = "\n".join(g.get("situation", []) + g.get("actions", []))
        assert "PIPELINE STOPPED" in text or "REPO MISMATCH" in text
        assert "Org/wrong" in text
        assert "Org/right" in text

    def test_interactive_mode_tells_user_to_switch(self, mod):
        state = {"repo_mismatch": {"actual": "Org/wrong", "expected": "Org/right"}}
        config = {"interactive": True}
        g = mod.get_step_guidance(1, "investigate", state, {}, config=config, output_dir="/tmp")
        text = "\n".join(g.get("situation", []) + g.get("actions", []))
        assert "wrong repository" in text.lower() or "REPO MISMATCH" in text
        assert "Org/right" in text

    def test_mismatch_has_no_handoff(self, mod):
        """Mismatch means stop — no handoff to next step."""
        state = {"repo_mismatch": {"actual": "Org/wrong", "expected": "Org/right"}}
        g = mod.get_step_guidance(1, "investigate", state, {}, config={}, output_dir="/tmp")
        assert g["handoff"] is None

    def test_no_mismatch_has_normal_guidance(self, mod):
        """Without mismatch state, step 1 guidance is normal."""
        g = mod.get_step_guidance(1, "investigate", {}, {"issue_id": "X-1"},
                                  config={}, output_dir="/tmp")
        assert g["handoff"] is not None
        text = "\n".join(g.get("actions", []))
        assert "repo sanity check" in text.lower() or "git remote" in text


class TestRepoMismatchOrchestration:
    def test_bot_mode_writes_failed_result(self, mod, tmp_path, monkeypatch):
        """Bot-mode repo mismatch writes pipeline-result.json with status: failed."""
        def fake_subprocess(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = "git@github.com:Org/wrong-repo.git"
                stderr = ""
            return R()
        monkeypatch.setattr(subprocess, "run", fake_subprocess)

        context = {"issue_id": "TEST-1", "repo_slug": "Org/right-repo"}
        config = {"interactive": False}
        state = {"completed_steps": [], "degradation_notes": []}
        mod._orchestrate_step(1, "investigate", config, state, context, str(tmp_path))

        assert state.get("repo_mismatch") is not None
        result_path = tmp_path / "pipeline-result.json"
        assert result_path.exists()
        result = json.loads(result_path.read_text())
        assert result["status"] == "failed"
        assert any("mismatch" in n.lower() or "Repo" in n for n in result["degradation_notes"])

    def test_interactive_mode_does_not_write_result(self, mod, tmp_path, monkeypatch):
        """Interactive-mode repo mismatch sets state but does NOT write pipeline-result.json."""
        def fake_subprocess(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = "git@github.com:Org/wrong-repo.git"
                stderr = ""
            return R()
        monkeypatch.setattr(subprocess, "run", fake_subprocess)

        context = {"issue_id": "TEST-1", "repo_slug": "Org/right-repo"}
        config = {"interactive": True}
        state = {"completed_steps": [], "degradation_notes": []}
        mod._orchestrate_step(1, "investigate", config, state, context, str(tmp_path))

        assert state.get("repo_mismatch") is not None
        assert not (tmp_path / "pipeline-result.json").exists()


# ---------------------------------------------------------------------------
# Stale Artifact Cleanup
# ---------------------------------------------------------------------------

STALE_CLEANUP_ROWS = [
    pytest.param("pipeline-state.json", "{}", False, id="pipeline-state-removed"),
    pytest.param("pipeline-result.json", "{}", False, id="pipeline-result-removed"),
    pytest.param("pipeline-events.jsonl", "", False, id="pipeline-events-removed"),
    pytest.param("run-config.json", '{"mode": "fix"}', True, id="run-config-preserved"),
    pytest.param("issue-context.json", '{"issue_id": "X-1"}', True, id="issue-context-preserved"),
    pytest.param("clarity-assessment.json", "{}", False, id="clarity-assessment-removed"),
]


class TestStaleArtifactCleanup:
    @pytest.mark.parametrize("filename,content,survives", STALE_CLEANUP_ROWS)
    def test_stale_artifact_cleanup(self, mod, tmp_path, filename, content, survives):
        (tmp_path / filename).write_text(content)
        mod.clean_stale_artifacts(str(tmp_path))
        assert (tmp_path / filename).exists() is survives


# ---------------------------------------------------------------------------
# Format Output
# ---------------------------------------------------------------------------

class TestFormatOutput:
    def test_includes_header_situation_actions_and_handoff(self, mod):
        """The section markers the orchestrator parses: header, SITUATION,
        ACTIONS, HANDOFF."""
        guidance = {
            "phase": "INVESTIGATION",
            "title": "Investigate",
            "situation": ["Bug investigation"],
            "actions": ["Search for duplicates"],
            "handoff": ["investigation-report.md must exist"],
            "next_step": None,
            "skip_reason": None,
        }
        output = mod.format_output(5, guidance)
        assert "Step 5" in output
        assert "INVESTIGATION" in output
        assert "Investigate" in output
        assert "SITUATION" in output
        assert "Bug investigation" in output
        assert "ACTIONS" in output
        assert "Search for duplicates" in output
        assert "HANDOFF" in output
        assert "investigation-report.md must exist" in output

    def test_pipeline_complete_when_no_next_step_else_points_at_next(self, mod):
        guidance = {
            "phase": "OUTPUT",
            "title": "Present Results",
            "situation": [],
            "actions": [],
            "handoff": None,
            "next_step": None,
            "skip_reason": None,
        }
        assert "PIPELINE COMPLETE" in mod.format_output(15, guidance)

        guidance["next_step"] = {"step": 2, "title": "Fetch Issue", "skip_reason": None}
        output = mod.format_output(1, guidance)
        assert "Step 2" in output
        assert "Fetch Issue" in output
        assert "pipeline.py" in output


# ---------------------------------------------------------------------------
# Step Guidance (smoke tests — detailed tests in Task 12/13)
# ---------------------------------------------------------------------------

class TestStepGuidance:
    def test_returns_none_for_invalid_step(self, mod):
        assert mod.get_step_guidance(99, "investigate", {}, {}, config={}, output_dir="/tmp") is None

    def test_all_steps_return_guidance(self, mod):
        for step_def in mod.STEP_SEQUENCE:
            result = mod.get_step_guidance(
                step_def["step"], "fix", {}, {},
                config={"interactive": False},
                output_dir="/tmp/test",
            )
            assert result is not None, f"Step {step_def['step']} returned None"
            assert "phase" in result
            assert "title" in result


# ---------------------------------------------------------------------------
# CLI (subprocess tests)
# ---------------------------------------------------------------------------

class TestCLI:
    def test_step_1_creates_state_cleans_stale_artifacts_and_prints_guidance(self, tmp_path):
        # Create a stale artifact from a previous run
        (tmp_path / "pipeline-result.json").write_text("{}")
        config = {"mode": "investigate", "interactive": False}
        (tmp_path / "run-config.json").write_text(json.dumps(config))
        (tmp_path / "issue-context.json").write_text(json.dumps({"issue_id": "TEST-1"}))

        result = subprocess.run(
            [sys.executable, str(PIPELINE_SCRIPT),
             "--step", "1", "--mode", "investigate",
             "--output-dir", str(tmp_path), "--issue-id", "TEST-1"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert (tmp_path / "pipeline-state.json").exists()
        assert not (tmp_path / "pipeline-result.json").exists()
        assert "Step 1" in result.stdout
        assert "SETUP" in result.stdout

    def test_invalid_step_exits_nonzero(self, tmp_path):
        config = {"mode": "investigate", "interactive": False}
        (tmp_path / "run-config.json").write_text(json.dumps(config))

        result = subprocess.run(
            [sys.executable, str(PIPELINE_SCRIPT),
             "--step", "99", "--mode", "investigate",
             "--output-dir", str(tmp_path), "--issue-id", "TEST-1"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0

    def test_missing_mode_at_step_1_exits_nonzero(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(PIPELINE_SCRIPT),
             "--step", "1",
             "--output-dir", str(tmp_path), "--issue-id", "TEST-1"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0

    def test_step_2_reads_existing_state(self, tmp_path):
        # Set up state and config as if step 1 already ran
        config = {"mode": "fix", "interactive": False}
        state = {"completed_steps": [1], "run_id": "test"}
        (tmp_path / "run-config.json").write_text(json.dumps(config))
        (tmp_path / "pipeline-state.json").write_text(json.dumps(state))
        (tmp_path / "issue-context.json").write_text(json.dumps({"issue_id": "TEST-1"}))

        result = subprocess.run(
            [sys.executable, str(PIPELINE_SCRIPT),
             "--step", "2", "--mode", "fix",
             "--output-dir", str(tmp_path), "--issue-id", "TEST-1"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "Step 2" in result.stdout

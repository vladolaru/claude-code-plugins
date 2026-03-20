"""Tests for linear-issue-pipeline.py — step sequence, routing, state, CLI."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
PIPELINE_SCRIPT = SCRIPTS_DIR / "linear-issue-pipeline.py"
TOTAL_STEPS = 14


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
    def test_has_14_steps(self, mod):
        assert len(mod.STEP_SEQUENCE) == TOTAL_STEPS

    def test_step_numbers_are_sequential(self, mod):
        numbers = [s["step"] for s in mod.STEP_SEQUENCE]
        assert numbers == list(range(1, TOTAL_STEPS + 1))

    def test_all_steps_have_required_keys(self, mod):
        required = {"step", "title", "phase", "condition"}
        for s in mod.STEP_SEQUENCE:
            assert required.issubset(s.keys()), f"Step {s.get('step')} missing keys"

    def test_step_map_covers_all_steps(self, mod):
        assert len(mod._STEP_MAP) == TOTAL_STEPS

    def test_phases_are_valid(self, mod):
        valid_phases = {"SETUP", "INVESTIGATION", "IMPLEMENTATION", "VALIDATION", "OUTPUT"}
        for s in mod.STEP_SEQUENCE:
            assert s["phase"] in valid_phases, f"Step {s['step']} has invalid phase {s['phase']}"

    def test_conditions_are_valid(self, mod):
        valid_conditions = {"always", "fix_mode_only"}
        for s in mod.STEP_SEQUENCE:
            assert s["condition"] in valid_conditions, f"Step {s['step']} has invalid condition {s['condition']}"


# ---------------------------------------------------------------------------
# Condition Evaluation
# ---------------------------------------------------------------------------

class TestConditionEvaluation:
    def test_always_is_true(self, mod):
        assert mod._eval_condition("always", "investigate", {}, {}, {})
        assert mod._eval_condition("always", "fix", {}, {}, {})

    def test_fix_mode_only_true_in_fix(self, mod):
        assert mod._eval_condition("fix_mode_only", "fix", {}, {}, {})

    def test_fix_mode_only_false_in_investigate(self, mod):
        assert not mod._eval_condition("fix_mode_only", "investigate", {}, {}, {})

    def test_unknown_condition_returns_false(self, mod):
        assert not mod._eval_condition("nonexistent_condition", "fix", {}, {}, {})


# ---------------------------------------------------------------------------
# Active Steps
# ---------------------------------------------------------------------------

class TestActiveSteps:
    def test_investigate_mode_skips_fix_steps(self, mod):
        active = mod.get_active_steps("investigate", {}, {}, {})
        # Steps 8-13 should NOT be active in investigate mode
        for step in [8, 9, 10, 11, 12, 13]:
            assert step not in active, f"Step {step} should be skipped in investigate mode"
        # Steps 1-7 and 14 should be active
        for step in [1, 2, 3, 4, 5, 6, 7, 14]:
            assert step in active, f"Step {step} should be active in investigate mode"

    def test_fix_mode_includes_all_steps(self, mod):
        active = mod.get_active_steps("fix", {}, {}, {})
        assert len(active) == TOTAL_STEPS
        for step in range(1, TOTAL_STEPS + 1):
            assert step in active

    def test_returns_set(self, mod):
        active = mod.get_active_steps("investigate", {}, {}, {})
        assert isinstance(active, set)


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

    def test_investigate_mode_jumps_7_to_14(self, mod):
        active = mod.get_active_steps("investigate", {}, {}, {})
        result = mod.compute_next_step(7, active)
        assert result["step"] == 14
        assert result["skip_reason"] is not None

    def test_fix_mode_7_to_8(self, mod):
        active = mod.get_active_steps("fix", {}, {}, {})
        result = mod.compute_next_step(7, active)
        assert result["step"] == 8
        assert result["skip_reason"] is None


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
# Stale Artifact Cleanup
# ---------------------------------------------------------------------------

class TestStaleArtifactCleanup:
    def test_removes_pipeline_state(self, mod, tmp_path):
        (tmp_path / "pipeline-state.json").write_text("{}")
        mod.clean_stale_artifacts(str(tmp_path))
        assert not (tmp_path / "pipeline-state.json").exists()

    def test_removes_pipeline_result(self, mod, tmp_path):
        (tmp_path / "pipeline-result.json").write_text("{}")
        mod.clean_stale_artifacts(str(tmp_path))
        assert not (tmp_path / "pipeline-result.json").exists()

    def test_removes_pipeline_events(self, mod, tmp_path):
        (tmp_path / "pipeline-events.jsonl").write_text("")
        mod.clean_stale_artifacts(str(tmp_path))
        assert not (tmp_path / "pipeline-events.jsonl").exists()

    def test_preserves_run_config(self, mod, tmp_path):
        (tmp_path / "run-config.json").write_text('{"mode": "fix"}')
        mod.clean_stale_artifacts(str(tmp_path))
        assert (tmp_path / "run-config.json").exists()

    def test_preserves_issue_context(self, mod, tmp_path):
        (tmp_path / "issue-context.json").write_text('{"issue_id": "X-1"}')
        mod.clean_stale_artifacts(str(tmp_path))
        assert (tmp_path / "issue-context.json").exists()


# ---------------------------------------------------------------------------
# Format Output
# ---------------------------------------------------------------------------

class TestFormatOutput:
    def test_includes_header(self, mod):
        guidance = {
            "phase": "SETUP",
            "title": "Parse Input",
            "situation": ["Mode: investigate"],
            "actions": ["Read issue-context.json"],
            "handoff": None,
            "next_step": {"step": 2, "title": "Fetch Issue", "skip_reason": None},
            "skip_reason": None,
        }
        output = mod.format_output(1, guidance)
        assert "Step 1" in output
        assert "SETUP" in output
        assert "Parse Input" in output

    def test_includes_situation_and_actions(self, mod):
        guidance = {
            "phase": "INVESTIGATION",
            "title": "Investigate",
            "situation": ["Bug investigation"],
            "actions": ["Search for duplicates"],
            "handoff": None,
            "next_step": None,
            "skip_reason": None,
        }
        output = mod.format_output(5, guidance)
        assert "SITUATION" in output
        assert "Bug investigation" in output
        assert "ACTIONS" in output
        assert "Search for duplicates" in output

    def test_includes_handoff_when_present(self, mod):
        guidance = {
            "phase": "OUTPUT",
            "title": "Write Report",
            "situation": [],
            "actions": [],
            "handoff": ["investigation-report.md must exist"],
            "next_step": None,
            "skip_reason": None,
        }
        output = mod.format_output(6, guidance)
        assert "HANDOFF" in output
        assert "investigation-report.md must exist" in output

    def test_pipeline_complete_when_no_next_step(self, mod):
        guidance = {
            "phase": "OUTPUT",
            "title": "Present Results",
            "situation": [],
            "actions": [],
            "handoff": None,
            "next_step": None,
            "skip_reason": None,
        }
        output = mod.format_output(14, guidance)
        assert "PIPELINE COMPLETE" in output

    def test_next_step_pointer(self, mod):
        guidance = {
            "phase": "SETUP",
            "title": "Parse Input",
            "situation": [],
            "actions": [],
            "handoff": None,
            "next_step": {"step": 2, "title": "Fetch Issue", "skip_reason": None},
            "skip_reason": None,
        }
        output = mod.format_output(1, guidance)
        assert "Step 2" in output
        assert "Fetch Issue" in output
        assert "linear-issue-pipeline.py" in output

    def test_skip_reason_in_output(self, mod):
        guidance = {
            "phase": "OUTPUT",
            "title": "Present Results",
            "situation": [],
            "actions": [],
            "handoff": None,
            "next_step": None,
            "skip_reason": "Skipped: Step 8 (Write Plan), Step 9 (Implement)",
        }
        output = mod.format_output(14, guidance)
        assert "Skipped" in output


# ---------------------------------------------------------------------------
# Step Guidance (smoke tests — detailed tests in Task 12/13)
# ---------------------------------------------------------------------------

class TestStepGuidance:
    def test_returns_dict_for_valid_step(self, mod):
        result = mod.get_step_guidance(1, "investigate", {}, {}, config={}, output_dir="/tmp")
        assert isinstance(result, dict)
        assert "phase" in result
        assert "title" in result
        assert "situation" in result
        assert "actions" in result

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
    def test_step_1_creates_state_file(self, tmp_path):
        # Write run-config
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

    def test_step_1_cleans_stale_artifacts(self, tmp_path):
        # Create stale artifact
        (tmp_path / "pipeline-result.json").write_text("{}")
        config = {"mode": "investigate", "interactive": False}
        (tmp_path / "run-config.json").write_text(json.dumps(config))
        (tmp_path / "issue-context.json").write_text(json.dumps({"issue_id": "TEST-1"}))

        subprocess.run(
            [sys.executable, str(PIPELINE_SCRIPT),
             "--step", "1", "--mode", "investigate",
             "--output-dir", str(tmp_path), "--issue-id", "TEST-1"],
            capture_output=True, text=True, timeout=10,
        )
        assert not (tmp_path / "pipeline-result.json").exists()

    def test_outputs_guidance_text(self, tmp_path):
        config = {"mode": "investigate", "interactive": False}
        (tmp_path / "run-config.json").write_text(json.dumps(config))
        (tmp_path / "issue-context.json").write_text(json.dumps({"issue_id": "TEST-1"}))

        result = subprocess.run(
            [sys.executable, str(PIPELINE_SCRIPT),
             "--step", "1", "--mode", "investigate",
             "--output-dir", str(tmp_path), "--issue-id", "TEST-1"],
            capture_output=True, text=True, timeout=10,
        )
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

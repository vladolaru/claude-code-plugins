"""Tests for pr-review-pipeline.py step-injection script."""

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

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "pr-review-pipeline.py"
TOTAL_STEPS = 14


def _load_module():
    spec = importlib.util.spec_from_file_location("pr_review_pipeline", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


class TestSetupPhase:
    """Steps 0-2: Parse PR, repo setup, context discovery."""

    def _vals(self, mod, tmp_path, pr_number="42", with_context=False):
        if with_context:
            (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        return mod.load_context_values(str(tmp_path), pr_number)

    def test_step_0_title(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(0, TOTAL_STEPS, vals, "")
        assert g["title"] == "Parse PR Number"

    def test_step_0_skips_ask_when_pr_provided(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(0, TOTAL_STEPS, vals, "")
        actions = "\n".join(g["actions"])
        assert "AskUserQuestion" not in actions

    def test_step_0_asks_when_no_pr(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path, pr_number=None)
        g = mod.get_step_guidance(0, TOTAL_STEPS, vals, "")
        actions = "\n".join(g["actions"])
        assert "Usage" in actions or "required" in actions.lower()

    def test_step_1_skips_repo_setup_when_context_exists(self, mod, tmp_path):
        """Bot mode: review-context.json exists → skip repo setup."""
        vals = self._vals(mod, tmp_path, with_context=True)
        g = mod.get_step_guidance(1, TOTAL_STEPS, vals, "")
        actions = "\n".join(g["actions"])
        assert "skip" in actions.lower() or "pre-computed" in actions.lower()
        assert "git stash" not in actions
        assert "git checkout" not in actions

    def test_step_1_full_setup_when_no_context(self, mod, tmp_path):
        """Interactive mode: full repo setup."""
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(1, TOTAL_STEPS, vals, "")
        actions = "\n".join(g["actions"])
        assert "git status" in actions or "uncommitted" in actions.lower()

    def test_step_1_auto_stash_when_not_bot_mode(self, mod, tmp_path):
        """Default (non-bot) mode: auto-stash uncommitted changes."""
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(1, TOTAL_STEPS, vals, "")
        actions = "\n".join(g["actions"])
        assert "auto" in actions.lower() or "stash push" in actions

    def test_step_2_skips_discovery_when_context_exists(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path, with_context=True)
        g = mod.get_step_guidance(2, TOTAL_STEPS, vals, "")
        actions = "\n".join(g["actions"])
        assert "review-context.json" in actions
        assert "gather-review-context" not in actions

    def test_step_2_discovers_when_no_context(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(2, TOTAL_STEPS, vals, "")
        actions = "\n".join(g["actions"])
        assert "gather-review-context" in actions


class TestContextExtraction:
    """Tests for load_context_values() extraction of reviews and linked issues."""

    def test_load_context_values_extracts_reviews_summary(self, mod, tmp_path):
        """load_context_values should extract a formatted reviews summary."""
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        assert "reviews_summary" in vals
        assert "1 approved" in vals["reviews_summary"].lower()

    def test_load_context_values_extracts_linked_issues(self, mod, tmp_path):
        """load_context_values should extract linked issues as a string."""
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        assert "linked_issues" in vals
        assert "WOOPLUG-1234" in vals["linked_issues"]

    def test_load_context_values_no_reviews(self, mod, tmp_path):
        """No reviews section should produce a sensible default."""
        ctx = {**COMPLETE_CONTEXT, "reviews": {}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        vals = mod.load_context_values(str(tmp_path), "42")
        assert "reviews_summary" in vals
        assert "first review" in vals["reviews_summary"].lower() or "no" in vals["reviews_summary"].lower()

    def test_load_context_values_no_linked_issues(self, mod, tmp_path):
        """No linked issues should produce 'None found'."""
        ctx = {**COMPLETE_CONTEXT, "linked_issues": []}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        vals = mod.load_context_values(str(tmp_path), "42")
        assert "linked_issues" in vals
        assert vals["linked_issues"] == "None found"


class TestContextSummary:
    """Step 3: Review Context Summary (replaces old Steps 3+4+5)."""

    def _vals(self, mod, tmp_path):
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        return mod.load_context_values(str(tmp_path), "42")

    def test_step_3_presents_reviews(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(3, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "approved" in actions.lower()
        # Should NOT run gh pr view — data is pre-computed
        assert "gh pr view" not in actions and "ghe pr view" not in actions

    def test_step_3_presents_linked_issues(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(3, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "WOOPLUG-1234" in actions

    def test_step_3_asks_for_context_synthesis(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(3, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "purpose" in actions.lower() or "PR purpose" in actions


class TestExecutionPhase:
    """Steps 6-9: Size, ground truth, dispatch, agents."""

    def test_step_6_reads_size_from_context(self, mod, tmp_path):
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(6, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "review-context.json" in actions

    def test_step_8_interpolates_actual_git_range(self, mod, tmp_path):
        """Script should print the actual git range, not a template variable."""
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(8, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "plan-review-dispatch" in actions
        # Should contain the actual range from the context file, not a placeholder
        assert "abc123..fix/thing" in actions
        assert "${GIT_RANGE}" not in actions
        assert "<GIT_RANGE>" not in actions

    def test_step_8_triage_references_dispatch_plan(self, mod, tmp_path):
        """Step 8 should tell Claude to use the script's triage, not redo it."""
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(8, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        # Should reference reviewing the script's decisions, not "Apply your own triage"
        assert "Apply your own triage" not in actions
        assert "override" in actions.lower() or "review the plan" in actions.lower()

    def test_step_9_has_parallel_dispatch(self, mod, tmp_path):
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(9, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "parallel" in actions.lower() or "SINGLE message" in actions

    def test_step_10_dispatches_reconciliator(self, mod, tmp_path):
        """Reconcile step should dispatch the reconciliator agent."""
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(10, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "review-reconciliator" in actions
        assert str(tmp_path) in actions
        assert "${OUTPUT_DIR}" not in actions

    def test_step_11_interpolates_report_path(self, mod, tmp_path):
        """Report path should be absolute, not a template."""
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(11, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert str(tmp_path / "review-report.md") in actions


class TestReconcilePhase:
    """Step 10: Reconcile + Verify."""

    def test_step_10_dispatches_reconciliator(self, mod, tmp_path):
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(10, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "review-reconciliator" in actions
        assert "review-findings.json" in actions

    def test_step_10_has_git_range(self, mod, tmp_path):
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(10, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "abc123..fix/thing" in actions


class TestReviewPhase:
    """Step 11: Generate review report."""

    def test_step_11_writes_report(self, mod, tmp_path):
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(11, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "review-report.md" in actions
        assert "review-findings" in actions


class TestValidationPhase:
    """Step 12: Decision critic."""

    def test_step_12_has_decision_critic(self, mod, tmp_path):
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(12, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "decision" in actions.lower()
        assert "review-report.md" in actions

    def test_step_12_no_ingest_verification(self, mod, tmp_path):
        """Decision critic should NOT reference ingestion verification."""
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(12, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "ingest-verification" not in actions
        assert "Ingestion Verification" not in actions
        assert "ingest-code-review" not in actions


class TestOutputPhase:
    """Step 13: Present results."""

    def test_step_13_presents_results(self, mod, tmp_path):
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(13, TOTAL_STEPS, vals, "state")
        actions = "\n".join(g["actions"])
        assert "review-report.md" in actions
        assert "verdict" in actions.lower()


class TestCleanupStep:
    """Step 14: Cleanup — restore workspace after presenting results."""

    def _vals(self, mod, tmp_path, pr_number="42"):
        return mod.load_context_values(str(tmp_path), pr_number)

    def test_step_14_exists(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(14, TOTAL_STEPS, vals, "state")
        assert g is not None
        assert g["phase"] == "OUTPUT"
        assert g["title"] == "Cleanup"

    def test_step_14_is_final(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(14, TOTAL_STEPS, vals, "state")
        assert g["next"] is None

    def test_step_13_points_to_step_14(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(13, TOTAL_STEPS, vals, "state")
        assert "14" in g["next"]

    def test_step_13_no_longer_has_cleanup(self, mod, tmp_path):
        """Step 13 should only present results, not restore branches."""
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(13, TOTAL_STEPS, vals, "state")
        # Filter out STATE_REQ boilerplate (contains "STASHED", "stashed")
        actions = "\n".join(a for a in g["actions"] if "CONTEXT REQUIREMENT" not in a)
        assert "checkout" not in actions.lower()
        assert "stash" not in actions.lower()

    def test_step_14_nonbot_restores_workspace(self, mod, tmp_path):
        """Non-bot runs (normal /pr-review) must restore workspace."""
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(14, TOTAL_STEPS, vals, "state")
        actions = "\n".join(a for a in g["actions"] if "CONTEXT REQUIREMENT" not in a)
        assert "checkout" in actions.lower() or "branch" in actions.lower()

    def test_step_14_bot_mode_is_noop(self, mod, tmp_path):
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(14, TOTAL_STEPS, vals, "state")
        actions = "\n".join(a for a in g["actions"] if "CONTEXT REQUIREMENT" not in a)
        assert "checkout" not in actions.lower()
        assert "stash" not in actions.lower()



class TestStructure:
    """Cross-cutting structural checks."""

    def _vals(self, mod, tmp_path):
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        return mod.load_context_values(str(tmp_path), "42")

    def test_state_req_includes_context_fields(self, mod, tmp_path):
        """STATE_REQ should mention PR purpose and review focus, not just branch/stash."""
        assert "purpose" in mod.STATE_REQ.lower()
        assert "review focus" in mod.STATE_REQ.lower() or "focus" in mod.STATE_REQ.lower()

    def test_steps_1_to_N_have_state_requirement(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        for step in range(1, TOTAL_STEPS + 1):
            g = mod.get_step_guidance(step, TOTAL_STEPS, vals, "state")
            actions = "\n".join(g["actions"])
            assert "CONTEXT REQUIREMENT" in actions, f"Step {step} missing CONTEXT REQUIREMENT"

    def test_non_final_steps_have_next(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        for step in range(0, TOTAL_STEPS):
            g = mod.get_step_guidance(step, TOTAL_STEPS, vals, "state")
            assert g["next"] is not None, f"Step {step} should have next"

    def test_final_step_has_no_next(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        g = mod.get_step_guidance(TOTAL_STEPS, TOTAL_STEPS, vals, "state")
        assert g["next"] is None

    def test_all_steps_have_role_reinforcement(self, mod, tmp_path):
        vals = self._vals(mod, tmp_path)
        for step in range(1, TOTAL_STEPS + 1):
            g = mod.get_step_guidance(step, TOTAL_STEPS, vals, "state")
            actions = "\n".join(g["actions"])
            assert "PR review" in actions or "review" in actions.lower()

    def test_no_template_variables_in_output(self, mod, tmp_path):
        """No step should contain ${...} or <...> template placeholders."""
        vals = self._vals(mod, tmp_path)
        for step in range(0, TOTAL_STEPS + 1):
            g = mod.get_step_guidance(step, TOTAL_STEPS, vals, "state")
            actions = "\n".join(g["actions"])
            assert "${OUTPUT_DIR}" not in actions, f"Step {step} has unresolved ${{OUTPUT_DIR}}"
            assert "${GIT_RANGE}" not in actions, f"Step {step} has unresolved ${{GIT_RANGE}}"
            assert "<GIT_RANGE>" not in actions, f"Step {step} has unresolved <GIT_RANGE>"
            assert "<OUTPUT_DIR>" not in actions, f"Step {step} has unresolved <OUTPUT_DIR>"


class TestFormatOutput:
    def test_header_has_separators(self, mod, tmp_path):
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(1, TOTAL_STEPS, vals, "")
        output = mod.format_output(1, TOTAL_STEPS, g)
        assert "═══" in output
        assert "Step 1/" in output

    def test_next_is_mandatory(self, mod, tmp_path):
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(1, TOTAL_STEPS, vals, "")
        output = mod.format_output(1, TOTAL_STEPS, g)
        assert "MANDATORY" in output

    def test_final_step_shows_complete(self, mod, tmp_path):
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        vals = mod.load_context_values(str(tmp_path), "42")
        g = mod.get_step_guidance(TOTAL_STEPS, TOTAL_STEPS, vals, "state")
        output = mod.format_output(TOTAL_STEPS, TOTAL_STEPS, g)
        assert "COMPLETE" in output


class TestCLIIntegration:
    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_0_exits_0(self, tmp_path):
        r = self._run(
            "--step-number", "0", "--total-steps", str(TOTAL_STEPS),
            "--pr-number", "42", "--output-dir", str(tmp_path), "--thoughts", "",
        )
        assert r.returncode == 0

    def test_invalid_step_exits_1(self, tmp_path):
        r = self._run(
            "--step-number", "99", "--total-steps", str(TOTAL_STEPS),
            "--output-dir", str(tmp_path), "--thoughts", "",
        )
        assert r.returncode == 1


class TestTelemetryIntegration:
    """Verify pipeline calls telemetry at each step."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_0_creates_telemetry_log(self, tmp_path):
        """Step 0 should create a telemetry log file."""
        log_dir = tmp_path / "telemetry-logs"
        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            r = self._run(
                "--step-number", "0", "--total-steps", str(TOTAL_STEPS),
                "--pr-number", "42", "--output-dir", str(tmp_path), "--thoughts", "",
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
                    "--step-number", "0", "--total-steps", str(TOTAL_STEPS),
                    "--pr-number", "42", "--output-dir", str(tmp_path), "--thoughts", "",
                )
            # Pipeline should still succeed
            assert r.returncode == 0
            assert "Step 0/" in r.stdout
        finally:
            log_dir.chmod(0o755)

    def test_step_1_appends_to_telemetry_log(self, tmp_path):
        """Step 1 should append to the log created by step 0."""
        log_dir = tmp_path / "telemetry-logs"
        env = {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}
        with patch.dict(os.environ, env):
            self._run(
                "--step-number", "0", "--total-steps", str(TOTAL_STEPS),
                "--pr-number", "42", "--output-dir", str(tmp_path), "--thoughts", "",
            )
            self._run(
                "--step-number", "1", "--total-steps", str(TOTAL_STEPS),
                "--pr-number", "42", "--output-dir", str(tmp_path), "--thoughts", "state",
            )
        marker = tmp_path / ".telemetry-log-path"
        log_path = marker.read_text().strip()
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "pipeline_start"
        assert json.loads(lines[1])["event"] == "step"

    def test_final_step_writes_pipeline_end(self, tmp_path):
        """Final step should write pipeline_end event."""
        log_dir = tmp_path / "telemetry-logs"
        env = {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}
        (tmp_path / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        with patch.dict(os.environ, env):
            self._run(
                "--step-number", "0", "--total-steps", str(TOTAL_STEPS),
                "--pr-number", "42", "--output-dir", str(tmp_path), "--thoughts", "",
            )
            self._run(
                "--step-number", str(TOTAL_STEPS), "--total-steps", str(TOTAL_STEPS),
                "--pr-number", "42", "--output-dir", str(tmp_path), "--thoughts", "state",
            )
        marker = tmp_path / ".telemetry-log-path"
        log_path = marker.read_text().strip()
        with open(log_path) as f:
            lines = f.readlines()
        last = json.loads(lines[-1])
        assert last["event"] == "pipeline_end"
        assert "summary" in last

"""Tests for linear-issue-pipeline.py — step briefing content (get_step_guidance).

Tests that guidance text for each step contains the right keywords, tool references,
and structural elements. Follows the same pattern as test_review_pipeline.py.
"""

import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "linear_issue_pipeline", SCRIPTS_DIR / "linear-issue-pipeline.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def _guidance_text(g):
    """Flatten all guidance sections into a single searchable string."""
    parts = []
    for key in ("situation", "actions", "handoff"):
        val = g.get(key)
        if val:
            if isinstance(val, list):
                parts.extend(val)
            else:
                parts.append(str(val))
    return "\n".join(parts)


# -- Shared fixtures --

INVESTIGATE_CTX = {"issue_id": "WOOPLUG-1234", "team_prefix": "WOOPLUG"}
FIX_CTX = {"issue_id": "WOOPLUG-5678", "team_prefix": "WOOPLUG"}


# ---------------------------------------------------------------------------
# Step 1: Parse Input
# ---------------------------------------------------------------------------

class TestStep1ParseInput:
    def test_mentions_issue_id(self, mod):
        g = mod.get_step_guidance(1, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert "WOOPLUG-1234" in _guidance_text(g)

    def test_investigate_mode_mentioned(self, mod):
        g = mod.get_step_guidance(1, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "investigate" in text.lower()

    def test_fix_mode_mentioned(self, mod):
        g = mod.get_step_guidance(1, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "fix" in text.lower()
        assert "draft PR" in text or "draft pr" in text.lower()

    def test_references_issue_context_json(self, mod):
        g = mod.get_step_guidance(1, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert "issue-context.json" in _guidance_text(g)

    def test_references_run_config_json(self, mod):
        g = mod.get_step_guidance(1, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert "run-config.json" in _guidance_text(g)

    def test_has_handoff(self, mod):
        g = mod.get_step_guidance(1, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["handoff"] is not None
        assert len(g["handoff"]) > 0

    def test_includes_pipeline_mission(self, mod):
        g = mod.get_step_guidance(1, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "investigator" in text.lower()


# ---------------------------------------------------------------------------
# Step 2: Fetch Issue
# ---------------------------------------------------------------------------

class TestStep2FetchIssue:
    def test_references_linear_mcp(self, mod):
        g = mod.get_step_guidance(2, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "mcp__linear-server__get_issue" in text
        assert "mcp__linear-server__list_comments" in text

    def test_mentions_issue_id(self, mod):
        g = mod.get_step_guidance(2, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert "WOOPLUG-1234" in _guidance_text(g)

    def test_mentions_comments_are_mandatory(self, mod):
        g = mod.get_step_guidance(2, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "comment" in text

    def test_mentions_hard_failure_on_mcp_unavailable(self, mod):
        g = mod.get_step_guidance(2, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "unavailable" in text or "fail" in text

    def test_has_handoff(self, mod):
        g = mod.get_step_guidance(2, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["handoff"] is not None
        handoff_text = "\n".join(g["handoff"]).lower()
        assert "comment" in handoff_text or "details" in handoff_text


# ---------------------------------------------------------------------------
# Step 3: Check Existing Work
# ---------------------------------------------------------------------------

class TestStep3CheckExisting:
    def test_references_github_search(self, mod):
        g = mod.get_step_guidance(3, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "gh" in text  # gh CLI reference

    def test_mentions_merged_pr_handling(self, mod):
        g = mod.get_step_guidance(3, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "merged" in text

    def test_mentions_open_pr_handling(self, mod):
        g = mod.get_step_guidance(3, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "open" in text

    def test_mentions_issue_id_in_search(self, mod):
        g = mod.get_step_guidance(3, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert "WOOPLUG-1234" in _guidance_text(g)

    def test_includes_repo_verification(self, mod):
        """Step 3 must tell the LLM to verify the issue belongs to this repo."""
        g = mod.get_step_guidance(3, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "verify" in text or "belongs" in text
        assert "repo" in text

    def test_repo_verification_checks_linked_prs(self, mod):
        """Should check which repo linked PRs target."""
        g = mod.get_step_guidance(3, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "linked pr" in text or "linked prs" in text

    def test_repo_verification_checks_file_paths(self, mod):
        """Should check if mentioned file paths exist in this codebase."""
        g = mod.get_step_guidance(3, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "file path" in text or "file paths" in text

    def test_repo_mismatch_instructs_stop(self, mod):
        """If issue doesn't belong here, LLM should stop and write failed result."""
        g = mod.get_step_guidance(3, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "STOP" in text or "stop" in text.lower()
        assert "pipeline-result.json" in text

    def test_handoff_includes_repo_verification(self, mod):
        g = mod.get_step_guidance(3, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        handoff_text = "\n".join(g["handoff"]).lower()
        assert "repo" in handoff_text or "verified" in handoff_text


# ---------------------------------------------------------------------------
# Step 4: Gather Context
# ---------------------------------------------------------------------------

class TestStep4GatherContext:
    def test_references_grep_glob(self, mod):
        g = mod.get_step_guidance(4, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "grep" in text or "glob" in text or "search" in text

    def test_references_git_blame(self, mod):
        g = mod.get_step_guidance(4, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "git blame" in text or "git log" in text

    def test_includes_phase_transition(self, mod):
        g = mod.get_step_guidance(4, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "investigation" in text

    def test_has_handoff(self, mod):
        g = mod.get_step_guidance(4, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["handoff"] is not None
        assert len(g["handoff"]) > 0


# ---------------------------------------------------------------------------
# Step 5: Investigate
# ---------------------------------------------------------------------------

class TestStep5Investigate:
    def test_mentions_bug_path(self, mod):
        g = mod.get_step_guidance(5, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "bug" in text

    def test_mentions_feature_path(self, mod):
        g = mod.get_step_guidance(5, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "feature" in text

    def test_mentions_task_path(self, mod):
        g = mod.get_step_guidance(5, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "task" in text

    def test_rca_mandatory_for_bugs(self, mod):
        g = mod.get_step_guidance(5, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "Root Cause Analysis" in text or "RCA" in text
        assert "MANDATORY" in text or "mandatory" in text.lower()

    def test_mentions_duplicate_search(self, mod):
        g = mod.get_step_guidance(5, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "duplicate" in text

    def test_mentions_verify_findings(self, mod):
        g = mod.get_step_guidance(5, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "verify" in text

    def test_has_handoff(self, mod):
        g = mod.get_step_guidance(5, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["handoff"] is not None
        handoff_text = "\n".join(g["handoff"]).lower()
        assert "rca" in handoff_text or "root cause" in handoff_text


# ---------------------------------------------------------------------------
# Step 6: Write Report
# ---------------------------------------------------------------------------

class TestStep6WriteReport:
    def test_references_report_path(self, mod):
        g = mod.get_step_guidance(6, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "investigation-report.md" in text

    def test_includes_report_template(self, mod):
        g = mod.get_step_guidance(6, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        # Should include template structure
        assert "Summary" in text
        assert "Recommendation" in text or "recommendation" in text.lower()

    def test_mentions_verdict(self, mod):
        g = mod.get_step_guidance(6, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "valid" in text or "invalid" in text or "verdict" in text

    def test_has_handoff_on_report_file(self, mod):
        g = mod.get_step_guidance(6, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["handoff"] is not None
        handoff_text = "\n".join(g["handoff"])
        assert "investigation-report.md" in handoff_text


# ---------------------------------------------------------------------------
# Step 7: Post to Linear
# ---------------------------------------------------------------------------

class TestStep7PostToLinear:
    def test_references_linear_mcp_save_comment(self, mod):
        g = mod.get_step_guidance(7, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "mcp__linear-server__save_comment" in text

    def test_mentions_best_effort(self, mod):
        g = mod.get_step_guidance(7, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "best-effort" in text or "not a blocking" in text or "degradation" in text

    def test_mentions_issue_id(self, mod):
        g = mod.get_step_guidance(7, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert "WOOPLUG-1234" in _guidance_text(g)

    def test_has_handoff(self, mod):
        g = mod.get_step_guidance(7, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["handoff"] is not None


# ---------------------------------------------------------------------------
# Fix Steps 8-13 (content tests)
# ---------------------------------------------------------------------------

class TestStep8WritePlan:
    def test_references_writing_plans_skill(self, mod):
        g = mod.get_step_guidance(8, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "writing-plans" in text

    def test_references_plan_file(self, mod):
        g = mod.get_step_guidance(8, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "implementation-plan.md" in text

    def test_includes_phase_transition(self, mod):
        g = mod.get_step_guidance(8, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "implementation" in text

    def test_has_handoff_on_plan_file(self, mod):
        g = mod.get_step_guidance(8, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["handoff"] is not None
        handoff_text = "\n".join(g["handoff"])
        assert "implementation-plan.md" in handoff_text

    def test_includes_complexity_assessment(self, mod):
        g = mod.get_step_guidance(8, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "complexity.json" in text
        assert "small" in text
        assert "medium" in text
        assert "large" in text


class TestStep9Implement:
    def test_references_subagent_skill(self, mod):
        g = mod.get_step_guidance(9, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "subagent-driven-development" in text

    def test_mentions_scope_discipline(self, mod):
        g = mod.get_step_guidance(9, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "scope" in text


class TestStep10Verify:
    def test_references_verification_skill(self, mod):
        g = mod.get_step_guidance(10, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "verification-before-completion" in text

    def test_mentions_tests(self, mod):
        g = mod.get_step_guidance(10, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "test" in text

    def test_includes_complexity_routing_decision(self, mod):
        """Step 10 must tell the orchestrator to skip codex review for small changes."""
        g = mod.get_step_guidance(10, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "complexity" in text.lower()
        assert "small" in text.lower()
        # Should mention skipping steps 11-12 for small
        assert "step 13" in text or "step 11" in text
        # Should mention codex reviewer decision
        assert "codex" in text.lower() or "code-reviewer" in text.lower()

    def test_small_skips_to_step_13(self, mod):
        """Step 10 guidance should say small complexity skips to step 13."""
        g = mod.get_step_guidance(10, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "small" in text
        assert "step 13" in text

    def test_medium_continues_to_step_11(self, mod):
        """Step 10 guidance should say medium/large continues to step 11."""
        g = mod.get_step_guidance(10, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "medium" in text
        assert "step 11" in text

    def test_handoff_includes_routing_decision(self, mod):
        g = mod.get_step_guidance(10, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        handoff_text = "\n".join(g["handoff"]).lower()
        assert "complexity" in handoff_text or "routing" in handoff_text


class TestStep11SelfReview:
    def test_references_iterative_review(self, mod):
        g = mod.get_step_guidance(11, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "iterative" in text.lower()
        assert "review" in text.lower()

    def test_references_review_loop(self, mod):
        g = mod.get_step_guidance(11, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "merge-base" in text.lower() or "merge_base" in text.lower()

    def test_includes_phase_transition(self, mod):
        g = mod.get_step_guidance(11, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "validation" in text or "validate" in text


class TestStep12ReVerify:
    def test_is_no_op(self, mod):
        g = mod.get_step_guidance(12, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "no-op" in text or "no action" in text

    def test_references_iterative_review_loop(self, mod):
        g = mod.get_step_guidance(12, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "iterative review loop" in text or "review round" in text

    def test_handoff_is_none(self, mod):
        g = mod.get_step_guidance(12, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["handoff"] is None


class TestStep13CreateDraftPR:
    def test_references_gh_pr_create(self, mod):
        g = mod.get_step_guidance(13, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "gh pr create" in text

    def test_mentions_draft_flag(self, mod):
        g = mod.get_step_guidance(13, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "--draft" in text

    def test_mentions_issue_id_in_pr_body(self, mod):
        g = mod.get_step_guidance(13, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "WOOPLUG-5678" in text

    def test_mentions_fallback_on_failure(self, mod):
        g = mod.get_step_guidance(13, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "fail" in text or "degradation" in text


# ---------------------------------------------------------------------------
# Step 14: Present Results
# ---------------------------------------------------------------------------

class TestStep14PresentResults:
    def test_references_pipeline_result_json(self, mod):
        g = mod.get_step_guidance(14, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "pipeline-result.json" in text

    def test_includes_result_schema(self, mod):
        g = mod.get_step_guidance(14, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "status" in text
        assert "verdict" in text
        assert "degradation_notes" in text

    def test_mentions_status_values(self, mod):
        g = mod.get_step_guidance(14, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "success" in text
        assert "degraded" in text
        assert "failed" in text

    def test_mentions_taskstop(self, mod):
        g = mod.get_step_guidance(14, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "TaskStop" in text

    def test_includes_phase_transition(self, mod):
        g = mod.get_step_guidance(14, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "output" in text or "present" in text

    def test_fix_mode_includes_pr_url(self, mod):
        g = mod.get_step_guidance(14, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "pr_url" in text

    def test_has_handoff(self, mod):
        g = mod.get_step_guidance(14, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["handoff"] is not None
        handoff_text = "\n".join(g["handoff"])
        assert "pipeline-result.json" in handoff_text


# ---------------------------------------------------------------------------
# Cross-Step: Phase assignment correctness
# ---------------------------------------------------------------------------

class TestPhaseAssignment:
    """Verify each step returns the correct phase in guidance."""

    EXPECTED_PHASES = {
        1: "SETUP", 2: "SETUP", 3: "SETUP",
        4: "INVESTIGATION", 5: "INVESTIGATION", 6: "INVESTIGATION", 7: "INVESTIGATION",
        8: "IMPLEMENTATION", 9: "IMPLEMENTATION", 10: "IMPLEMENTATION",
        11: "VALIDATION", 12: "VALIDATION",
        13: "OUTPUT", 14: "OUTPUT",
    }

    @pytest.mark.parametrize("step,expected_phase", EXPECTED_PHASES.items())
    def test_phase_matches(self, mod, step, expected_phase):
        g = mod.get_step_guidance(step, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["phase"] == expected_phase


# ---------------------------------------------------------------------------
# Cross-Step: Investigate vs Fix mode guidance differences
# ---------------------------------------------------------------------------

class TestModeGuidanceDifferences:
    def test_step_7_mentions_investigate_jump(self, mod):
        """Step 7 in investigate mode should hint that pipeline jumps to 14."""
        g = mod.get_step_guidance(7, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        # Should mention that investigate mode stops here or jumps
        assert "step 14" in text or "present results" in text.lower() or "jump" in text

    def test_step_7_fix_mode_does_not_mention_jump(self, mod):
        """Step 7 in fix mode must NOT tell the LLM to jump to step 14."""
        g = mod.get_step_guidance(7, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "jumps to step 14" not in text
        assert "Investigate mode" not in text

    def test_step_14_mode_in_schema(self, mod):
        """Step 14 guidance should include the current mode in the result schema."""
        g_inv = mod.get_step_guidance(14, "investigate", {}, INVESTIGATE_CTX,
                                      config={}, output_dir="/tmp/test")
        g_fix = mod.get_step_guidance(14, "fix", {}, FIX_CTX,
                                      config={}, output_dir="/tmp/test")
        assert "investigate" in _guidance_text(g_inv)
        assert "fix" in _guidance_text(g_fix)


# ---------------------------------------------------------------------------
# Orchestration: Step 14 writes pipeline-result.json
# ---------------------------------------------------------------------------

class TestStep14Orchestration:
    def _write_report(self, tmp_path, content="# Report\n\nValid bug."):
        """Helper to create a report file so the orchestrator sees real output."""
        (tmp_path / "investigation-report.md").write_text(content)

    def test_writes_pipeline_result_json(self, mod, tmp_path):
        self._write_report(tmp_path)
        state = {
            "completed_steps": list(range(1, 14)),
            "degradation_notes": [],
            "verdict": "valid",
            "pr_url": None,
            "linear_comment_posted": True,
            "codex_review_applied": False,
        }
        context = {"issue_id": "WOOPLUG-1234"}
        mod._orchestrate_step(14, "investigate", {}, state, context, str(tmp_path))
        result_path = tmp_path / "pipeline-result.json"
        assert result_path.exists()
        result = json.loads(result_path.read_text())
        assert result["status"] == "success"
        assert result["mode"] == "investigate"
        assert result["issue_id"] == "WOOPLUG-1234"

    def test_writes_degraded_status(self, mod, tmp_path):
        self._write_report(tmp_path)
        state = {
            "completed_steps": list(range(1, 14)),
            "degradation_notes": ["Linear comment posting failed"],
            "verdict": "valid",
        }
        context = {"issue_id": "TEST-1"}
        mod._orchestrate_step(14, "fix", {}, state, context, str(tmp_path))
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert len(result["degradation_notes"]) == 1

    def test_partial_run_reports_failed_not_success(self, mod, tmp_path):
        """P1 fix: a run without verdict or report must report failed, not success."""
        state = {
            "completed_steps": [1],
            "degradation_notes": [],
        }
        context = {"issue_id": "TEST-1"}
        mod._orchestrate_step(14, "investigate", {}, state, context, str(tmp_path))
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "failed"
        assert result["verdict"] is None
        assert any("No verdict" in n for n in result["degradation_notes"])

    def test_fix_mode_without_pr_url_reports_degraded(self, mod, tmp_path):
        """Fix mode completing without a PR URL is degraded, not success."""
        self._write_report(tmp_path)
        state = {
            "completed_steps": list(range(1, 14)),
            "degradation_notes": [],
            "verdict": "valid",
            "pr_url": None,
            "linear_comment_posted": True,
        }
        context = {"issue_id": "TEST-1"}
        mod._orchestrate_step(14, "fix", {}, state, context, str(tmp_path))
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert any("draft PR" in n for n in result["degradation_notes"])

    def test_fix_mode_resolved_issue_without_pr_is_success(self, mod, tmp_path):
        """Fix mode with resolved issue doesn't need a PR URL — success is valid."""
        self._write_report(tmp_path)
        state = {
            "completed_steps": list(range(1, 8)) + [14],
            "degradation_notes": [],
            "verdict": "already_fixed",
            "issue_resolved": True,
        }
        context = {"issue_id": "TEST-1"}
        mod._orchestrate_step(14, "fix", {}, state, context, str(tmp_path))
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "success"

    def test_emits_events(self, mod, tmp_path):
        """Step 14 orchestration emits pipeline_complete event."""
        self._write_report(tmp_path)
        events_spec = importlib.util.spec_from_file_location(
            "pipeline_events", SCRIPTS_DIR / "pipeline_events.py"
        )
        events_mod = importlib.util.module_from_spec(events_spec)
        events_spec.loader.exec_module(events_mod)
        emitter = events_mod.PipelineEventEmitter(str(tmp_path))

        state = {"completed_steps": list(range(1, 14)), "degradation_notes": [],
                 "verdict": "valid"}
        context = {"issue_id": "TEST-1"}
        mod._orchestrate_step(14, "investigate", {}, state, context, str(tmp_path), events=emitter)

        events_path = tmp_path / "pipeline-events.jsonl"
        assert events_path.exists()
        lines = events_path.read_text().strip().split("\n")
        events = [json.loads(l) for l in lines if l]
        event_types = [e["event"] for e in events]
        assert "step_started" in event_types
        assert "pipeline_complete" in event_types

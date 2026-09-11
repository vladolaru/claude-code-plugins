"""Tests for linear/pipeline.py — step briefing content (get_step_guidance).

Tests that guidance text for each step contains the right keywords, tool references,
and structural elements. Follows the same pattern as test_review_pipeline.py.

One pin per step contract: the artifact a step reads, the artifact its handoff
gates, the tool or command it must invoke, and any routing it decides. Word-presence
checks on plain wording (not a contract) are not pinned here.
"""

import importlib.util
import json
import os
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # linear/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "linear_issue_pipeline", SCRIPTS_DIR / "linear" / "pipeline.py"
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
    def test_fix_mode_mentioned_and_writes_context_with_handoff(self, mod):
        g_fix = mod.get_step_guidance(1, "fix", {}, FIX_CTX,
                                      config={}, output_dir="/tmp/test")
        fix_text = _guidance_text(g_fix)
        assert "fix" in fix_text.lower()
        assert "draft PR" in fix_text or "draft pr" in fix_text.lower()

        g = mod.get_step_guidance(1, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "issue-context.json" in text
        assert "run-config.json" in text
        assert g["handoff"] is not None
        assert len(g["handoff"]) > 0


# ---------------------------------------------------------------------------
# Step 2: Fetch Issue
# ---------------------------------------------------------------------------

class TestStep2FetchIssue:
    def test_references_linear_mcp_with_handoff(self, mod):
        g = mod.get_step_guidance(2, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "mcp__linear-server__get_issue" in text
        assert "mcp__linear-server__list_comments" in text
        assert g["handoff"] is not None
        handoff_text = "\n".join(g["handoff"]).lower()
        assert "comment" in handoff_text or "details" in handoff_text


# ---------------------------------------------------------------------------
# Step 3: Check Existing Work
# ---------------------------------------------------------------------------

class TestStep3CheckExisting:
    def test_repo_verification_checks_linked_prs_and_file_paths(self, mod):
        g = mod.get_step_guidance(3, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "linked pr" in text or "linked prs" in text
        assert "file path" in text or "file paths" in text

    def test_repo_mismatch_instructs_stop(self, mod):
        """If issue doesn't belong here, LLM should stop and write failed result."""
        g = mod.get_step_guidance(3, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "STOP" in text or "stop" in text.lower()
        assert "pipeline-result.json" in text

# ---------------------------------------------------------------------------
# Step 4: Gather Context
# ---------------------------------------------------------------------------

class TestStep4GatherContext:
    def test_references_git_blame_with_handoff(self, mod):
        g = mod.get_step_guidance(4, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "git blame" in text or "git log" in text
        assert g["handoff"] is not None
        assert len(g["handoff"]) > 0

    def test_surfaces_caller_requested_focus(self, mod):
        g = mod.get_step_guidance(
            4,
            "investigate",
            {},
            INVESTIGATE_CTX,
            config={"additional_instructions": "Focus on webhook retry behavior and related failure handling"},
            output_dir="/tmp/test",
        )
        text = _guidance_text(g)
        assert "Caller-Requested Focus" in text
        assert "webhook retry behavior" in text


# ---------------------------------------------------------------------------
# Step 5: Investigate
# ---------------------------------------------------------------------------

class TestStep5Investigate:
    def test_rca_mandatory_for_bugs_with_handoff(self, mod):
        g = mod.get_step_guidance(5, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "Root Cause Analysis" in text or "RCA" in text
        assert "MANDATORY" in text or "mandatory" in text.lower()
        handoff_text = "\n".join(g["handoff"]).lower()
        assert "rca" in handoff_text or "root cause" in handoff_text


# ---------------------------------------------------------------------------
# Step 6: Write Report
# ---------------------------------------------------------------------------

class TestStep6WriteReport:
    def test_report_path_in_text_and_handoff(self, mod):
        g = mod.get_step_guidance(6, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "investigation-report.md" in text
        handoff_text = "\n".join(g["handoff"])
        assert "investigation-report.md" in handoff_text


# ---------------------------------------------------------------------------
# Step 7: Post to Linear
# ---------------------------------------------------------------------------

class TestStep7PostToLinear:
    def test_references_linear_mcp_save_comment_with_handoff(self, mod):
        g = mod.get_step_guidance(7, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "mcp__linear-server__save_comment" in text
        assert g["handoff"] is not None


# ---------------------------------------------------------------------------
# Step 8: Assess Clarity
# ---------------------------------------------------------------------------

class TestStep8AssessClarity:
    def test_clarity_assessment_schema_with_handoff(self, mod):
        g = mod.get_step_guidance(8, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "problem_statement" in text or "Problem statement" in text
        assert "reproduction" in text.lower() or "scope" in text.lower()
        assert "success_criteria" in text or "Success criteria" in text
        assert "clarity-assessment.json" in text
        assert "clear_enough" in text
        assert "questions_for_author" in text
        handoff_text = "\n".join(g.get("handoff", []))
        assert "clarity-assessment.json" in handoff_text

    def test_works_in_investigate_mode(self, mod):
        g = mod.get_step_guidance(8, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g is not None
        assert g["title"] == "Assess Clarity"


# ---------------------------------------------------------------------------
# Fix Steps 9-14 (content tests)
# ---------------------------------------------------------------------------

class TestStep9WritePlan:
    def test_plan_file_in_text_and_handoff(self, mod):
        g = mod.get_step_guidance(9, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "implementation-plan.md" in text
        handoff_text = "\n".join(g["handoff"])
        assert "implementation-plan.md" in handoff_text

    def test_includes_complexity_assessment(self, mod):
        g = mod.get_step_guidance(9, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "complexity.json" in text
        assert "small" in text
        assert "medium" in text
        assert "large" in text

class TestStep10Implement:
    def test_references_subagent_skill(self, mod):
        g = mod.get_step_guidance(10, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "subagent-driven-development" in text


class TestStep11Verify:
    def test_complexity_routes_small_to_step14_others_to_step12(self, mod):
        """Step 11 guidance must name both routing branches: small complexity
        skips to step 14, medium/large continues to step 12's code review."""
        g = mod.get_step_guidance(11, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "small" in text
        assert "step 14" in text
        assert "medium" in text
        assert "step 12" in text
        assert "code review" in text or "code-reviewer" in text

class TestStep12SelfReview:
    def test_uses_module_invocation(self, mod):
        """Step 12 must use 'python3 -m iterative_review', not direct path."""
        g = mod.get_step_guidance(12, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "-m iterative_review" in text

    def test_detects_default_branch_dynamically(self, mod):
        """Step 12 must not hardcode 'main' as the merge-base branch."""
        g = mod.get_step_guidance(12, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "symbolic-ref" in text or "BASE_BRANCH" in text


STEP12_FLAG_ROWS = [
    pytest.param("adaptive_iterative_review", "--adaptive-effort", True, id="adaptive-effort-enabled"),
    pytest.param("adaptive_iterative_review", "--adaptive-effort", False, id="adaptive-effort-disabled"),
    pytest.param("autonomous_iterative_review", "--autonomous", True, id="autonomous-enabled"),
    pytest.param("autonomous_iterative_review", "--autonomous", False, id="autonomous-disabled"),
]


class TestStep12Flags:
    """Step 12 passes --adaptive-effort/--autonomous only when the matching
    config flag is enabled, and never on the --action advance line."""

    @pytest.mark.parametrize("config_key,flag,enabled", STEP12_FLAG_ROWS)
    def test_step12_flag_reflects_config(self, mod, config_key, flag, enabled):
        config = {"mode": "fix", config_key: True} if enabled else {"mode": "fix"}
        g = mod.get_step_guidance(12, "fix", {}, FIX_CTX,
                                  config=config, output_dir="/tmp/test")
        actions_text = "\n".join(g["actions"])
        if enabled:
            # Should appear at least twice: round 1 command and round N command
            assert actions_text.count(flag) >= 2
        else:
            assert flag not in actions_text
        for line in g["actions"]:
            if "--action advance" in line:
                assert flag not in line


class TestStep13ReVerify:
    def test_verification_already_handled_with_no_handoff(self, mod):
        g = mod.get_step_guidance(13, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g).lower()
        assert "already handled" in text or "redundant" in text
        assert g["handoff"] is None


class TestStep14CreateDraftPR:
    def test_gh_pr_create_draft_with_issue_id(self, mod):
        g = mod.get_step_guidance(14, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "gh pr create" in text
        assert "--draft" in text
        assert "WOOPLUG-5678" in text

    def test_includes_deferred_items_in_pr(self, mod):
        g = mod.get_step_guidance(14, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "deferred" in text.lower()
        assert "follow-up" in text.lower() or "follow-ups" in text.lower()


# ---------------------------------------------------------------------------
# Step 15: Present Results
# ---------------------------------------------------------------------------

class TestStep15PresentResults:
    def test_result_schema_and_status_values(self, mod):
        g = mod.get_step_guidance(15, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        text = _guidance_text(g)
        assert "status" in text
        assert "verdict" in text
        assert "degradation_notes" in text
        assert "success" in text
        assert "degraded" in text
        assert "failed" in text

        g_fix = mod.get_step_guidance(15, "fix", {}, FIX_CTX,
                                      config={}, output_dir="/tmp/test")
        assert "pr_url" in _guidance_text(g_fix)

    def test_has_handoff(self, mod):
        g = mod.get_step_guidance(15, "investigate", {}, INVESTIGATE_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["handoff"] is not None
        handoff_text = "\n".join(g["handoff"])
        assert "pipeline-result.json" in handoff_text


# ---------------------------------------------------------------------------
# Cross-Step: Phase assignment correctness
# ---------------------------------------------------------------------------

class TestPhaseAssignment:
    """get_step_guidance copies phase from the step definition; one
    representative row proves the copy. The phase vocabulary itself is
    pinned against STEP_SEQUENCE by test_pipeline.py::test_phases_are_valid."""

    def test_phase_matches(self, mod):
        g = mod.get_step_guidance(4, "fix", {}, FIX_CTX,
                                  config={}, output_dir="/tmp/test")
        assert g["phase"] == "INVESTIGATION"


# ---------------------------------------------------------------------------
# Cross-Step: Investigate vs Fix mode guidance differences
# ---------------------------------------------------------------------------

class TestModeGuidanceDifferences:
    def test_step_7_jump_hint_differs_by_mode(self, mod):
        """Step 7 hints at jumping to step 15 in investigate mode, and must
        not in fix mode."""
        g_inv = mod.get_step_guidance(7, "investigate", {}, INVESTIGATE_CTX,
                                      config={}, output_dir="/tmp/test")
        inv_text = _guidance_text(g_inv).lower()
        assert "step 15" in inv_text or "present results" in inv_text or "jump" in inv_text

        g_fix = mod.get_step_guidance(7, "fix", {}, FIX_CTX,
                                      config={}, output_dir="/tmp/test")
        fix_text = _guidance_text(g_fix)
        assert "jumps to step 15" not in fix_text
        assert "Investigate mode" not in fix_text

    def test_step_15_mode_in_schema(self, mod):
        """Step 15 guidance should include the current mode in the result schema."""
        g_inv = mod.get_step_guidance(15, "investigate", {}, INVESTIGATE_CTX,
                                      config={}, output_dir="/tmp/test")
        g_fix = mod.get_step_guidance(15, "fix", {}, FIX_CTX,
                                      config={}, output_dir="/tmp/test")
        assert "investigate" in _guidance_text(g_inv)
        assert "fix" in _guidance_text(g_fix)


# ---------------------------------------------------------------------------
# Orchestration: Step 15 writes pipeline-result.json
# ---------------------------------------------------------------------------

class TestStep15Orchestration:
    def _write_report(self, tmp_path, content="# Report\n\nValid bug."):
        """Helper to create a report file so the orchestrator sees real output."""
        (tmp_path / "investigation-report.md").write_text(content)

    def _write_passing_assessment(self, tmp_path):
        """Helper to create a passing clarity assessment so the gate doesn't block."""
        import json as _json
        (tmp_path / "clarity-assessment.json").write_text(_json.dumps({
            "clear_enough": True,
            "hard_gates": {},
            "soft_signals": {},
        }))

    def test_writes_pipeline_result_json(self, mod, tmp_path):
        self._write_report(tmp_path)
        self._write_passing_assessment(tmp_path)
        state = {
            "completed_steps": list(range(1, 15)),
            "degradation_notes": [],
            "verdict": "valid",
            "pr_url": None,
            "linear_comment_posted": True,
            "independent_code_review": "not_run",
        }
        context = {"issue_id": "WOOPLUG-1234"}
        mod._orchestrate_step(15, "investigate", {}, state, context, str(tmp_path))
        result_path = tmp_path / "pipeline-result.json"
        assert result_path.exists()
        result = json.loads(result_path.read_text())
        assert result["status"] == "success"
        assert result["mode"] == "investigate"
        assert result["issue_id"] == "WOOPLUG-1234"

    def test_writes_degraded_status(self, mod, tmp_path):
        self._write_report(tmp_path)
        self._write_passing_assessment(tmp_path)
        state = {
            "completed_steps": list(range(1, 15)),
            "degradation_notes": ["Linear comment posting failed"],
            "verdict": "valid",
        }
        context = {"issue_id": "TEST-1"}
        mod._orchestrate_step(15, "fix", {}, state, context, str(tmp_path))
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
        mod._orchestrate_step(15, "investigate", {}, state, context, str(tmp_path))
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "failed"
        assert result["verdict"] is None
        assert any("No verdict" in n for n in result["degradation_notes"])

    def test_fix_mode_without_pr_url_reports_degraded(self, mod, tmp_path):
        """Fix mode completing without a PR URL is degraded, not success."""
        self._write_report(tmp_path)
        self._write_passing_assessment(tmp_path)
        state = {
            "completed_steps": list(range(1, 15)),
            "degradation_notes": [],
            "verdict": "valid",
            "pr_url": None,
            "linear_comment_posted": True,
        }
        context = {"issue_id": "TEST-1"}
        mod._orchestrate_step(15, "fix", {}, state, context, str(tmp_path))
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert any("draft PR" in n for n in result["degradation_notes"])

    def test_fix_mode_resolved_issue_without_pr_is_success(self, mod, tmp_path):
        """Fix mode with resolved issue doesn't need a PR URL — success is valid."""
        self._write_report(tmp_path)
        state = {
            "completed_steps": list(range(1, 8)) + [15],
            "degradation_notes": [],
            "verdict": "already_fixed",
            "issue_resolved": True,
        }
        context = {"issue_id": "TEST-1"}
        mod._orchestrate_step(15, "fix", {}, state, context, str(tmp_path))
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "success"

    def test_emits_events(self, mod, tmp_path):
        """Step 15 orchestration emits pipeline_complete event."""
        self._write_report(tmp_path)
        events_spec = importlib.util.spec_from_file_location(
            "pipeline_events", SCRIPTS_DIR / "linear" / "events.py"
        )
        events_mod = importlib.util.module_from_spec(events_spec)
        events_spec.loader.exec_module(events_mod)
        emitter = events_mod.PipelineEventEmitter(str(tmp_path))

        state = {"completed_steps": list(range(1, 15)), "degradation_notes": [],
                 "verdict": "valid"}
        context = {"issue_id": "TEST-1"}
        mod._orchestrate_step(15, "investigate", {}, state, context, str(tmp_path), events=emitter)

        events_path = tmp_path / "pipeline-events.jsonl"
        assert events_path.exists()
        lines = events_path.read_text().strip().split("\n")
        events = [json.loads(l) for l in lines if l]
        event_types = [e["event"] for e in events]
        assert "step_started" in event_types
        assert "pipeline_complete" in event_types

"""Tests for review/pipeline.py — step briefing output (get_step_guidance)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_fixtures import COMPLETE_CONTEXT
from conftest import PIPELINE_SCRIPT_PATH as SCRIPT_PATH


@pytest.fixture(scope="module")
def mod(pipeline_mod):
    """Module-scoped alias — delegates to session-scoped pipeline_mod."""
    return pipeline_mod


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


class TestStructuredDataDiscipline:
    """Artifact discipline: verification checkpoints, handoff gates, schema-not-placeholders."""

    def test_step_3_handoff_has_verification(self, mod, tmp_path):
        """Step 3 handoff should instruct verifying change-purpose.md exists."""
        state = {"completed_steps": [], "resolved_params": {"has_unfetched_issues": False}}
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(3, "pr", state, ctx, output_dir=str(tmp_path))
        assert g.get("handoff") is not None
        handoff_text = "\n".join(g["handoff"])
        assert "verify" in handoff_text.lower() or "confirm" in handoff_text.lower() or "exists" in handoff_text.lower()

    def test_step_4_handoff_has_verification(self, mod, tmp_path):
        """Step 4 handoff should instruct verifying change-purpose.md exists."""
        state = {"resolved_params": {"has_unfetched_issues": True}, "completed_steps": [1, 2, 3]}
        ctx = COMPLETE_CONTEXT
        g = mod.get_step_guidance(4, "pr", state, ctx, output_dir=str(tmp_path))
        assert g.get("handoff") is not None
        handoff_text = "\n".join(g["handoff"])
        assert "verify" in handoff_text.lower() or "confirm" in handoff_text.lower() or "exists" in handoff_text.lower()

    def test_step_8_has_handoff(self, mod, tmp_path):
        """Step 8 should gate on reconciliation output files."""
        state = {
            "completed_steps": [1, 3, 5, 6, 7],
            "resolved_params": {"git_range": "abc..HEAD"},
            "agents": {"dispatched": ["pr-reviewer"], "completed": ["pr-reviewer"], "failed": []},
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        assert g.get("handoff") is not None
        handoff_text = "\n".join(g["handoff"])
        assert "review-findings.json" in handoff_text

    def test_step_9_has_handoff(self, mod, tmp_path):
        """Step 9 should gate on review-report.md."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(9, "pr", state, ctx, output_dir=str(tmp_path))
        assert g.get("handoff") is not None
        handoff_text = "\n".join(g["handoff"])
        assert "review-report.md" in handoff_text

    def test_step_10_has_handoff(self, mod, tmp_path):
        """Step 10 should gate on both verdict files."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        assert g.get("handoff") is not None
        handoff_text = "\n".join(g["handoff"])
        assert "decision-critic-verdict.json" in handoff_text
        assert "review-verdict.json" in handoff_text

    def test_step_10_uses_schema_not_placeholders(self, mod, tmp_path):
        """Step 10 JSON examples should show options, not copyable defaults."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        # Should have schema-style options
        assert "STAND" in text and "REVISE" in text and "ESCALATE" in text
        assert "APPROVE" in text and "REQUEST_CHANGES" in text and "COMMENT" in text
        # Should NOT have a bare {"verdict": "STAND"} (literal copyable value)
        # The schema format like <STAND | REVISE | ESCALATE> is acceptable
        import re
        bare_stand = re.search(r'"verdict":\s*"STAND"', text)
        bare_rc = re.search(r'"verdict":\s*"REQUEST_CHANGES"', text)
        assert bare_stand is None, "Found copyable placeholder: STAND"
        assert bare_rc is None, "Found copyable placeholder: REQUEST_CHANGES"


class TestStep2RepoSetup:
    """Step 2: Repo Setup — briefing reflects workspace setup result."""

    def test_success_confirms_checkout(self, mod, tmp_path):
        """On success, briefing confirms checkout, not instructs it."""
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        state = {
            "completed_steps": [1],
            "workspace": {"original_branch": "main", "stash_ref": None},
            "workspace_setup_result": {
                "original_branch": "main",
                "stash_ref": None,
                "was_dirty": False,
                "checkout_ok": True,
            },
        }
        ctx = {"git": {}}
        g = mod.get_step_guidance(2, "pr", state, ctx, config=config)
        text = "\n".join(g["situation"] + g["actions"])
        assert "successfully" in text.lower() or "ready" in text.lower()
        assert "--original-branch" not in text

    def test_success_with_stash(self, mod, tmp_path):
        """Dirty workspace: briefing mentions stash was created."""
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        state = {
            "completed_steps": [1],
            "workspace": {"original_branch": "develop", "stash_ref": "stash@{0}"},
            "workspace_setup_result": {
                "original_branch": "develop",
                "stash_ref": "stash@{0}",
                "was_dirty": True,
                "checkout_ok": True,
            },
        }
        ctx = {"git": {}}
        g = mod.get_step_guidance(2, "pr", state, ctx, config=config)
        text = "\n".join(g["situation"])
        assert "stash" in text.lower()
        assert "develop" in text

    def test_failure_falls_back_to_manual(self, mod, tmp_path):
        """On failure, briefing provides manual instructions."""
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        state = {
            "completed_steps": [1],
            "workspace": {"original_branch": None, "stash_ref": None},
            "workspace_setup_result": {
                "error": "gh pr checkout 42 failed",
                "checkout_ok": False,
            },
        }
        ctx = {"git": {}}
        g = mod.get_step_guidance(2, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "git status" in text
        assert "checkout" in text.lower()

    def test_no_result_falls_back_to_manual(self, mod, tmp_path):
        """No workspace_setup_result at all: fall back to manual."""
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        state = {
            "completed_steps": [1],
            "workspace": {"original_branch": None, "stash_ref": None},
        }
        ctx = {"git": {}}
        g = mod.get_step_guidance(2, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "checkout" in text.lower()


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

    def test_presents_linked_issues(self, mod, tmp_path):
        """Should present linked issue details in situation."""
        state = {"completed_steps": [1, 2]}
        ctx = self._make_context()
        g = mod.get_step_guidance(3, "pr", state, ctx)
        text = "\n".join(g["situation"])
        assert "WOOPLUG-1234" in text or "issue" in text.lower()

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
            "dispatch_plan_summary": {"dispatched": 7, "skipped": 3, "conditional": 2},
            "dispatch_plan_agents": [
                {"name": "pr-reviewer", "focus": "PR overall goal alignment, cross-domain bugs and regressions, overall code quality", "status": "DISPATCH", "reason": "always dispatch (domain has files)"},
                {"name": "security-reviewer", "focus": "XSS, SQL injection, CSRF, sanitization", "status": "SKIPPED", "reason": "no files in security domain"},
                {"name": "architecture-reviewer", "focus": "SOLID, design patterns, coupling", "status": "DISPATCH", "reason": "conditional (large change)"},
            ],
        }

    def test_presents_dispatch_plan_summary(self, mod, tmp_path):
        state = self._make_state_with_plan()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(5, "pr", state, ctx)
        text = "\n".join(g["situation"])
        # Human-readable summary lists dispatched and skipped agents with focus
        assert "pr-reviewer" in text
        assert "security-reviewer" in text
        assert "Dispatching" in text
        assert "Skipped" in text
        # Raw JSON should NOT be inlined
        full_text = "\n".join(g["actions"] + g["situation"])
        assert not ("python3" in full_text and "plan_dispatch.py" in full_text)

    def test_shows_focus_for_agents(self, mod, tmp_path):
        """Step 5 should show what each agent does so the LLM can make informed override decisions."""
        state = self._make_state_with_plan()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(5, "pr", state, ctx)
        text = "\n".join(g["situation"])
        # Focus descriptions should be visible for both dispatched and skipped agents
        assert "goal alignment" in text.lower()  # pr-reviewer's focus
        assert "XSS" in text  # security-reviewer's focus
        assert "SOLID" in text  # architecture-reviewer's focus

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


class TestStep5QuickMode:
    """Step 5 quick mode: filters SKIPPED_QUICK_MODE agents from display + aggressive nudge."""

    def _make_state_with_quick_plan(self):
        """State with quick mode agents included."""
        return {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3],
            "dispatch_plan_summary": {"dispatched": 5, "skipped": 2, "conditional": 1},
            "dispatch_plan_agents": [
                {"name": "pr-reviewer", "focus": "PR overall goal alignment", "status": "DISPATCH", "reason": "always dispatch (domain has files)"},
                {"name": "security-reviewer", "focus": "XSS, SQL injection", "status": "DISPATCH", "reason": "keywords matched (commits: auth)"},
                {"name": "wp-architecture-reviewer", "focus": "WordPress hooks", "status": "SKIPPED_QUICK_MODE", "reason": "excluded in quick review mode"},
                {"name": "history-insights-reviewer", "focus": "Git history", "status": "SKIPPED_QUICK_MODE", "reason": "excluded in quick review mode"},
                {"name": "reliability-reviewer", "focus": "Error handling", "status": "SKIPPED_QUICK_MODE", "reason": "excluded in quick review mode"},
            ],
        }

    def test_excluded_agents_not_in_situation(self, mod, tmp_path):
        """SKIPPED_QUICK_MODE agents should not appear in the briefing."""
        state = self._make_state_with_quick_plan()
        config = {"quick": True}
        g = mod.get_step_guidance(5, "pr", state, {}, config=config)
        text = "\n".join(g["situation"])
        assert "pr-reviewer" in text
        assert "security-reviewer" in text
        assert "wp-architecture-reviewer" not in text
        assert "history-insights-reviewer" not in text
        assert "reliability-reviewer" not in text

    def test_aggressive_override_nudge(self, mod, tmp_path):
        """Quick mode should nudge the orchestrator to be more aggressive with skips."""
        state = self._make_state_with_quick_plan()
        config = {"quick": True}
        g = mod.get_step_guidance(5, "pr", state, {}, config=config)
        text = "\n".join(g["actions"])
        assert "quick" in text.lower()

    def test_normal_mode_shows_all_agents(self, mod, tmp_path):
        """Without quick mode, all agents shown including those with unusual statuses."""
        state = self._make_state_with_quick_plan()
        config = {"quick": False}
        g = mod.get_step_guidance(5, "pr", state, {}, config=config)
        text = "\n".join(g["situation"])
        # In normal mode, SKIPPED_QUICK_MODE agents should still be visible
        assert "wp-architecture-reviewer" in text


class TestStep5AdditionalInstructions:
    """Step 5: additional_instructions surfaced as Reviewer-Requested Focus."""

    def _make_state_with_plan(self):
        return {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3],
            "dispatch_plan_summary": {"dispatched": 2, "skipped": 1, "conditional": 0},
            "dispatch_plan_agents": [
                {"name": "pr-reviewer", "focus": "PR goal alignment", "status": "DISPATCH", "reason": "always dispatch (domain has files)"},
                {"name": "security-reviewer", "focus": "XSS, SQL injection", "status": "SKIPPED", "reason": "no files in security domain"},
            ],
        }

    def test_additional_instructions_in_actions(self, mod, tmp_path):
        """When config has additional_instructions, actions contain Reviewer-Requested Focus."""
        state = self._make_state_with_plan()
        config = {"additional_instructions": "Pay special attention to error handling in the webhook path"}
        g = mod.get_step_guidance(5, "pr", state, {}, config=config)
        text = "\n".join(g["actions"])
        assert "Reviewer-Requested Focus" in text
        assert "Pay special attention to error handling in the webhook path" in text

    def test_no_additional_instructions_no_section(self, mod, tmp_path):
        """When config does NOT have additional_instructions, no Reviewer-Requested Focus section."""
        state = self._make_state_with_plan()
        g = mod.get_step_guidance(5, "pr", state, {})
        text = "\n".join(g["actions"])
        assert "Reviewer-Requested Focus" not in text


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
        assert "bootstrap.py" in text

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
        """Should reference agents_status.py for monitoring."""
        state = self._make_state_with_agents()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(6, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "agents_status.py" in text

    def test_step6_recomputes_dispatch_plan_summary(self, mod, tmp_path):
        """Step 6 orchestration must recompute summary from final dispatch-plan.json (post-override)."""
        import json

        # Write a dispatch plan with overrides applied
        plan = {
            "agents": [
                {"name": "pr-reviewer", "status": "DISPATCH", "reason": "always"},
                {"name": "security-reviewer", "status": "DISPATCH", "reason": "keywords"},
                {"name": "concurrency-reviewer", "status": "SKIPPED_OVERRIDE", "reason": "conditional", "override_reason": "test"},
                {"name": "a11y-reviewer", "status": "SKIPPED", "reason": "no files"},
            ]
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3, 5],
            # Pre-override summary (stale — should be overwritten)
            "dispatch_plan_summary": {"dispatched": 3, "skipped": 1, "conditional": 1},
        }
        config = {"mode": "pr", "interactive": True}
        context = {"git": {"git_range": "abc..HEAD"}}

        mod._orchestrate_step(6, "pr", config, state, context, str(tmp_path))

        # Summary should reflect post-override counts
        summary = state["dispatch_plan_summary"]
        assert summary["dispatched"] == 2  # pr-reviewer + security-reviewer
        assert summary["skipped"] == 2  # SKIPPED + SKIPPED_OVERRIDE


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

    def test_step_7_instructs_checking_agent_status(self, mod, tmp_path):
        """Step 7 should instruct checking agent completion before proceeding."""
        state = {"completed_steps": [], "resolved_params": {"git_range": "abc..HEAD"}}
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        g = mod.get_step_guidance(7, "full", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "agents_status" in text or "agent status" in text.lower()

    def test_step_7_surfaces_not_dispatched_agents(self, mod, tmp_path):
        """Step 7 should warn about NOT_DISPATCHED agents so missed dispatches don't silently pass."""
        state = {"completed_steps": [], "resolved_params": {"git_range": "abc..HEAD"}}
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        g = mod.get_step_guidance(7, "full", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "NOT_DISPATCHED" in text

    def test_step_7_discourages_sleep_polling(self, mod, tmp_path):
        """Step 7 should tell the LLM to wait for notifications, not poll in a sleep loop."""
        state = {"completed_steps": [], "resolved_params": {"git_range": "abc..HEAD"}}
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        g = mod.get_step_guidance(7, "full", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "sleep" in text.lower() and "not" in text.lower() or "do not poll" in text.lower()
        assert "notification" in text.lower() or "run_in_background" in text.lower()


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


class TestStep8AdditionalInstructions:
    """Step 8: additional_instructions surfaced as Reviewer-Requested Focus."""

    def _make_state_ready(self):
        return {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": ["pr-reviewer", "security-reviewer"],
                "completed": ["pr-reviewer", "security-reviewer"],
                "failed": [],
            },
        }

    def test_additional_instructions_in_actions(self, mod, tmp_path):
        """When config has additional_instructions, actions contain Reviewer-Requested Focus."""
        state = self._make_state_ready()
        config = {"additional_instructions": "Pay special attention to error handling in the webhook path"}
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, config=config, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "Reviewer-Requested Focus" in text
        assert "Pay special attention to error handling in the webhook path" in text

    def test_no_additional_instructions_no_section(self, mod, tmp_path):
        """When config does NOT have additional_instructions, no Reviewer-Requested Focus section."""
        state = self._make_state_ready()
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "Reviewer-Requested Focus" not in text


class TestStep8ReadinessGate:
    """Step 8 readiness gate: blocks reconciliation when agents are still running."""

    def test_blocked_when_agents_running(self, mod, tmp_path):
        """Step 8 should return a blocked briefing when waiting_on_agents has running agents."""
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "waiting_on_agents": {
                "running": ["security-reviewer", "performance-reviewer"],
                "not_dispatched": [],
            },
            "agents": {
                "dispatched": ["pr-reviewer", "security-reviewer", "performance-reviewer"],
                "completed": ["pr-reviewer"],
                "failed": [],
            },
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        assert "WAITING" in g["title"]
        text = "\n".join(g["situation"])
        assert "security-reviewer" in text
        assert "performance-reviewer" in text
        # Should instruct re-running status check
        actions_text = "\n".join(g["actions"])
        assert "agents_status.py" in actions_text

    def test_blocked_shows_not_dispatched(self, mod, tmp_path):
        """Blocked briefing should mention NOT_DISPATCHED agents too."""
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "waiting_on_agents": {
                "running": ["security-reviewer"],
                "not_dispatched": ["dead-code-reviewer"],
            },
            "agents": {"dispatched": [], "completed": [], "failed": []},
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["situation"])
        assert "dead-code-reviewer" in text

    def test_not_blocked_when_no_running_agents(self, mod, tmp_path):
        """Step 8 should proceed normally when waiting_on_agents is absent or empty."""
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": ["pr-reviewer"],
                "completed": ["pr-reviewer"],
                "failed": [],
            },
            "change_purpose": "Test change.",
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        assert "WAITING" not in g["title"]
        text = "\n".join(g["actions"])
        assert "review-reconciliator" in text

    def test_blocked_records_first_waiting_at(self, mod, tmp_path):
        """First WAITING call should record first_waiting_at timestamp."""
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "waiting_on_agents": {
                "running": ["security-reviewer"],
                "not_dispatched": [],
            },
            "agents": {"dispatched": ["security-reviewer"], "completed": [], "failed": []},
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        assert "WAITING" in g["title"]
        # The step function should have added first_waiting_at to state
        waiting = state.get("waiting_on_agents", {})
        assert "first_waiting_at" in waiting

    def test_escalates_after_timeout(self, mod, tmp_path):
        """Step 8 should escalate to force-proceed after timeout threshold."""
        from datetime import datetime, timezone, timedelta

        # Simulate first_waiting_at was 25 minutes ago (exceeds 20min + 60s)
        past = datetime.now(timezone.utc) - timedelta(minutes=25)
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "waiting_on_agents": {
                "running": ["security-reviewer"],
                "not_dispatched": [],
                "first_waiting_at": past.isoformat(),
                "agent_timeout_seconds": 1200,
            },
            "agents": {
                "dispatched": ["pr-reviewer", "security-reviewer"],
                "completed": ["pr-reviewer"],
                "failed": [],
            },
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        # Should NOT be in waiting state — should have escalated
        assert "WAITING" not in g["title"]
        actions_text = "\n".join(g["actions"])
        # Should instruct TaskStop of stuck agents
        assert "TaskStop" in actions_text
        # Should still proceed to reconciliation
        assert "review-reconciliator" in actions_text
        # Escalation should have cleared waiting_on_agents from state
        assert "waiting_on_agents" not in state
        # Escalation warning should appear in situation
        assert "Escalation" in "\n".join(g["situation"])

    def test_does_not_escalate_before_timeout(self, mod, tmp_path):
        """Step 8 should keep waiting when within timeout threshold."""
        from datetime import datetime, timezone, timedelta

        # Simulate first_waiting_at was 5 minutes ago (well within threshold)
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "waiting_on_agents": {
                "running": ["security-reviewer"],
                "not_dispatched": [],
                "first_waiting_at": past.isoformat(),
                "agent_timeout_seconds": 1200,
            },
            "agents": {"dispatched": ["security-reviewer"], "completed": [], "failed": []},
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        assert "WAITING" in g["title"]

    def test_not_blocked_when_only_not_dispatched(self, mod, tmp_path):
        """NOT_DISPATCHED alone should not block — only RUNNING agents block."""
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "waiting_on_agents": {
                "running": [],
                "not_dispatched": ["dead-code-reviewer"],
            },
            "agents": {
                "dispatched": ["pr-reviewer"],
                "completed": ["pr-reviewer"],
                "failed": [],
            },
            "change_purpose": "Test change.",
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        assert "WAITING" not in g["title"]


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

    def test_reinjects_change_purpose(self, mod, tmp_path):
        """Step 9 should re-inject change purpose to anchor the model."""
        state = {
            "completed_steps": [],
            "change_purpose": "Adds retry logic to the payment gateway with exponential backoff.",
        }
        ctx = {}
        g = mod.get_step_guidance(9, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["situation"] + g["actions"])
        assert "retry logic" in text.lower()

    def test_reinjects_commit_messages_when_no_change_purpose(self, mod, tmp_path):
        """Step 9 should use commit messages as fallback when change-purpose.md is missing."""
        state = {
            "completed_steps": [],
            "commit_messages": ["feat: add payment retry", "test: add retry tests"],
        }
        ctx = {}
        g = mod.get_step_guidance(9, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["situation"] + g["actions"])
        assert "payment retry" in text.lower() or "commit" in text.lower()


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

    def test_revise_instructs_report_edit(self, mod, tmp_path):
        """REVISE verdict instructions must explicitly mention editing review-report.md."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        # Find the REVISE section specifically — lines between REVISE and ESCALATE
        lines = text.split("\n")
        revise_lines = []
        in_revise = False
        for line in lines:
            if "REVISE" in line and "**" in line:
                in_revise = True
            elif "ESCALATE" in line and "**" in line:
                in_revise = False
            elif in_revise:
                revise_lines.append(line)
        revise_text = "\n".join(revise_lines)
        # REVISE section must mention review-report.md with a concrete action verb
        assert "review-report.md" in revise_text, (
            "REVISE instructions must explicitly mention editing review-report.md"
        )
        lower = revise_text.lower()
        assert any(verb in lower for verb in ["edit", "update", "fix", "correct", "reframe"]), (
            "REVISE instructions must use a concrete action verb (edit/update/fix/correct/reframe)"
        )

    def test_stand_instructs_no_changes(self, mod, tmp_path):
        """STAND verdict instructions must say no changes needed."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "no changes" in text.lower() or "no action" in text.lower()

    def test_includes_findings_json_for_critic(self, mod, tmp_path):
        """Step 10 must include review-findings.json path so critic can target verification."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "review-findings.json" in text

    def test_escalate_instructs_override_to_comment(self, mod, tmp_path):
        """ESCALATE verdict instructions must say to override verdict to COMMENT."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        # Find the ESCALATE section — the line containing ESCALATE
        escalate_lines = [l for l in text.split("\n") if "ESCALATE" in l and "**" in l]
        assert escalate_lines, "Must have an ESCALATE verdict line"
        escalate_text = escalate_lines[0]
        # ESCALATE must mention overriding to COMMENT (not just COMMENT in a JSON example)
        assert "COMMENT" in escalate_text, (
            "ESCALATE instructions must mention overriding verdict to COMMENT"
        )

    def test_omits_findings_json_when_reconciliation_failed(self, mod, tmp_path):
        """When reconciliation failed, findings JSON doesn't exist -- don't reference it."""
        state = {"completed_steps": [], "degradation": {"reconciliation_failed": True}}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "review-findings.json" not in text

    def test_includes_findings_json_in_normal_flow(self, mod, tmp_path):
        """In normal flow (no degradation), findings JSON should be referenced."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "review-findings.json" in text

    def test_step_10_dispatch_includes_output_dir(self, mod, tmp_path):
        """Step 10 dispatch prompt should include the output directory path."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "Output directory:" in text


class TestCriticVerdictPersistence:
    """Critic verdict is persisted to file and read back by step 11."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_10_instructs_writing_critic_verdict_file(self, mod, tmp_path):
        """Step 10 should instruct writing decision-critic-verdict.json."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "decision-critic-verdict.json" in text

    def test_step_11_reads_critic_verdict_from_file(self, tmp_path):
        """Step 11 should read decision-critic-verdict.json into state."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        (tmp_path / "review-verdict.json").write_text('{"verdict": "APPROVE"}')
        (tmp_path / "review-report.md").write_text("# Review")
        (tmp_path / "review-findings.json").write_text('{"verdict": "APPROVE", "issues": []}')
        (tmp_path / "decision-critic-verdict.json").write_text('{"verdict": "STAND"}')
        r = self._run("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["critic_verdict"] == "STAND"

    def test_step_11_critic_verdict_unavailable_when_file_missing(self, tmp_path):
        """Step 11 should report critic_verdict as unavailable when file is missing."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        (tmp_path / "review-verdict.json").write_text('{"verdict": "APPROVE"}')
        (tmp_path / "review-report.md").write_text("# Review")
        (tmp_path / "review-findings.json").write_text('{"verdict": "APPROVE", "issues": []}')
        r = self._run("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["critic_verdict"] == "unavailable"

    def test_step_11_maps_skipped_critic_to_unavailable(self, tmp_path):
        """SKIPPED verdict (quick mode) should map to unavailable for downstream consumers."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        (tmp_path / "review-verdict.json").write_text('{"verdict": "APPROVE"}')
        (tmp_path / "review-report.md").write_text("# Review")
        (tmp_path / "review-findings.json").write_text('{"verdict": "approve", "issues": []}')
        (tmp_path / "decision-critic-verdict.json").write_text(
            '{"verdict": "SKIPPED", "reason": "quick mode, reconciliation verdict: approve"}'
        )
        r = self._run("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["critic_verdict"] == "unavailable"

    def test_step_1_clears_stale_critic_verdict(self, tmp_path):
        """Step 1 should clear decision-critic-verdict.json from previous runs."""
        (tmp_path / "decision-critic-verdict.json").write_text('{"verdict": "REVISE"}')
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        assert not (tmp_path / "decision-critic-verdict.json").exists()


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

    def test_step11_reads_verdict_into_state(self, mod, tmp_path):
        """Step 11 orchestration must read review-verdict.json into state['verdict']."""
        import json
        verdict_file = tmp_path / "review-verdict.json"
        verdict_file.write_text(json.dumps({"verdict": "COMMENT"}))

        state = {
            "resolved_params": {},
            "completed_steps": [1, 2, 3, 5, 6, 7, 8, 9, 10],
            "verdict": None,
            "agents": {"dispatched": [], "completed": [], "failed": [], "review_files": []},
        }
        config = {"mode": "pr", "interactive": True}
        context = {}

        mod._orchestrate_step(11, "pr", config, state, context, str(tmp_path))

        assert state["verdict"] == "COMMENT"

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


class TestStep10QuickMode:
    """Step 10 quick mode: skip critic when verdict is low-risk."""

    def test_skip_critic_on_approve_verdict(self, mod, tmp_path):
        state = {"completed_steps": [], "reconciliation_verdict": "approve"}
        config = {"quick": True}
        g = mod.get_step_guidance(10, "pr", state, {}, config=config, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "decision-reviewer" not in text
        assert "SKIPPED" in text
        assert "decision-critic-verdict.json" in text

    def test_skip_critic_on_comment_verdict(self, mod, tmp_path):
        state = {"completed_steps": [], "reconciliation_verdict": "comment"}
        config = {"quick": True}
        g = mod.get_step_guidance(10, "pr", state, {}, config=config, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "decision-reviewer" not in text
        assert "SKIPPED" in text

    def test_run_critic_on_request_changes(self, mod, tmp_path):
        state = {"completed_steps": [], "reconciliation_verdict": "request_changes"}
        config = {"quick": True}
        g = mod.get_step_guidance(10, "pr", state, {}, config=config, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "decision-reviewer" in text

    def test_run_critic_on_block_verdict(self, mod, tmp_path):
        state = {"completed_steps": [], "reconciliation_verdict": "block"}
        config = {"quick": True}
        g = mod.get_step_guidance(10, "pr", state, {}, config=config, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "decision-reviewer" in text

    def test_normal_mode_always_runs_critic(self, mod, tmp_path):
        state = {"completed_steps": [], "reconciliation_verdict": "approve"}
        config = {"quick": False}
        g = mod.get_step_guidance(10, "pr", state, {}, config=config, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "decision-reviewer" in text

    def test_quick_skip_still_requires_verdict_files(self, mod, tmp_path):
        state = {"completed_steps": [], "reconciliation_verdict": "approve"}
        config = {"quick": True}
        g = mod.get_step_guidance(10, "pr", state, {}, config=config, output_dir=str(tmp_path))
        handoff_text = "\n".join(g["handoff"]) if g["handoff"] else ""
        assert "decision-critic-verdict.json" in handoff_text
        assert "review-verdict.json" in handoff_text

    def test_skip_critic_case_insensitive(self, mod, tmp_path):
        """Verdict casing should not affect critic skip (step 11 uppercases verdicts)."""
        for verdict in ("approve", "APPROVE", "Approve", "comment", "COMMENT"):
            state = {"completed_steps": [], "reconciliation_verdict": verdict}
            config = {"quick": True}
            g = mod.get_step_guidance(10, "pr", state, {}, config=config, output_dir=str(tmp_path))
            text = "\n".join(g["actions"])
            assert "decision-reviewer" not in text, (
                f"Critic should be skipped for verdict '{verdict}'"
            )

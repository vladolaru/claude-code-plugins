"""Tests for review/briefings.py through the pipeline.py compatibility facade."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/

sys.path.insert(0, str(TESTS_DIR))
from helpers.context_fixtures import COMPLETE_CONTEXT
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
            "agents": {"dispatched": ["code-reviewer"], "completed": ["code-reviewer"], "failed": []},
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

    def test_change_purpose_handoff_requires_attribution(self, mod, tmp_path):
        """Both change-purpose handoffs must instruct attributing intent to its
        source so downstream stages treat the summary as claims to verify."""
        state3 = {"resolved_params": {"has_unfetched_issues": False}, "completed_steps": [1]}
        ctx3 = {"git": {"merge_base": "abc", "git_range": "abc..HEAD",
                        "changed_files": ["a.py"], "commit_count": 3},
                "pr_size": {"files": 1, "lines": 20, "category": "tiny"}}
        state4 = {"resolved_params": {"has_unfetched_issues": True}, "completed_steps": [1, 2, 3]}
        for step, state, ctx in ((3, state3, ctx3), (4, state4, COMPLETE_CONTEXT)):
            g = mod.get_step_guidance(step, "pr", state, ctx)
            text = "\n".join(g["handoff"])
            assert "Attribute intent to its source" in text, step
            assert "verify" in text.lower(), step


class TestStep5DispatchPlan:
    """Step 5: Dispatch Plan + Triage. main() runs planner, passes output to get_step_guidance()."""

    def _make_state_with_plan(self):
        """State with planner output pre-computed by main()."""
        return {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3],
            "dispatch_plan_summary": {"dispatched": 7, "skipped": 3, "conditional": 2},
            "dispatch_plan_agents": [
                {"name": "code-reviewer", "focus": "PR overall goal alignment, cross-domain bugs and regressions, overall code quality", "status": "DISPATCH", "reason": "always dispatch (domain has files)"},
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
        assert "code-reviewer" in text
        assert "security-reviewer" in text
        assert "Dispatching" in text
        assert "Skipped" in text
        # Raw JSON should NOT be inlined
        full_text = "\n".join(g["actions"] + g["situation"])
        assert not ("python3" in full_text and "plan_dispatch.py" in full_text)

    def test_shows_focus_for_agents(self, mod, tmp_path):
        """Step 5 gives the main orchestrator agent focus for adjustments."""
        state = self._make_state_with_plan()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(5, "pr", state, ctx)
        text = "\n".join(g["situation"])
        # Focus descriptions should be visible for both dispatched and skipped agents
        assert "goal alignment" in text.lower()  # code-reviewer's focus
        assert "XSS" in text  # security-reviewer's focus
        assert "SOLID" in text  # architecture-reviewer's focus

    def test_triage_authority(self, mod, tmp_path):
        """The deterministic planner is the baseline for orchestrator adjustment."""
        state = self._make_state_with_plan()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(5, "full", state, ctx)
        text = "\n".join(g["actions"])
        assert "main orchestrator adjustment" in text.lower()
        assert "adjust" in text.lower()
        assert "preliminary" not in text.lower()

    def test_main_orchestrator_adjustment_contract(self, mod, tmp_path):
        """Step 5 names the actor without changing its routing policy."""
        state = self._make_state_with_plan()
        g = mod.get_step_guidance(5, "pr", state, {})
        text = "\n".join(g["actions"])
        lowered = text.lower()

        assert "main orchestrator" in lowered
        assert "planner handles keyword/file-type signals" in lowered
        assert "semantically" in lowered
        assert "clearly irrelevant" in lowered
        assert (
            "only force-dispatch a skipped agent when you're confident it will find "
            "something the plan missed."
        ) in lowered
        assert "useful review coverage" not in lowered
        assert "human override" not in lowered
        assert "DISPATCH_OVERRIDE" in text
        assert "SKIPPED_OVERRIDE" in text
        assert "override_reason" in text

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
                {"name": "code-reviewer", "focus": "PR overall goal alignment", "status": "DISPATCH", "reason": "always dispatch (domain has files)"},
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
        assert "code-reviewer" in text
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
                {"name": "code-reviewer", "focus": "PR goal alignment", "status": "DISPATCH", "reason": "always dispatch (domain has files)"},
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
                {"name": "code-reviewer", "domain": "code"},
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
        assert "code-reviewer" in text
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

    def test_codex_dispatch_uses_spawn_agent_and_canonical_reviewer(self, mod, tmp_path):
        """Codex dispatch reads the canonical reviewer instead of copying it."""
        state = self._make_state_with_agents()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        config = {"host": "codex"}
        g = mod.get_step_guidance(
            6, "full", state, ctx, config=config, output_dir=str(tmp_path)
        )
        text = "\n".join(g["actions"])

        assert "spawn_agent" in text
        assert "agents/code-reviewer.md" in text
        assert "agents/security-reviewer.md" in text
        assert "task name `code_reviewer`" in text
        assert "task name `security_reviewer`" in text
        assert "Claude Code packaging metadata" in text
        assert "Agent tool" not in text

    def test_codex_task_names_follow_host_schema(self, mod):
        assert mod._codex_task_name("security-reviewer") == "security_reviewer"
        assert mod._codex_task_name("Repo Reviewer/v2") == "repo_reviewer_v2"
        assert mod._codex_task_name("42-check") == "reviewer_42_check"

        long_name = f"repo-{'a' * 70}-renewals-reviewer"
        for reviewer_name in (
            "security-reviewer",
            "repo-a--b-reviewer",
            long_name,
            "42-check",
        ):
            task_name = mod._codex_task_name(reviewer_name)
            assert re.fullmatch(r"[a-z][a-z0-9_]*", task_name)
            assert len(task_name) <= 64

    def test_codex_task_names_preserve_repeated_separators(self, mod):
        single_separator = mod._codex_task_name("repo-a-b-reviewer")
        repeated_separator = mod._codex_task_name("repo-a--b-reviewer")

        assert single_separator == "repo_a_b_reviewer"
        assert repeated_separator == "repo_a__b_reviewer"
        assert single_separator != repeated_separator

    def test_codex_task_names_distinguish_long_shared_prefixes(self, mod):
        shared_prefix = f"repo-{'a' * 70}"
        first_name = f"{shared_prefix}-renewals-reviewer"
        second_name = f"{shared_prefix}-billing-reviewer"

        assert mod._codex_task_name(first_name) != mod._codex_task_name(second_name)

    def test_claude_remains_default_dispatch_host(self, mod, tmp_path):
        state = self._make_state_with_agents()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(6, "full", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])

        assert "Agent tool" in text
        assert "spawn_agent" not in text

    def test_references_status_check(self, mod, tmp_path):
        """Should reference agents_status.py for monitoring."""
        state = self._make_state_with_agents()
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(6, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "agents_status.py" in text

    def test_repo_reviewer_adapter_command(self, mod, tmp_path):
        """Adapter instances emit the ref-mode bootstrap command + subagent_type hint."""
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3, 5],
            "dispatched_agents": [
                {"name": "security-reviewer", "domain": "security"},
                {
                    "name": "repo-renewals-reviewer",
                    "adapter": "repo-reviewer-adapter",
                    "ref": ".ai/agents/review/renewals.md",
                    "label": "Renewals Expert",
                    "channel": "blocking",
                    "execution": "inline",
                    "model": "sonnet",
                    "scope_domains": ["wp-architecture", "architecture"],
                },
            ],
        }
        import shlex
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(6, "full", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        # Native agent: plain --agent command.
        assert "--agent security-reviewer" in text
        assert "subagent_type `repo-reviewer-adapter`" in text
        assert "model `sonnet`" in text
        # Adapter instance: parse the shell-quoted command and check its args.
        cmd_line = next(
            line for line in g["actions"]
            if "bootstrap.py" in line and "--repo-agent-ref" in line
        )
        tok = shlex.split(cmd_line)
        assert tok[tok.index("--agent") + 1] == "repo-reviewer-adapter"
        assert tok[tok.index("--instance-name") + 1] == "repo-renewals-reviewer"
        assert tok[tok.index("--repo-agent-ref") + 1] == ".ai/agents/review/renewals.md"
        assert tok[tok.index("--scope-domains") + 1] == "wp-architecture,architecture"
        # The dispatched tier reaches bootstrap so lifecycle telemetry
        # records it — not the adapter registry's static tier.
        assert tok[tok.index("--model-tier") + 1] == "sonnet"

    def test_codex_repo_reviewers_get_instance_task_names(self, mod, tmp_path):
        """Each repo reviewer instance is its own Codex task. Task names
        derive from the instance name — two reviewers sharing the
        repo-reviewer-adapter definition must not collide on one task
        path — while the adapter definition file is still what the task
        is told to read. Step 8 already targets instance names."""
        import shlex
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3, 5],
            "dispatched_agents": [
                {
                    "name": "repo-renewals-reviewer",
                    "adapter": "repo-reviewer-adapter",
                    "ref": ".ai/agents/review/renewals.md",
                    "label": "Renewals Expert",
                    "channel": "blocking",
                    "execution": "inline",
                    "scope_domains": ["architecture"],
                },
                {
                    "name": "repo-billing-reviewer",
                    "adapter": "repo-reviewer-adapter",
                    "ref": ".ai/agents/review/billing.md",
                    "label": "Billing Expert",
                    "channel": "blocking",
                    "execution": "inline",
                    "scope_domains": ["architecture"],
                },
            ],
        }
        ctx = {"git": {"git_range": "abc..HEAD"}}
        config = {"host": "codex"}
        g = mod.get_step_guidance(
            6, "full", state, ctx, config=config, output_dir=str(tmp_path)
        )
        text = "\n".join(g["actions"])
        step_6_task_names = {
            match.group(1)
            for line in g["actions"]
            if (match := re.search(r"task name `([^`]+)`", line))
        }
        assert step_6_task_names == {
            "repo_renewals_reviewer",
            "repo_billing_reviewer",
        }
        assert "task name `repo_reviewer_adapter`" not in text
        adapter_definition = mod.AGENTS_DIR / "repo-reviewer-adapter.md"
        adapter_instruction = (
            f"In the message, first read `{adapter_definition}` completely. "
            "Treat its YAML frontmatter as Claude Code packaging metadata, "
            "do not translate its model or tool labels, and follow the "
            "Markdown reviewer instructions."
        )
        assert text.count(adapter_instruction) == 2
        cmd_lines = [
            line for line in g["actions"]
            if "bootstrap.py" in line and "--repo-agent-ref" in line
        ]
        instance_names = {
            shlex.split(line)[shlex.split(line).index("--instance-name") + 1]
            for line in cmd_lines
        }
        assert instance_names == {"repo-renewals-reviewer", "repo-billing-reviewer"}

        step_8_state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": [
                    "repo-renewals-reviewer",
                    "repo-billing-reviewer",
                ],
                "completed": [
                    "repo-renewals-reviewer",
                    "repo-billing-reviewer",
                ],
                "failed": [],
            },
        }
        step_8 = mod.get_step_guidance(
            8, "full", step_8_state, ctx, config=config, output_dir=str(tmp_path)
        )
        target_heading = step_8["actions"].index("Codex task targets:")
        step_8_task_names = set(
            re.findall(r"`([^`]+)`", step_8["actions"][target_heading + 1])
        )
        assert step_8_task_names == step_6_task_names

    def test_codex_adapter_command_omits_the_claude_model_tier(self, mod, tmp_path):
        """The Codex host dispatches the native subagent with no Claude
        model override, so forwarding the declared tier would make
        telemetry attribute the execution to a model that never ran. Empty
        falls back to the adapter registry's honest 'inherit'."""
        import shlex
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3, 5],
            "dispatched_agents": [
                {
                    "name": "repo-renewals-reviewer",
                    "adapter": "repo-reviewer-adapter",
                    "ref": ".ai/agents/review/renewals.md",
                    "label": "Renewals Expert",
                    "channel": "blocking",
                    "execution": "inline",
                    "model": "sonnet",
                    "scope_domains": ["architecture"],
                },
            ],
        }
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(
            6, "full", state, ctx, config={"host": "codex"},
            output_dir=str(tmp_path),
        )
        cmd_line = next(
            line for line in g["actions"]
            if "bootstrap.py" in line and "--repo-agent-ref" in line
        )
        tok = shlex.split(cmd_line)
        assert tok[tok.index("--model-tier") + 1] == ""

    def test_adapter_command_escapes_repo_controlled_strings(self, mod, tmp_path):
        """A malicious repo-supplied label/ref cannot inject shell commands."""
        import shlex
        evil_label = '"; rm -rf ~; echo pwned "'
        evil_ref = '.ai/agents/x";$(id)".md'
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3, 5],
            "dispatched_agents": [{
                "name": "repo-evil-reviewer",
                "adapter": "repo-reviewer-adapter",
                "ref": evil_ref,
                "label": evil_label,
                "channel": "blocking",
                "execution": "inline",
                "model": None,
                "scope_domains": ["code"],
            }],
        }
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(6, "full", state, ctx, output_dir=str(tmp_path))
        # Find the generated bootstrap command line and parse it as a shell would.
        cmd_line = next(
            line for line in g["actions"]
            if "bootstrap.py" in line and "--repo-agent-ref" in line
        )
        tokens = shlex.split(cmd_line)
        # The malicious strings survive as SINGLE arguments — no breakout.
        assert tokens[tokens.index("--adapter-label") + 1] == evil_label
        assert tokens[tokens.index("--repo-agent-ref") + 1] == evil_ref
        # No unescaped injection metacharacters leak as separate tokens.
        assert "rm" not in tokens
        assert "$(id)" not in cmd_line or shlex.quote("$(id)") not in cmd_line

    def test_adapter_command_none_fields_use_defaults(self, mod, tmp_path):
        """Explicit None on optional fields falls back, not the literal 'None'."""
        import shlex
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3, 5],
            "dispatched_agents": [{
                "name": "repo-x-reviewer", "adapter": "repo-reviewer-adapter",
                "ref": ".ai/r.md", "label": None, "channel": None,
                "execution": None, "model": None, "scope_domains": ["code"],
            }],
        }
        ctx = {"git": {"git_range": "abc..HEAD"}}
        g = mod.get_step_guidance(6, "full", state, ctx, output_dir=str(tmp_path))
        cmd_line = next(l for l in g["actions"] if "bootstrap.py" in l and "--channel" in l)
        tokens = shlex.split(cmd_line)
        assert tokens[tokens.index("--channel") + 1] == "blocking"
        assert tokens[tokens.index("--execution") + 1] == "inline"
        assert tokens[tokens.index("--adapter-label") + 1] == "repo-x-reviewer"
        assert tokens[tokens.index("--model-tier") + 1] == ""
        assert "None" not in tokens

    def test_step6_recomputes_dispatch_plan_summary(self, mod, tmp_path):
        """Step 6 orchestration must recompute summary from final dispatch-plan.json (post-override)."""
        import json

        # Write a dispatch plan with overrides applied
        plan = {
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH", "reason": "always"},
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
        assert summary["dispatched"] == 2  # code-reviewer + security-reviewer
        assert summary["skipped"] == 2  # SKIPPED + SKIPPED_OVERRIDE

    @pytest.mark.parametrize("step", [5, 6])
    def test_dispatch_summaries_use_the_canonical_dispatched_set(
        self, mod, orchestration_mod, tmp_path, monkeypatch, step
    ):
        plan = {
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH", "reason": "always"},
                {
                    "name": "a11y-reviewer",
                    "status": "DISPATCH_OVERRIDE",
                    "reason": "no files",
                    "override_reason": "requested focus",
                },
                {"name": "docs-reviewer", "status": "SKIPPED", "reason": "no files"},
                {
                    "name": "perf-reviewer",
                    "status": "SKIPPED_OVERRIDE",
                    "reason": "conditional",
                    "override_reason": "irrelevant",
                },
            ]
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess", lambda *args, **kwargs: ("", True)
        )
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 2, 3],
        }
        config = {"mode": "pr", "interactive": True}
        context = {"git": {"git_range": "abc..HEAD"}}

        mod._orchestrate_step(step, "pr", config, state, context, str(tmp_path))

        assert state["dispatch_plan_summary"]["dispatched"] == 2
        assert state["dispatch_plan_summary"]["skipped"] == 2
        if step == 6:
            assert [agent["name"] for agent in state["dispatched_agents"]] == [
                "code-reviewer",
                "a11y-reviewer",
            ]


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
                "dispatched": ["code-reviewer", "security-reviewer", "performance-reviewer"],
                "completed": ["code-reviewer", "security-reviewer"],
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

    def test_codex_reconciliator_uses_canonical_agent_definition(self, mod, tmp_path):
        state = self._make_state_with_agents(change_purpose_exists=True)
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        config = {"host": "codex"}
        g = mod.get_step_guidance(
            8, "pr", state, ctx, config=config, output_dir=str(tmp_path)
        )
        text = "\n".join(g["actions"])

        assert "spawn_agent" in text
        assert "agents/review-reconciliator.md" in text
        assert "task name `review_reconciliator`" in text
        assert "interrupt_agent" in text
        assert "`code_reviewer`" in text
        assert "TaskStop" not in text

    def test_presents_agent_completion_summary(self, mod, tmp_path):
        """Should show which agents completed, missing, failed."""
        state = self._make_state_with_agents(change_purpose_exists=True)
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["situation"])
        assert "code-reviewer" in text
        assert "security-reviewer" in text

    def test_includes_reconciliation_context_path(self, mod, tmp_path):
        """All modes should pass reconciliation-context.md to reconciliator."""
        for mode in ("pr", "full", "incremental"):
            state = self._make_state_with_agents(change_purpose_exists=True)
            ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
            g = mod.get_step_guidance(8, mode, state, ctx, output_dir=str(tmp_path))
            text = "\n".join(g["actions"])
            assert "reconciliation-context.md" in text

    def test_includes_change_purpose_when_available(self, mod, tmp_path):
        """Should include change purpose in reconciliator prompt."""
        state = self._make_state_with_agents(change_purpose_exists=True)
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["situation"] + g["actions"])
        assert "retry logic" in text.lower() or "change purpose" in text.lower()

    def test_change_purpose_framed_as_claims_to_verify(self, mod, tmp_path):
        """The reconciliator dispatch must present change purpose as author-stated
        claims to verify, not context to adopt (regression guard for #66488)."""
        state = self._make_state_with_agents(change_purpose_exists=True)
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["situation"] + g["actions"])
        assert "author-stated" in text.lower()
        assert "claims to verify" in text

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

    def test_reconciliator_prompt_references_context_file(self, mod, tmp_path):
        """Step 8 should reference pre-gathered reconciliation-context.md instead of individual review files."""
        state = self._make_state_with_agents(change_purpose_exists=True)
        state["agents"]["review_files"] = [
            "/tmp/out/code-review.json",
            "/tmp/out/security-review.json",
        ]
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "reconciliation-context.md" in text
        # Individual review files are no longer listed — they're inside the context file
        assert "code-review.json" not in text


class TestStep8AdditionalInstructions:
    """Step 8: additional_instructions surfaced as Reviewer-Requested Focus."""

    def _make_state_ready(self):
        return {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": ["code-reviewer", "security-reviewer"],
                "completed": ["code-reviewer", "security-reviewer"],
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
                "dispatched": ["code-reviewer", "security-reviewer", "performance-reviewer"],
                "completed": ["code-reviewer"],
                "failed": [],
            },
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        assert "WAITING" in g["title"]
        assert g["blocks_progress"] is True
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
                "dispatched": ["code-reviewer"],
                "completed": ["code-reviewer"],
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
                "dispatched": ["code-reviewer", "security-reviewer"],
                "completed": ["code-reviewer"],
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
                "dispatched": ["code-reviewer"],
                "completed": ["code-reviewer"],
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

    def test_surfaces_inline_coverage_gaps(self, mod, tmp_path):
        """Files no reviewer saw inline must be forced into the report."""
        state = {
            "completed_steps": [],
            "inline_coverage_gaps": {
                "src/starved.php": ["code-reviewer", "security-reviewer"],
            },
        }
        g = mod.get_step_guidance(9, "full", state, {})
        text = "\n".join(g["actions"])
        assert "Review coverage" in text
        assert "src/starved.php" in text
        assert "code-reviewer, security-reviewer" in text

    def test_surfaces_deferred_claims_as_not_proof(self, mod, tmp_path):
        """Deferred-review claims stay visible without becoming proof of review."""
        state = {
            "completed_steps": [],
            "inline_coverage_gaps": {},
            "inline_coverage_claims": {
                "src/big_module.py": ["security-reviewer"],
            },
        }
        g = mod.get_step_guidance(9, "full", state, {})
        text = "\n".join(g["actions"])
        assert "claims" in text.lower()
        assert "src/big_module.py" in text
        assert "security-reviewer" in text
        assert "not proof" in text.lower()
        assert "`src/big_module.py`" in text
        assert "`security-reviewer`" in text

    def test_deferred_claims_render_untrusted_values_as_safe_code_spans(
        self, mod, tmp_path
    ):
        path = "src/evil``name.py\r\n## injected heading\r\n- injected file`"
        claimant = (
            "`security`reviewer\r\n# injected claimant\r\n* injected agent`"
        )
        state = {
            "completed_steps": [],
            "inline_coverage_gaps": {},
            "inline_coverage_claims": {path: [claimant]},
        }

        g = mod.get_step_guidance(9, "full", state, {})
        text = "\n".join(g["actions"])

        assert "\n## injected heading" not in text
        assert "\n- injected file" not in text
        assert "\n# injected claimant" not in text
        assert "\n* injected agent" not in text
        assert (
            "``` src/evil``name.py ## injected heading - injected file` ```"
            in text
        )
        assert (
            "`` `security`reviewer # injected claimant * injected agent` ``"
            in text
        )

    def test_malformed_deferred_claims_are_ignored(self, mod, tmp_path):
        state = {
            "completed_steps": [],
            "inline_coverage_gaps": {},
            "inline_coverage_claims": ["unexpected-list"],
        }
        g = mod.get_step_guidance(9, "full", state, {})
        assert "Review coverage claims" not in "\n".join(g["actions"])

    @pytest.mark.parametrize(
        "reconciliation_payload",
        [
            None,
            {"inline_coverage": ["malformed"]},
        ],
        ids=["missing-context", "malformed-coverage"],
    )
    def test_step9_state_loading_clears_stale_deferred_claims(
        self, mod, tmp_path, reconciliation_payload
    ):
        if reconciliation_payload is not None:
            (tmp_path / "reconciliation-context.json").write_text(
                json.dumps(reconciliation_payload)
            )
        state = {
            "inline_coverage_gaps": {"src/stale.py": ["code-reviewer"]},
            "inline_coverage_claims": {
                "src/stale.py": ["security-reviewer"],
            },
        }

        mod._orchestrate_step(9, "full", {}, state, {}, str(tmp_path))

        assert state["inline_coverage_claims"] == {}

    def test_step9_state_loading_wires_deferred_claims_to_warning(
        self, mod, tmp_path
    ):
        claims = {"src/big_module.py": ["security-reviewer"]}
        (tmp_path / "reconciliation-context.json").write_text(
            json.dumps(
                {
                    "inline_coverage": {
                        "files_never_inline": {},
                        "files_deferred_reviewed": claims,
                    },
                }
            )
        )
        state = {}

        mod._orchestrate_step(9, "full", {}, state, {}, str(tmp_path))

        assert state["inline_coverage_claims"] == claims
        g = mod.get_step_guidance(9, "full", state, {})
        text = "\n".join(g["actions"])
        assert "Review coverage claims" in text
        assert "src/big_module.py" in text
        assert "security-reviewer" in text

    def test_no_coverage_warning_without_gaps(self, mod, tmp_path):
        state = {"completed_steps": [], "inline_coverage_gaps": {}}
        g = mod.get_step_guidance(9, "full", state, {})
        assert "Review coverage" not in "\n".join(g["actions"])

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

    def test_change_purpose_subordinated_to_findings(self, mod, tmp_path):
        """Step 9 must present change purpose as author framing subordinate to
        the reconciled findings, for both the summary and commit fallbacks."""
        for state in (
            {"completed_steps": [], "change_purpose": "Fix retry logic."},
            {"completed_steps": [], "commit_messages": ["fix: retry logic"]},
        ):
            g = mod.get_step_guidance(9, "pr", state, {})
            text = "\n".join(g["situation"])
            assert "source of truth" in text

    def test_default_instructions_forbid_prose_demotion(self, mod, tmp_path):
        """Both default instruction sets must forbid demoting findings into
        tradeoff prose and asserting unverified likelihood claims as fact."""
        for mode in ("pr", "full"):
            state = {"completed_steps": []}
            ctx = {}
            g = mod.get_step_guidance(9, mode, state, ctx)
            text = "\n".join(g["actions"])
            assert "do not demote" in text.lower(), mode
            assert "narrow corner" in text, mode
            assert "verdict" in text.lower(), mode

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
    @staticmethod
    def _revise_section(guidance):
        """The REVISE block: from the `**REVISE**` line up to `**ESCALATE**`."""
        lines = "\n".join(guidance["actions"]).split("\n")
        collected = []
        in_revise = False
        for line in lines:
            if "REVISE" in line and "**" in line:
                in_revise = True
                collected.append(line)
            elif "ESCALATE" in line and "**" in line:
                in_revise = False
            elif in_revise:
                collected.append(line)
        assert collected, "no REVISE block found in the step-10 briefing"
        return "\n".join(collected)

    def test_dispatches_decision_reviewer(self, mod, tmp_path):
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "decision-reviewer" in text

    def test_codex_critic_uses_canonical_agent_definition(self, mod, tmp_path):
        state = {"completed_steps": []}
        config = {"host": "codex"}
        g = mod.get_step_guidance(
            10, "pr", state, {}, config=config, output_dir=str(tmp_path)
        )
        text = "\n".join(g["actions"])

        assert "spawn_agent" in text
        assert "agents/decision-reviewer.md" in text
        assert "task name `decision_reviewer`" in text


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
        # Find all REVISE-related text — the line containing REVISE plus any
        # continuation lines before ESCALATE (handles both multi-line and
        # single-line formats)
        lines = text.split("\n")
        revise_lines = []
        in_revise = False
        for line in lines:
            if "REVISE" in line and "**" in line:
                in_revise = True
                revise_lines.append(line)  # include the REVISE line itself
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

    def test_revise_routes_through_the_adjustments_ledger(self, mod, tmp_path):
        """REVISE must apply the critic's adjustments, not only edit prose.

        The load-bearing half of the flow is the `critic_adjustments.py`
        invocation: without it the critic's finding-level decisions reach
        the human report and never the machine-readable ledger that bot
        mode, baselines, and metrics consume.
        """
        state = {"completed_steps": []}
        g = mod.get_step_guidance(10, "pr", state, {}, output_dir=str(tmp_path))
        revise_text = self._revise_section(g)

        assert "decision-critic-adjustments.json" in revise_text, (
            "REVISE must tell the orchestrator to read the adjustments file"
        )
        assert "critic_adjustments.py" in revise_text, (
            "REVISE must invoke the module that carries adjustments into "
            "review-findings.json"
        )
        assert "--output-dir" in revise_text, (
            "the apply command must be runnable as written"
        )
        assert "rejected" in revise_text, (
            "a refuted adjustment must be marked rejected, not deleted"
        )

    def test_revise_updates_the_ledger_before_the_report(self, mod, tmp_path):
        """Ordering is the contract: JSON first, then prose that matches it."""
        state = {"completed_steps": []}
        g = mod.get_step_guidance(10, "pr", state, {}, output_dir=str(tmp_path))
        revise_text = self._revise_section(g)

        read_adjustments = revise_text.index("decision-critic-adjustments.json")
        apply_adjustments = revise_text.index("critic_adjustments.py")
        edit_report = revise_text.index("review-report.md")

        assert read_adjustments < apply_adjustments < edit_report, (
            "REVISE must read the adjustments, apply them to the findings "
            "JSON, and only then edit the report to match — a report edited "
            "first would describe a ledger the critic never reached"
        )

    def test_critic_dispatch_prompt_requires_the_adjustments_file(
        self, mod, tmp_path
    ):
        """The critic itself must be told to write the machine-readable form."""
        state = {"completed_steps": []}
        g = mod.get_step_guidance(10, "pr", state, {}, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        prompt = text.split("Use this dispatch prompt:", 1)[1]
        prompt = prompt.split("Act on the critic's verdict:", 1)[0]

        assert "decision-critic-adjustments.json" in prompt, (
            "the dispatch prompt must ask the critic for the adjustments file"
        )

    def test_stand_instructs_no_changes(self, mod, tmp_path):
        """STAND verdict instructions must convey that no edits are needed."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        lower = text.lower()
        assert any(phrase in lower for phrase in [
            "no changes", "no action", "proceed to writing",
        ]), "STAND must convey no report edits needed"

    def test_builds_critic_context_md_before_dispatch(self, mod, tmp_path):
        """Step 10 must instruct building critic-context.md before dispatching the critic."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "critic-context.md" in text
        # The build step must appear before the dispatch prompt
        build_pos = text.index("critic-context.md")
        assert "decision-reviewer" in text[build_pos:]

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

    def test_degraded_mode_skips_critic_context_and_passes_report(self, mod, tmp_path):
        """When reconciliation failed, skip critic-context.md and pass report directly."""
        state = {"completed_steps": [], "degradation": {"reconciliation_failed": True}}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "critic-context.md" not in text
        assert "review-report.md" in text
        # Must tell the agent there's no structured findings / no --context
        assert "without" in text.lower() or "no structured" in text.lower() or "no --context" in text.lower()

    def test_normal_flow_dispatches_with_critic_context(self, mod, tmp_path):
        """In normal flow, dispatch prompt references critic-context.md (not raw JSON)."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        # Dispatch prompt should use critic-context.md
        assert "critic-context.md" in text
        # Should NOT reference review-findings.json in the dispatch prompt
        # (it's consumed during the build step, not passed to the critic).
        # The slice ends where the prompt ends: the post-dispatch REVISE
        # actions legitimately name the findings JSON the orchestrator
        # patches, and that text never reaches the critic.
        dispatch_start = text.index("dispatch prompt")
        dispatch_section = text[dispatch_start:].split(
            "Act on the critic's verdict:", 1
        )[0]
        assert "review-findings.json" not in dispatch_section

    def test_normal_flow_includes_report_path_in_dispatch(self, mod, tmp_path):
        """Normal flow dispatch must include the report path for critic.py --report."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        # The dispatch prompt section (between ``` markers) must have the report path
        assert "review-report.md" in text
        assert "report" in text.lower()  # label for the report path

    def test_report_synthesis_failed_includes_findings_md_as_report(self, mod, tmp_path):
        """When report synthesis failed, the report path should be review-findings.md."""
        state = {"completed_steps": [], "degradation": {"report_synthesis_failed": True}}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "review-findings.md" in text

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

    @pytest.mark.parametrize("interactive", [True, False])
    @pytest.mark.parametrize(
        ("expected", "status"),
        [(2, "partial"), (0, "complete")],
    )
    def test_zero_reviewer_markdown_includes_regeneration_command(
        self, mod, tmp_path, interactive, expected, status
    ):
        config = {"mode": "pr", "interactive": interactive}
        state = {
            "completed_steps": [],
            "reviewer_markdown": {
                "ran": True,
                "written": 0,
                "expected": expected,
                "status": status,
            },
            "degradation": {"reviewer_markdown_incomplete": True},
        }

        guidance = mod.get_step_guidance(
            11, "pr", state, {}, config=config, output_dir=str(tmp_path)
        )

        lines = [
            line for line in guidance["actions"]
            if "Reviewer Markdown:" in line
        ]
        assert len(lines) == 1
        assert f"0/{expected}" in lines[0]
        assert (
            f"python3 {SCRIPT_PATH.parent}/agent/output.py "
            f"materialize {tmp_path}"
        ) in lines[0]

    @pytest.mark.parametrize("interactive", [True, False])
    def test_regeneration_command_quotes_paths_with_spaces(
        self, mod, tmp_path, interactive
    ):
        output_dir = tmp_path / "review output"
        state = {
            "completed_steps": [],
            "reviewer_markdown": {
                "ran": True,
                "written": 0,
                "expected": 1,
                "status": "partial",
            },
            "degradation": {"reviewer_markdown_incomplete": True},
        }

        guidance = mod.get_step_guidance(
            11,
            "pr",
            state,
            {},
            config={"mode": "pr", "interactive": interactive},
            output_dir=str(output_dir),
        )

        lines = [
            line for line in guidance["actions"]
            if "Reviewer Markdown:" in line
        ]
        assert len(lines) == 1
        assert f"materialize '{output_dir}'" in lines[0]
        assert "\n" not in lines[0]

    @pytest.mark.parametrize("interactive", [True, False])
    def test_complete_reviewer_markdown_reports_positive_count_without_command(
        self, mod, tmp_path, interactive
    ):
        config = {"mode": "pr", "interactive": interactive}
        state = {
            "completed_steps": [],
            "reviewer_markdown": {
                "ran": True,
                "written": 2,
                "expected": 2,
                "status": "complete",
            },
        }

        guidance = mod.get_step_guidance(
            11, "pr", state, {}, config=config, output_dir=str(tmp_path)
        )

        lines = [
            line for line in guidance["actions"]
            if "Reviewer Markdown:" in line
        ]
        assert lines == ["Reviewer Markdown: materialized 2/2 files."]
        assert "agent/output.py materialize" not in lines[0]

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
        lower = text.lower()
        assert any(phrase in lower for phrase in [
            "focused", "drill down", "re-invoke", "reconciliator",
        ]), "Interactive mode should offer follow-up analysis option"

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
                      "critic_verdict", "degradation_notes",
                      "worktree_hygiene", "usage"):
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
        lower = text.lower()
        assert any(word in lower for word in ["failed", "degraded", "degradation"]), (
            "Forced verdict must indicate pipeline degradation"
        )

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


class TestStep3DependencyRefresh:
    """Step 3 briefing renders trusted-branch dependency refresh guidance."""

    _SIGNAL_STATE = {
        "completed_steps": [],
        "dependency_refresh": {
            "signals": [
                {
                    "manager": "composer",
                    "directory": ".",
                    "reasons": ["changed_in_range"],
                    "changed_files": ["composer.lock"],
                    "installed_state_present": True,
                    "suggested_command": (
                        "composer install --no-scripts --no-plugins "
                        "--prefer-dist --no-interaction"
                    ),
                },
            ],
        },
    }

    def _text(self, g):
        parts = list(g["situation"]) + list(g["actions"])
        if g.get("handoff"):
            parts += list(g["handoff"])
        return "\n".join(parts)

    def test_signals_render_refresh_section(self, mod, tmp_path):
        config = {"mode": "full", "interactive": True,
                  "refresh_dependencies": True}
        g = mod.get_step_guidance(3, "full", dict(self._SIGNAL_STATE), {},
                                  config=config, output_dir=str(tmp_path))
        text = self._text(g)
        assert "Dependency refresh" in text
        assert "composer install" in text
        assert "Commands disable lifecycle scripts on purpose; do not strip flags" in text
        assert "yarn install --frozen-lockfile --ignore-scripts" in text
        assert "never chain commands (`&&`, `;`)" in text
        assert "Pipeline independently verifies reported commands and worktree state" in text
        assert "git status --porcelain" in text
        assert "--refresh-host-context" in text
        assert "dependency-refresh.json" in text

    def test_refresh_guidance_only_contains_adaptive_work_in_order(
        self, mod, tmp_path
    ):
        config = {"mode": "full", "interactive": True,
                  "refresh_dependencies": True}
        guidance = mod.get_step_guidance(
            3,
            "full",
            dict(self._SIGNAL_STATE),
            {},
            config=config,
            output_dir=str(tmp_path),
        )
        actions = "\n".join(guidance["actions"])

        ordered_guidance = [
            "1. Run each suggested command",
            "NEVER run update/upgrade/add/require",
            "2. After all install attempts",
            "record them as dependency-refresh failure evidence",
            "tracked worktree was verified clean before installs",
            "restore the refresh-created tracked changes",
            "3. Re-resolve host context",
        ]
        offsets = [actions.index(phrase) for phrase in ordered_guidance]
        assert offsets == sorted(offsets)
        assert "even when an install command fails" in actions
        assert "git restore --source=HEAD --staged --worktree -- <path>" in actions
        assert "git checkout -- <path>" not in actions
        assert "stash" not in actions.lower()
        assert "pre-existing tracked changes remain unstashed" not in actions

    def test_handoff_gates_the_refresh_report(self, mod, tmp_path):
        config = {"mode": "full", "interactive": True,
                  "refresh_dependencies": True}
        g = mod.get_step_guidance(3, "full", dict(self._SIGNAL_STATE), {},
                                  config=config, output_dir=str(tmp_path))
        handoff_text = "\n".join(g["handoff"])
        assert "dependency-refresh.json" in handoff_text
        # change-purpose handoff still present (no unfetched issues)
        assert "change-purpose.md" in handoff_text

    def test_refresh_handoff_survives_unfetched_issues(self, mod, tmp_path):
        state = dict(self._SIGNAL_STATE)
        state["resolved_params"] = {"has_unfetched_issues": True}
        config = {"mode": "full", "interactive": True,
                  "refresh_dependencies": True}
        g = mod.get_step_guidance(3, "full", state, {},
                                  config=config, output_dir=str(tmp_path))
        handoff_text = "\n".join(g["handoff"] or [])
        assert "dependency-refresh.json" in handoff_text
        # change-purpose moves to step 4 when issues are unfetched
        assert "change-purpose.md" not in handoff_text

    def test_enabled_with_no_signals_reports_nothing_to_refresh(self, mod, tmp_path):
        state = {"completed_steps": [],
                 "dependency_refresh": {"signals": []}}
        config = {"mode": "full", "interactive": True,
                  "refresh_dependencies": True}
        g = mod.get_step_guidance(3, "full", state, {},
                                  config=config, output_dir=str(tmp_path))
        text = self._text(g)
        assert "nothing to refresh" in text
        assert "composer install" not in text
        handoff_text = "\n".join(g["handoff"] or [])
        assert "dependency-refresh.json" not in handoff_text

    def test_detection_failure_reports_unknown_staleness(self, mod, tmp_path):
        state = {"completed_steps": [],
                 "dependency_refresh": {"signals": [],
                                        "detection_failed": True}}
        config = {"mode": "full", "interactive": True,
                  "refresh_dependencies": True}
        g = mod.get_step_guidance(3, "full", state, {},
                                  config=config, output_dir=str(tmp_path))
        text = self._text(g)
        assert "detection failed" in text

    def test_dirty_worktree_skips_refresh_with_honest_degradation(
        self, mod, tmp_path
    ):
        state = {
            "dependency_refresh": {
                **self._SIGNAL_STATE["dependency_refresh"],
                "skipped_reason": "dirty_worktree",
                "dirty_files": ["composer.lock"],
            }
        }
        config = {"refresh_dependencies": True}

        situation, actions, handoff = mod._dependency_refresh_briefing(
            state, config, str(tmp_path)
        )

        text = "\n".join(situation)
        assert "refresh skipped" in text.lower()
        assert "pre-existing tracked changes" in text
        assert "degraded host context" in text
        assert "commit or stash" in text
        assert "re-run" in text
        assert actions == []
        assert handoff == []

    def test_failed_worktree_status_skips_refresh_closed(self, mod, tmp_path):
        state = {
            "dependency_refresh": {
                **self._SIGNAL_STATE["dependency_refresh"],
                "skipped_reason": "worktree_status_failed",
                "dirty_files": [],
            }
        }
        config = {"refresh_dependencies": True}

        situation, actions, handoff = mod._dependency_refresh_briefing(
            state, config, str(tmp_path)
        )

        text = "\n".join(situation)
        assert "refresh skipped" in text.lower()
        assert "could not verify that the tracked worktree is clean" in text
        assert "degraded host context" in text
        assert "resolve the Git status failure" in text
        assert "re-run" in text
        assert actions == []
        assert handoff == []

    def test_flag_off_renders_nothing(self, mod, tmp_path):
        config = {"mode": "full", "interactive": True}
        g = mod.get_step_guidance(3, "full", dict(self._SIGNAL_STATE), {},
                                  config=config, output_dir=str(tmp_path))
        text = self._text(g)
        assert "Dependency refresh" not in text
        assert "dependency-refresh.json" not in text

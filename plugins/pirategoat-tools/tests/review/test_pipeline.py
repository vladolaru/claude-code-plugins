"""Tests for review/briefings.py through the pipeline.py compatibility facade."""

import json
import os
import pathlib
import re
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/

sys.path.insert(0, str(TESTS_DIR))
from helpers.context_fixtures import COMPLETE_CONTEXT
from helpers.pipeline_process import init_repo, run_pipeline
from helpers.review_fixtures import (
    canonical_findings_ledger,
    canonical_review_document,
)
from conftest import PIPELINE_SCRIPT_PATH as SCRIPT_PATH


@pytest.fixture(scope="module")
def mod(pipeline_mod):
    """Module-scoped alias — delegates to session-scoped pipeline_mod."""
    return pipeline_mod


def _publish_step_11(output_dir, cwd, mode="pr"):
    """Prepare without a report, then publish the authored report."""
    report = Path(output_dir) / "review-report.md"
    report_text = report.read_text() if report.is_file() else "# Review"
    report.unlink(missing_ok=True)
    prepared = run_pipeline(
        "--step", "11", "--mode", mode,
        "--output-dir", str(output_dir), cwd=cwd,
    )
    assert prepared.returncode == 0, prepared.stderr
    report.write_text(report_text)
    return run_pipeline(
        "--step", "11", "--mode", mode,
        "--output-dir", str(output_dir), cwd=cwd,
    )


def _write_critic_snapshot(output_dir, verdict):
    """Publish one live digest-bound critic snapshot for pipeline tests."""
    from review import critic_adjustments

    critic_adjustments.write_critic_verdict(
        str(output_dir), verdict, critic_adjustments.empty_proposal()
    )


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

    def test_step_11_gates_on_the_report(self, mod, tmp_path):
        """The report's gate followed the report to step 11.

        It was step 9's while step 9 authored it; authoring moved to step
        11, after validation, and the gate has to move with it — this is
        the file pirategoat-bot reads and fails the delivery without.
        """
        g = mod.get_step_guidance(
            11, "pr", {"completed_steps": []}, {}, output_dir=str(tmp_path)
        )
        assert g.get("handoff") is not None
        assert "review-report.md" in "\n".join(g["handoff"])

    def test_step_10_has_handoff(self, mod, tmp_path):
        """Step 10 gates on the critic's own verdict — and on nothing
        verdict-shaped from the orchestrator. A handoff that named a file no
        instruction creates would strand the run on a gate nobody can pass."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        assert g.get("handoff") is not None
        handoff_text = "\n".join(g["handoff"])
        assert "decision-critic-verdict.json" in handoff_text
        assert "review-verdict.json" not in handoff_text

    def test_step_10_never_asks_the_orchestrator_to_write_the_verdict(
        self, mod, tmp_path
    ):
        """`decision-critic-verdict.json` is the CRITIC's artifact, saved
        through `critic.py --save` — the validated, atomic channel
        `agents/decision-reviewer.md` forbids working around. A briefing
        that also told the ORCHESTRATOR to write it made a second,
        unvalidated, non-atomic writer: a mistranscription could overwrite
        the channel-validated verdict that gates the adjustments applier
        and feeds step 11's derivation, a crashed-after-prose critic could
        be papered over, and the rewrite would move the mtime
        `synthesis_lifecycle.observe()` reads as the critic's completion.
        """
        g = mod.get_step_guidance(
            10, "pr", {"completed_steps": []}, {}, output_dir=str(tmp_path)
        )
        text = "\n".join(g["actions"])
        # Negative: no write instruction for that artifact.
        assert "Save to: " + str(tmp_path) + "/decision-critic-verdict.json" \
            not in text
        assert '{"verdict": "<STAND | REVISE | ESCALATE>"}' not in text
        # Positive: the orchestrator reads what the critic already saved.
        assert "decision-critic-verdict.json" in text
        assert "critic.py --save" in text
        assert "You write nothing here" in text

    def test_step_10_never_asks_for_a_stand_in_verdict(self, mod, tmp_path):
        """A SKIPPED stand-in for a crashed critic hides exactly the lost
        stress test step 11 now reports. The briefing must not teach one."""
        g = mod.get_step_guidance(
            10, "pr", {"completed_steps": []}, {}, output_dir=str(tmp_path)
        )
        text = "\n".join(g["actions"])
        assert '"SKIPPED"' not in text
        assert "produced no verdict" in text

    def test_step_10_routes_every_critic_verdict_through_save(
        self, mod, tmp_path
    ):
        g = mod.get_step_guidance(
            10, "pr", {"completed_steps": []}, {}, output_dir=str(tmp_path)
        )
        text = "\n".join(g["actions"])

        assert "$TMPDIR/decision-critic-findings.md" in text
        assert "critic.py --save" in text
        assert "STAND, REVISE, or ESCALATE" in text
        assert (
            f"findings written to {tmp_path}/decision-critic-findings.md"
            not in text
        )
        assert (
            f"Write findings to `{tmp_path}/decision-critic-findings.md`"
            not in text
        )

    def test_step_10_uses_schema_not_placeholders(self, mod, tmp_path):
        """Step 10 JSON examples should show options, not copyable defaults."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        # Should have schema-style options
        assert "STAND" in text and "REVISE" in text and "ESCALATE" in text
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
        # The retired Task-tool spelling must not come back on this host.
        assert "Task tool" not in text

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
                "discarded_drafts": [],
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

    def test_claude_host_wait_uses_notifications_and_watchdog(self, mod, tmp_path):
        """Claude-host wait guidance: end-turn + notification wake-up + named
        anti-patterns + a background watchdog launched right after dispatch."""
        state = {"completed_steps": [], "resolved_params": {"git_range": "abc..HEAD"}}
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        g = mod.get_step_guidance(7, "full", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])

        assert "END YOUR TURN" in text
        assert "notification" in text.lower()
        # Named anti-patterns. The "polling without..." bullet says
        # "wake-up", not "notification" (M3, backlog #25 follow-up): the
        # watchdog's own expiry is a legitimate wake-up but not a
        # notification, so naming the anti-pattern after "notification"
        # alone would misdescribe the one wake-up path this same briefing
        # tells the orchestrator to rely on.
        assert "no foreground" in text.lower() and "sleep" in text.lower()
        assert "keepalive" in text.lower()
        assert "polling without a new wake-up" in text.lower()
        # Watchdog: background wait as a guaranteed wake-up
        assert "--wait" in text
        assert "--max-seconds 1500" in text
        assert "BACKGROUND" in text
        assert "run_in_background: true" in text
        assert "holds no model turn open" in text.lower()

        # Ordering (I1, backlog #25 follow-up): the watchdog must be
        # launched BEFORE the instruction to end the turn — a top-to-bottom
        # executor that ends its turn on reading step 1 would otherwise
        # never reach the watchdog launch and silently lose the guaranteed
        # wake-up.
        watchdog_pos = text.index("--max-seconds 1500")
        end_turn_pos = text.index("END YOUR TURN")
        assert watchdog_pos < end_turn_pos, (
            "the watchdog launch must appear before the END YOUR TURN "
            "instruction, not after"
        )

        # Must not carry the Codex-host cadence
        assert "once a minute" not in text.lower()
        assert "--max-seconds 60" not in text

    def test_codex_host_wait_uses_per_minute_polling(self, mod, tmp_path):
        """Codex-host wait guidance: foreground --wait --max-seconds 60 cadence,
        not the Claude notification/end-turn mechanism."""
        state = {"completed_steps": [], "resolved_params": {"git_range": "abc..HEAD"}}
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        config = {"host": "codex"}
        g = mod.get_step_guidance(7, "full", state, ctx, config=config, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])

        assert "--wait" in text
        assert "--max-seconds 60" in text
        assert "once a minute" in text.lower()
        assert "exit code 3" in text.lower()
        # I2, backlog #25 follow-up: the loop needs a stated termination —
        # anchored to the same 1200s agent timeout the Claude branch names,
        # plus the documented next move once step 8's own escalation is the
        # real backstop.
        assert "1200" in text
        assert "20 min" in text
        assert "escalation gate force-proceeds" in text.lower()
        assert "typical run" in text.lower()
        assert "typical phase" not in text.lower()

        # Must not carry the Claude-host end-turn/notification mechanism
        assert "END YOUR TURN" not in text
        assert "keepalive" not in text.lower()
        assert "--max-seconds 1500" not in text


class TestStep8Reconcile:
    """Step 8: Reconcile + Verify. main() reads dispatch-plan.json + review files, passes to get_step_guidance()."""

    def _make_state_with_agents(self, change_purpose_exists=False):
        return {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": ["code-reviewer", "security-reviewer", "performance-reviewer"],
                "completed": ["code-reviewer", "security-reviewer"],
                "discarded_drafts": [],
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
        """Step 8 points at the pre-gathered JSON context, not at the
        individual review files (they are inside it) and not at a Markdown
        projection written for one agent's eyes only."""
        state = self._make_state_with_agents(change_purpose_exists=True)
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "reconciliation-context.json" in text
        assert "reconciliation-context.md" not in text
        # Individual review files are no longer listed — they're inside the context file
        assert "code-review.json" not in text


class TestStep8FindingsArtifactOwnership:
    """The reconciliator publishes JSON; the pipeline renders the Markdown.

    A handoff gate that asks the orchestrator to verify an artifact the
    reconciliator no longer writes would stall every run — and telling the
    agent to write it is how the narrative went stale after every REVISE.
    """

    def _guidance(self, mod, tmp_path):
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": ["code-reviewer"],
                "completed": ["code-reviewer"],
                "discarded_drafts": [],
            },
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        return mod.get_step_guidance(
            8, "pr", state, ctx, output_dir=str(tmp_path)
        )

    def test_expected_output_names_the_json_as_the_agents_artifact(
        self, mod, tmp_path
    ):
        expected_line = next(
            line for line in self._guidance(mod, tmp_path)["actions"]
            if line.startswith("**Expected output:**")
        )
        assert "review-findings.json" in expected_line
        assert "only artifact" in expected_line
        # The .md may be named here, but only as something the PIPELINE
        # produces — never as an output the agent is asked to write.
        assert "writes no Markdown" in expected_line

    def test_handoff_gates_on_the_json_only(self, mod, tmp_path):
        handoff = "\n".join(self._guidance(mod, tmp_path)["handoff"])
        assert "review-findings.json" in handoff
        assert "review-findings.md" not in handoff

    def test_actions_say_the_pipeline_renders_the_markdown(self, mod, tmp_path):
        text = "\n".join(self._guidance(mod, tmp_path)["actions"])
        assert "review-findings.md" in text
        assert "pipeline renders" in text


class TestStep8AdditionalInstructions:
    """Step 8: additional_instructions surfaced as Reviewer-Requested Focus."""

    def _make_state_ready(self):
        return {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": ["code-reviewer", "security-reviewer"],
                "completed": ["code-reviewer", "security-reviewer"],
                "discarded_drafts": [],
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
                "discarded_drafts": [],
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
            "agents": {"dispatched": [], "completed": [], "discarded_drafts": []},
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
                "discarded_drafts": [],
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
            "agents": {"dispatched": ["security-reviewer"], "completed": [], "discarded_drafts": []},
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
                "discarded_drafts": [],
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
            "agents": {"dispatched": ["security-reviewer"], "completed": [], "discarded_drafts": []},
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
                "discarded_drafts": [],
            },
            "change_purpose": "Test change.",
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        assert "WAITING" not in g["title"]

    def _make_waiting_state(self):
        return {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "waiting_on_agents": {
                "running": ["security-reviewer"],
                "not_dispatched": [],
            },
            "agents": {"dispatched": ["security-reviewer"], "completed": [], "discarded_drafts": []},
        }

    def test_claude_host_waiting_uses_end_turn_and_fresh_watchdog(self, mod, tmp_path):
        """Claude-host WAITING gate (backlog #25 follow-up, I4): mirrors
        step 7's end-turn/notification mechanism, plus a fresh watchdog
        sized to the remaining budget before escalation force-proceeds —
        replacing the mechanism-free "wait, then re-run this step" prose
        that produced the field improvisations in the first place."""
        state = self._make_waiting_state()
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        assert "WAITING" in g["title"]
        assert g["blocks_progress"] is True
        text = "\n".join(g["actions"])

        assert "END YOUR TURN" in text
        assert "--wait" in text
        assert "run_in_background: true" in text
        assert "BACKGROUND" in text
        assert "holds no model turn open" in text.lower()
        # Ordering pin (the I1/D1 defect class): ending the turn is
        # terminal, so the watchdog instruction must come FIRST — a
        # top-to-bottom executor that ends its turn before launching it
        # loses what may be the only remaining wake-up in this state.
        watchdog_pos = text.index("--wait")
        end_turn_pos = text.index("END YOUR TURN")
        assert watchdog_pos < end_turn_pos
        # Escalation text itself is untouched (settled design) — still
        # reachable only via the elapsed>=threshold branch, not asserted
        # here since this state hits the not-yet-escalated branch.

        # Must not carry the Codex-host cadence
        assert "once a minute" not in text.lower()
        assert "--max-seconds 60" not in text

    def test_codex_host_waiting_uses_per_minute_polling(self, mod, tmp_path):
        """Codex-host WAITING gate (backlog #25 follow-up, I4): mirrors
        step 7's per-minute cadence, not the Claude end-turn mechanism."""
        state = self._make_waiting_state()
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"}}
        config = {"host": "codex"}
        g = mod.get_step_guidance(8, "pr", state, ctx, config=config, output_dir=str(tmp_path))
        assert "WAITING" in g["title"]
        assert g["blocks_progress"] is True
        text = "\n".join(g["actions"])

        assert "--wait" in text
        assert "--max-seconds 60" in text
        assert "once a minute" in text.lower()
        assert "exit code 3" in text.lower()

        # Must not carry the Claude-host end-turn/notification mechanism
        assert "END YOUR TURN" not in text
        assert "run_in_background" not in text


class TestReviewCoverageSection:
    """The coverage measurement's renderer, tested directly.

    It used to be reachable only through the step-9 briefing, which pasted
    it into a fenced block for the orchestrator to copy into the report.
    The record assembler is its primary caller now (step 11's briefing
    tells the orchestrator to quote it out of the record), so the pins live
    on the pure function — the one place both callers share.
    """

    @staticmethod
    def _render(mod, gaps=None, claims=None, unscoped=None, inline=None):
        # Straight at briefings.py: the renderer is shared by the record
        # assembler and step 11, so the facade is not the seam under test.
        from review.briefings import _render_file_review_section

        return _render_file_review_section({
            "agents_receiving_inline_diff_by_file": inline,
            "agents_with_unclaimed_review_by_file": gaps,
            "agents_claiming_review_by_file": claims,
            "unscoped_files": unscoped,
        })

    def test_all_three_populations_get_their_own_honest_sentence(self, mod):
        """The field failure this pins: a briefing that DESCRIBED a hedged
        measurement instead of rendering it, and the orchestrator restated
        "skipped by every matching agent's diff budget and no reviewer
        reported reviewing them" as "read by nobody" — false for files
        that were provably read."""
        text = self._render(
            mod,
            gaps={"src/starved.php": ["code-reviewer"]},
            claims={"src/big.py": ["security-reviewer"]},
            unscoped=["package-lock.json", ".editorconfig"],
        )

        assert text.count("## Review coverage") == 1

        assert (
            "1 changed file(s) were skipped by every matching agent's diff "
            "budget and no reviewer reported reviewing them from the "
            "review-claimable queue:" in text
        )
        assert "- `src/starved.php` (skipped by: `code-reviewer`)" in text

        assert (
            "2 changed file(s) matched no reviewer's domain and were "
            "reviewed by no one" in text
        )
        assert "- `package-lock.json`" in text
        assert "- `.editorconfig`" in text

        assert (
            "### Reviewed-file claims — claims, not proof of "
            "read" in text
        )
        assert "- `src/big.py` (claimed by: `security-reviewer`)" in text

    def test_unscoped_line_explains_why_it_can_exceed_the_metrics_figure(
        self, mod
    ):
        """F9: the section counts every changed file; run-level metrics
        count reviewable files only. Without the clause a reader treats
        the two figures as one measurement and reads the gap as a bug."""
        text = self._render(mod, unscoped=["assets/logo.png"])
        assert (
            "this counts every changed file, including binaries and "
            "non-reviewable paths — run-level metrics count reviewable "
            "files only, so its 'uncovered' figure can be smaller" in text
        )

    def test_gaps_name_every_agent_that_skipped_the_file(self, mod):
        text = self._render(
            mod,
            gaps={"src/starved.php": ["code-reviewer", "security-reviewer"]},
        )
        assert "`code-reviewer`, `security-reviewer`" in text

    def test_inline_receipt_prevents_per_agent_unclaimed_work_from_rendering_as_a_gap(
        self, mod
    ):
        text = self._render(
            mod,
            inline={"src/shared.php": ["code-reviewer"]},
            gaps={"src/shared.php": ["security-reviewer"]},
        )
        assert "skipped by every matching agent's diff budget" not in text

    def test_untrusted_values_render_as_safe_code_spans(self, mod):
        path = "src/evil``name.py\r\n## injected heading\r\n- injected file`"
        claimant = (
            "`security`reviewer\r\n# injected claimant\r\n* injected agent`"
        )
        text = self._render(mod, claims={path: [claimant]})

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

    def test_malformed_populations_are_ignored(self, mod):
        assert self._render(mod, claims=["unexpected-list"]) == ""

    def test_measured_and_empty_prints_nothing(self, mod):
        """A zero line would read as a finding."""
        assert self._render(mod, gaps={}, claims={}, unscoped=[]) == ""

    def test_unscoped_files_alone_still_render_the_section(self, mod):
        """This population reaches no per-agent bucket, so it is the one
        that would otherwise be invisible."""
        text = self._render(mod, unscoped=["assets/logo.png"])
        assert "## Review coverage" in text
        assert "- `assets/logo.png`" in text

    def test_unscoped_line_omitted_when_all_files_were_scoped(self, mod):
        text = self._render(mod, gaps={"src/starved.php": ["code-reviewer"]})
        assert "## Review coverage" in text
        assert "matched no reviewer's domain" not in text


class TestStep9ReviewRecord:
    """Step 9 is a READING step now — the record is already assembled.

    Report authoring moved wholesale to step 11, after the decision critic
    has run. What a briefing here must never do again is ask for prose:
    prose written before validation is prose that has to be corrected
    after it, and the REVISE edit-the-report dance is exactly what this
    step's rewrite deleted.
    """

    def test_points_at_the_assembled_record(self, mod, tmp_path):
        state = {"completed_steps": [], "review_record": {
            "ran": True, "written": 1, "expected": 1, "status": "complete",
        }}
        g = mod.get_step_guidance(9, "full", state, {}, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "review-record.md" in text
        assert "stand behind" in text

    def test_all_modes_have_this_step(self, mod, tmp_path):
        for mode in ("pr", "full", "incremental"):
            g = mod.get_step_guidance(9, mode, {"completed_steps": []}, {})
            assert g is not None
            assert "review-record.md" in "\n".join(g["actions"])

    def test_asks_for_no_report_and_no_edits(self, mod, tmp_path):
        """The whole point of the move: nothing presentation-shaped may
        exist while the critic runs, and the record is machine-written."""
        state = {"completed_steps": [], "review_record": {
            "ran": True, "written": 1, "expected": 1, "status": "complete",
        }}
        g = mod.get_step_guidance(9, "pr", state, {})
        text = "\n".join(g["actions"])
        assert "do not write a report yet" in text.lower()
        assert "Do not edit it" in text
        assert "authored from a source-bound settlement at step" in text

    def test_carries_no_output_instructions(self, mod, tmp_path):
        """The voice belongs to step 11 now — both the caller override and
        the mode defaults. A second copy here would drift."""
        config = {"mode": "pr", "output_instructions": "Custom only."}
        ctx = {"pr": {"author_name": "Maria Rodriguez"}}
        text = "\n".join(mod.get_step_guidance(
            9, "pr", {"completed_steps": []}, ctx, config=config
        )["actions"])
        assert "Custom only." not in text
        assert "Maria" not in text
        assert "collaboratively" not in text

    def test_carries_no_coverage_paste_block(self, mod, tmp_path):
        """The record carries the coverage section now; step 9 does not
        re-render it into a briefing the orchestrator would paste from."""
        state = {
            "completed_steps": [],
            "file_review": {
                "agents_with_unclaimed_review_by_file": {
                    "src/starved.php": ["code-reviewer"]
                },
                "agents_claiming_review_by_file": {},
                "unscoped_files": ["package-lock.json"],
            },
        }
        text = "\n".join(mod.get_step_guidance(9, "full", state, {})["actions"])
        assert "## Review coverage" not in text
        assert "```markdown" not in text

    def test_has_no_handoff_gate(self, mod, tmp_path):
        """It asks the orchestrator for no artifact. Gating on a file the
        pipeline itself just wrote would be theatre."""
        g = mod.get_step_guidance(9, "full", {"completed_steps": []}, {})
        assert g["handoff"] is None

    def test_no_recorded_outcome_makes_no_claim_either_way(
        self, mod, tmp_path
    ):
        """State with no `review_record` key at all — older state, or a
        briefing fetched on its own — is an UNMEASURED absence. Rendering
        "The review record is assembled at ..." for it states a positive
        fact nothing measured, the same failure the `critic_source`
        no-recorded-facts branch exists to avoid one step later."""
        g = mod.get_step_guidance(
            9, "full", {"completed_steps": []}, {}, output_dir=str(tmp_path)
        )
        text = "\n".join(g["situation"] + g["actions"])
        assert "The review record is assembled at" not in text
        assert "could not assemble" not in text
        assert "review-record.md" in text
        assert "if it is there" in text

    def test_reports_a_failed_assembly_and_routes_to_the_ledger(
        self, mod, tmp_path
    ):
        state = {"completed_steps": [], "review_record": {
            "ran": True, "written": 0, "expected": 1, "status": "failed",
        }}
        g = mod.get_step_guidance(9, "full", state, {}, output_dir=str(tmp_path))
        text = "\n".join(g["situation"])
        assert "could not assemble" in text
        assert "review-findings.json" in text

    def test_degraded_reconciliation_keeps_the_manual_fallback(
        self, mod, tmp_path
    ):
        """No ledger means no record. The raw agent output is the only
        material, and step 11 asks for the synthesis — not this step."""
        state = {
            "completed_steps": [],
            "degradation": {"reconciliation_failed": True},
        }
        g = mod.get_step_guidance(9, "full", state, {}, output_dir=str(tmp_path))
        text = "\n".join(g["situation"] + g["actions"])
        assert "Reconciliation failed" in text
        assert "-review.md" in text
        assert "at step 11" in text
        assert "Do not write it now" in text

    def test_keeps_the_empirical_verification_rules(self, mod, tmp_path):
        text = "\n".join(mod.get_step_guidance(
            9, "full", {"completed_steps": []}, {}
        )["actions"])
        assert "pirategoat-probe" in text
        assert "git clean" in text

    def test_change_purpose_subordinated_to_findings(self, mod, tmp_path):
        """Both the summary and the commit fallback must present author
        framing as subordinate to the reconciled findings."""
        for state in (
            {"completed_steps": [], "change_purpose": "Fix retry logic."},
            {"completed_steps": [], "commit_messages": ["fix: retry logic"]},
        ):
            g = mod.get_step_guidance(9, "pr", state, {})
            assert "source of truth" in "\n".join(g["situation"])

    def test_reinjects_change_purpose(self, mod, tmp_path):
        state = {
            "completed_steps": [],
            "change_purpose": "Adds retry logic to the payment gateway.",
        }
        g = mod.get_step_guidance(9, "pr", state, {}, output_dir=str(tmp_path))
        assert "retry logic" in "\n".join(g["situation"] + g["actions"]).lower()

    def test_reinjects_commit_messages_when_no_change_purpose(
        self, mod, tmp_path
    ):
        state = {
            "completed_steps": [],
            "commit_messages": ["feat: add payment retry"],
        }
        g = mod.get_step_guidance(9, "pr", state, {}, output_dir=str(tmp_path))
        text = "\n".join(g["situation"] + g["actions"])
        assert "payment retry" in text.lower()


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

    def test_outcome_accounting_is_per_entry_never_aggregate(
        self, mod, tmp_path
    ):
        """The field failure: a report said "all four spot-checked" about a
        FIVE-entry batch, and the unverified entry propagated as fact.

        The instruction must demand one line per adjustment id with its own
        outcome, and must forbid the aggregate phrasing outright.
        """
        g = mod.get_step_guidance(10, "pr", {"completed_steps": []}, {})
        revise = self._revise_section(g)

        assert "PER ENTRY, never in aggregate" in revise
        assert '"verified"' in revise
        assert '"refuted"' in revise
        assert "omitted" in revise and "not_checked" in revise
        assert "Never report the batch in aggregate anywhere" in revise

    def test_outcome_instruction_carries_no_aggregate_phrasing(self, mod):
        """An "all N probed" phrase may appear in exactly one place:
        the sentence that forbids it.

        Scoped per SENTENCE, not per action string. Filtering by whole
        action auto-exempted anything appended to the same `actions.append`
        as the prohibition — which is precisely where a future aggregate
        phrasing would land, since that is the action about reporting the
        batch.
        """
        revise = self._revise_section(
            mod.get_step_guidance(10, "pr", {"completed_steps": []}, {})
        )
        aggregate = re.compile(r'all ["\u201c]?(?:N|\d+)["\u201d]? '
                               r'(?:prob|verif|check)')
        sentences = re.split(r'(?<=[.:])\s+', revise)
        offenders = [
            sentence for sentence in sentences
            if aggregate.search(sentence)
            and "Never report the batch in aggregate" not in sentence
        ]
        assert not offenders, offenders
        # The prohibition itself must still be there to be exempted.
        assert any(aggregate.search(s) for s in sentences)

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

    def test_instructs_wait_for_critic(self, mod, tmp_path):
        """Critic must NOT run in background — LLM needs the verdict."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "wait" in text.lower() or "do not" in text.lower()
        assert "background" in text.lower()

    def test_revise_leaves_report_authoring_to_step_11(self, mod, tmp_path):
        """Step 10 settles data; step 11 authors prose from that state."""
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
        # The report may be named as the later product, but step 10 must not
        # tell the orchestrator to edit it.
        assert "review-report.md" in revise_text, (
            "REVISE instructions should name the report step 11 will author"
        )
        lower = revise_text.lower()
        assert "nothing else to edit" in lower

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
        assert "adjudicate" in revise_text and "--output-dir" in revise_text
        assert '"verified"' in revise_text
        assert '"refuted"' in revise_text
        assert '"revised_assessment"' in revise_text
        assert '"adjustment_id"' in revise_text
        assert "RECORDED ADJUDICATION" in revise_text
        assert "LEDGER VERDICT" in revise_text

    def test_revise_briefing_uses_assessment_language(self, mod, tmp_path):
        guidance = mod.get_step_guidance(
            10, "pr", {"completed_steps": []}, {}, output_dir=str(tmp_path)
        )
        revise_text = self._revise_section(guidance)

        assert "REVISED ASSESSMENT: present|absent" in revise_text
        assert "revised assessment" in revise_text
        assert "optional" in revise_text
        assert "REVISED NARRATIVE" not in revise_text
        assert "revised narrative" not in revise_text

    def test_revise_forbids_raw_settlement_mutation(self, mod, tmp_path):
        state = {"completed_steps": []}
        guidance = mod.get_step_guidance(
            10, "pr", state, {}, output_dir=str(tmp_path)
        )
        revise_text = self._revise_section(guidance)

        assert 'give every entry an `"outcome"` field' not in revise_text
        assert 'mark any adjustment' not in revise_text
        assert 'top-level `"revised_assessment"`' not in revise_text
        assert (
            f'critic_adjustments.py --output-dir "{tmp_path}"'
            not in revise_text
        ), "the retired bare implicit-apply command must stay absent"
        assert "never edits the committed proposal" in revise_text.lower()

    def test_revise_updates_the_ledger_before_the_report(self, mod, tmp_path):
        """Ordering is the contract: JSON first, then prose that matches it."""
        state = {"completed_steps": []}
        g = mod.get_step_guidance(10, "pr", state, {}, output_dir=str(tmp_path))
        revise_text = self._revise_section(g)

        read_adjustments = revise_text.index("decision-critic-adjustments.json")
        adjudicate = revise_text.index("critic_adjustments.py")
        edit_report = revise_text.index("review-report.md")

        assert read_adjustments < adjudicate < edit_report, (
            "REVISE must read the adjustments and adjudicate them into the "
            "findings JSON before step 11 authors the report"
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

    def test_builds_no_context_document_before_dispatch(self, mod, tmp_path):
        """The critic reads the record and the ledger directly.

        A `python3 -c` block used to merge the two into `critic-context.md`
        before every dispatch. The record renders the ledger through the
        same renderer that builder reimplemented, ids and all, so the
        builder had nothing left to add — and a context document nobody
        but one agent ever read is a projection with no human reader.
        """
        g = mod.get_step_guidance(
            10, "pr", {"completed_steps": []}, {}, output_dir=str(tmp_path)
        )
        text = "\n".join(g["actions"])
        assert "critic-context.md" not in text
        assert "build_critic_context" not in text
        # No pre-dispatch build block. (`python3 -c` still appears further
        # down, in the REVISE branch's prohibition on hand-editing the
        # ledger with one — a different instruction entirely.)
        pre_dispatch = text.split("Use this dispatch prompt:", 1)[0]
        assert "python3 -c" not in pre_dispatch

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

    def test_degraded_mode_hands_no_structured_findings(self, mod, tmp_path):
        """When reconciliation failed there is no ledger to hand over."""
        state = {"completed_steps": [], "degradation": {"reconciliation_failed": True}}
        g = mod.get_step_guidance(10, "pr", state, {}, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "--context" not in text.split("Act on the critic's verdict:")[0] or (
            "without --context" in text
        )
        assert "no structured findings" in text.lower()

    def test_normal_flow_hands_the_critic_the_record_and_the_ledger(
        self, mod, tmp_path
    ):
        """Two paths, both read directly: the record for the prose it is
        stress-testing, the JSON ledger for the ids it keys adjustments by."""
        g = mod.get_step_guidance(
            10, "pr", {"completed_steps": []}, {}, output_dir=str(tmp_path)
        )
        text = "\n".join(g["actions"])
        dispatch_start = text.index("dispatch prompt")
        dispatch_section = text[dispatch_start:].split(
            "Act on the critic's verdict:", 1
        )[0]
        assert (
            f"Review record to stress-test (for critic.py --report): "
            f"{tmp_path}/review-record.md"
        ) in dispatch_section
        assert (
            f"Structured findings (for critic.py --context): "
            f"{tmp_path}/review-findings.json"
        ) in dispatch_section

    def test_normal_flow_includes_the_record_path_in_dispatch(
        self, mod, tmp_path
    ):
        """The path handed to `critic.py --report`."""
        g = mod.get_step_guidance(
            10, "pr", {"completed_steps": []}, {}, output_dir=str(tmp_path)
        )
        text = "\n".join(g["actions"])
        assert f"{tmp_path}/review-record.md" in text
        assert "critic.py --report" in text

    def test_absent_report_puts_findings_md_in_the_dispatch_prompt(
        self, mod, tmp_path
    ):
        """When no review-record.md was assembled, the path handed to
        the critic is review-findings.md.

        Previously keyed on `degradation["report_synthesis_failed"]`, a flag
        nothing under scripts/ ever set — so this asserted a fallback that
        could not fire in production. It now keys on the existence facts
        step 10's orchestration records.
        """
        state = {
            "completed_steps": [],
            "critic_source": "review-findings.md",
        }
        g = mod.get_step_guidance(10, "pr", state, {}, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert f"{tmp_path}/review-findings.md" in text

    def test_step_10_dispatch_includes_output_dir(self, mod, tmp_path):
        """Step 10 dispatch prompt should include the output directory path."""
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "Output directory:" in text


class TestCriticVerdictPersistence:
    """Critic verdict is persisted to file and read back by step 11."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path):
        """run_pipeline's cwd has no default — see its docstring. The repo
        lives at tmp_path/repo, never at tmp_path itself: tmp_path/out is
        --output-dir for the rest of the class, and the allowlist sweep
        deletes any subdirectory it doesn't recognize (including a nested
        repo) — repo and output-dir must be siblings."""
        (tmp_path / "repo").mkdir()
        init_repo(tmp_path / "repo")
        (tmp_path / "out").mkdir()

    def test_step_11_reads_critic_verdict_from_file(self, tmp_path):
        """Step 11 should read decision-critic-verdict.json into state."""
        out = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(out), "--pr-number", "42", cwd=tmp_path / "repo")
        (out / "review-report.md").write_text("# Review")
        (out / "review-findings.json").write_text('{"verdict": "APPROVE", "findings": []}')
        _write_critic_snapshot(out, "STAND")
        r = _publish_step_11(out, tmp_path / "repo")
        assert r.returncode == 0
        result = json.loads((out / "pipeline-result.json").read_text())
        assert result["critic_verdict"] == "STAND"

    def test_step_11_critic_verdict_unavailable_when_file_missing(self, tmp_path):
        """Step 11 should report critic_verdict as unavailable when file is missing."""
        out = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(out), "--pr-number", "42", cwd=tmp_path / "repo")
        (out / "review-report.md").write_text("# Review")
        (out / "review-findings.json").write_text('{"verdict": "APPROVE", "findings": []}')
        r = _publish_step_11(out, tmp_path / "repo")
        assert r.returncode == 0
        result = json.loads((out / "pipeline-result.json").read_text())
        assert result["critic_verdict"] == "unavailable"

    def test_step_11_maps_skipped_critic_to_unavailable(self, tmp_path):
        """SKIPPED verdict (quick mode) should map to unavailable for downstream consumers."""
        out = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(out), "--pr-number", "42", cwd=tmp_path / "repo")
        (out / "review-report.md").write_text("# Review")
        (out / "review-findings.json").write_text('{"verdict": "approve", "findings": []}')
        _write_critic_snapshot(out, "SKIPPED")
        r = _publish_step_11(out, tmp_path / "repo")
        assert r.returncode == 0
        result = json.loads((out / "pipeline-result.json").read_text())
        assert result["critic_verdict"] == "unavailable"

    def test_step_1_clears_stale_critic_verdict(self, tmp_path):
        """Step 1 should clear decision-critic-verdict.json from previous runs."""
        out = tmp_path / "out"
        (out / "run-config.json").write_text('{"mode": "full"}')
        (out / "decision-critic-verdict.json").write_text('{"verdict": "REVISE"}')
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(out), cwd=tmp_path / "repo")
        assert not (out / "decision-critic-verdict.json").exists()


class TestStep10CriticSource:
    """The critic's source is chosen by what EXISTS, not by a flag.

    The old branch read `degradation["report_synthesis_failed"]` — a key no
    writer under `scripts/` ever sets, the same dead-flag class this branch
    already deleted once — and then used the render outcome as a proxy for
    the Markdown's existence, which reports `complete` for a run that had no
    ledger to render. Both roads led to the same place: the critic pointed
    at a file nobody wrote.

    `briefings.py` is pure, so step 10's orchestration records the existence
    facts into state, the way it already records `reconciliation_verdict`.
    """

    def _guidance(self, mod, tmp_path, **state_extra):
        state = {"completed_steps": [], **state_extra}
        return mod.get_step_guidance(
            10, "pr", state, {}, config={"mode": "pr"},
            output_dir=str(tmp_path),
        )

    def test_record_present_is_the_critic_target(self, mod, tmp_path):
        g = self._guidance(
            mod, tmp_path, critic_source="review-record.md",
            ledger_status="ok",
        )
        text = "\n".join(g["situation"] + g["actions"])
        assert f"{tmp_path}/review-record.md" in text
        assert "review-findings.md" not in text

    def test_the_report_is_never_the_critic_target(self, mod, tmp_path):
        """It does not exist yet — step 11 authors it, after this critic.

        The REVISE branch still NAMES it, to say the orchestrator has
        nothing to bring into agreement; what must not appear is a
        dispatch line pointing the critic at it.
        """
        g = self._guidance(
            mod, tmp_path, critic_source="review-record.md",
        )
        text = "\n".join(g["situation"] + g["actions"])
        assert f"{tmp_path}/review-report.md" not in text
        assert "authored there — once" in text

    def test_missing_report_falls_back_to_the_rendered_markdown(
        self, mod, tmp_path
    ):
        g = self._guidance(
            mod, tmp_path, critic_source="review-findings.md",
        )
        text = "\n".join(g["situation"] + g["actions"])
        assert (
            f"Review record to stress-test (for critic.py --report): "
            f"{tmp_path}/review-findings.md"
        ) in text
        assert "`review-record.md` is missing" in text

    def test_missing_markdown_falls_back_to_the_json_ledger(
        self, mod, tmp_path
    ):
        g = self._guidance(
            mod, tmp_path, critic_source="review-findings.json",
        )
        situation = "\n".join(g["situation"])
        assert "review-findings.json" in situation
        assert "review-findings.md" not in situation

    def test_an_incomplete_render_is_named_as_the_reason(self, mod, tmp_path):
        g = self._guidance(
            mod, tmp_path, critic_source="review-findings.json",
            degradation={"findings_markdown_incomplete": True},
        )
        situation = "\n".join(g["situation"])
        assert "render" in situation.lower()

    def test_nothing_present_says_so_instead_of_naming_a_missing_file(
        self, mod, tmp_path
    ):
        g = self._guidance(mod, tmp_path, critic_source=None)
        situation = "\n".join(g["situation"])
        assert "no review artifact" in situation.lower()

    def test_unrecorded_source_keeps_the_nominal_record_target(
        self, mod, tmp_path
    ):
        """No `critic_source` key at all — older state, or step 10's
        orchestration never ran — is not a measured absence, so it must not
        render as one."""
        g = self._guidance(mod, tmp_path)
        text = "\n".join(g["situation"] + g["actions"])
        assert f"{tmp_path}/review-record.md" in text
        assert "no review artifact" not in text.lower()

    def test_the_dead_flag_is_no_longer_consulted(self, mod, tmp_path):
        """`report_synthesis_failed` has no writer; reading it made the
        fallback depend on a fact nothing produced."""
        source = pathlib.Path(
            mod.__file__
        ).parent.joinpath("briefings.py")
        code = "\n".join(
            line for line in source.read_text().splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "report_synthesis_failed" not in code


class TestStep11ReportAuthoring:
    """The report is authored HERE, once, from the final post-critic state.

    Every one of these pins used to live on step 9. The move is the point:
    while the report was born before the decision critic ran, a REVISE had
    to edit it back into agreement with a ledger that had moved underneath
    it. Written after validation, it cannot be stale — there is nothing
    left to invalidate it.
    """

    _COMPLETE_RECORD = {
        "ran": True, "written": 1, "expected": 1, "status": "complete",
    }

    def _guidance(self, mod, mode="pr", state=None, ctx=None, config=None,
                  output_dir=None):
        base = {
            "completed_steps": [],
            "ledger_status": "ok",
            "review_record": self._COMPLETE_RECORD,
        }
        base.update(state or {})
        return mod.get_step_guidance(
            11, mode, base, ctx or {}, config=config, output_dir=output_dir,
        )

    def test_instructs_authoring_once_from_the_record(self, mod, tmp_path):
        text = "\n".join(
            self._guidance(mod, output_dir=str(tmp_path))["actions"]
        )
        assert "Author" in text and "review-report.md" in text
        assert "review-record.md" in text
        assert "review-findings.json" in text
        assert "once" in text.lower()
        assert "source-bound handoff" in text.lower()
        assert "rechecks their fingerprint" in text

    def test_names_the_record_as_the_thing_it_must_not_contradict(
        self, mod, tmp_path
    ):
        text = "\n".join(
            self._guidance(mod, output_dir=str(tmp_path))["actions"]
        )
        assert "must not contradict" in text

    def test_changed_prepared_source_requires_report_regeneration(
        self, mod, tmp_path
    ):
        guidance = self._guidance(mod, state={
            "publication_pending": True,
            "report_handoff_status": "source_changed",
        }, output_dir=str(tmp_path))
        text = "\n".join(guidance["actions"] + guidance["handoff"])

        assert guidance["blocks_progress"] is True
        assert "source changed" in text.lower()
        assert "regenerate" in text.lower()
        assert "re-run step 11" in text.lower()
        assert "terminal `pipeline-result.json` is now published" not in text

    def test_includes_pr_mode_defaults_with_the_author_name(self, mod):
        text = "\n".join(self._guidance(
            mod, ctx={"pr": {"author_name": "Maria Rodriguez"}},
        )["actions"])
        assert "Maria" in text
        assert "actionable" in text.lower()

    def test_output_instructions_override_replaces_the_default(self, mod):
        text = "\n".join(self._guidance(
            mod,
            ctx={"pr": {"author_name": "Maria Rodriguez"}},
            config={"mode": "pr", "output_instructions": "Custom only."},
        )["actions"])
        assert "Custom only." in text
        assert "Maria" not in text

    def test_branch_mode_default_never_names_an_author(self, mod):
        text = "\n".join(self._guidance(mod, mode="full")["actions"])
        assert "actionable" in text.lower()
        assert "first name" not in text.lower()

    def test_both_defaults_forbid_prose_demotion(self, mod):
        for mode in ("pr", "full"):
            text = "\n".join(self._guidance(mod, mode=mode)["actions"])
            assert "do not demote" in text.lower(), mode
            assert "narrow corner" in text, mode
            assert "verdict" in text.lower(), mode

    def test_report_structure_guidance(self, mod):
        text = "\n".join(self._guidance(mod)["actions"])
        assert "summary" in text.lower()
        assert "critical" in text.lower()
        assert "verdict" in text.lower()

    def test_what_held_is_sourced_from_the_ledger_not_memory(self, mod):
        text = "\n".join(self._guidance(mod)["actions"])
        assert "## Verified Checks" in text
        assert "never from memory" in text
        assert "write no such section" in text

    def test_banner_parenthetical_credits_the_pipeline_not_the_agent(
        self, mod
    ):
        ctx = {"host_context": {"banner": {
            "degraded": True, "message": "WooCommerce source unresolved.",
        }}}
        text = "\n".join(self._guidance(mod, ctx=ctx)["actions"])
        assert "Host context banner" in text
        assert "WooCommerce source unresolved." in text
        assert "reconciliator already did the same" not in text

    def test_coverage_is_quoted_from_the_record_never_re_rendered(self, mod):
        """The measurement lives in the record. Re-rendering it into the
        briefing would give the orchestrator a second copy to paraphrase
        — a field run turned the hedged sentence into "read by nobody"."""
        state = {
            "file_review": {
                "agents_with_unclaimed_review_by_file": {
                    "src/starved.php": ["code-reviewer"]
                },
                "agents_claiming_review_by_file": {},
                "unscoped_files": [],
            },
        }
        text = "\n".join(self._guidance(mod, state=state)["actions"])
        assert "Review coverage" in text
        assert "VERBATIM" in text
        assert "never restate, summarize, re-count, or edit" in text
        assert "commentary AFTER the block" in text
        assert "verdict must acknowledge this gap" in text
        # The section itself is NOT pasted here — the record carries it.
        assert "skipped by every matching agent's diff budget" not in text

    def test_verdict_gap_clause_rides_on_a_proven_gap(self, mod):
        """Claims are hedged as "not proof of read". Demanding the verdict
        acknowledge a gap on a claims-only run manufactures one."""
        state = {
            "file_review": {
                "agents_with_unclaimed_review_by_file": {},
                "agents_claiming_review_by_file": {
                    "src/big.py": ["security-reviewer"]
                },
                "unscoped_files": [],
            },
        }
        text = "\n".join(self._guidance(mod, state=state)["actions"])
        assert "Review coverage" in text
        assert "verdict must acknowledge" not in text

    def test_inline_receipt_prevents_an_unclaimed_reviewer_from_forcing_the_verdict_clause(
        self, mod
    ):
        state = {
            "file_review": {
                "agents_receiving_inline_diff_by_file": {
                    "src/shared.php": ["code-reviewer"]
                },
                "agents_with_unclaimed_review_by_file": {
                    "src/shared.php": ["security-reviewer"]
                },
                "agents_claiming_review_by_file": {},
                "unscoped_files": [],
            },
        }
        text = "\n".join(self._guidance(mod, state=state)["actions"])
        assert "verdict must acknowledge" not in text

    @pytest.mark.parametrize(
        "population",
        ["agents_with_unclaimed_review_by_file", "unscoped_files"],
    )
    def test_either_proven_gap_population_demands_the_verdict_clause(
        self, mod, population
    ):
        value = (
            {"src/starved.php": ["code-reviewer"]}
            if population == "agents_with_unclaimed_review_by_file"
            else ["package-lock.json"]
        )
        state = {"file_review": {
            "agents_with_unclaimed_review_by_file": {},
            "agents_claiming_review_by_file": {},
            "unscoped_files": [],
            population: value,
        }}
        text = "\n".join(self._guidance(mod, state=state)["actions"])
        assert "verdict must acknowledge this gap" in text

    def test_no_coverage_mention_without_a_measurement(self, mod):
        text = "\n".join(self._guidance(mod)["actions"])
        assert "Review coverage" not in text

    def test_bot_mode_says_the_report_is_the_posted_comment(self, mod):
        text = "\n".join(self._guidance(
            mod, config={"mode": "pr", "interactive": False},
        )["actions"])
        assert "posted verbatim as the PR comment" in text

    def test_degraded_reconciliation_authors_from_raw_agent_output(self, mod):
        """The sanctioned LLM-authored fallback: no ledger, no record, so
        the report is synthesized by hand — and says so."""
        text = "\n".join(self._guidance(mod, state={
            "degradation": {"reconciliation_failed": True},
            "review_record": None,
        })["actions"])
        assert "Reconciliation failed" in text
        assert "-review.md" in text
        assert "unreconciled" in text
        assert "review-record.md" not in text

    def test_failed_record_assembly_routes_to_the_ledger(self, mod):
        text = "\n".join(self._guidance(mod, state={
            "ledger_status": "ok",
            "review_record": {
                "ran": True, "written": 0, "expected": 1,
                "status": "failed",
            },
        })["actions"])
        assert "could not assemble" in text
        assert "review-findings.json" in text

    @pytest.mark.parametrize(
        "read_status", ["invalid", "io_error", "unparsable", "not_object"]
    )
    def test_rejected_ledger_is_not_report_authoring_source(
        self, mod, read_status
    ):
        text = "\n".join(self._guidance(mod, state={
            "ledger_status": read_status,
            "review_record": {
                "ran": True, "written": 0, "expected": 1,
                "status": "failed",
            },
        })["actions"])

        assert "Source:** `<OUTPUT_DIR>/review-findings.json" not in text
        assert "<OUTPUT_DIR>/<agent>-review.md" in text
        assert "rejected" in text.lower()

    def test_absent_ledger_keeps_the_no_ledger_fallback(self, mod):
        text = "\n".join(self._guidance(mod, state={
            "ledger_status": "absent",
            "review_record": None,
        })["actions"])

        assert "canonical ledger is absent" in text
        assert "<OUTPUT_DIR>/<agent>-review.md" in text
        assert "Source:** `<OUTPUT_DIR>/review-findings.json" not in text

    def test_missing_read_status_fails_closed(self, mod):
        text = "\n".join(self._guidance(mod, state={
            "ledger_status": None,
            "review_record": self._COMPLETE_RECORD,
        })["actions"])

        assert "status: `unavailable`" in text
        assert "<OUTPUT_DIR>/<agent>-review.md" in text
        assert "Source:** `<OUTPUT_DIR>/review-findings.json" not in text


class TestStep11PresentResults:
    def test_prepare_pass_blocks_until_the_report_is_handed_off(
        self, mod, tmp_path
    ):
        state = {
            "completed_steps": [],
            "publication_pending": True,
            "pipeline_status": "success",
            "verdict": "APPROVE",
            "verdict_source": "findings ledger",
            "degradation_notes": [],
        }

        guidance = mod.get_step_guidance(
            11, "pr", state, {},
            config={"mode": "pr", "interactive": False},
            output_dir=str(tmp_path),
        )
        rendered = mod.format_output(11, dict(guidance, next_step=None))

        assert guidance["blocks_progress"] is True
        assert guidance["handoff"]
        assert "review-report.md" in "\n".join(guidance["handoff"])
        assert "Prepared state:" in "\n".join(guidance["actions"])
        assert "Projection:" not in rendered
        assert "PIPELINE WAITING" in rendered
        assert "--step 11" in rendered
        assert "PIPELINE COMPLETE" not in rendered

    def test_publish_pass_closes_the_handoff(self, mod, tmp_path):
        state = {
            "completed_steps": [],
            "publication_pending": False,
            "pipeline_status": "success",
            "verdict": "APPROVE",
            "verdict_source": "findings ledger",
            "degradation_notes": [],
        }

        guidance = mod.get_step_guidance(
            11, "pr", state, {},
            config={"mode": "pr", "interactive": False},
            output_dir=str(tmp_path),
        )
        rendered = mod.format_output(11, dict(guidance, next_step=None))

        assert guidance.get("blocks_progress") is False
        assert guidance["handoff"] is None
        assert "Published: status=success  verdict=APPROVE" in rendered
        assert "PIPELINE WAITING" not in rendered
        assert "PIPELINE COMPLETE" in rendered

    def test_interactive_publish_pass_still_gates_on_the_recap(
        self, mod, tmp_path
    ):
        """Once the report exists, an interactive run still owes a human
        the recap — this is the one thing step 11 exists to produce, and
        it must not be the one step whose deliverable has no checklisted
        gate. An orchestrator that only verifies handoff-listed files
        could otherwise see the report on disk and advance to step 12
        having never actually posted the recap in chat."""
        state = {
            "completed_steps": [],
            "publication_pending": False,
            "pipeline_status": "success",
            "verdict": "APPROVE",
            "verdict_source": "findings ledger",
            "degradation_notes": [],
        }

        guidance = mod.get_step_guidance(
            11, "pr", state, {},
            config={"mode": "pr", "interactive": True},
            output_dir=str(tmp_path),
        )

        assert guidance["handoff"] is not None
        handoff_text = "\n".join(guidance["handoff"])
        assert "review-report.md" in handoff_text
        assert "chat message" in handoff_text.lower()
        assert "step 12" in handoff_text

    def test_legacy_state_never_implies_publication(self, mod, tmp_path):
        """Only an explicit false pending flag proves the terminal marker
        was committed; older state with a settled verdict remains pending."""
        state = {
            "completed_steps": [],
            "pipeline_status": "degraded",
            "verdict": "COMMENT",
            "verdict_source": "fallback: no usable ledger verdict",
            "degradation_notes": ["review-findings.json not found"],
        }

        guidance = mod.get_step_guidance(
            11, "pr", state, {},
            config={"mode": "pr", "interactive": False},
            output_dir=str(tmp_path),
        )
        text = "\n".join(guidance["actions"])

        assert guidance["blocks_progress"] is True
        assert "Prepared state:" in text
        assert "Published:" not in text

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
        [(2, "partial"), (1, "failed")],
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
            f"python3 {SCRIPT_PATH.parent}/review_markdown.py "
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

    @pytest.mark.parametrize("interactive", [True, False])
    def test_findings_markdown_outcome_is_reported_beside_the_reviewer_one(
        self, mod, tmp_path, interactive
    ):
        """Write-only state is unreportable state. Step 11 is where a run
        says what it left behind, and the findings render is a best-effort
        artifact three degraded paths depend on."""
        state = {
            "completed_steps": [],
            "ledger_status": "ok",
            "findings_markdown": {
                "ran": True, "written": 1, "expected": 1,
                "status": "complete",
            },
        }
        guidance = mod.get_step_guidance(
            11, "pr", state, {},
            config={"mode": "pr", "interactive": interactive},
            output_dir=str(tmp_path),
        )
        lines = [
            line for line in guidance["actions"]
            if "Findings Markdown:" in line
        ]
        assert lines == ["Findings Markdown: materialized 1/1 files."]

    @pytest.mark.parametrize("interactive", [True, False])
    def test_failed_findings_render_carries_its_own_recovery_command(
        self, mod, tmp_path, interactive
    ):
        state = {
            "completed_steps": [],
            "ledger_status": "ok",
            "findings_markdown": {
                "ran": True, "written": 0, "expected": 1, "status": "failed",
            },
            "degradation": {"findings_markdown_incomplete": True},
        }
        guidance = mod.get_step_guidance(
            11, "pr", state, {},
            config={"mode": "pr", "interactive": interactive},
            output_dir=str(tmp_path),
        )
        lines = [
            line for line in guidance["actions"]
            if "Findings Markdown:" in line
        ]
        assert len(lines) == 1
        assert "0/1" in lines[0]
        # The suffix is what makes the printed command actually rebuild the
        # findings ledger rather than the per-reviewer family.
        assert (
            f"materialize {tmp_path} --suffix review-findings.json"
            in lines[0]
        )

    def test_absent_findings_markdown_state_reports_it_did_not_run(
        self, mod, tmp_path
    ):
        guidance = mod.get_step_guidance(
            11, "pr", {
                "completed_steps": [], "ledger_status": "ok",
            }, {},
            config={"mode": "pr", "interactive": True},
            output_dir=str(tmp_path),
        )
        lines = [
            line for line in guidance["actions"]
            if "Findings Markdown:" in line
        ]
        assert len(lines) == 1
        assert "did not run" in lines[0]

    @pytest.mark.parametrize(
        "key,label",
        [("reviewer_markdown", "Reviewer Markdown"),
         ("findings_markdown", "Findings Markdown")],
    )
    def test_nothing_to_render_is_reported_without_a_recovery_command(
        self, mod, tmp_path, key, label
    ):
        """A completed render of zero sources is a measured zero, not a
        gap: there is nothing to regenerate, so offering the command
        would send the reader after work that cannot exist."""
        state = {
            "completed_steps": [],
            "ledger_status": (
                "absent" if key == "findings_markdown" else "ok"
            ),
            key: {
                "ran": True, "written": 0, "expected": 0,
                "status": "complete",
            },
        }
        guidance = mod.get_step_guidance(
            11, "pr", state, {},
            config={"mode": "pr", "interactive": True},
            output_dir=str(tmp_path),
        )
        lines = [l for l in guidance["actions"] if f"{label}:" in l]
        assert len(lines) == 1
        assert "⚠️" not in lines[0]
        assert "regenerate" not in lines[0]
        assert "nothing to render" in lines[0]

    def test_incremental_mentions_baseline_saved(self, mod, tmp_path):
        config = {"mode": "incremental", "interactive": True}
        state = {"completed_steps": [], "publication_pending": False}
        ctx = {}
        g = mod.get_step_guidance(11, "incremental", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "baseline saved" in text.lower() or "next" in text.lower()

    def test_interactive_has_focused_reconciliator_followup(self, mod, tmp_path):
        """Interactive mode should offer focused reconciliator for drill-down."""
        config = {"mode": "pr", "interactive": True}
        state = {"completed_steps": [], "publication_pending": False}
        ctx = {}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        lower = text.lower()
        assert any(phrase in lower for phrase in [
            "focused", "drill down", "re-invoke", "reconciliator",
        ]), "Interactive mode should offer follow-up analysis option"

    def test_step11_derives_the_verdict_into_state(self, mod, tmp_path):
        """Step 11 orchestration derives state['verdict'] from the findings
        ledger — the artifact whose verdict was actually computed from
        findings — not from a verdict the orchestrator transcribed."""
        findings = canonical_findings_ledger(["medium"], reconciliation={
            "reviewing_agents": ["code-reviewer"],
            "dispatched_agents": ["code-reviewer"],
        })
        (tmp_path / "review-findings.json").write_text(
            json.dumps(findings)
        )

        state = {
            "resolved_params": {},
            "completed_steps": [1, 2, 3, 5, 6, 7, 8, 9, 10],
            "verdict": None,
            "agents": {
                "dispatched": [], "completed": [], "discarded_drafts": [],
            },
        }
        config = {"mode": "pr", "interactive": True}
        context = {}

        mod._orchestrate_step(11, "pr", config, state, context, str(tmp_path))

        assert state["verdict"] == "COMMENT"
        assert state["verdict_source"] == "findings ledger"

    def test_incremental_mentions_next_code_review(self, mod, tmp_path):
        """Incremental should mention next /code-review scope."""
        config = {"mode": "incremental", "interactive": True}
        state = {"completed_steps": [], "publication_pending": False}
        ctx = {}
        g = mod.get_step_guidance(11, "incremental", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "new commits" in text.lower() or "/code-review" in text


class TestStep12Cleanup:
    def test_a_degraded_run_flags_the_footer(self, mod, tmp_path):
        """Step 12 is the LAST step of an INTERACTIVE run, so it — not step
        11 — is where the completion footer prints there. Setting the flag
        only on step 11 made the whole honesty mechanism bot-only: an
        interactive run printed its degradations at step 11 and then signed
        off "✅ PIPELINE COMPLETE" at step 12."""
        state = {"workspace": {"original_branch": "develop"},
                 "completed_steps": [], "pipeline_status": "degraded"}
        g = mod.get_step_guidance(12, "pr", state, {})
        assert g["degraded"] is True
        assert "DEGRADED" in mod.format_output(12, dict(g, next_step=None))

    def test_a_clean_run_keeps_the_checkmark(self, mod, tmp_path):
        state = {"workspace": {"original_branch": "develop"},
                 "completed_steps": [], "pipeline_status": "success"}
        g = mod.get_step_guidance(12, "pr", state, {})
        assert g["degraded"] is False
        assert "✅" in mod.format_output(12, dict(g, next_step=None))

    def test_an_unfinalized_run_claims_nothing(self, mod, tmp_path):
        """No finalize, no outcome. Claiming either way is a fabrication."""
        state = {"workspace": {"original_branch": "develop"},
                 "completed_steps": []}
        assert mod.get_step_guidance(12, "pr", state, {})["degraded"] is False

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
        state = {"completed_steps": [], "publication_pending": False}
        ctx = {}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "pipeline-result.json" in text
        # Schema fields should be referenced or documented
        for field in ("status", "verdict", "report_path", "findings_path",
                      "critic_verdict", "degradation_notes",
                      "worktree_hygiene", "usage", "verdict_source"):
            assert field in text, f"Step 11 output missing pipeline-result.json field: {field}"

    def test_scenario_a_reconciliation_failed(self, mod, tmp_path):
        """Step 9 should run degraded when reconciliation failed."""
        state = {"completed_steps": [], "degradation": {"reconciliation_failed": True}}
        ctx = {}
        g = mod.get_step_guidance(9, "pr", state, ctx)
        text = "\n".join(g["actions"])
        assert "raw agent" in text.lower() or "degraded" in text.lower()

    def test_scenario_b_report_missing(self, mod, tmp_path):
        """Step 10 falls back to review-findings.md when review-report.md is
        absent — established by step 10's recorded existence facts, not by
        the writer-less `report_synthesis_failed` flag this once read."""
        state = {
            "completed_steps": [],
            "critic_source": "review-findings.md",
        }
        g = mod.get_step_guidance(10, "pr", state, {})
        text = "\n".join(g["actions"])
        assert "review-findings.md" in text

    def test_scenario_c_critic_failed(self, mod, tmp_path):
        """Step 11 should show critic_verdict as unavailable when critic failed.

        `state["critic_verdict"]` is the sole signal for this — there is
        no separate `degradation["critic_failed"]` flag in production
        (grep confirms nothing under scripts/ ever sets it); a missing,
        unparseable, or SKIPPED critic verdict all collapse into
        "unavailable" via `critic_verdict_for_state()` before step 11
        even reaches this briefing.
        """
        state = {"completed_steps": [], "critic_verdict": "unavailable"}
        ctx = {}
        config = {"mode": "pr", "interactive": True}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "unavailable" in text.lower()

    def test_scenario_d_both_failed(self, mod, tmp_path):
        """Both reconciliation and report failed: the run publishes the
        fallback COMMENT and says so.

        This used to be pinned through a `forced_verdict` state key no
        writer under `scripts/` ever set, so the assertion passed on a
        branch production could not reach. It now reads the projection
        finalize actually records.
        """
        state = {"completed_steps": [],
                 "degradation": {"reconciliation_failed": True},
                 "pipeline_status": "degraded",
                 "verdict": "COMMENT",
                 "verdict_source": "fallback: no usable ledger verdict",
                 "degradation_notes": ["review-findings.json not found",
                                       "review-report.md not found"]}
        ctx = {}
        config = {"mode": "pr", "interactive": True}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        text = "\n".join(g["actions"])
        assert "verdict=COMMENT (fallback: no usable ledger verdict)" in text
        assert "status=degraded" in text
        assert "  - review-findings.json not found" in text
        assert g["degraded"] is True

    def test_step_11_briefing_without_a_projection(self, mod, tmp_path):
        """A briefing fetched before finalize ran has no outcome to report.
        It must render without one rather than fabricating a success line."""
        state = {"completed_steps": []}
        ctx = {}
        config = {"mode": "pr", "interactive": False}
        g = mod.get_step_guidance(11, "pr", state, ctx, config=config)
        assert g is not None
        assert "Projection:" not in "\n".join(g["actions"])
        assert g.get("degraded") is False


class TestStep10QuickMode:
    """Step 10 quick mode: skip critic when verdict is low-risk."""

    def test_skip_critic_on_approve_verdict(self, mod, tmp_path):
        state = {"completed_steps": [], "reconciliation_verdict": "approve"}
        config = {"quick": True}
        g = mod.get_step_guidance(10, "pr", state, {}, config=config, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "decision-reviewer" not in text
        assert "decision-critic-verdict.json" in text

    def test_quick_skip_asks_the_orchestrator_for_no_verdict(
        self, mod, tmp_path
    ):
        """The briefing used to hand the orchestrator a reconciliation-to-
        review verdict mapping to transcribe by hand — the one place a quick
        run could publish an approval for a `comment` reconciliation. The
        pipeline writes its own skip verdict now, and step 11 derives the
        published one from the ledger, so nothing is transcribed here."""
        state = {"completed_steps": [], "reconciliation_verdict": "comment"}
        config = {"quick": True}
        g = mod.get_step_guidance(10, "pr", state, {}, config=config, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert '{"verdict": "COMMENT"}' not in text
        assert '{"verdict": "APPROVE"}' not in text
        assert "review-verdict.json" not in text

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

    def test_quick_skip_gates_on_nothing(self, mod, tmp_path):
        """No artifact is asked of the orchestrator on this branch, so there
        is nothing to gate — a handoff naming a file the pipeline writes
        would be theatre."""
        state = {"completed_steps": [], "reconciliation_verdict": "approve"}
        config = {"quick": True}
        g = mod.get_step_guidance(10, "pr", state, {}, config=config, output_dir=str(tmp_path))
        assert g["handoff"] is None

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

    _CLEAN_STATE = {
        "completed_steps": [],
        "dependency_refresh_precheck": {
            "tracked_files_dirty": False,
            "dirty_files": [],
        },
    }

    def _text(self, g):
        parts = list(g["situation"]) + list(g["actions"])
        if g.get("handoff"):
            parts += list(g["handoff"])
        return "\n".join(parts)

    def test_clean_precheck_offers_adaptive_refresh_and_save_channel(
        self, mod, tmp_path
    ):
        config = {"mode": "full", "interactive": True,
                  "refresh_dependencies": True}
        g = mod.get_step_guidance(3, "full", dict(self._CLEAN_STATE), {},
                                  config=config, output_dir=str(tmp_path))
        text = self._text(g)
        lowered = text.lower()
        assert "Dependency refresh" in text
        assert "inspect the repository and reviewed change" in lowered
        assert "lockfile-preserving" in text
        assert "decide whether dependency installation is needed" in lowered
        assert "--refresh-host-context" in text
        assert "$TMPDIR/dependency-refresh-report.json" in text
        assert "dependency_refresh.py" in text
        assert " save " in text
        assert "SAVED dependency-refresh.json" in text
        assert '"schema": 1' in text
        assert '"status": "not_needed"' in text
        assert '"commands": []' in text
        assert "change-purpose.md" in text
        for manager in ("composer install", "npm ci", "pnpm install", "yarn install"):
            assert manager not in text
        assert "verifies execution" not in text

    def test_refresh_handoff_survives_unfetched_issues(self, mod, tmp_path):
        state = dict(self._CLEAN_STATE)
        state["resolved_params"] = {"has_unfetched_issues": True}
        config = {"mode": "full", "interactive": True,
                  "refresh_dependencies": True}
        g = mod.get_step_guidance(3, "full", state, {},
                                  config=config, output_dir=str(tmp_path))
        handoff_text = "\n".join(g["handoff"] or [])
        assert "dependency-refresh.json" in handoff_text
        # change-purpose moves to step 4 when issues are unfetched
        assert "change-purpose.md" not in handoff_text

    @pytest.mark.parametrize("tracked_files_dirty", [True, None])
    def test_unsafe_or_unknown_precheck_offers_no_execution_or_save_handoff(
        self, mod, tmp_path, tracked_files_dirty
    ):
        state = {
            "dependency_refresh_precheck": {
                "tracked_files_dirty": tracked_files_dirty,
                "dirty_files": (
                    ["tracked.txt"] if tracked_files_dirty is True else []
                ),
            }
        }
        config = {"refresh_dependencies": True}

        situation, actions, handoff = mod._dependency_refresh_briefing(
            state, config, str(tmp_path)
        )

        text = "\n".join(situation)
        assert "will not run dependency commands" in text
        assert "unsafe" in text or "unknown" in text
        assert actions == []
        assert handoff == []

    def test_flag_off_renders_nothing(self, mod, tmp_path):
        config = {"mode": "full", "interactive": True}
        g = mod.get_step_guidance(3, "full", dict(self._CLEAN_STATE), {},
                                  config=config, output_dir=str(tmp_path))
        text = self._text(g)
        assert "Dependency refresh" not in text
        assert "dependency-refresh.json" not in text


class TestStep11Projection:
    """Step 11's briefing distinguishes prepared from published state.

    Before this, the only outcome line the briefing carried was a
    `forced_verdict` warning no writer under `scripts/` ever set — so a
    degraded run printed "✅ PIPELINE COMPLETE" with its degradations
    sitting unread in a JSON file the human never opened.
    """

    _STATE = {
        "completed_steps": [],
        "publication_pending": False,
        "pipeline_status": "degraded",
        "verdict": "REQUEST_CHANGES",
        "verdict_source": "findings ledger",
        "degradation_notes": ["critic produced no verdict artifact"],
    }

    def _text(self, mod, **overrides):
        state = dict(self._STATE)
        state.update(overrides)
        g = mod.get_step_guidance(
            11, "pr", state, {}, config={"mode": "pr", "interactive": True}
        )
        return g, "\n".join(g["actions"])

    def test_it_renders_status_verdict_and_source(self, mod):
        _g, text = self._text(mod)
        assert (
            "Published: status=degraded  verdict=REQUEST_CHANGES "
            "(findings ledger)" in text
        )

    def test_it_lists_every_degradation(self, mod):
        _g, text = self._text(mod)
        assert "Degradations:" in text
        assert "  - critic produced no verdict artifact" in text

    def test_a_degraded_run_flags_the_footer(self, mod):
        g, _text = self._text(mod)
        assert g["degraded"] is True

    def test_a_clean_run_does_not_flag_the_footer(self, mod):
        g, text = self._text(
            mod, pipeline_status="success", degradation_notes=[]
        )
        assert g["degraded"] is False
        assert "Degradations:" not in text

    def test_an_unfinalized_run_reports_no_projection(self, mod):
        """A briefing fetched before finalize ran has nothing to report.
        Unmeasured and clean are different facts."""
        g = mod.get_step_guidance(
            11, "pr", {"completed_steps": []}, {},
            config={"mode": "pr", "interactive": True},
        )
        assert "Projection:" not in "\n".join(g["actions"])
        assert g["degraded"] is False

    def test_the_escalate_override_names_itself(self, mod):
        _g, text = self._text(
            mod, verdict="COMMENT",
            verdict_source="critic ESCALATE override",
        )
        assert "verdict=COMMENT (critic ESCALATE override)" in text


class TestStep10WritesItsOwnSkipVerdict:
    """The quick-mode skip is the PIPELINE's decision, so the pipeline
    records it. Asking the orchestrator to transcribe a verdict for a
    decision it did not make left a run that stopped short with no verdict
    artifact at all — indistinguishable at finalize from a critic that ran
    and crashed."""

    def _run_step_10(self, mod, tmp_path, recon_verdict, quick=True):
        state = {"completed_steps": [], "reconciliation_verdict": recon_verdict}
        mod._orchestrate_step(
            10, "pr", {"quick": quick}, state, {}, str(tmp_path)
        )
        return state

    @pytest.mark.parametrize("recon", ["approve", "comment", "COMMENT"])
    def test_the_skip_verdict_lands_on_disk(self, mod, tmp_path, recon):
        state = self._run_step_10(mod, tmp_path, recon)
        proposal = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        written = json.loads(
            (tmp_path / "decision-critic-verdict.json").read_text()
        )
        from review import critic_adjustments

        assert proposal == {"schema": 2, "adjustments": []}
        assert written == {
            "schema": 2,
            "verdict": "SKIPPED",
            "proposal_digest": critic_adjustments.proposal_digest(proposal),
        }
        assert recon in state["step_decisions"]["10"]["reason"]

    def test_a_dispatched_critic_gets_no_pipeline_written_verdict(
        self, mod, tmp_path
    ):
        """The critic's own verdict is the critic's to report; the
        orchestrator transcribes it verbatim after the critic returns."""
        self._run_step_10(mod, tmp_path, "request_changes")
        assert not (tmp_path / "decision-critic-verdict.json").exists()

    def test_the_skip_branch_writes_no_dispatch_marker(self, mod, tmp_path):
        """A critic that never ran has no duration, and the marker's
        absence is what keeps finalize from reporting a stall — or, now,
        the missing-artifact degradation."""
        from review import synthesis_lifecycle
        self._run_step_10(mod, tmp_path, "approve")
        assert not os.path.isfile(synthesis_lifecycle.marker_path(
            str(tmp_path), synthesis_lifecycle.DECISION_CRITIC
        ))

"""Tests for review-pipeline.py — orchestration: subprocess calls, telemetry, integration."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import PIPELINE_SCRIPT_PATH as SCRIPT_PATH


@pytest.fixture(scope="module")
def mod(pipeline_mod):
    return pipeline_mod


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


class TestStep3Orchestration:
    """Step 3 main() runs gather-review-context.py and hydrates state."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_3_runs_gather_context(self, tmp_path):
        """Step 3 should invoke gather-review-context.py (may fail in test env, but state should update)."""
        # Seed step 1
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        # Run step 3
        r = self._run("--step", "3", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        # State should have completed_steps including 3
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert 3 in state["completed_steps"]

    def test_step_3_hydrates_unfetched_issues_from_context(self, tmp_path):
        """When review-context.json has has_unfetched_issues, state should reflect it."""
        # Seed step 1
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        # Pre-write review-context.json as if gather-review-context.py produced it
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
            "has_unfetched_issues": True,
            "linked_issues": ["WOOPLUG-1234"],
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        # Run step 3 — it should read the context and hydrate state
        r = self._run("--step", "3", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["resolved_params"]["has_unfetched_issues"] is True

    def test_step_3_without_context_still_succeeds(self, tmp_path):
        """Step 3 should not crash if gather-review-context.py fails (no git repo)."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        r = self._run("--step", "3", "--mode", "full",
                       "--output-dir", str(tmp_path))
        # Should succeed even without a git repo — subprocess failure is tolerated
        assert r.returncode == 0

    def test_step_3_next_step_reflects_unfetched_issues(self, tmp_path):
        """When has_unfetched_issues is True, next step after 3 should be 4 (not 5)."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
            "has_unfetched_issues": True,
            "linked_issues": ["WOOPLUG-1234"],
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "3", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        # Output should point to step 4, not step 5
        assert "Step 4" in r.stdout


class TestStep5Orchestration:
    """Step 5 main() runs plan-review-dispatch.py and stores output in state."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_5_stores_dispatch_plan_output(self, tmp_path):
        """Step 5 should store dispatch plan output in state."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "5", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert 5 in state["completed_steps"]
        # dispatch_plan_output may be empty if planner fails, but key should exist
        assert "dispatch_plan_output" in state or "dispatch_plan_summary" in state


class TestStep6Orchestration:
    """Step 6 main() reads dispatch-plan.json and populates dispatched_agents."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_6_populates_dispatched_agents(self, tmp_path):
        """Step 6 should read dispatch-plan.json and populate state.dispatched_agents."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        plan = {
            "agents": [
                {"name": "pr-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "always"},
                {"name": "go-tests-reviewer", "domain": "go-tests", "status": "SKIPPED", "reason": "no files"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "6", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        names = [a["name"] for a in state.get("dispatched_agents", [])]
        assert "pr-reviewer" in names
        assert "security-reviewer" in names
        assert "go-tests-reviewer" not in names

    def test_step_6_output_contains_bootstrap_calls(self, tmp_path):
        """Step 6 output should contain concrete bootstrap-reviewer.py calls."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        plan = {
            "agents": [
                {"name": "pr-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "6", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert "bootstrap-reviewer.py" in r.stdout
        assert "pr-reviewer" in r.stdout
        assert "abc..HEAD" in r.stdout


class TestStep7Orchestration:
    """Step 7 main() writes .branch-review-baseline.json."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_7_writes_baseline_file(self, tmp_path):
        """Step 7 should create .branch-review-baseline.json."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "7", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        baseline_path = tmp_path / ".branch-review-baseline.json"
        assert baseline_path.is_file(), "Baseline file was not created"
        baseline = json.loads(baseline_path.read_text())
        assert "last_reviewed_sha" in baseline
        assert "last_reviewed_at" in baseline
        assert "review_type" in baseline
        assert baseline["review_type"] == "full"
        assert "git_range_used" in baseline
        assert ".." in baseline["git_range_used"]

    def test_step_7_baseline_grades_clean(self, tmp_path):
        """The written baseline should pass the grader."""
        from graders import grade_review_baseline
        self._run("--step", "1", "--mode", "incremental",
                   "--output-dir", str(tmp_path))
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        self._run("--step", "7", "--mode", "incremental",
                   "--output-dir", str(tmp_path))
        baseline_path = tmp_path / ".branch-review-baseline.json"
        result = grade_review_baseline(str(baseline_path))
        assert result.passed, f"Baseline grading failed: {result.failures}"


class TestStep8Orchestration:
    """Step 8 main() reads change-purpose.md and agent completion status."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_8_reads_change_purpose(self, tmp_path):
        """Step 8 should read change-purpose.md into state."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        (tmp_path / "change-purpose.md").write_text("Adds retry logic to payment gateway.")
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "8", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "retry logic" in state.get("change_purpose", "").lower()

    def test_step_8_stores_review_file_paths(self, tmp_path):
        """Step 8 should store paths to completed review files in state."""
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        plan = {
            "agents": [
                {"name": "pr-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "always"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        # Simulate pr-reviewer finished, security-reviewer not
        (tmp_path / "pr-review.json").write_text('{"verdict": "approve", "issues": []}')
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = self._run("--step", "8", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        review_files = state.get("agents", {}).get("review_files", [])
        assert any("pr-review.json" in f for f in review_files)


class TestStep11Orchestration:
    """Step 11 main() reads review-verdict.json and writes pipeline-result.json."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_11_writes_pipeline_result(self, tmp_path):
        """Step 11 should write pipeline-result.json."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        (tmp_path / "review-verdict.json").write_text('{"verdict": "REQUEST_CHANGES"}')
        (tmp_path / "review-report.md").write_text("# Review Report\nFindings here.")
        (tmp_path / "review-findings.json").write_text('{"verdict": "COMMENT", "issues": []}')
        r = self._run("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        result_path = tmp_path / "pipeline-result.json"
        assert result_path.is_file(), "pipeline-result.json was not created"
        result = json.loads(result_path.read_text())
        assert result["verdict"] == "REQUEST_CHANGES"
        assert result["status"] in ("success", "degraded")
        assert "report_path" in result

    def test_step_11_updates_findings_verdict(self, tmp_path):
        """Step 11 should update review-findings.json verdict to match review-verdict.json (rule 23)."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        (tmp_path / "review-verdict.json").write_text('{"verdict": "REQUEST_CHANGES"}')
        (tmp_path / "review-report.md").write_text("# Review")
        (tmp_path / "review-findings.json").write_text('{"verdict": "COMMENT", "issues": []}')
        self._run("--step", "11", "--mode", "pr",
                   "--output-dir", str(tmp_path))
        findings = json.loads((tmp_path / "review-findings.json").read_text())
        assert findings["verdict"] == "REQUEST_CHANGES"

    def test_step_11_handles_missing_verdict(self, tmp_path):
        """Step 11 should handle missing review-verdict.json gracefully."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        r = self._run("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        result_path = tmp_path / "pipeline-result.json"
        assert result_path.is_file()
        result = json.loads(result_path.read_text())
        assert result["status"] in ("degraded", "failed")

    def test_step_11_degrades_when_findings_missing(self, tmp_path):
        """Step 11 should report degraded when review-findings.json is missing (partial run)."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        # Verdict and report exist, but findings do not (reconciliation failed)
        (tmp_path / "review-verdict.json").write_text('{"verdict": "COMMENT"}')
        (tmp_path / "review-report.md").write_text("# Review\nReport here.")
        r = self._run("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert any("review-findings.json" in n for n in result["degradation_notes"])


class TestTelemetryFinalize:
    """Telemetry finalize is called at the last active step."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_last_step_finalizes_telemetry(self, tmp_path):
        """The last active step should call telemetry.finalize()."""
        log_dir = tmp_path / "telemetry-logs"
        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            self._run("--step", "1", "--mode", "full",
                       "--output-dir", str(tmp_path))
            # Step 11 is the last active step for non-interactive full mode
            # (step 12 needs workspace + interactive)
            self._run("--step", "11", "--mode", "full",
                       "--output-dir", str(tmp_path))
        marker = tmp_path / ".telemetry-log-path"
        if marker.is_file():
            log_path = marker.read_text().strip()
            with open(log_path) as f:
                lines = f.readlines()
            events = [json.loads(l)["event"] for l in lines]
            assert "pipeline_end" in events, f"Expected pipeline_end event, got: {events}"


class TestStep8AgentPrompt:
    """Step 8 should emit a complete reconciliator Agent tool prompt (rule 15)."""

    def test_reconciliator_prompt_has_concrete_values(self, mod, tmp_path):
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": ["pr-reviewer", "security-reviewer"],
                "completed": ["pr-reviewer", "security-reviewer"],
                "failed": [],
            },
            "change_purpose": "Adds retry logic.",
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py,b.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "abc..HEAD" in text  # concrete range
        assert "a.py" in text or "changed_files_csv" in text.lower() or "dispatch-plan.json" in text


class TestStep10AgentPrompt:
    """Step 10 should emit a complete decision critic Agent tool prompt (rule 15)."""

    def test_critic_prompt_has_concrete_path(self, mod, tmp_path):
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(10, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert str(tmp_path) in text or "review-report.md" in text


class TestFullSequenceIntegration:
    """Full multi-step sequence produces pipeline-result.json."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_full_sequence_produces_pipeline_result(self, tmp_path):
        """Run steps 1,3,5,6,7,8,11 in order — pipeline-result.json should exist."""
        od = str(tmp_path)
        # Step 1: seed
        r = self._run("--step", "1", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0

        # Pre-write context as if gather-review-context.py succeeded
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "changed_files_csv": "a.py",
                    "commit_count": 1, "base_ref": "main"},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))

        # Step 3: gather context (reads the pre-written file)
        r = self._run("--step", "3", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0

        # Step 5: dispatch plan (may fail without git, but should not crash)
        r = self._run("--step", "5", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0

        # Pre-write dispatch plan as if planner succeeded
        plan = {"agents": [{"name": "pr-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"}], "git_range": "abc..HEAD"}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        # Step 6: dispatch agents
        r = self._run("--step", "6", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0

        # Step 7: save baseline
        r = self._run("--step", "7", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0
        assert (tmp_path / ".branch-review-baseline.json").is_file()

        # Step 8: reconcile (no review files exist — that's OK)
        r = self._run("--step", "8", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0

        # Pre-write verdict, report, and findings as if steps 8-10 ran
        (tmp_path / "review-verdict.json").write_text('{"verdict": "APPROVE"}')
        (tmp_path / "review-report.md").write_text("# Review\nAll clear.")
        (tmp_path / "review-findings.json").write_text('{"verdict": "APPROVE", "issues": []}')

        # Step 11: present results
        r = self._run("--step", "11", "--mode", "full", "--output-dir", od)
        assert r.returncode == 0
        assert (tmp_path / "pipeline-result.json").is_file()
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["verdict"] == "APPROVE"
        assert result["status"] == "success"
        assert result["review_baseline_saved"] is True


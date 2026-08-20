"""Tests for review/orchestration.py through the pipeline.py compatibility facade."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
_SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(TESTS_DIR))
from helpers.pipeline_process import (
    add_commit as _add_commit,
    hermetic_env,
    init_repo as _init_git_repo,
    run_pipeline,
)
_dispatch_spec = importlib.util.spec_from_file_location(
    "plan_review_dispatch", str(_SCRIPTS_DIR / "review" / "plan_dispatch.py")
)
_dispatch_mod = importlib.util.module_from_spec(_dispatch_spec)
_dispatch_spec.loader.exec_module(_dispatch_mod)

build_dispatch_plan = _dispatch_mod.build_dispatch_plan
load_registry = _dispatch_mod.load_registry


@pytest.fixture(scope="module")
def mod(pipeline_mod):
    return pipeline_mod


def _review_json(reviewer):
    """Return one complete v1 review artifact accepted by render_markdown()."""
    return {
        "pr_id": "42",
        "reviewer": reviewer,
        "timestamp": "2026-08-10T12:00:00",
        "schema": 1,
        "verdict": "approve",
        "summary": {
            "total_issues": 0,
            "by_severity": {
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "info": 0,
            },
        },
        "issues": [],
        "observations": None,
        "recommendations": None,
        "positive_observations": None,
        "meta": {
            "files_reviewed": 1,
            "review_duration_ms": 10,
            "confidence_score": 1.0,
            "tool_results_used": None,
        },
    }


class TestTelemetryIntegration:
    """Verify pipeline calls telemetry at each step."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path):
        """Every subprocess call in this class needs an isolated cwd — see
        run_pipeline's docstring. Tests that need a specific git identity
        build their own `repo` subdir instead; this just gives the rest of
        the class a safe default at `tmp_path` itself."""
        _init_git_repo(tmp_path)

    def test_step_1_creates_telemetry_log(self, tmp_path):
        """Step 1 should create a telemetry log and running manifest."""
        log_dir = tmp_path / "telemetry-logs"
        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            r = run_pipeline(
                "--step", "1", "--mode", "pr",
                "--output-dir", str(tmp_path), "--pr-number", "42",
                cwd=tmp_path,
            )
        assert r.returncode == 0
        marker = tmp_path / ".telemetry-log-path"
        assert marker.is_file()
        log_path = Path(marker.read_text().strip())
        manifest_path = log_path.with_suffix(".manifest.json")
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["status"] == "running"

    def test_telemetry_failure_does_not_break_pipeline(self, tmp_path):
        """Pipeline works even if telemetry log_dir is unwritable."""
        log_dir = tmp_path / "unwritable"
        log_dir.mkdir()
        log_dir.chmod(0o000)
        try:
            with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
                r = run_pipeline(
                    "--step", "1", "--mode", "pr",
                    "--output-dir", str(tmp_path), "--pr-number", "42",
                    cwd=tmp_path,
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
            run_pipeline("--step", "1", "--mode", "pr",
                          "--output-dir", str(tmp_path), "--pr-number", "42",
                          cwd=tmp_path)
            run_pipeline("--step", "3", "--mode", "pr",
                         "--output-dir", str(tmp_path), cwd=tmp_path)
        marker = tmp_path / ".telemetry-log-path"
        log_path = marker.read_text().strip()
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "pipeline_start"
        assert json.loads(lines[1])["event"] == "step"

    def test_step_1_uses_preserved_bot_context_git_identity(self, tmp_path):
        """Bot-provided range and full SHAs survive into pipeline_start.

        The bot computes merge_base via `git merge-base` and head_sha via
        `git rev-parse HEAD`, so its context values are always full SHAs and
        pass through verbatim. Symbolic context values (an explicit range like
        "main..HEAD" stores "main" as merge_base) are resolved instead — a
        durable manifest must never record a movable ref as base_sha.
        """
        context_base = "a" * 40
        context_head = "b" * 40
        (tmp_path / "run-config.json").write_text(json.dumps({
            "mode": "pr",
            "pr_number": "42",
            "interactive": False,
            "session_id": "bot-session",
        }))
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "context-base..context-head",
                "merge_base": context_base,
                "head_sha": context_head,
            },
        }))
        log_dir = tmp_path / "telemetry-logs"

        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            result = run_pipeline("--step", "1", "--output-dir", str(tmp_path), cwd=tmp_path)

        assert result.returncode == 0
        log_path = (tmp_path / ".telemetry-log-path").read_text().strip()
        with open(log_path) as f:
            start = json.loads(f.readline())
        assert start["pipeline"]["git"] == {
            "requested_range": "context-base..context-head",
            "base_sha": context_base,
            "head_sha": context_head,
        }

    def test_step_1_resolves_symbolic_context_merge_base(self, tmp_path):
        """A symbolic context merge_base (explicit "main..HEAD" range) must be
        resolved to a commit SHA before entering the durable run identity."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        subprocess.run(
            ["git", "branch", "-M", "main"],
            cwd=repo, capture_output=True, check=True,
        )
        main_sha = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
        (tmp_path / "run-config.json").write_text(json.dumps({
            "mode": "full",
            "interactive": False,
            "session_id": "bot-session",
            "git_range": "main..HEAD",
        }))
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "main..HEAD",
                "merge_base": "main",
                "head_ref": "HEAD",
            },
        }))
        log_dir = tmp_path / "telemetry-logs"

        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            result = run_pipeline(
                "--step", "1", "--output-dir", str(tmp_path), cwd=repo
            )

        assert result.returncode == 0
        log_path = (tmp_path / ".telemetry-log-path").read_text().strip()
        with open(log_path) as f:
            start = json.loads(f.readline())
        git_identity = start["pipeline"]["git"]
        assert git_identity["requested_range"] == "main..HEAD"
        assert git_identity["base_sha"] == main_sha
        assert git_identity["head_sha"] == main_sha

    def test_step_1_interactive_run_ignores_stale_context_git_identity(self, tmp_path):
        """Interactive reruns do not leak the prior run's preserved Git identity."""
        (tmp_path / "run-config.json").write_text(json.dumps({
            "mode": "full",
            "interactive": True,
        }))
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "stale-base..stale-head",
                "merge_base": "stale-base-sha",
                "head_sha": "stale-head-sha",
            },
        }))
        log_dir = tmp_path / "telemetry-logs"
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        current_head = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=repo, text=True
        ).strip()

        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            result = run_pipeline(
                "--step", "1", "--output-dir", str(tmp_path), cwd=str(repo)
            )

        assert result.returncode == 0
        log_path = (tmp_path / ".telemetry-log-path").read_text().strip()
        with open(log_path) as f:
            start = json.loads(f.readline())
        assert start["pipeline"]["git"] == {
            "requested_range": "",
            "base_sha": "",
            "head_sha": current_head,
        }
        manifest = json.loads(Path(log_path).with_suffix(".manifest.json").read_text())
        assert manifest["run"]["git"] == start["pipeline"]["git"]
        assert json.loads((tmp_path / "review-context.json").read_text()) == {
            "output": {"directory": str(tmp_path)},
        }

    def test_step_1_interactive_range_resolves_current_git_not_stale_context(self, tmp_path):
        """An explicit interactive range resolves Git even when stale context matches it."""
        git_range = "HEAD~1..HEAD~1"
        (tmp_path / "run-config.json").write_text(json.dumps({
            "mode": "full",
            "interactive": True,
            "git_range": git_range,
        }))
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": git_range,
                "merge_base": "stale-base-sha",
                "head_sha": "stale-head-sha",
            },
        }))
        log_dir = tmp_path / "telemetry-logs"
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        expected_sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD~1"], cwd=repo, text=True
        ).strip()

        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            result = run_pipeline(
                "--step", "1", "--output-dir", str(tmp_path), cwd=str(repo)
            )

        assert result.returncode == 0
        log_path = (tmp_path / ".telemetry-log-path").read_text().strip()
        with open(log_path) as f:
            start = json.loads(f.readline())
        assert start["pipeline"]["git"] == {
            "requested_range": git_range,
            "base_sha": expected_sha,
            "head_sha": expected_sha,
        }

    def test_incremental_context_uses_step_1_output_seed_for_baseline(
        self, tmp_path
    ):
        # _isolated_repo (autouse) already initialized tmp_path as a repo.
        baseline_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
        ).strip()
        (tmp_path / ".branch-review-baseline.json").write_text(json.dumps({
            "last_reviewed_sha": baseline_sha,
        }))
        log_dir = tmp_path / "telemetry-logs"

        with patch.dict(
            os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}
        ):
            step_1 = run_pipeline(
                "--step", "1", "--mode", "incremental",
                "--output-dir", str(tmp_path), cwd=tmp_path,
            )
            seeded_context = json.loads(
                (tmp_path / "review-context.json").read_text()
            )
            step_3 = run_pipeline(
                "--step", "3", "--output-dir", str(tmp_path), cwd=tmp_path,
            )

        assert step_1.returncode == 0
        assert seeded_context == {"output": {"directory": str(tmp_path)}}
        assert step_3.returncode == 0
        context = json.loads((tmp_path / "review-context.json").read_text())
        assert context["output"]["directory"] == str(tmp_path)
        assert context["git"]["merge_base"] == baseline_sha
        assert context["git"]["git_range"] == f"{baseline_sha}..HEAD"
        assert (tmp_path / ".branch-review-baseline.json").is_file()



class TestStep2Orchestration:
    """Step 2 main() runs review/workspace_setup.py and persists workspace state."""

    def test_step_2_completes_without_crash(self, tmp_path):
        """Step 2 should complete even when review/workspace_setup.py fails (no git repo)."""
        _init_git_repo(tmp_path)
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=str(tmp_path))
        r = run_pipeline("--step", "2", "--mode", "pr",
                       "--output-dir", str(tmp_path), cwd=str(tmp_path))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert 2 in state["completed_steps"]

    def test_step_2_stores_workspace_setup_result(self, tmp_path):
        """Step 2 should store workspace_setup_result in state."""
        _init_git_repo(tmp_path)
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=str(tmp_path))
        run_pipeline("--step", "2", "--mode", "pr",
                       "--output-dir", str(tmp_path), cwd=str(tmp_path))
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "workspace_setup_result" in state


class TestStep3Orchestration:
    """Step 3 main() runs review/context.py and hydrates state."""

    def test_step_3_runs_gather_context(self, tmp_path):
        """Step 3 should invoke review/context.py (may fail in test env, but state should update)."""
        # Seed step 1
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        # Run step 3
        r = run_pipeline("--step", "3", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        # State should have completed_steps including 3
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert 3 in state["completed_steps"]

    def test_step_3_hydrates_unfetched_issues_from_context(self, tmp_path):
        """When review-context.json has has_unfetched_issues, state should reflect it."""
        # Seed step 1
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        # Pre-write review-context.json as if review/context.py produced it
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
            "has_unfetched_issues": True,
            "linked_issues": ["WOOPLUG-1234"],
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        # Run step 3 — it should read the context and hydrate state
        r = run_pipeline("--step", "3", "--mode", "pr",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["resolved_params"]["has_unfetched_issues"] is True

    def test_step_3_without_context_still_succeeds(self, tmp_path):
        """Step 3 should not crash if review/context.py fails (no git repo)."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        r = run_pipeline("--step", "3", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        # Should succeed even without a git repo — subprocess failure is tolerated
        assert r.returncode == 0

    def test_step_3_allows_known_ecosystem_cache_refreshes_to_finish(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        """The context wrapper should allow both known host caches to refresh."""
        seen_timeouts = []

        def fake_run_subprocess(cmd, cwd=None, timeout=60):
            seen_timeouts.append(timeout)
            return "", True

        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess", fake_run_subprocess
        )
        mod._orchestrate_step(
            3,
            "full",
            {},
            {"resolved_params": {}},
            {},
            str(tmp_path),
        )

        assert seen_timeouts
        assert seen_timeouts[0] > 2 * 30 * 60

    def test_step_3_next_step_reflects_unfetched_issues(self, tmp_path):
        """When has_unfetched_issues is True, next step after 3 should be 4 (not 5)."""
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
            "has_unfetched_issues": True,
            "linked_issues": ["WOOPLUG-1234"],
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "3", "--mode", "pr",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        # Output should point to step 4, not step 5
        assert "Step 4" in r.stdout

    def test_step_3_detects_stale_dependency_roots_when_opted_in(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        # Commit a composer root, then change the lockfile in a second commit
        # so HEAD~1..HEAD contains a manifest change.
        (repo / "composer.json").write_text("{}\n")
        (repo / "composer.lock").write_text("{}\n")
        subprocess.run(["git", "add", "."], cwd=repo,
                       capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Add composer root"],
                       cwd=repo, capture_output=True, check=True)
        (repo / "composer.lock").write_text('{"changed": true}\n')
        subprocess.run(["git", "add", "composer.lock"], cwd=repo,
                       capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Bump lock"],
                       cwd=repo, capture_output=True, check=True)

        out_dir = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "full",
                     "--output-dir", str(out_dir),
                     "--git-range", "HEAD~1..HEAD", "--refresh-deps",
                     cwd=repo, env=hermetic_env())
        r = run_pipeline("--step", "3", "--mode", "full",
                         "--output-dir", str(out_dir),
                         cwd=repo, env=hermetic_env())

        assert r.returncode == 0
        state = json.loads((out_dir / "pipeline-state.json").read_text())
        detection = state.get("dependency_refresh")
        assert detection is not None
        managers = [s["manager"] for s in detection["signals"]]
        assert "composer" in managers

    def test_step_3_skips_detection_without_opt_in(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)

        out_dir = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "full",
                     "--output-dir", str(out_dir),
                     "--git-range", "HEAD~1..HEAD",
                     cwd=repo, env=hermetic_env())
        r = run_pipeline("--step", "3", "--mode", "full",
                         "--output-dir", str(out_dir),
                         cwd=repo, env=hermetic_env())

        assert r.returncode == 0
        state = json.loads((out_dir / "pipeline-state.json").read_text())
        assert "dependency_refresh" not in state

    def test_step_3_detection_outside_git_repo_degrades_honestly(self, tmp_path):
        # No git repo: detection cannot resolve a repo root. The step must
        # still succeed and record a failed detection, not a clean empty one.
        # GIT_CEILING_DIRECTORIES stops rev-parse walking up into a parent
        # repository that may contain tmp_path on some machines.
        env = hermetic_env(GIT_CEILING_DIRECTORIES=str(tmp_path.parent))
        out_dir = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "full",
                     "--output-dir", str(out_dir), "--refresh-deps",
                     cwd=tmp_path, env=env)
        r = run_pipeline("--step", "3", "--mode", "full",
                         "--output-dir", str(out_dir), cwd=tmp_path, env=env)

        assert r.returncode == 0
        state = json.loads((out_dir / "pipeline-state.json").read_text())
        detection = state.get("dependency_refresh")
        assert detection == {"signals": [], "detection_failed": True}


class TestStep8WaitingRouting:
    """Step 8 WAITING state should persist without advancing the pipeline."""

    def test_waiting_step_is_not_completed_or_routed_forward(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        mod.write_config(str(tmp_path), {"mode": "pr", "interactive": True})
        mod.write_state(str(tmp_path), {
            "completed_steps": [1, 3, 5, 6, 7],
            "resolved_params": {"git_range": "abc..HEAD"},
            "workspace": {"original_branch": None, "stash_ref": None},
            "agents": {
                "dispatched": ["security-reviewer"],
                "completed": [],
                "failed": [],
            },
            "verdict": None,
        })
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"},
        }))

        def fake_orchestrate(step, mode, config, state, context, output_dir):
            state["waiting_on_agents"] = {
                "running": ["security-reviewer"],
                "not_dispatched": [],
                "status_output": "security-reviewer RUNNING",
                "agent_timeout_seconds": 1200,
            }
            return context

        monkeypatch.setattr(mod, "_orchestrate_step", fake_orchestrate)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py",
            "--step", "8",
            "--output-dir", str(tmp_path),
        ])

        mod.main()

        output = capsys.readouterr().out
        saved = mod.read_state(str(tmp_path))
        assert 8 not in saved["completed_steps"]
        assert "first_waiting_at" in saved["waiting_on_agents"]
        assert "Next:" not in output
        assert "PIPELINE COMPLETE" not in output
        assert "PIPELINE WAITING" in output


class TestStep5Orchestration:
    """Step 5 main() runs review/plan_dispatch.py and stores output in state."""

    def _make_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        return repo

    def test_step_5_stores_dispatch_plan_summary(self, tmp_path):
        """Step 5 should store dispatch plan summary in state."""
        repo = self._make_repo(tmp_path)
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=str(repo))
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "5", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=str(repo))
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert 5 in state["completed_steps"]
        assert "dispatch_plan_summary" in state

    def test_step_5_writes_dependency_refresh_verification_when_opted_in(
        self, tmp_path
    ):
        repo = self._make_repo(tmp_path)
        run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(tmp_path), "--git-range", "HEAD~1..HEAD",
            "--refresh-deps", cwd=str(repo),
        )
        (tmp_path / "dependency-refresh.json").write_text(json.dumps({
            "status": "completed",
            "commands": [],
            "tracked_files_dirty": False,
        }))

        result = run_pipeline(
            "--step", "5", "--mode", "full",
            "--output-dir", str(tmp_path), cwd=str(repo),
        )

        assert result.returncode == 0
        verification = json.loads(
            (tmp_path / "dependency-refresh-verification.json").read_text()
        )
        assert verification["report_present"] is True
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["dependency_refresh_verification"] == verification

    def test_step_5_records_skip_without_running_refresh_verification(
        self, tmp_path
    ):
        repo = self._make_repo(tmp_path)
        run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(tmp_path), "--git-range", "HEAD~1..HEAD",
            "--refresh-deps", cwd=str(repo),
        )
        state_path = tmp_path / "pipeline-state.json"
        state = json.loads(state_path.read_text())
        state["dependency_refresh"] = {
            "signals": [{"manager": "npm"}],
            "skipped_reason": "dirty_worktree",
            "dirty_files": ["tracked.txt"],
        }
        state_path.write_text(json.dumps(state))

        result = run_pipeline(
            "--step", "5", "--mode", "full",
            "--output-dir", str(tmp_path), cwd=str(repo),
        )

        assert result.returncode == 0
        skipped = json.loads(
            (tmp_path / "dependency-refresh-verification.json").read_text()
        )
        assert skipped == {
            "dirty_files": ["tracked.txt"],
            "skipped": True,
            "skipped_reason": "dirty_worktree",
        }
        state = json.loads(state_path.read_text())
        assert state["dependency_refresh_verification"] == skipped
        assert "verified clean" not in result.stdout.lower()

    def test_step_5_skips_dependency_refresh_verification_without_opt_in(
        self, tmp_path
    ):
        repo = self._make_repo(tmp_path)
        run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(tmp_path), "--git-range", "HEAD~1..HEAD",
            "--no-refresh-deps", cwd=str(repo),
        )
        (tmp_path / "dependency-refresh.json").write_text(json.dumps({
            "status": "completed",
            "commands": [],
            "tracked_files_dirty": False,
        }))

        result = run_pipeline(
            "--step", "5", "--mode", "full",
            "--output-dir", str(tmp_path), cwd=str(repo),
        )

        assert result.returncode == 0
        assert not (tmp_path / "dependency-refresh-verification.json").exists()
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "dependency_refresh_verification" not in state

    def test_step_5_dependency_refresh_verification_warns_before_dispatch(
        self, mod, tmp_path
    ):
        guidance = mod._step_5_dispatch_plan(
            "full",
            {
                "dependency_refresh_verification": {
                    "report_present": True,
                    "commands_allowed": True,
                    "disallowed_commands": [],
                    "tracked_files_dirty": True,
                    "dirty_files": ["src/changed.py"],
                    "verification_failed": False,
                }
            },
            {},
            {"refresh_dependencies": True},
            str(tmp_path),
        )

        situation = "\n".join(guidance["situation"])
        normalized = situation.lower()
        assert guidance["situation"][0].startswith("⚠️")
        assert "`src/changed.py`" in situation
        assert "inspect each listed change" in normalized
        assert "preserve or back up intentional edits" in normalized
        assert "git checkout -- <path>" in situation
        assert "only after confirming" in normalized
        assert "caused solely by the dependency refresh" in normalized
        assert "dependency-refresh.json" in situation
        assert "BEFORE dispatch" in situation

    def test_step_5_dependency_refresh_verification_reports_combined_evidence(
        self, mod, tmp_path
    ):
        guidance = mod._step_5_dispatch_plan(
            "full",
            {
                "dependency_refresh_verification": {
                    "report_present": True,
                    "commands_allowed": False,
                    "disallowed_commands": ["npm install"],
                    "tracked_files_dirty": True,
                    "dirty_files": ["src/changed.py"],
                    "verification_failed": True,
                }
            },
            {},
            {"refresh_dependencies": True},
            str(tmp_path),
        )

        situation = "\n".join(guidance["situation"]).lower()
        assert sum(
            line.startswith("⚠️") for line in guidance["situation"]
        ) == 2
        assert "modified tracked files" in situation
        assert "reported command outside the allowlist" in situation
        assert "verification itself failed" in situation

    @pytest.mark.parametrize(
        "verification",
        [
            {
                "report_present": True,
                "commands_allowed": False,
                "disallowed_commands": ["npm install"],
                "tracked_files_dirty": False,
                "dirty_files": [],
                "verification_failed": False,
            },
            {
                "report_present": True,
                "commands_allowed": True,
                "disallowed_commands": [],
                "tracked_files_dirty": False,
                "dirty_files": [],
                "verification_failed": True,
            },
        ],
    )
    def test_step_5_dependency_refresh_verification_degradation_is_honest(
        self, mod, tmp_path, verification
    ):
        guidance = mod._step_5_dispatch_plan(
            "full",
            {"dependency_refresh_verification": verification},
            {},
            {"refresh_dependencies": True},
            str(tmp_path),
        )

        situation = "\n".join(guidance["situation"])
        assert guidance["situation"][0].startswith("⚠️")
        assert "could not be verified clean" in situation
        assert "proceeding is allowed" in situation
        assert "manifest records" in situation

    def test_step_5_preserves_initial_plan_before_orchestrator_adjustment(
        self, tmp_path
    ):
        """Step 5 keeps the deterministic plan unchanged for measurement."""
        repo = self._make_repo(tmp_path)
        run_pipeline("--step", "1", "--mode", "full",
                  "--output-dir", str(tmp_path), cwd=str(repo))
        ctx = {
            "git": {
                "git_range": "HEAD~1..HEAD",
                "changed_files": ["plugins/pirategoat-tools/scripts/review/pipeline.py"],
                "commit_count": 1,
            },
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))

        result = run_pipeline(
            "--step", "5", "--mode", "full", "--output-dir", str(tmp_path),
            cwd=str(repo),
        )

        assert result.returncode == 0
        initial_path = tmp_path / "dispatch-plan.initial.json"
        final_path = tmp_path / "dispatch-plan.json"
        initial = json.loads(initial_path.read_text())
        final = json.loads(final_path.read_text())
        assert initial == final

        final["agents"][0]["status"] = "SKIPPED_OVERRIDE"
        final["agents"][0]["override_reason"] = "main orchestrator adjustment"
        final_path.write_text(json.dumps(final))

        assert json.loads(initial_path.read_text()) == initial
        assert json.loads(initial_path.read_text()) != json.loads(final_path.read_text())

    def test_initial_plan_write_failure_is_fail_open(self, mod, tmp_path):
        """Measurement failure neither alters the final plan nor raises."""
        plan = {
            "agents": [
                {
                    "name": "code-reviewer",
                    "status": "DISPATCH",
                    "reason": "always",
                }
            ]
        }
        final_path = tmp_path / "dispatch-plan.json"
        initial_path = tmp_path / "dispatch-plan.initial.json"
        final_path.write_text(json.dumps(plan))
        initial_path.write_text('{"stale": true}')

        with patch.object(mod.os, "replace", side_effect=OSError("nope")):
            mod._preserve_initial_dispatch_plan(str(tmp_path), plan)

        assert json.loads(final_path.read_text()) == plan
        assert not initial_path.exists()

    def test_step_5_real_planner_projects_persisted_codex_host(self, tmp_path):
        repo = self._make_repo(tmp_path)
        result = run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(tmp_path), "--host", "codex",
            cwd=str(repo),
        )
        assert result.returncode == 0

        prompt_path = repo / ".ai" / "agents" / "review" / "expert.md"
        prompt_path.parent.mkdir(parents=True)
        prompt_path.write_text("Review the domain behavior.")
        context = {
            "git": {
                "git_range": "HEAD..HEAD",
                "changed_files": ["src/x.php"],
                "changed_files_csv": "src/x.php",
            },
            "review_config": {
                "rules": [],
                "reviewers": [{
                    "id": "domain-expert",
                    "label": "Domain Expert",
                    "ref": ".ai/agents/review/expert.md",
                    "resolved_ref": str(prompt_path),
                    "applies_to": {"paths": ["**/*.php"]},
                    "channel": "blocking",
                    "execution": "inline",
                    "model": "opus",
                }],
                "untrusted": [],
            },
        }
        (tmp_path / "review-context.json").write_text(json.dumps(context))

        result = run_pipeline(
            "--step", "5", "--mode", "full",
            "--output-dir", str(tmp_path), cwd=str(repo),
        )

        assert result.returncode == 0
        plan = json.loads((tmp_path / "dispatch-plan.json").read_text())
        entry = next(
            agent for agent in plan["agents"]
            if agent.get("adapter") == "repo-reviewer-adapter"
        )
        assert entry["model"] == "inherit"
        assert entry["declared_model"] == "opus"

    def test_failed_planner_retry_preserves_existing_baseline_and_adjusted_plan(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        """A failed retry cannot reclassify an adjusted plan as deterministic."""
        initial = {
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH", "reason": "always"}
            ]
        }
        final = {
            "agents": [
                {
                    "name": "code-reviewer",
                    "status": "SKIPPED_OVERRIDE",
                    "reason": "always",
                    "override_reason": "main orchestrator adjustment",
                }
            ]
        }
        initial_path = tmp_path / "dispatch-plan.initial.json"
        final_path = tmp_path / "dispatch-plan.json"
        initial_path.write_text(json.dumps(initial))
        final_path.write_text(json.dumps(final))
        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess", lambda *args, **kwargs: ("", False)
        )

        mod._orchestrate_step(
            5,
            "full",
            {},
            {"resolved_params": {"git_range": "base..head"}},
            {"git": {"git_range": "base..head"}},
            str(tmp_path),
        )

        assert json.loads(initial_path.read_text()) == initial
        assert json.loads(final_path.read_text()) == final

    def test_failed_planner_without_baseline_does_not_fabricate_one(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        """A failed planner may reuse a final artifact but never invents a baseline."""
        final = {
            "agents": [
                {
                    "name": "code-reviewer",
                    "status": "SKIPPED_OVERRIDE",
                    "reason": "always",
                    "override_reason": "main orchestrator adjustment",
                }
            ]
        }
        final_path = tmp_path / "dispatch-plan.json"
        final_path.write_text(json.dumps(final))
        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess", lambda *args, **kwargs: ("", False)
        )

        mod._orchestrate_step(
            5,
            "full",
            {},
            {"resolved_params": {"git_range": "base..head"}},
            {"git": {"git_range": "base..head"}},
            str(tmp_path),
        )

        assert json.loads(final_path.read_text()) == final
        assert not (tmp_path / "dispatch-plan.initial.json").exists()

    def test_successful_planner_with_invalid_plan_shape_surfaces_value_error(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        """Subprocess success cannot hide a malformed planner artifact."""
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(["not", "a", "plan"]))
        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess", lambda *args, **kwargs: ("", True)
        )
        state = {"resolved_params": {"git_range": "base..head"}}

        with pytest.raises(ValueError, match="must be a JSON object"):
            mod._orchestrate_step(
                5,
                "full",
                {},
                state,
                {"git": {"git_range": "base..head"}},
                str(tmp_path),
            )

        assert not (tmp_path / "dispatch-plan.initial.json").exists()

    def test_step_1_clears_stale_initial_dispatch_plan(self, mod, tmp_path):
        """A prior run's planner baseline cannot leak into the next run."""
        initial_path = tmp_path / "dispatch-plan.initial.json"
        initial_path.write_text('{"stale": true}')

        mod.clean_stale_artifacts(str(tmp_path))

        assert not initial_path.exists()

    def test_step_1_clears_stale_dependency_refresh_verification(
        self, mod, tmp_path
    ):
        verification_path = tmp_path / "dependency-refresh-verification.json"
        verification_path.write_text('{"report_present": true}')

        mod.clean_stale_artifacts(str(tmp_path))

        assert not verification_path.exists()


class TestStep6Orchestration:
    """Step 6 main() reads dispatch-plan.json and populates dispatched_agents."""

    def test_step_6_populates_dispatched_agents(self, tmp_path):
        """Step 6 should read dispatch-plan.json and populate state.dispatched_agents."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        plan = {
            "agents": [
                {"name": "code-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "always"},
                {"name": "go-tests-reviewer", "domain": "go-tests", "status": "SKIPPED", "reason": "no files"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "6", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        names = [a["name"] for a in state.get("dispatched_agents", [])]
        assert "code-reviewer" in names
        assert "security-reviewer" in names
        assert "go-tests-reviewer" not in names

    def test_step_6_output_contains_bootstrap_calls(self, tmp_path):
        """Step 6 output should contain concrete bootstrap.py calls."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        plan = {
            "agents": [
                {"name": "code-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "6", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert "bootstrap.py" in r.stdout
        assert "code-reviewer" in r.stdout
        assert "abc..HEAD" in r.stdout

    def test_step_6_invalid_hand_edited_status_surfaces_value_error(
        self, mod, tmp_path
    ):
        plan = {
            "agents": [
                {
                    "name": "security-reviewer",
                    "status": "DISPATCHED",
                },
            ],
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        with pytest.raises(ValueError) as exc_info:
            mod._orchestrate_step(
                6,
                "full",
                {},
                {},
                {},
                str(tmp_path),
            )

        message = str(exc_info.value)
        assert "security-reviewer" in message
        assert repr("DISPATCHED") in message


class TestStep7Orchestration:
    """Step 7 main() writes .branch-review-baseline.json."""

    def test_step_7_writes_baseline_file(self, tmp_path):
        """Step 7 should create .branch-review-baseline.json."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "7", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
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
        from helpers.graders import grade_review_baseline
        run_pipeline("--step", "1", "--mode", "incremental",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        run_pipeline("--step", "7", "--mode", "incremental",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        baseline_path = tmp_path / ".branch-review-baseline.json"
        result = grade_review_baseline(str(baseline_path))
        assert result.passed, f"Baseline grading failed: {result.failures}"


class TestStep8Orchestration:
    """Step 8 main() reads change-purpose.md and agent completion status."""

    def test_step_1_records_that_reviewer_markdown_has_not_run(self, tmp_path):
        result = run_pipeline(
            "--step", "1", "--mode", "full", "--output-dir", str(tmp_path),
            cwd=tmp_path,
        )

        assert result.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["reviewer_markdown"] == {
            "ran": False,
            "written": 0,
            "expected": 0,
            "status": "not_run",
        }

    def test_step_8_reads_change_purpose(self, tmp_path):
        """Step 8 should read change-purpose.md into state."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        (tmp_path / "change-purpose.md").write_text("Adds retry logic to payment gateway.")
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "8", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "retry logic" in state.get("change_purpose", "").lower()

    def test_step_8_stores_review_file_paths(self, tmp_path):
        """Step 8 should store paths to completed review files in state."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        plan = {
            "agents": [
                {"name": "code-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "always"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        # Simulate code-reviewer finished, security-reviewer not
        (tmp_path / "code-review.json").write_text('{"verdict": "approve", "issues": []}')
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "8", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        review_files = state.get("agents", {}).get("review_files", [])
        assert any("code-review.json" in f for f in review_files)

    def test_step_8_materializes_every_settled_reviewer_json_at_readiness_gate(
        self, mod, tmp_path, monkeypatch
    ):
        for reviewer in ("code", "security"):
            (tmp_path / f"{reviewer}-review.json").write_text(
                json.dumps(_review_json(reviewer))
            )

        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.md").write_text("# Context\n")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )

        state = {"resolved_params": {}}
        result = mod._orchestrate_step(
            8,
            "full",
            {},
            state,
            {},
            str(tmp_path),
        )

        assert result == {}
        assert (tmp_path / "code-review.md").is_file()
        assert (tmp_path / "security-review.md").is_file()
        assert state["reviewer_markdown"] == {
            "ran": True,
            "written": 2,
            "expected": 2,
            "status": "complete",
        }

    def test_step_8_materializes_when_status_checker_crashes(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "security-review.json").write_text(
            json.dumps(_review_json("security"))
        )
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("checker crashed")
            ),
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.md").write_text("# Context\n")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        result = mod._orchestrate_step(
            8, "full", {}, state, {}, str(tmp_path)
        )

        assert result == {}
        assert (tmp_path / "security-review.md").is_file()
        assert state["reviewer_markdown"]["status"] == "complete"

    def test_step_8_uses_post_render_snapshot_when_json_arrives_during_materialization(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "security-review.json").write_text(
            json.dumps(_review_json("security"))
        )
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("checker crashed")
            ),
        )
        original_materialize = mod._orchestrate_step_8.__globals__[
            "_materialize_reviewer_markdown"
        ]

        def publish_then_materialize(output_dir, output_builder_path):
            (tmp_path / "code-review.json").write_text(
                json.dumps(_review_json("code"))
            )
            return original_materialize(output_dir, output_builder_path)

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_reviewer_markdown",
            publish_then_materialize,
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.md").write_text("# Context\n")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        result = mod._orchestrate_step(
            8, "full", {}, state, {}, str(tmp_path)
        )

        assert result == {}
        assert (tmp_path / "code-review.md").is_file()
        assert (tmp_path / "security-review.md").is_file()
        assert state["reviewer_markdown"] == {
            "ran": True,
            "written": 2,
            "expected": 2,
            "status": "complete",
        }

    def test_step_8_compares_materialized_path_identities_not_only_counts(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "security-review.json").write_text(
            json.dumps(_review_json("security"))
        )
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        unrelated_markdown = tmp_path / "code-review.md"
        unrelated_markdown.write_text("# Different reviewer\n")
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_reviewer_markdown",
            lambda *_args, **_kwargs: [str(unrelated_markdown)],
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.md").write_text("# Context\n")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        result = mod._orchestrate_step(
            8, "full", {}, state, {}, str(tmp_path)
        )

        assert result == {}
        assert state["reviewer_markdown"] == {
            "ran": True,
            "written": 1,
            "expected": 1,
            "status": "partial",
        }
        assert state["degradation"]["reviewer_markdown_incomplete"] is True

    def test_step_8_records_materialization_failure_without_aborting(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        (tmp_path / "security-review.json").write_text(
            json.dumps(_review_json("security"))
        )
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_reviewer_markdown",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("renderer crashed")
            ),
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.md").write_text("# Context\n")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        result = mod._orchestrate_step(
            8, "full", {}, state, {}, str(tmp_path)
        )

        assert result == {}
        assert state["reviewer_markdown"] == {
            "ran": True,
            "written": 0,
            "expected": 1,
            "status": "failed",
        }
        assert state["degradation"]["reviewer_markdown_incomplete"] is True
        assert "reviewer markdown materialization failed: renderer crashed" in (
            capsys.readouterr().err
        )

    def test_step_8_records_skipped_json_as_partial_materialization(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "security-review.json").write_text("{}")
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.md").write_text("# Context\n")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        result = mod._orchestrate_step(
            8, "full", {}, state, {}, str(tmp_path)
        )

        assert result == {}
        assert state["reviewer_markdown"] == {
            "ran": True,
            "written": 0,
            "expected": 1,
            "status": "partial",
        }
        assert state["degradation"]["reviewer_markdown_incomplete"] is True

    def test_step_8_reconciliation_failure_happens_after_reviewer_markdown(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "security-review.json").write_text(
            json.dumps(_review_json("security"))
        )
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("reconciliation crashed")
            ),
        )

        with pytest.raises(RuntimeError, match="reconciliation crashed"):
            mod._orchestrate_step(
                8,
                "full",
                {},
                {"resolved_params": {}},
                {},
                str(tmp_path),
            )

        assert (tmp_path / "security-review.md").is_file()

    def test_step_8_invalid_hand_edited_status_surfaces_value_error(
        self, mod, tmp_path, monkeypatch
    ):
        plan = {
            "agents": [
                {
                    "name": "security-reviewer",
                    "status": None,
                },
            ],
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=1, stdout="", stderr="invalid plan"
            ),
        )

        with pytest.raises(ValueError) as exc_info:
            mod._orchestrate_step(
                8,
                "full",
                {},
                {"resolved_params": {}},
                {},
                str(tmp_path),
            )

        message = str(exc_info.value)
        assert "security-reviewer" in message
        assert repr(None) in message


class TestStep9Orchestration:
    """Step 9 main() loads inline coverage gaps from reconciliation context."""

    def test_step_9_loads_inline_coverage_gaps(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        recon = {
            "inline_coverage": {
                "agents_reporting": 2,
                "files_inline": {"src/a.php": ["code-reviewer"]},
                "files_never_inline": {
                    "src/starved.php": ["code-reviewer", "security-reviewer"],
                },
            },
        }
        (tmp_path / "reconciliation-context.json").write_text(json.dumps(recon))
        r = run_pipeline("--step", "9", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state.get("inline_coverage_gaps") == {
            "src/starved.php": ["code-reviewer", "security-reviewer"],
        }
        # The briefing itself must carry the warning.
        assert "src/starved.php" in r.stdout

    def test_step_9_tolerates_missing_reconciliation_context(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        r = run_pipeline("--step", "9", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state.get("inline_coverage_gaps") == {}


class TestStep10Orchestration:
    """Step 10 main() reads the reconciliation verdict and records the
    quick-mode critic skip decision.

    This class exists because the step-10 orchestration branch had NO
    execution coverage: the pipeline module split extracted it into
    _orchestrate_step_10 while its body still referenced the `step`
    parameter the helper never receives, and the whole integration suite
    stayed green over a live NameError. Every test here runs the real
    step, so the branch cannot silently stop executing again.
    """

    def _findings(self, tmp_path, verdict):
        (tmp_path / "review-findings.json").write_text(
            json.dumps({"verdict": verdict, "issues": []})
        )

    def test_step_10_records_the_reconciliation_verdict(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        self._findings(tmp_path, "block")
        r = run_pipeline("--step", "10", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state.get("reconciliation_verdict") == "block"
        # Not quick mode — the critic always runs, so no skip decision.
        assert "10" not in state.get("step_decisions", {})

    def test_step_10_quick_mode_records_critic_skip(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full", "--quick",
                  "--output-dir", str(tmp_path), cwd=tmp_path)
        self._findings(tmp_path, "approve")
        r = run_pipeline("--step", "10", "--mode", "full", "--quick",
                      "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        decision = state.get("step_decisions", {}).get("10")
        assert decision is not None, "quick-mode critic skip was not recorded"
        assert decision["critic_skipped"] is True
        assert "approve" in decision["reason"]

    def test_step_10_quick_mode_keeps_critic_for_blocking_verdict(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full", "--quick",
                  "--output-dir", str(tmp_path), cwd=tmp_path)
        self._findings(tmp_path, "block")
        r = run_pipeline("--step", "10", "--mode", "full", "--quick",
                      "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "10" not in state.get("step_decisions", {})

    def test_step_10_clears_a_stale_skip_decision_on_rerun(self, tmp_path):
        """A rerun after the verdict escalates must drop the earlier skip.

        This is the exact line the split broke: the decision key is popped
        before it is conditionally rewritten, so a stale `critic_skipped`
        cannot survive into a run whose reconciliation now blocks.
        """
        run_pipeline("--step", "1", "--mode", "full", "--quick",
                  "--output-dir", str(tmp_path), cwd=tmp_path)
        self._findings(tmp_path, "approve")
        run_pipeline("--step", "10", "--mode", "full", "--quick",
                  "--output-dir", str(tmp_path), cwd=tmp_path)
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["step_decisions"]["10"]["critic_skipped"] is True

        self._findings(tmp_path, "block")
        r = run_pipeline("--step", "10", "--mode", "full", "--quick",
                      "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "10" not in state.get("step_decisions", {}), (
            "stale critic-skip decision survived a verdict escalation"
        )

    def test_step_10_tolerates_missing_reconciliation_findings(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        r = run_pipeline("--step", "10", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state.get("reconciliation_verdict", "") == ""

    def test_step_10_tolerates_malformed_reconciliation_findings(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        (tmp_path / "review-findings.json").write_text("{not json")
        r = run_pipeline("--step", "10", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state.get("reconciliation_verdict") == ""


class TestStep11Orchestration:
    """Step 11 main() reads review-verdict.json and writes pipeline-result.json."""

    def test_step_11_writes_pipeline_result(self, tmp_path):
        """Step 11 should write pipeline-result.json."""
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        (tmp_path / "review-verdict.json").write_text('{"verdict": "REQUEST_CHANGES"}')
        (tmp_path / "review-report.md").write_text("# Review Report\nFindings here.")
        (tmp_path / "review-findings.json").write_text('{"verdict": "COMMENT", "issues": []}')
        r = run_pipeline("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        result_path = tmp_path / "pipeline-result.json"
        assert result_path.is_file(), "pipeline-result.json was not created"
        result = json.loads(result_path.read_text())
        assert result["verdict"] == "REQUEST_CHANGES"
        assert result["status"] in ("success", "degraded")
        assert "report_path" in result

    def test_step_11_updates_findings_verdict(self, tmp_path):
        """Step 11 should update review-findings.json verdict to match review-verdict.json (rule 23)."""
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        (tmp_path / "review-verdict.json").write_text('{"verdict": "REQUEST_CHANGES"}')
        (tmp_path / "review-report.md").write_text("# Review")
        (tmp_path / "review-findings.json").write_text('{"verdict": "COMMENT", "issues": []}')
        run_pipeline("--step", "11", "--mode", "pr",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        findings = json.loads((tmp_path / "review-findings.json").read_text())
        assert findings["verdict"] == "REQUEST_CHANGES"

    def test_step_11_handles_missing_verdict(self, tmp_path):
        """Step 11 should handle missing review-verdict.json gracefully."""
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        r = run_pipeline("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        result_path = tmp_path / "pipeline-result.json"
        assert result_path.is_file()
        result = json.loads(result_path.read_text())
        assert result["status"] in ("degraded", "failed")

    def test_step_11_degrades_when_findings_missing(self, tmp_path):
        """Step 11 should report degraded when review-findings.json is missing (partial run)."""
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        # Verdict and report exist, but findings do not (reconciliation failed)
        (tmp_path / "review-verdict.json").write_text('{"verdict": "COMMENT"}')
        (tmp_path / "review-report.md").write_text("# Review\nReport here.")
        r = run_pipeline("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert any("review-findings.json" in n for n in result["degradation_notes"])


class TestTelemetryFinalize:
    """Telemetry finalize is called at the last active step."""

    def test_last_step_finalizes_telemetry(self, tmp_path):
        """The last active step should finalize telemetry and its manifest."""
        log_dir = tmp_path / "telemetry-logs"
        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            run_pipeline("--step", "1", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
            # Step 11 is the last active step for non-interactive full mode
            # (step 12 needs workspace + interactive)
            run_pipeline("--step", "11", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        marker = tmp_path / ".telemetry-log-path"
        if marker.is_file():
            log_path = marker.read_text().strip()
            with open(log_path) as f:
                lines = f.readlines()
            events = [json.loads(l)["event"] for l in lines]
            assert "pipeline_end" in events, f"Expected pipeline_end event, got: {events}"
            manifest_path = Path(log_path).with_suffix(".manifest.json")
            manifest = json.loads(manifest_path.read_text())
            assert manifest["status"] == "complete"


class TestStep8AgentPrompt:
    """Step 8 should emit a complete reconciliator Agent tool prompt (rule 15)."""

    def test_reconciliator_prompt_has_concrete_values(self, mod, tmp_path):
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": ["code-reviewer", "security-reviewer"],
                "completed": ["code-reviewer", "security-reviewer"],
                "failed": [],
            },
            "change_purpose": "Adds retry logic.",
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py,b.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "reconciliation-context.md" in text  # pre-gathered Markdown context file
        assert str(tmp_path) in text  # concrete output directory


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

    @pytest.fixture
    def repo(self, tmp_path):
        """A throwaway repository for the sequence to stand in.

        The pipeline measures the repo it is running in — step 3 snapshots
        its git status and step 11 compares, and sweeps probe residue,
        against that snapshot — so a sequence spawned without a cwd runs
        that machinery over whoever's checkout pytest happened to start in.
        The sequence is repo-coupled in its own right too: step 1 resolves
        git identity and step 7 writes a review baseline. The output
        directory stays outside this repo so the run's own artifacts never
        register as worktree changes.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        return repo

    def test_full_sequence_produces_pipeline_result(self, tmp_path, repo):
        """Run steps 1,3,5,6,7,8,11 in order — pipeline-result.json should exist."""
        od = str(tmp_path / "out")
        os.makedirs(od, exist_ok=True)
        # Step 1: seed
        r = run_pipeline("--step", "1", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0

        # Pre-write context as if review/context.py succeeded
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "changed_files_csv": "a.py",
                    "commit_count": 1, "base_ref": "main"},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (Path(od) / "review-context.json").write_text(json.dumps(ctx))

        # Step 3: gather context (reads the pre-written file)
        r = run_pipeline("--step", "3", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0

        # Step 5: dispatch plan (may fail without git, but should not crash)
        r = run_pipeline("--step", "5", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0

        # Pre-write dispatch plan as if planner succeeded
        plan = {"agents": [{"name": "code-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"}], "git_range": "abc..HEAD"}
        (Path(od) / "dispatch-plan.json").write_text(json.dumps(plan))

        # Step 6: dispatch agents
        r = run_pipeline("--step", "6", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0

        # Step 7: save baseline
        r = run_pipeline("--step", "7", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0
        assert (Path(od) / ".branch-review-baseline.json").is_file()

        # Step 8: reconcile (no review files exist — that's OK)
        r = run_pipeline("--step", "8", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0

        # Pre-write verdict, report, and findings as if steps 8-10 ran
        (Path(od) / "review-verdict.json").write_text('{"verdict": "APPROVE"}')
        (Path(od) / "review-report.md").write_text("# Review\nAll clear.")
        (Path(od) / "review-findings.json").write_text('{"verdict": "APPROVE", "issues": []}')

        # Step 11: present results
        r = run_pipeline("--step", "11", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0
        assert (Path(od) / "pipeline-result.json").is_file()
        result = json.loads((Path(od) / "pipeline-result.json").read_text())
        assert result["verdict"] == "APPROVE"
        assert result["status"] == "success"
        assert result["review_baseline_saved"] is True


# =============================================================================
# Quick Mode Dispatch Tests
# =============================================================================

# Files that cover enough domains to trigger most agents
_QUICK_MODE_TEST_FILES = [
    "src/Controller.php",
    "src/components/Modal.tsx",
    "src/hooks/useData.ts",
    "tests/ControllerTest.php",
    "src/styles/modal.scss",
    "e2e/checkout.spec.ts",
    ".github/workflows/ci.yml",
    "Dockerfile",
    "src/utils/auth.go",
    "src/utils/auth_test.go",
]

_QUICK_MODE_BLOCKED_AGENTS = frozenset([
    "wp-architecture-reviewer",
    "history-insights-reviewer",
    "data-flow-privacy-reviewer",
    "concurrency-reviewer",
    "reliability-reviewer",
])


def _init_main_repo(path):
    """A git repo with a `main` branch at HEAD.

    build_dispatch_plan's triage calls plan_dispatch.get_diff_text() /
    get_repository_identity() via `git diff`/`git rev-parse`, with no cwd
    override — they always read the ambient process CWD, not a subprocess
    we control. Left unpatched, `git_range="main..HEAD"` behaves
    differently depending on which repo pytest happens to be invoked from:
    inside this repo the pathspec resolves to an empty diff (low-signal,
    quick mode skips); from a foreign CWD `git diff` fails outright
    ("not a git repository"), which the triage treats as an unreadable
    scan and dispatches conservatively instead of skipping. Pointing CWD at
    a throwaway repo with a `main` branch at HEAD makes `main..HEAD`
    resolve to an empty diff everywhere, so the test stops depending on
    which repo happens to be running it.
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "checkout", "-q", "-B", "main"], cwd=path, check=True)
    (path / "README.md").write_text("init")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        cwd=path, check=True,
    )
    return path


class TestQuickModeDispatch:
    """Quick mode excludes low-signal agents from dispatch."""

    @pytest.fixture(scope="class")
    def registry(self):
        return load_registry()

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        """build_dispatch_plan calls straight into plan_dispatch's git
        helpers (no subprocess seam to pass cwd through), so isolation here
        means chdir'ing the test process itself — see _init_main_repo."""
        _init_main_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

    def test_quick_mode_excludes_blocklisted_agents_without_signals(self, registry):
        """quick=True skips blocklisted agents when no triage keywords match."""
        # Use files that don't trigger keyword matches for blocklisted agents
        # (no "hook", "filter", "concurrent", "privacy", "deploy", etc.)
        neutral_files = [
            "src/Controller.php",
            "src/components/Modal.tsx",
            "tests/ControllerTest.php",
            "src/utils/helpers.go",
        ]
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-quick",
            changed_files=neutral_files,
            registry=registry,
            quick=True,
            commit_messages="fix button alignment in modal",
            # This test pins quick-mode relabeling of conservative dispatch.
            diffstat={
                "added": 200,
                "removed": 40,
                "deleted_files": [],
                "renamed_files": [],
                "file_stats": {f: {"added": 50, "removed": 10} for f in neutral_files},
            },
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        for agent_name in _QUICK_MODE_BLOCKED_AGENTS:
            if agent_name not in dispatch_map:
                continue  # agent may have no files in domain
            assert dispatch_map[agent_name]["status"] == "SKIPPED_QUICK_MODE", (
                f"Expected SKIPPED_QUICK_MODE for '{agent_name}', "
                f"got '{dispatch_map[agent_name]['status']}'"
            )

    def test_normal_mode_does_not_exclude(self, registry):
        """quick=False (default) does not produce SKIPPED_QUICK_MODE status."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-normal",
            changed_files=_QUICK_MODE_TEST_FILES,
            registry=registry,
            quick=False,
        )
        for entry in plan["agents"]:
            assert entry["status"] != "SKIPPED_QUICK_MODE", (
                f"Agent '{entry['name']}' should not have SKIPPED_QUICK_MODE "
                f"when quick=False"
            )

    def test_quick_mode_non_blocked_agents_triage_normally(self, registry):
        """quick=True does not affect non-blocked agents — code-reviewer still dispatches."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-quick",
            changed_files=_QUICK_MODE_TEST_FILES,
            registry=registry,
            quick=True,
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        assert dispatch_map["code-reviewer"]["status"] == "DISPATCH", (
            "code-reviewer should still DISPATCH in quick mode"
        )

    def test_quick_mode_honors_keyword_triage(self, registry):
        """Blocklisted agents with keyword matches should still dispatch in quick mode."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-quick-keywords",
            changed_files=_QUICK_MODE_TEST_FILES,
            registry=registry,
            quick=True,
            # Commit messages with keywords that match blocklisted agents
            commit_messages="fix concurrent race condition in payment hook filter",
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        # concurrency-reviewer should dispatch (keyword "concurrent" matched)
        assert dispatch_map["concurrency-reviewer"]["status"] == "DISPATCH", (
            "concurrency-reviewer should DISPATCH when keywords match, "
            f"got {dispatch_map['concurrency-reviewer']['status']}"
        )
        # wp-architecture-reviewer should dispatch (keyword "hook"/"filter" matched)
        assert dispatch_map["wp-architecture-reviewer"]["status"] == "DISPATCH", (
            "wp-architecture-reviewer should DISPATCH when keywords match, "
            f"got {dispatch_map['wp-architecture-reviewer']['status']}"
        )


class TestStep8ReviewFileStems:
    """Step 8's completion check must map agent names to review files by
    terminal-suffix derivation only — a blanket replace looked for
    repo-api-review-v2-review.json and silently excluded valid output."""

    def test_mid_string_reviewer_name_counts_as_completed(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        plan = {"agents": [{
            "name": "repo-api-reviewer-v2-reviewer",
            "status": "DISPATCH",
            "reason": "repo reviewer applicable",
        }]}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        (tmp_path / "repo-api-reviewer-v2-review.json").write_text(
            json.dumps({"reviewer": "repo-api-reviewer-v2", "issues": []})
        )
        fake_done = subprocess.CompletedProcess(
            [], returncode=0, stdout="", stderr=""
        )
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: fake_done)

        def fake_run_subprocess(cmd, timeout=None, **kwargs):
            (tmp_path / "reconciliation-context.md").write_text("ctx")
            return ("", True)

        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess", fake_run_subprocess
        )
        state = {"resolved_params": {"git_range": "base..head"}}

        mod._orchestrate_step(
            8, "full", {}, state,
            {"git": {"git_range": "base..head"}}, str(tmp_path),
        )

        assert state["agents"]["completed"] == ["repo-api-reviewer-v2-reviewer"]

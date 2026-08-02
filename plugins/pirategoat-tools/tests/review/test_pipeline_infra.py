"""Tests for review/pipeline.py — infrastructure: step sequence, routing, state, CLI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/

sys.path.insert(0, str(TESTS_DIR))
from conftest import PIPELINE_SCRIPT_PATH as SCRIPT_PATH, PIPELINE_TOTAL_STEPS as TOTAL_STEPS


@pytest.fixture(scope="module")
def mod(pipeline_mod):
    return pipeline_mod


class TestStepSequence:
    """Universal step sequence is defined correctly."""

    def test_has_12_steps(self, mod):
        assert len(mod.STEP_SEQUENCE) == 12

    def test_step_numbers_are_sequential(self, mod):
        numbers = [s["step"] for s in mod.STEP_SEQUENCE]
        assert numbers == list(range(1, 13))

    def test_all_steps_have_required_fields(self, mod):
        for s in mod.STEP_SEQUENCE:
            assert "step" in s
            assert "title" in s
            assert "phase" in s
            assert "condition" in s


class TestRouting:
    """Mode-driven step routing."""

    def _make_state(self, mode="pr", **overrides):
        state = {
            "completed_steps": [],
            "skipped_steps": [],
            "resolved_params": {
                "has_unfetched_issues": False,
            },
            "workspace": {
                "original_branch": None,
                "stash_ref": None,
            },
            "agents": {
                "dispatched": [],
                "completed": [],
                "failed": [],
            },
            "verdict": None,
        }
        # Handle flat overrides that map to nested structure
        if "has_unfetched_issues" in overrides:
            state["resolved_params"]["has_unfetched_issues"] = overrides.pop("has_unfetched_issues")
        if "original_branch" in overrides:
            state["workspace"]["original_branch"] = overrides.pop("original_branch")
        if "stash_ref" in overrides:
            state["workspace"]["stash_ref"] = overrides.pop("stash_ref")
        state.update(overrides)
        return state

    def _make_config(self, mode="pr", **overrides):
        config = {
            "mode": mode,
            "interactive": True,
        }
        config.update(overrides)
        return config

    def test_pr_mode_active_steps(self, mod):
        """PR mode with pre-computed context: 1,3,5,6,7,8,9,10,11."""
        config = self._make_config("pr")
        state = self._make_state("pr")
        ctx = {"git": {"merge_base": "abc123"}}  # pre-computed
        active = mod.get_active_steps("pr", config, state, ctx)
        # Step 2 skipped (context pre-computed), 4 skipped (no linear), 12 skipped (no workspace state)
        assert 2 not in active
        assert 4 not in active
        assert 7 in active  # baseline written for ALL modes
        assert 12 not in active

    def test_pr_mode_interactive_steps(self, mod):
        """PR mode interactive without pre-computed context: includes step 2."""
        config = self._make_config("pr", interactive=True)
        state = self._make_state("pr")
        ctx = {"git": {}}  # no merge_base = not pre-computed
        active = mod.get_active_steps("pr", config, state, ctx)
        assert 2 in active

    def test_pr_mode_non_interactive_no_context_is_error(self, mod):
        """Non-interactive PR without pre-computed context: step 2 returns hard error."""
        config = self._make_config("pr", interactive=False)
        state = self._make_state("pr")
        ctx = {"git": {}}  # no merge_base = not pre-computed
        # Step 2 should not be in active steps — it's a hard error, not a skip
        active = mod.get_active_steps("pr", config, state, ctx)
        assert 2 not in active

    def test_full_mode_active_steps(self, mod):
        """Full mode: 1,3,5,6,7,8,9,10,11."""
        config = self._make_config("full")
        state = self._make_state("full")
        ctx = {"git": {}}
        active = mod.get_active_steps("full", config, state, ctx)
        assert 2 not in active
        assert 7 in active  # baseline written for ALL modes
        assert 12 not in active

    def test_incremental_mode_has_save_baseline(self, mod):
        """Incremental mode includes step 7 (as does every mode)."""
        config = self._make_config("incremental")
        state = self._make_state("incremental")
        ctx = {"git": {}}
        active = mod.get_active_steps("incremental", config, state, ctx)
        assert 7 in active

    def test_step_7_runs_for_all_modes(self, mod):
        """Step 7 (Save Review Baseline) runs for ALL modes."""
        for mode in ("pr", "full", "incremental"):
            config = self._make_config(mode)
            state = self._make_state(mode)
            ctx = {"git": {}}
            active = mod.get_active_steps(mode, config, state, ctx)
            assert 7 in active, f"Step 7 should be active for {mode} mode"

    def test_linear_issues_activates_step_4(self, mod):
        """Step 4 activates when linear issues are detected."""
        config = self._make_config("full")
        state = self._make_state("full", has_unfetched_issues=True)
        ctx = {"git": {}}
        active = mod.get_active_steps("full", config, state, ctx)
        assert 4 in active

    def test_workspace_state_activates_cleanup_interactive(self, mod):
        """Step 12 activates when original_branch exists AND interactive."""
        config = self._make_config("pr", interactive=True)
        state = self._make_state("pr", original_branch="main")
        ctx = {"git": {}}
        active = mod.get_active_steps("pr", config, state, ctx)
        assert 12 in active

    def test_workspace_state_skips_cleanup_non_interactive(self, mod):
        """Step 12 skipped in non-interactive mode even with workspace state."""
        config = self._make_config("pr", interactive=False)
        state = self._make_state("pr", original_branch="main")
        ctx = {"git": {}}
        active = mod.get_active_steps("pr", config, state, ctx)
        assert 12 not in active


class TestNextStep:
    """Next step computation with skip explanations."""

    def test_consecutive_step(self, mod):
        """When next step is active, return it directly."""
        active = {1, 3, 5, 6, 8, 9, 10, 11}
        result = mod.compute_next_step(5, active)
        assert result["step"] == 6
        assert result.get("skip_reason") is None

    def test_non_consecutive_jump(self, mod):
        """When steps are skipped, jump and explain."""
        active = {1, 3, 5, 6, 8, 9, 10, 11}
        result = mod.compute_next_step(1, active)
        assert result["step"] == 3
        assert result["skip_reason"] is not None

    def test_final_step_returns_none(self, mod):
        """Last active step has no next."""
        active = {1, 3, 5, 6, 8, 9, 10, 11}
        result = mod.compute_next_step(11, active)
        assert result is None


class TestStateManagement:
    """Pipeline state file operations — split into run-config.json and pipeline-state.json."""

    def test_write_and_read_config(self, mod, tmp_path):
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        mod.write_config(str(tmp_path), config)
        loaded = mod.read_config(str(tmp_path))
        assert loaded["mode"] == "pr"
        assert loaded["pr_number"] == "42"

    def test_write_and_read_state(self, mod, tmp_path):
        state = {"current_step": 1, "completed_steps": [], "resolved_params": {}}
        mod.write_state(str(tmp_path), state)
        loaded = mod.read_state(str(tmp_path))
        assert loaded["completed_steps"] == []

    def test_read_missing_state_returns_default(self, mod, tmp_path):
        state = mod.read_state(str(tmp_path))
        assert state["completed_steps"] == []

    def test_read_missing_config_returns_default(self, mod, tmp_path):
        config = mod.read_config(str(tmp_path))
        assert config.get("mode") is None

    @pytest.mark.parametrize(
        "payload", ['["a"]', '"scalar"', "7"], ids=["array", "string", "int"]
    )
    def test_read_review_context_rejects_non_dict_json(
        self, mod, tmp_path, payload
    ):
        """Valid JSON that is not an object would crash every
        context.get() consumer — degrade to the empty-dict fallback."""
        (tmp_path / "review-context.json").write_text(payload)
        assert mod.read_review_context(str(tmp_path)) == {}

    def test_state_persists_workspace_params(self, mod, tmp_path):
        state = mod.read_state(str(tmp_path))
        state["workspace"] = {"original_branch": "main", "stash_ref": "abc123"}
        mod.write_state(str(tmp_path), state)
        loaded = mod.read_state(str(tmp_path))
        assert loaded["workspace"]["original_branch"] == "main"
        assert loaded["workspace"]["stash_ref"] == "abc123"

    def test_config_is_never_overwritten(self, mod, tmp_path):
        """run-config.json fields are seeded from CLI on first call and never overwritten."""
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        mod.write_config(str(tmp_path), config)
        loaded = mod.read_config(str(tmp_path))
        assert loaded["mode"] == "pr"
        assert loaded["pr_number"] == "42"

    def test_config_is_source_of_truth_over_cli(self, mod, tmp_path):
        """When run-config.json exists, its values take precedence over CLI args."""
        config = {"mode": "pr", "pr_number": "42", "interactive": True}
        mod.write_config(str(tmp_path), config)
        resolved = mod.resolve_params(str(tmp_path), cli_mode="full", cli_pr_number=None)
        assert resolved["mode"] == "pr"  # config wins over CLI


class TestTelemetryIdentityHelpers:
    """Step 1 identity discovery is best-effort and release-aware."""

    def test_installed_semver_directory_is_plugin_version(self, mod, tmp_path):
        plugin_root = tmp_path / "1.108.0"
        plugin_root.mkdir()
        (plugin_root / "CHANGELOG.md").write_text("## [9.9.9] - 2026-01-01\n")

        assert mod._detect_plugin_version(plugin_root) == "1.108.0"

    def test_source_checkout_uses_first_changelog_version(self, mod, tmp_path):
        plugin_root = tmp_path / "pirategoat-tools"
        plugin_root.mkdir()
        (plugin_root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [1.108.0] - 2026-07-19\n\n## [1.107.0] - 2026-07-19\n"
        )

        assert mod._detect_plugin_version(plugin_root) == "1.108.0"

    def test_unavailable_identity_helpers_return_empty_strings(self, mod, tmp_path, monkeypatch):
        def fail(*_args, **_kwargs):
            raise OSError("unavailable")

        monkeypatch.setattr(mod.subprocess, "check_output", fail)

        assert mod._git_output("rev-parse", "HEAD") == ""
        assert mod._detect_plugin_version(tmp_path / "missing") == ""

    def test_explicit_right_endpoint_is_resolved_as_head(self, mod, monkeypatch):
        identities = {
            "HEAD~1^{commit}": "previous-head",
            "HEAD^{commit}": "current-head",
        }

        def fake_git_output(*args):
            return identities.get(args[-1], "")

        monkeypatch.setattr(mod, "_git_output", fake_git_output)

        requested_range, base_sha, head_sha = mod._resolve_git_identity(
            "HEAD~1..HEAD~1"
        )

        assert requested_range == "HEAD~1..HEAD~1"
        assert base_sha == "previous-head"
        assert head_sha == "previous-head"

    @pytest.mark.parametrize(
        ("git_range", "expected_base", "expected_head"),
        [
            ("..topic", "current-head", "topic-head"),
            ("...topic", "current-head", "topic-head"),
            ("topic..", "topic-head", "current-head"),
            ("topic...", "topic-head", "current-head"),
            ("missing..topic", "", "topic-head"),
            ("topic..missing", "topic-head", ""),
            ("missing...topic", "", "topic-head"),
            ("topic...missing", "topic-head", ""),
        ],
    )
    def test_range_defaults_omitted_endpoints_and_preserves_unresolved_refs(
        self, mod, monkeypatch, git_range, expected_base, expected_head
    ):
        identities = {
            "HEAD^{commit}": "current-head",
            "topic^{commit}": "topic-head",
        }

        def fake_git_output(*args):
            return identities.get(args[-1], "")

        monkeypatch.setattr(mod, "_git_output", fake_git_output)

        _, base_sha, head_sha = mod._resolve_git_identity(git_range)

        assert base_sha == expected_base
        assert head_sha == expected_head

    def test_symbolic_supplied_endpoints_are_resolved_to_shas(
        self, mod, monkeypatch
    ):
        """Context merge_base from an explicit range like "main..HEAD" is the
        literal branch name — the durable identity must resolve it, never
        record a movable ref."""
        identities = {
            "main^{commit}": "a" * 40,
            "HEAD^{commit}": "b" * 40,
        }

        def fake_git_output(*args):
            return identities.get(args[-1], "")

        monkeypatch.setattr(mod, "_git_output", fake_git_output)

        _, base_sha, head_sha = mod._resolve_git_identity(
            "main..HEAD", base_sha="main", head_sha="HEAD"
        )

        assert base_sha == "a" * 40
        assert head_sha == "b" * 40

    def test_full_sha_endpoints_survive_when_git_is_unavailable(
        self, mod, monkeypatch
    ):
        """Peeling needs git; without it an already-full object id is the
        best obtainable identity and must not be dropped."""
        monkeypatch.setattr(mod, "_git_output", lambda *_args: "")

        _, base_sha, head_sha = mod._resolve_git_identity(
            "main..HEAD", base_sha="c" * 40, head_sha="d" * 64
        )

        assert base_sha == "c" * 40
        assert head_sha == "d" * 64

    def test_full_sha_tag_object_endpoints_are_peeled_to_commits(
        self, mod, monkeypatch
    ):
        """A supplied full object id can be an annotated tag object —
        ^{commit} peels it so the manifest records commit identity."""
        tag_object = "e" * 40
        commit = "f" * 40
        identities = {
            f"{tag_object}^{{commit}}": commit,
            "HEAD^{commit}": "b" * 40,
        }

        monkeypatch.setattr(
            mod, "_git_output", lambda *args: identities.get(args[-1], "")
        )

        _, base_sha, head_sha = mod._resolve_git_identity(
            "v1.0..HEAD", base_sha=tag_object
        )

        assert base_sha == commit
        assert head_sha == "b" * 40


class TestFailureRecovery:
    """Pipeline handles invalid states gracefully."""

    def test_invalid_step_number(self, mod, tmp_path):
        state = {"completed_steps": []}
        ctx = {}
        g = mod.get_step_guidance(99, "pr", state, ctx)
        assert g is None

    def test_corrupted_state_file(self, mod, tmp_path):
        """Pipeline survives corrupted pipeline-state.json."""
        (tmp_path / "pipeline-state.json").write_text("not json{{{")
        state = mod.read_state(str(tmp_path))
        assert state["completed_steps"] == []  # returns default

    def test_corrupted_config_file(self, mod, tmp_path):
        """Pipeline survives corrupted run-config.json."""
        (tmp_path / "run-config.json").write_text("not json{{{")
        config = mod.read_config(str(tmp_path))
        assert config.get("mode") is None  # returns default


class TestFormatOutput:
    """Output formatting follows curated-context-pipeline pattern."""

    def test_has_separator_header(self, mod):
        guidance = {
            "phase": "SETUP", "title": "Parse Input",
            "situation": [], "actions": ["Do something."],
            "handoff": None, "next_step": {"step": 3, "title": "Gather Context"},
            "skip_reason": "Step 2 skipped: PR-only (repo setup)",
        }
        output = mod.format_output(1, guidance)
        assert "═══" in output
        assert "Step 1" in output

    def test_has_next_pointer(self, mod):
        guidance = {
            "phase": "SETUP", "title": "Parse Input",
            "situation": [], "actions": ["Do something."],
            "handoff": None, "next_step": {"step": 3, "title": "Gather Context"},
            "skip_reason": None,
        }
        output = mod.format_output(1, guidance)
        assert "Step 3" in output
        assert "Gather Context" in output

    def test_skip_explanation_in_output(self, mod):
        guidance = {
            "phase": "SETUP", "title": "Parse Input",
            "situation": [], "actions": ["Do something."],
            "handoff": None, "next_step": {"step": 3, "title": "Gather Context"},
            "skip_reason": "Step 2 skipped: context already pre-computed",
        }
        output = mod.format_output(1, guidance)
        assert "pre-computed" in output

    def test_final_step_shows_complete(self, mod):
        guidance = {
            "phase": "OUTPUT", "title": "Present Results",
            "situation": [], "actions": ["Show results."],
            "handoff": None, "next_step": None,
            "skip_reason": None,
        }
        output = mod.format_output(11, guidance)
        assert "COMPLETE" in output

    def test_blocked_step_does_not_show_complete(self, mod):
        guidance = {
            "phase": "SYNTHESIS", "title": "Reconcile + Verify — WAITING",
            "situation": ["Agents still running."],
            "actions": ["Wait, then re-run step 8."],
            "handoff": None,
            "next_step": None,
            "skip_reason": None,
            "blocks_progress": True,
        }
        output = mod.format_output(8, guidance)
        assert "PIPELINE COMPLETE" not in output
        assert "Next:" not in output
        assert "PIPELINE WAITING" in output

    def test_handoff_section(self, mod):
        guidance = {
            "phase": "SETUP", "title": "Gather Context",
            "situation": [], "actions": ["Run script."],
            "handoff": ["Write change purpose to /tmp/change-purpose.md"],
            "next_step": {"step": 5, "title": "Dispatch Plan"},
            "skip_reason": None,
        }
        output = mod.format_output(3, guidance)
        assert "HANDOFF" in output
        assert "change-purpose.md" in output


class TestCLIIntegration:
    """Subprocess tests for the CLI."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_step_1_pr_mode_exits_0(self, tmp_path):
        r = self._run("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path), "--pr-number", "42")
        assert r.returncode == 0

    def test_step_1_full_mode_exits_0(self, tmp_path):
        r = self._run("--step", "1", "--mode", "full",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0

    def test_step_1_incremental_mode_exits_0(self, tmp_path):
        r = self._run("--step", "1", "--mode", "incremental",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 0

    def test_invalid_step_exits_1(self, tmp_path):
        r = self._run("--step", "99", "--mode", "pr",
                       "--output-dir", str(tmp_path))
        assert r.returncode == 1

    def test_missing_mode_exits_error(self, tmp_path):
        r = self._run("--step", "1", "--output-dir", str(tmp_path))
        assert r.returncode != 0

    def test_writes_run_config_and_pipeline_state(self, tmp_path):
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        config_path = tmp_path / "run-config.json"
        state_path = tmp_path / "pipeline-state.json"
        assert config_path.is_file()
        assert state_path.is_file()
        config = json.loads(config_path.read_text())
        assert config["mode"] == "pr"
        assert config["pr_number"] == "42"

    def test_workspace_params_persisted_to_state(self, tmp_path):
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42",
                   "--original-branch", "develop", "--stash-ref", "abc123")
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["workspace"]["original_branch"] == "develop"
        assert state["workspace"]["stash_ref"] == "abc123"

    def test_mode_required_when_no_config(self, tmp_path):
        """First call must provide --mode."""
        r = self._run("--step", "1", "--output-dir", str(tmp_path))
        assert r.returncode != 0

    def test_step_1_clears_stale_artifacts(self, tmp_path):
        """Step 1 should clear stale artifacts from previous runs."""
        # Seed stale artifacts
        (tmp_path / "pipeline-state.json").write_text('{"stale": true}')
        (tmp_path / "dispatch-plan.json").write_text('{"stale": true}')
        (tmp_path / "code-review.json").write_text('{"stale": true}')
        (tmp_path / "review-findings.json").write_text('{"stale": true}')
        (tmp_path / "review-findings.md").write_text("stale")
        (tmp_path / "review-report.md").write_text("stale")
        (tmp_path / "review-verdict.json").write_text('{"stale": true}')
        (tmp_path / "pipeline-result.json").write_text('{"stale": true}')
        (tmp_path / "decision-critic-findings.md").write_text("stale")
        (tmp_path / "scoped-diff.patch").write_text("legacy")
        (tmp_path / "security-reviewer-scoped-diff.patch").write_text("current")
        # Seed files that should be PRESERVED
        (tmp_path / "run-config.json").write_text('{"mode": "pr", "pr_number": "42"}')
        (tmp_path / ".branch-review-baseline.json").write_text('{"preserved": true}')
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        # Stale artifacts should be gone
        assert not (tmp_path / "dispatch-plan.json").exists()
        assert not (tmp_path / "review-findings.json").exists()
        assert not (tmp_path / "review-report.md").exists()
        assert not (tmp_path / "review-verdict.json").exists()
        assert not (tmp_path / "pipeline-result.json").exists()
        assert not (tmp_path / "scoped-diff.patch").exists()
        assert not (tmp_path / "security-reviewer-scoped-diff.patch").exists()
        # Preserved files should still exist
        assert (tmp_path / "run-config.json").is_file()
        assert (tmp_path / ".branch-review-baseline.json").is_file()

    def test_step_1_clears_per_agent_and_reconciliation_artifacts(self, tmp_path):
        """Agent sidecars, markers, and reconciliation context are per-run artifacts.

        Stale copies caused real failures in a reused output dir: a leftover
        <agent>-review.md made an agent's Write no-op, a leftover .started
        marker turns a forgotten dispatch into TIMED_OUT instead of
        NOT_DISPATCHED, and stale scope summaries would contaminate the
        run-level inline-coverage map.
        """
        (tmp_path / "security-review.md").write_text("stale agent markdown")
        (tmp_path / "security-reviewer-scope-summary.json").write_text('{"stale": true}')
        (tmp_path / "a11y-reviewer-scope-summary-config-ops.json").write_text('{"stale": true}')
        (tmp_path / "security-reviewer.started").write_text("2026-07-20T00:00:00+00:00")
        (tmp_path / "reconciliation-context.json").write_text('{"stale": true}')
        (tmp_path / "reconciliation-context.md").write_text("stale")
        (tmp_path / "critic-context.md").write_text("stale")
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        assert not (tmp_path / "security-review.md").exists()
        assert not (tmp_path / "security-reviewer-scope-summary.json").exists()
        assert not (tmp_path / "a11y-reviewer-scope-summary-config-ops.json").exists()
        assert not (tmp_path / "security-reviewer.started").exists()
        assert not (tmp_path / "reconciliation-context.json").exists()
        assert not (tmp_path / "reconciliation-context.md").exists()
        assert not (tmp_path / "critic-context.md").exists()

    def test_step_1_resets_interactive_review_context_to_current_output(self, tmp_path):
        """Interactive runs seed context without retaining prior-run fields."""
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "stale-base..stale-head",
                "merge_base": "stale-base",
                "head_sha": "stale-head",
            },
            "pr": {"number": 41},
            "output": {"directory": "/stale/output"},
        }))

        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))

        assert json.loads((tmp_path / "review-context.json").read_text()) == {
            "output": {"directory": str(tmp_path)},
        }

    def test_step_1_preserves_noninteractive_review_context(self, tmp_path):
        """Bot runs retain their precomputed Git and PR context."""
        context = {
            "git": {
                "git_range": "bot-base..bot-head",
                "merge_base": "bot-base",
                "head_sha": "bot-head",
            },
            "pr": {"number": 42},
            "output": {"directory": str(tmp_path)},
        }
        (tmp_path / "run-config.json").write_text(json.dumps({
            "mode": "pr",
            "pr_number": "42",
            "interactive": False,
        }))
        (tmp_path / "review-context.json").write_text(json.dumps(context))

        result = self._run("--step", "1", "--output-dir", str(tmp_path))

        assert result.returncode == 0
        assert json.loads((tmp_path / "review-context.json").read_text()) == context

    def test_step_1_clears_change_purpose(self, tmp_path):
        """Step 1 should clear stale change-purpose.md from previous runs."""
        (tmp_path / "change-purpose.md").write_text("Old change purpose from previous review.")
        self._run("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path))
        assert not (tmp_path / "change-purpose.md").exists(), "Stale change-purpose.md should be cleared"

    def test_step_1_writes_run_id(self, tmp_path):
        """Step 1 should write a run_id to pipeline-state.json."""
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "run_id" in state
        assert len(state["run_id"]) > 0

    def test_step_1_persists_explicit_session_id(self, tmp_path):
        (tmp_path / "run-config.json").write_text(json.dumps({
            "mode": "full",
            "interactive": True,
            "session_id": "session-stale",
        }))

        result = self._run(
            "--step", "1", "--mode", "full", "--output-dir", str(tmp_path),
            "--session-id", "session-current",
        )

        assert result.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["session_id"] == "session-current"

    def test_step_1_clears_stale_session_id_on_interactive_rerun(
        self, tmp_path
    ):
        """Interactive output dirs are reused and run-config.json survives
        cleanup: an omitted --session-id means this run's session is
        unknown, and the previous run's ID must not correlate the new
        telemetry with the old Claude transcript."""
        (tmp_path / "run-config.json").write_text(json.dumps({
            "mode": "full",
            "interactive": True,
            "session_id": "session-stale",
        }))

        result = self._run(
            "--step", "1", "--mode", "full", "--output-dir", str(tmp_path)
        )

        assert result.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert "session_id" not in config

    def test_step_1_uses_preseeded_session_id_when_cli_omits_it(self, tmp_path):
        (tmp_path / "run-config.json").write_text(json.dumps({
            "mode": "pr",
            "pr_number": "42",
            "interactive": False,
            "session_id": "bot-session",
        }))
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {"merge_base": "abc123"},
        }))

        result = self._run("--step", "1", "--output-dir", str(tmp_path))

        assert result.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["session_id"] == "bot-session"

    def test_step_1_generates_unique_run_ids(self, tmp_path):
        first = tmp_path / "first"
        second = tmp_path / "second"

        self._run("--step", "1", "--mode", "full", "--output-dir", str(first))
        self._run("--step", "1", "--mode", "full", "--output-dir", str(second))

        first_state = json.loads((first / "pipeline-state.json").read_text())
        second_state = json.loads((second / "pipeline-state.json").read_text())
        assert first_state["run_id"] != second_state["run_id"]


class TestQuickModeConfig:
    """--quick CLI flag is stored in run-config.json and persists across steps."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_quick_flag_stored_in_config(self, tmp_path):
        """Passing --quick stores quick=true in run-config.json."""
        r = self._run("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path), "--pr-number", "42",
                       "--quick")
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is True

    def test_no_quick_flag_defaults_false(self, tmp_path):
        """Without --quick, config has quick=false."""
        r = self._run("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path), "--pr-number", "42")
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config.get("quick") is False

    def test_quick_from_config_on_subsequent_steps(self, mod, tmp_path):
        """--quick persists in config and is readable on subsequent steps."""
        # Seed config as step 1 would
        mod.write_config(str(tmp_path), {
            "mode": "pr", "pr_number": "42", "interactive": True, "quick": True,
        })
        # Subsequent step reads config — quick should still be true
        config = mod.read_config(str(tmp_path))
        assert config["quick"] is True

    def test_quick_flag_on_rerun_overrides_existing_config(self, tmp_path):
        """Rerunning step 1 with --quick on a previously non-quick output dir
        should update run-config.json to quick=true."""
        # First run: no --quick
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config.get("quick") is False
        # Second run: with --quick (same output dir)
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42",
                   "--quick")
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is True

    def test_quick_flag_resets_on_rerun_without_flag(self, tmp_path):
        """Rerunning step 1 WITHOUT --quick after a quick run should reset to false."""
        # First run: with --quick
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42",
                   "--quick")
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is True
        # Second run: without --quick (same output dir)
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42")
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is False

    def test_bot_mode_step1_rerun_preserves_quick(self, tmp_path):
        """In bot mode (interactive=false), re-invoking step 1 without --quick
        should NOT reset the pre-written quick=true. The bot writes the correct
        value in run-config.json and may not pass --quick on subsequent calls."""
        # Step 1: with --quick
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42",
                   "--quick")
        # Simulate bot mode by setting interactive=false and providing
        # the review-context.json that bot mode requires
        config_path = tmp_path / "run-config.json"
        config = json.loads(config_path.read_text())
        config["interactive"] = False
        config_path.write_text(json.dumps(config))
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {"merge_base": "abc123"},
        }))
        assert config["quick"] is True
        # Step 1 rerun: without --quick (bot mode)
        r = self._run("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path), "--pr-number", "42")
        assert r.returncode == 0
        config = json.loads(config_path.read_text())
        assert config["quick"] is True, \
            "bot-mode step 1 rerun should not reset quick to false"

    def test_interactive_step1_rerun_still_resets_quick(self, tmp_path):
        """In interactive mode, re-invoking step 1 without --quick should still
        reset quick to false (existing behavior for human-driven reruns)."""
        # Step 1: with --quick
        self._run("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42",
                   "--quick")
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is True
        assert config.get("interactive") is True  # default
        # Step 1 rerun: without --quick (interactive rerun)
        r = self._run("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path), "--pr-number", "42")
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["quick"] is False, \
            "interactive step 1 rerun should reset quick to false"


class TestHostConfig:
    """The first pipeline call selects a host for all later briefings."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_claude_is_the_backward_compatible_default(self, tmp_path):
        result = self._run(
            "--step", "1", "--mode", "full", "--output-dir", str(tmp_path)
        )
        assert result.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["host"] == "claude"

    def test_codex_host_is_persisted(self, tmp_path):
        result = self._run(
            "--step", "1", "--mode", "full", "--output-dir", str(tmp_path),
            "--host", "codex",
        )
        assert result.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["host"] == "codex"


class TestDependencyRefreshConfig:
    """--refresh-deps is stored in run-config.json; hard-off non-interactive."""

    def _run(self, *args):
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_flag_stored_in_config(self, tmp_path):
        r = self._run("--step", "1", "--mode", "pr",
                      "--output-dir", str(tmp_path), "--pr-number", "42",
                      "--refresh-deps")
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["refresh_dependencies"] is True

    def test_no_flag_defaults_false(self, tmp_path):
        r = self._run("--step", "1", "--mode", "pr",
                      "--output-dir", str(tmp_path), "--pr-number", "42")
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config.get("refresh_dependencies") is False

    def test_non_interactive_cli_flag_is_forced_off(self, tmp_path):
        r = self._run("--step", "1", "--mode", "full",
                      "--output-dir", str(tmp_path),
                      "--interactive", "false", "--refresh-deps")
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["refresh_dependencies"] is False
        assert "interactive-only" in r.stderr

    def test_non_interactive_preseeded_config_is_forced_off(self, tmp_path):
        # A bot pre-writes run-config.json; the pipeline must not honor a
        # pre-seeded refresh_dependencies in bot mode.
        (tmp_path / "run-config.json").write_text(json.dumps({
            "mode": "full", "interactive": False,
            "refresh_dependencies": True,
        }))
        r = self._run("--step", "1", "--output-dir", str(tmp_path))
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["refresh_dependencies"] is False

    def test_interactive_rerun_syncs_flag_from_cli(self, tmp_path):
        # First run without the flag; rerun with it — CLI is authoritative
        # on interactive reruns, matching --quick semantics.
        self._run("--step", "1", "--mode", "pr",
                  "--output-dir", str(tmp_path), "--pr-number", "42")
        r = self._run("--step", "1", "--mode", "pr",
                      "--output-dir", str(tmp_path), "--pr-number", "42",
                      "--refresh-deps")
        assert r.returncode == 0
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["refresh_dependencies"] is True
        # And back off again
        self._run("--step", "1", "--mode", "pr",
                  "--output-dir", str(tmp_path), "--pr-number", "42")
        config = json.loads((tmp_path / "run-config.json").read_text())
        assert config["refresh_dependencies"] is False

    def test_step_1_clears_stale_dependency_refresh_artifact(self, tmp_path):
        (tmp_path / "dependency-refresh.json").write_text("{}")
        r = self._run("--step", "1", "--mode", "pr",
                      "--output-dir", str(tmp_path), "--pr-number", "42")
        assert r.returncode == 0
        assert not (tmp_path / "dependency-refresh.json").exists()

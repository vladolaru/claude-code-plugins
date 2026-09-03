"""Tests for review/pipeline.py and pipeline_contract.py routing, state, and CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/

sys.path.insert(0, str(TESTS_DIR))
from conftest import PIPELINE_TOTAL_STEPS as TOTAL_STEPS
from helpers.pipeline_process import hermetic_env, init_repo, run_pipeline
from review import run_paths


def _state_path(output_dir):
    path = run_paths.artifact_path(output_dir, "pipeline_state")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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
                "discarded_drafts": [],
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
        """Interactive PR mode includes the final consent step."""
        config = self._make_config("pr")
        state = self._make_state("pr")
        ctx = {"git": {"merge_base": "abc123"}}  # pre-computed
        active = mod.get_active_steps("pr", config, state, ctx)
        # Step 2 skipped (context pre-computed), 4 skipped (no linear).
        assert 2 not in active
        assert 4 not in active
        assert 7 in active  # baseline written for ALL modes
        assert 12 in active

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
        assert 12 not in active

    def test_full_mode_active_steps(self, mod):
        """Interactive full mode includes the final consent step."""
        config = self._make_config("full")
        state = self._make_state("full")
        ctx = {"git": {}}
        active = mod.get_active_steps("full", config, state, ctx)
        assert 2 not in active
        assert 7 in active  # baseline written for ALL modes
        assert 12 in active

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

    def test_plugin_commit_is_the_checkout_head(self, mod, tmp_path):
        """`plugin_version` only moves at release, so ~200 dev-mount
        commits stamp the same number. The commit is what tells two builds
        of one version apart."""
        repo = tmp_path / "plugin"
        repo.mkdir()
        for args in (
            ["init", "-b", "main"],
            ["config", "user.email", "t@t.com"],
            ["config", "user.name", "T"],
            ["config", "commit.gpgsign", "false"],
        ):
            subprocess.run(["git", *args], cwd=repo, check=True,
                           capture_output=True)
        (repo / "CHANGELOG.md").write_text("## [1.0.0] - 2026-01-01\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True,
                       capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True,
                       capture_output=True)
        expected = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        assert mod._detect_plugin_commit(repo) == expected

    def test_plugin_commit_is_none_outside_a_repository(self, mod, tmp_path):
        """Marketplace installs are usually git clones, so this is the
        exception rather than the norm — but a plugin directory that is
        no repository must stay silent: not a warning, not an
        exception."""
        plugin_root = tmp_path / "1.108.0"
        plugin_root.mkdir()
        assert mod._detect_plugin_commit(plugin_root) is None

    def test_plugin_commit_is_none_when_git_is_unusable(self, mod, monkeypatch,
                                                        tmp_path):
        def fail(*_args, **_kwargs):
            raise FileNotFoundError("git")

        monkeypatch.setattr(mod.subprocess, "run", fail)
        assert mod._detect_plugin_commit(tmp_path) is None

    def test_plugin_commit_rejects_unparseable_output(self, mod, monkeypatch,
                                                      tmp_path):
        """Whatever a future Git prints, only an object name is recorded."""
        class _Result:
            returncode = 0
            stdout = "fatal: not a tree\n"

        monkeypatch.setattr(
            mod.subprocess, "run", lambda *a, **k: _Result()
        )
        assert mod._detect_plugin_commit(tmp_path) is None

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
        _state_path(tmp_path).write_text("not json{{{")
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
        assert "✅" in output

    def test_a_degraded_run_does_not_sign_off_with_a_checkmark(self, mod):
        """The footer is a claim, and the last thing the reader sees must
        not contradict the degradations printed above it."""
        guidance = {
            "phase": "OUTPUT", "title": "Present Results",
            "situation": [], "actions": ["Show results."],
            "handoff": None, "next_step": None,
            "skip_reason": None,
            "degraded": True,
        }
        output = mod.format_output(11, guidance)
        assert "PIPELINE COMPLETE (DEGRADED" in output
        assert "✅" not in output

    def test_a_missing_degraded_flag_reads_as_not_degraded(self, mod):
        """Every other step's guidance dict omits the key entirely."""
        guidance = {
            "phase": "OUTPUT", "title": "Present Results",
            "situation": [], "actions": ["Show results."],
            "handoff": None, "next_step": None, "skip_reason": None,
        }
        assert "✅ PIPELINE COMPLETE" in mod.format_output(11, guidance)

    def test_an_outstanding_handoff_does_not_sign_off_as_complete(self, mod):
        """The last step now ASKS for an artifact — `review-report.md`,
        authored after the critic. Printing "PIPELINE COMPLETE" directly
        beneath a handoff gate demanding that file contradicts the gate
        one line above it."""
        guidance = {
            "phase": "OUTPUT", "title": "Author Report + Present Results",
            "situation": [], "actions": ["Author the report."],
            "handoff": ["Verify `review-report.md` exists."],
            "next_step": None, "skip_reason": None,
        }
        output = mod.format_output(11, guidance)
        assert "✅ PIPELINE COMPLETE" not in output
        assert "HANDOFF" in output

    def test_an_outstanding_handoff_on_a_degraded_run_claims_neither(
        self, mod
    ):
        """Both falsifiers apply at once, and the line must carry both:
        the run degraded AND the report is not written yet. "PIPELINE
        COMPLETE (DEGRADED)" above an open gate still claims completion."""
        guidance = {
            "phase": "OUTPUT", "title": "Author Report + Present Results",
            "situation": [], "actions": ["Author the report."],
            "handoff": ["Verify `review-report.md` exists."],
            "next_step": None, "skip_reason": None, "degraded": True,
        }
        output = mod.format_output(11, guidance)
        assert "PIPELINE STEPS COMPLETE (DEGRADED" in output
        assert "finish the HANDOFF above" in output
        assert "PIPELINE COMPLETE" not in output
        assert "✅" not in output

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

    def test_step_footer_carries_no_truncation_rule(self, mod):
        """Every step handoff (next-step and blocks-progress) tells the
        orchestrator to run the command unfiltered — the run12 failure was
        `--step 10 | head -60` eating the verdict instructions."""
        guidance_next = {
            "phase": "SETUP", "title": "T",
            "situation": [], "actions": [],
            "handoff": None, "next_step": {"step": 5, "title": "Next"},
            "skip_reason": None,
        }
        guidance_wait = {
            "phase": "SYNTHESIS", "title": "T",
            "situation": [], "actions": [],
            "handoff": None, "next_step": None,
            "skip_reason": None, "blocks_progress": True,
        }
        out_next = mod.format_output(3, guidance_next)
        out_wait = mod.format_output(3, guidance_wait)
        assert mod._RUN_EXACT_NOTE in out_next
        assert mod._RUN_EXACT_NOTE in out_wait
        assert "never pipe" in mod._RUN_EXACT_NOTE


class TestCLIIntegration:
    """Subprocess tests for the CLI."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path):
        """run_pipeline's cwd has no default — see its docstring. Isolate
        every subprocess call in this class to a throwaway repo at
        tmp_path/repo so none of them can touch the real checkout. The repo
        lives in a subdirectory, never at tmp_path itself, and remains a
        sibling of tmp_path/out so run artifacts cannot dirty the repo."""
        (tmp_path / "repo").mkdir()
        init_repo(tmp_path / "repo")
        (tmp_path / "out").mkdir()

    def test_step_1_pr_mode_exits_0(self, tmp_path):
        r = run_pipeline("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path / "out"), "--pr-number", "42", cwd=tmp_path / "repo")
        assert r.returncode == 0

    def test_step_1_full_mode_exits_0(self, tmp_path):
        r = run_pipeline("--step", "1", "--mode", "full",
                       "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")
        assert r.returncode == 0

    def test_step_1_incremental_mode_exits_0(self, tmp_path):
        r = run_pipeline("--step", "1", "--mode", "incremental",
                       "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")
        assert r.returncode == 0

    def test_invalid_step_exits_1(self, tmp_path):
        r = run_pipeline("--step", "99", "--mode", "pr",
                       "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")
        assert r.returncode == 1

    def test_missing_mode_exits_error(self, tmp_path):
        r = run_pipeline("--step", "1", "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")
        assert r.returncode != 0

    def test_writes_run_config_and_pipeline_state(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path / "out"), "--pr-number", "42", cwd=tmp_path / "repo")
        config_path = tmp_path / "out" / "run-config.json"
        state_path = _state_path(tmp_path / "out")
        assert config_path.is_file()
        assert state_path.is_file()
        config = json.loads(config_path.read_text())
        assert config["mode"] == "pr"
        assert config["pr_number"] == "42"

    def test_cli_seeded_config_fields_are_never_overwritten_on_rerun(self, tmp_path):
        """run-config.json is seeded from the CLI on the FIRST step 1 only.

        Step 1 reruns against the same output dir are routine (interactive
        resume, bot retry), and they may carry a different command line.
        The seeded identity of the run — which mode, which PR, which range —
        must survive that: `pipeline.py:707`'s `if not existing_config.get(
        "mode")` gate is what keeps a rerun from re-pointing a half-finished
        review at a different target while its artifacts still describe the
        old one. Only the fields with explicit rerun-sync semantics (host,
        quick, refresh_dependencies, session_id) may change.
        """
        run_pipeline("--step", "1", "--mode", "pr",
                     "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                     "--git-range", "aaa111..bbb222",
                     "--output-instructions", "original", cwd=tmp_path / "repo")

        r = run_pipeline("--step", "1", "--mode", "full",
                         "--output-dir", str(tmp_path / "out"), "--pr-number", "99",
                         "--git-range", "ccc333..ddd444",
                         "--output-instructions", "rewritten", cwd=tmp_path / "repo")
        assert r.returncode == 0

        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["mode"] == "pr"
        assert config["pr_number"] == "42"
        assert config["git_range"] == "aaa111..bbb222"
        assert config["output_instructions"] == "original"

    def test_run_config_carries_the_running_plugin_version(self, mod, tmp_path):
        """Step 1 stamps the artifact with the plugin that produced it.

        The stamp is the SAME fact telemetry records on the manifest, taken
        from the one detector (_detect_plugin_version) at the one place it
        runs. Without it, a durable run directory could not be attributed
        to a plugin version once its telemetry log is gone.
        """
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path / "out"), "--pr-number", "42", cwd=tmp_path / "repo")
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        expected = mod._detect_plugin_version()
        assert expected  # source checkout must resolve a version
        assert config["plugin_version"] == expected

    def test_pre_seeded_config_is_stamped_on_the_bot_path(self, mod, tmp_path):
        """Bot runs pre-write run-config.json, so the seed branch is skipped.

        The stamp must land on the existing-config path too, or every
        non-interactive run ships an unattributed artifact.
        """
        (tmp_path / "out" / "run-config.json").write_text(json.dumps({
            "mode": "pr", "pr_number": "42", "interactive": False,
        }))
        run_pipeline("--step", "1", "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["plugin_version"] == mod._detect_plugin_version()

    def test_stale_stamp_from_an_earlier_plugin_is_refreshed(self, mod, tmp_path):
        """A resumed run keeps its run-config.json.

        A rerun under an upgraded plugin must re-stamp, or the artifact
        would credit the run to the version that ran LAST time.
        """
        (tmp_path / "out" / "run-config.json").write_text(json.dumps({
            "mode": "pr", "pr_number": "42", "plugin_version": "0.0.1",
        }))
        run_pipeline("--step", "1", "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["plugin_version"] == mod._detect_plugin_version()
        assert config["plugin_version"] != "0.0.1"

    def test_run_config_carries_the_producing_build_commit(self, mod, tmp_path):
        """The plugin under test IS a git checkout, so the field resolves."""
        run_pipeline("--step", "1", "--mode", "pr",
                     "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                     cwd=tmp_path / "repo")
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["plugin_commit"] == mod._detect_plugin_commit()
        assert config["plugin_commit"]

    def test_undeterminable_commit_is_written_as_an_explicit_null(
        self, mod, tmp_path, monkeypatch
    ):
        """One shape, always. An omitted key would make "we could not tell"
        indistinguishable from "this artifact predates the field"."""
        config = {"mode": "pr", "pr_number": "42"}
        mod._stamp_run_config(str(tmp_path), config, "plugin_commit", None)
        written = json.loads((tmp_path / "run-config.json").read_text())
        assert "plugin_commit" in written
        assert written["plugin_commit"] is None

    def test_unchanged_stamp_does_not_rewrite_the_config(self, mod, tmp_path):
        config = {"mode": "pr", "plugin_commit": "abc1234"}
        mod.write_config(str(tmp_path), config)
        mtime = (tmp_path / "run-config.json").stat().st_mtime_ns
        mod._stamp_run_config(str(tmp_path), config, "plugin_commit", "abc1234")
        assert (tmp_path / "run-config.json").stat().st_mtime_ns == mtime

    def test_stale_build_commit_is_refreshed_on_rerun(self, mod, tmp_path):
        """A step-1 retry on a newer build must re-stamp run-config.json."""
        (tmp_path / "out" / "run-config.json").write_text(json.dumps({
            "mode": "pr", "pr_number": "42", "plugin_commit": "0000000",
        }))
        run_pipeline("--step", "1", "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["plugin_commit"] == mod._detect_plugin_commit()
        assert config["plugin_commit"] != "0000000"

    def test_workspace_params_persisted_to_state(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                   "--original-branch", "develop", "--stash-ref", "abc123", cwd=tmp_path / "repo")
        state = json.loads(_state_path(tmp_path / "out").read_text())
        assert state["workspace"]["original_branch"] == "develop"
        assert state["workspace"]["stash_ref"] == "abc123"

    def test_step_1_resets_interactive_review_context_to_current_output(self, tmp_path):
        """Interactive runs seed context without retaining prior-run fields."""
        (tmp_path / "out" / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "stale-base..stale-head",
                "merge_base": "stale-base",
                "head_sha": "stale-head",
            },
            "pr": {"number": 41},
            "output": {"directory": "/stale/output"},
        }))

        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")

        assert json.loads((tmp_path / "out" / "review-context.json").read_text()) == {
            "output": {"directory": str(tmp_path / "out")},
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
            "output": {"directory": str(tmp_path / "out")},
        }
        (tmp_path / "out" / "run-config.json").write_text(json.dumps({
            "mode": "pr",
            "pr_number": "42",
            "interactive": False,
        }))
        (tmp_path / "out" / "review-context.json").write_text(json.dumps(context))

        result = run_pipeline("--step", "1", "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")

        assert result.returncode == 0
        assert json.loads((tmp_path / "out" / "review-context.json").read_text()) == context

    def test_step_1_writes_run_id(self, tmp_path):
        """Step 1 should write a run_id to pipeline-state.json."""
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path / "out"), "--pr-number", "42", cwd=tmp_path / "repo")
        state = json.loads(_state_path(tmp_path / "out").read_text())
        assert "run_id" in state
        assert len(state["run_id"]) > 0

    def test_step_1_persists_explicit_session_id(self, tmp_path):
        (tmp_path / "out" / "run-config.json").write_text(json.dumps({
            "mode": "full",
            "interactive": True,
            "session_id": "session-stale",
        }))

        result = run_pipeline(
            "--step", "1", "--mode", "full", "--output-dir", str(tmp_path / "out"),
            "--session-id", "session-current",
            cwd=tmp_path / "repo",
        )

        assert result.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["session_id"] == "session-current"

    def test_step_1_clears_stale_session_id_on_interactive_rerun(
        self, tmp_path
    ):
        """Interactive output dirs are reused and run-config.json survives
        cleanup: an omitted --session-id means this run's session is
        unknown, and the previous run's ID must not correlate the new
        telemetry with the old Claude transcript."""
        (tmp_path / "out" / "run-config.json").write_text(json.dumps({
            "mode": "full",
            "interactive": True,
            "session_id": "session-stale",
        }))

        result = run_pipeline(
            "--step", "1", "--mode", "full", "--output-dir", str(tmp_path / "out"),
            cwd=tmp_path / "repo",
        )

        assert result.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert "session_id" not in config

    def test_step_1_uses_preseeded_session_id_when_cli_omits_it(self, tmp_path):
        (tmp_path / "out" / "run-config.json").write_text(json.dumps({
            "mode": "pr",
            "pr_number": "42",
            "interactive": False,
            "session_id": "bot-session",
        }))
        (tmp_path / "out" / "review-context.json").write_text(json.dumps({
            "git": {"merge_base": "abc123"},
        }))

        result = run_pipeline("--step", "1", "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")

        assert result.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["session_id"] == "bot-session"

    def test_step_1_generates_unique_run_ids(self, tmp_path):
        first = tmp_path / "first"
        second = tmp_path / "second"

        run_pipeline("--step", "1", "--mode", "full", "--output-dir", str(first), cwd=tmp_path / "repo")
        run_pipeline("--step", "1", "--mode", "full", "--output-dir", str(second), cwd=tmp_path / "repo")

        first_state = json.loads(_state_path(first).read_text())
        second_state = json.loads(_state_path(second).read_text())
        assert first_state["run_id"] != second_state["run_id"]


class TestSkippedStepRecording:
    """Steps the router passes over are recorded in durable pipeline state."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path):
        """Keep the test repo isolated from the run output directory."""
        (tmp_path / "repo").mkdir()
        init_repo(tmp_path / "repo")
        (tmp_path / "out").mkdir()

    def _prepare_step_11(self, tmp_path):
        result = run_pipeline(
            "--step", "11", "--mode", "full",
            "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo",
        )
        assert result.returncode == 0
        state = json.loads(
            _state_path(tmp_path / "out").read_text()
        )
        assert state["publication_pending"] is True
        assert 11 not in state["completed_steps"]
        assert 12 not in {entry["step"] for entry in state["skipped_steps"]}
        assert not (tmp_path / "out" / "pipeline-result.json").exists()

    def _publish_step_11(self, tmp_path):
        (tmp_path / "out" / "review-report.md").write_text("# Review report")
        result = run_pipeline(
            "--step", "11", "--mode", "full",
            "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo",
        )
        assert result.returncode == 0
        assert (tmp_path / "out" / "pipeline-result.json").is_file()

    def test_skipped_steps_recorded_with_condition(self, tmp_path):
        """Branch mode passes over step 2 — needs_workspace_setup is PR-only."""
        r = run_pipeline("--step", "1", "--mode", "full",
                       "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")
        assert r.returncode == 0
        state = json.loads(_state_path(tmp_path / "out").read_text())
        skipped = {s["step"]: s for s in state["skipped_steps"]}
        assert 2 in skipped
        assert skipped[2]["condition"] == "needs_workspace_setup"
        assert skipped[2]["title"] == "Repo Setup"

    def test_trailing_skip_recorded_at_last_active_step(self, tmp_path):
        """Non-interactive step 12 is skipped after step 11 publishes terminally."""
        run_pipeline("--step", "1", "--mode", "full", "--interactive", "false",
                   "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")
        self._prepare_step_11(tmp_path)
        self._publish_step_11(tmp_path)

        state = json.loads(_state_path(tmp_path / "out").read_text())
        skipped = {s["step"]: s for s in state["skipped_steps"]}
        assert 11 in state["completed_steps"]
        assert 12 in skipped
        assert skipped[12]["condition"] == "interactive"
        assert skipped[12]["title"] == "Cleanup"

    def test_skip_records_are_not_duplicated(self, tmp_path):
        """Re-invoking the same step records each passed-over step once."""
        run_pipeline("--step", "1", "--mode", "full", "--interactive", "false",
                   "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")
        self._prepare_step_11(tmp_path)
        self._publish_step_11(tmp_path)
        run_pipeline("--step", "11", "--mode", "full",
                   "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")

        state = json.loads(_state_path(tmp_path / "out").read_text())
        recorded = [entry["step"] for entry in state["skipped_steps"]]
        assert recorded == sorted(set(recorded))
        assert recorded.count(12) == 1

    def test_active_steps_are_never_recorded_as_skipped(self, tmp_path):
        """A step the router runs must not appear in the skip ledger."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path / "out"), cwd=tmp_path / "repo")
        state = json.loads(_state_path(tmp_path / "out").read_text())
        recorded = {entry["step"] for entry in state["skipped_steps"]}
        assert recorded.isdisjoint({1, 3, 5, 6, 7, 8, 9, 10, 11, 12})


class TestQuickModeConfig:
    """--quick CLI flag is stored in run-config.json and persists across steps."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path):
        """Keep the test repo isolated from the run output directory."""
        (tmp_path / "repo").mkdir()
        init_repo(tmp_path / "repo")
        (tmp_path / "out").mkdir()

    def test_quick_flag_stored_in_config(self, tmp_path):
        """Passing --quick stores quick=true in run-config.json."""
        r = run_pipeline("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                       "--quick", cwd=tmp_path / "repo")
        assert r.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["quick"] is True

    def test_no_quick_flag_defaults_false(self, tmp_path):
        """Without --quick, config has quick=false."""
        r = run_pipeline("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path / "out"), "--pr-number", "42", cwd=tmp_path / "repo")
        assert r.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config.get("quick") is False

    def test_quick_flag_on_rerun_overrides_existing_config(self, tmp_path):
        """Rerunning step 1 with --quick on a previously non-quick output dir
        should update run-config.json to quick=true."""
        # First run: no --quick
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path / "out"), "--pr-number", "42", cwd=tmp_path / "repo")
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config.get("quick") is False
        # Second run: with --quick (same output dir)
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                   "--quick", cwd=tmp_path / "repo")
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["quick"] is True

    def test_quick_flag_resets_on_rerun_without_flag(self, tmp_path):
        """Rerunning step 1 WITHOUT --quick after a quick run should reset to false."""
        # First run: with --quick
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                   "--quick", cwd=tmp_path / "repo")
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["quick"] is True
        # Second run: without --quick (same output dir)
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path / "out"), "--pr-number", "42", cwd=tmp_path / "repo")
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["quick"] is False

    def test_bot_mode_step1_rerun_preserves_quick(self, tmp_path):
        """In bot mode (interactive=false), re-invoking step 1 without --quick
        should NOT reset the pre-written quick=true. The bot writes the correct
        value in run-config.json and may not pass --quick on subsequent calls."""
        # Step 1: with --quick
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                   "--quick", cwd=tmp_path / "repo")
        # Simulate bot mode by setting interactive=false and providing
        # the review-context.json that bot mode requires
        config_path = tmp_path / "out" / "run-config.json"
        config = json.loads(config_path.read_text())
        config["interactive"] = False
        config_path.write_text(json.dumps(config))
        (tmp_path / "out" / "review-context.json").write_text(json.dumps({
            "git": {"merge_base": "abc123"},
        }))
        assert config["quick"] is True
        # Step 1 rerun: without --quick (bot mode)
        r = run_pipeline("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path / "out"), "--pr-number", "42", cwd=tmp_path / "repo")
        assert r.returncode == 0
        config = json.loads(config_path.read_text())
        assert config["quick"] is True, \
            "bot-mode step 1 rerun should not reset quick to false"

    def test_interactive_step1_rerun_still_resets_quick(self, tmp_path):
        """In interactive mode, re-invoking step 1 without --quick should still
        reset quick to false (existing behavior for human-driven reruns)."""
        # Step 1: with --quick
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                   "--quick", cwd=tmp_path / "repo")
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["quick"] is True
        assert config.get("interactive") is True  # default
        # Step 1 rerun: without --quick (interactive rerun)
        r = run_pipeline("--step", "1", "--mode", "pr",
                       "--output-dir", str(tmp_path / "out"), "--pr-number", "42", cwd=tmp_path / "repo")
        assert r.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["quick"] is False, \
            "interactive step 1 rerun should reset quick to false"


class TestHostConfig:
    """The first pipeline call selects a host for all later briefings."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path):
        """Keep the test repo isolated from the run output directory."""
        (tmp_path / "repo").mkdir()
        init_repo(tmp_path / "repo")
        (tmp_path / "out").mkdir()

    def test_claude_is_the_backward_compatible_default(self, tmp_path):
        result = run_pipeline(
            "--step", "1", "--mode", "full", "--output-dir", str(tmp_path / "out"),
            cwd=tmp_path / "repo",
        )
        assert result.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["host"] == "claude"

    def test_codex_host_is_persisted(self, tmp_path):
        result = run_pipeline(
            "--step", "1", "--mode", "full", "--output-dir", str(tmp_path / "out"),
            "--host", "codex",
            cwd=tmp_path / "repo",
        )
        assert result.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["host"] == "codex"


class TestDependencyRefreshConfig:
    """--refresh-deps is stored in run-config.json; hard-off non-interactive."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path):
        """Keep the test repo isolated from the run output directory."""
        (tmp_path / "repo").mkdir()
        init_repo(tmp_path / "repo")
        (tmp_path / "out").mkdir()

    def _trusting_env(self, tmp_path):
        config_dir = tmp_path / "xdg" / "pirategoat"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.json").write_text(json.dumps(
            {"review": {"refresh_dependencies": True}}))
        return {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "xdg")}

    def test_help_describes_adaptive_lockfile_preserving_refresh(self, tmp_path):
        result = run_pipeline("--help", cwd=tmp_path / "repo")

        assert result.returncode == 0
        assert "adaptive" in result.stdout
        assert "lockfile-preserving" in result.stdout
        assert "frozen-mode" not in result.stdout

    def test_flag_stored_in_config(self, tmp_path):
        r = run_pipeline("--step", "1", "--mode", "pr",
                      "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                      "--refresh-deps", cwd=tmp_path / "repo", env=hermetic_env())
        assert r.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["refresh_dependencies"] is True

    def test_no_flag_defaults_false(self, tmp_path):
        r = run_pipeline("--step", "1", "--mode", "pr",
                      "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                      cwd=tmp_path / "repo", env=hermetic_env())
        assert r.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config.get("refresh_dependencies") is False

    def test_non_interactive_cli_flag_is_forced_off(self, tmp_path):
        r = run_pipeline("--step", "1", "--mode", "full",
                      "--output-dir", str(tmp_path / "out"),
                      "--interactive", "false", "--refresh-deps",
                      cwd=tmp_path / "repo", env=hermetic_env())
        assert r.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["refresh_dependencies"] is False
        assert "interactive-only" in r.stderr

    def test_non_interactive_preseeded_config_is_forced_off(self, tmp_path):
        # A bot pre-writes run-config.json; the pipeline must not honor a
        # pre-seeded refresh_dependencies in bot mode.
        (tmp_path / "out" / "run-config.json").write_text(json.dumps({
            "mode": "full", "interactive": False,
            "refresh_dependencies": True,
        }))
        r = run_pipeline("--step", "1", "--output-dir", str(tmp_path / "out"),
                      cwd=tmp_path / "repo", env=hermetic_env())
        assert r.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["refresh_dependencies"] is False

    def test_interactive_rerun_syncs_flag_from_cli(self, tmp_path):
        # First run without the flag; rerun with it — CLI is authoritative
        # on interactive reruns, matching --quick semantics.
        run_pipeline("--step", "1", "--mode", "pr",
                  "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                  cwd=tmp_path / "repo", env=hermetic_env())
        r = run_pipeline("--step", "1", "--mode", "pr",
                      "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                      "--refresh-deps", cwd=tmp_path / "repo", env=hermetic_env())
        assert r.returncode == 0
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["refresh_dependencies"] is True
        # And back off again
        run_pipeline("--step", "1", "--mode", "pr",
                  "--output-dir", str(tmp_path / "out"), "--pr-number", "42",
                  cwd=tmp_path / "repo", env=hermetic_env())
        config = json.loads((tmp_path / "out" / "run-config.json").read_text())
        assert config["refresh_dependencies"] is False

    def test_user_config_defaults_interactive_runs_on(self, tmp_path):
        # ~/.config/pirategoat/config.json declares interactive runs
        # dependency-trusted: no per-run flag needed.
        out = tmp_path / "out"
        r = run_pipeline("--step", "1", "--mode", "pr",
                      "--output-dir", str(out), "--pr-number", "42",
                      cwd=tmp_path / "repo", env=self._trusting_env(tmp_path))
        assert r.returncode == 0
        config = json.loads((out / "run-config.json").read_text())
        assert config["refresh_dependencies"] is True

    def test_no_refresh_deps_overrides_config_default(self, tmp_path):
        out = tmp_path / "out"
        r = run_pipeline("--step", "1", "--mode", "pr",
                      "--output-dir", str(out), "--pr-number", "42",
                      "--no-refresh-deps",
                      cwd=tmp_path / "repo", env=self._trusting_env(tmp_path))
        assert r.returncode == 0
        config = json.loads((out / "run-config.json").read_text())
        assert config["refresh_dependencies"] is False

    def test_user_config_never_applies_to_non_interactive(self, tmp_path):
        out = tmp_path / "out"
        r = run_pipeline("--step", "1", "--mode", "full",
                      "--output-dir", str(out),
                      "--interactive", "false",
                      cwd=tmp_path / "repo", env=self._trusting_env(tmp_path))
        assert r.returncode == 0
        config = json.loads((out / "run-config.json").read_text())
        assert config["refresh_dependencies"] is False

    def test_rerun_without_flag_keeps_config_default(self, tmp_path):
        # With a trusting user config, flag absence resolves to the
        # config default, not to off.
        out = tmp_path / "out"
        env = self._trusting_env(tmp_path)
        run_pipeline("--step", "1", "--mode", "pr",
                  "--output-dir", str(out), "--pr-number", "42",
                  cwd=tmp_path / "repo", env=env)
        run_pipeline("--step", "1", "--mode", "pr",
                  "--output-dir", str(out), "--pr-number", "42",
                  cwd=tmp_path / "repo", env=env)
        config = json.loads((out / "run-config.json").read_text())
        assert config["refresh_dependencies"] is True

    def test_malformed_user_config_defaults_off(self, tmp_path):
        config_dir = tmp_path / "xdg" / "pirategoat"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text("{not json")
        env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "xdg")}
        out = tmp_path / "out"
        r = run_pipeline("--step", "1", "--mode", "pr",
                      "--output-dir", str(out), "--pr-number", "42",
                      cwd=tmp_path / "repo", env=env)
        assert r.returncode == 0
        config = json.loads((out / "run-config.json").read_text())
        assert config["refresh_dependencies"] is False

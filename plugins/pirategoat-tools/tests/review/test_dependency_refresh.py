"""Tests for review/dependency_refresh.py — stale dependency root detection."""

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

from review.dependency_refresh import detect_dependency_refresh


def _make_root(tmp_path, files=(), dirs=()):
    for name in dirs:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
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
        assert signal["suggested_command"] == "composer install"

    def test_missing_vendor_signals_even_without_range_change(self, tmp_path):
        root = _make_root(tmp_path, files=("composer.json", "composer.lock"))
        result = detect_dependency_refresh(str(root), ["src/main.php"])
        assert len(result["signals"]) == 1
        signal = result["signals"][0]
        assert signal["reasons"] == ["installed_state_missing"]
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
            "pnpm install --frozen-lockfile"

    def test_yarn_lockfile_detected(self, tmp_path):
        root = _make_root(tmp_path, files=("package.json", "yarn.lock"))
        result = detect_dependency_refresh(str(root), ["yarn.lock"])
        assert result["signals"][0]["manager"] == "yarn"
        assert result["signals"][0]["suggested_command"] == \
            "yarn install --immutable"

    def test_npm_lockfile_detected(self, tmp_path):
        root = _make_root(tmp_path, files=("package.json", "package-lock.json"))
        result = detect_dependency_refresh(str(root), ["package-lock.json"])
        assert result["signals"][0]["manager"] == "npm"
        assert result["signals"][0]["suggested_command"] == "npm ci"


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

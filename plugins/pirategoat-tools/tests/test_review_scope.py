"""
Unit tests for review-scope.py — pure logic functions + merge-base gating.

Tests pure functions (no git needed) and the merge-base rebase decision
via mock-based tests. Zero external dependencies beyond stdlib + pytest.

These tests validate the fix for the "always use merge-base" change:
previously, merge-base rebasing only happened when is_stale=True
(branch >10 commits behind). Now it happens unconditionally when
a merge-base exists and the range contains "..".
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup — import review-scope as a module
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
REVIEW_SCOPE_SCRIPT = SCRIPTS_DIR / "review-scope.py"

# Import review_scope module functions directly for unit testing
sys.path.insert(0, str(SCRIPTS_DIR))
import importlib

review_scope = importlib.import_module("review-scope")


# =============================================================================
# Pure function tests — no git, no mocking
# =============================================================================


class TestRebaseRangeToMergeBase:
    """Tests for rebase_range_to_merge_base() — pure string manipulation."""

    def test_basic_rebase(self):
        result = review_scope.rebase_range_to_merge_base(
            "origin/trunk..HEAD", "abc1234"
        )
        assert result == "abc1234..HEAD"

    def test_preserves_range_end(self):
        result = review_scope.rebase_range_to_merge_base(
            "origin/main..feature-branch", "deadbeef"
        )
        assert result == "deadbeef..feature-branch"

    def test_empty_merge_base_returns_original(self):
        result = review_scope.rebase_range_to_merge_base(
            "origin/trunk..HEAD", ""
        )
        assert result == "origin/trunk..HEAD"

    def test_no_dots_returns_original(self):
        result = review_scope.rebase_range_to_merge_base(
            "--cached", "abc1234"
        )
        assert result == "--cached"

    def test_empty_range_returns_original(self):
        result = review_scope.rebase_range_to_merge_base("", "abc1234")
        assert result == ""

    def test_full_sha_merge_base(self):
        sha = "a" * 40
        result = review_scope.rebase_range_to_merge_base(
            "origin/trunk..HEAD", sha
        )
        assert result == f"{sha}..HEAD"

    def test_short_sha_merge_base(self):
        result = review_scope.rebase_range_to_merge_base(
            "origin/trunk..HEAD", "abc1234"
        )
        assert result.startswith("abc1234..")


class TestDetectBaseRef:
    """Tests for detect_base_ref() — pure string parsing."""

    def test_two_dot_range(self):
        assert review_scope.detect_base_ref("origin/main..HEAD") == "origin/main"

    def test_two_dot_range_with_branch(self):
        assert review_scope.detect_base_ref("trunk..feature") == "trunk"

    def test_sha_range(self):
        assert review_scope.detect_base_ref("abc123..def456") == "abc123"

    def test_no_dots_returns_head(self):
        assert review_scope.detect_base_ref("--cached") == "HEAD"

    def test_empty_returns_head(self):
        assert review_scope.detect_base_ref("") == "HEAD"


class TestCountDiffLines:
    """Tests for count_diff_lines() — pure string parsing."""

    def test_simple_addition(self):
        diff = "+added line\n+another added"
        assert review_scope.count_diff_lines(diff) == 2

    def test_simple_removal(self):
        diff = "-removed line\n-another removed"
        assert review_scope.count_diff_lines(diff) == 2

    def test_mixed_changes(self):
        diff = "+added\n-removed\n context line\n+added2"
        assert review_scope.count_diff_lines(diff) == 3

    def test_ignores_diff_headers(self):
        diff = "--- a/file.py\n+++ b/file.py\n+real change"
        assert review_scope.count_diff_lines(diff) == 1

    def test_empty_diff(self):
        assert review_scope.count_diff_lines("") == 0

    def test_context_only(self):
        diff = " context1\n context2\n context3"
        assert review_scope.count_diff_lines(diff) == 0


class TestFilterNoise:
    """Tests for filter_noise() — pure regex filtering."""

    def test_keeps_code_files(self):
        files = ["src/app.php", "lib/utils.ts", "main.py"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == files
        assert skipped == []

    def test_skips_lock_files(self):
        # .lock$ pattern matches composer.lock but NOT package-lock.json
        # (which ends in .json, not .lock)
        files = ["composer.lock", "yarn.lock", "src/app.php"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.php"]
        assert set(skipped) == {"composer.lock", "yarn.lock"}

    def test_skips_package_lock_json(self):
        files = ["package-lock.json", "src/app.php"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.php"]
        assert skipped == ["package-lock.json"]

    def test_skips_pnpm_lock_yaml(self):
        files = ["pnpm-lock.yaml", "src/app.php"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.php"]
        assert skipped == ["pnpm-lock.yaml"]

    def test_skips_images(self):
        files = ["logo.png", "icon.svg", "photo.jpg", "src/app.ts"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.ts"]
        assert len(skipped) == 3

    def test_skips_vendor_directories(self):
        files = ["vendor/autoload.php", "node_modules/lodash/index.js", "src/app.php"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.php"]
        assert len(skipped) == 2

    def test_skips_build_artifacts(self):
        files = ["dist/bundle.js", "build/output.css", "src/app.ts"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.ts"]
        assert len(skipped) == 2

    def test_skips_minified_files(self):
        files = ["app.min.js", "styles.min.css", "src/app.ts"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.ts"]
        assert len(skipped) == 2

    def test_skips_snapshots(self):
        files = ["Component.test.tsx.snap", "src/app.ts"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.ts"]
        assert skipped == ["Component.test.tsx.snap"]

    def test_skips_go_sum(self):
        files = ["go.sum", "go.mod", "main.go"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["go.mod", "main.go"]
        assert skipped == ["go.sum"]

    def test_skips_npm_shrinkwrap(self):
        files = ["npm-shrinkwrap.json", "src/app.ts"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.ts"]
        assert skipped == ["npm-shrinkwrap.json"]

    def test_skips_po_translation_files(self):
        files = ["languages/plugin-fr_FR.po", "languages/plugin.pot", "src/app.php"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.php"]
        assert len(skipped) == 2

    def test_skips_yarn_directory(self):
        files = [".yarn/releases/yarn-3.6.0.cjs", ".yarn/cache/lodash.zip", "src/app.ts"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.ts"]
        assert len(skipped) == 2

    def test_skips_pycache_directory(self):
        files = ["__pycache__/module.cpython-311.pyc", "src/app.py"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.py"]
        assert len(skipped) == 1

    def test_skips_coverage_directories(self):
        files = [
            "coverage/lcov.info",
            ".nyc_output/data.json",
            "htmlcov/index.html",
            "src/app.ts",
        ]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.ts"]
        assert len(skipped) == 3

    def test_skips_cache_directory(self):
        files = [".cache/eslint/data.json", "src/app.ts"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.ts"]
        assert skipped == [".cache/eslint/data.json"]

    def test_skips_tsbuildinfo(self):
        files = ["tsconfig.tsbuildinfo", "src/app.ts"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.ts"]
        assert skipped == ["tsconfig.tsbuildinfo"]

    def test_skips_linter_caches(self):
        files = [".eslintcache", ".stylelintcache", "src/app.ts"]
        kept, skipped = review_scope.filter_noise(files)
        assert kept == ["src/app.ts"]
        assert set(skipped) == {".eslintcache", ".stylelintcache"}

    def test_empty_list(self):
        kept, skipped = review_scope.filter_noise([])
        assert kept == []
        assert skipped == []


class TestFilterDomain:
    """Tests for filter_domain() — domain-specific file matching."""

    def test_code_domain_includes_all_code(self):
        files = ["app.php", "utils.ts", "main.py", "style.css", "query.sql"]
        matched, excluded = review_scope.filter_domain(files, "code")
        assert matched == files

    def test_code_domain_excludes_non_code(self):
        files = ["README.md", "config.yml", "app.php"]
        matched, excluded = review_scope.filter_domain(files, "code")
        assert matched == ["app.php"]
        assert set(excluded) == {"README.md", "config.yml"}

    def test_php_tests_domain(self):
        files = ["src/App.php", "tests/AppTest.php", "tests/bootstrap.php"]
        matched, excluded = review_scope.filter_domain(files, "php-tests")
        assert "tests/AppTest.php" in matched
        assert "tests/bootstrap.php" in matched
        assert "src/App.php" in excluded

    def test_js_tests_domain_excludes_e2e(self):
        files = ["src/app.test.ts", "e2e/login.spec.ts", "src/utils.spec.js"]
        matched, excluded = review_scope.filter_domain(files, "js-tests")
        assert "src/app.test.ts" in matched
        assert "src/utils.spec.js" in matched
        assert "e2e/login.spec.ts" in excluded

    def test_dead_code_excludes_tests(self):
        files = ["src/app.php", "tests/AppTest.php", "src/app.test.ts"]
        matched, excluded = review_scope.filter_domain(files, "dead-code")
        assert matched == ["src/app.php"]
        assert len(excluded) == 2

    def test_unknown_domain_raises(self):
        with pytest.raises(RuntimeError, match="Unknown domain"):
            review_scope.filter_domain(["app.py"], "nonexistent-domain")

    def test_empty_list(self):
        matched, excluded = review_scope.filter_domain([], "code")
        assert matched == []
        assert excluded == []


# =============================================================================
# Merge-base gating tests — the core bug fix
#
# These verify that merge-base rebasing happens unconditionally (not just
# when is_stale=True), and that --no-merge-base still works as escape hatch.
# =============================================================================


class TestMergeBaseGatingIntegration:
    """Integration tests using real temp git repos to verify merge-base behavior.

    Tests the critical fix: merge-base rebasing must happen even when the
    branch is NOT stale (< STALE_BRANCH_THRESHOLD commits behind).
    """

    _repos: dict = {}

    @classmethod
    def _setup_repo(cls, behind_count: int) -> str:
        """Create a repo where main has advanced N commits past the branch point.

        Layout:
            initial commit (common ancestor)
              ├── main: N extra commits (each adding a .php file)
              └── feature: 1 commit (adding feature.php)
        """
        cache_key = f"merge-base-{behind_count}"
        if cache_key in cls._repos:
            return cls._repos[cache_key]

        tmp = tempfile.mkdtemp(prefix="test-merge-base-")

        def _git(*args):
            return subprocess.run(
                ["git"] + list(args),
                cwd=tmp, capture_output=True, text=True, check=True,
            )

        _git("init", "-b", "main")
        _git("config", "user.email", "test@test.com")
        _git("config", "user.name", "Test")
        _git("config", "commit.gpgsign", "false")

        # Initial commit (common ancestor)
        readme = os.path.join(tmp, "README.md")
        with open(readme, "w") as f:
            f.write("# Test Project\n")
        _git("add", ".")
        _git("commit", "-m", "initial")

        # Create feature branch from this point
        _git("branch", "feature")

        # Advance main with N commits
        for i in range(behind_count):
            filepath = os.path.join(tmp, f"trunk-file-{i}.php")
            with open(filepath, "w") as f:
                f.write(f"<?php // trunk change {i}\n")
            _git("add", ".")
            _git("commit", "-m", f"trunk commit {i}")

        # Switch to feature branch and add 1 commit
        _git("checkout", "feature")
        feature_file = os.path.join(tmp, "feature.php")
        with open(feature_file, "w") as f:
            f.write("<?php // feature change\n")
        _git("add", ".")
        _git("commit", "-m", "feature commit")

        cls._repos[cache_key] = tmp
        return tmp

    @classmethod
    def teardown_class(cls):
        for path in cls._repos.values():
            shutil.rmtree(path, ignore_errors=True)
        cls._repos.clear()

    def _run_scope(self, repo: str, extra_args: list = None) -> subprocess.CompletedProcess:
        cmd = [
            sys.executable, str(REVIEW_SCOPE_SCRIPT),
            "--domain", "code",
            "--range", "main..HEAD",
            "--format", "json",
        ]
        if extra_args:
            cmd.extend(extra_args)
        return subprocess.run(
            cmd, cwd=repo, capture_output=True, text=True, timeout=30,
        )

    def _run_preflight(self, repo: str, extra_args: list = None) -> subprocess.CompletedProcess:
        cmd = [
            sys.executable, str(REVIEW_SCOPE_SCRIPT),
            "--preflight",
            "--range", "main..HEAD",
            "--format", "json",
        ]
        if extra_args:
            cmd.extend(extra_args)
        return subprocess.run(
            cmd, cwd=repo, capture_output=True, text=True, timeout=30,
        )

    # -- The critical fix: non-stale branches also get rebased --

    def test_non_stale_branch_still_rebased_to_merge_base(self):
        """3 commits behind (not stale) → range MUST still be rebased.

        This is the core fix: previously, branches < STALE_BRANCH_THRESHOLD
        behind would use the raw two-dot range, including unrelated trunk files.
        """
        repo = self._setup_repo(3)
        result = self._run_scope(repo)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        bf = data["branch_freshness"]

        assert bf["is_stale"] is False, "3 behind should NOT be stale"
        assert bf["range_rebased"] is True, (
            "Non-stale branch must still be rebased to merge-base"
        )
        # Only the feature file should be in scope
        assert data["total_changed"] == 1, (
            f"Expected 1 file (feature.php), got {data['total_changed']}: {data['files']}"
        )

    def test_non_stale_branch_preflight_also_rebased(self):
        """Preflight mode also rebases non-stale branches."""
        repo = self._setup_repo(3)
        result = self._run_preflight(repo)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        bf = data["branch_freshness"]

        assert bf["is_stale"] is False
        assert bf["range_rebased"] is True
        # Should see 1 file changed, not 1 + 3 trunk files
        assert data["files_changed"] == 1

    def test_1_commit_behind_still_rebased(self):
        """Even 1 commit behind should rebase (any divergence matters)."""
        repo = self._setup_repo(1)
        result = self._run_scope(repo)
        assert result.returncode == 0
        data = json.loads(result.stdout)

        assert data["branch_freshness"]["range_rebased"] is True
        assert data["total_changed"] == 1

    def test_stale_branch_still_rebased(self):
        """Stale branches (>10 behind) continue to work as before."""
        repo = self._setup_repo(15)
        result = self._run_scope(repo)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        bf = data["branch_freshness"]

        assert bf["is_stale"] is True
        assert bf["range_rebased"] is True
        assert data["total_changed"] == 1

    # -- --no-merge-base escape hatch --

    def test_no_merge_base_flag_prevents_rebase(self):
        """--no-merge-base should prevent rebase even when merge-base exists."""
        repo = self._setup_repo(3)
        result = self._run_scope(repo, extra_args=["--no-merge-base"])
        assert result.returncode == 0
        data = json.loads(result.stdout)

        assert data["branch_freshness"]["range_rebased"] is False
        # Without merge-base rebase, we see trunk files + feature file
        assert data["total_changed"] == 4  # 3 trunk + 1 feature

    def test_no_merge_base_flag_preflight(self):
        """--no-merge-base works in preflight mode too."""
        repo = self._setup_repo(3)
        result = self._run_preflight(repo, extra_args=["--no-merge-base"])
        assert result.returncode == 0
        data = json.loads(result.stdout)

        assert data["branch_freshness"]["range_rebased"] is False
        assert data["files_changed"] == 4  # 3 trunk + 1 feature

    def test_no_merge_base_stale_branch(self):
        """--no-merge-base on stale branch shows ALL files."""
        repo = self._setup_repo(15)
        result = self._run_scope(repo, extra_args=["--no-merge-base"])
        assert result.returncode == 0
        data = json.loads(result.stdout)

        assert data["branch_freshness"]["range_rebased"] is False
        assert data["total_changed"] == 16  # 15 trunk + 1 feature

    # -- Stale warning still works (decoupled from rebase) --

    def test_stale_warning_still_present(self):
        """Stale branches show is_stale=True even though rebase is unconditional."""
        repo = self._setup_repo(15)
        result = self._run_scope(repo)
        assert result.returncode == 0
        data = json.loads(result.stdout)

        assert data["branch_freshness"]["is_stale"] is True
        assert data["branch_freshness"]["behind"] == 15

    def test_non_stale_no_warning(self):
        """Non-stale branches show is_stale=False."""
        repo = self._setup_repo(3)
        result = self._run_scope(repo)
        assert result.returncode == 0
        data = json.loads(result.stdout)

        assert data["branch_freshness"]["is_stale"] is False
        assert data["branch_freshness"]["behind"] == 3

    # -- Text output format --

    def test_text_output_shows_range_rebased_for_non_stale(self):
        """Text output includes RANGE_REBASED even when not stale."""
        repo = self._setup_repo(3)
        result = subprocess.run(
            [sys.executable, str(REVIEW_SCOPE_SCRIPT),
             "--domain", "code", "--range", "main..HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "RANGE_REBASED: true" in result.stdout
        # Should NOT show stale warning
        assert "BRANCH_FRESHNESS: STALE" not in result.stdout

    def test_text_output_shows_both_for_stale(self):
        """Text output shows both RANGE_REBASED and STALE warning when stale."""
        repo = self._setup_repo(15)
        result = subprocess.run(
            [sys.executable, str(REVIEW_SCOPE_SCRIPT),
             "--domain", "code", "--range", "main..HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "RANGE_REBASED: true" in result.stdout
        assert "BRANCH_FRESHNESS: STALE" in result.stdout

    # -- Merge-base SHA is present --

    def test_merge_base_sha_present(self):
        """branch_freshness includes a valid merge_base SHA."""
        repo = self._setup_repo(3)
        result = self._run_scope(repo)
        assert result.returncode == 0
        data = json.loads(result.stdout)

        merge_base = data["branch_freshness"]["merge_base"]
        assert len(merge_base) >= 7, f"merge_base too short: {merge_base}"
        # Should be a valid hex string
        assert all(c in "0123456789abcdef" for c in merge_base)

    # -- Range is correctly rewritten --

    def test_range_uses_merge_base_sha(self):
        """The range in the output should start with the merge-base SHA."""
        repo = self._setup_repo(3)
        result = self._run_scope(repo)
        assert result.returncode == 0
        data = json.loads(result.stdout)

        merge_base = data["branch_freshness"]["merge_base"]
        expected_prefix = merge_base[:7]  # At least first 7 chars
        assert data["range"].startswith(expected_prefix), (
            f"Range '{data['range']}' should start with merge-base '{expected_prefix}'"
        )
        assert data["range"].endswith("..HEAD")

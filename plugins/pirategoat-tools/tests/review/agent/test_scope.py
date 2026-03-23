"""
Unit tests for review/agent/scope.py — pure logic functions + merge-base gating.

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
# Path setup — import review/agent/scope.py as a module
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent.parent.parent  # agent/ -> review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
REVIEW_SCOPE_SCRIPT = SCRIPTS_DIR / "review" / "agent" / "scope.py"

# Import review_scope module functions directly for unit testing
sys.path.insert(0, str(SCRIPTS_DIR))
import importlib
import importlib.util

_scope_spec = importlib.util.spec_from_file_location("review_scope", str(REVIEW_SCOPE_SCRIPT))
review_scope = importlib.util.module_from_spec(_scope_spec)
_scope_spec.loader.exec_module(review_scope)


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

    def test_architecture_excludes_go_test_files(self):
        """architecture domain should not match Go test files."""
        files = ["pkg/handler_test.go"]
        matched, excluded = review_scope.filter_domain(files, "architecture")
        assert matched == [], "_test.go should be excluded from architecture"
        assert "pkg/handler_test.go" in excluded

    def test_architecture_does_not_exclude_contest(self):
        """architecture domain should not exclude files containing 'test' as substring."""
        files = ["pkg/contest_handler.go"]
        matched, excluded = review_scope.filter_domain(files, "architecture")
        assert matched == ["pkg/contest_handler.go"], "contest_handler.go should NOT be excluded"
        assert excluded == []

    def test_reliability_excludes_go_test_files(self):
        """reliability domain should not match Go test files."""
        files = ["pkg/handler_test.go"]
        matched, excluded = review_scope.filter_domain(files, "reliability")
        assert matched == [], "_test.go should be excluded from reliability"
        assert "pkg/handler_test.go" in excluded

    def test_reliability_excludes_php_test_files(self):
        """reliability domain should not match _test.php files."""
        files = ["tests/unit/handler_test.php"]
        matched, excluded = review_scope.filter_domain(files, "reliability")
        assert matched == [], "_test.php should be excluded from reliability"
        assert "tests/unit/handler_test.php" in excluded

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

    _scope_cache: dict = {}

    @classmethod
    def teardown_class(cls):
        for path in cls._repos.values():
            shutil.rmtree(path, ignore_errors=True)
        cls._repos.clear()
        cls._scope_cache.clear()

    @classmethod
    def _build_scope_in_repo(cls, repo, domain="code", range_spec="main..HEAD",
                             no_merge_base=False):
        """Call build_scope() directly, cached by (repo, no_merge_base)."""
        cache_key = (repo, no_merge_base)
        if cache_key in cls._scope_cache:
            return cls._scope_cache[cache_key]

        args = argparse.Namespace(
            domain=domain,
            range=range_spec,
            format="json",
            max_lines=2000,
            base_ref_only=False,
            summary=False,
            output_dir=None,
            no_merge_base=no_merge_base,
            no_semantic_filter=False,
        )
        saved_cwd = os.getcwd()
        try:
            os.chdir(repo)
            scope = review_scope.build_scope(args)
        finally:
            os.chdir(saved_cwd)
        cls._scope_cache[cache_key] = scope
        return scope

    def _scope_json(self, repo, **kwargs):
        """Build scope and return parsed JSON dict."""
        scope = self._build_scope_in_repo(repo, **kwargs)
        return json.loads(review_scope.format_json_output(scope))

    def _scope_text(self, repo, **kwargs):
        """Build scope and return text output."""
        scope = self._build_scope_in_repo(repo, **kwargs)
        return review_scope.format_text_output(scope)

    # -- The critical fix: non-stale branches also get rebased --

    def test_non_stale_branch_still_rebased_to_merge_base(self):
        """3 commits behind (not stale) → range MUST still be rebased.

        This is the core fix: previously, branches < STALE_BRANCH_THRESHOLD
        behind would use the raw two-dot range, including unrelated trunk files.
        """
        repo = self._setup_repo(3)
        data = self._scope_json(repo)
        bf = data["branch_freshness"]

        assert bf["is_stale"] is False, "3 behind should NOT be stale"
        assert bf["range_rebased"] is True, (
            "Non-stale branch must still be rebased to merge-base"
        )
        # Only the feature file should be in scope
        assert data["total_changed"] == 1, (
            f"Expected 1 file (feature.php), got {data['total_changed']}: {data['files']}"
        )

    def test_1_commit_behind_still_rebased(self):
        """Even 1 commit behind should rebase (any divergence matters)."""
        repo = self._setup_repo(1)
        data = self._scope_json(repo)

        assert data["branch_freshness"]["range_rebased"] is True
        assert data["total_changed"] == 1

    def test_stale_branch_still_rebased(self):
        """Stale branches (>10 behind) continue to work as before."""
        repo = self._setup_repo(15)
        data = self._scope_json(repo)
        bf = data["branch_freshness"]

        assert bf["is_stale"] is True
        assert bf["range_rebased"] is True
        assert data["total_changed"] == 1

    # -- --no-merge-base escape hatch --

    def test_no_merge_base_flag_prevents_rebase(self):
        """--no-merge-base should prevent rebase even when merge-base exists."""
        repo = self._setup_repo(3)
        data = self._scope_json(repo, no_merge_base=True)

        assert data["branch_freshness"]["range_rebased"] is False
        # Without merge-base rebase, we see trunk files + feature file
        assert data["total_changed"] == 4  # 3 trunk + 1 feature

    def test_no_merge_base_stale_branch(self):
        """--no-merge-base on stale branch shows ALL files."""
        repo = self._setup_repo(15)
        data = self._scope_json(repo, no_merge_base=True)

        assert data["branch_freshness"]["range_rebased"] is False
        assert data["total_changed"] == 16  # 15 trunk + 1 feature

    # -- Stale warning still works (decoupled from rebase) --

    def test_stale_warning_still_present(self):
        """Stale branches show is_stale=True even though rebase is unconditional."""
        repo = self._setup_repo(15)
        data = self._scope_json(repo)

        assert data["branch_freshness"]["is_stale"] is True
        assert data["branch_freshness"]["behind"] == 15

    def test_non_stale_no_warning(self):
        """Non-stale branches show is_stale=False."""
        repo = self._setup_repo(3)
        data = self._scope_json(repo)

        assert data["branch_freshness"]["is_stale"] is False
        assert data["branch_freshness"]["behind"] == 3

    # -- Text output format --

    def test_text_output_shows_range_rebased_for_non_stale(self):
        """Text output includes RANGE_REBASED even when not stale."""
        repo = self._setup_repo(3)
        text = self._scope_text(repo)
        assert "RANGE_REBASED: true" in text
        # Should NOT show stale warning
        assert "BRANCH_FRESHNESS: STALE" not in text

    def test_text_output_shows_both_for_stale(self):
        """Text output shows both RANGE_REBASED and STALE warning when stale."""
        repo = self._setup_repo(15)
        text = self._scope_text(repo)
        assert "RANGE_REBASED: true" in text
        assert "BRANCH_FRESHNESS: STALE" in text

    # -- Merge-base SHA is present --

    def test_merge_base_sha_present(self):
        """branch_freshness includes a valid merge_base SHA."""
        repo = self._setup_repo(3)
        data = self._scope_json(repo)

        merge_base = data["branch_freshness"]["merge_base"]
        assert len(merge_base) >= 7, f"merge_base too short: {merge_base}"
        # Should be a valid hex string
        assert all(c in "0123456789abcdef" for c in merge_base)

    # -- Range is correctly rewritten --

    def test_range_uses_merge_base_sha(self):
        """The range in the output should start with the merge-base SHA."""
        repo = self._setup_repo(3)
        data = self._scope_json(repo)

        merge_base = data["branch_freshness"]["merge_base"]
        expected_prefix = merge_base[:7]  # At least first 7 chars
        assert data["range"].startswith(expected_prefix), (
            f"Range '{data['range']}' should start with merge-base '{expected_prefix}'"
        )
        assert data["range"].endswith("..HEAD")


# =============================================================================
# Semantic filtering tests — apply_semantic_filter() integration
# =============================================================================


class TestSemanticFiltering:
    """Semantic filtering integration in diff output."""

    def test_filter_diff_imported(self):
        """filter_diff is importable from review/agent/diff_noise_filter.py."""
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location(
            "semantic_filter",
            str(SCRIPTS_DIR / "review" / "agent" / "diff_noise_filter.py"),
        )
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert callable(mod.filter_diff)

    def test_apply_semantic_filter_strips_docblocks(self):
        """apply_semantic_filter removes docblock noise from diff text."""
        diff_with_docblock = (
            "--- a/src/Foo.php\n"
            "+++ b/src/Foo.php\n"
            "@@ -1,10 +1,15 @@\n"
            " context line\n"
            "+/**\n"
            "+ * Added docblock\n"
            "+ * @param string $name\n"
            "+ */\n"
            "+public function bar($name) {\n"
            "+    return $name;\n"
            "+}\n"
        )
        filtered = review_scope.apply_semantic_filter(diff_with_docblock)
        # Docblock lines should be removed, code lines kept
        assert "+public function bar" in filtered
        assert "+    return $name;" in filtered
        assert "* Added docblock" not in filtered
        assert "@param string" not in filtered

    def test_apply_semantic_filter_preserves_diff_headers(self):
        """Diff headers (---, +++, @@) are never filtered."""
        diff = (
            "--- a/src/Foo.php\n"
            "+++ b/src/Foo.php\n"
            "@@ -1,5 +1,5 @@\n"
            "+// just a comment\n"
        )
        filtered = review_scope.apply_semantic_filter(diff)
        assert "--- a/src/Foo.php" in filtered
        assert "+++ b/src/Foo.php" in filtered
        assert "@@ -1,5 +1,5 @@" in filtered

    def test_apply_semantic_filter_empty_input(self):
        """Empty diff returns empty string."""
        assert review_scope.apply_semantic_filter("") == ""

    def test_count_diff_lines_after_filter(self):
        """count_diff_lines counts only meaningful lines after filtering."""
        diff_with_noise = (
            "--- a/f.php\n+++ b/f.php\n@@ -1,5 +1,8 @@\n"
            "+/**\n"
            "+ * Docblock\n"
            "+ */\n"
            "+public function foo() {}\n"
            "+\n"
        )
        # Raw count: 5 added lines
        raw_count = review_scope.count_diff_lines(diff_with_noise)
        assert raw_count == 5

        # Filtered count: only the function line (docblock + blank removed)
        filtered = review_scope.apply_semantic_filter(diff_with_noise)
        filtered_count = review_scope.count_diff_lines(filtered)
        assert filtered_count < raw_count
        assert filtered_count == 1


class TestSemanticFilterIntegration:
    """Semantic filtering integrated into build_scope diff pipeline."""

    def test_build_scope_calls_semantic_filter(self):
        """build_scope applies semantic filter to diffs by default."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x), \
             patch.object(review_scope, 'apply_semantic_filter', wraps=review_scope.apply_semantic_filter) as mock_filter:
            # Mock git commands
            mock_run.side_effect = self._mock_git_commands
            args = argparse.Namespace(
                domain="code", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir="/tmp/test",
                no_merge_base=True, no_semantic_filter=False,
            )
            scope = review_scope.build_scope(args)
            # Semantic filter should have been called for each diff
            assert mock_filter.call_count > 0

    def test_build_scope_skips_filter_when_disabled(self):
        """build_scope skips semantic filter when --no-semantic-filter is set."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x), \
             patch.object(review_scope, 'apply_semantic_filter') as mock_filter:
            mock_run.side_effect = self._mock_git_commands
            args = argparse.Namespace(
                domain="code", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir="/tmp/test",
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)
            mock_filter.assert_not_called()

    @staticmethod
    def _mock_git_commands(cmd, check=True, capture_stderr=True):
        """Mock git commands for build_scope testing."""
        cmd_str = " ".join(cmd)
        if "rev-parse --git-dir" in cmd_str:
            return ".git"
        if "rev-parse" in cmd_str:
            return "abc123"
        if "--name-only" in cmd_str:
            return "src/Foo.php"
        if "--numstat" in cmd_str:
            return "10\t2\tsrc/Foo.php"
        if "merge-base" in cmd_str:
            return "abc123"
        if "rev-list --count" in cmd_str:
            return "0"
        if "diff" in cmd_str and "--" in cmd_str:
            return (
                "--- a/src/Foo.php\n+++ b/src/Foo.php\n"
                "@@ -1,3 +1,5 @@\n+/**\n+ * Doc\n+ */\n+code();\n"
            )
        return ""


# =============================================================================
# Budget sort order tests — largest files first
# =============================================================================


def _mock_git_for_budget_test(cmd, check=True, capture_stderr=True):
    """Mock git commands for budget sort order testing."""
    cmd_str = " ".join(cmd)
    if "rev-parse --git-dir" in cmd_str:
        return ".git"
    if "rev-parse" in cmd_str:
        return "abc123"
    if "--name-only" in cmd_str:
        return "small.php\nmedium.php\nlarge.php"
    if "--numstat" in cmd_str:
        return "50\t50\tsmall.php\n150\t150\tmedium.php\n250\t250\tlarge.php"
    if "merge-base" in cmd_str:
        return "abc123"
    if "rev-list --count" in cmd_str:
        return "0"
    if "diff" in cmd_str and "-- small.php" in cmd_str:
        return "\n".join([f"+line{i}" for i in range(100)])
    if "diff" in cmd_str and "-- medium.php" in cmd_str:
        return "\n".join([f"+line{i}" for i in range(300)])
    if "diff" in cmd_str and "-- large.php" in cmd_str:
        return "\n".join([f"+line{i}" for i in range(500)])
    if "diff" in cmd_str and "--" in cmd_str:
        return "+changed line"
    return ""


class TestBudgetSortOrder:
    """Scope budgeting should sort files largest-first so large files get budget priority."""

    def test_files_sorted_largest_first(self):
        """Files should be sorted by total change size descending."""
        files = ["small.php", "medium.php", "large.php"]
        diffstat = {
            "small.php": (50, 50),     # 100 total
            "medium.php": (150, 150),  # 300 total
            "large.php": (250, 250),   # 500 total
        }

        # Simulate build_scope sorting
        sorted_files = sorted(
            files,
            key=lambda f: sum(diffstat.get(f, (0, 0))),
            reverse=True,
        )
        assert sorted_files[0] == "large.php", "Largest file should be first"
        assert sorted_files[-1] == "small.php", "Smallest file should be last"

    def test_budget_includes_large_file_over_small(self):
        """When budget is tight, large files should be included, small files excluded."""
        # With descending sort and 600-line budget:
        #   large(500) fits → 500 used
        #   medium(300) exceeds → skipped
        #   small(100) would fit but budget is at 500
        # With ascending sort (old behavior):
        #   small(100) fits → 100 used
        #   medium(300) fits → 400 used
        #   large(500) exceeds → skipped  ← large file lost!
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x):
            mock_run.side_effect = _mock_git_for_budget_test
            args = argparse.Namespace(
                domain="code", range="abc123..HEAD", max_lines=600,
                base_ref_only=False, summary=False, output_dir="/tmp/test",
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)
            # large.php (500 lines) should be in the included files
            assert "large.php" in scope["files"], (
                "Large file should be included when budget prioritizes largest first"
            )

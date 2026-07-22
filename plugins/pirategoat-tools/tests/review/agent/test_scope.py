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
import re
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

    def test_e2e_tests_domain_does_not_match_production_page_file(self):
        files = ["src/HomePage.ts", "e2e/pages/HomePage.ts"]
        matched, excluded = review_scope.filter_domain(files, "e2e-tests")
        assert matched == ["e2e/pages/HomePage.ts"]
        assert "src/HomePage.ts" in excluded

    def test_rust_tests_domain_matches_all_rs_files(self):
        """rust-tests domain includes all .rs files so inline #[cfg(test)] blocks are visible."""
        files = ["src/lib.rs", "tests/integration.rs", "benches/my_bench.rs"]
        matched, excluded = review_scope.filter_domain(files, "rust-tests")
        assert "src/lib.rs" in matched
        assert "tests/integration.rs" in matched
        assert "benches/my_bench.rs" in matched
        assert excluded == []

    def test_rust_test_dirs_excludes_production_source(self):
        """rust-test-dirs is the narrow triage domain: tests/ and benches/ only."""
        files = ["src/lib.rs", "tests/integration.rs", "benches/my_bench.rs"]
        matched, excluded = review_scope.filter_domain(files, "rust-test-dirs")
        assert "tests/integration.rs" in matched
        assert "benches/my_bench.rs" in matched
        assert "src/lib.rs" in excluded

    def test_python_tests_domain(self):
        files = ["src/models.py", "tests/test_api.py", "conftest.py", "test_utils.py"]
        matched, excluded = review_scope.filter_domain(files, "python-tests")
        assert "tests/test_api.py" in matched
        assert "conftest.py" in matched
        assert "test_utils.py" in matched
        assert "src/models.py" in excluded

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

    def test_toolchain_domain_matches_configs(self):
        files = ["pnpm-workspace.yaml", ".npmrc", "tsconfig.json", "src/app.ts"]
        matched, excluded = review_scope.filter_domain(files, "toolchain")
        assert "pnpm-workspace.yaml" in matched
        assert ".npmrc" in matched
        assert "tsconfig.json" in matched
        assert "src/app.ts" in excluded

    def test_toolchain_domain_matches_lock_files(self):
        """Lock files should pass the toolchain domain include filter."""
        files = [
            "pnpm-lock.yaml", "package-lock.json", "composer.lock",
            "yarn.lock", "go.sum", "npm-shrinkwrap.json",
        ]
        matched, excluded = review_scope.filter_domain(files, "toolchain")
        assert matched == files, "All lock files should match toolchain domain"
        assert excluded == []

    def test_toolchain_domain_matches_ci_files(self):
        files = [".github/workflows/ci.yml", "Dockerfile", "Makefile"]
        matched, excluded = review_scope.filter_domain(files, "toolchain")
        assert matched == files

    # --- Language coverage: production-code domains must see non-web languages ---
    # Regression for the Rust blindness: .rs (and other mainstream languages) were
    # absent from every production-code domain, so security/code/etc. returned
    # NO_DOMAIN_FILES on a pure-Rust diff. See
    # .claude/docs/analysis/2026-06-10-claude-rust-domain-classifier-gap.md

    def test_security_domain_matches_rust_source(self):
        """The reported bug: security-reviewer must see Rust auth code."""
        files = ["src/auth/login.rs", "src/auth/store.rs"]
        matched, excluded = review_scope.filter_domain(files, "security")
        assert matched == files, "security must match .rs production source"
        assert excluded == []

    def test_code_domain_matches_rust_source(self):
        files = ["src/main.rs", "src/lib.rs"]
        matched, _ = review_scope.filter_domain(files, "code")
        assert matched == files

    @pytest.mark.parametrize("domain", [
        "code", "security", "performance", "architecture", "patterns",
        "concurrency", "clarity", "simplification", "reliability",
        "api-contract", "data-flow", "dead-code", "reference-integrity",
    ])
    def test_production_domains_match_rust(self, domain):
        """Every general-purpose production-code domain must recognize .rs."""
        matched, _ = review_scope.filter_domain(["src/auth/refresh.rs"], domain)
        assert matched == ["src/auth/refresh.rs"], f"{domain} should match .rs source"

    @pytest.mark.parametrize("filename", [
        "Service.kt",      # Kotlin
        "App.swift",       # Swift
        "engine.cpp",      # C++
        "engine.c",        # C
        "Handler.cs",      # C# (was the pre-existing partial gap)
        "actor.scala",     # Scala
    ])
    def test_security_domain_matches_other_mainstream_languages(self, filename):
        """Broadened coverage: not just Rust — all mainstream languages."""
        matched, _ = review_scope.filter_domain([filename], "security")
        assert matched == [filename], f"security should match {filename}"

    def test_rust_source_excluded_from_test_only_domain(self):
        """rust-test-dirs stays narrow — broadening must not leak src/ into it."""
        matched, excluded = review_scope.filter_domain(["src/auth/login.rs"], "rust-test-dirs")
        assert matched == []
        assert "src/auth/login.rs" in excluded

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

    def test_build_scope_calls_semantic_filter(self, tmp_path):
        """build_scope applies semantic filter to diffs by default."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x), \
             patch.object(review_scope, 'apply_semantic_filter', wraps=review_scope.apply_semantic_filter) as mock_filter:
            # Mock git commands
            mock_run.side_effect = self._mock_git_commands
            args = argparse.Namespace(
                domain="code", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=False,
            )
            scope = review_scope.build_scope(args)
            # Semantic filter should have been called for each diff
            assert mock_filter.call_count > 0

    def test_build_scope_skips_filter_when_disabled(self, tmp_path):
        """build_scope skips semantic filter when --no-semantic-filter is set."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x), \
             patch.object(review_scope, 'apply_semantic_filter') as mock_filter:
            mock_run.side_effect = self._mock_git_commands
            args = argparse.Namespace(
                domain="code", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
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


_OVERSIZED_BUDGET_FILE_LINES = {
    "oversized.php": 700,
    "later-medium.php": 400,
    "later-small.php": 200,
}

_RAW_EXACT_FIT_BUDGET_FILE_LINES = {
    "oversized.php": 700,
    "exact-fit.php": 600,
    "later-small.php": 200,
}


def _make_mock_git_for_oversized_budget_test(file_lines):
    """Mock an oversized leading diff plus later ordinary-budget candidates."""
    def _mock(cmd, check=True, capture_stderr=True):
        cmd_str = " ".join(cmd)
        if "rev-parse --git-dir" in cmd_str:
            return ".git"
        if "rev-parse" in cmd_str:
            return "abc123"
        if "--name-only" in cmd_str:
            return "\n".join(file_lines)
        if "--numstat" in cmd_str:
            return "\n".join(
                f"{line_count}\t0\t{filepath}"
                for filepath, line_count in file_lines.items()
            )
        if "merge-base" in cmd_str:
            return "abc123"
        if "rev-list --count" in cmd_str:
            return "0"
        if "diff" in cmd_str and "--" in cmd:
            filepath = cmd[-1]
            line_count = file_lines[filepath]
            return "\n".join(
                f"+$value_{line_number} = compute_{line_number}();"
                for line_number in range(line_count)
            )
        return ""

    return _mock


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

    def test_budget_includes_large_file_over_small(self, tmp_path):
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
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)
            # large.php (500 lines) should be in the included files
            assert "large.php" in scope["files"], (
                "Large file should be included when budget prioritizes largest first"
            )
            assert "medium.php" in scope["skipped_files"]["budget"]
            assert "small.php" in scope["files"]

    def test_oversized_first_diff_preserves_normal_budget_for_later_files(self, tmp_path):
        """One protected oversized diff must not consume the ordinary budget pool."""
        max_lines = 600
        with patch.object(review_scope, "run_cmd") as mock_run, \
             patch.object(review_scope, "freshen_base_ref", side_effect=lambda x: x):
            mock_run.side_effect = _make_mock_git_for_oversized_budget_test(
                _OVERSIZED_BUDGET_FILE_LINES
            )
            args = argparse.Namespace(
                domain="code", range="abc123..HEAD", max_lines=max_lines,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=False,
            )
            scope = review_scope.build_scope(args)

        assert list(scope["diffs"]) == list(_OVERSIZED_BUDGET_FILE_LINES)
        assert "later-medium.php" in scope["diffs"]
        assert "later-small.php" in scope["diffs"]

        included = set(scope["diffs"])
        assert included.isdisjoint(scope["skipped_files"]["budget"])

        included_sizes = {
            filepath: review_scope.count_diff_lines(diff_text)
            for filepath, diff_text in scope["diffs"].items()
        }
        oversized_size = included_sizes["oversized.php"]
        ordinary_size = sum(
            line_count
            for filepath, line_count in included_sizes.items()
            if filepath != "oversized.php"
        )
        assert oversized_size > max_lines
        assert ordinary_size == max_lines
        assert scope["total_diff_lines"] == sum(included_sizes.values())
        assert scope["total_diff_lines"] <= oversized_size + max_lines

    def test_raw_prefetch_allows_exact_fit_after_oversized_diff(self, tmp_path):
        """A raw estimate equal to remaining capacity fits the ordinary pool."""
        max_lines = 600
        with patch.object(review_scope, "run_cmd") as mock_run, \
             patch.object(review_scope, "freshen_base_ref", side_effect=lambda x: x):
            mock_run.side_effect = _make_mock_git_for_oversized_budget_test(
                _RAW_EXACT_FIT_BUDGET_FILE_LINES
            )
            args = argparse.Namespace(
                domain="code", range="abc123..HEAD", max_lines=max_lines,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)

        assert list(scope["diffs"]) == ["oversized.php", "exact-fit.php"]
        assert "exact-fit.php" not in scope["skipped_files"]["budget"]
        assert "later-small.php" in scope["skipped_files"]["budget"]
        assert scope["total_diff_lines"] == 1300
        assert scope["total_diff_lines"] <= 700 + max_lines


_PRODUCTION_FIRST_FILE_LINES = {
    "tests/test_helpers.php": 900,   # largest — would win under pure largest-first
    "src/service.php": 500,
    "src/util.php": 300,
    "tests/test_util.php": 250,
}


class TestProductionFirstBudget:
    """Mixed domains must budget production files before test files."""

    def _build(self, domain, max_lines, tmp_path):
        with patch.object(review_scope, "run_cmd") as mock_run, \
             patch.object(review_scope, "freshen_base_ref", side_effect=lambda x: x):
            mock_run.side_effect = _make_mock_git_for_oversized_budget_test(
                _PRODUCTION_FIRST_FILE_LINES
            )
            args = argparse.Namespace(
                domain=domain, range="abc123..HEAD", max_lines=max_lines,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=False,
            )
            return review_scope.build_scope(args)

    def test_security_budgets_production_before_tests(self, tmp_path):
        scope = self._build("security", max_lines=600, tmp_path=tmp_path)
        # Production files fill the ordinary pool first, largest-first.
        assert "src/service.php" in scope["diffs"]
        assert list(scope["diffs"])[0] == "src/service.php"
        # The giant test file no longer evicts production code.
        assert "tests/test_helpers.php" in scope["skipped_files"]["budget"]

    def test_production_first_is_largest_first_within_tier(self, tmp_path):
        scope = self._build("security", max_lines=2000, tmp_path=tmp_path)
        assert list(scope["diffs"]) == [
            "src/service.php",
            "src/util.php",
            "tests/test_helpers.php",
            "tests/test_util.php",
        ]

    def test_php_tests_domain_keeps_largest_first(self, tmp_path):
        # Test domains: test files are the evidence — no production tier.
        scope = self._build("php-tests", max_lines=2000, tmp_path=tmp_path)
        assert list(scope["diffs"]) == [
            "tests/test_helpers.php",
            "tests/test_util.php",
        ]

    def test_oversized_leading_production_file_still_protected(self, tmp_path):
        # When the largest PRODUCTION file alone exceeds the budget, it is
        # the protected oversized diff and later files still get the
        # ordinary pool (the two budget behaviors compose).
        scope = self._build("security", max_lines=400, tmp_path=tmp_path)
        assert list(scope["diffs"])[0] == "src/service.php"
        assert "src/util.php" in scope["diffs"]


class TestScopeSummaryJson:
    """--summary-json-out persists a machine-readable scope summary."""

    def test_write_scope_summary_contents(self, tmp_path):
        scope = {
            "status": "OK",
            "domain": "security",
            "range": "abc123..HEAD",
            "diffs": {"src/b.php": "+x", "src/a.php": "+y"},
            "budget_exceeded_files": ["tests/test_big.php"],
            "list_only_files": [],
            "total_diff_lines": 42,
            "budget_max": 2000,
        }
        path = tmp_path / "security-reviewer-scope-summary.json"
        review_scope.write_scope_summary(scope, str(path))

        data = json.loads(path.read_text())
        assert data["schema"] == 1
        assert data["domain"] == "security"
        assert data["status"] == "OK"
        assert data["files_with_diffs"] == ["src/a.php", "src/b.php"]
        assert data["budget_exceeded_files"] == ["tests/test_big.php"]
        assert data["total_diff_lines"] == 42
        assert data["budget_max"] == 2000

    def test_write_scope_summary_tolerates_minimal_scope(self, tmp_path):
        # NO_DOMAIN_FILES scopes lack diffs/budget keys — must not raise.
        path = tmp_path / "sub" / "summary.json"
        review_scope.write_scope_summary({"status": "NO_DOMAIN_FILES"}, str(path))
        data = json.loads(path.read_text())
        assert data["files_with_diffs"] == []
        assert data["budget_exceeded_files"] == []

    def test_write_scope_summary_fails_open(self, tmp_path, capsys):
        # Unwritable path: warn on stderr, do not raise.
        blocker = tmp_path / "blocker"
        blocker.write_text("")
        review_scope.write_scope_summary(
            {"status": "OK"}, str(blocker / "impossible" / "summary.json")
        )
        assert "could not write scope summary" in capsys.readouterr().err


# =============================================================================
# Markup-evidence budget priority — a11y domain
# =============================================================================


# Per-file diff bodies for the evidence-priority scenarios. The backend file
# is large, emits no markup, but DOES contain '$role = ...' assignments —
# the false positive that must not outrank genuine template evidence.
_PRIORITY_FILE_DIFFS = {
    "includes/backend.php": "\n".join(
        ["+$role = get_option( 'default_role' );"]
        + [f"+$x{i} = compute_{i}( $data );" for i in range(2099)]
    ),
    "templates/button.php": (
        '+<button type="submit"><?php echo esc_html( $label ); ?></button>\n'
        "+<?php // submit ?>"
    ),
    "resources/views/card.blade.php": "+{{ $slot }}",
    "Components/NavMenu.razor": "+@foreach ( var item in Items ) { @item.Name }",
    "src/styles/focus.scss": "+.wc-input:focus-visible {\n+  outline: 2px solid;",
}


def _combined_patch(files):
    return "\n".join(
        f"diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n"
        f"@@ -1,0 +1,{len(_PRIORITY_FILE_DIFFS[f].splitlines())} @@\n"
        f"{_PRIORITY_FILE_DIFFS[f]}"
        for f in files
    )


def _make_mock_git_for_priority(files, sizes):
    """Shared run_cmd stand-in for the evidence-priority tests.

    Routes by command shape, mirroring build_scope's real git traffic:
    rev-parse/merge-base/rev-list get inert values; --name-only and
    --numstat are synthesized from `files`/`sizes`; a MULTI-file `--`
    diff (the single combined evidence scan) returns _combined_patch
    (full headers + hunk markers — the strict in-hunk scanner ignores
    structurally invalid patches); a SINGLE-file `--` diff (the budget
    loop's per-file fetch) returns that file's body from
    _PRIORITY_FILE_DIFFS. Tests asserting call counts or ordering rely
    on this multi-vs-single discrimination."""
    def _mock(cmd, check=True, capture_stderr=True):
        cmd_str = " ".join(cmd)
        if "rev-parse --git-dir" in cmd_str:
            return ".git"
        if "rev-parse" in cmd_str:
            return "abc123"
        if "--name-only" in cmd_str:
            return "\n".join(files)
        if "--numstat" in cmd_str:
            return "\n".join(f"{sizes[f]}\t0\t{f}" for f in files)
        if "merge-base" in cmd_str:
            return "abc123"
        if "rev-list --count" in cmd_str:
            return "0"
        if "diff" in cmd_str and "--" in cmd:
            requested = cmd[cmd.index("--") + 1:]
            if len(requested) > 1:
                return _combined_patch(requested)
            if len(requested) == 1:
                return _PRIORITY_FILE_DIFFS[requested[0]]
        return ""
    return _mock


class TestMarkupEvidenceBudgetPriority:
    """The a11y domain budgets evidence-bearing files FIRST.

    Largest-first budgeting starved the file that actually carried the
    dispatch evidence: a 2,100-line backend PHP diff consumed the whole
    2,000-line budget while the tiny template/stylesheet change — the
    reason a11y dispatched at all — landed in NOT DIFFED. Evidence =
    markup tokens in the diff OR a stylesheet extension (style files are
    inherent visual-a11y surface, mirroring the has_style_files dispatch
    signal). And '$role = ...' backend assignments must not fake evidence."""

    def _build(self, tmp_path, domain, files, sizes, max_lines=2000):
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x):
            mock_run.side_effect = _make_mock_git_for_priority(files, sizes)
            args = argparse.Namespace(
                domain=domain, range="abc123..HEAD", max_lines=max_lines,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=True,
            )
            return review_scope.build_scope(args), mock_run

    _TEMPLATE_CASE = (
        ["includes/backend.php", "templates/button.php"],
        {"includes/backend.php": 2100, "templates/button.php": 2},
    )
    _STYLESHEET_CASE = (
        ["includes/backend.php", "src/styles/focus.scss"],
        {"includes/backend.php": 2100, "src/styles/focus.scss": 2},
    )
    _TOKEN_FREE_TEMPLATE_CASE = (
        ["includes/backend.php", "resources/views/card.blade.php"],
        {"includes/backend.php": 2100, "resources/views/card.blade.php": 1},
    )
    _RAZOR_TEMPLATE_CASE = (
        ["includes/backend.php", "Components/NavMenu.razor"],
        {"includes/backend.php": 2100, "Components/NavMenu.razor": 1},
    )

    def test_a11y_budget_includes_markup_file_before_large_backend_file(self, tmp_path):
        scope, _ = self._build(tmp_path, "a11y", *self._TEMPLATE_CASE)
        assert "templates/button.php" in scope["diffs"]
        assert "includes/backend.php" in scope["skipped_files"]["budget"]

    def test_a11y_budget_includes_stylesheet_before_large_backend_file(self, tmp_path):
        """A stylesheet-only a11y change (dispatched via has_style_files)
        must receive budget — markup tokens are not the only evidence."""
        scope, _ = self._build(tmp_path, "a11y", *self._STYLESHEET_CASE)
        assert "src/styles/focus.scss" in scope["diffs"]
        assert "includes/backend.php" in scope["skipped_files"]["budget"]

    def test_a11y_budget_includes_token_free_template_before_backend(self, tmp_path):
        scope, _ = self._build(tmp_path, "a11y", *self._TOKEN_FREE_TEMPLATE_CASE)
        assert "resources/views/card.blade.php" in scope["diffs"]
        assert "includes/backend.php" in scope["skipped_files"]["budget"]

    def test_a11y_budget_includes_razor_before_backend(self, tmp_path):
        scope, _ = self._build(tmp_path, "a11y", *self._RAZOR_TEMPLATE_CASE)
        assert "Components/NavMenu.razor" in scope["diffs"]
        assert "includes/backend.php" in scope["skipped_files"]["budget"]

    def test_backend_role_assignment_is_not_evidence(self, tmp_path):
        """backend.php contains '$role = ...' — it must land in the
        non-evidence tier despite the attribute-looking token."""
        scope, _ = self._build(tmp_path, "a11y", *self._TEMPLATE_CASE)
        assert "includes/backend.php" not in scope["diffs"]

    def test_evidence_scan_is_a_single_git_call(self, tmp_path):
        """Classification must not launch one git diff per matched file —
        one combined call for the scan, then per-file fetches only for
        files actually receiving budget."""
        _, mock_run = self._build(tmp_path, "a11y", *self._TEMPLATE_CASE)
        multi_file_diffs = [
            c for c in mock_run.call_args_list
            if "diff" in c.args[0] and "--" in c.args[0]
            and len(c.args[0][c.args[0].index("--") + 1:]) > 1
        ]
        single_file_diffs = [
            c for c in mock_run.call_args_list
            if "diff" in c.args[0] and "--" in c.args[0]
            and len(c.args[0][c.args[0].index("--") + 1:]) == 1
        ]
        assert len(multi_file_diffs) == 1, "expected exactly one combined evidence scan"
        # Only the budgeted file gets an individual fetch; the budget-excluded
        # backend file must NOT be fetched (it was never going to be included).
        fetched = {c.args[0][-1] for c in single_file_diffs}
        assert fetched == {"templates/button.php"}

    def test_non_priority_domains_keep_largest_first(self, tmp_path):
        """Domains without markup priority keep the largest-first order —
        the code domain still budgets the backend file."""
        scope, _ = self._build(tmp_path, "code", *self._TEMPLATE_CASE)
        assert "includes/backend.php" in scope["files"]


# =============================================================================
# Markup token edge cases + evidence-scan path handling
# =============================================================================


class TestMarkupTokenEdgeCases:
    def test_unquoted_role_attribute_is_markup(self):
        """<div role=button> is valid HTML — unquoted known ARIA roles count,
        while the backend-assignment guard stays intact."""
        assert review_scope.patch_has_markup_tokens("+ <div role=button>")
        assert review_scope.patch_has_markup_tokens("+ <li role=menuitem>")
        assert not review_scope.patch_has_markup_tokens("+ $role = $user->role;")
        assert not review_scope.patch_has_markup_tokens("+ role = resolve_role(user)")

    def test_semantic_structure_elements_are_markup(self):
        """Table semantics, figures, lists, description lists, landmarks —
        all screen-reader-visible structure (round-9 miss: removing a
        <caption> in TSX skipped a11y)."""
        for line in (
            "-      <caption>Order history</caption>",
            "+      <th scope=\"col\">Total</th>",
            "+ <figure><figcaption>Sales chart</figcaption></figure>",
            "+ <?php echo '<dl><dt>Status</dt><dd>' . $status . '</dd></dl>'; ?>",
            "+ <ol><li>Step one</li></ol>",
            "+ <progress max=\"100\" value=\"70\"></progress>",
            "+ <section></section>",
        ):
            assert review_scope.patch_has_markup_tokens(line), line

    def test_wp_form_helper_calls_are_markup(self):
        """WP/WC helpers emit controls with no literal tag in the diff —
        helper-generated markup is still markup (round-13 miss: a
        submit_button()/woocommerce_form_field() admin change skipped
        a11y before mixed-markup routing became conservative)."""
        for line in (
            "+\t\tsubmit_button( __( 'Save changes', 'woocommerce' ) );",
            "+\t\techo get_submit_button( $text, 'secondary' );",
            "+\t\twoocommerce_form_field( 'wc_locale', $args, $value );",
            "+\t\twp_dropdown_pages( array( 'name' => 'page_id' ) );",
            "+\t\twp_nonce_field( 'wc_save', '_wc_nonce' );",
            "+\t\techo wc_help_tip( $tip_text );",
        ):
            assert review_scope.patch_has_markup_tokens(line), line

    def test_woocommerce_wp_field_helpers_are_markup(self):
        """The woocommerce_wp_* field family (text_input, select, checkbox,
        radio, textarea, ...) emits labels and controls — and template
        rendering calls emit whole markup files (round-14 P1)."""
        for line in (
            "+\t\twoocommerce_wp_text_input( array( 'id' => '_sku' ) );",
            "+\t\twoocommerce_wp_select( array( 'id' => '_tax_status' ) );",
            "+\t\twoocommerce_wp_checkbox( $field );",
            "+\t\twoocommerce_wp_radio( $field );",
            "+\t\twc_get_template( 'checkout/form-login.php', $args );",
            "+\t\twc_get_template_html( 'emails/order-details.php', $args );",
            "+\t\tget_template_part( 'template-parts/order', 'row' );",
        ):
            assert review_scope.patch_has_markup_tokens(line), line

    @pytest.mark.parametrize(
        "line",
        [
            "+ wp_nav_menu( $args );",
            "+ wp_login_form( $args );",
            "+ get_search_form();",
            "+ comment_form( $args );",
            "+ wp_list_comments( $args );",
            "+ wp_page_menu( $args );",
            "+ wp_link_pages( $args );",
            "+ wp_loginout();",
            "+ wp_register();",
            "+ wp_get_archives( $args );",
            "+ wp_tag_cloud( $args );",
            "+ dynamic_sidebar( 'primary' );",
            "+ the_widget( WC_Widget_Cart::class );",
            "+ echo build_custom_navigation( $args );",
            "+ <?= build_custom_navigation( $args ); ?>",
            "+ echo $renderer->render( $context );",
            "+ $view->display( $context );",
            "+ $view->output( $context );",
            "+ $renderer->emit( $context );",
        ],
    )
    def test_php_render_surfaces_are_markup(self, line):
        assert review_scope.patch_has_markup_tokens(line)

    def test_template_composition_is_markup(self):
        """Includes/partials/renders pull an entire interactive UI into the
        page — composition IS markup emission even with no literal tag on
        the changed line (round-15 P1)."""
        for line in (
            '+{% include "checkout/payment-methods.twig" with { gateways: gateways } %}',
            "+{{> order-summary }}",
            "+<%= render partial: 'orders/row', collection: @orders %>",
            "+\t@include('orders.table', ['orders' => $orders])",
            '+{{ template "order-row" . }}',
        ):
            assert review_scope.patch_has_markup_tokens(line), line

    def test_composition_lookalikes_are_not_markup(self):
        for line in (
            "+    include 'class-wc-order.php';",
            "+    $data = render_totals( $order );",
        ):
            assert not review_scope.patch_has_markup_tokens(line), line

    def test_helper_lookalikes_are_not_markup(self):
        for line in (
            "+    submit_form_data( $payload );",
            "+    $button_count = 3;",
            "+    process_dropdown_choice( $value );",
            '+    const copy = "echo this value";',
            '+    const docs = "wp_nav_menu() renders navigation";',
            "+    // Example: echo build_custom_navigation();",
            "+    // wp_nav_menu() renders navigation.",
            "+    /* <button>Example</button> */",
            "+    $events->emit( 'order.created', $order );",
            "+    $stream->output( $bytes );",
        ):
            assert not review_scope.patch_has_markup_tokens(line), line

    def test_presentational_containers_and_generics_are_not_markup(self):
        """div/span/p stay outside the vocabulary (presentational
        containers), and TS generics must not read as tags."""
        for line in (
            "+ <div className=\"wrap\">",
            "+ <span>total</span>",
            "+ const rows: Promise<number> = load();",
            "+ const opts: Map<string, Order> = new Map();",
        ):
            assert not review_scope.patch_has_markup_tokens(line), line


class TestPhtmlIsExecutableCode:
    """.phtml is executable PHP in a template costume — it must sit in the
    general code domains, not only in a11y's markup class (round-12 P1: a
    pure-logic .phtml diff got NO code/security reviewer and no
    unrecognized-source warning because only the a11y domain saw it)."""

    def test_phtml_in_prog_langs(self):
        assert "phtml" in review_scope._PROG_LANGS
        assert "phtml" in review_scope._MARKUP_LANGS  # both roles

    @pytest.mark.parametrize("domain", ["code", "security", "performance"])
    def test_phtml_matches_code_domains(self, domain):
        include = review_scope.DOMAIN_CATALOG[domain]["include"]
        assert re.search(include, "templates/order-row.phtml"), domain


class TestTemplateFileClassification:
    @pytest.mark.parametrize(
        "filepath",
        [
            "views/cart.ejs",
            "templates/page.liquid",
            "views/page.njk",
            "views/page.nunjucks",
            "templates/page.jinja",
            "templates/page.jinja2",
            "templates/page.j2",
            "views/index.jsp",
            "views/index.jspx",
            "Views/Cart.cshtml",
            "Views/Cart.vbhtml",
            "Components/NavMenu.razor",
            "templates/email.tmpl",
            "templates/email.tpl",
            "views/page.gsp",
            "views/page.ftl",
            "views/page.vm",
            "views/page.haml",
            "views/page.slim",
            "resources/views/cart.blade.php",
        ],
    )
    def test_common_server_template_is_inherent_ui(self, filepath):
        assert review_scope.is_template_file(filepath)
        matched, _ = review_scope.filter_domain([filepath], "a11y")
        assert matched == [filepath]


class TestEvidenceScanPathHandling:
    def test_git_quoted_header_paths_are_decoded(self):
        """Git C-quotes non-ASCII paths ('diff --git "a/x" "b/x"' with
        literal backslash-octal escapes) — the evidence scan must still
        attribute tokens to the right file."""
        q = "templates/" + "\\303\\274" + "bersicht.php"  # literal \303\274 escapes
        raw = "templates/\u00fcbersicht.php"
        patch_text = (
            f'diff --git "a/{q}" "b/{q}"\n'
            f'--- "a/{q}"\n'
            f'+++ "b/{q}"\n'
            "@@ -1,0 +1,1 @@\n"
            "+<button>OK</button>"
        )
        with patch.object(review_scope, "run_cmd", return_value=patch_text):
            evidence = review_scope.classify_markup_evidence("abc..HEAD", [raw])
        assert evidence == {raw}

    def test_unparseable_header_degrades_to_non_evidence(self):
        """A header we cannot parse must never crash or misattribute —
        the file just lands in the non-evidence tier."""
        patch_text = "diff --git gibberish header\n+<button>OK</button>"
        with patch.object(review_scope, "run_cmd", return_value=patch_text):
            evidence = review_scope.classify_markup_evidence("abc..HEAD", ["a.php"])
        assert evidence == set()

    def test_space_containing_path_attributes_correctly(self):
        """Git does NOT quote ordinary spaces, so the two-path 'diff --git'
        line is ambiguous for a path containing ' b/' — the single-path
        '+++ b/...' marker is the reliable source."""
        raw = "foo b/form.php"
        patch_text = (
            f"diff --git a/{raw} b/{raw}\n"
            f"--- a/{raw}\n"
            f"+++ b/{raw}\n"
            "@@ -1,0 +1,1 @@\n"
            "+<button>OK</button>"
        )
        with patch.object(review_scope, "run_cmd", return_value=patch_text):
            evidence = review_scope.classify_markup_evidence("abc..HEAD", [raw])
        assert evidence == {raw}

    def test_deleted_file_attributes_via_old_side_marker(self):
        """A deletion has '+++ /dev/null'; its removed markup lines must
        attribute to the '--- a/...' path."""
        patch_text = (
            "diff --git a/templates/form.php b/templates/form.php\n"
            "deleted file mode 100644\n"
            "--- a/templates/form.php\n"
            "+++ /dev/null\n"
            "@@ -1,1 +0,0 @@\n"
            "-<label for=\"email\">Email</label>"
        )
        with patch.object(review_scope, "run_cmd", return_value=patch_text):
            evidence = review_scope.classify_markup_evidence(
                "abc..HEAD", ["templates/form.php"]
            )
        assert evidence == {"templates/form.php"}

    def test_dash_dash_content_line_is_not_a_file_marker(self):
        """A removed line whose content starts with '--' renders as '---...'
        — inside a hunk that is content, not a marker, and markup in it is
        evidence for the current file."""
        patch_text = (
            "diff --git a/templates/list.php b/templates/list.php\n"
            "--- a/templates/list.php\n"
            "+++ b/templates/list.php\n"
            "@@ -1,2 +1,1 @@\n"
            "--- <button>Remove</button> rendered per row\n"
            " <?php endforeach; ?>"
        )
        with patch.object(review_scope, "run_cmd", return_value=patch_text):
            evidence = review_scope.classify_markup_evidence(
                "abc..HEAD", ["templates/list.php"]
            )
        assert evidence == {"templates/list.php"}

    def test_evidence_scan_disables_git_path_quoting(self):
        """The scan command itself must ask git for raw paths."""
        captured = {}
        def fake_run(cmd, check=True, capture_stderr=True):
            captured["cmd"] = cmd
            return ""
        with patch.object(review_scope, "run_cmd", side_effect=fake_run):
            review_scope.classify_markup_evidence("abc..HEAD", ["a.php"])
        assert "core.quotepath=false" in " ".join(captured["cmd"])


# =============================================================================
# Raw-size pre-skip vs semantic filtering
# =============================================================================


def _docblock_heavy_diff(path, doc_lines, code_lines):
    body = (
        ["+/**"]
        + [f"+ * Documentation line {i}." for i in range(doc_lines - 2)]
        + ["+ */"]
        + [f"+$code_{i} = {i};" for i in range(code_lines)]
    )
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,0 +1,{doc_lines + code_lines} @@\n" + "\n".join(body)
    )


_PRESKIP_FILES = {
    # Both raw sizes exceed the 2,000-line budget; both filter down to code.
    "includes/class-alpha.php": (2100, _docblock_heavy_diff("includes/class-alpha.php", 2050, 50)),
    "includes/class-beta.php": (2050, _docblock_heavy_diff("includes/class-beta.php", 2040, 10)),
}


def _mock_git_for_preskip(cmd, check=True, capture_stderr=True):
    cmd_str = " ".join(cmd)
    if "rev-parse --git-dir" in cmd_str:
        return ".git"
    if "rev-parse" in cmd_str:
        return "abc123"
    if "--name-only" in cmd_str:
        return "\n".join(_PRESKIP_FILES)
    if "--numstat" in cmd_str:
        return "\n".join(f"{raw}\t0\t{f}" for f, (raw, _) in _PRESKIP_FILES.items())
    if "merge-base" in cmd_str:
        return "abc123"
    if "rev-list --count" in cmd_str:
        return "0"
    if "diff" in cmd_str and "--" in cmd:
        requested = cmd[cmd.index("--") + 1:]
        if len(requested) == 1 and requested[0] in _PRESKIP_FILES:
            return _PRESKIP_FILES[requested[0]][1]
        return "\n".join(_PRESKIP_FILES[f][1] for f in requested if f in _PRESKIP_FILES)
    return ""


class TestRawSizePreSkip:
    """Raw diffstat size only proves un-fittability when semantic filtering
    is OFF. A 2,050-line patch that is 2,040 docblock lines filters to 10
    reviewable lines — rejecting it unfetched would silently omit code that
    fits comfortably."""

    def _build(self, tmp_path, no_semantic_filter):
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x):
            mock_run.side_effect = _mock_git_for_preskip
            args = argparse.Namespace(
                domain="code", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=no_semantic_filter,
            )
            return review_scope.build_scope(args)

    def test_filtering_enabled_measures_before_rejecting(self, tmp_path):
        scope = self._build(tmp_path, no_semantic_filter=False)
        # alpha filters to ~50 lines, beta to ~10 — both fit the 2,000 budget.
        assert "includes/class-alpha.php" in scope["diffs"]
        assert "includes/class-beta.php" in scope["diffs"]
        assert scope["skipped_files"]["budget"] == []

    def test_filtering_disabled_keeps_the_cheap_pre_skip(self, tmp_path):
        scope = self._build(tmp_path, no_semantic_filter=True)
        # Raw == effective size here: alpha (2,100) is included as the first
        # file; beta's raw 2,050 >= the whole budget with diffs present —
        # rejected without a fetch.
        assert "includes/class-beta.php" in scope["skipped_files"]["budget"]


# =============================================================================
# List-only tests — lock files for toolchain domain
# =============================================================================


def _mock_git_for_list_only_test(cmd, check=True, capture_stderr=True):
    """Mock git commands for list-only (lock file rescue) testing.

    Simulates a diff with both config files and lock files.
    """
    cmd_str = " ".join(cmd)
    if "rev-parse --git-dir" in cmd_str:
        return ".git"
    if "rev-parse" in cmd_str:
        return "abc123"
    if "--name-only" in cmd_str:
        return ".npmrc\npackage.json\npnpm-lock.yaml\ncomposer.lock\nsrc/app.php"
    if "--numstat" in cmd_str:
        return "5\t2\t.npmrc\n10\t3\tpackage.json\n500\t200\tpnpm-lock.yaml\n300\t100\tcomposer.lock\n50\t20\tsrc/app.php"
    if "merge-base" in cmd_str:
        return "abc123"
    if "rev-list --count" in cmd_str:
        return "0"
    if "diff" in cmd_str and "-- .npmrc" in cmd_str:
        return "+registry=https://registry.npmjs.org/"
    if "diff" in cmd_str and "-- package.json" in cmd_str:
        return '+  "engines": {"node": ">=20"}'
    if "diff" in cmd_str and "-- pnpm-lock.yaml" in cmd_str:
        return "\n".join([f"+lockfile-line-{i}" for i in range(500)])
    if "diff" in cmd_str and "-- composer.lock" in cmd_str:
        return "\n".join([f"+composer-line-{i}" for i in range(300)])
    if "diff" in cmd_str and "-- src/app.php" in cmd_str:
        return "+<?php echo 'hello';"
    return ""


class TestListOnly:
    """Tests for list_only domain feature — lock files rescued from noise, diffstat included, diff skipped."""

    def test_lock_files_rescued_from_noise_for_toolchain(self, tmp_path):
        """Lock files normally caught by NOISE_PATTERNS should survive when domain has list_only."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x):
            mock_run.side_effect = _mock_git_for_list_only_test
            args = argparse.Namespace(
                domain="toolchain", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)
            assert scope["status"] == "OK"
            # Lock files should be in list_only_files, not in noise_skipped
            assert "pnpm-lock.yaml" in scope["list_only_files"]
            assert "composer.lock" in scope["list_only_files"]
            assert "pnpm-lock.yaml" not in scope["skipped_files"]["noise"]
            assert "composer.lock" not in scope["skipped_files"]["noise"]

    def test_lock_files_have_diffstat_but_no_diff(self, tmp_path):
        """List-only files appear in diffstat but not in diffs dict."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x):
            mock_run.side_effect = _mock_git_for_list_only_test
            args = argparse.Namespace(
                domain="toolchain", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)
            # Diffstat should include lock files
            assert "pnpm-lock.yaml" in scope["diffstat"]
            assert "composer.lock" in scope["diffstat"]
            # But their diffs should NOT be fetched
            assert "pnpm-lock.yaml" not in scope["diffs"]
            assert "composer.lock" not in scope["diffs"]

    def test_config_files_still_get_full_diffs(self, tmp_path):
        """Non-list-only files in the same domain still get their full diffs."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x):
            mock_run.side_effect = _mock_git_for_list_only_test
            args = argparse.Namespace(
                domain="toolchain", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)
            assert ".npmrc" in scope["diffs"]
            assert "package.json" in scope["diffs"]
            assert ".npmrc" in scope["files"]
            assert "package.json" in scope["files"]

    def test_lock_files_not_in_files_key(self, tmp_path):
        """In regular mode, files key only contains files with diffs."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x):
            mock_run.side_effect = _mock_git_for_list_only_test
            args = argparse.Namespace(
                domain="toolchain", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)
            assert "pnpm-lock.yaml" not in scope["files"]
            assert "composer.lock" not in scope["files"]

    def test_list_only_in_skipped_files(self, tmp_path):
        """List-only files should also appear in skipped_files.list_only."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x):
            mock_run.side_effect = _mock_git_for_list_only_test
            args = argparse.Namespace(
                domain="toolchain", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)
            assert scope["skipped_files"]["list_only"] == scope["list_only_files"]

    def test_non_toolchain_domain_still_filters_lock_files_as_noise(self, tmp_path):
        """Lock files should remain noise for domains without list_only."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x):
            mock_run.side_effect = _mock_git_for_list_only_test
            args = argparse.Namespace(
                domain="code", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)
            # Lock files should be in noise_skipped for non-toolchain domains
            assert "pnpm-lock.yaml" in scope["skipped_files"]["noise"]
            assert "composer.lock" in scope["skipped_files"]["noise"]

    def test_list_only_text_output_section(self, tmp_path):
        """Text output should include a CHANGED section for list-only files."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x):
            mock_run.side_effect = _mock_git_for_list_only_test
            args = argparse.Namespace(
                domain="toolchain", range="abc123..HEAD", max_lines=2000,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)
            text = review_scope.format_text_output(scope)
            assert "CHANGED (no diff" in text
            assert "pnpm-lock.yaml" in text
            assert "composer.lock" in text
            assert "LIST_ONLY_FILES: 2" in text

    def test_lock_files_dont_eat_diff_budget(self, tmp_path):
        """List-only files should not consume any of the diff line budget."""
        with patch.object(review_scope, 'run_cmd') as mock_run, \
             patch.object(review_scope, 'freshen_base_ref', side_effect=lambda x: x):
            mock_run.side_effect = _mock_git_for_list_only_test
            args = argparse.Namespace(
                domain="toolchain", range="abc123..HEAD", max_lines=50,
                base_ref_only=False, summary=False, output_dir=str(tmp_path),
                no_merge_base=True, no_semantic_filter=True,
            )
            scope = review_scope.build_scope(args)
            # Even with a tiny budget, lock files don't consume it
            assert "pnpm-lock.yaml" in scope["list_only_files"]
            # Config files should still get budget allocation
            assert scope["files_with_diffs"] > 0


class TestNotDiffedWorkQueueFraming:
    """NOT DIFFED must read as a mandatory work queue, not an optional appendix.

    Observed 2026-07-21 on a 349-file branch: agents used 37% of their tool
    budget and treated 'read any of these selectively' as license to skip the
    largest changed files entirely.
    """

    def test_not_diffed_section_frames_a_work_queue(self):
        scope = {
            "status": "OK",
            "range": "abc123..HEAD",
            "files": ["src/a.ts"],
            "diffstat": {"src/a.ts": (10, 2), "src/big.ts": (862, 0)},
            "diffs": {"src/a.ts": "+x"},
            "skipped_files": {"budget": ["src/big.ts"]},
        }
        text = review_scope.format_text_output(scope)
        assert "=== NOT DIFFED (budget exceeded, 1 files) ===" in text
        assert "ARE IN YOUR SCOPE" in text
        assert "work queue" in text
        assert "selectively" not in text

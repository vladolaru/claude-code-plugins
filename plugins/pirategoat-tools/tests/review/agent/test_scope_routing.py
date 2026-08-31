"""
Domain routing tests — verify review/agent/scope.py routes fixtures to correct domains.

Deterministic pytest suite. For each fixture, creates a temp git repo, applies the
diff, and runs review/agent/scope.py --domain <X> for all 11 domains. Asserts STATUS is
OK or NO_DOMAIN_FILES.

Also tests --preflight mode which checks all domains in one invocation.

Zero model calls.
"""

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent.parent.parent  # agent/ -> review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
REVIEW_SCOPE_SCRIPT = SCRIPTS_DIR / "review" / "agent" / "scope.py"
FIXTURES_DIR = TESTS_DIR / "fixtures"

# Import review/agent/scope.py functions directly for domain routing tests.
# filter_noise() and filter_domain() are pure functions — no subprocess needed.
sys.path.insert(0, str(SCRIPTS_DIR))
_scope_spec = importlib.util.spec_from_file_location(
    "review_scope", str(REVIEW_SCOPE_SCRIPT)
)
_review_scope = importlib.util.module_from_spec(_scope_spec)
_scope_spec.loader.exec_module(_review_scope)

ALL_DOMAINS = [
    "a11y",
    "architecture",
    "code",
    "config-ops",
    "dead-code",
    "e2e-tests",
    "go-tests",
    "js-tests",
    "patterns",
    "performance",
    "php-tests",
    "reliability",
    "security",
    "wp-architecture",
]

# ---------------------------------------------------------------------------
# Routing matrix: fixture -> {domain: expected_status}
#
# "OK"              = domain filter matches at least one file
# "NO_DOMAIN_FILES" = domain filter excludes all files in the diff
# ---------------------------------------------------------------------------
ROUTING_MATRIX = {
    "php-source.diff": {
        "a11y": "OK",  # PHP renders server-side markup — a11y domain includes it
        "code": "OK",
        "config-ops": "NO_DOMAIN_FILES",
        "dead-code": "OK",
        "reliability": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",
        "wp-architecture": "OK",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "go-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "js-ts-source.diff": {
        "a11y": "OK",
        "code": "OK",
        "config-ops": "NO_DOMAIN_FILES",
        "dead-code": "OK",
        "reliability": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",
        "wp-architecture": "OK",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "go-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "php-test-only.diff": {
        "a11y": "OK",  # .php matches the a11y markup-language group (no test exclude)
        "code": "OK",
        "config-ops": "NO_DOMAIN_FILES",
        "dead-code": "NO_DOMAIN_FILES",
        "reliability": "NO_DOMAIN_FILES",
        "security": "OK",
        "performance": "OK",
        "architecture": "NO_DOMAIN_FILES",
        "wp-architecture": "OK",
        "php-tests": "OK",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "go-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "js-test-only.diff": {
        "a11y": "OK",
        "code": "OK",
        "config-ops": "NO_DOMAIN_FILES",
        "dead-code": "NO_DOMAIN_FILES",
        "reliability": "NO_DOMAIN_FILES",
        "security": "OK",
        "performance": "OK",
        "architecture": "NO_DOMAIN_FILES",
        "wp-architecture": "OK",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "OK",
        "e2e-tests": "NO_DOMAIN_FILES",
        "go-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "e2e-test-only.diff": {
        "a11y": "OK",
        "code": "OK",
        "config-ops": "NO_DOMAIN_FILES",
        "dead-code": "OK",  # CheckoutPage.ts survives (no test/spec in filename)
        "reliability": "OK",  # CheckoutPage.ts survives (no test/spec in path or filename)
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",  # CheckoutPage.ts survives (no test/spec in filename)
        "wp-architecture": "OK",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",  # spec.ts excluded by e2e/ prefix
        "e2e-tests": "OK",
        "go-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "go-test-only.diff": {
        "a11y": "NO_DOMAIN_FILES",
        "code": "OK",
        "config-ops": "NO_DOMAIN_FILES",
        "dead-code": "NO_DOMAIN_FILES",  # _test.go excluded
        "reliability": "NO_DOMAIN_FILES",  # _test.go excluded by _TEST_EXCLUDE
        "security": "OK",
        "performance": "OK",
        "architecture": "NO_DOMAIN_FILES",  # _test.go contains "test" → excluded
        "wp-architecture": "NO_DOMAIN_FILES",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "go-tests": "OK",
        "patterns": "OK",
    },
    "go-source.diff": {
        "a11y": "NO_DOMAIN_FILES",
        "code": "OK",
        "config-ops": "NO_DOMAIN_FILES",
        "dead-code": "OK",
        "reliability": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",
        "wp-architecture": "NO_DOMAIN_FILES",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "go-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "mixed-code-and-tests.diff": {
        "a11y": "OK",  # src/cart.test.ts matches .ts extension
        "code": "OK",
        "config-ops": "NO_DOMAIN_FILES",
        "dead-code": "OK",  # src/Cart.php survives; tests/CartTest.php and src/cart.test.ts excluded
        "reliability": "OK",  # src/Cart.php survives; tests/ and .test. excluded
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",  # src/Cart.php survives
        "wp-architecture": "OK",
        "php-tests": "OK",
        "js-tests": "OK",
        "e2e-tests": "NO_DOMAIN_FILES",
        "go-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "wp-hooks-and-i18n.diff": {
        "a11y": "OK",  # PHP renders server-side markup — a11y domain includes it
        "code": "OK",
        "config-ops": "NO_DOMAIN_FILES",
        "dead-code": "OK",
        "reliability": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",
        "wp-architecture": "OK",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "go-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "multi-file-realistic.diff": {
        "a11y": "OK",  # .css, .tsx, .spec.ts, .test.tsx all match
        "code": "OK",
        "config-ops": "NO_DOMAIN_FILES",
        "dead-code": "OK",  # production files survive; test files excluded
        "reliability": "OK",  # production files survive; __tests__/, .spec., tests/, Test.php excluded
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",
        "wp-architecture": "OK",
        "php-tests": "OK",
        "js-tests": "OK",
        "e2e-tests": "OK",
        "go-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "ci-config-changes.diff": {
        "a11y": "NO_DOMAIN_FILES",
        "code": "NO_DOMAIN_FILES",
        "config-ops": "OK",
        "dead-code": "NO_DOMAIN_FILES",
        "reliability": "NO_DOMAIN_FILES",
        "security": "NO_DOMAIN_FILES",
        "performance": "NO_DOMAIN_FILES",
        "architecture": "NO_DOMAIN_FILES",
        "wp-architecture": "NO_DOMAIN_FILES",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "go-tests": "NO_DOMAIN_FILES",
        "patterns": "NO_DOMAIN_FILES",
    },
    "no-code-changes.diff": {
        "a11y": "NO_DOMAIN_FILES",
        "code": "NO_DOMAIN_FILES",
        "config-ops": "NO_DOMAIN_FILES",
        "dead-code": "NO_DOMAIN_FILES",
        "reliability": "NO_DOMAIN_FILES",
        "security": "NO_DOMAIN_FILES",
        "performance": "NO_DOMAIN_FILES",
        "architecture": "NO_DOMAIN_FILES",
        "wp-architecture": "NO_DOMAIN_FILES",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "go-tests": "NO_DOMAIN_FILES",
        "patterns": "NO_DOMAIN_FILES",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
sys.path.insert(0, str(TESTS_DIR))
from conftest import setup_temp_git_repo


def run_review_scope(domain: str, cwd: str) -> str:
    """Run review/agent/scope.py and extract STATUS from output.

    Uses --range HEAD~1..HEAD since temp repos have no remote and changes
    are committed (auto-detection would see a clean working tree).
    """
    result = subprocess.run(
        [
            sys.executable, str(REVIEW_SCOPE_SCRIPT),
            "--domain", domain,
            "--range", "HEAD~1..HEAD",
        ],
        cwd=cwd, capture_output=True, text=True, timeout=30,
    )

    for line in result.stdout.splitlines():
        if line.startswith("STATUS:"):
            return line.split(":", 1)[1].strip()

    return f"UNKNOWN (exit={result.returncode}, stderr={result.stderr[:200]})"


# ---------------------------------------------------------------------------
# Parametrized test
# ---------------------------------------------------------------------------
def _make_params():
    """Generate (fixture, domain, expected_status) for parametrize."""
    params = []
    for fixture_name, domain_map in sorted(ROUTING_MATRIX.items()):
        for domain in ALL_DOMAINS:
            expected = domain_map[domain]
            params.append(
                pytest.param(
                    fixture_name, domain, expected,
                    id=f"{fixture_name.replace('.diff', '')}--{domain}",
                )
            )
    return params


# Cache repos per fixture to avoid re-creating for each domain
_repo_cache: dict = {}


@pytest.fixture(autouse=True, scope="module")
def cleanup_repos():
    """Clean up all cached repos after the module finishes."""
    yield
    for path in _repo_cache.values():
        shutil.rmtree(path, ignore_errors=True)
    _repo_cache.clear()
    _file_list_cache.clear()


def _get_repo(fixture_name: str) -> str:
    """Get or create a cached temp repo for a fixture."""
    if fixture_name not in _repo_cache:
        diff_path = FIXTURES_DIR / fixture_name
        assert diff_path.is_file(), f"Fixture not found: {diff_path}"
        _repo_cache[fixture_name] = setup_temp_git_repo(str(diff_path))
    return _repo_cache[fixture_name]


_file_list_cache: dict = {}


def _get_changed_files(fixture_name: str) -> list:
    """Get changed files for a fixture via git. Cached per fixture."""
    if fixture_name not in _file_list_cache:
        repo = _get_repo(fixture_name)
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1..HEAD"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        _file_list_cache[fixture_name] = [
            f for f in result.stdout.strip().split("\n") if f
        ]
    return _file_list_cache[fixture_name]


@pytest.mark.parametrize("fixture_name, domain, expected_status", _make_params())
def test_domain_routing(fixture_name: str, domain: str, expected_status: str):
    """Verify filter_noise + filter_domain route each fixture to the correct domains.

    Uses direct function calls instead of subprocess. The routing logic
    IS filter_noise() + filter_domain() — testing them directly is testing
    the routing contract without 168 Python process spawns.
    """
    files = _get_changed_files(fixture_name)
    after_noise, _ = _review_scope.filter_noise(files)
    matched, _ = _review_scope.filter_domain(after_noise, domain)
    actual = "OK" if matched else "NO_DOMAIN_FILES"
    assert actual == expected_status, (
        f"Fixture {fixture_name}, domain {domain}: "
        f"expected STATUS={expected_status}, got STATUS={actual}"
    )



# ---------------------------------------------------------------------------
# Branch Freshness helpers
# ---------------------------------------------------------------------------
_freshness_repo_cache: dict = {}


@pytest.fixture(autouse=True, scope="module")
def cleanup_freshness_repos():
    """Clean up all cached freshness repos after the module finishes."""
    yield
    for path in _freshness_repo_cache.values():
        shutil.rmtree(path, ignore_errors=True)
    _freshness_repo_cache.clear()


def _setup_stale_branch_repo(behind_count: int) -> str:
    """Create a repo where main has advanced N commits past the branch point.

    Layout:
        initial commit (common ancestor)
          ├── main: N extra commits (each adding a .php file)
          └── feature: 1 commit (adding feature.php)

    Returns the repo path (cached by behind_count).
    """
    cache_key = f"stale-{behind_count}"
    if cache_key in _freshness_repo_cache:
        return _freshness_repo_cache[cache_key]

    tmp = tempfile.mkdtemp(prefix="test-freshness-")

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

    _freshness_repo_cache[cache_key] = tmp
    return tmp


# ---------------------------------------------------------------------------
# Server-template routing tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filepath",
    [
        "views/cart.ejs",
        "templates/page.liquid",
        "views/page.njk",
        "views/page.nunjucks",
        "templates/page.jinja2",
        "templates/page.j2",
        "views/index.jsp",
        "Views/Cart.cshtml",
        "Components/NavMenu.razor",
        "templates/email.tmpl",
        "resources/views/cart.blade.php",
    ],
)
def test_common_server_templates_route_to_a11y(filepath):
    matched, excluded = _review_scope.filter_domain([filepath], "a11y")
    assert matched == [filepath]
    assert excluded == []


# ---------------------------------------------------------------------------
# Branch Freshness tests
# ---------------------------------------------------------------------------
class TestBranchFreshness:
    """Tests for stale branch detection and merge-base range rebasing.

    Uses direct build_scope() calls with os.chdir to the temp repo.
    Results are cached by (repo, no_merge_base) — multiple tests asserting
    on different fields of the same scope reuse one build_scope() call.
    """

    _scope_cache: dict = {}

    @classmethod
    def _build_scope_in_repo(cls, repo, domain="code", range_spec="main..HEAD",
                             no_merge_base=False):
        """Call build_scope() directly, cached by (repo, no_merge_base)."""
        cache_key = (repo, no_merge_base)
        if cache_key in cls._scope_cache:
            return cls._scope_cache[cache_key]

        import argparse
        args = argparse.Namespace(
            domain=domain,
            range=range_spec,
            format="json",
            max_lines=2000,
            base_ref_only=False,
            summary=False,
            output_dir=os.path.join(repo, "review-output"),
            no_merge_base=no_merge_base,
            no_semantic_filter=False,
        )
        saved_cwd = os.getcwd()
        try:
            os.chdir(repo)
            scope = _review_scope.build_scope(args)
        finally:
            os.chdir(saved_cwd)
        cls._scope_cache[cache_key] = scope
        return scope

    @classmethod
    def teardown_class(cls):
        cls._scope_cache.clear()

    def _scope_json(self, repo, **kwargs):
        scope = self._build_scope_in_repo(repo, **kwargs)
        return json.loads(_review_scope.format_json_output(scope))

    def _scope_text(self, repo, **kwargs):
        scope = self._build_scope_in_repo(repo, **kwargs)
        return _review_scope.format_text_output(scope)

    def test_freshness_detects_stale_branch(self):
        """15 commits behind → is_stale: true, behind: 15."""
        repo = _setup_stale_branch_repo(15)
        data = self._scope_json(repo)
        bf = data["branch_freshness"]
        assert bf["is_stale"] is True
        assert bf["behind"] == 15

    def test_freshness_not_stale_when_close(self):
        """3 commits behind → is_stale: false."""
        repo = _setup_stale_branch_repo(3)
        data = self._scope_json(repo)
        bf = data["branch_freshness"]
        assert bf["is_stale"] is False

    def test_stale_branch_range_is_rebased_to_merge_base(self):
        """15 behind → RANGE_REBASED: true, only feature file in scope."""
        repo = _setup_stale_branch_repo(15)
        text = self._scope_text(repo)
        assert "RANGE_REBASED: true" in text
        # Only the feature file should be in scope (not trunk files)
        assert "FILES_CHANGED: 1" in text

    def test_no_merge_base_includes_trunk_files(self):
        """--no-merge-base → all trunk + feature files in scope."""
        repo = _setup_stale_branch_repo(15)
        text = self._scope_text(repo, no_merge_base=True)
        # 15 trunk .php files + 1 feature .php file = 16
        assert "FILES_CHANGED: 16" in text

    def test_freshness_in_json_output(self):
        """JSON output has branch_freshness with all expected fields."""
        repo = _setup_stale_branch_repo(15)
        data = self._scope_json(repo)
        assert "branch_freshness" in data
        bf = data["branch_freshness"]
        assert bf["ahead"] == 1
        assert bf["behind"] == 15
        assert bf["is_stale"] is True
        assert len(bf["merge_base"]) >= 7  # SHA
        assert bf["range_rebased"] is True

    def test_non_stale_branch_still_rebased(self):
        """3 behind → range_rebased: true (merge-base rebase is unconditional)."""
        repo = _setup_stale_branch_repo(3)
        data = self._scope_json(repo)
        bf = data["branch_freshness"]
        assert bf["is_stale"] is False
        assert bf["range_rebased"] is True

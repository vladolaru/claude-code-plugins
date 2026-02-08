"""
Domain routing tests — verify review-scope.py routes fixtures to correct domains.

Deterministic pytest suite. For each fixture, creates a temp git repo, applies the
diff, and runs review-scope.py --domain <X> for all 9 domains. Asserts STATUS is
OK or NO_DOMAIN_FILES.

Zero model calls.
"""

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
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
REVIEW_SCOPE_SCRIPT = SCRIPTS_DIR / "review-scope.py"
FIXTURES_DIR = TESTS_DIR / "fixtures"

ALL_DOMAINS = [
    "architecture",
    "code",
    "e2e-tests",
    "js-tests",
    "patterns",
    "performance",
    "php-tests",
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
        "code": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",
        "wp-architecture": "OK",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "js-ts-source.diff": {
        "code": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",
        "wp-architecture": "OK",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "php-test-only.diff": {
        "code": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "NO_DOMAIN_FILES",
        "wp-architecture": "OK",
        "php-tests": "OK",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "js-test-only.diff": {
        "code": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "NO_DOMAIN_FILES",
        "wp-architecture": "OK",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "OK",
        "e2e-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "e2e-test-only.diff": {
        "code": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",  # CheckoutPage.ts survives (no test/spec in filename)
        "wp-architecture": "OK",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",  # spec.ts excluded by e2e/ prefix
        "e2e-tests": "OK",
        "patterns": "OK",
    },
    "mixed-code-and-tests.diff": {
        "code": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",  # src/Cart.php survives
        "wp-architecture": "OK",
        "php-tests": "OK",
        "js-tests": "OK",
        "e2e-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "wp-hooks-and-i18n.diff": {
        "code": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",
        "wp-architecture": "OK",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "patterns": "OK",
    },
    "multi-file-realistic.diff": {
        "code": "OK",
        "security": "OK",
        "performance": "OK",
        "architecture": "OK",
        "wp-architecture": "OK",
        "php-tests": "OK",
        "js-tests": "OK",
        "e2e-tests": "OK",
        "patterns": "OK",
    },
    "no-code-changes.diff": {
        "code": "NO_DOMAIN_FILES",
        "security": "NO_DOMAIN_FILES",
        "performance": "NO_DOMAIN_FILES",
        "architecture": "NO_DOMAIN_FILES",
        "wp-architecture": "NO_DOMAIN_FILES",
        "php-tests": "NO_DOMAIN_FILES",
        "js-tests": "NO_DOMAIN_FILES",
        "e2e-tests": "NO_DOMAIN_FILES",
        "patterns": "NO_DOMAIN_FILES",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def setup_temp_git_repo(diff_file: str) -> str:
    """Create a temp git repo and apply a diff. Returns repo path."""
    tmp = tempfile.mkdtemp(prefix="test-routing-")
    subprocess.run(["git", "init"], cwd=tmp, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=tmp, capture_output=True, check=True,
    )

    # Initial commit
    readme = os.path.join(tmp, "README.md")
    with open(readme, "w") as f:
        f.write("# Test Project\n")
    subprocess.run(["git", "add", "."], cwd=tmp, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp, capture_output=True, check=True,
    )

    # Apply diff
    result = subprocess.run(
        ["git", "apply", str(diff_file)],
        cwd=tmp, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"git apply failed for {Path(diff_file).name}: {result.stderr}"
    )

    subprocess.run(["git", "add", "."], cwd=tmp, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "changes"],
        cwd=tmp, capture_output=True, check=True,
    )

    return tmp


def run_review_scope(domain: str, cwd: str) -> str:
    """Run review-scope.py and extract STATUS from output.

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


def _get_repo(fixture_name: str) -> str:
    """Get or create a cached temp repo for a fixture."""
    if fixture_name not in _repo_cache:
        diff_path = FIXTURES_DIR / fixture_name
        assert diff_path.is_file(), f"Fixture not found: {diff_path}"
        _repo_cache[fixture_name] = setup_temp_git_repo(str(diff_path))
    return _repo_cache[fixture_name]


@pytest.mark.parametrize("fixture_name, domain, expected_status", _make_params())
def test_domain_routing(fixture_name: str, domain: str, expected_status: str):
    """Verify review-scope.py routes each fixture to the correct domains."""
    repo = _get_repo(fixture_name)
    actual = run_review_scope(domain, repo)
    assert actual == expected_status, (
        f"Fixture {fixture_name}, domain {domain}: "
        f"expected STATUS={expected_status}, got STATUS={actual}"
    )

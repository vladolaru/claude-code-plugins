"""
Domain routing tests — verify review-scope.py routes fixtures to correct domains.

Deterministic pytest suite. For each fixture, creates a temp git repo, applies the
diff, and runs review-scope.py --domain <X> for all 10 domains. Asserts STATUS is
OK or NO_DOMAIN_FILES.

Also tests --preflight mode which checks all domains in one invocation.

Zero model calls.
"""

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
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
REVIEW_SCOPE_SCRIPT = SCRIPTS_DIR / "review-scope.py"
FIXTURES_DIR = TESTS_DIR / "fixtures"

ALL_DOMAINS = [
    "architecture",
    "code",
    "dead-code",
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
        "dead-code": "OK",
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
        "dead-code": "OK",
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
        "dead-code": "NO_DOMAIN_FILES",
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
        "dead-code": "NO_DOMAIN_FILES",
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
        "dead-code": "OK",  # CheckoutPage.ts survives (no test/spec in filename)
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
        "dead-code": "OK",  # src/Cart.php survives; tests/CartTest.php and src/cart.test.ts excluded
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
        "dead-code": "OK",
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
        "dead-code": "OK",  # production files survive; test files excluded
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
        "dead-code": "NO_DOMAIN_FILES",
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


# ---------------------------------------------------------------------------
# Preflight helpers
# ---------------------------------------------------------------------------
def run_preflight(cwd: str, fmt: str = "text", range_spec: str = "HEAD~1..HEAD") -> subprocess.CompletedProcess:
    """Run review-scope.py --preflight and return the CompletedProcess."""
    cmd = [sys.executable, str(REVIEW_SCOPE_SCRIPT), "--preflight", "--range", range_spec]
    if fmt == "json":
        cmd.extend(["--format", "json"])
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)


def parse_preflight_text(output: str) -> dict:
    """Parse preflight text output into {domain: file_count} dict."""
    result = {}
    in_domain_status = False
    for line in output.splitlines():
        if line.strip() == "DOMAIN_STATUS:":
            in_domain_status = True
            continue
        if in_domain_status:
            line = line.strip()
            if not line or line.startswith("DISPATCH_DOMAINS:") or line.startswith("SKIP_DOMAINS:"):
                in_domain_status = False
                continue
            # Parse "domain_name: STATUS (N files)"
            parts = line.split(":", 1)
            if len(parts) == 2:
                domain = parts[0].strip()
                rest = parts[1].strip()
                # Extract file count from "OK (12 files)" or "NO_FILES (0 files)"
                paren_start = rest.find("(")
                paren_end = rest.find(" files)")
                if paren_start != -1 and paren_end != -1:
                    count = int(rest[paren_start + 1:paren_end])
                    result[domain] = count
    return result


# ---------------------------------------------------------------------------
# Preflight tests
# ---------------------------------------------------------------------------
class TestPreflight:
    """Tests for --preflight mode of review-scope.py."""

    def test_preflight_text_output_format(self):
        """Preflight output contains expected sections."""
        repo = _get_repo("mixed-code-and-tests.diff")
        result = run_preflight(repo)
        assert result.returncode == 0
        assert "=== PREFLIGHT SCOPE CHECK ===" in result.stdout
        assert "DISPATCH_DOMAINS:" in result.stdout
        assert "SKIP_DOMAINS:" in result.stdout
        assert "DOMAIN_STATUS:" in result.stdout
        assert "RANGE:" in result.stdout
        assert "FILES_CHANGED:" in result.stdout
        assert "NOISE_SKIPPED:" in result.stdout
        assert "REVIEWABLE_FILES:" in result.stdout

    def test_preflight_json_output(self):
        """Preflight JSON output has expected structure."""
        repo = _get_repo("mixed-code-and-tests.diff")
        result = run_preflight(repo, fmt="json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "dispatch_domains" in data
        assert "skip_domains" in data
        assert "domains" in data
        assert "range" in data
        assert "files_changed" in data
        assert "noise_skipped" in data
        assert "reviewable_files" in data
        # Each domain entry has status and file_count
        for domain_name, info in data["domains"].items():
            assert "status" in info
            assert "file_count" in info
            assert info["status"] in ("OK", "NO_FILES")

    def test_preflight_no_domain_required(self):
        """--preflight works without --domain argument."""
        repo = _get_repo("php-source.diff")
        result = run_preflight(repo)
        assert result.returncode == 0
        assert "ERROR" not in result.stdout

    def test_preflight_with_range(self):
        """--preflight works with --range argument."""
        repo = _get_repo("php-source.diff")
        result = run_preflight(repo, range_spec="HEAD~1..HEAD")
        assert result.returncode == 0
        assert "PREFLIGHT" in result.stdout

    def test_preflight_matches_individual_domain_checks(self):
        """Preflight results must match running --domain X individually for each domain."""
        for fixture_name, domain_map in sorted(ROUTING_MATRIX.items()):
            repo = _get_repo(fixture_name)
            result = run_preflight(repo)
            assert result.returncode == 0, (
                f"Preflight failed for {fixture_name}: {result.stderr}"
            )
            preflight = parse_preflight_text(result.stdout)

            for domain, expected_status in domain_map.items():
                file_count = preflight.get(domain, -1)
                preflight_status = "OK" if file_count > 0 else "NO_DOMAIN_FILES"
                assert preflight_status == expected_status, (
                    f"Fixture {fixture_name}, domain {domain}: "
                    f"preflight says {preflight_status} ({file_count} files), "
                    f"individual check says {expected_status}"
                )

    def test_preflight_no_code_changes_all_skip(self):
        """When only non-code files changed, all domains should be in SKIP."""
        repo = _get_repo("no-code-changes.diff")
        result = run_preflight(repo)
        assert result.returncode == 0
        preflight = parse_preflight_text(result.stdout)
        assert all(count == 0 for count in preflight.values()), (
            f"Expected all domains to have 0 files, got: {preflight}"
        )

    def test_preflight_dispatch_skip_consistency(self):
        """DISPATCH_DOMAINS and SKIP_DOMAINS should cover all domains exactly once."""
        repo = _get_repo("multi-file-realistic.diff")
        result = run_preflight(repo, fmt="json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        all_domains_from_results = set(data["domains"].keys())
        dispatch_set = set(data["dispatch_domains"])
        skip_set = set(data["skip_domains"])
        # No overlap
        assert dispatch_set & skip_set == set(), "Overlap between dispatch and skip"
        # Complete coverage
        assert dispatch_set | skip_set == all_domains_from_results, (
            f"Missing domains: {all_domains_from_results - dispatch_set - skip_set}"
        )

    def test_preflight_json_domains_cover_all_catalog_domains(self):
        """Preflight JSON should report on every domain in DOMAIN_CATALOG."""
        repo = _get_repo("php-source.diff")
        result = run_preflight(repo, fmt="json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        reported_domains = set(data["domains"].keys())
        assert reported_domains == set(ALL_DOMAINS), (
            f"Preflight domains {reported_domains} != catalog domains {set(ALL_DOMAINS)}"
        )

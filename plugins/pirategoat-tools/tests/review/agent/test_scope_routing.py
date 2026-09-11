"""
Domain routing tests — verify review/agent/scope.py routes fixtures to correct domains.

For each fixture diff, reads the changed-file list straight from its `+++ b/`
headers (every fixture here is add/modify only) and asserts the whole
14-domain routing vector that filter_noise() + filter_domain() produce for
it. One row per fixture pins the complete per-fixture contract; no temp git
repo is needed since the fixtures are never actually applied.

Zero model calls.
"""

import importlib
import importlib.util
import sys
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
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fixture_files(fixture_name: str) -> list[str]:
    text = (FIXTURES_DIR / fixture_name).read_text()
    return [line[len("+++ b/"):].strip() for line in text.splitlines() if line.startswith("+++ b/")]


@pytest.mark.parametrize(
    "fixture_name", sorted(ROUTING_MATRIX),
    ids=[f.replace(".diff", "") for f in sorted(ROUTING_MATRIX)],
)
def test_domain_routing(fixture_name: str):
    """filter_noise + filter_domain route each fixture to exactly the
    expected domain vector; one row per fixture, all domains asserted."""
    files = _fixture_files(fixture_name)
    assert files, f"no +++ b/ headers in {fixture_name}"
    after_noise, _ = _review_scope.filter_noise(files)
    actual = {
        domain: ("OK" if _review_scope.filter_domain(after_noise, domain)[0] else "NO_DOMAIN_FILES")
        for domain in ALL_DOMAINS
    }
    assert actual == ROUTING_MATRIX[fixture_name]

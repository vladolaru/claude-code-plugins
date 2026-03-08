"""Structural invariant tests for yoloing-safe rule registry and scenarios.

These tests catch drift between RULE_REGISTRY, message dicts, allowlist entries,
and scenario files — no subprocess, no I/O beyond JSON loading.
"""

import json
import pytest
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def hook():
    """Import the hook script as a module."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "pre-tool-use-safety.py"
    spec = spec_from_file_location("safety_hook", str(script))
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def rule_ids(hook):
    """All rule_ids from RULE_REGISTRY."""
    return [r[0] for r in hook.RULE_REGISTRY]


@pytest.fixture
def block_rule_ids(hook):
    """Rule_ids with tier 'block'."""
    return [r[0] for r in hook.RULE_REGISTRY if r[1] == "block"]


@pytest.fixture
def ask_rule_ids(hook):
    """Rule_ids with tier 'ask'."""
    return [r[0] for r in hook.RULE_REGISTRY if r[1] == "ask"]


@pytest.fixture
def blocked_scenarios():
    """Load blocked.json scenarios."""
    path = Path(__file__).resolve().parent / "scenarios" / "blocked.json"
    return json.loads(path.read_text())


@pytest.fixture
def allowed_scenarios():
    """Load allowed.json scenarios."""
    path = Path(__file__).resolve().parent / "scenarios" / "allowed.json"
    return json.loads(path.read_text())


@pytest.fixture
def asked_scenarios():
    """Load asked.json scenarios."""
    path = Path(__file__).resolve().parent / "scenarios" / "asked.json"
    return json.loads(path.read_text())


# Known safe aliases used in allowed.json that are NOT rule_ids.
SAFE_ALIASES = {
    "safe_git",
    "allowlisted",
    "safe_rm",
    "safe_general",
    "safe_read",
    "non_bash_tool",
    "safe_write",
    "safe_scp_download",
    "safe_interpreter",
    "loopback_curl",
    "scoped_find_delete",
    "writer_heredoc",
    "container_exec",
    "safe_git_reset",
    "safe_git_stash",
    "safe_git_config",
    "safe_git_clean",
    "safe_terraform",
    "safe_github_cicd",
    "safe_docker",
    "safe_brew",
    "safe_database",
    "safe_chmod",
    "safe_write_target",
    "safe_inline_interpreter",
}


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestRuleMessageSync:
    """Every block rule has a BLOCK_MESSAGES entry; every ask rule has ASK_MESSAGES."""

    def test_block_rules_have_messages(self, hook, block_rule_ids):
        missing = [rid for rid in block_rule_ids if rid not in hook.BLOCK_MESSAGES]
        assert missing == [], f"Block rules missing BLOCK_MESSAGES entries: {missing}"

    def test_ask_rules_have_messages(self, hook, ask_rule_ids):
        missing = [rid for rid in ask_rule_ids if rid not in hook.ASK_MESSAGES]
        assert missing == [], f"Ask rules missing ASK_MESSAGES entries: {missing}"

    def test_no_orphaned_block_messages(self, hook, block_rule_ids):
        # self_protection is hardcoded (not in RULE_REGISTRY) — exempt
        orphaned = [
            key for key in hook.BLOCK_MESSAGES
            if key not in block_rule_ids and key != "self_protection"
        ]
        assert orphaned == [], f"Orphaned BLOCK_MESSAGES entries: {orphaned}"

    def test_no_orphaned_ask_messages(self, hook, ask_rule_ids):
        orphaned = [key for key in hook.ASK_MESSAGES if key not in ask_rule_ids]
        assert orphaned == [], f"Orphaned ASK_MESSAGES entries: {orphaned}"


class TestAllowlistIntegrity:
    """Every allowlist rule_id maps to a real RULE_REGISTRY entry."""

    def test_allowlist_rule_ids_exist(self, hook, rule_ids):
        rule_id_set = set(rule_ids)
        bad = [
            rid for rid, _pat in hook.ALLOWLIST_PATTERNS
            if rid not in rule_id_set
        ]
        assert bad == [], f"Allowlist references unknown rule_ids: {bad}"


class TestRuleRegistryExamples:
    """Every rule has at least one example command."""

    def test_every_rule_has_examples(self, hook):
        missing = [
            r[0] for r in hook.RULE_REGISTRY
            if not r[4]  # examples list is empty or falsy
        ]
        assert missing == [], f"Rules with no examples: {missing}"


class TestRuleRegistryNoDuplicates:
    """No duplicate rule_ids in RULE_REGISTRY."""

    def test_no_duplicate_rule_ids(self, rule_ids):
        seen = set()
        dupes = []
        for rid in rule_ids:
            if rid in seen:
                dupes.append(rid)
            seen.add(rid)
        assert dupes == [], f"Duplicate rule_ids: {dupes}"


class TestScenarioCategoryValidity:
    """Every category in scenario files is a valid rule_id or known alias."""

    def test_blocked_categories_are_rule_ids(self, rule_ids, blocked_scenarios):
        rule_id_set = set(rule_ids)
        bad = [
            s["category"] for s in blocked_scenarios
            if s["category"] not in rule_id_set
        ]
        assert bad == [], (
            f"blocked.json categories not in RULE_REGISTRY: {sorted(set(bad))}"
        )

    def test_allowed_categories_are_valid(self, rule_ids, allowed_scenarios):
        rule_id_set = set(rule_ids)
        valid = rule_id_set | SAFE_ALIASES
        bad = [
            s["category"] for s in allowed_scenarios
            if s["category"] not in valid
        ]
        assert bad == [], (
            f"allowed.json categories not in RULE_REGISTRY or SAFE_ALIASES: "
            f"{sorted(set(bad))}"
        )

    def test_asked_categories_are_rule_ids(self, rule_ids, asked_scenarios):
        rule_id_set = set(rule_ids)
        bad = [
            s["category"] for s in asked_scenarios
            if s["category"] not in rule_id_set
        ]
        assert bad == [], (
            f"asked.json categories not in RULE_REGISTRY: {sorted(set(bad))}"
        )


class TestScenarioCoveragePerRule:
    """Every rule must have scenario coverage at the fast test layer."""

    def _load_scenarios(self, filename):
        path = Path(__file__).resolve().parent / "scenarios" / filename
        if path.exists():
            return json.loads(path.read_text())
        return []

    def test_every_rule_has_scenario_coverage(self, rule_ids):
        """Each rule_id should appear as a category in blocked, allowed, or asked.json."""
        rule_id_set = set(rule_ids)
        blocked = self._load_scenarios("blocked.json")
        allowed = self._load_scenarios("allowed.json")
        asked = self._load_scenarios("asked.json")
        covered = (
            {s["category"] for s in blocked}
            | {s["category"] for s in allowed}
            | {s["category"] for s in asked}
        )
        uncovered = rule_id_set - covered
        assert not uncovered, (
            f"Rules with no scenario coverage in blocked/allowed/asked.json: "
            f"{sorted(uncovered)}. Add at least one scenario for each."
        )

    def test_block_rules_have_evasion_scenarios(self, hook):
        """Every block-tier rule should have at least one evasion scenario."""
        block_ids = {r[0] for r in hook.RULE_REGISTRY if r[1] == "block"}
        evasion = self._load_scenarios("evasion.json")
        evasion_rule_ids = {s["rule_id"] for s in evasion if "rule_id" in s}
        uncovered = block_ids - evasion_rule_ids
        assert not uncovered, (
            f"Block rules with no evasion scenarios: {sorted(uncovered)}. "
            f"Add at least one evasion entry with this rule_id in evasion.json."
        )

    def test_evasion_rule_ids_are_valid(self, rule_ids):
        """Evasion scenario rule_ids must map to real rules."""
        rule_id_set = set(rule_ids)
        evasion = self._load_scenarios("evasion.json")
        bad = [
            (s["technique"], s["rule_id"]) for s in evasion
            if "rule_id" in s and s["rule_id"] not in rule_id_set
        ]
        assert not bad, (
            f"Evasion scenarios with invalid rule_ids: {bad}"
        )


@pytest.fixture
def unit_test_classes():
    """Extract all Test* class names from test_safety_hook.py."""
    test_file = Path(__file__).resolve().parent / "test_safety_hook.py"
    content = test_file.read_text()
    import re as _re
    return set(_re.findall(r"^class (Test\w+)", content, _re.MULTILINE))


def _rule_id_to_class_name(rule_id):
    """Convert snake_case rule_id to PascalCase test class name.

    Examples: destructive_deletion -> TestDestructiveDeletion
              git_bare_push -> TestGitBarePush
              github_cicd_ops -> TestGitHubCICDOps
    """
    IRREGULAR = {
        "ssh_remote_destruction": "TestSSHRemoteDestruction",
        "github_repo_deletion": "TestGitHubRepoDelete",
        "github_cicd_ops": "TestGitHubCICDOps",
    }
    if rule_id in IRREGULAR:
        return IRREGULAR[rule_id]
    parts = rule_id.split("_")
    return "Test" + "".join(p.capitalize() for p in parts)


class TestUnitTestCoverage:
    """Every rule_id must have a corresponding unit test class."""

    def test_every_rule_has_unit_test_class(self, rule_ids, unit_test_classes):
        missing = []
        for rid in rule_ids:
            expected_class = _rule_id_to_class_name(rid)
            if expected_class not in unit_test_classes:
                missing.append((rid, expected_class))
        assert missing == [], (
            f"Rules missing unit test classes: {missing}. "
            f"Add a Test class for each in test_safety_hook.py."
        )

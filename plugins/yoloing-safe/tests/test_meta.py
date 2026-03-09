"""Structural invariant tests for yoloing-safe RULES dict and scenarios.

These tests catch drift between RULES dict, allowlist entries,
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
    """All rule_ids from RULES."""
    return list(hook.RULES.keys())


@pytest.fixture
def block_rule_ids(hook):
    """Rule_ids with tier 'block'."""
    return [rid for rid, r in hook.RULES.items() if r["tier"] == "block"]


@pytest.fixture
def ask_rule_ids(hook):
    """Rule_ids with tier 'ask'."""
    return [rid for rid, r in hook.RULES.items() if r["tier"] == "ask"]


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
    "safe_compound",
    "safe_pipe",
    "non_file_command",
}


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestRuleMessageSync:
    """Every rule has a message field."""

    def test_every_rule_has_message(self, hook):
        missing = [rid for rid, r in hook.RULES.items() if not r.get("message")]
        assert missing == [], f"Rules missing message field: {missing}"

    def test_self_protection_message_exists(self, hook):
        assert hasattr(hook, "_SELF_PROTECTION_MESSAGE"), (
            "_SELF_PROTECTION_MESSAGE constant missing"
        )


class TestAllowlistIntegrity:
    """Every allowlist rule_id maps to a real RULES entry."""

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
            rid for rid, r in hook.RULES.items()
            if not r.get("examples")
        ]
        assert missing == [], f"Rules with no examples: {missing}"


class TestRuleRegistryNoDuplicates:
    """No duplicate rule_ids (inherently true for dict keys, but documents the invariant)."""

    def test_no_duplicate_rule_ids(self, rule_ids):
        assert len(rule_ids) == len(set(rule_ids))


class TestScenarioCategoryValidity:
    """Every category in scenario files is a valid rule_id or known alias."""

    def test_blocked_categories_are_rule_ids(self, rule_ids, blocked_scenarios):
        rule_id_set = set(rule_ids)
        bad = [
            s["category"] for s in blocked_scenarios
            if s["category"] not in rule_id_set
        ]
        assert bad == [], (
            f"blocked.json categories not in RULES: {sorted(set(bad))}"
        )

    def test_allowed_categories_are_valid(self, rule_ids, allowed_scenarios):
        rule_id_set = set(rule_ids)
        valid = rule_id_set | SAFE_ALIASES
        bad = [
            s["category"] for s in allowed_scenarios
            if s["category"] not in valid
        ]
        assert bad == [], (
            f"allowed.json categories not in RULES or SAFE_ALIASES: "
            f"{sorted(set(bad))}"
        )

    def test_asked_categories_are_rule_ids(self, rule_ids, asked_scenarios):
        rule_id_set = set(rule_ids)
        bad = [
            s["category"] for s in asked_scenarios
            if s["category"] not in rule_id_set
        ]
        assert bad == [], (
            f"asked.json categories not in RULES: {sorted(set(bad))}"
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
        block_ids = {rid for rid, r in hook.RULES.items() if r["tier"] == "block"}
        evasion = self._load_scenarios("evasion.json")
        evasion_rule_ids = {s["rule_id"] for s in evasion if "rule_id" in s}
        uncovered = block_ids - evasion_rule_ids
        assert not uncovered, (
            f"Block rules with no evasion scenarios: {sorted(uncovered)}. "
            f"Add at least one evasion entry with this rule_id in evasion.json."
        )

    # Ask-tier rules that are security-critical enough to warrant evasion testing.
    CRITICAL_ASK_RULES = {
        "git_force_push", "git_hard_reset", "permission_changes",
        "database_destructive", "docker_destructive", "git_other_dangerous",
    }

    def test_critical_ask_rules_have_evasion_scenarios(self, hook):
        """Critical ask-tier rules should have at least one evasion scenario."""
        evasion = self._load_scenarios("evasion.json")
        evasion_rule_ids = {s["rule_id"] for s in evasion if "rule_id" in s}
        uncovered = self.CRITICAL_ASK_RULES - evasion_rule_ids
        assert not uncovered, (
            f"Critical ask rules with no evasion scenarios: {sorted(uncovered)}. "
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

    def test_every_rule_has_safe_variant_in_allowed(self, rule_ids):
        """Each rule_id should have a safe-variant category in allowed.json.

        Safe variants use the pattern safe_{topic} (e.g., safe_git for git rules).
        This ensures false-positive regression protection for every rule.
        """
        allowed = self._load_scenarios("allowed.json")
        allowed_categories = {s["category"] for s in allowed}

        RULE_SAFE_ALIAS_MAP = {
            "destructive_deletion": {"safe_rm", "allowlisted"},
            "alternative_deletion": {"scoped_find_delete"},
            "disk_formatting": {"safe_general"},
            "network_exfiltration": {"loopback_curl", "safe_scp_download"},
            "credential_access": {"safe_read"},
            "package_publishing": {"allowlisted"},
            "ssh_remote_destruction": {"safe_general"},
            "github_repo_deletion": {"safe_general"},
            "zero_access_paths": {"safe_read"},
            "git_bare_push": {"safe_git"},
            "git_force_push": {"allowlisted", "safe_git"},
            "git_hard_reset": {"safe_git_reset"},
            "git_discard_changes": {"safe_git"},
            "git_destroy_stash": {"safe_git_stash"},
            "git_history_rewrite": {"safe_git"},
            "git_config_changes": {"safe_git_config"},
            "git_other_dangerous": {"safe_git", "safe_git_clean"},
            "permission_changes": {"safe_chmod", "allowlisted"},
            "brew_commands": {"safe_brew"},
            "docker_destructive": {"safe_docker"},
            "database_destructive": {"safe_database"},
            "terraform_destructive": {"safe_terraform"},
            "github_cicd_ops": {"safe_github_cicd"},
            "sensitive_write_target": {"safe_write_target"},
            "inline_interpreter": {"safe_inline_interpreter"},
            "inline_heredoc": {"writer_heredoc"},
        }

        uncovered = []
        for rid in rule_ids:
            safe_aliases = RULE_SAFE_ALIAS_MAP.get(rid, set())
            has_coverage = safe_aliases & allowed_categories
            if not has_coverage:
                uncovered.append(rid)

        assert not uncovered, (
            f"Rules with no safe-variant in allowed.json: {sorted(uncovered)}. "
            f"Add a safe scenario and map it in RULE_SAFE_ALIAS_MAP."
        )


@pytest.fixture
def unit_test_classes():
    """Extract all Test* class names from the non-collected legacy unit test module."""
    test_file = Path(__file__).resolve().parent / "_legacy_safety_hook_tests.py"
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
            f"Add a Test class for each in the split rule suites."
        )


class TestDeclarativeRuleStructure:
    """Structural invariants for the declarative rule format."""

    def test_every_rule_has_detection_method(self, hook):
        bad = []
        for rid, r in hook.RULES.items():
            has_patterns = "patterns" in r or "pattern_groups" in r
            has_detect = "detect" in r
            if not has_patterns and not has_detect:
                bad.append(rid)
        assert bad == [], (
            f"Rules with neither patterns nor detect function: {bad}"
        )

    def test_every_rule_has_compiled_detect(self, hook):
        """build_registry() should have stored _detect on every rule."""
        missing = [rid for rid, r in hook.RULES.items() if "_detect" not in r]
        assert missing == [], f"Rules missing _detect after build_registry(): {missing}"

    def test_tier_values_are_valid(self, hook):
        bad = [
            (rid, r["tier"]) for rid, r in hook.RULES.items()
            if r["tier"] not in ("block", "ask")
        ]
        assert bad == [], f"Rules with invalid tier: {bad}"

    def test_tools_are_sets(self, hook):
        bad = [rid for rid, r in hook.RULES.items() if not isinstance(r["tools"], set)]
        assert bad == [], f"Rules where tools is not a set: {bad}"

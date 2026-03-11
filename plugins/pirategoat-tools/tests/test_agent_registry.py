"""
Tests for agent-registry.json — deterministic, no model calls.

Validates schema, completeness, and cross-references against review-scope.py domains.
"""

import json
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
REGISTRY_PATH = SCRIPTS_DIR / "agent-registry.json"

# Import DOMAIN_CATALOG from review-scope.py
sys.path.insert(0, str(SCRIPTS_DIR))
import importlib

_scope_spec = importlib.util.spec_from_file_location(
    "review_scope", str(SCRIPTS_DIR / "review-scope.py")
)
_scope_mod = importlib.util.module_from_spec(_scope_spec)
_scope_spec.loader.exec_module(_scope_mod)
DOMAIN_CATALOG = _scope_mod.DOMAIN_CATALOG

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_DISPATCH_CLASSES = {"always", "conditional", "manual", "special"}
VALID_PROTOCOLS = {"reviewer", "tests-reviewer"}
VALID_MODEL_TIERS = {"inherit", "sonnet", "haiku", "opus"}
EXPECTED_AGENT_COUNT = 16  # agents from AGENT_CONFIG in bootstrap-reviewer.py


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def registry():
    """Load and return the agent registry."""
    assert REGISTRY_PATH.exists(), f"Registry file not found: {REGISTRY_PATH}"
    with open(REGISTRY_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def agents(registry):
    """Return the agents dict from the registry."""
    assert "agents" in registry, "Registry must have an 'agents' key"
    return registry["agents"]


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------
class TestRegistryFile:
    """Registry file is valid JSON with correct top-level structure."""

    def test_file_exists(self):
        assert REGISTRY_PATH.exists(), f"Registry file not found: {REGISTRY_PATH}"

    def test_valid_json(self):
        with open(REGISTRY_PATH) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_has_agents_key(self, registry):
        assert "agents" in registry
        assert isinstance(registry["agents"], dict)


class TestRequiredFields:
    """All agents have required fields with valid values."""

    REQUIRED_FIELDS = {"domain", "protocols", "dispatch_class", "focus"}

    @pytest.mark.parametrize("field", sorted(REQUIRED_FIELDS))
    def test_all_agents_have_field(self, agents, field):
        for agent_name, config in agents.items():
            assert field in config, (
                f"Agent '{agent_name}' missing required field '{field}'"
            )

    def test_domain_is_string_or_none(self, agents):
        for agent_name, config in agents.items():
            assert config["domain"] is None or isinstance(config["domain"], str), (
                f"Agent '{agent_name}': domain must be string or null"
            )

    def test_protocols_is_list(self, agents):
        for agent_name, config in agents.items():
            assert isinstance(config["protocols"], list), (
                f"Agent '{agent_name}': protocols must be a list"
            )

    def test_dispatch_class_is_string(self, agents):
        for agent_name, config in agents.items():
            assert isinstance(config["dispatch_class"], str), (
                f"Agent '{agent_name}': dispatch_class must be a string"
            )

    def test_focus_is_string(self, agents):
        for agent_name, config in agents.items():
            assert isinstance(config["focus"], str), (
                f"Agent '{agent_name}': focus must be a string"
            )
            assert len(config["focus"]) > 0, (
                f"Agent '{agent_name}': focus must not be empty"
            )


class TestDomainReferences:
    """All domains reference valid entries in DOMAIN_CATALOG."""

    def test_primary_domains_valid(self, agents):
        for agent_name, config in agents.items():
            domain = config["domain"]
            if domain is not None:
                assert domain in DOMAIN_CATALOG, (
                    f"Agent '{agent_name}': domain '{domain}' not in DOMAIN_CATALOG"
                )

    def test_secondary_domains_valid(self, agents):
        for agent_name, config in agents.items():
            for sec_domain in config.get("secondary_domains", []):
                assert sec_domain in DOMAIN_CATALOG, (
                    f"Agent '{agent_name}': secondary domain '{sec_domain}' not in DOMAIN_CATALOG"
                )


class TestProtocols:
    """All protocol names are valid."""

    def test_protocols_valid(self, agents):
        for agent_name, config in agents.items():
            for protocol in config["protocols"]:
                assert protocol in VALID_PROTOCOLS, (
                    f"Agent '{agent_name}': protocol '{protocol}' not in {VALID_PROTOCOLS}"
                )

    def test_protocols_non_empty(self, agents):
        for agent_name, config in agents.items():
            # Special agents (e.g., decision-reviewer) don't use reviewer
            # protocols — they have their own workflows
            if config.get("dispatch_class") == "special":
                continue
            assert len(config["protocols"]) > 0, (
                f"Agent '{agent_name}': protocols list must not be empty"
            )


class TestDispatchClass:
    """dispatch_class is one of 'always', 'conditional', 'manual'."""

    def test_valid_dispatch_class(self, agents):
        for agent_name, config in agents.items():
            assert config["dispatch_class"] in VALID_DISPATCH_CLASSES, (
                f"Agent '{agent_name}': dispatch_class '{config['dispatch_class']}' "
                f"not in {VALID_DISPATCH_CLASSES}"
            )

    def test_conditional_agents_have_triage_criteria(self, agents):
        for agent_name, config in agents.items():
            if config["dispatch_class"] == "conditional":
                assert "triage_criteria" in config, (
                    f"Agent '{agent_name}': conditional agents must have 'triage_criteria'"
                )
                assert isinstance(config["triage_criteria"], list), (
                    f"Agent '{agent_name}': triage_criteria must be a list"
                )
                assert len(config["triage_criteria"]) > 0, (
                    f"Agent '{agent_name}': triage_criteria must not be empty"
                )

    def test_non_conditional_agents_no_triage_criteria(self, agents):
        """Non-conditional agents should not have triage_criteria."""
        for agent_name, config in agents.items():
            if config["dispatch_class"] != "conditional":
                assert "triage_criteria" not in config, (
                    f"Agent '{agent_name}': non-conditional agents should not have 'triage_criteria'"
                )


class TestModelTier:
    """model_tier is one of 'inherit', 'sonnet', 'haiku', 'opus'."""

    def test_model_tier_present(self, agents):
        for agent_name, config in agents.items():
            assert "model_tier" in config, (
                f"Agent '{agent_name}': missing 'model_tier' field"
            )

    def test_model_tier_valid(self, agents):
        for agent_name, config in agents.items():
            assert config["model_tier"] in VALID_MODEL_TIERS, (
                f"Agent '{agent_name}': model_tier '{config['model_tier']}' "
                f"not in {VALID_MODEL_TIERS}"
            )


class TestAgentCount:
    """Registry contains the expected number of agents."""

    def test_expected_count(self, agents):
        assert len(agents) == EXPECTED_AGENT_COUNT, (
            f"Expected {EXPECTED_AGENT_COUNT} agents, found {len(agents)}: "
            f"{sorted(agents.keys())}"
        )


class TestOptionalFields:
    """Optional fields have correct types when present."""

    def test_secondary_domains_is_list(self, agents):
        for agent_name, config in agents.items():
            if "secondary_domains" in config:
                assert isinstance(config["secondary_domains"], list), (
                    f"Agent '{agent_name}': secondary_domains must be a list"
                )

    def test_scope_flags_is_list(self, agents):
        for agent_name, config in agents.items():
            if "scope_flags" in config:
                assert isinstance(config["scope_flags"], list), (
                    f"Agent '{agent_name}': scope_flags must be a list"
                )

    def test_extra_scope_is_list(self, agents):
        for agent_name, config in agents.items():
            if "extra_scope" in config:
                assert isinstance(config["extra_scope"], list), (
                    f"Agent '{agent_name}': extra_scope must be a list"
                )

    def test_file_history_is_boolean(self, agents):
        for agent_name, config in agents.items():
            if "file_history" in config:
                assert isinstance(config["file_history"], bool), (
                    f"Agent '{agent_name}': file_history must be a boolean"
                )

    def test_triage_criteria_strings(self, agents):
        for agent_name, config in agents.items():
            if "triage_criteria" in config:
                for criterion in config["triage_criteria"]:
                    assert isinstance(criterion, str), (
                        f"Agent '{agent_name}': each triage criterion must be a string"
                    )


class TestBootstrapCompatibility:
    """Registry entries are compatible with bootstrap-reviewer.py expectations.

    bootstrap-reviewer.py accesses these keys from AGENT_CONFIG:
    - config["domain"]
    - config["protocols"]
    - config.get("scope_flags", [])
    - config.get("secondary_domains", [])
    - config.get("extra_scope")  (checked with "extra_scope" in config)
    - config.get("file_history")
    """

    def test_all_agents_have_scope_flags(self, agents):
        """scope_flags is used via .get() with default [], but should be present."""
        for agent_name, config in agents.items():
            assert "scope_flags" in config, (
                f"Agent '{agent_name}': missing 'scope_flags' (needed by bootstrap)"
            )

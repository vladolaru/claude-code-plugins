"""Compliance test for the ecosystem-integration-reviewer agent."""

import json
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent.parent.parent
AGENT_PATH = PLUGIN_ROOT / "agents" / "ecosystem-integration-reviewer.md"
REG_PATH = PLUGIN_ROOT / "scripts" / "review" / "agent_registry.json"


def test_agent_md_exists():
    assert AGENT_PATH.exists(), f"Agent markdown missing at {AGENT_PATH}"


def test_agent_frontmatter_has_required_fields():
    content = AGENT_PATH.read_text()
    assert content.startswith("---")
    assert "name: ecosystem-integration-reviewer" in content
    assert "description:" in content
    assert "model:" in content


def test_focus_alignment_between_registry_and_agent_description():
    """Registry focus and agent description must cover the same scope."""
    registry = json.loads(REG_PATH.read_text())
    entry = registry["agents"]["ecosystem-integration-reviewer"]
    focus = entry["focus"].lower()
    agent_content = AGENT_PATH.read_text().lower()
    # Spot-check that key capabilities from the focus line appear in the agent doc
    for keyword in ["filter", "override", "abstract", "rest"]:
        assert keyword in focus, f"focus should mention {keyword}"
        assert keyword in agent_content, f"agent description should mention {keyword}"


def test_agent_declares_boundaries_with_other_reviewers():
    content = AGENT_PATH.read_text()
    # Must explicitly reference the other agents whose territory it does NOT enter
    assert "wp-architecture-reviewer" in content
    assert "security-reviewer" in content


def test_agent_declares_host_context_dependency():
    content = AGENT_PATH.read_text()
    assert "Host Context" in content
    assert "runtime-host" in content.lower()


def test_agent_treats_host_context_as_non_exhaustive():
    content = AGENT_PATH.read_text().lower()
    assert "starting point" in content
    assert "not an exhaustive" in content
    assert "explore" in content


def test_agent_uses_bounded_upstream_discovery_then_rule_zero_exit():
    content = AGENT_PATH.read_text().lower()

    assert "## bounded upstream discovery" in content
    assert "host context paths" in content
    assert "repository config and changed-file imports" in content
    assert "declared dependency roots" in content
    assert "specific sibling checkout" in content
    assert "after one bounded pass" in content
    assert "apply rule 0 and omit the finding" in content


def test_agent_output_filename_matches_review_output_builder_contract():
    content = AGENT_PATH.read_text()
    assert "ecosystem-integration-review.{json,md}" in content
    assert "ecosystem-integration-reviewer.{json,md}" not in content

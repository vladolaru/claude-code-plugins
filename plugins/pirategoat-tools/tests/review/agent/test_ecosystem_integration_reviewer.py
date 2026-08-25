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
    """The discovery section must keep its lookup order and RULE 0 exit.

    Asserts structure (source priority order + clean exit) rather than exact
    prose, so editorial rewording that preserves the behavior doesn't fail.
    """
    content = AGENT_PATH.read_text().lower()

    start = content.find("## bounded upstream discovery")
    assert start != -1, "agent must define a bounded upstream discovery section"
    end = content.find("\n## ", start + 1)
    section = content[start:end] if end != -1 else content[start:]

    lookup_order = ["host context", "config", "dependency root", "sibling"]
    positions = [section.find(term) for term in lookup_order]
    for term, pos in zip(lookup_order, positions):
        assert pos != -1, f"discovery section must name lookup source: {term}"
    assert positions == sorted(positions), (
        f"lookup sources must appear in priority order {lookup_order}"
    )

    assert "rule 0" in section, "exhausted discovery must fall back to RULE 0"
    assert "omit" in section, "RULE 0 exit must omit the unverifiable finding"


def test_agent_uses_the_bootstrap_owned_output_lifecycle():
    content = AGENT_PATH.read_text()
    assert "Use the bootstrap-provided ReviewOutputBuilder lifecycle" in content
    assert "exact printed `FINALIZE REVIEW` command verbatim" in content
    assert "Never write review JSON or Markdown directly" in content
    assert "ecosystem-integration-review.{json,md}" not in content

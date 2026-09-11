"""Compliance test for the ecosystem-integration-reviewer agent."""

from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent.parent.parent
AGENT_PATH = PLUGIN_ROOT / "agents" / "ecosystem-integration-reviewer.md"


def test_agent_uses_bounded_upstream_discovery_then_rule_zero_exit():
    """The discovery section keeps its lookup order and RULE 0 exit, and a
    resolved Host Context entry is authoritative over lower discovery
    sources (fix a9216f4f).

    Asserts structure (source priority order + clean exit) rather than exact
    prose, so editorial rewording that preserves the behavior doesn't fail.
    """
    content = AGENT_PATH.read_text().lower()

    authority_start = content.find(
        "## operating procedure: host context as the authoritative source when resolved"
    )
    assert authority_start != -1, "agent must define the resolved-host authority contract"
    authority_end = content.find("\n## ", authority_start + 1)
    authority_section = (
        content[authority_start:authority_end] if authority_end != -1 else content[authority_start:]
    )
    assert "authoritative for the upstream surface it covers" in authority_section

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

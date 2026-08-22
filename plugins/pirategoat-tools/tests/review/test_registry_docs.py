"""The plugin AGENTS.md agent-registry reference stays pinned to the registry.

The `model_tier` row documented `"inherit"`/`"sonnet"`/`"haiku"` while five
registry agents legitimately ran at `opus` — nothing tied the prose to
`scripts/review/agent_registry.json`, so a cold agent reading the doc would
learn a vocabulary the machine does not use (found by the 2026-08-14
live-fire audit). These guards check both directions.

`README.md`'s "#### Model Tiers" section has the same failure mode, one
level up: it hand-summarizes the registry's `model_tier` counts and names a
few example agents per tier in prose. Nothing tied THAT prose to the
registry either, and it drifted — "opus (4 agents)" while the registry
carries five, silently omitting `woo-regression-reviewer` from the opus
paragraph entirely. `TestReadmeModelTierMatchesRegistry` below pins it.
"""

import json
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
REGISTRY = PLUGIN_ROOT / "scripts" / "review" / "agent_registry.json"
AGENTS_MD = PLUGIN_ROOT / "AGENTS.md"
README = PLUGIN_ROOT / "README.md"

# 'inherit' is a routing keyword (use the caller's model), legitimate to
# document even when no agent currently declares it.
ROUTING_KEYWORDS = {"inherit"}


def _registry_tiers():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {agent["model_tier"] for agent in registry["agents"].values()}


def _documented_tiers():
    rows = [
        line
        for line in AGENTS_MD.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `model_tier` |")
    ]
    assert rows, "AGENTS.md no longer documents the model_tier field"
    assert len(rows) == 1, (
        f"{len(rows)} model_tier rows in AGENTS.md — the guard would read "
        "an ambiguous vocabulary"
    )
    documented = set(re.findall(r'"([a-z]+)"', rows[0]))
    assert documented, (
        "no quoted tier values parsed from the model_tier row — did the "
        "row format change?"
    )
    return documented


def test_model_tier_doc_vocabulary_covers_registry():
    """Every tier the registry actually uses must be documented."""
    missing = _registry_tiers() - _documented_tiers()
    assert not missing, (
        f"registry uses model tiers {sorted(missing)} that the AGENTS.md "
        f"model_tier row does not document "
        f"(documented: {sorted(_documented_tiers())})"
    )


def test_documented_vocabulary_has_no_phantom_tiers():
    """The doc must not teach tiers no agent uses and no routing keyword
    defines — a phantom value sends an agent hunting for a convention that
    does not exist."""
    phantoms = _documented_tiers() - _registry_tiers() - ROUTING_KEYWORDS
    assert not phantoms, (
        f"AGENTS.md documents model tiers {sorted(phantoms)} that no "
        "registry agent uses"
    )


# =============================================================================
# TestReadmeModelTierMatchesRegistry
# =============================================================================

# README.md documents four agents the dispatch registry never carries at
# all (review-reconciliator, gemini-reviewer, codex-reviewer,
# technical-writer — they are invoked outside plan_dispatch's triage, so
# they have no agent_registry.json entry to be pinned against). Their model
# comes from their own agent .md frontmatter, not the registry, so the
# registry-agreement check below is scoped to agents the registry actually
# knows about; the count and named-agent checks still cover all 34, straight
# from the README's own tables.
_README_TIER_HEADING = re.compile(r"^#### Model Tiers\n(.*?)(?:\n### |\Z)", re.DOTALL | re.MULTILINE)
_README_AGENTS_SECTION = re.compile(r"^### \d+ Agents\n(.*?)^### \d+ Skills", re.DOTALL | re.MULTILINE)
_README_TABLE_ROW = re.compile(r"^\|\s*\*\*([a-z0-9][a-z0-9-]*)\*\*\s*\|.*\|\s*([a-z]+)\s*\|\s*$", re.MULTILINE)
_README_TIER_BULLET = re.compile(r"^- \*\*([a-z]+)\*\* \((\d+) agents?\) — (.+)$", re.MULTILINE)


def _readme_text():
    return README.read_text(encoding="utf-8")


def _readme_agent_table_models():
    """``{agent_name: model}`` parsed from every row of the README's four
    agent tables (Domain Review / Pipeline / Cross-Validators / Utility),
    all nested under the top-level ``### N Agents`` section."""
    section = _README_AGENTS_SECTION.search(_readme_text())
    assert section is not None, (
        "README.md no longer has a '### N Agents' section immediately "
        "followed by a '### N Skills' section — update the parser"
    )
    rows = _README_TABLE_ROW.findall(section.group(1))
    assert rows, "no agent table rows parsed from the README's Agents section"
    return dict(rows)


def _readme_model_tier_bullets():
    """``{tier: (declared_count, bullet_prose)}`` parsed from the
    '#### Model Tiers' bullet list."""
    section = _README_TIER_HEADING.search(_readme_text())
    assert section is not None, (
        "README.md no longer has a '#### Model Tiers' section — update the "
        "parser"
    )
    bullets = _README_TIER_BULLET.findall(section.group(1))
    assert bullets, (
        "no '- **<tier>** (<N> agents) — ...' bullets parsed from the "
        "README's Model Tiers section — did the bullet format change?"
    )
    return {tier: (int(count), prose) for tier, count, prose in bullets}


def _registry_agents():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))["agents"]


def test_readme_agent_table_matches_registry_model_tier():
    """Every agent the dispatch registry knows about must be tagged with
    its registry `model_tier` in the README's own agent tables — the table
    row, not just the prose, is what a reader actually scans."""
    table_models = _readme_agent_table_models()
    registry = _registry_agents()

    missing = sorted(set(registry) - set(table_models))
    assert not missing, (
        f"registry agents {missing} have no row in the README's agent "
        "tables"
    )

    mismatched = {
        name: {"readme": table_models[name], "registry": cfg["model_tier"]}
        for name, cfg in registry.items()
        if table_models[name] != cfg["model_tier"]
    }
    assert not mismatched, (
        f"README agent table 'Model' column disagrees with the registry's "
        f"model_tier: {mismatched}"
    )


def test_readme_model_tier_bullet_counts_match_the_readme_tables():
    """Each tier's '(N agents)' figure must equal how many agents the
    README's own tables actually tag with that tier — the count that went
    stale ('opus (4 agents)' while the tables tag 5)."""
    table_models = _readme_agent_table_models()
    tier_bullets = _readme_model_tier_bullets()

    for tier in ("opus", "sonnet", "haiku"):
        assert tier in tier_bullets, (
            f"README's Model Tiers section has no '{tier}' bullet"
        )
        declared_count, _prose = tier_bullets[tier]
        actual = sorted(
            name for name, model in table_models.items() if model == tier
        )
        assert declared_count == len(actual), (
            f"'{tier}' bullet claims ({declared_count} agents) but the "
            f"README's own tables tag {len(actual)}: {actual}"
        )


def test_readme_model_tier_bullet_names_every_registry_agent_at_that_tier():
    """Every registry agent at a given tier must be named by its exact
    slug somewhere in that tier's bullet prose — the omission that let
    `woo-regression-reviewer` (opus, per the registry) go unmentioned in
    the opus paragraph while the count silently undercounted it too.

    Scoped to `opus` and `haiku`: both are small enough (5 and 6 agents)
    that the README names every one of them individually, which is exactly
    the convention that let the omission go undetected. `sonnet` carries 22
    agents and is deliberately written as category-grouped prose ("Test
    reviewers check against catalogued smells...") rather than an
    exhaustive per-agent listing — that is a legitimate style choice for a
    bucket this size, not the drift this guard exists to catch, so its
    count alone is checked above.
    """
    tier_bullets = _readme_model_tier_bullets()
    registry = _registry_agents()

    for tier in ("opus", "haiku"):
        _declared_count, prose = tier_bullets[tier]
        registry_agents_at_tier = sorted(
            name for name, cfg in registry.items() if cfg["model_tier"] == tier
        )
        unnamed = [name for name in registry_agents_at_tier if name not in prose]
        assert not unnamed, (
            f"'{tier}' bullet prose does not name {unnamed} even though "
            f"the registry tags {'them' if len(unnamed) > 1 else 'it'} "
            f"'{tier}'"
        )

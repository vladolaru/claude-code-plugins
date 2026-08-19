"""The plugin AGENTS.md agent-registry reference stays pinned to the registry.

The `model_tier` row documented `"inherit"`/`"sonnet"`/`"haiku"` while five
registry agents legitimately ran at `opus` — nothing tied the prose to
`scripts/review/agent_registry.json`, so a cold agent reading the doc would
learn a vocabulary the machine does not use (found by the 2026-08-14
live-fire audit). These guards check both directions.
"""

import json
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
REGISTRY = PLUGIN_ROOT / "scripts" / "review" / "agent_registry.json"
AGENTS_MD = PLUGIN_ROOT / "AGENTS.md"

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

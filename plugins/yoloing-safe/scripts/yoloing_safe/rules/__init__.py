"""Ordered rule assembly for the yoloing-safe hook.

Each domain module exports an ordered RULE_SPECS list of (rule_id, spec)
tuples. This module concatenates them into the canonical RULES dict.
Block-tier rules come first, ask-tier rules second — order within each
tier follows the concatenation order below.
"""

from __future__ import annotations

from ..registry import build_registry
from . import filesystem, git, network, system


# Canonical global order: block-tier first, then ask-tier.
# Within each tier, domain modules are concatenated in a fixed order.
# Each module's RULE_SPECS keeps block rules before ask rules internally.
_ALL_SPECS = (
    filesystem.RULE_SPECS
    + network.RULE_SPECS
    + git.RULE_SPECS
    + system.RULE_SPECS
)

# Assemble with block-tier first, ask-tier second.
RULES = {}
for _rid, _spec in _ALL_SPECS:
    if _spec["tier"] == "block":
        RULES[_rid] = _spec
for _rid, _spec in _ALL_SPECS:
    if _spec["tier"] == "ask":
        RULES[_rid] = _spec

ALLOWLIST_PATTERNS = (
    git.ALLOWLIST_PATTERNS
    + filesystem.ALLOWLIST_PATTERNS
    + system.ALLOWLIST_PATTERNS
    + network.ALLOWLIST_PATTERNS
)

RULES_BY_TOOL = build_registry(RULES)

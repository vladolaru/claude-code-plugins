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

The agent-definition contract pins live here too, under their own heading
comment: prose on `agents/*.md` and the shared protocol, one pin per contract.
"""

import json
import re
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
REGISTRY = PLUGIN_ROOT / "scripts" / "review" / "agent_registry.json"
AGENTS_MD = PLUGIN_ROOT / "AGENTS.md"
README = PLUGIN_ROOT / "README.md"
AGENTS_DIR = PLUGIN_ROOT / "agents"

# 'inherit' is a routing keyword (use the caller's model), legitimate to
# document even when no agent currently declares it.
ROUTING_KEYWORDS = {"inherit"}


# =============================================================================
# Agent-definition contract pins (one per contract)
# =============================================================================
# Each pin sits on the sentence that carries its contract, in the agent
# definition or shared protocol that states it, and each contract names the
# real miss behind it. Whether the shared protocol reaches the built prompt
# is a delivery question, guarded in review/agent/test_bootstrap_integration.py.


def _agent_definition(relpath):
    return (AGENTS_DIR / relpath).read_text(encoding="utf-8")


def test_reviewer_protocol_says_a_mounted_host_is_not_always_upstream():
    """Run e08e: a WooCommerce core review listed WooPayments as a runtime
    host because the clone's local wp-env override mounts it. A mapping
    proves co-installation, not direction; the reviewer decides that from
    the diff, so the protocol has to say so instead of calling every host
    upstream."""
    protocol = _agent_definition("shared/reviewer-protocol.md")
    section = protocol.split("## Host Context Usage", 1)[1].split("\n## ", 1)[0]

    assert "downstream" in section


# woo-regression-reviewer: each invariant is a real corpus miss, pinned on its
# per-hunk self-audit row (the row is what lets the self-audit catch a
# dismissal). The floors row pins the structured argument that replaced the
# "Severity-floor:" description marker.
WOO_INVARIANT_ROWS = [
    pytest.param('severity_floor="high"', id="structured-severity-floor"),
    pytest.param(
        "Heuristics — proxy predicate vs. configuration variance",
        id="proxy-predicate-woocommerce-66613",
    ),
    pytest.param(
        "Markup — removed/renamed selector surface",
        id="markup-selector-contract-woocommerce-55669",
    ),
    pytest.param(
        "Hooks - late transform over foreign collection entries",
        id="late-transform-woocommerce-subscriptions-5575",
    ),
    pytest.param(
        "Settings - form-only invariant vs. REST/CLI writers",
        id="settings-write-surface-woocommerce-subscriptions-4612",
    ),
]


@pytest.mark.parametrize("row", WOO_INVARIANT_ROWS)
def test_woo_invariant_rows(row):
    assert row in _agent_definition("woo-regression-reviewer.md")


class TestAPIContractReviewerReturnSideHooks:
    """Regression guard for caller-side handling of filter return values: a
    change to what callers do with a filter's result breaks extensions even
    when the apply_filters() call itself is untouched."""

    def test_treats_established_runtime_behavior_as_contract(self):
        # The body, not the frontmatter description, carries the rule.
        body = _agent_definition("api-contract-reviewer.md").split("---", 2)[2]

        assert "established runtime behavior" in body.lower()


class TestDismissalDisciplineContract:
    """Regression guard for the woocommerce/woocommerce#66488 miss: a detected
    concern was demoted to "narrow and acceptable corner" tradeoff prose on
    an unverified frequency claim, because the verification rules were scoped
    to floored findings and three regression categories only."""

    def test_reconciliator_has_general_dismissal_discipline(self):
        assert "## Dismissal & Mitigation Discipline (ALL findings)" in (
            _agent_definition("review-reconciliator.md")
        )


class TestVerificationMethodContract:
    """Regression guard for the 2026-07-16 run: three agents 'cleared' the
    blast radius of a removed <label> with the same wrong grep, the raw
    signal read as 3-clear-vs-1-found, and the reconciliator then verified
    from a 37-line window of a 5,900-line stylesheet. Two contracts: the
    reconciliator weighs checks by method, and reviewers record absence
    claims as checks."""

    def test_reconciliator_has_verification_method_weighting(self):
        assert "## Verification-Method Weighting" in (
            _agent_definition("review-reconciliator.md")
        )

    def test_protocol_has_absence_claim_rules(self):
        assert "## Absence Claims" in _agent_definition("shared/reviewer-protocol.md")


class TestUnchangedCallerScopeContract:
    """A hunk that changes a function's contract puts the callers that relied
    on the old contract in scope, even in a file with no diff (fix 38ab4b6b),
    and a debug-only log is a resilience gap, not detection (fix 993a2ca8)."""

    def test_protocol_has_unchanged_caller_exception(self):
        assert "reaching an unchanged caller" in (
            _agent_definition("shared/reviewer-protocol.md")
        )

    def test_reliability_observable_rule_rejects_debug_only_signal(self):
        text = _agent_definition("reliability-reviewer.md")
        rule0 = text[text.index("## RULE 0"):text.index("## Core Mission")]

        assert "`debug`" in rule0


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

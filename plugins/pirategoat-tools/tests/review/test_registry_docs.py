"""The plugin AGENTS.md agent-registry reference stays pinned to the registry.

The `model_tier` row documented `"inherit"`/`"sonnet"`/`"haiku"` while five
registry agents legitimately ran at `opus` — nothing tied the prose to
`scripts/review/agent_registry.json`, so a cold agent reading the doc would
learn a vocabulary the machine does not use (found by the 2026-08-14
live-fire audit). The guard checks that every tier the registry uses is
documented; AGENTS.md is agent-facing instruction, so this is a contract.
README.md is human documentation and is left to docs-drift review.

The agent-definition contract pins live here too, under their own heading
comment: prose on `agents/*.md` and the shared protocol, one pin per contract.
"""

import json
import re
from pathlib import Path

import pytest

from review.reconciliation_context import FLOOR_MIN_CONFIDENCE

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
REGISTRY = PLUGIN_ROOT / "scripts" / "review" / "agent_registry.json"
AGENTS_MD = PLUGIN_ROOT / "AGENTS.md"
AGENTS_DIR = PLUGIN_ROOT / "agents"


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


def test_woo_floors_paragraph_states_the_confidence_the_pipeline_enforces():
    """The reviewer tells a self-audit promotion which confidence earns a
    floor; `reconciliation_context` is what strips the ones below it. Run
    #66900 locked two 0.5-confidence promotions at medium, so the prose and
    the constant must not be able to drift apart."""
    text = _agent_definition("woo-regression-reviewer.md")
    floors = text.split(
        "**Structured floors (do not breach):**", 1
    )[1].split("\n## ", 1)[0]

    assert str(FLOOR_MIN_CONFIDENCE) in floors


def test_wp_architecture_reviewer_audits_half_deprecations():
    """Deprecation Rule addition (regression guard for the
    woocommerce/woocommerce-subscriptions#5692 rework): a `@deprecated`
    tag added without the runtime notice, or the reverse, is an
    incomplete deprecation the removal-gated rule above never reaches."""
    prompt = (PLUGIN_ROOT / "agents/wp-architecture-reviewer.md").read_text()

    # The invariant, and why the removal-gated rule above does not reach it.
    assert "**Half-deprecations count.**" in prompt
    assert "the rule above does not cover it because nothing was removed" in prompt
    # Both halves named as the mechanisms they are. The bare function names
    # also appear in the removal-gated rule above, so they are asserted in
    # this paragraph's phrasing - a bare name would pass with the paragraph
    # deleted.
    assert "`@deprecated` tag, which speaks to" in prompt
    assert "runtime `_deprecated_function()` / `_deprecated_hook()` call" in prompt
    # Both directions of the incompleteness.
    assert "A tag with no notice means no consumer is ever told" in prompt
    assert "a notice with no tag" in prompt
    # The evidence the reviewer is sent to gather.
    assert "Check the sibling deprecated symbols in the same file" in prompt
    # The dismissal this closes.
    assert "An author's stated reason for the omission does not settle it" in prompt
    # Both false-positive gates, so a planned second half and genuinely
    # internal symbols stay unflagged.
    assert "when the diff names the version the missing half lands in" in prompt
    assert "provably unreachable from outside the codebase" in prompt


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


class TestConfirmedNoteMovesSeverity:
    """Regression guard for the 2026-09-10 review of
    woocommerce/woocommerce#12089: note n2 said the finding was a goal
    misalignment on a pre-existing race, not a regression. The
    reconciliator resolved n2 `confirmed` and left the finding at high,
    which published request_changes; the critic then demoted it on n2's
    own framing. Confirming a note and acting on it are one pass."""

    def test_the_notes_item_says_a_confirmed_note_moves_the_severity(self):
        item = _agent_definition("review-reconciliator.md").split(
            "9. **`orchestrator_notes`**", 1
        )[1].split("\n10. ", 1)[0]

        assert "moves that severity in this same pass" in item


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


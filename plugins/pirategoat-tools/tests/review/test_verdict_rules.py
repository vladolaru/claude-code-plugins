"""The shared severity-to-verdict ladder.

`verdict_rules.verdict_for_counts` is the one place the thresholds live.
Two callers depend on it — `agent/output.py` when it publishes a review and
`critic_adjustments.py` when an applying batch changes the severities under
a ledger — and step 11 now DERIVES the published pipeline verdict from the
ledger those two write, so a threshold that drifts here reaches GitHub.
"""

import ast
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review import verdict_rules
from review.verdict_rules import (
    LEDGER_VERDICTS,
    PIPELINE_VERDICTS,
    REVIEW_VERDICTS,
    SEVERITY_RANK,
    VALID_SEVERITIES,
    VERDICT_RANK,
    publish_verdict,
    summary_for,
    verdict_for_counts,
)
from review.agent import output as output_mod


class TestVerdictForCounts:
    @pytest.mark.parametrize("counts,expected", [
        ({"critical": 1}, "block"),
        ({"critical": 1, "high": 9, "medium": 9}, "block"),
        ({"high": 3}, "block"),
        ({"high": 4}, "block"),
        ({"high": 2}, "request_changes"),
        ({"high": 1}, "request_changes"),
        ({"medium": 5}, "request_changes"),
        ({"medium": 6}, "request_changes"),
        ({"medium": 4}, "comment"),
        ({"medium": 1}, "comment"),
        ({}, "approve"),
        ({"low": 20, "info": 20}, "approve"),
    ])
    def test_the_ladder(self, counts, expected):
        assert verdict_for_counts(counts) == expected

    def test_missing_keys_read_as_zero(self):
        """Callers pass a full by_severity block or just the gating three."""
        assert verdict_for_counts({"low": 3}) == "approve"

    def test_a_full_by_severity_block_is_accepted_unchanged(self):
        assert verdict_for_counts({
            "critical": 0, "high": 0, "medium": 2, "low": 7, "info": 1,
        }) == "comment"


class TestDeriveReviewState:
    def test_advisory_findings_count_without_gating(self):
        findings = [
            {"severity": "high", "channel": "advisory"},
            {"severity": "low"},
        ]

        derived = verdict_rules.derive_review_state(findings)

        assert derived["counts"] == {
            "critical": 0,
            "high": 1,
            "medium": 0,
            "low": 1,
            "info": 0,
        }
        assert derived["verdict"] == "approve"
        assert derived["advisory"] == {
            "suppressed_advisory_finding_count": 1,
            "verdict_without_advisory": "request_changes",
        }


class TestOutputBuilderUsesTheSharedLadder:
    """The extraction is a pure refactor: output.py must not keep a second
    copy of the thresholds it can drift from."""

    @pytest.mark.parametrize("severities,expected", [
        (["critical"], "block"),
        (["high", "high", "high"], "block"),
        (["high"], "request_changes"),
        (["medium"] * 5, "request_changes"),
        (["medium"], "comment"),
        (["low", "info"], "approve"),
        ([], "approve"),
    ])
    def test_builder_verdict_matches_the_shared_rule(self, severities, expected):
        builder = output_mod.ReviewOutputBuilder(pr_id="1", reviewer="security")
        for index, sev in enumerate(severities):
            builder.add_finding(
                sev, f"t{index}", "f.py", "d", "r", line=index + 1
            )
        assert builder.to_dict()["verdict"] == expected

    def test_output_module_delegates_rather_than_reimplementing(self):
        """Guards the drift this extraction exists to prevent: a future edit
        that re-inlines the ladder in output.py passes every behavioral test
        above on the day it lands and silently diverges later."""
        source = (SCRIPTS_DIR / "review" / "agent" / "output.py").read_text()
        assert "derive_review_state" in source
        assert "return 'block'" not in source and 'return "block"' not in source


class TestSeverityRank:
    """The rank table two modules used to hand-copy."""

    def test_rank_orders_the_severity_vocabulary(self):
        assert SEVERITY_RANK == {
            "info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4,
        }

    def test_rank_covers_exactly_the_valid_severities(self):
        assert set(SEVERITY_RANK) == set(VALID_SEVERITIES)


class TestPublishVerdict:
    """The one place the ledger layer and the published layer meet."""

    @pytest.mark.parametrize("ledger,published", [
        ("approve", "APPROVE"),
        ("comment", "COMMENT"),
        ("request_changes", "REQUEST_CHANGES"),
        ("block", "REQUEST_CHANGES"),
    ])
    def test_every_ledger_verdict_publishes(self, ledger, published):
        assert publish_verdict(ledger) == published

    def test_the_mapping_is_total_over_the_ledger_vocabulary(self):
        """A ledger verdict with no published answer would publish COMMENT
        for a critical-finding review — the failure deriving the verdict
        from the ledger exists to kill."""
        assert set(LEDGER_VERDICTS) == set(VERDICT_RANK)
        for ledger in LEDGER_VERDICTS:
            assert publish_verdict(ledger) in PIPELINE_VERDICTS

    @pytest.mark.parametrize("value", [
        "not_applicable", "BLOCK", "  approve  ", "Comment", "", None, 3,
    ])
    def test_anything_outside_the_ledger_vocabulary_is_refused(self, value):
        """Callers are handed a validated ledger verdict: already lowercase,
        already stripped, never `not_applicable`. Everything else is a
        defect to name, not a value to map."""
        with pytest.raises(ValueError):
            publish_verdict(value)

    def test_a_review_may_abstain_where_a_ledger_may_not(self):
        assert REVIEW_VERDICTS == LEDGER_VERDICTS + ("not_applicable",)


class TestSummaryFor:
    def test_summary_matches_what_the_validator_expects(self):
        findings = [
            {"severity": "high", "channel": "advisory"},
            {"severity": "medium"},
        ]

        assert summary_for(findings) == {
            "verdict": "comment",
            "summary": {
                "total_findings": 2,
                "by_severity": {
                    "critical": 0, "high": 1, "medium": 1, "low": 0, "info": 0,
                },
                "suppressed_advisory_finding_count": 1,
                "verdict_without_advisory": "request_changes",
            },
        }

    def test_an_empty_review_summarizes_as_approve(self):
        assert summary_for([]) == {
            "verdict": "approve",
            "summary": {
                "total_findings": 0,
                "by_severity": {
                    "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
                },
                "suppressed_advisory_finding_count": 0,
            },
        }

    def test_a_severity_outside_the_vocabulary_fails_loudly(self):
        with pytest.raises(ValueError):
            summary_for([{"id": "f1", "severity": "catastrophic"}])


_RESPELLINGS = frozenset({
    "_LEDGER_TO_REVIEW_VERDICT",
    "_RECONCILER_VERDICTS",
    "_REVIEW_ENTRY_VERDICTS",
    "_SEVERITY_RANK",
    "_VALID_SEVERITY_FLOORS",
})


def _module_level_assignments(path):
    names = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        names.update(
            target.id for target in targets if isinstance(target, ast.Name)
        )
    return names


class TestOneVocabularyOwner:
    """Six modules used to spell a verdict or severity vocabulary of their
    own. Behavioural tests pass on the day a seventh appears; this one does
    not, which is the whole point of consolidating them."""

    def test_no_module_respells_a_verdict_or_severity_vocabulary(self):
        offenders = {}
        for path in sorted(SCRIPTS_DIR.rglob("*.py")):
            respelled = _module_level_assignments(path) & _RESPELLINGS
            if respelled:
                offenders[str(path.relative_to(SCRIPTS_DIR))] = sorted(
                    respelled
                )

        assert offenders == {}

    def test_verdict_rules_owns_every_vocabulary(self):
        owned = _module_level_assignments(
            SCRIPTS_DIR / "review" / "verdict_rules.py"
        )

        assert {
            "VALID_SEVERITIES", "SEVERITY_RANK", "VERDICT_RANK",
            "LEDGER_VERDICTS", "REVIEW_VERDICTS", "PIPELINE_VERDICTS",
        } <= owned

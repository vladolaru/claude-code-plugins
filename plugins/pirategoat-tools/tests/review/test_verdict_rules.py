"""The shared severity-to-verdict ladder.

`verdict_rules.verdict_for_counts` is the one place the thresholds live.
Two callers depend on it — `agent/output.py` when it publishes a review and
`critic_adjustments.py` when an applying batch changes the severities under
a ledger — and step 11 now DERIVES the published pipeline verdict from the
ledger those two write, so a threshold that drifts here reaches GitHub.
"""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review import verdict_rules
from review.verdict_rules import verdict_for_counts
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
    def test_advisory_issues_count_without_gating(self):
        issues = [
            {"severity": "high", "channel": "advisory"},
            {"severity": "low"},
        ]

        derived = verdict_rules.derive_review_state(issues)

        assert derived["counts"] == {
            "critical": 0,
            "high": 1,
            "medium": 0,
            "low": 1,
            "info": 0,
        }
        assert derived["verdict"] == "approve"
        assert derived["advisory"] == {
            "advisory_suppressed": 1,
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
            builder.add_issue(sev, f"t{index}", "f.py", "d", "r", line=index + 1)
        assert builder.to_dict()["verdict"] == expected

    def test_output_module_delegates_rather_than_reimplementing(self):
        """Guards the drift this extraction exists to prevent: a future edit
        that re-inlines the ladder in output.py passes every behavioral test
        above on the day it lands and silently diverges later."""
        source = (SCRIPTS_DIR / "review" / "agent" / "output.py").read_text()
        assert "derive_review_state" in source
        assert "return 'block'" not in source and 'return "block"' not in source

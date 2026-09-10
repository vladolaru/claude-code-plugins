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
    SEVERITY_RANK,
    VALID_SEVERITIES,
    publish_verdict,
    summary_for,
    verdict_for_counts,
)
from review.agent import output as output_mod


class TestVerdictForCounts:
    @pytest.mark.parametrize("counts,expected", [
        pytest.param({"critical": 1}, "block", id="critical_gt_0-min"),
        pytest.param({"high": 3}, "block", id="high_gte_3-min"),
        pytest.param({"high": 2}, "request_changes", id="high_gt_0-just_below_block"),
        pytest.param({"medium": 5}, "request_changes", id="medium_gte_5-min"),
        pytest.param(
            {"critical": 0, "high": 0, "medium": 4, "low": 7, "info": 1},
            "comment",
            id="medium_gt_0-just_below_request_changes-full_block",
        ),
        pytest.param({}, "approve", id="no_keys-base_case"),
        pytest.param(
            {"low": 20, "info": 20}, "approve",
            id="missing_gating_keys_read_as_zero-non_gating_ignored",
        ),
    ])
    def test_the_ladder(self, counts, expected):
        """One row per boundary of the five-branch ladder (critical>0,
        high>=3, high>0-or-medium>=5, medium>0, else); the `full_block`
        row also proves a complete `by_severity` block with explicit
        zeros is accepted like a partial one, and the last row folds in
        the missing-keys-default contract callers rely on."""
        assert verdict_for_counts(counts) == expected


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
    """The extraction is a pure refactor: no module may keep a second copy
    of the thresholds it can drift from."""

    @pytest.mark.parametrize("severities,expected", [
        (["critical"], "block"),
        ([], "approve"),
    ])
    def test_builder_verdict_matches_the_shared_rule(self, severities, expected):
        """A parity guard, not a re-test of the ladder's branches: the
        builder reaches `verdict_for_counts` through `summary_for`, a
        single call site, so one row at each end of the vocabulary is
        enough to prove the wiring."""
        builder = output_mod.ReviewOutputBuilder(pr_id="1", reviewer="security")
        for index, sev in enumerate(severities):
            builder.add_finding(
                sev, f"t{index}", "f.py", "d", "r", line=index + 1
            )
        assert builder.to_dict()["verdict"] == expected

    def test_no_module_reimplements_the_ladder(self):
        """Guards the drift this extraction exists to prevent: an edit that
        re-inlines the thresholds anywhere passes every behavioural test
        above on the day it lands and silently diverges later. Scanning one
        file stopped being enough once the validators and the builder lived
        in different ones."""
        owner = SCRIPTS_DIR / "review" / "verdict_rules.py"
        offenders = []
        for path in sorted((SCRIPTS_DIR / "review").rglob("*.py")):
            if path == owner:
                continue
            source = path.read_text(encoding="utf-8")
            for verdict in ("block", "request_changes", "comment", "approve"):
                if (
                    f"return '{verdict}'" in source
                    or f'return "{verdict}"' in source
                ):
                    offenders.append((path.name, verdict))

        assert offenders == []


class TestSeverityRank:
    """The rank table two modules used to hand-copy."""

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

    @pytest.mark.parametrize("value", ["not_applicable", None])
    def test_anything_outside_the_ledger_vocabulary_is_refused(self, value):
        """Callers are handed a validated ledger verdict: already lowercase,
        already stripped, never `not_applicable` — the one near-miss a
        caller could plausibly pass. `None` proves the guard is not
        merely a string check. Both land on the same dict-miss branch of
        `publish_verdict`; the function's other except clause (an
        unhashable value) has no caller that could produce one."""
        with pytest.raises(ValueError):
            publish_verdict(value)


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
    own. This is a tripwire on those six retired names: it does not notice
    a seventh copy under a name nobody has used yet, but it does fail the
    moment one of the consolidated spellings comes back."""

    def test_no_module_respells_a_verdict_or_severity_vocabulary(self):
        offenders = {}
        for path in sorted(SCRIPTS_DIR.rglob("*.py")):
            respelled = _module_level_assignments(path) & _RESPELLINGS
            if respelled:
                offenders[str(path.relative_to(SCRIPTS_DIR))] = sorted(
                    respelled
                )

        assert offenders == {}

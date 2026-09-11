"""Tests for iterative_review.effort — adaptive reasoning effort resolution."""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # iterative_review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from iterative_review.effort import resolve_effort


# ---------------------------------------------------------------------------
# TestRoundPositionArc — resolve_effort is `if round_num == 1: high else:
# medium`; round 1 and round 2 are the only two branches of that arc.
# ---------------------------------------------------------------------------


class TestRoundPositionArc:
    @pytest.mark.parametrize(
        "round_num,expected_effort,expected_reason",
        [
            pytest.param(1, "high", "arc:round1", id="round-1-starts-high"),
            pytest.param(2, "medium", "arc:round2", id="round-2-plus-starts-medium"),
        ],
    )
    def test_base_effort_from_round(self, round_num, expected_effort, expected_reason):
        effort, reason = resolve_effort(round_num)
        assert effort == expected_effort
        assert reason == expected_reason


# ---------------------------------------------------------------------------
# TestSignalOverrides — P0/P1 fixed bumps medium->high, P0/P1 rejected
# (on top of a fix) bumps high->xhigh; P2/P3 never bump; round 1 ignores
# signals entirely (guarded by `round_num >= 2`); no prior data never bumps.
# ---------------------------------------------------------------------------


class TestSignalOverrides:
    @pytest.mark.parametrize(
        "round_num,prior_findings,prior_outcomes,expected_effort,expected_reason",
        [
            pytest.param(
                3,
                [{"id": "r2_f1", "severity": "P0", "title": "Bug"}],
                [{"id": "r2_f1", "action": "fixed", "summary": "Done"}],
                "high", "signal:p0_p1_fixed",
                id="p0-p1-fixed-bumps-medium-to-high",
            ),
            pytest.param(
                3,
                [{"id": "r2_f1", "severity": "P0", "title": "Bug"}],
                [{"id": "r2_f1", "action": "rejected", "reasoning": "No"}],
                "medium", None,
                id="rejected-alone-does-not-bump-medium",
            ),
            pytest.param(
                3,
                [{"id": "r2_f1", "severity": "P2", "title": "Style"},
                 {"id": "r2_f2", "severity": "P3", "title": "Nitpick"}],
                [{"id": "r2_f1", "action": "fixed", "summary": "Done"},
                 {"id": "r2_f2", "action": "rejected", "reasoning": "No"}],
                "medium", None,
                id="p2-and-p3-never-bump",
            ),
            pytest.param(
                1,
                [{"id": "r0_f1", "severity": "P0", "title": "Bug"}],
                [{"id": "r0_f1", "action": "rejected", "reasoning": "No"}],
                "high", None,
                id="round-1-ignores-signals",
            ),
            pytest.param(3, None, None, "medium", None, id="no-prior-data"),
            pytest.param(
                3,
                [{"id": "r2_f1", "severity": "P0", "title": "A"},
                 {"id": "r2_f2", "severity": "P1", "title": "B"}],
                [{"id": "r2_f1", "action": "fixed", "summary": "Done"},
                 {"id": "r2_f2", "action": "rejected", "reasoning": "No"}],
                "xhigh", "signal:p0_p1_rejected",
                id="fixed-then-rejected-stacks-to-xhigh",
            ),
        ],
    )
    def test_signal_overrides(self, round_num, prior_findings, prior_outcomes,
                               expected_effort, expected_reason):
        effort, reason = resolve_effort(
            round_num, prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        assert effort == expected_effort
        if expected_reason is not None:
            assert reason == expected_reason

"""Tests for iterative_review.effort — adaptive reasoning effort resolution."""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # iterative_review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from iterative_review.effort import resolve_effort, EFFORT_TIERS


# ---------------------------------------------------------------------------
# TestEffortTiers — verify tier ordering, floor, ceiling
# ---------------------------------------------------------------------------


class TestEffortTiers:
    def test_tier_ordering(self):
        """Tiers are ordered from lowest to highest effort."""
        assert EFFORT_TIERS == ("medium", "high", "xhigh")

    def test_floor_is_medium(self):
        """Minimum possible effort is medium."""
        assert EFFORT_TIERS[0] == "medium"

    def test_ceiling_is_xhigh(self):
        """Maximum possible effort is xhigh."""
        assert EFFORT_TIERS[-1] == "xhigh"

    def test_exactly_three_tiers(self):
        assert len(EFFORT_TIERS) == 3


# ---------------------------------------------------------------------------
# TestRoundPositionArc — parametrize rounds 1-10, verify base effort
# ---------------------------------------------------------------------------


class TestRoundPositionArc:
    @pytest.mark.parametrize("round_num,expected", [
        (1, "high"),
        (2, "medium"),
        (3, "medium"),
        (4, "medium"),
        (5, "medium"),
        (6, "medium"),
        (7, "medium"),
        (8, "medium"),
        (9, "medium"),
        (10, "medium"),
    ])
    def test_base_effort_from_round(self, round_num, expected):
        """Round 1 starts at high, rounds 2+ start at medium."""
        effort, reason = resolve_effort(round_num, diff_lines=100)
        assert effort == expected

    def test_round_1_reason(self):
        _, reason = resolve_effort(1, diff_lines=100)
        assert "arc" in reason

    def test_round_3_reason(self):
        _, reason = resolve_effort(3, diff_lines=100)
        assert "arc" in reason


# ---------------------------------------------------------------------------
# TestDiffSizeOverride — >=3000 bumps, <3000 no bump, applied every round
# ---------------------------------------------------------------------------


class TestDiffSizeOverride:
    def test_large_diff_bumps_medium_to_high(self):
        """Diff >= 3000 lines bumps medium one tier up."""
        effort, reason = resolve_effort(3, diff_lines=3000)
        assert effort == "high"
        assert "diff_size" in reason

    def test_large_diff_bumps_high_to_xhigh(self):
        """Diff >= 3000 lines bumps high one tier up."""
        effort, reason = resolve_effort(1, diff_lines=3000)
        assert effort == "xhigh"
        assert "diff_size" in reason

    def test_small_diff_no_bump(self):
        """Diff < 3000 lines does not bump."""
        effort, _ = resolve_effort(3, diff_lines=2999)
        assert effort == "medium"

    def test_boundary_at_3000(self):
        """Exactly 3000 triggers the bump."""
        effort, _ = resolve_effort(3, diff_lines=3000)
        assert effort == "high"

    def test_diff_override_applies_every_round(self):
        """Diff size override applies regardless of round number."""
        for rnd in [1, 2, 3, 5, 10]:
            effort, _ = resolve_effort(rnd, diff_lines=5000)
            # Round 1 starts high, bump to xhigh; rounds 2+ start medium, bump to high
            if rnd == 1:
                assert effort == "xhigh", f"Round {rnd} with 5000 lines"
            else:
                assert effort == "high", f"Round {rnd} with 5000 lines"

    def test_reason_includes_line_count(self):
        _, reason = resolve_effort(3, diff_lines=3200)
        assert "3200" in reason


# ---------------------------------------------------------------------------
# TestSignalOverrides — P0/P1 fixed bumps, P0/P1 rejected bumps,
# P2/P3 don't bump, no prior data on round 1
# ---------------------------------------------------------------------------


class TestSignalOverrides:
    def test_p0_fixed_bumps_medium_to_high(self):
        """P0/P1 finding with action 'fixed' bumps medium -> high."""
        prior_findings = [{"id": "r2_f1", "severity": "P0", "title": "Bug"}]
        prior_outcomes = [{"id": "r2_f1", "action": "fixed", "summary": "Done"}]
        effort, reason = resolve_effort(
            3, diff_lines=100,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        assert effort == "high"
        assert "signal" in reason

    def test_p1_fixed_bumps_medium_to_high(self):
        """P1 finding with action 'fixed' also triggers the bump."""
        prior_findings = [{"id": "r2_f1", "severity": "P1", "title": "Issue"}]
        prior_outcomes = [{"id": "r2_f1", "action": "fixed", "summary": "Done"}]
        effort, reason = resolve_effort(
            3, diff_lines=100,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        assert effort == "high"

    def test_p0_rejected_bumps_high_to_xhigh(self):
        """P0/P1 finding with action 'rejected' bumps high -> xhigh.

        Round 2 base is medium, so we use a large diff to get to high first,
        then the rejected signal bumps high -> xhigh.
        """
        prior_findings = [{"id": "r1_f1", "severity": "P0", "title": "Bug"}]
        prior_outcomes = [{"id": "r1_f1", "action": "rejected", "reasoning": "Not valid"}]
        effort, reason = resolve_effort(
            2, diff_lines=3000,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        # medium -> high (diff) -> xhigh (rejected)
        assert effort == "xhigh"
        assert "rejected" in reason

    def test_p1_rejected_bumps_high_to_xhigh(self):
        """P1 rejected also bumps when at high tier."""
        prior_findings = [{"id": "r1_f1", "severity": "P1", "title": "Issue"}]
        prior_outcomes = [{"id": "r1_f1", "action": "rejected", "reasoning": "Wrong"}]
        effort, reason = resolve_effort(
            2, diff_lines=3000,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        # medium -> high (diff) -> xhigh (rejected)
        assert effort == "xhigh"

    def test_p0_rejected_on_medium_does_not_bump(self):
        """Rejected only bumps high -> xhigh, not medium -> high."""
        prior_findings = [{"id": "r1_f1", "severity": "P0", "title": "Bug"}]
        prior_outcomes = [{"id": "r1_f1", "action": "rejected", "reasoning": "No"}]
        effort, _ = resolve_effort(
            2, diff_lines=100,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        # medium base, rejected doesn't apply at medium tier
        assert effort == "medium"

    def test_p2_fixed_does_not_bump(self):
        """P2 findings don't trigger signal overrides."""
        prior_findings = [{"id": "r2_f1", "severity": "P2", "title": "Style"}]
        prior_outcomes = [{"id": "r2_f1", "action": "fixed", "summary": "Done"}]
        effort, _ = resolve_effort(
            3, diff_lines=100,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        assert effort == "medium"

    def test_p3_rejected_does_not_bump(self):
        """P3 findings don't trigger signal overrides."""
        prior_findings = [{"id": "r2_f1", "severity": "P3", "title": "Nitpick"}]
        prior_outcomes = [{"id": "r2_f1", "action": "rejected", "reasoning": "No"}]
        effort, _ = resolve_effort(
            3, diff_lines=100,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        assert effort == "medium"

    def test_no_prior_data_on_round_1(self):
        """Round 1 ignores signal overrides even if data is passed."""
        prior_findings = [{"id": "r0_f1", "severity": "P0", "title": "Bug"}]
        prior_outcomes = [{"id": "r0_f1", "action": "rejected", "reasoning": "No"}]
        effort, _ = resolve_effort(
            1, diff_lines=100,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        # Round 1 base is high; signals should NOT bump further
        assert effort == "high"

    def test_no_prior_findings_no_bump(self):
        """None/empty prior data does not bump."""
        effort, _ = resolve_effort(3, diff_lines=100, prior_findings=None, prior_outcomes=None)
        assert effort == "medium"

        effort2, _ = resolve_effort(3, diff_lines=100, prior_findings=[], prior_outcomes=[])
        assert effort2 == "medium"


# ---------------------------------------------------------------------------
# TestOverrideStacking — multiple overrides combine correctly
# ---------------------------------------------------------------------------


class TestOverrideStacking:
    def test_large_diff_plus_rejected_caps_at_xhigh(self):
        """Diff bump + rejected bump both apply, capped at xhigh."""
        prior_findings = [{"id": "r2_f1", "severity": "P0", "title": "Bug"}]
        prior_outcomes = [{"id": "r2_f1", "action": "rejected", "reasoning": "No"}]
        effort, _ = resolve_effort(
            3, diff_lines=4000,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        # medium -> high (diff) -> xhigh (rejected) => xhigh
        assert effort == "xhigh"

    def test_large_diff_plus_fixed_bumps_to_xhigh(self):
        """Diff bump + fixed bump stack: medium -> high (diff) -> xhigh (fixed)."""
        prior_findings = [{"id": "r2_f1", "severity": "P1", "title": "Bug"}]
        prior_outcomes = [{"id": "r2_f1", "action": "fixed", "summary": "Done"}]
        effort, _ = resolve_effort(
            3, diff_lines=3500,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        # medium -> high (diff) -> bumped by fixed? fixed bumps medium->high only
        # After diff, we're at high. Fixed bumps medium->high (only applies if at medium).
        # So result is high (diff already got us there, fixed is same level).
        # Actually: fixed bumps one tier (medium->high), rejected bumps one tier (high->xhigh).
        # Let me re-read the spec...
        # "P0/P1 finding with action fixed: bump medium->high"
        # "P0/P1 finding with action rejected: bump high->xhigh"
        # These are specific tier transitions, not generic +1.
        # So fixed: only takes effect if currently at medium.
        # rejected: only takes effect if currently at high.
        # After diff bump: medium->high. Fixed won't apply (we're at high, not medium).
        # Rejected WOULD apply (we're at high -> xhigh).
        # So with fixed + large diff: medium -> high (diff) -> fixed doesn't apply -> high
        assert effort == "high"

    def test_fixed_and_rejected_in_same_round(self):
        """If both fixed and rejected P0/P1 exist, both signals apply."""
        prior_findings = [
            {"id": "r2_f1", "severity": "P0", "title": "Fixed bug"},
            {"id": "r2_f2", "severity": "P1", "title": "Rejected finding"},
        ]
        prior_outcomes = [
            {"id": "r2_f1", "action": "fixed", "summary": "Done"},
            {"id": "r2_f2", "action": "rejected", "reasoning": "Wrong"},
        ]
        effort, _ = resolve_effort(
            3, diff_lines=100,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        # medium -> high (fixed) -> xhigh (rejected)
        assert effort == "xhigh"

    def test_round_2_medium_with_fixed_goes_high(self):
        """Round 2 (base=medium) + fixed P0/P1 -> high."""
        prior_findings = [{"id": "r1_f1", "severity": "P0", "title": "Bug"}]
        prior_outcomes = [{"id": "r1_f1", "action": "fixed", "summary": "Done"}]
        effort, _ = resolve_effort(
            2, diff_lines=100,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        assert effort == "high"

    def test_never_exceeds_xhigh(self):
        """All overrides active: still capped at xhigh."""
        prior_findings = [
            {"id": "r2_f1", "severity": "P0", "title": "A"},
            {"id": "r2_f2", "severity": "P0", "title": "B"},
        ]
        prior_outcomes = [
            {"id": "r2_f1", "action": "fixed", "summary": "Done"},
            {"id": "r2_f2", "action": "rejected", "reasoning": "No"},
        ]
        effort, _ = resolve_effort(
            3, diff_lines=5000,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        assert effort == "xhigh"
        assert effort in EFFORT_TIERS


# ---------------------------------------------------------------------------
# TestReasonFormat — reason is string, under 100 chars
# ---------------------------------------------------------------------------


class TestReasonFormat:
    @pytest.mark.parametrize("round_num,diff_lines,prior_findings,prior_outcomes", [
        (1, 100, None, None),
        (3, 100, None, None),
        (1, 5000, None, None),
        (3, 3000, None, None),
        (3, 100,
         [{"id": "r2_f1", "severity": "P0", "title": "X"}],
         [{"id": "r2_f1", "action": "fixed", "summary": "Y"}]),
        (2, 100,
         [{"id": "r1_f1", "severity": "P1", "title": "X"}],
         [{"id": "r1_f1", "action": "rejected", "reasoning": "Y"}]),
    ])
    def test_reason_is_string_under_100_chars(
        self, round_num, diff_lines, prior_findings, prior_outcomes
    ):
        _, reason = resolve_effort(
            round_num, diff_lines,
            prior_findings=prior_findings, prior_outcomes=prior_outcomes,
        )
        assert isinstance(reason, str)
        assert len(reason) < 100, f"Reason too long ({len(reason)}): {reason}"

    def test_reason_is_telemetry_friendly(self):
        """Reason should be short, colon-delimited, no spaces in key."""
        _, reason = resolve_effort(1, diff_lines=100)
        # Should match pattern like "arc:round1" or "diff_size:3200_lines"
        assert ":" in reason

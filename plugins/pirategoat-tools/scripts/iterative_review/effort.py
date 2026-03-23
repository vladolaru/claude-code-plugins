"""Adaptive reasoning effort resolution for iterative review.

Pure-logic module with no I/O. Determines the effort tier for a review round
based on round position and prior round signals.
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EFFORT_TIERS = ("medium", "high", "xhigh")  # ordered low to high

_TIER_INDEX = {tier: i for i, tier in enumerate(EFFORT_TIERS)}

_SIGNAL_SEVERITIES = {"P0", "P1"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bump(current_tier, levels=1):
    """Bump a tier up by N levels, capped at ceiling."""
    idx = _TIER_INDEX[current_tier]
    new_idx = min(idx + levels, len(EFFORT_TIERS) - 1)
    return EFFORT_TIERS[new_idx]


def _has_signal(prior_findings, prior_outcomes, action):
    """Check if any P0/P1 finding in prior round has the given action."""
    if not prior_findings or not prior_outcomes:
        return False
    outcome_map = {o["id"]: o for o in prior_outcomes}
    for finding in prior_findings:
        severity = finding.get("severity", "")
        if severity not in _SIGNAL_SEVERITIES:
            continue
        outcome = outcome_map.get(finding["id"])
        if outcome and outcome.get("action") == action:
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_effort(round_num, prior_findings=None, prior_outcomes=None):
    """Resolve the reasoning effort tier for a review round.

    Returns (effort, reason) tuple where effort is one of EFFORT_TIERS
    and reason is a short telemetry-friendly string.

    Resolution rules (applied in order, each step can only bump UP, never down):

    Step 1 - Base effort from round position:
        Round 1:  high
        Round 2+: medium

    Step 2 - Signal overrides (round 2+ only):
        P0/P1 fixed in prior round:    bump medium -> high
        P0/P1 rejected in prior round: bump high -> xhigh
        (rejected checked second so it stacks on top of fixed)
    """
    reasons = []

    # Step 1: Base effort from round position
    if round_num == 1:
        effort = "high"
    else:
        effort = "medium"
    reasons.append(f"arc:round{round_num}")

    # Step 2: Signal overrides (round 2+ only)
    if round_num >= 2 and prior_findings and prior_outcomes:
        # Check fixed first (weaker signal)
        has_fixed = _has_signal(prior_findings, prior_outcomes, "fixed")
        if has_fixed and effort == "medium":
            effort = _bump(effort)
            reasons.append("signal:p0_p1_fixed")

        # Check rejected second (stronger signal, can stack)
        has_rejected = _has_signal(prior_findings, prior_outcomes, "rejected")
        if has_rejected and effort == "high":
            effort = _bump(effort)
            reasons.append("signal:p0_p1_rejected")

    # Return the last reason as the primary (most specific override)
    return effort, reasons[-1]

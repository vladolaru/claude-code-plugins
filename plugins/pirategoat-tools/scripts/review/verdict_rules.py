#!/usr/bin/env python3
"""The severity-to-verdict thresholds, in one place.

Two modules answer "what verdict do these findings carry": `agent/output.py`
when a reviewer or the reconciliator publishes a review, and
`critic_adjustments.py` when an applying critic batch changes the severities
under an already-published ledger. Before this module existed only the first
one did — `_recount_summary()` rebuilt `summary.by_severity` and left
`verdict` exactly as the reconciliator had written it — so a REVISE batch
that demoted the last high finding published a `request_changes` ledger over
an issue list that no longer justified one. Step 11's verdict sync used to
paper over that by copying the orchestrator's transcribed verdict into the
ledger; with the verdict now DERIVED from the ledger, a stale ledger verdict
is machine authority for a wrong published verdict, so the thresholds have to
be a shared rule rather than one module's private ladder.

Counts in, verdict out. No file access, no severity vocabulary of its own,
nothing that would make this hard to call from either side.
"""

# The three severities that gate. `low` and `info` count toward
# `summary.total_issues` and are rendered, but never move a verdict — a
# reviewer that records only informational notes still approves.
GATING_SEVERITIES = ("critical", "high", "medium")


def verdict_for_counts(counts) -> str:
    """Map severity counts onto the findings verdict vocabulary.

    `counts` is any mapping from severity name to count; missing keys read
    as zero, so a caller may pass a full `summary.by_severity` block or just
    the three gating severities.

    Returns one of `block`, `request_changes`, `comment`, `approve` — the
    lowercase per-review vocabulary of `schemas/review-output.ts`, NOT the
    outer-pipeline `APPROVE`/`COMMENT`/`REQUEST_CHANGES` values
    `pipeline-result.json` publishes. `orchestration.py` owns the mapping
    between the two layers.
    """
    critical = counts.get("critical", 0)
    high = counts.get("high", 0)
    medium = counts.get("medium", 0)

    if critical > 0:
        return "block"
    if high >= 3:
        return "block"
    if high > 0 or medium >= 5:
        return "request_changes"
    if medium > 0:
        return "comment"
    return "approve"

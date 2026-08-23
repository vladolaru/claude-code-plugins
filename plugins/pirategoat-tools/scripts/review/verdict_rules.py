#!/usr/bin/env python3
"""Channel-aware review verdict derivation, in one place.

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

The threshold ladder remains available for count-based consumers, while
``derive_review_state()`` owns the issue-population policy shared by both
writers: complete counts, advisory exclusion from gating, and advisory
suppression measurement. No file access or write-boundary policy lives here.
"""

VALID_SEVERITIES = ("critical", "high", "medium", "low", "info")
VERDICT_RANK = {
    "approve": 0,
    "comment": 1,
    "request_changes": 2,
    "block": 3,
}


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


def derive_review_state(issues):
    """Derive counts, gating verdict, and advisory measurement from issues.

    Every issue must be a dictionary carrying one severity from
    ``VALID_SEVERITIES``. Advisory issues remain in the complete counts but
    are excluded from the counts that gate the verdict. The counterfactual
    verdict over the full population is exposed only when advisory
    suppression actually softened the gating verdict.
    """
    counts = {severity: 0 for severity in VALID_SEVERITIES}
    blocking_counts = {severity: 0 for severity in VALID_SEVERITIES}
    advisory_suppressed = 0

    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise ValueError(f"issue at position {index} is not an object")
        severity = issue.get("severity")
        if severity not in counts:
            raise ValueError(
                f"issue {issue.get('id')!r} has severity {severity!r} "
                f"outside the vocabulary "
                f"(allowed: {', '.join(VALID_SEVERITIES)})"
            )
        counts[severity] += 1
        if issue.get("channel") == "advisory":
            advisory_suppressed += 1
        else:
            blocking_counts[severity] += 1

    verdict = verdict_for_counts(blocking_counts)
    verdict_without_advisory = verdict_for_counts(counts)
    advisory = {"advisory_suppressed": advisory_suppressed}
    if VERDICT_RANK[verdict_without_advisory] > VERDICT_RANK[verdict]:
        advisory["verdict_without_advisory"] = verdict_without_advisory

    return {"counts": counts, "verdict": verdict, "advisory": advisory}

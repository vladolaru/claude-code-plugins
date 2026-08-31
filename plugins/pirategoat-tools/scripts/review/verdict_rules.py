#!/usr/bin/env python3
"""Channel-aware review verdict derivation, in one place.

Two modules answer "what verdict do these findings carry": `agent/output.py`
when a reviewer or the reconciliator publishes a review, and
`critic_adjustments.py` when an applying critic batch changes the severities
under an already-published ledger. Before this module existed only the first
one did — `_recount_summary()` rebuilt `summary.by_severity` and left
`verdict` exactly as the reconciliator had written it — so a REVISE batch
that demoted the last high finding published a `request_changes` ledger over
a finding list that no longer justified one. Step 11's verdict sync used to
paper over that by copying the orchestrator's transcribed verdict into the
ledger; with the verdict now DERIVED from the ledger, a stale ledger verdict
is machine authority for a wrong published verdict, so the thresholds have to
be a shared rule rather than one module's private ladder.

The threshold ladder remains available for count-based consumers, while
``derive_review_state()`` owns the finding-population policy shared by both
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

# Severity order, derived from the vocabulary rather than restated beside
# it. Two hand-copies of this table existed — `agent/output.py` used it to
# promote a finding to its `severity_floor`, `analysis/session_analyzer.py`
# to reconstruct what the builder would have saved — and neither was
# sourced from the other, so a severity added here reached one of them.
SEVERITY_RANK = {
    severity: rank
    for rank, severity in enumerate(reversed(VALID_SEVERITIES))
}

# The three verdict layers, narrowest first.
#
# LEDGER_VERDICTS is what `verdict_for_counts` computes and what a
# reconciled findings ledger may carry. REVIEW_VERDICTS adds the one
# verdict no ladder produces: a reviewer with nothing in scope abstains,
# and abstention is not a threshold outcome. PIPELINE_VERDICTS is the
# uppercase layer a reviewer echoes in its return signal; `publish_verdict`
# maps onto the three of them the terminal result can carry, and BLOCK
# is in the tuple for the return signal alone.
LEDGER_VERDICTS = tuple(VERDICT_RANK)
REVIEW_VERDICTS = LEDGER_VERDICTS + ("not_applicable",)
PIPELINE_VERDICTS = ("APPROVE", "COMMENT", "REQUEST_CHANGES", "BLOCK")

_PUBLISHED_BY_LEDGER_VERDICT = {
    "approve": "APPROVE",
    "comment": "COMMENT",
    "request_changes": "REQUEST_CHANGES",
    "block": "REQUEST_CHANGES",
}


def publish_verdict(ledger_verdict) -> str:
    """Map one ledger verdict onto the verdict the pipeline publishes.

    The one place the two layers meet. `block` is real — any critical
    finding, or three highs — and publishes REQUEST_CHANGES because GitHub
    has no stronger action; dropping it would publish COMMENT over a
    critical finding, which is the exact failure deriving the published
    verdict from the ledger exists to kill.

    Total over `LEDGER_VERDICTS` and strict about everything else. Both
    callers read a ledger that passed `validate_findings_document()`, so the
    verdict is already lowercase, already stripped, and never
    `not_applicable` — a value outside the vocabulary is a defect to name,
    not a case to fall back from.
    """
    try:
        return _PUBLISHED_BY_LEDGER_VERDICT[ledger_verdict]
    except (KeyError, TypeError):
        raise ValueError(
            f"{ledger_verdict!r} is not a ledger verdict "
            f"(allowed: {', '.join(LEDGER_VERDICTS)})"
        ) from None


def summary_for(findings) -> dict:
    """The verdict and summary block one finding list justifies.

    Returns ``{"verdict": ..., "summary": {...}}``. The summary block is
    exactly what ``validate_review_content()`` reconstructs and compares
    against, so a writer that builds it here cannot publish a summary its
    own validator rejects. The verdict travels alongside because it cannot
    be recovered from the block: ``by_severity`` counts advisory findings
    and the gating verdict excludes them.

    Both writers of a review's summary call this — ``to_dict()`` when a
    reviewer or the reconciliator publishes, ``_recount_summary()`` when an
    applying critic batch changes severities under a published ledger.
    """
    derived = derive_review_state(findings)
    summary = {
        "total_findings": len(findings),
        "by_severity": derived["counts"],
    }
    summary.update(derived["advisory"])
    return {"verdict": derived["verdict"], "summary": summary}


def verdict_for_counts(counts) -> str:
    """Map severity counts onto the findings verdict vocabulary.

    `counts` is any mapping from severity name to count; missing keys read
    as zero, so a caller may pass a full `summary.by_severity` block or just
    the three gating severities.

    Returns one of `block`, `request_changes`, `comment`, `approve` — the
    lowercase per-review vocabulary of `schemas/review-output.ts`, NOT the
    outer-pipeline `APPROVE`/`COMMENT`/`REQUEST_CHANGES` values
    the terminal pipeline result publishes. `orchestration.py` owns the mapping
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


def derive_review_state(findings):
    """Derive counts, gating verdict, and advisory measurement from findings.

    Every finding must be a dictionary carrying one severity from
    ``VALID_SEVERITIES``. Advisory findings remain in the complete counts but
    are excluded from the counts that gate the verdict. The counterfactual
    verdict over the full population is exposed only when advisory
    suppression actually softened the gating verdict.
    """
    counts = {severity: 0 for severity in VALID_SEVERITIES}
    blocking_counts = {severity: 0 for severity in VALID_SEVERITIES}
    suppressed_advisory_finding_count = 0

    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"finding at position {index} is not an object")
        severity = finding.get("severity")
        if severity not in counts:
            raise ValueError(
                f"finding {finding.get('id')!r} has severity {severity!r} "
                f"outside the vocabulary "
                f"(allowed: {', '.join(VALID_SEVERITIES)})"
            )
        counts[severity] += 1
        if finding.get("channel") == "advisory":
            suppressed_advisory_finding_count += 1
        else:
            blocking_counts[severity] += 1

    verdict = verdict_for_counts(blocking_counts)
    verdict_without_advisory = verdict_for_counts(counts)
    advisory = {
        "suppressed_advisory_finding_count": (
            suppressed_advisory_finding_count
        )
    }
    if VERDICT_RANK[verdict_without_advisory] > VERDICT_RANK[verdict]:
        advisory["verdict_without_advisory"] = verdict_without_advisory

    return {"counts": counts, "verdict": verdict, "advisory": advisory}

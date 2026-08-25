"""Canonical finalized-review fixtures for consumer boundary tests."""


def canonical_review_document(
    reviewer="security",
    severities=(),
    *,
    reviewed_file_claims=(),
    review_claimable_files=(),
):
    """Return one complete schema-2 review with hand-derived expectations."""
    findings = [
        {
            "id": f"f{index}",
            "category": "general",
            "severity": severity,
            "title": f"{severity.title()} finding {index}",
            "description": "Observed behavior",
            "file": f"src/file-{index}.py",
            "line": index,
            "recommendation": "Correct the behavior",
            "confidence": 0.9,
        }
        for index, severity in enumerate(severities, 1)
    ]
    counts = {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0,
    }
    for severity in severities:
        counts[severity] += 1
    if counts["critical"] or counts["high"] >= 3:
        verdict = "block"
    elif counts["high"] or counts["medium"] >= 5:
        verdict = "request_changes"
    elif counts["medium"]:
        verdict = "comment"
    else:
        verdict = "approve"

    claimable = list(review_claimable_files)
    claimed = list(reviewed_file_claims)
    unclaimed = [path for path in claimable if path not in set(claimed)]
    return {
        "pr_id": "42",
        "reviewer": reviewer,
        "timestamp": "2026-08-26T00:00:00+03:00",
        "plugin_version": None,
        "schema": 2,
        "verdict": verdict,
        "summary": {
            "total_findings": len(findings),
            "by_severity": counts,
            "suppressed_advisory_finding_count": 0,
        },
        "findings": findings,
        "review_claimable_files": claimable,
        "reviewed_file_claims": claimed,
        "unclaimed_review_files": unclaimed,
        "inline_diff_file_count": 0,
        "review_accounted_file_count": len(claimed),
        "in_scope_review_file_count": len(claimable),
        "observations": None,
        "recommendations": None,
        "positive_observations": None,
        "checks": [],
        "assessment": None,
        "meta": {
            "review_duration_ms": 10,
            "confidence_score": 0.9,
            "next_finding_number": len(findings) + 1,
            "next_check_number": 1,
        },
    }

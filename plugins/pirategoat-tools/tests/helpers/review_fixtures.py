"""Canonical finalized-review fixtures for consumer boundary tests."""

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from review.verdict_rules import derive_review_state


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
    derived = derive_review_state(findings)

    claimable = list(review_claimable_files)
    claimed = list(reviewed_file_claims)
    unclaimed = [path for path in claimable if path not in set(claimed)]
    return {
        "pr_id": "42",
        "reviewer": reviewer,
        "timestamp": "2026-08-26T00:00:00+03:00",
        "plugin_version": None,
        "schema": 2,
        "verdict": derived["verdict"],
        "summary": {
            "total_findings": len(findings),
            "by_severity": derived["counts"],
            **derived["advisory"],
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


def canonical_findings_ledger(severities=(), *, checks=(), reconciliation=None):
    """Return one exact schema-3 findings ledger."""
    document = canonical_review_document("reconciliator", severities)
    for field in (
        "reviewer", "review_claimable_files", "reviewed_file_claims",
        "unclaimed_review_files", "inline_diff_file_count",
        "review_accounted_file_count", "in_scope_review_file_count",
    ):
        document.pop(field)
    document["schema"] = 3
    document["checks"] = list(checks)
    document["meta"]["next_check_number"] = len(checks) + 1
    count = len(document["findings"])
    document["meta"]["reconciliation"] = {
        "grouped_concern_count": count,
        "verified_concern_count": count,
        "false_positive_concern_count": 0,
        "out_of_scope_concern_count": 0,
        "input_finding_count": count,
        "contributing_agent_count": 1 if count else 0,
        "reviewing_agents": ["security-reviewer"],
        "not_applicable_agents": [],
        "dispatched_agents": ["security-reviewer"],
        "missing_agents": [],
        **(reconciliation or {}),
    }
    return document

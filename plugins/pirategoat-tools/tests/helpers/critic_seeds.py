"""Ledger and proposal seed helpers shared by the critic-adjustments and
step-11 orchestration test files.

Both `tests/review/test_critic_adjustments.py` and
`tests/review/test_step_11.py` need a findings ledger built the way the
reconciliator builds it, a critic proposal or verdict published the way
`critic.py --save` publishes it, and an adjudication run the way
`adjudicate()` runs it — every helper here goes through the real
production write path (`write_findings`, `write_critic_verdict`,
`adjudicate`) rather than hand-rolling JSON, so a test seeded through
these helpers exercises the same channel a real run does. Import from
here; do not copy a helper's body into either test file — a second copy
is exactly the drift this module exists to prevent.
"""

import json
import re
from pathlib import Path

from review import critic_adjustments as critic_adjustments_module
from review.critic_adjustments import (
    adjudicate,
    write_critic_verdict,
    write_findings,
)
from review.verdict_rules import derive_review_state

from helpers.review_fixtures import artifact_file as _artifact
from helpers.review_fixtures import canonical_findings_ledger


def _finding(id_, severity="low"):
    return {"id": id_, "severity": severity, "title": "t", "file": "f.go",
            "line": 10, "description": "d", "recommendation": "r",
            "category": "general", "confidence": 0.9}


def _write_findings(output_dir, findings, **extra):
    """Write a reconciliation ledger shaped the way the producer writes it.

    The adjustment writer reads only `findings`, but step 11 now renders
    `review-findings.md` from this same file, and the renderer is a pure
    function of the whole artifact. A minimal stub here would make every
    step-11 test report a render failure the pipeline would never see in a
    real run, where the ledger always comes from ReviewOutputBuilder.

    It goes out through `write_findings()` for the same reason: this helper
    stands in for the review-reconciliator's own write, which is the
    ledger's first IN-CHANNEL write. A raw `json.dumps` here would route
    around the one sanctioned write path the real producer uses.
    """
    checks = extra.get("checks", [])
    finding_numbers = [
        int(item["id"][1:])
        for item in findings
        if re.fullmatch(r"f[1-9][0-9]*", item.get("id", ""))
    ]
    check_numbers = [
        int(item["id"][1:])
        for item in checks
        if re.fullmatch(r"c[1-9][0-9]*", item.get("id", ""))
    ]
    derived = derive_review_state(findings)
    data = canonical_findings_ledger(checks=checks, reconciliation={
        "grouped_concern_count": len(findings),
        "verified_concern_count": len(findings),
        "input_finding_count": len(findings),
        "contributing_agent_count": 1 if findings else 0,
        "reviewing_agents": ["security-reviewer"],
        "dispatched_agents": ["security-reviewer"],
    })
    data["findings"] = findings
    # Lowercase: this is the per-review ledger vocabulary
    # (schemas/review-output.ts), not the outer-pipeline
    # APPROVE/COMMENT/REQUEST_CHANGES values pipeline-result.json
    # publishes. Step 11 maps between the two layers.
    data["verdict"] = derived["verdict"]
    data["summary"] = {
        "total_findings": len(findings),
        "by_severity": derived["counts"],
        **derived["advisory"],
    }
    data["meta"]["next_finding_number"] = max(finding_numbers, default=0) + 1
    data["meta"]["next_check_number"] = max(check_numbers, default=0) + 1
    if "meta" in extra:
        data["meta"].update(extra.pop("meta"))
    data.update(extra)
    write_findings(str(output_dir), data)
    return data


def _publish_revise(output_dir, adjustments):
    """Publish one REVISE proposal the way `critic.py --save` does.

    Returns the script-assigned adjustment ids in proposal order, which is
    the only handle the orchestrator's adjudication request has on them.
    """
    proposal = critic_adjustments_module.prepare_proposal({
        "schema": 2, "adjustments": adjustments,
    })
    write_critic_verdict(str(output_dir), "REVISE", proposal)
    return [entry["adjustment_id"] for entry in proposal["adjustments"]]


def _publish_verdict(output_dir, verdict):
    """Publish a non-REVISE verdict with its mandatory empty proposal."""
    write_critic_verdict(
        str(output_dir), verdict, critic_adjustments_module.empty_proposal()
    )


def _request(ids, *, verified=(), refuted=(), assessment=None, recommendations=None):
    """An adjudication request addressed by proposal index, for readability."""
    request = {
        "schema": 2,
        "verified": [ids[index] for index in verified],
        "refuted": [
            {"adjustment_id": ids[index], "rejection_reason": reason}
            for index, reason in refuted
        ],
        "revised_assessment": assessment,
    }
    if recommendations is not None:
        request["revised_recommendations"] = recommendations
    return request


def _adjudicate(
    output_dir, ids, *, verified=(), refuted=(), assessment=None, recommendations=None
):
    return adjudicate(str(output_dir), _request(
        ids, verified=verified, refuted=refuted, assessment=assessment,
        recommendations=recommendations,
    ))


def _publish_and_adjudicate(
    output_dir, adjustments, *, verified=(), refuted=(), assessment=None,
    recommendations=None,
):
    """Run one whole critic round: publish the proposal, then adjudicate it."""
    ids = _publish_revise(output_dir, adjustments)
    result = _adjudicate(
        output_dir, ids,
        verified=verified, refuted=refuted, assessment=assessment,
        recommendations=recommendations,
    )
    return ids, result


def _ledger(output_dir):
    return json.loads((Path(output_dir) / "review-findings.json").read_text())

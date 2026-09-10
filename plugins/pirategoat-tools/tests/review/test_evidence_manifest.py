"""Evidence facts survive projection; ledger prose and source paths do not."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from helpers.review_fixtures import canonical_findings_ledger, canonical_review_document
from review import critic_adjustments
from review.evidence_manifest import build_evidence_manifest
from review.reviewer_lifecycle import review_paths
from review.run_paths import artifact_path


from helpers.review_fixtures import write_artifact as _write  # noqa: E402


def _ledger():
    ledger = canonical_findings_ledger(["high", "medium"], checks=[{
        "id": "c1", "question": "Nonce verified?", "method": "secret src/a.php:10",
        "result": "yes", "source_reviewers": ["security-review"], "verifies": ["V1", "V9"],
    }])
    ledger["findings"][0]["sources"] = [
        {"reviewer": "security-review", "id": "f2", "severity": "high"},
        {"reviewer": "code-review", "id": "f1", "severity": "critical"},
    ]
    ledger["findings"][0]["critic_adjustment"] = {"action": "demote", "rationale": "secret"}
    ledger["findings"][1]["sources"] = [{"reviewer": "code-review", "id": "f4"}]
    ledger["dropped_findings"] = [
        {"reviewer": "performance-review", "id": "f1", "reason": "false_positive", "evidence": "secret"},
        {"reviewer": "performance-review", "id": "f2", "reason": "prefiltered", "evidence": "secret"},
    ]
    ledger["dropped_checks"] = [{"reviewer": "code-review", "id": "c3", "reason": "void", "evidence": "secret"}]
    ledger["orchestrator_notes"] = [{"id": "n1", "note": "secret", "outcome": "confirmed", "evidence": "secret"}]
    ledger["verdict_before_adjustments"] = "block"
    ledger["applied_critic_adjustments"] = [{"adjustment_id": "a" * 32, "outcome": "verified"}]
    return ledger


def _write_purpose(tmp_path, text):
    path = artifact_path(str(tmp_path), "change_purpose")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _write_review(tmp_path, reviewer, document):
    path = Path(review_paths(str(tmp_path), reviewer).final)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


def test_lineage_settlement_and_reasons_without_prose(tmp_path):
    ledger = _ledger()
    _write(tmp_path, "review_findings_json", ledger)
    _write_purpose(tmp_path, "## Verify\nV1. Nonce check holds — source: PR body\nV2. Cache stable — source: commit\n## Context\nNone.\n")

    evidence = build_evidence_manifest(str(tmp_path))

    assert evidence["findings"] == [
        {"id": "f1", "severity": "high", "sources": [
            {"agent": "security-reviewer", "id": "f2", "severity": "high"},
            {"agent": "code-reviewer", "id": "f1", "severity": "critical"},
        ], "critic_action": "demote"},
        # A source row without a severity (a ledger before the field) is
        # projected as None, never guessed from the final severity.
        {"id": "f2", "severity": "medium", "sources": [{"agent": "code-reviewer", "id": "f4", "severity": None}], "critic_action": None},
    ]
    assert evidence["dropped_findings"] == [
        {"agent": "performance-reviewer", "id": "f1", "reason": "false_positive"},
        {"agent": "performance-reviewer", "id": "f2", "reason": "prefiltered"},
    ]
    assert evidence["checks"] == {"count": 1, "dropped": {"void": 1}}
    assert evidence["verify_items"] == [
        {"id": "V1", "carried_over": False, "settled_by": 1},
        {"id": "V2", "carried_over": False, "settled_by": 0},
    ]
    assert evidence["undeclared_citations"] == 1
    assert evidence["orchestrator_notes"] == {"confirmed": 1, "refuted": 0, "not_checked": 0}
    serialized = json.dumps(evidence)
    assert "secret" not in serialized
    assert ledger["findings"][0]["title"] not in serialized


def test_a_critic_added_finding_has_a_known_empty_lineage(tmp_path):
    """The critic originated it, so it has no reviewer sources by
    construction: a measured empty collection, not an unknown one, or the
    whole run's lineage would read as unavailable."""
    ledger = _ledger()
    added = dict(ledger["findings"][1], id="f3")
    del added["sources"]
    added["critic_adjustment"] = {"action": "add", "rationale": "secret"}
    ledger["findings"].append(added)
    ledger["meta"]["next_finding_number"] = 4
    ledger["summary"] = canonical_findings_ledger(["high", "medium", "medium"])["summary"]
    _write(tmp_path, "review_findings_json", ledger)
    evidence = build_evidence_manifest(str(tmp_path))
    assert evidence["findings"][2] == {"id": "f3", "severity": "medium", "sources": [], "critic_action": "add"}


def test_critic_removed_findings_keep_their_sources(tmp_path):
    """A finding the critic removed left `findings` for
    `findings_removed_by_critic` with its sources; those sources were
    neither kept nor dropped by the reconciliator, and survival accounting
    must see them."""
    ledger = _ledger()
    removed = ledger["findings"].pop(1)
    removed["critic_adjustment"] = {"action": "remove", "rationale": "secret"}
    ledger["findings_removed_by_critic"] = [removed]
    ledger["summary"] = canonical_findings_ledger(["high"])["summary"]
    _write(tmp_path, "review_findings_json", ledger)
    evidence = build_evidence_manifest(str(tmp_path))
    assert [row["id"] for row in evidence["findings"]] == ["f1"]
    assert evidence["findings_removed_by_critic"] == [
        {"id": "f2", "severity": "medium", "sources": [{"agent": "code-reviewer", "id": "f4", "severity": None}], "critic_action": "remove"},
    ]
    assert "secret" not in json.dumps(evidence)


def test_no_critic_removal_is_a_measured_empty_collection(tmp_path):
    """The key is written only when a removal applied, so its absence
    means none, in every ledger version."""
    _write(tmp_path, "review_findings_json", _ledger())
    evidence = build_evidence_manifest(str(tmp_path))
    assert evidence["findings_removed_by_critic"] == []


def test_pre_lineage_ledger_has_no_sources_or_critic(tmp_path):
    _write(tmp_path, "review_findings_json", canonical_findings_ledger(["low"]))
    evidence = build_evidence_manifest(str(tmp_path))
    assert evidence["findings"][0]["sources"] is None
    assert evidence["dropped_findings"] is None
    assert evidence["checks"]["dropped"] is None
    assert evidence["verify_items"] is None
    assert evidence["undeclared_citations"] is None
    assert evidence["critic"] is None
    assert evidence["orchestrator_notes"] is None


def test_present_empty_lineage_drop_and_note_collections_remain_measured(tmp_path):
    ledger = canonical_findings_ledger()
    ledger["dropped_findings"] = []
    ledger["dropped_checks"] = []
    ledger["orchestrator_notes"] = []
    _write(tmp_path, "review_findings_json", ledger)

    evidence = build_evidence_manifest(str(tmp_path))

    assert evidence["findings"] == []
    assert evidence["dropped_findings"] == []
    assert evidence["checks"]["dropped"] == {"void": 0}
    assert evidence["orchestrator_notes"] == {
        "confirmed": 0,
        "refuted": 0,
        "not_checked": 0,
    }


def test_source_reviewer_text_is_validated_before_projection(tmp_path):
    ledger = _ledger()
    ledger["findings"][0]["sources"].append({"reviewer": "private/path-review", "id": "f9"})
    ledger["dropped_findings"].append({"reviewer": "private prose", "id": "f8", "reason": "prefiltered"})
    _write(tmp_path, "review_findings_json", ledger)
    evidence = build_evidence_manifest(str(tmp_path))
    assert len(evidence["findings"][0]["sources"]) == 3
    assert evidence["findings"][0]["sources"][2] == {"agent": None, "id": "f9", "severity": None}
    assert len(evidence["dropped_findings"]) == 3
    assert evidence["dropped_findings"][2]["agent"] is None
    assert "private" not in json.dumps(evidence)


def test_a_confirmed_note_counts_toward_settlement(tmp_path):
    ledger = canonical_findings_ledger(checks=[
        {"id": "c1", "question": "q", "method": "m", "result": "r",
         "source_reviewers": ["code-review"], "verifies": ["V1"]},
    ])
    ledger["orchestrator_notes"] = [
        {"id": "n1", "note": "secret", "outcome": "confirmed", "evidence": "secret", "verifies": ["V1", "V9"]},
        {"id": "n2", "note": "secret", "outcome": "refuted", "evidence": "secret"},
    ]
    _write(tmp_path, "review_findings_json", ledger)
    _write_purpose(tmp_path, "## Verify\nV1. one — source: PR body\nV2. two — source: PR body\n## Context\nNone.\n")
    evidence = build_evidence_manifest(str(tmp_path))
    assert evidence["verify_items"] == [
        {"id": "V1", "carried_over": False, "settled_by": 2},
        {"id": "V2", "carried_over": False, "settled_by": 0},
    ]
    assert evidence["undeclared_citations"] == 1
    assert "secret" not in json.dumps(evidence)


def test_carried_over_verify_item_can_be_settled_by_multiple_checks(tmp_path):
    ledger = canonical_findings_ledger(checks=[
        {"id": f"c{index}", "question": "q", "method": "m", "result": "r",
         "source_reviewers": ["code-review"], "verifies": ["V1"]}
        for index in (1, 2)
    ])
    _write(tmp_path, "review_findings_json", ledger)
    _write_purpose(tmp_path, "## Verify\nV1. private purpose — source: baseline (carried over)\n## Context\nNone.\n")
    assert build_evidence_manifest(str(tmp_path))["verify_items"] == [{
        "id": "V1", "carried_over": True, "settled_by": 2,
    }]


def _commit_proposal(tmp_path, ledger):
    proposal = critic_adjustments.prepare_proposal({"schema": critic_adjustments.ADJUSTMENTS_SCHEMA, "adjustments": [
        {"action": "demote", "target": {"kind": "finding", "id": "f1"}, "fields": {"severity": "medium"}, "rationale": "secret"},
        {"action": "remove", "target": {"kind": "finding", "id": "f2"}, "rationale": "secret"},
        {"action": "add", "target": {"kind": "finding"}, "fields": {
            "severity": "low", "title": "secret", "file": "src/a.php", "description": "secret", "recommendation": "secret",
        }, "rationale": "secret"},
    ]})
    ids = [entry["adjustment_id"] for entry in proposal["adjustments"]]
    ledger["applied_critic_adjustments"] = [
        {"adjustment_id": ids[0], "outcome": "verified"},
        {"adjustment_id": ids[1], "outcome": "not_checked"},
    ]
    ledger["rejected_critic_adjustments"] = [{
        "adjustment_id": ids[2], "outcome": "refuted", "action": "add",
        "target": {"kind": "finding"}, "rejection_reason": "secret",
    }]
    _write(tmp_path, "review_findings_json", ledger)
    _write(tmp_path, "critic_adjustments", proposal)
    critic_adjustments.write_critic_verdict(str(tmp_path), "REVISE", proposal)


def test_critic_counts_actions_and_outcomes(tmp_path):
    _commit_proposal(tmp_path, _ledger())
    assert build_evidence_manifest(str(tmp_path))["critic"] == {
        "verdict": "REVISE", "verdict_before_adjustments": "block", "adjustments": {
            "demote": {"proposed": 1, "verified": 1, "not_checked": 0, "refuted": 0},
            "remove": {"proposed": 1, "verified": 0, "not_checked": 1, "refuted": 0},
            "add": {"proposed": 1, "verified": 0, "not_checked": 0, "refuted": 1},
        },
    }


@pytest.mark.parametrize("payload", [{"schema": 2, "adjustments": []}, None], ids=["digest-mismatch", "missing"])
def test_bad_proposal_preserves_readable_verdict(tmp_path, payload):
    _commit_proposal(tmp_path, _ledger())
    if payload is None:
        artifact_path(str(tmp_path), "critic_adjustments").unlink()
    else:
        _write(tmp_path, "critic_adjustments", payload)
    critic = build_evidence_manifest(str(tmp_path))["critic"]
    assert critic == {"verdict": "REVISE", "verdict_before_adjustments": "block", "adjustments": None}


def test_validated_empty_proposal_has_measured_empty_adjustments(tmp_path):
    _write(tmp_path, "review_findings_json", canonical_findings_ledger())
    critic_adjustments.write_critic_verdict(str(tmp_path), "STAND", critic_adjustments.empty_proposal())
    assert build_evidence_manifest(str(tmp_path))["critic"] == {
        "verdict": "STAND", "verdict_before_adjustments": None, "adjustments": {},
    }


def test_host_citations_drop_identity_paths_and_unsafe_names(tmp_path):
    _write(tmp_path, "review_findings_json", canonical_findings_ledger(["low"]))
    document = canonical_review_document("ecosystem-integration", ["low"] * 4)
    for finding, cited in zip(document["findings"], [
        "wordpress@7.2-alpha:src/post.php:12", "woocommerce@unknown:includes/order.php:9",
        "secret/private@branch:src/foo:1", "unqualified/source.php:1",
    ]):
        finding["source_cited"] = cited
    _write_review(tmp_path, "ecosystem-integration", document)
    _write(tmp_path, "dispatch_plan", {"agents": [{"name": "ecosystem-integration-reviewer", "status": "DISPATCH"}]})
    evidence = build_evidence_manifest(str(tmp_path))
    assert evidence["host_citations"] == {"ecosystem-integration-reviewer": {"wordpress": 1, "woocommerce": 1, "unknown": 1}}
    assert "post.php" not in json.dumps(evidence)
    assert "secret" not in json.dumps(evidence)


def test_host_citations_count_check_methods_and_results(tmp_path):
    """Run 4dfe: fifteen `wordpress@…` citations, all inside checks,
    counted as zero because only a finding's `source_cited` was read. The
    citation grammar is the protocol's; a check cites in its method or
    result, and each distinct citation counts once per check."""
    _write(tmp_path, "review_findings_json", canonical_findings_ledger(["low"]))
    document = canonical_review_document("code", [])
    document["meta"]["next_check_number"] = 3
    document["checks"] = [
        {"id": "c1", "question": "Q?", "method": "Read wordpress@7.2-alpha-63166-src:src/wp-includes/post.php:4321 and wordpress@7.2-alpha-63166-src:src/wp-includes/post.php:4321 again",
         "result": "Confirmed at woocommerce@unknown:includes/class-wc-coupon.php:10.", "source_reviewers": ["code-review"]},
        {"id": "c2", "question": "Q?", "method": "grep -n foo src/a.php", "result": "no citation here", "source_reviewers": ["code-review"]},
    ]
    _write_review(tmp_path, "code", document)
    _write(tmp_path, "dispatch_plan", {"agents": [{"name": "code-reviewer", "status": "DISPATCH"}]})
    evidence = build_evidence_manifest(str(tmp_path))
    assert evidence["host_citations"] == {"code-reviewer": {"wordpress": 1, "woocommerce": 1}}
    assert "post.php" not in json.dumps(evidence)


def test_unreadable_ledger_is_unmeasured(tmp_path):
    """A missing ledger, broken JSON, a non-object list, and an invalid
    ledger shape all reach `read_findings_file`'s non-OK statuses — a
    contract pinned in `test_critic_adjustments.py` — and here collapse to
    the same `read.status != FINDINGS_READ_OK -> None`; `missing`
    represents the family."""
    assert build_evidence_manifest(str(tmp_path)) is None


def test_an_unstructured_purpose_reads_verify_settlement_as_unmeasured(tmp_path):
    ledger = canonical_findings_ledger(["low"])
    _write(tmp_path, "review_findings_json", ledger)
    (tmp_path / "pipeline").mkdir(exist_ok=True)
    artifact_path(str(tmp_path), "change_purpose").write_text("## What the change does\nAdds a thing.\n")
    evidence = build_evidence_manifest(str(tmp_path))
    assert evidence["verify_items"] is None
    assert evidence["undeclared_citations"] is None


def test_an_unreadable_dispatch_plan_reads_host_citations_as_unmeasured(tmp_path):
    _write(tmp_path, "review_findings_json", canonical_findings_ledger(["low"]))
    (tmp_path / "pipeline").mkdir(exist_ok=True)
    artifact_path(str(tmp_path), "dispatch_plan").write_text("not json")
    assert build_evidence_manifest(str(tmp_path))["host_citations"] is None

"""Tests for critic_adjustments — the sole writer that carries decision-critic
finding-level decisions into review-findings.json."""

import builtins
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "review" / "critic_adjustments.py"
sys.path.insert(0, str(SCRIPTS_DIR))

from review.atomic_io import atomic_write_json
from review.critic_adjustments import (
    APPLIED_IDS_KEY,
    REJECTED_ADJUSTMENTS_KEY,
    INVALIDATED_ASSESSMENTS_KEY,
    REFUSAL_EXIT_CODE,
    REFUSAL_NO_VERDICT,
    REFUSAL_VERDICT_NOT_REVISE,
    apply_adjustments,
    pending_count,
    read_critic_verdict,
    validate_proposal_input,
    write_findings,
)
from review import critic_adjustments as critic_adjustments_module
from review import orchestration as orchestration_mod
from review.orchestration import _orchestrate_step_11


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
    sev = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for i in findings:
        sev[i["severity"]] += 1
    data = {
        "pr_id": "42",
        "reviewer": "reconciliator",
        "timestamp": "2026-08-13T10:00:00",
        "plugin_version": None,
        "schema": 2,
        # Lowercase: this is the per-review ledger vocabulary
        # (schemas/review-output.ts), not the outer-pipeline
        # APPROVE/COMMENT/REQUEST_CHANGES values pipeline-result.json
        # publishes. Step 11 maps between the two layers.
        "verdict": "request_changes",
        "summary": {
            "total_findings": len(findings),
            "by_severity": sev,
            "suppressed_advisory_finding_count": 0,
        },
        "findings": findings,
        "review_claimable_files": [],
        "reviewed_file_claims": [],
        "unclaimed_review_files": [],
        "inline_diff_file_count": 1,
        "review_accounted_file_count": 1,
        "in_scope_review_file_count": 1,
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
    data.update(extra)
    write_findings(str(output_dir), data)
    return data


def _write_snapshot_document(output_dir, document, verdict="REVISE"):
    """One fixture for the live digest-bound critic snapshot.

    Invalid-document tests deliberately pass malformed lifecycle content;
    this factory still binds the marker to those bytes so the production
    validator, rather than an obsolete marker shape, is what rejects them.
    """
    output_dir = Path(output_dir)
    (output_dir / "decision-critic-adjustments.json").write_text(
        json.dumps(document)
    )
    atomic_write_json(
        str(output_dir / "decision-critic-verdict.json"),
        {
            "schema": 1,
            "verdict": verdict,
            "proposal_digest": critic_adjustments_module.proposal_digest(
                document
            ),
        },
    )
    return document


def _write_adjustments(output_dir, adjustments, **document_extra):
    entries = json.loads(json.dumps(adjustments))
    used_ids = {
        entry.get("adjustment_id")
        for entry in entries
        if isinstance(entry, dict) and entry.get("adjustment_id")
    }
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("action") == "remove" and "fields" not in entry:
            entry["fields"] = {}
        if not entry.get("adjustment_id"):
            candidate = uuid.uuid4().hex
            while candidate in used_ids:
                candidate = uuid.uuid4().hex
            entry["adjustment_id"] = candidate
            used_ids.add(candidate)

    lifecycle = any(
        isinstance(entry, dict)
        and ({"spot_check", "rejected", "rejection_reason", "applied"} & set(entry))
        for entry in entries
    ) or "revised_assessment" in document_extra
    document = {"schema": 1, "adjustments": entries}
    if lifecycle:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("rejected") is True:
                entry.setdefault("spot_check", "refuted")
            else:
                entry.setdefault("spot_check", "not_checked")
        assessment = document_extra.pop(
            "revised_assessment", "Fixture post-critic assessment."
        )
        document["adjudication"] = {
            "schema": 1,
            "source": "orchestrator",
            "proposal_digest": critic_adjustments_module.proposal_digest(
                document
            ),
            "recorded_at": "2026-08-24T10:00:00+00:00",
            "revised_assessment": assessment,
        }
    document.update(document_extra)
    return _write_snapshot_document(output_dir, document)


def _write_critic_verdict(output_dir, verdict):
    output_dir = Path(output_dir)
    adjustments_path = output_dir / "decision-critic-adjustments.json"
    if verdict == "REVISE" and adjustments_path.is_file():
        document = json.loads(adjustments_path.read_text())
    else:
        document = {"schema": 1, "adjustments": []}
    return _write_snapshot_document(output_dir, document, verdict=verdict)


def _commit_critic_snapshot(output_dir, adjustments, verdict="REVISE"):
    """Publish the live marker/proposal pair through the production helpers."""
    proposal = critic_adjustments_module.prepare_proposal({
        "schema": 1,
        "adjustments": adjustments if verdict == "REVISE" else [],
    })
    critic_adjustments_module.write_adjustments(str(output_dir), proposal)
    atomic_write_json(
        str(Path(output_dir) / "decision-critic-verdict.json"),
        {
            "schema": 1,
            "verdict": verdict,
            "proposal_digest": critic_adjustments_module.proposal_digest(
                proposal
            ),
        },
    )
    return proposal


def _settlement_request(
    proposal, *, verified=(), refuted=(), assessment="Settled assessment."
):
    ids = [entry["adjustment_id"] for entry in proposal["adjustments"]]
    verified_ids = [ids[index] for index in verified]
    refuted_entries = [
        {
            "adjustment_id": ids[index],
            "rejection_reason": reason,
        }
        for index, reason in refuted
    ]
    return {
        "schema": 1,
        "verified": verified_ids,
        "refuted": refuted_entries,
        "revised_assessment": assessment,
    }


def _applied_ids(findings):
    """The ids out of `applied_critic_adjustments`, whose entries are
    records (`{"adjustment_id": ..., "spot_check": ...}`) rather than bare
    strings — the id half is the idempotence bookkeeping, the spot_check
    half is the orchestrator's outcome for that decision."""
    return [record["adjustment_id"] for record in findings[APPLIED_IDS_KEY]]


def _finding(id_, severity="low"):
    return {"id": id_, "severity": severity, "title": "t", "file": "f.go",
            "line": 10, "description": "d", "recommendation": "r",
            "category": "general", "confidence": 0.9}


def _publish_step_11(output_dir, state=None):
    """Prepare without a report, then publish the authored report."""
    state = {} if state is None else state
    report = Path(output_dir) / "review-report.md"
    report_text = report.read_text() if report.is_file() else "# report"
    report.unlink(missing_ok=True)
    _orchestrate_step_11("pr", {}, state, {}, str(output_dir))
    report.write_text(report_text)
    return _orchestrate_step_11("pr", {}, state, {}, str(output_dir))


@pytest.fixture
def revise_verdict(tmp_path):
    """Write a REVISE verdict so apply_adjustments' gate lets the call through.

    Most of this file's tests exercise validation, writing, and batch
    logic that is orthogonal to the REVISE gate itself — that gate has
    its own dedicated coverage in TestCriticVerdictGate. This fixture
    supplies the one passing precondition once so the rest of the suite
    keeps testing what it was written to test, instead of every test
    hand-rolling the same `decision-critic-verdict.json` setup.
    """
    _write_critic_verdict(tmp_path, "REVISE")


class TestApplyAdjustments:
    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def test_no_adjustments_file_is_a_noop(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        result = apply_adjustments(str(tmp_path))
        assert result["status"] == "no_adjustments"
        assert result["applied"] == 0

    def test_promote_patches_severity_with_provenance(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "medium"},
            "rationale": "affects future strategy authors",
        }])
        result = apply_adjustments(str(tmp_path))
        assert result["applied"] == 1
        data = json.loads((tmp_path / "review-findings.json").read_text())
        finding = data["findings"][0]
        assert finding["severity"] == "medium"
        assert finding["critic_adjustment"]["action"] == "promote"
        assert finding["critic_adjustment"]["prior"] == {"severity": "low"}
        assert data["summary"]["by_severity"]["medium"] == 1
        assert data["summary"]["by_severity"]["low"] == 0

    def test_add_appends_full_finding_with_generated_id(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        _write_adjustments(tmp_path, [{
            "action": "add", "id": None,
            "fields": {"severity": "low", "title": "stale README",
                       "file": "internal/strategy/README.md",
                       "description": "teaches the deleted warm path",
                       "recommendation": "update the warm/cold section"},
            "rationale": "promoted from docs-drift observations",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["summary"]["total_findings"] == 2
        added = data["findings"][1]
        assert len(added["id"]) == 8
        assert added["critic_adjustment"]["action"] == "add"

    def test_remove_moves_finding_out_with_provenance(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1"), _finding("f2")])
        _write_adjustments(tmp_path, [{
            "action": "remove", "id": "f2",
            "fields": {}, "rationale": "false positive — refuted by source",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert [i["id"] for i in data["findings"]] == ["f1"]
        assert data["findings_removed_by_critic"][0]["id"] == "f2"
        assert data["summary"]["total_findings"] == 1

    def test_unknown_id_fails_loudly_and_writes_nothing(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        _write_adjustments(tmp_path, [
            {"action": "promote", "id": "f1",
             "fields": {"severity": "high"}, "rationale": "r"},
            {"action": "promote", "id": "zzzz9999",
             "fields": {"severity": "high"}, "rationale": "r"},
        ])
        with pytest.raises(ValueError, match="zzzz9999"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"  # entry 1 NOT applied either

    def test_invalid_action_and_field_rejected(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        _write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "f1",
            "fields": {}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="obliterate"):
            apply_adjustments(str(tmp_path))
        _write_adjustments(tmp_path, [{
            "action": "correct", "id": "f1",
            "fields": {"verdict": "APPROVE"}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="verdict"):
            apply_adjustments(str(tmp_path))

    def test_rejected_entries_are_skipped(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"},
            "rationale": "r", "rejected": True,
            "rejection_reason": "spot-check refuted the claim",
        }])
        result = apply_adjustments(str(tmp_path))
        assert result["applied"] == 0
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"

    def test_second_run_is_idempotent(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))
        result = apply_adjustments(str(tmp_path))
        assert result["applied"] == 0
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["summary"]["by_severity"]["medium"] == 1

    def test_mixed_batch_recounts_totals_and_severities(self, tmp_path):
        """add + remove + promote in one batch must leave the summary exact.

        The summary is what bot mode, baselines, and metrics read; a batch
        that touches the population from three directions is where a naive
        incremental counter drifts from the finding list it claims to describe.
        """
        _write_findings(tmp_path, [
            _finding("f1", "low"),
            _finding("f2", "high"),
            _finding("f3", "medium"),
        ])
        _write_adjustments(tmp_path, [
            {"action": "promote", "id": "f1",
             "fields": {"severity": "high"}, "rationale": "wider blast radius"},
            {"action": "remove", "id": "f3",
             "fields": {}, "rationale": "refuted by source"},
            {"action": "add", "id": None,
             "fields": {"severity": "critical", "title": "unbounded retry",
                        "file": "internal/queue/retry.go",
                        "description": "no ceiling on attempts",
                        "recommendation": "cap attempts"},
             "rationale": "critic found it independently"},
        ])
        result = apply_adjustments(str(tmp_path))
        assert result["status"] == "applied"
        assert result["applied"] == 3

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["summary"]["total_findings"] == 3
        assert len(data["findings"]) == 3
        assert data["summary"]["by_severity"] == {
            "critical": 1, "high": 2, "medium": 0, "low": 0, "info": 0,
        }
        assert data["summary"]["total_findings"] == len(data["findings"])
        assert [i["id"] for i in data["findings"]][:2] == ["f1", "f2"]
        assert data["findings_removed_by_critic"][0]["id"] == "f3"
        # The removed finding is out of the counted population entirely.
        assert "f3" not in {i["id"] for i in data["findings"]}

    def test_add_action_round_trip(self, tmp_path):
        """The `add` action's full solo round trip.

        `promote` has end-to-end coverage in
        TestCriticContextRoundTrip (context render -> critic adjustment
        -> apply -> ledger). `add` never had an equivalent belt-and-braces
        check beyond the mixed-batch assertions above — this pins the
        generated id shape, provenance, and summary recount for an `add`
        landing on its own.
        """
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "add", "id": None,
            "fields": {"severity": "high", "title": "unbounded retry",
                       "file": "internal/queue/retry.go",
                       "description": "no ceiling on attempts",
                       "recommendation": "cap attempts"},
            "rationale": "critic found it independently",
        }])
        result = apply_adjustments(str(tmp_path))
        assert result["status"] == "applied"
        assert result["applied"] == 1

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert len(data["findings"]) == 2
        added = data["findings"][1]
        assert re.fullmatch(r"[0-9a-f]{8}", added["id"]), (
            f"generated id must be 8 lowercase hex chars, got {added['id']!r}"
        )
        assert added["id"] != "f1"
        assert added["title"] == "unbounded retry"
        assert added["critic_adjustment"] == {
            "action": "add", "rationale": "critic found it independently",
        }
        assert data["summary"]["total_findings"] == 2
        assert data["summary"]["by_severity"] == {
            "critical": 0, "high": 1, "medium": 0, "low": 1, "info": 0,
        }

    def test_add_action_reapply_idempotent(self, tmp_path):
        """A second apply over the same adjustments file must not append
        a second copy of the added finding — the crash-safety contract
        TestCrashSafety pins for `promote`, exercised here for `add`."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "add", "id": None,
            "fields": {"severity": "high", "title": "unbounded retry",
                       "file": "internal/queue/retry.go",
                       "description": "no ceiling on attempts",
                       "recommendation": "cap attempts"},
            "rationale": "critic found it independently",
        }])
        first = apply_adjustments(str(tmp_path))
        assert first["status"] == "applied"
        assert first["applied"] == 1
        after_first = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        assert len(after_first["findings"]) == 2

        second = apply_adjustments(str(tmp_path))
        assert second["status"] == "nothing_pending"
        assert second["applied"] == 0
        after_second = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        assert len(after_second["findings"]) == 2, (
            "a re-apply must not append a duplicate finding"
        )
        assert after_second == after_first


class TestRejectionAudit:
    """A rejected critic decision must leave a trace in the artifact
    downstream readers actually consult, not only in
    decision-critic-adjustments.json, which none of them read."""

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def test_rejected_entry_lands_in_the_findings_audit_trail(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"},
            "rationale": "r", "rejected": True,
            "rejection_reason": "spot-check refuted the claim",
        }])
        result = apply_adjustments(str(tmp_path))
        assert result["applied"] == 0  # a rejected entry is never applied
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"

        records = data[REJECTED_ADJUSTMENTS_KEY]
        assert len(records) == 1
        record = records[0]
        assert record["action"] == "promote"
        assert record["target_id"] == "f1"
        assert record["spot_check"] == "refuted"
        assert record["rejection_reason"] == "spot-check refuted the claim"
        assert record["adjustment_id"]  # allocated so a re-run can dedupe

    def test_second_run_does_not_duplicate_the_rejection_record(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "info"},
            "rationale": "r", "rejected": True,
            "rejection_reason": "spot-check refuted it",
        }])
        apply_adjustments(str(tmp_path))
        first = json.loads((tmp_path / "review-findings.json").read_text())
        assert len(first[REJECTED_ADJUSTMENTS_KEY]) == 1

        second_result = apply_adjustments(str(tmp_path))
        assert second_result["status"] == "nothing_pending"
        assert second_result["applied"] == 0
        second = json.loads((tmp_path / "review-findings.json").read_text())
        assert second == first, "an idempotent re-run must not rewrite the ledger"
        assert len(second[REJECTED_ADJUSTMENTS_KEY]) == 1

    def test_a_second_rejected_entry_in_a_later_batch_appends(
        self, tmp_path
    ):
        _write_findings(
            tmp_path, [_finding("f1", "low"), _finding("f2", "low")]
        )
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "high"}, "rationale": "r",
            "rejected": True, "rejection_reason": "first round refutation",
        }])
        apply_adjustments(str(tmp_path))
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f2",
            "fields": {"severity": "info"}, "rationale": "r",
            "rejected": True, "rejection_reason": "second round refutation",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        records = data[REJECTED_ADJUSTMENTS_KEY]
        assert len(records) == 2
        assert {r["target_id"] for r in records} == {"f1", "f2"}
        assert {r["rejection_reason"] for r in records} == {
            "first round refutation", "second round refutation",
        }

    def test_a_purely_rejected_batch_is_reported_as_nothing_pending(
        self, tmp_path
    ):
        """The rejection audit write is real, but it is not an 'apply':
        `result['status']` describes whether findings were mutated, and a
        rejection never mutates `findings`."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "high"}, "rationale": "r",
            "rejected": True, "rejection_reason": "refuted",
        }])
        result = apply_adjustments(str(tmp_path))
        assert result["status"] == "nothing_pending"
        assert result["applied"] == 0

    def test_mixed_batch_applies_one_and_audits_the_other(self, tmp_path):
        _write_findings(
            tmp_path, [_finding("f1", "low"), _finding("f2", "low")]
        )
        _write_adjustments(tmp_path, [
            {"action": "promote", "id": "f1",
             "fields": {"severity": "high"}, "rationale": "r"},
            {"action": "demote", "id": "f2",
             "fields": {"severity": "info"}, "rationale": "r",
             "rejected": True, "rejection_reason": "refuted"},
        ])
        result = apply_adjustments(str(tmp_path))
        assert result["status"] == "applied"
        assert result["applied"] == 1
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "high"
        assert data["findings"][1]["severity"] == "low"  # rejected, untouched
        records = data[REJECTED_ADJUSTMENTS_KEY]
        assert len(records) == 1
        assert records[0]["target_id"] == "f2"

    @pytest.mark.parametrize("bad_reason", [None, "", "   "])
    def test_missing_or_blank_rejection_reason_refuses_the_whole_batch(
        self, tmp_path, bad_reason
    ):
        """rejection_reason is the entire payload of the audit record —
        a rejected entry without one is refused loudly, the same
        all-or-nothing style an unknown action or invalid severity gets,
        instead of silently writing an empty string into the ledger."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        entry = {
            "action": "promote", "id": "f1",
            "fields": {"severity": "high"}, "rationale": "r",
            "rejected": True,
        }
        if bad_reason is not None:
            entry["rejection_reason"] = bad_reason
        _write_adjustments(tmp_path, [entry])
        with pytest.raises(ValueError, match="non-empty 'rejection_reason'"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert REJECTED_ADJUSTMENTS_KEY not in data  # nothing written

    def test_applied_entry_cannot_be_rewritten_as_rejected_after_commit(
        self, tmp_path
    ):
        """The marker binds immutable facts and lifecycle validation rejects
        contradictory post-hoc settlement flags before touching the ledger."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "high"}, "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))
        after_apply = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        assert after_apply["findings"][0]["severity"] == "high"
        assert REJECTED_ADJUSTMENTS_KEY not in after_apply

        adj_path = tmp_path / "decision-critic-adjustments.json"
        doc = json.loads(adj_path.read_text())
        doc["adjustments"][0]["rejected"] = True
        doc["adjustments"][0]["rejection_reason"] = "hand-edited after apply"
        adj_path.write_text(json.dumps(doc))

        with pytest.raises(ValueError):
            apply_adjustments(str(tmp_path))
        after_second = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        assert after_second == after_apply, (
            "findings must be byte-equal — no rejection record, applied "
            "record intact"
        )
        assert REJECTED_ADJUSTMENTS_KEY not in after_second
        assert after_second["applied_critic_adjustments"] == (
            after_apply["applied_critic_adjustments"]
        )


class TestCrashSafety:
    """Application is recorded on both sides, so no crash point can either
    lose the batch or apply it twice."""

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def test_crash_between_writes_converges_without_double_applying(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))

        findings_path = tmp_path / "review-findings.json"
        adj_path = tmp_path / "decision-critic-adjustments.json"
        after_first = findings_path.read_bytes()
        doc = json.loads(adj_path.read_text())
        entry = doc["adjustments"][0]
        assert entry["adjustment_id"]  # allocated before the findings write
        assert _applied_ids(json.loads(after_first)) == [
            entry["adjustment_id"]
        ]

        # Simulate the crash: the findings write landed, the flag write did not.
        del entry["applied"]
        adj_path.write_text(json.dumps(doc))

        result = apply_adjustments(str(tmp_path))
        assert result["applied"] == 0
        assert findings_path.read_bytes() == after_first
        # The pre-critic state is still what `prior` reports — a second
        # application would have overwritten it with the critic's own output.
        data = json.loads(findings_path.read_text())
        assert data["findings"][0]["critic_adjustment"]["prior"] == {
            "severity": "low"
        }
        assert json.loads(adj_path.read_text())["adjustments"][0]["applied"] \
            is True

    def test_no_temp_files_survive_success_or_rejection(self, tmp_path):
        expected = [
            "decision-critic-adjustments.json",
            "decision-critic-verdict.json",
            "review-findings.json",
        ]
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))
        assert sorted(p.name for p in tmp_path.iterdir()) == expected

        _write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "f1",
            "fields": {}, "rationale": "r",
        }])
        with pytest.raises(ValueError):
            apply_adjustments(str(tmp_path))
        assert sorted(p.name for p in tmp_path.iterdir()) == expected

    def test_duplicate_adjustment_ids_are_rejected(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1"), _finding("f2")])
        _write_adjustments(tmp_path, [
            {"adjustment_id": "dup", "action": "promote", "id": "f1",
             "fields": {"severity": "high"}, "rationale": "r"},
            {"adjustment_id": "dup", "action": "promote", "id": "f2",
             "fields": {"severity": "high"}, "rationale": "r"},
        ])
        with pytest.raises(ValueError, match="duplicate adjustment_id"):
            apply_adjustments(str(tmp_path))


class TestBatchCoherence:
    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def test_duplicate_target_in_one_batch_is_rejected(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [
            {"action": "promote", "id": "f1",
             "fields": {"severity": "high"}, "rationale": "r"},
            {"action": "correct", "id": "f1",
             "fields": {"title": "clearer title"}, "rationale": "r"},
        ])
        with pytest.raises(ValueError, match="duplicate target"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"
        assert "critic_adjustment" not in data["findings"][0]

    def test_targeting_an_id_removed_earlier_in_the_batch_is_rejected(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1"), _finding("f2")])
        _write_adjustments(tmp_path, [
            {"action": "remove", "id": "f2",
             "fields": {}, "rationale": "false positive"},
            {"action": "promote", "id": "f2",
             "fields": {"severity": "high"}, "rationale": "r"},
        ])
        with pytest.raises(ValueError, match="removed by adjustment\\[0\\]"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert [i["id"] for i in data["findings"]] == ["f1", "f2"]

    def test_entry_without_an_id_fails_as_unknown_id(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "fields": {"severity": "high"},
            "rationale": "r",
        }])
        with pytest.raises(ValueError, match="non-empty target id"):
            apply_adjustments(str(tmp_path))

    def test_findings_finding_without_an_id_is_not_addressable(self, tmp_path):
        idless = _finding("f1")
        del idless["id"]
        _write_findings(tmp_path, [idless])
        _write_adjustments(tmp_path, [{
            "action": "promote", "fields": {"severity": "high"},
            "rationale": "r",
        }])
        # A None target must not silently match an id-less finding.
        with pytest.raises(ValueError, match="non-empty target id"):
            apply_adjustments(str(tmp_path))

    def test_add_rejects_a_critic_supplied_id_in_both_spellings(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1")])
        base_fields = {"severity": "low", "title": "t", "file": "f.go",
                       "description": "d", "recommendation": "r"}
        _write_adjustments(tmp_path, [{
            "action": "add", "id": "f3",
            "fields": dict(base_fields), "rationale": "r",
        }])
        with pytest.raises(ValueError, match="ids are generated"):
            apply_adjustments(str(tmp_path))

        _write_adjustments(tmp_path, [{
            "action": "add", "id": None,
            "fields": {**base_fields, "id": "f3"}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="'id' is not adjustable"):
            apply_adjustments(str(tmp_path))

    def test_malformed_ledger_severity_fails_instead_of_undercounting(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1", "low")])
        raw = json.loads((tmp_path / "review-findings.json").read_text())
        raw["findings"].append({**_finding("f2"), "severity": "blocker"})
        (tmp_path / "review-findings.json").write_text(json.dumps(raw))
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="f2"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"  # nothing written

    @pytest.mark.parametrize("shape", [[{"id": "f1"}], "findings", 7])
    def test_findings_that_is_not_an_object_fails_as_a_value_error(
        self, tmp_path, shape
    ):
        """The adjustments file is shape-guarded; the findings file was
        not, so a non-object ledger died on an AttributeError outside this
        module's ValueError contract — the one step 11 catches."""
        (tmp_path / "review-findings.json").write_text(json.dumps(shape))
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="must be a JSON object"):
            apply_adjustments(str(tmp_path))
        assert json.loads(
            (tmp_path / "review-findings.json").read_text()
        ) == shape
        doc = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        assert "applied" not in doc["adjustments"][0]  # nothing written


class TestValidateProposalInput:
    """Direct unit coverage for the critic-owned proposal validator."""

    def test_valid_batch_returns_no_problems(self):
        assert validate_proposal_input({
            "schema": 1,
            "adjustments": [{
                "action": "promote", "id": "f1",
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        }) == []

    def test_non_object_payload_is_a_problem(self):
        assert validate_proposal_input([1, 2, 3]) == [
            "decision-critic-adjustments.json must be a JSON object"
        ]

    def test_wrong_schema_is_a_problem(self):
        problems = validate_proposal_input({"schema": 2, "adjustments": []})
        assert len(problems) == 1
        assert "'schema' must be 1" in problems[0]

    def test_adjustments_not_a_list_is_a_problem(self):
        assert validate_proposal_input({"schema": 1, "adjustments": "nope"}) == [
            "decision-critic-adjustments.json: 'adjustments' must be a list"
        ]

    def test_missing_adjustments_key_is_a_problem(self):
        assert validate_proposal_input({"schema": 1}) == [
            "decision-critic-adjustments.json: 'adjustments' must be a list"
        ]

    def test_entry_not_an_object_is_a_problem(self):
        assert validate_proposal_input({
            "schema": 1, "adjustments": ["not-a-dict"],
        }) == ["adjustment[0] must be an object"]

    def test_adjustment_id_is_not_a_proposal_field(self):
        problems = validate_proposal_input({
            "schema": 1,
            "adjustments": [{
                "adjustment_id": "caller-owned", "action": "promote",
                "id": "f1",
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        })
        assert any("adjustment_id" in problem for problem in problems)

    def test_unknown_action_is_a_problem(self):
        problems = validate_proposal_input({
            "schema": 1,
            "adjustments": [{
                "action": "obliterate", "id": "f1",
                "fields": {}, "rationale": "r",
            }],
        })
        assert any("unknown action" in p and "obliterate" in p for p in problems)

    def test_invalid_field_is_a_problem(self):
        problems = validate_proposal_input({
            "schema": 1,
            "adjustments": [{
                "action": "correct", "id": "f1",
                "fields": {"verdict": "APPROVE"}, "rationale": "r",
            }],
        })
        assert any("not adjustable" in p for p in problems)

    def test_add_missing_required_fields_is_a_problem(self):
        problems = validate_proposal_input({
            "schema": 1,
            "adjustments": [{
                "action": "add", "id": None,
                "fields": {"severity": "low"}, "rationale": "r",
            }],
        })
        assert any("add requires fields" in p for p in problems)

    def test_add_with_a_critic_supplied_id_is_a_problem(self):
        problems = validate_proposal_input({
            "schema": 1,
            "adjustments": [{
                "action": "add", "id": "f3",
                "fields": {"severity": "low", "title": "t", "file": "f.go",
                           "description": "d", "recommendation": "r"},
                "rationale": "r",
            }],
        })
        assert any("ids are generated" in p for p in problems)

    @pytest.mark.parametrize(
        "action,fields,problem",
        [
            ("promote", {}, "promote requires exactly the severity field"),
            (
                "promote",
                {"severity": "high", "title": "also change the title"},
                "promote requires exactly the severity field",
            ),
            ("demote", {"title": "not a severity"},
             "demote requires exactly the severity field"),
            ("rescope", {}, "rescope requires exactly the line field"),
            (
                "rescope",
                {"line": 20, "file": "other.go"},
                "rescope requires exactly the line field",
            ),
            ("correct", {}, "correct requires at least one field"),
            (
                "remove", {"title": "replacement"},
                "remove does not accept replacement fields",
            ),
        ],
    )
    def test_action_specific_field_contract_is_enforced(
        self, action, fields, problem
    ):
        problems = validate_proposal_input({
            "schema": 1,
            "adjustments": [{
                "action": action,
                "id": "f1",
                "fields": fields,
                "rationale": "r",
            }],
        })

        assert any(problem in candidate for candidate in problems)

    def test_a_proposal_may_target_each_finding_only_once(self):
        problems = validate_proposal_input({
            "schema": 1,
            "adjustments": [
                {
                    "action": "promote",
                    "id": "f1",
                    "fields": {"severity": "high"},
                    "rationale": "r",
                },
                {
                    "action": "correct",
                    "id": "f1",
                    "fields": {"title": "Clearer title"},
                    "rationale": "r",
                },
            ],
        })

        assert any("duplicate target 'f1'" in problem for problem in problems)

    def test_two_independent_problems_are_both_reported(self):
        """The proposal validator collects every independent problem
        collects every problem instead of stopping at the first one it
        finds. apply_adjustments() only ever surfaces `problems[0]`, so
        this can only be pinned by calling the validator directly."""
        problems = validate_proposal_input({
            "schema": 1,
            "adjustments": [
                {"action": "obliterate", "id": "f1",
                 "fields": {}, "rationale": "r"},
                {"action": "add", "id": "f3",
                 "fields": {"severity": "low", "title": "t", "file": "f.go",
                            "description": "d", "recommendation": "r"},
                 "rationale": "r"},
            ],
        })
        assert len(problems) == 2
        assert any("unknown action" in p and "obliterate" in p for p in problems)
        assert any("ids are generated" in p for p in problems)


class TestAdjustmentsSchemaValidation:
    """decision-reviewer.md's taught template always writes `"schema": 1`
    alongside `"adjustments"`; a doc out of that template is refused
    whole, the same all-or-nothing way an unknown action is."""

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def _write_raw_adjustments(self, output_dir, doc):
        _write_snapshot_document(output_dir, doc)

    def test_schema_1_proceeds(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "high"}, "rationale": "r",
        }])
        result = apply_adjustments(str(tmp_path))
        assert result["status"] == "applied"
        assert result["applied"] == 1

    def test_schema_2_refuses_the_whole_batch(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        self._write_raw_adjustments(tmp_path, {
            "schema": 2,
            "adjustments": [{
                "action": "promote", "id": "f1",
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        })
        with pytest.raises(ValueError, match="'schema' must be 1"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"  # nothing written

    def test_missing_schema_refuses_with_the_same_message_shape(
        self, tmp_path
    ):
        """The taught template always includes `schema`; a doc missing it
        entirely is out-of-template the same way a wrong value is, and
        gets the same refusal rather than being read as version 1 by
        default."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        self._write_raw_adjustments(tmp_path, {
            "adjustments": [{
                "action": "promote", "id": "f1",
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        })
        with pytest.raises(ValueError, match="'schema' must be 1"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"  # nothing written

    def test_schema_as_a_string_refuses(self, tmp_path):
        """`"1"` is not `1` — no type coercion for the schema gate."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        self._write_raw_adjustments(tmp_path, {
            "schema": "1",
            "adjustments": [{
                "action": "promote", "id": "f1",
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        })
        with pytest.raises(ValueError, match="'schema' must be 1"):
            apply_adjustments(str(tmp_path))

    @pytest.mark.parametrize("shape", [[{"id": "f1"}], "hello", 5])
    def test_non_object_doc_fails_as_a_shape_error_not_a_schema_error(
        self, tmp_path, shape
    ):
        """[], "hello", and 5 are all valid JSON but not a document with a
        'schema' field to be wrong about — the diagnosis must name the
        actual defect (not a JSON object), the same distinction
        read_findings_file() draws for the findings ledger twenty lines
        away, rather than misreporting it as a missing/invalid schema."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        (tmp_path / "decision-critic-adjustments.json").write_text(
            json.dumps(shape)
        )
        with pytest.raises(
            ValueError, match="decision-critic-adjustments.json must be a JSON object"
        ):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"  # nothing written


class TestScopeLinePairing:
    """schemas/review-output.ts:36-37 and output.py's renderer treat
    scope/line as a pair; a patch must never split them."""

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def test_add_without_a_line_is_marked_file_scoped(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        _write_adjustments(tmp_path, [{
            "action": "add", "id": None,
            "fields": {"severity": "low", "title": "stale README",
                       "file": "README.md", "description": "d",
                       "recommendation": "r"},
            "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))
        added = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )["findings"][1]
        assert added["line"] is None
        assert added["scope"] == "file"

    def test_add_with_a_line_carries_no_scope_marker(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        _write_adjustments(tmp_path, [{
            "action": "add", "id": None,
            "fields": {"severity": "low", "title": "t", "file": "f.go",
                       "description": "d", "recommendation": "r", "line": 42},
            "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))
        added = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )["findings"][1]
        assert added["line"] == 42
        assert "scope" not in added

    def test_rescope_to_a_line_drops_the_stale_file_marker(self, tmp_path):
        file_scoped = {**_finding("f1"), "line": None, "scope": "file"}
        _write_findings(tmp_path, [file_scoped])
        _write_adjustments(tmp_path, [{
            "action": "rescope", "id": "f1",
            "fields": {"line": 88}, "rationale": "pinned to the call site",
        }])
        apply_adjustments(str(tmp_path))
        finding = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )["findings"][0]
        assert finding["line"] == 88
        assert "scope" not in finding

    def test_rescope_to_no_line_marks_the_finding_file_scoped(self, tmp_path):
        line_anchored = {**_finding("f1"), "line": 12}
        _write_findings(tmp_path, [line_anchored])
        _write_adjustments(tmp_path, [{
            "action": "rescope", "id": "f1",
            "fields": {"line": None}, "rationale": "the whole file drifted",
        }])
        apply_adjustments(str(tmp_path))
        finding = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )["findings"][0]
        assert finding["line"] is None
        assert finding["scope"] == "file"

    def test_a_patch_that_leaves_line_alone_leaves_scope_alone(self, tmp_path):
        file_scoped = {**_finding("f1"), "line": None, "scope": "file"}
        _write_findings(tmp_path, [file_scoped])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "high"}, "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))
        finding = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )["findings"][0]
        assert finding["scope"] == "file"
        assert finding["line"] is None

    @pytest.mark.parametrize("bad_line", ["88", True, 0, -5])
    def test_a_line_outside_the_1_indexed_contract_is_rejected(
        self, tmp_path, bad_line
    ):
        """output.py accepts only positive ints for `line`; a patch that
        smuggled 0 or a negative past this guard would publish a finding
        the builder itself would have refused."""
        _write_findings(tmp_path, [_finding("f1")])
        _write_adjustments(tmp_path, [{
            "action": "rescope", "id": "f1",
            "fields": {"line": bad_line}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="line must be a positive"):
            apply_adjustments(str(tmp_path))


class TestCLI:
    """Step 10's REVISE briefing shells out to this as a script (step 11
    imports and calls apply_adjustments() directly instead), so the
    process contract (exit status + stdout/stderr channels) is part of
    the interface."""

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def _run(self, output_dir):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "apply",
                "--output-dir",
                str(output_dir),
            ],
            capture_output=True, text=True, timeout=60,
        )

    def test_cli_applies_and_reports_result_json_on_stdout(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        proc = self._run(tmp_path)
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        assert result["status"] == "applied"
        assert result["applied"] == 1
        assert result["adjudication_source"] == "defensive_apply"
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "medium"

    def test_cli_reports_a_same_batch_remove_then_target_cleanly(
        self, tmp_path
    ):
        """This one used to die on a raw KeyError past validation, which
        the CLI's except tuple does not cover — no ERROR: line at all."""
        _write_findings(tmp_path, [_finding("f1"), _finding("f2")])
        _write_adjustments(tmp_path, [
            {"action": "remove", "id": "f2",
             "fields": {}, "rationale": "false positive"},
            {"action": "correct", "id": "f2",
             "fields": {"title": "t2"}, "rationale": "r"},
        ])
        proc = self._run(tmp_path)
        assert proc.returncode == 1
        assert proc.stderr.startswith("ERROR:")
        assert "Traceback" not in proc.stderr
        assert "removed by adjustment[0]" in proc.stderr
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert len(data["findings"]) == 2

    def test_cli_fails_loudly_on_invalid_action(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "f1",
            "fields": {}, "rationale": "r",
        }])
        proc = self._run(tmp_path)
        assert proc.returncode == 1
        assert "ERROR:" in proc.stderr
        assert "obliterate" in proc.stderr
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"


class TestCriticVerdictGate:
    """The gate lives inside apply_adjustments itself (see module
    docstring), so every caller — CLI, step 11, and any future one —
    shares it. These tests exercise the function directly and via the
    CLI subprocess, deliberately WITHOUT the `revise_verdict` fixture
    the rest of this file relies on, since the gate itself is what's
    under test here."""

    def test_apply_refuses_without_verdict_file(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        (tmp_path / "decision-critic-verdict.json").unlink()
        before = (tmp_path / "review-findings.json").read_bytes()

        result = apply_adjustments(str(tmp_path))

        assert result == {
            "status": "refused", "applied": 0, "reason": REFUSAL_NO_VERDICT,
        }
        assert (tmp_path / "review-findings.json").read_bytes() == before, (
            "a refusal must write nothing"
        )
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "decision-critic-adjustments.json", "review-findings.json",
        ], "the adjustments file must be untouched too — no id allocation"

    def test_apply_refuses_on_stand(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "STAND")
        before = (tmp_path / "review-findings.json").read_bytes()

        result = apply_adjustments(str(tmp_path))

        assert result == {
            "status": "refused", "applied": 0,
            "reason": f"{REFUSAL_VERDICT_NOT_REVISE} (STAND)",
        }
        assert (tmp_path / "review-findings.json").read_bytes() == before

    @pytest.mark.parametrize("near_miss", ["revise", " REVISE ", "REVISE\n"])
    def test_apply_refuses_on_a_non_exact_revise_spelling(
        self, tmp_path, near_miss
    ):
        """The gate is exact-match, not case-insensitive or whitespace-
        tolerant. The taught contract (briefings.py, critic.py's
        CRITIC_VERDICTS) renders the verdict as uppercase "REVISE" with no
        surrounding whitespace — a critic that emits anything else is
        deviating from the contract, and that deviation must refuse loudly
        rather than being silently normalized into an apply.
        """
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, near_miss)
        before = (tmp_path / "review-findings.json").read_bytes()

        result = apply_adjustments(str(tmp_path))

        assert result["status"] == "refused"
        assert result["reason"] == REFUSAL_NO_VERDICT, (
            "a malformed marker must not become a usable verdict"
        )
        assert (tmp_path / "review-findings.json").read_bytes() == before, (
            "a refusal must write nothing, even for a near-miss spelling"
        )

    def test_apply_refuses_on_unparseable_verdict(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        (tmp_path / "decision-critic-verdict.json").write_text("{not json")
        before = (tmp_path / "review-findings.json").read_bytes()

        result = apply_adjustments(str(tmp_path))

        assert result == {
            "status": "refused", "applied": 0, "reason": REFUSAL_NO_VERDICT,
        }
        assert (tmp_path / "review-findings.json").read_bytes() == before

    def test_apply_proceeds_on_revise(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "REVISE")

        result = apply_adjustments(str(tmp_path))

        assert result["status"] == "applied"
        assert result["applied"] == 1
        assert result["adjudication_source"] == "defensive_apply"
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "critical"

    def test_cli_exit_code_on_refusal(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "STAND")

        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "apply",
                "--output-dir",
                str(tmp_path),
            ],
            capture_output=True, text=True, timeout=60,
        )

        assert proc.returncode == REFUSAL_EXIT_CODE
        assert proc.returncode not in (0, 1), (
            "the refusal exit code must be distinct from success (0) and "
            "the validation/IO error code (1)"
        )
        assert "REFUSED" in proc.stderr
        assert REFUSAL_VERDICT_NOT_REVISE in proc.stderr
        # The result JSON reaches stdout on refusal too — one parser for
        # every status, not a special case that only prints on stderr.
        assert json.loads(proc.stdout) == {
            "status": "refused", "applied": 0,
            "reason": f"{REFUSAL_VERDICT_NOT_REVISE} (STAND)",
        }
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"


class TestReadCriticVerdict:
    """Unit coverage for the reader apply_adjustments' gate is built on —
    it returns an allowed verdict only from a complete source-bound snapshot
    and otherwise collapses the unusable snapshot to ``None``."""

    def test_missing_file_returns_none(self, tmp_path):
        assert read_critic_verdict(str(tmp_path)) is None

    def test_malformed_json_returns_none(self, tmp_path):
        (tmp_path / "decision-critic-verdict.json").write_text("{not json")
        assert read_critic_verdict(str(tmp_path)) is None

    def test_non_object_json_returns_none(self, tmp_path):
        (tmp_path / "decision-critic-verdict.json").write_text('["REVISE"]')
        assert read_critic_verdict(str(tmp_path)) is None

    def test_non_string_verdict_field_returns_none(self, tmp_path):
        (tmp_path / "decision-critic-verdict.json").write_text(
            json.dumps({"verdict": 1})
        )
        assert read_critic_verdict(str(tmp_path)) is None

    def test_explicit_null_adjudication_makes_the_snapshot_unusable(
        self, tmp_path
    ):
        proposal = critic_adjustments_module.prepare_proposal({
            "schema": 1,
            "adjustments": [{
                "action": "demote",
                "id": "f1",
                "fields": {"severity": "low"},
                "rationale": "Guarded upstream.",
            }],
        })
        proposal["adjudication"] = None
        _write_snapshot_document(tmp_path, proposal)

        assert read_critic_verdict(str(tmp_path)) is None

    def test_apply_rejects_explicit_null_adjudication_without_mutation(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1", "high")])
        proposal = critic_adjustments_module.prepare_proposal({
            "schema": 1,
            "adjustments": [{
                "action": "demote",
                "id": "f1",
                "fields": {"severity": "low"},
                "rationale": "Guarded upstream.",
            }],
        })
        proposal["adjudication"] = None
        _write_snapshot_document(tmp_path, proposal)
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match="'adjudication' must be an object"):
            apply_adjustments(str(tmp_path))

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    def test_missing_verdict_key_returns_none(self, tmp_path):
        (tmp_path / "decision-critic-verdict.json").write_text(
            json.dumps({"reason": "no verdict field at all"})
        )
        assert read_critic_verdict(str(tmp_path)) is None

    @pytest.mark.parametrize("verdict", ["REVISE", "STAND", "ESCALATE", "SKIPPED"])
    def test_valid_verdict_string_is_returned_as_is(self, tmp_path, verdict):
        _write_critic_verdict(tmp_path, verdict)
        assert read_critic_verdict(str(tmp_path)) == verdict


class TestDerivedVerdict:
    """Step 11 DERIVES the published verdict from the findings ledger.

    The chain this replaced ran LLM -> review-verdict.json -> finalize, with
    a Rule 23 sync writing the transcription back over the ledger's own
    verdict: a run whose orchestrator wrote COMMENT above a ledger holding a
    critical finding published COMMENT, and the sync then made the ledger
    agree with the transcription rather than the other way round. Nothing
    reads review-verdict.json any more, and nothing writes it.
    """

    def _seed(self, tmp_path, ledger_verdict, findings=()):
        (tmp_path / "review-report.md").write_text("# report")
        _write_findings(tmp_path, list(findings), verdict=ledger_verdict)

    def _finalize(self, tmp_path, state=None):
        state = {} if state is None else state
        _publish_step_11(tmp_path, state)
        return json.loads((tmp_path / "pipeline-result.json").read_text())

    @pytest.mark.parametrize("ledger,published", [
        ("block", "REQUEST_CHANGES"),
        ("request_changes", "REQUEST_CHANGES"),
        ("comment", "COMMENT"),
        ("approve", "APPROVE"),
        ("not_applicable", "COMMENT"),
    ])
    def test_every_ledger_verdict_maps(self, tmp_path, ledger, published):
        """All FIVE, `block` included: it is what any critical finding (or
        three highs) produces, and omitting it would publish COMMENT for a
        critical-finding review."""
        self._seed(tmp_path, ledger)
        result = self._finalize(tmp_path)
        assert result["verdict"] == published
        assert result["verdict_source"] == "findings ledger"
        assert result["status"] == "success"

    @pytest.mark.parametrize("ledger", ["BLOCK", "  Approve  ", "Comment"])
    def test_casing_and_padding_do_not_break_the_mapping(self, tmp_path, ledger):
        self._seed(tmp_path, ledger)
        assert self._finalize(tmp_path)["verdict_source"] == "findings ledger"

    def test_a_critical_finding_never_publishes_comment(self, tmp_path):
        """The failure this derivation exists to kill, end to end: the
        ledger's own verdict is computed from its findings, so a critical
        one cannot be published as advisory by a transcription slip."""
        self._seed(tmp_path, "block", [_finding("f1", "critical")])
        assert self._finalize(tmp_path)["verdict"] == "REQUEST_CHANGES"

    def test_escalate_overrides_the_ledger(self, tmp_path):
        """The critic's one unilateral power: conclusions that did not
        survive the stress test cannot gate a merge."""
        self._seed(tmp_path, "block", [_finding("f1", "critical")])
        _write_critic_verdict(tmp_path, "ESCALATE")
        result = self._finalize(tmp_path)
        assert result["verdict"] == "COMMENT"
        assert result["verdict_source"] == "critic ESCALATE override"

    def test_stand_does_not_override(self, tmp_path):
        self._seed(tmp_path, "block")
        _write_critic_verdict(tmp_path, "STAND")
        assert self._finalize(tmp_path)["verdict_source"] == "findings ledger"

    @pytest.mark.parametrize("payload,label", [
        (None, "no ledger at all"),
        ("[1, 2]", "non-object ledger"),
        ("{not json", "unparseable ledger"),
        ('{"verdict": null}', "null verdict"),
        ('{"verdict": "who knows"}', "verdict outside the vocabulary"),
        ('{"findings": []}', "no verdict key"),
    ])
    def test_an_unusable_ledger_falls_back_and_says_so(
        self, tmp_path, payload, label
    ):
        (tmp_path / "review-report.md").write_text("# report")
        if payload is not None:
            (tmp_path / "review-findings.json").write_text(payload)
        result = self._finalize(tmp_path)
        assert result["verdict"] == "COMMENT", label
        assert result["verdict_source"] == "fallback: no usable ledger verdict"
        assert result["status"] == "degraded"
        assert any(
            "no usable verdict in review-findings.json" in note
            for note in result["degradation_notes"]
        ), label

    def test_a_non_object_ledger_does_not_crash_finalize(self, tmp_path):
        """The shape that used to raise AttributeError past the guard and
        kill finalize before pipeline-result.json was ever written."""
        (tmp_path / "review-report.md").write_text("# report")
        (tmp_path / "review-findings.json").write_text("[1, 2]")
        assert self._finalize(tmp_path)["status"] == "degraded"

    def test_a_stale_review_verdict_file_is_ignored_entirely(self, tmp_path):
        """Nothing reads the artifact any more. A leftover one from an
        older run — or a hand-written one — must not reach the published
        verdict, which is the whole point of deleting the chain."""
        self._seed(tmp_path, "approve")
        (tmp_path / "review-verdict.json").write_text(
            json.dumps({"verdict": "REQUEST_CHANGES"})
        )
        assert self._finalize(tmp_path)["verdict"] == "APPROVE"

    def test_the_ledger_is_not_rewritten_by_finalize(self, tmp_path):
        """Rule 23's write is gone: finalize READS the ledger's verdict and
        never writes one back, so the ledger keeps saying what its own
        findings say."""
        self._seed(tmp_path, "approve")
        before = (tmp_path / "review-findings.json").read_bytes()
        self._finalize(tmp_path)
        assert (tmp_path / "review-findings.json").read_bytes() == before

    def test_verdict_source_reaches_state_for_the_step_11_briefing(
        self, tmp_path
    ):
        self._seed(tmp_path, "approve")
        state = {}
        _publish_step_11(tmp_path, state)
        assert state["verdict_source"] == "findings ledger"
        assert state["pipeline_status"] == "success"
        assert state["degradation_notes"] == []


class TestCriticAbsenceHonesty:
    """A critic that was DISPATCHED and produced no usable verdict is a run
    that lost its stress test; a critic that was never dispatched is quick
    mode working as designed.

    `critic_verdict_for_state()` collapses a missing file and an explicit
    SKIPPED into "unavailable" — right for pirategoat-bot, blind for the
    run's own status — so the dispatch marker is what separates the two
    cases, and the USABLE VERDICT (not the file's existence) is what
    decides the degradation. Keying on the artifact instead inverted the
    incentive: the orchestrator that stopped short degraded, while the one
    that dutifully recorded a SKIPPED stand-in for a crashed critic
    published success over the same lost stress test.
    """

    _NOTE = "critic was dispatched but produced no verdict"

    def _seed(self, tmp_path, *, dispatched=False):
        (tmp_path / "review-report.md").write_text("# report")
        _write_findings(tmp_path, [], verdict="approve")
        if dispatched:
            from review import synthesis_lifecycle
            synthesis_lifecycle.mark_dispatched(
                str(tmp_path), synthesis_lifecycle.DECISION_CRITIC
            )

    def _finalize(self, tmp_path):
        _publish_step_11(tmp_path)
        return json.loads((tmp_path / "pipeline-result.json").read_text())

    def test_a_dispatched_critic_that_wrote_nothing_degrades(self, tmp_path):
        self._seed(tmp_path, dispatched=True)
        result = self._finalize(tmp_path)
        assert result["status"] == "degraded"
        assert self._NOTE in result["degradation_notes"]
        # Still falls through to the ledger — a missing critique does not
        # cost the review the verdict its findings earned.
        assert result["verdict"] == "APPROVE"
        assert result["critic_verdict"] == "unavailable"

    def test_a_dispatched_critic_recorded_as_skipped_also_degrades(
        self, tmp_path
    ):
        """The other row of the same table. A SKIPPED stand-in written
        after a dispatch describes exactly the lost stress test above — it
        is the crashed critic, spelled out — so it must not buy the run a
        clean bill of health the run that wrote nothing was denied."""
        self._seed(tmp_path, dispatched=True)
        _write_critic_verdict(tmp_path, "SKIPPED")
        result = self._finalize(tmp_path)
        assert result["status"] == "degraded"
        assert self._NOTE in result["degradation_notes"]

    def test_the_quick_skip_is_silent(self, tmp_path):
        """Quick mode's SKIPPED record is written by the PIPELINE, on the
        branch that deliberately writes no dispatch marker. Nothing was
        dispatched, so nothing was lost."""
        self._seed(tmp_path)
        _write_critic_verdict(tmp_path, "SKIPPED")
        result = self._finalize(tmp_path)
        assert result["status"] == "success"
        assert result["degradation_notes"] == []

    def test_an_undispatched_critic_is_silent(self, tmp_path):
        self._seed(tmp_path)
        result = self._finalize(tmp_path)
        assert result["status"] == "success"
        assert result["degradation_notes"] == []

    @pytest.mark.parametrize("verdict", ["STAND", "REVISE", "ESCALATE"])
    def test_a_dispatched_critic_that_answered_is_silent(
        self, tmp_path, verdict
    ):
        self._seed(tmp_path, dispatched=True)
        _write_critic_verdict(tmp_path, verdict)
        assert self._finalize(tmp_path)["degradation_notes"] == []

    def test_an_unparseable_verdict_after_dispatch_degrades(self, tmp_path):
        """Artifact-INDEPENDENT: a file exists, but no usable verdict came
        out of it, which is the same lost stress test."""
        self._seed(tmp_path, dispatched=True)
        (tmp_path / "decision-critic-verdict.json").write_text("{not json")
        assert self._NOTE in self._finalize(tmp_path)["degradation_notes"]



class TestCriticInputRoundTrip:
    """The full REVISE loop across the two artifacts that must agree.

    Three modules meet here and none of their own tests span the seam: the
    record renders the critic's view of the findings, the critic keys its
    adjustments off an id, and critic_adjustments.py resolves those keys
    against review-findings.json. While the critic's view showed only
    positional F-labels, each module passed its own tests and the loop was
    still broken end to end — every REVISE run shipped degraded with "no
    finding with id 'F1'".

    The fix is structural now: the critic is handed the ledger itself, so
    the only key its view offers IS the ledger key. This crosses the seam
    by taking its id the way the critic must — out of the artifacts the
    dispatch prompt names, and asserting the record offers no rival handle.
    """

    def test_an_id_read_from_the_handed_ledger_applies(self, tmp_path):
        _write_findings(tmp_path, [_finding("f5", "low")])
        # The critic reads this file directly; it is the `--context` path.
        findings = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        visible_ids = [finding["id"] for finding in findings["findings"]]
        assert visible_ids

        proposal = _write_adjustments(tmp_path, [{
            "action": "promote", "id": visible_ids[0],
            "fields": {"severity": "high"},
            "rationale": "the exploit path is reachable from the REST route",
        }])
        critic_adjustments_module.settle(
            str(tmp_path),
            _settlement_request(proposal, verified=(0,)),
        )
        (tmp_path / "review-report.md").write_text("# report")

        _publish_step_11(tmp_path)

        data = json.loads((tmp_path / "review-findings.json").read_text())
        finding = data["findings"][0]
        assert finding["severity"] == "high", (
            "the id the critic could see did not resolve in the ledger"
        )
        assert finding["critic_adjustment"]["action"] == "promote"
        assert data["summary"]["by_severity"]["high"] == 1
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []
        assert result["status"] == "success"

    def test_the_record_offers_no_positional_label_to_mistake_for_a_key(
        self, tmp_path
    ):
        """The record titles findings; it never numbers them. And it says
        where the real key lives, so a reader cannot invent one."""
        _write_findings(tmp_path, [_finding("f5"), _finding("f6")])
        _write_critic_verdict(tmp_path, "STAND")

        _orchestrate_step_11("pr", {}, {}, {}, str(tmp_path))

        record = (tmp_path / "review-record.md").read_text()
        assert not re.search(r"^### F\d+\b", record, re.MULTILINE), record
        assert "canonical fN `id` in `review-findings.json` (`findings[].id`)" in record
        assert "a positional label is not a key" in record


class TestStepElevenAppliesAdjustments:
    """Step 11 is where pending critic adjustments land, so any run whose
    orchestrator stopped short of the step-10 briefing's instructions
    still converges on a findings JSON the critic reached — but only
    under REVISE, the verdict whose briefing spot-checked the entries
    first."""

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        """Keep finalize's worktree hygiene off the developer's own repo.

        Step 11 inspects the repo it is standing in, and pytest stands in
        the real checkout. Scoped to this class because only these tests
        call the step directly; the CLI tests elsewhere in this file run
        in a subprocess with their own cwd.
        """
        monkeypatch.chdir(tmp_path)

    def _step_11(self, output_dir):
        """Call the finalize step the way the pipeline facade routes it."""
        return _publish_step_11(output_dir)

    def test_pending_adjustments_applied_before_the_verdict_is_derived(
        self, tmp_path
    ):
        """Ordering is now load-bearing in one direction only: the apply
        recomputes the ledger verdict, and finalize READS that verdict a few
        lines later. Reading first would publish the pre-batch verdict."""
        _write_findings(tmp_path, [_finding("f1", "low")],
                        verdict="approve")
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "critical"
        assert data["verdict"] == "block"
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["verdict"] == "REQUEST_CHANGES"
        assert result["verdict_source"] == "findings ledger"
        assert result["status"] == "degraded"
        assert result["degradation_notes"] == [
            "critic adjustments were applied without orchestrator adjudication"
        ]

    def test_defensive_apply_degradation_is_stable_on_refinalize(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote",
            "id": "f1",
            "fields": {"severity": "high"},
            "rationale": "r",
        }])
        (tmp_path / "review-report.md").write_text("# report")
        state = {}

        _publish_step_11(tmp_path, state)
        _publish_step_11(tmp_path, state)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == [
            "critic adjustments were applied without orchestrator adjudication"
        ]
        assert state["step_11_degradation_records"] == [{
            "code": "critic_adjudication_missing",
            "message": (
                "critic adjustments were applied without orchestrator "
                "adjudication"
            ),
        }]

    def test_invalid_adjustments_degrade_instead_of_crashing(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "f1",
            "fields": {}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any("critic adjustment inspection failed" in n
                   for n in result["degradation_notes"])
        # The note must reach `status` too — appended after the status is
        # computed, it would publish a "success" run carrying a degradation.
        assert result["status"] == "degraded"
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"  # nothing half-applied

    def test_surfaces_an_unexpected_refusal_from_apply_adjustments(
        self, tmp_path, monkeypatch
    ):
        """This branch guards a one-edit-away divergence, not a reachable
        state under today's code: state["critic_verdict"] and
        apply_adjustments()'s own gate both derive from
        critic_verdict_for_state()/read_critic_verdict() reading the same
        file, so they cannot disagree today. But a future edit to either
        side's presentation mapping — SKIPPED handling, a new alias verdict
        — could make them diverge silently. Monkeypatching
        apply_adjustments() to return a refusal despite an on-disk REVISE
        verdict simulates exactly that divergence without waiting for it
        to actually happen, and pins that it degrades loudly instead of
        silently doing nothing.
        """
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")

        def fake_apply_adjustments(output_dir):
            return {
                "status": "refused", "applied": 0,
                "reason": "verdict_not_revise (STAND)",
            }

        monkeypatch.setattr(
            critic_adjustments_module, "apply_adjustments",
            fake_apply_adjustments,
        )
        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any(
            "refused" in note and "verdict_not_revise (STAND)" in note
            for note in result["degradation_notes"]
        ), result["degradation_notes"]
        assert result["status"] == "degraded"

    def test_non_revise_verdict_never_applies_pending_adjustments(
        self, tmp_path
    ):
        """Adjustments are a REVISE-only channel.

        One representative non-REVISE verdict: the gate's own refusal for
        every value in the vocabulary is pinned at the unit level by
        `TestCriticVerdictGate`, so what step 11 adds here — and what this
        test is for — is the orchestration half: the "REVISE-only channel"
        degradation note and the degraded status.

        Only the REVISE briefing has the orchestrator spot-check each
        entry and mark the refuted ones rejected. A critic that writes
        adjustments alongside any other verdict would otherwise get them
        applied here with no review at all — the apply would stop being a
        defensive re-run and become the sole, unreviewed application.
        """
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        document = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        _write_snapshot_document(tmp_path, document, verdict="STAND")
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low", "nothing may be applied"
        assert "critic_adjustment" not in data["findings"][0]
        assert APPLIED_IDS_KEY not in data

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any("critic adjustment inspection failed" in n
                   for n in result["degradation_notes"]), (
            "a non-REVISE marker cannot commit a non-empty proposal"
        )
        assert result["status"] == "degraded"

    def test_missing_critic_verdict_never_applies_pending_adjustments(
        self, tmp_path
    ):
        """No verdict file is not an implicit REVISE."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        (tmp_path / "decision-critic-verdict.json").unlink()
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["findings"][0]["severity"] == "low"
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any("critic adjustment inspection failed" in n
                   for n in result["degradation_notes"])

    def test_settled_adjustments_are_not_suspicious_under_stand(
        self, tmp_path
    ):
        """The gate reports pending entries, not the presence of a file.

        Entries the orchestrator already applied or rejected want nothing
        more from the ledger, so a run carrying only those is the ordinary
        settled state — noting it would make every re-entered step 11 look
        degraded.
        """
        _write_findings(tmp_path, [_finding("f1", "low")])
        _write_adjustments(tmp_path, [
            {"action": "promote", "id": "f1",
             "fields": {"severity": "medium"}, "rationale": "r",
             "applied": True},
            {"action": "remove", "id": "f1", "rationale": "r",
             "rejected": True, "rejection_reason": "spot-check refuted it"},
        ])
        _write_critic_verdict(tmp_path, "STAND")
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []
        assert result["status"] == "success"

    def test_ids_recorded_in_findings_also_count_as_settled(self, tmp_path):
        """The other half of the shared predicate: a crash between the two
        writes leaves an unflagged entry whose id the findings file already
        records. It is not pending, so it must not be reported either."""
        _write_findings(tmp_path, [_finding("f1", "medium")])
        findings_path = tmp_path / "review-findings.json"
        data = json.loads(findings_path.read_text())
        data[APPLIED_IDS_KEY] = ["f4"]
        # Through the sanctioned writer: the state being simulated is a
        # crash between apply_adjustments' two writes, where the FINDINGS
        # write (in channel) landed and only the flag write was lost. A
        # raw rewrite here would simulate a hand edit instead.
        write_findings(str(tmp_path), data)
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"}, "rationale": "r",
            "adjustment_id": "f4",
        }])
        _write_critic_verdict(tmp_path, "STAND")

        assert pending_count(str(tmp_path)) == 0

        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []

    def test_malformed_findings_file_degrades_instead_of_crashing(
        self, tmp_path
    ):
        """The measured regression: a list-shaped findings file used to
        survive step 11 only because Rule 23's write was gated on a
        verdict file being present — so the apply call must not become the
        thing that crashes finalize."""
        (tmp_path / "review-findings.json").write_text(
            json.dumps([_finding("f1", "low")])
        )
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any("critic adjustment apply attempt failed" in n
                   for n in result["degradation_notes"])
        assert result["status"] == "degraded"


class TestStepElevenRerendersFindingsMarkdown:
    """`review-findings.md` must describe the FINAL ledger, not the one the
    reconciliator first published.

    Field-proven defect: after every critic REVISE, the hand-written
    assessment still showed pre-adjustment severities while the JSON and the
    report showed post-adjustment ones — a guaranteed-stale fallback
    artifact, and the one the step-10 critic fallback and the failure-path
    report fallback both point at.
    """

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def _step_11(self, output_dir):
        return _publish_step_11(output_dir)

    def _seed(self, tmp_path, severity="high"):
        finding = _finding("f1", severity)
        finding["title"] = "Unescaped output"
        _write_findings(tmp_path, [finding])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")

    def test_demoted_severity_reaches_the_markdown(self, tmp_path):
        """THE pin: a REVISE demote must be visible in the rendered file."""
        self._seed(tmp_path, severity="high")
        (tmp_path / "review-findings.md").write_text(
            "## High Issues\n\n### Unescaped output\n"
        )
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])

        self._step_11(tmp_path)

        rendered = (tmp_path / "review-findings.md").read_text()
        assert "## Low Findings" in rendered
        assert "## High Issues" not in rendered
        assert "Unescaped output" in rendered

    def test_the_rendered_verdict_is_the_ledgers_own(self, tmp_path):
        """The Markdown renders the ledger, and the ledger's verdict is what
        finalize publishes — one number, one source. Nothing writes a
        verdict into this file at finalize any more."""
        self._seed(tmp_path, severity="low")
        _write_critic_verdict(tmp_path, "STAND")

        self._step_11(tmp_path)

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "request_changes"
        assert "**Verdict:** REQUEST_CHANGES" in (
            tmp_path / "review-findings.md"
        ).read_text()

    def test_render_failure_is_recorded_not_raised(self, tmp_path, monkeypatch):
        self._seed(tmp_path, severity="low")
        import review.orchestration as orchestration_module

        monkeypatch.setattr(
            orchestration_module, "_materialize_markdown",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        self._step_11(tmp_path)  # must not raise

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any(
            "review-findings.md render failed" in n
            for n in result["degradation_notes"]
        )
        assert result["status"] == "degraded"

    def test_missing_findings_json_renders_nothing_and_adds_no_note(
        self, tmp_path
    ):
        _write_critic_verdict(tmp_path, "STAND")
        (tmp_path / "review-report.md").write_text("# report")

        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert not (tmp_path / "review-findings.md").exists()
        assert not any(
            "render failed" in n for n in result["degradation_notes"]
        )

    def test_the_record_prepares_state_without_becoming_the_report_path(
        self, tmp_path
    ):
        """`review-report.md` is authored from the step-11 briefing this
        function is about to render. Until that handoff lands, neither the
        record nor findings Markdown may masquerade as the report path."""
        self._seed(tmp_path, severity="low")
        (tmp_path / "review-report.md").unlink()
        _write_critic_verdict(tmp_path, "STAND")
        state = {}

        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))

        assert not (tmp_path / "pipeline-result.json").exists()
        assert state["publication_pending"] is True
        assert not any(
            "review-report.md not found" in note
            for note in state["degradation_notes"]
        )

        (tmp_path / "review-report.md").write_text("# report")
        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["report_path"] == str(tmp_path / "review-report.md")
        assert state["publication_pending"] is False

    @pytest.mark.parametrize("render_recovers", [True, False])
    def test_prepare_render_degradation_survives_publication_once(
        self, tmp_path, monkeypatch, render_recovers
    ):
        """The report handoff must not erase failures settled before it."""
        self._seed(tmp_path, severity="low")
        (tmp_path / "review-report.md").unlink()
        _write_critic_verdict(tmp_path, "STAND")
        state = {}
        original_materialize = orchestration_mod._materialize_markdown

        monkeypatch.setattr(
            orchestration_mod, "_materialize_markdown",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))

        note = "review-findings.md render failed: boom"
        assert state["publication_pending"] is True
        assert state["degradation_notes"].count(note) == 1
        assert not (tmp_path / "pipeline-result.json").exists()

        if render_recovers:
            monkeypatch.setattr(
                orchestration_mod, "_materialize_markdown",
                original_materialize,
            )
        (tmp_path / "review-report.md").write_text("# report")
        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert result["degradation_notes"].count(note) == 1

    def test_varying_render_diagnostics_converge_by_stable_identity(
        self, tmp_path, monkeypatch
    ):
        """Volatile exception prose must not create an endless stale loop."""
        self._seed(tmp_path, severity="low")
        _write_critic_verdict(tmp_path, "STAND")
        state = {}
        attempts = iter(("boom one", "boom two"))

        def fail_with_changing_diagnostic(*_args, **_kwargs):
            raise RuntimeError(next(attempts))

        monkeypatch.setattr(
            orchestration_mod, "_materialize_markdown",
            fail_with_changing_diagnostic,
        )

        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))

        assert state["publication_pending"] is True
        assert state["report_handoff_status"] == "unbound_report"
        assert state["step_11_degradation_records"] == [{
            "code": "findings_markdown_render_failed",
            "message": "review-findings.md render failed: boom one",
        }]

        (tmp_path / "review-report.md").write_text(
            "# report\nRewritten from the prepared source."
        )
        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert result["degradation_notes"] == [
            "review-findings.md render failed: boom one"
        ]
        assert state["report_handoff_status"] == "published"
        assert len(state["step_11_degradation_records"]) == 1

    def test_malformed_owned_degradations_fail_closed(
        self, tmp_path
    ):
        """Re-entry inherits only a valid step-11-owned note collection."""
        self._seed(tmp_path, severity="low")
        _write_critic_verdict(tmp_path, "STAND")
        state = {
            "publication_pending": True,
            "step_11_degradation_notes": ["owned note", 42],
            "degradation_notes": ["unrelated generic state note"],
        }

        _publish_step_11(tmp_path, state)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "success"
        assert result["degradation_notes"] == []
        assert state["step_11_degradation_records"] == []
        assert "step_11_degradation_notes" not in state

    def test_distinct_degradation_categories_keep_first_seen_order(
        self
    ):
        state = {"step_11_degradation_records": [
            {
                "code": "findings_markdown_render_failed",
                "message": "render first diagnostic",
            },
            {
                "code": "review_record_assembly_failed",
                "message": "record diagnostic",
            },
        ]}
        current = [
            {
                "code": "findings_markdown_render_failed",
                "message": "render later diagnostic",
            },
            {"code": "findings_missing", "message": "findings diagnostic"},
        ]

        merged = orchestration_mod._merge_step_11_degradation_records(
            state, current
        )

        assert merged == [
            {
                "code": "findings_markdown_render_failed",
                "message": "render first diagnostic",
            },
            {
                "code": "review_record_assembly_failed",
                "message": "record diagnostic",
            },
            {"code": "findings_missing", "message": "findings diagnostic"},
        ]

    def test_unrecognized_private_degradation_code_is_not_inherited(self):
        state = {"step_11_degradation_records": [{
            "code": "foreign_producer",
            "message": "unrelated private state prose",
        }]}

        assert orchestration_mod._merge_step_11_degradation_records(
            state, []
        ) == []

    def test_unhashable_private_degradation_code_fails_closed(self):
        state = {"step_11_degradation_records": [{
            "code": ["not", "a", "string"],
            "message": "malformed private state prose",
        }]}

        assert orchestration_mod._merge_step_11_degradation_records(
            state, []
        ) == []

    @pytest.mark.parametrize("record", [
        {
            "code": "probe_residue_swept",
            "message": "probe diagnostic",
            "discriminator": "not-a-provenance-digest",
        },
        {
            "code": "probe_residue_swept",
            "message": "probe diagnostic",
            "discriminator": "paths-sha256:" + "A" * 64,
        },
        {
            "code": "findings_markdown_render_failed",
            "message": "render diagnostic",
            "discriminator": "paths-sha256:" + "a" * 64,
        },
    ])
    def test_private_degradation_discriminator_is_code_owned(self, record):
        state = {"step_11_degradation_records": [record]}

        assert orchestration_mod._merge_step_11_degradation_records(
            state, []
        ) == []

    def test_a_report_authored_after_preparation_is_published(self, tmp_path):
        """A source-bound report wins over non-terminal record fallbacks."""
        self._seed(tmp_path, severity="low")
        _write_critic_verdict(tmp_path, "STAND")

        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["report_path"] == str(tmp_path / "review-report.md")


class TestNarrativeSummaryInvalidation:
    """Prose that summarizes a mutable ledger cannot be corrected, only
    invalidated.

    The critic's vocabulary reaches every field of every finding, but
    `assessment` is ledger-level prose no adjustment can address. A
    demoted critical still described as "one CRITICAL blocker" survives the
    whole correction pipeline and renders directly above the list that
    contradicts it. The pipeline cannot re-derive the prose (it is LLM
    output), so an applying batch withdraws it — auditably.
    """

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    _SUMMARY = "One CRITICAL blocker: the payment path is unescaped."

    def _seed(self, tmp_path, severity="critical"):
        _write_findings(
            tmp_path, [_finding("f1", severity)],
            assessment=self._SUMMARY,
        )

    def test_an_applying_batch_withdraws_the_summary(self, tmp_path):
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        result = apply_adjustments(tmp_path)
        assert result["applied"] == 1
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["assessment"] is None

    def test_the_invalidated_text_stays_auditable(self, tmp_path):
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        apply_adjustments(tmp_path)
        data = json.loads((tmp_path / "review-findings.json").read_text())
        invalidated = data[INVALIDATED_ASSESSMENTS_KEY]
        assert len(invalidated) == 1
        assert invalidated[0]["text"] == self._SUMMARY
        # Tied to the exact decisions that caused it, the same way each
        # touched finding names the action that touched it.
        assert invalidated[0]["invalidated_by_adjustment_ids"] == _applied_ids(data)

    def test_a_second_withdrawal_names_only_its_own_batch(self, tmp_path):
        """invalidated_by_adjustment_ids is causal attribution, not history: a second
        reconciliation round's withdrawal must name the batch that caused
        it, never the cumulative applied-ids list."""
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "first round",
        }])
        apply_adjustments(tmp_path)
        findings_path = tmp_path / "review-findings.json"
        data = json.loads(findings_path.read_text())
        first_batch = _applied_ids(data)
        # Simulate a re-reconciliation writing fresh prose.
        data["assessment"] = "Fresh assessment after round two."
        findings_path.write_text(json.dumps(data))
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "medium"}, "rationale": "second round",
        }])
        apply_adjustments(tmp_path)
        data = json.loads(findings_path.read_text())
        invalidated = data[INVALIDATED_ASSESSMENTS_KEY]
        assert len(invalidated) == 2
        second_batch = [
            i for i in _applied_ids(data) if i not in first_batch
        ]
        assert second_batch
        assert invalidated[1]["invalidated_by_adjustment_ids"] == second_batch
        assert invalidated[0]["invalidated_by_adjustment_ids"] == first_batch

    def test_a_batch_that_applies_nothing_leaves_the_summary_alone(
        self, tmp_path
    ):
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "r",
            "rejected": True, "rejection_reason": "spot-check refuted it",
        }])
        result = apply_adjustments(tmp_path)
        assert result["applied"] == 0
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["assessment"] == self._SUMMARY
        assert INVALIDATED_ASSESSMENTS_KEY not in data

    def test_a_refused_call_leaves_the_summary_alone(self, tmp_path):
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "STAND")
        assert apply_adjustments(tmp_path)["status"] == "refused"
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["assessment"] == self._SUMMARY

    def test_no_summary_to_withdraw_records_no_withdrawal(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "critical")])
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "r",
        }])
        apply_adjustments(tmp_path)
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["assessment"] is None
        assert INVALIDATED_ASSESSMENTS_KEY not in data

    def test_a_second_batch_appends_rather_than_overwrites(self, tmp_path):
        """Two rounds of adjustments are two withdrawals — the first must
        not be erased by the second."""
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "r",
        }])
        apply_adjustments(tmp_path)
        # A second reconciliation pass writes fresh prose, then a second
        # critic round adjusts again.
        data = json.loads((tmp_path / "review-findings.json").read_text())
        data["assessment"] = "Second assessment."
        atomic_write_json(str(tmp_path / "review-findings.json"), data)
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "high"}, "rationale": "r2",
        }])
        apply_adjustments(tmp_path)
        data = json.loads((tmp_path / "review-findings.json").read_text())
        texts = [entry["text"] for entry in data[INVALIDATED_ASSESSMENTS_KEY]]
        assert texts == [self._SUMMARY, "Second assessment."]


class TestStepElevenWithdrawsContradictedProse:
    """The reproduced defect, end to end.

    A critical finding described in the Assessment, demoted by the critic:
    the rendered Markdown used to print the demotion in its finding list and
    the stale "one CRITICAL blocker" claim directly above it.
    """

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def test_demoted_finding_is_not_still_described_as_critical(
        self, tmp_path
    ):
        finding = _finding("f1", "critical")
        finding["title"] = "Unescaped payment path"
        _write_findings(
            tmp_path, [finding],
            assessment=(
                "One CRITICAL blocker: the payment path is unescaped."
            ),
        )
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")

        _orchestrate_step_11("pr", {}, {}, {}, str(tmp_path))

        rendered = (tmp_path / "review-findings.md").read_text()
        assert "## Low Findings" in rendered
        assert "CRITICAL blocker" not in rendered
        assert "invalidated" in rendered.lower()


class TestCheckPassthrough:
    """The ledger's `checks` must survive every writer after the
    reconciliator, or "what held" cannot be reported from the artifact.

    The field run only ever carried `checks: null`, so a write path
    that quietly drops unknown-to-it keys would have looked identical.
    """

    CHECKS = [
        {
            "id": "c1",
            "question": "Does any caller depend on the removed `legacy_hook` filter?",
            "method": "git grep -n legacy_hook across the repo + "
                      "enumerated every add_filter site",
            "result": "0 in-tree consumers",
            "source_reviewers": [
                "security-reviewer", "wp-architecture-reviewer"
            ],
        },
    ]

    def test_apply_adjustments_preserves_checks(
        self, tmp_path, revise_verdict
    ):
        _write_findings(
            tmp_path, [_finding("F1")], checks=self.CHECKS
        )
        _write_adjustments(tmp_path, [
            {"adjustment_id": "A1", "action": "promote", "id": "F1",
             "fields": {"severity": "high"}, "rationale": "r"},
        ])

        result = apply_adjustments(str(tmp_path))

        assert result["applied"] == 1
        after = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        assert after["checks"] == self.CHECKS

    def test_write_findings_does_not_filter_unknown_keys(self, tmp_path):
        """`write_findings` is a whole-document replace, not a projection —
        it has no field vocabulary of its own to fall out of date."""
        payload = {"findings": [], "checks": self.CHECKS,
                   "a_future_key": {"kept": True}}
        write_findings(str(tmp_path), payload)
        assert json.loads(
            (tmp_path / "review-findings.json").read_text()
        ) == payload

    def test_rendered_markdown_carries_the_checks_section(self, tmp_path):
        """End of the chain: the renderer the report is told to quote."""
        _write_findings(tmp_path, [_finding("F1")], checks=self.CHECKS)
        script = PLUGIN_ROOT / "scripts" / "review" / "agent" / "output.py"
        result = subprocess.run(
            [sys.executable, str(script), "render",
             str(tmp_path / "review-findings.json")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "## Verified Checks" in result.stdout
        assert "legacy_hook" in result.stdout
        assert "security-reviewer, wp-architecture-reviewer" in result.stdout


class TestReconciliatorCheckPin:
    """Writer #1 is an agent following a Markdown snippet, so only a test
    can hold it to teaching structurally merged checks.

    Before this, the taught template never mentioned it: the ledger's
    `checks` was always null, and step 9 rebuilt "what was verified and
    held" from the orchestrator's memory — the exact from-memory reporting
    the artifact chain exists to prevent.
    """

    SNIPPET = PLUGIN_ROOT / "agents" / "review-reconciliator.md"

    def _text(self):
        return self.SNIPPET.read_text(encoding="utf-8")

    def test_the_template_teaches_structural_check_recording(self):
        text = self._text()
        assert "builder._record_check(" in text
        for kwarg in (
            "question=", "method=", "result=", "source_reviewers="
        ):
            assert kwarg in text.split("builder._record_check(", 1)[1][:500]

    def test_the_template_excludes_void_and_correlated_checks(self):
        text = self._text()
        taught = text.split("builder._record_check(", 1)[0]
        assert "Do NOT record" in taught
        assert "VOID" in taught
        assert "method-correlated duplicates" in taught

    def test_the_structured_home_table_lists_checks(self):
        assert (
            "| `_record_check(...)` → `## Verified Checks` |"
            in self._text()
        )

    @staticmethod
    def _weighting_rules(text):
        """The numbered rules under Verification-Method Weighting."""
        section = text.split("## Verification-Method Weighting & Conflicts", 1)
        assert len(section) == 2, "weighting section missing"
        body = section[1].split("\n## ", 1)[0]
        rules = {}
        current = None
        for line in body.split("\n"):
            match = re.match(r"^(\d+)\. ", line)
            if match:
                current = int(match.group(1))
                rules[current] = line
            elif current is not None and line.strip():
                rules[current] += " " + line.strip()
        return rules

    def test_the_method_judgment_is_not_scoped_to_conflicts(self):
        """The defect this pins: the judgment that decides which
        checks get recorded used to be defined only for checks
        that CONTRADICT a finding.

        Read literally, that made the common case — a check nothing
        argues with — ineligible for recording, silently reverting the
        whole feature, and left a bad-method check that contradicts
        nothing with no void path at all.
        """
        rules = self._weighting_rules(self._text())
        judgment = rules[4]

        # The judgment rule is stated for every check...
        assert "EVERY check" in judgment
        assert "conflict or no conflict" in judgment
        # ...and says so where a reader would otherwise assume otherwise.
        assert "even when no finding contradicts it" in judgment
        # ...and the conflict case is explicitly the special case on top.
        assert "special case on top of rule 4" in rules[5]

    def test_recording_does_not_live_only_inside_the_conflict_rule(self):
        """`record_check` must be reachable from the universal judgment,
        not only from the rule about contested checks."""
        rules = self._weighting_rules(self._text())
        assert "record_check()" in rules[4]
        assert "RECORDED" in rules[4]
        # The conflict rule may reference the judgment, but must not be
        # the only place recording is authorized.
        assert "record_check()" not in rules[5]

    def test_the_template_agrees_that_uncontested_checks_are_recorded(
        self,
    ):
        taught = self._text().split("builder._record_check(", 1)[0]
        assert "not only to the ones some finding argued with" in taught
        assert "nothing contradicted is the ordinary case" in taught


class TestReconciliatorWritePathPin:
    """Writer #1 is an agent following a Markdown snippet, so the only
    thing that can hold it to the sanctioned write path is a test.

    Since findings_save.py shipped, the reconciliator no longer calls
    `write_findings()` directly — it stages the ledger in `$TMPDIR` and
    saves it through `findings_save.py`, the validating channel that
    calls `write_findings()` internally (mirroring critic.py's `--save`
    mode for the decision critic). If `agents/review-reconciliator.md`
    drifts back to writing the ledger directly — the bare atomic write it
    carried two commits ago, or a direct `write_findings()` call from
    before this channel existed — the ledger has an unvalidated write
    path again, with the rest of the suite green, because no Python
    caller changed.
    """

    SNIPPET = PLUGIN_ROOT / "agents" / "review-reconciliator.md"

    def _text(self):
        return self.SNIPPET.read_text(encoding="utf-8")

    def test_the_snippet_saves_through_findings_save(self):
        text = self._text()
        assert "scripts/review/findings_save.py" in text
        assert "--output-dir" in text
        assert "--findings" in text

    def test_the_snippet_does_not_write_the_ledger_any_other_way(self):
        """Named spellings, not a blanket ban: `atomic_write_json` and
        `write_findings` may legitimately appear in prose about the write
        path — what must not come back is a call that writes THIS
        artifact directly instead of going through findings_save.py."""
        text = self._text()
        for forbidden in (
            'atomic_write_json(f"{output_dir}/review-findings.json"',
            "atomic_write_json(f'{output_dir}/review-findings.json'",
            'open(f"{output_dir}/review-findings.json"',
            "json.dump(output",
            "from review.critic_adjustments import write_findings",
            "write_findings(output_dir, output)",
        ):
            assert forbidden not in text, forbidden



# =============================================================================
# Orchestrator judgment in the adjustments channel
# =============================================================================

class TestSpotCheckVocabulary:
    """Settlement vocabulary is script-derived lifecycle state."""

    def _document(self, **entry_extra):
        entry = {
            "adjustment_id": "script-owned",
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }
        entry.update(entry_extra)
        document = {"schema": 1, "adjustments": [entry]}
        document["adjudication"] = {
            "schema": 1,
            "source": "orchestrator",
            "proposal_digest": critic_adjustments_module.proposal_digest(
                document
            ),
            "recorded_at": "2026-08-24T10:00:00+00:00",
            "revised_assessment": "Settled assessment.",
        }
        return document

    @pytest.mark.parametrize("value", ["verified", "refuted", "not_checked"])
    def test_each_vocabulary_value_is_accepted(self, value):
        lifecycle = {"spot_check": value}
        if value == "refuted":
            lifecycle.update({
                "rejected": True,
                "rejection_reason": "The source probe refuted it.",
            })
        assert critic_adjustments_module.validate_adjustments_document(
            self._document(**lifecycle)
        ) == []

    def test_a_settled_entry_requires_a_spot_check(self):
        problems = critic_adjustments_module.validate_adjustments_document(
            self._document()
        )
        assert problems and "spot_check" in problems[0]

    @pytest.mark.parametrize("value", [
        "checked", "VERIFIED", "not checked", "", True, 1, None,
    ])
    def test_an_unknown_value_rejects_the_batch(self, value):
        problems = critic_adjustments_module.validate_adjustments_document(
            self._document(spot_check=value)
        )
        assert problems and "spot_check" in problems[0]

    def test_a_rejected_entry_may_not_claim_verified(self):
        problems = critic_adjustments_module.validate_adjustments_document(
            self._document(
                rejected=True,
                rejection_reason="probe refuted it",
                spot_check="verified",
            )
        )
        assert any("rejected is present only" in problem for problem in problems)

    def test_a_rejected_entry_marked_refuted_is_accepted(self):
        assert critic_adjustments_module.validate_adjustments_document(
            self._document(
                rejected=True,
                rejection_reason="probe refuted it",
                spot_check="refuted",
            )
        ) == []

    def test_a_rejected_entry_without_a_spot_check_is_rejected(self):
        problems = critic_adjustments_module.validate_adjustments_document(
            self._document(
                rejected=True, rejection_reason="probe refuted it"
            )
        )
        assert any("spot_check" in problem for problem in problems)

    def test_the_critic_proposal_gate_rejects_spot_check(self):
        problems = validate_proposal_input({
            "schema": 1,
            "adjustments": [{
                "action": "demote",
                "id": "f1",
                "fields": {"severity": "low"},
                "rationale": "guarded upstream",
                "spot_check": "verified",
            }],
        })
        assert any("spot_check" in problem for problem in problems)


class TestSpotCheckRecordedInTheLedger:
    """The applied-ids record carries the orchestrator's outcome per id."""

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def _apply(self, tmp_path, **entry_extra):
        _write_findings(tmp_path, [_finding("f1", "high")])
        entry = {
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }
        entry.update(entry_extra)
        _write_adjustments(tmp_path, [entry])
        assert apply_adjustments(str(tmp_path))["applied"] == 1
        return json.loads((tmp_path / "review-findings.json").read_text())

    def test_an_unchecked_entry_records_not_checked(self, tmp_path):
        data = self._apply(tmp_path)
        assert data[APPLIED_IDS_KEY][0]["spot_check"] == "not_checked"

    @pytest.mark.parametrize("value", ["verified", "not_checked"])
    def test_a_declared_outcome_is_recorded_verbatim(self, tmp_path, value):
        data = self._apply(tmp_path, spot_check=value)
        assert data[APPLIED_IDS_KEY][0]["spot_check"] == value

    def test_the_record_still_carries_the_adjustment_id(self, tmp_path):
        data = self._apply(tmp_path)
        adjustments = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        assert data[APPLIED_IDS_KEY][0]["adjustment_id"] == (
            adjustments["adjustments"][0]["adjustment_id"]
        )

    def test_a_legacy_string_record_still_reads_as_applied(self, tmp_path):
        """A ledger written before the record grew a shape must not
        re-apply its own settled decisions — idempotence is keyed on the
        id, whichever way the id was stored."""
        _write_findings(tmp_path, [_finding("f1", "high")])
        data = json.loads((tmp_path / "review-findings.json").read_text())
        data[APPLIED_IDS_KEY] = ["legacy-id"]
        write_findings(str(tmp_path), data)
        _write_adjustments(tmp_path, [{
            "adjustment_id": "legacy-id", "action": "demote",
            "id": "f1", "fields": {"severity": "low"},
            "rationale": "already landed",
        }])
        assert apply_adjustments(str(tmp_path))["applied"] == 0
        after = json.loads((tmp_path / "review-findings.json").read_text())
        assert after["findings"][0]["severity"] == "high"

    def test_pending_count_reads_both_record_shapes(self, tmp_path):
        _write_findings(tmp_path, [
            _finding("f1", "high"), _finding("f2", "low"),
        ])
        data = json.loads((tmp_path / "review-findings.json").read_text())
        data[APPLIED_IDS_KEY] = [
            "legacy-id", {"adjustment_id": "new-id", "spot_check": "verified"},
        ]
        write_findings(str(tmp_path), data)
        _write_adjustments(tmp_path, [
            {"adjustment_id": "legacy-id", "action": "demote", "id": "f1",
             "fields": {"severity": "low"}, "rationale": "r"},
            {"adjustment_id": "new-id", "action": "promote", "id": "f2",
             "fields": {"severity": "critical"}, "rationale": "r"},
        ])
        assert pending_count(str(tmp_path)) == 0


class TestRevisedNarrative:
    """The orchestrator's post-critic assessment, in the channel.

    An applying batch withdraws the reconciler's `assessment` and
    nothing used to replace it, so a REVISE run published a ledger whose
    Assessment section pointed at a report the machine could not read.
    """

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    _SUMMARY = "One CRITICAL blocker: the payment path is unescaped."
    _REVISED = "After spot-checking: the blocker is guarded upstream."

    def _seed(self, tmp_path):
        _write_findings(
            tmp_path, [_finding("f1", "critical")],
            assessment=self._SUMMARY,
        )

    def _write_doc(self, tmp_path, **doc_extra):
        entry = {
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }
        _write_adjustments(tmp_path, [entry], **doc_extra)

    def test_a_non_string_revised_assessment_rejects_the_batch(self):
        problems = validate_proposal_input({
            "schema": 1, "adjustments": [], "revised_assessment": ["a", "b"],
        })
        assert problems and "revised_assessment" in problems[0]

    def test_a_string_revised_assessment_is_accepted_in_adjudication(self):
        document = {"schema": 1, "adjustments": []}
        document["adjudication"] = {
            "schema": 1,
            "source": "orchestrator",
            "proposal_digest": critic_adjustments_module.proposal_digest(
                document
            ),
            "recorded_at": "2026-08-24T10:00:00+00:00",
            "revised_assessment": "text",
        }
        assert critic_adjustments_module.validate_adjustments_document(
            document
        ) == []

    def test_it_becomes_the_ledger_assessment(self, tmp_path):
        self._seed(tmp_path)
        self._write_doc(tmp_path, revised_assessment=self._REVISED)
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["assessment"] == self._REVISED

    def test_the_withdrawal_record_survives_the_replacement(self, tmp_path):
        """Replacement is not erasure: the reconciler's retracted words
        stay auditable beside the ids that cost them their standing."""
        self._seed(tmp_path)
        self._write_doc(tmp_path, revised_assessment=self._REVISED)
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data[INVALIDATED_ASSESSMENTS_KEY][0]["text"] == self._SUMMARY

    def test_a_blank_revised_assessment_is_rejected_without_mutation(
        self, tmp_path
    ):
        self._seed(tmp_path)
        self._write_doc(tmp_path, revised_assessment="   ")
        with pytest.raises(ValueError, match="revised_assessment"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["assessment"] == self._SUMMARY

    def test_a_batch_that_applies_nothing_never_replaces_the_summary(
        self, tmp_path
    ):
        self._seed(tmp_path)
        self._write_doc(tmp_path, revised_assessment=self._REVISED)
        doc = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        doc["adjustments"][0]["applied"] = True
        (tmp_path / "decision-critic-adjustments.json").write_text(
            json.dumps(doc)
        )
        ledger = json.loads((tmp_path / "review-findings.json").read_text())
        ledger[APPLIED_IDS_KEY] = [{
            "adjustment_id": doc["adjustments"][0]["adjustment_id"],
            "spot_check": doc["adjustments"][0]["spot_check"],
        }]
        write_findings(str(tmp_path), ledger)
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["assessment"] == self._SUMMARY


class TestWithdrawnAssessmentRender:
    """A invalidated-and-unreplaced assessment renders as an explicit
    absence — never an empty section, never the retracted text.

    The prior wording sent the reader to "the report for the current
    assessment", which on a bot run is a file nobody reads and on any run
    may carry no post-critic assessment at all.
    """

    def _render(self, **overrides):
        from review.agent.output import render_markdown
        data = {
            "pr_id": "42", "reviewer": "reconciliator",
            "timestamp": "2026-08-13T10:00:00", "plugin_version": None,
            "schema": 2, "verdict": "approve",
            "summary": {"total_findings": 0, "by_severity": {
                "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
            }},
            "findings": [], "assessment": None,
            "review_claimable_files": [], "reviewed_file_claims": [],
            "unclaimed_review_files": [], "inline_diff_file_count": 1,
            "review_accounted_file_count": 1,
            "in_scope_review_file_count": 1, "observations": None,
            "recommendations": None, "positive_observations": None,
            "checks": None,
            "meta": {"review_duration_ms": 1,
                     "confidence_score": 0.9},
        }
        data.update(overrides)
        return render_markdown(data)

    def test_invalidated_without_replacement_says_so(self):
        md = self._render(invalidated_assessments=[
            {"text": "One CRITICAL blocker.", "invalidated_by_adjustment_ids": ["a1"]},
        ])
        assert "No current assessment" in md
        assert "not replaced" in md
        assert "One CRITICAL blocker." not in md

    def test_a_replacement_is_not_attributed_to_the_reconciler(self):
        md = self._render(
            assessment="After spot-checking: guarded upstream.",
            invalidated_assessments=[
                {"text": "One CRITICAL blocker.", "invalidated_by_adjustment_ids": ["a1"]},
            ],
        )
        assert "After spot-checking: guarded upstream." in md
        assert "not adjusted by the decision critic" not in md

    def test_an_untouched_assessment_still_reads_as_the_reconcilers(self):
        md = self._render(assessment="The change is sound.")
        assert "not adjusted by the decision critic" in md

    def test_spot_check_outcomes_render_per_id(self):
        md = self._render(applied_critic_adjustments=[
            {"adjustment_id": "aaaa", "spot_check": "verified"},
            {"adjustment_id": "bbbb", "spot_check": "not_checked"},
        ])
        assert "aaaa" in md and "verified" in md
        assert "bbbb" in md and "not_checked" in md

    def test_mixed_applied_and_refuted_decisions_render_per_id(self):
        md = self._render(
            applied_critic_adjustments=[
                {"adjustment_id": "aaaa", "spot_check": "verified"},
            ],
            rejected_critic_adjustments=[
                {"adjustment_id": "bbbb", "rejection_reason": "refuted"},
            ],
        )
        assert "## Critic Adjustment Decisions" in md
        assert "- `aaaa` — verified" in md
        assert "- `bbbb` — refuted" in md

    def test_all_refuted_decisions_still_render(self):
        md = self._render(rejected_critic_adjustments=[
            {"adjustment_id": "aaaa", "spot_check": "refuted"},
            {"adjustment_id": "bbbb", "rejection_reason": "not true"},
        ])
        assert "## Critic Adjustment Decisions" in md
        assert "- `aaaa` — refuted" in md
        assert "- `bbbb` — refuted" in md

    def test_legacy_applied_ids_render_not_checked(self):
        md = self._render(applied_critic_adjustments=["legacy-id"])
        assert "- `legacy-id` — not_checked" in md

    def test_malformed_decision_records_are_ignored(self):
        md = self._render(
            applied_critic_adjustments=[
                None, "", {"spot_check": "verified"},
                {"adjustment_id": 7, "spot_check": "verified"},
                {"adjustment_id": "bad", "spot_check": []},
            ],
            rejected_critic_adjustments=[None, "bad", {}, {"adjustment_id": 7}],
        )
        assert "Critic Adjustment Decisions" not in md

    def test_no_critic_decisions_renders_no_section(self):
        assert "Critic Adjustment Decisions" not in self._render()


class TestLedgerVerdictRecompute:
    """`_recount_summary` rebuilt the severities and left `verdict` alone.

    That was survivable while step 11 copied an orchestrator-transcribed
    verdict over the ledger's; with the published verdict DERIVED from the
    ledger, a stale `request_changes` over a demoted-to-low finding list is
    machine authority for a wrong GitHub verdict.
    """

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def test_demoting_the_last_high_moves_the_verdict(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "high")],
                        verdict="request_changes")
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "approve"

    def test_promoting_to_critical_blocks(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "medium")],
                        verdict="comment")
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "critical"}, "rationale": "unguarded",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "block"

    def test_unrelated_adjustment_keeps_advisory_high_non_gating(self, tmp_path):
        advisory = _finding("f1", "high")
        advisory["channel"] = "advisory"
        _write_findings(
            tmp_path,
            [advisory, _finding("f2", "low")],
            verdict="request_changes",
        )
        _write_adjustments(tmp_path, [{
            "action": "correct", "id": "f2",
            "fields": {"title": "corrected title"},
            "rationale": "clarify the existing finding",
        }])

        apply_adjustments(str(tmp_path))

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["summary"]["by_severity"] == {
            "critical": 0,
            "high": 1,
            "medium": 0,
            "low": 1,
            "info": 0,
        }
        assert data["verdict"] == "approve"
        assert data["summary"]["suppressed_advisory_finding_count"] == 1
        assert data["summary"]["verdict_without_advisory"] == "request_changes"

    def test_the_pre_apply_verdict_is_preserved(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "high")],
                        verdict="request_changes")
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict_before_adjustments"] == "request_changes"

    def test_the_audit_trail_records_only_the_first_change(self, tmp_path):
        """A second round must name what the ledger came in as, not what
        the previous round left behind."""
        _write_findings(tmp_path, [_finding("f1", "high")],
                        verdict="request_changes")
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "round one",
        }])
        apply_adjustments(str(tmp_path))
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "f1",
            "fields": {"severity": "medium"}, "rationale": "round two",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "comment"
        assert data["verdict_before_adjustments"] == "request_changes"

    def test_a_batch_that_applies_nothing_leaves_the_verdict_alone(
        self, tmp_path
    ):
        """Including the stale-but-untouched case: nothing applied means
        nothing was recomputed, and no audit trail is fabricated."""
        _write_findings(tmp_path, [_finding("f1", "high")],
                        verdict="deliberately-stale")
        _write_adjustments(tmp_path, [{
            "adjustment_id": "landed", "applied": True, "action": "demote",
            "id": "f1", "fields": {"severity": "low"}, "rationale": "r",
        }])
        ledger = json.loads((tmp_path / "review-findings.json").read_text())
        ledger[APPLIED_IDS_KEY] = [{
            "adjustment_id": "landed", "spot_check": "not_checked",
        }]
        write_findings(str(tmp_path), ledger)
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "deliberately-stale"
        assert "verdict_before_adjustments" not in data

    def test_an_unchanged_verdict_records_no_audit_trail(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "high"),
                                   _finding("f2", "high")],
                        verdict="request_changes")
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "f1",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "request_changes"
        assert "verdict_before_adjustments" not in data


# =============================================================================
# Source-bound critic proposal -> adjudication -> ledger lifecycle
# =============================================================================


class TestProposalPreparation:
    def _entry(self, **extra):
        entry = {
            "action": "demote",
            "id": "f1",
            "fields": {"severity": "medium"},
            "rationale": "The claimed impact is narrower than stated.",
        }
        entry.update(extra)
        return entry

    def test_prepare_assigns_unique_stable_ids(self):
        payload = {
            "schema": 1,
            "adjustments": [self._entry(), {
                **self._entry(), "id": "f2",
            }],
        }

        proposal = critic_adjustments_module.prepare_proposal(payload)

        ids = [entry["adjustment_id"] for entry in proposal["adjustments"]]
        assert all(ids)
        assert len(ids) == len(set(ids)) == 2
        assert payload["adjustments"][0].get("adjustment_id") is None, (
            "normalization must not mutate the critic's temp input"
        )

    def test_prepare_retries_the_improbable_uuid_collision(self, monkeypatch):
        values = iter(("same", "same", "different"))

        class FakeUuid:
            def __init__(self, value):
                self.hex = value

        monkeypatch.setattr(
            critic_adjustments_module.uuid,
            "uuid4",
            lambda: FakeUuid(next(values)),
        )

        proposal = critic_adjustments_module.prepare_proposal({
            "schema": 1,
            "adjustments": [self._entry(), {
                **self._entry(), "id": "f2",
            }],
        })

        assert [
            entry["adjustment_id"] for entry in proposal["adjustments"]
        ] == ["same", "different"]

    def test_prepare_rejects_duplicate_targets_before_assigning_ids(self):
        with pytest.raises(ValueError, match="duplicate target 'f1'"):
            critic_adjustments_module.prepare_proposal({
                "schema": 1,
                "adjustments": [
                    self._entry(),
                    {
                        "action": "correct",
                        "id": "f1",
                        "fields": {"title": "Clearer title"},
                        "rationale": "Clarify the mechanism.",
                    },
                ],
            })

    @pytest.mark.parametrize(
        "forbidden,value",
        [
            ("adjustment_id", "critic-owned"),
            ("spot_check", "verified"),
            ("rejected", True),
            ("rejection_reason", "caller-owned"),
            ("applied", True),
        ],
    )
    def test_prepare_rejects_lifecycle_fields(self, forbidden, value):
        with pytest.raises(ValueError, match=forbidden):
            critic_adjustments_module.prepare_proposal({
                "schema": 1,
                "adjustments": [self._entry(**{forbidden: value})],
            })

    @pytest.mark.parametrize(
        "payload,problem",
        [
            (
                {"schema": 1, "adjustments": [], "revised_assessment": "x"},
                "revised_assessment",
            ),
            (
                {"schema": 1, "adjustments": [], "adjudication": {}},
                "adjudication",
            ),
            (
                {"schema": 1, "adjustments": [], "counts": {}},
                "counts",
            ),
        ],
    )
    def test_prepare_rejects_non_proposal_top_level_fields(
        self, payload, problem
    ):
        with pytest.raises(ValueError, match=problem):
            critic_adjustments_module.prepare_proposal(payload)

    def test_proposal_digest_ignores_adjudication_and_apply_state(self):
        proposal = critic_adjustments_module.prepare_proposal({
            "schema": 1,
            "adjustments": [self._entry()],
        })
        before = critic_adjustments_module.proposal_digest(proposal)
        proposal["adjustments"][0].update({
            "spot_check": "verified",
            "applied": True,
        })
        proposal["adjudication"] = {
            "schema": 1,
            "source": "orchestrator",
            "proposal_digest": before,
            "recorded_at": "2026-08-24T10:00:00+00:00",
            "revised_assessment": "Settled assessment.",
        }

        assert critic_adjustments_module.proposal_digest(proposal) == before

    def test_persisted_document_requires_unique_script_assigned_ids(self):
        entry = self._entry()
        missing = {"schema": 1, "adjustments": [entry]}
        duplicate = {
            "schema": 1,
            "adjustments": [
                {"adjustment_id": "dup", **entry},
                {"adjustment_id": "dup", **entry, "id": "f2"},
            ],
        }

        assert any(
            "adjustment_id" in problem
            for problem in critic_adjustments_module.validate_adjustments_document(
                missing
            )
        )
        assert any(
            "duplicate adjustment_id" in problem
            for problem in critic_adjustments_module.validate_adjustments_document(
                duplicate
            )
        )


class TestAdjudicationRequest:
    ENTRIES = [
        {
            "action": "demote",
            "id": "f1",
            "fields": {"severity": "medium"},
            "rationale": "Narrower than stated.",
        },
        {
            "action": "promote",
            "id": "f2",
            "fields": {"severity": "critical"},
            "rationale": "The source confirms a wider impact.",
        },
        {
            "action": "correct",
            "id": "f3",
            "fields": {"title": "Corrected title"},
            "rationale": "The original title overstates the mechanism.",
        },
    ]

    def _seed(self, tmp_path):
        _write_findings(tmp_path, [
            _finding("f1", "high"),
            _finding("f2", "medium"),
            _finding("f3", "low"),
        ])
        return _commit_critic_snapshot(tmp_path, self.ENTRIES)

    def test_settle_derives_the_unchecked_complement(self, tmp_path):
        proposal = self._seed(tmp_path)
        request = _settlement_request(
            proposal,
            verified=(0,),
            refuted=((1, "Refuted by the source probe."),),
            assessment="One proposal landed and one was rejected.",
        )

        result = critic_adjustments_module.settle(str(tmp_path), request)

        doc = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        assert [entry["spot_check"] for entry in doc["adjustments"]] == [
            "verified", "refuted", "not_checked",
        ]
        assert doc["adjustments"][1]["rejected"] is True
        assert doc["adjustments"][1]["rejection_reason"] == (
            "Refuted by the source probe."
        )
        assert "rejected" not in doc["adjustments"][0]
        assert "rejected" not in doc["adjustments"][2]
        assert doc["adjudication"]["source"] == "orchestrator"
        assert doc["adjudication"]["proposal_digest"] == (
            critic_adjustments_module.proposal_digest(doc)
        )
        assert doc["adjudication"]["revised_assessment"] == (
            "One proposal landed and one was rejected."
        )
        assert result["counts"] == {
            "verified": 1, "refuted": 1, "not_checked": 1,
        }
        assert not ({"verified", "refuted", "not_checked", "counts"} & set(doc))

    @pytest.mark.parametrize(
        "mutate,problem",
        [
            (
                lambda request, ids: request.update({"not_checked": [ids[2]]}),
                "not_checked",
            ),
            (
                lambda request, ids: request.update({"counts": {}}),
                "counts",
            ),
            (
                lambda request, ids: request.update({
                    "recorded_at": "2026-08-24T10:00:00+00:00"
                }),
                "recorded_at",
            ),
            (
                lambda request, ids: request.update({
                    "spot_check": "verified"
                }),
                "spot_check",
            ),
            (
                lambda request, ids: request.update({"applied": True}),
                "applied",
            ),
            (
                lambda request, ids: request["verified"].append(7),
                "string",
            ),
            (
                lambda request, ids: request["verified"].append(ids[0]),
                "duplicate",
            ),
            (
                lambda request, ids: request["refuted"].append({
                    "adjustment_id": ids[0],
                    "rejection_reason": "overlap",
                }),
                "both verified and refuted",
            ),
            (
                lambda request, ids: request["verified"].append("unknown-id"),
                "unknown",
            ),
            (
                lambda request, ids: request["refuted"].append({
                    "adjustment_id": ids[1],
                    "rejection_reason": " ",
                }),
                "rejection_reason",
            ),
            (
                lambda request, ids: request["refuted"].append({
                    "adjustment_id": ids[1],
                    "rejection_reason": "reason",
                    "rejected": True,
                }),
                "extra",
            ),
            (
                lambda request, ids: request.update({
                    "revised_assessment": " "
                }),
                "revised_assessment",
            ),
        ],
        ids=[
            "caller-not-checked", "caller-counts", "caller-timestamp",
            "caller-spot-check", "caller-apply-state", "non-string-verified",
            "duplicate-verified", "overlap", "unknown-id", "blank-reason",
            "refuted-extra-key", "blank-assessment",
        ],
    )
    def test_invalid_request_leaves_checkpoint_and_ledger_byte_identical(
        self, tmp_path, mutate, problem
    ):
        proposal = self._seed(tmp_path)
        ids = [entry["adjustment_id"] for entry in proposal["adjustments"]]
        request = _settlement_request(proposal, verified=(0,))
        mutate(request, ids)
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match=problem):
            critic_adjustments_module.settle(str(tmp_path), request)

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    def test_unknown_ledger_target_is_rejected_before_checkpoint(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1", "low")])
        proposal = _commit_critic_snapshot(tmp_path, [{
            "action": "promote",
            "id": "missing-id",
            "fields": {"severity": "high"},
            "rationale": "The proposal points at a missing finding.",
        }])
        request = _settlement_request(proposal, verified=(0,))
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match="no finding with id 'missing-id'"):
            critic_adjustments_module.settle(str(tmp_path), request)

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    def test_duplicate_ledger_target_is_rejected_before_checkpoint(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1", "low")])
        document = {
            "schema": 1,
            "adjustments": [
                {
                    "adjustment_id": "first",
                    "action": "promote",
                    "id": "f1",
                    "fields": {"severity": "high"},
                    "rationale": "First mutation.",
                },
                {
                    "adjustment_id": "second",
                    "action": "correct",
                    "id": "f1",
                    "fields": {"title": "Clearer title"},
                    "rationale": "Second mutation.",
                },
            ],
        }
        _write_snapshot_document(tmp_path, document)
        request = {
            "schema": 1,
            "verified": ["first", "second"],
            "refuted": [],
            "revised_assessment": "Settled assessment.",
        }
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match="duplicate target 'f1'"):
            critic_adjustments_module.settle(str(tmp_path), request)

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    def test_malformed_ledger_is_rejected_before_checkpoint(self, tmp_path):
        proposal = self._seed(tmp_path)
        ledger = json.loads((tmp_path / "review-findings.json").read_text())
        ledger[APPLIED_IDS_KEY] = "not-a-record-list"
        write_findings(str(tmp_path), ledger)
        request = _settlement_request(proposal, verified=(0,))
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match="applied_critic_adjustments"):
            critic_adjustments_module.settle(str(tmp_path), request)

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    @pytest.mark.parametrize(
        "target_id,fields,rejection_reason",
        [
            (
                "f1",
                {"severity": "high"},
                "The proposed mutation is a no-op.",
            ),
            (
                "missing-id",
                {"severity": "critical"},
                "The proposed target does not exist.",
            ),
            (
                "f1",
                {"severity": "medium"},
                "The proposed promotion moves severity downward.",
            ),
        ],
        ids=["no-op", "missing-target", "wrong-direction"],
    )
    def test_refuted_proposal_need_not_be_applicable(
        self, tmp_path, target_id, fields, rejection_reason
    ):
        _write_findings(tmp_path, [_finding("f1", "high")])
        proposal = _commit_critic_snapshot(tmp_path, [{
            "action": "promote",
            "id": target_id,
            "fields": fields,
            "rationale": "The orchestrator probe will reject this proposal.",
        }])
        request = _settlement_request(
            proposal,
            refuted=((0, rejection_reason),),
        )

        result = critic_adjustments_module.settle(str(tmp_path), request)

        document = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        findings = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        adjustment_id = proposal["adjustments"][0]["adjustment_id"]
        assert result["counts"] == {
            "verified": 0,
            "refuted": 1,
            "not_checked": 0,
        }
        assert document["adjustments"][0]["spot_check"] == "refuted"
        assert findings["findings"] == [_finding("f1", "high")]
        assert findings[REJECTED_ADJUSTMENTS_KEY] == [{
            "adjustment_id": adjustment_id,
            "action": "promote",
            "target_id": target_id,
            "spot_check": "refuted",
            "rejection_reason": rejection_reason,
        }]

    @pytest.mark.parametrize(
        "action,current,fields,problem",
        [
            (
                "promote", "high", {"severity": "medium"},
                "promote must increase severity",
            ),
            (
                "promote", "high", {"severity": "high"},
                "promote would not change severity",
            ),
            (
                "demote", "low", {"severity": "medium"},
                "demote must decrease severity",
            ),
            (
                "demote", "low", {"severity": "low"},
                "demote would not change severity",
            ),
            (
                "correct", "low", {"title": "t"},
                "correct would not change the finding",
            ),
            (
                "rescope", "low", {"line": 10},
                "rescope would not change the finding",
            ),
        ],
    )
    def test_noop_or_wrong_direction_proposal_is_rejected_before_checkpoint(
        self, tmp_path, action, current, fields, problem
    ):
        _write_findings(tmp_path, [_finding("f1", current)])
        proposal = _commit_critic_snapshot(tmp_path, [{
            "action": action,
            "id": "f1",
            "fields": fields,
            "rationale": "This mutation is not coherent with the ledger.",
        }])
        request = _settlement_request(proposal, verified=(0,))
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match=problem):
            critic_adjustments_module.settle(str(tmp_path), request)

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before


class TestAuthoritativeLedgerApplicationState:
    def _write_settled_entry(self, tmp_path, entry, **ledger_extra):
        _write_findings(
            tmp_path, [_finding("f1", "high")], **ledger_extra
        )
        _write_adjustments(tmp_path, [entry])
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        return adj_path, findings_path

    def test_applied_flag_without_ledger_provenance_is_rejected(self, tmp_path):
        paths = self._write_settled_entry(tmp_path, {
            "adjustment_id": "state-one",
            "applied": True,
            "action": "demote",
            "id": "f1",
            "fields": {"severity": "low"},
            "rationale": "Guarded upstream.",
        })
        before = tuple(path.read_bytes() for path in paths)

        with pytest.raises(ValueError, match="applied flag has no matching"):
            apply_adjustments(str(tmp_path))

        assert tuple(path.read_bytes() for path in paths) == before

    def test_pending_count_does_not_trust_an_unproven_applied_flag(
        self, tmp_path
    ):
        self._write_settled_entry(tmp_path, {
            "adjustment_id": "state-pending",
            "applied": True,
            "action": "demote",
            "id": "f1",
            "fields": {"severity": "low"},
            "rationale": "Guarded upstream.",
        })

        assert pending_count(str(tmp_path)) == 1

    def test_id_in_applied_and_rejected_provenance_is_rejected(self, tmp_path):
        paths = self._write_settled_entry(
            tmp_path,
            {
                "adjustment_id": "state-two",
                "applied": True,
                "action": "demote",
                "id": "f1",
                "fields": {"severity": "low"},
                "rationale": "Guarded upstream.",
            },
            applied_critic_adjustments=[{
                "adjustment_id": "state-two", "spot_check": "not_checked",
            }],
            rejected_critic_adjustments=[{
                "adjustment_id": "state-two",
                "action": "demote",
                "target_id": "f1",
                "spot_check": "refuted",
                "rejection_reason": "Contradictory provenance.",
            }],
        )
        before = tuple(path.read_bytes() for path in paths)

        with pytest.raises(ValueError, match="both applied and rejected"):
            apply_adjustments(str(tmp_path))

        assert tuple(path.read_bytes() for path in paths) == before

    def test_catch_up_rejects_a_spot_check_mismatch(self, tmp_path):
        paths = self._write_settled_entry(
            tmp_path,
            {
                "adjustment_id": "state-three",
                "spot_check": "verified",
                "action": "demote",
                "id": "f1",
                "fields": {"severity": "low"},
                "rationale": "Guarded upstream.",
            },
            applied_critic_adjustments=[{
                "adjustment_id": "state-three", "spot_check": "not_checked",
            }],
        )
        before = tuple(path.read_bytes() for path in paths)

        with pytest.raises(ValueError, match="spot_check.*does not match"):
            apply_adjustments(str(tmp_path))

        assert tuple(path.read_bytes() for path in paths) == before

    def test_existing_rejection_must_match_the_checkpoint(self, tmp_path):
        paths = self._write_settled_entry(
            tmp_path,
            {
                "adjustment_id": "state-four",
                "spot_check": "refuted",
                "rejected": True,
                "rejection_reason": "Checkpoint reason.",
                "action": "demote",
                "id": "f1",
                "fields": {"severity": "low"},
                "rationale": "Guarded upstream.",
            },
            rejected_critic_adjustments=[{
                "adjustment_id": "state-four",
                "action": "demote",
                "target_id": "f1",
                "spot_check": "refuted",
                "rejection_reason": "Different ledger reason.",
            }],
        )
        before = tuple(path.read_bytes() for path in paths)

        with pytest.raises(ValueError, match="rejection provenance does not match"):
            apply_adjustments(str(tmp_path))

        assert tuple(path.read_bytes() for path in paths) == before

    def test_legacy_rejection_without_spot_check_matches_refuted_checkpoint(
        self, tmp_path
    ):
        paths = self._write_settled_entry(
            tmp_path,
            {
                "adjustment_id": "state-five",
                "spot_check": "refuted",
                "rejected": True,
                "rejection_reason": "Checkpoint reason.",
                "action": "demote",
                "id": "f1",
                "fields": {"severity": "low"},
                "rationale": "Guarded upstream.",
            },
            rejected_critic_adjustments=[{
                "adjustment_id": "state-five",
                "action": "demote",
                "target_id": "f1",
                "rejection_reason": "Checkpoint reason.",
            }],
        )
        before = tuple(path.read_bytes() for path in paths)

        result = apply_adjustments(str(tmp_path))

        assert result["status"] == "nothing_pending"
        assert tuple(path.read_bytes() for path in paths) == before


class TestSourceBindingAndRecovery:
    ENTRIES = [
        {
            "action": "demote",
            "id": "f1",
            "fields": {"severity": "low"},
            "rationale": "Guarded upstream.",
        },
        {
            "action": "correct",
            "id": "f2",
            "fields": {"title": "Corrected title"},
            "rationale": "The source uses a narrower mechanism.",
        },
    ]

    def _seed(self, tmp_path):
        _write_findings(tmp_path, [
            _finding("f1", "high"), _finding("f2", "low")
        ], assessment="The original assessment.")
        return _commit_critic_snapshot(tmp_path, self.ENTRIES)

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda entry: entry.update({"adjustment_id": "raw-edit"}),
            lambda entry: entry.update({"action": "promote"}),
            lambda entry: entry.update({"id": "f2"}),
            lambda entry: entry.update({"fields": {"severity": "critical"}}),
            lambda entry: entry.update({"rationale": "Raw edit."}),
        ],
        ids=["assigned-id", "action", "target", "fields", "rationale"],
    )
    def test_immutable_edit_breaks_the_committed_digest(
        self, tmp_path, mutate
    ):
        proposal = self._seed(tmp_path)
        request = _settlement_request(proposal, verified=(0,))
        adj_path = tmp_path / "decision-critic-adjustments.json"
        doc = json.loads(adj_path.read_text())
        mutate(doc["adjustments"][0])
        adj_path.write_text(json.dumps(doc))
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match="proposal digest"):
            critic_adjustments_module.settle(str(tmp_path), request)

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    def test_checkpoint_is_written_before_the_ledger_applier(
        self, tmp_path, monkeypatch
    ):
        proposal = self._seed(tmp_path)
        request = _settlement_request(proposal, verified=(0,))

        def inspect_checkpoint(output_dir, _plan):
            doc = json.loads(
                (Path(output_dir) / "decision-critic-adjustments.json").read_text()
            )
            assert doc["adjudication"]["source"] == "orchestrator"
            assert all("spot_check" in entry for entry in doc["adjustments"])
            raise OSError("injected after checkpoint")

        monkeypatch.setattr(
            critic_adjustments_module,
            "_apply_adjustments_locked",
            inspect_checkpoint,
        )

        with pytest.raises(OSError, match="after checkpoint"):
            critic_adjustments_module.settle(str(tmp_path), request)

        ledger = json.loads((tmp_path / "review-findings.json").read_text())
        assert "critic_adjustment" not in ledger["findings"][0]

    def test_crash_after_checkpoint_converges_through_public_apply(
        self, tmp_path, monkeypatch
    ):
        proposal = self._seed(tmp_path)
        request = _settlement_request(proposal, verified=(0,))
        real_apply = critic_adjustments_module._apply_adjustments_locked

        def raise_after_checkpoint(_output_dir, _plan):
            raise OSError("injected after checkpoint")

        monkeypatch.setattr(
            critic_adjustments_module,
            "_apply_adjustments_locked",
            raise_after_checkpoint,
        )
        with pytest.raises(OSError, match="after checkpoint"):
            critic_adjustments_module.settle(str(tmp_path), request)
        checkpoint = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        assert checkpoint["adjudication"]["source"] == "orchestrator"
        assert "applied" not in checkpoint["adjustments"][0]

        monkeypatch.setattr(
            critic_adjustments_module,
            "_apply_adjustments_locked",
            real_apply,
        )
        recovered = critic_adjustments_module.apply_adjustments(str(tmp_path))

        assert recovered["status"] == "applied"
        assert recovered["applied"] == 2
        ledger = json.loads((tmp_path / "review-findings.json").read_text())
        assert len(ledger[APPLIED_IDS_KEY]) == 2

    def test_identical_retry_is_byte_stable_and_does_not_duplicate_provenance(
        self, tmp_path
    ):
        proposal = self._seed(tmp_path)
        request = _settlement_request(proposal, verified=(0,))
        critic_adjustments_module.settle(str(tmp_path), request)
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        retry = critic_adjustments_module.settle(str(tmp_path), request)

        assert retry["status"] == "already_settled"
        assert retry["apply"]["status"] == "nothing_pending"
        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before
        ledger = json.loads(findings_path.read_text())
        assert len(ledger[APPLIED_IDS_KEY]) == 2

    def test_different_request_after_checkpoint_is_rejected(self, tmp_path):
        proposal = self._seed(tmp_path)
        request = _settlement_request(proposal, verified=(0,))
        critic_adjustments_module.settle(str(tmp_path), request)
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())
        changed = _settlement_request(proposal, verified=(1,))

        with pytest.raises(ValueError, match="already settled"):
            critic_adjustments_module.settle(str(tmp_path), changed)

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    def test_defensive_apply_records_an_honest_checkpoint(self, tmp_path):
        proposal = self._seed(tmp_path)

        result = critic_adjustments_module.apply_adjustments(str(tmp_path))

        doc = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        assert doc["adjudication"]["source"] == "defensive_apply"
        assert doc["adjudication"]["revised_assessment"] is None
        assert [entry["spot_check"] for entry in doc["adjustments"]] == [
            "not_checked", "not_checked",
        ]
        assert doc["adjudication"]["proposal_digest"] == (
            critic_adjustments_module.proposal_digest(proposal)
        )
        assert doc["adjudication"]["recorded_at"].endswith("+00:00")
        assert result["adjudication_source"] == "defensive_apply"
        assert result["counts"] == {
            "verified": 0, "refuted": 0, "not_checked": 2,
        }

    def test_public_apply_acquires_one_lock_for_checkpoint_and_recovery(
        self, tmp_path, monkeypatch
    ):
        self._seed(tmp_path)
        active = False
        lock_entries = 0
        real_apply = critic_adjustments_module._apply_adjustments_locked

        @contextmanager
        def observed_lock(_output_dir):
            nonlocal active, lock_entries
            lock_entries += 1
            active = True
            try:
                yield
            finally:
                active = False

        def observed_apply(output_dir, plan):
            assert active, "public recovery must hold the output lock"
            return real_apply(output_dir, plan)

        monkeypatch.setattr(
            critic_adjustments_module.atomic_io,
            "output_dir_lock",
            observed_lock,
        )
        monkeypatch.setattr(
            critic_adjustments_module,
            "_apply_adjustments_locked",
            observed_apply,
        )

        result = critic_adjustments_module.apply_adjustments(str(tmp_path))

        assert result["status"] == "applied"
        assert lock_entries == 1

    def test_settle_holds_one_lock_across_checkpoint_and_apply(
        self, tmp_path, monkeypatch
    ):
        proposal = self._seed(tmp_path)
        request = _settlement_request(proposal, verified=(0,))
        active = False
        lock_entries = 0

        @contextmanager
        def observed_lock(_output_dir):
            nonlocal active, lock_entries
            lock_entries += 1
            active = True
            try:
                yield
            finally:
                active = False

        def observed_apply(_output_dir, _plan):
            assert active, "the checkpoint and ledger apply share one lock"
            return {"status": "nothing_pending", "applied": 0}

        monkeypatch.setattr(
            critic_adjustments_module.atomic_io,
            "output_dir_lock",
            observed_lock,
        )
        monkeypatch.setattr(
            critic_adjustments_module,
            "_apply_adjustments_locked",
            observed_apply,
        )

        critic_adjustments_module.settle(str(tmp_path), request)

        assert lock_entries == 1

    def test_save_and_settle_cannot_interleave_snapshots(
        self, tmp_path, monkeypatch
    ):
        from review import critic as critic_module

        _write_findings(tmp_path, [
            _finding("f1", "high"), _finding("f2", "low")
        ])
        old = _commit_critic_snapshot(tmp_path, self.ENTRIES)
        old_request = _settlement_request(old, verified=(0,))
        findings_source = tmp_path / "new-critic-findings.md"
        findings_source.write_text("# New findings\n")
        proposal_source = tmp_path / "new-proposal.json"
        proposal_source.write_text(json.dumps({
            "schema": 1,
            "adjustments": [{
                "action": "promote",
                "id": "f2",
                "fields": {"severity": "high"},
                "rationale": "New source evidence.",
            }],
        }))
        lock = threading.Lock()
        save_inside_write = threading.Event()
        release_save = threading.Event()
        real_write = critic_adjustments_module.write_adjustments

        @contextmanager
        def thread_lock(_output_dir):
            with lock:
                yield

        def blocking_write(output_dir, document):
            if threading.current_thread().name == "critic-save":
                save_inside_write.set()
                assert release_save.wait(timeout=2)
            return real_write(output_dir, document)

        monkeypatch.setattr(
            critic_adjustments_module.atomic_io,
            "output_dir_lock",
            thread_lock,
        )
        monkeypatch.setattr(
            critic_adjustments_module, "write_adjustments", blocking_write
        )
        results = {}

        def run_save():
            results["save"] = critic_module.run_save(type("Args", (), {
                "output_dir": str(tmp_path),
                "verdict": "REVISE",
                "findings": str(findings_source),
                "adjustments": str(proposal_source),
            })())

        def run_settle():
            try:
                results["settle"] = critic_adjustments_module.settle(
                    str(tmp_path), old_request
                )
            except ValueError as error:
                results["settle_error"] = str(error)

        save_thread = threading.Thread(target=run_save, name="critic-save")
        settle_thread = threading.Thread(target=run_settle, name="settle")
        save_thread.start()
        assert save_inside_write.wait(timeout=2)
        settle_thread.start()
        time.sleep(0.05)
        assert "settle" not in results and "settle_error" not in results
        release_save.set()
        save_thread.join(timeout=2)
        settle_thread.join(timeout=2)

        assert results["save"] == 0
        assert "settle_error" in results
        proposal = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        marker = json.loads(
            (tmp_path / "decision-critic-verdict.json").read_text()
        )
        assert marker["proposal_digest"] == (
            critic_adjustments_module.proposal_digest(proposal)
        )


class TestAdjudicationCLI:
    def _seed(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "high")])
        return _commit_critic_snapshot(tmp_path, [{
            "action": "demote",
            "id": "f1",
            "fields": {"severity": "low"},
            "rationale": "Guarded upstream.",
        }])

    def test_settle_echoes_derived_counts_digest_and_apply_result(self, tmp_path):
        proposal = self._seed(tmp_path)
        request = _settlement_request(proposal, verified=(0,))

        result = subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH), "settle",
                "--output-dir", str(tmp_path),
            ],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "RECORDED ADJUDICATION: 1" in result.stdout
        assert "VERIFIED: 1 | REFUTED: 0 | NOT_CHECKED: 0" in result.stdout
        assert "REVISED ASSESSMENT: present" in result.stdout
        assert "REVISED NARRATIVE" not in result.stdout
        assert "PROPOSAL DIGEST:" in result.stdout
        assert "APPLY: applied 1 | rejected 0" in result.stdout

    def test_repeated_cli_settlement_reports_both_idempotent_states(
        self, tmp_path
    ):
        proposal = self._seed(tmp_path)
        request = json.dumps(_settlement_request(proposal, verified=(0,)))
        cmd = [
            sys.executable, str(SCRIPT_PATH), "settle",
            "--output-dir", str(tmp_path),
        ]
        first = subprocess.run(
            cmd, input=request, capture_output=True, text=True, timeout=10
        )
        assert first.returncode == 0, first.stdout + first.stderr

        second = subprocess.run(
            cmd, input=request, capture_output=True, text=True, timeout=10
        )

        assert second.returncode == 0, second.stdout + second.stderr
        assert "ALREADY SETTLED" in second.stdout
        assert "ALREADY APPLIED" in second.stdout

    def test_bare_implicit_apply_cli_is_retired(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0

    def test_apply_remains_an_explicit_recovery_command(self, tmp_path):
        self._seed(tmp_path)

        result = subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH), "apply",
                "--output-dir", str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["adjudication_source"] == (
            "defensive_apply"
        )

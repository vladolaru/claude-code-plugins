"""Tests for critic_adjustments — the sole writer that carries decision-critic
finding-level decisions into review-findings.json."""

import builtins
import json
import os
import re
import subprocess
import sys
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
    WITHDRAWN_SUMMARY_KEY,
    REFUSAL_EXIT_CODE,
    REFUSAL_NO_VERDICT,
    REFUSAL_VERDICT_NOT_REVISE,
    apply_adjustments,
    pending_count,
    read_critic_verdict,
    validate_adjustments,
    write_findings,
)
from review import critic_adjustments as critic_adjustments_module
from review import orchestration as orchestration_mod
from review.orchestration import _orchestrate_step_11
from review.reconciliation_context import build_critic_context


def _write_findings(output_dir, issues, **extra):
    """Write a reconciliation ledger shaped the way the producer writes it.

    The adjustment writer reads only `issues`, but step 11 now renders
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
    for i in issues:
        sev[i["severity"]] += 1
    data = {
        "pr_id": "42",
        "reviewer": "reconciliator",
        "timestamp": "2026-08-13T10:00:00",
        "plugin_version": None,
        "schema": 1,
        # Lowercase: this is the per-review ledger vocabulary
        # (schemas/review-output.ts), not the outer-pipeline
        # APPROVE/COMMENT/REQUEST_CHANGES values pipeline-result.json
        # publishes. Step 11 maps between the two layers.
        "verdict": "request_changes",
        "summary": {"total_issues": len(issues), "by_severity": sev},
        "issues": issues,
        "unreviewed": None,
        "deferred_reviewed": [],
        "observations": None,
        "recommendations": None,
        "positive_observations": None,
        "clearances": None,
        "narrative_summary": None,
        "meta": {
            "files_reviewed": 1,
            "unreviewed_autofilled": None,
            "review_duration_ms": 10,
            "confidence_score": 0.9,
            "tool_results_used": None,
        },
    }
    data.update(extra)
    write_findings(str(output_dir), data)
    return data


def _write_adjustments(output_dir, adjustments):
    (Path(output_dir) / "decision-critic-adjustments.json").write_text(
        json.dumps({"schema": 1, "adjustments": adjustments})
    )


def _write_critic_verdict(output_dir, verdict):
    (Path(output_dir) / "decision-critic-verdict.json").write_text(
        json.dumps({"verdict": verdict})
    )


def _applied_ids(findings):
    """The ids out of `applied_critic_adjustments`, whose entries are
    records (`{"adjustment_id": ..., "spot_check": ...}`) rather than bare
    strings — the id half is the idempotence bookkeeping, the spot_check
    half is the orchestrator's outcome for that decision."""
    return [record["adjustment_id"] for record in findings[APPLIED_IDS_KEY]]


def _issue(id_, severity="low"):
    return {"id": id_, "severity": severity, "title": "t", "file": "f.go",
            "line": 10, "description": "d", "recommendation": "r",
            "category": "general", "confidence": 0.9}


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
        _write_findings(tmp_path, [_issue("aaaa1111")])
        result = apply_adjustments(str(tmp_path))
        assert result == {"status": "no_adjustments", "applied": 0}

    def test_promote_patches_severity_with_provenance(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "medium"},
            "rationale": "affects future strategy authors",
        }])
        result = apply_adjustments(str(tmp_path))
        assert result["applied"] == 1
        data = json.loads((tmp_path / "review-findings.json").read_text())
        issue = data["issues"][0]
        assert issue["severity"] == "medium"
        assert issue["critic_adjustment"]["action"] == "promote"
        assert issue["critic_adjustment"]["prior"] == {"severity": "low"}
        assert data["summary"]["by_severity"]["medium"] == 1
        assert data["summary"]["by_severity"]["low"] == 0

    def test_add_appends_full_issue_with_generated_id(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111")])
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
        assert data["summary"]["total_issues"] == 2
        added = data["issues"][1]
        assert len(added["id"]) == 8
        assert added["critic_adjustment"]["action"] == "add"

    def test_remove_moves_issue_out_with_provenance(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111"), _issue("bbbb2222")])
        _write_adjustments(tmp_path, [{
            "action": "remove", "id": "bbbb2222",
            "fields": {}, "rationale": "false positive — refuted by source",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert [i["id"] for i in data["issues"]] == ["aaaa1111"]
        assert data["removed_by_critic"][0]["id"] == "bbbb2222"
        assert data["summary"]["total_issues"] == 1

    def test_unknown_id_fails_loudly_and_writes_nothing(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111")])
        _write_adjustments(tmp_path, [
            {"action": "promote", "id": "aaaa1111",
             "fields": {"severity": "high"}, "rationale": "r"},
            {"action": "promote", "id": "zzzz9999",
             "fields": {"severity": "high"}, "rationale": "r"},
        ])
        with pytest.raises(ValueError, match="zzzz9999"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low"  # entry 1 NOT applied either

    def test_invalid_action_and_field_rejected(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111")])
        _write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "aaaa1111",
            "fields": {}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="obliterate"):
            apply_adjustments(str(tmp_path))
        _write_adjustments(tmp_path, [{
            "action": "correct", "id": "aaaa1111",
            "fields": {"verdict": "APPROVE"}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="verdict"):
            apply_adjustments(str(tmp_path))

    def test_rejected_entries_are_skipped(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"},
            "rationale": "r", "rejected": True,
            "rejection_reason": "spot-check refuted the claim",
        }])
        result = apply_adjustments(str(tmp_path))
        assert result["applied"] == 0
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low"

    def test_second_run_is_idempotent(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
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
        incremental counter drifts from the issue list it claims to describe.
        """
        _write_findings(tmp_path, [
            _issue("aaaa1111", "low"),
            _issue("bbbb2222", "high"),
            _issue("cccc3333", "medium"),
        ])
        _write_adjustments(tmp_path, [
            {"action": "promote", "id": "aaaa1111",
             "fields": {"severity": "high"}, "rationale": "wider blast radius"},
            {"action": "remove", "id": "cccc3333",
             "fields": {}, "rationale": "refuted by source"},
            {"action": "add", "id": None,
             "fields": {"severity": "critical", "title": "unbounded retry",
                        "file": "internal/queue/retry.go",
                        "description": "no ceiling on attempts",
                        "recommendation": "cap attempts"},
             "rationale": "critic found it independently"},
        ])
        result = apply_adjustments(str(tmp_path))
        assert result == {"status": "applied", "applied": 3}

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["summary"]["total_issues"] == 3
        assert len(data["issues"]) == 3
        assert data["summary"]["by_severity"] == {
            "critical": 1, "high": 2, "medium": 0, "low": 0, "info": 0,
        }
        assert data["summary"]["total_issues"] == len(data["issues"])
        assert [i["id"] for i in data["issues"]][:2] == ["aaaa1111", "bbbb2222"]
        assert data["removed_by_critic"][0]["id"] == "cccc3333"
        # The removed issue is out of the counted population entirely.
        assert "cccc3333" not in {i["id"] for i in data["issues"]}

    def test_add_action_round_trip(self, tmp_path):
        """The `add` action's full solo round trip.

        `promote` has end-to-end coverage in
        TestCriticContextRoundTrip (context render -> critic adjustment
        -> apply -> ledger). `add` never had an equivalent belt-and-braces
        check beyond the mixed-batch assertions above — this pins the
        generated id shape, provenance, and summary recount for an `add`
        landing on its own.
        """
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "add", "id": None,
            "fields": {"severity": "high", "title": "unbounded retry",
                       "file": "internal/queue/retry.go",
                       "description": "no ceiling on attempts",
                       "recommendation": "cap attempts"},
            "rationale": "critic found it independently",
        }])
        result = apply_adjustments(str(tmp_path))
        assert result == {"status": "applied", "applied": 1}

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert len(data["issues"]) == 2
        added = data["issues"][1]
        assert re.fullmatch(r"[0-9a-f]{8}", added["id"]), (
            f"generated id must be 8 lowercase hex chars, got {added['id']!r}"
        )
        assert added["id"] != "aaaa1111"
        assert added["title"] == "unbounded retry"
        assert added["critic_adjustment"] == {
            "action": "add", "rationale": "critic found it independently",
        }
        assert data["summary"]["total_issues"] == 2
        assert data["summary"]["by_severity"] == {
            "critical": 0, "high": 1, "medium": 0, "low": 1, "info": 0,
        }

    def test_add_action_reapply_idempotent(self, tmp_path):
        """A second apply over the same adjustments file must not append
        a second copy of the added finding — the crash-safety contract
        TestCrashSafety pins for `promote`, exercised here for `add`."""
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "add", "id": None,
            "fields": {"severity": "high", "title": "unbounded retry",
                       "file": "internal/queue/retry.go",
                       "description": "no ceiling on attempts",
                       "recommendation": "cap attempts"},
            "rationale": "critic found it independently",
        }])
        first = apply_adjustments(str(tmp_path))
        assert first == {"status": "applied", "applied": 1}
        after_first = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        assert len(after_first["issues"]) == 2

        second = apply_adjustments(str(tmp_path))
        assert second == {"status": "nothing_pending", "applied": 0}
        after_second = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        assert len(after_second["issues"]) == 2, (
            "a re-apply must not append a duplicate finding"
        )
        assert after_second == after_first


class TestRejectionAudit:
    """A rejected critic decision must leave a trace in the artifact
    downstream readers actually consult, not only in
    decision-critic-adjustments.json, which none of them read."""

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def test_rejected_entry_lands_in_the_findings_audit_trail(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"},
            "rationale": "r", "rejected": True,
            "rejection_reason": "spot-check refuted the claim",
        }])
        result = apply_adjustments(str(tmp_path))
        assert result["applied"] == 0  # a rejected entry is never applied
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low"

        records = data[REJECTED_ADJUSTMENTS_KEY]
        assert len(records) == 1
        record = records[0]
        assert record["action"] == "promote"
        assert record["target_id"] == "aaaa1111"
        assert record["rejection_reason"] == "spot-check refuted the claim"
        assert record["adjustment_id"]  # allocated so a re-run can dedupe

    def test_second_run_does_not_duplicate_the_rejection_record(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "info"},
            "rationale": "r", "rejected": True,
            "rejection_reason": "spot-check refuted it",
        }])
        apply_adjustments(str(tmp_path))
        first = json.loads((tmp_path / "review-findings.json").read_text())
        assert len(first[REJECTED_ADJUSTMENTS_KEY]) == 1

        second_result = apply_adjustments(str(tmp_path))
        assert second_result == {"status": "nothing_pending", "applied": 0}
        second = json.loads((tmp_path / "review-findings.json").read_text())
        assert second == first, "an idempotent re-run must not rewrite the ledger"
        assert len(second[REJECTED_ADJUSTMENTS_KEY]) == 1

    def test_a_second_rejected_entry_in_a_later_batch_appends(
        self, tmp_path
    ):
        _write_findings(
            tmp_path, [_issue("aaaa1111", "low"), _issue("bbbb2222", "low")]
        )
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "r",
            "rejected": True, "rejection_reason": "first round refutation",
        }])
        apply_adjustments(str(tmp_path))
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "bbbb2222",
            "fields": {"severity": "info"}, "rationale": "r",
            "rejected": True, "rejection_reason": "second round refutation",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        records = data[REJECTED_ADJUSTMENTS_KEY]
        assert len(records) == 2
        assert {r["target_id"] for r in records} == {"aaaa1111", "bbbb2222"}
        assert {r["rejection_reason"] for r in records} == {
            "first round refutation", "second round refutation",
        }

    def test_a_purely_rejected_batch_is_reported_as_nothing_pending(
        self, tmp_path
    ):
        """The rejection audit write is real, but it is not an 'apply':
        `result['status']` describes whether findings were mutated, and a
        rejection never mutates `issues`."""
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "r",
            "rejected": True, "rejection_reason": "refuted",
        }])
        result = apply_adjustments(str(tmp_path))
        assert result == {"status": "nothing_pending", "applied": 0}

    def test_mixed_batch_applies_one_and_audits_the_other(self, tmp_path):
        _write_findings(
            tmp_path, [_issue("aaaa1111", "low"), _issue("bbbb2222", "low")]
        )
        _write_adjustments(tmp_path, [
            {"action": "promote", "id": "aaaa1111",
             "fields": {"severity": "high"}, "rationale": "r"},
            {"action": "demote", "id": "bbbb2222",
             "fields": {"severity": "info"}, "rationale": "r",
             "rejected": True, "rejection_reason": "refuted"},
        ])
        result = apply_adjustments(str(tmp_path))
        assert result == {"status": "applied", "applied": 1}
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "high"
        assert data["issues"][1]["severity"] == "low"  # rejected, untouched
        records = data[REJECTED_ADJUSTMENTS_KEY]
        assert len(records) == 1
        assert records[0]["target_id"] == "bbbb2222"

    @pytest.mark.parametrize("bad_reason", [None, "", "   "])
    def test_missing_or_blank_rejection_reason_refuses_the_whole_batch(
        self, tmp_path, bad_reason
    ):
        """rejection_reason is the entire payload of the audit record —
        a rejected entry without one is refused loudly, the same
        all-or-nothing style an unknown action or invalid severity gets,
        instead of silently writing an empty string into the ledger."""
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        entry = {
            "action": "promote", "id": "aaaa1111",
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

    def test_applied_and_rejected_on_the_same_entry_keeps_the_applied_record_only(
        self, tmp_path
    ):
        """A hand edit that adds `rejected: true` to an already-applied
        entry is tampering with decision-critic-adjustments.json, not a
        second decision — the applied mutation is ground truth, and
        auditing the coexisting rejected flag would publish two
        contradictory outcomes for one adjustment_id."""
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))
        after_apply = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        assert after_apply["issues"][0]["severity"] == "high"
        assert REJECTED_ADJUSTMENTS_KEY not in after_apply

        adj_path = tmp_path / "decision-critic-adjustments.json"
        doc = json.loads(adj_path.read_text())
        doc["adjustments"][0]["rejected"] = True
        doc["adjustments"][0]["rejection_reason"] = "hand-edited after apply"
        adj_path.write_text(json.dumps(doc))

        result = apply_adjustments(str(tmp_path))
        assert result == {"status": "nothing_pending", "applied": 0}
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
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
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
        assert data["issues"][0]["critic_adjustment"]["prior"] == {
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
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))
        assert sorted(p.name for p in tmp_path.iterdir()) == expected

        _write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "aaaa1111",
            "fields": {}, "rationale": "r",
        }])
        with pytest.raises(ValueError):
            apply_adjustments(str(tmp_path))
        assert sorted(p.name for p in tmp_path.iterdir()) == expected

    def test_duplicate_adjustment_ids_are_rejected(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111"), _issue("bbbb2222")])
        _write_adjustments(tmp_path, [
            {"adjustment_id": "dup", "action": "promote", "id": "aaaa1111",
             "fields": {"severity": "high"}, "rationale": "r"},
            {"adjustment_id": "dup", "action": "promote", "id": "bbbb2222",
             "fields": {"severity": "high"}, "rationale": "r"},
        ])
        with pytest.raises(ValueError, match="duplicate adjustment_id"):
            apply_adjustments(str(tmp_path))


class TestBatchCoherence:
    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def test_duplicate_target_in_one_batch_is_rejected(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [
            {"action": "promote", "id": "aaaa1111",
             "fields": {"severity": "high"}, "rationale": "r"},
            {"action": "correct", "id": "aaaa1111",
             "fields": {"title": "clearer title"}, "rationale": "r"},
        ])
        with pytest.raises(ValueError, match="duplicate target"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low"
        assert "critic_adjustment" not in data["issues"][0]

    def test_targeting_an_id_removed_earlier_in_the_batch_is_rejected(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_issue("aaaa1111"), _issue("bbbb2222")])
        _write_adjustments(tmp_path, [
            {"action": "remove", "id": "bbbb2222",
             "fields": {}, "rationale": "false positive"},
            {"action": "promote", "id": "bbbb2222",
             "fields": {"severity": "high"}, "rationale": "r"},
        ])
        with pytest.raises(ValueError, match="removed by adjustment\\[0\\]"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert [i["id"] for i in data["issues"]] == ["aaaa1111", "bbbb2222"]

    def test_entry_without_an_id_fails_as_unknown_id(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "fields": {"severity": "high"},
            "rationale": "r",
        }])
        with pytest.raises(ValueError, match="no issue with id None"):
            apply_adjustments(str(tmp_path))

    def test_findings_issue_without_an_id_is_not_addressable(self, tmp_path):
        idless = _issue("aaaa1111")
        del idless["id"]
        _write_findings(tmp_path, [idless])
        _write_adjustments(tmp_path, [{
            "action": "promote", "fields": {"severity": "high"},
            "rationale": "r",
        }])
        # A None target must not silently match an id-less issue.
        with pytest.raises(ValueError, match="no issue with id None"):
            apply_adjustments(str(tmp_path))

    def test_add_rejects_a_critic_supplied_id_in_both_spellings(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_issue("aaaa1111")])
        base_fields = {"severity": "low", "title": "t", "file": "f.go",
                       "description": "d", "recommendation": "r"}
        _write_adjustments(tmp_path, [{
            "action": "add", "id": "cccc3333",
            "fields": dict(base_fields), "rationale": "r",
        }])
        with pytest.raises(ValueError, match="ids are generated"):
            apply_adjustments(str(tmp_path))

        _write_adjustments(tmp_path, [{
            "action": "add", "id": None,
            "fields": {**base_fields, "id": "cccc3333"}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="'id' is not adjustable"):
            apply_adjustments(str(tmp_path))

    def test_malformed_ledger_severity_fails_instead_of_undercounting(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        raw = json.loads((tmp_path / "review-findings.json").read_text())
        raw["issues"].append({**_issue("bbbb2222"), "severity": "blocker"})
        (tmp_path / "review-findings.json").write_text(json.dumps(raw))
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="bbbb2222"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low"  # nothing written

    @pytest.mark.parametrize("shape", [[{"id": "aaaa1111"}], "findings", 7])
    def test_findings_that_is_not_an_object_fails_as_a_value_error(
        self, tmp_path, shape
    ):
        """The adjustments file is shape-guarded; the findings file was
        not, so a non-object ledger died on an AttributeError outside this
        module's ValueError contract — the one step 11 catches."""
        (tmp_path / "review-findings.json").write_text(json.dumps(shape))
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
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
        assert "adjustment_id" not in doc["adjustments"][0]  # nothing written


class TestValidateAdjustments:
    """Direct unit coverage for the shared batch-shape validator extracted
    from apply_adjustments(). Every OTHER exercise of this logic goes
    through apply_adjustments() (which only ever raises `problems[0]`) or
    through critic.py's save tests (which each trigger exactly one
    problem) — neither pins the load-bearing "collect every problem,
    don't stop at the first" property, so it must be pinned here, calling
    validate_adjustments() directly."""

    def test_valid_batch_returns_no_problems(self):
        assert validate_adjustments({
            "schema": 1,
            "adjustments": [{
                "action": "promote", "id": "aaaa1111",
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        }) == []

    def test_non_object_payload_is_a_problem(self):
        assert validate_adjustments([1, 2, 3]) == [
            "decision-critic-adjustments.json must be a JSON object"
        ]

    def test_wrong_schema_is_a_problem(self):
        problems = validate_adjustments({"schema": 2, "adjustments": []})
        assert len(problems) == 1
        assert "'schema' must be 1" in problems[0]

    def test_adjustments_not_a_list_is_a_problem(self):
        assert validate_adjustments({"schema": 1, "adjustments": "nope"}) == [
            "decision-critic-adjustments.json: 'adjustments' must be a list"
        ]

    def test_missing_adjustments_key_is_a_problem(self):
        assert validate_adjustments({"schema": 1}) == [
            "decision-critic-adjustments.json: 'adjustments' must be a list"
        ]

    def test_entry_not_an_object_is_a_problem(self):
        assert validate_adjustments({
            "schema": 1, "adjustments": ["not-a-dict"],
        }) == ["adjustment[0] must be an object"]

    def test_duplicate_adjustment_id_is_a_problem(self):
        problems = validate_adjustments({
            "schema": 1,
            "adjustments": [
                {"adjustment_id": "dup", "action": "promote", "id": "aaaa1111",
                 "fields": {"severity": "high"}, "rationale": "r"},
                {"adjustment_id": "dup", "action": "promote", "id": "bbbb2222",
                 "fields": {"severity": "high"}, "rationale": "r"},
            ],
        })
        assert any("duplicate adjustment_id" in p for p in problems)

    def test_blank_adjustment_id_is_a_problem(self):
        problems = validate_adjustments({
            "schema": 1,
            "adjustments": [{
                "adjustment_id": "", "action": "promote", "id": "aaaa1111",
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        })
        assert any(
            "'adjustment_id' must be a non-empty string" in p for p in problems
        )

    def test_unknown_action_is_a_problem(self):
        problems = validate_adjustments({
            "schema": 1,
            "adjustments": [{
                "action": "obliterate", "id": "aaaa1111",
                "fields": {}, "rationale": "r",
            }],
        })
        assert any("unknown action" in p and "obliterate" in p for p in problems)

    def test_invalid_field_is_a_problem(self):
        problems = validate_adjustments({
            "schema": 1,
            "adjustments": [{
                "action": "correct", "id": "aaaa1111",
                "fields": {"verdict": "APPROVE"}, "rationale": "r",
            }],
        })
        assert any("not adjustable" in p for p in problems)

    def test_add_missing_required_fields_is_a_problem(self):
        problems = validate_adjustments({
            "schema": 1,
            "adjustments": [{
                "action": "add", "id": None,
                "fields": {"severity": "low"}, "rationale": "r",
            }],
        })
        assert any("add requires fields" in p for p in problems)

    def test_add_with_a_critic_supplied_id_is_a_problem(self):
        problems = validate_adjustments({
            "schema": 1,
            "adjustments": [{
                "action": "add", "id": "cccc3333",
                "fields": {"severity": "low", "title": "t", "file": "f.go",
                           "description": "d", "recommendation": "r"},
                "rationale": "r",
            }],
        })
        assert any("ids are generated" in p for p in problems)

    def test_two_independent_problems_are_both_reported(self):
        """The load-bearing property under test: validate_adjustments()
        collects every problem instead of stopping at the first one it
        finds. apply_adjustments() only ever surfaces `problems[0]`, so
        this can only be pinned by calling the validator directly."""
        problems = validate_adjustments({
            "schema": 1,
            "adjustments": [
                {"action": "obliterate", "id": "aaaa1111",
                 "fields": {}, "rationale": "r"},
                {"action": "add", "id": "cccc3333",
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
        (Path(output_dir) / "decision-critic-adjustments.json").write_text(
            json.dumps(doc)
        )

    def test_schema_1_proceeds(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        self._write_raw_adjustments(tmp_path, {
            "schema": 1,
            "adjustments": [{
                "action": "promote", "id": "aaaa1111",
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        })
        result = apply_adjustments(str(tmp_path))
        assert result == {"status": "applied", "applied": 1}

    def test_schema_2_refuses_the_whole_batch(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        self._write_raw_adjustments(tmp_path, {
            "schema": 2,
            "adjustments": [{
                "action": "promote", "id": "aaaa1111",
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        })
        with pytest.raises(ValueError, match="'schema' must be 1"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low"  # nothing written

    def test_missing_schema_refuses_with_the_same_message_shape(
        self, tmp_path
    ):
        """The taught template always includes `schema`; a doc missing it
        entirely is out-of-template the same way a wrong value is, and
        gets the same refusal rather than being read as version 1 by
        default."""
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        self._write_raw_adjustments(tmp_path, {
            "adjustments": [{
                "action": "promote", "id": "aaaa1111",
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        })
        with pytest.raises(ValueError, match="'schema' must be 1"):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low"  # nothing written

    def test_schema_as_a_string_refuses(self, tmp_path):
        """`"1"` is not `1` — no type coercion for the schema gate."""
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        self._write_raw_adjustments(tmp_path, {
            "schema": "1",
            "adjustments": [{
                "action": "promote", "id": "aaaa1111",
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        })
        with pytest.raises(ValueError, match="'schema' must be 1"):
            apply_adjustments(str(tmp_path))

    @pytest.mark.parametrize("shape", [[{"id": "aaaa1111"}], "hello", 5])
    def test_non_object_doc_fails_as_a_shape_error_not_a_schema_error(
        self, tmp_path, shape
    ):
        """[], "hello", and 5 are all valid JSON but not a document with a
        'schema' field to be wrong about — the diagnosis must name the
        actual defect (not a JSON object), the same distinction
        read_findings_file() draws for the findings ledger twenty lines
        away, rather than misreporting it as a missing/invalid schema."""
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        (tmp_path / "decision-critic-adjustments.json").write_text(
            json.dumps(shape)
        )
        with pytest.raises(
            ValueError, match="decision-critic-adjustments.json must be a JSON object"
        ):
            apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low"  # nothing written


class TestScopeLinePairing:
    """schemas/review-output.ts:36-37 and output.py's renderer treat
    scope/line as a pair; a patch must never split them."""

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def test_add_without_a_line_is_marked_file_scoped(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111")])
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
        )["issues"][1]
        assert added["line"] is None
        assert added["scope"] == "file"

    def test_add_with_a_line_carries_no_scope_marker(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111")])
        _write_adjustments(tmp_path, [{
            "action": "add", "id": None,
            "fields": {"severity": "low", "title": "t", "file": "f.go",
                       "description": "d", "recommendation": "r", "line": 42},
            "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))
        added = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )["issues"][1]
        assert added["line"] == 42
        assert "scope" not in added

    def test_rescope_to_a_line_drops_the_stale_file_marker(self, tmp_path):
        file_scoped = {**_issue("aaaa1111"), "line": None, "scope": "file"}
        _write_findings(tmp_path, [file_scoped])
        _write_adjustments(tmp_path, [{
            "action": "rescope", "id": "aaaa1111",
            "fields": {"line": 88}, "rationale": "pinned to the call site",
        }])
        apply_adjustments(str(tmp_path))
        issue = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )["issues"][0]
        assert issue["line"] == 88
        assert "scope" not in issue

    def test_rescope_to_no_line_marks_the_issue_file_scoped(self, tmp_path):
        line_anchored = {**_issue("aaaa1111"), "line": 12}
        _write_findings(tmp_path, [line_anchored])
        _write_adjustments(tmp_path, [{
            "action": "rescope", "id": "aaaa1111",
            "fields": {"line": None}, "rationale": "the whole file drifted",
        }])
        apply_adjustments(str(tmp_path))
        issue = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )["issues"][0]
        assert issue["line"] is None
        assert issue["scope"] == "file"

    def test_a_patch_that_leaves_line_alone_leaves_scope_alone(self, tmp_path):
        file_scoped = {**_issue("aaaa1111"), "line": None, "scope": "file"}
        _write_findings(tmp_path, [file_scoped])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))
        issue = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )["issues"][0]
        assert issue["scope"] == "file"
        assert issue["line"] is None

    @pytest.mark.parametrize("bad_line", ["88", True, 0, -5])
    def test_a_line_outside_the_1_indexed_contract_is_rejected(
        self, tmp_path, bad_line
    ):
        """output.py accepts only positive ints for `line`; a patch that
        smuggled 0 or a negative past this guard would publish a finding
        the builder itself would have refused."""
        _write_findings(tmp_path, [_issue("aaaa1111")])
        _write_adjustments(tmp_path, [{
            "action": "rescope", "id": "aaaa1111",
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
            [sys.executable, str(SCRIPT_PATH), "--output-dir", str(output_dir)],
            capture_output=True, text=True, timeout=60,
        )

    def test_cli_applies_and_reports_result_json_on_stdout(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        proc = self._run(tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == {"status": "applied", "applied": 1}
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "medium"

    def test_cli_reports_a_same_batch_remove_then_target_cleanly(
        self, tmp_path
    ):
        """This one used to die on a raw KeyError past validation, which
        the CLI's except tuple does not cover — no ERROR: line at all."""
        _write_findings(tmp_path, [_issue("aaaa1111"), _issue("bbbb2222")])
        _write_adjustments(tmp_path, [
            {"action": "remove", "id": "bbbb2222",
             "fields": {}, "rationale": "false positive"},
            {"action": "correct", "id": "bbbb2222",
             "fields": {"title": "t2"}, "rationale": "r"},
        ])
        proc = self._run(tmp_path)
        assert proc.returncode == 1
        assert proc.stderr.startswith("ERROR:")
        assert "Traceback" not in proc.stderr
        assert "removed by adjustment[0]" in proc.stderr
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert len(data["issues"]) == 2

    def test_cli_fails_loudly_on_invalid_action(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "aaaa1111",
            "fields": {}, "rationale": "r",
        }])
        proc = self._run(tmp_path)
        assert proc.returncode == 1
        assert "ERROR:" in proc.stderr
        assert "obliterate" in proc.stderr
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low"


class TestCriticVerdictGate:
    """The gate lives inside apply_adjustments itself (see module
    docstring), so every caller — CLI, step 11, and any future one —
    shares it. These tests exercise the function directly and via the
    CLI subprocess, deliberately WITHOUT the `revise_verdict` fixture
    the rest of this file relies on, since the gate itself is what's
    under test here."""

    def test_apply_refuses_without_verdict_file(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
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
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
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
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, near_miss)
        before = (tmp_path / "review-findings.json").read_bytes()

        result = apply_adjustments(str(tmp_path))

        assert result["status"] == "refused"
        assert result["reason"].startswith(REFUSAL_VERDICT_NOT_REVISE), (
            f"a near-miss spelling must refuse with the "
            f"{REFUSAL_VERDICT_NOT_REVISE!r} reason, not be silently "
            f"normalized into REVISE"
        )
        assert (tmp_path / "review-findings.json").read_bytes() == before, (
            "a refusal must write nothing, even for a near-miss spelling"
        )

    def test_apply_refuses_on_unparseable_verdict(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
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
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "REVISE")

        result = apply_adjustments(str(tmp_path))

        assert result == {"status": "applied", "applied": 1}
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "critical"

    def test_cli_exit_code_on_refusal(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "STAND")

        proc = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path)],
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
        assert data["issues"][0]["severity"] == "low"


class TestReadCriticVerdict:
    """Unit coverage for the reader apply_adjustments' gate is built on —
    it is deliberately permissive (returns the raw string or None) and
    lets the caller decide what None or an unexpected string means."""

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

    def _seed(self, tmp_path, ledger_verdict, issues=()):
        (tmp_path / "review-report.md").write_text("# report")
        _write_findings(tmp_path, list(issues), verdict=ledger_verdict)

    def _finalize(self, tmp_path, state=None):
        state = {} if state is None else state
        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))
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
        self._seed(tmp_path, "block", [_issue("aaaa1111", "critical")])
        assert self._finalize(tmp_path)["verdict"] == "REQUEST_CHANGES"

    def test_escalate_overrides_the_ledger(self, tmp_path):
        """The critic's one unilateral power: conclusions that did not
        survive the stress test cannot gate a merge."""
        self._seed(tmp_path, "block", [_issue("aaaa1111", "critical")])
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
        ('{"issues": []}', "no verdict key"),
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
        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))
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
        _orchestrate_step_11("pr", {}, {}, {}, str(tmp_path))
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



class TestCriticContextRoundTrip:
    """The full REVISE loop across the two artifacts that must agree.

    Three modules meet here and none of their own tests span the seam:
    reconciliation_context.py renders the critic's ONLY view of the
    findings, the critic keys its adjustments off that view, and
    critic_adjustments.py resolves those keys against review-findings.json.
    While the context rendered F-labels alone, each module passed its own
    tests and the loop was still broken end to end — every REVISE run
    shipped degraded with "no issue with id 'F1'". This test crosses the
    seam by taking its id the way the critic must: out of the rendered
    context, never out of the findings file.
    """

    # Deliberately regex over the RENDERED context: reading the id from
    # the findings dict would test the applier against itself and skip
    # the one hop — render to critic — where the contract broke.
    ID_IN_HEADING = re.compile(r"^### F\d+ \[id: ([^\]]+)\]:", re.MULTILINE)

    def test_an_id_read_from_the_critic_context_applies(self, tmp_path):
        _write_findings(tmp_path, [_issue("9f3a1c7d", "low")])
        findings = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        context = build_critic_context("# review report", findings)
        (tmp_path / "critic-context.md").write_text(context)

        # The critic's view is the context document, so the key it can use
        # is whatever that document shows it.
        visible_ids = self.ID_IN_HEADING.findall(context)
        assert visible_ids, (
            "the critic context shows no ledger id — a critic reading it "
            "has no key it can put in an adjustment"
        )

        _write_adjustments(tmp_path, [{
            "action": "promote", "id": visible_ids[0],
            "fields": {"severity": "high"},
            "rationale": "the exploit path is reachable from the REST route",
        }])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")

        _orchestrate_step_11("pr", {}, {}, {}, str(tmp_path))

        data = json.loads((tmp_path / "review-findings.json").read_text())
        issue = data["issues"][0]
        assert issue["severity"] == "high", (
            "the id the critic could see did not resolve in the ledger"
        )
        assert issue["critic_adjustment"]["action"] == "promote"
        assert data["summary"]["by_severity"]["high"] == 1
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []
        assert result["status"] == "success"

    def test_the_visible_id_is_the_ledger_id(self, tmp_path):
        """Not just parseable — the same string the findings file stores."""
        _write_findings(tmp_path, [_issue("9f3a1c7d"), _issue("0badf00d")])
        findings = json.loads((tmp_path / "review-findings.json").read_text())
        context = build_critic_context("# review report", findings)
        assert self.ID_IN_HEADING.findall(context) == [
            issue["id"] for issue in findings["issues"]
        ]


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
        return _orchestrate_step_11("pr", {}, {}, {}, str(output_dir))

    def test_pending_adjustments_applied_before_the_verdict_is_derived(
        self, tmp_path
    ):
        """Ordering is now load-bearing in one direction only: the apply
        recomputes the ledger verdict, and finalize READS that verdict a few
        lines later. Reading first would publish the pre-batch verdict."""
        _write_findings(tmp_path, [_issue("aaaa1111", "low")],
                        verdict="approve")
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "critical"
        assert data["verdict"] == "block"
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["verdict"] == "REQUEST_CHANGES"
        assert result["verdict_source"] == "findings ledger"

    def test_invalid_adjustments_degrade_instead_of_crashing(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "aaaa1111",
            "fields": {}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any("critic adjustments not applied" in n
                   for n in result["degradation_notes"])
        # The note must reach `status` too — appended after the status is
        # computed, it would publish a "success" run carrying a degradation.
        assert result["status"] == "degraded"
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low"  # nothing half-applied

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
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
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
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "STAND")
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low", "nothing may be applied"
        assert "critic_adjustment" not in data["issues"][0]
        assert APPLIED_IDS_KEY not in data

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any("REVISE-only channel" in n
                   for n in result["degradation_notes"]), (
            "a pending file under a non-REVISE verdict must be surfaced, "
            "not silently dropped"
        )
        assert result["status"] == "degraded"

    def test_missing_critic_verdict_never_applies_pending_adjustments(
        self, tmp_path
    ):
        """No verdict file is not an implicit REVISE."""
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "low"
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any("critic verdict is unavailable" in n
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
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [
            {"action": "promote", "id": "aaaa1111",
             "fields": {"severity": "medium"}, "rationale": "r",
             "applied": True},
            {"action": "remove", "id": "aaaa1111", "rationale": "r",
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
        _write_findings(tmp_path, [_issue("aaaa1111", "medium")])
        findings_path = tmp_path / "review-findings.json"
        data = json.loads(findings_path.read_text())
        data[APPLIED_IDS_KEY] = ["deadbeef"]
        # Through the sanctioned writer: the state being simulated is a
        # crash between apply_adjustments' two writes, where the FINDINGS
        # write (in channel) landed and only the flag write was lost. A
        # raw rewrite here would simulate a hand edit instead.
        write_findings(str(tmp_path), data)
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"}, "rationale": "r",
            "adjustment_id": "deadbeef",
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
            json.dumps([_issue("aaaa1111", "low")])
        )
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any("critic adjustments not applied" in n
                   for n in result["degradation_notes"])
        assert result["status"] == "degraded"


class TestStepElevenRerendersFindingsMarkdown:
    """`review-findings.md` must describe the FINAL ledger, not the one the
    reconciliator first published.

    Field-proven defect: after every critic REVISE, the hand-written
    narrative still showed pre-adjustment severities while the JSON and the
    report showed post-adjustment ones — a guaranteed-stale fallback
    artifact, and the one the step-10 critic fallback and the failure-path
    report fallback both point at.
    """

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def _step_11(self, output_dir):
        return _orchestrate_step_11("pr", {}, {}, {}, str(output_dir))

    def _seed(self, tmp_path, severity="high"):
        issue = _issue("aaaa1111", severity)
        issue["title"] = "Unescaped output"
        _write_findings(tmp_path, [issue])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")

    def test_demoted_severity_reaches_the_markdown(self, tmp_path):
        """THE pin: a REVISE demote must be visible in the rendered file."""
        self._seed(tmp_path, severity="high")
        (tmp_path / "review-findings.md").write_text(
            "## High Issues\n\n### Unescaped output\n"
        )
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])

        self._step_11(tmp_path)

        rendered = (tmp_path / "review-findings.md").read_text()
        assert "## Low Issues" in rendered
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

    def test_rendered_markdown_serves_the_report_fallback(self, tmp_path):
        """Step 10's critic fallback and finalize's report fallback both
        point at review-findings.md — now truthful, because the script
        renders it here even when report synthesis never happened."""
        self._seed(tmp_path, severity="low")
        (tmp_path / "review-report.md").unlink()
        _write_critic_verdict(tmp_path, "STAND")

        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["report_path"] == str(tmp_path / "review-findings.md")


class TestNarrativeSummaryInvalidation:
    """Prose that summarizes a mutable ledger cannot be corrected, only
    withdrawn.

    The critic's vocabulary reaches every field of every issue, but
    `narrative_summary` is ledger-level prose no adjustment can address. A
    demoted critical still described as "one CRITICAL blocker" survives the
    whole correction pipeline and renders directly above the list that
    contradicts it. The pipeline cannot re-derive the prose (it is LLM
    output), so an applying batch withdraws it — auditably.
    """

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    _SUMMARY = "One CRITICAL blocker: the payment path is unescaped."

    def _seed(self, tmp_path, severity="critical"):
        _write_findings(
            tmp_path, [_issue("aaaa1111", severity)],
            narrative_summary=self._SUMMARY,
        )

    def test_an_applying_batch_withdraws_the_summary(self, tmp_path):
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        result = apply_adjustments(tmp_path)
        assert result["applied"] == 1
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["narrative_summary"] is None

    def test_the_withdrawn_text_stays_auditable(self, tmp_path):
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        apply_adjustments(tmp_path)
        data = json.loads((tmp_path / "review-findings.json").read_text())
        withdrawn = data[WITHDRAWN_SUMMARY_KEY]
        assert len(withdrawn) == 1
        assert withdrawn[0]["text"] == self._SUMMARY
        # Tied to the exact decisions that caused it, the same way each
        # touched finding names the action that touched it.
        assert withdrawn[0]["withdrawn_by"] == _applied_ids(data)

    def test_a_second_withdrawal_names_only_its_own_batch(self, tmp_path):
        """withdrawn_by is causal attribution, not history: a second
        reconciliation round's withdrawal must name the batch that caused
        it, never the cumulative applied-ids list."""
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "first round",
        }])
        apply_adjustments(tmp_path)
        findings_path = tmp_path / "review-findings.json"
        data = json.loads(findings_path.read_text())
        first_batch = _applied_ids(data)
        # Simulate a re-reconciliation writing fresh prose.
        data["narrative_summary"] = "Fresh assessment after round two."
        findings_path.write_text(json.dumps(data))
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "medium"}, "rationale": "second round",
        }])
        apply_adjustments(tmp_path)
        data = json.loads(findings_path.read_text())
        withdrawn = data[WITHDRAWN_SUMMARY_KEY]
        assert len(withdrawn) == 2
        second_batch = [
            i for i in _applied_ids(data) if i not in first_batch
        ]
        assert second_batch
        assert withdrawn[1]["withdrawn_by"] == second_batch
        assert withdrawn[0]["withdrawn_by"] == first_batch

    def test_a_batch_that_applies_nothing_leaves_the_summary_alone(
        self, tmp_path
    ):
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "r",
            "rejected": True, "rejection_reason": "spot-check refuted it",
        }])
        result = apply_adjustments(tmp_path)
        assert result["applied"] == 0
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["narrative_summary"] == self._SUMMARY
        assert WITHDRAWN_SUMMARY_KEY not in data

    def test_a_refused_call_leaves_the_summary_alone(self, tmp_path):
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "STAND")
        assert apply_adjustments(tmp_path)["status"] == "refused"
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["narrative_summary"] == self._SUMMARY

    def test_no_summary_to_withdraw_records_no_withdrawal(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "critical")])
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "r",
        }])
        apply_adjustments(tmp_path)
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["narrative_summary"] is None
        assert WITHDRAWN_SUMMARY_KEY not in data

    def test_a_second_batch_appends_rather_than_overwrites(self, tmp_path):
        """Two rounds of adjustments are two withdrawals — the first must
        not be erased by the second."""
        self._seed(tmp_path)
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "r",
        }])
        apply_adjustments(tmp_path)
        # A second reconciliation pass writes fresh prose, then a second
        # critic round adjusts again.
        data = json.loads((tmp_path / "review-findings.json").read_text())
        data["narrative_summary"] = "Second assessment."
        atomic_write_json(str(tmp_path / "review-findings.json"), data)
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "r2",
        }])
        apply_adjustments(tmp_path)
        data = json.loads((tmp_path / "review-findings.json").read_text())
        texts = [entry["text"] for entry in data[WITHDRAWN_SUMMARY_KEY]]
        assert texts == [self._SUMMARY, "Second assessment."]


class TestStepElevenWithdrawsContradictedProse:
    """The reproduced defect, end to end.

    A critical finding described in the Assessment, demoted by the critic:
    the rendered Markdown used to print the demotion in its issue list and
    the stale "one CRITICAL blocker" claim directly above it.
    """

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def test_demoted_finding_is_not_still_described_as_critical(
        self, tmp_path
    ):
        issue = _issue("aaaa1111", "critical")
        issue["title"] = "Unescaped payment path"
        _write_findings(
            tmp_path, [issue],
            narrative_summary=(
                "One CRITICAL blocker: the payment path is unescaped."
            ),
        )
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")

        _orchestrate_step_11("pr", {}, {}, {}, str(tmp_path))

        rendered = (tmp_path / "review-findings.md").read_text()
        assert "## Low Issues" in rendered
        assert "CRITICAL blocker" not in rendered
        assert "withdrawn" in rendered.lower()


class TestClearancePassthrough:
    """The ledger's `clearances` must survive every writer after the
    reconciliator, or "what held" cannot be reported from the artifact.

    The field run only ever carried `clearances: null`, so a write path
    that quietly drops unknown-to-it keys would have looked identical.
    """

    CLEARANCES = [
        {
            "claim": "No caller depends on the removed `legacy_hook` filter",
            "method": "git grep -n legacy_hook across the repo + "
                      "enumerated every add_filter site",
            "evidence": "per security-reviewer, wp-architecture-reviewer — "
                        "0 in-tree consumers",
        },
    ]

    def test_apply_adjustments_preserves_clearances(
        self, tmp_path, revise_verdict
    ):
        _write_findings(
            tmp_path, [_issue("F1")], clearances=self.CLEARANCES
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
        assert after["clearances"] == self.CLEARANCES

    def test_write_findings_does_not_filter_unknown_keys(self, tmp_path):
        """`write_findings` is a whole-document replace, not a projection —
        it has no field vocabulary of its own to fall out of date."""
        payload = {"issues": [], "clearances": self.CLEARANCES,
                   "a_future_key": {"kept": True}}
        write_findings(str(tmp_path), payload)
        assert json.loads(
            (tmp_path / "review-findings.json").read_text()
        ) == payload

    def test_rendered_markdown_carries_the_clearances_section(self, tmp_path):
        """End of the chain: the renderer the report is told to quote."""
        _write_findings(tmp_path, [_issue("F1")], clearances=self.CLEARANCES)
        script = PLUGIN_ROOT / "scripts" / "review" / "agent" / "output.py"
        result = subprocess.run(
            [sys.executable, str(script), "render",
             str(tmp_path / "review-findings.json")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "## Clearances (verified absences)" in result.stdout
        assert "legacy_hook" in result.stdout
        assert "per security-reviewer, wp-architecture-reviewer" in result.stdout


class TestReconciliatorClearancePin:
    """Writer #1 is an agent following a Markdown snippet, so only a test
    can hold it to teaching `add_clearance`.

    Before this, the taught template never mentioned it: the ledger's
    `clearances` was always null, and step 9 rebuilt "what was verified and
    held" from the orchestrator's memory — the exact from-memory reporting
    the artifact chain exists to prevent.
    """

    SNIPPET = PLUGIN_ROOT / "agents" / "review-reconciliator.md"

    def _text(self):
        return self.SNIPPET.read_text(encoding="utf-8")

    def test_the_template_teaches_add_clearance(self):
        text = self._text()
        assert "builder.add_clearance(" in text
        for kwarg in ("claim=", "method=", "evidence="):
            assert kwarg in text.split("builder.add_clearance(", 1)[1][:400]

    def test_the_template_excludes_void_and_correlated_clearances(self):
        text = self._text()
        taught = text.split("builder.add_clearance(", 1)[0]
        assert "Do NOT record" in taught
        assert "VOID" in taught
        assert "method-correlated duplicates" in taught

    def test_the_structured_home_table_lists_clearances(self):
        assert (
            "| `add_clearance(...)` → `## Clearances (verified absences)` |"
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
        clearances get recorded used to be defined only for clearances
        that CONTRADICT a finding.

        Read literally, that made the common case — a clearance nothing
        argues with — ineligible for recording, silently reverting the
        whole feature, and left a bad-method clearance that contradicts
        nothing with no void path at all.
        """
        rules = self._weighting_rules(self._text())
        judgment = rules[4]

        # The judgment rule is stated for every clearance...
        assert "EVERY clearance" in judgment
        assert "conflict or no conflict" in judgment
        # ...and says so where a reader would otherwise assume otherwise.
        assert "even when no finding contradicts it" in judgment
        # ...and the conflict case is explicitly the special case on top.
        assert "special case on top of rule 4" in rules[5]

    def test_recording_does_not_live_only_inside_the_conflict_rule(self):
        """`add_clearance` must be reachable from the universal judgment,
        not only from the rule about contested clearances."""
        rules = self._weighting_rules(self._text())
        assert "add_clearance()" in rules[4]
        assert "RECORDED" in rules[4]
        # The conflict rule may reference the judgment, but must not be
        # the only place recording is authorized.
        assert "add_clearance()" not in rules[5]

    def test_the_template_agrees_that_uncontested_clearances_are_recorded(
        self,
    ):
        taught = self._text().split("builder.add_clearance(", 1)[0]
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
    """`spot_check` is the orchestrator's per-entry verdict on the critic's
    claim, written into the adjustments file rather than left as prose.

    Before it existed, the step-10 briefing asked for per-entry accounting
    and the only place it could land was the human report — so a run whose
    orchestrator skipped the probe published a batch nothing could tell
    apart from one that was checked entry by entry.

    Deliberately NEVER required at apply: step 11's defensive re-run exists
    for orchestrators that crashed before doing the step-10 work, and
    requiring the key there would turn the honest default into a hard
    failure for exactly the runs the re-run is meant to converge.
    """

    def _batch(self, **entry_extra):
        entry = {
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }
        entry.update(entry_extra)
        return {"schema": 1, "adjustments": [entry]}

    @pytest.mark.parametrize("value", ["verified", "refuted", "not_checked"])
    def test_each_vocabulary_value_is_accepted(self, value):
        assert validate_adjustments(self._batch(spot_check=value)) == []

    def test_an_absent_spot_check_is_not_a_problem(self):
        assert validate_adjustments(self._batch()) == []

    @pytest.mark.parametrize("value", [
        "checked", "VERIFIED", "not checked", "", True, 1, None,
    ])
    def test_an_unknown_value_rejects_the_batch(self, value):
        problems = validate_adjustments(self._batch(spot_check=value))
        assert problems and "spot_check" in problems[0]

    def test_a_rejected_entry_may_not_claim_verified(self):
        problems = validate_adjustments(self._batch(
            rejected=True, rejection_reason="probe refuted it",
            spot_check="verified",
        ))
        assert problems and "refuted" in problems[0]

    def test_a_rejected_entry_marked_refuted_is_accepted(self):
        assert validate_adjustments(self._batch(
            rejected=True, rejection_reason="probe refuted it",
            spot_check="refuted",
        )) == []

    def test_a_rejected_entry_without_a_spot_check_is_accepted(self):
        """`rejected: true` already means refuted; the key is redundant
        there, not mandatory."""
        assert validate_adjustments(self._batch(
            rejected=True, rejection_reason="probe refuted it",
        )) == []

    def test_the_gate_the_critic_saves_through_shares_this_check(self, tmp_path):
        """validate_adjustments is the seam critic.py --save and
        apply_adjustments both run — a rejection here is a rejection on
        both paths, with no second implementation to keep in step."""
        _write_critic_verdict(tmp_path, "REVISE")
        _write_findings(tmp_path, [_issue("aaaa1111", "high")])
        (tmp_path / "decision-critic-adjustments.json").write_text(
            json.dumps(self._batch(spot_check="probably fine"))
        )
        with pytest.raises(ValueError, match="spot_check"):
            apply_adjustments(str(tmp_path))


class TestSpotCheckRecordedInTheLedger:
    """The applied-ids record carries the orchestrator's outcome per id."""

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def _apply(self, tmp_path, **entry_extra):
        _write_findings(tmp_path, [_issue("aaaa1111", "high")])
        entry = {
            "action": "demote", "id": "aaaa1111",
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
        _write_findings(tmp_path, [_issue("aaaa1111", "high")])
        data = json.loads((tmp_path / "review-findings.json").read_text())
        data[APPLIED_IDS_KEY] = ["legacy-id"]
        write_findings(str(tmp_path), data)
        _write_adjustments(tmp_path, [{
            "adjustment_id": "legacy-id", "action": "demote",
            "id": "aaaa1111", "fields": {"severity": "low"},
            "rationale": "already landed",
        }])
        assert apply_adjustments(str(tmp_path))["applied"] == 0
        after = json.loads((tmp_path / "review-findings.json").read_text())
        assert after["issues"][0]["severity"] == "high"

    def test_pending_count_reads_both_record_shapes(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "high")])
        data = json.loads((tmp_path / "review-findings.json").read_text())
        data[APPLIED_IDS_KEY] = [
            "legacy-id", {"adjustment_id": "new-id", "spot_check": "verified"},
        ]
        write_findings(str(tmp_path), data)
        _write_adjustments(tmp_path, [
            {"adjustment_id": "legacy-id", "action": "demote", "id": "aaaa1111",
             "fields": {"severity": "low"}, "rationale": "r"},
            {"adjustment_id": "new-id", "action": "promote", "id": "aaaa1111",
             "fields": {"severity": "critical"}, "rationale": "r"},
        ])
        assert pending_count(str(tmp_path)) == 0


class TestRevisedNarrative:
    """The orchestrator's post-critic assessment, in the channel.

    An applying batch withdraws the reconciler's `narrative_summary` and
    nothing used to replace it, so a REVISE run published a ledger whose
    Assessment section pointed at a report the machine could not read.
    """

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    _SUMMARY = "One CRITICAL blocker: the payment path is unescaped."
    _REVISED = "After spot-checking: the blocker is guarded upstream."

    def _seed(self, tmp_path):
        _write_findings(
            tmp_path, [_issue("aaaa1111", "critical")],
            narrative_summary=self._SUMMARY,
        )

    def _write_doc(self, tmp_path, **doc_extra):
        doc = {"schema": 1, "adjustments": [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }]}
        doc.update(doc_extra)
        (tmp_path / "decision-critic-adjustments.json").write_text(
            json.dumps(doc)
        )

    def test_a_non_string_revised_narrative_rejects_the_batch(self):
        problems = validate_adjustments({
            "schema": 1, "adjustments": [], "revised_narrative": ["a", "b"],
        })
        assert problems and "revised_narrative" in problems[0]

    def test_a_string_revised_narrative_is_accepted(self):
        assert validate_adjustments({
            "schema": 1, "adjustments": [], "revised_narrative": "text",
        }) == []

    def test_it_becomes_the_ledger_assessment(self, tmp_path):
        self._seed(tmp_path)
        self._write_doc(tmp_path, revised_narrative=self._REVISED)
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["narrative_summary"] == self._REVISED

    def test_the_withdrawal_record_survives_the_replacement(self, tmp_path):
        """Replacement is not erasure: the reconciler's retracted words
        stay auditable beside the ids that cost them their standing."""
        self._seed(tmp_path)
        self._write_doc(tmp_path, revised_narrative=self._REVISED)
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data[WITHDRAWN_SUMMARY_KEY][0]["text"] == self._SUMMARY

    def test_a_blank_revised_narrative_leaves_the_summary_withdrawn(
        self, tmp_path
    ):
        self._seed(tmp_path)
        self._write_doc(tmp_path, revised_narrative="   ")
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["narrative_summary"] is None

    def test_a_batch_that_applies_nothing_never_replaces_the_summary(
        self, tmp_path
    ):
        self._seed(tmp_path)
        self._write_doc(tmp_path, revised_narrative=self._REVISED)
        doc = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        doc["adjustments"][0]["applied"] = True
        doc["adjustments"][0]["adjustment_id"] = "already-landed"
        (tmp_path / "decision-critic-adjustments.json").write_text(
            json.dumps(doc)
        )
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["narrative_summary"] == self._SUMMARY


class TestWithdrawnAssessmentRender:
    """A withdrawn-and-unreplaced assessment renders as an explicit
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
            "schema": 1, "verdict": "approve",
            "summary": {"total_issues": 0, "by_severity": {
                "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
            }},
            "issues": [], "narrative_summary": None,
            "unreviewed": None, "deferred_reviewed": [], "observations": None,
            "recommendations": None, "positive_observations": None,
            "clearances": None,
            "meta": {"files_reviewed": 1, "unreviewed_autofilled": None,
                     "review_duration_ms": 1, "confidence_score": 0.9},
        }
        data.update(overrides)
        return render_markdown(data)

    def test_withdrawn_without_replacement_says_so(self):
        md = self._render(withdrawn_narrative_summary=[
            {"text": "One CRITICAL blocker.", "withdrawn_by": ["a1"]},
        ])
        assert "No current assessment" in md
        assert "not replaced" in md
        assert "One CRITICAL blocker." not in md

    def test_a_replacement_is_not_attributed_to_the_reconciler(self):
        md = self._render(
            narrative_summary="After spot-checking: guarded upstream.",
            withdrawn_narrative_summary=[
                {"text": "One CRITICAL blocker.", "withdrawn_by": ["a1"]},
            ],
        )
        assert "After spot-checking: guarded upstream." in md
        assert "not adjusted by the decision critic" not in md

    def test_an_untouched_assessment_still_reads_as_the_reconcilers(self):
        md = self._render(narrative_summary="The change is sound.")
        assert "not adjusted by the decision critic" in md

    def test_spot_check_outcomes_render_per_id(self):
        md = self._render(applied_critic_adjustments=[
            {"adjustment_id": "aaaa", "spot_check": "verified"},
            {"adjustment_id": "bbbb", "spot_check": "not_checked"},
        ])
        assert "aaaa" in md and "verified" in md
        assert "bbbb" in md and "not_checked" in md

    def test_no_applied_adjustments_renders_no_section(self):
        assert "Critic Adjustments" not in self._render()


class TestLedgerVerdictRecompute:
    """`_recount_summary` rebuilt the severities and left `verdict` alone.

    That was survivable while step 11 copied an orchestrator-transcribed
    verdict over the ledger's; with the published verdict DERIVED from the
    ledger, a stale `request_changes` over a demoted-to-low issue list is
    machine authority for a wrong GitHub verdict.
    """

    pytestmark = pytest.mark.usefixtures("revise_verdict")

    def test_demoting_the_last_high_moves_the_verdict(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "high")],
                        verdict="request_changes")
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "approve"

    def test_promoting_to_critical_blocks(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "medium")],
                        verdict="comment")
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"}, "rationale": "unguarded",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "block"

    def test_the_pre_apply_verdict_is_preserved(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "high")],
                        verdict="request_changes")
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict_before_adjustments"] == "request_changes"

    def test_the_audit_trail_records_only_the_first_change(self, tmp_path):
        """A second round must name what the ledger came in as, not what
        the previous round left behind."""
        _write_findings(tmp_path, [_issue("aaaa1111", "high")],
                        verdict="request_changes")
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "round one",
        }])
        apply_adjustments(str(tmp_path))
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
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
        _write_findings(tmp_path, [_issue("aaaa1111", "high")],
                        verdict="deliberately-stale")
        _write_adjustments(tmp_path, [{
            "adjustment_id": "landed", "applied": True, "action": "demote",
            "id": "aaaa1111", "fields": {"severity": "low"}, "rationale": "r",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "deliberately-stale"
        assert "verdict_before_adjustments" not in data

    def test_an_unchanged_verdict_records_no_audit_trail(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "high"),
                                   _issue("bbbb2222", "high")],
                        verdict="request_changes")
        _write_adjustments(tmp_path, [{
            "action": "demote", "id": "aaaa1111",
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])
        apply_adjustments(str(tmp_path))
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "request_changes"
        assert "verdict_before_adjustments" not in data

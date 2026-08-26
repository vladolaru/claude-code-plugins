"""Tests for critic_adjustments — the sole writer that carries decision-critic
finding-level decisions into review-findings.json."""

import json
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "review" / "critic_adjustments.py"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(TESTS_DIR))

from helpers.review_fixtures import canonical_findings_ledger
from review.atomic_io import atomic_write_json
from review.critic_adjustments import (
    APPLIED_IDS_KEY,
    REJECTED_ADJUSTMENTS_KEY,
    INVALIDATED_ASSESSMENTS_KEY,
    OUTCOME_NOT_CHECKED,
    OUTCOME_REFUTED,
    OUTCOME_VERIFIED,
    adjudicate,
    adjudication_state,
    read_critic_verdict,
    validate_findings_document,
    validate_proposal_input,
    write_critic_verdict,
    write_findings,
)
from review import critic_adjustments as critic_adjustments_module
from review import orchestration as orchestration_mod
from review.orchestration import _orchestrate_step_11
from review.verdict_rules import derive_review_state


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


def _publish_raw_proposal(output_dir, document, verdict="REVISE"):
    """Bind a marker to exactly these proposal bytes, valid or not.

    Invalid-document tests need the production validator — rather than a
    digest mismatch — to be what rejects them.
    """
    (Path(output_dir) / "decision-critic-adjustments.json").write_text(
        json.dumps(document)
    )
    atomic_write_json(
        str(Path(output_dir) / "decision-critic-verdict.json"),
        {
            "schema": 2,
            "verdict": verdict,
            "proposal_digest": critic_adjustments_module.proposal_digest(
                document
            ),
        },
    )


def _request(ids, *, verified=(), refuted=(), assessment=None):
    """An adjudication request addressed by proposal index, for readability."""
    return {
        "schema": 2,
        "verified": [ids[index] for index in verified],
        "refuted": [
            {"adjustment_id": ids[index], "rejection_reason": reason}
            for index, reason in refuted
        ],
        "revised_assessment": assessment,
    }


def _adjudicate(output_dir, ids, *, verified=(), refuted=(), assessment=None):
    return adjudicate(str(output_dir), _request(
        ids, verified=verified, refuted=refuted, assessment=assessment
    ))


def _publish_and_adjudicate(
    output_dir, adjustments, *, verified=(), refuted=(), assessment=None
):
    """Run one whole critic round: publish the proposal, then adjudicate it."""
    ids = _publish_revise(output_dir, adjustments)
    result = _adjudicate(
        output_dir, ids,
        verified=verified, refuted=refuted, assessment=assessment,
    )
    return ids, result


def _ledger(output_dir):
    return json.loads((Path(output_dir) / "review-findings.json").read_text())


def _applied_ids(findings):
    """The ids out of `applied_critic_adjustments`, whose entries are
    records (`{"adjustment_id": ..., "outcome": ...}`) rather than bare
    strings — the id half is what makes a second adjudication detectable,
    the outcome half is the orchestrator's verdict on that decision."""
    return [record["adjustment_id"] for record in findings[APPLIED_IDS_KEY]]


def _finding(id_, severity="low"):
    return {"id": id_, "severity": severity, "title": "t", "file": "f.go",
            "line": 10, "description": "d", "recommendation": "r",
            "category": "general", "confidence": 0.9}


def _check(id_, *, result="No matching callers."):
    return {
        "id": id_,
        "question": "Do any in-tree callers use the removed parameter?",
        "method": "rg removed_parameter src tests",
        "result": result,
        "source_reviewers": ["ecosystem-integration"],
    }


class TestCanonicalFindingsReader:
    """The reader boundary rejects any ledger a live consumer cannot trust."""

    @staticmethod
    def _write_raw(tmp_path, payload):
        path = tmp_path / "review-findings.json"
        path.write_text(json.dumps(payload))
        return path

    def test_schema_three_ledger_without_reviewed_files_is_canonical(self):
        validate_findings_document(canonical_findings_ledger(("high",)))

    @pytest.mark.parametrize("extra", [
        {"reviewer": "reconciliator"},
        {"review_claimable_files": []},
        {"schema": 2},
    ])
    def test_reviewer_envelope_fields_are_rejected_on_the_ledger(self, extra):
        with pytest.raises(ValueError):
            validate_findings_document(
                {**canonical_findings_ledger(("high",)), **extra}
            )

    def test_reconciliation_counts_must_partition_grouped(self):
        ledger = canonical_findings_ledger(("high",), reconciliation={
            "grouped_concern_count": 5, "verified_concern_count": 1,
            "false_positive_concern_count": 3, "out_of_scope_concern_count": 0,
            "input_finding_count": 6,
        })
        with pytest.raises(ValueError, match="grouped_concern_count"):
            validate_findings_document(ledger)

    @pytest.mark.parametrize(
        "reconciliation",
        [
            {"reviewing_agents": ["Security Reviewer"]},
            {"dispatched_agents": ["security-reviewer", "Rogue_Agent"]},
            {"missing_agents": ["a11y reviewer"]},
            {"not_applicable_agents": [
                {"name": "A11y Reviewer", "skip_reason": "no UI changed"},
            ]},
        ],
        ids=(
            "reviewing-agents",
            "dispatched-agents",
            "missing-agents",
            "not-applicable-agent-name",
        ),
    )
    def test_reconciliation_agent_names_follow_the_dispatch_grammar(
        self, reconciliation
    ):
        """A name outside `[a-z0-9][a-z0-9-]*` used to pass here and then
        null the whole reconciliation block in the offline metrics report,
        so the ledger is where it has to be refused."""
        with pytest.raises(ValueError, match="agent name"):
            validate_findings_document(
                canonical_findings_ledger(reconciliation=reconciliation)
            )

    @pytest.mark.parametrize(
        "skip_reason",
        ["", "   ", "x" * 4097, "no UI\x07 changed"],
        ids=("empty", "blank", "over-the-ceiling", "control-character"),
    )
    def test_not_applicable_skip_reasons_are_bounded_text(self, skip_reason):
        with pytest.raises(ValueError, match="skip_reason"):
            validate_findings_document(canonical_findings_ledger(
                reconciliation={"not_applicable_agents": [
                    {"name": "a11y-reviewer", "skip_reason": skip_reason},
                ]},
            ))

    def test_a_live_entry_may_not_carry_a_removal_adjustment(self):
        ledger = canonical_findings_ledger(("high",))
        ledger["findings"][0]["critic_adjustment"] = {
            "action": "remove", "rationale": "Not reproducible.",
        }
        with pytest.raises(ValueError, match="critic_adjustment provenance"):
            validate_findings_document(ledger)

    @pytest.mark.parametrize(
        "payload",
        [
            {"verdict": "block"},
            {"schema": 2, "verdict": "approve", "findings": "none"},
            {"schema": 2, "issues": [], "verdict": "approve"},
        ],
    )
    def test_object_shaped_noncanonical_ledgers_are_invalid(
        self, tmp_path, payload
    ):
        path = self._write_raw(tmp_path, payload)

        read = critic_adjustments_module.read_findings_file(path)

        assert read.status == "invalid"
        assert read.findings is None
        assert isinstance(read.error, ValueError)

    @pytest.mark.parametrize(
        "mutation",
        [
            lambda ledger: ledger.update(findings=[{"id": "f1"}]),
            lambda ledger: ledger["findings"].append(
                dict(ledger["findings"][0])
            ),
            lambda ledger: ledger["meta"].update(next_finding_number=1),
            lambda ledger: ledger.update(applied_critic_adjustments=[{
                "adjustment_id": "orphan", "outcome": "verified",
            }]),
        ],
        ids=(
            "malformed-finding",
            "duplicate-finding-id",
            "counter-reuses-live-id",
            "applied-adjustment-without-critic-provenance",
        ),
    )
    def test_complete_ledger_invariants_are_checked_at_read(
        self, tmp_path, mutation
    ):
        ledger = _write_findings(tmp_path, [_finding("f1")])
        mutation(ledger)
        path = self._write_raw(tmp_path, ledger)

        read = critic_adjustments_module.read_findings_file(path)

        assert read.status == "invalid"
        assert isinstance(read.error, ValueError)

    def test_canonical_reconciler_ledger_is_accepted(self, tmp_path):
        _write_findings(
            tmp_path,
            [_finding("f1")],
            checks=[_check("c1")],
            assessment="One low-severity finding remains.",
        )

        read = critic_adjustments_module.read_findings_file(
            tmp_path / "review-findings.json"
        )

        assert read.status == critic_adjustments_module.FINDINGS_READ_OK
        assert read.findings["findings"][0]["id"] == "f1"

    def test_canonical_critic_adjusted_ledger_is_accepted(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _publish_and_adjudicate(tmp_path, [{
            "action": "promote",
            "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "high"},
            "rationale": "The verified impact crosses the high threshold.",
        }], verified=(0,))

        read = critic_adjustments_module.read_findings_file(
            tmp_path / "review-findings.json"
        )

        assert read.status == critic_adjustments_module.FINDINGS_READ_OK
        assert read.findings["findings"][0]["severity"] == "high"

    def test_only_absence_is_distinguished_from_being_unusable(self, tmp_path):
        """Absent is the one state a caller answers differently.

        Every other way of being unreadable — a directory, undecodable
        bytes, a non-object payload — means the same thing to every
        consumer: nothing may read this ledger.
        """
        absent = critic_adjustments_module.read_findings_file(
            tmp_path / "missing.json"
        )
        directory = tmp_path / "ledger-directory"
        directory.mkdir()
        undecodable = tmp_path / "review-findings.json"
        undecodable.write_bytes(b'{"schema": 2, "invalid": "\xff"}')
        not_an_object = tmp_path / "list-ledger.json"
        not_an_object.write_text("[]")

        assert absent.status == critic_adjustments_module.FINDINGS_READ_ABSENT
        assert isinstance(absent.error, FileNotFoundError)
        for path in (directory, undecodable, not_an_object):
            read = critic_adjustments_module.read_findings_file(path)
            assert read.status == (
                critic_adjustments_module.FINDINGS_READ_INVALID
            ), path
            assert read.findings is None


def _publish_step_11(output_dir, state=None):
    """Prepare without a report, then publish the authored report."""
    state = {} if state is None else state
    report = Path(output_dir) / "review-report.md"
    report_text = report.read_text() if report.is_file() else "# report"
    report.unlink(missing_ok=True)
    _orchestrate_step_11("pr", {}, state, {}, str(output_dir))
    report.write_text(report_text)
    return _orchestrate_step_11("pr", {}, state, {}, str(output_dir))


class TestAdjudicateWritesTheLedgerOnce:
    """The whole settlement lifecycle: one proposal, one adjudication, one
    ledger write that records every entry's outcome."""

    def test_verified_refuted_and_unchecked_land_as_outcomes(self, tmp_path):
        write_findings(str(tmp_path), canonical_findings_ledger(
            ("high", "medium")
        ))
        ids = _publish_revise(tmp_path, [
            {"action": "demote", "target": {"kind": "finding", "id": "f1"},
             "fields": {"severity": "low"}, "rationale": "r1"},
            {"action": "remove", "target": {"kind": "finding", "id": "f2"},
             "fields": {}, "rationale": "r2"},
            {"action": "add", "target": {"kind": "finding"},
             "fields": {"severity": "low", "title": "n", "file": "src/n.py",
                        "description": "d", "recommendation": "r"},
             "rationale": "r3"},
        ])
        proposal_before = (
            tmp_path / "decision-critic-adjustments.json"
        ).read_bytes()

        result = adjudicate(str(tmp_path), {
            "schema": 2,
            "verified": [ids[0]],
            "refuted": [{
                "adjustment_id": ids[1],
                "rejection_reason": "the code does not do that",
            }],
            "revised_assessment": "Two low findings remain.",
        })

        assert result["counts"] == {
            "verified": 1, "refuted": 1, "not_checked": 1,
        }
        assert (
            tmp_path / "decision-critic-adjustments.json"
        ).read_bytes() == proposal_before, "the proposal is never rewritten"
        ledger = _ledger(tmp_path)
        assert [r["outcome"] for r in ledger[APPLIED_IDS_KEY]] == [
            "verified", "not_checked",
        ]
        assert ledger[REJECTED_ADJUSTMENTS_KEY][0]["outcome"] == "refuted"
        assert [f["id"] for f in ledger["findings"]] == ["f1", "f2", "f3"]
        assert ledger["findings"][0]["severity"] == "low"
        assert ledger["assessment"] == "Two low findings remain."
        assert ledger["verdict"] == "comment"
        assert adjudication_state(str(tmp_path)) == "adjudicated"

    def test_second_adjudication_of_the_same_proposal_is_refused(
        self, tmp_path
    ):
        write_findings(str(tmp_path), canonical_findings_ledger(("high",)))
        ids = _publish_revise(tmp_path, [
            {"action": "demote", "target": {"kind": "finding", "id": "f1"},
             "fields": {"severity": "low"}, "rationale": "r"},
        ])
        _adjudicate(tmp_path, ids, verified=(0,))
        settled = _ledger(tmp_path)

        with pytest.raises(ValueError, match="already adjudicated"):
            _adjudicate(tmp_path, ids, verified=(0,))
        assert _ledger(tmp_path) == settled

    def test_state_is_pending_until_adjudicated(self, tmp_path):
        write_findings(str(tmp_path), canonical_findings_ledger(("high",)))
        _publish_revise(tmp_path, [
            {"action": "demote", "target": {"kind": "finding", "id": "f1"},
             "fields": {"severity": "low"}, "rationale": "r"},
        ])
        assert adjudication_state(str(tmp_path)) == "pending"

    def test_non_revise_verdict_cannot_be_adjudicated(self, tmp_path):
        write_findings(str(tmp_path), canonical_findings_ledger(("high",)))
        _publish_verdict(tmp_path, "STAND")

        assert adjudication_state(str(tmp_path)) == "empty"
        with pytest.raises(ValueError, match="STAND"):
            _adjudicate(tmp_path, [])

    def test_a_tampered_proposal_is_refused(self, tmp_path):
        """The marker commits a digest; an edited proposal is unusable."""
        write_findings(str(tmp_path), canonical_findings_ledger(("high",)))
        ids = _publish_revise(tmp_path, [
            {"action": "demote", "target": {"kind": "finding", "id": "f1"},
             "fields": {"severity": "low"}, "rationale": "r"},
        ])
        path = tmp_path / "decision-critic-adjustments.json"
        proposal = json.loads(path.read_text())
        proposal["adjustments"][0]["fields"]["severity"] = "info"
        path.write_text(json.dumps(proposal))

        with pytest.raises(ValueError, match="digest mismatch"):
            _adjudicate(tmp_path, ids, verified=(0,))
        assert read_critic_verdict(str(tmp_path)) is None

    def test_removed_module_surface(self):
        for name in (
            "apply_adjustments", "pending_count", "settle", "SPOT_CHECK_KEY",
            "ADJUDICATION_KEY", "REFUSAL_EXIT_CODE",
        ):
            assert not hasattr(critic_adjustments_module, name)


class TestApplyAdjustments:
    def test_promote_patches_severity_with_provenance(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _, result = _publish_and_adjudicate(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "medium"},
            "rationale": "affects future strategy authors",
        }], verified=(0,))
        assert result["applied"] == 1
        data = _ledger(tmp_path)
        finding = data["findings"][0]
        assert finding["severity"] == "medium"
        assert finding["critic_adjustment"]["action"] == "promote"
        assert finding["critic_adjustment"]["prior"] == {"severity": "low"}
        assert data["summary"]["by_severity"]["medium"] == 1
        assert data["summary"]["by_severity"]["low"] == 0

    def test_add_appends_full_finding_with_generated_id(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        _publish_and_adjudicate(tmp_path, [{
            "action": "add", "target": {"kind": "finding"},
            "fields": {"severity": "low", "title": "stale README",
                       "file": "internal/strategy/README.md",
                       "description": "teaches the deleted warm path",
                       "recommendation": "update the warm/cold section"},
            "rationale": "promoted from docs-drift observations",
        }])
        data = _ledger(tmp_path)
        assert data["summary"]["total_findings"] == 2
        added = data["findings"][1]
        assert added["id"] == "f2"
        assert added["critic_adjustment"]["action"] == "add"

    def test_remove_moves_finding_out_with_provenance(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1"), _finding("f2")])
        _publish_and_adjudicate(tmp_path, [{
            "action": "remove", "target": {"kind": "finding", "id": "f2"},
            "fields": {}, "rationale": "false positive — refuted by source",
        }])
        data = _ledger(tmp_path)
        assert [i["id"] for i in data["findings"]] == ["f1"]
        assert data["findings_removed_by_critic"][0]["id"] == "f2"
        assert data["summary"]["total_findings"] == 1

    def test_unknown_id_fails_loudly_and_writes_nothing(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        ids = _publish_revise(tmp_path, [
            {"action": "promote", "target": {"kind": "finding", "id": "f1"},
             "fields": {"severity": "high"}, "rationale": "r"},
            {"action": "promote", "target": {"kind": "finding", "id": "f9"},
             "fields": {"severity": "high"}, "rationale": "r"},
        ])
        with pytest.raises(ValueError, match="f9"):
            _adjudicate(tmp_path, ids)
        data = _ledger(tmp_path)
        assert data["findings"][0]["severity"] == "low"  # entry 1 NOT applied

    def test_invalid_action_and_field_rejected(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        with pytest.raises(ValueError, match="obliterate"):
            _publish_revise(tmp_path, [{
                "action": "obliterate",
                "target": {"kind": "finding", "id": "f1"},
                "fields": {}, "rationale": "r",
            }])
        with pytest.raises(ValueError, match="verdict"):
            _publish_revise(tmp_path, [{
                "action": "correct", "target": {"kind": "finding", "id": "f1"},
                "fields": {"verdict": "APPROVE"}, "rationale": "r",
            }])

    def test_refuted_entries_are_skipped(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _, result = _publish_and_adjudicate(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "critical"}, "rationale": "r",
        }], refuted=((0, "the probe refuted the claim"),))
        assert result["applied"] == 0
        assert _ledger(tmp_path)["findings"][0]["severity"] == "low"

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
        _, result = _publish_and_adjudicate(tmp_path, [
            {"action": "promote", "target": {"kind": "finding", "id": "f1"},
             "fields": {"severity": "high"}, "rationale": "wider blast radius"},
            {"action": "remove", "target": {"kind": "finding", "id": "f3"},
             "fields": {}, "rationale": "refuted by source"},
            {"action": "add", "target": {"kind": "finding"},
             "fields": {"severity": "critical", "title": "unbounded retry",
                        "file": "internal/queue/retry.go",
                        "description": "no ceiling on attempts",
                        "recommendation": "cap attempts"},
             "rationale": "critic found it independently"},
        ], verified=(0, 1, 2))
        assert result["applied"] == 3

        data = _ledger(tmp_path)
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
        _, result = _publish_and_adjudicate(tmp_path, [{
            "action": "add", "target": {"kind": "finding"},
            "fields": {"severity": "high", "title": "unbounded retry",
                       "file": "internal/queue/retry.go",
                       "description": "no ceiling on attempts",
                       "recommendation": "cap attempts"},
            "rationale": "critic found it independently",
        }], verified=(0,))
        assert result["applied"] == 1

        data = _ledger(tmp_path)
        assert len(data["findings"]) == 2
        added = data["findings"][1]
        assert added["id"] == "f2"
        assert added["title"] == "unbounded retry"
        assert added["critic_adjustment"] == {
            "action": "add", "rationale": "critic found it independently",
        }
        assert data["summary"]["total_findings"] == 2
        assert data["summary"]["by_severity"] == {
            "critical": 0, "high": 1, "medium": 0, "low": 1, "info": 0,
        }


class TestRejectionAudit:
    """A refuted critic decision must leave a trace in the artifact
    downstream readers actually consult, not only in
    decision-critic-adjustments.json, which none of them read."""

    def test_refuted_entry_lands_in_the_findings_audit_trail(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _, result = _publish_and_adjudicate(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "critical"}, "rationale": "r",
        }], refuted=((0, "the probe refuted the claim"),))
        assert result["applied"] == 0  # a refuted entry is never applied
        assert result["rejected"] == 1
        data = _ledger(tmp_path)
        assert data["findings"][0]["severity"] == "low"

        records = data[REJECTED_ADJUSTMENTS_KEY]
        assert len(records) == 1
        record = records[0]
        assert record["action"] == "promote"
        assert record["target"] == {"kind": "finding", "id": "f1"}
        assert record["outcome"] == "refuted"
        assert record["rejection_reason"] == "the probe refuted the claim"
        assert record["adjustment_id"]

    def test_a_refuted_entry_in_a_later_round_appends(self, tmp_path):
        _write_findings(
            tmp_path, [_finding("f1", "low"), _finding("f2", "low")]
        )
        _publish_and_adjudicate(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "high"}, "rationale": "r",
        }], refuted=((0, "first round refutation"),))
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f2"},
            "fields": {"severity": "info"}, "rationale": "r",
        }], refuted=((0, "second round refutation"),))

        records = _ledger(tmp_path)[REJECTED_ADJUSTMENTS_KEY]
        assert len(records) == 2
        assert {r["target"]["id"] for r in records} == {"f1", "f2"}
        assert {r["rejection_reason"] for r in records} == {
            "first round refutation", "second round refutation",
        }

    def test_mixed_batch_applies_one_and_audits_the_other(self, tmp_path):
        _write_findings(
            tmp_path, [_finding("f1", "low"), _finding("f2", "low")]
        )
        _, result = _publish_and_adjudicate(tmp_path, [
            {"action": "promote", "target": {"kind": "finding", "id": "f1"},
             "fields": {"severity": "high"}, "rationale": "r"},
            {"action": "demote", "target": {"kind": "finding", "id": "f2"},
             "fields": {"severity": "info"}, "rationale": "r"},
        ], verified=(0,), refuted=((1, "refuted"),))
        assert result["applied"] == 1
        assert result["rejected"] == 1
        data = _ledger(tmp_path)
        assert data["findings"][0]["severity"] == "high"
        assert data["findings"][1]["severity"] == "low"  # refuted, untouched
        records = data[REJECTED_ADJUSTMENTS_KEY]
        assert len(records) == 1
        assert records[0]["target"] == {"kind": "finding", "id": "f2"}

    @pytest.mark.parametrize("bad_reason", [None, "", "   "])
    def test_missing_or_blank_rejection_reason_refuses_the_whole_request(
        self, tmp_path, bad_reason
    ):
        """rejection_reason is the entire payload of the audit record —
        a refutation without one is refused loudly, the same
        all-or-nothing style an unknown action or invalid severity gets,
        instead of silently writing an empty string into the ledger."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        ids = _publish_revise(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "high"}, "rationale": "r",
        }])
        refuted = {"adjustment_id": ids[0]}
        if bad_reason is not None:
            refuted["rejection_reason"] = bad_reason
        with pytest.raises(ValueError, match="rejection_reason"):
            adjudicate(str(tmp_path), {
                "schema": 2, "verified": [], "refuted": [refuted],
                "revised_assessment": None,
            })
        assert REJECTED_ADJUSTMENTS_KEY not in _ledger(tmp_path)


class TestBatchCoherence:
    def test_duplicate_target_in_one_batch_is_rejected(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        with pytest.raises(ValueError, match="duplicate target"):
            _publish_revise(tmp_path, [
                {"action": "promote",
                 "target": {"kind": "finding", "id": "f1"},
                 "fields": {"severity": "high"}, "rationale": "r"},
                {"action": "correct",
                 "target": {"kind": "finding", "id": "f1"},
                 "fields": {"title": "clearer title"}, "rationale": "r"},
            ])
        data = _ledger(tmp_path)
        assert data["findings"][0]["severity"] == "low"
        assert "critic_adjustment" not in data["findings"][0]

    def test_targeting_an_id_removed_earlier_in_the_batch_is_rejected(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1"), _finding("f2")])
        with pytest.raises(ValueError, match="removed by adjustment\\[0\\]"):
            _publish_revise(tmp_path, [
                {"action": "remove", "target": {"kind": "finding", "id": "f2"},
                 "fields": {}, "rationale": "false positive"},
                {"action": "promote",
                 "target": {"kind": "finding", "id": "f2"},
                 "fields": {"severity": "high"}, "rationale": "r"},
            ])
        assert [i["id"] for i in _ledger(tmp_path)["findings"]] == ["f1", "f2"]

    def test_entry_without_an_id_fails_as_unknown_id(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        with pytest.raises(ValueError, match="target.id"):
            _publish_revise(tmp_path, [{
                "action": "promote", "target": {"kind": "finding"},
                "fields": {"severity": "high"}, "rationale": "r",
            }])

    def test_findings_finding_without_an_id_is_not_addressable(self, tmp_path):
        """A None target must not silently match an id-less finding."""
        idless = _finding("f1")
        del idless["id"]
        _write_findings(tmp_path, [idless])
        ids = _publish_revise(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "high"}, "rationale": "r",
        }])
        with pytest.raises(
            ValueError, match="missing required fields: id"
        ):
            _adjudicate(tmp_path, ids)

    def test_add_rejects_a_critic_supplied_id_in_both_spellings(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1")])
        base_fields = {"severity": "low", "title": "t", "file": "f.go",
                       "description": "d", "recommendation": "r"}
        with pytest.raises(ValueError, match="must not include id"):
            _publish_revise(tmp_path, [{
                "action": "add", "target": {"kind": "finding", "id": "f3"},
                "fields": dict(base_fields), "rationale": "r",
            }])
        with pytest.raises(ValueError, match="'id' is not adjustable"):
            _publish_revise(tmp_path, [{
                "action": "add", "target": {"kind": "finding"},
                "fields": {**base_fields, "id": "f3"}, "rationale": "r",
            }])

    def test_malformed_ledger_severity_fails_instead_of_undercounting(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1", "low")])
        raw = _ledger(tmp_path)
        raw["findings"].append({**_finding("f2"), "severity": "blocker"})
        (tmp_path / "review-findings.json").write_text(json.dumps(raw))
        ids = _publish_revise(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="finding 1.severity is invalid"):
            _adjudicate(tmp_path, ids)
        assert _ledger(tmp_path)["findings"][0]["severity"] == "low"

    @pytest.mark.parametrize("shape", [[{"id": "f1"}], "findings", 7])
    def test_findings_that_is_not_an_object_fails_as_a_value_error(
        self, tmp_path, shape
    ):
        """The adjustments file is shape-guarded; the findings file was
        not, so a non-object ledger died on an AttributeError outside this
        module's ValueError contract — the one step 11 catches."""
        (tmp_path / "review-findings.json").write_text(json.dumps(shape))
        ids = _publish_revise(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        with pytest.raises(ValueError, match="must be a JSON object"):
            _adjudicate(tmp_path, ids)
        assert json.loads(
            (tmp_path / "review-findings.json").read_text()
        ) == shape


class TestValidateProposalInput:
    """Direct unit coverage for the critic-owned proposal validator."""

    def test_valid_batch_returns_no_problems(self):
        assert validate_proposal_input({
            "schema": 2,
            "adjustments": [{
                "action": "promote", "target": {"kind": "finding", "id": "f1"},
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        }) == []

    def test_non_object_payload_is_a_problem(self):
        assert validate_proposal_input([1, 2, 3]) == [
            "decision-critic-adjustments.json must be a JSON object"
        ]

    def test_wrong_schema_is_a_problem(self):
        problems = validate_proposal_input({"schema": 1, "adjustments": []})
        assert len(problems) == 1
        assert "'schema' must be 2" in problems[0]

    def test_adjustments_not_a_list_is_a_problem(self):
        assert validate_proposal_input({"schema": 2, "adjustments": "nope"}) == [
            "decision-critic-adjustments.json: 'adjustments' must be a list"
        ]

    def test_missing_adjustments_key_is_a_problem(self):
        assert validate_proposal_input({"schema": 2}) == [
            "decision-critic-adjustments.json: 'adjustments' must be a list"
        ]

    def test_entry_not_an_object_is_a_problem(self):
        assert validate_proposal_input({
            "schema": 2, "adjustments": ["not-a-dict"],
        }) == ["adjustment[0] must be an object"]

    def test_adjustment_id_is_not_a_proposal_field(self):
        problems = validate_proposal_input({
            "schema": 2,
            "adjustments": [{
                "adjustment_id": "caller-owned", "action": "promote",
                "target": {"kind": "finding", "id": "f1"},
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        })
        assert any("adjustment_id" in problem for problem in problems)

    def test_unknown_action_is_a_problem(self):
        problems = validate_proposal_input({
            "schema": 2,
            "adjustments": [{
                "action": "obliterate", "target": {"kind": "finding", "id": "f1"},
                "fields": {}, "rationale": "r",
            }],
        })
        assert any("unknown action" in p and "obliterate" in p for p in problems)

    def test_invalid_field_is_a_problem(self):
        problems = validate_proposal_input({
            "schema": 2,
            "adjustments": [{
                "action": "correct", "target": {"kind": "finding", "id": "f1"},
                "fields": {"verdict": "APPROVE"}, "rationale": "r",
            }],
        })
        assert any("not adjustable" in p for p in problems)

    def test_add_missing_required_fields_is_a_problem(self):
        problems = validate_proposal_input({
            "schema": 2,
            "adjustments": [{
                "action": "add", "target": {"kind": "finding"},
                "fields": {"severity": "low"}, "rationale": "r",
            }],
        })
        assert any("add requires fields" in p for p in problems)

    def test_add_with_a_critic_supplied_id_is_a_problem(self):
        problems = validate_proposal_input({
            "schema": 2,
            "adjustments": [{
                "action": "add", "target": {"kind": "finding", "id": "f3"},
                "fields": {"severity": "low", "title": "t", "file": "f.go",
                           "description": "d", "recommendation": "r"},
                "rationale": "r",
            }],
        })
        assert any("must not include id" in p for p in problems)

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
            ("rescope", {}, "rescope requires exactly the file and line fields"),
            (
                "rescope",
                {"line": 20},
                "rescope requires exactly the file and line fields",
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
            "schema": 2,
            "adjustments": [{
                "action": action,
                "target": {"kind": "finding", "id": "f1"},
                "fields": fields,
                "rationale": "r",
            }],
        })

        assert any(problem in candidate for candidate in problems)

    def test_a_proposal_may_target_each_finding_only_once(self):
        problems = validate_proposal_input({
            "schema": 2,
            "adjustments": [
                {
                    "action": "promote",
                    "target": {"kind": "finding", "id": "f1"},
                    "fields": {"severity": "high"},
                    "rationale": "r",
                },
                {
                    "action": "correct",
                    "target": {"kind": "finding", "id": "f1"},
                    "fields": {"title": "Clearer title"},
                    "rationale": "r",
                },
            ],
        })

        assert any(
            "duplicate target finding 'f1'" in problem for problem in problems
        )

    def test_two_independent_problems_are_both_reported(self):
        """The proposal validator collects every independent problem
        instead of stopping at the first one it finds, which can only be
        pinned by calling the validator directly."""
        problems = validate_proposal_input({
            "schema": 2,
            "adjustments": [
                {"action": "obliterate", "target": {"kind": "finding", "id": "f1"},
                 "fields": {}, "rationale": "r"},
                {"action": "add", "target": {"kind": "finding", "id": "f3"},
                 "fields": {"severity": "low", "title": "t", "file": "f.go",
                            "description": "d", "recommendation": "r"},
                 "rationale": "r"},
            ],
        })
        assert len(problems) == 2
        assert any("unknown action" in p and "obliterate" in p for p in problems)
        assert any("must not include id" in p for p in problems)


class TestAdjustmentsSchemaValidation:
    """decision-reviewer.md's taught template always writes `"schema": 2`
    alongside `"adjustments"`; a doc out of that template is refused
    whole, the same all-or-nothing way an unknown action is."""

    def test_schema_2_proceeds(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _, result = _publish_and_adjudicate(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "high"}, "rationale": "r",
        }], verified=(0,))
        assert result["applied"] == 1

    @pytest.mark.parametrize(
        "schema_field",
        [{"schema": 1}, {}, {"schema": "2"}],
        ids=("prior-schema", "missing-schema", "numeric-string"),
    )
    def test_a_schema_out_of_template_refuses_the_whole_batch(
        self, tmp_path, schema_field
    ):
        """The taught template always writes `"schema": 2`. A prior value,
        an absent key, and the string `"2"` are all out of that template
        and get the same refusal — never a silent read as version 1 or a
        coerced integer."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        _publish_raw_proposal(tmp_path, {
            **schema_field,
            "adjustments": [{
                "adjustment_id": "a1",
                "action": "promote", "target": {"kind": "finding", "id": "f1"},
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        })
        with pytest.raises(ValueError, match="'schema' must be 2"):
            _adjudicate(tmp_path, [])
        data = _ledger(tmp_path)
        assert data["findings"][0]["severity"] == "low"  # nothing written

    @pytest.mark.parametrize("shape", [[{"id": "f1"}], "hello", 5])
    def test_non_object_doc_fails_as_a_shape_error_not_a_schema_error(
        self, tmp_path, shape
    ):
        """[], "hello", and 5 are all valid JSON but not a document with a
        'schema' field to be wrong about — the diagnosis must name the
        actual defect (not a JSON object) rather than misreporting it as a
        missing or invalid schema."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        _publish_verdict(tmp_path, "REVISE")
        (tmp_path / "decision-critic-adjustments.json").write_text(
            json.dumps(shape)
        )
        with pytest.raises(
            ValueError,
            match="decision-critic-adjustments.json must be a JSON object",
        ):
            _adjudicate(tmp_path, [])
        data = _ledger(tmp_path)
        assert data["findings"][0]["severity"] == "low"  # nothing written

    def test_a_prepared_entry_may_carry_only_its_proposal_fields(self):
        """Adjudication is recorded in the ledger, never back on the entry."""
        problems = critic_adjustments_module.validate_adjustments_document({
            "schema": 2,
            "adjustments": [{
                "adjustment_id": "a1",
                "action": "promote", "target": {"kind": "finding", "id": "f1"},
                "fields": {"severity": "high"}, "rationale": "r",
                "outcome": "verified", "applied": True,
            }],
        })
        assert any("'outcome' is not allowed" in p for p in problems)
        assert any("'applied' is not allowed" in p for p in problems)

    def test_duplicate_adjustment_ids_are_rejected(self):
        problems = critic_adjustments_module.validate_adjustments_document({
            "schema": 2,
            "adjustments": [
                {"adjustment_id": "dup", "action": "promote",
                 "target": {"kind": "finding", "id": "f1"},
                 "fields": {"severity": "high"}, "rationale": "r"},
                {"adjustment_id": "dup", "action": "promote",
                 "target": {"kind": "finding", "id": "f2"},
                 "fields": {"severity": "high"}, "rationale": "r"},
            ],
        })
        assert any("duplicate adjustment_id" in p for p in problems)


class TestScopeLinePairing:
    """schemas/review-output.ts:36-37 and output.py's renderer treat
    scope/line as a pair; a patch must never split them."""

    def test_add_without_a_line_is_marked_file_scoped(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        _publish_and_adjudicate(tmp_path, [{
            "action": "add", "target": {"kind": "finding"},
            "fields": {"severity": "low", "title": "stale README",
                       "file": "README.md", "description": "d",
                       "recommendation": "r"},
            "rationale": "r",
        }])
        added = _ledger(tmp_path)["findings"][1]
        assert added["line"] is None
        assert added["scope"] == "file"

    def test_add_with_a_line_carries_no_scope_marker(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1")])
        _publish_and_adjudicate(tmp_path, [{
            "action": "add", "target": {"kind": "finding"},
            "fields": {"severity": "low", "title": "t", "file": "f.go",
                       "description": "d", "recommendation": "r", "line": 42},
            "rationale": "r",
        }])
        added = _ledger(tmp_path)["findings"][1]
        assert added["line"] == 42
        assert "scope" not in added

    def test_rescope_to_a_line_drops_the_stale_file_marker(self, tmp_path):
        file_scoped = {**_finding("f1"), "line": None, "scope": "file"}
        _write_findings(tmp_path, [file_scoped])
        _publish_and_adjudicate(tmp_path, [{
            "action": "rescope", "target": {"kind": "finding", "id": "f1"},
            "fields": {"file": "f.go", "line": 88},
            "rationale": "pinned to the call site",
        }])
        finding = _ledger(tmp_path)["findings"][0]
        assert finding["line"] == 88
        assert "scope" not in finding

    def test_rescope_to_no_line_marks_the_finding_file_scoped(self, tmp_path):
        line_anchored = {**_finding("f1"), "line": 12}
        _write_findings(tmp_path, [line_anchored])
        _publish_and_adjudicate(tmp_path, [{
            "action": "rescope", "target": {"kind": "finding", "id": "f1"},
            "fields": {"file": "f.go", "line": None},
            "rationale": "the whole file drifted",
        }])
        finding = _ledger(tmp_path)["findings"][0]
        assert finding["line"] is None
        assert finding["scope"] == "file"

    def test_a_patch_that_leaves_line_alone_leaves_scope_alone(self, tmp_path):
        file_scoped = {**_finding("f1"), "line": None, "scope": "file"}
        _write_findings(tmp_path, [file_scoped])
        _publish_and_adjudicate(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "high"}, "rationale": "r",
        }])
        finding = _ledger(tmp_path)["findings"][0]
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
        with pytest.raises(ValueError, match="line must be a positive"):
            _publish_revise(tmp_path, [{
                "action": "rescope", "target": {"kind": "finding", "id": "f1"},
                "fields": {"file": "f.go", "line": bad_line}, "rationale": "r",
            }])


class TestReadCriticVerdict:
    """Unit coverage for the reader `adjudicate`'s gate is built on — it
    returns an allowed verdict only from a complete source-bound snapshot
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

    def test_missing_verdict_key_returns_none(self, tmp_path):
        (tmp_path / "decision-critic-verdict.json").write_text(
            json.dumps({"reason": "no verdict field at all"})
        )
        assert read_critic_verdict(str(tmp_path)) is None

    def test_a_lifecycle_field_on_a_proposal_entry_is_unusable(self, tmp_path):
        """Adjudication lives in the ledger; an entry carrying it is not a
        proposal this module will read."""
        proposal = critic_adjustments_module.prepare_proposal({
            "schema": 2,
            "adjustments": [{
                "action": "demote",
                "target": {"kind": "finding", "id": "f1"},
                "fields": {"severity": "low"},
                "rationale": "Guarded upstream.",
            }],
        })
        proposal["adjustments"][0]["outcome"] = "verified"
        _publish_raw_proposal(tmp_path, proposal)

        assert read_critic_verdict(str(tmp_path)) is None

    def test_adjudicate_rejects_that_proposal_without_mutation(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "high")])
        proposal = critic_adjustments_module.prepare_proposal({
            "schema": 2,
            "adjustments": [{
                "action": "demote",
                "target": {"kind": "finding", "id": "f1"},
                "fields": {"severity": "low"},
                "rationale": "Guarded upstream.",
            }],
        })
        proposal["adjustments"][0]["outcome"] = "verified"
        _publish_raw_proposal(tmp_path, proposal)
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match="'outcome' is not allowed"):
            _adjudicate(tmp_path, [])

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    @pytest.mark.parametrize(
        "verdict", ["REVISE", "STAND", "ESCALATE", "SKIPPED"]
    )
    def test_valid_verdict_string_is_returned_as_is(self, tmp_path, verdict):
        _publish_verdict(tmp_path, verdict)
        assert read_critic_verdict(str(tmp_path)) == verdict

    @pytest.mark.parametrize("near_miss", ["revise", " REVISE ", "REVISE\n"])
    def test_a_near_miss_spelling_is_never_a_usable_verdict(
        self, tmp_path, near_miss
    ):
        """The vocabulary is exact-match, not case-insensitive or
        whitespace-tolerant: a critic that deviates fails loudly rather
        than being silently normalized into an adjudicable REVISE."""
        with pytest.raises(ValueError, match="unknown critic verdict"):
            _publish_verdict(tmp_path, near_miss)
        assert read_critic_verdict(str(tmp_path)) is None


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

    @pytest.mark.parametrize("ledger,severity,published", [
        ("block", "critical", "REQUEST_CHANGES"),
        ("request_changes", "high", "REQUEST_CHANGES"),
        ("comment", "medium", "COMMENT"),
        ("approve", None, "APPROVE"),
    ])
    def test_every_canonical_ledger_verdict_maps(
        self, tmp_path, ledger, severity, published
    ):
        """Every reconciler verdict maps only when its findings derive it."""
        findings = [] if severity is None else [_finding("f1", severity)]
        self._seed(tmp_path, ledger, findings)
        result = self._finalize(tmp_path)
        assert result["verdict"] == published
        assert result["verdict_source"] == "findings ledger"
        assert result["status"] == "success"

    @pytest.mark.parametrize("ledger", ["BLOCK", "  Approve  ", "Comment"])
    def test_casing_and_padding_fail_closed(self, tmp_path, ledger):
        self._seed(tmp_path, ledger)
        result = self._finalize(tmp_path)
        assert result["verdict_source"] == "fallback: no usable ledger verdict"
        assert result["status"] == "degraded"

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
        _publish_verdict(tmp_path, "ESCALATE")
        result = self._finalize(tmp_path)
        assert result["verdict"] == "COMMENT"
        assert result["verdict_source"] == "critic ESCALATE override"

    def test_stand_does_not_override(self, tmp_path):
        self._seed(tmp_path, "block", [_finding("f1", "critical")])
        _publish_verdict(tmp_path, "STAND")
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
        _publish_verdict(tmp_path, "SKIPPED")
        result = self._finalize(tmp_path)
        assert result["status"] == "degraded"
        assert self._NOTE in result["degradation_notes"]

    def test_the_quick_skip_is_silent(self, tmp_path):
        """Quick mode's SKIPPED record is written by the PIPELINE, on the
        branch that deliberately writes no dispatch marker. Nothing was
        dispatched, so nothing was lost."""
        self._seed(tmp_path)
        _publish_verdict(tmp_path, "SKIPPED")
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
        _publish_verdict(tmp_path, verdict)
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

        _publish_and_adjudicate(tmp_path, [{
            "action": "promote",
            "target": {"kind": "finding", "id": visible_ids[0]},
            "fields": {"severity": "high"},
            "rationale": "the exploit path is reachable from the REST route",
        }], verified=(0,), assessment="One high-severity finding remains.")
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
        _publish_verdict(tmp_path, "STAND")

        _orchestrate_step_11("pr", {}, {}, {}, str(tmp_path))

        record = (tmp_path / "review-record.md").read_text()
        assert not re.search(r"^### F\d+\b", record, re.MULTILINE), record
        assert "canonical fN `id` in `review-findings.json` (`findings[].id`)" in record
        assert "a positional label is not a key" in record


class TestStepElevenReportsUnadjudicatedProposal:
    """Step 11 does not adjudicate on the orchestrator's behalf — it reports
    a REVISE proposal that never was, so a ledger published without the
    critic's adjustments says so out loud."""

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        """Keep finalize's worktree hygiene off the developer's own repo.

        Step 11 inspects the repo it is standing in, and pytest stands in
        the real checkout. Scoped to this class because only these tests
        call the step directly; the CLI tests elsewhere in this file run
        in a subprocess with their own cwd.
        """
        monkeypatch.chdir(tmp_path)

    _NOTE = (
        "critic REVISE proposal was never adjudicated; the ledger is "
        "published without its adjustments"
    )

    def _step_11(self, output_dir, state=None):
        return _publish_step_11(output_dir, state)

    def test_a_pending_proposal_degrades_the_run(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")], verdict="approve")
        _publish_revise(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        (tmp_path / "review-report.md").write_text("# report")

        self._step_11(tmp_path)

        data = _ledger(tmp_path)
        assert data["findings"][0]["severity"] == "low", (
            "step 11 must not apply an unprobed batch"
        )
        assert APPLIED_IDS_KEY not in data
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == [self._NOTE]
        assert result["status"] == "degraded"

    def test_the_degradation_is_stable_across_the_publication_handoff(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1", "low")])
        _publish_revise(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "high"}, "rationale": "r",
        }])
        (tmp_path / "review-report.md").write_text("# report")
        state = {}

        self._step_11(tmp_path, state)
        self._step_11(tmp_path, state)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == [self._NOTE]
        assert state["step_11_degradation_records"] == [{
            "code": "critic_adjudication_missing",
            "message": self._NOTE,
        }]

    def test_an_adjudicated_proposal_is_silent(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "low")], verdict="approve")
        _publish_and_adjudicate(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "critical"}, "rationale": "r",
        }], verified=(0,), assessment="One critical finding stands.")
        (tmp_path / "review-report.md").write_text("# report")

        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []
        assert result["status"] == "success"
        assert result["verdict"] == "REQUEST_CHANGES", (
            "the derived verdict must come from the adjudicated ledger"
        )

    def test_a_non_revise_verdict_is_never_inspected(self, tmp_path):
        """Adjustments are a REVISE-only channel, and a non-REVISE marker
        cannot commit a non-empty proposal, so there is nothing to check."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        _publish_verdict(tmp_path, "STAND")
        (tmp_path / "review-report.md").write_text("# report")

        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []
        assert result["status"] == "success"

    def test_an_unreadable_proposal_is_a_missing_verdict_not_a_crash(
        self, tmp_path
    ):
        """An unbound snapshot has no usable verdict at all, so it degrades
        as the lost critique it is rather than as an adjustment problem."""
        from review import synthesis_lifecycle

        _write_findings(tmp_path, [_finding("f1", "low")])
        _publish_revise(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        (tmp_path / "decision-critic-adjustments.json").write_text("{not json")
        synthesis_lifecycle.mark_dispatched(
            str(tmp_path), synthesis_lifecycle.DECISION_CRITIC
        )
        (tmp_path / "review-report.md").write_text("# report")

        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == [
            "critic was dispatched but produced no verdict"
        ]
        assert result["status"] == "degraded"

    def test_a_malformed_ledger_degrades_instead_of_crashing(self, tmp_path):
        """The measured regression: a list-shaped findings file must not
        make the inspection the thing that crashes finalize."""
        _publish_revise(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        (tmp_path / "review-findings.json").write_text(
            json.dumps([_finding("f1", "low")])
        )
        (tmp_path / "review-report.md").write_text("# report")

        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any("critic adjustment inspection failed" in note
                   for note in result["degradation_notes"])
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
        _publish_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")

    def test_demoted_severity_reaches_the_markdown(self, tmp_path):
        """THE pin: a REVISE demote must be visible in the rendered file."""
        self._seed(tmp_path, severity="high")
        (tmp_path / "review-findings.md").write_text(
            "## High Issues\n\n### Unescaped output\n"
        )
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }], verified=(0,))

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
        _publish_verdict(tmp_path, "STAND")

        self._step_11(tmp_path)

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "approve"
        assert "**Verdict:** APPROVE" in (
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
        _publish_verdict(tmp_path, "STAND")
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
        _publish_verdict(tmp_path, "STAND")
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
        _publish_verdict(tmp_path, "STAND")
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
        _publish_verdict(tmp_path, "STAND")
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
        _publish_verdict(tmp_path, "STAND")
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
        _publish_verdict(tmp_path, "STAND")

        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["report_path"] == str(tmp_path / "review-report.md")


class TestAssessmentInvalidation:
    """Prose that summarizes a mutable ledger cannot be corrected, only
    invalidated.

    The critic's vocabulary reaches every field of every finding, but
    `assessment` is ledger-level prose no adjustment can address. A
    demoted critical still described as "one CRITICAL blocker" survives the
    whole correction pipeline and renders directly above the list that
    contradicts it. The pipeline cannot re-derive the prose (it is LLM
    output), so an applying batch withdraws it — auditably.
    """

    _SUMMARY = "One CRITICAL blocker: the payment path is unescaped."

    def _seed(self, tmp_path, severity="critical"):
        _write_findings(
            tmp_path, [_finding("f1", severity)],
            assessment=self._SUMMARY,
        )

    def test_an_applying_batch_withdraws_the_summary(self, tmp_path):
        self._seed(tmp_path)
        _, result = _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }], verified=(0,))
        assert result["applied"] == 1
        assert _ledger(tmp_path)["assessment"] is None

    def test_the_invalidated_text_stays_auditable(self, tmp_path):
        self._seed(tmp_path)
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }], verified=(0,))
        data = _ledger(tmp_path)
        invalidated = data[INVALIDATED_ASSESSMENTS_KEY]
        assert len(invalidated) == 1
        assert invalidated[0]["text"] == self._SUMMARY
        # Tied to the exact decisions that caused it, the same way each
        # touched finding names the action that touched it.
        assert invalidated[0]["invalidated_by_critic_adjustment_ids"] == _applied_ids(data)

    def test_a_second_withdrawal_names_only_its_own_batch(self, tmp_path):
        """invalidated_by_critic_adjustment_ids is causal attribution, not history: a second
        reconciliation round's withdrawal must name the batch that caused
        it, never the cumulative applied-ids list."""
        self._seed(tmp_path)
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "first round",
        }], verified=(0,))
        findings_path = tmp_path / "review-findings.json"
        data = _ledger(tmp_path)
        first_batch = _applied_ids(data)
        # Simulate a re-reconciliation writing fresh prose.
        data["assessment"] = "Fresh assessment after round two."
        write_findings(str(tmp_path), data)
        _publish_and_adjudicate(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "medium"}, "rationale": "second round",
        }], verified=(0,))
        data = _ledger(tmp_path)
        invalidated = data[INVALIDATED_ASSESSMENTS_KEY]
        assert len(invalidated) == 2
        second_batch = [
            i for i in _applied_ids(data) if i not in first_batch
        ]
        assert second_batch
        assert invalidated[1]["invalidated_by_critic_adjustment_ids"] == second_batch
        assert invalidated[0]["invalidated_by_critic_adjustment_ids"] == first_batch

    def test_a_wholly_refuted_batch_leaves_the_summary_alone(self, tmp_path):
        self._seed(tmp_path)
        _, result = _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "r",
        }], refuted=((0, "the probe refuted it"),))
        assert result["applied"] == 0
        data = _ledger(tmp_path)
        assert data["assessment"] == self._SUMMARY
        assert INVALIDATED_ASSESSMENTS_KEY not in data

    def test_a_refused_call_leaves_the_summary_alone(self, tmp_path):
        self._seed(tmp_path)
        _publish_verdict(tmp_path, "STAND")
        with pytest.raises(ValueError, match="STAND"):
            _adjudicate(tmp_path, [])
        assert _ledger(tmp_path)["assessment"] == self._SUMMARY

    def test_no_summary_to_withdraw_records_no_withdrawal(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "critical")])
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "r",
        }], verified=(0,))
        data = _ledger(tmp_path)
        assert data["assessment"] is None
        assert INVALIDATED_ASSESSMENTS_KEY not in data

    def test_a_second_batch_appends_rather_than_overwrites(self, tmp_path):
        """Two rounds of adjustments are two withdrawals — the first must
        not be erased by the second."""
        self._seed(tmp_path)
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "r",
        }], verified=(0,))
        # A second reconciliation pass writes fresh prose, then a second
        # critic round adjusts again.
        data = _ledger(tmp_path)
        data["assessment"] = "Second assessment."
        write_findings(str(tmp_path), data)
        _publish_and_adjudicate(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "high"}, "rationale": "r2",
        }], verified=(0,))
        data = _ledger(tmp_path)
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
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }], verified=(0,))
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

    def test_adjudication_preserves_checks(self, tmp_path):
        _write_findings(
            tmp_path, [_finding("f1")], checks=self.CHECKS
        )
        _, result = _publish_and_adjudicate(tmp_path, [
            {"action": "promote", "target": {"kind": "finding", "id": "f1"},
             "fields": {"severity": "high"}, "rationale": "r"},
        ], verified=(0,))

        assert result["applied"] == 1
        assert _ledger(tmp_path)["checks"] == self.CHECKS

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
        _write_findings(tmp_path, [_finding("f1")], checks=self.CHECKS)
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
        assert "builder.record_check(" in text
        for kwarg in (
            "question=", "method=", "result=", "source_reviewers="
        ):
            assert kwarg in text.split("builder.record_check(", 1)[1][:500]

    def test_the_template_excludes_void_and_correlated_checks(self):
        text = self._text()
        taught = text.split("builder.record_check(", 1)[0]
        assert "Do NOT record" in taught
        assert "VOID" in taught
        assert "method-correlated duplicates" in taught

    def test_the_structured_home_table_lists_checks(self):
        assert (
            "| `record_check(...)` → `## Verified Checks` |"
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
        taught = self._text().split("builder.record_check(", 1)[0]
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

    def test_the_snippet_builds_the_ledger_with_the_ledger_builder(self):
        """`ReviewOutputBuilder` produces a reviewer document — it carries a
        `reviewer` field and a reviewed-file lifecycle the ledger does not
        have. Only `FindingsLedgerBuilder` produces the artifact this agent
        is asked for."""
        text = self._text()
        assert 'FindingsLedgerBuilder(pr_id="PR_ID_FROM_CONTEXT", output_dir=' in text
        assert "from review.findings_ledger import FindingsLedgerBuilder" in text
        assert "ReviewOutputBuilder(" not in text

    def test_the_snippet_authors_the_four_judgments_and_nothing_else(self):
        """The pipeline-owned facts are stamped by findings_save.py from
        reconciliation-context.json. A snippet that teaches the agent to
        author them produces a ledger the save channel rejects."""
        text = self._text()
        call = text.split("builder.set_reconciliation(", 1)[1].split("\n)", 1)[0]
        for judgment in (
            "grouped_concern_count", "verified_concern_count",
            "false_positive_concern_count", "out_of_scope_concern_count",
        ):
            assert f"{judgment}=" in call, judgment
        assert "output['meta']['reconciliation']" not in text
        assert 'output["meta"]["reconciliation"]' not in text



# =============================================================================
# Orchestrator judgment in the adjustments channel
# =============================================================================

class TestOutcomeVocabulary:
    """The per-entry outcome is script-derived from the request and lands
    in the ledger — never on the proposal entry."""

    def _ledger_with(self, applied=None, rejected=None):
        ledger = canonical_findings_ledger(("high",))
        ledger["findings"][0]["critic_adjustment"] = {
            "action": "demote", "rationale": "guarded upstream",
            "prior": {"severity": "critical"},
        }
        ledger[APPLIED_IDS_KEY] = applied if applied is not None else [
            {"adjustment_id": "a1", "outcome": OUTCOME_VERIFIED},
        ]
        if rejected is not None:
            ledger[REJECTED_ADJUSTMENTS_KEY] = rejected
        return ledger

    @pytest.mark.parametrize("value", [OUTCOME_VERIFIED, OUTCOME_NOT_CHECKED])
    def test_each_applied_outcome_is_accepted(self, value):
        validate_findings_document(self._ledger_with(
            applied=[{"adjustment_id": "a1", "outcome": value}]
        ))

    @pytest.mark.parametrize("value", [
        "checked", "VERIFIED", "not checked", "", True, 1, None,
    ])
    def test_an_unknown_value_rejects_the_ledger(self, value):
        with pytest.raises(ValueError, match="applied_critic_adjustments"):
            validate_findings_document(self._ledger_with(
                applied=[{"adjustment_id": "a1", "outcome": value}]
            ))

    def test_an_applied_record_may_not_claim_refuted(self):
        """`refuted` belongs to the rejected list, and nowhere else."""
        with pytest.raises(ValueError, match="applied outcomes are invalid"):
            validate_findings_document(self._ledger_with(
                applied=[{"adjustment_id": "a1", "outcome": OUTCOME_REFUTED}]
            ))

    def test_a_rejected_record_must_claim_refuted(self):
        with pytest.raises(ValueError, match="rejected_critic_adjustments"):
            validate_findings_document(self._ledger_with(rejected=[{
                "adjustment_id": "a2", "action": "demote",
                "target": {"kind": "finding", "id": "f1"},
                "outcome": OUTCOME_VERIFIED,
                "rejection_reason": "the probe refuted it",
            }]))

    def test_a_complete_rejected_record_is_accepted(self):
        validate_findings_document(self._ledger_with(rejected=[{
            "adjustment_id": "a2", "action": "demote",
            "target": {"kind": "finding", "id": "f1"},
            "outcome": OUTCOME_REFUTED,
            "rejection_reason": "the probe refuted it",
        }]))

    def test_the_critic_proposal_gate_rejects_an_outcome(self):
        problems = validate_proposal_input({
            "schema": 2,
            "adjustments": [{
                "action": "demote",
                "target": {"kind": "finding", "id": "f1"},
                "fields": {"severity": "low"},
                "rationale": "guarded upstream",
                "outcome": "verified",
            }],
        })
        assert any("outcome" in problem for problem in problems)


class TestOutcomeRecordedInTheLedger:
    """The applied-ids record carries the orchestrator's outcome per id."""

    def _adjudicate(self, tmp_path, **request_kwargs):
        _write_findings(tmp_path, [_finding("f1", "high")])
        _, result = _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }], **request_kwargs)
        assert result["applied"] == 1
        return _ledger(tmp_path)

    def test_an_omitted_entry_records_not_checked(self, tmp_path):
        data = self._adjudicate(tmp_path)
        assert data[APPLIED_IDS_KEY][0]["outcome"] == OUTCOME_NOT_CHECKED

    def test_a_verified_entry_records_verified(self, tmp_path):
        data = self._adjudicate(tmp_path, verified=(0,))
        assert data[APPLIED_IDS_KEY][0]["outcome"] == OUTCOME_VERIFIED

    def test_the_record_still_carries_the_adjustment_id(self, tmp_path):
        data = self._adjudicate(tmp_path)
        adjustments = json.loads(
            (tmp_path / "decision-critic-adjustments.json").read_text()
        )
        assert data[APPLIED_IDS_KEY][0]["adjustment_id"] == (
            adjustments["adjustments"][0]["adjustment_id"]
        )

    def test_a_schema_one_string_record_is_rejected(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "high")])
        data = _ledger(tmp_path)
        data[APPLIED_IDS_KEY] = ["legacy-id"]
        write_findings(str(tmp_path), data)
        ids = _publish_revise(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "already landed",
        }])
        with pytest.raises(
            ValueError, match="'applied_critic_adjustments' must be a list"
        ):
            _adjudicate(tmp_path, ids)


class TestRevisedAssessment:
    """The orchestrator's post-critic assessment, in the channel.

    An applying batch withdraws the reconciler's `assessment` and
    nothing used to replace it, so a REVISE run published a ledger whose
    Assessment section pointed at a report the machine could not read.
    """

    _SUMMARY = "One CRITICAL blocker: the payment path is unescaped."
    _REVISED = "After spot-checking: the blocker is guarded upstream."

    _DEMOTION = [{
        "action": "demote", "target": {"kind": "finding", "id": "f1"},
        "fields": {"severity": "low"}, "rationale": "guarded upstream",
    }]

    def _seed(self, tmp_path):
        _write_findings(
            tmp_path, [_finding("f1", "critical")],
            assessment=self._SUMMARY,
        )

    def test_a_non_string_revised_assessment_rejects_the_proposal(self):
        problems = validate_proposal_input({
            "schema": 2, "adjustments": [], "revised_assessment": ["a", "b"],
        })
        assert problems and "revised_assessment" in problems[0]

    def test_it_becomes_the_ledger_assessment(self, tmp_path):
        self._seed(tmp_path)
        _publish_and_adjudicate(
            tmp_path, self._DEMOTION,
            verified=(0,), assessment=self._REVISED,
        )
        assert _ledger(tmp_path)["assessment"] == self._REVISED

    def test_the_withdrawal_record_survives_the_replacement(self, tmp_path):
        """Replacement is not erasure: the reconciler's retracted words
        stay auditable beside the ids that cost them their standing."""
        self._seed(tmp_path)
        _publish_and_adjudicate(
            tmp_path, self._DEMOTION,
            verified=(0,), assessment=self._REVISED,
        )
        data = _ledger(tmp_path)
        assert data[INVALIDATED_ASSESSMENTS_KEY][0]["text"] == self._SUMMARY

    def test_a_blank_revised_assessment_is_rejected_without_mutation(
        self, tmp_path
    ):
        self._seed(tmp_path)
        ids = _publish_revise(tmp_path, self._DEMOTION)
        with pytest.raises(ValueError, match="revised_assessment"):
            _adjudicate(tmp_path, ids, verified=(0,), assessment="   ")
        assert _ledger(tmp_path)["assessment"] == self._SUMMARY

    def test_a_wholly_refuted_batch_never_replaces_the_summary(self, tmp_path):
        self._seed(tmp_path)
        _publish_and_adjudicate(
            tmp_path, self._DEMOTION,
            refuted=((0, "The probe refuted this proposal."),),
            assessment=self._REVISED,
        )
        assert _ledger(tmp_path)["assessment"] == self._SUMMARY


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
            "reviewed_file_count": 1,
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
            {"text": "One CRITICAL blocker.", "invalidated_by_critic_adjustment_ids": ["a1"]},
        ])
        assert "No current assessment" in md
        assert "not replaced" in md
        assert "One CRITICAL blocker." not in md

    def test_a_replacement_is_not_attributed_to_the_reconciler(self):
        md = self._render(
            assessment="After spot-checking: guarded upstream.",
            invalidated_assessments=[
                {"text": "One CRITICAL blocker.", "invalidated_by_critic_adjustment_ids": ["a1"]},
            ],
        )
        assert "After spot-checking: guarded upstream." in md
        assert "not adjusted by the decision critic" not in md

    def test_an_untouched_assessment_still_reads_as_the_reconcilers(self):
        md = self._render(assessment="The change is sound.")
        assert "not adjusted by the decision critic" in md

    def test_outcomes_render_per_id(self):
        md = self._render(applied_critic_adjustments=[
            {"adjustment_id": "aaaa", "outcome": "verified"},
            {"adjustment_id": "bbbb", "outcome": "not_checked"},
        ])
        assert "aaaa" in md and "verified" in md
        assert "bbbb" in md and "not_checked" in md

    def test_mixed_applied_and_refuted_decisions_render_per_id(self):
        md = self._render(
            applied_critic_adjustments=[
                {"adjustment_id": "aaaa", "outcome": "verified"},
            ],
            rejected_critic_adjustments=[
                {
                    "adjustment_id": "bbbb",
                    "action": "remove",
                    "target": {"kind": "finding", "id": "f1"},
                    "outcome": "refuted",
                    "rejection_reason": "refuted",
                },
            ],
        )
        assert "## Critic Adjustment Decisions" in md
        assert "- `aaaa` — verified" in md
        assert "- `bbbb` — refuted" in md

    def test_all_refuted_decisions_still_render(self):
        md = self._render(rejected_critic_adjustments=[
            {
                "adjustment_id": "aaaa", "action": "remove",
                "target": {"kind": "finding", "id": "f1"},
                "outcome": "refuted", "rejection_reason": "refuted",
            },
            {
                "adjustment_id": "bbbb", "action": "correct",
                "target": {"kind": "check", "id": "c1"},
                "outcome": "refuted", "rejection_reason": "not true",
            },
        ])
        assert "## Critic Adjustment Decisions" in md
        assert "- `aaaa` — refuted" in md
        assert "- `bbbb` — refuted" in md

    def test_schema_one_applied_ids_do_not_render(self):
        md = self._render(applied_critic_adjustments=["legacy-id"])
        assert "Critic Adjustment Decisions" not in md

    def test_malformed_decision_records_are_ignored(self):
        md = self._render(
            applied_critic_adjustments=[
                None, "", {"outcome": "verified"},
                {"adjustment_id": 7, "outcome": "verified"},
                {"adjustment_id": "bad", "outcome": []},
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

    def test_demoting_the_last_high_moves_the_verdict(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "high")],
                        verdict="request_changes")
        _, result = _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }], verified=(0,))
        assert result["verdict"] == "approve"
        assert _ledger(tmp_path)["verdict"] == "approve"

    def test_promoting_to_critical_blocks(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "medium")],
                        verdict="comment")
        _publish_and_adjudicate(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "critical"}, "rationale": "unguarded",
        }], verified=(0,))
        assert _ledger(tmp_path)["verdict"] == "block"

    def test_unrelated_adjustment_keeps_advisory_high_non_gating(
        self, tmp_path
    ):
        advisory = _finding("f1", "high")
        advisory["channel"] = "advisory"
        _write_findings(
            tmp_path,
            [advisory, _finding("f2", "low")],
            verdict="approve",
        )
        _publish_and_adjudicate(tmp_path, [{
            "action": "correct", "target": {"kind": "finding", "id": "f2"},
            "fields": {"title": "corrected title"},
            "rationale": "clarify the existing finding",
        }], verified=(0,))

        data = _ledger(tmp_path)
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
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }], verified=(0,))
        assert _ledger(tmp_path)["verdict_before_adjustments"] == (
            "request_changes"
        )

    def test_the_audit_trail_records_only_the_first_change(self, tmp_path):
        """A second round must name what the ledger came in as, not what
        the previous round left behind."""
        _write_findings(tmp_path, [_finding("f1", "high")],
                        verdict="request_changes")
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "round one",
        }], verified=(0,))
        _publish_and_adjudicate(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "medium"}, "rationale": "round two",
        }], verified=(0,))
        data = _ledger(tmp_path)
        assert data["verdict"] == "comment"
        assert data["verdict_before_adjustments"] == "request_changes"

    def test_a_stale_ledger_verdict_is_refused_at_the_reader(self, tmp_path):
        """The reader boundary refuses stale verdicts before any consumer."""
        _write_findings(tmp_path, [_finding("f1", "high")],
                        verdict="deliberately-stale")
        ids = _publish_revise(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "r",
        }])
        before = (tmp_path / "review-findings.json").read_bytes()

        with pytest.raises(ValueError, match="verdict does not match"):
            _adjudicate(tmp_path, ids, verified=(0,))

        assert (tmp_path / "review-findings.json").read_bytes() == before

    def test_an_unchanged_verdict_records_no_audit_trail(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "high"),
                                   _finding("f2", "high")],
                        verdict="request_changes")
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }], verified=(0,))
        data = _ledger(tmp_path)
        assert data["verdict"] == "request_changes"
        assert "verdict_before_adjustments" not in data


# =============================================================================
# Source-bound critic proposal -> adjudication -> ledger lifecycle
# =============================================================================


class TestSchemaTwoTargetUnion:
    @staticmethod
    def _entry(action, *, kind="finding", id_="f1", fields=None):
        target = {"kind": kind}
        if id_ is not None:
            target["id"] = id_
        return {
            "action": action,
            "target": target,
            "fields": {} if fields is None else fields,
            "rationale": "Verified correction.",
        }

    @staticmethod
    def _commit(tmp_path, adjustments, *, verdict="REVISE"):
        proposal = critic_adjustments_module.prepare_proposal({
            "schema": 2,
            "adjustments": adjustments if verdict == "REVISE" else [],
        })
        write_critic_verdict(str(tmp_path), verdict, proposal)
        return proposal

    @staticmethod
    def _adjudicate(
        tmp_path, proposal, *, verified=(0,), refuted=(), assessment=None
    ):
        ids = [entry["adjustment_id"] for entry in proposal["adjustments"]]
        return _adjudicate(
            tmp_path, ids,
            verified=verified, refuted=refuted, assessment=assessment,
        )

    @pytest.mark.parametrize(
        ("action", "fields"),
        [
            ("promote", {"severity": "high"}),
            ("demote", {"severity": "low"}),
            ("rescope", {"file": "src/b.py", "line": 20}),
            ("correct", {"description": "Corrected description."}),
            ("remove", {}),
        ],
    )
    def test_finding_mutations_require_kind_and_id(self, action, fields):
        payload = {
            "schema": 2,
            "adjustments": [self._entry(action, fields=fields)],
        }

        assert validate_proposal_input(payload) == []

    def test_add_finding_has_no_caller_supplied_id(self):
        entry = self._entry(
            "add",
            id_=None,
            fields={
                "severity": "high",
                "title": "Missing authorization",
                "file": "src/api.py",
                "line": 42,
                "description": "State changes before authorization.",
                "recommendation": "Authorize before mutation.",
                "category": "security",
                "confidence": 0.98,
            },
        )
        payload = {"schema": 2, "adjustments": [entry]}

        assert validate_proposal_input(payload) == []
        entry["target"]["id"] = "f9"
        assert "must not include id" in " ".join(
            validate_proposal_input(payload)
        )

    @pytest.mark.parametrize("action", ["add", "correct"])
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("severity", "urgent"),
            ("title", None),
            ("description", []),
            ("recommendation", 7),
            ("file", None),
            ("line", 0),
            ("category", {}),
            ("confidence", True),
            ("confidence", -0.01),
            ("confidence", 1.01),
        ],
    )
    def test_finding_content_values_follow_the_canonical_domain_contract(
        self, action, field, value
    ):
        fields = {
            "severity": "medium",
            "title": "Missing validation",
            "file": "src/api.py",
            "line": 42,
            "description": "The input reaches mutation unchecked.",
            "recommendation": "Validate before mutation.",
            "category": "security",
            "confidence": 0.9,
        }
        if action == "correct":
            fields = {field: value}
        else:
            fields[field] = value
        payload = {
            "schema": 2,
            "adjustments": [self._entry(
                action,
                id_=None if action == "add" else "f1",
                fields=fields,
            )],
        }

        problems = validate_proposal_input(payload)

        assert problems
        assert field in " ".join(problems)

    @pytest.mark.parametrize("action", ["add", "correct"])
    def test_line_is_the_nullable_finding_content_field(self, action):
        fields = {
            "severity": "medium",
            "title": "Missing validation",
            "file": "src/api.py",
            "line": None,
            "description": "The input reaches mutation unchecked.",
            "recommendation": "Validate before mutation.",
        }
        if action == "correct":
            fields = {"line": None}
        payload = {
            "schema": 2,
            "adjustments": [self._entry(
                action,
                id_=None if action == "add" else "f1",
                fields=fields,
            )],
        }

        assert validate_proposal_input(payload) == []

    @pytest.mark.parametrize("action", ["add", "correct"])
    def test_an_invalid_planned_finding_leaves_the_ledger_unchanged(
        self, tmp_path, action
    ):
        _write_findings(tmp_path, [_finding("f1")])
        entry = self._entry(
            action,
            id_=None if action == "add" else "f1",
            fields={
                "severity": "medium",
                "title": "Invalid file",
                "file": None,
                "description": "The file value violates the domain.",
                "recommendation": "Name the affected file.",
            } if action == "add" else {"file": None},
        )
        entry["adjustment_id"] = "invalid-file"
        _publish_raw_proposal(
            tmp_path, {"schema": 2, "adjustments": [entry]}
        )
        paths = (
            tmp_path / "decision-critic-adjustments.json",
            tmp_path / "review-findings.json",
        )
        before = tuple(path.read_bytes() for path in paths)

        with pytest.raises(ValueError, match=r"file.*string"):
            _adjudicate(tmp_path, ["invalid-file"], verified=(0,))

        assert tuple(path.read_bytes() for path in paths) == before

    @pytest.mark.parametrize(
        ("action", "fields"),
        [
            ("correct", {"result": "No production caller reaches it."}),
            ("remove", {}),
        ],
    )
    def test_checks_support_only_correction_and_removal(self, action, fields):
        payload = {
            "schema": 2,
            "adjustments": [
                self._entry(action, kind="check", id_="c1", fields=fields)
            ],
        }

        assert validate_proposal_input(payload) == []

    @pytest.mark.parametrize(
        ("action", "fields"),
        [
            ("promote", {"severity": "high"}),
            ("demote", {"severity": "low"}),
            ("rescope", {"file": "src/b.py", "line": 20}),
            (
                "add",
                {
                    "severity": "low",
                    "title": "Invented check",
                    "file": "src/b.py",
                    "description": "The critic did not perform this check.",
                    "recommendation": "Do not add it.",
                },
            ),
        ],
    )
    def test_check_targets_reject_finding_only_actions(self, action, fields):
        payload = {
            "schema": 2,
            "adjustments": [
                self._entry(
                    action,
                    kind="check",
                    id_=None if action == "add" else "c1",
                    fields=fields,
                )
            ],
        }

        problems = validate_proposal_input(payload)

        assert problems
        assert "check" in " ".join(problems)

    @pytest.mark.parametrize("field", ["id", "source_reviewers", "severity"])
    def test_check_correction_rejects_immutable_or_finding_fields(self, field):
        payload = {
            "schema": 2,
            "adjustments": [
                self._entry(
                    "correct",
                    kind="check",
                    id_="c1",
                    fields={field: "replacement"},
                )
            ],
        }

        assert field in " ".join(validate_proposal_input(payload))

    def test_non_add_target_requires_id_and_add_rejects_surplus_id(self):
        missing = {
            "schema": 2,
            "adjustments": [
                self._entry("remove", kind="check", id_=None, fields={})
            ],
        }
        surplus = {
            "schema": 2,
            "adjustments": [self._entry(
                "add",
                id_="f9",
                fields={
                    "severity": "low",
                    "title": "Added finding",
                    "file": "src/b.py",
                    "description": "A verified defect.",
                    "recommendation": "Correct it.",
                },
            )],
        }

        assert "target.id" in " ".join(validate_proposal_input(missing))
        assert "must not include id" in " ".join(
            validate_proposal_input(surplus)
        )

    def test_duplicate_target_is_kind_aware(self):
        duplicate = {
            "schema": 2,
            "adjustments": [
                self._entry(
                    "correct", fields={"description": "First correction."}
                ),
                self._entry(
                    "remove", fields={},
                ),
            ],
        }
        distinct_kinds = {
            "schema": 2,
            "adjustments": [
                self._entry(
                    "correct", fields={"description": "First correction."}
                ),
                self._entry(
                    "correct",
                    kind="check",
                    id_="c1",
                    fields={"result": "Corrected result."},
                ),
            ],
        }

        assert "duplicate target finding 'f1'" in " ".join(
            validate_proposal_input(duplicate)
        )
        assert validate_proposal_input(distinct_kinds) == []

    def test_schema_one_is_rejected_without_a_compatibility_reader(self):
        payload = {
            "schema": 1,
            "adjustments": [
                self._entry("correct", fields={"description": "Corrected."})
            ],
        }

        assert "schema' must be 2" in " ".join(
            validate_proposal_input(payload)
        )

    def test_proposal_digest_commits_the_nested_target(self):
        proposal = critic_adjustments_module.prepare_proposal({
            "schema": 2,
            "adjustments": [
                self._entry("correct", fields={"description": "Corrected."})
            ],
        })
        original = critic_adjustments_module.proposal_digest(proposal)
        edited = json.loads(json.dumps(proposal))

        assert critic_adjustments_module.proposal_digest(edited) == original
        edited["adjustments"][0]["target"]["id"] = "f2"
        assert critic_adjustments_module.proposal_digest(edited) != original

    def test_add_uses_ledger_allocator_and_increments_it(self, tmp_path):
        _write_findings(
            tmp_path,
            [_finding("f1")],
            assessment="Original assessment.",
            meta={
                "review_duration_ms": 10,
                "confidence_score": 0.9,
                "next_finding_number": 7,
                "next_check_number": 1,
            },
        )
        proposal = self._commit(tmp_path, [self._entry(
            "add",
            id_=None,
            fields={
                "severity": "medium",
                "title": "Missing validation",
                "file": "src/api.py",
                "line": 42,
                "description": "The input reaches mutation unchecked.",
                "recommendation": "Validate before mutation.",
            },
        )])

        self._adjudicate(tmp_path, proposal)

        ledger = json.loads((tmp_path / "review-findings.json").read_text())
        assert [finding["id"] for finding in ledger["findings"]] == ["f1", "f7"]
        assert ledger["meta"]["next_finding_number"] == 8
        assert ledger["findings"][1]["critic_adjustment"]["action"] == "add"

    def test_check_correction_captures_only_changed_prior_fields_and_replays(
        self, tmp_path
    ):
        check = _check("c1")
        _write_findings(
            tmp_path,
            [_finding("f1")],
            checks=[check],
            assessment="Original assessment.",
            meta={
                "review_duration_ms": 10,
                "confidence_score": 0.9,
                "next_finding_number": 2,
                "next_check_number": 2,
            },
        )
        proposal = self._commit(tmp_path, [self._entry(
            "correct",
            kind="check",
            id_="c1",
            fields={
                "method": check["method"],
                "result": "No production caller reaches it.",
            },
        )])

        self._adjudicate(
            tmp_path,
            proposal,
            assessment="The corrected check supports the review.",
        )
        ledger_path = tmp_path / "review-findings.json"
        first_bytes = ledger_path.read_bytes()
        ledger = json.loads(first_bytes)
        adjustment_id = proposal["adjustments"][0]["adjustment_id"]
        assert ledger["checks"][0]["source_reviewers"] == [
            "ecosystem-integration"
        ]
        assert ledger["checks"][0]["critic_adjustment"]["prior"] == {
            "result": "No matching callers."
        }
        assert ledger["invalidated_assessments"] == [{
            "text": "Original assessment.",
            "invalidated_by_critic_adjustment_ids": [adjustment_id],
        }]
        assert ledger["assessment"] == (
            "The corrected check supports the review."
        )

        with pytest.raises(ValueError, match="already adjudicated"):
            self._adjudicate(
                tmp_path,
                proposal,
                assessment="The corrected check supports the review.",
            )

        assert ledger_path.read_bytes() == first_bytes

    def test_check_removal_moves_the_complete_entry_to_its_own_container(
        self, tmp_path
    ):
        check = _check("c1")
        _write_findings(
            tmp_path,
            [_finding("f1")],
            checks=[check],
            assessment="Original assessment.",
            meta={
                "review_duration_ms": 10,
                "confidence_score": 0.9,
                "next_finding_number": 2,
                "next_check_number": 2,
            },
        )
        proposal = self._commit(tmp_path, [
            self._entry("remove", kind="check", id_="c1", fields={})
        ])

        self._adjudicate(tmp_path, proposal)

        ledger = json.loads((tmp_path / "review-findings.json").read_text())
        assert ledger["checks"] == []
        removed = ledger["checks_removed_by_critic"]
        assert {key: removed[0][key] for key in check} == check
        assert removed[0]["critic_adjustment"]["action"] == "remove"

    def test_unknown_check_target_is_rejected_before_any_write(self, tmp_path):
        _write_findings(
            tmp_path,
            [_finding("f1")],
            checks=[_check("c1")],
            assessment="Original assessment.",
            meta={
                "review_duration_ms": 10,
                "confidence_score": 0.9,
                "next_finding_number": 2,
                "next_check_number": 2,
            },
        )
        proposal = self._commit(tmp_path, [self._entry(
            "remove", kind="check", id_="c9", fields={}
        )])
        paths = (
            tmp_path / "decision-critic-adjustments.json",
            tmp_path / "review-findings.json",
        )
        before = tuple(path.read_bytes() for path in paths)

        with pytest.raises(ValueError, match="no check with id 'c9'"):
            self._adjudicate(tmp_path, proposal)

        assert tuple(path.read_bytes() for path in paths) == before

    def test_refuted_and_noop_corrections_leave_assessment_untouched(
        self, tmp_path
    ):
        _write_findings(
            tmp_path,
            [_finding("f1")],
            assessment="Original assessment.",
        )
        refuted = self._commit(tmp_path, [self._entry(
            "correct", fields={"description": "Changed description."}
        )])
        self._adjudicate(
            tmp_path,
            refuted,
            verified=(),
            refuted=((0, "Source confirms the original description."),),
        )
        after_refuted = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        assert after_refuted["assessment"] == "Original assessment."
        assert "invalidated_assessments" not in after_refuted

        other_dir = tmp_path / "noop"
        other_dir.mkdir()
        _write_findings(
            other_dir,
            [_finding("f1")],
            assessment="Original assessment.",
        )
        no_op = self._commit(other_dir, [self._entry(
            "correct", fields={"description": "d"}
        )])
        ledger_path = other_dir / "review-findings.json"
        before_noop = ledger_path.read_bytes()

        with pytest.raises(ValueError, match="would not change"):
            self._adjudicate(other_dir, no_op)

        assert ledger_path.read_bytes() == before_noop


class TestProposalPreparation:
    def _entry(self, **extra):
        entry = {
            "action": "demote",
            "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "medium"},
            "rationale": "The claimed impact is narrower than stated.",
        }
        entry.update(extra)
        return entry

    def test_prepare_assigns_unique_stable_ids(self):
        payload = {
            "schema": 2,
            "adjustments": [self._entry(), {
                **self._entry(), "target": {"kind": "finding", "id": "f2"},
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
            "schema": 2,
            "adjustments": [self._entry(), {
                **self._entry(), "target": {"kind": "finding", "id": "f2"},
            }],
        })

        assert [
            entry["adjustment_id"] for entry in proposal["adjustments"]
        ] == ["same", "different"]

    def test_prepare_rejects_duplicate_targets_before_assigning_ids(self):
        with pytest.raises(ValueError, match="duplicate target finding 'f1'"):
            critic_adjustments_module.prepare_proposal({
                "schema": 2,
                "adjustments": [
                    self._entry(),
                    {
                        "action": "correct",
                        "target": {"kind": "finding", "id": "f1"},
                        "fields": {"title": "Clearer title"},
                        "rationale": "Clarify the mechanism.",
                    },
                ],
            })

    @pytest.mark.parametrize(
        "forbidden,value",
        [
            ("adjustment_id", "critic-owned"),
            ("outcome", "verified"),
            ("rejected", True),
            ("rejection_reason", "caller-owned"),
            ("applied", True),
        ],
    )
    def test_prepare_rejects_lifecycle_fields(self, forbidden, value):
        with pytest.raises(ValueError, match=forbidden):
            critic_adjustments_module.prepare_proposal({
                "schema": 2,
                "adjustments": [self._entry(**{forbidden: value})],
            })

    @pytest.mark.parametrize(
        "payload,problem",
        [
            (
                {"schema": 2, "adjustments": [], "revised_assessment": "x"},
                "revised_assessment",
            ),
            (
                {"schema": 2, "adjustments": [], "adjudication": {}},
                "adjudication",
            ),
            (
                {"schema": 2, "adjustments": [], "counts": {}},
                "counts",
            ),
        ],
    )
    def test_prepare_rejects_non_proposal_top_level_fields(
        self, payload, problem
    ):
        with pytest.raises(ValueError, match=problem):
            critic_adjustments_module.prepare_proposal(payload)

    def test_the_digest_covers_every_byte_of_the_proposal(self):
        """The proposal is never rewritten, so the digest has nothing to
        exclude: any edit at all breaks the binding."""
        proposal = critic_adjustments_module.prepare_proposal({
            "schema": 2,
            "adjustments": [self._entry()],
        })
        before = critic_adjustments_module.proposal_digest(proposal)
        proposal["adjustments"][0]["outcome"] = "verified"

        assert critic_adjustments_module.proposal_digest(proposal) != before

    def test_persisted_document_requires_unique_script_assigned_ids(self):
        entry = self._entry()
        missing = {"schema": 2, "adjustments": [entry]}
        duplicate = {
            "schema": 2,
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
            "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "medium"},
            "rationale": "Narrower than stated.",
        },
        {
            "action": "promote",
            "target": {"kind": "finding", "id": "f2"},
            "fields": {"severity": "critical"},
            "rationale": "The source confirms a wider impact.",
        },
        {
            "action": "correct",
            "target": {"kind": "finding", "id": "f3"},
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
        return _publish_revise(tmp_path, self.ENTRIES)

    def test_adjudication_derives_the_unchecked_complement(self, tmp_path):
        ids = self._seed(tmp_path)
        before = (tmp_path / "decision-critic-adjustments.json").read_bytes()

        result = _adjudicate(
            tmp_path, ids,
            verified=(0,),
            refuted=((1, "Refuted by the source probe."),),
            assessment="One proposal landed and one was rejected.",
        )

        assert result["counts"] == {
            "verified": 1, "refuted": 1, "not_checked": 1,
        }
        assert (
            tmp_path / "decision-critic-adjustments.json"
        ).read_bytes() == before
        ledger = _ledger(tmp_path)
        assert [record["outcome"] for record in ledger[APPLIED_IDS_KEY]] == [
            "verified", "not_checked",
        ]
        assert ledger[REJECTED_ADJUSTMENTS_KEY][0]["rejection_reason"] == (
            "Refuted by the source probe."
        )
        assert ledger["assessment"] == (
            "One proposal landed and one was rejected."
        )

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
                lambda request, ids: request.update({"outcome": "verified"}),
                "outcome",
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
            "caller-outcome", "caller-apply-state", "non-string-verified",
            "duplicate-verified", "overlap", "unknown-id", "blank-reason",
            "refuted-extra-key", "blank-assessment",
        ],
    )
    def test_an_invalid_request_leaves_both_files_byte_identical(
        self, tmp_path, mutate, problem
    ):
        ids = self._seed(tmp_path)
        request = _request(ids, verified=(0,))
        mutate(request, ids)
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match=problem):
            adjudicate(str(tmp_path), request)

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    def test_unknown_ledger_target_is_rejected_before_any_write(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1", "low")])
        ids = _publish_revise(tmp_path, [{
            "action": "promote",
            "target": {"kind": "finding", "id": "f9"},
            "fields": {"severity": "high"},
            "rationale": "The proposal points at a missing finding.",
        }])
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match="no finding with id 'f9'"):
            _adjudicate(tmp_path, ids, verified=(0,))

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    def test_duplicate_ledger_target_is_rejected_before_any_write(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1", "low")])
        document = {
            "schema": 2,
            "adjustments": [
                {
                    "adjustment_id": "first",
                    "action": "promote",
                    "target": {"kind": "finding", "id": "f1"},
                    "fields": {"severity": "high"},
                    "rationale": "First mutation.",
                },
                {
                    "adjustment_id": "second",
                    "action": "correct",
                    "target": {"kind": "finding", "id": "f1"},
                    "fields": {"title": "Clearer title"},
                    "rationale": "Second mutation.",
                },
            ],
        }
        _publish_raw_proposal(tmp_path, document)
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match="duplicate target finding 'f1'"):
            _adjudicate(tmp_path, ["first", "second"], verified=(0, 1))

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    def test_a_malformed_ledger_is_rejected_before_any_write(self, tmp_path):
        ids = self._seed(tmp_path)
        ledger = _ledger(tmp_path)
        ledger[APPLIED_IDS_KEY] = "not-a-record-list"
        write_findings(str(tmp_path), ledger)
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(
            ValueError, match="'applied_critic_adjustments' must be a list"
        ):
            _adjudicate(tmp_path, ids, verified=(0,))

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
                "f9",
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
        ids = _publish_revise(tmp_path, [{
            "action": "promote",
            "target": {"kind": "finding", "id": target_id},
            "fields": fields,
            "rationale": "The orchestrator probe will reject this proposal.",
        }])

        result = _adjudicate(
            tmp_path, ids, refuted=((0, rejection_reason),)
        )

        findings = _ledger(tmp_path)
        assert result["counts"] == {
            "verified": 0,
            "refuted": 1,
            "not_checked": 0,
        }
        assert findings["findings"] == [_finding("f1", "high")]
        assert findings[REJECTED_ADJUSTMENTS_KEY] == [{
            "adjustment_id": ids[0],
            "action": "promote",
            "target": {"kind": "finding", "id": target_id},
            "outcome": "refuted",
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
                "rescope", "low", {"file": "f.go", "line": 10},
                "rescope would not change the finding",
            ),
        ],
    )
    def test_a_noop_or_wrong_direction_proposal_is_rejected_before_any_write(
        self, tmp_path, action, current, fields, problem
    ):
        _write_findings(tmp_path, [_finding("f1", current)])
        ids = _publish_revise(tmp_path, [{
            "action": action,
            "target": {"kind": "finding", "id": "f1"},
            "fields": fields,
            "rationale": "This mutation is not coherent with the ledger.",
        }])
        adj_path = tmp_path / "decision-critic-adjustments.json"
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match=problem):
            _adjudicate(tmp_path, ids, verified=(0,))

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before


class TestPublicationAndAdjudicationShareOneLock:
    """critic.py's publication and `adjudicate` hold the same output-directory
    lock, so neither can observe the other's files half-written."""

    def test_save_and_adjudicate_cannot_interleave_snapshots(
        self, tmp_path, monkeypatch
    ):
        from review import critic as critic_module

        _write_findings(tmp_path, [
            _finding("f1", "high"), _finding("f2", "low")
        ])
        old_ids = _publish_revise(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "Guarded upstream.",
        }])
        findings_source = tmp_path / "new-critic-findings.md"
        findings_source.write_text("# New findings\n")
        proposal_source = tmp_path / "new-proposal.json"
        proposal_source.write_text(json.dumps({
            "schema": 2,
            "adjustments": [{
                "action": "promote",
                "target": {"kind": "finding", "id": "f2"},
                "fields": {"severity": "high"},
                "rationale": "New source evidence.",
            }],
        }))
        lock = threading.Lock()
        save_inside_write = threading.Event()
        release_save = threading.Event()
        real_write = critic_adjustments_module.write_critic_verdict

        @contextmanager
        def thread_lock(_output_dir):
            with lock:
                yield

        def blocking_write(output_dir, verdict, proposal):
            if threading.current_thread().name == "critic-save":
                save_inside_write.set()
                assert release_save.wait(timeout=2)
            return real_write(output_dir, verdict, proposal)

        monkeypatch.setattr(
            critic_adjustments_module.atomic_io,
            "output_dir_lock",
            thread_lock,
        )
        monkeypatch.setattr(
            critic_adjustments_module, "write_critic_verdict", blocking_write
        )
        monkeypatch.setattr(
            critic_module.critic_adjustments, "write_critic_verdict",
            blocking_write, raising=False,
        )
        results = {}

        def run_save():
            results["save"] = critic_module.run_save(type("Args", (), {
                "output_dir": str(tmp_path),
                "verdict": "REVISE",
                "findings": str(findings_source),
                "adjustments": str(proposal_source),
            })())

        def run_adjudicate():
            try:
                results["adjudicated"] = _adjudicate(
                    tmp_path, old_ids, verified=(0,)
                )
            except ValueError as error:
                results["error"] = str(error)

        save_thread = threading.Thread(target=run_save, name="critic-save")
        adjudicate_thread = threading.Thread(
            target=run_adjudicate, name="adjudicate"
        )
        save_thread.start()
        assert save_inside_write.wait(timeout=2)
        adjudicate_thread.start()
        time.sleep(0.05)
        assert "adjudicated" not in results and "error" not in results
        release_save.set()
        save_thread.join(timeout=2)
        adjudicate_thread.join(timeout=2)

        assert results["save"] == 0
        assert "error" in results, (
            "the superseded proposal's ids are not in the new one"
        )
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
    """Step 10's REVISE briefing shells out to this as a script, so the
    process contract (exit status, stdout lines) is part of the interface."""

    def _seed(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "high")])
        return _publish_revise(tmp_path, [{
            "action": "demote",
            "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"},
            "rationale": "Guarded upstream.",
        }])

    def _run(self, tmp_path, request):
        return subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH), "adjudicate",
                "--output-dir", str(tmp_path),
            ],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_it_echoes_the_derived_counts_and_the_ledger_verdict(
        self, tmp_path
    ):
        ids = self._seed(tmp_path)

        result = self._run(tmp_path, _request(
            ids, verified=(0,), assessment="The blocker is guarded upstream."
        ))

        assert result.returncode == 0, result.stdout + result.stderr
        assert "RECORDED ADJUDICATION: 1" in result.stdout
        assert "VERIFIED: 1 | REFUTED: 0 | NOT_CHECKED: 0" in result.stdout
        assert "REVISED ASSESSMENT: present" in result.stdout
        assert "APPLIED: 1 | REJECTED: 0" in result.stdout
        assert "LEDGER VERDICT: approve" in result.stdout

    def test_an_omitted_assessment_is_reported_absent(self, tmp_path):
        ids = self._seed(tmp_path)

        result = self._run(tmp_path, _request(ids, verified=(0,)))

        assert result.returncode == 0, result.stdout + result.stderr
        assert "REVISED ASSESSMENT: absent" in result.stdout

    def test_a_second_adjudication_is_refused_on_stdout(self, tmp_path):
        ids = self._seed(tmp_path)
        request = _request(ids, verified=(0,))
        first = self._run(tmp_path, request)
        assert first.returncode == 0, first.stdout + first.stderr
        settled = (tmp_path / "review-findings.json").read_bytes()

        second = self._run(tmp_path, request)

        assert second.returncode == 1
        assert second.stdout.startswith("REJECTED:")
        assert "already adjudicated" in second.stdout
        assert "Traceback" not in second.stderr
        assert (tmp_path / "review-findings.json").read_bytes() == settled

    def test_an_invalid_request_is_rejected_line_by_line(self, tmp_path):
        self._seed(tmp_path)

        result = self._run(tmp_path, {
            "schema": 2, "verified": ["unknown-id"], "refuted": [],
            "revised_assessment": None,
        })

        assert result.returncode == 1
        assert "REJECTED: unknown adjustment id 'unknown-id'" in result.stdout
        assert "Traceback" not in result.stderr

    def test_unparseable_stdin_is_rejected_cleanly(self, tmp_path):
        self._seed(tmp_path)

        result = subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH), "adjudicate",
                "--output-dir", str(tmp_path),
            ],
            input="{not json", capture_output=True, text=True, timeout=10,
        )

        assert result.returncode == 1
        assert "REJECTED: adjudication request is not valid JSON" in (
            result.stdout
        )

    @pytest.mark.parametrize(
        "argv", [[], ["apply"], ["settle"]],
        ids=("bare", "retired-apply", "retired-settle"),
    )
    def test_only_the_adjudicate_subcommand_exists(self, tmp_path, argv):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *argv,
             "--output-dir", str(tmp_path)],
            capture_output=True, text=True, timeout=10,
        )

        assert result.returncode != 0

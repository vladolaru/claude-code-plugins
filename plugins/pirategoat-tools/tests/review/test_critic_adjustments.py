"""Tests for critic_adjustments — the sole writer that carries decision-critic
finding-level decisions into review-findings.json."""

import json
import re
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "review" / "critic_adjustments.py"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(TESTS_DIR))

from helpers.review_fixtures import (
    apply_schema,
    canonical_findings_ledger,
    rejected_schema_values,
)
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
from review.orchestration import _orchestrate_step_11
from review.run_paths import artifact_path
from review.verdict_rules import derive_review_state


from helpers.review_fixtures import artifact_file as _artifact  # noqa: E402


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
    _artifact(output_dir, "critic_adjustments").write_text(
        json.dumps(document)
    )
    atomic_write_json(
        str(_artifact(output_dir, "critic_verdict")),
        {
            "schema": 2,
            "verdict": verdict,
            "proposal_digest": critic_adjustments_module.proposal_digest(
                document
            ),
        },
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

    def test_reviewer_envelope_fields_are_rejected_on_the_ledger(self):
        with pytest.raises(ValueError):
            validate_findings_document(
                {**canonical_findings_ledger(("high",)), "reviewer": "reconciliator"}
            )

    def test_reconciliation_counts_must_partition_grouped(self):
        ledger = canonical_findings_ledger(("high",), reconciliation={
            "grouped_concern_count": 5, "verified_concern_count": 1,
            "false_positive_concern_count": 3, "out_of_scope_concern_count": 0,
            "input_finding_count": 6,
        })
        with pytest.raises(ValueError, match="grouped_concern_count"):
            validate_findings_document(ledger)

    def test_reconciliation_agent_names_follow_the_dispatch_grammar(self):
        """A name outside `[a-z0-9][a-z0-9-]*` used to pass here and then
        null the whole reconciliation block in the offline metrics report,
        so the ledger is where it has to be refused. Fix 2e0fcec5."""
        with pytest.raises(ValueError, match="agent name"):
            validate_findings_document(canonical_findings_ledger(
                reconciliation={"not_applicable_agents": [
                    {"name": "A11y Reviewer", "skip_reason": "no UI changed"},
                ]},
            ))

    @pytest.mark.parametrize(
        "skip_reason",
        ["", "no UI\x07 changed"],
        ids=("empty", "control-character"),
    )
    def test_not_applicable_skip_reasons_are_bounded_text(self, skip_reason):
        """Fix 2e0fcec5."""
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

    def test_object_shaped_noncanonical_ledgers_are_invalid(self, tmp_path):
        payload = {"schema": 2, "verdict": "approve", "findings": "none"}
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
        proposal_before = _artifact(
            tmp_path, "critic_adjustments"
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
        assert _artifact(
            tmp_path, "critic_adjustments"
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
        path = _artifact(tmp_path, "critic_adjustments")
        proposal = json.loads(path.read_text())
        proposal["adjustments"][0]["fields"]["severity"] = "info"
        path.write_text(json.dumps(proposal))

        with pytest.raises(ValueError, match="digest mismatch"):
            _adjudicate(tmp_path, ids, verified=(0,))
        assert read_critic_verdict(str(tmp_path)) is None


class TestApplyAdjustments:
    @pytest.mark.parametrize(
        "action,before,after,verdict",
        [("promote", "low", "high", "request_changes"),
         ("demote", "high", "low", "approve")],
    )
    def test_severity_and_content_corrections_apply_atomically(
        self, tmp_path, action, before, after, verdict
    ):
        _write_findings(tmp_path, [_finding("f1", before)])
        ids, result = _publish_and_adjudicate(tmp_path, [{
            "action": action,
            "target": {"kind": "finding", "id": "f1"},
            "fields": {
                "severity": after, "title": "Corrected title",
                "description": "Corrected reachability.",
                "recommendation": "Check the caller.", "file": "caller.go",
                "line": None, "category": "general", "confidence": 0.95,
            },
            "rationale": "The caller changes the impact and location.",
        }], verified=(0,))

        assert result["applied"] == 1
        ledger = _ledger(tmp_path)
        finding = ledger["findings"][0]
        assert finding["severity"] == after
        assert finding["title"] == "Corrected title"
        assert finding["file"] == "caller.go"
        assert finding["line"] is None
        assert finding["scope"] == "file"
        assert finding["critic_adjustment"] == {
            "action": action,
            "rationale": "The caller changes the impact and location.",
            "prior": {
                "severity": before, "title": "t", "description": "d",
                "recommendation": "r", "file": "f.go", "line": 10,
                "confidence": 0.9,
            },
        }
        assert ledger[APPLIED_IDS_KEY] == [
            {"adjustment_id": ids[0], "outcome": "verified"}
        ]
        assert ledger["verdict"] == verdict

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

class TestBatchCoherence:
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

    def test_add_rejects_a_critic_supplied_id_in_fields(self, tmp_path):
        """The `target.id` spelling of this refusal is pinned by
        `TestSchemaTwoTargetUnion::test_non_add_target_requires_id_and_add_rejects_surplus_id`;
        this is the other spelling, a caller-supplied `id` smuggled into
        `fields` instead."""
        _write_findings(tmp_path, [_finding("f1")])
        base_fields = {"severity": "low", "title": "t", "file": "f.go",
                       "description": "d", "recommendation": "r"}
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

    def test_findings_that_is_not_an_object_fails_as_a_value_error(
        self, tmp_path
    ):
        """The adjustments file is shape-guarded; the findings file was
        not, so a non-object ledger died on an AttributeError outside this
        module's ValueError contract — the one step 11 catches. Fix
        81ac20af."""
        shape = [{"id": "f1"}]
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


def _one_adjustment(action, fields, target_id="f1"):
    """One-entry proposal payload for `TestValidateProposalInput`'s table."""
    target = {"kind": "finding"}
    if target_id is not None:
        target["id"] = target_id
    return {
        "schema": 2,
        "adjustments": [{
            "action": action, "target": target, "fields": fields,
            "rationale": "r",
        }],
    }


class TestValidateProposalInput:
    """Direct unit coverage for the critic-owned proposal validator."""

    proposal_problems = [
        pytest.param(
            _one_adjustment("promote", {"severity": "high"}),
            [], id="valid-batch",
        ),
        pytest.param(
            [1, 2, 3],
            ["decision-critic-adjustments.json must be a JSON object"],
            id="non-object-payload",
        ),
        pytest.param(
            {"schema": 2, "adjustments": "nope"},
            ["decision-critic-adjustments.json: 'adjustments' must be a list"],
            id="adjustments-not-a-list",
        ),
        pytest.param(
            {"schema": 2},
            ["decision-critic-adjustments.json: 'adjustments' must be a list"],
            id="missing-adjustments-key",
        ),
        pytest.param(
            {"schema": 2, "adjustments": ["not-a-dict"]},
            ["adjustment[0] must be an object"],
            id="entry-not-an-object",
        ),
        pytest.param(
            _one_adjustment("obliterate", {}),
            ("unknown action", "obliterate"), id="unknown-action",
        ),
        pytest.param(
            _one_adjustment("correct", {"verdict": "APPROVE"}),
            "not adjustable", id="invalid-field",
        ),
        pytest.param(
            _one_adjustment("add", {"severity": "low"}, target_id=None),
            "add requires fields", id="add-missing-required-fields",
        ),
        pytest.param(
            _one_adjustment(
                "correct", {"severity": "low", "title": "Better title"},
            ),
            "correct may not change severity; use promote or demote",
            id="correct-may-not-carry-severity",
        ),
        pytest.param(
            _one_adjustment("correct", {"title": "Better title"}),
            [], id="correct-without-a-severity-still-validates",
        ),
        pytest.param(
            _one_adjustment("promote", {}),
            "promote requires the severity field", id="promote-empty",
        ),
        pytest.param(
            _one_adjustment("promote", {"title": "not a severity"}),
            "promote requires the severity field", id="promote-wrong-field",
        ),
        pytest.param(
            _one_adjustment("demote", {"title": "not a severity"}),
            "demote requires the severity field", id="demote-wrong-field",
        ),
        pytest.param(
            _one_adjustment("rescope", {}),
            "rescope requires exactly the file and line fields",
            id="rescope-empty",
        ),
        pytest.param(
            _one_adjustment("rescope", {"line": 20}),
            "rescope requires exactly the file and line fields",
            id="rescope-partial",
        ),
        pytest.param(
            _one_adjustment("correct", {}),
            "correct requires at least one field", id="correct-empty",
        ),
        pytest.param(
            _one_adjustment("remove", {"title": "replacement"}),
            "remove does not accept replacement fields",
            id="remove-with-fields",
        ),
    ]

    @pytest.mark.parametrize("payload,expected", proposal_problems)
    def test_action_specific_field_contract_is_enforced(self, payload, expected):
        problems = validate_proposal_input(payload)
        if isinstance(expected, list):
            assert problems == expected
        elif isinstance(expected, tuple):
            assert any(all(s in p for s in expected) for p in problems)
        else:
            assert any(expected in p for p in problems)

    @pytest.mark.parametrize("schema_field", rejected_schema_values(2))
    def test_a_schema_out_of_template_refuses_the_whole_batch(
        self, tmp_path, schema_field
    ):
        """The taught template always writes `"schema": 2`.

        Anything else is out of that template and gets the same
        all-or-nothing refusal — never a silent read as version 1, a
        coerced integer, or a bool that compares equal to one. A wrong
        schema is the ONLY problem this well-formed document reports —
        it does not cascade into a second complaint about `adjustments`.
        """
        payload = apply_schema({
            "adjustments": [{
                "adjustment_id": "a1",
                "action": "promote", "target": {"kind": "finding", "id": "f1"},
                "fields": {"severity": "high"}, "rationale": "r",
            }],
        }, schema_field)
        problems = critic_adjustments_module.validate_adjustments_document(
            payload
        )
        assert len(problems) == 1
        assert "'schema' must be 2" in problems[0]

        _write_findings(tmp_path, [_finding("f1", "low")])
        _publish_raw_proposal(tmp_path, payload)
        with pytest.raises(ValueError, match="'schema' must be 2"):
            _adjudicate(tmp_path, [])
        data = _ledger(tmp_path)
        assert data["findings"][0]["severity"] == "low"  # nothing written

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

    def test_non_object_doc_fails_as_a_shape_error_not_a_schema_error(
        self, tmp_path
    ):
        """`[{"id": "f1"}]` is valid JSON but not a document with a
        'schema' field to be wrong about — the diagnosis must name the
        actual defect (not a JSON object) rather than misreporting it as a
        missing or invalid schema."""
        _write_findings(tmp_path, [_finding("f1", "low")])
        _publish_verdict(tmp_path, "REVISE")
        _artifact(tmp_path, "critic_adjustments").write_text(
            json.dumps([{"id": "f1"}])
        )
        with pytest.raises(
            ValueError,
            match="decision-critic-adjustments.json must be a JSON object",
        ):
            _adjudicate(tmp_path, [])
        data = _ledger(tmp_path)
        assert data["findings"][0]["severity"] == "low"  # nothing written


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

    def test_a_line_outside_the_1_indexed_contract_is_rejected(self, tmp_path):
        """output.py accepts only positive ints for `line`; a patch that
        smuggled a negative past this guard would publish a finding the
        builder itself would have refused. Same `validate_finding_content_field`
        domain as `proposal_field_domain` above."""
        _write_findings(tmp_path, [_finding("f1")])
        with pytest.raises(ValueError, match="line must be a positive"):
            _publish_revise(tmp_path, [{
                "action": "rescope", "target": {"kind": "finding", "id": "f1"},
                "fields": {"file": "f.go", "line": -5}, "rationale": "r",
            }])


class TestReadCriticVerdict:
    """Unit coverage for the reader `adjudicate`'s gate is built on — it
    returns an allowed verdict only from a complete source-bound snapshot
    and otherwise collapses the unusable snapshot to ``None``."""

    unusable_verdict_marker = [
        pytest.param(False, None, id="absent"),
        pytest.param(True, "{not json", id="unparseable"),
        pytest.param(
            True, json.dumps({"reason": "no verdict field at all"}),
            id="no-key",
        ),
    ]

    @pytest.mark.parametrize("write,content", unusable_verdict_marker)
    def test_unusable_verdict_marker(self, tmp_path, write, content):
        """A shape the reader cannot use collapses to `None`, the same
        outcome as a missing file — `non_object_json` and
        `non_string_verdict_field` reach the same collapse through the
        same shape check as `unparseable`."""
        if write:
            _artifact(tmp_path, "critic_verdict").write_text(content)
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
        adj_path = _artifact(tmp_path, "critic_adjustments")
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match="'outcome' is not allowed"):
            _adjudicate(tmp_path, [])

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    @pytest.mark.parametrize("verdict", ["REVISE", "SKIPPED"])
    def test_valid_verdict_string_is_returned_as_is(self, tmp_path, verdict):
        _publish_verdict(tmp_path, verdict)
        assert read_critic_verdict(str(tmp_path)) == verdict

    def test_a_near_miss_spelling_is_never_a_usable_verdict(self, tmp_path):
        """The vocabulary is exact-match, not case-insensitive or
        whitespace-tolerant: a critic that deviates fails loudly rather
        than being silently normalized into an adjudicable REVISE."""
        with pytest.raises(ValueError, match="unknown critic verdict"):
            _publish_verdict(tmp_path, "revise")
        assert read_critic_verdict(str(tmp_path)) is None


class TestRecommendationsInvalidation:
    """An applying batch must withdraw advice that its revisions may contradict."""

    _RECS = {
        "immediate": ["Escape the payment notice before merge."],
        "important": [],
        "suggestions": ["Consider a nonce on the form."],
    }

    def _seed(self, tmp_path):
        _write_findings(
            tmp_path, [_finding("f1", "critical")], recommendations=self._RECS,
        )
        return _publish_revise(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }])

    def test_an_applying_batch_withdraws_the_recommendations(self, tmp_path):
        ids = self._seed(tmp_path)
        result = _adjudicate(tmp_path, ids, verified=(0,))
        assert result["applied"] == 1
        data = _ledger(tmp_path)
        assert data["recommendations"] == {
            "immediate": [], "important": [], "suggestions": [],
        }
        assert data["invalidated_recommendations"] == [{
            "recommendations": self._RECS,
            "invalidated_by_critic_adjustment_ids": _applied_ids(data),
        }]
        validate_findings_document(data)

    @pytest.mark.parametrize("replacement,expected", [
        pytest.param({"suggestions": ["  Add a nonce when convenient.  "]},
                     ["Add a nonce when convenient."], id="normalized-subset"),
        pytest.param({}, [], id="empty-replacement"),
    ])
    def test_revised_recommendations_are_installed(self, tmp_path, replacement, expected):
        ids = self._seed(tmp_path)
        _adjudicate(tmp_path, ids, verified=(0,), recommendations=replacement)
        data = _ledger(tmp_path)
        assert data["recommendations"] == {
            "immediate": [], "important": [], "suggestions": expected,
        }
        assert len(data["invalidated_recommendations"]) == 1

    def test_a_wholly_refuted_batch_leaves_them_alone(self, tmp_path):
        ids = self._seed(tmp_path)
        _adjudicate(
            tmp_path, ids, refuted=((0, "the probe refuted it"),),
            recommendations={"suggestions": ["Replacement must not install."]},
        )
        data = _ledger(tmp_path)
        assert data["recommendations"] == self._RECS
        assert "invalidated_recommendations" not in data

    def test_empty_recommendations_record_no_invalidation(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "critical")])
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }], verified=(0,))
        assert "invalidated_recommendations" not in _ledger(tmp_path)

    @pytest.mark.parametrize("bad", [
        pytest.param("not a dict", id="not-object"),
        pytest.param({"immediate": "x"}, id="not-list"),
        pytest.param({"immediate": [1]}, id="not-string"),
    ])
    def test_malformed_revised_recommendations_are_refused(self, tmp_path, bad):
        ids = self._seed(tmp_path)
        before = (tmp_path / "review-findings.json").read_bytes()
        with pytest.raises(critic_adjustments_module.AdjustmentValidationError) as excinfo:
            _adjudicate(tmp_path, ids, verified=(0,), recommendations=bad)
        assert any("revised_recommendations" in p for p in excinfo.value.problems)
        assert (tmp_path / "review-findings.json").read_bytes() == before

    def test_reader_rejects_malformed_withdrawn_priority(self, tmp_path):
        bad = "not a list"
        ids = self._seed(tmp_path)
        _adjudicate(tmp_path, ids, verified=(0,))
        data = _ledger(tmp_path)
        data["invalidated_recommendations"] = [{
            "recommendations": {"immediate": ["Valid advice."], "important": bad},
            "invalidated_by_critic_adjustment_ids": ids,
        }]
        with pytest.raises(ValueError, match="invalidated_recommendations.*malformed"):
            validate_findings_document(data)


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
        data = _ledger(tmp_path)
        assert data["assessment"] is None
        invalidated = data[INVALIDATED_ASSESSMENTS_KEY]
        assert len(invalidated) == 1
        assert invalidated[0]["text"] == self._SUMMARY
        # Tied to the exact decisions that caused it, the same way each
        # touched finding names the action that touched it.
        assert invalidated[0]["invalidated_by_critic_adjustment_ids"] == _applied_ids(data)

    def test_a_second_withdrawal_names_only_its_own_batch(self, tmp_path):
        """invalidated_by_critic_adjustment_ids is causal attribution, not history: a second
        reconciliation round's withdrawal must name the batch that caused
        it, never the cumulative applied-ids list. Also covers a second
        round appending rather than overwriting the first withdrawal's
        text. Fix 47cd4c16."""
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
        texts = [entry["text"] for entry in invalidated]
        assert texts == [self._SUMMARY, "Fresh assessment after round two."]
        second_batch = [
            i for i in _applied_ids(data) if i not in first_batch
        ]
        assert second_batch
        assert invalidated[1]["invalidated_by_critic_adjustment_ids"] == second_batch
        assert invalidated[0]["invalidated_by_critic_adjustment_ids"] == first_batch

    def test_no_summary_to_withdraw_records_no_withdrawal(self, tmp_path):
        _write_findings(tmp_path, [_finding("f1", "critical")])
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "r",
        }], verified=(0,))
        data = _ledger(tmp_path)
        assert data["assessment"] is None
        assert INVALIDATED_ASSESSMENTS_KEY not in data


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

    def test_the_snippet_builds_the_ledger_and_saves_through_findings_save(
        self
    ):
        """`ReviewOutputBuilder` produces a reviewer document — it carries a
        `reviewer` field and a reviewed-file lifecycle the ledger does not
        have. Only `FindingsLedgerBuilder` produces the artifact this agent
        is asked for, and the built ledger is saved only through
        `findings_save.py`, never a bare write."""
        text = self._text()
        assert 'FindingsLedgerBuilder(pr_id="PR_ID_FROM_CONTEXT", output_dir=' in text
        assert "from review.findings_ledger import FindingsLedgerBuilder" in text
        assert "ReviewOutputBuilder(" not in text
        assert "scripts/review/findings_save.py" in text
        assert "--output-dir" in text
        assert "--findings" in text



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

    @pytest.mark.parametrize("value", ["not checked", True])
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

    def test_it_becomes_the_ledger_assessment(self, tmp_path):
        """Replacement is not erasure: the reconciler's retracted words
        stay auditable beside the ids that cost them their standing."""
        self._seed(tmp_path)
        _publish_and_adjudicate(
            tmp_path, self._DEMOTION,
            verified=(0,), assessment=self._REVISED,
        )
        data = _ledger(tmp_path)
        assert data["assessment"] == self._REVISED
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


class TestLedgerVerdictRecompute:
    """`_recount_summary` rebuilt the severities and left `verdict` alone.

    That was survivable while step 11 copied an orchestrator-transcribed
    verdict over the ledger's; with the published verdict DERIVED from the
    ledger, a stale `request_changes` over a demoted-to-low finding list is
    machine authority for a wrong GitHub verdict.
    """

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
            pytest.param(
                "promote", {"severity": "high"}, id="severity-required-branch",
            ),
            pytest.param(
                "rescope", {"file": "src/b.py", "line": 20},
                id="exactly-file-and-line-branch",
            ),
        ],
    )
    def test_finding_mutations_require_kind_and_id(self, action, fields):
        """One representative each of the severity-required branch
        (`promote`) and the exactly-file-and-line branch (`rescope`); the
        other actions (`demote`, `correct`, `remove`) reach the same two
        branches with no distinct outcome of their own."""
        payload = {
            "schema": 2,
            "adjustments": [self._entry(action, fields=fields)],
        }
        assert validate_proposal_input(payload) == []

    def test_severity_actions_accept_related_finding_corrections(self):
        payload = {"schema": 2, "adjustments": [self._entry(
            "promote",
            fields={
                "severity": "medium", "title": "Corrected title",
                "description": "Corrected description.",
                "recommendation": "Correct the caller.", "file": "caller.py",
                "line": None, "category": "security", "confidence": 0.95,
            },
        )]}

        assert validate_proposal_input(payload) == []

    def test_a_file_change_requires_its_line(self):
        payload = {"schema": 2, "adjustments": [self._entry(
            "promote", fields={"file": "caller.py", "severity": "medium"},
        )]}

        problems = validate_proposal_input(payload)

        assert problems == [
            "adjustment[0]: a file change requires the line field as well "
            "(null for a file-scoped finding), so a moved finding never keeps "
            "a stale line"
        ]

    proposal_field_domain = [("severity", "urgent"), ("confidence", 1.01)]

    @pytest.mark.parametrize("action", ["add", "correct"])
    @pytest.mark.parametrize(("field", "value"), proposal_field_domain)
    def test_finding_content_values_follow_the_canonical_domain_contract(
        self, action, field, value
    ):
        """Every field/action combination reaches
        `review_document.validate_finding_content_field` — one delegated
        function pinned per field at its owner (`agent/test_output.py`).
        `add` and `correct` are kept because they build the `fields` dict
        differently (merged onto a full finding vs. built from scratch);
        `promote`/`demote` reach the identical branch through `add`'s
        merged-dict path."""
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

    def test_an_invalid_planned_finding_leaves_the_ledger_unchanged(
        self, tmp_path
    ):
        _write_findings(tmp_path, [_finding("f1")])
        entry = self._entry(
            "add",
            id_=None,
            fields={
                "severity": "medium",
                "title": "Invalid file",
                "file": None,
                "description": "The file value violates the domain.",
                "recommendation": "Name the affected file.",
            },
        )
        entry["adjustment_id"] = "invalid-file"
        _publish_raw_proposal(
            tmp_path, {"schema": 2, "adjustments": [entry]}
        )
        paths = (
            _artifact(tmp_path, "critic_adjustments"),
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
            # Hits the generic "action not allowed for check targets" branch.
            ("promote", {"severity": "high"}),
            # `add` hits a second, distinct branch first: `_validate_target`
            # refuses a non-finding kind before the generic check ever runs.
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

    def test_check_correction_rejects_immutable_or_finding_fields(self):
        payload = {
            "schema": 2,
            "adjustments": [
                self._entry(
                    "correct",
                    kind="check",
                    id_="c1",
                    fields={"severity": "replacement"},
                )
            ],
        }

        assert "severity" in " ".join(validate_proposal_input(payload))

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

    def test_unhashable_target_kind_is_a_validation_problem_not_a_crash(self):
        """A model-authored `target.kind` of `[]` must reach the REJECTED path."""
        proposal = {
            "schema": 2,
            "adjustments": [
                self._entry("correct", kind=[], fields={"description": "x"}),
                self._entry("correct", fields={"description": "y"}),
            ],
        }
        problems = critic_adjustments_module.validate_adjustments_document(
            proposal
        )
        assert any("'kind' must be one of" in problem for problem in problems)

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
            _artifact(tmp_path, "critic_adjustments"),
            tmp_path / "review-findings.json",
        )
        before = tuple(path.read_bytes() for path in paths)

        with pytest.raises(ValueError, match="no check with id 'c9'"):
            self._adjudicate(tmp_path, proposal)

        assert tuple(path.read_bytes() for path in paths) == before

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

    @pytest.mark.parametrize(
        "forbidden,value",
        [
            ("adjustment_id", "critic-owned"),
            ("outcome", "verified"),
        ],
    )
    def test_prepare_rejects_lifecycle_fields(self, forbidden, value):
        with pytest.raises(ValueError, match=forbidden):
            critic_adjustments_module.prepare_proposal({
                "schema": 2,
                "adjustments": [self._entry(**{forbidden: value})],
            })

    def test_prepare_rejects_non_proposal_top_level_fields(self):
        """`revised_assessment` doubles as the same-named row that used to
        cover the now-deleted `TestRevisedAssessment::
        test_a_non_string_revised_assessment_rejects_the_proposal`."""
        with pytest.raises(ValueError, match="revised_assessment"):
            critic_adjustments_module.prepare_proposal(
                {"schema": 2, "adjustments": [], "revised_assessment": "x"}
            )

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
        before = _artifact(tmp_path, "critic_adjustments").read_bytes()

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
            _artifact(tmp_path, "critic_adjustments")
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
            # One representative of the five `caller-*` rows: all are
            # spellings of the same `_extra_key_problems(request,
            # _REQUEST_KEYS)` check.
            (
                lambda request, ids: request.update({"not_checked": [ids[2]]}),
                "not_checked",
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
            "caller-not-checked", "non-string-verified",
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
        adj_path = _artifact(tmp_path, "critic_adjustments")
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
        adj_path = _artifact(tmp_path, "critic_adjustments")
        findings_path = tmp_path / "review-findings.json"
        before = (adj_path.read_bytes(), findings_path.read_bytes())

        with pytest.raises(ValueError, match="no finding with id 'f9'"):
            _adjudicate(tmp_path, ids, verified=(0,))

        assert (adj_path.read_bytes(), findings_path.read_bytes()) == before

    def test_a_malformed_ledger_is_rejected_before_any_write(self, tmp_path):
        ids = self._seed(tmp_path)
        ledger = _ledger(tmp_path)
        ledger[APPLIED_IDS_KEY] = "not-a-record-list"
        write_findings(str(tmp_path), ledger)
        adj_path = _artifact(tmp_path, "critic_adjustments")
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
                "promote", "high", {"severity": "medium", "title": "Updated"},
                "promote must increase severity",
            ),
            (
                "promote", "high", {"severity": "high", "title": "Updated"},
                "promote would not change severity",
            ),
            (
                "demote", "low", {"severity": "medium", "title": "Updated"},
                "demote must decrease severity",
            ),
            (
                "demote", "low", {"severity": "low", "title": "Updated"},
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
        adj_path = _artifact(tmp_path, "critic_adjustments")
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
        adjudicate_reached_lock = threading.Event()
        release_save = threading.Event()
        real_write = critic_adjustments_module.write_critic_verdict

        @contextmanager
        def thread_lock(_output_dir):
            if threading.current_thread().name == "adjudicate":
                adjudicate_reached_lock.set()
            with lock:
                yield

        def blocking_write(output_dir, verdict, proposal):
            if threading.current_thread().name == "critic-save":
                save_inside_write.set()
                assert release_save.wait(timeout=5)
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
        assert save_inside_write.wait(timeout=5)
        adjudicate_thread.start()
        # `adjudicate_reached_lock` fires the instant the adjudicate thread
        # calls `output_dir_lock`, before it blocks trying to acquire the
        # real lock `blocking_write` still holds — so the thread cannot
        # have produced a result yet, with no sleep needed to prove it.
        assert adjudicate_reached_lock.wait(timeout=5)
        assert adjudicate_thread.is_alive()
        assert "adjudicated" not in results and "error" not in results
        release_save.set()
        save_thread.join(timeout=5)
        adjudicate_thread.join(timeout=5)

        assert results["save"] == 0
        assert "error" in results, (
            "the superseded proposal's ids are not in the new one"
        )
        proposal = json.loads(
            _artifact(tmp_path, "critic_adjustments").read_text()
        )
        marker = json.loads(
            _artifact(tmp_path, "critic_verdict").read_text()
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
            cwd=tmp_path,
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

    def test_the_assessment_and_recommendations_echo_report_presence(
        self, tmp_path
    ):
        """One request without a revised assessment but with revised
        recommendations proves the presence/absence echo is per-key, not
        a single flag."""
        ids = self._seed(tmp_path)

        result = self._run(tmp_path, _request(
            ids, verified=(0,), recommendations={"suggestions": ["Add a nonce."]},
        ))

        assert result.returncode == 0, result.stdout + result.stderr
        assert "REVISED ASSESSMENT: absent" in result.stdout
        assert "REVISED RECOMMENDATIONS: present" in result.stdout

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


class TestProvenanceAtTheReaderBoundary:
    """The ledger reader accepts the reconciliator's provenance and refuses
    a malformed copy of it; absence stays valid for prior-run ledgers."""

    def _with(self, **extra):
        doc = canonical_findings_ledger(("high",), checks=[_check("c1")])
        doc.update(extra)
        return doc

    def test_absence_is_still_a_valid_ledger(self):
        validate_findings_document(self._with())

    def test_accepts_a_confirmed_note_citing_verify_items(self):
        doc = self._with(orchestrator_notes=[{
            "id": "n1", "note": "the lockfile regenerates cleanly",
            "outcome": "confirmed", "evidence": "deleted and regenerated it",
            "verifies": ["V2", "V3"],
        }])
        validate_findings_document(doc)

    def test_accepts_sources_notes_and_drops(self):
        doc = self._with(
            dropped_findings=[{"reviewer": "code-review", "id": "f2",
                               "reason": "out_of_scope", "evidence": "not in diff"}],
            dropped_checks=[{"reviewer": "code-review", "id": "c9",
                             "reason": "void", "evidence": "wrong artifact"}],
            orchestrator_notes=[{"id": "n1", "note": "f1 and f2 are one",
                                 "outcome": "confirmed", "evidence": "same sink"}],
        )
        doc["findings"][0]["sources"] = [
            {"reviewer": "security-review", "id": "f1", "severity": "high"}
        ]
        doc["findings"][0]["severity_note"] = "kept at high: reachable."
        doc["checks"][0]["sources"] = [{"reviewer": "security-review", "id": "c1"}]
        validate_findings_document(doc)

    @pytest.mark.parametrize("mutate", [
        pytest.param(
            lambda d: d["findings"][0].__setitem__("sources", []),
            id="empty-sources",
        ),
        pytest.param(
            lambda d: d["checks"][0].__setitem__(
                "sources",
                [{"reviewer": "security-review", "id": "c1", "severity": "high"}],
            ),
            id="severity-on-a-check-source",
        ),
        pytest.param(
            lambda d: d.__setitem__("dropped_findings", [
                {"reviewer": "x-review", "id": "f2", "reason": "merged",
                 "evidence": "e"},
            ]),
            id="bad-drop-reason",
        ),
        pytest.param(
            lambda d: d.__setitem__("dropped_checks", [
                {"reviewer": "x-review", "id": "c2", "reason": "void",
                 "evidence": "e", "scope_status": "in_scope"},
            ]),
            id="stamped-key-on-a-check-drop",
        ),
        pytest.param(
            lambda d: d.__setitem__("orchestrator_notes", [
                {"id": "n1", "outcome": "refuted", "evidence": "e",
                 "verifies": ["V2"]},
            ]),
            id="verifies-on-a-refuted-note",
        ),
    ])
    def test_malformed_provenance_is_refused(self, mutate):
        """One row per validated collection (`_validate_sources`,
        `_validate_dropped` per drop reason set, `_validate_orchestrator_notes`);
        the reader's counterpart to the builder's matching matrix in
        `test_findings_ledger.py`."""
        doc = self._with()
        mutate(doc)
        with pytest.raises(ValueError):
            validate_findings_document(doc)


class TestCriticCannotTouchVerifies:
    def test_a_check_correction_keeps_its_citations(self, tmp_path):
        ledger = canonical_findings_ledger(("high",), checks=[{
            "id": "c1", "question": "q", "method": "m", "result": "r",
            "source_reviewers": ["security-reviewer"], "verifies": ["V1"],
        }])
        critic_adjustments_module.write_findings(str(tmp_path), ledger)
        proposal = critic_adjustments_module.prepare_proposal({
            "schema": 2,
            "adjustments": [{
                "action": "correct", "target": {"kind": "check", "id": "c1"},
                "fields": {"result": "r, re-read"}, "rationale": "Wording.",
            }],
        })
        critic_adjustments_module.write_critic_verdict(str(tmp_path), "REVISE", proposal)
        critic_adjustments_module.adjudicate(str(tmp_path), {
            "schema": 2,
            "verified": [proposal["adjustments"][0]["adjustment_id"]],
            "refuted": [],
        })
        settled = critic_adjustments_module.read_findings_file(
            tmp_path / "review-findings.json"
        ).findings
        assert settled["checks"][0]["result"] == "r, re-read"
        assert settled["checks"][0]["verifies"] == ["V1"]

    def test_a_proposal_naming_verifies_is_rejected(self):
        with pytest.raises(ValueError, match="verifies"):
            critic_adjustments_module.prepare_proposal({
                "schema": 2,
                "adjustments": [{
                    "action": "correct", "target": {"kind": "check", "id": "c1"},
                    "fields": {"verifies": ["V2"]}, "rationale": "No.",
                }],
            })

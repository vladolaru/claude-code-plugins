"""Tests for critic_adjustments — the sole writer that carries decision-critic
finding-level decisions into review-findings.json."""

import json
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

from review.critic_adjustments import (
    APPLIED_IDS_KEY,
    apply_adjustments,
    pending_count,
)
from review.orchestration import _orchestrate_step_11
from review.reconciliation_context import build_critic_context


def _write_findings(output_dir, issues):
    sev = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for i in issues:
        sev[i["severity"]] += 1
    (Path(output_dir) / "review-findings.json").write_text(json.dumps({
        "reviewer": "reconciliator",
        "verdict": "REQUEST_CHANGES",
        "summary": {"total_issues": len(issues), "by_severity": sev},
        "issues": issues,
    }))


def _write_adjustments(output_dir, adjustments):
    (Path(output_dir) / "decision-critic-adjustments.json").write_text(
        json.dumps({"schema": 1, "adjustments": adjustments})
    )


def _write_critic_verdict(output_dir, verdict):
    (Path(output_dir) / "decision-critic-verdict.json").write_text(
        json.dumps({"verdict": verdict})
    )


def _issue(id_, severity="low"):
    return {"id": id_, "severity": severity, "title": "t", "file": "f.go",
            "description": "d", "recommendation": "r", "category": "general",
            "confidence": 0.9}


class TestApplyAdjustments:
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


class TestCrashSafety:
    """Application is recorded on both sides, so no crash point can either
    lose the batch or apply it twice."""

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
        assert json.loads(after_first)["applied_critic_adjustments"] == [
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
        expected = ["decision-critic-adjustments.json", "review-findings.json"]
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


class TestScopeLinePairing:
    """schemas/review-output.ts:36-37 and output.py's renderer treat
    scope/line as a pair; a patch must never split them."""

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
    """The step-11 wiring calls this as a script, so the process contract
    (exit status + stdout/stderr channels) is part of the interface."""

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


class TestVerdictSyncHardening:
    """Step 11's Rule 23 sync is the other writer of review-findings.json.

    Crash-safety is a property of the artifact, not of one module: the
    adjustments apply replaces the ledger atomically, but the verdict sync
    ran last and wrote it with a truncating open, so a crash there left a
    truncated ledger regardless. It also assumed the file was an object.
    """

    def test_verdict_sync_leaves_no_temp_residue(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        (tmp_path / "review-verdict.json").write_text(
            json.dumps({"verdict": "COMMENT"})
        )
        (tmp_path / "review-report.md").write_text("# report")
        _orchestrate_step_11("pr", {}, {}, {}, str(tmp_path))

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "COMMENT"
        leftovers = [
            path.name for path in tmp_path.iterdir()
            if path.name.startswith("tmp") or path.suffix == ".tmp"
        ]
        assert leftovers == [], f"temp files survived the sync: {leftovers}"

    def test_list_shaped_ledger_degrades_instead_of_crashing(self, tmp_path):
        """The subscript assignment used to raise TypeError past the except
        tuple, taking finalize down with a review that had already run."""
        (tmp_path / "review-findings.json").write_text(
            json.dumps([_issue("aaaa1111", "low")])
        )
        (tmp_path / "review-verdict.json").write_text(
            json.dumps({"verdict": "COMMENT"})
        )
        (tmp_path / "review-report.md").write_text("# report")
        _orchestrate_step_11("pr", {}, {}, {}, str(tmp_path))

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any("verdict sync skipped" in note
                   for note in result["degradation_notes"])
        assert result["status"] == "degraded", (
            "a degradation found during the sync must reach the status "
            "published beside it"
        )
        # The unusable ledger is left exactly as found, not half-rewritten.
        assert json.loads(
            (tmp_path / "review-findings.json").read_text()
        ) == [_issue("aaaa1111", "low")]


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
        (tmp_path / "review-verdict.json").write_text(
            json.dumps({"verdict": "REQUEST_CHANGES"})
        )
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
    """Step 11 is where pending critic adjustments land, so bot mode (which
    follows no briefing) and any run whose orchestrator did not apply them
    still converge on a findings JSON the critic reached — but only under
    REVISE, the verdict whose briefing spot-checked the entries first."""

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

    def test_pending_adjustments_applied_before_verdict_sync(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "medium"}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-verdict.json").write_text(
            json.dumps({"verdict": "REQUEST_CHANGES"})
        )
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)
        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["issues"][0]["severity"] == "medium"
        # Both effects landed — the patch and the Rule 23 verdict sync.
        # The pair is order-invariant (each write preserves the other's
        # field); what pins the placement is the sibling test's
        # `status == "degraded"` assertion.
        assert data["verdict"] == "REQUEST_CHANGES"

    def test_invalid_adjustments_degrade_instead_of_crashing(self, tmp_path):
        _write_findings(tmp_path, [_issue("aaaa1111", "low")])
        _write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "aaaa1111",
            "fields": {}, "rationale": "r",
        }])
        _write_critic_verdict(tmp_path, "REVISE")
        (tmp_path / "review-verdict.json").write_text(
            json.dumps({"verdict": "REQUEST_CHANGES"})
        )
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

    @pytest.mark.parametrize("critic_verdict", ["STAND", "ESCALATE", "SKIPPED"])
    def test_non_revise_verdict_never_applies_pending_adjustments(
        self, tmp_path, critic_verdict
    ):
        """Adjustments are a REVISE-only channel.

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
        _write_critic_verdict(tmp_path, critic_verdict)
        (tmp_path / "review-verdict.json").write_text(
            json.dumps({"verdict": "APPROVE"})
        )
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
        (tmp_path / "review-verdict.json").write_text(
            json.dumps({"verdict": "APPROVE"})
        )
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
        (tmp_path / "review-verdict.json").write_text(
            json.dumps({"verdict": "APPROVE"})
        )
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
        findings_path.write_text(json.dumps(data))
        _write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "critical"}, "rationale": "r",
            "adjustment_id": "deadbeef",
        }])
        _write_critic_verdict(tmp_path, "STAND")

        assert pending_count(str(tmp_path)) == 0

        (tmp_path / "review-verdict.json").write_text(
            json.dumps({"verdict": "APPROVE"})
        )
        (tmp_path / "review-report.md").write_text("# report")
        self._step_11(tmp_path)
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []

    def test_malformed_findings_file_degrades_instead_of_crashing(
        self, tmp_path
    ):
        """The measured regression: a list-shaped findings file with no
        review-verdict.json used to survive step 11 — Rule 23's write is
        gated on verdict_data — so the apply call must not become the
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

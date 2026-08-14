"""Tests for critic_adjustments — the sole writer that carries decision-critic
finding-level decisions into review-findings.json."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "review" / "critic_adjustments.py"
sys.path.insert(0, str(SCRIPTS_DIR))

from review.critic_adjustments import apply_adjustments


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

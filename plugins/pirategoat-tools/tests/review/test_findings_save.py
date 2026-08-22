"""Tests for review/findings_save.py — the reconciliator's validating save
channel for review-findings.json.

Sibling design to review/critic.py's TestCriticSave: this is the ONLY
channel the review-reconciliator agent is allowed to write
review-findings.json through (agents/review-reconciliator.md). It validates
the whole ledger document and writes it atomically via
critic_adjustments.write_findings() — the single sanctioned write path — or
writes nothing at all.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "review" / "findings_save.py"


def _valid_findings(**overrides):
    doc = {
        "verdict": "request_changes",
        "issues": [
            {
                "id": "aaaa1111",
                "category": "security",
                "severity": "high",
                "title": "Unsanitized input",
                "description": "User input reaches the query unsanitized.",
                "file": "src/foo.php",
                "line": 42,
                "recommendation": "Sanitize before use.",
                "confidence": 0.9,
            },
        ],
        "summary": {
            "total_issues": 1,
            "by_severity": {
                "critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0,
            },
        },
        "narrative_summary": "One high-severity issue found.",
        "clearances": [
            {"claim": "no callers", "method": "grep", "evidence": "0 hits"},
        ],
    }
    doc.update(overrides)
    return doc


class TestFindingsSave:
    def _run_save(self, output_dir, findings_path):
        cmd = [
            sys.executable, str(SCRIPT),
            "--output-dir", str(output_dir),
            "--findings", str(findings_path),
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    def _write_findings(self, tmp_path, doc, name="f.json"):
        path = tmp_path / name
        path.write_text(json.dumps(doc))
        return path

    def test_valid_findings_saved_atomically(self, tmp_path):
        findings = self._write_findings(tmp_path, _valid_findings())

        result = self._run_save(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        ledger_path = tmp_path / "review-findings.json"
        assert ledger_path.is_file()
        saved = json.loads(ledger_path.read_text())
        assert saved["verdict"] == "request_changes"
        assert len(saved["issues"]) == 1

    def test_echo_format(self, tmp_path):
        doc = _valid_findings(
            issues=[
                {
                    "id": f"id{i}",
                    "category": "general",
                    "severity": sev,
                    "title": "t",
                    "description": "d",
                    "file": "f.php",
                    "line": 1,
                    "recommendation": "r",
                    "confidence": 0.9,
                }
                for i, sev in enumerate(
                    ["high", "medium", "medium", "medium",
                     "medium", "medium", "medium", "low"]
                )
            ],
            summary={
                "total_issues": 8,
                "by_severity": {
                    "critical": 0, "high": 1, "medium": 6, "low": 1, "info": 0,
                },
            },
            clearances=[
                {"claim": f"c{i}", "method": "grep", "evidence": "0 hits"}
                for i in range(12)
            ],
        )
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "RECORDED VERDICT: request_changes" in result.stdout
        assert (
            "RECORDED FINDINGS: 8 (critical 0, high 1, medium 6, low 1)"
            in result.stdout
        )
        assert "CLEARANCES: 12 | NARRATIVE: present" in result.stdout

    def test_echo_reflects_absent_narrative_and_no_clearances(self, tmp_path):
        doc = _valid_findings(narrative_summary=None, clearances=[])
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "CLEARANCES: 0 | NARRATIVE: absent" in result.stdout

    def test_rejects_non_object_top_level(self, tmp_path):
        findings = self._write_findings(tmp_path, ["not", "an", "object"])

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()
        assert [p.name for p in tmp_path.iterdir()] == [findings.name]

    def test_rejects_bad_verdict(self, tmp_path):
        findings = self._write_findings(
            tmp_path, _valid_findings(verdict="MAYBE")
        )

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "verdict" in result.stdout.lower()
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_uppercase_verdict(self, tmp_path):
        """Casing matters — the ledger's real vocabulary is lowercase,
        matching _verdict_for_issues()'s return values in agent/output.py."""
        findings = self._write_findings(
            tmp_path, _valid_findings(verdict="REQUEST_CHANGES")
        )

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_count_mismatch(self, tmp_path):
        doc = _valid_findings()
        doc["summary"]["total_issues"] = 5  # actual issues list has 1 entry
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "summary" in result.stdout.lower()
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_by_severity_mismatch(self, tmp_path):
        doc = _valid_findings()
        doc["summary"]["by_severity"]["high"] = 0
        doc["summary"]["by_severity"]["low"] = 1  # doesn't match the 1 high issue
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    @pytest.mark.parametrize(
        "missing_field",
        ["severity", "title", "file", "description", "recommendation", "id"],
    )
    def test_rejects_issue_missing_required_field(self, tmp_path, missing_field):
        doc = _valid_findings()
        del doc["issues"][0][missing_field]
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert missing_field in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_issue_invalid_severity(self, tmp_path):
        doc = _valid_findings()
        doc["issues"][0]["severity"] = "catastrophic"
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "catastrophic" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_issue_not_an_object(self, tmp_path):
        doc = _valid_findings(issues=["not an object"])
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_issues_not_a_list(self, tmp_path):
        doc = _valid_findings(issues={"not": "a list"})
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_missing_findings_file(self, tmp_path):
        result = self._run_save(tmp_path, tmp_path / "nonexistent.json")

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_invalid_json(self, tmp_path):
        findings = tmp_path / "bad.json"
        findings.write_text("{not valid json")

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_collects_multiple_problems(self, tmp_path):
        doc = _valid_findings(verdict="MAYBE")
        doc["issues"][0]["severity"] = "catastrophic"
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        rejected_lines = [
            line for line in result.stdout.splitlines()
            if line.startswith("REJECTED:")
        ]
        assert len(rejected_lines) >= 2
        assert not (tmp_path / "review-findings.json").exists()

    @pytest.mark.parametrize(
        "verdict", ["block", "request_changes", "comment", "approve"]
    )
    def test_accepts_every_reconciler_verdict(self, tmp_path, verdict):
        doc = _valid_findings(verdict=verdict)
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"RECORDED VERDICT: {verdict}" in result.stdout

    def test_rejects_not_applicable_verdict(self, tmp_path):
        """not_applicable is a per-reviewer abstention verdict
        (ReviewOutputBuilder.mark_not_applicable) that the reconciliator
        never emits — it always produces a reconciled ledger, never
        abstains from the whole PR."""
        doc = _valid_findings(verdict="not_applicable")
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_accepts_empty_issues_with_approve(self, tmp_path):
        doc = _valid_findings(
            verdict="approve",
            issues=[],
            summary={
                "total_issues": 0,
                "by_severity": {
                    "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
                },
            },
        )
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "RECORDED FINDINGS: 0 (critical 0, high 0, medium 0, low 0)" in (
            result.stdout
        )

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
sys.path.insert(0, str(TESTS_DIR))

from helpers.review_fixtures import canonical_findings_ledger


def _valid_findings(**overrides):
    doc = canonical_findings_ledger(reconciliation={
        "reviewing_agents": ["security-reviewer"],
        "dispatched_agents": ["security-reviewer"],
    })
    doc.update({
        "timestamp": "2026-08-26T10:00:00+00:00",
        "plugin_version": "1.114.0",
        "verdict": "request_changes",
        "findings": [
            {
                "id": "f1",
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
            "total_findings": 1,
            "by_severity": {
                "critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0,
            },
            "suppressed_advisory_finding_count": 0,
        },
        "assessment": "One high-severity finding found.",
        "checks": [
            {
                "id": "c1",
                "question": "Are there any callers?",
                "method": "grep",
                "result": "0 hits",
                "source_reviewers": ["security"],
            },
        ],
    })
    meta_override = overrides.pop("meta", None)
    doc.update(overrides)
    findings = doc.get("findings")
    checks = doc.get("checks")
    finding_count = len(findings) if isinstance(findings, list) else 0
    check_count = len(checks) if isinstance(checks, list) else 0
    doc["meta"].update({
        "next_finding_number": finding_count + 1,
        "next_check_number": check_count + 1,
    })
    doc["meta"]["reconciliation"].update({
        "input_finding_count": finding_count,
        "contributing_agent_count": 1 if finding_count else 0,
        "grouped_concern_count": finding_count,
        "verified_concern_count": finding_count,
    })
    if meta_override is not None:
        doc["meta"].update(meta_override)
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

    def test_accepts_canonical_findings_checks_and_assessment(self, tmp_path):
        findings = self._write_findings(tmp_path, _valid_findings())

        result = self._run_save(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        saved = json.loads((tmp_path / "review-findings.json").read_text())
        assert saved["findings"][0]["id"] == "f1"
        assert saved["checks"][0]["source_reviewers"] == ["security"]
        assert saved["assessment"] == "One high-severity finding found."
        assert saved["schema"] == 3
        assert "issues" not in saved
        assert "clearances" not in saved
        assert "narrative_summary" not in saved

    def test_rejects_missing_schema(self, tmp_path):
        doc = _valid_findings()
        del doc["schema"]
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "schema" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    @pytest.mark.parametrize("schema", [1, 2, "3", True, None])
    def test_rejects_schema_other_than_the_exact_ledger_integer(
        self, tmp_path, schema
    ):
        findings = self._write_findings(
            tmp_path, _valid_findings(schema=schema)
        )

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "schema" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("checks", None),
            ("checks", {}),
            ("assessment", ["not", "text"]),
        ],
    )
    def test_rejects_noncanonical_checks_or_assessment(
        self, tmp_path, field, value
    ):
        findings = self._write_findings(
            tmp_path, _valid_findings(**{field: value})
        )

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert field in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_mutable_or_malformed_check_identity(self, tmp_path):
        checks = _valid_findings()["checks"]
        checks[0]["source_reviewers"] = ["security", "", 7]
        findings = self._write_findings(
            tmp_path, _valid_findings(checks=checks)
        )

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "source_reviewers" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_retired_tool_metadata(self, tmp_path):
        doc = _valid_findings()
        doc["meta"]["tool_results_used"] = ["rg"]
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "tool_results_used" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_summary_without_advisory_finding_count(self, tmp_path):
        doc = _valid_findings()
        del doc["summary"]["suppressed_advisory_finding_count"]
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "summary does not match" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_valid_findings_saved_atomically(self, tmp_path):
        findings = self._write_findings(tmp_path, _valid_findings())

        result = self._run_save(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        ledger_path = tmp_path / "review-findings.json"
        assert ledger_path.is_file()
        saved = json.loads(ledger_path.read_text())
        assert saved["verdict"] == "request_changes"
        assert len(saved["findings"]) == 1

    def test_echo_format(self, tmp_path):
        doc = _valid_findings(
            findings=[
                {
                    "id": f"f{i + 1}",
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
                "total_findings": 8,
                "by_severity": {
                    "critical": 0, "high": 1, "medium": 6, "low": 1, "info": 0,
                },
                "suppressed_advisory_finding_count": 0,
            },
            checks=[{
                "id": f"c{i + 1}",
                "question": f"Check {i + 1}?",
                "method": "grep",
                "result": "0 hits",
                "source_reviewers": ["security"],
            } for i in range(12)],
        )
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "RECORDED VERDICT: request_changes" in result.stdout
        assert (
            "RECORDED FINDINGS: 8 (critical 0, high 1, medium 6, low 1)"
            in result.stdout
        )
        assert "CHECKS: 12 | ASSESSMENT: present" in result.stdout

    def test_echo_reflects_absent_assessment_and_no_checks(self, tmp_path):
        doc = _valid_findings(assessment=None, checks=[])
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "CHECKS: 0 | ASSESSMENT: absent" in result.stdout

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

    def test_rejects_verdict_that_does_not_match_issues(self, tmp_path):
        findings = self._write_findings(
            tmp_path, _valid_findings(verdict="approve")
        )

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "verdict does not match its findings" in result.stdout
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
        doc["summary"]["total_findings"] = 5  # actual findings list has 1 entry
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "summary" in result.stdout.lower()
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_by_severity_mismatch(self, tmp_path):
        doc = _valid_findings()
        doc["summary"]["by_severity"]["high"] = 0
        doc["summary"]["by_severity"]["low"] = 1  # doesn't match the 1 high finding
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    @pytest.mark.parametrize(
        "missing_field",
        [
            "severity", "title", "file", "description", "recommendation",
            "id", "category", "confidence", "line",
        ],
    )
    def test_rejects_issue_missing_required_field(self, tmp_path, missing_field):
        doc = _valid_findings()
        del doc["findings"][0][missing_field]
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert missing_field in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_accepts_null_line_for_file_scoped_issue(self, tmp_path):
        doc = _valid_findings()
        doc["findings"][0]["line"] = None
        doc["findings"][0]["scope"] = "file"
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        saved = json.loads((tmp_path / "review-findings.json").read_text())
        assert saved["findings"][0]["line"] is None

    def test_rejects_issue_invalid_severity(self, tmp_path):
        doc = _valid_findings()
        doc["findings"][0]["severity"] = "catastrophic"
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "catastrophic" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_issue_not_an_object(self, tmp_path):
        doc = _valid_findings(findings=["not an object"])
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_issues_not_a_list(self, tmp_path):
        doc = _valid_findings(findings={"not": "a list"})
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_checks_not_a_list(self, tmp_path):
        """A truthy non-list `checks` (e.g. an integer) must be
        rejected here — not merely tolerated by `_echo()` after the
        ledger is already written. `findings.get("checks") or []`
        only normalizes FALSY values; a truthy non-list would otherwise
        reach `len()`/iteration in `_echo()` post-write."""
        doc = _valid_findings(checks=12)
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "checks" in result.stdout.lower()
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_clearance_entry_not_an_object(self, tmp_path):
        doc = _valid_findings(checks=["not an object"])
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    @pytest.mark.parametrize(
        "bad_clearance",
        [
            {"method": "grep"},  # missing claim
            {"claim": "no callers"},  # missing method
            {"claim": "", "method": "grep"},  # blank claim
            {"claim": "no callers", "method": ""},  # blank method
            {"claim": "no callers", "method": "grep", "evidence": 5},  # bad evidence type
        ],
    )
    def test_rejects_clearance_wrong_shape(self, tmp_path, bad_clearance):
        doc = _valid_findings(checks=[bad_clearance])
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_assessment_wrong_type(self, tmp_path):
        doc = _valid_findings(assessment=["not", "a", "string"])
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "assessment" in result.stdout.lower()
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
        doc["findings"][0]["severity"] = "catastrophic"
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
        ("verdict", "severity", "counts"),
        [
            (
                "block", "critical",
                {"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0},
            ),
            (
                "request_changes", "high",
                {"critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0},
            ),
            (
                "comment", "medium",
                {"critical": 0, "high": 0, "medium": 1, "low": 0, "info": 0},
            ),
            (
                "approve", None,
                {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
            ),
        ],
    )
    def test_accepts_every_reconciler_verdict(
        self, tmp_path, verdict, severity, counts
    ):
        findings = []
        if severity is not None:
            finding = _valid_findings()["findings"][0]
            finding["severity"] = severity
            findings.append(finding)
        doc = _valid_findings(
            verdict=verdict,
            findings=findings,
            summary={
                "total_findings": len(findings),
                "by_severity": counts,
                "suppressed_advisory_finding_count": 0,
            },
        )
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

    @pytest.mark.parametrize(
        "field",
        [
            "applied_critic_adjustments",
            "rejected_critic_adjustments",
            "findings_removed_by_critic",
            "checks_removed_by_critic",
            "invalidated_assessments",
            "verdict_before_adjustments",
        ],
    )
    def test_rejects_actor_supplied_critic_lifecycle_fields(
        self, tmp_path, field
    ):
        findings = self._write_findings(
            tmp_path, _valid_findings(**{field: []})
        )

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "critic-owned lifecycle" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    @pytest.mark.parametrize("collection", ["findings", "checks"])
    def test_rejects_actor_supplied_critic_provenance(
        self, tmp_path, collection
    ):
        doc = _valid_findings()
        doc[collection][0]["critic_adjustment"] = {
            "action": "correct",
            "rationale": "Caller invented provenance.",
        }
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode != 0
        assert "script-owned provenance" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_accepts_empty_findings_with_approve(self, tmp_path):
        doc = _valid_findings(
            verdict="approve",
            findings=[],
            summary={
                "total_findings": 0,
                "by_severity": {
                    "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
                },
                "suppressed_advisory_finding_count": 0,
            },
        )
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "RECORDED FINDINGS: 0 (critical 0, high 0, medium 0, low 0)" in (
            result.stdout
        )

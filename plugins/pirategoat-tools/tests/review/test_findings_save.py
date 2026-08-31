"""Tests for review/findings_save.py — the reconciliator's validating save
channel for review-findings.json.

Sibling design to review/critic.py's TestCriticSave: this is the ONLY
channel the review-reconciliator agent is allowed to write
review-findings.json through (agents/review-reconciliator.md). It validates
the whole ledger document and writes it atomically via
critic_adjustments.write_findings() — the single sanctioned write path — or
writes nothing at all. It also stamps the run's pipeline-owned reconciliation
facts onto the ledger from reconciliation-context.json, so the agent authors
review content and its four judgment counts and nothing else.
"""

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "review" / "findings_save.py"
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from helpers.review_fixtures import (
    apply_schema,
    canonical_findings_ledger,
    rejected_schema_values,
)
from review import run_paths
from review.findings_save import run_save

CONTEXT_FILENAME = run_paths.artifact_path(
    "", "reconciliation_context"
).name

# The class below is about the ledger's own validity, not about the run it
# reconciled, so every test in it saves against one default context: a single
# reviewing agent whose input findings exceed every grouped-concern count
# those ledgers claim. The tests that ARE about the stamped facts write their
# own context instead.
_DEFAULT_CONTEXT_INPUT_FINDINGS = 12


def _write_context(
    output_dir, reviews_by_agent, *, dispatched=None, missing=None, banner=None
):
    """Write the reconciliation context findings_save.py stamps from."""
    context = {
        "schema": 3,
        "reviews_by_agent": reviews_by_agent,
        "missing_agents": missing,
        "host_context_banner": banner,
        "prefiltered_out_of_scope": {"count": 0, "by_agent": {}},
    }
    if dispatched is not None:
        context["dispatched_agents"] = dispatched
    path = run_paths.artifact_path(output_dir, "reconciliation_context")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(context))
    return path


def _args(output_dir, findings_path):
    return types.SimpleNamespace(
        output_dir=str(output_dir), findings=str(findings_path)
    )


def _valid_findings(**overrides):
    doc = canonical_findings_ledger()
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
    # The reconciliator authors its four judgments and nothing else:
    # findings_save.py stamps every pipeline-owned field from the context.
    doc["meta"]["reconciliation"] = {
        "grouped_concern_count": finding_count,
        "verified_concern_count": finding_count,
        "false_positive_concern_count": 0,
        "out_of_scope_concern_count": 0,
    }
    if meta_override is not None:
        doc["meta"].update(meta_override)
    return doc


class TestFindingsSave:
    @pytest.fixture(autouse=True)
    def _default_context(self, tmp_path):
        _write_context(tmp_path, {
            "security-review": {
                "verdict": "request_changes",
                "findings": [
                    {"severity": "high"}
                ] * _DEFAULT_CONTEXT_INPUT_FINDINGS,
                "checks": [],
            },
        }, dispatched=["security-review"], missing=[])

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

    @pytest.mark.parametrize("schema", rejected_schema_values(3))
    def test_rejects_schema_other_than_the_exact_ledger_integer(
        self, tmp_path, schema
    ):
        findings = self._write_findings(
            tmp_path, apply_schema(_valid_findings(), schema)
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
        assert {
            path.relative_to(tmp_path)
            for path in tmp_path.rglob("*")
            if path.is_file()
        } == {
            Path(findings.name),
            Path("synthesis") / CONTEXT_FILENAME,
        }

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
        assert "severity is invalid" in result.stdout
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
        """Producer problems are collected, not reported one at a time —
        the canonical validator raises on the first shape error it meets,
        so this gate is the only place a caller learns everything it got
        wrong about actor ownership in a single run."""
        doc = _valid_findings(applied_critic_adjustments=[])
        doc["findings"][0]["critic_adjustment"] = {
            "action": "correct", "rationale": "Caller invented provenance.",
        }
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


# =============================================================================
# Pipeline-owned facts are stamped from reconciliation-context.json
# =============================================================================


def test_save_stamps_pipeline_facts_from_context(tmp_path):
    _write_context(tmp_path, {
        "security-review": {
            "verdict": "request_changes",
            "findings": [{"severity": "high"}],
            "checks": [],
        },
        "a11y-review": {
            "verdict": "not_applicable",
            "skip_reason": "No UI.",
            "findings": [],
            "checks": [],
        },
        "code-review": {"verdict": "approve", "findings": [], "checks": []},
    }, dispatched=[
        "a11y-review", "code-review", "security-review", "perf-review",
    ], missing=["perf-review"])
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings()))

    assert run_save(_args(tmp_path, staged)) == 0

    recorded = json.loads(
        (tmp_path / "review-findings.json").read_text()
    )["meta"]["reconciliation"]
    assert recorded["input_finding_count"] == 1
    assert recorded["contributing_agent_count"] == 1
    assert recorded["reviewing_agents"] == ["code-review", "security-review"]
    assert recorded["not_applicable_agents"] == [
        {"name": "a11y-review", "skip_reason": "No UI."},
    ]
    assert recorded["dispatched_agents"] == [
        "a11y-review", "code-review", "security-review", "perf-review",
    ]
    assert recorded["missing_agents"] == ["perf-review"]


def test_save_rejects_verified_count_that_disagrees_with_findings(
    tmp_path, capsys
):
    _write_context(tmp_path, {
        "security-review": {"verdict": "approve", "findings": [], "checks": []},
    })
    doc = _valid_findings()
    doc["meta"]["reconciliation"]["verified_concern_count"] = 2
    doc["meta"]["reconciliation"]["grouped_concern_count"] = 2
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(doc))

    assert run_save(_args(tmp_path, staged)) == 1
    assert "verified_concern_count" in capsys.readouterr().out
    assert not (tmp_path / "review-findings.json").exists()


def test_save_rejects_pipeline_fields_authored_by_the_agent(tmp_path, capsys):
    _write_context(tmp_path, {
        "security-review": {"verdict": "approve", "findings": [], "checks": []},
    })
    doc = _valid_findings()
    doc["meta"]["reconciliation"]["missing_agents"] = []
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(doc))

    assert run_save(_args(tmp_path, staged)) == 1
    assert "pipeline-owned" in capsys.readouterr().out


def test_save_rejects_grouped_count_above_the_input_population(
    tmp_path, capsys
):
    """The judgment the agent authors has to fit the inputs the pipeline
    measured — more concerns than findings read is arithmetic nothing in
    the run can support."""
    _write_context(tmp_path, {
        "security-review": {"verdict": "approve", "findings": [], "checks": []},
    })
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings()))

    assert run_save(_args(tmp_path, staged)) == 1
    assert "grouped_concern_count exceeds" in capsys.readouterr().out
    assert not (tmp_path / "review-findings.json").exists()


def test_save_copies_degraded_host_banner(tmp_path):
    banner = {
        "degraded": True,
        "reason": "partial_unresolved",
        "message": "m",
        "unresolved": [],
    }
    _write_context(tmp_path, {
        "security-review": {
            "verdict": "request_changes",
            "findings": [{"severity": "high"}],
            "checks": [],
        },
    }, banner=banner)
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings()))

    assert run_save(_args(tmp_path, staged)) == 0
    saved = json.loads((tmp_path / "review-findings.json").read_text())
    assert saved["host_context_banner"] == banner


def test_save_leaves_an_undegraded_host_banner_off_the_ledger(tmp_path):
    _write_context(tmp_path, {
        "security-review": {
            "verdict": "request_changes",
            "findings": [{"severity": "high"}],
            "checks": [],
        },
    }, banner=None)
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings()))

    assert run_save(_args(tmp_path, staged)) == 0
    saved = json.loads((tmp_path / "review-findings.json").read_text())
    assert "host_context_banner" not in saved


def test_save_rejects_advisory_finding_without_advisory_source(
    tmp_path, capsys
):
    _write_context(tmp_path, {
        "security-review": {"verdict": "approve", "findings": [], "checks": []},
    })
    doc = _valid_findings()
    doc["findings"][0]["channel"] = "advisory"
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(doc))

    assert run_save(_args(tmp_path, staged)) == 1
    assert "advisory" in capsys.readouterr().out


def test_save_accepts_an_advisory_finding_a_source_review_carried(tmp_path):
    _write_context(tmp_path, {
        "security-review": {
            "verdict": "comment",
            "findings": [{"severity": "high", "channel": "advisory"}],
            "checks": [],
        },
    })
    doc = _valid_findings(
        verdict="approve",
        summary={
            "total_findings": 1,
            "by_severity": {
                "critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0,
            },
            "suppressed_advisory_finding_count": 1,
            "verdict_without_advisory": "request_changes",
        },
    )
    doc["findings"][0]["channel"] = "advisory"
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(doc))

    assert run_save(_args(tmp_path, staged)) == 0
    saved = json.loads((tmp_path / "review-findings.json").read_text())
    assert saved["findings"][0]["channel"] == "advisory"


def test_save_rejects_a_run_with_no_reconciliation_context(tmp_path, capsys):
    """The context is the only source for the stamped facts, so its absence
    is a rejection rather than a ledger missing half its stamped facts."""
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings()))

    assert run_save(_args(tmp_path, staged)) == 1
    assert CONTEXT_FILENAME in capsys.readouterr().out
    assert not (tmp_path / "review-findings.json").exists()


def test_save_rejects_a_host_context_banner_authored_by_the_agent(
    tmp_path, capsys
):
    """The banner is a pipeline fact like the six reconciliation rosters:
    an agent-authored one is a claim about the run's host that nothing
    measured, so it is refused rather than quietly replaced."""
    _write_context(tmp_path, {
        "security-review": {
            "verdict": "request_changes",
            "findings": [{"severity": "high"}],
            "checks": [],
        },
    }, banner=None)
    doc = _valid_findings()
    doc["host_context_banner"] = {
        "degraded": True,
        "reason": "fully_unavailable",
        "message": "invented",
        "unresolved": [],
    }
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(doc))

    assert run_save(_args(tmp_path, staged)) == 1
    out = capsys.readouterr().out
    assert "pipeline-owned field: host_context_banner" in out
    assert not (tmp_path / "review-findings.json").exists()


@pytest.mark.parametrize("schema", rejected_schema_values(3))
def test_save_rejects_a_context_written_at_another_schema(
    tmp_path, capsys, schema
):
    """The context reader accepts exactly the schema it was written against."""
    path = _write_context(tmp_path, {
        "security-review": {"verdict": "approve", "findings": [], "checks": []},
    })
    path.write_text(json.dumps(
        apply_schema(json.loads(path.read_text()), schema)
    ))
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings()))
    assert run_save(_args(tmp_path, staged)) == 1
    out = capsys.readouterr().out
    assert "REJECTED:" in out and "schema" in out
    assert not (tmp_path / "review-findings.json").exists()


@pytest.mark.parametrize("raw, expectation", [
    pytest.param(
        "x" * 5000,
        lambda value: len(value) <= 4096 and value.endswith("…"),
        id="over-long",
    ),
    pytest.param(
        "not a\x07 WooCommerce\x00 codebase",
        lambda value: value == "not a WooCommerce codebase",
        id="control-characters",
    ),
    pytest.param(
        "\x01\x02", lambda value: value.strip() != "", id="all-unprintable",
    ),
])
def test_the_stamp_fits_a_skip_reason_inside_the_ledger_bound(
    tmp_path, capsys, raw, expectation
):
    """The pipeline owns this field, so the pipeline makes it fit.

    `mark_not_applicable()` does not bound `skip_reason`, and
    `stamp_pipeline_facts` copies it into
    `meta.reconciliation.not_applicable_agents[].skip_reason`, which the
    ledger validator caps at 4096 characters with no control characters.
    An over-long or control-bearing reason is the one REJECTED: line the
    reconciliator cannot act on — it did not author the field and cannot
    rewrite it, so the run dead-ends on a reviewer's prose.
    """
    _write_context(tmp_path, {
        "security-review": {
            "verdict": "not_applicable", "skip_reason": raw,
            "findings": [], "checks": [],
        },
        # A reviewing agent beside it: the ledger's one grouped concern
        # needs an input finding to have been grouped FROM, or the save
        # rejects the count and the skip reason never gets its turn.
        "code-review": {
            "verdict": "comment",
            "findings": [{"id": "f1"}, {"id": "f2"}],
            "checks": [],
        },
    })
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings()))

    assert run_save(_args(tmp_path, staged)) == 0, capsys.readouterr().out

    ledger = json.loads((tmp_path / "review-findings.json").read_text())
    entry = ledger["meta"]["reconciliation"]["not_applicable_agents"][0]
    assert entry["name"] == "security-review"
    assert expectation(entry["skip_reason"])


@pytest.mark.parametrize("entry", [
    "not-an-object",
    {"findings": []},
    {"verdict": "approve"},
    {"verdict": "approve", "findings": None},
    {"verdict": "bogus", "findings": []},
    {"verdict": "approve", "findings": ["not-a-finding"]},
    {"verdict": "not_applicable", "findings": []},
    {"verdict": "not_applicable", "skip_reason": "  ", "findings": []},
    {
        "verdict": "not_applicable",
        "skip_reason": "No UI.",
        "findings": [{"severity": "low"}],
    },
    {"verdict": "approve", "skip_reason": "No UI.", "findings": []},
])
def test_save_rejects_a_context_review_entry_of_the_wrong_shape(
    tmp_path, capsys, entry
):
    """Rosters and counts are stamped from entries the context vouches for."""
    _write_context(tmp_path, {"security-review": entry})
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings()))
    assert run_save(_args(tmp_path, staged)) == 1
    assert "reviews_by_agent['security-review']" in capsys.readouterr().out
    assert not (tmp_path / "review-findings.json").exists()

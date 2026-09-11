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
from review.reconciliation_context import RECONCILIATION_CONTEXT_SCHEMA
from review.reconciliation_notes import add_note

CONTEXT_FILENAME = run_paths.artifact_path(
    "", "reconciliation_context"
).name

# The class below is about the ledger's own validity, not about the run it
# reconciled, so every test in it saves against one default context: a single
# reviewing agent whose input findings exceed every grouped-concern count
# those ledgers claim. The tests that ARE about the stamped facts write their
# own context instead.
_DEFAULT_CONTEXT_INPUT_FINDINGS = 12


def _reviews(**stems):
    """{stem: (finding_count, check_methods)} → reviews_by_agent."""
    reviews = {}
    for stem, (finding_count, methods) in stems.items():
        reviews[stem] = {
            "verdict": "request_changes" if finding_count else "approve",
            "findings": [
                {"id": f"f{i}", "severity": "high"}
                for i in range(1, finding_count + 1)
            ],
            "checks": [
                {"id": f"c{i}", "question": f"q{i}", "method": method,
                 "result": "0 hits", "source_reviewers": [stem]}
                for i, method in enumerate(methods, start=1)
            ],
        }
    return reviews



def _review(finding_count, *, severity="high", verdict="request_changes"):
    """One reviewer's context entry with an explicit verdict and severity."""
    review = _reviews(**{"security-review": (finding_count, ["grep"])})["security-review"]
    review["verdict"] = verdict
    for finding in review["findings"]:
        finding["severity"] = severity
    return review


_DEFAULT_REVIEWS = _reviews(**{"security-review": (_DEFAULT_CONTEXT_INPUT_FINDINGS, ["grep"])})


from helpers.review_fixtures import write_reconciliation_context as _write_context  # noqa: E402


def _args(output_dir, findings_path):
    return types.SimpleNamespace(
        output_dir=str(output_dir), findings=str(findings_path)
    )


def _sources_of(reviews, collection):
    return [
        (stem, entry["id"])
        for stem, review in reviews.items()
        for entry in (review.get(collection) or [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    ]


def _valid_findings(context=None, **overrides):
    """A ledger that accounts for every source in `context`.

    The ledger's f1 merges the first source finding and its c1 the first
    source check; every other source is dropped (false positive / void)
    so the accounting gate is satisfied by construction. Overrides win,
    including a replaced `findings` list — the drops are recomputed from
    whatever the final ledger cites.
    """
    reviews = _DEFAULT_REVIEWS if context is None else context
    finding_sources = _sources_of(reviews, "findings")
    check_sources = _sources_of(reviews, "checks")
    doc = canonical_findings_ledger()
    findings = []
    if finding_sources:
        stem, fid = finding_sources[0]
        findings.append({
            "id": "f1",
            "category": "security",
            "severity": "high",
            "title": "Unsanitized input",
            "description": "User input reaches the query unsanitized.",
            "file": "src/foo.php",
            "line": 42,
            "recommendation": "Sanitize before use.",
            "confidence": 0.9,
            "sources": [{"reviewer": stem, "id": fid}],
        })
    checks = []
    if check_sources:
        stem, cid = check_sources[0]
        source = next(
            c for c in reviews[stem]["checks"] if c.get("id") == cid
        )
        checks.append({
            "id": "c1",
            "question": "Are there any callers?",
            "method": source.get("method", "grep"),
            "result": "0 hits",
            "source_reviewers": ["security"],
            "sources": [{"reviewer": stem, "id": cid}],
        })
    doc.update({
        "timestamp": "2026-08-26T10:00:00+00:00",
        "plugin_version": "1.114.0",
        "verdict": "request_changes" if findings else "approve",
        "findings": findings,
        "summary": {
            "total_findings": len(findings),
            "by_severity": {
                "critical": 0, "high": len(findings), "medium": 0,
                "low": 0, "info": 0,
            },
            "suppressed_advisory_finding_count": 0,
        },
        "assessment": "One high-severity finding found.",
        "checks": checks,
    })
    meta_override = overrides.pop("meta", None)
    doc.update(overrides)
    findings = doc.get("findings")
    checks = doc.get("checks")
    finding_count = len(findings) if isinstance(findings, list) else 0
    check_count = len(checks) if isinstance(checks, list) else 0
    cited_findings = {
        (s["reviewer"], s["id"])
        for f in (findings if isinstance(findings, list) else [])
        if isinstance(f, dict)
        for s in (f.get("sources") or [])
        if isinstance(s, dict)
    }
    cited_checks = {
        (s["reviewer"], s["id"])
        for c in (checks if isinstance(checks, list) else [])
        if isinstance(c, dict)
        for s in (c.get("sources") or [])
        if isinstance(s, dict)
    }
    doc.setdefault("dropped_findings", [
        {"reviewer": stem, "id": fid, "reason": "false_positive",
         "evidence": "fixture: not a real defect"}
        for stem, fid in finding_sources if (stem, fid) not in cited_findings
    ])
    doc.setdefault("dropped_checks", [
        {"reviewer": stem, "id": cid, "reason": "void",
         "evidence": "fixture: the method could not find it"}
        for stem, cid in check_sources if (stem, cid) not in cited_checks
    ])
    doc.setdefault("orchestrator_notes", [])
    doc["meta"].update({
        "next_finding_number": finding_count + 1,
        "next_check_number": check_count + 1,
    })
    # The reconciliator authors its four judgments and nothing else:
    # findings_save.py stamps every pipeline-owned field from the context.
    dropped_fp = any(
        d.get("reason") == "false_positive"
        for d in doc["dropped_findings"] if isinstance(d, dict)
    )
    doc["meta"]["reconciliation"] = {
        "grouped_concern_count": finding_count + (1 if dropped_fp else 0),
        "verified_concern_count": finding_count,
        "false_positive_concern_count": 1 if dropped_fp else 0,
        "out_of_scope_concern_count": 0,
    }
    if meta_override is not None:
        doc["meta"].update(meta_override)
    return doc


class TestFindingsSave:
    @pytest.fixture(autouse=True)
    def _default_context(self, tmp_path):
        _write_context(
            tmp_path, _DEFAULT_REVIEWS,
            dispatched=["security-review"], missing=[],
        )

    def _run_save(self, output_dir, findings_path, capsys):
        code = run_save(_args(output_dir, findings_path))
        captured = capsys.readouterr()
        return types.SimpleNamespace(returncode=code, stdout=captured.out, stderr=captured.err)

    def _run_save_subprocess(self, output_dir, findings_path):
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

        result = self._run_save_subprocess(tmp_path, findings)

        assert result.returncode == 0, result.stdout + result.stderr
        saved = json.loads((tmp_path / "review-findings.json").read_text())
        assert saved["findings"][0]["id"] == "f1"
        assert saved["checks"][0]["source_reviewers"] == ["security"]
        assert saved["assessment"] == "One high-severity finding found."
        assert saved["schema"] == 3
        assert "issues" not in saved
        assert "clearances" not in saved
        assert "narrative_summary" not in saved

    @pytest.mark.parametrize(
        "schema",
        [v for v in rejected_schema_values(3) if v.id in ("prior-schema", "bool", "absent")],
    )
    def test_rejects_schema_other_than_the_exact_ledger_integer(
        self, tmp_path, capsys, schema
    ):
        findings = self._write_findings(
            tmp_path, apply_schema(_valid_findings(), schema)
        )

        result = self._run_save(tmp_path, findings, capsys)

        assert result.returncode != 0
        assert "schema" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_summary_without_advisory_finding_count(self, tmp_path, capsys):
        doc = _valid_findings()
        del doc["summary"]["suppressed_advisory_finding_count"]
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings, capsys)

        assert result.returncode != 0
        assert "summary does not match" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_echo_format(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (8, ["grep"] * 12)})
        for index, severity in enumerate(
            ["high", "medium", "medium", "medium",
             "medium", "medium", "medium", "low"]
        ):
            reviews["security-review"]["findings"][index]["severity"] = severity
        _write_context(tmp_path, reviews)
        doc = _valid_findings(
            context=reviews,
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
                    "sources": [{
                        "reviewer": "security-review", "id": f"f{i + 1}",
                    }],
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
                "sources": [{
                    "reviewer": "security-review", "id": f"c{i + 1}",
                }],
            } for i in range(12)],
        )
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings, capsys)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "RECORDED VERDICT: request_changes" in result.stdout
        assert (
            "RECORDED FINDINGS: 8 (critical 0, high 1, medium 6, low 1)"
            in result.stdout
        )
        assert "CHECKS: 12 | ASSESSMENT: present" in result.stdout

        # Second row: absent assessment and no checks (restore the default
        # single-reviewer context this scenario's ledger accounts for).
        _write_context(
            tmp_path, _DEFAULT_REVIEWS, dispatched=["security-review"], missing=[],
        )
        doc2 = _valid_findings(assessment=None, checks=[])
        findings2 = self._write_findings(tmp_path, doc2, name="f2.json")

        result2 = self._run_save(tmp_path, findings2, capsys)

        assert result2.returncode == 0, result2.stdout + result2.stderr
        assert "CHECKS: 0 | ASSESSMENT: absent" in result2.stdout

        # Third row: zero findings on an approve verdict — the by-severity
        # echo's all-zero spelling, otherwise unpinned once
        # test_accepts_empty_findings_with_approve folded into
        # test_accepts_every_reconciler_verdict (whose approve row only
        # asserts RECORDED VERDICT, not the RECORDED FINDINGS count).
        reviews3 = {
            "security-review": {"verdict": "approve", "findings": [], "checks": []},
        }
        _write_context(tmp_path, reviews3)
        doc3 = _valid_findings(
            context=reviews3,
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
        findings3 = self._write_findings(tmp_path, doc3, name="f3.json")

        result3 = self._run_save(tmp_path, findings3, capsys)

        assert result3.returncode == 0, result3.stdout + result3.stderr
        assert (
            "RECORDED FINDINGS: 0 (critical 0, high 0, medium 0, low 0)"
            in result3.stdout
        )

    def test_rejects_non_object_top_level(self, tmp_path, capsys):
        findings = self._write_findings(tmp_path, ["not", "an", "object"])

        result = self._run_save(tmp_path, findings, capsys)

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

    def test_rejects_verdict_that_does_not_match_issues(self, tmp_path, capsys):
        findings = self._write_findings(
            tmp_path, _valid_findings(verdict="approve")
        )

        result = self._run_save(tmp_path, findings, capsys)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "verdict does not match its findings" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_issue_missing_required_field(self, tmp_path, capsys):
        doc = _valid_findings()
        del doc["findings"][0]["severity"]
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings, capsys)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "severity" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_missing_findings_file(self, tmp_path, capsys):
        result = self._run_save(tmp_path, tmp_path / "nonexistent.json", capsys)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_invalid_json(self, tmp_path):
        findings = tmp_path / "bad.json"
        findings.write_text("{not valid json")

        result = self._run_save_subprocess(tmp_path, findings)

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_collects_multiple_problems(self, tmp_path, capsys):
        """Producer problems are collected, not reported one at a time —
        the canonical validator raises on the first shape error it meets,
        so this gate is the only place a caller learns everything it got
        wrong about actor ownership in a single run."""
        doc = _valid_findings(applied_critic_adjustments=[])
        doc["findings"][0]["critic_adjustment"] = {
            "action": "correct", "rationale": "Caller invented provenance.",
        }
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings, capsys)

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
                "approve", None,
                {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
            ),
        ],
    )
    def test_accepts_every_reconciler_verdict(
        self, tmp_path, capsys, verdict, severity, counts
    ):
        reviews = {
            "security-review": _review(
                1 if severity is not None else 0,
                severity=severity or "high",
                verdict="request_changes" if severity is not None else "approve",
            ),
        }
        _write_context(tmp_path, reviews)
        findings = []
        if severity is not None:
            finding = _valid_findings(context=reviews)["findings"][0]
            finding["severity"] = severity
            findings.append(finding)
        doc = _valid_findings(
            context=reviews,
            verdict=verdict,
            findings=findings,
            summary={
                "total_findings": len(findings),
                "by_severity": counts,
                "suppressed_advisory_finding_count": 0,
            },
        )
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings, capsys)

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"RECORDED VERDICT: {verdict}" in result.stdout

    def test_rejects_actor_supplied_critic_lifecycle_fields(
        self, tmp_path, capsys
    ):
        findings = self._write_findings(
            tmp_path, _valid_findings(applied_critic_adjustments=[])
        )

        result = self._run_save(tmp_path, findings, capsys)

        assert result.returncode != 0
        assert "critic-owned lifecycle" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()

    def test_rejects_actor_supplied_critic_provenance(
        self, tmp_path, capsys
    ):
        doc = _valid_findings()
        doc["findings"][0]["critic_adjustment"] = {
            "action": "correct",
            "rationale": "Caller invented provenance.",
        }
        findings = self._write_findings(tmp_path, doc)

        result = self._run_save(tmp_path, findings, capsys)

        assert result.returncode != 0
        assert "script-owned provenance" in result.stdout
        assert not (tmp_path / "review-findings.json").exists()


# =============================================================================
# Pipeline-owned facts are stamped from reconciliation-context.json
# =============================================================================


def test_save_stamps_pipeline_facts_from_context(tmp_path):
    reviews = {
        "security-review": {
            "verdict": "request_changes",
            "findings": [{"id": "f1", "severity": "high"}],
            "checks": [],
        },
        "a11y-review": {
            "verdict": "not_applicable",
            "skip_reason": "No UI.",
            "findings": [],
            "checks": [],
        },
        "code-review": {"verdict": "approve", "findings": [], "checks": []},
    }
    _write_context(tmp_path, reviews, dispatched=[
        "a11y-review", "code-review", "security-review", "perf-review",
    ], missing=["perf-review"])
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings(context=reviews)))

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
    reviews = {
        "security-review": {"verdict": "approve", "findings": [], "checks": []},
    }
    _write_context(tmp_path, reviews)
    doc = _valid_findings(context=reviews)
    doc["meta"]["reconciliation"]["verified_concern_count"] = 2
    doc["meta"]["reconciliation"]["grouped_concern_count"] = 2
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(doc))

    assert run_save(_args(tmp_path, staged)) == 1
    assert "verified_concern_count" in capsys.readouterr().out
    assert not (tmp_path / "review-findings.json").exists()


def test_save_rejects_pipeline_fields_authored_by_the_agent(tmp_path, capsys):
    reviews = {
        "security-review": {"verdict": "approve", "findings": [], "checks": []},
    }
    _write_context(tmp_path, reviews)
    doc = _valid_findings(context=reviews)
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
    reviews = {
        "security-review": {"verdict": "approve", "findings": [], "checks": []},
    }
    _write_context(tmp_path, reviews)
    doc = _valid_findings(context=reviews)
    doc["meta"]["reconciliation"]["grouped_concern_count"] = 2
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(doc))

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
    reviews = {
        "security-review": {
            "verdict": "request_changes",
            "findings": [{"id": "f1", "severity": "high"}],
            "checks": [],
        },
    }
    _write_context(tmp_path, reviews, banner=banner)
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings(context=reviews)))

    assert run_save(_args(tmp_path, staged)) == 0
    saved = json.loads((tmp_path / "review-findings.json").read_text())
    assert saved["host_context_banner"] == banner


def test_save_leaves_an_undegraded_host_banner_off_the_ledger(tmp_path):
    reviews = {
        "security-review": {
            "verdict": "request_changes",
            "findings": [{"id": "f1", "severity": "high"}],
            "checks": [],
        },
    }
    _write_context(tmp_path, reviews, banner=None)
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings(context=reviews)))

    assert run_save(_args(tmp_path, staged)) == 0
    saved = json.loads((tmp_path / "review-findings.json").read_text())
    assert "host_context_banner" not in saved


def test_save_rejects_advisory_finding_without_advisory_source(
    tmp_path, capsys
):
    reviews = {
        "security-review": {
            "verdict": "request_changes",
            "findings": [{"id": "f1", "severity": "high"}],
            "checks": [],
        },
    }
    _write_context(tmp_path, reviews)
    doc = _valid_findings(context=reviews)
    doc["findings"][0]["channel"] = "advisory"
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(doc))

    assert run_save(_args(tmp_path, staged)) == 1
    assert "advisory" in capsys.readouterr().out


def test_save_accepts_an_advisory_finding_a_source_review_carried(tmp_path):
    reviews = {
        "security-review": {
            "verdict": "comment",
            "findings": [{"id": "f1", "severity": "high", "channel": "advisory"}],
            "checks": [],
        },
    }
    _write_context(tmp_path, reviews)
    doc = _valid_findings(
        context=reviews,
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
    reviews = {
        "security-review": {
            "verdict": "request_changes",
            "findings": [{"id": "f1", "severity": "high"}],
            "checks": [],
        },
    }
    _write_context(tmp_path, reviews, banner=None)
    doc = _valid_findings(context=reviews)
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


@pytest.mark.parametrize(
    "schema",
    [
        v for v in rejected_schema_values(RECONCILIATION_CONTEXT_SCHEMA)
        if v.id in ("prior-schema", "bool", "absent")
    ],
)
def test_save_rejects_a_context_written_at_another_schema(
    tmp_path, capsys, schema
):
    """The context reader accepts exactly the schema it was written against."""
    reviews = {
        "security-review": {"verdict": "approve", "findings": [], "checks": []},
    }
    path = _write_context(tmp_path, reviews)
    path.write_text(json.dumps(
        apply_schema(json.loads(path.read_text()), schema)
    ))
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings(context=reviews)))
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
    reviews = {
        "security-review": {
            "verdict": "not_applicable", "skip_reason": raw,
            "findings": [], "checks": [],
        },
        # A reviewing agent beside it: the ledger's one grouped concern
        # needs an input finding to have been grouped FROM, or the save
        # rejects the count and the skip reason never gets its turn.
        "code-review": {
            "verdict": "comment",
            "findings": [
                {"id": "f1", "severity": "high"},
                {"id": "f2", "severity": "high"},
            ],
            "checks": [],
        },
    }
    _write_context(tmp_path, reviews)
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_valid_findings(context=reviews)))

    assert run_save(_args(tmp_path, staged)) == 0, capsys.readouterr().out

    ledger = json.loads((tmp_path / "review-findings.json").read_text())
    entry = ledger["meta"]["reconciliation"]["not_applicable_agents"][0]
    assert entry["name"] == "security-review"
    assert expectation(entry["skip_reason"])


@pytest.mark.parametrize("entry", [
    pytest.param("not-an-object", id="not-an-object"),
    pytest.param(
        {"verdict": "bogus", "findings": []}, id="verdict-outside-vocabulary",
    ),
    pytest.param(
        {"verdict": "not_applicable", "findings": []},
        id="not-applicable-without-skip-reason",
    ),
    pytest.param(
        {"verdict": "approve", "skip_reason": "No UI.", "findings": []},
        id="approve-with-skip-reason",
    ),
])
def test_save_rejects_a_context_review_entry_of_the_wrong_shape(
    tmp_path, capsys, entry
):
    """Rosters and counts are stamped from entries the context vouches for."""
    reviews = {"security-review": entry}
    _write_context(tmp_path, reviews)
    staged = tmp_path / "staged.json"
    ledger_context = reviews if isinstance(entry, dict) else {}
    staged.write_text(json.dumps(_valid_findings(context=ledger_context)))
    assert run_save(_args(tmp_path, staged)) == 1
    assert "reviews_by_agent['security-review']" in capsys.readouterr().out
    assert not (tmp_path / "review-findings.json").exists()


def _save(tmp_path, doc, capsys):
    path = tmp_path / "f.json"
    path.write_text(json.dumps(doc))
    code = run_save(_args(tmp_path, path))
    return code, capsys.readouterr().out


class TestAccountingGate:
    """Every source finding and check is merged or dropped, on the record."""

    def _drop(reviewer, id, reason="false_positive"):
        return {"reviewer": reviewer, "id": id, "reason": reason, "evidence": "e"}

    @pytest.mark.parametrize("stems, mutate, fragment", [
        pytest.param(
            {"security-review": (2, ["grep"])},
            lambda doc: doc.__setitem__("dropped_findings", []),  # f2 neither merged nor dropped
            "REJECTED: source finding security-review:f2 is neither merged into a finding nor dropped",
            id="unaccounted-source-finding",
        ),
        pytest.param(
            {"security-review": (1, ["grep", "git log -S"])},
            lambda doc: doc.__setitem__("dropped_checks", []),
            "REJECTED: source check security-review:c2 is neither merged into a check nor dropped",
            id="unaccounted-source-check",
        ),
        pytest.param(
            {"security-review": (1, ["grep"])},
            lambda doc: doc.__setitem__("dropped_findings", [TestAccountingGate._drop("security-review", "f1")]),
            "security-review:f1 is both merged into findings[0] and dropped",
            id="source-claimed-twice",
        ),
        pytest.param(
            {"security-review": (1, ["grep"])},
            lambda doc: doc["findings"][0].__setitem__("sources", [{"reviewer": "code-review", "id": "f1"}]),
            "findings[0] cites unknown source code-review:f1",
            id="unknown-source",
        ),
        pytest.param(
            {"security-review": (2, ["grep"])},
            lambda doc: doc.__setitem__("dropped_findings", doc["dropped_findings"] * 2),
            "REJECTED: security-review:f2 is dropped twice",
            id="finding-dropped-twice",
        ),
        pytest.param(
            {"security-review": (1, ["grep", "rg"])},
            lambda doc: doc.__setitem__("dropped_checks", doc["dropped_checks"] * 2),
            "REJECTED: security-review:c2 is dropped twice",
            id="check-dropped-twice",
        ),
    ])
    def test_a_broken_evidence_trail_rejects_the_save(self, tmp_path, capsys, stems, mutate, fragment):
        reviews = _reviews(**stems)
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)
        mutate(doc)
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert fragment in out
        assert not (tmp_path / "review-findings.json").exists()

    def test_an_unknown_drop_reason_is_named(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (2, ["grep", "rg"])})
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)
        doc["dropped_findings"][0]["reason"] = "duplicate"
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert (
            "REJECTED: dropped_findings[0] has an unknown reason 'duplicate' "
            "(allowed: false_positive, out_of_scope, prefiltered)"
        ) in out

    def test_an_entry_naming_no_sources_rejects_the_save(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (1, ["grep"])})
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)
        doc["findings"][0]["sources"] = []
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "REJECTED: findings[0] names no sources" in out

    def test_a_malformed_sources_entry_rejects_the_save(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (1, ["grep"])})
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)
        doc["findings"][0]["sources"] = [{"reviewer": "security-review"}]
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "REJECTED: findings[0] has a malformed sources entry" in out

    def test_a_merged_check_must_carry_every_source_method_verbatim(self, tmp_path, capsys):
        reviews = _reviews(**{
            "security-review": (1, ["git grep -n wp_delete_post"]),
            "code-review": (0, ["rg 'wp_delete_post\\(' includes/"]),
        })
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)
        doc["checks"][0]["sources"] = [
            {"reviewer": "security-review", "id": "c1"},
            {"reviewer": "code-review", "id": "c1"},
        ]
        doc["checks"][0]["method"] = "git grep -n wp_delete_post; rg across includes/"
        doc["dropped_checks"] = []
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "checks[0] merges code-review:c1 but does not carry its method verbatim" in out

        doc["checks"][0]["method"] = (
            "git grep -n wp_delete_post | rg 'wp_delete_post\\(' includes/"
        )
        code, out = _save(tmp_path, doc, capsys)
        assert code == 0, out
        saved = json.loads((tmp_path / "review-findings.json").read_text())
        assert saved["checks"][0]["sources"] == doc["checks"][0]["sources"]

    def test_a_merged_check_keeps_the_union_of_its_sources_verifies(self, tmp_path, capsys):
        reviews = _reviews(**{
            "security-review": (1, ["git grep -n wp_delete_post"]),
            "code-review": (0, ["rg 'wp_delete_post\\(' includes/"]),
        })
        reviews["security-review"]["checks"][0]["verifies"] = ["V1"]
        reviews["code-review"]["checks"][0]["verifies"] = ["V2"]
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)
        doc["checks"][0]["sources"] = [
            {"reviewer": "security-review", "id": "c1"},
            {"reviewer": "code-review", "id": "c1"},
        ]
        doc["checks"][0]["method"] = (
            "git grep -n wp_delete_post | rg 'wp_delete_post\\(' includes/"
        )
        doc["checks"][0]["verifies"] = ["V1"]
        doc["dropped_checks"] = []
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "checks[0] merges code-review:c1 but drops its verifies V2" in out

        doc["checks"][0]["verifies"] = ["V1", "V2"]
        code, out = _save(tmp_path, doc, capsys)
        assert code == 0, out
        saved = json.loads((tmp_path / "review-findings.json").read_text())
        assert saved["checks"][0]["verifies"] == ["V1", "V2"]

    def test_a_severity_matching_no_source_needs_a_note(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (1, ["grep"])})
        reviews["security-review"]["findings"][0]["severity"] = "medium"
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)  # ledger f1 is high
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "findings[0] is high but its sources are medium; severity_note is required" in out

        doc["findings"][0]["severity_note"] = "Reachable from GET; the reviewer under-rated it."
        code, out = _save(tmp_path, doc, capsys)
        assert code == 0, out
        saved = json.loads((tmp_path / "review-findings.json").read_text())
        assert saved["findings"][0]["sources"] == [
            {"reviewer": "security-review", "id": "f1", "severity": "medium"}
        ]

    def test_source_severity_is_stamped_from_the_context(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (1, ["grep"])})
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)
        doc["findings"][0]["sources"][0]["severity"] = "low"  # agent-authored: overwritten
        code, out = _save(tmp_path, doc, capsys)
        assert code == 0, out
        saved = json.loads((tmp_path / "review-findings.json").read_text())
        assert saved["findings"][0]["sources"][0]["severity"] == "high"

    def test_prefiltered_sources_must_be_dropped_as_prefiltered(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (2, ["grep"])})
        reviews["security-review"]["findings"][1]["prefiltered"] = "OUT_OF_SCOPE:file_not_in_diff"
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)  # drops f2 as false_positive
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "security-review:f2 was prefiltered by the pipeline and must be dropped as prefiltered" in out

        doc["dropped_findings"] = [{"reviewer": "security-review", "id": "f2", "reason": "prefiltered"}]
        doc["meta"]["reconciliation"].update({
            "grouped_concern_count": 2, "false_positive_concern_count": 0,
            "out_of_scope_concern_count": 1,
        })
        code, out = _save(tmp_path, doc, capsys)
        assert code == 0, out
        saved = json.loads((tmp_path / "review-findings.json").read_text())
        assert saved["dropped_findings"][0]["scope_status"] == "OUT_OF_SCOPE:file_not_in_diff"

    def test_a_prefiltered_source_cannot_be_merged(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (1, ["grep"])})
        reviews["security-review"]["findings"][0]["prefiltered"] = (
            "OUT_OF_SCOPE:file_not_in_diff"
        )
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)

        code, out = _save(tmp_path, doc, capsys)

        assert code == 1
        assert (
            "security-review:f1 was prefiltered by the pipeline and cannot be "
            "merged into findings[0]; it must be dropped as prefiltered"
        ) in out
        assert not (tmp_path / "review-findings.json").exists()

    def test_a_non_prefiltered_source_cannot_be_dropped_as_prefiltered(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (2, ["grep"])})
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)
        doc["dropped_findings"] = [{"reviewer": "security-review", "id": "f2", "reason": "prefiltered"}]
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "security-review:f2 was not prefiltered by the pipeline" in out

    def test_judgment_counts_agree_with_the_drops(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (3, ["grep"])})
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)  # f2, f3 dropped as false positives
        doc["meta"]["reconciliation"].update({
            "grouped_concern_count": 4, "false_positive_concern_count": 3,
        })
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "false_positive_concern_count 3 exceeds the 2 findings dropped as false positives" in out

        doc["meta"]["reconciliation"].update({
            "grouped_concern_count": 1, "false_positive_concern_count": 0,
        })
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "2 findings were dropped as false positives but false_positive_concern_count is 0" in out

    @pytest.mark.parametrize("notes, existing_ledger", [
        pytest.param(..., False, id="missing"),
        pytest.param([None], True, id="non-object-entry"),
        pytest.param(
            [{"id": "n1", "note": "a"}, {"id": "n3", "note": "b"}], False,
            id="non-contiguous",
        ),
    ])
    def test_malformed_note_collection_rejects_save_without_writing(
        self, tmp_path, capsys, notes, existing_ledger
    ):
        reviews = _reviews(**{"security-review": (1, ["grep"])})
        context_path = _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)
        ledger_path = tmp_path / "review-findings.json"
        before_ledger = None
        if existing_ledger:
            code, out = _save(tmp_path, doc, capsys)
            assert code == 0, out
            before_ledger = ledger_path.read_bytes()
        context = json.loads(context_path.read_text())
        if notes is ...:
            context.pop("orchestrator_notes")
        else:
            context["orchestrator_notes"] = notes
        context_path.write_text(json.dumps(context))
        before_context = context_path.read_bytes()

        code, out = _save(tmp_path, _valid_findings(context=reviews), capsys)

        assert code == 1
        assert "REJECTED: reconciliation-context.json orchestrator_notes" in out
        assert context_path.read_bytes() == before_context
        if existing_ledger:
            assert ledger_path.read_bytes() == before_ledger
        else:
            assert not ledger_path.exists()

    def test_empty_note_collection_remains_saveable_then_appendable(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (1, ["grep"])})
        path = _write_context(tmp_path, reviews)
        code, out = _save(tmp_path, _valid_findings(context=reviews), capsys)
        assert code == 0, out

        assert add_note(tmp_path, "First claim.")["id"] == "n1"
        assert add_note(tmp_path, "Second claim.")["id"] == "n2"
        before = path.read_bytes()
        doc = _valid_findings(context=reviews)
        doc["orchestrator_notes"] = [
            {"id": "n1", "outcome": "confirmed", "evidence": "First proof."},
            {"id": "n2", "outcome": "refuted", "evidence": "Second proof."},
        ]

        code, out = _save(tmp_path, doc, capsys)

        assert code == 0, out
        saved = json.loads((tmp_path / "review-findings.json").read_text())
        assert saved["orchestrator_notes"] == [
            {"id": "n1", "note": "First claim.", "outcome": "confirmed", "evidence": "First proof."},
            {"id": "n2", "note": "Second claim.", "outcome": "refuted", "evidence": "Second proof."},
        ]
        assert path.read_bytes() == before

    def test_a_confirmed_note_keeps_the_verify_items_it_settles(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (1, ["grep"])})
        _write_context(tmp_path, reviews, notes=[{"id": "n1", "note": "the lockfile regenerates"}])
        doc = _valid_findings(context=reviews)
        doc["orchestrator_notes"] = [
            {"id": "n1", "outcome": "confirmed", "evidence": "deleted and regenerated it", "verifies": ["V2", "V3"]},
        ]
        code, out = _save(tmp_path, doc, capsys)
        assert code == 0, out
        saved = json.loads((tmp_path / "review-findings.json").read_text())
        assert saved["orchestrator_notes"] == [{
            "id": "n1", "note": "the lockfile regenerates", "outcome": "confirmed",
            "evidence": "deleted and regenerated it", "verifies": ["V2", "V3"],
        }]

    def test_every_orchestrator_note_needs_an_outcome(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (1, ["grep"])})
        _write_context(tmp_path, reviews, notes=[
            {"id": "n1", "note": "security f1 and code f1 are one concern"},
        ])
        doc = _valid_findings(context=reviews)
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "orchestrator note n1 has no outcome" in out

        doc["orchestrator_notes"] = [
            {"id": "n1", "outcome": "refuted", "evidence": "different sinks: a.php:4 vs b.php:9"},
        ]
        code, out = _save(tmp_path, doc, capsys)
        assert code == 0, out
        saved = json.loads((tmp_path / "review-findings.json").read_text())
        assert saved["orchestrator_notes"][0]["note"] == "security f1 and code f1 are one concern"

    def test_note_text_is_pipeline_owned(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (1, ["grep"])})
        _write_context(tmp_path, reviews, notes=[{"id": "n1", "note": "x"}])
        doc = _valid_findings(context=reviews)
        doc["orchestrator_notes"] = [
            {"id": "n1", "note": "rewritten", "outcome": "confirmed", "evidence": "e"},
        ]
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "orchestrator_notes[0].note is pipeline-owned" in out

    def test_a_note_id_the_context_does_not_have_is_refused(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (1, ["grep"])})
        _write_context(tmp_path, reviews)
        doc = _valid_findings(context=reviews)
        doc["orchestrator_notes"] = [{"id": "n1", "outcome": "confirmed", "evidence": "e"}]
        code, out = _save(tmp_path, doc, capsys)
        assert code == 1
        assert "orchestrator note n1 is not in the context" in out

    def test_the_echo_reports_the_accounting(self, tmp_path, capsys):
        reviews = _reviews(**{"security-review": (3, ["grep", "rg"])})
        _write_context(tmp_path, reviews)
        code, out = _save(tmp_path, _valid_findings(context=reviews), capsys)
        assert code == 0, out
        assert "ACCOUNTED: findings 3/3 (1 merged, 2 dropped) | checks 2/2 (1 merged, 1 dropped) | notes 0/0" in out

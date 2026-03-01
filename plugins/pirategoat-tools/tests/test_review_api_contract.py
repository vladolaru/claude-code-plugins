"""
Tests for review API contracts — cross-component integration tests.

Verifies the contracts between the three review pipeline layers:
  1. Producer: ReviewOutputBuilder (review_output_simple.py)
  2. Consumer 1: reconcile() (reconcile-reviews.py)
  3. Consumer 2: preprocess_findings() (ingest-preprocess.py)

Each test feeds real output from one layer into the next, catching
interface mismatches like the clusters/issues key bug.

Zero external dependencies beyond stdlib + pytest.
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import all three components
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"

# ReviewOutputBuilder — direct import (underscored filename)
sys.path.insert(0, str(SCRIPTS_DIR))
from review_output_simple import ReviewOutputBuilder

# reconcile-reviews.py — importlib (hyphenated filename)
_reconcile_spec = importlib.util.spec_from_file_location(
    "reconcile_reviews", str(SCRIPTS_DIR / "reconcile-reviews.py")
)
_reconcile_mod = importlib.util.module_from_spec(_reconcile_spec)
_reconcile_spec.loader.exec_module(_reconcile_mod)
reconcile = _reconcile_mod.reconcile

# ingest-preprocess.py — importlib (hyphenated filename)
_ingest_spec = importlib.util.spec_from_file_location(
    "ingest_preprocess", str(SCRIPTS_DIR / "ingest-preprocess.py")
)
_ingest_mod = importlib.util.module_from_spec(_ingest_spec)
_ingest_spec.loader.exec_module(_ingest_mod)
preprocess_findings = _ingest_mod.preprocess_findings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_and_save(tmp_dir, reviewer, issues, pr_id="123"):
    """Create a ReviewOutputBuilder, add issues from spec dicts, and save.

    Each issue dict should have keys matching add_issue params:
    severity, title, file, description, recommendation, and optionally
    category, line, confidence.
    """
    builder = ReviewOutputBuilder(pr_id=pr_id, reviewer=reviewer)
    for spec in issues:
        builder.add_issue(**spec)
    builder.save(tmp_dir)
    return builder


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


# =============================================================================
# TestProducerToReconcileContract
# =============================================================================


class TestProducerToReconcileContract:
    """ReviewOutputBuilder output consumed correctly by reconcile()."""

    def test_single_agent_consumed(self, tmp_dir):
        """Single agent ReviewOutputBuilder output is read by reconcile."""
        _build_and_save(tmp_dir, "security", [
            {"severity": "high", "title": "SQL Injection", "file": "src/db.php",
             "description": "Direct input in query", "recommendation": "Use prepare()"},
        ])

        result = reconcile(tmp_dir)
        assert result["total_findings"] == 1
        assert result["deduplicated_findings"] == 1
        assert len(result["clusters"]) == 1
        assert result["clusters"][0]["canonical"]["title"] == "SQL Injection"

    def test_multi_agent_dedup(self, tmp_dir):
        """Same finding from two agents via ReviewOutputBuilder is deduplicated."""
        _build_and_save(tmp_dir, "security", [
            {"severity": "critical", "title": "SQL Injection", "file": "src/db.php",
             "line": 42, "description": "Direct input in query",
             "recommendation": "Use prepare()"},
        ])
        _build_and_save(tmp_dir, "pr", [
            {"severity": "high", "title": "SQL Injection", "file": "src/db.php",
             "line": 42, "description": "User input passed directly to query",
             "recommendation": "Sanitize input"},
        ])

        result = reconcile(tmp_dir)
        assert result["total_findings"] == 2
        assert result["deduplicated_findings"] == 1
        canonical = result["clusters"][0]["canonical"]
        assert canonical["severity"] == "critical"  # highest wins
        assert set(canonical["source_agents"]) == {"security", "pr"}

    def test_all_issue_fields_survive(self, tmp_dir):
        """Key fields from ReviewOutputBuilder survive reconcile."""
        _build_and_save(tmp_dir, "security", [
            {"severity": "high", "title": "XSS Vulnerability", "file": "src/view.php",
             "line": 10, "description": "Unescaped output in template",
             "recommendation": "Use esc_html()", "category": "xss",
             "confidence": 0.92},
        ])

        result = reconcile(tmp_dir)
        canonical = result["clusters"][0]["canonical"]
        assert canonical["title"] == "XSS Vulnerability"
        assert canonical["file"] == "src/view.php"
        assert canonical["line"] == 10
        assert canonical["severity"] == "high"
        assert canonical["confidence"] == 0.92
        assert canonical["description"] == "Unescaped output in template"
        assert canonical["category"] == "xss"

    def test_non_builder_json_skipped(self, tmp_dir):
        """Non-ReviewOutputBuilder JSON files are skipped gracefully."""
        # Valid builder output
        _build_and_save(tmp_dir, "security", [
            {"severity": "high", "title": "Real Issue", "file": "f.py",
             "description": "desc", "recommendation": "rec"},
        ])
        # Non-builder JSON (no issues key)
        bad_path = os.path.join(tmp_dir, "broken-review.json")
        with open(bad_path, "w") as f:
            json.dump({"status": "ok", "notes": "not a review"}, f)

        result = reconcile(tmp_dir)
        assert result["total_findings"] == 1
        assert "broken" in result["skipped_agents"]

    def test_extra_fields_dont_break_reconcile(self, tmp_dir):
        """Extra kwargs from add_issue don't break reconcile."""
        _build_and_save(tmp_dir, "security", [
            {"severity": "high", "title": "Issue", "file": "f.py",
             "description": "desc", "recommendation": "rec",
             "vulnerability_type": "sqli", "cwe_id": "CWE-89"},
        ])

        result = reconcile(tmp_dir)
        assert result["total_findings"] == 1
        assert result["deduplicated_findings"] == 1


# =============================================================================
# TestReconcileToIngestContract
# =============================================================================


class TestReconcileToIngestContract:
    """reconcile() output consumed correctly by preprocess_findings()."""

    def test_reconcile_output_consumed_by_ingest(self, tmp_dir):
        """Reconcile output is read by ingest — the core contract test.

        This catches the clusters/issues mismatch bug: reconcile writes
        clusters, but ingest reads issues. After the fix, reconcile also
        writes an issues key.
        """
        _build_and_save(tmp_dir, "security", [
            {"severity": "high", "title": "SQL Injection", "file": "src/db.php",
             "line": 10, "description": "Direct input in query",
             "recommendation": "Use prepare()"},
        ])

        # Run reconcile and write output
        reconcile(tmp_dir, write_output=True)

        # Run ingest on the reconcile output
        result = preprocess_findings(
            output_dir=tmp_dir,
            changed_files=["src/db.php"],
            diff_hunks={"src/db.php": [(1, 50)]},
            git_range="main..HEAD",
        )

        # The critical assertion: findings should NOT be empty
        assert len(result["findings"]) > 0, (
            "Ingest got zero findings from reconcile output — "
            "likely reading wrong key (clusters vs issues)"
        )
        assert result["findings"][0]["title"] == "SQL Injection"

    def test_reconcile_output_has_issues_key(self, tmp_dir):
        """Reconcile output contains an 'issues' key for downstream consumers."""
        _build_and_save(tmp_dir, "security", [
            {"severity": "medium", "title": "Issue", "file": "f.py",
             "description": "desc", "recommendation": "rec"},
        ])

        result = reconcile(tmp_dir)
        assert "issues" in result, "Reconcile output missing 'issues' key"
        assert "clusters" in result, "Reconcile output missing 'clusters' key"
        assert len(result["issues"]) == len(result["clusters"])

    def test_ingest_reads_correct_key(self, tmp_dir):
        """Ingest reads the 'issues' key from reconcile output."""
        _build_and_save(tmp_dir, "security", [
            {"severity": "high", "title": "Issue A", "file": "src/a.php",
             "line": 5, "description": "desc A", "recommendation": "rec A"},
            {"severity": "medium", "title": "Issue B", "file": "src/b.php",
             "line": 15, "description": "desc B", "recommendation": "rec B"},
        ])

        reconcile(tmp_dir, write_output=True)
        result = preprocess_findings(
            output_dir=tmp_dir,
            changed_files=["src/a.php", "src/b.php"],
            diff_hunks={"src/a.php": [(1, 50)], "src/b.php": [(1, 50)]},
            git_range="main..HEAD",
        )

        assert result["summary"]["total"] == 2

    def test_empty_reconcile_empty_ingest(self, tmp_dir):
        """Empty reconcile output produces empty ingest output."""
        # No agent output files → empty reconcile
        reconcile(tmp_dir, write_output=True)

        result = preprocess_findings(
            output_dir=tmp_dir,
            changed_files=["src/a.php"],
            diff_hunks={"src/a.php": [(1, 50)]},
            git_range="main..HEAD",
        )

        assert result["findings"] == []
        assert result["summary"]["total"] == 0


# =============================================================================
# TestFullRoundTrip
# =============================================================================


class TestFullRoundTrip:
    """Full 3-layer pipeline: ReviewOutputBuilder → reconcile → ingest."""

    def test_three_agent_pipeline(self, tmp_dir):
        """3 agents → reconcile → ingest: no findings silently dropped."""
        _build_and_save(tmp_dir, "security", [
            {"severity": "critical", "title": "SQL Injection", "file": "src/db.php",
             "line": 42, "description": "Direct input in query",
             "recommendation": "Use prepare()"},
        ])
        _build_and_save(tmp_dir, "pr", [
            {"severity": "high", "title": "SQL Injection", "file": "src/db.php",
             "line": 42, "description": "User input in query without sanitization",
             "recommendation": "Sanitize input"},
            {"severity": "medium", "title": "Missing error handling",
             "file": "src/api.php", "line": 10, "description": "No try/catch",
             "recommendation": "Add error handling"},
        ])
        _build_and_save(tmp_dir, "architecture", [
            {"severity": "low", "title": "God class detected",
             "file": "src/manager.php", "line": 1,
             "description": "Class has too many responsibilities",
             "recommendation": "Split into smaller classes"},
        ])

        # Reconcile
        reconciled = reconcile(tmp_dir, write_output=True)
        # SQL Injection should be deduped (2→1), others unique
        assert reconciled["deduplicated_findings"] == 3

        # Ingest
        result = preprocess_findings(
            output_dir=tmp_dir,
            changed_files=["src/db.php", "src/api.php", "src/manager.php"],
            diff_hunks={
                "src/db.php": [(1, 100)],
                "src/api.php": [(1, 50)],
                "src/manager.php": [(1, 50)],
            },
            git_range="main..HEAD",
        )

        # All 3 deduplicated findings should arrive in ingest
        assert result["summary"]["total"] == 3, (
            f"Expected 3 findings in ingest, got {result['summary']['total']}. "
            f"Findings may have been silently dropped between reconcile and ingest."
        )

    def test_severity_preserved_through_pipeline(self, tmp_dir):
        """Severity from the highest-severity source survives the full pipeline."""
        _build_and_save(tmp_dir, "security", [
            {"severity": "critical", "title": "XSS Vulnerability",
             "file": "src/view.php", "line": 5,
             "description": "Unescaped output", "recommendation": "Escape"},
        ])
        _build_and_save(tmp_dir, "pr", [
            {"severity": "medium", "title": "XSS Vulnerability",
             "file": "src/view.php", "line": 5,
             "description": "Raw HTML output", "recommendation": "Use esc_html()"},
        ])

        reconcile(tmp_dir, write_output=True)
        result = preprocess_findings(
            output_dir=tmp_dir,
            changed_files=["src/view.php"],
            diff_hunks={"src/view.php": [(1, 50)]},
            git_range="main..HEAD",
        )

        assert len(result["findings"]) == 1
        assert result["findings"][0]["severity"] == "critical"

    def test_finding_fields_present_in_ingest(self, tmp_dir):
        """All key finding fields survive the full pipeline to ingest output."""
        _build_and_save(tmp_dir, "security", [
            {"severity": "high", "title": "CSRF Missing", "file": "src/form.php",
             "line": 25, "description": "No nonce verification",
             "recommendation": "Add wp_verify_nonce()", "category": "csrf",
             "confidence": 0.88},
        ])

        reconcile(tmp_dir, write_output=True)
        result = preprocess_findings(
            output_dir=tmp_dir,
            changed_files=["src/form.php"],
            diff_hunks={"src/form.php": [(1, 50)]},
            git_range="main..HEAD",
        )

        finding = result["findings"][0]
        assert finding["title"] == "CSRF Missing"
        assert finding["file"] == "src/form.php"
        assert finding["line"] == 25
        assert finding["severity"] == "high"
        assert finding["confidence"] == 0.88
        assert finding["description"] == "No nonce verification"
        assert finding["category"] == "csrf"

"""
Tests for review API contracts — cross-component integration tests.

Verifies the contracts between the review pipeline layers:
  1. Producer: ReviewOutputBuilder (review_output_simple.py)
  2. Consumer: reconcile() (reconcile-reviews.py)

Each test feeds real output from one layer into the next, catching
interface mismatches.

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_and_save(tmp_dir, reviewer, issues, pr_id="123"):
    """Create a ReviewOutputBuilder, add issues from spec dicts, and save.

    Each issue dict should have keys matching add_issue params:
    severity, title, file, description, recommendation, line (required),
    and optionally category, confidence.
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
             "line": 10, "description": "Direct input in query", "recommendation": "Use prepare()"},
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
             "line": 1, "description": "desc", "recommendation": "rec"},
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
             "line": 1, "description": "desc", "recommendation": "rec",
             "vulnerability_type": "sqli", "cwe_id": "CWE-89"},
        ])

        result = reconcile(tmp_dir)
        assert result["total_findings"] == 1
        assert result["deduplicated_findings"] == 1


    def test_reconcile_output_has_issues_key(self, tmp_dir):
        """Reconcile output contains an 'issues' key for downstream consumers."""
        _build_and_save(tmp_dir, "security", [
            {"severity": "medium", "title": "Issue", "file": "f.py",
             "line": 1, "description": "desc", "recommendation": "rec"},
        ])

        result = reconcile(tmp_dir)
        assert "issues" in result, "Reconcile output missing 'issues' key"
        assert "clusters" in result, "Reconcile output missing 'clusters' key"
        assert len(result["issues"]) == len(result["clusters"])

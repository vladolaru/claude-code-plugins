"""
Tests for ReviewOutputBuilder — direct unit tests on the producer API.

Validates the structured review output builder that all reviewer agents use
to emit findings. Tests cover initialization, issue addition with validation,
recommendations, verdicts, serialization (dict, JSON, markdown), and file output.

Zero external dependencies beyond stdlib + pytest.
"""

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import ReviewOutputBuilder from scripts/
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review_output_simple import ReviewOutputBuilder


# =============================================================================
# TestBuilderInit
# =============================================================================


class TestBuilderInit:
    """Constructor stores pr_id/reviewer and sets sensible defaults."""

    def test_stores_pr_id_and_reviewer(self):
        b = ReviewOutputBuilder(pr_id="456", reviewer="security")
        assert b.pr_id == "456"
        assert b.reviewer == "security"

    def test_defaults(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        assert b.issues == []
        assert b.positive_observations == []
        assert b.tool_results_used == []
        assert b.files_reviewed == 0
        assert b.overall_confidence == 0.95
        assert b.recommendations == {"immediate": [], "important": [], "suggestions": []}

    def test_timestamp_is_iso(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        # Should parse without error — ISO 8601 format
        parsed = datetime.fromisoformat(b.timestamp)
        assert isinstance(parsed, datetime)


# =============================================================================
# TestAddIssue
# =============================================================================


class TestAddIssue:
    """add_issue validates inputs, stores all fields, and returns an ID."""

    def test_returns_8_char_id(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        issue_id = b.add_issue("high", "Title", "f.py", "desc", "rec")
        assert isinstance(issue_id, str)
        assert len(issue_id) == 8

    def test_stores_all_fields(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue(
            severity="high",
            title="SQL Injection",
            file="src/db.php",
            description="Direct input in query",
            recommendation="Use prepared statements",
            category="sql-injection",
            line=42,
            confidence=0.9,
        )
        issue = b.issues[0]
        assert issue["severity"] == "high"
        assert issue["title"] == "SQL Injection"
        assert issue["file"] == "src/db.php"
        assert issue["description"] == "Direct input in query"
        assert issue["recommendation"] == "Use prepared statements"
        assert issue["category"] == "sql-injection"
        assert issue["line"] == 42
        assert issue["confidence"] == 0.9

    def test_severity_case_insensitive(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("HIGH", "Title", "f.py", "desc", "rec")
        assert b.issues[0]["severity"] == "high"

    def test_invalid_severity_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="Invalid severity"):
            b.add_issue("urgent", "Title", "f.py", "desc", "rec")

    def test_confidence_boundaries_valid(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("high", "A", "f.py", "d", "r", confidence=0.0)
        b.add_issue("high", "B", "f.py", "d", "r", confidence=1.0)
        assert b.issues[0]["confidence"] == 0.0
        assert b.issues[1]["confidence"] == 1.0

    def test_confidence_boundaries_invalid(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="Confidence"):
            b.add_issue("high", "A", "f.py", "d", "r", confidence=-0.1)
        with pytest.raises(ValueError, match="Confidence"):
            b.add_issue("high", "B", "f.py", "d", "r", confidence=1.1)

    def test_extra_kwargs_preserved(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue(
            "high", "Title", "f.py", "desc", "rec",
            vulnerability_type="xss",
            cwe_id="CWE-79",
        )
        issue = b.issues[0]
        assert issue["vulnerability_type"] == "xss"
        assert issue["cwe_id"] == "CWE-79"

    def test_defaults_category_and_line(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("medium", "Title", "f.py", "desc", "rec")
        issue = b.issues[0]
        assert issue["category"] == "general"
        assert issue["line"] is None


# =============================================================================
# TestAddRecommendation
# =============================================================================


class TestAddRecommendation:
    """add_recommendation stores by priority bucket."""

    def test_valid_priorities(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_recommendation("immediate", "Fix now")
        b.add_recommendation("important", "Fix soon")
        b.add_recommendation("suggestions", "Nice to have")
        assert b.recommendations["immediate"] == ["Fix now"]
        assert b.recommendations["important"] == ["Fix soon"]
        assert b.recommendations["suggestions"] == ["Nice to have"]

    def test_invalid_priority_silently_ignored(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_recommendation("urgent", "Fix now")
        # No error, and no bucket created
        assert "urgent" not in b.recommendations
        assert all(len(v) == 0 for v in b.recommendations.values())

    def test_multiple_per_bucket(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_recommendation("immediate", "First")
        b.add_recommendation("immediate", "Second")
        assert b.recommendations["immediate"] == ["First", "Second"]


# =============================================================================
# TestAddPositive
# =============================================================================


class TestAddPositive:
    """add_positive stores observations in insertion order."""

    def test_stores_in_order(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_positive("Good test coverage")
        b.add_positive("Clean error handling")
        assert b.positive_observations == ["Good test coverage", "Clean error handling"]


# =============================================================================
# TestSetFilesReviewed
# =============================================================================


class TestSetFilesReviewed:
    """set_files_reviewed stores the count."""

    def test_stores_count(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.set_files_reviewed(42)
        assert b.files_reviewed == 42


# =============================================================================
# TestSetConfidence
# =============================================================================


class TestSetConfidence:
    """set_confidence validates range."""

    def test_valid_range(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.set_confidence(0.5)
        assert b.overall_confidence == 0.5

    def test_invalid_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        with pytest.raises(ValueError, match="Confidence"):
            b.set_confidence(1.5)
        with pytest.raises(ValueError, match="Confidence"):
            b.set_confidence(-0.1)


# =============================================================================
# TestAddToolResult
# =============================================================================


class TestAddToolResult:
    """add_tool_result stores tool names and deduplicates."""

    def test_stores_tool(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_tool_result("grep")
        assert b.tool_results_used == ["grep"]

    def test_deduplicates(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_tool_result("grep")
        b.add_tool_result("grep")
        b.add_tool_result("ast-grep")
        assert b.tool_results_used == ["grep", "ast-grep"]


# =============================================================================
# TestCalculateVerdict
# =============================================================================


class TestCalculateVerdict:
    """_calculate_verdict auto-selects verdict from issue severity counts."""

    def _builder_with_issues(self, severities):
        """Create a builder with issues at given severity levels."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        for i, sev in enumerate(severities):
            b.add_issue(sev, f"Issue {i}", f"f{i}.py", "desc", "rec")
        return b

    def test_no_issues_approve(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        assert b._calculate_verdict() == "approve"

    def test_one_critical_blocks(self):
        b = self._builder_with_issues(["critical"])
        assert b._calculate_verdict() == "block"

    def test_two_high_request_changes(self):
        b = self._builder_with_issues(["high", "high"])
        verdict = b._calculate_verdict()
        assert verdict == "request_changes"
        assert verdict != "block"

    def test_three_high_blocks(self):
        b = self._builder_with_issues(["high", "high", "high"])
        assert b._calculate_verdict() == "block"

    def test_one_high_request_changes(self):
        b = self._builder_with_issues(["high"])
        assert b._calculate_verdict() == "request_changes"

    def test_four_medium_comment(self):
        b = self._builder_with_issues(["medium"] * 4)
        verdict = b._calculate_verdict()
        assert verdict == "comment"
        assert verdict != "request_changes"

    def test_five_medium_request_changes(self):
        b = self._builder_with_issues(["medium"] * 5)
        assert b._calculate_verdict() == "request_changes"

    def test_one_medium_comment(self):
        b = self._builder_with_issues(["medium"])
        assert b._calculate_verdict() == "comment"

    def test_low_and_info_only_approve(self):
        b = self._builder_with_issues(["low", "info", "low", "info"])
        assert b._calculate_verdict() == "approve"


# =============================================================================
# TestToDict
# =============================================================================


class TestToDict:
    """to_dict produces correct structure."""

    def test_all_top_level_keys(self):
        b = ReviewOutputBuilder(pr_id="99", reviewer="arch")
        b.add_issue("medium", "Title", "f.py", "desc", "rec")
        d = b.to_dict()
        expected_keys = {
            "pr_id", "reviewer", "timestamp", "version", "verdict",
            "summary", "issues", "recommendations", "positive_observations", "meta",
        }
        assert expected_keys == set(d.keys())

    def test_severity_counts_correct(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_issue("critical", "A", "a.py", "d", "r")
        b.add_issue("high", "B", "b.py", "d", "r")
        b.add_issue("high", "C", "c.py", "d", "r")
        b.add_issue("medium", "D", "d.py", "d", "r")
        d = b.to_dict()
        counts = d["summary"]["by_severity"]
        assert counts["critical"] == 1
        assert counts["high"] == 2
        assert counts["medium"] == 1
        assert counts["low"] == 0
        assert counts["info"] == 0

    def test_meta_structure(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.set_files_reviewed(10)
        b.set_confidence(0.8)
        b.add_tool_result("grep")
        d = b.to_dict()
        meta = d["meta"]
        assert meta["files_reviewed"] == 10
        assert meta["confidence_score"] == 0.8
        assert meta["tool_results_used"] == ["grep"]
        assert "review_duration_ms" in meta

    def test_none_for_empty_recommendations_and_positives(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        d = b.to_dict()
        assert d["recommendations"] is None
        assert d["positive_observations"] is None


# =============================================================================
# TestToJson
# =============================================================================


class TestToJson:
    """to_json roundtrips through json.loads to match to_dict."""

    def test_roundtrip(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("high", "XSS", "f.php", "desc", "rec")
        b.add_positive("Good patterns")
        parsed = json.loads(b.to_json())
        assert parsed == b.to_dict()


# =============================================================================
# TestToMarkdown
# =============================================================================


class TestToMarkdown:
    """to_markdown produces human-readable output."""

    def test_header_format(self):
        b = ReviewOutputBuilder(pr_id="42", reviewer="security")
        md = b.to_markdown()
        assert "# Security Review - PR #42" in md

    def test_issues_grouped_by_severity(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_issue("low", "Low Issue", "a.py", "desc", "rec")
        b.add_issue("critical", "Critical Issue", "b.py", "desc", "rec")
        b.add_issue("high", "High Issue", "c.py", "desc", "rec")
        md = b.to_markdown()
        # Critical section should appear before High, High before Low
        crit_pos = md.index("## Critical Issues")
        high_pos = md.index("## High Issues")
        low_pos = md.index("## Low Issues")
        assert crit_pos < high_pos < low_pos

    def test_positive_observations_section(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_positive("Well-structured code")
        md = b.to_markdown()
        assert "## Positive Observations" in md
        assert "Well-structured code" in md


# =============================================================================
# TestSave
# =============================================================================


class TestSave:
    """save writes both JSON and markdown files."""

    def test_creates_both_files(self):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.add_issue("high", "Title", "f.py", "desc", "rec")
            b.save(d)
            assert os.path.isfile(os.path.join(d, "security-review.json"))
            assert os.path.isfile(os.path.join(d, "security-review.md"))

    def test_json_content_matches_to_dict(self):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.add_issue("high", "Title", "f.py", "desc", "rec")
            b.save(d)
            with open(os.path.join(d, "security-review.json")) as f:
                saved = json.load(f)
            assert saved == b.to_dict()

    def test_return_value_has_correct_paths(self):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="arch")
            result = b.save(d)
            assert result["json"] == os.path.join(d, "arch-review.json")
            assert result["markdown"] == os.path.join(d, "arch-review.md")

"""
Tests for the grading functions in graders.py.

Validates graders work correctly on synthetic inputs.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent  # grading/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
_scripts_dir = str(PLUGIN_ROOT / "scripts")

# Add tests/ and scripts/ to path before importing local modules
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, _scripts_dir)

import pytest

from helpers.graders import (
    GradeResult,
    grade_review_json,
    grade_review_markdown,
    grade_signal_format,
    grade_no_domain_files,
    grade_error_exit,
    grade_output_pair,
    grade_review_baseline,
)

from review.agent.output import ReviewOutputBuilder


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _make_valid_json(tmp_dir: str, reviewer: str = "security") -> str:
    """Create a valid review JSON file using ReviewOutputBuilder."""
    builder = ReviewOutputBuilder(pr_id="123", reviewer=reviewer)
    builder.add_issue(
        severity="high",
        title="SQL Injection",
        file="src/User.php",
        description="Direct input in query",
        recommendation="Use prepared statements",
        line=42,
    )
    builder.set_files_reviewed(3)

    path = os.path.join(tmp_dir, f"{reviewer}-review.json")
    with open(path, "w") as f:
        f.write(builder.to_json())
    return path


def _make_valid_markdown(tmp_dir: str, reviewer: str = "security") -> str:
    """Create a valid review markdown file using ReviewOutputBuilder."""
    builder = ReviewOutputBuilder(pr_id="123", reviewer=reviewer)
    builder.add_issue(
        severity="high",
        title="SQL Injection",
        file="src/User.php",
        description="Direct input in query",
        recommendation="Use prepared statements",
        line=42,
    )

    path = os.path.join(tmp_dir, f"{reviewer}-review.md")
    with open(path, "w") as f:
        f.write(builder.to_markdown())
    return path


class TestGradeReviewJson:
    """Tests for grade_review_json."""

    def test_valid_json_passes(self, tmp_dir):
        path = _make_valid_json(tmp_dir)
        result = grade_review_json(path)
        assert result.passed, f"Failures: {result.failures}"
        assert result.score == 1.0

    def test_missing_file_fails(self):
        result = grade_review_json("/nonexistent/path.json")
        assert not result.passed
        assert any("does not exist" in f for f in result.failures)

    def test_invalid_json_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            f.write("not json at all {{{")
        result = grade_review_json(path)
        assert not result.passed
        assert any("Invalid JSON" in f for f in result.failures)

    def test_missing_required_field_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "missing.json")
        data = {"pr_id": "1", "reviewer": "test"}  # missing verdict, summary, issues, meta
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_json(path)
        assert not result.passed
        assert any("verdict" in f for f in result.failures)

    def test_invalid_verdict_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad-verdict.json")
        data = {
            "pr_id": "1",
            "reviewer": "test",
            "verdict": "INVALID_VERDICT",
            "summary": {"total_issues": 0, "by_severity": {}},
            "issues": [],
            "meta": {},
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_json(path)
        assert not result.passed
        assert any("Invalid verdict" in f for f in result.failures)

    def test_empty_file_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "empty.json")
        with open(path, "w") as f:
            f.write("")
        result = grade_review_json(path)
        assert not result.passed

    def test_issue_with_invalid_severity_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad-sev.json")
        data = {
            "pr_id": "1",
            "reviewer": "test",
            "verdict": "comment",
            "summary": {"total_issues": 1, "by_severity": {"unknown": 1}},
            "issues": [
                {
                    "id": "abc",
                    "severity": "unknown",
                    "title": "Test",
                    "file": "a.py",
                    "description": "desc",
                    "recommendation": "fix",
                }
            ],
            "meta": {},
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_json(path)
        assert not result.passed
        assert any("invalid severity" in f.lower() for f in result.failures)


class TestGradeReviewMarkdown:
    """Tests for grade_review_markdown."""

    def test_valid_markdown_passes(self, tmp_dir):
        path = _make_valid_markdown(tmp_dir)
        result = grade_review_markdown(path)
        assert result.passed, f"Failures: {result.failures}"

    def test_missing_file_fails(self):
        result = grade_review_markdown("/nonexistent/review.md")
        assert not result.passed

    def test_missing_header_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "no-header.md")
        with open(path, "w") as f:
            f.write("Just some text without a proper header\n")
        result = grade_review_markdown(path)
        assert not result.passed


class TestGradeSignalFormat:
    """Tests for grade_signal_format."""

    def test_valid_signal(self):
        signal = (
            "STATUS: FINISHED\n"
            "OUTPUT_FILES:\n"
            "  - /tmp/security-review.json\n"
            "COUNTS:\n"
            "  critical: 0\n"
            "VERDICT: APPROVE\n"
            "SUMMARY: No issues found\n"
        )
        result = grade_signal_format(signal)
        assert result.passed

    def test_missing_status(self):
        result = grade_signal_format("OUTPUT_FILES:\nVERDICT: APPROVE\n")
        assert not result.passed


class TestGradeNoDomainFiles:
    """Tests for grade_no_domain_files."""

    def test_approve_with_no_findings(self):
        text = "VERDICT: APPROVE\nNo security files to review."
        result = grade_no_domain_files(text)
        assert result.passed

    def test_non_approve_fails(self):
        text = "VERDICT: REQUEST_CHANGES\nCRITICAL: found issue"
        result = grade_no_domain_files(text)
        assert not result.passed


class TestGradeOutputPair:
    """Tests for grade_output_pair."""

    def test_valid_pair_passes(self, tmp_dir):
        _make_valid_json(tmp_dir, "security")
        _make_valid_markdown(tmp_dir, "security")
        result = grade_output_pair(tmp_dir, "security")
        assert result.passed, f"Failures: {result.failures}"

    def test_missing_json_fails(self, tmp_dir):
        _make_valid_markdown(tmp_dir, "security")
        result = grade_output_pair(tmp_dir, "security")
        assert not result.passed


def _make_valid_baseline(tmp_dir: str) -> str:
    """Create a valid .branch-review-baseline.json file."""
    path = os.path.join(tmp_dir, ".branch-review-baseline.json")
    data = {
        "last_reviewed_sha": "abc123def456789012345678901234567890abcd",
        "last_reviewed_at": "2026-02-09T12:34:56",
        "review_type": "full",
        "review_count": 1,
        "base_ref": "main",
        "git_range_used": "main..HEAD",
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return path


class TestGradeReviewBaseline:
    """Tests for grade_review_baseline."""

    def test_valid_baseline_passes(self, tmp_dir):
        path = _make_valid_baseline(tmp_dir)
        result = grade_review_baseline(path)
        assert result.passed, f"Failures: {result.failures}"
        assert result.score == 1.0

    def test_missing_file_fails(self):
        result = grade_review_baseline("/nonexistent/.branch-review-baseline.json")
        assert not result.passed
        assert any("does not exist" in f for f in result.failures)

    def test_invalid_json_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        with open(path, "w") as f:
            f.write("not json {{{")
        result = grade_review_baseline(path)
        assert not result.passed
        assert any("Invalid JSON" in f for f in result.failures)

    def test_missing_required_field_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        data = {"last_reviewed_sha": "abc1234", "review_count": 1}
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_baseline(path)
        assert not result.passed
        assert any("base_ref" in f for f in result.failures)

    def test_invalid_sha_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        data = {
            "last_reviewed_sha": "not-a-sha!",
            "last_reviewed_at": "2026-02-09T12:34:56",
            "review_type": "full",
            "review_count": 1,
            "base_ref": "main",
            "git_range_used": "main..HEAD",
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_baseline(path)
        assert not result.passed
        assert any("Invalid SHA" in f for f in result.failures)

    def test_short_sha_passes(self, tmp_dir):
        """Short SHAs (7+ chars) are valid."""
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        data = {
            "last_reviewed_sha": "abc1234",
            "last_reviewed_at": "2026-02-09T12:34:56",
            "review_type": "incremental",
            "review_count": 1,
            "base_ref": "main",
            "git_range_used": "main..HEAD",
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_baseline(path)
        assert result.passed, f"Failures: {result.failures}"

    def test_zero_review_count_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        data = {
            "last_reviewed_sha": "abc1234",
            "last_reviewed_at": "2026-02-09T12:34:56",
            "review_type": "full",
            "review_count": 0,
            "base_ref": "main",
            "git_range_used": "main..HEAD",
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_baseline(path)
        assert not result.passed
        assert any("review_count" in f for f in result.failures)

    def test_missing_range_separator_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        data = {
            "last_reviewed_sha": "abc1234",
            "last_reviewed_at": "2026-02-09T12:34:56",
            "review_type": "full",
            "review_count": 1,
            "base_ref": "main",
            "git_range_used": "HEAD",
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_baseline(path)
        assert not result.passed
        assert any("git_range_used" in f for f in result.failures)


def test_issue_accepts_behavior_evidence_and_source_cited():
    b = ReviewOutputBuilder(reviewer="ecosystem-integration-reviewer", pr_id="0")
    b.add_issue(
        severity="medium",
        category="behavior-assumption",
        title="State assumption mismatch",
        description="Callback reads saved status before save fires.",
        file="src/hooks.php",
        line=42,
        recommendation="Switch to woocommerce_after_order_object_save.",
        behavior_evidence="cited",
        source_cited="woocommerce/.../class-wc-order.php:200",
    )
    output = b.to_dict()
    issue = output["issues"][0]
    assert issue["behavior_evidence"] == "cited"
    assert issue["source_cited"] == "woocommerce/.../class-wc-order.php:200"


def test_issue_behavior_evidence_optional():
    b = ReviewOutputBuilder(reviewer="security-reviewer", pr_id="0")
    b.add_issue(
        severity="low", category="xss", title="X", description="y",
        file="f.php", line=1, recommendation="z",
    )
    output = b.to_dict()
    # Fields omitted when not provided
    assert "behavior_evidence" not in output["issues"][0]
    assert "source_cited" not in output["issues"][0]


def test_issue_behavior_evidence_invalid_rejected():
    import pytest

    b = ReviewOutputBuilder(reviewer="ecosystem-integration-reviewer", pr_id="0")
    with pytest.raises(ValueError, match="behavior_evidence"):
        b.add_issue(
            severity="low", category="other", title="T", description="d",
            file="f.php", line=1, recommendation="r",
            behavior_evidence="MAYBE",
        )


def test_issue_behavior_evidence_rejects_speculative():
    import pytest

    b = ReviewOutputBuilder(reviewer="ecosystem-integration-reviewer", pr_id="0")
    with pytest.raises(ValueError, match="behavior_evidence"):
        b.add_issue(
            severity="low", category="behavior-assumption", title="T", description="d",
            file="f.php", line=1, recommendation="r",
            behavior_evidence="speculative",
        )

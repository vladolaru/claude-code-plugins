"""
Tests for quality metrics extraction from reviewer session JSONL logs.

Validates the --quality-metrics mode of analyze-reviewer-sessions.py:
- Parsing agent Write output (JSON) to extract finding counts
- Parsing ingest subagent log to extract categorization outcomes
- Handling missing/partial data gracefully
- Overlap detection across agents
- Severity disagreements across agents
- Empty session data
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# Import the module under test (hyphenated filename requires importlib)
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "analyze-reviewer-sessions.py"

_spec = importlib.util.spec_from_file_location("analyze_reviewer_sessions", str(SCRIPT_PATH))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

extract_agent_findings = _mod.extract_agent_findings
extract_ingest_outcomes = _mod.extract_ingest_outcomes
compute_survival_rate = _mod.compute_survival_rate
detect_overlaps = _mod.detect_overlaps


# ---------------------------------------------------------------------------
# Helpers: build mock review JSON output (what an agent writes via Write tool)
# ---------------------------------------------------------------------------

def _make_review_json(
    reviewer="security",
    issues=None,
    verdict="comment",
):
    """Build a review JSON dict matching ReviewOutputBuilder.to_dict() schema."""
    if issues is None:
        issues = []

    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for issue in issues:
        sev = issue.get("severity", "info")
        if sev in severity_counts:
            severity_counts[sev] += 1

    return {
        "pr_id": "42",
        "reviewer": reviewer,
        "verdict": verdict,
        "summary": {
            "total_issues": len(issues),
            "by_severity": severity_counts,
        },
        "issues": issues,
        "meta": {
            "files_reviewed": 5,
            "review_duration_ms": 12000,
            "confidence_score": 0.9,
        },
    }


def _make_issue(
    severity="high",
    title="Test Issue",
    file="src/Foo.php",
    line=10,
    issue_id="abc1",
    description="desc",
    recommendation="fix it",
):
    """Build a single issue dict."""
    return {
        "id": issue_id,
        "severity": severity,
        "title": title,
        "file": file,
        "line": line,
        "description": description,
        "recommendation": recommendation,
        "confidence": 0.9,
    }


# ---------------------------------------------------------------------------
# Tests: extract_agent_findings
# ---------------------------------------------------------------------------

class TestExtractAgentFindings:
    """extract_agent_findings(write_output) → dict with total, by_severity, issues list."""

    def test_basic_extraction(self):
        """Extracts finding counts from a well-formed review JSON."""
        review = _make_review_json(
            reviewer="security",
            issues=[
                _make_issue(severity="critical", title="SQL Injection", file="a.php", line=10, issue_id="s1"),
                _make_issue(severity="high", title="XSS", file="b.php", line=20, issue_id="s2"),
                _make_issue(severity="medium", title="Missing escape", file="c.php", line=30, issue_id="s3"),
            ],
        )
        result = extract_agent_findings(review)
        assert result["total_findings"] == 3
        assert result["findings_by_severity"]["critical"] == 1
        assert result["findings_by_severity"]["high"] == 1
        assert result["findings_by_severity"]["medium"] == 1
        assert result["findings_by_severity"].get("low", 0) == 0

    def test_empty_issues(self):
        """Agent produced zero findings — clean approve."""
        review = _make_review_json(reviewer="perf", issues=[], verdict="approve")
        result = extract_agent_findings(review)
        assert result["total_findings"] == 0
        assert all(v == 0 for v in result["findings_by_severity"].values())

    def test_issues_list_preserved(self):
        """The parsed issues list is returned for downstream overlap detection."""
        issues = [
            _make_issue(severity="high", file="x.php", line=42, issue_id="h1"),
            _make_issue(severity="low", file="y.php", line=7, issue_id="h2"),
        ]
        review = _make_review_json(issues=issues)
        result = extract_agent_findings(review)
        assert len(result["issues"]) == 2
        assert result["issues"][0]["file"] == "x.php"
        assert result["issues"][0]["line"] == 42

    def test_missing_issues_key(self):
        """Gracefully handles review JSON with no 'issues' key."""
        review = {"pr_id": "1", "reviewer": "security", "verdict": "approve"}
        result = extract_agent_findings(review)
        assert result["total_findings"] == 0

    def test_missing_summary_key(self):
        """Gracefully handles review JSON with no 'summary' key — counts from issues."""
        review = {
            "pr_id": "1",
            "reviewer": "security",
            "verdict": "comment",
            "issues": [_make_issue(severity="high")],
        }
        result = extract_agent_findings(review)
        assert result["total_findings"] == 1
        assert result["findings_by_severity"]["high"] == 1

    def test_non_dict_input(self):
        """Non-dict input returns empty result."""
        result = extract_agent_findings("not a dict")
        assert result["total_findings"] == 0

    def test_none_input(self):
        """None input returns empty result."""
        result = extract_agent_findings(None)
        assert result["total_findings"] == 0


# ---------------------------------------------------------------------------
# Tests: extract_ingest_outcomes
# ---------------------------------------------------------------------------

class TestExtractIngestOutcomes:
    """extract_ingest_outcomes(ingest_texts) → dict with category counts."""

    def test_all_categories(self):
        """Recognizes all five ingest categories in text output."""
        texts = [
            "F1 [IN_SCOPE] VERIFIED:\n  Status: VERIFIED\n  Rationale: confirmed issue",
            "F2 [OUT_OF_SCOPE: file not in diff]: Missing escape",
            "F3 [IN_SCOPE] FAILED:\n  Status: FAILED\n  Rationale: code already handles this",
            "F4 [IN_SCOPE] VERIFIED but STYLE/PREFERENCE:\n  Naming convention preference",
            "Final category mapping:\n"
            "| F1 | CONFIRMED |\n"
            "| F2 | OUT OF SCOPE |\n"
            "| F3 | FALSE POSITIVE |\n"
            "| F4 | STYLE |\n"
            "| F5 | LIKELY VALID |\n",
        ]
        result = extract_ingest_outcomes(texts)
        assert result["confirmed"] >= 1
        assert result["out_of_scope"] >= 1
        assert result["false_positive"] >= 1
        assert result["style"] >= 1
        assert result["likely_valid"] >= 1

    def test_empty_texts(self):
        """Empty text list returns zeroes."""
        result = extract_ingest_outcomes([])
        assert result["confirmed"] == 0
        assert result["out_of_scope"] == 0
        assert result["false_positive"] == 0
        assert result["style"] == 0
        assert result["likely_valid"] == 0

    def test_no_categories_found(self):
        """Text without any recognizable categories returns zeroes."""
        texts = ["Just some random text without any finding categories."]
        result = extract_ingest_outcomes(texts)
        assert all(v == 0 for v in result.values())

    def test_multiple_confirmed(self):
        """Multiple CONFIRMED findings are counted."""
        texts = [
            "| F1 | CONFIRMED |\n| F2 | CONFIRMED |\n| F3 | CONFIRMED |"
        ]
        result = extract_ingest_outcomes(texts)
        assert result["confirmed"] == 3

    def test_case_insensitive_categories(self):
        """Categories are matched case-insensitively."""
        texts = ["| F1 | confirmed |\n| F2 | Out of Scope |"]
        result = extract_ingest_outcomes(texts)
        assert result["confirmed"] >= 1
        assert result["out_of_scope"] >= 1


# ---------------------------------------------------------------------------
# Tests: compute_survival_rate
# ---------------------------------------------------------------------------

class TestComputeSurvivalRate:
    """compute_survival_rate(agent_findings, ingest_outcomes) → float 0.0-1.0."""

    def test_all_survived(self):
        """All findings confirmed → 1.0 survival rate."""
        agent_findings = {"total_findings": 3, "findings_by_severity": {}, "issues": []}
        ingest_outcomes = {
            "confirmed": 3, "likely_valid": 0,
            "false_positive": 0, "out_of_scope": 0, "style": 0,
        }
        rate = compute_survival_rate(agent_findings, ingest_outcomes)
        assert rate == pytest.approx(1.0)

    def test_none_survived(self):
        """All findings filtered out → 0.0 survival rate."""
        agent_findings = {"total_findings": 4, "findings_by_severity": {}, "issues": []}
        ingest_outcomes = {
            "confirmed": 0, "likely_valid": 0,
            "false_positive": 2, "out_of_scope": 1, "style": 1,
        }
        rate = compute_survival_rate(agent_findings, ingest_outcomes)
        assert rate == pytest.approx(0.0)

    def test_partial_survival(self):
        """Mix of confirmed/likely_valid and filtered → fractional rate."""
        agent_findings = {"total_findings": 5, "findings_by_severity": {}, "issues": []}
        ingest_outcomes = {
            "confirmed": 2, "likely_valid": 1,
            "false_positive": 1, "out_of_scope": 1, "style": 0,
        }
        # survived = confirmed + likely_valid = 3, total = 5
        rate = compute_survival_rate(agent_findings, ingest_outcomes)
        assert rate == pytest.approx(0.6)

    def test_zero_findings(self):
        """Zero total findings → 0.0 (avoid division by zero)."""
        agent_findings = {"total_findings": 0, "findings_by_severity": {}, "issues": []}
        ingest_outcomes = {
            "confirmed": 0, "likely_valid": 0,
            "false_positive": 0, "out_of_scope": 0, "style": 0,
        }
        rate = compute_survival_rate(agent_findings, ingest_outcomes)
        assert rate == pytest.approx(0.0)

    def test_missing_total_findings(self):
        """Missing total_findings key → 0.0."""
        agent_findings = {"findings_by_severity": {}, "issues": []}
        ingest_outcomes = {"confirmed": 1, "likely_valid": 0, "false_positive": 0, "out_of_scope": 0, "style": 0}
        rate = compute_survival_rate(agent_findings, ingest_outcomes)
        assert rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: detect_overlaps
# ---------------------------------------------------------------------------

class TestDetectOverlaps:
    """detect_overlaps(all_findings) → dict with overlap_clusters, severity_disagreements."""

    def test_basic_overlap(self):
        """Two agents flag the same file+line → one overlap cluster."""
        findings = [
            {"agent": "security", "file": "src/Foo.php", "line": 42, "severity": "critical", "title": "SQL Injection"},
            {"agent": "code", "file": "src/Foo.php", "line": 42, "severity": "critical", "title": "Unescaped input"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 1
        assert result["severity_disagreements"] == 0

    def test_no_overlaps(self):
        """Different files → no overlap."""
        findings = [
            {"agent": "security", "file": "src/A.php", "line": 10, "severity": "high", "title": "Issue A"},
            {"agent": "code", "file": "src/B.php", "line": 20, "severity": "high", "title": "Issue B"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 0
        assert result["severity_disagreements"] == 0

    def test_severity_disagreement(self):
        """Same file+line but different severity → severity disagreement."""
        findings = [
            {"agent": "security", "file": "src/Foo.php", "line": 42, "severity": "critical", "title": "SQL Injection"},
            {"agent": "code", "file": "src/Foo.php", "line": 42, "severity": "medium", "title": "Input handling"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 1
        assert result["severity_disagreements"] == 1

    def test_multiple_overlaps(self):
        """Three findings at two locations → two overlap clusters."""
        findings = [
            {"agent": "security", "file": "a.php", "line": 10, "severity": "high", "title": "A1"},
            {"agent": "code", "file": "a.php", "line": 10, "severity": "high", "title": "A2"},
            {"agent": "security", "file": "b.php", "line": 20, "severity": "medium", "title": "B1"},
            {"agent": "perf", "file": "b.php", "line": 20, "severity": "medium", "title": "B2"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 2

    def test_three_agents_same_location(self):
        """Three agents at the same file+line → still one cluster."""
        findings = [
            {"agent": "security", "file": "x.php", "line": 5, "severity": "high", "title": "X1"},
            {"agent": "code", "file": "x.php", "line": 5, "severity": "high", "title": "X2"},
            {"agent": "perf", "file": "x.php", "line": 5, "severity": "medium", "title": "X3"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 1
        # perf has different severity than security/code
        assert result["severity_disagreements"] == 1

    def test_empty_findings(self):
        """Empty findings list → no overlaps."""
        result = detect_overlaps([])
        assert result["overlap_clusters"] == 0
        assert result["severity_disagreements"] == 0

    def test_single_finding(self):
        """Single finding → no possible overlap."""
        findings = [
            {"agent": "security", "file": "a.php", "line": 1, "severity": "high", "title": "Solo"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 0
        assert result["severity_disagreements"] == 0

    def test_same_file_different_lines(self):
        """Same file but different lines → no overlap."""
        findings = [
            {"agent": "security", "file": "a.php", "line": 10, "severity": "high", "title": "Line10"},
            {"agent": "code", "file": "a.php", "line": 20, "severity": "high", "title": "Line20"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 0

    def test_none_line_ignored(self):
        """Findings with None line are excluded from overlap detection."""
        findings = [
            {"agent": "security", "file": "a.php", "line": None, "severity": "high", "title": "NoLine1"},
            {"agent": "code", "file": "a.php", "line": None, "severity": "high", "title": "NoLine2"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 0

    def test_overlap_details_returned(self):
        """Overlap details include file, line, and involved agents."""
        findings = [
            {"agent": "security", "file": "src/Foo.php", "line": 42, "severity": "critical", "title": "A"},
            {"agent": "code", "file": "src/Foo.php", "line": 42, "severity": "high", "title": "B"},
        ]
        result = detect_overlaps(findings)
        assert len(result["clusters"]) == 1
        cluster = result["clusters"][0]
        assert cluster["file"] == "src/Foo.php"
        assert cluster["line"] == 42
        assert set(cluster["agents"]) == {"security", "code"}

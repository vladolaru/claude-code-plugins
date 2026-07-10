"""Tests for build_critic_context() — deterministic, no model calls.

Tests the critic context builder that produces a curated Markdown document
from a review report and structured findings for the decision critic.
"""

import importlib.util
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "review" / "reconciliation_context.py"


def _load_module():
    """Load the reconciliation_context module via importlib."""
    spec = importlib.util.spec_from_file_location(
        "reconciliation_context", str(SCRIPT_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    """Module-scoped import of reconciliation_context."""
    return _load_module()


# ---------------------------------------------------------------------------
# Test fixture helpers
# ---------------------------------------------------------------------------

def _make_issue(
    id="abc12345",
    severity="medium",
    title="Test issue",
    file="src/app.py",
    line=42,
    description="Some issue found",
    recommendation="Fix it",
    category="general",
    confidence=0.9,
    severity_floor=None,
):
    """Create a single issue dict matching ReviewOutputBuilder format."""
    issue = {
        "id": id,
        "severity": severity,
        "title": title,
        "file": file,
        "line": line,
        "description": description,
        "recommendation": recommendation,
        "category": category,
        "confidence": confidence,
    }
    if severity_floor is not None:
        issue["severity_floor"] = severity_floor
    return issue


def _make_findings(
    pr_id="42",
    verdict="request_changes",
    issues=None,
    recommendations=None,
    meta=None,
):
    """Create a findings dict matching ReviewOutputBuilder.to_dict() output."""
    if issues is None:
        issues = [
            _make_issue(id="aaa11111", severity="high", title="XSS vulnerability",
                        file="src/User.php", line=42,
                        description="Direct input in output",
                        recommendation="Use esc_html()",
                        category="security", confidence=0.95),
            _make_issue(id="bbb22222", severity="high", title="SQL injection risk",
                        file="src/Query.php", line=88,
                        description="Unescaped query parameter",
                        recommendation="Use $wpdb->prepare()",
                        category="security", confidence=0.90),
            _make_issue(id="ccc33333", severity="medium", title="Missing nonce check",
                        file="src/Admin.php", line=15,
                        description="Form handler lacks CSRF protection",
                        recommendation="Add wp_verify_nonce()",
                        category="security", confidence=0.85),
        ]

    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for issue in issues:
        sev = issue.get("severity", "medium")
        if sev in severity_counts:
            severity_counts[sev] += 1

    if recommendations is None:
        recommendations = {
            "immediate": ["Fix XSS in User.php"],
            "important": ["Add nonce checks to all form handlers"],
            "suggestions": [],
        }

    if meta is None:
        meta = {
            "reconciliation": {
                "input_findings_count": 15,
                "agents_contributing": 5,
                "concerns_after_grouping": 7,
                "false_positives_dropped": 3,
                "out_of_scope_dropped": 5,
                "verified_concerns": 7,
                "merge_ratio": 0.53,
                "reviewing_agents": ["security-review", "performance-review"],
                "missing_agents": [],
                "not_applicable_agents": [
                    {"name": "e2e-tests-reviewer", "skip_reason": "no test files"},
                ],
            }
        }

    return {
        "pr_id": pr_id,
        "reviewer": "reconciliator",
        "verdict": verdict,
        "summary": {
            "total_issues": len(issues),
            "by_severity": severity_counts,
        },
        "issues": issues,
        "recommendations": recommendations,
        "meta": meta,
    }


SAMPLE_REPORT = """\
# Review Report

This PR introduces several security concerns that need to be addressed
before merging.

## Critical Findings

The User.php file has a direct XSS vulnerability on line 42.
The Query.php file has a SQL injection risk on line 88.

## Recommendations

Fix these issues before merging.
"""


# ===========================================================================
# Tests
# ===========================================================================

class TestBuildCriticContext:
    """Tests for build_critic_context()."""

    def test_returns_string(self, mod):
        """Function returns a string."""
        result = mod.build_critic_context(SAMPLE_REPORT, _make_findings())
        assert isinstance(result, str)

    def test_contains_report_section(self, mod):
        """Output has '## Review Report' and the report content."""
        result = mod.build_critic_context(SAMPLE_REPORT, _make_findings())
        assert "## Review Report" in result
        assert "User.php file has a direct XSS vulnerability" in result

    def test_contains_findings_section(self, mod):
        """Output has '## Structured Findings'."""
        result = mod.build_critic_context(SAMPLE_REPORT, _make_findings())
        assert "## Structured Findings" in result

    def test_assigns_sequential_ids(self, mod):
        """Output has F1, F2, F3 for three issues."""
        findings = _make_findings()
        result = mod.build_critic_context(SAMPLE_REPORT, findings)
        assert "### F1:" in result
        assert "### F2:" in result
        assert "### F3:" in result
        # Original UUIDs should not appear
        assert "aaa11111" not in result
        assert "bbb22222" not in result
        assert "ccc33333" not in result

    def test_includes_severity_and_file_line(self, mod):
        """Severity and file:line location are present."""
        result = mod.build_critic_context(SAMPLE_REPORT, _make_findings())
        assert "high" in result
        assert "`src/User.php:42`" in result
        assert "`src/Query.php:88`" in result

    def test_includes_severity_floor(self, mod):
        issue = _make_issue(severity="medium", severity_floor="medium")

        result = mod.build_critic_context(
            SAMPLE_REPORT,
            _make_findings(issues=[issue]),
        )

        assert "- **Severity floor:** medium" in result

    @pytest.mark.parametrize("marker_source", ["report", "description"])
    def test_does_not_expose_rejected_legacy_floor(self, mod, marker_source):
        marker = "Severity-floor: silent false-success;"
        retained_context = "The verified issue details remain relevant."
        legacy_floor_text = f"{marker} {retained_context}"
        report = SAMPLE_REPORT
        description = "An unrelated issue description."
        if marker_source == "report":
            report = f"{SAMPLE_REPORT}\n{legacy_floor_text}\n"
        else:
            description = legacy_floor_text
        issue = _make_issue(severity="medium", description=description)

        result = mod.build_critic_context(
            report,
            _make_findings(issues=[issue]),
        )

        assert marker not in result
        assert retained_context in result

    def test_includes_reconciliation_metrics(self, mod):
        """Pipeline stats are present."""
        result = mod.build_critic_context(SAMPLE_REPORT, _make_findings())
        assert "## Reconciliation Metrics" in result
        assert "15 findings from 5 agents" in result
        assert "7 verified concerns" in result
        assert "53%" in result
        assert "3 false positives" in result
        assert "5 out-of-scope" in result
        assert "security-review" in result
        assert "performance-review" in result

    def test_includes_verdict(self, mod):
        """Verdict is included in the output."""
        result = mod.build_critic_context(SAMPLE_REPORT, _make_findings())
        assert "REQUEST_CHANGES" in result

    def test_empty_issues(self, mod):
        """Handles no findings gracefully."""
        findings = _make_findings(
            verdict="approve",
            issues=[],
            recommendations={"immediate": [], "important": [], "suggestions": []},
            meta={
                "reconciliation": {
                    "input_findings_count": 5,
                    "agents_contributing": 3,
                    "concerns_after_grouping": 0,
                    "false_positives_dropped": 5,
                    "out_of_scope_dropped": 0,
                    "verified_concerns": 0,
                    "merge_ratio": 0.0,
                    "reviewing_agents": ["security-review"],
                    "missing_agents": [],
                    "not_applicable_agents": [],
                }
            },
        )
        result = mod.build_critic_context(SAMPLE_REPORT, findings)
        assert isinstance(result, str)
        assert "## Structured Findings" in result
        assert "0 findings" in result

    def test_no_json_braces_in_findings_section(self, mod):
        """Findings section is Markdown, not raw JSON."""
        result = mod.build_critic_context(SAMPLE_REPORT, _make_findings())
        # Extract the Structured Findings section
        start = result.index("## Structured Findings")
        # Find next major section (---) or end
        end = result.find("---", start)
        if end == -1:
            end = len(result)
        findings_section = result[start:end]
        # No JSON object braces
        assert "{" not in findings_section
        assert "}" not in findings_section

    def test_includes_recommendations(self, mod):
        """Recommendations section present when data exists."""
        result = mod.build_critic_context(SAMPLE_REPORT, _make_findings())
        assert "Recommendations" in result
        assert "[immediate]" in result
        assert "Fix XSS in User.php" in result
        assert "[important]" in result
        assert "Add nonce checks" in result

    def test_fences_report_with_dynamic_fence(self, mod):
        """When report contains triple backticks, fence adjusts."""
        report_with_backticks = "Here is some code:\n```python\nprint('hello')\n```\nEnd."
        result = mod.build_critic_context(report_with_backticks, _make_findings())
        # The fence around the report must be longer than 3 backticks
        # since the report itself contains ```
        lines = result.split("\n")
        in_report_section = False
        found_longer_fence = False
        for line in lines:
            if "## Review Report" in line:
                in_report_section = True
                continue
            if in_report_section and line.startswith("````"):
                found_longer_fence = True
                break
        assert found_longer_fence, "Expected a fence longer than ``` to wrap report containing backticks"


class TestNonReviewFiles:
    """Verify critic-context.md is in _NON_REVIEW_FILES."""

    def test_critic_context_in_non_review_files(self, mod):
        """critic-context.md should be excluded from agent finding loading."""
        assert "critic-context.md" in mod._NON_REVIEW_FILES

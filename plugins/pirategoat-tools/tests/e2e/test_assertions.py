"""Tests for e2e assertion helpers."""

import json
from pathlib import Path

import pytest

from assertions import (
    assert_file_exists,
    assert_valid_json,
    assert_context_schema,
    assert_dispatch_plan,
    assert_findings_severity,
    assert_context_field,
    assert_verdict_in,
    assert_must_skip_triage,
    AssertionFailure,
)


@pytest.fixture
def context_file(tmp_path):
    """Write a valid review-context.json and return the path."""
    ctx = {
        "version": 1,
        "mode": "pr",
        "github_cli_command": "gh",
        "git": {
            "merge_base": "abc123",
            "git_range": "abc123..feat/thing",
            "head_ref": "feat/thing",
            "base_ref": "main",
            "changed_files": ["src/a.php"],
            "changed_files_csv": "src/a.php",
            "diff_stats": " 1 file changed",
            "commit_count": 2,
        },
        "output": {"directory": str(tmp_path)},
        "pr": {
            "number": 1,
            "title": "Test PR",
            "author": "testuser",
            "state": "OPEN",
            "is_draft": False,
            "base_ref_name": "main",
            "head_ref_name": "feat/thing",
        },
        "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        "review": {"agent_timeout_seconds": 1200},
        "source": "gather-context-pr",
    }
    path = tmp_path / "review-context.json"
    path.write_text(json.dumps(ctx))
    return str(path)


class TestFileExists:
    def test_passes_when_file_exists(self, context_file):
        result = assert_file_exists(context_file)
        assert result.passed

    def test_fails_when_file_missing(self):
        result = assert_file_exists("/nonexistent/path.json")
        assert not result.passed
        assert "not found" in result.reason.lower()


class TestValidJson:
    def test_passes_for_valid_json(self, context_file):
        result = assert_valid_json(context_file)
        assert result.passed

    def test_fails_for_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json {{{")
        result = assert_valid_json(str(bad))
        assert not result.passed


class TestContextSchema:
    def test_passes_for_valid_context(self, context_file):
        result = assert_context_schema(context_file)
        assert result.passed

    def test_fails_when_git_missing(self, tmp_path):
        path = tmp_path / "review-context.json"
        path.write_text(json.dumps({"version": 1}))
        result = assert_context_schema(str(path))
        assert not result.passed


class TestContextField:
    def test_matches_nested_field(self, context_file):
        result = assert_context_field(context_file, "git.base_ref", "main")
        assert result.passed

    def test_fails_on_wrong_value(self, context_file):
        result = assert_context_field(context_file, "git.base_ref", "develop")
        assert not result.passed

    def test_fails_on_missing_path(self, context_file):
        result = assert_context_field(context_file, "git.nonexistent", "x")
        assert not result.passed


class TestDispatchPlan:
    def test_passes_when_agents_dispatched(self, tmp_path):
        plan = {
            "agents": [
                {"name": "pr-reviewer", "status": "DISPATCH"},
                {"name": "security-reviewer", "status": "DISPATCH"},
                {"name": "a11y-reviewer", "status": "SKIP", "reason": "no files"},
            ],
        }
        path = tmp_path / "dispatch-plan.json"
        path.write_text(json.dumps(plan))
        result = assert_dispatch_plan(
            str(path),
            must_dispatch=["pr-reviewer", "security-reviewer"],
            min_dispatched=2,
        )
        assert result.passed

    def test_fails_when_expected_agent_skipped(self, tmp_path):
        plan = {
            "agents": [
                {"name": "pr-reviewer", "status": "DISPATCH"},
                {"name": "security-reviewer", "status": "SKIP", "reason": "no files"},
            ],
        }
        path = tmp_path / "dispatch-plan.json"
        path.write_text(json.dumps(plan))
        result = assert_dispatch_plan(
            str(path),
            must_dispatch=["security-reviewer"],
        )
        assert not result.passed


class TestFindingsSeverity:
    def test_passes_within_bounds(self, tmp_path):
        reconciled = {
            "clusters": [
                {"severity": "critical", "title": "SQL injection"},
                {"severity": "medium", "title": "Missing i18n"},
            ],
        }
        path = tmp_path / "reconciled-structured.json"
        path.write_text(json.dumps(reconciled))
        result = assert_findings_severity(
            str(path), min_critical=1, max_critical=2,
        )
        assert result.passed

    def test_fails_below_minimum(self, tmp_path):
        reconciled = {"clusters": [{"severity": "medium", "title": "Minor"}]}
        path = tmp_path / "reconciled-structured.json"
        path.write_text(json.dumps(reconciled))
        result = assert_findings_severity(str(path), min_critical=1)
        assert not result.passed

    def test_severity_reads_from_canonical_path(self, tmp_path):
        """Regression: severity must read from canonical.severity, not top-level."""
        data = {
            "clusters": [
                {"canonical": {"severity": "critical", "title": "XSS", "file": "a.php"}},
                {"canonical": {"severity": "high", "title": "SQLi", "file": "b.php"}},
            ]
        }
        path = tmp_path / "reconciled-structured.json"
        path.write_text(json.dumps(data))

        result = assert_findings_severity(
            str(path), min_critical=1, min_important=1
        )
        assert result.passed, f"Should find 1 critical + 1 high but got: {result.reason}"


class TestVerdictIn:
    def test_approve_for_low_findings(self, tmp_path):
        """verdict_in assertion computes verdict from cluster severities."""
        data = {
            "clusters": [
                {"canonical": {"severity": "low", "title": "Minor", "file": "a.php"}},
            ]
        }
        path = tmp_path / "reconciled-structured.json"
        path.write_text(json.dumps(data))

        result = assert_verdict_in(str(path), ["APPROVE", "COMMENT"])
        assert result.passed, f"No medium+ findings should yield APPROVE but: {result.reason}"

    def test_fails_when_verdict_excluded(self, tmp_path):
        """verdict_in fails when computed verdict is not in expected list."""
        data = {
            "clusters": [
                {"canonical": {"severity": "critical", "title": "XSS", "file": "a.php"}},
            ]
        }
        path = tmp_path / "reconciled-structured.json"
        path.write_text(json.dumps(data))

        result = assert_verdict_in(str(path), ["APPROVE", "COMMENT"])
        assert not result.passed, "Critical finding should yield BLOCK, not in [APPROVE, COMMENT]"

    def test_block_on_3_highs(self, tmp_path):
        """verdict_in: 3+ highs escalate to BLOCK."""
        data = {
            "clusters": [
                {"canonical": {"severity": "high", "title": f"Issue {i}", "file": f"{i}.php"}}
                for i in range(3)
            ]
        }
        path = tmp_path / "reconciled-structured.json"
        path.write_text(json.dumps(data))

        result = assert_verdict_in(str(path), ["BLOCK"])
        assert result.passed, f"3 highs should BLOCK but: {result.reason}"

    def test_request_changes_on_5_mediums(self, tmp_path):
        """verdict_in: 5+ mediums escalate to REQUEST_CHANGES."""
        data = {
            "clusters": [
                {"canonical": {"severity": "medium", "title": f"Issue {i}", "file": f"{i}.php"}}
                for i in range(5)
            ]
        }
        path = tmp_path / "reconciled-structured.json"
        path.write_text(json.dumps(data))

        result = assert_verdict_in(str(path), ["REQUEST_CHANGES"])
        assert result.passed, f"5 mediums should REQUEST_CHANGES but: {result.reason}"


class TestMustSkipTriage:
    def test_passes_when_agent_skipped(self, tmp_path):
        """must_skip_triage passes when expected agents have SKIPPED_TRIAGE status."""
        dispatch = tmp_path / "dispatch-plan.json"
        dispatch.write_text(json.dumps({
            "agents": [
                {"name": "a11y-reviewer", "domain": "a11y", "status": "SKIPPED_TRIAGE", "reason": "No frontend files"},
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "PHP files changed"},
            ]
        }))

        result = assert_must_skip_triage(str(dispatch), ["a11y-reviewer"])
        assert result.passed

    def test_fails_when_dispatched(self, tmp_path):
        """must_skip_triage fails when expected-skipped agent was dispatched."""
        dispatch = tmp_path / "dispatch-plan.json"
        dispatch.write_text(json.dumps({
            "agents": [
                {"name": "a11y-reviewer", "domain": "a11y", "status": "DISPATCH", "reason": "Frontend files found"},
            ]
        }))

        result = assert_must_skip_triage(str(dispatch), ["a11y-reviewer"])
        assert not result.passed

    def test_fails_when_agent_missing(self, tmp_path):
        """must_skip_triage fails when expected agent is not in dispatch plan at all."""
        dispatch = tmp_path / "dispatch-plan.json"
        dispatch.write_text(json.dumps({
            "agents": [
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "PHP files"},
            ]
        }))

        result = assert_must_skip_triage(str(dispatch), ["a11y-reviewer"])
        assert not result.passed

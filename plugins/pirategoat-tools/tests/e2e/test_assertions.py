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

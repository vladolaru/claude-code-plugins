"""Tests for iterative_review.backends.claude — output parsing and context composition."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # iterative_review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from iterative_review.backends.claude import (
    invoke_review,
    parse_output,
    write_prompt_file,
    get_rubric,
    check_auth,
    TIMEOUT_SENTINEL,
    TIMEOUT,
    _EFFORT_MAP,
)


# --- Sample data ---

# The findings payload (matches claude-review-schema.json)
SAMPLE_FINDINGS = {
    "findings": [
        {
            "title": "Missing null check",
            "body": "The function does not check for null input.",
            "confidence_score": 0.9,
            "priority": 1,
            "code_location": {
                "file_path": "src/handler.py",
                "line_range": {"start": 42, "end": 45}
            }
        },
        {
            "title": "Consider using const",
            "body": "Variable is never reassigned.",
            "confidence_score": 0.6,
            "priority": 3,
            "code_location": {
                "file_path": "src/utils.py",
                "line_range": {"start": 10, "end": 10}
            }
        }
    ],
    "overall_correctness": "patch is mostly correct",
    "overall_explanation": "Two issues found.",
    "overall_confidence_score": 0.8
}

# CC returns a JSON envelope with structured_output
SAMPLE_CC_RESPONSE = json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "The model's text response summarizing findings.",
    "structured_output": SAMPLE_FINDINGS,
    "total_cost_usd": 0.21,
    "session_id": "test-session-uuid"
})

# CC response without structured_output (degraded — only result field)
SAMPLE_CC_RESPONSE_DEGRADED = json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "Some review prose that is not structured.",
    "total_cost_usd": 0.05,
    "session_id": "test-session-uuid-2"
})

# CC response with empty findings in structured_output
SAMPLE_CC_RESPONSE_EMPTY = json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "No issues found.",
    "structured_output": {
        "findings": [],
        "overall_correctness": "clean",
        "overall_explanation": "No issues.",
        "overall_confidence_score": 0.95
    },
    "total_cost_usd": 0.10,
    "session_id": "test-session-uuid-3"
})


class TestParseOutput:
    """parse_output receives raw CC stdout (JSON envelope) and extracts findings."""

    def test_parses_structured_output(self):
        findings, degraded = parse_output(SAMPLE_CC_RESPONSE, round_num=1)
        assert len(findings) == 2
        assert findings[0]["id"] == "r1_f1"
        assert findings[0]["severity"] == "P1"
        assert findings[0]["title"] == "Missing null check"
        assert findings[0]["location"] == "src/handler.py:42-45"
        assert findings[0]["confidence"] == 0.9
        assert findings[1]["id"] == "r1_f2"
        assert findings[1]["severity"] == "P3"
        assert findings[1]["location"] == "src/utils.py:10"
        assert degraded is False

    def test_assigns_round_prefix_to_ids(self):
        findings, _ = parse_output(SAMPLE_CC_RESPONSE, round_num=3)
        assert findings[0]["id"] == "r3_f1"
        assert findings[1]["id"] == "r3_f2"

    def test_degraded_fallback_no_structured_output(self):
        """When structured_output is missing, falls back to result field as plain text."""
        findings, degraded = parse_output(SAMPLE_CC_RESPONSE_DEGRADED, round_num=2)
        assert len(findings) == 1
        assert findings[0]["id"] == "r2_raw"
        assert findings[0]["severity"] == "unknown"
        assert "Some review prose" in findings[0]["body"]
        assert degraded is True

    def test_plain_text_fallback(self):
        """Non-JSON raw output triggers degraded mode."""
        raw = "Some review prose that is not JSON at all."
        findings, degraded = parse_output(raw, round_num=2)
        assert len(findings) == 1
        assert findings[0]["id"] == "r2_raw"
        assert findings[0]["severity"] == "unknown"
        assert "Some review prose" in findings[0]["body"]
        assert degraded is True

    def test_empty_findings_returns_empty(self):
        findings, degraded = parse_output(SAMPLE_CC_RESPONSE_EMPTY, round_num=1)
        assert len(findings) == 0
        assert degraded is False

    def test_missing_code_location_handled(self):
        response = json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "One issue.",
            "structured_output": {
                "findings": [{
                    "title": "Issue",
                    "body": "Detail",
                    "confidence_score": 0.5,
                    "priority": 2,
                }],
                "overall_correctness": "minor issues",
                "overall_explanation": "One issue found.",
            },
            "session_id": "test"
        })
        findings, _ = parse_output(response, round_num=1)
        assert findings[0]["location"] == "unknown"

    def test_file_path_used_instead_of_absolute(self):
        """CC schema uses file_path (relative), not absolute_file_path."""
        response = json.dumps({
            "type": "result",
            "structured_output": {
                "findings": [{
                    "title": "Test",
                    "body": "Detail",
                    "priority": 1,
                    "code_location": {
                        "file_path": "lib/utils.js",
                        "line_range": {"start": 5, "end": 5}
                    }
                }],
                "overall_correctness": "ok",
                "overall_explanation": "ok",
            }
        })
        findings, _ = parse_output(response, round_num=1)
        assert findings[0]["location"] == "lib/utils.js:5"

    def test_line_range_with_different_start_end(self):
        """When start != end, produces file:start-end format."""
        response = json.dumps({
            "type": "result",
            "structured_output": {
                "findings": [{
                    "title": "Multi-line issue",
                    "body": "Spans multiple lines.",
                    "priority": 2,
                    "code_location": {
                        "file_path": "src/foo.py",
                        "line_range": {"start": 10, "end": 20}
                    }
                }],
                "overall_correctness": "ok",
                "overall_explanation": "ok",
            }
        })
        findings, _ = parse_output(response, round_num=1)
        assert findings[0]["location"] == "src/foo.py:10-20"

    def test_is_error_envelope_returns_empty_findings(self):
        """CLI error envelopes (auth failures, budget, etc.) return empty findings, not pseudo-findings."""
        error_response = json.dumps({
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "Not logged in · Please run /login",
            "session_id": "test"
        })
        findings, degraded = parse_output(error_response, round_num=1)
        assert len(findings) == 0
        assert degraded is True

    def test_is_error_false_not_rejected(self):
        """Normal responses with is_error=false are not rejected."""
        findings, degraded = parse_output(SAMPLE_CC_RESPONSE, round_num=1)
        assert len(findings) == 2
        assert degraded is False

    def test_no_line_range_returns_path_only(self):
        """When line_range is missing, returns just the file path."""
        response = json.dumps({
            "type": "result",
            "structured_output": {
                "findings": [{
                    "title": "General issue",
                    "body": "Whole file.",
                    "priority": 3,
                    "code_location": {
                        "file_path": "README.md"
                    }
                }],
                "overall_correctness": "ok",
                "overall_explanation": "ok",
            }
        })
        findings, _ = parse_output(response, round_num=1)
        assert findings[0]["location"] == "README.md"


class TestWritePromptFile:
    """Prompt composition is identical to codex backend."""

    def test_writes_prompt_to_file(self, tmp_path):
        path = write_prompt_file(
            str(tmp_path), 1, rubric="# Review Guidelines\nBe thorough.",
            merge_base="abc123", context="Fix checkout button bug.",
            pushback_log=None, analysis_doc_path="r1-analysis.md",
        )
        assert Path(path).exists()
        content = Path(path).read_text()
        assert "Review Guidelines" in content
        assert "abc123" in content
        assert "Fix checkout button bug" in content
        assert "r1-analysis.md" in content
        assert "Review History" not in content

    def test_round_2_includes_pushback_and_prior_analysis(self, tmp_path):
        path = write_prompt_file(
            str(tmp_path), 2, rubric="# Rubric",
            merge_base="abc123", context="Context.",
            pushback_log="### Round 1\nREJECTED: [r1_f2] ...",
            analysis_doc_path="r2-analysis.md",
            prior_analysis_path="r1-analysis.md",
        )
        content = Path(path).read_text()
        assert "Review History" in content
        assert "REJECTED" in content
        assert "r1-analysis.md" in content
        assert "r2-analysis.md" in content

    def test_round_2_includes_deferred_items_section(self, tmp_path):
        deferred = [
            {"severity": "P2", "title": "Missing null check", "location": "api.ts:42"},
            {"severity": "P3", "title": "Vague error message", "location": "handler.ts:15"},
        ]
        path = write_prompt_file(
            str(tmp_path), 2, rubric="# Rubric",
            merge_base="abc123", context="Context.",
            pushback_log="### Round 1\nREJECTED: ...",
            analysis_doc_path="r2-analysis.md",
            deferred_items=deferred,
        )
        content = Path(path).read_text()
        assert "Previously Deferred Items" in content
        assert "Missing null check" in content
        assert "api.ts:42" in content
        assert "Do not re-raise deferred items" in content

    def test_no_deferred_section_when_empty(self, tmp_path):
        path = write_prompt_file(
            str(tmp_path), 2, rubric="# Rubric",
            merge_base="abc123", context="Context.",
            pushback_log="### Round 1\nREJECTED: ...",
            analysis_doc_path="r2-analysis.md",
            deferred_items=None,
        )
        content = Path(path).read_text()
        assert "Previously Deferred Items" not in content

    def test_review_history_mentions_deferred(self, tmp_path):
        path = write_prompt_file(
            str(tmp_path), 2, rubric="# Rubric",
            merge_base="abc123", context="Context.",
            pushback_log="### Round 1\nDEFERRED: ...",
            analysis_doc_path="r2-analysis.md",
        )
        content = Path(path).read_text()
        assert "Treat deferred items as" in content
        assert "out-of-scope" in content

    def test_round_specific_filename(self, tmp_path):
        path = write_prompt_file(
            str(tmp_path), 3, rubric="# R", merge_base="x",
            context="", pushback_log=None, analysis_doc_path="a.md",
        )
        assert Path(path) == tmp_path / "reviewers" / "round-3" / "prompt.md"


class TestInvokeReview:
    """Tests for subprocess command construction and response handling."""

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_command_contains_isolation_flags(self, mock_run, tmp_path):
        """The CC command includes all isolation flags."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        # First call: git rev-parse, second call: claude
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60)

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        # Check isolation flags are present
        assert "--permission-mode" in cmd
        assert "dontAsk" in cmd
        assert "--settings" in cmd
        assert "--mcp-config" in cmd
        assert "--strict-mcp-config" in cmd
        assert "--disable-slash-commands" in cmd

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_command_uses_json_output_format(self, mock_run, tmp_path):
        """The CC command requests JSON output format."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60)

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "json"

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_command_passes_schema_inline(self, mock_run, tmp_path):
        """The schema JSON is passed inline via --json-schema, not as a file path."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema_content = '{"type":"object","properties":{"findings":{"type":"array"}}}'
        schema.write_text(schema_content)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60)

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        idx = cmd.index("--json-schema")
        # The value should be the schema content string, not the file path
        assert cmd[idx + 1] == schema_content

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_prompt_passed_via_input_kwarg(self, mock_run, tmp_path):
        """The prompt content is passed via input= kwarg to subprocess.run."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code please.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60)

        claude_call = mock_run.call_args_list[1]
        kwargs = claude_call[1]
        assert kwargs.get("input") == "Review this code please."

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_no_effort_omits_effort_flag(self, mock_run, tmp_path):
        """When effort is None, --effort is not in the command."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60)

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        assert "--effort" not in cmd

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_effort_high_injects_flag(self, mock_run, tmp_path):
        """When effort='high', --effort high is present."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60, effort="high")

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        idx = cmd.index("--effort")
        assert cmd[idx + 1] == "high"

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_effort_xhigh_maps_to_high(self, mock_run, tmp_path):
        """CC caps at 'high' — xhigh maps to high via _EFFORT_MAP."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60, effort="xhigh")

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        idx = cmd.index("--effort")
        assert cmd[idx + 1] == "high"  # xhigh -> high

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_effort_medium_stays_medium(self, mock_run, tmp_path):
        """Medium effort passes through unchanged."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60, effort="medium")

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        idx = cmd.index("--effort")
        assert cmd[idx + 1] == "medium"

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_timeout_returns_sentinel(self, mock_run, tmp_path):
        """invoke_review returns TIMEOUT_SENTINEL on TimeoutExpired."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            subprocess.TimeoutExpired(cmd="claude", timeout=TIMEOUT),
        ]

        result, success = invoke_review(str(prompt), str(schema))
        assert result == TIMEOUT_SENTINEL
        assert success is False

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_file_not_found_returns_empty(self, mock_run, tmp_path):
        """When claude binary is not found, returns empty string."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            FileNotFoundError("claude not found"),
        ]

        result, success = invoke_review(str(prompt), str(schema))
        assert result == ""
        assert success is False

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_returns_raw_stdout(self, mock_run, tmp_path):
        """invoke_review returns the raw stdout string from CC."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        result, success = invoke_review(str(prompt), str(schema))
        assert result == SAMPLE_CC_RESPONSE
        assert success is True

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_nonzero_exit_returns_stdout_with_false(self, mock_run, tmp_path):
        """Non-zero exit still returns stdout but success=False."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=1, stdout="partial output"),
        ]

        result, success = invoke_review(str(prompt), str(schema))
        assert result == "partial output"
        assert success is False

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_uses_model_sonnet(self, mock_run, tmp_path):
        """The CC command specifies --model sonnet."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60)

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_allowed_tools_scoped(self, mock_run, tmp_path):
        """The CC command scopes tools to Read, Grep, Glob, Write, and git commands."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60)

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        idx = cmd.index("--allowedTools")
        tools = cmd[idx + 1]
        assert "Read" in tools
        assert "Grep" in tools
        assert "Glob" in tools
        assert "Write" in tools
        assert "git diff" in tools

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_add_dir_when_output_dir_provided(self, mock_run, tmp_path):
        """When output_dir= is passed, --add-dir grants access to the workspace."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60,
                      output_dir="/tmp/iterative-review-test")

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        idx = cmd.index("--add-dir")
        assert cmd[idx + 1] == "/tmp/iterative-review-test"

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_no_add_dir_when_output_dir_absent(self, mock_run, tmp_path):
        """When output_dir= is not passed, --add-dir is not in the command."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60)

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        assert "--add-dir" not in cmd

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_invoke_review_ignores_extra_kwargs(self, mock_run, tmp_path):
        """invoke_review silently ignores output_file= kwarg (CC doesn't use it)."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        # Should not raise TypeError for unexpected keyword argument
        result, success = invoke_review(
            str(prompt), str(schema), timeout=60,
            output_file="/tmp/ignored.json"
        )
        assert success is True


class TestCheckAuth:
    """Tests for check_auth — uses `claude auth status` for real auth check."""

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_logged_in_returns_true(self, mock_run):
        """When claude auth status reports loggedIn=true, returns (True, ...)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"loggedIn": true, "authMethod": "claude.ai", "email": "test@test.com"}',
            stderr=""
        )
        ok, msg = check_auth()
        assert ok is True
        assert "loggedIn" in msg

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_not_logged_in_returns_false(self, mock_run):
        """When claude auth status reports loggedIn=false, returns (False, ...)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"loggedIn": false}',
            stderr=""
        )
        ok, msg = check_auth()
        assert ok is False
        assert "not logged in" in msg

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_not_found_returns_error(self, mock_run):
        """When claude is not in PATH, returns (False, error message)."""
        mock_run.side_effect = FileNotFoundError("claude not found")
        ok, msg = check_auth()
        assert ok is False
        assert "not found" in msg

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_nonzero_exit_returns_error(self, mock_run):
        """When claude auth status exits non-zero, returns (False, stderr)."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="auth command failed"
        )
        ok, msg = check_auth()
        assert ok is False
        assert "auth command failed" in msg

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_unparseable_json_returns_error(self, mock_run):
        """When auth status output isn't valid JSON, returns (False, ...)."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="not json at all", stderr=""
        )
        ok, msg = check_auth()
        assert ok is False
        assert "unexpected" in msg

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_timeout_returns_error(self, mock_run):
        """When claude auth status times out, returns (False, timeout message)."""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="claude", timeout=10
        )
        ok, msg = check_auth()
        assert ok is False
        assert "timed out" in msg


class TestEffortMap:
    """Effort mapping constants."""

    def test_medium_maps_to_medium(self):
        assert _EFFORT_MAP["medium"] == "medium"

    def test_high_maps_to_high(self):
        assert _EFFORT_MAP["high"] == "high"

    def test_xhigh_maps_to_high(self):
        assert _EFFORT_MAP["xhigh"] == "high"


class TestTimeoutSentinel:
    """TIMEOUT_SENTINEL and TIMEOUT constants."""

    def test_sentinel_is_string(self):
        assert isinstance(TIMEOUT_SENTINEL, str)

    def test_timeout_is_1800(self):
        assert TIMEOUT == 1800

    def test_sentinel_contains_claude(self):
        """Sentinel is distinct from codex sentinel."""
        assert "CLAUDE" in TIMEOUT_SENTINEL


class TestRubric:
    """get_rubric reads the shared rubric file."""

    def test_rubric_file_exists(self):
        rubric = get_rubric()
        assert len(rubric) > 0, "Rubric file missing or empty"

    def test_rubric_contains_severity_levels(self):
        rubric = get_rubric()
        assert "P0" in rubric
        assert "P1" in rubric
        assert "P2" in rubric
        assert "P3" in rubric

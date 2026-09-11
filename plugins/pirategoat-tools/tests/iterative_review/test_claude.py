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
    check_auth,
    TIMEOUT_SENTINEL,
    TIMEOUT,
)
from iterative_review.backends import codex as codex_backend


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
    """claude.write_prompt_file and codex.write_prompt_file are
    byte-identical apart from their docstrings. Full coverage of the
    composed prompt shape (pushback log, deferred items, prior analysis,
    round-specific filename) lives in test_codex.py::TestWritePromptFile;
    this is the parity guard that keeps the two backends in lockstep."""

    def test_matches_codex_backend_byte_for_byte(self, tmp_path):
        kwargs = dict(
            rubric="# Rubric",
            merge_base="abc123",
            context="Context.",
            pushback_log="### Round 1\nREJECTED: [r1_f2] ...",
            analysis_doc_path="r2-analysis.md",
            prior_analysis_path="r1-analysis.md",
            deferred_items=[
                {"severity": "P2", "title": "Missing null check", "location": "api.ts:42"},
            ],
        )
        claude_path = write_prompt_file(str(tmp_path / "claude"), 2, **kwargs)
        codex_path = codex_backend.write_prompt_file(str(tmp_path / "codex"), 2, **kwargs)
        assert Path(claude_path).read_text() == Path(codex_path).read_text()


class TestInvokeReview:
    """Tests for subprocess command construction and response handling."""

    @patch("iterative_review.backends.claude.subprocess.run")
    def test_default_command_shape(self, mock_run, tmp_path):
        """One invocation pins every isolation flag, the model, the output
        format, the inline schema, the input-kwarg prompt delivery, the
        scoped tool allowlist, and the two flags absent by default."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code please.")
        schema = tmp_path / "schema.json"
        schema_content = '{"type":"object","properties":{"findings":{"type":"array"}}}'
        schema.write_text(schema_content)
        # First call: git rev-parse, second call: claude
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60)

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        kwargs = claude_call[1]

        # Isolation flags
        assert "--permission-mode" in cmd
        assert "dontAsk" in cmd
        assert "--settings" in cmd
        assert "--mcp-config" in cmd
        assert "--strict-mcp-config" in cmd
        assert "--disable-slash-commands" in cmd

        # Model and output format
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "json"

        # Schema is passed inline, not as a file path
        idx = cmd.index("--json-schema")
        assert cmd[idx + 1] == schema_content

        # Tool allowlist
        idx = cmd.index("--allowedTools")
        tools = cmd[idx + 1]
        assert "Read" in tools
        assert "Grep" in tools
        assert "Glob" in tools
        assert "Write" in tools
        assert "git diff" in tools

        # Prompt delivered via input= kwarg, not embedded in the command
        assert kwargs.get("input") == "Review this code please."

        # Absent by default
        assert "--effort" not in cmd
        assert "--add-dir" not in cmd

    @pytest.mark.parametrize(
        "effort,expected",
        [
            pytest.param("xhigh", "high", id="xhigh-maps-to-high"),
            pytest.param("medium", "medium", id="medium-passthrough"),
        ],
    )
    @patch("iterative_review.backends.claude.subprocess.run")
    def test_claude_effort_flag(self, mock_run, tmp_path, effort, expected):
        """_EFFORT_MAP caps Sonnet at 'high'; other values pass through unchanged."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        schema = tmp_path / "schema.json"
        schema.write_text('{"type":"object"}')
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            MagicMock(returncode=0, stdout=SAMPLE_CC_RESPONSE),
        ]

        invoke_review(str(prompt), str(schema), timeout=60, effort=effort)

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        idx = cmd.index("--effort")
        assert cmd[idx + 1] == expected

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


def _auth_logged_in(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"loggedIn": true, "authMethod": "claude.ai", "email": "test@test.com"}',
        stderr="",
    )


def _auth_not_logged_in(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout='{"loggedIn": false}', stderr="")


def _auth_not_found(mock_run):
    mock_run.side_effect = FileNotFoundError("claude not found")


def _auth_nonzero_exit(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="auth command failed")


def _auth_unparseable_json(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="not json at all", stderr="")


def _auth_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=10)


class TestCheckAuth:
    """check_auth uses `claude auth status` (claude --version is NOT enough —
    it exits 0 even when unauthenticated). Fix 1a59efe0: an unauthenticated
    CC previously produced pseudo-findings instead of a clean failure."""

    @pytest.mark.parametrize(
        "mock_setup,expected_ok,msg_fragment",
        [
            pytest.param(_auth_logged_in, True, "loggedIn", id="logged-in"),
            pytest.param(_auth_not_logged_in, False, "not logged in", id="not-logged-in"),
            pytest.param(_auth_not_found, False, "not found", id="binary-not-found"),
            pytest.param(_auth_nonzero_exit, False, "auth command failed", id="nonzero-exit"),
            pytest.param(_auth_unparseable_json, False, "unexpected", id="unparseable-json"),
            pytest.param(_auth_timeout, False, "timed out", id="timeout"),
        ],
    )
    @patch("iterative_review.backends.claude.subprocess.run")
    def test_check_auth(self, mock_run, mock_setup, expected_ok, msg_fragment):
        mock_setup(mock_run)
        ok, msg = check_auth()
        assert ok is expected_ok
        assert msg_fragment in msg

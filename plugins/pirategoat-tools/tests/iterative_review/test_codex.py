"""Tests for iterative_review.backends.codex — output parsing and context composition."""

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

from iterative_review.backends.codex import (
    invoke_review,
    parse_output,
    check_auth,
    write_prompt_file,
    get_rubric,
    detect_failure_reason,
    TIMEOUT_SENTINEL,
    TIMEOUT,
)


SAMPLE_JSON_OUTPUT = json.dumps({
    "findings": [
        {
            "title": "Missing null check",
            "body": "The function does not check for null input.",
            "confidence_score": 0.9,
            "priority": 1,
            "code_location": {
                "absolute_file_path": "/src/handler.py",
                "line_range": {"start": 42, "end": 45}
            }
        },
        {
            "title": "Consider using const",
            "body": "Variable is never reassigned.",
            "confidence_score": 0.6,
            "priority": 3,
            "code_location": {
                "absolute_file_path": "/src/utils.py",
                "line_range": {"start": 10, "end": 10}
            }
        }
    ],
    "overall_correctness": "patch is mostly correct",
    "overall_explanation": "Two issues found.",
    "overall_confidence_score": 0.8
})


class TestParseOutput:
    def test_parses_structured_json(self):
        findings, degraded = parse_output(SAMPLE_JSON_OUTPUT, round_num=1)
        assert len(findings) == 2
        assert findings[0]["id"] == "r1_f1"
        assert findings[0]["severity"] == "P1"
        assert findings[0]["title"] == "Missing null check"
        assert findings[0]["location"] == "/src/handler.py:42-45"
        assert findings[1]["id"] == "r1_f2"
        assert findings[1]["severity"] == "P3"
        assert degraded is False

    def test_assigns_round_prefix_to_ids(self):
        findings, _ = parse_output(SAMPLE_JSON_OUTPUT, round_num=3)
        assert findings[0]["id"] == "r3_f1"
        assert findings[1]["id"] == "r3_f2"

    def test_plain_text_fallback(self):
        raw = "Some review prose that is not JSON."
        findings, degraded = parse_output(raw, round_num=2)
        assert len(findings) == 1
        assert findings[0]["id"] == "r2_raw"
        assert findings[0]["severity"] == "unknown"
        assert "Some review prose" in findings[0]["body"]
        assert degraded is True

    def test_empty_findings_returns_empty(self):
        output = json.dumps({"findings": [], "overall_correctness": "clean"})
        findings, degraded = parse_output(output, round_num=1)
        assert len(findings) == 0
        assert degraded is False

    def test_missing_code_location_handled(self):
        output = json.dumps({"findings": [{
            "title": "Issue",
            "body": "Detail",
            "confidence_score": 0.5,
            "priority": 2,
        }]})
        findings, _ = parse_output(output, round_num=1)
        assert findings[0]["location"] == "unknown"


class TestWritePromptFile:
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


class TestRubric:
    def test_rubric_contains_severity_levels(self):
        rubric = get_rubric()
        assert len(rubric) > 0, "Rubric file missing or empty"
        assert "P0" in rubric
        assert "P1" in rubric
        assert "P2" in rubric
        assert "P3" in rubric


class TestInvokeReviewEffort:
    """Tests for the effort parameter in invoke_review()."""

    @pytest.mark.parametrize(
        "effort,expect_c_value,expect_fast",
        [
            pytest.param(None, None, False, id="no-effort"),
            pytest.param("medium", "medium", False, id="medium"),
            pytest.param("high", "high", True, id="high"),
            pytest.param("xhigh", "xhigh", True, id="xhigh"),
        ],
    )
    @patch("iterative_review.backends.codex.subprocess.run")
    def test_codex_effort_cmd(self, mock_run, tmp_path, effort, expect_c_value, expect_fast):
        """-c presence/value, fast-tier presence, and the stdin marker '-'
        staying last are facets of one command per effort value."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        invoke_review(
            str(prompt), "schema.json",
            output_file=str(tmp_path / "out.json"), effort=effort
        )

        # The second call is the actual codex exec (first is git rev-parse)
        codex_call = mock_run.call_args_list[1]
        cmd = codex_call[0][0]
        assert cmd[-1] == "-", "stdin marker '-' must be last element"
        if expect_c_value is None:
            assert "-c" not in cmd
        else:
            c_idx = cmd.index("-c")
            assert cmd[c_idx + 1] == f'model_reasoning_effort="{expect_c_value}"'
            assert c_idx < len(cmd) - 1, "-c flag must come before the stdin marker"
        assert ('service_tier="fast"' in cmd) is expect_fast


class TestTimeoutSentinel:
    """TIMEOUT_SENTINEL is returned when invoke_review's subprocess times out."""

    @patch("iterative_review.backends.codex.subprocess.run")
    def test_timeout_returns_sentinel(self, mock_run, tmp_path):
        """invoke_review returns TIMEOUT_SENTINEL on TimeoutExpired."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        # First call is git rev-parse (succeeds), second raises TimeoutExpired
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=str(tmp_path)),
            subprocess.TimeoutExpired(cmd="codex", timeout=TIMEOUT),
        ]
        result, success = invoke_review(
            str(prompt), "schema.json",
            output_file=str(tmp_path / "out.json")
        )
        assert result == TIMEOUT_SENTINEL
        assert success is False


class TestInvokeReviewOutputFile:
    """invoke_review handles output_file kwarg."""

    @patch("iterative_review.backends.codex.subprocess.run")
    def test_invoke_review_accepts_output_file_kwarg(self, mock_run, tmp_path):
        """invoke_review passes output_file through to the codex exec command."""
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Review this code.")
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        out_file = str(tmp_path / "explicit-output.json")

        invoke_review(str(prompt), "schema.json", output_file=out_file)

        # The codex exec call should reference the explicit output file
        codex_call = mock_run.call_args_list[1]
        cmd = codex_call[0][0]
        assert out_file in cmd


class TestDetectFailureReason:
    """detect_failure_reason classifies Codex stderr for telemetry — one
    classifier, its rate-limit and unknown branches."""

    @pytest.mark.parametrize(
        "stderr,expected",
        [
            pytest.param(
                'ERROR codex_api: error=http 429 Too Many Requests: {"error": {"code": "rate_limit_exceeded"}}',
                "rate_limit", id="429-json",
            ),
            pytest.param(
                "You've hit your usage limit. Upgrade to Pro or try again in 4h.",
                "rate_limit", id="usage-limit-message",
            ),
            pytest.param("Error: connection refused", "unknown", id="generic-error"),
            pytest.param(None, "unknown", id="none-stderr"),
        ],
    )
    def test_detect_failure_reason(self, stderr, expected):
        assert detect_failure_reason(stderr) == expected

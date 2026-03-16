"""
Tests for run-ground-truth.py — ground truth collection orchestrator.

Tests tool detection, file classification, tool execution, parser integration,
and output schema. All external tool calls are mocked.

Zero external dependencies beyond stdlib + pytest.
"""

import importlib.util
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — import run-ground-truth as a module
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "run-ground-truth.py"

_spec = importlib.util.spec_from_file_location("run_ground_truth", str(SCRIPT_PATH))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_output_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _write_eslint_results(output_dir, violations=None):
    """Write a mock ESLint results JSON file."""
    if violations is None:
        violations = [
            {
                "filePath": "/project/src/app.js",
                "messages": [
                    {
                        "line": 42,
                        "column": 5,
                        "severity": 2,
                        "ruleId": "no-unused-vars",
                        "message": "'helper' is defined but never used",
                    }
                ],
            }
        ]
    with open(os.path.join(output_dir, "eslint-results.json"), "w") as f:
        json.dump(violations, f)


def _write_phpcs_results(output_dir, files=None):
    """Write a mock PHPCS results JSON file."""
    if files is None:
        files = {
            "src/handler.php": {
                "messages": [
                    {
                        "line": 23,
                        "column": 1,
                        "type": "ERROR",
                        "source": "WordPress.Security.EscapeOutput",
                        "message": "Output not escaped",
                    }
                ]
            }
        }
    data = {"totals": {"errors": 1, "warnings": 0}, "files": files}
    with open(os.path.join(output_dir, "phpcs-results.json"), "w") as f:
        json.dump(data, f)


def _write_semgrep_results(output_dir, results=None):
    """Write a mock Semgrep results JSON file."""
    if results is None:
        results = [
            {
                "path": "src/handler.php",
                "start": {"line": 15, "col": 1},
                "check_id": "php.lang.security.injection",
                "extra": {
                    "severity": "HIGH",
                    "message": "Possible SQL injection",
                    "metadata": {"cwe": ["CWE-89"]},
                },
            }
        ]
    data = {"results": results}
    with open(os.path.join(output_dir, "semgrep-results.json"), "w") as f:
        json.dump(data, f)


def _write_jest_results(output_dir, success=True, failures=None):
    """Write a mock Jest results JSON file."""
    data = {
        "success": success,
        "numTotalTests": 50,
        "numPassedTests": 48 if not success else 50,
        "numFailedTests": 2 if not success else 0,
        "testResults": failures or [],
    }
    with open(os.path.join(output_dir, "jest-results.json"), "w") as f:
        json.dump(data, f)




# =============================================================================
# Parser integration tests
# =============================================================================


class TestParseLinterFindings:
    """Tests for parse_linter_findings with real parser module."""

    def test_eslint_findings_parsed(self, tmp_output_dir):
        _write_eslint_results(tmp_output_dir)
        changed = frozenset(["src/app.js"])
        findings = _mod.parse_linter_findings(tmp_output_dir, changed)
        assert len(findings) >= 1
        f = findings[0]
        assert f["tool"] == "eslint"
        assert f["category"] == "lint"
        assert f["rule"] == "no-unused-vars"
        assert f["line"] == 42

    def test_phpcs_findings_parsed(self, tmp_output_dir):
        _write_phpcs_results(tmp_output_dir)
        changed = frozenset(["src/handler.php"])
        findings = _mod.parse_linter_findings(tmp_output_dir, changed)
        assert len(findings) >= 1
        f = findings[0]
        assert f["tool"] == "phpcs"
        assert f["category"] == "lint"
        assert f["rule"] == "WordPress.Security.EscapeOutput"

    def test_findings_filtered_to_changed_files(self, tmp_output_dir):
        _write_eslint_results(tmp_output_dir)
        # Changed files don't include the file with violations
        changed = frozenset(["src/other.js"])
        findings = _mod.parse_linter_findings(tmp_output_dir, changed)
        assert len(findings) == 0

    def test_no_results_returns_empty(self, tmp_output_dir):
        findings = _mod.parse_linter_findings(tmp_output_dir, frozenset())
        assert findings == []


class TestParseSecurityFindings:
    """Tests for parse_security_findings with real parser module."""

    def test_semgrep_findings_parsed(self, tmp_output_dir):
        _write_semgrep_results(tmp_output_dir)
        changed = frozenset(["src/handler.php"])
        findings = _mod.parse_security_findings(tmp_output_dir, changed)
        assert len(findings) >= 1
        f = findings[0]
        assert f["tool"] == "semgrep"
        assert f["category"] == "security"
        assert f["severity"] == "high"

    def test_info_severity_filtered_out(self, tmp_output_dir):
        _write_semgrep_results(
            tmp_output_dir,
            results=[
                {
                    "path": "src/app.js",
                    "start": {"line": 1, "col": 1},
                    "check_id": "info-rule",
                    "extra": {
                        "severity": "INFO",
                        "message": "Informational",
                        "metadata": {},
                    },
                }
            ],
        )
        changed = frozenset(["src/app.js"])
        findings = _mod.parse_security_findings(tmp_output_dir, changed)
        assert len(findings) == 0

    def test_findings_filtered_to_changed_files(self, tmp_output_dir):
        _write_semgrep_results(tmp_output_dir)
        changed = frozenset(["src/other.php"])
        findings = _mod.parse_security_findings(tmp_output_dir, changed)
        assert len(findings) == 0

    def test_bandit_results_ignored(self, tmp_output_dir):
        """Bandit results file should not be parsed even if present."""
        bandit_data = {
            "results": [
                {
                    "filename": "script.py",
                    "line_number": 10,
                    "issue_severity": "HIGH",
                    "test_id": "B101",
                    "issue_text": "Use of eval()",
                    "issue_cwe": {"id": 95},
                }
            ]
        }
        with open(os.path.join(tmp_output_dir, "bandit-results.json"), "w") as f:
            json.dump(bandit_data, f)
        changed = frozenset(["script.py"])
        findings = _mod.parse_security_findings(tmp_output_dir, changed)
        # No findings because only Semgrep is parsed, not Bandit
        assert len(findings) == 0


class TestParseTestResults:
    """Tests for parse_test_results with real parser module."""

    def test_jest_results_parsed(self, tmp_output_dir):
        _write_jest_results(tmp_output_dir, success=True)
        result = _mod.parse_test_results(tmp_output_dir)
        assert result is not None
        assert result["passed"] == 50
        assert result["failed"] == 0

    def test_jest_failures_included(self, tmp_output_dir):
        _write_jest_results(
            tmp_output_dir,
            success=False,
            failures=[
                {
                    "name": "tests/auth.test.js",
                    "status": "failed",
                    "failureMessages": ["Expected true, got false"],
                    "location": "tests/auth.test.js:42",
                }
            ],
        )
        result = _mod.parse_test_results(tmp_output_dir)
        assert result is not None
        assert result["failed"] == 2
        assert len(result["failures"]) == 1

    def test_no_results_returns_none(self, tmp_output_dir):
        result = _mod.parse_test_results(tmp_output_dir)
        assert result is None


# =============================================================================
# Path normalization and filtering tests
# =============================================================================


class TestFileInChangeset:
    """Tests for _file_in_changeset matching logic."""

    def test_direct_match(self):
        assert _mod._file_in_changeset("src/app.js", frozenset(["src/app.js"])) is True

    def test_no_match(self):
        assert (
            _mod._file_in_changeset("src/other.js", frozenset(["src/app.js"])) is False
        )

    def test_absolute_to_relative_suffix_match(self):
        assert (
            _mod._file_in_changeset(
                "/project/src/app.js", frozenset(["src/app.js"])
            )
            is True
        )

    def test_empty_changeset_includes_all(self):
        assert _mod._file_in_changeset("anything.js", frozenset()) is True

    def test_empty_filepath(self):
        assert _mod._file_in_changeset("", frozenset(["src/app.js"])) is True


# =============================================================================
# Tool config loader tests
# =============================================================================


class TestLoadToolConfig:
    """Tests for load_tool_config — loading and validating tool-config.json."""

    def test_valid_config_loaded(self, tmp_output_dir):
        config = {
            "eslint": {"cmd": "npx eslint --format json --output-file {output_file} {files}"},
            "jest": {"cmd": "npx jest --json --outputFile={output_file}"},
        }
        path = os.path.join(tmp_output_dir, "tool-config.json")
        with open(path, "w") as f:
            json.dump(config, f)
        result = _mod.load_tool_config(path)
        assert result == {
            "eslint": "npx eslint --format json --output-file {output_file} {files}",
            "jest": "npx jest --json --outputFile={output_file}",
        }

    def test_unknown_tool_skipped(self, tmp_output_dir):
        config = {
            "eslint": {"cmd": "npx eslint {files}"},
            "unknown_tool": {"cmd": "run-something"},
        }
        path = os.path.join(tmp_output_dir, "tool-config.json")
        with open(path, "w") as f:
            json.dump(config, f)
        result = _mod.load_tool_config(path)
        assert "eslint" in result
        assert "unknown_tool" not in result

    def test_missing_cmd_skipped(self, tmp_output_dir):
        config = {
            "eslint": {"version": "8.0"},  # no cmd key
        }
        path = os.path.join(tmp_output_dir, "tool-config.json")
        with open(path, "w") as f:
            json.dump(config, f)
        result = _mod.load_tool_config(path)
        assert result == {}

    def test_null_entry_skipped(self, tmp_output_dir):
        config = {
            "eslint": {"cmd": "npx eslint {files}"},
            "phpcs": None,
        }
        path = os.path.join(tmp_output_dir, "tool-config.json")
        with open(path, "w") as f:
            json.dump(config, f)
        result = _mod.load_tool_config(path)
        assert "eslint" in result
        assert "phpcs" not in result

    def test_empty_cmd_skipped(self, tmp_output_dir):
        config = {"eslint": {"cmd": "  "}}
        path = os.path.join(tmp_output_dir, "tool-config.json")
        with open(path, "w") as f:
            json.dump(config, f)
        result = _mod.load_tool_config(path)
        assert result == {}

    def test_empty_config_returns_empty(self, tmp_output_dir):
        path = os.path.join(tmp_output_dir, "tool-config.json")
        with open(path, "w") as f:
            json.dump({}, f)
        result = _mod.load_tool_config(path)
        assert result == {}

    def test_all_seven_tools_accepted(self, tmp_output_dir):
        config = {
            "eslint": {"cmd": "a"},
            "phpcs": {"cmd": "b"},
            "semgrep": {"cmd": "c"},
            "jest": {"cmd": "d"},
            "jest_coverage": {"cmd": "e"},
            "phpunit": {"cmd": "f"},
            "phpunit_coverage": {"cmd": "g"},
        }
        path = os.path.join(tmp_output_dir, "tool-config.json")
        with open(path, "w") as f:
            json.dump(config, f)
        result = _mod.load_tool_config(path)
        assert len(result) == 7


# =============================================================================
# Config-driven tool runner tests
# =============================================================================


class TestRunConfiguredTool:
    """Tests for run_configured_tool — template substitution and execution."""

    @patch("subprocess.run")
    def test_substitutes_output_file(self, mock_run, tmp_output_dir):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        # Create the expected output file to simulate tool success
        expected = os.path.join(tmp_output_dir, "eslint-results.json")
        with open(expected, "w") as f:
            f.write('{"results": []}')

        ok, err = _mod.run_configured_tool(
            "eslint",
            "npx eslint --output-file {output_file} {files}",
            tmp_output_dir,
            ["src/app.js"],
            timeout=30,
        )
        assert ok is True
        assert err == ""
        # Verify the command had the substituted path
        cmd_str = mock_run.call_args[0][0]
        assert expected in cmd_str

    @patch("subprocess.run")
    def test_substitutes_output_dir(self, mock_run, tmp_output_dir):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        expected = os.path.join(tmp_output_dir, "jest-coverage-summary.json")
        with open(expected, "w") as f:
            f.write('{"total": {}}')

        ok, err = _mod.run_configured_tool(
            "jest_coverage",
            "npx jest --coverageDirectory={output_dir}",
            tmp_output_dir,
            [],
            timeout=60,
        )
        assert ok is True
        cmd_str = mock_run.call_args[0][0]
        assert tmp_output_dir in cmd_str

    @patch("subprocess.run")
    def test_files_are_shell_quoted(self, mock_run, tmp_output_dir):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        expected = os.path.join(tmp_output_dir, "eslint-results.json")
        with open(expected, "w") as f:
            f.write("[]")

        _mod.run_configured_tool(
            "eslint",
            "eslint {files}",
            tmp_output_dir,
            ["src/my file.js", "src/app.js"],
            timeout=30,
        )
        cmd_str = mock_run.call_args[0][0]
        # File with space should be quoted
        assert "'src/my file.js'" in cmd_str or '"src/my file.js"' in cmd_str

    @patch("subprocess.run")
    def test_no_output_file_means_failure(self, mock_run, tmp_output_dir):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        # Don't create the output file

        ok, err = _mod.run_configured_tool(
            "eslint",
            "eslint {files}",
            tmp_output_dir,
            ["src/app.js"],
            timeout=30,
        )
        assert ok is False
        assert "no output file" in err

    @patch("subprocess.run")
    def test_timeout_returns_failure(self, mock_run, tmp_output_dir):
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(["cmd"], 30)

        ok, err = _mod.run_configured_tool(
            "eslint", "eslint {files}", tmp_output_dir, ["src/app.js"], timeout=30,
        )
        assert ok is False
        assert "timed out" in err

    @patch("subprocess.run")
    def test_os_error_returns_failure(self, mock_run, tmp_output_dir):
        mock_run.side_effect = OSError("No such file")

        ok, err = _mod.run_configured_tool(
            "eslint", "eslint {files}", tmp_output_dir, ["src/app.js"], timeout=30,
        )
        assert ok is False
        assert "eslint" in err


# =============================================================================
# Output schema tests — config-driven
# =============================================================================


class TestOutputSchema:
    """Tests for ground-truth-summary.json output format."""

    def test_no_config_produces_all_not_configured(self, tmp_output_dir):
        summary = _mod.collect_ground_truth(["src/app.js"], tmp_output_dir, tool_config={})
        assert summary["tools_run"] == []
        assert summary["tools_failed"] == []
        assert set(summary["tools_not_configured"]) == _mod.KNOWN_TOOLS
        assert summary["findings"] == []

    def test_empty_changed_files_produces_valid_schema(self, tmp_output_dir):
        summary = _mod.collect_ground_truth([], tmp_output_dir, tool_config={})
        assert "tools_run" in summary
        assert "tools_failed" in summary
        assert "tools_not_configured" in summary
        assert "findings" in summary

    def test_summary_written_to_file(self, tmp_output_dir):
        _mod.collect_ground_truth([], tmp_output_dir, tool_config={})
        summary_path = os.path.join(tmp_output_dir, "ground-truth-summary.json")
        assert os.path.exists(summary_path)
        with open(summary_path) as f:
            data = json.load(f)
        assert "tools_run" in data


# =============================================================================
# Integration tests — config-driven pipeline
# =============================================================================


class TestCollectGroundTruthIntegration:
    """Integration tests for collect_ground_truth with mocked tool execution."""

    @patch.object(_mod, "run_configured_tool")
    def test_stale_output_files_not_parsed(self, mock_run, tmp_output_dir):
        """Stale result files from a previous run should not be parsed."""
        # Simulate a previous run that left eslint results on disk
        _write_eslint_results(tmp_output_dir)
        assert os.path.exists(os.path.join(tmp_output_dir, "eslint-results.json"))

        # Current run has no tools configured
        mock_run.return_value = (True, "")
        summary = _mod.collect_ground_truth(
            ["src/app.js"], tmp_output_dir, tool_config={}
        )
        # Stale eslint results should NOT appear in findings
        assert summary["findings"] == [], (
            f"Stale eslint findings leaked into current run: {summary['findings']}"
        )

    @patch.object(_mod, "run_configured_tool")
    def test_configured_tool_runs_and_findings_collected(
        self, mock_run, tmp_output_dir
    ):
        def side_effect(tool, cmd, out_dir, files, timeout):
            _write_eslint_results(out_dir)
            return True, ""

        mock_run.side_effect = side_effect
        config = {"eslint": "npx eslint --format json --output-file {output_file} {files}"}

        summary = _mod.collect_ground_truth(
            ["src/app.js"], tmp_output_dir, tool_config=config
        )
        assert "eslint" in summary["tools_run"]
        assert len(summary["findings"]) >= 1
        assert summary["findings"][0]["tool"] == "eslint"

    @patch.object(_mod, "run_configured_tool")
    def test_multiple_tools(self, mock_run, tmp_output_dir):
        def side_effect(tool, cmd, out_dir, files, timeout):
            if tool == "phpcs":
                _write_phpcs_results(out_dir)
            elif tool == "semgrep":
                _write_semgrep_results(out_dir)
            return True, ""

        mock_run.side_effect = side_effect
        config = {
            "phpcs": "phpcs {files}",
            "semgrep": "semgrep {files}",
        }

        summary = _mod.collect_ground_truth(
            ["src/handler.php"], tmp_output_dir, tool_config=config
        )
        assert "phpcs" in summary["tools_run"]
        assert "semgrep" in summary["tools_run"]
        categories = {f["category"] for f in summary["findings"]}
        assert "lint" in categories
        assert "security" in categories

    @patch.object(_mod, "run_configured_tool")
    def test_test_results_included(self, mock_run, tmp_output_dir):
        def side_effect(tool, cmd, out_dir, files, timeout):
            _write_jest_results(out_dir, success=False, failures=[
                {
                    "name": "tests/auth.test.js",
                    "status": "failed",
                    "failureMessages": ["Assertion failed"],
                }
            ])
            return True, ""

        mock_run.side_effect = side_effect
        config = {"jest": "npx jest --json --outputFile={output_file}"}

        summary = _mod.collect_ground_truth(
            ["src/app.js"], tmp_output_dir, tool_config=config
        )
        assert "jest" in summary["tools_run"]
        assert "test_results" in summary
        assert summary["test_results"]["failed"] == 2

    @patch.object(_mod, "run_configured_tool")
    def test_failed_tool_in_tools_failed(self, mock_run, tmp_output_dir):
        mock_run.return_value = (False, "eslint: command not found")
        config = {"eslint": "eslint {files}"}

        summary = _mod.collect_ground_truth(
            ["src/app.js"], tmp_output_dir, tool_config=config
        )
        assert "eslint" in summary["tools_failed"]
        assert "eslint" not in summary["tools_run"]
        assert summary["findings"] == []

    @patch.object(_mod, "run_configured_tool")
    def test_unconfigured_tools_listed(self, mock_run, tmp_output_dir):
        mock_run.return_value = (True, "")
        # Only configure eslint — everything else should be not_configured
        config = {"eslint": "eslint {files}"}
        # Write mock results so eslint "succeeds"
        _write_eslint_results(tmp_output_dir)

        summary = _mod.collect_ground_truth(
            ["src/app.js"], tmp_output_dir, tool_config=config
        )
        assert "eslint" not in summary["tools_not_configured"]
        assert "phpcs" in summary["tools_not_configured"]
        assert "semgrep" in summary["tools_not_configured"]
        assert "jest" in summary["tools_not_configured"]

    @patch.object(_mod, "run_configured_tool")
    def test_tools_run_concurrently(self, mock_run, tmp_output_dir):
        """Multiple tools should run in parallel, not sequentially."""
        import threading
        import time

        active_threads = []
        lock = threading.Lock()
        max_concurrent = [0]

        def side_effect(tool, cmd, out_dir, files, timeout):
            with lock:
                active_threads.append(threading.current_thread().name)
                current = len(active_threads)
                if current > max_concurrent[0]:
                    max_concurrent[0] = current
            time.sleep(0.1)  # Simulate work
            with lock:
                active_threads.remove(threading.current_thread().name)
            # Write mock output files so tools "succeed"
            if tool == "eslint":
                _write_eslint_results(out_dir)
            return True, ""

        mock_run.side_effect = side_effect
        config = {
            "eslint": "eslint {files}",
            "phpcs": "phpcs {files}",
            "semgrep": "semgrep {files}",
        }

        _mod.collect_ground_truth(["src/app.js"], tmp_output_dir, tool_config=config)
        # With 3 tools and 0.1s sleep, sequential would never exceed 1 concurrent
        assert max_concurrent[0] >= 2, f"Expected concurrent execution, got max {max_concurrent[0]} threads"

    @patch.object(_mod, "run_configured_tool")
    def test_jest_with_coverage_produces_both_outputs(self, mock_run, tmp_output_dir):
        """A single jest run with --coverage should populate both test_results and coverage."""
        def side_effect(tool, cmd, out_dir, files, timeout):
            _write_jest_results(out_dir, success=True)
            # Also write coverage (simulating --coverage flag in the same run)
            data = {
                "total": {
                    "lines": {"pct": 85.5},
                    "branches": {"pct": 72.0},
                    "functions": {"pct": 90.0},
                    "statements": {"pct": 85.0},
                }
            }
            with open(os.path.join(out_dir, "jest-coverage-summary.json"), "w") as f:
                json.dump(data, f)
            return True, ""

        mock_run.side_effect = side_effect
        config = {"jest": "npx jest --json --outputFile={output_file} --coverage --coverageDirectory={output_dir}"}

        summary = _mod.collect_ground_truth(["src/app.js"], tmp_output_dir, tool_config=config)
        assert "jest" in summary["tools_run"]
        assert "test_results" in summary
        assert "coverage" in summary

    @patch.object(_mod, "run_configured_tool")
    def test_coverage_included_when_configured(self, mock_run, tmp_output_dir):
        def side_effect(tool, cmd, out_dir, files, timeout):
            if tool == "jest_coverage":
                # Write mock Jest coverage summary
                data = {
                    "total": {
                        "lines": {"pct": 85.5},
                        "branches": {"pct": 72.0},
                        "functions": {"pct": 90.0},
                        "statements": {"pct": 85.0},
                    }
                }
                with open(os.path.join(out_dir, "jest-coverage-summary.json"), "w") as f:
                    json.dump(data, f)
            return True, ""

        mock_run.side_effect = side_effect
        config = {"jest_coverage": "npx jest --coverage"}

        summary = _mod.collect_ground_truth(
            ["src/app.js"], tmp_output_dir, tool_config=config
        )
        assert "jest_coverage" in summary["tools_run"]
        assert "coverage" in summary
        assert summary["coverage"]["overall_line"] > 0

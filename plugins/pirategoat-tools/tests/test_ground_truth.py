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
# File classification tests
# =============================================================================


class TestClassifyChangedFiles:
    """Tests for classify_changed_files."""

    def test_php_files_classified(self):
        result = _mod.classify_changed_files(["src/handler.php", "src/utils.php"])
        assert result["php"] == ["src/handler.php", "src/utils.php"]
        assert result["js"] == []

    def test_js_files_classified(self):
        result = _mod.classify_changed_files(
            ["src/app.js", "src/utils.ts", "src/component.tsx"]
        )
        assert len(result["js"]) == 3
        assert result["php"] == []

    def test_mixed_files(self):
        files = ["src/app.js", "src/handler.php", "README.md"]
        result = _mod.classify_changed_files(files)
        assert result["php"] == ["src/handler.php"]
        assert result["js"] == ["src/app.js"]
        assert len(result["all"]) == 3

    def test_production_code_detected(self):
        result = _mod.classify_changed_files(["src/app.js", "tests/app.test.js"])
        assert result["has_production_code"] is True

    def test_test_only_no_production(self):
        result = _mod.classify_changed_files(
            ["tests/app.test.js", "tests/handler.spec.php"]
        )
        assert result["has_production_code"] is False

    def test_empty_files(self):
        result = _mod.classify_changed_files([])
        assert result["php"] == []
        assert result["js"] == []
        assert result["all"] == []
        assert result["has_production_code"] is False


# =============================================================================
# Test file detection tests
# =============================================================================


class TestIsTestFile:
    """Tests for is_test_file."""

    def test_jest_test(self):
        assert _mod.is_test_file("src/app.test.js") is True

    def test_spec_file(self):
        assert _mod.is_test_file("src/handler.spec.php") is True

    def test_tests_directory(self):
        assert _mod.is_test_file("tests/unit/auth.php") is True

    def test_test_directory(self):
        assert _mod.is_test_file("test/helpers.js") is True

    def test_dunder_tests(self):
        assert _mod.is_test_file("src/__tests__/app.js") is True

    def test_production_file(self):
        assert _mod.is_test_file("src/handler.php") is False

    def test_production_with_test_in_name(self):
        # "testimonial.php" should not be detected as test
        # but our heuristic catches "test." — this is a known limitation
        # The function checks for patterns, not full words
        assert _mod.is_test_file("src/testimonial-widget.php") is False


# =============================================================================
# Tool detection tests (mocked)
# =============================================================================


class TestDetectTools:
    """Tests for tool detection functions."""

    @patch("shutil.which", return_value="/usr/bin/npx")
    def test_eslint_detected_with_config(self, mock_which, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".eslintrc.json").write_text("{}")
        assert _mod.detect_eslint() is True

    @patch("shutil.which", return_value="/usr/bin/npx")
    def test_eslint_detected_via_package_json(self, mock_which, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "package.json").write_text(
            json.dumps({"devDependencies": {"eslint": "^8.0.0"}})
        )
        assert _mod.detect_eslint() is True

    @patch("shutil.which", return_value=None)
    def test_eslint_not_detected_without_npx(self, mock_which, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".eslintrc.json").write_text("{}")
        assert _mod.detect_eslint() is False

    @patch("shutil.which", return_value="/usr/bin/phpcs")
    def test_phpcs_detected(self, mock_which):
        assert _mod.detect_phpcs() is True

    @patch("shutil.which", return_value=None)
    def test_phpcs_not_detected(self, mock_which):
        assert _mod.detect_phpcs() is False

    @patch("shutil.which", return_value="/usr/bin/semgrep")
    def test_semgrep_detected(self, mock_which):
        assert _mod.detect_semgrep() is True

    @patch("shutil.which", return_value=None)
    def test_semgrep_not_detected(self, mock_which):
        assert _mod.detect_semgrep() is False


# =============================================================================
# Tool execution tests (mocked subprocess)
# =============================================================================


class TestRunTool:
    """Tests for run_tool helper."""

    @patch("subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="output", stderr=""
        )
        ok, stdout, stderr = _mod.run_tool(["echo", "hi"], 30, "test")
        assert ok is True
        assert stdout == "output"

    @patch("subprocess.run", side_effect=TimeoutError)
    def test_timeout(self, mock_run):
        # subprocess.TimeoutExpired needs args
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(["cmd"], 30)
        ok, stdout, stderr = _mod.run_tool(["slow-cmd"], 30, "SlowTool")
        assert ok is False
        assert "timed out" in stderr

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_command_not_found(self, mock_run):
        ok, stdout, stderr = _mod.run_tool(["nonexistent"], 30, "Missing")
        assert ok is False
        assert "not found" in stderr


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
# Output schema tests
# =============================================================================


class TestOutputSchema:
    """Tests for ground-truth-summary.json output format."""

    def test_empty_run_produces_valid_schema(self, tmp_output_dir):
        summary = _mod.collect_ground_truth([], tmp_output_dir)
        assert "tools_run" in summary
        assert "tools_skipped" in summary
        assert "tools_unavailable" in summary
        assert "findings" in summary
        assert isinstance(summary["tools_run"], list)
        assert isinstance(summary["findings"], list)

    @patch.object(_mod, "detect_tools", return_value={
        "eslint": False, "phpcs": False, "semgrep": False,
        "jest": False, "phpunit": False,
    })
    def test_no_tools_available(self, mock_detect, tmp_output_dir):
        summary = _mod.collect_ground_truth(
            ["src/app.js"], tmp_output_dir
        )
        assert summary["tools_run"] == []
        assert "eslint" in summary["tools_unavailable"]

    def test_summary_written_to_file(self, tmp_output_dir):
        _mod.collect_ground_truth([], tmp_output_dir)
        summary_path = os.path.join(tmp_output_dir, "ground-truth-summary.json")
        assert os.path.exists(summary_path)
        with open(summary_path) as f:
            data = json.load(f)
        assert "tools_run" in data


# =============================================================================
# Integration test — full pipeline with mocked tool execution
# =============================================================================


class TestCollectGroundTruthIntegration:
    """Integration tests for collect_ground_truth with mocked tools."""

    @patch.object(_mod, "detect_tools", return_value={
        "eslint": True, "phpcs": False, "semgrep": False,
        "jest": False, "phpunit": False,
    })
    @patch.object(_mod, "run_eslint")
    def test_eslint_findings_in_summary(
        self, mock_eslint, mock_detect, tmp_output_dir
    ):
        # Mock eslint to write results file and return path
        def write_results(files, out_dir, timeout):
            _write_eslint_results(out_dir)
            return os.path.join(out_dir, "eslint-results.json")

        mock_eslint.side_effect = write_results

        summary = _mod.collect_ground_truth(
            ["src/app.js"], tmp_output_dir
        )
        assert "eslint" in summary["tools_run"]
        assert len(summary["findings"]) >= 1
        assert summary["findings"][0]["tool"] == "eslint"

    @patch.object(_mod, "detect_tools", return_value={
        "eslint": False, "phpcs": True, "semgrep": True,
        "jest": False, "phpunit": False,
    })
    @patch.object(_mod, "run_phpcs")
    @patch.object(_mod, "run_semgrep")
    def test_multiple_tools(
        self, mock_semgrep, mock_phpcs, mock_detect, tmp_output_dir
    ):
        def write_phpcs(files, out_dir, timeout):
            _write_phpcs_results(out_dir)
            return os.path.join(out_dir, "phpcs-results.json")

        def write_semgrep(files, out_dir, timeout):
            _write_semgrep_results(out_dir)
            return os.path.join(out_dir, "semgrep-results.json")

        mock_phpcs.side_effect = write_phpcs
        mock_semgrep.side_effect = write_semgrep

        summary = _mod.collect_ground_truth(
            ["src/handler.php"], tmp_output_dir
        )
        assert "phpcs" in summary["tools_run"]
        assert "semgrep" in summary["tools_run"]
        # Should have lint + security findings
        categories = {f["category"] for f in summary["findings"]}
        assert "lint" in categories
        assert "security" in categories

    @patch.object(_mod, "detect_tools", return_value={
        "eslint": False, "phpcs": False, "semgrep": False,
        "jest": True, "phpunit": False,
    })
    @patch.object(_mod, "run_jest")
    def test_test_results_included(
        self, mock_jest, mock_detect, tmp_output_dir
    ):
        def write_jest(out_dir, timeout):
            _write_jest_results(out_dir, success=False, failures=[
                {
                    "name": "tests/auth.test.js",
                    "status": "failed",
                    "failureMessages": ["Assertion failed"],
                }
            ])
            return os.path.join(out_dir, "jest-results.json")

        mock_jest.side_effect = write_jest

        summary = _mod.collect_ground_truth(
            ["src/app.js"], tmp_output_dir
        )
        assert "jest" in summary["tools_run"]
        assert "test_results" in summary
        assert summary["test_results"]["failed"] == 2

    @patch.object(_mod, "detect_tools", return_value={
        "eslint": False, "phpcs": False, "semgrep": False,
        "jest": True, "phpunit": False,
    })
    def test_tests_skipped_for_test_only_changes(
        self, mock_detect, tmp_output_dir
    ):
        """Test runners don't execute when only test files changed."""
        summary = _mod.collect_ground_truth(
            ["tests/app.test.js", "tests/handler.spec.js"],
            tmp_output_dir,
        )
        # Jest should not be in tools_run because has_production_code is False
        assert "jest" not in summary["tools_run"]
        assert "test_results" not in summary

    @patch.object(_mod, "detect_tools", return_value={
        "eslint": True, "phpcs": False, "semgrep": False,
        "jest": False, "phpunit": False,
    })
    @patch.object(_mod, "run_eslint", return_value=None)
    def test_tool_failure_handled_gracefully(
        self, mock_eslint, mock_detect, tmp_output_dir
    ):
        """Tool that fails to produce output is moved to skipped."""
        summary = _mod.collect_ground_truth(
            ["src/app.js"], tmp_output_dir
        )
        assert "eslint" in summary["tools_skipped"]
        assert summary["findings"] == []

"""
Tests for bootstrap-reviewer.py — deterministic, no model calls.

Tests both by importing functions directly and by running via subprocess.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup: allow importing from scripts/
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
BOOTSTRAP_SCRIPT = SCRIPTS_DIR / "bootstrap-reviewer.py"

sys.path.insert(0, str(SCRIPTS_DIR))

# Import functions under test
# The module name has a hyphen, so use importlib
import importlib

_spec = importlib.util.spec_from_file_location("bootstrap_reviewer", str(BOOTSTRAP_SCRIPT))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

derive_reviewer_name = _mod.derive_reviewer_name
extract_protocol_sections = _mod.extract_protocol_sections
extract_pr_number = _mod.extract_pr_number
extract_output_dir = _mod.extract_output_dir
extract_status = _mod.extract_status
build_output = _mod.build_output
build_error_output = _mod.build_error_output
AGENT_CONFIG = _mod.AGENT_CONFIG
REVIEWER_PROTOCOL_SKIP_SECTIONS = _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS

# All 11 reviewer agents (from AGENT_CONFIG)
ALL_AGENTS = sorted(AGENT_CONFIG.keys())
TEST_AGENTS = ["php-tests-reviewer", "js-tests-reviewer", "e2e-tests-reviewer"]


# =============================================================================
# Unit Tests — direct import
# =============================================================================


class TestDeriveReviewerName:
    """Name derivation for all agents and edge cases."""

    @pytest.mark.parametrize(
        "agent_name,expected",
        [
            ("security-reviewer", "security"),
            ("pr-reviewer", "pr"),
            ("performance-reviewer", "performance"),
            ("architecture-reviewer", "architecture"),
            ("wp-architecture-reviewer", "wp-architecture"),
            ("php-tests-reviewer", "php-tests"),
            ("js-tests-reviewer", "js-tests"),
            ("e2e-tests-reviewer", "e2e-tests"),
            ("patterns-reviewer", "patterns"),
            ("history-insights-reviewer", "history-insights"),
            ("tests-mutation-reviewer", "tests-mutation"),
            ("dead-code-reviewer", "dead-code"),
        ],
    )
    def test_all_agents(self, agent_name, expected):
        assert derive_reviewer_name(agent_name) == expected

    def test_no_suffix(self):
        """Agent name without -reviewer suffix is returned as-is."""
        assert derive_reviewer_name("custom-agent") == "custom-agent"

    def test_empty_string(self):
        assert derive_reviewer_name("") == ""

    def test_just_reviewer(self):
        """'reviewer' has no '-reviewer' suffix, so returned as-is."""
        assert derive_reviewer_name("reviewer") == "reviewer"


class TestExtractProtocolSections:
    """Skip-list extraction on synthetic markdown."""

    SAMPLE_PROTOCOL = """\
# Shared Reviewer Protocol

## Step 0: Locate Plugin Root

Setup instructions that should be skipped.

## Scope Discovery

More setup to skip.

### Subsection of Scope

This subsection should also be skipped.

## RULE: Reviewing vs Exploring

Important rule that should be included.

## Output Directory

Output dir instructions to skip.

## ReviewOutputBuilder API

API docs to skip.

## File-Based Output

File output instructions to skip.

## Project-Specific Knowledge

Should be included.

## Ground Truth Data Loading

Should be included.

## New Future Section

This section was added later and should be included automatically.
"""

    def test_skipped_sections_absent(self):
        result = extract_protocol_sections(
            self.SAMPLE_PROTOCOL, REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert "## Step 0" not in result
        assert "## Scope Discovery" not in result
        assert "Subsection of Scope" not in result
        assert "## Output Directory" not in result
        assert "## ReviewOutputBuilder API" not in result
        assert "## File-Based Output" not in result

    def test_non_skipped_sections_present(self):
        result = extract_protocol_sections(
            self.SAMPLE_PROTOCOL, REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert "## RULE: Reviewing vs Exploring" in result
        assert "Important rule that should be included" in result
        assert "## Project-Specific Knowledge" in result
        assert "## Ground Truth Data Loading" in result

    def test_new_section_auto_included(self):
        """New sections added to protocol are included by default (skip-list resilience)."""
        result = extract_protocol_sections(
            self.SAMPLE_PROTOCOL, REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert "## New Future Section" in result
        assert "added later and should be included automatically" in result

    def test_code_fences_not_parsed_as_headings(self):
        """Code fences with # characters should not be parsed as headings."""
        content = """\
# Title

## Included Section

```bash
# This is a comment, not a heading
## Also a comment
echo "hello"
```

After the code fence.

## Step 0: Setup

Should be skipped.
"""
        result = extract_protocol_sections(content, ["## Step 0"])
        assert "# This is a comment" in result
        assert "## Also a comment" in result
        assert 'echo "hello"' in result
        assert "After the code fence." in result
        assert "## Step 0" not in result

    def test_level1_title_stripped(self):
        result = extract_protocol_sections(
            self.SAMPLE_PROTOCOL, REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert "# Shared Reviewer Protocol" not in result


class TestExtractFields:
    """PR number, output dir, status parsing from scope output."""

    SCOPE_OUTPUT = """\
STATUS: OK
RANGE: main..HEAD
BASE_REF: main
OUTPUT_DIR: /tmp/pr-review-42
PR_NUMBER: 42
"""

    def test_extract_pr_number(self):
        assert extract_pr_number(self.SCOPE_OUTPUT) == "42"

    def test_extract_pr_number_missing(self):
        assert extract_pr_number("STATUS: OK\nRANGE: main..HEAD") is None

    def test_extract_output_dir(self):
        assert extract_output_dir(self.SCOPE_OUTPUT) == "/tmp/pr-review-42"

    def test_extract_output_dir_missing(self):
        assert extract_output_dir("STATUS: OK") is None

    def test_extract_status_ok(self):
        assert extract_status(self.SCOPE_OUTPUT) == "OK"

    def test_extract_status_no_domain_files(self):
        assert extract_status("STATUS: NO_DOMAIN_FILES") == "NO_DOMAIN_FILES"

    def test_extract_status_error(self):
        assert extract_status("STATUS: ERROR") == "ERROR"

    def test_extract_status_missing(self):
        assert extract_status("nothing here") is None


class TestBuildOutput:
    """Output structure with known inputs."""

    def setup_method(self):
        self.output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/plugin/root",
            status="OK",
            review_rules="Rule content here.",
            domain_rules=None,
            scope_output="STATUS: OK\nfiles listed here",
            exploration_scope=None,
            output_dir="/tmp/pr-review-99",
            pr_number="99",
            reviewer_name="security",
        )

    def test_header(self):
        assert "=== BOOTSTRAP: security-reviewer ===" in self.output

    def test_plugin_root(self):
        assert "PLUGIN_ROOT: /fake/plugin/root" in self.output

    def test_status(self):
        assert "STATUS: OK" in self.output

    def test_section_markers(self):
        assert "--- Section 1: REVIEW RULES" in self.output
        assert "--- Section 2: REVIEW CONTENT" in self.output
        assert "--- Section 3: OUTPUT INSTRUCTIONS" in self.output

    def test_review_rules(self):
        assert "=== REVIEW RULES ===" in self.output
        assert "Rule content here." in self.output

    def test_no_domain_rules_when_none(self):
        assert "=== DOMAIN RULES ===" not in self.output

    def test_domain_rules_when_provided(self):
        output = build_output(
            agent_name="php-tests-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules",
            domain_rules="Test-specific rules here.",
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="php-tests",
        )
        assert "=== DOMAIN RULES ===" in output
        assert "Test-specific rules here." in output

    def test_scope_output(self):
        assert "=== REVIEW SCOPE ===" in self.output
        assert "files listed here" in self.output

    def test_no_exploration_scope_when_none(self):
        assert "=== EXPLORATION SCOPE ===" not in self.output

    def test_exploration_scope_when_provided(self):
        output = build_output(
            agent_name="patterns-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope="Exploration files here.",
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="patterns",
        )
        assert "=== EXPLORATION SCOPE ===" in output
        assert "Exploration files here." in output

    def test_output_instructions(self):
        assert "OUTPUT_DIR: /tmp/pr-review-99" in self.output
        assert "REVIEWER_NAME: security" in self.output
        assert "/tmp/pr-review-99/security-review.json" in self.output
        assert "/tmp/pr-review-99/security-review.md" in self.output

    def test_builder_snippet(self):
        assert "ReviewOutputBuilder" in self.output
        assert 'builder = ReviewOutputBuilder(pr_id=99, reviewer="security")' in self.output

    def test_return_signal_format(self):
        assert "STATUS: FINISHED" in self.output
        assert "VERDICT:" in self.output
        assert "COUNTS:" in self.output


class TestBuildErrorOutput:
    """Error output format."""

    def test_error_structure(self):
        result = build_error_output("security-reviewer", "Something went wrong")
        assert "=== BOOTSTRAP: security-reviewer ===" in result
        assert "STATUS: ERROR" in result
        assert "ERROR: Something went wrong" in result
        assert "ACTION: Report this error" in result

    def test_error_with_plugin_root(self):
        result = build_error_output("pr-reviewer", "Bad", "/some/root")
        assert "PLUGIN_ROOT: /some/root" in result

    def test_error_default_plugin_root(self):
        result = build_error_output("pr-reviewer", "Bad")
        assert "PLUGIN_ROOT: UNKNOWN" in result


# =============================================================================
# Integration Tests — subprocess
# =============================================================================


def run_bootstrap(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run bootstrap-reviewer.py via subprocess."""
    cmd = [sys.executable, str(BOOTSTRAP_SCRIPT)] + list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        cwd=str(PLUGIN_ROOT),
    )


class TestOutputStructure:
    """All 11 agents produce correct section markers."""

    @pytest.mark.parametrize("agent_name", ALL_AGENTS)
    def test_section_markers(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        stdout = result.stdout

        assert f"=== BOOTSTRAP: {agent_name} ===" in stdout
        assert "--- Section 1: REVIEW RULES" in stdout
        assert "=== REVIEW RULES ===" in stdout
        assert "--- Section 2: REVIEW CONTENT" in stdout
        assert "--- Section 3: OUTPUT INSTRUCTIONS" in stdout
        assert "=== OUTPUT INSTRUCTIONS ===" in stdout


class TestContentIdentity:
    """REVIEW RULES content identical across all agents."""

    @pytest.fixture(scope="class")
    def agent_outputs(self):
        """Run bootstrap for all agents and cache outputs."""
        outputs = {}
        for agent in ALL_AGENTS:
            result = run_bootstrap("--agent", agent, "--output-dir", "/tmp/test-bootstrap")
            outputs[agent] = result.stdout
        return outputs

    def _extract_section(self, text: str, start_marker: str, *end_markers: str) -> str:
        """Extract text between start_marker and the earliest end_marker found."""
        start = text.find(start_marker)
        if start == -1:
            return ""
        # Find the earliest end marker after the start
        end = len(text)
        for marker in end_markers:
            pos = text.find(marker, start + len(start_marker))
            if pos != -1 and pos < end:
                end = pos
        return text[start:end].strip()

    def test_review_rules_identical_across_agents(self, agent_outputs):
        """REVIEW RULES section should be identical for all agents.

        End at whichever comes first: DOMAIN RULES (test agents) or Section 2.
        """
        rules = {}
        for agent, output in agent_outputs.items():
            section = self._extract_section(
                output, "=== REVIEW RULES ===",
                "=== DOMAIN RULES ===", "--- Section 2:",
            )
            rules[agent] = section

        reference = rules[ALL_AGENTS[0]]
        for agent in ALL_AGENTS[1:]:
            assert rules[agent] == reference, (
                f"REVIEW RULES differ between {ALL_AGENTS[0]} and {agent}"
            )

    def test_domain_rules_identical_across_test_agents(self, agent_outputs):
        """DOMAIN RULES should be identical for all test agents."""
        rules = {}
        for agent in TEST_AGENTS:
            output = agent_outputs[agent]
            section = self._extract_section(output, "=== DOMAIN RULES ===", "--- Section 2:")
            rules[agent] = section



        reference = rules[TEST_AGENTS[0]]
        assert reference, "Expected DOMAIN RULES for test agents"
        for agent in TEST_AGENTS[1:]:
            assert rules[agent] == reference, (
                f"DOMAIN RULES differ between {TEST_AGENTS[0]} and {agent}"
            )


class TestConditionalSections:
    """Sections appear only for relevant agents."""

    @pytest.mark.parametrize("agent_name", TEST_AGENTS)
    def test_domain_rules_present_for_test_agents(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert "=== DOMAIN RULES ===" in result.stdout

    @pytest.mark.parametrize(
        "agent_name",
        [a for a in ALL_AGENTS if a not in TEST_AGENTS],
    )
    def test_domain_rules_absent_for_non_test_agents(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert "=== DOMAIN RULES ===" not in result.stdout

    def test_exploration_scope_for_patterns_reviewer(self):
        result = run_bootstrap("--agent", "patterns-reviewer", "--output-dir", "/tmp/test-bootstrap")
        assert "=== EXPLORATION SCOPE ===" in result.stdout

    @pytest.mark.parametrize(
        "agent_name",
        [a for a in ALL_AGENTS if a != "patterns-reviewer"],
    )
    def test_no_exploration_scope_for_other_agents(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert "=== EXPLORATION SCOPE ===" not in result.stdout

    def test_mutation_reviewer_no_scope(self):
        result = run_bootstrap("--agent", "tests-mutation-reviewer", "--output-dir", "/tmp/test-bootstrap")
        assert "No scope discovery" in result.stdout


class TestPersonalization:
    """Agent-specific values are correctly interpolated."""

    @pytest.mark.parametrize(
        "agent_name,reviewer_name",
        [(a, derive_reviewer_name(a)) for a in ALL_AGENTS],
    )
    def test_reviewer_name(self, agent_name, reviewer_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-personalization")
        assert f"REVIEWER_NAME: {reviewer_name}" in result.stdout

    @pytest.mark.parametrize(
        "agent_name,reviewer_name",
        [(a, derive_reviewer_name(a)) for a in ALL_AGENTS],
    )
    def test_output_file_paths(self, agent_name, reviewer_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-personalization")
        assert f"/tmp/test-personalization/{reviewer_name}-review.json" in result.stdout
        assert f"/tmp/test-personalization/{reviewer_name}-review.md" in result.stdout

    @pytest.mark.parametrize(
        "agent_name,reviewer_name",
        [(a, derive_reviewer_name(a)) for a in ALL_AGENTS],
    )
    def test_builder_snippet(self, agent_name, reviewer_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-personalization")
        assert f'reviewer="{reviewer_name}"' in result.stdout


class TestErrorHandling:
    """Error cases: unknown agent, valid agents."""

    def test_unknown_agent_exits_1(self):
        result = run_bootstrap("--agent", "nonexistent-reviewer", "--output-dir", "/tmp/test-err")
        assert result.returncode == 1
        assert "STATUS: ERROR" in result.stdout
        assert "Unknown agent" in result.stdout

    def test_unknown_agent_structured_error(self):
        result = run_bootstrap("--agent", "fake", "--output-dir", "/tmp/test-err")
        assert "=== BOOTSTRAP: fake ===" in result.stdout
        assert "ACTION: Report this error" in result.stdout

    @pytest.mark.parametrize("agent_name", ALL_AGENTS)
    def test_valid_agents_exit_0(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert result.returncode == 0, f"{agent_name} exited with {result.returncode}: {result.stderr}"

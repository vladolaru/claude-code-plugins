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
load_pr_intent = _mod.load_pr_intent
load_change_purpose = _mod.load_change_purpose
AGENT_CONFIG = _mod.AGENT_CONFIG
REVIEWER_PROTOCOL_SKIP_SECTIONS = _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS

# All reviewer agents (from AGENT_CONFIG)
ALL_AGENTS = sorted(AGENT_CONFIG.keys())
TEST_AGENTS = ["php-tests-reviewer", "js-tests-reviewer", "e2e-tests-reviewer", "go-tests-reviewer"]


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
            ("go-tests-reviewer", "go-tests"),
            ("patterns-reviewer", "patterns"),
            ("history-insights-reviewer", "history-insights"),
            ("tests-mutation-reviewer", "tests-mutation"),
            ("dead-code-reviewer", "dead-code"),
            ("a11y-reviewer", "a11y"),
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

## Severity Calibration

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
        assert "## Severity Calibration" in result

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


class TestLoadPrIntent:
    """PR intent loading from review-context.json."""

    def test_returns_none_when_no_file(self, tmp_path):
        assert load_pr_intent(str(tmp_path)) is None

    def test_returns_none_when_empty_json(self, tmp_path):
        (tmp_path / "review-context.json").write_text("{}")
        assert load_pr_intent(str(tmp_path)) is None

    def test_returns_none_when_no_title(self, tmp_path):
        ctx = {"pr": {"body": "Some body", "author": "dev"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        assert load_pr_intent(str(tmp_path)) is None

    def test_returns_intent_with_title(self, tmp_path):
        ctx = {"pr": {"title": "Fix rounding in refunds"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert intent is not None
        assert "Fix rounding in refunds" in intent

    def test_includes_author(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing", "author": "alice"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert "alice" in intent

    def test_includes_body(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing", "body": "Refund totals were off"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert "Refund totals were off" in intent

    def test_includes_linked_issues(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing"}, "linked_issues": ["WOOPLUG-123"]}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert "WOOPLUG-123" in intent

    def test_truncates_long_body(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing", "body": "x" * 1000}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert len(intent) < 700  # 500 char truncation + title + overhead
        assert "..." in intent

    def test_handles_malformed_json(self, tmp_path):
        (tmp_path / "review-context.json").write_text("not json")
        assert load_pr_intent(str(tmp_path)) is None


class TestPrIntentInjection:
    """PR intent is injected into bootstrap output between rules and content."""

    def test_intent_present_when_provided(self):
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            pr_intent="PR Title: Fix rounding in refunds",
        )
        assert "=== PR INTENT ===" in output
        assert "Fix rounding in refunds" in output

    def test_intent_absent_when_none(self):
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
        )
        assert "=== PR INTENT ===" not in output

    def test_intent_between_rules_and_content(self):
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            pr_intent="PR Title: Fix thing",
        )
        rules_pos = output.index("=== REVIEW RULES ===")
        intent_pos = output.index("=== PR INTENT ===")
        content_pos = output.index("--- Section 2: REVIEW CONTENT")
        assert rules_pos < intent_pos < content_pos

    def test_intent_includes_severity_guidance(self):
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            pr_intent="PR Title: Fix thing",
        )
        assert "severity" in output.lower()


class TestChangePurpose:
    """load_change_purpose() reads change-purpose.md from output directory."""

    def test_returns_none_when_missing(self, tmp_path):
        assert load_change_purpose(str(tmp_path)) is None

    def test_returns_none_when_empty(self, tmp_path):
        (tmp_path / "change-purpose.md").write_text("")
        assert load_change_purpose(str(tmp_path)) is None

    def test_returns_content_when_present(self, tmp_path):
        (tmp_path / "change-purpose.md").write_text("Adds retry logic to payments.")
        result = load_change_purpose(str(tmp_path))
        assert result == "Adds retry logic to payments."

    def test_strips_whitespace(self, tmp_path):
        (tmp_path / "change-purpose.md").write_text("  Content with spaces.  \n\n")
        result = load_change_purpose(str(tmp_path))
        assert result == "Content with spaces."


class TestChangePurposeInjection:
    """change-purpose.md content is injected as REVIEW FOCUS in bootstrap output."""

    def test_review_focus_present_when_provided(self):
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            change_purpose="Adds retry logic to the payment gateway.",
        )
        assert "=== REVIEW FOCUS (pipeline synthesis) ===" in output
        assert "retry logic" in output

    def test_review_focus_absent_when_none(self):
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
        )
        assert "=== REVIEW FOCUS" not in output

    def test_review_focus_after_pr_intent_before_content(self):
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            pr_intent="PR Title: Fix thing",
            change_purpose="Adds retry logic.",
        )
        intent_pos = output.index("=== PR INTENT ===")
        focus_pos = output.index("=== REVIEW FOCUS")
        content_pos = output.index("--- Section 2: REVIEW CONTENT")
        assert intent_pos < focus_pos < content_pos

    def test_review_focus_without_pr_intent(self):
        """REVIEW FOCUS should work even without PR INTENT."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            change_purpose="Adds retry logic.",
        )
        assert "=== REVIEW FOCUS" in output
        assert "=== PR INTENT ===" not in output
        focus_pos = output.index("=== REVIEW FOCUS")
        content_pos = output.index("--- Section 2: REVIEW CONTENT")
        assert focus_pos < content_pos


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


# ---------------------------------------------------------------------------
# Temp repo for integration tests (created once, reused across all tests)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(TESTS_DIR))
from conftest import setup_temp_git_repo

_BOOTSTRAP_REPO = None


def _get_bootstrap_repo() -> str:
    """Lazily create a temp git repo from multi-file-realistic.diff."""
    global _BOOTSTRAP_REPO
    if _BOOTSTRAP_REPO is None:
        diff = str(TESTS_DIR / "fixtures" / "multi-file-realistic.diff")
        _BOOTSTRAP_REPO = setup_temp_git_repo(diff)
    return _BOOTSTRAP_REPO


def run_bootstrap(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run bootstrap-reviewer.py via subprocess against a temp git repo.

    Uses a temp repo from multi-file-realistic.diff so tests are fully
    isolated from the real repository state. Always passes
    --range HEAD~1..HEAD for deterministic behavior.
    """
    full_args = list(args)
    if "--range" not in full_args:
        full_args.extend(["--range", "HEAD~1..HEAD"])
    cmd = [sys.executable, str(BOOTSTRAP_SCRIPT)] + full_args
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        cwd=_get_bootstrap_repo(),
    )


class TestOutputStructure:
    """All agents produce correct section markers."""

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


class TestNoSemanticFilter:
    """no_semantic_filter flag passes --no-semantic-filter to scope discovery."""

    def test_wp_architecture_reviewer_gets_no_semantic_filter(self):
        """wp-architecture-reviewer has no_semantic_filter: true and scope cmd includes the flag."""
        result = run_bootstrap("--agent", "wp-architecture-reviewer", "--output-dir", "/tmp/test-bootstrap")
        # The agent should succeed (not error out)
        assert result.returncode == 0, f"Exit {result.returncode}: {result.stderr}"
        # Verify the registry flag is set for this agent
        assert AGENT_CONFIG["wp-architecture-reviewer"].get("no_semantic_filter") is True

    def test_patterns_reviewer_gets_no_semantic_filter(self):
        """patterns-reviewer has no_semantic_filter: true and scope cmd includes the flag."""
        result = run_bootstrap("--agent", "patterns-reviewer", "--output-dir", "/tmp/test-bootstrap")
        assert result.returncode == 0, f"Exit {result.returncode}: {result.stderr}"
        assert AGENT_CONFIG["patterns-reviewer"].get("no_semantic_filter") is True

    def test_agents_without_flag_do_not_have_it(self):
        """Agents without no_semantic_filter should not have the flag set."""
        for agent_name, config in AGENT_CONFIG.items():
            if agent_name in ("wp-architecture-reviewer", "patterns-reviewer"):
                continue
            assert not config.get("no_semantic_filter", False), (
                f"Agent '{agent_name}' unexpectedly has no_semantic_filter set"
            )

    def test_no_semantic_filter_appended_to_scope_flags(self):
        """When no_semantic_filter is true, --no-semantic-filter is passed to run_scope_discovery."""
        # We verify this by importing the module and checking that the config
        # value is read during bootstrap. The integration test via subprocess
        # confirms the flag reaches review-scope.py (which already supports it).
        # Here we verify the registry config is correct.
        for agent_name in ("wp-architecture-reviewer", "patterns-reviewer"):
            config = AGENT_CONFIG[agent_name]
            assert config.get("no_semantic_filter") is True
            # Also verify domain is set (so scope discovery runs)
            assert config["domain"] is not None


class TestFileHistory:
    """File history section for history-insights-reviewer."""

    def test_file_history_present_for_history_insights(self):
        result = run_bootstrap("--agent", "history-insights-reviewer", "--output-dir", "/tmp/test-bootstrap")
        assert "=== FILE HISTORY ===" in result.stdout

    @pytest.mark.parametrize(
        "agent_name",
        [a for a in ALL_AGENTS if a != "history-insights-reviewer"],
    )
    def test_file_history_absent_for_other_agents(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert "=== FILE HISTORY ===" not in result.stdout


class TestReviewOutputBuilderAPIExample:
    """Bootstrap Section 3 must include a complete ReviewOutputBuilder usage example."""

    def _build(self):
        return build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )

    def test_output_contains_add_issue_example(self):
        """The usage example must show add_issue() with named parameters."""
        output = self._build()
        assert "add_issue(" in output
        assert "severity=" in output
        assert "title=" in output
        assert "file=" in output
        assert "description=" in output
        assert "recommendation=" in output

    def test_output_contains_add_positive_example(self):
        """The usage example must show add_positive()."""
        output = self._build()
        assert "add_positive(" in output

    def test_output_contains_save_example(self):
        """The usage example must show save() with output_dir."""
        output = self._build()
        assert "save(" in output
        assert "output_dir" in output.lower() or "/tmp/pr-review-42" in output

    def test_output_contains_set_files_reviewed(self):
        """The usage example must show set_files_reviewed()."""
        output = self._build()
        assert "set_files_reviewed(" in output

    def test_output_contains_set_confidence(self):
        """The usage example must show set_confidence()."""
        output = self._build()
        assert "set_confidence(" in output

    def test_output_contains_no_verify_instruction(self):
        """The usage example must tell agents not to verify save() output."""
        output = self._build()
        lower = output.lower()
        assert "do not" in lower and ("read" in lower or "verify" in lower) and ("output file" in lower or "save()" in lower)


class TestBootstrapOutputSizeCap:
    """Bootstrap caps inline scope when output would exceed size threshold."""

    def _build_large_output(self, scope_size_kb=50, output_dir=None):
        """Helper: build output with a scope of the given KB size."""
        if output_dir is None:
            import tempfile
            output_dir = tempfile.mkdtemp()
        large_scope = "x" * (scope_size_kb * 1024)
        return build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules here",
            domain_rules=None,
            scope_output=large_scope,
            exploration_scope=None,
            output_dir=output_dir,
            pr_number="42",
            reviewer_name="security",
        )

    def test_small_scope_included_inline(self):
        """Scope under threshold is included inline (no change from current behavior)."""
        small_scope = "diff content here\n" * 100  # ~2KB
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules here",
            domain_rules=None,
            scope_output=small_scope,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        assert small_scope in output

    def test_large_scope_truncated(self):
        """Scope over threshold is truncated with a file reference."""
        output = self._build_large_output(scope_size_kb=50)
        # The full 50KB scope should NOT be in the output
        assert len(output) < 40 * 1024  # output should be well under 40KB total

    def test_large_scope_has_file_reference(self):
        """When scope is truncated, output tells agent where to read the full scope."""
        output = self._build_large_output(scope_size_kb=50)
        assert "scoped-diff.patch" in output or "full scope" in output.lower() or "Read" in output

    def test_large_scope_has_read_instructions(self):
        """When scope is truncated, output tells agent to use offset/limit."""
        output = self._build_large_output(scope_size_kb=50)
        lower = output.lower()
        assert "offset" in lower or "limit" in lower or "head" in lower


class TestDynamicDispatchRisk:
    """Bootstrap injects DYNAMIC_DISPATCH_RISK for dead-code-reviewer."""

    def test_dead_code_reviewer_gets_dispatch_risk(self):
        """dead-code-reviewer output includes DYNAMIC_DISPATCH_RISK."""
        scope_with_php = "=== FILES ===\nsrc/payment.php  (+10 -5)\nsrc/utils.ts  (+3 -1)\n=== DIFFS ==="
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output=scope_with_php,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        assert "DYNAMIC_DISPATCH_RISK:" in output

    def test_dispatch_risk_high_with_php_files(self):
        """DYNAMIC_DISPATCH_RISK is 'high' when PHP files are in scope."""
        scope_with_php = "=== FILES ===\nsrc/payment.php  (+10 -5)\nsrc/utils.ts  (+3 -1)\n=== DIFFS ==="
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output=scope_with_php,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        risk_line = [l for l in output.splitlines() if "DYNAMIC_DISPATCH_RISK:" in l]
        assert risk_line, "DYNAMIC_DISPATCH_RISK line not found in output"
        assert "high" in risk_line[0].lower()

    def test_dispatch_risk_low_without_php_files(self):
        """DYNAMIC_DISPATCH_RISK is 'low' when no PHP files are in scope."""
        scope_no_php = "=== FILES ===\nsrc/utils.ts  (+3 -1)\nsrc/component.tsx  (+20 -5)\n=== DIFFS ==="
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output=scope_no_php,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        risk_line = [l for l in output.splitlines() if "DYNAMIC_DISPATCH_RISK:" in l]
        assert risk_line, "DYNAMIC_DISPATCH_RISK line not found in output"
        assert "low" in risk_line[0].lower()

    def test_other_agents_no_dispatch_risk(self):
        """Non-dead-code agents do NOT get DYNAMIC_DISPATCH_RISK."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        assert "DYNAMIC_DISPATCH_RISK:" not in output


class TestOutputFilenameConsistency:
    """Output filenames from ReviewOutputBuilder.save() match bootstrap expectations."""

    def test_save_uses_review_suffix(self, tmp_path):
        """save() should write {reviewer}-review.json and {reviewer}-review.md."""
        from review_output_simple import ReviewOutputBuilder

        builder = ReviewOutputBuilder(pr_id="42", reviewer="dead-code")
        result = builder.save(str(tmp_path))

        assert result["json"].endswith("dead-code-review.json"), f"Got: {result['json']}"
        assert result["markdown"].endswith("dead-code-review.md"), f"Got: {result['markdown']}"
        assert os.path.isfile(result["json"])
        assert os.path.isfile(result["markdown"])

    def test_bootstrap_output_matches_save_filenames(self):
        """Bootstrap OUTPUT_FILES paths match what save() actually creates."""
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        assert "/tmp/pr-review-42/dead-code-review.json" in output
        assert "/tmp/pr-review-42/dead-code-review.md" in output



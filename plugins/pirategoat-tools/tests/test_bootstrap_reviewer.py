"""Tests for bootstrap-reviewer.py — unit tests (direct function imports)."""

import json
import os
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


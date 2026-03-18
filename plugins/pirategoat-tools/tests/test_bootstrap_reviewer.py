"""Tests for bootstrap-reviewer.py — unit tests (direct function imports)."""

import json
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

extract_protocol_sections = _mod.extract_protocol_sections
build_output = _mod.build_output
build_error_output = _mod.build_error_output
load_pr_intent = _mod.load_pr_intent
load_change_purpose = _mod.load_change_purpose
REVIEWER_PROTOCOL_SKIP_SECTIONS = _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS


# =============================================================================
# Unit Tests — direct import
# =============================================================================


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

class TestBuildErrorOutput:
    """Error output format."""

    def test_error_structure(self):
        result = build_error_output("security-reviewer", "Something went wrong")
        assert "=== BOOTSTRAP: security-reviewer ===" in result
        assert "STATUS: ERROR" in result
        assert "ERROR: Something went wrong" in result
        assert "ACTION: Report this error" in result



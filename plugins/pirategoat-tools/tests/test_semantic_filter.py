"""Tests for semantic-filter.py — content-level noise removal from diffs."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

# Import the module
spec = importlib.util.spec_from_file_location(
    "semantic_filter", str(SCRIPTS_DIR / "semantic-filter.py")
)
semantic_filter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(semantic_filter)


class TestSuppressionDirectiveExemptions:
    """Suppression directives must NOT be filtered — they carry intent."""

    @pytest.mark.parametrize("comment", [
        "// eslint-disable-next-line no-explicit-any",
        "// eslint-disable-next-line @typescript-eslint/no-unused-vars",
        "// eslint-disable no-console",
        "// @ts-ignore",
        "// @ts-expect-error",
        "// @ts-nocheck",
        "// noinspection JSUnusedLocalSymbols",
        "# noqa: E501",
        "# noqa",
        "# type: ignore",
        "# type: ignore[assignment]",
        "# nosec",
        "# nosec B105",
        "# pylint: disable=too-many-arguments",
        "# nolint",
    ])
    def test_suppression_directives_not_filtered(self, comment):
        line = f"+{comment}"
        assert semantic_filter.should_filter(line) is False, (
            f"Suppression directive should NOT be filtered: {comment}"
        )

    @pytest.mark.parametrize("comment", [
        "// phpcs:ignore WordPress.Security.NonceVerification",
        "// phpcs:ignore WordPress.DB.DirectDatabaseQuery",
        "// phpcs:disable WordPress.NamingConventions",
        "// phpcs:enable WordPress.NamingConventions",
    ])
    def test_phpcs_directives_not_filtered(self, comment):
        line = f"+{comment}"
        assert semantic_filter.should_filter(line) is False

    @pytest.mark.parametrize("comment", [
        "// @deprecated since 3.0",
        "// @deprecated Use newFunction() instead",
        "# Deprecated: will be removed in v4",
    ])
    def test_deprecation_comments_not_filtered(self, comment):
        line = f"+{comment}"
        assert semantic_filter.should_filter(line) is False

    @pytest.mark.parametrize("comment", [
        "// TODO: fix this before merge",
        "// FIXME: race condition here",
        "// HACK: temporary workaround for #1234",
        "// XXX: known issue",
        "# TODO: add error handling",
    ])
    def test_todo_fixme_comments_not_filtered(self, comment):
        line = f"+{comment}"
        assert semantic_filter.should_filter(line) is False


class TestRegularCommentsStillFiltered:
    """Regular comments should still be filtered as before."""

    @pytest.mark.parametrize("comment", [
        "// Set the name",
        "// Initialize the variable",
        "# This is a helper function",
        "// Returns the value",
    ])
    def test_regular_comments_filtered(self, comment):
        line = f"+{comment}"
        assert semantic_filter.should_filter(line) is True


class TestFilterDiffIntegration:
    """Integration test: filter_diff preserves suppression directives in full diffs."""

    def test_preserves_eslint_disable_in_diff(self):
        diff = (
            "--- a/src/app.ts\n"
            "+++ b/src/app.ts\n"
            "@@ -1,5 +1,8 @@\n"
            " import React from 'react';\n"
            "+// eslint-disable-next-line @typescript-eslint/no-explicit-any\n"
            "+const data: any = fetchData();\n"
            "+// This is just a regular comment\n"
        )
        filtered, stats = semantic_filter.filter_diff(diff)
        assert "eslint-disable" in filtered
        assert "const data" in filtered
        assert "regular comment" not in filtered

    def test_preserves_phpcs_ignore_in_diff(self):
        diff = (
            "--- a/src/Plugin.php\n"
            "+++ b/src/Plugin.php\n"
            "@@ -10,3 +10,5 @@\n"
            " class Plugin {\n"
            "+    // phpcs:ignore WordPress.Security.NonceVerification\n"
            "+    $value = $_POST['key'];\n"
        )
        filtered, stats = semantic_filter.filter_diff(diff)
        assert "phpcs:ignore" in filtered
        assert "$_POST" in filtered


class TestBasicFiltering:
    """Verify existing filtering behavior is preserved."""

    def test_filters_blank_lines(self):
        assert semantic_filter.should_filter("+") is True
        assert semantic_filter.should_filter("+   ") is True

    def test_filters_docblock_start(self):
        assert semantic_filter.should_filter("+/**") is True

    def test_filters_docblock_content(self):
        assert semantic_filter.should_filter("+ * Some docblock text") is True

    def test_filters_docblock_end(self):
        assert semantic_filter.should_filter("+ */") is True

    def test_filters_formatting_braces(self):
        assert semantic_filter.should_filter("+{") is True
        assert semantic_filter.should_filter("+}") is True

    def test_keeps_diff_headers(self):
        assert semantic_filter.should_filter("--- a/file.py") is False
        assert semantic_filter.should_filter("+++ b/file.py") is False
        assert semantic_filter.should_filter("@@ -1,5 +1,5 @@") is False

    def test_keeps_code_lines(self):
        assert semantic_filter.should_filter("+return $result;") is False
        assert semantic_filter.should_filter("+function foo() {") is False

    def test_keeps_context_lines(self):
        assert semantic_filter.should_filter(" unchanged code") is False

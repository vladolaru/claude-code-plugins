"""Tests for review/agent/diff_noise_filter.py — content-level noise removal from diffs."""

import importlib.util
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent.parent  # agent/ -> review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

# Import the module
spec = importlib.util.spec_from_file_location(
    "semantic_filter", str(SCRIPTS_DIR / "review" / "agent" / "diff_noise_filter.py")
)
semantic_filter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(semantic_filter)


class TestSuppressionDirectiveExemptions:
    """Suppression directives must NOT be filtered — they carry intent.

    One row per alternation family in `_STRUCTURED_COMMENT_PATTERNS`.
    Spelling variants within a family (`# noqa` vs `# noqa: E501`, or the
    `@deprecated` / `deprecated:` pair) are not separately pinned — they
    share a regex branch, so one representative per family is enough.
    """

    @pytest.mark.parametrize("comment", [
        pytest.param("// eslint-disable-next-line no-explicit-any", id="eslint_disable"),
        pytest.param("// @ts-ignore", id="ts_directive"),
        pytest.param("// noinspection JSUnusedLocalSymbols", id="noinspection"),
        pytest.param("# noqa", id="noqa"),
        pytest.param("# type: ignore", id="type_ignore"),
        pytest.param("# nosec", id="nosec"),
        pytest.param("# pylint: disable=too-many-arguments", id="pylint"),
        pytest.param("# nolint", id="nolint"),
        pytest.param("// phpcs:ignore WordPress.Security.NonceVerification", id="phpcs"),
        pytest.param("// @deprecated since 3.0", id="deprecated"),
        pytest.param("// TODO: fix this before merge", id="todo_fixme"),
    ])
    def test_suppression_directive_exempt(self, comment):
        line = f"+{comment}"
        assert semantic_filter.should_filter(line) is False, (
            f"Suppression directive should NOT be filtered: {comment}"
        )


class TestInlineCommentsPreserved:
    """Inline comments are preserved — they carry developer intent
    (translators directives, API contracts, ordering constraints)."""

    @pytest.mark.parametrize("comment", [
        pytest.param("// Set the name", id="plain_comment"),
        pytest.param("// translators: %s: formatted currency amount", id="translators_directive"),
    ])
    def test_inline_comments_not_filtered(self, comment):
        line = f"+{comment}"
        assert semantic_filter.should_filter(line) is False


class TestFilterDiffIntegration:
    """Integration test: filter_diff preserves suppression directives in full diffs."""

    def test_preserves_suppression_directives_in_diff(self):
        diff = (
            "--- a/src/Plugin.php\n"
            "+++ b/src/Plugin.php\n"
            "@@ -1,5 +1,8 @@\n"
            " class Plugin {\n"
            "+    // eslint-disable-next-line @typescript-eslint/no-explicit-any\n"
            "+    // phpcs:ignore WordPress.Security.NonceVerification\n"
            "+    $value = $_POST['key'];\n"
            "+    // This is just a regular comment\n"
        )
        filtered, stats = semantic_filter.filter_diff(diff)
        assert "eslint-disable" in filtered
        assert "phpcs:ignore" in filtered
        assert "$_POST" in filtered
        assert "regular comment" in filtered  # inline comments are preserved


class TestBasicFiltering:
    """Verify existing filtering behavior is preserved.

    One row per guard branch in `should_filter`; where multiple concrete
    lines exercise the same branch (for example "+" vs "+   " for the
    blank-line check, or the two code-line examples for the default
    keep-everything-else path), only one representative is kept.
    """

    @pytest.mark.parametrize("line, filtered", [
        pytest.param("+", True, id="blank_line"),
        pytest.param("+/**", True, id="docblock_start"),
        pytest.param("+ * Some docblock text", True, id="docblock_content"),
        pytest.param("+ */", True, id="docblock_end"),
        pytest.param("+{", True, id="formatting_open_brace"),
        pytest.param("+}", True, id="formatting_close_brace"),
        pytest.param("--- a/file.py", False, id="diff_header_removed_file"),
        pytest.param("+++ b/file.py", False, id="diff_header_added_file"),
        pytest.param("@@ -1,5 +1,5 @@", False, id="diff_header_hunk"),
        pytest.param("+return $result;", False, id="code_line_kept"),
        pytest.param(" unchanged code", False, id="context_line_kept"),
    ])
    def test_filtering(self, line, filtered):
        assert semantic_filter.should_filter(line) is filtered

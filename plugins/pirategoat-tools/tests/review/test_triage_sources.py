"""Tests for the prose sources keyword triage reads, cleaned."""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from review import triage_sources as ts  # noqa: E402


class TestHtmlComments:
    def test_single_and_multiline_comments_are_removed(self):
        text = "Keep <!-- drop --> this\n<!--\nmulti\nline\n-->\nand this"
        assert ts.strip_html_comments(text) == "Keep  this\n\nand this"

    def test_unterminated_comment_is_left_alone(self):
        assert ts.strip_html_comments("a <!-- b") == "a <!-- b"


class TestTemplateSubtraction:
    TEMPLATE = (
        "### Changes proposed in this Pull Request:\n"
        "<!-- Describe the changes made -->\n"
        "-   I have reviewed my code for [security best practices](https://x).\n"
        "- [ ] Automatically create a changelog entry from the details below.\n"
        "#### Type\n"
        "-   [ ] Performance - Address performance issues\n"
    )

    def test_template_lines_are_removed_even_when_heading_punctuation_drifts(self):
        body = (
            "### Changes proposed in this Pull Request\n"
            "The dropdown opens the keyboard on touch devices.\n"
            "-   I have reviewed my code for [security best practices](https://x).\n"
            "- [x] Automatically create a changelog entry from the details below.\n"
            "-   [ ] Performance - Address performance issues\n"
        )
        assert ts.subtract_template(body, self.TEMPLATE) == (
            "The dropdown opens the keyboard on touch devices."
        )

    def test_an_edited_template_line_survives(self):
        """Whole-line comparison: a checklist line the author extended is
        the author's sentence, not the template's."""
        body = "- [x] I have reviewed my code for [security best practices](https://x) and added nonce checks.\n"
        assert ts.subtract_template(body, self.TEMPLATE) == body.strip()

    def test_author_prose_under_a_template_heading_survives(self):
        body = "#### Type\nWe rewrote the request handling for auth tokens.\n"
        assert ts.subtract_template(body, self.TEMPLATE) == (
            "We rewrote the request handling for auth tokens."
        )

    def test_no_template_still_strips_html_comments(self):
        body = "Real prose <!-- template comment mentioning password --> here"
        assert ts.subtract_template(body, "") == "Real prose  here"

    def test_normalization_rules(self):
        assert ts.normalize_template_line("### Heading:") == "heading"
        assert ts.normalize_template_line("-   [X] Item.") == "[ ] item"
        assert ts.normalize_template_line("  *  two   words ") == "two words"
        assert ts.normalize_template_line("<!-- gone -->") == ""


class TestFindPrTemplate:
    def test_reads_every_github_location_and_concatenates(self, tmp_path):
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "PULL_REQUEST_TEMPLATE.md").write_text("root template\n")
        (tmp_path / ".github" / "PULL_REQUEST_TEMPLATE").mkdir()
        (tmp_path / ".github" / "PULL_REQUEST_TEMPLATE" / "bug.md").write_text("bug template\n")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "pull_request_template.md").write_text("docs template\n")
        text = ts.find_pr_template(tmp_path)
        assert "root template" in text and "bug template" in text and "docs template" in text

    def test_absent_template_is_empty(self, tmp_path):
        """No template in the checkout, and no checkout at all."""
        assert ts.find_pr_template(tmp_path) == ""
        assert ts.find_pr_template(tmp_path / "missing") == ""

    def test_reads_templates_from_every_supported_multiple_template_directory(self, tmp_path):
        for template_directory, text in (
            (".github/PULL_REQUEST_TEMPLATE", "github directory template\n"),
            ("PULL_REQUEST_TEMPLATE", "root directory template\n"),
            ("docs/PULL_REQUEST_TEMPLATE", "docs directory template\n"),
        ):
            directory = tmp_path / template_directory
            directory.mkdir(parents=True)
            (directory / "bug.md").write_text(text)
        combined = ts.find_pr_template(tmp_path)
        for label in ("github", "root", "docs"):
            assert f"{label} directory template" in combined, label


TRAILER_CASES = [
    pytest.param(
        "fix: keep the keyboard closed\n"
        "The input is a dropdown.\n"
        "\n"
        "Co-Authored-By: Claude <noreply@example.com>\n"
        "Claude-Session: https://example.com/session_1\n"
        "\x00"
        "docs: note the change\n"
        "Refs #53136\n"
        "\x00",
        "fix: keep the keyboard closed\nThe input is a dropdown.\n"
        "docs: note the change",
        id="final-trailer-paragraph-dropped-per-commit",
    ),
    pytest.param(
        "fix: x\nBody.\n\nCo-Authored-By:  Two Spaces <a@b>\nSigned-off-by:\tTab <t@b>\n\x00",
        "fix: x\nBody.",
        id="whitespace-after-the-colon-is-git-s",
    ),
    pytest.param(
        "fix: y\nBody.\n\nRefs WOOPLUG-1, WOOPLUG-2.\nfixes #7\n\x00",
        "fix: y\nBody.",
        id="reference-list-with-a-period-is-a-trailer",
    ),
    pytest.param(
        "feat: add auth\nNote: this changes the login flow.\nAnd more prose.\n\x00",
        "feat: add auth\nNote: this changes the login flow.\nAnd more prose.",
        id="prose-paragraph-with-one-colon-line-kept",
    ),
    # `git interpret-trailers --parse` classifies a lone final `Token: value`
    # line as a trailer; the planner follows git rather than guessing which
    # tokens are prose.
    pytest.param(
        "fix: z\nBody.\n\nNote: token handling changed.\n\x00",
        "fix: z\nBody.",
        id="one-line-final-note-paragraph-is-a-trailer",
    ),
    pytest.param("chore: bump\n\x00", "chore: bump", id="subject-only-commit-kept"),
    pytest.param("fix: x\nRefs #1\n\x00", "fix: x", id="only-trailers-keeps-its-subject"),
    pytest.param(
        "fix: add guard\nDetails.\n\nFixes #123 by enforcing authentication and sanitization.\n\x00",
        "fix: add guard\nDetails.\n\nFixes #123 by enforcing authentication and sanitization.",
        id="reference-line-with-an-explanation-is-prose",
    ),
    pytest.param("", "", id="empty-log"),
]


class TestCommitTrailers:
    @pytest.mark.parametrize("log, expected", TRAILER_CASES)
    def test_strips_trailer_paragraphs(self, log, expected):
        assert ts.strip_commit_trailers(log) == expected

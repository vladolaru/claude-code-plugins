"""Contract tests for the shared Git C-quoted path grammar."""

import pytest

from git_paths import decode_git_c_quoted_path


@pytest.mark.parametrize(
    "quoted,expected",
    [
        (r'"caf\303\251.php"', "café.php"),
        (r'"tab\tname.php"', "tab\tname.php"),
        (r'"quote\"name.php"', 'quote"name.php'),
        (r'"back\\slash.php"', "back\\slash.php"),
    ],
)
def test_decodes_git_c_quoting(quoted, expected):
    assert decode_git_c_quoted_path(quoted) == (expected, True)


def test_preserves_ordinary_path():
    assert decode_git_c_quoted_path("plain.php") == ("plain.php", False)


def test_quote_delimited_literal_without_escapes_is_not_invented_as_git_quoting():
    literal = '"literal.php"'
    assert decode_git_c_quoted_path(literal) == (literal, False)


@pytest.mark.parametrize(
    "malformed",
    ['"unterminated\\', r'"bad\qescape"', r'"short\41"'],
)
def test_malformed_escape_bearing_quoting_fails_closed(malformed):
    assert decode_git_c_quoted_path(malformed) == (None, True)


def test_invalid_utf8_can_be_preserved_for_provenance_identity():
    quoted = r'"bad-\377.md"'
    assert decode_git_c_quoted_path(quoted) == (None, True)
    decoded, was_quoted = decode_git_c_quoted_path(
        quoted, errors="surrogateescape"
    )
    assert decoded == "bad-\udcff.md"
    assert was_quoted is True


def test_rejects_unknown_decode_policy():
    with pytest.raises(ValueError, match="unsupported UTF-8 error policy"):
        decode_git_c_quoted_path("plain.php", errors="replace")

"""Tests for atomic_io — the pipeline's JSON file conventions: the atomic
write every writer shares, the one reader of a JSON object file, and the
output-directory lock."""

import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from review.atomic_io import (
    atomic_write_json, atomic_write_text, collect_json_object, output_dir_lock,
    read_json_object,
)

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


class TestCrashSafety:
    def test_atomic_write_json_replaces_not_truncates(self, tmp_path, monkeypatch):
        """A failed os.replace must leave the pre-existing file untouched —
        never a truncated or partially-written artifact — and must not
        leak its temp file into the directory either."""
        path = tmp_path / "target.json"
        path.write_text('{"original": true}')

        def _boom(*args, **kwargs):
            raise OSError("simulated os.replace failure")

        monkeypatch.setattr(os, "replace", _boom)

        with pytest.raises(OSError):
            atomic_write_json(str(path), {"new": True})

        assert path.read_text() == '{"original": true}'
        # The failed write's temp file must be cleaned up — the target is
        # the ONLY thing left in the directory, not target.json plus an
        # orphaned tmpXXXXXX sibling.
        assert list(tmp_path.iterdir()) == [path]

    def test_successful_write_leaves_no_temp_file(self, tmp_path):
        """A normal write leaves only the target behind — the temp file
        used to stage it is renamed away, not left as a sibling."""
        path = tmp_path / "target.json"

        atomic_write_json(str(path), {"ok": True})

        assert list(tmp_path.iterdir()) == [path]


class TestEncoding:
    def test_ensure_ascii_false(self, tmp_path):
        """Non-ASCII prose (em dashes, curly quotes, etc.) must survive as
        UTF-8 bytes, not \\uXXXX escapes — findings/telemetry files are read
        as UTF-8 by every other writer in the pipeline."""
        path = tmp_path / "target.json"
        payload = {"description": "a bug — and a fix"}
        atomic_write_json(str(path), payload)

        raw = path.read_bytes()
        assert "a bug — and a fix".encode("utf-8") in raw
        assert b"\\u2014" not in raw
        assert json.loads(path.read_text(encoding="utf-8")) == payload


class TestSameDirectoryTempFile:
    def test_temp_file_in_same_directory(self, tmp_path, monkeypatch):
        """The temp file must be created in the target's own directory, not
        a system temp dir — os.replace across filesystems is not atomic."""
        target = tmp_path / "target.json"
        seen_dirs = []
        real_named_temp_file = tempfile.NamedTemporaryFile

        def _spy(*args, **kwargs):
            seen_dirs.append(kwargs.get("dir"))
            return real_named_temp_file(*args, **kwargs)

        monkeypatch.setattr(tempfile, "NamedTemporaryFile", _spy)

        atomic_write_json(str(target), {"ok": True})

        assert seen_dirs == [str(tmp_path)]


class TestOutputDirectoryLock:
    def test_lock_uses_directory_descriptor_without_creating_artifact(self, tmp_path):
        before = list(tmp_path.iterdir())

        with output_dir_lock(str(tmp_path)):
            assert list(tmp_path.iterdir()) == before

        assert list(tmp_path.iterdir()) == before


def _calls_os_replace(source_path):
    """True if the file's AST contains an `os.replace` attribute access.

    An AST check (not a text scan) so mentioning `os.replace` in a
    comment or docstring — like atomic_io.py's own module docstring, or
    critic_adjustments.py's docstring naming the mechanism it delegates
    to — is free. Matches any `os.replace` attribute access, called or
    not, so aliasing it into a variable is caught too. Scoped to this
    codebase's universal `import os` convention: a `from os import
    replace` spelling would not be matched (none exists under scripts/).
    """
    import ast

    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == "replace"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        for node in ast.walk(tree)
    )


class TestReadJsonObject:
    """The one reader of "a JSON file holding an object". The critic's
    snapshot reader, its save channel and the reviewer finalizer each had
    their own, with three wordings for one fault."""

    def test_an_object_is_returned(self, tmp_path):
        path = tmp_path / "a.json"
        path.write_text('{"k": 1}')
        assert read_json_object(path, "thing") == {"k": 1}

    def test_a_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_json_object(tmp_path / "absent.json", "thing")

    @pytest.mark.parametrize("contents, message", [
        pytest.param(b"null", "thing must be a JSON object, got null", id="null"),
        pytest.param(b"[]", "thing must be a JSON object, got list", id="list"),
        pytest.param(b"5", "thing must be a JSON object, got int", id="scalar"),
        pytest.param(b"{oops", "thing is not readable JSON", id="not-json"),
        pytest.param(b"\xff\xfe", "thing is not readable JSON", id="not-utf8"),
        pytest.param(b"[" * 200000, "thing is not readable JSON", id="too-deeply-nested"),
    ])
    def test_every_other_fault_is_one_value_error_naming_the_label(
        self, tmp_path, contents, message
    ):
        path = tmp_path / "a.json"
        path.write_bytes(contents)
        with pytest.raises(ValueError, match=re.escape(message)):
            read_json_object(path, "thing")

    def test_a_directory_is_a_value_error_not_an_os_error(self, tmp_path):
        with pytest.raises(ValueError, match="thing is not readable JSON"):
            read_json_object(tmp_path, "thing")


class TestCollectJsonObject:
    """The save channels' form of the reader: every outcome is a return
    value or one recorded problem, never an exception."""

    def test_an_object_is_returned_with_no_problem(self, tmp_path):
        path = tmp_path / "a.json"
        path.write_text('{"k": 1}')
        problems = []
        assert collect_json_object(path, "--thing", problems) == {"k": 1}
        assert problems == []

    @pytest.mark.parametrize("contents, problem", [
        pytest.param(None, "--thing file not found: {path}", id="missing"),
        pytest.param(b"{oops", "--thing ({path}) is not readable JSON", id="not-json"),
        pytest.param(b"[]", "--thing ({path}) must be a JSON object, got list", id="list"),
    ])
    def test_every_fault_is_one_problem_naming_the_flag_and_path(
        self, tmp_path, contents, problem
    ):
        path = tmp_path / "a.json"
        if contents is not None:
            path.write_bytes(contents)
        problems = []
        assert collect_json_object(path, "--thing", problems) is None
        assert len(problems) == 1
        assert problems[0].startswith(problem.format(path=path))


class TestAtomicWriteText:
    """atomic_write_text shares atomic_write_json's crash-safety contract
    for prose artifacts (e.g. decision-critic-findings.md) that have no
    JSON shape to serialize."""

    def test_writes_the_text_verbatim(self, tmp_path):
        """Crash-safety, no-temp-file-left-behind, and same-directory
        staging are `_atomic_write`'s contract, already pinned once for
        JSON in TestCrashSafety and TestSameDirectoryTempFile above; this
        proves the text writer routes through the same primitive rather
        than re-proving each of its guarantees a second time."""
        path = tmp_path / "target.md"
        atomic_write_text(str(path), "# Decision Critic Findings\n")
        assert path.read_text(encoding="utf-8") == "# Decision Critic Findings\n"


class TestNoStrayAtomicSpellings:
    def test_no_stray_atomic_spellings(self):
        """Pins the consolidation against future drift: no file anywhere
        under scripts/ may call os.replace outside atomic_io.py, except
        agent/output.py's deliberately different staged-nonce protocol.
        AST-matched (not text-matched), so a comment or docstring naming
        `os.replace` cannot trip this — only an actual call can."""
        allowed = {
            SCRIPTS_DIR / "review" / "atomic_io.py",
            SCRIPTS_DIR / "review" / "agent" / "output.py",
        }
        hits = {
            path for path in SCRIPTS_DIR.rglob("*.py") if _calls_os_replace(path)
        }

        stray = hits - allowed
        assert not stray, f"Stray atomic-write spelling(s) found: {sorted(stray)}"
        # Sanity check the scan itself is not vacuous, and that both
        # allowlisted files are genuinely hit (not dead exemptions).
        assert hits == allowed, (
            f"Allowlist entries not actually matched: {sorted(allowed - hits)}"
        )

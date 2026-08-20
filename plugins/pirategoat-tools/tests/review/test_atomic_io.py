"""Tests for atomic_io — the single atomic-JSON-write primitive shared by
every writer in the review pipeline (except agent/output.py's deliberately
different staged-nonce protocol, see atomic_io.py's module docstring)."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from review.atomic_io import atomic_write_json

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


class TestCrashSafety:
    def test_atomic_write_json_replaces_not_truncates(self, tmp_path, monkeypatch):
        """A failed os.replace must leave the pre-existing file untouched —
        never a truncated or partially-written artifact."""
        path = tmp_path / "target.json"
        path.write_text('{"original": true}')

        def _boom(*args, **kwargs):
            raise OSError("simulated os.replace failure")

        monkeypatch.setattr(os, "replace", _boom)

        with pytest.raises(OSError):
            atomic_write_json(str(path), {"new": True})

        assert path.read_text() == '{"original": true}'


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


class TestNoStrayAtomicSpellings:
    def test_no_stray_atomic_spellings(self):
        """Pins the consolidation against future drift: no file under
        scripts/review/ or scripts/analysis/ may reimplement the
        temp-file-then-os.replace pattern outside atomic_io.py, except
        agent/output.py's deliberately different staged-nonce protocol."""
        allowed = {
            SCRIPTS_DIR / "review" / "atomic_io.py",
            SCRIPTS_DIR / "review" / "agent" / "output.py",
        }
        hits = set()
        for root in (SCRIPTS_DIR / "review", SCRIPTS_DIR / "analysis"):
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "NamedTemporaryFile" in text and "os.replace" in text:
                    hits.add(path)

        stray = hits - allowed
        assert not stray, f"Stray atomic-write spelling(s) found: {sorted(stray)}"
        # Sanity check the scan itself is not vacuous.
        assert SCRIPTS_DIR / "review" / "atomic_io.py" in hits

"""Tests for review/reconciliation_notes.py."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "review" / "reconciliation_notes.py"
sys.path.insert(0, str(SCRIPTS_DIR))

from review import run_paths
from review.reconciliation_context import RECONCILIATION_CONTEXT_SCHEMA
from review.reconciliation_notes import add_note
from helpers.review_fixtures import write_reconciliation_context  # noqa: E402


def _write_context(output_dir, *, schema=RECONCILIATION_CONTEXT_SCHEMA, notes=()):
    return write_reconciliation_context(output_dir, {}, schema=schema, notes=notes)


def _context(output_dir):
    return json.loads(
        run_paths.artifact_path(output_dir, "reconciliation_context").read_text()
    )


def test_add_note_appends_with_the_next_id(tmp_path):
    _write_context(tmp_path)
    assert add_note(tmp_path, "security f1 and code f1 look like one concern") == {
        "id": "n1", "note": "security f1 and code f1 look like one concern",
    }
    assert add_note(tmp_path, "  the @since claim is disputed  ")["id"] == "n2"
    assert _context(tmp_path)["orchestrator_notes"] == [
        {"id": "n1", "note": "security f1 and code f1 look like one concern"},
        {"id": "n2", "note": "the @since claim is disputed"},
    ]


def test_add_note_keeps_the_rest_of_the_context(tmp_path):
    path = _write_context(tmp_path)
    data = json.loads(path.read_text())
    data["reviews_by_agent"] = {"security-review": {"findings": []}}
    path.write_text(json.dumps(data))
    add_note(tmp_path, "x")
    assert _context(tmp_path)["reviews_by_agent"] == {"security-review": {"findings": []}}


@pytest.mark.parametrize("notes", [
    pytest.param(..., id="missing"),
    pytest.param([None], id="non-object-entry"),
    pytest.param([{"id": "n2", "note": "claim"}], id="wrong-first-id"),
    pytest.param([{"id": "n1", "note": None}], id="non-text"),
    pytest.param([{"id": "n1", "note": " padded "}], id="unclean-text"),
])
def test_add_note_rejects_malformed_existing_collection_without_writing(tmp_path, notes):
    path = _write_context(tmp_path)
    context = json.loads(path.read_text())
    if notes is ...:
        context.pop("orchestrator_notes")
    else:
        context["orchestrator_notes"] = notes
    path.write_text(json.dumps(context))
    before = path.read_bytes()

    with pytest.raises(ValueError, match="orchestrator_notes"):
        add_note(tmp_path, "A new claim.")

    assert path.read_bytes() == before


@pytest.mark.parametrize("text", ["", "a\x00b", "x" * 4097])
def test_add_note_refuses_unusable_text(tmp_path, text):
    _write_context(tmp_path)
    with pytest.raises(ValueError):
        add_note(tmp_path, text)
    assert _context(tmp_path)["orchestrator_notes"] == []


def test_add_note_refuses_a_foreign_schema(tmp_path):
    _write_context(tmp_path, schema=RECONCILIATION_CONTEXT_SCHEMA - 1)
    with pytest.raises(ValueError, match="schema"):
        add_note(tmp_path, "x")


def test_add_note_refuses_a_missing_context(tmp_path):
    with pytest.raises(ValueError, match="reconciliation-context.json"):
        add_note(tmp_path, "x")


def test_cli_records_and_names_the_note(tmp_path):
    _write_context(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path),
         "--note", "the retry helper is reused by the cron path"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "RECORDED NOTE: n1"
    assert _context(tmp_path)["orchestrator_notes"][0]["id"] == "n1"


def test_cli_rejects_and_writes_nothing(tmp_path):
    _write_context(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path), "--note", " "],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert result.stdout.startswith("REJECTED: ")
    assert _context(tmp_path)["orchestrator_notes"] == []

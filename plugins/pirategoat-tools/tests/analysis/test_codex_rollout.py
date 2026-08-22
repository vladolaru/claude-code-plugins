"""
Tests for analysis/codex_rollout.py — Codex rollout discovery and parsing.

Codex stores each conversation thread as a JSONL rollout under
~/.codex/sessions/YYYY/MM/DD/. These tests use hand-built fixtures rather
than real rollouts so the schema expectations are explicit and readable.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # analysis/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "analysis" / "codex_rollout.py"

_spec = importlib.util.spec_from_file_location("codex_rollout", str(SCRIPT_PATH))
codex_rollout = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass resolves its module via sys.modules during
# class creation, and raises AttributeError on None if the entry is missing.
sys.modules["codex_rollout"] = codex_rollout
_spec.loader.exec_module(codex_rollout)


def _meta_line(timestamp="2026-08-18T16:00:00.000Z", **overrides):
    """Build a session_meta line. Overrides are merged into the payload.

    The timestamp defaults to the earliest one used across these fixtures,
    because line 1 is what scan_thread() takes as a thread's start time.
    """
    payload = {
        "session_id": "sess-1",
        "id": "thread-1",
        "parent_thread_id": None,
        "cwd": "/work/project",
        "originator": "codex-tui",
        "cli_version": "0.147.0",
    }
    payload.update(overrides)
    return json.dumps({"timestamp": timestamp, "type": "session_meta", "payload": payload})


def _subagent_source(role="worker", agent_path="/root/child_1", depth=1):
    return {
        "subagent": {
            "thread_spawn": {
                "parent_thread_id": "thread-root",
                "depth": depth,
                "agent_path": agent_path,
                "agent_nickname": "Fermat the 3rd",
                "agent_role": role,
            }
        }
    }


def test_parses_root_thread_when_source_absent(tmp_path):
    path = tmp_path / "rollout-root.jsonl"
    path.write_text(_meta_line() + "\n")

    meta = codex_rollout.read_thread_meta(path)

    assert meta.thread_id == "thread-1"
    assert meta.cwd == "/work/project"
    assert meta.agent_role == "root"
    assert meta.agent_path == "/root"
    assert meta.depth == 0


def test_parses_subagent_thread(tmp_path):
    path = tmp_path / "rollout-child.jsonl"
    path.write_text(_meta_line(source=_subagent_source()) + "\n")

    meta = codex_rollout.read_thread_meta(path)

    assert meta.agent_role == "worker"
    assert meta.agent_path == "/root/child_1"
    assert meta.depth == 1
    assert meta.parent_thread_id == "thread-root"


def test_tolerates_source_as_bare_string(tmp_path):
    """Roughly 200 of 2069 real August rollouts have a string here, not a dict."""
    path = tmp_path / "rollout-oddsource.jsonl"
    path.write_text(_meta_line(source="subagent") + "\n")

    meta = codex_rollout.read_thread_meta(path)

    assert meta.agent_role == "root"
    assert meta.agent_path == "/root"


def test_returns_none_for_malformed_first_line(tmp_path):
    path = tmp_path / "rollout-broken.jsonl"
    path.write_text("{not json\n")

    assert codex_rollout.read_thread_meta(path) is None


def test_returns_none_when_first_line_is_not_session_meta(tmp_path):
    path = tmp_path / "rollout-nometa.jsonl"
    path.write_text(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")

    assert codex_rollout.read_thread_meta(path) is None

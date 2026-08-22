"""
Tests for analysis/codex_rollout.py — Codex rollout discovery and parsing.

Codex stores each conversation thread as a JSONL rollout under
~/.codex/sessions/YYYY/MM/DD/. These tests use hand-built fixtures rather
than real rollouts so the schema expectations are explicit and readable.
"""

import importlib.util
import json
import os
import sys
import time
from datetime import date
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


STALE_OFFSET = 3600  # comfortably outside the active-rollout window


def _write_rollout(sessions_dir, day: date, name: str, mtime=None, **meta_overrides):
    """Write a rollout, aged so discovery treats it as a finished session.

    A file written just now looks like a live session and is skipped by
    design, so any fixture expected to be discovered must be aged first.
    Pass mtime explicitly to control ordering or to simulate a live file.
    """
    day_dir = sessions_dir / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"rollout-{name}.jsonl"
    path.write_text(_meta_line(**meta_overrides) + "\n")
    stamp = mtime if mtime is not None else time.time() - STALE_OFFSET
    os.utime(path, (stamp, stamp))
    return path


def test_discovers_only_days_inside_the_window(tmp_path):
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today, "recent", id="recent")
    _write_rollout(tmp_path, date(2026, 8, 1), "old", id="old")

    found = codex_rollout.discover_threads(tmp_path, since_days=7, today=today)

    assert [m.thread_id for m in found] == ["recent"]


def test_filters_by_cwd(tmp_path):
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today, "a", id="a", cwd="/work/alpha")
    _write_rollout(tmp_path, today, "b", id="b", cwd="/work/beta")

    found = codex_rollout.discover_threads(tmp_path, since_days=7, today=today, cwd="/work/beta")

    assert [m.thread_id for m in found] == ["b"]


def test_filters_by_agent_role(tmp_path):
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today, "w", id="w", source=_subagent_source(role="worker"))
    _write_rollout(tmp_path, today, "r", id="r", source=_subagent_source(role="code-reviewer"))

    found = codex_rollout.discover_threads(tmp_path, since_days=7, today=today, agent="code-reviewer")

    assert [m.thread_id for m in found] == ["r"]


def test_agent_filter_accepts_comma_separated_list(tmp_path):
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today, "w", id="w", source=_subagent_source(role="worker"))
    _write_rollout(tmp_path, today, "r", id="r", source=_subagent_source(role="code-reviewer"))
    _write_rollout(tmp_path, today, "e", id="e", source=_subagent_source(role="explorer"))

    found = codex_rollout.discover_threads(
        tmp_path, since_days=7, today=today, agent="worker,explorer"
    )

    assert sorted(m.thread_id for m in found) == ["e", "w"]


def test_skips_probably_active_rollouts(tmp_path):
    """A rollout still being written grows mid-read, so its numbers are junk."""
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today, "active", mtime=time.time(), id="active")
    _write_rollout(tmp_path, today, "finished", id="finished")

    found = codex_rollout.discover_threads(tmp_path, since_days=7, today=today)
    assert [m.thread_id for m in found] == ["finished"]

    found_all = codex_rollout.discover_threads(
        tmp_path, since_days=7, today=today, include_active=True
    )
    assert sorted(m.thread_id for m in found_all) == ["active", "finished"]


def test_respects_limit_newest_first(tmp_path):
    today = date(2026, 8, 22)
    base = time.time() - STALE_OFFSET - 1000

    for index, name in enumerate(["oldest", "middle", "newest"]):
        _write_rollout(tmp_path, today, name, mtime=base + index * 100, id=name)

    found = codex_rollout.discover_threads(tmp_path, since_days=7, today=today, limit=2)

    assert [m.thread_id for m in found] == ["newest", "middle"]


def test_missing_sessions_dir_returns_empty(tmp_path):
    found = codex_rollout.discover_threads(tmp_path / "nope", since_days=7, today=date(2026, 8, 22))
    assert found == []

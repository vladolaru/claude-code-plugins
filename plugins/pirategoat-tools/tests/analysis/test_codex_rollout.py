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
from datetime import date, timedelta
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
    assert meta.spawn_parent_thread_id == "thread-root"


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
    _write_rollout(tmp_path, today, "root", id="root")
    _write_rollout(tmp_path, today, "w", id="w", source=_subagent_source(role="worker"))
    _write_rollout(tmp_path, today, "r", id="r", source=_subagent_source(role="code-reviewer"))

    found = codex_rollout.discover_threads(tmp_path, since_days=7, today=today, agent="code-reviewer")

    assert [m.thread_id for m in found] == ["r"]


def test_agent_filter_accepts_comma_separated_list(tmp_path):
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today, "root", id="root")
    _write_rollout(tmp_path, today, "w", id="w", source=_subagent_source(role="worker"))
    _write_rollout(tmp_path, today, "r", id="r", source=_subagent_source(role="code-reviewer"))
    _write_rollout(tmp_path, today, "e", id="e", source=_subagent_source(role="explorer"))

    found = codex_rollout.discover_threads(
        tmp_path, since_days=7, today=today, agent="worker,explorer"
    )

    assert sorted(m.thread_id for m in found) == ["e", "w"]


def test_skips_active_subagents_but_keeps_their_finished_siblings(tmp_path):
    """A rollout still being written grows mid-read, so its numbers are junk."""
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today, "root", id="root")
    _write_rollout(tmp_path, today, "active", mtime=time.time(), id="active",
                   source=_subagent_source(agent_path="/root/a"))
    _write_rollout(tmp_path, today, "finished", id="finished",
                   source=_subagent_source(agent_path="/root/b"))

    stats = codex_rollout.DiscoveryStats()
    found = codex_rollout.discover_threads(tmp_path, since_days=7, today=today, stats=stats)
    assert sorted(m.thread_id for m in found) == ["finished", "root"]
    assert stats.skipped_active == 1

    found_all = codex_rollout.discover_threads(
        tmp_path, since_days=7, today=today, include_active=True
    )
    assert sorted(m.thread_id for m in found_all) == ["active", "finished", "root"]


def test_a_live_root_still_carries_its_finished_subagents(tmp_path):
    """Dropping a running session's root discarded every completed child.

    One real session had its only root written 0.4 minutes earlier, which
    disqualified the session and threw away 242 finished subagents. Membership
    in the window and trustworthiness of a rollout's own numbers are separate
    questions.
    """
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today, "live-root", mtime=time.time(), id="live-root")
    _write_rollout(tmp_path, today, "done-child", id="done-child", source=_subagent_source())

    stats = codex_rollout.DiscoveryStats()
    found = codex_rollout.discover_threads(tmp_path, since_days=7, today=today, stats=stats)

    assert sorted(m.thread_id for m in found) == ["done-child", "live-root"]
    assert stats.active_roots_included == 1
    assert any("still running" in note for note in stats.notes())
    assert next(m for m in found if m.thread_id == "live-root").is_active is True


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


def _item_line(item_type, timestamp="2026-08-18T16:01:30.000Z", **fields):
    item = {"type": item_type, "id": f"id-{item_type}"}
    item.update(fields)
    return json.dumps(
        {"timestamp": timestamp, "type": "event_msg", "payload": {"type": "item_completed", "item": item}}
    )


def _token_line(total, timestamp="2026-08-18T16:01:31.000Z"):
    return json.dumps(
        {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"total_tokens": total, "cached_input_tokens": 10, "input_tokens": 90},
                    "last_token_usage": {"total_tokens": 5},
                },
            },
        }
    )


def _turn_context_line(model="gpt-5.6-terra", timestamp="2026-08-18T16:01:25.000Z"):
    return json.dumps({"timestamp": timestamp, "type": "turn_context", "payload": {"model": model}})


def test_scan_counts_items_and_command_failures(tmp_path):
    path = tmp_path / "rollout-scan.jsonl"
    path.write_text(
        "\n".join(
            [
                _meta_line(),
                _turn_context_line(),
                _item_line("CommandExecution", exit_code=0, duration=1.5, command="ls"),
                _item_line("CommandExecution", exit_code=1, duration=0.5, command="false"),
                _item_line(
                    "FileChange",
                    changes={"/w/a.py": {"type": "update"}, "/w/b.py": {"type": "add"}},
                ),
                _item_line("AgentMessage", content="done"),
                _item_line("ContextCompaction"),
                _token_line(100),
            ]
        )
        + "\n"
    )

    scan = codex_rollout.scan_thread(path)

    assert scan.commands == 2
    assert scan.failed_commands == 1
    assert scan.files_changed == 2
    assert scan.messages == 1
    assert scan.compactions == 1
    assert scan.model == "gpt-5.6-terra"


def test_scan_takes_last_token_count_not_the_sum(tmp_path):
    """total_token_usage is cumulative; summing multiply-counts by ~1900x."""
    path = tmp_path / "rollout-tokens.jsonl"
    path.write_text("\n".join([_meta_line(), _token_line(100), _token_line(250)]) + "\n")

    scan = codex_rollout.scan_thread(path)

    assert scan.total_tokens == 250


def test_scan_computes_duration_from_first_and_last_timestamp(tmp_path):
    """Duration spans line 1 to the last entry, so session_meta sets the start."""
    path = tmp_path / "rollout-duration.jsonl"
    path.write_text(
        "\n".join(
            [
                _meta_line(timestamp="2026-08-18T16:00:00.000Z"),
                _item_line("AgentMessage", timestamp="2026-08-18T16:01:00.000Z", content="start"),
                _item_line("AgentMessage", timestamp="2026-08-18T16:02:30.000Z", content="end"),
            ]
        )
        + "\n"
    )

    scan = codex_rollout.scan_thread(path)

    assert scan.duration_seconds == pytest.approx(150.0, abs=1.0)


def test_scan_counts_malformed_lines_without_raising(tmp_path):
    path = tmp_path / "rollout-malformed.jsonl"
    path.write_text("\n".join([_meta_line(), "{truncated", _item_line("AgentMessage", content="ok")]) + "\n")

    scan = codex_rollout.scan_thread(path)

    assert scan.malformed_lines == 1
    assert scan.messages == 1


def test_scan_ignores_item_type_spelling(tmp_path):
    """The field is item.type; item_type does not exist and must not be relied on."""
    path = tmp_path / "rollout-spelling.jsonl"
    entry = {
        "timestamp": "2026-08-18T16:01:30.000Z",
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": {"item_type": "CommandExecution", "id": "x"}},
    }
    path.write_text("\n".join([_meta_line(), json.dumps(entry)]) + "\n")

    scan = codex_rollout.scan_thread(path)

    assert scan.commands == 0


def test_scan_keeps_items_when_asked(tmp_path):
    path = tmp_path / "rollout-keep.jsonl"
    path.write_text(
        "\n".join([_meta_line(), _item_line("CommandExecution", exit_code=0, duration=1.0, command="ls")]) + "\n"
    )

    scan = codex_rollout.scan_thread(path, keep_items=True)

    assert [item["type"] for item in scan.items] == ["CommandExecution"]
    assert scan.items[0]["command"] == "ls"


def test_scan_holds_no_items_by_default(tmp_path):
    path = tmp_path / "rollout-nokeep.jsonl"
    path.write_text(
        "\n".join([_meta_line(), _item_line("CommandExecution", exit_code=0, duration=1.0, command="ls")]) + "\n"
    )

    assert codex_rollout.scan_thread(path).items == []


def test_parent_agent_path_strips_last_segment():
    assert codex_rollout.parent_agent_path("/root/child_1") == "/root"
    assert codex_rollout.parent_agent_path("/root/child_1/grandchild") == "/root/child_1"


def test_parent_agent_path_of_root_is_none():
    assert codex_rollout.parent_agent_path("/root") is None


def test_build_tree_groups_children_under_parents(tmp_path):
    today = date(2026, 8, 22)

    specs = [
        ("root", None),
        ("child-a", "/root/a"),
        ("child-b", "/root/b"),
        ("grandchild", "/root/a/deep"),
    ]
    for name, agent_path in specs:
        overrides = {"id": name}
        if agent_path:
            overrides["source"] = _subagent_source(agent_path=agent_path)
        _write_rollout(tmp_path, today, name, **overrides)

    metas = codex_rollout.discover_threads(tmp_path, since_days=7, today=today)
    tree = codex_rollout.build_tree(metas)
    by_id = {m.thread_id: m for m in metas}

    assert [m.thread_id for m in tree.roots] == ["root"]
    assert sorted(m.thread_id for m in tree.children_of(by_id["root"])) == ["child-a", "child-b"]
    assert [m.thread_id for m in tree.children_of(by_id["child-a"])] == ["grandchild"]


def test_threads_are_excluded_when_their_session_is_not_rooted_in_the_window(tmp_path):
    """--since selects whole sessions, so a subagent without its root is not reported.

    Reporting it as a root instead invented a tree that never existed.
    """
    today = date(2026, 8, 22)
    _write_rollout(
        tmp_path, today, "stray", id="stray", session_id="elsewhere",
        source=_subagent_source(agent_path="/root/gone/x"),
    )
    _write_rollout(tmp_path, today, "rooted", id="rooted", session_id="here")

    stats = codex_rollout.DiscoveryStats()
    metas = codex_rollout.discover_threads(tmp_path, since_days=7, today=today, stats=stats)

    assert [m.thread_id for m in metas] == ["rooted"]
    assert stats.dropped_unrooted_threads == 1
    assert stats.dropped_unrooted_sessions == 1
    assert any("widen --since" in note for note in stats.notes())


def test_a_rooted_session_keeps_subagents_of_any_age(tmp_path):
    """Once a session is rooted in the window, its whole tree comes along."""
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today - timedelta(days=6), "root", id="root")
    _write_rollout(tmp_path, today, "late-child", id="late-child", source=_subagent_source())

    metas = codex_rollout.discover_threads(tmp_path, since_days=7, today=today)

    assert sorted(m.thread_id for m in metas) == ["late-child", "root"]


def test_resumes_do_not_split_a_session(tmp_path):
    """Resuming writes another root rollout under the same session_id.

    A resume continues the session rather than starting one, so the session is
    represented once — by its original root, the one whose thread id equals the
    session id — with the rest available as resumes.
    """
    today = date(2026, 8, 22)
    base = time.time() - STALE_OFFSET - 500
    _write_rollout(tmp_path, today, "orig", mtime=base, id="sess-1", session_id="sess-1")
    _write_rollout(tmp_path, today, "resume-1", mtime=base + 100, id="r1", session_id="sess-1")
    _write_rollout(tmp_path, today, "resume-2", mtime=base + 200, id="r2", session_id="sess-1")
    _write_rollout(
        tmp_path, today, "child", mtime=base + 50, id="kid", session_id="sess-1",
        source=_subagent_source(),
    )

    metas = codex_rollout.discover_threads(tmp_path, since_days=7, today=today)
    tree = codex_rollout.build_tree(metas)

    assert [m.thread_id for m in tree.roots] == ["sess-1"]
    assert [m.thread_id for m in tree.resumes_of(tree.roots[0])] == ["r1", "r2"]
    assert [m.thread_id for m in tree.children_of(tree.roots[0])] == ["kid"]


def test_session_representative_falls_back_to_earliest_root(tmp_path):
    """Where the original rollout has aged out, the earliest present one stands in."""
    today = date(2026, 8, 22)
    base = time.time() - STALE_OFFSET - 500
    _write_rollout(tmp_path, today, "r1", mtime=base + 100, id="r1", session_id="sess-1")
    _write_rollout(tmp_path, today, "r2", mtime=base + 200, id="r2", session_id="sess-1")

    tree = codex_rollout.build_tree(
        codex_rollout.discover_threads(tmp_path, since_days=7, today=today)
    )

    assert [m.thread_id for m in tree.roots] == ["r1"]


def test_resumed_root_records_what_it_resumed_from(tmp_path):
    """parent_thread_id on a root means "resumed from", not "spawned by".

    4047 of 4290 resumed roots in the sampled corpus carry it, so merging the
    two relations would answer "who spawned this?" with a resume pointer.
    """
    path = tmp_path / "rollout-resumed.jsonl"
    path.write_text(_meta_line(id="r2", session_id="sess-1", parent_thread_id="sess-1") + "\n")

    meta = codex_rollout.read_thread_meta(path)

    assert meta.resumed_from_thread_id == "sess-1"
    assert meta.spawn_parent_thread_id is None


def test_build_tree_does_not_merge_separate_sessions(tmp_path):
    """agent_path is session-scoped, not globally unique — every root is "/root".

    Keying the tree on the bare path merged every session in the window into one
    namespace. Against real data that gave the first root 994 "children", all 994
    belonging to other sessions.
    """
    today = date(2026, 8, 22)
    for session in ("sess-a", "sess-b"):
        _write_rollout(tmp_path, today, f"{session}-root", id=f"{session}-root", session_id=session)
        _write_rollout(
            tmp_path,
            today,
            f"{session}-child",
            id=f"{session}-child",
            session_id=session,
            source=_subagent_source(agent_path="/root/worker_1"),
        )

    metas = codex_rollout.discover_threads(tmp_path, since_days=7, today=today)
    tree = codex_rollout.build_tree(metas)
    by_id = {m.thread_id: m for m in metas}

    assert sorted(m.thread_id for m in tree.roots) == ["sess-a-root", "sess-b-root"]
    for session in ("sess-a", "sess-b"):
        children = tree.children_of(by_id[f"{session}-root"])
        assert [m.thread_id for m in children] == [f"{session}-child"]


def test_window_boundary_is_inclusive_on_both_ends(tmp_path):
    """since_days=7 means today plus the previous seven days — eight directories."""
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today - timedelta(days=7), "edge", id="edge")
    _write_rollout(tmp_path, today - timedelta(days=8), "beyond", id="beyond")

    found = codex_rollout.discover_threads(tmp_path, since_days=7, today=today)

    assert [m.thread_id for m in found] == ["edge"]


def test_limit_zero_returns_nothing(tmp_path):
    """`if limit else` treated 0 as "unlimited" and returned every thread."""
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today, "a", id="a")
    _write_rollout(tmp_path, today, "b", id="b")

    assert codex_rollout.discover_threads(tmp_path, since_days=7, today=today, limit=0) == []
    assert len(codex_rollout.discover_threads(tmp_path, since_days=7, today=today, limit=None)) == 2


def test_scan_reports_cached_and_input_token_totals(tmp_path):
    path = tmp_path / "rollout-tokenfields.jsonl"
    path.write_text("\n".join([_meta_line(), _token_line(300)]) + "\n")

    scan = codex_rollout.scan_thread(path)

    assert scan.total_tokens == 300
    assert scan.cached_input_tokens == 10
    assert scan.input_tokens == 90


def test_agent_filter_tolerates_spaces_after_commas(tmp_path):
    today = date(2026, 8, 22)
    _write_rollout(tmp_path, today, "root", id="root")
    _write_rollout(tmp_path, today, "w", id="w", source=_subagent_source(role="worker"))
    _write_rollout(tmp_path, today, "e", id="e", source=_subagent_source(role="explorer"))

    found = codex_rollout.discover_threads(
        tmp_path, since_days=7, today=today, agent="worker, explorer"
    )

    assert sorted(m.thread_id for m in found) == ["e", "w"]

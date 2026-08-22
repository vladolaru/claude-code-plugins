"""
CLI-level tests for the Codex analysis scripts.

These run the scripts as subprocesses so the argparse surface and the
output contract are both covered.
"""

import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
ANALYZER = PLUGIN_ROOT / "scripts" / "analysis" / "codex_session_analyzer.py"
METRICS = PLUGIN_ROOT / "scripts" / "analysis" / "codex_session_metrics.py"

STALE_OFFSET = 3600  # comfortably outside the active-rollout window


def _meta(thread_id, cwd="/work/project", agent_path=None, role=None):
    payload = {
        "session_id": "sess-1",
        "id": thread_id,
        "cwd": cwd,
        "originator": "codex-tui",
        "cli_version": "0.147.0",
    }
    if agent_path:
        payload["source"] = {
            "subagent": {
                "thread_spawn": {
                    "parent_thread_id": "root",
                    "depth": agent_path.count("/") - 1,
                    "agent_path": agent_path,
                    "agent_nickname": "Tester",
                    "agent_role": role or "worker",
                }
            }
        }
    return json.dumps({"timestamp": "2026-08-22T10:00:00.000Z", "type": "session_meta", "payload": payload})


def _command(exit_code=0, duration=1.0, command="ls", timestamp="2026-08-22T10:00:05.000Z"):
    item = {
        "type": "CommandExecution",
        "id": "c1",
        "command": command,
        "exit_code": exit_code,
        "duration": duration,
    }
    return json.dumps(
        {"timestamp": timestamp, "type": "event_msg", "payload": {"type": "item_completed", "item": item}}
    )


def _tokens(total, timestamp="2026-08-22T10:00:10.000Z"):
    return json.dumps(
        {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {"total_tokens": total, "cached_input_tokens": 5, "input_tokens": 20}},
            },
        }
    )


@pytest.fixture
def sessions_dir(tmp_path):
    """A sessions tree with a root thread and one worker child, both finished."""
    day = date(2026, 8, 22)
    day_dir = tmp_path / "2026" / "08" / "22"
    day_dir.mkdir(parents=True)

    root = day_dir / "rollout-root.jsonl"
    root.write_text("\n".join([_meta("root-thread"), _command(), _tokens(100)]) + "\n")

    child = day_dir / "rollout-child.jsonl"
    child.write_text(
        "\n".join(
            [
                _meta("child-thread", agent_path="/root/reviewer_1", role="code-reviewer"),
                _command(exit_code=1, command="false"),
                _tokens(250),
            ]
        )
        + "\n"
    )

    # Distinct mtimes: equal ones make "newest first" ordering arbitrary.
    stale = time.time() - STALE_OFFSET
    os.utime(child, (stale, stale))
    os.utime(root, (stale + 60, stale + 60))
    return tmp_path


def _run(script, *args):
    return subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True, check=False
    )


def test_analyzer_json_reports_the_tree(sessions_dir):
    result = _run(
        ANALYZER, "--sessions-dir", str(sessions_dir), "--since", "3650", "--format", "json"
    )
    assert result.returncode == 0, result.stderr

    data = json.loads(result.stdout)
    assert data["thread"]["thread_id"] == "root-thread"
    assert data["thread"]["agent_role"] == "root"
    assert [child["thread_id"] for child in data["children"]] == ["child-thread"]
    assert data["children"][0]["agent_role"] == "code-reviewer"


def test_analyzer_reports_command_failures(sessions_dir):
    result = _run(
        ANALYZER, "--sessions-dir", str(sessions_dir), "--since", "3650", "--format", "json"
    )
    data = json.loads(result.stdout)

    assert data["children"][0]["failed_commands"] == 1
    assert data["thread"]["failed_commands"] == 0


def test_analyzer_selects_an_explicit_thread_id(sessions_dir):
    result = _run(
        ANALYZER,
        "--sessions-dir",
        str(sessions_dir),
        "--since",
        "3650",
        "--thread-id",
        "child-thread",
        "--format",
        "json",
    )
    data = json.loads(result.stdout)

    assert data["thread"]["thread_id"] == "child-thread"


def test_analyzer_prefers_a_root_over_a_newer_subagent(sessions_dir):
    """Defaulting to the newest thread outright can land on a leaf, which has no tree."""
    day_dir = sessions_dir / "2026" / "08" / "22"
    newer_child = day_dir / "rollout-newer-child.jsonl"
    newer_child.write_text(
        "\n".join([_meta("newer-child", agent_path="/root/reviewer_2", role="explorer"), _tokens(10)]) + "\n"
    )
    # Newer than the root, but still old enough to count as a finished session.
    newer = time.time() - STALE_OFFSET + 120
    os.utime(newer_child, (newer, newer))

    result = _run(
        ANALYZER, "--sessions-dir", str(sessions_dir), "--since", "3650", "--format", "json"
    )
    data = json.loads(result.stdout)

    assert data["thread"]["thread_id"] == "root-thread"


def test_analyzer_falls_back_to_newest_match_when_no_root_qualifies(sessions_dir):
    result = _run(
        ANALYZER,
        "--sessions-dir",
        str(sessions_dir),
        "--since",
        "3650",
        "--agent",
        "code-reviewer",
        "--format",
        "json",
    )
    data = json.loads(result.stdout)

    assert data["thread"]["thread_id"] == "child-thread"


def test_analyzer_text_output_is_human_readable(sessions_dir):
    result = _run(ANALYZER, "--sessions-dir", str(sessions_dir), "--since", "3650")

    assert result.returncode == 0, result.stderr
    assert "root-thread" in result.stdout
    assert "code-reviewer" in result.stdout


def test_analyzer_exits_nonzero_when_sessions_dir_missing(tmp_path):
    result = _run(ANALYZER, "--sessions-dir", str(tmp_path / "nope"))

    assert result.returncode != 0
    assert "not found" in result.stderr.lower()


def test_analyzer_reports_when_nothing_matches(sessions_dir):
    result = _run(ANALYZER, "--sessions-dir", str(sessions_dir), "--since", "3650", "--cwd", "/nowhere")

    assert result.returncode != 0
    assert "no threads" in result.stderr.lower()

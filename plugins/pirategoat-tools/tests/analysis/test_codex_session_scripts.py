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


def test_metrics_json_has_one_row_per_thread(sessions_dir):
    result = _run(
        METRICS, "--sessions-dir", str(sessions_dir), "--since", "3650", "--format", "json"
    )
    assert result.returncode == 0, result.stderr

    data = json.loads(result.stdout)
    ids = sorted(row["thread_id"] for row in data["threads"])
    assert ids == ["child-thread", "root-thread"]


def test_metrics_rolls_up_by_agent_role(sessions_dir):
    result = _run(
        METRICS, "--sessions-dir", str(sessions_dir), "--since", "3650", "--format", "json"
    )
    data = json.loads(result.stdout)

    by_role = {entry["agent_role"]: entry for entry in data["by_role"]}
    assert by_role["code-reviewer"]["threads"] == 1
    assert by_role["code-reviewer"]["total_tokens"] == 250
    assert by_role["code-reviewer"]["failed_commands"] == 1


def test_metrics_filters_by_agent(sessions_dir):
    result = _run(
        METRICS,
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

    assert [row["thread_id"] for row in data["threads"]] == ["child-thread"]


def test_metrics_markdown_renders_a_table(sessions_dir):
    result = _run(
        METRICS, "--sessions-dir", str(sessions_dir), "--since", "3650", "--format", "markdown"
    )

    assert result.returncode == 0, result.stderr
    assert "| thread |" in result.stdout
    assert "code-reviewer" in result.stdout


def test_metrics_exits_nonzero_when_sessions_dir_missing(tmp_path):
    result = _run(METRICS, "--sessions-dir", str(tmp_path / "nope"))

    assert result.returncode != 0
    assert "not found" in result.stderr.lower()


def test_metrics_reports_empty_result_without_crashing(sessions_dir):
    result = _run(
        METRICS,
        "--sessions-dir",
        str(sessions_dir),
        "--since",
        "3650",
        "--cwd",
        "/nowhere",
        "--format",
        "json",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["threads"] == []


@pytest.mark.skipif(
    not (Path.home() / ".codex" / "sessions").is_dir(),
    reason="no real Codex sessions on this machine",
)
def test_scripts_run_against_real_sessions():
    """Smoke test: the real schema still parses. Skipped where Codex is absent."""
    for script in (ANALYZER, METRICS):
        result = _run(script, "--since", "2", "--limit", "3", "--format", "json")
        # Exit 1 is legitimate when nothing was recorded in the window.
        assert result.returncode in (0, 1), result.stderr
        if result.returncode == 0 and result.stdout.strip():
            json.loads(result.stdout)


def test_analyzer_text_truncates_long_command_lines(sessions_dir):
    """argv for a Codex shell call can hold a whole multi-line script.

    Printed verbatim, a handful of failures buries the report — a real 30-day
    run produced 36 failures each dumping its full script.
    """
    day_dir = sessions_dir / "2026" / "08" / "22"
    script = "set -eu\n" + "\n".join(f"echo line-{i}" for i in range(200))
    noisy = day_dir / "rollout-noisy.jsonl"
    noisy.write_text(
        "\n".join(
            [
                _meta("noisy-thread"),
                _command(exit_code=1, command=["/bin/zsh", "-lc", script]),
            ]
        )
        + "\n"
    )
    stale = time.time() - STALE_OFFSET + 120
    os.utime(noisy, (stale, stale))

    result = _run(
        ANALYZER, "--sessions-dir", str(sessions_dir), "--since", "3650",
        "--thread-id", "noisy-thread",
    )

    assert result.returncode == 0, result.stderr
    failure_lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("exit 1:")]
    assert len(failure_lines) == 1
    assert len(failure_lines[0]) < 200
    assert "\n" not in failure_lines[0]


def test_analyzer_json_keeps_the_full_command(sessions_dir):
    """Truncation is a text-rendering concern; JSON stays full fidelity."""
    day_dir = sessions_dir / "2026" / "08" / "22"
    script = "set -eu\n" + "\n".join(f"echo line-{i}" for i in range(200))
    noisy = day_dir / "rollout-noisy2.jsonl"
    noisy.write_text(
        "\n".join([_meta("noisy2"), _command(exit_code=1, command=["/bin/zsh", "-lc", script])]) + "\n"
    )
    stale = time.time() - STALE_OFFSET + 120
    os.utime(noisy, (stale, stale))

    result = _run(
        ANALYZER, "--sessions-dir", str(sessions_dir), "--since", "3650",
        "--thread-id", "noisy2", "--format", "json",
    )
    data = json.loads(result.stdout)

    assert "line-199" in str(data["failures"][0]["command"])


def _stray_meta(thread_id, session_id):
    """A subagent line belonging to a session with no root in the window."""
    payload = {
        "session_id": session_id,
        "id": thread_id,
        "cwd": "/work/project",
        "originator": "codex-tui",
        "cli_version": "0.147.0",
        "source": {
            "subagent": {
                "thread_spawn": {
                    "parent_thread_id": "gone",
                    "depth": 1,
                    "agent_path": "/root/gone",
                    "agent_nickname": "Tester",
                    "agent_role": "worker",
                }
            }
        },
    }
    return json.dumps({"timestamp": "2026-08-22T10:00:00.000Z", "type": "session_meta", "payload": payload})


def _root_meta(thread_id, session_id):
    """A root rollout line with an explicit session id (for resume fixtures)."""
    payload = {
        "session_id": session_id,
        "id": thread_id,
        "cwd": "/work/project",
        "originator": "codex-tui",
        "cli_version": "0.147.0",
    }
    return json.dumps({"timestamp": "2026-08-22T10:00:00.000Z", "type": "session_meta", "payload": payload})


def test_analyzer_presents_a_resumed_session_as_one_session(sessions_dir):
    """A resume continues the session; it must not appear as a separate root."""
    day_dir = sessions_dir / "2026" / "08" / "22"
    base = time.time() - STALE_OFFSET - 500
    for name, tid, offset in [("orig", "sess-x", 0), ("res1", "rx1", 100), ("res2", "rx2", 200)]:
        path = day_dir / f"rollout-{name}.jsonl"
        path.write_text("\n".join([_root_meta(tid, "sess-x"), _command(), _tokens(50)]) + "\n")
        os.utime(path, (base + offset, base + offset))

    result = _run(
        ANALYZER, "--sessions-dir", str(sessions_dir), "--since", "3650",
        "--thread-id", "sess-x", "--format", "json",
    )
    data = json.loads(result.stdout)

    assert data["session_id"] == "sess-x"
    assert data["thread"]["thread_id"] == "sess-x"
    assert sorted(r["thread_id"] for r in data["resumes"]) == ["rx1", "rx2"]


def test_analyzer_does_not_sum_resume_tokens_into_the_session(sessions_dir):
    """A resume replays prior context, so its tokens are re-sent, not new work."""
    day_dir = sessions_dir / "2026" / "08" / "22"
    base = time.time() - STALE_OFFSET - 500
    orig = day_dir / "rollout-o.jsonl"
    orig.write_text("\n".join([_root_meta("sess-y", "sess-y"), _tokens(100)]) + "\n")
    os.utime(orig, (base, base))
    res = day_dir / "rollout-r.jsonl"
    res.write_text("\n".join([_root_meta("ry1", "sess-y"), _tokens(9000)]) + "\n")
    os.utime(res, (base + 100, base + 100))

    data = json.loads(
        _run(ANALYZER, "--sessions-dir", str(sessions_dir), "--since", "3650",
             "--thread-id", "sess-y", "--format", "json").stdout
    )

    assert data["thread"]["total_tokens"] == 100
    assert data["resumes"][0]["total_tokens"] == 9000


def test_analyzer_explains_why_threads_were_excluded(sessions_dir):
    """Silently analyzing nothing is confusing; say what was skipped."""
    day_dir = sessions_dir / "2026" / "08" / "22"
    stray = day_dir / "rollout-stray.jsonl"
    stray.write_text("\n".join([_stray_meta("stray", "other-session"), _tokens(5)]) + "\n")
    stale = time.time() - STALE_OFFSET
    os.utime(stray, (stale, stale))

    data = json.loads(
        _run(ANALYZER, "--sessions-dir", str(sessions_dir), "--since", "3650",
             "--format", "json").stdout
    )

    assert any("widen --since" in note for note in data["notes"])


def test_metrics_reports_skip_notes(sessions_dir):
    day_dir = sessions_dir / "2026" / "08" / "22"
    stray = day_dir / "rollout-stray2.jsonl"
    stray.write_text("\n".join([_stray_meta("stray2", "other-session-2"), _tokens(5)]) + "\n")
    stale = time.time() - STALE_OFFSET
    os.utime(stray, (stale, stale))

    result = _run(METRICS, "--sessions-dir", str(sessions_dir), "--since", "3650", "--format", "json")
    data = json.loads(result.stdout)

    assert any("widen --since" in note for note in data["notes"])


def test_analyzer_caps_the_failed_command_list(sessions_dir):
    """A real 30-day run had 36 failures; printing every one buries the report."""
    day_dir = sessions_dir / "2026" / "08" / "22"
    lines = [_meta("many-failures")]
    lines += [_command(exit_code=1, command=f"cmd-{i}") for i in range(25)]
    path = day_dir / "rollout-many.jsonl"
    path.write_text("\n".join(lines) + "\n")
    stale = time.time() - STALE_OFFSET
    os.utime(path, (stale, stale))

    result = _run(
        ANALYZER, "--sessions-dir", str(sessions_dir), "--since", "3650", "--thread-id", "many-failures"
    )

    assert "… and 15 more" in result.stdout
    shown = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("exit 1:")]
    assert len(shown) == 10

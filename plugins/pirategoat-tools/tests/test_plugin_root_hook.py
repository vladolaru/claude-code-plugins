"""The PreToolUse hook records the plugin root for the current session only.

One machine-wide /tmp file was last-writer-wins across every open session,
so a dev checkout and the installed release overwrote each other before
every Bash call. The hook now writes sessions/<session id>/plugin-root
under the state root run_paths.state_root() owns, keyed by the session id
every shell in a session carries as CLAUDE_CODE_SESSION_ID.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "init-plugin-root.sh"
STRIPPED = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_SESSION_ID", "PIRATEGOAT_TOOLS_HOME", "HOME")


def _run_hook(env_overrides, stdin_json):
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED}
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(HOOK)], input=json.dumps(stdin_json),
        capture_output=True, text=True, env=env, timeout=10,
    )


@pytest.fixture
def state_root(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def plugin_root(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    return root


def _pointer(state_root, session):
    return state_root / "sessions" / session / "plugin-root"


def test_writes_the_root_under_the_session_id_from_the_environment(state_root, plugin_root):
    result = _run_hook(
        {"CLAUDE_PLUGIN_ROOT": str(plugin_root), "CLAUDE_CODE_SESSION_ID": "s-env",
         "PIRATEGOAT_TOOLS_HOME": str(state_root)},
        {"session_id": "s-stdin"},
    )
    assert result.returncode == 0, result.stderr
    assert _pointer(state_root, "s-env").read_text() == f"{plugin_root}\n"
    assert not _pointer(state_root, "s-stdin").exists()


def test_falls_back_to_the_session_id_on_stdin(state_root, plugin_root):
    result = _run_hook(
        {"CLAUDE_PLUGIN_ROOT": str(plugin_root), "PIRATEGOAT_TOOLS_HOME": str(state_root)},
        {"session_id": "s-stdin"},
    )
    assert result.returncode == 0, result.stderr
    assert _pointer(state_root, "s-stdin").read_text() == f"{plugin_root}\n"


def test_defaults_to_home_dot_pirategoat_tools(tmp_path, plugin_root):
    home = tmp_path / "home"
    home.mkdir()
    result = _run_hook(
        {"CLAUDE_PLUGIN_ROOT": str(plugin_root), "CLAUDE_CODE_SESSION_ID": "s1", "HOME": str(home)},
        {},
    )
    assert result.returncode == 0, result.stderr
    assert (home / ".pirategoat-tools" / "sessions" / "s1" / "plugin-root").read_text() == f"{plugin_root}\n"


def test_ignores_a_relative_override_like_state_root_does(tmp_path, plugin_root):
    home = tmp_path / "home"
    home.mkdir()
    result = _run_hook(
        {"CLAUDE_PLUGIN_ROOT": str(plugin_root), "CLAUDE_CODE_SESSION_ID": "s1",
         "HOME": str(home), "PIRATEGOAT_TOOLS_HOME": "relative/path"},
        {},
    )
    assert result.returncode == 0, result.stderr
    assert (home / ".pirategoat-tools" / "sessions" / "s1" / "plugin-root").exists()
    assert not (Path.cwd() / "relative").exists()


def test_writes_nothing_without_a_plugin_root(state_root):
    result = _run_hook(
        {"CLAUDE_CODE_SESSION_ID": "s1", "PIRATEGOAT_TOOLS_HOME": str(state_root)}, {}
    )
    assert result.returncode == 0
    assert not state_root.exists()


def test_writes_nothing_without_a_session_id(state_root, plugin_root):
    result = _run_hook(
        {"CLAUDE_PLUGIN_ROOT": str(plugin_root), "PIRATEGOAT_TOOLS_HOME": str(state_root)}, {}
    )
    assert result.returncode == 0
    assert not state_root.exists()


@pytest.mark.parametrize("bad", ["../evil", "..", ".", "a/b", "a b"])
def test_refuses_a_session_id_that_is_not_a_safe_segment(state_root, plugin_root, bad):
    result = _run_hook(
        {"CLAUDE_PLUGIN_ROOT": str(plugin_root), "CLAUDE_CODE_SESSION_ID": bad,
         "PIRATEGOAT_TOOLS_HOME": str(state_root)},
        {},
    )
    assert result.returncode == 0
    assert not state_root.exists()
    assert not (state_root.parent / "evil").exists()


def test_sweeps_session_dirs_whose_pointer_is_older_than_a_day(state_root, plugin_root):
    stale = _pointer(state_root, "stale")
    stale.parent.mkdir(parents=True)
    stale.write_text("/old\n")
    two_days_ago = time.time() - 2 * 86400
    os.utime(stale, (two_days_ago, two_days_ago))
    fresh = _pointer(state_root, "fresh")
    fresh.parent.mkdir(parents=True)
    fresh.write_text("/recent\n")

    result = _run_hook(
        {"CLAUDE_PLUGIN_ROOT": str(plugin_root), "CLAUDE_CODE_SESSION_ID": "s1",
         "PIRATEGOAT_TOOLS_HOME": str(state_root)},
        {},
    )
    assert result.returncode == 0, result.stderr
    assert not stale.parent.exists()
    assert fresh.read_text() == "/recent\n"
    assert _pointer(state_root, "s1").exists()

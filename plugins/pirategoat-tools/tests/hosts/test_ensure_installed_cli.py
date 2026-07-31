"""CLI tests for ensure_installed.py.

These tests mock the subprocess.run call rather than actually running
composer/npm. They verify orchestration: cache hit avoids subprocess;
cache miss runs it; failures emit banner JSON with non-blocking exit.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

from hosts.ensure_installed import _handle_dep_root
from hosts.install.lockfile import DepRoot


SCRIPTS = (Path(__file__).parent.parent.parent / "scripts").resolve()


def _run_cli(*args, env_extra=None):
    env = {**os.environ, "PYTHONPATH": str(SCRIPTS)}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "hosts.ensure_installed", *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


def test_cli_runs_from_absolute_script_path_without_pythonpath(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "hosts" / "ensure_installed.py"),
            "--repo", str(repo),
        ],
        capture_output=True, text=True, cwd=repo, env=env, timeout=30,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "nothing_to_install"


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"php": ["--no-dev"]}',
        '{"js": "bad"}',
    ],
)
def test_cli_rejects_malformed_override_shapes_with_json_error(tmp_path, payload):
    repo = tmp_path / "repo"
    repo.mkdir()

    result = _run_cli(
        "--repo", str(repo),
        "--overrides-json", payload,
    )

    assert result.returncode == 2
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert "object" in data["error"]
    assert "Traceback" not in result.stderr


def test_cli_rejects_non_string_js_manager_with_json_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    result = _run_cli(
        "--repo", str(repo),
        "--overrides-json", '{"js": {"manager": []}}',
    )

    assert result.returncode == 2
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert "js.manager must be a string" in data["error"]
    assert "Traceback" not in result.stderr


def test_cli_skip_install_returns_skipped(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = _run_cli(
        "--repo", str(repo),
        "--overrides-json", '{"skip_install": true}',
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "skipped"
    assert payload["reason"] == "skip_install override"


def test_cli_no_lockfile_returns_nothing_to_install(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = _run_cli(
        "--repo", str(repo),
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "nothing_to_install"


def test_missing_install_binary_returns_failed_status(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "composer.lock").write_text("{}")
    (repo / "composer.json").write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path))

    with mock.patch("hosts.ensure_installed.subprocess.run",
                    side_effect=FileNotFoundError("composer not found")):
        result = _handle_dep_root(DepRoot("composer", "."), str(repo), [])

    assert result["status"] == "failed"
    assert result["error_class"] == "install_command_unavailable"
    assert "composer not found" in result["error"]


def test_install_timeout_returns_failed_status(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "composer.lock").write_text("{}")
    (repo / "composer.json").write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path))

    with mock.patch("hosts.ensure_installed.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="composer", timeout=1200)):
        result = _handle_dep_root(DepRoot("composer", "."), str(repo), [])

    assert result["status"] == "failed"
    assert result["error_class"] == "install_timeout"
    assert "timed out" in result["error"]


def test_retry_install_exception_returns_failed_status(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package-lock.json").write_text("{}")
    (repo / "package.json").write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path))
    first = subprocess.CompletedProcess(
        args=["npm"], returncode=1, stdout="", stderr="npm ERR! code ERESOLVE"
    )

    with mock.patch("hosts.ensure_installed.subprocess.run",
                    side_effect=[first, FileNotFoundError("npm not found")]):
        result = _handle_dep_root(DepRoot("npm", "."), str(repo), [])

    assert result["status"] == "failed"
    assert result["error_class"] == "install_command_unavailable"
    assert "npm not found" in result["error"]


def test_install_overrides_apply_env_to_cache_miss(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package-lock.json").write_text("{}")
    (repo / "package.json").write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path))

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["npm", "install"]:
            Path(kwargs["cwd"], "node_modules").mkdir()
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("hosts.ensure_installed.subprocess.run", side_effect=fake_run) as run:
        result = _handle_dep_root(
            DepRoot("npm", "."),
            str(repo),
            [],
            env={"NPM_CONFIG_REGISTRY": "https://registry.example.test"},
        )

    assert result["status"] == "ok"
    assert result["action"] == "installed"
    for call in run.call_args_list:
        assert call.kwargs["env"]["NPM_CONFIG_REGISTRY"] == "https://registry.example.test"


def test_ensure_installed_contains_no_shell_true():
    """No shell=True anywhere in ensure_installed — remote-exec guard."""
    source = Path(__file__).parents[2] / "scripts" / "hosts" / "ensure_installed.py"
    content = source.read_text()
    assert "shell=True" not in content, (
        "shell=True reintroduced into ensure_installed.py — this is a "
        "remote shell execution surface if any user-controlled string "
        "reaches it. If you need shell functionality, add it behind an "
        "allowlisted code path with tests, not by flipping shell=True."
    )


class _FakeRun:
    """Stand-in for subprocess.run that records calls and returns success."""
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def fake_run(monkeypatch):
    """Patch subprocess.run inside hosts.ensure_installed (the call site)."""
    import hosts.ensure_installed as ei_mod
    fake = _FakeRun()
    fake_subprocess = types.SimpleNamespace(
        run=fake,
        TimeoutExpired=subprocess.TimeoutExpired,
        CompletedProcess=subprocess.CompletedProcess,
    )
    monkeypatch.setattr(ei_mod, "subprocess", fake_subprocess)
    return fake


@pytest.fixture
def composer_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "composer.json").write_text('{"name":"test/test"}')
    (repo / "composer.lock").write_text(
        '{"_readme":[],"content-hash":"x","packages":[],"packages-dev":[]}'
    )
    return repo


def _run_main(args):
    from hosts.ensure_installed import main
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(args)
    return rc, json.loads(buf.getvalue())


class TestPayloadShape:
    def test_payload_includes_cache_path_and_action(self, composer_repo, fake_run):
        rc, payload = _run_main(["--repo", str(composer_repo)])
        assert rc == 0
        assert payload["status"] == "ok"
        assert len(payload["managers"]) == 1
        m = payload["managers"][0]
        assert m["manager"] == "composer"
        assert m["status"] == "ok"
        assert "cache_path" in m
        assert m["action"] == "installed"
        assert m["inputs_hash"]
        # No legacy "symlink" / "cache_key" / "attempts" fields
        for legacy in ("symlink", "cache_key", "attempts"):
            assert legacy not in m

    def test_second_run_with_same_lockfile_is_cache_hit(self, composer_repo, fake_run):
        rc1, payload1 = _run_main(["--repo", str(composer_repo)])
        assert payload1["managers"][0]["action"] == "installed"
        # subprocess.run was called once for the install
        first_call_count = len(fake_run.calls)
        assert first_call_count >= 1

        rc2, payload2 = _run_main(["--repo", str(composer_repo)])
        assert payload2["managers"][0]["action"] == "cache_hit"
        # No additional subprocess.run calls — install_fn was not invoked
        assert len(fake_run.calls) == first_call_count

    def test_lockfile_change_triggers_replaced_action(self, composer_repo, fake_run):
        rc1, _ = _run_main(["--repo", str(composer_repo)])
        # Mutate the lockfile so the hash differs
        (composer_repo / "composer.lock").write_text(
            '{"_readme":[],"content-hash":"y","packages":[],"packages-dev":[]}'
        )
        rc2, payload2 = _run_main(["--repo", str(composer_repo)])
        assert payload2["managers"][0]["action"] == "replaced"

    def test_staged_config_change_without_lockfile_change_busts_the_cache(
        self, tmp_path, fake_run, monkeypatch,
    ):
        """A .npmrc edit changes what the install produces even though the
        lockfile is untouched — reporting a cache hit would expose the old
        dependency layout built from the old config."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "jsrepo"
        repo.mkdir()
        (repo / "package.json").write_text("{}")
        (repo / "package-lock.json").write_text("{}")
        (repo / ".npmrc").write_text("registry=https://registry.example.test\n")
        rc1, payload1 = _run_main(["--repo", str(repo)])
        assert payload1["managers"][0]["action"] == "installed"

        (repo / ".npmrc").write_text("registry=https://other.example.test\n")

        rc2, payload2 = _run_main(["--repo", str(repo)])
        assert payload2["managers"][0]["action"] == "replaced"

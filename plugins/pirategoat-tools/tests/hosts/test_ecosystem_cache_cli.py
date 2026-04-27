"""CLI tests for ecosystem_cache.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = (Path(__file__).parent.parent.parent / "scripts").resolve()


def _run_cli(*args, env_extra=None):
    env = {**os.environ, "PYTHONPATH": str(SCRIPTS)}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "hosts.ecosystem_cache", *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


def test_cli_runs_from_absolute_script_path_without_pythonpath(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "hosts" / "ecosystem_cache.py"),
            "--list",
        ],
        capture_output=True, text=True, cwd=repo, env=env, timeout=30,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "list"


def test_list_reports_both_hosts(tmp_path):
    result = _run_cli("--list", env_extra={"HOME": str(tmp_path)})
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    names = {e["name"] for e in payload["hosts"]}
    assert names == {"wordpress", "woocommerce"}


def test_verify_runs_without_error(tmp_path):
    result = _run_cli("--verify", env_extra={"HOME": str(tmp_path)})
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "hosts" in payload


def test_missing_subcommand_errors(tmp_path):
    result = _run_cli(env_extra={"HOME": str(tmp_path)})
    assert result.returncode != 0


def test_ecosystem_cache_cli_unknown_host_returns_structured_error(tmp_path, monkeypatch, capsys):
    import json as json_lib
    from hosts import ecosystem_cache

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ecosystem_cache", "--update", "--host", "not-a-real-host"])
    rc = ecosystem_cache.main()
    assert rc != 0  # user error — it's fine for this path to exit non-zero
    captured = capsys.readouterr()
    data = json_lib.loads(captured.out)
    assert data["status"] == "error"
    assert "unknown" in data["error"].lower() or "not-a-real-host" in data["error"]

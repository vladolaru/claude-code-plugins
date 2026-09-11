"""CLI tests for ecosystem_cache.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hosts import ecosystem_cache

SCRIPTS = (Path(__file__).parent.parent.parent / "scripts").resolve()


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


def test_list_prints_the_identity_slot_for_every_known_host(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(sys, "argv", ["ecosystem_cache", "--list"])

    rc = ecosystem_cache.main()

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = {e["name"] for e in payload["hosts"]}
    assert names == {"wordpress", "woocommerce"}
    assert [row["identity"] for row in payload["hosts"]] == [None, None]


def test_verify_runs_without_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ecosystem_cache", "--verify"])

    rc = ecosystem_cache.main()

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "hosts" in payload


def test_missing_subcommand_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ecosystem_cache"])

    with pytest.raises(SystemExit) as exc:
        ecosystem_cache.main()

    assert exc.value.code != 0


def test_ecosystem_cache_cli_unknown_host_returns_structured_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ecosystem_cache", "--update", "--host", "not-a-real-host"])
    rc = ecosystem_cache.main()
    assert rc != 0  # user error — it's fine for this path to exit non-zero
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "error"
    assert "unknown" in data["error"].lower() or "not-a-real-host" in data["error"]

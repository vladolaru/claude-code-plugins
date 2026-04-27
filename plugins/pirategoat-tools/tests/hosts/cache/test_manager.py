"""Tests for ecosystem cache manager."""

import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from hosts.cache.manager import (
    KNOWN_ECOSYSTEM_REPOS, cache_dir_for, update_host, list_hosts, verify_hosts,
)


def test_known_repos_has_wordpress_and_woocommerce():
    names = {r.name for r in KNOWN_ECOSYSTEM_REPOS}
    assert "wordpress" in names
    assert "woocommerce" in names


def test_cache_dir_uses_ecosystem_namespace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    d = cache_dir_for("wordpress")
    assert str(d).endswith(".cache/pirategoat/ecosystem/wordpress/latest")


def test_cache_dir_honors_xdg_cache_home(tmp_path, monkeypatch):
    xdg_root = tmp_path / "xdg-cache"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_root))
    d = cache_dir_for("wordpress")
    assert d == xdg_root / "pirategoat" / "ecosystem" / "wordpress" / "latest"


def test_update_host_clones_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with mock.patch("hosts.cache.manager.subprocess.run") as m_run:
        m_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        result = update_host("wordpress")
    assert result["action"] == "cloned"
    # First positional arg of subprocess.run is the cmd list
    cmd = m_run.call_args[0][0]
    assert cmd[0] == "git"
    assert "clone" in cmd


def test_update_host_pulls_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".cache" / "pirategoat" / "ecosystem" / "wordpress" / "latest"
    d.mkdir(parents=True)
    (d / ".git").mkdir()
    with mock.patch("hosts.cache.manager.subprocess.run") as m_run:
        m_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        result = update_host("wordpress")
    assert result["action"] == "pulled"


def test_list_hosts_reports_presence(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    wp = tmp_path / ".cache" / "pirategoat" / "ecosystem" / "wordpress" / "latest"
    wp.mkdir(parents=True)
    result = list_hosts()
    names = {e["name"]: e for e in result}
    assert names["wordpress"]["present"] is True
    assert names["woocommerce"]["present"] is False


def test_verify_hosts_flags_stale(tmp_path, monkeypatch):
    """When last-updated marker is older than 30 days, flag stale."""
    import time
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".cache" / "pirategoat" / "ecosystem" / "wordpress" / "latest"
    d.mkdir(parents=True)
    marker = d / ".last_updated"
    marker.write_text("0")  # epoch 0 -> way stale
    os.utime(str(marker), (0, 0))
    result = verify_hosts()
    wp_entry = next(r for r in result if r["name"] == "wordpress")
    assert wp_entry["stale"] is True


def test_update_host_handles_git_timeout(monkeypatch, tmp_path):
    from hosts.cache import manager

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))

    monkeypatch.setattr(manager.subprocess, "run", fake_run)
    result = manager.update_host("wordpress")
    assert result["ok"] is False
    assert "timed out" in result["stderr"].lower() or "timeout" in result["stderr"].lower()


def test_update_host_handles_git_not_found(monkeypatch, tmp_path):
    from hosts.cache import manager

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("No such file or directory: 'git'")

    monkeypatch.setattr(manager.subprocess, "run", fake_run)
    result = manager.update_host("wordpress")
    assert result["ok"] is False
    assert "git" in result["stderr"].lower() or "not found" in result["stderr"].lower()


def test_ensure_fresh_no_op_when_within_window(tmp_path, monkeypatch):
    """Slot exists with recent .last_updated marker → no git call."""
    import time as _time
    from hosts.cache import manager

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = tmp_path / "pirategoat" / "ecosystem" / "wordpress" / "latest"
    d.mkdir(parents=True)
    (d / ".last_updated").write_text(str(int(_time.time())))

    update_calls = []
    monkeypatch.setattr(
        manager, "update_host",
        lambda *a, **kw: update_calls.append(a) or {"ok": True, "action": "pulled"},
    )

    result = manager.ensure_fresh("wordpress")
    assert result["action"] == "fresh"
    assert result["ok"] is True
    assert update_calls == []  # update_host was NOT called


def test_ensure_fresh_calls_update_when_slot_missing(tmp_path, monkeypatch):
    """Slot doesn't exist → ensure_fresh calls update_host."""
    from hosts.cache import manager

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    update_calls = []

    def fake_update(name):
        update_calls.append(name)
        return {"name": name, "action": "cloned", "ok": True, "stderr": ""}

    monkeypatch.setattr(manager, "update_host", fake_update)
    result = manager.ensure_fresh("wordpress")
    assert update_calls == ["wordpress"]
    assert result["action"] == "cloned"


def test_ensure_fresh_calls_update_when_slot_stale(tmp_path, monkeypatch):
    """Slot exists but .last_updated is older than max_age → call update_host."""
    import os as _os
    from hosts.cache import manager

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = tmp_path / "pirategoat" / "ecosystem" / "wordpress" / "latest"
    d.mkdir(parents=True)
    marker = d / ".last_updated"
    marker.write_text("0")  # epoch 0 — way stale
    _os.utime(str(marker), (0, 0))

    update_calls = []
    monkeypatch.setattr(
        manager, "update_host",
        lambda name: update_calls.append(name) or {"ok": True, "action": "pulled"},
    )
    manager.ensure_fresh("wordpress", max_age_seconds=3600)
    assert update_calls == ["wordpress"]


def test_ensure_fresh_respects_custom_max_age(tmp_path, monkeypatch):
    """Custom max_age_seconds works (e.g. 0 forces refresh always)."""
    import time as _time
    from hosts.cache import manager

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = tmp_path / "pirategoat" / "ecosystem" / "wordpress" / "latest"
    d.mkdir(parents=True)
    (d / ".last_updated").write_text(str(int(_time.time())))

    update_calls = []
    monkeypatch.setattr(
        manager, "update_host",
        lambda name: update_calls.append(name) or {"ok": True, "action": "pulled"},
    )
    manager.ensure_fresh("wordpress", max_age_seconds=0)
    assert update_calls == ["wordpress"]  # 0 max_age forces refresh


def test_update_host_is_serialized_by_lock(monkeypatch, tmp_path):
    """Two calls into the same host name serialize via advisory lock."""
    from hosts.cache import manager

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        # Simulate a successful clone by creating the target with .git
        cmd = args[0]
        if "clone" in cmd:
            target = Path(cmd[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(manager.subprocess, "run", fake_run)

    result1 = manager.update_host("wordpress")
    result2 = manager.update_host("wordpress")
    assert result1["ok"] is True
    assert result2["ok"] is True
    # The second call should have seen the .git from the first, so it went
    # down the "pull" path, not "clone".
    assert result2["action"] == "pulled"

"""Tests for ecosystem cache manager."""

import os
import subprocess
from pathlib import Path
from unittest import mock

from helpers.pipeline_process import init_repo
from hosts.cache.manager import (
    cache_dir_for, update_host, list_hosts, verify_hosts,
)


def _commit_all(slot: Path, message: str) -> str:
    """Commit everything in the slot; returns the new HEAD sha."""
    subprocess.run(["git", "-C", str(slot), "add", "."], check=True)
    subprocess.run(["git", "-C", str(slot), "commit", "-q", "-m", message], check=True)
    return subprocess.run(
        ["git", "-C", str(slot), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()


def _init_slot(slot: Path, version_file: str, content: str) -> str:
    """A real git repo in the slot with the version file committed; returns its HEAD sha."""
    init_repo(slot, branch="trunk")
    (slot / version_file).parent.mkdir(parents=True, exist_ok=True)
    (slot / version_file).write_text(content)
    (slot / ".last_updated").write_text("1788552248")
    return _commit_all(slot, "seed")


def test_slot_identity_reads_commit_version_and_refresh(tmp_path, monkeypatch):
    from hosts.cache.manager import slot_identity

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    slot = cache_dir_for("wordpress")
    sha = _init_slot(slot, "src/wp-includes/version.php", "<?php\n$wp_version = '7.2-alpha-63166-src';\n")

    identity = slot_identity("wordpress")

    assert identity["present"] is True
    assert identity["commit"] == sha
    assert "branch" not in identity
    assert identity["commit_date"].startswith("20")
    assert identity["version"] == "7.2-alpha-63166-src"
    assert identity["refreshed"] == "2026-09-04T20:04:08Z"


def test_slot_identity_reads_version_from_the_captured_commit_during_a_refresh(tmp_path, monkeypatch):
    from hosts.cache import manager

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    slot = cache_dir_for("wordpress")
    first_sha = _init_slot(slot, "src/wp-includes/version.php", "<?php\n$wp_version = '7.2-alpha-a';\n")
    (slot / "src/wp-includes/version.php").write_text("<?php\n$wp_version = '7.2-alpha-b';\n")
    refreshed_sha = _commit_all(slot, "refresh")
    subprocess.run(["git", "-C", str(slot), "reset", "--hard", "-q", first_sha], check=True)

    from hosts import identity as identity_module

    real_git_read = identity_module.git_read

    def read_and_refresh(target, *args):
        value = real_git_read(target, *args)
        if args[0] == "log":  # the commit was just captured; the refresh lands now
            subprocess.run(["git", "-C", str(slot), "reset", "--hard", "-q", refreshed_sha], check=True)
        return value

    monkeypatch.setattr(identity_module, "git_read", read_and_refresh)

    identity = manager.slot_identity("wordpress")

    assert identity["commit"] == first_sha
    assert identity["version"] == "7.2-alpha-a"


def test_slot_identity_ignores_an_uncommitted_version_change(tmp_path, monkeypatch):
    from hosts.cache import manager

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    slot = cache_dir_for("wordpress")
    _init_slot(slot, "src/wp-includes/version.php", "<?php\n$wp_version = '7.2-alpha-a';\n")
    (slot / "src/wp-includes/version.php").write_text("<?php\n$wp_version = '7.2-alpha-b';\n")

    identity = manager.slot_identity("wordpress")

    assert identity["version"] == "7.2-alpha-a"


def test_slot_identity_is_all_none_when_nothing_can_be_read(tmp_path, monkeypatch):
    from hosts.cache.manager import slot_identity

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert slot_identity("wordpress") == {
        "present": False,
        "commit": None,
        "commit_date": None,
        "version": None,
        "refreshed": None,
    }

    slot = cache_dir_for("wordpress")
    slot.mkdir(parents=True)
    identity = slot_identity("wordpress")

    assert identity["present"] is True
    assert identity["commit"] is None
    assert identity["version"] is None


def test_slot_identity_reads_a_version_file_with_invalid_utf8(tmp_path, monkeypatch):
    """git prints the file's bytes as they are; a stray byte elsewhere in the
    file must not hide the version."""
    from hosts.cache.manager import slot_identity

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    slot = cache_dir_for("wordpress")
    _init_slot(slot, "src/wp-includes/version.php", "<?php\n$wp_version = '6.9';\n")
    (slot / "src/wp-includes/version.php").write_bytes(b"<?php\n$wp_version = '6.9';\n// \xff\n")
    _commit_all(slot, "bytes")

    assert slot_identity("wordpress")["version"] == "6.9"


def test_slot_identity_reports_no_refresh_without_a_readable_marker(tmp_path, monkeypatch):
    """A directory mtime is not a refresh time: without the marker the fact
    is unknown, and an unreadable marker is the same unknown."""
    from hosts.cache.manager import ensure_fresh, slot_identity

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    slot = cache_dir_for("wordpress")
    _init_slot(slot, "src/wp-includes/version.php", "<?php\n$wp_version = '6.9';\n")
    (slot / ".last_updated").unlink()
    assert slot_identity("wordpress")["refreshed"] is None

    (slot / ".last_updated").write_text("1788552248")
    (slot / ".last_updated").chmod(0)
    try:
        assert slot_identity("wordpress")["refreshed"] is None
        monkeypatch.setattr("hosts.cache.manager.update_host", lambda name: {"name": name, "action": "pulled", "ok": True, "stderr": ""})
        assert ensure_fresh("wordpress")["ok"] is True
    finally:
        (slot / ".last_updated").chmod(0o644)


def test_slot_identity_survives_a_missing_git_binary(tmp_path, monkeypatch):
    from hosts.cache.manager import slot_identity

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    cache_dir_for("wordpress").mkdir(parents=True)

    with mock.patch("hosts.cache.manager.subprocess.run", side_effect=FileNotFoundError("git")):
        assert slot_identity("wordpress")["commit"] is None


def test_list_hosts_carries_the_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    sha = _init_slot(cache_dir_for("wordpress"), "src/wp-includes/version.php", "<?php\n$wp_version = '7.2';\n")

    rows = {row["name"]: row for row in list_hosts()}

    assert rows["wordpress"]["identity"]["commit"] == sha
    assert rows["woocommerce"]["identity"] is None
    assert rows["wordpress"]["present"] is True
    assert rows["woocommerce"]["present"] is False


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



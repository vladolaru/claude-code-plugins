"""Tests for ecosystem-cache resolver."""

from pathlib import Path

import pytest

from hosts.resolvers.ecosystem_cache import EcosystemCacheResolver


def test_returns_empty_when_cache_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = EcosystemCacheResolver().resolve(str(tmp_path / "repo"))
    assert result.entries == []
    assert result.notes.get("state") == "cache_missing"


def test_returns_wordpress_when_cache_present(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    wp = tmp_path / ".cache" / "pirategoat" / "ecosystem" / "wordpress" / "latest"
    wp.mkdir(parents=True)
    (wp / "index.php").write_text("<?php")
    result = EcosystemCacheResolver().resolve(str(tmp_path / "repo"))
    names = [e.name for e in result.entries]
    assert "wordpress" in names
    e = next(e for e in result.entries if e.name == "wordpress")
    assert e.version == "latest"
    assert e.source == "ecosystem-cache"


def test_returns_woocommerce_when_present(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    wc = tmp_path / ".cache" / "pirategoat" / "ecosystem" / "woocommerce" / "latest"
    wc.mkdir(parents=True)
    result = EcosystemCacheResolver().resolve(str(tmp_path / "repo"))
    names = [e.name for e in result.entries]
    assert "woocommerce" in names


def test_returns_both_when_both_present(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cache" / "pirategoat" / "ecosystem" / "wordpress" / "latest").mkdir(parents=True)
    (tmp_path / ".cache" / "pirategoat" / "ecosystem" / "woocommerce" / "latest").mkdir(parents=True)
    result = EcosystemCacheResolver().resolve(str(tmp_path / "repo"))
    names = sorted(e.name for e in result.entries)
    assert names == ["woocommerce", "wordpress"]


def test_xdg_cache_home_overrides_default(tmp_path, monkeypatch):
    """When XDG_CACHE_HOME is set, use it instead of ~/.cache."""
    xdg_root = tmp_path / "xdg-cache"
    (xdg_root / "pirategoat" / "ecosystem" / "wordpress" / "latest").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # home exists but no .cache
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_root))
    result = EcosystemCacheResolver().resolve(str(tmp_path / "repo"))
    names = [e.name for e in result.entries]
    assert "wordpress" in names

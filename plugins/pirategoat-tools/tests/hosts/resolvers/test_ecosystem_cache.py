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


class TestResolveForNames:
    """Fulfillment-mode tests — only emit entries for explicitly requested
    names, refresh each via ensure_fresh first, confidence is high."""

    def _stub_ensure_fresh(self, monkeypatch, action="fresh", ok=True):
        """Replace ensure_fresh with a stub that doesn't touch the network."""
        import hosts.resolvers.ecosystem_cache as ec_mod
        calls = []

        def stub(name, max_age_seconds=None):
            calls.append(name)
            return {"name": name, "action": action, "ok": ok, "stderr": ""}

        monkeypatch.setattr(ec_mod, "ensure_fresh", stub)
        return calls

    def test_empty_names_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        ensure_calls = self._stub_ensure_fresh(monkeypatch)
        result = EcosystemCacheResolver().resolve_for_names([])
        assert result.entries == []
        assert result.unresolved == []
        assert ensure_calls == []  # no refresh attempted for empty input

    def test_unknown_names_filtered_out(self, tmp_path, monkeypatch):
        """Names outside _KNOWN_HOSTS (e.g. 'jetpack') are silently ignored."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        ensure_calls = self._stub_ensure_fresh(monkeypatch)
        result = EcosystemCacheResolver().resolve_for_names({"jetpack", "akismet"})
        assert result.entries == []
        assert ensure_calls == []  # no refresh for unknown names

    def test_known_name_with_populated_cache_returns_high_confidence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        wp = tmp_path / ".cache" / "pirategoat" / "ecosystem" / "wordpress" / "latest"
        wp.mkdir(parents=True)
        ensure_calls = self._stub_ensure_fresh(monkeypatch, action="fresh")
        result = EcosystemCacheResolver().resolve_for_names({"wordpress"})
        assert ensure_calls == ["wordpress"]
        assert len(result.entries) == 1
        e = result.entries[0]
        assert e.name == "wordpress"
        assert e.confidence == "high"
        assert e.notes.get("fulfillment") is True
        assert e.notes.get("refresh_action") == "fresh"

    def test_known_name_with_empty_cache_returns_unresolved(self, tmp_path, monkeypatch):
        """ensure_fresh runs but cache slot still missing → unresolved."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        # Cache root exists but the wordpress slot doesn't.
        (tmp_path / ".cache" / "pirategoat" / "ecosystem").mkdir(parents=True)
        self._stub_ensure_fresh(monkeypatch, action="cloned", ok=False)
        result = EcosystemCacheResolver().resolve_for_names({"wordpress"})
        assert result.entries == []
        assert len(result.unresolved) == 1
        item = result.unresolved[0]
        assert item["name"] == "wordpress"
        assert item["reason"] == "cache_unpopulated"

    def test_filters_to_known_hosts_only(self, tmp_path, monkeypatch):
        """Mix of known + unknown names → ensure_fresh called only for known."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        wp = tmp_path / ".cache" / "pirategoat" / "ecosystem" / "wordpress" / "latest"
        wp.mkdir(parents=True)
        ensure_calls = self._stub_ensure_fresh(monkeypatch)
        result = EcosystemCacheResolver().resolve_for_names(
            {"wordpress", "jetpack", "akismet"}
        )
        assert ensure_calls == ["wordpress"]  # only known host refreshed
        names = [e.name for e in result.entries]
        assert names == ["wordpress"]


def test_xdg_cache_home_overrides_default(tmp_path, monkeypatch):
    """When XDG_CACHE_HOME is set, use it instead of ~/.cache."""
    xdg_root = tmp_path / "xdg-cache"
    (xdg_root / "pirategoat" / "ecosystem" / "wordpress" / "latest").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # home exists but no .cache
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_root))
    result = EcosystemCacheResolver().resolve(str(tmp_path / "repo"))
    names = [e.name for e in result.entries]
    assert "wordpress" in names

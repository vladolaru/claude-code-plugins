"""Tests for ecosystem-cache resolver."""

from pathlib import Path

import pytest

from hosts.resolvers.ecosystem_cache import EcosystemCacheResolver


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

    def test_a_fulfilled_entry_carries_the_slot_identity(self, tmp_path, monkeypatch):
        import hosts.resolvers.ecosystem_cache as ec_mod

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        (tmp_path / ".cache" / "pirategoat" / "ecosystem" / "wordpress" / "latest").mkdir(parents=True)
        self._stub_ensure_fresh(monkeypatch, action="pulled")
        monkeypatch.setattr(ec_mod, "slot_identity", lambda name: {
            "present": True,
            "commit": "474555a85c052de90ddd22d4abdf163e678b88ac",
            "branch": "trunk",
            "commit_date": "2026-09-04T18:35:44Z",
            "version": "7.2-alpha-63166-src",
            "refreshed": "2026-09-04T00:04:08Z",
        })

        entry = EcosystemCacheResolver().resolve_for_names({"wordpress"}).entries[0]

        assert entry.version == "7.2-alpha-63166-src"
        assert entry.version_freshness == "2026-09-04T00:04:08Z"
        assert entry.notes["commit"] == "474555a85c052de90ddd22d4abdf163e678b88ac"
        assert "branch" not in entry.notes  # never projected, never shared
        assert entry.notes["commit_date"] == "2026-09-04T18:35:44Z"
        assert entry.notes["refresh_action"] == "pulled"

    def test_an_unreadable_slot_identity_is_none_not_latest(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        (tmp_path / ".cache" / "pirategoat" / "ecosystem" / "wordpress" / "latest").mkdir(parents=True)
        self._stub_ensure_fresh(monkeypatch)

        entry = EcosystemCacheResolver().resolve_for_names({"wordpress"}).entries[0]

        assert entry.version is None
        assert entry.version_freshness is None
        assert entry.notes["commit"] is None

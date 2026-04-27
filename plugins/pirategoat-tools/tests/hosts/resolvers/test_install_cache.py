"""Tests for the per-clone install-cache resolver."""

import pytest

from hosts.install.cache import (
    cache_path_for_clone, clone_id_for, write_stored_lockfile_hash,
)
from hosts.resolvers.install_cache import InstallCacheResolver


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path


class TestInstallCacheResolver:
    def test_emits_library_dep_when_cache_exists(self, cache_env):
        repo = cache_env / "repo"
        repo.mkdir()
        (repo / "composer.lock").write_text("{}")
        cid = clone_id_for(str(repo))
        slot = cache_path_for_clone(cid, "composer")
        slot.mkdir(parents=True)
        (slot / "vendor").mkdir()
        write_stored_lockfile_hash(cid, "composer", "abc123")

        result = InstallCacheResolver().resolve(str(repo))
        assert len(result.entries) == 1
        e = result.entries[0]
        assert e.kind == "library-dep"
        assert e.name == "vendor"  # same name VendorResolver uses, for dedup
        assert e.path == str(slot / "vendor")
        assert e.source == "install-cache"

    def test_silent_when_no_cache(self, cache_env):
        repo = cache_env / "repo"
        repo.mkdir()
        (repo / "composer.lock").write_text("{}")  # lockfile but no cache yet
        result = InstallCacheResolver().resolve(str(repo))
        assert result.entries == []
        assert result.unresolved == []  # do not yell — VendorResolver handles missing

    def test_silent_when_no_lockfile(self, cache_env):
        repo = cache_env / "repo"
        repo.mkdir()
        result = InstallCacheResolver().resolve(str(repo))
        assert result.entries == []

    def test_silent_when_marker_present_without_artifact_dir(self, cache_env):
        """Marker exists but the artifact dir was removed (partial cleanup) →
        gate's is_dir() half rejects. Locks both halves of the populated check."""
        repo = cache_env / "repo"
        repo.mkdir()
        (repo / "composer.lock").write_text("{}")
        cid = clone_id_for(str(repo))
        write_stored_lockfile_hash(cid, "composer", "abc123")  # marker, no vendor/
        result = InstallCacheResolver().resolve(str(repo))
        assert result.entries == []

    def test_emits_pnpm_node_modules_path(self, cache_env):
        repo = cache_env / "repo"
        repo.mkdir()
        (repo / "pnpm-lock.yaml").write_text("lockfileVersion: 9.0\n")
        cid = clone_id_for(str(repo))
        slot = cache_path_for_clone(cid, "pnpm")
        slot.mkdir(parents=True)
        (slot / "node_modules").mkdir()
        write_stored_lockfile_hash(cid, "pnpm", "def456")

        result = InstallCacheResolver().resolve(str(repo))
        assert len(result.entries) == 1
        assert result.entries[0].name == "node_modules"
        assert result.entries[0].path == str(slot / "node_modules")

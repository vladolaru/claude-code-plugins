"""Tests for the per-clone install-cache resolver."""

import pytest

from hosts.install.cache import (
    cache_path_for_clone, clone_id_for, write_selected_slots,
    write_stored_inputs_hash,
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
        write_stored_inputs_hash(cid, "composer", "abc123")

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
        write_stored_inputs_hash(cid, "composer", "abc123")  # marker, no vendor/
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
        write_stored_inputs_hash(cid, "pnpm", "def456")

        result = InstallCacheResolver().resolve(str(repo))
        assert len(result.entries) == 1
        assert result.entries[0].name == "node_modules"
        assert result.entries[0].path == str(slot / "node_modules")

    @staticmethod
    def _populate(cid, slot, artifact):
        slot_path = cache_path_for_clone(cid, slot)
        slot_path.mkdir(parents=True)
        (slot_path / artifact).mkdir()
        write_stored_inputs_hash(cid, slot, "hash-" + slot)
        return slot_path

    def test_selection_marker_limits_exposure_to_the_current_slots(self, cache_env):
        """The clone root accumulates every slot ever populated. After a repo
        migrates npm→pnpm, the obsolete npm slot sorts first and its
        node_modules would shadow the current slot through kind:name dedup —
        only the recorded selection may surface."""
        repo = cache_env / "repo"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        self._populate(cid, "npm", "node_modules")   # historical
        pnpm_slot = self._populate(cid, "pnpm", "node_modules")  # current
        write_selected_slots(cid, [
            {"slot": "pnpm", "manager": "pnpm", "rel_path": "."},
        ])

        result = InstallCacheResolver().resolve(str(repo))

        assert len(result.entries) == 1
        assert result.entries[0].path == str(pnpm_slot / "node_modules")

    def test_empty_selection_exposes_nothing(self, cache_env):
        """A run that detected no roots supersedes older populated slots."""
        repo = cache_env / "repo"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        self._populate(cid, "composer", "vendor")
        write_selected_slots(cid, [])

        result = InstallCacheResolver().resolve(str(repo))

        assert result.entries == []

    def test_selected_but_unpopulated_slot_emits_nothing(self, cache_env):
        """Selection records intent; only the populated gate grants entries
        (a failed install stays invisible, as before)."""
        repo = cache_env / "repo"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        write_selected_slots(cid, [
            {"slot": "composer", "manager": "composer", "rel_path": "."},
        ])
        # Ensure the clone root exists so the resolver proceeds past is_dir().
        cache_path_for_clone(cid, "composer").mkdir(parents=True)

        result = InstallCacheResolver().resolve(str(repo))

        assert result.entries == []

    def test_no_marker_falls_back_to_enumerating_populated_slots(self, cache_env):
        """Pre-marker caches and standalone chain runs keep working."""
        repo = cache_env / "repo"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        slot = self._populate(cid, "composer", "vendor")

        result = InstallCacheResolver().resolve(str(repo))

        assert len(result.entries) == 1
        assert result.entries[0].path == str(slot / "vendor")

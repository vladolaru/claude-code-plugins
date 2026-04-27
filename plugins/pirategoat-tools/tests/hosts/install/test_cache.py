"""Tests for install cache layer."""

import os

import pytest

from hosts.install.cache import (
    clone_id_for,
    cache_path_for_clone,
    read_stored_lockfile_hash,
    write_stored_lockfile_hash,
    read_clone_realpath,
    write_clone_realpath,
)


class TestCloneId:
    def test_returns_16_char_hex(self, tmp_path):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        assert len(cid) == 16
        assert all(c in "0123456789abcdef" for c in cid)

    def test_deterministic_per_realpath(self, tmp_path):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        assert clone_id_for(str(repo)) == clone_id_for(str(repo))

    def test_resolves_symlinks(self, tmp_path):
        real = tmp_path / "real-repo"
        real.mkdir()
        link = tmp_path / "link-to-repo"
        link.symlink_to(real)
        assert clone_id_for(str(link)) == clone_id_for(str(real))

    def test_paths_with_hyphens_do_not_collide(self, tmp_path):
        # Regression: prior slug-based id collapsed "-" and "/" indistinguishably.
        a = tmp_path / "woocommerce-payments"
        b = tmp_path / "woocommerce" / "payments"
        a.mkdir()
        b.mkdir(parents=True)
        assert clone_id_for(str(a)) != clone_id_for(str(b))


class TestCachePathForClone:
    def test_path_layout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "r"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        path = cache_path_for_clone(cid, "composer")
        # <XDG>/pirategoat/library-deps/<clone_id>/<manager>
        assert path.parent.name == cid
        assert path.name == "composer"
        assert "pirategoat/library-deps" in str(path)


class TestLockfileMarker:
    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "r"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        slot = cache_path_for_clone(cid, "pnpm")
        slot.mkdir(parents=True)
        write_stored_lockfile_hash(cid, "pnpm", "abc123")
        assert read_stored_lockfile_hash(cid, "pnpm") == "abc123"

    def test_returns_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "r"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        assert read_stored_lockfile_hash(cid, "composer") is None


class TestRealpathMarker:
    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "myrepo"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        write_clone_realpath(cid, str(repo))
        assert read_clone_realpath(cid) == os.path.realpath(str(repo))

    def test_returns_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        # No write_clone_realpath called
        assert read_clone_realpath("deadbeef0badbeef") is None


class TestEnsureCurrent:
    def test_no_op_when_hash_matches(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "r"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        slot = cache_path_for_clone(cid, "composer")
        slot.mkdir(parents=True)
        (slot / "vendor").mkdir()
        (slot / "vendor" / "marker.txt").write_text("kept")
        write_stored_lockfile_hash(cid, "composer", "abc123")

        install_calls = []
        def fake_install(staging_path):
            install_calls.append(staging_path)

        from hosts.install.cache import ensure_current
        result = ensure_current(str(repo), "composer", "abc123", fake_install)

        assert install_calls == []  # install_fn not called
        assert (slot / "vendor" / "marker.txt").read_text() == "kept"
        assert result.action == "cache_hit"
        assert result.cache_path == slot

    def test_rmtree_and_reinstall_when_hash_differs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "r"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        slot = cache_path_for_clone(cid, "composer")
        slot.mkdir(parents=True)
        (slot / "vendor").mkdir()
        (slot / "vendor" / "stale.txt").write_text("OLD")
        write_stored_lockfile_hash(cid, "composer", "old-hash")

        def fake_install(staging_path):
            (staging_path / "vendor").mkdir(exist_ok=True)
            (staging_path / "vendor" / "fresh.txt").write_text("NEW")

        from hosts.install.cache import ensure_current
        result = ensure_current(str(repo), "composer", "new-hash", fake_install)

        assert result.action == "replaced"
        assert not (slot / "vendor" / "stale.txt").exists()
        assert (slot / "vendor" / "fresh.txt").read_text() == "NEW"
        assert read_stored_lockfile_hash(cid, "composer") == "new-hash"

    def test_first_install_when_slot_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "r"
        repo.mkdir()
        cid = clone_id_for(str(repo))

        def fake_install(staging_path):
            (staging_path / "vendor").mkdir()

        from hosts.install.cache import ensure_current
        result = ensure_current(str(repo), "composer", "abc123", fake_install)

        assert result.action == "installed"
        assert read_stored_lockfile_hash(cid, "composer") == "abc123"

    def test_install_failure_preserves_prior_cache(self, tmp_path, monkeypatch):
        """If install_fn raises during a *replace*, the prior good cache survives."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "r"
        repo.mkdir()
        cid = clone_id_for(str(repo))

        # Set up a previously-good cache for "old-hash"
        slot = cache_path_for_clone(cid, "composer")
        slot.mkdir(parents=True)
        (slot / "vendor").mkdir()
        (slot / "vendor" / "good.txt").write_text("PRIOR")
        write_stored_lockfile_hash(cid, "composer", "old-hash")

        def fake_install(staging_path):
            raise RuntimeError("install boom")

        from hosts.install.cache import ensure_current
        with pytest.raises(RuntimeError, match="install boom"):
            ensure_current(str(repo), "composer", "new-hash", fake_install)

        # Prior cache + marker still intact
        assert (slot / "vendor" / "good.txt").read_text() == "PRIOR"
        assert read_stored_lockfile_hash(cid, "composer") == "old-hash"
        # Staging dir cleaned up — no .composer.staging.* siblings remain
        siblings = list((slot.parent).iterdir())
        assert all(not s.name.startswith(".composer.staging") for s in siblings)

    def test_install_failure_first_time_leaves_slot_absent(self, tmp_path, monkeypatch):
        """If install_fn raises during a *first install*, no slot or marker exists."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "r"
        repo.mkdir()
        cid = clone_id_for(str(repo))

        def fake_install(staging_path):
            raise RuntimeError("install boom")

        from hosts.install.cache import ensure_current
        with pytest.raises(RuntimeError, match="install boom"):
            ensure_current(str(repo), "composer", "abc123", fake_install)

        assert not cache_path_for_clone(cid, "composer").exists()
        assert read_stored_lockfile_hash(cid, "composer") is None

    def test_writes_realpath_marker_on_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "r"
        repo.mkdir()
        cid = clone_id_for(str(repo))

        def fake_install(staging_path):
            (staging_path / "vendor").mkdir()

        from hosts.install.cache import ensure_current, read_clone_realpath
        ensure_current(str(repo), "composer", "abc123", fake_install)
        assert read_clone_realpath(cid) == os.path.realpath(str(repo))


class TestPruneDeadClones:
    def test_removes_entry_for_deleted_clone(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        from hosts.install.cache import (
            cache_path_for_clone, clone_id_for, prune_dead_clones,
            write_clone_realpath, write_stored_lockfile_hash,
        )

        # Live clone — realpath still exists
        live = tmp_path / "live"
        live.mkdir()
        live_id = clone_id_for(str(live))
        cache_path_for_clone(live_id, "composer").mkdir(parents=True)
        write_clone_realpath(live_id, str(live))
        write_stored_lockfile_hash(live_id, "composer", "abc")

        # Dead clone — set up cache for a path that we then delete
        dead = tmp_path / "dead"
        dead.mkdir()
        dead_id = clone_id_for(str(dead))
        cache_path_for_clone(dead_id, "composer").mkdir(parents=True)
        cache_path_for_clone(dead_id, "pnpm").mkdir(parents=True)
        write_clone_realpath(dead_id, str(dead))
        write_stored_lockfile_hash(dead_id, "composer", "abc")
        write_stored_lockfile_hash(dead_id, "pnpm", "def")
        # Now delete the dead clone — its .realpath marker still points there
        import shutil
        shutil.rmtree(dead)

        removed = prune_dead_clones()
        assert dead_id in removed
        assert live_id not in removed
        assert cache_path_for_clone(live_id, "composer").is_dir()
        assert not cache_path_for_clone(dead_id, "composer").exists()
        assert not cache_path_for_clone(dead_id, "pnpm").exists()

    def test_paths_with_hyphens_not_falsely_pruned(self, tmp_path, monkeypatch):
        """Regression: paths containing '-' must not be misread as deleted."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        from hosts.install.cache import (
            cache_path_for_clone, clone_id_for, prune_dead_clones,
            write_clone_realpath,
        )
        # A real, existing path with a hyphen — mimics
        # ~/Work/a8c/.duplicates/woocommerce-payments
        repo = tmp_path / "woocommerce-payments"
        repo.mkdir()
        cid = clone_id_for(str(repo))
        cache_path_for_clone(cid, "composer").mkdir(parents=True)
        write_clone_realpath(cid, str(repo))

        removed = prune_dead_clones()
        assert cid not in removed
        assert cache_path_for_clone(cid, "composer").is_dir()

    def test_skips_entries_with_no_realpath_marker(self, tmp_path, monkeypatch):
        """Entries lacking a .realpath marker are left alone (conservative)."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        from hosts.install.cache import _cache_root, prune_dead_clones
        cache_root = _cache_root()
        cache_root.mkdir(parents=True)
        # Manually-created entry with no .realpath marker (e.g., from old layout)
        (cache_root / "abcdef0123456789").mkdir()

        removed = prune_dead_clones()
        assert "abcdef0123456789" not in removed
        assert (cache_root / "abcdef0123456789").is_dir()

    def test_max_scan_bound(self, tmp_path, monkeypatch):
        """Scan stops at max_scan even with many entries to avoid runaway cost."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        from hosts.install.cache import _cache_root, prune_dead_clones
        cache_root = _cache_root()
        cache_root.mkdir(parents=True)
        # 100 dead-clone entries with .realpath markers pointing nowhere
        nonexistent = str(tmp_path / "doesnotexist")
        for i in range(100):
            entry = cache_root / f"deadbeef{i:08x}"
            entry.mkdir()
            (entry / ".realpath").write_text(f"{nonexistent}-{i}")

        removed = prune_dead_clones(max_scan=10)
        assert len(removed) == 10  # only first 10 inspected & removed

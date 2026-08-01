"""Contract tests for the hosts/ containment invariant.

Everything under scripts/hosts/ obeys two invariants:
  I1. A review never modifies the reviewed working tree.
  I2. Nothing outside the repo's resolved path is read as an install input
      or used as an execution directory.
containment.py is the single enforcement point; this module tests the
primitives, guards against reimplementation drift, and proves I1
end-to-end against the installer.
"""

import contextlib
import hashlib
import io
import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from hosts.install.containment import contains, contains_lexically, resolve_inside


class TestContains:
    def test_path_inside_repo_is_contained(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        assert contains(str(repo), str(repo / "src"))

    def test_repo_itself_is_contained(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        assert contains(str(repo), str(repo))

    def test_sibling_directory_is_not_contained(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (tmp_path / "other").mkdir()
        assert not contains(str(repo), str(tmp_path / "other"))

    def test_symlink_escaping_the_repo_is_not_contained(self, tmp_path):
        external = tmp_path / "external"
        external.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        os.symlink(str(external), str(repo / "link"))
        assert not contains(str(repo), str(repo / "link"))

    def test_in_repo_symlink_is_contained(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "real").mkdir(parents=True)
        os.symlink(str(repo / "real"), str(repo / "alias"))
        assert contains(str(repo), str(repo / "alias"))

    def test_repo_accessed_via_symlink_still_contains_its_children(self, tmp_path):
        real_repo = tmp_path / "real-repo"
        (real_repo / "src").mkdir(parents=True)
        linked = tmp_path / "linked-repo"
        os.symlink(str(real_repo), str(linked))
        assert contains(str(linked), str(linked / "src"))

    def test_name_prefix_sibling_is_not_contained(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (tmp_path / "repo-extra").mkdir()
        assert not contains(str(repo), str(tmp_path / "repo-extra"))


class TestResolveInside:
    def test_relative_path_resolves_to_absolute_inside(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "a").mkdir(parents=True)
        (repo / "a" / "f.txt").write_text("x")
        resolved = resolve_inside(str(repo), "a/f.txt")
        assert resolved == str((repo / "a" / "f.txt").resolve())

    def test_traversal_escape_returns_none(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (tmp_path / "secret.txt").write_text("s")
        assert resolve_inside(str(repo), "../secret.txt") is None

    def test_symlink_escape_returns_none(self, tmp_path):
        external = tmp_path / "external"
        external.mkdir()
        (external / "f.txt").write_text("x")
        repo = tmp_path / "repo"
        repo.mkdir()
        os.symlink(str(external), str(repo / "link"))
        assert resolve_inside(str(repo), "link/f.txt") is None

    def test_nonexistent_path_still_resolves_lexically_inside(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        resolved = resolve_inside(str(repo), "not/yet/here.txt")
        assert resolved is not None
        assert resolved.startswith(str(repo.resolve()))


class TestContainsLexically:
    def test_bounds_a_walk_without_touching_the_filesystem(self, tmp_path):
        repo = tmp_path / "repo"  # never created — lexical only
        assert contains_lexically(str(repo), str(repo / "a" / "b"))
        assert not contains_lexically(str(repo), str(tmp_path / "other"))

    def test_does_not_resolve_symlinks(self, tmp_path):
        external = tmp_path / "external"
        external.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        os.symlink(str(external), str(repo / "link"))
        # Lexically inside even though it resolves outside — which is why
        # this primitive must never be a trust decision on its own.
        assert contains_lexically(str(repo), str(repo / "link"))

    def test_lexical_mixed_forms_fail_closed(self):
        """ValueError inside the prefix check (mixed relative/absolute)
        must mean 'not contained', never an exception or True."""
        assert not contains_lexically("relative/repo", "/absolute/candidate")


class TestDriftGuard:
    # Spellings with unambiguous containment intent and ZERO legitimate
    # uses under scripts/hosts/ today — the bans stay allowlist-free, so a
    # hit is always a real re-derivation, never a false positive to wave
    # through. General primitives (realpath, startswith, relpath) are
    # deliberately NOT banned: they have many non-containment uses here
    # (cache identity, YAML parsing, path-spelling classification) and a
    # ban would breed an allowlist that decays into ritual.
    _BANNED_SPELLINGS = ("commonpath", "is_relative_to", "commonprefix")

    def test_containment_spellings_are_centralized(self):
        """Every containment decision under scripts/hosts/ goes through
        containment.py. A new inline check is exactly how the
        symlinked-dep-root and escaped-bin-dir bypasses were born, and a
        realpath+startswith variant in the wp-env resolver survived the
        first consolidation pass because only commonpath was banned.
        Catches the commonpath, is_relative_to, and commonprefix spellings
        specifically; other re-derivations (startswith, relpath) rely on
        code review — the resolver symlink behavior tests pin the outcomes
        those spellings would have to reproduce."""
        hosts_dir = Path(__file__).parents[2] / "scripts" / "hosts"
        offenders = [
            f"{path.relative_to(hosts_dir)}: {spelling}"
            for path in sorted(hosts_dir.rglob("*.py"))
            if path.name != "containment.py"
            for spelling in self._BANNED_SPELLINGS
            if spelling in path.read_text()
        ]
        assert offenders == []


def _tree_snapshot(root: Path) -> dict:
    # Files-only walk: empty dirs and dir-symlinks are invisible to the
    # snapshot, so the fake must keep writing a file into every directory
    # it creates for the diff to see a worktree escape.
    snapshot = {}
    for dirpath, _dirnames, filenames in os.walk(str(root)):
        for name in filenames:
            path = Path(dirpath) / name
            rel = str(path.relative_to(root))
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


class TestWorktreeImmutability:
    def test_ensure_installed_never_touches_the_reviewed_worktree(
        self, tmp_path, monkeypatch,
    ):
        """Invariant I1 proven end to end: run the installer over a repo
        that exercises every write-redirect edge — a composer root with
        config.bin-dir configured OUTSIDE vendor, a nested composer root,
        and an npm root — with a fake package manager that honors env
        redirects exactly like the real one. If any redirect is removed,
        the fake writes into the repo and the snapshot diff fails."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        repo = tmp_path / "repo"
        (repo / "plugins" / "woocommerce").mkdir(parents=True)
        (repo / "composer.json").write_text(json.dumps({
            "config": {"bin-dir": "bin", "cache-dir": ".composer-cache"},
        }))
        (repo / "composer.lock").write_text("{}")
        (repo / "plugins" / "woocommerce" / "composer.json").write_text("{}")
        (repo / "plugins" / "woocommerce" / "composer.lock").write_text("{}")
        (repo / "package.json").write_text("{}")
        (repo / "package-lock.json").write_text("{}")
        (repo / ".npmrc").write_text("registry=https://registry.example.test\n")
        before = _tree_snapshot(repo)

        def fake_run(cmd, **kwargs):
            env = kwargs["env"]
            cwd = kwargs["cwd"]
            if cmd[0] == "composer":
                assert "--no-scripts" in cmd  # repo scripts are the biggest worktree-write vector
                vendor = env.get("COMPOSER_VENDOR_DIR") or os.path.join(cwd, "vendor")
                config = json.loads(
                    Path(cwd, "composer.json").read_text()
                )
                # Real composer precedence: COMPOSER_BIN_DIR env, then
                # config.bin-dir (relative to the project root), then
                # {vendor-dir}/bin. Modeling config.bin-dir is what makes
                # the escaped-bin-dir edge non-vacuous: drop the env
                # redirect and this writes bin/phpunit into the repo.
                bin_dir = env.get("COMPOSER_BIN_DIR")
                if not bin_dir:
                    configured = config.get("config", {}).get("bin-dir")
                    bin_dir = (
                        os.path.join(cwd, configured) if configured
                        else os.path.join(vendor, "bin")
                    )
                configured_cache = config.get("config", {}).get("cache-dir")
                cache_dir = env.get("COMPOSER_CACHE_DIR") or (
                    os.path.join(cwd, configured_cache) if configured_cache
                    else os.path.expanduser("~/.cache/composer")
                )
                os.makedirs(vendor, exist_ok=True)
                os.makedirs(bin_dir, exist_ok=True)
                os.makedirs(cache_dir, exist_ok=True)
                Path(vendor, "autoload.php").write_text("<?php\n")
                Path(bin_dir, "phpunit").write_text("#!/bin/sh\n")
                Path(cache_dir, "packages.json").write_text("{}")
            else:
                assert "--ignore-scripts" in cmd  # repo scripts are the biggest worktree-write vector
                os.makedirs(os.path.join(cwd, "node_modules"), exist_ok=True)
                Path(cwd, "node_modules", ".package-lock.json").write_text("{}")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr="",
            )

        from hosts.ensure_installed import main as ensure_installed_main

        with mock.patch(
            "hosts.ensure_installed.subprocess.run", side_effect=fake_run,
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ensure_installed_main([
                    "--repo", str(repo),
                    "--scope-path", "plugins/woocommerce/src/File.php",
                ])

        assert rc == 0
        payload = json.loads(buf.getvalue())
        statuses = {m["status"] for m in payload["managers"]}
        assert len(payload["managers"]) == 3, payload  # root composer + npm + nested composer
        assert statuses == {"ok"}, payload  # not vacuous — installs really ran
        assert _tree_snapshot(repo) == before

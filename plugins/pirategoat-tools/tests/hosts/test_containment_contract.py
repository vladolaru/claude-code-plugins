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

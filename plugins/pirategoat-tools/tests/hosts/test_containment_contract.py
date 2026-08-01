"""Contract tests for the hosts/ containment invariant.

Host-context resolution never trusts paths outside the repo's resolved root;
containment.py is the single enforcement point.
"""

import os
from pathlib import Path

from hosts.containment import contains, contains_lexically, resolve_inside


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

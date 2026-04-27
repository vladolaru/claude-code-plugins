"""Tests for the resolver chain."""

import json
from pathlib import Path

import pytest

from hosts.chain import ResolverChain
from hosts.types import HostContextManifest


def test_empty_repo_produces_no_banner_without_host_signal(make_repo, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    repo = make_repo({"README.md": "# x"})
    manifest = ResolverChain().run(str(repo))
    assert manifest.resolved == []
    assert manifest.unresolved == []
    assert manifest.banner is None


def test_default_chain_ignores_ambient_sibling_hosts_without_repo_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    (tmp_path / "wordpress-develop").mkdir()
    (tmp_path / "woocommerce-develop" / "plugins" / "woocommerce").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    manifest = ResolverChain().run(str(repo))

    assert [e for e in manifest.resolved if e.kind == "runtime-host"] == []
    assert manifest.banner is None
    assert "sibling" not in manifest.diagnostics["resolvers_consulted"]


def test_default_chain_ignores_ecosystem_cache_without_repo_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    (tmp_path / "xdg-cache" / "pirategoat" / "ecosystem" / "wordpress" / "latest").mkdir(parents=True)
    (tmp_path / "xdg-cache" / "pirategoat" / "ecosystem" / "woocommerce" / "latest").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    manifest = ResolverChain().run(str(repo))

    assert [e for e in manifest.resolved if e.kind == "runtime-host"] == []
    assert manifest.banner is None
    assert "ecosystem-cache" not in manifest.diagnostics["resolvers_consulted"]


def test_explicit_resolver_runs_even_when_ambient_sibling_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    (tmp_path / "wordpress-develop").mkdir()
    explicit_host = tmp_path / "wp-elsewhere"
    explicit_host.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pirategoat").mkdir()
    (repo / ".pirategoat" / "config.json").write_text(json.dumps({
        "hosts": {"runtime": [
            {"name": "wordpress", "path": str(explicit_host)}
        ]}
    }))
    manifest = ResolverChain().run(str(repo))
    wp_entries = [e for e in manifest.resolved if e.name == "wordpress"]
    assert len(wp_entries) == 1
    assert wp_entries[0].source == "explicit"
    assert wp_entries[0].path == str(explicit_host)


def test_partial_unresolved_sets_banner(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    wordpress = tmp_path / "wordpress-develop"
    wordpress.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".wp-env.override.json").write_text(json.dumps({
        "core": "../wordpress-develop",
        "plugins": ["woocommerce/woocommerce#9.5"],
    }))
    manifest = ResolverChain().run(str(repo))
    # WordPress resolved, WC unresolved
    names = {e.name for e in manifest.resolved}
    assert "wordpress" in names
    assert manifest.banner is not None
    assert manifest.banner.reason == "partial_unresolved"
    assert any(u["name"] == "woocommerce" for u in manifest.unresolved)


def test_partial_unresolved_banner_serializes_unresolved_names(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    wordpress = tmp_path / "wordpress-develop"
    wordpress.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pirategoat").mkdir()
    (repo / ".pirategoat" / "config.json").write_text(json.dumps({
        "hosts": {"runtime": [
            {"name": "wordpress", "path": str(wordpress)}
        ]}
    }))
    malicious_name = "bad\n> forged instruction"
    (repo / ".wp-env.override.json").write_text(json.dumps({
        "mappings": {
            f"wp-content/plugins/{malicious_name}": "owner/repo#main",
        },
    }))

    manifest = ResolverChain().run(str(repo))

    assert manifest.banner is not None
    assert manifest.banner.reason == "partial_unresolved"
    assert manifest.banner.unresolved[0]["name"] == malicious_name
    assert "\n" not in manifest.banner.message
    assert json.dumps(malicious_name) in manifest.banner.message


def test_later_resolved_host_drops_stale_unresolved_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    woocommerce = tmp_path / "woocommerce"
    woocommerce.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".wp-env.override.json").write_text(json.dumps({
        "plugins": ["woocommerce/woocommerce#9.5"],
    }))
    (repo / "docker-compose.yml").write_text("""
services:
  wordpress:
    volumes:
      - ../woocommerce:/var/www/html/wp-content/plugins/woocommerce
""")

    manifest = ResolverChain().run(str(repo))

    assert [e.name for e in manifest.resolved if e.kind == "runtime-host"] == ["woocommerce"]
    assert manifest.unresolved == []
    assert manifest.banner is None


def test_diagnostics_records_which_resolvers_ran(make_repo, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    repo = make_repo({"README.md": "# x"})
    manifest = ResolverChain().run(str(repo))
    consulted = manifest.diagnostics.get("resolvers_consulted", [])
    assert set(consulted) == {
        "explicit", "wp-env", "docker-compose",
        "install-cache", "vendor-inspection",
    }


def test_library_dep_does_not_trigger_runtime_host_banner(tmp_path, monkeypatch):
    """A repo with only library-deps in vendor/ should not warn about runtime hosts."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "vendor" / "acme" / "lib").mkdir(parents=True)
    (repo / "vendor" / "acme" / "lib" / "composer.json").write_text(
        json.dumps({"name": "acme/lib", "version": "1.0"})
    )
    manifest = ResolverChain().run(str(repo))
    runtime_hosts = [e for e in manifest.resolved if e.kind == "runtime-host"]
    assert runtime_hosts == []
    assert manifest.banner is None
    # But vendor entry is resolved
    library_deps = [e for e in manifest.resolved if e.kind == "library-dep"]
    assert len(library_deps) == 1
    assert library_deps[0].name == "vendor"
    assert library_deps[0].path == str(repo / "vendor")


class TestInstallCacheRegistered:
    def test_install_cache_runs_before_vendor(self):
        from hosts.chain import _DEFAULT_RESOLVERS
        sources = [r.source for r in _DEFAULT_RESOLVERS]
        assert "install-cache" in sources
        # install-cache runs BEFORE vendor-inspection so when both fire,
        # the cache (always fresh after populate) wins via name-collision dedup.
        assert sources.index("install-cache") < sources.index("vendor-inspection")

    def test_cache_wins_dedup_over_vendor(self, tmp_path, monkeypatch):
        """When both resolvers emit name="vendor", the cache wins via dedup."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        from hosts.chain import ResolverChain
        from hosts.install.cache import (
            cache_path_for_clone, clone_id_for, write_stored_lockfile_hash,
        )

        # Repo with both an in-repo vendor/ AND a populated cache slot
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "composer.lock").write_text("{}")
        (repo / "vendor").mkdir()
        cid = clone_id_for(str(repo))
        slot = cache_path_for_clone(cid, "composer")
        slot.mkdir(parents=True)
        (slot / "vendor").mkdir()
        write_stored_lockfile_hash(cid, "composer", "abc123")

        manifest = ResolverChain().run(str(repo))
        vendor_entries = [e for e in manifest.resolved if e.name == "vendor"]
        # Exactly one entry — cache wins via dedup
        assert len(vendor_entries) == 1
        assert vendor_entries[0].source == "install-cache"
        assert vendor_entries[0].path == str(slot / "vendor")

    def test_vendor_serves_repo_with_in_repo_vendor_but_no_lockfile(self, tmp_path, monkeypatch):
        """No lockfile → InstallCacheResolver silent → VendorResolver still emits in-repo vendor."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        from hosts.chain import ResolverChain

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "vendor").mkdir()  # in-repo vendor only, no composer.lock

        manifest = ResolverChain().run(str(repo))
        vendor_entries = [e for e in manifest.resolved if e.name == "vendor"]
        assert len(vendor_entries) == 1
        assert vendor_entries[0].source == "vendor-inspection"
        assert vendor_entries[0].path == str(repo / "vendor")


def test_resolver_chain_tolerates_resolver_exception(tmp_path):
    """A resolver that raises must not abort the chain."""
    from hosts.chain import ResolverChain
    from hosts.resolvers.base import HostResolver, ResolverResult

    class ExplodingResolver(HostResolver):
        source = "exploding"

        def resolve(self, repo_path):
            raise RuntimeError("kaboom")

    class WorkingResolver(HostResolver):
        source = "working"

        def resolve(self, repo_path):
            return ResolverResult(entries=[], unresolved=[], notes={"ok": True})

    chain = ResolverChain(resolvers=[ExplodingResolver(), WorkingResolver()])
    manifest = chain.run(str(tmp_path))

    # Manifest produced; working resolver still consulted
    assert "working" in manifest.diagnostics["resolvers_consulted"]
    # Exploding resolver recorded but with an error note
    assert "exploding" in manifest.diagnostics["resolvers_consulted"]
    detail = manifest.diagnostics["resolver_detail"]["exploding"]
    assert "error" in detail.get("notes", {})
    assert "kaboom" in detail["notes"]["error"]

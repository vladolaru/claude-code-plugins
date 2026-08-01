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
    # Block cache fulfillment from rescuing the unresolved woocommerce entry —
    # this test verifies the partial-unresolved banner path, not fulfillment.
    monkeypatch.setattr(
        "hosts.cache.manager.update_host",
        lambda name: {"name": name, "action": "cloned", "ok": False, "stderr": "blocked in test"},
    )
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
        "explicit", "wp-env", "docker-compose", "plugin-headers",
        "vendor-inspection",
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


class TestCacheFulfillment:
    """The chain's post-loop fulfillment pass promotes unresolved →
    resolved when the cache can satisfy the name."""

    def _block_network(self, monkeypatch):
        """Prevent any test in this class from doing real git pulls."""
        monkeypatch.setattr(
            "hosts.cache.manager.update_host",
            lambda name: {"name": name, "action": "cloned", "ok": False, "stderr": "blocked"},
        )

    def _stub_update_to_populate(self, monkeypatch, cache_root_dir):
        """Make update_host populate the slot so resolve_for_names succeeds."""
        import time as _time

        def fake_update(name):
            slot = cache_root_dir / "pirategoat" / "ecosystem" / name / "latest"
            slot.mkdir(parents=True, exist_ok=True)
            (slot / ".last_updated").write_text(str(int(_time.time())))
            (slot / "wp-config-sample.php").write_text("<?php")
            return {"name": name, "action": "cloned", "ok": True, "stderr": ""}

        monkeypatch.setattr("hosts.cache.manager.update_host", fake_update)

    def test_fulfillment_promotes_unresolved_to_resolved(self, tmp_path, monkeypatch):
        """vendored_self_mount of WP core → cache populated → wordpress
        ends up in resolved with source='ecosystem-cache'."""
        cache_root_dir = tmp_path / "xdg-cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        self._stub_update_to_populate(monkeypatch, cache_root_dir)

        repo = tmp_path / "wcpay"
        repo.mkdir()
        (repo / "docker" / "wordpress").mkdir(parents=True)
        (repo / "docker-compose.yml").write_text("""
services:
  wordpress:
    volumes:
      - ./docker/wordpress:/var/www/html
""")
        manifest = ResolverChain().run(str(repo))

        wp_entries = [e for e in manifest.resolved if e.name == "wordpress"]
        assert len(wp_entries) == 1
        e = wp_entries[0]
        assert e.source == "ecosystem-cache"
        assert e.confidence == "high"
        assert e.notes.get("fulfillment") is True
        # Unresolved cleared, no banner
        assert manifest.unresolved == []
        assert manifest.banner is None

    def test_fulfillment_records_diagnostics(self, tmp_path, monkeypatch):
        """When fulfillment fires, diagnostics record what was fulfilled."""
        cache_root_dir = tmp_path / "xdg-cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        self._stub_update_to_populate(monkeypatch, cache_root_dir)

        repo = tmp_path / "wcpay"
        repo.mkdir()
        (repo / "docker" / "wordpress").mkdir(parents=True)
        (repo / "docker-compose.yml").write_text("""
services:
  wordpress:
    volumes:
      - ./docker/wordpress:/var/www/html
""")
        manifest = ResolverChain().run(str(repo))

        consulted = manifest.diagnostics["resolvers_consulted"]
        assert "ecosystem-cache-fulfillment" in consulted
        detail = manifest.diagnostics["resolver_detail"]["ecosystem-cache-fulfillment"]
        assert detail["entries"] == 1
        assert detail["notes"]["fulfilled"] == ["wordpress"]

    def test_no_fulfillment_when_repo_signals_nothing(self, tmp_path, monkeypatch):
        """Empty repo + populated cache → no fulfillment, no leakage."""
        cache_root_dir = tmp_path / "xdg-cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        # Pre-populate cache (no fulfillment should still skip it)
        wp = cache_root_dir / "pirategoat" / "ecosystem" / "wordpress" / "latest"
        wp.mkdir(parents=True)
        self._block_network(monkeypatch)

        repo = tmp_path / "repo"
        repo.mkdir()
        manifest = ResolverChain().run(str(repo))

        runtime_hosts = [e for e in manifest.resolved if e.kind == "runtime-host"]
        assert runtime_hosts == []
        assert "ecosystem-cache-fulfillment" not in manifest.diagnostics["resolvers_consulted"]

    def test_fulfillment_skipped_when_higher_priority_resolver_won(self, tmp_path, monkeypatch):
        """wp-env declares woocommerce remote (unresolved), docker-compose
        also resolves woocommerce locally. Pre-filter must skip fulfillment
        for woocommerce since seen_names already has it — no network call."""
        cache_root_dir = tmp_path / "xdg-cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

        update_calls = []
        monkeypatch.setattr(
            "hosts.cache.manager.update_host",
            lambda name: update_calls.append(name) or {"ok": True, "action": "fresh"},
        )

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

        # docker-compose's woocommerce wins; fulfillment skipped via pre-filter.
        wc_entries = [e for e in manifest.resolved if e.name == "woocommerce"]
        assert len(wc_entries) == 1
        assert wc_entries[0].source == "docker-compose"
        assert update_calls == []  # no ensure_fresh / update_host calls

    def test_fulfillment_falls_back_to_banner_when_cache_unpopulated(self, tmp_path, monkeypatch):
        """vendored_self_mount of WP + cache empty + offline → wordpress
        stays unresolved, banner fires."""
        cache_root_dir = tmp_path / "xdg-cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        # update_host fails (offline) and doesn't populate the slot.
        monkeypatch.setattr(
            "hosts.cache.manager.update_host",
            lambda name: {"name": name, "action": "cloned", "ok": False, "stderr": "offline"},
        )
        # Cache root exists so resolve_for_names doesn't take the missing-root path.
        (cache_root_dir / "pirategoat" / "ecosystem").mkdir(parents=True)

        repo = tmp_path / "wcpay"
        repo.mkdir()
        (repo / "docker" / "wordpress").mkdir(parents=True)
        (repo / "docker-compose.yml").write_text("""
services:
  wordpress:
    volumes:
      - ./docker/wordpress:/var/www/html
""")
        manifest = ResolverChain().run(str(repo))

        # WP not resolved → unresolved + banner (fully_unavailable since
        # no other runtime-host resolved; partial_unresolved would require
        # at least one resolved runtime-host alongside)
        wp_entries = [e for e in manifest.resolved if e.name == "wordpress"]
        assert wp_entries == []
        assert any(u["name"] == "wordpress" for u in manifest.unresolved)
        assert manifest.banner is not None
        assert manifest.banner.reason == "fully_unavailable"


class TestPluginHeadersIntegration:
    """End-to-end: plugin headers declare WP+WC need, fulfillment satisfies
    both from the cache. Simulates the bot environment (fresh clone, no
    user-personal docker-compose.override.yml)."""

    def _stub_update_to_populate(self, monkeypatch, cache_root_dir):
        import time as _time

        def fake_update(name):
            slot = cache_root_dir / "pirategoat" / "ecosystem" / name / "latest"
            slot.mkdir(parents=True, exist_ok=True)
            (slot / ".last_updated").write_text(str(int(_time.time())))
            return {"name": name, "action": "cloned", "ok": True, "stderr": ""}

        monkeypatch.setattr("hosts.cache.manager.update_host", fake_update)

    def test_woopayments_fresh_clone_resolves_both_wp_and_wc(self, tmp_path, monkeypatch):
        """Committed config: docker-compose.yml self-mounts WP, plugin file
        declares WC + WP via headers. Cache fulfillment satisfies both.
        This is the bot's environment."""
        cache_root_dir = tmp_path / "xdg-cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        self._stub_update_to_populate(monkeypatch, cache_root_dir)

        repo = tmp_path / "woocommerce-payments"
        repo.mkdir()
        # Self-mount WP (vendored docker setup) and the plugin slot
        (repo / "docker" / "wordpress").mkdir(parents=True)
        (repo / "docker-compose.yml").write_text("""
services:
  wordpress:
    volumes:
      - ./docker/wordpress:/var/www/html
      - .:/var/www/html/wp-content/plugins/woocommerce-payments
""")
        # Plugin file with the headers WooPayments actually uses
        (repo / "woocommerce-payments.php").write_text("""<?php
/**
 * Plugin Name: WooPayments
 * Requires at least: 6.0
 * WC requires at least: 7.6
 * Requires Plugins: woocommerce
 */
""")
        manifest = ResolverChain().run(str(repo))

        runtime_hosts = sorted(
            e.name for e in manifest.resolved if e.kind == "runtime-host"
        )
        assert "wordpress" in runtime_hosts
        assert "woocommerce" in runtime_hosts
        # Both came from the cache (no local sibling configured)
        for name in ("wordpress", "woocommerce"):
            entry = next(e for e in manifest.resolved if e.name == name)
            assert entry.source == "ecosystem-cache"
            assert entry.confidence == "high"
        assert manifest.banner is None

    def test_local_dev_with_sibling_mount_keeps_local_wc(self, tmp_path, monkeypatch):
        """Local dev: docker-compose mounts a local WC sibling. Plugin
        headers also declare WC. The local mount wins via dedup; cache
        fulfillment is correctly skipped (no spurious git pull)."""
        cache_root_dir = tmp_path / "xdg-cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

        update_calls = []
        monkeypatch.setattr(
            "hosts.cache.manager.update_host",
            lambda name: update_calls.append(name) or {"ok": True, "action": "fresh"},
        )

        # Sibling WC checkout
        wc_sibling = tmp_path / "woocommerce-develop" / "plugins" / "woocommerce"
        wc_sibling.mkdir(parents=True)

        repo = tmp_path / "woocommerce-payments"
        repo.mkdir()
        (repo / "docker" / "wordpress").mkdir(parents=True)
        (repo / "docker-compose.yml").write_text("""
services:
  wordpress:
    volumes:
      - ./docker/wordpress:/var/www/html
""")
        (repo / "docker-compose.override.yml").write_text(f"""
services:
  wordpress:
    volumes:
      - {wc_sibling}:/var/www/html/wp-content/plugins/woocommerce
""")
        (repo / "woocommerce-payments.php").write_text("""<?php
/**
 * Plugin Name: WooPayments
 * Requires at least: 6.0
 * Requires Plugins: woocommerce
 */
""")
        # Pre-populate WP cache too (will be used by fulfillment for WP).
        wp_slot = cache_root_dir / "pirategoat" / "ecosystem" / "wordpress" / "latest"
        wp_slot.mkdir(parents=True)
        import time as _time
        (wp_slot / ".last_invalidated").write_text("")
        (wp_slot / ".last_updated").write_text(str(int(_time.time())))

        manifest = ResolverChain().run(str(repo))

        wc_entry = next(e for e in manifest.resolved if e.name == "woocommerce")
        # Local docker-compose sibling wins, NOT cache.
        assert wc_entry.source == "docker-compose"
        assert wc_entry.path == str(wc_sibling)
        # No update_host call for woocommerce (pre-filter prevented it).
        assert "woocommerce" not in update_calls

    def test_unfulfillable_declared_dep_surfaces_in_banner(self, tmp_path, monkeypatch):
        """`Requires Plugins: jetpack` — not in _FULFILLABLE_PLUGIN_SLUGS —
        stays unresolved and shows up in the partial-unresolved banner."""
        cache_root_dir = tmp_path / "xdg-cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        self._stub_update_to_populate(monkeypatch, cache_root_dir)

        repo = tmp_path / "myplugin"
        repo.mkdir()
        (repo / "myplugin.php").write_text("""<?php
/**
 * Plugin Name: MyPlugin
 * Requires at least: 6.0
 * Requires Plugins: jetpack
 */
""")
        manifest = ResolverChain().run(str(repo))

        # WP fulfilled via cache
        wp = next(e for e in manifest.resolved if e.name == "wordpress")
        assert wp.source == "ecosystem-cache"
        # Jetpack stays unresolved
        assert any(u["name"] == "jetpack" for u in manifest.unresolved)
        assert manifest.banner is not None
        assert "jetpack" in manifest.banner.message


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

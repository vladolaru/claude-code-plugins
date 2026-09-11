"""Tests for the resolver chain."""

import json
from pathlib import Path

import pytest

from hosts.chain import ResolverChain
from hosts.resolvers.base import ResolverResult
from hosts.types import HostContextManifest


def test_empty_repo_with_ambient_hosts_and_populated_cache_yields_nothing(tmp_path, monkeypatch):
    """No repo signal at all: an empty repo resolves nothing and shows no
    banner, even with adjacent directories that look like ecosystem
    checkouts and a populated cache sitting right next to it — the default
    chain doesn't have a sibling-directory resolver, and cache fulfillment
    never fires without a repo signal asking for a name."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setattr(
        "hosts.cache.manager.update_host",
        lambda name: {"name": name, "action": "cloned", "ok": False, "stderr": "blocked in test"},
    )
    (tmp_path / "wordpress-develop").mkdir()
    (tmp_path / "woocommerce-develop" / "plugins" / "woocommerce").mkdir(parents=True)
    (tmp_path / "xdg-cache" / "pirategoat" / "ecosystem" / "wordpress" / "latest").mkdir(parents=True)
    (tmp_path / "xdg-cache" / "pirategoat" / "ecosystem" / "woocommerce" / "latest").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# x")

    manifest = ResolverChain().run(str(repo))

    assert manifest.resolved == []
    assert manifest.unresolved == []
    assert manifest.banner is None
    assert set(manifest.diagnostics["resolvers_consulted"]) == {
        "explicit", "wp-env", "docker-compose", "plugin-headers",
        "vendor-inspection",
    }


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
    """wp-env declares a remote woocommerce (unresolved); docker-compose then
    resolves it locally. The stale unresolved signal is dropped and cache
    fulfillment — which would otherwise try to satisfy it — is never asked,
    since the pre-filter sees woocommerce is already in `seen_names`."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
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

    assert [e.name for e in manifest.resolved if e.kind == "runtime-host"] == ["woocommerce"]
    assert manifest.unresolved == []
    assert manifest.banner is None
    assert update_calls == []  # no ensure_fresh / update_host calls


def test_library_dep_does_not_trigger_runtime_host_banner(tmp_path, monkeypatch):
    """A repo with only vendor packages should not warn about runtime hosts."""
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
        ends up in resolved with source='ecosystem-cache', and diagnostics
        record what fulfillment did."""
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
        consulted = manifest.diagnostics["resolvers_consulted"]
        assert "ecosystem-cache-fulfillment" in consulted
        detail = manifest.diagnostics["resolver_detail"]["ecosystem-cache-fulfillment"]
        assert detail["entries"] == 1
        assert detail["notes"]["fulfilled"] == ["wordpress"]

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

    def test_the_woocommerce_monorepo_resolves_wordpress_and_never_itself(self, tmp_path, monkeypatch):
        """Measured layout: two plugin roots two levels down, one of which
        declares WooCommerce — the repository under review — as a
        dependency. WordPress is fulfilled from the cache with the strictest
        declared minimum; WooCommerce is dropped as self-provided and the
        cache is never asked for it."""
        cache_root_dir = tmp_path / "xdg-cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root_dir))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        self._stub_update_to_populate(monkeypatch, cache_root_dir)
        requested = []
        import hosts.chain as chain_mod
        real = chain_mod.EcosystemCacheResolver.resolve_for_names

        def spy(self_, names):
            requested.append(sorted(names))
            return real(self_, names)

        monkeypatch.setattr(chain_mod.EcosystemCacheResolver, "resolve_for_names", spy)

        repo = tmp_path / "woocommerce-develop"
        wc = repo / "plugins" / "woocommerce"
        beta = repo / "plugins" / "woocommerce-beta-tester"
        wc.mkdir(parents=True)
        beta.mkdir(parents=True)
        (wc / "woocommerce.php").write_text("<?php\n/**\n * Plugin Name: WooCommerce\n * Text Domain: woocommerce\n * Requires at least: 7.0\n */\n")
        (wc / ".wp-env.json").write_text(json.dumps({"core": "https://wordpress.org/wordpress-latest.zip", "plugins": ["."]}))
        (beta / "woocommerce-beta-tester.php").write_text("<?php\n/**\n * Plugin Name: WooCommerce Beta Tester\n * Text Domain: woocommerce-beta-tester\n * Requires at least: 5.8\n * WC requires at least: 9.4\n */\n")
        (beta / ".wp-env.json").write_text(json.dumps({"plugins": [".", "https://downloads.wordpress.org/plugin/woocommerce.zip"]}))

        manifest = ResolverChain().run(str(repo))

        assert requested == [["wordpress"]]
        runtime = [e for e in manifest.resolved if e.kind == "runtime-host"]
        assert [e.name for e in runtime] == ["wordpress"]
        wordpress = runtime[0]
        assert wordpress.source == "ecosystem-cache"
        assert wordpress.notes["declared_minimum"] == "7.0"
        assert [d["root"] for d in wordpress.notes["declared_by"]] == [
            "plugins/woocommerce", "plugins/woocommerce", "plugins/woocommerce-beta-tester",
        ]
        assert manifest.unresolved == []
        assert manifest.banner is None
        assert manifest.diagnostics["self_provided"] == ["woocommerce"]
        assert manifest.diagnostics["scan_roots"] == 4
        assert manifest.diagnostics["config_errors"] == []
        assert json.dumps(manifest.to_dict())

    def test_unresolved_signals_merge_by_name_with_the_strictest_version(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr("hosts.cache.manager.update_host",
                            lambda name: {"name": name, "action": "cloned", "ok": False, "stderr": "offline"})
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "plugin.php").write_text("<?php\n/**\n * Plugin Name: P\n * Requires at least: 6.2\n */\n")
        (repo / ".wp-env.json").write_text(json.dumps({"core": "https://wordpress.org/wordpress-6.7.1.zip"}))

        manifest = ResolverChain().run(str(repo))

        assert len(manifest.unresolved) == 1
        item = manifest.unresolved[0]
        assert item["name"] == "wordpress"
        assert item["version"] == "6.7.1"
        assert item["reason"] == "remote_url_not_local"   # wp-env ran before plugin-headers
        assert item["declared_by"] == [
            {"source": "wp-env", "reason": "remote_url_not_local", "version": "6.7.1", "root": ""},
            {"source": "plugin-headers", "reason": "declared_in_plugin_headers", "version": "6.2", "root": ""},
        ]
        assert manifest.banner is not None
        assert manifest.banner.message.count('"wordpress"') == 1


def test_resolver_chain_tolerates_resolver_exception(tmp_path):
    """A resolver that raises must not abort the chain."""
    from hosts.chain import ResolverChain
    from hosts.resolvers.base import HostResolver, ResolverResult

    class ExplodingResolver(HostResolver):
        source = "exploding"

        def resolve(self, repo_path, scan=None):
            raise RuntimeError("kaboom")

    class WorkingResolver(HostResolver):
        source = "working"

        def resolve(self, repo_path, scan=None):
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


def test_resolver_chain_preserves_numeric_string_unresolved_version(tmp_path):
    """Numeric version strings remain declarations, not malformed payloads."""
    from hosts.resolvers.base import HostResolver, ResolverResult

    class VersionResolver(HostResolver):
        source = "version"

        def resolve(self, repo_path, scan=None):
            return ResolverResult(
                entries=[],
                unresolved=[{"name": "jetpack", "version": "9.4"}],
                notes={},
            )

    manifest = ResolverChain(resolvers=[VersionResolver()]).run(str(tmp_path))

    assert manifest.unresolved[0]["version"] == "9.4"
    assert json.dumps(manifest.to_dict())


def test_resolver_chain_merges_numeric_and_large_unresolved_versions(tmp_path):
    """A component past int()'s digit limit is no version: the chain keeps the
    first declaration and stays JSON-safe instead of raising."""
    from hosts.resolvers.base import HostResolver, ResolverResult

    huge_component = "9" * 4301

    class VersionResolver(HostResolver):
        source = "version"

        def resolve(self, repo_path, scan=None):
            return ResolverResult(
                entries=[],
                unresolved=[
                    {"name": "jetpack", "version": "6.9"},
                    {"name": "jetpack", "version": "6.10"},
                    {"name": "akismet", "version": "1.9"},
                    {"name": "akismet", "version": f"1.{huge_component}"},
                ],
                notes={},
            )

    manifest = ResolverChain(resolvers=[VersionResolver()]).run(str(tmp_path))

    versions = {item["name"]: item["version"] for item in manifest.unresolved}
    assert versions == {"jetpack": "6.10", "akismet": "1.9"}
    assert json.dumps(manifest.to_dict())


def test_a_failing_fulfilment_pass_leaves_the_names_unresolved_with_the_banner(tmp_path, monkeypatch):
    """The cache pass refreshes a slot and reads its git identity, so it can
    fail in ways no resolver can; the review still gets a manifest."""
    import hosts.chain as chain_mod

    def explode(self_, names):
        raise PermissionError("[Errno 13] Permission denied: '.last_updated'")

    monkeypatch.setattr(chain_mod.EcosystemCacheResolver, "resolve_for_names", explode)
    repo = tmp_path / "plugin"
    repo.mkdir()
    (repo / "plugin.php").write_text("<?php\n/**\n * Plugin Name: P\n * Requires at least: 6.0\n */\n")

    manifest = ResolverChain().run(str(repo))

    assert [u["name"] for u in manifest.unresolved] == ["wordpress"]
    assert manifest.banner is not None
    detail = manifest.diagnostics["resolver_detail"]["ecosystem-cache-fulfillment"]
    assert detail["notes"]["errors"] == [
        "PermissionError: [Errno 13] Permission denied: '.last_updated'"
    ]
    assert json.dumps(manifest.to_dict())


def test_an_explicitly_configured_host_the_repo_also_provides_stays_resolved(tmp_path, monkeypatch):
    """`hosts.runtime` names a WooCommerce checkout while the repository's own
    plugin header both provides WooCommerce and (as an extension bundled in
    the same tree would) declares it: the explicit entry wins, the declared
    signal is dropped as self-provided, and the cache is never asked."""
    import hosts.chain as chain_mod
    requested = []
    monkeypatch.setattr(
        chain_mod.EcosystemCacheResolver, "resolve_for_names",
        lambda self_, names: requested.append(sorted(names)) or ResolverResult(entries=[], unresolved=[], notes={}),
    )
    external = tmp_path / "woocommerce-checkout"
    external.mkdir()
    repo = tmp_path / "woocommerce"
    (repo / ".pirategoat").mkdir(parents=True)
    (repo / ".pirategoat" / "config.json").write_text(json.dumps({
        "hosts": {"runtime": [{"name": "woocommerce", "path": "../woocommerce-checkout"}]},
    }))
    (repo / "woocommerce.php").write_text(
        "<?php\n/**\n * Plugin Name: WooCommerce\n * Text Domain: woocommerce\n * Requires at least: 6.7\n */\n"
    )
    (repo / "packages").mkdir()
    (repo / "packages" / "blocks").mkdir()
    (repo / "packages" / "blocks" / "blocks.php").write_text(
        "<?php\n/**\n * Plugin Name: Blocks\n * WC requires at least: 9.0\n */\n"
    )

    manifest = ResolverChain().run(str(repo))

    assert [(e.name, e.source) for e in manifest.resolved if e.kind == "runtime-host"] == [
        ("woocommerce", "explicit"),
    ]
    assert [u["name"] for u in manifest.unresolved] == ["wordpress"]
    assert manifest.diagnostics["self_provided"] == ["woocommerce"]
    assert requested == [["wordpress"]]


def test_resolved_local_runtime_hosts_carry_their_declared_version_and_commit(tmp_path):
    """Run 4 briefed every reviewer "woocommerce (docker-compose): version
    unknown, commit unknown" for a checkout with `Version: 11.2.0-dev` in its
    header and a git HEAD; only cache slots had an identity reader."""
    import subprocess
    from hosts.resolvers.base import HostResolver, ResolverResult
    from hosts.types import HostEntry

    checkout = tmp_path / "woocommerce"
    checkout.mkdir()
    (checkout / "woocommerce.php").write_text("<?php\n/**\n * Plugin Name: WooCommerce\n * Version: 11.2.0-dev\n */\n")
    subprocess.run(["git", "-C", str(checkout), "init", "-q", "-b", "trunk"], check=True)
    subprocess.run(["git", "-C", str(checkout), "-c", "user.email=t@example.com", "-c", "user.name=t", "add", "."], check=True)
    subprocess.run(["git", "-C", str(checkout), "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "init"], check=True)
    head = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

    class MountResolver(HostResolver):
        source = "docker-compose"

        def resolve(self, repo_path, scan=None):
            return ResolverResult(entries=[
                HostEntry(name="woocommerce", kind="runtime-host", path=str(checkout), source=self.source, confidence="high"),
                # A resolver's own reading wins over the identity pass.
                HostEntry(name="jetpack", kind="runtime-host", path=str(checkout), source=self.source, version="15.0", notes={"commit": "abc"}),
                # Not a local checkout: everything stays unknown.
                HostEntry(name="ghost", kind="runtime-host", path=str(tmp_path / "absent"), source=self.source),
            ], unresolved=[], notes={})

    manifest = ResolverChain(resolvers=[MountResolver()]).run(str(tmp_path))
    by_name = {entry.name: entry for entry in manifest.resolved}

    assert by_name["woocommerce"].version == "11.2.0-dev"
    assert by_name["woocommerce"].notes["commit"] == head
    assert by_name["woocommerce"].notes["identity_scope"] == "checkout"
    assert "branch" not in by_name["woocommerce"].notes  # never read, never projected
    assert by_name["jetpack"].version == "15.0"
    assert by_name["jetpack"].notes["commit"] == "abc"
    assert by_name["ghost"].version is None and "commit" not in by_name["ghost"].notes
    assert "identity_errors" not in manifest.diagnostics


def test_an_identity_read_failure_is_a_diagnostic_never_an_aborted_review(tmp_path, monkeypatch):
    from hosts import chain as chain_module
    from hosts.resolvers.base import HostResolver, ResolverResult
    from hosts.types import HostEntry

    def explode(_path):
        raise RuntimeError("git hung")

    monkeypatch.setattr(chain_module, "path_identity", explode)

    class MountResolver(HostResolver):
        source = "docker-compose"

        def resolve(self, repo_path, scan=None):
            return ResolverResult(entries=[
                HostEntry(name="woocommerce", kind="runtime-host", path=str(tmp_path), source=self.source),
            ], unresolved=[], notes={})

    manifest = ResolverChain(resolvers=[MountResolver()]).run(str(tmp_path))

    assert [entry.name for entry in manifest.resolved] == ["woocommerce"]
    assert manifest.resolved[0].version is None
    assert manifest.diagnostics["identity_errors"] == ["woocommerce: RuntimeError: git hung"]

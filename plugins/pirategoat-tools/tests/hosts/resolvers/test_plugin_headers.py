"""Tests for the plugin-headers resolver."""

import textwrap
from pathlib import Path

from hosts.resolvers.plugin_headers import PluginHeadersResolver


def _write_plugin(repo: Path, name: str, headers: str) -> Path:
    full = repo / name
    full.write_text(textwrap.dedent(headers))
    return full


def test_empty_repo_returns_no_unresolved(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = PluginHeadersResolver().resolve(str(repo))
    assert result.entries == []
    assert result.unresolved == []


def test_php_file_without_plugin_name_header_ignored(tmp_path):
    """A .php file that's not a plugin (no `Plugin Name:` header) is skipped."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_plugin(repo, "helper.php", """\
        <?php
        // just a helper, not a plugin
        function do_thing() {}
    """)
    result = PluginHeadersResolver().resolve(str(repo))
    assert result.unresolved == []


def test_plugin_with_requires_at_least_emits_wordpress_unresolved(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_plugin(repo, "myplugin.php", """\
        <?php
        /**
         * Plugin Name: MyPlugin
         * Requires at least: 6.0
         * Version: 1.0
         */
    """)
    result = PluginHeadersResolver().resolve(str(repo))
    names = [u["name"] for u in result.unresolved]
    assert "wordpress" in names
    wp = next(u for u in result.unresolved if u["name"] == "wordpress")
    assert wp["version"] == "6.0"
    assert wp["reason"] == "declared_in_plugin_headers"
    assert wp["source"] == "plugin-headers"


def test_plugin_with_wc_requires_at_least_emits_woocommerce_unresolved(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_plugin(repo, "myplugin.php", """\
        <?php
        /**
         * Plugin Name: MyPlugin
         * WC requires at least: 7.6
         */
    """)
    result = PluginHeadersResolver().resolve(str(repo))
    names = [u["name"] for u in result.unresolved]
    assert "woocommerce" in names
    wc = next(u for u in result.unresolved if u["name"] == "woocommerce")
    assert wc["version"] == "7.6"


def test_plugin_with_requires_plugins_emits_each_slug(tmp_path):
    """`Requires Plugins: woocommerce, jetpack` → two unresolved entries."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_plugin(repo, "myplugin.php", """\
        <?php
        /**
         * Plugin Name: MyPlugin
         * Requires Plugins: woocommerce, jetpack
         */
    """)
    result = PluginHeadersResolver().resolve(str(repo))
    names = sorted(u["name"] for u in result.unresolved)
    assert names == ["jetpack", "woocommerce"]
    wc = next(u for u in result.unresolved if u["name"] == "woocommerce")
    jp = next(u for u in result.unresolved if u["name"] == "jetpack")
    assert wc["fulfillable"] is True  # in _FULFILLABLE_PLUGIN_SLUGS
    assert jp["fulfillable"] is False


def test_woocommerce_dedupes_across_wc_header_and_requires_plugins(tmp_path):
    """Both `WC requires at least` AND `Requires Plugins: woocommerce` →
    only one woocommerce unresolved entry (the version-bearing one)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_plugin(repo, "myplugin.php", """\
        <?php
        /**
         * Plugin Name: MyPlugin
         * WC requires at least: 7.6
         * Requires Plugins: woocommerce
         */
    """)
    result = PluginHeadersResolver().resolve(str(repo))
    wc_entries = [u for u in result.unresolved if u["name"] == "woocommerce"]
    assert len(wc_entries) == 1
    assert wc_entries[0]["version"] == "7.6"  # the version-bearing one wins


def test_woopayments_style_full_header_block(tmp_path):
    """End-to-end: a header block matching WooPayments' real plugin file
    produces both wordpress and woocommerce unresolved entries."""
    repo = tmp_path / "woocommerce-payments"
    repo.mkdir()
    _write_plugin(repo, "woocommerce-payments.php", """\
        <?php
        /**
         * Plugin Name: WooPayments
         * Plugin URI: https://woocommerce.com/payments/
         * Description: Accept payments via credit card.
         * Version: 10.7.1
         * Author: Automattic
         * WC requires at least: 7.6
         * WC tested up to: 10.7.0
         * Requires at least: 6.0
         * Requires PHP: 7.3
         * Requires Plugins: woocommerce
         */
    """)
    result = PluginHeadersResolver().resolve(str(repo))
    names = sorted(u["name"] for u in result.unresolved)
    assert names == ["woocommerce", "wordpress"]
    assert result.notes.get("detected") == "plugin"


def test_first_php_file_with_plugin_name_wins(tmp_path):
    """Non-plugin .php files come first alphabetically don't shadow the real plugin."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_plugin(repo, "a-helper.php", """\
        <?php
        // helper file
        function thing() {}
    """)
    _write_plugin(repo, "b-plugin.php", """\
        <?php
        /**
         * Plugin Name: BPlugin
         * Requires at least: 6.0
         */
    """)
    result = PluginHeadersResolver().resolve(str(repo))
    names = [u["name"] for u in result.unresolved]
    assert "wordpress" in names


def test_theme_with_requires_at_least_emits_wordpress_unresolved(tmp_path):
    """Theme detection via style.css with `Theme Name:` header."""
    repo = tmp_path / "mytheme"
    repo.mkdir()
    (repo / "style.css").write_text(textwrap.dedent("""\
        /*
        Theme Name: MyTheme
        Requires at least: 6.0
        */
    """))
    result = PluginHeadersResolver().resolve(str(repo))
    names = [u["name"] for u in result.unresolved]
    assert "wordpress" in names
    assert result.notes.get("detected") == "theme"


def test_theme_without_theme_name_header_ignored(tmp_path):
    """A style.css without `Theme Name:` is not treated as a theme."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "style.css").write_text("body { color: red; }")
    result = PluginHeadersResolver().resolve(str(repo))
    assert result.unresolved == []


def test_unreadable_repo_returns_empty(tmp_path):
    """Nonexistent path doesn't crash."""
    result = PluginHeadersResolver().resolve(str(tmp_path / "does-not-exist"))
    assert result.entries == []
    assert result.unresolved == []

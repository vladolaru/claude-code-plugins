"""WordPress plugin/theme header resolver.

Reads the standard plugin/theme header block from the repo's main file and
emits declared dependencies as unresolved entries. The chain's
cache-fulfillment pass then satisfies known ecosystem hosts (WordPress,
WooCommerce) from the cache, while unfulfillable declared deps surface as
banner entries so the reviewer knows source is missing.

Headers parsed:
- ``Plugin Name`` — required to detect a plugin file (otherwise theme via
  ``style.css`` with ``Theme Name``).
- ``Requires at least`` — WP minimum version. Emits unresolved
  ``name="wordpress"``.
- ``WC requires at least`` — WooCommerce minimum version (registered by WC
  via the ``extra_plugin_headers`` filter; an official header once WC is
  active). Emits unresolved ``name="woocommerce"``.
- ``Requires Plugins`` — WP 6.5+ comma-separated wp.org plugin slugs. Each
  slug emits an unresolved entry; ``woocommerce`` is fulfillable from
  cache, others are not but still surface in the manifest.
"""

import os
import re
from typing import Any, Dict, List, Optional

from hosts.resolvers.base import HostResolver, ResolverResult


# Match `FieldName: value` lines. Field name allows letters, digits, spaces,
# underscores, hyphens — covers all known WP/WC header conventions. Leading
# `*` is optional (PHPDoc-style block comments use ` * Field: value`).
_HEADER_LINE_RE = re.compile(
    r"^\s*\*?\s*([A-Za-z][A-Za-z0-9 _-]*?):\s*(.+?)\s*$"
)

_PLUGIN_NAME_FIELD = "Plugin Name"
_THEME_NAME_FIELD = "Theme Name"

_WP_VERSION_FIELD = "Requires at least"
_WC_VERSION_FIELD = "WC requires at least"
_REQUIRES_PLUGINS_FIELD = "Requires Plugins"

# Plugin slugs the cache can fulfill. Other declared deps still emit
# unresolved entries (so the reviewer knows source is missing) but no
# fulfillment path exists.
_FULFILLABLE_PLUGIN_SLUGS = {"woocommerce"}

# Caps to keep CPU bounded on weird inputs.
_MAX_TOP_LEVEL_PHP_FILES = 20
_MAX_HEADER_LINES = 100


class PluginHeadersResolver(HostResolver):
    source = "plugin-headers"

    def resolve(self, repo_path: str) -> ResolverResult:
        headers = self._find_plugin_headers(repo_path)
        if not headers:
            headers = self._find_theme_headers(repo_path)
        if not headers:
            return ResolverResult(entries=[], unresolved=[], notes={})

        unresolved: List[Dict[str, Any]] = []
        emitted_names: set = set()

        wp_version = headers.get(_WP_VERSION_FIELD)
        if wp_version:
            unresolved.append({
                "name": "wordpress",
                "version": wp_version,
                "reason": "declared_in_plugin_headers",
                "source": self.source,
            })
            emitted_names.add("wordpress")

        wc_version = headers.get(_WC_VERSION_FIELD)
        if wc_version:
            unresolved.append({
                "name": "woocommerce",
                "version": wc_version,
                "reason": "declared_in_plugin_headers",
                "source": self.source,
            })
            emitted_names.add("woocommerce")

        # `Requires Plugins:` (WP 6.5+) — comma-separated wp.org slugs.
        requires = headers.get(_REQUIRES_PLUGINS_FIELD, "")
        for raw_slug in requires.split(","):
            slug = raw_slug.strip()
            if not slug or slug in emitted_names:
                continue
            unresolved.append({
                "name": slug,
                "reason": "declared_in_plugin_headers",
                "source": self.source,
                "fulfillable": slug in _FULFILLABLE_PLUGIN_SLUGS,
            })
            emitted_names.add(slug)

        notes = {"detected": "plugin" if _PLUGIN_NAME_FIELD in headers else "theme"}
        return ResolverResult(entries=[], unresolved=unresolved, notes=notes)

    @classmethod
    def _find_plugin_headers(cls, repo_path: str) -> Optional[Dict[str, str]]:
        """Walk top-level .php files; return the header dict of the first
        file containing ``Plugin Name:``."""
        try:
            entries = sorted(os.listdir(repo_path))
        except OSError:
            return None
        php_files = [
            name for name in entries
            if name.endswith(".php")
            and os.path.isfile(os.path.join(repo_path, name))
        ][:_MAX_TOP_LEVEL_PHP_FILES]
        for name in php_files:
            full = os.path.join(repo_path, name)
            headers = cls._parse_header_block(full)
            if headers and _PLUGIN_NAME_FIELD in headers:
                return headers
        return None

    @classmethod
    def _find_theme_headers(cls, repo_path: str) -> Optional[Dict[str, str]]:
        style = os.path.join(repo_path, "style.css")
        if not os.path.isfile(style):
            return None
        headers = cls._parse_header_block(style)
        if headers and _THEME_NAME_FIELD in headers:
            return headers
        return None

    @staticmethod
    def _parse_header_block(path: str) -> Optional[Dict[str, str]]:
        """Parse the leading comment block of a .php or .css file into a
        ``{field: value}`` dict. Returns None if unreadable."""
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = []
                for _ in range(_MAX_HEADER_LINES):
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line)
        except OSError:
            return None
        out: Dict[str, str] = {}
        for line in lines:
            if line.lstrip().startswith("*/"):
                break
            m = _HEADER_LINE_RE.match(line)
            if m:
                field, value = m.group(1).strip(), m.group(2).strip()
                # First occurrence wins (mirrors WP's get_file_data behavior).
                if field not in out:
                    out[field] = value
        return out or None

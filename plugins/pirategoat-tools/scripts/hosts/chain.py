"""Resolver chain — merges repository host signals and emits a manifest.

Composes the repo-signaled advisory resolvers in priority order — explicit,
wp-env, docker-compose, plugin-headers, vendor — and dedups the resolved
entries by `kind:name`, the first (highest-priority) entry winning. The
sibling resolver stays a standalone, non-default helper.

Every resolved local runtime host is then stamped with the identity
`hosts/identity.py` reads from its path: the declared version, the commit,
the commit date, and whether that commit belongs to the checkout's own
repository or to the repository enclosing it. Only facts the resolver left
unknown are filled, and never the branch — no host projection carries a
branch name, because a personal checkout's would reach the shared manifest.
Stamping failures land in `diagnostics.identity_errors` rather than aborting
a review.

Unresolved signals merge by host name, keeping one `declared_by` entry per
signal and the strictest declared `version`. Hosts the repository itself
provides are dropped before fulfillment, so a repository is never verified
against a cache clone of itself; `plugin-headers` derives that `provides`
list from the `Text Domain` header or the main file's stem, constrained to
the known cache names. Ecosystem-cache fulfillment then runs for the
remaining WordPress and WooCommerce signals, copying each declared minimum
onto the fulfilled entry, and the degradation banner is generated last.

Diagnostics record `scan_roots`, `config_errors` and `self_provided`
alongside the per-resolver detail.
"""

import json
import re
import time
from typing import Dict, List, Optional

from hosts.resolvers.base import HostResolver, ResolverResult
from hosts.resolvers.docker_compose import DockerComposeResolver
from hosts.resolvers.ecosystem_cache import EcosystemCacheResolver
from hosts.resolvers.explicit import ExplicitResolver
from hosts.resolvers.plugin_headers import PluginHeadersResolver
from hosts.resolvers.vendor import VendorResolver
from hosts.resolvers.wp_env import WpEnvResolver
from hosts.identity import path_identity
from hosts.scan_roots import scan_roots
from hosts.types import Banner, HostContextManifest, HostEntry


# Priority order: lower index = higher priority for dedup.
_DEFAULT_RESOLVERS: List[HostResolver] = [
    ExplicitResolver(),
    WpEnvResolver(),
    DockerComposeResolver(),
    PluginHeadersResolver(),  # after operational mounts — header decls
                              # surface declared deps that mounts didn't
                              # cover (most importantly: WC need on a
                              # fresh clone with no committed mount).
    VendorResolver(),
]

_SIGNAL_KEYS = ("source", "reason", "version", "root")
_VERSION_DIGITS_RE = re.compile(r"\d+")


def _version_key(value):
    """Comparable numeric tokens for the strictest minimum; no version is lowest."""
    if not isinstance(value, str):
        return ()
    try:
        return tuple(int(part) for part in _VERSION_DIGITS_RE.findall(value))
    except ValueError:  # a component past int()'s digit limit is not a version
        return ()


class ResolverChain:
    def __init__(self, resolvers: Optional[List[HostResolver]] = None):
        self.resolvers = resolvers if resolvers is not None else list(_DEFAULT_RESOLVERS)

    def run(self, repo_path: str) -> HostContextManifest:
        start = time.monotonic()
        resolved: List[HostEntry] = []
        unresolved: List[Dict] = []
        consulted: List[str] = []
        per_resolver: Dict[str, Dict] = {}

        seen_names: Dict[str, HostEntry] = {}  # kind:name -> first (highest-priority) entry
        scan = scan_roots(repo_path)  # one walk, shared by every resolver and the diagnostics

        for resolver in self.resolvers:
            consulted.append(resolver.source)
            try:
                result = resolver.resolve(repo_path, scan)
                entries, resolver_unresolved, notes = (
                    result.entries, result.unresolved, result.notes
                )
                per_resolver[resolver.source] = {
                    "entries": len(entries),
                    "unresolved": len(resolver_unresolved),
                    "notes": notes,
                }
                for entry in entries:
                    key = f"{entry.kind}:{entry.name}"
                    if key not in seen_names:
                        seen_names[key] = entry
                        resolved.append(entry)
                unresolved.extend(resolver_unresolved)
            except Exception as err:  # noqa: BLE001 — resolver isolation is the point
                per_resolver[resolver.source] = {
                    "entries": 0,
                    "unresolved": 0,
                    "notes": {"error": f"{type(err).__name__}: {err}"},
                }
                continue

        # Identity pass: every resolved runtime host that is a local checkout
        # reports the version it declares and the commit it sits at, the
        # way cache slots already do. Only unknown facts are filled — a
        # resolver's own reading wins — and a failure is a diagnostic note,
        # never an aborted review.
        identity_errors: List[str] = []
        for entry in resolved:
            if entry.kind != "runtime-host" or entry.source == EcosystemCacheResolver.source:
                continue
            try:
                self._stamp_identity(entry)
            except Exception as err:  # noqa: BLE001 — same isolation as a resolver
                identity_errors.append(f"{entry.name}: {type(err).__name__}: {err}")

        provides = set()
        for detail in per_resolver.values():
            for name in (detail.get("notes") or {}).get("provides") or []:
                provides.add(name)
        unresolved, self_provided = self._drop_self_provided(unresolved, provides)
        unresolved = self._merge_unresolved(unresolved)

        # Fulfillment pass: try to satisfy unresolved names from the
        # ecosystem cache. Fires only for names earlier resolvers signaled
        # the repo needs — keeps machine-wide cache state from leaking into
        # repos that didn't ask for it.
        # The pass refreshes the slot and reads its git identity, so it can
        # fail in every way a resolver can and more; a failure is a note and
        # the names stay unresolved with the banner, never an aborted review.
        fulfillment_errors: List[str] = []
        try:
            fulfilled = self._fulfill_from_cache(unresolved, seen_names)
        except Exception as err:  # noqa: BLE001 — same isolation as a resolver
            fulfilled = []
            fulfillment_errors.append(f"{type(err).__name__}: {err}")
        if fulfilled or fulfillment_errors:
            fulfillment_notes = {"fulfilled": [e.name for e in fulfilled]}
            if fulfillment_errors:
                fulfillment_notes["errors"] = fulfillment_errors
            per_resolver["ecosystem-cache-fulfillment"] = {
                "entries": len(fulfilled),
                "unresolved": 0,
                "notes": fulfillment_notes,
            }
            consulted.append("ecosystem-cache-fulfillment")
            resolved.extend(fulfilled)

        # Also drops every signal a fulfilled slot satisfied.
        unresolved = self._drop_resolved_unresolved(resolved, unresolved)
        banner = self._build_banner(resolved, unresolved)

        diagnostics = {
            "resolvers_consulted": consulted,
            "resolver_detail": per_resolver,
            "runtime_ms": int((time.monotonic() - start) * 1000),
            "scan_roots": len(scan.roots),
            "config_errors": list(scan.config_errors),
            "self_provided": self_provided,
        }
        if identity_errors:
            diagnostics["identity_errors"] = identity_errors

        return HostContextManifest(
            version=1,
            resolved=resolved,
            unresolved=unresolved,
            banner=banner,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _stamp_identity(entry: HostEntry) -> None:
        """Fill the entry's unknown version and git identity from its path.

        `identity_scope` says whether the commit is the host directory's own
        repository or the repository enclosing it (a plugin inside a
        monorepo, or vendored into a site repository).
        """
        identity = path_identity(entry.path)
        if entry.version is None and identity["version"] is not None:
            entry.version = identity["version"]
        for field in ("commit", "commit_date"):
            if entry.notes.get(field) is None and identity[field] is not None:
                entry.notes[field] = identity[field]
        if identity["scope"] is not None and entry.notes.get("identity_scope") is None:
            entry.notes["identity_scope"] = identity["scope"]

    @staticmethod
    def _fulfill_from_cache(
        unresolved: List[Dict],
        seen_names: Dict[str, HostEntry],
    ) -> List[HostEntry]:
        # Pre-filter: drop names that a higher-priority resolver already
        # claimed. Fulfillment calls `ensure_fresh()` which can do a network
        # git pull — never fire it for hosts the repo already has locally.
        requested = {
            u.get("name")
            for u in unresolved
            if u.get("name") and f"runtime-host:{u['name']}" not in seen_names
        }
        if not requested:
            return []
        result = EcosystemCacheResolver().resolve_for_names(requested)
        by_name = {u.get("name"): u for u in unresolved}
        out: List[HostEntry] = []
        for entry in result.entries:
            key = f"{entry.kind}:{entry.name}"
            if key in seen_names:
                continue  # extra safety; pre-filter should prevent this
            declared = by_name.get(entry.name) or {}
            entry.notes["declared_minimum"] = declared.get("version")
            entry.notes["declared_by"] = list(declared.get("declared_by") or [])
            seen_names[key] = entry
            out.append(entry)
        return out

    @staticmethod
    def _drop_self_provided(unresolved: List[Dict], provides: set):
        """Drop hosts the repository itself provides; they are not upstream needs."""
        dropped = sorted({u.get("name") for u in unresolved if u.get("name") in provides})
        kept = [u for u in unresolved if u.get("name") not in provides]
        return kept, dropped

    @staticmethod
    def _merge_unresolved(unresolved: List[Dict]) -> List[Dict]:
        """Merge signals by host, preserving the first and strictest version."""
        merged: Dict[str, Dict] = {}
        for item in unresolved:
            name = item.get("name")
            signal = {
                key: item.get(key)
                for key in _SIGNAL_KEYS
                if item.get(key) is not None
            }
            if name not in merged:
                merged[name] = dict(item)
                merged[name]["declared_by"] = [signal]
                continue
            merged[name]["declared_by"].append(signal)
            if _version_key(item.get("version")) > _version_key(merged[name].get("version")):
                merged[name]["version"] = item.get("version")
        return list(merged.values())

    @staticmethod
    def _drop_resolved_unresolved(resolved: List[HostEntry], unresolved: List[Dict]) -> List[Dict]:
        resolved_runtime_names = {
            entry.name
            for entry in resolved
            if entry.kind == "runtime-host"
        }
        if not resolved_runtime_names:
            return unresolved
        return [
            item
            for item in unresolved
            if item.get("name") not in resolved_runtime_names
        ]

    @staticmethod
    def _build_banner(resolved: List[HostEntry], unresolved: List[Dict]):
        has_runtime_host = any(e.kind == "runtime-host" for e in resolved)
        if not has_runtime_host and not unresolved:
            return None
        if not has_runtime_host:
            unresolved_names = ", ".join(
                ResolverChain._banner_display_name(u.get("name", "?"))
                for u in unresolved
            )
            return Banner(
                degraded=True,
                reason="fully_unavailable",
                message=(
                    f"Host context unavailable: no runtime-host resolved; unresolved: {unresolved_names}. "
                    "Integration risks may not be verified."
                ),
                unresolved=list(unresolved),
            )
        if unresolved:
            unresolved_names = ", ".join(
                ResolverChain._banner_display_name(u.get("name", "?"))
                for u in unresolved
            )
            return Banner(
                degraded=True,
                reason="partial_unresolved",
                message=(
                    f"Host context partially degraded — unresolved: {unresolved_names}. "
                    "Findings against unresolved hosts should not make absence claims."
                ),
                unresolved=list(unresolved),
            )
        return None

    @staticmethod
    def _banner_display_name(name) -> str:
        return json.dumps(str(name), ensure_ascii=True)

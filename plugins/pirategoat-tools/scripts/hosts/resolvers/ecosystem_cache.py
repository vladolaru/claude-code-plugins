"""Ecosystem-cache resolver — reads the shared pirategoat ecosystem cache."""

import os
from typing import Any, Dict, Iterable, List

from hosts.cache.manager import cache_root, ensure_fresh
from hosts.resolvers.base import HostResolver, ResolverResult
from hosts.types import HostEntry


_KNOWN_HOSTS = ["wordpress", "woocommerce"]


class EcosystemCacheResolver(HostResolver):
    source = "ecosystem-cache"

    def resolve(self, repo_path: str) -> ResolverResult:
        cache_root_path = str(cache_root())
        if not os.path.isdir(cache_root_path):
            return ResolverResult(
                entries=[], unresolved=[],
                notes={"state": "cache_missing", "path": cache_root_path},
            )

        entries: List[HostEntry] = []
        for host in _KNOWN_HOSTS:
            path = os.path.join(cache_root_path, host, "latest")
            if os.path.isdir(path):
                entries.append(HostEntry(
                    name=host,
                    kind="runtime-host",
                    path=path,
                    source=self.source,
                    version="latest",
                    confidence="medium",
                ))
        return ResolverResult(entries=entries, unresolved=[], notes={})

    def resolve_for_names(self, names: Iterable[str]) -> ResolverResult:
        """Fulfillment mode — emit cache entries for explicitly requested
        names only, refreshing each via `ensure_fresh()` first.

        Used by the chain's post-loop fulfillment pass to satisfy unresolved
        host signals from earlier resolvers. Confidence is `high` because the
        slot is guaranteed within the freshness window after `ensure_fresh`.
        Names outside `_KNOWN_HOSTS` are ignored.
        """
        requested = {n for n in names if n in _KNOWN_HOSTS}
        if not requested:
            return ResolverResult(entries=[], unresolved=[], notes={})

        refresh_results: Dict[str, Any] = {
            name: ensure_fresh(name) for name in sorted(requested)
        }

        cache_root_path = str(cache_root())
        if not os.path.isdir(cache_root_path):
            return ResolverResult(
                entries=[], unresolved=[],
                notes={
                    "state": "cache_missing",
                    "path": cache_root_path,
                    "refresh": refresh_results,
                },
            )

        entries: List[HostEntry] = []
        unresolved: List[Dict[str, Any]] = []
        for name in sorted(requested):
            path = os.path.join(cache_root_path, name, "latest")
            refresh = refresh_results.get(name, {})
            if os.path.isdir(path):
                entries.append(HostEntry(
                    name=name,
                    kind="runtime-host",
                    path=path,
                    source=self.source,
                    version="latest",
                    confidence="high",
                    notes={
                        "fulfillment": True,
                        "refresh_action": refresh.get("action"),
                        "refresh_ok": refresh.get("ok"),
                    },
                ))
            else:
                unresolved.append({
                    "name": name,
                    "reason": "cache_unpopulated",
                    "source": self.source,
                    "refresh": refresh,
                })
        return ResolverResult(entries=entries, unresolved=unresolved, notes={})

"""Ecosystem-cache resolver — reads the shared pirategoat ecosystem cache."""

import os
from typing import Any, Dict, Iterable, List

from hosts.cache.manager import KNOWN_ECOSYSTEM_NAMES, cache_dir_for, ensure_fresh, slot_identity
from hosts.resolvers.base import HostResolver, ResolverResult
from hosts.types import HostEntry


def _slot_entry(name: str, path: str, confidence: str, notes: Dict[str, Any]) -> HostEntry:
    """One cache entry, carrying the slot identity every consumer reports.

    The slot's branch stays out of the notes: no host projection carries a
    branch name, since the shared telemetry manifest is built from that
    projection and a slot someone checked out to a personal branch would
    upload its name. The commit identifies the slot on its own.
    """
    identity = slot_identity(name)
    return HostEntry(
        name=name,
        kind="runtime-host",
        path=path,
        source=EcosystemCacheResolver.source,
        version=identity["version"],
        version_freshness=identity["refreshed"],
        confidence=confidence,
        notes={
            **notes,
            "commit": identity["commit"],
            "commit_date": identity["commit_date"],
        },
    )


class EcosystemCacheResolver(HostResolver):
    source = "ecosystem-cache"

    def resolve(self, repo_path: str, scan=None) -> ResolverResult:
        """Ambient mode — required by the abstract base, never called.

        `chain.py` never puts this resolver in its chain; fulfillment goes
        through `resolve_for_names`, called only for names an earlier
        resolver signalled the repo needs (see `chain.py::_fulfill_from_cache`).
        """
        return ResolverResult(entries=[], unresolved=[], notes={})

    def resolve_for_names(self, names: Iterable[str]) -> ResolverResult:
        """Fulfillment mode — emit cache entries for explicitly requested
        names only, refreshing each via `ensure_fresh()` first.

        Used by the chain's post-loop fulfillment pass to satisfy unresolved
        host signals from earlier resolvers. Confidence is `high` because the
        slot is guaranteed within the freshness window after `ensure_fresh`.
        Names outside the known ecosystem hosts are ignored.
        """
        requested = {n for n in names if n in KNOWN_ECOSYSTEM_NAMES}
        if not requested:
            return ResolverResult(entries=[], unresolved=[], notes={})

        # `ensure_fresh` creates the cache root before it clones or pulls,
        # so a missing root after it is an OSError it already raised.
        refresh_results: Dict[str, Any] = {
            name: ensure_fresh(name) for name in sorted(requested)
        }

        entries: List[HostEntry] = []
        unresolved: List[Dict[str, Any]] = []
        for name in sorted(requested):
            path = str(cache_dir_for(name))
            refresh = refresh_results.get(name, {})
            if os.path.isdir(path):
                entries.append(
                    _slot_entry(
                        name,
                        path,
                        "high",
                        {
                        "fulfillment": True,
                        "refresh_action": refresh.get("action"),
                        "refresh_ok": refresh.get("ok"),
                        },
                    )
                )
            else:
                unresolved.append({
                    "name": name,
                    "reason": "cache_unpopulated",
                    "source": self.source,
                    "refresh": refresh,
                })
        return ResolverResult(entries=entries, unresolved=unresolved, notes={})

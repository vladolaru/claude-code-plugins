"""Ecosystem-cache resolver — reads the shared pirategoat ecosystem cache."""

import os
from typing import List

from hosts.cache.manager import cache_root
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

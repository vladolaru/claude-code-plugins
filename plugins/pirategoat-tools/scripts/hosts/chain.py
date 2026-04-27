"""Resolver chain — composes resolvers in priority order and emits a manifest."""

import json
import time
from typing import Dict, List, Optional

from hosts.resolvers.base import HostResolver
from hosts.resolvers.explicit import ExplicitResolver
from hosts.resolvers.wp_env import WpEnvResolver
from hosts.resolvers.docker_compose import DockerComposeResolver
from hosts.resolvers.install_cache import InstallCacheResolver
from hosts.resolvers.vendor import VendorResolver
from hosts.types import Banner, HostContextManifest, HostEntry


# Priority order: lower index = higher priority for dedup.
_DEFAULT_RESOLVERS: List[HostResolver] = [
    ExplicitResolver(),
    WpEnvResolver(),
    DockerComposeResolver(),
    InstallCacheResolver(),  # before VendorResolver — cache wins via dedup
    VendorResolver(),
]


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

        for resolver in self.resolvers:
            consulted.append(resolver.source)
            try:
                result = resolver.resolve(repo_path)
            except Exception as err:  # noqa: BLE001 — resolver isolation is the point
                per_resolver[resolver.source] = {
                    "entries": 0,
                    "unresolved": 0,
                    "notes": {"error": f"{type(err).__name__}: {err}"},
                }
                continue
            per_resolver[resolver.source] = {
                "entries": len(result.entries),
                "unresolved": len(result.unresolved),
                "notes": result.notes,
            }
            for entry in result.entries:
                key = f"{entry.kind}:{entry.name}"
                if key not in seen_names:
                    seen_names[key] = entry
                    resolved.append(entry)
            unresolved.extend(result.unresolved)

        unresolved = self._drop_resolved_unresolved(resolved, unresolved)
        banner = self._build_banner(resolved, unresolved)

        diagnostics = {
            "resolvers_consulted": consulted,
            "resolver_detail": per_resolver,
            "runtime_ms": int((time.monotonic() - start) * 1000),
        }

        return HostContextManifest(
            version=1,
            resolved=resolved,
            unresolved=unresolved,
            banner=banner,
            diagnostics=diagnostics,
        )

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
            return Banner(
                degraded=True,
                reason="fully_unavailable",
                message=(
                    "Host context unavailable: no runtime-host resolved. "
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

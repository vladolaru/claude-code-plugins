"""Sibling-convention resolver — checks adjacent dirs for known ecosystem projects."""

import os
from dataclasses import dataclass
from typing import List

from hosts.resolvers.base import HostResolver, ResolverResult
from hosts.types import HostEntry


@dataclass(frozen=True)
class _SiblingTarget:
    sibling_dir: str            # dir name next to repo
    name: str                   # entry name
    sub_path: str = ""          # optional subpath inside the sibling dir


_TARGETS: List[_SiblingTarget] = [
    _SiblingTarget("wordpress-develop", "wordpress"),
    _SiblingTarget("woocommerce-develop", "woocommerce",
                   sub_path="plugins/woocommerce"),
]


class SiblingResolver(HostResolver):
    source = "sibling"

    def resolve(self, repo_path: str) -> ResolverResult:
        parent = os.path.dirname(os.path.abspath(repo_path))
        entries: List[HostEntry] = []
        for target in _TARGETS:
            full_path = os.path.join(parent, target.sibling_dir, target.sub_path) if target.sub_path \
                else os.path.join(parent, target.sibling_dir)
            if os.path.isdir(full_path):
                entries.append(HostEntry(
                    name=target.name,
                    kind="runtime-host",
                    path=full_path,
                    source=self.source,
                    confidence="medium",
                ))
        return ResolverResult(entries=entries, unresolved=[], notes={})

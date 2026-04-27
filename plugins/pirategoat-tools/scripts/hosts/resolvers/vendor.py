"""Library-dep resolver — exposes vendor/ and node_modules/ roots."""

import os
from typing import List

from hosts.resolvers.base import HostResolver, ResolverResult
from hosts.types import HostEntry


class VendorResolver(HostResolver):
    source = "vendor-inspection"

    def resolve(self, repo_path: str) -> ResolverResult:
        entries: List[HostEntry] = []

        # Composer dependencies: expose the root; agents can inspect contents.
        vendor_dir = os.path.join(repo_path, "vendor")
        if os.path.isdir(vendor_dir):
            entries.append(HostEntry(
                name="vendor", kind="library-dep", path=vendor_dir,
                source=self.source, version=None, confidence="high",
            ))

        # npm dependencies: expose the root; agents can inspect contents.
        nm_dir = os.path.join(repo_path, "node_modules")
        if os.path.isdir(nm_dir):
            entries.append(HostEntry(
                name="node_modules", kind="library-dep", path=nm_dir,
                source=self.source, version=None, confidence="high",
            ))

        return ResolverResult(entries=entries, unresolved=[], notes={})

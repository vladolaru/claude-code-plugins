"""Per-clone install-cache resolver — exposes populated cache slots."""

from typing import List

from hosts.install.cache import (
    cache_path_for_clone, clone_id_for, read_stored_lockfile_hash,
)
from hosts.install.lockfile import detect_js_manager, detect_php_manager
from hosts.resolvers.base import HostResolver, ResolverResult
from hosts.types import HostEntry


# Each manager's install produces a known top-level directory inside the
# cache slot. Reviewers Read/Grep that directory. The artifact name doubles
# as the host_context entry name so chain dedup with VendorResolver picks
# whichever resolver runs first (this one, by chain ordering in Task 6).
_ARTIFACT_DIR_BY_MANAGER = {
    "composer": "vendor",
    "npm": "node_modules",
    "pnpm": "node_modules",
    "yarn": "node_modules",
}


class InstallCacheResolver(HostResolver):
    source = "install-cache"

    def resolve(self, repo_path: str) -> ResolverResult:
        entries: List[HostEntry] = []
        clone_id = clone_id_for(repo_path)

        managers = []
        php = detect_php_manager(repo_path)
        if php:
            managers.append(php)
        js = detect_js_manager(repo_path)
        if js:
            managers.append(js)

        for manager in managers:
            artifact = _ARTIFACT_DIR_BY_MANAGER.get(manager)
            if not artifact:
                continue
            slot = cache_path_for_clone(clone_id, manager)
            artifact_path = slot / artifact
            # Only emit when the slot is populated. Use the stored hash
            # marker as the populated signal; a slot directory existing
            # without a marker means a crashed install we shouldn't trust,
            # and a marker without the artifact directory means a partial
            # cleanup. Both halves of the gate must hold.
            if (
                artifact_path.is_dir()
                and read_stored_lockfile_hash(clone_id, manager) is not None
            ):
                entries.append(HostEntry(
                    name=artifact,  # "vendor" or "node_modules" — matches VendorResolver
                    kind="library-dep",
                    path=str(artifact_path),
                    source=self.source,
                    confidence="high",
                ))

        return ResolverResult(entries=entries, unresolved=[], notes={})

"""Per-clone install-cache resolver — exposes populated cache slots."""

from typing import List

from hosts.install.cache import (
    cache_path_for_clone, clone_id_for, clone_root_for, read_stored_lockfile_hash,
)
from hosts.install.lockfile import manager_for_slot
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
        clone_root = clone_root_for(clone_id)
        if not clone_root.is_dir():
            return ResolverResult(entries=entries, unresolved=[], notes={})

        # Enumerate what the installer actually populated rather than
        # re-deriving detection. Dependency roots are scope-derived — they
        # depend on which files a review touched — so re-detecting here
        # would disagree with the installer whenever scope differs, and
        # would miss nested roots (e.g. plugins/woocommerce) entirely.
        for slot_dir in sorted(clone_root.iterdir()):
            # Skips the .realpath marker and in-flight .<slot>.staging.* dirs.
            if not slot_dir.is_dir() or slot_dir.name.startswith("."):
                continue

            slot = slot_dir.name
            artifact = _ARTIFACT_DIR_BY_MANAGER.get(manager_for_slot(slot))
            if not artifact:
                continue
            artifact_path = cache_path_for_clone(clone_id, slot) / artifact
            # Only emit when the slot is populated. Use the stored hash
            # marker as the populated signal; a slot directory existing
            # without a marker means a crashed install we shouldn't trust,
            # and a marker without the artifact directory means a partial
            # cleanup. Both halves of the gate must hold.
            if (
                artifact_path.is_dir()
                and read_stored_lockfile_hash(clone_id, slot) is not None
            ):
                entries.append(HostEntry(
                    name=artifact,  # "vendor" or "node_modules" — matches VendorResolver
                    kind="library-dep",
                    path=str(artifact_path),
                    source=self.source,
                    confidence="high",
                ))

        return ResolverResult(entries=entries, unresolved=[], notes={})

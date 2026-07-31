"""Per-clone install-cache resolver — exposes populated cache slots."""

from typing import List

from hosts.install.cache import (
    cache_path_for_clone, clone_id_for, clone_root_for, read_selected_slots,
    read_stored_inputs_hash,
)
from hosts.install.lockfile import manager_for_slot
from hosts.resolvers.base import HostResolver, ResolverResult
from hosts.types import HostEntry


# Each manager's install produces a known top-level directory inside the
# cache slot. Reviewers Read/Grep that directory. For repo-root slots the
# artifact name doubles as the host_context entry name so chain dedup with
# VendorResolver picks whichever resolver runs first (this one, by chain
# ordering); scoped roots carry the artifact plus their path — see
# _entry_name.
_ARTIFACT_DIR_BY_MANAGER = {
    "composer": "vendor",
    "npm": "node_modules",
    "pnpm": "node_modules",
    "yarn": "node_modules",
}


def _entry_name(artifact: str, slot: str, rel_path) -> str:
    """host_context entry name for one populated slot.

    Repo-root slots keep the bare artifact name ("vendor"/"node_modules"):
    it matches VendorResolver's entry for the same content, so chain dedup
    lets the cache shadow a possibly-stale in-repo directory. Every other
    slot needs its own identity — the chain dedups on kind:name, so shared
    names would silently drop all but the first of several scoped roots.
    The rel_path (from the selection marker) is the readable identity;
    the slot name stands in when only enumeration is available.
    """
    if rel_path in (".", ""):
        return artifact
    if rel_path:
        return f"{artifact}:{rel_path}"
    # Enumeration fallback: no rel_path on record. Bare slots are repo
    # roots by construction (slot_name keeps them as the manager name).
    if slot == manager_for_slot(slot):
        return artifact
    return f"{artifact}:{slot}"


class InstallCacheResolver(HostResolver):
    source = "install-cache"

    def resolve(self, repo_path: str) -> ResolverResult:
        entries: List[HostEntry] = []
        clone_id = clone_id_for(repo_path)
        clone_root = clone_root_for(clone_id)
        if not clone_root.is_dir():
            return ResolverResult(entries=entries, unresolved=[], notes={})

        # Resolve the slots the installer selected for the CURRENT review,
        # recorded in the .dep_roots.json marker. The clone root accumulates
        # every slot ever populated — an old npm slot after a pnpm migration,
        # scoped roots from earlier reviews with different changed files —
        # and enumerating them would expose dependency source the current
        # review never asked for. Without a marker (pre-marker cache layout,
        # or a standalone chain run with no installer) fall back to
        # enumerating populated slots, which is all the information there is.
        selection = read_selected_slots(clone_id)
        if selection is None:
            candidates = [
                (slot_dir.name, None)
                for slot_dir in sorted(clone_root.iterdir())
                # Skips the dot-prefixed markers and in-flight staging dirs.
                if slot_dir.is_dir() and not slot_dir.name.startswith(".")
            ]
        else:
            candidates = list({
                entry["slot"]: (entry["slot"], entry.get("rel_path"))
                for entry in selection
            }.values())

        for slot, rel_path in candidates:
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
                and read_stored_inputs_hash(clone_id, slot) is not None
            ):
                entries.append(HostEntry(
                    name=_entry_name(artifact, slot, rel_path),
                    kind="library-dep",
                    path=str(artifact_path),
                    source=self.source,
                    confidence="high",
                ))

        return ResolverResult(entries=entries, unresolved=[], notes={})

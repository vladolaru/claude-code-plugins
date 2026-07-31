"""Per-clone install cache.

One slot per (clone_id, manager). Slot content is replaced when the
lockfile hash drifts. Atomic staging means a failed install preserves
the prior good cache. Reviewers consume the slot path via the
host_context library-dep entries emitted by InstallCacheResolver.
"""

import hashlib
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from hosts.cache.paths import pirategoat_cache_root


def _cache_root() -> Path:
    return pirategoat_cache_root("library-deps")


def clone_id_for(repo_path: str) -> str:
    """Return an opaque 16-char hex id uniquely identifying this clone.

    Resolves symlinks first so two paths pointing at the same realpath map
    to the same id. The id is the first 16 hex chars of sha256(realpath),
    chosen to be:
      - Filesystem-safe (no slashes, dots, or unicode)
      - Free of ambiguous reversal (paths-with-hyphens vs paths-with-slashes
        produce distinct ids)
      - Short enough to be readable in logs / `du`-output without truncation
    """
    real = os.path.realpath(repo_path)
    return hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]


def clone_root_for(clone_id: str) -> Path:
    """Return the per-clone directory holding all of that clone's slots."""
    return _cache_root() / clone_id


def cache_path_for_clone(clone_id: str, manager: str) -> Path:
    """Return the per-clone cache slot path for a given slot name.

    Layout: <_cache_root()>/<clone_id>/<slot>/
    where _cache_root() resolves to <XDG_CACHE_HOME>/pirategoat/library-deps/.

    The slot is the bare manager name for a repo-root dependency root, or
    "<manager>@<slug>" for a nested one — see lockfile.slot_name.
    """
    return clone_root_for(clone_id) / manager


def _lockfile_hash_path(clone_id: str, manager: str) -> Path:
    return cache_path_for_clone(clone_id, manager) / ".lockfile_hash"


def read_stored_lockfile_hash(clone_id: str, manager: str) -> Optional[str]:
    """Return the lockfile hash currently cached for this clone+manager.

    Returns None if no marker file exists or it is unreadable.
    """
    try:
        return _lockfile_hash_path(clone_id, manager).read_text().strip() or None
    except (FileNotFoundError, OSError):
        return None


def write_stored_lockfile_hash(clone_id: str, manager: str, lockfile_hash: str) -> None:
    """Write the marker file recording which lockfile hash this slot holds."""
    marker = _lockfile_hash_path(clone_id, manager)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(lockfile_hash)


def _realpath_marker_path(clone_id: str) -> Path:
    """Path to the .realpath marker recording the clone's original location.

    Lives at <cache_root>/<clone_id>/.realpath — one level ABOVE the
    per-manager slots, since one realpath corresponds to one clone_id
    regardless of how many managers are detected.
    """
    return _cache_root() / clone_id / ".realpath"


def read_clone_realpath(clone_id: str) -> Optional[str]:
    """Return the realpath recorded for *clone_id*, or None.

    Used by prune_dead_clones (Task 8) to verify the underlying clone
    still exists without reverse-engineering the hash.
    """
    try:
        return _realpath_marker_path(clone_id).read_text().strip() or None
    except (FileNotFoundError, OSError):
        return None


def write_clone_realpath(clone_id: str, repo_path: str) -> None:
    """Record the realpath of the clone so GC can verify liveness later.

    Idempotent — safe to call on every populate. Always writes the canonical
    realpath, not the input path (which may be a symlink).
    """
    marker = _realpath_marker_path(clone_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(os.path.realpath(repo_path))


@dataclass(frozen=True)
class EnsureResult:
    action: str  # "cache_hit" | "installed" | "replaced"
    cache_path: Path


def ensure_current(
    repo_path: str,
    manager: str,
    lockfile_hash: str,
    install_fn: Callable[[Path], None],
) -> EnsureResult:
    """Make sure the per-clone cache slot is populated for *lockfile_hash*.

    - Cache hit: marker matches lockfile_hash → return without calling install_fn.
    - Mismatch / first-time: stage a fresh install in a sibling tmp dir,
      atomic-rename into place, then write the lockfile-hash marker.

    Atomic staging ensures a failed reinstall preserves the prior good cache:
    install_fn writes into <slot>.staging.<pid>.<ts>/, and the rename only
    happens after install_fn returns. If install_fn raises, the staging dir
    is rmtree'd and the existing slot (if any) is untouched.

    The .realpath marker is also written on success so prune_dead_clones
    can verify clone liveness without inverse-engineering the clone_id.
    """
    clone_id = clone_id_for(repo_path)
    slot = cache_path_for_clone(clone_id, manager)
    stored = read_stored_lockfile_hash(clone_id, manager)

    if stored == lockfile_hash and slot.is_dir():
        return EnsureResult(action="cache_hit", cache_path=slot)

    action = "replaced" if slot.is_dir() else "installed"

    # Stage in a sibling dir under <cache_root>/<clone_id>/, distinct enough
    # that two concurrent populates for different (clone, manager) pairs
    # cannot collide.
    slot.parent.mkdir(parents=True, exist_ok=True)
    staging = slot.parent / f".{manager}.staging.{os.getpid()}.{time.monotonic_ns()}"
    if staging.exists():
        shutil.rmtree(staging)  # paranoid: leftover from a crash with same pid+ts
    staging.mkdir(parents=True)

    try:
        install_fn(staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # Promote staging → slot atomically. On POSIX, os.replace() over an
    # existing directory fails — so rmtree the old slot first. The window
    # between rmtree and rename is brief; concurrent reviews on the same
    # (clone, manager) are rare and would each just re-stage.
    if slot.exists():
        shutil.rmtree(slot)
    os.replace(staging, slot)

    write_stored_lockfile_hash(clone_id, manager, lockfile_hash)
    write_clone_realpath(clone_id, repo_path)
    return EnsureResult(action=action, cache_path=slot)


def prune_dead_clones(max_scan: int = 50) -> list:
    """Remove cache entries for clone_ids whose recorded realpath is gone.

    Scans up to *max_scan* clone-id directories under the cache root. For
    each, reads the .realpath marker and rmtree's the entry only if the
    recorded path no longer exists on disk. Entries without a .realpath
    marker (e.g., from a partially-populated state, or the old cache
    layout before this migration) are left alone — conservative.

    Returns the list of removed clone_ids.

    Verification is exact (read the recorded path, stat it). No reverse-
    engineering of clone_id, so paths containing "-" or any other
    character are handled correctly. False positives (deleting a live
    entry) cannot happen with this design.
    """
    root = _cache_root()
    if not root.is_dir():
        return []

    removed = []
    scanned = 0
    for entry in sorted(root.iterdir()):
        if scanned >= max_scan:
            break
        scanned += 1
        if not entry.is_dir():
            continue
        recorded = read_clone_realpath(entry.name)
        if recorded is None:
            # No marker — leave it alone (conservative).
            continue
        if os.path.isdir(recorded):
            # Clone still lives.
            continue
        # Recorded path is gone → safe to remove.
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry.name)
    return removed

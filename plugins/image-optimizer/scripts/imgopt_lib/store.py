"""The variant store: every measured candidate, shared by all working folders.

`candidates` keys each candidate by its source and reference hashes, settings, rung and tool identities
(`candidates._key`). The store holds, per key, the candidate's record (``rec.json``) and its file, so a run in
any working folder reuses a candidate measured in another one: a file set measured once costs nothing to measure
again in a folder of its own. It also remembers the sha256 of every lossy variant it ever held (``produced``):
that is how `candidates` recognises a file an earlier lossy pass produced, however it got into place, and it is
never pruned. Entries unused for ``MAX_AGE_DAYS`` go, and the store is kept under ``MAX_BYTES`` by dropping the
least recently used first (`prune`). Every write is best effort: a sandbox that cannot write the cache runs
without the store.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from .workdirs import cache_root

MAX_AGE_DAYS = 30
MAX_BYTES = 2_000_000_000


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def root() -> Path:
    return cache_root() / "store"


def _entry(key: str) -> Path:
    return root() / "variants" / key[:2] / key


def _produced(sha: str) -> Path:
    return root() / "produced" / sha[:2] / sha


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:  # another file system, or links not allowed
        shutil.copyfile(src, dst)


def get(key: str) -> dict | None:
    """The stored candidate for ``key``, or None; a candidate whose file is gone counts as absent. A hit is a use."""
    entry = _entry(key)
    try:
        rec = json.loads((entry / "rec.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if rec.get("file") and not (entry / rec["file"]).is_file():
        return None
    try:
        os.utime(entry)
    except OSError:
        pass
    return rec


def restore(rec: dict, folder: Path) -> bool:
    """Put ``rec``'s file into ``folder`` from the store; True when ``folder`` then has it."""
    name = rec.get("file")
    if not name:
        return True
    target = Path(folder) / name
    if target.is_file():
        return True
    stored = _entry(rec["key"]) / name
    if not stored.is_file():
        return False
    try:
        _link_or_copy(stored, target)
    except OSError:
        return False
    return True


def mark_produced(sha: str, kind: str) -> None:
    path = _produced(sha)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(kind)
    except OSError:
        pass


def produced_lossy(sha: str) -> bool:
    try:
        return _produced(sha).read_text() == "lossy"
    except OSError:
        return False


def put(rec: dict, folder: Path) -> None:
    """Store ``rec`` (a `candidates` candidate record) and its file from ``folder``. Errors other than an
    encoder's own refusal are not stored: they are retried next time."""
    key = rec.get("key")
    if not key or ("error" in rec and rec.get("error_kind") != "refused"):
        return
    entry = _entry(key)
    try:
        entry.mkdir(parents=True, exist_ok=True)
        if rec.get("file"):
            stored = entry / rec["file"]
            if not stored.is_file():
                _link_or_copy(Path(folder) / rec["file"], stored)
            if rec.get("kind") == "lossy":
                mark_produced(sha256(stored), "lossy")
        tmp = entry / "rec.json.tmp"
        tmp.write_text(json.dumps(rec))
        tmp.replace(entry / "rec.json")
    except OSError:
        pass


def _entries() -> list[tuple[float, int, Path]]:
    """(last use, bytes, folder) of each stored variant. A concurrent run may prune an entry while this lists
    it, and a store folder may be unreadable: whatever vanishes or fails is left out, never raised."""
    found = []
    try:
        shards = list((root() / "variants").iterdir())
    except OSError:
        return found
    for shard in shards:
        try:
            entries = list(shard.iterdir())
        except OSError:  # not a folder, or gone
            continue
        for entry in entries:
            try:
                size = sum(p.stat().st_size for p in entry.iterdir() if p.is_file())
                found.append((entry.stat().st_mtime, size, entry))
            except OSError:
                continue
    return found


def size() -> int:
    return sum(s for _, s, _ in _entries())


def prune(now: float | None = None, max_age_days: float = MAX_AGE_DAYS, max_bytes: int = MAX_BYTES) -> tuple[int, int]:
    """Drop entries unused for ``max_age_days``, then the least recently used until the store holds at most
    ``max_bytes``; returns (entries removed, bytes freed). The ``produced`` index is kept."""
    now = time.time() if now is None else now
    entries = sorted(_entries())
    total = sum(s for _, s, _ in entries)
    removed = freed = 0
    for mtime, size_, entry in entries:
        if now - mtime <= max_age_days * 86400 and total <= max_bytes:
            break
        shutil.rmtree(entry, ignore_errors=True)
        total -= size_
        removed += 1
        freed += size_
    return removed, freed

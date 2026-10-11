"""The variant store: every measured candidate, shared by all working folders.

`candidates` keys each candidate by its source and reference hashes, settings, rung and tool identities
(`candidates._key`). The store holds, per key, the candidate's record (``rec.json``) and its file, so a run in
any working folder reuses a candidate measured in another one: a file set measured once costs nothing to measure
again in a folder of its own. It also remembers the sha256 of every lossy variant it ever held that lost
something (``outputs``: not one with the source's or the reference's bytes, nor a pixel-identical one), with its
kind and the hash of the source it was made from: that is how `candidates` recognises a file an earlier lossy pass
produced, however it got into place, and the index is never pruned. Entries unused for ``MAX_AGE_DAYS`` go, and the
store is kept under ``MAX_BYTES`` by dropping the least recently used first (`prune`). Every write is best effort: a
sandbox that cannot write the cache runs without the store.
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from .workdirs import cache_root

MAX_AGE_DAYS = 30
MAX_BYTES = 2_000_000_000
# The first index (one text file per sha) also marked lossy-kind variants that kept the source's bytes, so it
# refused untouched originals; `prune` deletes it.
LEGACY_INDEX = "produced"


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
    return root() / "outputs" / sha[:2] / f"{sha}.json"


def _link_or_copy(src: Path, dst: Path) -> None:
    """Variant files are write-once: a working folder's file may be a hard link to the store's, so nothing may
    write into one in place (`ladder.generate` and `restore` unlink a file before replacing it). Whoever places a
    variant copies it, never moves or hard-links it."""
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
    """Put ``rec``'s file into ``folder`` from the store; True when ``folder`` then has it. A file of that name
    already there counts only when it is the stored one: a run with other settings or tools in the same folder
    leaves files of the same names with other content, and those are replaced."""
    name = rec.get("file")
    if not name:
        return True
    target = Path(folder) / name
    stored = _entry(rec["key"]) / name
    if not stored.is_file():
        return False
    try:
        if target.is_file() and (target.samefile(stored) or filecmp.cmp(target, stored, shallow=False)):
            return True
        target.unlink(missing_ok=True)
        _link_or_copy(stored, target)
    except OSError:
        return False
    return True


def mark_produced(sha: str, kind: str, source: str) -> None:
    """Index the variant whose bytes hash to ``sha``: its ``kind`` and the hash of the ``source`` it was made from."""
    path = _produced(sha)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"kind": kind, "source": source}))
        tmp.replace(path)
    except OSError:
        pass


def produced_lossy(sha: str) -> bool:
    """Whether a file whose bytes hash to ``sha`` is a lossy variant imgopt produced."""
    try:
        return json.loads(_produced(sha).read_text()).get("kind") == "lossy"
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def put(rec: dict, folder: Path, *, source_sha: str, ref_sha: str) -> None:
    """Store ``rec`` (a `candidates` candidate record) and its file from ``folder``, made from the source and
    measured against the reference whose hashes are ``source_sha`` and ``ref_sha``. Errors other than an encoder's
    own refusal are not stored: they are retried next time. A lossy variant is indexed as produced unless it lost
    nothing: a rung that handed back the source's or the reference's bytes, or pixel-identical output."""
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
            if rec.get("kind") == "lossy" and not rec.get("identical"):
                sha = sha256(stored)
                if sha not in (source_sha, ref_sha):
                    mark_produced(sha, "lossy", source_sha)
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
    ``max_bytes``; returns (entries removed, bytes freed). The ``outputs`` index is kept; the legacy one goes."""
    shutil.rmtree(root() / LEGACY_INDEX, ignore_errors=True)
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

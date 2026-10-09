"""Where imgopt keeps working folders, and how they are removed.

``cache_root()`` is stable across sessions (so a re-run reuses candidates): $IMGOPT_CACHE, else
~/Library/Caches/imgopt on macOS, else $XDG_CACHE_HOME/imgopt or ~/.cache/imgopt. Sandboxes such as Codex's
cannot write there, so ``workdir()`` falls back to $TMPDIR/image-optimization when TMPDIR is set; it never
picks /tmp itself. ``clean()`` deletes a working folder or its losing candidates.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from .formats import WORKDIR_MARKER, WORKDIR_MARKER_TEXT
from .ladder import UsageError


def cache_root() -> Path:
    if os.environ.get("IMGOPT_CACHE"):
        return Path(os.environ["IMGOPT_CACHE"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "imgopt"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "imgopt"


def _slug(task: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")
    if not slug:
        raise UsageError("the task name needs at least one letter or digit")
    return slug[:60]


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".imgopt-write-test"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def workdir(task: str) -> Path:
    """The working folder for ``task``: under the cache root, else under $TMPDIR (sandboxes)."""
    slug = _slug(task)
    options = [cache_root() / "work" / slug]
    if os.environ.get("TMPDIR"):
        options.append(Path(os.environ["TMPDIR"]) / "image-optimization" / slug)
    for folder in options:
        if _writable(folder):
            return folder
    raise UsageError("no writable working folder (tried " + ", ".join(map(str, options)) + "); pass --out yourself")


def mark(out: Path) -> None:
    """Make ``out`` an imgopt working folder: created if needed, never committed (.gitignore), and carrying the
    marker `clean` requires. `candidates` calls it before it writes anything into ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    ignore = out / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")  # a working folder never belongs in a commit
    (out / WORKDIR_MARKER).write_text(WORKDIR_MARKER_TEXT)


def ledger_path() -> Path:
    """One line per lossy pick `apply` wrote (sha256 of the written file): how `candidates` recognises a file
    an earlier lossy pass produced, so it is not re-encoded against itself."""
    return cache_root() / "written.jsonl"


def record_written(path: Path, sha: str, label: str) -> None:
    ledger = ledger_path()
    try:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger, "a") as fh:
            fh.write(json.dumps({"sha256": sha, "path": str(path), "label": label}) + "\n")
    except OSError:  # a sandbox that cannot write the cache: the guard is best effort there
        pass


def written_hashes() -> set[str]:
    try:
        lines = ledger_path().read_text().splitlines()
    except OSError:
        return set()
    hashes = set()
    for line in lines:
        try:
            hashes.add(json.loads(line)["sha256"])
        except (json.JSONDecodeError, KeyError, TypeError):  # a torn or foreign line: skip it, keep the rest
            continue
    return hashes


def folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())


def clean(out: Path, *, keep_picks: bool) -> int:
    """Delete a working folder or, with ``keep_picks``, everything in its record folders except what `sheet`
    and `apply` read (the record, the reference, the source copy and the pick), including the measurement
    folders a killed run leaves behind; returns the bytes freed. The marker, run.json and .gitignore stay, so the folder still names its last run. Only a folder carrying
    the marker `candidates` writes is touched: file names like run.json or metrics.json are common elsewhere."""
    out = Path(out)
    if not (out / WORKDIR_MARKER).is_file():
        raise UsageError(f"{out}: not an imgopt working folder (`candidates` makes one and marks it with "
                         f"{WORKDIR_MARKER}); nothing deleted")
    before = folder_size(out)
    if not keep_picks:
        shutil.rmtree(out)
        return before
    for meta in out.glob("*/metrics.json"):
        try:
            pick = json.loads(meta.read_text()).get("pick") or ""
        except json.JSONDecodeError:  # not a record imgopt wrote (it replaces records whole): leave the folder
            continue
        keep = {"metrics.json", "reference.png", pick}
        for p in meta.parent.iterdir():
            if p.name in keep or p.name.startswith("source."):
                continue
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
    return before - folder_size(out)

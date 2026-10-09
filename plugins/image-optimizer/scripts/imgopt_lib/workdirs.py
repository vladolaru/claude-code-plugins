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

from .formats import WORKDIR_MARKER
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


def folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())


def clean(out: Path, *, keep_picks: bool) -> int:
    """Delete a working folder or, with ``keep_picks``, every file in its record folders except what `sheet`
    and `apply` read (the record, the reference, the source copy and the pick); returns the bytes freed.
    The marker, run.json and .gitignore stay, so the folder still names its last run. Only a folder carrying
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
        except json.JSONDecodeError:  # an interrupted run: leave this folder as it is
            continue
        keep = {"metrics.json", "reference.png", pick}
        for p in meta.parent.iterdir():
            if p.is_file() and p.name not in keep and not p.name.startswith("source."):
                p.unlink()
    return before - folder_size(out)

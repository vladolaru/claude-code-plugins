"""File-format vocabulary and input expansion shared by every imgopt command."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

FORMAT_BY_EXT = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif",
                 ".svg": "svg", ".webp": "webp", ".avif": "avif"}
EXT_BY_FORMAT = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "svg": ".svg",
                 "webp": ".webp", "avif": ".avif"}
INPUT_FORMATS = ("jpeg", "png", "gif", "svg")
OUTPUT_FORMATS = ("keep", "jpeg", "png", "webp", "avif")
VENDORED = frozenset({"node_modules", "vendor", "bower_components"})
# `candidates` puts this file in every --out folder; `clean` deletes only folders that carry it, and folder inputs
# skip them. It is the only sign of a working folder: names like run.json and metrics.json are common in projects.
# It lives here, not in workdirs, because workdirs imports ladder, which imports imaging, which imports this module.
WORKDIR_MARKER = ".imgopt-workdir"
WORKDIR_MARKER_TEXT = "Made by `imgopt candidates`: a working folder that `imgopt clean` may delete.\n"


def format_of(path: Path) -> str | None:
    return FORMAT_BY_EXT.get(Path(path).suffix.lower())


def _ignored(folder: Path, files: list[Path]) -> set[Path]:
    """The files git ignores under ``folder``; empty outside a work tree or without git."""
    if not files:
        return set()
    rel = "\0".join(str(f.relative_to(folder)) for f in files)
    try:
        proc = subprocess.run(["git", "-C", str(folder), "check-ignore", "--stdin", "-z"], input=rel,
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if proc.returncode not in (0, 1):
        return set()
    return {folder / p for p in proc.stdout.split("\0") if p}


def _walk(folder: Path, skipped: list[str]) -> list[Path]:
    found: list[Path] = []
    for f in sorted(folder.rglob("*")):
        rel = f.relative_to(folder)
        if not f.is_file() or format_of(f) not in INPUT_FORMATS or any(p.startswith(".") for p in rel.parts):
            continue
        vendored = next((p for p in rel.parts[:-1] if p in VENDORED), None)
        if vendored:
            skipped.append(f"{f}: under {vendored}/")
            continue
        if any((folder / Path(*rel.parts[:i]) / WORKDIR_MARKER).is_file() for i in range(1, len(rel.parts))):
            skipped.append(f"{f}: inside an imgopt working folder")
            continue
        found.append(f)
    ignored = _ignored(folder, found)
    skipped += [f"{f}: git-ignored" for f in found if f in ignored]
    return [f for f in found if f not in ignored]


def expand_inputs(paths, skipped: list[str] | None = None) -> list[Path]:
    """Files as given (never skipped); folders walked recursively, leaving out hidden parts, vendored folders
    (``VENDORED``), imgopt working folders (``WORKDIR_MARKER``) and git-ignored files, each appended to ``skipped``
    with why."""
    skipped = [] if skipped is None else skipped
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found += _walk(p, skipped)
        elif p.is_file():
            if format_of(p) not in INPUT_FORMATS:
                raise ValueError(f"{p}: not a supported image (jpg, jpeg, png, gif, svg)")
            found.append(p)
        else:
            raise ValueError(f"{p}: does not exist")
    out: list[Path] = []
    seen: set[Path] = set()
    for f in found:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def subdir_name(path: Path) -> str:
    """Readable, collision-free folder name for one input inside --out."""
    p = Path(path).resolve()
    digest = hashlib.sha1(str(p).encode()).hexdigest()[:8]
    # Drop the root anchor: near "/" it would make the name absolute and escape --out.
    return "__".join(p.relative_to(p.anchor).parts[-3:]) + "--" + digest

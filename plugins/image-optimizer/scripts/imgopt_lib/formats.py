"""File-format vocabulary and input expansion shared by every imgopt command."""

from __future__ import annotations

import hashlib
from pathlib import Path

FORMAT_BY_EXT = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif",
                 ".svg": "svg", ".webp": "webp", ".avif": "avif"}
EXT_BY_FORMAT = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "svg": ".svg",
                 "webp": ".webp", "avif": ".avif"}
INPUT_FORMATS = ("jpeg", "png", "gif", "svg")
OUTPUT_FORMATS = ("keep", "jpeg", "png", "webp", "avif")


def format_of(path: Path) -> str | None:
    return FORMAT_BY_EXT.get(Path(path).suffix.lower())


def expand_inputs(paths) -> list[Path]:
    """Files as given, folders walked recursively (hidden parts skipped)."""
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found += sorted(
                f for f in p.rglob("*")
                if f.is_file()
                and format_of(f) in INPUT_FORMATS
                and not any(part.startswith(".") for part in f.relative_to(p).parts)
            )
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

"""corpus.json: one entry per corpus file, with where it came from and how it was made."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image

MANIFEST = "corpus.json"


@dataclass(frozen=True)
class Entry:
    path: str          # relative to the corpus folder: <category>/<name>
    category: str
    origin: str        # URL, "kodak:kodim01", "synthetic", or "derived:<path of its parent>"
    transform: str     # what made it from the origin, with the seed for synthetic files
    license: str
    sha256: str
    width: int | None
    height: int | None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entry_for(path: Path, corpus: Path, **fields) -> Entry:
    try:
        with Image.open(path) as im:
            w, h = im.size
    except OSError:  # SVG, LFS pointer: no raster size
        w = h = None
    return Entry(path=path.relative_to(corpus).as_posix(), sha256=_sha(path), width=w, height=h, **fields)


def write(entries: list[Entry], corpus: Path) -> None:
    data = sorted((asdict(e) for e in entries), key=lambda e: e["path"])
    (corpus / MANIFEST).write_text(json.dumps(data, indent=1))


def read(corpus: Path) -> list[Entry]:
    return [Entry(**e) for e in json.loads((corpus / MANIFEST).read_text())]

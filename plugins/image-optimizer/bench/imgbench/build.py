"""`build`: assemble the corpus folder and corpus.json from the edge, synthetic, derived and GPL builders.

Categories are the plan's keys. The derived ones (every photo variant) come from one derive pass that cannot be
split, so naming any of them rebuilds all six. A rebuilt category's folder is deleted first, so no stale file
survives, and the manifest entries of categories not rebuilt are kept as they are."""

from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path
from typing import Callable

from imgopt_lib import tools as T

from . import derive, edge, manifest, paths, sources, synth

DERIVED = ("photo-camera", "photo-small", "phone-upload", "product-plain", "jpeg-recompressed", "png-master")
CATEGORIES = DERIVED + ("screenshot", "illustration", "icon", "gpl-asset", "edge")
REQUIREMENTS = T.Requirements(("pillow",), (), ("rsvg-convert", "cjpeg"))
SKIPPED_FETCH = "skipped: run fetch first"


def _missing_originals(listed: list[sources.Source], categories: tuple[str, ...]) -> list[str]:
    return [f"{s.category}/{s.key}" for s in listed
            if s.category in categories and not (paths.downloads_dir() / s.category / s.key).is_file()]


def copy_gpl_assets(listed: list[sources.Source], corpus: Path, downloads: Path) -> list[manifest.Entry]:
    """The GPL assets unchanged, named from ASCII slugs of their keys like the derived files."""
    chosen = sorted((s for s in listed if s.category == "gpl-asset"), key=lambda s: s.key)
    entries = []
    for source, stem in zip(chosen, derive.slugs([s.key for s in chosen])):
        dest = corpus / "gpl-asset" / f"{stem}{Path(source.key).suffix.lower()}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(downloads / "gpl-asset" / source.key, dest)
        entries.append(manifest.entry_for(dest, corpus, category="gpl-asset", origin=source.key,
                                          transform="copied unchanged", license=source.license))
    return entries


def _size(nbytes: int) -> str:
    return f"{nbytes / 1_000_000:.1f} MB"


def table(entries: list[manifest.Entry], corpus: Path, notes: dict[str, list[str]]) -> list[str]:
    """One line per category: count, total size, notes; then the corpus total."""
    count: dict[str, int] = defaultdict(int)
    size: dict[str, int] = defaultdict(int)
    for e in entries:
        count[e.category] += 1
        size[e.category] += (corpus / e.path).stat().st_size
    lines = [f"{'category':<18}{'files':>6}{'size':>12}  notes"]
    for category in CATEGORIES:
        if category in count or category in notes:
            lines.append(f"{category:<18}{count[category]:>6}{_size(size[category]):>12}  "
                         + "; ".join(notes.get(category, [])))
    lines.append(f"{'total':<18}{len(entries):>6}{_size(sum(size.values())):>12}")
    return lines


def build(only: list[str] | None, log: Callable[[str], None] = print) -> int:
    """Rebuild the `only` categories (all when None) and rewrite corpus.json. Returns the exit code."""
    chk = T.check(REQUIREMENTS)
    if chk.blocked:
        log("build needs: " + ", ".join(chk.missing_required + chk.missing_quality))
        return 1
    rebuilt = set(only or CATEGORIES)
    if rebuilt & set(DERIVED):
        rebuilt |= set(DERIVED)
    corpus = paths.corpus_dir()
    corpus.mkdir(parents=True, exist_ok=True)
    previous = manifest.read(corpus) if (corpus / manifest.MANIFEST).is_file() else []
    for category in rebuilt:
        shutil.rmtree(corpus / category, ignore_errors=True)
    entries = [e for e in previous if e.category not in rebuilt]
    notes: dict[str, list[str]] = defaultdict(list)
    listed = sources.load(paths.SOURCES_FILE)
    downloads = paths.downloads_dir()

    if "edge" in rebuilt:
        entries += edge.build_all(corpus, chk.tools)
    wanted = [c for c in synth.CATEGORIES if c in rebuilt]
    if wanted:
        made, skipped = synth.build_all(corpus, chk.tools, synth.find_chrome(), only=wanted)
        entries += made
        for note in skipped:  # "<categories> skipped: <reason>": attach it to the categories it names
            for category in wanted:
                if category in note.split(" skipped")[0]:
                    notes[category].append(note.split(": ", 1)[1])
    if rebuilt & set(DERIVED):
        missing = _missing_originals(listed, ("photo-camera", "product-plain", "photo-small"))
        if missing:
            for category in DERIVED:
                notes[category].append(SKIPPED_FETCH)
        else:
            lines: list[str] = []
            entries += derive.build_all(downloads, corpus, chk.tools, log=lines.append)
            for line in lines:
                notes["jpeg-recompressed"].append(line)
    if "gpl-asset" in rebuilt:
        if _missing_originals(listed, ("gpl-asset",)):
            notes["gpl-asset"].append(SKIPPED_FETCH)
        else:
            entries += copy_gpl_assets(listed, corpus, downloads)

    manifest.write(entries, corpus)
    for line in table(entries, corpus, notes):
        log(line)
    return 0

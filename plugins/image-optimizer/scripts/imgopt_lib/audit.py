"""`imgopt inspect`: facts and lossless headroom per file; changes nothing.

A file whose colour profile cannot be converted does not stop the run: its
row carries the facts already read plus an ``error`` reason and no lossless
headroom, and the command exits 1 after printing every row.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import ladder, metrics
from .candidates import kb
from .formats import format_of
from .imaging import ImagingError, display_pixels, estimate_jpeg_quality, read_facts


def inspect_file(path: Path, tools: dict, workdir: Path) -> dict:
    path = Path(path).resolve()
    row = {"path": str(path), "format": format_of(path), "size": path.stat().st_size, "width": None,
           "height": None, "mode": None, "colors": None, "profile": None, "orientation": None,
           "quality": None, "progressive": None, "lossless_size": None, "headroom": None,
           "error": None}
    if row["format"] == "svg":
        return row
    f = read_facts(path)
    row.update(width=f.width, height=f.height, mode=f.mode, colors=f.colors,
               profile=f.icc_desc, orientation=f.orientation, progressive=f.progressive,
               quality=estimate_jpeg_quality(path) if f.format == "jpeg" else None)
    if f.format not in ("jpeg", "png"):
        return row
    try:
        reference = display_pixels(path)
    except ImagingError as error:
        row["error"] = str(error)
        return row
    best = None
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        for rung in ladder.plan(f, profile="lossless").rungs:
            if not all(n in tools and tools[n].ok for n in rung.tools):
                continue
            try:
                out = ladder.generate(rung, inputs={"source": path}, out_dir=Path(tmp), tools=tools)
                if out is None:
                    continue
                ok, _ = metrics.metadata_preserved(f, read_facts(out))
                if not ok or display_pixels(out).tobytes() != reference.tobytes():
                    continue
            except (ladder.EncodeError, ImagingError):
                continue
            size = out.stat().st_size
            best = size if best is None else min(best, size)
    if best is not None:
        row["lossless_size"] = best
        row["headroom"] = max(0, row["size"] - best)
    return row


def print_table(rows: list[dict], log=print) -> None:
    log(f"{'file':48} {'fmt':4} {'size':>9} {'dims':>11} {'mode':5} {'cols':>5} {'q':>3} "
        f"{'prog':4} {'orient':6} {'lossless':>9} {'headroom':>9}  profile")
    for r in rows:
        dims = f"{r['width']}x{r['height']}" if r["width"] else "-"
        profile = f"error: {r['error']}" if r["error"] else r["profile"] or "-"
        log(f"{Path(r['path']).name[:48]:48} {r['format']:4} {kb(r['size']):>9} {dims:>11} "
            f"{(r['mode'] or '-'):5} {str(r['colors'] or '-'):>5} {str(r['quality'] or '-'):>3} "
            f"{('yes' if r['progressive'] else 'no') if r['progressive'] is not None else '-':4} "
            f"{str(r['orientation'] or '-'):6} "
            f"{kb(r['lossless_size']) if r['lossless_size'] else '-':>9} "
            f"{kb(r['headroom']) if r['headroom'] else '-':>9}  {profile}")
    total = sum(r["size"] for r in rows)
    room = sum(r["headroom"] or 0 for r in rows)
    log(f"\n{len(rows)} file(s), {kb(total)}; lossless headroom {kb(room)}. "
        "q is the estimated IJG quality (jpegoptim -m uses a different scale).")

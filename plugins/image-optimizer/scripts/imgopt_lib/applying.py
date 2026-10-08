"""`imgopt apply`: write approved picks, then prove what was written.

Lossy picks are refused unless --approved is passed, which the skill allows
only after the human approved on the comparison page. A source that changed
since `candidates` ran is refused. Each pick is staged beside its target and
re-measured against the recorded reference there; only a staged file that
matches the recorded numbers replaces the target. A mismatch deletes the
staged file and leaves the target's bytes and mtime as they were.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from . import ladder, metrics
from .candidates import kb, load_records, pick_of, sha256
from .imaging import READ_FAILURES, display_pixels, read_facts
from .ladder import UsageError
from .sheet import needs_tiles

TOLERANCE = {"ssim": 1e-6, "ss2": 1e-3, "band": 1e-6}


def _records(out: Path) -> list[tuple[Path, dict]]:
    out = Path(out)
    rows = load_records(out) if out.is_dir() else []
    if not rows:
        raise UsageError(f"{out}: no candidates results here (expected <folder>/metrics.json from "
                         "`imgopt candidates --out`); check the path")
    return rows


def _matches(record: dict, name: str) -> bool:
    path = record["source"]["path"]
    return name in (path, Path(path).name)


def _selected(out: Path, only) -> tuple[list[tuple[Path, dict]], list[str]]:
    """(the records `only` selects or all of them, the `only` names that match no record)."""
    rows = _records(out)
    if not only:
        return rows, []
    return ([(f, r) for f, r in rows if any(_matches(r, n) for n in only)],
            [n for n in only if not any(_matches(r, n) for _, r in rows)])


def _to_apply(out: Path, only) -> list[tuple[Path, dict]]:
    return [(f, r) for f, r in _selected(out, only)[0] if r["verdict"] == "apply"]


def needs_metrics(out: Path, only=()) -> bool:
    """True when a selected pick is lossy, so verifying it needs ffmpeg and ssimulacra2."""
    return any(needs_tiles(r) for _, r in _to_apply(out, only))


def needs_rsvg(out: Path, only=()) -> bool:
    """True when a selected pick is an SVG, so verifying it needs rsvg-convert."""
    return any(r["format"] == "svg" for _, r in _to_apply(out, only))


def _took_source(record: dict, chosen: dict, facts) -> bool:
    """Whether the pick was made from the source file itself, which is what the metadata check covers.

    Pixel-input rungs bake orientation and convert device profiles by design, so only the plan
    (the one place that knows each rung's input) can say; a rung it cannot find is checked. The test
    is `candidates._judge`'s: a lossless-kind rung (``Rung.kind``).
    """
    plan = ladder.plan(facts, profile=record.get("profile", "lossless"), out_format=record["format"],
                       resize=record.get("resize"))
    rung = next((r for r in plan.rungs if r.label == chosen["label"]), None)
    return rung is None or rung.kind == "lossless"


def _verify(folder: Path, record: dict, chosen: dict, written: Path, tools: dict) -> str:
    """Empty when ``written`` is what the record promised, else why it is not."""
    fmt = record["format"]
    if fmt == "svg":
        ok = metrics.svg_identical(tools["rsvg-convert"].path, next(folder.glob("source.*")), written, folder)
        return "" if ok else "rendering differs from the source"
    if fmt == "gif":
        return "" if metrics.frames_identical(next(folder.glob("source.*")), written) else "frames differ"
    ref = Image.open(folder / "reference.png").convert("RGBA")
    if chosen.get("identical"):
        source_facts = read_facts(next(folder.glob("source.*")))
        if _took_source(record, chosen, source_facts):
            ok, why = metrics.metadata_preserved(source_facts, read_facts(written))
            if not ok:
                return why
        pixels = display_pixels(written)
        same = pixels.size == ref.size and pixels.tobytes() == ref.tobytes()
        return "" if same else "pixels differ from the reference"
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        s = metrics.measure(ref, display_pixels(written), tools, Path(tmp))
    drifts = (_drift(k, tol, chosen.get(k), getattr(s, k)) for k, tol in TOLERANCE.items())
    return "; ".join(d for d in drifts if d)


def _drift(key: str, tol: float, recorded, now) -> str:
    """Why ``now`` is not the recorded score, or empty. A score that is absent or not a number never passes."""
    for label, value in (("recorded", recorded), ("re-measured", now)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{key} not {label}"
    return "" if abs(now - recorded) <= tol else f"{key} recorded {recorded} now {now}"


def _write(folder: Path, record: dict, chosen: dict, target: Path, tools: dict) -> str:
    """Stage the pick beside ``target``, verify it there, and move it over the target only if it passed."""
    # Hidden, unique (never a user's file, safe beside a concurrent run), and keeps the extension for the decoders.
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=".imgopt-", suffix=target.suffix)
    os.close(fd)
    staged = Path(name)
    try:
        shutil.copyfile(folder / chosen["file"], staged)
        shutil.copymode(target if target.exists() else folder / chosen["file"], staged)
        problem = _verify(folder, record, chosen, staged, tools)
        if not problem:
            os.replace(staged, target)
        return problem
    finally:
        staged.unlink(missing_ok=True)


def apply(out: Path, *, tools: dict, only=(), approved: bool = False, dest: Path | None = None, log=print) -> int:
    selected, unmatched = _selected(Path(out), only)
    for name in unmatched:
        log(f"No result in {out} for --only {name!r}; use a file name or path as `candidates` recorded it.")
    rows = [(f, r) for f, r in selected if r["verdict"] == "apply"]
    if not rows:
        if not unmatched:
            log("Nothing to apply.")
        return 1 if unmatched else 0
    if dest:
        clashes = sorted({n for n in (Path(r["target"]).name for _, r in rows)
                          if sum(Path(r["target"]).name == n for _, r in rows) > 1})
        if clashes:
            raise UsageError(f"--dest {dest}: more than one result writes a file named {', '.join(clashes)}; "
                             "apply them separately with --only")
    lossy = [r for _, r in rows if needs_tiles(r)]
    if lossy and not approved:
        log("REFUSED: these picks are lossy and need the human's approval on the comparison page first:")
        for r in lossy:
            log(f"  {r['source']['path']}  ({pick_of(r)['label']})")
        log("Show the page (imgopt sheet), get an explicit yes, then re-run with --approved.")
        return 1
    stale = [r for _, r in rows if not Path(r["source"]["path"]).is_file()
             or sha256(Path(r["source"]["path"])) != r["source"]["sha256"]]
    if stale:
        log("REFUSED: these files changed since candidates ran; re-run candidates first:")
        for r in stale:
            log(f"  {r['source']['path']}")
        return 1
    failures = len(unmatched)
    written = before = after = 0
    for folder, record in rows:
        chosen = pick_of(record)
        target = Path(record["target"])
        if dest:
            target = Path(dest) / target.name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            problem = _write(folder, record, chosen, target, tools)
        except (metrics.MetricError, ValueError, *READ_FAILURES) as error:  # ValueError: Pillow, ladder.plan
            problem = f"could not verify: {error}"
        if problem:
            failures += 1
            log(f"  MISMATCH {target}: {problem}; the target was left as it was")
            continue
        written += 1
        before += record["source"]["size"]
        after += target.stat().st_size
        log(f"  verified {target}  {kb(record['source']['size'])} -> {kb(target.stat().st_size)}  "
            f"({chosen['label']})")
    log(f"\nWrote {written} of {len(rows)} file(s): {kb(before)} -> {kb(after)}."
        + (f" {failures} problem(s): investigate before committing." if failures else ""))
    return 1 if failures else 0

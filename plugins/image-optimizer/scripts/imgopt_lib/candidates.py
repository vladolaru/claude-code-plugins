"""`imgopt candidates`: try the ladder on each input, measure, gate, pick, cache.

Per input, a folder under --out holds the source copy, the prepared
reference (reference.png, what the pick is measured against), the pixel
inputs, the candidates and metrics.json. metrics.json is both the cache
(each candidate keyed by input and reference hashes, settings and tool
versions, so a re-run only redoes what changed) and the record that `sheet`
and `apply` read. Schema: see SCHEMA and the plan's Task 7 interface block.

A source whose colour profile cannot be converted (ImagingError) is skipped
by run(): it logs the reason, writes nothing for that file and carries on, so
callers compare len(records) with len(inputs) to learn that files were skipped.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import gates as G
from . import ladder, metrics
from .formats import EXT_BY_FORMAT, format_of, subdir_name
from .imaging import ImagingError, display_pixels, flatten, read_facts, srgb_shift
from .tools import describe

SCHEMA = 1
CACHE_VERSION = 1
UNCALIBRATED = ("webp", "avif")
METRIC_TOOLS = ("ffmpeg", "ssimulacra2", "butteraugli_main")
# Besides its encoder, a cached verdict depends on whatever decoded, converted and compared the pixels.
RASTER_VERDICT_TOOLS = frozenset({"pillow", *METRIC_TOOLS})
SVG_VERDICT_TOOLS = frozenset({"pillow", "rsvg-convert"})


@dataclass(frozen=True)
class Options:
    profile: str
    out: Path
    gates: G.Gates
    ref: Path | None = None
    resize: int | None = None
    out_format: str = "keep"
    waived: tuple[str, ...] = ()


def kb(n: int) -> str:
    return f"{n / 1024:.1f} KB"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pick_of(record: dict) -> dict | None:
    return next((c for c in record["candidates"] if c.get("file") == record.get("pick")), None)


def load_records(out: Path) -> list[tuple[Path, dict]]:
    found = []
    for path in sorted(Path(out).glob("*/metrics.json")):
        found.append((path.parent, json.loads(path.read_text())))
    return found


def _previous(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return {c["key"]: c for c in json.loads(path.read_text()).get("candidates", []) if "key" in c}
    except (json.JSONDecodeError, KeyError):
        return {}


def keyed_tools(rung: ladder.Rung) -> list[str]:
    """Every tool whose version can change this rung's cached result: the encoder and the verdict tools."""
    return sorted(rung.tools | (SVG_VERDICT_TOOLS if rung == ladder.SVG_RUNG else RASTER_VERDICT_TOOLS))


def _key(src_hash: str, ref_hash: str, opts: Options, rung: ladder.Rung, tools: dict) -> str:
    names = keyed_tools(rung)
    payload = {"v": CACHE_VERSION, "src": src_hash, "ref": ref_hash, "resize": opts.resize,
               "format": opts.out_format, "rung": [rung.label, rung.tool, list(rung.args), rung.input,
                                                   rung.post_oxipng, rung.post_jpegtran],
               "tools": {n: tools[n].version for n in names if n in tools and tools[n].ok}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _available(rung: ladder.Rung, tools: dict) -> bool:
    return all(n in tools and tools[n].ok for n in rung.tools)


def _reusable(old: dict | None, folder: Path) -> bool:
    """A cached candidate is reused when its file is still there; a recorded error is retried."""
    return bool(old) and "error" not in old and (folder / old["file"]).is_file()


def _perfect(rec: dict) -> dict:
    rec.update(identical=True, ssim=1.0, ssim_white=1.0, ss2=100.0, band=0.0, butteraugli=0.0)
    return rec


def _run_rung(rung, key, inputs, folder, facts, ref_img, tools, can_measure) -> dict:
    rec = {"key": key, "label": rung.label, "tool": rung.tool, "kind": rung.kind, "band_gated": rung.palette}
    try:
        out = ladder.generate(rung, inputs=inputs, out_dir=folder, tools=tools)
    except ladder.EncodeError as error:
        rec["error"] = str(error)
        return rec
    if out is None:
        rec["error"] = "pngquant could not reach this quality range"
        return rec
    rec["file"], rec["size"] = out.name, out.stat().st_size
    ofacts = read_facts(out)
    rec["progressive"] = ofacts.progressive
    if rung.kind == "lossless" and rung.input == "source":
        ok, why = metrics.metadata_preserved(facts, ofacts)
        if not ok:
            rec["discarded"] = why
            return rec
    if facts.format == "gif":
        if not metrics.frames_identical(inputs["source"], out):
            rec["discarded"] = "gifsicle changed the frames"
        elif not inputs["ref_is_source"]:
            rec["discarded"] = "the reference is not the source; GIF supports only lossless identity"
        else:
            _perfect(rec)
        return rec
    try:
        cand = display_pixels(out)
    except ImagingError as error:
        rec["error"] = str(error)
        return rec
    rec["identical"] = cand.size == ref_img.size and cand.tobytes() == ref_img.tobytes()
    if rec["identical"]:
        return _perfect(rec)
    if rung.kind == "lossless":
        rec["kind"] = "lossy"
    if not can_measure:
        rec["discarded"] = "pixels differ and the metric tools are absent (lossless profile)"
        return rec
    try:
        with tempfile.TemporaryDirectory(dir=folder) as tmp:
            s = metrics.measure(ref_img, cand, tools, Path(tmp))
    except metrics.MetricError as error:
        rec["error"] = str(error)
        return rec
    rec.update(ssim=s.ssim, ssim_white=s.ssim_white, ss2=s.ss2, band=s.band, butteraugli=s.butteraugli)
    return rec


def _svg_rung(rung, key, inputs, folder, src, tools) -> dict:
    rec = {"key": key, "label": rung.label, "tool": rung.tool, "kind": "lossless", "band_gated": False}
    try:
        out = ladder.generate(rung, inputs=inputs, out_dir=folder, tools=tools)
        rec["file"], rec["size"], rec["progressive"] = out.name, out.stat().st_size, False
        if metrics.svg_identical(tools["rsvg-convert"].path, src, out, folder):
            _perfect(rec)
        else:
            rec["discarded"] = "svgo changed the rendering"
    except (ladder.EncodeError, metrics.MetricError) as error:
        rec["error"] = str(error)
    return rec


def _process(src: Path, opts: Options, tools: dict) -> dict:
    folder = opts.out / subdir_name(src)
    ref_path = opts.ref or src
    src_hash, ref_hash = sha256(src), sha256(ref_path)
    fmt = format_of(src)
    notes: list[str] = []
    can_measure = all(n in tools and tools[n].ok for n in ("ffmpeg", "ssimulacra2"))
    if fmt == "svg":
        if opts.resize or opts.out_format != "keep":
            raise ladder.UsageError(f"{src.name}: SVG supports only in-place lossless optimization")
        if opts.profile != "lossless":
            notes.append("SVG gets the lossless svgo rung only")
        rungs, target_fmt, facts = [ladder.SVG_RUNG], "svg", None
        source_info = {"format": "svg", "width": None, "height": None, "colors": None}
        folder.mkdir(parents=True, exist_ok=True)
    else:
        facts = read_facts(src)
        plan = ladder.plan(facts, profile=opts.profile, out_format=opts.out_format, resize=opts.resize)
        notes += plan.notes
        rungs, target_fmt = plan.rungs, plan.out_format
        source_info = {"format": facts.format, "width": facts.width, "height": facts.height, "colors": facts.colors}
        # Prepared before the folder exists: an unconvertible profile skips the file cleanly.
        ref_img = display_pixels(ref_path, width=opts.resize)
        pix = display_pixels(src, width=opts.resize)
        folder.mkdir(parents=True, exist_ok=True)
        if target_fmt == "jpeg" and facts.has_alpha:
            ref_img = flatten(ref_img, "white").convert("RGBA")
        ref_img.save(folder / "reference.png")
        pix.save(folder / "pixels.png")
        flatten(pix, "white").save(folder / "pixels_flat.png")
        flatten(pix, "white").save(folder / "pixels.ppm")
        if facts.device_profile:
            mean, peak = srgb_shift(src)
            notes.append(f"device colour profile '{facts.icc_desc}': pixel candidates are converted to sRGB "
                         f"(the conversion alone shifts mean {mean:.1f}, max {peak} of 255)")
        if facts.orientation != 1:
            notes.append(f"EXIF orientation {facts.orientation}: lossless candidates keep the tag, "
                         "pixel candidates have it applied")
        if opts.resize:
            notes.append(f"resized {facts.width}x{facts.height} -> {pix.width}x{pix.height} (Lanczos); "
                         "the reference is scaled the same way")
    previous = _previous(folder / "metrics.json")
    shutil.copyfile(src, folder / f"source{src.suffix.lower()}")
    if opts.ref:
        notes.append(f"measured against {opts.ref}")
    skipped = [r for r in rungs if not _available(r, tools)]
    if skipped:
        missing = sorted({n for r in skipped for n in r.tools if not (n in tools and tools[n].ok)})
        notes.append(f"skipped {len(skipped)} rung(s), tools waived: {', '.join(missing)}")
    inputs = {"source": src, "pixels": folder / "pixels.png", "pixels_flat": folder / "pixels_flat.png",
              "pixels_ppm": folder / "pixels.ppm", "ref_is_source": ref_hash == src_hash}
    cands = []
    for rung in (r for r in rungs if _available(r, tools)):
        key = _key(src_hash, ref_hash, opts, rung, tools)
        old = previous.get(key)
        if _reusable(old, folder):
            rec = dict(old)
        elif fmt == "svg":
            rec = _svg_rung(rung, key, inputs, folder, src, tools)
        else:
            rec = _run_rung(rung, key, inputs, folder, facts, ref_img, tools, can_measure)
        rec["pass"], rec["reason"] = G.evaluate(rec, opts.gates)
        cands.append(rec)
    chosen = G.pick(cands)
    in_place = target_fmt == fmt and not opts.resize
    size = src.stat().st_size
    verdict, why = G.verdict(size, chosen, in_place=in_place)
    target = src if in_place else src.with_suffix(EXT_BY_FORMAT[target_fmt])
    record = {
        "schema": SCHEMA,
        "source": {"path": str(src), "sha256": src_hash, "size": size, **source_info},
        "ref": {"path": str(ref_path), "sha256": ref_hash},
        "profile": opts.profile, "gates": asdict(opts.gates), "resize": opts.resize,
        "format": target_fmt, "target": str(target), "uncalibrated": target_fmt in UNCALIBRATED,
        "waived": list(opts.waived), "notes": notes,
        "tools": {n: {"path": t.path, "version": t.version} for n, t in sorted(tools.items()) if t.ok},
        "candidates": cands, "pick": chosen["file"] if chosen else None,
        "verdict": verdict, "verdict_reason": why,
    }
    tmp = folder / "metrics.json.tmp"
    tmp.write_text(json.dumps(record, indent=1))
    tmp.replace(folder / "metrics.json")
    return record


def _fmt_score(c: dict) -> str:
    if "ssim" not in c:
        return f"{'-':>8} {'-':>6} {'-':>11}"
    band = f"{c['band']:.1f} {'gated' if c.get('band_gated') else 'rep.'}"
    return f"{c['ssim']:8.4f} {c['ss2']:6.1f} {band:>11}"


def print_record(record: dict, log=print) -> None:
    s = record["source"]
    dims = f"{s['width']}x{s['height']} " if s.get("width") else ""
    colours = f", {s['colors']} colours" if s.get("colors") else ""
    log(f"\n== {s['path']}  {kb(s['size'])}  {dims}{s['format']}{colours}")
    for note in record["notes"]:
        log(f"   note: {note}")
    log(f"   {'candidate':22} {'size':>9} {'saved':>6} {'SSIM':>8} {'ss2':>6} {'banding':>11}  result")
    for c in sorted(record["candidates"], key=lambda c: c.get("size", 1 << 62)):
        if "size" in c:
            saved = f"{(c['size'] - s['size']) / s['size']:+.0%}"
            size = kb(c["size"])
        else:
            saved, size = "", ""
        result = "PASS" + (" (identical)" if c.get("identical") else "") if c["pass"] else f"fail: {c['reason']}"
        log(f"   {c['label']:22} {size:>9} {saved:>6} {_fmt_score(c)}  {result}")
    chosen = pick_of(record)
    pick_text = f"{chosen['label']} ({kb(chosen['size'])})" if chosen else "none"
    log(f"   pick: {pick_text}   verdict: {record['verdict']} ({record['verdict_reason']})")


def summarize(records: list[dict], out: Path, script: Path, tools: dict, log=print) -> None:
    applied = [r for r in records if r["verdict"] == "apply"]
    before = sum(r["source"]["size"] for r in applied)
    after = sum(pick_of(r)["size"] for r in applied)
    saved = f" (-{(before - after) / before:.0%})" if before else ""
    log(f"\n{len(records)} file(s): {len(applied)} to apply, {len(records) - len(applied)} untouched. "
        f"Apply total {kb(before)} -> {kb(after)}{saved}.")
    log(describe(tools))
    if any(r["uncalibrated"] for r in records):
        log("UNCALIBRATED FORMAT: WebP/AVIF output was never calibrated against these gates; "
            "every sheet tile is required viewing.")
    waived = sorted({w for r in records for w in r["waived"]})
    if waived:
        log("WAIVED TOOLS (state this in any report): " + ", ".join(waived))
    lossy = [r for r in applied if pick_of(r)["kind"] == "lossy"]
    if lossy:
        log(f"Next: python3 {script} sheet {out}   (view the required tiles, show the page; "
            f"{len(lossy)} lossy pick(s) need the human's approval before apply --approved)")
    elif applied:
        log(f"Next: python3 {script} apply {out}   (all picks are lossless; no approval gate)")
    else:
        log("Next: nothing to apply.")


def run(inputs: list[Path], opts: Options, tools: dict, log=print, script: Path | None = None) -> list[dict]:
    """One record per input that could be prepared; a skipped input is logged and left out."""
    opts.out.mkdir(parents=True, exist_ok=True)
    records = []
    for src in inputs:
        try:
            record = _process(Path(src), opts, tools)
        except ImagingError as error:
            log(f"\n== {src}: skipped: {error}")
            continue
        print_record(record, log)
        records.append(record)
    summarize(records, opts.out, script or Path("imgopt.py"), tools, log)
    return records

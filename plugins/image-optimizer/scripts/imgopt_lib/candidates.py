"""`imgopt candidates`: try the ladder on each input, measure, gate, pick, cache.

Per input, a folder under --out holds the source copy, the prepared
reference (reference.png, what the pick is measured against), the pixel
inputs the ladder reads (written only when a rung needs them), the
candidates and metrics.json. metrics.json is both the cache (each
candidate keyed by input and reference hashes, settings and tool versions,
so a re-run only redoes what changed) and the record that `sheet` and
`apply` read. It is rewritten after every candidate with ``complete``
false, so an interrupted run resumes from its last finished candidate and
is not applied; the finished record says ``complete`` true. run.json names
the inputs and settings of the last run: `load_records` keeps only the
complete records that match it, and `stale_records` says why it left each
other folder out. Schema: see SCHEMA.

A source that cannot be read or whose colour profile cannot be converted
(imaging.READ_FAILURES: also 16-bit files, content that does not match its
extension, and a --resize that would upscale or keep the width)
is skipped by run(): it logs the reason, writes nothing for that file and
carries on, so callers compare len(records) with len(inputs) to learn that
files were skipped. A record whose every candidate errored (all_errored) is a
problem too.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import compare as CP
from . import gates as G
from . import ladder, metrics
from .formats import EXT_BY_FORMAT, format_of, subdir_name
from .imaging import (READ_FAILURES, ImagingError, display_pixels, flatten, metadata_kinds, read_facts,
                      srgb_shift)
from .tools import OPTIONAL_ENCODERS, describe
from .workdirs import folder_size

SCHEMA = 1
# Bump when a cached record would be wrong or lack a key. 5: every raster rung records metadata_removed;
# 3: svgo keeps ids, roles and classes; 2: a rung's kind follows its input.
CACHE_VERSION = 5
RUN_FILE = "run.json"  # the last `candidates` run in this --out: which inputs, which settings
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


def all_errored(record: dict) -> bool:
    """Every candidate failed to encode or measure: a tool problem, not "nothing passed the gates"."""
    return bool(record["candidates"]) and all("error" in c for c in record["candidates"])


def pick_of(record: dict) -> dict | None:
    """The picked candidate, or None. Without a pick, a candidate that has no file (it errored) is not it."""
    if not record.get("pick"):
        return None
    return next((c for c in record["candidates"] if c.get("file") == record.get("pick")), None)


def write_run(out: Path, inputs: list[Path], opts: Options) -> None:
    """Record which inputs and settings this run has; `load_records` keeps only what matches it."""
    run = {"schema": SCHEMA, "inputs": sorted(str(Path(p).resolve()) for p in inputs), "profile": opts.profile,
           "resize": opts.resize, "format": opts.out_format}
    tmp = out / (RUN_FILE + ".tmp")
    tmp.write_text(json.dumps(run, indent=1))
    tmp.replace(out / RUN_FILE)


def _scan(out: Path) -> tuple[list[tuple[Path, dict]], list[str]]:
    run_path = Path(out) / RUN_FILE
    run = json.loads(run_path.read_text()) if run_path.is_file() else None
    current, stale = [], []
    for path in sorted(Path(out).glob("*/metrics.json")):
        record = json.loads(path.read_text())
        name = path.parent.name
        if not record.get("complete", True):
            stale.append(f"{name}: interrupted before its last candidate")
        elif run is None:  # a folder written by hand (tests) has no manifest: every complete record counts
            current.append((path.parent, record))
        elif record["source"]["path"] not in run["inputs"]:
            stale.append(f"{name}: {Path(record['source']['path']).name} is not in the last run")
        elif (record.get("profile"), record.get("resize"), record.get("format_requested", run["format"])) != (
                run["profile"], run["resize"], run["format"]):
            stale.append(f"{name}: made with other settings than the last run")
        else:
            current.append((path.parent, record))
    return current, stale


def load_records(out: Path) -> list[tuple[Path, dict]]:
    """The complete records of the last run (``RUN_FILE``); what `sheet` and `apply` act on."""
    return _scan(out)[0]


def stale_records(out: Path) -> list[str]:
    """Record folders ``load_records`` leaves out, with why: other inputs, other settings, or interrupted."""
    return _scan(out)[1]


def _write_record(folder: Path, record: dict) -> None:
    tmp = folder / "metrics.json.tmp"
    tmp.write_text(json.dumps(record, indent=1))
    tmp.replace(folder / "metrics.json")


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
               "tools": {n: tools[n].cache_id for n in names if n in tools and tools[n].ok}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _available(rung: ladder.Rung, tools: dict) -> bool:
    return all(n in tools and tools[n].ok for n in rung.tools)


def _reusable(old: dict | None, folder: Path) -> bool:
    """A cached candidate is reused when its file is still there, and so is an encoder's own refusal of this
    input (same key = same input and build). Other errors (timeouts, a tool that could not start) are retried."""
    if not old:
        return False
    if "error" in old:
        return old.get("error_kind") == "refused"
    return (folder / old["file"]).is_file()


def _perfect(rec: dict) -> dict:
    rec.update(identical=True, ssim=1.0, ssim_white=1.0, ss2=100.0, band=0.0, butteraugli=0.0)
    return rec


def _run_rung(rung, key, inputs, folder, facts, source_kinds, ref_img, tools, can_measure) -> dict:
    rec = {"key": key, "label": rung.label, "tool": rung.tool, "kind": rung.kind, "band_gated": rung.palette}
    try:
        out = ladder.generate(rung, inputs=inputs, out_dir=folder, tools=tools)
    except ladder.EncodeError as error:
        rec["error"] = str(error)
        if isinstance(error, ladder.EncoderRefused):
            rec["error_kind"] = "refused"
        return rec
    if out is None:
        rec["error"] = "pngquant could not reach this quality range"
        return rec
    rec["file"], rec["size"] = out.name, out.stat().st_size
    try:
        return _judge(rec, rung, out, inputs, facts, source_kinds, ref_img, tools, can_measure, folder)
    except READ_FAILURES as error:
        rec["error"] = str(error)
        return rec


def _judge(rec, rung, out, inputs, facts, source_kinds, ref_img, tools, can_measure, folder) -> dict:
    """Read the encoded file and fill in its metadata verdict and scores."""
    ofacts = read_facts(out)
    rec["progressive"] = ofacts.progressive
    removed = sorted(source_kinds - metadata_kinds(out))
    if removed:
        rec["metadata_removed"] = removed
    if rung.kind == "lossless":  # made from the source file, so its metadata must survive
        ok, why = metrics.metadata_preserved(facts, ofacts)
        if not ok:
            rec["discarded"] = why
            return rec
    if facts.format == "gif" or facts.frames > 1:
        if not metrics.frames_identical(inputs["source"], out):
            rec["discarded"] = f"{rung.tool} changed the frames"
        elif not inputs["ref_is_source"]:
            rec["discarded"] = "the reference is not the source; GIF supports only lossless identity"
        else:
            _perfect(rec)
        return rec
    cand = display_pixels(out)
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
        if not metrics.svg_identical(tools["rsvg-convert"].path, src, out, folder):
            rec["discarded"] = "svgo changed the rendering"
        elif lost := metrics.svg_semantics_lost(src, out):
            rec["discarded"] = f"svgo {lost} (ids, roles and aria attributes must survive)"
        else:
            _perfect(rec)
    except (ladder.EncodeError, metrics.MetricError) as error:
        rec["error"] = str(error)
        if isinstance(error, ladder.EncoderRefused):
            rec["error_kind"] = "refused"
    return rec


def _reviewer_check(rec: dict, ref_path: Path, ref_facts, folder: Path, tools: dict) -> None:
    """Record the SSIM a reviewer's ffmpeg one-liner (`compare`) prints for this candidate as
    ``ssim_evidence``, or None with ``evidence_note`` saying why ffmpeg alone cannot reproduce it.
    A failed ffmpeg run is also marked ``evidence_failed``, so the next run checks again (see _reuse)."""
    new = folder / rec["file"]
    ri, ni = display_pixels(ref_path), display_pixels(new)
    obstacles = CP.reviewer_obstacles(ref_facts, read_facts(new), ri.size, ni.size)
    rec["ssim_evidence"] = None
    if obstacles:
        rec["evidence_note"] = "; ".join(obstacles)
        return
    try:
        rec["ssim_evidence"], _ = CP.reviewer_check(tools["ffmpeg"].path, ref_path, new, ri, ni)
    except metrics.MetricError as error:
        rec["evidence_note"] = f"ffmpeg could not read the pair ({error})"
        rec["evidence_failed"] = True


def _reuse(old: dict) -> dict:
    """A cached candidate as recorded, except a reviewer check whose ffmpeg run failed, which is redone."""
    rec = dict(old)
    if rec.pop("evidence_failed", False):
        rec.pop("ssim_evidence", None)
        rec.pop("evidence_note", None)
    return rec


def _pick(cands: list[dict], gates: G.Gates, check) -> dict | None:
    """The smallest passing candidate whose Evidence SSIM also clears the SSIM floor.

    ffmpeg decodes JPEG differently from Pillow (up to about 1e-3), so a pick at a gate SSIM of 0.9801
    could show a reviewer 0.979. Only picks are checked, one ffmpeg run each: a pick that fails is marked
    failed and the next one is tried. A cached candidate keeps its ``ssim_evidence``, already gated.
    """
    while (chosen := G.pick(cands)) is not None:
        if gates.ssim is None or chosen.get("identical") or "ssim_evidence" in chosen:
            return chosen
        check(chosen)
        chosen["pass"], chosen["reason"] = G.evaluate(chosen, gates)
        if chosen["pass"]:
            return chosen
    return None


def _no_pick_reason(cands: list[dict], opts: Options) -> str:
    """Why nothing was picked, with the closest measured candidate so the human can judge the gap.
    A file whose candidates all errored has a tool problem (run() reports it), not a quality question."""
    why = "no candidate passed the gates"
    if cands and all(c.get("error") for c in cands):
        return why + "; every candidate errored"
    measured = [c for c in cands if c.get("ssim") is not None and not c.get("error") and not c.get("discarded")]
    if measured:
        best = max(measured, key=lambda c: (c["ssim"], c["ss2"]))
        why += (f"; closest: {best['label']} at SSIM {best['ssim']:.4f}, ss2 {best['ss2']:.1f} "
                f"(failed: {best['reason']})")
    if opts.resize and opts.profile == "high":
        medium = G.PROFILES["medium"]
        why += (f"; for a resize, --profile medium (SSIM {medium.ssim:g}, ss2 {medium.ss2:g}) is the usual "
                "next step if the human accepts that floor")
    return why


def _process(src: Path, opts: Options, tools: dict) -> dict:
    folder = opts.out / subdir_name(src)
    ref_path = opts.ref or src
    src_hash, ref_hash = sha256(src), sha256(ref_path)
    fmt = format_of(src)
    notes: list[str] = []
    can_measure = all(n in tools and tools[n].ok for n in ("ffmpeg", "ssimulacra2"))
    if fmt == "svg":
        if opts.profile != "lossless":
            notes.append("SVG gets the lossless svgo rung only")
        rungs, target_fmt, facts = [ladder.SVG_RUNG], "svg", None
        source_info = {"format": "svg", "width": None, "height": None, "colors": None, "frames": 1}
        folder.mkdir(parents=True, exist_ok=True)
    else:
        facts = read_facts(src)
        source_kinds = metadata_kinds(src)
        ref_facts = read_facts(ref_path) if opts.ref else facts
        if opts.resize and opts.resize == facts.display_width == ref_facts.display_width:
            raise ImagingError(f"{src.name}: already displays {opts.resize} px wide, so there is nothing to "
                               "resize (optimize it without --resize)")
        plan = ladder.plan(facts, profile=opts.profile, out_format=opts.out_format, resize=opts.resize)
        notes += plan.notes
        rungs, target_fmt = plan.rungs, plan.out_format
        source_info = {"format": facts.format, "width": facts.width, "height": facts.height, "colors": facts.colors,
                       "frames": facts.frames}
        # Prepared before the folder exists: an unconvertible profile skips the file cleanly.
        ref_img = display_pixels(ref_path, width=opts.resize)
        pix = display_pixels(src, width=opts.resize)
        folder.mkdir(parents=True, exist_ok=True)
        if target_fmt == "jpeg" and facts.has_alpha:
            ref_img = flatten(ref_img, "white").convert("RGBA")
        ref_img.save(folder / "reference.png")
        # Pixel files are written only when a rung reads them: a lossless job needs none.
        needed = {r.input for r in rungs}
        flat = flatten(pix, "white") if needed & {"pixels_flat", "pixels_gray", "pixels_ppm"} else None
        if "pixels" in needed:
            pix.save(folder / "pixels.png")
        if "pixels_flat" in needed:
            flat.save(folder / "pixels_flat.png")
        if "pixels_gray" in needed:
            flat.convert("L").save(folder / "pixels_gray.png")
        if "pixels_ppm" in needed:
            flat.save(folder / "pixels.ppm")
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
    missing = sorted({n for r in skipped for n in r.tools if not (n in tools and tools[n].ok)})
    optional_missing = [n for n in missing if n in OPTIONAL_ENCODERS]
    unwaived = [n for n in missing if n not in opts.waived and n not in OPTIONAL_ENCODERS]
    if unwaived:  # the strict tool check must have blocked these; going on would silently skip rungs
        raise RuntimeError(f"{src.name}: the ladder needs {', '.join(unwaived)}, which the tool check neither "
                           "found nor saw waived (an imgopt bug: tools.requirements and the ladder disagree)")
    if optional_missing:
        n_opt = sum(1 for r in skipped if set(r.tools) & set(optional_missing))
        notes.append(f"{', '.join(optional_missing)} not installed: {n_opt} rung(s) skipped "
                     "(optional; picks may be larger)")
    waived_here = [n for n in missing if n in opts.waived]
    if waived_here:
        n_waived = sum(1 for r in skipped if set(r.tools) & set(waived_here))
        notes.append(f"skipped {n_waived} rung(s), tools waived: {', '.join(waived_here)}")
    inputs = {"source": src, "pixels": folder / "pixels.png", "pixels_flat": folder / "pixels_flat.png",
              "pixels_gray": folder / "pixels_gray.png",
              "pixels_ppm": folder / "pixels.ppm", "ref_is_source": ref_hash == src_hash}
    cands = []
    for rung in (r for r in rungs if _available(r, tools)):
        key = _key(src_hash, ref_hash, opts, rung, tools)
        old = previous.get(key)
        if _reusable(old, folder):
            rec = _reuse(old)
        elif fmt == "svg":
            rec = _svg_rung(rung, key, inputs, folder, src, tools)
        else:
            rec = _run_rung(rung, key, inputs, folder, facts, source_kinds, ref_img, tools, can_measure)
        rec["pass"], rec["reason"] = G.evaluate(rec, opts.gates)
        cands.append(rec)
        _write_record(folder, {"schema": SCHEMA, "complete": False, "candidates": cands})
    if fmt == "svg":
        chosen = G.pick(cands)
    else:
        chosen = _pick(cands, opts.gates, lambda rec: _reviewer_check(rec, ref_path, ref_facts, folder, tools))
    # A same-format job rewrites the source itself (a resize too); only a new format gets a new name.
    target = src if target_fmt == fmt else src.with_suffix(EXT_BY_FORMAT[target_fmt])
    size = src.stat().st_size
    verdict, why = G.verdict(size, chosen, in_place=target == src and not opts.resize,
                             replaces_source=target == src)
    if chosen is None:
        why = _no_pick_reason(cands, opts)
    record = {
        "schema": SCHEMA,
        "source": {"path": str(src), "sha256": src_hash, "size": size, **source_info},
        "ref": {"path": str(ref_path), "sha256": ref_hash},
        "profile": opts.profile, "gates": asdict(opts.gates), "resize": opts.resize,
        "format_requested": opts.out_format, "format": target_fmt, "target": str(target),
        "uncalibrated": target_fmt in UNCALIBRATED,
        "waived": list(opts.waived), "optional_missing": optional_missing, "notes": notes,
        "tools": {n: {"path": t.path, "version": t.version, "id": t.cache_id}
                  for n, t in sorted(tools.items()) if t.ok},
        "candidates": cands, "pick": chosen["file"] if chosen else None,
        "verdict": verdict, "verdict_reason": why, "complete": True,
    }
    _write_record(folder, record)
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
    if chosen and chosen.get("metadata_removed"):
        log(f"   pick removes metadata: {', '.join(chosen['metadata_removed'])} "
            "(say so to the human: copyright and credit live there)")
    if chosen and "ssim_evidence" in chosen:
        evidence = (f"{chosen['ssim_evidence']:.6f}" if chosen["ssim_evidence"] is not None
                    else f"not reproducible with ffmpeg alone ({chosen['evidence_note']})")
        log(f"   Evidence SSIM (luma): {evidence}")


def summarize(records: list[dict], out: Path, script: Path, tools: dict, log=print) -> None:
    applied = [r for r in records if r["verdict"] == "apply"]
    before = sum(r["source"]["size"] for r in applied)
    after = sum(pick_of(r)["size"] for r in applied)
    total_before = sum(r["source"]["size"] for r in records)
    total_after = sum(pick_of(r)["size"] if r in applied else r["source"]["size"] for r in records)
    # Signed like the comparison page's totals: a resized or converted pick can be larger than its original.
    change = f" ({(total_after - total_before) / total_before:+.1%})" if total_before else ""
    log(f"\n{len(records)} file(s), {kb(total_before)} -> {kb(total_after)}{change} overall; "
        f"{len(applied)} to apply ({kb(before)} -> {kb(after)}), {len(records) - len(applied)} untouched.")
    log(describe(tools))
    if any(r["uncalibrated"] for r in records):
        log("UNCALIBRATED FORMAT: WebP/AVIF output was never calibrated against these gates; "
            "every sheet tile is required viewing.")
    larger = [r for r in applied if pick_of(r)["size"] > r["source"]["size"]]
    if larger:
        log(f"LARGER: {len(larger)} pick(s) are larger than their original (a resize or format change was "
            "asked for, so they still apply); "
            "tell the human before applying: " + ", ".join(Path(r["source"]["path"]).name for r in larger))
    waived = sorted({w for r in records for w in r["waived"]})
    if waived:
        log("WAIVED TOOLS (fewer candidates were tried, so picks may be larger than with them; state this in "
            "any report): " + ", ".join(waived))
    absent = sorted({n for r in records for n in r.get("optional_missing", [])})
    if absent:
        log("OPTIONAL TOOLS MISSING (picks may be larger; mention it in any report): " + ", ".join(absent))
    lossy = [r for r in applied if pick_of(r)["kind"] == "lossy"]
    q = shlex.quote
    command = f"python3 {q(str(script))}"
    log(f"Working folder: {q(str(out))} ({folder_size(out) / 1e6:.1f} MB); remove it with: "
        f"{command} clean {q(str(out))}")
    if lossy:
        log(f"Next: {command} sheet {q(str(out))}   (view the required tiles, show the page; "
            f"{len(lossy)} lossy pick(s) need the human's approval before apply --approve)")
    elif applied:
        log(f"Next: {command} apply {q(str(out))}   (all picks are lossless; no approval gate)")
    else:
        log("Next: nothing to apply.")


def check_inputs(inputs: list[Path], opts: Options) -> list[tuple[Path, str]]:
    """The inputs the ladder cannot serve, with why; nothing is written yet. When every input is refused the
    request itself is wrong, so that is a UsageError naming the first file."""
    refused = []
    for src in inputs:
        try:
            ladder.check_job(format_of(src), profile=opts.profile, out_format=opts.out_format, resize=opts.resize)
        except ladder.UsageError as error:
            refused.append((Path(src), str(error)))
    if inputs and len(refused) == len(inputs):
        src, why = refused[0]
        raise ladder.UsageError(f"{src.name}: {why}")
    return refused


def run(inputs: list[Path], opts: Options, tools: dict, log=print, script: Path | None = None) -> list[dict]:
    """One record per input that could be prepared; a skipped input is logged and left out.

    An input the ladder cannot serve is logged and left out; a request it cannot serve for any input is a
    UsageError before anything is written.
    """
    inputs = [Path(p).resolve() for p in inputs]  # records and RUN_FILE name inputs by the same string
    refused = check_inputs(inputs, opts)
    for src, why in refused:
        log(f"\n== {src}: skipped: {why}")
    refused_paths = {src for src, _ in refused}
    inputs = [p for p in inputs if Path(p) not in refused_paths]
    opts.out.mkdir(parents=True, exist_ok=True)
    ignore = opts.out / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")  # a working folder never belongs in a commit
    write_run(opts.out, inputs, opts)
    records = []
    for src in inputs:
        try:
            record = _process(Path(src), opts, tools)
        except READ_FAILURES as error:
            log(f"\n== {src}: skipped: {error}")
            continue
        print_record(record, log)
        if all_errored(record):
            log(f"   problem: every candidate errored, so this file was not optimized "
                f"(first error: {record['candidates'][0]['error']})")
        records.append(record)
    summarize(records, opts.out, script or Path("imgopt.py"), tools, log)
    return records

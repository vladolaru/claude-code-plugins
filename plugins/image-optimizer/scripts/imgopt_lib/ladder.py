"""The candidate ladder: which encoder settings imgopt tries for a file.

``plan()`` is pure (facts, profile, output format in; rungs out) so the
ladder is testable without encoders. ``generate()`` runs one rung. Inputs
are named: "source" (the file itself), "pixels" (RGBA PNG of what the viewer
sees, resized when asked), "pixels_flat" (that, flattened onto white),
"pixels_ppm" (the same as PPM for cjpeg). Lossless rungs read "source" so
metadata checks apply; anything re-encoded from pixels bakes orientation and
sRGB in.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .imaging import Facts

JPEG_LEVELS = tuple(range(95, 35, -5))          # 95 .. 40
PNG_QUALITY_RANGES = ("95-100", "90-100", "85-100", "80-95", "70-95", "60-85", "50-80")
PNG_COLOURS = (256, 192, 128, 96, 64, 48, 32, 16)
FEW_COLOURS = 32
PNG_KEEP_WITH_EXIF = "eXIf,cICP,iCCP,sRGB,pHYs,acTL,fcTL,fdAT"
GUETZLI_LEVELS = (84, 90)
WEB_LEVELS = tuple(range(95, 45, -5))           # 95 .. 50
GUETZLI_PROGRESSIVE = True                      # jpegtran -progressive shrank guetzli output 4-6% on all three samples
SVGO_CONFIG = Path(__file__).resolve().parents[1] / "svgo.config.mjs"


class UsageError(ValueError):
    pass


class EncodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Rung:
    label: str
    tool: str
    kind: str          # "lossless" or "lossy"
    input: str         # key into generate()'s inputs
    ext: str
    args: tuple[str, ...]
    post_oxipng: bool = False
    palette: bool = False
    post_jpegtran: bool = False

    @property
    def tools(self) -> set[str]:
        extra = {"oxipng"} if self.post_oxipng else set()
        return {self.tool} | extra | ({"jpegtran"} if self.post_jpegtran else set())


@dataclass(frozen=True)
class Plan:
    rungs: list[Rung]
    pixel_encode: bool
    out_format: str
    notes: list[str] = field(default_factory=list)


SVG_RUNG = Rung("svgo", "svgo", "lossless", "source", ".svg", ())


def _jpeg_strip_args(f: Facts) -> tuple[str, ...]:
    args = ["--strip-com", "--strip-iptc", "--strip-xmp", "--all-progressive"]
    if f.orientation == 1:
        args.insert(0, "--strip-exif")
    return tuple(args)


def plan(f: Facts, *, profile: str, out_format: str = "keep", resize: int | None = None) -> Plan:
    target = f.format if out_format == "keep" else out_format
    lossy = profile != "lossless"
    notes: list[str] = []
    if f.format == "gif":
        if target != "gif" or resize:
            raise UsageError("GIF supports only in-place lossless optimization")
        if lossy:
            notes.append("GIF gets the lossless gifsicle rung only")
        return Plan([Rung("gifsicle", "gifsicle", "lossless", "source", ".gif", ("-O3",))], False, "gif", notes)
    if f.format not in ("jpeg", "png") or target not in ("jpeg", "png", "webp", "avif"):
        raise UsageError(f"cannot turn {f.format} into {target}")
    pixel = bool(resize) or target != f.format or (lossy and f.device_profile)
    rungs: list[Rung] = []
    if target == "jpeg":
        if pixel and not lossy:
            raise UsageError("the lossless profile cannot re-encode JPEG pixels (resize, colour "
                             "conversion or format change); use --profile high or medium")
        if f.has_alpha and f.format != "jpeg":
            notes.append("JPEG has no transparency: transparent areas are flattened onto white")
        gray = f.mode in ("L", "LA")
        if not pixel:
            rungs.append(Rung("lossless-jpegoptim", "jpegoptim", "lossless", "source", ".jpg", _jpeg_strip_args(f)))
            rungs.append(Rung("lossless-jpegtran", "jpegtran", "lossless", "source", ".jpg",
                              ("-optimize", "-progressive", "-copy", "all" if f.orientation != 1 else "icc")))
            if lossy:
                rungs += [Rung(f"jpegoptim-m{q}", "jpegoptim", "lossy", "source", ".jpg",
                               (f"-m{q}",) + _jpeg_strip_args(f)) for q in JPEG_LEVELS]
        else:
            rungs += [Rung(f"cjpeg-q{q}", "cjpeg", "lossy", "pixels_ppm", ".jpg",
                           ("-quality", str(q), "-optimize", "-progressive")) for q in JPEG_LEVELS]
        if lossy:
            g_in = "source" if (not pixel and f.orientation == 1 and not gray) else "pixels_flat"
            rungs += [Rung(f"guetzli-q{q}", "guetzli", "lossy", g_in, ".jpg", ("--quality", str(q)),
                           post_jpegtran=GUETZLI_PROGRESSIVE) for q in GUETZLI_LEVELS]
    elif target == "png":
        # --strip safe drops eXIf, the PNG orientation carrier; a source that has one keeps
        # exactly the chunks --strip safe keeps plus eXIf. Baked pixels have no orientation.
        keep_orientation = not pixel and f.orientation != 1
        strip = ("--keep", PNG_KEEP_WITH_EXIF) if keep_orientation else ("--strip", "safe")
        rungs.append(Rung("oxipng", "oxipng", "lossless", "pixels" if pixel else "source", ".png",
                          ("-o", "max", *strip)))
        if lossy:
            if f.colors is not None and f.colors <= FEW_COLOURS:
                notes.append(f"source has {f.colors} colours: palette candidates skipped, lossless is the floor")
            else:
                rungs += [Rung(f"pngquant-q{r}", "pngquant", "lossy", "pixels", ".png", (f"--quality={r}",),
                               post_oxipng=True, palette=True) for r in PNG_QUALITY_RANGES]
                rungs += [Rung(f"pngquant-c{n}", "pngquant", "lossy", "pixels", ".png", (str(n),),
                               post_oxipng=True, palette=True)
                          for n in PNG_COLOURS if f.colors is None or n < f.colors]
    elif target == "webp":
        rungs.append(Rung("cwebp-lossless", "cwebp", "lossless", "pixels", ".webp", ("-lossless", "-z", "9")))
        if lossy:
            rungs += [Rung(f"cwebp-q{q}", "cwebp", "lossy", "pixels", ".webp", ("-q", str(q), "-m", "6"))
                      for q in WEB_LEVELS]
    else:
        rungs.append(Rung("avifenc-lossless", "avifenc", "lossless", "pixels", ".avif", ("--lossless",)))
        if lossy:
            rungs += [Rung(f"avifenc-q{q}", "avifenc", "lossy", "pixels", ".avif", ("-q", str(q), "-s", "0"))
                      for q in WEB_LEVELS]
    return Plan(rungs, pixel, target, notes)


def _exe(tools: dict, name: str) -> str:
    tool = tools.get(name)
    if tool is None or not tool.ok:
        raise EncodeError(f"{name} is not available")
    return tool.path


def _run(argv: list[str], label: str, tool: str) -> subprocess.CompletedProcess:
    """Run one encoder; every way it can fail to run surfaces as EncodeError naming ``label`` and ``tool``."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=3600)
    except subprocess.TimeoutExpired as error:
        raise EncodeError(f"{label}: {tool} timed out after {error.timeout:.0f}s") from error
    except OSError as error:
        raise EncodeError(f"{label}: cannot run {tool}: {error}") from error


def _tail(proc: subprocess.CompletedProcess) -> str:
    return (proc.stderr or proc.stdout).strip()[-300:]


def generate(rung: Rung, *, inputs: dict, out_dir: Path, tools: dict) -> Path | None:
    """Run one rung and return its output, or None when pngquant cannot reach its quality range.

    Raises EncodeError when an encoder or post-pass cannot run or exits non-zero; no partial
    output is left in ``out_dir`` in that case.
    """
    exe = _exe(tools, rung.tool)
    src = Path(inputs[rung.input])
    out = Path(out_dir) / f"{rung.label}{rung.ext}"
    scratch = out.with_suffix(".prog.jpg")
    for leftover in (out, scratch):
        leftover.unlink(missing_ok=True)
    try:
        return _encode(rung, exe, src, out, scratch, tools)
    except BaseException:
        for leftover in (out, scratch):
            leftover.unlink(missing_ok=True)
        raise


def _encode(rung: Rung, exe: str, src: Path, out: Path, scratch: Path, tools: dict) -> Path | None:
    if rung.tool == "jpegoptim":
        shutil.copyfile(src, out)
        argv = [exe, "-q", *rung.args, str(out)]
    elif rung.tool in ("jpegtran", "cjpeg"):
        argv = [exe, *rung.args, "-outfile", str(out), str(src)]
    elif rung.tool == "guetzli":
        argv = [exe, *rung.args, str(src), str(out)]
    elif rung.tool == "oxipng":
        argv = [exe, *rung.args, "-q", "--out", str(out), str(src)]
    elif rung.tool == "pngquant":
        argv = [exe, *rung.args, "--speed", "1", "--force", "--output", str(out), "--", str(src)]
    elif rung.tool == "gifsicle":
        argv = [exe, *rung.args, "-o", str(out), str(src)]
    elif rung.tool == "svgo":
        argv = [exe, "--config", str(SVGO_CONFIG), str(src), "-o", str(out)]
    elif rung.tool == "cwebp":
        argv = [exe, *rung.args, str(src), "-o", str(out)]
    elif rung.tool == "avifenc":
        argv = [exe, *rung.args, str(src), str(out)]
    else:
        raise EncodeError(f"no runner for {rung.tool}")
    proc = _run(argv, rung.label, rung.tool)
    if rung.tool == "pngquant" and proc.returncode == 99:
        return None
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        raise EncodeError(f"{rung.label}: {Path(exe).name} exited {proc.returncode}: {_tail(proc)}")
    if rung.post_oxipng:
        post = _exe(tools, "oxipng")
        proc = _run([post, "-o", "max", "--strip", "safe", "-q", str(out)], rung.label, "oxipng")
        if proc.returncode != 0:
            raise EncodeError(f"{rung.label}: post-pass {Path(post).name} exited {proc.returncode}: {_tail(proc)}")
    if rung.post_jpegtran:
        post = _exe(tools, "jpegtran")
        proc = _run([post, "-optimize", "-progressive", "-copy", "none", "-outfile", str(scratch), str(out)],
                    rung.label, "jpegtran")
        if proc.returncode != 0 or not scratch.is_file():
            raise EncodeError(f"{rung.label}: post-pass {Path(post).name} exited {proc.returncode}: {_tail(proc)}")
        scratch.replace(out)
    return out

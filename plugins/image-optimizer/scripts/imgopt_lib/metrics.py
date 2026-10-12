"""Measurements for imgopt: the one place a score is computed.

SSIM is ffmpeg's ``ssim`` filter on gray-converted inputs (``SSIM_GRAY``, which
the reviewer one-liner in compare.py uses too), so JPEG, PNG and alpha PNG
share one parse path (``All:``). ssimulacra2 runs on the same flattened
PNGs; ``measure`` computes only what the gates read, and butteraugli, which is
reported and never gated, runs through ``butteraugli_score`` on a pick. Images with alpha are flattened onto white and onto
dark grey and the worse value counts, because quantized soft edges fringe on
dark backgrounds; ``ssim_white`` is kept separately because it is the number
a reviewer's ffmpeg one-liner reproduces. The banding score is the session's
gradient-quantisation measure (prbuild/banding.py), rewritten with Pillow
filters: the 99.5th percentile of the max-channel difference over areas of
the reference that are smooth (3x3 spread 1..6) but not flat.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageSequence

from .imaging import Facts, alpha_used, backgrounds, flatten

SSIM_RE = re.compile(r"All:\s*([0-9.]+)")
FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")
MIN_SMOOTH_PIXELS = 50
# What both inputs of every SSIM go through, here and in the reviewer one-liner (compare.py). ffmpeg's ssim
# (vf_ssim.c) uses C1 = .01²·255²·64 against sums over 8x8 windows, 1/64 of Wang et al.'s constant, so near
# black a window of 0s against one of 1s scores 0.09 (the paper: 0.87). A JPEG whose luma DC step puts black
# on a rounding tie decodes it as 1 in libjpeg-turbo, and whole black backgrounds scored as lost (SSIM 0.42 at
# ss2 76); noisy near-black backgrounds hit the same term. Luminance is compared on value + SSIM_PEDESTAL,
# scaled into 16 bits so no two levels merge; ordinary content between SSIM 0.95 and 0.99 moves by +0.0003
# (median) to +0.0013, within the decoder spread the floors already absorb, so no floor was re-calibrated
# (analysis 2026-10-12). 4 is about the black of a 1000:1 display (sRGB code 3.3).
SSIM_PEDESTAL = 4
SSIM_GRAY = (f"format=gray,format=gray16le,"
             f"lut=c0={SSIM_PEDESTAL * 256}+val*{65535 - SSIM_PEDESTAL * 256}/65535+0.5")
# The PNGs handed to ffmpeg, ssimulacra2 and butteraugli are read once and deleted: compressing them harder
# only costs time (Pillow's default level 6 took 3x as long on a 12 MP image).
SCRATCH_PNG = {"compress_level": 1}


class MetricError(RuntimeError):
    pass


@dataclass(frozen=True)
class Scores:
    ssim: float
    ssim_white: float
    ss2: float
    band: float


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MetricError(f"{Path(argv[0]).name} could not run ({exc.__class__.__name__}: {exc})") from exc


def ssim_gray(ffmpeg: str, a: Path, b: Path) -> float:
    proc = _run([ffmpeg, "-hide_banner", "-nostats", "-i", str(a), "-i", str(b), "-lavfi",
                 f"[0:v]{SSIM_GRAY}[x];[1:v]{SSIM_GRAY}[y];[x][y]ssim", "-f", "null", "-"])
    match = SSIM_RE.search(proc.stderr)
    if not match:
        raise MetricError(f"ffmpeg printed no SSIM for {a.name} vs {b.name}: {proc.stderr.strip()[-300:]}")
    return float(match.group(1))


def ssimulacra2(exe: str, ref: Path, new: Path) -> float:
    proc = _run([exe, str(ref), str(new)])
    match = FLOAT_RE.search(proc.stdout)
    if proc.returncode != 0 or not match:
        raise MetricError(f"ssimulacra2 failed on {new.name}: {(proc.stderr or proc.stdout).strip()[-300:]}")
    return float(match.group(0))


def butteraugli(exe: str, ref: Path, new: Path) -> float | None:
    """The score, or None when butteraugli cannot run or prints no number (it is optional)."""
    try:
        proc = _run([exe, str(ref), str(new)])
    except MetricError:
        return None
    match = FLOAT_RE.search(proc.stdout)
    return float(match.group(0)) if proc.returncode == 0 and match else None


def diff_max(a: Image.Image, b: Image.Image) -> Image.Image:
    out = None
    for ca, cb in zip(a.convert("RGB").split(), b.convert("RGB").split()):
        d = ImageChops.difference(ca, cb)
        out = d if out is None else ImageChops.lighter(out, d)
    return out


def smooth_mask(rgb: Image.Image) -> Image.Image:
    spread = None
    for channel in rgb.convert("RGB").split():
        s = ImageChops.subtract(channel.filter(ImageFilter.MaxFilter(3)),
                                channel.filter(ImageFilter.MinFilter(3)))
        spread = s if spread is None else ImageChops.lighter(spread, s)
    return spread.point(lambda v: 255 if 1 <= v <= 6 else 0)


def percentile_from_histogram(hist: list[int], q: float) -> float:
    """numpy's default (linear) percentile over the values a histogram counts."""
    total = sum(hist)
    if total == 0:
        return 0.0
    pos = (total - 1) * q / 100
    lo, hi = int(pos), min(int(pos) + 1, total - 1)

    def value_at(k: int) -> int:
        seen = 0
        for value, count in enumerate(hist):
            seen += count
            if seen > k:
                return value
        return len(hist) - 1

    v_lo, v_hi = value_at(lo), value_at(hi)
    return v_lo + (v_hi - v_lo) * (pos - lo)


def band_score(ref: Image.Image, new: Image.Image, mask: Image.Image | None = None) -> float:
    """``mask`` is ``smooth_mask(ref)``, passed in when the caller scores many candidates against one reference."""
    if ref.size != new.size:
        raise MetricError(f"banding needs equal sizes, got {ref.size} and {new.size}")
    mask = smooth_mask(ref) if mask is None else mask
    hist = diff_max(ref, new).histogram(mask=mask)
    if sum(hist) < MIN_SMOOTH_PIXELS:
        return 0.0
    return percentile_from_histogram(hist, 99.5)


class ReferenceCache:
    """One reference's flattened pixels, their PNG in ``workdir`` and their smooth mask per background, made on
    first use instead of once per candidate. Threads measuring candidates of the same source share one."""

    def __init__(self, ref: Image.Image, workdir: Path):
        self.ref, self.workdir = ref, Path(workdir)
        self._made: dict[str, tuple[Image.Image, Path, Image.Image]] = {}
        self._lock = threading.Lock()

    def get(self, bg: str) -> tuple[Image.Image, Path, Image.Image]:
        with self._lock:
            if bg not in self._made:
                r = flatten(self.ref, bg)
                rp = self.workdir / f"ref_{bg}.png"
                r.save(rp, **SCRATCH_PNG)
                self._made[bg] = (r, rp, smooth_mask(r))
            return self._made[bg]


def measure(ref: Image.Image, new: Image.Image, tools: dict, workdir: Path, *,
            ref_cache: ReferenceCache | None = None) -> Scores:
    """``ref_cache``, made for ``ref`` by a caller measuring many candidates of one source, must live as long
    as its folder; without one the reference is prepared for this call alone."""
    if ref.size != new.size:
        raise MetricError(f"cannot compare {ref.size} with {new.size}")
    workdir.mkdir(parents=True, exist_ok=True)
    ffmpeg = tools["ffmpeg"].path
    ss2_exe = tools["ssimulacra2"].path
    ssims, ss2s, bands = {}, [], []
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        cache = ref_cache if ref_cache is not None else ReferenceCache(ref, Path(tmp))
        for bg in backgrounds(alpha_used(ref) or alpha_used(new)):
            r, rp, mask = cache.get(bg)
            n = flatten(new, bg)
            np_ = Path(tmp) / f"new_{bg}.png"
            n.save(np_, **SCRATCH_PNG)
            ssims[bg] = ssim_gray(ffmpeg, rp, np_)
            ss2s.append(ssimulacra2(ss2_exe, rp, np_))
            bands.append(band_score(r, n, mask))
    return Scores(min(ssims.values()), ssims["white"], min(ss2s), max(bands))


def butteraugli_score(ref: Image.Image, new: Image.Image, tools: dict, workdir: Path) -> float | None:
    """Worst-background butteraugli for one pair, or None when the tool is absent or fails (reported only)."""
    ba = tools.get("butteraugli_main")
    if ba is None or not ba.ok:
        return None
    workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        values = []
        for bg in backgrounds(alpha_used(ref) or alpha_used(new)):
            rp, np_ = Path(tmp) / f"r_{bg}.png", Path(tmp) / f"n_{bg}.png"
            flatten(ref, bg).save(rp, **SCRATCH_PNG)
            flatten(new, bg).save(np_, **SCRATCH_PNG)
            value = butteraugli(ba.path, rp, np_)
            if value is None:
                return None
            values.append(value)
    return max(values)


def metadata_preserved(src: Facts, out: Facts) -> tuple[bool, str]:
    """Lossless means the file still displays the same: orientation, device profile and PNG colour chunks intact."""
    if src.orientation != out.orientation:
        return False, f"orientation tag {src.orientation} became {out.orientation}"
    if src.device_profile and out.icc != src.icc:
        return False, f"device colour profile '{src.icc_desc}' was not preserved"
    if not src.device_profile and out.device_profile:
        return False, "output gained a device colour profile"
    if src.colour_chunks != out.colour_chunks:
        lost = sorted({k for k, _ in set(src.colour_chunks) ^ set(out.colour_chunks)})
        return False, f"PNG colour chunks changed ({', '.join(lost)}): browsers would render it differently"
    return True, ""


def frames_identical(a: Path, b: Path) -> bool:
    with Image.open(a) as ia, Image.open(b) as ib:
        fa = [f.convert("RGBA").tobytes() for f in ImageSequence.Iterator(ia)]
        fb = [f.convert("RGBA").tobytes() for f in ImageSequence.Iterator(ib)]
        return ia.size == ib.size and fa == fb


def svg_identical(rsvg: str, a: Path, b: Path, workdir: Path) -> bool:
    workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        renders = []
        for name, svg in (("a", a), ("b", b)):
            png = Path(tmp) / f"{name}.png"
            proc = _run([rsvg, "-z", "2", "-o", str(png), str(svg)])
            if proc.returncode != 0:
                raise MetricError(f"rsvg-convert failed on {svg.name}: {proc.stderr.strip()[-300:]}")
            with Image.open(png) as im:
                renders.append((im.size, im.convert("RGBA").tobytes()))
    return renders[0] == renders[1]


def _svg_semantics(path: Path) -> Counter:
    """ids, classes, roles, aria-* attributes and title/desc text, as (name, value) counts. Keyed without the
    element, because svgo may move attributes between elements without changing what they mean."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise MetricError(f"{Path(path).name}: not parseable as XML ({exc})") from exc
    found: Counter = Counter()
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        for key, value in el.attrib.items():
            name = key.rsplit("}", 1)[-1]
            if name in ("id", "class", "role") or name.startswith("aria-"):
                found[(name, " ".join(value.split()))] += 1
        if tag in ("title", "desc") and el.text and el.text.strip():
            found[(tag, " ".join(el.text.split()))] += 1
    return found


def svg_semantics_lost(a: Path, b: Path) -> str:
    """Empty when ``b`` keeps every id, class, role, aria-* attribute and title/desc text of ``a``."""
    lost = sorted((_svg_semantics(a) - _svg_semantics(b)).elements())
    if not lost:
        return ""
    shown = ", ".join(f"{name}={value!r}" for name, value in lost[:6])
    return f"removed {shown}" + (f" and {len(lost) - 6} more" if len(lost) > 6 else "")

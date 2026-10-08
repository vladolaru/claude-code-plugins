"""`imgopt compare`: measure any pair and print a check a reviewer can run.

Inputs are local paths, `git:<rev>:<path>` (read with `git show` in the
current repository), or http(s) URLs (for the files GitHub serves at pinned
commits). The reviewer one-liner uses ffmpeg alone; `compare` runs that
exact graph itself and reports its number as `ssim_reviewer`, which is what
the evidence quotes, because ffmpeg's JPEG decoder differs from Pillow's by
up to about 1e-3 (9.8e-4 on real JPEGs, 2026-10-08; enough to flip a 0.98
floor). Alpha images are composited onto white with `overlay=...:format=rgb`:
the default YUV compositing drifted 5e-4 from Pillow's flatten (self-review,
2026-10-08).
The one-liner cannot reproduce numbers when dimensions differ or when either
file carries a device colour profile or an EXIF orientation, because ffmpeg
ignores both; then the evidence must say "verified locally".

The non-alpha graph converts to rgb24 before gray: that is the path the
gate SSIM takes (Pillow-decoded RGB), so the two differ only by decoder
rounding. Reading a JPEG's Y plane directly drifted 7e-4 on a q95-vs-q60
pair. Animations are not reproducible either: Pillow scores the first frame
and ffmpeg's ssim averages all of them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image

from . import metrics
from .ladder import UsageError
from .imaging import alpha_used, display_pixels, read_facts

# Through rgb24 first, as the module docstring explains.
PLAIN_GRAPH = "[0:v]format=rgb24,format=gray[a];[1:v]format=rgb24,format=gray[b];[a][b]ssim"
SSIM_RE = re.compile(r"All:([0-9.]+)")
FETCH_TIMEOUT = 60  # seconds without data before a download fails (OSError, so `compare` exits 2)


def reviewer_graph(size: tuple[int, int], alpha: bool) -> str:
    if not alpha:
        return PLAIN_GRAPH
    w, h = size
    return (f"color=white:s={w}x{h}[w0];color=white:s={w}x{h}[w1];"
            "[w0][0:v]overlay=shortest=1:format=rgb,format=gray[a];"
            "[w1][1:v]overlay=shortest=1:format=rgb,format=gray[b];[a][b]ssim")


def reviewer_command(graph: str) -> str:
    return f'ffmpeg -hide_banner -i REF -i NEW -lavfi "{graph}" -f null - 2>&1 | grep -o \'All:[0-9.]*\''


def run_reviewer_graph(ffmpeg: str, ref: Path, new: Path, graph: str) -> float:
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(ref), "-i", str(new), "-lavfi", graph,
                               "-f", "null", "-"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise metrics.MetricError(f"ffmpeg could not run ({exc.__class__.__name__}: {exc})") from exc
    match = SSIM_RE.search(proc.stderr)
    if not match:
        raise metrics.MetricError(f"reviewer graph printed no SSIM: {proc.stderr.strip()[-300:]}")
    return float(match.group(1))


def fetch(spec: str, workdir: Path, cwd: Path | None = None) -> Path:
    """The pair member as a local file. Each fetch lands in its own folder, because two
    specs often share a file name (the same path at two commits)."""
    workdir.mkdir(parents=True, exist_ok=True)
    if spec.startswith(("http://", "https://")):
        target = Path(tempfile.mkdtemp(dir=workdir)) / (Path(spec.split("?")[0]).name or "download")
        with urllib.request.urlopen(spec, timeout=FETCH_TIMEOUT) as response, open(target, "wb") as fh:
            shutil.copyfileobj(response, fh)
        return target
    if spec.startswith("git:"):
        parts = spec.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            raise ValueError(f"{spec}: expected git:<rev>:<path>")
        _, rev, path = parts
        if rev.startswith("-"):  # `git show` would take it as an option (--output=... writes files)
            raise UsageError(f"{spec}: the revision may not start with '-'")
        data = subprocess.run(["git", "show", "--end-of-options", f"{rev}:{path}"], capture_output=True, check=True, cwd=cwd).stdout
        target = Path(tempfile.mkdtemp(dir=workdir)) / Path(path).name
        target.write_bytes(data)
        return target
    path = Path(spec)
    if not path.is_file():
        raise ValueError(f"{spec}: not a file, git:<rev>:<path>, or URL")
    return path


def compare(ref_spec: str, new_spec: str, tools: dict, workdir: Path, cwd: Path | None = None) -> dict:
    ref, new = fetch(ref_spec, workdir, cwd), fetch(new_spec, workdir, cwd)
    if ref.suffix.lower() == ".svg" or new.suffix.lower() == ".svg":
        raise ValueError("compare measures raster images; SVGs are checked by candidates' render identity")
    rf, nf = read_facts(ref), read_facts(new)
    ri, ni = display_pixels(ref), display_pixels(new)
    animated = rf.frames > 1 or nf.frames > 1
    identical = (metrics.frames_identical(ref, new) if animated
                 else ri.size == ni.size and ri.tobytes() == ni.tobytes())
    result = {"ref": ref_spec, "new": new_spec, "identical": identical}
    reasons = []
    if animated:
        reasons.append("an animation is involved (the scores cover the first frame)")
    if ri.size != ni.size:
        reasons.append("dimensions differ")
        ri = ri.resize(ni.size, Image.LANCZOS)
    if rf.device_profile or nf.device_profile:
        reasons.append("a device colour profile is involved")
    if rf.orientation != 1 or nf.orientation != 1:
        reasons.append("an EXIF orientation is involved")
    if identical:
        result.update(ssim=1.0, ssim_white=1.0, ss2=100.0, band=0.0, butteraugli=0.0)
    else:
        s = metrics.measure(ri, ni, tools, workdir)
        result.update(ssim=s.ssim, ssim_white=s.ssim_white, ss2=s.ss2, band=s.band, butteraugli=s.butteraugli)
    result["reproducible"] = not reasons
    result["reason"] = "; ".join(reasons)
    result["command"] = result["ssim_reviewer"] = None
    if not reasons:
        graph = reviewer_graph(ni.size, alpha_used(ri) or alpha_used(ni))
        try:
            result["ssim_reviewer"] = run_reviewer_graph(tools["ffmpeg"].path, ref, new, graph)
            result["command"] = reviewer_command(graph)
        except metrics.MetricError as error:  # ffmpeg cannot decode an input Pillow reads (AVIF, JXL)
            result["reproducible"] = False
            result["reason"] = f"ffmpeg could not read the pair ({error})"
    return result


def print_result(r: dict, log=print) -> None:
    log(f"ref: {r['ref']}\nnew: {r['new']}")
    log(f"pixel-identical: {'yes' if r['identical'] else 'no'}")
    ba = f"  butteraugli {r['butteraugli']:.2f}" if r.get("butteraugli") is not None else ""
    log(f"gate SSIM {r['ssim']:.4f} (white {r['ssim_white']:.4f})  ssimulacra2 {r['ss2']:.1f}  "
        f"banding {r['band']:.1f} (reported){ba}")
    if r["reproducible"]:
        log(f"Evidence SSIM: {r['ssim_reviewer']:.6f}  (what the reviewer check prints; quote this one)")
        log("Reviewer check (replace REF and NEW with the two files, quoted if the paths contain spaces):")
        log(f"  {r['command']}")
    else:
        log(f"Reviewer check: not reproducible with ffmpeg alone ({r['reason']}); "
            "quote the gate SSIM and state 'verified locally with imgopt compare' in the evidence.")

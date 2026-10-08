"""Tool discovery for imgopt: one fixed source per tool, versions, and the
strict missing-tool policy.

Each tool resolves through ``resolve()`` in a fixed order (``ORDER``, else
``DEFAULT_ORDER``). ``jpegtran`` and ``cjpeg`` must be mozjpeg builds: a
libjpeg-turbo copy on PATH is rejected because its output was 1.5-4% larger
in the 2026-10-07 WooCommerce session; svgo older than 4 is rejected
(``MIN_MAJOR``), because the bundled config relies on svgo 4's preset-default. ``requirements()`` maps a job, a
profile and the formats present to required, quality-affecting and optional
tools; the encoders come from ``ladder.tools_for``, so a job asks for exactly
what its ladder runs (a convert job for the target format's encoders). ``check()`` applies the policy: required tools always block,
quality-affecting ones block unless waived, optional ones never block.
Linux package names in ``APT`` are best effort and unverified.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BUNDLE_DIR = Path("/Applications/ImageOptim.app/Contents/Frameworks/"
                  "ImageOptimGPL.framework/Versions/A/Resources")
KEG_DIRS = (Path("/opt/homebrew/opt/mozjpeg/bin"), Path("/usr/local/opt/mozjpeg/bin"),
            Path("/home/linuxbrew/.linuxbrew/opt/mozjpeg/bin"))
CHROME_APP = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")

JOBS = ("audit", "recompress", "prepare", "convert", "compare")

VERSION_ARGS: dict[str, list[str] | None] = {
    "jpegoptim": ["--version"], "jpegtran": ["-version"], "cjpeg": ["-version"],
    "oxipng": ["--version"], "pngquant": ["--version"], "guetzli": None,
    "gifsicle": ["--version"], "svgo": ["--version"], "rsvg-convert": ["--version"],
    "ffmpeg": ["-version"], "ssimulacra2": None, "butteraugli_main": None,
    "cwebp": ["-version"], "avifenc": ["--version"], "chrome": ["--version"],
}

ADDS = {
    "pillow": "decoding, colour conversion, flattening, banding and tiles; nothing runs without it",
    "jpegoptim": "lossless JPEG optimization and the jpegoptim -m quality ladder",
    "jpegtran": "mozjpeg lossless JPEG optimization (progressive, optimized Huffman)",
    "cjpeg": "mozjpeg encoder for JPEG made from pixels (resize, colour conversion, PNG master)",
    "oxipng": "lossless PNG optimization",
    "pngquant": "palette PNG candidates, usually the largest PNG savings",
    "guetzli": "perceptual JPEG encoder; often the best size for larger photos",
    "gifsicle": "lossless GIF optimization",
    "svgo": "SVG optimization",
    "rsvg-convert": "renders SVG before and after so a changed drawing is rejected",
    "ffmpeg": "SSIM, the gate every lossy candidate must pass",
    "ssimulacra2": "perceptual score that catches what SSIM misses on flat backgrounds",
    "butteraugli_main": "worst-spot perceptual score, reported next to the gates",
    "cwebp": "WebP encoder",
    "avifenc": "AVIF encoder",
    "chrome": "screenshots of the comparison page at 2x",
}

BREW = {"jpegoptim": "jpegoptim", "jpegtran": "mozjpeg", "cjpeg": "mozjpeg", "oxipng": "oxipng",
        "pngquant": "pngquant", "guetzli": "guetzli", "gifsicle": "gifsicle",
        "rsvg-convert": "librsvg", "ffmpeg": "ffmpeg", "ssimulacra2": "jpeg-xl",
        "butteraugli_main": "jpeg-xl", "cwebp": "webp", "avifenc": "libavif"}
APT = {"jpegoptim": "jpegoptim", "pngquant": "pngquant", "guetzli": "guetzli",
       "gifsicle": "gifsicle", "rsvg-convert": "librsvg2-bin", "ffmpeg": "ffmpeg",
       "ssimulacra2": "libjxl-tools", "butteraugli_main": "libjxl-tools", "cwebp": "webp",
       "avifenc": "libavif-bin"}
OTHER = {"pillow": "python3 -m pip install --user pillow", "svgo": "npm install -g svgo",
         "jpegtran": "build mozjpeg: https://github.com/mozilla/mozjpeg",
         "cjpeg": "build mozjpeg: https://github.com/mozilla/mozjpeg",
         "oxipng": "cargo install oxipng", "chrome": "install Google Chrome or Chromium"}

# The ImageOptim bundle comes first only for jpegoptim: it is the one build linked against mozjpeg (Homebrew's
# links libjpeg-turbo). Everything else prefers what the package manager keeps current; the bundle is a
# fallback that updates only with ImageOptim releases.
ORDER = {"jpegoptim": ("bundle", "path"), "jpegtran": ("keg", "bundle", "path"),
         "cjpeg": ("keg", "path"), "chrome": ("app", "path")}
DEFAULT_ORDER = ("path", "bundle")
MOZJPEG_ONLY = frozenset({"jpegtran", "cjpeg"})
# Tools ImageOptim 1.9.3 bundles older than Homebrew ships them (oxipng 9.0.0 against 10.x, whose zopfli mode
# ran 5x faster and saved more on the WooCommerce PNGs; pngquant 3.0.2; gifsicle). doctor suggests the
# Homebrew install when one of these resolves to the bundle; it never blocks. guetzli is not listed: its
# upstream is archived, so no newer build exists.
FRESHER_ON_BREW = frozenset({"oxipng", "pngquant", "gifsicle"})
# svgo 3's preset-default removes viewBox and <title>, which the bundled config (written for 4) does not stop.
MIN_MAJOR = {"svgo": 4}

TARGET_ENCODER = {"jpeg": "cjpeg", "webp": "cwebp", "avif": "avifenc", "png": "oxipng"}
ALL_FORMATS = ("jpeg", "png", "gif", "svg")


@dataclass(frozen=True)
class Env:
    path: str
    bundle: Path | None
    kegs: tuple[Path, ...]
    chrome_app: Path | None

    @classmethod
    def current(cls) -> "Env":
        darwin = sys.platform == "darwin"
        return cls(os.environ.get("PATH", ""), BUNDLE_DIR if darwin else None, KEG_DIRS,
                   CHROME_APP if darwin else None)


@dataclass(frozen=True)
class Tool:
    name: str
    path: str | None
    version: str = ""
    source: str = "missing"
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None

    @property
    def cache_id(self) -> str:
        """What the candidates cache keys on: the version line, or, for a binary that prints none
        (guetzli, ssimulacra2, butteraugli_main), its size and modification time, so an upgrade still
        invalidates cached results."""
        if self.version and self.version != "unknown" and not self.version.startswith("unreadable"):
            return self.version
        st = os.stat(self.path)
        return f"{self.version or 'unknown'}:{st.st_size}:{st.st_mtime_ns}"


@dataclass(frozen=True)
class Requirements:
    required: tuple[str, ...]
    quality: tuple[str, ...]
    optional: tuple[str, ...]

    @property
    def all(self) -> tuple[str, ...]:
        return self.required + self.quality + self.optional


@dataclass(frozen=True)
class Check:
    requirements: Requirements
    tools: dict
    missing_required: tuple[str, ...]
    missing_quality: tuple[str, ...]
    missing_optional: tuple[str, ...]
    waived: tuple[str, ...]
    refused_waivers: tuple[str, ...]

    @property
    def blocked(self) -> bool:
        return bool(self.missing_required or self.missing_quality)


class ToolingError(RuntimeError):
    """A required or unwaived quality-affecting tool is missing; the message is the report."""


def _locations(name: str, env: Env, source: str) -> list[Path]:
    if source == "bundle":
        return [env.bundle / name] if env.bundle else []
    if source == "keg":
        return [k / name for k in env.kegs]
    if source == "app":
        return [env.chrome_app] if env.chrome_app else []
    names = CHROME_NAMES if name == "chrome" else (name,)
    return [Path(f) for f in (shutil.which(n, path=env.path) for n in names) if f]


def probe_version(path: Path, name: str) -> str:
    args = VERSION_ARGS.get(name)
    if args is None:
        return "unknown"
    try:
        proc = subprocess.run([str(path), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"unreadable ({error.__class__.__name__})"
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        if line.strip():
            return line.strip()
    return "unknown"


def resolve(name: str, env: Env | None = None) -> Tool:
    if name == "pillow":
        try:
            import PIL
        except ImportError:
            return Tool("pillow", None, note="not importable by this python3")
        return Tool("pillow", PIL.__file__, PIL.__version__, "python")
    env = env or Env.current()
    rejected = ""
    for source in ORDER.get(name, DEFAULT_ORDER):
        for loc in _locations(name, env, source):
            if not (loc.is_file() and os.access(loc, os.X_OK)):
                continue
            version = probe_version(loc, name)
            why = _unusable(name, version)
            if why:
                rejected = f"{loc}: {why}"
                continue
            return Tool(name, str(loc), version, source)
    return Tool(name, None, note=rejected)


def _unusable(name: str, version: str) -> str:
    """Why a found binary of ``name`` reporting ``version`` must not be used, or empty."""
    if name in MOZJPEG_ONLY and "mozjpeg" not in version.lower():
        return f"not mozjpeg ({version})"
    if name in MIN_MAJOR:
        major = re.match(r"\D*(\d+)\.", version)
        if not major or int(major.group(1)) < MIN_MAJOR[name]:
            return f"{name} {MIN_MAJOR[name]} or newer required ({version})"
    return ""


def _dedupe(seq) -> tuple[str, ...]:
    out: list[str] = []
    for item in seq:
        if item not in out:
            out.append(item)
    return tuple(out)


def requirements(job: str, profile: str = "lossless", formats=None, target: str = "keep") -> Requirements:
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r}; expected one of {', '.join(JOBS)}")
    if job == "compare":
        return Requirements(("pillow", "ffmpeg", "ssimulacra2"), (), ("butteraugli_main",))
    fmts = sorted(set(formats or ALL_FORMATS))
    required = ["pillow"]
    quality: list[str] = []
    optional: list[str] = []
    if job == "audit":
        quality += [tool for fmt, tool in (("jpeg", "jpegtran"), ("png", "oxipng")) if fmt in fmts]
        return Requirements(tuple(required), _dedupe(quality), ())
    # Imported here: ladder needs Pillow, and this module must import without it (conftest, doctor's report).
    from .ladder import tools_for

    out_format = target if job == "convert" else "keep"
    resize = 1 if job == "prepare" else None
    for fmt in fmts:
        ladder_tools = tools_for(fmt, profile=profile, out_format=out_format, resize=resize)
        if fmt == "svg" and ladder_tools:
            required.append("rsvg-convert")  # the render check that decides every SVG candidate
        quality += sorted(ladder_tools)
        if job in ("prepare", "convert") and fmt in ("jpeg", "png"):
            encoder = TARGET_ENCODER.get(fmt if out_format == "keep" else out_format)
            required += [encoder] if encoder in ladder_tools else []
    if profile != "lossless":
        required += ["ffmpeg", "ssimulacra2"]
        optional += ["butteraugli_main", "chrome"]
    req = _dedupe(required)
    return Requirements(req, tuple(q for q in _dedupe(quality) if q not in req), _dedupe(optional))


def check(req: Requirements, env: Env | None = None, allow_missing=()) -> Check:
    env = env or Env.current()
    allow = set(allow_missing)
    tools = {n: resolve(n, env) for n in req.all}
    return Check(
        requirements=req,
        tools=tools,
        missing_required=tuple(n for n in req.required if not tools[n].ok),
        missing_quality=tuple(n for n in req.quality if not tools[n].ok and n not in allow),
        missing_optional=tuple(n for n in req.optional if not tools[n].ok),
        waived=tuple(n for n in req.quality if not tools[n].ok and n in allow),
        refused_waivers=tuple(n for n in req.required if n in allow),
    )


def install_lines(names, platform: str | None = None) -> list[str]:
    platform = platform or sys.platform
    table = BREW if platform == "darwin" else APT
    packages: list[str] = []
    extra: list[str] = []
    for name in names:
        if platform == "darwin" and name == "chrome":
            command = "brew install --cask google-chrome"
        elif name in table:
            if table[name] not in packages:
                packages.append(table[name])
            continue
        else:
            command = OTHER.get(name, f"install {name}")
        if command not in extra:
            extra.append(command)
    lines = []
    if packages:
        lines.append(("brew install " if platform == "darwin" else "sudo apt install ") + " ".join(packages))
    return lines + extra


def report(chk: Check, *, job: str, profile: str, platform: str | None = None) -> str:
    req = chk.requirements
    lines = [f"imgopt doctor: job={job} profile={profile}"]
    for group, names in (("required", req.required), ("quality", req.quality), ("optional", req.optional)):
        for name in names:
            tool = chk.tools[name]
            if tool.ok:
                status = "ok"
            elif name in chk.waived:
                status = "WAIVED"
            elif group == "optional":
                status = "absent"
            else:
                status = "MISSING"
            lines.append(f"  {status:8} {group:9} {name:17} {(tool.version or '-')[:40]:40} "
                         f"{tool.source:7} {tool.path or tool.note}")
    stale = [n for n in req.all if chk.tools[n].ok and chk.tools[n].source == "bundle" and n in FRESHER_ON_BREW]
    if stale:
        lines.append("Older copies from the ImageOptim bundle (it updates only with ImageOptim): "
                     + ", ".join(stale) + ". Suggest to the human; this does not block:")
        lines += ["  " + line for line in install_lines(stale, platform)]
    blockers = chk.missing_required + chk.missing_quality
    for name in blockers:
        lines.append(f"  - {name}: {ADDS[name]}")
    if chk.refused_waivers:
        lines.append("Cannot be waived (required for this job): " + ", ".join(chk.refused_waivers))
    if chk.waived:
        lines.append("Waived by --allow-missing (stamped on every output): " + ", ".join(chk.waived))
    if chk.missing_optional:
        lines.append("Optional, ask the human once: " + "; ".join(
            f"{n} ({ADDS[n]})" for n in chk.missing_optional))
        lines += ["  " + line for line in install_lines(chk.missing_optional, platform)]
    if chk.blocked:
        lines.append("BLOCKED. Ask the human to install the missing tools before any work:")
        lines += ["  " + line for line in install_lines(blockers, platform)]
    else:
        lines.append("Ready.")
    return "\n".join(lines)


def ensure(job: str, profile: str, formats, target: str = "keep", allow_missing=(),
           env: Env | None = None) -> Check:
    chk = check(requirements(job, profile, formats, target), env, allow_missing)
    if chk.blocked:
        raise ToolingError(report(chk, job=job, profile=profile))
    return chk


def describe(tools: dict) -> str:
    """One line naming each available tool's version and path, for every command's output."""
    return "tools: " + "; ".join(f"{t.name} {t.version} ({t.path})"
                                 for _, t in sorted(tools.items()) if t.ok)

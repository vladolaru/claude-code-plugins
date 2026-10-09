"""Derived variants of real photos: what a camera, a phone, a CMS and a designer would have made of them.

Every output is named from an ASCII slug of the source key (Wikimedia titles carry spaces, commas and non-ASCII, and
the review page cannot approve a path with a comma); the entry's origin keeps the original key. Encoders are
deterministic, so building twice gives identical bytes."""

from __future__ import annotations

import io
import subprocess
import re
import unicodedata
from pathlib import Path
from typing import Callable

from PIL import Image, ImageCms

from imgopt_lib.imaging import display_pixels, resize_width

from . import manifest, paths, sources

CAMERA_WIDTH, CATALOG_WIDTH, MASTER_WIDTH = 4032, 1200, 2000
CATALOG_QUALITIES = (95, 85, 75)
MOZJPEG_QUALITY = 75
MASTER_COUNT = 15  # PNG masters (and recompressed sets for product-plain) come from the first N sources by key
P3_PROFILE = Path("/System/Library/ColorSync/Profiles/Display P3.icc")
NO_P3 = "no P3 profile on this machine"


def slug(key: str) -> str:
    """The key's stem as lowercase [a-z0-9-]: transliterated where possible, other runs become one dash."""
    ascii_stem = unicodedata.normalize("NFKD", Path(key).stem).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_stem.lower()).strip("-") or "image"


def slugs(keys: list[str]) -> list[str]:
    """One slug per key, in order; a slug already taken gets -2, -3 and so on."""
    taken: dict[str, int] = {}
    out = []
    for key in keys:
        base = slug(key)
        taken[base] = taken.get(base, 0) + 1
        out.append(base if taken[base] == 1 else f"{base}-{taken[base]}")
    return out


def _rgb(src: Path, width: int | None) -> Image.Image:
    im = display_pixels(src).convert("RGB")
    return resize_width(im, width) if width and im.width > width else im


def camera_jpeg(src: Path, dest: Path) -> Path:
    _rgb(src, CAMERA_WIDTH).save(dest, "JPEG", quality=92, subsampling=2, optimize=False)
    return dest


def phone_upload(src: Path, dest: Path, p3_icc: bytes | None) -> Path:
    im = _rgb(src, CAMERA_WIDTH)
    kwargs = {}
    if p3_icc:
        srgb = ImageCms.createProfile("sRGB")
        p3 = ImageCms.ImageCmsProfile(io.BytesIO(p3_icc))
        im = ImageCms.profileToProfile(im, srgb, p3, outputMode="RGB")
        kwargs["icc_profile"] = p3_icc
    exif = Image.Exif()
    exif[0x0112] = 6  # display = stored rotated 90° clockwise, as phones write portrait shots
    im.transpose(Image.Transpose.ROTATE_90).save(dest, "JPEG", quality=90, subsampling=2, exif=exif.tobytes(),
                                                  **kwargs)
    return dest


def recompressed(src: Path, dest: Path, *, encoder: str, quality: int, cjpeg: str | None = None) -> Path:
    """The catalog-width JPEG a CMS would keep: `libjpeg` is Pillow, `mozjpeg` is the `cjpeg` executable given."""
    im = _rgb(src, CATALOG_WIDTH)
    if encoder == "libjpeg":
        im.save(dest, "JPEG", quality=quality, subsampling=2)
    elif encoder == "mozjpeg":
        ppm = dest.with_suffix(".ppm")
        try:
            im.save(ppm)
            subprocess.run([cjpeg, "-quality", str(quality), "-outfile", str(dest), str(ppm)], check=True,
                           capture_output=True)
        finally:
            ppm.unlink(missing_ok=True)
    else:
        raise ValueError(encoder)
    return dest


def png_master(src: Path, dest: Path) -> Path:
    _rgb(src, MASTER_WIDTH).save(dest, "PNG")
    return dest


def kodak_jpeg(src: Path, dest: Path) -> Path:
    _rgb(src, None).save(dest, "JPEG", quality=92, subsampling=2)
    return dest


def _cjpeg(tools: dict) -> str | None:
    """The mozjpeg `cjpeg` imgopt resolved, or None when it is not available."""
    tool = tools.get("cjpeg")
    return tool.path if tool is not None and tool.ok else None


def build_all(downloads: Path, corpus: Path, tools: dict, *, sources_file: Path = paths.SOURCES_FILE,
              p3_profile: Path = P3_PROFILE, log: Callable[[str], None] = lambda message: None) -> list[manifest.Entry]:
    """Every derived photo variant of the originals in `downloads`, written under `corpus`, one Entry per file.
    Sources are taken in key order within a category, so "the first 15" is stable; licenses come from sources.json.
    Without a mozjpeg `cjpeg` in `tools` the mozjpeg variant is skipped and `log` says so."""
    listed = sources.load(sources_file)
    p3_icc = p3_profile.read_bytes() if p3_profile.is_file() else None
    cjpeg = _cjpeg(tools)
    if cjpeg is None:
        log("no mozjpeg cjpeg available: skipping the mozjpeg recompressions")
    entries: list[manifest.Entry] = []
    seen: set[Path] = set()

    def emit(sub: str, name: str, source: sources.Source, transform: str, make: Callable[[Path], Path]) -> None:
        dest = corpus / sub / name
        if dest in seen:
            raise ValueError(f"two sources derive {sub}/{name}")
        seen.add(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        make(dest)
        entries.append(manifest.entry_for(dest, corpus, category=sub, origin=f"derived:{source.category}/{source.key}",
                                          transform=transform, license=source.license))

    def recompressions(source: sources.Source, original: Path, stem: str) -> None:
        for quality in CATALOG_QUALITIES:
            emit("jpeg-recompressed", f"{stem}-libjpeg-q{quality}.jpg", source,
                 f"recompressed(libjpeg, q{quality}, {CATALOG_WIDTH} wide)",
                 lambda dest, q=quality: recompressed(original, dest, encoder="libjpeg", quality=q))
        if cjpeg:
            emit("jpeg-recompressed", f"{stem}-mozjpeg-q{MOZJPEG_QUALITY}.jpg", source,
                 f"recompressed(mozjpeg, q{MOZJPEG_QUALITY}, {CATALOG_WIDTH} wide)",
                 lambda dest: recompressed(original, dest, encoder="mozjpeg", quality=MOZJPEG_QUALITY, cjpeg=cjpeg))

    def master(source: sources.Source, original: Path, stem: str) -> None:
        emit("png-master", f"{stem}.png", source, f"png_master({MASTER_WIDTH} wide)",
             lambda dest: png_master(original, dest))

    for category in ("photo-camera", "product-plain", "photo-small"):
        chosen = sorted((s for s in listed if s.category == category), key=lambda s: s.key)
        for index, (source, stem) in enumerate(zip(chosen, slugs([s.key for s in chosen]))):
            original = downloads / category / source.key
            if category == "photo-camera":
                emit("photo-camera", f"{stem}.jpg", source, f"camera_jpeg({CAMERA_WIDTH} wide, q92, 4:2:0)",
                     lambda dest: camera_jpeg(original, dest))
                emit("phone-upload", f"{stem}.jpg", source,
                     f"phone_upload({CAMERA_WIDTH} wide, rotated, EXIF orientation 6, q90, 4:2:0, "
                     f"{'Display P3 profile embedded' if p3_icc else NO_P3})",
                     lambda dest: phone_upload(original, dest, p3_icc))
            elif category == "product-plain":
                emit("product-plain", f"{stem}.jpg", source, f"camera_jpeg({CAMERA_WIDTH} wide cap, q92, 4:2:0)",
                     lambda dest: camera_jpeg(original, dest))
            else:
                emit("photo-small", f"{stem}.jpg", source, "kodak_jpeg(q92, 4:2:0, original size)",
                     lambda dest: kodak_jpeg(original, dest))
            if category != "photo-small" and (category == "photo-camera" or index < MASTER_COUNT):
                recompressions(source, original, stem)
            if category != "photo-small" and index < MASTER_COUNT:
                master(source, original, stem)
    return entries

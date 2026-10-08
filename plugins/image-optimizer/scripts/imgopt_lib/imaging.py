"""Pixel handling for imgopt: what a file looks like on screen.

``display_pixels()`` is the one definition of "what the viewer sees": EXIF
orientation applied, a device colour profile converted to sRGB, RGBA. Every
comparison (metrics, tiles, apply re-checks) goes through it, so both sides
of a comparison are prepared the same way. ``read_facts()`` reads the raw
file without transforming it.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageCms, ImageOps, ImageStat

BACKGROUNDS = {"white": (255, 255, 255), "dark": (40, 40, 40)}
ORIENTATION_TAG = 0x0112
IJG_LUMA = (16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
            14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
            18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
            49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99)


@dataclass(frozen=True)
class Facts:
    path: Path
    format: str
    width: int
    height: int
    mode: str
    has_alpha: bool
    colors: int | None
    icc: bytes | None
    icc_desc: str | None
    device_profile: bool
    orientation: int
    progressive: bool
    frames: int


def is_srgb(desc: str | None) -> bool:
    return bool(desc) and "srgb" in desc.lower().replace(" ", "").replace("-", "")


def profile_description(icc: bytes | None) -> str | None:
    if not icc:
        return None
    try:
        return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()
    except Exception:  # unreadable profile: treat as a device profile
        return "unreadable profile"


def read_facts(path: Path) -> Facts:
    with Image.open(path) as im:
        icc = im.info.get("icc_profile") or None
        desc = profile_description(icc)
        colors = im.convert("RGBA").getcolors(maxcolors=4096)
        return Facts(
            path=Path(path),
            format=(im.format or "").lower(),
            width=im.width,
            height=im.height,
            mode=im.mode,
            has_alpha=im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info,
            colors=len(colors) if colors else None,
            icc=icc,
            icc_desc=desc,
            device_profile=bool(icc) and not is_srgb(desc),
            orientation=int(im.getexif().get(ORIENTATION_TAG, 1) or 1),
            progressive=bool(im.info.get("progressive") or im.info.get("progression")),
            frames=getattr(im, "n_frames", 1),
        )


def to_srgb(rgba: Image.Image, icc: bytes) -> Image.Image:
    src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    dst = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    rgb = ImageCms.profileToProfile(rgba.convert("RGB"), src, dst, outputMode="RGB")
    out = rgb.convert("RGBA")
    out.putalpha(rgba.getchannel("A"))
    return out


def _oriented_rgba(path: Path) -> tuple[Image.Image, bytes | None]:
    with Image.open(path) as im:
        im.load()
        icc = im.info.get("icc_profile") or None
        return ImageOps.exif_transpose(im).convert("RGBA"), icc


def resize_width(img: Image.Image, width: int) -> Image.Image:
    height = max(1, round(img.height * width / img.width))
    return img.resize((width, height), Image.LANCZOS)


def display_pixels(path: Path, *, width: int | None = None) -> Image.Image:
    rgba, icc = _oriented_rgba(path)
    if icc and not is_srgb(profile_description(icc)):
        rgba = to_srgb(rgba, icc)
    if width and width != rgba.width:
        rgba = resize_width(rgba, width)
    return rgba


def srgb_shift(path: Path) -> tuple[float, int]:
    """How far converting the device profile to sRGB moves pixels (mean, max of 255)."""
    raw, icc = _oriented_rgba(path)
    if not icc:
        return 0.0, 0
    diff = ImageChops.difference(raw.convert("RGB"), to_srgb(raw, icc).convert("RGB"))
    mean = sum(ImageStat.Stat(diff).mean) / 3
    peak = max(high for _, high in diff.getextrema())
    return mean, peak


def flatten(rgba: Image.Image, bg: str) -> Image.Image:
    base = Image.new("RGBA", rgba.size, BACKGROUNDS[bg] + (255,))
    base.alpha_composite(rgba.convert("RGBA"))
    return base.convert("RGB")


def alpha_used(rgba: Image.Image) -> bool:
    return rgba.getchannel("A").getextrema()[0] < 255


def backgrounds(has_alpha: bool) -> list[str]:
    return ["white", "dark"] if has_alpha else ["white"]


def _scaled_luma(quality: int) -> list[int]:
    scale = 5000 // quality if quality < 50 else 200 - 2 * quality
    return [min(255, max(1, (v * scale + 50) // 100)) for v in IJG_LUMA]


def estimate_jpeg_quality(path: Path) -> int | None:
    """IJG-scale quality whose scaled luma table best matches the file's.

    Sums are order-independent, so zigzag versus natural table order does
    not matter. jpegoptim's -m scale is not this scale (session finding).
    """
    with Image.open(path) as im:
        tables = getattr(im, "quantization", None)
    if not tables or 0 not in tables:
        return None
    target = sum(tables[0])
    return min(range(1, 101), key=lambda q: abs(sum(_scaled_luma(q)) - target))

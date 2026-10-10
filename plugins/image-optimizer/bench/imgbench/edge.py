"""Edge-case files: the inputs the 2026-10-09 audit found mishandled, plus format corners.

Each file is built from a fixed recipe (no random state), so building twice gives identical bytes. The chunk-level
PNG code mirrors the `gamma_png` and `deep_png` factories in tests/conftest.py, which cannot be imported from here."""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageOps

from imgopt_lib.imaging import display_pixels

from . import manifest, paths, sources

CATEGORY = "edge"
LICENSE = "MIT (generated)"
HUGE_SIZE = (6000, 4000)
# The transform that turns a stored image into the upright one, per EXIF orientation tag (Pillow's exif_transpose).
UPRIGHT = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180, 4: Image.Transpose.FLIP_TOP_BOTTOM,
           5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
           8: Image.Transpose.ROTATE_90}
INVERSE = {Image.Transpose.ROTATE_270: Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_90: Image.Transpose.ROTATE_270}


def photo(size: tuple[int, int]) -> Image.Image:
    """A photo-like RGB image from Pillow primitives only (gradients and a Mandelbrot texture): deterministic, fast,
    and asymmetric, so a wrong orientation shows."""
    w, h = size
    lum = Image.linear_gradient("L").resize(size)
    texture = Image.effect_mandelbrot((w, h), (-2.2, -1.5, 1.0, 1.5), 40)
    r = Image.blend(lum, texture, 0.35)
    g = Image.blend(ImageOps.mirror(lum.rotate(90, expand=True).resize(size)), texture, 0.25)
    b = Image.radial_gradient("L").resize(size)
    im = Image.merge("RGB", (r, g, b))
    ImageDraw.Draw(im).rectangle((w // 20, h // 15, w // 5, h // 4), fill=(250, 250, 250))  # a corner landmark
    return im


def _chunk(tag: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))


def _gamma_png(dest: Path, size: tuple[int, int], gamma: float) -> None:
    """A PNG with gAMA and cHRM chunks right after IHDR: browsers apply them, Pillow's decoder ignores them."""
    photo(size).save(dest, "PNG")
    data = dest.read_bytes()
    ihdr_end = 8 + 12 + 13
    extra = (_chunk(b"gAMA", struct.pack(">I", round(gamma * 100000)))
             + _chunk(b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)))
    dest.write_bytes(data[:ihdr_end] + extra + data[ihdr_end:])


def _deep_png(dest: Path, size: tuple[int, int]) -> None:
    """A 16-bit-per-sample RGB PNG written by hand: Pillow cannot save 16-bit colour."""
    w, h = size
    rows = b"".join(b"\0" + b"".join(struct.pack(">H", (x * 4099 + y * 997 + c * 13001) % 65536)
                                      for x in range(w) for c in range(3)) for y in range(h))
    dest.write_bytes(b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))
                     + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b""))


def _animation(dest: Path, fmt: str, size: tuple[int, int]) -> None:
    base = photo(size)
    frames = [base, base.rotate(90), ImageOps.invert(base)]
    frames[0].save(dest, fmt, save_all=True, append_images=frames[1:], duration=120, loop=0)


def _orientation(dest: Path, tag: int) -> None:
    """Pixels stored so that applying the EXIF orientation shows the 300x200 photo upright."""
    upright = photo((300, 200))
    stored = upright.transpose(INVERSE.get(UPRIGHT[tag], UPRIGHT[tag]))
    exif = Image.Exif()
    exif[0x0112] = tag
    stored.save(dest, "JPEG", quality=90, subsampling=2, exif=exif.tobytes())


def _lfs_pointer(dest: Path) -> None:
    oid = hashlib.sha256(b"imgbench lfs pointer").hexdigest()
    dest.write_text(f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 1048576\n")


ACCESSIBLE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" role="img" aria-labelledby="t d">'
    '<title id="t">Shopping cart</title><desc id="d">A cart with two wheels</desc>'
    '<g id="cart-body" class="icon-body"><path d="M3 4h2l2 10h10l2-7H7" fill="none" stroke="#222"/></g>'
    '<circle id="wheel-left" cx="9" cy="19" r="1.5"/><circle cx="17" cy="19" r="1.5"/></svg>')


def _tiny(dest: Path) -> None:
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.ellipse((1, 1, 14, 14), fill=(34, 113, 177, 255))
    draw.line((4, 8, 7, 11, 12, 5), fill=(255, 255, 255, 255), width=2)
    im.save(dest, "PNG")


def _huge(dest: Path) -> str:
    """A 6000x4000 JPEG from the first downloaded photo-camera original (cropped to 3:2), else a synthetic
    gradient photo. Returns the origin to record."""
    listed = sorted((s for s in sources.load(paths.SOURCES_FILE) if s.category == "photo-camera"), key=lambda s: s.key)
    for source in listed:
        original = paths.downloads_dir() / source.category / source.key
        if original.is_file():
            im = display_pixels(original).convert("RGB")
            ImageOps.fit(im, HUGE_SIZE, Image.Resampling.LANCZOS).save(dest, "JPEG", quality=92, subsampling=2)
            return f"derived:{source.category}/{source.key}"
    small = photo((1500, 1000))
    small.resize(HUGE_SIZE, Image.Resampling.BICUBIC).save(dest, "JPEG", quality=92, subsampling=2)
    return "synthetic"


def build_all(corpus: Path, tools: dict) -> list[manifest.Entry]:
    """Every edge-case file under `corpus/edge`, one Entry each. `tools` is accepted for symmetry with the other
    builders; no edge case needs an external tool."""
    folder = corpus / CATEGORY
    folder.mkdir(parents=True, exist_ok=True)
    entries: list[manifest.Entry] = []

    def add(name: str, transform: str, make: Callable[[Path], object]) -> None:
        """`make` writes the file; when it returns a string, that is the origin (a derived file), else synthetic."""
        dest = folder / name
        made = make(dest)
        entries.append(manifest.entry_for(dest, corpus, category=CATEGORY, transform=transform, license=LICENSE,
                                          origin=made if isinstance(made, str) else "synthetic"))

    add("anim.png", "3-frame 256x256 APNG: a still-PNG pipeline would flatten it (audit G1)",
        lambda d: _animation(d, "PNG", (256, 256)))
    add("anim.gif", "3-frame 256x256 GIF: animation must survive optimization",
        lambda d: _animation(d, "GIF", (256, 256)))
    add("gamma10.png", "400x300 PNG with gAMA 1.0 and cHRM after IHDR: browsers render it unlike its pixels (G2)",
        lambda d: _gamma_png(d, (400, 300), 1.0))
    add("cmyk.jpg", "600x400 CMYK JPEG: needs a colour conversion before any RGB tool",
        lambda d: photo((600, 400)).convert("CMYK").save(d, "JPEG", quality=90))
    add("gray.jpg", "600x400 grayscale JPEG: one channel, no chroma to subsample",
        lambda d: photo((600, 400)).convert("L").save(d, "JPEG", quality=90))
    add("deep16.png", "256x192 16-bit RGB PNG written by hand: 8-bit tools would quantize it",
        lambda d: _deep_png(d, (256, 192)))
    for tag in sorted(UPRIGHT):
        add(f"orient-{tag}.jpg", f"300x200 JPEG with EXIF orientation {tag}, pixels stored so it displays upright",
            lambda d, t=tag: _orientation(d, t))
    add("jpeg-named.png", "JPEG content under a .png name: the extension lies about the format",
        lambda d: photo((320, 240)).save(d, "JPEG", quality=88, subsampling=2))
    add("lfs-pointer.png", "a Git LFS pointer text under a .png name: not an image at all", _lfs_pointer)
    add("accessible.svg", "SVG with role, aria-labelledby, title/desc ids and ids on groups: svgo must keep them (G3)",
        lambda d: d.write_text(ACCESSIBLE_SVG))
    add("huge-24mp.jpg", "6000x4000 JPEG, the largest input (R5); from the first photo-camera original if downloaded",
        _huge)
    add("tiny-16.png", "16x16 RGBA icon: smaller than any size gate", _tiny)
    return entries

"""Fixtures for the image-optimizer suite.

Puts ``scripts/`` on sys.path so tests import ``imgopt_lib`` directly, and
builds small deterministic images (no random noise, so metrics are stable).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageOps

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from imgopt_lib import tools as T  # noqa: E402

DEVICE_PROFILES = (
    Path("/System/Library/ColorSync/Profiles/Display P3.icc"),
    Path("/usr/share/color/icc/colord/DisplayP3.icc"),
)


def _photo(size: tuple[int, int]) -> Image.Image:
    w, h = size
    base = Image.linear_gradient("L").resize(size)
    data = bytes(((x * 37 + y * 91 + (x * y) % 23 * 11) % 256) for y in range(h) for x in range(w))
    tex = Image.frombytes("L", size, data)
    r = Image.blend(base, tex, 0.35)
    g = Image.blend(ImageOps.mirror(base), tex, 0.25)
    b = ImageOps.flip(base)
    return Image.merge("RGB", (r, g, b))


def _gradient(size: tuple[int, int], lo: int = 40, hi: int = 100) -> Image.Image:
    w, h = size
    row = bytes(int(lo + (hi - lo) * x / (w - 1)) for x in range(w))
    lum = Image.frombytes("L", (w, 1), row).resize(size, Image.NEAREST)
    return Image.merge("RGB", (lum.point(lambda v: v // 3), lum.point(lambda v: v // 2 + 20), lum))


class Factory:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def photo(self, name="photo.jpg", size=(160, 120), quality=92, orientation=1,
              icc: bytes | None = None, progressive=False, gray=False) -> Path:
        im = _photo(size)
        if gray:
            im = im.convert("L")
        path = self.root / name
        kwargs: dict = {}
        if orientation != 1:
            exif = Image.Exif()
            exif[0x0112] = orientation
            kwargs["exif"] = exif.tobytes()
        if icc:
            kwargs["icc_profile"] = icc
        if path.suffix.lower() in (".jpg", ".jpeg"):
            im.save(path, "JPEG", quality=quality, progressive=progressive, **kwargs)
        else:
            im.save(path, "PNG", **kwargs)
        return path

    def gradient(self, name="gradient.png", size=(480, 60), colors: int | None = None,
                 lo=40, hi=100) -> Path:
        im = _gradient(size, lo, hi)
        if colors:
            im = im.quantize(colors).convert("RGB")
        path = self.root / name
        im.save(path, "PNG")
        return path

    def logo(self, name="logo.png", size=(120, 120)) -> Path:
        big = Image.new("RGBA", (size[0] * 4, size[1] * 4), (0, 0, 0, 0))
        ImageDraw.Draw(big).ellipse((16, 16, big.width - 16, big.height - 16), fill=(200, 40, 90, 255))
        path = self.root / name
        big.resize(size, Image.LANCZOS).save(path, "PNG")
        return path


@pytest.fixture
def factory(tmp_path) -> Factory:
    return Factory(tmp_path / "images")


@pytest.fixture
def device_icc() -> bytes:
    for p in DEVICE_PROFILES:
        if p.is_file():
            return p.read_bytes()
    pytest.skip("needs a Display P3 ICC profile on this machine")


@pytest.fixture
def toolset():
    def _toolset(job, profile="lossless", formats=None, target="keep"):
        chk = T.check(T.requirements(job, profile, formats, target))
        missing = chk.missing_required + chk.missing_quality
        if missing:
            pytest.skip("needs " + ", ".join(missing))
        return chk.tools
    return _toolset

"""Fixtures for the image-optimizer suite.

Puts ``scripts/`` on sys.path so tests import ``imgopt_lib`` directly, and
builds small deterministic images (no random noise, so metrics are stable).

Pillow is the one third-party dependency. A pytest whose interpreter cannot
import it (for example a uv-tool pytest) would otherwise turn the repo-wide
``pytest plugins/`` run into a collection error for every plugin, so this
suite steps aside instead: its test modules are not collected and the
terminal summary says why. Everything below that touches ``Image`` runs only
inside fixtures, so the guarded import is the only change needed.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import pytest

try:
    from PIL import Image, ImageDraw, ImageOps
except ImportError:
    Image = ImageDraw = ImageOps = None

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from imgopt_lib import tools as T  # noqa: E402

DEVICE_PROFILES = (
    Path("/System/Library/ColorSync/Profiles/Display P3.icc"),
    Path("/usr/share/color/icc/colord/DisplayP3.icc"),
)

if Image is None:
    collect_ignore_glob = ["test_*.py"]

    def pytest_terminal_summary(terminalreporter):
        terminalreporter.write_line(
            "image-optimizer: tests skipped, this pytest's python cannot import Pillow; "
            "run `python3 -m pytest plugins/image-optimizer/tests/` with a python3 that has it.",
            yellow=True)


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

    def truncated_jpeg(self, name="cut.jpg") -> Path:
        """A JPEG missing its last 3000 bytes: Pillow opens it and fails while decoding."""
        path = self.photo(name=name, size=(400, 300))
        path.write_bytes(path.read_bytes()[:-3000])
        return path

    def deep_png(self, name="deep.png", channels="RGB", size=(16, 8)) -> Path:
        """A 16-bit-per-sample PNG ("L", "RGB" or "RGBA"), written by hand: Pillow cannot save 16-bit colour."""
        colour_type = {"L": 0, "RGB": 2, "RGBA": 6}[channels]
        w, h = size
        rows = b"".join(b"\0" + b"".join(struct.pack(">H", (x * 4099 + y * 997 + c * 13001) % 65536)
                                          for x in range(w) for c in range(len(channels)))
                        for y in range(h))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

        path = self.root / name
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, colour_type, 0, 0, 0))
                         + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
        return path

    def apng(self, name="anim.png", frames=3, size=(64, 64)) -> Path:
        """An animated PNG: one solid colour per frame."""
        colours = [(255, 0, 0), (0, 160, 0), (0, 0, 255), (240, 200, 0)]
        ims = [Image.new("RGB", size, colours[i % len(colours)]) for i in range(frames)]
        path = self.root / name
        ims[0].save(path, "PNG", save_all=True, append_images=ims[1:], duration=100, loop=0)
        return path

    def gamma_png(self, name="gamma.png", gamma=1.0, size=(200, 120)) -> Path:
        """A PNG whose gAMA (and cHRM) chunks change how browsers render it; Pillow ignores them on decode."""
        im = _photo(size)
        path = self.root / name
        im.save(path, "PNG")
        data = path.read_bytes()

        def chunk(tag: bytes, body: bytes) -> bytes:
            return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

        ihdr_end = 8 + 12 + 13
        extra = (chunk(b"gAMA", struct.pack(">I", round(gamma * 100000)))
                 + chunk(b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)))
        path.write_bytes(data[:ihdr_end] + extra + data[ihdr_end:])
        return path

    def accessible_svg(self, name="icon.svg") -> Path:
        """An inline-style SVG whose accessible name lives in role, aria-labelledby, title/desc and their ids."""
        path = self.root / name
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" role="img" aria-labelledby="t d">'
            '<title id="t">Shopping cart</title><desc id="d">A cart with two wheels</desc>'
            '<g id="cart-body" class="icon-body"><path d="M3 4h2l2 10h10l2-7H7" fill="none" stroke="#222"/></g>'
            '<circle id="wheel-left" cx="9" cy="19" r="1.5"/><circle cx="17" cy="19" r="1.5"/></svg>')
        return path

    def styled_svg(self, name="styled.svg") -> Path:
        """An SVG whose fills come from a <style> block and two classes svgo would inline away."""
        path = self.root / name
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<style>.a{fill:#c00}.b{fill:#06c}</style>'
            '<rect class="a" x="2" y="2" width="9" height="20"/><rect class="b" x="13" y="2" width="9" height="20"/></svg>')
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
    def _toolset(job, profile="lossless", formats=None, target="keep", waive=()):
        chk = T.check(T.requirements(job, profile, formats, target), allow_missing=waive)
        missing = chk.missing_required + chk.missing_quality
        if missing:
            pytest.skip("needs " + ", ".join(missing))
        return chk.tools
    return _toolset

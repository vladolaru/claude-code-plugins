import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

from imgbench import edge as E, synth as SY  # noqa: E402
from imgopt_lib import imaging as I  # noqa: E402


def test_svg_generators_are_seeded():
    assert SY.illustration_svg(3, 800, 500, dark=True) == SY.illustration_svg(3, 800, 500, dark=True)
    assert SY.illustration_svg(3, 800, 500, dark=True) != SY.illustration_svg(4, 800, 500, dark=True)
    assert "<linearGradient" in SY.illustration_svg(1, 800, 500, dark=False)


def test_an_icon_renders_with_soft_alpha(tmp_path):
    rsvg = shutil.which("rsvg-convert")
    if not rsvg:
        pytest.skip("needs rsvg-convert")
    png = SY.render_svg(SY.icon_svg(7), tmp_path / "i.png", 256, rsvg)
    with Image.open(png) as im:
        alpha = im.convert("RGBA").getchannel("A")
        partial = sum(alpha.histogram()[1:255])  # pixels that are neither fully clear nor fully opaque
        assert im.width == 256 and partial > 0


def test_edge_cases_cover_the_audit(tmp_path):
    entries = E.build_all(tmp_path, tools={})
    names = {Path(e.path).name for e in entries}
    for expected in ("anim.png", "gamma10.png", "cmyk.jpg", "deep16.png", "orient-8.jpg", "jpeg-named.png",
                     "lfs-pointer.png", "accessible.svg", "anim.gif", "gray.jpg", "tiny-16.png"):
        assert expected in names
    assert I.read_facts(tmp_path / "edge" / "anim.png").frames > 1


def test_edge_cases_are_deterministic_and_complete(tmp_path):
    first = E.build_all(tmp_path / "a", tools={})
    second = E.build_all(tmp_path / "b", tools={})
    assert [(e.path, e.sha256) for e in first] == [(e.path, e.sha256) for e in second]
    assert len(first) == 18 and {e.category for e in first} == {"edge"} and {e.origin for e in first} == {"synthetic"}
    assert all(e.transform and e.license for e in first)
    for n in range(2, 9):  # every orientation tag is stored, and the pixels display upright
        facts = I.read_facts(tmp_path / "a" / "edge" / f"orient-{n}.jpg")
        assert facts.orientation == n and facts.display_width == 300
    assert I.read_facts(tmp_path / "a" / "edge" / "huge-24mp.jpg").display_width == 6000


def test_a_screenshot_at_scale_two_doubles_the_pixels(tmp_path):
    chrome = SY.find_chrome()
    if not chrome:
        pytest.skip("needs Chrome")
    template = BENCH / "templates" / "dashboard.html"
    png = SY.screenshot(template, tmp_path / "s.png", width=1440, scale=2, dark=True, chrome=chrome)
    with Image.open(png) as im:
        assert im.width == 2880

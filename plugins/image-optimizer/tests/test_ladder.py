from dataclasses import replace
from pathlib import Path

import pytest

from imgopt_lib import imaging as I
from imgopt_lib import ladder as L


def facts(fmt="jpeg", **kw):
    base = I.Facts(path=Path("x"), format=fmt, width=100, height=80, mode="RGB", has_alpha=False,
                   colors=None, icc=None, icc_desc=None, device_profile=False, orientation=1,
                   progressive=False, frames=1)
    return replace(base, **kw)


def labels(p):
    return [r.label for r in p.rungs]


def test_lossless_jpeg_keeps_the_profile_and_strips_exif_only_without_orientation():
    p = L.plan(facts(), profile="lossless")
    assert labels(p) == ["lossless-jpegoptim", "lossless-jpegtran"]
    jpegoptim, jpegtran = p.rungs
    assert "--strip-exif" in jpegoptim.args and "--strip-icc" not in jpegoptim.args
    assert jpegtran.args[-2:] == ("-copy", "icc")
    rotated = L.plan(facts(orientation=6), profile="lossless")
    assert "--strip-exif" not in rotated.rungs[0].args
    assert rotated.rungs[1].args[-2:] == ("-copy", "all")


def test_lossy_jpeg_adds_the_jpegoptim_ladder_and_guetzli():
    p = L.plan(facts(), profile="high")
    assert "jpegoptim-m95" in labels(p) and "jpegoptim-m40" in labels(p)
    guetzli = [r for r in p.rungs if r.tool == "guetzli"]
    assert [r.label for r in guetzli] == ["guetzli-q84", "guetzli-q90"]
    assert all(r.input == "source" for r in guetzli)
    assert all(r.post_jpegtran for r in guetzli)


def test_gray_jpeg_feeds_guetzli_rgb_pixels():
    p = L.plan(facts(mode="L"), profile="high")
    assert all(r.input == "pixels_flat" for r in p.rungs if r.tool == "guetzli")


def test_resize_encodes_jpeg_from_pixels_with_mozjpeg():
    p = L.plan(facts(), profile="high", resize=50)
    assert p.pixel_encode
    assert "lossless-jpegoptim" not in labels(p)
    assert all(r.input == "pixels_ppm" for r in p.rungs if r.tool == "cjpeg")


def test_lossless_profile_cannot_reencode_jpeg_pixels():
    with pytest.raises(L.UsageError, match="lossless profile cannot re-encode JPEG"):
        L.plan(facts(fmt="png"), profile="lossless", out_format="jpeg")


def test_png_with_few_colours_gets_no_palette_rungs():
    p = L.plan(facts(fmt="png", colors=24), profile="high")
    assert labels(p) == ["oxipng"]
    assert any("palette candidates skipped" in n for n in p.notes)


def test_png_palette_rungs_stay_below_the_source_colour_count_and_are_gated():
    p = L.plan(facts(fmt="png", colors=100), profile="high")
    colour_rungs = [r for r in p.rungs if r.label.startswith("pngquant-c")]
    assert [r.label for r in colour_rungs] == ["pngquant-c96", "pngquant-c64", "pngquant-c48",
                                              "pngquant-c32", "pngquant-c16"]
    assert all(r.palette and r.post_oxipng for r in p.rungs if r.tool == "pngquant")


def test_png_to_jpeg_with_alpha_notes_the_white_matte():
    p = L.plan(facts(fmt="png", has_alpha=True), profile="high", out_format="jpeg")
    assert any("flattened onto white" in n for n in p.notes)


def test_webp_and_avif_ladders():
    assert labels(L.plan(facts(fmt="png"), profile="lossless", out_format="webp")) == ["cwebp-lossless"]
    avif = L.plan(facts(fmt="png"), profile="high", out_format="avif")
    assert labels(avif)[0] == "avifenc-lossless" and "avifenc-q50" in labels(avif)


def test_gif_is_lossless_in_place_only():
    assert labels(L.plan(facts(fmt="gif"), profile="high")) == ["gifsicle"]
    with pytest.raises(L.UsageError):
        L.plan(facts(fmt="gif"), profile="lossless", resize=10)


def test_generate_runs_lossless_rungs(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"jpeg", "png"})
    jpg = factory.photo()
    for rung in L.plan(I.read_facts(jpg), profile="lossless").rungs:
        out = L.generate(rung, inputs={"source": jpg}, out_dir=tmp_path, tools=tools)
        assert out.is_file() and out.stat().st_size > 0


def test_pngquant_unreachable_quality_returns_none(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"png"})
    src = factory.photo(name="textured.png", size=(160, 120))  # thousands of colours: 99-100 is out of reach
    rung = L.Rung("pngquant-q99-100", "pngquant", "lossy", "pixels", ".png", ("--quality=99-100",),
                  post_oxipng=True, palette=True)
    assert L.generate(rung, inputs={"pixels": src}, out_dir=tmp_path, tools=tools) is None


def test_guetzli_rung_ends_with_a_progressive_jpeg(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    jpg = factory.photo(size=(64, 48))
    rung = next(r for r in L.plan(I.read_facts(jpg), profile="high").rungs if r.label == "guetzli-q84")
    out = L.generate(rung, inputs={"source": jpg}, out_dir=tmp_path, tools=tools)
    assert I.read_facts(out).progressive

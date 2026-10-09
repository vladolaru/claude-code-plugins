from pathlib import Path

import pytest
from PIL import Image

from imgopt_lib import imaging as I


def test_read_facts_reports_orientation_progressive_and_colours(factory):
    jpg = I.read_facts(factory.photo(orientation=6, progressive=True))
    assert (jpg.format, jpg.orientation, jpg.progressive) == ("jpeg", 6, True)
    png = I.read_facts(factory.gradient(colors=24))
    assert png.colors == 24
    assert not png.has_alpha
    assert I.read_facts(factory.logo()).has_alpha


def test_estimate_jpeg_quality_matches_the_ijg_scale(factory):
    for q in (40, 75, 90):
        est = I.estimate_jpeg_quality(factory.photo(name=f"q{q}.jpg", quality=q))
        assert abs(est - q) <= 1, (q, est)
    assert I.estimate_jpeg_quality(factory.logo()) is None


def test_display_pixels_applies_orientation(factory):
    path = factory.photo(size=(40, 20), orientation=6)
    assert I.display_pixels(path).size == (20, 40)


def test_display_pixels_converts_a_device_profile_to_srgb(factory, device_icc):
    plain = factory.photo(name="plain.png")
    tagged = factory.photo(name="p3.png", icc=device_icc)
    facts = I.read_facts(tagged)
    assert facts.device_profile and not I.is_srgb(facts.icc_desc)
    assert I.display_pixels(tagged).tobytes() != I.display_pixels(plain).tobytes()
    mean, peak = I.srgb_shift(tagged)
    assert mean > 0 and peak > 0


def test_is_srgb():
    assert I.is_srgb("sRGB IEC61966-2.1")
    assert I.is_srgb("sRGB built-in")
    assert not I.is_srgb("Display P3")
    assert not I.is_srgb(None)


def test_flatten_and_backgrounds():
    rgba = Image.new("RGBA", (2, 1), (255, 0, 0, 128))
    assert I.flatten(rgba, "white").getpixel((0, 0)) == (255, 127, 127)
    assert I.flatten(rgba, "dark").getpixel((0, 0))[0] > 140
    assert I.backgrounds(True) == ["white", "dark"]
    assert I.backgrounds(False) == ["white"]


def test_resize_width_keeps_aspect():
    img = Image.new("RGBA", (200, 100))
    assert I.resize_width(img, 100).size == (100, 50)


PROFILES = Path("/System/Library/ColorSync/Profiles")


def _system_profile(name: str) -> bytes:
    path = PROFILES / name
    if not path.is_file():
        pytest.skip(f"needs the system profile {path}")
    return path.read_bytes()


def test_display_pixels_converts_a_grey_profile_in_its_native_mode(tmp_path):
    icc = _system_profile("Generic Gray Profile.icc")
    path = tmp_path / "grey.png"
    Image.new("L", (4, 4), 100).save(path, icc_profile=icc)
    assert I.display_pixels(path).getpixel((0, 0)) == (119, 119, 119, 255)
    assert I.srgb_shift(path)[1] > 0


def test_display_pixels_keeps_alpha_out_of_a_grey_profile_transform(tmp_path):
    icc = _system_profile("Generic Gray Profile.icc")
    path = tmp_path / "grey-alpha.png"
    Image.new("LA", (4, 4), (100, 90)).save(path, icc_profile=icc)
    assert I.display_pixels(path).getpixel((0, 0)) == (119, 119, 119, 90)


def test_display_pixels_converts_a_cmyk_profile(tmp_path):
    icc = _system_profile("Generic CMYK Profile.icc")
    path = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (8, 8), (200, 10, 10, 0)).save(path, icc_profile=icc)
    out = I.display_pixels(path)
    assert out.mode == "RGBA" and out.size == (8, 8)


def test_display_pixels_names_the_file_when_the_profile_is_unreadable(tmp_path):
    path = tmp_path / "garbage-icc.png"
    Image.new("RGB", (4, 4), (10, 20, 30)).save(path, icc_profile=b"not a profile")
    with pytest.raises(I.ImagingError, match="garbage-icc.png"):
        I.display_pixels(path)
    with pytest.raises(I.ImagingError, match="garbage-icc.png"):
        I.srgb_shift(path)


def test_display_pixels_converts_a_palette_image_tagged_with_a_grey_profile(tmp_path):
    icc = _system_profile("Generic Gray Profile.icc")
    path = tmp_path / "palette-grey.png"
    Image.new("L", (4, 4), 100).quantize(2).save(path, icc_profile=icc)
    assert I.display_pixels(path).getpixel((0, 0)) == (119, 119, 119, 255)


@pytest.mark.parametrize("channels", ["L", "RGB", "RGBA"])
def test_16_bit_images_are_refused_by_the_shared_read_path(factory, channels):
    path = factory.deep_png(channels=channels)
    with pytest.raises(I.ImagingError, match="16-bit images are not supported yet"):
        I.read_facts(path)
    with pytest.raises(I.ImagingError, match="16-bit images are not supported yet"):
        I.display_pixels(path)


def test_an_upscaling_resize_is_refused(factory):
    path = factory.photo(size=(40, 20), orientation=6)  # displayed 20 px wide
    assert I.display_pixels(path, width=20).size == (20, 40)
    assert I.read_facts(path).display_width == 20
    with pytest.raises(I.ImagingError, match="would upscale"):
        I.display_pixels(path, width=30)


def test_a_file_whose_content_does_not_match_its_name_is_refused(factory):
    path = factory.photo(name="b.jpg")
    path = path.rename(path.with_name("b.png"))
    with pytest.raises(I.ImagingError, match="b.png: its content is JPEG but its name says PNG"):
        I.read_facts(path)


def test_a_jpeg_pillow_decodes_as_mpo_is_a_jpeg(factory):
    first = Image.open(factory.photo(name="a.jpg"))
    path = factory.root / "phone.jpg"
    first.save(path, "MPO", save_all=True, append_images=[first.copy()])
    assert Image.open(path).format == "MPO"
    facts = I.read_facts(path)
    assert facts.format == "jpeg" and facts.frames == 1  # browsers show only the first picture


def test_the_16_bit_check_reads_plain_tuple_tiles():
    """Pillow before 11 keeps tiles as plain tuples, without the ``args`` attribute."""
    class Old:
        mode = "RGB"
        tile = [("zip", (0, 0, 4, 4), 0, "RGB;16B")]
    with pytest.raises(I.ImagingError, match="16-bit"):
        I._refuse_16_bit(Old(), Path("old.png"))


def test_png_colour_chunks_are_facts(factory):
    f = I.read_facts(factory.gamma_png())
    assert dict(f.colour_chunks)["gamma"] == "1.0" and "chromaticity" in dict(f.colour_chunks)
    assert f.png_colour  # no sRGB chunk and no ICC profile: gAMA decides how browsers render it
    assert not I.read_facts(factory.logo()).png_colour


def test_apng_frames_are_counted(factory):
    assert I.read_facts(factory.apng(frames=3)).frames == 3


def test_metadata_kinds_reads_jpeg_segments(factory, tmp_path):
    src = factory.photo(orientation=6)
    with Image.open(src) as im:
        im.save(tmp_path / "c.jpg", "JPEG", exif=im.getexif().tobytes(), comment=b"(c) Someone")
    assert {"exif", "comment"} <= I.metadata_kinds(tmp_path / "c.jpg")


def test_metadata_kinds_reads_png_text(factory, tmp_path):
    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("Copyright", "Someone")
    Image.open(factory.logo()).save(tmp_path / "t.png", pnginfo=info)
    assert "text" in I.metadata_kinds(tmp_path / "t.png")

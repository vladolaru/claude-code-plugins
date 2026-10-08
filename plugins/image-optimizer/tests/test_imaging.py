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

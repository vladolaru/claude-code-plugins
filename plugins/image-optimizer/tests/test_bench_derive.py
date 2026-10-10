import sys
from pathlib import Path

import pytest
from PIL import Image

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

from imgbench import derive as D  # noqa: E402
from imgbench import manifest as M  # noqa: E402
from imgbench import sources as SR  # noqa: E402
from imgopt_lib import imaging as I  # noqa: E402
from imgopt_lib import tools as T  # noqa: E402

P3 = Path("/System/Library/ColorSync/Profiles/Display P3.icc")


@pytest.fixture
def tools():
    """The tools dict imgopt resolves for the benchmark: only cjpeg matters here, and it may be missing."""
    return T.check(T.Requirements(required=(), quality=(), optional=("cjpeg",))).tools


def _big(tmp_path, name="big.jpg", size=(4800, 3600)):
    """A large photo-like JPEG built from Pillow primitives (the conftest photo loops in Python: too slow here)."""
    lum = Image.linear_gradient("L").resize(size)
    im = Image.merge("RGB", (lum, lum.rotate(90).resize(size), Image.radial_gradient("L").resize(size)))
    path = tmp_path / name
    im.save(path, "JPEG", quality=95)
    return path


def test_phone_upload_displays_like_the_camera_jpeg(tmp_path, device_icc):
    cam = D.camera_jpeg(_big(tmp_path), tmp_path / "cam.jpg")
    phone = D.phone_upload(cam, tmp_path / "phone.jpg", device_icc)
    f = I.read_facts(phone)
    assert f.orientation == 6 and f.device_profile and f.display_width == 4032
    with Image.open(phone) as im:
        assert im.size == (3024, 4032)  # stored rotated, displayed upright


def test_recompressed_is_catalog_width_at_the_asked_quality(tmp_path):
    out = D.recompressed(_big(tmp_path, size=(4032, 3024)), tmp_path / "r.jpg", encoder="libjpeg", quality=75)
    assert I.read_facts(out).width == 1200 and abs(I.estimate_jpeg_quality(out) - 75) <= 2


def test_derivations_are_deterministic(tmp_path):
    src = _big(tmp_path)
    a = D.camera_jpeg(src, tmp_path / "a.jpg").read_bytes()
    b = D.camera_jpeg(src, tmp_path / "b.jpg").read_bytes()
    assert a == b


def test_slug_is_ascii_lowercase_and_comma_free():
    assert D.slug("Stopwatch, 1810201155, ako.jpg") == "stopwatch-1810201155-ako"
    assert D.slug("Café_Crème Brûlée (1).JPG") == "cafe-creme-brulee-1"
    assert D.slug("日本.jpg") == "image"  # nothing ASCII left: a fixed fallback keeps the name non-empty


def test_slugs_get_numbered_when_two_keys_collide():
    assert D.slugs(["A b.jpg", "a-b.png", "A,B.jpg", "c.jpg"]) == ["a-b", "a-b-2", "a-b-3", "c"]


def test_png_master_of_a_device_profile_photo_embeds_no_generated_profile(tmp_path, device_icc):
    """The sRGB profile imgopt converts to is generated with the current time in its header, so embedding it
    would give every build different bytes."""
    src = tmp_path / "p3.jpg"
    Image.open(_big(tmp_path)).save(src, "JPEG", quality=90, icc_profile=device_icc)
    with Image.open(D.png_master(src, tmp_path / "m.png")) as im:
        assert "icc_profile" not in im.info


def test_png_and_kodak_variants(tmp_path):
    src = _big(tmp_path)
    with Image.open(D.png_master(src, tmp_path / "m.png")) as im:
        assert im.format == "PNG" and im.width == 2000
    kodak = Image.new("RGB", (768, 512), (90, 120, 200))
    kodak_png = tmp_path / "kodim01.png"
    kodak.save(kodak_png)
    out = D.kodak_jpeg(kodak_png, tmp_path / "k.jpg")
    assert I.read_facts(out).width == 768 and abs(I.estimate_jpeg_quality(out) - 92) <= 2


def test_mozjpeg_recompression_uses_the_given_cjpeg(tmp_path, tools):
    cjpeg = tools["cjpeg"]
    if not cjpeg.ok:
        pytest.skip("needs mozjpeg's cjpeg")
    out = D.recompressed(_big(tmp_path, size=(2400, 1800)), tmp_path / "m.jpg", encoder="mozjpeg", quality=75,
                         cjpeg=cjpeg.path)
    assert I.read_facts(out).width == 1200 and not list(tmp_path.glob("*.ppm"))


def _tree(tmp_path):
    """A downloads folder with one original per kind and a sources.json that names them."""
    downloads, corpus = tmp_path / "downloads", tmp_path / "corpus"
    listed = []
    for category, key, size in (("photo-camera", "Stopwatch, 1810201155, ako.jpg", (4400, 3300)),
                                ("product-plain", "Radio.JPG", (1800, 1200))):
        path = downloads / category / key
        path.parent.mkdir(parents=True, exist_ok=True)
        _big(path.parent, key, size)
        listed.append(SR.Source(key, category, "https://example/x", "CC BY 4.0", "A", "ab" * 20, None))
    kodak = downloads / "photo-small" / "kodim01.png"
    kodak.parent.mkdir(parents=True)
    Image.new("RGB", (768, 512), (10, 200, 30)).save(kodak)
    listed.append(SR.Source("kodim01.png", "photo-small", "https://example/k", "Kodak license", "Kodak", None, None))
    sources_file = tmp_path / "sources.json"
    SR.save(listed, sources_file)
    return downloads, corpus, sources_file


def _paths(entries):
    return sorted(e.path for e in entries)


def test_build_all_names_every_file_from_an_ascii_slug_and_keeps_the_source_key(tmp_path, tools):
    downloads, corpus, sources_file = _tree(tmp_path)
    entries = D.build_all(downloads, corpus, tools, sources_file=sources_file, p3_profile=tmp_path / "no-p3.icc")
    got = _paths(entries)
    for expected in ("photo-camera/stopwatch-1810201155-ako.jpg", "phone-upload/stopwatch-1810201155-ako.jpg",
                     "png-master/stopwatch-1810201155-ako.png", "png-master/radio.png", "product-plain/radio.jpg",
                     "photo-small/kodim01.jpg", "jpeg-recompressed/radio-libjpeg-q95.jpg",
                     "jpeg-recompressed/stopwatch-1810201155-ako-libjpeg-q75.jpg"):
        assert expected in got
    assert all(path.isascii() and "," not in path and " " not in path for path in got)
    assert all((corpus / path).is_file() for path in got) and len(set(got)) == len(got)
    # every entry matches the file on disk, the licence comes from sources.json, the origin is the source key
    for e in entries:
        assert M.entry_for(corpus / e.path, corpus, category=e.category, origin=e.origin, transform=e.transform,
                           license=e.license) == e
    by_path = {e.path: e for e in entries}
    cam = by_path["photo-camera/stopwatch-1810201155-ako.jpg"]
    assert cam.origin == "derived:photo-camera/Stopwatch, 1810201155, ako.jpg" and cam.license == "CC BY 4.0"
    assert by_path["photo-small/kodim01.jpg"].license == "Kodak license"
    assert "camera_jpeg" in cam.transform and by_path["phone-upload/stopwatch-1810201155-ako.jpg"].width == 3024
    assert "no P3 profile on this machine" in by_path["phone-upload/stopwatch-1810201155-ako.jpg"].transform


def test_build_all_embeds_display_p3_when_the_machine_has_it(tmp_path, tools):
    if not P3.is_file():
        pytest.skip("needs the system Display P3 profile")
    downloads, corpus, sources_file = _tree(tmp_path)
    entries = D.build_all(downloads, corpus, tools, sources_file=sources_file, p3_profile=P3)
    phone = next(e for e in entries if e.category == "phone-upload")
    assert I.read_facts(corpus / phone.path).device_profile and "Display P3" in phone.transform


def test_build_all_adds_the_mozjpeg_variant_only_with_cjpeg(tmp_path, tools):
    downloads, corpus, sources_file = _tree(tmp_path)
    notes = []
    entries = D.build_all(downloads, corpus, {}, sources_file=sources_file, log=notes.append)
    assert not [e for e in entries if "mozjpeg" in e.path] and any("cjpeg" in n for n in notes)
    if tools["cjpeg"].ok:
        again = D.build_all(downloads, tmp_path / "corpus2", tools, sources_file=sources_file)
        assert sorted(e.path for e in again if "mozjpeg" in e.path) == [
            "jpeg-recompressed/radio-mozjpeg-q75.jpg", "jpeg-recompressed/stopwatch-1810201155-ako-mozjpeg-q75.jpg"]


def test_build_all_twice_gives_identical_bytes(tmp_path, tools):
    downloads, _, sources_file = _tree(tmp_path)
    one = D.build_all(downloads, tmp_path / "one", tools, sources_file=sources_file)
    two = D.build_all(downloads, tmp_path / "two", tools, sources_file=sources_file)
    assert [(e.path, e.sha256) for e in one] == [(e.path, e.sha256) for e in two]

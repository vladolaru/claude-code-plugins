import stat
from pathlib import Path

import pytest
from PIL import Image

from imgopt_lib import imaging as I
from imgopt_lib import metrics as M
from imgopt_lib.tools import Tool


def numpy_band_score(ref, new):
    """The session's prbuild/banding.py score for one background (reference implementation)."""
    np = pytest.importorskip("numpy")
    a = np.asarray(ref).astype(int)
    b = np.asarray(new).astype(int)
    pad = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
    stack = np.stack([pad[dy:dy + a.shape[0], dx:dx + a.shape[1]] for dy in range(3) for dx in range(3)])
    spread = (stack.max(axis=0) - stack.min(axis=0)).max(axis=2)
    smooth = (spread <= 6) & (spread > 0)
    d = np.abs(a - b).max(axis=2)
    if smooth.sum() < 50:
        return 0.0
    return float(np.percentile(d[smooth], 99.5))


def test_band_score_flags_a_gradient_cut_to_four_colours(factory):
    # Calibrated 2026-10-08: on this fixture Pillow's cut to 16 colours scores 2,
    # to 8 colours 4 (too close to the gate), to 4 colours 8.
    ref = Image.open(factory.gradient(colors=24)).convert("RGB")
    cut = ref.quantize(4).convert("RGB")
    assert M.band_score(ref, ref) == 0.0
    assert M.band_score(ref, cut) > 3


def test_band_score_matches_the_session_numpy_score(factory):
    ref = Image.open(factory.gradient(colors=24)).convert("RGB")
    for new in (ref.quantize(8).convert("RGB"), ref.quantize(16).convert("RGB")):
        assert abs(M.band_score(ref, new) - numpy_band_score(ref, new)) <= 0.5


def test_percentile_matches_linear_interpolation():
    hist = [0] * 256
    hist[1], hist[3] = 3, 1  # values 1,1,1,3
    assert M.percentile_from_histogram(hist, 50) == 1.0
    assert M.percentile_from_histogram(hist, 100) == 3.0
    assert abs(M.percentile_from_histogram(hist, 90) - 2.4) < 1e-9


def test_metadata_preserved_catches_orientation_and_device_profile(factory, device_icc):
    rotated = I.read_facts(factory.photo(name="r.jpg", orientation=6))
    plain = I.read_facts(factory.photo(name="p.jpg"))
    assert M.metadata_preserved(rotated, plain) == (False, "orientation tag 6 became 1")
    tagged = I.read_facts(factory.photo(name="t.jpg", icc=device_icc))
    ok, why = M.metadata_preserved(tagged, plain)
    assert not ok and "device colour profile" in why
    assert M.metadata_preserved(plain, plain) == (True, "")


def test_ssim_parsing_works_for_jpeg_png_and_alpha(factory, toolset, tmp_path):
    tools = toolset("compare")
    pairs = [
        (factory.photo(name="a.jpg", quality=95), factory.photo(name="b.jpg", quality=60)),
        (factory.logo(name="l1.png"), factory.logo(name="l2.png")),
    ]
    for a, b in pairs:
        ra, rb = I.display_pixels(a), I.display_pixels(b)
        s = M.measure(ra, rb, tools, tmp_path / "w")
        assert 0 < s.ssim <= 1 and 0 < s.ssim_white <= 1
        assert s.ssim <= s.ssim_white + 1e-12


def test_hidden_rgb_under_transparency_does_not_count(factory, toolset, tmp_path):
    """The session saw 0.74 for an image that scores 0.965 once flattened: an
    unflattened compare scores colour hidden under alpha 0. Repainting the
    invisible pixels must leave every score perfect."""
    tools = toolset("compare")
    logo = I.display_pixels(factory.logo())
    repainted = logo.copy()
    px = repainted.load()
    for y in range(repainted.height):
        for x in range(repainted.width):
            if px[x, y][3] == 0:
                px[x, y] = (0, 255, 0, 0)
    assert repainted.tobytes() != logo.tobytes()
    s = M.measure(logo, repainted, tools, tmp_path / "w")
    assert s.ssim == pytest.approx(1.0) and s.ss2 > 95


def test_svg_identical(tmp_path, toolset):
    tools = toolset("recompress", "lossless", {"svg"})
    a = tmp_path / "a.svg"
    b = tmp_path / "b.svg"
    c = tmp_path / "c.svg"
    a.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" width="10" height="10">'
                 '<!-- c --><rect x="1.0000" y="1" width="8" height="8" fill="#ff0000"/></svg>')
    b.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                 '<path fill="red" d="M1 1h8v8H1z"/></svg>')
    c.write_text(b.read_text().replace("red", "blue"))
    rsvg = tools["rsvg-convert"].path
    assert M.svg_identical(rsvg, a, b, tmp_path)
    assert not M.svg_identical(rsvg, a, c, tmp_path)


def test_frames_identical_compares_every_frame(tmp_path):
    red, blue = Image.new("RGB", (8, 8), "red"), Image.new("RGB", (8, 8), "blue")
    one = tmp_path / "one.gif"
    same = tmp_path / "same.gif"
    other = tmp_path / "other.gif"
    red.save(one, save_all=True, append_images=[blue], duration=50)
    red.save(same, save_all=True, append_images=[blue], duration=50, optimize=True)
    red.save(other, save_all=True, append_images=[red], duration=50)
    assert M.frames_identical(one, same)
    assert not M.frames_identical(one, other)


def test_worst_background_decides_ssim(toolset, tmp_path):
    """White pixels at changing alpha are invisible on white but not on dark."""
    tools = toolset("compare")
    size = (64, 64)
    ref = Image.new("RGBA", size, (255, 255, 255, 255))
    new = Image.new("RGBA", size, (255, 255, 255, 255))
    for x in range(0, 64, 8):
        for img, alpha in ((ref, 0), (new, 100)):
            img.paste((255, 255, 255, alpha), (x, 0, x + 4, 64))
    s = M.measure(ref, new, tools, tmp_path / "w")
    work = tmp_path / "check"
    work.mkdir()
    dark = [work / "ref.png", work / "new.png"]
    I.flatten(ref, "dark").save(dark[0])
    I.flatten(new, "dark").save(dark[1])
    assert s.ssim_white == pytest.approx(1.0)
    assert s.ssim < s.ssim_white
    assert s.ssim == pytest.approx(M.ssim_gray(tools["ffmpeg"].path, *dark))


def _fake_butteraugli(tmp_path, body):
    exe = tmp_path / "fake-butteraugli"
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return Tool("butteraugli_main", str(exe), "fake", "test")


def test_butteraugli_is_none_unless_every_background_has_a_value(factory, toolset, tmp_path):
    tools = dict(toolset("compare"))
    logo = I.display_pixels(factory.logo())
    other = I.display_pixels(factory.logo(name="l2.png"))
    tools["butteraugli_main"] = _fake_butteraugli(tmp_path, 'case "$2" in *dark*) exit 1;; esac\necho 1.5\n')
    assert M.measure(logo, other, tools, tmp_path / "w1").butteraugli is None
    tools["butteraugli_main"] = _fake_butteraugli(tmp_path, "echo 1.5\n")
    assert M.measure(logo, other, tools, tmp_path / "w2").butteraugli == 1.5


def test_butteraugli_can_be_left_out_of_a_measurement(factory, toolset, tmp_path):
    tools = dict(toolset("compare"))
    logo = I.display_pixels(factory.logo())
    other = I.display_pixels(factory.logo(name="l2.png"))
    tools["butteraugli_main"] = _fake_butteraugli(tmp_path, "echo 1.5\n")
    assert M.measure(logo, other, tools, tmp_path / "w1", with_butteraugli=False).butteraugli is None
    assert M.butteraugli_score(logo, other, tools, tmp_path / "w1") == 1.5


def test_butteraugli_score_is_none_without_the_tool_or_a_value(factory, toolset, tmp_path):
    tools = dict(toolset("compare"))
    logo = I.display_pixels(factory.logo())
    other = I.display_pixels(factory.logo(name="l2.png"))
    tools.pop("butteraugli_main", None)
    assert M.butteraugli_score(logo, other, tools, tmp_path) is None
    tools["butteraugli_main"] = _fake_butteraugli(tmp_path, 'case "$2" in *dark*) exit 1;; esac\necho 1.5\n')
    assert M.butteraugli_score(logo, other, tools, tmp_path) is None


def test_the_flattened_reference_is_written_once_per_background(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("compare")
    ref = I.display_pixels(factory.logo())
    others = [I.display_pixels(factory.logo(name=f"l{i}.png")) for i in range(2)]
    saves, cache = [], {}
    original = Image.Image.save
    monkeypatch.setattr(Image.Image, "save", lambda self, fp, *a, **k: (saves.append(Path(fp).name),
                                                                       original(self, fp, *a, **k))[1])
    work = tmp_path / "w"
    first = M.measure(ref, others[0], tools, work, with_butteraugli=False, ref_cache=cache)
    M.measure(ref, others[1], tools, work, with_butteraugli=False, ref_cache=cache)
    assert sorted(saves).count("ref_white.png") == 1 and sorted(saves).count("ref_dark.png") == 1
    assert first.ssim == M.measure(ref, others[0], tools, tmp_path / "w2", with_butteraugli=False).ssim


def test_a_tool_that_cannot_run_raises_metric_error(tmp_path):
    missing = str(tmp_path / "no-such-ffmpeg")
    with pytest.raises(M.MetricError, match="no-such-ffmpeg"):
        M.ssim_gray(missing, tmp_path / "a.png", tmp_path / "b.png")


def test_losing_a_gamma_chunk_is_not_lossless(factory):
    src = I.read_facts(factory.gamma_png())
    plain = I.read_facts(factory.photo(name="plain.png", size=(200, 120)))
    ok, why = M.metadata_preserved(src, plain)
    assert not ok and "gAMA" in why


def test_svg_semantics_lost_names_removed_ids_and_roles(factory, tmp_path):
    src = factory.accessible_svg()
    stripped = tmp_path / "stripped.svg"
    stripped.write_text(src.read_text().replace(' role="img"', "").replace(' id="t"', ""))
    lost = M.svg_semantics_lost(src, stripped)
    assert "role='img'" in lost and "id='t'" in lost
    assert M.svg_semantics_lost(src, src) == ""

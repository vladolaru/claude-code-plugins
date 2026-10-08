import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from imgopt_lib import candidates as C
from imgopt_lib import gates as G
from imgopt_lib import ladder

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "imgopt.py"


def opts(out, profile="lossless", **kw):
    return C.Options(profile=profile, out=out, gates=G.gates_for(profile), **kw)


def quiet(_):
    return None


def test_lossless_default_on_a_folder(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"jpeg", "png"})
    factory.photo()
    factory.logo()
    from imgopt_lib.formats import expand_inputs
    records = C.run(expand_inputs([factory.root]), opts(tmp_path / "out"), tools, log=quiet)
    assert len(records) == 2
    for r in records:
        chosen = C.pick_of(r)
        if chosen:
            assert chosen["kind"] == "lossless" and chosen["identical"]
        assert (tmp_path / "out").exists()
    assert len(C.load_records(tmp_path / "out")) == 2


def test_lossless_jpeg_keeps_orientation(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"jpeg"})
    src = factory.photo(orientation=6, quality=95)
    [r] = C.run([src.resolve()], opts(tmp_path / "out"), tools, log=quiet)
    assert not any(c.get("discarded") for c in r["candidates"] if c["label"] == "lossless-jpegoptim")
    chosen = C.pick_of(r)
    if chosen:
        folder = C.load_records(tmp_path / "out")[0][0]
        with Image.open(folder / chosen["file"]) as im:
            assert im.getexif().get(0x0112) == 6


def test_few_colour_gradient_keeps_the_lossless_floor(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"png"})
    src = factory.gradient(colors=24)
    [r] = C.run([src.resolve()], opts(tmp_path / "out", "high"), tools, log=quiet)
    assert [c["label"] for c in r["candidates"]] == ["oxipng"]
    assert any("palette candidates skipped" in n for n in r["notes"])


def test_banded_palettes_fail_the_gate(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"png"})
    src = factory.gradient(size=(480, 60), lo=20, hi=230)
    [r] = C.run([src.resolve()], opts(tmp_path / "out", "high"), tools, log=quiet)
    palette = [c for c in r["candidates"] if c.get("band_gated") and "band" in c]
    assert palette, "expected palette candidates"
    assert all(c["band"] <= 3 for c in palette if c["pass"])
    assert any("banding" in c["reason"] for c in palette)


def test_cache_reuses_unchanged_candidates_and_invalidates_on_tool_version(factory, toolset, tmp_path, monkeypatch):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.logo().resolve()
    C.run([src], opts(tmp_path / "out"), tools, log=quiet)

    def boom(*a, **k):
        raise AssertionError("re-encoded a cached candidate")

    monkeypatch.setattr(ladder, "generate", boom)
    C.run([src], opts(tmp_path / "out"), tools, log=quiet)
    bumped = dict(tools)
    bumped["oxipng"] = type(tools["oxipng"])("oxipng", tools["oxipng"].path, "99.0", "path")
    with pytest.raises(AssertionError, match="re-encoded"):
        C.run([src], opts(tmp_path / "out"), bumped, log=quiet)


def test_ref_measures_lossless_candidates_against_the_baseline(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    original = factory.photo(name="orig.jpg", quality=95)
    current = factory.photo(name="current.jpg", quality=50)
    [r] = C.run([current.resolve()], opts(tmp_path / "out", "high", ref=original.resolve()), tools, log=quiet)
    lossless = [c for c in r["candidates"] if c["label"].startswith("lossless-") and "ssim" in c]
    assert lossless and all(not c["identical"] and c["kind"] == "lossy" for c in lossless)
    assert r["ref"]["path"] == str(original.resolve())


def test_webp_output_is_labelled_uncalibrated(factory, toolset, tmp_path):
    tools = toolset("convert", "high", {"png"}, "webp")
    src = factory.logo().resolve()
    [r] = C.run([src], opts(tmp_path / "out", "high", out_format="webp"), tools, log=quiet)
    assert r["uncalibrated"] and r["format"] == "webp"
    assert r["target"].endswith(".webp")


def test_png_to_jpeg_flattens_alpha_with_a_note(factory, toolset, tmp_path):
    tools = toolset("convert", "high", {"png"}, "jpeg")
    src = factory.logo().resolve()
    [r] = C.run([src], opts(tmp_path / "out", "high", out_format="jpeg"), tools, log=quiet)
    assert any("flattened onto white" in n for n in r["notes"])


def test_cli_candidates_prints_tools_and_next_step(factory, tmp_path):
    src = factory.logo()
    proc = subprocess.run([sys.executable, str(SCRIPT), "candidates", str(src), "--out", str(tmp_path / "o")],
                          capture_output=True, text=True)
    if proc.returncode == 2 and "BLOCKED" in proc.stdout:
        pytest.skip("tools missing: " + proc.stdout.splitlines()[-1])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "tools:" in proc.stdout and "Next:" in proc.stdout


def test_unconvertible_colour_profile_skips_that_file_and_continues(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"png"})
    bad = factory.photo(name="bad.png", icc=b"not an ICC profile")
    good = factory.logo()
    lines = []
    records = C.run([bad.resolve(), good.resolve()], opts(tmp_path / "out"), tools, log=lines.append)
    assert [r["source"]["path"] for r in records] == [str(good.resolve())]
    assert any(f"== {bad.resolve()}: skipped:" in line for line in lines)
    assert [r["source"]["path"] for _, r in C.load_records(tmp_path / "out")] == [str(good.resolve())]
    assert not list((tmp_path / "out").glob("*/metrics.json.tmp"))


def test_cli_exits_1_when_a_file_was_skipped(factory, tmp_path):
    factory.photo(name="bad.png", icc=b"not an ICC profile")
    factory.logo()
    proc = subprocess.run([sys.executable, str(SCRIPT), "candidates", str(factory.root), "--out", str(tmp_path / "o")],
                          capture_output=True, text=True)
    if proc.returncode == 2 and "BLOCKED" in proc.stdout:
        pytest.skip("tools missing: " + proc.stdout.splitlines()[-1])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "skipped:" in proc.stdout and "Next:" in proc.stdout
    assert len(C.load_records(tmp_path / "o")) == 1

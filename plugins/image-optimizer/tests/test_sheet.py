import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from imgopt_lib import sheet as S
from imgopt_lib import tools as T

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "imgopt.py"


def fake_out(tmp_path, factory, *, uncalibrated=False, band_gated=False, size=(160, 120), alpha=False,
             name="photo--x"):
    out = tmp_path / "out"
    folder = out / name
    folder.mkdir(parents=True)
    src = factory.logo(size=size) if alpha else factory.photo(size=size)
    ext = src.suffix
    (folder / f"source{ext}").write_bytes(src.read_bytes())
    ref = Image.open(src).convert("RGBA")
    ref.save(folder / "reference.png")
    pick = folder / "pick.png"
    ref.quantize(8).convert("RGBA").save(pick)
    record = {
        "schema": 1, "source": {"path": str(src), "size": src.stat().st_size, "format": "png",
                                "width": size[0], "height": size[1], "colors": None},
        "format": "webp" if uncalibrated else "png", "uncalibrated": uncalibrated, "waived": ["guetzli"],
        "notes": [], "candidates": [{"label": "pngquant-c8", "file": "pick.png", "kind": "lossy",
                                     "size": pick.stat().st_size, "band_gated": band_gated, "ssim": 0.95,
                                     "ssim_white": 0.95, "ss2": 70.0, "band": 5.0, "pass": True, "reason": ""}],
        "pick": "pick.png", "verdict": "apply", "verdict_reason": "x", "profile": "high",
    }
    (folder / "metrics.json").write_text(json.dumps(record))
    return out


def test_tiles_stay_under_1000px_and_mark_the_smooth_tile_required(tmp_path, factory):
    out = fake_out(tmp_path, factory, size=(900, 600))
    tiles, page = S.build(out)
    assert {t.window for t in tiles} == {"smooth", "overall"}
    assert [t.window for t in tiles if t.required] == ["smooth"]
    for t in tiles:
        assert Image.open(t.path).width <= 1000
    assert page.is_file() and "pngquant-c8" in page.read_text()


def test_small_images_get_a_2x_tile(tmp_path, factory):
    tiles, _ = S.build(fake_out(tmp_path, factory, size=(160, 120)))
    two_x = [t for t in tiles if t.window.endswith("@2x")]
    assert two_x and all(Image.open(t.path).width <= 1000 for t in two_x)


def test_uncalibrated_requires_every_tile_and_alpha_adds_an_edge_tile(tmp_path, factory):
    tiles, page = S.build(fake_out(tmp_path, factory, uncalibrated=True, alpha=True, size=(500, 500)))
    assert "edge" in {t.window for t in tiles}
    assert all(t.required for t in tiles)
    text = page.read_text()
    assert "UNCALIBRATED" in text and "guetzli" in text


def test_gated_palette_pick_has_no_required_tiles(tmp_path, factory):
    tiles, _ = S.build(fake_out(tmp_path, factory, band_gated=True, size=(900, 600)))
    assert tiles and not any(t.required for t in tiles)


def test_lossless_and_untouched_files_get_a_card_but_no_tiles(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    record["candidates"][0]["kind"] = "lossless"
    path.write_text(json.dumps(record))
    tiles, page = S.build(out)
    assert tiles == [] and "pngquant-c8" in page.read_text()
    # No pick at all, and an errored candidate without a file: nothing to show, nothing to crash on.
    record.update(pick=None, verdict="keep", verdict_reason="nothing beats the original")
    record["candidates"] = [{"label": "pngquant-c8", "kind": "lossy", "band_gated": True,
                             "error": "encoder failed", "pass": False, "reason": "error"}]
    path.write_text(json.dumps(record))
    tiles, page = S.build(out)
    assert tiles == [] and "nothing beats the original" in page.read_text()


def test_page_urls_are_quoted(tmp_path, factory):
    _, page = S.build(fake_out(tmp_path, factory, name="my photo--x"))
    assert "../my%20photo--x/pick.png" in page.read_text()


def test_unreadable_pick_is_reported_and_the_other_files_still_get_tiles(tmp_path, factory):
    out = fake_out(tmp_path, factory, name="a--x")
    (out / "a--x" / "pick.png").write_bytes(b"not an image")
    good = fake_out(tmp_path, factory, name="b--x")
    assert good == out
    problems: list[str] = []
    tiles, page = S.build(out, problems=problems)
    assert tiles and all("b--x" in str(t.path) for t in tiles)
    assert len(problems) == 1 and "a--x" in problems[0]
    assert page.is_file()


def run_sheet(*args):
    return subprocess.run([sys.executable, str(SCRIPT), "sheet", *map(str, args)], capture_output=True, text=True)


def test_sheet_cli_lists_required_tiles_and_the_page(tmp_path, factory):
    out = fake_out(tmp_path, factory, size=(900, 600))
    proc = run_sheet(out)
    assert proc.returncode == 0, proc.stderr
    assert "REQUIRED" in proc.stdout and "index.html" in proc.stdout and "tools: pillow" in proc.stdout
    assert "apply" in proc.stdout.splitlines()[-1]


def test_sheet_cli_exits_1_when_a_file_cannot_be_read(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    (out / "photo--x" / "pick.png").write_bytes(b"not an image")
    proc = run_sheet(out)
    assert proc.returncode == 1
    assert "photo--x" in proc.stdout + proc.stderr


def test_screenshot_with_a_real_chrome(tmp_path, factory):
    chrome = T.resolve("chrome")
    if not chrome.ok:
        pytest.skip("needs chrome")
    _, page = S.build(fake_out(tmp_path, factory))
    png = S.screenshot(page, chrome.path)
    assert png.name == "page@2x.png" and Image.open(png).width >= 2000


def test_screenshot_failure_does_not_leave_a_stale_png(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<html></html>")
    (tmp_path / "page@2x.png").write_bytes(b"stale")
    with pytest.raises(RuntimeError, match="screenshot"):
        S.screenshot(page, "/usr/bin/false")


def test_sheet_cli_browser_flag_takes_a_screenshot(tmp_path, factory):
    if not T.resolve("chrome").ok:
        pytest.skip("needs chrome")
    proc = run_sheet(fake_out(tmp_path, factory), "--browser")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Screenshot at 2x" in proc.stdout and "tools: " in proc.stdout and "chrome" in proc.stdout

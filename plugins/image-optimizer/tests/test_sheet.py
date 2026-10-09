import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from imgopt_lib import sheet as S
from imgopt_lib import tools as T
from imgopt_lib.ladder import UsageError

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
    with pytest.raises(RuntimeError, match="screenshot.*drop --browser"):
        S.screenshot(page, "/usr/bin/false")


def test_sheet_cli_browser_flag_takes_a_screenshot(tmp_path, factory):
    if not T.resolve("chrome").ok:
        pytest.skip("needs chrome")
    proc = run_sheet(fake_out(tmp_path, factory), "--browser")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Screenshot at 2x" in proc.stdout and "tools: " in proc.stdout and "chrome" in proc.stdout


def window_flags(tiles):
    return {(t.window, t.required) for t in tiles}


@pytest.mark.parametrize("kwargs, expected", [
    (dict(size=(900, 600)), {("smooth", True), ("overall", False)}),
    (dict(size=(160, 120)), {("smooth", True), ("smooth@2x", True), ("overall", False), ("overall@2x", False)}),
    (dict(size=(900, 600), band_gated=True), {("smooth", False), ("overall", False)}),
    (dict(size=(500, 500), alpha=True), {("smooth", True), ("overall", False), ("edge", False)}),
    (dict(size=(500, 500), alpha=True, uncalibrated=True), {("smooth", True), ("overall", True), ("edge", True)}),
    (dict(size=(120, 120), alpha=True, uncalibrated=True),
     {(w + s, True) for w in ("smooth", "overall", "edge") for s in ("", "@2x")}),
], ids=["large", "small-2x", "gated", "alpha-edge-optional", "uncalibrated-large", "uncalibrated-small"])
def test_which_tiles_are_required(tmp_path, factory, kwargs, expected):
    tiles, _ = S.build(fake_out(tmp_path, factory, **kwargs))
    assert window_flags(tiles) == expected


def test_the_2x_rule_follows_the_displayed_width_not_the_source_width(tmp_path, factory):
    out = fake_out(tmp_path, factory, size=(300, 200))
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    record["source"]["width"] = 2400  # a 2400 px source resized to 300
    path.write_text(json.dumps(record))
    tiles, _ = S.build(out)
    assert {"smooth@2x", "overall@2x"} <= {t.window for t in tiles}
    out = fake_out(tmp_path / "up", factory, size=(900, 600))
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    record["source"]["width"] = 300  # a small source scaled up to 900
    path.write_text(json.dumps(record))
    tiles, _ = S.build(out)
    assert not any(t.window.endswith("@2x") for t in tiles)


def test_a_mistyped_or_empty_out_is_refused_before_anything_is_written(tmp_path):
    missing = tmp_path / "nope"
    proc = run_sheet(missing)
    assert proc.returncode == 2 and str(missing) in proc.stdout + proc.stderr
    assert not missing.exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = run_sheet(empty)
    assert proc.returncode == 2 and str(empty) in proc.stdout + proc.stderr
    assert not (empty / "_sheet").exists()
    assert "Next:" not in proc.stdout
    with pytest.raises(UsageError, match="nope"):
        S.build(missing)


def test_a_missing_source_copy_is_a_problem(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    (out / "photo--x" / "source.jpg").unlink()
    problems: list[str] = []
    S.build(out, problems=problems)
    assert len(problems) == 1 and "photo--x" in problems[0] and "source" in problems[0]


def test_an_alt_label_that_matches_nothing_is_a_problem(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    problems: list[str] = []
    _, page = S.build(out, alt="pngquant-c8", problems=problems)
    assert problems == [] and "Alternative: pngquant-c8" in page.read_text()
    S.build(out, alt="jpegoptim-m70", problems=problems)
    assert len(problems) == 1 and "jpegoptim-m70" in problems[0]
    proc = run_sheet(out, "--alt", "jpegoptim-m70")
    assert proc.returncode == 1 and "jpegoptim-m70" in proc.stderr
    assert "Next:" not in proc.stdout


def test_cli_counts_lossy_picks_it_could_not_tile_and_withholds_the_apply_line(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    (out / "photo--x" / "pick.png").write_bytes(b"not an image")
    proc = run_sheet(out)
    assert proc.returncode == 1
    assert "No lossy picks" not in proc.stdout
    assert "1 lossy pick(s) could not be tiled" in proc.stdout
    assert "Next:" not in proc.stdout
    assert "photo--x" in proc.stderr


def test_cli_with_only_lossless_picks_points_at_apply_without_approval(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    record["candidates"][0]["kind"] = "lossless"
    path.write_text(json.dumps(record))
    proc = run_sheet(out)
    assert proc.returncode == 0
    assert "No lossy picks" in proc.stdout
    assert "apply" in proc.stdout.splitlines()[-1] and "--approve" not in proc.stdout.splitlines()[-1]


def _edit_record(out, **changes):
    path = out / "photo--x" / "metrics.json"
    record = json.loads(path.read_text())
    record["source"]["format"] = changes.pop("source_format", record["source"]["format"])
    record["candidates"][0].update(changes.pop("pick", {}))
    record.update(changes)
    path.write_text(json.dumps(record))


def test_a_lossy_pick_in_a_new_format_requires_every_tile(tmp_path, factory):
    out = fake_out(tmp_path, factory, band_gated=True, size=(900, 600))  # band-gated alone: nothing required
    _edit_record(out, source_format="jpeg", format="png")
    tiles, _ = S.build(out)
    assert tiles and all(t.required for t in tiles)


def test_a_pixel_identical_pick_in_a_new_format_gets_no_tiles(tmp_path, factory):
    out = fake_out(tmp_path, factory)
    _edit_record(out, source_format="png", format="jpeg", pick={"kind": "lossless", "identical": True})
    tiles, _ = S.build(out)
    assert tiles == []


def test_the_card_meta_names_the_metadata_the_pick_removes():
    record = {"source": {"width": 10, "height": 10, "format": "jpeg", "size": 2048}, "format": "jpeg",
              "verdict": "apply", "verdict_reason": "smaller"}
    chosen = {"size": 1024, "label": "x", "identical": True, "metadata_removed": ["exif"]}
    assert "removes exif" in S._meta(record, chosen)
    assert "removes" not in S._meta(record, {k: v for k, v in chosen.items() if k != "metadata_removed"})

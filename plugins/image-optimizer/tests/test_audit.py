import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from imgopt_lib import audit as A
from imgopt_lib import ladder

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "imgopt.py"


def run_inspect(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT), "inspect", *map(str, args)],
                          capture_output=True, text=True)
    if proc.returncode == 2 and "BLOCKED" in proc.stdout:
        pytest.skip("tools missing: " + proc.stdout.splitlines()[-1])
    return proc


def test_inspect_reports_facts_and_lossless_headroom(factory, toolset, tmp_path):
    tools = toolset("audit", "lossless", {"jpeg", "png"})
    row = A.inspect_file(factory.photo(quality=75, orientation=6), tools, tmp_path)
    assert row["format"] == "jpeg" and abs(row["quality"] - 75) <= 1 and row["orientation"] == 6
    assert row["error"] is None
    assert row["lossless_size"] is None or row["lossless_size"] <= row["size"]
    png = A.inspect_file(factory.logo(), tools, tmp_path)
    assert png["format"] == "png" and png["quality"] is None
    assert png["lossless_size"] is None or png["lossless_size"] <= png["size"]


def test_inspect_cli_json_changes_nothing(factory, tmp_path):
    src = factory.photo()
    before = src.read_bytes()
    proc = run_inspect("--json", src)
    assert proc.returncode == 0, proc.stderr
    rows = json.loads(proc.stdout)
    assert rows[0]["path"] == str(src.resolve())
    assert "jpegtran" in proc.stderr, "the tools line goes to stderr so stdout stays JSON"
    assert src.read_bytes() == before


def test_unconvertible_profile_marks_the_row_and_the_run_goes_on(factory, tmp_path):
    bad = factory.gradient("bad.png")
    with Image.open(bad) as im:
        im.save(bad, "PNG", icc_profile=b"not an icc profile at all" * 20)
    good = factory.photo()
    proc = run_inspect("--json", bad, good)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    rows = json.loads(proc.stdout)
    assert [Path(r["path"]).name for r in rows] == ["bad.png", "photo.jpg"]
    assert rows[0]["error"] and rows[0]["width"] == 480 and rows[0]["headroom"] is None
    assert rows[1]["error"] is None
    table = run_inspect(bad)
    assert table.returncode == 1 and "error: " in table.stdout


def _not_an_image(factory):
    path = factory.root / "x.png"
    path.write_text("this is not a PNG")
    return path


@pytest.mark.parametrize("make_bad, name", [(lambda f: f.truncated_jpeg(), "cut.jpg"), (_not_an_image, "x.png")])
def test_unreadable_file_marks_the_row_and_the_run_goes_on(factory, make_bad, name):
    bad = make_bad(factory)
    good = factory.logo()
    proc = run_inspect("--json", bad, good)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    rows = json.loads(proc.stdout)
    assert [Path(r["path"]).name for r in rows] == [name, "logo.png"]
    assert rows[0]["error"] and rows[0]["headroom"] is None
    assert rows[1]["error"] is None and rows[1]["width"] == 120
    assert "error: " in run_inspect(bad, good).stdout


def test_headroom_counts_the_verified_lossless_saving(factory, toolset, tmp_path):
    tools = toolset("audit", "lossless", {"jpeg", "png"})
    jpg = factory.root / "baseline.jpg"
    Image.open(factory.photo()).save(jpg, "JPEG", quality=85, optimize=False, progressive=False)
    png = factory.root / "stored.png"
    Image.open(factory.logo()).save(png, "PNG", compress_level=0)
    for path in (jpg, png):
        row = A.inspect_file(path, tools, tmp_path)
        assert row["headroom"] > 0, path.name
        assert row["lossless_size"] == row["size"] - row["headroom"] < row["size"]


@pytest.mark.parametrize("orientation, altered", [
    (1, lambda src, out: Image.open(src).point(lambda v: 255 - v).save(out)),   # pixels changed
    (6, lambda src, out: Image.open(src).save(out)),                            # orientation tag dropped
])
def test_headroom_ignores_candidates_that_change_what_the_viewer_sees(
        factory, toolset, tmp_path, monkeypatch, orientation, altered):
    tools = toolset("audit", "lossless", {"jpeg"})
    src = factory.photo(orientation=orientation)

    def fake_generate(rung, *, inputs, out_dir, tools):
        out = Path(out_dir) / f"{rung.label}.jpg"
        altered(inputs["source"], out)
        return out

    monkeypatch.setattr(ladder, "generate", fake_generate)
    row = A.inspect_file(src, tools, tmp_path)
    assert row["lossless_size"] is None and row["headroom"] is None

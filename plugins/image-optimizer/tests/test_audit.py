import json
import subprocess
import sys
from pathlib import Path

from imgopt_lib import audit as A

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "imgopt.py"


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
    proc = subprocess.run([sys.executable, str(SCRIPT), "inspect", "--json", str(src)],
                          capture_output=True, text=True)
    if proc.returncode == 2:
        return  # blocked on tools; the report is the output
    assert proc.returncode == 0, proc.stderr
    rows = json.loads(proc.stdout)
    assert rows[0]["path"] == str(src.resolve())
    assert src.read_bytes() == before


def test_unconvertible_profile_marks_the_row_and_the_run_goes_on(factory, tmp_path):
    bad = factory.gradient("bad.png")
    from PIL import Image
    with Image.open(bad) as im:
        im.save(bad, "PNG", icc_profile=b"not an icc profile at all" * 20)
    good = factory.photo()
    proc = subprocess.run([sys.executable, str(SCRIPT), "inspect", "--json", str(bad), str(good)],
                          capture_output=True, text=True)
    if proc.returncode == 2:
        return  # blocked on tools
    assert proc.returncode == 1, proc.stdout + proc.stderr
    rows = json.loads(proc.stdout)
    assert [Path(r["path"]).name for r in rows] == ["bad.png", "photo.jpg"]
    assert rows[0]["error"] and rows[0]["width"] == 480 and rows[0]["headroom"] is None
    assert rows[1]["error"] is None
    table = subprocess.run([sys.executable, str(SCRIPT), "inspect", str(bad)],
                           capture_output=True, text=True)
    assert table.returncode == 1 and "error: " in table.stdout

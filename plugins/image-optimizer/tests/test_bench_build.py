import json
import subprocess
import sys
from pathlib import Path

ENTRY = Path(__file__).resolve().parents[1] / "bench" / "imgbench.py"


def test_build_edge_and_synthetic_categories_twice_gives_identical_bytes(tmp_path):
    env = {"IMGOPT_CACHE": str(tmp_path / "c"), "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"}
    cmd = [sys.executable, str(ENTRY), "build", "--only", "edge,icon"]
    first = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert first.returncode == 0, first.stderr
    corpus = next((tmp_path / "c" / "bench").glob("corpus-v*"))
    a = json.loads((corpus / "corpus.json").read_text())
    subprocess.run(cmd, capture_output=True, text=True, env=env, check=True)
    b = json.loads((corpus / "corpus.json").read_text())
    assert [e["sha256"] for e in a] == [e["sha256"] for e in b] and len(a) >= 20


def run(tmp_path, *args):
    env = {"IMGOPT_CACHE": str(tmp_path / "c"), "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"}
    return subprocess.run([sys.executable, str(ENTRY), "build", *args], capture_output=True, text=True, env=env)


def test_an_unknown_category_is_a_usage_error(tmp_path):
    result = run(tmp_path, "--only", "icon,nope")
    assert result.returncode == 2 and "nope" in result.stdout


def test_categories_without_downloads_are_skipped_and_the_rest_are_kept(tmp_path):
    first = run(tmp_path, "--only", "icon")
    assert first.returncode == 0, first.stderr
    second = run(tmp_path, "--only", "gpl-asset,photo-camera")
    assert second.returncode == 0, second.stderr
    assert second.stdout.count("skipped: run fetch first") == 7  # gpl-asset and the six photo categories
    corpus = next((tmp_path / "c" / "bench").glob("corpus-v*"))
    assert {e["category"] for e in json.loads((corpus / "corpus.json").read_text())} == {"icon"}

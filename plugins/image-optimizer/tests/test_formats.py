import subprocess
from pathlib import Path

import pytest

from imgopt_lib.formats import WORKDIR_MARKER, expand_inputs, format_of, subdir_name


def test_format_of_maps_extensions_case_insensitively():
    assert format_of(Path("a.JPG")) == "jpeg"
    assert format_of(Path("a.jpeg")) == "jpeg"
    assert format_of(Path("a.svg")) == "svg"
    assert format_of(Path("a.txt")) is None


def test_expand_inputs_walks_folders_and_skips_hidden_and_unknown(tmp_path):
    (tmp_path / "d" / ".git").mkdir(parents=True)
    for rel in ("d/a.png", "d/sub/b.jpg", "d/.git/c.png", "d/notes.txt"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    got = [p.relative_to(tmp_path.resolve()).as_posix() for p in expand_inputs([tmp_path / "d"])]
    assert got == ["d/a.png", "d/sub/b.jpg"]


def test_expand_inputs_rejects_missing_and_unsupported(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        expand_inputs([tmp_path / "nope.png"])
    (tmp_path / "x.bmp").write_bytes(b"x")
    with pytest.raises(ValueError, match="not a supported image"):
        expand_inputs([tmp_path / "x.bmp"])


def test_subdir_name_is_stable_and_distinguishes_same_basenames(tmp_path):
    a = tmp_path / "one" / "logo.png"
    b = tmp_path / "two" / "logo.png"
    assert subdir_name(a) == subdir_name(a)
    assert subdir_name(a) != subdir_name(b)
    assert "logo.png" in subdir_name(a)


@pytest.mark.parametrize("raw", ["/a/x.png", "/x.png"])
def test_subdir_name_is_one_relative_component_near_the_root(raw):
    name = subdir_name(Path(raw))
    assert not Path(name).is_absolute()
    assert "/" not in name
    assert name.startswith(("a__x.png--", "x.png--"))


def test_expand_inputs_keeps_first_seen_order_without_duplicates(tmp_path):
    for name in ("b.png", "a.png", "c.jpg"):
        (tmp_path / name).write_bytes(b"x")
    got = expand_inputs([tmp_path / "c.jpg", tmp_path, tmp_path / "b.png", tmp_path / "." / "c.jpg"])
    assert [p.name for p in got] == ["c.jpg", "a.png", "b.png"]


def test_folder_inputs_skip_vendored_ignored_and_working_folders(tmp_path):
    root = tmp_path / "site"
    for rel in ("img/a.png", "node_modules/pkg/b.png", "vendor/c.png", "build/d.png", "work/x--1/e.png"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    (root / "work" / "run.json").write_text("{}")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("build/\n")
    skipped: list[str] = []
    found = expand_inputs([root], skipped=skipped)
    assert [p.name for p in found] == ["a.png"]
    assert any("node_modules" in s for s in skipped) and any("git-ignored" in s for s in skipped)
    assert any("imgopt working folder" in s for s in skipped)


def test_folder_inputs_skip_a_folder_carrying_the_workdir_marker(tmp_path):
    (tmp_path / "work" / "x--1").mkdir(parents=True)
    (tmp_path / "work" / "x--1" / "e.png").write_bytes(b"x")
    (tmp_path / "work" / WORKDIR_MARKER).write_text("")
    skipped: list[str] = []
    assert expand_inputs([tmp_path], skipped=skipped) == []
    assert any("imgopt working folder" in s for s in skipped)


def test_an_explicit_file_is_never_skipped(tmp_path):
    p = tmp_path / "node_modules" / "a.png"
    p.parent.mkdir()
    p.write_bytes(b"x")
    assert expand_inputs([p]) == [p.resolve()]

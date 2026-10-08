from pathlib import Path

import pytest

from imgopt_lib.formats import expand_inputs, format_of, subdir_name


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

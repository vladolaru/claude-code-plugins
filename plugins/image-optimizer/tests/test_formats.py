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
    (root / "work" / WORKDIR_MARKER).write_text("")
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


def _git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", "-c", "protocol.file.allow=always", *args],
                   cwd=cwd, check=True, capture_output=True)


def test_an_image_in_a_submodule_does_not_turn_the_ignore_filter_off(tmp_path):
    # git check-ignore fails the whole batch (exit 128) when one path is inside a submodule
    lib = tmp_path / "lib"
    (lib / "gen").mkdir(parents=True)
    (lib / "a.png").write_bytes(b"x")
    (lib / "gen" / "y.png").write_bytes(b"x")
    (lib / ".gitignore").write_text("gen/\n")
    _git("init", "-q", cwd=lib)
    _git("add", "a.png", ".gitignore", cwd=lib)
    _git("commit", "-q", "-m", "lib", cwd=lib)
    site = tmp_path / "site"
    (site / "build").mkdir(parents=True)
    (site / "img.png").write_bytes(b"x")
    (site / "build" / "x.png").write_bytes(b"x")
    (site / ".gitignore").write_text("build/\n")
    _git("init", "-q", cwd=site)
    _git("submodule", "add", "-q", str(lib), "sub", cwd=site)
    (site / "sub" / "gen").mkdir()
    (site / "sub" / "gen" / "y.png").write_bytes(b"x")
    skipped: list[str] = []
    found = [p.relative_to(site.resolve()).as_posix() for p in expand_inputs([site], skipped=skipped)]
    assert found == ["img.png", "sub/a.png"], skipped
    assert sum("git-ignored" in s for s in skipped) == 2


def test_a_folder_holding_repositories_filters_each_by_its_own_ignores(tmp_path):
    sites = tmp_path / "sites"  # not a repository itself
    for name in ("r1", "r2"):
        (sites / name / "dist").mkdir(parents=True)
        (sites / name / "a.png").write_bytes(b"x")
        (sites / name / "dist" / "built.png").write_bytes(b"x")
        _git("init", "-q", cwd=sites / name)
    (sites / "r1" / ".gitignore").write_text("dist/\n")
    (sites / "loose.png").write_bytes(b"x")
    skipped: list[str] = []
    found = [p.relative_to(sites.resolve()).as_posix() for p in expand_inputs([sites], skipped=skipped)]
    assert found == ["loose.png", "r1/a.png", "r2/a.png", "r2/dist/built.png"], skipped


def test_a_symlinked_image_pointing_outside_the_repository_is_walked(tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "link.png").symlink_to(outside)
    assert [p.name for p in expand_inputs([repo])] == ["outside.png"]  # inputs are resolved


def test_a_vendored_folder_is_reported_once_and_not_walked(tmp_path):
    for name in ("a.png", "b.png", "c.png"):
        p = tmp_path / "node_modules" / "pkg" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    (tmp_path / "keep.png").write_bytes(b"x")
    skipped: list[str] = []
    assert [p.name for p in expand_inputs([tmp_path], skipped=skipped)] == ["keep.png"]
    assert len(skipped) == 1 and "node_modules" in skipped[0]


def test_a_project_folder_holding_run_or_metrics_json_is_walked(tmp_path):
    for rel in ("data/run.json", "data/a.png", "stats/sub/b.png", "stats/metrics.json"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    skipped: list[str] = []
    assert [p.name for p in expand_inputs([tmp_path], skipped=skipped)] == ["a.png", "b.png"]
    assert skipped == []


def test_an_explicit_file_is_never_skipped(tmp_path):
    p = tmp_path / "node_modules" / "a.png"
    p.parent.mkdir()
    p.write_bytes(b"x")
    assert expand_inputs([p]) == [p.resolve()]

import json

import pytest

from imgopt_lib import cli
from imgopt_lib import workdirs as W
from imgopt_lib.ladder import UsageError


def test_cache_root_honours_imgopt_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("IMGOPT_CACHE", str(tmp_path / "c"))
    assert W.cache_root() == tmp_path / "c"


def test_workdir_is_stable_and_slugged(monkeypatch, tmp_path):
    monkeypatch.setenv("IMGOPT_CACHE", str(tmp_path / "c"))
    a = W.workdir("WooCommerce #69539 icons")
    assert a == tmp_path / "c" / "work" / "woocommerce-69539-icons" and a.is_dir()
    assert W.workdir("WooCommerce #69539 icons") == a


def test_a_task_name_without_letters_or_digits_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("IMGOPT_CACHE", str(tmp_path / "c"))
    with pytest.raises(UsageError, match="at least one letter or digit"):
        W.workdir("###")


def test_workdir_falls_back_to_tmpdir_when_the_cache_is_not_writable(monkeypatch, tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    monkeypatch.setenv("IMGOPT_CACHE", str(ro / "c"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "t"))
    (tmp_path / "t").mkdir()
    try:
        assert W.workdir("task") == tmp_path / "t" / "image-optimization" / "task"
    finally:
        ro.chmod(0o700)


def test_clean_refuses_a_folder_that_is_not_a_working_folder(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "keep.txt").write_text("mine")
    with pytest.raises(UsageError, match="not an imgopt working folder"):
        W.clean(tmp_path / "x", keep_picks=False)
    assert (tmp_path / "x" / "keep.txt").exists()


def test_clean_keep_picks_leaves_records_sources_and_picks(tmp_path):
    out = tmp_path / "out"
    f = out / "a--1"
    f.mkdir(parents=True)
    (out / "run.json").write_text("{}")
    (out / ".gitignore").write_text("*\n")
    for name in ("source.png", "reference.png", "pixels.png", "oxipng.png", "pngquant-q80-95.png"):
        (f / name).write_bytes(b"x" * 100)
    (f / "metrics.json").write_text(json.dumps({"pick": "oxipng.png", "candidates": []}))
    freed = W.clean(out, keep_picks=True)
    assert sorted(p.name for p in f.iterdir()) == ["metrics.json", "oxipng.png", "reference.png", "source.png"]
    assert sorted(p.name for p in out.iterdir()) == [".gitignore", "a--1", "run.json"]
    assert freed == 200
    W.clean(out, keep_picks=False)
    assert not out.exists()


def test_the_cli_prints_the_workdir_and_cleans_it(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IMGOPT_CACHE", str(tmp_path / "c"))
    assert cli.main(["workdir", "icons"]) == 0
    folder = tmp_path / "c" / "work" / "icons"
    assert capsys.readouterr().out.strip() == str(folder)
    (folder / "run.json").write_text("{}")
    assert cli.main(["clean", str(folder)]) == 0
    assert "Freed" in capsys.readouterr().out and not folder.exists()

from pathlib import Path

import pytest

from imgopt_lib import selection as SEL


def rows_for(*paths):
    return [(Path(f"/w/{i}"), {"source": {"path": str(p)}}) for i, p in enumerate(paths)]


def test_an_absolute_path_selects_its_file(tmp_path):
    rows = rows_for(tmp_path / "a" / "x.png", tmp_path / "b" / "x.png")
    picked, problems = SEL.resolve([str(tmp_path / "b" / "x.png")], rows)
    assert [r["source"]["path"] for _, r in picked] == [str(tmp_path / "b" / "x.png")] and problems == []


def test_a_relative_path_is_read_from_the_current_folder(tmp_path):
    rows = rows_for(tmp_path / "a" / "x.png", tmp_path / "b" / "x.png")
    picked, problems = SEL.resolve(["a/x.png"], rows, cwd=tmp_path)
    assert [r["source"]["path"] for _, r in picked] == [str(tmp_path / "a" / "x.png")] and problems == []


def test_a_unique_bare_name_selects_its_file(tmp_path):
    rows = rows_for(tmp_path / "a" / "x.png", tmp_path / "b" / "y.png")
    picked, _ = SEL.resolve(["y.png"], rows, cwd=tmp_path / "elsewhere")
    assert [r["source"]["path"] for _, r in picked] == [str(tmp_path / "b" / "y.png")]


def test_an_ambiguous_bare_name_is_refused_with_every_match_listed(tmp_path):
    rows = rows_for(tmp_path / "a" / "x.png", tmp_path / "b" / "x.png")
    picked, [problem] = SEL.resolve(["x.png"], rows, cwd=tmp_path / "elsewhere")
    assert picked == []
    assert "ambiguous" in problem
    assert str(tmp_path / "a" / "x.png") in problem and str(tmp_path / "b" / "x.png") in problem


def test_a_name_that_matches_nothing_is_a_problem(tmp_path):
    picked, [problem] = SEL.resolve(["zzz.png"], rows_for(tmp_path / "x.png"), cwd=tmp_path)
    assert picked == [] and "matches no file" in problem


def test_read_list_takes_non_empty_lines(tmp_path):
    listing = tmp_path / "set.txt"
    listing.write_text("a.png\n\n  b.png  \n")
    assert SEL.read_list(str(listing)) == ["a.png", "b.png"]


@pytest.mark.parametrize("argv", [
    ["candidates", "--files", "{missing}", "--out", "{out}"],
    ["apply", "{out}", "--files", "{missing}"],
    ["record", "--verdicts", "{missing}"],
])
def test_a_missing_list_or_verdicts_file_is_a_usage_error(tmp_path, capsys, argv):
    from imgopt_lib import cli

    values = {"missing": str(tmp_path / "gone.txt"), "out": str(tmp_path / "out")}
    assert cli.main([a.format(**values) for a in argv]) == 2
    assert f"{tmp_path / 'gone.txt'}: no such file" in capsys.readouterr().err

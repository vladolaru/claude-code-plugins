import json
import os
import shutil
from pathlib import Path

import pytest

from imgopt_lib import candidates as C
from imgopt_lib import review as RV
from imgopt_lib import store as ST
from imgopt_lib.ladder import UsageError


def fake_record(tmp_path, *, name="a.png", folder_name="a--1"):
    """A record of a 20,000 B source with four measured variants: 9,000 B fails the gates, 12,000 B is the pick,
    15,000 and 18,000 B pass."""
    folder = tmp_path / "out" / folder_name
    folder.mkdir(parents=True)
    src = tmp_path / "src" / name
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(b"s" * 20_000)
    cands = []
    for size, passed in ((9000, False), (12000, True), (15000, True), (18000, True)):
        label = f"v{size}"
        (folder / f"{label}.png").write_bytes(b"v" * size)
        cands.append({"key": f"k{size}", "label": label, "file": f"{label}.png", "kind": "lossy", "size": size,
                      "ssim": 0.99, "ss2": 85.0, "band": 1.0, "pass": passed,
                      "reason": "" if passed else "banding 4.0 > 3"})
    record = {"schema": 1, "source": {"path": str(src), "sha256": C.sha256(src), "size": 20_000, "format": "png"},
              "target": str(src), "resize": None, "format": "png", "candidates": cands, "pick": "v12000.png",
              "verdict": "apply", "verdict_reason": "x", "min_saving": 0}
    C.write_record(folder, record)
    return folder, record


def test_milder_takes_the_next_larger_passing_variant(tmp_path):
    folder, record = fake_record(tmp_path)
    assert RV.choose(folder, record, "milder")["label"] == "v15000"
    saved = json.loads((folder / "metrics.json").read_text())
    assert saved["choice"] == "v15000.png" and C.choice_of(saved)["label"] == "v15000"


def test_stronger_may_take_a_gate_failing_variant(tmp_path):
    folder, record = fake_record(tmp_path)
    chosen = RV.choose(folder, record, "stronger")
    assert chosen["label"] == "v9000" and chosen["pass"] is False


def test_a_label_and_default(tmp_path):
    folder, record = fake_record(tmp_path)
    RV.choose(folder, record, "v18000")
    assert C.choice_of(record)["label"] == "v18000"
    RV.choose(folder, record, "default")
    assert "choice" not in record and C.choice_of(record)["label"] == "v12000"


def test_nothing_milder_is_a_usage_error(tmp_path):
    folder, record = fake_record(tmp_path)
    RV.choose(folder, record, "v18000")
    with pytest.raises(UsageError, match="no passing variant is milder"):
        RV.choose(folder, record, "milder")


def test_the_verdict_follows_the_choice(tmp_path):
    folder, record = fake_record(tmp_path)
    RV.choose(folder, record, "v15000")
    assert record["verdict"] == "apply"  # saves 5,000 B
    record["min_saving"] = 6000
    RV.choose(folder, record, "v15000")
    assert record["verdict"] == "apply"  # 5,000 B is below --min-saving 6000, but the choice was made by hand
    assert "chosen by hand" in record["verdict_reason"]
    record["min_saving"] = 9000
    RV.choose(folder, record, "default")
    assert record["verdict"] == "untouched"  # the gates' own pick saves 8,000 B, below the floor


def test_a_missing_file_comes_back_from_the_store(tmp_path):
    folder, record = fake_record(tmp_path)
    for c in record["candidates"]:
        ST.put(c, folder, source_sha=record["source"]["sha256"], ref_sha=record["source"]["sha256"])
    (folder / "v15000.png").unlink()
    RV.choose(folder, record, "v15000")
    assert (folder / "v15000.png").is_file()


def test_a_file_gone_from_the_store_too_names_the_rerun(tmp_path):
    folder, record = fake_record(tmp_path)
    (folder / "v15000.png").unlink()
    with pytest.raises(UsageError, match="run candidates again"):
        RV.choose(folder, record, "v15000")


def test_variant_lines_mark_the_default_and_the_choice(tmp_path):
    folder, record = fake_record(tmp_path)
    RV.choose(folder, record, "v15000")
    text = "\n".join(RV.variant_lines(folder, record))
    assert "v12000" in text and "default" in text and "choice" in text and "fails: banding 4.0 > 3" in text


def block(fp, files):
    return RV.BLOCK_HEADER + "\n" + json.dumps({"fingerprint": fp, "files": files})


def test_a_block_from_another_page_is_refused(tmp_path):
    folder, record = fake_record(tmp_path)
    with pytest.raises(UsageError, match="built from other results"):
        RV.record_verdicts([folder.parent], block("0" * 16, {}))


def test_keep_with_another_variant_sets_the_choice_and_is_stored(tmp_path):
    folder, record = fake_record(tmp_path)
    rows = RV.rows_of([folder.parent])
    path = record["source"]["path"]
    counts = RV.record_verdicts([folder.parent], block(RV.fingerprint(rows), {
        path: {"verdict": "keep", "variant": "v15000", "note": "the pick looked soft"}}))
    assert counts == {"keep": 1}
    [(_, fresh)] = RV.rows_of([folder.parent])
    assert C.choice_of(fresh)["label"] == "v15000"
    assert RV.verdict_of(folder.parent, fresh)["note"] == "the pick looked soft"


@pytest.mark.parametrize("how, stepped", [("stronger", "v12000"), ("milder", "v18000")])
def test_milder_or_stronger_steps_from_the_variant_shown_and_reject_ignores_it(tmp_path, how, stepped):
    folder, record = fake_record(tmp_path)
    folder2, record2 = fake_record(tmp_path, name="b.png", folder_name="b--1")
    a, b = record["source"]["path"], record2["source"]["path"]
    RV.record_verdicts([folder.parent], block(RV.fingerprint(RV.rows_of([folder.parent])), {
        a: {"verdict": how, "variant": "v15000"}, b: {"verdict": "reject", "variant": "v18000"}}))
    fresh = {r["source"]["path"]: (f, r) for f, r in RV.rows_of([folder.parent])}
    folder, shown = fresh[a]
    assert C.choice_of(shown)["label"] == "v15000"
    assert RV.verdict_of(folder.parent, shown) == {"verdict": how, "variant": "v15000", "key": "k15000", "note": "",
                                                    "sha256": shown["source"]["sha256"]}
    assert RV.choose(folder, shown, how)["label"] == stepped  # one step from what the human saw, not the pick
    _, rejected = fresh[b]
    assert C.choice_of(rejected)["label"] == "v12000" and RV.verdict_of(folder.parent, rejected)["verdict"] == "reject"


def test_a_stored_verdict_stops_counting_once_the_choice_changes(tmp_path):
    folder, record = fake_record(tmp_path)
    path = record["source"]["path"]
    RV.record_verdicts([folder.parent], block(RV.fingerprint(RV.rows_of([folder.parent])), {
        path: {"verdict": "keep", "variant": "v12000", "note": ""}}))
    RV.choose(folder, record, "v18000")
    assert RV.verdict_of(folder.parent, record) is None


def test_a_path_not_on_the_page_and_a_bad_verdict_are_refused(tmp_path):
    folder, record = fake_record(tmp_path)
    fp = RV.fingerprint(RV.rows_of([folder.parent]))
    with pytest.raises(UsageError, match="not on this page"):
        RV.record_verdicts([folder.parent], block(fp, {"/elsewhere/x.png": {"verdict": "keep", "variant": "v12000"}}))
    with pytest.raises(UsageError, match="verdict must be one of"):
        RV.record_verdicts([folder.parent], block(fp, {
            record["source"]["path"]: {"verdict": "maybe", "variant": "v12000"}}))


def test_text_around_the_block_is_ignored_and_a_cut_block_is_named():
    with pytest.raises(UsageError, match="not complete JSON"):
        RV.parse_block("Here you go:\n" + RV.BLOCK_HEADER + '\n{"fingerprint": "ab", "files": {')
    assert RV.parse_block("x\n" + block("ab", {}) + "\nthanks")["fingerprint"] == "ab"


def test_a_bad_keep_midway_changes_nothing_and_the_block_stays_valid(tmp_path):
    folder, record = fake_record(tmp_path)
    folder2, record2 = fake_record(tmp_path, name="b.png", folder_name="b--1")
    fp = RV.fingerprint(RV.rows_of([folder.parent]))
    files = {record["source"]["path"]: {"verdict": "keep", "variant": "v15000"},
             record2["source"]["path"]: {"verdict": "keep", "variant": "v99999"}}
    with pytest.raises(UsageError, match="not a measured variant"):
        RV.record_verdicts([folder.parent], block(fp, files))
    assert C.choice_of(record)["label"] == "v12000"
    assert "choice" not in json.loads((folder / "metrics.json").read_text())
    assert not (folder.parent / RV.VERDICTS_FILE).exists()
    files[record2["source"]["path"]]["variant"] = "v18000"
    assert RV.record_verdicts([folder.parent], block(fp, files)) == {"keep": 2}


def test_the_same_source_in_two_working_folders_is_refused(tmp_path):
    folder, record = fake_record(tmp_path)
    other = tmp_path / "out2"
    shutil.copytree(folder, other / folder.name)
    with pytest.raises(UsageError, match="separate pages") as error:
        RV.rows_of([folder.parent, other])
    assert record["source"]["path"] in str(error.value) and str(other) in str(error.value)


def test_manifest_lists_paths_and_filters_accepted(tmp_path):
    folder, record = fake_record(tmp_path)
    rows = RV.rows_of([folder.parent])
    [row] = RV.manifest_rows(rows)
    assert row["variant"] == str(folder / "v12000.png") and row["exists"] and row["verdict"] is None
    assert RV.manifest_rows(rows, accepted=True) == []
    RV.record_verdicts([folder.parent], RV.BLOCK_HEADER + "\n" + json.dumps(
        {"fingerprint": RV.fingerprint(rows),
         "files": {record["source"]["path"]: {"verdict": "keep", "variant": "v12000"}}}))
    assert [r["label"] for r in RV.manifest_rows(RV.rows_of([folder.parent]), accepted=True)] == ["v12000"]


def test_manifest_command_prints_the_rows_as_json_and_as_text(tmp_path, capsys):
    from imgopt_lib import cli

    folder, record = fake_record(tmp_path)
    assert cli.main(["manifest", str(folder.parent), "--json"]) == 0
    [row] = json.loads(capsys.readouterr().out)
    assert row["source"] == record["source"]["path"] and row["saved"] == 8000
    assert cli.main(["manifest", str(folder.parent)]) == 0
    assert f"{folder / 'v12000.png'} -> {record['target']}" in capsys.readouterr().out  # what to copy, and where


def test_a_conversion_manifest_points_from_the_variant_to_the_new_target(tmp_path, capsys):
    from imgopt_lib import cli

    folder, record = fake_record(tmp_path)
    target = str(Path(record["source"]["path"]).with_suffix(".webp"))
    record.update(target=target, format="webp")
    C.write_record(folder, record)
    assert cli.main(["manifest", str(folder.parent)]) == 0
    out = capsys.readouterr().out
    assert f"{folder / 'v12000.png'} -> {target}" in out and f"-> {record['source']['path']}" not in out


def removing(folder, record, label, kinds):
    next(c for c in record["candidates"] if c["label"] == label)["metadata_removed"] = kinds
    C.write_record(folder, record)


def test_the_metadata_a_choice_removes_shows_in_variants_choose_and_manifest(tmp_path, capsys):
    from imgopt_lib import cli

    folder, record = fake_record(tmp_path)
    removing(folder, record, "v15000", ["exif", "xmp"])
    assert any("v15000" in line and "removes exif, xmp" in line for line in RV.variant_lines(folder, record))
    assert cli.main(["choose", str(folder.parent), "--file", record["source"]["path"], "--label", "v15000"]) == 0
    assert "removes exif, xmp" in capsys.readouterr().out
    [row] = RV.manifest_rows(RV.rows_of([folder.parent]))
    assert row["metadata_removed"] == ["exif", "xmp"]
    assert cli.main(["manifest", str(folder.parent)]) == 0
    assert "removes exif, xmp" in capsys.readouterr().out



def remeasure(folder, record, label, key):
    """What a re-run with other settings leaves: the same label, the same file name, another variant (key)."""
    next(c for c in record["candidates"] if c["label"] == label)["key"] = key
    C.write_record(folder, record)


def test_a_verdict_names_the_variant_by_its_key_not_its_label(tmp_path):
    folder, record = fake_record(tmp_path)
    path = record["source"]["path"]
    RV.record_verdicts([folder.parent], block(RV.fingerprint(RV.rows_of([folder.parent])), {
        path: {"verdict": "keep", "variant": "v15000", "note": ""}}))
    [(_, fresh)] = RV.rows_of([folder.parent])
    assert RV.verdict_of(folder.parent, fresh)["key"] == "k15000"
    remeasure(folder, fresh, "v15000", "k15000-resized")
    [(_, rerun)] = RV.rows_of([folder.parent])
    assert C.choice_of(rerun)["label"] == "v15000" and RV.verdict_of(folder.parent, rerun) is None
    assert not RV.is_accepted(rerun, RV.verdict_of(folder.parent, rerun))


def test_the_fingerprint_follows_the_variants_content(tmp_path):
    folder, record = fake_record(tmp_path)
    before = RV.fingerprint(RV.rows_of([folder.parent]))
    remeasure(folder, record, "v18000", "k18000-other-tools")  # not the choice, but a variant the page offers
    assert RV.fingerprint(RV.rows_of([folder.parent])) != before


def test_a_keep_does_not_follow_its_label_into_a_resized_rerun(factory, toolset, tmp_path, keep):
    from imgopt_lib import gates as G

    tools = {**toolset("recompress", "high", {"jpeg"}), **toolset("prepare", "high", {"jpeg"})}
    src = factory.photo(size=(800, 600), quality=95).resolve()
    out = tmp_path / "F"
    opts = lambda **kw: C.Options(profile="medium", out=out, gates=G.gates_for("medium"), **kw)
    C.run([src], opts(), tools, log=lambda _: None)
    [(_, record)] = C.load_records(out)
    first = C.choice_of(record)
    keep(out, src)
    C.run([src], opts(resize=600), tools, log=lambda _: None)
    [(folder, record)] = C.load_records(out)
    RV.choose(folder, record, first["label"])
    assert C.choice_of(record)["key"] != first["key"] and RV.verdict_of(out, record) is None


def two_folders(tmp_path):
    """Two working folders of one page: a.png in the first, b.png in the second."""
    folder, record = fake_record(tmp_path)
    folder2, record2 = fake_record(tmp_path / "second", name="b.png", folder_name="b--1")
    return [folder.parent, folder2.parent], record["source"]["path"], record2["source"]["path"]


def page_block(dirs, files, folders=None):
    return RV.BLOCK_HEADER + "\n" + json.dumps({"fingerprint": RV.fingerprint(RV.rows_of(dirs)),
                                                "folders": [str(d) for d in (dirs if folders is None else folders)],
                                                "files": files})


def test_record_takes_the_folders_from_the_block_and_refuses_others(tmp_path):
    dirs, a, b = two_folders(tmp_path)
    text = page_block(dirs, {a: {"verdict": "reject", "variant": "v12000"}, b: {"verdict": "reject"}})
    with pytest.raises(UsageError) as error:
        RV.record_verdicts([dirs[0]], text)
    assert all(str(d) in str(error.value) for d in dirs)
    assert RV.record_verdicts(None, text) == {"reject": 2}
    assert (dirs[0] / RV.VERDICTS_FILE).is_file() and (dirs[1] / RV.VERDICTS_FILE).is_file()
    assert RV.record_verdicts(list(reversed(dirs)), text) == {"reject": 2}  # the same folders, in any order


def test_record_cli_names_every_folder_of_the_page(tmp_path, capsys):
    from imgopt_lib import cli

    dirs, a, b = two_folders(tmp_path)
    pasted = tmp_path / "pasted.txt"
    pasted.write_text(page_block(dirs, {a: {"verdict": "milder", "variant": "v12000"},
                                        b: {"verdict": "keep", "variant": "v12000"}}))
    assert cli.main(["record", "--verdicts", str(pasted)]) == 0
    out = capsys.readouterr().out
    both = f"{dirs[0]} {dirs[1]}"
    assert f"choose {both} --file {a} --milder" in out
    assert f"sheet {both}" in out and f"apply {both}" in out


def test_choose_cli_rebuilds_the_page_of_every_folder(tmp_path, capsys):
    from imgopt_lib import cli

    dirs, a, _ = two_folders(tmp_path)
    assert cli.main(["choose", *map(str, dirs), "--file", a, "--label", "v15000"]) == 0
    out = capsys.readouterr().out
    assert f"sheet {dirs[0]} {dirs[1]}" in out
    assert "now v15000 (14.6 KB), passes the gates; status: apply (saves 5000 B (25%))" in out
    assert "verdict:" not in out  # the human's verdicts are keep, reject, milder and stronger


def test_record_cli_warns_of_a_keep_switched_to_a_variant_without_crops(tmp_path, capsys):
    from imgopt_lib import cli

    dirs, a, b = two_folders(tmp_path)
    pasted = tmp_path / "pasted.txt"
    pasted.write_text(page_block(dirs, {a: {"verdict": "keep", "variant": "v15000"},  # switched on the page
                                        b: {"verdict": "keep", "variant": "v12000"}}))  # the page's choice
    assert cli.main(["record", "--verdicts", str(pasted)]) == 0
    out = capsys.readouterr().out
    assert f"kept without its 1:1 crops: {a} (v15000)" in out and b not in out.split("kept without")[-1]
    assert f"view them before placing: python3 {cli.SCRIPT} sheet {dirs[0]} {dirs[1]}" in out


def test_a_keep_must_name_its_variant(tmp_path):
    folder, record = fake_record(tmp_path)
    fp = RV.fingerprint(RV.rows_of([folder.parent]))
    with pytest.raises(UsageError, match="a keep must name the variant"):
        RV.record_verdicts([folder.parent], block(fp, {record["source"]["path"]: {"verdict": "keep", "variant": None}}))


def test_a_kept_pick_under_the_floor_is_written(tmp_path):
    folder, record = fake_record(tmp_path)
    record.update(min_saving=9000, verdict="untouched", verdict_reason="saving 8000 B is below the floor")
    C.write_record(folder, record)
    RV.record_verdicts([folder.parent], block(RV.fingerprint(RV.rows_of([folder.parent])), {
        record["source"]["path"]: {"verdict": "keep", "variant": "v12000"}}))
    [(_, kept)] = RV.rows_of([folder.parent])
    assert kept["verdict"] == "apply" and RV.is_accepted(kept, RV.verdict_of(folder.parent, kept))


def test_choosing_the_pick_by_hand_is_stored_as_a_choice(tmp_path):
    folder, record = fake_record(tmp_path)
    record["min_saving"] = 9000
    RV.choose(folder, record, "v12000")  # the gates' own pick, under the floor
    saved = json.loads((folder / "metrics.json").read_text())
    assert saved["choice"] == saved["pick"] == "v12000.png"  # a re-run carries the by-hand decision forward
    assert saved["verdict"] == "apply" and "chosen by hand" in saved["verdict_reason"]


def test_record_cli_warns_of_a_kept_pick_the_page_built_no_crops_for(tmp_path, capsys):
    from imgopt_lib import cli

    folder, record = fake_record(tmp_path)
    record.update(min_saving=9000, verdict="untouched", verdict_reason="saving 8000 B is below the floor")
    C.write_record(folder, record)  # untouched when the page was built, so it has no crops
    path = record["source"]["path"]
    pasted = tmp_path / "pasted.txt"
    pasted.write_text(page_block([folder.parent], {path: {"verdict": "keep", "variant": "v12000"}}))
    assert cli.main(["record", "--verdicts", str(pasted)]) == 0
    out = capsys.readouterr().out
    assert f"kept without its 1:1 crops: {path} (v12000)" in out
    assert f"view them before placing: python3 {cli.SCRIPT} sheet {folder.parent}" in out


def with_a_larger_variant(tmp_path):
    """fake_record plus a 25,000 B variant: larger than its 20,000 B source, so keeping it writes nothing."""
    folder, record = fake_record(tmp_path)
    (folder / "v25000.png").write_bytes(b"v" * 25000)
    record["candidates"].append({**record["candidates"][-1], "key": "k25000", "label": "v25000",
                                 "file": "v25000.png", "size": 25000})
    C.write_record(folder, record)
    return folder, record


def test_record_and_apply_name_a_keep_that_writes_nothing(tmp_path, capsys):
    from imgopt_lib import applying as AP
    from imgopt_lib import cli

    folder, record = with_a_larger_variant(tmp_path)
    pasted = tmp_path / "pasted.txt"
    pasted.write_text(page_block([folder.parent], {record["source"]["path"]: {"verdict": "keep", "variant": "v25000"}}))
    assert cli.main(["record", "--verdicts", str(pasted)]) == 0
    assert f"kept, but nothing to write: {record['source']['path']}" in capsys.readouterr().out
    lines = []
    assert AP.apply([folder.parent], tools={}, log=lines.append) == 0
    assert any(f"kept, but nothing to write: {record['source']['path']}" in line for line in lines), lines


def test_a_corrupt_verdicts_file_is_refused_not_treated_as_empty(tmp_path):
    folder, record = fake_record(tmp_path)
    (folder.parent / RV.VERDICTS_FILE).write_text('{"files": {"/a.png": {"verdict": "keep"')  # cut short
    with pytest.raises(UsageError, match="holds the human's verdicts"):
        RV.verdict_of(folder.parent, record)
    with pytest.raises(UsageError, match="holds the human's verdicts"):
        RV.record_verdicts([folder.parent], block(RV.fingerprint(RV.rows_of([folder.parent])), {
            record["source"]["path"]: {"verdict": "reject"}}))


def test_a_corrupt_verdicts_file_refuses_before_any_choice_changes(tmp_path):
    folder, record = fake_record(tmp_path)
    fp = RV.fingerprint(RV.rows_of([folder.parent]))
    text = block(fp, {record["source"]["path"]: {"verdict": "keep", "variant": "v15000"}})
    (folder.parent / RV.VERDICTS_FILE).write_text("not json")
    with pytest.raises(UsageError, match="holds the human's verdicts"):
        RV.record_verdicts([folder.parent], text)
    assert "choice" not in json.loads((folder / "metrics.json").read_text())  # the page's results are as they were
    (folder.parent / RV.VERDICTS_FILE).unlink()  # repaired by hand: the same block is still good
    assert RV.record_verdicts([folder.parent], text) == {"keep": 1}


def test_verdicts_are_written_whole_or_not_at_all(tmp_path, monkeypatch):
    folder, record = fake_record(tmp_path)
    book = folder.parent / RV.VERDICTS_FILE
    book.write_text('{"files": {}}')

    def fails(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fails)
    with pytest.raises(OSError, match="disk full"):
        RV.record_verdicts([folder.parent], block(RV.fingerprint(RV.rows_of([folder.parent])), {
            record["source"]["path"]: {"verdict": "reject"}}))
    assert book.read_text() == '{"files": {}}'


def test_choose_takes_one_way_of_naming_the_variant(tmp_path, capsys):
    from imgopt_lib import cli

    folder, record = fake_record(tmp_path)
    with pytest.raises(SystemExit) as error:
        cli.main(["choose", str(folder.parent), "--file", record["source"]["path"], "--label", "v15000", "--milder"])
    assert error.value.code == 2 and "not allowed with argument" in capsys.readouterr().err
    assert "choice" not in json.loads((folder / "metrics.json").read_text())

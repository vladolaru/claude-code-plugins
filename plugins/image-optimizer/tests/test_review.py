import json
import shutil

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
    assert record["verdict"] == "untouched"  # 5,000 B is below --min-saving 6000


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
    assert f"{record['source']['path']} -> {folder / 'v12000.png'}" in capsys.readouterr().out



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
    assert f"sheet {dirs[0]} {dirs[1]}" in capsys.readouterr().out


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

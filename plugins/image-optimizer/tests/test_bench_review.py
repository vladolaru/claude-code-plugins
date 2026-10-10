import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

from imgbench import cli, harness, manifest, paths  # noqa: E402
from imgbench import report as R  # noqa: E402
from imgbench import review as RV  # noqa: E402
from imgopt_lib import candidates as C  # noqa: E402
from imgopt_lib.ladder import UsageError  # noqa: E402


def make_row(i, category="c", job="recompress-high", **extra):
    return {"job": job, "category": category, "file": f"f{i}", "verdict": "apply", "pick": "x", "pick_kind": "lossy",
            "pick_scores": {"ssim": 0.98 + i / 1000, "ss2": 90}, "gates": {"ssim": 0.98, "ss2": 80}, **extra}


def test_sample_takes_the_picks_closest_to_a_floor():
    rows = [make_row(i) for i in range(10)]
    picked = RV.sample(rows, per_category=3, seed=1)
    assert [r["file"] for r in picked[:3]] == ["f0", "f1", "f2"] and len(picked) == 4
    assert picked[3]["file"] not in {"f0", "f1", "f2"}
    assert RV.sample(rows, per_category=3, seed=1) == picked


def test_sample_works_within_each_job_and_category():
    rows = [make_row(i, category=c, job=j) for i in range(5) for c in ("a", "b") for j in ("recompress-high", "x")]
    picked = RV.sample(rows, per_category=2, seed=1)
    assert len(picked) == 3 * 2 * 2
    for job in ("recompress-high", "x"):
        for category in ("a", "b"):
            assert [r["file"] for r in picked if (r["job"], r["category"]) == (job, category)][:2] == ["f0", "f1"]


def test_sample_leaves_out_lossless_identical_untouched_and_pickless_rows():
    identical = {"ssim": 1.0, "ss2": 100.0, "band": 0.0, "identical": True}  # oxipng on resized pixels: kind lossy
    rows = [make_row(0, pick_kind="lossless"), make_row(1, verdict="untouched"), make_row(2, pick=None),
            make_row(3), make_row(4), make_row(5, pick_scores=identical)]
    assert sorted(r["file"] for r in RV.sample(rows, per_category=5)) == ["f3", "f4"]


def test_sample_ranks_a_missing_score_last():
    rows = [make_row(0, pick_scores={}), make_row(1)]
    assert [r["file"] for r in RV.sample(rows, per_category=2)] == ["f1", "f0"]


def test_record_marks_unapproved_files_unacceptable(tmp_path):
    (tmp_path / "sample.json").write_text('[{"file": "/a/x.jpg"}, {"file": "/a/y.jpg"}]')
    out = RV.record(tmp_path, approved=["/a/x.jpg"])
    v = {r["file"]: r["acceptable"] for r in json.loads(out.read_text())}
    assert v == {"/a/x.jpg": True, "/a/y.jpg": False}


def test_record_refuses_a_path_outside_the_sample(tmp_path):
    (tmp_path / "sample.json").write_text('[{"file": "/a/x.jpg"}]')
    with pytest.raises(UsageError, match="/a/other.jpg"):
        RV.record(tmp_path, approved=["/a/other.jpg"])
    assert not (tmp_path / "verdicts.json").exists()


def test_approved_from_takes_a_bare_list_or_the_pages_whole_command():
    assert RV.approved_from("/a/x.jpg,/a/y.jpg") == ["/a/x.jpg", "/a/y.jpg"]
    assert RV.approved_from("") == []
    assert RV.approved_from("/a/it's here/x.jpg,/a/y.jpg") == ["/a/it's here/x.jpg", "/a/y.jpg"]
    command = "python3 '/p/my scripts/imgopt.py' apply /r/out --approve '/a/it'\\''s here/x.jpg,/a/y.jpg'"
    assert RV.approved_from(command) == ["/a/it's here/x.jpg", "/a/y.jpg"]
    assert RV.approved_from("python3 imgopt.py apply /r/out --approve=/a/x.jpg") == ["/a/x.jpg"]


def tiny_run(factory, toolset, tmp_path):
    """A real harness run of the recompress-high job over two photos, small enough to be quick."""
    toolset("recompress", "high", {"jpeg"})
    corpus = tmp_path / "corpus"
    entries = []
    for name in ("a.jpg", "b.jpg"):
        src = factory.photo(name, size=(320, 240), quality=97)
        dest = corpus / "photo-small" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        entries.append(manifest.entry_for(dest, corpus, category="photo-small", origin="test", transform="",
                                          license="CC0"))
    manifest.write(entries, corpus)
    job = harness.Job("recompress-high", ("photo-small",), "high")
    return harness.run(corpus, tmp_path / "run", [job], say=lambda _: None)


def assemble_and_check(run_dir, tmp_path):
    rows = [json.loads(line) for line in (run_dir / "rows.jsonl").read_text().splitlines()]
    sampled = RV.sample(rows, per_category=2, seed=1)
    if not sampled:
        pytest.skip("no lossy pick on the tiny photos with this toolset")
    dest = RV.assemble(run_dir, sampled, tmp_path / "review" / "recompress-high")
    assert {r["source"]["path"] for _, r in C.load_records(dest)} == {r["file"] for r in sampled}
    page = RV.page_of(dest)
    text = page.read_text()
    assert page.is_file() and all(r["file"] in text for r in sampled)
    assert "imgopt.py" in text and "--approve" in text
    assert [s["file"] for s in json.loads((dest / "sample.json").read_text())] == [r["file"] for r in sampled]
    assert json.loads((dest / "run.json").read_text())["profile"] == "high"


def test_assemble_refuses_rows_of_two_jobs_and_names_a_missing_record(tmp_path):
    with pytest.raises(UsageError, match="one job"):
        RV.assemble(tmp_path, [make_row(0), make_row(1, job="lossless")], tmp_path / "d")
    with pytest.raises(UsageError, match="no record folder"):
        RV.assemble(tmp_path, [make_row(0, file=str(tmp_path / "gone.jpg"))], tmp_path / "d")


def check_cli(run_dir, capsys):
    assert cli.main(["review", str(run_dir), "--per-category", "2"]) == 0
    out = capsys.readouterr().out
    dest = paths.bench_root() / "review" / run_dir.name / "recompress-high"
    assert str(RV.page_of(dest)) in out and "Do not run it" in out
    sampled = json.loads((dest / "sample.json").read_text())
    first = sampled[0]["file"]
    pasted = f"python3 /x/imgopt.py apply {dest} --approve '{first}'"
    assert cli.main(["review-record", str(dest), "--approve", pasted]) == 0
    verdicts = {v["file"]: v["acceptable"] for v in json.loads((dest / "verdicts.json").read_text())}
    assert verdicts[first] is True and not any(v for f, v in verdicts.items() if f != first)
    assert cli.main(["review", str(run_dir)]) == 2  # a rebuild would erase the verdicts
    assert "verdicts" in capsys.readouterr().out


def test_a_real_run_is_sampled_assembled_reviewed_and_recorded(factory, toolset, tmp_path, capsys):
    run_dir = tiny_run(factory, toolset, tmp_path)
    assemble_and_check(run_dir, tmp_path)
    check_cli(run_dir, capsys)


def test_review_record_needs_a_review_folder(tmp_path, capsys):
    assert cli.main(["review-record", str(tmp_path), "--approve", "x"]) == 2
    assert cli.main(["review", str(tmp_path)]) == 2


def test_sample_ranks_by_the_report_floor_margin_evidence_ssim_included():
    near = make_row(9, pick_scores={"ssim": 0.99, "ss2": 95, "ssim_evidence": 0.9801})  # gate SSIM far, Evidence near
    rows = [make_row(i) for i in range(5, 9)] + [near]
    picked = RV.sample(rows, per_category=1)
    assert picked[0]["file"] == "f9"
    sampled = RV._slim(picked[0])
    assert sampled["distance"] == pytest.approx(R.floor_margin(near))


def test_job_settings_come_from_the_run_then_from_the_job_table(tmp_path):
    job = next(j for j in harness.JOBS if j.name == "prepare-catalog")
    assert RV.job_settings(tmp_path, "prepare-catalog") == (job.profile, job.resize, job.out_format)  # no timing
    (tmp_path / "timing.json").write_text(json.dumps({"jobs": {"retired-job": {
        "profile": "medium", "resize": 800, "format": "webp", "missing_optional": []}}}))
    assert RV.job_settings(tmp_path, "retired-job") == ("medium", 800, "webp")  # the run's own record wins
    with pytest.raises(UsageError, match="not-a-job"):
        RV.job_settings(tmp_path, "not-a-job")

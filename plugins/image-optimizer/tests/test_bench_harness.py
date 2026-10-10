import hashlib
import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

from imgbench import cli, harness, manifest, report  # noqa: E402
from imgopt_lib import tools as T  # noqa: E402

FIELDS = {"job", "category", "profile", "file", "source_size", "in_place", "verdict", "pick", "pick_family",
          "pick_scores", "gates", "notes", "candidates", "seconds", "disk"}


def tiny_corpus(factory, tmp_path):
    corpus = tmp_path / "corpus"
    files = [factory.photo("a.jpg", size=(96, 64)), factory.gradient("b.png", size=(96, 32)),
             factory.truncated_jpeg("cut.jpg")]
    entries = []
    for src in files:
        dest = corpus / "photo-small" / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        entries.append(manifest.entry_for(dest, corpus, category="photo-small", origin="test", transform="",
                                          license="CC0"))
    manifest.write(entries, corpus)
    return corpus


def test_run_writes_rows_prunes_to_picks_and_records_skips(factory, toolset, tmp_path):
    toolset("recompress", "lossless", {"jpeg", "png"})  # skips the test when a lossless tool is missing
    corpus = tiny_corpus(factory, tmp_path)
    job = harness.Job("lossless", ("photo-small",), "lossless", None, "keep")
    run_dir = tmp_path / "run"
    messages = []
    assert harness.run(corpus, run_dir, [job], jobs_parallel=1, say=messages.append) == run_dir

    rows = [json.loads(line) for line in (run_dir / "rows.jsonl").read_text().splitlines()]
    assert sorted(r["file"].rsplit("/", 1)[-1] for r in rows) == ["a.jpg", "b.png", "cut.jpg"]
    by_name = {r["file"].rsplit("/", 1)[-1]: r for r in rows}
    done = by_name["a.jpg"]
    assert FIELDS <= set(done) and done["in_place"] is True and done["job"] == "lossless"
    assert done["category"] == "photo-small" and done["profile"] == "lossless"
    assert done["pick_family"] == harness.family_of(done["pick"])
    # a lossless pick keeps every pixel; the record says so, and whether its banding was gated
    assert done["pick_scores"]["identical"] is True and done["pick_scores"]["band_gated"] is False
    assert all({"label", "family", "size", "pass", "ssim", "ss2", "band"} <= set(c) for c in done["candidates"])
    assert done["disk"] > 0 and done["seconds"] >= 0
    assert by_name["cut.jpg"]["verdict"] == "skipped" and by_name["cut.jpg"]["reason"]

    out = run_dir / "lossless" / "photo-small"
    assert (out / "log.txt").is_file() and "skipped" in (out / "log.txt").read_text()
    for folder in out.glob("*/metrics.json"):
        record = json.loads(folder.read_text())
        kept = {p.name for p in folder.parent.iterdir()}
        allowed = {"metrics.json", "reference.png", record["pick"]}
        assert {n for n in kept if not n.startswith("source.")} <= allowed
    timing = json.loads((run_dir / "timing.json").read_text())
    assert timing["tools"].startswith("tools: ") and "lossless/photo-small" in timing["categories"]
    assert timing["jobs"]["lossless"] == {"profile": "lossless", "resize": None, "format": "keep",
                                          "missing_optional": []}
    assert timing["corpus_sha256"] == hashlib.sha256((corpus / "corpus.json").read_bytes()).hexdigest()


def test_job_kinds_and_categories():
    kinds = {j.name: j.kind for j in harness.JOBS}
    assert kinds == {"recompress-high": "recompress", "recompress-medium": "recompress", "lossless": "recompress",
                     "prepare-catalog": "prepare", "convert-webp": "convert", "convert-jpeg": "convert",
                     "edge-high": "recompress"}
    by_name = {j.name: j for j in harness.JOBS}
    assert "edge" not in by_name["recompress-high"].categories and "edge" in by_name["lossless"].categories
    # the edge files also meet a lossy profile, in a job of their own so it can run after the main run
    assert by_name["edge-high"].categories == ("edge",) and by_name["edge-high"].profile == "high"
    assert by_name["prepare-catalog"].resize == 1200 and by_name["convert-webp"].out_format == "webp"


def test_run_dirs_are_new_each_time(tmp_path):
    first = harness.new_run_dir(tmp_path, "2026-10-09", "abc1234")
    second = harness.new_run_dir(tmp_path, "2026-10-09", "abc1234")
    third = harness.new_run_dir(tmp_path, "2026-10-09", "abc1234")
    assert [p.name for p in (first, second, third)] == ["2026-10-09-abc1234", "2026-10-09-abc1234-2",
                                                        "2026-10-09-abc1234-3"]
    assert all(p.is_dir() for p in (first, second, third))


@pytest.mark.parametrize("args", [["--only-job", "nope"], ["--only-category", "nope"],
                                  ["--only-job", "convert-jpeg", "--only-category", "icon"]])
def test_run_usage_errors_exit_2(args, capsys):
    assert cli.main(["run", *args]) == 2
    assert capsys.readouterr().out


def test_report_command_prints_and_writes_report_md(tmp_path, capsys):
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    (run_dir / "rows.jsonl").write_text(json.dumps(
        {"category": "icon", "job": "lossless", "profile": "lossless", "source_size": 1000, "in_place": True,
         "seconds": 1.0, "disk": 10, "verdict": "apply", "pick": "oxipng", "pick_family": "oxipng",
         "pick_size": 500, "candidates": [{"family": "oxipng", "size": 500, "pass": True}]}) + "\n")
    (run_dir / "timing.json").write_text(json.dumps({"tools": "tools: oxipng 10 (/x)", "commit": "abc"}))
    assert cli.main(["report", str(run_dir)]) == 0
    text = capsys.readouterr().out
    assert "| icon |" in text and "oxipng 10" in text
    assert (run_dir / "report.md").read_text() == text


@pytest.mark.parametrize("target, resize, in_place", [
    ("/c/a.png", None, True), ("/c/a.webp", None, False), ("/c/a.png", 1200, False)])
def test_row_takes_in_place_from_the_record(target, resize, in_place):
    record = {"source": {"path": "/c/a.png", "size": 1000}, "target": target, "resize": resize,
              "verdict": "untouched", "verdict_reason": "", "gates": {}, "notes": [], "candidates": [], "pick": None}
    job = harness.Job("recompress-high", ("icon",), "high")  # a same-format job without resize: in place by the job
    assert harness._row(job, "icon", "icon/a.png", record, 1.0, 0)["in_place"] is in_place


def test_run_checks_every_job_s_tools_before_writing_anything(factory, tmp_path, monkeypatch):
    corpus = tiny_corpus(factory, tmp_path)
    seen = []

    def blocked(*args, **kwargs):
        seen.append((args, kwargs))
        raise T.ToolingError("BLOCKED: cjpegli")
    monkeypatch.setattr(harness.T, "ensure", blocked)
    with pytest.raises(T.ToolingError, match="cjpegli"):
        harness.run(corpus, tmp_path / "refused", [harness.Job("high", ("photo-small",), "high")], say=lambda _: None)
    assert not (tmp_path / "refused").exists()
    assert seen and all("allow_missing" not in kwargs for _, kwargs in seen)  # a run never waives a tool


def test_report_header_names_the_missing_optional_tools(tmp_path):
    (tmp_path / "rows.jsonl").write_text("")
    (tmp_path / "timing.json").write_text(json.dumps({"jobs": {
        "recompress-high": {"missing_optional": ["cjpegli"]}, "lossless": {"missing_optional": []}}}))
    assert "- Optional tools missing: recompress-high: cjpegli" in report.render(tmp_path)
    (tmp_path / "timing.json").write_text(json.dumps({"jobs": {"lossless": {"missing_optional": []}}}))
    assert "- Optional tools missing: none" in report.render(tmp_path)
    (tmp_path / "timing.json").write_text("{}")
    assert "- Optional tools missing: not recorded" in report.render(tmp_path)


def test_report_header_names_the_corpus_checksum_and_the_ledger_skips(tmp_path):
    (tmp_path / "rows.jsonl").write_text("")
    (tmp_path / "timing.json").write_text(json.dumps({"corpus_sha256": "ab12"}))
    text = report.render(tmp_path)
    assert "- Corpus checksum (corpus.json sha256): ab12" in text
    assert "written.jsonl" in text

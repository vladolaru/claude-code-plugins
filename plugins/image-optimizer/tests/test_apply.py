import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from imgopt_lib import applying as AP
from imgopt_lib import candidates as C
from imgopt_lib import gates as G
from imgopt_lib import imaging, metrics
from imgopt_lib.ladder import UsageError

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "imgopt.py"


def run_candidates(src, out, tools, profile="lossless"):
    opts = C.Options(profile=profile, out=out, gates=G.gates_for(profile), waived=("cjpegli",))
    return C.run([src.resolve()], opts, tools, log=lambda _: None)


def staged_files(directory):
    return sorted(p.name for p in Path(directory).glob(".imgopt-*"))


def _lossy_record(out, factory, *, name="photo.jpg", folder_name="f--1", scores=None, quality=60, kind="lossy",
                  target=None, resize=None):
    """A record whose pick is a real, smaller JPEG. ``scores`` are the numbers it claims; the default
    claims nothing a re-measure could match. ``target`` defaults to the source (an in-place job)."""
    folder = out / folder_name
    folder.mkdir(parents=True)
    src = factory.photo(name=name)
    shutil.copyfile(src, folder / "source.jpg")
    Image.open(src).convert("RGBA").save(folder / "reference.png")
    Image.open(src).save(folder / "pick.jpg", "JPEG", quality=quality)
    claimed = scores or {"ssim": 0.5, "ss2": 50.0, "band": 0.0}
    record = {"schema": 1, "source": {"path": str(src), "sha256": C.sha256(src), "size": src.stat().st_size,
                                      "format": "jpeg"},
              "format": "jpeg", "target": str(target or src), "uncalibrated": False, "waived": [], "notes": [],
              "profile": "high", "resize": resize,
              "candidates": [{"label": "jpegoptim-m60", "file": "pick.jpg", "kind": kind,
                              "size": (folder / "pick.jpg").stat().st_size, **claimed, "pass": True}],
              "pick": "pick.jpg", "verdict": "apply", "verdict_reason": "x"}
    (folder / "metrics.json").write_text(json.dumps(record))
    return src, folder


def _measured(folder, tools):
    """The numbers a re-measure of the folder's pick gives, as candidates would have recorded them."""
    ref = Image.open(folder / "reference.png").convert("RGBA")
    s = metrics.measure(ref, imaging.display_pixels(folder / "pick.jpg"), tools, folder / "scratch")
    return {"ssim": s.ssim, "ss2": s.ss2, "band": s.band}


def test_lossless_pick_is_copied_and_verified(factory, toolset, tmp_path):
    tools = toolset("recompress", "lossless", {"png"})
    src = factory.logo(size=(300, 300))
    [r] = run_candidates(src, tmp_path / "out", tools)
    if r["verdict"] != "apply":
        pytest.skip("oxipng found nothing to save on this machine")
    logs = []
    assert AP.apply(tmp_path / "out", tools=tools, log=logs.append) == 0
    assert src.stat().st_size == C.pick_of(r)["size"]
    assert any("verified" in line for line in logs)
    assert staged_files(src.parent) == []


def test_lossy_picks_need_approval(factory, tmp_path):
    out = tmp_path / "out"
    src, _ = _lossy_record(out, factory)
    before = src.read_bytes()
    logs = []
    assert AP.apply(out, tools={}, log=logs.append) == 1
    assert "--approved" in "\n".join(logs)
    assert src.read_bytes() == before


def test_refuses_when_the_source_changed_since_candidates(factory, tmp_path):
    out = tmp_path / "out"
    src, _ = _lossy_record(out, factory)
    src.write_bytes(src.read_bytes() + b"\0")
    changed = src.read_bytes()
    logs = []
    assert AP.apply(out, tools={}, approved=True, log=logs.append) == 1
    assert "changed since candidates ran" in "\n".join(logs)
    assert src.read_bytes() == changed


def test_untouched_files_are_skipped(factory, tmp_path):
    out = tmp_path / "out"
    src, folder = _lossy_record(out, factory)
    rec = json.loads((folder / "metrics.json").read_text())
    rec["verdict"] = "untouched"
    (folder / "metrics.json").write_text(json.dumps(rec))
    before = src.read_bytes()
    logs = []
    assert AP.apply(out, tools={}, approved=True, log=logs.append) == 0
    assert "Nothing to apply." in logs
    assert src.read_bytes() == before


def test_a_matching_lossy_pick_replaces_the_target(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    out = tmp_path / "out"
    src, folder = _lossy_record(out, factory)
    record = json.loads((folder / "metrics.json").read_text())
    record["candidates"][0].update(_measured(folder, tools))
    (folder / "metrics.json").write_text(json.dumps(record))
    logs = []
    assert AP.apply(out, tools=tools, approved=True, log=logs.append) == 0
    assert src.read_bytes() == (folder / "pick.jpg").read_bytes()
    assert any("verified" in line for line in logs)
    assert staged_files(src.parent) == []


def test_remeasure_mismatch_fails_loudly_and_leaves_the_target_alone(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    out = tmp_path / "out"
    src, _ = _lossy_record(out, factory)  # claims ssim 0.5, which the real pick does not have
    before, mtime = src.read_bytes(), src.stat().st_mtime_ns
    logs = []
    assert AP.apply(out, tools=tools, approved=True, log=logs.append) == 1
    assert "MISMATCH" in "\n".join(logs)
    assert src.read_bytes() == before and src.stat().st_mtime_ns == mtime
    assert staged_files(src.parent) == []


def test_a_pick_tampered_with_after_candidates_is_not_written(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    out = tmp_path / "out"
    src, folder = _lossy_record(out, factory)
    record = json.loads((folder / "metrics.json").read_text())
    record["candidates"][0].update(_measured(folder, tools))
    (folder / "metrics.json").write_text(json.dumps(record))
    Image.open(src).save(folder / "pick.jpg", "JPEG", quality=20)  # not the file that was measured
    before, mtime = src.read_bytes(), src.stat().st_mtime_ns
    logs = []
    assert AP.apply(out, tools=tools, approved=True, log=logs.append) == 1
    assert "MISMATCH" in "\n".join(logs)
    assert src.read_bytes() == before and src.stat().st_mtime_ns == mtime
    assert staged_files(src.parent) == []


def test_a_failed_verification_does_not_replace_the_target(factory, tmp_path, monkeypatch):
    out = tmp_path / "out"
    src, _ = _lossy_record(out, factory)
    monkeypatch.setattr(AP, "_verify", lambda *a: "forced")
    before, mtime = src.read_bytes(), src.stat().st_mtime_ns
    logs = []
    assert AP.apply(out, tools={}, approved=True, log=logs.append) == 1
    assert "MISMATCH" in "\n".join(logs) and "forced" in "\n".join(logs)
    assert src.read_bytes() == before and src.stat().st_mtime_ns == mtime
    assert staged_files(src.parent) == []


@pytest.mark.parametrize("failure", [metrics.MetricError("ffmpeg died"), imaging.ImagingError("unreadable"),
                                     OSError("disk"), ValueError("pillow"), UsageError("no such plan")])
def test_a_verification_failure_is_that_files_mismatch_and_the_run_continues(factory, tmp_path, monkeypatch,
                                                                             failure):
    out = tmp_path / "out"
    first, _ = _lossy_record(out, factory, name="a.jpg", folder_name="a--1")
    second, _ = _lossy_record(out, factory, name="b.jpg", folder_name="b--1")
    calls = []

    def verify(folder, *args):
        calls.append(folder.name)
        if folder.name == "a--1":
            raise failure
        return ""

    monkeypatch.setattr(AP, "_verify", verify)
    first_before = first.read_bytes()
    logs = []
    assert AP.apply(out, tools={}, approved=True, log=logs.append) == 1
    assert calls == ["a--1", "b--1"]
    assert first.read_bytes() == first_before
    assert second.read_bytes() == (out / "b--1" / "pick.jpg").read_bytes()
    assert sum("MISMATCH" in line for line in logs) == 1
    assert staged_files(first.parent) == []


@pytest.mark.parametrize("make", [lambda p: p / "missing", lambda p: p / "file", lambda p: p / "empty"])
def test_a_bad_out_is_a_usage_error_naming_the_path(tmp_path, make):
    (tmp_path / "file").write_text("x")
    (tmp_path / "empty").mkdir()
    bad = make(tmp_path)
    with pytest.raises(UsageError, match=re.escape(str(bad))):
        AP.apply(bad, tools={}, log=lambda _: None)
    with pytest.raises(UsageError, match=re.escape(str(bad))):
        AP.needs_metrics(bad)
    assert not (tmp_path / "missing").exists()


def test_only_names_that_match_nothing_are_reported_and_fail_the_run(factory, tmp_path):
    out = tmp_path / "out"
    src, folder = _lossy_record(out, factory)
    logs = []
    assert AP.apply(out, tools={}, only=("nope.jpg",), approved=True, log=logs.append) == 1
    assert sum("nope.jpg" in line for line in logs) == 1
    assert "Nothing to apply." not in logs
    assert src.read_bytes() != (folder / "pick.jpg").read_bytes()


def test_a_matched_only_name_is_applied_even_when_another_matches_nothing(factory, tmp_path, monkeypatch):
    out = tmp_path / "out"
    src, folder = _lossy_record(out, factory)
    monkeypatch.setattr(AP, "_verify", lambda *a: "")
    logs = []
    assert AP.apply(out, tools={}, only=("photo.jpg", "nope.jpg"), approved=True, log=logs.append) == 1
    assert sum("nope.jpg" in line for line in logs) == 1
    assert src.read_bytes() == (folder / "pick.jpg").read_bytes()


def test_dest_receives_the_file_and_the_source_is_untouched(factory, tmp_path, monkeypatch):
    out = tmp_path / "out"
    src, folder = _lossy_record(out, factory)
    monkeypatch.setattr(AP, "_verify", lambda *a: "")
    before = src.read_bytes()
    assert AP.apply(out, tools={}, approved=True, dest=tmp_path / "dest" / "deep", log=lambda _: None) == 0
    assert (tmp_path / "dest" / "deep" / "photo.jpg").read_bytes() == (folder / "pick.jpg").read_bytes()
    assert src.read_bytes() == before


def test_needs_metrics_only_for_lossy_picks_that_will_be_applied(factory, tmp_path):
    out = tmp_path / "out"
    _, folder = _lossy_record(out, factory)
    assert AP.needs_metrics(out) and not AP.needs_metrics(out, only=("other.jpg",))
    record = json.loads((folder / "metrics.json").read_text())
    record["verdict"] = "untouched"
    (folder / "metrics.json").write_text(json.dumps(record))
    assert not AP.needs_metrics(out)


def test_metadata_is_checked_only_for_picks_made_from_the_source(factory, device_icc):
    src = factory.photo(name="p.png", icc=device_icc)
    facts = imaging.read_facts(src)
    chosen = {"label": "oxipng"}
    record = {"format": "png", "profile": "lossless", "resize": None}
    assert AP._took_source(record, chosen, facts)
    assert AP._took_source({**record, "profile": "high"}, chosen, facts)  # a device profile alone keeps it
    assert not AP._took_source({**record, "profile": "high", "resize": 80}, chosen, facts)  # baked by design
    assert AP._took_source(record, {"label": "not-a-rung"}, facts)  # unknown: check it


def _claim(folder, **scores):
    record = json.loads((folder / "metrics.json").read_text())
    record["candidates"][0].update(scores)
    (folder / "metrics.json").write_text(json.dumps(record))


def _drop_key(folder, key):
    record = json.loads((folder / "metrics.json").read_text())
    del record["candidates"][0][key]
    (folder / "metrics.json").write_text(json.dumps(record))


def _scores(monkeypatch, **delta):
    base = {"ssim": 0.9, "ssim_white": 0.9, "ss2": 50.0, "band": 1.0, "butteraugli": None}
    monkeypatch.setattr(AP.metrics, "measure", lambda *a, **k: metrics.Scores(**{**base, **delta}))


@pytest.mark.parametrize("break_record", [
    pytest.param(lambda f: _claim(f, band=None), id="recorded-none"),
    pytest.param(lambda f: _drop_key(f, "band"), id="missing-key"),
    pytest.param(lambda f: _claim(f, band=math.nan), id="recorded-nan"),
])
def test_unusable_recorded_scores_fail_closed_before_anything_is_replaced(factory, tmp_path, monkeypatch,
                                                                         break_record):
    out = tmp_path / "out"
    src, folder = _lossy_record(out, factory, scores={"ssim": 0.9, "ss2": 50.0, "band": 1.0})
    break_record(folder)
    _scores(monkeypatch)
    before, mtime = src.read_bytes(), src.stat().st_mtime_ns
    logs = []
    assert AP.apply(out, tools={}, approved=True, log=logs.append) == 1
    assert "MISMATCH" in "\n".join(logs) and "band" in "\n".join(logs)
    assert src.read_bytes() == before and src.stat().st_mtime_ns == mtime
    assert staged_files(src.parent) == []


@pytest.mark.parametrize("remeasured", [{"band": None}, {"band": math.nan}])
def test_unusable_remeasured_scores_fail_closed(factory, tmp_path, monkeypatch, remeasured):
    out = tmp_path / "out"
    src, _ = _lossy_record(out, factory, scores={"ssim": 0.9, "ss2": 50.0, "band": 1.0})
    _scores(monkeypatch, **remeasured)
    before = src.read_bytes()
    logs = []
    assert AP.apply(out, tools={}, approved=True, log=logs.append) == 1
    assert "MISMATCH" in "\n".join(logs)
    assert src.read_bytes() == before


@pytest.mark.parametrize("key,tol", [("ssim", 1e-6), ("ss2", 1e-3), ("band", 1e-6)])
def test_the_tolerance_boundary(factory, tmp_path, monkeypatch, key, tol):
    recorded = {"ssim": 0.9, "ss2": 50.0, "band": 1.0}
    for shift, expected in ((0.9 * tol, 0), (1.1 * tol, 1)):
        out = tmp_path / f"out-{expected}"
        src, folder = _lossy_record(out, factory, scores=recorded)
        _scores(monkeypatch, **{key: recorded[key] + shift})
        assert AP.apply(out, tools={}, approved=True, log=lambda _: None) == expected
        wrote = src.read_bytes() == (folder / "pick.jpg").read_bytes()
        assert wrote == (expected == 0)


def test_a_user_file_named_like_the_old_staging_name_survives(factory, tmp_path, monkeypatch):
    out = tmp_path / "out"
    src, folder = _lossy_record(out, factory)
    bystander = src.with_name(f".imgopt-{src.name}")
    bystander.write_bytes(b"mine")
    monkeypatch.setattr(AP, "_verify", lambda *a: "")
    assert AP.apply(out, tools={}, approved=True, log=lambda _: None) == 0
    assert bystander.read_bytes() == b"mine"
    assert src.read_bytes() == (folder / "pick.jpg").read_bytes()
    assert [p.name for p in src.parent.glob(".imgopt-*")] == [bystander.name]


def test_a_staged_file_keeps_the_targets_permissions(factory, tmp_path, monkeypatch):
    out = tmp_path / "out"
    src, _ = _lossy_record(out, factory)
    src.chmod(0o640)
    monkeypatch.setattr(AP, "_verify", lambda *a: "")
    assert AP.apply(out, tools={}, approved=True, log=lambda _: None) == 0
    assert src.stat().st_mode & 0o777 == 0o640


def _subfactory(factory, name):
    return type(factory)(factory.root / name)


def test_dest_refuses_two_records_that_share_a_basename_before_writing(factory, tmp_path, monkeypatch):
    out = tmp_path / "out"
    first, _ = _lossy_record(out, factory, name="photo.jpg", folder_name="a--1")
    other = _subfactory(factory, "sub")
    second, _ = _lossy_record(out, other, name="photo.jpg", folder_name="b--1")
    monkeypatch.setattr(AP, "_verify", lambda *a: "")
    dest = tmp_path / "dest"
    with pytest.raises(UsageError, match="photo.jpg"):
        AP.apply(out, tools={}, approved=True, dest=dest, log=lambda _: None)
    assert not dest.exists()


def test_a_mixed_selection_without_approval_writes_nothing(factory, tmp_path, monkeypatch):
    out = tmp_path / "out"
    plain, _ = _lossy_record(out, factory, name="a.jpg", folder_name="a--1", kind="lossless")
    lossy, _ = _lossy_record(out, factory, name="b.jpg", folder_name="b--1")
    monkeypatch.setattr(AP, "_verify", lambda *a: "")
    before = plain.read_bytes(), lossy.read_bytes()
    assert AP.apply(out, tools={}, log=lambda _: None) == 1
    assert (plain.read_bytes(), lossy.read_bytes()) == before


def test_metadata_is_not_checked_for_a_lossy_kind_rung_even_from_the_source(factory):
    facts = imaging.read_facts(factory.photo())
    record = {"format": "jpeg", "profile": "high", "resize": None}
    assert AP._took_source(record, {"label": "lossless-jpegoptim"}, facts)
    assert not AP._took_source(record, {"label": "jpegoptim-m60"}, facts)


def run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), "apply", *map(str, args)], capture_output=True, text=True)


def test_cli_exits_2_for_a_missing_out(tmp_path):
    proc = run_cli(tmp_path / "missing")
    assert proc.returncode == 2 and str(tmp_path / "missing") in proc.stderr


def test_cli_prints_the_tools_line_and_applies(factory, toolset, tmp_path):
    tools = toolset("recompress", "high", {"jpeg"})
    out = tmp_path / "out"
    src, folder = _lossy_record(out, factory)
    record = json.loads((folder / "metrics.json").read_text())
    record["candidates"][0].update(_measured(folder, tools))
    (folder / "metrics.json").write_text(json.dumps(record))
    refused = run_cli(out)
    assert refused.returncode == 1 and "--approved" in refused.stdout
    proc = run_cli(out, "--approved")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ffmpeg" in proc.stdout and "verified" in proc.stdout
    assert src.read_bytes() == (folder / "pick.jpg").read_bytes()


def test_two_records_that_write_one_target_are_refused_before_writing(factory, tmp_path, monkeypatch):
    out = tmp_path / "out"
    a, _ = _lossy_record(out, factory, name="a.png", folder_name="a--1", target=factory.root / "a.webp")
    b, _ = _lossy_record(out, factory, name="a.jpg", folder_name="b--1", target=factory.root / "a.webp")
    monkeypatch.setattr(AP, "_verify", lambda *a: "")
    with pytest.raises(UsageError, match=re.escape(str(factory.root / "a.webp"))):
        AP.apply(out, tools={}, approved=True, log=lambda _: None)
    assert not (factory.root / "a.webp").exists()


@pytest.mark.parametrize("with_dest", [False, True])
def test_an_existing_file_that_is_not_the_source_is_never_overwritten(factory, tmp_path, monkeypatch, with_dest):
    out = tmp_path / "out"
    dest = tmp_path / "dest"
    existing = (dest if with_dest else factory.root) / "photo.webp"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"someone else's file")
    src, _ = _lossy_record(out, factory, target=factory.root / "photo.webp")
    monkeypatch.setattr(AP, "_verify", lambda *a: "")
    with pytest.raises(UsageError, match=re.escape(str(existing))) as info:
        AP.apply(out, tools={}, approved=True, dest=dest if with_dest else None, log=lambda _: None)
    assert "--dest" in str(info.value)
    assert existing.read_bytes() == b"someone else's file"


def test_a_same_format_resize_may_write_over_its_own_source(factory, tmp_path, monkeypatch):
    out = tmp_path / "out"
    src, folder = _lossy_record(out, factory, resize=80)
    monkeypatch.setattr(AP, "_verify", lambda *a: "")
    assert AP.apply(out, tools={}, approved=True, log=lambda _: None) == 0
    assert src.read_bytes() == (folder / "pick.jpg").read_bytes()

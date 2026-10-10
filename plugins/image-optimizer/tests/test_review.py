import json

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
                      "ssim": 0.99, "ss2": 85.0, "band": 1.0, "pass": passed, "reason": "" if passed else "banding 4.0 > 3"})
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
        ST.put(c, folder)
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

import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

from imgbench import harness, manifest  # noqa: E402
from imgbench import report as R  # noqa: E402
from imgbench.harness import family_of  # noqa: E402


def row(category, size, cands, profile="high"):
    return {"category": category, "job": "recompress-high", "profile": profile, "source_size": size,
            "in_place": True, "seconds": 1.0, "disk": 1000, "pick_family": None,
            "candidates": [{"family": f, "size": s, "pass": p} for f, s, p in cands]}


def test_ablation_drops_a_family_and_repicks():
    # Sizes in KB-scale units: a saving under max(1 KB, 1%) would leave the file untouched (see the next test)
    rows = [row("photo-camera", 100_000, [("cjpegli", 60_000, True), ("jpegoptim", 70_000, True)]),
            row("photo-camera", 100_000, [("cjpegli", 90_000, False), ("jpegoptim", 80_000, True)])]
    full = R.ablate(rows, drop=set())
    without = R.ablate(rows, drop={"cjpegli"})
    assert full["photo-camera"] == (100_000 + 100_000 - 60_000 - 80_000) / 200_000
    assert without["photo-camera"] == (200_000 - 70_000 - 80_000) / 200_000


def test_untouched_threshold_applies_in_ablation():
    rows = [row("icon", 1000, [("oxipng", 995, True)])]  # 5 B saved: below max(1 KB, 1%)
    assert R.ablate(rows, drop=set())["icon"] == 0.0


def test_markdown_reports_per_category_never_one_overall_number():
    rows = [row("photo-camera", 1000, [("cjpegli", 600, True)]), row("icon", 1000, [("oxipng", 900, True)])]
    md = R.markdown(rows)
    assert "| photo-camera |" in md and "| icon |" in md and "overall" not in md.lower()


@pytest.mark.parametrize("label, family", [
    ("jpegoptim-m85", "jpegoptim"), ("cjpeg-q75", "cjpeg"), ("cjpegli-q55", "cjpegli"),
    ("guetzli-q84", "guetzli"), ("pngquant-q70-95", "pngquant"), ("pngquant-c64", "pngquant"),
    ("cwebp-q80", "cwebp"), ("avifenc-q50", "avifenc"), ("cwebp-lossless", "cwebp-lossless"),
    ("avifenc-lossless", "avifenc-lossless"), ("lossless-jpegoptim", "lossless-jpegoptim"),
    ("lossless-jpegtran", "lossless-jpegtran"), ("oxipng", "oxipng"), ("oxipng-zopfli", "oxipng-zopfli"),
    ("svgo", "svgo"), ("gifsicle", "gifsicle"), (None, None)])
def test_family_of_every_ladder_label(label, family):
    assert family_of(label) == family


def test_ablation_columns_map_to_families():
    rows = [row("photo-camera", 100_000, [("jpegoptim", 80_000, True), ("lossless-jpegoptim", 90_000, True),
                                          ("cjpegli", 60_000, True)]),
            row("png-master", 100_000, [("oxipng", 70_000, True), ("oxipng-zopfli", 65_000, True)])]
    cols = dict(R.ABLATION_COLUMNS)
    assert cols["jpegoptim"] == {"jpegoptim", "lossless-jpegoptim"} and cols["zopfli"] == {"oxipng-zopfli"}
    md = R.markdown(rows)
    # jpegoptim off removes both of its families; the best remaining is cjpegli, so the saving is unchanged
    assert R.ablate(rows, drop=cols["jpegoptim"])["photo-camera"] == 0.4
    assert "−zopfli" in md and "−jpegli" in md and "−pngquant" not in md


def test_skipped_rows_are_counted_and_their_reasons_listed():
    skipped = {"category": "edge", "job": "lossless", "profile": "lossless", "file": "/c/edge/a.png",
               "source_size": 100, "verdict": "skipped", "reason": "a.png: 16-bit PNG", "candidates": []}
    done = row("edge", 1000, [("oxipng", 500, True)])
    done.update(job="lossless", verdict="apply", pick="oxipng", pick_family="oxipng", pick_size=500)
    skipped.update(seconds=1.0, disk=0)
    md = R.markdown([skipped, done])
    assert "16-bit PNG" in md and "| edge | 1 | 1 | 0 | 0 |" in md


def test_picks_at_the_floor_and_banding_use_the_pick_scores():
    def picked(ssim, ss2, band, family="cjpegli", ssim_evidence=None):
        r = row("photo-camera", 1000, [(family, 500, True)])
        r.update(verdict="apply", pick=family + "-q50", pick_family=family, pick_size=500,
                 pick_kind="lossy",
                 gates={"ssim": 0.98, "ss2": 80.0, "band": 3.0, "lossless_only": False},
                 pick_scores={"ssim": ssim, "ss2": ss2, "band": band, "ssim_evidence": ssim_evidence})
        return r
    rows = [picked(0.9805, 90.0, 1.0, ssim_evidence=0.9795), picked(0.99, 85.0, 2.0), picked(0.995, 95.0, 0.5)]
    md = R.markdown(rows)
    assert "| photo-camera | 3 | 1 | 33.3% |" in md  # one of three lossy picks sits within 0.002 of the SSIM floor
    assert "| photo-camera | 1 | 0.0010 | 0.0010 |" in md  # |Evidence SSIM - gate SSIM| of the one pick that has both
    assert "| photo-camera | 3 | 1.00 | 2.00 | 2.00 |" in md  # banding median, p90, max


@pytest.mark.parametrize("scores, margin", [
    ({"ssim": 0.99, "ss2": 95.0}, 0.01),  # SSIM margin 0.01, ss2 margin 15 points = 0.015
    ({"ssim": 0.99, "ss2": 95.0, "ssim_evidence": 0.981}, 0.001),  # the Evidence SSIM is gated against the floor too
    ({"ssim": 0.99, "ss2": 95.0, "ssim_evidence": None}, 0.01),  # no Evidence SSIM (ffmpeg could not reproduce)
    ({"ssim": 0.999, "ss2": 81.0}, 0.001),  # ss2 margin 1 point, scaled by 1/1000
    ({}, float("inf")),
])
def test_floor_margin_is_the_smallest_gated_margin(scores, margin):
    r = {"pick_scores": scores, "gates": {"ssim": 0.98, "ss2": 80.0, "band": 3.0, "lossless_only": False}}
    assert R.floor_margin(r) == pytest.approx(margin)


def test_a_pick_whose_evidence_ssim_sits_at_the_floor_counts_at_the_floor():
    r = row("photo-camera", 1000, [("cjpegli", 500, True)])
    r.update(verdict="apply", pick="cjpegli-q50", pick_family="cjpegli", pick_size=500, pick_kind="lossy",
             gates={"ssim": 0.98, "ss2": 80.0, "band": 3.0, "lossless_only": False},
             pick_scores={"ssim": 0.99, "ss2": 95.0, "band": 1.0, "ssim_evidence": 0.9805})
    assert "| photo-camera | 1 | 1 | 100.0% |" in R.markdown([r])


def lossy_pick(ssim, band, *, family="cjpegli", identical=None, band_gated=None):
    r = row("photo-camera", 1000, [(family, 500, True)])
    scores = {"ssim": ssim, "ss2": 99.0, "band": band}
    if identical is not None:
        scores.update(identical=identical, band_gated=band_gated)
    r.update(verdict="apply", pick=family + "-q50", pick_family=family, pick_size=500, pick_kind="lossy",
             gates={"ssim": 0.98, "ss2": 80.0, "band": 3.0, "lossless_only": False}, pick_scores=scores)
    return r


def test_identical_picks_stay_out_of_the_floor_and_banding_tables():
    rows = [lossy_pick(0.981, 2.5, identical=False, band_gated=False),
            lossy_pick(1.0, 0.0, family="oxipng", identical=True, band_gated=False),  # pixel-identical, kind lossy
            lossy_pick(1.0, 0.0, family="cwebp-lossless")]  # an older row: the _perfect signature marks it
    md = R.markdown(rows)
    assert "| photo-camera | 1 | 1 | 100.0% |" in md  # floor: the one pick that changes pixels
    assert "| photo-camera | 1 | 2.50 | 2.50 | 2.50 |" in md  # banding: same pick


def test_banding_leaves_out_the_picks_whose_banding_was_gated():
    # the record says which picks had their banding gated; the report does not guess from the family
    rows = [lossy_pick(0.99, 2.0, family="some-palette-rung", identical=False, band_gated=True),
            lossy_pick(0.99, 1.0, identical=False, band_gated=False)]
    assert "| photo-camera | 1 | 1.00 | 1.00 | 1.00 |" in R.markdown(rows)


def test_load_backfills_identical_and_band_gated_from_the_kept_record(tmp_path):
    old = lossy_pick(0.99, 1.5, family="pngquant")
    old.update(file=str(tmp_path / "corpus" / "x.png"))
    del old["pick_scores"]["band"]
    bare = lossy_pick(1.0, 0.0, family="oxipng")  # no record folder left: the signature decides
    bare.update(file=str(tmp_path / "corpus" / "gone.png"))
    (tmp_path / "rows.jsonl").write_text(json.dumps(old) + "\n" + json.dumps(bare) + "\n")
    folder = harness.record_dir(tmp_path, old)
    folder.mkdir(parents=True)
    (folder / "metrics.json").write_text(json.dumps({"pick": "p.png", "candidates": [
        {"file": "q.png", "identical": True, "band_gated": False},
        {"file": "p.png", "identical": False, "band_gated": True}]}))
    loaded, gone = R.load(tmp_path)
    assert loaded["pick_scores"]["identical"] is False and loaded["pick_scores"]["band_gated"] is True
    assert R.changes_pixels(loaded) and R.band_gated(loaded)
    assert "identical" not in gone["pick_scores"] and not R.changes_pixels(gone)


def test_ablation_says_not_tried_where_a_category_never_ran_the_encoder():
    # guetzli is capped out above 6 MP: one photo-camera file and no phone-upload file got a guetzli candidate
    rows = [row("photo-camera", 100_000, [("cjpegli", 60_000, True), ("guetzli", 50_000, True)]),
            row("photo-camera", 100_000, [("cjpegli", 60_000, True)]),
            row("phone-upload", 100_000, [("cjpegli", 70_000, True)])]
    md = R.markdown(rows)
    assert "| photo-camera | 45.0% | 25.0% | 40.0% (tried on 1 of 2) |" in md
    assert "| phone-upload | 30.0% | 0.0% | not tried |" in md


def test_rows_whose_candidates_all_errored_are_counted_apart_from_no_pick():
    failed = row("icon", 1000, [("oxipng", None, False)])
    failed.update(verdict="untouched", pick=None, errored=True)  # a tool failure, not a gate failure
    gated = row("icon", 1000, [("pngquant", 400, False)])
    gated.update(verdict="untouched", pick=None, errored=False)
    md = R.markdown([failed, gated])
    assert "| Category | Files | Skipped | No pick | Errored | Saved |" in md
    assert "| icon | 2 | 0 | 1 | 1 | 0.0% |" in md


def test_ablate_refuses_rows_of_several_jobs():
    medium = row("photo-camera", 1000, [("cjpegli", 500, True)], profile="medium")
    medium["job"] = "recompress-medium"
    with pytest.raises(ValueError, match="recompress-high, recompress-medium"):
        R.ablate([row("photo-camera", 1000, [("cjpegli", 600, True)]), medium], drop=set())


def test_markdown_warns_when_all_differs_from_saved():
    r = row("photo-camera", 100_000, [("cjpegli", 50_000, False)])  # the pick failed its gates on the record
    r.update(verdict="apply", pick="cjpegli-q50", pick_family="cjpegli", pick_size=50_000)
    md = R.markdown([r])
    assert "WARNING: recompress-high/photo-camera: the ablation's All (0.0%) differs from Saved (50.0%)" in md


def test_all_equals_saved_on_a_real_run(factory, toolset, tmp_path):
    """The ablation replays imgopt's pick rule from the recorded gate pass: a smaller candidate that failed the
    Evidence check is recorded pass=False, so on a fresh run "All" and "Saved" agree in every job and category."""
    toolset("recompress", "high", {"jpeg", "png"})
    corpus = tmp_path / "corpus"
    entries = []
    sources = [factory.photo("a.jpg", size=(320, 240), quality=97), factory.photo("b.jpg", size=(200, 150)),
               factory.gradient("c.png", size=(240, 60))]
    for src in sources:
        dest = corpus / "photo-small" / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        entries.append(manifest.entry_for(dest, corpus, category="photo-small", origin="test", transform="",
                                          license="CC0"))
    manifest.write(entries, corpus)
    run_dir = harness.run(corpus, tmp_path / "run", [harness.Job("recompress-high", ("photo-small",), "high")],
                          say=lambda _: None)
    rows = R.load(run_dir)
    assert any(r["verdict"] == "apply" for r in rows)  # the invariant is about something
    assert R.ablate(rows, set())["photo-small"] == pytest.approx(R._real_saved(rows))
    assert "WARNING" not in R.markdown(rows)

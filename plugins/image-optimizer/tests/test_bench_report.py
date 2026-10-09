import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

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
    assert "16-bit PNG" in md and "| edge | 1 | 1 | 0 |" in md


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

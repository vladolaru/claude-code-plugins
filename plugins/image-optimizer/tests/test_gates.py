import pytest

from imgopt_lib import gates as G


def rec(**kw):
    base = {"kind": "lossy", "identical": False, "ssim": 0.99, "ss2": 85.0, "band": 1.0,
            "band_gated": False, "size": 1000, "progressive": True}
    base.update(kw)
    return base


def test_profiles_carry_the_spec_floors():
    assert G.PROFILES["high"] == G.Gates(0.98, 80.0, 3.0, False)
    assert G.PROFILES["medium"] == G.Gates(0.96, 60.0, 3.0, False)
    assert G.PROFILES["lossless"].lossless_only


def test_overrides_apply_and_are_refused_on_lossless():
    assert G.gates_for("high", ss2=70).ss2 == 70
    with pytest.raises(ValueError):
        G.gates_for("lossless", ssim=0.9)


def test_lossless_identical_passes_every_profile():
    r = rec(kind="lossless", identical=True)
    for name in ("lossless", "high", "medium"):
        assert G.evaluate(r, G.PROFILES[name]) == (True, "")


def test_lossless_profile_rejects_changed_pixels():
    assert G.evaluate(rec(), G.PROFILES["lossless"]) == (False, "not pixel-identical")


def test_failures_name_each_gate():
    ok, why = G.evaluate(rec(ssim=0.97, ss2=74.0), G.PROFILES["high"])
    assert not ok and "SSIM 0.9700 < 0.98" in why and "ss2 74.0 < 80" in why


def test_banding_is_gated_only_when_the_rung_is_palette():
    assert G.evaluate(rec(band=9.0), G.PROFILES["high"])[0]
    ok, why = G.evaluate(rec(band=9.0, band_gated=True), G.PROFILES["high"])
    assert not ok and "banding 9.0 > 3" in why


def test_errors_and_discards_never_pass():
    assert G.evaluate(rec(error="boom"), G.PROFILES["high"]) == (False, "boom")
    assert G.evaluate(rec(discarded="orientation"), G.PROFILES["high"]) == (False, "orientation")


def test_pick_prefers_smallest_then_lossless_then_progressive():
    a = rec(size=900, **{"pass": True})
    b = rec(size=900, kind="lossless", identical=True, **{"pass": True})
    c = rec(size=800, **{"pass": False})
    assert G.pick([a, b, c]) is b
    assert G.pick([c]) is None


def test_verdict_skips_trivial_savings_in_place_only():
    assert G.verdict(100_000, rec(size=99_500))[0] == "untouched"   # 500 B < 1 KB
    assert G.verdict(500_000, rec(size=497_000))[0] == "untouched"  # 3 KB < 1% of 500 KB
    assert G.verdict(100_000, rec(size=90_000))[0] == "apply"
    assert G.verdict(100_000, rec(size=120_000))[0] == "untouched"
    assert G.verdict(100_000, rec(size=99_900), in_place=False)[0] == "apply"
    assert G.verdict(100_000, None)[0] == "untouched"


def without(key, **kw):
    r = rec(**kw)
    del r[key]
    return r


@pytest.mark.parametrize("key, kw", [
    ("ssim", {}), ("ss2", {}), ("band", {"band_gated": True}),
])
def test_unmeasured_metric_fails_the_gate_instead_of_raising(key, kw):
    assert G.evaluate(rec(**{key: None}, **kw), G.PROFILES["high"]) == (False, f"{key} not measured")
    assert G.evaluate(without(key, **kw), G.PROFILES["high"]) == (False, f"{key} not measured")


def test_unmeasured_band_is_ignored_when_the_rung_is_not_palette():
    assert G.evaluate(rec(band=None), G.PROFILES["high"])[0]
    assert G.evaluate(without("band"), G.PROFILES["high"])[0]


@pytest.mark.parametrize("ssim, passes", [
    (0.98, True), (0.9799999, False),
])
def test_high_ssim_floor_is_inclusive(ssim, passes):
    assert G.evaluate(rec(ssim=ssim, ss2=85.0, band=1.0), G.PROFILES["high"])[0] is passes


@pytest.mark.parametrize("ss2, passes", [
    (80.0, True), (79.99, False),
])
def test_high_ss2_floor_is_inclusive(ss2, passes):
    assert G.evaluate(rec(ss2=ss2), G.PROFILES["high"])[0] is passes


@pytest.mark.parametrize("band, passes", [
    (3.0, True), (3.01, False),
])
def test_high_band_ceiling_is_inclusive(band, passes):
    assert G.evaluate(rec(band=band, band_gated=True), G.PROFILES["high"])[0] is passes


@pytest.mark.parametrize("ssim, passes", [
    (0.96, True), (0.9599999, False),
])
def test_medium_ssim_floor_is_inclusive(ssim, passes):
    assert G.evaluate(rec(ssim=ssim, ss2=85.0), G.PROFILES["medium"])[0] is passes


@pytest.mark.parametrize("ss2, passes", [
    (60.0, True), (59.99, False),
])
def test_medium_ss2_floor_is_inclusive(ss2, passes):
    assert G.evaluate(rec(ss2=ss2), G.PROFILES["medium"])[0] is passes


def test_lossy_record_fails_the_lossless_profile_even_when_it_looks_perfect():
    assert G.evaluate(rec(kind="lossless", identical=False), G.PROFILES["lossless"]) == (
        False, "not pixel-identical")


def test_pick_breaks_equal_sizes_in_favour_of_progressive():
    plain = rec(size=900, progressive=False, **{"pass": True})
    prog = rec(size=900, progressive=True, **{"pass": True})
    assert G.pick([plain, prog]) is prog
    assert G.pick([prog, plain]) is prog


def test_verdict_applies_at_exactly_the_absolute_floor():
    assert G.verdict(100_000, rec(size=100_000 - 1024))[0] == "apply"
    assert G.verdict(100_000, rec(size=100_000 - 1023))[0] == "untouched"


def test_verdict_applies_at_exactly_one_percent_of_a_large_source():
    assert G.verdict(500_000, rec(size=495_000))[0] == "apply"
    assert G.verdict(500_000, rec(size=495_001))[0] == "untouched"


def test_verdict_leaves_equal_size_untouched():
    assert G.verdict(100_000, rec(size=100_000))[0] == "untouched"


def test_verdict_in_place_false_applies_even_a_tiny_saving():
    assert G.verdict(100_000, rec(size=100_000 - 10), in_place=False)[0] == "apply"


def test_a_job_that_rewrites_the_source_says_it_replaces_the_original():
    verdict, why = G.verdict(100_000, rec(size=60_000), in_place=False, replaces_source=True)
    assert verdict == "apply" and "replaces the original" in why and "new file" not in why
    assert "new file" in G.verdict(100_000, rec(size=60_000), in_place=False)[1]


@pytest.mark.parametrize("reviewer, passes", [(0.979999, False), (0.98, True), (None, True)])
def test_the_reviewer_ssim_is_held_to_the_ssim_floor_when_measured(reviewer, passes):
    ok, why = G.evaluate(rec(ssim=0.9801, ssim_reviewer=reviewer), G.PROFILES["high"])
    assert ok is passes
    assert passes or why == "reviewer SSIM 0.979999 < 0.98"

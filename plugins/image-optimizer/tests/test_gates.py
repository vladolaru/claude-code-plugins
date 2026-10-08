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

"""Profiles, gates and the pick rule: which candidate wins and whether to touch the file.

Floors come from the 2026-10-07 session: SSIM 0.98 is the floor Vlad set for
images the product shows, 0.96 for orphaned files; the ssimulacra2 floors
were chosen in the session and confirmed by eye. Banding is gated only on
palette PNG output, the one case calibrated against a real failure (#69556).
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Gates:
    ssim: float | None
    ss2: float | None
    band: float | None
    lossless_only: bool


PROFILES = {
    "lossless": Gates(None, None, None, True),
    "high": Gates(0.98, 80.0, 3.0, False),
    "medium": Gates(0.96, 60.0, 3.0, False),
}
SKIP_BYTES = 1024
SKIP_RATIO = 0.01


def gates_for(profile: str, *, ssim=None, ss2=None, band=None) -> Gates:
    gates = PROFILES[profile]
    overrides = {k: v for k, v in (("ssim", ssim), ("ss2", ss2), ("band", band)) if v is not None}
    if overrides and gates.lossless_only:
        raise ValueError("--ssim/--ss2/--band need --profile high or medium")
    return replace(gates, **overrides)


def evaluate(rec: dict, gates: Gates) -> tuple[bool, str]:
    if rec.get("error"):
        return False, rec["error"]
    if rec.get("discarded"):
        return False, rec["discarded"]
    if rec.get("kind") == "lossless" and rec.get("identical"):
        return True, ""
    if gates.lossless_only:
        return False, "not pixel-identical"
    fails = []
    if gates.ssim is not None:
        if rec.get("ssim") is None:
            fails.append("ssim not measured")
        elif rec["ssim"] < gates.ssim:
            fails.append(f"SSIM {rec['ssim']:.4f} < {gates.ssim:g}")
        # The number a reviewer's ffmpeg check prints, when candidates measured it (picks only).
        if rec.get("ssim_reviewer") is not None and rec["ssim_reviewer"] < gates.ssim:
            fails.append(f"reviewer SSIM {rec['ssim_reviewer']:.6f} < {gates.ssim:g}")
    if gates.ss2 is not None:
        if rec.get("ss2") is None:
            fails.append("ss2 not measured")
        elif rec["ss2"] < gates.ss2:
            fails.append(f"ss2 {rec['ss2']:.1f} < {gates.ss2:g}")
    if gates.band is not None and rec.get("band_gated"):
        if rec.get("band") is None:
            fails.append("band not measured")
        elif rec["band"] > gates.band:
            fails.append(f"banding {rec['band']:.1f} > {gates.band:g}")
    return not fails, "; ".join(fails)


def pick(cands: list[dict]) -> dict | None:
    passing = [c for c in cands if c.get("pass")]
    if not passing:
        return None
    return min(passing, key=lambda c: (c["size"], 0 if c.get("kind") == "lossless" else 1,
                                       0 if c.get("progressive") else 1))


def verdict(source_size: int, chosen: dict | None, *, in_place: bool = True,
            replaces_source: bool = False) -> tuple[str, str]:
    """("apply" or "untouched", why). Only an ``in_place`` job (same format, same size) can be too small a
    saving to apply; ``replaces_source`` says a resize job writes its pick over the original. Any other job
    applies its pick even when it is larger (a new format is wanted for more than size), and says so."""
    if chosen is None:
        return "untouched", "no candidate passed the gates"
    if not in_place:
        grew = chosen["size"] - source_size
        larger = f", {grew / source_size:.0%} LARGER than the original" if grew > 0 else ""
        if replaces_source:
            return "apply", f"replaces the original: {source_size} B -> {chosen['size']} B{larger}"
        return "apply", f"new file of {chosen['size']} B{larger}"
    saved = source_size - chosen["size"]
    if saved <= 0:
        return "untouched", "no candidate is smaller than the original"
    if saved < max(SKIP_BYTES, SKIP_RATIO * source_size):
        return "untouched", f"saving {saved} B is below max(1 KB, 1%)"
    return "apply", f"saves {saved} B ({saved / source_size:.0%})"

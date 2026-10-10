"""Per-file choices and the human's verdicts: what a file will be written as, and what the human said about it.

A record keeps the gates' ``pick`` and, once someone chose another variant, a ``choice`` (`choose`). Everything
that writes or shows "the" variant (`sheet`, `apply`, `manifest`, `clean --keep-picks`) reads
`candidates.choice_of`. A variant whose file is gone is restored from the variant store; one the store no longer
has needs `candidates` again.
"""

from __future__ import annotations

from pathlib import Path

from . import gates as G
from . import store as ST
from .candidates import choice_of, kb, write_record
from .ladder import UsageError


def live_variants(record: dict) -> list[dict]:
    """Measured candidates with a file, smallest first: what a human can be shown and choose."""
    live = [c for c in record["candidates"]
            if c.get("file") and "size" in c and not c.get("error") and not c.get("discarded")]
    return sorted(live, key=lambda c: c["size"])


def _in_place(record: dict) -> bool:
    return record.get("target") == record["source"]["path"] and not record.get("resize")


def _ensure_file(folder: Path, cand: dict) -> None:
    if (folder / cand["file"]).is_file() or ST.restore(cand, folder):
        return
    raise UsageError(f"{cand['label']}: its file is gone from {folder} and from the variant store; run candidates "
                     f"again for {Path(folder).name} to remake it (one encode)")


def choose(folder: Path, record: dict, how: str) -> dict:
    """Set the file's variant: a label, "milder" (the next larger passing variant), "stronger" (the next smaller
    variant, which may fail the gates), or "default" (back to the gates' pick). Rewrites the record, verdict
    included, and returns the chosen candidate."""
    live = live_variants(record)
    current = choice_of(record)
    if how == "default":
        record.pop("choice", None)
        chosen = choice_of(record)
    elif how in ("milder", "stronger"):
        if current is None:
            raise UsageError(f"{record['source']['path']}: no current variant to step from; name a label "
                             "(see `imgopt variants`)")
        if how == "milder":
            options = [c for c in live if c["size"] > current["size"] and c.get("pass")]
            if not options:
                raise UsageError(f"no passing variant is milder than {current['label']}; leaving the original is "
                                 "the mildest choice (reject it on the page)")
            chosen = options[0]
        else:
            options = [c for c in live if c["size"] < current["size"]]
            if not options:
                raise UsageError(f"no measured variant is smaller than {current['label']}")
            chosen = options[-1]
    else:
        chosen = next((c for c in live if c["label"] == how), None)
        if chosen is None:
            raise UsageError(f"{how!r} is not a measured variant of {record['source']['path']}; "
                             f"choose one of: {', '.join(c['label'] for c in live)}")
    if chosen is not None:
        _ensure_file(Path(folder), chosen)
        if chosen.get("file") != record.get("pick"):
            record["choice"] = chosen["file"]
        else:
            record.pop("choice", None)
    target_is_source = record.get("target") == record["source"]["path"]
    record["verdict"], record["verdict_reason"] = G.verdict(
        record["source"]["size"], chosen, in_place=_in_place(record), replaces_source=target_is_source,
        min_saving=record.get("min_saving", 0))
    write_record(Path(folder), record)
    return chosen


def variant_lines(folder: Path, record: dict) -> list[str]:
    """One line per measured variant: label, size, saving, scores, gate result, default/choice marks, file state."""
    src = record["source"]
    current = choice_of(record)
    lines = [f"{src['path']}  {kb(src['size'])}"]
    for c in live_variants(record):
        marks = [m for m, on in (("default", c.get("file") == record.get("pick")),
                                 ("choice", current is not None and c is current)) if on]
        state = "on disk" if (Path(folder) / c["file"]).is_file() else (
            "in store" if c.get("key") and ST.get(c["key"]) else "gone")
        gate = "PASS" if c.get("pass") else f"fails: {c.get('reason', '')}"
        scores = (f"SSIM {c['ssim']:.4f} ss2 {c['ss2']:.1f} band {c['band']:.1f}" if "ssim" in c else "not measured")
        mark = f"  [{', '.join(marks)}]" if marks else ""
        lines.append(f"  {c['label']:22} {kb(c['size']):>9} {(c['size'] - src['size']) / src['size']:+.0%}  "
                     f"{scores}  {gate}{mark}  {state}")
    return lines

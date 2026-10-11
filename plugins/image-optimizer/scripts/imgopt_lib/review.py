"""Per-file choices and the human's verdicts: what a file will be written as, and what the human said about it.

A record keeps the gates' ``pick`` and, once someone chose another variant, a ``choice`` (`choose`). Everything
that writes or shows "the" variant (`sheet`, `apply`, `manifest`, `clean --keep-picks`) reads
`candidates.choice_of`. A variant whose file is gone is restored from the variant store; one the store no longer
has needs `candidates` again.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from . import gates as G
from . import store as ST
from .candidates import choice_of, kb, load_records, write_record
from .ladder import UsageError

BLOCK_HEADER = "imgopt-verdicts 1"
VERDICTS_FILE = "verdicts.json"
VERDICTS = ("keep", "reject", "milder", "stronger")


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


def rows_of(dirs) -> list[tuple[Path, dict]]:
    """Every record of the last run in each working folder, in folder order. A source path in two folders is
    refused: the page, the verdict block and verdicts.json all key files by source path."""
    rows = []
    seen: dict[str, Path] = {}
    for d in dirs:
        found = load_records(Path(d))
        if not found:
            raise UsageError(f"{d}: no candidates results here (expected <folder>/metrics.json); check the path")
        for folder, record in found:
            path = record["source"]["path"]
            if path in seen:
                raise UsageError(f"{path} has results in both {seen[path]} and {folder}; review those working "
                                 "folders on separate pages")
            seen[path] = folder
        rows += found
    return rows


def fingerprint(rows) -> str:
    """What a page showed: each file's source hash and current choice. A verdict block carries its page's
    fingerprint, so verdicts made on a page built from other results (a later `choose` or `candidates`) are
    refused instead of landing on variants nobody saw."""
    items = sorted((r["source"]["path"], r["source"].get("sha256"), (choice_of(r) or {}).get("label")) for _, r in rows)
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()[:16]


def parse_block(text: str) -> dict:
    """The verdict block the page's "Copy verdicts" gives: the header line, then one JSON object
    {"fingerprint", "files": {source path: {"verdict", "variant", "note"}}}. Text around it is ignored."""
    start = text.find(BLOCK_HEADER)
    if start < 0:
        raise UsageError(f"no {BLOCK_HEADER!r} line: paste the whole block the page's Copy verdicts button gives")
    body = text[start + len(BLOCK_HEADER):].lstrip()
    try:
        data, _ = json.JSONDecoder().raw_decode(body)
    except json.JSONDecodeError as error:
        raise UsageError(f"the verdict block is not complete JSON ({error}); copy it again whole") from error
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict) or "fingerprint" not in data:
        raise UsageError("the verdict block lacks its fingerprint or files; copy it again from the page")
    for path, entry in data["files"].items():
        if not isinstance(entry, dict) or entry.get("verdict") not in VERDICTS:
            raise UsageError(f"{path}: verdict must be one of {', '.join(VERDICTS)}")
    return data


def _load(out: Path) -> dict:
    try:
        return json.loads((Path(out) / VERDICTS_FILE).read_text())
    except (OSError, json.JSONDecodeError):
        return {"files": {}}


def record_verdicts(dirs, text: str) -> dict[str, int]:
    """Store the pasted verdicts in each working folder's verdicts.json and return the count per verdict. A
    "keep", "milder" or "stronger" naming a variant other than the current choice chooses it first, so the stored
    entry names the variant the human was shown and a later `choose --milder/--stronger` steps from it. A "reject"
    ignores the variant."""
    data = parse_block(text)
    rows = rows_of(dirs)
    if data["fingerprint"] != fingerprint(rows):
        raise UsageError("these verdicts are from a page built from other results (a variant was chosen or "
                         "candidates ran again since); rebuild the page with `sheet` and review again")
    by_path = {r["source"]["path"]: (folder, r) for folder, r in rows}
    unknown = sorted(set(data["files"]) - set(by_path))
    if unknown:
        raise UsageError(f"not on this page: {', '.join(unknown)}")
    changes = []
    for path, entry in data["files"].items():
        folder, record = by_path[path]
        variant = entry.get("variant")
        current = choice_of(record)
        if entry["verdict"] != "reject" and variant and (current is None or variant != current["label"]):
            # Validate every change before applying any: a failed `choose` midway would leave the page
            # half-applied, and the block unrecoverable (the fingerprint has moved).
            cand = next((c for c in live_variants(record) if c["label"] == variant), None)
            if cand is None:
                raise UsageError(f"{path}: {variant!r} is not a measured variant; choose one of: "
                                 f"{', '.join(c['label'] for c in live_variants(record))}")
            _ensure_file(folder, cand)
            changes.append((folder, record, variant))
    for folder, record, variant in changes:
        choose(folder, record, variant)
    stored: dict[Path, dict] = {}
    for path, entry in data["files"].items():
        folder, record = by_path[path]
        chosen = choice_of(record)
        out = folder.parent
        book = stored.setdefault(out, _load(out))
        book["files"][path] = {"verdict": entry["verdict"], "variant": chosen["label"] if chosen else None,
                               "note": str(entry.get("note") or ""), "sha256": record["source"]["sha256"]}
    for out, book in stored.items():
        (out / VERDICTS_FILE).write_text(json.dumps(book, indent=1))
    return dict(Counter(e["verdict"] for e in data["files"].values()))


def verdict_of(out: Path, record: dict) -> dict | None:
    """The human's stored verdict for ``record``, while it still describes it (same source, same choice)."""
    entry = _load(out)["files"].get(record["source"]["path"])
    chosen = choice_of(record)
    if not entry or entry.get("sha256") != record["source"]["sha256"]:
        return None
    if entry["verdict"] in ("keep", "milder", "stronger") and entry.get("variant") != (chosen or {}).get("label"):
        return None
    return entry


def writes(record: dict) -> bool:
    """Whether the record has a choice to write: a verdict of "apply" and a variant."""
    return record["verdict"] == "apply" and bool(choice_of(record))


def is_accepted(record: dict, entry: dict | None) -> bool:
    """Whether `apply` writes ``record`` given its stored verdict ``entry`` (`verdict_of`): a lossy choice needs a
    "keep"; a lossless one needs no entry or a "keep". Reject, milder and stronger hold either back."""
    if not writes(record):
        return False
    verdict = entry["verdict"] if entry else None
    return verdict == "keep" or (verdict is None and choice_of(record)["kind"] != "lossy")


def manifest_rows(rows, *, accepted: bool = False) -> list[dict]:
    """Per file, where its chosen variant is and where it goes: the contract for an agent that places files
    itself. ``accepted`` keeps what `apply` would write (`is_accepted`). Largest saving first."""
    out = []
    for folder, record in rows:
        chosen = choice_of(record)
        entry = verdict_of(folder.parent, record)
        if accepted and not is_accepted(record, entry):
            continue
        variant = (Path(folder) / chosen["file"]) if chosen else None
        out.append({"source": record["source"]["path"], "target": record.get("target"),
                    "variant": str(variant) if variant else None, "exists": bool(variant and variant.is_file()),
                    "label": chosen["label"] if chosen else None, "kind": chosen["kind"] if chosen else None,
                    "size": chosen["size"] if chosen else None,
                    "saved": record["source"]["size"] - chosen["size"] if writes(record) else 0,
                    "gate": ("pass" if chosen.get("pass") else chosen.get("reason", "")) if chosen else None,
                    "scores": {k: chosen.get(k) for k in ("ssim", "ss2", "band")} if chosen else {},
                    "status": record["verdict"], "verdict": entry["verdict"] if entry else None})
    return sorted(out, key=lambda r: -r["saved"])

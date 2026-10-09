"""`report`: turn a run's rows.jsonl into per-category Markdown tables.

Everything is per job and per category, never one number for the whole corpus: the categories differ in what
the ladder can do to them, and a corpus-wide figure would only reflect how many files each category has.

The ablation answers "what would the saving be without this encoder?" from the recorded candidates: per file,
the smallest candidate that passed the gates and whose family is not dropped, under the same in-place rule
`imgopt_lib.gates.verdict` applies. It uses the gate pass alone (the Evidence SSIM was measured for the real
picks only), so its "All" column can differ slightly from the real picks' "Saved".
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from pathlib import Path

from imgopt_lib import gates as G

from . import harness

# Ablation column -> the candidate families that encoder contributes. jpegoptim covers its lossless rung too.
ABLATION_COLUMNS = (("jpegli", {"cjpegli"}), ("guetzli", {"guetzli"}),
                    ("jpegoptim", {"jpegoptim", "lossless-jpegoptim"}), ("zopfli", {"oxipng-zopfli"}),
                    ("pngquant", {"pngquant"}))
PALETTE_FAMILIES = {"pngquant"}  # the picks whose banding is gated; the others' banding is only reported
FLOOR_SSIM = 0.002
FLOOR_SS2 = 2.0


def load(run_dir: Path) -> list[dict]:
    """The rows of a run: what `report` and `review` read."""
    return [json.loads(line) for line in (Path(run_dir) / harness.ROWS).read_text().splitlines() if line.strip()]


def timing(run_dir: Path) -> dict:
    """The run's timing.json (tool versions, commit, per-category wall time), or {} when the run has none."""
    path = Path(run_dir) / harness.TIMING
    return json.loads(path.read_text()) if path.is_file() else {}


def _done(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("verdict") != "skipped"]


def _by(rows: list[dict], key: str) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r[key], []).append(r)
    return groups


def _after_ablation(row: dict, drop: set[str]) -> int:
    """The size the file ends at without the ``drop`` families: the smallest passing candidate left, applied
    only when `gates.verdict` says so (an in-place saving below max(1 KB, 1%) leaves the file alone)."""
    live = [c for c in row["candidates"] if c.get("pass") and c.get("size") is not None and c["family"] not in drop]
    chosen = min(live, key=lambda c: c["size"]) if live else None
    applied, _ = G.verdict(row["source_size"], chosen, in_place=row.get("in_place", True))
    return chosen["size"] if applied == "apply" else row["source_size"]


def ablate(rows: list[dict], drop: set[str]) -> dict[str, float]:
    """Per category, the share of bytes saved when the candidate families in ``drop`` are removed."""
    saved: dict[str, float] = {}
    for category, group in _by(_done(rows), "category").items():
        before = sum(r["source_size"] for r in group)
        saved[category] = (before - sum(_after_ablation(r, drop) for r in group)) / before if before else 0.0
    return saved


def _real_saved(group: list[dict]) -> float:
    before = sum(r["source_size"] for r in group)
    after = sum(r["pick_size"] if r.get("verdict") == "apply" and r.get("pick_size") is not None
                else r["source_size"] for r in group)
    return (before - after) / before if before else 0.0


def _pct(value: float) -> str:
    return f"{value:.1%}"


def _bytes(n: float) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1024:.0f} KB"


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _table(header: list[str], body: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    return lines + ["| " + " | ".join(row) + " |" for row in body] + [""]


def _scores(row: dict) -> dict:
    return row.get("pick_scores") or {}


def _lossy(row: dict) -> bool:
    return row.get("pick_kind") == "lossy"


def _summary(group: list[dict]) -> list[list[str]]:
    body = []
    for category, rows in _by(group, "category").items():
        done = _done(rows)
        families = Counter(r["pick_family"] for r in done if r.get("pick"))
        body.append([category, str(len(done)), str(len(rows) - len(done)),
                     str(sum(1 for r in done if not r.get("pick"))), _pct(_real_saved(done)),
                     ", ".join(f"{f} {n}" for f, n in families.most_common()) or "-",
                     f"{statistics.median(r['seconds'] for r in rows):.1f}",
                     _bytes(statistics.median(r["disk"] for r in done)) if done else "-"])
    return body


def _ablation(group: list[dict]) -> list[str]:
    present = {c["family"] for r in group for c in r["candidates"]}
    columns = [(name, families) for name, families in ABLATION_COLUMNS if present & families]
    if not columns:
        return []
    full = ablate(group, set())
    cuts = [ablate(group, families) for _, families in columns]
    body = [[category, _pct(full[category]), *(_pct(cut[category]) for cut in cuts)] for category in full]
    return _table(["Category", "All", *(f"−{name}" for name, _ in columns)], body)


def _floor(group: list[dict]) -> list[str]:
    body = []
    for category, rows in _by(group, "category").items():
        picks = [r for r in _done(rows)
                 if r.get("pick") and _lossy(r) and (r.get("gates") or {}).get("ssim") is not None]
        if not picks:
            continue
        at_floor = 0
        for r in picks:
            ssim, ss2 = _scores(r).get("ssim"), _scores(r).get("ss2")
            gates = r["gates"]
            near_ssim = ssim is not None and ssim - gates["ssim"] <= FLOOR_SSIM
            near_ss2 = ss2 is not None and gates.get("ss2") is not None and ss2 - gates["ss2"] <= FLOOR_SS2
            at_floor += near_ssim or near_ss2
        body.append([category, str(len(picks)), str(at_floor), _pct(at_floor / len(picks))])
    return _table(["Category", "Lossy picks", "At the floor", "Share"], body) if body else []


def _evidence(group: list[dict]) -> list[str]:
    body = []
    for category, rows in _by(group, "category").items():
        gaps = [abs(_scores(r)["ssim_evidence"] - _scores(r)["ssim"]) for r in _done(rows)
                if r.get("pick") and _scores(r).get("ssim_evidence") is not None
                and _scores(r).get("ssim") is not None]
        if gaps:
            body.append([category, str(len(gaps)), f"{max(gaps):.4f}", f"{statistics.median(gaps):.4f}"])
    return _table(["Category", "Picks", "Max", "Median"], body) if body else []


def _banding(group: list[dict]) -> list[str]:
    body = []
    for category, rows in _by(group, "category").items():
        bands = [_scores(r)["band"] for r in _done(rows)
                 if r.get("pick") and _lossy(r) and r["pick_family"] not in PALETTE_FAMILIES
                 and _scores(r).get("band") is not None]
        if bands:
            body.append([category, str(len(bands)), f"{statistics.median(bands):.2f}",
                         f"{_percentile(bands, 0.9):.2f}", f"{max(bands):.2f}"])
    return _table(["Category", "Picks", "Median", "p90", "Max"], body) if body else []


def _skip_reasons(rows: list[dict]) -> list[list[str]]:
    counts: Counter = Counter()
    for r in rows:
        if r.get("verdict") == "skipped":
            reason = r.get("reason", "")
            name = Path(r["file"]).name
            counts[(r["job"], reason.removeprefix(name + ": "))] += 1
    return [[job, reason, str(n)] for (job, reason), n in sorted(counts.items())]


def markdown(rows: list[dict]) -> str:
    """The run's tables, per job and category."""
    jobs = _by(rows, "job")
    out = ["Candidate files other than picks are pruned after measurement (each record keeps its metrics, "
           "reference, source copy and pick). Seconds per file are the category's wall time divided by its files; "
           "files run in parallel, so they are not single-file timings. \"Saved\" is the real picks' saving, the "
           "ablation's \"All\" the same rule applied to the gate pass alone (the Evidence SSIM was measured for "
           "the real picks only). Every number is per category.", ""]
    sections = [
        ("Per-category results", lambda g: _table(
            ["Category", "Files", "Skipped", "No pick", "Saved", "Pick families", "Median s/file", "Disk/file"],
            _summary(g))),
        ("Encoder ablations (saving with that encoder removed)", _ablation),
        ("Picks at the floor (lossy picks within 0.002 SSIM or 2 ss2 points of a floor)", _floor),
        ("Evidence vs gate SSIM (|Evidence SSIM − gate SSIM| of the picks)", _evidence),
        ("Banding on lossy non-palette picks (the input for calibrating JPEG/WebP/AVIF thresholds)", _banding),
    ]
    for title, build_section in sections:
        out += [f"## {title}", ""]
        found = False
        for job, group in jobs.items():
            lines = build_section(group)
            if lines:
                found = True
                out += [f"### {job} (profile {group[0]['profile']})", "", *lines]
        if not found:
            out += ["Nothing to report in this run.", ""]
    reasons = _skip_reasons(rows)
    out += ["## Skip reasons", ""]
    out += _table(["Job", "Reason", "Files"], reasons) if reasons else ["No file was skipped.", ""]
    return "\n".join(out)


def render(run_dir: Path) -> str:
    """The full report: a header naming the run, commit, corpus version and tool versions, then ``markdown``."""
    run_dir = Path(run_dir)
    meta = timing(run_dir)
    header = [f"# imgbench run {run_dir.name}", ""]
    for label, key in (("Commit", "commit"), ("Corpus version", "corpus_version"),
                       ("Files in parallel", "jobs_parallel")):
        if key in meta:
            header.append(f"- {label}: {meta[key]}")
    if meta.get("tools"):
        header.append(f"- {meta['tools']}")
    return "\n".join(header + ["", markdown(load(run_dir))]).rstrip("\n") + "\n"

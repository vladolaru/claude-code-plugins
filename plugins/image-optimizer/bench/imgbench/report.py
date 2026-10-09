"""`report`: turn a run's rows.jsonl into per-category Markdown tables.

Everything is per job and per category, never one number for the whole corpus: the categories differ in what
the ladder can do to them, and a corpus-wide figure would only reflect how many files each category has.

The ablation answers "what would the saving be without this encoder?" from the recorded candidates: per file,
the smallest candidate that passed the gates and whose family is not dropped, under the same in-place rule
`imgopt_lib.gates.verdict` applies. It reads the recorded gate pass, which already holds the Evidence check: a
smaller candidate whose Evidence SSIM failed is recorded pass=False. So its "All" column equals the real picks'
"Saved" in every job and category of a run (a tested invariant); `markdown` warns where they differ, because then
the ablation no longer replays imgopt's pick rule and its other columns cannot be trusted either.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from pathlib import Path

from imgopt_lib import candidates as C
from imgopt_lib import gates as G

from . import harness

# Ablation column -> the candidate families that encoder contributes. jpegoptim covers its lossless rung too.
ABLATION_COLUMNS = (("jpegli", {"cjpegli"}), ("guetzli", {"guetzli"}),
                    ("jpegoptim", {"jpegoptim", "lossless-jpegoptim"}), ("zopfli", {"oxipng-zopfli"}),
                    ("pngquant", {"pngquant"}))
SS2_PER_SSIM = 1000  # ss2 points per SSIM unit when the two margins are compared: 2 ss2 points = 0.002 SSIM
AT_FLOOR = 0.002  # a pick within this `floor_margin` of a floor is "at the floor"


def load(run_dir: Path) -> list[dict]:
    """The rows of a run: what `report` and `review` read. A row written before the harness carried the pick's
    ``identical`` and ``band_gated`` takes them from the record folder the run kept for it (see `_backfill`)."""
    rows = [json.loads(line) for line in (Path(run_dir) / harness.ROWS).read_text().splitlines() if line.strip()]
    return [_backfill(row, run_dir) for row in rows]


def _backfill(row: dict, run_dir: Path) -> dict:
    scores = row.get("pick_scores")
    if not row.get("pick") or scores is None or "identical" in scores:
        return row
    path = harness.record_dir(run_dir, row) / "metrics.json"
    chosen = C.pick_of(json.loads(path.read_text())) if path.is_file() else None
    if chosen is not None:
        scores.update(identical=bool(chosen.get("identical")), band_gated=bool(chosen.get("band_gated")))
    return row


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
    """Per category, the share of bytes saved when the candidate families in ``drop`` are removed. The rows must
    be one job's: two jobs over one category differ in profile and settings, and their sum means nothing."""
    jobs = {r.get("job") for r in rows}
    if len(jobs) > 1:
        raise ValueError(f"ablate one job's rows at a time; got {', '.join(sorted(map(str, jobs)))}")
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


def identical(row: dict) -> bool:
    """The pick is pixel-identical to the reference. Rows with no ``identical`` (written before the harness
    carried it, and with no record folder left) are read by the signature imgopt's `_perfect` stamps on such a
    candidate: SSIM exactly 1.0 and banding exactly 0.0."""
    scores = _scores(row)
    if "identical" in scores:
        return bool(scores["identical"])
    return scores.get("ssim") == 1.0 and scores.get("band") == 0.0


def band_gated(row: dict) -> bool:
    """The pick's banding was gated (palette output), not only reported. Unknown on a row that lacks it."""
    return bool(_scores(row).get("band_gated"))


def changes_pixels(row: dict) -> bool:
    """A lossy pick that is not pixel-identical: the picks the floor and banding tables describe and the review
    shows a human. imgopt files every rung that reads prepared pixels as kind "lossy", oxipng on resized pixels
    and cwebp-lossless included, though those keep every pixel."""
    return bool(row.get("pick")) and row.get("pick_kind") == "lossy" and not identical(row)


def _summary(group: list[dict]) -> list[list[str]]:
    body = []
    for category, rows in _by(group, "category").items():
        done = _done(rows)
        families = Counter(r["pick_family"] for r in done if r.get("pick"))
        errored = sum(1 for r in done if r.get("errored"))  # every candidate failed: a tool problem
        body.append([category, str(len(done)), str(len(rows) - len(done)),
                     str(sum(1 for r in done if not r.get("pick")) - errored), str(errored), _pct(_real_saved(done)),
                     ", ".join(f"{f} {n}" for f, n in families.most_common()) or "-",
                     f"{statistics.median(r['seconds'] for r in rows):.1f}",
                     _bytes(statistics.median(r["disk"] for r in done)) if done else "-"])
    return body


def _tried(rows: list[dict], families: set[str]) -> int:
    """How many of ``rows`` got at least one candidate of ``families``: an encoder capped out by size (guetzli
    above 6 MP, zopfli above 2 MP) or skipped (pngquant on few colours) was never tried on the others."""
    return sum(any(c["family"] in families for c in r["candidates"]) for r in rows)


def _ablation(group: list[dict]) -> list[str]:
    present = {c["family"] for r in group for c in r["candidates"]}
    columns = [(name, families) for name, families in ABLATION_COLUMNS if present & families]
    if not columns:
        return []
    full = ablate(group, set())
    cuts = [ablate(group, families) for _, families in columns]
    done = _by(_done(group), "category")
    body = []
    for category in full:
        cells = []
        for (_, families), cut in zip(columns, cuts):
            tried, files = _tried(done[category], families), len(done[category])
            if not tried:
                cells.append("not tried")  # dropping it changes nothing because it never ran here
            else:
                cells.append(_pct(cut[category]) + (f" (tried on {tried} of {files})" if tried < files else ""))
        body.append([category, _pct(full[category]), *cells])
    return _table(["Category", "All", *(f"−{name}" for name, _ in columns)], body)


def floor_margin(row: dict) -> float:
    """How far the pick sits above the nearest floor it was gated on, in SSIM units: the smaller of its SSIM
    margin and its ss2 margin divided by ``SS2_PER_SSIM``. The SSIM margin uses the lower of the gate SSIM and
    the Evidence SSIM, because `gates.evaluate` holds both to the SSIM floor (ffmpeg and Pillow decode JPEG up
    to about 1e-3 apart). Rows without an Evidence SSIM use the gate SSIM; a missing score or floor is left
    out, and a pick with no gated score at all is infinitely far (`inf`)."""
    scores, gates = row.get("pick_scores") or {}, row.get("gates") or {}
    margins = []
    measured = [s for s in (scores.get("ssim"), scores.get("ssim_evidence")) if s is not None]
    if measured and gates.get("ssim") is not None:
        margins.append(min(measured) - gates["ssim"])
    if scores.get("ss2") is not None and gates.get("ss2") is not None:
        margins.append((scores["ss2"] - gates["ss2"]) / SS2_PER_SSIM)
    return min(margins) if margins else math.inf


def _floor(group: list[dict]) -> list[str]:
    body = []
    for category, rows in _by(group, "category").items():
        picks = [r for r in _done(rows)
                 if changes_pixels(r) and (r.get("gates") or {}).get("ssim") is not None]
        if not picks:
            continue
        at_floor = sum(floor_margin(r) <= AT_FLOOR for r in picks)
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
                 if changes_pixels(r) and not band_gated(r) and _scores(r).get("band") is not None]
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


def _saving_mismatches(jobs: dict[str, list[dict]]) -> list[str]:
    """A line per job and category whose ablation "All" differs from the real picks' "Saved"."""
    lines = []
    for job, group in jobs.items():
        full = ablate(group, set())
        for category, rows in _by(_done(group), "category").items():
            real = _real_saved(rows)
            if not math.isclose(full[category], real, abs_tol=1e-9):
                lines.append(f"WARNING: {job}/{category}: the ablation's All ({_pct(full[category])}) differs from "
                             f"Saved ({_pct(real)}); the ablation does not replay imgopt's pick rule here, so its "
                             "columns cannot be trusted.")
    return lines


def markdown(rows: list[dict]) -> str:
    """The run's tables, per job and category."""
    jobs = _by(rows, "job")
    out = ["Candidate files other than picks are pruned after measurement (each record keeps its metrics, "
           "reference, source copy and pick). Seconds per file are the category's wall time divided by its files; "
           "files run in parallel, so they are not single-file timings. \"Saved\" is the real picks' saving; the "
           "ablation's \"All\" replays the pick rule on the recorded gate pass and must equal it. Pixel-identical "
           "picks (oxipng or cwebp-lossless on prepared pixels, which imgopt files as lossy) are left out of the "
           "floor and banding tables. Every number is per category.", ""]
    mismatches = _saving_mismatches(jobs)
    if mismatches:
        out += [*mismatches, ""]
    sections = [
        ("Per-category results", lambda g: _table(
            ["Category", "Files", "Skipped", "No pick", "Errored", "Saved", "Pick families", "Median s/file",
             "Disk/file"],
            _summary(g))),
        ("Encoder ablations (saving with that encoder removed; \"not tried\" where no file of the category got its "
         "candidates, \"tried on n of m\" where only some did)", _ablation),
        ("Picks at the floor (lossy picks within 0.002 SSIM, gate or Evidence, or 2 ss2 points of a floor)",
         _floor),
        ("Evidence vs gate SSIM (|Evidence SSIM − gate SSIM| of the picks)", _evidence),
        ("Banding on lossy picks whose banding is not gated (the input for calibrating JPEG/WebP/AVIF thresholds)",
         _banding),
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


def _missing_optional(meta: dict) -> str:
    if "jobs" not in meta:
        return "not recorded (the run predates the record; the tools line lists what was found)"
    missing = [f"{job}: {', '.join(j['missing_optional'])}" for job, j in meta["jobs"].items()
               if j.get("missing_optional")]
    return "; ".join(missing) or "none"


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
    header.append(f"- Optional tools missing: {_missing_optional(meta)}")
    return "\n".join(header + ["", markdown(load(run_dir))]).rstrip("\n") + "\n"

"""`review`: pick the lossy picks a human should judge at 1:1, and keep the verdict.

A lossless or pixel-identical pick keeps every pixel, so only a lossy pick that changes pixels and would be
applied is worth a human's eye. Within each (job, category) of a run, `sample` takes the picks closest to a floor
(the ones a gate barely let through, where a wrong floor shows first) plus one random pick from the rest, so the
review also catches a floor that is too tight everywhere. `assemble` builds, for one job, an imgopt working
folder holding copies of the sampled record folders and the comparison page `sheet` makes; `review-record` turns
the page's ticks into `verdicts.json`.

One folder per job, because a `run.json` carries one profile, resize and format and `load_records` keeps only the
records that match it. The folders live under `paths.bench_root()/review/<run name>/<job>/`, never in the run.
The page's `apply` command must never be run: apply would write the picks over the corpus files.
"""

from __future__ import annotations

import json
import math
import random
import shlex
import shutil
from collections import defaultdict
from pathlib import Path

from imgopt_lib import candidates as C
from imgopt_lib import gates as G
from imgopt_lib import sheet as S
from imgopt_lib import workdirs as W
from imgopt_lib.ladder import UsageError

from . import harness, report

SAMPLE = "sample.json"
VERDICTS = "verdicts.json"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "imgopt.py"


def is_reviewable(row: dict) -> bool:
    """A pick that would be applied and changes pixels (`report.changes_pixels`): what `sheet.needs_tiles`
    offers a tick for, less the pixel-identical picks no eye can fault."""
    return row.get("verdict") == "apply" and report.changes_pixels(row)


def sample(rows: list[dict], per_category: int = 3, seed: int = 1) -> list[dict]:
    """Per (job, category): the ``per_category`` reviewable rows closest to a floor (smallest `report.floor_margin`
    first, ties by file name), then one more chosen at random from the rest. Deterministic for a given ``seed``."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if is_reviewable(row):
            groups[(row.get("job"), row["category"])].append(row)
    chosen: list[dict] = []
    for (job, category), group in sorted(groups.items(), key=lambda kv: tuple(map(str, kv[0]))):
        ranked = sorted(group, key=lambda r: (report.floor_margin(r), r["file"]))
        chosen += ranked[:per_category]
        rest = ranked[per_category:]
        if rest:
            chosen.append(random.Random(f"{seed}/{job}/{category}").choice(rest))
    return chosen


def _slim(row: dict) -> dict:
    """What `sample.json` keeps of a row: enough to find it again and to judge a verdict against its gates."""
    distance = report.floor_margin(row)
    return {"job": row.get("job"), "category": row["category"], "file": row["file"], "pick": row["pick"],
            "pick_family": row.get("pick_family"), "pick_scores": row.get("pick_scores"), "gates": row.get("gates"),
            "distance": None if distance == math.inf else distance}


def page_of(dest: Path) -> Path:
    return Path(dest) / "_sheet" / "index.html"


def assemble(run_dir: Path, sampled: list[dict], dest: Path, *, script: Path | None = None,
             problems: list[str] | None = None) -> Path:
    """Build ``dest`` for the rows of one job: copies of their record folders, a `run.json` of that job's settings
    naming their sources, `sample.json`, and the `sheet` page. Returns ``dest``; `page_of` names the page.

    Raises UsageError when the rows span several jobs or one of their record folders is gone from the run."""
    jobs = {r["job"] for r in sampled}
    if len(jobs) != 1:
        raise UsageError(f"one review folder holds one job's picks (their settings differ); got {sorted(jobs)}")
    job = next((j for j in harness.JOBS if j.name == jobs.pop()), None)
    if job is None:
        raise UsageError(f"{sampled[0]['job']}: not a job of the harness")
    dest = Path(dest)
    W.mark(dest)
    for row in sampled:
        folder = harness.record_dir(run_dir, row)
        if not (folder / "metrics.json").is_file():
            raise UsageError(f"{folder}: no record folder in the run for {row['file']}")
        shutil.copytree(folder, dest / folder.name, dirs_exist_ok=True)
    opts = C.Options(profile=job.profile, out=dest, gates=G.gates_for(job.profile), resize=job.resize,
                     out_format=job.out_format)
    C.write_run(dest, [Path(r["file"]) for r in sampled], opts)
    (dest / SAMPLE).write_text(json.dumps([_slim(r) for r in sampled], indent=1))
    S.build(dest, problems=problems, script=script or SCRIPT)
    return dest


def assemble_run(run_dir: Path, review_dir: Path, *, per_category: int = 3, seed: int = 1,
                 script: Path | None = None) -> tuple[dict[str, Path], list[str]]:
    """Sample a run's rows and build one review folder per job under ``review_dir``. Returns ({job: folder},
    problems). Refuses before writing when a folder already holds verdicts: a rebuild would erase them."""
    rows = report.load(run_dir)
    by_job: dict[str, list[dict]] = defaultdict(list)
    for row in sample(rows, per_category, seed):
        by_job[row["job"]].append(row)
    kept = sorted(str(review_dir / job / VERDICTS) for job in by_job if (review_dir / job / VERDICTS).is_file())
    if kept:
        raise UsageError("these folders hold recorded verdicts that a rebuild would erase; move them first: "
                         + ", ".join(kept))
    problems: list[str] = []
    built: dict[str, Path] = {}
    for job, sampled in sorted(by_job.items()):
        dest = review_dir / job
        if dest.exists():
            W.clean(dest, keep_picks=False)
        built[job] = assemble(run_dir, sampled, dest, script=script, problems=problems)
    return built, problems


def approved_from(text: str) -> list[str]:
    """The paths approved in ``text``: a bare comma list, or the whole `apply ... --approve '<list>'` command the
    page prints (the value of its `--approve`)."""
    value = text.strip()
    if "--approve" in text:  # a pasted command is shell-quoted; a bare list is taken as it is (names may hold ')
        tokens = shlex.split(text)
        for i, token in enumerate(tokens):
            if token == "--approve" and i + 1 < len(tokens):
                value = tokens[i + 1]
            elif token.startswith("--approve="):
                value = token.split("=", 1)[1]
    return [p.strip() for p in value.split(",") if p.strip()]


def record(dest: Path, approved: list[str]) -> Path:
    """Write ``dest``/verdicts.json: every sampled file with `acceptable` true when it was approved. Raises
    UsageError for an approved path that is not in the sample (a tick from some other page)."""
    dest = Path(dest)
    sampled = json.loads((dest / SAMPLE).read_text())
    unknown = sorted(set(approved) - {s["file"] for s in sampled})
    if unknown:
        raise UsageError(f"not in {dest / SAMPLE}: {', '.join(unknown)}; paste the command of this folder's page")
    ticked = set(approved)
    out = dest / VERDICTS
    out.write_text(json.dumps([{**s, "acceptable": s["file"] in ticked} for s in sampled], indent=1))
    return out

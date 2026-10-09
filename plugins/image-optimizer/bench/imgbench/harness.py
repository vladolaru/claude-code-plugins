"""`run`: feed the corpus to `imgopt_lib.candidates.run` job by job and record one row per file.

The harness imports imgopt_lib directly, so it measures exactly what users run. For each job and each category
of that job it makes a new output folder `<run>/<job>/<category>/`, runs the ladder on the category's files,
reads the records back and appends one row per file to `rows.jsonl`: the pick, its scores, every candidate's
size and gate outcome, the seconds and the disk the record folder took. Right after the rows are written the
folder is pruned with `workdirs.clean(keep_picks=True)`, which leaves each record's metrics.json, reference,
source copy and pick: all `sheet` and the human review need, and a small part of the ladder's output (the full
ladder over this corpus does not fit on a laptop disk). `timing.json` carries the tool versions, the commit,
the corpus.json checksum, each job's settings and missing optional tools, and each category's wall time. Row
schema: see `_row`. `report.load` and `report.timing` are the one reader of both files; `report` and `review`
use nothing else of the run but the kept record folders.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from imgopt_lib import candidates as C
from imgopt_lib import gates as G
from imgopt_lib import tools as T
from imgopt_lib import workdirs as W
from imgopt_lib.formats import INPUT_FORMATS, format_of, subdir_name
from imgopt_lib.ladder import UsageError

from . import CORPUS_VERSION, build, manifest

ROWS = "rows.jsonl"
TIMING = "timing.json"
PHOTO_CATEGORIES = ("photo-camera", "photo-small", "phone-upload", "product-plain", "jpeg-recompressed")
SETTING = re.compile(r"-[qmc][\d-]+$")  # the setting suffix of a ladder label: -q55, -m85, -c64, -q70-95
SKIP_LINE = re.compile(r"^== (.+?): skipped: (.*)$", re.S)


@dataclass(frozen=True)
class Job:
    name: str
    categories: tuple[str, ...]
    profile: str
    resize: int | None = None
    out_format: str = "keep"

    @property
    def kind(self) -> str:
        """The job kind `tools.requirements` knows, chosen as the real `candidates` command chooses it."""
        if self.out_format != "keep":
            return "convert"
        return "prepare" if self.resize else "recompress"

    @property
    def in_place(self) -> bool:
        """What the job asks for, for skipped rows only: a row with a record takes imgopt's own decision."""
        return self.resize is None and self.out_format == "keep"


JOBS = [
    Job("recompress-high", tuple(c for c in build.CATEGORIES if c != "edge"), "high"),
    Job("recompress-medium", PHOTO_CATEGORIES, "medium"),
    Job("lossless", build.CATEGORIES, "lossless"),
    Job("prepare-catalog", ("photo-camera", "phone-upload", "png-master"), "high", resize=1200),
    Job("convert-webp", ("photo-camera", "product-plain", "screenshot", "illustration"), "high", out_format="webp"),
    Job("convert-jpeg", ("png-master", "illustration"), "high", out_format="jpeg"),
]


def family_of(label: str | None) -> str | None:
    """The encoder family of a ladder label: `cjpegli-q55` is `cjpegli`, `pngquant-q70-95` is `pngquant`;
    labels without a setting (`oxipng-zopfli`, `lossless-jpegoptim`, `cwebp-lossless`) are their own family."""
    return SETTING.sub("", label) if label else None


def new_run_dir(base: Path, date: str, sha: str) -> Path:
    """`<base>/<date>-<sha>/`, or with `-2`, `-3` when that exists; created empty."""
    base.mkdir(parents=True, exist_ok=True)
    name, n = f"{date}-{sha}", 1
    while (base / (name if n == 1 else f"{name}-{n}")).exists():
        n += 1
    folder = base / (name if n == 1 else f"{name}-{n}")
    folder.mkdir()
    return folder


def record_dir(run_dir: Path, row: dict) -> Path:
    """The record folder `run` keeps for a row: `<run>/<job>/<category>/<imgopt's subfolder of the file>/`."""
    return Path(run_dir) / row["job"] / row["category"] / subdir_name(Path(row["file"]))


def _row(job: Job, category: str, rel: str, record: dict, seconds: float, disk: int) -> dict:
    """One file's row. ``pick_scores`` also carries the pick's ``identical`` (imgopt stamps a pixel-identical
    candidate with perfect scores, and files oxipng or cwebp-lossless on prepared pixels as kind "lossy") and
    ``band_gated`` (whether its banding was gated or only reported)."""
    chosen = C.pick_of(record) or {}
    return {
        "job": job.name, "category": category, "profile": job.profile, "file": record["source"]["path"], "rel": rel,
        "source_size": record["source"]["size"],
        "in_place": record["target"] == record["source"]["path"] and not record["resize"],  # as candidates._process
        "verdict": record["verdict"], "reason": record["verdict_reason"],
        "pick": chosen.get("label"), "pick_family": family_of(chosen.get("label")), "pick_size": chosen.get("size"),
        "pick_kind": chosen.get("kind"),
        "pick_scores": {**{k: chosen.get(k) for k in ("ssim", "ssim_evidence", "ss2", "band", "butteraugli")},
                        "identical": bool(chosen.get("identical")), "band_gated": bool(chosen.get("band_gated"))},
        "gates": record["gates"], "notes": record["notes"], "errored": C.all_errored(record),
        "candidates": [{"label": c["label"], "family": family_of(c["label"]), "size": c.get("size"),
                        "pass": c["pass"], "ssim": c.get("ssim"), "ss2": c.get("ss2"), "band": c.get("band")}
                       for c in record["candidates"]],
        "seconds": seconds, "disk": disk,
    }


def _skipped_row(job: Job, category: str, rel: str, path: Path, why: str, seconds: float) -> dict:
    return {"job": job.name, "category": category, "profile": job.profile, "file": str(path), "rel": rel,
            "source_size": path.stat().st_size, "in_place": job.in_place, "verdict": "skipped", "reason": why,
            "pick": None, "pick_family": None, "candidates": [], "seconds": seconds, "disk": 0}


def _files(entries: list[manifest.Entry], corpus: Path, job: Job) -> dict[str, list[tuple[Path, str]]]:
    """Per category of the job, its (resolved path, corpus-relative path) pairs that exist and imgopt reads."""
    found: dict[str, list[tuple[Path, str]]] = {c: [] for c in job.categories}
    for e in sorted(entries, key=lambda e: e.path):
        path = (corpus / e.path).resolve()
        if e.category in found and path.is_file() and format_of(path) in INPUT_FORMATS:
            found[e.category].append((path, e.path))
    return {c: files for c, files in found.items() if files}


def _check_tools(plan: dict[Job, dict[str, list]], allow_missing) -> dict:
    """Resolve and check the tools of every job before anything is written; a blocked job raises ToolingError
    with the doctor report. Returns each job's `tools.Check`.

    imgopt never blocks on a missing optional encoder (cjpegli): it skips its rungs and notes it. A benchmark
    that did the same would silently measure a smaller ladder than users with the encoder run, so a missing
    optional encoder also raises ToolingError here unless ``allow_missing`` names it."""
    resolved: dict = {}
    for job, by_category in plan.items():
        formats = {format_of(path) for files in by_category.values() for path, _ in files}
        resolved[job] = T.ensure(job.kind, job.profile, formats, job.out_format)
    refused = {job.name: sorted(n for n in chk.missing_optional if n in T.OPTIONAL_ENCODERS and n not in allow_missing)
               for job, chk in resolved.items()}
    refused = {name: tools for name, tools in refused.items() if tools}
    if refused:
        names = sorted({n for tools in refused.values() for n in tools})
        raise T.ToolingError("; ".join(f"{job}: {', '.join(tools)}" for job, tools in refused.items())
                             + " not installed: imgopt would skip their rungs and the run would measure a smaller "
                             f"ladder. Put them on PATH, or pass --allow-missing {','.join(names)} to run without.")
    return resolved


def _run_category(job: Job, category: str, files: list[tuple[Path, str]], chk, out: Path,
                  jobs_parallel: int) -> tuple[list[dict], float, int]:
    """Run the ladder on one category. Returns (rows, wall seconds, pruned bytes)."""
    W.mark(out)
    skips: dict[str, str] = {}
    with open(out / "log.txt", "w") as log_file:
        def log(message: str) -> None:
            log_file.write(message + "\n")
            log_file.flush()
            found = SKIP_LINE.match(message.lstrip("\n"))
            if found:
                skips[str(Path(found.group(1)).resolve())] = found.group(2).strip()

        opts = C.Options(profile=job.profile, out=out, gates=G.gates_for(job.profile), resize=job.resize,
                         out_format=job.out_format, waived=chk.waived)
        started = time.monotonic()
        try:
            records = C.run([p for p, _ in files], opts, chk.tools, log=log, jobs=jobs_parallel)
        except UsageError as error:  # the request fits none of the files
            records = []
            for path, _ in files:
                skips[str(path)] = str(error)
        seconds = time.monotonic() - started
    per_file = seconds / len(files)
    by_path = {r["source"]["path"]: r for r in records}
    rows = []
    for path, rel in files:
        record = by_path.get(str(path))
        if record is not None:
            rows.append(_row(job, category, rel, record, per_file, W.folder_size(out / subdir_name(path))))
        else:
            rows.append(_skipped_row(job, category, rel, path, skips.get(str(path), "no record and no skip line"),
                                     per_file))
    return rows, seconds, W.clean(out, keep_picks=True)


def run(corpus: Path, run_dir: Path, jobs: list[Job], *, jobs_parallel: int = 1, say=print,
        meta: dict | None = None, allow_missing=()) -> Path:
    """Run ``jobs`` over the corpus into ``run_dir`` and return it. Raises ``tools.ToolingError`` before writing
    anything when a job's tools are missing, an optional encoder included unless ``allow_missing`` names it."""
    corpus, run_dir = Path(corpus).resolve(), Path(run_dir)
    entries = manifest.read(corpus)
    plan = {job: _files(entries, corpus, job) for job in jobs}
    checks = _check_tools(plan, set(allow_missing))
    tools = {name: tool for chk in checks.values() for name, tool in chk.tools.items()}
    timing = {**(meta or {}), "corpus_version": CORPUS_VERSION, "jobs_parallel": jobs_parallel,
              "tools": T.describe(tools),
              "corpus_sha256": hashlib.sha256((corpus / manifest.MANIFEST).read_bytes()).hexdigest(),
              "jobs": {job.name: {"profile": job.profile, "resize": job.resize, "format": job.out_format,
                                  "missing_optional": list(checks[job].missing_optional)} for job in plan},
              "categories": {}}
    run_dir.mkdir(parents=True, exist_ok=True)
    for job, by_category in plan.items():
        for category, files in by_category.items():
            say(f"{job.name}/{category}: {len(files)} file(s)")
            rows, seconds, pruned = _run_category(job, category, files, checks[job], run_dir / job.name / category,
                                                  jobs_parallel)
            with open(run_dir / ROWS, "a") as fh:
                fh.writelines(json.dumps(row) + "\n" for row in rows)
            timing["categories"][f"{job.name}/{category}"] = {
                "files": len(files), "skipped": sum(r["verdict"] == "skipped" for r in rows),
                "seconds": round(seconds, 2), "pruned_bytes": pruned}
            (run_dir / TIMING).write_text(json.dumps(timing, indent=1))
            say(f"  {seconds:.1f} s, {pruned / 1e6:.0f} MB of candidates pruned")
    return run_dir

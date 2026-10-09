"""argparse wiring for imgbench. Each command returns an exit code: 0 success, 1 problems found, 2 usage error."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from imgopt_lib import tools as T
from imgopt_lib.ladder import UsageError
from imgopt_lib.tools import ToolingError

from . import build, harness, manifest, paths, report, review, sources

SOURCES_FILE = paths.SOURCES_FILE


def _counts(found: list[sources.Source]) -> str:
    return ", ".join(f"{category}: {n}" for category, n in sorted(Counter(s.category for s in found).items()))


def _select_gpl() -> int:
    """Re-pick the GPL assets at the commits already pinned; every other entry stays exactly as it is."""
    listed = sources.load(SOURCES_FILE)
    fresh = sources.gpl_assets(commits=sources.pinned_commits(listed))
    chosen = sources.replace_category(listed, "gpl-asset", fresh)
    sources.save(chosen, SOURCES_FILE)
    print(f"wrote {SOURCES_FILE.name}: {_counts(chosen)}")
    return 0


def cmd_select(args) -> int:
    """Query Wikimedia, Kodak and the GPL repositories and pin the result in sources.json."""
    if args.only:
        if not SOURCES_FILE.is_file():
            print(f"{SOURCES_FILE.name} does not exist yet: run `select` without --only first")
            return 2
        return _select_gpl()
    if SOURCES_FILE.is_file() and not args.force:
        print(f"{SOURCES_FILE.name} already exists and holds the pins: `select --only gpl-asset` refreshes the GPL "
              "part alone, `select --force` starts over")
        return 2
    found = sources.camera_photos()
    print(f"photo-camera: {len(found)} found ({sources.PER_CATEGORY} wanted from each of "
          f"{len(sources.PHOTO_CAMERA_CATEGORIES)} categories)")
    candidates = sources.product_candidates()
    print(f"product-plain: {len(candidates)} candidates; keeping the first {sources.PLAIN_COUNT} "
          "with a plain background")
    plain = sources.pick_plain(candidates, sources.PLAIN_COUNT, paths.downloads_dir(), pause=1.0, log=print)
    print(f"product-plain: {len(plain)} kept")
    gpl = sources.gpl_assets()
    chosen = found + plain + sources.kodak() + gpl
    sources.save(chosen, SOURCES_FILE)
    print(f"wrote {SOURCES_FILE.name}: {_counts(chosen)}")
    return 0 if len(plain) == sources.PLAIN_COUNT else 1


def cmd_fetch(args) -> int:
    """Download the originals listed in sources.json, then pin a sha256 on the entries that had none."""
    listed = sources.load(SOURCES_FILE)
    dest = paths.downloads_dir()
    problems = sources.fetch(listed, dest, get=sources.download)
    fetched = [s for s in listed if (dest / s.category / s.key).is_file()]
    pinned = {(s.category, s.key): s for s in sources.pin_unpinned(fetched, dest)}
    sources.save([pinned.get((s.category, s.key), s) for s in listed], SOURCES_FILE)
    for problem in problems:
        print(problem)
    print(f"{len(fetched)} of {len(listed)} originals in {dest}")
    return 1 if problems else 0


def cmd_build(args) -> int:
    """Assemble the corpus and corpus.json; `--only` rebuilds just the named categories."""
    only = [c for c in args.only.split(",") if c] if args.only else None
    unknown = sorted(set(only or ()) - set(build.CATEGORIES))
    if unknown:
        print(f"unknown categories: {', '.join(unknown)}; choose from {', '.join(build.CATEGORIES)}")
        return 2
    return build.build(only)


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _commit() -> str:
    """The short sha of the checkout the benchmark runs from, or "nogit"."""
    try:
        proc = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return "nogit"
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "nogit"


def _selected_jobs(only_job: list[str], only_category: list[str]) -> list[harness.Job] | str:
    """The jobs to run (their categories narrowed by ``only_category``), or a usage message."""
    known = {j.name: j for j in harness.JOBS}
    unknown = sorted(set(only_job) - set(known))
    if unknown:
        return f"unknown jobs: {', '.join(unknown)}; choose from {', '.join(known)}"
    unknown = sorted(set(only_category) - set(build.CATEGORIES))
    if unknown:
        return f"unknown categories: {', '.join(unknown)}; choose from {', '.join(build.CATEGORIES)}"
    chosen = []
    for job in harness.JOBS:
        if only_job and job.name not in only_job:
            continue
        categories = tuple(c for c in job.categories if not only_category or c in only_category)
        if categories:
            chosen.append(harness.Job(job.name, categories, job.profile, job.resize, job.out_format))
    return chosen or "no job covers those categories (see the job table in bench/README.md)"


def cmd_run(args) -> int:
    """Run imgopt over the corpus into a new run folder; the full run takes hours."""
    chosen = _selected_jobs(_split(args.only_job), _split(args.only_category))
    if isinstance(chosen, str):
        print(chosen)
        return 2
    allowed = _split(args.allow_missing)
    if set(allowed) - T.OPTIONAL_ENCODERS:
        print(f"--allow-missing takes optional encoders only: {', '.join(sorted(T.OPTIONAL_ENCODERS))}")
        return 2
    corpus = paths.corpus_dir()
    if not (corpus / manifest.MANIFEST).is_file():
        print(f"{corpus} has no {manifest.MANIFEST}: run `build` first")
        return 2
    commit = _commit()
    run_dir = harness.new_run_dir(paths.runs_dir(), datetime.now(timezone.utc).strftime("%Y-%m-%d"), commit)
    print(f"run folder: {run_dir}")
    try:
        harness.run(corpus, run_dir, chosen, jobs_parallel=max(1, args.jobs_parallel), allow_missing=allowed,
                    meta={"commit": commit, "started": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    except ToolingError as error:
        print(error)
        if not any(run_dir.iterdir()):
            run_dir.rmdir()
        return 1
    print(f"done: {run_dir}")
    return 0


def cmd_report(args) -> int:
    """Print a run's Markdown report and write it to report.md in the run folder."""
    run_dir = Path(args.run_dir)
    if not (run_dir / harness.ROWS).is_file():
        print(f"{run_dir}: no {harness.ROWS}; give a run folder that `run` made")
        return 2
    text = report.render(run_dir)
    (run_dir / "report.md").write_text(text)
    print(text, end="")
    return 0


def cmd_review(args) -> int:
    """Build one comparison page per job from the lossy picks closest to a floor, for a human to judge."""
    run_dir = Path(args.run_dir)
    if not (run_dir / harness.ROWS).is_file():
        print(f"{run_dir}: no {harness.ROWS}; give a run folder that `run` made")
        return 2
    review_dir = paths.bench_root() / "review" / run_dir.resolve().name
    try:
        built, problems = review.assemble_run(run_dir, review_dir, per_category=args.per_category, seed=args.seed)
    except UsageError as error:
        print(error)
        return 2
    if not built:
        print("no lossy pick to review in this run")
        return 0
    for job, dest in built.items():
        print(f"{job}: {len(json.loads((dest / review.SAMPLE).read_text()))} picks: {review.page_of(dest)}")
    for problem in problems:
        print(f"  problem: {problem}", file=sys.stderr)
    print("Tick every pick that looks acceptable at 1:1, then paste the page's command here. "
          "Do not run it: apply would write the picks over the corpus files.")
    return 1 if problems else 0


def _approval_text(value: str) -> str:
    return sys.stdin.read() if value == "-" else value


def cmd_review_record(args) -> int:
    """Write verdicts.json for a review folder from the picks the human ticked."""
    dest = Path(args.dest)
    if not (dest / review.SAMPLE).is_file():
        print(f"{dest}: no {review.SAMPLE}; give a review folder that `review` made")
        return 2
    try:
        out = review.record(dest, review.approved_from(_approval_text(args.approve)))
    except (UsageError, ValueError) as error:  # ValueError: a command with an unclosed quote
        print(error)
        return 2
    verdicts = json.loads(out.read_text())
    print(f"{out}: {sum(v['acceptable'] for v in verdicts)} of {len(verdicts)} acceptable")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imgbench",
        description="Build the benchmark corpus and run imgopt on it: select, fetch, build, run, report, review, "
                    "review-record.")
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select", help="choose the real images and pin them in sources.json")
    select.add_argument("--only", choices=["gpl-asset"], help="re-pick only this category, merging into sources.json")
    select.add_argument("--force", action="store_true", help="overwrite an existing sources.json and its pins")
    select.set_defaults(func=cmd_select)
    commands.add_parser("fetch", help="download the pinned originals and verify their checksums").set_defaults(
        func=cmd_fetch)
    assemble = commands.add_parser("build", help="assemble the corpus and corpus.json from the fetched originals")
    assemble.add_argument("--only", metavar="CATEGORY,...", help="rebuild just these categories (the six photo "
                          "categories rebuild together); others keep their files and manifest entries")
    assemble.set_defaults(func=cmd_build)
    run = commands.add_parser("run", help="run imgopt over the corpus into a new run folder (takes hours)")
    run.add_argument("--jobs-parallel", type=int, default=2, metavar="N", help="files processed at once (default 2)")
    run.add_argument("--only-job", metavar="JOB,...", help="run just these jobs: "
                     + ", ".join(j.name for j in harness.JOBS))
    run.add_argument("--only-category", metavar="CATEGORY,...", help="narrow every job to these categories")
    run.add_argument("--allow-missing", metavar="ENCODER,...", help="run without these optional encoders ("
                     + ", ".join(sorted(T.OPTIONAL_ENCODERS)) + "); without it a missing one stops the run")
    run.set_defaults(func=cmd_run)
    summary = commands.add_parser("report", help="summarize a run into per-category Markdown tables")
    summary.add_argument("run_dir", help="a run folder made by `run`")
    summary.set_defaults(func=cmd_report)
    pages = commands.add_parser("review", help="build the 1:1 review pages (one per job) for a human to judge")
    pages.add_argument("run_dir", help="a run folder made by `run`")
    pages.add_argument("--per-category", type=int, default=3, metavar="N",
                       help="picks closest to a floor per job and category (default 3), plus one random pick")
    pages.add_argument("--seed", type=int, default=1, help="seed of the random pick (default 1)")
    pages.set_defaults(func=cmd_review)
    record = commands.add_parser("review-record", help="record the picks a human ticked as verdicts.json")
    record.add_argument("dest", help="a review folder made by `review` (one per job)")
    record.add_argument("--approve", required=True, metavar="LIST|COMMAND|-",
                        help="the comma list of approved paths, the page's whole pasted command, or - for stdin")
    record.set_defaults(func=cmd_review_record)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)

"""argparse wiring for imgopt. Each command returns an exit code:
0 success, 1 refused or verification failed, 2 tooling missing or usage error."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from . import applying as AP
from . import audit as A
from . import candidates as C
from . import compare as CP
from . import gates as G
from . import metrics as M
from . import sheet as S
from . import tools as T
from . import workdirs as W
from .formats import OUTPUT_FORMATS, WORKDIR_MARKER, expand_inputs, format_of, subdir_name
from .ladder import UsageError

SCRIPT = Path(__file__).resolve().parents[1] / "imgopt.py"
PROFILE_NAMES = ("lossless", "high", "medium")


def split_csv(value: str | None) -> tuple[str, ...]:
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


def _formats(paths) -> set[str]:
    return {format_of(p) for p in expand_inputs(paths)} if paths else set()


def cmd_doctor(args) -> int:
    chk = T.check(T.requirements(args.job, args.profile, _formats(args.paths) or None, args.format))
    print(T.report(chk, job=args.job, profile=args.profile))
    return 2 if chk.blocked else 0


def _job_for(args) -> str:
    if args.format != "keep":
        return "convert"
    return "prepare" if args.resize else "recompress"


def _git_top(folder: Path) -> Path | None:
    try:
        proc = subprocess.run(["git", "-C", str(folder), "rev-parse", "--show-toplevel"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None


def _refuse_out_inside_inputs(out: Path, paths) -> None:
    """--out inside an input's folder would be read as input next run, and inside a git work tree it is one
    `git add -A` from a commit; both are refused before anything is written."""
    for raw in paths:
        p = Path(raw).resolve()
        folder = p if p.is_dir() else p.parent
        if out.is_relative_to(folder):
            raise UsageError(f"--out {out} is inside the input folder {folder}; use `imgopt workdir <task>`")
        top = _git_top(folder)
        if top and out.is_relative_to(top):
            raise UsageError(f"--out {out} is inside the git work tree {top}; use `imgopt workdir <task>`")


def _refuse_unmarked_out(out: Path) -> None:
    """A later `clean` deletes any marked folder, so `candidates` marks only an empty folder or one it made."""
    if out.is_dir() and not (out / WORKDIR_MARKER).is_file() and any(out.iterdir()):
        raise UsageError(f"--out {out} already has files and was not made by `imgopt candidates`, which writes "
                         "only into an empty folder or one it made earlier; use `imgopt workdir <task>`")


def _baselines(inputs: list[Path], rev: str, out: Path) -> dict[Path, Path]:
    """Each input's content at ``rev``, extracted under out/_baselines (git show); UsageError naming a file
    the revision does not have."""
    if rev.startswith("-"):
        raise UsageError(f"--ref-rev {rev}: a revision may not start with '-'")
    W.mark(out)  # the folder holds our files from here on, even if the run stops before candidates marks it
    refs = {}
    for src in inputs:
        top = _git_top(src.parent)
        if top is None:
            raise UsageError(f"{src}: not inside a git work tree, so --ref-rev cannot find its baseline")
        rel = src.relative_to(top).as_posix()
        proc = subprocess.run(["git", "-C", str(top), "show", "--end-of-options", f"{rev}:{rel}"],
                              capture_output=True)
        if proc.returncode != 0:
            raise UsageError(f"{rel}: not found at {rev} ({proc.stderr.decode(errors='replace').strip()})")
        target = out / "_baselines" / subdir_name(src) / src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(proc.stdout)
        refs[src] = target
    return refs


def _expand_reporting(paths) -> list[Path]:
    """expand_inputs, saying on stderr what the folder walk left out."""
    skipped: list[str] = []
    files = expand_inputs(paths, skipped=skipped)
    if skipped:
        more = " ..." if len(skipped) > 5 else ""
        print(f"Skipped {len(skipped)} file(s) in folders: {'; '.join(skipped[:5])}{more}", file=sys.stderr)
    return files


def cmd_candidates(args) -> int:
    out = Path(args.out).resolve()
    _refuse_out_inside_inputs(out, args.paths)
    _refuse_unmarked_out(out)
    inputs = _expand_reporting(args.paths)
    if args.ref and len(inputs) != 1:
        raise ValueError("--ref works with exactly one input file")
    if (args.ref or args.ref_rev) and any(format_of(p) == "svg" for p in inputs):
        raise UsageError("--ref and --ref-rev do not apply to SVG: the rendering check compares svgo's output "
                         "with the source itself")
    waive = split_csv(args.allow_missing)
    chk = T.ensure(_job_for(args), args.profile, {format_of(p) for p in inputs}, args.format, waive)
    gates = G.gates_for(args.profile, ssim=args.ssim, ss2=args.ss2, band=args.band)
    refs = {}
    if args.ref:
        refs = {inputs[0]: Path(args.ref).resolve()}
    elif args.ref_rev:
        refs = _baselines(inputs, args.ref_rev, out)
    opts = C.Options(profile=args.profile, out=out, gates=gates, refs=refs, ref_rev=args.ref_rev,
                     resize=args.resize, out_format=args.format, waived=chk.waived)
    records = C.run(inputs, opts, chk.tools, script=SCRIPT, jobs=max(1, args.jobs))
    return 0 if len(records) == len(inputs) and not any(C.all_errored(r) for r in records) else 1


def cmd_inspect(args) -> int:
    files = _expand_reporting(args.paths)
    chk = T.ensure("audit", "lossless", {format_of(p) for p in files}, allow_missing=split_csv(args.allow_missing))
    with tempfile.TemporaryDirectory() as tmp:
        rows = [A.inspect_file(p, chk.tools, Path(tmp)) for p in files]
    if args.json:
        print(json.dumps(rows, indent=1))
        print(T.describe(chk.tools), file=sys.stderr)  # stdout stays parseable
    else:
        A.print_table(rows)
        print(T.describe(chk.tools))
    return 1 if any(r["error"] for r in rows) else 0


def _print_ignored(out: Path) -> None:
    """Name the record folders in ``out`` that are not part of the last run, so nobody wonders where they went."""
    for entry in C.stale_records(out):
        print(f"  ignored: {entry}")


def cmd_sheet(args) -> int:
    chk = T.check(T.Requirements(("pillow",), (), ()))
    if chk.blocked:
        raise T.ToolingError(T.report(chk, job="sheet", profile="-"))
    out = Path(args.out).resolve()
    _print_ignored(out)
    problems: list[str] = []
    tiles, page = S.build(out, alt=args.alt, problems=problems, script=SCRIPT)
    records = [r for _, r in C.load_records(out)]
    lossy = [r for r in records if S.needs_tiles(r)]
    untiled = len(lossy) - len({t.source for t in tiles})
    if tiles:
        print("Tiles (open each at its natural size; view every REQUIRED one before calling a pick good):")
        for t in tiles:
            print(f"  {'REQUIRED' if t.required else 'optional':8} {t.path}   {Path(t.source).name} [{t.window}]")
    if untiled:
        print(f"{untiled} lossy pick(s) could not be tiled (reasons on stderr); do not approve them unseen.")
    elif not lossy:
        print("No lossy picks: no tiles to view.")
    for problem in problems:
        print(f"  problem: {problem}", file=sys.stderr)
    quoted_page = shlex.quote(str(page))
    print(f"Page for the human: {page}   (macOS: open {quoted_page}; Linux: xdg-open {quoted_page})")
    failed = bool(problems)
    apply_command = f"python3 {shlex.quote(str(SCRIPT))} apply {shlex.quote(str(out))}"
    print(T.describe(chk.tools))
    if not failed and lossy:
        print("Next: the human ticks the picks they approve on the page, which shows the apply command "
              f"({apply_command} --approve ...).")
    elif not failed and any(r["verdict"] == "apply" for r in records):
        print(f"Next: {apply_command}   (all picks are lossless; no approval gate)")
    return 1 if failed else 0


def cmd_apply(args) -> int:
    out = Path(args.out).resolve()
    _print_ignored(out)
    only = split_csv(args.only)
    needed = ["pillow"]
    if AP.needs_metrics(out, only):  # also refuses an `out` that is not a folder of candidates results
        needed += ["ffmpeg", "ssimulacra2"]
    if AP.needs_rsvg(out, only):
        needed.append("rsvg-convert")
    chk = T.check(T.Requirements(tuple(needed), (), ()))
    if chk.blocked:
        raise T.ToolingError(T.report(chk, job="apply", profile="-"))
    print(T.describe(chk.tools))
    return AP.apply(out, tools=chk.tools, only=only, approve=split_csv(args.approve),
                    dest=Path(args.dest).resolve() if args.dest else None)


def cmd_compare(args) -> int:
    chk = T.ensure("compare", "high", None)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = CP.compare(args.ref, args.new, chk.tools, Path(tmp))
        except subprocess.CalledProcessError as error:  # git show on a bad revision or path
            print(f"error: {(error.stderr or b'').decode(errors='replace').strip() or error}", file=sys.stderr)
            return 2
        except (OSError, M.MetricError) as error:  # unreadable file, failed download, a tool that failed
            print(f"error: {error}", file=sys.stderr)
            return 2
        CP.print_result(result)
    print(T.describe(chk.tools))
    return 0


def cmd_workdir(args) -> int:
    print(W.workdir(args.task))
    return 0


def cmd_clean(args) -> int:
    freed = W.clean(Path(args.out).resolve(), keep_picks=args.keep_picks)
    print(f"Freed {freed / 1e6:.1f} MB.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imgopt",
        description="Measured image optimization: doctor, inspect, workdir, candidates, sheet, apply, compare, clean.")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor", help="check the tools a job needs and print install lines")
    d.add_argument("--job", choices=T.JOBS, required=True)
    d.add_argument("--profile", choices=PROFILE_NAMES, default="lossless")
    d.add_argument("--format", choices=OUTPUT_FORMATS, default="keep")
    d.add_argument("paths", nargs="*", help="narrow the check to the formats present")
    d.set_defaults(func=cmd_doctor)
    c = sub.add_parser("candidates", help="try encoder settings per file, measure, gate and pick")
    c.add_argument("paths", nargs="+", help="files or folders")
    c.add_argument("--out", required=True, help="working folder (from `imgopt workdir <task>`; never the repo)")
    c.add_argument("--profile", choices=PROFILE_NAMES, default="lossless")
    baseline = c.add_mutually_exclusive_group()
    baseline.add_argument("--ref", help="baseline to measure against (single input only)")
    baseline.add_argument("--ref-rev", metavar="REV", help="measure each input against its content at this git "
                          "revision (the original before an earlier pass)")
    c.add_argument("--resize", type=int, metavar="WIDTH", help="Lanczos resize to this width first")
    c.add_argument("--format", choices=OUTPUT_FORMATS, default="keep")
    c.add_argument("--ssim", type=float)
    c.add_argument("--ss2", type=float)
    c.add_argument("--band", type=float)
    c.add_argument("--allow-missing", help="comma list of quality tools the human chose to go without")
    c.add_argument("--jobs", type=int, default=2,
                   help="files measured in parallel (default 2)")
    c.set_defaults(func=cmd_candidates)
    i = sub.add_parser("inspect", help="facts and lossless headroom per file; changes nothing")
    i.add_argument("paths", nargs="+")
    i.add_argument("--json", action="store_true")
    i.add_argument("--allow-missing")
    i.set_defaults(func=cmd_inspect)
    s = sub.add_parser("sheet", help="1:1 tiles for the agent and a comparison page for the human")
    s.add_argument("out")
    s.add_argument("--alt", help="candidate label to show as a third pane, for example jpegoptim-m70")
    s.set_defaults(func=cmd_sheet)
    a = sub.add_parser("apply", help="write approved picks and re-verify them")
    a.add_argument("out")
    a.add_argument("--only", help="comma list of file names or paths")
    a.add_argument("--approve", help="comma list of lossy picks the human approved on the page (file names or paths)")
    a.add_argument("--dest", help="folder for prepare/convert outputs instead of next to the source")
    a.set_defaults(func=cmd_apply)
    m = sub.add_parser("compare", help="measure a pair and print a reviewer-runnable check")
    m.add_argument("ref", help="path, git:<rev>:<path>, or URL")
    m.add_argument("new", help="path, git:<rev>:<path>, or URL")
    m.set_defaults(func=cmd_compare)
    w = sub.add_parser("workdir", help="print (and create) a stable working folder for a task")
    w.add_argument("task")
    w.set_defaults(func=cmd_workdir)
    k = sub.add_parser("clean", help="delete a working folder, or with --keep-picks only its losing candidates")
    k.add_argument("out")
    k.add_argument("--keep-picks", action="store_true")
    k.set_defaults(func=cmd_clean)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except T.ToolingError as error:
        print(error, file=sys.stdout)
        return 2
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

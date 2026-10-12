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
from . import formats as F
from . import gates as G
from . import metrics as M
from . import review as RV
from . import selection as SEL
from . import sheet as S
from . import store as ST
from . import tools as T
from . import workdirs as W
from .formats import OUTPUT_FORMATS, WORKDIR_MARKER, command_line, expand_inputs, format_of, subdir_name
from .ladder import UsageError

SCRIPT = Path(__file__).resolve().parents[1] / "imgopt.py"
PROFILE_NAMES = ("lossless", "high", "medium")


def split_csv(value: str | None) -> tuple[str, ...]:
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


def parse_size(value: str) -> int:
    """Bytes from "3072", "3KB" or "3 kb"."""
    text = value.strip().lower().replace(" ", "")
    return int(float(text[:-2]) * 1024) if text.endswith("kb") else int(text)


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


def _refuse_out_inside_inputs(out: Path, paths) -> None:
    """--out inside an input folder would be walked as input next run, and inside the git work tree of any input
    it is one `git add -A` from a commit; both are refused before anything is written. A file input is not
    walked, so its folder may hold the working folder: ~/hero.png with the cache under ~/Library/Caches. A folder
    imgopt already marked (`workdir` made it, wherever the cache or its $TMPDIR fallback is) passes the git check:
    marking writes a .gitignore of `*`, so nothing in it is committed, even where the home folder is itself a git
    work tree (dotfiles). An unmarked folder inside a work tree may hold the repository's own files, so it is
    refused. git is asked once per folder."""
    marked = (out / WORKDIR_MARKER).is_file()
    tops: dict[Path, Path | None] = {}
    for raw in paths:
        p = Path(raw).resolve()
        folder = p if p.is_dir() else p.parent
        if p.is_dir() and out.is_relative_to(p):
            raise UsageError(f"--out {out} is inside the input folder {p}; use `imgopt workdir <task>`")
        if marked:
            continue
        if folder not in tops:
            tops[folder] = F.repo_root(folder)
        top = tops[folder]
        if top and out.is_relative_to(top):
            raise UsageError(f"--out {out} is inside the git work tree {top}; use `imgopt workdir <task>`")


def _refuse_unmarked_out(out: Path) -> None:
    """A later `clean` deletes any marked folder, so `candidates` marks only an empty folder or one imgopt made
    (`workdir`, or an earlier run)."""
    if out.is_dir() and not (out / WORKDIR_MARKER).is_file() and any(out.iterdir()):
        raise UsageError(f"--out {out} already has files and was not made by imgopt; `candidates` writes only into "
                         "an empty folder or one `imgopt workdir` or an earlier run made; use `imgopt workdir <task>`")


def _baselines(inputs: list[Path], rev: str, out: Path) -> dict[Path, Path]:
    """Each input's content at ``rev``, extracted under out/_baselines (git show); UsageError naming a file
    the revision does not have."""
    if rev.startswith("-"):
        raise UsageError(f"--ref-rev {rev}: a revision may not start with '-'")
    W.mark(out)  # the folder holds our files from here on, even if the run stops before candidates marks it
    refs = {}
    for src in inputs:
        top = F.repo_root(src.parent)
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
    paths = list(args.paths) + (SEL.read_list(args.files) if args.files else [])
    if not paths:
        raise UsageError("name the files: paths, folders, or --files <list>")
    out = Path(args.out).resolve()
    _refuse_out_inside_inputs(out, paths)
    _refuse_unmarked_out(out)
    inputs = _expand_reporting(paths)
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
                     resize=args.resize, out_format=args.format, waived=chk.waived,
                     min_saving=args.min_saving, assume_srgb=args.assume_srgb)
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
    outs = [Path(o).resolve() for o in args.out]
    for out in outs:
        _print_ignored(out)
    problems: list[str] = []
    tiles, page = S.build(outs, problems=problems)
    records = [r for _, r in RV.rows_of(outs)]
    lossy = [r for r in records if S.needs_tiles(r)]
    untiled = len(lossy) - len({t.source for t in tiles})
    if tiles:
        print("Tiles (open each at its natural size; view every REQUIRED one before calling a choice good):")
        for t in tiles:
            print(f"  {'REQUIRED' if t.required else 'optional':8} {t.path}   {Path(t.source).name} [{t.window}]")
    if untiled:
        print(f"{untiled} lossy choice(s) could not be tiled (reasons on stderr); do not keep them unseen.")
    elif not lossy:
        print("No lossy choices: no tiles to view.")
    for problem in problems:
        print(f"  problem: {problem}", file=sys.stderr)
    quoted_page = shlex.quote(str(page))
    print(f"Page for the human: {page}   (macOS: open {quoted_page}; Linux: xdg-open {quoted_page})")
    failed = bool(problems)
    print(T.describe(chk.tools))
    if not failed and lossy:
        print("Next: the human marks a verdict for each file on the page and copies the block; store it with: "
              + command_line(SCRIPT, "record", *outs, "--verdicts", "-"))
    elif not failed and any(r["verdict"] == "apply" for r in records):
        print(f"Next: {command_line(SCRIPT, 'apply', *outs)}   (lossless choices need no verdict)")
    return 1 if failed else 0


def cmd_apply(args) -> int:
    outs = [Path(o).resolve() for o in args.out]
    for out in outs:
        _print_ignored(out)
    only = split_csv(args.only) + tuple(SEL.read_list(args.files) if args.files else ())
    chk = T.check(T.Requirements(("pillow", *AP.tools_needed(outs, only)), (), ()))
    if chk.blocked:
        raise T.ToolingError(T.report(chk, job="apply", profile="-"))
    print(T.describe(chk.tools))
    code = AP.apply(outs, tools=chk.tools, only=only, dest=Path(args.dest).resolve() if args.dest else None)
    marked = [out for out in outs if (out / WORKDIR_MARKER).is_file()]
    if code == 0 and marked:
        waiting = AP.unplaced(outs, dest=Path(args.dest).resolve() if args.dest else None)
        if waiting:
            print(f"Next: {len(waiting)} file(s) still wait for a verdict, a new choice or their own `apply` "
                  "(outside --only); keep the working folder until each is placed or rejected.")
        else:
            size = sum(W.folder_size(out) for out in marked)
            cleans = "; ".join(command_line(SCRIPT, "clean", out) for out in marked)
            print(f"Next: every choice is placed or rejected. Once the evidence is written, offer the human to "
                  f"remove the working folder(s), which hold {size / 1e6:.1f} MB: {cleans} (or --keep-picks to keep "
                  "each file's record and choice)")
    return code


def cmd_manifest(args) -> int:
    rows = RV.manifest_rows(RV.rows_of([Path(o).resolve() for o in args.out]), accepted=args.accepted)
    if args.json:
        print(json.dumps(rows, indent=1))
        return 0
    total = sum(r["saved"] for r in rows) or 1
    running = 0
    for r in rows:
        running += r["saved"]
        state = "" if r["exists"] else "  (file gone: run choose to restore it)"
        note = f"  {C.removes(r)}" if C.removes(r) else ""  # a row carries metadata_removed as a candidate does
        print(f"{C.kb(r['saved']):>9} {running / total:>4.0%}  {r['verdict'] or '-':8} {r['label'] or '-':20} "
              f"{r['variant']} -> {r['target']}{note}{state}")
    return 0


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
    """Prints the folder alone on stdout, which `$(imgopt workdir <task>)` captures; working folders forgotten
    beside it are named on stderr."""
    folder = W.workdir(args.task)
    print(folder)
    stale = W.forgotten(folder)
    if stale:
        total = sum(size for _, size in stale)
        print(f"note: {len(stale)} other working folder(s) untouched for {W.FORGOTTEN_DAYS} days or more hold "
              f"{total / 1e6:.1f} MB. Each may hold files not yet placed, so offer the human their removal:",
              file=sys.stderr)
        for stale_folder, size in stale:
            print(f"  {command_line(SCRIPT, 'clean', stale_folder)}   ({size / 1e6:.1f} MB)", file=sys.stderr)
    return 0


def cmd_clean(args) -> int:
    if args.store:
        removed, freed, left = ST.prune(max_age_days=ST.MAX_AGE_DAYS if args.older_than is None else args.older_than)
        print(f"Pruned {removed} variant(s), freed {freed / 1e6:.1f} MB; the store holds {left / 1e6:.1f} MB.")
        return 0
    if not args.out:
        raise UsageError("name the working folder, or pass --store")
    freed = W.clean(Path(args.out).resolve(), keep_picks=args.keep_picks)
    print(f"Freed {freed / 1e6:.1f} MB.")
    return 0


def _one(dirs, name):
    """The one ``(folder, record)`` that ``name`` selects among the records of the working folders ``dirs``."""
    rows = RV.rows_of([Path(d).resolve() for d in dirs])
    picked, problems = SEL.resolve([name], rows)
    if problems:
        raise UsageError(problems[0])
    return picked[0]


def cmd_variants(args) -> int:
    folder, record = _one(args.out, args.file)
    for line in RV.variant_lines(folder, record):
        print(line)
    return 0


def cmd_choose(args) -> int:
    how = args.label or ("milder" if args.milder else "stronger" if args.stronger
                         else "default" if args.default else "")
    if not how:
        raise UsageError("name a variant with --label, or pass --milder, --stronger or --default")
    folder, record = _one(args.out, args.file)
    chosen = RV.choose(folder, record, how)
    if chosen is None:
        print(f"{record['source']['path']}: no variant (nothing passed the gates)")
    else:
        gate = "passes the gates" if chosen.get("pass") else f"FAILS the gates ({chosen.get('reason', '')})"
        note = f"; {C.removes(chosen)} (tell the human)" if C.removes(chosen) else ""
        print(f"{record['source']['path']}: now {chosen['label']} ({C.kb(chosen['size'])}), {gate}; "
              f"status: {record['verdict']} ({record['verdict_reason']}){note}")
    print(f"Next: rebuild the page: {command_line(SCRIPT, 'sheet', *(Path(o).resolve() for o in args.out))}")
    return 0


def cmd_record(args) -> int:
    text = SEL.read_text(args.verdicts)
    data = RV.parse_block(text)
    dirs = RV.page_folders(data, args.out)
    # The variant the page built 1:1 crops of, per file: its choice, when that was a lossy file to write.
    tiled = {r["source"]["path"]: C.choice_of(r)["file"] for _, r in RV.rows_of(dirs) if S.needs_tiles(r)}
    counts = RV.record_verdicts(dirs, text)
    print("Recorded: " + (", ".join(f"{n} {v}" for v, n in sorted(counts.items())) or "no verdicts"))
    # A kept lossy file to write that the page has no crops of: another variant than the page's choice, or one
    # the page saw untouched (a keep under the saving floor).
    unseen = []
    for _, record in RV.rows_of(dirs):
        path, chosen = record["source"]["path"], C.choice_of(record)
        if data["files"].get(path, {}).get("verdict") != "keep":
            continue
        if not RV.writes(record):
            print(f"  kept, but nothing to write: {path} ({record['verdict_reason']})")
        elif S.needs_tiles(record) and chosen["file"] != tiled.get(path):
            unseen.append((path, chosen))
    for path, chosen in unseen:
        print(f"  kept without its 1:1 crops: {path} ({chosen['label']})")
    if unseen:
        print(f"  view them before placing: {command_line(SCRIPT, 'sheet', *dirs)}")
    asked = [(f, r) for f, r in RV.rows_of(dirs)
             if (RV.verdict_of(f.parent, r) or {}).get("verdict") in ("milder", "stronger")]
    for folder, record in asked:
        how = RV.verdict_of(folder.parent, record)["verdict"]
        print(f"  asked {how}: {command_line(SCRIPT, 'choose', *dirs, '--file', record['source']['path'], '--' + how)}")
    if asked:
        print(f"Then rebuild the page: {command_line(SCRIPT, 'sheet', *dirs)}")
    if counts.get("keep"):
        print(f"Place the kept files: {command_line(SCRIPT, 'apply', *dirs)}   "
              f"(or copy the paths: {command_line(SCRIPT, 'manifest', *dirs, '--accepted')})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imgopt",
        description="Measured image optimization: doctor, inspect, workdir, candidates, variants, choose, sheet, "
                    "record, apply, manifest, compare, clean.")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor", help="check the tools a job needs and print install lines")
    d.add_argument("--job", choices=T.JOBS, required=True)
    d.add_argument("--profile", choices=PROFILE_NAMES, default="lossless")
    d.add_argument("--format", choices=OUTPUT_FORMATS, default="keep")
    d.add_argument("paths", nargs="*", help="narrow the check to the formats present")
    d.set_defaults(func=cmd_doctor)
    c = sub.add_parser("candidates", help="try encoder settings per file, measure, gate and pick")
    c.add_argument("paths", nargs="*", help="files or folders")
    c.add_argument("--files", metavar="LIST", help="a file listing one path per line (- for stdin): the file set")
    c.add_argument("--out", required=True, help="working folder (from `imgopt workdir <task>`; never the repo)")
    c.add_argument("--profile", choices=PROFILE_NAMES, default="lossless")
    baseline = c.add_mutually_exclusive_group()
    baseline.add_argument("--ref", help="baseline to measure against (single input only)")
    baseline.add_argument("--ref-rev", metavar="REV", help="measure each input against its content at this git "
                          "revision (the original before an earlier pass)")
    c.add_argument("--resize", type=int, metavar="WIDTH", help="Lanczos resize to this width first")
    c.add_argument("--format", choices=OUTPUT_FORMATS, default="keep")
    c.add_argument("--min-saving", type=parse_size, default=0, metavar="BYTES",
                   help="leave files that save less than this untouched (bytes, or 3KB)")
    c.add_argument("--assume-srgb", action="store_true",
                   help="treat PNG colour chunks (cICP, gAMA, cHRM) as sRGB so lossy candidates are tried; only "
                        "for a file the human compared in a browser, run as its own set")
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
    s = sub.add_parser("sheet", help="1:1 tiles for the agent and a review page for the human")
    s.add_argument("out", nargs="+", help="working folder(s); one page covers them all")
    s.set_defaults(func=cmd_sheet)
    v = sub.add_parser("variants", help="list every measured variant of one file")
    v.add_argument("out", nargs="+", help="working folder(s)")
    v.add_argument("--file", required=True, help="the file: path, relative path, or unique name")
    v.set_defaults(func=cmd_variants)
    ch = sub.add_parser("choose", help="set one file's variant: --label L, --milder, --stronger or --default")
    ch.add_argument("out", nargs="+", help="working folder(s)")
    ch.add_argument("--file", required=True, help="the file: path, relative path, or unique name")
    how = ch.add_mutually_exclusive_group()
    how.add_argument("--label", help="a variant label from `imgopt variants`")
    how.add_argument("--milder", action="store_true")
    how.add_argument("--stronger", action="store_true")
    how.add_argument("--default", action="store_true")
    ch.set_defaults(func=cmd_choose)
    rc = sub.add_parser("record", help="store the verdicts the human copied from the page")
    rc.add_argument("out", nargs="*", help="the working folder(s) the page was built from (default: the ones "
                    "the block names)")
    rc.add_argument("--verdicts", required=True, metavar="FILE|-", help="the pasted block, in a file or on stdin")
    rc.set_defaults(func=cmd_record)
    a = sub.add_parser("apply", help="write the choices the verdicts keep and re-verify them")
    a.add_argument("out", nargs="+")
    a.add_argument("--only", help="comma list of file names or paths")
    a.add_argument("--files", metavar="LIST", help="like --only, one path per line (- for stdin)")
    a.add_argument("--dest", help="folder for prepare/convert outputs instead of next to the source")
    a.set_defaults(func=cmd_apply)
    mf = sub.add_parser("manifest", help="each file's chosen variant and target: paths an agent can copy")
    mf.add_argument("out", nargs="+")
    mf.add_argument("--json", action="store_true")
    mf.add_argument("--accepted", action="store_true",
                    help="only what apply would write: lossy choices kept, lossless ones kept or with no verdict")
    mf.set_defaults(func=cmd_manifest)
    m = sub.add_parser("compare", help="measure a pair and print a reviewer-runnable check")
    m.add_argument("ref", help="path, git:<rev>:<path>, or URL")
    m.add_argument("new", help="path, git:<rev>:<path>, or URL")
    m.set_defaults(func=cmd_compare)
    w = sub.add_parser("workdir", help="print (and create) a stable working folder for a task")
    w.add_argument("task")
    w.set_defaults(func=cmd_workdir)
    k = sub.add_parser("clean", help="delete a working folder, or with --keep-picks only its unchosen variants "
                       "(each file's pick and choice stay)")
    k.add_argument("out", nargs="?", help="the working folder (omit with --store)")
    k.add_argument("--keep-picks", action="store_true")
    k.add_argument("--store", action="store_true", help="prune the shared variant store instead of a folder")
    k.add_argument("--older-than", type=float, metavar="DAYS",
                   help=f"with --store: drop variants unused for this many days (default {ST.MAX_AGE_DAYS})")
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

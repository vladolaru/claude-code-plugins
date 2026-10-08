"""argparse wiring for imgopt. Each command returns an exit code:
0 success, 1 refused or verification failed, 2 tooling missing or usage error."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import audit as A
from . import candidates as C
from . import gates as G
from . import sheet as S
from . import tools as T
from .formats import OUTPUT_FORMATS, expand_inputs, format_of
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


def cmd_candidates(args) -> int:
    inputs = expand_inputs(args.paths)
    if args.ref and len(inputs) != 1:
        raise ValueError("--ref works with exactly one input file")
    if args.ref and any(format_of(p) == "svg" for p in inputs):
        raise UsageError("--ref does not apply to SVG: the rendering check compares svgo's output "
                         "with the source itself")
    waive = split_csv(args.allow_missing)
    chk = T.ensure(_job_for(args), args.profile, {format_of(p) for p in inputs}, args.format, waive)
    gates = G.gates_for(args.profile, ssim=args.ssim, ss2=args.ss2, band=args.band)
    opts = C.Options(profile=args.profile, out=Path(args.out).resolve(), gates=gates,
                     ref=Path(args.ref).resolve() if args.ref else None, resize=args.resize,
                     out_format=args.format, waived=chk.waived)
    records = C.run(inputs, opts, chk.tools, script=SCRIPT)
    return 0 if len(records) == len(inputs) else 1


def cmd_inspect(args) -> int:
    files = expand_inputs(args.paths)
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


def cmd_sheet(args) -> int:
    chk = T.check(T.Requirements(("pillow", "chrome") if args.browser else ("pillow",), (), ()))
    if chk.blocked:
        raise T.ToolingError(T.report(chk, job="sheet", profile="-"))
    problems: list[str] = []
    tiles, page = S.build(Path(args.out).resolve(), alt=args.alt, problems=problems)
    if tiles:
        print("Tiles (open each at its natural size; view every REQUIRED one before calling a pick good):")
        for t in tiles:
            print(f"  {'REQUIRED' if t.required else 'optional':8} {t.path}   {Path(t.source).name} [{t.window}]")
    else:
        print("No lossy picks: no tiles to view.")
    for problem in problems:
        print(f"  skipped: {problem}", file=sys.stderr)
    print(f"Page for the human: {page}   (macOS: open '{page}'; Linux: xdg-open '{page}')")
    failed = bool(problems)
    if args.browser:
        try:
            print(f"Screenshot at 2x: {S.screenshot(page, chk.tools['chrome'].path)}")
        except RuntimeError as error:
            print(f"error: {error}", file=sys.stderr)
            failed = True
    print(T.describe(chk.tools))
    print(f"Next: after the human approves on the page, python3 {SCRIPT} apply {Path(args.out).resolve()} --approved")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imgopt",
        description="Measured image optimization: doctor, inspect, candidates, sheet, apply, compare.")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor", help="check the tools a job needs and print install lines")
    d.add_argument("--job", choices=T.JOBS, required=True)
    d.add_argument("--profile", choices=PROFILE_NAMES, default="lossless")
    d.add_argument("--format", choices=OUTPUT_FORMATS, default="keep")
    d.add_argument("paths", nargs="*", help="narrow the check to the formats present")
    d.set_defaults(func=cmd_doctor)
    c = sub.add_parser("candidates", help="try encoder settings per file, measure, gate and pick")
    c.add_argument("paths", nargs="+", help="files or folders")
    c.add_argument("--out", required=True, help="working folder (scratchpad, never the repo)")
    c.add_argument("--profile", choices=PROFILE_NAMES, default="lossless")
    c.add_argument("--ref", help="baseline to measure against (single input only)")
    c.add_argument("--resize", type=int, metavar="WIDTH", help="Lanczos resize to this width first")
    c.add_argument("--format", choices=OUTPUT_FORMATS, default="keep")
    c.add_argument("--ssim", type=float)
    c.add_argument("--ss2", type=float)
    c.add_argument("--band", type=float)
    c.add_argument("--allow-missing", help="comma list of quality tools the human chose to go without")
    c.set_defaults(func=cmd_candidates)
    i = sub.add_parser("inspect", help="facts and lossless headroom per file; changes nothing")
    i.add_argument("paths", nargs="+")
    i.add_argument("--json", action="store_true")
    i.add_argument("--allow-missing")
    i.set_defaults(func=cmd_inspect)
    s = sub.add_parser("sheet", help="1:1 tiles for the agent and a comparison page for the human")
    s.add_argument("out")
    s.add_argument("--alt", help="candidate label to show as a third pane, for example jpegoptim-m70")
    s.add_argument("--browser", action="store_true", help="also screenshot the page at 2x with headless Chrome")
    s.set_defaults(func=cmd_sheet)
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

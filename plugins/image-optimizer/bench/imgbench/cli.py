"""argparse wiring for imgbench. Each command returns an exit code: 0 success, 1 problems found, 2 usage error."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imgbench",
        description="Build the benchmark corpus and run imgopt on it: select, fetch, build, run, report, review.")
    parser.add_subparsers(dest="command", required=True)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)

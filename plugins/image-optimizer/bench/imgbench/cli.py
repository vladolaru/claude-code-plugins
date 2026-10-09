"""argparse wiring for imgbench. Each command returns an exit code: 0 success, 1 problems found, 2 usage error."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from . import paths, sources

SOURCES_FILE = Path(__file__).resolve().parents[1] / "sources.json"


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imgbench",
        description="Build the benchmark corpus and run imgopt on it: select, fetch, build, run, report, review.")
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select", help="choose the real images and pin them in sources.json")
    select.add_argument("--only", choices=["gpl-asset"], help="re-pick only this category, merging into sources.json")
    select.add_argument("--force", action="store_true", help="overwrite an existing sources.json and its pins")
    select.set_defaults(func=cmd_select)
    commands.add_parser("fetch", help="download the pinned originals and verify their checksums").set_defaults(
        func=cmd_fetch)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)

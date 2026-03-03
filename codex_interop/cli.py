#!/usr/bin/env python3
"""Codex interop generator CLI.

Phase 1 implements project and installed-plugin discovery only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PACKAGE_PARENT = Path(__file__).resolve().parent.parent
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from codex_interop.discover import discover
from codex_interop.model import DiscoveryError


DISCOVERY_ONLY_COMMANDS = {"reconcile", "dry-run", "diff"}


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--project",
        type=Path,
        help="Project path to resolve instead of the current working directory.",
    )
    common.add_argument(
        "--cache-dir",
        type=Path,
        help="Override the Claude plugin cache path (mainly for testing).",
    )

    parser = argparse.ArgumentParser(
        description="Generate Codex interop artifacts from installed Claude Code plugins.",
        parents=[common],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("reconcile", "validate", "dry-run", "diff"):
        subparsers.add_parser(command, parents=[common])

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = discover(project_path=args.project, cache_dir=args.cache_dir)
    except DiscoveryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.command == "validate":
        _print_summary(result)
        return 0

    _print_summary(result)
    print(
        f"Phase 1 complete: discovery works, but `{args.command}` generation is not "
        "implemented yet.",
        file=sys.stderr,
    )
    return 2


def _print_summary(result) -> None:
    """Print a human-readable discovery summary."""
    print(f"PROJECT_ROOT: {result.project.root}")
    print(f"AGENTS_MD: {result.project.agents_md_path}")
    print(f"PLUGINS_FOUND: {len(result.plugins)}")
    for plugin in result.plugins:
        print(
            "PLUGIN: "
            f"{plugin.marketplace}/{plugin.plugin_name}@{plugin.version_text} "
            f"source={plugin.source_path} "
            f"skills={len(plugin.skills)} "
            f"agents={len(plugin.agents)}"
        )


if __name__ == "__main__":
    sys.exit(main())

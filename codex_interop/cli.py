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

from codex_interop.claude_shim import plan_claude_shim
from codex_interop.discover import discover
from codex_interop.install_launchagent import (
    DEFAULT_START_INTERVAL,
    build_launchagent_label,
    build_launchagent_plist,
    install_launchagent,
)
from codex_interop.model import DiscoveryError, ReconcileError, TranslationError
from codex_interop.reconcile import (
    build_desired_state,
    diff_desired_state,
    format_change_report,
    format_diff_report,
    reconcile_desired_state,
)
from codex_interop.render_codex_config import render_inline_codex_config, render_prompt_files
from codex_interop.translate_agents import translate_installed_agents
from codex_interop.translate_skills import translate_installed_skills


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
    common.add_argument(
        "--codex-home",
        type=Path,
        help="Override the Codex home path (mainly for testing).",
    )

    parser = argparse.ArgumentParser(
        description="Generate Codex interop artifacts from installed Claude Code plugins.",
        parents=[common],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("reconcile", "validate", "dry-run", "diff"):
        subparsers.add_parser(command, parents=[common])

    launchagent_common = argparse.ArgumentParser(add_help=False)
    launchagent_common.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_START_INTERVAL,
        help="LaunchAgent StartInterval in seconds.",
    )
    launchagent_common.add_argument(
        "--label",
        help="Override the generated LaunchAgent label.",
    )
    launchagent_common.add_argument(
        "--python-executable",
        type=Path,
        help="Override the Python executable used by the LaunchAgent.",
    )
    launchagent_common.add_argument(
        "--cli-path",
        type=Path,
        help="Override the CLI script path used by the LaunchAgent.",
    )
    launchagent_common.add_argument(
        "--logs-dir",
        type=Path,
        help="Override the LaunchAgent log directory.",
    )

    subparsers.add_parser("print-launchagent", parents=[common, launchagent_common])
    install_parser = subparsers.add_parser("install-launchagent", parents=[common, launchagent_common])
    install_parser.add_argument(
        "--launchagents-dir",
        type=Path,
        help="Override the LaunchAgents destination directory.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in {"print-launchagent", "install-launchagent"}:
        return _handle_launchagent_command(args)

    try:
        result = discover(project_path=args.project, cache_dir=args.cache_dir)
        shim_decision = plan_claude_shim(result.project)
        roles = translate_installed_agents(result.plugins)
        skills = translate_installed_skills(result.plugins)
        prompt_files = render_prompt_files(roles)
        rendered_config = render_inline_codex_config(roles)
        desired_state = build_desired_state(
            result,
            shim_decision,
            prompt_files,
            rendered_config,
            skills,
            codex_home=args.codex_home,
        )
    except (DiscoveryError, TranslationError, ReconcileError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.command == "validate":
        _print_summary(
            result,
            shim_decision.action,
            len(roles),
            len(prompt_files),
            len(skills),
            rendered_config,
        )
        return 0

    try:
        if args.command == "reconcile":
            report = reconcile_desired_state(desired_state)
            _print_summary(
                result,
                shim_decision.action,
                len(roles),
                len(prompt_files),
                len(skills),
                rendered_config,
            )
            print(format_change_report(report))
            return 0

        if args.command == "dry-run":
            report = diff_desired_state(desired_state)
            _print_summary(
                result,
                shim_decision.action,
                len(roles),
                len(prompt_files),
                len(skills),
                rendered_config,
            )
            print(format_change_report(report))
            return 0

        if args.command == "diff":
            report = diff_desired_state(desired_state)
            _print_summary(
                result,
                shim_decision.action,
                len(roles),
                len(prompt_files),
                len(skills),
                rendered_config,
            )
            print(format_diff_report(desired_state, report))
            return 0
    except ReconcileError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    _print_summary(
        result,
        shim_decision.action,
        len(roles),
        len(prompt_files),
        len(skills),
        rendered_config,
    )
    print(f"Error: unsupported command `{args.command}`", file=sys.stderr)
    return 1


def _handle_launchagent_command(args: argparse.Namespace) -> int:
    """Handle LaunchAgent rendering or installation commands."""
    try:
        project = args.project or Path.cwd()
        resolved_project = project.expanduser().resolve()
        label = args.label or build_launchagent_label(resolved_project)
        plist_bytes = build_launchagent_plist(
            project_root=resolved_project,
            interval_seconds=args.interval,
            cache_dir=args.cache_dir,
            codex_home=args.codex_home,
            python_executable=args.python_executable,
            cli_path=args.cli_path,
            label=label,
            logs_dir=args.logs_dir,
        )
    except ReconcileError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.command == "print-launchagent":
        sys.stdout.buffer.write(plist_bytes)
        return 0

    destination = install_launchagent(
        plist_bytes,
        label=label,
        launchagents_dir=args.launchagents_dir,
    )
    print(f"LAUNCHAGENT_LABEL: {label}")
    print(f"LAUNCHAGENT_PATH: {destination}")
    print(f"NEXT_STEP: launchctl bootstrap gui/$(id -u) {destination}")
    return 0


def _print_summary(
    result,
    shim_action: str,
    role_count: int,
    prompt_count: int,
    skill_count: int,
    rendered_config: str,
) -> None:
    """Print a human-readable discovery summary."""
    print(f"PROJECT_ROOT: {result.project.root}")
    print(f"AGENTS_MD: {result.project.agents_md_path}")
    print(f"CLAUDE_MD_ACTION: {shim_action}")
    print(f"PLUGINS_FOUND: {len(result.plugins)}")
    print(f"GENERATED_ROLES: {role_count}")
    print(f"GENERATED_PROMPTS: {prompt_count}")
    print(f"GENERATED_SKILLS: {skill_count}")
    print(f"CONFIG_LINES: {len(rendered_config.splitlines())}")
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

#!/usr/bin/env python3
"""Validate and atomically publish one dependency-refresh report.

The interactive orchestrator decides whether dependency work is needed and
what commands to run. This module owns only the closed report schema, a
bounded observation of final tracked Git state, and canonical publication.
Reported command strings are evidence, not execution attestation.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from .atomic_io import atomic_write_json
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.atomic_io import atomic_write_json


REPORT_SCHEMA = 1
REPORT_STATUSES = ("not_needed", "completed", "partial", "failed")
EXIT_STATUSES = ("ok", "failed")
REPORT_FILENAME = "dependency-refresh.json"

_MAX_DIRTY_FILES = 20
_MAX_DIRTY_FILE_CHARS = 500
_MAX_REPORT_BYTES = 1024 * 1024
_MAX_REPORTED_COMMANDS = 32
_MAX_DIRECTORY_CHARS = 200
_MAX_COMMAND_CHARS = 500
_REQUEST_FIELDS = frozenset({"schema", "status", "commands"})
_SCRIPT_OWNED_FIELDS = frozenset({"tracked_files_dirty", "dirty_files"})
_CANONICAL_FIELDS = _REQUEST_FIELDS | _SCRIPT_OWNED_FIELDS
_COMMAND_FIELDS = frozenset({"directory", "command", "exit_status"})


def observe_tracked_worktree(
    repo_root: os.PathLike[str] | str,
) -> dict[str, object]:
    """Return bounded tracked state without treating untracked files as dirty."""
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(repo_root),
                "status",
                "--porcelain",
                "--untracked-files=no",
                "--ignore-submodules=untracked",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise OSError(proc.stderr.strip())
        dirty_files = [
            line[3:][:_MAX_DIRTY_FILE_CHARS]
            for line in proc.stdout.splitlines()
            if line and not line.startswith("??")
        ][:_MAX_DIRTY_FILES]
        return {
            "tracked_files_dirty": bool(dirty_files),
            "dirty_files": dirty_files,
        }
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {"tracked_files_dirty": None, "dirty_files": []}


def _read_report_request(path):
    """Return ``(JSON object, problems)`` without interpreting its schema."""
    with Path(path).open("rb") as report_file:
        report_bytes = report_file.read(_MAX_REPORT_BYTES + 1)

    if len(report_bytes) > _MAX_REPORT_BYTES:
        return None, [f"report must contain at most {_MAX_REPORT_BYTES} bytes"]
    try:
        report_text = report_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None, ["report must contain valid UTF-8"]
    try:
        payload = json.loads(report_text)
    except (json.JSONDecodeError, ValueError, RecursionError, MemoryError):
        return None, ["report must contain valid JSON"]
    if not isinstance(payload, dict):
        return None, ["report must be a JSON object"]
    return payload, []


def _validate_string(value, label, max_chars):
    problems = []
    if not isinstance(value, str):
        return [f"{label} must be a string"]
    if not value:
        problems.append(f"{label} must not be empty")
    if len(value) > max_chars:
        problems.append(f"{label} must contain at most {max_chars} characters")
    if not value.isprintable():
        problems.append(f"{label} must be printable")
    return problems


def _validate_request_fields(payload, *, reject_script_owned):
    problems = []
    for field in ("schema", "status", "commands"):
        if field not in payload:
            problems.append(f"missing required field: '{field}'")

    if reject_script_owned:
        for field in sorted(_SCRIPT_OWNED_FIELDS & payload.keys()):
            problems.append(f"'{field}' is script-owned")

    # Script-owned fields receive their own actionable diagnostic on requests;
    # do not report the same field a second time as merely unknown.
    allowed_fields = _CANONICAL_FIELDS
    for field in sorted(payload.keys() - allowed_fields):
        problems.append(f"unknown top-level field: '{field}'")

    if (
        "schema" in payload
        and (
            not isinstance(payload["schema"], int)
            or isinstance(payload["schema"], bool)
            or payload["schema"] != REPORT_SCHEMA
        )
    ):
        problems.append(f"'schema' must be {REPORT_SCHEMA}")
    status = payload.get("status")
    if "status" in payload and status not in REPORT_STATUSES:
        problems.append(
            "'status' must be one of: " + ", ".join(REPORT_STATUSES)
        )

    commands = payload.get("commands")
    if "commands" in payload and not isinstance(commands, list):
        problems.append("'commands' must be a list")
        commands = None
    if isinstance(commands, list):
        if len(commands) > _MAX_REPORTED_COMMANDS:
            problems.append(
                f"'commands' must contain at most {_MAX_REPORTED_COMMANDS} entries"
            )
        for index, command_entry in enumerate(commands[:_MAX_REPORTED_COMMANDS]):
            if not isinstance(command_entry, dict):
                problems.append(f"commands[{index}] must be an object")
                continue
            for field in ("directory", "command", "exit_status"):
                if field not in command_entry:
                    problems.append(
                        f"commands[{index}] missing required field: '{field}'"
                    )
            for field in sorted(command_entry.keys() - _COMMAND_FIELDS):
                problems.append(
                    f"unknown commands[{index}] field: '{field}'"
                )
            if "directory" in command_entry:
                problems.extend(_validate_string(
                    command_entry["directory"],
                    f"commands[{index}].directory",
                    _MAX_DIRECTORY_CHARS,
                ))
            if "command" in command_entry:
                problems.extend(_validate_string(
                    command_entry["command"],
                    f"commands[{index}].command",
                    _MAX_COMMAND_CHARS,
                ))
            exit_status = command_entry.get("exit_status")
            if (
                "exit_status" in command_entry
                and exit_status not in EXIT_STATUSES
            ):
                problems.append(
                    f"commands[{index}].exit_status must be one of: "
                    + ", ".join(EXIT_STATUSES)
                )
        if status == "not_needed" and commands:
            problems.append("'not_needed' requires an empty 'commands' list")
    return problems


def validate_report_request(payload):
    """Return every schema problem in an orchestrator-authored request."""
    if not isinstance(payload, dict):
        return ["report must be a JSON object"]
    return _validate_request_fields(payload, reject_script_owned=True)


def validate_canonical_report(payload):
    """Return every schema problem in a script-published canonical report."""
    if not isinstance(payload, dict):
        return ["report must be a JSON object"]

    problems = _validate_request_fields(payload, reject_script_owned=False)
    for field in ("tracked_files_dirty", "dirty_files"):
        if field not in payload:
            problems.append(f"missing required field: '{field}'")

    tracked_files_dirty = payload.get("tracked_files_dirty")
    if (
        "tracked_files_dirty" in payload
        and tracked_files_dirty is not None
        and not isinstance(tracked_files_dirty, bool)
    ):
        problems.append("'tracked_files_dirty' must be a boolean or null")

    dirty_files = payload.get("dirty_files")
    if "dirty_files" in payload and not isinstance(dirty_files, list):
        problems.append("'dirty_files' must be a list")
        dirty_files = None
    if isinstance(dirty_files, list):
        if len(dirty_files) > _MAX_DIRTY_FILES:
            problems.append(
                f"'dirty_files' must contain at most {_MAX_DIRTY_FILES} entries"
            )
        for index, dirty_file in enumerate(dirty_files[:_MAX_DIRTY_FILES]):
            problems.extend(_validate_string(
                dirty_file,
                f"dirty_files[{index}]",
                _MAX_DIRTY_FILE_CHARS,
            ))
    return problems


def load_dependency_refresh_report(output_dir):
    """Return a complete canonical report, or ``None`` when absent/invalid."""
    try:
        payload, read_problems = _read_report_request(
            Path(output_dir) / REPORT_FILENAME
        )
    except OSError:
        return None
    if read_problems or validate_canonical_report(payload):
        return None
    return payload


def save_report(output_dir, report_path, repo_root):
    """Validate a request, add final tracked state, and publish atomically."""
    payload, problems = _read_report_request(report_path)
    if not problems:
        problems = validate_report_request(payload)
    if problems:
        return problems

    observation = observe_tracked_worktree(repo_root)
    canonical = {
        "schema": payload["schema"],
        "status": payload["status"],
        "commands": [dict(command) for command in payload["commands"]],
        "tracked_files_dirty": observation["tracked_files_dirty"],
        "dirty_files": list(observation["dirty_files"]),
    }
    atomic_write_json(Path(output_dir) / REPORT_FILENAME, canonical)
    return []


def _resolve_repo_root():
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        pass
    return os.getcwd()


def run_save(args):
    """Run the save subcommand and return its process exit status."""
    problems = save_report(
        output_dir=args.output_dir,
        report_path=args.report,
        repo_root=_resolve_repo_root(),
    )
    if problems:
        for problem in problems:
            print(
                f"INVALID dependency refresh report: {problem}",
                file=sys.stderr,
            )
        return 1
    print(f"SAVED {REPORT_FILENAME}")
    return 0


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Validate and save a dependency-refresh report."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    save_parser = subparsers.add_parser("save")
    save_parser.add_argument("--output-dir", required=True)
    save_parser.add_argument("--report", required=True)
    save_parser.set_defaults(handler=run_save)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

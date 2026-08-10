#!/usr/bin/env python3
"""Deterministic detection of dependency roots needing a trusted-branch refresh.

The review pipeline never installs dependencies itself (the 1.113.0 release
removed manifest-driven installation as a security boundary). When the
requester explicitly opts in (run-config.json ``refresh_dependencies``), this
module detects which dependency roots look stale relative to the reviewed
range so the main orchestrator can refresh them adaptively with frozen-mode
installs. Detection is deterministic and side-effect free; execution belongs
to the orchestrator, never this module.

A root signals when its manifest and lockfile both exist AND either a
manifest/lockfile in that directory changed within the reviewed range, or the
installed state (``vendor/`` or ``node_modules/``) is missing. Detection is
bounded: only the repo root and directories containing changed manifest files
are examined. A manifest without a lockfile never signals — no frozen-mode
install exists for it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from .dispatch_status import AGENT_NAME_RE  # noqa: F401 — path setup probe
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)

from git_paths import decode_git_c_quoted_path

# Node managers in priority order — a more specific lockfile beats npm's.
_NODE_SPECS = (
    {
        "manager": "pnpm",
        "lockfile": "pnpm-lock.yaml",
        "suggested_command": "pnpm install --frozen-lockfile --ignore-scripts",
    },
    {
        "manager": "yarn",
        "lockfile": "yarn.lock",
        "suggested_command": "yarn install --immutable --mode=skip-build",
    },
    {
        "manager": "npm",
        "lockfile": "package-lock.json",
        "suggested_command": "npm ci --ignore-scripts --no-audit --no-fund",
    },
)

_COMPOSER_SPEC = {
    "manager": "composer",
    "lockfile": "composer.lock",
    "suggested_command": (
        "composer install --no-scripts --no-plugins --prefer-dist --no-interaction"
    ),
}

_MANIFEST_BASENAMES = frozenset(
    {"composer.json", "composer.lock", "package.json"}
    | {spec["lockfile"] for spec in _NODE_SPECS}
)

# Verification vocabulary for the trusted-branch refresh; bases are accepted
# openings, flags accepted extra tokens.
ALLOWED_INSTALL_BASES = (
    ("composer", "install"),
    ("npm", "ci"),
    ("pnpm", "install"),
    ("yarn", "install"),
)
ALLOWED_INSTALL_FLAGS = frozenset({
    "--ignore-scripts", "--no-scripts", "--no-plugins", "--prefer-dist",
    "--no-interaction", "--no-audit", "--no-fund", "--frozen-lockfile",
    "--immutable", "--mode=skip-build",
})

_MAX_DIRTY_FILES = 20
_MAX_REPORT_BYTES = 1024 * 1024
_MAX_REPORTED_COMMANDS = 128
SKIP_REASON_DIRTY_WORKTREE = "dirty_worktree"
SKIP_REASON_WORKTREE_STATUS_FAILED = "worktree_status_failed"
DEPENDENCY_REFRESH_SKIP_REASONS = frozenset({
    SKIP_REASON_DIRTY_WORKTREE,
    SKIP_REASON_WORKTREE_STATUS_FAILED,
})


def detect_dependency_refresh(repo_root, changed_files):
    """Describe dependency roots to refresh and whether refresh is safe.

    ``changed_files`` are repo-relative paths from the reviewed range (Git
    C-quoted spellings tolerated; malformed entries are skipped). A dirty or
    unknowable tracked worktree returns a fail-closed ``skipped_reason`` while
    preserving the detected signals. Read-only.
    """
    root = Path(repo_root)
    changed_by_dir = {}
    for raw in changed_files or []:
        if not isinstance(raw, str) or not raw:
            continue
        decoded, _was_git_quoted = decode_git_c_quoted_path(raw)
        if decoded is None:
            continue
        path = decoded.strip('"').replace("\\", "/")
        segments = path.split("/")
        # Only repo-relative paths can name a dependency root we may touch.
        if path.startswith("/") or ".." in segments or "" in segments:
            continue
        basename = segments[-1]
        if basename not in _MANIFEST_BASENAMES:
            continue
        directory = "/".join(segments[:-1]) or "."
        changed_by_dir.setdefault(directory, set()).add(basename)

    candidate_dirs = {"."} | set(changed_by_dir)
    signals = []
    for directory in sorted(candidate_dirs):
        base = root if directory == "." else root / directory
        if not base.is_dir():
            continue
        changed_here = changed_by_dir.get(directory, set())

        if (base / "composer.json").is_file() and \
                (base / _COMPOSER_SPEC["lockfile"]).is_file():
            signal = _signal(
                directory,
                _COMPOSER_SPEC,
                changed_here & {"composer.json", _COMPOSER_SPEC["lockfile"]},
                (base / "vendor").is_dir(),
            )
            if signal:
                signals.append(signal)

        if (base / "package.json").is_file():
            for spec in _NODE_SPECS:
                if (base / spec["lockfile"]).is_file():
                    signal = _signal(
                        directory,
                        spec,
                        changed_here & {"package.json", spec["lockfile"]},
                        (base / "node_modules").is_dir(),
                    )
                    if signal:
                        signals.append(signal)
                    break

    result = {"signals": signals}
    if not signals:
        return result
    dirty_files, status_failed = _tracked_worktree_status(root)
    if status_failed:
        result.update({
            "skipped_reason": SKIP_REASON_WORKTREE_STATUS_FAILED,
            "dirty_files": [],
        })
    elif dirty_files:
        result.update({
            "skipped_reason": SKIP_REASON_DIRTY_WORKTREE,
            "dirty_files": dirty_files,
        })
    return result


def _signal(directory, spec, changed, installed):
    """Build one signal dict, or None when the root needs no refresh."""
    reasons = []
    if changed:
        reasons.append("changed_in_range")
    if not installed:
        reasons.append("installed_state_missing")
    if not reasons:
        return None
    return {
        "manager": spec["manager"],
        "directory": directory,
        "reasons": reasons,
        "changed_files": sorted(changed),
        "installed_state_present": installed,
        "suggested_command": spec["suggested_command"],
    }


def _tracked_worktree_status(repo_root):
    """Return ``(bounded_dirty_files, failed)`` for tracked Git state."""
    try:
        git_status = subprocess.run(
            [
                "git", "-C", str(repo_root), "status", "--porcelain",
                "--untracked-files=no", "--ignore-submodules=untracked",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if git_status.returncode != 0:
            raise OSError(git_status.stderr.strip())
        dirty_files = [
            line[3:]
            for line in git_status.stdout.splitlines()
            if line and not line.startswith("??")
        ]
        return dirty_files[:_MAX_DIRTY_FILES], False
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return [], True


def _command_allowed(command):
    """Return whether a reported command matches the frozen-install grammar."""
    if not isinstance(command, str):
        return False
    if not command.isprintable():
        return False
    if any(character in command for character in "&;|`$><"):
        return False

    tokens = command.split()
    for base in ALLOWED_INSTALL_BASES:
        if tuple(tokens[:len(base)]) != base:
            continue
        return all(token in ALLOWED_INSTALL_FLAGS for token in tokens[len(base):])
    return False


def load_dependency_refresh_report(output_dir):
    """Return ``(report, load_failed)`` for the orchestrator self-report.

    A missing report is not itself a failure. Unreadable, oversized,
    malformed, non-object, or parser-exhausting reports are failures and do
    not return evidence.
    """
    report_path = Path(output_dir) / "dependency-refresh.json"
    try:
        with report_path.open("rb") as report_file:
            report_bytes = report_file.read(_MAX_REPORT_BYTES + 1)
    except FileNotFoundError:
        return None, False
    except OSError:
        return None, True

    if len(report_bytes) > _MAX_REPORT_BYTES:
        return None, True

    try:
        report = json.loads(report_bytes.decode("utf-8"))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
        MemoryError,
    ):
        return None, True
    if not isinstance(report, dict):
        return None, True
    return report, False


def verify_dependency_refresh(repo_root, output_dir):
    """Independently verify a dependency refresh report and tracked Git state."""
    result = {
        "report_present": False,
        "commands_allowed": None,
        "disallowed_commands": [],
        "tracked_files_dirty": None,
        "dirty_files": [],
        "verification_failed": False,
    }

    report, report_load_failed = load_dependency_refresh_report(output_dir)
    if report_load_failed:
        result["verification_failed"] = True

    if isinstance(report, dict):
        result["report_present"] = True
        commands = report.get("commands")
        commands_schema_valid = (
            isinstance(commands, list)
            and len(commands) <= _MAX_REPORTED_COMMANDS
            and all(
                isinstance(entry, dict)
                and isinstance(entry.get("command"), str)
                for entry in commands
            )
        )
        if commands_schema_valid:
            for entry in commands:
                command = entry.get("command")
                if not _command_allowed(command):
                    result["disallowed_commands"].append(str(command)[:500])
            result["disallowed_commands"] = result["disallowed_commands"][
                :_MAX_DIRTY_FILES
            ]
            result["commands_allowed"] = not result["disallowed_commands"]
        else:
            result["verification_failed"] = True

    dirty_files, status_failed = _tracked_worktree_status(repo_root)
    if not status_failed:
        result["tracked_files_dirty"] = bool(dirty_files)
        result["dirty_files"] = dirty_files
    else:
        result["verification_failed"] = True

    return result

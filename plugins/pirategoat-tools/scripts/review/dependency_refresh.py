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

import os
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
        "suggested_command": "pnpm install --frozen-lockfile",
    },
    {
        "manager": "yarn",
        "lockfile": "yarn.lock",
        "suggested_command": "yarn install --immutable",
    },
    {
        "manager": "npm",
        "lockfile": "package-lock.json",
        "suggested_command": "npm ci",
    },
)

_COMPOSER_SPEC = {
    "manager": "composer",
    "lockfile": "composer.lock",
    "suggested_command": "composer install",
}

_MANIFEST_BASENAMES = frozenset(
    {"composer.json", "composer.lock", "package.json"}
    | {spec["lockfile"] for spec in _NODE_SPECS}
)


def detect_dependency_refresh(repo_root, changed_files):
    """Return ``{"signals": [...]}`` describing dependency roots to refresh.

    ``changed_files`` are repo-relative paths from the reviewed range (Git
    C-quoted spellings tolerated; malformed entries are skipped). Read-only.
    """
    root = Path(repo_root)
    changed_by_dir = {}
    for raw in changed_files or []:
        if not isinstance(raw, str) or not raw:
            continue
        decoded, malformed = decode_git_c_quoted_path(raw)
        if malformed or decoded is None:
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

    return {"signals": signals}


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

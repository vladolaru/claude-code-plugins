"""macOS LaunchAgent rendering and installation for scheduled reconcile runs."""

from __future__ import annotations

import plistlib
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from uuid import uuid5, NAMESPACE_URL

from codex_interop.model import ReconcileError


DEFAULT_START_INTERVAL = 300
DEFAULT_LAUNCHAGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
DEFAULT_LOGS_DIR = Path.home() / "Library" / "Logs" / "codex-interop"


def build_launchagent_label(project_root: str | Path) -> str:
    """Build a deterministic LaunchAgent label from a project path."""
    project_path = Path(project_root).expanduser().resolve()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", project_path.name).strip("-").lower() or "project"
    stable_hash = uuid5(NAMESPACE_URL, str(project_path)).hex[:10]
    return f"com.openai.codex-interop.{slug}.{stable_hash}"


def build_launchagent_plist(
    *,
    project_root: str | Path,
    interval_seconds: int = DEFAULT_START_INTERVAL,
    cache_dir: str | Path | None = None,
    codex_home: str | Path | None = None,
    python_executable: str | Path | None = None,
    cli_path: str | Path | None = None,
    label: str | None = None,
    logs_dir: str | Path | None = None,
) -> bytes:
    """Render a LaunchAgent plist that periodically runs reconcile."""
    project_path = Path(project_root).expanduser().resolve()
    if interval_seconds <= 0:
        raise ReconcileError("LaunchAgent interval must be a positive integer")
    if not (project_path / "AGENTS.md").is_file():
        raise ReconcileError(f"LaunchAgent project root must contain AGENTS.md: {project_path}")

    label_value = label or build_launchagent_label(project_path)
    python_path = str(Path(python_executable or sys.executable).expanduser().resolve())
    cli_script_path = str(Path(cli_path or (Path(__file__).resolve().parent / "cli.py")).resolve())
    log_root = Path(logs_dir or DEFAULT_LOGS_DIR).expanduser().resolve()
    stdout_path = log_root / f"{label_value}.out.log"
    stderr_path = log_root / f"{label_value}.err.log"

    program_arguments = [
        python_path,
        cli_script_path,
        "reconcile",
        "--project",
        str(project_path),
    ]
    if cache_dir is not None:
        program_arguments.extend(["--cache-dir", str(Path(cache_dir).expanduser().resolve())])
    if codex_home is not None:
        program_arguments.extend(["--codex-home", str(Path(codex_home).expanduser().resolve())])

    payload: dict[str, Any] = {
        "Label": label_value,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(project_path),
        "RunAtLoad": True,
        "StartInterval": interval_seconds,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "ProcessType": "Background",
    }
    return plistlib.dumps(payload, sort_keys=True)


def install_launchagent(
    plist_bytes: bytes,
    *,
    label: str,
    launchagents_dir: str | Path | None = None,
) -> Path:
    """Install a rendered LaunchAgent plist into a LaunchAgents directory."""
    root = Path(launchagents_dir or DEFAULT_LAUNCHAGENTS_DIR).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{label}.plist"
    with tempfile.NamedTemporaryFile(dir=root, delete=False) as handle:
        handle.write(plist_bytes)
        temp_path = Path(handle.name)
    temp_path.replace(destination)
    return destination

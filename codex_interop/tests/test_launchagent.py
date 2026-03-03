"""Tests for macOS LaunchAgent rendering and installation."""

from __future__ import annotations

from pathlib import Path
import plistlib

import pytest

from codex_interop.install_launchagent import (
    build_launchagent_label,
    build_launchagent_plist,
    install_launchagent,
)
from codex_interop.model import ReconcileError


def test_build_launchagent_plist_renders_reconcile_command(make_project, tmp_path: Path):
    """Rendered plists target reconcile for one explicit project root."""
    project_root, _agents_md = make_project()
    codex_home = tmp_path / "codex-home"
    plist_bytes = build_launchagent_plist(
        project_root=project_root,
        interval_seconds=600,
        cache_dir=tmp_path / "claude-cache",
        codex_home=codex_home,
        python_executable=Path("/usr/bin/python3"),
        cli_path=Path("/tmp/codex_interop/cli.py"),
        logs_dir=tmp_path / "logs",
    )

    payload = plistlib.loads(plist_bytes)

    assert payload["RunAtLoad"] is True
    assert payload["StartInterval"] == 600
    assert payload["WorkingDirectory"] == str(project_root)
    assert payload["ProgramArguments"] == [
        "/usr/bin/python3",
        str(Path("/tmp/codex_interop/cli.py").resolve()),
        "reconcile",
        "--project",
        str(project_root),
        "--cache-dir",
        str((tmp_path / "claude-cache").resolve()),
        "--codex-home",
        str(codex_home.resolve()),
    ]
    assert payload["Label"] == build_launchagent_label(project_root)
    assert payload["StandardOutPath"].endswith(".out.log")
    assert payload["StandardErrorPath"].endswith(".err.log")


def test_install_launchagent_writes_plist(make_project, tmp_path: Path):
    """Installing a plist writes it atomically to the target directory."""
    project_root, _agents_md = make_project()
    label = build_launchagent_label(project_root)
    plist_bytes = build_launchagent_plist(project_root=project_root, label=label)
    launchagents_dir = tmp_path / "LaunchAgents"

    destination = install_launchagent(plist_bytes, label=label, launchagents_dir=launchagents_dir)

    assert destination == launchagents_dir / f"{label}.plist"
    assert plistlib.loads(destination.read_bytes())["Label"] == label


def test_build_launchagent_plist_requires_project_agents_md(tmp_path: Path):
    """A LaunchAgent cannot target a path that is not a valid project root."""
    project_root = tmp_path / "not-a-project"
    project_root.mkdir()

    with pytest.raises(ReconcileError, match="must contain AGENTS.md"):
        build_launchagent_plist(project_root=project_root)

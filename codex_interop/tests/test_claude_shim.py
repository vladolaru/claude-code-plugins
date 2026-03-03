"""Tests for safe CLAUDE.md shim planning."""

from __future__ import annotations

from pathlib import Path

from codex_interop.claude_shim import SHIM_CONTENT, plan_claude_shim
from codex_interop.model import ProjectContext


def test_plan_claude_shim_creates_when_missing(tmp_path: Path):
    """Missing CLAUDE.md produces a create decision."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "create"
    assert decision.path == project_root / "CLAUDE.md"
    assert decision.content == SHIM_CONTENT


def test_plan_claude_shim_preserves_exact_shim(tmp_path: Path):
    """Existing exact shim is preserved."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    claude_md_path = project_root / "CLAUDE.md"
    claude_md_path.write_text("@AGENTS.md\n")

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "preserve"
    assert decision.content == SHIM_CONTENT


def test_plan_claude_shim_preserves_symlink_to_agents_md(tmp_path: Path):
    """Symlinked CLAUDE.md -> AGENTS.md is preserved."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    (project_root / "CLAUDE.md").symlink_to(agents_md_path.name)

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "preserve"


def test_plan_claude_shim_fails_for_hand_authored_file(tmp_path: Path):
    """Hand-authored CLAUDE.md is not overwritten."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    (project_root / "CLAUDE.md").write_text("# custom claude instructions\n")

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "fail"
    assert "not a generator-owned shim" in decision.reason


def test_plan_claude_shim_fails_for_non_agents_symlink(tmp_path: Path):
    """Symlinks to anything except AGENTS.md are rejected."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    other_file = project_root / "OTHER.md"
    other_file.write_text("# Other\n")
    (project_root / "CLAUDE.md").symlink_to(other_file.name)

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "fail"
    assert "not to AGENTS.md" in decision.reason

"""Test setup for codex_interop package tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))


@pytest.fixture
def make_project(tmp_path: Path):
    """Create isolated project directories with AGENTS.md."""

    def _make_project(
        relative_path: str = "project",
        *,
        agents_content: str = "# Shared instructions\n",
    ) -> tuple[Path, Path]:
        project_root = tmp_path / relative_path
        project_root.mkdir(parents=True, exist_ok=True)
        agents_md = project_root / "AGENTS.md"
        agents_md.write_text(agents_content)
        return project_root, agents_md

    return _make_project


@pytest.fixture
def make_plugin_version(tmp_path: Path):
    """Create fake installed Claude plugin versions in an isolated cache root."""
    cache_root = tmp_path / "claude-cache"

    def _make_plugin_version(
        marketplace: str,
        plugin_name: str,
        version: str,
        *,
        skill_names: tuple[str, ...] = (),
        agent_names: tuple[str, ...] = (),
    ) -> tuple[Path, Path]:
        version_dir = cache_root / marketplace / plugin_name / version
        version_dir.mkdir(parents=True, exist_ok=True)

        skills_dir = version_dir / "skills"
        agents_dir = version_dir / "agents"

        for skill_name in skill_names:
            skill_dir = skills_dir / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {skill_name}\ndescription: test skill\n---\n"
            )

        for agent_name in agent_names:
            agents_dir.mkdir(parents=True, exist_ok=True)
            (agents_dir / f"{agent_name}.md").write_text(
                f"---\nname: {agent_name}\ndescription: test agent\n---\n"
            )

        return cache_root, version_dir

    return _make_plugin_version

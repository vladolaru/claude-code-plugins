"""Tests for Phase 1 Codex interop discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from codex_interop.discover import discover, discover_latest_plugins, resolve_project_root
from codex_interop.model import DiscoveryError


def _make_plugin_version(
    cache_root: Path,
    marketplace: str,
    plugin_name: str,
    version: str,
    *,
    skill_names: tuple[str, ...] = (),
    agent_names: tuple[str, ...] = (),
) -> Path:
    """Create a fake installed Claude plugin version directory."""
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

    return version_dir


def test_resolve_project_root_from_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Project root resolves from current working directory when AGENTS.md exists above."""
    project_root = tmp_path / "project"
    nested_dir = project_root / "src" / "feature"
    nested_dir.mkdir(parents=True)
    agents_md = project_root / "AGENTS.md"
    agents_md.write_text("# Shared instructions\n")

    monkeypatch.chdir(nested_dir)
    result = resolve_project_root()

    assert result.root == project_root.resolve()
    assert result.agents_md_path == agents_md.resolve()


def test_resolve_project_root_from_explicit_project_path(tmp_path: Path):
    """Explicit project path can point inside a repo and still resolves upward."""
    project_root = tmp_path / "project"
    nested_dir = project_root / "docs" / "nested"
    nested_dir.mkdir(parents=True)
    agents_md = project_root / "AGENTS.md"
    agents_md.write_text("# Shared instructions\n")

    result = resolve_project_root(nested_dir)

    assert result.root == project_root.resolve()
    assert result.agents_md_path == agents_md.resolve()


def test_resolve_project_root_requires_agents_md(tmp_path: Path):
    """Missing AGENTS.md is a hard discovery failure."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    with pytest.raises(DiscoveryError, match="AGENTS.md"):
        resolve_project_root(project_root)


def test_discover_latest_plugins_uses_semver_order(tmp_path: Path):
    """Latest installed plugin version is selected by semantic version precedence."""
    cache_root = tmp_path / "claude-cache"
    _make_plugin_version(
        cache_root, "market", "prompt-engineer", "2.0.0", skill_names=("prompt-engineer",)
    )
    _make_plugin_version(
        cache_root, "market", "prompt-engineer", "2.1.0", skill_names=("prompt-engineer",)
    )
    _make_plugin_version(
        cache_root, "market", "prompt-engineer", "2.0.5", skill_names=("prompt-engineer",)
    )

    plugins = discover_latest_plugins(cache_root)

    assert len(plugins) == 1
    assert plugins[0].plugin_name == "prompt-engineer"
    assert plugins[0].version_text == "2.1.0"


def test_discover_latest_plugins_ignores_invalid_version_dirs(tmp_path: Path):
    """Malformed version directories are ignored."""
    cache_root = tmp_path / "claude-cache"
    _make_plugin_version(cache_root, "market", "dex", "1.5.3", skill_names=("knowledge-capture",))
    (cache_root / "market" / "dex" / "latest").mkdir(parents=True)

    plugins = discover_latest_plugins(cache_root)

    assert len(plugins) == 1
    assert plugins[0].version_text == "1.5.3"


def test_discover_latest_plugins_fails_if_no_valid_versions(tmp_path: Path):
    """A plugin with no valid semantic versions fails clearly."""
    cache_root = tmp_path / "claude-cache"
    (cache_root / "market" / "broken-plugin" / "latest").mkdir(parents=True)

    with pytest.raises(DiscoveryError, match="No valid semantic versions"):
        discover_latest_plugins(cache_root)


def test_discover_latest_plugins_follows_symlinked_repo_source(tmp_path: Path):
    """Installed plugin versions that are symlinks resolve to the repo target."""
    cache_root = tmp_path / "claude-cache"
    repo_root = tmp_path / "repo" / "prompt-engineer"
    skills_dir = repo_root / "skills" / "prompt-engineer"
    agents_dir = repo_root / "agents"
    skills_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: test skill\n---\n"
    )
    (agents_dir / "optimizer.md").write_text(
        "---\nname: optimizer\ndescription: test agent\n---\n"
    )

    install_link = cache_root / "market" / "prompt-engineer" / "2.1.0"
    install_link.parent.mkdir(parents=True, exist_ok=True)
    install_link.symlink_to(repo_root, target_is_directory=True)

    plugins = discover_latest_plugins(cache_root)

    assert len(plugins) == 1
    assert plugins[0].installed_path == install_link
    assert plugins[0].source_path == repo_root.resolve()
    assert plugins[0].skills == (skills_dir.resolve(),)
    assert plugins[0].agents == ((agents_dir / "optimizer.md").resolve(),)


def test_discover_combines_project_and_plugins(tmp_path: Path):
    """Top-level discover returns both project and installed plugin information."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("# Shared instructions\n")

    cache_root = tmp_path / "claude-cache"
    _make_plugin_version(
        cache_root,
        "market",
        "prompt-engineer",
        "2.1.0",
        skill_names=("prompt-engineer",),
        agent_names=("optimizer",),
    )

    result = discover(project_root, cache_root)

    assert result.project.root == project_root.resolve()
    assert len(result.plugins) == 1
    assert result.plugins[0].plugin_name == "prompt-engineer"
    assert result.plugins[0].version_text == "2.1.0"

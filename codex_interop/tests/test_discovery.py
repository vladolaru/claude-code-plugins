"""Tests for Phase 1 Codex interop discovery and SemVer behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from codex_interop.discover import discover, discover_latest_plugins, resolve_project_root
from codex_interop.model import DiscoveryError, SemVer

def test_resolve_project_root_from_cwd(make_project, monkeypatch: pytest.MonkeyPatch):
    """Project root resolves from current working directory when AGENTS.md exists above."""
    project_root, agents_md = make_project()
    nested_dir = project_root / "src" / "feature"
    nested_dir.mkdir(parents=True)

    monkeypatch.chdir(nested_dir)
    result = resolve_project_root()

    assert result.root == project_root.resolve()
    assert result.agents_md_path == agents_md.resolve()


def test_resolve_project_root_from_explicit_project_path(make_project):
    """Explicit project path can point inside a repo and still resolves upward."""
    project_root, agents_md = make_project()
    nested_dir = project_root / "docs" / "nested"
    nested_dir.mkdir(parents=True)

    result = resolve_project_root(nested_dir)

    assert result.root == project_root.resolve()
    assert result.agents_md_path == agents_md.resolve()


def test_resolve_project_root_from_file_path(make_project):
    """Explicit file paths resolve from the parent directory."""
    project_root, agents_md = make_project()
    nested_file = project_root / "docs" / "guide.md"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("guide\n")

    result = resolve_project_root(nested_file)

    assert result.root == project_root.resolve()
    assert result.agents_md_path == agents_md.resolve()


def test_resolve_project_root_requires_agents_md(tmp_path: Path):
    """Missing AGENTS.md is a hard discovery failure."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    with pytest.raises(DiscoveryError, match="AGENTS.md"):
        resolve_project_root(project_root)


def test_discover_latest_plugins_uses_semver_order(make_plugin_version):
    """Latest installed plugin version is selected by semantic version precedence."""
    cache_root, _ = make_plugin_version(
        "market", "prompt-engineer", "2.0.0", skill_names=("prompt-engineer",)
    )
    make_plugin_version(
        "market", "prompt-engineer", "2.1.0", skill_names=("prompt-engineer",)
    )
    make_plugin_version(
        "market", "prompt-engineer", "2.0.5", skill_names=("prompt-engineer",)
    )

    plugins = discover_latest_plugins(cache_root)

    assert len(plugins) == 1
    assert plugins[0].plugin_name == "prompt-engineer"
    assert plugins[0].version_text == "2.1.0"


def test_discover_latest_plugins_ignores_invalid_version_dirs(make_plugin_version):
    """Malformed version directories are ignored."""
    cache_root, _ = make_plugin_version(
        "market", "dex", "1.5.3", skill_names=("knowledge-capture",)
    )
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


def test_discover_latest_plugins_requires_existing_cache_dir(tmp_path: Path):
    """A missing cache root is a hard discovery failure."""
    with pytest.raises(DiscoveryError, match="cache not found"):
        discover_latest_plugins(tmp_path / "missing-cache")


def test_discover_latest_plugins_requires_at_least_one_plugin(tmp_path: Path):
    """An empty cache root fails clearly instead of returning an empty result."""
    cache_root = tmp_path / "claude-cache"
    cache_root.mkdir()

    with pytest.raises(DiscoveryError, match="No installed Claude plugins found"):
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


def test_discover_combines_project_and_plugins(make_project, make_plugin_version):
    """Top-level discover returns both project and installed plugin information."""
    project_root, _agents_md = make_project()
    cache_root, _ = make_plugin_version(
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


def test_semver_prerelease_ordering_and_type_guards():
    """Prerelease precedence follows semver ordering rules."""
    alpha1 = SemVer.parse("1.2.3-alpha.1")
    alpha2 = SemVer.parse("1.2.3-alpha.2")
    beta = SemVer.parse("1.2.3-beta")
    stable = SemVer.parse("1.2.3")

    assert alpha1 < alpha2 < beta < stable
    assert SemVer.__lt__(stable, "1.2.4") is NotImplemented

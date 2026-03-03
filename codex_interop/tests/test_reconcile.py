"""Tests for Phase 4 reconcile behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

import codex_interop.reconcile as reconcile_module
from codex_interop.claude_shim import plan_claude_shim
from codex_interop.discover import discover
from codex_interop.model import ReconcileError
from codex_interop.reconcile import (
    STATE_RELATIVE_PATH,
    ReconcileReport,
    build_desired_state,
    diff_desired_state,
    format_change_report,
    format_diff_report,
    reconcile_desired_state,
)
from codex_interop.render_codex_config import render_inline_codex_config, render_prompt_files
from codex_interop.translate_agents import translate_installed_agents
from codex_interop.translate_skills import translate_installed_skills


def test_reconcile_writes_project_and_codex_outputs(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Reconcile writes local project artifacts and global Codex skills."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.2.3",
        skill_names=("decision-critic",),
        agent_names=("architecture-reviewer",),
    )
    (version_dir / "agents" / "architecture-reviewer.md").write_text(
        "---\n"
        "name: architecture-reviewer\n"
        "description: Architecture review\n"
        "tools:\n"
        "  - Read\n"
        "---\n\n"
        "You are an architecture reviewer.\n"
    )
    (version_dir / "skills" / "decision-critic" / "SKILL.md").write_text(
        "---\n"
        "name: decision-critic\n"
        "description: Criticize decisions\n"
        "---\n\n"
        "Use this skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    desired = _build_desired(project_root, cache_root, codex_home)
    report = reconcile_desired_state(desired)

    assert report.applied is True
    assert (project_root / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    assert (project_root / ".codex" / "config.toml").exists()
    assert (
        project_root / ".codex" / "prompts" / "agents" / "pirategoat-tools-architecture-reviewer.md"
    ).read_text() == "You are an architecture reviewer.\n"
    assert (project_root / STATE_RELATIVE_PATH).exists()
    assert (
        codex_home / "skills" / "pirategoat-tools-decision-critic" / "SKILL.md"
    ).read_text().startswith("---\nname: pirategoat-tools-decision-critic\n")


def test_reconcile_is_idempotent(make_project, make_plugin_version, tmp_path: Path):
    """Running reconcile twice without input changes becomes a no-op."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")
    first = reconcile_desired_state(desired)
    second = reconcile_desired_state(desired)

    assert first.changes
    assert second.changes == ()


def test_diff_does_not_write_outputs(make_project, make_plugin_version, tmp_path: Path):
    """Diff computes changes without creating generated artifacts."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    report = diff_desired_state(_build_desired(project_root, cache_root, codex_home))

    assert report.changes
    assert not (project_root / "CLAUDE.md").exists()
    assert not (project_root / ".codex").exists()
    assert not codex_home.exists()


def test_reconcile_fails_for_non_owned_project_config(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Existing hand-authored `.codex/config.toml` is not overwritten."""
    project_root, _agents_md = make_project()
    (project_root / ".codex").mkdir()
    (project_root / ".codex" / "config.toml").write_text("# hand-authored\n")
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")

    with pytest.raises(ReconcileError, match="Refusing to overwrite non-generated project file"):
        reconcile_desired_state(desired)


def test_reconcile_removes_stale_managed_skill(make_project, make_plugin_version, tmp_path: Path):
    """A later reconcile removes previously managed skills no longer desired."""
    project_root, _agents_md = make_project()
    cache_root, v1_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.0.0",
        skill_names=("decision-critic",),
    )
    (v1_dir / "skills" / "decision-critic" / "SKILL.md").write_text(
        "---\nname: decision-critic\ndescription: Criticize\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    first_desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(first_desired)
    installed_skill = codex_home / "skills" / "pirategoat-tools-decision-critic"
    assert installed_skill.exists()

    _, v2_dir = make_plugin_version("market", "pirategoat-tools", "1.0.1")
    # Remove the old skill source so the fake installed latest plugin has no skills.
    stale_source = v2_dir / "skills"
    if stale_source.exists():
        raise AssertionError("Unexpected skills directory in the later fixture version")

    second_desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(second_desired)

    assert not installed_skill.exists()


def test_diff_report_includes_unified_diff_for_updated_text_files(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Diff output includes a unified diff when a managed text file changes."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer",),
    )
    agent_path = version_dir / "agents" / "reviewer.md"
    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nOld body.\n")
    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")
    reconcile_desired_state(desired)

    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nNew body.\n")
    updated = _build_desired(project_root, cache_root, tmp_path / "codex-home")
    report = diff_desired_state(updated)
    rendered = format_diff_report(updated, report)

    assert "UPDATE:" in rendered
    assert "@@" in rendered
    assert "-Old body." in rendered
    assert "+New body." in rendered


def test_reconcile_removes_stale_managed_prompt_file(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Previously managed prompt files are removed when no longer desired."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    codex_home = tmp_path / "codex-home"

    first = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(first)
    prompt_path = project_root / ".codex" / "prompts" / "agents" / "prompt-engineer-reviewer.md"
    assert prompt_path.exists()

    updated_agent = version_dir / "agents" / "reviewer.md"
    updated_agent.unlink()
    second = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(second)

    assert not prompt_path.exists()


def test_reconcile_rejects_symlinked_project_file(make_project, make_plugin_version, tmp_path: Path):
    """Managed project targets may not be symlinks."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "prompt-engineer", "1.0.0", agent_names=("reviewer",)
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    config_dir = project_root / ".codex"
    config_dir.mkdir()
    real_config = project_root / "real-config.toml"
    real_config.write_text("real\n")
    (config_dir / "config.toml").symlink_to(real_config)

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")

    with pytest.raises(ReconcileError, match="symlinked project file"):
        diff_desired_state(desired)


def test_reconcile_rejects_non_directory_skill_target(make_project, make_plugin_version, tmp_path: Path):
    """A file where a skill directory should be is a hard error."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "prompt-engineer", "1.0.0", skill_names=("prompt-engineer",)
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    skills_root = codex_home / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "prompt-engineer-prompt-engineer").write_text("not a directory\n")

    desired = _build_desired(project_root, cache_root, codex_home)

    with pytest.raises(ReconcileError, match="Expected a skill directory but found a file"):
        diff_desired_state(desired)


def test_reconcile_rejects_non_owned_skill_directory(make_project, make_plugin_version, tmp_path: Path):
    """Existing hand-authored skill directories are not overwritten."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "prompt-engineer", "1.0.0", skill_names=("prompt-engineer",)
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    skill_dir = codex_home / "skills" / "prompt-engineer-prompt-engineer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("hand-authored\n")

    desired = _build_desired(project_root, cache_root, codex_home)

    with pytest.raises(ReconcileError, match="non-generated skill directory"):
        diff_desired_state(desired)


def test_format_change_report_handles_empty_report():
    """Empty reports render as a single no-op line."""
    assert format_change_report(ReconcileReport(changes=(), applied=False)) == "No changes."


def test_format_diff_report_handles_no_changes(make_project, make_plugin_version, tmp_path: Path):
    """No-op diffs render the short form."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "prompt-engineer", "1.0.0", skill_names=("prompt-engineer",)
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")
    reconcile_desired_state(desired)
    report = diff_desired_state(desired)

    assert format_change_report(report) == "No changes."
    assert format_diff_report(desired, report) == "No changes."


def test_reconcile_rolls_back_project_outputs_when_skill_swap_fails(
    make_project,
    make_plugin_version,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Later skill swap failures restore previously generated project outputs and state."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
        agent_names=("reviewer",),
    )
    agent_path = version_dir / "agents" / "reviewer.md"
    skill_path = version_dir / "skills" / "prompt-engineer" / "SKILL.md"
    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nOld prompt.\n")
    skill_path.write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nOld skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    first_desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(first_desired)

    original_config = (project_root / ".codex" / "config.toml").read_text()
    original_prompt = (
        project_root / ".codex" / "prompts" / "agents" / "prompt-engineer-reviewer.md"
    ).read_text()
    original_skill = (
        codex_home / "skills" / "prompt-engineer-prompt-engineer" / "SKILL.md"
    ).read_text()
    original_state = (project_root / STATE_RELATIVE_PATH).read_text()

    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nNew prompt.\n")
    skill_path.write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nNew skill.\n"
    )
    updated = _build_desired(project_root, cache_root, codex_home)

    original_swap_path = reconcile_module._swap_path

    def fail_on_skill_swap(destination: Path, staged_path: Path, backup_root: Path, *, is_dir: bool):
        if is_dir and destination.name == "prompt-engineer-prompt-engineer":
            raise RuntimeError("boom during skill swap")
        return original_swap_path(destination, staged_path, backup_root, is_dir=is_dir)

    monkeypatch.setattr(reconcile_module, "_swap_path", fail_on_skill_swap)

    with pytest.raises(RuntimeError, match="boom during skill swap"):
        reconcile_desired_state(updated)

    assert (project_root / ".codex" / "config.toml").read_text() == original_config
    assert (
        project_root / ".codex" / "prompts" / "agents" / "prompt-engineer-reviewer.md"
    ).read_text() == original_prompt
    assert (
        codex_home / "skills" / "prompt-engineer-prompt-engineer" / "SKILL.md"
    ).read_text() == original_skill
    assert (project_root / STATE_RELATIVE_PATH).read_text() == original_state


def test_reconcile_rolls_back_when_stale_prompt_removal_fails(
    make_project,
    make_plugin_version,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Stale-output removal failures restore the prior prompt tree and state."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer",),
    )
    agent_path = version_dir / "agents" / "reviewer.md"
    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n")
    codex_home = tmp_path / "codex-home"

    first_desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(first_desired)

    prompt_path = project_root / ".codex" / "prompts" / "agents" / "prompt-engineer-reviewer.md"
    original_config = (project_root / ".codex" / "config.toml").read_text()
    original_prompt = prompt_path.read_text()
    original_state = (project_root / STATE_RELATIVE_PATH).read_text()

    agent_path.unlink()
    updated = _build_desired(project_root, cache_root, codex_home)

    original_remove_path = reconcile_module._remove_path

    def fail_on_prompt_removal(destination: Path, backup_root: Path, *, is_dir: bool):
        if destination == prompt_path:
            raise RuntimeError("boom during stale prompt removal")
        return original_remove_path(destination, backup_root, is_dir=is_dir)

    monkeypatch.setattr(reconcile_module, "_remove_path", fail_on_prompt_removal)

    with pytest.raises(RuntimeError, match="boom during stale prompt removal"):
        reconcile_desired_state(updated)

    assert (project_root / ".codex" / "config.toml").read_text() == original_config
    assert prompt_path.read_text() == original_prompt
    assert (project_root / STATE_RELATIVE_PATH).read_text() == original_state


def _build_desired(project_root: Path, cache_root: Path, codex_home: Path):
    """Build desired state from fixture project and cache roots."""
    discovery = discover(project_path=project_root, cache_dir=cache_root)
    shim_decision = plan_claude_shim(discovery.project)
    roles = translate_installed_agents(discovery.plugins)
    skills = translate_installed_skills(discovery.plugins)
    prompt_files = render_prompt_files(roles)
    rendered_config = render_inline_codex_config(roles)
    return build_desired_state(
        discovery,
        shim_decision,
        prompt_files,
        rendered_config,
        skills,
        codex_home=codex_home,
    )

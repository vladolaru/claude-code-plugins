"""Tests for Phase 2 agent translation and rendering."""

from __future__ import annotations

from pathlib import Path

from codex_interop.discover import discover_latest_plugins
from codex_interop.render_codex_config import (
    render_inline_codex_config,
    render_prompt_files,
)
from codex_interop.translate_agents import translate_installed_agents

def test_translate_installed_agents_generates_deterministic_roles(make_plugin_version):
    """Claude agents translate to deterministic Codex role objects."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.2.3",
        agent_names=("architecture-reviewer",),
    )
    agent_path = version_dir / "agents" / "architecture-reviewer.md"
    agent_path.write_text(
        "---\n"
        "name: architecture-reviewer\n"
        "description: Software architecture review\n"
        "model: sonnet\n"
        "tools:\n"
        "  - Read\n"
        "  - Bash\n"
        "  - WebSearch\n"
        "---\n\n"
        "You are an architecture reviewer.\n"
    )

    plugins = discover_latest_plugins(cache_root)
    roles = translate_installed_agents(plugins)

    assert len(roles) == 1
    role = roles[0]
    assert role.role_name == "pirategoat-tools_architecture_reviewer"
    assert role.description == "Software architecture review"
    assert role.original_model_hint == "sonnet"
    assert role.model == "gpt-5.3-codex"
    assert role.tools == ("read", "bash", "web_search")
    assert role.prompt_relpath.as_posix() == "prompts/agents/pirategoat-tools-architecture-reviewer.md"
    assert role.prompt_body == "You are an architecture reviewer.\n"


def test_render_prompt_files_uses_dot_codex_relative_paths(make_plugin_version):
    """Rendered prompt files land under `.codex/prompts/agents/`."""
    cache_root, version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0", agent_names=("reviewer",)
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )

    roles = translate_installed_agents(discover_latest_plugins(cache_root))
    prompt_files = render_prompt_files(roles)

    assert prompt_files == {
        Path(".codex/prompts/agents/test-plugin-reviewer.md"): "Prompt body.\n"
    }


def test_render_inline_codex_config_is_deterministic(make_plugin_version):
    """Inline config rendering is stable and references generated prompt files."""
    cache_root, first = make_plugin_version(
        "market", "alpha", "1.0.0", agent_names=("b-reviewer",)
    )
    _, second = make_plugin_version(
        "market", "beta", "2.0.0", agent_names=("a-reviewer",)
    )
    (first / "agents" / "b-reviewer.md").write_text(
        "---\nname: b-reviewer\ndescription: B role\nmodel: sonnet\n---\n\nB prompt.\n"
    )
    (second / "agents" / "a-reviewer.md").write_text(
        "---\nname: a-reviewer\ndescription: A role\ntools:\n  - Read\n  - Write\n---\n\nA prompt.\n"
    )

    roles = translate_installed_agents(discover_latest_plugins(cache_root))
    rendered = render_inline_codex_config(roles)

    assert '[agents.alpha_b_reviewer]' in rendered
    assert '[agents.beta_a_reviewer]' in rendered
    assert 'prompt = ".codex/prompts/agents/alpha-b-reviewer.md"' in rendered
    assert 'prompt = ".codex/prompts/agents/beta-a-reviewer.md"' in rendered
    assert '# original_claude_model_hint = "sonnet"' in rendered
    assert 'tools = ["read", "write"]' in rendered

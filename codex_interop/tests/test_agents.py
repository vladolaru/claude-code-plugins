"""Tests for Phase 2 agent translation and rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from codex_interop.discover import discover_latest_plugins
from codex_interop.model import InstalledPlugin, SemVer, TranslationError
from codex_interop.render_codex_config import (
    render_inline_codex_config,
    render_prompt_files,
)
from codex_interop.translate_agents import (
    _parse_frontmatter_lines,
    parse_markdown_with_frontmatter,
    translate_tools,
    translate_installed_agents,
)

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
    assert role.tools == ("bash", "read", "web_search")
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


def test_translate_tools_and_rendered_config_ignore_source_tool_order(make_plugin_version):
    """Equivalent tool sets produce the same translated order and config output."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "test-plugin",
        "1.0.0",
        agent_names=("reviewer",),
    )
    agent_path = version_dir / "agents" / "reviewer.md"
    agent_path.write_text(
        "---\n"
        "name: reviewer\n"
        "description: Review\n"
        "tools:\n"
        "  - Write\n"
        "  - Read\n"
        "  - Bash\n"
        "---\n\n"
        "Prompt body.\n"
    )

    first_roles = translate_installed_agents(discover_latest_plugins(cache_root))
    first_render = render_inline_codex_config(first_roles)

    agent_path.write_text(
        "---\n"
        "name: reviewer\n"
        "description: Review\n"
        "tools:\n"
        "  - Bash\n"
        "  - Write\n"
        "  - Read\n"
        "---\n\n"
        "Prompt body.\n"
    )

    second_roles = translate_installed_agents(discover_latest_plugins(cache_root))
    second_render = render_inline_codex_config(second_roles)

    assert first_roles[0].tools == ("bash", "read", "write")
    assert second_roles[0].tools == ("bash", "read", "write")
    assert first_render == second_render


def test_parse_markdown_with_frontmatter_supports_folded_scalars_and_nested_maps(tmp_path: Path):
    """The shared frontmatter parser accepts valid YAML-like multiline fields."""
    path = tmp_path / "skill.md"
    path.write_text(
        "---\n"
        "name: knowledge-capture\n"
        "description: >\n"
        "  Shared dex logic for project discovery,\n"
        "  CLAUDE.md budget management, and promotion flow.\n"
        "metadata:\n"
        "  short-description: Shared dex guidance\n"
        "---\n\n"
        "Body.\n"
    )

    frontmatter, body = parse_markdown_with_frontmatter(path)

    assert frontmatter["name"] == "knowledge-capture"
    assert frontmatter["description"] == (
        "Shared dex logic for project discovery, "
        "CLAUDE.md budget management, and promotion flow."
    )
    assert frontmatter["metadata"] == {"short-description": "Shared dex guidance"}
    assert body == "\nBody."


def test_translate_installed_agents_requires_name_and_description(make_plugin_version):
    """Missing required frontmatter fields fail clearly."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "test-plugin",
        "1.0.0",
        agent_names=("broken",),
    )
    (version_dir / "agents" / "broken.md").write_text("---\ndescription: Missing name\n---\n")

    with pytest.raises(TranslationError, match="missing required name"):
        translate_installed_agents(discover_latest_plugins(cache_root))

    (version_dir / "agents" / "broken.md").write_text("---\nname: broken\n---\n")

    with pytest.raises(TranslationError, match="missing required description"):
        translate_installed_agents(discover_latest_plugins(cache_root))


def test_translate_installed_agents_detects_duplicate_role_names(make_plugin_version):
    """Role-name collisions across plugins are rejected."""
    cache_root, version_dir = make_plugin_version("market", "alpha", "1.0.0", agent_names=("same",))
    first_agent = version_dir / "agents" / "same.md"
    first_agent.write_text("---\nname: same-role\ndescription: First\n---\n\nPrompt.\n")
    second_agent = version_dir / "agents" / "same-again.md"
    second_agent.write_text("---\nname: same role\ndescription: Second\n---\n\nPrompt.\n")

    plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="alpha",
        version_text="1.0.0",
        version=SemVer.parse("1.0.0"),
        installed_path=version_dir,
        source_path=version_dir,
        skills=(),
        agents=(first_agent, second_agent),
    )

    with pytest.raises(TranslationError, match="duplicate role name"):
        translate_installed_agents((plugin,))


def test_parse_markdown_with_frontmatter_handles_literal_blocks_and_errors(tmp_path: Path):
    """Parser covers literal blocks and malformed frontmatter."""
    literal = tmp_path / "literal.md"
    literal.write_text(
        "---\n"
        "name: literal\n"
        "description: |\n"
        "  line one\n"
        "  line two\n"
        "---\n\n"
        "Body.\n"
    )
    frontmatter, body = parse_markdown_with_frontmatter(literal)
    assert frontmatter["description"] == "line one\nline two"
    assert body == "\nBody."

    no_frontmatter = tmp_path / "plain.md"
    no_frontmatter.write_text("Plain body.\n")
    frontmatter, body = parse_markdown_with_frontmatter(no_frontmatter)
    assert frontmatter == {}
    assert body == "Plain body.\n"

    unclosed = tmp_path / "unclosed.md"
    unclosed.write_text("---\nname: broken\n")
    with pytest.raises(TranslationError, match="Unclosed frontmatter"):
        parse_markdown_with_frontmatter(unclosed)


def test_translate_tools_rejects_invalid_shapes():
    """Tool translation handles invalid non-list or non-string inputs."""
    assert translate_tools(None) == ()
    assert translate_tools(["Write", "Read", "Read", "Unknown"]) == ("read", "write")

    with pytest.raises(TranslationError, match="must be a list"):
        translate_tools("Read")

    with pytest.raises(TranslationError, match="must be a string"):
        translate_tools(["Read", 1])


def test_parse_frontmatter_lines_rejects_invalid_indentation():
    """Low-level frontmatter parser rejects invalid list and indentation shapes."""
    with pytest.raises(TranslationError, match="List item found before a frontmatter key"):
        _parse_frontmatter_lines(["- Read"])

    with pytest.raises(TranslationError, match="Unexpected indented frontmatter line"):
        _parse_frontmatter_lines([" name: bad"])

    with pytest.raises(TranslationError, match="Mixed scalar and list values"):
        _parse_frontmatter_lines(["name: reviewer", "  - Read"])

    with pytest.raises(TranslationError, match="Invalid frontmatter line"):
        _parse_frontmatter_lines(["broken"])

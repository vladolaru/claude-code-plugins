"""Rendering for generated Codex prompt files and inline config."""

from __future__ import annotations

from pathlib import Path

from codex_interop.model import GeneratedAgentRole


def render_prompt_files(
    roles: tuple[GeneratedAgentRole, ...],
) -> dict[Path, str]:
    """Render generated prompt file contents keyed by project-relative path."""
    rendered: dict[Path, str] = {}
    for role in roles:
        rendered[Path(".codex") / role.prompt_relpath] = role.prompt_body
    return rendered


def render_inline_codex_config(
    roles: tuple[GeneratedAgentRole, ...],
) -> str:
    """Render inline `.codex/config.toml` agent role config deterministically."""
    lines = [
        "# GENERATED FILE - DO NOT EDIT",
        "# Source: codex_interop phase 2",
        "",
    ]

    for role in roles:
        lines.extend(
            [
                f"[agents.{role.role_name}]",
                f'description = "{_escape(role.description)}"',
                f'model = "{_escape(role.model)}"',
                f'prompt = ".codex/{role.prompt_relpath.as_posix()}"',
                f"tools = [{_render_tools(role.tools)}]",
            ]
        )
        if role.original_model_hint:
            lines.append(
                f'# original_claude_model_hint = "{_escape(role.original_model_hint)}"'
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_tools(tools: tuple[str, ...]) -> str:
    """Render a deterministic TOML list of tool strings."""
    return ", ".join(f'"{_escape(tool)}"' for tool in tools)


def _escape(value: str) -> str:
    """Escape TOML double-quoted string content minimally."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


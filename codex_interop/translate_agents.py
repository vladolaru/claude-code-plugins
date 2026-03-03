"""Phase 2 translation from Claude agent markdown to Codex roles."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from codex_interop.model import GeneratedAgentRole, InstalledPlugin, TranslationError


TOOL_TRANSLATIONS = {
    "Read": "read",
    "Glob": "glob",
    "Grep": "grep",
    "Write": "write",
    "Bash": "bash",
    "WebSearch": "web_search",
}


def translate_installed_agents(
    plugins: Iterable[InstalledPlugin],
    *,
    default_model: str = "gpt-5.3-codex",
) -> tuple[GeneratedAgentRole, ...]:
    """Translate installed Claude agent files into Codex role definitions."""
    roles: list[GeneratedAgentRole] = []
    seen_role_names: set[str] = set()

    for plugin in plugins:
        for agent_path in plugin.agents:
            frontmatter, body = parse_markdown_with_frontmatter(agent_path)
            agent_name = str(frontmatter.get("name", "")).strip()
            if not agent_name:
                raise TranslationError(f"Agent missing required name frontmatter: {agent_path}")

            description = str(frontmatter.get("description", "")).strip()
            if not description:
                raise TranslationError(
                    f"Agent missing required description frontmatter: {agent_path}"
                )

            role_name = f"{plugin.plugin_name}_{_normalize_name(agent_name)}"
            if role_name in seen_role_names:
                raise TranslationError(f"Generated duplicate role name: {role_name}")
            seen_role_names.add(role_name)

            prompt_relpath = Path("prompts") / "agents" / f"{plugin.plugin_name}-{agent_name}.md"

            roles.append(
                GeneratedAgentRole(
                    plugin_name=plugin.plugin_name,
                    source_path=agent_path,
                    role_name=role_name,
                    description=description,
                    original_model_hint=_optional_str(frontmatter.get("model")),
                    model=default_model,
                    tools=translate_tools(frontmatter.get("tools")),
                    prompt_relpath=prompt_relpath,
                    prompt_body=body.strip() + ("\n" if body.strip() else ""),
                )
            )

    return tuple(sorted(roles, key=lambda role: role.role_name))


def parse_markdown_with_frontmatter(path: Path) -> tuple[dict[str, object], str]:
    """Parse simple YAML-like frontmatter plus markdown body."""
    content = path.read_text()
    if not content.startswith("---\n"):
        return {}, content

    lines = content.splitlines()
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        raise TranslationError(f"Unclosed frontmatter in: {path}")

    frontmatter_lines = lines[1:end_index]
    body = "\n".join(lines[end_index + 1 :])
    frontmatter = _parse_frontmatter_lines(frontmatter_lines)
    return frontmatter, body


def translate_tools(raw_tools: object) -> tuple[str, ...]:
    """Translate Claude tool names into Codex tool identifiers."""
    if raw_tools is None:
        return ()
    if not isinstance(raw_tools, list):
        raise TranslationError(f"Agent tools must be a list, got: {type(raw_tools).__name__}")

    translated: list[str] = []
    for tool in raw_tools:
        if not isinstance(tool, str):
            raise TranslationError(f"Agent tool entry must be a string, got: {tool!r}")
        translated_tool = TOOL_TRANSLATIONS.get(tool)
        if translated_tool and translated_tool not in translated:
            translated.append(translated_tool)

    return tuple(translated)


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, object]:
    """Parse the simple frontmatter shapes used by current Claude agent files."""
    result: dict[str, object] = {}
    current_key: str | None = None

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            continue

        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current_key is None:
                raise TranslationError("List item found before a frontmatter key")
            current_value = result.setdefault(current_key, [])
            if not isinstance(current_value, list):
                raise TranslationError(f"Mixed scalar and list values for key: {current_key}")
            current_value.append(stripped[2:].strip())
            continue

        if ":" not in line:
            raise TranslationError(f"Invalid frontmatter line: {line}")

        key, value = line.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        if value:
            result[current_key] = value
        else:
            result[current_key] = []

    return result


def _optional_str(value: object) -> str | None:
    """Return a stripped string or None."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _normalize_name(value: str) -> str:
    """Normalize agent names for Codex role identifiers."""
    return value.strip().replace("-", "_").replace(" ", "_")


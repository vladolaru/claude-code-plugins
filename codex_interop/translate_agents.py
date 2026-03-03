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

    return tuple(sorted(translated))


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, object]:
    """Parse the YAML frontmatter shapes used by current Claude and Codex assets."""
    result: dict[str, object] = {}
    current_key: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index].rstrip()
        if not line.strip():
            index += 1
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current_key is None:
                raise TranslationError("List item found before a frontmatter key")
            current_value = result.setdefault(current_key, [])
            if not isinstance(current_value, list):
                raise TranslationError(f"Mixed scalar and list values for key: {current_key}")
            current_value.append(stripped[2:].strip())
            index += 1
            continue

        if indent:
            if current_key is None:
                raise TranslationError(f"Unexpected indented frontmatter line: {line}")
            current_value = result.get(current_key)
            if isinstance(current_value, str):
                block_lines, next_index = _consume_block_scalar(lines, index, indent)
                separator = "\n" if "\n" in current_value else " "
                joined = separator.join(part.strip() for part in block_lines if part.strip())
                result[current_key] = f"{current_value}{separator}{joined}".strip()
                index = next_index
                continue
            if isinstance(current_value, dict):
                nested_key, nested_value = _parse_key_value(stripped)
                current_value[nested_key] = nested_value
                index += 1
                continue
            raise TranslationError(f"Unexpected indented frontmatter line: {line}")

        if ":" not in line:
            raise TranslationError(f"Invalid frontmatter line: {line}")

        key, value = _parse_key_value(line)
        current_key = key
        if value in {">", "|"}:
            block_lines, next_index = _consume_block_scalar(lines, index + 1, min_indent=1)
            result[current_key] = _join_block_scalar(block_lines, folded=(value == ">"))
            index = next_index
            continue
        if value:
            result[current_key] = value
        else:
            next_line = _next_nonempty_line(lines, index + 1)
            if next_line is None:
                result[current_key] = []
            else:
                next_indent = len(next_line) - len(next_line.lstrip(" "))
                next_stripped = next_line.lstrip()
                if next_indent == 0:
                    result[current_key] = []
                elif next_stripped.startswith("- "):
                    result[current_key] = []
                else:
                    result[current_key] = {}
        index += 1

    return result


def _parse_key_value(line: str) -> tuple[str, str]:
    """Parse one `key: value` line."""
    if ":" not in line:
        raise TranslationError(f"Invalid frontmatter line: {line}")
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _next_nonempty_line(lines: list[str], start_index: int) -> str | None:
    """Return the next non-empty line after `start_index`."""
    for index in range(start_index, len(lines)):
        if lines[index].strip():
            return lines[index]
    return None


def _consume_block_scalar(
    lines: list[str],
    start_index: int,
    min_indent: int,
) -> tuple[list[str], int]:
    """Consume consecutive indented lines for a block scalar."""
    collected: list[str] = []
    index = start_index
    base_indent: int | None = None

    while index < len(lines):
        raw_line = lines[index]
        if not raw_line.strip():
            collected.append("")
            index += 1
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent < min_indent:
            break
        if base_indent is None:
            base_indent = indent
        collected.append(raw_line[base_indent:].rstrip())
        index += 1

    return collected, index


def _join_block_scalar(lines: list[str], *, folded: bool) -> str:
    """Join YAML-like block scalar lines."""
    if folded:
        joined = " ".join(part.strip() for part in lines if part.strip())
        return joined.strip()
    return "\n".join(lines).rstrip()


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

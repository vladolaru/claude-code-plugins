#!/usr/bin/env python3
"""Generate Codex compatibility files from canonical Claude plugin sources."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"
CODEX_MARKETPLACE_PATH = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
GENERATED_MARKER = "GENERATED FILE - DO NOT EDIT"
GENERATED_SKILL_SOURCE_PREFIX = "<!-- Source: ./commands/"

CATEGORY_MAP = {
    "development-tools": "Developer Tools",
    "productivity": "Productivity",
    "security": "Security",
}

DISPLAY_NAME_OVERRIDES = {
    "dex": "DEX",
    "pirategoat-tools": "Pirategoat Tools",
    "yoloing-safe": "YOLOing Safe",
}

DEFAULT_PROMPT_OVERRIDES = {
    "caffeinate-claude": "Keep this Codex session awake while it works.",
    "yoloing-safe": "Use YOLOing Safe guardrails while working in this repository.",
}


@dataclass(frozen=True)
class ExpectedFile:
    path: Path
    content: str


def normalize_text(value: str) -> str:
    """Normalize punctuation in newly generated files."""
    return value.replace("\u2014", "-")


def display_name(plugin_name: str) -> str:
    return DISPLAY_NAME_OVERRIDES.get(
        plugin_name,
        plugin_name.replace("-", " ").title(),
    )


def short_description(description: str, limit: int = 120) -> str:
    description = normalize_text(description).strip()
    if len(description) <= limit:
        return description
    shortened = description[: limit - 1].rsplit(" ", 1)[0]
    return shortened.rstrip(".,;:") + "."


def load_marketplace() -> dict:
    marketplace = json.loads(CLAUDE_MARKETPLACE_PATH.read_text())
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        raise ValueError("Canonical marketplace must contain a non-empty plugins list")

    seen: set[str] = set()
    for entry in plugins:
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Every canonical plugin must have a name")
        if name in seen:
            raise ValueError(f"Duplicate canonical plugin name: {name}")
        seen.add(name)

        expected_source = f"./plugins/{name}"
        if entry.get("source") != expected_source:
            raise ValueError(
                f"{name}: source must be {expected_source}, got {entry.get('source')}"
            )
        plugin_root = REPO_ROOT / expected_source.removeprefix("./")
        if not plugin_root.is_dir():
            raise ValueError(f"{name}: plugin source directory does not exist")

    return marketplace


def render_codex_marketplace(canonical: dict) -> str:
    entries = []
    for plugin in canonical["plugins"]:
        entries.append(
            {
                "name": plugin["name"],
                "source": {
                    "source": "local",
                    "path": plugin["source"],
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": CATEGORY_MAP.get(
                    plugin.get("category", ""),
                    display_name(plugin.get("category", "Other")),
                ),
            }
        )

    result = {
        "name": canonical["name"],
        "interface": {
            "displayName": "Vlad Olaru's Claude Code and Codex Plugins",
        },
        "plugins": entries,
    }
    return json.dumps(result, indent=2, ensure_ascii=False) + "\n"


def render_manifest(plugin: dict) -> str:
    name = plugin["name"]
    description = normalize_text(plugin["description"])
    author = plugin.get("author") or {}
    author_name = author.get("name") or "Vlad Olaru"
    repository = plugin.get("repository") or (
        "https://github.com/vladolaru/claude-code-plugins"
    )

    manifest = {
        "name": name,
        "version": plugin["version"],
        "description": description,
        "author": {
            "name": author_name,
            "url": "https://github.com/vladolaru",
        },
        "homepage": repository,
        "repository": repository,
        "license": plugin.get("license", "MIT"),
        "keywords": plugin.get("keywords", []),
    }
    if plugin.get("commands"):
        manifest["skills"] = "./codex-skills/"

    capabilities = []
    if plugin.get("skills") or plugin.get("commands"):
        capabilities.extend(["Read", "Write"])
    plugin_root = REPO_ROOT / plugin["source"].removeprefix("./")
    if (plugin_root / "hooks" / "hooks.json").is_file():
        capabilities.append("Hooks")

    interface = {
        "displayName": display_name(name),
        "shortDescription": short_description(description),
        "longDescription": description,
        "developerName": author_name,
        "category": CATEGORY_MAP.get(
            plugin.get("category", ""),
            display_name(plugin.get("category", "Other")),
        ),
        "capabilities": capabilities,
        "websiteURL": repository,
    }
    commands = plugin.get("commands", [])
    if commands:
        interface["defaultPrompt"] = [
            f"Use ${name}:{Path(command).stem}."
            for command in commands[:3]
        ]
    else:
        interface["defaultPrompt"] = [
            DEFAULT_PROMPT_OVERRIDES.get(
                name,
                f"Use {display_name(name)} for this task.",
            )
        ]
    manifest["interface"] = interface

    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def parse_command(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text()
    if not text.startswith("---\n"):
        return {}, text

    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"{path}: unterminated frontmatter")

    frontmatter: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip("\"'")
    return frontmatter, text[end + 5 :]


def rewrite_same_plugin_commands(
    body: str,
    *,
    plugin_name: str,
    command_stems: list[str],
) -> str:
    for stem in sorted(command_stems, key=len, reverse=True):
        candidates = (f"/{plugin_name}:{stem}", f"/{stem}")
        for candidate in candidates:
            body = re.sub(
                rf"(?<![A-Za-z0-9_]){re.escape(candidate)}(?![A-Za-z0-9_-])",
                f"${plugin_name}:{stem}",
                body,
            )
    return body


def translate_command_body(
    body: str,
    *,
    plugin_name: str,
    command_stems: list[str],
) -> str:
    body = normalize_text(body)
    body = body.replace("${CLAUDE_PLUGIN_ROOT}", "${CODEX_PLUGIN_ROOT}")
    body = body.replace("$ARGUMENTS", "${CODEX_SKILL_ARGUMENTS}")
    body = body.replace("AskUserQuestion", "the host's user-input mechanism")
    body = rewrite_same_plugin_commands(
        body,
        plugin_name=plugin_name,
        command_stems=command_stems,
    )

    if plugin_name == "pirategoat-tools":
        body = body.replace(
            "scripts/review/pipeline.py \\\n",
            "scripts/review/pipeline.py \\\n  --host codex \\\n",
        )

    return body.rstrip() + "\n"


def render_command_skill(
    *,
    plugin_name: str,
    command_ref: str,
    command_path: Path,
    command_stems: list[str],
) -> str:
    frontmatter, body = parse_command(command_path)
    description = frontmatter.get("description") or (
        command_path.stem.replace("-", " ").replace("_", " ")
    )
    translated = translate_command_body(
        body,
        plugin_name=plugin_name,
        command_stems=command_stems,
    )

    return (
        "---\n"
        f"name: {command_path.stem}\n"
        f"description: {json.dumps(normalize_text(description), ensure_ascii=False)}\n"
        "---\n\n"
        f"<!-- {GENERATED_MARKER} -->\n"
        f"<!-- Source: {command_ref} -->\n\n"
        "## Codex Host Adapter\n\n"
        "This skill is generated from the canonical Claude Code command named "
        f"above. To execute it in Codex:\n\n"
        "1. Treat the text supplied after the skill mention as the invocation "
        "arguments. Substitute that exact text for "
        "`${CODEX_SKILL_ARGUMENTS}` before executing shell commands.\n"
        "2. Resolve `CODEX_PLUGIN_ROOT` to the absolute plugin root. The loaded "
        "skill directory is `<plugin-root>/codex-skills/<skill-name>`, so the "
        "plugin root is two directories above the directory containing this "
        "`SKILL.md`.\n"
        "3. Assign both variables explicitly in any shell call that uses them. "
        "Codex does not export these instruction variables automatically.\n"
        "4. Use Codex's available user-input and subagent tools when the "
        "workflow requests them.\n"
        "5. Follow the canonical workflow below without skipping its gates or "
        "artifact checks.\n\n"
        "## Canonical Workflow\n\n"
        f"{translated}"
    )


def render_openai_yaml(plugin_name: str, command_stem: str, description: str) -> str:
    default_prompt = f"Use ${plugin_name}:{command_stem}."
    return (
        f"# {GENERATED_MARKER}\n"
        f"# Source: ./commands/{command_stem}.md\n"
        "interface:\n"
        f"  display_name: {json.dumps(display_name(command_stem))}\n"
        f"  short_description: {json.dumps(short_description(description, 80))}\n"
        f"  default_prompt: {json.dumps(default_prompt)}\n"
        "policy:\n"
        "  allow_implicit_invocation: false\n"
    )


def expected_files(canonical: dict) -> list[ExpectedFile]:
    files = [
        ExpectedFile(
            CODEX_MARKETPLACE_PATH,
            render_codex_marketplace(canonical),
        )
    ]

    for plugin in canonical["plugins"]:
        plugin_root = REPO_ROOT / plugin["source"].removeprefix("./")
        files.append(
            ExpectedFile(
                plugin_root / ".codex-plugin" / "plugin.json",
                render_manifest(plugin),
            )
        )

        command_refs = plugin.get("commands", [])
        command_stems = [Path(ref).stem for ref in command_refs]
        for command_ref in command_refs:
            command_path = plugin_root / command_ref.removeprefix("./")
            if not command_path.is_file():
                raise ValueError(
                    f"{plugin['name']}: command does not exist: {command_ref}"
                )
            frontmatter, _ = parse_command(command_path)
            description = frontmatter.get("description") or command_path.stem.replace(
                "-", " "
            )
            skill_root = (
                plugin_root
                / "codex-skills"
                / command_path.stem
            )
            files.extend(
                [
                    ExpectedFile(
                        skill_root / "SKILL.md",
                        render_command_skill(
                            plugin_name=plugin["name"],
                            command_ref=command_ref,
                            command_path=command_path,
                            command_stems=command_stems,
                        ),
                    ),
                    ExpectedFile(
                        skill_root / "agents" / "openai.yaml",
                        render_openai_yaml(
                            plugin["name"],
                            command_path.stem,
                            description,
                        ),
                    ),
                ]
            )

    return files


def stale_generated_skill_dirs(expected: list[ExpectedFile]) -> list[Path]:
    expected_skills = {
        item.path.parent
        for item in expected
        if item.path.name == "SKILL.md"
    }
    stale: list[Path] = []
    patterns = (
        "plugins/*/skills/*/SKILL.md",
        "plugins/*/.codex-plugin/skills/*/SKILL.md",
        "plugins/*/codex-skills/*/SKILL.md",
    )
    for pattern in patterns:
        for skill_path in REPO_ROOT.glob(pattern):
            try:
                head = skill_path.read_text()[:500]
            except OSError:
                continue
            if (
                GENERATED_MARKER in head
                and GENERATED_SKILL_SOURCE_PREFIX in head
                and skill_path.parent not in expected_skills
            ):
                stale.append(skill_path.parent)
    return sorted(stale)


def empty_legacy_skill_dirs() -> list[Path]:
    """Find empty skill directories left by an older generator layout."""
    return sorted(
        path
        for path in REPO_ROOT.glob("plugins/*/.codex-plugin/skills")
        if path.is_dir() and not any(path.iterdir())
    )


def check(expected: list[ExpectedFile]) -> int:
    failures: list[str] = []
    for item in expected:
        if not item.path.is_file():
            failures.append(f"MISSING {item.path.relative_to(REPO_ROOT)}")
        elif item.path.read_text() != item.content:
            failures.append(f"STALE {item.path.relative_to(REPO_ROOT)}")

    for stale_dir in stale_generated_skill_dirs(expected):
        failures.append(f"UNEXPECTED {stale_dir.relative_to(REPO_ROOT)}")
    for empty_dir in empty_legacy_skill_dirs():
        failures.append(f"UNEXPECTED {empty_dir.relative_to(REPO_ROOT)}")

    if failures:
        print("\n".join(failures))
        print("Run: python3 scripts/generate_codex_compat.py")
        return 1

    print(f"Codex compatibility files are current ({len(expected)} files).")
    return 0


def write(expected: list[ExpectedFile]) -> int:
    changed = 0
    for stale_dir in stale_generated_skill_dirs(expected):
        shutil.rmtree(stale_dir)
        print(f"REMOVED {stale_dir.relative_to(REPO_ROOT)}")
        changed += 1
    for empty_dir in empty_legacy_skill_dirs():
        empty_dir.rmdir()
        print(f"REMOVED {empty_dir.relative_to(REPO_ROOT)}")
        changed += 1

    for item in expected:
        if item.path.is_file() and item.path.read_text() == item.content:
            continue
        item.path.parent.mkdir(parents=True, exist_ok=True)
        item.path.write_text(item.content)
        print(f"WROTE {item.path.relative_to(REPO_ROOT)}")
        changed += 1

    print(f"Codex compatibility generation complete ({changed} changes).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Codex compatibility files from Claude plugin sources"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when generated files are missing, stale, or unexpected",
    )
    args = parser.parse_args()

    try:
        canonical = load_marketplace()
        expected = expected_files(canonical)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    return check(expected) if args.check else write(expected)


if __name__ == "__main__":
    raise SystemExit(main())

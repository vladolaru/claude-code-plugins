"""Shared helpers for command tests."""

import json
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
COMMANDS_DIR = PLUGIN_ROOT / "commands"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
REPO_ROOT = PLUGIN_ROOT.parent.parent
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"

ORCHESTRATOR_COMMANDS = [
    "pr-review.md",
    "full-code-review.md",
    "code-review.md",
]

ALL_REVIEW_COMMANDS = ORCHESTRATOR_COMMANDS

AGENT_REF_PATTERN = re.compile(r"`pirategoat-tools:([\w-]+)`")
SCRIPT_REF_PATTERN = re.compile(r"(?:scripts/|/)(\w[\w-]*\.py)")


def read_command(filename: str) -> str:
    """Read a command file and return its content."""
    path = COMMANDS_DIR / filename
    return path.read_text()


def parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from a command file."""
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    fm_text = content[3:end].strip()
    result = {}
    for line in fm_text.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def extract_agent_refs(content: str) -> list:
    """Extract agent names from pirategoat-tools:xxx references in markdown."""
    return AGENT_REF_PATTERN.findall(content)


def load_marketplace_agents() -> list:
    """Load agent list from marketplace.json for the pirategoat-tools plugin."""
    data = json.loads(MARKETPLACE_JSON.read_text())
    for plugin in data["plugins"]:
        if plugin["name"] == "pirategoat-tools":
            return [Path(a).stem for a in plugin.get("agents", [])]
    return []


def load_marketplace_skills() -> list:
    """Load skill list from marketplace.json for the pirategoat-tools plugin."""
    data = json.loads(MARKETPLACE_JSON.read_text())
    for plugin in data["plugins"]:
        if plugin["name"] == "pirategoat-tools":
            return [Path(s).name for s in plugin.get("skills", [])]
    return []


def load_marketplace_commands() -> list:
    """Load command list from marketplace.json for the pirategoat-tools plugin."""
    data = json.loads(MARKETPLACE_JSON.read_text())
    for plugin in data["plugins"]:
        if plugin["name"] == "pirategoat-tools":
            return [Path(c).name for c in plugin.get("commands", [])
            ]
    return []

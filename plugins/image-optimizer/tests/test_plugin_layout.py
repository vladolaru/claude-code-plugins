import json
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]


def entry():
    data = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    return next(p for p in data["plugins"] if p["name"] == "image-optimizer")


def test_marketplace_registers_the_skill_and_version():
    e = entry()
    assert e["version"] == "2.0.0"
    assert e["skills"] == ["./skills/image-optimization"]
    assert e["commands"] == ["./commands/optimize-images.md"]
    assert "imageoptim-cli" not in e["description"]


def test_skill_frontmatter_and_script_path():
    text = (PLUGIN / "skills" / "image-optimization" / "SKILL.md").read_text()
    assert text.startswith("---\nname: image-optimization\n")
    assert '$SKILL_DIR/../../scripts/imgopt.py' in text
    assert (PLUGIN / "skills" / "image-optimization" / "../../scripts/imgopt.py").resolve().is_file()
    assert "${CLAUDE_SKILL_DIR}" not in text and "python3 -I" not in text.replace("never `python3 -I`", "")


def test_command_loads_the_skill_and_old_script_is_gone():
    text = (PLUGIN / "commands" / "optimize-images.md").read_text()
    assert "`image-optimization` skill" in text
    assert not (PLUGIN / "scripts" / "optimize-images.sh").exists()


def test_codex_gets_the_skill_beside_the_command_adapter():
    assert (PLUGIN / "codex-skills" / "image-optimization" / "SKILL.md").is_file()
    assert (PLUGIN / "codex-skills" / "optimize-images" / "SKILL.md").is_file()


def test_svgo_config_has_no_svgo3_override():
    assert "removeViewBox" not in (PLUGIN / "scripts" / "svgo.config.mjs").read_text()

"""Repository-wide tests for generated Codex marketplace compatibility."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CLAUDE_MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
CODEX_MARKETPLACE = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
GENERATOR = REPO_ROOT / "scripts" / "generate_codex_compat.py"
GENERATED_MARKER = "GENERATED FILE - DO NOT EDIT"


def _canonical_plugins() -> list[dict]:
    return json.loads(CLAUDE_MARKETPLACE.read_text())["plugins"]


def test_codex_marketplace_covers_every_canonical_plugin():
    assert CODEX_MARKETPLACE.is_file()
    marketplace = json.loads(CODEX_MARKETPLACE.read_text())
    canonical = _canonical_plugins()

    assert marketplace["name"] == "vladolaru-claude-code-plugins"
    assert marketplace["interface"]["displayName"]
    assert [entry["name"] for entry in marketplace["plugins"]] == [
        entry["name"] for entry in canonical
    ]

    for entry in marketplace["plugins"]:
        assert entry["source"] == {
            "source": "local",
            "path": f"./plugins/{entry['name']}",
        }
        assert entry["policy"] == {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        }
        assert entry["category"]


def test_every_plugin_has_matching_codex_manifest():
    for entry in _canonical_plugins():
        plugin_root = REPO_ROOT / entry["source"].removeprefix("./")
        manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
        assert manifest_path.is_file(), entry["name"]

        manifest = json.loads(manifest_path.read_text())
        assert manifest["name"] == entry["name"]
        assert manifest["version"] == entry["version"]
        assert manifest["description"] == entry["description"].replace("\u2014", "-")
        assert manifest["author"]["name"] == entry["author"]["name"]
        assert manifest["repository"] == entry["repository"]
        assert manifest["license"] == entry["license"]
        assert manifest["interface"]["displayName"]
        assert manifest["interface"]["shortDescription"]
        assert manifest["interface"]["developerName"] == entry["author"]["name"]
        assert sorted(path.name for path in manifest_path.parent.iterdir()) == [
            "plugin.json"
        ]

        if entry.get("commands"):
            assert manifest["skills"] == "./codex-skills/"
            assert (plugin_root / manifest["skills"]).is_dir()


def test_every_claude_command_has_generated_codex_skill():
    for entry in _canonical_plugins():
        plugin_root = REPO_ROOT / entry["source"].removeprefix("./")
        for command_ref in entry.get("commands", []):
            command_path = plugin_root / command_ref.removeprefix("./")
            skill_dir = (
                plugin_root
                / "codex-skills"
                / command_path.stem
            )
            skill_path = skill_dir / "SKILL.md"
            policy_path = skill_dir / "agents" / "openai.yaml"

            assert skill_path.is_file(), command_path
            skill_text = skill_path.read_text()
            assert GENERATED_MARKER in skill_text
            assert f"Source: {command_ref}" in skill_text
            assert f"name: {command_path.stem}" in skill_text
            assert "${CLAUDE_PLUGIN_ROOT}" not in skill_text
            assert "$ARGUMENTS" not in skill_text

            assert policy_path.is_file(), command_path
            policy_text = policy_path.read_text()
            assert GENERATED_MARKER in policy_text
            assert "allow_implicit_invocation: false" in policy_text
            assert not (
                plugin_root / "skills" / command_path.stem / "SKILL.md"
            ).exists()


def test_command_referenced_shared_skills_are_surfaced_to_codex():
    """A shared skill a command depends on is generated into codex-skills/ so
    Codex (which only loads codex-skills/) can resolve the delegated reference."""
    cases = [
        ("dex", "knowledge-capture", "./skills/knowledge-capture"),
        ("prompt-engineer", "prompt-engineer", "./skills/prompt-engineer"),
    ]
    for plugin_name, skill_name, source_ref in cases:
        plugin_root = REPO_ROOT / "plugins" / plugin_name
        surfaced = plugin_root / "codex-skills" / skill_name / "SKILL.md"
        assert surfaced.is_file(), surfaced
        text = surfaced.read_text()
        assert GENERATED_MARKER in text
        assert f"Source: {source_ref}" in text
        # Canonical frontmatter is preserved verbatim.
        assert f"name: {skill_name}" in text
        # The canonical skill remains the single source of truth.
        assert (plugin_root / "skills" / skill_name / "SKILL.md").is_file()


def test_surfaced_skill_includes_reference_assets():
    """A surfaced skill's sibling assets (files it reads via $SKILL_DIR) are
    copied alongside SKILL.md, so Codex reads don't fail on missing files."""
    refs = (
        REPO_ROOT / "plugins" / "prompt-engineer" / "codex-skills"
        / "prompt-engineer" / "references"
    )
    assert refs.is_dir()
    surfaced = {p.name for p in refs.glob("*.md")}
    canonical = {
        p.name
        for p in (
            REPO_ROOT / "plugins" / "prompt-engineer" / "skills"
            / "prompt-engineer" / "references"
        ).glob("*.md")
    }
    assert surfaced == canonical
    assert "prompt-engineering-single-turn.md" in surfaced


def test_unreferenced_shared_skills_are_not_surfaced_to_codex():
    """pirategoat's shared skills are not referenced by its command bodies, so
    they must not be pulled into Codex — surfacing is dependency-scoped."""
    codex_skills = REPO_ROOT / "plugins" / "pirategoat-tools" / "codex-skills"
    generated = {p.name for p in codex_skills.iterdir() if p.is_dir()}
    # Only the seven review/utility command adapters, no shared skills.
    assert "testing-patterns" not in generated
    assert "software-architecture" not in generated
    assert "using-figma" not in generated


def _load_generator():
    import importlib.util

    spec = importlib.util.spec_from_file_location("generate_codex_compat", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's dataclass string annotations resolve.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_arguments_substitution_respects_word_boundary():
    """`$ARGUMENTS` is rewritten but `$ARGUMENTS_LIST` (a longer name) is not."""
    gen = _load_generator()
    out = gen.translate_command_body(
        "run $ARGUMENTS then read $ARGUMENTS_LIST\n",
        plugin_name="example",
        command_stems=[],
    )
    assert "${CODEX_SKILL_ARGUMENTS}" in out
    assert "$ARGUMENTS_LIST" in out
    assert "${CODEX_SKILL_ARGUMENTS}_LIST" not in out


def test_pipeline_injection_fails_loudly_on_pattern_drift():
    """A pirategoat command that calls the pipeline but no longer matches the
    injection pattern must raise, not silently drop `--host codex`."""
    import pytest

    gen = _load_generator()
    with pytest.raises(ValueError, match="host codex"):
        gen.translate_command_body(
            "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review/pipeline.py --step 1\n",
            plugin_name="pirategoat-tools",
            command_stems=[],
        )


def test_pipeline_injection_skips_non_pipeline_commands():
    """Pirategoat commands that never call the pipeline (copy-as, switch-to)
    legitimately lack the pattern and must not raise."""
    gen = _load_generator()
    out = gen.translate_command_body(
        "Copy content to the clipboard. No pipeline here.\n",
        plugin_name="pirategoat-tools",
        command_stems=[],
    )
    assert "clipboard" in out


def test_review_command_adapters_select_codex_host():
    plugin_root = REPO_ROOT / "plugins" / "pirategoat-tools"
    for command_name in ("pr-review", "full-code-review", "code-review"):
        text = (
            plugin_root
            / "codex-skills"
            / command_name
            / "SKILL.md"
        ).read_text()
        assert "--host codex" in text


def test_canonical_skills_use_host_neutral_skill_directory():
    for skill_path in REPO_ROOT.glob("plugins/*/skills/*/SKILL.md"):
        text = skill_path.read_text()
        assert "${CLAUDE_SKILL_DIR}" not in text, skill_path
        if "$SKILL_DIR" in text:
            assert "directory containing this `SKILL.md`" in text
            assert "not a host-exported environment variable" in text


def test_generated_codex_compatibility_files_are_current():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_gitignored_dotfiles_are_not_surfaced_skill_assets():
    """Local machine junk (.DS_Store and friends) inside a surfaced shared
    skill must neither crash the generator (such files are often not UTF-8)
    nor be copied into codex-skills/. Non-ignored dotfiles remain assets."""
    skill_dir = REPO_ROOT / "plugins" / "dex" / "skills" / "knowledge-capture"
    assert skill_dir.is_dir(), "surfaced shared skill moved; update the test"
    junk = skill_dir / ".DS_Store"
    # Finder may already have dropped a real .DS_Store here — the very
    # environment this test exists to tolerate. Preserve and restore it
    # instead of asserting absence.
    original = junk.read_bytes() if junk.exists() else None
    try:
        # Real .DS_Store files are binary; invalid UTF-8 is the crash case.
        junk.write_bytes(b"Bud1\x00\x01\x86\x99junk")
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    finally:
        if original is None:
            junk.unlink(missing_ok=True)
        else:
            junk.write_bytes(original)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ".DS_Store" not in result.stdout

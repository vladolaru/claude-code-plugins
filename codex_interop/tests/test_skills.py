"""Tests for Phase 3 skill translation and materialization."""

from __future__ import annotations

from pathlib import Path

from codex_interop.discover import discover_latest_plugins
from codex_interop.translate_skills import (
    materialize_generated_skills,
    translate_installed_skills,
)


def test_materialize_generated_skills_copies_bundled_resources(make_plugin_version, tmp_path: Path):
    """A translated skill stays self-contained after installation."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    skill_dir = version_dir / "skills" / "prompt-engineer"
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: prompt-engineer\n"
        "description: Prompt help\n"
        "---\n\n"
        "Read `references/guide.md` before using `scripts/check.sh`.\n"
    )
    references_dir = skill_dir / "references"
    references_dir.mkdir()
    (references_dir / "guide.md").write_text("Reference material.\n")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    check_script = scripts_dir / "check.sh"
    check_script.write_text("#!/bin/sh\necho ok\n")
    check_script.chmod(0o755)

    skills = translate_installed_skills(discover_latest_plugins(cache_root))
    codex_home = tmp_path / "codex-home"
    installed_paths = materialize_generated_skills(skills, codex_home)

    assert installed_paths == (codex_home / "skills" / "prompt-engineer-prompt-engineer",)
    installed_root = installed_paths[0]
    assert (installed_root / "SKILL.md").read_text().startswith(
        "---\nname: prompt-engineer-prompt-engineer\n"
    )
    assert (installed_root / "references" / "guide.md").read_text() == "Reference material.\n"
    assert (installed_root / "scripts" / "check.sh").read_text() == "#!/bin/sh\necho ok\n"
    assert (installed_root / "scripts" / "check.sh").stat().st_mode & 0o777 == 0o755


def test_translate_installed_skills_rewrites_plugin_root_script_paths(
    make_plugin_version,
    tmp_path: Path,
):
    """Plugin-root script references are rewritten and vendored locally."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.0.0",
        skill_names=("decision-critic",),
    )
    skill_dir = version_dir / "skills" / "decision-critic"
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: decision-critic\n"
        "description: Criticize decisions\n"
        "---\n\n"
        'PLUGIN_ROOT="<skill base directory>/../.."\n'
        'python3 "$PLUGIN_ROOT/scripts/decision-critic.py"\n'
    )
    plugin_scripts_dir = version_dir / "scripts"
    plugin_scripts_dir.mkdir()
    (plugin_scripts_dir / "decision-critic.py").write_text("print('ok')\n")

    skills = translate_installed_skills(discover_latest_plugins(cache_root))
    materialize_generated_skills(skills, tmp_path / "codex-home")

    installed_root = tmp_path / "codex-home" / "skills" / "pirategoat-tools-decision-critic"
    skill_md = (installed_root / "SKILL.md").read_text()
    assert 'PLUGIN_ROOT="<skill base directory>/_plugin"' in skill_md
    assert (installed_root / "_plugin" / "scripts" / "decision-critic.py").read_text() == "print('ok')\n"


def test_translate_installed_skills_vendors_referenced_sibling_skills(
    make_plugin_version,
    tmp_path: Path,
):
    """Cross-skill relative references are rewritten to vendored sibling copies."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.0.0",
        skill_names=("e2e-testing-patterns", "testing-patterns"),
    )
    e2e_skill_dir = version_dir / "skills" / "e2e-testing-patterns"
    (e2e_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: e2e-testing-patterns\n"
        "description: E2E guidance\n"
        "---\n\n"
        "Read `../testing-patterns/references/test-philosophy.md` first.\n"
    )
    testing_skill_dir = version_dir / "skills" / "testing-patterns"
    (testing_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: testing-patterns\n"
        "description: Shared testing guidance\n"
        "---\n\n"
        "Sibling skill.\n"
    )
    sibling_references = testing_skill_dir / "references"
    sibling_references.mkdir()
    (sibling_references / "test-philosophy.md").write_text("Behavior over implementation.\n")

    skills = translate_installed_skills(discover_latest_plugins(cache_root))
    materialize_generated_skills(skills, tmp_path / "codex-home")

    installed_root = tmp_path / "codex-home" / "skills" / "pirategoat-tools-e2e-testing-patterns"
    skill_md = (installed_root / "SKILL.md").read_text()
    assert "_plugin/skills/testing-patterns/references/test-philosophy.md" in skill_md
    assert (
        installed_root / "_plugin" / "skills" / "testing-patterns" / "references" / "test-philosophy.md"
    ).read_text() == "Behavior over implementation.\n"


def test_translate_installed_skills_handles_name_and_directory_collisions(make_plugin_version):
    """Marketplace prefixes resolve deterministic collisions."""
    cache_root, alpha_dir = make_plugin_version(
        "alpha",
        "shared-plugin",
        "1.0.0",
        skill_names=("review",),
    )
    _, beta_dir = make_plugin_version(
        "beta",
        "shared-plugin",
        "1.0.0",
        skill_names=("review",),
    )
    for skill_dir in (alpha_dir / "skills" / "review", beta_dir / "skills" / "review"):
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: reviewer\n"
            "description: Review things\n"
            "---\n\n"
            "Review instructions.\n"
        )

    skills = translate_installed_skills(discover_latest_plugins(cache_root))

    assert [skill.install_dir_name for skill in skills] == [
        "alpha-shared-plugin-review",
        "beta-shared-plugin-review",
    ]
    assert [skill.codex_skill_name for skill in skills] == [
        "alpha-shared-plugin-review",
        "beta-shared-plugin-review",
    ]

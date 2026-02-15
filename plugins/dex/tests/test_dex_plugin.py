"""
Tests for the dex knowledge capture plugin — deterministic, no model calls.

Validates structural properties:
- All command files exist and have valid frontmatter
- Marketplace.json registration is complete and consistent
- Skill file exists with valid frontmatter
- Commands reference the shared skill
- Document format templates are present in the skill
- Key behavioral instructions are present in each command
"""

import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
COMMANDS_DIR = PLUGIN_ROOT / "commands"
SKILLS_DIR = PLUGIN_ROOT / "skills"
REPO_ROOT = PLUGIN_ROOT.parent.parent
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALL_COMMANDS = [
    "grok.md",
    "init.md",
    "learn.md",
    "pattern.md",
    "research.md",
    "sharpen.md",
    "status.md",
]

# Commands that capture knowledge (write documents)
CAPTURE_COMMANDS = [
    "learn.md",
    "pattern.md",
    "research.md",
    "sharpen.md",
]

SKILL_PATH = SKILLS_DIR / "knowledge-capture" / "SKILL.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_file(path: Path) -> str:
    """Read a file and return its content."""
    return path.read_text()


def _read_command(filename: str) -> str:
    """Read a command file and return its content."""
    return _read_file(COMMANDS_DIR / filename)


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from a markdown file.

    Returns dict with parsed fields, or empty dict if no frontmatter.
    """
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


def _load_marketplace_plugin() -> dict:
    """Load the dex plugin entry from marketplace.json."""
    data = json.loads(MARKETPLACE_JSON.read_text())
    for plugin in data["plugins"]:
        if plugin["name"] == "dex":
            return plugin
    return {}


def _load_marketplace_commands() -> list:
    """Load command filenames from marketplace.json for the dex plugin."""
    plugin = _load_marketplace_plugin()
    return [Path(c).name for c in plugin.get("commands", [])]


def _load_marketplace_skills() -> list:
    """Load skill directory names from marketplace.json for the dex plugin."""
    plugin = _load_marketplace_plugin()
    return [Path(s).name for s in plugin.get("skills", [])]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def marketplace_plugin():
    """The dex plugin entry from marketplace.json."""
    return _load_marketplace_plugin()


@pytest.fixture(scope="module")
def marketplace_commands():
    """Command filenames from marketplace.json."""
    return _load_marketplace_commands()


@pytest.fixture(scope="module")
def skill_content():
    """Content of the knowledge-capture SKILL.md."""
    return _read_file(SKILL_PATH)


# =============================================================================
# Plugin Registration
# =============================================================================


class TestMarketplaceRegistration:
    """dex plugin is properly registered in marketplace.json."""

    def test_plugin_exists_in_marketplace(self, marketplace_plugin):
        assert marketplace_plugin, "dex plugin not found in marketplace.json"

    def test_has_version(self, marketplace_plugin):
        assert "version" in marketplace_plugin
        assert re.match(r"^\d+\.\d+\.\d+$", marketplace_plugin["version"]), (
            f"Version '{marketplace_plugin['version']}' is not valid semver"
        )

    def test_has_description(self, marketplace_plugin):
        assert "description" in marketplace_plugin
        assert len(marketplace_plugin["description"]) > 20

    def test_has_keywords(self, marketplace_plugin):
        assert "keywords" in marketplace_plugin
        assert len(marketplace_plugin["keywords"]) >= 3

    def test_source_directory_exists(self, marketplace_plugin):
        source = REPO_ROOT / marketplace_plugin["source"]
        assert source.is_dir(), f"Source directory not found: {source}"

    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_command_registered(self, command, marketplace_commands):
        assert command in marketplace_commands, (
            f"{command} not registered in marketplace.json commands: {marketplace_commands}"
        )

    def test_all_registered_commands_exist_on_disk(self, marketplace_plugin):
        for cmd_path in marketplace_plugin.get("commands", []):
            full_path = PLUGIN_ROOT / cmd_path
            assert full_path.is_file(), (
                f"Registered command not found on disk: {full_path}"
            )

    def test_all_registered_skills_exist_on_disk(self, marketplace_plugin):
        for skill_path in marketplace_plugin.get("skills", []):
            full_path = PLUGIN_ROOT / skill_path / "SKILL.md"
            assert full_path.is_file(), (
                f"Registered skill not found on disk: {full_path}"
            )

    def test_no_agents_registered(self, marketplace_plugin):
        """dex should have no agents — commands run in main conversation."""
        assert "agents" not in marketplace_plugin or len(marketplace_plugin.get("agents", [])) == 0


# =============================================================================
# Command Frontmatter
# =============================================================================


class TestCommandFrontmatter:
    """All commands have valid YAML frontmatter."""

    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_file_exists(self, command):
        path = COMMANDS_DIR / command
        assert path.is_file(), f"Command file not found: {path}"

    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_has_frontmatter(self, command):
        content = _read_command(command)
        assert content.startswith("---"), f"{command}: missing frontmatter delimiter"
        end = content.find("---", 3)
        assert end > 3, f"{command}: unclosed frontmatter"

    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_has_description(self, command):
        content = _read_command(command)
        fm = _parse_frontmatter(content)
        assert "description" in fm, f"{command}: frontmatter missing 'description'"
        assert len(fm["description"]) > 10, f"{command}: description too short"


# =============================================================================
# Skill Structure
# =============================================================================


class TestSkillStructure:
    """knowledge-capture skill has required structure."""

    def test_skill_file_exists(self):
        assert SKILL_PATH.is_file(), f"Skill file not found: {SKILL_PATH}"

    def test_skill_has_frontmatter(self, skill_content):
        assert skill_content.startswith("---"), "SKILL.md: missing frontmatter"
        end = skill_content.find("---", 3)
        assert end > 3, "SKILL.md: unclosed frontmatter"

    def test_skill_has_name(self, skill_content):
        fm = _parse_frontmatter(skill_content)
        assert "name" in fm, "SKILL.md: frontmatter missing 'name'"
        assert fm["name"] == "knowledge-capture"

    def test_skill_has_description(self, skill_content):
        fm = _parse_frontmatter(skill_content)
        assert "description" in fm, "SKILL.md: frontmatter missing 'description'"


# =============================================================================
# Skill Content — Document Formats
# =============================================================================


class TestSkillDocumentFormats:
    """Skill contains all three document format templates."""

    def test_has_learning_format(self, skill_content):
        assert "## Rule" in skill_content, "Skill missing Learning format (## Rule section)"
        assert "## Context" in skill_content, "Skill missing Learning format (## Context section)"
        assert "## Examples" in skill_content, "Skill missing Learning format (## Examples section)"

    def test_has_pattern_format(self, skill_content):
        assert "## Pattern" in skill_content, "Skill missing Pattern format"
        assert "When to apply" in skill_content, "Skill missing Pattern format (When to apply)"
        assert "Alternatives" in skill_content, "Skill missing Pattern format (Alternatives)"
        assert "Reference implementation" in skill_content, "Skill missing Pattern format (Reference implementation)"

    def test_has_decision_format(self, skill_content):
        assert "## Decision" in skill_content, "Skill missing Decision format"
        assert "Alternatives considered" in skill_content, "Skill missing Decision format (Alternatives)"
        assert "Why this choice" in skill_content, "Skill missing Decision format (Why this choice)"

    def test_has_research_format(self, skill_content):
        assert "### Research Format" in skill_content, "Skill missing Research Format section"
        assert "## Summary" in skill_content, "Skill missing Research format (Summary)"
        assert "What Works" in skill_content, "Skill missing Research format (What Works)"
        assert "What Doesn't Work" in skill_content, "Skill missing Research format (What Doesn't Work)"
        assert "Key Findings" in skill_content, "Skill missing Research format (Key Findings)"
        assert "Environment" in skill_content, "Skill missing Research format (Environment)"
        assert "Status" in skill_content, "Skill missing Research format (Status)"


# =============================================================================
# Skill Content — Core Logic
# =============================================================================


class TestSkillCoreLogic:
    """Skill contains required core logic sections."""

    def test_has_discovery_section(self, skill_content):
        assert "Project Discovery" in skill_content, "Skill missing Project Discovery section"
        assert "git rev-parse" in skill_content, "Skill missing git root detection"

    def test_has_scaffolding_section(self, skill_content):
        assert "Scaffolding" in skill_content, "Skill missing Scaffolding section"
        assert "learnings" in skill_content
        assert "patterns" in skill_content
        assert "decisions" in skill_content
        assert "research" in skill_content

    def test_has_promotion_section(self, skill_content):
        assert "Promotion" in skill_content, "Skill missing Promotion section"

    def test_has_budget_enforcement(self, skill_content):
        assert "500" in skill_content, "Skill missing 500-line budget reference"
        assert "550" in skill_content, "Skill missing 550-line hard block reference"

    def test_has_auto_placement(self, skill_content):
        assert "Auto-Placement" in skill_content, "Skill missing Auto-Placement section"

    def test_has_extraction_guidance(self, skill_content):
        assert "Extraction" in skill_content, "Skill missing knowledge extraction guidance"

    def test_has_filename_convention(self, skill_content):
        assert "YYYY-MM-DD" in skill_content, "Skill missing filename date convention"

    def test_promotion_uses_bare_path(self, skill_content):
        assert "Details:" in skill_content, (
            "Skill promoted rule format should use bare path (Details: path)"
        )
        # The promoted rule format section should not use markdown link syntax
        # Find the Promoted Rule Format section and check its code block
        promo_idx = skill_content.find("### Promoted Rule Format")
        assert promo_idx != -1, "Skill missing Promoted Rule Format section"
        promo_section = skill_content[promo_idx:promo_idx + 500]
        assert "Details:" in promo_section, (
            "Promoted Rule Format section should use bare path format"
        )

    def test_has_agent_behavior_analysis_section(self, skill_content):
        assert "Agent Behavior Analysis" in skill_content, (
            "Skill missing Agent Behavior Analysis section"
        )

    def test_has_inefficiency_categories(self, skill_content):
        assert "Inefficiency Categories" in skill_content, (
            "Skill missing Inefficiency Categories subsection"
        )
        for category in ["Wrong tool usage", "Inefficient discovery", "Missed shortcuts"]:
            assert category in skill_content, (
                f"Skill missing inefficiency category: {category}"
            )

    def test_has_root_cause_classification(self, skill_content):
        assert "Root Cause Classification" in skill_content, (
            "Skill missing Root Cause Classification subsection"
        )
        assert "learnings/" in skill_content or "`.claude/docs/learnings/`" in skill_content, (
            "Skill root cause classification missing learnings destination"
        )
        assert "patterns/" in skill_content or "`.claude/docs/patterns/`" in skill_content, (
            "Skill root cause classification missing patterns destination"
        )

    def test_has_sharpen_extraction_quality(self, skill_content):
        assert "Sharpen Extraction Quality" in skill_content, (
            "Skill missing Sharpen Extraction Quality subsection"
        )
        assert "Agent-operational" in skill_content, "Skill missing Agent-operational quality check"
        assert "Preventive" in skill_content, "Skill missing Preventive quality check"
        assert "Non-obvious" in skill_content, "Skill missing Non-obvious quality check"


# =============================================================================
# Command Content — Router (dex.md)
# =============================================================================


class TestGrokRouter:
    """grok.md routes to the correct handlers."""

    def test_classifies_four_types(self):
        content = _read_command("grok.md")
        content_lower = content.lower()
        assert "learning" in content_lower, "grok.md: missing 'learning' classification"
        assert "pattern" in content_lower, "grok.md: missing 'pattern' classification"
        assert "decision" in content_lower, "grok.md: missing 'decision' classification"
        assert "research" in content_lower, "grok.md: missing 'research' classification"

    def test_uses_ask_user_question(self):
        content = _read_command("grok.md")
        assert "AskUserQuestion" in content, "grok.md: missing AskUserQuestion for classification"

    def test_delegates_to_learn(self):
        content = _read_command("grok.md")
        assert "dex:learn" in content or "learn command" in content.lower(), (
            "grok.md: missing delegation to learn"
        )

    def test_delegates_to_pattern(self):
        content = _read_command("grok.md")
        assert "dex:pattern" in content or "pattern command" in content.lower(), (
            "grok.md: missing delegation to pattern"
        )

    def test_delegates_to_research(self):
        content = _read_command("grok.md")
        assert "dex:research" in content or "research command" in content.lower(), (
            "grok.md: missing delegation to research"
        )

    def test_has_graduation_prompt(self):
        """grok.md offers to upgrade a learning to pattern when reusability signals are detected."""
        content = _read_command("grok.md")
        assert "reusability" in content.lower() or "graduation" in content.lower(), (
            "grok.md: missing graduation/reusability language"
        )
        assert "upgrade to pattern" in content.lower(), (
            "grok.md: missing upgrade-to-pattern AskUserQuestion option"
        )

    def test_decision_uses_decision_format(self):
        content = _read_command("grok.md")
        assert "Decision Format" in content or "decision" in content.lower(), (
            "grok.md: missing decision format handling"
        )


# =============================================================================
# Command Content — Init (init.md)
# =============================================================================


class TestInitCommand:
    """init.md scaffolds knowledge infrastructure."""

    def test_scans_for_existing_infrastructure(self):
        content = _read_command("init.md")
        assert ".claude/docs" in content, "init.md: missing .claude/docs reference"

    def test_creates_four_directories(self):
        content = _read_command("init.md")
        assert "learnings" in content, "init.md: missing learnings directory"
        assert "patterns" in content, "init.md: missing patterns directory"
        assert "decisions" in content, "init.md: missing decisions directory"
        assert "research" in content, "init.md: missing research directory"

    def test_uses_ask_user_question(self):
        content = _read_command("init.md")
        assert "AskUserQuestion" in content, "init.md: missing AskUserQuestion for confirmation"

    def test_handles_already_exists(self):
        content = _read_command("init.md")
        assert "already" in content.lower(), "init.md: missing already-exists handling"

    def test_references_knowledge_capture_skill(self):
        content = _read_command("init.md")
        assert "knowledge-capture" in content, "init.md: missing reference to knowledge-capture skill"


# =============================================================================
# Command Content — Learn (learn.md)
# =============================================================================


class TestLearnCommand:
    """learn.md captures learnings with promotion flow."""

    def test_references_skill(self):
        content = _read_command("learn.md")
        assert "knowledge-capture" in content, "learn.md: missing reference to knowledge-capture skill"

    def test_has_discovery_step(self):
        content = _read_command("learn.md")
        assert "Discovery" in content or "Discover" in content, "learn.md: missing discovery step"

    def test_has_extraction_step(self):
        content = _read_command("learn.md")
        assert "Extract" in content, "learn.md: missing extraction step"

    def test_uses_ask_user_question_for_confirm(self):
        content = _read_command("learn.md")
        assert "AskUserQuestion" in content, "learn.md: missing AskUserQuestion"
        assert "Accept" in content, "learn.md: missing Accept option"

    def test_has_promotion_flow(self):
        content = _read_command("learn.md")
        assert "CLAUDE.md" in content, "learn.md: missing CLAUDE.md promotion"
        assert "rule" in content.lower(), "learn.md: missing rule-worthy evaluation"

    def test_writes_to_learnings_dir(self):
        content = _read_command("learn.md")
        assert "learnings/" in content, "learn.md: missing learnings/ output path"

    def test_offers_scaffolding(self):
        content = _read_command("learn.md")
        assert "scaffold" in content.lower() or "Create" in content, (
            "learn.md: missing first-run scaffolding"
        )


# =============================================================================
# Command Content — Pattern (pattern.md)
# =============================================================================


class TestPatternCommand:
    """pattern.md captures patterns with promotion flow."""

    def test_references_skill(self):
        content = _read_command("pattern.md")
        assert "knowledge-capture" in content, "pattern.md: missing reference to knowledge-capture skill"

    def test_has_discovery_step(self):
        content = _read_command("pattern.md")
        assert "Discovery" in content or "Discover" in content, "pattern.md: missing discovery step"

    def test_has_extraction_step(self):
        content = _read_command("pattern.md")
        assert "Extract" in content, "pattern.md: missing extraction step"

    def test_uses_ask_user_question_for_confirm(self):
        content = _read_command("pattern.md")
        assert "AskUserQuestion" in content, "pattern.md: missing AskUserQuestion"
        assert "Accept" in content, "pattern.md: missing Accept option"

    def test_has_promotion_flow(self):
        content = _read_command("pattern.md")
        assert "CLAUDE.md" in content, "pattern.md: missing CLAUDE.md promotion"

    def test_writes_to_patterns_dir(self):
        content = _read_command("pattern.md")
        assert "patterns/" in content, "pattern.md: missing patterns/ output path"

    def test_has_when_to_apply(self):
        content = _read_command("pattern.md")
        assert "When to apply" in content or "when to apply" in content.lower(), (
            "pattern.md: missing 'when to apply' extraction"
        )


# =============================================================================
# Command Content — Research (research.md)
# =============================================================================


class TestResearchCommand:
    """research.md captures research findings without promotion."""

    def test_references_skill(self):
        content = _read_command("research.md")
        assert "knowledge-capture" in content, "research.md: missing reference to knowledge-capture skill"

    def test_has_discovery_step(self):
        content = _read_command("research.md")
        assert "Discovery" in content or "Discover" in content, "research.md: missing discovery step"

    def test_has_extraction_step(self):
        content = _read_command("research.md")
        assert "Extract" in content, "research.md: missing extraction step"

    def test_uses_ask_user_question(self):
        content = _read_command("research.md")
        assert "AskUserQuestion" in content, "research.md: missing AskUserQuestion"
        assert "Accept" in content, "research.md: missing Accept option"

    def test_writes_to_research_dir(self):
        content = _read_command("research.md")
        assert "research/" in content, "research.md: missing research/ output path"

    def test_no_promotion_step(self):
        content = _read_command("research.md")
        content_lower = content.lower()
        assert "no" in content_lower and "promotion" in content_lower, (
            "research.md: must explicitly state no CLAUDE.md promotion"
        )

    def test_environment_field(self):
        content = _read_command("research.md")
        assert "Environment" in content, "research.md: missing Environment field extraction"

    def test_status_field(self):
        content = _read_command("research.md")
        assert "Status" in content, "research.md: missing Status field"
        assert "current" in content, "research.md: should set Status to current"

    def test_summary_not_rule(self):
        """Research extracts Summary, not Rule — it's reference material."""
        content = _read_command("research.md")
        assert "Summary" in content, "research.md: missing Summary extraction"

    def test_what_works_and_doesnt(self):
        content = _read_command("research.md")
        assert "What Works" in content, "research.md: missing What Works section"
        assert "What Doesn't Work" in content or "What Doesn't Work" in content, (
            "research.md: missing What Doesn't Work section"
        )

    def test_offers_scaffolding(self):
        content = _read_command("research.md")
        assert "scaffold" in content.lower() or "Create" in content, (
            "research.md: missing first-run scaffolding"
        )


# =============================================================================
# Command Content — Status (status.md)
# =============================================================================


class TestStatusCommand:
    """status.md reports knowledge health."""

    def test_references_skill(self):
        content = _read_command("status.md")
        assert "knowledge-capture" in content, "status.md: missing reference to knowledge-capture skill"

    def test_reports_claude_md_lines(self):
        content = _read_command("status.md")
        assert "500" in content or "line" in content.lower(), (
            "status.md: missing CLAUDE.md line count reporting"
        )

    def test_reports_doc_counts(self):
        content = _read_command("status.md")
        assert "learnings" in content.lower(), "status.md: missing learnings count"
        assert "patterns" in content.lower(), "status.md: missing patterns count"
        assert "decisions" in content.lower(), "status.md: missing decisions count"
        assert "research" in content.lower(), "status.md: missing research count"

    def test_reports_latest_oldest(self):
        content = _read_command("status.md")
        assert "Latest" in content or "latest" in content, "status.md: missing latest entry"
        assert "Oldest" in content or "oldest" in content, "status.md: missing oldest entry"

    def test_handles_missing_infrastructure(self):
        content = _read_command("status.md")
        content_lower = content.lower()
        assert "not" in content_lower or "missing" in content_lower or "no " in content_lower, (
            "status.md: missing handling for absent .claude/docs/"
        )

    def test_is_read_only(self):
        """status.md should not write or modify anything."""
        content = _read_command("status.md")
        lower = content.lower()
        assert (
            "no modifications" in lower
            or "no input" in lower
            or "read-only" in lower
            or "modifies nothing" in lower
        ), "status.md: should explicitly state it makes no modifications"


# =============================================================================
# Command Content — Sharpen (sharpen.md)
# =============================================================================


class TestSharpenCommand:
    """sharpen.md analyzes agent behavior and captures efficiency fixes."""

    def test_references_shared_skill(self):
        content = _read_command("sharpen.md")
        assert "knowledge-capture" in content, (
            "sharpen.md: missing reference to knowledge-capture skill"
        )

    def test_has_behavior_analysis_step(self):
        content = _read_command("sharpen.md")
        content_lower = content.lower()
        assert "inefficien" in content_lower, (
            "sharpen.md: missing inefficiency analysis step"
        )
        assert "behavior" in content_lower or "behaviour" in content_lower, (
            "sharpen.md: missing behavior analysis reference"
        )

    def test_has_root_cause_classification(self):
        content = _read_command("sharpen.md")
        assert "Root Cause" in content or "root cause" in content.lower(), (
            "sharpen.md: missing root cause classification reference"
        )

    def test_uses_ask_user_question(self):
        content = _read_command("sharpen.md")
        assert "AskUserQuestion" in content, (
            "sharpen.md: missing AskUserQuestion for confirmation"
        )

    def test_has_promotion_flow(self):
        content = _read_command("sharpen.md")
        assert "CLAUDE.md" in content, "sharpen.md: missing CLAUDE.md promotion"
        assert "Promot" in content or "promot" in content, (
            "sharpen.md: missing promotion flow"
        )

    def test_writes_to_knowledge_dirs(self):
        content = _read_command("sharpen.md")
        assert "learnings/" in content or "patterns/" in content, (
            "sharpen.md: missing reference to learnings/ or patterns/ output"
        )

    def test_identifies_inefficiency_types(self):
        """Must mention at least 3 inefficiency categories by reference."""
        content = _read_command("sharpen.md")
        assert "Inefficiency Categories" in content, (
            "sharpen.md: must reference Inefficiency Categories from skill"
        )

    def test_handles_no_inefficiencies(self):
        content = _read_command("sharpen.md")
        content_lower = content.lower()
        assert "no inefficien" in content_lower or "none" in content_lower, (
            "sharpen.md: must handle the 'nothing found' case"
        )

    def test_has_early_exit(self):
        content = _read_command("sharpen.md")
        content_lower = content.lower()
        assert "stop" in content_lower or "abort" in content_lower, (
            "sharpen.md: must have early exit path for no inefficiencies"
        )

    def test_references_document_formats(self):
        content = _read_command("sharpen.md")
        assert "Learning Format" in content or "Pattern Format" in content, (
            "sharpen.md: must reference standard document formats from skill"
        )

    def test_references_subagent_analyzer(self):
        content = _read_command("sharpen.md")
        assert "analyze-subagents" in content, (
            "sharpen.md: must reference sub-agent analyzer script"
        )


# =============================================================================
# Script Existence
# =============================================================================


class TestScriptExists:
    """Plugin scripts exist on disk."""

    def test_analyze_subagents_exists(self):
        path = PLUGIN_ROOT / "scripts" / "analyze-subagents.py"
        assert path.is_file(), f"Script not found: {path}"


# =============================================================================
# Cross-Cutting Concerns
# =============================================================================


class TestCrossCutting:
    """Properties that apply across multiple commands."""

    @pytest.mark.parametrize("command", CAPTURE_COMMANDS)
    def test_capture_commands_reference_ask_user_question(self, command):
        content = _read_command(command)
        assert "AskUserQuestion" in content, (
            f"{command}: capture commands must use AskUserQuestion"
        )

    @pytest.mark.parametrize("command", CAPTURE_COMMANDS)
    def test_capture_commands_reference_skill(self, command):
        content = _read_command(command)
        assert "knowledge-capture" in content, (
            f"{command}: must reference knowledge-capture skill"
        )

    @pytest.mark.parametrize("command", CAPTURE_COMMANDS)
    def test_capture_commands_offer_scaffolding(self, command):
        content = _read_command(command)
        assert ".claude/docs" in content, (
            f"{command}: must reference .claude/docs/ for scaffolding"
        )

    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_no_agent_dispatching(self, command):
        """dex commands should not dispatch subagents via Task tool."""
        content = _read_command(command)
        assert "subagent_type" not in content.lower(), (
            f"{command}: dex commands should not dispatch subagents"
        )
        # Check for Task tool invocation patterns (but not analysis of traces)
        assert "Task tool" not in content or "analyze" in content.lower(), (
            f"{command}: dex commands should not dispatch via Task tool"
        )

    def test_changelog_exists(self):
        path = PLUGIN_ROOT / "CHANGELOG.md"
        assert path.is_file(), "CHANGELOG.md not found"

    def test_readme_exists(self):
        path = PLUGIN_ROOT / "README.md"
        assert path.is_file(), "README.md not found"


# =============================================================================
# Gap 1: Capture Automation — Init Proactive Directive
# =============================================================================


class TestInitCaptureDirective:
    """init.md offers to add a proactive /dex capture directive to CLAUDE.md."""

    def test_mentions_capture_directive(self):
        """init.md should offer a CLAUDE.md directive for proactive capture."""
        content = _read_command("init.md")
        assert "/dex" in content and "CLAUDE.md" in content, (
            "init.md: must mention adding /dex reference to CLAUDE.md"
        )
        # Must mention the concept of proactive/automatic suggestion
        content_lower = content.lower()
        assert any(word in content_lower for word in [
            "proactive", "suggest", "remind", "directive", "nudge",
        ]), (
            "init.md: must describe proactive capture suggestion"
        )

    def test_checks_for_existing_dex_directive(self):
        """Before offering, should check if CLAUDE.md already has a /dex reference."""
        content = _read_command("init.md")
        # Must explicitly mention checking CLAUDE.md for an existing capture directive
        # The word "directive" must appear in context of checking/skipping
        assert "directive" in content.lower(), (
            "init.md: must mention checking for existing capture directive in CLAUDE.md"
        )

    def test_uses_ask_user_question_for_directive(self):
        """The directive offer should use AskUserQuestion for confirmation."""
        content = _read_command("init.md")
        # Already has one AskUserQuestion for scaffolding; should have a second
        count = content.count("AskUserQuestion")
        assert count >= 2, (
            f"init.md: expected at least 2 AskUserQuestion calls (scaffolding + directive), found {count}"
        )


# =============================================================================
# Gap 2: Cross-Session Intelligence — Sharpen Audit Log
# =============================================================================


class TestSharpenAuditLog:
    """sharpen.md writes an audit log for cross-session intelligence."""

    def test_sharpen_mentions_audit_log(self):
        """sharpen.md should reference an audit log mechanism."""
        content = _read_command("sharpen.md")
        content_lower = content.lower()
        assert "audit" in content_lower or "log" in content_lower, (
            "sharpen.md: must reference audit log for cross-session tracking"
        )
        assert ".sharpen-log" in content or "sharpen-log" in content_lower, (
            "sharpen.md: must reference .sharpen-log file"
        )

    def test_sharpen_reads_existing_log(self):
        """sharpen.md should read the audit log to avoid duplicating previous findings."""
        content = _read_command("sharpen.md")
        content_lower = content.lower()
        assert any(phrase in content_lower for phrase in [
            "existing log", "previous findings", "previously captured",
            "read the audit", "check the audit", "read .sharpen-log",
        ]), (
            "sharpen.md: must read existing audit log for deduplication"
        )

    def test_sharpen_appends_to_log(self):
        """sharpen.md should append entries to the audit log after capturing."""
        content = _read_command("sharpen.md")
        content_lower = content.lower()
        assert "append" in content_lower and ".sharpen-log" in content_lower, (
            "sharpen.md: must append new entries to the .sharpen-log audit log"
        )

    def test_skill_has_audit_log_format(self):
        """knowledge-capture SKILL.md should define the audit log format."""
        content = _read_file(SKILL_PATH)
        assert "Sharpen Audit Log" in content or "Audit Log" in content, (
            "SKILL.md: must define the sharpen audit log format"
        )


# =============================================================================
# Gap 3: Knowledge Hygiene — Status Freshness Warnings
# =============================================================================


class TestStatusFreshness:
    """status.md includes freshness warnings for stale knowledge."""

    def test_mentions_freshness_or_staleness(self):
        """status.md should include freshness analysis."""
        content = _read_command("status.md")
        content_lower = content.lower()
        assert any(word in content_lower for word in [
            "freshness", "stale", "staleness", "age", "aging", "outdated",
        ]), (
            "status.md: must include freshness/staleness analysis"
        )

    def test_has_age_threshold(self):
        """status.md should define when a doc is considered stale."""
        content = _read_command("status.md")
        # Should mention a specific number of days as threshold
        assert any(f"{n} day" in content.lower() for n in [30, 60, 90, 120, 180]) or "day" in content.lower(), (
            "status.md: must define an age threshold for staleness (e.g., 90 days)"
        )

    def test_warns_about_stale_docs(self):
        """status.md should produce a warning when docs are stale."""
        content = _read_command("status.md")
        content_lower = content.lower()
        assert any(phrase in content_lower for phrase in [
            "review", "refresh", "update", "may be outdated", "consider reviewing",
        ]), (
            "status.md: must suggest reviewing stale documents"
        )

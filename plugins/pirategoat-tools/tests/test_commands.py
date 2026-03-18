"""Tests for review command files — shared structural tests + review commands."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_commands_helpers import (
    read_command,
    parse_frontmatter,
    extract_agent_refs,
    load_marketplace_agents,
    load_marketplace_skills,
    load_marketplace_commands,
    ALL_REVIEW_COMMANDS,
    ALL_COMMANDS,
    ORCHESTRATOR_COMMANDS,
    COMMANDS_DIR,
    SCRIPTS_DIR,
    AGENT_REF_PATTERN,
    SCRIPT_REF_PATTERN,
    MARKETPLACE_JSON,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def marketplace_agents():
    """Agent names from marketplace.json (e.g., 'pr-reviewer', 'security-reviewer')."""
    return load_marketplace_agents()


@pytest.fixture(scope="module")
def marketplace_skills():
    """Skill names from marketplace.json (e.g., 'decision-critic', 'reviewing-pr')."""
    return load_marketplace_skills()


@pytest.fixture(scope="module")
def marketplace_commands():
    """Command filenames from marketplace.json."""
    return load_marketplace_commands()


# =============================================================================
# Structural Tests — Frontmatter
# =============================================================================


class TestFrontmatter:
    """All review commands have valid frontmatter."""

    @pytest.mark.parametrize("command", ALL_REVIEW_COMMANDS)
    def test_command_file_exists(self, command):
        path = COMMANDS_DIR / command
        assert path.is_file(), f"Command file not found: {path}"

    @pytest.mark.parametrize("command", ALL_REVIEW_COMMANDS)
    def test_has_frontmatter(self, command):
        content = read_command(command)
        assert content.startswith("---"), f"{command}: missing frontmatter delimiter"
        end = content.find("---", 3)
        assert end > 3, f"{command}: unclosed frontmatter"

    @pytest.mark.parametrize("command", ALL_REVIEW_COMMANDS)
    def test_has_description(self, command):
        content = read_command(command)
        fm = parse_frontmatter(content)
        assert "description" in fm, f"{command}: frontmatter missing 'description'"
        assert len(fm["description"]) > 10, f"{command}: description too short"


# =============================================================================
# Structural Tests — All Commands (review + non-review)
# =============================================================================


NON_REVIEW_COMMANDS = [c for c in ALL_COMMANDS if c not in ALL_REVIEW_COMMANDS]


class TestAllCommandsStructural:
    """Every command file exists, has valid frontmatter, and is registered."""

    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_command_file_exists(self, command):
        path = COMMANDS_DIR / command
        assert path.is_file(), f"Command file not found: {path}"

    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_has_frontmatter_with_description(self, command):
        content = read_command(command)
        assert content.startswith("---"), f"{command}: missing frontmatter delimiter"
        fm = parse_frontmatter(content)
        assert "description" in fm, f"{command}: frontmatter missing 'description'"
        assert len(fm["description"]) > 10, f"{command}: description too short"

    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_registered_in_marketplace(self, command, marketplace_commands):
        assert command in marketplace_commands, (
            f"{command}: not registered in marketplace.json commands: {marketplace_commands}"
        )

    @pytest.mark.parametrize("command", NON_REVIEW_COMMANDS)
    def test_non_review_commands_not_in_review_list(self, command):
        """Non-review commands must NOT appear in ALL_REVIEW_COMMANDS."""
        assert command not in ALL_REVIEW_COMMANDS, (
            f"{command}: should not be in ALL_REVIEW_COMMANDS"
        )


# =============================================================================
# Structural Tests — Script References (all commands reference review-pipeline.py)
# =============================================================================


class TestScriptReferences:
    """Review commands reference review-pipeline.py (which exists on disk)."""

    @pytest.mark.parametrize("command", ALL_REVIEW_COMMANDS)
    def test_references_review_pipeline(self, command):
        content = read_command(command)
        assert "review-pipeline.py" in content, (
            f"{command}: should reference review-pipeline.py"
        )

    def test_review_pipeline_exists(self):
        path = SCRIPTS_DIR / "review-pipeline.py"
        assert path.is_file(), f"review-pipeline.py not found at {path}"


class TestReviewCommandsReferenceUnifiedScript:
    """All review commands reference review-pipeline.py with correct mode."""

    def test_pr_review_uses_pr_mode(self):
        content = read_command("pr-review.md")
        assert "--mode pr" in content

    def test_full_code_review_uses_full_mode(self):
        content = read_command("full-code-review.md")
        assert "--mode full" in content

    def test_code_review_uses_incremental_mode(self):
        content = read_command("code-review.md")
        assert "--mode incremental" in content


# =============================================================================
# Structural Tests — Marketplace Registration
# =============================================================================


class TestMarketplaceRegistration:
    """Review commands are registered in marketplace.json."""

    @pytest.mark.parametrize("command", ALL_REVIEW_COMMANDS)
    def test_command_in_marketplace(self, command, marketplace_commands):
        assert command in marketplace_commands, (
            f"{command}: not registered in marketplace.json commands: {marketplace_commands}"
        )


# =============================================================================
# Structural Tests — Command-Specific Content
# =============================================================================


class TestCodeReviewIterative:
    """code-review.md has iterative-specific content."""

    def test_has_incremental_mode(self):
        content = read_command("code-review.md")
        assert "incremental" in content.lower(), (
            "code-review.md: missing 'incremental' keyword"
        )

    def test_has_full_reset_option(self):
        content = read_command("code-review.md")
        assert "full" in content.lower() and "reset" in content.lower(), (
            "code-review.md: missing 'full' or 'reset' argument handling"
        )

    def test_has_full_reset_deletes_baseline(self):
        """full/reset should delete .branch-review-baseline.json."""
        content = read_command("code-review.md")
        assert ".branch-review-baseline.json" in content, (
            "code-review.md: should reference .branch-review-baseline.json for full/reset"
        )


class TestFullCodeReview:
    """full-code-review.md has expected structure."""

    def test_has_full_mode(self):
        content = read_command("full-code-review.md")
        assert "--mode full" in content


# =============================================================================
# Unified Mission Tests — All review commands share identity language
# =============================================================================


class TestUnifiedMission:
    """All review commands share unified mission language."""

    @pytest.mark.parametrize("command", ORCHESTRATOR_COMMANDS)
    def test_has_orchestrator_identity(self, command):
        content = read_command(command)
        assert "code review orchestrator" in content.lower()

    @pytest.mark.parametrize("command", ORCHESTRATOR_COMMANDS)
    def test_has_artifact_discipline(self, command):
        """Commands should instruct treating artifacts as contracts."""
        content = read_command(command)
        assert "artifact" in content.lower() or "contract" in content.lower() or "verify" in content.lower()

    @pytest.mark.parametrize("command", ORCHESTRATOR_COMMANDS)
    def test_no_pr_specific_identity(self, command):
        """No command should say 'PR review orchestrator' — identity is mode-agnostic."""
        content = read_command(command)
        assert "pr review orchestrator" not in content.lower()


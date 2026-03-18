"""Tests for pr-update.md — detailed content checks."""

import pytest

from test_commands_helpers import (
    read_command,
    parse_frontmatter,
    load_marketplace_commands,
    ALL_REVIEW_COMMANDS,
    COMMANDS_DIR,
)


@pytest.fixture(scope="module")
def marketplace_commands():
    return load_marketplace_commands()


class TestPrUpdate:
    """pr-update.md has expected structure and content."""

    COMMAND = "pr-update.md"

    def test_file_exists(self):
        path = COMMANDS_DIR / self.COMMAND
        assert path.is_file(), f"Command file not found: {path}"

    def test_has_frontmatter_with_description(self):
        content = read_command(self.COMMAND)
        assert content.startswith("---"), f"{self.COMMAND}: missing frontmatter delimiter"
        fm = parse_frontmatter(content)
        assert "description" in fm, f"{self.COMMAND}: frontmatter missing 'description'"
        assert len(fm["description"]) > 10, f"{self.COMMAND}: description too short"

    def test_has_pr_detection(self):
        """Should reference gh pr view for PR detection."""
        content = read_command(self.COMMAND)
        assert "gh pr view" in content, (
            f"{self.COMMAND}: missing 'gh pr view' for PR detection"
        )

    def test_has_template_detection(self):
        """Should search for PULL_REQUEST_TEMPLATE."""
        content = read_command(self.COMMAND)
        assert "PULL_REQUEST_TEMPLATE" in content, (
            f"{self.COMMAND}: missing PULL_REQUEST_TEMPLATE detection"
        )

    def test_has_validation_step(self):
        """Should have a validation/verify pass before presenting."""
        content = read_command(self.COMMAND)
        assert "Validation" in content or "verify" in content.lower(), (
            f"{self.COMMAND}: missing validation step"
        )

    def test_has_user_approval_gate(self):
        """Should wait for user approval before updating."""
        content = read_command(self.COMMAND)
        assert "approval" in content.lower() or "confirm" in content.lower(), (
            f"{self.COMMAND}: missing user approval gate"
        )

    def test_has_pr_edit(self):
        """Should reference gh pr edit for updating the PR."""
        content = read_command(self.COMMAND)
        assert "pr edit" in content, (
            f"{self.COMMAND}: missing 'pr edit' for PR update"
        )

    def test_has_ghe_fallback(self):
        """Should support ghe as fallback for GitHub Enterprise."""
        content = read_command(self.COMMAND)
        assert "ghe" in content, (
            f"{self.COMMAND}: missing 'ghe' fallback for GitHub Enterprise"
        )

    def test_has_stop_conditions(self):
        """Should have STOP conditions for invalid states."""
        content = read_command(self.COMMAND)
        content_lower = content.lower()
        assert "stop" in content_lower or "halt" in content_lower, (
            f"{self.COMMAND}: missing STOP conditions"
        )
        # Should guard against merged/closed PRs
        assert "merged" in content_lower or "closed" in content_lower, (
            f"{self.COMMAND}: missing merged/closed PR guard"
        )

    def test_has_brevity_calibration(self):
        """Should scale description depth to PR size (small/medium/large)."""
        content = read_command(self.COMMAND)
        content_lower = content.lower()
        assert "small" in content_lower and "large" in content_lower, (
            f"{self.COMMAND}: missing size-based brevity calibration"
        )

    def test_has_artifact_discovery(self):
        """Should discover and filter review/plan artifacts."""
        content = read_command(self.COMMAND)
        assert "artifact" in content.lower(), (
            f"{self.COMMAND}: missing artifact discovery"
        )

    def test_not_in_review_commands(self):
        """pr-update should NOT be in ALL_REVIEW_COMMANDS (it's not a review orchestrator)."""
        assert self.COMMAND not in ALL_REVIEW_COMMANDS, (
            f"{self.COMMAND}: should not be in ALL_REVIEW_COMMANDS"
        )

    def test_registered_in_marketplace(self, marketplace_commands):
        """pr-update.md should be registered in marketplace.json."""
        assert self.COMMAND in marketplace_commands, (
            f"{self.COMMAND}: not registered in marketplace.json commands: {marketplace_commands}"
        )


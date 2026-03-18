"""Tests for switch-to.md — detailed content checks."""

import pytest

from test_commands_helpers import (
    read_command,
    parse_frontmatter,
    load_marketplace_commands,
    COMMANDS_DIR,
)


@pytest.fixture(scope="module")
def marketplace_commands():
    return load_marketplace_commands()


class TestSwitchTo:
    """switch-to.md has expected structure and content."""

    COMMAND = "switch-to.md"

    # --- Structural ---

    def test_file_exists(self):
        path = COMMANDS_DIR / self.COMMAND
        assert path.is_file(), f"Command file not found: {path}"

    def test_has_frontmatter_with_description(self):
        content = read_command(self.COMMAND)
        assert content.startswith("---"), f"{self.COMMAND}: missing frontmatter delimiter"
        fm = parse_frontmatter(content)
        assert "description" in fm, f"{self.COMMAND}: frontmatter missing 'description'"
        assert len(fm["description"]) > 10, f"{self.COMMAND}: description too short"

    def test_registered_in_marketplace(self, marketplace_commands):
        assert self.COMMAND in marketplace_commands, (
            f"{self.COMMAND}: not registered in marketplace.json: {marketplace_commands}"
        )

    # --- Input handling ---

    def test_has_argument_parsing(self):
        """Should parse $ARGUMENTS for branch name or PR URL."""
        content = read_command(self.COMMAND)
        assert "$ARGUMENTS" in content, (
            f"{self.COMMAND}: missing $ARGUMENTS parsing"
        )

    def test_has_empty_arguments_guard(self):
        """Should STOP when no arguments provided."""
        content = read_command(self.COMMAND)
        content_lower = content.lower()
        assert "stop" in content_lower and "empty" in content_lower or "usage" in content_lower, (
            f"{self.COMMAND}: missing empty arguments guard"
        )

    def test_detects_pr_url(self):
        """Should detect PR URLs by /pull/ pattern."""
        content = read_command(self.COMMAND)
        assert "/pull/" in content, (
            f"{self.COMMAND}: missing PR URL detection (/pull/)"
        )

    def test_has_already_on_branch_early_exit(self):
        """Should handle already being on the target branch."""
        content = read_command(self.COMMAND)
        content_lower = content.lower()
        assert "already on" in content_lower, (
            f"{self.COMMAND}: missing already-on-branch early exit"
        )

    def test_has_error_normalization(self):
        """Should normalize expected git/gh failures."""
        content = read_command(self.COMMAND)
        content_lower = content.lower()
        assert "expected failures" in content_lower, (
            f"{self.COMMAND}: missing error normalization for expected failures"
        )

    # --- Dirty working tree handling ---

    def test_has_dirty_state_check(self):
        """Should check git status --porcelain before switching."""
        content = read_command(self.COMMAND)
        assert "git status --porcelain" in content, (
            f"{self.COMMAND}: missing dirty state check (git status --porcelain)"
        )

    def test_has_stash_option(self):
        """Should offer to stash changes."""
        content = read_command(self.COMMAND)
        assert "git stash push" in content, (
            f"{self.COMMAND}: missing git stash push for dirty state"
        )

    def test_has_commit_first_option(self):
        """Should offer to commit first as an alternative to stashing."""
        content = read_command(self.COMMAND)
        content_lower = content.lower()
        assert "commit first" in content_lower or "commit your changes" in content_lower, (
            f"{self.COMMAND}: missing 'commit first' option"
        )

    def test_has_cancel_option(self):
        """Should offer to cancel the switch."""
        content = read_command(self.COMMAND)
        content_lower = content.lower()
        assert "cancel" in content_lower or "abort" in content_lower, (
            f"{self.COMMAND}: missing cancel option"
        )

    def test_has_ask_user_for_dirty_state(self):
        """Should use AskUserQuestion when working tree is dirty."""
        content = read_command(self.COMMAND)
        assert "AskUserQuestion" in content, (
            f"{self.COMMAND}: missing AskUserQuestion for dirty state"
        )

    # --- Branch switching ---

    def test_has_local_branch_check(self):
        """Should check if branch exists locally."""
        content = read_command(self.COMMAND)
        assert "git branch --list" in content, (
            f"{self.COMMAND}: missing local branch check (git branch --list)"
        )

    def test_has_git_checkout(self):
        """Should use git checkout to switch branches."""
        content = read_command(self.COMMAND)
        assert "git checkout" in content, (
            f"{self.COMMAND}: missing git checkout"
        )

    def test_has_remote_branch_check(self):
        """Should check remote for branch if not found locally."""
        content = read_command(self.COMMAND)
        assert "git ls-remote" in content or "git fetch" in content, (
            f"{self.COMMAND}: missing remote branch check"
        )

    def test_has_branch_not_found_stop(self):
        """Should STOP if branch not found locally or on remote."""
        content = read_command(self.COMMAND)
        content_lower = content.lower()
        assert "not found" in content_lower, (
            f"{self.COMMAND}: missing 'branch not found' STOP condition"
        )

    # --- Remote sync ---

    def test_has_remote_sync_check(self):
        """Should check for new remote commits after switching."""
        content = read_command(self.COMMAND)
        assert "git fetch" in content, (
            f"{self.COMMAND}: missing git fetch for remote sync"
        )

    def test_has_pull_rebase_option(self):
        """Should offer pull --rebase as an option."""
        content = read_command(self.COMMAND)
        assert "--rebase" in content, (
            f"{self.COMMAND}: missing pull --rebase option"
        )

    def test_has_ask_user_for_remote_sync(self):
        """Should use AskUserQuestion when remote has new commits."""
        content = read_command(self.COMMAND)
        assert content.count("AskUserQuestion") >= 2, (
            f"{self.COMMAND}: expected at least 2 AskUserQuestion calls "
            f"(dirty state + remote sync)"
        )

    # --- PR flow ---

    def test_has_gh_pr_view(self):
        """Should use gh pr view to get PR details."""
        content = read_command(self.COMMAND)
        assert "gh pr view" in content, (
            f"{self.COMMAND}: missing gh pr view for PR details"
        )

    def test_has_repo_validation(self):
        """Should validate CWD repo matches PR repo."""
        content = read_command(self.COMMAND)
        assert "gh repo view" in content, (
            f"{self.COMMAND}: missing gh repo view for repo validation"
        )

    def test_has_fork_handling(self):
        """Should handle fork PRs by adding the fork as a remote."""
        content = read_command(self.COMMAND)
        assert "git remote add" in content, (
            f"{self.COMMAND}: missing fork remote handling (git remote add)"
        )

    def test_has_base_branch_fetch(self):
        """Should fetch the base branch for PR flow."""
        content = read_command(self.COMMAND)
        assert "BASE_BRANCH" in content, (
            f"{self.COMMAND}: missing BASE_BRANCH fetch for PR flow"
        )

    # --- Post-switch context ---

    def test_has_post_switch_log(self):
        """Should show recent commits after switching."""
        content = read_command(self.COMMAND)
        assert "git log --oneline" in content, (
            f"{self.COMMAND}: missing post-switch commit log"
        )

    def test_has_ahead_behind(self):
        """Should show ahead/behind status."""
        content = read_command(self.COMMAND)
        assert "rev-list --left-right --count" in content, (
            f"{self.COMMAND}: missing ahead/behind count"
        )

    def test_has_stash_reminder(self):
        """Should remind user about stashed changes in post-switch context."""
        content = read_command(self.COMMAND)
        assert "git stash pop" in content, (
            f"{self.COMMAND}: missing stash pop reminder"
        )

"""Git rule tests."""

import pytest


def get_detect(hook, rule_id):
    """Get the detection function for a rule, whether custom or generated."""
    return hook.RULES[rule_id]["_detect"]


class TestGitBarePush:
    """Test detect_git_bare_push — blocks git push without explicit branch."""

    @pytest.mark.parametrize("command", [
        "git push",
        "git push origin",
        "git push -u origin",
        "git push --set-upstream origin",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = get_detect(hook, "git_bare_push")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "git push origin HEAD",
        "git push origin main",
        "git push -u origin HEAD",
        "git push --set-upstream origin feature/branch",
        "git push origin HEAD:refs/heads/main",
        "git push --tags",
        "git push origin --tags",
        "git push --all",
        "git push --force",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "git_bare_push")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False


class TestGitForcePush:
    @pytest.mark.parametrize("command", [
        "git push --force",
        "git push -f",
        "git push origin main --force",
        "git push -f origin main",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = get_detect(hook, "git_force_push")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "git push --force-with-lease",
        "git push --force-if-includes",
        "git push origin main",
        "git push",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "git_force_push")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False


class TestGitHardReset:
    @pytest.mark.parametrize("command", [
        "git reset --hard",
        "git reset --hard HEAD~3",
        "git reset --merge",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = get_detect(hook, "git_hard_reset")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "git reset --soft",
        "git reset --soft HEAD~1",
        "git reset HEAD file.txt",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "git_hard_reset")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False


class TestGitDiscardChanges:
    @pytest.mark.parametrize("command", [
        "git checkout -- .",
        "git checkout -- file.txt",
        "git checkout HEAD~3 -- src/",
        "git restore file.txt",
        "git restore --worktree file.txt",
        "git restore -W file.txt",
        "git restore --staged --worktree file.txt",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = get_detect(hook, "git_discard_changes")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "git checkout -b branch",
        "git checkout main",
        "git restore --staged file.txt",
        "git restore -S file.txt",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "git_discard_changes")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False


class TestGitDestroyStash:
    @pytest.mark.parametrize("command", [
        "git stash drop",
        "git stash clear",
        "git stash drop stash@{0}",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = get_detect(hook, "git_destroy_stash")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "git stash",
        "git stash pop",
        "git stash list",
        "git stash apply",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "git_destroy_stash")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False


class TestGitHistoryRewrite:
    @pytest.mark.parametrize("command", [
        "git filter-branch --tree-filter 'rm -rf passwords' HEAD",
        "git filter-repo --path secret.txt --invert-paths",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = get_detect(hook, "git_history_rewrite")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None


class TestGitConfigChanges:
    @pytest.mark.parametrize("command", [
        'git config --global user.name "x"',
        "git config --system core.editor vim",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = get_detect(hook, "git_config_changes")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        'git config user.name "x"',
        "git config --local user.email a@b.com",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "git_config_changes")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False


class TestGitOtherDangerous:
    @pytest.mark.parametrize("command", [
        "git clean -fd",
        "git clean -f",
        "git branch -D feat",
        "git remote remove origin",
        "git reflog expire --expire=now --all",
        "git gc --prune=now",
        "git push origin --delete feature-branch",
        "git push origin :refs/heads/feature-branch",
        "git push origin :refs/tags/v1.0.0",
        "git push origin :feature-branch",
        "git push origin :hotfix/urgent",
        "git push origin -d feature-branch",
        "git push -d origin feature-branch",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = get_detect(hook, "git_other_dangerous")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "git branch -d feat",
        "git remote add origin url",
        "git gc",
        "git status",
        "git log",
        "git push origin main",
        "git push origin HEAD",
        "git push --tags",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "git_other_dangerous")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False

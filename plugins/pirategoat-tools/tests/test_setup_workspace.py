"""Tests for setup-workspace.py."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "setup-workspace.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("setup_workspace", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run_cmd_mock(responses):
    """Create a _run_cmd mock that returns based on command patterns.

    Args:
        responses: dict mapping a command substring to a return value.
                   The first matching key (checked in insertion order) wins.
                   If no key matches, returns None.
    """
    def mock_run_cmd(cmd, cwd=None):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        for pattern, value in responses.items():
            if pattern in cmd_str:
                return value
        return None
    return mock_run_cmd


# ---------------------------------------------------------------------------
# TestResolveGhCmd
# ---------------------------------------------------------------------------

class TestResolveGhCmd:
    """Detect gh vs ghe from git remote origin URL."""

    def test_gh_default(self, mod):
        """No a8c/automattic domain → gh."""
        mock = _make_run_cmd_mock({
            "get-url origin": "https://github.com/user/repo.git",
        })
        with patch.object(mod, "_run_cmd", side_effect=mock):
            assert mod.resolve_gh_cmd() == "gh"

    def test_ghe_from_a8c_remote(self, mod):
        """a8c.com in origin → ghe."""
        mock = _make_run_cmd_mock({
            "get-url origin": "https://code.a8c.com/org/repo.git",
        })
        with patch.object(mod, "_run_cmd", side_effect=mock):
            assert mod.resolve_gh_cmd() == "ghe"

    def test_ghe_from_automattic_remote(self, mod):
        """automattic.com in origin → ghe."""
        mock = _make_run_cmd_mock({
            "get-url origin": "https://git.automattic.com/org/repo.git",
        })
        with patch.object(mod, "_run_cmd", side_effect=mock):
            assert mod.resolve_gh_cmd() == "ghe"

    def test_fallback_on_failure(self, mod):
        """If git remote fails, default to gh."""
        mock = _make_run_cmd_mock({})  # all commands return None
        with patch.object(mod, "_run_cmd", side_effect=mock):
            assert mod.resolve_gh_cmd() == "gh"


# ---------------------------------------------------------------------------
# TestSetupWorkspace
# ---------------------------------------------------------------------------

class TestSetupWorkspace:
    """Core workspace setup logic."""

    def test_clean_workspace(self, mod):
        """Clean repo: no stash needed, records branch, checkout succeeds."""
        mock = _make_run_cmd_mock({
            "branch --show-current": "main",
            "status --porcelain": "",
            "pr checkout": "",
        })
        with patch.object(mod, "_run_cmd", side_effect=mock):
            result = mod.setup_workspace(pr_number="42", gh_cmd="gh")

        assert result["original_branch"] == "main"
        assert result["was_dirty"] is False
        assert result["stash_ref"] is None
        assert result["checkout_ok"] is True
        assert "error" not in result

    def test_dirty_workspace(self, mod):
        """Dirty repo: stash created, ref captured from git stash list."""
        mock = _make_run_cmd_mock({
            "branch --show-current": "feature-branch",
            "status --porcelain": " M src/app.js\n?? new-file.txt",
            "stash push": "",
            "stash list": "stash@{0}: On feature-branch: pr-review-auto-stash",
            "pr checkout": "",
        })
        with patch.object(mod, "_run_cmd", side_effect=mock):
            result = mod.setup_workspace(pr_number="99", gh_cmd="gh")

        assert result["original_branch"] == "feature-branch"
        assert result["was_dirty"] is True
        assert result["stash_ref"] == "stash@{0}"
        assert result["checkout_ok"] is True
        assert "error" not in result

    def test_checkout_failure(self, mod):
        """Checkout fails: error reported, workspace still recorded."""
        mock = _make_run_cmd_mock({
            "branch --show-current": "main",
            "status --porcelain": "",
            "pr checkout": None,  # failure
        })
        # Override to return None specifically for checkout
        def selective_mock(cmd, cwd=None):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "pr checkout" in cmd_str:
                return None
            if "branch --show-current" in cmd_str:
                return "main"
            if "status --porcelain" in cmd_str:
                return ""
            return None

        with patch.object(mod, "_run_cmd", side_effect=selective_mock):
            result = mod.setup_workspace(pr_number="42", gh_cmd="gh")

        assert result["original_branch"] == "main"
        assert result["checkout_ok"] is False
        assert "error" in result

    def test_uses_ghe_when_specified(self, mod):
        """When gh_cmd is ghe, checkout uses ghe."""
        commands_seen = []

        def tracking_mock(cmd, cwd=None):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            commands_seen.append(cmd_str)
            if "branch --show-current" in cmd_str:
                return "main"
            if "status --porcelain" in cmd_str:
                return ""
            if "pr checkout" in cmd_str:
                return ""
            return None

        with patch.object(mod, "_run_cmd", side_effect=tracking_mock):
            result = mod.setup_workspace(pr_number="42", gh_cmd="ghe")

        assert result["checkout_ok"] is True
        # Verify ghe was used in the checkout command
        checkout_cmds = [c for c in commands_seen if "pr checkout" in c]
        assert len(checkout_cmds) == 1
        assert "ghe" in checkout_cmds[0]

    def test_branch_detection_failure(self, mod):
        """If branch detection fails, falls back to 'unknown'."""
        mock = _make_run_cmd_mock({
            "branch --show-current": None,  # failure
            "status --porcelain": "",
            "pr checkout": "",
        })
        # Override for branch returning None
        def selective_mock(cmd, cwd=None):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "branch --show-current" in cmd_str:
                return None
            if "status --porcelain" in cmd_str:
                return ""
            if "pr checkout" in cmd_str:
                return ""
            return None

        with patch.object(mod, "_run_cmd", side_effect=selective_mock):
            result = mod.setup_workspace(pr_number="42", gh_cmd="gh")

        assert result["original_branch"] == "unknown"
        assert result["checkout_ok"] is True

    def test_untracked_files_trigger_stash(self, mod):
        """Untracked files (status output) mean dirty → stash with -u flag."""
        commands_seen = []

        def tracking_mock(cmd, cwd=None):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            commands_seen.append(cmd_str)
            if "branch --show-current" in cmd_str:
                return "dev"
            if "status --porcelain" in cmd_str:
                return "?? untracked.txt"
            if "stash push" in cmd_str:
                return ""
            if "stash list" in cmd_str:
                return "stash@{0}: On dev: pr-review-auto-stash"
            if "pr checkout" in cmd_str:
                return ""
            return None

        with patch.object(mod, "_run_cmd", side_effect=tracking_mock):
            result = mod.setup_workspace(pr_number="42", gh_cmd="gh")

        assert result["was_dirty"] is True
        assert result["stash_ref"] == "stash@{0}"
        # Verify stash command included -u flag for untracked files
        stash_cmds = [c for c in commands_seen if "stash push" in c]
        assert len(stash_cmds) == 1
        assert "-u" in stash_cmds[0]

    def test_stash_failure_still_proceeds(self, mod):
        """If stash fails, we still attempt checkout (best effort)."""
        def selective_mock(cmd, cwd=None):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "branch --show-current" in cmd_str:
                return "main"
            if "status --porcelain" in cmd_str:
                return " M dirty.txt"
            if "stash push" in cmd_str:
                return None  # stash fails
            if "stash list" in cmd_str:
                return None
            if "pr checkout" in cmd_str:
                return ""
            return None

        with patch.object(mod, "_run_cmd", side_effect=selective_mock):
            result = mod.setup_workspace(pr_number="42", gh_cmd="gh")

        assert result["was_dirty"] is True
        assert result["stash_ref"] is None  # stash failed
        assert result["checkout_ok"] is True  # checkout still attempted


# ---------------------------------------------------------------------------
# TestMain (CLI entrypoint)
# ---------------------------------------------------------------------------

class TestMain:
    """CLI entrypoint: --pr-number required, --gh-cmd optional."""

    def test_outputs_json_to_stdout(self, mod, capsys):
        """main() prints JSON to stdout."""
        mock = _make_run_cmd_mock({
            "branch --show-current": "main",
            "status --porcelain": "",
            "pr checkout": "",
            "get-url origin": "https://github.com/user/repo.git",
        })
        with patch.object(mod, "_run_cmd", side_effect=mock), \
             patch("sys.argv", ["setup-workspace.py", "--pr-number", "42"]):
            mod.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["checkout_ok"] is True
        assert output["original_branch"] == "main"

    def test_gh_cmd_override(self, mod, capsys):
        """--gh-cmd overrides auto-detection."""
        commands_seen = []

        def tracking_mock(cmd, cwd=None):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            commands_seen.append(cmd_str)
            if "branch --show-current" in cmd_str:
                return "main"
            if "status --porcelain" in cmd_str:
                return ""
            if "pr checkout" in cmd_str:
                return ""
            return None

        with patch.object(mod, "_run_cmd", side_effect=tracking_mock), \
             patch("sys.argv", ["setup-workspace.py", "--pr-number", "42", "--gh-cmd", "ghe"]):
            mod.main()

        checkout_cmds = [c for c in commands_seen if "pr checkout" in c]
        assert len(checkout_cmds) == 1
        assert "ghe" in checkout_cmds[0]

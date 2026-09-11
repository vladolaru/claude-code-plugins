"""Tests for review/workspace_setup.py.

Every test mocks one seam, ``_run`` — the subprocess boundary — with a
table of (returncode, stdout, stderr) responses keyed by a command
substring, so the helpers above it (``_run_cmd``, the checkout) are
exercised for real.
"""

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "review" / "workspace_setup.py"

TIMEOUT = "timeout"  # response marker: the command exceeded its timeout


def _load_module():
    spec = importlib.util.spec_from_file_location("setup_workspace", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def _run_mock(responses, seen=None, timeouts=None):
    """A ``_run`` stand-in answering from ``responses``.

    Values: a str is that stdout with exit 0; ``None`` is exit 1 with a
    generic stderr; a tuple is (returncode, stdout, stderr) verbatim;
    ``TIMEOUT`` raises the timeout the real seam raises. A command matching
    no key fails with exit 1. ``seen`` collects command strings and
    ``timeouts`` the timeout each call was given.
    """
    def run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if seen is not None:
            seen.append(cmd_str)
        if timeouts is not None:
            timeouts.append((cmd_str, timeout))
        for pattern, value in responses.items():
            if pattern in cmd_str:
                if value is TIMEOUT:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                if value is None:
                    return subprocess.CompletedProcess(cmd, 1, "", "failed\n")
                if isinstance(value, tuple):
                    return subprocess.CompletedProcess(cmd, *value)
                return subprocess.CompletedProcess(cmd, 0, value, "")
        return subprocess.CompletedProcess(cmd, 1, "", "no such command\n")
    return run


def _setup(mod, responses, pr_number="42", gh_cmd="gh", **collect):
    with patch.object(mod, "_run", side_effect=_run_mock(responses, **collect)):
        return mod.setup_workspace(pr_number=pr_number, gh_cmd=gh_cmd)


CLEAN = {"branch --show-current": "main", "status --porcelain": "", "pr checkout": ""}


class TestResolveGhCmd:
    """Detect gh vs ghe from git remote origin URL."""

    @pytest.mark.parametrize("origin, expected", [
        ("https://github.com/user/repo.git", "gh"),
        ("https://code.a8c.com/org/repo.git", "ghe"),
        ("https://git.automattic.com/org/repo.git", "ghe"),
        (None, "gh"),  # git remote fails → default
    ])
    def test_cli_follows_the_origin_host(self, mod, origin, expected):
        with patch.object(mod, "_run", side_effect=_run_mock({"get-url origin": origin})):
            assert mod.resolve_gh_cmd() == expected


class TestSetupWorkspace:
    """Core workspace setup logic."""

    def test_clean_workspace(self, mod):
        """Clean repo: no stash needed, records branch, checkout succeeds."""
        result = _setup(mod, CLEAN)
        assert result["original_branch"] == "main"
        assert result["was_dirty"] is False
        assert result["stash_ref"] is None
        assert result["checkout_ok"] is True
        assert "error" not in result

    def test_dirty_workspace(self, mod):
        """Dirty repo: stash created (with -u, for untracked files too),
        ref captured from git stash list."""
        seen = []
        result = _setup(mod, {
            "branch --show-current": "feature-branch",
            "status --porcelain": " M src/app.js\n?? new-file.txt",
            "stash push": "",
            "stash list": "stash@{0}: On feature-branch: pr-review-auto-stash",
            "pr checkout": "",
        }, pr_number="99", seen=seen)
        assert result["original_branch"] == "feature-branch"
        assert result["was_dirty"] is True
        assert result["stash_ref"] == "stash@{0}"
        assert result["checkout_ok"] is True
        assert "error" not in result
        stash_cmds = [c for c in seen if "stash push" in c]
        assert len(stash_cmds) == 1
        assert "-u" in stash_cmds[0]

    def test_checkout_failure_carries_gh_stderr_and_exit_status(self, mod):
        """WooCommerce PR #68063: the step-2 briefing said only "Failed to
        checkout PR #68063" because the helper dropped gh's stderr and exit
        status, so the orchestrator improvised a `git reset --hard`. The
        last stderr line and the exit status travel with the failure."""
        result = _setup(mod, {
            **CLEAN,
            "pr checkout": (1, "", "From https://github.com/o/r\nerror: Your local changes would be overwritten by merge\n"),
        })
        assert result["original_branch"] == "main"
        assert result["checkout_ok"] is False
        assert result["error"] == (
            "Failed to checkout PR #42: error: Your local changes would be "
            "overwritten by merge (exit 1)"
        )

    def test_checkout_failure_without_stderr_still_names_the_exit(self, mod):
        result = _setup(mod, {**CLEAN, "pr checkout": (128, "", "")})
        assert result["error"] == "Failed to checkout PR #42: exit 128"

    def test_checkout_timeout_is_reported_as_such(self, mod):
        result = _setup(mod, {**CLEAN, "pr checkout": TIMEOUT})
        assert result["checkout_ok"] is False
        assert result["error"] == (
            f"Failed to checkout PR #42: timed out after "
            f"{mod.CHECKOUT_TIMEOUT_SECONDS}s"
        )

    def test_checkout_gets_the_fetch_sized_timeout(self, mod):
        """A monorepo fetch can exceed the 30 s the other git calls get."""
        timeouts = []
        _setup(mod, CLEAN, timeouts=timeouts)
        by_cmd = {cmd: t for cmd, t in timeouts}
        assert by_cmd["gh pr checkout 42"] == mod.CHECKOUT_TIMEOUT_SECONDS
        assert by_cmd["gh pr checkout 42"] > by_cmd["git status --porcelain"]

    def test_uses_ghe_when_specified(self, mod):
        """When gh_cmd is ghe, checkout uses ghe."""
        seen = []
        result = _setup(mod, CLEAN, gh_cmd="ghe", seen=seen)
        assert result["checkout_ok"] is True
        assert [c for c in seen if "pr checkout" in c] == ["ghe pr checkout 42"]

    def test_branch_detection_failure(self, mod):
        """If branch detection fails, falls back to 'unknown'."""
        result = _setup(mod, {**CLEAN, "branch --show-current": None})
        assert result["original_branch"] == "unknown"
        assert result["checkout_ok"] is True

    def test_stash_failure_still_proceeds(self, mod):
        """If stash fails, we still attempt checkout (best effort)."""
        result = _setup(mod, {
            "branch --show-current": "main",
            "status --porcelain": " M dirty.txt",
            "stash push": None,
            "stash list": None,
            "pr checkout": "",
        })
        assert result["was_dirty"] is True
        assert result["stash_ref"] is None
        assert result["checkout_ok"] is True


class TestMain:
    """CLI entrypoint: --pr-number required, --gh-cmd optional."""

    def test_outputs_json_to_stdout(self, mod, capsys):
        """main() prints JSON to stdout."""
        responses = {**CLEAN, "get-url origin": "https://github.com/user/repo.git"}
        with patch.object(mod, "_run", side_effect=_run_mock(responses)), \
             patch("sys.argv", ["workspace_setup.py", "--pr-number", "42"]):
            mod.main()
        output = json.loads(capsys.readouterr().out)
        assert output["checkout_ok"] is True
        assert output["original_branch"] == "main"

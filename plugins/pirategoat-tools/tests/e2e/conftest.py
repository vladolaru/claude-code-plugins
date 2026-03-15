"""Shared fixtures for e2e pipeline tests."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Add the e2e directory to sys.path so test modules can import
# sibling modules (expectations, assertions, stream_monitor, etc.)
# using simple `from <module> import ...` syntax.
_E2E_DIR = str(Path(__file__).resolve().parent)
if _E2E_DIR not in sys.path:
    sys.path.insert(0, _E2E_DIR)

TEST_REPO = "vladolaru/pirategoat-pr-review-pipeline-test-repo"
TEST_REPO_URL = f"https://github.com/{TEST_REPO}.git"

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def _clone_test_repo(tmp_dir: str) -> str:
    """Clone the test repo into a temp directory. Returns the repo path.

    Fetches all branches so gather-review-context.py can resolve
    merge-bases and head refs without needing `gh pr checkout`.
    The test repo is small, so a full clone is fast.
    """
    repo_path = os.path.join(tmp_dir, "test-repo")
    result = subprocess.run(
        ["git", "clone", TEST_REPO_URL, repo_path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Clone failed: {result.stderr.strip()}")
    # Disable GPG signing in the clone.
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=repo_path,
        capture_output=True,
    )
    # Fetch all remote branches as local refs so git merge-base can
    # resolve branch names (e.g., feat/currency-conversion) directly.
    subprocess.run(
        ["git", "fetch", "--all"],
        cwd=repo_path,
        capture_output=True,
        timeout=30,
    )
    # Create local tracking branches for all remote branches.
    remote_refs = subprocess.run(
        ["git", "branch", "-r", "--format=%(refname:short)"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    for ref in remote_refs.stdout.strip().split("\n"):
        if ref and not ref.endswith("/HEAD"):
            local = ref.replace("origin/", "", 1)
            if local != "main":  # main already exists from clone
                subprocess.run(
                    ["git", "branch", "--track", local, ref],
                    cwd=repo_path,
                    capture_output=True,
                )
    return repo_path


@pytest.fixture(scope="session")
def test_repo(tmp_path_factory):
    """Session-scoped clone of the test repo.

    Cloned once per test session — all tests share the same clone.
    The clone is read-only from the tests' perspective (PRs are
    permanent fixtures on GitHub, never modified locally).
    """
    tmp_dir = str(tmp_path_factory.mktemp("e2e"))
    repo_path = _clone_test_repo(tmp_dir)
    yield repo_path
    # tmp_path_factory handles cleanup.


@pytest.fixture
def output_dir(tmp_path):
    """Per-test output directory for pipeline artifacts."""
    out = tmp_path / "output"
    out.mkdir()
    return str(out)


@pytest.fixture
def scripts_dir():
    """Path to the plugin's scripts directory."""
    return str(SCRIPTS_DIR)


@pytest.fixture
def plugin_root():
    """Path to the plugin root."""
    return str(PLUGIN_ROOT)

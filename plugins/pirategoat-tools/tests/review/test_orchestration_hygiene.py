"""Tests for the worktree-hygiene snapshot/compare/sweep in orchestration.

The reviewed repo is the user's LIVE working tree. The pipeline snapshots
git status at step 3 and, at step 11, sweeps only its own probe-marker
residue and reports everything else without blame. A missing baseline
reads 'unknown', never 'clean'.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review.orchestration import (
    PROBE_MARKER,
    _capture_worktree_baseline,
    _check_worktree_hygiene,
)


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """A throwaway git repo as CWD, with a separate output dir."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        cwd=repo, check=True,
    )
    monkeypatch.chdir(repo)
    out = tmp_path / "out"
    out.mkdir()
    return repo, out


class TestBaselineCapture:
    def test_baseline_written_with_entries(self, git_repo):
        repo, out = git_repo
        (repo / "wip.txt").write_text("uncommitted user work")
        _capture_worktree_baseline(str(out))
        data = json.loads((out / ".worktree-baseline.json").read_text())
        assert data["schema"] == 1
        assert any("wip.txt" in e for e in data["entries"])

    def test_clean_tree_writes_empty_entries(self, git_repo):
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        data = json.loads((out / ".worktree-baseline.json").read_text())
        assert data["entries"] == []

    def test_capture_failure_writes_nothing(self, git_repo, monkeypatch):
        repo, out = git_repo
        monkeypatch.chdir("/")  # not a git repo — git status exits nonzero
        _capture_worktree_baseline(str(out))
        assert not (out / ".worktree-baseline.json").exists()

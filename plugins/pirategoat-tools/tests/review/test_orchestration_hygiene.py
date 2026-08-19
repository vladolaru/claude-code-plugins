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
    _orchestrate_step_11,
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


class TestHygieneCheck:
    def test_clean_run_reports_clean(self, git_repo):
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        result = _check_worktree_hygiene(str(out))
        assert result["status"] == "clean"
        assert result["new_files"] == []
        data = json.loads((out / "worktree-hygiene.json").read_text())
        assert data["status"] == "clean"

    def test_probe_residue_swept_and_recorded(self, git_repo):
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        probe = repo / f"zz_{PROBE_MARKER}_test.go"
        probe.write_text("package main")
        result = _check_worktree_hygiene(str(out))
        assert not probe.exists()
        assert result["probe_residue_removed"] == [probe.name]
        assert result["status"] == "clean"

    def test_foreign_new_file_reported_never_deleted(self, git_repo):
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        user_file = repo / "user-wip.txt"
        user_file.write_text("the user's work")
        result = _check_worktree_hygiene(str(out))
        assert user_file.exists()  # NEVER auto-removed
        assert result["status"] == "changed_during_review"
        assert any("user-wip.txt" in e for e in result["new_files"])

    def test_preexisting_dirt_is_not_flagged(self, git_repo):
        repo, out = git_repo
        (repo / "pre-existing-wip.txt").write_text("dirty before review")
        _capture_worktree_baseline(str(out))
        result = _check_worktree_hygiene(str(out))
        assert result["status"] == "clean"

    def test_missing_baseline_reports_unknown(self, git_repo):
        repo, out = git_repo
        result = _check_worktree_hygiene(str(out))
        assert result["status"] == "unknown"

    def test_probe_in_subdirectory_swept(self, git_repo):
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        sub = repo / "pkg"
        sub.mkdir()
        probe = sub / f"zz_{PROBE_MARKER}_test.go"
        probe.write_text("package pkg")
        result = _check_worktree_hygiene(str(out))
        assert not probe.exists()
        assert result["status"] == "clean"

    def test_probe_in_new_untracked_directory_swept(self, git_repo):
        """A probe inside a directory that did not exist at baseline.

        Plain `git status --porcelain` collapses an untracked directory
        into a single "?? newpkg/" entry without recursing, which would
        hide the probe from a per-file sweep and leave the directory
        reported as a foreign change. The status command carries
        --untracked-files=all in both functions so the probe is visible
        as its own entry here.
        """
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        sub = repo / "newpkg"
        sub.mkdir()
        probe = sub / f"zz_{PROBE_MARKER}_test.go"
        probe.write_text("package newpkg")
        result = _check_worktree_hygiene(str(out))
        assert not probe.exists()
        assert result["probe_residue_removed"] == [f"newpkg/{probe.name}"]
        assert result["status"] == "clean"

    def test_tracked_marker_file_is_never_deleted(self, git_repo):
        """Only untracked marker files can be pipeline residue.

        Probes are created as NEW files and never committed, so a tracked
        path carrying the marker is somebody's versioned work — deleting
        it would destroy history-backed content on the strength of a name.
        """
        repo, out = git_repo
        tracked = repo / f"{PROBE_MARKER}-notes.md"
        tracked.write_text("committed notes about probes")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "notes"],
            cwd=repo, check=True,
        )
        _capture_worktree_baseline(str(out))
        tracked.write_text("edited during the review")
        result = _check_worktree_hygiene(str(out))
        assert tracked.exists()
        assert result["probe_residue_removed"] == []
        assert result["status"] == "changed_during_review"
        assert any("notes.md" in e for e in result["changed_files"])


def _seed_step_11(out):
    """The minimum finalize needs to reach `status: success` on its own.

    Every hygiene assertion below is about what step 11 adds to that
    baseline, so the seed has to leave `degradation_notes` empty.
    """
    (out / "review-verdict.json").write_text(json.dumps({"verdict": "APPROVE"}))
    (out / "review-report.md").write_text("# report")
    (out / "review-findings.json").write_text(json.dumps({
        "reviewer": "reconciliator",
        "verdict": "APPROVE",
        "summary": {"total_issues": 0, "by_severity": {}},
        "issues": [],
    }))
    (out / "decision-critic-verdict.json").write_text(
        json.dumps({"verdict": "STAND"})
    )


class TestStepElevenHygieneNotes:
    """Finalize is where the run reports what it left behind."""

    def _step_11(self, out):
        return _orchestrate_step_11("pr", {}, {}, {}, str(out))

    def test_seed_alone_finalizes_clean(self, git_repo):
        """Guards the harness: a note below must come from hygiene."""
        repo, out = git_repo
        _seed_step_11(out)
        _capture_worktree_baseline(str(out))
        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []
        assert result["status"] == "success"

    def test_foreign_change_is_reported_without_blame(self, git_repo):
        repo, out = git_repo
        _seed_step_11(out)
        _capture_worktree_baseline(str(out))
        user_file = repo / "user-wip.txt"
        user_file.write_text("the user's work")
        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())
        assert any("worktree changed during review" in n
                   for n in result["degradation_notes"])
        assert result["status"] == "degraded"
        assert user_file.exists()

    def test_probe_residue_alone_degrades_the_run(self, git_repo):
        """A sweep at finalize means a probe outlived its own command.

        Creating, running, and deleting a probe in one command is the
        protocol; needing this sweep is the evidence it was not followed,
        which is worth surfacing even though the tree ends up clean.
        """
        repo, out = git_repo
        _seed_step_11(out)
        _capture_worktree_baseline(str(out))
        probe = repo / f"zz_{PROBE_MARKER}_test.go"
        probe.write_text("package main")
        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())
        assert any("probe residue swept" in n
                   for n in result["degradation_notes"])
        assert result["status"] == "degraded"
        assert not probe.exists()

    def test_non_git_cwd_adds_no_hygiene_notes(self, git_repo, monkeypatch,
                                               tmp_path):
        """"unknown" is the normal reading for a non-repo or a pre-C1 run.

        Noting it would mark every such run degraded for the absence of a
        measurement that was never taken.
        """
        repo, out = git_repo
        _seed_step_11(out)
        plain = tmp_path / "plain"
        plain.mkdir()
        monkeypatch.chdir(plain)
        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []
        assert result["status"] == "success"
        hygiene = json.loads((out / "worktree-hygiene.json").read_text())
        assert hygiene["status"] == "unknown"

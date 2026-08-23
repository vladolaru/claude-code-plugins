"""Tests for the worktree-hygiene snapshot/compare/sweep in orchestration.

The reviewed repo is the user's LIVE working tree. The pipeline snapshots
git status at step 3 and, at step 11, sweeps only its own probe-marker
residue and reports everything else without blame. A missing baseline
reads 'unknown', never 'clean' — and, since the sweep is gated on a
baseline that names the repo it measured, an unverified run deletes
nothing at all.
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

from review import orchestration as orchestration_mod
from review.critic_adjustments import write_findings
from review.orchestration import (
    PROBE_MARKER,
    _capture_worktree_baseline,
    _check_worktree_hygiene,
    _orchestrate_step_11,
)


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """A throwaway git repo as CWD, with a separate output dir."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    out = tmp_path / "out"
    out.mkdir()
    return repo, out


def _init_repo(path):
    """A git repo with one commit at `path`."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "tracked.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        cwd=path, check=True,
    )
    return path


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

    def test_baseline_records_the_repo_it_measured(self, git_repo):
        """Identity, not just content: the sweep will check this later."""
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        data = json.loads((out / ".worktree-baseline.json").read_text())
        assert data["repo_root"] == os.path.realpath(str(repo))

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

    def test_clean_run_dates_its_baseline(self, git_repo):
        """The counts mean nothing without the window they cover."""
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        baseline = json.loads((out / ".worktree-baseline.json").read_text())
        result = _check_worktree_hygiene(str(out))
        assert result["baseline_captured_at"] == baseline["captured_at"]

    def test_unknown_run_dates_nothing(self, git_repo):
        repo, out = git_repo
        result = _check_worktree_hygiene(str(out))
        assert result["status"] == "unknown"
        assert result["baseline_captured_at"] is None

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

    def test_probe_with_git_quoted_path_swept(self, git_repo):
        """A probe whose porcelain line git C-quotes is still swept.

        Under core.quotePath (default true) any non-ASCII byte makes git
        print the path as a C-quoted string — '"zz-\\303\\244-..."' — so
        the printed text is not the filename. The sweep must decode it
        or the probe silently survives: the quoted string is not a path
        that exists on disk.
        """
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        probe = repo / f"zz-ä-{PROBE_MARKER}.txt"
        probe.write_text("probe")
        # Test validity guard: this scenario must actually produce a
        # quoted line, or the test stops exercising the decoder.
        lines = orchestration_mod._git_status_lines(str(repo))
        assert any(line.startswith('?? "') for line in lines)
        result = _check_worktree_hygiene(str(out))
        assert not probe.exists()
        assert len(result["probe_residue_removed"]) == 1
        recorded = result["probe_residue_removed"][0]
        assert PROBE_MARKER in recorded
        assert not recorded.startswith('"')
        assert result["status"] == "clean"

    def test_malformed_quoted_line_fails_closed(self, git_repo, monkeypatch):
        """A malformed C-quoted line is reported, never acted on.

        Real git output cannot produce one, but the sweep unlinks files,
        so the decode policy must fail closed: an undecodable path means
        no delete, and the raw line surfaces as an ordinary entry.
        """
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        bad = f'?? "zz-\\q-{PROBE_MARKER}.txt'
        monkeypatch.setattr(
            orchestration_mod, "_git_status_lines", lambda root: [bad]
        )
        result = _check_worktree_hygiene(str(out))
        assert result["probe_residue_removed"] == []
        assert bad in result["new_files"]
        assert result["status"] == "changed_during_review"

    def test_baseline_from_another_repo_never_sweeps(
        self, git_repo, tmp_path, monkeypatch
    ):
        """The delete is bound to the repo the baseline actually measured.

        A run directory reused across clones — or a process whose cwd moved
        — would otherwise let a baseline taken in one repo authorize
        deletions in another, and publish the result as `clean`.
        """
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        foreign = _init_repo(tmp_path / "foreign")
        victim = foreign / f"notes_{PROBE_MARKER}.md"
        victim.write_text("a marker-named file in a repo we never measured")
        monkeypatch.chdir(foreign)

        result = _check_worktree_hygiene(str(out))

        assert victim.exists(), "no delete outside the measured repo"
        assert result["probe_residue_removed"] == []
        assert result["status"] == "unknown", "an unverified pair is not clean"

    def test_baseline_without_repo_root_never_sweeps(self, git_repo):
        """A baseline that cannot prove its origin authorizes nothing."""
        repo, out = git_repo
        (out / ".worktree-baseline.json").write_text(
            json.dumps({"schema": 1, "entries": []})
        )
        probe = repo / f"zz_{PROBE_MARKER}_test.go"
        probe.write_text("package main")

        result = _check_worktree_hygiene(str(out))

        assert probe.exists()
        assert result["status"] == "unknown"

    def test_sweep_works_from_a_subdirectory_of_the_repo(
        self, git_repo, monkeypatch
    ):
        """Porcelain paths are repo-relative wherever git is invoked.

        Resolved against the cwd instead of the verified root, every path
        below a subdirectory cwd fails `isfile` and the sweep silently
        becomes a no-op that still reports `clean`.
        """
        repo, out = git_repo
        sub = repo / "pkg"
        sub.mkdir()
        (sub / "keep.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "sub"],
            cwd=repo, check=True,
        )
        monkeypatch.chdir(sub)
        _capture_worktree_baseline(str(out))
        probe = repo / f"zz_{PROBE_MARKER}_test.go"
        probe.write_text("package main")

        result = _check_worktree_hygiene(str(out))

        assert not probe.exists()
        assert result["probe_residue_removed"] == [probe.name]
        assert result["status"] == "clean"

    def test_marker_named_directory_does_not_condemn_its_contents(
        self, git_repo
    ):
        """The guard matches the basename, not the whole path.

        Matching the path would make one marker-named directory turn every
        ordinary file beneath it into residue.
        """
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        holder = repo / f"dir_{PROBE_MARKER}"
        holder.mkdir()
        bystander = holder / "user-notes.txt"
        bystander.write_text("the user's notes, inside a marker-named dir")
        probe = holder / f"zz_{PROBE_MARKER}_test.go"
        probe.write_text("package main")

        result = _check_worktree_hygiene(str(out))

        assert bystander.exists(), "a non-marker file is never residue"
        assert not probe.exists(), "the marker file beside it still goes"
        assert result["probe_residue_removed"] == [f"dir_{PROBE_MARKER}/{probe.name}"]
        assert any("user-notes.txt" in e for e in result["new_files"])
        assert result["status"] == "changed_during_review"

    def test_marker_named_symlink_to_directory_is_not_removed(self, git_repo):
        """`isfile` is what keeps the unlink to regular files.

        git lists a symlink as one entry; without the guard the pipeline
        would unlink a link the user owns on the strength of its name.
        """
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        target = repo / "real_dir"
        target.mkdir()
        (target / "user-work.txt").write_text("the user's work")
        link = repo / f"link_{PROBE_MARKER}"
        link.symlink_to(target, target_is_directory=True)

        result = _check_worktree_hygiene(str(out))

        assert link.is_symlink(), "a link to a directory is not residue"
        assert (target / "user-work.txt").exists()
        assert result["probe_residue_removed"] == []

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
    (out / "review-report.md").write_text("# report")
    # Complete enough for the Markdown renderer step 11 runs over it: a
    # stub would add a render-failure note and defeat the empty-notes
    # baseline every assertion below depends on. Written through the
    # sanctioned findings writer for the same reason — it stands in for
    # the reconciliator's own in-channel write, and a raw one would seed
    # the unstamped ledger finalize now reports as an out-of-channel
    # rewrite.
    write_findings(str(out), {
        "pr_id": "42",
        "reviewer": "reconciliator",
        "timestamp": "2026-08-13T10:00:00",
        "plugin_version": None,
        "schema": 1,
        "verdict": "APPROVE",
        "summary": {
            "total_issues": 0,
            "by_severity": {
                "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
            },
        },
        "issues": [],
        "unreviewed": None,
        "deferred_reviewed": [],
        "observations": None,
        "recommendations": None,
        "positive_observations": None,
        "clearances": None,
        "narrative_summary": None,
        "meta": {
            "files_reviewed": 1,
            "unreviewed_autofilled": None,
            "review_duration_ms": 10,
            "confidence_score": 0.9,
            "tool_results_used": None,
        },
    })
    (out / "decision-critic-verdict.json").write_text(
        json.dumps({"verdict": "STAND"})
    )


def _publish_step_11(out):
    """Prepare without a report, then publish the authored report."""
    report = out / "review-report.md"
    report_text = report.read_text() if report.is_file() else "# report"
    report.unlink(missing_ok=True)
    state = {}
    _orchestrate_step_11("pr", {}, state, {}, str(out))
    report.write_text(report_text)
    return _orchestrate_step_11("pr", {}, state, {}, str(out))


class TestStepElevenHygieneNotes:
    """Finalize is where the run reports what it left behind.

    Two channels, deliberately separate: `worktree_hygiene` on the pipeline
    result carries the measurement, and `status` degrades only for what the
    pipeline itself did wrong.
    """

    def _step_11(self, out):
        return _publish_step_11(out)

    def test_seed_alone_finalizes_clean(self, git_repo):
        """Guards the harness: a note below must come from hygiene."""
        repo, out = git_repo
        _seed_step_11(out)
        _capture_worktree_baseline(str(out))
        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []
        assert result["status"] == "success"
        baseline = json.loads((out / ".worktree-baseline.json").read_text())
        assert result["worktree_hygiene"] == {
            "status": "clean", "new_files": 0,
            "changed_files": 0, "probe_residue_removed": 0,
            "baseline_captured_at": baseline["captured_at"],
        }

    def test_foreign_change_is_measured_not_blamed(self, git_repo):
        """The requester editing their own tree is data, not a defect.

        `status` is a bot contract meaning the review pipeline
        underperformed; spending it on someone else's keystrokes would
        teach every consumer to ignore it.
        """
        repo, out = git_repo
        _seed_step_11(out)
        _capture_worktree_baseline(str(out))
        user_file = repo / "user-wip.txt"
        user_file.write_text("the user's work")
        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []
        assert result["status"] == "success"
        baseline = json.loads((out / ".worktree-baseline.json").read_text())
        assert result["worktree_hygiene"] == {
            "status": "changed_during_review", "new_files": 1,
            "changed_files": 0, "probe_residue_removed": 0,
            "baseline_captured_at": baseline["captured_at"],
        }
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
        baseline = json.loads((out / ".worktree-baseline.json").read_text())
        assert result["worktree_hygiene"] == {
            "status": "clean", "new_files": 0,
            "changed_files": 0, "probe_residue_removed": 1,
            "baseline_captured_at": baseline["captured_at"],
        }
        assert not probe.exists()

    def test_publish_pass_preserves_prepare_pass_probe_sweep(self, git_repo):
        """The sweep is intentionally mutating, so re-entry sees a clean
        tree; its first-pass evidence must still reach terminal publication."""
        repo, out = git_repo
        _seed_step_11(out)
        (out / "review-report.md").unlink()
        _capture_worktree_baseline(str(out))
        probe = repo / f"zz_{PROBE_MARKER}_test.go"
        probe.write_text("package main")
        state = {}

        _orchestrate_step_11("pr", {}, state, {}, str(out))

        assert state["publication_pending"] is True
        assert state["pipeline_status"] == "degraded"
        assert not probe.exists()
        assert not (out / "pipeline-result.json").exists()

        (out / "review-report.md").write_text("# report")
        _orchestrate_step_11("pr", {}, state, {}, str(out))

        result = json.loads((out / "pipeline-result.json").read_text())
        hygiene = json.loads((out / "worktree-hygiene.json").read_text())
        assert result["status"] == "degraded"
        assert result["worktree_hygiene"]["probe_residue_removed"] == 1
        assert hygiene["probe_residue_removed"] == [probe.name]
        assert any(
            "probe residue swept" in note
            for note in result["degradation_notes"]
        )

    def test_non_git_cwd_adds_no_hygiene_notes(self, git_repo, monkeypatch,
                                               tmp_path):
        """"unknown" is inert: nothing swept, nothing compared.

        The pipeline result carries `null` rather than a zeroed summary, so
        an unmeasured run can never be read as a measured-clean one.
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
        assert result["worktree_hygiene"] is None
        hygiene = json.loads((out / "worktree-hygiene.json").read_text())
        assert hygiene["status"] == "unknown"


class TestStepElevenUsageSnapshot:
    """Finalize also records what the run cost.

    The capture is a subprocess seam so `scripts/review/` never imports
    `scripts/analysis/`. Its failure mode is deliberately quiet: missing
    transcripts are normal on a Codex host and on every run older than this
    feature, so an absent snapshot reads as unmeasured and degrades nothing
    — the same reasoning that keeps hygiene "unknown" silent.
    """

    def _step_11(self, out):
        return _publish_step_11(out)

    def _usage_snapshot(self, subagents="complete", orchestrator="partial"):
        def usage(output):
            return {
                "input_tokens": 1,
                "cache_creation_input_tokens": 2,
                "cache_read_input_tokens": 3,
                "effective_input_tokens": 6,
                "output_tokens": output,
            }
        return {
            "schema": 1,
            "captured_at": "2026-08-19T10:43:00+00:00",
            "window": {"started_at": "2026-08-19T10:00:00+00:00",
                       "ended_at": "2026-08-19T10:43:00+00:00",
                       "closed": False},
            "availability": {"subagents": subagents,
                             "orchestrator": orchestrator},
            "reason": None,
            "agents_measured": {"measured": 14, "expected": 14},
            "subagent_usage": [],
            "subagent_totals": usage(200825),
            "usage_by_model": {"claude-opus-5[1m]": usage(99000),
                               "claude-sonnet-5": usage(101825)},
            "orchestrator_usage": usage(82725),
        }

    def _fake_capture(self, monkeypatch, payload):
        """Stand in for the CLI without spawning it."""
        def fake(cmd, cwd=None, timeout=60):
            if payload is not None:
                out_dir = Path(cmd[cmd.index("--output-dir") + 1])
                (out_dir / "usage-snapshot.json").write_text(payload)
            return "", payload is not None
        monkeypatch.setattr(orchestration_mod, "_run_subprocess", fake)

    def test_capture_records_an_absence_rather_than_nothing(self, git_repo):
        """No telemetry for this run: the artifact still lands, saying so."""
        repo, out = git_repo
        _seed_step_11(out)
        self._step_11(out)

        snapshot = json.loads((out / "usage-snapshot.json").read_text())
        result = json.loads((out / "pipeline-result.json").read_text())

        assert snapshot["availability"] == {
            "subagents": "missing", "orchestrator": "missing",
        }
        assert result["usage"]["availability"] == snapshot["availability"]
        assert result["usage"]["subagent_effective_input"] is None
        assert result["usage"]["subagent_output"] is None
        assert result["status"] == "success"
        assert result["degradation_notes"] == []

    def test_compact_block_mirrors_a_measured_snapshot(self, git_repo,
                                                       monkeypatch):
        repo, out = git_repo
        _seed_step_11(out)
        self._fake_capture(
            monkeypatch, json.dumps(self._usage_snapshot())
        )

        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())

        assert result["usage"] == {
            "subagent_effective_input": 6,
            "subagent_output": 200825,
            "by_model": {
                "claude-opus-5[1m]": {"eff_in": 6, "out": 99000},
                "claude-sonnet-5": {"eff_in": 6, "out": 101825},
            },
            "agents_measured": "14/14",
            "availability": {
                "subagents": "complete", "orchestrator": "partial",
            },
            "window_closed": False,
        }
        assert result["status"] == "success"
        assert result["degradation_notes"] == []

    def test_compact_block_carries_the_windows_own_state(self, git_repo,
                                                         monkeypatch):
        """"partial" has two stories and the bot has to tell them apart:
        a substituted bound (the run was still open at capture) versus
        damaged transcript evidence inside a window that really closed."""
        repo, out = git_repo
        _seed_step_11(out)
        payload = self._usage_snapshot()
        payload["window"]["closed"] = True
        self._fake_capture(monkeypatch, json.dumps(payload))

        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())

        assert result["usage"]["window_closed"] is True
        assert result["usage"]["availability"]["orchestrator"] == "partial"

    def test_failed_capture_reads_unmeasured_and_degrades_nothing(
        self, git_repo, monkeypatch
    ):
        """A Codex host and every pre-feature run land here. Spending
        `status` on a legacy-normal absence would teach consumers to ignore
        the one field that means the review underperformed."""
        repo, out = git_repo
        _seed_step_11(out)
        self._fake_capture(monkeypatch, None)

        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())

        assert not (out / "usage-snapshot.json").exists()
        assert result["usage"] is None
        assert result["status"] == "success"
        assert result["degradation_notes"] == []

    def test_unreadable_snapshot_reads_unmeasured(self, git_repo, monkeypatch):
        repo, out = git_repo
        _seed_step_11(out)
        self._fake_capture(monkeypatch, "[]")

        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())

        assert result["usage"] is None
        assert result["status"] == "success"

    def test_measured_missing_half_is_reported_not_zeroed(self, git_repo,
                                                          monkeypatch):
        """A snapshot whose subagent half is missing publishes no totals."""
        repo, out = git_repo
        _seed_step_11(out)
        payload = self._usage_snapshot(subagents="missing",
                                       orchestrator="missing")
        payload["subagent_totals"] = None
        payload["usage_by_model"] = None
        payload["agents_measured"] = {"measured": 0, "expected": None}
        self._fake_capture(monkeypatch, json.dumps(payload))

        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())

        assert result["usage"]["subagent_effective_input"] is None
        assert result["usage"]["by_model"] == {}
        assert result["usage"]["agents_measured"] == "0/?"

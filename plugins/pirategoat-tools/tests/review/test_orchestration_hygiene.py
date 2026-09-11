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
import types
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(TESTS_DIR))

from helpers.review_fixtures import canonical_findings_ledger

from review import critic_adjustments
from review import orchestration as orchestration_mod
from review import run_paths
from review.critic_adjustments import write_findings
from review.orchestration import (
    PROBE_MARKER,
    _capture_worktree_baseline,
    _check_worktree_hygiene,
    _orchestrate_step_11,
)


def _artifact(output_dir, key):
    path = run_paths.artifact_path(output_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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


class TestStepNineReconciliationVerification:
    def _read_ok(self, verified):
        return types.SimpleNamespace(
            status=critic_adjustments.FINDINGS_READ_OK,
            findings=canonical_findings_ledger(("high",) * verified),
        )

    @pytest.mark.parametrize(
        "reads,status", [(None, "unmeasured"), (0, "unverified"), (2, "verified")],
    )
    def test_read_quality_is_actor_specific(self, tmp_path, monkeypatch, reads, status):
        snapshot = {"schema": 1, "availability": {"subagents": "partial"}, "subagent_usage": [
            {"agent": "security-reviewer", "repository_reads": None, "tool_calls": 8},
            {"agent": "review-reconciliator", "repository_reads": reads, "tool_calls": 4},
        ]}
        monkeypatch.setattr(orchestration_mod, "_run_subprocess",
                            lambda cmd, cwd=None, timeout=60: (json.dumps(snapshot), True))
        result = orchestration_mod._reconciliation_verification(str(tmp_path), self._read_ok(1))
        assert result["status"] == status
        assert result["repository_reads"] == reads
        assert result["verified_concern_count"] == 1

    @pytest.mark.parametrize("stdout, ok", [
        ("", False), ("not json", True),
        (json.dumps({"schema": 1, "subagent_usage": []}), True),
        *[(json.dumps({"schema": 1, "subagent_usage": [
            {"agent": "review-reconciliator", "repository_reads": value}]}), True)
          for value in (None, True, -1, "2")],
    ])
    def test_no_measurement_is_unmeasured_never_unverified(self, tmp_path, monkeypatch, stdout, ok):
        monkeypatch.setattr(orchestration_mod, "_run_subprocess",
                            lambda cmd, cwd=None, timeout=60: (stdout, ok))
        result = orchestration_mod._reconciliation_verification(str(tmp_path), self._read_ok(1))
        assert result["status"] == "unmeasured"
        assert result["repository_reads"] is None

    def test_measures_through_stdout_mode(self, tmp_path, monkeypatch):
        seen = {}

        def fake(cmd, cwd=None, timeout=60):
            seen["cmd"] = cmd
            return json.dumps({"schema": 1, "subagent_usage": []}), True

        monkeypatch.setattr(orchestration_mod, "_run_subprocess", fake)
        orchestration_mod._reconciliation_verification(str(tmp_path), self._read_ok(1))
        assert "--stdout" in seen["cmd"]
        assert seen["cmd"][1].endswith("usage_snapshot.py")
        assert seen["cmd"][seen["cmd"].index("--output-dir") + 1] == str(tmp_path)

    def test_an_unusable_ledger_has_no_verified_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess",
            lambda cmd, cwd=None, timeout=60: (json.dumps({"schema": 1, "subagent_usage": [
                {"agent": "review-reconciliator", "repository_reads": 2}]}), True),
        )
        read = types.SimpleNamespace(status=critic_adjustments.FINDINGS_READ_ABSENT, findings=None)
        result = orchestration_mod._reconciliation_verification(str(tmp_path), read)
        assert result["verified_concern_count"] is None
        assert result["status"] == "verified"


class TestBaselineCapture:
    @pytest.mark.parametrize("dirty", [True, False], ids=["dirty", "clean"])
    def test_baseline_captures_entries_and_repo_identity(self, git_repo, dirty):
        """Identity, not just content: the sweep will check `repo_root` later."""
        repo, out = git_repo
        if dirty:
            (repo / "wip.txt").write_text("uncommitted user work")
        _capture_worktree_baseline(str(out))
        data = json.loads((_artifact(out, "worktree_baseline")).read_text())
        assert data["schema"] == 1
        if dirty:
            assert any("wip.txt" in e for e in data["entries"])
        else:
            assert data["entries"] == []
        assert data["repo_root"] == os.path.realpath(str(repo))

    def test_capture_failure_writes_nothing(self, git_repo, monkeypatch):
        repo, out = git_repo
        monkeypatch.chdir("/")  # not a git repo — git status exits nonzero
        _capture_worktree_baseline(str(out))
        assert not (_artifact(out, "worktree_baseline")).exists()


class TestHygieneCheck:
    def test_clean_run_dates_its_baseline(self, git_repo):
        """The counts mean nothing without the window they cover."""
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        baseline = json.loads((_artifact(out, "worktree_baseline")).read_text())
        result = _check_worktree_hygiene(str(out))
        assert result["status"] == "clean"
        assert result["new_files"] == []
        assert result["baseline_captured_at"] == baseline["captured_at"]
        data = json.loads((_artifact(out, "worktree_hygiene")).read_text())
        assert data["status"] == "clean"

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

    def test_malformed_prior_probe_paths_are_not_inherited(self, git_repo):
        repo, out = git_repo
        _capture_worktree_baseline(str(out))
        (_artifact(out, "worktree_hygiene")).write_text(json.dumps({
            "schema": 1,
            "probe_residue_removed": ["../foreign", "/absolute/probe"],
        }))

        result = _check_worktree_hygiene(str(out))

        assert result["probe_residue_removed"] == []
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
        (_artifact(out, "worktree_baseline")).write_text(
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
    write_findings(str(out), canonical_findings_ledger())
    critic_adjustments.write_critic_verdict(
        str(out), "STAND", critic_adjustments.empty_proposal()
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

    @pytest.fixture(autouse=True)
    def _no_usage_snapshot_spawn(self, monkeypatch, orchestration_mod):
        monkeypatch.setattr(orchestration_mod, "_run_subprocess", lambda *a, **k: ("", False))

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
        baseline = json.loads((_artifact(out, "worktree_baseline")).read_text())
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
        baseline = json.loads((_artifact(out, "worktree_baseline")).read_text())
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
        baseline = json.loads((_artifact(out, "worktree_baseline")).read_text())
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
        hygiene = json.loads((_artifact(out, "worktree_hygiene")).read_text())
        assert result["status"] == "degraded"
        assert result["worktree_hygiene"]["probe_residue_removed"] == 1
        assert hygiene["probe_residue_removed"] == [probe.name]
        assert any(
            "probe residue swept" in note
            for note in result["degradation_notes"]
        )

    def test_new_probe_after_prepare_requires_report_rewrite(self, git_repo):
        """The report is bound to every distinct probe swept by the run."""
        repo, out = git_repo
        _seed_step_11(out)
        (out / "review-report.md").unlink()
        _capture_worktree_baseline(str(out))
        probe_a = repo / f"a_{PROBE_MARKER}_test.go"
        probe_b = repo / f"b_{PROBE_MARKER}_test.go"
        probe_a.write_text("package main")
        state = {}

        _orchestrate_step_11("pr", {}, state, {}, str(out))

        assert state["publication_pending"] is True
        assert state["degradation_notes"] == [
            "probe residue swept at finalize: 1 file(s) — a probe should "
            "be deleted in the same command that created it"
        ]

        (out / "review-report.md").write_text("# report for probe A")
        probe_b.write_text("package main")
        _orchestrate_step_11("pr", {}, state, {}, str(out))

        assert state["report_handoff_status"] == "source_changed"
        assert state["publication_pending"] is True
        assert not (out / "pipeline-result.json").exists()
        assert state["degradation_notes"] == [
            "probe residue swept at finalize: 2 file(s) — a probe should "
            "be deleted in the same command that created it"
        ]
        probe_records = [
            record for record in state["step_11_degradation_records"]
            if record["code"] == "probe_residue_swept"
        ]
        assert len(probe_records) == 1

        _orchestrate_step_11("pr", {}, state, {}, str(out))

        assert state["report_handoff_status"] == "stale_report_unchanged"
        assert not (out / "pipeline-result.json").exists()

        (out / "review-report.md").write_text("# report for probes A and B")
        _orchestrate_step_11("pr", {}, state, {}, str(out))

        result = json.loads((out / "pipeline-result.json").read_text())
        assert state["report_handoff_status"] == "published"
        assert result["worktree_hygiene"]["probe_residue_removed"] == 2
        assert result["degradation_notes"] == [
            "probe residue swept at finalize: 2 file(s) — a probe should "
            "be deleted in the same command that created it"
        ]

    def test_probe_provenance_is_order_independent_and_deduplicated(self):
        first = orchestration_mod._probe_residue_provenance([
            "z/probe.go", "a/probe.go", "z/probe.go",
        ])
        reordered = orchestration_mod._probe_residue_provenance([
            "a/probe.go", "z/probe.go",
        ])

        assert first == reordered
        assert first["count"] == 2
        assert first["discriminator"].startswith("paths-sha256:")

    def test_probe_provenance_stably_hashes_surrogateescaped_paths(self):
        surrogate_path = f"z/{PROBE_MARKER}_\udcff.go"

        first = orchestration_mod._probe_residue_provenance([
            surrogate_path, "a/probe.go", surrogate_path,
        ])
        reordered = orchestration_mod._probe_residue_provenance([
            "a/probe.go", surrogate_path,
        ])

        assert first == reordered
        assert first["count"] == 2

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
        hygiene = json.loads((_artifact(out, "worktree_hygiene")).read_text())
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
                (_artifact(out_dir, "usage_snapshot")).write_text(payload)
            return "", payload is not None
        monkeypatch.setattr(orchestration_mod, "_run_subprocess", fake)

    def test_capture_records_an_absence_rather_than_nothing(self, git_repo):
        """No telemetry for this run: the artifact still lands, saying so."""
        repo, out = git_repo
        _seed_step_11(out)
        self._step_11(out)

        snapshot = json.loads((_artifact(out, "usage_snapshot")).read_text())
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
        """"partial" has two stories and the bot has to tell them apart:
        a substituted bound (the run was still open at capture) versus
        damaged transcript evidence inside a window that really closed —
        this snapshot is captured with the window already closed."""
        repo, out = git_repo
        _seed_step_11(out)
        payload = self._usage_snapshot()
        payload["window"]["closed"] = True
        self._fake_capture(monkeypatch, json.dumps(payload))

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
            "window_closed": True,
        }
        assert result["status"] == "success"
        assert result["degradation_notes"] == []

    @pytest.mark.parametrize(
        "payload", [None, "[]"], ids=["capture-fails", "unreadable-snapshot"],
    )
    def test_unmeasured_usage_degrades_nothing(
        self, git_repo, monkeypatch, payload
    ):
        """A Codex host and every pre-feature run land here. Spending
        `status` on a legacy-normal absence would teach consumers to ignore
        the one field that means the review underperformed."""
        repo, out = git_repo
        _seed_step_11(out)
        self._fake_capture(monkeypatch, payload)

        self._step_11(out)
        result = json.loads((out / "pipeline-result.json").read_text())

        if payload is None:
            assert not (_artifact(out, "usage_snapshot")).exists()
        assert result["usage"] is None
        assert result["status"] == "success"
        assert result["degradation_notes"] == []

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


class TestCriticProsePaths:
    def _write(self, tmp_path, text):
        path = run_paths.artifact_path(str(tmp_path), "critic_findings")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    @pytest.mark.parametrize("text, changed, expected", [
        pytest.param(
            "Claims Verified\n- **f1** — Evidence: src/admin.php:42 echoes it\n"
            "Coverage: the review reached includes/class-wc-cart.php and "
            "src/retry.php; wp-includes/formatting.php was consulted.\n",
            ["src/admin.php", "src/retry.php"],
            ["includes/class-wc-cart.php", "wp-includes/formatting.php"],
            id="paths-the-diff-does-not-contain",
        ),
        pytest.param(
            "Coverage: includes/class-wc-order.php was read; class-wc-order.php/other.php was not.\n",
            ["plugins/woocommerce/includes/class-wc-order.php"],
            ["class-wc-order.php/other.php"],
            id="abbreviated-paths-name-their-changed-file",
        ),
        pytest.param(
            "Run ./bin/lint.sh from ../tools/run.py; 3/4.5 of the cases pass; src/real.php was not read.\n",
            [], ["src/real.php"], id="relative-prefixes-and-ratios-are-not-paths",
        ),
        pytest.param("Only src/a.py and src/b.py are named.", ["src/a.py", "src/b.py"], [], id="no-foreign-paths"),
        pytest.param(
            "See README.md and https://example.com/docs/page.html and v1.2.3.", [], [],
            id="bare-filenames-and-urls-are-not-paths",
        ),
        pytest.param(
            "Sentence final: includes/foreign.php.\n"
            "Multi-dot: src/admin.test.php was checked.\n"
            "Scoped: packages/@scope/lib/index.ts was checked.\n"
            "Code: `lib/render.test.js` was checked.\n"
            "Line: src/controller.test.php:42 echoes it.\n",
            [],
            ["includes/foreign.php", "lib/render.test.js", "packages/@scope/lib/index.ts",
             "src/admin.test.php", "src/controller.test.php"],
            id="complete-paths-in-ordinary-prose",
        ),
        pytest.param(
            "See [docs](https://example.com/docs/page.html)—`includes/foreign.php`.", [],
            ["includes/foreign.php"], id="url-does-not-swallow-an-adjacent-code-span",
        ),
        pytest.param("See https://example.com/?next=src/file.php and lib/local.php.", [], ["lib/local.php"],
                     id="path-in-url-query-value"),
        pytest.param("See https://example.com/docs#target=src/file.php.", [], [], id="path-in-url-fragment"),
    ])
    def test_names_paths_the_diff_does_not_contain(self, tmp_path, text, changed, expected):
        self._write(tmp_path, text)
        assert orchestration_mod._critic_prose_paths_outside_diff(str(tmp_path), changed) == expected

    def test_absent_findings_file_is_none(self, tmp_path):
        assert orchestration_mod._critic_prose_paths_outside_diff(str(tmp_path), ["a.py"]) is None

    def test_step_11_stores_the_measurement_in_state(self, git_repo):
        repo, out = git_repo
        _seed_step_11(out)
        self._write(
            out,
            "The review reached src/a.py and includes/foreign.php was consulted.",
        )
        state = {}

        _orchestrate_step_11(
            "pr",
            {},
            state,
            {"git": {"changed_files_csv": "src/a.py"}},
            str(out),
        )

        assert state["critic_prose_paths_outside_diff"] == [
            "includes/foreign.php"
        ]


class TestPlanOverrideOrphans:
    def test_reads_the_orphans_every_override_skip_recorded(self, orchestration_mod):
        plan = {"agents": [
            {"name": "docs-drift-reviewer", "status": "SKIPPED_OVERRIDE", "orphaned_files": ["changelog/x"]},
            {"name": "a11y-reviewer", "status": "SKIPPED_OVERRIDE", "orphaned_files": ["changelog/x", "assets/a.css"]},
            {"name": "code-reviewer", "status": "DISPATCH"},
        ], "changed_files": ["changelog/x", "assets/a.css", "src/a.php"]}
        assert orchestration_mod._plan_override_orphans(plan) == {
            "assets/a.css": ["a11y-reviewer"],
            "changelog/x": ["a11y-reviewer", "docs-drift-reviewer"],
        }

    def test_no_plan_is_unmeasured(self, orchestration_mod, tmp_path):
        assert orchestration_mod._load_plan_or_none(str(tmp_path)) is None
        assert orchestration_mod._plan_override_orphans(None) is None

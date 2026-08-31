"""run_paths — location, allocation, retention, and internal layout of review run dirs."""

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from review import run_paths


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review" / "run_paths.py"


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PIRATEGOAT_TOOLS_HOME", raising=False)
    return tmp_path


class TestStateRoot:
    def test_defaults_to_dot_pirategoat_tools_under_home(self, home):
        assert run_paths.state_root() == home / ".pirategoat-tools"

    def test_honours_an_absolute_override(self, home, monkeypatch, tmp_path):
        override = tmp_path / "elsewhere"
        monkeypatch.setenv("PIRATEGOAT_TOOLS_HOME", str(override))
        assert run_paths.state_root() == override

    def test_ignores_a_relative_override(self, home, monkeypatch):
        monkeypatch.setenv("PIRATEGOAT_TOOLS_HOME", "relative/path")
        assert run_paths.state_root() == home / ".pirategoat-tools"


class TestSafeSegment:
    def test_keeps_a_readable_prefix_and_stable_digest(self):
        segment = run_paths.safe_segment("feat/x y")

        assert segment.startswith("feat-x-y--")
        digest = segment.rsplit("--", 1)[1]
        assert len(digest) == 12
        assert set(digest) <= set("0123456789abcdef")

    def test_same_identity_is_deterministic(self):
        assert run_paths.safe_segment("a.B_c-1") == run_paths.safe_segment(
            "a.B_c-1"
        )

    def test_sanitization_collisions_keep_distinct_digests(self):
        assert run_paths.safe_segment("feature/foo") != run_paths.safe_segment(
            "feature-foo"
        )

    @pytest.mark.parametrize("bad", ["", ".", "..", "..."])
    def test_rejects_empty_and_dot_only_identities(self, bad):
        with pytest.raises(ValueError, match="empty or dot-only"):
            run_paths.safe_segment(bad)


class TestTargetDir:
    def test_builds_kind_repo_target_hierarchy(self, home):
        d = run_paths.target_dir("pr", "/Users/x/work/repo", "123")
        base = home / ".pirategoat-tools" / "reviews" / "pr"
        assert d.parent.parent == base
        assert d.parent.name.startswith("Users-x-work-repo--")
        assert d.name.startswith("123--")

    def test_target_sanitization_collisions_resolve_to_distinct_paths(self, home):
        slash = run_paths.target_dir("branch", "/repo", "feature/foo")
        hyphen = run_paths.target_dir("branch", "/repo", "feature-foo")

        assert slash != hyphen

    def test_repo_sanitization_collisions_resolve_to_distinct_paths(self, home):
        nested = run_paths.target_dir("branch", "/one/two-three", "main")
        flattened = run_paths.target_dir("branch", "/one-two/three", "main")

        assert nested != flattened

    @pytest.mark.parametrize("bad", ["", ".", "..", "..."])
    def test_rejects_dot_only_repo_or_target_components(self, home, bad):
        with pytest.raises(ValueError, match="empty or dot-only"):
            run_paths.target_dir("branch", bad, "main")
        with pytest.raises(ValueError, match="empty or dot-only"):
            run_paths.target_dir("branch", "/repo", bad)

    def test_rejects_a_resolved_target_outside_the_kind_directory(
        self, home, tmp_path
    ):
        repo_root = str(tmp_path / "repo")
        base = home / ".pirategoat-tools" / "reviews" / "branch"
        repo_dir = base / run_paths.safe_segment(str(Path(repo_root).resolve()))
        repo_dir.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (repo_dir / run_paths.safe_segment("main")).symlink_to(
            outside, target_is_directory=True
        )

        with pytest.raises(ValueError, match="escapes review state root"):
            run_paths.target_dir("branch", repo_root, "main")

    def test_rejects_an_unknown_kind(self, home):
        with pytest.raises(ValueError):
            run_paths.target_dir("nope", "/r", "t")


class TestAllocateRunDir:
    def test_creates_run_dir_with_id_and_the_four_subdirs(self, home):
        target = run_paths.target_dir("branch", "/r", "feat-x")
        run = run_paths.allocate_run_dir(target)
        assert run.parent == target / "runs"
        assert run_paths.RUN_ID_RE.match(run.name)
        for sub in ("pipeline", "reviewers", "synthesis", "tmp"):
            assert (run / sub).is_dir()

    def test_latest_run_dir_is_the_newest_allocation(self, home):
        target = run_paths.target_dir("branch", "/r", "feat-x")
        run = run_paths.allocate_run_dir(target)
        assert run_paths.latest_run_dir(target) == run

    def test_two_allocations_in_one_second_get_distinct_ids(self, home):
        target = run_paths.target_dir("branch", "/r", "feat-x")
        a = run_paths.allocate_run_dir(target)
        b = run_paths.allocate_run_dir(target)
        assert a != b

    def test_suffix_exhaustion_keeps_run_id_within_four_hex_digits(self, home, monkeypatch):
        timestamps = iter((datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
                           datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc)))

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz):
                return next(timestamps)

        monkeypatch.setattr(run_paths, "datetime", FrozenDateTime)
        target = run_paths.target_dir("branch", "/r", "feat-x")
        runs = target / "runs"
        runs.mkdir(parents=True)
        exhausted = runs / "20260102T030405Z-ffff"
        exhausted.mkdir()

        run = run_paths.allocate_run_dir(target)

        assert run != exhausted
        assert run_paths.RUN_ID_RE.match(run.name)

    def test_retries_when_the_selected_run_id_collides(
        self, home, monkeypatch
    ):
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz):
                return datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

        target = run_paths.target_dir("branch", "/r", "feat-x")
        real_mkdir = Path.mkdir
        collided = []

        def collide_once(path, *args, **kwargs):
            if run_paths.RUN_ID_RE.match(path.name) and not collided:
                real_mkdir(path, *args, **kwargs)
                collided.append(path)
                raise FileExistsError(path)
            return real_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(run_paths, "datetime", FrozenDateTime)
        monkeypatch.setattr(run_paths.secrets, "token_hex", lambda _size: "1000")
        monkeypatch.setattr(Path, "mkdir", collide_once)

        run = run_paths.allocate_run_dir(target)

        assert collided[0].name == "20260102T030405Z-1000"
        assert run.name == "20260102T030405Z-1001"

    def test_failed_initialization_removes_the_partial_run_and_releases_lock(
        self, home, monkeypatch
    ):
        target = run_paths.target_dir("branch", "/r", "feat-x")
        real_mkdir = Path.mkdir
        failed = False

        def fail_synthesis_once(path, *args, **kwargs):
            nonlocal failed
            if path.name == "synthesis" and not failed:
                failed = True
                raise OSError("injected initialization failure")
            return real_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", fail_synthesis_once)
        with pytest.raises(OSError, match="injected initialization failure"):
            run_paths.allocate_run_dir(target)

        runs_dir = target / "runs"
        assert list(runs_dir.iterdir()) == []

        monkeypatch.setattr(Path, "mkdir", real_mkdir)
        assert run_paths.allocate_run_dir(target).is_dir()

    def test_live_allocator_lock_times_out_and_an_unlocked_file_is_harmless(
        self, home, monkeypatch
    ):
        target = run_paths.target_dir("branch", "/r", "feat-x")
        target.mkdir(parents=True)
        lock_path = target / ".run-allocation.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(
            run_paths, "_ALLOCATION_LOCK_TIMEOUT_SECONDS", 0.05, raising=False
        )
        started = time.monotonic()
        try:
            with pytest.raises(TimeoutError, match="allocator lock"):
                run_paths.allocate_run_dir(target)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

        assert time.monotonic() - started < 1
        assert not (target / "runs").exists()
        assert run_paths.allocate_run_dir(target).is_dir()

    def test_concurrent_processes_preserve_allocation_order_and_retention(
        self, home, tmp_path
    ):
        target = run_paths.target_dir("branch", "/r", "feat-x")
        (target / "runs").mkdir(parents=True)
        gate = tmp_path / "start"
        ready_dir = tmp_path / "ready"
        ready_dir.mkdir()
        scripts_dir = SCRIPT.parents[1]
        worker = """
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from review import run_paths

target = Path(sys.argv[2])
gate = Path(sys.argv[3])
ready = Path(sys.argv[4])
suffix = sys.argv[5]
delay = float(sys.argv[6])

class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz):
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

run_paths.datetime = FrozenDateTime
run_paths.secrets.token_hex = lambda _size: suffix
real_iterdir = Path.iterdir
slowed = False

def slow_first_scan(path):
    global slowed
    entries = list(real_iterdir(path))
    if path == target / "runs" and not slowed:
        slowed = True
        time.sleep(delay)
    return iter(entries)

Path.iterdir = slow_first_scan
ready.touch()
deadline = time.monotonic() + 5
while not gate.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("start gate")
    time.sleep(0.005)
print(run_paths.allocate_run_dir(target))
"""
        suffixes = [
            "f000", "e000", "d000", "c000", "b000", "a000",
            "9000", "8000", "7000", "6000", "5000", "4000",
        ]
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    worker,
                    str(scripts_dir),
                    str(target),
                    str(gate),
                    str(ready_dir / str(index)),
                    suffix,
                    str(0.1 + index * 0.01),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for index, suffix in enumerate(suffixes)
        ]
        deadline = time.monotonic() + 5
        while len(list(ready_dir.iterdir())) != len(processes):
            if time.monotonic() >= deadline:
                pytest.fail("concurrent allocators did not reach the start gate")
            time.sleep(0.005)
        gate.touch()
        results = [process.communicate(timeout=15) for process in processes]

        assert [
            (process.returncode, stderr)
            for process, (_stdout, stderr) in zip(processes, results)
        ] == [(0, "")] * len(processes)
        survivors = sorted(
            path for path in (target / "runs").iterdir()
            if path.is_dir() and run_paths.RUN_ID_RE.match(path.name)
        )
        creation_order = sorted(survivors, key=lambda path: path.stat().st_mtime_ns)
        assert len(survivors) == run_paths.KEEP_RUNS
        assert creation_order == survivors
        assert run_paths.latest_run_dir(target) == survivors[-1]


class TestPruneRuns:
    def test_keeps_the_newest_ten(self, home):
        target = run_paths.target_dir("branch", "/r", "feat-x")
        runs = [run_paths.allocate_run_dir(target) for _ in range(12)]
        survivors = sorted((target / "runs").iterdir())
        assert len(survivors) == 10
        assert runs[0] not in survivors and runs[1] not in survivors
        assert run_paths.latest_run_dir(target) == runs[-1]


class TestInternalLayout:
    def test_reviewer_dir_nests_under_reviewers(self, tmp_path):
        assert run_paths.reviewer_dir(tmp_path, "a11y") == tmp_path / "reviewers" / "a11y"

    @pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "a\x00b"])
    def test_reviewer_dir_rejects_unsafe_identities(self, tmp_path, bad):
        with pytest.raises(ValueError):
            run_paths.reviewer_dir(tmp_path, bad)

    def test_artifact_path_resolves_each_group(self, tmp_path):
        assert run_paths.artifact_path(tmp_path, "run_config") == tmp_path / "run-config.json"
        assert run_paths.artifact_path(tmp_path, "pipeline_state") == tmp_path / "pipeline" / "pipeline-state.json"
        assert run_paths.artifact_path(tmp_path, "reconciliation_context") == tmp_path / "synthesis" / "reconciliation-context.json"

    def test_artifact_registry_is_the_exact_run_contract(self):
        assert run_paths.ARTIFACTS == {
            "run_config": ("", "run-config.json"),
            "review_context": ("", "review-context.json"),
            "pipeline_result": ("", "pipeline-result.json"),
            "review_report": ("", "review-report.md"),
            "review_record": ("", "review-record.md"),
            "review_findings_json": ("", "review-findings.json"),
            "review_findings_md": ("", "review-findings.md"),
            "pipeline_state": ("pipeline", "pipeline-state.json"),
            "review_intake": ("pipeline", "review-intake.json"),
            "dispatch_plan": ("pipeline", "dispatch-plan.json"),
            "dispatch_plan_initial": ("pipeline", "dispatch-plan.initial.json"),
            "change_purpose": ("pipeline", "change-purpose.md"),
            "dependency_refresh": ("pipeline", "dependency-refresh.json"),
            "synthesis_agents": ("pipeline", "synthesis-agents.json"),
            "usage_snapshot": ("pipeline", "usage-snapshot.json"),
            "worktree_hygiene": ("pipeline", "worktree-hygiene.json"),
            "telemetry_log_path": ("pipeline", ".telemetry-log-path"),
            "worktree_baseline": ("pipeline", ".worktree-baseline.json"),
            "reconciliation_context": ("synthesis", "reconciliation-context.json"),
            "critic_adjustments": ("synthesis", "decision-critic-adjustments.json"),
            "critic_findings": ("synthesis", "decision-critic-findings.md"),
            "critic_verdict": ("synthesis", "decision-critic-verdict.json"),
        }

    def test_artifact_path_rejects_unknown_keys(self, tmp_path):
        with pytest.raises(KeyError):
            run_paths.artifact_path(tmp_path, "nope")

    def test_synthesis_started_marker_and_scratch(self, tmp_path):
        assert run_paths.synthesis_started_marker(tmp_path, "decision-reviewer") == tmp_path / "synthesis" / "decision-reviewer.synthesis-started"
        assert run_paths.scratch_dir(tmp_path) == tmp_path / "tmp"


class TestArtifactLiteralAuthority:
    def test_run_artifact_literals_exist_only_in_path_authorities(self):
        scripts = Path(__file__).resolve().parents[2] / "scripts"
        allowed = {
            scripts / "review" / "run_paths.py",
            scripts / "review" / "reviewer_lifecycle.py",
        }
        literals = {
            filename for _subdir, filename in run_paths.ARTIFACTS.values()
        } | {"scope-summary", "scoped-diff.patch", ".synthesis-started"}
        violations = []
        for root in (scripts / "review", scripts / "analysis"):
            for path in root.rglob("*.py"):
                if path in allowed:
                    continue
                text = path.read_text(encoding="utf-8")
                for literal in sorted(literals):
                    if literal in text:
                        violations.append(f"{path.relative_to(scripts)}: {literal}")
        assert violations == []


class TestCli:
    def _run(self, home, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(home), "PIRATEGOAT_TOOLS_HOME": ""},
        )

    def test_allocate_prints_an_existing_run_dir_and_seeds_run_config(self, home):
        result = self._run(home, "allocate", "--kind", "pr", "--repo-root", "/r", "--target", "42")
        assert result.returncode == 0, result.stderr
        run_dir = Path(result.stdout.strip())
        assert run_dir.is_dir()
        config = json.loads((run_dir / "run-config.json").read_text())
        assert config == {"target_dir": str(run_dir.parent.parent)}

    def test_latest_finds_the_allocated_run(self, home):
        first = self._run(home, "allocate", "--kind", "pr", "--repo-root", "/r", "--target", "42")
        latest = self._run(home, "latest", "--kind", "pr", "--repo-root", "/r", "--target", "42")
        assert latest.returncode == 0
        assert Path(latest.stdout.strip()).resolve() == Path(first.stdout.strip()).resolve()

    @pytest.mark.parametrize("target", ["", ".", "..", "..."])
    def test_allocate_rejects_dot_only_direct_cli_targets(self, home, target):
        result = self._run(
            home,
            "allocate",
            "--kind",
            "branch",
            "--repo-root",
            "/r",
            "--target",
            target,
        )

        assert result.returncode != 0
        assert not (home / ".pirategoat-tools" / "reviews").exists()

    def test_latest_on_an_empty_target_exits_1_with_no_runs(self, home):
        result = self._run(home, "latest", "--kind", "pr", "--repo-root", "/r", "--target", "99")
        assert result.returncode == 1
        assert "NO_RUNS" in result.stderr

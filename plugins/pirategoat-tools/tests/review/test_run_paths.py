"""run_paths — location, allocation, retention, and internal layout of review run dirs."""

import json
import os
import re
import subprocess
import sys
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
    def test_slashes_and_forbidden_chars_become_hyphens(self):
        assert run_paths.safe_segment("feat/x y") == "feat-x-y"

    def test_allowed_chars_pass_through(self):
        assert run_paths.safe_segment("a.B_c-1") == "a.B_c-1"


class TestTargetDir:
    def test_builds_kind_repo_target_hierarchy(self, home):
        d = run_paths.target_dir("pr", "/Users/x/work/repo", "123")
        assert d == home / ".pirategoat-tools" / "reviews" / "pr" / "Users-x-work-repo" / "123"

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

        class FrozenDateTime:
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

    def test_pipeline_synthesis_and_scratch_helpers(self, tmp_path):
        assert run_paths.pipeline_path(tmp_path, "pipeline-state.json") == tmp_path / "pipeline" / "pipeline-state.json"
        assert run_paths.synthesis_path(tmp_path, "reconciliation-context.json") == tmp_path / "synthesis" / "reconciliation-context.json"
        assert run_paths.scratch_dir(tmp_path) == tmp_path / "tmp"


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

    def test_latest_on_an_empty_target_exits_1_with_no_runs(self, home):
        result = self._run(home, "latest", "--kind", "pr", "--repo-root", "/r", "--target", "99")
        assert result.returncode == 1
        assert "NO_RUNS" in result.stderr

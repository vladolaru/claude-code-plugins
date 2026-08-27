"""Tests for review/telemetry.py — JSONL telemetry for PR review pipelines."""

import glob
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent

# Import the module under test
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "review" / "telemetry.py"

sys.path.insert(0, str(TESTS_DIR))
from helpers.context_fixtures import COMPLETE_CONTEXT
from helpers.review_fixtures import (
    canonical_findings_ledger,
    canonical_review_document,
)
from review import dependency_refresh
from review import synthesis_lifecycle as lifecycle_contract
from review.reconciliation_context import aggregate_file_review
from review.reviewer_lifecycle import ReviewPaths


def _load_module():
    spec = importlib.util.spec_from_file_location("review_telemetry", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


@pytest.fixture
def output_dir(tmp_path):
    """Simulate a PR review output directory."""
    od = tmp_path / "pr-review-org-repo-42"
    od.mkdir()
    return od


@pytest.fixture
def telemetry(mod, output_dir, tmp_path):
    """ReviewTelemetry with a test log_dir."""
    log_dir = tmp_path / "logs"
    return mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))


def _read_events(log_path):
    """Read all JSONL events from a log file."""
    events = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _read_manifest(telemetry):
    """Read the materialized manifest for a telemetry run."""
    return json.loads(Path(telemetry.manifest_path).read_text())


def _write_dispatch_plan(output_dir, agent_names):
    """Name the agents whose finals the run is entitled to project."""
    (output_dir / "dispatch-plan.json").write_text(json.dumps({
        "agents": [
            {"name": name, "status": "DISPATCH"} for name in agent_names
        ],
    }))


def _write_coverage_inputs(output_dir, changed, reviewable, agents):
    """Write the two authoritative path sets used by coverage measurement."""
    (output_dir / "review-context.json").write_text(json.dumps({
        "git": {"changed_files": changed},
    }))
    (output_dir / "dispatch-plan.json").write_text(json.dumps({
        "changed_files": reviewable,
        "agents": agents,
    }))


# ── start() ─────────────────────────────────────────────────────────


class TestStart:
    """ReviewTelemetry.start() creates log infrastructure."""

    def test_default_log_dir_is_pirategoat_tools(self, mod):
        assert "/.pirategoat-tools/" in mod.LOG_DIR
        assert mod.LOG_DIR.endswith("/logs/reviews")

    def test_creates_log_with_pipeline_start_event(self, telemetry):
        """start() creates a JSONL log file with a pipeline_start event."""
        path = telemetry.start(pr_number="42")
        assert os.path.isfile(path)
        assert path.endswith(".jsonl")
        assert telemetry.log_path == path
        events = _read_events(path)
        assert len(events) == 1
        assert events[0]["event"] == "pipeline_start"
        assert events[0]["step"] == 0
        # Timestamp is UTC-aware
        ts = datetime.fromisoformat(events[0]["timestamp"])
        assert ts.tzinfo is not None

    def test_pipeline_start_has_pipeline_info(self, telemetry, output_dir):
        path = telemetry.start(pr_number="42", total_steps=15, bot_mode=False)
        events = _read_events(path)
        pipeline = events[0]["pipeline"]
        assert pipeline["pr_number"] == "42"
        assert pipeline["output_dir"] == str(output_dir)
        assert pipeline["total_steps"] == 15
        assert pipeline["bot_mode"] is False

    def test_writes_marker_file(self, telemetry, output_dir):
        path = telemetry.start(pr_number="42")
        marker = output_dir / ".telemetry-log-path"
        assert marker.is_file()
        assert marker.read_text().strip() == path

    def test_start_records_versioned_run_identity(self, telemetry, mod):
        path = telemetry.start(
            pr_number="42",
            run_id="run-1",
            session_id="session-123",
            plugin_version="1.108.0",
            mode="pr",
            repo_path="/repo",
            git_range="abc..def",
            base_sha="abc",
            head_sha="def",
        )

        start = _read_events(path)[0]
        assert start["schema"] == mod.EVENT_SCHEMA
        assert "schema_version" not in start
        assert start["run_id"] == "run-1"
        assert start["pipeline"]["session_id"] == "session-123"
        assert start["pipeline"]["plugin_version"] == "1.108.0"
        assert start["pipeline"]["mode"] == "pr"
        assert start["pipeline"]["repo_path"] == "/repo"
        assert start["pipeline"]["git"] == {
            "requested_range": "abc..def",
            "base_sha": "abc",
            "head_sha": "def",
        }

    def test_every_event_inherits_schema_and_run_id(self, telemetry, mod, output_dir, tmp_path):
        telemetry.start(run_id="run-1")
        later_process = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )
        later_process.log_agent_start(agent_name="security-reviewer")

        identities = {
            (event["schema"], event["run_id"])
            for event in _read_events(telemetry.log_path)
        }
        assert identities == {(mod.EVENT_SCHEMA, "run-1")}


# ── path_to_slug() ─────────────────────────────────────────────────


class TestPathToSlug:
    """path_to_slug converts absolute paths to filename-safe slugs."""

    def test_absolute_path(self, mod):
        assert mod.ReviewTelemetry.path_to_slug("/Users/vladolaru/Work/a8c/woocommerce-payments") == \
            "Users-vladolaru-Work-a8c-woocommerce-payments"

    def test_strips_leading_separator(self, mod):
        slug = mod.ReviewTelemetry.path_to_slug("/foo/bar")
        assert not slug.startswith("-")

    def test_preserves_dots_and_underscores(self, mod):
        assert mod.ReviewTelemetry.path_to_slug("/my_project/.duplicates/repo") == \
            "my_project-.duplicates-repo"

    def test_collapses_consecutive_separators(self, mod):
        slug = mod.ReviewTelemetry.path_to_slug("/a///b//c")
        assert "--" not in slug


# ── Structured filename ────────────────────────────────────────────


class TestPrefixCapping:
    """Oversized prefixes are capped so every derived filename fits the
    common 255-byte component limit — an ENAMETOOLONG at allocation would
    be swallowed by the fail-open pipeline into a run with no telemetry."""

    def test_long_branch_name_still_allocates_telemetry(self, mod, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        t = mod.ReviewTelemetry(str(out), log_dir=str(tmp_path / "logs"))
        path = t.start(
            mode="full",
            repo_path="/ci/worktrees/" + "deep/" * 30 + "repo",
            identifier="feature/" + "x" * 300,
        )
        assert os.path.isfile(path)
        assert len(os.path.basename(path).encode("utf-8")) <= 255
        manifest_path = t.manifest_path
        assert os.path.isfile(manifest_path)
        assert len(os.path.basename(manifest_path).encode("utf-8")) <= 255

    def test_capping_is_deterministic_and_groups_run_numbers(
        self, mod, tmp_path
    ):
        out = tmp_path / "output"
        out.mkdir()
        long_prefix = "full-" + "a" * 300
        capped = mod.ReviewTelemetry._cap_prefix(long_prefix)
        assert capped == mod.ReviewTelemetry._cap_prefix(long_prefix)
        assert len(capped.encode("utf-8")) <= mod.ReviewTelemetry._PREFIX_MAX_BYTES

    def test_distinct_long_prefixes_stay_distinct(self, mod):
        base = "full-" + "a" * 300
        assert mod.ReviewTelemetry._cap_prefix(base + "-one") != (
            mod.ReviewTelemetry._cap_prefix(base + "-two")
        )

    def test_short_prefixes_are_untouched(self, mod):
        assert mod.ReviewTelemetry._cap_prefix("pr-repo-42") == "pr-repo-42"

    def test_capping_is_byte_safe_for_non_ascii_fallback(self, mod):
        """The legacy fallback prefix is not ASCII-sanitized — truncation
        must never split a multibyte character."""
        capped = mod.ReviewTelemetry._cap_prefix("plăți-" + "ă" * 300)
        assert len(capped.encode("utf-8")) <= mod.ReviewTelemetry._PREFIX_MAX_BYTES
        capped.encode("utf-8").decode("utf-8")  # round-trips cleanly


class TestStructuredFilename:
    """Telemetry log filenames use structured <mode>-<repo_slug>-<identifier>-run<N> format."""

    def test_pr_mode_filename(self, mod, tmp_path):
        """PR reviews use mode-repo_slug-pr_number-runN."""
        out = tmp_path / "output"
        out.mkdir()
        t = mod.ReviewTelemetry(str(out), log_dir=str(tmp_path / "logs"))
        path = t.start(pr_number="42", mode="pr",
                       repo_path="/Users/vlad/Work/a8c/woocommerce-payments",
                       identifier="42")
        filename = os.path.basename(path)
        assert filename.startswith("pr-Users-vlad-Work-a8c-woocommerce-payments-42-run1--")
        assert filename.endswith(".jsonl")

    def test_full_mode_with_branch(self, mod, tmp_path):
        """Full reviews use mode-repo_slug-branch_slug-runN."""
        out = tmp_path / "output"
        out.mkdir()
        t = mod.ReviewTelemetry(str(out), log_dir=str(tmp_path / "logs"))
        path = t.start(mode="full",
                       repo_path="/Users/vlad/Work/a8c/ciab-admin",
                       identifier="fix/WOOPLUG-123-some-bug")
        filename = os.path.basename(path)
        assert filename.startswith("full-Users-vlad-Work-a8c-ciab-admin-fix-WOOPLUG-123-some-bug-run1--")

    def test_incremental_mode(self, mod, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        t = mod.ReviewTelemetry(str(out), log_dir=str(tmp_path / "logs"))
        path = t.start(mode="incremental",
                       repo_path="/Users/vlad/Work/a8c/ciab-admin",
                       identifier="feat/add-settings")
        filename = os.path.basename(path)
        assert filename.startswith("incremental-Users-vlad-Work-a8c-ciab-admin-feat-add-settings-run1--")

    def test_run_number_increments(self, mod, tmp_path):
        """Subsequent runs of the same review get incrementing run numbers."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        out1 = tmp_path / "output1"
        out1.mkdir()
        t1 = mod.ReviewTelemetry(str(out1), log_dir=str(log_dir))
        path1 = t1.start(mode="pr", repo_path="/repo", identifier="99")
        assert "-run1--" in os.path.basename(path1)

        out2 = tmp_path / "output2"
        out2.mkdir()
        t2 = mod.ReviewTelemetry(str(out2), log_dir=str(log_dir))
        path2 = t2.start(mode="pr", repo_path="/repo", identifier="99")
        assert "-run2--" in os.path.basename(path2)

    def test_same_run_number_and_timestamp_allocate_distinct_logs(self, mod, tmp_path):
        """Concurrent starts never share a JSONL file or durable run identity."""
        log_dir = tmp_path / "logs"
        out1 = tmp_path / "output1"
        out2 = tmp_path / "output2"
        out1.mkdir()
        out2.mkdir()
        fixed_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        with (
            patch.object(mod.ReviewTelemetry, "_next_run_number", return_value=1),
            patch.object(mod, "datetime", FrozenDatetime),
        ):
            first = mod.ReviewTelemetry(str(out1), log_dir=str(log_dir))
            second = mod.ReviewTelemetry(str(out2), log_dir=str(log_dir))
            first_path = first.start(
                mode="pr", repo_path="/repo", identifier="42", run_id="run-a"
            )
            second_path = second.start(
                mode="pr", repo_path="/repo", identifier="42", run_id="run-b"
            )

        assert first_path != second_path
        assert [(event["event"], event["run_id"]) for event in _read_events(first_path)] == [
            ("pipeline_start", "run-a")
        ]
        assert [(event["event"], event["run_id"]) for event in _read_events(second_path)] == [
            ("pipeline_start", "run-b")
        ]

        later_first = mod.ReviewTelemetry(str(out1), log_dir=str(log_dir))
        later_second = mod.ReviewTelemetry(str(out2), log_dir=str(log_dir))
        later_first.log_agent_start(agent_name="security-reviewer")
        later_second.log_agent_start(agent_name="performance-reviewer")

        assert {event["run_id"] for event in _read_events(first_path)} == {"run-a"}
        assert {event["run_id"] for event in _read_events(second_path)} == {"run-b"}

    def test_missing_identifier_falls_back_to_branch(self, mod, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        t = mod.ReviewTelemetry(str(out), log_dir=str(tmp_path / "logs"))
        path = t.start(mode="full", repo_path="/repo", identifier="")
        filename = os.path.basename(path)
        assert filename.startswith("full-repo-branch-run1--")

    def test_fallback_without_structured_params(self, mod, tmp_path):
        """Legacy callers that don't pass mode/repo_path get output_dir basename."""
        out = tmp_path / "branch-review-some-repo"
        out.mkdir()
        t = mod.ReviewTelemetry(str(out), log_dir=str(tmp_path / "logs"))
        path = t.start(pr_number="42")
        filename = os.path.basename(path)
        assert filename.startswith("branch-review-some-repo-run1--")


# ── log_step() ──────────────────────────────────────────────────────


class TestLogStep:
    """ReviewTelemetry.log_step() appends step events."""

    def test_appends_step_event(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_step(step=1, phase="SETUP", title="Repo Setup")
        events = _read_events(telemetry.log_path)
        assert len(events) == 2
        assert events[1]["event"] == "step"
        assert events[1]["step"] == 1

    def test_includes_phase_and_title(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_step(step=3, phase="AWARENESS", title="PR Review State")
        events = _read_events(telemetry.log_path)
        assert events[1]["phase"] == "AWARENESS"
        assert events[1]["title"] == "PR Review State"

    def test_calculates_duration_since_prev(self, telemetry):
        """Duration is calculated from previous event's timestamp."""
        telemetry.start(pr_number="42")
        # Small sleep to ensure measurable duration
        time.sleep(0.05)
        telemetry.log_step(step=1, phase="SETUP", title="Repo Setup")
        events = _read_events(telemetry.log_path)
        duration = events[1]["duration_since_prev_ms"]
        assert duration is not None
        assert duration >= 40  # At least ~50ms minus some tolerance

    def test_noop_without_start(self, mod, output_dir, tmp_path):
        """log_step is a no-op if start() was never called."""
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        # Should not raise
        t.log_step(step=1, phase="SETUP", title="Repo Setup")
        # No log file created
        assert t.log_path is None

    def test_multiple_steps_accumulate(self, telemetry):
        telemetry.start(pr_number="42")
        for i in range(1, 5):
            telemetry.log_step(step=i, phase="TEST", title=f"Step {i}")
        events = _read_events(telemetry.log_path)
        assert len(events) == 5  # 1 start + 4 steps
        assert [e["step"] for e in events] == [0, 1, 2, 3, 4]

    def test_reads_marker_across_instances(self, mod, output_dir, tmp_path):
        """A new ReviewTelemetry instance can find the log via marker file."""
        log_dir = tmp_path / "logs"
        t1 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        path = t1.start(pr_number="42")

        # New instance (simulates separate process invocation)
        t2 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t2.log_step(step=1, phase="SETUP", title="Repo Setup")

        events = _read_events(path)
        assert len(events) == 2


# ── finalize() ──────────────────────────────────────────────────────


class TestFinalize:
    """ReviewTelemetry.finalize() writes pipeline_end with summary."""

    def test_writes_pipeline_end_event(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(telemetry.log_path)
        assert events[-1]["event"] == "pipeline_end"

    def test_includes_total_duration(self, telemetry):
        telemetry.start(pr_number="42")
        time.sleep(0.05)
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(telemetry.log_path)
        summary = events[-1]["summary"]
        assert "total_duration_ms" in summary
        assert summary["total_duration_ms"] >= 40

    def test_includes_summary_dict(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(telemetry.log_path)
        assert "summary" in events[-1]
        assert isinstance(events[-1]["summary"], dict)

    def test_noop_without_start(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        assert t.log_path is None

    def test_summary_includes_context_fields(self, mod, output_dir, tmp_path):
        """Summary extracts PR size category from review-context.json."""
        log_dir = tmp_path / "logs"
        (output_dir / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        summary = events[-1]["summary"]
        assert summary.get("pr_size_category") == "small"
        assert summary.get("changed_files_count") == 2
        assert summary.get("commit_count") == 3


# ── Unmeasured fields ────────────────────────────────────────


class TestNoFabricatedMeasurements:
    """Zero ≠ unknown: an event may not carry a value nobody measured.

    ``thoughts_length`` used to default to 0 on both writers while no
    caller ever passed it, so every step and pipeline_end event of every
    run reported a measured zero for a measurement that never happened.
    The field was removed at three sites, and each needs its own guard,
    because a guard that only reads freshly written events is satisfied
    by the writer's silence alone: with nothing emitting the key, the
    projection allowlists can be reverted and such a test still passes.
    So the writer guards use fresh events, and the allowlist guard feeds
    a pre-change event shape through the projection — which is the live
    mechanism for old logs, since a manifest is rebuilt from the whole
    JSONL on every refresh. Old logs stay readable either way; the key
    they carry is dropped in projection rather than honored.
    """

    def test_step_and_pipeline_end_events_omit_thoughts_length(
        self, telemetry
    ):
        telemetry.start(pr_number="42")
        telemetry.log_step(step=1, phase="SETUP", title="Repo Setup",
                           bot_mode=True)
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results",
                           bot_mode=True)

        events = _read_events(telemetry.log_path)
        written = [e for e in events if e["event"] in ("step", "pipeline_end")]
        assert [e["event"] for e in written] == ["step", "pipeline_end"]
        for event in written:
            assert event["args"] == {"bot_mode": True}
            assert "thoughts_length" not in json.dumps(event)

    def test_manifest_projection_drops_thoughts_length_from_old_events(
        self, mod, telemetry
    ):
        """The allowlist drops the key, not the writer's silence.

        Written as a pre-change producer would have: the event goes
        straight into the JSONL, so restoring the allowlist entry fails
        this test. A same-run log_step() call could not, since the
        current writer emits no key for the allowlist to select.
        """
        telemetry.start(run_id="run-1")
        old_event = {
            "schema": mod.EVENT_SCHEMA,
            "run_id": "run-1",
            "event": "step",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "step": 10,
            "phase": "VALIDATION",
            "title": "Decision Critic",
            "duration_since_prev_ms": 12,
            "args": {"bot_mode": True, "thoughts_length": 321},
            "decisions": {"critic_skipped": True},
        }
        with open(telemetry.log_path, "a") as f:
            f.write(json.dumps(old_event) + "\n")

        # Any refresh reprojects every step event in the log, old included.
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")

        steps = _read_manifest(telemetry)["steps"]
        assert [step["step"] for step in steps] == [10]
        assert steps[0]["args"] == {"bot_mode": True}
        # Neighbours in the same allowlist must survive the drop.
        assert steps[0]["decisions"] == {"critic_skipped": True}
        assert "thoughts_length" not in json.dumps(steps[0])

    def test_log_step_and_finalize_reject_thoughts_length(self, telemetry):
        """The parameter is gone from the signatures, not merely unused."""
        telemetry.start(pr_number="42")
        with pytest.raises(TypeError):
            telemetry.log_step(step=1, phase="SETUP", title="Repo Setup",
                               thoughts_length=321)
        with pytest.raises(TypeError):
            telemetry.finalize(step=15, phase="OUTPUT", title="Present",
                               thoughts_length=321)


# ── Run manifest ───────────────────────────────────────────────


class TestRunManifest:
    """A fail-open sidecar materializes the current run state."""

    def test_start_materializes_running_manifest(self, telemetry, mod):
        log_path = telemetry.start(
            run_id="run-1",
            session_id="session-1",
            plugin_version="1.108.0",
            mode="pr",
            repo_path="/repo",
        )

        assert telemetry.manifest_path == str(
            Path(log_path).with_suffix(".manifest.json")
        )
        manifest = _read_manifest(telemetry)
        assert manifest["schema"] == mod.EVENT_SCHEMA
        assert "schema_version" not in manifest
        assert manifest["status"] == "running"
        assert manifest["run"]["id"] == "run-1"
        assert manifest["run"]["session_id"] == "session-1"
        assert manifest["run"]["plugin_version"] == "1.108.0"
        assert manifest["run"]["mode"] == "pr"
        assert manifest["run"]["repo_path"] == "/repo"
        assert manifest["run"]["started_at"] is not None
        assert manifest["run"]["ended_at"] is None
        assert manifest["availability"] == {
            "pipeline": True,
            "transcript": False,
            "assignment": False,
            "worktree_hygiene": False,
            "synthesis_agents": False,
            "usage": False,
            "skipped_steps": False,
            "dependency_refresh": False,
            "reviewer_markdown": False,
            "findings_markdown": False,
        }
        assert manifest["assignment"] is None

    def test_log_step_refreshes_running_manifest(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_step(step=3, phase="AWARENESS", title="Gather Context")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "running"
        assert manifest["steps"][-1]["step"] == 3
        assert manifest["steps"][-1]["phase"] == "AWARENESS"

    def test_log_step_opens_no_review_artifact(
        self, telemetry, output_dir, monkeypatch
    ):
        """A running manifest is cheap. Only finalize pays for the heavy sections.

        `_build_manifest` ran on every `log_step`, and it re-opened
        `review-findings.json` and every `<reviewer>-review.json` each
        time — roughly 17 opens per file for a 15-step run, for sections
        no consumer reads until the run settles.
        """
        telemetry.start(run_id="run-1")
        (output_dir / "code-review.json").write_text(
            json.dumps(canonical_review_document("code", ["medium"]))
        )
        (output_dir / "review-findings.json").write_text(
            json.dumps(canonical_findings_ledger(["medium"]))
        )

        opened = []
        real_open = open

        def spy(path, *args, **kwargs):
            opened.append(os.fspath(path))
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", spy)
        telemetry.log_step(step=9, phase="SYNTHESIS", title="Review Record")

        assert not [
            path for path in opened
            if path.endswith("-review.json")
            or path.endswith("review-findings.json")
        ]

    def test_a_running_manifest_declares_the_heavy_sections_unavailable(
        self, telemetry, output_dir
    ):
        telemetry.start(run_id="run-1")
        _write_coverage_inputs(
            output_dir,
            ["src/a.py"],
            ["src/a.py"],
            [{"name": "code-reviewer", "status": "DISPATCH"}],
        )
        (output_dir / "review-findings.json").write_text(
            json.dumps(canonical_findings_ledger(["medium"]))
        )

        telemetry.log_step(step=9, phase="SYNTHESIS", title="Review Record")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "running"
        for section in ("assignment", "synthesis_agents", "usage"):
            assert manifest[section] is None
            assert manifest["availability"][section] is False
        assert manifest["outcome"]["reconciliation"] is None

    def test_finalize_builds_the_heavy_sections_and_reads_once(
        self, mod, telemetry, output_dir, monkeypatch
    ):
        telemetry.start(run_id="run-1")
        _write_coverage_inputs(
            output_dir,
            ["src/a.py"],
            ["src/a.py"],
            [{"name": "code-reviewer", "status": "DISPATCH"}],
        )
        (output_dir / "review-findings.json").write_text(
            json.dumps(canonical_findings_ledger(["medium"]))
        )

        reads = []
        real = mod.read_findings_file
        monkeypatch.setattr(
            mod, "read_findings_file",
            lambda path: reads.append(path) or real(path),
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "complete"
        assert manifest["assignment"] is not None
        assert len(reads) == 1

    def test_the_manifest_section_is_named_for_what_it_holds(
        self, mod, telemetry, output_dir
    ):
        """`assignment`, not `coverage` — the section IS the assignment.

        Its six fields are the assignment vocabulary Plan A settled on, and
        the sidecar they describe is `<reviewer>-assignment.json`.
        """
        telemetry.start(run_id="run-1")
        _write_coverage_inputs(
            output_dir,
            ["src/a.py"],
            ["src/a.py"],
            [{"name": "code-reviewer", "status": "DISPATCH"}],
        )

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert "coverage" not in manifest
        assert "coverage" not in manifest["availability"]
        assert set(manifest["assignment"]) >= set(
            mod.manifest_sections.ASSIGNMENT_FIELDS
        )
        assert manifest["availability"]["assignment"] is True

    def test_the_assignment_vocabulary_has_one_owner(self, mod):
        with pytest.raises(ImportError):
            import review.assignment_vocabulary  # noqa: F401
        assert mod.manifest_sections.ASSIGNMENT_FIELDS

    def test_log_step_manifest_allowlists_lifecycle_and_decision_fields(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_step(
            step=10,
            phase="VALIDATION",
            title="Decision Critic",
            bot_mode=True,
            decisions={
                "critic_skipped": True,
                "reason": "SENSITIVE_DECISION_PROSE",
                "prompt": "SENSITIVE_PROMPT",
                "tool_result": {"body": "SENSITIVE_RESULT"},
            },
        )

        step = _read_manifest(telemetry)["steps"][-1]
        assert step["event"] == "step"
        assert step["step"] == 10
        assert step["phase"] == "VALIDATION"
        assert step["title"] == "Decision Critic"
        assert step["args"] == {"bot_mode": True}
        assert step["decisions"] == {"critic_skipped": True}
        serialized = json.dumps(step)
        assert "SENSITIVE_DECISION_PROSE" not in serialized
        assert "SENSITIVE_PROMPT" not in serialized
        assert "SENSITIVE_RESULT" not in serialized
        assert "reason" not in step["decisions"]
        assert "prompt" not in step["decisions"]
        assert "tool_result" not in step["decisions"]

    def test_finalize_materializes_complete_sanitized_outcome(
        self, telemetry, output_dir
    ):
        (output_dir / "pipeline-result.json").write_text(json.dumps({
            "status": "degraded",
            "verdict": "COMMENT",
            "critic_verdict": "REVISE",
            "review_body": "PIPELINE_RESULT_SECRET",
            "degradation_notes": ["TOOL_RESULT_SECRET"],
        }))
        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "complete"
        assert manifest["run"]["ended_at"] is not None
        assert manifest["outcome"]["summary"]["total_duration_ms"] is not None
        assert manifest["outcome"]["pipeline_status"] == "degraded"
        assert manifest["outcome"]["verdict"] == "COMMENT"
        assert manifest["outcome"]["critic_verdict"] == "REVISE"
        serialized = json.dumps(manifest)
        assert "PIPELINE_RESULT_SECRET" not in serialized
        assert "TOOL_RESULT_SECRET" not in serialized

    def test_manifest_counts_event_parse_gaps(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="security-reviewer")
        with open(telemetry.log_path, "a") as log:
            log.write("not json\n")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert _read_manifest(telemetry)["event_parse_gaps"] == 1

    def test_clean_manifest_omits_event_parse_gaps(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="security-reviewer")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert "event_parse_gaps" not in _read_manifest(telemetry)

    def test_manifest_counts_invalid_utf8_event_parse_gap(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="security-reviewer")
        with open(telemetry.log_path, "ab") as log:
            log.write(b"\xff\n")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["event_parse_gaps"] == 1
        assert manifest["status"] == "complete"
        assert manifest["run"]["id"] == "run-1"
        assert manifest["agents"]["started"][0]["agent"] == (
            "security-reviewer"
        )

    def test_read_first_event_rejects_invalid_utf8_without_scanning_forward(
        self, telemetry
    ):
        log_path = Path(telemetry.output_dir) / "invalid-first.jsonl"
        later_event = json.dumps({"event": "pipeline_start"}).encode("utf-8")
        log_path.write_bytes(b"\xff\n" + later_event + b"\n")
        telemetry._log_path = str(log_path)

        assert telemetry._read_first_event() is None

    def test_finalize_records_agent_lifecycle_and_incomplete_names(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            agent_name="security-reviewer", domain="security"
        )
        telemetry.log_agent_start(
            agent_name="performance-reviewer", domain="performance"
        )
        telemetry.log_agent_complete(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert [event["agent"] for event in agents["started"]] == [
            "security-reviewer",
            "performance-reviewer",
        ]
        assert [event["agent"] for event in agents["completed"]] == [
            "security-reviewer"
        ]
        assert agents["incomplete"] == ["performance-reviewer"]
        assert "failed" not in agents

    def test_finalize_preserves_unmatched_retry_execution(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert _read_manifest(telemetry)["agents"]["incomplete"] == [
            "code-reviewer"
        ]

    def test_start_after_completion_creates_a_retry_execution(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest="b" * 64,
            verdict="comment",
            finding_count=1, severities={"medium": 1},
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert len(agents["started"]) == 2
        assert [event["verdict"] for event in agents["completed"]] == [
            "approve",
            "comment",
        ]
        assert agents["incomplete"] == []

    def test_overlapping_executions_each_keep_their_completion(
        self, telemetry
    ):
        """Two starts before either completes: both completions match
        outstanding starts — never a false incomplete execution."""
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest="b" * 64,
            verdict="comment",
            finding_count=1, severities={"medium": 1},
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert len(agents["started"]) == 2
        assert [event["verdict"] for event in agents["completed"]] == [
            "approve",
            "comment",
        ]
        assert agents["incomplete"] == []

    def test_completion_without_start_remains_visible_for_strict_validation(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert agents["started"] == []
        assert [event["agent"] for event in agents["completed"]] == [
            "code-reviewer"
        ]

    def test_agent_events_do_not_refresh_running_manifest(self, telemetry):
        telemetry.start(run_id="run-1")
        manifest_path = Path(telemetry.manifest_path)
        initial_manifest = manifest_path.read_bytes()

        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )

        assert manifest_path.read_bytes() == initial_manifest
        assert [event["event"] for event in _read_events(telemetry.log_path)] == [
            "pipeline_start",
            "agent_start",
            "agent_complete",
        ]

    @pytest.mark.parametrize(
        "completion_order",
        [("a-reviewer", "b-reviewer"), ("b-reviewer", "a-reviewer")],
        ids=["a-then-b", "b-then-a"],
    )
    def test_finalize_sorts_unmatched_execution_multiset(
        self, telemetry, completion_order
    ):
        telemetry.start(run_id="run-1")
        for agent_name in (
            "a-reviewer",
            "a-reviewer",
            "b-reviewer",
            "b-reviewer",
            "b-reviewer",
        ):
            telemetry.log_agent_start(agent_name=agent_name, domain="code")
        for agent_name in completion_order:
            telemetry.log_agent_complete(
                agent_name=agent_name, review_digest=FINAL_DIGEST,
                verdict="approve",
            )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert _read_manifest(telemetry)["agents"]["incomplete"] == [
            "a-reviewer",
            "b-reviewer",
            "b-reviewer",
        ]

    def test_running_manifest_materializes_current_unmatched_executions(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        telemetry.log_step(
            step=6,
            phase="EXECUTION",
            title="Observe Agent Lifecycle",
        )

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "running"
        assert manifest["agents"]["incomplete"] == ["code-reviewer"]

    def test_agent_manifest_allowlists_aggregate_severity_fields(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            agent_name="security-reviewer",
            domain="security",
            model_tier="sonnet",
            scope_files=2,
            scope_lines=40,
            budget_target=20,
        )
        telemetry.log_agent_complete(
            agent_name="security-reviewer",
            review_digest=FINAL_DIGEST,
            verdict="comment",
            finding_count=1,
            severities={
                "high": 1,
                "prompt": "SENSITIVE_AGENT_PROMPT",
                "tool_result": {"body": "SENSITIVE_AGENT_RESULT"},
            },
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert agents["started"][0]["scope"] == {"files": 2, "lines": 40}
        assert agents["started"][0]["budget_target"] == 20
        assert agents["completed"][0]["severities"] == {"high": 1}
        serialized = json.dumps(agents)
        assert "SENSITIVE_AGENT_PROMPT" not in serialized
        assert "SENSITIVE_AGENT_RESULT" not in serialized

    def test_agent_manifest_allowlists_only_sanitized_scope_paths(
        self, telemetry
    ):
        telemetry.start(run_id="run-1", repo_path="/repo")
        with open(telemetry.log_path, "a") as log:
            log.write(json.dumps({
                "event": "agent_start",
                "agent": "security-reviewer",
                "scope": {
                    "files": 4,
                    "lines": 80,
                    "paths": [
                        "./src/ok.py",
                        {"nested": "SENSITIVE_NESTED_PATH"},
                        ["SENSITIVE_LIST_PATH"],
                        "../SENSITIVE_TRAVERSAL_PATH",
                        "/Users/alice/SENSITIVE_HOST_PATH",
                    ],
                    "arbitrary": "SENSITIVE_SCOPE_FIELD",
                },
            }) + "\n")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        started = _read_manifest(telemetry)["agents"]["started"][0]
        assert started["scope"] == {
            "files": 4,
            "lines": 80,
            "paths": ["src/ok.py"],
        }
        serialized = json.dumps(started)
        assert "SENSITIVE_" not in serialized
        assert "arbitrary" not in serialized

    def test_builds_canonical_assigned_excluded_and_uncovered_coverage(
        self, telemetry, output_dir
    ):
        _write_coverage_inputs(
            output_dir,
            changed=[
                "./src/a.py",
                "src//b.py",
                "docs\\readme.md",
                "vendor/generated.js",
            ],
            reviewable=["src/a.py", "src/b.py", "docs/readme.md"],
            agents=[
                {"name": "security-reviewer", "status": "DISPATCH"},
                {"name": "docs-reviewer", "status": "DISPATCH"},
            ],
        )
        telemetry.start(run_id="run-1", repo_path="/repo")
        telemetry.log_agent_start(
            "security-reviewer",
            scope_paths=[
                "src/b.py",
                "src/a.py",
                "src/a.py",
                "vendor/generated.js",
                "outside/context.py",
            ],
        )
        telemetry.log_agent_start(
            "docs-reviewer", scope_paths=["docs/readme.md"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is True
        assert manifest["assignment"] == {
            "changed_files": [
                "docs/readme.md",
                "src/a.py",
                "src/b.py",
                "vendor/generated.js",
            ],
            "reviewable_files": ["docs/readme.md", "src/a.py", "src/b.py"],
            "assigned_files_by_agent": {
                "docs-reviewer": ["docs/readme.md"],
                "security-reviewer": [
                    "src/a.py",
                    "src/b.py",
                    "vendor/generated.js",
                ],
            },
            "assigned_files": ["docs/readme.md", "src/a.py", "src/b.py"],
            "file_exclusions": [
                {"path": "vendor/generated.js", "reason": "noise_filtered"},
            ],
            "unassigned_reviewable_files": [],
            "reviewed_files_by_agent": {},
            "review_claimable_file_count_by_agent": {},
            "semantics": "generated_scope_not_proof_of_model_read",
        }

    def test_manifest_unassigned_and_recon_unscoped_files_diverge_by_design(
        self, telemetry, output_dir
    ):
        """The one-definition guarantee, in the shape this repo chose:
        these two measurements are NOT unified, and this pins exactly how
        they differ so a future "reconcile the numbers" edit has to argue
        with a test instead of guessing.

        Both answer "which changed files did no agent's scope contain",
        from different evidence over different populations. Read the
        DIVERGENCE NOTE at `manifest_sections.py`'s
        `"unassigned_reviewable_files"` key and
        its reciprocal at `reconciliation_context.py`'s `"unscoped_files"`
        before changing either.
        """
        # Non-ASCII on purpose: the changed set arrives Git-C-quoted (a
        # plain `git diff --name-only`) while every scope producer emits
        # real UTF-8, so both measurements have to decode through the one
        # shared grammar before they can be compared at all. An ASCII-only
        # fixture would pass with either side skipping normalization.
        changed = [
            r'"src/caf\303\251.py"', "src/orphan.py", "vendor/generated.js",
        ]
        _write_coverage_inputs(
            output_dir,
            changed=changed,
            # vendor/generated.js is noise-filtered out of `reviewable`.
            reviewable=[r'"src/caf\303\251.py"', "src/orphan.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1", repo_path="/repo")
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["src/café.py"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")
        # The runtime sidecar the reviewer actually wrote, unquoted because
        # scope.py runs `-c core.quotepath=false`.
        (output_dir / "security-reviewer-scope-summary.json").write_text(
            json.dumps({
                "schema": 3,
                "inline_diff_files": ["src/café.py"],
                "review_claimable_files": [],
                "list_only_files": [],
                "routing_files": ["src/café.py"],
            })
        )

        manifest_uncovered = _read_manifest(telemetry)["assignment"][
            "unassigned_reviewable_files"
        ]
        recon_unscoped = aggregate_file_review(
            str(output_dir), changed_files=changed
        )["unscoped_files"]

        # Population: the manifest works over `reviewable`, so the
        # noise-filtered file can never appear there — it is reported under
        # `excluded` instead. The reconciliation context works over the
        # full changed set, so it does.
        assert manifest_uncovered == ["src/orphan.py"]
        assert recon_unscoped == ["src/orphan.py", "vendor/generated.js"]
        assert manifest_uncovered != recon_unscoped
        # And neither reports the covered non-ASCII file as uncovered.
        assert "src/café.py" not in manifest_uncovered
        assert "src/café.py" not in recon_unscoped

    def test_git_c_quoted_unicode_paths_match_real_unicode_scope(
        self, telemetry, output_dir
    ):
        git_quoted = r'"src/\346\270\254\350\251\246.py"'
        unicode_path = "src/測試.py"
        _write_coverage_inputs(
            output_dir,
            changed=[git_quoted],
            reviewable=[git_quoted],
            agents=[{"name": "code-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "code-reviewer", scope_paths=[unicode_path]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is True
        assert manifest["assignment"]["changed_files"] == [unicode_path]
        assert manifest["assignment"]["reviewable_files"] == [unicode_path]
        assert manifest["assignment"]["assigned_files_by_agent"] == {
            "code-reviewer": [unicode_path],
        }
        assert manifest["assignment"]["assigned_files"] == [unicode_path]
        assert manifest["assignment"]["unassigned_reviewable_files"] == []

    def test_git_quoted_literal_backslash_does_not_collide_with_nested_path(
        self, telemetry, output_dir
    ):
        git_quoted_backslash = r'"src/literal\\name.py"'
        literal_backslash = r"src/literal\name.py"
        nested_path = "src/literal/name.py"
        _write_coverage_inputs(
            output_dir,
            changed=[git_quoted_backslash, nested_path],
            reviewable=[git_quoted_backslash, nested_path],
            agents=[{"name": "code-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "code-reviewer",
            scope_paths=[git_quoted_backslash, nested_path],
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        coverage = _read_manifest(telemetry)["assignment"]
        assert coverage["changed_files"] == [nested_path, literal_backslash]
        assert coverage["reviewable_files"] == [nested_path, literal_backslash]
        assert coverage["assigned_files_by_agent"]["code-reviewer"] == [
            nested_path,
            literal_backslash,
        ]
        assert coverage["assigned_files"] == [nested_path, literal_backslash]
        assert len(coverage["assigned_files"]) == 2

    def test_quote_delimited_filename_stays_distinct_from_plain_filename(
        self, telemetry, output_dir
    ):
        plain_path = "name.py"
        literal_quoted_path = '"name.py"'
        git_quoted_representation = r'"\"name.py\""'
        _write_coverage_inputs(
            output_dir,
            changed=[plain_path, literal_quoted_path],
            reviewable=[plain_path, literal_quoted_path],
            agents=[{"name": "code-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "code-reviewer", scope_paths=[git_quoted_representation]
        )
        event_scope = _read_events(telemetry.log_path)[1]["scope"]["paths"]

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert event_scope == [literal_quoted_path]
        assert manifest["agents"]["started"][0]["scope"]["paths"] == [
            literal_quoted_path,
        ]
        coverage = manifest["assignment"]
        assert coverage["changed_files"] == [literal_quoted_path, plain_path]
        assert coverage["reviewable_files"] == [literal_quoted_path, plain_path]
        assert coverage["assigned_files_by_agent"] == {
            "code-reviewer": [literal_quoted_path],
        }
        assert coverage["assigned_files"] == [literal_quoted_path]
        assert coverage["unassigned_reviewable_files"] == [plain_path]

    def test_raw_quote_delimited_scope_path_without_escape_is_not_git_wrapper(
        self, telemetry
    ):
        literal_quoted_path = '"name.py"'
        telemetry.start(run_id="run-1")

        telemetry.log_agent_start(
            "code-reviewer",
            scope_paths=["name.py", literal_quoted_path],
        )
        event_scope = _read_events(telemetry.log_path)[1]["scope"]["paths"]
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert event_scope == [literal_quoted_path, "name.py"]
        assert _read_manifest(telemetry)["agents"]["started"][0]["scope"][
            "paths"
        ] == event_scope

    @pytest.mark.parametrize(
        "git_quoted",
        [
            pytest.param(r'"src/\q.py"', id="invalid-escape"),
            pytest.param(r'"src/\377.py"', id="invalid-utf8"),
            pytest.param(r'"src/\346.py', id="unterminated-quote"),
        ],
    )
    def test_malformed_git_quoted_authoritative_path_makes_coverage_unavailable(
        self, telemetry, output_dir, git_quoted
    ):
        _write_coverage_inputs(
            output_dir,
            changed=[git_quoted],
            reviewable=[git_quoted],
            agents=[],
        )

        telemetry.start(run_id="run-1")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is False
        assert manifest["assignment"] is None

    def test_mixed_invalid_persisted_scope_paths_make_coverage_unavailable(
        self, telemetry, output_dir
    ):
        _write_coverage_inputs(
            output_dir,
            changed=["src/a.py"],
            reviewable=["src/a.py"],
            agents=[{"name": "code-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        with open(telemetry.log_path, "a") as log:
            log.write(json.dumps({
                "event": "agent_start",
                "agent": "code-reviewer",
                "scope": {
                    "paths": [
                        "src/a.py",
                        {"nested": "SENSITIVE_INVALID_SCOPE"},
                    ],
                },
            }) + "\n")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["agents"]["started"][0]["scope"]["paths"] == [
            "src/a.py",
        ]
        assert manifest["availability"]["assignment"] is False
        assert manifest["assignment"] is None

    def test_finally_skipped_agent_assigns_nothing_despite_start_event(
        self, telemetry, output_dir
    ):
        _write_coverage_inputs(
            output_dir,
            changed=["src/a.py"],
            reviewable=["src/a.py"],
            agents=[
                {"name": "security-reviewer", "status": "SKIPPED_OVERRIDE"},
            ],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["src/a.py"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        coverage = _read_manifest(telemetry)["assignment"]
        assert coverage["assigned_files_by_agent"] == {}
        assert coverage["assigned_files"] == []
        assert coverage["unassigned_reviewable_files"] == ["src/a.py"]

    def test_planned_but_never_started_agent_leaves_file_uncovered(
        self, telemetry, output_dir
    ):
        _write_coverage_inputs(
            output_dir,
            changed=["src/a.py"],
            reviewable=["src/a.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        coverage = _read_manifest(telemetry)["assignment"]
        assert coverage["assigned_files_by_agent"] == {}
        assert coverage["assigned_files"] == []
        assert coverage["unassigned_reviewable_files"] == ["src/a.py"]

    def test_retries_merge_scope_paths_for_the_same_agent(
        self, telemetry, output_dir
    ):
        _write_coverage_inputs(
            output_dir,
            changed=["src/a.py", "src/b.py"],
            reviewable=["src/a.py", "src/b.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["src/b.py"]
        )
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["src/a.py", "src/b.py"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert _read_manifest(telemetry)["assignment"]["assigned_files_by_agent"] == {
            "security-reviewer": ["src/a.py", "src/b.py"],
        }

    def test_dispatch_override_status_assigns_scope(
        self, telemetry, output_dir
    ):
        _write_coverage_inputs(
            output_dir,
            changed=["templates/page.php"],
            reviewable=["templates/page.php"],
            agents=[{"name": "a11y-reviewer", "status": "DISPATCH_OVERRIDE"}],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "a11y-reviewer", scope_paths=["templates/page.php"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        coverage = _read_manifest(telemetry)["assignment"]
        assert coverage["assigned_files_by_agent"] == {
            "a11y-reviewer": ["templates/page.php"],
        }
        assert coverage["assigned_files"] == ["templates/page.php"]

    @pytest.mark.parametrize(
        "context_payload,plan_payload",
        [
            pytest.param(
                None,
                {"changed_files": ["src/a.py"], "agents": []},
                id="missing-context",
            ),
            pytest.param(
                "NOT JSON",
                {"changed_files": ["src/a.py"], "agents": []},
                id="malformed-context",
            ),
            pytest.param(
                {"git": {}},
                {"changed_files": ["src/a.py"], "agents": []},
                id="partial-context",
            ),
            pytest.param(
                {"git": {"changed_files": ["src/a.py", None]}},
                {"changed_files": ["src/a.py"], "agents": []},
                id="malformed-context-paths",
            ),
            pytest.param(
                {"git": {"changed_files": ["src/a.py"]}},
                None,
                id="missing-plan",
            ),
            pytest.param(
                {"git": {"changed_files": ["src/a.py"]}},
                "NOT JSON",
                id="malformed-plan",
            ),
            pytest.param(
                {"git": {"changed_files": ["src/a.py"]}},
                {"agents": []},
                id="partial-plan",
            ),
        ],
    )
    def test_coverage_is_explicitly_unavailable_for_incomplete_inputs(
        self, mod, tmp_path, context_payload, plan_payload, capsys
    ):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        if context_payload is not None:
            (output_dir / "review-context.json").write_text(
                context_payload
                if isinstance(context_payload, str)
                else json.dumps(context_payload)
            )
        if plan_payload is not None:
            (output_dir / "dispatch-plan.json").write_text(
                plan_payload
                if isinstance(plan_payload, str)
                else json.dumps(plan_payload)
            )
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        telemetry.start(run_id="run-1")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is False
        assert manifest["assignment"] is None
        # Legitimate absence (malformed/partial/missing inputs) is normal
        # operation and must stay silent — only an unexpected builder bug
        # is diagnostic-worthy. See
        # test_unexpected_coverage_builder_exception_is_diagnosed_on_stderr.
        assert capsys.readouterr().err == ""

    def test_unexpected_coverage_builder_exception_is_diagnosed_on_stderr(
        self, mod, telemetry, output_dir, capsys, monkeypatch
    ):
        """A bug inside the coverage builder must be distinguishable from
        the legitimate ``return None`` absence paths above: it still
        yields ``coverage: None`` (fail-open — the run is unaffected) but
        it must be diagnosed on stderr, unlike every silent absence path.
        """
        _write_coverage_inputs(
            output_dir,
            changed=["src/a.py"],
            reviewable=["src/a.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )

        def _boom(*_args, **kwargs):
            # `repo_path` is passed only by build_assignment_manifest, so
            # this breaks the coverage builder alone and leaves the
            # context extract — which shares this same normalizer, and
            # runs in the same finalize — working.
            if "repo_path" not in kwargs:
                return normalize_repo_paths(*_args, **kwargs)
            raise RuntimeError("simulated coverage builder bug")

        # normalize_paths is the sole collaborator build_assignment_manifest
        # takes as an injected dependency; breaking it simulates a real
        # defect in the builder without touching its explicit absence
        # branches (those `return None` directly, never raising). Patched
        # in telemetry's namespace, which is where the injection site reads
        # the shared `git_paths` normalizer from.
        normalize_repo_paths = mod.normalize_repo_paths
        monkeypatch.setattr(mod, "normalize_repo_paths", _boom)

        # Must not raise: a builder bug must never fail the review run.
        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is False
        assert manifest["assignment"] is None
        err = capsys.readouterr().err
        assert "assignment manifest build failed" in err
        assert str(output_dir) in err
        assert "simulated coverage builder bug" in err

    def test_build_assignment_manifest_diagnoses_unexpected_exception_directly(
        self, mod, capsys
    ):
        """Direct-call pin on build_assignment_manifest's own contract.

        normalize_paths is an injected parameter of the function itself, so
        this exercises the except-Exception path with zero telemetry
        coupling — a stronger seam than going through ReviewTelemetry.
        """
        def _boom(*args, **kwargs):
            raise RuntimeError("simulated coverage builder bug")

        final_info = {
            "available": True,
            "duplicates": [],
            "plan": {"changed_files": ["src/a.py"]},
            "index": {},
        }

        result = mod.manifest_sections.build_assignment_manifest(
            "/output/dir",
            [],
            {"git": {"changed_files": ["src/a.py"]}},
            "/repo",
            final_info,
            normalize_paths=_boom,
        )

        assert result is None
        err = capsys.readouterr().err
        assert "assignment manifest build failed for /output/dir" in err
        assert "simulated coverage builder bug" in err

    def test_valid_empty_path_sets_are_available_zero_coverage(
        self, telemetry, output_dir
    ):
        _write_coverage_inputs(
            output_dir, changed=[], reviewable=[], agents=[]
        )

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is True
        assert manifest["assignment"] == {
            "changed_files": [],
            "reviewable_files": [],
            "assigned_files_by_agent": {},
            "assigned_files": [],
            "file_exclusions": [],
            "unassigned_reviewable_files": [],
            "reviewed_files_by_agent": {},
            "review_claimable_file_count_by_agent": {},
            "semantics": "generated_scope_not_proof_of_model_read",
        }

    def test_duplicate_final_agent_names_make_coverage_unavailable(
        self, telemetry, output_dir
    ):
        _write_coverage_inputs(
            output_dir,
            changed=["src/a.py"],
            reviewable=["src/a.py"],
            agents=[
                {"name": "security-reviewer", "status": "DISPATCH"},
                {"name": "security-reviewer", "status": "SKIPPED_OVERRIDE"},
            ],
        )
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["src/a.py"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["assignment"] is False
        assert manifest["assignment"] is None

    def test_coverage_carries_canonical_reviewed_files_per_reviewer(
        self, telemetry, output_dir
    ):
        from review.agent.output import ReviewOutputBuilder, finalize_review

        _write_coverage_inputs(
            output_dir,
            changed=["a.py", "b.py", "c.py"],
            reviewable=["a.py", "b.py", "c.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )
        (output_dir / "security-assignment.json").write_text(json.dumps({
            "schema": 4,
            "agent_name": "security-reviewer",
            "reviewer": "security",
            "review_claimable_files": ["a.py", "b.py", "c.py"],
            "review_budget": 15,
            "in_scope_review_file_count": 3,
            "inline_diff_file_count": 0,
            "channels": ["blocking"],
        }))
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["a.py", "b.py", "c.py"]
        )
        builder = ReviewOutputBuilder.open(str(output_dir), "42", "security")
        builder.claim_files_reviewed("a.py")
        saved = builder.save_draft()
        finalize_review(
            str(output_dir), "security", saved["review_digest"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        coverage = _read_manifest(telemetry)["assignment"]
        assert coverage["reviewed_files_by_agent"] == {
            "security-reviewer": {
                "reviewed_file_claim_count": 1,
                "unclaimed_review_file_count": 2,
            },
        }
        assert coverage["review_claimable_file_count_by_agent"] == {
            "security-reviewer": 3
        }

    def test_direct_coverage_reads_follow_review_paths_authority(
        self, mod, output_dir, monkeypatch
    ):
        authority_dir = output_dir / "authority"
        authority_dir.mkdir()
        paths = ReviewPaths(
            draft=str(authority_dir / "draft.json"),
            final=str(authority_dir / "final.json"),
            assignment=str(authority_dir / "authority.json"),
        )
        Path(paths.final).write_text(json.dumps(canonical_review_document(
            "security",
            reviewed_file_claims=["a.py"],
            review_claimable_files=["a.py", "b.py"],
        )))
        Path(paths.assignment).write_text(json.dumps({
            "schema": 4,
            "agent_name": "security-reviewer",
            "reviewer": "security",
            "review_claimable_files": ["a.py", "b.py"],
            "review_budget": 15,
            "in_scope_review_file_count": 2,
            "inline_diff_file_count": 0,
            "channels": ["blocking"],
        }))
        monkeypatch.setattr(
            mod.manifest_sections, "review_paths", lambda *_args: paths
        )

        review = mod.manifest_sections._load_final_review(
            str(output_dir), "security-reviewer"
        )
        claimable_count = (
            mod.manifest_sections._load_review_claimable_file_count(
                str(output_dir), "security-reviewer"
            )
        )

        assert len(review["reviewed_file_claims"]) == 1
        assert len(review["unclaimed_review_files"]) == 1
        assert len(review["review_claimable_files"]) == 2
        assert claimable_count == 2

    def test_reviewed_files_rejects_retired_final_review(
        self, mod, output_dir
    ):
        paths = ReviewPaths(
            draft=str(output_dir / "security-review.draft.json"),
            final=str(output_dir / "security-review.json"),
            assignment=str(
                output_dir / "security-assignment.json"
            ),
        )
        Path(paths.final).write_text(json.dumps({
            "schema": 1,
            "reviewer": "security",
            "issues": [],
            "reviewed_file_claims": [],
        }))
        Path(paths.assignment).write_text(json.dumps({
            "schema": 4,
            "agent_name": "security-reviewer",
            "reviewer": "security",
            "review_claimable_files": [],
            "review_budget": 15,
            "in_scope_review_file_count": 0,
            "inline_diff_file_count": 0,
            "channels": ["blocking"],
        }))

        assert mod.manifest_sections._load_final_review(
            str(output_dir), "security-reviewer"
        ) is None

    def test_coverage_omits_unfinalized_draft_counts(
        self, telemetry, output_dir
    ):
        from review.agent.output import ReviewOutputBuilder

        _write_coverage_inputs(
            output_dir,
            changed=["a.py"],
            reviewable=["a.py"],
            agents=[{"name": "security-reviewer", "status": "DISPATCH"}],
        )
        (output_dir / "security-assignment.json").write_text(json.dumps({
            "schema": 4,
            "agent_name": "security-reviewer",
            "reviewer": "security",
            "review_claimable_files": ["a.py"],
            "review_budget": 15,
            "in_scope_review_file_count": 1,
            "inline_diff_file_count": 0,
            "channels": ["blocking"],
        }))
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start("security-reviewer", scope_paths=["a.py"])
        ReviewOutputBuilder.open(
            str(output_dir), "42", "security"
        ).save_draft()
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        coverage = _read_manifest(telemetry)["assignment"]
        assert coverage["reviewed_files_by_agent"] == {}
        assert coverage["review_claimable_file_count_by_agent"] == {
            "security-reviewer": 1
        }

    def test_manifest_path_resolves_from_marker_in_fresh_instance(
        self, telemetry, mod, output_dir, tmp_path
    ):
        log_path = telemetry.start(run_id="run-1")

        later_process = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        assert later_process.manifest_path == str(
            Path(log_path).with_suffix(".manifest.json")
        )

    def test_manifest_merges_non_empty_resolved_context_git_identity(
        self, telemetry, output_dir
    ):
        resolved_head = "b" * 40
        telemetry.start(
            run_id="run-1",
            git_range="initial-base..initial-head",
            base_sha="initial-base",
            head_sha="initial-head",
        )
        (output_dir / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "resolved-base..resolved-head",
                "merge_base": "",
                "head_sha": resolved_head,
            },
        }))

        telemetry.log_step(step=3, phase="AWARENESS", title="Gather Context")

        assert _read_manifest(telemetry)["run"]["git"] == {
            "requested_range": "resolved-base..resolved-head",
            "base_sha": "initial-base",
            "head_sha": resolved_head,
        }

    def test_manifest_refresh_keeps_resolved_shas_over_symbolic_context_refs(
        self, telemetry, output_dir
    ):
        """An explicit symbolic range stores "main" as context merge_base;
        the refresh must not replace the resolved durable identity with a
        movable ref."""
        resolved_base = "a" * 40
        resolved_head = "b" * 40
        telemetry.start(
            run_id="run-1",
            git_range="main..HEAD",
            base_sha=resolved_base,
            head_sha=resolved_head,
        )
        (output_dir / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "main..HEAD",
                "merge_base": "main",
                "head_sha": "HEAD",
            },
        }))

        telemetry.log_step(step=3, phase="AWARENESS", title="Gather Context")

        assert _read_manifest(telemetry)["run"]["git"] == {
            "requested_range": "main..HEAD",
            "base_sha": resolved_base,
            "head_sha": resolved_head,
        }

    def test_manifest_compares_planner_and_orchestrator_dispatches(
        self, telemetry, output_dir
    ):
        initial = {
            "agent_signals": [
                "security-reviewer: STATUS=DISPATCH (keywords matched (files: auth))",
                "a11y-reviewer: STATUS=SKIPPED_TRIAGE (no UI signal)",
                "code-reviewer: STATUS=DISPATCH",
            ],
            "agents": [
                {
                    "name": "security-reviewer",
                    "domain": "security",
                    "status": "DISPATCH",
                    "reason": "keywords matched (files: auth)",
                },
                {
                    "name": "a11y-reviewer",
                    "domain": "a11y",
                    "status": "SKIPPED_TRIAGE",
                    "reason": "no UI signal",
                },
                {
                    "name": "code-reviewer",
                    "domain": "code",
                    "status": "DISPATCH",
                    "reason": "always dispatch (domain has files)",
                },
            ]
        }
        final = {
            "agents": [
                {
                    "name": "security-reviewer",
                    "domain": "security",
                    "status": "SKIPPED_OVERRIDE",
                    "reason": "keywords matched (files: auth)",
                    "override_reason": "change does not touch an auth boundary",
                },
                {
                    "name": "a11y-reviewer",
                    "domain": "a11y",
                    "status": "DISPATCH_OVERRIDE",
                    "reason": "no UI signal",
                    "override_reason": "rendered markup coverage was missed",
                },
                {
                    "name": "code-reviewer",
                    "domain": "code",
                    "status": "DISPATCH",
                    "reason": "always dispatch (domain has files)",
                },
            ]
        }
        (output_dir / "dispatch-plan.initial.json").write_text(json.dumps(initial))
        (output_dir / "dispatch-plan.json").write_text(json.dumps(final))

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        dispatch = _read_manifest(telemetry)["dispatch"]
        assert dispatch["planner_baseline_available"] is True
        assert dispatch["final_plan_available"] is True
        assert dispatch["comparison_available"] is True
        assert dispatch["adjustment_counts"] == {
            "added": 1,
            "removed": 1,
            "unchanged": 1,
        }
        assert dispatch["planner_candidate_count"] == 2
        assert dispatch["final_dispatch_count"] == 2

        removed = dispatch["agents"]["security-reviewer"]
        assert removed == {
            "domain": "security",
            "initial_status": "DISPATCH",
            "initial_reason": "keywords matched (files: auth)",
            "final_status": "SKIPPED_OVERRIDE",
            "final_reason": "keywords matched (files: auth)",
            "planner_signals": [
                "security-reviewer: STATUS=DISPATCH (keywords matched (files: auth))"
            ],
            "configured_planner_checks": [],
            "model_tier": "sonnet",
            "declared_model": None,
            "adjustment_reason": "change does not touch an auth boundary",
            "change": "removed",
        }
        added = dispatch["agents"]["a11y-reviewer"]
        assert added["initial_status"] == "SKIPPED_TRIAGE"
        assert added["final_status"] == "DISPATCH_OVERRIDE"
        assert added["adjustment_reason"] == "rendered markup coverage was missed"
        assert added["change"] == "added"
        assert added["configured_planner_checks"] == [
            "has_markup_changes",
            "has_style_files",
            "has_template_files",
        ]
        assert added["model_tier"] == "opus"
        assert dispatch["agents"]["code-reviewer"]["change"] == "unchanged"

    def test_repo_reviewer_model_override_reaches_dispatch_telemetry(
        self, telemetry, output_dir
    ):
        """Adapter entries carry their explicit override under "model" (the
        dispatch contract step 6 honors) and have no registry fallback —
        without reading it, their requested tier is omitted."""
        entry = {
            "name": "repo-renewals-reviewer",
            "domain": None,
            "status": "DISPATCH",
            "reason": "repo-declared reviewer applies",
            "adapter": "repo-reviewer-adapter",
            "model": "opus",
        }
        (output_dir / "dispatch-plan.initial.json").write_text(
            json.dumps({"agents": [entry]})
        )
        (output_dir / "dispatch-plan.json").write_text(
            json.dumps({"agents": [entry]})
        )

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        dispatch = _read_manifest(telemetry)["dispatch"]
        assert dispatch["agents"]["repo-renewals-reviewer"]["model_tier"] == "opus"

    def test_repo_reviewer_declared_model_reaches_dispatch_telemetry(
        self, telemetry, output_dir
    ):
        entry = {
            "name": "repo-renewals-reviewer",
            "domain": None,
            "status": "DISPATCH",
            "reason": "repo-declared reviewer applies",
            "adapter": "repo-reviewer-adapter",
            "model": "inherit",
            "declared_model": "opus",
        }
        (output_dir / "dispatch-plan.initial.json").write_text(
            json.dumps({"agents": [entry]})
        )
        (output_dir / "dispatch-plan.json").write_text(
            json.dumps({"agents": [entry]})
        )

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        decision = _read_manifest(telemetry)["dispatch"]["agents"][
            "repo-renewals-reviewer"
        ]
        assert decision["model_tier"] == "inherit"
        assert decision["declared_model"] == "opus"

    # manifest_sections.py:203-207 calls the same inspect_dispatch_plan() for
    # both plans and both land on the one validator at dispatch_status.py:50-53,
    # so the plan_name axis doubled nodes without reaching new code. One param
    # per invalid shape the validator can distinguish is what remains.
    @pytest.mark.parametrize(
        "invalid_status",
        [
            pytest.param("__missing__", id="missing"),
            None,
            "",
            "UNKNOWN",
        ],
    )
    def test_manifest_rejects_incomplete_dispatch_statuses(
        self, telemetry, output_dir, invalid_status
    ):
        initial_agent = {"name": "code-reviewer", "status": "DISPATCH"}
        final_agent = {"name": "code-reviewer", "status": "DISPATCH"}
        if invalid_status == "__missing__":
            initial_agent.pop("status")
        else:
            initial_agent["status"] = invalid_status
        (output_dir / "dispatch-plan.initial.json").write_text(
            json.dumps({"agents": [initial_agent]})
        )
        (output_dir / "dispatch-plan.json").write_text(
            json.dumps({"agents": [final_agent]})
        )

        telemetry.start(run_id="run-1")
        dispatch = _read_manifest(telemetry)["dispatch"]

        assert dispatch["comparison_available"] is False
        assert dispatch["planner_baseline_available"] is False
        assert "planner_baseline_unavailable" in dispatch["invalid_reason_codes"]
        assert dispatch["final_plan_available"] is True
        assert dispatch["agents"]["code-reviewer"]["initial_status"] == "DISPATCH"
        assert dispatch["agents"]["code-reviewer"]["final_status"] == "DISPATCH"
        assert dispatch["agents"]["code-reviewer"]["change"] == "unchanged"

    @pytest.mark.parametrize(
        "status,dispatched",
        [
            ("DISPATCH", True),
            ("DISPATCH_OVERRIDE", True),
            ("SKIPPED", False),
            ("SKIPPED_OVERRIDE", False),
            ("SKIPPED_QUICK_MODE", False),
            ("SKIPPED_TRIAGE", False),
        ],
    )
    def test_manifest_accepts_supported_dispatch_status_vocabulary(
        self, telemetry, output_dir, status, dispatched
    ):
        plan = {"agents": [{"name": "code-reviewer", "status": status}]}
        (output_dir / "dispatch-plan.initial.json").write_text(json.dumps(plan))
        (output_dir / "dispatch-plan.json").write_text(json.dumps(plan))

        telemetry.start(run_id="run-1")
        dispatch = _read_manifest(telemetry)["dispatch"]

        assert dispatch["comparison_available"] is True
        assert dispatch["planner_candidate_count"] == int(dispatched)
        assert dispatch["final_dispatch_count"] == int(dispatched)
        assert dispatch["agents"]["code-reviewer"]["change"] == "unchanged"

    @pytest.mark.parametrize(
        "initial_names,final_names,planner_count,final_count",
        [
            (["code-reviewer"], ["code-reviewer", "security-reviewer"], 1, 2),
            (["code-reviewer", "security-reviewer"], ["code-reviewer"], 2, 1),
        ],
        ids=["agent-added", "agent-removed"],
    )
    def test_manifest_agent_set_mismatch_disables_only_comparison(
        self,
        telemetry,
        output_dir,
        initial_names,
        final_names,
        planner_count,
        final_count,
    ):
        def plan(names):
            return {
                "agents": [
                    {"name": name, "status": "DISPATCH"}
                    for name in names
                ]
            }

        (output_dir / "dispatch-plan.initial.json").write_text(
            json.dumps(plan(initial_names))
        )
        (output_dir / "dispatch-plan.json").write_text(
            json.dumps(plan(final_names))
        )

        telemetry.start(run_id="run-1")
        dispatch = _read_manifest(telemetry)["dispatch"]

        assert dispatch == {
            "planner_baseline_available": True,
            "final_plan_available": True,
            "comparison_available": False,
            "planner_candidate_count": planner_count,
            "final_dispatch_count": final_count,
            "adjustment_counts": {"added": 0, "removed": 0, "unchanged": 0},
            "invalid_reason_codes": ["dispatch_agent_set_mismatch"],
            "agents": {},
            "plan_projections": {
                "planner_baseline": {
                    name: "DISPATCH" for name in initial_names
                },
                "final_plan": {
                    name: "DISPATCH" for name in final_names
                },
            },
        }

    def test_manifest_agent_set_mismatch_projects_sorted_statuses_without_plan_prose(
        self, telemetry, output_dir
    ):
        initial = {
            "agents": [
                {
                    "name": "z-reviewer",
                    "status": "SKIPPED_TRIAGE",
                    "reason": "SENSITIVE_INITIAL_REASON",
                    "raw_diff": "SENSITIVE_INITIAL_SOURCE",
                },
                {"name": "a-reviewer", "status": "DISPATCH"},
            ]
        }
        final = {
            "agents": [
                {
                    "name": "m-reviewer",
                    "status": "SKIPPED_OVERRIDE",
                    "override_reason": "SENSITIVE_FINAL_REASON",
                    "private_notes": ["SENSITIVE_FINAL_FINDING"],
                },
                {"name": "a-reviewer", "status": "DISPATCH_OVERRIDE"},
            ]
        }
        (output_dir / "dispatch-plan.initial.json").write_text(
            json.dumps(initial)
        )
        (output_dir / "dispatch-plan.json").write_text(json.dumps(final))

        telemetry.start(run_id="run-1")
        dispatch = _read_manifest(telemetry)["dispatch"]

        assert dispatch["planner_candidate_count"] == 1
        assert dispatch["final_dispatch_count"] == 1
        assert dispatch["plan_projections"] == {
            "planner_baseline": {
                "a-reviewer": "DISPATCH",
                "z-reviewer": "SKIPPED_TRIAGE",
            },
            "final_plan": {
                "a-reviewer": "DISPATCH_OVERRIDE",
                "m-reviewer": "SKIPPED_OVERRIDE",
            },
        }
        assert list(dispatch["plan_projections"]["planner_baseline"]) == [
            "a-reviewer",
            "z-reviewer",
        ]
        assert list(dispatch["plan_projections"]["final_plan"]) == [
            "a-reviewer",
            "m-reviewer",
        ]
        serialized = json.dumps(dispatch)
        assert not any(
            sentinel in serialized
            for sentinel in (
                "SENSITIVE_INITIAL_REASON",
                "SENSITIVE_INITIAL_SOURCE",
                "SENSITIVE_FINAL_REASON",
                "SENSITIVE_FINAL_FINDING",
            )
        )

    def test_manifest_agent_set_mismatch_allows_one_empty_identity_set(
        self, telemetry, output_dir
    ):
        (output_dir / "dispatch-plan.initial.json").write_text(
            json.dumps({"agents": []})
        )
        (output_dir / "dispatch-plan.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "name": "security-reviewer",
                            "status": "SKIPPED_TRIAGE",
                        }
                    ]
                }
            )
        )

        telemetry.start(run_id="run-1")
        dispatch = _read_manifest(telemetry)["dispatch"]

        assert dispatch["planner_candidate_count"] == 0
        assert dispatch["final_dispatch_count"] == 0
        assert dispatch["plan_projections"] == {
            "planner_baseline": {},
            "final_plan": {"security-reviewer": "SKIPPED_TRIAGE"},
        }

    @pytest.mark.parametrize(
        "mode",
        ["comparable", "legacy-final", "unavailable", "duplicate"],
    )
    def test_manifest_omits_plan_projections_outside_agent_set_mismatch(
        self, telemetry, output_dir, mode
    ):
        plan = {
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}]
        }
        if mode in {"comparable", "duplicate"}:
            initial = plan
            if mode == "duplicate":
                initial = {"agents": plan["agents"] * 2}
            (output_dir / "dispatch-plan.initial.json").write_text(
                json.dumps(initial)
            )
        if mode in {"comparable", "legacy-final", "duplicate"}:
            (output_dir / "dispatch-plan.json").write_text(json.dumps(plan))

        telemetry.start(run_id="run-1")

        assert "plan_projections" not in _read_manifest(telemetry)["dispatch"]

    def test_manifest_legacy_plan_falls_back_to_unchanged_baseline(
        self, telemetry, output_dir
    ):
        final = {
            "agents": [
                {
                    "name": "security-reviewer",
                    "domain": "security",
                    "status": "DISPATCH_OVERRIDE",
                    "reason": "legacy plan",
                    "override_reason": "legacy adjustment without a baseline",
                }
            ]
        }
        (output_dir / "dispatch-plan.json").write_text(json.dumps(final))

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        dispatch = _read_manifest(telemetry)["dispatch"]
        assert dispatch["planner_baseline_available"] is False
        assert dispatch["final_plan_available"] is True
        assert dispatch["comparison_available"] is False
        decision = dispatch["agents"]["security-reviewer"]
        assert decision["initial_status"] == "DISPATCH_OVERRIDE"
        assert decision["final_status"] == "DISPATCH_OVERRIDE"
        assert decision["change"] == "unchanged"
        assert dispatch["adjustment_counts"] == {
            "added": 0,
            "removed": 0,
            "unchanged": 1,
        }
        assert dispatch["planner_candidate_count"] == 1
        assert dispatch["final_dispatch_count"] == 1

    def test_manifest_malformed_baseline_uses_legacy_unchanged_projection(
        self, telemetry, output_dir
    ):
        (output_dir / "dispatch-plan.initial.json").write_text("NOT JSON")
        (output_dir / "dispatch-plan.json").write_text(json.dumps({
            "agents": [
                {
                    "name": "code-reviewer",
                    "domain": "code",
                    "status": "DISPATCH",
                    "reason": "always",
                }
            ]
        }))

        telemetry.start(run_id="run-1")

        dispatch = _read_manifest(telemetry)["dispatch"]
        assert dispatch["planner_baseline_available"] is False
        assert dispatch["final_plan_available"] is True
        assert dispatch["comparison_available"] is False
        assert dispatch["agents"]["code-reviewer"]["change"] == "unchanged"
        assert dispatch["adjustment_counts"] == {
            "added": 0,
            "removed": 0,
            "unchanged": 1,
        }

    def test_manifest_dispatch_is_fail_open_for_malformed_partial_plans(
        self, telemetry, output_dir
    ):
        (output_dir / "dispatch-plan.initial.json").write_text("NOT JSON")
        (output_dir / "dispatch-plan.json").write_text(json.dumps({
            "agents": [
                None,
                "not-an-agent",
                {"status": "DISPATCH"},
                {
                    "name": "code-reviewer",
                    "status": "DISPATCH",
                    "reason": "always",
                },
            ]
        }))

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "complete"
        dispatch = manifest["dispatch"]
        assert dispatch["planner_baseline_available"] is False
        assert dispatch["final_plan_available"] is False
        assert dispatch["comparison_available"] is False
        assert dispatch["agents"] == {}
        assert "planner_baseline_unavailable" in dispatch["invalid_reason_codes"]
        assert "final_plan_unavailable" in dispatch["invalid_reason_codes"]

    def test_manifest_dispatch_allowlist_omits_arbitrary_plan_payloads(
        self, telemetry, output_dir
    ):
        plan = {
            "prompt": "SENSITIVE_PLAN_PROMPT",
            "tool_result": {"body": "SENSITIVE_PLAN_RESULT"},
            "agents": [
                {
                    "name": "code-reviewer",
                    "domain": "code",
                    "status": "DISPATCH",
                    "reason": "always",
                    "focus": "SENSITIVE_FOCUS_PROSE",
                    "raw_diff": "SENSITIVE_SOURCE",
                    "private_notes": ["SENSITIVE_FINDING"],
                }
            ],
        }
        (output_dir / "dispatch-plan.initial.json").write_text(json.dumps(plan))
        (output_dir / "dispatch-plan.json").write_text(json.dumps(plan))

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        serialized = Path(telemetry.manifest_path).read_text()
        assert not any(sentinel in serialized for sentinel in (
            "SENSITIVE_PLAN_PROMPT",
            "SENSITIVE_PLAN_RESULT",
            "SENSITIVE_FOCUS_PROSE",
            "SENSITIVE_SOURCE",
            "SENSITIVE_FINDING",
        ))

    def test_manifest_continues_when_final_dispatch_plan_is_malformed(
        self, telemetry, output_dir
    ):
        (output_dir / "dispatch-plan.initial.json").write_text(json.dumps({
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}]
        }))
        (output_dir / "dispatch-plan.json").write_text("NOT JSON")

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "complete"
        dispatch = manifest["dispatch"]
        assert dispatch["planner_baseline_available"] is True
        assert dispatch["final_plan_available"] is False
        assert dispatch["comparison_available"] is False
        assert dispatch["planner_candidate_count"] == 1
        assert dispatch["final_dispatch_count"] == 0
        assert dispatch["agents"] == {}

    def test_manifest_distinguishes_observed_zero_from_unavailable_zero(
        self, telemetry, output_dir
    ):
        telemetry.start(run_id="run-1")

        unavailable = _read_manifest(telemetry)["dispatch"]
        assert unavailable["planner_candidate_count"] == 0
        assert unavailable["final_dispatch_count"] == 0
        assert unavailable["planner_baseline_available"] is False
        assert unavailable["final_plan_available"] is False
        assert unavailable["comparison_available"] is False

        empty_plan = {"agents": []}
        (output_dir / "dispatch-plan.initial.json").write_text(json.dumps(empty_plan))
        (output_dir / "dispatch-plan.json").write_text(json.dumps(empty_plan))
        telemetry.log_step(step=5, phase="EXECUTION", title="Dispatch Plan")

        observed = _read_manifest(telemetry)["dispatch"]
        assert observed["planner_candidate_count"] == 0
        assert observed["final_dispatch_count"] == 0
        assert observed["planner_baseline_available"] is True
        assert observed["final_plan_available"] is True
        assert observed["comparison_available"] is True

    def test_manifest_duplicate_agents_invalidate_comparison_but_keep_raw_counts(
        self, telemetry, output_dir
    ):
        initial = {
            "agents": [
                {
                    "name": "security-reviewer",
                    "status": "DISPATCH",
                    "reason": "signal",
                    "prompt": "SENSITIVE_DUPLICATE_PROMPT",
                },
                {
                    "name": "security-reviewer",
                    "status": "SKIPPED_TRIAGE",
                    "reason": "conflicting duplicate",
                    "tool_result": "SENSITIVE_DUPLICATE_RESULT",
                },
            ]
        }
        final = {
            "agents": [
                {"name": "security-reviewer", "status": "DISPATCH"},
                {"name": "security-reviewer", "status": "DISPATCH_OVERRIDE"},
            ]
        }
        (output_dir / "dispatch-plan.initial.json").write_text(json.dumps(initial))
        (output_dir / "dispatch-plan.json").write_text(json.dumps(final))

        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        dispatch = _read_manifest(telemetry)["dispatch"]
        assert dispatch["planner_baseline_available"] is True
        assert dispatch["final_plan_available"] is True
        assert dispatch["comparison_available"] is False
        assert dispatch["planner_candidate_count"] == 1
        assert dispatch["final_dispatch_count"] == 2
        assert dispatch["duplicate_agent_names"] == {
            "planner_baseline": ["security-reviewer"],
            "final_plan": ["security-reviewer"],
        }
        assert dispatch["agents"] == {}
        serialized = json.dumps(dispatch)
        assert "SENSITIVE_DUPLICATE_PROMPT" not in serialized
        assert "SENSITIVE_DUPLICATE_RESULT" not in serialized

    def test_read_events_skips_malformed_blank_and_non_object_lines(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_step(step=3, phase="AWARENESS", title="Gather Context")
        with open(telemetry.log_path, "a") as log:
            log.write("\nNOT JSON\n[]\n\"string\"\n")

        events = telemetry._read_events()
        assert [event["event"] for event in events] == [
            "pipeline_start",
            "step",
        ]

    def test_manifest_omits_pr_prompt_finding_and_tool_result_prose(
        self, telemetry, output_dir
    ):
        sentinels = {
            "PR_TITLE_SECRET",
            "PR_AUTHOR_SECRET",
            "PR_BODY_SECRET",
            "RAW_PROMPT_SECRET",
            "SOURCE_SECRET",
            "FINDING_SECRET",
            "TOOL_RESULT_SECRET",
        }
        (output_dir / "review-context.json").write_text(json.dumps({
            "pr": {
                "title": "PR_TITLE_SECRET",
                "author": "PR_AUTHOR_SECRET",
                "body": "PR_BODY_SECRET",
            },
            "prompt": "RAW_PROMPT_SECRET",
            "source": "SOURCE_SECRET",
            "git": {
                "git_range": "base..head",
                "merge_base": "base",
                "head_sha": "head",
            },
        }))
        (output_dir / "review-findings.json").write_text(json.dumps({
            "verdict": "comment",
            "summary": "FINDING_SECRET",
            "findings": [{
                "severity": "medium",
                "description": "FINDING_SECRET",
            }],
        }))
        (output_dir / "pipeline-result.json").write_text(json.dumps({
            "status": "complete",
            "verdict": "COMMENT",
            "critic_verdict": "STAND",
            "tool_result": "TOOL_RESULT_SECRET",
        }))
        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        serialized = Path(telemetry.manifest_path).read_text()
        assert not any(sentinel in serialized for sentinel in sentinels)

    def test_start_manifest_replace_failure_preserves_start_event(
        self, telemetry, mod
    ):
        with patch.object(
            mod.os, "replace", side_effect=OSError("nope")
        ) as replace:
            telemetry.start(run_id="run-1")

        replace.assert_called_once()
        assert _read_events(telemetry.log_path)[-1]["event"] == "pipeline_start"

    def test_log_step_manifest_replace_failure_preserves_step_event_and_cleans_temp(
        self, telemetry, mod
    ):
        telemetry.start(run_id="run-1")
        existing = set(Path(telemetry.log_dir).iterdir())

        with patch.object(
            mod.os, "replace", side_effect=OSError("nope")
        ) as replace:
            telemetry.log_step(step=3, phase="AWARENESS", title="Gather Context")

        replace.assert_called_once()
        assert _read_events(telemetry.log_path)[-1]["step"] == 3
        assert set(Path(telemetry.log_dir).iterdir()) == existing

    def test_finalize_manifest_replace_failure_preserves_end_event(
        self, telemetry, mod
    ):
        telemetry.start(run_id="run-1")

        with patch.object(
            mod.os, "replace", side_effect=OSError("nope")
        ) as replace:
            telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        replace.assert_called_once()
        assert _read_events(telemetry.log_path)[-1]["event"] == "pipeline_end"


# ── Snapshot extraction ─────────────────────────────────────────────


class TestSnapshot:
    """Snapshot extraction — only present in finalize() / pipeline_end."""

    def test_finalize_has_snapshot(self, telemetry, output_dir):
        (output_dir / "review-context.json").write_text('{"version": 1}')
        telemetry.start(pr_number="42")
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(telemetry.log_path)
        assert "snapshot" in events[-1]

    def test_lists_files_with_sizes(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        (output_dir / "review-context.json").write_text('{"version": 1}')
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        files = events[-1]["snapshot"]["files"]
        names = [f["name"] for f in files]
        assert "review-context.json" in names
        for f in files:
            assert "size" in f
            assert isinstance(f["size"], int)

    def test_extracts_context_from_review_context_json(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        (output_dir / "review-context.json").write_text(json.dumps(COMPLETE_CONTEXT))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        ctx = events[-1]["snapshot"]["context"]
        assert ctx["pr_number"] == 42
        assert ctx["pr_title"] == "Fix the thing"
        assert ctx["pr_author"] == "octocat"
        assert ctx["git_range"] == "abc123..fix/thing"
        assert ctx["pr_size"] == {"files": 2, "lines": 38, "category": "small"}
        assert ctx["linked_issues"] == ["WOOPLUG-1234"]
        assert ctx["source"] == "pirategoat-bot"
        assert ctx["changed_files"] == ["src/a.js", "src/b.js"]

    def test_context_changed_files_are_normalized_and_deduplicated(
        self, mod, output_dir, tmp_path
    ):
        (output_dir / "review-context.json").write_text(json.dumps({
            "git": {
                "changed_files": [
                    "./src/a.py",
                    "src//a.py",
                    "tests\\test_a.py",
                ],
            },
        }))
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        telemetry.start(pr_number="42")
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")

        context = _read_events(telemetry.log_path)[-1]["snapshot"]["context"]
        assert context["changed_files"] == ["src/a.py", "tests/test_a.py"]
        assert context["changed_files_count"] == 2

    def test_extracts_dispatch_plan(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        dispatch = {
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH", "domain": "code", "reason": "always"},
                {"name": "security-reviewer", "status": "DISPATCH", "domain": "security", "reason": "triage match"},
                {"name": "performance-reviewer", "status": "SKIPPED_TRIAGE", "domain": "performance", "reason": "no perf files"},
            ]
        }
        (output_dir / "dispatch-plan.json").write_text(json.dumps(dispatch))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        d = events[-1]["snapshot"]["dispatch"]
        assert d["total_agents"] == 3
        assert "DISPATCH" in d["by_status"]
        assert len(d["by_status"]["DISPATCH"]) == 2
        assert d["agents"]["code-reviewer"]["status"] == "DISPATCH"

    @pytest.mark.parametrize(
        "plan",
        [
            pytest.param(
                {
                    "agents": [
                        {
                            "name": "security-reviewer",
                            "status": "DISPATCHED",
                        },
                    ],
                },
                id="unsupported-status",
            ),
            pytest.param({}, id="missing-agents"),
        ],
    )
    def test_invalid_dispatch_plan_omits_snapshot_and_summary(
        self, mod, output_dir, tmp_path, plan
    ):
        (output_dir / "dispatch-plan.json").write_text(json.dumps(plan))
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        telemetry.start(pr_number="42")
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")

        event = _read_events(telemetry.log_path)[-1]
        assert "dispatch" not in event["snapshot"]
        assert "agents_total" not in event["summary"]
        assert "agents_dispatched" not in event["summary"]
        assert "agents_skipped" not in event["summary"]

    def test_dispatch_plan_read_error_fails_open(self, mod, output_dir, tmp_path):
        (output_dir / "dispatch-plan.json").write_text(json.dumps({
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH"},
            ],
        }))
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        with patch("builtins.open", side_effect=OSError("unreadable")):
            dispatch = telemetry._extract_dispatch()

        assert dispatch is None

    def test_extracts_agent_results(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        review = canonical_review_document("security", ["high", "medium"])
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        (output_dir / "security-review.json").write_text(json.dumps(review))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        agents = events[-1]["snapshot"]["agent_results"]
        assert "security" in agents
        assert agents["security"]["verdict"] == "request_changes"
        assert agents["security"]["finding_count"] == 2
        assert agents["security"]["severities"]["high"] == 1

    def test_retired_agent_review_extracts_only_malformed_evidence(
        self, mod, output_dir, tmp_path
    ):
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        (output_dir / "security-review.json").write_text(json.dumps({
            "schema": 1,
            "reviewer": "security",
            "issues": [],
            "verdict": "approve",
        }))
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        assert telemetry._extract_agent_results()["security"] == {
            "error": "malformed"
        }

    def test_extracts_agent_advisory_measurement(self, mod, output_dir, tmp_path):
        review = canonical_review_document(
            "security", ["critical", "critical"]
        )
        for finding in review["findings"]:
            finding["channel"] = "advisory"
        review["verdict"] = "approve"
        review["summary"]["suppressed_advisory_finding_count"] = 2
        review["summary"]["verdict_without_advisory"] = "block"
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        (output_dir / "security-review.json").write_text(json.dumps(review))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))

        extracted = t._extract_agent_results()["security"]

        assert extracted["suppressed_advisory_finding_count"] == 2
        assert extracted["verdict_without_advisory"] == "block"

    def test_excludes_review_findings_from_agent_results(self, mod, output_dir, tmp_path):
        """review-findings.json is reconciled output, not an agent result.

        Asserted on a run that HAS an agent result, so the projection is
        populated and the boundary is a real one: the ledger belongs to
        `findings` and the dispatched reviewer to `agent_results`, and
        neither section may borrow from the other.
        """
        log_dir = tmp_path / "logs"
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        (output_dir / "security-review.json").write_text(json.dumps(
            canonical_review_document("security", ["medium"])
        ))
        (output_dir / "review-findings.json").write_text(json.dumps(
            canonical_findings_ledger(["medium"])
        ))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        snap = events[-1]["snapshot"]
        assert set(snap["agent_results"]) == {"security"}
        assert snap["findings"]["final_finding_count"] == 1

    def test_extracts_findings(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        findings = canonical_findings_ledger(["high", "medium", "low"])
        (output_dir / "review-findings.json").write_text(json.dumps(findings))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        f = events[-1]["snapshot"]["findings"]
        assert f["verdict"] == "request_changes"
        assert f["final_finding_count"] == 3
        assert f["severities"]["high"] == 1

    def test_severities_carry_every_severity_including_the_zeros(
        self, mod, output_dir, tmp_path
    ):
        """A zero is a measurement, and every projection publishes all five.

        The recounts these replaced built their dict from the findings
        actually present, so a manifest could not tell "no critical
        findings" from "critical was never counted". Both severity maps —
        the per-agent one and the ledger's, which `outcome.summary`
        republishes as `final_severities` — now carry the whole
        vocabulary.
        """
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        (output_dir / "security-review.json").write_text(json.dumps(
            canonical_review_document("security", ["high"])
        ))
        (output_dir / "review-findings.json").write_text(json.dumps(
            canonical_findings_ledger(["high"])
        ))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))
        t.start(pr_number="42")

        t.finalize(step=15, phase="OUTPUT", title="Present Results")

        expected = {
            "critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0,
        }
        snapshot = _read_events(t.log_path)[-1]["snapshot"]
        assert snapshot["agent_results"]["security"]["severities"] == expected
        assert snapshot["findings"]["severities"] == expected
        assert _read_manifest(t)["outcome"]["summary"][
            "final_severities"
        ] == expected

    def test_findings_measurement_reaches_summary_and_manifest(
        self, mod, output_dir, tmp_path
    ):
        findings = canonical_findings_ledger(["critical"])
        findings["findings"][0]["channel"] = "advisory"
        findings["verdict"] = "approve"
        findings["summary"].update({
            "suppressed_advisory_finding_count": 1,
            "verdict_without_advisory": "block",
        })
        (output_dir / "review-findings.json").write_text(json.dumps(findings))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))
        t.start(pr_number="42")

        t.finalize(step=15, phase="OUTPUT", title="Present Results")

        event = _read_events(t.log_path)[-1]
        snapshot = event["snapshot"]["findings"]
        assert snapshot["suppressed_advisory_finding_count"] == 1
        assert snapshot["verdict_without_advisory"] == "block"
        assert event["summary"]["final_suppressed_advisory_finding_count"] == 1
        assert event["summary"]["final_verdict_without_advisory"] == "block"
        manifest_summary = _read_manifest(t)["outcome"]["summary"]
        assert manifest_summary["final_suppressed_advisory_finding_count"] == 1
        assert manifest_summary["final_verdict_without_advisory"] == "block"

    def test_findings_omit_malformed_advisory_measurement(
        self, mod, output_dir, tmp_path
    ):
        (output_dir / "review-findings.json").write_text(json.dumps({
            "verdict": "approve",
            "summary": {
                "suppressed_advisory_finding_count": True,
                "verdict_without_advisory": "banana",
            },
            "findings": [],
        }))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))

        findings = t._extract_findings()

        assert findings is None

    def test_findings_preserve_count_but_reject_impossible_counterfactual(
        self, mod, output_dir, tmp_path
    ):
        (output_dir / "review-findings.json").write_text(json.dumps({
            "verdict": "block",
            "summary": {
                "suppressed_advisory_finding_count": 1,
                "verdict_without_advisory": "comment",
            },
            "findings": [],
        }))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))

        findings = t._extract_findings()

        assert findings is None

    def test_omits_missing_snapshot_sections(self, telemetry):
        """Snapshot keys are absent when source files don't exist."""
        telemetry.start(pr_number="42")
        telemetry.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(telemetry.log_path)
        snap = events[-1]["snapshot"]
        assert "context" not in snap
        assert "dispatch" not in snap
        assert "agent_results" not in snap
        assert "findings" not in snap

    def test_handles_malformed_json(self, mod, output_dir, tmp_path):
        """Malformed files are skipped gracefully."""
        log_dir = tmp_path / "logs"
        (output_dir / "review-context.json").write_text("NOT JSON")
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        assert "context" not in events[-1]["snapshot"]

    def test_list_shaped_findings_file_extracts_empty_and_finalize_completes(
        self, mod, output_dir, tmp_path
    ):
        """A non-object review-findings.json (a list) must not crash finalize.

        Both extractors must route through critic_adjustments.read_findings_file
        so a non-object payload degrades to the extractor's own empty/default
        return instead of an uncaught AttributeError escaping finalize() and
        silently losing the whole pipeline_end event (manifest finalize,
        summary, durations) into pipeline.py's blanket except.
        """
        (output_dir / "review-findings.json").write_text(json.dumps([1, 2, 3]))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))
        t.start(pr_number="42")

        t.finalize(step=15, phase="OUTPUT", title="Present Results")

        events = _read_events(t.log_path)
        assert events[-1]["event"] == "pipeline_end"
        assert "findings" not in events[-1]["snapshot"]
        assert t._extract_findings() is None

    def test_string_shaped_agent_review_file_extracts_malformed_and_finalize_completes(
        self, mod, output_dir, tmp_path
    ):
        """A non-object <agent>-review.json (a string) must not crash finalize.

        See test_list_shaped_findings_file_extracts_empty_and_finalize_completes
        for the shared rationale — this is the sibling extractor.
        """
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        (output_dir / "security-review.json").write_text(json.dumps("oops"))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))
        t.start(pr_number="42")

        t.finalize(step=15, phase="OUTPUT", title="Present Results")

        events = _read_events(t.log_path)
        assert events[-1]["event"] == "pipeline_end"
        assert events[-1]["snapshot"]["agent_results"]["security"] == {
            "error": "malformed"
        }
        assert t._extract_agent_results()["security"] == {"error": "malformed"}


# ── Re-reviews ──────────────────────────────────────────────────────


class TestReReviews:
    """Multiple review runs for the same PR create separate log files."""

    @staticmethod
    def _mock_datetime_sequence(mod, times):
        """Return a patch that makes mod.datetime.now() cycle through `times`."""
        call_count = [0]
        real_datetime = datetime

        class FakeDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                idx = min(call_count[0], len(times) - 1)
                call_count[0] += 1
                return times[idx]

        return patch.object(mod, "datetime", FakeDatetime)

    def test_separate_files_per_run(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"

        # Two timestamps 2s apart (filename uses 1-second resolution).
        # start() calls datetime.now() once per invocation.
        t1_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t2_time = datetime(2026, 1, 1, 12, 0, 2, tzinfo=timezone.utc)

        with self._mock_datetime_sequence(mod, [t1_time, t2_time]):
            t1 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
            path1 = t1.start(pr_number="42")

            t2 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
            path2 = t2.start(pr_number="42")

        assert path1 != path2
        assert os.path.isfile(path1)
        assert os.path.isfile(path2)

    def test_glob_finds_all_runs(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"

        t1_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t2_time = datetime(2026, 1, 1, 12, 0, 2, tzinfo=timezone.utc)

        with self._mock_datetime_sequence(mod, [t1_time, t2_time]):
            t1 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
            t1.start(pr_number="42")

            t2 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
            t2.start(pr_number="42")

        pattern = str(log_dir / "pr-review-org-repo-42-run*--*.jsonl")
        matches = glob.glob(pattern)
        assert len(matches) == 2


# ── log_agent_start() ────────────────────────────────────────────


class TestLogAgentStart:
    """ReviewTelemetry.log_agent_start() appends agent lifecycle events."""

    def test_appends_agent_start_event(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_agent_start(agent_name="security-reviewer", domain="security")
        events = _read_events(telemetry.log_path)
        assert len(events) == 2
        assert events[1]["event"] == "agent_start"
        assert events[1]["agent"] == "security-reviewer"

    def test_includes_domain_and_model_tier(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_agent_start(
            agent_name="security-reviewer", domain="security",
            model_tier="sonnet", scope_files=3, scope_lines=150,
        )
        events = _read_events(telemetry.log_path)
        assert events[1]["domain"] == "security"
        assert events[1]["model_tier"] == "sonnet"

    def test_null_domain_is_canonicalized_to_empty_string(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(
            agent_name="tests-mutation-reviewer", domain=None
        )
        telemetry.log_step(step=6, phase="EXECUTION", title="Run Reviewers")

        events = _read_events(telemetry.log_path)
        start = next(event for event in events if event["event"] == "agent_start")
        assert start["domain"] == ""
        assert _read_manifest(telemetry)["agents"]["started"][0]["domain"] == ""

    def test_includes_scope(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_agent_start(
            agent_name="security-reviewer", domain="security",
            scope_files=3, scope_lines=150,
        )
        events = _read_events(telemetry.log_path)
        assert events[1]["scope"]["files"] == 3
        assert events[1]["scope"]["lines"] == 150

    def test_scope_paths_are_normalized_deduplicated_and_safely_relativized(
        self, mod, tmp_path
    ):
        repo = tmp_path / "repo"
        output = tmp_path / "output"
        repo.mkdir()
        output.mkdir()
        telemetry = mod.ReviewTelemetry(
            str(output), log_dir=str(tmp_path / "logs")
        )
        telemetry.start(run_id="run-1", repo_path=str(repo))

        telemetry.log_agent_start(
            agent_name="security-reviewer",
            scope_files=7,
            scope_lines=20,
            scope_paths=[
                "./src/a.py",
                "src//a.py",
                "tests\\test_a.py",
                str(repo / "src" / "absolute.py"),
                "src/../SENSITIVE_TRAVERSAL.py",
                str(tmp_path / "SENSITIVE_OUTSIDE.py"),
                "C:SENSITIVE_DRIVE_RELATIVE.py",
                r"C:\SENSITIVE_DRIVE_ABSOLUTE.py",
                {"nested": "SENSITIVE_DICT"},
                ["SENSITIVE_LIST"],
                42,
            ],
        )

        start_event = _read_events(telemetry.log_path)[1]
        assert start_event["scope"] == {
            "files": 7,
            "lines": 20,
            "paths": [
                "src/a.py",
                "src/absolute.py",
                "tests/test_a.py",
            ],
        }
        assert "SENSITIVE_" not in json.dumps(start_event)

    @pytest.mark.parametrize(
        "unsafe_path",
        [
            pytest.param("src/control\x7fname.py", id="unicode-control"),
            pytest.param("src/format\u202ename.py", id="unicode-format"),
        ],
    )
    def test_scope_paths_reject_unicode_control_and_format_characters(
        self, telemetry, unsafe_path
    ):
        telemetry.start(run_id="run-1")

        telemetry.log_agent_start(
            agent_name="security-reviewer",
            scope_paths=[unsafe_path, "src/caf\N{LATIN SMALL LETTER E WITH ACUTE}.py"],
        )

        start_event = _read_events(telemetry.log_path)[1]
        assert start_event["scope"]["paths"] == ["src/café.py"]

    def test_noop_without_start(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.log_agent_start(agent_name="security-reviewer", domain="security")
        assert t.log_path is None

    def test_multiple_agents(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_agent_start(agent_name="security-reviewer", domain="security")
        telemetry.log_agent_start(agent_name="performance-reviewer", domain="performance")
        events = _read_events(telemetry.log_path)
        agents = [e for e in events if e["event"] == "agent_start"]
        assert len(agents) == 2
        assert agents[0]["agent"] == "security-reviewer"
        assert agents[1]["agent"] == "performance-reviewer"

    def test_agent_start_includes_budget(self, telemetry):
        """agent_start event should include the budget_target field."""
        telemetry.start(pr_number="42")
        telemetry.log_agent_start(
            agent_name="security-reviewer",
            domain="security",
            model_tier="sonnet",
            scope_files=10,
            scope_lines=200,
            budget_target=35,
        )
        events = _read_events(telemetry.log_path)
        start_event = [e for e in events if e["event"] == "agent_start"][0]
        assert start_event["budget_target"] == 35

    def test_agent_start_omits_budget_when_none(self, telemetry):
        """agent_start event should omit budget_target when not provided."""
        telemetry.start(pr_number="42")
        telemetry.log_agent_start(
            agent_name="security-reviewer",
            domain="security",
        )
        events = _read_events(telemetry.log_path)
        start_event = [e for e in events if e["event"] == "agent_start"][0]
        assert "budget_target" not in start_event


# ── reviewer publication events ──────────────────────────────────


FINAL_DIGEST = "a" * 64


class TestLogAgentReviewDraftSaved:
    def test_records_digest_bound_draft_evidence(self, telemetry, mod):
        telemetry.start(run_id="run-1")

        telemetry.log_agent_review_draft_saved(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST
        )

        event = _read_events(telemetry.log_path)[-1]
        assert event == {
            "event": "agent_review_draft_saved",
            "timestamp": event["timestamp"],
            "agent": "security-reviewer",
            "review_digest": FINAL_DIGEST,
            "schema": mod.EVENT_SCHEMA,
            "run_id": "run-1",
        }


class TestLogAgentComplete:
    """ReviewTelemetry.log_agent_complete() appends completion events."""

    def test_appends_agent_complete_event(self, telemetry, output_dir):
        telemetry.start(pr_number="42")
        (output_dir / "security-reviewer.started").write_text(
            datetime.now(timezone.utc).isoformat()
        )
        time.sleep(0.05)
        telemetry.log_agent_complete(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST,
            verdict="comment",
            finding_count=2, severities={"high": 1, "medium": 1},
        )
        events = _read_events(telemetry.log_path)
        assert events[-1]["event"] == "agent_complete"
        assert events[-1]["agent"] == "security-reviewer"

    def test_includes_verdict_and_issues(self, telemetry, output_dir):
        telemetry.start(pr_number="42")
        (output_dir / "security-reviewer.started").write_text(
            datetime.now(timezone.utc).isoformat()
        )
        telemetry.log_agent_complete(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST,
            verdict="comment",
            finding_count=2, severities={"high": 1, "medium": 1},
        )
        events = _read_events(telemetry.log_path)
        assert events[-1]["verdict"] == "comment"
        assert events[-1]["finding_count"] == 2
        assert events[-1]["severities"] == {"high": 1, "medium": 1}

    def test_calculates_duration_from_started_file(self, telemetry, output_dir):
        telemetry.start(pr_number="42")
        (output_dir / "security-reviewer.started").write_text(
            datetime.now(timezone.utc).isoformat()
        )
        time.sleep(0.05)
        telemetry.log_agent_complete(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        events = _read_events(telemetry.log_path)
        assert events[-1]["duration_ms"] is not None
        assert events[-1]["duration_ms"] >= 40

    def test_duration_none_without_started_file(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_agent_complete(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        events = _read_events(telemetry.log_path)
        assert events[-1]["duration_ms"] is None

    def test_noop_without_start(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.log_agent_complete(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        assert t.log_path is None

    def test_completion_carries_finalized_review_digest(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_agent_complete(
            agent_name="security-reviewer", review_digest=FINAL_DIGEST,
            verdict="approve",
        )
        event = _read_events(telemetry.log_path)[-1]
        assert event["review_digest"] == FINAL_DIGEST

    def test_completion_uses_finding_count_without_retired_issue_count(
        self, telemetry
    ):
        """Renaming only review artifacts would leave lifecycle telemetry
        teaching and persisting the retired review-domain noun."""
        telemetry.start(run_id="run-1")

        telemetry.log_agent_complete(
            agent_name="security-reviewer",
            review_digest=FINAL_DIGEST,
            verdict="comment",
            finding_count=2,
            severities={"high": 1, "medium": 1},
        )

        event = _read_events(telemetry.log_path)[-1]
        assert event["finding_count"] == 2
        assert "issue_count" not in event


class TestReviewVocabularyManifestProjection:
    def test_manifest_projects_assignment_and_review_claims_separately(
        self, mod, output_dir, monkeypatch
    ):
        monkeypatch.setattr(
            mod.manifest_sections,
            "_load_final_review",
            lambda output_dir, agent: {
                "reviewed_file_claims": ["a.php"],
                "unclaimed_review_files": [],
                "review_claimable_files": ["a.php"],
            },
        )

        coverage = mod.manifest_sections.build_assignment_manifest(
            str(output_dir),
            [{
                "event": "agent_start",
                "agent": "security-reviewer",
                "scope": {"paths": ["a.php", "b.php"]},
            }],
            {"git": {"changed_files": ["a.php", "b.php"]}},
            str(output_dir),
            {
                "available": True,
                "duplicates": [],
                "plan": {"changed_files": ["a.php", "b.php"]},
                "index": {
                    "security-reviewer": {"status": "DISPATCH"},
                },
            },
            normalize_paths=lambda paths, **kwargs: list(paths),
        )

        assert coverage == {
            "changed_files": ["a.php", "b.php"],
            "reviewable_files": ["a.php", "b.php"],
            "assigned_files_by_agent": {
                "security-reviewer": ["a.php", "b.php"],
            },
            "assigned_files": ["a.php", "b.php"],
            "file_exclusions": [],
            "unassigned_reviewable_files": [],
            "reviewed_files_by_agent": {
                "security-reviewer": {
                    "reviewed_file_claim_count": 1,
                    "unclaimed_review_file_count": 0,
                },
            },
            "review_claimable_file_count_by_agent": {
                "security-reviewer": 1,
            },
            "semantics": "generated_scope_not_proof_of_model_read",
        }
        for retired in (
            "changed", "reviewable", "by_agent", "assigned", "excluded",
            "uncovered", "deferred_honesty_by_agent",
        ):
            assert retired not in coverage

    def test_finalized_summary_and_reconciliation_use_finding_vocabulary(
        self, mod, output_dir, tmp_path
    ):
        _write_dispatch_plan(output_dir, ["security-reviewer"])
        (output_dir / "security-review.json").write_text(json.dumps(
            canonical_review_document("security", ["medium"])
        ))
        reconciliation = {
            "input_finding_count": 3,
            "contributing_agent_count": 2,
            "grouped_concern_count": 2,
            "false_positive_concern_count": 1,
            "out_of_scope_concern_count": 0,
            "verified_concern_count": 1,
            "not_applicable_agents": [],
            "reviewing_agents": ["security-reviewer"],
            "dispatched_agents": ["security-reviewer"],
            "missing_agents": [],
        }
        ledger = canonical_findings_ledger(
            ["medium"], reconciliation=reconciliation
        )
        (output_dir / "review-findings.json").write_text(json.dumps(ledger))
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )
        telemetry.start(run_id="run-1")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        summary = manifest["outcome"]["summary"]
        assert summary["total_agent_findings"] == 1
        assert summary["final_finding_count"] == 1
        assert manifest["outcome"]["reconciliation"] == reconciliation
        serialized = json.dumps(manifest)
        for retired in (
            "total_agent_issues", "final_issues", "total_issues",
            "input_findings_count", "agents_contributing",
            "concerns_after_grouping", "false_positives_dropped",
            "out_of_scope_dropped", "verified_concerns", "merge_ratio",
            "not_applicable_count", "false_positive_finding_count",
            "out_of_scope_finding_count", "verified_finding_count",
            "deduplication_ratio", "not_applicable_agent_count",
        ):
            assert retired not in serialized

    def test_missing_reconciliation_is_null_not_an_empty_measurement(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")

        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert _read_manifest(telemetry)["outcome"]["reconciliation"] is None


# ── _build_summary override counting ────────────────────────────


class TestSummaryOverrideCounting:
    """_build_summary must count DISPATCH_OVERRIDE as dispatched, not skipped."""

    def test_dispatch_override_counted_as_dispatched(self, mod, output_dir, tmp_path):
        plan = {
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH"},
                {"name": "perf-reviewer", "status": "DISPATCH_OVERRIDE"},
                {"name": "a11y-reviewer", "status": "SKIPPED"},
                {"name": "concurrency-reviewer", "status": "SKIPPED_OVERRIDE"},
            ]
        }
        (output_dir / "dispatch-plan.json").write_text(json.dumps(plan))

        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        summary = t._build_summary(total_duration_ms=10000)

        assert summary["agents_total"] == 4
        assert summary["agents_dispatched"] == 2, \
            "DISPATCH_OVERRIDE should count as dispatched"
        assert summary["agents_skipped"] == 2, \
            "SKIPPED_OVERRIDE should count as skipped, DISPATCH_OVERRIDE should not"

    def test_all_explicit_skipped_statuses_are_counted(
        self, mod, output_dir, tmp_path
    ):
        plan = {
            "agents": [
                {"name": "code-reviewer", "status": "SKIPPED"},
                {"name": "perf-reviewer", "status": "SKIPPED_OVERRIDE"},
                {"name": "a11y-reviewer", "status": "SKIPPED_QUICK_MODE"},
                {"name": "security-reviewer", "status": "SKIPPED_TRIAGE"},
            ],
        }
        (output_dir / "dispatch-plan.json").write_text(json.dumps(plan))
        telemetry = mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "logs")
        )

        summary = telemetry._build_summary(total_duration_ms=10000)

        assert summary["agents_total"] == 4
        assert summary["agents_dispatched"] == 0
        assert summary["agents_skipped"] == 4


# ── Quick mode + decisions telemetry ──────────────────────────────


class TestQuickModeTelemetry:
    """Quick mode flag and decisions captured in telemetry."""

    def test_start_captures_quick_mode(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42", quick_mode=True)
        events = _read_events(t.log_path)
        start = events[0]
        assert start["pipeline"]["quick_mode"] is True

    def test_start_defaults_quick_mode_false(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        events = _read_events(t.log_path)
        start = events[0]
        assert start["pipeline"]["quick_mode"] is False

    def test_log_step_captures_decisions(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        decisions = {"critic_skipped": True, "reason": "quick mode + verdict: comment"}
        t.log_step(step=10, phase="VALIDATION", title="Decision Critic",
                   decisions=decisions)
        events = _read_events(t.log_path)
        step_event = events[1]
        assert step_event["decisions"] == decisions

    def test_log_step_no_decisions_by_default(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan")
        events = _read_events(t.log_path)
        step_event = events[1]
        assert "decisions" not in step_event

    def test_summary_includes_quick_mode(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42", quick_mode=True)
        t.finalize(step=11, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        final = events[-1]
        assert final["summary"]["quick_mode"] is True

    def test_summary_quick_mode_false_by_default(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=11, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        final = events[-1]
        assert final["summary"]["quick_mode"] is False

    def test_summary_quick_mode_cross_process(self, mod, tmp_path):
        """Separate ReviewTelemetry instance (simulating different process)
        should still read quick_mode from the JSONL start event."""
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"
        # Process 1: start() records quick_mode=True
        t1 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t1.start(pr_number="42", quick_mode=True)
        # Process 2: new instance, never called start()
        t2 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t2.finalize(step=11, phase="OUTPUT", title="Present Results")
        events = _read_events(t2.log_path)
        final = events[-1]
        assert final["summary"]["quick_mode"] is True


class TestReviewerMarkdownManifest:
    """The manifest records the sanitized reviewer-Markdown outcome."""

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        telemetry = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        telemetry.start(
            mode="full",
            repo_path=str(tmp_path),
            identifier="branch",
            run_id="run-1",
        )
        return telemetry, out_dir

    def test_absent_state_is_recorded_as_unavailable(self, mod, tmp_path):
        telemetry, _out_dir = self._telemetry(mod, tmp_path)

        manifest = json.loads(Path(telemetry.manifest_path).read_text())

        assert manifest["reviewer_markdown"] is None

    def test_state_outcome_is_sanitized_into_manifest(self, mod, tmp_path):
        telemetry, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "reviewer_markdown": {
                "ran": True,
                "written": 2,
                "expected": 3,
                "status": "partial",
                "ignored": "do not persist",
            },
        }))

        telemetry.log_step(step=8, phase="SYNTHESIS", title="Reconcile")
        manifest = json.loads(Path(telemetry.manifest_path).read_text())

        assert manifest["reviewer_markdown"] == {
            "ran": True,
            "written": 2,
            "expected": 3,
            "status": "partial",
        }

    def test_partial_outcome_allows_equal_counts_when_path_identities_differ(
        self, mod, tmp_path
    ):
        telemetry, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "reviewer_markdown": {
                "ran": True,
                "written": 1,
                "expected": 1,
                "status": "partial",
            },
        }))

        telemetry.log_step(step=8, phase="SYNTHESIS", title="Reconcile")
        manifest = json.loads(Path(telemetry.manifest_path).read_text())

        assert manifest["reviewer_markdown"] == {
            "ran": True,
            "written": 1,
            "expected": 1,
            "status": "partial",
        }

    def test_malformed_state_outcome_is_unavailable(self, mod, tmp_path):
        telemetry, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "reviewer_markdown": {
                "ran": "yes",
                "written": True,
                "expected": -1,
                "status": "complete",
            },
        }))

        telemetry.log_step(step=8, phase="SYNTHESIS", title="Reconcile")
        manifest = json.loads(Path(telemetry.manifest_path).read_text())

        assert manifest["reviewer_markdown"] is None

    def test_availability_flag_tracks_the_payload(self, mod, tmp_path):
        """Task 13: `availability["reviewer_markdown"]` used to not exist
        at all — the section was written with no flag beside it. It now
        shares the section's own top-level key, derived from whether the
        section actually parsed, the same rule every other optional
        section in `OPTIONAL_SECTION_AVAILABILITY_KEYS` follows."""
        telemetry, out_dir = self._telemetry(mod, tmp_path)

        absent_manifest = json.loads(Path(telemetry.manifest_path).read_text())
        assert absent_manifest["availability"]["reviewer_markdown"] is False

        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "reviewer_markdown": {
                "ran": True,
                "written": 1,
                "expected": 1,
                "status": "complete",
            },
        }))
        telemetry.log_step(step=8, phase="SYNTHESIS", title="Reconcile")
        measured_manifest = json.loads(Path(telemetry.manifest_path).read_text())
        assert measured_manifest["availability"]["reviewer_markdown"] is True


class TestFindingsMarkdownManifest:
    """The manifest records the sanitized findings-Markdown outcome —
    `reviewer_markdown`'s sibling family (steps 9 and 11's render of
    `review-findings.md`, versus step 8's per-reviewer render), new in
    Task 13. `build_findings_markdown_manifest` shares its validator with
    `build_reviewer_markdown_manifest`, so this class mirrors
    `TestReviewerMarkdownManifest` field for field, reading
    `state["findings_markdown"]` instead."""

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        telemetry = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        telemetry.start(
            mode="full",
            repo_path=str(tmp_path),
            identifier="branch",
            run_id="run-1",
        )
        return telemetry, out_dir

    def test_absent_state_is_recorded_as_unavailable(self, mod, tmp_path):
        telemetry, _out_dir = self._telemetry(mod, tmp_path)

        manifest = json.loads(Path(telemetry.manifest_path).read_text())

        assert manifest["findings_markdown"] is None
        assert manifest["availability"]["findings_markdown"] is False

    def test_state_outcome_is_sanitized_into_manifest(self, mod, tmp_path):
        telemetry, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "findings_markdown": {
                "ran": True,
                "written": 1,
                "expected": 1,
                "status": "complete",
                "ignored": "do not persist",
            },
        }))

        telemetry.log_step(step=9, phase="VALIDATION", title="Render Findings")
        manifest = json.loads(Path(telemetry.manifest_path).read_text())

        assert manifest["findings_markdown"] == {
            "ran": True,
            "written": 1,
            "expected": 1,
            "status": "complete",
        }
        assert manifest["availability"]["findings_markdown"] is True

    def test_a_failed_render_is_recorded_not_dropped(self, mod, tmp_path):
        telemetry, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "findings_markdown": {
                "ran": True,
                "written": 0,
                "expected": 1,
                "status": "failed",
            },
        }))

        telemetry.log_step(step=9, phase="VALIDATION", title="Render Findings")
        manifest = json.loads(Path(telemetry.manifest_path).read_text())

        assert manifest["findings_markdown"] == {
            "ran": True,
            "written": 0,
            "expected": 1,
            "status": "failed",
        }
        assert manifest["availability"]["findings_markdown"] is True

    def test_malformed_state_outcome_is_unavailable(self, mod, tmp_path):
        telemetry, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "findings_markdown": {
                "ran": "yes",
                "written": True,
                "expected": -1,
                "status": "complete",
            },
        }))

        telemetry.log_step(step=9, phase="VALIDATION", title="Render Findings")
        manifest = json.loads(Path(telemetry.manifest_path).read_text())

        assert manifest["findings_markdown"] is None
        assert manifest["availability"]["findings_markdown"] is False


class TestDependencyRefreshManifest:
    """The manifest records the sanitized dependency-refresh report."""

    def _telemetry(self, mod, tmp_path):
        subprocess.run(
            ["git", "init", str(tmp_path)], check=True, capture_output=True
        )
        (tmp_path / "tracked.txt").write_text("initial\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "tracked.txt"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git", "-C", str(tmp_path),
                "-c", "user.name=Dependency Refresh Test",
                "-c", "user.email=dependency-refresh@example.com",
                "commit", "-m", "Initial commit",
            ],
            check=True,
            capture_output=True,
        )
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        t.start(mode="full", repo_path=str(tmp_path), identifier="branch",
                run_id="run-1")
        return t, out_dir

    @staticmethod
    def _save_report(out_dir, *, status="completed", commands=None):
        if commands is None:
            commands = [{
                "directory": ".",
                "command": "custom sync --locked",
                "exit_status": "ok",
            }]
        request = out_dir.parent / "dependency-refresh-request.json"
        request.write_text(json.dumps({
            "schema": 1,
            "status": status,
            "commands": commands,
        }))
        assert dependency_refresh.save_report(
            out_dir, request, out_dir.parent
        ) == []

    def test_absent_when_never_requested_and_no_report(self, mod, tmp_path):
        t, out_dir = self._telemetry(mod, tmp_path)
        manifest = json.loads(Path(t.manifest_path).read_text())
        assert manifest["dependency_refresh"] is None

    def test_requested_without_report_is_recorded(self, mod, tmp_path):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        t.log_step(step=3, phase="SETUP", title="Gather Context")
        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section["requested"] is True
        assert section["reported"] is False
        assert "status" not in section

    def test_saved_report_is_projected_into_the_manifest(self, mod, tmp_path):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        self._save_report(out_dir)
        t.log_step(step=3, phase="SETUP", title="Gather Context")
        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section == {
            "requested": True,
            "reported": True,
            "status": "completed",
            "tracked_files_dirty": False,
            "dirty_files": [],
            "commands": [
                {"directory": ".", "command": "custom sync --locked",
                 "exit_status": "ok"},
            ],
        }

    def test_dirty_precheck_refusal_is_projected_without_a_report(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "dependency_refresh_precheck": {
                "tracked_files_dirty": True,
                "dirty_files": ["tracked.txt"],
            },
        }))

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        assert manifest["dependency_refresh"] == {
            "requested": True,
            "reported": False,
            "precheck": {
                "tracked_files_dirty": True,
                "dirty_files": ["tracked.txt"],
            },
        }

    def test_unknown_precheck_refusal_is_projected_without_a_report(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "dependency_refresh_precheck": {
                "tracked_files_dirty": None,
                "dirty_files": [],
            },
        }))

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section == {
            "requested": True,
            "reported": False,
            "precheck": {
                "tracked_files_dirty": None,
                "dirty_files": [],
            },
        }

    def test_malformed_canonical_report_is_unreported_without_replacement(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "dependency-refresh.json").write_text(json.dumps({
            "status": [],
            "commands": [],
            "tracked_files_dirty": False,
        }))

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        assert manifest["dependency_refresh"] == {
            "requested": True,
            "reported": False,
        }
        assert manifest["steps"][-1]["step"] == 5

    def test_saved_report_projects_final_dirty_files(self, mod, tmp_path):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")
        self._save_report(out_dir, status="failed", commands=[{
            "directory": ".",
            "command": "custom sync",
            "exit_status": "failed",
        }])

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section["status"] == "failed"
        assert section["tracked_files_dirty"] is True
        assert section["dirty_files"] == ["tracked.txt"]

    def test_clean_precheck_is_not_repeated_in_the_manifest(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "dependency_refresh_precheck": {
                "tracked_files_dirty": False,
                "dirty_files": [],
            },
        }))

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section == {"requested": True, "reported": False}

    @pytest.mark.parametrize(
        "report_bytes",
        [
            b"{not-json",
            json.dumps({
                "commands": [],
                "padding": "x" * (1024 * 1024),
            }).encode("utf-8"),
            (
                b'{"commands":[],"value":'
                + (b"[" * 200000)
                + b"0"
                + (b"]" * 200000)
                + b"}"
            ),
            b"\xff",
        ],
        ids=("malformed", "oversized", "deeply-nested", "invalid-utf8"),
    )
    def test_hostile_canonical_report_reads_as_unreported(
        self, mod, tmp_path, report_bytes
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "dependency-refresh.json").write_bytes(report_bytes)

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section == {"requested": True, "reported": False}

    def test_invalid_report_values_read_as_unreported(self, mod, tmp_path):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "dependency-refresh.json").write_text(json.dumps({
            "status": "did-things",
            "commands": [
                "rm -rf /",                       # not a dict — dropped
                {"directory": 42, "command": ["x"], "exit_status": "great"},
            ],
            "tracked_files_dirty": "yes",
        }))
        t.log_step(step=3, phase="SETUP", title="Gather Context")
        manifest = json.loads(Path(t.manifest_path).read_text())
        assert manifest["dependency_refresh"] is None

    def test_non_object_report_reads_as_unreported(self, mod, tmp_path):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "dependency-refresh.json").write_text("[1, 2, 3]")
        t.log_step(step=3, phase="SETUP", title="Gather Context")
        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section["requested"] is True
        assert section["reported"] is False

    def test_availability_flag_tracks_the_payload(self, mod, tmp_path):
        """Task 13: `availability["dependency_refresh"]` used to not
        exist at all — the section was written with no flag beside it.
        It now shares the section's own top-level key, derived from
        whether the section actually parsed, the same rule every other
        optional section in `OPTIONAL_SECTION_AVAILABILITY_KEYS`
        follows."""
        t, out_dir = self._telemetry(mod, tmp_path)

        absent_manifest = json.loads(Path(t.manifest_path).read_text())
        assert absent_manifest["availability"]["dependency_refresh"] is False

        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        t.log_step(step=3, phase="SETUP", title="Gather Context")
        requested_manifest = json.loads(Path(t.manifest_path).read_text())
        assert requested_manifest["availability"]["dependency_refresh"] is True


class TestWorktreeHygieneManifest:
    """The manifest records the step-11 worktree-hygiene measurement."""

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        t.start(mode="full", repo_path=str(tmp_path), identifier="branch",
                run_id="run-1")
        return t, out_dir

    def test_absent_artifact_yields_none(self, mod, tmp_path):
        build = mod.manifest_sections.build_worktree_hygiene_manifest
        assert build(str(tmp_path)) is None

    def test_artifact_projected_into_manifest(self, mod, tmp_path):
        (tmp_path / "worktree-hygiene.json").write_text(json.dumps({
            "schema": 1,
            "status": "clean",
            "new_files": [],
            "changed_files": [],
            "probe_residue_removed": ["zz_pirategoat-probe.go"],
            "baseline_captured_at": "2026-08-19T10:00:00+00:00",
        }))

        section = mod.manifest_sections.build_worktree_hygiene_manifest(
            str(tmp_path)
        )

        assert section["status"] == "clean"
        assert section["probe_residue_removed"] == ["zz_pirategoat-probe.go"]
        assert section["baseline_captured_at"] == "2026-08-19T10:00:00+00:00"

    def test_malformed_artifact_yields_none(self, mod, tmp_path):
        (tmp_path / "worktree-hygiene.json").write_text("[]")
        build = mod.manifest_sections.build_worktree_hygiene_manifest
        assert build(str(tmp_path)) is None

    def test_missing_fields_project_safely(self, mod, tmp_path):
        (tmp_path / "worktree-hygiene.json").write_text(json.dumps(
            {"schema": 1}
        ))

        section = mod.manifest_sections.build_worktree_hygiene_manifest(
            str(tmp_path)
        )

        assert section["status"] == "unknown"
        assert section["new_files"] == []
        assert section["changed_files"] == []
        assert section["probe_residue_removed"] == []
        assert section["baseline_captured_at"] is None

    def test_non_string_entries_are_dropped(self, mod, tmp_path):
        (tmp_path / "worktree-hygiene.json").write_text(json.dumps({
            "schema": 1,
            "status": 7,
            "new_files": ["?? a.txt", 3, None],
            "changed_files": " M b.txt",
            "probe_residue_removed": [{"path": "x"}],
            "baseline_captured_at": 1234,
        }))

        section = mod.manifest_sections.build_worktree_hygiene_manifest(
            str(tmp_path)
        )

        assert section["status"] == "unknown"
        assert section["new_files"] == ["?? a.txt"]
        assert section["changed_files"] == []
        assert section["probe_residue_removed"] == []
        assert section["baseline_captured_at"] is None

    def test_unrecognized_status_degrades_to_unknown(self, mod, tmp_path):
        """A well-typed status outside the allowlist reads "unknown"."""
        (tmp_path / "worktree-hygiene.json").write_text(json.dumps(
            {"schema": 1, "status": "corrupted"}
        ))

        section = mod.manifest_sections.build_worktree_hygiene_manifest(
            str(tmp_path)
        )

        assert section["status"] == "unknown"

    def test_measured_unknown_is_not_absent(self, mod, tmp_path):
        """A measured "unknown" is a section; only an absent artifact is None."""
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "worktree-hygiene.json").write_text(json.dumps({
            "schema": 1,
            "status": "unknown",
            "new_files": [],
            "changed_files": [],
            "probe_residue_removed": [],
            "baseline_captured_at": None,
        }))

        t.log_step(step=11, phase="OUTPUT", title="Present Results")
        manifest = _read_manifest(t)

        assert manifest["worktree_hygiene"]["status"] == "unknown"
        assert manifest["availability"]["worktree_hygiene"] is True

    def test_manifest_wires_the_section_and_availability_flag(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "worktree-hygiene.json").write_text(json.dumps({
            "schema": 1,
            "status": "changed_during_review",
            "new_files": ["?? notes.md"],
            "changed_files": [" M src/app.py"],
            "probe_residue_removed": ["zz_pirategoat-probe.go"],
            "baseline_captured_at": "2026-08-19T10:00:00+00:00",
        }))

        t.log_step(step=11, phase="OUTPUT", title="Present Results")
        manifest = _read_manifest(t)

        assert manifest["worktree_hygiene"] == {
            "status": "changed_during_review",
            "new_files": ["?? notes.md"],
            "changed_files": [" M src/app.py"],
            "probe_residue_removed": ["zz_pirategoat-probe.go"],
            "baseline_captured_at": "2026-08-19T10:00:00+00:00",
        }
        assert manifest["availability"]["worktree_hygiene"] is True

    def test_absent_artifact_is_recorded_as_unavailable(self, mod, tmp_path):
        t, _out_dir = self._telemetry(mod, tmp_path)

        manifest = _read_manifest(t)

        assert manifest["worktree_hygiene"] is None
        assert manifest["availability"]["worktree_hygiene"] is False


class TestUsageManifest:
    """The manifest records the step-11 token-usage snapshot.

    The snapshot has two halves with independent warrants: subagent
    transcripts are closed at capture time and can read "complete", while
    the orchestrator is measuring its own still-open session. The
    projection must preserve that split rather than flattening it into one
    "usage was measured" bit.
    """

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        t.start(mode="full", repo_path=str(tmp_path), identifier="branch",
                run_id="run-1")
        return t, out_dir

    def _usage(self, output=7):
        return {
            "input_tokens": 1,
            "cache_creation_input_tokens": 2,
            "cache_read_input_tokens": 3,
            "effective_input_tokens": 6,
            "output_tokens": output,
        }

    def _snapshot(self, **overrides):
        snapshot = {
            "schema": 1,
            "captured_at": "2026-08-19T10:43:00+00:00",
            "window": {"started_at": "2026-08-19T10:00:00+00:00",
                       "ended_at": "2026-08-19T10:43:00+00:00",
                       "closed": False},
            "availability": {"subagents": "complete",
                             "orchestrator": "partial"},
            "reason": None,
            "agents_measured": {"measured": 2, "expected": 2},
            "subagent_usage": [
                {"agent": "code-reviewer", "model": "claude-opus-5[1m]",
                 "usage": self._usage(output=5)},
                {"agent": "security-reviewer", "model": "claude-sonnet-5",
                 "usage": self._usage(output=2)},
            ],
            "subagent_totals": self._usage(),
            "usage_by_model": {"claude-opus-5[1m]": self._usage(output=5),
                               "claude-sonnet-5": self._usage(output=2)},
            "orchestrator_usage": self._usage(output=9),
        }
        snapshot.update(overrides)
        return snapshot

    def _write(self, output_dir, snapshot):
        (Path(output_dir) / "usage-snapshot.json").write_text(
            json.dumps(snapshot)
        )

    def test_absent_artifact_yields_none(self, mod, tmp_path):
        build = mod.manifest_sections.build_usage_manifest
        assert build(str(tmp_path)) is None

    def test_malformed_artifact_yields_none(self, mod, tmp_path):
        (tmp_path / "usage-snapshot.json").write_text("[]")
        build = mod.manifest_sections.build_usage_manifest
        assert build(str(tmp_path)) is None

    def test_artifact_projected_into_a_section(self, mod, tmp_path):
        self._write(tmp_path, self._snapshot())

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["captured_at"] == "2026-08-19T10:43:00+00:00"
        assert section["window"] == {
            "started_at": "2026-08-19T10:00:00+00:00",
            "ended_at": "2026-08-19T10:43:00+00:00",
            "closed": False,
        }
        assert section["availability"] == {
            "subagents": "complete", "orchestrator": "partial",
        }
        assert section["agents_measured"] == {"measured": 2, "expected": 2}
        assert section["subagent_totals"]["output_tokens"] == 7
        assert section["orchestrator_usage"]["output_tokens"] == 9
        assert section["usage_by_model"]["claude-opus-5[1m]"][
            "output_tokens"] == 5
        assert section["by_agent"] == [
            {"agent": "code-reviewer", "model": "claude-opus-5[1m]",
             "usage": self._usage(output=5)},
            {"agent": "security-reviewer", "model": "claude-sonnet-5",
             "usage": self._usage(output=2)},
        ]

    def test_unknown_schema_yields_none(self, mod, tmp_path):
        """A snapshot announcing a schema this builder does not know was
        written by a producer whose field meanings it cannot vouch for."""
        self._write(tmp_path, self._snapshot(schema=2))

        build = mod.manifest_sections.build_usage_manifest
        assert build(str(tmp_path)) is None

    def test_missing_schema_yields_none(self, mod, tmp_path):
        snapshot = self._snapshot()
        del snapshot["schema"]
        self._write(tmp_path, snapshot)

        build = mod.manifest_sections.build_usage_manifest
        assert build(str(tmp_path)) is None

    def test_a_closed_window_is_projected_as_closed(self, mod, tmp_path):
        """The flag is what separates "partial because the run was still
        open" from "partial because the evidence was damaged"."""
        self._write(tmp_path, self._snapshot(
            window={"started_at": "2026-08-19T10:00:00+00:00",
                    "ended_at": "2026-08-19T10:43:05+00:00",
                    "closed": True},
        ))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["window"]["closed"] is True

    @pytest.mark.parametrize(
        "window",
        [{}, {"closed": "yes"}, {"closed": 1}, "not-an-object", None],
        ids=["absent", "string", "int", "scalar", "null"],
    )
    def test_unreadable_window_falls_to_substituted(self, mod, tmp_path,
                                                    window):
        """"closed" is the stronger claim, so an unreadable flag must fall
        to the weaker one rather than license the stronger."""
        self._write(tmp_path, self._snapshot(window=window))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["window"] == {
            "started_at": None, "ended_at": None, "closed": False,
        }

    def test_measured_missing_is_not_absent(self, mod, tmp_path):
        """A run that tried and found no transcripts is a section, not None.

        Only a run that never attempted the capture has no artifact — the
        same distinction hygiene draws between a measured "unknown" and an
        absent measurement.
        """
        self._write(tmp_path, self._snapshot(
            availability={"subagents": "missing", "orchestrator": "missing"},
            reason="missing_session_id",
            agents_measured={"measured": 0, "expected": None},
            subagent_usage=[],
            subagent_totals=None,
            usage_by_model=None,
            orchestrator_usage=None,
        ))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section is not None
        assert section["availability"] == {
            "subagents": "missing", "orchestrator": "missing",
        }
        assert section["subagent_totals"] is None
        assert section["orchestrator_usage"] is None
        assert section["usage_by_model"] == {}
        assert section["by_agent"] == []
        assert section["agents_measured"] == {"measured": 0, "expected": None}

    def test_unrecognized_availability_degrades_to_missing(self, mod, tmp_path):
        """A well-typed label outside the vocabulary is not a measurement."""
        self._write(tmp_path, self._snapshot(
            availability={"subagents": "excellent", "orchestrator": 7},
        ))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["availability"] == {
            "subagents": "missing", "orchestrator": "missing",
        }

    def test_damaged_usage_maps_are_dropped_not_zeroed(self, mod, tmp_path):
        """A partially typed usage map is unusable evidence, not a zero."""
        self._write(tmp_path, self._snapshot(
            subagent_totals={"output_tokens": "lots"},
            orchestrator_usage={"output_tokens": 4},
            # JSON keys are always strings, so only the value side of
            # a model bucket can be damaged.
            usage_by_model={"claude-sonnet-5": None},
            subagent_usage=[
                {"agent": "code-reviewer", "model": 5, "usage": self._usage()},
                {"agent": 7, "model": "x", "usage": self._usage()},
                "not-a-row",
            ],
        ))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["subagent_totals"] is None
        assert section["orchestrator_usage"] is None
        assert section["usage_by_model"] == {}
        assert section["by_agent"] == [
            {"agent": "code-reviewer", "model": None, "usage": self._usage()},
        ]

    def test_non_integer_agent_counts_are_dropped(self, mod, tmp_path):
        self._write(tmp_path, self._snapshot(
            agents_measured={"measured": True, "expected": -1},
        ))

        section = mod.manifest_sections.build_usage_manifest(str(tmp_path))

        assert section["agents_measured"] == {
            "measured": None, "expected": None,
        }

    def test_manifest_wires_the_section_and_availability_flag(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        self._write(out_dir, self._snapshot())

        t.finalize(step=11, phase="OUTPUT", title="Present Results")
        manifest = _read_manifest(t)

        assert manifest["usage"]["availability"]["subagents"] == "complete"
        assert manifest["availability"]["usage"] is True

    def test_absent_artifact_is_recorded_as_unavailable(self, mod, tmp_path):
        t, _out_dir = self._telemetry(mod, tmp_path)

        manifest = _read_manifest(t)

        assert manifest["usage"] is None
        assert manifest["availability"]["usage"] is False


class TestSkippedStepsManifest:
    """The manifest records the step-skip decisions the router made."""

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        t.start(mode="full", repo_path=str(tmp_path), identifier="branch",
                run_id="run-1")
        return t, out_dir

    def test_absent_state_yields_none(self, mod, tmp_path):
        build = mod.manifest_sections.build_skipped_steps_manifest
        assert build(str(tmp_path)) is None

    def test_recorded_skips_projected(self, mod, tmp_path):
        (tmp_path / "pipeline-state.json").write_text(json.dumps({
            "skipped_steps": [
                {"step": 2, "title": "Repo Setup",
                 "condition": "needs_workspace_setup"},
            ],
        }))

        section = mod.manifest_sections.build_skipped_steps_manifest(
            str(tmp_path)
        )

        assert section == [{"step": 2, "title": "Repo Setup",
                            "condition": "needs_workspace_setup"}]

    def test_state_without_the_key_yields_none(self, mod, tmp_path):
        (tmp_path / "pipeline-state.json").write_text(json.dumps({}))
        build = mod.manifest_sections.build_skipped_steps_manifest
        assert build(str(tmp_path)) is None

    def test_empty_list_is_a_measured_zero(self, mod, tmp_path):
        (tmp_path / "pipeline-state.json").write_text(json.dumps({
            "skipped_steps": [],
        }))
        build = mod.manifest_sections.build_skipped_steps_manifest
        assert build(str(tmp_path)) == []

    def test_malformed_state_yields_none(self, mod, tmp_path):
        (tmp_path / "pipeline-state.json").write_text("[]")
        build = mod.manifest_sections.build_skipped_steps_manifest
        assert build(str(tmp_path)) is None

    def test_non_list_value_yields_none(self, mod, tmp_path):
        (tmp_path / "pipeline-state.json").write_text(json.dumps({
            "skipped_steps": {"step": 2},
        }))
        build = mod.manifest_sections.build_skipped_steps_manifest
        assert build(str(tmp_path)) is None

    def test_unusable_entries_are_dropped_and_fields_default(
        self, mod, tmp_path
    ):
        """Only step-identified records survive; absent prose reads empty."""
        (tmp_path / "pipeline-state.json").write_text(json.dumps({
            "skipped_steps": [
                {"step": 4},
                {"step": "12", "title": "Cleanup"},
                "step 2",
                {"title": "Repo Setup"},
                {"step": True, "title": "Bool Is Not A Step"},
            ],
        }))

        section = mod.manifest_sections.build_skipped_steps_manifest(
            str(tmp_path)
        )

        assert section == [{"step": 4, "title": "", "condition": ""}]

    def test_manifest_wires_the_section_and_availability_flag(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "skipped_steps": [
                {"step": 2, "title": "Repo Setup",
                 "condition": "needs_workspace_setup"},
                {"step": 12, "title": "Cleanup",
                 "condition": "has_workspace_state_interactive"},
            ],
        }))

        t.log_step(step=11, phase="OUTPUT", title="Present Results")
        manifest = _read_manifest(t)

        assert manifest["skipped_steps"] == [
            {"step": 2, "title": "Repo Setup",
             "condition": "needs_workspace_setup"},
            {"step": 12, "title": "Cleanup",
             "condition": "has_workspace_state_interactive"},
        ]
        assert manifest["availability"]["skipped_steps"] is True

    def test_measured_zero_skips_is_available(self, mod, tmp_path):
        """[] is a measured result, not an absent measurement."""
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "pipeline-state.json").write_text(json.dumps({
            "skipped_steps": [],
        }))

        t.log_step(step=11, phase="OUTPUT", title="Present Results")
        manifest = _read_manifest(t)

        assert manifest["skipped_steps"] == []
        assert manifest["availability"]["skipped_steps"] is True

    def test_absent_state_is_recorded_as_unavailable(self, mod, tmp_path):
        t, _out_dir = self._telemetry(mod, tmp_path)

        manifest = _read_manifest(t)

        assert manifest["skipped_steps"] is None
        assert manifest["availability"]["skipped_steps"] is False


class TestSynthesisAgentsManifest:
    """The manifest records the reconciliator/critic lifecycle.

    A family of its own, never folded into `manifest["agents"]`: those two
    agents are never in a dispatch plan and produce no reviewer lifecycle
    events, so mixing them in would corrupt every reviewer count
    downstream. The three outcomes this projection keeps apart are `None`
    (never measured — every run predating the feature), a measured empty
    list (finalize looked and found no dispatch markers), and the rows.
    """

    RECONCILIATOR = lifecycle_contract.RECONCILIATOR
    CRITIC = lifecycle_contract.DECISION_CRITIC

    def _row(self, agent, **overrides):
        row = {
            "agent": agent,
            "verdict": (
                "request_changes" if agent == self.RECONCILIATOR else "STAND"
            ),
            "started_at": "2026-08-19T12:00:00+00:00",
            "completed_at": "2026-08-19T12:11:05+00:00",
            "duration_ms": 665_000,
            "stalled": False,
        }
        row.update(overrides)
        return row

    def _write(self, tmp_path, payload):
        (tmp_path / "synthesis-agents.json").write_text(json.dumps(payload))

    def _artifact(self, *rows, **overrides):
        payload = {
            "schema": 1,
            "finalized": True,
            "agents": list(rows),
        }
        payload.update(overrides)
        return payload

    def _build(self, mod, tmp_path):
        return mod.manifest_sections.build_synthesis_agents_manifest(
            str(tmp_path)
        )

    def test_absent_artifact_is_unmeasured(self, mod, tmp_path):
        assert self._build(mod, tmp_path) is None

    def test_unknown_schema_is_unmeasured(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(
            self._row(self.CRITIC), schema=2,
        ))
        assert self._build(mod, tmp_path) is None

    def test_boolean_schema_is_unmeasured(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(
            self._row(self.CRITIC), schema=True,
        ))
        assert self._build(mod, tmp_path) is None

    def test_measured_empty_is_not_absent(self, mod, tmp_path):
        """Finalize ran and found no dispatch markers. That is a measured
        zero dispatches, not an unmeasured run."""
        self._write(tmp_path, self._artifact())
        assert self._build(mod, tmp_path) == {
            "finalized": True,
            "agents": [],
        }

    def test_durations_project_intact(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(self._row(self.CRITIC)))
        section = self._build(mod, tmp_path)
        assert section["agents"] == [self._row(self.CRITIC)]

    def test_stall_projects_as_stalled_without_a_duration(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(self._row(
            self.RECONCILIATOR, completed_at=None, duration_ms=None,
            stalled=True,
        )))
        row = self._build(mod, tmp_path)["agents"][0]
        assert row["stalled"] is True
        assert row["duration_ms"] is None

    @pytest.mark.parametrize(
        "value", [None, "yes", 1, 0, "true"],
        ids=["null", "string", "int", "zero", "truthy-string"],
    )
    def test_only_an_explicit_true_reads_as_stalled(self, mod, tmp_path, value):
        """A stall accuses the run. An unreadable flag does not license
        that claim — same rule usage's `window.closed` follows."""
        self._write(tmp_path, self._artifact(
            self._row(self.CRITIC, stalled=value)
        ))
        assert self._build(mod, tmp_path)["agents"][0]["stalled"] is False

    @pytest.mark.parametrize(
        "value", [-1, "665000", 6.5, True, None],
        ids=["negative", "string", "float", "bool", "null"],
    )
    def test_unusable_duration_is_none_never_zero(self, mod, tmp_path, value):
        """A duration that cannot be read is absent. Zeroing it would
        publish "the phase finished instantly"."""
        self._write(tmp_path, self._artifact(
            self._row(self.CRITIC, duration_ms=value)
        ))
        assert self._build(mod, tmp_path)["agents"][0]["duration_ms"] is None

    def test_rows_without_a_named_agent_are_dropped(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(
            {"duration_ms": 5}, "not-a-row", self._row(self.CRITIC),
        ))
        section = self._build(mod, tmp_path)
        assert [row["agent"] for row in section["agents"]] == [self.CRITIC]

    def test_non_list_agents_projects_measured_empty(self, mod, tmp_path):
        self._write(tmp_path, self._artifact(agents="nope"))
        assert self._build(mod, tmp_path)["agents"] == []

    def test_manifest_carries_the_section_and_its_availability(
        self, mod, tmp_path
    ):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        t = mod.ReviewTelemetry(str(out_dir), log_dir=str(tmp_path / "logs"))
        t.start(mode="full", repo_path=str(tmp_path), identifier="branch",
                run_id="run-1")

        manifest = _read_manifest(t)
        assert manifest["synthesis_agents"] is None
        assert manifest["availability"]["synthesis_agents"] is False

        self._write(out_dir, self._artifact(self._row(self.CRITIC)))
        t.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(t)
        assert manifest["availability"]["synthesis_agents"] is True
        assert manifest["synthesis_agents"]["agents"][0]["duration_ms"] == (
            665_000
        )

    def test_reviewer_lifecycle_is_untouched_by_synthesis_rows(
        self, mod, tmp_path
    ):
        """The non-interference pin: a full reviewer cohort's started /
        completed / incomplete projection must be byte-identical whether
        or not the synthesis section exists beside it."""
        def build(out_dir):
            t = mod.ReviewTelemetry(
                str(out_dir), log_dir=str(out_dir / "logs")
            )
            t.start(mode="full", repo_path=str(tmp_path),
                    identifier="branch", run_id="run-1")
            for index in range(19):
                name = f"agent-{index:02d}-reviewer"
                t.log_agent_start(name, domain="code", model_tier="sonnet")
                t.log_agent_complete(
                    name, review_digest=FINAL_DIGEST,
                    verdict="approve", finding_count=0,
                )
            t.finalize(step=11, phase="OUTPUT", title="Present Results")
            return _read_manifest(t)

        plain = tmp_path / "plain"
        plain.mkdir()
        baseline = build(plain)

        beside = tmp_path / "beside"
        beside.mkdir()
        self._write(beside, self._artifact(
            self._row(self.RECONCILIATOR), self._row(self.CRITIC),
        ))
        with_synthesis = build(beside)

        assert len(baseline["agents"]["started"]) == 19
        assert len(baseline["agents"]["completed"]) == 19
        assert baseline["agents"]["incomplete"] == []

        def scrub(events):
            return [
                {k: v for k, v in event.items()
                 if k not in ("timestamp", "duration_ms")}
                for event in events
            ]

        for key in ("started", "completed"):
            assert scrub(with_synthesis["agents"][key]) == scrub(
                baseline["agents"][key]
            )
        assert with_synthesis["agents"]["incomplete"] == (
            baseline["agents"]["incomplete"]
        )
        assert with_synthesis["synthesis_agents"] is not None
        assert baseline["synthesis_agents"] is None


class TestSynthesisAgentsManifestShape:
    """The section self-describes, and its row shape is declared once."""

    def _build(self, mod, tmp_path, payload):
        (tmp_path / "synthesis-agents.json").write_text(json.dumps(payload))
        return mod.manifest_sections.build_synthesis_agents_manifest(
            str(tmp_path)
        )

    def _artifact(self, *rows):
        return {
            "schema": 1,
            "finalized": True,
            "agents": list(rows),
        }

    def _row(self, **overrides):
        row = {
            key: None for key in lifecycle_contract.ROW_KEYS
        }
        row.update({
            "agent": lifecycle_contract.DECISION_CRITIC,
            "verdict": "STAND",
            "duration_ms": 665_000,
            "stalled": False,
        })
        row.update(overrides)
        return row

    def test_builder_covers_exactly_the_declared_row_keys(self, mod, tmp_path):
        """Row-shape parity, producer side. Three modules write this
        shape; teaching only one of them must fail loudly."""
        section = self._build(mod, tmp_path, self._artifact(self._row()))
        assert set(section["agents"][0]) == set(lifecycle_contract.ROW_KEYS)

    def test_an_undeclared_row_key_is_dropped(self, mod, tmp_path):
        section = self._build(
            mod, tmp_path, self._artifact(self._row(invented_key="x"))
        )
        assert "invented_key" not in section["agents"][0]

    def test_the_verdict_reaches_the_manifest(self, mod, tmp_path):
        section = self._build(
            mod, tmp_path, self._artifact(self._row(verdict="SKIPPED"))
        )
        assert section["agents"][0]["verdict"] == "SKIPPED"

    @pytest.mark.parametrize(
        "value", [None, 5, True, ["STAND"]],
        ids=["null", "int", "bool", "list"],
    )
    def test_an_unusable_verdict_is_none(self, mod, tmp_path, value):
        section = self._build(
            mod, tmp_path, self._artifact(self._row(verdict=value))
        )
        assert section["agents"][0]["verdict"] is None



class TestOptionalSectionAvailabilityKeysContract:
    """I2: the producer-declared contract, restated at the producer.

    `OPTIONAL_SECTION_AVAILABILITY_KEYS` names every optional section
    `_build_manifest` ever assigns into `availability`. This pins that
    claim in both directions against what the method actually produces —
    minus `pipeline`/`transcript`, the two structurally-always-present
    keys `_build_manifest` sets before any optional section runs, neither
    of which is optional or shares a same-named top-level section.

    A future engineer wiring a new section's availability flag directly
    into `_build_manifest` without adding it to the tuple (the reviewer's
    original probe scenario: Task 13 closed it for `dependency_refresh`
    and `reviewer_markdown`, the two sections that used to lack a flag —
    the probe below now simulates the same gap with a section name that
    stays permanently fictional) fails THIS assertion — not the
    consumer-side sanitize pin three call frames away in
    `review_metrics`, and not silently.
    """

    def test_produced_keys_equal_the_declared_tuple_both_directions(
        self, mod, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        produced = set(manifest["availability"]) - {"pipeline", "transcript"}
        declared = set(mod.OPTIONAL_SECTION_AVAILABILITY_KEYS)

        assert produced == declared, (
            f"_build_manifest's availability keys {sorted(produced)} != "
            f"the declared OPTIONAL_SECTION_AVAILABILITY_KEYS "
            f"{sorted(declared)} — update the tuple in telemetry.py (and "
            "its sanitizer in review_metrics/sanitize.py's "
            "_OPTIONAL_SECTION_SANITIZERS) when a section's availability "
            "wiring changes."
        )

    def test_the_reviewers_probe_a_flag_added_without_the_tuple_fails_here(
        self, mod, telemetry, output_dir
    ):
        """Simulates the exact gap this contract exists to catch:
        `_build_manifest` assigning a flag for a section the tuple does
        not declare. Patches the bound method for one call only."""
        real_build_manifest = telemetry._build_manifest

        def _build_manifest_with_undeclared_flag(status, extracts=None):
            manifest = real_build_manifest(status, extracts)
            manifest["availability"]["speculative_section"] = True
            return manifest

        telemetry._build_manifest = _build_manifest_with_undeclared_flag
        telemetry.start(run_id="run-1")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        manifest = _read_manifest(telemetry)
        produced = set(manifest["availability"]) - {"pipeline", "transcript"}
        declared = set(mod.OPTIONAL_SECTION_AVAILABILITY_KEYS)

        assert produced != declared
        assert "speculative_section" in produced - declared


class TestReprojectUsage:
    """`ReviewTelemetry.reproject_usage()` — the manifest's own patch path
    for a `usage_snapshot.py` re-run that happens out of band, long after
    `finalize()` already returned. Telemetry keeps ONE owning module for
    the manifest even with two call sites into it: the normal event-driven
    rebuild in `_materialize_manifest`, and this narrow out-of-band patch.
    """

    def _seed_snapshot(self, output_dir, **overrides):
        payload = {
            "schema": 1,
            "captured_at": "2026-08-19T10:43:00+00:00",
            "window": {
                "started_at": "2026-08-19T10:00:00+00:00",
                "ended_at": "2026-08-19T10:43:00+00:00",
                "closed": True,
            },
            "availability": {
                "subagents": "complete", "orchestrator": "complete",
            },
            "reason": None,
            "agents_measured": {"measured": 1, "expected": 1},
            "subagent_usage": [],
            "subagent_totals": None,
            "usage_by_model": None,
            "orchestrator_usage": None,
        }
        payload.update(overrides)
        (Path(output_dir) / "usage-snapshot.json").write_text(
            json.dumps(payload)
        )

    @staticmethod
    def _strip_usage(manifest):
        """The residual: everything a `usage` patch has no license to touch."""
        stripped = dict(manifest)
        stripped.pop("usage", None)
        availability = dict(stripped.get("availability") or {})
        availability.pop("usage", None)
        stripped["availability"] = availability
        return stripped

    def _fully_populated_manifest(self, mod, output_dir):
        """Every optional section carries real, distinguishable content —
        the shape a full `_build_manifest` rebuild from THIS instance's
        actual (near-empty) JSONL log would NOT reproduce. A
        `reproject_usage()` that reconstructed the whole manifest instead
        of surgically patching two keys — the reviewer's mutation (d) —
        would replace every one of these with the rebuild's own (emptier)
        values; a correct surgical patch leaves them exactly as written
        here.
        """
        return {
            "schema": mod.EVENT_SCHEMA,
            "status": "complete",
            "run": {
                "id": "run-1",
                "session_id": "session-fixture",
                "plugin_version": "9.9.9",
                "mode": "pr",
                "repo_path": "/fixture/repo",
                "output_dir": str(output_dir),
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": "2026-01-01T01:00:00+00:00",
                "git": {
                    "requested_range": "base..head",
                    "base_sha": "a" * 40,
                    "head_sha": "b" * 40,
                },
            },
            "steps": [
                {"step": 99, "phase": "FIXTURE", "title": "Vandal Probe"}
            ],
            "agents": {
                "started": [{"agent": "fixture-reviewer"}],
                "completed": [],
                "incomplete": [],
            },
            "dispatch": {"fixture": "dispatch-payload"},
            "assignment": {"fixture": "coverage-payload"},
            "outcome": {"summary": {"fixture": "outcome-payload"}},
            "availability": {
                "pipeline": True,
                "transcript": True,
                "assignment": True,
                "worktree_hygiene": True,
                "synthesis_agents": True,
                "usage": True,
                "skipped_steps": True,
                "dependency_refresh": True,
                "reviewer_markdown": True,
                "findings_markdown": True,
            },
            "worktree_hygiene": {"fixture": "hygiene-payload"},
            "synthesis_agents": {"fixture": "synthesis-payload"},
            "usage": {"fixture": "stale — must be replaced"},
            "skipped_steps": [{"fixture": "skip-payload"}],
            "dependency_refresh": {"fixture": "deps-payload"},
            "reviewer_markdown": {"fixture": "markdown-payload"},
            "findings_markdown": {"fixture": "findings-payload"},
        }

    def test_patches_usage_and_leaves_every_other_section_byte_identical(
        self, mod, telemetry, output_dir
    ):
        """I2/M2/M4 residual pin. The reviewer's mutation (d) — a
        `reproject_usage()` that rebuilds the whole manifest rather than
        patching two keys — silently vandalized seven other optional
        sections and still passed the full 5093-test suite, because no
        existing fixture carried real content in all of them at once.
        This one does, and asserts the residual (everything but `usage`
        and `availability.usage`) survives byte-identical.
        """
        telemetry.start(run_id="run-1", session_id="session-real")
        manifest_path = Path(telemetry.manifest_path)
        fixture = self._fully_populated_manifest(mod, output_dir)
        manifest_path.write_text(json.dumps(fixture))
        self._seed_snapshot(output_dir)

        result = telemetry.reproject_usage()

        assert result == "written"
        after = json.loads(manifest_path.read_text())
        assert self._strip_usage(after) == self._strip_usage(fixture)
        assert after["usage"] is not None
        assert after["usage"] != fixture["usage"]
        assert after["availability"]["usage"] is True

    def test_running_manifest_is_left_untouched(
        self, mod, telemetry, output_dir
    ):
        """M2 gate: a still-running manifest is `finalize()`'s territory
        alone. The in-pipeline step-11 call reaches this method while the
        manifest still reads "running" (finalize has not appended
        `pipeline_end` yet), so it is a no-op there every time —
        `finalize()`'s own full rebuild, moments later in the same run,
        is what actually settles `usage` for a normal pipeline run.
        """
        telemetry.start(run_id="run-1")  # status stays "running"
        manifest_path = Path(telemetry.manifest_path)
        before_bytes = manifest_path.read_bytes()
        self._seed_snapshot(output_dir)

        result = telemetry.reproject_usage()

        assert result == "not_settled"
        assert manifest_path.read_bytes() == before_bytes

    def test_unsupported_schema_manifest_is_left_untouched(
        self, mod, telemetry, output_dir
    ):
        """M4 gate: an unsupported-schema manifest is not this method's
        to interpret."""
        telemetry.start(run_id="run-1")
        manifest_path = Path(telemetry.manifest_path)
        manifest = json.loads(manifest_path.read_text())
        manifest["status"] = "complete"
        manifest["schema"] = mod.EVENT_SCHEMA - 1
        manifest_path.write_text(json.dumps(manifest))
        before_bytes = manifest_path.read_bytes()
        self._seed_snapshot(output_dir)

        result = telemetry.reproject_usage()

        assert result == "unsupported_schema"
        assert manifest_path.read_bytes() == before_bytes

    def test_no_manifest_is_a_silent_no_op(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs-none"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))

        assert t.reproject_usage() == "absent"

    def test_unreadable_snapshot_still_reprojects_an_honest_absence(
        self, mod, telemetry, output_dir
    ):
        """A settled manifest whose `usage-snapshot.json` cannot be read
        still gets patched — to `usage: None`, `availability.usage:
        False` — because that IS the current truth, not a reason to skip
        the write."""
        telemetry.start(run_id="run-1")
        manifest_path = Path(telemetry.manifest_path)
        manifest = json.loads(manifest_path.read_text())
        manifest["status"] = "complete"
        manifest_path.write_text(json.dumps(manifest))
        # No usage-snapshot.json written at all.

        result = telemetry.reproject_usage()

        after = json.loads(manifest_path.read_text())
        assert result == "written"
        assert after["usage"] is None
        assert after["availability"]["usage"] is False

    def test_a_corrupt_marker_reports_io_failure_never_raises(
        self, mod, output_dir, tmp_path
    ):
        """The marker read behind `manifest_path` raises on invalid bytes;
        the method must answer, not traceback — the CLI calls it after the
        snapshot already wrote, and a raise would cost the whole summary
        (regression: the first cut read the property unguarded)."""
        log_dir = tmp_path / "logs-corrupt"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        marker = Path(output_dir) / mod.MARKER_FILE
        marker.write_bytes(b"\xff\xfe not utf-8")

        assert t.reproject_usage() == "io_failure"

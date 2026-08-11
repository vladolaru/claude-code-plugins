"""Tests for review/telemetry.py — JSONL telemetry for PR review pipelines."""

import glob
import importlib.util
import json
import os
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

    def test_start_records_versioned_run_identity(self, telemetry):
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
        assert start["schema_version"] == 1
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
            (event["schema_version"], event["run_id"])
            for event in _read_events(telemetry.log_path)
        }
        assert identities == {(1, "run-1")}


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


# ── Run manifest ───────────────────────────────────────────────


class TestRunManifest:
    """A fail-open sidecar materializes the current run state."""

    def test_start_materializes_running_manifest(self, telemetry):
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
        assert manifest["schema_version"] == 1
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
            "coverage": False,
        }
        assert manifest["coverage"] is None

    def test_log_step_refreshes_running_manifest(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_step(step=3, phase="AWARENESS", title="Gather Context")

        manifest = _read_manifest(telemetry)
        assert manifest["status"] == "running"
        assert manifest["steps"][-1]["step"] == 3
        assert manifest["steps"][-1]["phase"] == "AWARENESS"

    def test_log_step_manifest_allowlists_lifecycle_and_decision_fields(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_step(
            step=10,
            phase="VALIDATION",
            title="Decision Critic",
            bot_mode=True,
            thoughts_length=321,
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
        assert step["args"] == {
            "bot_mode": True,
            "thoughts_length": 321,
        }
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
            agent_name="security-reviewer", verdict="approve"
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
            agent_name="code-reviewer", verdict="approve"
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        assert _read_manifest(telemetry)["agents"]["incomplete"] == [
            "code-reviewer"
        ]

    def test_repeated_completions_are_latest_save_revisions(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer",
            verdict="comment",
            issue_count=1,
            severities={"medium": 1},
        )
        telemetry.log_agent_complete(
            agent_name="code-reviewer",
            verdict="approve",
            issue_count=0,
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        raw_completions = [
            event for event in _read_events(telemetry.log_path)
            if event["event"] == "agent_complete"
        ]
        assert len(raw_completions) == 2
        assert [event["verdict"] for event in raw_completions] == [
            "comment",
            "approve",
        ]
        assert _read_manifest(telemetry)["agents"]["completed"] == [
            {
                "schema_version": 1,
                "run_id": "run-1",
                "event": "agent_complete",
                "timestamp": raw_completions[-1]["timestamp"],
                "agent": "code-reviewer",
                "duration_ms": None,
                "verdict": "approve",
                "issue_count": 0,
                "severities": {},
            }
        ]

    def test_start_after_completion_creates_a_retry_execution(self, telemetry):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", verdict="approve"
        )
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", verdict="comment",
            issue_count=1, severities={"medium": 1},
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
            agent_name="code-reviewer", verdict="approve"
        )
        telemetry.log_agent_complete(
            agent_name="code-reviewer", verdict="comment",
            issue_count=1, severities={"medium": 1},
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert len(agents["started"]) == 2
        assert [event["verdict"] for event in agents["completed"]] == [
            "approve",
            "comment",
        ]
        assert agents["incomplete"] == []

    def test_completion_beyond_outstanding_starts_is_a_corrected_save(
        self, telemetry
    ):
        """Once every start is matched, a further completion revises the
        latest one instead of inventing an execution."""
        telemetry.start(run_id="run-1")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", verdict="approve"
        )
        telemetry.log_agent_complete(
            agent_name="code-reviewer", verdict="comment",
            issue_count=1, severities={"medium": 1},
        )
        telemetry.log_agent_complete(
            agent_name="code-reviewer", verdict="request_changes",
            issue_count=2, severities={"high": 2},
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        agents = _read_manifest(telemetry)["agents"]
        assert len(agents["started"]) == 2
        assert [event["verdict"] for event in agents["completed"]] == [
            "approve",
            "request_changes",
        ]
        assert agents["incomplete"] == []

    def test_completion_without_start_remains_visible_for_strict_validation(
        self, telemetry
    ):
        telemetry.start(run_id="run-1")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", verdict="approve"
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
            agent_name="code-reviewer", verdict="approve"
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
                agent_name=agent_name, verdict="approve"
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
            agent_name="code-reviewer", verdict="approve"
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
            verdict="comment",
            issue_count=1,
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
        assert manifest["availability"]["coverage"] is True
        assert manifest["coverage"] == {
            "changed": [
                "docs/readme.md",
                "src/a.py",
                "src/b.py",
                "vendor/generated.js",
            ],
            "reviewable": ["docs/readme.md", "src/a.py", "src/b.py"],
            "by_agent": {
                "docs-reviewer": ["docs/readme.md"],
                "security-reviewer": [
                    "src/a.py",
                    "src/b.py",
                    "vendor/generated.js",
                ],
            },
            "assigned": ["docs/readme.md", "src/a.py", "src/b.py"],
            "excluded": [
                {"path": "vendor/generated.js", "reason": "noise_filtered"},
            ],
            "uncovered": [],
            "semantics": "generated_scope_not_proof_of_model_read",
        }

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
        assert manifest["availability"]["coverage"] is True
        assert manifest["coverage"]["changed"] == [unicode_path]
        assert manifest["coverage"]["reviewable"] == [unicode_path]
        assert manifest["coverage"]["by_agent"] == {
            "code-reviewer": [unicode_path],
        }
        assert manifest["coverage"]["assigned"] == [unicode_path]
        assert manifest["coverage"]["uncovered"] == []

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

        coverage = _read_manifest(telemetry)["coverage"]
        assert coverage["changed"] == [nested_path, literal_backslash]
        assert coverage["reviewable"] == [nested_path, literal_backslash]
        assert coverage["by_agent"]["code-reviewer"] == [
            nested_path,
            literal_backslash,
        ]
        assert coverage["assigned"] == [nested_path, literal_backslash]
        assert len(coverage["assigned"]) == 2

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
        coverage = manifest["coverage"]
        assert coverage["changed"] == [literal_quoted_path, plain_path]
        assert coverage["reviewable"] == [literal_quoted_path, plain_path]
        assert coverage["by_agent"] == {
            "code-reviewer": [literal_quoted_path],
        }
        assert coverage["assigned"] == [literal_quoted_path]
        assert coverage["uncovered"] == [plain_path]

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
        assert manifest["availability"]["coverage"] is False
        assert manifest["coverage"] is None

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
        assert manifest["availability"]["coverage"] is False
        assert manifest["coverage"] is None

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

        coverage = _read_manifest(telemetry)["coverage"]
        assert coverage["by_agent"] == {}
        assert coverage["assigned"] == []
        assert coverage["uncovered"] == ["src/a.py"]

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

        coverage = _read_manifest(telemetry)["coverage"]
        assert coverage["by_agent"] == {}
        assert coverage["assigned"] == []
        assert coverage["uncovered"] == ["src/a.py"]

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

        assert _read_manifest(telemetry)["coverage"]["by_agent"] == {
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

        coverage = _read_manifest(telemetry)["coverage"]
        assert coverage["by_agent"] == {
            "a11y-reviewer": ["templates/page.php"],
        }
        assert coverage["assigned"] == ["templates/page.php"]

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
        self, mod, tmp_path, context_payload, plan_payload
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
        assert manifest["availability"]["coverage"] is False
        assert manifest["coverage"] is None

    def test_valid_empty_path_sets_are_available_zero_coverage(
        self, telemetry, output_dir
    ):
        _write_coverage_inputs(
            output_dir, changed=[], reviewable=[], agents=[]
        )

        telemetry.start(run_id="run-1")

        manifest = _read_manifest(telemetry)
        assert manifest["availability"]["coverage"] is True
        assert manifest["coverage"] == {
            "changed": [],
            "reviewable": [],
            "by_agent": {},
            "assigned": [],
            "excluded": [],
            "uncovered": [],
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
        assert manifest["availability"]["coverage"] is False
        assert manifest["coverage"] is None

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

    @pytest.mark.parametrize("plan_name", ["initial", "final"])
    @pytest.mark.parametrize(
        "invalid_status",
        [
            pytest.param("__missing__", id="missing"),
            None,
            "",
            "UNKNOWN",
            "DISPATCHED",
            [],
            {},
            [{"nested": []}],
            {"nested": []},
        ],
    )
    def test_manifest_rejects_incomplete_dispatch_statuses(
        self, telemetry, output_dir, plan_name, invalid_status
    ):
        initial_agent = {"name": "code-reviewer", "status": "DISPATCH"}
        final_agent = {"name": "code-reviewer", "status": "DISPATCH"}
        target = initial_agent if plan_name == "initial" else final_agent
        if invalid_status == "__missing__":
            target.pop("status")
        else:
            target["status"] = invalid_status
        (output_dir / "dispatch-plan.initial.json").write_text(
            json.dumps({"agents": [initial_agent]})
        )
        (output_dir / "dispatch-plan.json").write_text(
            json.dumps({"agents": [final_agent]})
        )

        telemetry.start(run_id="run-1")
        dispatch = _read_manifest(telemetry)["dispatch"]

        assert dispatch["comparison_available"] is False
        unavailable_field = (
            "planner_baseline_available"
            if plan_name == "initial"
            else "final_plan_available"
        )
        assert dispatch[unavailable_field] is False
        expected_reason = (
            "planner_baseline_unavailable"
            if plan_name == "initial"
            else "final_plan_unavailable"
        )
        assert expected_reason in dispatch["invalid_reason_codes"]
        if plan_name == "initial":
            assert dispatch["final_plan_available"] is True
            assert dispatch["agents"]["code-reviewer"]["initial_status"] == "DISPATCH"
            assert dispatch["agents"]["code-reviewer"]["final_status"] == "DISPATCH"
            assert dispatch["agents"]["code-reviewer"]["change"] == "unchanged"
        else:
            assert dispatch["planner_baseline_available"] is True
            assert dispatch["agents"] == {}

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
                    "issues": ["SENSITIVE_FINAL_FINDING"],
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
                    "issues": ["SENSITIVE_FINDING"],
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
            "issues": [{
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
        review = {
            "verdict": "comment",
            "issues": [
                {"severity": "high", "title": "XSS vuln"},
                {"severity": "medium", "title": "Missing escape"},
            ],
        }
        (output_dir / "security-review.json").write_text(json.dumps(review))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        agents = events[-1]["snapshot"]["agent_results"]
        assert "security" in agents
        assert agents["security"]["verdict"] == "comment"
        assert agents["security"]["issue_count"] == 2
        assert agents["security"]["severities"]["high"] == 1

    def test_extracts_agent_advisory_measurement(self, mod, output_dir, tmp_path):
        review = {
            "verdict": "approve",
            "summary": {
                "advisory_suppressed": 2,
                "verdict_without_advisory": "block",
            },
            "issues": [{"severity": "critical", "channel": "advisory"}],
        }
        (output_dir / "security-review.json").write_text(json.dumps(review))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))

        extracted = t._extract_agent_results()["security"]

        assert extracted["advisory_suppressed"] == 2
        assert extracted["verdict_without_advisory"] == "block"

    @pytest.mark.parametrize(
        ("verdict", "summary", "expected_count"),
        [
            pytest.param(
                "approve",
                {"advisory_suppressed": True, "verdict_without_advisory": "block"},
                None,
                id="boolean-count",
            ),
            pytest.param(
                "approve",
                {"advisory_suppressed": -1, "verdict_without_advisory": "block"},
                None,
                id="negative-count",
            ),
            pytest.param(
                "approve",
                {"advisory_suppressed": 1, "verdict_without_advisory": "banana"},
                1,
                id="unknown-verdict",
            ),
            pytest.param(
                "approve",
                {"advisory_suppressed": 1, "verdict_without_advisory": []},
                1,
                id="non-string-verdict",
            ),
            pytest.param(
                "approve",
                {"advisory_suppressed": 1, "verdict_without_advisory": "not_applicable"},
                1,
                id="not-applicable-counterfactual",
            ),
            pytest.param(
                "block",
                {"advisory_suppressed": 1, "verdict_without_advisory": "block"},
                1,
                id="equal-counterfactual",
            ),
            pytest.param(
                "request_changes",
                {"advisory_suppressed": 1, "verdict_without_advisory": "comment"},
                1,
                id="softer-counterfactual",
            ),
            pytest.param(
                "banana",
                {"advisory_suppressed": 1, "verdict_without_advisory": "block"},
                1,
                id="unknown-actual-verdict",
            ),
        ],
    )
    def test_agent_advisory_measurement_omits_malformed_values(
        self, mod, output_dir, tmp_path, verdict, summary, expected_count
    ):
        (output_dir / "security-review.json").write_text(json.dumps({
            "verdict": verdict,
            "summary": summary,
            "issues": [],
        }))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))

        extracted = t._extract_agent_results()["security"]

        if expected_count is not None:
            assert extracted["advisory_suppressed"] == expected_count
        else:
            assert "advisory_suppressed" not in extracted
        assert "verdict_without_advisory" not in extracted

    def test_excludes_review_findings_from_agent_results(self, mod, output_dir, tmp_path):
        """review-findings.json is reconciled output, not an agent result."""
        log_dir = tmp_path / "logs"
        (output_dir / "review-findings.json").write_text(json.dumps(
            {"verdict": "comment", "issues": [{"severity": "high"}]}
        ))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        snap = events[-1]["snapshot"]
        assert "agent_results" not in snap
        assert "findings" in snap

    def test_extracts_findings(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        findings = {
            "verdict": "comment",
            "issues": [
                {"severity": "high", "title": "Real issue"},
                {"severity": "medium", "title": "Minor issue"},
                {"severity": "low", "title": "Nit"},
            ],
        }
        (output_dir / "review-findings.json").write_text(json.dumps(findings))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.start(pr_number="42")
        t.finalize(step=15, phase="OUTPUT", title="Present Results")
        events = _read_events(t.log_path)
        f = events[-1]["snapshot"]["findings"]
        assert f["verdict"] == "comment"
        assert f["total_issues"] == 3
        assert f["severities"]["high"] == 1

    def test_findings_measurement_reaches_summary_and_manifest(
        self, mod, output_dir, tmp_path
    ):
        findings = {
            "verdict": "approve",
            "summary": {
                "advisory_suppressed": 1,
                "verdict_without_advisory": "block",
            },
            "issues": [{"severity": "critical", "channel": "advisory"}],
        }
        (output_dir / "review-findings.json").write_text(json.dumps(findings))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))
        t.start(pr_number="42")

        t.finalize(step=15, phase="OUTPUT", title="Present Results")

        event = _read_events(t.log_path)[-1]
        snapshot = event["snapshot"]["findings"]
        assert snapshot["advisory_suppressed"] == 1
        assert snapshot["verdict_without_advisory"] == "block"
        assert event["summary"]["final_advisory_suppressed"] == 1
        assert event["summary"]["final_verdict_without_advisory"] == "block"
        manifest_summary = _read_manifest(t)["outcome"]["summary"]
        assert manifest_summary["final_advisory_suppressed"] == 1
        assert manifest_summary["final_verdict_without_advisory"] == "block"

    def test_findings_omit_malformed_advisory_measurement(
        self, mod, output_dir, tmp_path
    ):
        (output_dir / "review-findings.json").write_text(json.dumps({
            "verdict": "approve",
            "summary": {
                "advisory_suppressed": True,
                "verdict_without_advisory": "banana",
            },
            "issues": [],
        }))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))

        findings = t._extract_findings()

        assert "advisory_suppressed" not in findings
        assert "verdict_without_advisory" not in findings

    def test_findings_preserve_count_but_reject_impossible_counterfactual(
        self, mod, output_dir, tmp_path
    ):
        (output_dir / "review-findings.json").write_text(json.dumps({
            "verdict": "block",
            "summary": {
                "advisory_suppressed": 1,
                "verdict_without_advisory": "comment",
            },
            "issues": [],
        }))
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(tmp_path / "logs"))

        findings = t._extract_findings()

        assert findings["advisory_suppressed"] == 1
        assert "verdict_without_advisory" not in findings

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


# ── log_agent_complete() ─────────────────────────────────────────


class TestLogAgentComplete:
    """ReviewTelemetry.log_agent_complete() appends completion events."""

    def test_appends_agent_complete_event(self, telemetry, output_dir):
        telemetry.start(pr_number="42")
        (output_dir / "security-reviewer.started").write_text(
            datetime.now(timezone.utc).isoformat()
        )
        time.sleep(0.05)
        telemetry.log_agent_complete(
            agent_name="security-reviewer", verdict="comment",
            issue_count=2, severities={"high": 1, "medium": 1},
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
            agent_name="security-reviewer", verdict="comment",
            issue_count=2, severities={"high": 1, "medium": 1},
        )
        events = _read_events(telemetry.log_path)
        assert events[-1]["verdict"] == "comment"
        assert events[-1]["issue_count"] == 2
        assert events[-1]["severities"] == {"high": 1, "medium": 1}

    def test_calculates_duration_from_started_file(self, telemetry, output_dir):
        telemetry.start(pr_number="42")
        (output_dir / "security-reviewer.started").write_text(
            datetime.now(timezone.utc).isoformat()
        )
        time.sleep(0.05)
        telemetry.log_agent_complete(agent_name="security-reviewer", verdict="approve")
        events = _read_events(telemetry.log_path)
        assert events[-1]["duration_ms"] is not None
        assert events[-1]["duration_ms"] >= 40

    def test_duration_none_without_started_file(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_agent_complete(agent_name="security-reviewer", verdict="approve")
        events = _read_events(telemetry.log_path)
        assert events[-1]["duration_ms"] is None

    def test_noop_without_start(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t.log_agent_complete(agent_name="security-reviewer", verdict="approve")
        assert t.log_path is None


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


class TestDependencyRefreshManifest:
    """The manifest records the sanitized dependency-refresh report."""

    def _telemetry(self, mod, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        log_dir = tmp_path / "logs"
        t = mod.ReviewTelemetry(str(out_dir), log_dir=str(log_dir))
        t.start(mode="full", repo_path=str(tmp_path), identifier="branch",
                run_id="run-1")
        return t, out_dir

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

    def test_report_is_sanitized_into_the_manifest(self, mod, tmp_path):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "dependency-refresh.json").write_text(json.dumps({
            "status": "completed",
            "commands": [
                {"directory": ".", "command": "composer install",
                 "exit_status": "ok"},
            ],
            "tracked_files_dirty": False,
        }))
        t.log_step(step=3, phase="SETUP", title="Gather Context")
        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section == {
            "requested": True,
            "reported": True,
            "status": "completed",
            "tracked_files_dirty": False,
            "commands": [
                {"directory": ".", "command": "composer install",
                 "exit_status": "ok"},
            ],
        }

    def test_verification_is_sanitized_into_the_manifest(self, mod, tmp_path):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "dependency-refresh.json").write_text(json.dumps({
            "status": "completed",
            "commands": [],
            "tracked_files_dirty": False,
        }))
        (out_dir / "dependency-refresh-verification.json").write_text(
            json.dumps({
                "report_present": True,
                "commands_allowed": False,
                "disallowed_commands": ["x" * 600, 42],
                "tracked_files_dirty": False,
                "dirty_files": ["ignored.php"],
                "verification_failed": False,
            })
        )

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        assert manifest["dependency_refresh"]["verification"] == {
            "report_present": True,
            "commands_allowed": False,
            "disallowed_commands": ["x" * 500],
            "tracked_files_dirty": False,
            "verification_failed": False,
        }

    def test_skipped_refresh_is_distinct_from_successful_verification(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "dependency-refresh-verification.json").write_text(
            json.dumps({
                "skipped": True,
                "skipped_reason": "dirty_worktree",
                "dirty_files": [
                    *(f"tracked-{index:02d}.txt" for index in range(25)),
                    42,
                ],
            })
        )

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section == {
            "requested": True,
            "reported": False,
            "skipped": True,
            "skipped_reason": "dirty_worktree",
            "dirty_files": [
                f"tracked-{index:02d}.txt" for index in range(20)
            ],
        }
        assert "verification" not in section

    def test_unhashable_status_preserves_report_and_verification(
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
        (out_dir / "dependency-refresh-verification.json").write_text(
            json.dumps({
                "report_present": True,
                "commands_allowed": True,
                "disallowed_commands": [],
                "tracked_files_dirty": False,
                "dirty_files": [],
                "verification_failed": False,
            })
        )

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        assert manifest["dependency_refresh"] == {
            "requested": True,
            "reported": True,
            "status": "invalid",
            "tracked_files_dirty": False,
            "commands": [],
            "verification": {
                "report_present": True,
                "commands_allowed": True,
                "disallowed_commands": [],
                "tracked_files_dirty": False,
                "verification_failed": False,
            },
        }
        assert manifest["steps"][-1]["step"] == 5

    def test_verification_is_absent_when_file_is_missing(self, mod, tmp_path):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "dependency-refresh.json").write_text(json.dumps({
            "status": "completed",
            "commands": [],
            "tracked_files_dirty": False,
        }))

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        assert "verification" not in manifest["dependency_refresh"]

    def test_verification_is_preserved_when_self_report_is_missing(
        self, mod, tmp_path
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "dependency-refresh-verification.json").write_text(
            json.dumps({
                "report_present": False,
                "commands_allowed": None,
                "disallowed_commands": [],
                "tracked_files_dirty": False,
                "dirty_files": [],
                "verification_failed": False,
            })
        )

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section["reported"] is False
        assert section["verification"] == {
            "report_present": False,
            "commands_allowed": None,
            "disallowed_commands": [],
            "tracked_files_dirty": False,
            "verification_failed": False,
        }

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
    def test_hostile_self_report_preserves_verification(
        self, mod, tmp_path, report_bytes
    ):
        t, out_dir = self._telemetry(mod, tmp_path)
        (out_dir / "run-config.json").write_text(json.dumps(
            {"mode": "full", "refresh_dependencies": True}))
        (out_dir / "dependency-refresh.json").write_bytes(report_bytes)
        (out_dir / "dependency-refresh-verification.json").write_text(
            json.dumps({
                "report_present": False,
                "commands_allowed": None,
                "disallowed_commands": [],
                "tracked_files_dirty": False,
                "dirty_files": [],
                "verification_failed": True,
            })
        )

        t.log_step(step=5, phase="EXECUTION", title="Dispatch Plan + Triage")

        manifest = json.loads(Path(t.manifest_path).read_text())
        section = manifest["dependency_refresh"]
        assert section["reported"] is False
        assert section["verification"] == {
            "report_present": False,
            "commands_allowed": None,
            "disallowed_commands": [],
            "tracked_files_dirty": False,
            "verification_failed": True,
        }

    def test_invalid_report_values_sanitize_not_crash(self, mod, tmp_path):
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
        section = manifest["dependency_refresh"]
        assert section["requested"] is False
        assert section["reported"] is True
        assert section["status"] == "invalid"
        assert section["tracked_files_dirty"] is None
        assert section["commands"] == [
            {"directory": None, "command": None, "exit_status": "invalid"},
        ]

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

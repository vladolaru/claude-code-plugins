"""Tests for review-telemetry.py — JSONL telemetry for PR review pipelines."""

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

# Import the module under test
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "review-telemetry.py"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_fixtures import COMPLETE_CONTEXT


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


# ── start() ─────────────────────────────────────────────────────────


class TestStart:
    """ReviewTelemetry.start() creates log infrastructure."""

    def test_default_log_dir_is_pirategoat_tools(self, mod):
        assert "/.pirategoat-tools/" in mod.LOG_DIR
        assert mod.LOG_DIR.endswith("/logs/pr-reviews")

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

    def test_extracts_dispatch_plan(self, mod, output_dir, tmp_path):
        log_dir = tmp_path / "logs"
        dispatch = {
            "agents": [
                {"name": "pr-reviewer", "status": "DISPATCH", "domain": "code", "reason": "always"},
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
        assert d["agents"]["pr-reviewer"]["status"] == "DISPATCH"

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

    def test_separate_files_per_run(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"

        t1 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        path1 = t1.start(pr_number="42")
        time.sleep(1.1)  # Ensure different timestamp (1-second resolution)

        t2 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        path2 = t2.start(pr_number="42")

        assert path1 != path2
        assert os.path.isfile(path1)
        assert os.path.isfile(path2)

    def test_glob_finds_all_runs(self, mod, tmp_path):
        output_dir = tmp_path / "pr-review-org-repo-42"
        output_dir.mkdir()
        log_dir = tmp_path / "logs"

        t1 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t1.start(pr_number="42")
        time.sleep(1.1)

        t2 = mod.ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
        t2.start(pr_number="42")

        pattern = str(log_dir / "pr-review-org-repo-42--*.jsonl")
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

    def test_includes_scope(self, telemetry):
        telemetry.start(pr_number="42")
        telemetry.log_agent_start(
            agent_name="security-reviewer", domain="security",
            scope_files=3, scope_lines=150,
        )
        events = _read_events(telemetry.log_path)
        assert events[1]["scope"]["files"] == 3
        assert events[1]["scope"]["lines"] == 150

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
                {"name": "pr-reviewer", "status": "DISPATCH"},
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

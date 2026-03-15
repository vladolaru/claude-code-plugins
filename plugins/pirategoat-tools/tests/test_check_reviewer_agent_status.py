"""Tests for check-reviewer-agent-status.py."""

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check-reviewer-agent-status.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_status", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def _write_plan(tmp_path, agents):
    (tmp_path / "dispatch-plan.json").write_text(json.dumps({"agents": agents}))


def _start_agent(tmp_path, name, minutes_ago=0):
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    (tmp_path / f"{name}.started").write_text(ts.isoformat())


def _finish_agent(tmp_path, name, findings=None, verdict="APPROVE"):
    (tmp_path / f"{name}-review.json").write_text(json.dumps({
        "findings": findings or [], "verdict": verdict,
    }))


class TestCheckStatus:
    def test_all_finished(self, mod, tmp_path):
        _write_plan(tmp_path, [
            {"name": "pr-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "pr-reviewer")
        _start_agent(tmp_path, "security-reviewer")
        _finish_agent(tmp_path, "pr-reviewer", [{"severity": "critical"}], "REQUEST_CHANGES")
        _finish_agent(tmp_path, "security-reviewer")

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True
        assert result["finished"] == 2
        assert result["running"] == 0
        assert result["not_dispatched"] == 0

    def test_running_has_started_but_no_review(self, mod, tmp_path):
        _write_plan(tmp_path, [
            {"name": "pr-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "pr-reviewer")
        _finish_agent(tmp_path, "pr-reviewer")
        _start_agent(tmp_path, "security-reviewer")

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is False
        assert result["finished"] == 1
        assert result["running"] == 1

    def test_not_dispatched_has_no_started_marker(self, mod, tmp_path):
        _write_plan(tmp_path, [
            {"name": "pr-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "pr-reviewer")
        _finish_agent(tmp_path, "pr-reviewer")

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is False
        assert result["not_dispatched"] == 1
        security = [a for a in result["agents"] if a["name"] == "security-reviewer"][0]
        assert security["status"] == "NOT_DISPATCHED"

    def test_timed_out_agent(self, mod, tmp_path):
        """Agent started 25 minutes ago, no review file → TIMED_OUT."""
        _write_plan(tmp_path, [
            {"name": "pr-reviewer", "status": "DISPATCH"},
            {"name": "slow-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "pr-reviewer")
        _finish_agent(tmp_path, "pr-reviewer")
        _start_agent(tmp_path, "slow-reviewer", minutes_ago=25)

        # Default timeout is 1200s (20 min) — 25 min exceeds it
        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True  # timed out = not waiting
        assert result["timed_out"] == 1
        assert result["running"] == 0
        slow = [a for a in result["agents"] if a["name"] == "slow-reviewer"][0]
        assert slow["status"] == "TIMED_OUT"

    def test_timed_out_does_not_block_all_done(self, mod, tmp_path):
        """ALL_DONE should be true when agents are finished or timed out."""
        _write_plan(tmp_path, [
            {"name": "pr-reviewer", "status": "DISPATCH"},
            {"name": "slow-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "pr-reviewer")
        _finish_agent(tmp_path, "pr-reviewer")
        _start_agent(tmp_path, "slow-reviewer", minutes_ago=25)

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True
        assert result["finished"] == 1
        assert result["timed_out"] == 1

    def test_reads_timeout_from_context_file(self, mod, tmp_path):
        """Timeout should come from review-context.json if present."""
        _write_plan(tmp_path, [{"name": "slow-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "slow-reviewer", minutes_ago=12)
        # Write context with 10-minute timeout (600s)
        (tmp_path / "review-context.json").write_text(json.dumps({
            "review": {"agent_timeout_seconds": 600},
        }))

        result = mod.check_status(str(tmp_path))  # reads 600s from file
        assert result["timed_out"] == 1  # 12 min > 10 min timeout

    def test_skipped_agents_dont_count(self, mod, tmp_path):
        _write_plan(tmp_path, [
            {"name": "pr-reviewer", "status": "DISPATCH"},
            {"name": "a11y-reviewer", "status": "SKIP", "reason": "no frontend files"},
        ])
        _start_agent(tmp_path, "pr-reviewer")
        _finish_agent(tmp_path, "pr-reviewer")

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True
        assert result["dispatched"] == 1
        assert result["skipped"] == 1

    def test_extracts_severity_counts(self, mod, tmp_path):
        _write_plan(tmp_path, [{"name": "pr-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "pr-reviewer")
        _finish_agent(tmp_path, "pr-reviewer", [
            {"severity": "critical"},
            {"severity": "high"},
            {"severity": "high"},
            {"severity": "medium"},
        ], "REQUEST_CHANGES")

        result = mod.check_status(str(tmp_path))
        agent = result["agents"][0]
        assert agent["counts"]["critical"] == 1
        assert agent["counts"]["high"] == 2
        assert agent["verdict"] == "REQUEST_CHANGES"

    def test_no_dispatch_plan_exits_1(self, tmp_path):
        cmd = [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode == 1

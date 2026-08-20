"""Tests for review/agents_status.py."""

import importlib.util
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "review" / "agents_status.py"

from review import dispatch_status


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


def _reviewer_filename(agent_name: str) -> str:
    """Mirror the production derive_reviewer_name convention."""
    if agent_name.endswith("-reviewer"):
        base = agent_name[: -len("-reviewer")]
    else:
        base = agent_name
    return f"{base}-review.json"


def _finish_agent(tmp_path, name, issues=None, verdict="APPROVE"):
    (tmp_path / _reviewer_filename(name)).write_text(json.dumps({
        "issues": issues or [], "verdict": verdict,
    }))


class TestCheckStatus:
    def test_all_finished(self, mod, tmp_path):
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _start_agent(tmp_path, "security-reviewer")
        _finish_agent(tmp_path, "code-reviewer", [{"severity": "critical"}], "REQUEST_CHANGES")
        _finish_agent(tmp_path, "security-reviewer")

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True
        assert result["finished"] == 2
        assert result["running"] == 0
        assert result["not_dispatched"] == 0

    def test_running_has_started_but_no_review(self, mod, tmp_path):
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")
        _start_agent(tmp_path, "security-reviewer")

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is False
        assert result["finished"] == 1
        assert result["running"] == 1

    def test_not_dispatched_has_no_started_marker(self, mod, tmp_path):
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True  # NOT_DISPATCHED no longer blocks
        assert result["not_dispatched"] == 1
        security = [a for a in result["agents"] if a["name"] == "security-reviewer"][0]
        assert security["status"] == "NOT_DISPATCHED"

    def test_timed_out_agent(self, mod, tmp_path):
        """Agent started 25 minutes ago, no review file → TIMED_OUT."""
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "slow-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")
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
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "slow-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")
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
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "a11y-reviewer", "status": "SKIPPED", "reason": "no frontend files"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True
        assert result["dispatched"] == 1
        assert result["skipped"] == 1

    def test_extracts_severity_counts(self, mod, tmp_path):
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer", [
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

    def test_invalid_status_exits_1_with_actionable_error(self, tmp_path):
        _write_plan(tmp_path, [
            {"name": "security-reviewer", "status": "DISPATCHED"},
        ])

        cmd = [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)

        assert result.returncode == 1
        assert "security-reviewer" in result.stderr
        assert repr("DISPATCHED") in result.stderr


class TestDispatchStatusContract:
    def test_supported_statuses_partition_into_explicit_sets(self):
        assert dispatch_status.SKIPPED_STATUSES == frozenset({
            dispatch_status.SKIPPED,
            dispatch_status.SKIPPED_OVERRIDE,
            dispatch_status.SKIPPED_QUICK_MODE,
            dispatch_status.SKIPPED_TRIAGE,
        })
        assert dispatch_status.SUPPORTED_DISPATCH_STATUSES == (
            dispatch_status.DISPATCHED_STATUSES
            | dispatch_status.SKIPPED_STATUSES
        )

    @pytest.mark.parametrize(
        "status",
        [
            "DISPATCH",
            "DISPATCH_OVERRIDE",
            "SKIPPED",
            "SKIPPED_OVERRIDE",
            "SKIPPED_QUICK_MODE",
            "SKIPPED_TRIAGE",
        ],
    )
    def test_validator_accepts_each_supported_status(self, status):
        agents = [{"name": "code-reviewer", "status": status}]

        assert dispatch_status.validate_dispatch_plan_agents(agents) == agents

    @pytest.mark.parametrize(
        "agents",
        [
            None,
            {},
            "code-reviewer",
        ],
    )
    def test_validator_rejects_non_list_agents(self, agents):
        with pytest.raises(ValueError) as exc_info:
            dispatch_status.validate_dispatch_plan_agents(agents)

        assert repr(agents) in str(exc_info.value)

    @pytest.mark.parametrize("entry", [None, "code-reviewer", []])
    def test_validator_rejects_non_dict_entries_with_index(self, entry):
        with pytest.raises(ValueError) as exc_info:
            dispatch_status.validate_dispatch_plan_agents([entry])

        assert "index 0" in str(exc_info.value)
        assert repr(entry) in str(exc_info.value)

    @pytest.mark.parametrize(
        "name",
        [None, "", [], {}],
    )
    def test_validator_rejects_invalid_names_with_index_and_value(self, name):
        with pytest.raises(ValueError) as exc_info:
            dispatch_status.validate_dispatch_plan_agents([
                {"name": name, "status": "DISPATCH"},
            ])

        assert "index 0" in str(exc_info.value)
        assert repr(name) in str(exc_info.value)

    @pytest.mark.parametrize(
        "status,expected_repr",
        [
            pytest.param("__missing__", repr(None), id="missing"),
            pytest.param(None, repr(None), id="null"),
            pytest.param("", repr(""), id="empty"),
            pytest.param([], repr([]), id="structured-list"),
            pytest.param(
                {"state": "DISPATCH"},
                repr({"state": "DISPATCH"}),
                id="structured-dict",
            ),
            pytest.param("DISPATCHED", repr("DISPATCHED"), id="unknown"),
        ],
    )
    def test_validator_rejects_invalid_status_with_agent_and_repr(
        self, status, expected_repr
    ):
        agent = {"name": "security-reviewer"}
        if status != "__missing__":
            agent["status"] = status

        with pytest.raises(ValueError) as exc_info:
            dispatch_status.validate_dispatch_plan_agents([agent])

        message = str(exc_info.value)
        assert "security-reviewer" in message
        assert expected_repr in message


class TestExplicitSkippedFormatting:
    @pytest.mark.parametrize(
        "status",
        [
            "SKIPPED",
            "SKIPPED_OVERRIDE",
            "SKIPPED_QUICK_MODE",
            "SKIPPED_TRIAGE",
        ],
    )
    def test_formats_each_supported_skipped_status(self, mod, status):
        result = {
            "all_done": True,
            "dispatched": 0,
            "finished": 0,
            "running": 0,
            "timed_out": 0,
            "not_dispatched": 0,
            "skipped": 1,
            "agents": [
                {"name": "code-reviewer", "status": status, "reason": "not needed"},
            ],
        }

        output = mod.format_output(result)

        assert status in output
        assert "not needed" in output

    def test_does_not_format_unknown_skip_prefix_as_skipped(self, mod):
        result = {
            "all_done": True,
            "dispatched": 0,
            "finished": 0,
            "running": 0,
            "timed_out": 0,
            "not_dispatched": 0,
            "skipped": 0,
            "agents": [
                {
                    "name": "code-reviewer",
                    "status": "SKIPPED_FOREVER",
                    "reason": "unsupported",
                },
            ],
        }

        output = mod.format_output(result)

        assert "SKIPPED_FOREVER" not in output
        assert "unsupported" not in output


class TestNotDispatchedDoesNotBlockPipeline:
    """NOT_DISPATCHED agents must not block ALL_DONE or trigger ACTION REQUIRED."""

    def test_not_dispatched_does_not_block_all_done(self, mod, tmp_path):
        """Pipeline should proceed even if some DISPATCH agents never started."""
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")
        # security-reviewer: plan says DISPATCH but never started (triaged out)

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True, (
            "NOT_DISPATCHED should not block ALL_DONE — pipeline must not hang "
            "waiting for agents that will never start"
        )
        assert result["not_dispatched"] == 1
        assert result["finished"] == 1

    def test_not_dispatched_shown_as_note_not_action_required(self, mod, tmp_path):
        """NOT_DISPATCHED should produce a NOTE, not ACTION REQUIRED."""
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "dead-code-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")

        result = mod.check_status(str(tmp_path))
        output = mod.format_output(result)
        assert "ACTION REQUIRED" not in output
        assert "NOTE" in output or "not dispatched" in output.lower()

    def test_all_not_dispatched_still_all_done(self, mod, tmp_path):
        """Even if ALL agents are NOT_DISPATCHED, pipeline should not hang."""
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        # Neither agent started

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True


class TestFilenameConvention:
    """Status check must find files using the reviewer name, not the agent name."""

    def test_finds_review_file_with_reviewer_name(self, mod, tmp_path):
        """security-reviewer agent writes security-review.json — status should be FINISHED."""
        plan = {"agents": [{"name": "security-reviewer", "status": "DISPATCH"}]}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        review = {"issues": [], "verdict": "approve"}
        (tmp_path / "security-review.json").write_text(json.dumps(review))

        result = mod.check_status(str(tmp_path))
        agent = result["agents"][0]
        assert agent["status"] == "FINISHED", (
            f"Expected FINISHED but got {agent['status']}. "
            f"Status check is looking for the wrong filename."
        )

    def test_agent_without_reviewer_suffix(self, mod, tmp_path):
        """code-reviewer → code-review.json (same convention applies)."""
        plan = {"agents": [{"name": "code-reviewer", "status": "DISPATCH"}]}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        review = {"issues": [{"title": "Bug", "file": "a.py", "severity": "high"}], "verdict": "request_changes"}
        (tmp_path / "code-review.json").write_text(json.dumps(review))

        result = mod.check_status(str(tmp_path))
        agent = result["agents"][0]
        assert agent["status"] == "FINISHED"

    def test_non_reviewer_agent_name_unchanged(self, mod, tmp_path):
        """Agent names not ending in -reviewer use the name as-is."""
        plan = {"agents": [{"name": "gemini-reviewer", "status": "DISPATCH"}]}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        review = {"issues": [], "verdict": "approve"}
        (tmp_path / "gemini-review.json").write_text(json.dumps(review))

        result = mod.check_status(str(tmp_path))
        agent = result["agents"][0]
        assert agent["status"] == "FINISHED"


class TestIssuesKey:
    """Status check must read the 'issues' key, not 'findings'."""

    def test_reads_issues_key(self, mod, tmp_path):
        """ReviewOutputBuilder emits 'issues', not 'findings'."""
        plan = {"agents": [{"name": "security-reviewer", "status": "DISPATCH"}]}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        review = {
            "issues": [
                {"title": "XSS", "file": "a.php", "severity": "critical"},
                {"title": "CSRF", "file": "b.php", "severity": "high"},
            ],
            "verdict": "block",
        }
        (tmp_path / "security-review.json").write_text(json.dumps(review))

        result = mod.check_status(str(tmp_path))
        agent = result["agents"][0]
        assert agent["status"] == "FINISHED"
        assert agent["counts"]["critical"] == 1
        assert agent["counts"]["high"] == 1


class TestOverrideStatuses:
    """SKIPPED_OVERRIDE and DISPATCH_OVERRIDE must be handled correctly."""

    def test_skipped_override_treated_as_skip(self, mod, tmp_path):
        """SKIPPED_OVERRIDE agent should be counted as skipped, not dispatched."""
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "a11y-reviewer", "status": "SKIPPED_OVERRIDE", "reason": "LLM override: no UI changes"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True
        assert result["skipped"] == 1
        assert result["dispatched"] == 1
        assert result["not_dispatched"] == 0

    def test_skipped_override_shown_in_output(self, mod, tmp_path):
        """format_output should show SKIPPED_OVERRIDE with reason, not NOT_DISPATCHED."""
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "a11y-reviewer", "status": "SKIPPED_OVERRIDE", "reason": "LLM override: no UI changes"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")

        result = mod.check_status(str(tmp_path))
        output = mod.format_output(result)
        assert "SKIPPED_OVERRIDE" in output
        assert "LLM override: no UI changes" in output
        assert "NOT_DISPATCHED" not in output

    def test_dispatch_override_treated_as_dispatch(self, mod, tmp_path):
        """DISPATCH_OVERRIDE agent should be counted as dispatched and FINISHED when review file exists."""
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "perf-reviewer", "status": "DISPATCH_OVERRIDE"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")
        _start_agent(tmp_path, "perf-reviewer")
        _finish_agent(tmp_path, "perf-reviewer")

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True
        assert result["dispatched"] == 2
        assert result["finished"] == 2

    def test_multiple_override_statuses_mixed(self, mod, tmp_path):
        """Mix of DISPATCH, SKIPPED, SKIPPED_OVERRIDE, DISPATCH_OVERRIDE all handled correctly."""
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "SKIPPED", "reason": "no security-relevant files"},
            {"name": "a11y-reviewer", "status": "SKIPPED_OVERRIDE", "reason": "LLM override: no frontend"},
            {"name": "perf-reviewer", "status": "DISPATCH_OVERRIDE"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")
        _start_agent(tmp_path, "perf-reviewer")
        _finish_agent(tmp_path, "perf-reviewer")

        result = mod.check_status(str(tmp_path))
        assert result["all_done"] is True
        assert result["dispatched"] == 2   # DISPATCH + DISPATCH_OVERRIDE
        assert result["finished"] == 2
        assert result["skipped"] == 2      # SKIPPED + SKIPPED_OVERRIDE
        assert result["not_dispatched"] == 0


class TestWaitMode:
    """--wait / --max-seconds: script-owned polling. See module docstring for
    the exit-code contract (0/2/1 unchanged, 3 added for --wait expiry)."""

    def test_wait_returns_zero_immediately_when_all_done(self, mod, tmp_path):
        """Already-satisfied status must not pay the poll-interval sleep."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")

        start = time.monotonic()
        result, expired = mod.wait_for_all_done(str(tmp_path), max_seconds=30)
        elapsed = time.monotonic() - start

        assert result["all_done"] is True
        assert expired is False
        # DEFAULT_POLL_INTERVAL_SECONDS is 1.5s — a real sleep would show up here.
        assert elapsed < 1.0, f"wait_for_all_done slept when already done ({elapsed}s)"

    def test_wait_exit_3_on_expiry(self, tmp_path):
        """Unfinished agent + a short --max-seconds must exit 3, not 0/1/2."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")
        # No review file written — code-reviewer stays RUNNING forever.

        cmd = [
            sys.executable, str(SCRIPT_PATH),
            "--output-dir", str(tmp_path),
            "--wait", "--max-seconds", "1",
        ]
        # Generous subprocess-level timeout: if a mutation makes --wait ignore
        # --max-seconds (never expiring), this call hangs. A bounded
        # subprocess timeout turns that hang into a clean test failure
        # (TimeoutExpired) instead of blocking the suite forever.
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        assert r.returncode == 3
        assert "EXPIRED" in r.stderr

    def test_wait_wakes_on_completion(self, mod, tmp_path):
        """Completion mid-wait must be observed well before --max-seconds."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")

        def _finish_late():
            time.sleep(2)
            _finish_agent(tmp_path, "code-reviewer")

        thread = threading.Thread(target=_finish_late)
        thread.start()
        start = time.monotonic()
        result, expired = mod.wait_for_all_done(str(tmp_path), max_seconds=30)
        elapsed = time.monotonic() - start
        thread.join()

        assert result["all_done"] is True
        assert expired is False
        assert elapsed < 10, f"wait_for_all_done did not wake promptly ({elapsed}s)"

    def test_wait_requires_max_seconds(self, tmp_path):
        """--wait without --max-seconds refuses to block unbounded."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])

        cmd = [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path), "--wait"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        assert r.returncode == 1
        assert "--max-seconds" in r.stderr

    def test_no_wait_paths_unchanged(self, tmp_path):
        """The no-wait CLI path keeps its pinned 0/2 exit codes.

        Error-path exit 1 is already pinned at the CLI level by
        TestCheckStatus.test_no_dispatch_plan_exits_1 and
        test_invalid_status_exits_1_with_actionable_error; check_status()'s
        all_done computation itself is exercised directly by every test in
        TestCheckStatus / TestNotDispatchedDoesNotBlockPipeline /
        TestOverrideStatuses. This closes the one CLI-level gap: no existing
        test invoked main() end-to-end for the success/still-running cases.
        """
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")

        cmd = [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        assert r.returncode == 0
        assert "ALL_DONE: true" in r.stdout

        still_running_dir = tmp_path / "running"
        still_running_dir.mkdir()
        _write_plan(still_running_dir, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(still_running_dir, "code-reviewer")

        cmd = [sys.executable, str(SCRIPT_PATH), "--output-dir", str(still_running_dir)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        assert r.returncode == 2
        assert "ALL_DONE: false" in r.stdout

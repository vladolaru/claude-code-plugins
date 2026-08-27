"""Tests for review/agents_status.py."""

import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "review" / "agents_status.py"
sys.path.insert(0, str(TESTS_DIR))

from review import dispatch_status
from review import synthesis_lifecycle
from review.agent.output import ReviewOutputBuilder, finalize_review
from review.reconciliation_context import load_agent_reviews
from review.reviewer_lifecycle import ReviewPaths
from helpers.review_fixtures import canonical_review_document


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


def _finish_agent(tmp_path, name, findings=None, verdict=None):
    severities = [finding["severity"] for finding in findings or []]
    reviewer = _reviewer_filename(name)[: -len("-review.json")]
    review = canonical_review_document(reviewer, severities)
    if verdict is not None:
        assert review["verdict"] == verdict
    (tmp_path / _reviewer_filename(name)).write_text(json.dumps(review))


def _write_assignment(tmp_path, reviewer, agent_name, claimable_files):
    (tmp_path / f"{reviewer}-assignment.json").write_text(json.dumps({
        "schema": 4,
        "agent_name": agent_name,
        "reviewer": reviewer,
        "review_claimable_files": claimable_files,
        "review_budget": 15,
        "inline_diff_file_count": 1,
        "in_scope_review_file_count": 1 + len(claimable_files),
        "channels": ["blocking"],
    }))


class _FakeClock:
    """Deterministic now_fn/sleep_fn pair for wait_for_all_done tests.

    now_fn() returns the current fake time. sleep_fn(seconds) advances the
    fake clock by exactly the requested amount and records it in .sleeps —
    so a test can assert both how long each requested sleep was and how
    many were requested, with zero dependency on real wall-clock timing
    (no flaky elapsed-time thresholds, no real `time.sleep`).

    Guards against runaway loops: a mutation that disables the
    --max-seconds expiry check would otherwise spin forever here (a fake
    sleep never actually blocks), and unlike a subprocess call, an
    in-process infinite loop has no timeout to kill it. The guard raises
    once the loop clearly isn't converging, turning a hang into a fast,
    clean test failure.
    """

    _MAX_SLEEPS = 200

    def __init__(self, start=0.0):
        self.now = start
        self.sleeps = []

    def now_fn(self):
        return self.now

    def sleep_fn(self, seconds):
        self.sleeps.append(seconds)
        if len(self.sleeps) > self._MAX_SLEEPS:
            raise AssertionError(
                f"wait_for_all_done slept {len(self.sleeps)} times without "
                "expiring or finishing — runaway loop (--max-seconds check "
                "disabled?)"
            )
        self.now += seconds


class TestCheckStatus:
    def test_final_status_follows_the_review_paths_authority(
        self, mod, tmp_path, monkeypatch
    ):
        _write_plan(tmp_path, [
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        authority_dir = tmp_path / "authority"
        authority_dir.mkdir()
        final_path = authority_dir / "final.json"
        final_path.write_text(json.dumps(canonical_review_document("security")))
        monkeypatch.setattr(
            mod,
            "review_paths",
            lambda *_args: ReviewPaths(
                draft=str(authority_dir / "draft.json"),
                final=str(final_path),
                assignment=str(authority_dir / "authority.json"),
            ),
        )

        result = mod.check_status(str(tmp_path))

        assert result["agents"][0]["status"] == "FINISHED"
        assert result["all_done"] is True

    def test_draft_evidence_does_not_replace_execution_status(
        self, mod, tmp_path
    ):
        _write_plan(tmp_path, [
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        _write_assignment(
            tmp_path, "security", "security-reviewer", []
        )
        _start_agent(tmp_path, "security-reviewer", minutes_ago=60)
        ReviewOutputBuilder.open(
            tmp_path, "42", "security"
        ).save_draft()

        status = mod.attach_draft_evidence(
            str(tmp_path), mod.check_status(str(tmp_path), timeout_seconds=0)
        )
        agent = status["agents"][0]

        assert agent["status"] == "TIMED_OUT"
        assert agent["draft_available"] is True

    def test_all_finished(self, mod, tmp_path):
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _start_agent(tmp_path, "security-reviewer")
        _finish_agent(tmp_path, "code-reviewer", [{"severity": "critical"}], "block")
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

    def test_replaceable_drafts_do_not_finish_until_exact_finalization(
        self, mod, tmp_path
    ):
        """Removing the final-only branch would let draft A or B
        race reconciliation; this sequence pins the final B snapshot all
        the way through the real reconciliation loader."""
        _write_plan(tmp_path, [
            {"name": "a11y-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "a11y-reviewer")
        _write_assignment(
            tmp_path, "a11y", "a11y-reviewer", ["src/late.ts"]
        )
        builder = ReviewOutputBuilder.open(str(tmp_path), "13", "a11y")

        first = builder.save_draft()
        first_status = mod.attach_draft_evidence(
            str(tmp_path), mod.check_status(str(tmp_path))
        )
        assert first_status["all_done"] is False
        assert first_status["agents"][0]["status"] == "RUNNING"
        assert first_status["agents"][0]["draft_available"] is True
        assert first_status["agents"][0]["draft_digest"] == (
            first["review_digest"]
        )

        builder.claim_files_reviewed("src/late.ts")
        second = builder.save_draft()
        second_status = mod.attach_draft_evidence(
            str(tmp_path), mod.check_status(str(tmp_path))
        )
        assert second_status["all_done"] is False
        assert second_status["agents"][0]["draft_digest"] == (
            second["review_digest"]
        )
        assert first["review_digest"] != second["review_digest"]
        formatted = mod.format_output(second_status)
        assert f"DRAFT  digest={second['review_digest']}" in formatted
        assert "FINALIZE_REVIEW_COMMAND:" in formatted
        assert (
            second_status["agents"][0]["finalize_review_command"] in formatted
        )

        finalize_review(
            str(tmp_path), "a11y", second["review_digest"]
        )
        assert mod.check_status(str(tmp_path))["all_done"] is True
        reviews = load_agent_reviews(
            str(tmp_path), dispatched_agents=["a11y-reviewer"]
        )
        assert reviews["a11y-review"]["reviewed_file_claims"] == [
            "src/late.ts"
        ]

    def test_timed_out_draft_stays_timed_out_with_finalize_evidence(
        self, mod, tmp_path
    ):
        """Draft evidence must enrich TIMED_OUT, not create a new
        terminal status or turn it back into RUNNING."""
        _write_plan(tmp_path, [
            {"name": "slow-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "slow-reviewer", minutes_ago=25)
        draft = tmp_path / "slow-review.draft.json"
        draft.write_bytes(b'{"snapshot":"late"}')

        result = mod.attach_draft_evidence(
            str(tmp_path), mod.check_status(str(tmp_path))
        )

        assert result["all_done"] is True
        assert result["timed_out"] == 1
        [slow] = result["agents"]
        assert slow["status"] == "TIMED_OUT"
        assert slow["draft_available"] is True
        assert len(slow["draft_digest"]) == 64
        assert "--reviewer slow" in slow["finalize_review_command"]
        assert (
            f"--review-digest {slow['draft_digest']}"
            in slow["finalize_review_command"]
        )

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
        assert result["dispatched_names"] == ["code-reviewer"]

    def test_dispatched_names_carry_plan_order(self, mod, tmp_path):
        """Step 8 freezes intake from these names, so order is the contract."""
        _write_plan(tmp_path, [
            {"name": "security-reviewer", "status": "DISPATCH"},
            {"name": "a11y-reviewer", "status": "SKIPPED_TRIAGE", "reason": "x"},
            {"name": "code-reviewer", "status": "DISPATCH_OVERRIDE"},
        ])

        result = mod.check_status(str(tmp_path))
        assert result["dispatched_names"] == [
            "security-reviewer", "code-reviewer",
        ]
        assert result["dispatched"] == len(result["dispatched_names"])

    def test_dispatched_names_empty_when_plan_selected_nobody(
        self, mod, tmp_path
    ):
        """An empty list is a known-empty set, not unknown dispatch."""
        _write_plan(tmp_path, [
            {"name": "a11y-reviewer", "status": "SKIPPED", "reason": "docs"},
        ])

        result = mod.check_status(str(tmp_path))
        assert result["dispatched_names"] == []

    def test_extracts_severity_counts(self, mod, tmp_path):
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer", [
            {"severity": "critical"},
            {"severity": "high"},
            {"severity": "high"},
            {"severity": "medium"},
        ], "block")

        result = mod.check_status(str(tmp_path))
        agent = result["agents"][0]
        assert agent["counts"]["critical"] == 1
        assert agent["counts"]["high"] == 2
        assert agent["verdict"] == "block"

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

        review = canonical_review_document("security")
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

        review = canonical_review_document("code", ["high"])
        (tmp_path / "code-review.json").write_text(json.dumps(review))

        result = mod.check_status(str(tmp_path))
        agent = result["agents"][0]
        assert agent["status"] == "FINISHED"

    def test_non_reviewer_agent_name_unchanged(self, mod, tmp_path):
        """Agent names not ending in -reviewer use the name as-is."""
        plan = {"agents": [{"name": "gemini-reviewer", "status": "DISPATCH"}]}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        review = canonical_review_document("gemini")
        (tmp_path / "gemini-review.json").write_text(json.dumps(review))

        result = mod.check_status(str(tmp_path))
        agent = result["agents"][0]
        assert agent["status"] == "FINISHED"


class TestFindingsKey:
    """Status check reads the canonical findings collection."""

    def test_reads_findings_key(self, mod, tmp_path):
        """ReviewOutputBuilder emits findings with canonical severities."""
        plan = {"agents": [{"name": "security-reviewer", "status": "DISPATCH"}]}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        review = canonical_review_document("security", ["critical", "high"])
        (tmp_path / "security-review.json").write_text(json.dumps(review))

        result = mod.check_status(str(tmp_path))
        agent = result["agents"][0]
        assert agent["status"] == "FINISHED"
        assert agent["counts"]["critical"] == 1
        assert agent["counts"]["high"] == 1

    def test_retired_review_is_terminal_process_evidence_not_finished_content(
        self, mod, tmp_path
    ):
        _write_plan(tmp_path, [
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        (tmp_path / "security-review.json").write_text(json.dumps({
            "schema": 1,
            "reviewer": "security",
            "issues": [],
            "verdict": "approve",
        }))

        result = mod.check_status(str(tmp_path))

        assert result["all_done"] is True
        assert result["finished"] == 0
        assert result["invalid"] == 1
        assert result["agents"][0] == {
            "name": "security-reviewer",
            "status": "INVALID_OUTPUT",
            "output_present": True,
            "note": "final review failed canonical validation",
        }


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
    the exit-code contract (0/2/1 unchanged, 3 added for --wait expiry).

    Timing-sensitive properties (expiry-before-sleep, the final-sleep
    clamp, waking on the very next poll) are pinned deterministically via
    `_FakeClock`/injected `sleep_fn` — no real wall-clock elapsed-time
    thresholds, so no flakiness.

    The subprocess tests are NOT restatements of those: the exit codes
    are the contract the step-7/8 briefings teach the orchestrator by
    number, so the CLI is their unit level. One cheap ALL_DONE smoke
    covers exit 0 and the `--wait` wiring; `test_wait_exit_3_on_expiry`
    covers expiry; `test_no_wait_paths_unchanged` covers 0/2. A real
    threaded completion is not spawned a second time here — the
    "observed on the very next poll" property is what mattered, and
    `test_wait_wakes_on_completion` pins it deterministically.
    """

    def test_the_wait_loop_hashes_each_draft_once(
        self, mod, tmp_path, monkeypatch
    ):
        """Draft evidence is a presentation fact, computed when the wait ends.

        check_status sha256'd every running agent's draft bytes on every
        1.5s tick and threw all but the last result away.
        """
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _write_assignment(tmp_path, "code", "code-reviewer", [])
        _start_agent(tmp_path, "code-reviewer")
        ReviewOutputBuilder.open(tmp_path, "42", "code").save_draft()
        digested = []
        original = mod.draft_evidence
        monkeypatch.setattr(
            mod, "draft_evidence",
            lambda output_dir, name: digested.append(name)
            or original(output_dir, name),
        )

        clock = _FakeClock()
        result, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=6.0,
            sleep_fn=clock.sleep_fn, now_fn=clock.now_fn,
        )
        mod.attach_draft_evidence(str(tmp_path), result)

        assert expired is True
        assert digested == ["code-reviewer"]
        assert result["agents"][0]["draft_available"] is True

    def test_wait_returns_zero_immediately_when_all_done(self, mod, tmp_path):
        """Already-satisfied status must not sleep at all."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")

        clock = _FakeClock()
        result, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=30,
            sleep_fn=clock.sleep_fn, now_fn=clock.now_fn,
        )

        assert result["all_done"] is True
        assert expired is False
        assert clock.sleeps == [], "wait_for_all_done slept when already done"

    def test_wait_all_done_cli_smoke(self, tmp_path):
        """Cheap end-to-end smoke: the CLI wires --wait through for real."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")

        cmd = [
            sys.executable, str(SCRIPT_PATH),
            "--output-dir", str(tmp_path), "--wait", "--max-seconds", "30",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        assert r.returncode == 0
        assert "ALL_DONE: true" in r.stdout

    def test_wait_checks_expiry_before_sleeping(self, mod, tmp_path):
        """The poll that lands exactly on expiry must return WITHOUT
        sleeping again — a mutation that sleeps unconditionally before
        checking the remaining budget would add an extra sleep here."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")
        # Never finished — stays RUNNING for the whole fake-clock window.

        clock = _FakeClock()
        result, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=3.0, poll_interval=1.5,
            sleep_fn=clock.sleep_fn, now_fn=clock.now_fn,
        )

        assert expired is True
        assert result["all_done"] is False
        # 3 checks (t=0, 1.5, 3.0) but only 2 sleeps: the third check lands
        # exactly on expiry and returns instead of sleeping a third time.
        assert clock.sleeps == [1.5, 1.5]

    def test_wait_clamps_final_sleep_to_remaining(self, mod, tmp_path):
        """The last sleep before expiry must be clamped to whatever time is
        actually left, not the full poll_interval — otherwise every wait
        can overshoot --max-seconds by up to one poll grain."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")

        clock = _FakeClock()
        result, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=4.0, poll_interval=1.5,
            sleep_fn=clock.sleep_fn, now_fn=clock.now_fn,
        )

        assert expired is True
        # t=0 rem=4.0 -> sleep 1.5; t=1.5 rem=2.5 -> sleep 1.5; t=3.0
        # rem=1.0 -> sleep clamped to 1.0, not the full 1.5s poll_interval.
        assert clock.sleeps == [1.5, 1.5, 1.0]

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

    def test_wait_expired_status_flushed_before_stderr(self, tmp_path):
        """On expiry, the status table (stdout) must precede EXPIRED
        (stderr) in a MERGED stream — a caller that captures both on one
        pipe (e.g. a Codex subprocess) must never see them interleaved out
        of order."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")

        cmd = [
            sys.executable, str(SCRIPT_PATH),
            "--output-dir", str(tmp_path),
            "--wait", "--max-seconds", "1",
        ]
        r = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=15,
        )
        assert r.returncode == 3
        merged = r.stdout
        assert merged.index("ALL_DONE:") < merged.index("EXPIRED:")

    def test_wait_wakes_on_completion(self, mod, tmp_path):
        """Completion must be observed on the very first poll after it
        happens — the loop has to re-check status on every iteration, not
        return after a single pass."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")

        clock = _FakeClock()
        calls = {"n": 0}

        def sleep_fn(seconds):
            calls["n"] += 1
            assert calls["n"] <= 200, "runaway loop — completion never observed"
            clock.now += seconds
            if calls["n"] == 2:
                # Simulate the reviewer finishing partway through the wait.
                _finish_agent(tmp_path, "code-reviewer")

        result, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=30, poll_interval=1.5,
            sleep_fn=sleep_fn, now_fn=clock.now_fn,
        )

        assert result["all_done"] is True
        assert expired is False
        # The check_status() call right after the 2nd sleep is the one
        # that observes the finish — proves every iteration re-checks.
        assert calls["n"] == 2

    def test_wait_requires_max_seconds(self, tmp_path):
        """--wait without --max-seconds refuses to block unbounded."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])

        cmd = [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path), "--wait"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        assert r.returncode == 1
        assert "--max-seconds" in r.stderr

    def test_max_seconds_requires_wait(self, tmp_path):
        """--max-seconds without --wait is rejected, not silently ignored."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])

        cmd = [
            sys.executable, str(SCRIPT_PATH),
            "--output-dir", str(tmp_path), "--max-seconds", "30",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        assert r.returncode == 1
        assert "--wait" in r.stderr

    @pytest.mark.parametrize("value", ["0", "-5"])
    def test_max_seconds_must_be_positive(self, tmp_path, value):
        """--max-seconds must be > 0 — 0 or negative is rejected loudly
        rather than producing a wait that expires instantly or never."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])

        cmd = [
            sys.executable, str(SCRIPT_PATH),
            "--output-dir", str(tmp_path), "--wait", "--max-seconds", value,
        ]
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


class TestSynthesisMarkersAreInvisible:
    """The synthesis agents are measured elsewhere and must not leak here.

    TWO independent guards, and this class keeps both honest even though
    either alone would suffice today.

    1. NAMESPACING (primary, since the markers were renamed). Synthesis
       markers end `.synthesis-started`, not `.started`, so a directory
       scan for the reviewer suffix cannot see them at all. That is what
       enforces the separation now: pirategoat-bot's resume path ran
       exactly such a scan and treated every hit as a reviewer, seeding
       both synthesis agents as permanently NOT_DISPATCHED and renaming
       their markers away as orphans. A name list maintained by hand in
       another repo is a contract nobody enforces; the suffix is one
       nobody has to.

    2. DISPATCH-PLAN ITERATION (this module's own guard). agents_status
       reports on the agents in `dispatch-plan.json` and nothing else,
       and neither synthesis agent is ever in one.

    Namespacing makes the first test near-trivial, which is the point —
    the invariant should be cheap to hold. The second test deliberately
    plants the OLD, reviewer-suffixed names to prove guard 2 still stands
    alone, so a future revert of the suffix cannot silently take both
    guards down at once.
    """

    SYNTHESIS_MARKERS = (
        synthesis_lifecycle.RECONCILIATOR,
        synthesis_lifecycle.DECISION_CRITIC,
    )

    def _plant(self, tmp_path):
        """Real markers, through the production writer."""
        for name in self.SYNTHESIS_MARKERS:
            synthesis_lifecycle.mark_dispatched(str(tmp_path), name)

    def _plant_with_reviewer_suffix(self, tmp_path):
        """Synthesis names under the REVIEWER suffix — the pre-namespacing
        collision, simulated so guard 2 is pinned on its own."""
        for name in self.SYNTHESIS_MARKERS:
            _start_agent(tmp_path, name)

    def test_synthesis_markers_do_not_carry_the_reviewer_suffix(
        self, tmp_path
    ):
        self._plant(tmp_path)
        assert not list(tmp_path.glob("*.started"))
        assert len(list(tmp_path.glob("*.synthesis-started"))) == 2

    def test_dispatch_plan_iteration_holds_without_namespacing(
        self, mod, tmp_path
    ):
        """Guard 2, alone: even under the colliding old names, these
        agents cannot appear, because they are not in the plan."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")

        before = mod.check_status(str(tmp_path))
        self._plant_with_reviewer_suffix(tmp_path)

        assert mod.check_status(str(tmp_path)) == before

    def test_counts_unchanged_with_synthesis_markers_present(
        self, mod, tmp_path
    ):
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")
        _start_agent(tmp_path, "security-reviewer")

        before = mod.check_status(str(tmp_path))
        self._plant(tmp_path)
        after = mod.check_status(str(tmp_path))

        assert after == before
        assert [agent["name"] for agent in after["agents"]] == [
            "code-reviewer", "security-reviewer",
        ]

    def test_exit_code_unchanged_with_synthesis_markers_present(
        self, tmp_path
    ):
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")
        self._plant(tmp_path)

        r = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 0
        assert "ALL_DONE: true" in r.stdout
        for name in self.SYNTHESIS_MARKERS:
            assert name not in r.stdout

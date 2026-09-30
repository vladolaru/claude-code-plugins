"""Tests for review/agents_status.py."""

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "review" / "agents_status.py"
sys.path.insert(0, str(TESTS_DIR))

from review import dispatch_status
from review import run_paths
from review import synthesis_lifecycle
from review.agent.output import ReviewOutputBuilder, finalize_review
from review.reconciliation_context import load_agent_reviews
from review.reviewer_lifecycle import (
    record_bootstrap_error, review_paths, started_marker_path,
)
from review.reviewer_names import derive_reviewer_name
from helpers.review_fixtures import (
    canonical_assignment,
    canonical_review_document,
)


def _load_module():
    spec = importlib.util.spec_from_file_location("check_status", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def _write_plan(tmp_path, agents):
    path = run_paths.artifact_path(tmp_path, "dispatch_plan")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"agents": agents}))


def _start_agent(tmp_path, name, minutes_ago=0):
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    marker = Path(started_marker_path(tmp_path, derive_reviewer_name(name)))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(ts.isoformat())


def _start_agent_at(tmp_path, name, at):
    marker = Path(started_marker_path(tmp_path, derive_reviewer_name(name)))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(at.isoformat())


def _finish_agent(tmp_path, name, findings=None, verdict=None):
    severities = [finding["severity"] for finding in findings or []]
    reviewer = derive_reviewer_name(name)
    review = canonical_review_document(reviewer, severities)
    if verdict is not None:
        assert review["verdict"] == verdict
    path = Path(review_paths(tmp_path, reviewer).final)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(review))


def _write_assignment(tmp_path, reviewer, agent_name, claimable_files):
    path = Path(review_paths(tmp_path, reviewer).assignment)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        canonical_assignment(
            reviewer,
            agent_name=agent_name,
            review_claimable_files=claimable_files,
            inline_diff_file_count=1,
        )
    ))


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

    @staticmethod
    def _fail_bootstrap(tmp_path, name, error_output):
        """A failed bootstrap's record, through the production writer."""
        record_bootstrap_error(str(tmp_path), derive_reviewer_name(name), error_output)

    def test_a_failed_bootstrap_is_terminal_and_not_read_as_never_dispatched(
        self, mod, tmp_path
    ):
        """A reviewer whose bootstrap exited with STATUS: ERROR used to read
        as NOT_DISPATCHED, so the step-7 briefing dispatched it again into
        the same failure. Its failure record makes it BOOTSTRAP_ERROR:
        terminal, carrying the ERROR line, and told not to dispatch."""
        _write_plan(tmp_path, [
            {"name": "code-reviewer", "status": "DISPATCH"},
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        _start_agent(tmp_path, "code-reviewer")
        _finish_agent(tmp_path, "code-reviewer")
        self._fail_bootstrap(
            tmp_path, "security-reviewer",
            "ERROR: [config-ops] git diff timed out\nACTION: Report this error to the caller.",
        )

        result = mod.check_status(str(tmp_path))

        assert result["all_done"] is True
        assert result["bootstrap_error"] == 1 and result["not_dispatched"] == 0
        security = [a for a in result["agents"] if a["name"] == "security-reviewer"][0]
        assert security == {
            "name": "security-reviewer", "status": "BOOTSTRAP_ERROR",
            "error": "[config-ops] git diff timed out",
        }
        output = mod.format_output(result)
        # Every dispatched agent is counted in exactly one bucket.
        assert (
            "2 expected, 1 finished, 0 invalid, 0 running, 0 timed out, "
            "1 bootstrap errors, 0 never started"
        ) in output
        assert "BOOTSTRAP_ERROR ([config-ops] git diff timed out)" in output
        assert "do not dispatch them again: security-reviewer" in output

    def test_a_started_marker_supersedes_a_failure_record(self, mod, tmp_path):
        """Whichever was written first: an earlier failure the reviewer got
        past on a later dispatch, or a duplicate dispatch that failed while
        this one runs. Either way a reviewer got past bootstrap and is
        running."""
        _write_plan(tmp_path, [{"name": "security-reviewer", "status": "DISPATCH"}])
        self._fail_bootstrap(tmp_path, "security-reviewer", "ERROR: an earlier failure")
        _start_agent(tmp_path, "security-reviewer")

        result = mod.check_status(str(tmp_path))

        assert result["agents"][0]["status"] == "RUNNING"
        assert result["bootstrap_error"] == 0

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
        draft = Path(review_paths(tmp_path, "slow").draft)
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

    def test_invalid_status_exits_1_with_actionable_error(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        _write_plan(tmp_path, [
            {"name": "security-reviewer", "status": "DISPATCHED"},
        ])
        monkeypatch.setattr(
            sys, "argv",
            ["agents_status.py", "--output-dir", str(tmp_path)],
        )

        with pytest.raises(SystemExit) as exc:
            mod.main()

        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "security-reviewer" in err
        assert repr("DISPATCHED") in err

        # test_no_dispatch_plan_exits_1 folded in: same `except` clause,
        # FileNotFoundError from a missing dispatch plan also exits 1.
        no_plan_dir = tmp_path / "no-plan"
        no_plan_dir.mkdir()
        monkeypatch.setattr(
            sys, "argv",
            ["agents_status.py", "--output-dir", str(no_plan_dir)],
        )

        with pytest.raises(SystemExit) as exc2:
            mod.main()

        assert exc2.value.code == 1


class TestDispatchStatusContract:
    def test_validator_accepts_each_supported_status(self):
        agents = [{"name": "code-reviewer", "status": "DISPATCH"}]

        assert dispatch_status.validate_dispatch_plan_agents(agents) == agents

    def test_validator_rejects_non_list_agents(self):
        with pytest.raises(ValueError):
            dispatch_status.validate_dispatch_plan_agents(None)

    def test_validator_rejects_non_dict_entries_with_index(self):
        with pytest.raises(ValueError) as exc_info:
            dispatch_status.validate_dispatch_plan_agents([None])

        assert "index 0" in str(exc_info.value)

    def test_validator_rejects_invalid_names_with_index_and_value(self):
        with pytest.raises(ValueError) as exc_info:
            dispatch_status.validate_dispatch_plan_agents([
                {"name": None, "status": "DISPATCH"},
            ])

        assert "index 0" in str(exc_info.value)

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param("__missing__", id="missing"),
            pytest.param("DISPATCHED", id="unknown"),
        ],
    )
    def test_validator_rejects_invalid_status_with_agent_and_repr(self, status):
        agent = {"name": "security-reviewer"}
        if status != "__missing__":
            agent["status"] = status

        with pytest.raises(ValueError) as exc_info:
            dispatch_status.validate_dispatch_plan_agents([agent])

        assert "security-reviewer" in str(exc_info.value)


_STAMP = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _rows(*names, status="DISPATCH"):
    return [{"name": name, "status": status} for name in names]


def _lists(state, keys=("queued", "pending")):
    """The named name lists of a queue_state() result."""
    return {key: state[key] for key in keys}


def _release(at, count=1, counted=0):
    """A `released` entry: latest release time, releases, counted releases."""
    return {"at": at, "count": count, "counted": counted}


def _waves(cap=2, wave_1=("a", "b"), queued=("c",), **overrides):
    record = {
        "schema": 1,
        "cap": cap,
        "cap_source": "claude_default",
        "wave_1": list(wave_1),
        "queued": list(queued),
        "stamped_at": _STAMP.isoformat(),
        "grace_seconds": 90,
    }
    record.update(overrides)
    return record


class TestDispatchWavesRecord:
    """The plan-level `dispatch_waves` record: builder, validator, reader, queue."""

    @pytest.mark.parametrize(
        "cap, wave_1, queued",
        [
            pytest.param(5, ["a", "b", "c"], [], id="under-cap"),
            pytest.param(3, ["a", "b", "c"], [], id="equal-cap"),
            pytest.param(2, ["a", "b"], ["c"], id="over-cap"),
            pytest.param(None, ["a", "b", "c"], [], id="unbounded"),
        ],
    )
    def test_builder_splits_dispatched_rows_at_the_cap(self, cap, wave_1, queued):
        record = dispatch_status.build_dispatch_waves(
            _rows("a", "b", "c"), cap, "claude_env", _STAMP.isoformat(),
        )

        assert record == {
            "schema": 1,
            "cap": cap,
            "cap_source": "claude_env",
            "wave_1": wave_1,
            "queued": queued,
            "stamped_at": _STAMP.isoformat(),
            "grace_seconds": dispatch_status.DISPATCH_WAVES_GRACE_SECONDS,
        }

    def test_builder_keeps_plan_order_and_excludes_skipped_rows(self):
        agents = [
            {"name": "zeta", "status": "DISPATCH"},
            {"name": "alpha", "status": "SKIPPED_TRIAGE"},
            {"name": "mid", "status": "DISPATCH_OVERRIDE"},
            {"name": "beta", "status": "SKIPPED_OVERRIDE"},
            {"name": "last", "status": "DISPATCH"},
        ]

        record = dispatch_status.build_dispatch_waves(
            agents, 2, "claude_default", _STAMP.isoformat(),
        )

        assert record["wave_1"] == ["zeta", "mid"]
        assert record["queued"] == ["last"]

    def test_validator_accepts_a_built_record(self):
        record = dispatch_status.build_dispatch_waves(
            _rows("a", "b", "c"), 2, "claude_default", _STAMP.isoformat(),
        )

        assert dispatch_status.validate_dispatch_waves(record) is record

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param(None, id="not-object"),
            pytest.param(_waves(schema=2), id="schema-2"),
            pytest.param(_waves(schema=True), id="schema-bool"),
            pytest.param(_waves(cap=0), id="cap-zero"),
            pytest.param(_waves(cap=True), id="cap-bool"),
            pytest.param(_waves(cap_source="guess"), id="unknown-source"),
            pytest.param(_waves(queued=["a"]), id="overlap"),
            pytest.param(_waves(wave_1=["Bad Name"]), id="bad-name"),
            pytest.param({**_waves(), "queued": "c"}, id="queued-not-list"),
            pytest.param(_waves(stamped_at="yesterday"), id="bad-timestamp"),
            pytest.param(_waves(stamped_at=None), id="missing-timestamp"),
            pytest.param(_waves(grace_seconds=-1), id="negative-grace"),
        ],
    )
    def test_validator_rejects_each_bad_field(self, bad):
        with pytest.raises(ValueError):
            dispatch_status.validate_dispatch_waves(bad)

    def test_validator_accepts_unbounded_cap(self):
        record = _waves(cap=None, cap_source="unbounded", queued=[])

        assert dispatch_status.validate_dispatch_waves(record) is record

    def test_load_passes_legacy_and_valid_plans_through_unchanged(self, tmp_path):
        path = tmp_path / "dispatch-plan.json"
        for plan in (
            {"agents": _rows("a")},
            {"agents": _rows("a", "b", "c"), "dispatch_waves": _waves()},
        ):
            path.write_text(json.dumps(plan))

            assert dispatch_status.load_dispatch_plan(path) == plan

    def test_load_rejects_an_invalid_record_naming_the_file(self, tmp_path):
        path = tmp_path / "dispatch-plan.json"
        path.write_text(json.dumps({
            "agents": _rows("a"), "dispatch_waves": _waves(cap=0),
        }))

        with pytest.raises(ValueError, match="dispatch-plan.json"):
            dispatch_status.load_dispatch_plan(path)

    def test_queue_state_inside_grace_holds_wave_1_as_pending(self):
        state = dispatch_status.queue_state(
            _waves(), ["c", "b", "a"], _STAMP + timedelta(seconds=90),
        )

        assert _lists(state) == {"queued": ["c"], "pending": ["a", "b"]}

    def test_queue_state_past_grace_queues_wave_1_rejections_first(self):
        state = dispatch_status.queue_state(
            _waves(), ["c", "b"], _STAMP + timedelta(seconds=91),
        )

        assert _lists(state) == {"queued": ["b", "c"], "pending": []}

    def test_queue_state_ignores_started_reviewers(self):
        state = dispatch_status.queue_state(
            _waves(), [], _STAMP + timedelta(seconds=10),
        )

        assert state == {
            "queued": [], "pending": [], "abandoned": [], "attempts": {},
        }

    def test_queue_state_for_a_legacy_plan_is_empty(self):
        assert dispatch_status.queue_state(None, ["a"], _STAMP) == {
            "queued": [], "pending": [], "abandoned": [], "attempts": {},
        }


class TestExplicitSkippedFormatting:
    def test_formats_each_supported_skipped_status(self, mod):
        status = "SKIPPED_OVERRIDE"
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


class TestFindingsKey:
    """Status check reads the canonical findings collection."""

    def test_reads_findings_key(self, mod, tmp_path):
        """ReviewOutputBuilder emits findings with canonical severities."""
        plan = {"agents": [{"name": "security-reviewer", "status": "DISPATCH"}]}
        _write_plan(tmp_path, plan["agents"])

        review = canonical_review_document("security", ["critical", "high"])
        path = Path(review_paths(tmp_path, "security").final)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(review))

        result = mod.check_status(str(tmp_path))
        agent = result["agents"][0]
        assert agent["status"] == "FINISHED"
        assert agent["counts"]["critical"] == 1
        assert agent["counts"]["high"] == 1
        assert agent["verdict"] == "block"

    def test_retired_review_is_terminal_process_evidence_not_finished_content(
        self, mod, tmp_path
    ):
        _write_plan(tmp_path, [
            {"name": "security-reviewer", "status": "DISPATCH"},
        ])
        path = Path(review_paths(tmp_path, "security").final)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
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
    covers exit 0 and the `--wait` wiring;
    `test_wait_expired_status_flushed_before_stderr` covers expiry (exit
    3, EXPIRED on stderr, and stdout-before-stderr in a merged pipe);
    `test_no_wait_paths_unchanged` covers 0/2. A real threaded completion
    is not spawned a second time here — the "observed on the very next
    poll" property is what mattered, and `test_wait_wakes_on_completion`
    pins it deterministically.
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

    def test_wait_expired_status_flushed_before_stderr(self, tmp_path):
        """On expiry, the status table (stdout) must precede EXPIRED
        (stderr) in a MERGED stream — a caller that captures both on one
        pipe (e.g. a Codex subprocess) must never see them interleaved out
        of order. This also covers exit 3 on expiry: block buffering under
        a pipe only exists in a real process, so it stays a subprocess
        test; --max-seconds is type=float, so 0.05 clamps the one sleep to
        50ms instead of a real second."""
        _write_plan(tmp_path, [{"name": "code-reviewer", "status": "DISPATCH"}])
        _start_agent(tmp_path, "code-reviewer")

        cmd = [
            sys.executable, str(SCRIPT_PATH),
            "--output-dir", str(tmp_path),
            "--wait", "--max-seconds", "0.05",
        ]
        r = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=15,
        )
        assert r.returncode == 3
        merged = r.stdout
        assert "EXPIRED" in merged
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

    @pytest.mark.parametrize("argv_tail,fragment", [
        pytest.param(["--wait"], "--max-seconds", id="wait-without-max"),
        pytest.param(["--max-seconds", "5"], "--wait", id="max-without-wait"),
        pytest.param(["--wait", "--max-seconds", "0"], "> 0", id="non-positive"),
    ])
    def test_wait_argument_guards_exit_1(
        self, mod, tmp_path, monkeypatch, capsys, argv_tail, fragment
    ):
        """--wait requires --max-seconds and vice versa, and --max-seconds
        must be > 0 — each guard refuses loudly rather than blocking
        unbounded or expiring instantly/never."""
        _write_plan(tmp_path, [])
        monkeypatch.setattr(
            sys, "argv",
            ["agents_status.py", "--output-dir", str(tmp_path), *argv_tail],
        )

        with pytest.raises(SystemExit) as exc:
            mod.main()

        assert exc.value.code == 1
        assert fragment in capsys.readouterr().err

    def test_no_wait_paths_unchanged(self, tmp_path):
        """The no-wait CLI path keeps its pinned 0/2 exit codes.

        Error-path exit 1 is already pinned at the CLI level by
        TestCheckStatus.test_invalid_status_exits_1_with_actionable_error
        (which covers both the invalid-status ValueError and the
        no-dispatch-plan FileNotFoundError, folded into it as a second
        in-process main() call); check_status()'s all_done computation
        itself is exercised directly by every test in TestCheckStatus /
        TestNotDispatchedDoesNotBlockPipeline / TestOverrideStatuses. This
        closes the one CLI-level gap: no existing test invoked main()
        end-to-end for the success/still-running cases.
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
        assert {
            run_paths.synthesis_started_marker(tmp_path, name)
            for name in self.SYNTHESIS_MARKERS
        } == set((tmp_path / "synthesis").glob("*.synthesis-started"))

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


def _write_wave_plan(tmp_path, names, cap, queued_from, stamped_at=None,
                     **record_extra):
    """A plan whose first `queued_from` dispatched names form wave 1.

    `record_extra` adds record keys (`released`, `last_release`)."""
    stamp = stamped_at or datetime.now(timezone.utc)
    plan = {
        "agents": _rows(*names),
        "dispatch_waves": {
            "schema": 1,
            "cap": cap,
            "cap_source": "claude_default" if cap else "unbounded",
            "wave_1": list(names[:queued_from]),
            "queued": list(names[queued_from:]),
            "stamped_at": stamp.isoformat(),
            "grace_seconds": 90,
            **record_extra,
        },
    }
    path = run_paths.artifact_path(tmp_path, "dispatch_plan")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan))


class TestQueueStatus:
    """check_status() reports the queue, the cap, free slots and SLOT_FREE."""

    def test_legacy_plan_has_no_queue_and_never_frees_a_slot(self, mod, tmp_path):
        _write_plan(tmp_path, _rows("a", "b"))
        _start_agent(tmp_path, "a")

        result = mod.check_status(str(tmp_path))

        assert result["waves_recorded"] is False
        assert result["queued"] == []
        assert result["pending"] == []
        assert result["cap"] is None
        assert result["slot_free"] is False
        # b is NOT_DISPATCHED and does not block, exactly as before.
        _finish_agent(tmp_path, "a")
        assert mod.check_status(str(tmp_path))["all_done"] is True

    def test_queued_reviewer_with_a_free_slot(self, mod, tmp_path):
        _write_wave_plan(tmp_path, ["a", "b", "c"], cap=2, queued_from=2,
                         stamped_at=_STAMP)
        _start_agent(tmp_path, "a")
        _finish_agent(tmp_path, "b")

        result = mod.check_status(str(tmp_path), now=_STAMP + timedelta(seconds=5))

        assert result["queued"] == ["c"]
        assert result["slots"] == 1
        assert result["slot_free"] is True
        assert result["all_done"] is False
        row = next(a for a in result["agents"] if a["name"] == "c")
        assert row == {"name": "c", "status": "NOT_DISPATCHED", "queued": True}

    def test_queue_with_every_slot_running_is_not_slot_free(self, mod, tmp_path):
        _write_wave_plan(tmp_path, ["a", "b", "c"], cap=2, queued_from=2,
                         stamped_at=_STAMP)
        _start_agent(tmp_path, "a")
        _start_agent(tmp_path, "b")

        result = mod.check_status(str(tmp_path), now=_STAMP + timedelta(seconds=5))

        assert result["queued"] == ["c"]
        assert result["slots"] == 0
        assert result["slot_free"] is False

    def test_queue_with_nothing_running_is_slot_free_not_all_done(
        self, mod, tmp_path
    ):
        _write_wave_plan(tmp_path, ["a", "b", "c"], cap=2, queued_from=2,
                         stamped_at=_STAMP)
        _finish_agent(tmp_path, "a")
        _finish_agent(tmp_path, "b")

        result = mod.check_status(str(tmp_path), now=_STAMP + timedelta(seconds=5))

        assert result["running"] == 0
        assert result["slot_free"] is True
        assert result["all_done"] is False

    def test_wave_1_rejection_is_pending_inside_grace_then_queued(
        self, mod, tmp_path
    ):
        _write_wave_plan(tmp_path, ["a", "b", "c"], cap=2, queued_from=2,
                         stamped_at=_STAMP)
        _start_agent(tmp_path, "a")

        inside = mod.check_status(str(tmp_path), now=_STAMP + timedelta(seconds=30))
        past = mod.check_status(str(tmp_path), now=_STAMP + timedelta(seconds=91))

        # Inside grace, b holds its slot: a running + b pending fill cap 2.
        assert inside["pending"] == ["b"]
        assert inside["queued"] == ["c"]
        assert inside["slots"] == 0
        assert inside["slot_free"] is False
        # Past grace, b was rejected: it queues ahead of c and frees its slot.
        assert past["pending"] == []
        assert past["queued"] == ["b", "c"]
        assert past["slots"] == 1
        assert past["slot_free"] is True

    def test_unbounded_cap_with_a_late_wave_1_row(self, mod, tmp_path):
        _write_wave_plan(tmp_path, ["a", "b"], cap=None, queued_from=2,
                         stamped_at=_STAMP)
        _start_agent(tmp_path, "a")

        result = mod.check_status(str(tmp_path), now=_STAMP + timedelta(seconds=120))

        assert result["cap"] is None
        assert result["slots"] is None
        assert result["queued"] == ["b"]
        assert result["slot_free"] is True

    def test_empty_queue_with_record_is_all_done_when_nothing_runs(
        self, mod, tmp_path
    ):
        _write_wave_plan(tmp_path, ["a", "b"], cap=2, queued_from=2,
                         stamped_at=_STAMP)
        _finish_agent(tmp_path, "a")
        _finish_agent(tmp_path, "b")

        result = mod.check_status(str(tmp_path), now=_STAMP + timedelta(seconds=5))

        assert result["queued"] == []
        assert result["slot_free"] is False
        assert result["all_done"] is True


class TestSlotFreeExit:
    """--wait settles SLOT_FREE before exit 4; the envelope names the queue."""

    def _slot_free_plan(self, tmp_path):
        # Stamped long ago: no pending rows, so the queue is decided by rows.
        _write_wave_plan(tmp_path, ["a", "b", "c"], cap=2, queued_from=2,
                         stamped_at=datetime.now(timezone.utc) - timedelta(hours=1))
        _start_agent(tmp_path, "a")
        _finish_agent(tmp_path, "b")

    def test_slot_free_returns_only_after_the_settle_window(self, mod, tmp_path):
        self._slot_free_plan(tmp_path)

        clock = _FakeClock()
        result, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=300, poll_interval=10,
            sleep_fn=clock.sleep_fn, now_fn=clock.now_fn, settle_seconds=30,
        )

        assert expired is False
        assert result["slot_free"] is True
        assert clock.sleeps == [10, 10, 10]

    def test_slot_free_that_clears_before_settling_does_not_return(
        self, mod, tmp_path
    ):
        self._slot_free_plan(tmp_path)
        clock = _FakeClock()

        def sleep_fn(seconds):
            clock.sleep_fn(seconds)
            if len(clock.sleeps) == 1:
                # The launched reviewer writes its started marker.
                _start_agent(tmp_path, "c")

        result, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=60, poll_interval=10,
            sleep_fn=sleep_fn, now_fn=clock.now_fn, settle_seconds=30,
        )

        assert expired is True
        assert result["slot_free"] is False
        assert result["queued"] == []

    def test_all_done_with_an_empty_queue_returns_immediately(self, mod, tmp_path):
        _write_wave_plan(tmp_path, ["a"], cap=2, queued_from=1)
        _finish_agent(tmp_path, "a")

        clock = _FakeClock()
        result, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=60,
            sleep_fn=clock.sleep_fn, now_fn=clock.now_fn,
        )

        assert (result["all_done"], expired, clock.sleeps) == (True, False, [])

    def test_expiry_with_an_unsettled_slot_free_reports_expired(
        self, mod, tmp_path
    ):
        self._slot_free_plan(tmp_path)

        clock = _FakeClock()
        result, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=20, poll_interval=10,
            sleep_fn=clock.sleep_fn, now_fn=clock.now_fn, settle_seconds=30,
        )

        assert expired is True
        assert result["slot_free"] is True

    def test_format_output_names_queue_and_slots(self, mod, tmp_path):
        self._slot_free_plan(tmp_path)

        text = mod.format_output(mod.check_status(str(tmp_path)))

        assert "c" in text.split("QUEUED: ", 1)[1].splitlines()[0]
        assert "SLOTS: 1" in text
        assert "QUEUED (launch when a slot frees)" in text
        assert "never started" not in text.split("ALL_DONE:", 1)[1]

    def test_format_output_legacy_plan_has_no_queue_lines(self, mod, tmp_path):
        _write_plan(tmp_path, _rows("a"))

        text = mod.format_output(mod.check_status(str(tmp_path)))

        assert "QUEUED:" not in text
        assert "SLOTS:" not in text
        assert "LLM may have failed to dispatch" in text

    def test_format_output_unbounded_slots_read_all(self, mod, tmp_path):
        _write_wave_plan(tmp_path, ["a"], cap=None, queued_from=1)
        _start_agent(tmp_path, "a")

        text = mod.format_output(mod.check_status(str(tmp_path)))

        assert "QUEUED: none" in text
        assert "SLOTS: all" in text

    def _assert_envelope(self, stdout):
        assert "ALL_DONE: false" in stdout
        assert "QUEUED: c" in stdout
        assert "SLOTS: 1" in stdout

    def test_no_wait_cli_exits_4_with_the_envelope(self, mod, tmp_path):
        self._slot_free_plan(tmp_path)

        r = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path)],
            capture_output=True, text=True, timeout=15, cwd=tmp_path,
        )

        assert r.returncode == mod.EXIT_SLOT_FREE == 4
        self._assert_envelope(r.stdout)

    def test_wait_cli_exits_4_with_the_envelope(self, mod, tmp_path):
        """A real process, with the 30s settle window shortened.

        main() reads SLOT_FREE_SETTLE_SECONDS at call time, so a wrapper
        that loads the script and lowers it exercises the real --wait
        wiring, stdout and exit code without a 30s test.
        """
        self._slot_free_plan(tmp_path)
        wrapper = (
            "import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('s', {str(SCRIPT_PATH)!r})\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "mod.SLOT_FREE_SETTLE_SECONDS = 0.1\n"
            "mod.main()\n"
        )

        r = subprocess.run(
            [sys.executable, "-c", wrapper, "--output-dir", str(tmp_path),
             "--wait", "--max-seconds", "30"],
            capture_output=True, text=True, timeout=30, cwd=tmp_path,
        )

        assert r.returncode == 4, r.stderr
        self._assert_envelope(r.stdout)

    def test_legacy_plan_cli_exit_codes_unchanged(self, tmp_path):
        _write_plan(tmp_path, _rows("a", "b"))
        _start_agent(tmp_path, "a")

        def run(*tail):
            return subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path),
                 *tail],
                capture_output=True, text=True, timeout=15, cwd=tmp_path,
            ).returncode

        assert run() == 2
        assert run("--wait", "--max-seconds", "0.05") == 3
        _finish_agent(tmp_path, "a")
        assert run() == 0
        assert run("--wait", "--max-seconds", "5") == 0


def _plan_record(tmp_path):
    path = run_paths.artifact_path(tmp_path, "dispatch_plan")
    return json.loads(path.read_text())["dispatch_waves"]


class TestPendingBlocksAllDone:
    """A launched wave-1 reviewer with no started marker yet is not done."""

    def test_wave_1_launched_moments_ago_is_not_all_done(self, mod, tmp_path):
        _write_wave_plan(tmp_path, ["a", "b"], cap=2, queued_from=2,
                         stamped_at=_STAMP)

        result = mod.check_status(str(tmp_path), now=_STAMP + timedelta(seconds=5))

        assert result["pending"] == ["a", "b"]
        assert result["running"] == 0
        assert result["queued"] == []
        assert result["all_done"] is False
        assert result["slot_free"] is False

    def test_wait_cli_expires_instead_of_reporting_all_done(self, tmp_path):
        """The reviewer's repro: record stamped now, no markers. --wait used
        to exit 0 at once and step 8 closed intake over wave 1."""
        _write_wave_plan(tmp_path, ["a", "b"], cap=2, queued_from=2)

        r = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path),
             "--wait", "--max-seconds", "0.5"],
            capture_output=True, text=True, timeout=15, cwd=tmp_path,
        )

        assert r.returncode == 3, r.stdout
        assert "ALL_DONE: false" in r.stdout
        assert "PENDING (launched, not started yet; no action)" in r.stdout


class TestReleaseRecord:
    """`released` / `last_release` on the wave record, and the 180s grace."""

    def test_default_grace_keeps_a_slow_wave_1_start_pending(self):
        record = dispatch_status.build_dispatch_waves(
            _rows("a"), 2, "claude_default", _STAMP.isoformat(),
        )

        assert record["grace_seconds"] == 180
        assert _lists(dispatch_status.queue_state(
            record, ["a"], _STAMP + timedelta(seconds=150),
        )) == {"queued": [], "pending": ["a"]}

    def test_a_released_name_is_pending_inside_grace_then_queued(self):
        released_at = _STAMP + timedelta(seconds=300)
        record = _waves(released={"c": _release(released_at.isoformat())})

        inside = dispatch_status.queue_state(
            record, ["c"], released_at + timedelta(seconds=90),
        )
        past = dispatch_status.queue_state(
            record, ["c"], released_at + timedelta(seconds=91),
        )

        assert _lists(inside) == {"queued": [], "pending": ["c"]}
        assert _lists(past) == {"queued": ["c"], "pending": []}

    def test_validator_accepts_release_fields(self):
        record = _waves(
            released={"c": _release(_STAMP.isoformat())},
            last_release={"at": _STAMP.isoformat(), "terminal_count": 0},
        )

        assert dispatch_status.validate_dispatch_waves(record) is record

    @pytest.mark.parametrize(
        "extra",
        [
            pytest.param({"released": ["c"]}, id="released-not-object"),
            pytest.param({"released": {"zz": _release(_STAMP.isoformat())}},
                         id="unplanned-name"),
            pytest.param({"released": {"c": _release("soon")}}, id="bad-release-time"),
            pytest.param({"last_release": None}, id="last-release-null"),
            pytest.param({"last_release": {"terminal_count": 1}}, id="missing-at"),
            pytest.param(
                {"last_release": {"at": _STAMP.isoformat(), "terminal_count": -1}},
                id="negative-count",
            ),
            pytest.param(
                {"last_release": {"at": _STAMP.isoformat(), "terminal_count": True}},
                id="bool-count",
            ),
        ],
    )
    def test_validator_rejects_bad_release_fields(self, extra):
        with pytest.raises(ValueError):
            dispatch_status.validate_dispatch_waves(_waves(**extra))

    def test_record_release_is_pure_and_counts_repeat_releases(self):
        """A repeat release counts up. With nothing occupied and no reviewer
        ended since the previous release, the release counts."""
        record = _waves(
            released={"b": _release("2026-01-01T00:00:00+00:00")},
            last_release={"at": "2026-01-01T00:00:00+00:00", "terminal_count": 3},
        )

        updated = dispatch_status.record_release(
            record, ["b", "c"], 3, _STAMP.isoformat(), 0,
        )

        assert updated["released"] == {
            "b": {"at": _STAMP.isoformat(), "count": 2, "counted": 1},
            "c": {"at": _STAMP.isoformat(), "count": 1, "counted": 1},
        }
        assert updated["last_release"] == {"at": _STAMP.isoformat(), "terminal_count": 3}
        assert record["released"] == {"b": _release("2026-01-01T00:00:00+00:00")}
        dispatch_status.validate_dispatch_waves(updated)

    @pytest.mark.parametrize(
        "occupied, terminal, counted",
        [
            pytest.param(0, 0, 1, id="host-held-nothing"),
            pytest.param(1, 0, 0, id="a-reviewer-running-or-pending"),
            pytest.param(0, 1, 0, id="a-reviewer-ended-since-step-6"),
        ],
    )
    def test_a_release_counts_only_when_the_host_held_nothing_of_ours(
        self, occupied, terminal, counted
    ):
        """A refusal while another reviewer runs, waits to start, or ran
        and ended since the previous release (here: since step 6) may be
        host contention, so it never counts toward abandonment."""
        updated = dispatch_status.record_release(
            _waves(), ["c"], terminal, _STAMP.isoformat(), occupied,
        )

        assert updated["released"]["c"] == {
            "at": _STAMP.isoformat(), "count": 1, "counted": counted,
        }

    @pytest.mark.parametrize(
        "entry",
        [
            pytest.param({"at": _STAMP.isoformat(), "count": 1, "counted": 2},
                         id="counted-over-count"),
            pytest.param({"at": _STAMP.isoformat(), "count": 1, "counted": -1},
                         id="negative-counted"),
            pytest.param({"at": _STAMP.isoformat(), "count": 1}, id="no-counted"),
            pytest.param({"at": _STAMP.isoformat(), "count": 1, "counted": 0,
                          "first_at": _STAMP.isoformat()}, id="extra-key"),
            pytest.param(_STAMP.isoformat(), id="bare-release-time"),
            pytest.param({"at": _STAMP.isoformat(), "first_at": _STAMP.isoformat(),
                          "count": 2}, id="first-at-form"),
        ],
    )
    def test_validator_rejects_bad_counted_entries(self, entry):
        with pytest.raises(ValueError):
            dispatch_status.validate_dispatch_waves(_waves(released={"c": entry}))


class TestReleaseAndProgress:
    """The watchdog stamps what it releases; a re-fire needs progress."""

    def _check(self, mod, tmp_path, at):
        return mod.check_status(str(tmp_path), now=at)

    def test_release_stamps_the_first_slots_names_and_prints_exactly_them(
        self, mod, tmp_path
    ):
        _write_wave_plan(tmp_path, ["a", "b", "c", "d", "e"], cap=2,
                         queued_from=2, stamped_at=_STAMP)
        _start_agent(tmp_path, "a")
        _finish_agent(tmp_path, "b")
        released_at = _STAMP + timedelta(seconds=200)
        result = self._check(mod, tmp_path, released_at)
        assert (result["queued"], result["slots"]) == (["c", "d", "e"], 1)

        names = mod.release_queued(str(tmp_path), result, now=released_at)
        text = mod.format_output(result)

        assert names == ["c"]
        # a is RUNNING, so c's release may meet a full host: not counted.
        assert _plan_record(tmp_path)["released"] == {"c": {
            "at": released_at.isoformat(), "count": 1, "counted": 0,
        }}
        assert _plan_record(tmp_path)["last_release"] == {
            "at": released_at.isoformat(), "terminal_count": 1,
        }
        assert "QUEUED: c\n" in text
        assert "QUEUED (launch now)" in text
        assert "QUEUED (launch when a slot frees)" in text  # d and e wait
        after = self._check(mod, tmp_path, released_at + timedelta(seconds=10))
        assert after["pending"] == ["c"]
        assert after["slots"] == 0
        assert after["slot_free"] is False

    def test_unbounded_release_takes_the_whole_queue(self, mod, tmp_path):
        _write_wave_plan(tmp_path, ["a", "b", "c"], cap=None, queued_from=3,
                         stamped_at=_STAMP)
        _start_agent(tmp_path, "a")
        result = self._check(mod, tmp_path, _STAMP + timedelta(seconds=200))

        names = mod.release_queued(
            str(tmp_path), result, now=_STAMP + timedelta(seconds=200),
        )

        assert names == ["b", "c"]
        assert sorted(_plan_record(tmp_path)["released"]) == ["b", "c"]

    def test_accepted_but_unstarted_reviewer_does_not_over_launch(
        self, mod, tmp_path
    ):
        """The code reviewer's repro: cap 2, a running, b accepted in wave 1
        but not started past grace, c queued. The release names b only, and
        once stamped b holds its slot: c is never launched over the cap."""
        _write_wave_plan(tmp_path, ["a", "b", "c"], cap=2, queued_from=2,
                         stamped_at=_STAMP)
        _start_agent(tmp_path, "a")
        release_at = _STAMP + timedelta(seconds=100)
        first = self._check(mod, tmp_path, release_at)
        assert (first["queued"], first["slots"], first["slot_free"]) == (
            ["b", "c"], 1, True,
        )

        assert mod.release_queued(str(tmp_path), first, now=release_at) == ["b"]
        assert "QUEUED: b\n" in mod.format_output(first)

        inside = self._check(mod, tmp_path, release_at + timedelta(seconds=30))
        assert inside["pending"] == ["b"]
        assert inside["slot_free"] is False
        # Past b's release grace with nothing finished: b is queued again,
        # but its slot is explained by its own release, so no re-fire.
        stale = self._check(mod, tmp_path, release_at + timedelta(seconds=91))
        assert stale["queued"] == ["b", "c"]
        assert stale["slot_free"] is False
        # A reviewer finishing is progress: SLOT_FREE fires again.
        _finish_agent(tmp_path, "a")
        assert self._check(
            mod, tmp_path, release_at + timedelta(seconds=92),
        )["slot_free"] is True

    def test_wave_1_rejection_after_a_release_waits_for_progress(
        self, mod, tmp_path
    ):
        """After a release, a wave-1 rejection crossing its own grace frees
        a slot no release explains. That alone never fires, not even once
        the release starts; a reviewer finishing does."""
        _write_wave_plan(tmp_path, ["a", "b", "c", "d"], cap=3, queued_from=3,
                         stamped_at=_STAMP)
        _finish_agent(tmp_path, "a")
        _start_agent(tmp_path, "b")
        release_at = _STAMP + timedelta(seconds=50)
        first = self._check(mod, tmp_path, release_at)
        assert first["pending"] == ["c"] and first["queued"] == ["d"]
        mod.release_queued(str(tmp_path), first, now=release_at)

        later = self._check(mod, tmp_path, _STAMP + timedelta(seconds=100))
        assert later["queued"] == ["c"]
        assert later["pending"] == ["d"]
        assert later["slots"] == 1
        assert later["slot_free"] is False
        _start_agent(tmp_path, "d")
        started = self._check(mod, tmp_path, _STAMP + timedelta(seconds=100))
        assert started["queued"] == ["c"]
        assert started["slots"] == 1
        assert started["slot_free"] is False
        _finish_agent(tmp_path, "b")
        finished = self._check(mod, tmp_path, _STAMP + timedelta(seconds=110))

        assert finished["queued"] == ["c"]
        assert finished["slot_free"] is True

    def test_nothing_running_or_pending_may_fire_again(self, mod, tmp_path):
        now = datetime.now(timezone.utc)
        _write_wave_plan(
            tmp_path, ["a", "b"], cap=None, queued_from=2,
            stamped_at=now - timedelta(seconds=700),
            released={"b": _release((now - timedelta(seconds=500)).isoformat())},
            last_release={"at": (now - timedelta(seconds=500)).isoformat(),
                          "terminal_count": 1},
        )
        _finish_agent(tmp_path, "a")

        result = mod.check_status(str(tmp_path))

        assert (result["running"], result["pending"], result["queued"]) == (
            0, [], ["b"],
        )
        assert result["slot_free"] is True

    def _rejected_codex_plan(self, tmp_path):
        """Codex, unbounded: b was released, the thread limit rejected it,
        and its release grace ran out with nothing finished since."""
        now = datetime.now(timezone.utc)
        released_at = (now - timedelta(seconds=500)).isoformat()
        _write_wave_plan(
            tmp_path, ["a", "b", "c"], cap=None, queued_from=3,
            stamped_at=now - timedelta(seconds=700),
            released={"b": _release(released_at)},
            last_release={"at": released_at, "terminal_count": 0},
        )
        _start_agent(tmp_path, "a")
        _start_agent(tmp_path, "c")

    def test_unbounded_rejected_release_does_not_refire(self, mod, tmp_path):
        self._rejected_codex_plan(tmp_path)
        clock = _FakeClock()

        result, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=60, poll_interval=10,
            sleep_fn=clock.sleep_fn, now_fn=clock.now_fn, settle_seconds=30,
        )

        assert result["queued"] == ["b"]
        assert result["slot_free"] is False
        assert expired is True

    def test_unbounded_fires_once_after_a_reviewer_finishes(self, mod, tmp_path):
        self._rejected_codex_plan(tmp_path)
        _finish_agent(tmp_path, "a")
        clock = _FakeClock()

        result, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=120, poll_interval=10,
            sleep_fn=clock.sleep_fn, now_fn=clock.now_fn, settle_seconds=30,
        )
        assert (result["slot_free"], expired) == (True, False)
        assert mod.release_queued(str(tmp_path), result) == ["b"]

        clock = _FakeClock()
        again, expired = mod.wait_for_all_done(
            str(tmp_path), max_seconds=60, poll_interval=10,
            sleep_fn=clock.sleep_fn, now_fn=clock.now_fn, settle_seconds=30,
        )
        assert expired is True
        assert again["pending"] == ["b"]
        assert again["slot_free"] is False

    def test_no_wait_cli_exit_4_is_read_only(self, tmp_path):
        """Only --wait releases: a notification-time status call that
        stamped names the orchestrator never launched left them pending."""
        _write_wave_plan(tmp_path, ["a", "b", "c", "d"], cap=2, queued_from=2,
                         stamped_at=datetime.now(timezone.utc) - timedelta(seconds=600))
        _start_agent(tmp_path, "a")
        _finish_agent(tmp_path, "b")
        plan_path = run_paths.artifact_path(tmp_path, "dispatch_plan")
        before = plan_path.read_bytes()

        r = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path)],
            capture_output=True, text=True, timeout=15, cwd=tmp_path,
        )

        assert r.returncode == 4, r.stderr
        assert plan_path.read_bytes() == before
        assert "do not launch from this output" in r.stdout
        assert "QUEUED (launch now)" not in r.stdout
        assert "NOTE: launch every QUEUED agent now" not in r.stdout

    def test_wait_cli_exit_4_still_stamps_the_release(self, tmp_path):
        _write_wave_plan(tmp_path, ["a", "b", "c", "d"], cap=2, queued_from=2,
                         stamped_at=datetime.now(timezone.utc) - timedelta(seconds=600))
        _start_agent(tmp_path, "a")
        _finish_agent(tmp_path, "b")

        r = _run_wait_cli(tmp_path)

        assert r.returncode == 4, r.stderr
        assert "QUEUED: c\n" in r.stdout
        assert "NOTE: launch every QUEUED agent now" in r.stdout
        assert list(_plan_record(tmp_path)["released"]) == ["c"]


def _run_wait_cli(tmp_path, max_seconds="30"):
    """The real --wait CLI with the 30s settle window lowered to 0.1s."""
    wrapper = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('s', {str(SCRIPT_PATH)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "mod.SLOT_FREE_SETTLE_SECONDS = 0.1\n"
        "mod.main()\n"
    )
    return subprocess.run(
        [sys.executable, "-c", wrapper, "--output-dir", str(tmp_path),
         "--wait", "--max-seconds", max_seconds],
        capture_output=True, text=True, timeout=30, cwd=tmp_path,
    )


class TestAbandonment:
    """A reviewer that never starts stops blocking within bounded time, so
    step 7's watchdog always reaches exit 0 (as on a legacy plan), and a
    healthy reviewer the host refused because it was full never does."""

    TIMEOUT = 1200

    def _check(self, mod, tmp_path, at):
        return mod.check_status(str(tmp_path), timeout_seconds=self.TIMEOUT, now=at)

    def _simulate(self, mod, tmp_path, durations, host_limit, lost=(),
                  start=_STAMP, step=30, horizon=6 * 3600):
        """Drive the watchdog against a simulated host every `step` seconds
        from `start`, the plan's `stamped_at`, releasing on SLOT_FREE.

        Step 6 launches `wave_1` at `start`. The host accepts a launch
        while fewer than `host_limit` accepted reviewers are live and
        refuses the rest. An accepted reviewer starts at once and runs for
        `durations[name]` seconds; past the agent timeout it reads
        TIMED_OUT and its host thread ends with it. A `lost` reviewer's
        launch is accepted but it never starts and holds no thread.
        Returns (seconds until all_done, last result, trace of
        (seconds, abandoned names) per check)."""
        record = _plan_record(tmp_path)
        started, trace = {}, []

        def ends_at(name):
            return started[name] + timedelta(
                seconds=min(durations[name], self.TIMEOUT),
            )

        def launch(names, at):
            for name in names:
                live = sum(1 for other in started if ends_at(other) > at)
                if name in lost or name in started or live >= host_limit:
                    continue
                started[name] = at
                _start_agent_at(tmp_path, name, at)

        launch(record["wave_1"], start)
        at = start
        while at - start <= timedelta(seconds=horizon):
            for name in started:
                if durations[name] <= self.TIMEOUT and ends_at(name) <= at:
                    _finish_agent(tmp_path, name)
            result = self._check(mod, tmp_path, at)
            seconds = int((at - start).total_seconds())
            trace.append((seconds, list(result["abandoned"])))
            if result["all_done"]:
                return seconds, result, trace
            if result["slot_free"]:
                launch(mod.release_queued(str(tmp_path), result, now=at), at)
            at += timedelta(seconds=step)
        raise AssertionError(f"not all_done within {horizon}s: {result}")

    def test_codex_thread_limit_refusals_never_abandon_a_healthy_reviewer(
        self, mod, tmp_path
    ):
        """The code reviewer's probe: Codex, unbounded cap, 8 reviewers and
        a host thread limit of 4. a-d run 580-1080s; e-h are refused at
        step 6 and on every release while the pool is full. Every one of
        them runs; none is abandoned."""
        names = list("abcdefgh")
        _write_wave_plan(tmp_path, names, cap=None, queued_from=8,
                         stamped_at=_STAMP)
        durations = dict(a=580, b=880, c=980, d=1080,
                         e=600, f=600, g=600, h=600)

        _, result, _ = self._simulate(mod, tmp_path, durations, host_limit=4)

        assert result["abandoned"] == []
        assert result["finished"] == 8

    def test_a_serial_host_never_abandons_a_healthy_reviewer(self, mod, tmp_path):
        """A host that runs one reviewer at a time: each release goes out
        once the only running reviewer has ended, so nothing of ours runs
        then, yet the host refuses every co-released name but one. A
        reviewer ending since the previous release keeps those refusals
        from counting."""
        names = list("abcde")
        _write_wave_plan(tmp_path, names, cap=None, queued_from=5,
                         stamped_at=_STAMP)

        _, result, _ = self._simulate(
            mod, tmp_path, dict.fromkeys(names, 200), host_limit=1,
        )

        assert result["abandoned"] == []
        assert result["finished"] == 5

    def test_a_fresh_release_is_pending_not_abandoned(self, mod, tmp_path):
        """The spec reviewer's probe: x was refused at step 6 and once
        more; a and b finish just before the agent timeout, x is released
        again at +1175 and checked at +1200. The retired rule (b) abandoned
        it there, 25s into its grace window."""
        _write_wave_plan(tmp_path, ["a", "b", "x"], cap=3, queued_from=3,
                         stamped_at=_STAMP)
        _start_agent_at(tmp_path, "a", _STAMP)
        _start_agent_at(tmp_path, "b", _STAMP)
        at = _STAMP + timedelta(seconds=200)
        mod.release_queued(str(tmp_path), self._check(mod, tmp_path, at), now=at)
        _finish_agent(tmp_path, "a")
        _finish_agent(tmp_path, "b")
        at = _STAMP + timedelta(seconds=1175)
        result = self._check(mod, tmp_path, at)
        assert result["slot_free"] is True
        mod.release_queued(str(tmp_path), result, now=at)

        for seconds in (1180, 1200, 1175 + 90):
            later = self._check(mod, tmp_path, _STAMP + timedelta(seconds=seconds))
            assert (later["pending"], later["abandoned"]) == (["x"], [])
            assert later["all_done"] is False

    def test_nothing_is_abandoned_inside_its_grace_window(self, mod, tmp_path):
        released_at = _STAMP + timedelta(seconds=300)
        record = _waves(released={"c": {
            "at": released_at.isoformat(), "count": 5, "counted": 5,
        }})

        inside = dispatch_status.queue_state(
            record, ["c"], released_at + timedelta(seconds=90),
        )
        past = dispatch_status.queue_state(
            record, ["c"], released_at + timedelta(seconds=91),
        )

        assert _lists(inside, ("pending", "abandoned")) == {
            "pending": ["c"], "abandoned": [],
        }
        assert _lists(past, ("pending", "abandoned")) == {
            "pending": [], "abandoned": ["c"],
        }

    def test_a_lone_lost_reviewer_is_abandoned_after_two_counted_releases(
        self, mod, tmp_path
    ):
        """Unbounded: a runs and finishes, x's launches are all accepted
        and lost. Its release while a runs and the one right after a
        ended do not count (a may have held the host); the next two do,
        and --wait then exits 0."""
        start = datetime.now(timezone.utc) - timedelta(hours=3)
        _write_wave_plan(tmp_path, ["a", "x"], cap=None, queued_from=2,
                         stamped_at=start)

        seconds, result, _ = self._simulate(
            mod, tmp_path, {"a": 300}, host_limit=4, lost={"x"},
            start=start, step=60,
        )

        assert seconds < 3 * 3600
        assert result["abandoned"] == ["x"]
        assert _plan_record(tmp_path)["released"]["x"]["counted"] == (
            dispatch_status.MAX_RELEASES
        )
        text = mod.format_output(result)
        # step 6, two uncounted releases, two counted ones
        assert "ABANDONED (never started after 5 launch attempts)" in text
        assert "ALL_DONE: true" in text
        r = _run_wait_cli(tmp_path, max_seconds="5")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "ABANDONED" in r.stdout

    def test_a_running_reviewer_defers_abandonment_until_it_times_out(
        self, mod, tmp_path
    ):
        """cap 2: a runs past the agent timeout, x is lost. While a runs,
        x's releases do not count; once a turns TIMED_OUT they do, and the
        run still ends."""
        _write_wave_plan(tmp_path, ["a", "x"], cap=2, queued_from=2,
                         stamped_at=_STAMP)

        _, result, trace = self._simulate(
            mod, tmp_path, {"a": 5000}, host_limit=4, lost={"x"},
        )

        assert result["abandoned"] == ["x"]
        assert result["timed_out"] == 1
        first_abandoned = next(seconds for seconds, names in trace if names)
        assert first_abandoned > self.TIMEOUT

    def test_an_all_lost_queue_terminates(self, mod, tmp_path):
        """cap 2, four reviewers, every launch lost: each pair is released
        together with nothing else occupying the host, so both count, and
        every one is abandoned in bounded time."""
        names = list("abcd")
        _write_wave_plan(tmp_path, names, cap=2, queued_from=2,
                         stamped_at=_STAMP)

        seconds, result, _ = self._simulate(
            mod, tmp_path, {}, host_limit=4, lost=set(names),
        )

        assert result["abandoned"] == names
        # Two pairs, each: step 6 or first-release grace, then two counted
        # releases' grace windows (90s each here), polled every 30s.
        assert seconds <= 2 * 3 * (90 + 30)

    def test_two_lost_reviewers_released_in_turn_still_terminate(
        self, mod, tmp_path
    ):
        """cap 3: c (wave 1) and d (queued) are lost. d is released when
        a finishes, while c is still pending; c then re-queues while d is
        pending. Released in turn, each while the other was pending,
        neither release counted, forever. A re-fire now waits for nothing
        to be pending, so they are released together and both count."""
        _write_wave_plan(tmp_path, list("abcd"), cap=3, queued_from=3,
                         stamped_at=_STAMP)

        _, result, _ = self._simulate(
            mod, tmp_path, {"a": 60, "b": 150}, host_limit=3, lost={"c", "d"},
        )

        assert result["abandoned"] == ["c", "d"]
        assert result["finished"] == 2

    def test_an_abandoned_row_frees_its_slot_for_the_queue(self, mod, tmp_path):
        """cap 1: c abandoned after its counted releases; d, never
        launched, is released next instead of waiting behind c forever."""
        stamp = _STAMP.isoformat()
        _write_wave_plan(
            tmp_path, ["a", "c", "d"], cap=1, queued_from=1, stamped_at=_STAMP,
            released={"c": {"at": stamp, "count": 2, "counted": 2}},
            last_release={"at": stamp, "terminal_count": 1},
        )
        _finish_agent(tmp_path, "a")

        result = self._check(mod, tmp_path, _STAMP + timedelta(seconds=91))

        assert result["abandoned"] == ["c"]
        assert result["queued"] == ["d"]
        assert result["slot_free"] is True
        assert mod.release_queued(str(tmp_path), result,
                                  now=_STAMP + timedelta(seconds=91)) == ["d"]

    def test_a_late_start_after_abandonment_runs_normally(self, mod, tmp_path):
        stamp = _STAMP.isoformat()
        _write_wave_plan(
            tmp_path, ["a", "c"], cap=2, queued_from=1, stamped_at=_STAMP,
            released={"c": {"at": stamp, "count": 2, "counted": 2}},
        )
        _finish_agent(tmp_path, "a")
        _start_agent(tmp_path, "c")

        result = mod.check_status(str(tmp_path))

        assert result["abandoned"] == []
        assert result["running"] == 1 and result["all_done"] is False

    def test_legacy_plan_never_abandons(self, mod, tmp_path):
        _write_plan(tmp_path, _rows("a", "b"))
        _finish_agent(tmp_path, "a")

        result = mod.check_status(
            str(tmp_path), now=datetime.now(timezone.utc) + timedelta(days=1),
        )

        assert result["abandoned"] == []
        assert result["all_done"] is True
        text = mod.format_output(result)
        assert "ABANDONED" not in text
        assert "NOT_DISPATCHED (never started" in text


class TestReleaseFiltersAgainstTheReloadedRecord:
    """A step-6 restamp between the status check and the release write."""

    def _restamped_between(self, mod, tmp_path, new_names):
        _write_wave_plan(tmp_path, ["a", "b", "c", "d"], cap=2, queued_from=2,
                         stamped_at=_STAMP)
        _finish_agent(tmp_path, "a")
        _finish_agent(tmp_path, "b")
        result = mod.check_status(str(tmp_path), now=_STAMP + timedelta(seconds=200))
        assert result["queued"][:result["slots"]] == ["c", "d"]
        # The restamp drops c (an override skipped it after the check).
        _write_wave_plan(tmp_path, new_names, cap=2, queued_from=2,
                         stamped_at=_STAMP + timedelta(seconds=199))
        return result

    def test_a_dropped_name_is_not_stamped_and_the_plan_stays_valid(
        self, mod, tmp_path
    ):
        result = self._restamped_between(mod, tmp_path, ["a", "b", "d"])

        names = mod.release_queued(
            str(tmp_path), result, now=_STAMP + timedelta(seconds=200),
        )

        assert names == ["d"]
        assert result["slot_free"] is True
        plan_path = run_paths.artifact_path(tmp_path, "dispatch_plan")
        plan = dispatch_status.load_dispatch_plan(plan_path)
        assert list(plan["dispatch_waves"]["released"]) == ["d"]
        assert "QUEUED: d\n" in mod.format_output(result)

    def test_no_surviving_name_clears_slot_free(self, mod, tmp_path):
        result = self._restamped_between(mod, tmp_path, ["a", "b"])
        plan_path = run_paths.artifact_path(tmp_path, "dispatch_plan")
        before = plan_path.read_bytes()

        names = mod.release_queued(
            str(tmp_path), result, now=_STAMP + timedelta(seconds=200),
        )

        assert names == []
        assert result["slot_free"] is False
        assert plan_path.read_bytes() == before

    def test_an_invalid_new_record_is_not_written(self, mod, tmp_path, capsys,
                                                  monkeypatch):
        _write_wave_plan(tmp_path, ["a", "b", "c"], cap=2, queued_from=2,
                         stamped_at=_STAMP)
        _finish_agent(tmp_path, "a")
        result = mod.check_status(str(tmp_path), now=_STAMP + timedelta(seconds=200))
        plan_path = run_paths.artifact_path(tmp_path, "dispatch_plan")
        before = plan_path.read_bytes()
        monkeypatch.setattr(
            mod, "record_release",
            lambda record, *args: {**record, "released": {"zz": "soon"}},
        )

        names = mod.release_queued(
            str(tmp_path), result, now=_STAMP + timedelta(seconds=200),
        )

        assert names == ["b", "c"]
        assert plan_path.read_bytes() == before
        assert "WARNING: not recording the release" in capsys.readouterr().err

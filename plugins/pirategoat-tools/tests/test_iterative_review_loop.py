"""Tests for iterative_review.loop — state, convergence, max rounds."""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from iterative_review.loop import (
    read_loop_state,
    write_loop_state,
    compute_max_rounds,
    check_convergence,
    DEFAULT_STATE,
    build_pushback_entry,
    append_pushback_log,
    read_pushback_log,
    append_deferred_item,
    read_deferred_items,
    validate_outcomes,
    compute_relevant_diff_size,
)


class TestMaxRounds:
    """Diff-size-based max rounds computation."""

    @pytest.mark.parametrize("diff_lines,expected", [
        (0, 3),
        (100, 3),
        (499, 3),
        (500, 4),
        (999, 4),
        (1000, 5),
        (1999, 5),
        (2000, 6),
        (2999, 6),
        (3000, 7),
        (4999, 7),
        (5000, 8),
        (9999, 8),
        (10000, 10),
        (50000, 10),
    ])
    def test_max_rounds_by_diff_size(self, diff_lines, expected):
        assert compute_max_rounds(diff_lines) == expected


class TestStateManagement:
    def test_read_returns_default_when_missing(self, tmp_path):
        state = read_loop_state(str(tmp_path / "code-review"))
        assert state["current_round"] == 0
        assert state["rounds"] == []

    def test_write_and_read_roundtrip(self, tmp_path):
        d = str(tmp_path / "code-review")
        Path(d).mkdir()
        state = {**DEFAULT_STATE, "current_round": 2, "merge_base": "abc123"}
        write_loop_state(d, state)
        loaded = read_loop_state(d)
        assert loaded["current_round"] == 2
        assert loaded["merge_base"] == "abc123"

    def test_read_handles_corrupted_json(self, tmp_path):
        d = tmp_path / "code-review"
        d.mkdir()
        (d / "review-loop-state.json").write_text("not json{{{")
        state = read_loop_state(str(d))
        assert state["current_round"] == 0


class TestConvergence:
    """Four convergence conditions + none-met case."""

    def test_zero_findings(self):
        result = check_convergence(
            findings_count=0, all_p3=False, all_rejected=False,
            current_round=1, max_rounds=3
        )
        assert result == "zero_findings"

    def test_max_rounds_reached(self):
        result = check_convergence(
            findings_count=3, all_p3=False, all_rejected=False,
            current_round=3, max_rounds=3
        )
        assert result == "max_rounds"

    def test_all_rejected(self):
        result = check_convergence(
            findings_count=2, all_p3=False, all_rejected=True,
            current_round=1, max_rounds=3
        )
        assert result == "all_rejected"

    def test_nitpicks_only(self):
        result = check_convergence(
            findings_count=2, all_p3=True, all_rejected=False,
            current_round=1, max_rounds=3
        )
        assert result == "nitpicks_only"

    def test_continue_when_no_condition_met(self):
        result = check_convergence(
            findings_count=3, all_p3=False, all_rejected=False,
            current_round=1, max_rounds=3
        )
        assert result is None

    def test_zero_findings_takes_priority_over_max_rounds(self):
        result = check_convergence(
            findings_count=0, all_p3=False, all_rejected=False,
            current_round=3, max_rounds=3
        )
        assert result == "zero_findings"

    def test_hard_limit_terminates_regardless(self):
        from iterative_review.loop import MAX_ROUNDS_HARD_LIMIT
        result = check_convergence(
            findings_count=5, all_p3=False, all_rejected=False,
            current_round=MAX_ROUNDS_HARD_LIMIT, max_rounds=MAX_ROUNDS_HARD_LIMIT + 5
        )
        assert result == "hard_limit"

    def test_hard_limit_takes_priority_over_continue(self):
        from iterative_review.loop import MAX_ROUNDS_HARD_LIMIT
        # Even with findings and max_rounds above the limit, hard limit wins
        result = check_convergence(
            findings_count=3, all_p3=False, all_rejected=False,
            current_round=MAX_ROUNDS_HARD_LIMIT, max_rounds=100
        )
        assert result == "hard_limit"

    def test_hard_limit_is_20(self):
        from iterative_review.loop import MAX_ROUNDS_HARD_LIMIT
        assert MAX_ROUNDS_HARD_LIMIT == 20


class TestPushbackLog:
    def test_builds_entry_for_p1_rejection(self):
        outcome = {
            "id": "r1_f2", "action": "rejected",
            "reasoning": "Input is pre-validated."
        }
        finding = {
            "id": "r1_f2", "severity": "P1",
            "title": "Missing null check", "location": "handler.py:42"
        }
        entry = build_pushback_entry(outcome, finding, round_num=1)
        assert entry is not None
        assert "REJECTED" in entry
        assert "r1_f2" in entry
        assert "handler.py:42" in entry

    def test_skips_entry_for_p2_rejection(self):
        outcome = {"id": "r1_f3", "action": "rejected", "reasoning": "Minor."}
        finding = {"id": "r1_f3", "severity": "P2", "title": "X", "location": "a.py:1"}
        entry = build_pushback_entry(outcome, finding, round_num=1)
        assert entry is None

    def test_skips_entry_for_fixed(self):
        outcome = {"id": "r1_f1", "action": "fixed", "summary": "Done."}
        finding = {"id": "r1_f1", "severity": "P0", "title": "X", "location": "a.py:1"}
        entry = build_pushback_entry(outcome, finding, round_num=1)
        assert entry is None

    def test_includes_p0_deferred(self):
        outcome = {"id": "r1_f1", "action": "deferred", "reasoning": "Out of scope."}
        finding = {"id": "r1_f1", "severity": "P0", "title": "X", "location": "a.py:1"}
        entry = build_pushback_entry(outcome, finding, round_num=1)
        assert entry is not None
        assert "DEFERRED" in entry

    def test_append_and_read_log(self, tmp_path):
        d = str(tmp_path)
        append_pushback_log(d, "### Round 1\nREJECTED: [r1_f2] ...\n")
        append_pushback_log(d, "### Round 2\nDEFERRED: [r2_f1] ...\n")
        log = read_pushback_log(d)
        assert "Round 1" in log
        assert "Round 2" in log


class TestDeferredItems:
    def test_append_and_read(self, tmp_path):
        d = str(tmp_path)
        append_deferred_item(d, {
            "id": "r1_f3", "severity": "P1",
            "title": "Race condition", "location": "cache.py:67",
            "reasoning": "Out of scope."
        })
        items = read_deferred_items(d)
        assert len(items) == 1
        assert items[0]["id"] == "r1_f3"


class TestValidateOutcomes:
    def test_valid_outcomes(self):
        findings = [{"id": "r1_f1"}, {"id": "r1_f2"}]
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Done"},
            {"id": "r1_f2", "action": "rejected", "reasoning": "No"},
        ]
        missing, stray = validate_outcomes(findings, outcomes)
        assert missing == []
        assert stray == []

    def test_missing_outcome(self):
        findings = [{"id": "r1_f1"}, {"id": "r1_f2"}]
        outcomes = [{"id": "r1_f1", "action": "fixed", "summary": "Done"}]
        missing, stray = validate_outcomes(findings, outcomes)
        assert "r1_f2" in missing
        assert stray == []

    def test_stray_outcome_id(self):
        findings = [{"id": "r1_f1"}]
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Done"},
            {"id": "r1_f99", "action": "rejected", "reasoning": "Typo"},
        ]
        missing, stray = validate_outcomes(findings, outcomes)
        assert missing == []
        assert "r1_f99" in stray


class TestDiffSizing:
    def test_excludes_lock_files(self):
        files = ["src/app.py", "package-lock.json", "src/util.py"]
        relevant, excluded = compute_relevant_diff_size(files)
        assert "package-lock.json" not in relevant
        assert "src/app.py" in relevant
        assert excluded == 1

    def test_excludes_vendor_dirs(self):
        files = ["src/app.py", "vendor/lib/foo.php"]
        relevant, excluded = compute_relevant_diff_size(files)
        assert len(relevant) == 1
        assert excluded == 1

    def test_excludes_images(self):
        files = ["src/app.py", "assets/logo.png", "assets/icon.svg"]
        relevant, excluded = compute_relevant_diff_size(files)
        assert len(relevant) == 1
        assert excluded == 2

    def test_all_noise_returns_empty(self):
        files = ["package-lock.json", "assets/logo.png"]
        relevant, excluded = compute_relevant_diff_size(files)
        assert len(relevant) == 0
        assert excluded == 2

    def test_empty_input(self):
        relevant, excluded = compute_relevant_diff_size([])
        assert relevant == []
        assert excluded == 0

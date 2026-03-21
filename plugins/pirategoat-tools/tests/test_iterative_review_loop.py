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

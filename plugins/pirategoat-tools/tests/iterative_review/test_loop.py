"""Tests for iterative_review.loop — state, convergence, max rounds."""

import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # iterative_review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from iterative_review.loop import (
    read_loop_state,
    write_loop_state,
    compute_max_rounds,
    check_convergence,
    DEFAULT_STATE,
    MAX_ROUNDS_HARD_LIMIT,
    build_pushback_entry,
    append_pushback_log,
    read_pushback_log,
    append_deferred_item,
    read_deferred_items,
    validate_outcomes,
    compute_relevant_diff_size,
    outcome_severity,
)
from iterative_review.paths import iterative_artifact_path


class TestMaxRounds:
    """Diff-size-based max rounds computation — a step function over
    _MAX_ROUNDS_TABLE's thresholds. Each row is a threshold's first value
    plus one below it; the bucket's upper edge is implied by the next
    lower edge, so no separate "just under the next threshold" row is
    needed."""

    @pytest.mark.parametrize("diff_lines,expected", [
        (0, 3),
        (199, 3),
        (200, 4),
        (500, 5),
        (700, 6),
        (1000, 7),
        (2000, 8),
        (3000, 9),
        (5000, 10),
        (10000, 12),
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
        state_path = iterative_artifact_path(d, "state")
        state_path.parent.mkdir(parents=True)
        state_path.write_text("not json{{{")
        state = read_loop_state(str(d))
        assert state["current_round"] == 0


class TestConvergence:
    """check_convergence's priority ladder: hard_limit > zero_findings >
    all_rejected > nitpicks_only > max_rounds > continue (None)."""

    @pytest.mark.parametrize(
        "findings_count,all_p3,all_rejected,current_round,max_rounds,expected",
        [
            pytest.param(0, False, False, 1, 3, "zero_findings", id="zero-findings"),
            pytest.param(3, False, False, 3, 3, "max_rounds", id="max-rounds-reached"),
            pytest.param(2, False, True, 1, 3, "all_rejected", id="all-rejected"),
            pytest.param(2, True, False, 1, 3, "nitpicks_only", id="nitpicks-only"),
            pytest.param(3, False, False, 1, 3, None, id="continue-no-condition-met"),
            pytest.param(0, False, False, 3, 3, "zero_findings", id="zero-findings-beats-max-rounds"),
            pytest.param(
                5, False, False, MAX_ROUNDS_HARD_LIMIT, MAX_ROUNDS_HARD_LIMIT + 5,
                "hard_limit", id="hard-limit-beats-everything",
            ),
        ],
    )
    def test_check_convergence(self, findings_count, all_p3, all_rejected,
                                current_round, max_rounds, expected):
        result = check_convergence(
            findings_count=findings_count, all_p3=all_p3, all_rejected=all_rejected,
            current_round=current_round, max_rounds=max_rounds,
        )
        assert result == expected


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

    @pytest.mark.parametrize("severity", ["P2", "P3"])
    def test_deferred_always_logged(self, severity):
        """Deferred items bypass the severity gate — P2/P3 are outside the
        gate ({P0, P1}) and would otherwise be skipped, but a reviewer
        must see every scope decision regardless of severity."""
        outcome = {"id": "r1_f3", "action": "deferred", "reasoning": "Out of scope."}
        finding = {"id": "r1_f3", "severity": severity, "title": "Edge case", "location": "b.py:5"}
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


class TestOutcomeSeverity:
    """outcome_severity prefers finding severity, falls back to outcome's
    when the finding's is absent, empty, or the degraded-round placeholder
    'unknown'."""

    @pytest.mark.parametrize(
        "outcome,finding,expected",
        [
            pytest.param(
                {"severity": "P3"}, {"severity": "P0"}, "P0",
                id="finding-severity-preferred",
            ),
            pytest.param(
                {"severity": "P1"}, None, "P1",
                id="outcome-fallback-finding-missing",
            ),
            pytest.param(
                {"severity": "P2"}, {"id": "r1_f1"}, "P2",
                id="outcome-fallback-finding-has-no-severity-key",
            ),
            pytest.param(
                {"severity": "P1"}, {"severity": ""}, "P1",
                id="outcome-fallback-finding-severity-empty-string",
            ),
            pytest.param(
                {"severity": "P1"}, {"severity": "unknown"}, "P1",
                id="outcome-fallback-finding-severity-unknown",
            ),
            pytest.param(
                {}, {"severity": "unknown"}, "unknown",
                id="unknown-when-neither-has-real-severity",
            ),
        ],
    )
    def test_outcome_severity(self, outcome, finding, expected):
        assert outcome_severity(outcome, finding) == expected

"""Tests for iterative_review.briefing — evaluation briefings and completion."""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # iterative_review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from iterative_review.briefing import (
    format_evaluation_briefing,
    format_completion_briefing,
    format_degraded_briefing,
    format_timeout_briefing,
)
from iterative_review.paths import round_artifact_path


SAMPLE_FINDINGS = [
    {"id": "r1_f1", "severity": "P0", "title": "SQL injection", "body": "User input unsanitized.", "location": "db.py:42-45"},
    {"id": "r1_f2", "severity": "P1", "title": "Missing null check", "body": "May crash on None.", "location": "handler.py:10"},
    {"id": "r1_f3", "severity": "P3", "title": "Magic number", "body": "Use constant.", "location": "utils.py:30"},
]


class TestEvaluationBriefing:
    def test_contains_header_with_round(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=2, merge_base="abc123def", diff_lines=750)
        assert "Review Round 2" in text
        assert "Evaluate" in text
        assert "abc123def" in text
        assert "750" in text

    def test_contains_all_finding_ids(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert "[r1_f1]" in text
        assert "[r1_f2]" in text
        assert "[r1_f3]" in text
        assert "[P0]" in text
        assert "[P1]" in text
        assert "[P3]" in text
        assert "db.py:42-45" in text
        assert "handler.py:10" in text

    def test_contains_evaluation_steps(self, tmp_path):
        outcomes_path = round_artifact_path(tmp_path, 3, "outcomes")
        text = format_evaluation_briefing(
            SAMPLE_FINDINGS,
            round_num=3,
            merge_base="abc",
            diff_lines=100,
            outcomes_path=outcomes_path,
        )
        assert "Evaluate" in text
        assert "1. READ" in text
        assert "2. VERIFY" in text
        assert "3. EVALUATE" in text
        assert "4. DECIDE" in text
        assert "Fix discipline" in text
        assert str(outcomes_path) in text
        assert "r3_f1" in text
        assert "r3_f2" in text

    def test_nitpicks_only_adds_note(self):
        p3_only = [{"id": "r2_f1", "severity": "P3", "title": "Nit", "body": "Nit detail.", "location": "a.py:1"}]
        text = format_evaluation_briefing(p3_only, round_num=2, merge_base="abc", diff_lines=100)
        assert "P3" in text
        assert "no further rounds" in text

    @pytest.mark.parametrize(
        "round_num,traps,stalemate,escalation",
        [
            pytest.param(1, True, False, False, id="round-1"),
            pytest.param(2, False, True, False, id="round-2"),
            pytest.param(3, False, True, True, id="round-3"),
        ],
    )
    def test_round_gated_blocks(self, round_num, traps, stalemate, escalation):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=round_num, merge_base="abc", diff_lines=100)
        lower = text.lower()
        assert ("cognitive traps" in lower) is traps
        assert ("stalemate" in lower) is stalemate
        assert ("force-defer" in lower) is escalation


class TestCompletionBriefing:
    def test_contains_human_readable_reason_and_stats(self):
        text = format_completion_briefing(
            termination="zero_findings", rounds_completed=2,
            total_fixed=3, total_rejected=1, total_deferred=0
        )
        assert "clean" in text.lower()
        assert "complete" in text.lower()
        assert "2" in text
        assert "3" in text

    def test_unknown_termination_falls_back_to_raw(self):
        text = format_completion_briefing(
            termination="some_new_reason", rounds_completed=1,
            total_fixed=0, total_rejected=0, total_deferred=0
        )
        assert "some_new_reason" in text


class TestDegradedBriefing:
    def test_uses_round_num_for_outcomes_file(self, tmp_path):
        outcomes_path = round_artifact_path(tmp_path, 3, "outcomes")
        text = format_degraded_briefing(
            round_num=3, raw_id="r3_raw", outcomes_path=outcomes_path
        )
        assert str(outcomes_path) in text
        assert "r3_raw" in text
        assert "unstructured" in text.lower()


class TestTimeoutBriefing:
    @pytest.mark.parametrize(
        "round_num,autonomous,at_round_cap,must_contain,must_not_contain",
        [
            pytest.param(
                2, True, False,
                ["skip", "proceed"],
                ["ask the user", "ask the human"],
                id="autonomous",
            ),
            pytest.param(
                1, False, False,
                ["ask", "retry", "skip", "stop", "30"],
                [],
                id="interactive-under-cap",
            ),
            pytest.param(
                3, False, True,
                ["retry", "stop"],
                ["skip"],
                id="interactive-at-cap",
            ),
        ],
    )
    def test_timeout_briefing_modes(self, round_num, autonomous, at_round_cap, must_contain, must_not_contain):
        text = format_timeout_briefing(
            round_num=round_num, timeout_seconds=1800,
            autonomous=autonomous, at_round_cap=at_round_cap,
        )
        lower = text.lower()
        assert "timed out" in lower
        for phrase in must_contain:
            assert phrase in lower
        for phrase in must_not_contain:
            assert phrase not in lower

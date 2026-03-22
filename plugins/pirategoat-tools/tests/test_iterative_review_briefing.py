"""Tests for iterative_review.briefing — evaluation briefings and completion."""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from iterative_review.briefing import (
    format_evaluation_briefing,
    format_completion_briefing,
    format_degraded_briefing,
)


SAMPLE_FINDINGS = [
    {"id": "r1_f1", "severity": "P0", "title": "SQL injection", "body": "User input unsanitized.", "location": "db.py:42-45"},
    {"id": "r1_f2", "severity": "P1", "title": "Missing null check", "body": "May crash on None.", "location": "handler.py:10"},
    {"id": "r1_f3", "severity": "P3", "title": "Magic number", "body": "Use constant.", "location": "utils.py:30"},
]


class TestEvaluationBriefing:
    def test_contains_header_with_round(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=2, merge_base="abc123", diff_lines=500)
        assert "Review Round 2" in text
        assert "Evaluate" in text

    def test_contains_all_finding_ids(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert "[r1_f1]" in text
        assert "[r1_f2]" in text
        assert "[r1_f3]" in text

    def test_contains_severity_labels(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert "[P0]" in text
        assert "[P1]" in text
        assert "[P3]" in text

    def test_contains_file_locations(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert "db.py:42-45" in text
        assert "handler.py:10" in text

    def test_contains_evaluation_steps(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert "Evaluate" in text
        assert "1. READ" in text
        assert "2. VERIFY" in text
        assert "3. EVALUATE" in text
        assert "4. DECIDE" in text
        assert "Fix discipline" in text
        assert "outcomes.json" in text

    def test_contains_merge_base_visibility(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc123def", diff_lines=750)
        assert "abc123def" in text
        assert "750" in text

    def test_nitpicks_only_adds_note(self):
        p3_only = [{"id": "r2_f1", "severity": "P3", "title": "Nit", "body": "Nit detail.", "location": "a.py:1"}]
        text = format_evaluation_briefing(p3_only, round_num=2, merge_base="abc", diff_lines=100)
        assert "P3" in text
        assert "no further rounds" in text

    def test_round_2_includes_stalemate_prompt(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=2, merge_base="abc", diff_lines=100)
        assert "stalemate" in text.lower() or "own it" in text.lower()

    def test_round_1_no_stalemate_prompt(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert "stalemate" not in text.lower()

    def test_contains_commit_instruction(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert "commit" in text.lower()


class TestCompletionBriefing:
    def test_contains_termination_reason(self):
        text = format_completion_briefing(
            termination="zero_findings", rounds_completed=2,
            total_fixed=3, total_rejected=1, total_deferred=0
        )
        assert "zero_findings" in text
        assert "complete" in text.lower()

    def test_contains_stats(self):
        text = format_completion_briefing(
            termination="max_rounds", rounds_completed=5,
            total_fixed=8, total_rejected=2, total_deferred=1
        )
        assert "5" in text
        assert "8" in text


class TestDegradedBriefing:
    def test_contains_raw_output_reference(self):
        text = format_degraded_briefing(round_num=1, raw_id="r1_raw")
        assert "r1_raw" in text
        assert "unstructured" in text.lower() or "manually" in text.lower()

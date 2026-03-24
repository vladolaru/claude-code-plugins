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
    _TERMINATION_REASONS,
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

    def test_contains_phase_headers(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert "### Phase 1: Evaluate Findings" in text
        assert "### Phase 2: Fix" in text
        assert "### Phase 3: Commit and Record" in text

    def test_outcomes_format_uses_round_num(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=3, merge_base="abc", diff_lines=100)
        assert "r3_f1" in text
        assert "r3_f2" in text
        assert "round-3-outcomes.json" in text

    def test_outcomes_format_shows_all_actions(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert '"fixed"' in text
        assert '"rejected"' in text
        assert '"deferred"' in text
        assert '"summary"' in text
        assert '"reasoning"' in text

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
        assert "stalemate" in text.lower()

    def test_round_1_no_stalemate_prompt(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert "stalemate" not in text.lower()

    def test_round_1_includes_cognitive_traps(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert "Cognitive traps" in text
        assert "Rubber-stamping" in text
        assert "Positional entrenchment" in text
        assert "Scope inflation" in text

    def test_round_2_no_cognitive_traps(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=2, merge_base="abc", diff_lines=100)
        assert "Cognitive traps" not in text

    def test_round_2_correction_pattern(self):
        """Round 2+ prompts agent to state what was wrong in prior reasoning."""
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=2, merge_base="abc", diff_lines=100)
        assert "what was wrong" in text.lower()
        assert "prior reasoning" in text.lower()
        assert "correction" in text.lower()

    def test_round_3_includes_stalemate_escalation(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=3, merge_base="abc", diff_lines=100)
        assert "force-defer" in text.lower()
        assert "stalemate escalation" in text.lower()

    def test_round_2_no_stalemate_escalation(self):
        """Round 2 has correction prompt but not the escalation guidance."""
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=2, merge_base="abc", diff_lines=100)
        assert "force-defer" not in text.lower()

    def test_contains_commit_instruction(self):
        text = format_evaluation_briefing(SAMPLE_FINDINGS, round_num=1, merge_base="abc", diff_lines=100)
        assert "commit" in text.lower()


class TestCompletionBriefing:
    def test_contains_human_readable_reason(self):
        text = format_completion_briefing(
            termination="zero_findings", rounds_completed=2,
            total_fixed=3, total_rejected=1, total_deferred=0
        )
        assert "clean" in text.lower()
        assert "complete" in text.lower()

    def test_contains_stats(self):
        text = format_completion_briefing(
            termination="max_rounds", rounds_completed=5,
            total_fixed=8, total_rejected=2, total_deferred=1
        )
        assert "5" in text
        assert "8" in text

    def test_all_termination_reasons_have_descriptions(self):
        for key in ["zero_findings", "all_rejected", "nitpicks_only",
                     "max_rounds", "hard_limit", "codex_unavailable",
                     "codex_timeout"]:
            assert key in _TERMINATION_REASONS

    def test_unknown_termination_falls_back_to_raw(self):
        text = format_completion_briefing(
            termination="some_new_reason", rounds_completed=1,
            total_fixed=0, total_rejected=0, total_deferred=0
        )
        assert "some_new_reason" in text

    def test_contains_actionable_instruction(self):
        text = format_completion_briefing(
            termination="zero_findings", rounds_completed=1,
            total_fixed=0, total_rejected=0, total_deferred=0
        )
        assert "report" in text.lower()
        assert "deferred" in text.lower()


class TestDegradedBriefing:
    def test_contains_raw_output_reference(self):
        text = format_degraded_briefing(round_num=1, raw_id="r1_raw")
        assert "r1_raw" in text
        assert "unstructured" in text.lower()

    def test_contains_evaluation_guidance(self):
        text = format_degraded_briefing(round_num=1, raw_id="r1_raw")
        assert "READ" in text
        assert "VERIFY" in text
        assert "EVALUATE" in text
        assert "DECIDE" in text

    def test_uses_round_num_for_outcomes_file(self):
        text = format_degraded_briefing(round_num=3, raw_id="r3_raw")
        assert "round-3-outcomes.json" in text
        assert "r3_raw" in text


class TestTimeoutBriefing:
    def test_interactive_asks_user(self):
        text = format_timeout_briefing(round_num=1, timeout_seconds=1800, autonomous=False)
        assert "timed out" in text.lower()
        assert "user" in text.lower() or "ask" in text.lower()

    def test_interactive_offers_options(self):
        text = format_timeout_briefing(round_num=1, timeout_seconds=1800, autonomous=False)
        assert "retry" in text.lower()
        assert "skip" in text.lower()
        assert "stop" in text.lower()

    def test_autonomous_skips_round(self):
        text = format_timeout_briefing(round_num=2, timeout_seconds=1800, autonomous=True)
        assert "timed out" in text.lower()
        assert "skip" in text.lower()
        assert "advance" in text.lower() or "proceed" in text.lower()

    def test_autonomous_no_user_prompt(self):
        text = format_timeout_briefing(round_num=1, timeout_seconds=1800, autonomous=True)
        lower = text.lower()
        assert "ask the user" not in lower
        assert "ask the human" not in lower

    def test_contains_round_number(self):
        text = format_timeout_briefing(round_num=3, timeout_seconds=1800, autonomous=False)
        assert "3" in text

    def test_contains_timeout_duration(self):
        text = format_timeout_briefing(round_num=1, timeout_seconds=1800, autonomous=False)
        assert "30" in text  # 30 minutes

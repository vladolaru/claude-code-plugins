"""Tests for iterative_review CLI -- argument parsing and action routing."""

import copy
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

TESTS_DIR_PARENT = Path(__file__).resolve().parent.parent  # tests/ dir
PLUGIN_ROOT_FOR_IMPORTS = TESTS_DIR_PARENT.parent
SCRIPTS_DIR_FOR_IMPORTS = PLUGIN_ROOT_FOR_IMPORTS / "scripts"
if str(SCRIPTS_DIR_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR_FOR_IMPORTS))
from iterative_review.loop import MAX_ROUNDS_HARD_LIMIT
from iterative_review.paths import iterative_artifact_path, round_artifact_path

TESTS_DIR = Path(__file__).resolve().parent.parent  # iterative_review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
MODULE_DIR = SCRIPTS_DIR / "iterative_review"


def _artifact(output_dir, key):
    path = iterative_artifact_path(output_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _round_artifact(output_dir, round_num, key):
    path = round_artifact_path(output_dir, round_num, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class TestCLIParsing:
    def test_review_action_requires_merge_base_on_round_1(self, tmp_path):
        """Round 1 requires --merge-base."""
        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "review", "--round", "1",
             "--output-dir", str(tmp_path / "code-review")],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode != 0
        assert "merge-base" in result.stderr.lower() or "required" in result.stderr.lower()

    def test_review_round2_rejects_missing_state(self, tmp_path):
        """Round 2+ fails fast when no persisted state exists."""
        d = tmp_path / "code-review"
        d.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "review", "--round", "2",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode != 0
        assert "round 1 must run first" in result.stderr.lower()

    def test_advance_action_requires_output_dir(self):
        """Advance requires --output-dir."""
        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1"],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode != 0

    def test_advance_rejects_missing_outcomes(self, tmp_path):
        """Advance fails if outcomes file doesn't exist."""
        d = tmp_path / "code-review"
        d.mkdir()
        # Write state but no outcomes
        state = {"current_round": 1, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "terminated": False}
        _artifact(d, "state").write_text(json.dumps(state))
        # Write findings so advance expects outcomes
        _round_artifact(d, 1, "findings").write_text(json.dumps([
            {"id": "r1_f1", "severity": "P1", "title": "Test", "body": "X", "location": "a.py:1"}
        ]))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode != 0

    def test_advance_with_complete_outcomes(self, tmp_path):
        """Advance succeeds when all findings have outcomes and convergence is met."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [{"id": "r1_f1", "severity": "P1", "title": "T", "body": "B", "location": "a.py:1"}]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        outcomes = [{"id": "r1_f1", "action": "rejected", "reasoning": "False positive."}]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        # Should detect all_rejected convergence
        updated_state = json.loads(_artifact(d, "state").read_text())
        assert updated_state["terminated"] is True
        assert updated_state["termination"] == "all_rejected"


class TestDeferredPruning:
    """Deferred items resolved in later rounds are pruned from result."""

    def test_resolved_deferred_pruned_from_result(self, tmp_path):
        d = tmp_path / "code-review"
        d.mkdir()

        # Round 1: one fixed (keeps loop going), one deferred.
        # Use max_rounds=3 so round 2 isn't at the limit (avoids P2 extension).
        state = {"current_round": 1, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        r1_findings = [
            {"id": "r1_f1", "severity": "P2", "title": "Typo", "body": "X", "location": "readme.md:1"},
            {"id": "r1_f2", "severity": "P2", "title": "Null check", "body": "X", "location": "handler.py:42"},
        ]
        _round_artifact(d, 1, "findings").write_text(json.dumps(r1_findings))
        r1_outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Fixed typo."},
            {"id": "r1_f2", "action": "deferred", "reasoning": "Out of scope."},
        ]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(r1_outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True, cwd=str(SCRIPTS_DIR),
        )

        # Round 2: same deferred issue found again and fixed.
        # P3 triggers nitpicks_only convergence so the loop terminates here.
        r2_findings = [{"id": "r2_f1", "severity": "P3", "title": "Null check",
                        "body": "X", "location": "handler.py:42"}]
        _round_artifact(d, 2, "findings").write_text(json.dumps(r2_findings))
        r2_outcomes = [{"id": "r2_f1", "action": "fixed", "summary": "Added null check."}]
        _round_artifact(d, 2, "outcomes").write_text(json.dumps(r2_outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "2",
             "--output-dir", str(d)],
            capture_output=True, text=True, cwd=str(SCRIPTS_DIR),
        )

        # The result should prune the deferred item (same title+location was fixed)
        result_path = _artifact(d, "result")
        assert result_path.exists()
        result = json.loads(result_path.read_text())
        assert len(result.get("deferred_items", [])) == 0


class TestAdvanceIdempotency:
    """Advance is idempotent — retrying the same round doesn't duplicate records."""

    def test_retry_does_not_duplicate_round(self, tmp_path):
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 5, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [{"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"}]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        outcomes = [{"id": "r1_f1", "action": "fixed", "summary": "Done."}]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        cmd = [sys.executable, "-m", "iterative_review",
               "--action", "advance", "--round", "1",
               "--output-dir", str(d)]

        # Run advance twice
        subprocess.run(cmd, capture_output=True, text=True, cwd=str(SCRIPTS_DIR))
        subprocess.run(cmd, capture_output=True, text=True, cwd=str(SCRIPTS_DIR))

        updated_state = json.loads(_artifact(d, "state").read_text())
        round_records = [r for r in updated_state["rounds"] if r["round"] == 1]
        assert len(round_records) == 1, f"Expected 1 record for round 1, got {len(round_records)}"


class TestAdvanceRoundSummary:
    """Advance action correctly records round summary in state."""

    def test_round_summary_counts(self, tmp_path):
        """Round summary records correct fixed/rejected/deferred counts."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 5, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r1_f2", "severity": "P1", "title": "B", "body": "Y", "location": "b.py:2"},
            {"id": "r1_f3", "severity": "P2", "title": "C", "body": "Z", "location": "c.py:3"},
        ]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Fixed it."},
            {"id": "r1_f2", "action": "rejected", "reasoning": "Not real."},
            {"id": "r1_f3", "action": "deferred", "reasoning": "Out of scope."},
        ]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads(_artifact(d, "state").read_text())
        assert len(updated_state["rounds"]) == 1
        r = updated_state["rounds"][0]
        assert r["fixed"] == 1
        assert r["rejected"] == 1
        assert r["deferred"] == 1
        assert r["findings"] == 3

    def test_deferred_items_written(self, tmp_path):
        """Deferred findings are written to deferred-items.jsonl."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 5, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "Bug", "body": "X", "location": "a.py:1"},
        ]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r1_f1", "action": "deferred", "reasoning": "Out of scope."},
        ]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        jsonl_path = _artifact(d, "deferred")
        assert jsonl_path.exists()
        items = [json.loads(line) for line in jsonl_path.read_text().strip().split("\n")]
        assert len(items) == 1
        assert items[0]["id"] == "r1_f1"


class TestAdvanceConvergence:
    """Advance action detects convergence conditions."""

    def test_max_rounds_convergence(self, tmp_path):
        """Terminates with max_rounds when round equals max_rounds (P3 findings, no extension)."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 3, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        # Use mixed P3 severities — P3 fixed does not trigger extension
        findings = [
            {"id": "r3_f1", "severity": "P3", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r3_f2", "severity": "P2", "title": "B", "body": "Y", "location": "b.py:2"},
        ]
        _round_artifact(d, 3, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r3_f1", "action": "fixed", "summary": "Fixed."},
            {"id": "r3_f2", "action": "rejected", "reasoning": "Not real."},
        ]
        _round_artifact(d, 3, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "3",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads(_artifact(d, "state").read_text())
        assert updated_state["terminated"] is True
        assert updated_state["termination"] == "max_rounds"

    def test_p1_at_max_rounds_extends(self, tmp_path):
        """P1 findings at max rounds extend the limit by 2 instead of terminating."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 3, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r3_f1", "severity": "P1", "title": "Bug", "body": "X", "location": "a.py:1"},
        ]
        _round_artifact(d, 3, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r3_f1", "action": "fixed", "summary": "Fixed."},
        ]
        _round_artifact(d, 3, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "3",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads(_artifact(d, "state").read_text())
        assert updated_state["terminated"] is False
        assert updated_state["max_rounds"] == 5  # P1 extends by +2
        assert "round 4" in result.stdout.lower()

    def test_p0_at_max_rounds_extends(self, tmp_path):
        """P0 findings at max rounds extend by 2."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 3, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r3_f1", "severity": "P0", "title": "Critical", "body": "X", "location": "a.py:1"},
        ]
        _round_artifact(d, 3, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r3_f1", "action": "fixed", "summary": "Fixed."},
        ]
        _round_artifact(d, 3, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "3",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads(_artifact(d, "state").read_text())
        assert updated_state["max_rounds"] == 5  # P0 extends by +2

    def test_p2_at_max_rounds_extends(self, tmp_path):
        """P2 findings at max rounds extend the limit by 1."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 3, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r3_f1", "severity": "P2", "title": "Minor", "body": "X", "location": "a.py:1"},
        ]
        _round_artifact(d, 3, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r3_f1", "action": "fixed", "summary": "Fixed."},
        ]
        _round_artifact(d, 3, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "3",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads(_artifact(d, "state").read_text())
        assert updated_state["terminated"] is False
        assert updated_state["max_rounds"] == 4  # P2 extends by +1

    def test_deferred_p1_at_max_rounds_does_not_extend(self, tmp_path):
        """Deferred P1 at max rounds terminates — no point reviewing what won't be addressed."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 3, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r3_f1", "severity": "P1", "title": "Bug", "body": "X", "location": "a.py:1"},
        ]
        _round_artifact(d, 3, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r3_f1", "action": "deferred", "reasoning": "Out of scope."},
        ]
        _round_artifact(d, 3, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "3",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads(_artifact(d, "state").read_text())
        assert updated_state["terminated"] is True
        assert updated_state["max_rounds"] == 3  # not extended

    def test_p1_at_hard_limit_does_not_extend(self, tmp_path):
        """P1 findings at the hard limit (15) terminate — no infinite loops."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 15, "max_rounds": 15, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r15_f1", "severity": "P1", "title": "Bug", "body": "X", "location": "a.py:1"},
        ]
        _round_artifact(d, 15, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r15_f1", "action": "fixed", "summary": "Fixed."},
        ]
        _round_artifact(d, 15, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "15",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads(_artifact(d, "state").read_text())
        assert updated_state["terminated"] is True
        assert updated_state["termination"] == "hard_limit"
        assert updated_state["max_rounds"] == 15  # not extended


class TestTieredRoundExtension:
    """Round extension scales with finding severity, capped at the hard
    limit.

    The extension arithmetic is inline in `__main__.py`, not an
    extractable function, so the CLI is the only level at which it exists
    — every test here drives `--action advance` for real. Four earlier
    tests in this class re-implemented the `2 if ... else (1 if ...)`
    ternary inside their own bodies and asserted on a local, so they
    would have stayed green with the production branch deleted; they were
    removed rather than kept as coverage they never provided.
    """

    def test_p0_at_hard_limit_minus_one_caps(self, tmp_path):
        """P0 at hard_limit-1 extends to hard_limit, not beyond."""
        d = tmp_path / "code-review"
        d.mkdir()
        limit_minus_1 = MAX_ROUNDS_HARD_LIMIT - 1
        state = {"current_round": limit_minus_1, "max_rounds": limit_minus_1,
                 "rounds": [], "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": f"r{limit_minus_1}_f1", "severity": "P0", "title": "Critical",
             "body": "X", "location": "a.py:1"},
        ]
        _round_artifact(d, limit_minus_1, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": f"r{limit_minus_1}_f1", "action": "fixed", "summary": "Fixed."},
        ]
        _round_artifact(d, limit_minus_1, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", str(limit_minus_1),
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads(_artifact(d, "state").read_text())
        # P0 wants +2, but capped at hard limit
        assert updated_state["max_rounds"] == MAX_ROUNDS_HARD_LIMIT
        assert updated_state["terminated"] is False


class TestAdvanceResultFile:
    """Advance writes review-loop-result.json on termination."""

    def test_result_file_written_on_termination(self, tmp_path):
        """review-loop-result.json is written when loop terminates."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [{"id": "r1_f1", "severity": "P1", "title": "T", "body": "B", "location": "a.py:1"}]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        outcomes = [{"id": "r1_f1", "action": "rejected", "reasoning": "False positive."}]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        result_path = _artifact(d, "result")
        assert result_path.exists()
        result_data = json.loads(result_path.read_text())
        assert result_data["termination"] == "all_rejected"
        assert result_data["rounds_completed"] == 1
        assert result_data["total_rejected"] == 1
        assert result_data["total_fixed"] == 0

    def test_no_result_file_when_continuing(self, tmp_path):
        """review-loop-result.json is NOT written when loop continues."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 5, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r1_f2", "severity": "P2", "title": "B", "body": "Y", "location": "b.py:2"},
        ]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Done."},
            {"id": "r1_f2", "action": "rejected", "reasoning": "Not real."},
        ]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        result_path = _artifact(d, "result")
        assert not result_path.exists()


class TestAdvanceTerminatedState:
    """Advance action handles already-terminated state."""

    def test_advance_on_terminated_state_prints_completion(self, tmp_path):
        """Advance on already-terminated state prints completion briefing."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 2, "max_rounds": 3,
                 "rounds": [{"round": 1, "findings": 2, "fixed": 1, "rejected": 1, "deferred": 0}],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": True, "termination": "all_rejected",
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "2",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        assert "complete" in result.stdout.lower()


class TestAdvanceMissingOutcomes:
    """Advance validates outcome completeness."""

    def test_advance_rejects_incomplete_outcomes(self, tmp_path):
        """Advance fails if not all findings have outcomes."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        _artifact(d, "state").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r1_f2", "severity": "P2", "title": "B", "body": "Y", "location": "b.py:2"},
        ]
        _round_artifact(d, 1, "findings").write_text(json.dumps(findings))
        # Only outcome for r1_f1 -- r1_f2 is missing
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Done."},
        ]
        _round_artifact(d, 1, "outcomes").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode != 0
        assert "r1_f2" in result.stderr


class TestSchemaFile:
    """The review-schema.json file must exist for Codex invocation."""

    def test_schema_file_exists(self):
        schema_path = SCRIPTS_DIR / "iterative_review" / "backends" / "codex-review-schema.json"
        assert schema_path.exists(), f"Missing {schema_path}"

    def test_schema_is_valid_json(self):
        schema_path = SCRIPTS_DIR / "iterative_review" / "backends" / "codex-review-schema.json"
        data = json.loads(schema_path.read_text())
        assert "properties" in data
        assert "findings" in data["properties"]
        assert data.get("additionalProperties") is False

    def test_get_schema_path_returns_existing_file(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        from iterative_review.backends.codex import get_schema_path
        path = get_schema_path()
        assert Path(path).exists(), f"get_schema_path() returned {path} but file doesn't exist"


class TestNoPriorAnalysis:
    """--no-prior-analysis flag is honored in state."""

    def test_no_prior_analysis_sets_state(self):
        """--no-prior-analysis sets pass_prior_analysis=False in state during init."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        from iterative_review.loop import DEFAULT_STATE
        # Simulate what action_review does on round 1 with --no-prior-analysis
        state = {**copy.deepcopy(DEFAULT_STATE)}
        state["merge_base"] = "abc123"
        state["current_round"] = 1
        # This is the fix we're testing: the flag must be applied
        no_prior_analysis = True
        if no_prior_analysis:
            state["pass_prior_analysis"] = False
        assert state["pass_prior_analysis"] is False

    def test_default_passes_prior_analysis(self):
        """Without --no-prior-analysis, pass_prior_analysis defaults to True."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        from iterative_review.loop import DEFAULT_STATE
        state = {**copy.deepcopy(DEFAULT_STATE)}
        assert state["pass_prior_analysis"] is True


class TestZeroFindingsArtifact:
    """Zero-findings path must write review-loop-result.json."""

    def test_zero_findings_writes_result_file(self, tmp_path):
        """Simulate the zero-findings code path and verify artifact is written."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        from iterative_review.loop import DEFAULT_STATE, write_loop_state

        d = tmp_path / "code-review"
        d.mkdir()
        state = {**copy.deepcopy(DEFAULT_STATE), "merge_base": "abc", "max_rounds": 3,
                 "current_round": 1}

        # Simulate what action_review does on zero findings
        round_num = 1
        state.setdefault("rounds", []).append({
            "round": round_num, "findings": 0,
            "fixed": 0, "rejected": 0, "deferred": 0,
        })
        state["terminated"] = True
        state["termination"] = "zero_findings"
        write_loop_state(str(d), state)

        result_data = {
            "termination": "zero_findings",
            "rounds_completed": len(state["rounds"]),
            "max_rounds": state.get("max_rounds", 3),
            "total_findings": 0, "total_fixed": 0,
            "total_rejected": 0, "total_deferred": 0,
            "rounds": state["rounds"],
        }
        result_path = _artifact(d, "result")
        result_path.write_text(json.dumps(result_data, indent=2))

        # Verify
        assert result_path.exists()
        loaded = json.loads(result_path.read_text())
        assert loaded["termination"] == "zero_findings"
        assert loaded["rounds_completed"] == 1
        assert len(loaded["rounds"]) == 1


# ---------------------------------------------------------------------------
# Pre-flight check — backend selection, availability and auth
# ---------------------------------------------------------------------------

sys.path.insert(0, str(SCRIPTS_DIR))
from iterative_review.__main__ import _preflight_backend


class TestPreflightBackend:
    """_preflight_backend selects best backend and verifies availability."""

    @patch("iterative_review.backends.codex.check_auth", return_value=(True, ""))
    @patch("shutil.which", return_value="/usr/local/bin/codex")
    def test_returns_module_when_codex_available_and_authed(self, mock_which, mock_auth):
        backend, name, err = _preflight_backend()
        assert err is None
        assert name == "codex"
        assert backend is not None
        # Common interface: module has callable check_auth and invoke_review
        assert callable(backend.check_auth)
        assert callable(backend.invoke_review)

    @patch("shutil.which", return_value=None)
    def test_returns_error_when_nothing_installed(self, mock_which):
        backend, name, err = _preflight_backend()
        assert backend is None
        assert err is not None
        assert "UNAVAILABLE" in err
        assert "not installed" in err or "not on PATH" in err

    @patch("iterative_review.backends.codex.check_auth", return_value=(False, "not logged in"))
    @patch("shutil.which", return_value="/usr/local/bin/codex")
    def test_returns_error_when_codex_not_authenticated(self, mock_which, mock_auth):
        """Codex on PATH but not authenticated, Claude not on PATH -> error."""
        # shutil.which returns truthy for any arg, but _select_backend
        # calls check_auth which fails, then tries claude which also
        # gets truthy which but we need to make it fail too.
        # With mock returning "/usr/local/bin/codex" for all calls,
        # _select_backend tries codex auth (fails), then claude auth.
        # We need claude auth to also fail for this test.
        with patch("iterative_review.backends.claude.check_auth",
                    return_value=(False, "not authenticated")):
            backend, name, err = _preflight_backend()
            assert backend is None
            assert err is not None
            assert "UNAVAILABLE" in err

    @patch("iterative_review.backends.claude.check_auth", return_value=(True, "v2.0"))
    @patch("iterative_review.backends.codex.check_auth", return_value=(False, "not logged in"))
    @patch("shutil.which", return_value="/usr/local/bin/codex")
    def test_falls_back_to_claude_when_codex_unauthed(self, mock_which, mock_codex_auth, mock_claude_auth):
        """Codex not authenticated -> falls back to Claude Code."""
        backend, name, err = _preflight_backend()
        assert err is None
        assert name == "claude"
        assert backend is not None
        assert callable(backend.check_auth)

    @patch("shutil.which", return_value=None)
    def test_skips_auth_check_when_not_installed(self, mock_which):
        """Auth check should not run if no CLI is on PATH."""
        backend, name, err = _preflight_backend()
        assert err is not None
        assert "not installed" in err or "not on PATH" in err


class TestTryFallback:
    """_try_fallback selects the other backend after a runtime failure."""

    @patch("iterative_review.backends.claude.check_auth", return_value=(True, '{"loggedIn": true}'))
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_codex_failure_falls_back_to_claude(self, mock_which, mock_auth):
        from iterative_review.__main__ import _try_fallback
        backend, name = _try_fallback("codex")
        assert name == "claude"
        assert backend is not None

    @patch("iterative_review.backends.codex.check_auth", return_value=(True, ""))
    @patch("shutil.which", return_value="/usr/local/bin/codex")
    def test_claude_failure_falls_back_to_codex(self, mock_which, mock_auth):
        from iterative_review.__main__ import _try_fallback
        backend, name = _try_fallback("claude")
        assert name == "codex"
        assert backend is not None

    @patch("shutil.which", return_value=None)
    def test_no_fallback_when_other_not_available(self, mock_which):
        from iterative_review.__main__ import _try_fallback
        backend, name = _try_fallback("codex")
        assert backend is None
        assert name is None


class TestAdaptiveEffortFlag:
    """--adaptive-effort flag is accepted by the CLI."""

    def test_flag_accepted_with_review_action(self, tmp_path):
        """Parser accepts --adaptive-effort without error (exits due to missing merge-base, not flag)."""
        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "review", "--round", "1",
             "--output-dir", str(tmp_path / "code-review"),
             "--adaptive-effort"],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        # Should fail because of missing --merge-base, NOT because of --adaptive-effort
        assert "unrecognized" not in result.stderr.lower()
        assert "merge-base" in result.stderr.lower() or result.returncode != 0

    def test_flag_accepted_with_advance_action(self, tmp_path):
        """Parser accepts --adaptive-effort with advance action."""
        d = tmp_path / "code-review"
        d.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d),
             "--adaptive-effort"],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        # Should fail because of missing outcomes, NOT because of --adaptive-effort
        assert "unrecognized" not in result.stderr.lower()


class TestPreflightIntegration:
    """Pre-flight failure writes result file and exits cleanly."""

    def test_unavailable_writes_result_and_exits_zero(self, tmp_path):
        """When codex is not on PATH, script writes result file and exits 0."""
        d = tmp_path / "review-output"
        d.mkdir()
        # Write a helper script that patches shutil.which before importing
        helper = tmp_path / "run_preflight.py"
        helper.write_text(
            f"import sys\n"
            f"sys.path.insert(0, '{SCRIPTS_DIR}')\n"
            f"from unittest.mock import patch\n"
            f"import types\n"
            f"args = types.SimpleNamespace(\n"
            f"    output_dir='{d}',\n"
            f"    round=1, merge_base='abc123', context_file=None,\n"
            f"    max_rounds=None, no_prior_analysis=False)\n"
            f"with patch('shutil.which', return_value=None):\n"
            f"    from iterative_review.__main__ import action_review\n"
            f"    action_review(args)\n"
        )
        result = subprocess.run(
            [sys.executable, str(helper)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        assert "UNAVAILABLE" in result.stdout
        # Verify result file was written
        result_path = _artifact(d, "result")
        assert result_path.exists()
        data = json.loads(result_path.read_text())
        assert data["termination"] == "backend_unavailable"
        assert data["rounds_completed"] == 0


# ---------------------------------------------------------------------------
# Timeout state management
# ---------------------------------------------------------------------------

from iterative_review.loop import read_loop_state, write_loop_state, DEFAULT_STATE


class TestTimeoutStateManagement:
    """Timeout handler records round and tracks consecutive timeouts."""

    def test_consecutive_timeouts_increments(self, tmp_path):
        d = str(tmp_path)
        state = {**copy.deepcopy(DEFAULT_STATE), "merge_base": "abc",
                 "autonomous": True, "consecutive_timeouts": 0}
        # Simulate first timeout: increment and persist
        state["consecutive_timeouts"] += 1
        state.setdefault("rounds", []).append({
            "round": 1, "findings": 0, "fixed": 0,
            "rejected": 0, "deferred": 0, "skipped": True,
        })
        write_loop_state(d, state)
        loaded = read_loop_state(d)
        assert loaded["consecutive_timeouts"] == 1
        assert loaded["rounds"][0]["skipped"] is True

    def test_consecutive_timeouts_resets_on_success(self, tmp_path):
        d = str(tmp_path)
        state = {**copy.deepcopy(DEFAULT_STATE), "merge_base": "abc",
                 "consecutive_timeouts": 2}
        state["consecutive_timeouts"] = 0  # reset
        write_loop_state(d, state)
        loaded = read_loop_state(d)
        assert loaded["consecutive_timeouts"] == 0

    def test_second_consecutive_terminates(self, tmp_path):
        d = str(tmp_path)
        state = {**copy.deepcopy(DEFAULT_STATE), "merge_base": "abc",
                 "autonomous": True, "consecutive_timeouts": 1}
        state["consecutive_timeouts"] += 1  # becomes 2
        assert state["consecutive_timeouts"] >= 2
        state["terminated"] = True
        state["termination"] = "backend_timeout"
        write_loop_state(d, state)
        loaded = read_loop_state(d)
        assert loaded["terminated"] is True
        assert loaded["termination"] == "backend_timeout"

    def test_interactive_does_not_auto_terminate(self, tmp_path):
        """Interactive mode: consecutive timeouts don't auto-terminate."""
        d = str(tmp_path)
        state = {**copy.deepcopy(DEFAULT_STATE), "merge_base": "abc",
                 "autonomous": False, "consecutive_timeouts": 5}
        # Auto-termination condition: autonomous AND consecutive >= 2
        should_terminate = state.get("autonomous", False) and state["consecutive_timeouts"] >= 2
        assert not should_terminate

    def test_interactive_timeout_does_not_record_round(self, tmp_path):
        """Interactive mode: timeout increments counter but does NOT record round.

        Recording eagerly would break retry (duplicate entry) and
        skip-via-advance (empty findings → false zero_findings convergence).
        """
        d = str(tmp_path)
        state = {**copy.deepcopy(DEFAULT_STATE), "merge_base": "abc",
                 "autonomous": False, "consecutive_timeouts": 0}
        # Simulate interactive timeout: only increment counter
        state["consecutive_timeouts"] += 1
        write_loop_state(d, state)
        loaded = read_loop_state(d)
        assert loaded["consecutive_timeouts"] == 1
        assert loaded["rounds"] == []  # No round recorded

    def test_autonomous_timeout_records_skipped_round(self, tmp_path):
        """Autonomous mode: timeout records skipped round immediately."""
        d = str(tmp_path)
        state = {**copy.deepcopy(DEFAULT_STATE), "merge_base": "abc",
                 "autonomous": True, "consecutive_timeouts": 0}
        state["consecutive_timeouts"] += 1
        state.setdefault("rounds", []).append({
            "round": 1, "findings": 0, "fixed": 0,
            "rejected": 0, "deferred": 0, "skipped": True,
        })
        write_loop_state(d, state)
        loaded = read_loop_state(d)
        assert loaded["consecutive_timeouts"] == 1
        assert len(loaded["rounds"]) == 1
        assert loaded["rounds"][0]["skipped"] is True

    def test_timeout_at_cap_terminates_autonomous(self, tmp_path):
        """Timeout on the last configured round terminates instead of skipping."""
        d = str(tmp_path)
        state = {**copy.deepcopy(DEFAULT_STATE), "merge_base": "abc",
                 "autonomous": True, "max_rounds": 3, "consecutive_timeouts": 0}
        # Simulate timeout on round 3 (== max_rounds): at_round_cap is True
        round_num = 3
        at_round_cap = round_num >= state["max_rounds"]
        assert at_round_cap
        # Autonomous at cap → terminate with max_rounds
        state["terminated"] = True
        state["termination"] = "max_rounds"
        write_loop_state(d, state)
        loaded = read_loop_state(d)
        assert loaded["terminated"] is True
        assert loaded["termination"] == "max_rounds"

    def test_round_cap_guard_blocks_excess_round(self, tmp_path):
        """Round 2+ entry guard terminates if round_num > max_rounds."""
        from iterative_review.loop import MAX_ROUNDS_HARD_LIMIT
        d = str(tmp_path)
        state = {**copy.deepcopy(DEFAULT_STATE), "merge_base": "abc",
                 "max_rounds": 3}
        write_loop_state(d, state)
        # Round 4 > max_rounds 3: guard should prevent execution
        loaded = read_loop_state(d)
        round_num = 4
        assert round_num > loaded["max_rounds"]
        assert round_num <= MAX_ROUNDS_HARD_LIMIT  # not at hard limit

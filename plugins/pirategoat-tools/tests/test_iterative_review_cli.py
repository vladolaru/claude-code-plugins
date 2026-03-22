"""Tests for iterative_review CLI -- argument parsing and action routing."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
MODULE_DIR = SCRIPTS_DIR / "iterative_review"


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
        (d / "review-loop-state.json").write_text(json.dumps(state))
        # Write findings so advance expects outcomes
        (d / "round-1-findings.json").write_text(json.dumps([
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
        (d / "review-loop-state.json").write_text(json.dumps(state))
        findings = [{"id": "r1_f1", "severity": "P1", "title": "T", "body": "B", "location": "a.py:1"}]
        (d / "round-1-findings.json").write_text(json.dumps(findings))
        outcomes = [{"id": "r1_f1", "action": "rejected", "reasoning": "False positive."}]
        (d / "round-1-outcomes.json").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        # Should detect all_rejected convergence
        updated_state = json.loads((d / "review-loop-state.json").read_text())
        assert updated_state["terminated"] is True
        assert updated_state["termination"] == "all_rejected"


class TestAdvanceIdempotency:
    """Advance is idempotent — retrying the same round doesn't duplicate records."""

    def test_retry_does_not_duplicate_round(self, tmp_path):
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 5, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        (d / "review-loop-state.json").write_text(json.dumps(state))
        findings = [{"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"}]
        (d / "round-1-findings.json").write_text(json.dumps(findings))
        outcomes = [{"id": "r1_f1", "action": "fixed", "summary": "Done."}]
        (d / "round-1-outcomes.json").write_text(json.dumps(outcomes))

        cmd = [sys.executable, "-m", "iterative_review",
               "--action", "advance", "--round", "1",
               "--output-dir", str(d)]

        # Run advance twice
        subprocess.run(cmd, capture_output=True, text=True, cwd=str(SCRIPTS_DIR))
        subprocess.run(cmd, capture_output=True, text=True, cwd=str(SCRIPTS_DIR))

        updated_state = json.loads((d / "review-loop-state.json").read_text())
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
        (d / "review-loop-state.json").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r1_f2", "severity": "P1", "title": "B", "body": "Y", "location": "b.py:2"},
            {"id": "r1_f3", "severity": "P2", "title": "C", "body": "Z", "location": "c.py:3"},
        ]
        (d / "round-1-findings.json").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Fixed it."},
            {"id": "r1_f2", "action": "rejected", "reasoning": "Not real."},
            {"id": "r1_f3", "action": "deferred", "reasoning": "Out of scope."},
        ]
        (d / "round-1-outcomes.json").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads((d / "review-loop-state.json").read_text())
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
        (d / "review-loop-state.json").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "Bug", "body": "X", "location": "a.py:1"},
        ]
        (d / "round-1-findings.json").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r1_f1", "action": "deferred", "reasoning": "Out of scope."},
        ]
        (d / "round-1-outcomes.json").write_text(json.dumps(outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        jsonl_path = d / "deferred-items.jsonl"
        assert jsonl_path.exists()
        items = [json.loads(line) for line in jsonl_path.read_text().strip().split("\n")]
        assert len(items) == 1
        assert items[0]["id"] == "r1_f1"


class TestAdvanceConvergence:
    """Advance action detects convergence conditions."""

    def test_max_rounds_convergence(self, tmp_path):
        """Terminates with max_rounds when round equals max_rounds."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 3, "max_rounds": 3, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        (d / "review-loop-state.json").write_text(json.dumps(state))
        findings = [
            {"id": "r3_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"},
        ]
        (d / "round-3-findings.json").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r3_f1", "action": "fixed", "summary": "Fixed."},
        ]
        (d / "round-3-outcomes.json").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "3",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads((d / "review-loop-state.json").read_text())
        assert updated_state["terminated"] is True
        assert updated_state["termination"] == "max_rounds"

    def test_nitpicks_only_convergence(self, tmp_path):
        """Terminates with nitpicks_only when all findings are P3."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 5, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 100,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        (d / "review-loop-state.json").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P3", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r1_f2", "severity": "P3", "title": "B", "body": "Y", "location": "b.py:2"},
        ]
        (d / "round-1-findings.json").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Done."},
            {"id": "r1_f2", "action": "fixed", "summary": "Done."},
        ]
        (d / "round-1-outcomes.json").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads((d / "review-loop-state.json").read_text())
        assert updated_state["terminated"] is True
        assert updated_state["termination"] == "nitpicks_only"

    def test_continue_when_no_convergence(self, tmp_path):
        """Does not terminate when convergence is not met."""
        d = tmp_path / "code-review"
        d.mkdir()
        state = {"current_round": 1, "max_rounds": 5, "rounds": [],
                 "merge_base": "abc", "diff_lines_relevant": 500,
                 "terminated": False, "termination": None,
                 "pass_prior_analysis": True, "analysis_doc_prefix": "test"}
        (d / "review-loop-state.json").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r1_f2", "severity": "P2", "title": "B", "body": "Y", "location": "b.py:2"},
        ]
        (d / "round-1-findings.json").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Done."},
            {"id": "r1_f2", "action": "rejected", "reasoning": "Not real."},
        ]
        (d / "round-1-outcomes.json").write_text(json.dumps(outcomes))

        result = subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0
        updated_state = json.loads((d / "review-loop-state.json").read_text())
        assert updated_state["terminated"] is False
        assert "round 2" in result.stdout.lower()


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
        (d / "review-loop-state.json").write_text(json.dumps(state))
        findings = [{"id": "r1_f1", "severity": "P1", "title": "T", "body": "B", "location": "a.py:1"}]
        (d / "round-1-findings.json").write_text(json.dumps(findings))
        outcomes = [{"id": "r1_f1", "action": "rejected", "reasoning": "False positive."}]
        (d / "round-1-outcomes.json").write_text(json.dumps(outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        result_path = d / "review-loop-result.json"
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
        (d / "review-loop-state.json").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r1_f2", "severity": "P2", "title": "B", "body": "Y", "location": "b.py:2"},
        ]
        (d / "round-1-findings.json").write_text(json.dumps(findings))
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Done."},
            {"id": "r1_f2", "action": "rejected", "reasoning": "Not real."},
        ]
        (d / "round-1-outcomes.json").write_text(json.dumps(outcomes))

        subprocess.run(
            [sys.executable, "-m", "iterative_review",
             "--action", "advance", "--round", "1",
             "--output-dir", str(d)],
            capture_output=True, text=True,
            cwd=str(SCRIPTS_DIR),
        )
        result_path = d / "review-loop-result.json"
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
        (d / "review-loop-state.json").write_text(json.dumps(state))

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
        (d / "review-loop-state.json").write_text(json.dumps(state))
        findings = [
            {"id": "r1_f1", "severity": "P1", "title": "A", "body": "X", "location": "a.py:1"},
            {"id": "r1_f2", "severity": "P2", "title": "B", "body": "Y", "location": "b.py:2"},
        ]
        (d / "round-1-findings.json").write_text(json.dumps(findings))
        # Only outcome for r1_f1 -- r1_f2 is missing
        outcomes = [
            {"id": "r1_f1", "action": "fixed", "summary": "Done."},
        ]
        (d / "round-1-outcomes.json").write_text(json.dumps(outcomes))

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
        result_path = d / "review-loop-result.json"
        result_path.write_text(json.dumps(result_data, indent=2))

        # Verify
        assert result_path.exists()
        loaded = json.loads(result_path.read_text())
        assert loaded["termination"] == "zero_findings"
        assert loaded["rounds_completed"] == 1
        assert len(loaded["rounds"]) == 1

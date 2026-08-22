"""Tests for review/critic.py — review-specific decision criticism pipeline."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "review" / "critic.py"


def run_critic(*args):
    """Run review/critic.py and return the result."""
    cmd = [sys.executable, str(SCRIPT)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)


class TestStepCount:
    """The script should have exactly 4 steps."""

    def test_total_steps_is_4(self):
        result = run_critic(
            "--step-number", "1",
            "--total-steps", "4",
            "--report", "/tmp/nonexistent-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "initial",
        )
        assert result.returncode == 0
        assert "Step 1/4" in result.stdout

    def test_total_steps_mismatch_is_invalid(self):
        result = run_critic(
            "--step-number", "1",
            "--total-steps", "7",
            "--report", "/tmp/nonexistent-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "initial",
        )
        assert result.returncode != 0
        assert "must be 4" in result.stderr

    def test_step_5_is_invalid(self):
        result = run_critic(
            "--step-number", "5",
            "--total-steps", "4",
            "--report", "/tmp/nonexistent-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "initial",
        )
        assert result.returncode != 0


class TestStepTitles:
    """Each step should have the expected phase and title."""

    @pytest.mark.parametrize("step,expected_title,expected_phase", [
        (1, "Decompose", "DECOMPOSITION"),
        (2, "Verify", "VERIFICATION"),
        (3, "Challenge", "CHALLENGE"),
        (4, "Synthesize", "SYNTHESIS"),
    ])
    def test_step_metadata(self, step, expected_title, expected_phase):
        result = run_critic(
            "--step-number", str(step),
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "accumulated state",
        )
        assert result.returncode == 0
        assert expected_title in result.stdout
        assert expected_phase in result.stdout


class TestNextStepDirective:
    """Each step except the last must direct to the next step."""

    @pytest.mark.parametrize("step", [1, 2, 3])
    def test_non_final_step_has_next(self, step):
        result = run_critic(
            "--step-number", str(step),
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "state",
        )
        assert f"--step-number {step + 1}" in result.stdout

    def test_final_step_has_no_next(self):
        result = run_critic(
            "--step-number", "4",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "state",
        )
        assert "--step-number 5" not in result.stdout
        assert "COMPLETE" in result.stdout


class TestOutputPathInSynthesis:
    """Step 4 must include the output directory path."""

    def test_output_dir_in_step_4(self):
        result = run_critic(
            "--step-number", "4",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic-output",
            "--thoughts", "state",
        )
        assert "/tmp/test-critic-output" in result.stdout
        assert "decision-critic-findings.md" in result.stdout


class TestCriticContextArg:
    """The --context flag replaces --findings-json for curated Markdown input."""

    def test_context_surfaced_in_step_1(self):
        result = run_critic(
            "--step-number", "1",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--context", "/tmp/test-critic-context.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "initial",
        )
        assert result.returncode == 0
        assert "test-critic-context.md" in result.stdout

    def test_context_surfaced_in_step_2(self):
        """Step 2 should also reference the context path."""
        result = run_critic(
            "--step-number", "2",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--context", "/tmp/test-critic-context.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "state",
        )
        assert result.returncode == 0
        assert "test-critic-context.md" in result.stdout

    def test_step_1_references_finding_ids(self):
        """Step 1 should reference F1, F2 finding IDs from context."""
        result = run_critic(
            "--step-number", "1",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--context", "/tmp/test-critic-context.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "initial",
        )
        assert result.returncode == 0
        # Should mention using pre-assigned IDs
        assert "F1" in result.stdout or "finding IDs" in result.stdout.lower()


class TestCriticSave:
    """--save is the ONLY channel the decision-reviewer agent is allowed to
    write decision-critic-* artifacts through (agents/decision-reviewer.md):
    it validates the whole batch and records verdict + findings +
    adjustments atomically, or writes nothing at all."""

    def _run_save(self, output_dir, *extra_args):
        cmd = [
            sys.executable, str(SCRIPT), "--save",
            "--output-dir", str(output_dir), *extra_args,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    def _write_findings(self, tmp_path, text="# Decision Critic Findings\n"):
        path = tmp_path / "f.md"
        path.write_text(text)
        return path

    def _write_adjustments(self, tmp_path, adjustments):
        path = tmp_path / "a.json"
        path.write_text(json.dumps({"schema": 1, "adjustments": adjustments}))
        return path

    def test_critic_save_writes_all_artifacts_atomically(self, tmp_path):
        findings = self._write_findings(tmp_path)
        adjustments = self._write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "r",
        }])

        result = self._run_save(
            tmp_path, "--verdict", "REVISE",
            "--findings", str(findings), "--adjustments", str(adjustments),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "decision-critic-findings.md").is_file()
        assert (tmp_path / "decision-critic-adjustments.json").is_file()
        verdict_doc = json.loads(
            (tmp_path / "decision-critic-verdict.json").read_text()
        )
        assert verdict_doc == {"verdict": "REVISE"}

    def test_critic_save_rejects_bad_verdict(self, tmp_path):
        findings = self._write_findings(tmp_path)

        result = self._run_save(
            tmp_path, "--verdict", "MAYBE", "--findings", str(findings),
        )

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert [p.name for p in tmp_path.iterdir()] == [findings.name], (
            "a rejected save must write nothing"
        )

    def test_critic_save_rejects_revise_without_adjustments(self, tmp_path):
        findings = self._write_findings(tmp_path)

        result = self._run_save(
            tmp_path, "--verdict", "REVISE", "--findings", str(findings),
        )

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "adjustments" in result.stdout.lower()
        assert [p.name for p in tmp_path.iterdir()] == [findings.name]

    @pytest.mark.parametrize("verdict", ["STAND", "ESCALATE"])
    def test_critic_save_rejects_non_revise_with_adjustments(
        self, tmp_path, verdict
    ):
        """STAND/ESCALATE alongside a non-empty batch is the contradiction
        the apply gate could only quarantine downstream; now rejected at
        source. run_save() treats both verdicts identically — pin both,
        not just one."""
        findings = self._write_findings(tmp_path)
        adjustments = self._write_adjustments(tmp_path, [{
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "r",
        }])

        result = self._run_save(
            tmp_path, "--verdict", verdict,
            "--findings", str(findings), "--adjustments", str(adjustments),
        )

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "contradiction" in result.stdout.lower()
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "a.json", "f.md",
        ], "a rejected save must write nothing"

    def test_critic_save_stand_without_adjustments_succeeds(self, tmp_path):
        """The success-path counterpart to the rejection tests above: a
        STAND with no --adjustments is the ordinary case, not just the one
        that must be refused when adjustments are present."""
        findings = self._write_findings(tmp_path)

        result = self._run_save(
            tmp_path, "--verdict", "STAND", "--findings", str(findings),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "decision-critic-findings.md").is_file()
        assert not (tmp_path / "decision-critic-adjustments.json").exists()
        verdict_doc = json.loads(
            (tmp_path / "decision-critic-verdict.json").read_text()
        )
        assert verdict_doc == {"verdict": "STAND"}
        assert "RECORDED VERDICT: STAND" in result.stdout
        assert "RECORDED ADJUSTMENTS: 0" in result.stdout

    def test_critic_save_rejects_two_simultaneous_problems(self, tmp_path):
        """run_save() must collect every problem, not stop at the first —
        the same "don't stop at problems[0]" contract TestValidateAdjustments
        pins for the validator itself, pinned here at the save-command
        level with a bad verdict AND an invalid batch in the same call."""
        findings = self._write_findings(tmp_path)
        adjustments = self._write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "aaaa1111",
            "fields": {}, "rationale": "r",
        }])

        result = self._run_save(
            tmp_path, "--verdict", "MAYBE",
            "--findings", str(findings), "--adjustments", str(adjustments),
        )

        assert result.returncode != 0
        rejected_lines = [
            line for line in result.stdout.splitlines()
            if line.startswith("REJECTED:")
        ]
        assert len(rejected_lines) == 2, (
            f"expected exactly 2 REJECTED lines, got: {rejected_lines}"
        )
        assert any("verdict must be one of" in line for line in rejected_lines)
        assert any("obliterate" in line for line in rejected_lines)
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "a.json", "f.md",
        ], "a rejected save must write nothing"

    def test_critic_save_rejects_invalid_batch(self, tmp_path):
        findings = self._write_findings(tmp_path)
        adjustments = self._write_adjustments(tmp_path, [{
            "action": "obliterate", "id": "aaaa1111",
            "fields": {}, "rationale": "r",
        }])

        result = self._run_save(
            tmp_path, "--verdict", "REVISE",
            "--findings", str(findings), "--adjustments", str(adjustments),
        )

        assert result.returncode != 0
        assert "REJECTED" in result.stdout
        assert "obliterate" in result.stdout
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "a.json", "f.md",
        ]

    def test_critic_save_echo_names_what_was_recorded(self, tmp_path):
        findings = self._write_findings(tmp_path)
        adjustments = self._write_adjustments(tmp_path, [{
            "adjustment_id": "adj-1",
            "action": "promote", "id": "aaaa1111",
            "fields": {"severity": "high"}, "rationale": "r",
        }])

        result = self._run_save(
            tmp_path, "--verdict", "REVISE",
            "--findings", str(findings), "--adjustments", str(adjustments),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "REVISE" in result.stdout
        assert "adj-1" in result.stdout


class TestReviewSpecificLanguage:
    """Prompts should contain review-specific terms, not generic decision language."""

    def test_step_2_mentions_source_code(self):
        result = run_critic(
            "--step-number", "2",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "state",
        )
        assert "source" in result.stdout.lower() or "code" in result.stdout.lower()
        assert "file" in result.stdout.lower()

    def test_step_3_mentions_severity(self):
        result = run_critic(
            "--step-number", "3",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "state",
        )
        assert "severity" in result.stdout.lower() or "false positive" in result.stdout.lower()

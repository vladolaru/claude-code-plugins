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

    def test_findings_json_still_accepted(self):
        """Backward compat: --findings-json should still be accepted."""
        result = run_critic(
            "--step-number", "1",
            "--total-steps", "4",
            "--report", "/tmp/test-report.md",
            "--findings-json", "/tmp/test-findings.json",
            "--output-dir", "/tmp/test-critic",
            "--thoughts", "initial",
        )
        assert result.returncode == 0
        # The old flag should work (surfaced somehow)
        assert "test-findings.json" in result.stdout

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

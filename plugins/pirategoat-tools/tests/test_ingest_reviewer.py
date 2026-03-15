# test_ingest_reviewer.py
import importlib.util
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "ingest-code-review.py"

# Load module for unit tests
_spec = importlib.util.spec_from_file_location("ingest_code_review", str(SCRIPT_PATH))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestGetPhaseName:
    def test_steps_1_2_are_setup(self):
        assert _mod.get_phase_name(1) == "SETUP"
        assert _mod.get_phase_name(2) == "SETUP"

    def test_step_3_is_scope(self):
        assert _mod.get_phase_name(3) == "SCOPE"

    def test_steps_4_5_are_verification(self):
        assert _mod.get_phase_name(4) == "VERIFICATION"
        assert _mod.get_phase_name(5) == "VERIFICATION"

    def test_step_6_is_synthesis(self):
        assert _mod.get_phase_name(6) == "SYNTHESIS"


class TestGetStepGuidance:
    def test_step_1_title(self):
        g = _mod.get_step_guidance(1, 6, "/tmp/review", "")
        assert g["step_title"] == "Locate & Initialize"

    def test_step_2_title(self):
        g = _mod.get_step_guidance(2, 6, None, "prior state")
        assert g["step_title"] == "Parse Findings & Assign IDs"

    def test_step_3_title(self):
        g = _mod.get_step_guidance(3, 6, None, "prior state")
        assert g["step_title"] == "Classify Scope"

    def test_step_4_title(self):
        g = _mod.get_step_guidance(4, 6, None, "prior state")
        assert g["step_title"] == "Generate Verification Questions"

    def test_step_5_title(self):
        g = _mod.get_step_guidance(5, 6, None, "prior state")
        assert g["step_title"] == "Factored Verification"

    def test_step_6_title(self):
        g = _mod.get_step_guidance(6, 6, None, "prior state")
        assert g["step_title"] == "Categorize & Plan"

    def test_step_6_has_no_next(self):
        g = _mod.get_step_guidance(6, 6, None, "")
        assert g["next"] is None

    def test_steps_1_to_5_have_next(self):
        for step in range(1, 6):
            g = _mod.get_step_guidance(step, 6, None, "")
            assert g["next"] is not None, f"Step {step} missing 'next'"

    def test_step_5_has_academic_note(self):
        """Factored verification step cites Chain-of-Verification."""
        g = _mod.get_step_guidance(5, 6, None, "")
        assert g.get("academic_note") is not None
        assert "Chain-of-Verification" in g["academic_note"]

    def test_steps_2_to_6_have_state_requirement(self):
        """Steps 2-6 must include state_requirement text."""
        for step in range(2, 7):
            g = _mod.get_step_guidance(step, 6, None, "prior state")
            actions_text = "\n".join(g["actions"])
            assert "CONTEXT REQUIREMENT" in actions_text, (
                f"Step {step} missing CONTEXT REQUIREMENT (state_requirement)"
            )

    def test_step_1_no_state_requirement(self):
        """Step 1 has no prior state to preserve."""
        g = _mod.get_step_guidance(1, 6, "/tmp/review", "")
        actions_text = "\n".join(g["actions"])
        assert "CONTEXT REQUIREMENT" not in actions_text

    def test_step_3_mentions_changed_files(self):
        """Scope step must reference CHANGED_FILES."""
        g = _mod.get_step_guidance(3, 6, None, "CHANGED_FILES=[src/foo.php]")
        actions_text = "\n".join(g["actions"])
        assert "CHANGED_FILES" in actions_text

    def test_step_5_mentions_epistemic_boundary(self):
        """Factored verification must include the epistemic boundary rule."""
        g = _mod.get_step_guidance(5, 6, None, "F1 verified questions")
        actions_text = "\n".join(g["actions"])
        assert "EPISTEMIC BOUNDARY" in actions_text

    def test_step_5_mentions_read_tool(self):
        """Factored verification must tell Claude to use the Read tool."""
        g = _mod.get_step_guidance(5, 6, None, "F1 verified questions")
        actions_text = "\n".join(g["actions"])
        assert "Read tool" in actions_text

    def test_step_6_mentions_all_categories(self):
        """Synthesis step must mention all 5 finding categories."""
        g = _mod.get_step_guidance(6, 6, None, "findings")
        actions_text = "\n".join(g["actions"])
        for cat in ["CONFIRMED", "LIKELY VALID", "FALSE POSITIVE", "OUT OF SCOPE", "STYLE"]:
            assert cat in actions_text, f"Step 6 missing category: {cat}"


class TestFormatOutput:
    def test_header_format(self):
        g = _mod.get_step_guidance(1, 6, "/tmp/r", "")
        output = _mod.format_output(1, 6, g)
        assert "═══ INGEST CODE REVIEW Step 1/" in output
        assert "SETUP" in output

    def test_academic_note_included_when_present(self):
        g = _mod.get_step_guidance(5, 6, None, "")
        output = _mod.format_output(5, 6, g)
        assert "Chain-of-Verification" in output

    def test_next_step_shown(self):
        g = _mod.get_step_guidance(1, 6, "/tmp/r", "")
        output = _mod.format_output(1, 6, g)
        assert "NEXT (MANDATORY):" in output

    def test_workflow_complete_on_step_6(self):
        g = _mod.get_step_guidance(6, 6, None, "")
        output = _mod.format_output(6, 6, g)
        assert "PIPELINE COMPLETE" in output


class TestCLIIntegration:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            capture_output=True, text=True
        )

    def test_step_1_exits_0(self):
        result = self._run(
            "--step-number", "1",
            "--total-steps", "6",
            "--output-dir", "/tmp/test-review",
            "--thoughts", "",
        )
        assert result.returncode == 0

    def test_step_1_requires_output_dir(self):
        result = self._run(
            "--step-number", "1",
            "--total-steps", "6",
            "--thoughts", "",
        )
        assert result.returncode == 1
        assert "output-dir" in result.stderr.lower()

    def test_invalid_step_exits_1(self):
        result = self._run(
            "--step-number", "8",
            "--total-steps", "6",
            "--thoughts", "some state",
        )
        assert result.returncode == 1

    def test_step_2_no_output_dir_needed(self):
        result = self._run(
            "--step-number", "2",
            "--total-steps", "6",
            "--thoughts", "OUTPUT_DIR=/tmp/r GIT_RANGE=main..HEAD CHANGED_FILES=[foo.php]",
        )
        assert result.returncode == 0

    def test_thoughts_required(self):
        result = self._run(
            "--step-number", "1",
            "--total-steps", "6",
            "--output-dir", "/tmp/r",
        )
        assert result.returncode != 0

    def test_all_steps_produce_phase_header(self):
        for step in range(1, 7):
            args = ["--step-number", str(step), "--total-steps", "6", "--thoughts", "state"]
            if step == 1:
                args += ["--output-dir", "/tmp/r"]
            result = self._run(*args)
            assert result.returncode == 0, f"Step {step} failed: {result.stderr}"
            assert "═══" in result.stdout, f"Step {step} missing formatted header"

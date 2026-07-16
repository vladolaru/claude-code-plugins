"""
Tests for the eval_agent_compliance CLI — the documented offline
compliance-grading entry point.

test_graders.py covers the grading library; these cover the runner that
wraps it. The runner is a subprocess CLI, so it is exercised as one —
its module-level imports and argument wiring are exactly what unit tests
of the library can't reach (see TESTING.md §"subprocess only for
orchestration").
"""

import json
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent  # grading/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
EVAL_SCRIPT = TESTS_DIR / "grading" / "eval_agent_compliance.py"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
from review.agent.output import ReviewOutputBuilder


def _write_review_pair(output_dir: Path, reviewer: str = "security") -> None:
    """Produce a real review output pair with the production builder."""
    builder = ReviewOutputBuilder(pr_id="1", reviewer=reviewer)
    builder.add_issue(
        severity="high",
        title="Unescaped output",
        file="src/render.php",
        description="Value is echoed without escaping",
        recommendation="Wrap in esc_html()",
        category="xss",
        line=42,
    )
    builder.set_files_reviewed(1)
    builder.save(str(output_dir))


def _run_eval(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke the runner from an isolated cwd.

    Per TESTING.md, subprocess tests run outside the real repo. The runner
    resolves everything from __file__, so an unrelated cwd also proves it
    does not depend on the caller's location.
    """
    return subprocess.run(
        [sys.executable, str(EVAL_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


class TestGradeOnlyMode:
    """--grade-only is the entry point AGENTS.md documents for grading
    saved review output without model calls."""

    def test_grades_a_passing_review_pair(self, tmp_path):
        _write_review_pair(tmp_path)

        result = _run_eval("--grade-only", str(tmp_path), cwd=tmp_path)

        assert "Traceback" not in result.stderr, result.stderr
        assert result.returncode == 0, result.stderr
        assert "security" in result.stdout

    def test_reports_a_failing_review_pair(self, tmp_path):
        """A malformed JSON review is reported, not crashed on."""
        _write_review_pair(tmp_path)
        bad = tmp_path / "security-review.json"
        data = json.loads(bad.read_text())
        del data["verdict"]  # required top-level field
        bad.write_text(json.dumps(data))

        result = _run_eval("--grade-only", str(tmp_path), cwd=tmp_path)

        assert "Traceback" not in result.stderr, result.stderr
        assert "security" in result.stdout

    def test_missing_directory_is_reported_not_crashed(self, tmp_path):
        result = _run_eval("--grade-only", str(tmp_path / "does-not-exist"), cwd=tmp_path)

        assert "Traceback" not in result.stderr, result.stderr
        assert "ERROR" in result.stdout

    def test_no_args_prints_help(self, tmp_path):
        result = _run_eval(cwd=tmp_path)

        assert "Traceback" not in result.stderr, result.stderr
        assert "--grade-only" in result.stdout

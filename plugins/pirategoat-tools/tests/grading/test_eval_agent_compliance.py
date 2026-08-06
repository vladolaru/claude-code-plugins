"""
Tests for the eval_agent_compliance CLI — the documented offline
compliance-grading entry point.

test_graders.py covers the grading library; these cover the runner that
wraps it. The runner is a subprocess CLI, so it is exercised as one —
its module-level imports and argument wiring are exactly what unit tests
of the library can't reach (see TESTING.md §"subprocess only for
orchestration").
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent  # grading/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
EVAL_SCRIPT = TESTS_DIR / "grading" / "eval_agent_compliance.py"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
from review.agent.output import ReviewOutputBuilder

# The runner is mostly exercised as a subprocess (see _run_eval), but
# run_grade_only is also called directly to inspect its GradeResults.
# Load it the same way the runner itself loads bootstrap: by exact path.
_eval_spec = importlib.util.spec_from_file_location(
    "_eval_agent_compliance_under_test", str(EVAL_SCRIPT),
)
_eval_mod = importlib.util.module_from_spec(_eval_spec)
_eval_spec.loader.exec_module(_eval_mod)
run_grade_only = _eval_mod.run_grade_only


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

    def test_grade_only_materializes_missing_markdown(self, tmp_path):
        """Output dirs from fresh runs hold only the JSON — save() publishes the
        canonical JSON and Markdown is derived. Grading renders the missing
        Markdown from the JSON before grading the pair instead of failing the
        md grader on absence."""
        _write_review_pair(tmp_path)
        assert not (tmp_path / "security-review.md").is_file()  # save() = JSON only

        results = run_grade_only(str(tmp_path))
        assert "security" in results
        # md grade passed: the pair result flattens json + md checks, so no
        # failure may mention the md file — and the rendered md must itself
        # pass the markdown grader (same grader grade_output_pair delegates to).
        result = results["security"]
        assert result.passed, result.failures
        assert not any("security-review.md" in failure for failure in result.failures)
        md_grade = _eval_mod.grade_review_markdown(str(tmp_path / "security-review.md"))
        assert md_grade.passed, md_grade.failures
        assert (tmp_path / "security-review.md").is_file()

    def test_missing_directory_is_reported_not_crashed(self, tmp_path):
        result = _run_eval("--grade-only", str(tmp_path / "does-not-exist"), cwd=tmp_path)

        assert "Traceback" not in result.stderr, result.stderr
        assert "ERROR" in result.stdout

    def test_no_args_prints_help(self, tmp_path):
        result = _run_eval(cwd=tmp_path)

        assert "Traceback" not in result.stderr, result.stderr
        assert "--grade-only" in result.stdout


class TestDispatchIdentity:
    """The benchmark must dispatch the configured reviewer, not generic Claude.

    These pin the measurement instrument itself: the agent .md body must
    reach the dispatch prompt and its frontmatter model routing must be
    honored — a keyed run that silently grades a bare-bootstrap session
    measures the wrong thing (found live 2026-08-06, after every layer
    above this one had been reviewed diff-by-diff).
    """

    AGENT_MD = (
        "---\n"
        "name: security-reviewer\n"
        "description: reviews security\n"
        "model: sonnet\n"
        "---\n"
        "\n"
        "Trace attack paths before reporting.\n"
    )

    def test_frontmatter_splits_into_body_and_model(self):
        body, model = _eval_mod.split_agent_definition(self.AGENT_MD)
        assert model == "sonnet"
        assert "Trace attack paths" in body
        assert "description:" not in body

    def test_definition_without_frontmatter_passes_through(self):
        body, model = _eval_mod.split_agent_definition("Just instructions.")
        assert body == "Just instructions."
        assert model is None

    def test_prompt_embeds_definition_before_bootstrap(self):
        prompt = _eval_mod.build_dispatch_prompt(
            "security-reviewer", "BOOTSTRAP-CONTENT", "Trace attack paths.",
        )
        assert "Trace attack paths." in prompt
        assert prompt.index("Trace attack paths.") < prompt.index("BOOTSTRAP-CONTENT")

    def test_empty_definition_omits_definition_section(self):
        prompt = _eval_mod.build_dispatch_prompt("x-reviewer", "BOOT", "")
        assert "BOOT" in prompt
        assert "agent definition" not in prompt

    def test_every_eval_agent_definition_resolves(self):
        # A benchmark agent whose .md goes missing or empty would silently
        # regress the dispatch back into grading generic Claude.
        for agent in _eval_mod.ALL_AGENTS:
            path = _eval_mod.PLUGIN_ROOT / "agents" / f"{agent}.md"
            assert path.is_file(), f"{agent}: no agent definition at {path}"
            body, model = _eval_mod.split_agent_definition(path.read_text())
            assert body.strip(), f"{agent}: definition body is empty"
            assert model is None or model == "inherit" or (
                model in _eval_mod._DISPATCHABLE_MODELS
            ), f"{agent}: frontmatter model {model!r} is not dispatchable"

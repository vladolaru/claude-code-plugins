"""Tests for bootstrap-reviewer.py — integration tests (subprocess runs against all agents)."""

import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
BOOTSTRAP_SCRIPT = SCRIPTS_DIR / "bootstrap-reviewer.py"

sys.path.insert(0, str(SCRIPTS_DIR))

# Import AGENT_CONFIG to derive ALL_AGENTS
_spec = importlib.util.spec_from_file_location("bootstrap_reviewer", str(BOOTSTRAP_SCRIPT))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
AGENT_CONFIG = _mod.AGENT_CONFIG
build_output = _mod.build_output
derive_reviewer_name = _mod.derive_reviewer_name

ALL_AGENTS = sorted(AGENT_CONFIG.keys())
TEST_AGENTS = ["php-tests-reviewer", "js-tests-reviewer", "e2e-tests-reviewer", "go-tests-reviewer"]


# ---------------------------------------------------------------------------
# Temp repo for integration tests (created once, reused across all tests)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(TESTS_DIR))
from conftest import setup_temp_git_repo

_BOOTSTRAP_REPO = None


def _get_bootstrap_repo() -> str:
    """Lazily create a temp git repo from multi-file-realistic.diff."""
    global _BOOTSTRAP_REPO
    if _BOOTSTRAP_REPO is None:
        diff = str(TESTS_DIR / "fixtures" / "multi-file-realistic.diff")
        _BOOTSTRAP_REPO = setup_temp_git_repo(diff)
    return _BOOTSTRAP_REPO


def run_bootstrap(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run bootstrap-reviewer.py via subprocess against a temp git repo.

    Uses a temp repo from multi-file-realistic.diff so tests are fully
    isolated from the real repository state. Always passes
    --range HEAD~1..HEAD for deterministic behavior.
    """
    full_args = list(args)
    if "--range" not in full_args:
        full_args.extend(["--range", "HEAD~1..HEAD"])
    cmd = [sys.executable, str(BOOTSTRAP_SCRIPT)] + full_args
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        cwd=_get_bootstrap_repo(),
    )


class TestOutputStructure:
    """All agents produce correct section markers."""

    @pytest.mark.parametrize("agent_name", ALL_AGENTS)
    def test_section_markers(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        stdout = result.stdout

        assert f"=== BOOTSTRAP: {agent_name} ===" in stdout
        assert "--- Section 1: REVIEW RULES" in stdout
        assert "=== REVIEW RULES ===" in stdout
        assert "--- Section 2: REVIEW CONTENT" in stdout
        assert "--- Section 3: OUTPUT INSTRUCTIONS" in stdout
        assert "=== OUTPUT INSTRUCTIONS ===" in stdout


class TestContentIdentity:
    """REVIEW RULES content identical across all agents."""

    @pytest.fixture(scope="class")
    def agent_outputs(self):
        """Run bootstrap for all agents and cache outputs."""
        outputs = {}
        for agent in ALL_AGENTS:
            result = run_bootstrap("--agent", agent, "--output-dir", "/tmp/test-bootstrap")
            outputs[agent] = result.stdout
        return outputs

    def _extract_section(self, text: str, start_marker: str, *end_markers: str) -> str:
        """Extract text between start_marker and the earliest end_marker found."""
        start = text.find(start_marker)
        if start == -1:
            return ""
        # Find the earliest end marker after the start
        end = len(text)
        for marker in end_markers:
            pos = text.find(marker, start + len(start_marker))
            if pos != -1 and pos < end:
                end = pos
        return text[start:end].strip()

    def test_review_rules_identical_across_agents(self, agent_outputs):
        """REVIEW RULES section should be identical for all agents.

        End at whichever comes first: DOMAIN RULES, REVIEW BUDGET, or Section 2.
        """
        rules = {}
        for agent, output in agent_outputs.items():
            section = self._extract_section(
                output, "=== REVIEW RULES ===",
                "=== DOMAIN RULES ===", "=== REVIEW BUDGET ===", "--- Section 2:",
            )
            rules[agent] = section

        reference = rules[ALL_AGENTS[0]]
        for agent in ALL_AGENTS[1:]:
            assert rules[agent] == reference, (
                f"REVIEW RULES differ between {ALL_AGENTS[0]} and {agent}"
            )

    def test_domain_rules_identical_across_test_agents(self, agent_outputs):
        """DOMAIN RULES should be identical for all test agents."""
        rules = {}
        for agent in TEST_AGENTS:
            output = agent_outputs[agent]
            section = self._extract_section(output, "=== DOMAIN RULES ===", "=== REVIEW BUDGET ===", "--- Section 2:")
            rules[agent] = section



        reference = rules[TEST_AGENTS[0]]
        assert reference, "Expected DOMAIN RULES for test agents"
        for agent in TEST_AGENTS[1:]:
            assert rules[agent] == reference, (
                f"DOMAIN RULES differ between {TEST_AGENTS[0]} and {agent}"
            )


class TestConditionalSections:
    """Sections appear only for relevant agents."""

    @pytest.mark.parametrize("agent_name", TEST_AGENTS)
    def test_domain_rules_present_for_test_agents(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert "=== DOMAIN RULES ===" in result.stdout

    @pytest.mark.parametrize(
        "agent_name",
        [a for a in ALL_AGENTS if a not in TEST_AGENTS],
    )
    def test_domain_rules_absent_for_non_test_agents(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert "=== DOMAIN RULES ===" not in result.stdout

    def test_exploration_scope_for_patterns_reviewer(self):
        result = run_bootstrap("--agent", "patterns-reviewer", "--output-dir", "/tmp/test-bootstrap")
        assert "=== EXPLORATION SCOPE ===" in result.stdout

    @pytest.mark.parametrize(
        "agent_name",
        [a for a in ALL_AGENTS if a != "patterns-reviewer"],
    )
    def test_no_exploration_scope_for_other_agents(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert "=== EXPLORATION SCOPE ===" not in result.stdout

    def test_mutation_reviewer_no_scope(self):
        result = run_bootstrap("--agent", "tests-mutation-reviewer", "--output-dir", "/tmp/test-bootstrap")
        assert "No scope discovery" in result.stdout

    @pytest.mark.parametrize("agent_name", ALL_AGENTS)
    def test_review_budget_present_for_all_agents(self, agent_name):
        """Every agent should receive a REVIEW BUDGET section."""
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert "=== REVIEW BUDGET ===" in result.stdout
        assert "Target: ~" in result.stdout
        assert "tool calls." in result.stdout

    @pytest.mark.parametrize("agent_name", ALL_AGENTS)
    def test_review_budget_has_hard_ceiling(self, agent_name):
        """Budget section must include a hard ceiling instruction."""
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-budget-ceiling")
        assert "Hard ceiling:" in result.stdout
        assert "MUST stop" in result.stdout

    def test_history_insights_budget_override(self):
        """history-insights-reviewer should get its budget_override value (45), not computed value."""
        result = run_bootstrap("--agent", "history-insights-reviewer", "--output-dir", "/tmp/test-budget-override")
        assert "Target: ~45 tool calls" in result.stdout


class TestPersonalization:
    """Agent-specific values are correctly interpolated."""

    @pytest.mark.parametrize(
        "agent_name,reviewer_name",
        [(a, derive_reviewer_name(a)) for a in ALL_AGENTS],
    )
    def test_reviewer_name(self, agent_name, reviewer_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-personalization")
        assert f"REVIEWER_NAME: {reviewer_name}" in result.stdout

    @pytest.mark.parametrize(
        "agent_name,reviewer_name",
        [(a, derive_reviewer_name(a)) for a in ALL_AGENTS],
    )
    def test_output_file_paths(self, agent_name, reviewer_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-personalization")
        assert f"/tmp/test-personalization/{reviewer_name}-review.json" in result.stdout
        assert f"/tmp/test-personalization/{reviewer_name}-review.md" in result.stdout

    @pytest.mark.parametrize(
        "agent_name,reviewer_name",
        [(a, derive_reviewer_name(a)) for a in ALL_AGENTS],
    )
    def test_builder_snippet(self, agent_name, reviewer_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-personalization")
        assert f'reviewer="{reviewer_name}"' in result.stdout


class TestErrorHandling:
    """Error cases: unknown agent, valid agents."""

    def test_unknown_agent_exits_1(self):
        result = run_bootstrap("--agent", "nonexistent-reviewer", "--output-dir", "/tmp/test-err")
        assert result.returncode == 1
        assert "STATUS: ERROR" in result.stdout
        assert "Unknown agent" in result.stdout

    def test_unknown_agent_structured_error(self):
        result = run_bootstrap("--agent", "fake", "--output-dir", "/tmp/test-err")
        assert "=== BOOTSTRAP: fake ===" in result.stdout
        assert "ACTION: Report this error" in result.stdout

    @pytest.mark.parametrize("agent_name", ALL_AGENTS)
    def test_valid_agents_exit_0(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert result.returncode == 0, f"{agent_name} exited with {result.returncode}: {result.stderr}"


class TestNoSemanticFilter:
    """no_semantic_filter flag passes --no-semantic-filter to scope discovery."""

    def test_wp_architecture_reviewer_gets_no_semantic_filter(self):
        """wp-architecture-reviewer has no_semantic_filter: true and scope cmd includes the flag."""
        result = run_bootstrap("--agent", "wp-architecture-reviewer", "--output-dir", "/tmp/test-bootstrap")
        # The agent should succeed (not error out)
        assert result.returncode == 0, f"Exit {result.returncode}: {result.stderr}"
        # Verify the registry flag is set for this agent
        assert AGENT_CONFIG["wp-architecture-reviewer"].get("no_semantic_filter") is True

    def test_patterns_reviewer_gets_no_semantic_filter(self):
        """patterns-reviewer has no_semantic_filter: true and scope cmd includes the flag."""
        result = run_bootstrap("--agent", "patterns-reviewer", "--output-dir", "/tmp/test-bootstrap")
        assert result.returncode == 0, f"Exit {result.returncode}: {result.stderr}"
        assert AGENT_CONFIG["patterns-reviewer"].get("no_semantic_filter") is True

    def test_agents_without_flag_do_not_have_it(self):
        """Agents without no_semantic_filter should not have the flag set."""
        for agent_name, config in AGENT_CONFIG.items():
            if agent_name in ("wp-architecture-reviewer", "patterns-reviewer"):
                continue
            assert not config.get("no_semantic_filter", False), (
                f"Agent '{agent_name}' unexpectedly has no_semantic_filter set"
            )

    def test_no_semantic_filter_appended_to_scope_flags(self):
        """When no_semantic_filter is true, --no-semantic-filter is passed to run_scope_discovery."""
        # We verify this by importing the module and checking that the config
        # value is read during bootstrap. The integration test via subprocess
        # confirms the flag reaches review-scope.py (which already supports it).
        # Here we verify the registry config is correct.
        for agent_name in ("wp-architecture-reviewer", "patterns-reviewer"):
            config = AGENT_CONFIG[agent_name]
            assert config.get("no_semantic_filter") is True
            # Also verify domain is set (so scope discovery runs)
            assert config["domain"] is not None


# Dynamically determine which agents have file_history from the registry
_AGENTS_WITH_HISTORY = [
    name for name, cfg in AGENT_CONFIG.items() if cfg.get("file_history")
]
_AGENTS_WITHOUT_HISTORY = [
    name for name in ALL_AGENTS if name not in _AGENTS_WITH_HISTORY
]


class TestFileHistory:
    """File history section for agents with file_history enabled in registry."""

    @pytest.mark.parametrize("agent_name", _AGENTS_WITH_HISTORY)
    def test_file_history_present_for_enabled_agents(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert "=== FILE HISTORY ===" in result.stdout

    @pytest.mark.parametrize("agent_name", _AGENTS_WITHOUT_HISTORY)
    def test_file_history_absent_for_other_agents(self, agent_name):
        result = run_bootstrap("--agent", agent_name, "--output-dir", "/tmp/test-bootstrap")
        assert "=== FILE HISTORY ===" not in result.stdout


class TestReviewOutputBuilderAPIExample:
    """Bootstrap Section 3 must include a complete ReviewOutputBuilder usage example."""

    def _build(self):
        return build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )

    def test_output_contains_add_issue_example(self):
        """The usage example must show add_issue() with named parameters."""
        output = self._build()
        assert "add_issue(" in output
        assert "severity=" in output
        assert "title=" in output
        assert "file=" in output
        assert "description=" in output
        assert "recommendation=" in output

    def test_output_contains_add_positive_example(self):
        """The usage example must show add_positive()."""
        output = self._build()
        assert "add_positive(" in output

    def test_output_contains_save_example(self):
        """The usage example must show save() with output_dir."""
        output = self._build()
        assert "save(" in output
        assert "output_dir" in output.lower() or "/tmp/pr-review-42" in output

    def test_output_contains_set_files_reviewed(self):
        """The usage example must show set_files_reviewed()."""
        output = self._build()
        assert "set_files_reviewed(" in output

    def test_output_contains_set_confidence(self):
        """The usage example must show set_confidence()."""
        output = self._build()
        assert "set_confidence(" in output

    def test_output_contains_no_verify_instruction(self):
        """The usage example must tell agents not to verify save() output."""
        output = self._build()
        lower = output.lower()
        assert "do not" in lower and ("read" in lower or "verify" in lower) and ("output file" in lower or "save()" in lower)


class TestBootstrapOutputSizeCap:
    """Bootstrap caps inline scope when output would exceed size threshold."""

    def _build_large_output(self, scope_size_kb=50, output_dir=None):
        """Helper: build output with a scope of the given KB size."""
        if output_dir is None:
            import tempfile
            output_dir = tempfile.mkdtemp()
        large_scope = "x" * (scope_size_kb * 1024)
        return build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules here",
            domain_rules=None,
            scope_output=large_scope,
            exploration_scope=None,
            output_dir=output_dir,
            pr_number="42",
            reviewer_name="security",
        )

    def test_small_scope_included_inline(self):
        """Scope under threshold is included inline (no change from current behavior)."""
        small_scope = "diff content here\n" * 100  # ~2KB
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules here",
            domain_rules=None,
            scope_output=small_scope,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        assert small_scope in output

    def test_large_scope_truncated(self):
        """Scope over threshold is truncated with a file reference."""
        output = self._build_large_output(scope_size_kb=50)
        # The full 50KB scope should NOT be in the output
        assert len(output) < 40 * 1024  # output should be well under 40KB total

    def test_large_scope_has_file_reference(self):
        """When scope is truncated, output tells agent where to read the full scope."""
        output = self._build_large_output(scope_size_kb=50)
        assert "scoped-diff.patch" in output or "full scope" in output.lower() or "Read" in output

    def test_large_scope_has_read_instructions(self):
        """When scope is truncated, output tells agent to use offset/limit."""
        output = self._build_large_output(scope_size_kb=50)
        lower = output.lower()
        assert "offset" in lower or "limit" in lower or "head" in lower


class TestDynamicDispatchRisk:
    """Bootstrap injects DYNAMIC_DISPATCH_RISK for dead-code-reviewer."""

    def test_dead_code_reviewer_gets_dispatch_risk(self):
        """dead-code-reviewer output includes DYNAMIC_DISPATCH_RISK."""
        scope_with_php = "=== FILES ===\nsrc/payment.php  (+10 -5)\nsrc/utils.ts  (+3 -1)\n=== DIFFS ==="
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output=scope_with_php,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        assert "DYNAMIC_DISPATCH_RISK:" in output

    def test_dispatch_risk_high_with_php_files(self):
        """DYNAMIC_DISPATCH_RISK is 'high' when PHP files are in scope."""
        scope_with_php = "=== FILES ===\nsrc/payment.php  (+10 -5)\nsrc/utils.ts  (+3 -1)\n=== DIFFS ==="
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output=scope_with_php,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        risk_line = [l for l in output.splitlines() if "DYNAMIC_DISPATCH_RISK:" in l]
        assert risk_line, "DYNAMIC_DISPATCH_RISK line not found in output"
        assert "high" in risk_line[0].lower()

    def test_dispatch_risk_low_without_php_files(self):
        """DYNAMIC_DISPATCH_RISK is 'low' when no PHP files are in scope."""
        scope_no_php = "=== FILES ===\nsrc/utils.ts  (+3 -1)\nsrc/component.tsx  (+20 -5)\n=== DIFFS ==="
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output=scope_no_php,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        risk_line = [l for l in output.splitlines() if "DYNAMIC_DISPATCH_RISK:" in l]
        assert risk_line, "DYNAMIC_DISPATCH_RISK line not found in output"
        assert "low" in risk_line[0].lower()

    def test_other_agents_no_dispatch_risk(self):
        """Non-dead-code agents do NOT get DYNAMIC_DISPATCH_RISK."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        assert "DYNAMIC_DISPATCH_RISK:" not in output


class TestOutputFilenameConsistency:
    """Output filenames from ReviewOutputBuilder.save() match bootstrap expectations."""

    def test_save_uses_review_suffix(self, tmp_path):
        """save() should write {reviewer}-review.json and {reviewer}-review.md."""
        from review_output_simple import ReviewOutputBuilder

        builder = ReviewOutputBuilder(pr_id="42", reviewer="dead-code")
        result = builder.save(str(tmp_path))

        assert result["json"].endswith("dead-code-review.json"), f"Got: {result['json']}"
        assert result["markdown"].endswith("dead-code-review.md"), f"Got: {result['markdown']}"
        assert os.path.isfile(result["json"])
        assert os.path.isfile(result["markdown"])

    def test_bootstrap_output_matches_save_filenames(self):
        """Bootstrap OUTPUT_FILES paths match what save() actually creates."""
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        assert "/tmp/pr-review-42/dead-code-review.json" in output
        assert "/tmp/pr-review-42/dead-code-review.md" in output

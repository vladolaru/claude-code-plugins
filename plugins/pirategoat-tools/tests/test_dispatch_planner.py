"""
Tests for plan-review-dispatch.py — deterministic, no model calls.

Tests the dispatch planner by importing functions directly and by
validating output schema. Mocks subprocess calls to avoid git dependency.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

# Import the planner module using importlib (hyphenated filename)
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "plan_review_dispatch", str(SCRIPTS_DIR / "plan-review-dispatch.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Import functions under test
load_registry = _mod.load_registry
parse_changed_files_list = _mod.parse_changed_files_list
render_agent_signals_text = _mod.render_agent_signals_text
count_files_in_domain = _mod.count_files_in_domain
build_domain_counts = _mod.build_domain_counts
decide_agent_dispatch = _mod.decide_agent_dispatch
build_dispatch_plan = _mod.build_dispatch_plan
DOMAIN_CATALOG = _mod.DOMAIN_CATALOG


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def registry():
    """Load the real agent registry."""
    return load_registry()


@pytest.fixture(scope="module")
def agents(registry):
    """Return agents dict from registry."""
    return registry["agents"]


# Sample file lists for testing
SAMPLE_PHP_FILES = [
    "src/Controller.php",
    "src/Service.php",
    "tests/ControllerTest.php",
]

SAMPLE_JS_FILES = [
    "src/components/Modal.tsx",
    "src/hooks/useData.ts",
    "src/styles/modal.scss",
]

SAMPLE_MIXED_FILES = [
    "src/Controller.php",
    "src/components/Modal.tsx",
    "src/hooks/useData.ts",
    "tests/ControllerTest.php",
    "src/styles/modal.scss",
    "e2e/checkout.spec.ts",
    ".github/workflows/ci.yml",
    "Dockerfile",
    "src/utils/auth.go",
    "src/utils/auth_test.go",
]

SAMPLE_CONFIG_ONLY_FILES = [
    ".github/workflows/ci.yml",
    "Dockerfile",
    "terraform/main.tf",
]

SAMPLE_NOISE_ONLY_FILES = [
    "package-lock.json",
    "assets/logo.png",
    "vendor/autoload.php",
]


# =============================================================================
# Unit Tests — parse_changed_files_list
# =============================================================================

class TestParseChangedFilesList:
    """Parsing comma-separated file lists."""

    def test_basic_list(self):
        result = parse_changed_files_list("a.py,b.ts,c.php")
        assert result == ["a.py", "b.ts", "c.php"]

    def test_with_spaces(self):
        result = parse_changed_files_list("a.py, b.ts , c.php")
        assert result == ["a.py", "b.ts", "c.php"]

    def test_empty_string(self):
        result = parse_changed_files_list("")
        assert result == []

    def test_none_input(self):
        result = parse_changed_files_list(None)
        assert result == []

    def test_single_file(self):
        result = parse_changed_files_list("a.py")
        assert result == ["a.py"]

    def test_trailing_comma(self):
        result = parse_changed_files_list("a.py,b.ts,")
        assert result == ["a.py", "b.ts"]


class TestRenderAgentSignalsText:
    """Canonical text rendering for downstream shell/prompt usage."""

    def test_joins_signals_with_newlines(self):
        signals = [
            "pr-reviewer: STATUS=DISPATCH",
            "a11y-reviewer: STATUS=SKIPPED_TRIAGE (no UI changes)",
        ]
        assert render_agent_signals_text(signals) == (
            "pr-reviewer: STATUS=DISPATCH\n"
            "a11y-reviewer: STATUS=SKIPPED_TRIAGE (no UI changes)"
        )


# =============================================================================
# Unit Tests — count_files_in_domain
# =============================================================================

class TestCountFilesInDomain:
    """Domain file counting using DOMAIN_CATALOG patterns."""

    def test_code_domain_matches_php(self):
        count = count_files_in_domain(SAMPLE_PHP_FILES, "code")
        assert count > 0

    def test_code_domain_matches_tsx(self):
        count = count_files_in_domain(SAMPLE_JS_FILES, "code")
        assert count > 0

    def test_a11y_domain_matches_tsx_and_scss(self):
        count = count_files_in_domain(SAMPLE_JS_FILES, "a11y")
        assert count > 0

    def test_php_tests_domain_matches_test_files(self):
        count = count_files_in_domain(SAMPLE_PHP_FILES, "php-tests")
        assert count > 0

    def test_go_tests_domain_matches_test_go(self):
        files = ["src/utils/auth_test.go"]
        count = count_files_in_domain(files, "go-tests")
        assert count == 1

    def test_e2e_tests_domain_matches_e2e_dir(self):
        files = ["e2e/checkout.spec.ts"]
        count = count_files_in_domain(files, "e2e-tests")
        assert count == 1

    def test_config_ops_matches_ci(self):
        count = count_files_in_domain(SAMPLE_CONFIG_ONLY_FILES, "config-ops")
        assert count > 0

    def test_unknown_domain_returns_zero(self):
        count = count_files_in_domain(SAMPLE_PHP_FILES, "nonexistent-domain")
        assert count == 0

    def test_empty_files_returns_zero(self):
        count = count_files_in_domain([], "code")
        assert count == 0


# =============================================================================
# Unit Tests — build_domain_counts
# =============================================================================

class TestBuildDomainCounts:
    """Domain counts for a file set."""

    def test_mixed_files_coverage(self):
        counts = build_domain_counts(SAMPLE_MIXED_FILES)
        assert counts["code"] > 0
        assert counts["security"] > 0
        assert counts["a11y"] > 0
        assert counts["config-ops"] > 0

    def test_empty_files(self):
        counts = build_domain_counts([])
        for domain, count in counts.items():
            assert count == 0, f"Domain '{domain}' should have 0 files for empty input"

    def test_all_domains_present(self):
        """All DOMAIN_CATALOG entries appear in the counts."""
        counts = build_domain_counts(SAMPLE_MIXED_FILES)
        for domain in DOMAIN_CATALOG:
            assert domain in counts, f"Domain '{domain}' missing from counts"


# =============================================================================
# Unit Tests — decide_agent_dispatch
# =============================================================================

class TestDecideAgentDispatch:
    """Dispatch decisions for individual agents."""

    def _make_counts(self, **overrides):
        """Build domain counts with all zeros, then apply overrides."""
        counts = {d: 0 for d in DOMAIN_CATALOG}
        counts.update(overrides)
        return counts

    # --- Always-dispatch agents ---

    def test_always_agent_with_files_dispatches(self):
        config = {"dispatch_class": "always", "domain": "code"}
        counts = self._make_counts(code=5)
        status, reason = decide_agent_dispatch("pr-reviewer", config, counts)
        assert status == "DISPATCH"

    def test_always_agent_without_files_skips(self):
        config = {"dispatch_class": "always", "domain": "go-tests"}
        counts = self._make_counts(code=5)  # go-tests = 0
        status, reason = decide_agent_dispatch("go-tests-reviewer", config, counts)
        assert status == "SKIPPED"
        assert "no files" in reason

    # --- Conditional agents ---

    def test_conditional_agent_with_files_dispatches(self):
        config = {
            "dispatch_class": "conditional",
            "domain": "security",
            "triage_criteria": ["New endpoints"],
        }
        counts = self._make_counts(security=3)
        status, reason = decide_agent_dispatch("security-reviewer", config, counts)
        assert status == "DISPATCH"

    def test_conditional_agent_without_files_skips(self):
        config = {
            "dispatch_class": "conditional",
            "domain": "a11y",
            "triage_criteria": ["JSX components"],
        }
        counts = self._make_counts()  # all zeros
        status, reason = decide_agent_dispatch("a11y-reviewer", config, counts)
        assert status == "SKIPPED"
        assert "no files" in reason

    # --- Manual agents ---

    def test_manual_agent_always_skipped(self):
        config = {"dispatch_class": "manual", "domain": None}
        counts = self._make_counts(code=10)
        status, reason = decide_agent_dispatch("tests-mutation-reviewer", config, counts)
        assert status == "SKIPPED"
        assert "manual" in reason

    # --- Secondary domains ---

    def test_secondary_domain_triggers_dispatch(self):
        config = {
            "dispatch_class": "conditional",
            "domain": "security",
            "secondary_domains": ["config-ops"],
            "triage_criteria": ["CI/CD changes"],
        }
        # Primary domain has 0 files, but secondary has files
        counts = self._make_counts(**{"config-ops": 2})
        status, reason = decide_agent_dispatch("security-reviewer", config, counts)
        assert status == "DISPATCH"

    def test_no_primary_or_secondary_files_skips(self):
        config = {
            "dispatch_class": "conditional",
            "domain": "security",
            "secondary_domains": ["config-ops"],
            "triage_criteria": ["CI/CD changes"],
        }
        counts = self._make_counts()  # all zeros
        status, reason = decide_agent_dispatch("security-reviewer", config, counts)
        assert status == "SKIPPED"


# =============================================================================
# Integration Tests — build_dispatch_plan
# =============================================================================

class TestBuildDispatchPlan:
    """Full dispatch plan generation."""

    def test_output_has_required_fields(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-review",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        assert "mode" in plan
        assert "git_range" in plan
        assert "output_dir" in plan
        assert "scope_summary" in plan
        assert "dispatch" in plan
        assert "agent_signals" in plan
        assert "agent_signals_text" in plan

    def test_mode_passed_through(self, registry):
        for mode in ["full", "incremental", "pr"]:
            plan = build_dispatch_plan(
                mode=mode,
                git_range="main..HEAD",
                output_dir="/tmp/test",
                changed_files=SAMPLE_MIXED_FILES,
                registry=registry,
            )
            assert plan["mode"] == mode

    def test_all_agents_in_dispatch(self, registry):
        """Every agent in the registry appears in the dispatch list."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        agent_names_in_plan = {d["agent"] for d in plan["dispatch"]}
        registry_agents = set(registry["agents"].keys())
        assert agent_names_in_plan == registry_agents

    def test_dispatch_entry_format(self, registry):
        """Each dispatch entry has agent, domain, status, reason."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        for entry in plan["dispatch"]:
            assert "agent" in entry
            assert "domain" in entry
            assert "status" in entry
            assert entry["status"] in ("DISPATCH", "SKIPPED")
            assert "reason" in entry

    def test_agent_signals_format(self, registry):
        """Each signal matches expected format."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        for signal in plan["agent_signals"]:
            assert isinstance(signal, str)
            assert "STATUS=" in signal
            # Each signal starts with an agent name followed by ": STATUS="
            assert ": STATUS=" in signal

    def test_agent_signals_text_matches_signal_list(self, registry):
        """The canonical text block is a newline-join of the signal list."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        assert plan["agent_signals_text"] == "\n".join(plan["agent_signals"])

    def test_scope_summary_structure(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        summary = plan["scope_summary"]
        assert "total_files" in summary
        assert "noise_filtered" in summary
        assert "reviewable_files" in summary
        assert "by_domain" in summary
        assert isinstance(summary["by_domain"], dict)

    def test_empty_file_list(self, registry):
        """Empty file list produces a valid plan with all agents skipped."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=[],
            registry=registry,
        )
        assert plan["scope_summary"]["total_files"] == 0
        for entry in plan["dispatch"]:
            # All agents should be SKIPPED (no files) or SKIPPED (manual)
            assert entry["status"] == "SKIPPED", (
                f"Agent '{entry['agent']}' should be SKIPPED with no files, "
                f"got status='{entry['status']}'"
            )

    def test_noise_only_files_all_skipped(self, registry):
        """Files that are all noise result in all agents skipped."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_NOISE_ONLY_FILES,
            registry=registry,
        )
        assert plan["scope_summary"]["noise_filtered"] > 0
        assert plan["scope_summary"]["reviewable_files"] == 0
        for entry in plan["dispatch"]:
            assert entry["status"] == "SKIPPED"


# =============================================================================
# Integration Tests — Always-dispatch agents with mixed files
# =============================================================================

class TestAlwaysDispatchAgents:
    """Agents with dispatch_class 'always' that have domain files are dispatched."""

    ALWAYS_AGENTS_WITH_CODE_DOMAIN = ["pr-reviewer", "history-insights-reviewer", "patterns-reviewer"]

    def test_code_domain_always_agents_dispatch(self, registry):
        """pr-reviewer, history-insights, patterns always dispatch when code files exist."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        dispatch_map = {d["agent"]: d for d in plan["dispatch"]}
        for agent in self.ALWAYS_AGENTS_WITH_CODE_DOMAIN:
            assert dispatch_map[agent]["status"] == "DISPATCH", (
                f"Always-dispatch agent '{agent}' should be DISPATCH with code files"
            )


# =============================================================================
# Integration Tests — Manual agents
# =============================================================================

class TestManualAgents:
    """Manual agents are always skipped."""

    def test_tests_mutation_reviewer_skipped(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        dispatch_map = {d["agent"]: d for d in plan["dispatch"]}
        entry = dispatch_map["tests-mutation-reviewer"]
        assert entry["status"] == "SKIPPED"
        assert "manual" in entry["reason"]

    def test_tests_mutation_reviewer_skipped_even_with_many_files(self, registry):
        """Manual agents skip regardless of file coverage."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES * 10,
            registry=registry,
        )
        dispatch_map = {d["agent"]: d for d in plan["dispatch"]}
        assert dispatch_map["tests-mutation-reviewer"]["status"] == "SKIPPED"


# =============================================================================
# Integration Tests — Conditional agents domain gating
# =============================================================================

class TestConditionalAgentsDomainGating:
    """Conditional agents skip when their domain has no files."""

    def test_a11y_reviewer_skipped_for_php_only(self, registry):
        """a11y-reviewer should skip when only PHP files changed."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_PHP_FILES,
            registry=registry,
        )
        dispatch_map = {d["agent"]: d for d in plan["dispatch"]}
        # a11y domain requires JS/TS/JSX/TSX/CSS/SCSS — PHP-only should skip
        # But PHP files match the "code" and "security" domains
        assert dispatch_map["a11y-reviewer"]["status"] == "SKIPPED"

    def test_security_reviewer_dispatches_for_config_ops(self, registry):
        """security-reviewer has config-ops as secondary domain."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_CONFIG_ONLY_FILES,
            registry=registry,
        )
        dispatch_map = {d["agent"]: d for d in plan["dispatch"]}
        assert dispatch_map["security-reviewer"]["status"] == "DISPATCH"

    def test_architecture_reviewer_dispatches_for_config_ops(self, registry):
        """architecture-reviewer has config-ops as secondary domain."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_CONFIG_ONLY_FILES,
            registry=registry,
        )
        dispatch_map = {d["agent"]: d for d in plan["dispatch"]}
        assert dispatch_map["architecture-reviewer"]["status"] == "DISPATCH"


# =============================================================================
# Integration Tests — Real registry consistency
# =============================================================================

class TestRegistryConsistency:
    """Dispatch planner handles all agents in the real registry."""

    def test_signal_count_matches_agent_count(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        assert len(plan["agent_signals"]) == len(registry["agents"])

    def test_dispatch_count_matches_agent_count(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        assert len(plan["dispatch"]) == len(registry["agents"])

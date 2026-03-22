"""
Tests for review/plan_dispatch.py — deterministic, no model calls.

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
    "plan_review_dispatch", str(SCRIPTS_DIR / "review" / "plan_dispatch.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Import functions under test
load_registry = _mod.load_registry
parse_changed_files_list = _mod.parse_changed_files_list
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
        assert "changed_files" in plan
        assert "scope_summary" in plan
        assert "agents" in plan
        assert "agent_signals" in plan


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
        """Every cohort agent (non-manual, non-special) appears in the dispatch list."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        agent_names_in_plan = {d["name"] for d in plan["agents"]}
        cohort_agents = {
            name for name, cfg in registry["agents"].items()
            if cfg.get("dispatch_class") not in ("manual", "special")
        }
        assert agent_names_in_plan == cohort_agents

    def test_dispatch_entry_format(self, registry):
        """Each dispatch entry has agent, domain, status, reason."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        for entry in plan["agents"]:
            assert "name" in entry
            assert "domain" in entry
            assert "status" in entry
            assert entry["status"] in ("DISPATCH", "SKIPPED", "SKIPPED_TRIAGE")
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
        for entry in plan["agents"]:
            # All agents should be SKIPPED (no files) or SKIPPED (manual/special)
            assert entry["status"] in ("SKIPPED", "SKIPPED_TRIAGE"), (
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
        for entry in plan["agents"]:
            assert entry["status"] in ("SKIPPED", "SKIPPED_TRIAGE")


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
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        for agent in self.ALWAYS_AGENTS_WITH_CODE_DOMAIN:
            assert dispatch_map[agent]["status"] == "DISPATCH", (
                f"Always-dispatch agent '{agent}' should be DISPATCH with code files"
            )


# =============================================================================
# Integration Tests — Manual agents
# =============================================================================

class TestNonCohortAgents:
    """Manual and special agents are excluded from the dispatch plan entirely."""

    def test_tests_mutation_reviewer_not_in_plan(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        agent_names = {d["name"] for d in plan["agents"]}
        assert "tests-mutation-reviewer" not in agent_names

    def test_decision_reviewer_not_in_plan(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        agent_names = {d["name"] for d in plan["agents"]}
        assert "decision-reviewer" not in agent_names

    def test_exclusion_holds_regardless_of_file_coverage(self, registry):
        """Non-cohort agents stay excluded even with maximum file coverage."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES * 10,
            registry=registry,
        )
        agent_names = {d["name"] for d in plan["agents"]}
        assert "tests-mutation-reviewer" not in agent_names
        assert "decision-reviewer" not in agent_names


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
        dispatch_map = {d["name"]: d for d in plan["agents"]}
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
        dispatch_map = {d["name"]: d for d in plan["agents"]}
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
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        assert dispatch_map["architecture-reviewer"]["status"] == "DISPATCH"


# =============================================================================
# Integration Tests — Real registry consistency
# =============================================================================

class TestRegistryConsistency:
    """Dispatch planner handles all cohort agents in the real registry."""

    def _cohort_count(self, registry):
        return sum(
            1 for cfg in registry["agents"].values()
            if cfg.get("dispatch_class") not in ("manual", "special")
        )

    def test_signal_count_matches_cohort_agent_count(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        assert len(plan["agent_signals"]) == self._cohort_count(registry)

    def test_dispatch_count_matches_cohort_agent_count(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        assert len(plan["agents"]) == self._cohort_count(registry)


# =============================================================================
# New imports for triage tests
# =============================================================================

is_test_file = _mod.is_test_file
get_domain_files = _mod.get_domain_files
triage_conditional_agent = _mod.triage_conditional_agent


# =============================================================================
# Unit Tests — is_test_file
# =============================================================================

class TestIsTestFile:
    """Test file detection using test domain patterns."""

    def test_php_test_file(self):
        assert is_test_file("tests/ControllerTest.php") is True

    def test_js_test_file(self):
        assert is_test_file("src/utils.test.ts") is True

    def test_e2e_test_file(self):
        assert is_test_file("e2e/checkout.spec.ts") is True

    def test_go_test_file(self):
        assert is_test_file("src/utils/auth_test.go") is True

    def test_production_php_file(self):
        assert is_test_file("src/Controller.php") is False

    def test_production_ts_file(self):
        assert is_test_file("src/hooks/useData.ts") is False

    def test_config_file(self):
        assert is_test_file(".github/workflows/ci.yml") is False


# =============================================================================
# Unit Tests — triage_conditional_agent
# =============================================================================

class TestTriageConditionalAgent:
    """Deterministic triage for conditional agents."""

    def _make_config(self, **overrides):
        base = {
            "dispatch_class": "conditional",
            "domain": "security",
        }
        base.update(overrides)
        return base

    # --- Test-only filter ---

    def test_test_only_files_skipped(self):
        """All domain files are test files → SKIPPED_TRIAGE."""
        config = self._make_config()
        domain_files = ["tests/ControllerTest.php", "tests/ServiceTest.php"]
        status, reason = triage_conditional_agent(
            "security-reviewer", config, domain_files, "", {},
        )
        assert status == "SKIPPED_TRIAGE"
        assert "test files" in reason

    def test_mixed_production_and_test_files_dispatches(self):
        """Domain has both production and test files → DISPATCH."""
        config = self._make_config()
        domain_files = ["src/Controller.php", "tests/ControllerTest.php"]
        status, reason = triage_conditional_agent(
            "security-reviewer", config, domain_files, "", {},
        )
        assert status == "DISPATCH"

    def test_production_only_files_dispatches(self):
        """Only production files → DISPATCH."""
        config = self._make_config()
        domain_files = ["src/Controller.php", "src/Service.php"]
        status, reason = triage_conditional_agent(
            "security-reviewer", config, domain_files, "", {},
        )
        assert status == "DISPATCH"

    # --- Keyword matching ---

    def test_commit_keyword_match_dispatches(self):
        """Commit message matches triage keyword → DISPATCH with reason."""
        config = self._make_config(triage_keywords=["auth", "security"])
        status, reason = triage_conditional_agent(
            "security-reviewer", config,
            ["src/Login.php"],
            "fix auth token validation",
            {},
        )
        assert status == "DISPATCH"
        assert "auth" in reason

    def test_commit_keyword_partial_match(self):
        """Partial keyword match (substring) → DISPATCH."""
        config = self._make_config(triage_keywords=["sanitiz"])
        status, reason = triage_conditional_agent(
            "security-reviewer", config,
            ["src/Form.php"],
            "add sanitization to user input",
            {},
        )
        assert status == "DISPATCH"
        assert "sanitiz" in reason

    def test_no_keyword_match_still_dispatches_by_default(self):
        """No keyword match → still DISPATCH (conservative default)."""
        config = self._make_config(triage_keywords=["auth", "security"])
        status, reason = triage_conditional_agent(
            "security-reviewer", config,
            ["src/Report.php"],
            "add CSV export feature",
            {},
        )
        assert status == "DISPATCH"

    # --- Agent-specific checks ---

    def test_dead_code_file_deletions(self):
        """File deletions trigger dead-code-reviewer."""
        config = self._make_config(
            domain="dead-code",
            triage_checks=["file_deletions"],
        )
        diffstat = {"deleted_files": ["src/old.php"], "renamed_files": [], "added": 0, "removed": 50}
        status, reason = triage_conditional_agent(
            "dead-code-reviewer", config, ["src/new.php"], "", diffstat,
        )
        assert status == "DISPATCH"
        assert "deleted or renamed" in reason

    def test_dead_code_net_removal(self):
        """Net code removal triggers dead-code-reviewer."""
        config = self._make_config(
            domain="dead-code",
            triage_checks=["net_removal"],
        )
        diffstat = {"deleted_files": [], "renamed_files": [], "added": 10, "removed": 100}
        status, reason = triage_conditional_agent(
            "dead-code-reviewer", config, ["src/old.php"], "", diffstat,
        )
        assert status == "DISPATCH"
        assert "net removal" in reason

    def test_architecture_large_pr(self):
        """Large PR (20+ files) triggers architecture-reviewer."""
        config = self._make_config(
            domain="architecture",
            triage_checks=["large_pr"],
        )
        domain_files = [f"src/file{i}.php" for i in range(25)]
        status, reason = triage_conditional_agent(
            "architecture-reviewer", config, domain_files, "", {},
        )
        assert status == "DISPATCH"
        assert "large change" in reason

    # --- Empty domain files ---

    def test_empty_domain_files_dispatches(self):
        """Empty domain file list → DISPATCH (conservative)."""
        config = self._make_config()
        status, reason = triage_conditional_agent(
            "security-reviewer", config, [], "", {},
        )
        assert status == "DISPATCH"


# =============================================================================
# Integration Tests — Triage in full dispatch plan
# =============================================================================

SAMPLE_TEST_ONLY_PHP_FILES = [
    "tests/ControllerTest.php",
    "tests/ServiceTest.php",
    "tests/HelperTest.php",
]


class TestTriageInDispatchPlan:
    """Triage integration within build_dispatch_plan."""

    def test_test_only_php_skips_conditional_agents(self, registry):
        """PHP test-only changes skip conditional agents via triage."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_TEST_ONLY_PHP_FILES,
            registry=registry,
            commit_messages="",
            diffstat={"added": 50, "removed": 10, "deleted_files": [], "renamed_files": []},
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}

        # security-reviewer domain matches .php, but all are test files → SKIPPED_TRIAGE
        assert dispatch_map["security-reviewer"]["status"] == "SKIPPED_TRIAGE"
        assert "test files" in dispatch_map["security-reviewer"]["reason"]

        # php-tests-reviewer is always-dispatch and should still run
        assert dispatch_map["php-tests-reviewer"]["status"] == "DISPATCH"

    def test_commit_keywords_dispatch_conditional(self, registry):
        """Commit keywords trigger dispatch for conditional agents."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=["src/Controller.php"],
            registry=registry,
            commit_messages="fix auth token validation and csrf protection",
            diffstat={"added": 50, "removed": 10, "deleted_files": [], "renamed_files": []},
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}

        # security-reviewer should dispatch with keyword match
        assert dispatch_map["security-reviewer"]["status"] == "DISPATCH"
        assert "keyword" in dispatch_map["security-reviewer"]["reason"]

    def test_plan_includes_changed_files(self, registry):
        """Dispatch plan includes changed_files for downstream use."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_MIXED_FILES,
            registry=registry,
        )
        assert "changed_files" in plan
        assert isinstance(plan["changed_files"], list)

    def test_skipped_triage_signal_format(self, registry):
        """SKIPPED_TRIAGE agents produce correct signal format."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_TEST_ONLY_PHP_FILES,
            registry=registry,
            commit_messages="",
            diffstat={"added": 50, "removed": 10, "deleted_files": [], "renamed_files": []},
        )
        triage_skipped = [
            s for s in plan["agent_signals"] if "SKIPPED_TRIAGE" in s
        ]
        assert len(triage_skipped) > 0
        for signal in triage_skipped:
            assert "STATUS=SKIPPED_TRIAGE" in signal

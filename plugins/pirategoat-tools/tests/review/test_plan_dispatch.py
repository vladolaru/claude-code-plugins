"""
Tests for review/plan_dispatch.py — deterministic, no model calls.

Tests the dispatch planner by importing functions directly and by
validating output schema. Mocks subprocess calls to avoid git dependency.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from review.dispatch_status import SIGNAL_ALWAYS, SIGNAL_NO_DOMAIN_FILES, SIGNAL_UNTRIAGED

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
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
triage_conditional_agent = _mod.triage_conditional_agent
build_dispatch_plan = _mod.build_dispatch_plan
get_diffstat = _mod.get_diffstat
detect_unrecognized_source = _mod.detect_unrecognized_source
expand_repo_reviewers = _mod.expand_repo_reviewers
DOMAIN_CATALOG = _mod.DOMAIN_CATALOG


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def registry():
    """Load the real agent registry."""
    return load_registry()


# Representative positive forms. They prove recognition only; they do not
# claim that every iteration or client form in a language is detectable.
ITERATION_PROOF_FORMS = {
    "js/ts for-of": "+  for (const order of orders) {",
    "js/ts forEach": "+  orders.forEach(renderRow);",
    "php foreach": "+\tforeach ( $orders as $order ) {",
    "python for-in": "+    for order in orders:",
    "ruby each": "+    orders.each do |order|",
    "go range": "+\tfor _, order := range orders {",
    "rust/swift for-in": "+    for order in orders {",
    "java enhanced for": "+        for (Order order : orders) {",
    "kotlin for-in": "+        for (order in orders) {",
    "scala comprehension": "+    for (order <- orders) render(order)",
    "ruby scala foreach": "+    orders.foreach(render)",
}

HTTP_CLIENT_PROOF_FORMS = {
    "go": "+\tresp, err := http.Get(url)",
    "js/ts": "+  const r = await fetch(url);",
    "python": "+    resp = requests.get(api_url, timeout=10)",
    "php": "+    $ch = curl_init( $endpoint );",
}


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

SAMPLE_SQL_MIGRATION_FILES = [
    "db/migrations/20260414_add_orders_table.sql",
]

SAMPLE_NOISE_ONLY_FILES = [
    "package-lock.json",
    "assets/logo.png",
    "vendor/autoload.php",
]


# =============================================================================
# Dispatch status contract
# =============================================================================

class TestDispatchStatusContract:
    """Planner output and telemetry share one canonical status vocabulary."""

    @pytest.mark.parametrize("quick", [False, True], ids=["normal", "quick"])
    def test_planner_outputs_use_canonical_supported_statuses(
        self, tmp_path, quick
    ):
        from review.dispatch_status import SUPPORTED_DISPATCH_STATUSES

        quick_plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir=str(tmp_path),
            changed_files=["src/service.py"],
            registry={
                "agents": {
                    "simplification-reviewer": {
                        "dispatch_class": "always",
                        "domain": "code",
                        "focus": "",
                    },
                }
            },
            commit_messages="",
            diffstat={"added": 5, "removed": 0},
            quick=quick,
        )

        assert {
            agent["status"] for agent in quick_plan["agents"]
        } <= SUPPORTED_DISPATCH_STATUSES


# =============================================================================
# Unit Tests — parse_changed_files_list
# =============================================================================

# One row per branch: the falsy-input guard (empty string, None) and the
# split that strips each entry and drops empty ones.
PARSE_FILE_LISTS = [
    pytest.param("a.py, b.ts ,c.php,", ["a.py", "b.ts", "c.php"], id="strips-and-drops-empty"),
    pytest.param("", [], id="empty-string"),
    pytest.param(None, [], id="none"),
]


class TestParseChangedFilesList:
    """Parsing comma-separated file lists."""

    @pytest.mark.parametrize("files_str, expected", PARSE_FILE_LISTS)
    def test_parses_a_comma_separated_list(self, files_str, expected):
        assert parse_changed_files_list(files_str) == expected


# =============================================================================
# Unit Tests — count_files_in_domain
# =============================================================================

# count_files_in_domain has two branches (an unknown domain counts zero; a
# known one counts filter_domain's matches); the rest is DOMAIN_CATALOG's
# regexes, whose per-extension rows live with the catalog in
# agent/test_scope.py (TestTemplateFileClassification for the a11y
# template extensions) and agent/test_scope_routing.py. One row per domain
# the planner routes on.
DOMAIN_COUNTS = [
    pytest.param(SAMPLE_PHP_FILES, "code", 3, id="php-is-code"),
    pytest.param(["src/components/Modal.tsx"], "code", 1, id="tsx-is-code"),
    pytest.param(SAMPLE_SQL_MIGRATION_FILES, "architecture", 1, id="sql-migration-is-architecture"),
    pytest.param(["views/settings.twig"], "a11y", 1, id="twig-is-a11y"),
    pytest.param(["src/auth.go", "src/lib.rs", "db/migrations/001.sql"], "a11y", 0, id="backend-languages-are-not-a11y"),
    pytest.param(["src/utils/auth_test.go"], "go-tests", 1, id="go-test-file"),
    pytest.param(["e2e/checkout.spec.ts"], "e2e-tests", 1, id="e2e-spec"),
    # ci.yml, Dockerfile and main.tf are three branches of the config-ops regex.
    pytest.param(SAMPLE_CONFIG_ONLY_FILES, "config-ops", 3, id="ci-docker-terraform-are-config-ops"),
    pytest.param(SAMPLE_PHP_FILES, "nonexistent-domain", 0, id="unknown-domain"),
    pytest.param([], "code", 0, id="no-files"),
]


class TestCountFilesInDomain:
    """Domain file counting using DOMAIN_CATALOG patterns."""

    @pytest.mark.parametrize("files, domain, expected", DOMAIN_COUNTS)
    def test_counts_the_files_a_domain_matches(self, files, domain, expected):
        assert count_files_in_domain(files, domain) == expected


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
# Unit Tests — get_diffstat
# =============================================================================

class TestGetDiffstat:
    """Git diffstat parsing and path normalization."""

    def test_normalizes_renamed_numstat_paths_to_new_path(self):
        """Renamed numstat entries should key file_stats by the new path."""
        subprocess_results = [
            subprocess.CompletedProcess(
                args=["git", "diff", "--numstat", "main..HEAD"],
                returncode=0,
                stdout="55\t10\tsrc/legacy/CheckoutService.php => src/modern/CheckoutService.php\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "diff", "--diff-filter=A", "--name-only", "main..HEAD"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "diff", "--diff-filter=D", "--name-only", "main..HEAD"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "diff", "--diff-filter=R", "--name-only", "main..HEAD"],
                returncode=0,
                stdout="src/modern/CheckoutService.php\n",
                stderr="",
            ),
        ]

        with patch("subprocess.run", side_effect=subprocess_results):
            diffstat = get_diffstat("main..HEAD")

        assert diffstat["added"] == 55
        assert diffstat["removed"] == 10
        assert diffstat["renamed_files"] == ["src/modern/CheckoutService.php"]
        assert diffstat["file_stats"] == {
            "src/modern/CheckoutService.php": {"added": 55, "removed": 10},
        }


# =============================================================================
# Unit Tests — decide_agent_dispatch
# =============================================================================

def _domain_counts(**overrides):
    """Domain counts with all zeros, then the overrides applied."""
    counts = {d: 0 for d in DOMAIN_CATALOG}
    counts.update(overrides)
    return counts


_SECURITY_WITH_CONFIG_OPS = {
    "dispatch_class": "conditional",
    "domain": "security",
    "secondary_domains": ["config-ops"],
    "triage_criteria": ["CI/CD changes"],
}

# One row per decide_agent_dispatch branch reachable without triage context:
# the primary-domain count, the secondary-domain loop, the no-files skip, the
# always-dispatch return, and the untriaged conditional return. Each row pins
# the status and the signal the branch emits together.
DECIDE_ROWS = [
    pytest.param(
        {"dispatch_class": "always", "domain": "code"}, _domain_counts(code=5),
        "DISPATCH", SIGNAL_ALWAYS, "always dispatch", id="always-with-files",
    ),
    pytest.param(
        {"dispatch_class": "always", "domain": "go-tests"}, _domain_counts(code=5),
        "SKIPPED", SIGNAL_NO_DOMAIN_FILES, "no files", id="always-without-files",
    ),
    pytest.param(
        _SECURITY_WITH_CONFIG_OPS, _domain_counts(**{"config-ops": 2}),
        "DISPATCH", SIGNAL_UNTRIAGED, "conditional (domain has files)",
        id="conditional-via-secondary-domain",
    ),
    pytest.param(
        _SECURITY_WITH_CONFIG_OPS, _domain_counts(),
        "SKIPPED", SIGNAL_NO_DOMAIN_FILES, "no files", id="conditional-without-files",
    ),
]


class TestDecideAgentDispatch:
    """Dispatch decisions for individual agents."""

    @pytest.mark.parametrize("config, counts, status, signal, reason_fragment", DECIDE_ROWS)
    def test_decides_status_and_signal(self, config, counts, status, signal, reason_fragment):
        actual_status, reason, actual_signal = decide_agent_dispatch("x-reviewer", config, counts)
        assert (actual_status, actual_signal) == (status, signal), reason
        assert reason_fragment in reason


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

    def test_domain_patch_text_is_memoized_across_agents(self):
        registry = {
            "agents": {
                "first-reviewer": {
                    "dispatch_class": "conditional",
                    "domain": "security",
                    "focus": "",
                    "triage_keywords": ["auth"],
                },
                "second-reviewer": {
                    "dispatch_class": "conditional",
                    "domain": "security",
                    "focus": "",
                    "triage_keywords": ["token"],
                },
            }
        }

        with patch.object(_mod, "get_diff_text", return_value="+ auth token") as diff_mock:
            plan = build_dispatch_plan(
                mode="full",
                git_range="main..HEAD",
                output_dir="/tmp/test",
                changed_files=["src/Auth.php"],
                registry=registry,
                commit_messages="",
                diffstat={"added": 5, "removed": 0, "deleted_files": [], "renamed_files": []},
            )

        assert diff_mock.call_count == 1
        assert [entry["status"] for entry in plan["agents"]] == ["DISPATCH", "DISPATCH"]

    def test_integration_agent_dispatches_on_diff_keyword_without_host_context(self, tmp_path):
        output_dir = tmp_path / "review"
        output_dir.mkdir()
        (output_dir / "review-context.json").write_text(json.dumps({
            "host_context": {"resolved": []},
        }))
        registry = {
            "agents": {
                "ecosystem-integration-reviewer": {
                    "dispatch_class": "conditional",
                    "domain": "wp-architecture",
                    "focus": "",
                    "triage_keywords": ["add_filter"],
                    "require_triage_keyword_match": True,
                },
            }
        }

        with patch.object(_mod, "get_diff_text", return_value="+ add_filter( 'wc_x', $cb );") as diff_mock:
            plan = build_dispatch_plan(
                mode="full",
                git_range="main..HEAD",
                output_dir=str(output_dir),
                changed_files=["plugin.php"],
                registry=registry,
                commit_messages="",
                diffstat={"added": 5, "removed": 0, "deleted_files": [], "renamed_files": []},
            )

        assert diff_mock.call_count == 1
        assert plan["agents"][0]["status"] == "DISPATCH"
        assert "diff" in plan["agents"][0]["reason"]


# =============================================================================
# Integration Tests — Always-dispatch agents with mixed files
# =============================================================================

class TestAlwaysDispatchAgents:
    """Agents with dispatch_class 'always' that have domain files are dispatched."""

    ALWAYS_AGENTS_WITH_CODE_DOMAIN = ["code-reviewer", "history-insights-reviewer", "patterns-reviewer"]

    def test_code_domain_always_agents_dispatch(self, registry):
        """code-reviewer, history-insights, patterns always dispatch when code files exist."""
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

    def test_a11y_reviewer_dispatches_backend_php_conservatively(self, registry):
        """PHP is in the a11y domain because arbitrary composition code can
        emit UI even when the finite markup detector remains silent."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_PHP_FILES,
            registry=registry,
            commit_messages="refactor csv export batching",
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        assert dispatch_map["a11y-reviewer"]["status"] == "DISPATCH"

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

    def test_unsupported_triage_check_fails_even_without_domain_files(self):
        registry = {
            "agents": {
                "security-reviewer": {
                    "domain": "security",
                    "dispatch_class": "conditional",
                    "focus": "Security",
                    "triage_checks": ["not_a_real_check"],
                },
            },
        }
        with pytest.raises(ValueError, match="Unsupported triage check"):
            build_dispatch_plan(
                mode="full",
                git_range="main..HEAD",
                output_dir="/tmp/test",
                changed_files=["README.md"],
                registry=registry,
            )


# =============================================================================
# New imports for triage tests
# =============================================================================

is_test_file = _mod.is_test_file
get_domain_files = _mod.get_domain_files
triage_conditional_agent = _mod.triage_conditional_agent


# =============================================================================
# Unit Tests — is_test_file
# =============================================================================

# is_test_file ORs the include regexes of the _TEST_DOMAINS catalog entries.
# One positive row per test domain, plus the rust benches/ and python root
# test_ and conftest alternatives, which no other row reaches; two negatives
# whose paths sit next to a test pattern (inline-test rust source, a
# *Page.ts outside a page-objects directory).
IS_TEST_FILE = [
    pytest.param("tests/ControllerTest.php", True, id="php-tests"),
    pytest.param("src/utils.test.ts", True, id="js-tests"),
    pytest.param("e2e/checkout.spec.ts", True, id="e2e-tests"),
    pytest.param("src/utils/auth_test.go", True, id="go-tests"),
    pytest.param("tests/integration_test.rs", True, id="rust-tests-dir"),
    pytest.param("benches/my_bench.rs", True, id="rust-benches-dir"),
    pytest.param("test_utils.py", True, id="python-test-prefix"),
    pytest.param("conftest.py", True, id="python-conftest"),
    # src/lib.rs may hold inline #[cfg(test)] blocks but is production code
    # for triage; production reviewers still run on it.
    pytest.param("src/lib.rs", False, id="rust-production-source"),
    pytest.param("src/HomePage.ts", False, id="production-page-ts"),
]


class TestIsTestFile:
    """Test file detection using test domain patterns."""

    @pytest.mark.parametrize("filepath, expected", IS_TEST_FILE)
    def test_classifies_test_files(self, filepath, expected):
        assert is_test_file(filepath) is expected


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
        status, reason, _signal = triage_conditional_agent(
            "security-reviewer", config, domain_files, "", {},
        )
        assert status == "SKIPPED_TRIAGE"
        assert "test files" in reason

    def test_mixed_production_and_test_files_dispatches(self):
        """Domain has both production and test files → DISPATCH."""
        config = self._make_config()
        domain_files = ["src/Controller.php", "tests/ControllerTest.php"]
        status, reason, _signal = triage_conditional_agent(
            "security-reviewer", config, domain_files, "", {},
        )
        assert status == "DISPATCH"

    def test_production_page_ts_file_does_not_trigger_test_only_skip(self):
        """Conditional reviewers should treat src/*Page.ts as production code."""
        config = self._make_config()
        status, reason, _signal = triage_conditional_agent(
            "security-reviewer", config, ["src/HomePage.ts"], "", {},
        )
        assert status == "DISPATCH"
        assert "test files" not in reason

    def test_production_only_files_dispatches(self):
        """Only production files → DISPATCH."""
        config = self._make_config()
        domain_files = ["src/Controller.php", "src/Service.php"]
        status, reason, _signal = triage_conditional_agent(
            "security-reviewer", config, domain_files, "", {},
        )
        assert status == "DISPATCH"

    # --- Keyword matching ---

    def test_commit_keyword_match_dispatches(self):
        """Commit message matches triage keyword → DISPATCH with reason."""
        config = self._make_config(triage_keywords=["auth", "security"])
        status, reason, _signal = triage_conditional_agent(
            "security-reviewer", config,
            ["src/Login.php"],
            "fix auth token validation",
            {},
        )
        assert status == "DISPATCH"
        assert "auth" in reason

    def test_commit_prefix_keyword_match(self):
        """An explicitly marked prefix keyword → DISPATCH."""
        config = self._make_config(triage_keywords=["sanitiz*"])
        status, reason, _signal = triage_conditional_agent(
            "security-reviewer", config,
            ["src/Form.php"],
            "add sanitization to user input",
            {},
        )
        assert status == "DISPATCH"
        assert "sanitiz*" in reason

    def test_no_keyword_match_still_dispatches_by_default(self):
        """No keyword match → still DISPATCH (conservative default)."""
        config = self._make_config(triage_keywords=["auth", "security"])
        status, reason, _signal = triage_conditional_agent(
            "security-reviewer", config,
            ["src/Report.php"],
            "add CSV export feature",
            {},
        )
        assert status == "DISPATCH"

    def test_devils_advocate_dispatches_for_substantial_in_scope_additions(self):
        """Large in-scope production additions dispatch even without keywords."""
        config = self._make_config(
            domain="architecture",
            min_added_lines=50,
            triage_checks=["substantial_non_test_additions"],
            triage_keywords=["cache", "adapter", "workaround"],
        )
        status, reason, _signal = triage_conditional_agent(
            "devils-advocate-reviewer", config,
            ["src/CheckoutService.php"],
            "refine checkout service orchestration",
            {
                "added": 75,
                "removed": 10,
                "deleted_files": [],
                "renamed_files": [],
                "file_stats": {
                    "src/CheckoutService.php": {"added": 75, "removed": 10},
                },
            },
        )
        assert status == "DISPATCH"
        assert "substantial non-test additions" in reason

    def test_devils_advocate_counts_production_page_ts_additions(self):
        """Production *Page.ts files count toward architecture additions."""
        config = self._make_config(
            domain="architecture",
            min_added_lines=50,
            triage_checks=["substantial_non_test_additions"],
            triage_keywords=["adapter"],
        )
        status, reason, _signal = triage_conditional_agent(
            "devils-advocate-reviewer", config,
            ["src/HomePage.ts"],
            "build homepage application flow",
            {
                "added": 75,
                "removed": 10,
                "deleted_files": [],
                "renamed_files": [],
                "file_stats": {
                    "src/HomePage.ts": {"added": 75, "removed": 10},
                },
            },
        )
        assert status == "DISPATCH"
        assert "substantial non-test additions" in reason

    def test_devils_advocate_dispatches_for_new_abstraction_file(self):
        """New abstraction-shaped files count as positive triage signals."""
        config = self._make_config(
            domain="architecture",
            min_added_lines=50,
            triage_checks=["new_abstraction_files", "substantial_non_test_additions"],
            triage_keywords=["cache", "adapter", "workaround"],
        )
        status, reason, _signal = triage_conditional_agent(
            "devils-advocate-reviewer", config,
            ["src/CheckoutService.php"],
            "refine checkout service orchestration",
            {
                "added": 75,
                "removed": 10,
                "deleted_files": [],
                "renamed_files": [],
                "added_files": ["src/CheckoutService.php"],
                "file_stats": {
                    "src/CheckoutService.php": {"added": 75, "removed": 10},
                },
            },
        )
        assert status == "DISPATCH"
        assert "new abstraction file" in reason

    def test_min_added_lines_uses_in_scope_non_test_additions(self):
        """Threshold gates on in-scope production additions, not total diff lines."""
        config = self._make_config(
            domain="architecture",
            min_added_lines=50,
            triage_checks=["new_abstraction_files", "substantial_non_test_additions"],
            triage_keywords=["adapter"],
        )
        status, reason, _signal = triage_conditional_agent(
            "devils-advocate-reviewer", config,
            ["src/CheckoutAdapter.php", "tests/CheckoutAdapterTest.php"],
            "",
            {
                "added": 105,
                "removed": 5,
                "deleted_files": [],
                "renamed_files": [],
                "added_files": ["src/CheckoutAdapter.php", "tests/CheckoutAdapterTest.php"],
                "file_stats": {
                    "src/CheckoutAdapter.php": {"added": 5, "removed": 0},
                    "tests/CheckoutAdapterTest.php": {"added": 100, "removed": 5},
                },
            },
        )
        assert status == "SKIPPED_TRIAGE"
        assert "5 < 50" in reason

    # --- Agent-specific checks ---

    def test_dead_code_file_deletions(self):
        """File deletions trigger dead-code-reviewer."""
        config = self._make_config(
            domain="dead-code",
            triage_checks=["file_deletions"],
        )
        diffstat = {"deleted_files": ["src/old.php"], "renamed_files": [], "added": 0, "removed": 50}
        status, reason, _signal = triage_conditional_agent(
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
        status, reason, _signal = triage_conditional_agent(
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
        status, reason, _signal = triage_conditional_agent(
            "architecture-reviewer", config, domain_files, "", {},
        )
        assert status == "DISPATCH"
        assert "large change" in reason

    def test_new_function_check_dispatches(self):
        """Configured structural checks should be active, not silently ignored."""
        config = self._make_config(
            domain="clarity",
            triage_checks=["has_new_functions"],
        )
        status, reason, _signal = triage_conditional_agent(
            "code-clarity-reviewer",
            config,
            ["src/service.py"],
            "",
            {},
            diff_text="@@\n+def calculate_total(items):\n+    return sum(items)\n",
        )
        assert status == "DISPATCH"
        assert "new function" in reason

    def test_public_api_check_dispatches(self):
        """Docs drift registry checks should dispatch for public API surface changes."""
        config = self._make_config(
            domain="docs-drift",
            triage_checks=["has_public_api_changes"],
        )
        status, reason, _signal = triage_conditional_agent(
            "docs-drift-reviewer",
            config,
            ["src/api.ts"],
            "",
            {},
            diff_text="@@\n+export function createPaymentIntent(input: Input): Promise<Result> {\n",
        )
        assert status == "DISPATCH"
        assert "public API" in reason

    def test_a_changelog_fragment_is_a_documentation_file(self):
        assert _mod._has_documentation_files(["changelog/fix-thing"]) is True
        assert _mod._has_documentation_files([
            "plugins/woocommerce/changelog/35520-fix"
        ]) is True
        # Real fragment names carry versions; a dot is not an extension.
        assert _mod._has_documentation_files(["changelog/bump-phpstan-2.2.2"]) is True
        # The shared pattern is the whole rule, on both the scope and the
        # planner side: a file directly under `changelog/` is docs-drift's.
        assert _mod._has_documentation_files(["src/changelog/helper.php"]) is True
        assert _mod._has_documentation_files(["changelog/nested/deeper"]) is False

    def test_unsupported_triage_check_raises(self):
        """Unknown registry checks should fail fast instead of falling through."""
        config = self._make_config(
            triage_checks=["not_a_real_check"],
        )
        with pytest.raises(ValueError, match="Unsupported triage check"):
            triage_conditional_agent(
                "security-reviewer",
                config,
                ["src/Controller.php"],
                "",
                {},
            )

    # --- Empty domain files ---

    def test_empty_domain_files_dispatches(self):
        """Empty domain file list → DISPATCH (conservative)."""
        config = self._make_config()
        status, reason, _signal = triage_conditional_agent(
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

    def test_devils_advocate_dispatches_for_large_architecture_change_without_keywords(self, registry):
        """Large architecture diffs still dispatch without keyword matches."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=["src/CheckoutService.php"],
            registry=registry,
            commit_messages="refine checkout service orchestration",
            diffstat={"added": 75, "removed": 10, "deleted_files": [], "renamed_files": []},
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}

        assert dispatch_map["devils-advocate-reviewer"]["status"] == "DISPATCH"
        assert (
            "new abstraction file" in dispatch_map["devils-advocate-reviewer"]["reason"]
            or "substantial non-test additions" in dispatch_map["devils-advocate-reviewer"]["reason"]
        )

    def test_devils_advocate_dispatches_for_large_production_page_file(self, registry):
        """Production *Page.ts files should not be treated as E2E-only code."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=["src/HomePage.ts"],
            registry=registry,
            commit_messages="build homepage application flow",
            diffstat={
                "added": 75,
                "removed": 10,
                "deleted_files": [],
                "renamed_files": [],
                "file_stats": {
                    "src/HomePage.ts": {"added": 75, "removed": 10},
                },
            },
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}

        assert dispatch_map["devils-advocate-reviewer"]["status"] == "DISPATCH"
        assert "substantial non-test additions" in dispatch_map["devils-advocate-reviewer"]["reason"]

    def test_devils_advocate_dispatches_for_sql_migration(self, registry):
        """SQL migrations that look like new infrastructure should reach triage."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=SAMPLE_SQL_MIGRATION_FILES,
            registry=registry,
            commit_messages="",
            diffstat={"added": 75, "removed": 0, "deleted_files": [], "renamed_files": []},
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}

        assert dispatch_map["devils-advocate-reviewer"]["status"] == "DISPATCH"
        assert "migration" in dispatch_map["devils-advocate-reviewer"]["reason"]

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

    def test_quick_mode_skips_simplification_reviewer(self, registry):
        """Quick mode excludes the always-dispatch simplification reviewer."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=["src/Controller.php"],
            registry=registry,
            commit_messages="refine controller flow",
            diffstat={
                "added": 20,
                "removed": 5,
                "deleted_files": [],
                "renamed_files": [],
                "file_stats": {
                    "src/Controller.php": {"added": 20, "removed": 5},
                },
            },
            quick=True,
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}

        assert dispatch_map["simplification-reviewer"]["status"] == "SKIPPED_QUICK_MODE"
        assert "quick review mode" in dispatch_map["simplification-reviewer"]["reason"]

    def test_quick_mode_ignores_repository_identity_for_generic_keywords(
        self, registry, tmp_path, monkeypatch
    ):
        repo = tmp_path / "arbitrary-checkout"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo), "remote", "add", "origin",
                "https://github.com/woocommerce/woocommerce.git",
            ],
            check=True,
        )
        monkeypatch.chdir(repo)

        with patch.object(
            _mod,
            "get_diff_text",
            return_value="-$old_name = 1;\n+$renamed_value = 1;",
        ):
            plan = build_dispatch_plan(
                mode="full",
                git_range="main..HEAD",
                output_dir=str(tmp_path / "review"),
                changed_files=["src/Internal/Service.php"],
                registry={
                    "agents": {
                        "wp-architecture-reviewer": registry["agents"]["wp-architecture-reviewer"],
                    },
                },
                commit_messages="rename local variable",
                # This test pins quick-mode relabeling of the conservative
                # Layer-6 default dispatch.
                diffstat={
                    "added": 60,
                    "removed": 10,
                    "deleted_files": [],
                    "renamed_files": [],
                    "file_stats": {
                        "src/Internal/Service.php": {"added": 60, "removed": 10},
                    },
                },
                quick=True,
            )

        assert plan["agents"][0]["status"] == "SKIPPED_QUICK_MODE"


# Files that cover enough domains to trigger most agents
_QUICK_MODE_TEST_FILES = [
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

_QUICK_MODE_BLOCKED_AGENTS = frozenset([
    "wp-architecture-reviewer",
    "history-insights-reviewer",
    "data-flow-privacy-reviewer",
    "concurrency-reviewer",
    "reliability-reviewer",
])


def _init_main_repo(path):
    """A git repo with a `main` branch at HEAD.

    build_dispatch_plan's triage calls plan_dispatch.get_diff_text() /
    get_repository_identity() via `git diff`/`git rev-parse`, with no cwd
    override — they always read the ambient process CWD, not a subprocess
    we control. Left unpatched, `git_range="main..HEAD"` behaves
    differently depending on which repo pytest happens to be invoked from:
    inside this repo the pathspec resolves to an empty diff (low-signal,
    quick mode skips); from a foreign CWD `git diff` fails outright
    ("not a git repository"), which the triage treats as an unreadable
    scan and dispatches conservatively instead of skipping. Pointing CWD at
    a throwaway repo with a `main` branch at HEAD makes `main..HEAD`
    resolve to an empty diff everywhere, so the test stops depending on
    which repo happens to be running it.
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "checkout", "-q", "-B", "main"], cwd=path, check=True)
    (path / "README.md").write_text("init")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        cwd=path, check=True,
    )
    return path


class TestQuickModeDispatch:
    """Quick mode excludes low-signal agents from dispatch."""

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        """build_dispatch_plan calls straight into plan_dispatch's git
        helpers (no subprocess seam to pass cwd through), so isolation here
        means chdir'ing the test process itself — see _init_main_repo."""
        _init_main_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

    def test_quick_mode_excludes_blocklisted_agents_without_signals(self, registry):
        """quick=True skips blocklisted agents when no triage keywords match."""
        # Use files that don't trigger keyword matches for blocklisted agents
        # (no "hook", "filter", "concurrent", "privacy", "deploy", etc.)
        neutral_files = [
            "src/Controller.php",
            "src/components/Modal.tsx",
            "tests/ControllerTest.php",
            "src/utils/helpers.go",
        ]
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-quick",
            changed_files=neutral_files,
            registry=registry,
            quick=True,
            commit_messages="fix button alignment in modal",
            # This test pins quick-mode relabeling of conservative dispatch.
            diffstat={
                "added": 200,
                "removed": 40,
                "deleted_files": [],
                "renamed_files": [],
                "file_stats": {f: {"added": 50, "removed": 10} for f in neutral_files},
            },
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        for agent_name in _QUICK_MODE_BLOCKED_AGENTS:
            if agent_name not in dispatch_map:
                continue  # agent may have no files in domain
            assert dispatch_map[agent_name]["status"] == "SKIPPED_QUICK_MODE", (
                f"Expected SKIPPED_QUICK_MODE for '{agent_name}', "
                f"got '{dispatch_map[agent_name]['status']}'"
            )

    def test_quick_mode_non_blocked_agents_triage_normally(self, registry):
        """quick=True does not affect non-blocked agents — code-reviewer still dispatches."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-quick",
            changed_files=_QUICK_MODE_TEST_FILES,
            registry=registry,
            quick=True,
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        assert dispatch_map["code-reviewer"]["status"] == "DISPATCH", (
            "code-reviewer should still DISPATCH in quick mode"
        )

    def test_quick_mode_honors_keyword_triage(self, registry):
        """Blocklisted agents with keyword matches should still dispatch in quick mode."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-quick-keywords",
            changed_files=_QUICK_MODE_TEST_FILES,
            registry=registry,
            quick=True,
            # Commit messages with keywords that match blocklisted agents
            commit_messages="fix concurrent race condition in payment hook filter",
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        # concurrency-reviewer should dispatch (keyword "concurrent" matched)
        assert dispatch_map["concurrency-reviewer"]["status"] == "DISPATCH", (
            "concurrency-reviewer should DISPATCH when keywords match, "
            f"got {dispatch_map['concurrency-reviewer']['status']}"
        )
        # wp-architecture-reviewer should dispatch (keyword "hook"/"filter" matched)
        assert dispatch_map["wp-architecture-reviewer"]["status"] == "DISPATCH", (
            "wp-architecture-reviewer should DISPATCH when keywords match, "
            f"got {dispatch_map['wp-architecture-reviewer']['status']}"
        )


# =============================================================================
# Keyword triage — shares the class grouping used by other triage tests
# =============================================================================


class TestKeywordRequiredTriage:
    """Keyword triage remains a positive signal, not an ecosystem-review hard gate."""

    def test_ecosystem_integration_dispatches_php_subclass_without_hook_keyword(self, registry):
        config = registry["agents"]["ecosystem-integration-reviewer"]
        status, reason, _signal = triage_conditional_agent(
            agent_name="ecosystem-integration-reviewer",
            config=config,
            domain_files=["src/OrdersController.php"],
            commit_messages="refactor orders controller",
            diffstat={"added": 10, "removed": 0},
            pr_text="",
            diff_text=(
                "+class OrdersController extends WC_REST_Orders_Controller {\n"
                "+    public function prepare_item_for_response( $object, $request ) {}\n"
                "+}\n"
            ),
        )

        assert status == "DISPATCH"

    def test_triage_dispatches_without_runtime_host_when_keyword_matches(self):
        config = {
            "domain": "wp-architecture",
            "dispatch_class": "conditional",
            "triage_keywords": ["add_filter"],
            "require_triage_keyword_match": True,
        }
        status, reason, _signal = triage_conditional_agent(
            agent_name="ecosystem-integration-reviewer",
            config=config,
            domain_files=["plugin.php"],
            commit_messages="added add_filter hook",
            diffstat={"added": 10, "removed": 0},
            pr_text="",
        )
        assert status == "DISPATCH"
        assert "commits" in reason.lower()

    def test_triage_dispatches_when_keyword_matches(self):
        config = {
            "domain": "wp-architecture",
            "dispatch_class": "conditional",
            "triage_keywords": ["add_filter"],
            "require_triage_keyword_match": True,
        }
        status, reason, _signal = triage_conditional_agent(
            agent_name="ecosystem-integration-reviewer",
            config=config,
            domain_files=["plugin.php"],
            commit_messages="added add_filter hook",
            diffstat={"added": 10, "removed": 0},
            pr_text="",
        )
        assert status == "DISPATCH"

    def test_triage_skips_when_no_keyword_matches(self):
        """Keyword-required agents do not fall through to default dispatch."""
        config = {
            "domain": "wp-architecture",
            "dispatch_class": "conditional",
            "triage_keywords": ["add_filter", "apply_filters", "do_action"],
            "require_triage_keyword_match": True,
        }
        status, reason, _signal = triage_conditional_agent(
            agent_name="ecosystem-integration-reviewer",
            config=config,
            domain_files=["plugin.php"],
            commit_messages="refactored internal helper",  # no keywords
            diffstat={"added": 10, "removed": 0},
            pr_text="",
            diff_text="",  # successful scan, nothing found
        )
        assert status == "SKIPPED_TRIAGE"
        assert "keyword" in reason.lower()

    def test_triage_dispatches_when_diff_keyword_matches(self):
        config = {
            "domain": "wp-architecture",
            "dispatch_class": "conditional",
            "triage_keywords": ["add_filter", "apply_filters", "do_action"],
            "require_triage_keyword_match": True,
        }
        status, reason, _signal = triage_conditional_agent(
            agent_name="ecosystem-integration-reviewer",
            config=config,
            domain_files=["src/Foo.php"],
            commit_messages="refactored internal helper",
            diffstat={"added": 10, "removed": 0},
            pr_text="generic pr title",
            diff_text="+add_filter( 'woocommerce_cart_item_name', 'prefix_name' );",
        )
        assert status == "DISPATCH"
        assert "diff" in reason


# Layer 2's require_php_source_file gate, once per registry agent that sets
# it. Every row carries a keyword that would dispatch at layer 3, so the
# skip shows the gate runs first.
PHP_SOURCE_GATE = [
    pytest.param(
        "ecosystem-integration-reviewer", "src/blocks/checkout/index.ts",
        "register checkout block", "+register_block_type( 'example/checkout', settings );",
        id="ecosystem-integration-ts-block",
    ),
    pytest.param(
        "woo-regression-reviewer", "assets/js/checkout.js",
        "woocommerce checkout tweak", None,
        id="woo-regression-js-only",
    ),
    pytest.param(
        "wp-architecture-reviewer", "src/hooks/useTransient.ts",
        "add hook and filter transient cache", None,
        id="wp-architecture-ts-only",
    ),
]


class TestPhpSourceGate:
    """run12: 'hook, filter, transient' keywords matched a pure-TS repo's
    commit messages and planned wp-architecture into a React codebase. The
    WordPress and WooCommerce reviewers require an actual PHP source file
    before any keyword can dispatch them."""

    @pytest.mark.parametrize("agent_name, filepath, commits, diff_text", PHP_SOURCE_GATE)
    def test_non_php_change_skips_despite_a_keyword(
        self, registry, agent_name, filepath, commits, diff_text,
    ):
        status, reason, _signal = triage_conditional_agent(
            agent_name, registry["agents"][agent_name], [filepath], commits,
            {"added": 10, "removed": 0}, diff_text=diff_text,
        )
        assert status == "SKIPPED_TRIAGE"
        assert "PHP source" in reason


# ---------------------------------------------------------------------------
# Unrecognized-source safety net
# ---------------------------------------------------------------------------

class TestDetectUnrecognizedSource:
    """detect_unrecognized_source() — fail loud on languages no domain reviews."""

    def test_empty(self):
        assert detect_unrecognized_source([]) == []

    def test_rust_is_recognized_after_broadening(self):
        """Rust is now in _PROG_LANGS — must NOT be flagged (the original bug)."""
        files = ["src/auth/login.rs", "src/main.rs"]
        assert detect_unrecognized_source(files) == []

    def test_mainstream_languages_recognized(self):
        files = ["a.kt", "b.swift", "c.cpp", "d.cs", "e.scala", "f.go", "g.py"]
        assert detect_unrecognized_source(files) == []

    def test_longtail_language_flagged(self):
        """A language outside the catalog but in the source superset is flagged."""
        flagged = detect_unrecognized_source(["contract.sol", "lib.nim", "app.py"])
        assert "contract.sol" in flagged
        assert "lib.nim" in flagged
        assert "app.py" not in flagged  # recognized → reviewed → not flagged

    def test_non_source_files_not_flagged(self):
        """Docs/config/data are not 'unrecognized source' — they have their own domains."""
        files = ["README.md", "config.yaml", "data.json", "Cargo.toml", "LICENSE"]
        assert detect_unrecognized_source(files) == []

    def test_result_is_sorted(self):
        flagged = detect_unrecognized_source(["z.nim", "a.sol"])
        assert flagged == ["a.sol", "z.nim"]


class TestSafetyNetInPlan:
    """The safety net is surfaced through build_dispatch_plan output."""

    def test_clean_rust_diff_has_no_warning(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=["src/auth/login.rs", "src/main.rs"],
            registry=registry,
            commit_messages="",
            diffstat={},
        )
        assert plan["warnings"] == []
        assert plan["scope_summary"]["unrecognized_source"] == []

    def test_unrecognized_source_produces_warning(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=["contract.sol", "lib.nim", "src/app.py"],
            registry=registry,
            commit_messages="",
            diffstat={},
        )
        assert plan["scope_summary"]["unrecognized_source"] == ["contract.sol", "lib.nim"]
        assert len(plan["warnings"]) == 1
        assert "UNRECOGNIZED SOURCE" in plan["warnings"][0]
        assert "contract.sol" in plan["warnings"][0]

    def test_warnings_field_always_present(self, registry):
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=["src/app.php"],
            registry=registry,
            commit_messages="",
            diffstat={},
        )
        assert "warnings" in plan
        assert isinstance(plan["warnings"], list)


# =============================================================================
# Unit Tests — woo-regression-reviewer triage gating
# =============================================================================

class TestWooRegressionReviewerTriage:
    """WC-keyword-gated dispatch: only WooCommerce core/extension diffs dispatch."""

    def _config(self, registry):
        return registry["agents"]["woo-regression-reviewer"]

    def test_registry_declares_keyword_gate(self, registry):
        config = self._config(registry)
        assert config["require_triage_keyword_match"] is True
        assert config["require_php_source_file"] is True
        assert config["dispatch_class"] == "conditional"

    def test_dispatches_on_wc_file_path_signal(self, registry):
        status, reason, _signal = triage_conditional_agent(
            "woo-regression-reviewer", self._config(registry),
            ["includes/class-wc-order.php"],
            "fix order total rounding",
            {},
        )
        assert status == "DISPATCH"

    def test_dispatches_on_wc_diff_signal(self, registry):
        status, reason, _signal = triage_conditional_agent(
            "woo-regression-reviewer", self._config(registry),
            ["src/OrderTotals.php"],
            "fix rounding",
            {},
            diff_text="+$total = apply_filters( 'woocommerce_order_get_total', $total );",
        )
        assert status == "DISPATCH"
        assert "woocommerce" in reason

    @pytest.mark.parametrize(
        ("remote_url", "diff_text"),
        [
            (
                "git@github.com:Automattic/woocommerce-payments.git",
                # Deliberately WC-token-free: a WCPay namespace would now match
                # change-local keywords (acronym splitting makes 'WCPay' a wc
                # signal), and this test isolates the REPOSITORY-identity path.
                "+namespace Internal;\n+class Service {}",
            ),
            (
                "https://github.com/woocommerce/automatewoo.git",
                "+namespace AutomateWoo\\Workflows;\n+class Workflow {}",
            ),
        ],
    )
    def test_dispatches_supported_extension_from_repository_identity(
        self, registry, tmp_path, monkeypatch, remote_url, diff_text
    ):
        repo = tmp_path / "arbitrary-checkout"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", remote_url],
            check=True,
        )
        monkeypatch.chdir(repo)

        with patch.object(_mod, "get_diff_text", return_value=diff_text):
            plan = build_dispatch_plan(
                mode="full",
                git_range="main..HEAD",
                output_dir=str(tmp_path / "review"),
                changed_files=["src/Internal/Service.php"],
                registry={"agents": {"woo-regression-reviewer": self._config(registry)}},
                commit_messages="refactor internal service",
                diffstat={"added": 2, "removed": 0},
            )

        assert plan["agents"][0]["status"] == "DISPATCH"
        assert "repository" in plan["agents"][0]["reason"]

    def test_dispatches_from_canonical_upstream_fetch_remote(
        self, registry, tmp_path, monkeypatch
    ):
        repo = tmp_path / "arbitrary-checkout"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo), "remote", "add", "origin",
                "https://github.com/example/payments-fork.git",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(repo), "remote", "add", "upstream",
                "https://github.com/Automattic/woocommerce-payments.git",
            ],
            check=True,
        )
        monkeypatch.chdir(repo)

        with patch.object(
            _mod,
            "get_diff_text",
            return_value="+namespace Vendor\\Payments;\n+class Service {}",
        ):
            plan = build_dispatch_plan(
                mode="full",
                git_range="main..HEAD",
                output_dir=str(tmp_path / "review"),
                changed_files=["src/Internal/Service.php"],
                registry={"agents": {"woo-regression-reviewer": self._config(registry)}},
                commit_messages="refactor internal service",
                diffstat={"added": 2, "removed": 0},
            )

        assert plan["agents"][0]["status"] == "DISPATCH"
        assert "repository" in plan["agents"][0]["reason"]

    def test_repository_identity_ignores_push_only_urls(
        self, tmp_path, monkeypatch
    ):
        repo = tmp_path / "arbitrary-checkout"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo), "remote", "add", "origin",
                "https://github.com/example/payments-fork.git",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(repo), "remote", "set-url", "--add", "--push",
                "origin", "https://github.com/Automattic/woocommerce-payments.git",
            ],
            check=True,
        )
        monkeypatch.chdir(repo)

        identity = _mod.get_repository_identity()

        assert "payments-fork" in identity
        assert "woocommerce-payments" not in identity

    def test_skipped_without_wc_signal(self, registry):
        """Non-WooCommerce PHP repo → SKIPPED_TRIAGE (the triage-out requirement)."""
        status, reason, _signal = triage_conditional_agent(
            "woo-regression-reviewer", self._config(registry),
            ["src/Controller.php"],
            "add csv export feature",
            {},
            pr_text="adds a csv export endpoint",
            diff_text="+function export_csv() { return true; }",
        )
        assert status == "SKIPPED_TRIAGE"
        assert "keyword" in reason


# =============================================================================
# Unit Tests — wp-architecture-reviewer PHP source gate
# =============================================================================

class TestWpArchitectureReviewerTriage:
    """The PHP-source gate's passing side (its skip side is
    TestPhpSourceGate)."""

    def _config(self, registry):
        return registry["agents"]["wp-architecture-reviewer"]

    def test_registry_declares_php_source_gate(self, registry):
        config = self._config(registry)
        assert config["require_php_source_file"] is True
        assert config["dispatch_class"] == "conditional"

    def test_dispatches_for_keyword_match_with_php_file(self, registry):
        """Same keyword signal, but a real PHP source file is in scope →
        DISPATCH still fires."""
        status, reason, _signal = triage_conditional_agent(
            "wp-architecture-reviewer", self._config(registry),
            ["includes/class-wc-hooks.php"],
            "add hook and filter transient cache",
            {},
        )
        assert status == "DISPATCH"


# =============================================================================
# Keyword matching precision — whole-word anchoring, separator normalization,
# structural-path stoplist
# =============================================================================

class TestKeywordMatchingPrecision:
    """Keywords match as whole words, tolerate -/_ separators, and ignore
    repo-structural path segments — the 2026-07-16 false-positive fixes."""

    def _make_config(self, **overrides):
        base = {"dispatch_class": "conditional", "domain": "dead-code"}
        base.update(overrides)
        return base

    # --- Word-start anchoring ---

    def test_keyword_does_not_match_inside_longer_word(self):
        """'move' must not match inside 'remove' (observed FP: dead-code
        dispatched with reason 'commits: remove, move' on one word)."""
        config = self._make_config(triage_keywords=["move"])
        status, reason, _signal = triage_conditional_agent(
            "dead-code-reviewer", config,
            ["src/Renderer.php"],
            "remove dangling label from radio branch",
            {},
        )
        assert "keywords matched" not in reason

    def test_keyword_still_matches_at_word_start(self):
        """'move' matches as a standalone whole word."""
        config = self._make_config(triage_keywords=["move"])
        status, reason, _signal = triage_conditional_agent(
            "dead-code-reviewer", config,
            ["src/Renderer.php"],
            "move helper into trait",
            {},
        )
        assert status == "DISPATCH"
        assert "keywords matched" in reason and "move" in reason

    @pytest.mark.parametrize("keyword, text", [
        ("auth", "Co-Authored-By: someone"),
        ("token", "tokenized-express-checkout--product-page.test.js"),
        ("config", "Chromium was configured to skip it"),
        ("ci", "a circular import"),
        ("address", "the feedback is not addressed"),
    ])
    def test_a_keyword_is_a_whole_word(self, keyword, text):
        """'auth' must not match 'authored' (every audited run dispatched
        security on the Co-Authored-By trailer), 'token' not 'tokenized',
        'config' not 'configured', 'ci' not 'circular'."""
        config = self._make_config(triage_keywords=[keyword])
        status, reason, _signal = triage_conditional_agent(
            "dead-code-reviewer", config, ["src/Renderer.php"], text, {},
        )
        assert "keywords matched" not in reason, (keyword, text, reason)

    def test_a_star_declares_a_prefix(self):
        """'accessib*' keeps matching 'accessibility'; the star survives
        into the reason so the orchestrator sees which entry fired."""
        config = self._make_config(triage_keywords=["accessib*"])
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", config,
            ["src/Renderer.php"],
            "improve accessibility of radio settings",
            {},
        )
        assert status == "DISPATCH"
        assert "accessib*" in reason

    def test_a_prefix_keyword_still_respects_the_start_boundary(self):
        """'cach*' opens 'cache' and 'caching' but never 'recache'."""
        config = self._make_config(triage_keywords=["cach*"])
        status, reason, _signal = triage_conditional_agent(
            "performance-reviewer", config, ["src/Renderer.php"],
            "recache the totals on save", {},
        )
        assert "keywords matched" not in reason

    def test_underscore_and_hyphen_bound_a_whole_word_on_both_sides(self):
        """'register_rest' matches 'register_rest_route' and 'lock' matches
        'release_cache_lock' — identifier separators are boundaries, so a
        keyword ending at one is still a whole word."""
        assert _mod._match_keywords_multi_source(
            ["register_rest"], [("diff", "+ register_rest_route( 'wc/v3', ... )")],
        ) == [("register_rest", "diff")]
        assert _mod._match_keywords_multi_source(
            ["lock"], [("diff", "+ release_cache_lock();")],
        ) == [("lock", "diff")]
        assert _mod._match_keywords_multi_source(
            ["lock"], [("diff", "+ $lockfile = 'pnpm-lock.yaml';")],
        ) == [("lock", "diff")]

    def test_a_keyword_ending_in_a_non_word_character_keeps_prefix_meaning(self):
        assert _mod._match_keywords_multi_source(
            ["wp_"], [("diff", "+ wp_enqueue_script( 'x' );")],
        ) == [("wp_", "diff")]
        assert _mod._match_keywords_multi_source(
            ["query("], [("diff", "+ $wpdb->query( $sql );")],
        ) == [("query(", "diff")]
        assert not _mod._is_prefix_keyword("/**")
        assert _mod._match_keywords_multi_source(
            ["/**"], [("diff", "+ /** A docblock. */")],
        ) == [("/**", "diff")]

    def test_multiword_prefix_keyword(self):
        assert _mod._match_keywords_multi_source(
            ["screen reader*"], [("pr", "tested with screen-readers")],
        ) == [("screen reader*", "pr")]

    # --- Identifier boundaries: snake_case and camelCase ---

    def test_keyword_matches_after_underscore_in_identifier(self):
        """'lock' must match inside release_cache_lock — code separators are
        word starts. \\b treats _ as a word character, hiding keywords that
        appear as identifier segments (observed: concurrency skipped on a
        small diff whose only signal was a *_lock call)."""
        config = self._make_config(
            domain="concurrency", triage_keywords=["lock"],
        )
        status, reason, _signal = triage_conditional_agent(
            "concurrency-reviewer", config,
            ["includes/class-cache.php"],
            "tidy cache handling",
            {},
            diff_text="+ $this->release_cache_lock( $key );",
        )
        assert status == "DISPATCH"
        assert "lock" in reason

    def test_underscore_composed_keyword_matches_snake_case_identifier(self):
        """'user_data' must match get_user_data( — the leading _ before
        'user' blocked \\b too."""
        config = self._make_config(
            domain="data-flow", triage_keywords=["user_data"],
        )
        status, reason, _signal = triage_conditional_agent(
            "data-flow-privacy-reviewer", config,
            ["includes/class-export.php"],
            "extend export",
            {},
            diff_text="+ $payload = get_user_data( $id );",
        )
        assert status == "DISPATCH"
        assert "keywords matched" in reason

    def test_keyword_matches_at_camel_case_boundary(self):
        """'email' must match customerEmail — the camel boundary is a word
        start, and it must survive the lowercasing that keyword matching
        performs."""
        config = self._make_config(
            domain="data-flow", triage_keywords=["email"],
        )
        status, reason, _signal = triage_conditional_agent(
            "data-flow-privacy-reviewer", config,
            ["src/checkout/logger.ts"],
            "extend logger fields",
            {},
            diff_text="+ logger.info({ customerEmail });",
        )
        assert status == "DISPATCH"
        assert "keywords matched" in reason

    def test_word_interior_still_blocked_after_boundary_fix(self):
        """The identifier-boundary fix must not reopen the substring hole:
        'move' still must not match 'remove' or 'removeAll'."""
        config = self._make_config(triage_keywords=["move"])
        for text in ("+ remove_dangling_label();", "+ items.removeAll();"):
            status, reason, _signal = triage_conditional_agent(
                "dead-code-reviewer", config,
                ["src/Renderer.php"],
                "",
                {},
                diff_text=text,
            )
            assert "keywords matched" not in reason, text

    # --- Separator normalization for multiword keywords ---

    def test_multiword_keyword_matches_hyphenated_text(self):
        """'screen reader' must match 'screen-reader-text' in diff text
        (observed miss: the 55669 diff contained screen-reader-text)."""
        config = self._make_config(triage_keywords=["screen reader"])
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", config,
            ["src/Renderer.php"],
            "",
            {},
            diff_text='+ <legend class="screen-reader-text">',
        )
        assert status == "DISPATCH"
        assert "screen reader" in reason

    def test_space_anchored_keyword_matches_separator_variants(self):
        """' wc ' style keywords match across -, _, and space separators."""
        config = self._make_config(triage_keywords=[" wc "])
        status, reason, _signal = triage_conditional_agent(
            "woo-regression-reviewer", config,
            ["includes/class-renderer.php"],
            "update-wc-templates for checkout",
            {},
        )
        assert status == "DISPATCH"

    # --- Structural path segments ---

    def test_diff_headers_do_not_feed_keyword_matching(self):
        """'diff --git a/plugins/...' and '+++ b/plugins/...' are metadata —
        only CHANGED LINES are keyword territory, or the structural-path
        stoplist is defeated by the raw patch."""
        raw_patch = (
            "diff --git a/plugins/woocommerce/includes/class-x.php b/plugins/woocommerce/includes/class-x.php\n"
            "index abc..def 100644\n"
            "--- a/plugins/woocommerce/includes/class-x.php\n"
            "+++ b/plugins/woocommerce/includes/class-x.php\n"
            "@@ -1,2 +1,3 @@\n"
            "+ $y = 1;"
        )
        config = self._make_config(
            domain="reference-integrity", triage_keywords=["plugin"],
        )
        status, reason, _signal = triage_conditional_agent(
            "reference-integrity-reviewer", config,
            ["includes/class-x.php"],
            "",
            {},
            diff_text=raw_patch,
        )
        assert "keywords matched" not in reason
        # And the extraction helper itself keeps only changed-line content:
        assert "diff --git" not in _mod._changed_lines_text(raw_patch)
        assert "$y = 1;" in _mod._changed_lines_text(raw_patch)

    def test_changed_lines_still_feed_keyword_matching(self):
        assert _mod._match_keywords_multi_source(
            ["plugin"], [("diff", "+ register_plugin( $slug );")],
        ) == [("plugin", "diff")]


    def test_structural_path_segment_does_not_match_keyword(self):
        """'plugin' must not match the monorepo scaffold 'plugins/' segment
        (observed FP: reference-integrity dispatched on plugins/woocommerce/)."""
        config = self._make_config(
            domain="reference-integrity",
            triage_keywords=["plugin"],
        )
        status, reason, _signal = triage_conditional_agent(
            "reference-integrity-reviewer", config,
            ["plugins/woocommerce/includes/admin/class-wc-admin-settings.php"],
            "",
            {},
        )
        assert "keywords matched" not in reason

    def test_meaningful_path_segment_still_matches_keyword(self):
        """'plugin' still matches a meaningful (non-scaffold) path segment."""
        config = self._make_config(
            domain="reference-integrity",
            triage_keywords=["plugin"],
        )
        status, reason, _signal = triage_conditional_agent(
            "reference-integrity-reviewer", config,
            ["src/plugin-loader.php"],
            "",
            {},
        )
        assert status == "DISPATCH"
        assert "files: plugin" in reason

    def test_structural_segment_only_dropped_as_directory(self):
        """A basename matching a structural word is kept (only directory
        segments are scaffold noise)."""
        config = self._make_config(
            domain="reference-integrity",
            triage_keywords=["vendor"],
        )
        status, reason, _signal = triage_conditional_agent(
            "reference-integrity-reviewer", config,
            ["src/vendor-sync.php"],
            "",
            {},
        )
        assert status == "DISPATCH"
        assert "files: vendor" in reason


class TestHasNewSourceFiles:
    """A brand-new source module is positive triage evidence ("New modules
    or packages introduced", "New files replacing or superseding existing
    ones") — no signature/keyword signal fires for a new function-only
    file, so without this check a small new module skipped the agents
    whose criteria promise it."""

    def _config(self):
        return {
            "domain": "code",
            "dispatch_class": "conditional",
            "triage_criteria": ["x"],
            "triage_checks": ["has_new_source_files"],
        }

    def _diffstat(self, added_files, files):
        return {
            "added": 8, "removed": 0,
            "added_files": list(added_files),
            "deleted_files": [], "renamed_files": [],
            "file_stats": {f: {"added": 8, "removed": 0} for f in files},
        }

    def test_new_source_module_dispatches(self):
        files = ["scripts/util/parsing.py"]
        status, reason, _signal = triage_conditional_agent(
            "architecture-reviewer", self._config(), files,
            "add parsing helpers", self._diffstat(files, files),
            diff_text="+def parse_header(raw):\n+    return raw.strip()",
        )
        assert status == "DISPATCH"
        assert "new source file" in reason

    def test_new_test_file_does_not_fire_source_check(self):
        files = ["tests/util/test_parsing.py", "scripts/util/existing.py"]
        status, reason, _signal = triage_conditional_agent(
            "architecture-reviewer", self._config(), files,
            "cover parsing helpers",
            self._diffstat(["tests/util/test_parsing.py"], files),
            diff_text="+assert parse('x')",
        )
        assert status == "DISPATCH"
        assert "no triage signal to skip" in reason

    def test_new_non_source_file_does_not_fire_source_check(self):
        files = ["docs/parsing.md", "scripts/util/existing.py"]
        status, reason, _signal = triage_conditional_agent(
            "architecture-reviewer", self._config(), files,
            "document parsing", self._diffstat(["docs/parsing.md"], files),
            diff_text="+How parsing works.",
        )
        assert status == "DISPATCH"
        assert "no triage signal to skip" in reason


# One row per branch of _has_renamed_symbols / _line_rename_pair.
RENAME_DIFFS = [
    pytest.param(
        "-  const tmp = items.filter(active);\n"
        "-  return tmp.length;\n"
        "+  const filteredItems = items.filter(active);\n"
        "+  return filteredItems.length;",
        True, id="local-variable-rename",
    ),
    pytest.param(
        "-        $this->do_thing( $order );\n"
        "+        $this->do_stuff( $order );",
        True, id="call-site-rename",
    ),
    pytest.param(
        "-    $retries = 1;\n"
        "+    $retries = 2;",
        False, id="value-change",
    ),
    pytest.param(
        "-        charge( $order );\n"
        "+        charge( $order, $currency );",
        False, id="structural-change",
    ),
    pytest.param(
        "-    $msg = 'payment failed';\n"
        "+    $msg = 'payment declined';",
        False, id="string-wording-change",
    ),
    pytest.param(
        "+  const filteredItems = items.filter(active);",
        False, id="pure-addition",
    ),
    # Two different identifiers changing on one line is a rewrite, not a rename.
    pytest.param(
        "-  return alpha + beta;\n"
        "+  return gamma + delta;",
        False, id="two-identifiers-change",
    ),
]


class TestHasRenamedSymbols:
    """A symbol rename shows as paired -/+ lines identical except for one
    identifier swap. No signature/keyword signal fires for a local rename
    with neutral commit text, so "Renamed symbols" criteria need this
    structural detector."""

    @pytest.mark.parametrize("diff, expected", RENAME_DIFFS)
    def test_detects_a_one_identifier_swap(self, diff, expected):
        assert _mod._has_renamed_symbols(diff) is expected


class TestDetectorPolarity:
    """Positive form coverage never proves that detector silence is negative
    evidence. Small-diff skipping requires an explicit complete-criteria
    contract, not extension membership or representative proof forms."""

    def _config(self):
        return {
            "domain": "code",
            "dispatch_class": "conditional",
            "triage_criteria": ["x"],
            "triage_keywords": ["wp_remote"],
            "triage_checks": ["has_http_client_calls"],
        }

    def _small(self, filepath):
        return {
            "added": 2, "removed": 1,
            "deleted_files": [], "renamed_files": [], "added_files": [],
            "file_stats": {filepath: {"added": 2, "removed": 1}},
        }

    def test_unclaimed_language_dispatches_conservatively(self):
        f = "src/main/java/SyncClient.java"
        status, reason, _signal = triage_conditional_agent(
            "reliability-reviewer", self._config(), [f],
            "load remote status", self._small(f),
            diff_text="+        HttpResponse<String> resp = client.send(req, handler);",
        )
        assert status == "DISPATCH"

    def test_claimed_language_dispatches_when_detector_is_partial(self):
        f = "internal/sync/notes.go"
        status, _, _signal = triage_conditional_agent(
            "reliability-reviewer", self._config(), [f],
            "tidy comments", self._small(f),
            diff_text="+\t// clarify rounding behavior",
        )
        assert status == "DISPATCH"

    def test_check_registries_derive_from_single_specs_record(self):
        """Supported and diff-based check views derive from one record."""
        specs = _mod._CHECK_SPECS
        for name, spec in specs.items():
            assert set(spec) == {"reads_diff"}, name
        assert set(_mod._SUPPORTED_TRIAGE_CHECKS) == set(specs)
        assert set(_mod._DIFF_BASED_CHECKS) == {
            n for n, s in specs.items() if s["reads_diff"]
        }

    def test_check_runners_cover_specs(self):
        """_CHECK_RUNNERS is the EXECUTION view over _CHECK_SPECS: every
        declared check has exactly one runner and vice versa. Without this
        binding a check could be added to _CHECK_SPECS (passing
        _validate_triage_checks) yet lack a dispatch branch — the silent-skip
        class the ladder-to-dict refactor eliminates."""
        assert set(_mod._CHECK_RUNNERS) == set(_mod._CHECK_SPECS)
        assert all(callable(r) for r in _mod._CHECK_RUNNERS.values())

    @pytest.mark.parametrize("family", sorted(HTTP_CLIENT_PROOF_FORMS))
    def test_http_client_form_is_evidence(self, family):
        line = HTTP_CLIENT_PROOF_FORMS[family]
        assert _mod._has_http_client_calls(line)


class TestTemplateExtensionsAreInherentUI:
    """Pure-template extensions (.twig/.hbs/.erb/.html) ARE the UI — they
    dispatch a11y on extension alone (round-15 P1: a 120-line include-only
    Twig change was skipped because the old gate preceded the fallback).
    PHP/PHTML mix executable logic and markup, so their finite positive
    detectors cannot prove that a token-silent change emits no UI."""

    def _config(self, registry_agents):
        return registry_agents["a11y-reviewer"]

    def test_template_only_diff_dispatches_without_literal_markup(self, registry):
        cfg = registry["agents"]["a11y-reviewer"]
        f = "templates/checkout/form.twig"
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", cfg, [f],
            "compose payment methods",
            {"added": 3, "removed": 0,
             "file_stats": {f: {"added": 3, "removed": 0}}},
            diff_text='+{% set gateways = payment_gateways %}',
        )
        assert status == "DISPATCH", reason

    def test_backend_php_only_diff_dispatches_conservatively(self, registry):
        cfg = registry["agents"]["a11y-reviewer"]
        f = "includes/class-wc-order-store.php"
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", cfg, [f],
            "tune order lookups",
            {"added": 3, "removed": 0,
             "file_stats": {f: {"added": 3, "removed": 0}}},
            diff_text="+ $orders = $this->store->load( $ids );",
        )
        assert status == "DISPATCH"
        assert "no triage signal to skip" in reason

    def test_template_alias_dispatches_as_inherent_ui(self, registry):
        """A compound template extension (.blade.php) is template, not PHP;
        the other extensions is_template_file accepts are pinned in
        agent/test_scope.py::TestTemplateFileClassification."""
        filepath = "resources/views/cart.blade.php"
        cfg = registry["agents"]["a11y-reviewer"]
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer",
            cfg,
            [filepath],
            "pass cart data to view",
            {
                "added": 3,
                "removed": 0,
                "file_stats": {filepath: {"added": 3, "removed": 0}},
            },
            diff_text="+{{ view_model }}",
        )
        assert status == "DISPATCH"
        assert "template file changes" in reason


class TestDashPrefixedContentLines:
    """Changed lines whose CONTENT starts with '--'/'++' render as '---'/'+++'
    in the patch. A prefix blacklist misreads them as file markers and drops
    them; only lines shaped like real markers ('--- a/...', '+++ b/...',
    '/dev/null') are metadata."""

    def test_removed_sql_comment_lines_feed_keyword_matching(self):
        raw_patch = (
            "diff --git a/db/cleanup.sql b/db/cleanup.sql\n"
            "--- a/db/cleanup.sql\n"
            "+++ b/db/cleanup.sql\n"
            "@@ -1,2 +1,1 @@\n"
            "--- drop the stale transient cache first\n"
            " DELETE FROM wp_options WHERE option_name LIKE '%_transient_%';"
        )
        assert "drop the stale transient cache" in _mod._changed_lines_text(raw_patch)

    def test_incremented_added_lines_feed_keyword_matching(self):
        raw_patch = (
            "diff --git a/src/retry.c b/src/retry.c\n"
            "--- a/src/retry.c\n"
            "+++ b/src/retry.c\n"
            "@@ -1,1 +1,2 @@\n"
            " while (pending) {\n"
            "+++retries_with_backoff;"
        )
        assert "++retries_with_backoff;" in _mod._changed_lines_text(raw_patch)

    def test_real_markers_stay_excluded(self):
        raw_patch = (
            "diff --git a/includes/checkout.php b/includes/checkout.php\n"
            "--- a/includes/checkout.php\n"
            "+++ b/includes/checkout.php\n"
            "@@ -1,1 +1,1 @@\n"
            "+$x = 1;"
        )
        assert "checkout" not in _mod._changed_lines_text(raw_patch)

    def test_block_tracker_survives_dash_dash_content_line(self):
        """A removed '-- ...' comment inside an open multiline declaration
        must not reset the block tracker (it is content, not a file
        boundary) — the parameter change after it is still evidence."""
        raw_patch = (
            "diff --git a/src/query.py b/src/query.py\n"
            "--- a/src/query.py\n"
            "+++ b/src/query.py\n"
            "@@ -1,5 +1,5 @@\n"
            " def run_cleanup(\n"
            "--- legacy comment pulled from the SQL template\n"
            "+    batch_size,\n"
            " ):"
        )
        assert _mod._has_modified_signatures(raw_patch)


class TestA11yMixedMarkupDispatch:
    """a11y-reviewer covers server-rendered markup, including PHP/PHTML.

    Known markup and renderer signals provide specific positive reasons, but
    detector silence falls through to conservative dispatch because arbitrary
    composition code can emit UI without matching the finite vocabulary.

    Regression tests for the 2026-07-16 false negative: a PHP-only diff that
    removed a <label for> and added a screen-reader <legend> was skipped with
    'no files in a11y domain' before its keywords were ever consulted."""

    def _a11y_config(self, registry):
        return registry["agents"]["a11y-reviewer"]

    def test_dispatches_on_markup_removal_in_php_diff(self, registry):
        """Removed markup lines count as markup evidence (the 55669 diff
        REMOVED a label — deletion is exactly when blast radius needs review)."""
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["includes/admin/class-wc-admin-settings.php"],
            "fix settings radio markup",
            {},
            diff_text='-<label for="<?php echo esc_attr( $value[\'id\'] ); ?>">',
        )
        assert status == "DISPATCH"
        assert "markup" in reason

    def test_dispatches_on_markup_addition_in_php_diff(self, registry):
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["includes/admin/class-wc-admin-settings.php"],
            "fix settings radio markup",
            {},
            diff_text='+<legend class="screen-reader-text"><span>title</span></legend>',
        )
        assert status == "DISPATCH"

    def test_dispatches_on_a11y_keyword_without_markup_in_diff(self, registry):
        """Commit keywords rescue dispatch even when the scanned diff text
        carries no markup tokens."""
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["includes/class-renderer.php"],
            "improve accessibility of settings rows",
            {},
            diff_text="+ return $rows;",
        )
        assert status == "DISPATCH"
        assert "keyword" in reason

    # The WordPress core renderer alternation, the `<?=` output construct,
    # and the ->render/->display method form of MARKUP_CODE_TOKEN_PATTERNS.
    PHP_RENDER_SURFACES = (
        "wp_nav_menu( $args );",
        "wp_login_form( $args );",
        "get_search_form();",
        "comment_form( $args );",
        "wp_list_comments( $args );",
        "wp_page_menu( $args );",
        "dynamic_sidebar( 'primary' );",
        "the_widget( WC_Widget_Cart::class );",
        "<?= build_custom_navigation( $args ); ?>",
        "$view->display( $context );",
    )

    def test_dispatches_on_php_render_surfaces(self, registry):
        filepath = "includes/class-renderer.php"
        reasons = {}
        for render_call in self.PHP_RENDER_SURFACES:
            status, reason, _signal = triage_conditional_agent(
                "a11y-reviewer",
                self._a11y_config(registry),
                [filepath],
                "adjust rendered output",
                self._large_diffstat(filepath),
                diff_text=f"+ {render_call}",
            )
            reasons[render_call] = (status, reason)
        missed = {
            call: outcome for call, outcome in reasons.items()
            if outcome[0] != "DISPATCH" or "markup emission" not in outcome[1]
        }
        assert missed == {}

    @pytest.mark.parametrize(
        "filepath, render_call",
        [
            ("theme/header.php", "get_header();"),
            ("theme/footer.php", "get_footer();"),
            ("theme/comments.php", "comments_template();"),
            ("views/shell.phtml", "my_plugin_render_shell();"),
        ],
    )
    def test_dispatches_when_mixed_markup_detector_is_silent(
        self, registry, filepath, render_call,
    ):
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer",
            self._a11y_config(registry),
            [filepath],
            "compose rendered page",
            self._large_diffstat(filepath),
            diff_text=f"+ {render_call}",
        )
        assert status == "DISPATCH"
        assert "no triage signal to skip" in reason

    def test_backend_php_dispatches_when_markup_detector_is_silent(self, registry):
        """Backend-looking PHP may still call project-specific render paths;
        detector silence is not proof of accessibility irrelevance."""
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["includes/class-wc-query.php"],
            "refactor query batching",
            {
                "added": 120, "removed": 30,
                "deleted_files": [], "renamed_files": [], "added_files": [],
                "file_stats": {"includes/class-wc-query.php": {"added": 120, "removed": 30}},
            },
            diff_text="+ $results = $wpdb->get_results( $sql );",
        )
        assert status == "DISPATCH"
        assert "no triage signal to skip" in reason

    def test_php_loop_dispatches_without_false_markup_evidence(self, registry):
        """A PHP loop remains token-silent but dispatches conservatively."""
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["includes/class-wc-query.php"],
            "refactor query batching",
            {},
            diff_text="+ for ( $i = 0; $i < $count; $i++ ) { $formats[] = '%s'; }",
        )
        assert status == "DISPATCH"
        assert "no triage signal to skip" in reason

    def test_jsx_with_interactive_markup_dispatches(self, registry):
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["src/components/Modal.tsx"],
            "tweak modal",
            {},
            diff_text='+ <button onClick={close} aria-label="Close">',
        )
        assert status == "DISPATCH"

    # --- Frontend/style files retain their specific positive signals ---

    def _large_diffstat(self, filepath):
        return {
            "added": 100, "removed": 20,
            "deleted_files": [], "renamed_files": [], "added_files": [],
            "file_stats": {filepath: {"added": 100, "removed": 20}},
        }

    def test_css_only_focus_and_contrast_change_dispatches(self, registry):
        """'CSS/SCSS affecting visibility, focus indicators, or contrast' is
        an explicit a11y triage criterion — a sizable CSS-only change must
        dispatch even without keywords or markup tokens."""
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["src/styles/buttons.scss"],
            "adjust button outline and contrast tokens",
            self._large_diffstat("src/styles/buttons.scss"),
            diff_text="+ outline: 2px solid var(--focus-ring);\n+ color: #767676;",
        )
        assert status == "DISPATCH"

    def test_ts_speak_announcement_dispatches_without_import_in_hunk(self, registry):
        """'Screen reader announcements: speak() calls' is an explicit a11y
        triage criterion — a speak() change must not depend on the
        @wordpress/a11y import line happening to be in the diff."""
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["src/store/notices.ts"],
            "announce settings save result",
            self._large_diffstat("src/store/notices.ts"),
            diff_text="+ speak( message, 'polite' );",
        )
        assert status == "DISPATCH"

    def test_mixed_php_and_css_diff_dispatches_on_style_evidence(self, registry):
        """A style file supplies specific positive accessibility evidence."""
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["includes/class-renderer.php", "src/styles/admin.scss"],
            "restyle settings rows",
            {
                "added": 90, "removed": 10,
                "deleted_files": [], "renamed_files": [], "added_files": [],
                "file_stats": {
                    "includes/class-renderer.php": {"added": 30, "removed": 5},
                    "src/styles/admin.scss": {"added": 60, "removed": 5},
                },
            },
            diff_text="+ padding-right: 24px;",
        )
        assert status == "DISPATCH"

    def test_one_line_css_outline_removal_dispatches(self, registry):
        """'+ outline: none;' is a one-line focus-indicator regression — an
        explicit a11y criterion. Style files are inherent visual-a11y surface
        (has_style_files), so this carries positive evidence."""
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["src/styles/buttons.scss"],
            "tidy button styles",
            {
                "added": 1, "removed": 1,
                "deleted_files": [], "renamed_files": [], "added_files": [],
                "file_stats": {"src/styles/buttons.scss": {"added": 1, "removed": 1}},
            },
            diff_text="+ outline: none;",
        )
        assert status == "DISPATCH"

    def test_small_ts_speak_change_dispatches(self, registry):
        """A small speak() announcement change is markup evidence."""
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["src/store/notices.ts"],
            "announce save result",
            {
                "added": 3, "removed": 1,
                "deleted_files": [], "renamed_files": [], "added_files": [],
                "file_stats": {"src/store/notices.ts": {"added": 3, "removed": 1}},
            },
            diff_text="+ speak( message, 'polite' );",
        )
        assert status == "DISPATCH"

    def test_unrelated_test_file_does_not_change_conservative_dispatch(self, registry):
        """An unrelated test cannot manufacture or suppress routing evidence
        for the production PHP change."""
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["includes/class-wc-query.php", "tests/js/query.test.ts"],
            "refactor query batching",
            {
                "added": 80, "removed": 15,
                "deleted_files": [], "renamed_files": [], "added_files": [],
                "file_stats": {
                    "includes/class-wc-query.php": {"added": 60, "removed": 10},
                    "tests/js/query.test.ts": {"added": 20, "removed": 5},
                },
            },
            diff_text="+ $results = $wpdb->get_results( $sql );",
        )
        assert status == "DISPATCH"
        assert "no triage signal to skip" in reason

    def test_small_pure_logic_ts_change_dispatches_without_exhaustive_contract(
        self, registry,
    ):
        """The a11y detector vocabulary is not exhaustive enough to turn
        silence into absence in any mixed-purpose UI language."""
        status, reason, _signal = triage_conditional_agent(
            "a11y-reviewer", self._a11y_config(registry),
            ["src/utils/currency.ts"],
            "fix rounding in currency formatter",
            {
                "added": 6, "removed": 2,
                "deleted_files": [], "renamed_files": [], "added_files": [],
                "file_stats": {"src/utils/currency.ts": {"added": 6, "removed": 2}},
            },
            diff_text="+ return Math.round(value * 100) / 100;",
        )
        assert status == "DISPATCH"
        assert "no triage signal to skip" in reason


class TestHasMarkupChanges:
    """Unit tests for the has_markup_changes triage check helper."""

    def test_interactive_elements_detected(self):
        assert _mod._has_markup_changes('+ <button type="submit">save</button>')
        assert _mod._has_markup_changes("+ <input name='email'>")
        assert _mod._has_markup_changes("- <fieldset><legend>opts</legend>")

    def test_aria_and_a11y_attributes_detected(self):
        assert _mod._has_markup_changes('+ aria-live="polite"')
        assert _mod._has_markup_changes('+ role="dialog"')
        assert _mod._has_markup_changes("+ tabindex=0")
        assert _mod._has_markup_changes('+ <span class="screen-reader-text">')

    def test_focus_management_detected(self):
        assert _mod._has_markup_changes("+ node.focus();")

    def test_media_and_embedded_elements_detected(self):
        """Captions, autoplay, and iframe titles are core a11y concerns —
        media/embedded elements are markup evidence."""
        assert _mod._has_markup_changes("+ <video autoPlay muted src={url} />")
        assert _mod._has_markup_changes("+ <iframe src={embed} />")
        assert _mod._has_markup_changes("+ <audio controls>")
        assert _mod._has_markup_changes('+ <track kind="captions" src={vtt} />')
        assert _mod._has_markup_changes("+ <svg viewBox=\"0 0 24 24\">")
        assert _mod._has_markup_changes("+ <canvas ref={chart}>")

    def test_closing_tags_detected(self):
        """Closing-tag-only changes alter nesting/associations — a moved
        </label> or </fieldset> is markup evidence too."""
        assert _mod._has_markup_changes("- </label>")
        assert _mod._has_markup_changes("+ </fieldset>")

    def test_speak_call_detected(self):
        assert _mod._has_markup_changes("+ speak( __( 'Saved' ) );")

    def test_attribute_assignment_tolerates_whitespace(self):
        """Valid HTML permits whitespace around = — 'role = \"button\"' on a
        <div> (not in the tag list) must still read as markup."""
        assert _mod._has_markup_changes('+ <div role = "button">')
        assert _mod._has_markup_changes('+ <span alt = "decorative">')
        assert _mod._has_markup_changes('+ <div tabindex = "0">')

    def test_php_variable_assignments_are_not_markup(self):
        """$role/$alt/$for assignments emit no markup — they must not count
        as evidence (a large backend false positive can outrank the genuine
        template hunk in a11y budget priority)."""
        assert not _mod._has_markup_changes("+ $role = 'administrator';")
        assert not _mod._has_markup_changes("+ $alt = $fallback;")
        assert not _mod._has_markup_changes("+ $for = $matches[1];")
        assert not _mod._has_markup_changes("+ $tabindex = get_option( 'x' );")

    def test_attribute_forms_still_detected_with_context(self):
        """Real attribute emission keeps matching: quoted values, JSX braces,
        and PHP string-built attributes."""
        assert _mod._has_markup_changes('+ role="button"')
        assert _mod._has_markup_changes("+ tabIndex={0}")
        assert _mod._has_markup_changes("+ echo '<span for=\"' . $id . '\">';")
        assert _mod._has_markup_changes("+ tabindex=0")

    def test_plain_logic_not_detected(self):
        assert not _mod._has_markup_changes("+ for ( $i = 0; $i < 3; $i++ ) {}")
        assert not _mod._has_markup_changes("+ $format = '%s';")
        assert not _mod._has_markup_changes("+ const total = a < b ? a : b;")

    def test_context_lines_ignored(self):
        """Only +/- patch lines count — surrounding context markup doesn't."""
        assert not _mod._has_markup_changes('  <button>unchanged</button>\n+ $count++;')


class TestSmallDiffPolarity:
    """Diff size cannot turn detector silence into negative evidence.

    Partial semantic detectors remain positive-evidence accelerators, never
    absence proofs. There is no declarative escape hatch for that rule.
    """

    def _make_config(self, **overrides):
        base = {"dispatch_class": "conditional", "domain": "concurrency"}
        base.update(overrides)
        return base

    def _small_diffstat(self, filepath="includes/class-renderer.php", added=6, removed=1):
        return {
            "added": added,
            "removed": removed,
            "deleted_files": [],
            "renamed_files": [],
            "added_files": [],
            "file_stats": {filepath: {"added": added, "removed": removed}},
        }

    def test_small_diff_without_signal_dispatches(self):
        config = self._make_config(triage_keywords=["async", "lock"])
        status, reason, _signal = triage_conditional_agent(
            "concurrency-reviewer", config,
            ["includes/class-renderer.php"],
            "fix settings radio markup",
            self._small_diffstat(),
            diff_text="",
        )
        assert status == "DISPATCH"
        assert "no triage signal to skip" in reason

    @pytest.mark.parametrize(
        "agent_name",
        ["performance-reviewer", "reliability-reviewer"],
    )
    def test_go_client_do_dispatches_when_partial_detector_is_silent(
        self, agents, agent_name,
    ):
        filepath = "internal/client.go"
        status, _, _signal = triage_conditional_agent(
            agent_name,
            agents[agent_name],
            [filepath],
            "tidy client code",
            self._small_diffstat(filepath),
            diff_text="+ response, err := client.Do(req)",
        )
        assert status == "DISPATCH"

    def test_indexed_javascript_loop_dispatches_when_partial_detector_is_silent(
        self, agents,
    ):
        filepath = "src/items.js"
        status, _, _signal = triage_conditional_agent(
            "performance-reviewer",
            agents["performance-reviewer"],
            [filepath],
            "tidy item processing",
            self._small_diffstat(filepath),
            diff_text=(
                "+ for (let i = 0; i < items.length; i++) { consume(items[i]); }"
            ),
        )
        assert status == "DISPATCH"

    def test_large_diff_without_signal_still_dispatches_by_default(self):
        config = self._make_config(triage_keywords=["async", "lock"])
        diffstat = self._small_diffstat(added=180, removed=40)
        status, reason, _signal = triage_conditional_agent(
            "concurrency-reviewer", config,
            ["includes/class-renderer.php"],
            "restructure renderer",
            diffstat,
            diff_text="",  # successful scan, nothing found
        )
        assert status == "DISPATCH"
        assert "no triage signal to skip" in reason

    def test_small_diff_with_keyword_evidence_dispatches(self):
        config = self._make_config(triage_keywords=["async", "lock"])
        status, reason, _signal = triage_conditional_agent(
            "concurrency-reviewer", config,
            ["includes/class-renderer.php"],
            "add lock around cache write",
            self._small_diffstat(),
        )
        assert status == "DISPATCH"
        assert "keywords matched" in reason

    def test_small_diff_with_check_evidence_dispatches(self):
        config = self._make_config(
            domain="dead-code",
            triage_checks=["net_removal"],
        )
        diffstat = self._small_diffstat(added=2, removed=30)
        status, reason, _signal = triage_conditional_agent(
            "dead-code-reviewer", config,
            ["includes/class-renderer.php"],
            "trim renderer",
            diffstat,
        )
        assert status == "DISPATCH"

    def test_unsized_diffstat_keeps_default_dispatch(self):
        """No sizing data (empty diffstat) → cannot prove smallness → dispatch."""
        config = self._make_config(triage_keywords=["async"])
        status, reason, _signal = triage_conditional_agent(
            "concurrency-reviewer", config,
            ["includes/class-renderer.php"],
            "fix renderer",
            {},
        )
        assert status == "DISPATCH"

    def test_api_contract_registry_has_signature_checks(self, agents):
        """'Public function signature changes' is an explicit api-contract
        criterion — structural checks must back it so small signature
        changes don't depend on commit-text keywords."""
        checks = set(agents["api-contract-reviewer"].get("triage_checks", []))
        assert {"has_modified_signatures", "has_public_api_changes"} <= checks

    def test_two_line_required_param_addition_dispatches_api_contract(self, agents):
        """Adding a required parameter to a public method is a classic
        two-line breaking change — it must dispatch api-contract-reviewer
        without any keyword in commits."""
        status, reason, _signal = triage_conditional_agent(
            "api-contract-reviewer", agents["api-contract-reviewer"],
            ["src/PaymentGatewayInterface.php"],
            "extend process method",
            {
                "added": 1, "removed": 1,
                "deleted_files": [], "renamed_files": [], "added_files": [],
                "file_stats": {"src/PaymentGatewayInterface.php": {"added": 1, "removed": 1}},
            },
            diff_text=(
                "-    public function process( $order ) {\n"
                "+    public function process( $order, $currency ) {"
            ),
        )
        assert status == "DISPATCH"

    def test_one_line_superglobal_echo_dispatches_security(self, agents):
        status, reason, _signal = triage_conditional_agent(
            "security-reviewer", agents["security-reviewer"],
            ["includes/render.php"],
            "show visitor name",
            {
                "added": 1, "removed": 0,
                "deleted_files": [], "renamed_files": [], "added_files": [],
                "file_stats": {"includes/render.php": {"added": 1, "removed": 0}},
            },
            diff_text="+ echo $_GET['name'];",
        )
        assert status == "DISPATCH"

    def test_test_file_size_does_not_change_conservative_dispatch(self):
        config = self._make_config(triage_keywords=["async"])
        diffstat = {
            "added": 300, "removed": 10,
            "deleted_files": [], "renamed_files": [], "added_files": [],
            "file_stats": {
                "includes/class-renderer.php": {"added": 6, "removed": 1},
                "tests/RendererTest.php": {"added": 294, "removed": 9},
            },
        }
        status, reason, _signal = triage_conditional_agent(
            "concurrency-reviewer", config,
            ["includes/class-renderer.php", "tests/RendererTest.php"],
            "fix renderer",
            diffstat,
            diff_text="",  # successful scan, nothing found
        )
        assert status == "DISPATCH"
        assert "no triage signal to skip" in reason


class TestScenario55669:
    """End-to-end replay of the 2026-07-16 run that motivated the dispatch
    fixes: a 3-file, ~51-line WooCommerce change removing a dangling
    <label for> from the radio branch of WC_Admin_Settings::output_fields().
    The planner dispatched 19 agents (a human pruned to 8) and skipped the
    single most relevant one (a11y) on a file-extension gate."""

    CHANGED_FILES = [
        "plugins/woocommerce/changelog/fix-55669-settings-radio-labels",
        "plugins/woocommerce/includes/admin/class-wc-admin-settings.php",
        "plugins/woocommerce/tests/php/includes/admin/class-wc-admin-settings-test.php",
    ]
    COMMITS = (
        "fix accessibility of radio settings labels\n"
        "remove dangling label for attribute, add screen-reader legend"
    )
    DIFF_TEXT = (
        '-<label for="<?php echo esc_attr( $value[\'id\'] ); ?>">\n'
        '+<legend class="screen-reader-text"><span>'
        "<?php echo esc_html( $value['title'] ); ?></span></legend>\n"
        '+ aria-label="radio help"\n'
        "+ // phpcs:ignore wordpress.security.escapeoutput.outputnotescaped\n"
    )
    DIFFSTAT = {
        "added": 50,
        "removed": 1,
        "deleted_files": [],
        "renamed_files": [],
        "added_files": ["plugins/woocommerce/changelog/fix-55669-settings-radio-labels"],
        "file_stats": {
            "plugins/woocommerce/changelog/fix-55669-settings-radio-labels": {"added": 4, "removed": 0},
            "plugins/woocommerce/includes/admin/class-wc-admin-settings.php": {"added": 12, "removed": 1},
            "plugins/woocommerce/tests/php/includes/admin/class-wc-admin-settings-test.php": {"added": 34, "removed": 0},
        },
    }

    @pytest.fixture()
    def plan(self, registry):
        with patch.object(_mod, "get_diff_text", return_value=self.DIFF_TEXT.lower()):
            return build_dispatch_plan(
                mode="full",
                git_range="main..HEAD",
                output_dir="/tmp/test-55669",
                changed_files=self.CHANGED_FILES,
                registry=registry,
                commit_messages=self.COMMITS,
                diffstat=self.DIFFSTAT,
            )

    def _status(self, plan, name):
        return {d["name"]: d for d in plan["agents"]}[name]["status"]

    def test_a11y_reviewer_dispatches(self, plan):
        """The headline fix: the a11y agent was the highest-value reviewer
        for this change and was skipped on 'no files in a11y domain'."""
        assert self._status(plan, "a11y-reviewer") == "DISPATCH"

    def test_detector_silence_agents_dispatch_conservatively(self, plan):
        """Partial detector silence cannot remove a reviewer from the cohort."""
        for name in (
            "api-contract-reviewer",
            "architecture-reviewer",
            "code-clarity-reviewer",
            "concurrency-reviewer",
            "data-flow-privacy-reviewer",
            "dead-code-reviewer",
            "docs-drift-reviewer",
            "performance-reviewer",
            "reference-integrity-reviewer",
            "reliability-reviewer",
        ):
            assert self._status(plan, name) == "DISPATCH", name

    def test_evidence_backed_agents_still_dispatch(self, plan):
        """Agents with genuine signals keep dispatching: always-on agents,
        keyword-matched specialists, and the history miner."""
        for name in (
            "code-reviewer",
            "patterns-reviewer",
            "php-tests-reviewer",
            "security-reviewer",
            "wp-architecture-reviewer",
            "history-insights-reviewer",
        ):
            assert self._status(plan, name) == "DISPATCH", name

class TestDiffFetchFlags:
    """Triage patch text is fetched with function context and raw paths.

    --function-context (-W) keeps declaration openers visible when a
    parameter changes beyond git's default 3 context lines (otherwise the
    multiline-declaration tracker can never enter declaration state), and
    core.quotepath=false keeps non-ASCII paths matchable. Keyword matching
    is unaffected by the extra context because it reads CHANGED LINES only.
    """

    def test_get_diff_text_uses_function_context_and_raw_paths(self):
        captured = {}
        def fake_run(cmd, capture_output=True, text=True, timeout=30):
            captured["cmd"] = cmd
            class R: returncode = 0; stdout = ""
            return R()
        with patch.object(_mod.subprocess, "run", side_effect=fake_run):
            _mod.get_diff_text("main..HEAD", ["a.php"])
        joined = " ".join(captured["cmd"])
        assert "core.quotepath=false" in joined
        assert "--function-context" in joined or " -W" in f" {joined}"


class TestDiffFetchFailureConservatism:
    """Unknown is not negative — I/O edition. A failed patch fetch must not
    read as a successful negative scan: get_diff_text returns None on
    failure ('' is a real empty result), and every gate that infers signal
    ABSENCE from patch text dispatches conservatively when the scan never
    happened."""

    def _fail_run(self, *a, **k):
        class R:
            returncode = 128
            stdout = ""
            stderr = "fatal: bad revision"
        return R()

    def _blanket_config(self):
        return {
            "domain": "code",
            "dispatch_class": "conditional",
            "triage_criteria": ["x"],
            "triage_keywords": ["woocommerce"],
            "require_triage_keyword_match": True,
        }

    def test_get_diff_text_separates_a_failed_fetch_from_an_empty_diff(self):
        """Nonzero exit and timeout return None; an empty successful diff
        returns "". (A missing git binary is the wrapper-parity test's.)"""
        def timeout_run(cmd, capture_output=True, text=True, timeout=30):
            raise _mod.subprocess.TimeoutExpired(cmd, timeout)

        def ok_run(cmd, capture_output=True, text=True, timeout=30):
            class R:
                returncode = 0
                stdout = ""
            return R()

        for label, run, expected in (
            ("nonzero exit", self._fail_run, None),
            ("timeout", timeout_run, None),
            ("empty success", ok_run, ""),
        ):
            with patch.object(_mod.subprocess, "run", side_effect=run):
                assert _mod.get_diff_text("main..HEAD", ["a.php"]) == expected, label

    def test_blanket_gate_dispatches_when_scan_failed(self):
        status, reason, _signal = triage_conditional_agent(
            "woo-regression-reviewer", self._blanket_config(),
            ["includes/class-wc-order.php"], "", {}, diff_text=None,
        )
        assert status == "DISPATCH"
        assert "unavailable" in reason

    def test_small_diff_gate_dispatches_when_scan_failed(self):
        config = {
            "domain": "code",
            "dispatch_class": "conditional",
            "triage_criteria": ["x"],
            "triage_keywords": ["transaction"],
        }
        diffstat = {
            "added": 4, "removed": 1,
            "file_stats": {"src/orders.php": {"added": 4, "removed": 1}},
        }
        status, reason, _signal = triage_conditional_agent(
            "concurrency-reviewer", config,
            ["src/orders.php"], "", diffstat, diff_text=None,
        )
        assert status == "DISPATCH"
        assert "unavailable" in reason

    def test_successful_empty_scan_still_dispatches(self):
        config = {
            "domain": "code",
            "dispatch_class": "conditional",
            "triage_criteria": ["x"],
            "triage_keywords": ["transaction"],
        }
        diffstat = {
            "added": 4, "removed": 1,
            "file_stats": {"src/orders.php": {"added": 4, "removed": 1}},
        }
        status, _, _signal = triage_conditional_agent(
            "concurrency-reviewer", config,
            ["src/orders.php"], "", diffstat, diff_text="",
        )
        assert status == "DISPATCH"

    def test_fetch_failure_reaches_gates_through_decide_agent_dispatch(self):
        """END-TO-END: the None sentinel must survive the production path.
        A `diff_text or ''` at the triage call converted the failed fetch
        into a successful empty scan, defeating the guard exactly where it
        matters (round-9 finding: unit tests called triage_conditional_agent
        directly and never exercised this)."""
        config = self._blanket_config()
        diffstat = {
            "added": 4, "removed": 1,
            "file_stats": {"includes/class-admin.php": {"added": 4, "removed": 1}},
        }
        with patch.object(_mod, "get_diff_text", return_value=None):
            status, reason, _signal = _mod.decide_agent_dispatch(
                "woo-regression-reviewer", config,
                {"code": 1},
                clean_files=["includes/class-admin.php"],
                commit_messages="",
                diffstat=diffstat,
                git_range="main..HEAD",
            )
        assert status == "DISPATCH"
        assert "unavailable" in reason

    def test_fetch_failure_survives_the_diff_text_cache(self):
        config = self._blanket_config()
        diffstat = {
            "added": 4, "removed": 1,
            "file_stats": {"includes/class-admin.php": {"added": 4, "removed": 1}},
        }
        cache = {}
        with patch.object(_mod, "get_diff_text", return_value=None):
            status, reason, _signal = _mod.decide_agent_dispatch(
                "woo-regression-reviewer", config,
                {"code": 1},
                clean_files=["includes/class-admin.php"],
                commit_messages="",
                diffstat=diffstat,
                git_range="main..HEAD",
                diff_text_cache=cache,
            )
        assert status == "DISPATCH"
        assert "unavailable" in reason
        assert list(cache.values()) == [None]

    def test_agent_needing_no_diff_scan_is_unaffected_by_none(self):
        """No keywords, no diff-based checks: None just means 'never
        needed'; detector silence still cannot authorize a skip."""
        config = {
            "domain": "code",
            "dispatch_class": "conditional",
            "triage_criteria": ["x"],
        }
        diffstat = {
            "added": 4, "removed": 1,
            "file_stats": {"src/orders.php": {"added": 4, "removed": 1}},
        }
        status, _, _signal = triage_conditional_agent(
            "some-reviewer", config,
            ["src/orders.php"], "", diffstat, diff_text=None,
        )
        assert status == "DISPATCH"


class TestKeywordNormalization:
    """Registry keywords are normalized like source text before compiling —
    uppercase or hyphenated keywords were silently dead ('allowBuilds',
    'wp-env' could never match the normalized text)."""

    def test_camel_case_keyword_matches(self):
        matches = _mod._match_keywords_multi_source(
            ["allowBuilds"], [("diff", "+allowBuilds: []")],
        )
        assert matches == [("allowBuilds", "diff")]

    def test_hyphenated_keyword_matches_flattened_path_text(self):
        # _build_file_paths_text turns '.wp-env.json' into '.wp env.json'
        matches = _mod._match_keywords_multi_source(
            ["wp-env"], [("files", ".wp env.json")],
        )
        assert matches == [("wp-env", "files")]

    def test_underscore_keyword_matches_camel_identifier(self):
        matches = _mod._match_keywords_multi_source(
            ["error_log"], [("diff", "+ errorLog(payload);")],
        )
        assert matches == [("error_log", "diff")]

    def test_acronym_camel_boundary_is_split(self):
        """DBTransaction/RESTEndpoint/WCLogger must expose their trailing
        words — the lower→UPPER rule alone leaves 'dbtransaction'."""
        assert _mod._normalize_for_matching("DBTransaction") == "db transaction"
        assert _mod._normalize_for_matching("RESTEndpoint") == "rest endpoint"
        assert _mod._normalize_for_matching("WCLogger") == "wc logger"

    def test_keyword_matches_after_acronym_prefix(self):
        matches = _mod._match_keywords_multi_source(
            ["transaction"], [("diff", "+ $tx = new DBTransaction( $wpdb );")],
        )
        assert matches == [("transaction", "diff")]

    def test_all_caps_identifier_not_over_split(self):
        """A pure acronym stays one token (no interior boundary invented)."""
        assert _mod._normalize_for_matching("HTTPS") == "https"


class TestProseSourceHygiene:
    """The planner matches the author's words: no PR template, no commit
    trailers, no labels (the three runs' named noise sources)."""

    def _context(self, body, labels=("plugin: woocommerce",)):
        return {
            "pr": {"title": "Fix the dropdown", "body": body, "labels": list(labels)},
            "git": {"head_ref": "fix/53136-dropdown"},
            "linked_issues_details": [{"title": "Keyboard opens on mobile"}],
        }

    def test_pr_text_excludes_labels(self):
        text = _mod._build_pr_text(self._context("prose"))
        assert "plugin" not in text.lower()
        assert "Fix the dropdown" in text and "Keyboard opens on mobile" in text
        assert "fix 53136 dropdown" in text

    def test_pr_text_subtracts_the_template_and_its_comments(self):
        template = "### Changes proposed in this Pull Request:\n<!-- describe -->\n- [ ] I reviewed for security best practices\n"
        body = "### Changes proposed in this Pull Request\n<!-- describe -->\nOnly the author wrote this.\n- [x] I reviewed for security best practices\n"
        text = _mod._build_pr_text(self._context(body), pr_template=template)
        assert "security" not in text
        assert "Only the author wrote this." in text

    def test_pr_text_without_a_template_still_drops_comments(self):
        body = "Prose <!-- template: passwords and user data --> more prose"
        text = _mod._build_pr_text(self._context(body), pr_template="")
        assert "password" not in text and "Prose  more prose" in text

    def test_commit_messages_exclude_trailers(self):
        log = "fix: closed keyboard\nBody.\n\nCo-Authored-By: Claude <x@y>\nClaude-Session: https://s\n\x00"
        with patch.object(_mod.subprocess, "run") as run:
            run.return_value = type("R", (), {"returncode": 0, "stdout": log})()
            text = _mod.get_commit_messages("main..HEAD")
        assert text == "fix: closed keyboard\nBody."
        # The contract is per-commit separation, not the exact argv.
        assert any("%x00" in arg for arg in run.call_args[0][0])

    def test_commit_messages_drop_trailers_from_the_actual_git_subject_body_shape(self):
        log = "fix: dropdown\nCo-Authored-By: Security Bot <test@example.com>\n\x00"
        with patch.object(_mod.subprocess, "run") as run:
            run.return_value = type("R", (), {"returncode": 0, "stdout": log})()
            text = _mod.get_commit_messages("main..HEAD")
        assert text == "fix: dropdown"

    @pytest.mark.parametrize("wrapper, args", [
        ("get_changed_files_from_git", ("main..HEAD",)),
        ("get_diff_text", ("main..HEAD",)),
        ("get_diffstat", ("main..HEAD",)),
        ("get_commit_messages", ("main..HEAD",)),
        ("get_repository_identity", ()),
    ])
    def test_every_git_wrapper_degrades_the_same_way_on_a_permission_error(self, wrapper, args):
        """One failure shape across the planner: a git the process may not
        run (PermissionError) degrades exactly like a git that is not
        installed (FileNotFoundError), so the plan is still built from the
        conservative defaults instead of aborting in whichever wrapper
        happens to run first."""
        fn = getattr(_mod, wrapper)
        with patch.object(_mod.subprocess, "run", side_effect=FileNotFoundError):
            missing = fn(*args)
        with patch.object(_mod.subprocess, "run", side_effect=PermissionError):
            assert fn(*args) == missing

    def test_toplevel_is_unknown_on_os_error(self, monkeypatch):
        with patch.object(_mod.subprocess, "run", side_effect=PermissionError):
            assert _mod._git_toplevel() is None

    def test_plan_looks_the_template_up_from_the_checkout(self, registry, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        (repo / ".github").mkdir(parents=True)
        (repo / ".github" / "PULL_REQUEST_TEMPLATE.md").write_text(
            "- [ ] I have reviewed my code for security best practices\n"
        )
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        monkeypatch.chdir(repo)
        body = "- [x] I have reviewed my code for security best practices\nMoves a label.\n"
        with patch.object(_mod, "get_diff_text", return_value="+ $label = 'x';"):
            plan = build_dispatch_plan(
                mode="pr", git_range="main..HEAD", output_dir=str(tmp_path / "out"),
                changed_files=["src/Renderer.php"], registry=registry,
                commit_messages="fix: move the label", diffstat={"added": 2, "removed": 1},
                review_context=self._context(body, labels=()),
            )
        security = next(a for a in plan["agents"] if a["name"] == "security-reviewer")
        assert "pr: security" not in security["reason"]

    def test_an_explicit_empty_template_skips_the_lookup(self, registry, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(_mod, "find_pr_template") as lookup, \
                patch.object(_mod, "get_diff_text", return_value=""):
            build_dispatch_plan(
                mode="pr", git_range="main..HEAD", output_dir=str(tmp_path / "out"),
                changed_files=["src/Renderer.php"], registry=registry,
                commit_messages="", diffstat={"added": 1, "removed": 0},
                review_context=self._context("body"), pr_template="",
            )
        lookup.assert_not_called()






class TestStructuralChecks:
    """Unit tests for the criteria-backing structural check helpers."""

    def test_has_new_types_detects_type_definitions(self):
        assert _mod._has_new_types("+class PaymentRouter {")
        assert _mod._has_new_types("+export interface OrderShape {")
        assert _mod._has_new_types("+abstract class Gateway {")
        assert _mod._has_new_types("+trait HandlesRefunds {")

    def test_has_new_types_ignores_functions_and_removals(self):
        assert not _mod._has_new_types("+function wc_get_order( $id ) {")
        assert not _mod._has_new_types("-class LegacyRouter {")
        assert not _mod._has_new_types("+ $obj = new PaymentRouter();")

    def test_has_import_changes_detects_import_shapes(self):
        assert _mod._has_import_changes("-import { debounce } from 'lodash';")
        assert _mod._has_import_changes("+from collections import OrderedDict")
        assert _mod._has_import_changes("+use Vendor\\Payments\\Client;")
        assert _mod._has_import_changes("+require_once( 'includes/class-cart.php' );")

    def test_has_import_changes_ignores_non_imports(self):
        assert not _mod._has_import_changes("+ $this->import_orders( $file );")
        assert not _mod._has_import_changes("+ // requires manual setup")

    def test_spans_architectural_layers(self):
        assert _mod._spans_architectural_layers(
            ["src/api/A.php", "src/services/B.php", "src/repositories/C.php"]
        )
        assert not _mod._spans_architectural_layers(
            ["src/api/A.php", "src/api/B.php"]
        )

    def test_spans_ignores_test_files(self):
        assert not _mod._spans_architectural_layers(
            ["src/api/A.php", "tests/x/T1.php", "tests/y/T2.php"]
        )

    def test_parameter_change_inside_multiline_signature(self):
        """Adding a required parameter on its own line inside a multiline
        public declaration is a breaking change — the opener is unchanged
        CONTEXT, so line-local patterns alone see nothing."""
        diff = (
            " public function charge(\n"
            "     $order,\n"
            "+    $currency,\n"
            " ) {"
        )
        assert _mod._has_modified_signatures(diff)
        assert _mod._has_public_api_changes(diff)

    def test_argument_change_inside_multiline_hook_emission(self):
        diff = (
            " return apply_filters(\n"
            "     'wc_order_total',\n"
            "     $total,\n"
            "+    $order,\n"
            " );"
        )
        assert _mod._has_public_api_changes(diff)

    def test_string_literal_delimiters_do_not_close_declarations(self):
        """A default like `suffix: str = ")"` must not consume the
        declaration's closing depth — delimiters are counted OUTSIDE
        string literals (round-16 miss: the raw count closed the block
        early and a parameter change after the default skipped
        api-contract and code-clarity)."""
        diff = (
            " def build_label(\n"
            '     suffix: str = ")",\n'
            "+    locale: str,\n"
            " ):"
        )
        assert _mod._has_modified_signatures(diff)

    def test_string_literal_openers_do_not_hold_blocks_open(self):
        """The mirror case: a literal "(" must not keep the tracker inside
        a declaration that already closed."""
        diff = (
            ' def wrap(prefix: str = "(") -> str:\n'
            "+    self.audit()\n"
        )
        assert not _mod._has_modified_signatures(diff)

    def test_changes_after_closed_declaration_do_not_count(self):
        """Once the declaration's parens close, body changes are not
        signature evidence."""
        diff = (
            " public function charge( $order ) {\n"
            "+    $this->log( $order );\n"
            " }"
        )
        assert not _mod._has_modified_signatures(diff)

    def test_php_typed_property_change_is_public_api(self):
        """Public DATA members are API surface too — a DTO property change
        is as breaking as a signature change, and no function-shaped
        pattern sees it (observed miss: `public string $status` →
        `public ?string $status` skipped api-contract on a small diff)."""
        diff = (
            "-    public string $status;\n"
            "+    public ?string $status;"
        )
        assert _mod._has_public_api_changes(diff)

    def test_php_readonly_property_is_public_api(self):
        assert _mod._has_public_api_changes("+    public readonly int $count;")

    def test_php_untyped_property_is_public_api(self):
        assert _mod._has_public_api_changes("+    public $legacy_total;")

    def test_php_promoted_constructor_property_is_public_api(self):
        assert _mod._has_public_api_changes(
            "+        public string $currency = 'USD',"
        )

    def test_php_class_constant_is_public_api(self):
        assert _mod._has_public_api_changes(
            "+    public const STATUS_OPEN = 'open';"
        )

    def test_php_private_property_is_not_public_api(self):
        assert not _mod._has_public_api_changes("+    private string $cache;")

    def test_ts_public_class_field_is_public_api(self):
        assert _mod._has_public_api_changes("+  public status: string;")
        assert _mod._has_public_api_changes("+  public readonly id?: number;")

    def test_csharp_auto_property_is_public_api(self):
        assert _mod._has_public_api_changes(
            "+    public string Status { get; set; }"
        )

    def test_java_public_field_is_public_api(self):
        assert _mod._has_public_api_changes(
            "+    public static final int MAX_RETRIES = 3;"
        )

    def test_ts_implicit_public_field_is_public_api(self):
        """TS class fields are public BY DEFAULT — requiring the `public`
        keyword missed plain DTO fields (round-11 miss: status: string →
        status?: string skipped api-contract). Optional markers and
        primitive/generic/array/union types identify the field shape."""
        for line in (
            "+  status?: string;",
            "-  status: string;",
            "+  readonly id: number;",
            "+  total: number[];",
            "+  items: Array<OrderItem>;",
            "+  state: OrderState | null;",
        ):
            assert _mod._has_public_api_changes(line), line

    def test_css_declarations_are_not_public_api(self):
        """CSS declarations share the `name: value;` line shape — the field
        patterns key on TS-only markers (`?:`) and type-shaped values."""
        for line in (
            "+  color: red;",
            "+  display: block;",
            "+  margin: 0 auto;",
            "+  font-size: 1.2rem;",
        ):
            assert not _mod._has_public_api_changes(line), line

    def test_object_literal_members_are_not_public_api(self):
        assert not _mod._has_public_api_changes("+  status: 'open',")
        assert not _mod._has_public_api_changes("+  retries: 3,")

    def test_go_struct_field_change_is_public_api(self):
        """Exported Go struct fields are wire format — a field TYPE change
        inside a struct body is a contract change even though fields are
        not signatures (round-14 miss). The body tracker lowercases, so
        unexported struct bodies over-dispatch rather than exported ones
        skipping — the chosen error side."""
        diff = (
            " type OrderResponse struct {\n"
            "-\tTotal int64 `json:\"total\"`\n"
            "+\tTotal float64 `json:\"total\"`\n"
            " }"
        )
        assert _mod._has_public_api_changes(diff)
        # struct bodies stay OUT of signature evidence (fields != methods):
        assert not _mod._has_modified_signatures(diff)

    def test_new_exported_go_declarations_are_public_api(self):
        """Go exports by CAPITALIZATION — the one signal that cannot
        survive the lowercased triage pipeline, so it gets a dedicated
        case-preserving scan (round-13 miss: a new exported func skipped
        docs-drift)."""
        for line in (
            "+func ExportedName(ctx context.Context) (string, error) {",
            "+func (s *Store) Lookup(key string) (string, error) {",
            "+type OrderStore interface {",
            "+type OrderRow struct {",
        ):
            assert _mod._has_public_api_changes(line), line

    def test_unexported_go_declarations_are_not_public_api(self):
        for line in (
            "+func lookup(ctx context.Context) (string, error) {",
            "+func (s *Store) refresh() error {",
            "+type orderRow struct {",
        ):
            assert not _mod._has_public_api_changes(line), line

    def test_qualified_and_nullable_member_types_are_public_api(self):
        """C#/Java member types can be namespace-qualified or nullable —
        the type grammar rejected dots (round-12 miss: a
        `public Foo.Bar Status;` change skipped api-contract)."""
        for line in (
            "+    public Billing.Status Status;",
            "+    public int? Count { get; set; }",
            "+    public Map<String, Order.Line> Lines;",
        ):
            assert _mod._has_public_api_changes(line), line

    def test_local_assignments_are_not_public_api(self):
        assert not _mod._has_public_api_changes("+    $status = 'open';")
        assert not _mod._has_public_api_changes("+  const status = 'open';")
        assert not _mod._has_public_api_changes("+    self.status = status")

    def test_http_client_calls_are_client_evidence(self):
        """"HTTP/API client calls" had near-zero vocabulary outside
        'fetch'/'request' keywords (round-13 miss: go http.Get(url)
        skipped performance and reliability)."""
        for line in (
            "+\tresp, err := http.Get(url)",
            "+\treq, _ := http.NewRequest(\"POST\", url, body)",
            "+    $ch = curl_init( $endpoint );",
            "+    const client = new HttpClient(baseUrl);",
            "+    resp = axios.get(url)",
        ):
            assert _mod._has_http_client_calls(line), line

    def test_http_lookalikes_are_not_client_evidence(self):
        for line in (
            "+    const rows = prefetch(keys);",
            "+    fetched = store.fetchFromCache(id)",
            "+    http_status = 200",
        ):
            assert not _mod._has_http_client_calls(line), line


    def test_collection_iteration_forms_are_evidence(self):
        """Loops over collections back performance's iteration criterion
        (round-15 miss: a TSX for-of skipped despite the criterion).
        These forms prove positive recognition, not exhaustive coverage."""
        missed = [
            family for family, line in ITERATION_PROOF_FORMS.items()
            if not _mod._has_collection_iteration(line)
        ]
        assert missed == []

    def test_prose_and_comments_are_not_iteration_evidence(self):
        for line in (
            "+    # for each item in the list, keep the newest",
            "+    // for those in need of context, see the docs",
            "+    * for x in xs the invariant holds",
            "+    reason = 'checked for regressions in payments'",
        ):
            assert not _mod._has_collection_iteration(line), line

    def test_raw_sql_statements_are_query_evidence(self):
        """Raw SQL in strings carries no keyword outside WP ('wpdb',
        'query(' miss cursor.execute) — statement shapes are the
        language-agnostic signal (round-9 miss: a SELECT skipped
        performance on a small diff)."""
        for line in (
            '+    cursor.execute("SELECT id, total FROM orders")',
            "+    db.exec('DELETE FROM sessions WHERE expired = 1')",
            '+    conn.run("INSERT INTO logs VALUES (?, ?)")',
            "+    stmt = 'UPDATE orders SET status = ? WHERE id = ?'",
        ):
            assert _mod._has_sql_queries(line), line

    def test_interior_sql_clause_edits_are_query_evidence(self):
        """The DML opener is often unchanged CONTEXT while an ORDER BY /
        JOIN / GROUP BY line changes — clause shapes are evidence in their
        own right (round-10 miss: an ORDER BY edit skipped performance)."""
        for line in (
            "+        ORDER BY created_at DESC, id DESC",
            "+        GROUP BY customer_id",
            "+        LEFT JOIN order_items ON order_items.order_id = orders.id",
            "+        JOIN order_items ON order_items.order_id = orders.id",
            "+        JOIN order_items oi ON oi.order_id = orders.id",
            "+        HAVING COUNT(*) > 1",
            "+        UNION ALL SELECT id, total FROM archived_orders",
            "+        LIMIT 50 OFFSET 100",
            "+        WHERE status = 'open' AND total > 100",
            '+        WHERE created_at >= %s',
            "+        WHERE customer_id IN (1, 2, 3)",
        ):
            assert _mod._has_sql_queries(line), line

    def test_ddl_statements_are_query_evidence(self):
        for line in (
            "+CREATE INDEX idx_orders_created ON orders (created_at);",
            "+CREATE UNIQUE INDEX idx_sku ON products (sku);",
            "+ALTER TABLE orders ADD COLUMN currency TEXT;",
            "+DROP INDEX idx_orders_created;",
            '+    db.exec("CREATE TABLE sessions (id TEXT PRIMARY KEY)")',
        ):
            assert _mod._has_sql_queries(line), line

    def test_sql_lookalikes_are_not_query_evidence(self):
        for line in (
            "+    document.querySelector('.from');",
            "+    <select name=\"from_currency\">",
            "+    const selected = fromEntries(pairs);",
            "+    // select the widget from the registry",
            "+    const sorted = orderBy(items, 'date');",
            "+    // group by category, then sort",
            "+    joined = ', '.join(parts)",
            "+    // join us on slack for updates",
            "+    // where the config = null we fall back",
            "+    const clause = somewhere(x);",
        ):
            assert not _mod._has_sql_queries(line), line

    def test_bare_param_arrow_declaration_is_signature_evidence(self):
        """`const f = value => ...` needs no parens around a single
        parameter (round-12 miss: code-clarity skipped the new-function
        criterion for this common form)."""
        for line in (
            "+const normalize = value => value.trim();",
            "+export const load = async id => fetchOrder(id);",
        ):
            assert _mod._has_new_functions(line), line

    def test_plain_const_assignments_are_not_signature_evidence(self):
        for line in (
            "+const retries = limit >= 3 ? 3 : limit;",
            "+const total = price * qty;",
        ):
            assert not _mod._has_new_functions(line), line

    def test_package_private_java_method_is_signature_evidence(self):
        """Methods with NO access modifier are legal Java (package-private)
        — the modifier-anchored pattern alone silently gated them."""
        assert _mod._has_new_functions("+    OrderStatus resolveStatus(Order order) {")
        assert _mod._has_new_functions("+    void doThing(Order o) {")
        assert _mod._has_new_functions("+    List<Order> activeOrders(Store s) {")

    def test_type_first_pattern_rejects_control_flow_and_calls(self):
        for line in (
            "+    else if (isReady) {",
            "+    return new Order(id) {",
            "+    export default function(x) {",
            "+    log.info(msg);",
            "+    foreach ($items as $item) {",
        ):
            assert not _mod._has_new_functions(line), line

    def test_head_and_options_routes_are_endpoint_evidence(self):
        """HEAD/OPTIONS are standard verbs — the decorator and attribute
        patterns knew them but the router-method patterns did not
        (round-11 miss: router.options('/orders') skipped api-contract)."""
        for line in (
            "+router.options('/orders', preflight);",
            "+router.head('/orders/:id', probe);",
            "+    app.options(\"orders\") { req in",
        ):
            assert _mod._has_public_api_changes(line), line

    def test_router_handle_registrations_are_endpoint_evidence(self):
        """gorilla/chi register via r.HandleFunc(...) — handle verbs were
        missing from the receiver pattern (round-13 miss)."""
        for line in (
            '+\tr.HandleFunc("/orders", listOrders).Methods("GET")',
            '+\tmux.Handle("/orders", ordersHandler)',
        ):
            assert _mod._has_public_api_changes(line), line

    def test_php_route_receivers_are_endpoint_evidence(self):
        """PHP routers register with :: and -> operators, not dots
        (round-12 miss: Route::get('/orders') skipped api-contract)."""
        for line in (
            "+Route::get('/orders', [OrderController::class, 'index']);",
            "+$router->get('/orders', 'OrderController@index');",
            "+$app->post('/orders', OrderCreateAction::class);",
        ):
            assert _mod._has_public_api_changes(line), line

    def test_php_collection_getters_are_not_endpoint_evidence(self):
        for line in (
            "+$order->get('total');",
            "+$request->get('ref');",
            "+Config::get('app.locale');",
        ):
            assert not _mod._has_public_api_changes(line), line

    def test_route_lookalikes_are_not_endpoint_evidence(self):
        """Collection getters, generic decorators, and plain calls must not
        read as route registrations."""
        for line in (
            "+    params.get('order_id')",
            "+    cache.get('user:42')",
            "+    @functools.wraps(fn)",
            "+    @property",
            "+    get(name)",
            "+    self.route = route",
        ):
            assert not _mod._has_public_api_changes(line), line

    def test_python_multiline_import_member_is_import_evidence(self):
        """`from x import (` spans lines; the member line carries no import
        token (round-11 miss: dead-code skipped on a small addition inside
        the block)."""
        diff = (
            " from orders.types import (\n"
            "+    OrderStatus,\n"
            " )"
        )
        assert _mod._has_import_changes(diff)

    def test_rust_multiline_use_member_is_import_evidence(self):
        diff = (
            " use crate::orders::{\n"
            "+    Router,\n"
            " };"
        )
        assert _mod._has_import_changes(diff)

    def test_plain_call_blocks_are_not_import_evidence(self):
        diff = (
            " results = fetch(\n"
            "+    OrderStatus,\n"
            " )"
        )
        assert not _mod._has_import_changes(diff)

    def test_removed_signature_is_modified_signature_evidence(self):
        """Deleting a function is a contract change — a removed signature
        counts WITHOUT a paired added one (round-12 miss: a sub-50-line
        `func Exported(...)` deletion skipped api-contract and docs-drift).
        Go's export-by-capitalization is unrecoverable after lowercasing,
        so any removed signature counts — unexported removals over-dispatch
        rather than exported ones skipping."""
        diff = (
            "-func ExportedName(ctx context.Context) (string, error) {\n"
            "-\treturn lookup(ctx)\n"
            "-}"
        )
        assert _mod._has_modified_signatures(diff)

    def test_added_only_signature_is_not_modified(self):
        """Pure additions stay excluded — has_new_functions owns that
        signal, and agents that deliberately don't carry it (api-contract
        on new modules) must not regain it through the back door."""
        assert not _mod._has_modified_signatures(
            "+def parse_header(raw):\n+    return raw.strip()"
        )

    def test_go_interface_member_change_is_signature_evidence(self):
        """Go declares interfaces name-first (`type X interface {`) — the
        TS-anchored opener (`interface X`) never entered body state, so a
        member-signature change inside a Go interface carried no signal
        (round-10 miss)."""
        diff = (
            " type OrderStore interface {\n"
            "-\tGetName(ctx context.Context, key string) (string, error)\n"
            "+\tGetName(ctx context.Context, key string, loc Locale) (string, error)\n"
            " }"
        )
        assert _mod._has_modified_signatures(diff)

    def test_go_struct_body_change_is_not_interface_evidence(self):
        """Struct bodies are fields, not signatures — only `interface`
        bodies enter the type-body tracker via the Go opener."""
        diff = (
            " type OrderRow struct {\n"
            "+\tCreatedAt time.Time\n"
            " }"
        )
        assert not _mod._has_modified_signatures(diff)

    def test_ts_interface_member_change_is_signature_evidence(self):
        """Interface members are signatures — with or without the interface
        opener in the hunk (annotated members match line-locally; block
        tracking covers the rest)."""
        assert _mod._has_modified_signatures(
            "-  getName(key: string): string;\n"
            "+  getName(key: string, loc: Locale): string;"
        )

    def test_ts_class_method_change_is_signature_evidence(self):
        assert _mod._has_modified_signatures(
            "-  async getName(key: string): Promise<string> {\n"
            "+  async getName(key: string, loc: Locale): Promise<string> {"
        )

    def test_ts_arrow_function_change_is_signature_evidence(self):
        assert _mod._has_modified_signatures(
            "-export const getName = (key: string): string =>\n"
            "+export const getName = (key: string, loc: Locale): string =>"
        )

    def test_member_change_inside_interface_block_is_signature_evidence(self):
        """Unannotated members carry no line-local shape — the interface
        BLOCK context makes every changed line inside it a signature."""
        diff = (
            " export interface OrderShape {\n"
            "   id: number;\n"
            "+  currency: string;\n"
            " }"
        )
        assert _mod._has_modified_signatures(diff)

    def test_call_statements_are_not_signatures(self):
        """The TS method patterns must not fire on ordinary call statements
        or control flow — that would dispatch clarity on every added call."""
        for line in (
            "+ doThing(arg);",
            "+ logger.info(payload);",
            "+ if (enabled) {",
            "+ while (pending) {",
            "+ it('renders', () => {",
        ):
            assert not _mod._has_new_functions(line), line

    def test_replacement_inside_multiline_import_block_detected(self):
        """Swapping an entry inside a Go/TS import block shows no import
        opener on the changed lines — block tracking must catch it."""
        assert _mod._has_import_changes(
            ' import (\n \t"context"\n-\t"legacy/pkg"\n+\t"acme/pkg"\n )'
        )
        assert _mod._has_import_changes(
            " import {\n   parse,\n-  legacyHelper,\n+  newHelper,\n } from './utils';"
        )

    def test_inline_hash_comment_counts_as_comment_change(self):
        """Python/Ruby inline comments after code — '# ' mid-line."""
        assert _mod._has_docblock_changes("+ total = subtotal  # shipping added later")

    def test_hash_in_hex_color_is_not_a_comment(self):
        assert not _mod._has_docblock_changes("+ $accent = '#fff';")

    def test_inline_comment_counts_as_docblock_change(self):
        assert _mod._has_docblock_changes("+ $total = $subtotal; // recalculated later")
        assert _mod._has_docblock_changes("+// guard against double capture")
        assert _mod._has_docblock_changes("+# fallback for legacy configs")

    def test_url_is_not_an_inline_comment(self):
        assert not _mod._has_docblock_changes("+ $url = 'https://example.com/docs';")

    def test_linter_directives_are_not_comment_signals(self):
        """phpcs/eslint directives are machine instructions, not docs —
        the 55669 diff's phpcs:ignore swap must not dispatch clarity."""
        assert not _mod._has_docblock_changes(
            "+ // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped"
        )
        assert not _mod._has_docblock_changes("+ // eslint-disable-next-line no-console")
        assert not _mod._has_docblock_changes("+ x = f()  # noqa: E501")

    def test_large_pr_check_fires_on_500_lines(self):
        config = {
            "dispatch_class": "conditional",
            "domain": "architecture",
            "triage_checks": ["large_pr"],
        }
        status, reason, _signal = triage_conditional_agent(
            "architecture-reviewer", config,
            ["src/Checkout.php"],
            "",
            {
                "added": 520, "removed": 30,
                "deleted_files": [], "renamed_files": [], "added_files": [],
                "file_stats": {"src/Checkout.php": {"added": 520, "removed": 30}},
            },
        )
        assert status == "DISPATCH"
        assert "550 lines" in reason  # added + removed in scope


class TestRegistryKeywordHygiene:
    """Triage keyword lists must not contain language-structural terms that
    match virtually any commit (they defeat evidence-based dispatch)."""

    BANNED_GENERIC_KEYWORDS = {
        "function", "class", "method", "interface", "enum",
        "option", "feature", "remove", "delete",
    }

    def test_no_agent_uses_generic_structural_keywords(self, agents):
        offenders = {}
        for name, config in agents.items():
            hits = self.BANNED_GENERIC_KEYWORDS & {
                kw.strip() for kw in config.get("triage_keywords", [])
            }
            if hits:
                offenders[name] = sorted(hits)
        assert offenders == {}, (
            f"Generic keywords make conditional agents de-facto always-dispatch: {offenders}"
        )

    def test_prefix_keywords_declare_a_stem_of_at_least_four_characters(self, agents):
        """A one-letter stem with a star is a wildcard, not a keyword."""
        short = {
            name: [kw for kw in config.get("triage_keywords", [])
                   if _mod._is_prefix_keyword(kw) and len(kw.rstrip("*").strip()) < 4]
            for name, config in agents.items()
        }
        assert {k: v for k, v in short.items() if v} == {}

    def test_every_star_is_a_prefix_marker_or_the_docblock_opener(self, agents):
        """A star after a non-alphanumeric character is a literal, so an
        entry such as `wp_*` would match only the text "wp_*" and pass the
        stem-length hygiene above unseen. Only `/**` earns a literal star."""
        literal_stars = {
            name: [kw for kw in config.get("triage_keywords", [])
                   if kw.endswith("*") and not _mod._is_prefix_keyword(kw) and kw != "/**"]
            for name, config in agents.items()
        }
        assert {k: v for k, v in literal_stars.items() if v} == {}

    def test_no_bare_truncated_stems_remain(self, agents):
        """The stems the prefix era relied on must now carry their star;
        a bare stem is a whole word that matches nothing."""
        stems = {"sanitiz", "escap", "optimiz", "accessib", "deprecat",
                 "restructur", "idempoten", "schedul", "cach", "migrat",
                 "architect", "inject", "decoupl", "consolidat", "concurren"}
        offenders = {
            name: sorted(stems & set(config.get("triage_keywords", [])))
            for name, config in agents.items()
        }
        assert {k: v for k, v in offenders.items() if v} == {}


# =============================================================================
# Repo-contributed reviewer expansion
# =============================================================================

def _review_ctx(reviewers):
    return {"review_config": {"rules": [], "reviewers": reviewers}}


class TestRepoReviewerExpansion:
    """expand_repo_reviewers turns declared reviewers into adapter dispatches."""

    def test_no_review_config_yields_nothing(self):
        dispatch = []
        signals, warnings = expand_repo_reviewers(None, {}, [], dispatch)
        assert dispatch == []
        assert signals == []
        assert warnings == []

    def test_applicable_reviewer_dispatches(self):
        dispatch = []
        rev = {"id": "renewals", "label": "Renewals Expert",
               "ref": ".ai/agents/review/renewals.md",
               "applies_to": {"domains": ["security"]},
               "channel": "blocking", "execution": "inline", "model": "sonnet"}
        signals, _warnings = expand_repo_reviewers(
            _review_ctx([rev]), {"security": 3}, ["includes/foo.php"], dispatch
        )
        assert len(dispatch) == 1
        e = dispatch[0]
        assert e["name"] == "repo-renewals-reviewer"  # -reviewer suffix load-bearing
        assert e["adapter"] == "repo-reviewer-adapter"
        assert e["ref"] == ".ai/agents/review/renewals.md"
        assert e["channel"] == "blocking"
        assert e["execution"] == "inline"
        assert e["model"] == "sonnet"
        assert e["scope_domains"] == ["security"]
        assert e["include_paths"] == []
        assert e["status"] == "DISPATCH"
        assert any("repo-renewals-reviewer: STATUS=DISPATCH" in s for s in signals)

    def test_inapplicable_reviewer_skipped(self):
        dispatch = []
        rev = {"id": "switch", "label": "Switch", "ref": "r.md",
               "applies_to": {"domains": ["performance"]}, "channel": "blocking",
               "execution": "inline", "model": None}
        expand_repo_reviewers(_review_ctx([rev]), {"security": 2}, ["a.php"], dispatch)
        assert dispatch[0]["status"] == "SKIPPED_TRIAGE"

    def test_path_glob_dispatches(self):
        dispatch = []
        rev = {"id": "sw", "label": "Sw", "ref": "r.md",
               "applies_to": {"paths": ["includes/**"]}, "channel": "advisory",
               "execution": "inline", "model": None}
        expand_repo_reviewers(_review_ctx([rev]), {}, ["includes/core/x.php"], dispatch)
        assert dispatch[0]["status"] == "DISPATCH"
        assert dispatch[0]["channel"] == "advisory"

    def test_resolved_ref_is_preferred_over_root_relative_ref(self):
        """Bootstrap resolves a relative ref against its own invocation
        directory — from a repo subdirectory the valid prompt would report
        missing and the adapter would write an empty result. The dispatch
        entry must carry the already-validated absolute path."""
        dispatch = []
        rev = {"id": "renewals", "label": "R", "ref": ".ai/r.md",
               "resolved_ref": "/repo/.ai/r.md",
               "applies_to": {"domains": ["security"]},
               "channel": "blocking", "execution": "inline", "model": None}
        expand_repo_reviewers(
            _review_ctx([rev]), {"security": 1}, ["a.php"], dispatch
        )
        assert dispatch[0]["ref"] == "/repo/.ai/r.md"

    def test_isolated_execution_is_refused_not_dispatched(self):
        """An explicit isolation request must never silently widen into
        inline execution of the repo prompt."""
        dispatch = []
        rev = {"id": "iso", "label": "Iso", "ref": "r.md",
               "applies_to": {"domains": ["security"]},
               "channel": "blocking", "execution": "isolated", "model": None}
        expand_repo_reviewers(
            _review_ctx([rev]), {"security": 1}, ["a.php"], dispatch
        )
        assert dispatch[0]["status"] == "SKIPPED"
        assert "isolated execution is not implemented" in dispatch[0]["reason"]

    def test_untrusted_exclusions_surface_as_warnings(self):
        """Provenance-gated entries are hard-excluded at config
        normalization, so nothing remains to dispatch — the exclusion must
        still be LOUD, and warnings are the only channel the step-5
        briefing renders (agent_signals never reach the orchestrator)."""
        ctx = {"review_config": {
            "rules": [], "reviewers": [],
            "untrusted": [{
                "kind": "reviewer", "id": "evil", "path": ".ai/evil.md",
                "reason": "defined or modified within the reviewed range",
            }],
        }}
        dispatch = []
        signals, warnings = expand_repo_reviewers(ctx, {}, [], dispatch)
        assert dispatch == []
        assert signals == []
        assert any(
            "UNTRUSTED reviewer 'evil'" in warning for warning in warnings
        )

    def test_untrusted_warnings_reach_the_rendered_plan_warnings(self, registry):
        """build_dispatch_plan must carry provenance exclusions in its
        ``warnings`` array — the field pipeline step 5 renders with ⚠️."""
        ctx = {"review_config": {
            "rules": [], "reviewers": [],
            "untrusted": [{
                "kind": "config", "id": None, "path": ".pirategoat/config.json",
                "reason": "defined or modified within the reviewed range",
            }],
        }}
        plan = build_dispatch_plan(
            mode="pr", git_range="base..head", output_dir="/tmp/out",
            changed_files=["includes/foo.php"], registry=registry,
            commit_messages="", diffstat={}, review_context=ctx,
        )
        assert any("UNTRUSTED config" in w for w in plan["warnings"])
        assert not any("UNTRUSTED" in s for s in plan["agent_signals"])

    def test_declared_path_globs_ride_on_the_plan_row(self):
        """The globs bootstrap passes as `--include-path` are the plan row's
        `include_paths`, so the orphan measurement sees the same scope."""
        dispatch = []
        rev = {"id": "contracts", "ref": ".ai/agents/review/contracts.md",
               "applies_to": {"paths": ["contracts/*.contract"]}}
        expand_repo_reviewers(_review_ctx([rev]), {}, ["contracts/payment.contract"], dispatch)
        assert dispatch[0]["status"] == "DISPATCH"
        assert dispatch[0]["include_paths"] == ["contracts/*.contract"]

    def test_registry_rows_carry_their_declared_scope(self, registry):
        """A plan row states the agent's scope — primary plus secondary
        domains — so a reader of the plan (dispatch_adjust's orphan
        measurement) needs no registry."""
        plan = build_dispatch_plan(
            mode="full", git_range="main..HEAD", output_dir="/tmp/test",
            changed_files=["Dockerfile", "src/a.php"], registry=registry,
        )
        rows = {a["name"]: a for a in plan["agents"]}
        assert rows["security-reviewer"]["scope_domains"] == ["security", "config-ops"]
        assert rows["code-reviewer"]["scope_domains"] == ["code"]

    def test_scope_domains_fallback_to_code(self):
        dispatch = []
        rev = {"id": "any", "label": "Any", "ref": "r.md",
               "applies_to": {"paths": ["**/*.php"]}, "channel": "blocking",
               "execution": "inline", "model": None}
        expand_repo_reviewers(_review_ctx([rev]), {}, ["x.php"], dispatch)
        assert dispatch[0]["scope_domains"] == ["code"]

    def test_integration_through_build_dispatch_plan(self, registry):
        rev = {"id": "domain-expert", "label": "Domain Expert",
               "ref": ".ai/agents/review/expert.md",
               "applies_to": {"paths": ["**/*.php"]}, "channel": "blocking",
               "execution": "inline", "model": "sonnet"}
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test",
            changed_files=["includes/foo.php"],
            registry=registry,
            review_context=_review_ctx([rev]),
        )
        names = [a["name"] for a in plan["agents"]]
        assert "repo-domain-expert-reviewer" in names
        entry = next(a for a in plan["agents"] if a["name"] == "repo-domain-expert-reviewer")
        assert entry["adapter"] == "repo-reviewer-adapter"
        assert entry["status"] == "DISPATCH"

    def test_codex_host_projects_effective_model_tier(self, registry, tmp_path):
        rev = {"id": "domain-expert", "label": "Domain Expert",
               "ref": ".ai/agents/review/expert.md",
               "applies_to": {"paths": ["**/*.php"]}, "channel": "blocking",
               "execution": "inline", "model": "opus"}
        plan = build_dispatch_plan(
            mode="full",
            git_range="abc..HEAD",
            output_dir=str(tmp_path),
            changed_files=["src/x.php"],
            registry=registry,
            commit_messages="",
            diffstat={},
            review_context=_review_ctx([rev]),
            host="codex",
        )

        entry = next(
            agent for agent in plan["agents"]
            if agent.get("adapter") == "repo-reviewer-adapter"
        )
        assert entry["model"] == "inherit"
        assert entry["declared_model"] == "opus"

    def test_claude_host_keeps_declared_model_tier(self, registry, tmp_path):
        rev = {"id": "domain-expert", "label": "Domain Expert",
               "ref": ".ai/agents/review/expert.md",
               "applies_to": {"paths": ["**/*.php"]}, "channel": "blocking",
               "execution": "inline", "model": "opus"}
        plan = build_dispatch_plan(
            mode="full",
            git_range="abc..HEAD",
            output_dir=str(tmp_path),
            changed_files=["src/x.php"],
            registry=registry,
            commit_messages="",
            diffstat={},
            review_context=_review_ctx([rev]),
            host="claude",
        )

        entry = next(
            agent for agent in plan["agents"]
            if agent.get("adapter") == "repo-reviewer-adapter"
        )
        assert entry["model"] == "opus"
        assert entry["declared_model"] == "opus"


class TestDispatchSignals:
    """The planner names why it decided, from the code path that decided."""

    _CONFIG = {"domain": "security", "triage_keywords": ["nonce"], "triage_checks": []}

    @pytest.mark.parametrize("config, files, text, diffstat, kwargs, expected", [
        pytest.param(_CONFIG, ["tests/test_a.php"], "", {}, {}, "test_only", id="test_only"),
        pytest.param({**_CONFIG, "min_added_lines": 5}, ["src/a.php"], "", {"added": 0, "removed": 0}, {}, "min_added_lines", id="min_added_lines"),
        pytest.param({**_CONFIG, "require_php_source_file": True}, ["src/a.js"], "", {}, {}, "source_gate", id="source_gate"),
        pytest.param(_CONFIG, ["src/a.php"], "add nonce check", {}, {}, "keyword", id="keyword"),
        pytest.param({**_CONFIG, "triage_repository_keywords": ["woocommerce"]}, ["src/a.php"], "tidy", {}, {"diff_text": "", "repository_text": "woocommerce/woocommerce"}, "repository_keyword", id="repository_keyword"),
        pytest.param(_CONFIG, ["src/a.php"], "tidy", {}, {"diff_text": None}, "diff_unavailable", id="diff_unavailable"),
        pytest.param({**_CONFIG, "require_triage_keyword_match": True}, ["src/a.php"], "tidy", {}, {"diff_text": ""}, "evidence_gate", id="evidence_gate"),
        pytest.param(_CONFIG, ["src/a.php"], "tidy", {}, {"diff_text": ""}, "default", id="default"),
    ])
    def test_every_triage_layer_emits_its_own_signal(self, config, files, text, diffstat, kwargs, expected):
        status, reason, signal = triage_conditional_agent("security-reviewer", config, files, text, diffstat, **kwargs)
        assert signal == expected, (status, reason)

    def test_a_triage_check_emits_the_check_signal(self, registry):
        from review.dispatch_status import SIGNAL_CHECK
        dead = registry["agents"]["dead-code-reviewer"]
        status, reason, signal = triage_conditional_agent(
            "dead-code-reviewer", dead, ["src/a.php"], "",
            {"deleted_files": ["src/old.php"], "renamed_files": [], "added": 0, "removed": 40},
        )
        assert signal == SIGNAL_CHECK, reason

    def test_decide_agent_dispatch_falls_back_to_the_default_signal(self):
        from review.dispatch_status import SIGNAL_DEFAULT
        status, reason, signal = decide_agent_dispatch("x-reviewer", {"domain": "code", "dispatch_class": "special"}, {"code": 3})
        assert (reason, signal) == ("default", SIGNAL_DEFAULT)

    def test_repo_reviewers_carry_their_own_signals(self):
        from review.dispatch_status import SIGNAL_NO_DOMAIN_FILES, SIGNAL_REPO_REVIEWER
        dispatch = []
        expand_repo_reviewers(
            _review_ctx([
                {"id": "foo", "prompt": "review.md", "applies_to": {"domains": ["php"]}},
                {"id": "bar", "prompt": "review.md", "applies_to": {"domains": ["go"]}},
            ]),
            {"php": 2, "go": 0}, ["src/a.php"], dispatch,
        )
        signals = {entry["name"]: entry["signal"] for entry in dispatch}
        assert signals == {"repo-foo-reviewer": SIGNAL_REPO_REVIEWER, "repo-bar-reviewer": SIGNAL_NO_DOMAIN_FILES}

    def test_quick_mode_keys_on_the_signal_not_the_reason_prose(self, registry, tmp_path):
        from review.dispatch_status import SIGNAL_QUICK_MODE, SKIPPED_QUICK_MODE

        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir=str(tmp_path),
            changed_files=["src/Controller.php"],
            registry=registry,
            commit_messages="refine controller flow",
            diffstat={
                "added": 20,
                "removed": 5,
                "deleted_files": [],
                "renamed_files": [],
                "file_stats": {"src/Controller.php": {"added": 20, "removed": 5}},
            },
            quick=True,
        )
        skipped = [a for a in plan["agents"] if a["status"] == SKIPPED_QUICK_MODE]
        assert skipped and all(a["signal"] == SIGNAL_QUICK_MODE for a in skipped)
        assert all("signal" in a and a["signal"] for a in plan["agents"])

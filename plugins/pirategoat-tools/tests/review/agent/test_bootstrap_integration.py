"""Tests for review/agent/bootstrap.py — integration tests (subprocess runs against all agents)."""

from concurrent.futures import ThreadPoolExecutor
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent.parent  # agent/ -> review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
BOOTSTRAP_SCRIPT = SCRIPTS_DIR / "review" / "agent" / "bootstrap.py"

sys.path.insert(0, str(SCRIPTS_DIR))

from review.agent.output import ReviewOutputBuilder

# Import AGENT_CONFIG to derive ALL_AGENTS
_spec = importlib.util.spec_from_file_location("bootstrap_reviewer", str(BOOTSTRAP_SCRIPT))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
AGENT_CONFIG = _mod.AGENT_CONFIG
build_output = _mod.build_output
derive_reviewer_name = _mod.derive_reviewer_name
extract_scope_files = _mod.extract_scope_files
extract_not_diffed_files = _mod.extract_not_diffed_files
extract_list_only_files = _mod.extract_list_only_files

ALL_AGENTS = sorted(AGENT_CONFIG.keys())

# ---------------------------------------------------------------------------
# Temp repo for integration tests (created once, reused across all tests)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(TESTS_DIR))
from conftest import setup_temp_git_repo

_fixture_repo_cache: dict = {}


def _get_fixture_repo(fixture: str = "multi-file-realistic.diff") -> str:
    """Lazily create a temp git repo from the given fixture diff."""
    if fixture not in _fixture_repo_cache:
        diff = str(TESTS_DIR / "fixtures" / fixture)
        _fixture_repo_cache[fixture] = setup_temp_git_repo(diff)
    return _fixture_repo_cache[fixture]


def run_bootstrap(*args: str, timeout: int = 60, fixture: str = "multi-file-realistic.diff") -> subprocess.CompletedProcess:
    """Run review/agent/bootstrap.py via subprocess against a temp git repo.

    Uses a temp repo from the specified fixture diff so tests are fully
    isolated from the real repository state. Always passes
    --range HEAD~1..HEAD for deterministic behavior.
    """
    full_args = list(args)
    if "--range" not in full_args:
        full_args.extend(["--range", "HEAD~1..HEAD"])
    cmd = [sys.executable, str(BOOTSTRAP_SCRIPT)] + full_args
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        cwd=_get_fixture_repo(fixture),
    )


class TestCategoryRepresentatives:
    """One integration test per agent category.

    Each test runs the full subprocess chain for one representative agent
    and verifies section structure, conditional sections, personalization,
    and budget — all in one comprehensive assertion set.

    This replaces the previous pattern of parameterizing every assertion
    over ALL_AGENTS. Each category covers a distinct conditional path
    through main(). If an assertion holds for one agent in the category,
    it holds for all agents in that category (same code path).
    """

    def test_standard_agent(self, tmp_path):
        """Standard conditional agent with no special flags (performance-reviewer)."""
        result = run_bootstrap("--agent", "performance-reviewer", "--output-dir", str(tmp_path))
        stdout = result.stdout
        assert result.returncode == 0

        # Section structure (hardcoded in build_output template)
        assert "=== BOOTSTRAP: performance-reviewer ===" in stdout
        assert "--- Section 1: REVIEW RULES" in stdout
        assert "=== REVIEW RULES ===" in stdout
        assert "--- Section 2: REVIEW CONTENT" in stdout
        assert "--- Section 3: OUTPUT INSTRUCTIONS" in stdout
        assert "=== OUTPUT INSTRUCTIONS ===" in stdout

        # Personalization
        assert "REVIEWER_NAME: performance" in stdout
        assert f"{tmp_path}/performance-review.json" in stdout
        assert f"{tmp_path}/performance-review.md" not in stdout
        assert "PIRATEGOAT_REVIEWER_NAME=performance" in stdout

        # Budget present with hard ceiling
        assert "=== REVIEW BUDGET ===" in stdout
        assert "Target: ~" in stdout
        assert "Hard ceiling:" in stdout
        assert "STOP exploring" in stdout

        # Conditional sections absent for standard agents
        assert "=== DOMAIN RULES ===" not in stdout
        assert "=== EXPLORATION SCOPE ===" not in stdout
        assert "=== FILE HISTORY ===" not in stdout

        # REVIEW SCOPE header not duplicated
        assert stdout.count("=== REVIEW SCOPE ===") <= 1

    def test_agent_start_telemetry_uses_the_already_parsed_scope_paths(
        self, tmp_path
    ):
        telemetry_log = tmp_path / "review.jsonl"
        telemetry_log.write_text(json.dumps({
            "schema": 1,
            "run_id": "run-1",
            "event": "pipeline_start",
            "pipeline": {"repo_path": _get_fixture_repo()},
        }) + "\n")
        (tmp_path / ".telemetry-log-path").write_text(str(telemetry_log))

        result = run_bootstrap(
            "--agent", "performance-reviewer", "--output-dir", str(tmp_path)
        )

        assert result.returncode == 0
        # Telemetry scope covers the full in-scope set: inline FILES entries,
        # deferred NOT DIFFED paths (in-scope work whose diffs were withheld
        # for context budget), and list-only CHANGED (no diff) paths the
        # reviewer is told to inspect when relevant.
        expected_scope = sorted(set(
            extract_scope_files(result.stdout)
            + extract_not_diffed_files(result.stdout)
            + extract_list_only_files(result.stdout)
        ))
        events = [json.loads(line) for line in telemetry_log.read_text().splitlines()]
        agent_start = next(
            event for event in events if event.get("event") == "agent_start"
        )
        assert expected_scope
        assert agent_start["scope"]["paths"] == expected_scope

    def test_ref_mode_instance_writes_scope_summaries_and_sidecars(
        self, tmp_path, monkeypatch
    ):
        """Adapter ref-mode instances must leave the same per-agent scope
        evidence as native reviewers — instance-named scope summaries (so
        run-level coverage reconciliation sees adapter scopes) and an
        instance-named deferred sidecar (so the builder's declaration
        verification finds it via PIRATEGOAT_REVIEWER_NAME)."""
        ref = tmp_path / "renewals.md"
        ref.write_text("Review renewals logic end to end.")

        result = run_bootstrap(
            "--agent", "repo-reviewer-adapter",
            "--repo-agent-ref", str(ref),
            "--instance-name", "repo-renewals-reviewer",
            "--channel", "advisory",
            "--scope-domains", "code",
            "--output-dir", str(tmp_path),
        )

        assert result.returncode == 0
        summary = tmp_path / "repo-renewals-reviewer-scope-summary-code.json"
        assert summary.is_file()
        data = json.loads(summary.read_text())
        assert data["domain"] == "code"
        assert isinstance(data["in_scope_stat_lines"], int)
        # Identity chain: sidecar name matches what the builder derives
        # from PIRATEGOAT_REVIEWER_NAME.
        assert "PIRATEGOAT_REVIEWER_NAME=repo-renewals" in result.stdout
        assert (tmp_path / "repo-renewals-deferred-files.json").is_file()
        entitlement = json.loads(
            (tmp_path / "repo-renewals-advisory-entitlement.json").read_text()
        )
        assert entitlement == {"schema": 1, "advisory_entitled": True}

        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "repo-renewals")
        builder = ReviewOutputBuilder(pr_id="1", reviewer="repo-renewals")
        builder.add_issue(
            severity="critical", title="Advisory", file="src/app.py",
            description="d", recommendation="r", line=1,
            channel="advisory",
        )
        assert builder.to_dict()["verdict"] == "approve"

    def test_ref_mode_scope_failure_is_an_error_not_a_clean_exit(
        self, tmp_path
    ):
        """When every declared ref-mode domain fails scope discovery (bad
        range, git error, timeout), the adapter must report the
        infrastructure failure — a NO_DOMAIN_FILES exit would let the repo
        reviewer emit a clean not-applicable result for a run that never
        inspected anything."""
        ref = tmp_path / "renewals.md"
        ref.write_text("Review renewals logic end to end.")

        result = run_bootstrap(
            "--agent", "repo-reviewer-adapter",
            "--repo-agent-ref", str(ref),
            "--instance-name", "repo-renewals-reviewer",
            "--scope-domains", "code",
            "--output-dir", str(tmp_path),
            "--range", "no-such-ref..HEAD",
        )

        assert result.returncode == 1
        assert "STATUS: ERROR" in result.stdout
        assert "No files matched" not in result.stdout

    def test_ref_mode_agent_start_records_the_dispatched_model_tier(
        self, tmp_path
    ):
        """A repo reviewer dispatched with an explicit model override must
        log that tier — the static adapter tier ("inherit") would make the
        durable manifest report conflicting models for one agent identity
        (the dispatch projection carries the override)."""
        telemetry_log = tmp_path / "review.jsonl"
        telemetry_log.write_text(json.dumps({
            "schema": 1,
            "run_id": "run-1",
            "event": "pipeline_start",
            "pipeline": {"repo_path": _get_fixture_repo()},
        }) + "\n")
        (tmp_path / ".telemetry-log-path").write_text(str(telemetry_log))
        ref = tmp_path / "renewals.md"
        ref.write_text("Review renewals logic end to end.")

        result = run_bootstrap(
            "--agent", "repo-reviewer-adapter",
            "--repo-agent-ref", str(ref),
            "--instance-name", "repo-renewals-reviewer",
            "--scope-domains", "code",
            "--model-tier", "opus",
            "--output-dir", str(tmp_path),
        )

        assert result.returncode == 0
        events = [
            json.loads(line)
            for line in telemetry_log.read_text().splitlines()
        ]
        agent_start = next(
            event for event in events if event.get("event") == "agent_start"
        )
        assert agent_start["agent"] == "repo-renewals-reviewer"
        assert agent_start["model_tier"] == "opus"

    def test_native_agent_start_keeps_the_registry_model_tier(self, tmp_path):
        """Outside ref-mode the registry is the single source of truth for
        the tier — a stray --model-tier flag must not override it."""
        telemetry_log = tmp_path / "review.jsonl"
        telemetry_log.write_text(json.dumps({
            "schema": 1,
            "run_id": "run-1",
            "event": "pipeline_start",
            "pipeline": {"repo_path": _get_fixture_repo()},
        }) + "\n")
        (tmp_path / ".telemetry-log-path").write_text(str(telemetry_log))

        result = run_bootstrap(
            "--agent", "performance-reviewer",
            "--model-tier", "opus",
            "--output-dir", str(tmp_path),
        )

        assert result.returncode == 0
        events = [
            json.loads(line)
            for line in telemetry_log.read_text().splitlines()
        ]
        agent_start = next(
            event for event in events if event.get("event") == "agent_start"
        )
        assert agent_start["model_tier"] == "sonnet"

    def test_deferred_sidecar_backs_add_unreviewed_validation(self, tmp_path):
        """Bootstrap persists the authoritative NOT DIFFED set so the
        builder can reject declarations that match no deferred file."""
        result = run_bootstrap(
            "--agent", "performance-reviewer", "--output-dir", str(tmp_path)
        )
        assert result.returncode == 0
        sidecar = tmp_path / "performance-deferred-files.json"
        assert sidecar.is_file()
        data = json.loads(sidecar.read_text())
        assert sorted(data["deferred_files"]) == sorted(
            extract_not_diffed_files(result.stdout)
        )
        # Closes the main()->build_output() seam: not_diffed_count must be
        # derived from this exact deferred set, not a neighboring fact
        # (e.g. total scope files) that also happens to be non-empty here.
        # A mis-wired count would pass every other assertion in this suite.
        assert ("Not reviewed (budget):" in result.stdout) == bool(
            data["deferred_files"]
        )

    def test_test_agent(self, tmp_path):
        """Test-reviewer agent gets DOMAIN RULES (php-tests-reviewer)."""
        result = run_bootstrap("--agent", "php-tests-reviewer", "--output-dir", str(tmp_path))
        stdout = result.stdout
        assert result.returncode == 0

        # Test-agent-specific: DOMAIN RULES present
        assert "=== DOMAIN RULES ===" in stdout

        # Standard structure still present
        assert "=== REVIEW RULES ===" in stdout
        assert "=== REVIEW BUDGET ===" in stdout
        assert "REVIEWER_NAME: php-tests" in stdout
        assert f"{tmp_path}/php-tests-review.json" in stdout
        assert "PIRATEGOAT_REVIEWER_NAME=php-tests" in stdout

        # Other conditional sections absent
        assert "=== EXPLORATION SCOPE ===" not in stdout

    def test_exploration_agent(self, tmp_path):
        """patterns-reviewer gets EXPLORATION SCOPE + no_semantic_filter (patterns-reviewer)."""
        result = run_bootstrap("--agent", "patterns-reviewer", "--output-dir", str(tmp_path))
        stdout = result.stdout
        assert result.returncode == 0

        # Exploration-specific: EXPLORATION SCOPE present
        assert "=== EXPLORATION SCOPE ===" in stdout

        # Personalization
        assert "REVIEWER_NAME: patterns" in stdout
        assert "PIRATEGOAT_REVIEWER_NAME=patterns" in stdout

        # Not a test agent — no DOMAIN RULES
        assert "=== DOMAIN RULES ===" not in stdout

    def test_null_domain_agent(self, tmp_path):
        """Null-domain agent skips scope discovery (tests-mutation-reviewer)."""
        result = run_bootstrap("--agent", "tests-mutation-reviewer", "--output-dir", str(tmp_path))
        stdout = result.stdout
        assert result.returncode == 0

        # Null-domain-specific: no scope discovery
        assert "No scope discovery" in stdout

        # tests-mutation-reviewer has protocols=["reviewer"], NOT "tests-reviewer"
        assert "=== DOMAIN RULES ===" not in stdout

        # Personalization still works
        assert "REVIEWER_NAME: tests-mutation" in stdout
        assert "PIRATEGOAT_REVIEWER_NAME=tests-mutation" in stdout

    def test_secondary_domains_agent(self, tmp_path):
        """Agent with secondary_domains gets SECONDARY SCOPE (security-reviewer).

        Uses php-with-ci-config fixture which has both security-domain files
        (PHP) and config-ops files (CI YAML), so the secondary scope append
        branch is exercised.
        """
        result = run_bootstrap(
            "--agent", "security-reviewer", "--output-dir", str(tmp_path),
            fixture="php-with-ci-config.diff",
        )
        stdout = result.stdout
        assert result.returncode == 0

        # Secondary domains: config-ops scope appended
        assert "=== SECONDARY SCOPE: config-ops ===" in stdout

        # Standard structure still present
        assert "=== REVIEW RULES ===" in stdout
        assert "REVIEWER_NAME: security" in stdout

    def test_history_and_budget_override_agent(self, tmp_path):
        """history-insights-reviewer gets FILE HISTORY + budget override."""
        result = run_bootstrap("--agent", "history-insights-reviewer", "--output-dir", str(tmp_path))
        stdout = result.stdout
        assert result.returncode == 0

        # History-specific: FILE HISTORY section present
        assert "=== FILE HISTORY ===" in stdout

        # Budget override: fixed value 45 (from registry), not scope-computed
        assert "Target: ~45 tool calls" in stdout

        # Personalization
        assert "REVIEWER_NAME: history-insights" in stdout

    def test_file_history_without_budget_override(self, tmp_path):
        """api-contract-reviewer gets FILE HISTORY but uses scope-computed budget."""
        result = run_bootstrap("--agent", "api-contract-reviewer", "--output-dir", str(tmp_path))
        stdout = result.stdout
        assert result.returncode == 0

        # file_history present
        assert "=== FILE HISTORY ===" in stdout

        # No budget override — uses scope-computed value (not 45)
        assert "Target: ~45 tool calls" not in stdout
        assert "Target: ~" in stdout

        # Personalization
        assert "REVIEWER_NAME: api-contract" in stdout


class TestArchitecturalInvariants:
    """Cross-agent properties that must hold.

    These test real architectural contracts, not template strings.
    Uses 3 representative agents (standard, test, special) — sufficient
    to verify determinism without running all 21.
    """

    _REPRESENTATIVE_AGENTS = ["performance-reviewer", "php-tests-reviewer", "patterns-reviewer"]

    @staticmethod
    def _extract_section(text: str, start_marker: str, *end_markers: str) -> str:
        """Extract text between start_marker and the earliest end_marker."""
        start = text.find(start_marker)
        if start == -1:
            return ""
        end = len(text)
        for marker in end_markers:
            pos = text.find(marker, start + len(start_marker))
            if pos != -1 and pos < end:
                end = pos
        return text[start:end].strip()

    def test_review_rules_identical_across_categories(self, tmp_path):
        """REVIEW RULES (shared protocol) must be identical for all agent categories.

        The protocol extraction uses the same file + same skip-list for every agent.
        If it produces different results, something is wrong with the extraction logic
        or the protocol file has agent-conditional content (which it should not).
        """
        rules = {}
        for agent in self._REPRESENTATIVE_AGENTS:
            result = run_bootstrap("--agent", agent, "--output-dir", str(tmp_path))
            rules[agent] = self._extract_section(
                result.stdout, "=== REVIEW RULES ===",
                "=== DOMAIN RULES ===", "=== REVIEW BUDGET ===", "--- Section 2:",
            )

        reference = rules[self._REPRESENTATIVE_AGENTS[0]]
        assert reference, "REVIEW RULES section should not be empty"
        for agent in self._REPRESENTATIVE_AGENTS[1:]:
            assert rules[agent] == reference, (
                f"REVIEW RULES differ between {self._REPRESENTATIVE_AGENTS[0]} and {agent}"
            )

    def test_shared_rules_bound_recursive_filesystem_discovery(self, tmp_path):
        """The bounded-discovery protocol section must reach generated prompts.

        Diffs the section body against source instead of pinning prose, so
        rewording the protocol doesn't break the test — only dropping the
        section (or the prompt path losing it) does.
        """
        protocol = (PLUGIN_ROOT / "agents/shared/reviewer-protocol.md").read_text()
        section = self._extract_section(
            protocol, "### Bounded Filesystem Discovery", "\n## ", "\n### ",
        )
        assert section, "protocol must define a Bounded Filesystem Discovery section"

        review_rules = _mod.extract_protocol_sections(
            protocol,
            _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS,
        )
        prompt = build_output(
            agent_name="code-reviewer",
            plugin_root=str(PLUGIN_ROOT),
            status="OK",
            review_rules=review_rules,
            domain_rules=None,
            scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number=None,
            reviewer_name="code",
            not_diffed_count=0,
            has_php=False,
        )

        assert section in prompt

    def test_domain_rules_identical_across_test_agents(self, tmp_path):
        """DOMAIN RULES (tests-reviewer protocol) must be identical for all test agents.

        All 4 test agents (php, js, e2e, go) must produce the same DOMAIN RULES.
        A registry drift removing tests-reviewer from any agent would be caught here.
        """
        agents = ["php-tests-reviewer", "js-tests-reviewer", "e2e-tests-reviewer", "go-tests-reviewer"]
        rules = {}
        for agent in agents:
            result = run_bootstrap("--agent", agent, "--output-dir", str(tmp_path))
            rules[agent] = self._extract_section(
                result.stdout, "=== DOMAIN RULES ===",
                "=== REVIEW BUDGET ===", "--- Section 2:",
            )

        reference = rules[agents[0]]
        assert reference, "DOMAIN RULES section should not be empty"
        for agent in agents[1:]:
            assert rules[agent] == reference, (
                f"DOMAIN RULES differ between {agents[0]} and {agent}"
            )


class TestCanonicalExecutableBuilderSource:
    """Bootstrap is the sole executable ReviewOutputBuilder command source."""

    def test_protocol_is_reference_only_and_bootstrap_emits_one_builder_command(
        self, tmp_path
    ):
        protocol = (PLUGIN_ROOT / "agents/shared/reviewer-protocol.md").read_text()
        review_rules = _mod.extract_protocol_sections(
            protocol,
            _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS,
        )
        prompt = build_output(
            agent_name="security-reviewer",
            plugin_root=str(PLUGIN_ROOT),
            status="OK",
            review_rules=review_rules,
            domain_rules=None,
            scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number="42",
            reviewer_name="security",
            not_diffed_count=0,
            has_php=False,
        )

        assert "python3 <<'PY'" not in protocol
        for shell_variable in (
            "PIRATEGOAT_PLUGIN_ROOT=",
            "PIRATEGOAT_OUTPUT_DIR=",
            "PIRATEGOAT_REVIEWER_NAME=",
            "PIRATEGOAT_PR_ID=",
        ):
            assert shell_variable not in protocol
        assert prompt.count("python3 <<'PY'") == 1
        assert f"PIRATEGOAT_PLUGIN_ROOT={PLUGIN_ROOT}" in prompt
        assert f"PIRATEGOAT_OUTPUT_DIR={tmp_path}" in prompt
        assert "PIRATEGOAT_REVIEWER_NAME=security" in prompt
        assert "PIRATEGOAT_PR_ID=42" in prompt
        assert (
            "builder = ReviewOutputBuilder(pr_id=pr_id, reviewer=reviewer_name)"
            in prompt
        )
        assert "result = builder.save(output_dir)" in prompt
        assert "MUST NOT create or write a temporary builder script" in prompt
        assert "generic filenames collide" in prompt
        assert "RECORDED COUNTS" in prompt
        assert "Return signal format:" in prompt
        assert "STATUS: FINISHED" in prompt
        assert f"{tmp_path}/security-review.json" in prompt
        assert f"{tmp_path}/security-review.md" not in prompt

    def test_envelope_carries_the_plugin_version_assignment(self, tmp_path):
        """The producing plugin version travels in the same envelope.

        Emitted unconditionally, empty when unresolved, so the envelope
        keeps a constant five-assignment shape — the transcript analyzers
        recognize the builder command by exactly that shape.
        """
        (tmp_path / "run-config.json").write_text(
            json.dumps({"mode": "pr", "plugin_version": "1.114.0"})
        )
        prompt = build_output(
            agent_name="security-reviewer",
            plugin_root=str(PLUGIN_ROOT),
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number="42",
            reviewer_name="security",
            not_diffed_count=0,
            has_php=False,
            plugin_version="1.114.0",
        )
        assert "PIRATEGOAT_PLUGIN_VERSION=1.114.0" in prompt

    def test_envelope_keeps_the_assignment_when_the_version_is_unknown(
        self, tmp_path
    ):
        prompt = build_output(
            agent_name="security-reviewer",
            plugin_root=str(PLUGIN_ROOT),
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number="42",
            reviewer_name="security",
            not_diffed_count=0,
            has_php=False,
        )
        assert "PIRATEGOAT_PLUGIN_VERSION=''" in prompt

    def test_main_reads_the_version_from_the_run_config_stamp(self, tmp_path):
        """One detector: step 1 stamps run-config.json, bootstrap forwards it.

        Re-detecting here would create a second source of the same fact.
        """
        (tmp_path / "run-config.json").write_text(
            json.dumps({"mode": "pr", "plugin_version": "9.9.9"})
        )
        result = run_bootstrap(
            "--agent", "security-reviewer", "--output-dir", str(tmp_path)
        )
        assert "PIRATEGOAT_PLUGIN_VERSION=9.9.9" in result.stdout


class TestNotApplicableCompletionContract:
    """The shared protocol is the sole executable abstention recipe."""

    def test_bootstrap_includes_shared_not_applicable_sequence(self, tmp_path):
        protocol = (PLUGIN_ROOT / "agents/shared/reviewer-protocol.md").read_text()
        review_rules = _mod.extract_protocol_sections(
            protocol,
            _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS,
        )
        prompt = build_output(
            agent_name="woo-regression-reviewer",
            plugin_root=str(PLUGIN_ROOT),
            status="OK",
            review_rules=review_rules,
            domain_rules=None,
            scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number=None,
            reviewer_name="woo-regression",
            not_diffed_count=0,
            has_php=False,
        )

        assert "builder.mark_not_applicable(" in prompt
        assert "builder.save(OUTPUT_DIR)" in prompt
        assert "STATUS: FINISHED" in prompt

    def test_output_instructions_require_collision_safe_builder_invocation(self, tmp_path):
        """Parallel reviewers must execute the builder without a shared script file."""
        prompt = build_output(
            agent_name="security-reviewer",
            plugin_root=str(PLUGIN_ROOT),
            status="OK",
            review_rules="",
            domain_rules=None,
            scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number=None,
            reviewer_name="security",
            not_diffed_count=0,
            has_php=False,
        )

        heredoc_body = prompt.split("python3 <<'PY'\n", 1)[1].split("\nPY", 1)[0]
        compile(heredoc_body, "<bootstrap builder example>", "exec")

        assert "MUST use a one-shot quoted heredoc" in prompt
        assert "python3 <<'PY'" in prompt
        assert "MUST NOT create or write a temporary builder script with the Write tool" in prompt
        assert "parallel reviewers share the parent-session scratch directory" in prompt
        assert "generic filenames collide" in prompt
        assert "script FILE (Write tool) or a heredoc" not in prompt
        assert "python3 -c" in prompt  # named so it can be forbidden
        assert "NEVER" in prompt

    def test_output_instructions_require_count_reconciliation(self, tmp_path):
        """Agents must report the builder's recorded state, not their intent."""
        prompt = build_output(
            agent_name="security-reviewer",
            plugin_root=str(PLUGIN_ROOT),
            status="OK",
            review_rules="",
            domain_rules=None,
            scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number=None,
            reviewer_name="security",
            not_diffed_count=0,
            has_php=False,
        )

        assert "RECORDED COUNTS" in prompt

    def test_registered_agents_derive_unique_nonempty_reviewer_names(self):
        """Every shipped agent has a collision-safe output identity."""
        reviewer_names = [derive_reviewer_name(agent_name) for agent_name in ALL_AGENTS]

        assert all(reviewer_names)
        assert len(reviewer_names) == len(set(reviewer_names))

    def test_bootstrap_heredocs_save_distinct_outputs_for_parallel_reviewers(
        self, tmp_path
    ):
        """Concrete bootstrap commands sharing OUTPUT_DIR cannot collide."""
        output_dir = tmp_path / "shared reviewer's output folder"
        invocations = []
        for agent_name in ("security-reviewer", "performance-reviewer"):
            reviewer_name = derive_reviewer_name(agent_name)
            prompt = build_output(
                agent_name=agent_name,
                plugin_root=str(PLUGIN_ROOT),
                status="OK",
                review_rules="",
                domain_rules=None,
                scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
                exploration_scope=None,
                output_dir=str(output_dir),
                pr_number="42",
                reviewer_name=reviewer_name,
                not_diffed_count=0,
                has_php=False,
            )
            start = prompt.index("PIRATEGOAT_PLUGIN_ROOT=")
            end = prompt.index("\nPY", start) + len("\nPY")
            invocations.append(
                prompt[start:end].replace(
                    "builder.set_files_reviewed(N)",
                    "builder.set_files_reviewed(2)",
                )
            )

        def run_invocation(invocation):
            return subprocess.run(
                ["bash", "-c", invocation],
                cwd=tmp_path,
                timeout=30,
                capture_output=True,
                text=True,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run_invocation, invocations))

        assert all(result.returncode == 0 for result in results), [
            result.stderr for result in results
        ]
        assert all("RECORDED COUNTS:" in result.stdout for result in results)
        assert sorted(path.name for path in output_dir.iterdir()) == [
            "performance-review.json",
            "security-review.json",
        ]
        for reviewer_name in ("security", "performance"):
            saved = json.loads(
                (output_dir / f"{reviewer_name}-review.json").read_text()
            )
            assert saved["reviewer"] == reviewer_name
            assert saved["pr_id"] == "42"
            assert saved["meta"]["files_reviewed"] == 2

    def test_bootstrap_heredoc_executes_with_shell_sensitive_paths(self, tmp_path):
        """Bootstrap must hand paths to stdin Python without literal interpolation."""
        plugin_root = tmp_path / "plugin root's copy"
        shutil.copytree(PLUGIN_ROOT / "scripts", plugin_root / "scripts")
        output_dir = tmp_path / "reviewer's output folder"
        prompt = build_output(
            agent_name="security-reviewer",
            plugin_root=str(plugin_root),
            status="OK",
            review_rules="",
            domain_rules=None,
            scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
            exploration_scope=None,
            output_dir=str(output_dir),
            pr_number="42",
            reviewer_name="security",
            not_diffed_count=0,
            has_php=False,
        )
        start = prompt.index("PIRATEGOAT_PLUGIN_ROOT=")
        end = prompt.index("\nPY", start) + len("\nPY")
        shell_example = prompt[start:end]
        shell_example = shell_example.replace(
            "builder.set_files_reviewed(N)",
            "builder.set_files_reviewed(3)",
        )
        python_files_before = set(tmp_path.rglob("*.py"))

        result = subprocess.run(
            ["bash", "-c", shell_example],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert "RECORDED COUNTS:" in result.stdout
        assert sorted(path.name for path in output_dir.iterdir()) == [
            "security-review.json",
        ]
        saved = json.loads((output_dir / "security-review.json").read_text())
        assert saved["meta"]["files_reviewed"] == 3
        assert set(tmp_path.rglob("*.py")) == python_files_before

    def test_agent_definitions_do_not_duplicate_abstention_calls(self):
        offenders = [
            path.name
            for path in sorted((PLUGIN_ROOT / "agents").glob("*.md"))
            if "mark_not_applicable(" in path.read_text()
        ]

        assert offenders == [], (
            "Agent-local abstention calls drift from the shared persisted "
            f"completion sequence: {offenders}"
        )

    def test_woo_reviewer_uses_structured_floors_not_description_markers(self):
        prompt = (PLUGIN_ROOT / "agents/woo-regression-reviewer.md").read_text()

        assert 'severity_floor="medium"' in prompt
        assert 'severity_floor="high"' in prompt
        assert "Severity-floor:" not in prompt

    def test_woo_reviewer_audits_heuristic_proxy_predicates(self):
        """Invariant 11 (regression guard for woocommerce/woocommerce#66613):
        proxy predicates inferred from persisted state shape must be audited
        against every writer of that state and every store configuration."""
        prompt = (PLUGIN_ROOT / "agents/woo-regression-reviewer.md").read_text()

        # Per-hunk audit row exists, so the self-audit can catch dismissals.
        assert "Heuristics — proxy predicate vs. configuration variance" in prompt
        # Invariant section with the producer-verification rule.
        assert "Heuristic proxy predicates and configuration variance" in prompt
        assert "guaranteed-true under some supported configuration" in prompt
        assert "verified at the producers" in prompt
        # Findings can carry the dedicated category.
        assert "`proxy-predicate`" in prompt

    def test_woo_reviewer_audits_markup_selector_contracts(self):
        """Invariant 12 (regression guard for the 2026-07-16 catch on the
        woocommerce/woocommerce#55669 fix): rendered markup is a selector
        surface — removing an element breaks the CSS/JS/tests that key on it,
        and the dependency must be verified from the dependent side."""
        prompt = (PLUGIN_ROOT / "agents/woo-regression-reviewer.md").read_text()

        # Per-hunk audit row exists.
        assert "Markup — removed/renamed selector surface" in prompt
        # Invariant section with the dependent-side verification rule.
        assert "Rendered markup is a contract" in prompt
        assert "dependent side" in prompt
        # The corpus example.
        assert "55669" in prompt
        # Findings can carry the dedicated category.
        assert "`markup-contract`" in prompt

    def test_downstream_prompts_preserve_explicit_floor_contract(self):
        reconciliator = (
            PLUGIN_ROOT / "agents/review-reconciliator.md"
        ).read_text().lower()
        critic = (PLUGIN_ROOT / "agents/decision-reviewer.md").read_text().lower()

        assert "categories never invent a floor" in reconciliator
        assert "strongest verified" in reconciliator
        assert "severity_floor" in reconciliator
        assert "severity floor" in critic


class TestDismissalDisciplineContract:
    """Dismissal/mitigation verification must apply to ALL findings.

    Regression guard for the woocommerce/woocommerce#66488 miss: a detected
    concern was demoted to "narrow and acceptable corner" tradeoff prose on
    an unverified frequency claim, because the verification rules were scoped
    to floored findings and three regression categories only.
    """

    def test_reconciliator_has_general_dismissal_discipline(self):
        text = (PLUGIN_ROOT / "agents/review-reconciliator.md").read_text()
        assert "## Dismissal & Mitigation Discipline (ALL findings)" in text
        assert "Frequency claims are not structural reasons" in text
        assert "verified at the producers" in text
        assert "verified at file:line for the cited input shape" in text

    def test_reconciliator_sanctions_upstream_producer_tracing(self):
        text = (PLUGIN_ROOT / "agents/review-reconciliator.md").read_text()
        assert "sanctioned exception" in text
        assert "upstream producers" in text

    def test_tradeoffs_section_has_exit_criteria(self):
        text = (PLUGIN_ROOT / "agents/review-reconciliator.md").read_text()
        assert "not a disposal path for findings" in text
        assert "`add_issue()` at Low or Medium" in text


class TestVerificationMethodContract:
    """Verification-method rules ported from ai-regression-review's triage.md
    (the half the 2026-07-15 dismissal port did not cover).

    Regression guard for the 2026-07-16 run: three agents 'cleared' the blast
    radius of a removed <label> with the same wrong grep ('.titledesc label'
    when the load-bearing selectors were 'th label'), the raw signal read as
    3-clear-vs-1-found, and the reconciliator then repeated the failure one
    level up by verifying from a 37-line window of a 5,900-line stylesheet,
    missing a third dependent rule.
    """

    def test_reconciliator_has_verification_method_weighting(self):
        text = (PLUGIN_ROOT / "agents/review-reconciliator.md").read_text()
        assert "## Verification-Method Weighting" in text
        # Correlated-signal rule: same method = one probe, not N confirmations
        assert "one probe" in text
        # Anti-vote-counting: counts alone never move a verdict or severity
        assert "counts alone" in text
        # Negative-evidence rule: a negative search proves pattern absence only
        assert "searched pattern is absent" in text
        # Whole-artifact rule: enumerate all occurrences before concluding
        assert "every occurrence" in text

    def test_reconciliator_convergence_is_method_aware(self):
        text = (PLUGIN_ROOT / "agents/review-reconciliator.md").read_text()
        assert "distinct verification methods" in text
        assert "More agents = higher confidence" not in text

    def test_reconciliator_treats_clearance_conflicts_as_verification_targets(self):
        text = (PLUGIN_ROOT / "agents/review-reconciliator.md").read_text()
        assert "Clearance vs. finding" in text
        assert "never a vote" in text

    def test_protocol_requires_add_clearance_for_absence_claims(self):
        text = (PLUGIN_ROOT / "agents/shared/reviewer-protocol.md").read_text()
        assert "add_clearance" in text

    def test_protocol_has_absence_claim_rules(self):
        text = (PLUGIN_ROOT / "agents/shared/reviewer-protocol.md").read_text()
        assert "## Absence Claims" in text
        # Directionality: search the dependent side's vocabulary
        assert "dependent side" in text
        # Negative-evidence limit
        assert "searched pattern is absent" in text
        # Auditability: state the method used
        assert "state the exact search" in text

    def test_absence_claim_rules_reach_agent_prompts(self, tmp_path):
        """The new protocol section must flow through bootstrap's skip-list
        extraction into generated agent prompts (in-process build against the
        repo's protocol file — the subprocess path resolves the installed
        plugin cache, not this checkout)."""
        protocol = (PLUGIN_ROOT / "agents/shared/reviewer-protocol.md").read_text()
        review_rules = _mod.extract_protocol_sections(
            protocol,
            _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS,
        )
        prompt = build_output(
            agent_name="code-reviewer",
            plugin_root=str(PLUGIN_ROOT),
            status="OK",
            review_rules=review_rules,
            domain_rules=None,
            scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number=None,
            reviewer_name="code",
            not_diffed_count=0,
            has_php=False,
        )
        assert "## Absence Claims" in prompt
        assert "searched pattern is absent" in prompt


class TestEmpiricalProbeContract:
    """The probe-naming convention must reach the reviewers that run code.

    The sweep in orchestration deletes only untracked files whose BASENAME
    carries `pirategoat-probe`. That enforcement half is inert unless the
    producer half — this protocol section — actually reaches an agent, and
    a section placed in a stripped part of the protocol reaches nobody
    (the 1.108.0 failure `TestNotDiffedContractIsDelivered` guards for the
    NOT DIFFED contract). This class is the same guard for the convention.
    """

    CLAUSES = (
        "## Empirical Probes",
        "Never create or modify tracked files",
        "pirategoat-probe",
        "FILENAME",
        "git does not ignore",
        "Create, run, and delete in a single command",
        "git reset",
    )

    def _delivered_prompt(self, tmp_path):
        protocol = (
            PLUGIN_ROOT / "agents/shared/reviewer-protocol.md"
        ).read_text()
        review_rules = _mod.extract_protocol_sections(
            protocol,
            _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS,
        )
        return build_output(
            agent_name="code-reviewer",
            plugin_root=str(PLUGIN_ROOT),
            status="OK",
            review_rules=review_rules,
            domain_rules=None,
            scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number=None,
            reviewer_name="code",
            not_diffed_count=0,
            has_php=False,
        )

    @pytest.mark.parametrize("clause", CLAUSES)
    def test_clause_reaches_agent_prompts(self, clause, tmp_path):
        """Each clause survives skip-list extraction into the built prompt.

        Compared with whitespace collapsed: the protocol is hard-wrapped
        prose, so a clause spanning a line break is still delivered. Only
        deleting or rewording it should fail this guard.
        """
        delivered = " ".join(self._delivered_prompt(tmp_path).split())
        assert " ".join(clause.split()) in delivered

    def test_section_is_not_in_the_skip_list(self):
        """A future skip-list entry must not silently strip the convention."""
        assert not any(
            skipped.startswith("## Empirical Probes")
            for skipped in _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS
        ), (
            "The probe convention is policy, not mechanics bootstrap "
            "performs — stripping it makes the residue sweep's producer "
            "half reach zero agents."
        )


class TestSmokeAllAgents:
    """Every registered agent must run bootstrap without crashing.

    This is the one legitimate ALL_AGENTS parameterization — each agent
    CAN independently fail due to bad registry config (invalid domain,
    missing protocol file, etc.). The test validates registry correctness.
    """

    @pytest.mark.parametrize("agent_name", ALL_AGENTS)
    def test_exits_0(self, agent_name, tmp_path):
        result = run_bootstrap("--agent", agent_name, "--output-dir", str(tmp_path))
        assert result.returncode == 0, (
            f"{agent_name} exited with {result.returncode}: {result.stderr}"
        )


class TestErrorCases:
    """Error paths: unknown agent, malformed input."""

    def test_unknown_agent_exits_1(self, tmp_path):
        result = run_bootstrap("--agent", "nonexistent-reviewer", "--output-dir", str(tmp_path))
        assert result.returncode == 1
        assert "STATUS: ERROR" in result.stdout
        assert "Unknown agent" in result.stdout

    def test_unknown_agent_structured_error(self, tmp_path):
        result = run_bootstrap("--agent", "fake", "--output-dir", str(tmp_path))
        assert "=== BOOTSTRAP: fake ===" in result.stdout
        assert "ACTION: Report this error" in result.stdout


class TestReviewOutputBuilderAPIExample:
    """Bootstrap Section 3 must include a complete ReviewOutputBuilder usage example."""

    def _build(self, output_dir):
        return build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir=str(output_dir),
            pr_number="42",
            reviewer_name="security",
            not_diffed_count=0,
            has_php=False,
        )

    def test_output_contains_add_issue_example(self, tmp_path):
        """The usage example must show add_issue() with named parameters."""
        output = self._build(tmp_path)
        assert "add_issue(" in output
        assert "severity=" in output
        assert "title=" in output
        assert "file=" in output
        assert "description=" in output
        assert "recommendation=" in output

    def test_output_contains_add_positive_example(self, tmp_path):
        """The usage example must show add_positive()."""
        output = self._build(tmp_path)
        assert "add_positive(" in output

    def test_output_contains_save_example(self, tmp_path):
        """The usage example must show save() with the resolved output_dir."""
        output = self._build(tmp_path)
        assert "save(" in output
        assert str(tmp_path) in output

    def test_output_contains_set_files_reviewed(self, tmp_path):
        """The example must require the actual reviewed-file count."""
        output = self._build(tmp_path)
        assert "builder.set_files_reviewed(N)" in output
        assert "REQUIRED: replace N with the actual number of files you reviewed" in output
        assert "builder.set_files_reviewed(1)" not in output

    def test_output_contains_set_confidence(self, tmp_path):
        """The usage example must show set_confidence()."""
        output = self._build(tmp_path)
        assert "set_confidence(" in output

    def test_output_contains_no_verify_instruction(self, tmp_path):
        """The usage example must tell agents not to verify save() output."""
        output = self._build(tmp_path)
        lower = output.lower()
        # The instruction must convey "proceed directly after save()" — either
        # via "do not read/verify" or "proceed directly to the status signal".
        has_do_not = "do not" in lower and ("read" in lower or "verify" in lower) and ("output file" in lower or "save()" in lower)
        has_proceed_directly = "proceed directly" in lower and "save()" in lower
        assert has_do_not or has_proceed_directly


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
            not_diffed_count=0,
            has_php=False,
        )

    def test_small_scope_included_inline(self, tmp_path):
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
            output_dir=str(tmp_path),
            pr_number="42",
            reviewer_name="security",
            not_diffed_count=0,
            has_php=False,
        )
        assert small_scope in output

    def test_large_scope_truncated(self):
        """Scope over threshold is truncated with a file reference."""
        output = self._build_large_output(scope_size_kb=50)
        # The full 50KB scope should NOT be in the output
        assert len(output) < 40 * 1024  # output should be well under 40KB total

    def test_large_scope_has_file_reference(self, tmp_path):
        """When scope is truncated, output tells agent where to read the full scope."""
        output = self._build_large_output(scope_size_kb=50, output_dir=str(tmp_path))
        expected_path = tmp_path / "security-reviewer-scoped-diff.patch"
        assert str(expected_path) in output

    def test_large_scope_has_read_instructions(self):
        """When scope is truncated, output tells agent to use offset/limit."""
        output = self._build_large_output(scope_size_kb=50)
        lower = output.lower()
        assert "offset" in lower or "limit" in lower or "head" in lower

    def test_large_scopes_are_namespaced_by_agent(self, tmp_path):
        """Parallel reviewers retain distinct large scope files."""

        def build(agent_name, reviewer_name, domain):
            scope = f"DOMAIN: {domain}\n" + (f"{domain} diff line\n" * 2000)
            return build_output(
                agent_name=agent_name,
                plugin_root="/fake/root",
                status="OK",
                review_rules="rules here",
                domain_rules=None,
                scope_output=scope,
                exploration_scope=None,
                output_dir=str(tmp_path),
                pr_number="42",
                reviewer_name=reviewer_name,
                not_diffed_count=0,
                has_php=False,
            )

        security_output = build("security-reviewer", "security", "security")
        concurrency_output = build(
            "concurrency-reviewer", "concurrency", "concurrency"
        )
        security_path = tmp_path / "security-reviewer-scoped-diff.patch"
        concurrency_path = tmp_path / "concurrency-reviewer-scoped-diff.patch"

        assert str(security_path) in security_output
        assert str(concurrency_path) in concurrency_output
        assert "DOMAIN: security" in security_path.read_text()
        assert "DOMAIN: concurrency" not in security_path.read_text()
        assert "DOMAIN: concurrency" in concurrency_path.read_text()
        assert "DOMAIN: security" not in concurrency_path.read_text()


class TestDynamicDispatchRisk:
    """Bootstrap injects DYNAMIC_DISPATCH_RISK for dead-code-reviewer.

    has_php is a REQUIRED fact the caller supplies (main() derives it from
    telemetry_scope_paths — the same fact-based, sidecar-preferring path
    union used for scope telemetry and the NOT DIFFED contract).
    build_output() never parses scope_output for PHP filenames — see the
    regression tests at the bottom of this class for the failure mode that
    replaced.
    """

    def _build(self, tmp_path, has_php, scope_output="=== FILES ===\n=== DIFFS ===",
               agent_name="dead-code-reviewer"):
        return build_output(
            agent_name=agent_name,
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output=scope_output,
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number="42",
            reviewer_name="dead-code",
            not_diffed_count=0,
            has_php=has_php,
        )

    def test_dead_code_reviewer_gets_dispatch_risk(self, tmp_path):
        """dead-code-reviewer output includes DYNAMIC_DISPATCH_RISK."""
        output = self._build(tmp_path, has_php=True)
        assert "DYNAMIC_DISPATCH_RISK:" in output

    def test_dispatch_risk_high_with_php_files(self, tmp_path):
        """DYNAMIC_DISPATCH_RISK is 'high' when the caller's fact says PHP files are in scope."""
        output = self._build(tmp_path, has_php=True)
        risk_line = [l for l in output.splitlines() if "DYNAMIC_DISPATCH_RISK:" in l]
        assert risk_line, "DYNAMIC_DISPATCH_RISK line not found in output"
        assert "high" in risk_line[0].lower()

    def test_dispatch_risk_low_without_php_files(self, tmp_path):
        """DYNAMIC_DISPATCH_RISK is 'low' when the caller's fact says no PHP files are in scope."""
        output = self._build(tmp_path, has_php=False)
        risk_line = [l for l in output.splitlines() if "DYNAMIC_DISPATCH_RISK:" in l]
        assert risk_line, "DYNAMIC_DISPATCH_RISK line not found in output"
        assert "low" in risk_line[0].lower()

    def test_other_agents_no_dispatch_risk(self, tmp_path):
        """Non-dead-code agents do NOT get DYNAMIC_DISPATCH_RISK, regardless of has_php."""
        output = self._build(tmp_path, has_php=True, agent_name="security-reviewer")
        assert "DYNAMIC_DISPATCH_RISK:" not in output

    def test_php_looking_text_cannot_force_high_when_fact_says_low(self, tmp_path):
        """A scope_output full of .php filenames must not flip the decision
        when the caller's fact (has_php=False) says otherwise.

        This is the exact failure shape being fixed: the old implementation
        derived has_php by splitting rendered scope_output text on a double
        space and checking for a '.php' suffix — a second, independent
        derivation of the same fact build_output() now receives explicitly.
        """
        php_looking_text = (
            "=== FILES ===\n"
            "src/handler.php  (+10 -5)\n"
            "src/other.php  (+3 -1)\n"
            "=== DIFFS ==="
        )
        output = self._build(tmp_path, has_php=False, scope_output=php_looking_text)
        risk_line = [l for l in output.splitlines() if "DYNAMIC_DISPATCH_RISK:" in l]
        assert risk_line, "DYNAMIC_DISPATCH_RISK line not found in output"
        assert "low" in risk_line[0].lower()

    def test_garbled_text_cannot_suppress_high_when_fact_says_php(self, tmp_path):
        """A scope_output with no recognizable '.php' text must not suppress
        the high-risk contract when the caller's fact says PHP files are
        genuinely in scope.

        This mirrors the NOT DIFFED fix's renamed-header test: a future
        scope.py refactor that reformats or renames the FILES/DIFFS section
        (spacing, column order, a new section name) must not silently flip
        has_php just because the old '.php'-suffix text scan no longer
        matches — the caller's fact is authoritative regardless of how
        scope.py renders.
        """
        garbled_scope = (
            "=== SCOPE TRUNCATED ===\n"
            "Full scope written to external file; see it for details.\n"
        )
        assert ".php" not in garbled_scope  # the old text-scan's anchor is gone
        output = self._build(tmp_path, has_php=True, scope_output=garbled_scope)
        risk_line = [l for l in output.splitlines() if "DYNAMIC_DISPATCH_RISK:" in l]
        assert risk_line, "DYNAMIC_DISPATCH_RISK line not found in output"
        assert "high" in risk_line[0].lower()

    def test_real_php_scope_yields_high_end_to_end(self, tmp_path):
        """End-to-end (subprocess, real scope.py + main()) proof that a
        real PHP file in scope drives has_php through main()'s derivation.

        The class above covers build_output() in isolation, which cannot
        catch a mutation to main()'s has_php derivation itself (e.g.
        `has_php = False`) — that computation lives outside build_output(),
        so a unit test that only calls build_output() directly is blind to
        it. This runs the full subprocess chain against a fixture with a
        genuinely in-scope PHP file (src/ProductManager.php; the domain
        also excludes tests/ProductManagerTest.php, which must not count).
        """
        r = run_bootstrap(
            "--agent", "dead-code-reviewer", "--output-dir", str(tmp_path),
            fixture="multi-file-realistic.diff",
        )
        assert r.returncode == 0, r.stderr
        assert "DYNAMIC_DISPATCH_RISK: high" in r.stdout

    def test_real_php_free_scope_yields_low_end_to_end(self, tmp_path):
        """End-to-end companion to the test above: a fixture with zero PHP
        files (only .ts/.tsx) must drive has_php to False through the same
        real main() derivation.
        """
        r = run_bootstrap(
            "--agent", "dead-code-reviewer", "--output-dir", str(tmp_path),
            fixture="js-ts-source.diff",
        )
        assert r.returncode == 0, r.stderr
        assert "DYNAMIC_DISPATCH_RISK: low" in r.stdout

    def test_domain_excluded_php_test_file_does_not_force_high_end_to_end(self, tmp_path):
        """A PHP file present only under '=== SKIPPED === Outside domain'
        (e.g. a test file the dead-code domain deliberately excludes) must
        not count as PHP-in-scope.

        This is the reachable divergence between the old and new
        derivations on real scope text: the old text scan read every
        non-'===' line, including the SKIPPED summary line
        "Outside domain (N): tests/ProductManagerTest.php" — which has no
        double space, so the whole line survived as one token and its
        '.php' suffix set has_php=True even though no PHP file was
        genuinely in scope. telemetry_scope_paths only contains files that
        are actually in scope (inline, NOT DIFFED, or list-only), so it
        excludes SKIPPED files correctly.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "README.md").write_text("# init\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

        (repo / "src").mkdir()
        (repo / "tests").mkdir()
        (repo / "src" / "app.ts").write_text("export const x = 1;\n")
        (repo / "tests" / "ProductManagerTest.php").write_text(
            "<?php\nclass ProductManagerTest extends TestCase {\n"
            "    public function test_get_product() {\n"
            "        $this->assertTrue( true );\n    }\n}\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add ts app + php test"], cwd=repo, check=True)

        out_dir = tmp_path / "out"
        result = subprocess.run(
            [sys.executable, str(BOOTSTRAP_SCRIPT), "--agent", "dead-code-reviewer",
             "--output-dir", str(out_dir), "--range", "HEAD~1..HEAD"],
            capture_output=True, text=True, timeout=60, cwd=repo,
        )
        assert result.returncode == 0, result.stderr
        assert "Outside domain" in result.stdout and "ProductManagerTest.php" in result.stdout, (
            "fixture setup didn't produce the expected SKIPPED line — test doesn't pin what it claims"
        )
        assert "DYNAMIC_DISPATCH_RISK: low" in result.stdout


class TestRepoRuleAndRefModeSelection:
    """Repo rules must reach the reviewers they target (effective identity,
    complete scope), adapter instances must receive their declared path
    scope, and an explicit isolation request must never run inline."""

    @staticmethod
    def _write_review_context(output_dir: Path, rules=None, reviewers=None):
        (output_dir / "review-context.json").write_text(json.dumps({
            "review_config": {
                "rules": rules or [],
                "reviewers": reviewers or [],
            }
        }))

    @staticmethod
    def _rule(rule_dir: Path, rule_id, body, applies_to=None, channel="blocking"):
        rule_file = rule_dir / f"{rule_id}.md"
        rule_file.write_text(body)
        return {
            "id": rule_id,
            "path": f"{rule_id}.md",
            "resolved_path": str(rule_file),
            "applies_to": applies_to
            or {"agents": [], "domains": [], "paths": []},
            "channel": channel,
        }

    @staticmethod
    def _make_repo(repo: Path, feature_files):
        repo.mkdir()

        def _git(*git_args):
            subprocess.run(
                ["git"] + list(git_args),
                cwd=repo, capture_output=True, text=True, check=True,
            )

        _git("init", "-b", "main")
        _git("config", "user.email", "t@t.com")
        _git("config", "user.name", "T")
        _git("config", "commit.gpgsign", "false")
        (repo / "base.txt").write_text("base\n")
        _git("add", ".")
        _git("commit", "-m", "initial")
        for relpath, content in feature_files.items():
            target = repo / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        _git("add", ".")
        _git("commit", "-m", "feature")

    @staticmethod
    def _run_in_repo(repo: Path, *args):
        cmd = (
            [sys.executable, str(BOOTSTRAP_SCRIPT)]
            + list(args)
            + ["--range", "HEAD~1..HEAD"]
        )
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, cwd=str(repo)
        )

    def test_rule_targeting_the_instance_name_reaches_the_adapter(
        self, tmp_path
    ):
        """In ref-mode args.agent is always "repo-reviewer-adapter" — rule
        selection must key on the synthetic instance name."""
        ref = tmp_path / "r.md"
        ref.write_text("Review renewals.")
        self._write_review_context(tmp_path, rules=[self._rule(
            tmp_path, "renewals-rule", "RENEWALS INSTANCE RULE MARKER",
            applies_to={
                "agents": ["repo-renewals-reviewer"],
                "domains": [], "paths": [],
            },
        )])
        result = run_bootstrap(
            "--agent", "repo-reviewer-adapter",
            "--repo-agent-ref", str(ref),
            "--instance-name", "repo-renewals-reviewer",
            "--scope-domains", "code",
            "--output-dir", str(tmp_path),
        )
        assert result.returncode == 0
        assert "RENEWALS INSTANCE RULE MARKER" in result.stdout

    def test_rule_targeting_a_declared_scope_domain_reaches_the_adapter(
        self, tmp_path
    ):
        """The adapter's registry domain is null — rule selection must use
        the parsed --scope-domains, not the registry-derived list."""
        ref = tmp_path / "r.md"
        ref.write_text("Review renewals.")
        self._write_review_context(tmp_path, rules=[self._rule(
            tmp_path, "code-rule", "DECLARED DOMAIN RULE MARKER",
            applies_to={"agents": [], "domains": ["code"], "paths": []},
        )])
        result = run_bootstrap(
            "--agent", "repo-reviewer-adapter",
            "--repo-agent-ref", str(ref),
            "--instance-name", "repo-renewals-reviewer",
            "--scope-domains", "code",
            "--output-dir", str(tmp_path),
        )
        assert result.returncode == 0
        assert "DECLARED DOMAIN RULE MARKER" in result.stdout

    def test_advisory_rule_injects_the_channel_contract(
        self, tmp_path, monkeypatch
    ):
        """The channel exists only as rendered prose unless the reviewer is
        told to propagate it — an untagged advisory-rule finding counts as
        blocking in the verdict, letting an advisory rule gate the review."""
        self._write_review_context(tmp_path, rules=[self._rule(
            tmp_path, "adv-rule", "ADVISORY BODY", channel="advisory",
        )])
        result = run_bootstrap(
            "--agent", "performance-reviewer", "--output-dir", str(tmp_path)
        )
        assert result.returncode == 0
        assert 'add_issue(..., channel="advisory")' in result.stdout

        entitlement = json.loads(
            (tmp_path / "performance-advisory-entitlement.json").read_text()
        )
        assert entitlement == {"schema": 1, "advisory_entitled": True}

        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "performance")
        builder = ReviewOutputBuilder(pr_id="1", reviewer="performance")
        builder.add_issue(
            severity="high", title="Advisory", file="src/app.py",
            description="d", recommendation="r", line=1,
            channel="advisory",
        )
        assert builder.to_dict()["verdict"] == "approve"

    def test_blocking_only_rules_omit_the_channel_contract(
        self, tmp_path, monkeypatch
    ):
        self._write_review_context(tmp_path, rules=[self._rule(
            tmp_path, "blk-rule", "BLOCKING BODY", channel="blocking",
        )])
        result = run_bootstrap(
            "--agent", "performance-reviewer", "--output-dir", str(tmp_path)
        )
        assert result.returncode == 0
        assert "BLOCKING BODY" in result.stdout
        assert "CHANNEL CONTRACT" not in result.stdout

        entitlement = json.loads(
            (tmp_path / "performance-advisory-entitlement.json").read_text()
        )
        assert entitlement == {"schema": 1, "advisory_entitled": False}

        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "performance")
        builder = ReviewOutputBuilder(pr_id="1", reviewer="performance")
        with pytest.raises(ValueError, match="advisory.*not entitled"):
            builder.add_issue(
                severity="high", title="Advisory", file="src/app.py",
                description="d", recommendation="r", line=1,
                channel="advisory",
            )

    def test_isolated_execution_is_refused(self, tmp_path):
        """An explicit isolation request must never silently widen into
        inline execution of the repo prompt — not even via override."""
        ref = tmp_path / "r.md"
        ref.write_text("Review renewals.")
        result = run_bootstrap(
            "--agent", "repo-reviewer-adapter",
            "--repo-agent-ref", str(ref),
            "--instance-name", "repo-renewals-reviewer",
            "--execution", "isolated",
            "--scope-domains", "code",
            "--output-dir", str(tmp_path),
        )
        assert result.returncode == 1
        assert "Isolated execution is not implemented" in result.stdout

    def test_path_rule_matches_a_budget_deferred_file(self, tmp_path):
        """A rule about a NOT DIFFED file applies precisely when the
        reviewer must inspect that file — selection must see the complete
        in-scope set, not only the inline diff list."""
        repo = tmp_path / "repo"
        self._make_repo(repo, {
            "alpha.php": "<?php\n" + "\n".join(
                f"echo {i};" for i in range(3000)
            ) + "\n",
            "deferred_target.php": "<?php\n" + "\n".join(
                f"print({i});" for i in range(2500)
            ) + "\n",
        })
        outdir = tmp_path / "out"
        outdir.mkdir()
        self._write_review_context(outdir, rules=[self._rule(
            outdir, "deferred-rule", "DEFERRED FILE RULE MARKER",
            applies_to={
                "agents": [], "domains": [],
                "paths": ["deferred_target.php"],
            },
        )])
        result = self._run_in_repo(
            repo, "--agent", "code-reviewer", "--output-dir", str(outdir)
        )
        assert result.returncode == 0
        assert "deferred_target.php" in extract_not_diffed_files(result.stdout)
        assert "DEFERRED FILE RULE MARKER" in result.stdout

    def test_ref_mode_path_declaration_scopes_the_matching_file(
        self, tmp_path
    ):
        """A reviewer dispatched because applies_to.paths matched must
        receive those files in scope even when no declared domain's
        extension filter covers them."""
        repo = tmp_path / "repo"
        self._make_repo(repo, {
            "docs/guide.md": "# guide\n",
            "app.php": "<?php echo 1;\n",
        })
        outdir = tmp_path / "out"
        outdir.mkdir()
        ref = outdir / "docs-expert.md"
        ref.write_text("Review the docs.")
        self._write_review_context(outdir, reviewers=[{
            "id": "docs-expert", "label": "Docs Expert",
            "ref": "docs-expert.md", "resolved_ref": str(ref),
            "applies_to": {
                "agents": [], "domains": [], "paths": ["docs/**"],
            },
            "channel": "blocking", "execution": "inline", "model": None,
        }])
        result = self._run_in_repo(
            repo, "--agent", "repo-reviewer-adapter",
            "--repo-agent-ref", str(ref),
            "--instance-name", "repo-docs-expert-reviewer",
            "--scope-domains", "code",
            "--output-dir", str(outdir),
        )
        assert result.returncode == 0
        assert "docs/guide.md" in extract_scope_files(result.stdout)


class TestOutputFilenameConsistency:
    """Output filenames from ReviewOutputBuilder.save() match bootstrap expectations."""

    def test_save_uses_review_suffix(self, tmp_path):
        """save() should write {reviewer}-review.json only."""
        from review.agent.output import ReviewOutputBuilder

        builder = ReviewOutputBuilder(pr_id="42", reviewer="dead-code")
        result = builder.save(str(tmp_path))

        assert set(result) == {"json"}
        assert result["json"].endswith("dead-code-review.json"), f"Got: {result['json']}"
        assert os.path.isfile(result["json"])
        assert not os.path.exists(os.path.join(str(tmp_path), "dead-code-review.md"))

    def test_bootstrap_output_matches_save_filenames(self, tmp_path):
        """Bootstrap OUTPUT_FILES must name exactly the artifact save() publishes:
        the review JSON, and no md the pipeline derives elsewhere.

        This checks the briefing TEXT only (what the agent is told to produce);
        the save() filesystem contract is covered by the tests above.
        """
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number="42",
            reviewer_name="dead-code",
            not_diffed_count=0,
            has_php=False,
        )
        assert f"{tmp_path}/dead-code-review.json" in output
        assert f"{tmp_path}/dead-code-review.md" not in output


class TestBootstrapImportDoesNotBreakTelemetry:
    """Importing `review.agent.bootstrap` first must leave a working
    `ReviewTelemetry` — a real regression, not a hypothetical one.

    `derive_reviewer_name()` used to live in bootstrap.py itself; the day
    `manifest_sections.py` started importing it FROM bootstrap
    (`from .agent.bootstrap import derive_reviewer_name`), a
    package-qualified `import review.agent.bootstrap` re-entered
    bootstrap mid-initialization: bootstrap's own top-level telemetry
    load (`spec_from_file_location` + `exec_module` on `telemetry.py`)
    runs `telemetry.py`'s top level, which falls back to
    `from review import manifest_sections`, which in turn tried
    `from .agent.bootstrap import derive_reviewer_name` — but
    `sys.modules['review.agent.bootstrap']` was still the PARTIAL module
    from step one, with `derive_reviewer_name` not yet defined (it sat
    after the telemetry-loading block in file order). That raised
    ImportError, caught by telemetry's own best-effort try/except, and
    `ReviewTelemetry` silently became `None`.

    Must run in a fresh subprocess: the in-process `sys.modules` cache
    from every other test in this file (and pytest's own collection
    order) would otherwise make this test pass by accident depending on
    what already imported what.
    """

    def test_import_bootstrap_first_leaves_telemetry_working(self):
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import review.agent.bootstrap as bootstrap\n"
                "assert bootstrap.ReviewTelemetry is not None, "
                "'ReviewTelemetry is None — import cycle regression'\n"
                "print('OK')",
            ],
            capture_output=True, text=True, timeout=30,
            cwd=str(SCRIPTS_DIR),
        )
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert result.stdout.strip() == "OK"


def test_ecosystem_integration_reviewer_registered():
    """ecosystem-integration-reviewer is in the registry with correct shape."""
    import json
    from pathlib import Path
    reg_path = (
        Path(__file__).parent.parent.parent.parent
        / "scripts" / "review" / "agent_registry.json"
    )
    registry = json.loads(reg_path.read_text())
    agents = registry["agents"]
    entry = agents.get("ecosystem-integration-reviewer")
    assert entry is not None, "Agent must be registered"
    assert entry["domain"] == "wp-architecture"
    assert "reviewer" in entry["protocols"]
    assert entry["dispatch_class"] == "conditional"
    assert entry["model_tier"] == "sonnet"
    # Narrative field (human-facing)
    assert isinstance(entry.get("triage_criteria"), list) and entry["triage_criteria"]
    # Machine-consumed fields
    assert isinstance(entry.get("triage_keywords"), list) and entry["triage_keywords"]
    assert "require_triage_keyword_match" not in entry
    assert entry.get("require_php_source_file") is True
    assert "host_context_runtime_host_resolved" not in entry.get("triage_checks", [])
    assert entry.get("budget_override", 0) > 0


class TestNotDiffedContractIsDelivered:
    """The NOT DIFFED handling contract must survive protocol stripping.

    Regression guard for 1.109.0: the contract originally lived in
    reviewer-protocol.md's '## Scope Discovery' section, which bootstrap strips,
    so it never reached a single reviewer. Policy belongs in build_output.

    Regression guard for the 1.114.0 fix: build_output() used to re-derive the
    deferred-file count by regexing its OWN rendered scope text for
    '=== NOT DIFFED (budget exceeded, N files) ===' — a second, independent
    text-parsing path duplicating the one load_scope_facts()/main() already
    used. Any rename or reformat of that header in scope.py silently zeroed
    the count and dropped the entire honesty contract, with no error and
    (because these tests hardcoded the same header text the regex expected)
    no test failure either. build_output() now receives not_diffed_count as
    an explicit fact from the caller and never inspects scope_output for it.
    """

    NOT_DIFFED_SCOPE = (
        "=== REVIEW SCOPE ===\n"
        "=== FILES ===\n"
        "src/big.py  (+900 -10)\n"
        "=== NOT DIFFED (budget exceeded, 3 files) ===\n"
        "  src/big.py  (+900 -10)\n"
    )

    def _build(self, tmp_path, scope_output, not_diffed_count, **kwargs):
        kwargs.setdefault("has_php", False)
        return build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output=scope_output,
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number="42",
            reviewer_name="security",
            not_diffed_count=not_diffed_count,
            review_budget=80,
            **kwargs,
        )

    @pytest.mark.parametrize(
        "phrase",
        [
            "Not reviewed (budget):",          # the declaration format
            'builder.add_unreviewed("<path>")',  # the supported API for it
            "protocol violation",              # declaring on unspent budget
            "false statement",                 # citing budget you did not spend
            "never count a declared-unreviewed file",
            "is a contradiction save() rejects",  # declare-vs-claim, moved from
                                                   # '## ReviewOutputBuilder API'
        ],
    )
    def test_contract_reaches_reviewer(self, tmp_path, phrase):
        """Each clause of the contract appears in the delivered briefing."""
        output = self._build(tmp_path, self.NOT_DIFFED_SCOPE, not_diffed_count=1)
        assert phrase in output

    def test_contract_absent_without_not_diffed_files(self, tmp_path):
        """No NOT DIFFED files means no declaration contract to deliver."""
        clean_scope = "=== REVIEW SCOPE ===\n=== FILES ===\nsrc/a.py  (+5 -1)\n"
        output = self._build(tmp_path, clean_scope, not_diffed_count=0)
        assert "Not reviewed (budget):" not in output
        assert "is a contradiction save() rejects" not in output

    def test_contract_is_not_sourced_from_stripped_protocol(self):
        """The stripped protocol must not be the contract's only home.

        extract_protocol_sections() drops '## Scope Discovery', so anything
        placed there is invisible to reviewers by construction.
        """
        protocol = (PLUGIN_ROOT / "agents" / "shared" / "reviewer-protocol.md").read_text()
        delivered = _mod.extract_protocol_sections(
            protocol, _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert "Not reviewed (budget):" not in delivered, (
            "Contract text placed in a stripped protocol section never reaches "
            "a reviewer — keep it in build_output()'s REVIEW BUDGET block."
        )
        assert "is a contradiction save() rejects" not in delivered, (
            "The declare-vs-claim contradiction is policy, not mechanics "
            "bootstrap performs — keep it in build_output()'s REVIEW BUDGET "
            "block, the same as the rest of this contract."
        )

    def test_declare_claim_contradiction_was_moved_not_copied(self):
        """Regression guard: the contradiction rule used to live ONLY in
        reviewer-protocol.md's '## ReviewOutputBuilder API' section, which
        bootstrap also strips (see REVIEWER_PROTOCOL_SKIP_SECTIONS) — so it
        reached zero reviewers despite being taught. The fix moves the
        teaching into build_output(); the source sentence must not survive
        in the protocol file as a second, still-inert copy.
        """
        protocol = (
            PLUGIN_ROOT / "agents" / "shared" / "reviewer-protocol.md"
        ).read_text()
        assert "declaring and claiming the same path is rejected" not in protocol, (
            "the old sentence should have moved into build_output(), not "
            "been left behind as a dead copy in a stripped section"
        )

    def test_renamed_scope_header_cannot_suppress_a_real_count(self, tmp_path):
        """A scope.py header rename/reformat must not silently drop the contract.

        This is the exact failure shape being fixed: the old regex expected
        the literal string '=== NOT DIFFED (budget exceeded, N files) ===' in
        scope_output. Here that header is renamed to something a future
        scope.py refactor might plausibly emit, and NO section matches the
        old pattern at all — yet because the caller still supplies the real
        fact via not_diffed_count, the contract must still be delivered.
        """
        renamed_header_scope = (
            "=== REVIEW SCOPE ===\n"
            "=== FILES ===\n"
            "src/big.py  (+900 -10)\n"
            "=== DEFERRED (too large to inline, 3 files) ===\n"
            "  src/big.py  (+900 -10)\n"
        )
        assert "NOT DIFFED" not in renamed_header_scope  # the old regex's anchor is gone
        output = self._build(tmp_path, renamed_header_scope, not_diffed_count=1)
        assert "Not reviewed (budget):" in output
        assert "protocol violation" in output

    def test_original_header_text_alone_no_longer_drives_the_contract(self, tmp_path):
        """The rendered header text must never re-enable the contract by itself.

        NOT_DIFFED_SCOPE carries the exact header the old regex parsed, but
        not_diffed_count is explicitly 0 (the caller's fact says nothing was
        deferred). If build_output() still read scope_output text for this
        decision, the contract would incorrectly appear. It must not.
        """
        output = self._build(tmp_path, self.NOT_DIFFED_SCOPE, not_diffed_count=0)
        assert "Not reviewed (budget):" not in output


class TestTestingDocCounts:
    """TESTING.md's per-class count tables must match real collection.

    Four of the eight documented counts had silently drifted (5 vs 13, 2 vs
    3, 21 vs 30, 4 vs 5) and six classes were undocumented entirely, so the
    table read as authoritative while being wrong — the failure mode the
    table exists to prevent. Counts are COLLECTED tests, not test methods:
    parameterized classes expand, and for TestSmokeAllAgents (one method
    over every registered agent) the method count would be useless.

    The guard lives here, next to the first table it covered, and covers
    every count table in TESTING.md — a table added without a guard is a
    table that drifts. It checks itself: its own row is in the first table.
    """

    # (TESTING.md heading, test file under tests/, coverage mode)
    #
    # "full"    — the table claims to describe the whole file, so both
    #             directions hold: documented counts are real, and no class
    #             is missing a row.
    # "partial" — the table documents a deliberate subset of a large file
    #             (e.g. the reconciliation-context table calls out the 2
    #             classes carrying the NOT DIFFED contract out of 22). Its
    #             rows must still be true; requiring the undocumented
    #             classes would be demanding prose the section never
    #             promised.
    TABLES = (
        ("###Bootstrap Integration Tests",
         "review/agent/test_bootstrap_integration.py", "full"),
        ("###Critic Adjustments Tests",
         "review/test_critic_adjustments.py", "full"),
        ("###Reconciliation Context Tests",
         "review/test_reconciliation_context.py", "partial"),
        ("###Orchestration Hygiene Tests",
         "review/test_orchestration_hygiene.py", "full"),
        ("###Pipeline Infrastructure Tests",
         "review/test_pipeline_infra.py", "partial"),
        ("###Telemetry Tests",
         "review/test_telemetry.py", "partial"),
        ("###Synthesis Agent Lifecycle Tests",
         "review/test_synthesis_lifecycle.py", "full"),
    )
    FULL_TABLES = tuple(row for row in TABLES if row[2] == "full")

    def _documented(self, heading):
        """Parse {class: count} from one table in TESTING.md."""
        text = (TESTS_DIR / "TESTING.md").read_text(encoding="utf-8")
        assert text.count(heading) == 1, (
            f"heading {heading!r} is not unique in TESTING.md — the guard "
            f"would parse an ambiguous block"
        )
        block = text[text.index(heading):].split("\n###", 1)[0]
        documented = {}
        for line in block.splitlines():
            match = re.match(r"\|\s*`(Test\w+)`\s*\|\s*(\d+)\s*\|", line)
            if match:
                documented[match.group(1)] = int(match.group(2))
        assert documented, (
            f"no rows parsed under {heading!r} — did the table format change?"
        )
        return documented

    def _collected(self, rel_path):
        """Real per-class collected counts, via pytest's own collector."""
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(TESTS_DIR / rel_path),
             "--collect-only", "-q", "-p", "no:cacheprovider"],
            capture_output=True, text=True, cwd=str(PLUGIN_ROOT.parent.parent),
        )
        assert result.returncode == 0, (
            f"collection failed for {rel_path}:\n{result.stdout}\n{result.stderr}"
        )
        counts = {}
        for line in result.stdout.splitlines():
            match = re.search(r"::(Test\w+)::", line)
            if match:
                counts[match.group(1)] = counts.get(match.group(1), 0) + 1
        assert counts, f"collected nothing from {rel_path} — format changed?"
        return counts

    @pytest.mark.parametrize("heading,rel_path,mode", TABLES)
    def test_documented_counts_match_collection(self, heading, rel_path, mode):
        documented = self._documented(heading)
        collected = self._collected(rel_path)
        wrong = {
            name: (count, collected.get(name))
            for name, count in documented.items()
            if collected.get(name) != count
        }
        assert not wrong, (
            f"{heading} counts are stale (class: documented -> actual): "
            f"{wrong}. Update the table."
        )

    @pytest.mark.parametrize("heading,rel_path,mode", FULL_TABLES)
    def test_every_class_is_documented(self, heading, rel_path, mode):
        documented = self._documented(heading)
        collected = self._collected(rel_path)
        missing = sorted(set(collected) - set(documented))
        assert not missing, (
            f"classes absent from {heading}: {missing}. Add a row each, "
            "or the table implies coverage that isn't described."
        )

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
from review.agent.scope import format_text_output

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
        # claimable NOT DIFFED paths (in-scope work whose diffs were withheld
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
        instance-named accounting input (so the builder's claim
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
        accounting_input = json.loads(
            (tmp_path / "repo-renewals-review-accounting-input.json").read_text()
        )
        assert accounting_input["channels"] == ["advisory"]
        assert not (tmp_path / "repo-renewals-advisory-entitlement.json").exists()

        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "repo-renewals")
        builder = ReviewOutputBuilder(pr_id="1", reviewer="repo-renewals")
        builder.add_finding(
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

    def test_accounting_input_backs_claim_validation(self, tmp_path):
        """Bootstrap persists the authoritative NOT DIFFED set so the
        builder can reject claims that match no claimable file."""
        result = run_bootstrap(
            "--agent", "performance-reviewer", "--output-dir", str(tmp_path)
        )
        assert result.returncode == 0
        accounting_input = (
            tmp_path / "performance-review-accounting-input.json"
        )
        assert accounting_input.is_file()
        data = json.loads(accounting_input.read_text())
        assert sorted(data["review_claimable_files"]) == sorted(
            extract_not_diffed_files(result.stdout)
        )
        # Closes the main()->build_output() seam: review_claimable_count must be
        # derived from this exact claimable set, not a neighboring fact
        # (e.g. total scope files) that also happens to be non-empty here.
        # A mis-wired count would pass every other assertion in this suite.
        assert ("Not reviewed (budget):" in result.stdout) == bool(
            data["review_claimable_files"]
        )

    def test_accounting_input_carries_budget_and_scope_counts(self, tmp_path):
        """Schema 3 carries the effective (override-applied)
        budget and scope counts save()'s PROGRESS line reads — the retired
        env-var budget transport silently died for any agent that rebuilt
        its save command, so the sidecar is the only carrier.

        history-insights-reviewer has a fixed budget_override (45) in the
        registry — proof the sidecar carries the FINAL number, not a
        scope-only figure a downstream reader would have to recompute.
        """
        result = run_bootstrap(
            "--agent", "history-insights-reviewer", "--output-dir", str(tmp_path)
        )
        assert result.returncode == 0
        assert "Target: ~45 tool calls" in result.stdout

        accounting_input = (
            tmp_path / "history-insights-review-accounting-input.json"
        )
        assert accounting_input.is_file()
        data = json.loads(accounting_input.read_text())
        assert data["schema"] == 4
        assert data["review_budget"] == 45
        assert data["channels"] == ["blocking"]
        assert "budget_capped" not in data

        diffed = extract_scope_files(result.stdout)
        not_diffed = extract_not_diffed_files(result.stdout)
        expected_in_scope = len(
            dict.fromkeys([*diffed, *not_diffed])
        )
        assert data["in_scope_review_file_count"] == expected_in_scope
        assert data["inline_diff_file_count"] == len(diffed)

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
            review_claimable_count=0,
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
            review_claimable_count=0,
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
            "builder = ReviewOutputBuilder.open("
            "output_dir, pr_id, reviewer_name)" in prompt
        )
        assert "builder.save_draft()" in prompt
        assert "MUST NOT create or write a temporary builder script" in prompt
        assert "generic filenames collide" in prompt
        assert "DRAFT TOTALS" in prompt
        assert "run the exact FINALIZE REVIEW command printed by" in prompt
        assert "REVIEW FINALIZED" in prompt
        assert "Only then return the FINISHED signal" in prompt
        assert "Return signal format:" in prompt
        assert "STATUS: FINISHED" in prompt
        assert f"{tmp_path}/security-review.json" in prompt
        assert f"{tmp_path}/security-review.md" not in prompt

    def test_every_bootstrapped_reviewer_sees_only_the_canonical_contract(
        self, tmp_path
    ):
        forbidden = (
            "add_issue",
            "add_clearance",
            "add_deferred_reviewed",
            "add_tool_result",
            "REVIEW DIGEST",
        )
        required = (
            "ReviewOutputBuilder.open",
            "add_finding",
            "record_check",
            "claim_files_reviewed",
            "save_draft",
            "run the exact FINALIZE REVIEW command printed by",
        )

        for agent_name in ALL_AGENTS:
            prompt = build_output(
                agent_name=agent_name,
                plugin_root=str(PLUGIN_ROOT),
                status="OK",
                review_rules="rules",
                domain_rules=None,
                scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
                exploration_scope=None,
                output_dir=str(tmp_path),
                pr_number="42",
                reviewer_name=derive_reviewer_name(agent_name),
                review_claimable_count=1,
                has_php=False,
            )
            assert all(token in prompt for token in required), agent_name
            assert not any(token in prompt for token in forbidden), agent_name

    def test_registered_reviewer_definitions_do_not_restore_raw_output_paths(self):
        stale = "Use ReviewOutputBuilder per shared protocol. Write to"
        canonical = (
            "Use the bootstrap-provided ReviewOutputBuilder lifecycle. "
            "Save the complete draft"
        )

        raw_reviewers = set(ALL_AGENTS) - {
            "decision-reviewer",
            "repo-reviewer-adapter",
        }
        for agent_name in sorted(raw_reviewers):
            definition = (PLUGIN_ROOT / "agents" / f"{agent_name}.md").read_text()
            assert stale not in definition, agent_name
            assert canonical in definition, agent_name
            assert "builder.set_assessment(" not in definition, agent_name

    def test_shared_protocol_teaches_the_complete_draft_lifecycle(self):
        protocol = (PLUGIN_ROOT / "agents/shared/reviewer-protocol.md").read_text()

        for phrase in (
            "rehydrates the existing complete draft",
            "DRAFT TOTALS",
            "full persisted draft",
            "CHANGED",
            "current invocation",
            "no claimable review files remain unclaimed",
            "separate tool turn",
            "verbatim",
            "Raw reviewers must not call `set_assessment()`",
        ):
            assert phrase in protocol

        lifecycle_section = protocol.split(
            "## Canonical Draft Lifecycle", 1
        )[1].split("\n## ", 1)[0]
        lifecycle = [
            "ReviewOutputBuilder.open",
            "builder.add_finding",
            "builder.record_check",
            "builder.claim_files_reviewed",
            "builder.save_draft",
            "FINALIZE REVIEW",
        ]
        positions = [lifecycle_section.index(token) for token in lifecycle]
        assert positions == sorted(positions)

    def test_tests_protocol_requires_structured_evidence_for_material_negatives(self):
        protocol = (
            PLUGIN_ROOT / "agents/shared/tests-reviewer-protocol.md"
        ).read_text()

        assert "material negative" in protocol
        assert "builder.record_check(" in protocol

    def test_continuation_index_precedes_the_executable_builder_snippet(
        self, tmp_path
    ):
        from review.agent.output import ReviewOutputBuilder

        (tmp_path / "security-review-accounting-input.json").write_text(
            json.dumps({
                "schema": 4,
                "agent_name": "security-reviewer",
                "reviewer": "security",
                "review_claimable_files": [
                    "src/service.py", "tests/test_service.py",
                ],
                "review_budget": 15,
                "inline_diff_file_count": 1,
                "in_scope_review_file_count": 3,
                "channels": ["blocking"],
            })
        )
        builder = ReviewOutputBuilder.open(tmp_path, "42", "security")
        finding_id = builder.add_finding(
            severity="medium",
            title="Existing finding",
            file="src/code.py",
            line=7,
            description="Description",
            recommendation="Recommendation",
        )
        file_scoped_id = builder.add_finding(
            severity="medium",
            title="Existing file-scoped finding",
            file="tests/test_code.py",
            line=None,
            description="Description",
            recommendation="Recommendation",
        )
        builder.claim_files_reviewed(
            "src/service.py", "tests/test_service.py"
        )
        builder.save_draft()

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
            review_claimable_count=0,
            has_php=False,
        )

        assert f"finding {finding_id}" in prompt
        assert 'src/code.py:7' in prompt
        assert f"finding {file_scoped_id}" in prompt
        assert "tests/test_code.py (file scope)" in prompt
        assert "reviewed-file claim: src/service.py" in prompt
        assert "reviewed-file claim: tests/test_service.py" in prompt
        assert prompt.index("DRAFT INDEX:") < prompt.index(
            "ReviewOutputBuilder — MUST use"
        )

    def test_first_use_bootstrap_omits_the_continuation_index(self, tmp_path):
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
            review_claimable_count=0,
            has_php=False,
        )

        assert "DRAFT INDEX:" not in prompt

    def test_envelope_carries_the_plugin_version_assignment(self, tmp_path):
        """The producing plugin version travels in the same envelope.

        Emitted unconditionally, empty when unresolved: it is a fact that
        is sometimes unknown, never one that is sometimes absent, and the
        transcript analyzers recognize the builder command by its
        assignment names.
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
            review_claimable_count=0,
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
            review_claimable_count=0,
            has_php=False,
        )
        assert "PIRATEGOAT_PLUGIN_VERSION=''" in prompt

    def test_output_dir_is_taught_as_an_artifact_only_namespace(self, tmp_path):
        """Scratch work has a home, and the briefing has to name it.

        A field run had a reviewer awk-slice its scoped diff into three
        ad-hoc .patch files inside OUTPUT_DIR. The technique was sound; the
        location was never taught, and the only $TMPDIR mention reaching a
        reviewer was buried in a protocol probe example.
        """
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
            review_claimable_count=0,
            has_php=False,
        )
        assert "OUTPUT_DIR accepts only your named artifacts" in prompt
        assert "goes in $TMPDIR" in prompt

    @pytest.mark.parametrize("review_budget", [80, None])
    def test_envelope_never_carries_a_budget_assignment(
        self, tmp_path, review_budget
    ):
        """The budget travels in the accounting input, never the
        builder envelope — the retired env-var budget transport silently
        died for any agent that rebuilt its save command (run12's worst
        under-spender, 15% of target, never saw the TARGET echo). The
        envelope must carry exactly its five known assignments (plugin
        root, output dir, reviewer name, PR id, plugin version) and never
        a sixth, regardless of whether the run calibrated a budget.
        """
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
            review_claimable_count=0,
            has_php=False,
            review_budget=review_budget,
        )
        assert prompt.count("python3 <<'PY'") == 1
        command_start = prompt.index("PIRATEGOAT_PLUGIN_ROOT=")
        command_end = prompt.index("python3 <<'PY'", command_start)
        assignment_line = prompt[command_start:command_end]
        assert assignment_line.count("PIRATEGOAT_") == 5

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
            review_claimable_count=0,
            has_php=False,
        )

        assert "builder.mark_not_applicable(" in prompt
        assert "builder.save_draft()" in prompt
        assert "FINALIZE REVIEW" in prompt
        assert "REVIEW FINALIZED" in prompt
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
            review_claimable_count=0,
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
            review_claimable_count=0,
            has_php=False,
        )

        assert "DRAFT TOTALS" in prompt

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
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{reviewer_name}-review-accounting-input.json").write_text(
                json.dumps({
                    "schema": 4,
                    "agent_name": agent_name,
                    "reviewer": reviewer_name,
                    "review_claimable_files": [],
                    "review_budget": 15,
                    "inline_diff_file_count": 2,
                    "in_scope_review_file_count": 2,
                    "channels": ["blocking"],
                })
            )
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
                review_claimable_count=0,
                has_php=False,
            )
            start = prompt.index("PIRATEGOAT_PLUGIN_ROOT=")
            end = prompt.index("\nPY", start) + len("\nPY")
            invocations.append(prompt[start:end])

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
        assert all("DRAFT TOTALS:" in result.stdout for result in results)
        finalize_results = []
        for result in results:
            finalize_command = next(
                line.removeprefix("FINALIZE REVIEW: ")
                for line in result.stdout.splitlines()
                if line.startswith("FINALIZE REVIEW: ")
            )
            finalize_results.append(subprocess.run(
                ["bash", "-c", finalize_command],
                cwd=tmp_path,
                timeout=30,
                capture_output=True,
                text=True,
            ))
        assert all(result.returncode == 0 for result in finalize_results)
        assert all(
            "REVIEW FINALIZED" in result.stdout for result in finalize_results
        )
        for reviewer_name in ("security", "performance"):
            saved = json.loads(
                (output_dir / f"{reviewer_name}-review.json").read_text()
            )
            assert saved["reviewer"] == reviewer_name
            assert saved["pr_id"] == "42"
            assert saved["review_accounted_file_count"] == 2

    def test_bootstrap_heredoc_executes_with_shell_sensitive_paths(self, tmp_path):
        """Bootstrap must hand paths to stdin Python without literal interpolation."""
        plugin_root = tmp_path / "plugin root's copy"
        shutil.copytree(PLUGIN_ROOT / "scripts", plugin_root / "scripts")
        output_dir = tmp_path / "reviewer's output folder"
        output_dir.mkdir(parents=True)
        (output_dir / "security-review-accounting-input.json").write_text(json.dumps({
            "schema": 4,
            "agent_name": "security-reviewer",
            "reviewer": "security",
            "review_claimable_files": [],
            "review_budget": 15,
            "inline_diff_file_count": 3,
            "in_scope_review_file_count": 3,
            "channels": ["blocking"],
        }))
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
            review_claimable_count=0,
            has_php=False,
        )
        start = prompt.index("PIRATEGOAT_PLUGIN_ROOT=")
        end = prompt.index("\nPY", start) + len("\nPY")
        shell_example = prompt[start:end]
        python_files_before = set(tmp_path.rglob("*.py"))

        result = subprocess.run(
            ["bash", "-c", shell_example],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert "DRAFT TOTALS:" in result.stdout
        finalize_command = next(
            line.removeprefix("FINALIZE REVIEW: ")
            for line in result.stdout.splitlines()
            if line.startswith("FINALIZE REVIEW: ")
        )
        final = subprocess.run(
            ["bash", "-c", finalize_command],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert final.returncode == 0, final.stderr
        assert "REVIEW FINALIZED" in final.stdout
        saved = json.loads((output_dir / "security-review.json").read_text())
        assert saved["review_accounted_file_count"] == 3
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


class TestDecisionReviewerContract:
    def test_record_content_provenance_is_explicit(self):
        critic = (PLUGIN_ROOT / "agents/decision-reviewer.md").read_text().lower()

        assert "mechanically assembled" in critic
        assert "no model edits it after assembly" in critic
        assert "initial findings, assessment, and verified checks" in critic
        assert "reconciliator-authored `review-findings.json`" in critic
        assert "pipeline supplies measurements and run notes" in critic
        assert "`findings[].critic_adjustment`" in critic
        assert "`applied_critic_adjustments`" in critic
        assert "`rejected_critic_adjustments`" in critic
        assert "`invalidated_assessments`" in critic
        assert "inspect these audit fields" in critic
        assert "nothing in it was authored by an agent" not in critic

    def test_live_ledger_guidance_uses_findings_and_verified_checks(self):
        critic = (PLUGIN_ROOT / "agents/decision-reviewer.md").read_text()

        assert "stable `fN` `id`" in critic
        assert "`findings[].id`" in critic
        assert "`## Verified Checks`" in critic
        assert "8-hex" not in critic
        assert "`issues[].id`" not in critic
        assert "`## Clearances" not in critic

    def test_critic_owns_only_schema_two_finding_and_check_proposals(self):
        critic = (PLUGIN_ROOT / "agents/decision-reviewer.md").read_text()

        assert "schema 2" in critic
        assert '"kind": "finding"' in critic
        assert '"kind": "check"' in critic
        assert "The orchestrator owns settlement" in critic
        assert "Do not supply target ids for `add`" in critic
        for pipeline_owned in (
            "`source_reviewers`",
            "`adjustment_id`",
            "`applied`",
            "`revised_assessment`",
        ):
            assert pipeline_owned in critic


class TestRepoReviewerAdapterContract:
    def test_empty_review_uses_the_same_draft_finalization_flow(self):
        adapter = (
            PLUGIN_ROOT / "agents/repo-reviewer-adapter.md"
        ).read_text()
        empty_branch = adapter.split(
            "If the repo prompt produced no findings", 1
        )[1].split("\n- ", 1)[0]

        assert "`save_draft()`" in empty_branch
        assert "compact receipt" in empty_branch
        assert "exact printed `FINALIZE REVIEW` command verbatim" in empty_branch
        assert "`finalize_review_command`" not in empty_branch
        assert "`save()`" not in empty_branch
        assert "standard pirategoat finding" in adapter
        assert "Tag EVERY finding" in adapter
        assert "standard pirategoat issue" not in adapter
        assert "Tag EVERY issue" not in adapter

    def test_example_runs_the_printed_finalization_command_verbatim(self):
        adapter = (
            PLUGIN_ROOT / "agents/repo-reviewer-adapter.md"
        ).read_text()

        assert "receipt = builder.save_draft()" not in adapter
        assert "builder.save_draft()" in adapter
        assert "exact printed `FINALIZE REVIEW` command verbatim" in adapter
        assert "separate tool turn" in adapter


class TestReconcilerReviewDomainOwnership:
    def test_reconciler_carries_complete_structured_reviewer_evidence(self):
        reconciler = (
            PLUGIN_ROOT / "agents/review-reconciliator.md"
        ).read_text()

        for token in (
            "reviews_by_agent",
            "positive_observations",
            "review_accounting",
            "builder.set_assessment(",
            "builder._record_check(",
            "source_reviewers=",
        ):
            assert token in reconciler
        assert "ReviewOutputBuilder(pr_id=" in reconciler
        assert "ReviewOutputBuilder.open(" not in reconciler
        assert "builder.add_positive_observation(" in reconciler


class TestAPIContractReviewerReturnSideHooks:
    """Regression guard for caller-side handling of filter return values."""

    @staticmethod
    def _prompt() -> str:
        return (PLUGIN_ROOT / "agents/api-contract-reviewer.md").read_text().lower()

    def test_compares_returned_value_handling_before_and_after_diff(self):
        prompt = self._prompt()

        assert "compare the caller's handling" in prompt
        assert "returned value before and after the diff" in prompt

    def test_includes_concrete_post_filter_processing_break(self):
        prompt = self._prompt()

        assert "apply_filters() remains present" in prompt
        assert "removed normalization" in prompt
        assert "hook-contract-break" in prompt

    def test_treats_undocumented_established_runtime_behavior_as_contract(self):
        prompt = self._prompt()

        assert "established runtime behavior" in prompt
        assert "even when the hook docblock does not document it" in prompt

    def test_accepts_established_behavior_evidence_without_direct_consumer_code(self):
        prompt = self._prompt()

        assert (
            "pre-diff implementation or tests can establish changed observable "
            "behavior without direct consumer code"
        ) in prompt
        assert (
            "when implementation and test evidence are absent, require existing "
            "consumer code"
        ) in prompt

    def test_requires_evidence_before_internal_refactoring_dismissal(self):
        prompt = self._prompt()

        assert "concrete evidence" in prompt
        assert "observable result is unchanged" in prompt


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
        assert "`add_finding()` at Low or Medium" in text


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

    def test_reconciliator_treats_check_conflicts_as_verification_targets(self):
        """A check that contradicts a finding is resolved by verifying
        the finding, not by counting sides.

        Pinned on the rule's meaning rather than its old heading text
        ("check vs. finding"), which moved when the method-adequacy
        judgment was lifted out to apply to EVERY check — the wording
        can change, this contract cannot.
        """
        text = (PLUGIN_ROOT / "agents/review-reconciliator.md").read_text()
        assert "contradicts a finding" in text
        assert "never a vote" in text
        # And the judgment that voids a bad-method check is not gated
        # on some finding having disagreed with it first.
        assert "Judge EVERY check by its method" in text

    def test_protocol_requires_record_check_for_absence_claims(self):
        text = (PLUGIN_ROOT / "agents/shared/reviewer-protocol.md").read_text()
        assert "record_check" in text

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
            review_claimable_count=0,
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
            review_claimable_count=0,
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
            review_claimable_count=0,
            has_php=False,
        )

    def test_output_contains_add_finding_example(self, tmp_path):
        """The usage example must show add_finding() with named parameters."""
        output = self._build(tmp_path)
        assert "add_finding(" in output
        assert "severity=" in output
        assert "title=" in output
        assert "file=" in output
        assert "description=" in output
        assert "recommendation=" in output
        assert "FILE-SCOPED finding" in output
        assert "FILE-SCOPED issue" not in output

    def test_output_contains_add_positive_example(self, tmp_path):
        """The usage example must show add_positive_observation()."""
        output = self._build(tmp_path)
        assert "add_positive_observation(" in output

    def test_output_contains_bound_save_draft_example(self, tmp_path):
        """The example opens against output_dir and saves without a path."""
        output = self._build(tmp_path)
        assert "ReviewOutputBuilder.open(" in output
        assert "save_draft()" in output
        assert str(tmp_path) in output

    def test_output_uses_positive_claims_as_the_only_coverage_input(self, tmp_path):
        output = self._build(tmp_path)
        assert 'builder.claim_files_reviewed("path/read1.py", "path/read2.py")' in output
        assert "builder.add_un" + "reviewed" not in output
        assert "builder.set_files_" + "reviewed" not in output

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
            review_claimable_count=0,
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
            review_claimable_count=0,
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
                review_claimable_count=0,
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
            review_claimable_count=0,
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
        assert 'add_finding(..., channel="advisory")' in result.stdout

        accounting_input = json.loads(
            (tmp_path / "performance-review-accounting-input.json").read_text()
        )
        assert accounting_input["channels"] == ["blocking", "advisory"]
        assert not (tmp_path / "performance-advisory-entitlement.json").exists()

        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "performance")
        builder = ReviewOutputBuilder(pr_id="1", reviewer="performance")
        builder.add_finding(
            severity="high", title="Advisory", file="src/app.py",
            description="d", recommendation="r", line=1,
            channel="advisory",
        )
        assert builder.to_dict()["verdict"] == "approve"

    def test_accounting_input_carries_channels_and_no_sidecar(self, tmp_path):
        """The accounting input is now the sole carrier of channel
        entitlement — bootstrap no longer writes a separate advisory
        entitlement sidecar."""
        self._write_review_context(tmp_path, rules=[self._rule(
            tmp_path, "adv-rule", "ADVISORY BODY", channel="advisory",
        )])
        result = run_bootstrap(
            "--agent", "performance-reviewer", "--output-dir", str(tmp_path)
        )
        assert result.returncode == 0

        data = json.loads(
            (tmp_path / "performance-review-accounting-input.json").read_text()
        )
        assert data["schema"] == 4
        assert data["channels"] == ["blocking", "advisory"]
        assert isinstance(data["review_budget"], int)
        assert not (tmp_path / "performance-advisory-entitlement.json").exists()

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

        accounting_input = json.loads(
            (tmp_path / "performance-review-accounting-input.json").read_text()
        )
        assert accounting_input["channels"] == ["blocking"]
        assert not (tmp_path / "performance-advisory-entitlement.json").exists()

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

    def test_path_rule_matches_a_budget_claimable_file(self, tmp_path):
        """A rule about a NOT DIFFED file applies precisely when the
        reviewer must inspect that file — selection must see the complete
        in-scope set, not only the inline diff list."""
        repo = tmp_path / "repo"
        self._make_repo(repo, {
            "alpha.php": "<?php\n" + "\n".join(
                f"echo {i};" for i in range(3000)
            ) + "\n",
            "claimable_target.php": "<?php\n" + "\n".join(
                f"print({i});" for i in range(2500)
            ) + "\n",
        })
        outdir = tmp_path / "out"
        outdir.mkdir()
        self._write_review_context(outdir, rules=[self._rule(
            outdir, "claimable-rule", "CLAIMABLE FILE RULE MARKER",
            applies_to={
                "agents": [], "domains": [],
                "paths": ["claimable_target.php"],
            },
        )])
        result = self._run_in_repo(
            repo, "--agent", "code-reviewer", "--output-dir", str(outdir)
        )
        assert result.returncode == 0
        assert "claimable_target.php" in extract_not_diffed_files(result.stdout)
        assert "CLAIMABLE FILE RULE MARKER" in result.stdout

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
    """Draft save and immutable finalization use distinct filenames."""

    def test_save_stages_draft_then_finalization_publishes_final(
        self, tmp_path
    ):
        """save_draft() stages a draft; finalization publishes the review."""
        from review.agent.output import ReviewOutputBuilder, finalize_review

        (tmp_path / "dead-code-review-accounting-input.json").write_text(json.dumps({
            "schema": 4,
            "agent_name": "dead-code-reviewer",
            "reviewer": "dead-code",
            "review_claimable_files": [],
            "review_budget": 15,
            "inline_diff_file_count": 1,
            "in_scope_review_file_count": 1,
            "channels": ["blocking"],
        }))
        builder = ReviewOutputBuilder.open(str(tmp_path), "42", "dead-code")
        result = builder.save_draft()

        assert set(result) == {
            "draft", "review_digest", "finalize_review_command"
        }
        assert result["draft"].endswith("dead-code-review.draft.json")
        draft = Path(result["draft"])
        final = tmp_path / "dead-code-review.json"
        assert draft.is_file()
        assert not final.exists()

        finalized = finalize_review(
            str(tmp_path), "dead-code", result["review_digest"]
        )
        assert finalized["final"] == str(final)
        assert final.is_file()
        assert not draft.exists()
        assert not os.path.exists(os.path.join(str(tmp_path), "dead-code-review.md"))

    def test_bootstrap_output_names_finalized_file_not_draft(self, tmp_path):
        """Bootstrap OUTPUT_FILES must name the finalized review JSON,
        and no Markdown the pipeline derives elsewhere.

        This checks the briefing TEXT only (what the agent is told to produce);
        the draft/finalization filesystem contract is covered above.
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
            review_claimable_count=0,
            has_php=False,
        )
        assert f"{tmp_path}/dead-code-review.json" in output
        assert f"{tmp_path}/dead-code-review.draft.json" not in output
        assert "run the exact FINALIZE REVIEW command printed by" in output
        assert f"{tmp_path}/dead-code-review.md" not in output

    def test_testing_inventory_names_draft_lifecycle_contract(self):
        testing_doc = (TESTS_DIR / "TESTING.md").read_text()
        row = next(
            line for line in testing_doc.splitlines()
            if "`TestOutputFilenameConsistency`" in line
        )

        assert "draft" in row
        assert "finalization" in row
        assert "match bootstrap expectations" not in row


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
    claimable-file count by regexing its OWN rendered scope text for
    '=== NOT DIFFED (budget exceeded, N files) ===' — a second, independent
    text-parsing path duplicating the one load_scope_facts()/main() already
    used. Any rename or reformat of that header in scope.py silently zeroed
    the count and dropped the entire honesty contract, with no error and
    (because these tests hardcoded the same header text the regex expected)
    no test failure either. build_output() now receives review_claimable_count as
    an explicit fact from the caller and never inspects scope_output for it.
    """

    NOT_DIFFED_SCOPE = (
        "=== REVIEW SCOPE ===\n"
        "=== FILES ===\n"
        "src/big.py  (+900 -10)\n"
        "=== NOT DIFFED (budget exceeded, 3 files) ===\n"
        "  src/big.py  (+900 -10)\n"
    )

    def _build(self, tmp_path, scope_output, review_claimable_count, **kwargs):
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
            review_claimable_count=review_claimable_count,
            review_budget=80,
            **kwargs,
        )

    @pytest.mark.parametrize(
        "phrase",
        [
            'builder.claim_files_reviewed("<path>")',
            "authoritative review-accounting input",
            "derives every unclaimed review file",
            "Never count an unclaimed review file toward your verdict",
        ],
    )
    def test_contract_reaches_reviewer(self, tmp_path, phrase):
        """Each clause of the contract appears in the delivered briefing."""
        output = self._build(tmp_path, self.NOT_DIFFED_SCOPE, review_claimable_count=1)
        assert phrase in output

    def test_scope_and_guidance_never_teach_gap_declarations(self, tmp_path):
        scope_output = format_text_output({
            "status": "OK",
            "range": "base..head",
            "domain": "code",
            "files": ["src/inline.py"],
            "diffs": {"src/inline.py": "diff --git"},
            "diffstat": {
                "src/inline.py": (2, 1),
                "src/claimable.py": (20, 2),
            },
            "skipped_files": {"budget": ["src/claimable.py"]},
        })
        output = self._build(tmp_path, scope_output, review_claimable_count=1)
        protocol = (
            PLUGIN_ROOT / "agents" / "shared" / "reviewer-protocol.md"
        ).read_text()

        for delivered in (scope_output, output, protocol):
            assert "declare only the files" not in delivered.lower()
            assert "claim or declare" not in delivered.lower()
            assert "what a declaration costs" not in delivered.lower()

    @pytest.mark.parametrize(
        "phrase",
        [
            "false statement",
            "Declaring is for genuine budget exhaustion only",
            "written with most of your budget unspent",
        ],
    )
    def test_unenforceable_underspend_rule_is_not_restored(
        self, tmp_path, phrase
    ):
        """The under-spend "protocol violation" sentence must stay deleted.

        It conditioned on a quantity no reviewer is ever shown at the moment
        it decides — models keep no running tool-call tally — and a 19-agent
        field run delivered it verbatim to every one of them for zero effect
        (0/19 reached target, median 44% spent, nine declaring 100+ files
        while under half budget). Its premise was falsified in the same run:
        under-spend did not predict weak output. The replacement is salience
        at the decision point (save()'s TARGET echo), not sterner prose.
        """
        output = self._build(tmp_path, self.NOT_DIFFED_SCOPE, review_claimable_count=1)
        assert phrase not in output

    def test_contract_absent_without_not_diffed_files(self, tmp_path):
        """No NOT DIFFED files means no positive-claim contract to deliver."""
        clean_scope = "=== REVIEW SCOPE ===\n=== FILES ===\nsrc/a.py  (+5 -1)\n"
        output = self._build(tmp_path, clean_scope, review_claimable_count=0)
        assert "authoritative review-accounting input" not in output

    def test_contract_is_not_sourced_from_stripped_protocol(self):
        """The stripped protocol must not be the contract's only home.

        extract_protocol_sections() drops '## Scope Discovery', so anything
        placed there is invisible to reviewers by construction.
        """
        protocol = (PLUGIN_ROOT / "agents" / "shared" / "reviewer-protocol.md").read_text()
        delivered = _mod.extract_protocol_sections(
            protocol, _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert "authoritative review-accounting input" not in delivered, (
            "Contract text placed in a stripped protocol section never reaches "
            "a reviewer — keep it in build_output()'s REVIEW BUDGET block."
        )

    def test_renamed_scope_header_cannot_suppress_a_real_count(self, tmp_path):
        """A scope.py header rename/reformat must not silently drop the contract.

        This is the exact failure shape being fixed: the old regex expected
        the literal string '=== NOT DIFFED (budget exceeded, N files) ===' in
        scope_output. Here that header is renamed to something a future
        scope.py refactor might plausibly emit, and NO section matches the
        old pattern at all — yet because the caller still supplies the real
        fact via review_claimable_count, the contract must still be delivered.
        """
        renamed_header_scope = (
            "=== REVIEW SCOPE ===\n"
            "=== FILES ===\n"
            "src/big.py  (+900 -10)\n"
            "=== CLAIMABLE (too large to inline, 3 files) ===\n"
            "  src/big.py  (+900 -10)\n"
        )
        assert "NOT DIFFED" not in renamed_header_scope  # the old regex's anchor is gone
        output = self._build(tmp_path, renamed_header_scope, review_claimable_count=1)
        assert "authoritative review-accounting input" in output
        assert "derives every unclaimed review file" in output

    def test_original_header_text_alone_no_longer_drives_the_contract(self, tmp_path):
        """The rendered header text must never re-enable the contract by itself.

        NOT_DIFFED_SCOPE carries the exact header the old regex parsed, but
        review_claimable_count is explicitly 0 (the caller's fact says nothing was
        claimable). If build_output() still read scope_output text for this
        decision, the contract would incorrectly appear. It must not.
        """
        output = self._build(tmp_path, self.NOT_DIFFED_SCOPE, review_claimable_count=0)
        assert "authoritative review-accounting input" not in output

    def test_briefing_never_commands_bulk_unclaimed_enumeration(self, tmp_path):
        """run12: performance-reviewer burned ~1/3 of its calls hand-assembling
        254 unclaimed paths because the briefing said 'Declare each file you
        could not reach' — the builder already derives them for free."""
        output = self._build(tmp_path, self.NOT_DIFFED_SCOPE, review_claimable_count=3)
        assert "Declare each file you could not reach" not in output
        assert "derives every unclaimed review file" in output


class TestReviewClaimableOrderingEndToEnd:
    """The accounting input's claimable list is largest-first end to end.

    load_scope_facts() reads budget_exceeded_files straight off the
    scope-summary sidecar in PRIORITY-TIER order (production files before
    test files, regardless of size, for domains with budget_priority
    "production_first") — not pure size order. A small production file can
    therefore land ahead of a much larger test file in the raw list. This
    reproduces that exact divergence with a real git repo and checks
    bootstrap's own re-sort (order_by_diffstat_largest_first) fixes it in
    the sidecar it persists — the same sidecar output.py's save() replays
    for the NEXT UNREAD echo.
    """

    def _repo_with_priority_tier_divergence(self, repo_dir):
        """A small production file and a much larger test file, sized so
        both exceed history-insights-reviewer's 500-line max (registry
        budget_override does not affect scope.py's own --max-lines
        claimability, only the tool-call target build_output later reports)."""
        os.makedirs(os.path.join(repo_dir, "src"), exist_ok=True)
        os.makedirs(os.path.join(repo_dir, "tests"), exist_ok=True)

        def write(relpath, n_lines):
            with open(os.path.join(repo_dir, relpath), "w") as f:
                f.write("\n".join(f"line {i}" for i in range(n_lines)) + "\n")

        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo_dir, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo_dir, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "commit.gpgsign", "false"],
            cwd=repo_dir, capture_output=True, check=True,
        )
        for relpath, n in [
            ("src/huge_prod.py", 5), ("src/small_prod.py", 5),
            ("tests/huge_test.py", 5),
        ]:
            write(relpath, n)
        subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo_dir, capture_output=True, check=True
        )
        # huge_prod (485) nearly exhausts the 500-line budget; small_prod
        # (35, still production tier) is processed next and made claimable;
        # only then does the test tier run, making huge_test (405) claimable — larger
        # than small_prod but ordered after it by the priority tier alone.
        for relpath, n in [
            ("src/huge_prod.py", 485), ("src/small_prod.py", 35),
            ("tests/huge_test.py", 405),
        ]:
            write(relpath, n)
        subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "changes"], cwd=repo_dir, capture_output=True, check=True
        )

    def test_accounting_claimable_files_are_largest_first_despite_priority_tiering(
        self, tmp_path
    ):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        self._repo_with_priority_tier_divergence(str(repo_dir))
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = subprocess.run(
            [
                sys.executable, str(BOOTSTRAP_SCRIPT),
                "--agent", "history-insights-reviewer",
                "--output-dir", str(output_dir),
                "--range", "HEAD~1..HEAD",
            ],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0

        accounting_input = json.loads(
            (
                output_dir
                / "history-insights-review-accounting-input.json"
            ).read_text()
        )
        # Both claimable (the regression this guards): a same-tier-only
        # re-sort would still fail to fix the divergence, since these two
        # files are in DIFFERENT priority tiers.
        assert set(accounting_input["review_claimable_files"]) == {
            "src/small_prod.py", "tests/huge_test.py",
        }
        # Largest first, size overriding the priority tier that put the
        # smaller production file first in scope.py's own raw ordering.
        assert accounting_input["review_claimable_files"] == [
            "tests/huge_test.py", "src/small_prod.py",
        ]

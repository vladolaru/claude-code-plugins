"""Tests for review/agent/bootstrap.py — integration tests (subprocess runs against all agents)."""

from concurrent.futures import ThreadPoolExecutor
import importlib
import importlib.util
import json
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
from review import run_paths
from review.reviewer_lifecycle import (
    review_paths,
    scope_summary_path,
    scoped_diff_path,
)

# Import AGENT_CONFIG to derive ALL_AGENTS
_spec = importlib.util.spec_from_file_location("bootstrap_reviewer", str(BOOTSTRAP_SCRIPT))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
AGENT_CONFIG = _mod.AGENT_CONFIG
build_output = _mod.build_output
derive_reviewer_name = _mod.derive_reviewer_name

ALL_AGENTS = sorted(AGENT_CONFIG.keys())


def _write_telemetry_marker(output_dir, telemetry_log):
    marker = run_paths.artifact_path(output_dir, "telemetry_log_path")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(telemetry_log))


# ---------------------------------------------------------------------------
# Independent oracles for the rendered briefing
# ---------------------------------------------------------------------------
# Bootstrap derives every scope fact from scope.py's machine-readable summary
# sidecar. These parsers read the OTHER artifact the same run produces — the
# scope text scope.py renders for the reviewer to read — so a test asserting
# a sidecar-derived number against them checks the two agree. Deliberately
# test-local: production has exactly one authority for these facts, and a
# second one living in the tests is an oracle, not a source.


def _files_in_sections(scope_output, *header_prefixes):
    """Paths from every "path  (+N -M)" line under the given headers."""
    paths = []
    in_section = False
    for line in scope_output.splitlines():
        if line.startswith("==="):
            in_section = line.startswith(header_prefixes)
            continue
        match = re.match(r"\s*(.+?)\s{2,}\(\+\d+\s+-\d+\)", line)
        if in_section and match:
            paths.append(match.group(1).strip())
    return paths


def scope_files_in_text(scope_output):
    # Equals inline_diff_files only outside --base-ref-only / --summary mode,
    # where FILES lists routed files whose diffs were never fetched.
    return _files_in_sections(scope_output, "=== FILES ===")


def not_diffed_files_in_text(scope_output):
    return _files_in_sections(scope_output, "=== NOT DIFFED")


def list_only_files_in_text(scope_output):
    return _files_in_sections(scope_output, "=== CHANGED (no diff")


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


_IN_PROCESS_SCOPE = "STATUS: OK\n=== FILES ===\nsrc/a.py  (+1 -0)\n"
_IN_PROCESS_FACTS = {
    "inline_diff_files": ["src/a.py"],
    "review_claimable_files": [],
    "list_only_files": [],
    "in_scope_stat_lines": 10,
}


def _main_in_process(
    agent, tmp_path, monkeypatch, capsys,
    scope_output=_IN_PROCESS_SCOPE, facts=_IN_PROCESS_FACTS,
):
    """Run bootstrap's real main() in-process and return its stdout.

    Same harness as test_bootstrap.py::TestPartitionScopePaths: scope.py
    and its sidecar are stubbed (run_scope_discovery, load_scope_facts);
    everything else is real, including find_plugin_root() and the protocol
    files main() reads from this checkout.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        _mod, "run_scope_discovery", lambda *_args, **_kwargs: (0, scope_output)
    )
    monkeypatch.setattr(_mod, "load_scope_facts", lambda _paths: facts)
    monkeypatch.setattr(sys, "argv", [
        "bootstrap.py", "--agent", agent, "--range", "base..head",
        "--output-dir", str(tmp_path / "out"),
    ])
    with pytest.raises(SystemExit) as exc:
        _mod.main()
    out = capsys.readouterr().out
    assert exc.value.code == 0, out
    return out


def _delivered_protocol_headings(protocol, skip_prefixes):
    """The `## `/`### ` heading lines of `protocol` a reviewer must receive.

    A test-local oracle, deliberately not extract_protocol_sections(): it
    walks the protocol with the same skip list, dropping a skipped heading
    and every deeper heading under it, and ignoring `#` lines inside code
    fences.
    """
    headings = []
    skip_level = None
    in_fence = False
    for line in protocol.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        match = None if in_fence else re.match(r"(#{2,3}) ", line)
        if not match:
            continue
        level = len(match.group(1))
        if skip_level is not None and level > skip_level:
            continue
        skip_level = None
        if any(line.strip().startswith(prefix) for prefix in skip_prefixes):
            skip_level = level
            continue
        headings.append(line.strip())
    return headings


def _inline(count):
    """`count` distinct inline placeholder paths for a schema-5 assignment."""
    return [f"src/inline-{n}.php" for n in range(count)]

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
        # Step 1 stamps run-config.json and bootstrap forwards it: one
        # detector, so re-detecting the version here would be a second source.
        (tmp_path / "run-config.json").write_text(
            json.dumps({"mode": "pr", "plugin_version": "9.9.9"})
        )
        result = run_bootstrap("--agent", "performance-reviewer", "--output-dir", str(tmp_path))
        stdout = result.stdout
        assert result.returncode == 0
        assert "PIRATEGOAT_PLUGIN_VERSION=9.9.9" in stdout

        # Section structure (hardcoded in build_output template)
        assert "=== BOOTSTRAP: performance-reviewer ===" in stdout
        assert "--- Section 1: REVIEW RULES" in stdout
        assert "=== REVIEW RULES ===" in stdout
        assert "--- Section 2: REVIEW CONTENT" in stdout
        assert "--- Section 3: OUTPUT INSTRUCTIONS" in stdout
        assert "=== OUTPUT INSTRUCTIONS ===" in stdout

        # Personalization
        assert "REVIEWER_NAME: performance" in stdout
        reviewer_dir = tmp_path / "reviewers" / "performance"
        assert f"{reviewer_dir}/review.json" in stdout
        assert f"{reviewer_dir}/review.md" not in stdout
        assert "PIRATEGOAT_REVIEWER_NAME=performance" in stdout

        assert (reviewer_dir / "assignment.json").is_file()
        assert (reviewer_dir / "scope-summary.json").is_file()
        assert (reviewer_dir / "started").is_file()
        assert not list(tmp_path.glob("performance-*"))

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

        # The assignment persists the authoritative NOT DIFFED set so the
        # builder can reject claims that match no claimable file.
        data = json.loads(
            Path(review_paths(tmp_path, "performance").assignment).read_text()
        )
        assert sorted(data["review_claimable_files"]) == sorted(
            not_diffed_files_in_text(stdout)
        )
        # Closes the main()->build_output() seam: review_claimable_count must be
        # derived from this exact claimable set, not a neighboring fact
        # (e.g. total scope files) that also happens to be non-empty here.
        # A mis-wired count would pass every other assertion in this suite.
        assert ("Not reviewed (budget):" in stdout) == bool(
            data["review_claimable_files"]
        )

    def test_large_end_to_end_bootstrap_keeps_every_artifact_in_reviewer_directory(
        self, tmp_path
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=repo, check=True
        )
        source = repo / "large.py"
        source.write_text("value = 1\n")
        subprocess.run(["git", "add", "large.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
        source.write_text("".join(
            f"def function_{index}():\n    return {index}\n"
            for index in range(2500)
        ))
        subprocess.run(["git", "add", "large.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "change"], cwd=repo, check=True)
        output_dir = tmp_path / "out"

        result = subprocess.run(
            [
                sys.executable,
                str(BOOTSTRAP_SCRIPT),
                "--agent", "performance-reviewer",
                "--output-dir", str(output_dir),
                "--range", "HEAD~1..HEAD",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, result.stderr
        reviewer_dir = output_dir / "reviewers" / "performance"
        assert {
            "assignment.json", "scope-summary.json", "scoped-diff.patch", "started",
        } <= {path.name for path in reviewer_dir.iterdir()}
        assert not list(output_dir.glob("performance-*"))

    def test_ref_mode_instance_writes_scope_summaries_and_assignment(
        self, tmp_path
    ):
        """Adapter ref-mode instances must leave the same per-agent scope
        evidence as native reviewers — instance-named scope summaries (so
        run-level coverage reconciliation sees adapter scopes) and an
        instance-named assignment (so a builder bound to this output
        directory finds the instance's own assignment facts)."""
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
        summary = Path(scope_summary_path(tmp_path, "repo-renewals", "code"))
        assert summary.is_file()
        data = json.loads(summary.read_text())
        # The domain lives in the filename asserted above — the summary body
        # carries only the facts its readers consume.
        assert data["schema"] == 3
        assert isinstance(data["in_scope_stat_lines"], int)
        # Identity chain: the assignment is named for the reviewer
        # the instance is taught to construct its builder with.
        assert "PIRATEGOAT_REVIEWER_NAME=repo-renewals" in result.stdout
        assignment = json.loads(
            Path(review_paths(tmp_path, "repo-renewals").assignment).read_text()
        )
        assert assignment["channels"] == ["advisory"]

        builder = ReviewOutputBuilder.open(tmp_path, "1", "repo-renewals")
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
        _write_telemetry_marker(tmp_path, telemetry_log)
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
        the tier — a stray --model-tier flag must not override it — and the
        agent_start event carries the scope paths main() already parsed."""
        telemetry_log = tmp_path / "review.jsonl"
        telemetry_log.write_text(json.dumps({
            "schema": 1,
            "run_id": "run-1",
            "event": "pipeline_start",
            "pipeline": {"repo_path": _get_fixture_repo()},
        }) + "\n")
        _write_telemetry_marker(tmp_path, telemetry_log)

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
        # Telemetry scope covers the full in-scope set: inline FILES entries,
        # claimable NOT DIFFED paths (in-scope work whose diffs were withheld
        # for context budget), and list-only CHANGED (no diff) paths the
        # reviewer is told to inspect when relevant.
        expected_scope = sorted(set(
            scope_files_in_text(result.stdout)
            + not_diffed_files_in_text(result.stdout)
            + list_only_files_in_text(result.stdout)
        ))
        assert expected_scope
        assert agent_start["scope"]["paths"] == expected_scope

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
        assert review_paths(tmp_path, "php-tests").final in stdout
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
        """history-insights-reviewer gets FILE HISTORY + budget override.

        The assignment carries the effective (override-applied) budget and
        the scope counts save()'s PROGRESS line reads — the retired env-var
        budget transport silently died for any agent that rebuilt its save
        command, so the sidecar is the only carrier. The fixed override (45)
        proves it carries the FINAL number, not a scope-only figure a
        downstream reader would have to recompute.
        """
        result = run_bootstrap("--agent", "history-insights-reviewer", "--output-dir", str(tmp_path))
        stdout = result.stdout
        assert result.returncode == 0

        # History-specific: FILE HISTORY section present
        assert "=== FILE HISTORY ===" in stdout

        # Budget override: fixed value 45 (from registry), not scope-computed
        assert "Target: ~45 tool calls" in stdout

        # Personalization
        assert "REVIEWER_NAME: history-insights" in stdout

        data = json.loads(
            Path(review_paths(tmp_path, "history-insights").assignment).read_text()
        )
        assert data["schema"] == 5
        assert data["review_budget"] == 45
        assert data["channels"] == ["blocking"]
        assert "budget_capped" not in data

        diffed = scope_files_in_text(stdout)
        not_diffed = not_diffed_files_in_text(stdout)
        assert data["in_scope_review_file_count"] == len(
            dict.fromkeys([*diffed, *not_diffed])
        )
        assert set(data["inline_diff_files"]) == set(diffed)
        assert len(data["inline_diff_files"]) == len(set(diffed))

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

    def test_review_rules_identical_across_categories(
        self, tmp_path, monkeypatch, capsys
    ):
        """REVIEW RULES (shared protocol) must be identical for all agent categories.

        The protocol extraction uses the same file + same skip-list for every agent.
        If it produces different results, something is wrong with the extraction logic
        or the protocol file has agent-conditional content (which it should not).
        """
        rules = {}
        for agent in self._REPRESENTATIVE_AGENTS:
            stdout = _main_in_process(agent, tmp_path, monkeypatch, capsys)
            rules[agent] = self._extract_section(
                stdout, "=== REVIEW RULES ===",
                "=== DOMAIN RULES ===", "=== REVIEW BUDGET ===", "--- Section 2:",
            )

        reference = rules[self._REPRESENTATIVE_AGENTS[0]]
        assert reference, "REVIEW RULES section should not be empty"
        for agent in self._REPRESENTATIVE_AGENTS[1:]:
            assert rules[agent] == reference, (
                f"REVIEW RULES differ between {self._REPRESENTATIVE_AGENTS[0]} and {agent}"
            )

    def test_every_delivered_protocol_heading_reaches_the_prompt(
        self, tmp_path, monkeypatch, capsys
    ):
        """Every `## `/`### ` heading of the real reviewer-protocol.md outside
        the skip list reaches the prompt main() builds, verbatim.

        One guard for every section instead of one per section: a section
        the extractor drops, or main() loses on the way to build_output(),
        fails here by name, and a new section is covered the day it is
        added. Adding a heading to the skip list is a deliberate policy
        change this guard does not police; TestEmpiricalProbeContract pins
        the section that must never be skipped.
        """
        protocol = (PLUGIN_ROOT / "agents/shared/reviewer-protocol.md").read_text()
        expected = _delivered_protocol_headings(
            protocol, _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert any(heading.startswith("### ") for heading in expected), expected

        prompt_lines = set(
            _main_in_process(
                "code-reviewer", tmp_path, monkeypatch, capsys
            ).splitlines()
        )

        missing = [heading for heading in expected if heading not in prompt_lines]
        assert not missing, f"protocol headings missing from the prompt: {missing}"

    def test_domain_rules_identical_across_test_agents(
        self, tmp_path, monkeypatch, capsys
    ):
        """DOMAIN RULES (tests-reviewer protocol) must be identical for all test agents.

        All 4 test agents (php, js, e2e, go) must produce the same DOMAIN RULES.
        A registry drift removing tests-reviewer from any agent would be caught here.
        """
        agents = ["php-tests-reviewer", "js-tests-reviewer", "e2e-tests-reviewer", "go-tests-reviewer"]
        rules = {}
        for agent in agents:
            stdout = _main_in_process(agent, tmp_path, monkeypatch, capsys)
            rules[agent] = self._extract_section(
                stdout, "=== DOMAIN RULES ===",
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

        # The protocol is reference-only; bootstrap emits the one executable
        # builder command, envelope filled in.
        assert "python3 <<'PY'" not in protocol
        assert prompt.count("python3 <<'PY'") == 1
        assert f"PIRATEGOAT_PLUGIN_ROOT={PLUGIN_ROOT}" in prompt
        assert f"PIRATEGOAT_OUTPUT_DIR={tmp_path}" in prompt
        assert "PIRATEGOAT_REVIEWER_NAME=security" in prompt
        assert "PIRATEGOAT_PR_ID=42" in prompt
        assert (
            "builder = ReviewOutputBuilder.open("
            "output_dir, pr_id, reviewer_name)" in prompt
        )
        assert "DRAFT TOTALS" in prompt
        assert "run the exact FINALIZE REVIEW command printed by" in prompt

    def test_bootstrapped_reviewer_sees_only_the_canonical_contract(
        self, tmp_path
    ):
        """build_output()'s Section 3 does not branch on the agent, so one
        bootstrapped reviewer (security-reviewer) proves the contract for all."""
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
            review_claimable_count=1,
            has_php=False,
        )
        assert [token for token in required if token not in prompt] == []
        assert [token for token in forbidden if token in prompt] == []

    def test_registered_reviewer_definitions_do_not_restore_raw_output_paths(self):
        canonical = (
            "Use ReviewOutputBuilder per the shared protocol's "
            "Canonical Draft Lifecycle."
        )

        raw_reviewers = set(ALL_AGENTS) - {
            "decision-reviewer",
            "repo-reviewer-adapter",
        }
        for agent_name in sorted(raw_reviewers):
            definition = (PLUGIN_ROOT / "agents" / f"{agent_name}.md").read_text()
            assert canonical in definition, agent_name

    def test_shared_protocol_teaches_the_complete_draft_lifecycle(self):
        protocol = (PLUGIN_ROOT / "agents/shared/reviewer-protocol.md").read_text()

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
        location was never taught. The run now reserves OUTPUT_DIR/tmp/ for
        that scratch work while keeping the run root artifact-only.
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
        assert "goes in OUTPUT_DIR/tmp/" in prompt

    @pytest.mark.parametrize("review_budget", [80, None])
    def test_envelope_never_carries_a_budget_assignment(
        self, tmp_path, review_budget
    ):
        """The budget travels in the assignment, never the
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

        assert "MUST NOT create or write a temporary builder script with the Write tool" in prompt
        assert "NEVER inline `python3 -c" in prompt

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
            assignment_path = Path(review_paths(output_dir, reviewer_name).assignment)
            assignment_path.parent.mkdir(parents=True, exist_ok=True)
            assignment_path.write_text(
                json.dumps({
                    "schema": 5,
                    "agent_name": agent_name,
                    "reviewer": reviewer_name,
                    "review_claimable_files": [],
                    "review_budget": 15,
                    "inline_diff_files": _inline(2),
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
                Path(review_paths(output_dir, reviewer_name).final).read_text()
            )
            assert saved["reviewer"] == reviewer_name
            assert saved["pr_id"] == "42"
            assert saved["reviewed_file_count"] == 2

    def test_bootstrap_heredoc_executes_with_shell_sensitive_paths(self, tmp_path):
        """Bootstrap must hand paths to stdin Python without literal interpolation."""
        plugin_root = tmp_path / "plugin root's copy"
        shutil.copytree(PLUGIN_ROOT / "scripts", plugin_root / "scripts")
        output_dir = tmp_path / "reviewer's output folder"
        output_dir.mkdir(parents=True)
        assignment_path = Path(review_paths(output_dir, "security").assignment)
        assignment_path.parent.mkdir(parents=True, exist_ok=True)
        assignment_path.write_text(json.dumps({
            "schema": 5,
            "agent_name": "security-reviewer",
            "reviewer": "security",
            "review_claimable_files": [],
            "review_budget": 15,
            "inline_diff_files": _inline(3),
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
        saved = json.loads(Path(review_paths(output_dir, "security").final).read_text())
        assert saved["reviewed_file_count"] == 3
        assert set(tmp_path.rglob("*.py")) == python_files_before


class TestRepoReviewerAdapterContract:
    def test_empty_review_uses_the_same_draft_finalization_flow(self):
        """The adapter's empty-findings branch saves a draft and finalizes
        like every other reviewer instead of skipping publication."""
        adapter = (
            PLUGIN_ROOT / "agents/repo-reviewer-adapter.md"
        ).read_text()
        empty_branch = adapter.split(
            "If the repo prompt produced no findings", 1
        )[1].split("\n- ", 1)[0]

        assert "`save_draft()`" in empty_branch


class TestEmpiricalProbeContract:
    """The probe-naming convention must reach the reviewers that run code.

    The sweep in orchestration deletes only untracked files whose BASENAME
    carries `pirategoat-probe`. That enforcement half is inert unless the
    producer half — this protocol section — reaches an agent. Delivery of
    every non-skipped section is guarded by
    TestArchitecturalInvariants::test_every_delivered_protocol_heading_reaches_the_prompt;
    this class pins that the section never joins the skip list.
    """

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
        assert "=== BOOTSTRAP: nonexistent-reviewer ===" in result.stdout
        assert "STATUS: ERROR" in result.stdout
        assert "Unknown agent" in result.stdout
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

    def test_output_names_the_builder_api(self, tmp_path):
        output = self._build(tmp_path)
        for api in (
            "add_finding(",
            "add_positive_observation(",
            "save_draft()",
            "set_confidence(",
        ):
            assert api in output, api
        # Two rules the example carries that no other test pins.
        assert "FILE-SCOPED finding" in output
        assert "Do NOT read the output file back to verify" in output


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
        """When scope is truncated, output tells agent where to read the full
        scope and how to read it in slices."""
        output = self._build_large_output(scope_size_kb=50, output_dir=str(tmp_path))
        expected_path = Path(scoped_diff_path(tmp_path, "security"))
        assert str(expected_path) in output
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
        security_path = Path(scoped_diff_path(tmp_path, "security"))
        concurrency_path = Path(scoped_diff_path(tmp_path, "concurrency"))

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
    build_output() never parses scope_output for PHP filenames: the
    text-inert rows below pin the failure mode that replaced, and the
    in-process main() rows pin the derivation itself.
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

    @pytest.mark.parametrize(
        ("has_php", "scope_output", "expected"),
        [
            pytest.param(
                True, "=== FILES ===\n=== DIFFS ===", "high", id="php-fact-high",
            ),
            pytest.param(
                False, "=== FILES ===\n=== DIFFS ===", "low", id="no-php-fact-low",
            ),
            # Fix 6d99ab03: the old implementation derived has_php by
            # scanning rendered scope text for a '.php' suffix. PHP-looking
            # text must not force high when the caller's fact says low...
            pytest.param(
                False,
                "=== FILES ===\n"
                "src/handler.php  (+10 -5)\n"
                "src/other.php  (+3 -1)\n"
                "=== DIFFS ===",
                "low",
                id="php-looking-text-cannot-force-high",
            ),
            # ...and a scope.py reformat that loses the '.php' text must
            # not suppress high when the fact says PHP is in scope.
            pytest.param(
                True,
                "=== SCOPE TRUNCATED ===\n"
                "Full scope written to external file; see it for details.\n",
                "high",
                id="garbled-text-cannot-suppress-high",
            ),
        ],
    )
    def test_dispatch_risk_follows_the_has_php_fact(
        self, tmp_path, has_php, scope_output, expected
    ):
        output = self._build(tmp_path, has_php=has_php, scope_output=scope_output)
        risk_lines = [
            line for line in output.splitlines() if "DYNAMIC_DISPATCH_RISK:" in line
        ]
        assert risk_lines, "DYNAMIC_DISPATCH_RISK line not found in output"
        assert expected in risk_lines[0].lower()

    def test_other_agents_no_dispatch_risk(self, tmp_path):
        """Non-dead-code agents do NOT get DYNAMIC_DISPATCH_RISK, regardless of has_php."""
        output = self._build(tmp_path, has_php=True, agent_name="security-reviewer")
        assert "DYNAMIC_DISPATCH_RISK:" not in output

    @pytest.mark.parametrize(
        ("inline_files", "scope_output", "expected"),
        [
            pytest.param(
                ["src/a.php"], _IN_PROCESS_SCOPE, "high",
                id="php-file-in-scope-facts",
            ),
            pytest.param(
                ["src/a.ts"], _IN_PROCESS_SCOPE, "low",
                id="php-free-scope-facts",
            ),
            # The old text scan read the SKIPPED summary line as one token
            # and its '.php' suffix forced high; the facts carry only files
            # genuinely in scope, so a domain-excluded test file stays out.
            pytest.param(
                ["src/app.ts"],
                "STATUS: OK\n=== FILES ===\nsrc/app.ts  (+1 -0)\n"
                "=== SKIPPED ===\nOutside domain (1): tests/ProductManagerTest.php\n",
                "low",
                id="domain-excluded-php-test-file",
            ),
        ],
    )
    def test_main_derives_has_php_from_the_scope_facts(
        self, tmp_path, monkeypatch, capsys, inline_files, scope_output, expected
    ):
        """main()'s own has_php derivation, which the build_output() rows
        above cannot reach because they supply the fact as a parameter (a
        mutation such as `has_php = False` in main() passes them all)."""
        stdout = _main_in_process(
            "dead-code-reviewer", tmp_path, monkeypatch, capsys,
            scope_output=scope_output,
            facts={**_IN_PROCESS_FACTS, "inline_diff_files": inline_files},
        )
        assert f"DYNAMIC_DISPATCH_RISK: {expected}" in stdout


class TestRepoRuleAndRefModeSelection:
    """Repo rules must reach the reviewers they target (effective identity,
    complete scope), and adapter instances must receive their declared path
    scope. The refusal of an explicit isolation request is pinned at the unit
    level (test_bootstrap.py::TestResolveReviewerIdentity)."""

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

    def test_advisory_rule_injects_the_channel_contract(self, tmp_path):
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

        # The assignment is the sole carrier of the reviewer's channels.
        assignment = json.loads(
            Path(review_paths(tmp_path, "performance").assignment).read_text()
        )
        assert assignment["channels"] == ["blocking", "advisory"]

    def test_blocking_only_rules_omit_the_channel_contract(self, tmp_path):
        self._write_review_context(tmp_path, rules=[self._rule(
            tmp_path, "blk-rule", "BLOCKING BODY", channel="blocking",
        )])
        result = run_bootstrap(
            "--agent", "performance-reviewer", "--output-dir", str(tmp_path)
        )
        assert result.returncode == 0
        assert "BLOCKING BODY" in result.stdout
        assert "CHANNEL CONTRACT" not in result.stdout

        assignment = json.loads(
            Path(review_paths(tmp_path, "performance").assignment).read_text()
        )
        assert assignment["channels"] == ["blocking"]

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
        assert "claimable_target.php" in not_diffed_files_in_text(result.stdout)
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
        assert "docs/guide.md" in scope_files_in_text(result.stdout)


class TestOutputFilenameConsistency:
    """Draft save and immutable finalization use distinct filenames."""

    def test_bootstrap_output_names_finalized_file_not_draft(self, tmp_path):
        """Bootstrap OUTPUT_FILES must name the finalized review JSON,
        and no Markdown the pipeline derives elsewhere.

        This checks the briefing TEXT only (what the agent is told to produce);
        the draft/finalization filesystem contract is pinned in test_output.py
        (TestSaveDraft::test_creates_only_the_draft_json and
        TestDerivedReviewedFiles::test_finalized_json_preserves_derived_coverage).
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
        assert review_paths(tmp_path, "dead-code").final in output
        assert review_paths(tmp_path, "dead-code").draft not in output
        assert "run the exact FINALIZE REVIEW command printed by" in output
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


class TestNotDiffedContractIsDelivered:
    """The NOT DIFFED handling contract must survive protocol stripping.

    Regression guard for 1.109.0: the contract originally lived in
    reviewer-protocol.md's '## Scope Discovery' section, which bootstrap strips,
    so it never reached a single reviewer. Policy belongs in build_output.

    Regression guard for the 1.114.0 fix: build_output() used to re-derive the
    claimable-file count by regexing its OWN rendered scope text for
    '=== NOT DIFFED (budget exceeded, N files) ===' — a second text-parsing
    path for a fact the caller already held. Any rename or reformat of that
    header in scope.py silently zeroed the count and dropped the entire
    honesty contract, with no error and (because these tests hardcoded the
    same header text the regex expected) no test failure either.
    build_output() now receives review_claimable_count as an explicit fact
    from the caller and never inspects scope_output for it.
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
            "derives every unclaimed review file",
        ],
    )
    def test_contract_reaches_reviewer(self, tmp_path, phrase):
        """The claim call and the derived complement reach the briefing."""
        output = self._build(tmp_path, self.NOT_DIFFED_SCOPE, review_claimable_count=1)
        assert phrase in output

    @pytest.mark.parametrize(
        ("scope_text", "review_claimable_count", "delivered"),
        [
            pytest.param(
                "=== REVIEW SCOPE ===\n=== FILES ===\nsrc/a.py  (+5 -1)\n",
                0,
                False,
                id="no-claimable-files",
            ),
            # Fix 606519ab: the count is the caller's fact, never a regex over
            # scope_output. A renamed header cannot suppress a real count...
            pytest.param(
                "=== REVIEW SCOPE ===\n"
                "=== FILES ===\n"
                "src/big.py  (+900 -10)\n"
                "=== CLAIMABLE (too large to inline, 3 files) ===\n"
                "  src/big.py  (+900 -10)\n",
                1,
                True,
                id="renamed-header-cannot-suppress-a-real-count",
            ),
            # ...and the header the old regex parsed cannot enable it alone.
            pytest.param(
                NOT_DIFFED_SCOPE,
                0,
                False,
                id="original-header-text-alone-cannot-enable-it",
            ),
        ],
    )
    def test_contract_follows_the_claimable_count_not_the_scope_text(
        self, tmp_path, scope_text, review_claimable_count, delivered
    ):
        output = self._build(
            tmp_path, scope_text, review_claimable_count=review_claimable_count
        )
        assert ("authoritative review assignment" in output) is delivered
        assert ("derives every unclaimed review file" in output) is delivered
        assert (
            "Never count an unclaimed review file toward your verdict" in output
        ) is delivered

    def test_contract_is_not_sourced_from_stripped_protocol(self):
        """The stripped protocol must not be the contract's only home.

        extract_protocol_sections() drops '## Scope Discovery', so anything
        placed there is invisible to reviewers by construction.
        """
        protocol = (PLUGIN_ROOT / "agents" / "shared" / "reviewer-protocol.md").read_text()
        delivered = _mod.extract_protocol_sections(
            protocol, _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert "authoritative review assignment" not in delivered, (
            "Contract text placed in a stripped protocol section never reaches "
            "a reviewer — keep it in build_output()'s REVIEW BUDGET block."
        )


# Registry agents that are not dispatched through bootstrap.py. The critic
# receives the record and ledger paths in its dispatch prompt and runs
# critic.py; its definition only uses bootstrap.py's path to locate the
# plugin root. The cross-validators and the reconciliator are also not
# bootstrap-dispatched but are not in AGENT_CONFIG, so they need no entry —
# and an entry naming an unregistered agent fails the subset test below
# instead of silently exempting nothing.
BOOTSTRAP_EXEMPT_AGENTS = {
    "decision-reviewer",
}


class TestEveryReviewerMandatesBootstrap:
    """Run 6e6a: ecosystem-integration-reviewer lacked the MANDATORY SETUP
    section, read the bare bootstrap command as context, explored for 43
    calls, never saved, and was re-dispatched (8 % of subagent cost)."""

    def test_exempt_set_names_only_registered_agents(self):
        assert BOOTSTRAP_EXEMPT_AGENTS <= set(ALL_AGENTS), (
            BOOTSTRAP_EXEMPT_AGENTS - set(ALL_AGENTS)
        )

    @pytest.mark.parametrize("agent", ALL_AGENTS)
    def test_definition_contains_mandatory_bootstrap_section(self, agent):
        if agent in BOOTSTRAP_EXEMPT_AGENTS:
            pytest.skip("not dispatched through bootstrap")
        path = PLUGIN_ROOT / "agents" / f"{agent}.md"
        # Deliberately not a skip: a registry entry whose definition file is
        # missing is a worse version of the defect this test exists to catch.
        assert path.is_file(), f"{agent} is in the registry with no definition"
        text = path.read_text()
        assert "## MANDATORY SETUP — Run Bootstrap Before Reviewing" in text, agent
        assert f"bootstrap.py --agent {agent}" in text, agent


class TestBuilderSnippetSignatures:
    """Run e582 and 6e6a: both Opus reviewers called add_observation with
    one positional string, copied from the add_positive_observation
    example, and failed their first save with
    'missing 1 required positional argument: note'."""

    def _build(self, tmp_path, review_claimable_count):
        return build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="=== FILES ===\n=== DIFFS ===",
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number="42",
            reviewer_name="security",
            review_claimable_count=review_claimable_count,
            has_php=False,
        )

    def test_snippet_shows_add_observation_with_its_real_signature(self, tmp_path):
        output = self._build(tmp_path, review_claimable_count=0)
        assert 'builder.add_observation(file="path/to/file.py",' in output
        assert 'note="' in output
        assert 'category="tradeoff")' in output

    def test_claim_example_present_when_files_are_claimable(self, tmp_path):
        output = self._build(tmp_path, review_claimable_count=3)
        assert 'builder.claim_files_reviewed("path/read1.py", "path/read2.py")' in output
        assert "do not call claim_files_reviewed()" not in output

    def test_claim_example_replaced_when_nothing_is_claimable(self, tmp_path):
        output = self._build(tmp_path, review_claimable_count=0)
        assert 'builder.claim_files_reviewed("path/read1.py"' not in output
        assert "# No review-claimable files in this assignment: do not call claim_files_reviewed()." in output

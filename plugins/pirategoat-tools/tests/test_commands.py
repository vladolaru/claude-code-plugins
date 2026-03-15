"""
Tests for review command files — deterministic, no model calls.

Validates structural properties of command markdown files:
- Valid frontmatter
- Agent references match marketplace.json
- Script references exist on disk
- Agent dispatch consistency between commands
- State file schema grading
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
COMMANDS_DIR = PLUGIN_ROOT / "commands"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
REPO_ROOT = PLUGIN_ROOT.parent.parent
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"

sys.path.insert(0, str(TESTS_DIR))

from graders import grade_review_state

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REVIEW_COMMANDS = [
    "full-code-review.md",
    "code-review.md",
    "ingest-code-review.md",
]

# Commands that dispatch agents directly (have agent tables)
DISPATCH_COMMANDS = [
    "full-code-review.md",
    "code-review.md",
]

# Commands that orchestrate other commands/skills (no inline agent tables)
ORCHESTRATOR_COMMANDS = [
    "pr-review.md",
]

# All review-related commands (for shared structural tests)
ALL_REVIEW_COMMANDS = REVIEW_COMMANDS + ORCHESTRATOR_COMMANDS

# Agent name pattern in markdown tables: `pirategoat-tools:<name>`
AGENT_REF_PATTERN = re.compile(r"`pirategoat-tools:([\w-]+)`")

# Script reference pattern: scripts/<name>.py or bootstrap-reviewer.py or review-scope.py
SCRIPT_REF_PATTERN = re.compile(r"(?:scripts/|/)(\w[\w-]*\.py)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_command(filename: str) -> str:
    """Read a command file and return its content."""
    path = COMMANDS_DIR / filename
    return path.read_text()


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from a command file.

    Returns dict with parsed fields, or empty dict if no frontmatter.
    """
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    fm_text = content[3:end].strip()
    result = {}
    for line in fm_text.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def _extract_agent_refs(content: str) -> list:
    """Extract agent names from pirategoat-tools:xxx references in markdown."""
    return AGENT_REF_PATTERN.findall(content)


def _load_marketplace_agents() -> list:
    """Load agent list from marketplace.json for the pirategoat-tools plugin."""
    data = json.loads(MARKETPLACE_JSON.read_text())
    for plugin in data["plugins"]:
        if plugin["name"] == "pirategoat-tools":
            # Extract agent short names from paths like "./agents/pr-reviewer.md"
            return [
                Path(a).stem for a in plugin.get("agents", [])
            ]
    return []


def _load_marketplace_skills() -> list:
    """Load skill list from marketplace.json for the pirategoat-tools plugin."""
    data = json.loads(MARKETPLACE_JSON.read_text())
    for plugin in data["plugins"]:
        if plugin["name"] == "pirategoat-tools":
            # Extract skill short names from paths like "./skills/decision-critic"
            return [
                Path(s).name for s in plugin.get("skills", [])
            ]
    return []


def _load_marketplace_commands() -> list:
    """Load command list from marketplace.json for the pirategoat-tools plugin."""
    data = json.loads(MARKETPLACE_JSON.read_text())
    for plugin in data["plugins"]:
        if plugin["name"] == "pirategoat-tools":
            return [
                Path(c).name for c in plugin.get("commands", [])
            ]
    return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture(scope="module")
def marketplace_agents():
    """Agent names from marketplace.json (e.g., 'pr-reviewer', 'security-reviewer')."""
    return _load_marketplace_agents()


@pytest.fixture(scope="module")
def marketplace_skills():
    """Skill names from marketplace.json (e.g., 'decision-critic', 'reviewing-pr')."""
    return _load_marketplace_skills()


@pytest.fixture(scope="module")
def marketplace_commands():
    """Command filenames from marketplace.json."""
    return _load_marketplace_commands()


# =============================================================================
# Structural Tests — Frontmatter
# =============================================================================


class TestFrontmatter:
    """All review commands have valid frontmatter."""

    @pytest.mark.parametrize("command", ALL_REVIEW_COMMANDS)
    def test_command_file_exists(self, command):
        path = COMMANDS_DIR / command
        assert path.is_file(), f"Command file not found: {path}"

    @pytest.mark.parametrize("command", ALL_REVIEW_COMMANDS)
    def test_has_frontmatter(self, command):
        content = _read_command(command)
        assert content.startswith("---"), f"{command}: missing frontmatter delimiter"
        end = content.find("---", 3)
        assert end > 3, f"{command}: unclosed frontmatter"

    @pytest.mark.parametrize("command", ALL_REVIEW_COMMANDS)
    def test_has_description(self, command):
        content = _read_command(command)
        fm = _parse_frontmatter(content)
        assert "description" in fm, f"{command}: frontmatter missing 'description'"
        assert len(fm["description"]) > 10, f"{command}: description too short"


# =============================================================================
# Structural Tests — Agent References
# =============================================================================


class TestAgentReferences:
    """Agent names in dispatch tables match marketplace.json."""

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_agents_exist_in_marketplace(self, command, marketplace_agents, marketplace_skills):
        content = _read_command(command)
        refs = _extract_agent_refs(content)
        # Filter out skill references (e.g., decision-critic is a skill, not an agent)
        refs = [r for r in refs if r not in marketplace_skills]
        assert len(refs) > 0, f"{command}: no agent references found"

        # Exclude reconciliator since it's dispatched separately, not in the table
        reviewer_agents = [a for a in marketplace_agents if a != "technical-writer"]
        for ref in refs:
            assert ref in reviewer_agents, (
                f"{command}: agent '{ref}' not in marketplace.json agents: {reviewer_agents}"
            )

    def test_dispatch_agents_consistent(self, marketplace_skills):
        """All dispatch commands should dispatch the same set of agents."""
        skills = set(marketplace_skills)
        agent_sets = {}
        for cmd in DISPATCH_COMMANDS:
            # Filter out skill references before comparing
            agent_sets[cmd] = set(_extract_agent_refs(_read_command(cmd))) - skills
        first_cmd = DISPATCH_COMMANDS[0]
        for cmd in DISPATCH_COMMANDS[1:]:
            assert agent_sets[first_cmd] == agent_sets[cmd], (
                f"Agent mismatch between {first_cmd} and {cmd}.\n"
                f"Only in {first_cmd}: {agent_sets[first_cmd] - agent_sets[cmd]}\n"
                f"Only in {cmd}: {agent_sets[cmd] - agent_sets[first_cmd]}"
            )

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_dispatches_14_agents(self, command, marketplace_skills):
        """Each dispatch command references exactly 14 agents."""
        content = _read_command(command)
        refs = _extract_agent_refs(content)
        # Exclude reconciliator and skill references from the count
        skills = set(marketplace_skills)
        dispatch_refs = [r for r in refs if r != "review-reconciliator" and r not in skills]
        assert len(dispatch_refs) == 14, (
            f"{command}: expected 14 dispatch agents, found {len(dispatch_refs)}: {dispatch_refs}"
        )


# =============================================================================
# Structural Tests — Script References
# =============================================================================


class TestScriptReferences:
    """Scripts referenced in commands exist on disk."""

    @pytest.mark.parametrize("command", REVIEW_COMMANDS)
    def test_script_files_exist(self, command):
        content = _read_command(command)
        scripts = set(SCRIPT_REF_PATTERN.findall(content))
        for script in scripts:
            path = SCRIPTS_DIR / script
            assert path.is_file(), (
                f"{command}: references script '{script}' but {path} does not exist"
            )


class TestAgentSignalsContract:
    """Review commands dispatch the reconciliator with explicit file paths."""

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_uses_reconciliator(self, command):
        content = _read_command(command)
        assert "review-reconciliator" in content, (
            f"{command}: should dispatch review-reconciliator for reconciliation"
        )


# =============================================================================
# Structural Tests — Marketplace Registration
# =============================================================================


class TestMarketplaceRegistration:
    """Review commands are registered in marketplace.json."""

    @pytest.mark.parametrize("command", ALL_REVIEW_COMMANDS)
    def test_command_in_marketplace(self, command, marketplace_commands):
        assert command in marketplace_commands, (
            f"{command}: not registered in marketplace.json commands: {marketplace_commands}"
        )


# =============================================================================
# Structural Tests — Command-Specific Content
# =============================================================================


class TestCodeReviewIterative:
    """code-review.md has iterative-specific content."""

    def test_has_state_file_reference(self):
        content = _read_command("code-review.md")
        assert ".review-state.json" in content, (
            "code-review.md: missing .review-state.json reference"
        )

    def test_has_incremental_mode(self):
        content = _read_command("code-review.md")
        assert "incremental" in content.lower(), (
            "code-review.md: missing 'incremental' keyword"
        )

    def test_has_full_reset_option(self):
        content = _read_command("code-review.md")
        assert "full" in content.lower() and "reset" in content.lower(), (
            "code-review.md: missing 'full' or 'reset' argument handling"
        )

    def test_has_rebase_detection(self):
        content = _read_command("code-review.md")
        assert "merge-base" in content, (
            "code-review.md: missing rebase detection (merge-base)"
        )

    def test_has_no_new_commits_guard(self):
        content = _read_command("code-review.md")
        assert "no new commits" in content.lower(), (
            "code-review.md: missing 'no new commits' guard"
        )

    def test_has_conditional_stale_branch_handling(self):
        content = _read_command("code-review.md")
        assert "BRANCH_FRESHNESS" in content or "stale" in content.lower()
        # Stale check should be conditional on history-insights-reviewer
        assert "history-insights" in content.lower()

    def test_has_reconciliator_dispatch(self):
        """Should dispatch the reconciliator for semantic dedup + verification."""
        content = _read_command("code-review.md")
        assert "review-reconciliator" in content, (
            "code-review.md: missing reconciliator dispatch"
        )


class TestIngestCodeReview:
    """ingest-code-review.md has validation-specific content."""

    def test_has_scope_validation(self):
        content = _read_command("ingest-code-review.md")
        assert "OUT_OF_SCOPE" in content or "out of scope" in content.lower(), (
            "ingest-code-review.md: missing scope validation"
        )

    def test_has_false_positive_handling(self):
        content = _read_command("ingest-code-review.md")
        assert "FALSE_POSITIVE" in content or "false positive" in content.lower(), (
            "ingest-code-review.md: missing false positive handling"
        )

    def test_has_validation_checks(self):
        """Should reference checking files against CHANGED_FILES."""
        content = _read_command("ingest-code-review.md")
        assert "CHANGED_FILES" in content, (
            "ingest-code-review.md: missing CHANGED_FILES reference"
        )

    def test_has_action_plan(self):
        content = _read_command("ingest-code-review.md")
        assert "Action Plan" in content, (
            "ingest-code-review.md: missing 'Action Plan' section"
        )

    def test_has_categorization(self):
        """Should categorize findings into buckets."""
        content = _read_command("ingest-code-review.md")
        categories = ["CONFIRMED", "LIKELY VALID", "FALSE POSITIVE", "OUT OF SCOPE", "STYLE"]
        found = [c for c in categories if c in content]
        assert len(found) >= 4, (
            f"ingest-code-review.md: expected 4+ categories, found {len(found)}: {found}"
        )

    def test_references_step_injector_script(self):
        """ingest-code-review.md should reference the step injector script."""
        content = _read_command("ingest-code-review.md")
        assert "ingest-code-review.py" in content, (
            "ingest-code-review.md: missing reference to ingest-code-review.py script"
        )
        script_path = SCRIPTS_DIR / "ingest-code-review.py"
        assert script_path.is_file(), (
            f"ingest-code-review.py not found at {script_path}"
        )


class TestFullCodeReview:
    """full-code-review.md has expected structure."""

    def test_has_default_branch_guard(self):
        content = _read_command("full-code-review.md")
        assert "default branch" in content.lower(), (
            "full-code-review.md: missing default branch guard"
        )

    def test_has_reconciliator_dispatch(self):
        content = _read_command("full-code-review.md")
        assert "review-reconciliator" in content, (
            "full-code-review.md: missing reconciliator dispatch"
        )

    def test_no_state_file(self):
        """full-code-review should NOT reference state files (that's code-review's job)."""
        content = _read_command("full-code-review.md")
        assert ".review-state.json" not in content, (
            "full-code-review.md: should NOT reference .review-state.json"
        )

    def test_has_stale_branch_handling(self):
        content = _read_command("full-code-review.md")
        assert "BRANCH_FRESHNESS" in content or "stale" in content.lower()

    def test_has_merge_base_reference(self):
        content = _read_command("full-code-review.md")
        assert "merge-base" in content.lower() or "RANGE_REBASED" in content

    def test_has_review_findings_output(self):
        """Should produce review-findings.json and review-findings.md."""
        content = _read_command("full-code-review.md")
        assert "review-findings.json" in content, (
            "full-code-review.md: missing review-findings.json output reference"
        )


# =============================================================================
# Decision Critic Pipeline Tests
# =============================================================================


# Commands that include the decision critic pipeline
CRITIC_COMMANDS = [
    "full-code-review.md",
    "code-review.md",
    # pr-review.md is now a thin wrapper — decision critic is in pr-review-pipeline.py
]


class TestDecisionCritic:
    """Decision critic phase is present and well-formed in review commands."""

    @pytest.mark.parametrize("command", CRITIC_COMMANDS)
    def test_has_decision_critic_dispatch(self, command):
        """Must dispatch the decision-reviewer agent."""
        content = _read_command(command)
        assert "decision-reviewer" in content, (
            f"{command}: missing decision-reviewer agent dispatch"
        )

    @pytest.mark.parametrize("command", CRITIC_COMMANDS)
    def test_has_verdict_fallback_chain(self, command):
        """Should fall back to findings file if return message verdict parse fails."""
        content = _read_command(command)
        assert "decision-critic-findings.md" in content, (
            f"{command}: missing fallback to decision-critic-findings.md"
        )
        content_lower = content.lower()
        assert "unavailable" in content_lower, (
            f"{command}: missing critic-unavailable graceful degradation"
        )

    @pytest.mark.parametrize("command", CRITIC_COMMANDS)
    def test_has_verdict_recalculation_on_revise(self, command):
        """REVISE must trigger verdict recalculation from updated findings."""
        content = _read_command(command)
        content_lower = content.lower()
        assert "recalculate" in content_lower and "verdict" in content_lower, (
            f"{command}: REVISE missing verdict recalculation"
        )

    @pytest.mark.parametrize("command", CRITIC_COMMANDS)
    def test_has_verdict_override_on_escalate(self, command):
        """ESCALATE must override the review verdict to COMMENT."""
        content = _read_command(command)
        content_lower = content.lower()
        assert "override" in content_lower and "COMMENT" in content, (
            f"{command}: ESCALATE missing verdict override to COMMENT"
        )

    @pytest.mark.parametrize("command", CRITIC_COMMANDS)
    def test_critic_dispatch_after_reconciliator(self, command):
        """Decision critic must appear after reconciliator in the pipeline."""
        content = _read_command(command)
        reconciliator_pos = content.find("review-reconciliator")
        critic_pos = content.find("decision-reviewer")
        assert reconciliator_pos > 0 and critic_pos > 0, (
            f"{command}: missing reconciliator or critic reference"
        )
        assert reconciliator_pos < critic_pos, (
            f"{command}: decision-reviewer must appear after review-reconciliator "
            f"(reconciliator at {reconciliator_pos}, critic at {critic_pos})"
        )

    @pytest.mark.parametrize("command", CRITIC_COMMANDS)
    def test_critic_reviews_findings(self, command):
        """Decision critic should review the review-findings output."""
        content = _read_command(command)
        assert "review-findings.md" in content, (
            f"{command}: decision-reviewer should reference review-findings.md"
        )

    @pytest.mark.parametrize("command", CRITIC_COMMANDS)
    def test_critic_no_ingestion_verification(self, command):
        """Decision critic should NOT receive ingestion verification (independent)."""
        content = _read_command(command)
        assert "Ingestion Verification" not in content, (
            f"{command}: decision-reviewer should not receive Ingestion Verification"
        )


# =============================================================================
# Triage Block Tests (Step 6: Adaptive Agent Triage)
# =============================================================================


# The 6 agents subject to LLM triage (must match design doc)
TRIAGED_AGENTS = [
    "security-reviewer",
    "dead-code-reviewer",
    "architecture-reviewer",
    "wp-architecture-reviewer",
    "performance-reviewer",
    "a11y-reviewer",
]


class TestTriageBlock:
    """Step 6 adaptive agent triage is present and well-formed in dispatch commands."""

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_has_triage_step(self, command):
        """Dispatch commands must contain Step 6 triage block."""
        content = _read_command(command)
        assert "Step 6" in content or "Adaptive Agent Triage" in content, (
            f"{command}: missing Step 6 (Adaptive Agent Triage)"
        )

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_triage_lists_all_conditional_agents(self, command):
        """Triage block must reference all 6 conditional agents."""
        content = _read_command(command)
        for agent in TRIAGED_AGENTS:
            # Agent name without -reviewer suffix is acceptable in criteria headings
            agent_base = agent.replace("-reviewer", "")
            assert agent in content or agent_base in content, (
                f"{command}: triage block missing conditional agent '{agent}'"
            )

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_triage_output_format(self, command):
        """Triage block must document the TRIAGE: output format."""
        content = _read_command(command)
        assert "TRIAGE:" in content, (
            f"{command}: missing TRIAGE: output format"
        )
        # Must show both DISPATCH and SKIP as possible decisions
        assert "DISPATCH" in content and "SKIP" in content, (
            f"{command}: triage format must show both DISPATCH and SKIP decisions"
        )

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_triage_skipped_signal(self, command):
        """Dispatch commands must include STATUS=SKIPPED_TRIAGE signal for reconciliator."""
        content = _read_command(command)
        assert "SKIPPED_TRIAGE" in content, (
            f"{command}: missing STATUS=SKIPPED_TRIAGE signal for reconciliator"
        )

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_triage_between_preflight_and_dispatch(self, command):
        """Step 6 must appear between Step 5 (dispatch plan) and Step 7 (dispatch)."""
        content = _read_command(command)
        pos_5 = content.find("Step 5")
        pos_6 = content.find("Step 6")
        pos_7 = content.find("Step 7")
        assert pos_5 < pos_6 < pos_7, (
            f"{command}: Step 6 must be between Step 5 and Step 7 "
            f"(positions: 5={pos_5}, 6={pos_6}, 7={pos_7})"
        )

    @pytest.mark.parametrize("command", DISPATCH_COMMANDS)
    def test_triage_default_is_dispatch(self, command):
        """Triage must default to DISPATCH when in doubt (safety mechanism)."""
        content = _read_command(command).lower()
        assert "when in doubt" in content and "dispatch" in content, (
            f"{command}: triage must specify 'when in doubt, DISPATCH' default"
        )


# =============================================================================
# State File Grader Tests
# =============================================================================


class TestStateFileGrading:
    """Grade .review-state.json files via the grader."""

    def test_valid_state_roundtrip(self, tmp_dir):
        """Write a state file and grade it — full round-trip."""
        path = os.path.join(tmp_dir, ".review-state.json")
        state = {
            "last_reviewed_sha": "abc123def456789012345678901234567890abcd",
            "last_reviewed_at": "2026-02-09T12:34:56",
            "review_count": 3,
            "base_ref": "main",
            "git_range_used": "abc123..HEAD",
        }
        with open(path, "w") as f:
            json.dump(state, f)
        result = grade_review_state(path)
        assert result.passed, f"Failures: {result.failures}"

    def test_incremented_count(self, tmp_dir):
        """State with review_count > 1 is valid (iterative reviews)."""
        path = os.path.join(tmp_dir, ".review-state.json")
        state = {
            "last_reviewed_sha": "def4567",
            "last_reviewed_at": "2026-02-09T15:00:00",
            "review_count": 5,
            "base_ref": "develop",
            "git_range_used": "def4567..HEAD",
        }
        with open(path, "w") as f:
            json.dump(state, f)
        result = grade_review_state(path)
        assert result.passed, f"Failures: {result.failures}"

    def test_explicit_range(self, tmp_dir):
        """git_range_used with explicit SHA range is valid."""
        path = os.path.join(tmp_dir, ".review-state.json")
        state = {
            "last_reviewed_sha": "1234567890abcdef1234567890abcdef12345678",
            "last_reviewed_at": "2026-02-09T15:00:00",
            "review_count": 1,
            "base_ref": "main",
            "git_range_used": "abc1234..def5678",
        }
        with open(path, "w") as f:
            json.dump(state, f)
        result = grade_review_state(path)
        assert result.passed, f"Failures: {result.failures}"


# =============================================================================
# PR Update Command Tests
# =============================================================================


class TestPrUpdate:
    """pr-update.md has expected structure and content."""

    COMMAND = "pr-update.md"

    def test_file_exists(self):
        path = COMMANDS_DIR / self.COMMAND
        assert path.is_file(), f"Command file not found: {path}"

    def test_has_frontmatter_with_description(self):
        content = _read_command(self.COMMAND)
        assert content.startswith("---"), f"{self.COMMAND}: missing frontmatter delimiter"
        fm = _parse_frontmatter(content)
        assert "description" in fm, f"{self.COMMAND}: frontmatter missing 'description'"
        assert len(fm["description"]) > 10, f"{self.COMMAND}: description too short"

    def test_has_pr_detection(self):
        """Should reference gh pr view for PR detection."""
        content = _read_command(self.COMMAND)
        assert "gh pr view" in content, (
            f"{self.COMMAND}: missing 'gh pr view' for PR detection"
        )

    def test_has_template_detection(self):
        """Should search for PULL_REQUEST_TEMPLATE."""
        content = _read_command(self.COMMAND)
        assert "PULL_REQUEST_TEMPLATE" in content, (
            f"{self.COMMAND}: missing PULL_REQUEST_TEMPLATE detection"
        )

    def test_has_validation_step(self):
        """Should have a validation/verify pass before presenting."""
        content = _read_command(self.COMMAND)
        assert "Validation" in content or "verify" in content.lower(), (
            f"{self.COMMAND}: missing validation step"
        )

    def test_has_user_approval_gate(self):
        """Should wait for user approval before updating."""
        content = _read_command(self.COMMAND)
        assert "approval" in content.lower() or "confirm" in content.lower(), (
            f"{self.COMMAND}: missing user approval gate"
        )

    def test_has_pr_edit(self):
        """Should reference gh pr edit for updating the PR."""
        content = _read_command(self.COMMAND)
        assert "pr edit" in content, (
            f"{self.COMMAND}: missing 'pr edit' for PR update"
        )

    def test_has_ghe_fallback(self):
        """Should support ghe as fallback for GitHub Enterprise."""
        content = _read_command(self.COMMAND)
        assert "ghe" in content, (
            f"{self.COMMAND}: missing 'ghe' fallback for GitHub Enterprise"
        )

    def test_has_stop_conditions(self):
        """Should have STOP conditions for invalid states."""
        content = _read_command(self.COMMAND)
        content_lower = content.lower()
        assert "stop" in content_lower or "halt" in content_lower, (
            f"{self.COMMAND}: missing STOP conditions"
        )
        # Should guard against merged/closed PRs
        assert "merged" in content_lower or "closed" in content_lower, (
            f"{self.COMMAND}: missing merged/closed PR guard"
        )

    def test_has_brevity_calibration(self):
        """Should scale description depth to PR size (small/medium/large)."""
        content = _read_command(self.COMMAND)
        content_lower = content.lower()
        assert "small" in content_lower and "large" in content_lower, (
            f"{self.COMMAND}: missing size-based brevity calibration"
        )

    def test_has_artifact_discovery(self):
        """Should discover and filter review/plan artifacts."""
        content = _read_command(self.COMMAND)
        assert "artifact" in content.lower(), (
            f"{self.COMMAND}: missing artifact discovery"
        )

    def test_not_in_review_commands(self):
        """pr-update should NOT be in REVIEW_COMMANDS (it's not a review dispatcher)."""
        assert self.COMMAND not in REVIEW_COMMANDS, (
            f"{self.COMMAND}: should not be in REVIEW_COMMANDS"
        )

    def test_registered_in_marketplace(self, marketplace_commands):
        """pr-update.md should be registered in marketplace.json."""
        assert self.COMMAND in marketplace_commands, (
            f"{self.COMMAND}: not registered in marketplace.json commands: {marketplace_commands}"
        )


# =============================================================================
# PR Review Command Tests (End-to-End Orchestrator)
# =============================================================================


class TestPrReview:
    """pr-review.md is a thin wrapper delegating to pr-review-pipeline.py."""

    COMMAND = "pr-review.md"

    # --- Structural ---

    def test_file_exists(self):
        path = COMMANDS_DIR / self.COMMAND
        assert path.is_file(), f"Command file not found: {path}"

    def test_has_frontmatter_with_description(self):
        content = _read_command(self.COMMAND)
        assert content.startswith("---"), f"{self.COMMAND}: missing frontmatter delimiter"
        fm = _parse_frontmatter(content)
        assert "description" in fm, f"{self.COMMAND}: frontmatter missing 'description'"
        assert len(fm["description"]) > 10, f"{self.COMMAND}: description too short"

    def test_registered_in_marketplace(self, marketplace_commands):
        assert self.COMMAND in marketplace_commands, (
            f"{self.COMMAND}: not registered in marketplace.json: {marketplace_commands}"
        )

    # --- Pipeline script reference ---

    def test_references_pipeline_script(self):
        """Should delegate to pr-review-pipeline.py."""
        content = _read_command(self.COMMAND)
        assert "pr-review-pipeline.py" in content, (
            f"{self.COMMAND}: missing reference to pr-review-pipeline.py"
        )

    def test_has_headless_flag(self):
        """Should always pass --headless for autonomous operation."""
        content = _read_command(self.COMMAND)
        assert "--headless" in content, (
            f"{self.COMMAND}: missing --headless flag"
        )

    def test_has_phase_overview(self):
        """Should have a phase overview table."""
        content = _read_command(self.COMMAND)
        assert "SETUP" in content
        assert "EXECUTION" in content
        assert "VALIDATION" in content
        assert "OUTPUT" in content

    def test_has_failure_recovery(self):
        """Should document failure recovery paths."""
        content = _read_command(self.COMMAND)
        content_lower = content.lower()
        assert "failure" in content_lower or "recovery" in content_lower, (
            f"{self.COMMAND}: missing failure recovery documentation"
        )

    def test_does_not_reference_reviewing_pr(self):
        """Should NOT reference the deleted reviewing-pr skill."""
        content = _read_command(self.COMMAND)
        assert "reviewing-pr" not in content, (
            f"{self.COMMAND}: still references deleted reviewing-pr skill"
        )


# =============================================================================
# Switch-To Command Tests (Branch/PR Switcher)
# =============================================================================


class TestSwitchTo:
    """switch-to.md has expected structure and content."""

    COMMAND = "switch-to.md"

    # --- Structural ---

    def test_file_exists(self):
        path = COMMANDS_DIR / self.COMMAND
        assert path.is_file(), f"Command file not found: {path}"

    def test_has_frontmatter_with_description(self):
        content = _read_command(self.COMMAND)
        assert content.startswith("---"), f"{self.COMMAND}: missing frontmatter delimiter"
        fm = _parse_frontmatter(content)
        assert "description" in fm, f"{self.COMMAND}: frontmatter missing 'description'"
        assert len(fm["description"]) > 10, f"{self.COMMAND}: description too short"

    def test_registered_in_marketplace(self, marketplace_commands):
        assert self.COMMAND in marketplace_commands, (
            f"{self.COMMAND}: not registered in marketplace.json: {marketplace_commands}"
        )

    # --- Input handling ---

    def test_has_argument_parsing(self):
        """Should parse $ARGUMENTS for branch name or PR URL."""
        content = _read_command(self.COMMAND)
        assert "$ARGUMENTS" in content, (
            f"{self.COMMAND}: missing $ARGUMENTS parsing"
        )

    def test_has_empty_arguments_guard(self):
        """Should STOP when no arguments provided."""
        content = _read_command(self.COMMAND)
        content_lower = content.lower()
        assert "stop" in content_lower and "empty" in content_lower or "usage" in content_lower, (
            f"{self.COMMAND}: missing empty arguments guard"
        )

    def test_detects_pr_url(self):
        """Should detect PR URLs by /pull/ pattern."""
        content = _read_command(self.COMMAND)
        assert "/pull/" in content, (
            f"{self.COMMAND}: missing PR URL detection (/pull/)"
        )

    def test_has_already_on_branch_early_exit(self):
        """Should handle already being on the target branch."""
        content = _read_command(self.COMMAND)
        content_lower = content.lower()
        assert "already on" in content_lower, (
            f"{self.COMMAND}: missing already-on-branch early exit"
        )

    def test_has_error_normalization(self):
        """Should normalize expected git/gh failures."""
        content = _read_command(self.COMMAND)
        content_lower = content.lower()
        assert "expected failures" in content_lower, (
            f"{self.COMMAND}: missing error normalization for expected failures"
        )

    # --- Dirty working tree handling ---

    def test_has_dirty_state_check(self):
        """Should check git status --porcelain before switching."""
        content = _read_command(self.COMMAND)
        assert "git status --porcelain" in content, (
            f"{self.COMMAND}: missing dirty state check (git status --porcelain)"
        )

    def test_has_stash_option(self):
        """Should offer to stash changes."""
        content = _read_command(self.COMMAND)
        assert "git stash push" in content, (
            f"{self.COMMAND}: missing git stash push for dirty state"
        )

    def test_has_commit_first_option(self):
        """Should offer to commit first as an alternative to stashing."""
        content = _read_command(self.COMMAND)
        content_lower = content.lower()
        assert "commit first" in content_lower or "commit your changes" in content_lower, (
            f"{self.COMMAND}: missing 'commit first' option"
        )

    def test_has_cancel_option(self):
        """Should offer to cancel the switch."""
        content = _read_command(self.COMMAND)
        content_lower = content.lower()
        assert "cancel" in content_lower or "abort" in content_lower, (
            f"{self.COMMAND}: missing cancel option"
        )

    def test_has_ask_user_for_dirty_state(self):
        """Should use AskUserQuestion when working tree is dirty."""
        content = _read_command(self.COMMAND)
        assert "AskUserQuestion" in content, (
            f"{self.COMMAND}: missing AskUserQuestion for dirty state"
        )

    # --- Branch switching ---

    def test_has_local_branch_check(self):
        """Should check if branch exists locally."""
        content = _read_command(self.COMMAND)
        assert "git branch --list" in content, (
            f"{self.COMMAND}: missing local branch check (git branch --list)"
        )

    def test_has_git_checkout(self):
        """Should use git checkout to switch branches."""
        content = _read_command(self.COMMAND)
        assert "git checkout" in content, (
            f"{self.COMMAND}: missing git checkout"
        )

    def test_has_remote_branch_check(self):
        """Should check remote for branch if not found locally."""
        content = _read_command(self.COMMAND)
        assert "git ls-remote" in content or "git fetch" in content, (
            f"{self.COMMAND}: missing remote branch check"
        )

    def test_has_branch_not_found_stop(self):
        """Should STOP if branch not found locally or on remote."""
        content = _read_command(self.COMMAND)
        content_lower = content.lower()
        assert "not found" in content_lower, (
            f"{self.COMMAND}: missing 'branch not found' STOP condition"
        )

    # --- Remote sync ---

    def test_has_remote_sync_check(self):
        """Should check for new remote commits after switching."""
        content = _read_command(self.COMMAND)
        assert "git fetch" in content, (
            f"{self.COMMAND}: missing git fetch for remote sync"
        )

    def test_has_pull_rebase_option(self):
        """Should offer pull --rebase as an option."""
        content = _read_command(self.COMMAND)
        assert "--rebase" in content, (
            f"{self.COMMAND}: missing pull --rebase option"
        )

    def test_has_ask_user_for_remote_sync(self):
        """Should use AskUserQuestion when remote has new commits."""
        content = _read_command(self.COMMAND)
        assert content.count("AskUserQuestion") >= 2, (
            f"{self.COMMAND}: expected at least 2 AskUserQuestion calls "
            f"(dirty state + remote sync)"
        )

    # --- PR flow ---

    def test_has_gh_pr_view(self):
        """Should use gh pr view to get PR details."""
        content = _read_command(self.COMMAND)
        assert "gh pr view" in content, (
            f"{self.COMMAND}: missing gh pr view for PR details"
        )

    def test_has_repo_validation(self):
        """Should validate CWD repo matches PR repo."""
        content = _read_command(self.COMMAND)
        assert "gh repo view" in content, (
            f"{self.COMMAND}: missing gh repo view for repo validation"
        )

    def test_has_fork_handling(self):
        """Should handle fork PRs by adding the fork as a remote."""
        content = _read_command(self.COMMAND)
        assert "git remote add" in content, (
            f"{self.COMMAND}: missing fork remote handling (git remote add)"
        )

    def test_has_base_branch_fetch(self):
        """Should fetch the base branch for PR flow."""
        content = _read_command(self.COMMAND)
        assert "BASE_BRANCH" in content, (
            f"{self.COMMAND}: missing BASE_BRANCH fetch for PR flow"
        )

    # --- Post-switch context ---

    def test_has_post_switch_log(self):
        """Should show recent commits after switching."""
        content = _read_command(self.COMMAND)
        assert "git log --oneline" in content, (
            f"{self.COMMAND}: missing post-switch commit log"
        )

    def test_has_ahead_behind(self):
        """Should show ahead/behind status."""
        content = _read_command(self.COMMAND)
        assert "rev-list --left-right --count" in content, (
            f"{self.COMMAND}: missing ahead/behind count"
        )

    def test_has_stash_reminder(self):
        """Should remind user about stashed changes in post-switch context."""
        content = _read_command(self.COMMAND)
        assert "git stash pop" in content, (
            f"{self.COMMAND}: missing stash pop reminder"
        )

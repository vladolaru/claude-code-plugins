#!/usr/bin/env python3
"""
Unified Review Pipeline — curated-context-pipeline for code reviews.

PIPELINE GOAL: Deliver a complete review of code changes that is comprehensive
in its analysis, contextual in its focus, accurate in its findings, and actionable
in its recommendations — maintaining a high quality bar for codebases so they can
deliver great business results and awesome user experiences.

A single script owns a 12-step universal sequence. Mode (pr|full|incremental) and
data-driven conditions determine which steps run. The script curates context as
conversational briefings. Three command .md files are thin wrappers calling this
script with --mode flags.

Split file-based state:
  - run-config.json:     Caller config (mode, pr_number, interactive, output_instructions).
                         Set before step 1 (or by the script at step 1 from CLI args).
                         Read-only during the run.
  - pipeline-state.json: Execution state. Owned exclusively by the script.
                         The LLM never reads or writes it.

Zero external dependencies (stdlib only).
"""

import argparse
import glob as glob_mod
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    from .dispatch_status import (
        DISPATCHED_STATUSES,
        SKIPPED_STATUSES,
        SKIPPED_QUICK_MODE,
        validate_dispatch_plan_agents,
    )
except ImportError:
    _scripts_parent = str(Path(__file__).resolve().parent.parent)
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.dispatch_status import (
        DISPATCHED_STATUSES,
        SKIPPED_STATUSES,
        SKIPPED_QUICK_MODE,
        validate_dispatch_plan_agents,
    )

SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS_DIR.parents[1]
AGENTS_DIR = PLUGIN_ROOT / "agents"

HOST_CLAUDE = "claude"
HOST_CODEX = "codex"
SUPPORTED_HOSTS = (HOST_CLAUDE, HOST_CODEX)


def _host(config):
    """Return the persisted orchestration host."""
    host = (config or {}).get("host", HOST_CLAUDE)
    return host if host in SUPPORTED_HOSTS else HOST_CLAUDE


def _agent_definition_path(agent_name):
    """Return the canonical reviewer definition path for either host."""
    return AGENTS_DIR / f"{agent_name}.md"


def _codex_task_name(agent_name):
    """Map a reviewer name to Codex's lowercase task-name contract."""
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(agent_name).lower())
    normalized = normalized.strip("_")
    if not normalized:
        normalized = "reviewer"
    if normalized[0].isdigit():
        normalized = f"reviewer_{normalized}"
    return normalized[:64]


def _codex_agent_instruction(agent_name):
    """Describe how Codex should reuse a canonical Claude agent definition."""
    return (
        f"In the message, first read `{_agent_definition_path(agent_name)}` "
        "completely. Treat its YAML frontmatter as Claude Code packaging "
        "metadata, do not translate its model or tool labels, and follow the "
        "Markdown reviewer instructions."
    )


def _stop_operation(config):
    """Return the host-native operation used to stop a subagent."""
    return "interrupt_agent" if _host(config) == HOST_CODEX else "TaskStop"

# ---------------------------------------------------------------------------
# Pipeline Identity
# ---------------------------------------------------------------------------

_PIPELINE_MISSION = (
    "You are a code review orchestrator. Run the pipeline to completion, "
    "producing an accurate and actionable review the author can act on. "
    "Every step has required artifacts; treat each as a contract. Verify "
    "each step's outputs before proceeding."
)

_PHASE_TRANSITIONS = {
    "EXECUTION": (
        "You understand the changes. Now dispatch specialist reviewers — "
        "launch every planned agent correctly. Precision here determines "
        "downstream quality."
    ),
    "SYNTHESIS": (
        "Agents have produced findings. Deduplicate, reconcile, and produce "
        "a coherent picture. Every surviving finding must trace to an agent's "
        "work. Write structured data cleanly — these files are the source of "
        "truth for remaining steps."
    ),
    "VALIDATION": (
        "Report is written. The decision critic will challenge your "
        "conclusions. Act on the critic's verdict: REVISE means revise. "
        "Persist all verdicts to their files precisely. Deliver a review "
        "the author can trust."
    ),
    "OUTPUT": (
        "Review is validated. Present clearly, confirm all artifacts are "
        "written, verify the pipeline result is complete. This is what the "
        "author or calling system receives."
    ),
}

# ---------------------------------------------------------------------------
# Step Sequence
# ---------------------------------------------------------------------------

STEP_SEQUENCE = [
    {"step": 1,  "title": "Parse Input",            "phase": "SETUP",      "condition": "always"},
    {"step": 2,  "title": "Repo Setup",              "phase": "SETUP",      "condition": "needs_workspace_setup"},
    {"step": 3,  "title": "Gather Context",           "phase": "SETUP",      "condition": "always"},
    {"step": 4,  "title": "Fetch Issue Context",      "phase": "SETUP",      "condition": "has_unfetched_issues"},
    {"step": 5,  "title": "Dispatch Plan + Triage",   "phase": "EXECUTION",  "condition": "always"},
    {"step": 6,  "title": "Dispatch Agents",          "phase": "EXECUTION",  "condition": "always"},
    {"step": 7,  "title": "Save Review Baseline",     "phase": "EXECUTION",  "condition": "always"},
    {"step": 8,  "title": "Reconcile + Verify",       "phase": "SYNTHESIS",  "condition": "always"},
    {"step": 9,  "title": "Review Report Synthesis",  "phase": "SYNTHESIS",  "condition": "always"},
    {"step": 10, "title": "Decision Critic",          "phase": "VALIDATION", "condition": "always"},
    {"step": 11, "title": "Present Results",          "phase": "OUTPUT",     "condition": "always"},
    {"step": 12, "title": "Cleanup",                  "phase": "OUTPUT",     "condition": "has_workspace_state_interactive"},
]

_STEP_MAP = {s["step"]: s for s in STEP_SEQUENCE}

# Artifacts to clear at step 1 (stale from previous runs)
_STALE_ARTIFACTS = [
    "pipeline-state.json",
    ".telemetry-log-path",
    "dispatch-plan.json",
    "dispatch-plan.initial.json",
    "*-review.json",
    "*-review.md",
    "*-scope-summary*.json",
    "*-deferred-files.json",
    "*.started",
    "reconciliation-context.json",
    "reconciliation-context.md",
    "critic-context.md",
    "review-findings.json",
    "review-findings.md",
    "review-report.md",
    "review-verdict.json",
    "pipeline-result.json",
    "decision-critic-findings.md",
    "decision-critic-verdict.json",
    "change-purpose.md",
    "scoped-diff.patch",
    "*-scoped-diff.patch",
]

DEFAULT_AGENT_TIMEOUT = 1200  # 20 minutes — matches agents_status.py
CONTEXT_GATHER_TIMEOUT = (20 * 60) + 60  # install-cache inner timeout + grace
AGENT_WAIT_GRACE_SECONDS = 60

# Files to preserve across runs
_PRESERVED_FILES = {
    "run-config.json",
    ".branch-review-baseline.json",
}


# ---------------------------------------------------------------------------
# Condition Evaluation
# ---------------------------------------------------------------------------

def _eval_condition(condition, mode, config, state, context):
    """Evaluate a step condition. Returns True if step should run."""
    if condition == "always":
        return True

    if condition == "needs_workspace_setup":
        # PR mode + interactive + no pre-computed merge_base
        if mode != "pr":
            return False
        if not config.get("interactive", True):
            return False
        git = context.get("git", {})
        return not git.get("merge_base")

    if condition == "has_unfetched_issues":
        return state.get("resolved_params", {}).get("has_unfetched_issues", False)

    if condition == "has_workspace_state_interactive":
        ws = state.get("workspace", {})
        has_branch = ws.get("original_branch") is not None
        is_interactive = config.get("interactive", True)
        return has_branch and is_interactive

    return False


# ---------------------------------------------------------------------------
# Step Routing
# ---------------------------------------------------------------------------

def get_active_steps(mode, config, state, context):
    """Return set of active step numbers for this mode/config/state/context."""
    active = set()
    for step_def in STEP_SEQUENCE:
        if _eval_condition(step_def["condition"], mode, config, state, context):
            active.add(step_def["step"])
    return active


def compute_next_step(current_step, active_steps):
    """Compute the next step after current_step.

    Returns dict with 'step', 'title', and optional 'skip_reason',
    or None if current_step is the last active step.
    """
    # Find next active step after current
    candidates = sorted(s for s in active_steps if s > current_step)
    if not candidates:
        return None

    next_num = candidates[0]
    step_def = _STEP_MAP[next_num]

    # Compute skip reason if steps were skipped
    skip_reason = None
    skipped = [s for s in range(current_step + 1, next_num) if s not in active_steps]
    if skipped:
        skipped_titles = [_STEP_MAP[s]["title"] for s in skipped]
        skip_reason = f"Skipped: {', '.join(f'Step {s} ({t})' for s, t in zip(skipped, skipped_titles))}"

    return {
        "step": next_num,
        "title": step_def["title"],
        "skip_reason": skip_reason,
    }


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

_DEFAULT_STATE = {
    "run_id": "",
    "completed_steps": [],
    "skipped_steps": [],
    "resolved_params": {
        "has_unfetched_issues": False,
    },
    "workspace": {
        "original_branch": None,
        "stash_ref": None,
    },
    "agents": {
        "dispatched": [],
        "completed": [],
        "failed": [],
    },
    "verdict": None,
}

_DEFAULT_CONFIG = {}


def read_state(output_dir):
    """Read pipeline-state.json, return default if missing or corrupted."""
    path = os.path.join(output_dir, "pipeline-state.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return json.loads(json.dumps(_DEFAULT_STATE))


def write_state(output_dir, state):
    """Write pipeline-state.json."""
    path = os.path.join(output_dir, "pipeline-state.json")
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def read_config(output_dir):
    """Read run-config.json, return empty dict if missing or corrupted."""
    path = os.path.join(output_dir, "run-config.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(_DEFAULT_CONFIG)


def write_config(output_dir, config):
    """Write run-config.json."""
    path = os.path.join(output_dir, "run-config.json")
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def _reset_interactive_review_context(output_dir):
    """Atomically replace prior-run context with the current run seed."""
    context = {"output": {"directory": output_dir}}
    path = os.path.join(output_dir, "review-context.json")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=output_dir,
            encoding="utf-8",
        ) as temp_file:
            temp_path = temp_file.name
            json.dump(context, temp_file, indent=2)
            temp_file.flush()
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    return context


def read_review_context(output_dir):
    """Read preserved review-context.json, or return an empty dict."""
    path = os.path.join(output_dir, "review-context.json")
    try:
        with open(path) as f:
            context = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    # A valid-JSON array/scalar would crash every context.get() consumer —
    # degrade to the same empty fallback as malformed JSON.
    return context if isinstance(context, dict) else {}


def resolve_params(output_dir, cli_mode=None, cli_pr_number=None,
                   cli_interactive=None, cli_output_instructions=None,
                   cli_git_range=None):
    """Resolve parameters: run-config.json wins over CLI args."""
    config = read_config(output_dir)
    # Config values take precedence; CLI fills in missing fields
    resolved = {}
    resolved["mode"] = config.get("mode") or cli_mode
    resolved["pr_number"] = config.get("pr_number") or cli_pr_number
    if "interactive" in config:
        resolved["interactive"] = config["interactive"]
    elif cli_interactive is not None:
        resolved["interactive"] = cli_interactive
    else:
        resolved["interactive"] = True
    if "output_instructions" in config:
        resolved["output_instructions"] = config["output_instructions"]
    elif cli_output_instructions:
        resolved["output_instructions"] = cli_output_instructions
    if config.get("git_range") or cli_git_range:
        resolved["git_range"] = config.get("git_range") or cli_git_range
    return resolved


# ---------------------------------------------------------------------------
# Stale Artifact Cleanup
# ---------------------------------------------------------------------------

def clean_stale_artifacts(output_dir):
    """Remove stale run artifacts, preserving run-config.json and .branch-review-baseline.json."""
    for pattern in _STALE_ARTIFACTS:
        if "*" in pattern:
            for filepath in glob_mod.glob(os.path.join(output_dir, pattern)):
                basename = os.path.basename(filepath)
                if basename not in _PRESERVED_FILES:
                    try:
                        os.remove(filepath)
                    except OSError:
                        pass
        else:
            filepath = os.path.join(output_dir, pattern)
            basename = os.path.basename(filepath)
            if basename not in _PRESERVED_FILES and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    pass


def _preserve_initial_dispatch_plan(output_dir, plan):
    """Atomically preserve the planner baseline without blocking the review.

    Any prior baseline is removed first so a failed measurement write cannot
    make an older plan look like the current run's deterministic output.
    """
    initial_path = os.path.join(output_dir, "dispatch-plan.initial.json")
    temp_path = None
    try:
        try:
            os.remove(initial_path)
        except FileNotFoundError:
            pass

        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=output_dir,
            encoding="utf-8",
        ) as temp_file:
            temp_path = temp_file.name
            json.dump(plan, temp_file, indent=2, sort_keys=True)
            temp_file.flush()
        os.replace(temp_path, initial_path)
    except (OSError, TypeError, ValueError):
        try:
            os.remove(initial_path)
        except OSError:
            pass
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _load_dispatch_plan(plan_path):
    """Load one dispatch plan and validate its agent decisions."""
    with open(plan_path) as plan_file:
        plan = json.load(plan_file)
    if not isinstance(plan, dict):
        raise ValueError(
            f"Dispatch plan at {plan_path} must be a JSON object, got {plan!r}"
        )
    validate_dispatch_plan_agents(plan.get("agents"))
    return plan


# ---------------------------------------------------------------------------
# Step Guidance (pure formatting function — no I/O, no subprocesses)
# ---------------------------------------------------------------------------

def get_step_guidance(step, mode, state, context, config=None, output_dir=None):
    """Return briefing dict for a step. Pure formatting — no I/O.

    Args:
        step: Step number (1-12)
        mode: Review mode (pr, full, incremental)
        state: Pipeline state dict (from pipeline-state.json)
        context: Review context dict (from review-context.json or gathered data)
        config: Run config dict (from run-config.json), optional
        output_dir: Output directory path, optional

    Returns:
        Dict with phase, title, situation, actions, handoff, next_step, skip_reason.
        None if step number is invalid.
    """
    if step not in _STEP_MAP:
        return None

    step_def = _STEP_MAP[step]
    config = config or {}

    if step == 1:
        return _step_1_parse_input(mode, state, context, config, output_dir)
    elif step == 2:
        return _step_2_repo_setup(mode, state, context, config, output_dir)
    elif step == 3:
        return _step_3_gather_context(mode, state, context, config, output_dir)
    elif step == 4:
        return _step_4_fetch_issues(mode, state, context, config, output_dir)
    elif step == 5:
        return _step_5_dispatch_plan(mode, state, context, config, output_dir)
    elif step == 6:
        return _step_6_dispatch_agents(mode, state, context, config, output_dir)
    elif step == 7:
        return _step_7_save_baseline(mode, state, context, config, output_dir)
    elif step == 8:
        return _step_8_reconcile(mode, state, context, config, output_dir)
    elif step == 9:
        return _step_9_review_report(mode, state, context, config, output_dir)
    elif step == 10:
        return _step_10_decision_critic(mode, state, context, config, output_dir)
    elif step == 11:
        return _step_11_present_results(mode, state, context, config, output_dir)
    elif step == 12:
        return _step_12_cleanup(mode, state, context, config, output_dir)
    else:
        return None


# ---------------------------------------------------------------------------
# Step 1: Parse Input
# ---------------------------------------------------------------------------

def _step_1_parse_input(mode, state, context, config, output_dir):
    """Step 1: Parse Input — confirm parameters and mode."""
    situation = [_PIPELINE_MISSION, ""]
    actions = []

    if mode == "pr":
        pr_number = config.get("pr_number")
        if pr_number:
            situation.append(f"Mode: PR review (PR #{pr_number})")
            actions.append(f"PR #{pr_number} confirmed. Proceed to context gathering.")
        else:
            situation.append("Mode: PR review (no PR number provided)")
            actions.append("A PR number or URL is required for PR review mode.")
            actions.append("Usage: /pr-review <PR_URL_or_number>")

    elif mode == "full":
        situation.append("Mode: Full branch review")
        # Check default branch guard
        if context.get("on_default_branch"):
            actions.append("⛔ PIPELINE STOPPED: You are on the default branch.")
            actions.append("Switch to a feature branch before running a full review.")
        else:
            git_range = config.get("git_range", "")
            if git_range:
                actions.append(f"Explicit git range provided: `{git_range}`")
            else:
                actions.append("The branch range will be auto-detected from the merge base.")
            actions.append("Reviewing all changes on this branch against the base branch.")

    elif mode == "incremental":
        situation.append("Mode: Incremental branch review")
        if context.get("no_new_commits"):
            actions.append("⛔ PIPELINE STOPPED: No new commits since the last review.")
            actions.append("There is nothing new to review. Make more commits and try again.")
        else:
            actions.append("Incremental mode — reviewing only new commits since the last review.")

    return {
        "phase": "SETUP",
        "title": "Parse Input",
        "situation": situation,
        "actions": actions,
        "handoff": None,
    }


# ---------------------------------------------------------------------------
# Step 2: Repo Setup (PR mode + interactive only)
# ---------------------------------------------------------------------------

def _step_2_repo_setup(mode, state, context, config, output_dir):
    """Step 2: Repo Setup — checkout PR branch, stash changes."""
    pr_number = config.get("pr_number", "")
    gh_cmd = context.get("github_cli_command", "gh")
    ws_result = state.get("workspace_setup_result")

    manual_fallback = [
        "1. `git status` — check for uncommitted changes",
        "2. If dirty: `git stash push -u -m 'pr-review-auto-stash'`",
        "3. `git branch --show-current` — record current branch",
        f"4. `{gh_cmd} pr checkout {pr_number}`",
    ]

    if ws_result and ws_result.get("checkout_ok"):
        # Success path
        original_branch = ws_result.get("original_branch", "unknown")
        situation = [
            f"Workspace set up for PR #{pr_number}. Was on `{original_branch}`, now on PR branch.",
        ]
        stash_ref = ws_result.get("stash_ref")
        if stash_ref:
            situation.append(f"Stashed uncommitted changes (ref: `{stash_ref}`).")
        actions = ["Workspace ready. Proceed to next step."]

    elif ws_result and ws_result.get("error"):
        # Failure path
        situation = [
            f"Automatic workspace setup for PR #{pr_number} failed: {ws_result['error']}",
        ]
        actions = ["Manual fallback:"] + manual_fallback

    else:
        # No result path
        situation = [
            f"Setting up workspace for PR #{pr_number}. Automatic setup did not run.",
        ]
        actions = ["Manual setup required:"] + manual_fallback

    return {
        "phase": "SETUP",
        "title": "Repo Setup",
        "situation": situation,
        "actions": actions,
        "handoff": None,
    }


# ---------------------------------------------------------------------------
# Step 3: Gather Context
# ---------------------------------------------------------------------------

def _format_pr_metadata(context):
    """Format PR metadata for step 3 situation."""
    pr = context.get("pr", {})
    lines = []
    if pr.get("title"):
        lines.append(f"**PR:** #{pr.get('number', '?')} — \"{pr['title']}\" by {pr.get('author', '?')}")
    if pr.get("labels"):
        lines.append(f"**Labels:** {', '.join(pr['labels'])}")
    if pr.get("is_draft"):
        lines.append("**Status:** Draft PR")
    return lines


def _format_reviews_summary(context):
    """Format existing review summary for step 3 situation."""
    reviews = context.get("reviews", {})
    summary = reviews.get("summary", {})
    reviewers = reviews.get("reviewers", [])
    pending = reviews.get("pending", [])
    lines = []

    if summary.get("total", 0) > 0:
        parts = []
        for key in ("approved", "changes_requested", "commented"):
            count = summary.get(key, 0)
            if count > 0:
                parts.append(f"{count} {key.replace('_', ' ')}")
        reviewer_details = []
        for r in reviewers:
            reviewer_details.append(f"{r['login']} ({r.get('type', '?')}, {r.get('state', '?')})")
        lines.append(f"**Reviews:** {', '.join(parts) if parts else 'none'}")
        if reviewer_details:
            lines.append(f"**Reviewers:** {'; '.join(reviewer_details)}")
        if pending:
            lines.append(f"**Pending:** {', '.join(pending)}")
        lines.append("This is a re-review.")
    else:
        lines.append("**Reviews:** No existing reviews. This is the first review.")

    return lines


def _format_size(context):
    """Format PR/change size info."""
    pr_size = context.get("pr_size", {})
    category = pr_size.get("category", "unknown")
    files = pr_size.get("files", 0)
    lines = pr_size.get("lines", 0)
    return f"**Size:** {category} ({files} files, {lines} lines changed)"


def _format_staleness(context):
    """Format staleness info with freshen suggestion."""
    staleness = context.get("staleness", {})
    if not staleness.get("is_stale"):
        return []
    behind = staleness.get("commits_behind", 0)
    lines = [
        f"⚠️  **Branch is stale:** {behind} commits behind the base branch.",
        "Consider rebasing or freshening the base branch before review for the most accurate results.",
    ]
    return lines


def _format_domain_counts(context):
    """Compute and format domain file counts from changed files."""
    git = context.get("git", {})
    changed_files = git.get("changed_files", [])
    if not changed_files:
        return []

    # Simple domain detection by file extension/path patterns
    domain_counts = {}
    for f in changed_files:
        fl = f.lower()
        domains = set()
        if fl.endswith((".php",)):
            domains.add("code")
        if fl.endswith((".js", ".ts", ".jsx", ".tsx")):
            domains.add("code")
        if fl.endswith((".py",)):
            domains.add("code")
        if "test" in fl or "spec" in fl:
            if fl.endswith((".php",)):
                domains.add("php-tests")
            elif fl.endswith((".js", ".ts", ".jsx", ".tsx")):
                domains.add("js-tests")
            elif fl.endswith((".py",)):
                domains.add("code")
        if fl.endswith((".css", ".scss")):
            domains.add("code")
        if not domains:
            domains.add("code")
        for d in domains:
            domain_counts[d] = domain_counts.get(d, 0) + 1

    if not domain_counts:
        return []

    parts = [f"{d}: {c}" for d, c in sorted(domain_counts.items())]
    return [f"**Domains:** {', '.join(parts)}"]


def _format_linked_issues(context):
    """Format linked issues — reference at top, details at bottom."""
    issues = context.get("linked_issues", [])
    if not issues:
        return [], []

    # Top reference
    top_lines = [f"**Linked issues:** {', '.join(str(i) for i in issues)}"]

    # Bottom details for fetched issues
    details = context.get("linked_issues_details", [])
    bottom_lines = []
    if details:
        bottom_lines.append("--- LINKED ISSUE DETAILS ---")
        for issue in details:
            bottom_lines.append(f"### {issue.get('id', '?')}: {issue.get('title', '?')}")
            if issue.get("body"):
                bottom_lines.append(issue["body"])
            bottom_lines.append("")
        bottom_lines.append("--- END LINKED ISSUE DETAILS ---")

    return top_lines, bottom_lines


def _change_purpose_handoff(output_dir):
    """Shared handoff instructions for writing change-purpose.md."""
    return [
        f"Write a brief change-purpose summary to `{output_dir or '<OUTPUT_DIR>'}/change-purpose.md`.",
        "Include: what the change does, why it's being made, and what to focus on during review.",
        "Attribute intent to its source (\"the PR description states...\", \"the linked issue asks for...\") "
        "and keep author-asserted discriminators, assumptions, and likelihood claims recognizable as "
        "claims — downstream stages treat this summary as material to verify, not as established fact.",
        "Verify the file exists before proceeding.",
    ]


def _step_3_gather_context(mode, state, context, config, output_dir):
    """Step 3: Gather Context — present curated briefing."""
    git = context.get("git", {})
    situation = []
    actions = []
    handoff = None

    # Git range
    git_range = git.get("git_range", "")
    if git_range:
        situation.append(f"**Git range:** `{git_range}`")

    # Commit count
    commit_count = git.get("commit_count", 0)
    if commit_count:
        situation.append(f"**Commits:** {commit_count}")

    # Size
    if context.get("pr_size"):
        situation.append(_format_size(context))

    # PR metadata (PR mode only)
    if mode == "pr":
        situation.extend(_format_pr_metadata(context))

    # Reviews summary (PR mode only)
    if mode == "pr" and context.get("reviews"):
        situation.extend(_format_reviews_summary(context))

    # Staleness
    situation.extend(_format_staleness(context))

    # Domain counts
    situation.extend(_format_domain_counts(context))

    # Linked issues
    issue_top, issue_bottom = _format_linked_issues(context)
    situation.extend(issue_top)
    if issue_bottom:
        situation.append("")
        situation.extend(issue_bottom)

    # Diff stats
    diff_stats = git.get("diff_stats", "")
    if diff_stats:
        situation.append(f"**Diff stats:**\n```\n{diff_stats}\n```")
        situation.append("")

    # Host context status — if present, show a one-line summary.
    host_context = context.get("host_context")
    if host_context:
        banner = host_context.get("banner") or {}
        resolved = host_context.get("resolved", [])
        runtime_count = sum(1 for e in resolved if e.get("kind") == "runtime-host")
        library_root_count = sum(1 for e in resolved if e.get("kind") == "library-dep")
        if banner.get("degraded"):
            situation.append(
                f"**Host context:** ⚠ degraded ({banner.get('reason')}) — "
                f"{runtime_count} runtime-hosts, {library_root_count} dependency roots resolved."
            )
        else:
            situation.append(
                f"**Host context:** {runtime_count} runtime-hosts, "
                f"{library_root_count} dependency roots resolved."
            )

    # Actions
    actions.append("Review the context above and write the change-purpose summary.")

    # Change-purpose handoff — only when no unfetched issues
    has_unfetched = state.get("resolved_params", {}).get("has_unfetched_issues", False)
    if not has_unfetched:
        handoff = _change_purpose_handoff(output_dir)

    return {
        "phase": "SETUP",
        "title": "Gather Context",
        "situation": situation,
        "actions": actions,
        "handoff": handoff,
    }


# ---------------------------------------------------------------------------
# Step 4: Fetch Issue Context (conditional — has_unfetched_issues)
# ---------------------------------------------------------------------------

def _step_4_fetch_issues(mode, state, context, config, output_dir):
    """Step 4: Fetch Issue Context — Linear issues via MCP."""
    issues = context.get("linked_issues", [])
    # Filter to Linear IDs only
    import re as _re
    linear_ids = [i for i in issues if _re.match(r'^[A-Z]+-\d+$', str(i))]

    situation = [
        f"Linked Linear issues detected: {', '.join(linear_ids)}",
        "These issues need fetching for you to get the full context.",
    ]

    actions = [
        "Use the Linear MCP server to fetch the details for each linked issue:",
        "",
    ]
    for issue_id in linear_ids:
        actions.append(f"- Fetch **{issue_id}**: title, description, status, labels")
    actions.append("")
    actions.append("After fetching, you'll have enough context to write the change purpose.")

    handoff = _change_purpose_handoff(output_dir)

    return {
        "phase": "SETUP",
        "title": "Fetch Issue Context",
        "situation": situation,
        "actions": actions,
        "handoff": handoff,
    }


# ---------------------------------------------------------------------------
# Step 5: Dispatch Plan + Triage
# ---------------------------------------------------------------------------

def _step_5_dispatch_plan(mode, state, context, config, output_dir):
    """Present the planner baseline for main-orchestrator adjustment."""
    od = output_dir or "<OUTPUT_DIR>"

    situation = [_PHASE_TRANSITIONS["EXECUTION"], ""]
    actions = []

    plan_summary = state.get("dispatch_plan_summary", {})
    plan_agents = state.get("dispatch_plan_agents", [])
    plan_warnings = state.get("dispatch_plan_warnings", [])

    # Coverage warnings go FIRST — a degraded-coverage signal must not be buried
    # under the agent list. Unrecognized-source files would otherwise produce a
    # clean review for code no reviewer read.
    if plan_warnings:
        for w in plan_warnings:
            situation.append(f"⚠️  {w}")
        situation.append("")

    if plan_summary:
        situation.append(
            f"Dispatch plan computed: {plan_summary.get('dispatched', 0)} agents to dispatch, "
            f"{plan_summary.get('skipped', 0)} skipped, "
            f"{plan_summary.get('conditional', 0)} conditional."
        )

    # Build human-readable dispatch summary from agent details
    if plan_agents:
        is_quick = config.get("quick", False) if config else False
        # In quick mode, filter out SKIPPED_QUICK_MODE agents from display
        visible_agents = [
            a for a in plan_agents
            if not (is_quick and a["status"] == SKIPPED_QUICK_MODE)
        ]
        dispatched = [a for a in visible_agents if a["status"] in DISPATCHED_STATUSES]
        skipped = [a for a in visible_agents if a["status"] in SKIPPED_STATUSES]

        if dispatched:
            situation.append("")
            situation.append("**Dispatching:**")
            for a in dispatched:
                focus = a.get("focus", "")
                label = f"{a['name']} — {focus}" if focus else a['name']
                reason = a["reason"]
                if reason and reason != "always dispatch (domain has files)":
                    situation.append(f"- {label} ({reason})")
                else:
                    situation.append(f"- {label}")

        if skipped:
            situation.append("")
            situation.append("**Skipped:**")
            for a in skipped:
                focus = a.get("focus", "")
                label = f"{a['name']} — {focus}" if focus else a['name']
                situation.append(f"- {label} | {a['reason']}")
    else:
        situation.append("(Dispatch plan will be computed by the script at runtime.)")

    actions.append(
        "**Main orchestrator adjustment rule: Lean toward skipping.** The planner "
        "handles keyword/file-type signals, while you, the main orchestrator, have "
        "read the diff and understand the change semantically. Use that to adjust "
        "the plan:"
    )
    actions.append(
        '- Agents with reason "conditional (domain has files, no triage signal to skip)" '
        "were dispatched by default, not evidence — skip if their focus is clearly "
        "irrelevant to the change."
    )
    actions.append(
        "- Only force-dispatch a skipped agent when you're confident it will find "
        "something the plan missed."
    )
    actions.append("")
    actions.append(
        f"To record a main orchestrator adjustment, edit `{od}/dispatch-plan.json`:"
    )
    actions.append('- Force-skip a dispatched agent: set status to `"SKIPPED_OVERRIDE"` with `"override_reason": "..."`')
    actions.append('- Force-dispatch a skipped agent: set status to `"DISPATCH_OVERRIDE"` with `"override_reason": "..."`')

    if config and config.get("quick"):
        actions.append("")
        actions.append(
            "**Quick mode active.** Be aggressive with skips — uncertain value means skip."
        )

    additional = config.get("additional_instructions") if config else None
    if additional:
        actions.append("")
        actions.append("## Reviewer-Requested Focus")
        actions.append(f"> {additional}")
        actions.append("")
        actions.append(
            "Ensure the dispatch plan covers this focus. Adjust skipped agents if "
            "they're relevant to this guidance."
        )

    return {
        "phase": "EXECUTION",
        "title": "Dispatch Plan + Triage",
        "situation": situation,
        "actions": actions,
        "handoff": None,
    }


# ---------------------------------------------------------------------------
# Step 6: Dispatch Agents
# ---------------------------------------------------------------------------

def _step_6_dispatch_agents(mode, state, context, config, output_dir):
    """Step 6: Dispatch Agents — parallel agent dispatch with concrete calls."""
    git = context.get("git", {})
    git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")
    dispatched = state.get("dispatched_agents", [])
    od = output_dir or "<OUTPUT_DIR>"

    situation = [
        f"{len(dispatched)} agents ready for dispatch." if dispatched else
        "Agents will be dispatched based on the dispatch plan.",
    ]

    codex_host = _host(config) == HOST_CODEX
    if codex_host:
        actions = [
            "Dispatch ALL eligible reviewers in parallel with multiple `spawn_agent` calls.",
            "Issue the calls together rather than waiting for one reviewer before starting the next.",
            "For each subagent, use the canonical reviewer definition and exact bootstrap command below.",
            "",
        ]
    else:
        actions = [
            "Dispatch ALL eligible agents in a SINGLE message with MULTIPLE Agent tool calls.",
            "Each agent runs in parallel - do NOT dispatch them one at a time.",
            "",
        ]

    if dispatched:
        if codex_host:
            actions.append("Codex subagent dispatch inputs:")
        else:
            actions.append("Agent dispatch calls (copy each to an Agent tool):")
        actions.append("")
        for agent in dispatched:
            name = agent.get("name", agent) if isinstance(agent, dict) else agent
            adapter = agent.get("adapter") if isinstance(agent, dict) else None
            agent_type = adapter or name
            if adapter:
                # Repo-contributed reviewer: dispatch the generic adapter
                # subagent, parameterized with this reviewer's ref. The Agent
                # tool's subagent_type MUST be the adapter (a real CC subagent),
                # not the synthetic instance name.
                scope_domains = ",".join(agent.get("scope_domains") or ["code"])
                # ref/label/id all originate from the reviewed repo's
                # .pirategoat/config.json (PR-controlled, semi-trusted), and the
                # adapter is instructed to run this command in a shell. Every
                # token MUST be shell-quoted to prevent command injection. Use
                # `or` (not dict.get default) so an explicit None falls back
                # instead of embedding the literal string "None".
                cmd_parts = [
                    "python3", f"{SCRIPTS_DIR}/agent/bootstrap.py",
                    "--agent", adapter,
                    "--instance-name", name,
                    "--repo-agent-ref", agent.get("ref") or "",
                    "--adapter-label", agent.get("label") or name,
                    "--execution", agent.get("execution") or "inline",
                    "--channel", agent.get("channel") or "blocking",
                    "--scope-domains", scope_domains,
                    "--range", git_range,
                    "--output-dir", od,
                ]
                cmd = " ".join(shlex.quote(p) for p in cmd_parts)
                model = agent.get("model")
                model_hint = f" with model `{model}`" if model else ""
                if codex_host:
                    actions.append(f"**{name}** (repo reviewer adapter):")
                else:
                    actions.append(
                        f"**{name}** (repo reviewer - dispatch as subagent_type "
                        f"`{adapter}`{model_hint}):"
                    )
                if codex_host:
                    actions.append(
                        f"- Call `spawn_agent` with task name "
                        f"`{_codex_task_name(agent_type)}` and no Claude model "
                        "override. The instance identity travels in the "
                        "`--instance-name` argument of the bootstrap command below, "
                        "not the task name."
                    )
                    actions.append(
                        f"- {_codex_agent_instruction(agent_type)} Then run the exact "
                        "bootstrap command below and follow the emitted scope and "
                        "output contract."
                    )
                actions.append("```")
                actions.append(cmd)
                actions.append("```")
                actions.append("")
            else:
                actions.append(f"**{name}:**")
                if codex_host:
                    actions.append(
                        f"- Call `spawn_agent` with task name `{_codex_task_name(name)}`."
                    )
                    actions.append(
                        f"- {_codex_agent_instruction(agent_type)} Then run the exact "
                        "bootstrap command below and follow the emitted scope and "
                        "output contract."
                    )
                actions.append("```")
                actions.append(f'python3 {SCRIPTS_DIR}/agent/bootstrap.py --agent {name} --range "{git_range}" --output-dir "{od}"')
                actions.append("```")
                actions.append("")

    actions.append("Monitor progress at any time:")
    actions.append(f"```")
    actions.append(f"python3 {SCRIPTS_DIR}/agents_status.py --output-dir \"{od}\"")
    actions.append(f"```")

    return {
        "phase": "EXECUTION",
        "title": "Dispatch Agents",
        "situation": situation,
        "actions": actions,
        "handoff": None,
    }


# ---------------------------------------------------------------------------
# Step 7: Save Review Baseline
# ---------------------------------------------------------------------------

def _step_7_save_baseline(mode, state, context, config, output_dir):
    """Step 7: Save Review Baseline — script writes file internally."""
    git = context.get("git", {})
    git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")

    situation = [
        f"Review baseline saved to `.branch-review-baseline.json`.",
    ]

    actions = []
    if mode == "incremental":
        actions.append(
            "Baseline saved. Next `/code-review` will only cover new commits from this point forward."
        )
    elif mode == "full":
        actions.append(
            "Review baseline saved. Next `/code-review` on this branch will start from this point."
        )
    else:  # PR mode
        actions.append("Review baseline saved.")

    actions.append("")
    actions.append("The script wrote `.branch-review-baseline.json` with the current HEAD SHA, "
                   "timestamp, review type, and git range.")

    actions.append("")
    actions.append("**Wait for agents before step 8.** Agents run in the background — "
                   "wait for notifications, then check status:")
    actions.append(f"```")
    actions.append(f"python3 {SCRIPTS_DIR}/agents_status.py --output-dir \"{output_dir or '<OUTPUT_DIR>'}\"")
    actions.append(f"```")
    actions.append("- Exit code 0 (ALL_DONE): proceed to step 8")
    actions.append("- Exit code 2 (still running): wait for more notifications, re-check")
    actions.append("- NOT_DISPATCHED agents: dispatch them first, then re-check")

    return {
        "phase": "EXECUTION",
        "title": "Save Review Baseline",
        "situation": situation,
        "actions": actions,
        "handoff": None,
    }


# ---------------------------------------------------------------------------
# Step 8: Reconcile + Verify
# ---------------------------------------------------------------------------

def _step_8_reconcile(mode, state, context, config, output_dir):
    """Step 8: Reconcile + Verify — dispatch reconciliator with all context."""
    # Readiness gate: if agents are still running, wait for them
    waiting = state.get("waiting_on_agents")
    if waiting and waiting.get("running"):
        # Record when we first started waiting (preserve across retries)
        if "first_waiting_at" not in waiting:
            waiting["first_waiting_at"] = datetime.now(timezone.utc).isoformat()

        # Time-based escalation: if we've waited longer than the agent timeout
        # plus a grace period, force-proceed with available results
        agent_timeout = waiting.get("agent_timeout_seconds", DEFAULT_AGENT_TIMEOUT)
        escalation_threshold = agent_timeout + 60  # 60s grace period
        try:
            first_waiting = datetime.fromisoformat(waiting["first_waiting_at"])
            elapsed = (datetime.now(timezone.utc) - first_waiting).total_seconds()
        except (ValueError, KeyError):
            elapsed = 0

        if elapsed >= escalation_threshold:
            # Escalate: proceed with whatever agents completed
            running = waiting["running"]
            elapsed_min = int(elapsed // 60)
            # Clear waiting state so reconciliation proceeds
            state.pop("waiting_on_agents", None)
            # Store escalation warning for the normal reconciliation briefing
            state["_escalation_warning"] = (
                f"**Escalation:** Waited {elapsed_min}m for {len(running)} agent(s) "
                f"that never finished: {', '.join(running)}. "
                f"**Use {_stop_operation(config)} on these agents before proceeding.**"
            )
            # Don't return — fall through to normal reconciliation briefing below
        else:
            # Not yet escalated — return WAITING briefing
            running = waiting["running"]
            od = output_dir or "<OUTPUT_DIR>"
            situation = [
                f"**Waiting:** {len(running)} agent(s) still running: {', '.join(running)}",
                "Reconciliation cannot start until all dispatched agents have finished.",
            ]
            not_dispatched = waiting.get("not_dispatched", [])
            if not_dispatched:
                situation.append(
                    f"**Also not dispatched:** {', '.join(not_dispatched)} — dispatch these first."
                )
            actions = [
                "Wait for running agents to finish, then re-run this step:",
                f"```",
                f"python3 {SCRIPTS_DIR}/agents_status.py --output-dir \"{od}\"",
                f"```",
                "When ALL_DONE is true, re-run step 8.",
            ]
            return {
                "phase": "SYNTHESIS",
                "title": "Reconcile + Verify — WAITING",
                "situation": situation,
                "actions": actions,
                "handoff": None,
                "blocks_progress": True,
            }

    # Normal reconciliation briefing (agents done or escalated)
    escalation = state.pop("_escalation_warning", None)

    git = context.get("git", {})
    git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")
    changed_files_csv = git.get("changed_files_csv", "")
    od = output_dir or "<OUTPUT_DIR>"
    agents_state = state.get("agents", {})
    dispatched = agents_state.get("dispatched", [])
    completed = agents_state.get("completed", [])
    failed = agents_state.get("failed", [])
    change_purpose = state.get("change_purpose")
    commit_messages = state.get("commit_messages", [])

    situation = [
        _PHASE_TRANSITIONS["SYNTHESIS"],
        "",
        f"**Agents dispatched:** {', '.join(dispatched) if dispatched else 'see dispatch plan'}",
        f"**Agents completed:** {', '.join(completed) if completed else 'check status'}",
    ]
    if failed:
        situation.append(f"**Agents failed:** {', '.join(failed)}")

    if change_purpose:
        situation.append(f"**Change purpose (author-stated — claims to verify, not established fact):** {change_purpose}")
    elif commit_messages:
        situation.append(f"**Change purpose (derived from commits — claims to verify, not established fact):** {'; '.join(commit_messages[:3])}")

    if escalation:
        situation.insert(0, escalation)
        situation.insert(1, "")

    stop_operation = _stop_operation(config)
    actions = [
        (f"**1. {stop_operation}** stuck agents that exceeded their timeout."
         if escalation else
         f"**1. {stop_operation}** all remaining background review agents."),
        "",
    ]
    if _host(config) == HOST_CODEX and dispatched:
        actions.extend([
            "Codex task targets:",
            ", ".join(f"`{_codex_task_name(name)}`" for name in dispatched),
            "",
        ])
    if _host(config) == HOST_CODEX:
        actions.extend([
            "**2. Call `spawn_agent` with task name `review_reconciliator` "
            "for `review-reconciliator`.**",
            f"- {_codex_agent_instruction('review-reconciliator')}",
            "- Then provide these concrete inputs:",
        ])
    else:
        actions.append("**2. Dispatch `review-reconciliator`** with:")
    actions.extend([
        f"- **Reconciliation context:** `{od}/reconciliation-context.md` (pre-gathered Markdown briefing: all agent findings, source snippets, scope annotations)",
        f"- **Output builder path:** `{SCRIPTS_DIR / 'agent' / 'output.py'}`",
        f"- Output directory: `{od}`",
    ])

    if change_purpose:
        actions.append(
            f"- **Change purpose (author-stated):** {change_purpose}"
        )
        actions.append(
            "  Treat it as claims to verify against the diff, not context to adopt — "
            "author-asserted discriminators and likelihood claims are review inputs, not conclusions."
        )

    actions.append("")
    actions.append(f"**Expected output:** `{od}/review-findings.json` + `{od}/review-findings.md`")

    additional = config.get("additional_instructions") if config else None
    if additional:
        actions.append("")
        actions.append("**Reviewer-Requested Focus:**")
        actions.append(f"> {additional}")
        actions.append("Give additional weight to findings addressing this guidance.")

    handoff = [
        f"Verify `{od}/review-findings.json` and `{od}/review-findings.md` both exist before proceeding.",
    ]

    return {
        "phase": "SYNTHESIS",
        "title": "Reconcile + Verify",
        "situation": situation,
        "actions": actions,
        "handoff": handoff,
    }


# ---------------------------------------------------------------------------
# Step 9: Review Report Synthesis
# ---------------------------------------------------------------------------

# Default output instructions for PR mode
_DEFAULT_OUTPUT_INSTRUCTIONS_PR = """\
Address the PR author by first name — use a warm, collegial tone.
Be specific and actionable, not vague.
Acknowledge intent or effort before raising concerns.
Frame suggestions collaboratively: "What if we...?" not "You should..."
Say what's genuinely good, plainly.

STRUCTURE:
- Brief human recap (3-5 short bullets: what you noticed, what matters)
- Below a ---, detailed findings in a collapsible <details> block
- For each finding: the file/line, what's wrong, what to do about it
- Group findings by severity (critical > important > consider)

Include a clear verdict recommendation and summary of key findings.
Keep it actionable — every finding should have a concrete recommendation.
Carry every reconciled finding into the report as a finding — do not demote
one into narrative-only "tradeoff"/"note to be aware of" prose. Never present
an unverified likelihood claim ("rare", "narrow corner", "coincidental") as
fact; if the findings don't verify it, don't assert it.
"""

_DEFAULT_OUTPUT_INSTRUCTIONS_BRANCH = """\
Be specific and actionable, not vague.
Frame suggestions collaboratively.

STRUCTURE:
- Brief summary of key findings
- Detailed findings grouped by severity (critical > important > consider)
- For each finding: the file/line, what's wrong, what to do about it

Include a clear verdict recommendation and summary of key findings.
Keep it actionable — every finding should have a concrete recommendation.
Carry every reconciled finding into the report as a finding — do not demote
one into narrative-only "tradeoff"/"note to be aware of" prose. Never present
an unverified likelihood claim ("rare", "narrow corner", "coincidental") as
fact; if the findings don't verify it, don't assert it.
"""


def _step_9_review_report(mode, state, context, config, output_dir):
    """Step 9: Review Report Synthesis — generate the review report."""
    od = output_dir or "<OUTPUT_DIR>"
    degradation = state.get("degradation", {})

    situation = []
    actions = []

    change_purpose = state.get("change_purpose")
    commit_messages = state.get("commit_messages", [])

    if change_purpose:
        situation.append(f"**Change purpose (author-stated — the reconciled findings, not this framing, are the source of truth):** {change_purpose}")
    elif commit_messages:
        situation.append(f"**Change purpose (from commits — the reconciled findings, not this framing, are the source of truth):** {'; '.join(commit_messages[:3])}")

    if degradation.get("reconciliation_failed"):
        situation.append("⚠️ Reconciliation failed — working with raw agent output in degraded mode.")
        actions.append("Read the individual agent review files directly (raw agent output).")
        actions.append("Synthesize them manually into a coherent review report.")
        actions.append("")

    # Resolve output instructions
    output_instructions = config.get("output_instructions")
    if output_instructions:
        # Caller-provided override — use verbatim
        actions.append("**Output instructions (caller override):**")
        actions.append(output_instructions)
    else:
        # Default instructions based on mode
        if mode == "pr":
            pr = context.get("pr", {})
            author_name = pr.get("author_name", "")
            if author_name:
                first_name = author_name.split()[0]
                instructions = _DEFAULT_OUTPUT_INSTRUCTIONS_PR.replace(
                    "Address the PR author by first name",
                    f"Address {first_name} by name"
                )
            else:
                instructions = _DEFAULT_OUTPUT_INSTRUCTIONS_PR
            actions.append("**Output instructions (default — PR mode):**")
            actions.append(instructions)
        else:
            actions.append("**Output instructions (default — branch mode):**")
            actions.append(_DEFAULT_OUTPUT_INSTRUCTIONS_BRANCH)

    actions.append("")
    actions.append(f"Write `{od}/review-report.md` with: findings summary, critical/important "
                   "issues highlighted, and a verdict (APPROVE, REQUEST_CHANGES, or COMMENT).")
    actions.append(f"Source: `{od}/review-findings.json` and `{od}/review-findings.md`.")

    # Host context banner passthrough — if degraded, surface message at top
    host_context = context.get("host_context")
    banner = (host_context or {}).get("banner") or {}
    if banner.get("degraded"):
        actions.append("")
        actions.append(
            f"**Host context banner:** prepend this blockquote to the top of "
            f"`review-report.md` (reconciliator already did the same for "
            f"`review-findings.md`):"
        )
        actions.append("")
        actions.append(f"> **⚠ Host Context Banner:** {banner.get('message', '')}")
        actions.append("")

    # Inline coverage gaps — computed deterministically at reconciliation and
    # loaded into state by _orchestrate_step. A starved review must not
    # present as a clean one, regardless of reconciliator diligence.
    gaps = state.get("inline_coverage_gaps") or {}
    if gaps:
        actions.append("")
        actions.append(
            f"**⚠ Review coverage:** {len(gaps)} changed file(s) were skipped "
            "by every matching agent's diff budget and no reviewer reported "
            "reviewing them from the deferred NOT DIFFED queue. Include a "
            "'Review coverage' section in `review-report.md` listing them; "
            "the verdict must acknowledge this gap:"
        )
        for f_path, agents in sorted(gaps.items()):
            agents_list = agents if isinstance(agents, list) else [str(agents)]
            actions.append(f"- `{f_path}` (skipped by: {', '.join(agents_list)})")
        actions.append("")

    handoff = [
        f"Verify `{od}/review-report.md` exists before proceeding.",
    ]

    return {
        "phase": "SYNTHESIS",
        "title": "Review Report Synthesis",
        "situation": situation,
        "actions": actions,
        "handoff": handoff,
    }


# ---------------------------------------------------------------------------
# Step 10: Decision Critic
# ---------------------------------------------------------------------------

def _step_10_decision_critic(mode, state, context, config, output_dir):
    """Step 10: Decision Critic — stress-test conclusions."""
    od = output_dir or "<OUTPUT_DIR>"

    # Quick-mode critic skip: low-risk verdicts don't need stress-testing
    is_quick = config.get("quick", False)
    recon_verdict = state.get("reconciliation_verdict", "")
    skip_critic = is_quick and recon_verdict.lower() in ("approve", "comment")

    if skip_critic:
        # Map reconciliation verdict to review verdict
        review_verdict = "APPROVE" if recon_verdict.lower() == "approve" else "COMMENT"

        situation = [
            _PHASE_TRANSITIONS["VALIDATION"],
            f"Quick mode is active and reconciliation verdict is **{recon_verdict}** — "
            "skipping the decision critic. Low-risk verdicts do not need stress-testing.",
        ]
        actions = [
            f"Write the critic skip verdict:",
            f"```json",
            f'// Save to: {od}/decision-critic-verdict.json',
            f'{{"verdict": "SKIPPED", "reason": "quick mode, reconciliation verdict: {recon_verdict}"}}',
            f"```",
            f"",
            f"Write the final review verdict:",
            f"```json",
            f'// Save to: {od}/review-verdict.json',
            f'{{"verdict": "{review_verdict}"}}',
            f"```",
            f"",
            f"Before proceeding, verify both files exist and contain valid JSON.",
        ]
        handoff = [
            f"`{od}/decision-critic-verdict.json` and `{od}/review-verdict.json` must both exist with valid JSON.",
        ]

        return {
            "phase": "VALIDATION",
            "title": "Decision Critic",
            "situation": situation,
            "actions": actions,
            "handoff": handoff,
        }

    degradation = state.get("degradation", {})

    situation = [_PHASE_TRANSITIONS["VALIDATION"]]
    actions = []

    # Determine which file the critic should review
    if degradation.get("report_synthesis_failed"):
        critic_target = f"{od}/review-findings.md"
        situation.append("⚠️ Review report synthesis failed — critic will review review-findings.md instead.")
    else:
        critic_target = f"{od}/review-report.md"

    has_findings = not degradation.get("reconciliation_failed")
    findings_path = f"{od}/review-findings.json"
    critic_context_path = f"{od}/critic-context.md"

    if has_findings:
        # Build curated critic context before dispatching
        actions.append("Build the critic context document:")
        actions.append("```bash")
        actions.append(
            f'python3 -c "\nimport sys, json, pathlib\n'
            f"sys.path.insert(0, str(pathlib.Path('{SCRIPTS_DIR}').parent))\n"
            f"from review.reconciliation_context import build_critic_context\n"
            f"report = pathlib.Path('{critic_target}').read_text()\n"
            f"findings = json.loads(pathlib.Path('{findings_path}').read_text())\n"
            f"pathlib.Path('{critic_context_path}').write_text(build_critic_context(report, findings))\n"
            f'"'
        )
        actions.append("```")
        actions.append("")

    if _host(config) == HOST_CODEX:
        actions.append(
            "Call `spawn_agent` with task name `decision_reviewer` for "
            "`decision-reviewer` to stress-test the review conclusions."
        )
        actions.append(
            _codex_agent_instruction("decision-reviewer")
        )
    else:
        actions.append(
            "Dispatch the `decision-reviewer` agent to stress-test the review conclusions."
        )
    actions.append("")
    actions.append("Use this dispatch prompt:")
    actions.append("```")
    if has_findings:
        actions.append(f"Critic context (report + structured findings): {critic_context_path}")
        actions.append(f"Report path (for critic.py --report): {critic_target}")
    else:
        actions.append(f"Review report to stress-test: {critic_target}")
        actions.append(f"No structured findings available (reconciliation failed) — critique the report directly without --context.")
    actions.append(f"Output directory: {od}")
    actions.append(f"Context: <one-line summary of PR scope, verdict, and finding count>")
    actions.append(f"Return STAND, REVISE, or ESCALATE with findings written to {od}/decision-critic-findings.md.")
    actions.append("```")
    actions.append("")
    actions.append("**Wait for the critic to finish — do not run in background.**")
    actions.append("")
    actions.append("Act on the critic's verdict:")
    actions.append("")
    actions.append("**STAND** — No changes needed. Proceed to writing verdict files.")
    actions.append("")
    actions.append(f"**REVISE** — 1) Read critic's recommendations → 2) Spot-check claims "
                   f"with `git grep`/`Read` → 3) Edit `{od}/review-report.md` to fix verified "
                   f"issues → 4) Write verdict files.")
    actions.append("")
    actions.append("**ESCALATE** — Override review verdict to **COMMENT** regardless of report, "
                   "then write verdict files.")
    actions.append("")
    actions.append("Write the critic's verdict (before any adjustments):")
    actions.append(f"```json")
    actions.append(f'// Save to: {od}/decision-critic-verdict.json')
    actions.append(f'{{"verdict": "<STAND | REVISE | ESCALATE>"}}')
    actions.append(f"```")
    actions.append("")
    actions.append("Then write the final review verdict:")
    actions.append(f"```json")
    actions.append(f'// Save to: {od}/review-verdict.json')
    actions.append(f'{{"verdict": "<APPROVE | REQUEST_CHANGES | COMMENT>"}}')
    actions.append(f"```")

    handoff = [
        f"`{od}/decision-critic-verdict.json` and `{od}/review-verdict.json` must both exist with valid JSON.",
    ]

    return {
        "phase": "VALIDATION",
        "title": "Decision Critic",
        "situation": situation,
        "actions": actions,
        "handoff": handoff,
    }


# ---------------------------------------------------------------------------
# Step 11: Present Results
# ---------------------------------------------------------------------------

def _step_11_present_results(mode, state, context, config, output_dir):
    """Step 11: Present Results — show review output."""
    od = output_dir or "<OUTPUT_DIR>"
    is_interactive = config.get("interactive", True)
    degradation = state.get("degradation", {})
    critic_verdict = state.get("critic_verdict")
    forced_verdict = state.get("forced_verdict")
    review_verdict = state.get("review_verdict")

    situation = [_PHASE_TRANSITIONS["OUTPUT"]]
    actions = []

    if is_interactive:
        actions.append(f"Read `{od}/review-report.md` and present a formatted summary "
                       "with verdict and key findings.")

        if critic_verdict == "unavailable" or degradation.get("critic_failed"):
            actions.append("⚠️ Critic verdict unavailable — present review as-is.")

        if forced_verdict:
            actions.append(f"⚠️ Verdict forced to **{forced_verdict}** — degraded pipeline.")

        actions.append("To drill down on a specific topic, re-invoke the reconciliator "
                       "in focused mode.")

        if mode == "incremental":
            actions.append("Baseline saved. Next `/code-review` reviews only new commits.")

    else:
        # Non-interactive: list output files
        actions.append("PIPELINE COMPLETE. Output files:")
        actions.append(f"- `{od}/review-report.md`")
        actions.append(f"- `{od}/review-findings.json` + `review-findings.md`")
        actions.append(f"- `{od}/pipeline-result.json` — status, verdict, report_path, "
                       "findings_path, critic_verdict, degradation_notes")

        if mode == "incremental":
            actions.append("Baseline saved. Next run reviews only new commits.")

    return {
        "phase": "OUTPUT",
        "title": "Present Results",
        "situation": situation,
        "actions": actions,
        "handoff": None,
    }


# ---------------------------------------------------------------------------
# Step 12: Cleanup
# ---------------------------------------------------------------------------

def _step_12_cleanup(mode, state, context, config, output_dir):
    """Step 12: Cleanup — restore workspace (interactive only)."""
    ws = state.get("workspace", {})
    original_branch = ws.get("original_branch")
    stash_ref = ws.get("stash_ref")

    situation = []
    actions = []

    if original_branch:
        situation.append(f"Original branch: `{original_branch}`"
                         + (f". Stashed changes: `{stash_ref}`" if stash_ref else ""))

        actions.append("Ask user before restoring workspace:")
        actions.append(f"- `git checkout {original_branch}`")
        if stash_ref:
            actions.append("- `git stash pop`")
    else:
        actions.append("No workspace changes to restore.")

    return {
        "phase": "OUTPUT",
        "title": "Cleanup",
        "situation": situation,
        "actions": actions,
        "handoff": None,
    }


# ---------------------------------------------------------------------------
# Output Formatting
# ---------------------------------------------------------------------------

def format_output(step, guidance):
    """Format guidance into curated-context-pipeline output."""
    lines = []

    # Header
    phase = guidance["phase"]
    title = guidance["title"]
    lines.append(f"{'═' * 60}")
    lines.append(f"REVIEW PIPELINE Step {step} — {phase}: {title}")
    lines.append(f"{'═' * 60}")
    lines.append("")

    # Skip explanation (if steps were skipped to get here)
    skip_reason = guidance.get("skip_reason")
    if skip_reason:
        lines.append(f"ℹ️  {skip_reason}")
        lines.append("")

    # Situation
    if guidance.get("situation"):
        lines.append("## SITUATION")
        lines.append("")
        for item in guidance["situation"]:
            lines.append(item)
        lines.append("")

    # Actions
    if guidance.get("actions"):
        lines.append("## ACTIONS")
        lines.append("")
        for item in guidance["actions"]:
            lines.append(item)
        lines.append("")

    # Handoff
    if guidance.get("handoff"):
        lines.append("## HANDOFF — Required before proceeding")
        lines.append("")
        for item in guidance["handoff"]:
            lines.append(f"- {item}")
        lines.append("")

    # Next step pointer or completion
    next_step = guidance.get("next_step")
    if guidance.get("blocks_progress"):
        lines.append(f"{'─' * 60}")
        lines.append("⏸️  PIPELINE WAITING")
        lines.append("")
        lines.append("Complete the actions above, then re-run this step.")
        lines.append(f"Run: python3 {SCRIPTS_DIR / 'pipeline.py'} --step {step} --output-dir <OUTPUT_DIR>")
    elif next_step:
        lines.append(f"{'─' * 60}")
        ns = next_step
        lines.append(f"➡️  Next: Step {ns['step']} — {ns['title']}")
        if ns.get("skip_reason"):
            lines.append(f"    ({ns['skip_reason']})")
        lines.append("")
        lines.append(f"Run: python3 {SCRIPTS_DIR / 'pipeline.py'} --step {ns['step']} --output-dir <OUTPUT_DIR>")
    else:
        lines.append(f"{'─' * 60}")
        lines.append("✅ PIPELINE COMPLETE")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telemetry Integration (best-effort)
# ---------------------------------------------------------------------------

def _init_telemetry(output_dir, log_dir=None):
    """Import and initialize ReviewTelemetry. Returns None on failure."""
    try:
        import importlib.util
        telemetry_path = SCRIPTS_DIR / "telemetry.py"
        spec = importlib.util.spec_from_file_location("review_telemetry", telemetry_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Check for env override
        env_log_dir = os.environ.get("PIRATEGOAT_TELEMETRY_LOG_DIR")
        return mod.ReviewTelemetry(output_dir, log_dir=env_log_dir or log_dir)
    except Exception:
        return None


_SEMVER_PATTERN = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
_SEMVER_ROOT_RE = re.compile(rf"^{_SEMVER_PATTERN}$")
_CHANGELOG_VERSION_RE = re.compile(rf"^## \[({_SEMVER_PATTERN})\]", re.MULTILINE)
# Full SHA-1 (40 hex) or SHA-256 (64 hex) object name.
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def _git_output(*args):
    """Return one Git identity value, or an empty string when unavailable."""
    try:
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.DEVNULL, timeout=5
        ).strip()
    except Exception:
        return ""


def _detect_plugin_version(plugin_root=None):
    """Return the installed or source-checkout plugin version, best-effort."""
    try:
        root = Path(plugin_root) if plugin_root is not None else SCRIPTS_DIR.parent.parent
        if _SEMVER_ROOT_RE.fullmatch(root.name):
            return root.name

        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        match = _CHANGELOG_VERSION_RE.search(changelog)
        return match.group(1) if match else ""
    except Exception:
        return ""


def _resolve_git_identity(git_range, base_sha="", head_sha=""):
    """Resolve requested range endpoints without mutating Git.

    Omitted endpoints around ``..`` or ``...`` default to ``HEAD``. For a
    three-dot range, ``base_sha`` is the resolved left endpoint, not the Git
    merge base; later context or manifest collection can record that value.
    """
    requested_range = git_range if isinstance(git_range, str) else ""
    base_ref = ""
    head_ref = ""
    has_range_operator = False
    if "..." in requested_range:
        base_ref, head_ref = requested_range.split("...", 1)
        has_range_operator = True
    elif ".." in requested_range:
        base_ref, head_ref = requested_range.split("..", 1)
        has_range_operator = True

    base_ref = base_ref.strip()
    head_ref = head_ref.strip()
    if has_range_operator:
        base_ref = base_ref or "HEAD"
        head_ref = head_ref or "HEAD"

    # Supplied context values may be symbolic (an explicit range like
    # "main..HEAD" stores "main" as the context merge_base). The durable
    # manifest must record COMMIT identity: ^{commit} both resolves refs and
    # peels annotated tags, whose plain rev-parse would return the tag
    # OBJECT id — even a full-hex supplied value can be a tag object.
    def resolve_endpoint(supplied, ref):
        for candidate in (supplied if isinstance(supplied, str) else "", ref):
            if not candidate:
                continue
            peeled = _git_output(
                "rev-parse", "--verify", f"{candidate}^{{commit}}"
            )
            if peeled:
                return peeled
            if _FULL_SHA_RE.fullmatch(candidate):
                # Git unavailable — an already-full object id is the best
                # obtainable identity.
                return candidate
        return ""

    return (
        requested_range,
        resolve_endpoint(base_sha, base_ref),
        resolve_endpoint(head_sha, head_ref or "HEAD"),
    )


# ---------------------------------------------------------------------------
# Subprocess Helper
# ---------------------------------------------------------------------------

def _run_subprocess(cmd, cwd=None, timeout=60):
    """Run a subprocess and return (stdout, success). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        if r.returncode == 0:
            return r.stdout.strip(), True
        print(f"WARNING: {cmd[0]} exited {r.returncode}: {r.stderr[:200]}", file=sys.stderr)
        return r.stdout.strip(), False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"WARNING: {cmd[0]} failed: {e}", file=sys.stderr)
        return "", False


# ---------------------------------------------------------------------------
# Step Orchestration (side effects — subprocesses, file I/O)
# ---------------------------------------------------------------------------

def _orchestrate_step(step, mode, config, state, context, output_dir):
    """Run step-specific side effects (subprocesses, file I/O).

    Called by main() BEFORE get_step_guidance(). Mutates state and context
    in place. Returns the (possibly updated) context dict.
    """
    context_path = os.path.join(output_dir, "review-context.json")

    if step == 2:
        # Run workspace_setup.py to stash, record branch, checkout PR
        pr_number = config.get("pr_number", "")
        if pr_number:
            setup_cmd = [
                sys.executable, str(SCRIPTS_DIR / "workspace_setup.py"),
                "--pr-number", str(pr_number),
            ]
            stdout, ok = _run_subprocess(setup_cmd, timeout=60)
            if ok and stdout:
                try:
                    ws_result = json.loads(stdout)
                    state["workspace"]["original_branch"] = ws_result.get("original_branch")
                    state["workspace"]["stash_ref"] = ws_result.get("stash_ref")
                    state["workspace_setup_result"] = ws_result
                except (json.JSONDecodeError, KeyError):
                    state["workspace_setup_result"] = {"error": "Failed to parse script output"}
            else:
                state["workspace_setup_result"] = {
                    "error": "workspace_setup.py failed or produced no output",
                    "checkout_ok": False,
                }

    if step == 3:
        # Run context.py to collect git context, PR metadata, etc.
        gather_cmd = [sys.executable, str(SCRIPTS_DIR / "context.py"),
                      "--output-dir", output_dir]
        if mode == "pr":
            pr_number = config.get("pr_number", "")
            if pr_number:
                gather_cmd.extend(["--pr-number", pr_number])
        else:
            gather_cmd.append("--branch")
            if mode == "incremental":
                gather_cmd.append("--incremental")
        git_range = config.get("git_range")
        if git_range:
            gather_cmd.extend(["--git-range", git_range])

        stdout, ok = _run_subprocess(gather_cmd, timeout=CONTEXT_GATHER_TIMEOUT)
        # Re-read context (context.py writes review-context.json)
        if os.path.isfile(context_path):
            try:
                with open(context_path) as f:
                    context = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        # Hydrate state from gathered context
        if context.get("has_unfetched_issues"):
            state["resolved_params"]["has_unfetched_issues"] = True
        # Store git range in resolved_params for downstream steps
        git = context.get("git", {})
        if git.get("git_range"):
            state["resolved_params"]["git_range"] = git["git_range"]

    if step == 5:
        # Run plan_dispatch.py to determine which agents to dispatch
        git = context.get("git", {})
        git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")
        if git_range:
            planner_cmd = [
                sys.executable, str(SCRIPTS_DIR / "plan_dispatch.py"),
                "--mode", mode,
                "--git-range", git_range,
                "--output-dir", output_dir,
            ]
            changed_csv = git.get("changed_files_csv", "")
            if changed_csv:
                planner_cmd.extend(["--changed-files-list", changed_csv])
            # Pass review context for PR metadata triage (title, body, labels, branch, issues)
            ctx_path = os.path.join(output_dir, "review-context.json")
            if os.path.isfile(ctx_path):
                planner_cmd.extend(["--review-context", ctx_path])
            if config.get("quick"):
                planner_cmd.append("--quick")

            stdout, ok = _run_subprocess(planner_cmd, timeout=60)

            plan_path = os.path.join(output_dir, "dispatch-plan.json")
            if os.path.isfile(plan_path):
                try:
                    plan = _load_dispatch_plan(plan_path)
                    if ok:
                        _preserve_initial_dispatch_plan(output_dir, plan)
                    agents = plan["agents"]
                    state["dispatch_plan_summary"] = {
                        "dispatched": sum(1 for a in agents if a.get("status") in DISPATCHED_STATUSES),
                        "skipped": sum(1 for a in agents if a.get("status") in SKIPPED_STATUSES),
                        "conditional": sum(1 for a in agents if a.get("status") in DISPATCHED_STATUSES and "conditional" in a.get("reason", "").lower()),
                    }
                    # Store agent details for human-readable step 5 summary
                    state["dispatch_plan_agents"] = [
                        {
                            "name": a["name"],
                            "focus": a.get("focus", ""),
                            "status": a.get("status", ""),
                            "reason": a.get("reason", ""),
                        }
                        for a in agents
                    ]
                    # Surface coverage warnings (e.g. unrecognized source language).
                    state["dispatch_plan_warnings"] = plan.get("warnings", [])
                except (json.JSONDecodeError, OSError):
                    state["dispatch_plan_summary"] = {}
                    state["dispatch_plan_agents"] = []
                    state["dispatch_plan_warnings"] = []
        else:
            state["dispatch_plan_summary"] = {}
            state["dispatch_plan_agents"] = []
            state["dispatch_plan_warnings"] = []

    if step == 6:
        plan_path = os.path.join(output_dir, "dispatch-plan.json")
        if os.path.isfile(plan_path):
            try:
                plan = _load_dispatch_plan(plan_path)
                dispatched = [
                    {
                        "name": a["name"],
                        "domain": a.get("domain", ""),
                        # Adapter fields (present only for repo-contributed
                        # reviewers). Carried so step 6 can emit the ref-mode
                        # bootstrap command instead of a plain --agent call.
                        "adapter": a.get("adapter"),
                        "ref": a.get("ref"),
                        "label": a.get("label"),
                        "channel": a.get("channel"),
                        "execution": a.get("execution"),
                        "model": a.get("model"),
                        "scope_domains": a.get("scope_domains"),
                    }
                    for a in plan.get("agents", [])
                    if a.get("status") in DISPATCHED_STATUSES
                ]
                state["dispatched_agents"] = dispatched
                # Recompute dispatch_plan_summary from final plan (post-override)
                all_agents = plan["agents"]
                state["dispatch_plan_summary"] = {
                    "dispatched": sum(
                        1 for a in all_agents
                        if a.get("status") in DISPATCHED_STATUSES
                    ),
                    "skipped": sum(
                        1 for a in all_agents
                        if a.get("status") in SKIPPED_STATUSES
                    ),
                    "conditional": sum(
                        1 for a in all_agents
                        if a.get("status") in DISPATCHED_STATUSES
                        and "conditional" in a.get("reason", "").lower()
                    ),
                }
            except (json.JSONDecodeError, OSError):
                state["dispatched_agents"] = []
        else:
            state["dispatched_agents"] = []

    if step == 7:
        git = context.get("git", {})
        git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")
        base_ref = git.get("base_ref", "main")

        head_sha, _ = _run_subprocess(["git", "rev-parse", "HEAD"])
        if not head_sha or len(head_sha) < 7:
            head_sha = "0000000"

        baseline_path = os.path.join(output_dir, ".branch-review-baseline.json")
        review_count = 0
        if os.path.isfile(baseline_path):
            try:
                with open(baseline_path) as f:
                    old = json.load(f)
                review_count = old.get("review_count", 0)
            except (json.JSONDecodeError, OSError):
                pass

        baseline = {
            "last_reviewed_sha": head_sha,
            "last_reviewed_at": datetime.now(timezone.utc).isoformat(),
            "review_type": mode,
            "review_count": review_count + 1,
            "base_ref": base_ref,
            "git_range_used": git_range or f"{head_sha}..HEAD",
        }
        with open(baseline_path, "w") as f:
            json.dump(baseline, f, indent=2)

    if step == 8:
        # Hard readiness gate: check if all dispatched agents have finished
        # before allowing reconciliation to proceed.
        # Exit code 0 = all done, 2 = agents still running, 1 = error.
        status_cmd = [
            sys.executable, str(SCRIPTS_DIR / "agents_status.py"),
            "--output-dir", output_dir,
        ]
        try:
            r = subprocess.run(status_cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 2:
                previous_waiting = state.get("waiting_on_agents", {})
                # Agents still running — parse text output for names
                running = []
                not_dispatched = []
                for line in r.stdout.splitlines():
                    stripped = line.strip()
                    if "RUNNING" in stripped and "NOT_DISPATCHED" not in stripped:
                        # Lines look like: "agent-name           RUNNING   (3m 42s)"
                        name = stripped.split()[0]
                        running.append(name)
                    elif "NOT_DISPATCHED" in stripped:
                        name = stripped.split()[0]
                        not_dispatched.append(name)
                state["waiting_on_agents"] = {
                    "running": running,
                    "not_dispatched": not_dispatched,
                    "status_output": r.stdout.strip(),
                }
                if previous_waiting.get("first_waiting_at"):
                    state["waiting_on_agents"]["first_waiting_at"] = previous_waiting["first_waiting_at"]
                else:
                    state["waiting_on_agents"]["first_waiting_at"] = datetime.now(timezone.utc).isoformat()
                # Read per-agent timeout for escalation threshold
                agent_timeout = DEFAULT_AGENT_TIMEOUT
                ctx_path = os.path.join(output_dir, "review-context.json")
                if os.path.isfile(ctx_path):
                    try:
                        with open(ctx_path) as f:
                            ctx_data = json.load(f)
                        agent_timeout = ctx_data.get("review", {}).get(
                            "agent_timeout_seconds", DEFAULT_AGENT_TIMEOUT
                        )
                    except (json.JSONDecodeError, OSError):
                        pass
                state["waiting_on_agents"]["agent_timeout_seconds"] = agent_timeout
                try:
                    first_waiting = datetime.fromisoformat(
                        state["waiting_on_agents"]["first_waiting_at"]
                    )
                    elapsed = (datetime.now(timezone.utc) - first_waiting).total_seconds()
                except (ValueError, KeyError):
                    elapsed = 0
                if elapsed < agent_timeout + AGENT_WAIT_GRACE_SECONDS:
                    return context
            else:
                state.pop("waiting_on_agents", None)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # Gate is best-effort; if checker fails, proceed normally and
            # avoid carrying stale waiting state forward.
            state.pop("waiting_on_agents", None)

        cp_path = os.path.join(output_dir, "change-purpose.md")
        if os.path.isfile(cp_path):
            try:
                with open(cp_path) as f:
                    state["change_purpose"] = f.read().strip()
            except OSError:
                pass

        git = context.get("git", {})
        git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")
        if git_range and not state.get("change_purpose"):
            log_out, _ = _run_subprocess(["git", "log", "--format=%s", git_range])
            if log_out:
                state["commit_messages"] = log_out.strip().split("\n")

        plan_path = os.path.join(output_dir, "dispatch-plan.json")
        if os.path.isfile(plan_path):
            try:
                plan = _load_dispatch_plan(plan_path)
                dispatched_names = [
                    a["name"] for a in plan["agents"]
                    if a.get("status") in DISPATCHED_STATUSES
                ]
                review_files = []
                completed = []
                for name in dispatched_names:
                    review_file = os.path.join(output_dir, f"{name.replace('-reviewer', '-review')}.json")
                    if os.path.isfile(review_file):
                        completed.append(name)
                        review_files.append(review_file)
                state["agents"] = {
                    "dispatched": dispatched_names,
                    "completed": completed,
                    "failed": [],
                    "review_files": review_files,
                }
            except (json.JSONDecodeError, OSError):
                pass

        # Build reconciliation context (pre-gather all data for the reconciliator)
        recon_ctx_cmd = [
            sys.executable, str(SCRIPTS_DIR / "reconciliation_context.py"),
            "--output-dir", output_dir,
            "--git-range", git_range,
            "--changed-files", context.get("git", {}).get("changed_files_csv", ""),
        ]
        cp = state.get("change_purpose", "")
        if cp:
            recon_ctx_cmd.extend(["--change-purpose", cp])
        pr_id = config.get("pr_number", "")
        if pr_id:
            recon_ctx_cmd.extend(["--pr-id", str(pr_id)])
        # Pass dispatched agents when real dispatch metadata exists.
        # Distinguish three cases:
        # 1. dispatched is non-empty → pass agent names (filter to those agents)
        # 2. dispatched is empty BUT dispatch-plan.json exists → plan ran and
        #    selected 0 agents (e.g., docs-only change). Pass empty string so
        #    reconciliation_context.py loads nothing (not stale files).
        # 3. No dispatch plan file → truly unknown, omit flag so
        #    reconciliation_context.py falls back to scanning all *-review.json.
        agents_info = state.get("agents")
        if agents_info is not None:
            dispatched = agents_info.get("dispatched", [])
            if dispatched:
                recon_ctx_cmd.extend(["--dispatched-agents", ",".join(dispatched)])
            elif os.path.isfile(plan_path):
                recon_ctx_cmd.extend(["--dispatched-agents", ""])
        _, ctx_ok = _run_subprocess(recon_ctx_cmd, timeout=30)
        recon_ctx_path = os.path.join(output_dir, "reconciliation-context.md")
        if not ctx_ok or not os.path.isfile(recon_ctx_path):
            raise RuntimeError(
                "reconciliation_context.py failed — cannot proceed to "
                "reconciliation without a valid context file. "
                f"Check stderr above. Expected: {recon_ctx_path}"
            )

    if step == 9:
        # Load inline coverage gaps computed at reconciliation so the report
        # briefing can surface files no reviewer saw inline — deterministic,
        # not dependent on the reconciliator having carried them forward.
        recon_json_path = os.path.join(output_dir, "reconciliation-context.json")
        gaps = {}
        if os.path.isfile(recon_json_path):
            try:
                with open(recon_json_path) as f:
                    recon = json.load(f)
                coverage = recon.get("inline_coverage") or {}
                if isinstance(coverage, dict):
                    raw_gaps = coverage.get("files_never_inline") or {}
                    if isinstance(raw_gaps, dict):
                        gaps = raw_gaps
            except (json.JSONDecodeError, OSError):
                gaps = {}
        state["inline_coverage_gaps"] = gaps

    if step == 10:
        # Read reconciliation verdict for quick-mode critic skip decision
        findings_path = os.path.join(output_dir, "review-findings.json")
        if os.path.isfile(findings_path):
            try:
                with open(findings_path) as f:
                    findings = json.load(f)
                state["reconciliation_verdict"] = findings.get("verdict", "")
            except (json.JSONDecodeError, OSError):
                state["reconciliation_verdict"] = ""

        # Record critic skip decision for telemetry.
        # Clear any stale decision first (step 10 may be rerun after
        # review-findings.json changes from approve/comment to a higher verdict).
        state.setdefault("step_decisions", {}).pop(str(step), None)
        is_quick = config.get("quick", False)
        recon_verdict = state.get("reconciliation_verdict", "")
        if is_quick and recon_verdict.lower() in ("approve", "comment"):
            state["step_decisions"][str(step)] = {
                "critic_skipped": True,
                "reason": f"quick mode + reconciliation verdict: {recon_verdict}",
            }

    if step == 11:
        # Read critic verdict from file (written by LLM at step 10)
        critic_path = os.path.join(output_dir, "decision-critic-verdict.json")
        if os.path.isfile(critic_path):
            try:
                with open(critic_path) as f:
                    critic_data = json.load(f)
                raw_verdict = critic_data.get("verdict", "unavailable")
                # Map SKIPPED → unavailable so downstream consumers
                # (pirategoat-bot) correctly show "not cross-validated"
                state["critic_verdict"] = (
                    "unavailable" if raw_verdict == "SKIPPED" else raw_verdict
                )
            except (json.JSONDecodeError, OSError):
                state["critic_verdict"] = "unavailable"
        else:
            state["critic_verdict"] = "unavailable"

        verdict_path = os.path.join(output_dir, "review-verdict.json")
        verdict_data = None
        if os.path.isfile(verdict_path):
            try:
                with open(verdict_path) as f:
                    verdict_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        report_path = os.path.join(output_dir, "review-report.md")
        findings_path = os.path.join(output_dir, "review-findings.json")
        degradation_notes = []

        if not verdict_data:
            degradation_notes.append("review-verdict.json not found")
        if not os.path.isfile(report_path):
            degradation_notes.append("review-report.md not found")
            alt = os.path.join(output_dir, "review-findings.md")
            report_path = alt if os.path.isfile(alt) else None
        if not os.path.isfile(findings_path):
            degradation_notes.append("review-findings.json not found")

        verdict = verdict_data.get("verdict", "COMMENT") if verdict_data else "COMMENT"
        status = "success" if not degradation_notes else "degraded"

        # Rule 23: update review-findings.json verdict to match
        if verdict_data and os.path.isfile(findings_path):
            try:
                with open(findings_path) as f:
                    findings = json.load(f)
                findings["verdict"] = verdict
                with open(findings_path, "w") as f:
                    json.dump(findings, f, indent=2)
            except (json.JSONDecodeError, OSError):
                pass

        pipeline_result = {
            "status": status,
            "verdict": verdict,
            "report_path": report_path if report_path and os.path.isfile(report_path) else None,
            "findings_path": findings_path if os.path.isfile(findings_path) else None,
            "critic_verdict": state.get("critic_verdict", "unavailable"),
            "degradation_notes": degradation_notes,
            "review_baseline_saved": os.path.isfile(
                os.path.join(output_dir, ".branch-review-baseline.json")
            ),
        }
        result_path = os.path.join(output_dir, "pipeline-result.json")
        with open(result_path, "w") as f:
            json.dump(pipeline_result, f, indent=2)

        state["verdict"] = verdict
        state["review_verdict"] = verdict
        state["pipeline_status"] = status

    return context


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Unified review pipeline")
    parser.add_argument("--step", type=int, required=True, help="Step number (1-12)")
    parser.add_argument("--mode", choices=["pr", "full", "incremental"],
                        help="Review mode")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--pr-number", help="PR number (PR mode)")
    parser.add_argument("--session-id", help="Claude session ID for telemetry correlation")
    parser.add_argument("--interactive", type=lambda x: x.lower() in ("true", "1", "yes"),
                        default=None, help="Interactive mode (default: true)")
    parser.add_argument("--output-instructions", help="Custom output instructions")
    parser.add_argument("--git-range", help="Explicit git range")
    parser.add_argument("--original-branch", help="Branch to restore on cleanup")
    parser.add_argument("--stash-ref", help="Stash ref to restore on cleanup")
    parser.add_argument("--quick", action="store_true", default=False,
                        help="Quick review mode: fewer agents, conditional critic skip")
    parser.add_argument("--host", choices=SUPPORTED_HOSTS, default=None,
                        help="Orchestration host (default on first call: claude)")

    args = parser.parse_args()
    output_dir = args.output_dir
    step = args.step

    # Ensure output dir exists
    os.makedirs(output_dir, exist_ok=True)
    context = read_review_context(output_dir)

    # --- Step 1: Special handling (seed config, clean artifacts) ---
    if step == 1:
        # Clean stale artifacts first
        clean_stale_artifacts(output_dir)

        # Resolve mode: config wins, then CLI, then error
        existing_config = read_config(output_dir)
        mode = existing_config.get("mode") or args.mode
        if not mode:
            print("ERROR: --mode is required on the first call", file=sys.stderr)
            sys.exit(2)

        # Write/update run-config.json (seed from CLI on first call)
        if not existing_config.get("mode"):
            config = {
                "mode": mode,
                "host": args.host or HOST_CLAUDE,
            }
            if args.pr_number:
                config["pr_number"] = args.pr_number
            if args.interactive is not None:
                config["interactive"] = args.interactive
            else:
                config["interactive"] = True
            if args.output_instructions:
                config["output_instructions"] = args.output_instructions
            if args.git_range:
                config["git_range"] = args.git_range
            if args.session_id is not None:
                config["session_id"] = args.session_id
            config["quick"] = args.quick
            write_config(output_dir, config)
        else:
            config = existing_config
            config_changed = False
            if "host" not in config:
                config["host"] = args.host or HOST_CLAUDE
                config_changed = True
            elif args.host is not None and config.get("host") != args.host:
                config["host"] = args.host
                config_changed = True
            # On interactive rerun, sync --quick from CLI into config.
            # Without this, a quick→normal rerun stays in quick mode,
            # and a normal→quick rerun was already handled.
            # In bot mode (interactive: false), the bot pre-writes the
            # correct quick value in run-config.json and subsequent steps
            # may not pass --quick on the CLI (especially with custom
            # prompt overrides), so we must not overwrite the bot's value.
            if config.get("interactive", True) and config.get("quick") != args.quick:
                config["quick"] = args.quick
                config_changed = True
            if config_changed:
                write_config(output_dir, config)
            # Session identity follows the same interactive/bot split as
            # quick: interactive reruns reuse output dirs and run-config.json
            # survives cleanup, so the CLI is authoritative INCLUDING
            # absence — an omitted --session-id means this run's session is
            # unknown, and retaining the previous run's ID would correlate
            # telemetry with the old Claude transcript. Bot runs pre-seed
            # the ID in run-config.json and may omit the flag on reruns.
            if config.get("interactive", True):
                cli_session = args.session_id or ""
                if config.get("session_id", "") != cli_session:
                    if cli_session:
                        config["session_id"] = cli_session
                    else:
                        config.pop("session_id", None)
                    write_config(output_dir, config)
            elif (
                args.session_id is not None
                and config.get("session_id") != args.session_id
            ):
                config["session_id"] = args.session_id
                write_config(output_dir, config)

        # Interactive output directories may be reused, so prior-run context
        # cannot remain authoritative until step 3 gathers it afresh. Bot runs
        # are non-interactive and retain their precomputed context contract.
        if config.get("interactive", True):
            context = _reset_interactive_review_context(output_dir)

        # Initialize fresh pipeline state
        state = json.loads(json.dumps(_DEFAULT_STATE))
        now = datetime.now(timezone.utc)
        identifier = config.get("pr_number", "branch")
        state["run_id"] = (
            f"{now.strftime('%Y%m%dT%H%M%S')}-{mode}-{identifier}-"
            f"{uuid.uuid4().hex[:8]}"
        )

        # Persist workspace params
        if args.original_branch:
            state["workspace"]["original_branch"] = args.original_branch
        if args.stash_ref:
            state["workspace"]["stash_ref"] = args.stash_ref

        write_state(output_dir, state)

        # Telemetry: start
        telemetry = _init_telemetry(output_dir)
        if telemetry:
            try:
                pr_number = config.get("pr_number", "")
                bot_mode = not config.get("interactive", True)
                quick_mode = config.get("quick", False)
                repo_path = _git_output("rev-parse", "--show-toplevel")
                # Identifier: PR number for pr mode, branch name otherwise
                identifier = pr_number
                if not identifier:
                    identifier = _git_output("branch", "--show-current")
                git_context = (
                    context.get("git", {})
                    if not config.get("interactive", True)
                    else {}
                )
                config_git_range = config.get("git_range", "")
                context_git_range = git_context.get("git_range", "")
                git_range = config_git_range or context_git_range
                context_matches_range = (
                    not config_git_range or config_git_range == context_git_range
                )
                context_base_sha = (
                    git_context.get("merge_base", "") if context_matches_range else ""
                )
                context_head_sha = (
                    git_context.get("head_sha", "") if context_matches_range else ""
                )
                git_range, base_sha, head_sha = _resolve_git_identity(
                    git_range, base_sha=context_base_sha,
                    head_sha=context_head_sha,
                )
                telemetry.start(pr_number=pr_number, total_steps=12,
                                bot_mode=bot_mode, quick_mode=quick_mode,
                                mode=mode, repo_path=repo_path,
                                identifier=identifier,
                                run_id=state["run_id"],
                                session_id=config.get("session_id", ""),
                                plugin_version=_detect_plugin_version(),
                                git_range=git_range, base_sha=base_sha,
                                head_sha=head_sha)
            except Exception:
                pass

    else:
        # Steps 2+: read existing config and state
        config = read_config(output_dir)
        mode = config.get("mode") or args.mode
        if not mode:
            print("ERROR: No mode found in run-config.json and --mode not provided",
                  file=sys.stderr)
            sys.exit(2)

        state = read_state(output_dir)

        # Persist workspace params if provided
        if args.original_branch:
            state["workspace"]["original_branch"] = args.original_branch
        if args.stash_ref:
            state["workspace"]["stash_ref"] = args.stash_ref

        # Telemetry: log step (deferred until after orchestration, see below)

    # Validate step number
    if step not in _STEP_MAP:
        print(f"ERROR: Invalid step {step}. Valid steps: 1-12", file=sys.stderr)
        sys.exit(1)

    # --- Step-specific orchestration ---
    # A dispatch plan that fails validation is operator-actionable (step 5 invites
    # hand-editing statuses), so surface it as a clean CLI error instead of a
    # traceback. Matches agents_status.py, the other consumer of that contract.
    try:
        context = _orchestrate_step(step, mode, config, state, context, output_dir)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)

    # Telemetry: log step (after orchestration so decisions are available)
    if step > 1:
        telemetry = _init_telemetry(output_dir)
        if telemetry:
            try:
                step_def = _STEP_MAP.get(step, {})
                bot_mode = not config.get("interactive", True)
                decisions = state.get("step_decisions", {}).get(str(step))
                telemetry.log_step(
                    step=step, phase=step_def.get("phase", ""),
                    title=step_def.get("title", ""),
                    bot_mode=bot_mode,
                    decisions=decisions,
                )
            except Exception:
                pass

    # Check for hard error: non-interactive PR without pre-computed context
    if mode == "pr" and not config.get("interactive", True):
        git_ctx = context.get("git", {})
        if not git_ctx.get("merge_base") and step <= 2:
            print("PIPELINE STOPPED: Non-interactive PR mode requires pre-computed "
                  "review-context.json with a valid merge_base.", file=sys.stderr)
            sys.exit(1)

    # --- Get guidance ---
    guidance = get_step_guidance(step, mode, state, context, config=config,
                                output_dir=output_dir)
    if guidance is None:
        print(f"ERROR: No guidance for step {step}", file=sys.stderr)
        sys.exit(1)

    blocks_progress = guidance.get("blocks_progress", False)

    # --- Update state ---
    if not blocks_progress and step not in state.get("completed_steps", []):
        state.setdefault("completed_steps", []).append(step)
    write_state(output_dir, state)

    # --- Compute routing AFTER orchestration/guidance (state may have changed) ---
    active = get_active_steps(mode, config, state, context)

    # Add next step info
    next_info = None if blocks_progress else compute_next_step(step, active)
    guidance["next_step"] = next_info
    if next_info:
        guidance["skip_reason"] = next_info.get("skip_reason")
    else:
        guidance["skip_reason"] = None

    # Telemetry: finalize at last active step
    if next_info is None and not blocks_progress and telemetry:
        try:
            step_def = _STEP_MAP.get(step, {})
            bot_mode = not config.get("interactive", True)
            telemetry.finalize(
                step=step, phase=step_def.get("phase", ""),
                title=step_def.get("title", ""),
                bot_mode=bot_mode,
            )
        except Exception:
            pass

    # --- Format and output ---
    output = format_output(step, guidance)
    print(output)


if __name__ == "__main__":
    main()

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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

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
    "dispatch-plan.json",
    "*-review.json",
    "review-findings.json",
    "review-findings.md",
    "review-report.md",
    "review-verdict.json",
    "pipeline-result.json",
    "decision-critic-findings.md",
    "change-purpose.md",
]

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
    situation = []
    actions = []

    if mode == "pr":
        pr_number = config.get("pr_number")
        if pr_number:
            situation.append(f"Mode: PR review (PR #{pr_number})")
            actions.append(f"PR #{pr_number} confirmed. The pipeline will review this pull request.")
            actions.append("")
            actions.append("The pipeline script will run gather-review-context.py at step 3 to "
                           "collect git context, PR metadata, and review history.")
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
            actions.append("The pipeline will review all changes on this branch against the base branch.")

    elif mode == "incremental":
        situation.append("Mode: Incremental branch review")
        if context.get("no_new_commits"):
            actions.append("⛔ PIPELINE STOPPED: No new commits since the last review.")
            actions.append("There is nothing new to review. Make more commits and try again.")
        else:
            actions.append("Running in incremental mode — the pipeline will review only new commits since the last review.")
            actions.append("If no previous review state exists, this will behave like a full review.")

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

    situation = [
        f"Setting up workspace for PR #{pr_number} review.",
        "Need to checkout the PR branch and stash any uncommitted changes.",
    ]

    actions = [
        "1. Check for uncommitted changes with `git status`",
        "2. If dirty: run `git stash push -u -m 'pr-review-auto-stash'`",
        "3. Record the current branch name with `git branch --show-current`",
        f"4. Checkout the PR branch: `{gh_cmd} pr checkout {pr_number}`",
        "",
        "Pass the workspace state on the next pipeline call:",
        f"    --original-branch <CURRENT_BRANCH> --stash-ref <STASH_REF_IF_STASHED>",
    ]

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

    # Actions
    actions.append("Review the context above. The pipeline has gathered all available data.")
    if not git_range:
        actions.append("The git range will be determined by gather-review-context.py.")

    # Change-purpose handoff — only when no unfetched issues
    has_unfetched = state.get("resolved_params", {}).get("has_unfetched_issues", False)
    if not has_unfetched:
        handoff = [
            f"Write a brief change-purpose summary to `{output_dir or '<OUTPUT_DIR>'}/change-purpose.md`",
            "Include: what the change does, why it's being made, and what to focus on during review.",
        ]

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

    handoff = [
        f"Write a brief change-purpose summary to `{output_dir or '<OUTPUT_DIR>'}/change-purpose.md`",
        "Include: what the change does, why it's being made, and what to focus on during review.",
    ]

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
    """Step 5: Dispatch Plan + Triage — present planner output, allow overrides."""
    git = context.get("git", {})
    git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")

    situation = []
    actions = []

    # The script runs plan-review-dispatch.py internally and stores the output
    plan_output = state.get("dispatch_plan_output", "")
    plan_summary = state.get("dispatch_plan_summary", {})

    if plan_summary:
        situation.append(
            f"Dispatch plan computed: {plan_summary.get('dispatched', 0)} agents to dispatch, "
            f"{plan_summary.get('skipped', 0)} skipped, "
            f"{plan_summary.get('conditional', 0)} conditional."
        )

    actions.append(
        "The dispatch planner's decisions are authoritative. Review the plan below and override "
        "only if you have specific domain knowledge that contradicts the planner's analysis."
    )
    actions.append("")

    if plan_output:
        actions.append("--- DISPATCH PLAN ---")
        actions.append(plan_output)
        actions.append("--- END DISPATCH PLAN ---")
    else:
        actions.append("(Dispatch plan output will be provided by the script at runtime.)")

    actions.append("")
    actions.append("To override an agent's status, edit `dispatch-plan.json` in the output directory:")
    actions.append('- To force-dispatch a skipped agent: set status to `"DISPATCH_OVERRIDE"` with `"override_reason": "..."`')
    actions.append('- To force-skip a dispatched agent: set status to `"SKIPPED_OVERRIDE"` with `"override_reason": "..."`')
    actions.append("")
    actions.append("Example overrides in dispatch-plan.json:")
    actions.append('```json')
    actions.append('{"name": "dead-code-reviewer", "status": "DISPATCH_OVERRIDE", "override_reason": "Large refactor with deletions"}')
    actions.append('{"name": "go-tests-reviewer", "status": "SKIPPED_OVERRIDE", "override_reason": "No Go code in this repo"}')
    actions.append('```')

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

    actions = [
        "Dispatch ALL eligible agents in a SINGLE message with MULTIPLE Agent tool calls.",
        "Each agent runs in parallel — do NOT dispatch them one at a time.",
        "",
    ]

    if dispatched:
        actions.append("Agent dispatch calls (copy each to an Agent tool):")
        actions.append("")
        for agent in dispatched:
            name = agent.get("name", agent) if isinstance(agent, dict) else agent
            actions.append(f"**{name}:**")
            actions.append(f"```")
            actions.append(f'python3 {SCRIPTS_DIR}/bootstrap-reviewer.py --agent {name} --range "{git_range}" --output-dir "{od}"')
            actions.append(f"```")
            actions.append("")

    actions.append("After dispatching all agents, you can monitor their progress at any time:")
    actions.append(f"```")
    actions.append(f"python3 {SCRIPTS_DIR}/check-reviewer-agent-status.py --output-dir \"{od}\"")
    actions.append(f"```")
    actions.append("")
    actions.append("Use the Agent tool for dispatching. Each agent should run via "
                   "`bootstrap-reviewer.py` which handles scope discovery, protocol extraction, "
                   "and output instructions.")

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
    actions.append("**Before proceeding to step 8:** Verify all review agents have finished.")
    actions.append(f"```")
    actions.append(f"python3 {SCRIPTS_DIR}/check-reviewer-agent-status.py --output-dir \"{output_dir or '<OUTPUT_DIR>'}\"")
    actions.append(f"```")
    actions.append("- Exit code 0 → all agents finished or timed out — proceed to step 8.")
    actions.append("- Exit code 2 → agents still running — wait 30 seconds, re-check.")
    actions.append("- Only proceed to step 8 when ALL agents are done or timed out.")

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
        f"**Agents dispatched:** {', '.join(dispatched) if dispatched else 'see dispatch plan'}",
        f"**Agents completed:** {', '.join(completed) if completed else 'check status'}",
    ]
    if failed:
        situation.append(f"**Agents failed:** {', '.join(failed)}")

    if change_purpose:
        situation.append(f"**Change purpose:** {change_purpose}")
    elif commit_messages:
        situation.append(f"**Change purpose (derived from commits):** {'; '.join(commit_messages[:3])}")

    actions = [
        "**First:** Stop any remaining background review agents. Their work has either completed "
        "(review files written) or will not be incorporated. Use TaskStop to force-stop all "
        "remaining agents before proceeding.",
        "",
        f"**Then:** Dispatch the `review-reconciliator` agent to deduplicate, verify, and produce "
        f"consolidated findings.",
        "",
        "The reconciliator needs:",
        f"- **Git range:** `{git_range}`",
        f"- **Changed files:** `{changed_files_csv}`",
        f"- **Output directory:** `{od}`",
        f"- **Dispatch plan:** `{od}/dispatch-plan.json`",
    ]

    if change_purpose:
        actions.append(f"- **Change purpose:** {change_purpose}")
    else:
        actions.append("- **Change purpose:** Derive from commit messages if change-purpose.md is missing")

    review_files = agents_state.get("review_files", [])
    if review_files:
        actions.append("")
        actions.append("**Completed review files:**")
        for rf in review_files:
            actions.append(f"- `{rf}`")
    else:
        actions.append("")
        actions.append("**Completed review files:** Check output directory for `*-review.json` files.")

    actions.append("")
    actions.append("The reconciliator will produce:")
    actions.append(f"- `{od}/review-findings.json` — structured findings")
    actions.append(f"- `{od}/review-findings.md` — human-readable findings")

    return {
        "phase": "SYNTHESIS",
        "title": "Reconcile + Verify",
        "situation": situation,
        "actions": actions,
        "handoff": None,
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
        situation.append(f"**Change purpose:** {change_purpose}")
    elif commit_messages:
        situation.append(f"**Change purpose (from commits):** {'; '.join(commit_messages[:3])}")

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
    actions.append(f"Write the review report to `{od}/review-report.md`.")
    actions.append("")
    actions.append("The report should include:")
    actions.append("- A summary of the review findings")
    actions.append("- Critical and important issues highlighted")
    actions.append("- A clear verdict recommendation (APPROVE, REQUEST_CHANGES, or COMMENT)")
    actions.append("")
    actions.append(f"Reference `{od}/review-findings.json` and `{od}/review-findings.md` "
                   "for the consolidated findings from the reconciliator.")

    return {
        "phase": "SYNTHESIS",
        "title": "Review Report Synthesis",
        "situation": situation,
        "actions": actions,
        "handoff": None,
    }


# ---------------------------------------------------------------------------
# Step 10: Decision Critic
# ---------------------------------------------------------------------------

def _step_10_decision_critic(mode, state, context, config, output_dir):
    """Step 10: Decision Critic — stress-test conclusions."""
    od = output_dir or "<OUTPUT_DIR>"
    degradation = state.get("degradation", {})

    situation = []
    actions = []

    # Determine which file the critic should review
    if degradation.get("report_synthesis_failed"):
        critic_target = f"{od}/review-findings.md"
        situation.append("⚠️ Review report synthesis failed — critic will review review-findings.md instead.")
    else:
        critic_target = f"{od}/review-report.md"

    actions.append(
        f"Dispatch the `decision-reviewer` agent to stress-test the review conclusions."
    )
    actions.append("")
    actions.append(f"The critic should review: `{critic_target}`")
    actions.append("")
    actions.append("**IMPORTANT:** Wait for the critic to finish — do NOT run in background.")
    actions.append("You need the critic's verdict before proceeding to the next step.")
    actions.append("")
    actions.append("The critic will return one of three verdicts:")
    actions.append("- **STAND** — Conclusions are sound. No changes needed.")
    actions.append("- **REVISE** — Apply recommended adjustments (spot-check factual claims first).")
    actions.append("- **ESCALATE** — Flag validity concerns, override verdict to COMMENT.")
    actions.append("")
    actions.append("After acting on the critic's verdict, write the final verdict:")
    actions.append(f"```json")
    actions.append(f'// Write to {od}/review-verdict.json')
    actions.append(f'{{"verdict": "REQUEST_CHANGES"}}')
    actions.append(f"```")
    actions.append(f"Valid values: APPROVE, REQUEST_CHANGES, COMMENT")

    return {
        "phase": "VALIDATION",
        "title": "Decision Critic",
        "situation": situation,
        "actions": actions,
        "handoff": None,
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

    situation = []
    actions = []

    if is_interactive:
        actions.append(f"Present to the user: read `{od}/review-report.md` and present "
                       "a formatted summary of the review findings.")
        actions.append("")
        actions.append(f"Show the verdict and any key findings.")

        if critic_verdict == "unavailable" or degradation.get("critic_failed"):
            actions.append("")
            actions.append("⚠️ Decision critic verdict is unavailable — present the review as-is.")

        if forced_verdict:
            actions.append("")
            actions.append(f"⚠️ Verdict forced to **{forced_verdict}** due to pipeline degradation.")
            if degradation.get("reconciliation_failed") or degradation.get("report_synthesis_failed"):
                actions.append("Both reconciliation and report synthesis failed — presenting degraded results.")

        if mode == "incremental":
            actions.append("")
            actions.append("Note: Review baseline saved. Next `/code-review` will only review new commits.")

        actions.append("")
        actions.append("If you want to drill down on a specific topic, re-invoke the reconciliator "
                       "in focused mode for a deeper analysis of specific findings.")

    else:
        # Non-interactive: list output files
        actions.append("PIPELINE COMPLETE. Output files:")
        actions.append(f"- `{od}/review-report.md` — review report")
        actions.append(f"- `{od}/review-findings.json` — structured findings")
        actions.append(f"- `{od}/review-findings.md` — human-readable findings")
        actions.append(f"- `{od}/pipeline-result.json` — structured result for callers")
        actions.append("")
        actions.append("`pipeline-result.json` contains:")
        actions.append("- `status` — pipeline completion status (success/degraded/failed)")
        actions.append("- `verdict` — final review verdict (APPROVE/REQUEST_CHANGES/COMMENT)")
        actions.append("- `report_path` — path to the review report")
        actions.append("- `findings_path` — path to the structured findings")
        actions.append("- `critic_verdict` — decision critic verdict (STAND/REVISE/ESCALATE/unavailable)")
        actions.append("- `degradation_notes` — list of degradation reasons (empty if clean)")

        if mode == "incremental":
            actions.append("")
            actions.append("Review baseline saved. Next run will only review new commits.")

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
        situation.append(f"Workspace was modified: original branch was `{original_branch}`.")
        if stash_ref:
            situation.append(f"Changes were stashed (ref: {stash_ref}).")

        actions.append(f"Ask the user if they want to restore the workspace:")
        actions.append(f"- Checkout original branch: `git checkout {original_branch}`")
        if stash_ref:
            actions.append(f"- Restore stashed changes: `git stash pop`")
        actions.append("")
        actions.append("Confirm with the user before making changes.")
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
    if next_step:
        lines.append(f"{'─' * 60}")
        ns = next_step
        lines.append(f"➡️  Next: Step {ns['step']} — {ns['title']}")
        if ns.get("skip_reason"):
            lines.append(f"    ({ns['skip_reason']})")
        lines.append("")
        lines.append(f"Run: python3 {SCRIPTS_DIR / 'review-pipeline.py'} --step {ns['step']} --output-dir <OUTPUT_DIR>")
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
        telemetry_path = SCRIPTS_DIR / "review-telemetry.py"
        spec = importlib.util.spec_from_file_location("review_telemetry", telemetry_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Check for env override
        env_log_dir = os.environ.get("PIRATEGOAT_TELEMETRY_LOG_DIR")
        return mod.ReviewTelemetry(output_dir, log_dir=env_log_dir or log_dir)
    except Exception:
        return None


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

    if step == 3:
        # Run gather-review-context.py to collect git context, PR metadata, etc.
        gather_cmd = [sys.executable, str(SCRIPTS_DIR / "gather-review-context.py"),
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

        stdout, ok = _run_subprocess(gather_cmd, timeout=120)
        # Re-read context (gather-review-context.py writes review-context.json)
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
        # Run plan-review-dispatch.py to determine which agents to dispatch
        git = context.get("git", {})
        git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")
        if git_range:
            planner_cmd = [
                sys.executable, str(SCRIPTS_DIR / "plan-review-dispatch.py"),
                "--mode", mode,
                "--git-range", git_range,
                "--output-dir", output_dir,
            ]
            changed_csv = git.get("changed_files_csv", "")
            if changed_csv:
                planner_cmd.extend(["--changed-files-list", changed_csv])

            stdout, ok = _run_subprocess(planner_cmd, timeout=60)
            state["dispatch_plan_output"] = stdout if ok else ""

            plan_path = os.path.join(output_dir, "dispatch-plan.json")
            if os.path.isfile(plan_path):
                try:
                    with open(plan_path) as f:
                        plan = json.load(f)
                    agents = plan.get("agents", [])
                    state["dispatch_plan_summary"] = {
                        "dispatched": sum(1 for a in agents if a.get("status") == "DISPATCH"),
                        "skipped": sum(1 for a in agents if a.get("status", "").startswith("SKIPPED")),
                        "conditional": sum(1 for a in agents if a.get("status") == "DISPATCH" and "conditional" in a.get("reason", "").lower()),
                    }
                except (json.JSONDecodeError, OSError):
                    state["dispatch_plan_summary"] = {}
        else:
            state["dispatch_plan_output"] = ""
            state["dispatch_plan_summary"] = {}

    if step == 6:
        plan_path = os.path.join(output_dir, "dispatch-plan.json")
        if os.path.isfile(plan_path):
            try:
                with open(plan_path) as f:
                    plan = json.load(f)
                dispatched = [
                    {"name": a["name"], "domain": a.get("domain", "")}
                    for a in plan.get("agents", [])
                    if a.get("status") in ("DISPATCH", "DISPATCH_OVERRIDE")
                ]
                state["dispatched_agents"] = dispatched
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
                with open(plan_path) as f:
                    plan = json.load(f)
                dispatched_names = [
                    a["name"] for a in plan.get("agents", [])
                    if a.get("status") in ("DISPATCH", "DISPATCH_OVERRIDE")
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

    if step == 11:
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
    parser.add_argument("--interactive", type=lambda x: x.lower() in ("true", "1", "yes"),
                        default=None, help="Interactive mode (default: true)")
    parser.add_argument("--output-instructions", help="Custom output instructions")
    parser.add_argument("--git-range", help="Explicit git range")
    parser.add_argument("--original-branch", help="Branch to restore on cleanup")
    parser.add_argument("--stash-ref", help="Stash ref to restore on cleanup")

    args = parser.parse_args()
    output_dir = args.output_dir
    step = args.step

    # Ensure output dir exists
    os.makedirs(output_dir, exist_ok=True)

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
            write_config(output_dir, config)
        else:
            config = existing_config

        # Note: review-context.json is NOT cleared here. For interactive runs,
        # gather-review-context.py overwrites it at step 3. For non-interactive
        # (bot) runs, the bot pre-writes it — deleting would break that flow.
        # The output.directory field is needed by gather-review-context.py to
        # locate .branch-review-baseline.json for incremental reviews.

        # Initialize fresh pipeline state
        state = json.loads(json.dumps(_DEFAULT_STATE))
        now = datetime.now(timezone.utc)
        identifier = config.get("pr_number", "branch")
        state["run_id"] = f"{now.strftime('%Y%m%dT%H%M%S')}-{mode}-{identifier}"

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
                telemetry.start(pr_number=pr_number, total_steps=12,
                                bot_mode=bot_mode)
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

        # Telemetry: log step
        telemetry = _init_telemetry(output_dir)
        if telemetry:
            try:
                step_def = _STEP_MAP.get(step, {})
                bot_mode = not config.get("interactive", True)
                telemetry.log_step(
                    step=step, phase=step_def.get("phase", ""),
                    title=step_def.get("title", ""),
                    bot_mode=bot_mode,
                )
            except Exception:
                pass

    # Validate step number
    if step not in _STEP_MAP:
        print(f"ERROR: Invalid step {step}. Valid steps: 1-12", file=sys.stderr)
        sys.exit(1)

    # --- Read review context if available ---
    context_path = os.path.join(output_dir, "review-context.json")
    context = {}
    if os.path.isfile(context_path):
        try:
            with open(context_path) as f:
                context = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # --- Step-specific orchestration ---
    context = _orchestrate_step(step, mode, config, state, context, output_dir)

    # --- Update state ---
    if step not in state.get("completed_steps", []):
        state.setdefault("completed_steps", []).append(step)
    write_state(output_dir, state)

    # --- Compute routing AFTER orchestration (state may have changed) ---
    active = get_active_steps(mode, config, state, context)

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

    # Add next step info
    next_info = compute_next_step(step, active)
    guidance["next_step"] = next_info
    if next_info:
        guidance["skip_reason"] = next_info.get("skip_reason")
    else:
        guidance["skip_reason"] = None

    # Telemetry: finalize at last active step
    if next_info is None and telemetry:
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

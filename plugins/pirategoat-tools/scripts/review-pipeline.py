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
    else:
        # Placeholder for steps 4-12 — implemented in subsequent tasks
        return {
            "phase": step_def["phase"],
            "title": step_def["title"],
            "situation": [f"Step {step}: {step_def['title']} ({mode} mode)"],
            "actions": [f"Execute step {step} actions."],
            "handoff": None,
        }


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

    # --- Get active steps and compute next ---
    active = get_active_steps(mode, config, state, context)

    # Check for hard error: non-interactive PR without pre-computed context
    if mode == "pr" and not config.get("interactive", True):
        git = context.get("git", {})
        if not git.get("merge_base") and step <= 2:
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

    # --- Update state ---
    if step not in state.get("completed_steps", []):
        state.setdefault("completed_steps", []).append(step)
    write_state(output_dir, state)

    # --- Format and output ---
    output = format_output(step, guidance)
    print(output)


if __name__ == "__main__":
    main()

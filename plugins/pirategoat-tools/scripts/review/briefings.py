"""Pure curated-context briefings for review pipeline steps."""

import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from .pipeline_contract import (
        DEFAULT_AGENT_TIMEOUT,
        HOST_CODEX,
        REVIEW_RECORD_MD,
        SCRIPTS_DIR,
        _STEP_MAP,
        _codex_agent_instruction,
        _codex_task_name,
        _host,
        _stop_operation,
    )
    from .dispatch_status import (
        DISPATCHED_STATUSES,
        SKIPPED_QUICK_MODE,
        SKIPPED_STATUSES,
    )
except ImportError:
    _scripts_parent = str(Path(__file__).resolve().parent.parent)
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.pipeline_contract import (
        DEFAULT_AGENT_TIMEOUT,
        HOST_CODEX,
        REVIEW_RECORD_MD,
        SCRIPTS_DIR,
        _STEP_MAP,
        _codex_agent_instruction,
        _codex_task_name,
        _host,
        _stop_operation,
    )
    from review.dispatch_status import (
        DISPATCHED_STATUSES,
        SKIPPED_QUICK_MODE,
        SKIPPED_STATUSES,
    )

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
        "The record is assembled. The decision critic will challenge your "
        "conclusions before anything is written for a human to read. Act on "
        "the critic's verdict: REVISE means revise. Persist all verdicts to "
        "their files precisely. Deliver a review the author can trust."
    ),
    "OUTPUT": (
        "Review is validated. Now write the report — once, from the settled "
        "record — then present clearly, confirm all artifacts are written, "
        "and verify the pipeline result is complete. This is what the "
        "author or calling system receives."
    ),
}

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
        return _step_9_review_record(mode, state, context, config, output_dir)
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

def _markdown_code_span(value):
    """Render an untrusted scalar as a single-line Markdown code span."""
    text = "".join(
        char if char.isprintable() else " "
        for char in str(value)
    )
    text = re.sub(r"\s+", " ", text).strip()
    longest_backtick_run = max(
        (len(match.group(0)) for match in re.finditer(r"`+", text)),
        default=0,
    )
    delimiter = "`" * max(1, longest_backtick_run + 1)
    padding = " " if not text or text.startswith("`") or text.endswith("`") else ""
    return f"{delimiter}{padding}{text}{padding}{delimiter}"


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


def _dependency_refresh_briefing(state, config, output_dir):
    """Situation/actions/handoff lines for trusted-branch dependency refresh.

    Opt-in establishes execution trust; the tracked precheck establishes the
    separate worktree-custody boundary. Only a measured clean baseline offers
    adaptive execution and the validating save handoff.
    """
    if not (config or {}).get("refresh_dependencies"):
        return [], [], []
    od = output_dir or "<OUTPUT_DIR>"
    precheck = state.get("dependency_refresh_precheck") or {}
    tracked_files_dirty = precheck.get("tracked_files_dirty")

    if tracked_files_dirty is True:
        return (
            ["**Dependency refresh:** enabled, but the pipeline will not run "
             "dependency commands against an unsafe tracked baseline. The "
             "tracked worktree is dirty; preserve the requester's work and "
             "continue with the existing host context.", ""],
            [],
            [],
        )
    if tracked_files_dirty is not False:
        return (
            ["**Dependency refresh:** enabled, but the pipeline will not run "
             "dependency commands while the tracked baseline is unknown. "
             "Continue with the existing host context.", ""],
            [],
            [],
        )

    situation = [
        "**Dependency refresh (trusted-branch mode):** the requester "
        "authorized adaptive dependency refresh in this worktree, and the "
        "tracked baseline is clean.",
    ]
    situation.append("")

    actions = [
        "1. Inspect the repository and reviewed change, then decide whether "
        "dependency installation is needed. Do not infer work from a fixed "
        "manager list.",
        "2. When refresh work is needed, run the appropriate "
        "lockfile-preserving commands adaptively in the relevant directories "
        "within the requester's effective opt-in. Record every attempted "
        "command and whether it exited successfully.",
        "3. After any install attempt, re-resolve host context so reviewers "
        "see the resulting state: "
        f"`python3 {SCRIPTS_DIR / 'context.py'} --output-dir {od} "
        "--refresh-host-context`",
        "4. Prepare the exact schema-1 request at "
        "`$TMPDIR/dependency-refresh-report.json`. When inspection finds no "
        "refresh work, report `not_needed` with an empty command list.",
        "",
    ]

    handoff = [
        "Prepare one of these request shapes under `$TMPDIR`:",
        "```json",
        '{"schema": 1, "status": "not_needed", "commands": []}',
        "```",
        "or, when commands were attempted:",
        "```json",
        '{"schema": 1, "status": "<completed | partial | failed>",',
        ' "commands": [{"directory": "<dir>", "command": "<command run>", '
        '"exit_status": "<ok | failed>"}]}',
        "```",
        "Publish it only through the validating save channel:",
        f"`python3 {SCRIPTS_DIR / 'dependency_refresh.py'} save "
        f"--output-dir {od} --report "
        '"$TMPDIR/dependency-refresh-report.json"`',
        "Proceed only when the command prints literal `SAVED "
        "dependency-refresh.json` and the canonical file exists in the "
        "output directory.",
    ]
    return situation, actions, handoff


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

    # Trusted-branch dependency refresh — renders only when the requester
    # opted in (run-config refresh_dependencies).
    refresh_situation, refresh_actions, refresh_handoff = \
        _dependency_refresh_briefing(state, config, output_dir)
    situation.extend(refresh_situation)

    # Actions — refresh first, so host context is fresh before the
    # change-purpose summary is written.
    actions.extend(refresh_actions)
    actions.append("Review the context above and write the change-purpose summary.")

    # Handoff — the refresh report gates step 3 whenever a refresh was
    # instructed; change-purpose joins it unless unfetched issues push the
    # summary to step 4.
    has_unfetched = state.get("resolved_params", {}).get("has_unfetched_issues", False)
    handoff_lines = list(refresh_handoff)
    if not has_unfetched:
        if handoff_lines:
            handoff_lines.append("")
        handoff_lines.extend(_change_purpose_handoff(output_dir))
    handoff = handoff_lines or None

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

    situation = []
    actions = []

    if (config or {}).get("refresh_dependencies"):
        report = state.get("dependency_refresh_report")
        if not isinstance(report, dict):
            situation.extend([
                "⚠️  The requested dependency-refresh report is missing or "
                "malformed. Reviewer dispatch may proceed, but dependency "
                "freshness is not recorded.",
                "",
            ])
        elif report.get("tracked_files_dirty") is True:
            situation.append(
                "⚠️  The dependency-refresh report records a dirty final "
                "tracked state before reviewer dispatch."
            )
            for path in report.get("dirty_files", []):
                if isinstance(path, str):
                    situation.append(f"- `{path}`")
            situation.append("")
        elif report.get("tracked_files_dirty") is not False:
            situation.extend([
                "⚠️  The dependency-refresh report records an unknown final "
                "tracked state before reviewer dispatch.",
                "",
            ])

    situation.extend([_PHASE_TRANSITIONS["EXECUTION"], ""])

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
                    # The tier actually dispatched for this instance (the
                    # model hint below) — telemetry must record it, not the
                    # adapter registry's static tier, or the manifest holds
                    # conflicting models for one agent. On the Codex host no
                    # Claude model override is applied (the native subagent
                    # runs the Codex model), so forwarding the declaration
                    # would attribute the execution to a tier that never ran;
                    # empty falls back to the adapter's registry "inherit".
                    "--model-tier", "" if codex_host else (agent.get("model") or ""),
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
                        f"`{_codex_task_name(name)}` and no Claude model override. "
                        "The task name is this reviewer instance's identity; the "
                        "shared adapter definition remains below, and the bootstrap "
                        "`--instance-name` argument carries the same identity."
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

def _draft_finalization_guidance():
    return [
        "A saved review draft remains RUNNING; only final "
        "`<reviewer>-review.json` that passes canonical validation is FINISHED; "
        "an invalid final filename is terminal process evidence only.",
        "After a host subagent-completion notification, run agents_status. "
        "If that returned agent's status block contains a `DRAFT` line, "
        "run the exact command printed on its `FINALIZE_REVIEW_COMMAND` line, then "
        "run agents_status again.",
        "Polling or draft presence without a host completion notification "
        "never authorizes parent-side finalization.",
        "A TIMED_OUT unfinalized draft remains timed out and is discarded "
        "when review intake closes.",
        "",
    ]

def _step_7_save_baseline(mode, state, context, config, output_dir):
    """Step 7: Save Review Baseline — script writes file internally."""
    git = context.get("git", {})
    git_range = state.get("resolved_params", {}).get("git_range") or git.get("git_range", "")
    od = output_dir or "<OUTPUT_DIR>"

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
    actions.append("**Wait for agents before step 8.**")
    actions.append("")
    actions.extend(_draft_finalization_guidance())

    if _host(config) == HOST_CODEX:
        actions.extend([
            "Poll for completion once a minute — the wait lives inside the "
            "script, so each call blocks for up to 60 seconds before "
            "returning control to you:",
            "```",
            f"python3 {SCRIPTS_DIR}/agents_status.py --output-dir \"{od}\" --wait --max-seconds 60",
            "```",
            "- Exit code 0 (ALL_DONE): proceed to step 8",
            "- Exit code 3 (60s elapsed, still running): print one progress "
            "line and re-run the same call",
            "- NOT_DISPATCHED agents: dispatch them first, then re-check",
            "",
            "This terminates on its own: a RUNNING agent flips to TIMED_OUT "
            f"at the configured agent timeout (default {DEFAULT_AGENT_TIMEOUT}s "
            "/ 20 min), and a timed-out agent no longer blocks ALL_DONE, so "
            "exit 0 arrives within about one more polling cycle even in the "
            "worst case. If it doesn't, proceed to step 8 anyway — its own "
            "escalation gate force-proceeds.",
            "",
            "Expect roughly 10 calls for a typical run.",
        ])
    else:
        actions.extend([
            "Sequence matters here — do these two things IN ORDER, not in "
            "parallel:",
            "",
            "1. Immediately after dispatching, launch ONE watchdog in the "
            "BACKGROUND (a Bash call with `run_in_background: true`) — a "
            "guaranteed wake-up past the 1200s agent timeout even if every "
            "per-agent notification is missed. It holds no model turn open "
            "while it waits:",
            "```",
            f"python3 {SCRIPTS_DIR}/agents_status.py --output-dir \"{od}\" --wait --max-seconds 1500",
            "```",
            "2. THEN END YOUR TURN. Notifications are primary from here — "
            "each subagent's completion notification is your wake-up "
            "signal; do not do anything else while waiting.",
            "",
            "On wake-up, run agents_status once:",
            "```",
            f"python3 {SCRIPTS_DIR}/agents_status.py --output-dir \"{od}\"",
            "```",
            "- Exit code 0 (ALL_DONE): proceed to step 8",
            "- Exit code 2 (still running): end your turn again and wait for "
            "the next wake-up",
            "- NOT_DISPATCHED agents: dispatch them first, then re-check",
            "",
            "Do not do any of these while waiting:",
            "- No foreground `sleep` — the harness blocks it",
            "- No keepalive loops — empty turns just to \"stay alive\"",
            "- No polling without a new wake-up — re-checking status without "
            "a fresh notification or watchdog expiry wastes turns",
        ])

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

            # Remaining budget before the escalation above force-proceeds —
            # a fresh watchdog here should not outlive that backstop.
            remaining_budget = max(1, int(escalation_threshold - elapsed))

            if _host(config) == HOST_CODEX:
                actions = _draft_finalization_guidance() + [
                    "Poll for completion once a minute — the wait lives "
                    "inside the script, so each call blocks for up to 60 "
                    "seconds before returning control to you:",
                    "```",
                    f"python3 {SCRIPTS_DIR}/agents_status.py --output-dir \"{od}\" --wait --max-seconds 60",
                    "```",
                    "- Exit code 0 (ALL_DONE): re-run step 8",
                    "- Exit code 3 (60s elapsed, still running): print one "
                    "progress line and re-run the same call",
                    "This terminates on its own within the "
                    f"{agent_timeout}s agent timeout; if it doesn't, the "
                    f"escalation above force-proceeds {escalation_threshold}s "
                    "after waiting began.",
                ]
            else:
                actions = _draft_finalization_guidance() + [
                    "Sequence matters here — do these two things IN ORDER, "
                    "not in parallel:",
                    "",
                    "1. If the step-7 watchdog may already have expired (or "
                    "was never launched), launch a fresh one now with the "
                    "remaining budget before the escalation above "
                    "force-proceeds. Run it via a BACKGROUND Bash call "
                    "(`run_in_background: true`) — it holds no model turn "
                    "open, and in this state it may be the only remaining "
                    "wake-up:",
                    "```",
                    f"python3 {SCRIPTS_DIR}/agents_status.py --output-dir \"{od}\" --wait --max-seconds {remaining_budget}",
                    "```",
                    "2. THEN END YOUR TURN. Notifications are primary from "
                    "here — wait for the next subagent completion "
                    "notification (or the watchdog's exit); do not poll in "
                    "a loop.",
                    "",
                    "On wake-up, run agents_status once:",
                    "```",
                    f"python3 {SCRIPTS_DIR}/agents_status.py --output-dir \"{od}\"",
                    "```",
                    "- Exit code 0 (ALL_DONE): re-run step 8",
                    "- Exit code 2 (still running): end your turn again and "
                    "wait for the next wake-up",
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
    discarded_drafts = agents_state.get("discarded_drafts", [])
    change_purpose = state.get("change_purpose")
    commit_messages = state.get("commit_messages", [])

    situation = [
        _PHASE_TRANSITIONS["SYNTHESIS"],
        "",
        f"**Agents dispatched:** {', '.join(dispatched) if dispatched else 'see dispatch plan'}",
        f"**Agents completed:** {', '.join(completed) if completed else 'check status'}",
    ]
    if discarded_drafts:
        situation.append(
            "**Discarded reviewer drafts:** "
            + ", ".join(discarded_drafts)
        )

    intake = state.get("review_intake", {})
    if intake.get("status") == "closed":
        discarded = intake.get("discarded_drafts", [])
        situation.append(
            "**Review intake:** closed before synthesis; discarded "
            + (", ".join(discarded) if discarded else "no drafts")
            + "."
        )

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
        f"- **Reconciliation context:** `{od}/reconciliation-context.json` (pre-gathered: all agent findings, source snippets, scope annotations)",
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
    actions.append(
        f"**Expected output:** `{od}/review-findings.json` — the "
        "reconciliator's only artifact. The pipeline renders "
        f"`{od}/review-findings.md` from it; the agent writes no Markdown."
    )

    additional = config.get("additional_instructions") if config else None
    if additional:
        actions.append("")
        actions.append("**Reviewer-Requested Focus:**")
        actions.append(f"> {additional}")
        actions.append("Give additional weight to findings addressing this guidance.")

    handoff = [
        f"Verify `{od}/review-findings.json` exists before proceeding.",
    ]

    return {
        "phase": "SYNTHESIS",
        "title": "Reconcile + Verify",
        "situation": situation,
        "actions": actions,
        "handoff": handoff,
    }


# ---------------------------------------------------------------------------
# Review coverage rendering (shared by the record assembler and step 11)
# ---------------------------------------------------------------------------

def _run_wide_review_gaps(file_review):
    """Return unclaimed files that no reviewer received inline or claimed.

    `agents_with_unclaimed_review_by_file` is deliberately per-agent: one
    reviewer can leave a file unclaimed while another received it inline or
    claimed it from their queue. Only the set difference is a run-wide gap.
    """
    if not isinstance(file_review, dict):
        return {}
    unclaimed = file_review.get("agents_with_unclaimed_review_by_file")
    if not isinstance(unclaimed, dict):
        return {}
    covered_elsewhere = set()
    for field in (
        "agents_receiving_inline_diff_by_file",
        "agents_claiming_review_by_file",
    ):
        population = file_review.get(field)
        if isinstance(population, dict):
            covered_elsewhere.update(population)
    return {
        path: agents
        for path, agents in unclaimed.items()
        if path not in covered_elsewhere
    }


def _has_file_review_gap(file_review):
    """True when something is PROVEN uncovered, claims aside.

    Files starved for every reviewer and domain-unmatched files are gaps.
    Inline receipt or a reviewed-file claim makes a file accounted for at
    run level; the latter remains a claim rather than proof of read. Demanding
    the verdict acknowledge "this gap" on a claims-only run converts that
    hedge into the certainty it was written to avoid.
    """
    if not isinstance(file_review, dict):
        return False
    return bool(
        _run_wide_review_gaps(file_review) or
        file_review.get("unscoped_files")
    )


def _render_file_review_section(file_review):
    """Render the report's complete `## Review coverage` section, or "".

    One paste, three populations, each with its own honest sentence:

    * **gaps** — matched a reviewer domain, received no inline diff, and
      earned no reviewed-file claim from any matching reviewer.
    * **unscoped** — matched no reviewer domain at all, so no agent's
      scope ever contained them.
    * **claims** — never diffed inline, but a reviewer says it
      reviewed them anyway. A claim, never proof of read.

    They are never merged: "no one saw it" and "someone says they saw it"
    are different facts, and so are "starved by a budget" and "routed to
    nobody". Returning finished Markdown rather than a description is the
    whole point — the orchestrator's job here is to paste, not to
    summarize.
    """
    if not isinstance(file_review, dict):
        return ""
    gaps = _run_wide_review_gaps(file_review)
    claims = file_review.get("agents_claiming_review_by_file")
    unscoped = file_review.get("unscoped_files")
    gaps = gaps if isinstance(gaps, dict) else {}
    claims = claims if isinstance(claims, dict) else {}
    unscoped = unscoped if isinstance(unscoped, list) else []
    if not (gaps or claims or unscoped):
        return ""

    lines = ["## Review coverage", ""]
    if gaps:
        lines.append(
            f"{len(gaps)} changed file(s) were skipped by every matching "
            "agent's diff budget and no reviewer reported reviewing them "
            "from the review-claimable queue:"
        )
        lines.append("")
        for f_path, agents in sorted(gaps.items()):
            agents_list = agents if isinstance(agents, list) else [agents]
            skipped_by = ", ".join(
                _markdown_code_span(agent) for agent in agents_list
            )
            lines.append(
                f"- {_markdown_code_span(f_path)} (skipped by: {skipped_by})"
            )
        lines.append("")
    if unscoped:
        lines.append(
            f"{len(unscoped)} changed file(s) matched no reviewer's domain "
            "and were reviewed by no one — no agent's scope contained them "
            "in any form (this counts every changed file, including "
            "binaries and non-reviewable paths — run-level metrics count "
            "reviewable files only, so its 'uncovered' figure can be "
            "smaller):"
        )
        lines.append("")
        for f_path in sorted(unscoped):
            lines.append(f"- {_markdown_code_span(f_path)}")
        lines.append("")
    if claims:
        lines.append("### Reviewed-file claims — claims, not proof of read")
        lines.append("")
        lines.append(
            f"{len(claims)} changed file(s) never received their diff "
            "inline, but an agent claims to have reviewed them from the "
            "review-claimable queue. These claims are not proof of read:"
        )
        lines.append("")
        for f_path, agents in sorted(claims.items()):
            agents_list = agents if isinstance(agents, list) else [agents]
            claimed_by = ", ".join(
                _markdown_code_span(agent) for agent in agents_list
            )
            lines.append(
                f"- {_markdown_code_span(f_path)} (claimed by: {claimed_by})"
            )
        lines.append("")
    return "\n".join(lines).rstrip("\n")


# ---------------------------------------------------------------------------
# Step 9: Review Record
# ---------------------------------------------------------------------------

def _step_9_review_record(mode, state, context, config, output_dir):
    """Step 9: Review Record — read what the pipeline assembled.

    This step used to have the orchestrator author `review-report.md` from
    the ledger, which meant the audience-facing document was born BEFORE
    the decision critic ran and had to be edited back into agreement with
    a ledger that moved underneath it. Authoring moved wholesale to step
    11, after validation. What is left here is a reading step: the
    pipeline has already assembled `review-record.md` — the complete,
    machine-written account of the run — and the orchestrator's job is to
    read it and satisfy itself that it presents a review worth standing
    behind before the critic starts pulling on it.
    """
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
        # The sanctioned LLM-authored fallback. With no ledger there is no
        # record to assemble, so there is nothing for this step to hand
        # over; the raw agent output is the only material, and step 11
        # asks for the manual synthesis.
        situation.append(
            "⚠️ Reconciliation failed — there is no findings ledger, so the "
            "pipeline assembled no review record. This run is in degraded "
            "mode and works from raw agent output."
        )
        actions.append(
            f"Read the individual `{od}/<agent>-review.md` files directly "
            "(raw agent output) and build your own picture of the change."
        )
        actions.append(
            "You will synthesize `review-report.md` manually at step 11, "
            "from that raw output. Do not write it now — the decision "
            "critic runs first."
        )
        return {
            "phase": "SYNTHESIS",
            "title": "Review Record",
            "situation": situation,
            "actions": actions,
            "handoff": None,
        }

    # Three states, never two — the same discipline step 10's
    # `critic_source` follows a hundred lines below. A run that recorded
    # NO assembly outcome (older state, a briefing fetched on its own) has
    # not been measured, and saying "the record is assembled at <path>" for
    # it states a fact nothing established. An unmeasured absence and a
    # measured success are different claims.
    record_outcome = state.get("review_record")
    if not isinstance(record_outcome, dict):
        actions.append(
            f"**Read `{od}/{REVIEW_RECORD_MD}` if it is there.** This run "
            "recorded no assembly outcome, so whether the pipeline wrote "
            "the record is unknown — look, rather than assume either way. "
            f"If it is absent, read `{od}/review-findings.json` directly; "
            "it is the canonical ledger the record projects."
        )
    elif record_outcome.get("status") != "complete":
        situation.append(
            f"⚠️ The pipeline could not assemble `{od}/{REVIEW_RECORD_MD}`. "
            f"Read `{od}/review-findings.json` directly instead — it is the "
            "canonical ledger the record would have projected."
        )
    else:
        actions.append(
            f"**The review record is assembled at `{od}/{REVIEW_RECORD_MD}`.** "
            "The pipeline wrote it from the findings ledger and this run's "
            "own measurements — findings, verified checks, the reconciler's "
            "assessment, run notes, and the coverage measurement. Nothing "
            "in it was authored by an agent."
        )
        actions.append("")
        actions.append(
            "Read it end to end and verify it presents the review you would "
            "stand behind: the findings are the ones you expect, the "
            "coverage section matches what the run actually reached, and "
            "nothing in it surprises you. Then proceed."
        )
        actions.append("")
        actions.append(
            "**Do not edit it, and do not write a report yet.** The record "
            "is the pipeline's own account and stays machine-written; the "
            "audience-facing `review-report.md` is authored from a "
            "source-bound settlement at step 11, after the decision critic "
            "has run and any adjustments have landed. Writing prose now "
            "would only be prose that has to be corrected later."
        )

    actions.append("")
    actions.append(
        "**Empirical verification rules:** never create or modify tracked "
        "files in the reviewed repo. If spot-checking a claim requires a "
        "new file, put `pirategoat-probe` in its filename (not just a "
        "directory name), keep it in a non-ignored path, and create+run+"
        "delete it in a single command. Never use `git reset`/"
        "`git checkout --`/`git clean` as cleanup — the tree may hold the "
        "user's uncommitted work."
    )

    return {
        "phase": "SYNTHESIS",
        "title": "Review Record",
        "situation": situation,
        "actions": actions,
        # No gate: this step asks the orchestrator for no artifact. The
        # record is the pipeline's own write, and gating on a file the
        # pipeline just wrote would be theatre — the same reason step 10's
        # quick-skip branch carries no handoff.
        "handoff": None,
    }


# ---------------------------------------------------------------------------
# Step 10: Decision Critic
# ---------------------------------------------------------------------------

# Distinguishes "step 10's orchestration never ran" from the measured
# absence it records as `None`. A briefing that cannot tell them apart
# reports a filesystem it never looked at.
_NO_RECORDED_SOURCE = object()


def _step_10_decision_critic(mode, state, context, config, output_dir):
    """Step 10: Decision Critic — stress-test conclusions."""
    od = output_dir or "<OUTPUT_DIR>"

    # Quick-mode critic skip: low-risk verdicts don't need stress-testing
    is_quick = config.get("quick", False)
    recon_verdict = state.get("reconciliation_verdict", "")
    skip_critic = is_quick and recon_verdict.lower() in ("approve", "comment")

    if skip_critic:
        situation = [
            _PHASE_TRANSITIONS["VALIDATION"],
            f"Quick mode is active and reconciliation verdict is **{recon_verdict}** — "
            "skipping the decision critic. Low-risk verdicts do not need stress-testing.",
        ]
        actions = [
            f"Nothing to do here. The pipeline recorded the skip itself in "
            f"`{od}/decision-critic-verdict.json`, and step 11 derives the "
            f"published verdict from `{od}/review-findings.json`.",
            "",
            "Proceed to the next step.",
        ]
        # No handoff: this branch asks the orchestrator for no artifact, and
        # a gate on a file the pipeline writes would be theatre.
        handoff = None

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

    # Determine which file the critic should review.
    #
    # By EXISTENCE, recorded at step 10's orchestration into
    # `state["critic_source"]`, because this module is pure. The two things
    # this replaced were both proxies for existence and both wrong: a
    # `report_synthesis_failed` degradation key that no writer under
    # scripts/ ever set (so the fallback never fired, however badly step 9
    # had gone), and the findings-render outcome, which reports `complete`
    # for a run that had no ledger to render — pointing the critic at a
    # Markdown file nobody wrote.
    #
    # Order is preference, not availability: the machine-assembled record
    # first, the pipeline-rendered findings Markdown second, and the
    # canonical ledger last — the critic can read JSON, and a raw findings
    # list is a worse read than a rendering but an infinitely better one
    # than a missing file. `review-report.md` is not in the list at all:
    # it is authored at step 11, after this critic runs.
    critic_source = state.get("critic_source", _NO_RECORDED_SOURCE)
    if critic_source is _NO_RECORDED_SOURCE:
        # No recorded facts at all (step 10's orchestration never ran).
        # That is not a measured absence and must not render as one, so
        # the nominal target stands.
        critic_target = f"{od}/{REVIEW_RECORD_MD}"
    elif critic_source is None:
        # Measured absence: step 10 looked and found none of the three.
        critic_target = f"{od}/{REVIEW_RECORD_MD}"
        situation.append(
            f"⚠️ No review artifact was found to stress-test — neither "
            f"{REVIEW_RECORD_MD}, review-findings.md, nor "
            "review-findings.json is present. The critic has nothing to "
            "read; expect its verdict to be unusable."
        )
    else:
        critic_target = f"{od}/{critic_source}"
        if critic_source != REVIEW_RECORD_MD:
            reason = (
                " (the findings Markdown render did not complete)"
                if critic_source == "review-findings.json"
                and degradation.get("findings_markdown_incomplete")
                else ""
            )
            situation.append(
                f"⚠️ `{REVIEW_RECORD_MD}` is missing — critic will "
                f"review `{critic_source}` instead{reason}."
            )

    ledger_status = state.get("ledger_status")
    has_findings = (
        ledger_status == "ok" if ledger_status is not None
        else not degradation.get("reconciliation_failed")
    )
    findings_path = f"{od}/review-findings.json"

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
        # Two paths, both read directly — no context document is built.
        # The record IS the curated read (it renders the ledger through the
        # same renderer, with each finding's `fN` and check's `cN` id), and the
        # ledger itself is the machine-readable anchor the critic keys its
        # adjustments by. A builder that merged them into a third file
        # existed only because the record did not.
        actions.append(
            f"Review record to stress-test (for critic.py --report): "
            f"{critic_target}"
        )
        actions.append(
            f"Structured findings (for critic.py --context): {findings_path}"
        )
    else:
        actions.append(f"Review document to stress-test: {critic_target}")
        actions.append(f"No structured findings available (reconciliation failed) — critique the document directly without --context.")
    actions.append(f"Output directory: {od}")
    actions.append(f"Context: <one-line summary of PR scope, verdict, and finding count>")
    actions.append(
        "Return STAND, REVISE, or ESCALATE. Author findings first at "
        "`$TMPDIR/decision-critic-findings.md`, then publish the findings "
        "and verdict through `critic.py --save` for every verdict. Never "
        "write a canonical `decision-critic-*` artifact directly."
    )
    actions.append(
        "On REVISE, also author every finding or check adjustment in "
        "`$TMPDIR/decision-critic-adjustments.json` and pass it to the "
        "same `critic.py --save` command, "
        "per your agent instructions. "
        "On STAND or ESCALATE, invoke that command without an adjustments "
        "file. A recommendation that exists only as prose cannot reach "
        "the machine-readable ledger, while a raw write bypasses its "
        "source-bound commit."
    )
    actions.append("```")
    actions.append("")
    actions.append("**Wait for the critic to finish — do not run in background.**")
    actions.append("")
    actions.append(
        "Read the critic's verdict — before acting on it, and before any "
        "adjustments. The critic saved it itself, through `critic.py "
        "--save`, the channel that validates the whole artifact set and "
        "writes it atomically:"
    )
    actions.append("```bash")
    actions.append(f'cat "{od}/decision-critic-verdict.json"')
    actions.append("```")
    actions.append(
        "**You write nothing here.** That file is the critic's own "
        "artifact and it already exists; a second, hand-written copy would "
        "be an unvalidated writer of a file three things depend on — the "
        "REVISE gate inside the adjustments applier, step 11's derived "
        "verdict, and the critic's measured duration, which is keyed on "
        "this file's mtime. A mistranscription would overwrite a "
        "channel-validated verdict with a typo."
    )
    actions.append(
        "If the file is absent, the critic produced no verdict. Write "
        "nothing in its place and carry on: a dispatched critic that "
        "produced no verdict is a run that lost its stress test, step 11 "
        "reports it as a degradation, and a stand-in would hide exactly "
        "that."
    )
    actions.append("")
    actions.append("Act on the critic's verdict:")
    actions.append("")
    actions.append(
        "**STAND** — No changes needed. The review stands as reconciled; "
        "proceed to the next step."
    )
    actions.append("")
    actions.append(
        "**REVISE** — the machine-readable ledger updates first, the prose second:"
    )
    actions.append(
        f"1) Read the critic's recommendations, the committed proposal IDs "
        f"in `{od}/decision-critic-adjustments.json`, the source-bound marker "
        f"in `{od}/decision-critic-verdict.json`, the current "
        f"`{od}/review-findings.json` ledger, and the source files needed to "
        f"probe each material claim."
    )
    actions.append(
        "2) Probe the claims with `git grep`/`Read`, then author ONLY "
        "this schema-2 adjudication request under `$TMPDIR` (create the "
        "directory first if needed):"
    )
    actions.append("```json")
    actions.append("{")
    actions.append('  "schema": 2,')
    actions.append('  "verified": ["<script-assigned-adjustment-id>"],')
    actions.append('  "refuted": [')
    actions.append("    {")
    actions.append(
        '      "adjustment_id": "<another-script-assigned-adjustment-id>",'
    )
    actions.append(
        '      "rejection_reason": "<what the source probe disproved>"'
    )
    actions.append("    }")
    actions.append("  ],")
    actions.append(
        '  "revised_assessment": "<optional post-critic assessment>"'
    )
    actions.append("}")
    actions.append("```")
    actions.append(
        "   Account for positive claims PER ENTRY, never in aggregate. Put "
        "only individually confirmed IDs in `\"verified\"` and only "
        "individually disproved IDs in `\"refuted\"`, each refutation with "
        "its non-empty reason. Every committed ID omitted from both lists is "
        "derived as `not_checked`. The orchestrator never edits the committed "
        "proposal. `revised_assessment` is optional: omit it when no "
        "replacement assessment should be installed."
    )
    actions.append(
        "3) Save the request as `$TMPDIR/critic-adjudication.json`, then run "
        "the validating adjudication channel exactly once:"
    )
    actions.append("```bash")
    actions.append(
        f'python3 {SCRIPTS_DIR}/critic_adjustments.py adjudicate '
        f'--output-dir "{od}" < "$TMPDIR/critic-adjudication.json"'
    )
    actions.append("```")
    actions.append(
        "A successful handoff reports `RECORDED ADJUDICATION`, the derived "
        "`VERIFIED | REFUTED | NOT_CHECKED` counts, `REVISED ASSESSMENT: "
        "present|absent`, `APPLIED | REJECTED`, and the `LEDGER VERDICT`. On "
        "any `REJECTED:` line, correct only the temp request and resubmit it; "
        "never edit the output artifact or bypass `adjudicate`."
    )
    actions.append(
        f"The adjudication channel verifies "
        f"`{od}/decision-critic-verdict.json` "
        f"against the committed proposal digest and then records your "
        f"adjudication in `{od}/review-findings.json` in a single write "
        f"through the ledger's sole writer."
    )
    actions.append(
        "Never hand-edit `review-findings.json` either: that one write "
        "carries provenance, invalidates the reconciler's prior assessment "
        "only when an accepted operation really changes the ledger, installs "
        "a supplied revised assessment, recounts findings, and derives the "
        "final ledger verdict. Refuted operations do not invalidate or "
        "replace the assessment."
    )
    actions.append(
        f"4) Nothing else to edit. The pipeline re-assembles "
        f"`{od}/{REVIEW_RECORD_MD}` from the updated ledger at step 11, and "
        f"`review-report.md` is authored there — once, from that settled "
        f"record. There is no report to bring back into agreement with the "
        f"JSON, which is exactly why authoring waits until after you."
    )
    actions.append(
        "   The script-derived per-entry outcome reaches the record on "
        "its own. Never report the batch in aggregate anywhere — \"all N "
        "probed\" over a batch where one entry went unprobed publishes "
        "that entry as verified, which is the exact false claim per-entry "
        "outcome tracking exists to prevent."
    )
    actions.append("")
    actions.append(
        "**ESCALATE** — nothing to do here. The pipeline forces the "
        "published verdict to **COMMENT** at step 11: an ESCALATE means the "
        "review's conclusions did not survive the stress test, so none of "
        "them is strong enough to gate a merge."
    )
    actions.append("")
    actions.append(
        "**You do not write a final review verdict.** Step 11 DERIVES it "
        f"from `{od}/review-findings.json` — the one artifact whose verdict "
        "was actually computed from findings, and which the adjustments "
        "command above recomputes for you. Transcribing a verdict by hand "
        "is how a review holding a critical finding used to publish COMMENT."
    )

    handoff = [
        f"You have READ `{od}/decision-critic-verdict.json` if the critic "
        f"saved one, and written nothing verdict-shaped yourself. If the "
        f"critic produced no verdict, that file is absent and stays "
        f"absent — step 11 reports it.",
        f"On REVISE: `{od}/review-findings.json` carries the applied "
        f"adjustments. Nothing else needs syncing — step 11 re-assembles "
        f"the record from that ledger before the report is written.",
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

# The report's voice. Bot runs override this wholesale through
# `output_instructions` in run-config.json (pirategoat-bot seeds it, and
# the result IS the posted PR comment); interactive runs fall back to the
# mode-appropriate default below. These live at step 11 because that is
# where the report is authored — once, from the final post-critic state.
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



def _derived_markdown_status_line(state, output_dir, *, key, label, suffix=None):
    """Summarize one derived-Markdown outcome for a human.

    Both derived families — the per-reviewer `<reviewer>-review.md` rendered
    at step 8 and `review-findings.md` rendered at steps 9 and 11 — are
    best-effort renders that record the same written/expected/status
    outcome, so they report through one helper rather than two that drift.
    `suffix` is passed to the printed recovery command, which is what makes
    that command rebuild the family the line is actually about.
    """
    outcome = state.get(key)
    if not isinstance(outcome, dict) or outcome.get("ran") is not True:
        detail = "materialization did not run"
    else:
        written = outcome.get("written", 0)
        expected = outcome.get("expected", 0)
        if outcome.get("status") == "complete" and written == expected:
            if written > 0:
                return f"{label}: materialized {written}/{expected} files."
            # A completed render of zero sources is a measured zero, not a
            # gap. Warning about it and offering a recovery command sends
            # the reader after work that cannot exist — there is no source
            # JSON in the directory for the command to render.
            return f"{label}: nothing to render (no source JSON)."
        detail = f"materialization {outcome.get('status', 'incomplete')} ({written}/{expected} files)"

    command = (
        f"python3 {shlex.quote(str(SCRIPTS_DIR / 'review_markdown.py'))} "
        f"materialize {shlex.quote(str(output_dir))}"
    )
    if suffix:
        command += f" --suffix {shlex.quote(suffix)}"
    return f"⚠️ {label}: {detail}; regenerate with: `{command}`."


def _run_degraded(state):
    """Did finalize publish a degraded run?

    One predicate, read by every step that can be a run's last — step 11
    (bot mode) and step 12 (interactive) — so the completion footer cannot
    tell one host the truth and the other a checkmark. An unfinalized run
    reads as not degraded: there is no outcome to report, and claiming one
    either way would be a fabrication.
    """
    status = state.get("pipeline_status")
    return bool(status) and status != "success"


def _settlement_lines(state):
    """Render settled state according to its publication phase.

    Reads the facts step 11's orchestration recorded in state rather than
    re-opening pipeline-result.json — this module is pure, the same division
    that already puts `critic_source` in state for step 10.

    A prepare pass may describe state as prepared, but must not call it a
    projection: pipeline-result.json does not exist yet. A publish pass can
    say published because the report handoff and terminal marker now exist.
    A run whose settlement never ran records nothing rather than fabricating
    a success line.
    """
    status = state.get("pipeline_status")
    if not status:
        return None
    verdict = state.get("verdict") or "unknown"
    source = state.get("verdict_source")
    source_suffix = f" ({source})" if source else ""
    label = (
        "Prepared state"
        if state.get("publication_pending") is not False
        else "Published"
    )
    lines = [f"{label}: status={status}  verdict={verdict}{source_suffix}"]
    notes = state.get("degradation_notes")
    if isinstance(notes, list) and notes:
        lines.append("Degradations:")
        lines.extend(f"  - {note}" for note in notes)
    return lines


def _report_authoring_actions(mode, state, context, config, output_dir):
    """The step-11 block that authors or regenerates the bound report.

    This is the whole of the review's audience-facing output, and in bot
    mode it IS the posted GitHub PR comment (pirategoat-bot reads the file
    verbatim). It is authored here, at the end, for one reason: nothing
    presentation-shaped exists while the decision critic runs, so there is
    no pre-critic prose for a REVISE to chase. Everything the report needs
    is settled by now — the ledger has absorbed any adjustments, the record
    has been re-assembled from it, and the verdict has been derived. The
    source fingerprint handles the exceptional late change by rejecting
    stale prose and requiring regeneration before publication.
    """
    od = output_dir or "<OUTPUT_DIR>"
    degradation = state.get("degradation", {})
    handoff_status = state.get("report_handoff_status")
    regenerate = handoff_status in {
        "source_changed", "stale_report_unchanged", "unbound_report",
    }
    actions = []

    reconciliation_failed = bool(degradation.get("reconciliation_failed"))
    ledger_status = state.get("ledger_status")
    ledger_usable = ledger_status == "ok"
    ledger_absent = ledger_status == "absent"
    record_outcome = state.get("review_record")
    record_usable = (
        not reconciliation_failed
        and ledger_usable
        and isinstance(record_outcome, dict)
        and record_outcome.get("status") == "complete"
    )
    settled_source_label = (
        "newly settled record and ledger below"
        if ledger_usable
        else "available finalized reviewer sources below"
    )

    if handoff_status == "source_changed":
        actions.append(
            "⚠️ **The prepared report source changed during re-settlement.** "
            "The existing report is stale and cannot be published. "
            f"Regenerate it from the {settled_source_label}."
        )
        actions.append("")
    elif handoff_status == "stale_report_unchanged":
        actions.append(
            "⚠️ **The rejected stale report has not changed.** Regenerate "
            f"it from the {settled_source_label} before "
            "re-running step 11."
        )
        actions.append("")
    elif handoff_status == "unbound_report":
        actions.append(
            "⚠️ **The existing report predates a prepared source binding.** "
            "It is not deliverable as-is. Regenerate it from the "
            f"{settled_source_label}, then re-run step 11."
        )
        actions.append("")

    action_verb = "Regenerate" if regenerate else "Author"
    actions.append(
        f"**{action_verb} `{od}/review-report.md` now"
        + (".** " if regenerate else " — once.** ")
        + "This is the "
        "audience-facing presentation of the review"
        + (
            ", and it is posted verbatim as the PR comment."
            if not config.get("interactive", True)
            else "."
        )
    )
    actions.append("")

    if reconciliation_failed:
        actions.append(
            "⚠️ Reconciliation failed, so there is no ledger and no review "
            f"record. Synthesize the report manually from the raw "
            f"`{od}/<agent>-review.md` files — this is the sanctioned "
            "degraded path. Say plainly in the report that reconciliation "
            "failed and the findings are unreconciled."
        )
    elif ledger_absent:
        actions.append(
            "⚠️ The canonical ledger is absent, so there is no usable review "
            f"record. Synthesize the report manually from the finalized "
            f"`{od}/<agent>-review.md` files and say plainly that the "
            "findings are unreconciled."
        )
    elif not ledger_usable:
        actions.append(
            "⚠️ The canonical ledger was rejected at the pipeline boundary "
            f"(status: `{ledger_status or 'unavailable'}`), so it is "
            "not valid report source context. Synthesize the report manually "
            f"from the finalized `{od}/<agent>-review.md` files and say "
            "plainly that the findings are unreconciled."
        )
    elif record_usable:
        actions.append(
            f"**Source:** `{od}/{REVIEW_RECORD_MD}` — the pipeline's own "
            "machine-assembled account of this run, re-assembled moments "
            f"ago from the final ledger — and `{od}/review-findings.json`, "
            "the canonical ledger it projects. The record is the reference "
            "the report must not contradict: every finding, severity, "
            "count, check, and coverage statement in your report has to "
            "agree with it."
        )
    else:
        actions.append(
            f"**Source:** `{od}/review-findings.json` — the canonical "
            f"ledger. The pipeline could not assemble `{REVIEW_RECORD_MD}` "
            "for this run, so read the ledger directly."
        )
    actions.append("")

    # The report's voice: the caller's override verbatim when one exists
    # (bot mode seeds it into run-config.json), otherwise the
    # mode-appropriate default with the PR author's name folded in.
    output_instructions = config.get("output_instructions")
    if output_instructions:
        actions.append("**Output instructions (caller override):**")
        actions.append(output_instructions)
    elif mode == "pr":
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
    verdict_source_clause = (
        f"it came from `{od}/review-findings.json`, and the report is a "
        "presentation of that decision, not a second one."
        if ledger_usable
        else "the rejected or absent ledger is not a source for this report, "
             "so present the pipeline's fallback or override, not a second "
             "decision."
    )
    actions.append(
        "Include a verdict (APPROVE, REQUEST_CHANGES, or COMMENT) matching "
        "the one the pipeline derived and printed below — "
        f"{verdict_source_clause}"
    )

    if ledger_usable:
        actions.append(
            f"**What held comes from the ledger, never from memory.** Any "
            f"\"what we checked and it held\" / \"verified absences\" "
            f"content in the report is quoted from the `## Verified Checks` "
            f"section of the record — the reconciliator recorded there "
            f"exactly the checks that "
            f"survived its method judgment, with attribution. Do not "
            f"reconstruct that list from what you recall reviewers saying: "
            f"a check the reconciliator rejected as method-inadequate "
            f"would come back as fact. If that section is absent, nothing "
            f"was recorded as held — write no such section rather than "
            f"filling one in."
        )

    # Host context banner passthrough — if degraded, surface at the top.
    banner = (context.get("host_context") or {}).get("banner") or {}
    if banner.get("degraded"):
        actions.append("")
        record_projection = (
            " (the pipeline renders the same blockquote onto the record "
            "from the findings JSON)"
            if ledger_usable
            else ""
        )
        actions.append(
            f"**Host context banner:** prepend this blockquote to the top of "
            f"`review-report.md`{record_projection}:"
        )
        actions.append("")
        actions.append(f"> **⚠ Host Context Banner:** {banner.get('message', '')}")

    # Coverage. The measurement itself is already rendered, complete and
    # hedged, in the record — so the report quotes it rather than
    # re-deriving it. A field run once paraphrased "skipped by every
    # matching agent's diff budget and no reviewer reported reviewing
    # them" into "read by nobody", false for 8 of 41 files.
    if record_usable and _render_file_review_section(
        state.get("file_review")
    ):
        gap_clause = (
            " The verdict must acknowledge this gap."
            if _has_file_review_gap(state.get("file_review"))
            else ""
        )
        actions.append("")
        actions.append(
            f"**⚠ Review coverage.** The record carries a `## Review "
            f"coverage` section. Copy it into `{od}/review-report.md` "
            "VERBATIM. You may add your own commentary AFTER the block; "
            "never restate, summarize, re-count, or edit the machine's "
            "sentences — the hedges in them are the measurement, and a "
            f"tighter paraphrase is a false claim.{gap_clause}"
        )

    actions.append("")
    actions.append(
        "**This is a source-bound handoff.** Write from the exact settled "
        "sources above. The next step-11 pass rechecks their fingerprint "
        "before terminal publication and rejects this report if settlement "
        "changed underneath it."
    )
    return actions


def _step_11_present_results(mode, state, context, config, output_dir):
    """Step 11: gate report authoring, then present terminal publication."""
    od = output_dir or "<OUTPUT_DIR>"
    is_interactive = config.get("interactive", True)
    critic_verdict = state.get("critic_verdict")
    publication_pending = state.get("publication_pending") is not False
    handoff_status = state.get("report_handoff_status")

    situation = [_PHASE_TRANSITIONS["OUTPUT"]]
    if publication_pending:
        actions = _report_authoring_actions(
            mode, state, context, config, output_dir
        )
        if is_interactive and critic_verdict == "unavailable":
            actions.append("")
            critic_source = (
                "the settled record as-is"
                if state.get("ledger_status") == "ok"
                else "the available finalized sources as directed above"
            )
            actions.append(
                "⚠️ Critic verdict unavailable — author the report from "
                f"{critic_source}."
            )
        actions.append("")
        actions.append(
            "After writing the report, verify the file exists and re-run "
            "step 11 exactly as printed below. A matching pass publishes "
            "`pipeline-result.json`; do not report this run complete before "
            "that pass succeeds."
        )
    else:
        actions = [
            f"Report handoff verified at `{od}/review-report.md`; the "
            "terminal `pipeline-result.json` is now published.",
            "",
        ]

    if not publication_pending and is_interactive:
        actions.append("Then present a formatted summary of the report you "
                       "just wrote, with verdict and key findings.")

        if critic_verdict == "unavailable":
            actions.append("⚠️ Critic verdict unavailable — present review as-is.")

        actions.append("To drill down on a specific topic, re-invoke the reconciliator "
                       "in focused mode.")

        if mode == "incremental":
            actions.append("Baseline saved. Next `/code-review` reviews only new commits.")

    elif not publication_pending:
        # Non-interactive: list output files
        actions.append("Published output files:")
        actions.append(f"- `{od}/review-report.md` — the report you author "
                       "here; posted verbatim as the PR comment")
        actions.append(
            f"- `{od}/{REVIEW_RECORD_MD}` — the pipeline's machine-assembled "
            "record of the run"
        )
        actions.append(
            f"- `{od}/review-findings.json` + `review-findings.md` "
            "(rendered from the JSON by the pipeline)"
        )
        actions.append(f"- `{od}/pipeline-result.json` — status, verdict, report_path, "
                       "findings_path, critic_verdict, degradation_notes, "
                       "worktree_hygiene (compact hygiene summary; null when "
                       "the run never measured it), usage (compact token "
                       "usage: subagent totals, per-model split, agents "
                       "measured, and each half's availability — subagent "
                       "usage is complete at finalize, orchestrator usage is "
                       "partial because its own session is still open; null "
                       "when the run never measured usage), verdict_source "
                       "(which branch produced the verdict: the findings "
                       "ledger, the critic's ESCALATE override, or the "
                       "fallback, whose degradation note says why)")

        if mode == "incremental":
            actions.append("Baseline saved. Next run reviews only new commits.")

    actions.append(_derived_markdown_status_line(
        state, od, key="reviewer_markdown", label="Reviewer Markdown",
    ))
    ledger_status = state.get("ledger_status")
    if ledger_status in ("ok", "absent"):
        actions.append(_derived_markdown_status_line(
            state, od, key="findings_markdown", label="Findings Markdown",
            suffix="review-findings.json",
        ))
    else:
        actions.append(
            "⚠️ Findings Markdown: not materialized because the canonical "
            f"ledger status is `{ledger_status or 'unavailable'}`."
        )

    # The first pass reports prepared state without implying publication;
    # the second reports the terminal projection that now exists.
    settlement = _settlement_lines(state)
    if settlement:
        actions.append("")
        actions.extend(settlement)

    return {
        "phase": "OUTPUT",
        "title": "Author Report + Present Results",
        "situation": situation,
        "actions": actions,
        # The one artifact this step asks the orchestrator for, and the one
        # the run's whole output rests on: pirategoat-bot reads this file
        # and fails the delivery if it is absent, so the gate has to be
        # here rather than left implicit.
        "handoff": (
            [(
                f"Regenerate `{od}/review-report.md` from the newly settled "
                "source, then re-run step 11 before reporting the pipeline "
                "complete."
                if handoff_status in {
                    "source_changed", "stale_report_unchanged",
                    "unbound_report",
                }
                else f"Verify `{od}/review-report.md` exists, then re-run "
                     "step 11 before reporting the pipeline complete."
            )]
            if publication_pending
            else None
        ),
        "blocks_progress": publication_pending,
        # Read by pipeline.py's format_output for the completion footer: a
        # run that degraded must not sign off with a green checkmark. Step
        # 12 sets it the same way — it, not this step, is where an
        # interactive run's footer is printed.
        "degraded": _run_degraded(state),
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
        # Step 12 is the LAST step of an interactive run, so it — not step
        # 11 — is where `format_output` prints the completion footer there.
        # Setting the flag only on step 11 made the whole honesty mechanism
        # bot-only: an interactive run that degraded printed its
        # degradations at step 11 and then signed off "✅ PIPELINE COMPLETE"
        # at step 12. `pipeline_status` survives in state from finalize, so
        # the fact is already here to read.
        "degraded": _run_degraded(state),
    }

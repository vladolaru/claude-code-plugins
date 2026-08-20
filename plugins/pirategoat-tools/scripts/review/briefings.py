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
    from .dependency_refresh import (
        SKIP_REASON_DIRTY_WORKTREE,
        SKIP_REASON_WORKTREE_STATUS_FAILED,
    )
except ImportError:
    _scripts_parent = str(Path(__file__).resolve().parent.parent)
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.pipeline_contract import (
        DEFAULT_AGENT_TIMEOUT,
        HOST_CODEX,
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
    from review.dependency_refresh import (
        SKIP_REASON_DIRTY_WORKTREE,
        SKIP_REASON_WORKTREE_STATUS_FAILED,
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

    All three lists are empty when the requester did not opt in. Detection
    failure and empty detection each get one honest situation line so the
    orchestrator never has to guess why no refresh instructions appeared.
    """
    if not (config or {}).get("refresh_dependencies"):
        return [], [], []
    od = output_dir or "<OUTPUT_DIR>"
    detection = state.get("dependency_refresh") or {}
    signals = detection.get("signals") or []

    if detection.get("detection_failed"):
        return (
            ["**Dependency refresh:** enabled, but stale-dependency "
             "detection failed — treat dependency staleness as unknown and "
             "proceed without refreshing.", ""],
            [],
            [],
        )
    if not signals:
        return (
            ["**Dependency refresh:** enabled — no stale dependency roots "
             "detected; nothing to refresh.", ""],
            [],
            [],
        )
    if detection.get("skipped_reason") == SKIP_REASON_DIRTY_WORKTREE:
        return (
            ["**Dependency refresh:** enabled, but refresh skipped — stale "
             "dependency roots were detected and the tracked worktree has "
             "pre-existing tracked changes. The review will proceed with "
             "degraded host context; the requester can commit or stash those "
             "changes, then re-run.", ""],
            [],
            [],
        )
    if detection.get("skipped_reason") == SKIP_REASON_WORKTREE_STATUS_FAILED:
        return (
            ["**Dependency refresh:** enabled, but refresh skipped — stale "
             "dependency roots were detected, but the pipeline could not "
             "verify that the tracked worktree is clean. The review will "
             "proceed with degraded host context; resolve the Git status "
             "failure and re-run.",
             ""],
            [],
            [],
        )
    situation = [
        "**Dependency refresh (trusted-branch mode):** the requester "
        "authorized refreshing installed dependencies in this worktree. "
        "Stale dependency roots detected:",
    ]
    for s in signals:
        reasons = ", ".join(s.get("reasons", []))
        presence = (
            "installed state present"
            if s.get("installed_state_present")
            else "installed state missing"
        )
        situation.append(
            f"- {s.get('manager')} in `{s.get('directory')}` ({reasons}; "
            f"{presence}) — suggested: `{s.get('suggested_command')}`"
        )
    situation.append("")

    actions = [
        "Refresh the stale dependency roots BEFORE writing the "
        "change-purpose summary, so host context reflects what reviewers "
        "will read:",
        "1. Run each suggested command in its listed directory. Commands "
        "disable lifecycle scripts on purpose; do not strip flags. Classic "
        "Yarn v1 spelling is `yarn install --frozen-lockfile "
        "--ignore-scripts`.",
        "- NEVER run update/upgrade/add/require, never chain commands "
        "(`&&`, `;`); install must not modify tracked files. Pipeline "
        "independently verifies reported commands and worktree state at "
        "next step.",
        # Detection proves cleanliness only at the start of step 3. The
        # retained post-install check catches tracked edits introduced in the
        # TOCTOU window before or during the orchestrator's install attempts.
        "2. After all install attempts, even when an install command fails, "
        "run `git status --porcelain --untracked-files=no` again. If tracked "
        "changes appear, first record them as dependency-refresh failure "
        "evidence. The tracked worktree was verified clean before installs, "
        "so restore the refresh-created tracked changes with "
        "`git restore --source=HEAD --staged --worktree -- <path>`.",
        "3. Re-resolve host context so reviewers see the refreshed state: "
        f"`python3 {SCRIPTS_DIR / 'context.py'} --output-dir {od} "
        "--refresh-host-context`",
        "",
    ]

    handoff = [
        f"Write `{od}/dependency-refresh.json` recording what you ran:",
        "```json",
        '{"status": "<completed | partial | failed>",',
        ' "commands": [{"directory": "<dir>", "command": "<command run>", '
        '"exit_status": "<ok | failed>"}],',
        ' "tracked_files_dirty": <true | false>}',
        "```",
        "Verify the file exists before proceeding.",
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

    verification = state.get("dependency_refresh_verification") or {}
    if verification.get("tracked_files_dirty") is True:
        situation.append(
            "⚠️  Dependency refresh verification found modified tracked files:"
        )
        for path in verification.get("dirty_files", []):
            if isinstance(path, str):
                situation.append(f"- `{path}`")
        situation.extend([
            "Inspect each listed change and preserve or back up intentional "
            "edits. Use `git checkout -- <path>` only after confirming that "
            "specific change was caused solely by the dependency refresh. "
            f"Then update `{od}/dependency-refresh.json` BEFORE dispatch.",
            "",
        ])

    verification_reasons = []
    if verification.get("disallowed_commands"):
        verification_reasons.append("reported command outside the allowlist")
    if verification.get("verification_failed") is True:
        verification_reasons.append("verification itself failed")
    if verification_reasons:
        situation.extend([
            "⚠️  Dependency refresh could not be verified clean; proceeding is "
            "allowed, and the telemetry manifest records the verification "
            f"evidence honestly. Reasons: {'; '.join(verification_reasons)}.",
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

    claims = state.get("inline_coverage_claims") or {}
    if not isinstance(claims, dict):
        claims = {}
    if claims:
        actions.append("")
        actions.append(
            f"**⚠ Review coverage claims:** {len(claims)} changed file(s) were "
            "never diffed inline, but a deferring agent claims review from the "
            "NOT DIFFED queue. These claims are not proof of read. Include them "
            "in the 'Review coverage' section of `review-report.md`, labeled as "
            "claims:"
        )
        for f_path, agents in sorted(claims.items()):
            agents_list = agents if isinstance(agents, list) else [agents]
            claimed_by = ", ".join(
                _markdown_code_span(agent) for agent in agents_list
            )
            actions.append(
                f"- {_markdown_code_span(f_path)} (claimed by: {claimed_by})"
            )
        actions.append("")

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
    actions.append(
        f"On REVISE, also record every finding-level adjustment in "
        f"{od}/decision-critic-adjustments.json, per your agent instructions — "
        f"a recommendation that exists only as prose cannot reach the "
        f"machine-readable ledger."
    )
    actions.append("```")
    actions.append("")
    actions.append("**Wait for the critic to finish — do not run in background.**")
    actions.append("")
    actions.append(
        "Write the critic's verdict now — before acting on it, and before "
        "any adjustments:"
    )
    actions.append(f"```json")
    actions.append(f'// Save to: {od}/decision-critic-verdict.json')
    actions.append(f'{{"verdict": "<STAND | REVISE | ESCALATE>"}}')
    actions.append(f"```")
    actions.append(
        f"If the critic crashed, timed out, or otherwise produced no "
        f"usable verdict, write `{{\"verdict\": \"SKIPPED\", \"reason\": "
        f"\"<why>\"}}` instead — never invent a STAND/REVISE/ESCALATE "
        f"value on the critic's behalf just to satisfy the handoff gate."
    )
    actions.append("")
    actions.append("Act on the critic's verdict:")
    actions.append("")
    actions.append("**STAND** — No changes needed. Proceed to writing the final review verdict.")
    actions.append("")
    actions.append(
        "**REVISE** — the machine-readable ledger updates first, the prose second:"
    )
    actions.append(
        f"1) Read the critic's recommendations AND "
        f"`{od}/decision-critic-adjustments.json`."
    )
    actions.append(
        f"2) Spot-check the claims with `git grep`/`Read`. Mark any adjustment "
        f'the spot-check refutes with `"rejected": true` plus a '
        f"`rejection_reason` — a refuted decision stays visible as rejected, "
        f"it is not deleted."
    )
    actions.append(
        f"3) Carry the surviving adjustments into `{od}/review-findings.json`:"
    )
    actions.append("```bash")
    actions.append(
        f'python3 {SCRIPTS_DIR}/critic_adjustments.py --output-dir "{od}"'
    )
    actions.append("```")
    actions.append(
        f"This refuses — no writes at all — unless "
        f"`{od}/decision-critic-verdict.json` (written above) says REVISE; "
        f"the CLI enforces the REVISE-only gate itself, it does not just "
        f"trust this branch."
    )
    actions.append(
        f"4) Edit `{od}/review-report.md` so it matches the updated findings — "
        f"counts, severity table, and finding list must agree with the JSON."
    )
    actions.append("5) Write the final review verdict.")
    actions.append("")
    actions.append("**ESCALATE** — Override review verdict to **COMMENT** regardless of report, "
                   "then write the final review verdict.")
    actions.append("")
    actions.append("Write the final review verdict:")
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

def _reviewer_markdown_status_line(state, output_dir):
    """Summarize the derived reviewer-Markdown outcome for a human."""
    outcome = state.get("reviewer_markdown")
    if not isinstance(outcome, dict) or outcome.get("ran") is not True:
        detail = "materialization did not run"
    else:
        written = outcome.get("written", 0)
        expected = outcome.get("expected", 0)
        if (
            outcome.get("status") == "complete"
            and written == expected
            and written > 0
        ):
            return f"Reviewer Markdown: materialized {written}/{expected} files."
        detail = f"materialization {outcome.get('status', 'incomplete')} ({written}/{expected} files)"

    command = (
        f"python3 {shlex.quote(str(SCRIPTS_DIR / 'agent' / 'output.py'))} "
        f"materialize {shlex.quote(str(output_dir))}"
    )
    return f"⚠️ Reviewer Markdown: {detail}; regenerate with: `{command}`."


def _step_11_present_results(mode, state, context, config, output_dir):
    """Step 11: Present Results — show review output."""
    od = output_dir or "<OUTPUT_DIR>"
    is_interactive = config.get("interactive", True)
    critic_verdict = state.get("critic_verdict")
    forced_verdict = state.get("forced_verdict")
    review_verdict = state.get("review_verdict")

    situation = [_PHASE_TRANSITIONS["OUTPUT"]]
    actions = []

    if is_interactive:
        actions.append(f"Read `{od}/review-report.md` and present a formatted summary "
                       "with verdict and key findings.")

        if critic_verdict == "unavailable":
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
                       "findings_path, critic_verdict, degradation_notes, "
                       "worktree_hygiene (compact hygiene summary; null when "
                       "the run never measured it), usage (compact token "
                       "usage: subagent totals, per-model split, agents "
                       "measured, and each half's availability — subagent "
                       "usage is complete at finalize, orchestrator usage is "
                       "partial because its own session is still open; null "
                       "when the run never measured usage), verdict_sync "
                       "(Rule 23's outcome syncing review-findings.json's "
                       "verdict: \"synced\", \"skipped_shape_mismatch\", "
                       "\"failed_io\", or null when the sync was never "
                       "attempted) with verdict_sync_reason for the non-"
                       "synced states")

        if mode == "incremental":
            actions.append("Baseline saved. Next run reviews only new commits.")

    actions.append(_reviewer_markdown_status_line(state, od))

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

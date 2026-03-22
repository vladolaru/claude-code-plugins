#!/usr/bin/env python3
"""
Linear Issue Pipeline — curated-context-pipeline for issue investigation and fixes.

PIPELINE GOAL: Investigate Linear issues thoroughly and accurately, producing
trustworthy reports with root cause analysis. In fix mode, implement solutions
with verification and self-review, delivering draft PRs that are ready for
human review.

A single script owns a 15-step universal sequence. Mode (investigate|fix) and
conditions determine which steps run. The script curates context as
conversational briefings — the LLM executes the actions described in each
briefing.

Split file-based state:
  - run-config.json:      Caller config (mode, interactive). Set by bot before
                          step 1. Read-only during the run.
  - issue-context.json:   Pre-computed issue and repo context from the bot.
                          Read-only during the run.
  - pipeline-state.json:  Execution state. Owned exclusively by the script.
                          The LLM never reads or writes it.

Zero external dependencies (stdlib only).
"""

import argparse
import glob as glob_mod
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Pipeline Identity
# ---------------------------------------------------------------------------

_PIPELINE_MISSION = (
    "You are a Linear issue investigator and implementer. Your mission: "
    "investigate the given issue thoroughly and accurately, verify before "
    "assuming, and produce a trustworthy report with root cause analysis. "
    "In fix mode, plan and implement a solution with verification and "
    "self-review, delivering a draft PR ready for human review. Every step "
    "has required artifacts; treat each as a contract. Do not approximate, "
    "skip, or move on until the step's outputs are verified."
)

_PHASE_TRANSITIONS = {
    "INVESTIGATION": (
        "You now have the issue details and repo context. The next phase "
        "is investigation — your job is to verify the issue, understand "
        "the root cause, and produce a clear report. Be thorough but "
        "focused. Verify your findings against the actual code."
    ),
    "IMPLEMENTATION": (
        "The investigation is complete. You now shift to implementation — "
        "plan carefully, execute methodically, and verify after each step. "
        "The quality of the draft PR depends on the discipline you bring here."
    ),
    "VALIDATION": (
        "Implementation is complete. Now validate — run the iterative review "
        "loop for multi-round independent review with pushback tracking and "
        "convergence detection. The goal is a draft PR the author can trust."
    ),
    "OUTPUT": (
        "All work is done. Present results clearly, confirm all artifacts "
        "are written, and verify the pipeline result is complete. This is "
        "what the bot and the user receive — make sure nothing is missing."
    ),
}

# ---------------------------------------------------------------------------
# Step Sequence
# ---------------------------------------------------------------------------

STEP_SEQUENCE = [
    {"step": 1,  "title": "Parse Input",         "phase": "SETUP",          "condition": "always"},
    {"step": 2,  "title": "Fetch Issue",          "phase": "SETUP",          "condition": "always"},
    {"step": 3,  "title": "Check Existing Work",  "phase": "SETUP",          "condition": "always"},
    {"step": 4,  "title": "Gather Context",        "phase": "INVESTIGATION",  "condition": "always"},
    {"step": 5,  "title": "Investigate",            "phase": "INVESTIGATION",  "condition": "always"},
    {"step": 6,  "title": "Write Report",           "phase": "INVESTIGATION",  "condition": "always"},
    {"step": 7,  "title": "Post to Linear",         "phase": "INVESTIGATION",  "condition": "always"},
    {"step": 8,  "title": "Assess Clarity",         "phase": "INVESTIGATION",  "condition": "always"},
    {"step": 9,  "title": "Write Plan",             "phase": "IMPLEMENTATION", "condition": "fix_mode_and_unresolved"},
    {"step": 10, "title": "Implement",              "phase": "IMPLEMENTATION", "condition": "fix_mode_and_unresolved"},
    {"step": 11, "title": "Verify",                 "phase": "IMPLEMENTATION", "condition": "fix_mode_and_unresolved"},
    {"step": 12, "title": "Self-Review",            "phase": "VALIDATION",     "condition": "fix_mode_and_unresolved"},
    {"step": 13, "title": "Re-Verify",              "phase": "VALIDATION",     "condition": "fix_mode_and_unresolved"},
    {"step": 14, "title": "Create Draft PR",        "phase": "OUTPUT",         "condition": "fix_mode_and_unresolved"},
    {"step": 15, "title": "Present Results",         "phase": "OUTPUT",         "condition": "always"},
]

_STEP_MAP = {s["step"]: s for s in STEP_SEQUENCE}

# Artifacts to clear at step 1 (stale from previous runs)
_STALE_ARTIFACTS = [
    "pipeline-state.json",
    "pipeline-result.json",
    "pipeline-events.jsonl",
    "investigation-report.md",
    "implementation-plan.md",
    "clarity-assessment.json",
]

# Files to preserve across runs
_PRESERVED_FILES = {
    "run-config.json",
    "issue-context.json",
}


# ---------------------------------------------------------------------------
# Condition Evaluation
# ---------------------------------------------------------------------------

def _eval_condition(condition, mode, config, state, context):
    """Evaluate a step condition. Returns True if step should run."""
    if condition == "always":
        return True

    if condition == "fix_mode_only":
        return mode == "fix"

    if condition == "fix_mode_and_unresolved":
        # Fix-mode steps 9-14 only run when:
        # 1. Mode is fix
        # 2. Issue isn't already resolved (step 3 sets issue_resolved)
        # 3. Clarity gate hasn't blocked (step 8 sets clarity_blocked)
        #    UNLESS skip_clarity_gate override is set in config
        if mode != "fix":
            return False
        if state.get("issue_resolved", False):
            return False
        if state.get("clarity_blocked", False):
            return config.get("skip_clarity_gate", False)
        return True

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
    candidates = sorted(s for s in active_steps if s > current_step)
    if not candidates:
        return None

    next_num = candidates[0]
    step_def = _STEP_MAP[next_num]

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
    "issue": {},
    "investigation": {},
    "implementation": {},
    "degradation_notes": [],
}


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
        return {}


def write_config(output_dir, config):
    """Write run-config.json."""
    path = os.path.join(output_dir, "run-config.json")
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def read_issue_context(output_dir):
    """Read issue-context.json, return empty dict if missing or corrupted."""
    path = os.path.join(output_dir, "issue-context.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Stale Artifact Cleanup
# ---------------------------------------------------------------------------

def clean_stale_artifacts(output_dir):
    """Remove stale run artifacts, preserving run-config.json and issue-context.json."""
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
# Subprocess Helper
# ---------------------------------------------------------------------------

def _run_subprocess(cmd, cwd=None, timeout=60):
    """Run a subprocess and return (stdout, success). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        if r.returncode == 0:
            return r.stdout.strip(), True
        return r.stdout.strip(), False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "", False


# ---------------------------------------------------------------------------
# Repo Sanity Check
# ---------------------------------------------------------------------------

def check_repo_match(context):
    """Check if CWD repo matches the expected repo from issue-context.json.

    Compares the origin remote URL against context["repo_slug"].
    Returns (matches: bool, actual_slug: str|None, expected_slug: str|None).
    """
    expected = context.get("repo_slug")
    if not expected:
        # No repo expectation in context — skip check
        return True, None, None

    stdout, ok = _run_subprocess(["git", "remote", "get-url", "origin"])
    if not ok or not stdout:
        # Can't determine current repo — skip check (don't block on git failures)
        return True, None, expected

    # Extract owner/repo from remote URL
    # Handles: git@github.com:Owner/Repo.git, https://github.com/Owner/Repo.git
    m = re.search(r'[:/]([^/]+/[^/]+?)(?:\.git)?$', stdout)
    if not m:
        return True, stdout, expected

    actual = m.group(1)

    # Case-insensitive comparison (GitHub slugs are case-insensitive)
    return actual.lower() == expected.lower(), actual, expected


# ---------------------------------------------------------------------------
# Event Emission (best-effort)
# ---------------------------------------------------------------------------

def _init_events(output_dir):
    """Import and initialize PipelineEventEmitter. Returns None on failure."""
    try:
        import importlib.util
        events_path = SCRIPTS_DIR / "pipeline_events.py"
        spec = importlib.util.spec_from_file_location("pipeline_events", events_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.PipelineEventEmitter(output_dir)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Format Output
# ---------------------------------------------------------------------------

def format_output(step, guidance):
    """Format guidance into curated-context-pipeline output."""
    lines = []

    # Header
    phase = guidance["phase"]
    title = guidance["title"]
    lines.append(f"{'═' * 60}")
    lines.append(f"LINEAR ISSUE PIPELINE Step {step} — {phase}: {title}")
    lines.append(f"{'═' * 60}")
    lines.append("")

    # Skip explanation
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
        lines.append(
            f"Run: python3 {SCRIPTS_DIR / 'linear-issue-pipeline.py'} "
            f"--step {ns['step']} --output-dir <OUTPUT_DIR> --issue-id <ISSUE_ID>"
        )
    else:
        lines.append(f"{'─' * 60}")
        lines.append("✅ PIPELINE COMPLETE")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step Guidance (pure formatting — no I/O, no subprocesses)
# ---------------------------------------------------------------------------

def get_step_guidance(step, mode, state, context, config=None, output_dir=None):
    """Return briefing dict for a step. Pure formatting — no I/O.

    Args:
        step: Step number (1-15)
        mode: Pipeline mode (investigate, fix)
        state: Pipeline state dict (from pipeline-state.json)
        context: Issue context dict (from issue-context.json)
        config: Run config dict (from run-config.json), optional
        output_dir: Output directory path, optional

    Returns:
        Dict with phase, title, situation, actions, handoff.
        None if step number is invalid.
    """
    if step not in _STEP_MAP:
        return None

    config = config or {}
    output_dir = output_dir or ""

    if step == 1:
        return _step_1_parse_input(mode, state, context, config, output_dir)
    elif step == 2:
        return _step_2_fetch_issue(mode, state, context, config, output_dir)
    elif step == 3:
        return _step_3_check_existing(mode, state, context, config, output_dir)
    elif step == 4:
        return _step_4_gather_context(mode, state, context, config, output_dir)
    elif step == 5:
        return _step_5_investigate(mode, state, context, config, output_dir)
    elif step == 6:
        return _step_6_write_report(mode, state, context, config, output_dir)
    elif step == 7:
        return _step_7_post_to_linear(mode, state, context, config, output_dir)
    elif step == 8:
        return _step_8_assess_clarity(mode, state, context, config, output_dir)
    elif step == 9:
        return _step_9_write_plan(mode, state, context, config, output_dir)
    elif step == 10:
        return _step_10_implement(mode, state, context, config, output_dir)
    elif step == 11:
        return _step_11_verify(mode, state, context, config, output_dir)
    elif step == 12:
        return _step_12_self_review(mode, state, context, config, output_dir)
    elif step == 13:
        return _step_13_reverify(mode, state, context, config, output_dir)
    elif step == 14:
        return _step_14_create_draft_pr(mode, state, context, config, output_dir)
    elif step == 15:
        return _step_15_present_results(mode, state, context, config, output_dir)
    else:
        return None


# ---------------------------------------------------------------------------
# Step 1: Parse Input
# ---------------------------------------------------------------------------

def _step_1_parse_input(mode, state, context, config, output_dir):
    """Step 1: Parse Input — confirm parameters, mode, and repo match."""
    issue_id = context.get("issue_id", config.get("issue_id", "unknown"))
    repo_mismatch = state.get("repo_mismatch")
    interactive = config.get("interactive", True)

    situation = [_PIPELINE_MISSION, ""]
    situation.append(f"Mode: **{mode}** {'(investigate only — report, no code changes)' if mode == 'investigate' else '(investigate + implement + draft PR)'}")
    situation.append(f"Issue: **{issue_id}**")

    # Repo mismatch — hard stop
    if repo_mismatch:
        actual = repo_mismatch.get("actual", "unknown")
        expected = repo_mismatch.get("expected", "unknown")
        situation.append("")
        situation.append(f"⛔ **REPO MISMATCH:** This repository is `{actual}` but issue **{issue_id}** belongs to `{expected}`.")

        if interactive:
            actions = [
                f"⛔ PIPELINE STOPPED: You are in the wrong repository.",
                f"",
                f"This issue ({issue_id}) belongs to **{expected}**, but the current",
                f"working directory is **{actual}**.",
                f"",
                f"Switch to the correct repository and try again:",
                f"  `cd /path/to/{expected.split('/')[-1] if '/' in expected else expected}`",
            ]
        else:
            actions = [
                f"⛔ PIPELINE STOPPED: Repo mismatch — expected {expected}, got {actual}.",
                f"pipeline-result.json has been written with status: failed.",
            ]

        return {
            "phase": "SETUP",
            "title": "Parse Input",
            "situation": situation,
            "actions": actions,
            "handoff": None,
        }

    actions = [
        f"1. Read `{output_dir}/issue-context.json` — this contains pre-computed issue and repo context from the bot.",
        f"2. Read `{output_dir}/run-config.json` — this contains the pipeline mode and configuration.",
        "3. Confirm the issue ID and mode are correct.",
        "4. If issue-context.json is missing or empty, the pipeline cannot proceed — report failure.",
        "",
        "5. **Repo sanity check:** Run `git remote get-url origin` and confirm the repo matches",
        "   the `repo_slug` in issue-context.json. If they don't match, STOP — you are in the",
        "   wrong codebase. Report the mismatch and do not proceed.",
    ]

    return {
        "phase": "SETUP",
        "title": "Parse Input",
        "situation": situation,
        "actions": actions,
        "handoff": [
            "issue-context.json has been read and contains a valid issue_id",
            "run-config.json has been read and contains a valid mode",
            "Repo matches the expected repo_slug (or no repo_slug to check against)",
        ],
    }


# ---------------------------------------------------------------------------
# Step 2: Fetch Issue
# ---------------------------------------------------------------------------

def _step_2_fetch_issue(mode, state, context, config, output_dir):
    """Step 2: Fetch Issue — pull issue details and comments from Linear."""
    issue_id = context.get("issue_id", "unknown")

    situation = [
        f"Issue **{issue_id}** needs to be fetched from Linear with all comments.",
        "",
        "Comments are mandatory — they often contain updates, workarounds, scope changes,",
        "and partial fixes that change the investigation direction.",
    ]

    actions = [
        f"1. Fetch the issue using Linear MCP: `mcp__linear-server__get_issue` with issue ID `{issue_id}`",
        f"2. Fetch all comments: `mcp__linear-server__list_comments` for issue `{issue_id}`",
        "3. Extract and note:",
        "   - Title, description, acceptance criteria",
        "   - All comments (read every one — they contain critical context)",
        "   - Labels, priority, assignees, state",
        "   - Linked PRs and their status (merged, open, closed, draft)",
        "   - Project membership (if any — check for project-level context)",
        "",
        "**If Linear MCP is unavailable:** This is a hard failure. Report that the pipeline",
        "cannot proceed without issue data and stop.",
    ]

    return {
        "phase": "SETUP",
        "title": "Fetch Issue",
        "situation": situation,
        "actions": actions,
        "handoff": [
            "Issue details (title, description, labels, priority, state) are captured",
            "All comments have been read and key information extracted",
            "Linked PRs identified with their current status",
        ],
    }


# ---------------------------------------------------------------------------
# Step 3: Check Existing Work
# ---------------------------------------------------------------------------

def _step_3_check_existing(mode, state, context, config, output_dir):
    """Step 3: Check Existing Work — search for existing PRs, verify repo fit."""
    issue_id = context.get("issue_id", "unknown")
    repo_slug = context.get("repo_slug", "")

    situation = [
        f"Before investigating, check if work on **{issue_id}** already exists,",
        "and verify this issue actually belongs to the current repository.",
        "",
        "A Linear team prefix (e.g., TRAPLAT) can map to multiple repos. The bot",
        "picks one based on config, but the issue may actually be about a different",
        "codebase. You must verify before investing time in investigation.",
    ]

    actions = [
        "**A. Verify this issue belongs to this repo**",
        "",
        "Cross-reference the issue content (from step 2) against this codebase:",
        "",
        "1. **Linked PRs:** Which repo are they in? If all linked PRs target a",
        "   different repo, that's strong signal this issue doesn't belong here.",
        "2. **File paths mentioned** in description/comments: Do they exist in this",
        "   repo? Run `ls` or `find` on a few key paths.",
        "3. **Component/module names:** Do the components mentioned in the issue",
        "   (class names, hook names, API endpoints, admin pages) exist here?",
        "4. **Labels or project context:** Do they reference a specific codebase",
        "   or deployment target that doesn't match this repo?",
        "",
        "If 2+ signals point to a different repo, **STOP the pipeline:**",
        f"- Write `{os.path.join(output_dir, 'pipeline-result.json') if output_dir else 'pipeline-result.json'}` with:",
        '  `{"status": "failed", "degradation_notes": ["Wrong repo: issue appears to be about <REPO>, not ' + (repo_slug or '<current>') + '"]}`',
        "- Output the JSON and stop. Do NOT continue to step 4.",
        "",
        "If the evidence is ambiguous (some paths exist, some don't), note the",
        "uncertainty and proceed — the investigation itself will clarify.",
        "",
        "**B. Check for existing work**",
        "",
        "1. Check linked PRs from the Linear issue data (step 2):",
        "   - Merged PR → investigate if the fix actually resolves the issue. If yes, report 'already done' and recommend closing.",
        "   - Open PR (non-draft) → note in findings. Investigation continues but should reference the PR.",
        "   - Draft PR → note but continue normally.",
        "",
        f"2. Search GitHub for branches/PRs mentioning `{issue_id}`:",
        f"   ```bash",
        f"   gh pr list --search '{issue_id}' --state all --limit 10",
        f"   ```",
        "",
        "3. If a merged PR fully resolves the issue:",
        "   - Write a brief note to pipeline state",
        "   - The pipeline will still produce a report (step 6) noting the resolution",
        "",
        "4. If no existing work found, proceed to investigation.",
    ]

    return {
        "phase": "SETUP",
        "title": "Check Existing Work",
        "situation": situation,
        "actions": actions,
        "handoff": [
            "Issue verified to belong to this repository (or ambiguity noted)",
            "Existing PRs and branches searched and documented",
            "Decision: issue is unresolved (continue) or resolved (report and stop at step 7)",
        ],
    }


# ---------------------------------------------------------------------------
# Step 4: Gather Context
# ---------------------------------------------------------------------------

def _step_4_gather_context(mode, state, context, config, output_dir):
    """Step 4: Gather Context — analyze repo code relevant to the issue."""
    issue_id = context.get("issue_id", "unknown")

    situation = [
        _PHASE_TRANSITIONS["INVESTIGATION"],
        "",
        f"Gathering code context for **{issue_id}**.",
        "The goal is to understand the current state of the code related to the issue",
        "before diving into type-specific investigation.",
    ]

    actions = [
        "1. From the issue description and comments, identify:",
        "   - Key components, files, or code paths mentioned",
        "   - Error messages, stack traces, or specific behaviors",
        "   - UI elements or admin pages affected",
        "",
        "2. Search the codebase for relevant code:",
        "   - Use Grep/Glob to find files related to the issue",
        "   - Use `git blame` on key files to understand recent changes",
        "   - Use `git log --oneline -20 -- <file>` for recent history on affected files",
        "",
        "3. Understand the current state:",
        "   - How does the affected code currently work?",
        "   - What are the relevant entry points, hooks, or API endpoints?",
        "   - What tests exist for this area?",
        "",
        "4. Note your findings — they feed into the investigation step.",
    ]

    return {
        "phase": "INVESTIGATION",
        "title": "Gather Context",
        "situation": situation,
        "actions": actions,
        "handoff": [
            "Relevant code files identified and understood",
            "Recent change history for affected code reviewed",
            "Current behavior documented",
        ],
    }


# ---------------------------------------------------------------------------
# Step 5: Investigate
# ---------------------------------------------------------------------------

def _step_5_investigate(mode, state, context, config, output_dir):
    """Step 5: Investigate — type-specific investigation."""
    issue_id = context.get("issue_id", "unknown")

    situation = [
        f"Deep investigation of **{issue_id}**.",
        "",
        "Identify the issue type from labels and description, then follow the",
        "appropriate investigation path.",
    ]

    actions = [
        "**Determine issue type** from labels and description:",
        "- `bug`, `defect`, 'broken', 'doesn't work', 'regression' → **Bug path**",
        "- `feature`, `enhancement`, 'add', 'new', 'implement' → **Feature path**",
        "- `task`, `chore`, `audit`, actionable work item → **Task path**",
        "",
        "### Bug Path",
        "1. **Search for duplicates:** Search Linear (same team) for similar keywords",
        "2. **Generate replication steps:** From issue + comments + code analysis",
        "3. **Validate the bug:**",
        "   - Code analysis: trace the code path, verify the bug exists in current code",
        "   - If UI bug: note for manual verification (browser testing not available in bot mode)",
        "4. **Root Cause Analysis (MANDATORY for all valid bugs):**",
        "   - What code is affected?",
        "   - WHY does it happen? (not just what)",
        "   - When was it introduced? (`git log`, `git blame`)",
        "   - Scope: isolated or pattern? (search for similar occurrences)",
        "",
        "### Feature Path",
        "1. Search for related issues (same team) and prior implementations",
        "2. Gather context from issue links, project docs",
        "3. Understand current state of the affected area",
        "4. Define scope: MVP vs full, constraints, open questions",
        "",
        "### Task Path",
        "1. Search for related work and completed issues",
        "2. Clarify acceptance criteria from issue + comments",
        "3. Identify affected areas and estimate effort",
        "",
        "**Verify your findings against the actual code** — do not re-read your own analysis.",
    ]

    return {
        "phase": "INVESTIGATION",
        "title": "Investigate",
        "situation": situation,
        "actions": actions,
        "handoff": [
            "Issue type determined (bug/feature/task)",
            "Type-specific investigation completed",
            "For bugs: RCA completed with affected code, root cause, scope",
            "Findings verified against actual code",
        ],
    }


# ---------------------------------------------------------------------------
# Step 6: Write Report
# ---------------------------------------------------------------------------

def _step_6_write_report(mode, state, context, config, output_dir):
    """Step 6: Write Report — generate investigation report."""
    issue_id = context.get("issue_id", "unknown")
    report_path = os.path.join(output_dir, "investigation-report.md") if output_dir else "investigation-report.md"

    situation = [
        f"Write the investigation report for **{issue_id}**.",
        "This report is the primary deliverable of the investigation phase.",
        "It will be posted to Linear (step 7) and delivered to the user via Slack.",
    ]

    actions = [
        f"Write `{report_path}` with the following structure:",
        "",
        "```markdown",
        f"# Investigation Report: {issue_id}",
        "",
        "## Summary",
        "One-paragraph verdict: valid/invalid/duplicate/already-fixed + key finding.",
        "",
        "## Issue Details",
        "- Type: bug/feature/task",
        "- Priority: from Linear",
        "- Status: current Linear status",
        "",
        "## Investigation Findings",
        "### For bugs:",
        "- Replication: confirmed/unconfirmed/code-analysis-only",
        "- Root Cause: [detailed RCA]",
        "- Scope: isolated/pattern (N occurrences)",
        "- Introduced: [commit/PR if identifiable]",
        "",
        "### For features:",
        "- Context gathered from: [sources]",
        "- Current state: [description]",
        "- Scope definition: [MVP/full]",
        "",
        "### For tasks:",
        "- Related work: [existing issues/PRs]",
        "- Scope: [definition]",
        "",
        "## Existing Work",
        "- Linked PRs: [list with status]",
        "- Related issues: [list]",
        "",
        "## Recommendation",
        "- Verdict: valid/invalid/duplicate/needs-more-info/already-fixed",
        "- Next steps: [specific actions]",
        "```",
        "",
        "**Be specific and evidence-based.** Link to code, commits, and issues.",
    ]

    return {
        "phase": "INVESTIGATION",
        "title": "Write Report",
        "situation": situation,
        "actions": actions,
        "handoff": [
            f"`{report_path}` exists and contains complete investigation findings",
        ],
    }


# ---------------------------------------------------------------------------
# Step 7: Post to Linear
# ---------------------------------------------------------------------------

def _step_7_post_to_linear(mode, state, context, config, output_dir):
    """Step 7: Post to Linear — post investigation report as a comment."""
    issue_id = context.get("issue_id", "unknown")

    situation = [
        f"Post the investigation report to **{issue_id}** as a Linear comment.",
        "This is best-effort — if Linear MCP fails, note the failure and continue.",
    ]

    actions = [
        "1. Read the investigation report from the previous step.",
        "2. Format it for Linear (markdown is supported).",
        "3. Post as a comment using Linear MCP:",
        f"   `mcp__linear-server__save_comment` on issue `{issue_id}`",
        "",
        "4. If posting fails:",
        "   - Note the failure as a degradation (the report is still saved locally)",
        "   - Continue to the next step — this is not a blocking failure",
        "",
        *([ "**Investigate mode:** After this step, the pipeline continues to the clarity assessment (step 8), then jumps to step 15 (Present Results)."] if mode == "investigate" else []),
    ]

    return {
        "phase": "INVESTIGATION",
        "title": "Post to Linear",
        "situation": situation,
        "actions": actions,
        "handoff": [
            "Linear comment posted (or failure noted as degradation)",
        ],
    }


# ---------------------------------------------------------------------------
# Step 8: Assess Clarity
# ---------------------------------------------------------------------------

def _step_8_assess_clarity(mode, state, context, config, output_dir):
    """Step 8: Assess Clarity — evaluate if the issue has sufficient clarity for implementation."""
    issue_id = context.get("issue_id", "unknown")
    assessment_path = os.path.join(output_dir, "clarity-assessment.json") if output_dir else "clarity-assessment.json"

    situation = [
        _PIPELINE_MISSION,
        "",
        f"Investigation of **{issue_id}** is complete. Before proceeding to implementation,",
        "assess whether the issue provides sufficient clarity for a successful fix.",
        "",
        "You have the full context: the raw issue description, all comments (from step 2),",
        "codebase analysis (from step 4), investigation findings (from step 5), and the",
        "investigation report (from step 6). Use ALL of this — especially the raw comments,",
        "which may contain contradictions the report summarized away.",
    ]

    actions = [
        "Evaluate the issue against these **hard gates** (any failure → block):",
        "",
        "1. **Problem statement** — Can you state in ONE sentence what needs to change?",
        "   If you cannot, the issue is too vague to implement safely.",
        "",
        "2. **Reproduction / scope** — For bugs: are there repro steps or can you derive",
        "   them from the investigation? For features: is the boundary clear (what's in",
        "   scope vs out of scope)?",
        "",
        "3. **Success criteria** — Is there a testable, observable outcome?",
        '   "Fix the checkout flow" is NOT criteria. "Payment total matches cart total',
        '   after applying the 10% coupon" IS criteria.',
        "",
        "Then check these **soft signals** (flag but don't block alone):",
        "",
        "4. **Conflicting signals** — Do comments contradict the description or each other?",
        "   Read the RAW comments from step 2, not just the report summary.",
        "",
        "5. **Missing technical context** — Does the issue reference components, flows, or",
        "   behavior that doesn't exist or is ambiguous in the codebase?",
        "",
        "6. **Implicit assumptions** — Are there unstated decisions the implementer would",
        "   have to guess at? (e.g., which users are affected, which edge cases matter)",
        "",
        f"Write `{assessment_path}` with this structure:",
        "```json",
        "{",
        '  "clear_enough": true | false,',
        '  "confidence": "high" | "medium" | "low",',
        '  "hard_gates": {',
        '    "problem_statement": {"pass": true | false, "note": "<explanation>"},',
        '    "reproduction_or_scope": {"pass": true | false, "note": "<explanation>"},',
        '    "success_criteria": {"pass": true | false, "note": "<explanation>"}',
        "  },",
        '  "soft_signals": {',
        '    "conflicting_signals": {"flagged": true | false, "note": "<explanation or null>"},',
        '    "missing_technical_context": {"flagged": true | false, "note": "<explanation or null>"},',
        '    "implicit_assumptions": {"flagged": true | false, "note": "<explanation or null>"}',
        "  },",
        '  "questions_for_author": ["<question 1>", "<question 2>", ...],',
        '  "summary": "<one paragraph explaining the clarity assessment>"',
        "}",
        "```",
        "",
        "**Decision rule:** `clear_enough` is `false` if ANY hard gate fails.",
        "All three hard gates pass + soft signals only → `clear_enough` is `true`",
        "(soft signals are surfaced as warnings but don't block).",
        "",
        "If `clear_enough` is false, generate 2-5 specific, answerable questions",
        "for the issue author in `questions_for_author`.",
    ]

    return {
        "phase": "INVESTIGATION",
        "title": "Assess Clarity",
        "situation": situation,
        "actions": actions,
        "handoff": [
            f"`{assessment_path}` exists and contains valid JSON",
            "`clear_enough` boolean is set based on hard gate evaluation",
            "If blocked: `questions_for_author` contains specific questions",
        ],
    }


# ---------------------------------------------------------------------------
# Step 9: Write Plan (fix mode only)
# ---------------------------------------------------------------------------

def _step_9_write_plan(mode, state, context, config, output_dir):
    """Step 9: Write Plan — create implementation plan from investigation."""
    issue_id = context.get("issue_id", "unknown")
    plan_path = os.path.join(output_dir, "implementation-plan.md") if output_dir else "implementation-plan.md"

    # If clarity gate was overridden, warn about known ambiguities
    clarity_note = []
    assessment_path = os.path.join(output_dir, "clarity-assessment.json") if output_dir else ""
    if config.get("skip_clarity_gate") and os.path.isfile(assessment_path):
        clarity_note = [
            "",
            "⚠️ **Clarity gate was overridden.** The user chose to proceed despite",
            f"flagged ambiguities. Read `{assessment_path}` and treat each flagged",
            "item as a documented risk in your plan. Call out assumptions explicitly.",
            "",
        ]

    situation = [
        _PHASE_TRANSITIONS["IMPLEMENTATION"],
        "",
        f"Create an implementation plan for **{issue_id}** based on the investigation findings.",
        "Use the `writing-plans` pattern for structured, bite-sized tasks.",
        *clarity_note,
    ]

    actions = [
        "1. Review the investigation report from step 6.",
        "2. Use the `superpowers:writing-plans` skill to create a structured plan:",
        "   - Break the fix into bite-sized tasks with exact file paths",
        "   - Include code examples where helpful",
        "   - Include test steps for each task",
        "   - Scope to minimal fix — no extra refactoring or 'while we're here' changes",
        "",
        f"3. Write the plan to `{plan_path}`",
        "",
        "4. The plan should include:",
        "   - Problem statement (from investigation)",
        "   - Solution approach (from RCA)",
        "   - Task breakdown (numbered, with file paths)",
        "   - Test strategy",
        "   - Risks and mitigation",
        "",
        "5. **Assess complexity** based on the plan you just wrote:",
        "   - **small**: ≤3 files, single concern, straightforward fix, no architectural changes",
        "   - **medium**: 4-10 files, multiple concerns, or subtle logic changes",
        "   - **large**: 10+ files, architectural changes, cross-cutting concerns",
        f"   Write to `{os.path.join(output_dir, 'complexity.json') if output_dir else 'complexity.json'}`:",
        '   `{"complexity": "small|medium|large", "reason": "brief justification"}`',
        "   This determines whether the iterative review loop runs at step 12.",
    ]

    return {
        "phase": "IMPLEMENTATION",
        "title": "Write Plan",
        "situation": situation,
        "actions": actions,
        "handoff": [
            f"`{plan_path}` exists with a complete, actionable implementation plan",
        ],
    }


# ---------------------------------------------------------------------------
# Step 10: Implement (fix mode only)
# ---------------------------------------------------------------------------

def _step_10_implement(mode, state, context, config, output_dir):
    """Step 10: Implement — execute the implementation plan."""
    issue_id = context.get("issue_id", "unknown")

    situation = [
        f"Execute the implementation plan for **{issue_id}**.",
        "Use `subagent-driven-development` to dispatch independent tasks in parallel.",
    ]

    actions = [
        "1. Read the implementation plan from step 9.",
        "2. Use the `superpowers:subagent-driven-development` skill to execute:",
        "   - Dispatch a fresh subagent per independent task",
        "   - Each subagent gets the plan task + investigation context",
        "   - Two-stage review after each task: spec compliance + code quality",
        "",
        "3. For sequential tasks, execute them in order.",
        "",
        "4. After all tasks complete:",
        "   - Verify all planned changes are in place",
        "   - Run a quick sanity check (`git diff --stat`)",
        "",
        "**Scope discipline:** Only implement what's in the plan. If you discover",
        "additional work needed, note it but don't expand scope.",
    ]

    return {
        "phase": "IMPLEMENTATION",
        "title": "Implement",
        "situation": situation,
        "actions": actions,
        "handoff": [
            "All planned tasks implemented",
            "Changes verified with `git diff --stat`",
        ],
    }


# ---------------------------------------------------------------------------
# Step 11: Verify (fix mode only)
# ---------------------------------------------------------------------------

def _step_11_verify(mode, state, context, config, output_dir):
    """Step 11: Verify — run tests, build, lint."""

    complexity_path = os.path.join(output_dir, "complexity.json") if output_dir else "complexity.json"

    situation = [
        "Verify the implementation, then decide whether an independent code review is needed.",
    ]

    actions = [
        "1. Use the `superpowers:verification-before-completion` skill:",
        "   - Run the project's test suite",
        "   - Run the build (if applicable)",
        "   - Run the linter (if applicable)",
        "",
        "2. If tests fail:",
        "   - Fix the failing tests",
        "   - Re-run verification",
        "   - If stuck after 2 attempts, note the failure and continue",
        "",
        "3. Record verification results for the pipeline result.",
        "",
        "4. **Decide whether to run the iterative review loop (steps 12-13):**",
        f"   Read `{complexity_path}` (written at step 9).",
        "   - **small complexity** → Skip steps 12-13, proceed directly to step 14.",
        "     The `superpowers:code-reviewer` from subagent-driven-development (step 10)",
        "     already validated each task — a multi-round independent review adds cost",
        "     without proportional value for small, single-concern changes.",
        "   - **medium or large complexity** → Continue to step 12 for iterative",
        "     independent review. Multi-file changes with subtle interactions benefit",
        "     from a fresh perspective that the per-task code-reviewer cannot provide.",
        "   - **complexity.json missing** → Treat as medium (err toward more review).",
    ]

    return {
        "phase": "IMPLEMENTATION",
        "title": "Verify",
        "situation": situation,
        "actions": actions,
        "handoff": [
            "Tests pass (or failures documented as degradation)",
            "Build succeeds (or N/A)",
            "Lint clean (or N/A)",
            "Complexity-based routing decision: step 12 (iterative code review) or step 14 (draft PR)",
        ],
    }


# ---------------------------------------------------------------------------
# Step 12: Self-Review (fix mode only)
# ---------------------------------------------------------------------------

def _step_12_self_review(mode, state, context, config, output_dir):
    """Step 12: Iterative Review — multi-round independent code review loop."""

    scripts_dir = Path(__file__).resolve().parent
    issue_id = context.get("issue_id", "unknown")

    situation = [
        _PHASE_TRANSITIONS["VALIDATION"],
        "",
        "Implementation is complete and verified. Starting the iterative",
        "review loop — an independent automated review with multi-round",
        "pushback tracking and convergence detection.",
    ]

    code_review_dir = os.path.join(output_dir, 'code-review')

    actions = [
        "1. Ensure all implementation changes are committed (the review tool only sees committed changes):",
        "   - Run `git status` to check for uncommitted work",
        "   - If there are staged/unstaged changes, commit them with semantic commit messages",
        "   - Do NOT create blanket WIP commits — each commit should be a logical unit",
        "   - The review loop warns about uncommitted changes but does not auto-commit",
        "",
        "2. Compute the merge base (detect default branch dynamically):",
        "   ```bash",
        "   BASE_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')",
        "   if [ -z \"$BASE_BRANCH\" ]; then",
        "     if git show-ref --verify --quiet refs/remotes/origin/main 2>/dev/null; then",
        "       BASE_BRANCH=main",
        "     elif git show-ref --verify --quiet refs/remotes/origin/trunk 2>/dev/null; then",
        "       BASE_BRANCH=trunk",
        "     elif git show-ref --verify --quiet refs/remotes/origin/develop 2>/dev/null; then",
        "       BASE_BRANCH=develop",
        "     else",
        "       echo 'ERROR: Cannot detect default branch. Set origin/HEAD or pass --merge-base manually.' >&2",
        "       exit 1",
        "     fi",
        "   fi",
        "   MERGE_BASE=$(git merge-base \"origin/$BASE_BRANCH\" HEAD)",
        "   ```",
        "",
        "3. Start the review loop with round 1:",
        f"   ```bash",
        f"   PYTHONPATH={scripts_dir}:$PYTHONPATH python3 -m iterative_review --action review --round 1 \\",
        f"     --output-dir {code_review_dir} \\",
        f"     --merge-base $MERGE_BASE \\",
        f"     --context-file {os.path.join(output_dir, 'investigation-report.md')} \\",
        f"     --analysis-prefix {issue_id.lower()}",
        f"   ```",
        f"   (Run from the target repo root — PYTHONPATH makes the module importable.)",
        "",
        f"   Tail `{os.path.join(code_review_dir, 'review-progress.jsonl')}` for status.",
        "",
        "4. If the script prints `UNAVAILABLE` — the review tool is not installed or not",
        "   authenticated. The iterative review cannot run. Note this as a degradation",
        "   and skip to step 14 (draft PR). Include in the PR description that the",
        "   iterative code review was skipped.",
        "",
        "5. When the review completes, follow the evaluation briefing.",
        "",
        "6. After writing outcomes, advance (replace N with the current round number):",
        f"   ```bash",
        f"   PYTHONPATH={scripts_dir}:$PYTHONPATH python3 -m iterative_review --action advance --round N \\",
        f"     --output-dir {code_review_dir}",
        f"   ```",
        "",
        "7. If advance says 'Proceed to review round M', run the next review:",
        f"   ```bash",
        f"   PYTHONPATH={scripts_dir}:$PYTHONPATH python3 -m iterative_review --action review --round M \\",
        f"     --output-dir {code_review_dir}",
        f"   ```",
        "   Then repeat from step 5. Only round 1 needs --merge-base and --context-file.",
        "",
        "8. When advance returns 'loop complete', proceed to step 13.",
    ]

    return {
        "phase": "VALIDATION",
        "title": "Iterative Review",
        "situation": situation,
        "actions": actions,
        "handoff": [
            f"`{os.path.join(output_dir, 'code-review', 'review-loop-result.json')}` exists with final review stats",
        ],
    }


# ---------------------------------------------------------------------------
# Step 13: Re-Verify (fix mode only)
# ---------------------------------------------------------------------------

def _step_13_reverify(mode, state, context, config, output_dir):
    """Step 13: Re-Verify — skipped when iterative review loop handles verification."""

    situation = [
        "Verification is already handled within each review round of the iterative",
        "review loop (step 12). Each round includes test/build/lint verification",
        "after fixes, so a separate re-verification step is redundant.",
    ]

    actions = [
        "Proceed directly to step 14.",
    ]

    return {
        "phase": "VALIDATION",
        "title": "Re-Verify (Handled by Review Loop)",
        "situation": situation,
        "actions": actions,
        "handoff": None,
    }


# ---------------------------------------------------------------------------
# Step 14: Create Draft PR (fix mode only)
# ---------------------------------------------------------------------------

def _step_14_create_draft_pr(mode, state, context, config, output_dir):
    """Step 14: Create Draft PR — open a draft PR on GitHub."""
    issue_id = context.get("issue_id", "unknown")

    situation = [
        f"Create a draft PR for the **{issue_id}** fix.",
        "The PR links back to the Linear issue and includes investigation context.",
    ]

    review_result_path = os.path.join(output_dir, "code-review", "review-loop-result.json")

    actions = [
        "1. Create a feature branch (if not already on one):",
        f"   ```bash",
        f"   git checkout -b fix/{issue_id.lower()}",
        f"   ```",
        "",
        "2. Stage and commit changes:",
        f"   - Use conventional commit: `fix: <description>`",
        f"   - Include `Refs {issue_id}` in the commit body",
        "",
        "3. Check for deferred review items (if the iterative review ran):",
        f"   - If `{review_result_path}` exists, read the `deferred_items` array",
        "   - This list is pre-pruned, but cross-round matching is approximate (by title+location)",
        "   - Before adding to the PR, deduplicate: if a deferred item describes the same issue",
        "     as something you fixed in a later round (even with different wording or shifted lines),",
        "     drop it — the fix already addresses it",
        "   - If any remain, include them in the PR description under a `## Follow-ups` section",
        "   - Each item has severity, title, location, and reasoning",
        "   - If the file doesn't exist (small complexity, step 12 skipped), skip this step",
        "",
        "4. Push and create draft PR:",
        "   ```bash",
        "   git push -u origin HEAD",
        f"   gh pr create --draft --title 'fix: <description>' --body-file <pr-body.md>",
        "   ```",
        f"   Include in the PR body: Summary, `Refs {issue_id}`, and if deferred items exist,",
        "   a Follow-ups section listing each deferred finding (severity, title, location, reason).",
        "",
        "5. If PR creation fails:",
        "   - Save the diff locally (`git diff > changes.diff`)",
        "   - Note as degradation with manual instructions",
        "   - Continue to step 15",
        "",
        "6. Record the PR URL for the pipeline result.",
    ]

    return {
        "phase": "OUTPUT",
        "title": "Create Draft PR",
        "situation": situation,
        "actions": actions,
        "handoff": [
            "Draft PR created (or failure noted as degradation with saved diff)",
            "PR URL recorded for pipeline result",
        ],
    }


# ---------------------------------------------------------------------------
# Step 15: Present Results
# ---------------------------------------------------------------------------

def _step_15_present_results(mode, state, context, config, output_dir):
    """Step 15: Present Results — write pipeline-result.json."""
    result_path = os.path.join(output_dir, "pipeline-result.json") if output_dir else "pipeline-result.json"
    report_path = os.path.join(output_dir, "investigation-report.md") if output_dir else "investigation-report.md"

    situation = [
        _PHASE_TRANSITIONS["OUTPUT"],
        "",
        "Write the final pipeline result and signal completion.",
    ]

    actions = [
        f"1. Write `{result_path}` with this structure:",
        "   ```json",
        "   {",
        '     "status": "<success | degraded | failed | blocked>",',
        f'     "mode": "{mode}",',
        f'     "issue_id": "<issue ID>",',
        f'     "report_path": "{report_path}",',
        '     "verdict": "<valid | invalid | duplicate | already_fixed | needs_more_info | needs_clarification>",',
        '     "pr_url": "<GitHub PR URL or null>",',
        '     "linear_comment_posted": true,',
        '     "independent_code_review": "<not_run | unavailable | clean | converged | max_rounds | hard_limit>",',
        '     "clarity_gate": "<object with clear_enough, hard_gates_failed, soft_signals_flagged, assessment_path | null>",',
        '     "clarity_gate_overridden": false,',
        '     "degradation_notes": []',
        "   }",
        "   ```",
        "",
        "2. Set `status`:",
        "   - `success` — all steps completed without degradation",
        "   - `degraded` — completed but with noted failures (Linear post failed, codex unavailable, etc.)",
        "   - `failed` — critical step failed (could not fetch issue, investigation inconclusive)",
        "   - `blocked` — clarity gate blocked implementation (needs author clarification)",
        "",
        "3. Force-stop any remaining background agents with TaskStop.",
        "",
        f"4. Read `{result_path}` and output its contents as raw JSON.",
    ]

    return {
        "phase": "OUTPUT",
        "title": "Present Results",
        "situation": situation,
        "actions": actions,
        "handoff": [
            f"`{result_path}` written with complete pipeline results",
        ],
    }


# ---------------------------------------------------------------------------
# Shared Orchestration Helpers
# ---------------------------------------------------------------------------

def _write_failed_result(output_dir, mode, context, error, events=None):
    """Write a failed pipeline-result.json. Used for early bail-outs."""
    pipeline_result = {
        "status": "failed",
        "mode": mode,
        "issue_id": context.get("issue_id", "unknown"),
        "report_path": None,
        "verdict": None,
        "pr_url": None,
        "linear_comment_posted": False,
        "independent_code_review": "not_run",
        "degradation_notes": [error],
    }
    result_path = os.path.join(output_dir, "pipeline-result.json")
    try:
        with open(result_path, "w") as f:
            json.dump(pipeline_result, f, indent=2)
    except OSError:
        pass
    if events:
        events.pipeline_failed(step=1, error=error)


# ---------------------------------------------------------------------------
# Step Orchestration (side effects — file I/O, event emission)
# ---------------------------------------------------------------------------

def _orchestrate_step(step, mode, config, state, context, output_dir, events=None):
    """Run step-specific side effects (file I/O, event emission).

    Called by main() BEFORE get_step_guidance(). Mutates state in place.
    Returns the (possibly updated) context dict.
    """
    # Emit step_started event
    if events:
        step_def = _STEP_MAP.get(step, {})
        events.step_started(step=step, title=step_def.get("title", ""))

    if step == 1:
        # Repo sanity check — verify CWD matches the expected repo.
        matches, actual, expected = check_repo_match(context)
        if not matches:
            state["repo_mismatch"] = {
                "actual": actual,
                "expected": expected,
            }
            interactive = config.get("interactive", True)
            if not interactive:
                # Bot mode: write failed result immediately and signal stop.
                _write_failed_result(
                    output_dir, mode, context,
                    error=f"Repo mismatch: expected {expected}, got {actual}",
                    events=events,
                )

    # When step 8 is re-run (re-triggered after clarification), clear the
    # stale clarity_blocked flag so routing computes correctly — otherwise
    # get_active_steps() drops steps 9-14 and the step-8 output points
    # to step 15 instead of step 9. The assessment will be re-evaluated
    # when the next step (9 or 15) enters _orchestrate_step.
    if step == 8 and state.get("clarity_blocked"):
        del state["clarity_blocked"]
        if state.get("verdict") == "needs_clarification":
            del state["verdict"]
        # Reset events flag so the new assessment can emit fresh events
        state.pop("_clarity_events_emitted", None)

    # Clarity gate check: runs on the step AFTER step 8 (step 9 in fix mode,
    # step 15 in investigate mode). By this point the LLM has executed step 8's
    # briefing and written clarity-assessment.json. We check it here because
    # _orchestrate_step runs BEFORE get_step_guidance — so checking on step 8
    # itself would read the file before it exists.
    #
    # When resuming with skip_clarity_gate, clear the stale needs_clarification
    # verdict from the first (blocked) run so step 15 doesn't emit a contradictory
    # result like status: "success" + verdict: "needs_clarification".
    if config.get("skip_clarity_gate") and state.get("clarity_blocked") and state.get("verdict") == "needs_clarification":
        del state["verdict"]

    # Terminal verdicts: issue_resolved (already_fixed via merged PR) or any
    # verdict already in state that indicates no implementation should happen.
    _TERMINAL_VERDICTS = {"already_fixed", "duplicate", "invalid", "needs_more_info"}
    _terminal = state.get("issue_resolved", False) or state.get("verdict") in _TERMINAL_VERDICTS
    _step8_ran = 8 in state.get("completed_steps", [])
    _overriding = config.get("skip_clarity_gate", False)
    # Re-evaluate on every post-step-8 entry so a re-triggered step 8 can
    # unblock with a new passing assessment. Skip when: terminal verdict or
    # override active. Use _clarity_events_emitted to avoid duplicate
    # milestone/deliverable events when _orchestrate_step is called multiple
    # times in the same run (e.g., early-exit block calls step 15 directly).
    _skip_events = state.get("_clarity_events_emitted", False)
    if step > 8 and _step8_ran and not _terminal and not _overriding:
        assessment_path = os.path.join(output_dir, "clarity-assessment.json")
        if os.path.isfile(assessment_path):
            try:
                with open(assessment_path) as f:
                    assessment = json.load(f)
                # Schema validation: required keys must exist. An LLM-written
                # file may omit clear_enough or hard_gates. Treat missing
                # required keys as malformed (fail closed).
                if "clear_enough" not in assessment or "hard_gates" not in assessment:
                    raise ValueError("Missing required keys: clear_enough, hard_gates")
                # Validate nested types: hard_gates and soft_signals values
                # must be dicts. LLM can write e.g. {"problem_statement": false}
                # instead of {"problem_statement": {"pass": false, "note": "..."}}.
                for section_key in ("hard_gates", "soft_signals"):
                    section = assessment.get(section_key, {})
                    if not isinstance(section, dict):
                        raise ValueError(f"{section_key} must be an object")
                    for k, v in section.items():
                        if not isinstance(v, dict):
                            raise ValueError(f"{section_key}.{k} must be an object, got {type(v).__name__}")
                if not assessment["clear_enough"]:
                    state["clarity_blocked"] = True
                    # Always set verdict to needs_clarification when blocking.
                    # Terminal verdicts (already_fixed, duplicate, etc.) are
                    # excluded by the _terminal guard above, so any verdict
                    # here is non-terminal (e.g., "valid") and should be
                    # overwritten to match the blocked status.
                    state["verdict"] = "needs_clarification"
                    if events and not _skip_events:
                        hard_failed = [k for k, v in assessment["hard_gates"].items()
                                       if not v.get("pass", True)]
                        events.milestone(
                            name="clarity_assessed",
                            step=8,
                            summary=f"blocked: {len(hard_failed)} hard gate(s) failed",
                        )
                        events.deliverable(
                            type_="clarity_assessment",
                            path="clarity-assessment.json",
                        )
                    state["_clarity_events_emitted"] = True
                else:
                    # Assessment passed — clear any prior block from a previous run
                    if state.get("clarity_blocked"):
                        del state["clarity_blocked"]
                    if state.get("verdict") == "needs_clarification":
                        del state["verdict"]
                    if events and not _skip_events:
                        events.milestone(
                            name="clarity_assessed",
                            step=8,
                            summary="passed",
                        )
                    state["_clarity_events_emitted"] = True
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                # Fail closed: malformed JSON, missing schema keys, or wrong
                # nested types = block. LLM-written files have realistic type drift.
                state["clarity_blocked"] = True
                state["verdict"] = "needs_clarification"
                state.setdefault("degradation_notes", []).append(
                    f"Clarity assessment file was invalid — blocking as precaution ({exc})"
                )
                if events and not _skip_events:
                    events.milestone(name="clarity_assessed", step=8, summary="blocked (file invalid)")
                state["_clarity_events_emitted"] = True
        else:
            # Fail closed: missing file = block.
            state["clarity_blocked"] = True
            state["verdict"] = "needs_clarification"
            state.setdefault("degradation_notes", []).append(
                "Clarity assessment file missing — blocking as precaution"
            )
            if events and not _skip_events:
                events.milestone(name="clarity_assessed", step=8, summary="blocked (file missing)")
            state["_clarity_events_emitted"] = True

    if step == 15:
        # Write pipeline-result.json from state, deriving status from real outputs.
        report_path = os.path.join(output_dir, "investigation-report.md")
        degradation_notes = list(state.get("degradation_notes", []))

        verdict = state.get("verdict")
        has_report = os.path.isfile(report_path)
        pr_url = state.get("pr_url")
        linear_posted = state.get("linear_comment_posted", False)

        # Check for clarity gate block
        clarity_blocked = state.get("clarity_blocked", False)
        clarity_overridden = config.get("skip_clarity_gate", False)

        # Read iterative review result — surface the outcome, not just whether it ran.
        # Values: "not_run" (skipped/small complexity), "unavailable" (tool missing),
        # "clean" (zero_findings), "converged" (all_rejected/nitpicks_only),
        # "max_rounds" (hit round limit), "hard_limit" (hit absolute ceiling).
        _REVIEW_OUTCOME_MAP = {
            "zero_findings": "clean",
            "all_rejected": "converged",
            "nitpicks_only": "converged",
            "max_rounds": "max_rounds",
            "hard_limit": "hard_limit",
            "codex_unavailable": "unavailable",
        }
        review_result_path = os.path.join(output_dir, "code-review", "review-loop-result.json")
        review_outcome = "not_run"
        if os.path.isfile(review_result_path):
            try:
                with open(review_result_path) as _rf:
                    _rdata = json.load(_rf)
                termination = _rdata.get("termination", "")
                review_outcome = _REVIEW_OUTCOME_MAP.get(termination, termination or "not_run")
            except (json.JSONDecodeError, OSError):
                pass

        # Mark unavailable review as degradation so status reflects reality
        if review_outcome == "unavailable":
            note = "Independent code review skipped — review tool unavailable"
            if note not in degradation_notes:
                degradation_notes.append(note)

        # Derive status from what actually exists, not from absence of errors.
        # A run that never produced a verdict or report is failed, not successful.
        # Clarity gate block: "blocked" in both modes so the bot surfaces the
        # assessment questions to Slack. The bot decides whether to offer the
        # "proceed anyway" override based on mode (fix only, not investigate).
        if clarity_blocked and not clarity_overridden:
            status = "blocked"
            if not verdict:
                verdict = "needs_clarification"
        elif not verdict and not has_report:
            status = "failed"
            if "No verdict or investigation report produced" not in degradation_notes:
                degradation_notes.append("No verdict or investigation report produced")
        elif degradation_notes:
            status = "degraded"
        elif mode == "fix" and not state.get("issue_resolved", False) and not pr_url:
            # Fix mode should produce a PR URL unless the issue was already resolved
            status = "degraded"
            if "Fix mode completed without creating a draft PR" not in degradation_notes:
                degradation_notes.append("Fix mode completed without creating a draft PR")
        else:
            status = "success"

        # Build clarity_gate summary from assessment file.
        # When the run is blocked, the summary must reflect the block — not
        # default to clear_enough: true from a malformed file.
        clarity_gate_summary = None
        assessment_path = os.path.join(output_dir, "clarity-assessment.json")
        if os.path.isfile(assessment_path):
            try:
                with open(assessment_path) as _cf:
                    _cdata = json.load(_cf)
                # Safely extract gate/signal names, tolerating nested type drift
                _hg = _cdata.get("hard_gates", {})
                hard_failed = [k for k, v in (_hg.items() if isinstance(_hg, dict) else [])
                               if isinstance(v, dict) and not v.get("pass", True)]
                _ss = _cdata.get("soft_signals", {})
                soft_flagged = [k for k, v in (_ss.items() if isinstance(_ss, dict) else [])
                                if isinstance(v, dict) and v.get("flagged", False)]
                # Use the blocked state as ground truth — if the gate blocked
                # but the file is malformed (missing clear_enough), don't
                # default to True and report a passing summary.
                _clear = _cdata.get("clear_enough")
                if _clear is None:
                    _clear = not clarity_blocked
                clarity_gate_summary = {
                    "clear_enough": _clear,
                    "hard_gates_failed": hard_failed,
                    "soft_signals_flagged": soft_flagged,
                    "assessment_path": "clarity-assessment.json",
                }
            except (json.JSONDecodeError, OSError):
                # File unreadable — if blocked, surface that in the summary
                if clarity_blocked:
                    clarity_gate_summary = {
                        "clear_enough": False,
                        "hard_gates_failed": [],
                        "soft_signals_flagged": [],
                        "assessment_path": "clarity-assessment.json",
                        "error": "assessment file unreadable",
                    }
        elif clarity_blocked:
            # File missing but gate blocked (fail-closed) — surface in summary
            clarity_gate_summary = {
                "clear_enough": False,
                "hard_gates_failed": [],
                "soft_signals_flagged": [],
                "assessment_path": "clarity-assessment.json",
                "error": "assessment file missing",
            }

        pipeline_result = {
            "status": status,
            "mode": mode,
            "issue_id": context.get("issue_id", "unknown"),
            "report_path": report_path if has_report else None,
            "verdict": verdict,
            "pr_url": pr_url,
            "linear_comment_posted": linear_posted,
            "independent_code_review": review_outcome,
            "clarity_gate": clarity_gate_summary,
            "clarity_gate_overridden": clarity_overridden and clarity_blocked,
            "degradation_notes": degradation_notes,
        }

        result_path = os.path.join(output_dir, "pipeline-result.json")
        try:
            with open(result_path, "w") as f:
                json.dump(pipeline_result, f, indent=2)
        except OSError:
            pass

        # Emit pipeline_complete event
        if events:
            events.pipeline_complete(status=status, mode=mode)

    # Emit step_completed event
    if events:
        step_def = _STEP_MAP.get(step, {})
        events.step_completed(step=step, title=step_def.get("title", ""))

    return context


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Linear issue investigation and fix pipeline")
    parser.add_argument("--step", type=int, required=True, help="Step number (1-15)")
    parser.add_argument("--mode", choices=["investigate", "fix"],
                        help="Pipeline mode")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--issue-id", help="Linear issue ID (e.g., WOOPLUG-1234)")

    args = parser.parse_args()
    output_dir = args.output_dir
    step = args.step

    # Ensure output dir exists
    os.makedirs(output_dir, exist_ok=True)

    # Initialize event emitter (best-effort)
    events = _init_events(output_dir)

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
            config = {"mode": mode, "interactive": False}
            if args.issue_id:
                config["issue_id"] = args.issue_id
            write_config(output_dir, config)
        else:
            config = existing_config

        # Initialize fresh pipeline state
        state = json.loads(json.dumps(_DEFAULT_STATE))
        now = datetime.now(timezone.utc)
        state["run_id"] = f"{now.strftime('%Y%m%dT%H%M%S')}-{mode}-{args.issue_id or 'unknown'}"

        write_state(output_dir, state)

    else:
        # Steps 2+: read existing config and state
        config = read_config(output_dir)
        mode = config.get("mode") or args.mode
        if not mode:
            print("ERROR: No mode found in run-config.json and --mode not provided",
                  file=sys.stderr)
            sys.exit(2)

        state = read_state(output_dir)

    # Validate step number
    if step not in _STEP_MAP:
        print(f"ERROR: Invalid step {step}. Valid steps: 1-{len(STEP_SEQUENCE)}", file=sys.stderr)
        sys.exit(1)

    # --- Read issue context if available ---
    context = read_issue_context(output_dir)

    # --- Step-specific orchestration ---
    context = _orchestrate_step(step, mode, config, state, context, output_dir, events)

    # --- Update state ---
    if step not in state.get("completed_steps", []):
        state.setdefault("completed_steps", []).append(step)
    write_state(output_dir, state)

    # --- Early exit: repo mismatch ---
    if state.get("repo_mismatch"):
        guidance = get_step_guidance(step, mode, state, context, config=config,
                                    output_dir=output_dir)
        guidance["next_step"] = None
        guidance["skip_reason"] = None
        output = format_output(step, guidance)
        print(output)
        if not config.get("interactive", True):
            # Bot mode: pipeline-result.json already written by orchestration
            sys.exit(1)
        return

    # --- Early exit: clarity gate blocked an implementation step ---
    # If the clarity check just set clarity_blocked and we're on an implementation
    # step (9-14), skip directly to step 15's full orchestration (write result,
    # emit events, print PIPELINE COMPLETE). This avoids rendering the Write Plan
    # briefing and avoids the bot prompt's "retry BLOCKED steps" loop — we
    # complete the pipeline right here instead of redirecting.
    if state.get("clarity_blocked") and not config.get("skip_clarity_gate") and 9 <= step <= 14:
        # Run step 15 orchestration directly — writes pipeline-result.json
        _orchestrate_step(15, mode, config, state, context, output_dir, events)
        if 15 not in state.get("completed_steps", []):
            state.setdefault("completed_steps", []).append(15)
        # Mark skipped implementation steps
        for s in range(step, 15):
            if s not in state.get("skipped_steps", []):
                state.setdefault("skipped_steps", []).append(s)
        write_state(output_dir, state)
        # Render step 15 guidance (with PIPELINE COMPLETE)
        active = get_active_steps(mode, config, state, context)
        guidance = get_step_guidance(15, mode, state, context, config=config,
                                    output_dir=output_dir)
        guidance["next_step"] = None  # Last step
        skipped_titles = [f"Step {s} ({_STEP_MAP[s]['title']})" for s in range(step, 15) if s in _STEP_MAP]
        guidance["skip_reason"] = f"Skipped (clarity gate blocked): {', '.join(skipped_titles)}" if skipped_titles else None
        output = format_output(15, guidance)
        print(output)
        return

    # --- Compute routing AFTER orchestration (state may have changed) ---
    active = get_active_steps(mode, config, state, context)

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

    # --- Format and output ---
    output = format_output(step, guidance)
    print(output)


if __name__ == "__main__":
    main()

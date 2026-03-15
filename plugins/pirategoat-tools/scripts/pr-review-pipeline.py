#!/usr/bin/env python3
"""
PR Review Pipeline — unified step-injection script.

Provides step-by-step guidance for the full PR review pipeline.
Mode-aware: detects bot mode from review-context.json, headless mode
from --headless flag.

15 steps (0-14) covering:
  SETUP      (0-2): Parse PR, repo setup, context discovery
  AWARENESS  (3-4): PR review state, decide approach
  CONTEXT    (5-7): Linked issue, fetch context, summarize
  EXECUTION  (8-11): Size assessment, ground truth, dispatch, agents
  VALIDATION (12): Ingest code review
  OUTPUT     (13-14): Report, critic, present, cleanup

Usage:
    python3 pr-review-pipeline.py --step-number 0 --total-steps 14 \
        --pr-number 42 --output-dir /tmp/pr-review-org-repo-42 \
        --headless --thoughts ""
"""

import argparse
import json
import os
import sys


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Absolute path to the scripts/ directory (derived from this file's location).
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Context loading
# ---------------------------------------------------------------------------

def load_context_values(output_dir, pr_number=None):
    """Load review-context.json and extract values for step interpolation."""
    ctx = {}
    ctx_path = os.path.join(output_dir, "review-context.json")
    if os.path.isfile(ctx_path):
        with open(ctx_path) as f:
            ctx = json.load(f)

    git = ctx.get("git", {})
    pr = ctx.get("pr", {})

    return {
        "output_dir": output_dir,
        "ctx_path": ctx_path,
        "scripts_dir": SCRIPTS_DIR,
        "pr_number": pr_number or str(pr.get("number", "")),
        "gh_cmd": ctx.get("github_cli_command", "gh"),
        "git_range": git.get("git_range", ""),
        "merge_base": git.get("merge_base", ""),
        "head_ref": git.get("head_ref", ""),
        "base_ref": git.get("base_ref", ""),
        "changed_files_csv": git.get("changed_files_csv", ""),
        "pr_title": pr.get("title", ""),
        "pr_author": pr.get("author", ""),
        "pr_body": pr.get("body", ""),
        "pr_url": pr.get("url", ""),
        "review_report": os.path.join(output_dir, "review-report.md"),
        "ground_truth": os.path.join(output_dir, "ground-truth-summary.json"),
        "dispatch_plan": os.path.join(output_dir, "dispatch-plan.json"),
        "agent_timeout": ctx.get("review", {}).get("agent_timeout_seconds", 1200),
        "bot_mode": ctx.get("source") == "pirategoat-bot",
        "has_context": bool(git.get("merge_base")),
    }


# ---------------------------------------------------------------------------
# Step guidance
# ---------------------------------------------------------------------------

STATE_REQ = (
    "CONTEXT REQUIREMENT: Your --thoughts must include: "
    "ORIGINAL_BRANCH (if repo setup ran), STASHED (bool), STASH_REF (if stashed), "
    "verdict (once determined), and any failure notes. "
    "Everything else is in review-context.json or the output directory."
)


def get_step_guidance(step, total_steps, vals, headless, thoughts):
    """Return guidance dict for a given step.

    Args:
        step: Current step number (0-14).
        total_steps: Total step count (14).
        vals: Dict from load_context_values().
        headless: Bool — auto-select decision points.
        thoughts: Accumulated --thoughts string.
    """
    od = vals["output_dir"]
    pr = vals["pr_number"]
    gh = vals["gh_cmd"]
    gr = vals["git_range"]
    sd = vals["scripts_dir"]

    # Step 0: Parse PR Number
    if step == 0:
        if pr:
            return {
                "phase": "SETUP",
                "title": "Parse PR Number",
                "actions": [
                    f"PR number: {pr}",
                    f"Output directory: {od}",
                    "",
                    "PR number confirmed. Proceeding to repo setup.",
                ],
                "next": "Step 1: Repo Setup",
            }
        else:
            return {
                "phase": "SETUP",
                "title": "Parse PR Number",
                "actions": [
                    "Usage: /pr-review <PR_URL_or_number>",
                    "",
                    "No PR number provided. A PR number or URL is required.",
                ],
                "next": None,
            }

    # Step 1: Repo Setup
    if step == 1:
        if vals["bot_mode"]:
            return {
                "phase": "SETUP",
                "title": "Repo Setup",
                "actions": [
                    "You are a thorough PR reviewer. Running the full review pipeline.",
                    "",
                    "review-context.json exists with pre-computed git context (bot mode).",
                    "Skip repo setup — context was pre-computed by the bot.",
                    "",
                    STATE_REQ,
                ],
                "next": "Step 2: Context Discovery",
            }
        elif headless:
            return {
                "phase": "SETUP",
                "title": "Repo Setup",
                "actions": [
                    "You are a thorough PR reviewer. Running the full review pipeline.",
                    "",
                    "Headless mode — auto-stash any uncommitted changes.",
                    "",
                    "1. Run `git status` to check for uncommitted changes",
                    "2. If dirty: `git stash push -u -m 'pr-review-auto-stash'`",
                    "   Record STASHED=true and STASH_REF (from `git stash list -1 --format=%H`) in --thoughts",
                    "3. Record ORIGINAL_BRANCH from `git branch --show-current`",
                    f"4. Checkout the PR branch: `{gh} pr checkout {pr}`",
                    "",
                    STATE_REQ,
                ],
                "next": "Step 2: Context Discovery",
            }
        else:
            return {
                "phase": "SETUP",
                "title": "Repo Setup",
                "actions": [
                    "You are a thorough PR reviewer. Running the full review pipeline.",
                    "",
                    "1. Run `git status` to check for uncommitted changes",
                    "2. If dirty: Ask user whether to stash or abort",
                    "   Use AskUserQuestion: 'You have uncommitted changes. Stash them?'",
                    "   If yes: `git stash push -u -m 'pr-review-stash'`",
                    "   Record STASHED=true and STASH_REF (from `git stash list -1 --format=%H`) in --thoughts",
                    "3. Record ORIGINAL_BRANCH from `git branch --show-current`",
                    f"4. Checkout the PR branch: `{gh} pr checkout {pr}`",
                    "",
                    STATE_REQ,
                ],
                "next": "Step 2: Context Discovery",
            }

    # Step 2: Context Discovery
    if step == 2:
        if vals["bot_mode"]:
            return {
                "phase": "SETUP",
                "title": "Context Discovery",
                "actions": [
                    "You are a thorough PR reviewer.",
                    "",
                    f"review-context.json already exists at {vals['ctx_path']} (bot mode).",
                    "Context was pre-computed by the bot. Read values from the file:",
                    f"  GIT_RANGE: {gr}",
                    f"  PR_NUMBER: {pr}",
                    f"  GH_CMD: {gh}",
                    "",
                    STATE_REQ,
                ],
                "next": "Step 3: PR Review State",
            }
        else:
            return {
                "phase": "SETUP",
                "title": "Context Discovery",
                "actions": [
                    "You are a thorough PR reviewer.",
                    "",
                    "Delete any existing review-context.json to ensure fresh context:",
                    f"    rm -f \"{vals['ctx_path']}\"",
                    "",
                    "Run gather-review-context.py to compute git context and PR metadata:",
                    "",
                    f"    python3 {sd}/gather-review-context.py \\",
                    f"      --pr-number \"{pr}\" \\",
                    f"      --output-dir \"{od}\"",
                    "",
                    "Read the resulting review-context.json for GIT_RANGE, MERGE_BASE, etc.",
                    "",
                    STATE_REQ,
                ],
                "next": "Step 3: PR Review State",
            }

    # Step 3: PR Review State
    if step == 3:
        return {
            "phase": "AWARENESS",
            "title": "PR Review State",
            "actions": [
                "You are a thorough PR reviewer. Analyze the current review state.",
                "",
                f"Run: `{gh} pr view {pr} --json reviews,reviewRequests`",
                "",
                "Summarize:",
                "- How many reviews exist? Approved/Changes Requested/Commented?",
                "- Are there pending review requests?",
                "- Is this a re-review or first review?",
                "",
                STATE_REQ,
            ],
            "next": "Step 4: Decide Approach",
        }

    # Step 4: Decide Approach
    if step == 4:
        if headless:
            return {
                "phase": "AWARENESS",
                "title": "Decide Approach",
                "actions": [
                    "You are a thorough PR reviewer.",
                    "",
                    "Headless mode — auto-selecting: Full review.",
                    "",
                    "Proceeding with full multi-agent review pipeline.",
                    "",
                    STATE_REQ,
                ],
                "next": "Step 5: Extract Linked Issue",
            }
        else:
            return {
                "phase": "AWARENESS",
                "title": "Decide Approach",
                "actions": [
                    "You are a thorough PR reviewer.",
                    "",
                    "Use AskUserQuestion to ask the user:",
                    "  'Based on the PR review state, how would you like to proceed?'",
                    "Options:",
                    "  1. Full review (recommended) — dispatch all relevant agents",
                    "  2. Quick review — PR reviewer only",
                    "  3. Abort — stop the review",
                    "",
                    "If 'Abort': STOP. Restore branch/stash if touched.",
                    "",
                    STATE_REQ,
                ],
                "next": "Step 5: Extract Linked Issue",
            }

    # Step 5: Extract Linked Issue
    if step == 5:
        body_preview = vals["pr_body"][:200] if vals["pr_body"] else "(no body)"
        return {
            "phase": "CONTEXT",
            "title": "Extract Linked Issue",
            "actions": [
                "You are a thorough PR reviewer. Check for linked issues.",
                "",
                f"PR body preview: {body_preview}",
                "",
                "Look for:",
                "- Linear issue IDs (e.g., WOOPLUG-1234, WOOPRD-56)",
                "- GitHub issue refs (Closes #99, Fixes #100)",
                "- Branch name patterns (fix/WOOPLUG-5988-desc → WOOPLUG-5988)",
                "",
                "Record any linked issue IDs for the next step.",
                "",
                STATE_REQ,
            ],
            "next": "Step 6: Fetch Issue Context",
        }

    # Step 6: Fetch Issue Context
    if step == 6:
        return {
            "phase": "CONTEXT",
            "title": "Fetch Issue Context",
            "actions": [
                "You are a thorough PR reviewer. Gather issue context if available.",
                "",
                "If linked issues were found in Step 5:",
                "  - For Linear IDs: use the Linear MCP server to fetch issue details",
                "  - For GitHub IDs: use `gh issue view <ID> --json title,body,labels`",
                "",
                "If no linked issues or fetch fails: skip — note 'no issue context'.",
                "",
                "Summarize: what problem is this PR solving? What are the acceptance criteria?",
                "",
                STATE_REQ,
            ],
            "next": "Step 7: Summarize Context",
        }

    # Step 7: Summarize Context
    if step == 7:
        return {
            "phase": "CONTEXT",
            "title": "Summarize Context",
            "actions": [
                "You are a thorough PR reviewer. Synthesize all gathered context.",
                "",
                "Write a brief context summary to --thoughts including:",
                "- PR purpose (from title, body, issue)",
                "- Key changes (from diff stats)",
                "- Review focus areas (from issue context, if any)",
                "",
                STATE_REQ,
            ],
            "next": "Step 8: Assess PR Size",
        }

    # Step 8: Assess PR Size
    if step == 8:
        return {
            "phase": "EXECUTION",
            "title": "Assess PR Size",
            "actions": [
                "You are a thorough PR reviewer. Assess the PR size.",
                "",
                f"Read size data from review-context.json at {vals['ctx_path']}:",
                "- pr_size.files — number of changed files",
                "- pr_size.lines — total changed lines",
                "- pr_size.category — size bucket (tiny/small/medium/large/huge/vlad-sized)",
                "",
                "Display the size assessment. For huge/vlad-sized PRs:",
                "  Note that the review will take longer and some agents may time out.",
                "",
                STATE_REQ,
            ],
            "next": "Step 9: Ground Truth Collection",
        }

    # Step 9: Ground Truth Collection
    if step == 9:
        return {
            "phase": "EXECUTION",
            "title": "Ground Truth Collection",
            "actions": [
                "You are a thorough PR reviewer. Collect ground truth signals (optional).",
                "",
                "Run available linters/type-checkers on the changed files.",
                "This is best-effort — if tools aren't available, skip.",
                "",
                f"If results collected, write to: {vals['ground_truth']}",
                "",
                STATE_REQ,
            ],
            "next": "Step 10: Generate Dispatch Plan",
        }

    # Step 10: Generate Dispatch Plan
    if step == 10:
        return {
            "phase": "EXECUTION",
            "title": "Generate Dispatch Plan + Triage",
            "actions": [
                "You are a thorough PR reviewer. Generate the dispatch plan.",
                "",
                "Run this command exactly:",
                "",
                f"    python3 {sd}/plan-review-dispatch.py \\",
                f"      --mode pr \\",
                f"      --git-range \"{gr}\" \\",
                f"      --output-dir \"{od}\"",
                "",
                "Parse the JSON output. Display which agents will be dispatched.",
                "",
                "For conditional agents with SKIPPED_TRIAGE:",
                "  Check triage criteria against commit messages and diffstat.",
                "  If criteria match, override to DISPATCH.",
                "",
                STATE_REQ,
            ],
            "next": "Step 11: Dispatch Agents + Reconcile",
        }

    # Step 11: Dispatch Agents + Reconcile
    if step == 11:
        gt_arg = ""
        if os.path.isfile(vals["ground_truth"]):
            gt_arg = f" --ground-truth \"{vals['ground_truth']}\""

        return {
            "phase": "EXECUTION",
            "title": "Dispatch Agents + Reconcile",
            "actions": [
                "You are a thorough PR reviewer. Dispatch all agents in parallel.",
                "",
                "CRITICAL: Dispatch ALL agents from the plan in a SINGLE message",
                "using the Agent tool. Each agent gets its own Agent tool call.",
                "",
                "For each DISPATCH agent, use:",
                "",
                "    Agent tool:",
                "      subagent_type: pirategoat-tools:<agent-name>",
                "      run_in_background: true",
                "      prompt: |",
                f"        python3 {sd}/bootstrap-reviewer.py \\",
                f"          --agent <agent-name> \\",
                f"          --range \"{gr}\" \\",
                f"          --output-dir \"{od}\"{gt_arg}",
                "",
                "After ALL agents complete, check status:",
                "",
                f"    python3 {sd}/check-reviewer-agent-status.py \\",
                f"      --output-dir \"{od}\"",
                "",
                "When ALL_DONE=true, reconcile:",
                "",
                f"    python3 {sd}/reconcile-reviews.py \\",
                f"      --output-dir \"{od}\" \\",
                f"      --dispatch-plan \"{vals['dispatch_plan']}\" \\",
                f"      --changed-files \"{vals['changed_files_csv']}\"",
                "",
                STATE_REQ,
            ],
            "next": "Step 12: Ingest Code Review",
        }

    # Step 12: Ingest Code Review
    if step == 12:
        return {
            "phase": "VALIDATION",
            "title": "Ingest Code Review",
            "actions": [
                "You are a thorough PR reviewer. Validate review findings against actual code.",
                "",
                "    Skill tool:",
                "      skill: pirategoat-tools:ingest-code-review",
                f"      args: {od} --git-range {gr}",
                "",
                STATE_REQ,
            ],
            "next": "Step 13: Generate Review Report",
        }

    # Step 13: Generate Review Report + Decision Critic
    if step == 13:
        return {
            "phase": "OUTPUT",
            "title": "Generate Review Report + Decision Critic",
            "actions": [
                "You are a thorough PR reviewer. Write the review report.",
                "",
                f"Write the review report to: {vals['review_report']}",
                "",
                "The report should include:",
                "- Executive summary (1-3 sentences)",
                "- Findings by severity (critical → low)",
                "- Recommendations",
                "- Overall verdict (APPROVE / REQUEST_CHANGES / COMMENT)",
                "",
                "After writing, dispatch the decision critic:",
                "",
                "    Agent tool:",
                "      subagent_type: pirategoat-tools:decision-reviewer",
                "      prompt: |",
                f"        Document Path: {vals['review_report']}",
                f"        Output Directory: {od}",
                f"        Ingestion Verification: {od}/ingest-verification.json",
                "",
                "Wait for the critic to finish. Read its findings:",
                f"    Read {od}/decision-critic-findings.md",
                "",
                "Check the critic's verdict (STAND / REVISE / ESCALATE):",
                "- STAND: No changes needed. Proceed to Step 14.",
                "- REVISE: Update the review report to address the critic's concerns,",
                f"  then rewrite {vals['review_report']} with the revisions.",
                "- ESCALATE: Flag for human review in Step 14.",
                "",
                STATE_REQ,
            ],
            "next": "Step 14: Present Results + Cleanup",
        }

    # Step 14: Present Results + Cleanup
    if step == 14:
        if vals["bot_mode"]:
            return {
                "phase": "OUTPUT",
                "title": "Present Results + Cleanup",
                "actions": [
                    "You are a thorough PR reviewer. Present the final results.",
                    "",
                    "Bot mode — skip branch restore and stash pop.",
                    "",
                    f"Read the review report from: {vals['review_report']}",
                    "",
                    "Present to user:",
                    "1. Overall verdict",
                    "2. Key findings summary",
                    f"3. Full report location: {vals['review_report']}",
                    f"4. Output directory: {od}",
                    "",
                    STATE_REQ,
                ],
                "next": None,
            }
        else:
            return {
                "phase": "OUTPUT",
                "title": "Present Results + Cleanup",
                "actions": [
                    "You are a thorough PR reviewer. Present results and restore workspace.",
                    "",
                    f"Read the review report from: {vals['review_report']}",
                    "",
                    "Present to user:",
                    "1. Overall verdict",
                    "2. Key findings summary",
                    f"3. Full report location: {vals['review_report']}",
                    f"4. Output directory: {od}",
                    "",
                    "Cleanup:",
                    "- If ORIGINAL_BRANCH in --thoughts: `git checkout <ORIGINAL_BRANCH>`",
                    "- If STASHED=true: `git stash apply <STASH_REF> && git stash drop <STASH_REF>`",
                    "",
                    STATE_REQ,
                ],
                "next": None,
            }

    return None


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_output(step, total_steps, guidance):
    """Format step guidance for display."""
    lines = []
    lines.append(f"═══ PR REVIEW Step {step}/{total_steps}: {guidance['title']} ({guidance['phase']}) ═══")
    lines.append("")
    for action in guidance["actions"]:
        lines.append(action)
    lines.append("")
    if guidance["next"]:
        lines.append(f"NEXT (MANDATORY): {guidance['next']} Do NOT stop — call pr-review-pipeline.py with --step-number {step + 1} immediately.")
    else:
        lines.append("PIPELINE COMPLETE — Present results to user.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="PR Review Pipeline — unified step-injection script.",
    )
    parser.add_argument("--step-number", type=int, required=True)
    parser.add_argument("--total-steps", type=int, required=True)
    parser.add_argument("--pr-number", type=str)
    parser.add_argument("--output-dir", type=str, required=True)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--thoughts", type=str, required=True)

    args = parser.parse_args()

    if args.step_number < 0 or args.step_number > args.total_steps:
        print(f"ERROR: Step {args.step_number} out of range (0-{args.total_steps})",
              file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    vals = load_context_values(args.output_dir, args.pr_number)

    guidance = get_step_guidance(
        args.step_number, args.total_steps,
        vals, args.headless, args.thoughts,
    )

    if guidance is None:
        print(f"ERROR: No guidance for step {args.step_number}", file=sys.stderr)
        sys.exit(1)

    print(format_output(args.step_number, args.total_steps, guidance))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
PR Review Pipeline — unified step-injection script.

Provides step-by-step guidance for the full PR review pipeline.
Always runs autonomously. Detects bot mode from review-context.json.

15 steps (0-14) covering:
  SETUP      (0-2):  Parse PR, repo setup, context discovery
  AWARENESS  (3):    Review context summary (reviews, linked issues, PR metadata)
  CONTEXT    (4-5):  Fetch issue context, summarize
  EXECUTION  (6-10): Size assessment, ground truth, dispatch, agents, reconcile+verify
  REVIEW     (11):   Generate review report
  VALIDATION (12):   Decision critic
  OUTPUT     (13-14): Present results, cleanup

Usage:
    python3 pr-review-pipeline.py --step-number 0 --total-steps 14 \
        --pr-number 42 --output-dir /tmp/pr-review-org-repo-42 \
        --thoughts ""
"""

import argparse
import importlib.util
import json
import os
import sys


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Absolute path to the scripts/ directory (derived from this file's location).
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# Import telemetry (sibling script, best-effort)
try:
    _telemetry_spec = importlib.util.spec_from_file_location(
        "review_telemetry", os.path.join(SCRIPTS_DIR, "review-telemetry.py")
    )
    _telemetry_mod = importlib.util.module_from_spec(_telemetry_spec)
    _telemetry_spec.loader.exec_module(_telemetry_mod)
    ReviewTelemetry = _telemetry_mod.ReviewTelemetry
except Exception:
    ReviewTelemetry = None


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

    # Reviews summary
    reviews = ctx.get("reviews", {})
    review_summary = reviews.get("summary", {})
    reviewers_list = reviews.get("reviewers", [])
    pending = reviews.get("pending", [])

    if review_summary.get("total", 0) > 0:
        parts = []
        for key in ("approved", "changes_requested", "commented"):
            count = review_summary.get(key, 0)
            if count > 0:
                parts.append(f"{count} {key.replace('_', ' ')}")
        reviewer_lines = []
        for r in reviewers_list:
            reviewer_lines.append(f"{r['login']} ({r['type']}, {r['state']})")
        pending_str = f"Pending: {', '.join(pending)}" if pending else "No pending requests"
        reviews_formatted = (
            f"Reviews: {', '.join(parts) if parts else 'none'}. "
            f"Reviewers: {'; '.join(reviewer_lines) if reviewer_lines else 'none'}. "
            f"{pending_str}. "
            f"Re-review."
        )
    else:
        reviews_formatted = "No existing reviews. First review."

    # Linked issues
    raw_issues = ctx.get("linked_issues", [])
    issues_formatted = ", ".join(raw_issues) if raw_issues else "None found"

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
        "reviews_summary": reviews_formatted,
        "linked_issues": issues_formatted,
    }


# ---------------------------------------------------------------------------
# Step guidance
# ---------------------------------------------------------------------------

STATE_REQ = (
    "CONTEXT REQUIREMENT: Your --thoughts must include: "
    "ORIGINAL_BRANCH (if repo setup ran), STASHED (bool), STASH_REF (if stashed), "
    "PR purpose (1-2 sentences), key changes, review focus areas, "
    "verdict (once determined), and any failure notes. "
    "Everything else is in review-context.json or the output directory."
)


def get_step_guidance(step, total_steps, vals, thoughts):
    """Return guidance dict for a given step.

    Args:
        step: Current step number (0-14).
        total_steps: Total step count (14).
        vals: Dict from load_context_values().
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
        else:
            return {
                "phase": "SETUP",
                "title": "Repo Setup",
                "actions": [
                    "You are a thorough PR reviewer. Running the full review pipeline.",
                    "",
                    "Auto-stash any uncommitted changes.",
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

    # Step 3: Review Context Summary (merged from old Steps 3+4+5+6+7)
    if step == 3:
        return {
            "phase": "AWARENESS",
            "title": "Review Context Summary",
            "actions": [
                "You are a thorough PR reviewer. Internalize the review context.",
                "",
                f"**Existing reviews:** {vals['reviews_summary']}",
                "",
                f"**Linked issues:** {vals['linked_issues']}",
                "",
                f"**PR title:** {vals['pr_title']}",
                f"**PR author:** {vals['pr_author']}",
                "",
                "Analyze the context above. Record in --thoughts:",
                "- PR purpose (from title, body, linked issue)",
                "- Key changes (from diff stats in review-context.json)",
                "- Review focus areas (from issue context, if any)",
                "- Whether this is a first review or re-review",
                "",
                STATE_REQ,
            ],
            "next": "Step 4: Fetch Issue Context",
        }

    # Step 4: Fetch Issue Context (was Step 6)
    if step == 4:
        return {
            "phase": "CONTEXT",
            "title": "Fetch Issue Context",
            "actions": [
                "You are a thorough PR reviewer. Gather issue context if available.",
                "",
                f"Linked issues from review context: {vals['linked_issues']}",
                "",
                "If linked issues were found:",
                "  - For Linear IDs: use the Linear MCP server to fetch issue details",
                "  - For GitHub IDs: use `gh issue view <ID> --json title,body,labels`",
                "",
                "If no linked issues or fetch fails: skip — note 'no issue context'.",
                "",
                "Summarize: what problem is this PR solving? What are the acceptance criteria?",
                "",
                STATE_REQ,
            ],
            "next": "Step 5: Summarize Context",
        }

    # Step 5: Summarize Context (was Step 7)
    if step == 5:
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
            "next": "Step 6: Assess PR Size",
        }

    # Step 6: Assess PR Size (was Step 8)
    if step == 6:
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
            "next": "Step 7: Ground Truth Collection",
        }

    # Step 7: Ground Truth Collection (was Step 9)
    if step == 7:
        return {
            "phase": "EXECUTION",
            "title": "Ground Truth Collection",
            "actions": [
                "You are a thorough PR reviewer. Collect ground truth signals (optional).",
                "",
                "**Phase A — Tool Discovery:**",
                "Read the project's CLAUDE.md and AGENTS.md (if present). Extract the commands",
                "this project uses to run linters, test suites, security scanners, and coverage.",
                f"Write a tool-config.json in {od}:",
                "",
                f'    cat > "{od}/tool-config.json" << \'TOOLCFG\'',
                '    {',
                '      "<tool_name>": { "cmd": "<command template>" }',
                '    }',
                "    TOOLCFG",
                "",
                "**Supported tools:**",
                "",
                "| Tool | Purpose | Scope | Output |",
                "|------|---------|-------|--------|",
                "| `eslint` | JS/TS linting | Changed files (`{files}`) | `eslint-results.json` |",
                "| `phpcs` | PHP linting | Changed files (`{files}`) | `phpcs-results.json` |",
                "| `semgrep` | Security scanning | Changed files (`{files}`) | `semgrep-results.json` |",
                "| `jest` | JS/TS tests + coverage | Full project (no `{files}`) | `jest-results.json` + coverage |",
                "| `phpunit` | PHP tests + coverage | Full project (no `{files}`) | `phpunit-results.json` + coverage |",
                "",
                "**Scoping rules:**",
                "- Linters/scanners: use `{files}` placeholder — scopes to changed files only",
                "- Test suites: do NOT use `{files}` — must run full project to catch regressions",
                "- Coverage: include coverage flags in test commands (e.g., `--coverage`, `--coverage-clover`)",
                "  Scope coverage to changed files where supported (`--collectCoverageFrom`, `--coverage-filter`)",
                "",
                "**Rules:**",
                "- Only include tools the project actually uses and has instructions for",
                "- If you can't determine the exact command, omit the tool",
                "- If the project has no tool instructions at all, write an empty config `{}`",
                "",
                "**Phase B — Tool Execution:**",
                "",
                f"    python3 {sd}/run-ground-truth.py \\",
                f"      --output-dir \"{od}\" \\",
                f"      --changed-files \"{vals['changed_files_csv']}\" \\",
                f"      --tool-config \"{od}/tool-config.json\"",
                "",
                "If the script exits non-zero or produces no findings, continue — ground truth is additive, not required.",
                "",
                STATE_REQ,
            ],
            "next": "Step 8: Generate Dispatch Plan",
        }

    # Step 8: Generate Dispatch Plan (was Step 10)
    if step == 8:
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
                "Review the dispatch plan output. The script's triage decisions are authoritative.",
                "Only override a decision if you have strong evidence the script missed:",
                "  - Override SKIPPED_TRIAGE → DISPATCH: when commit messages or PR context clearly indicate relevance the script couldn't detect",
                "  - Override DISPATCH → SKIP: almost never — when in doubt, keep DISPATCH",
                "Log any overrides: 'TRIAGE OVERRIDE: <agent>: <old> → <new> — <one-line reason>'",
                "",
                STATE_REQ,
            ],
            "next": "Step 9: Dispatch Agents + Reconcile",
        }

    # Step 9: Dispatch Agents (was Step 11)
    if step == 9:
        gt_arg = ""
        if os.path.isfile(vals["ground_truth"]):
            gt_arg = f" --ground-truth \"{vals['ground_truth']}\""

        return {
            "phase": "EXECUTION",
            "title": "Dispatch Agents",
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
                STATE_REQ,
            ],
            "next": "Step 10: Reconcile + Verify",
        }

    # Step 10: Reconcile + Verify (was Step 12)
    if step == 10:
        gt_line = ""
        if os.path.isfile(vals["ground_truth"]):
            gt_line = f"\n        Ground Truth: {vals['ground_truth']}"

        return {
            "phase": "EXECUTION",
            "title": "Reconcile + Verify",
            "actions": [
                "You are a thorough PR reviewer. Dispatch the reconciliator.",
                "",
                f"Build the review file path list from the dispatch plan at {vals['dispatch_plan']}.",
                "For EVERY agent with status DISPATCH in the plan (not just finished ones),",
                f"add: {od}/<agent-name>-review.json",
                "",
                "Also include any agents you dispatched via triage override in Step 8",
                f"(their review files may exist at {od}/<agent>-review.json even though",
                "the dispatch plan still shows SKIPPED_TRIAGE for them).",
                "",
                "The reconciliator handles missing/empty files — it needs the full expected list",
                "to track which agents were dispatched but produced no output.",
                "",
                "Dispatch the reconciliator with the explicit file list:",
                "",
                "    Agent tool:",
                "      subagent_type: pirategoat-tools:review-reconciliator",
                "      prompt: |",
                "        Review Files:",
                f"        (one line per completed agent: {od}/<agent>-review.json)",
                "",
                f"        Output Directory: {od}",
                f"        Git Range: {gr}",
                f"        PR Context: PR #{pr} in this repo",
                "        Change Purpose: <1-3 sentence summary: PR title, what the PR is solving, linked issue goal if known — from your accumulated --thoughts context>",
                f"        Changed Files: {vals['changed_files_csv']}",
                f"        Dispatch Plan: {vals['dispatch_plan']}" + gt_line,
                "",
                "The reconciliator reads all agent findings, groups by underlying concern,",
                "verifies each against actual code, and writes:",
                f"- {od}/review-findings.json — structured output",
                f"- {od}/review-findings.md — narrative review summary",
                "",
                STATE_REQ,
            ],
            "next": "Step 11: Generate Review Report",
        }

    # Step 11: Generate Review Report (was Step 13)
    if step == 11:
        return {
            "phase": "REVIEW",
            "title": "Generate Review Report",
            "actions": [
                "You are a thorough PR reviewer. Write the review report.",
                "",
                "Read the reconciled output:",
                f"- {od}/review-findings.json — structured findings (severity, file, line, confidence)",
                f"- {od}/review-findings.md — the reconciliator's narrative framing",
                "",
                "Combine with PR context accumulated from Steps 0-5:",
                "- PR purpose (title, body, linked issue details)",
                "- Size assessment",
                "- Review focus areas",
                "",
                "Connect findings to what the developer was trying to accomplish.",
                "",
                f"Write the review report to: {vals['review_report']}",
                "",
                "The report should include:",
                "- Executive summary (1-3 sentences connecting findings to PR intent)",
                "- Findings by severity (critical → low) with file:line and actionable recommendations",
                "- Overall verdict (APPROVE / REQUEST_CHANGES / COMMENT)",
                "",
                STATE_REQ,
            ],
            "next": "Step 12: Decision Critic",
        }

    # Step 12: Decision Critic (was Step 14)
    if step == 12:
        return {
            "phase": "VALIDATION",
            "title": "Decision Critic",
            "actions": [
                "You are a thorough PR reviewer. Dispatch the decision critic.",
                "",
                "    Agent tool:",
                "      subagent_type: pirategoat-tools:decision-reviewer",
                "      prompt: |",
                f"        Document Path: {vals['review_report']}",
                f"        Output Directory: {od}",
                "",
                "Wait for the critic to finish. Read its findings:",
                f"    Read {od}/decision-critic-findings.md",
                "",
                "Extract the verdict using this priority chain:",
                "1. Parse the agent's return message for `Verdict: STAND|REVISE|ESCALATE`.",
                f"2. Fallback: read `{od}/decision-critic-findings.md` for the `**Verdict:**` line.",
                "3. If both fail: note 'Decision critic unavailable' and present as-is.",
                "",
                "Act on the verdict:",
                "- STAND: No changes needed. Proceed to Step 13.",
                "- REVISE: Spot-check 2-3 of the critic's factual claims (file paths, line refs,",
                "  counts) with a single command each. Strip any claim that fails spot-check.",
                "  Apply valid adjustments to the review report (upgrade/downgrade severities,",
                "  recategorize findings, add/remove items). Recalculate the verdict from updated",
                "  findings (any critical -> REQUEST_CHANGES, any high/medium -> COMMENT,",
                f"  all clear -> APPROVE). Rewrite {vals['review_report']}.",
                "- ESCALATE: Spot-check factual claims as for REVISE. Update the review report",
                "  to flag prominently that the review has significant validity concerns",
                "  requiring human judgment before acting on findings. Override the verdict to",
                f"  COMMENT regardless of original verdict. Rewrite {vals['review_report']}.",
                "",
                STATE_REQ,
            ],
            "next": "Step 13: Present Results",
        }

    # Step 13: Present Results (was Step 15)
    if step == 13:
        return {
            "phase": "OUTPUT",
            "title": "Present Results",
            "actions": [
                "You are a thorough PR reviewer. Present the final results.",
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
            "next": "Step 14: Cleanup",
        }

    # Step 14: Cleanup (was Step 16)
    if step == 14:
        if vals["bot_mode"]:
            return {
                "phase": "OUTPUT",
                "title": "Cleanup",
                "actions": [
                    "Pipeline complete.",
                    "",
                    STATE_REQ,
                ],
                "next": None,
            }
        else:
            return {
                "phase": "OUTPUT",
                "title": "Cleanup",
                "actions": [
                    "Restore workspace:",
                    "- If ORIGINAL_BRANCH in --thoughts: `git checkout <ORIGINAL_BRANCH>`",
                    "- If STASHED=true: `git stash apply <STASH_REF> && git stash drop <STASH_REF>`",
                    "",
                    "Pipeline complete.",
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
        vals, args.thoughts,
    )

    if guidance is None:
        print(f"ERROR: No guidance for step {args.step_number}", file=sys.stderr)
        sys.exit(1)

    # --- Telemetry (best-effort) ---
    if ReviewTelemetry is not None and guidance is not None:
        try:
            log_dir = os.environ.get("PIRATEGOAT_TELEMETRY_LOG_DIR")
            kwargs = {"log_dir": log_dir} if log_dir else {}
            telemetry = ReviewTelemetry(args.output_dir, **kwargs)

            step_kwargs = dict(
                step=args.step_number,
                phase=guidance["phase"],
                title=guidance["title"],
                bot_mode=vals["bot_mode"],
                thoughts_length=len(args.thoughts),
            )

            if args.step_number == 0:
                telemetry.start(
                    pr_number=args.pr_number or "",
                    total_steps=args.total_steps,
                    bot_mode=vals["bot_mode"],
                )
            elif args.step_number == args.total_steps:
                telemetry.finalize(**step_kwargs)
            else:
                telemetry.log_step(**step_kwargs)
        except Exception:
            pass  # Telemetry is non-critical
    # --- End Telemetry ---

    print(format_output(args.step_number, args.total_steps, guidance))


if __name__ == "__main__":
    main()

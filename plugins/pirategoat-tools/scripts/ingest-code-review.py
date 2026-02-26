#!/usr/bin/env python3
"""
Ingest Code Review - Step-by-step prompt injection for structured finding validation.

Grounded in:
- Chain-of-Verification (Dhuliawala et al., 2023)
- Multi-Expert Prompting (Wang et al., 2024)
"""

import argparse
import sys
from typing import Optional


def get_phase_name(step: int) -> str:
    """Return the phase name for a given step number."""
    if step <= 2:
        return "SETUP"
    elif step == 3:
        return "SCOPE"
    elif step <= 5:
        return "VERIFICATION"
    else:
        return "SYNTHESIS"


def get_step_guidance(step: int, total_steps: int, output_dir: Optional[str], thoughts: Optional[str]) -> dict:
    """Return step-specific guidance and actions."""

    next_step = step + 1 if step < total_steps else None
    phase = get_phase_name(step)

    # Common state requirement for steps 2+
    state_requirement = (
        "CONTEXT REQUIREMENT: Your --thoughts from this step must include ALL finding IDs (F1, F2...), "
        "their scope status (IN_SCOPE/OUT_OF_SCOPE), and any verification questions and statuses "
        "from previous steps. This accumulated state is essential for workflow continuity."
    )

    # SETUP PHASE — Step 1
    if step == 1:
        return {
            "phase": phase,
            "step_title": "Locate & Initialize",
            "actions": [
                "You are a senior engineer ingesting code review findings. Your first task is to "
                "locate the review output and establish the git context for scope checking.",
                "",
                "PARSE --output-dir:",
                "  - If value is 'auto': detect from current branch:",
                "    BRANCH=$(git branch --show-current)",
                "    BRANCH_SAFE=$(echo \"$BRANCH\" | tr '/' '-' | sed 's/^-//')",
                "    OUTPUT_DIR=\"/tmp/branch-review-${BRANCH_SAFE}\"",
                "  - Otherwise: use the provided path directly as OUTPUT_DIR",
                "",
                "VERIFY review output exists:",
                "  Run: ls \"${OUTPUT_DIR}\"/*.json 2>/dev/null",
                "  If no review files found: STOP.",
                "  Tell the user: \"No review output found at <OUTPUT_DIR>. Run /code-review or /full-code-review first.\"",
                "",
                "READ review state:",
                "  Run: cat \"${OUTPUT_DIR}/.review-state.json\" 2>/dev/null",
                "  If state file exists: read git_range_used from it.",
                "  If not: compute from branch:",
                "    DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')",
                "    GIT_RANGE=\"${DEFAULT_BRANCH}..HEAD\"",
                "",
                "GET changed files:",
                "  Run: git diff --name-only <GIT_RANGE>",
                "  Store result as CHANGED_FILES.",
                "",
                "OUTPUT_FORMAT: Record in --thoughts:",
                "  OUTPUT_DIR=<path>",
                "  GIT_RANGE=<range>",
                "  CHANGED_FILES=[file1, file2, ...]",
            ],
            "next": "Step 2: Parse findings and assign stable IDs.",
            "academic_note": None,
        }

    # SETUP PHASE — Step 2
    if step == 2:
        return {
            "phase": phase,
            "step_title": "Parse Findings & Assign IDs",
            "actions": [
                "You are a senior engineer parsing code review findings and assigning stable IDs.",
                "",
                "READ review output (from OUTPUT_DIR in --thoughts):",
                "  Primary:  ${OUTPUT_DIR}/reconciled.json",
                "  Fallback: ${OUTPUT_DIR}/reconciled.md",
                "  Last:     ${OUTPUT_DIR}/*-review.json (individual agent files)",
                "",
                "PARSE ALL FINDINGS and assign stable IDs: F1, F2, F3, ...",
                "",
                "EXTRACT per finding:",
                "  - file       (path to the file containing the issue)",
                "  - line       (line number)",
                "  - severity   (critical/high/medium/low)",
                "  - title      (short description)",
                "  - source_agents (list of agents that flagged this)",
                "  - confidence (0.0–1.0)",
                "",
                "OUTPUT_FORMAT (one line per finding):",
                "  F1: <title> | <file>:<line> | severity=<s> | agents=[<a1>,<a2>] | confidence=<c>",
                "  F2: <title> | <file>:<line> | severity=<s> | agents=[<a1>] | confidence=<c>",
                "",
                "COUNT: \"N findings total.\"",
                "",
                state_requirement,
            ],
            "next": "Step 3: Classify each finding as IN_SCOPE or OUT_OF_SCOPE.",
            "academic_note": None,
        }

    # SCOPE PHASE — Step 3
    if step == 3:
        return {
            "phase": phase,
            "step_title": "Classify Scope",
            "actions": [
                "You are a senior engineer classifying each finding's scope against the reviewed diff.",
                "",
                "Use CHANGED_FILES and GIT_RANGE from --thoughts.",
                "",
                "FOR EACH FINDING Fi:",
                "",
                "  CHECK 1: Is the finding's file in CHANGED_FILES?",
                "    If NO  → OUT_OF_SCOPE (file not in diff)",
                "    If YES → proceed to Check 2",
                "",
                "  CHECK 2: Is the finding's line in the diff hunks?",
                "    Run: git diff <GIT_RANGE> -- <file>",
                "    Examine the diff hunks (+/- lines and their line numbers).",
                "    If the flagged line falls OUTSIDE the hunks:",
                "      → OUT_OF_SCOPE (pre-existing code in changed file)",
                "      EXCEPTION: If the change directly interacts with the flagged line",
                "      (e.g., calls a function that has the vulnerability), mark IN_SCOPE with note.",
                "    If the flagged line IS in the hunks → IN_SCOPE",
                "",
                "OUTPUT_FORMAT (one line per finding):",
                "  F1 [IN_SCOPE]: <title>",
                "  F2 [OUT_OF_SCOPE: file not in diff]: <title>",
                "  F3 [OUT_OF_SCOPE: pre-existing code]: <title>",
                "  F4 [IN_SCOPE*: interacts with change]: <title>",
                "",
                "COUNT: \"X of N findings are IN_SCOPE. Proceeding to generate verification questions.\"",
                "",
                "SKIP_NOTE: OUT_OF_SCOPE findings skip steps 4-5 and go directly to SYNTHESIS",
                "as the OUT_OF_SCOPE category.",
                "",
                state_requirement,
            ],
            "next": "Step 4: Generate verification questions for IN_SCOPE findings.",
            "academic_note": None,
        }

    # VERIFICATION PHASE — Step 4
    if step == 4:
        return {
            "phase": phase,
            "step_title": "Generate Verification Questions",
            "actions": [
                "You are a senior engineer generating falsification questions for each IN_SCOPE finding.",
                "",
                "For each IN_SCOPE finding from --thoughts, generate 1-2 verification questions.",
                "",
                "CRITERIA FOR GOOD QUESTIONS:",
                "  - Specific and independently answerable using only the actual code",
                "  - Designed to reveal if the finding could be WRONG (falsification focus)",
                "  - Do not assume the finding is correct when framing the question",
                "  - Each question tests a different aspect of the claim",
                "",
                "QUESTION BOUNDS:",
                "  - Simple finding: 1 question",
                "  - Multi-part or complex finding: 2 questions maximum",
                "",
                "OUTPUT_FORMAT:",
                "  F1 [IN_SCOPE]: <title>",
                "    Q1: <can you find the actual code doing X at file:line?>",
                "  F2 [IN_SCOPE]: <title>",
                "    Q1: <does the code at file:line actually do Y?>",
                "    Q2: <does the codebase have protection Z already?>",
                "",
                state_requirement,
            ],
            "next": "Step 5: Answer each question independently with factored verification.",
            "academic_note": (
                "Chain-of-Verification (Dhuliawala et al., 2023): \"Plan verification questions "
                "to check its work, and then systematically answer those questions.\""
            ),
        }

    # VERIFICATION PHASE — Step 5
    if step == 5:
        return {
            "phase": phase,
            "step_title": "Factored Verification",
            "actions": [
                "You are a senior engineer performing factored verification of each IN_SCOPE finding.",
                "This is the most important step. Your accuracy here directly determines which findings",
                "are actionable. Take your time and be rigorous.",
                "",
                "Answer each question INDEPENDENTLY.",
                "",
                "EPISTEMIC BOUNDARY (critical for avoiding confirmation bias):",
                "",
                "  Answer using ONLY:",
                "    (a) The actual code at the referenced location — use the Read tool to examine the file",
                "    (b) Stated context from --thoughts (git range, CHANGED_FILES, constraints)",
                "    (c) Established domain knowledge (security patterns, WP conventions, etc.)",
                "",
                "  Do NOT assume the finding is correct and work backward.",
                "  Do NOT assume the finding is wrong and seek to disprove.",
                "  Answer the question on its own merits from the evidence.",
                "",
                "SEPARATE answer from implication:",
                "  ANSWER: What the code actually does at that location (evidence-based, from Read tool)",
                "  IMPLICATION: What this means for the finding's accuracy",
                "",
                "Mark each IN_SCOPE finding:",
                "  VERIFIED  — answers are consistent with the finding; issue exists as described",
                "  FAILED    — answers reveal the finding is inaccurate, doesn't apply, or misunderstands code",
                "  UNCERTAIN — insufficient evidence; state what would resolve it",
                "",
                "OUTPUT_FORMAT:",
                "  F1 [IN_SCOPE] VERIFIED:",
                "    Q1: <question>",
                "      Answer: <what the code actually does, based on Read tool>",
                "      Implication: <what this means for the finding>",
                "    Status: VERIFIED",
                "    Rationale: <one sentence explaining the status>",
                "",
                state_requirement,
            ],
            "next": "Step 6: Categorize all findings and produce the action plan.",
            "academic_note": (
                "Chain-of-Verification: \"Factored variants which separate out verification steps, "
                "in terms of which context is attended to, give further performance gains.\""
            ),
        }

    # SYNTHESIS PHASE — Step 6
    if step == 6:
        return {
            "phase": phase,
            "step_title": "Categorize & Plan",
            "actions": [
                "You are a senior engineer synthesizing all findings into a categorized action plan.",
                "",
                "MAP scope + verification status to final categories:",
                "",
                "  CONFIRMED     = IN_SCOPE + VERIFIED",
                "  LIKELY VALID  = IN_SCOPE + UNCERTAIN (plausible but unverified)",
                "  FALSE POSITIVE = IN_SCOPE + FAILED (finding is inaccurate)",
                "  OUT OF SCOPE  = OUT_OF_SCOPE (from step 3)",
                "  STYLE/PREFERENCE = IN_SCOPE + VERIFIED but subjective/non-defect",
                "",
                "PRESENT validation summary table:",
                "",
                "  | Finding | Source | Severity | Verdict | Reason |",
                "  |---------|--------|----------|---------|--------|",
                "  | <title> | <agents> | <sev> | CONFIRMED | <evidence> |",
                "  | <title> | <agents> | <sev> | OUT OF SCOPE | <reason> |",
                "",
                "BUILD Action Plan for CONFIRMED + LIKELY VALID only:",
                "",
                "  ### Critical / Must Fix (security, data loss, crashes)",
                "  - [ ] <finding> — <what to change> — <scope estimate>",
                "",
                "  ### Important / Should Fix (bugs, performance, significant quality)",
                "  - [ ] <finding> — <what to change>",
                "",
                "  ### Consider (LIKELY VALID — uncertain but plausible)",
                "  - [ ] <finding> — <caveat>",
                "",
                "  ### Dismissed",
                "  - <finding> — OUT_OF_SCOPE: <reason>",
                "  - <finding> — FALSE POSITIVE: <why inaccurate>",
                "  - <finding> — STYLE/PREFERENCE: <why dismissed>",
                "",
                "PRESENT the plan and ask:",
                "\"How would you like to proceed — fix everything, fix critical only, or discuss specific items?\"",
                "",
                state_requirement,
            ],
            "next": None,
            "academic_note": None,
        }

    return {
        "phase": "UNKNOWN",
        "step_title": "Unknown Step",
        "actions": ["Invalid step number."],
        "next": None,
        "academic_note": None,
    }


def format_output(step: int, total_steps: int, guidance: dict) -> str:
    """Format the output for display."""
    lines = []

    # Header
    lines.append(f"INGEST CODE REVIEW - Step {step}/{total_steps}: {guidance['step_title']}")
    lines.append(f"Phase: {guidance['phase']}")
    lines.append("")

    # Actions
    for action in guidance["actions"]:
        lines.append(action)
    lines.append("")

    # Academic note if present
    if guidance.get("academic_note"):
        lines.append(f"[{guidance['academic_note']}]")
        lines.append("")

    # Next step or completion
    if guidance["next"]:
        lines.append(f"NEXT: {guidance['next']}")
    else:
        lines.append("WORKFLOW COMPLETE - Present action plan to user.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Ingest Code Review - Structured finding validation workflow"
    )
    parser.add_argument(
        "--step-number",
        type=int,
        required=True,
        help="Current step number (1-6)",
    )
    parser.add_argument(
        "--total-steps",
        type=int,
        required=True,
        help="Total steps in workflow (always 6)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Path to review output directory, or 'auto' to detect from branch (required for step 1)",
    )
    parser.add_argument(
        "--thoughts",
        type=str,
        required=True,
        help="Your accumulated findings, IDs, and statuses from all previous steps",
    )

    args = parser.parse_args()

    # Validate step number
    if args.step_number < 1 or args.step_number > 6:
        print("ERROR: step-number must be between 1 and 6", file=sys.stderr)
        sys.exit(1)

    # Validate step 1 requirements
    if args.step_number == 1 and not args.output_dir:
        print(
            "ERROR: --output-dir is required for step 1 (or set to 'auto' to detect from branch)",
            file=sys.stderr,
        )
        sys.exit(1)

    # Get guidance for current step
    guidance = get_step_guidance(
        args.step_number,
        args.total_steps,
        args.output_dir,
        args.thoughts,
    )

    # Print review output context on step 1
    if args.step_number == 1 and args.output_dir:
        print(f"REVIEW OUTPUT: {args.output_dir}")
        print()

    # Print formatted output
    print(format_output(args.step_number, args.total_steps, guidance))


if __name__ == "__main__":
    main()

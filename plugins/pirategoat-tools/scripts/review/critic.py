#!/usr/bin/env python3
"""
Review Critic - Step-by-step prompt injection for review-specific decision criticism.

A 4-phase pipeline tailored for code review criticism: Decompose, Verify, Challenge,
Synthesize. Fork of the generic decision-critic.py with prompts focused on severity
calibration, false positive detection, and code-grounded verification.

Grounded in:
- Chain-of-Verification (Dhuliawala et al., 2023)
- Self-Consistency (Wang et al., 2023)
- Multi-Expert Prompting (Wang et al., 2024)
"""

import argparse
import sys
from typing import Optional


TOTAL_STEPS = 4

# Canonical critic verdict vocabulary. The Step 4 rubric below and the
# pipeline.py critic briefings present exactly these verdicts, and the
# review_metrics consumer (analysis/review_metrics/contracts.py) classifies
# manifest critic verdicts against this constant — keep all three aligned.
CRITIC_VERDICTS = ("STAND", "REVISE", "ESCALATE")


def get_step_guidance(
    step: int,
    total_steps: int,
    report: str,
    output_dir: str,
    context_path: Optional[str],
) -> dict:
    """Return step-specific guidance and actions."""

    next_step = step + 1 if step < total_steps else None

    # Common state accumulation requirement for steps 2+
    state_requirement = (
        "STATE ACCUMULATION REQUIREMENT: Your --thoughts from this step must include "
        "ALL IDs, classifications, and status markers from previous steps. This "
        "accumulated state is essential for workflow continuity."
    )

    # STEP 1 — DECOMPOSITION
    if step == 1:
        actions = [
            "You are a review critic specializing in code review quality. Your task is to "
            "decompose this review into its constituent claims so each can be independently "
            "verified or challenged. This decomposition is critical to the quality of the "
            "entire workflow.",
            "",
            "Read the review report"
            + (
                " and critic context document. Extract claims using the stable IDs "
                "from the context document (F1, F2, ...) that will persist through "
                "ALL subsequent steps:"
                if context_path
                else ". Extract and assign stable IDs that will persist through ALL "
                "subsequent steps:"
            ),
            "",
            (
                "- FACTUAL CLAIMS [use F1, F2, ... IDs from context]: Statements about what the "
                "code does or doesn't do. Use the pre-assigned finding IDs (F1, F2, ...) from "
                "the context document — each finding maps to a factual claim to verify."
                if context_path
                else "- FACTUAL CLAIMS [F1, F2, ...]: Statements about what the code does or "
                "doesn't do (\"line 54 is missing an is_array guard\", \"the function uses "
                "non-Yoda comparison\"). These are the review's core assertions."
            ),
            "- SEVERITY ASSERTIONS [S1, S2, ...]: Claims about impact level (\"this is HIGH "
            "because it could cause a PHP fatal\", \"this is MEDIUM — coding standards "
            "violation\"). Include the stated justification.",
            "- JUDGMENT CALLS [J1, ...]: Subjective recommendations (\"should fix before "
            "merge\", \"consider for follow-up\", \"this is a preference, not a defect\")",
            "",
            "Classify each as VERIFIABLE (can check by reading code or running a command) "
            "or JUDGMENT (subjective tradeoff with no objectively correct answer). Count "
            "verifiable items for the next phase.",
        ]

        # Surface input files
        actions.append("")
        actions.append(f"REVIEW REPORT: {report}")
        if context_path:
            actions.append(f"CRITIC CONTEXT: {context_path}")

        return {
            "phase": "DECOMPOSITION",
            "step_title": "Decompose",
            "actions": actions,
            "next": f"Step {next_step}: Verify each verifiable item against primary source code.",
            "academic_note": (
                "Multi-Expert Prompting (Wang et al., 2024): \"Integrating multiple experts' "
                "perspectives catches blind spots in reasoning.\""
            ),
        }

    # STEP 2 — VERIFICATION
    if step == 2:
        actions_2 = [
            "You are a review critic performing factored verification. This is the most "
            "important step — your accuracy here directly determines verdict quality. "
            "Take your time and be rigorous.",
        ]
        if context_path:
            actions_2.append("")
            actions_2.append(f"CRITIC CONTEXT (for targeted verification): {context_path}")
        actions_2.extend([
            "",
            "For each VERIFIABLE item from Step 1, verify INDEPENDENTLY:",
            "",
            "EPISTEMIC BOUNDARY (critical for avoiding confirmation bias):",
            "- Verify by reading the PRIMARY SOURCE (actual code files), NOT by re-reading "
            "the review report. The report is what you're checking, not what you check against.",
            "- Do NOT assume the review is correct and look for confirming evidence.",
            "- Do NOT assume the review is wrong and look for disconfirming evidence.",
            "- Do NOT claim to have verified a factual assertion without running a command "
            "or reading a file.",
            "- Do NOT state specific numbers, counts, or line references without citing "
            "the tool output that produced them.",
            "",
            "For each item:",
            "- Read the actual source file at the referenced line using the Read tool",
            "- Check: does the code actually do what the finding claims?",
            "- Check: are line numbers accurate?",
            "- Check: is the severity proportionate to actual impact in context?",
            "- For quantitative claims (line counts, occurrence counts): run the command to verify",
            "",
            "SEPARATE your answer from its implication:",
            "- Tool used: <command run, file read, or 'NONE — domain knowledge only'>",
            "- Answer: <factual response based on tool output>",
            "- Implication: <what this means for the review's claim>",
            "",
            "Mark each: VERIFIED / FAILED / UNCERTAIN.",
            "",
            state_requirement,
        ])
        return {
            "phase": "VERIFICATION",
            "step_title": "Verify",
            "actions": actions_2,
            "next": f"Step {next_step}: Challenge the review with adversarial analysis.",
            "academic_note": (
                "Chain-of-Verification (Dhuliawala et al., 2023): \"Factored verification "
                "prevents confirmation bias. Plan verification questions, then answer them "
                "independently.\""
            ),
        }

    # STEP 3 — CHALLENGE
    if step == 3:
        return {
            "phase": "CHALLENGE",
            "step_title": "Challenge",
            "actions": [
                "You are a review critic shifting to adversarial analysis. Your task: generate "
                "the STRONGEST possible honest case for where the review is wrong or unfair.",
                "",
                "START FROM VERIFICATION RESULTS:",
                "- FAILED items are direct ammunition — the review made incorrect claims",
                "- UNCERTAIN items are attack vectors — unverified assertions presented as fact",
                "- Even VERIFIED items may have disproportionate severity or missing context",
                "",
                "REVIEW-SPECIFIC ATTACK VECTORS:",
                "- What did the review MISS that it should have caught?",
                "- Are any findings FALSE POSITIVES? (code actually handles the case, or the "
                "issue is pre-existing)",
                "- Are severities INFLATED? (MEDIUM issues labeled HIGH, preferences disguised "
                "as defects)",
                "- Is the verdict PROPORTIONATE? (REQUEST_CHANGES for issues that could be "
                "follow-up work)",
                "- Is there CONTEXT the review missed? (documentation conventions, project "
                "norms, author's likely intent)",
                "- Are there DOC-VS-CODE gaps? (code follows documented conventions that differ "
                "from production patterns)",
                "- Would the PR author have a legitimate defense for any finding?",
                "",
                "STEEL-MANNING: Present the PR author's BEST defense, not a strawman. Make the "
                "argument as strong as you can.",
                "",
                state_requirement,
            ],
            "next": f"Step {next_step}: Synthesize findings into verdict.",
            "academic_note": (
                "Self-Consistency (Wang et al., 2023): \"Correct reasoning processes tend to "
                "have greater agreement in their final answer than incorrect processes.\""
            ),
        }

    # STEP 4 — SYNTHESIS
    if step == 4:
        return {
            "phase": "SYNTHESIS",
            "step_title": "Synthesize",
            "actions": [
                "You are a review critic delivering your final assessment. This verdict will "
                "guide whether the review reaches a human as-is or gets revised first. Be "
                "confident in your analysis and precise in your recommendation.",
                "",
                "Weigh verification results against challenges. Apply the verdict rubric:",
                "",
                "STAND when ALL of these apply:",
                "- No FAILED items on core factual claims",
                "- Severity levels are proportionate (even if you'd calibrate slightly differently)",
                "- Challenges from Step 3 are minor or the review already addresses them",
                "- The verdict is proportionate to the actual issues found",
                "",
                "REVISE when ANY of these apply:",
                "- One or more FAILED factual claims that materially affect a finding",
                "- Severity is materially wrong on a finding that changes the verdict tier",
                "- The review omits important context that would change how a human interprets it",
                "- Specific adjustments can be identified (not just \"could be better\")",
                "",
                "ESCALATE when ANY of these apply:",
                "- Multiple FAILED claims suggesting systematic quality issues",
                "- The review may be actively misleading about what the code does",
                "- Fundamental framing problem that revision cannot fix",
                "",
                "BORDERLINE: When between STAND and REVISE, favor REVISE (cheaper to refine "
                "than to ship an unfair review).",
                "",
                f"Write findings to `{output_dir}/decision-critic-findings.md` using the format "
                "specified in the agent definition.",
                "",
                state_requirement,
            ],
            "next": None,
            "academic_note": None,
        }

    # Fallback (should not be reached with proper validation)
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
    lines.append(
        f"═══ REVIEW CRITIC Step {step}/{total_steps}: "
        f"{guidance['step_title']} ({guidance['phase']}) ═══"
    )
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
        lines.append(
            f"NEXT (MANDATORY): {guidance['next']} Do NOT stop — call "
            f"critic.py with --step-number {step + 1} immediately."
        )
    else:
        lines.append("PIPELINE COMPLETE — Present verdict to user.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Review Critic - Review-specific decision criticism workflow"
    )
    parser.add_argument(
        "--step-number",
        type=int,
        required=True,
        help="Current step number (1-4)",
    )
    parser.add_argument(
        "--total-steps",
        type=int,
        required=True,
        help="Total steps in workflow (always 4)",
    )
    parser.add_argument(
        "--report",
        type=str,
        required=True,
        help="Path to the review report being criticized",
    )
    parser.add_argument(
        "--context",
        type=str,
        default=None,
        help="Path to critic-context.md (curated Markdown with report + findings)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        required=True,
        help="Directory for output files",
    )
    parser.add_argument(
        "--thoughts",
        type=str,
        required=True,
        help="Accumulated analysis state from previous steps",
    )

    args = parser.parse_args()

    # Validate total steps matches the constant
    if args.total_steps != TOTAL_STEPS:
        print(
            f"ERROR: total-steps must be {TOTAL_STEPS} (got {args.total_steps})",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate step number
    if args.step_number < 1 or args.step_number > TOTAL_STEPS:
        print(
            f"ERROR: step-number must be between 1 and {TOTAL_STEPS}",
            file=sys.stderr,
        )
        sys.exit(1)

    context_path = args.context

    # Get guidance for current step
    guidance = get_step_guidance(
        args.step_number,
        args.total_steps,
        args.report,
        args.output_dir,
        context_path,
    )

    # Print formatted output
    print(format_output(args.step_number, args.total_steps, guidance))


if __name__ == "__main__":
    main()

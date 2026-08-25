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
import json
import os
import sys
from typing import Optional

try:
    from . import atomic_io, critic_adjustments
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import atomic_io, critic_adjustments

atomic_write_json = atomic_io.atomic_write_json
atomic_write_text = atomic_io.atomic_write_text


TOTAL_STEPS = 4

# Canonical critic verdict vocabulary. The Step 4 rubric below and the
# briefings.py critic guidance presents exactly these verdicts, the
# review_metrics consumer (analysis/review_metrics/contracts.py) classifies
# manifest critic verdicts against this constant, and
# critic_adjustments.REVISE_VERDICT is the same literal spelled out
# separately (deliberately not imported — see that constant's own
# comment) as the one verdict apply_adjustments()'s gate accepts — keep
# all four aligned.
CRITIC_VERDICTS = ("STAND", "REVISE", "ESCALATE")
# Deliberately NOT a member of CRITIC_VERDICTS above: it is not a critique
# outcome, it is the record that no critique happened — the step-10
# pipeline writes it when quick mode skips the critic. A crashed, timed-out,
# or unusable critic leaves no committed verdict and is reported honestly.
# Consumers that measure critique quality must exclude it; consumers that
# measure whether a critic ran must not. critic_adjustments.py spells the
# same literal separately for its own presentation mapping.
CRITIC_VERDICT_SKIPPED = "SKIPPED"


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
            "Read the review record"
            + (
                " and the structured findings ledger. Key each claim to the "
                "target's canonical ledger id: `fN` for a finding or `cN` for "
                "a recorded check. That kind/id pair persists through ALL "
                "subsequent steps and is the handle the pipeline resolves:"
                if context_path
                else ". Extract and assign stable IDs that will persist through ALL "
                "subsequent steps:"
            ),
            "",
            (
                "- FACTUAL CLAIMS [use each finding/check ledger id]: Statements about "
                "what the code does or doesn't do. Take ids from `findings[].id` and "
                "`checks[].id`; preserve whether each target is a finding or check."
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
                "Author findings in `$TMPDIR/decision-critic-findings.md` "
                "using the format specified in the agent definition; never "
                f"write `{output_dir}/decision-critic-findings.md` directly.",
                "Invoke `critic.py --save` for the final STAND, REVISE, or "
                "ESCALATE verdict. Pass the temp adjustments file only for "
                "REVISE, as specified in the agent definition.",
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


def _read_required(path, problems, label):
    """Read a required save-mode input file as text.

    Records a problem (and returns None) instead of raising when the path
    is absent, missing, or unreadable — `run_save()` collects every
    problem before deciding whether to write anything, so a bad
    `--findings`/`--adjustments` path is just one more REJECTED line, not
    a crash.
    """
    if not path:
        problems.append(f"--{label} is required")
        return None
    if not os.path.isfile(path):
        problems.append(f"--{label} file not found: {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as err:
        problems.append(f"--{label} could not be read ({path}): {err}")
        return None


def _read_json(path, problems, label):
    """Read a required save-mode input file as JSON.

    Layered on `_read_required()`: a missing/unreadable file reports
    through that shared check, and a present-but-unparseable file gets
    its own problem here instead of an uncaught `JSONDecodeError`.
    """
    text = _read_required(path, problems, label)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        problems.append(f"--{label} is not valid JSON ({path}): {err}")
        return None


def _invalidate_verdict_commit_marker(output_dir):
    """Make any prior critic snapshot incomplete before replacing payloads."""
    path = os.path.join(
        output_dir, critic_adjustments.CRITIC_VERDICT_FILENAME
    )
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def run_save(args):
    """Validate and publish one committed critic snapshot.

    The ONLY channel the decision-reviewer agent is allowed to write
    `decision-critic-findings.md`, `decision-critic-adjustments.json`, and
    `decision-critic-verdict.json` through (see agents/decision-reviewer.md)
    — raw writes to the output directory are forbidden, closing the gap a
    critic writing those files directly left open: nothing here validates
    a hand-written artifact after the fact.

    Every problem is collected before anything is decided, the same
    all-or-nothing style `critic_adjustments.prepare_proposal()` and
    `settle()` use: a bad verdict, a missing findings file, an
    invalid adjustments batch, and a REVISE/STAND contradiction are all
    independent facts, and reporting only the first would make a caller
    fix one problem at a time instead of seeing the whole rejection at
    once. Proposal normalization assigns the stable adjustment IDs before
    publication; settlement fields are not accepted from the critic. On ANY
    validation problem, the previous complete snapshot stays
    untouched and every problem is echoed as its own ``REJECTED: <problem>``
    line. Once validation succeeds, the old verdict commit marker is removed
    before either replacement payload is written. A publication failure may
    therefore leave partial payloads, but never a readable verdict that makes
    mixed state authoritative. The schema-versioned verdict marker commits
    the proposal digest, so no marker means incomplete and any later proposal
    edit makes the snapshot unusable.

    Returns 0 on success and 1 on validation rejection. Publication I/O
    failures propagate so the caller exits loudly with the commit marker
    absent.
    """
    problems = []
    verdict = (args.verdict or "").strip().upper()
    if verdict not in CRITIC_VERDICTS:
        problems.append(
            f"verdict must be one of {sorted(CRITIC_VERDICTS)}, got "
            f"{args.verdict!r}"
        )
    findings_text = _read_required(args.findings, problems, "findings")
    adjustments = None
    adjustment_snapshot = None
    if args.adjustments:
        adjustments = _read_json(args.adjustments, problems, "adjustments")
        if adjustments is not None:
            try:
                adjustment_snapshot = critic_adjustments.prepare_proposal(
                    adjustments
                )
            except critic_adjustments.AdjustmentValidationError as error:
                problems.extend(error.problems)
    adjustments_doc = adjustments if isinstance(adjustments, dict) else {}
    entries = adjustments_doc.get("adjustments") or []
    if verdict == "REVISE" and not entries:
        problems.append("REVISE requires a non-empty adjustments batch")
    if verdict in ("STAND", "ESCALATE") and entries:
        problems.append(
            f"{verdict} with adjustments is a contradiction — adjustments "
            f"are a REVISE-only channel"
        )

    if problems:
        for p in problems:
            print(f"REJECTED: {p}")
        return 1

    if verdict != "REVISE":
        adjustment_snapshot = critic_adjustments.prepare_proposal({
            "schema": critic_adjustments.ADJUSTMENTS_SCHEMA,
            "adjustments": [],
        })
    digest = critic_adjustments.proposal_digest(adjustment_snapshot)
    od = args.output_dir
    with atomic_io.output_dir_lock(od):
        # Invalidate any previous commit before replacing either payload. Only
        # an absent marker is tolerable; permission and other I/O failures
        # propagate before the snapshot can be mixed. The shared lock keeps a
        # concurrent settle from observing this incomplete publication.
        _invalidate_verdict_commit_marker(od)
        atomic_write_text(
            os.path.join(od, "decision-critic-findings.md"), findings_text
        )
        critic_adjustments.write_adjustments(od, adjustment_snapshot)
        atomic_write_json(
            os.path.join(od, critic_adjustments.CRITIC_VERDICT_FILENAME),
            {
                "schema": critic_adjustments.VERDICT_MARKER_SCHEMA,
                "verdict": verdict,
                "proposal_digest": digest,
            },
        )
    print(f"RECORDED VERDICT: {verdict}")
    recorded_entries = adjustment_snapshot["adjustments"]
    print(
        f"RECORDED ADJUSTMENTS: {len(recorded_entries)}"
        + (
            " — " + ", ".join(
                entry["adjustment_id"] for entry in recorded_entries
            )
            if recorded_entries else ""
        )
    )
    print(f"PROPOSAL DIGEST: {digest}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Review Critic - Review-specific decision criticism workflow"
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help=(
            "Save mode: validate and publish a committed critic snapshot "
            "of findings, adjustments, and verdict. Requires "
            "--verdict, --findings, --output-dir, and optionally "
            "--adjustments. Mutually exclusive with the step-guidance "
            "mode below."
        ),
    )
    parser.add_argument(
        "--verdict",
        type=str,
        default=None,
        help="Save mode: STAND | REVISE | ESCALATE",
    )
    parser.add_argument(
        "--findings",
        type=str,
        default=None,
        help="Save mode: path to the findings Markdown to record",
    )
    parser.add_argument(
        "--adjustments",
        type=str,
        default=None,
        help="Save mode: path to the adjustments JSON (REVISE only)",
    )
    parser.add_argument(
        "--step-number",
        type=int,
        default=None,
        help="Current step number (1-4)",
    )
    parser.add_argument(
        "--total-steps",
        type=int,
        default=None,
        help="Total steps in workflow (always 4)",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Path to the review document being criticized (normally review-record.md)",
    )
    parser.add_argument(
        "--context",
        type=str,
        default=None,
        help="Path to review-findings.json (the structured findings ledger)",
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
        default=None,
        help="Accumulated analysis state from previous steps",
    )

    args = parser.parse_args()

    if args.save:
        sys.exit(run_save(args))

    if (
        args.step_number is None
        or args.total_steps is None
        or args.report is None
        or args.thoughts is None
    ):
        parser.error(
            "--step-number, --total-steps, --report, and --thoughts are "
            "required unless --save is given"
        )

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

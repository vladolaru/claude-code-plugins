"""Briefing generation for the iterative review loop.

Formats evaluation briefings (findings + actions) and completion summaries
for the main session. Follows the curated-context-pipeline pattern:
SITUATION / ACTIONS / HANDOFF structure in the briefing headers.
"""


def format_evaluation_briefing(
    findings,
    round_num,
    merge_base,
    diff_lines,
    outcomes_path=None,
):
    """Format the evaluation briefing for a round's findings."""
    lines = []

    # Header
    lines.append(f"{'═' * 55}")
    lines.append(f"ITERATIVE REVIEW — Review Round {round_num}: Evaluate")
    lines.append(f"{'═' * 55}")
    lines.append("")
    lines.append(f"Reviewing against merge base {merge_base} ({diff_lines} lines changed)")
    lines.append("")

    # Check if all P3
    all_p3 = all(f["severity"] == "P3" for f in findings) if findings else False

    # Findings
    lines.append(f"## FINDINGS ({len(findings)})")
    lines.append("")
    for f in findings:
        lines.append(f"[{f['id']}] [{f['severity']}] {f['title']}")
        lines.append(f"  {f['location']}")
        lines.append(f"  {f['body']}")
        lines.append("")

    if all_p3 and findings:
        lines.append(
            "All findings this round are P3 (suggestions). Address them as appropriate,\n"
            "then the review loop will complete — no further rounds needed."
        )
        lines.append("")

    # Actions — Phase 1: Evaluate Findings
    lines.append("## ACTIONS")
    lines.append("")
    lines.append("### Phase 1: Evaluate Findings")
    lines.append("")
    lines.append("The independent reviewer is an external process. It may lack context, misread intent, or")
    lines.append("flag code that's correct for reasons it can't see. Do not accept or reject")
    lines.append("findings on face value.")
    lines.append("")
    lines.append("For each finding, work through these steps before deciding:")
    lines.append("")
    lines.append("  1. READ the code at the referenced location and its surrounding context.")
    lines.append("     Open the actual file — do not rely on the finding description alone.")
    lines.append("")
    lines.append("  2. VERIFY what the code actually does. State it in your own words.")
    lines.append("     If the finding references specific behavior, quote the relevant lines.")
    lines.append("")
    lines.append("  3. EVALUATE whether the finding's claim matches the code reality.")
    lines.append("     Separate what the code does from what the finding says it does.")
    lines.append("")
    lines.append("  4. DECIDE: fixed | rejected | deferred")
    lines.append("     - fixed: the problem is real — proceed to fix discipline below")
    lines.append("     - rejected: the code is correct — state the specific evidence")
    lines.append("     - deferred: valid but out of scope — note why")
    lines.append("     If you cannot verify a claim after investigation, say so rather than guessing.")
    lines.append("")

    # Cognitive traps (round 1 only — primes the evaluation posture)
    if round_num == 1:
        lines.append("Cognitive traps to avoid:")
        lines.append("  - Rubber-stamping: accepting a finding without reading the code because")
        lines.append("    'the reviewer found it'. READ the actual code first — always.")
        lines.append("  - Positional entrenchment (later rounds): rejecting a re-flagged finding")
        lines.append("    to defend a prior decision. Verify against the code, not your prior reasoning.")
        lines.append("  - Scope inflation: fixing tangentially related code the finding didn't flag.")
        lines.append("    The branch's mandate is the boundary.")
        lines.append("")

    # Actions — Phase 2: Fix
    lines.append("### Phase 2: Fix")
    lines.append("")
    lines.append("Fix discipline — right-size the fix based on where the root cause lives:")
    lines.append("")
    lines.append("  Pre-existing code (before this branch):")
    lines.append("  - Minimal, targeted fix — do not refactor code that worked before this branch")
    lines.append("  - If the right fix requires changing pre-existing code significantly, defer it")
    lines.append("  - A 3-line fix for a P1 in pre-existing code is better than a 30-line refactor")
    lines.append("")
    lines.append("  Our branch's code (changes we introduced):")
    lines.append("  - If the finding is a symptom of a design decision we made, question the approach")
    lines.append("  - Refactoring our own code is not scope creep — it's fixing our work properly")
    lines.append("  - Patching symptoms of a flawed design burns rounds; fixing the root cause converges faster")
    lines.append("  - Do not add abstractions or configurability for a single fix — but do reconsider")
    lines.append("    the structure if multiple findings point to the same design problem")
    lines.append("")
    lines.append("Sweep — before fixing, check for siblings within the branch's scope:")
    lines.append("  - The independent reviewer sees the diff, not the full codebase — it may")
    lines.append("    flag one instance of a problem that exists in several places we changed")
    lines.append("  - For each accepted finding, quickly check whether the same pattern appears")
    lines.append("    elsewhere in files this branch already touches or code closely related to")
    lines.append("    the branch's purpose")
    lines.append("  - Fix in-scope siblings now — this avoids a round where the reviewer spots them")
    lines.append("  - Do NOT expand into unrelated code. A finding about one endpoint does not")
    lines.append("    justify sweeping every other endpoint. The branch's mandate is the boundary.")
    lines.append("  - For important siblings outside scope, note them as follow-ups (they belong")
    lines.append("    in the PR description, not in this branch's changes)")
    lines.append("")
    lines.append("Implement — dispatch accepted fixes to subagents:")
    lines.append("  - Group independent fixes and dispatch in parallel")
    lines.append("  - Each subagent gets: the finding, the relevant code context, what to fix")
    lines.append("")
    lines.append("Verify — after all subagents complete:")
    lines.append("  - Run tests/build/lint")
    lines.append("  - Confirm fixes don't introduce regressions")
    lines.append("")

    # Actions — Phase 3: Commit and Record
    lines.append("### Phase 3: Commit and Record")
    lines.append("")
    lines.append("Commit — stage and commit fixes with semantic messages before advancing:")
    lines.append("  - Each fix should be its own logical commit (not a blanket 'fix review findings')")
    lines.append("  - The next review round reviews committed changes against the merge base")
    lines.append("  - Uncommitted fixes are invisible to the independent reviewer")
    lines.append("")
    outcomes_target = (
        f"`{outcomes_path}`" if outcomes_path else "the round outcomes artifact"
    )
    lines.append(
        f"Record — write {outcomes_target} with one entry per finding:"
    )
    lines.append("")
    lines.append(f'  [{{"id": "r{round_num}_f1", "severity": "P1", "action": "fixed", "summary": "What was fixed."}},')
    lines.append(f'   {{"id": "r{round_num}_f2", "severity": "P0", "action": "rejected", "reasoning": "Why it was rejected."}},')
    lines.append(f'   {{"id": "r{round_num}_f3", "severity": "P3", "action": "deferred", "reasoning": "Why it was deferred."}}]')
    lines.append("")
    lines.append("  Every finding ID must have an outcome. Copy severity from the finding.")
    lines.append("  Use 'summary' for fixed, 'reasoning' for rejected/deferred.")

    # Stalemate-breaking + correction prompt (round 2+ only)
    if round_num >= 2:
        lines.append("")
        lines.append("Findings that revisit issues from previous rounds:")
        lines.append("  When re-evaluating a finding you previously rejected, verify against the")
        lines.append("  code — not against your prior reasoning. If your prior rejection was wrong,")
        lines.append("  state specifically what was wrong in your thinking and write it into the")
        lines.append("  outcome's reasoning field. Do not defend why you pushed back — state the")
        lines.append("  correction factually and move on. If standing firm, defer and proceed.")
        lines.append("  Decisions that depart from the original spec go in the PR description.")
        lines.append("  Stalemates waste rounds.")

        if round_num >= 3:
            lines.append("")
            lines.append("Stalemate escalation (round 3+):")
            lines.append("  If you are rejecting the same type of finding for the third time,")
            lines.append("  force-defer it with a note for the PR author rather than burning")
            lines.append("  another round. Repeated rejection of a recurring theme means you and")
            lines.append("  the reviewer disagree — escalate to a human, don't loop.")

    return "\n".join(lines)


def format_timeout_briefing(round_num, timeout_seconds, autonomous=False,
                            at_round_cap=False):
    """Format briefing when the review backend times out.

    Interactive: asks the LLM to surface the timeout to the user with options.
    At the round cap, skip is not offered (would exceed the configured budget).
    Autonomous at-cap termination is handled by the caller — this function
    only emits the autonomous briefing when there are rounds remaining.
    """
    timeout_min = timeout_seconds // 60
    lines = []
    lines.append(f"{'═' * 55}")
    lines.append(f"ITERATIVE REVIEW — Review Round {round_num}: Reviewer Timeout")
    lines.append(f"{'═' * 55}")
    lines.append("")
    lines.append(f"The independent reviewer did not respond within {timeout_min} minutes.")
    lines.append("This is an infrastructure issue, not a code quality signal.")
    lines.append("")

    if autonomous:
        lines.append("## ACTION (autonomous mode)")
        lines.append("")
        lines.append(f"The reviewer timed out. Round {round_num} was skipped.")
        lines.append(f"Proceed to review round {round_num + 1} by running:")
        lines.append(f"  --action review --round {round_num + 1}")
    else:
        lines.append("## ACTION (interactive mode)")
        lines.append("")
        lines.append("Surface this to the user and ask how to proceed:")
        lines.append("")
        lines.append(f"  **The independent reviewer timed out** after {timeout_min} minutes on review round {round_num}.")
        lines.append("  Options:")
        lines.append(f"  1. **Retry** — re-run `--action review --round {round_num}` (same round)")
        if not at_round_cap:
            lines.append(f"  2. **Skip** — proceed directly to round {round_num + 1} via `--action review --round {round_num + 1}`")
        lines.append(f"  {'3' if not at_round_cap else '2'}. **Stop** — do not run any more review commands; report the timeout to the user and end the review")

    return "\n".join(lines)


_TERMINATION_REASONS = {
    "zero_findings": "The independent reviewer found no issues — the code is clean.",
    "all_rejected": "No code changes needed — findings were rejected or deferred.",
    "nitpicks_only": "Only P3 suggestions remain — addressed as appropriate.",
    "max_rounds": "Maximum review rounds reached.",
    "hard_limit": "Hard round limit reached.",
    "backend_unavailable": "Review backend became unavailable.",
    "backend_timeout": "The independent reviewer timed out on consecutive rounds.",
    "backend_timeout_at_cap": "The independent reviewer timed out on the last allowed round.",
}


def format_completion_briefing(termination, rounds_completed, total_fixed,
                                total_rejected, total_deferred):
    """Format the loop completion summary."""
    lines = []
    lines.append(f"{'═' * 55}")
    lines.append("ITERATIVE REVIEW — Review Loop Complete")
    lines.append(f"{'═' * 55}")
    lines.append("")
    lines.append(f"Result: {_TERMINATION_REASONS.get(termination, termination)}")
    lines.append(f"Rounds completed: {rounds_completed}")
    lines.append(f"Fixed: {total_fixed} | Rejected: {total_rejected} | Deferred: {total_deferred}")
    lines.append("")
    lines.append("Report these results to the user. If there are deferred items in")
    lines.append(
        "the loop result, list them as follow-ups for the PR description."
    )
    return "\n".join(lines)


def format_degraded_briefing(round_num, raw_id, outcomes_path=None):
    """Format briefing when the review backend returned unstructured output."""
    lines = []
    lines.append(f"{'═' * 55}")
    lines.append(f"ITERATIVE REVIEW — Review Round {round_num}: Evaluate (Degraded)")
    lines.append(f"{'═' * 55}")
    lines.append("")
    lines.append(
        "The independent reviewer returned unstructured output instead of structured findings."
    )
    lines.append("")
    lines.append("Read the raw output below and evaluate it as a single finding:")
    lines.append("  - Identify any actionable issues in the review text")
    lines.append("  - Evaluate each issue against the code (READ → VERIFY → EVALUATE → DECIDE)")
    lines.append("  - Assess overall severity (P0-P3) across all issues found")
    lines.append("")
    outcomes_target = (
        f"`{outcomes_path}`" if outcomes_path else "the round outcomes artifact"
    )
    lines.append(f"Write exactly one outcome to {outcomes_target}")
    lines.append(f"with `{raw_id}` as the finding ID. Set severity to your assessed level (P0-P3).")
    lines.append("Pick the action using this priority:")
    lines.append("  - If any issues were fixed → action: fixed (ensures another review pass)")
    lines.append("  - Else if any deferred → action: deferred")
    lines.append("  - Else → action: rejected")
    lines.append("Use 'summary' for fixed, 'reasoning' for rejected/deferred.")
    lines.append("Include the full breakdown (which issues fixed, rejected, deferred) in that field.")
    lines.append("If there are deferred items, call them out explicitly so the completion step")
    lines.append("can surface them as PR follow-ups.")
    return "\n".join(lines)

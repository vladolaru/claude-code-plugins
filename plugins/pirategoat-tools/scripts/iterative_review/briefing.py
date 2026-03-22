"""Briefing generation for the iterative review loop.

Formats evaluation briefings (findings + actions) and completion summaries
for the main session. Follows the curated-context-pipeline pattern:
SITUATION / ACTIONS / HANDOFF structure in the briefing headers.
"""


def format_evaluation_briefing(findings, round_num, merge_base, diff_lines):
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
    lines.append("Codex is an external reviewer. It may lack context, misread intent, or")
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
    lines.append("  - Uncommitted fixes are invisible to Codex")
    lines.append("")
    lines.append(f"Record — write round-{round_num}-outcomes.json with one entry per finding:")
    lines.append("")
    lines.append(f'  [{{"id": "r{round_num}_f1", "action": "fixed", "summary": "What was fixed."}},')
    lines.append(f'   {{"id": "r{round_num}_f2", "action": "rejected", "reasoning": "Why it was rejected."}},')
    lines.append(f'   {{"id": "r{round_num}_f3", "action": "deferred", "reasoning": "Why it was deferred."}}]')
    lines.append("")
    lines.append("  Every finding ID must have an outcome. Use 'summary' for fixed, 'reasoning' for rejected/deferred.")

    # Stalemate-breaking prompt (round 2+ only)
    if round_num >= 2:
        lines.append("")
        lines.append("Findings that revisit issues from previous rounds: decide and move on.")
        lines.append("Changed your mind from a prior round? Note why. Standing firm? Defer and")
        lines.append("proceed. Decisions that depart from the original spec go in the PR description.")
        lines.append("Stalemates waste rounds.")

    return "\n".join(lines)


_TERMINATION_REASONS = {
    "zero_findings": "Codex found no issues — the code is clean.",
    "all_rejected": "All findings were rejected — no code changes needed.",
    "nitpicks_only": "Only P3 suggestions remain — addressed as appropriate.",
    "max_rounds": "Maximum review rounds reached.",
    "hard_limit": "Hard round limit reached.",
    "codex_unavailable": "Codex CLI became unavailable.",
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
    lines.append("review-loop-result.json, list them as follow-ups for the PR description.")
    return "\n".join(lines)


def format_degraded_briefing(round_num, raw_id):
    """Format briefing when Codex returned unstructured output."""
    lines = []
    lines.append(f"{'═' * 55}")
    lines.append(f"ITERATIVE REVIEW — Review Round {round_num}: Evaluate (Degraded)")
    lines.append(f"{'═' * 55}")
    lines.append("")
    lines.append(
        "Codex returned unstructured output instead of structured findings."
    )
    lines.append("")
    lines.append("Read the raw output below and evaluate it as a single finding:")
    lines.append("  - Identify any actionable issues in the review text")
    lines.append("  - Evaluate each issue against the code (READ → VERIFY → EVALUATE → DECIDE)")
    lines.append("  - Assess overall severity (P0-P3) across all issues found")
    lines.append("")
    lines.append(f"Write exactly one outcome to round-{round_num}-outcomes.json")
    lines.append(f"with `{raw_id}` as the finding ID. Choose the action (fixed/rejected/deferred)")
    lines.append("that best represents the overall resolution. If issues have mixed resolutions,")
    lines.append("note the breakdown in the summary field.")
    return "\n".join(lines)

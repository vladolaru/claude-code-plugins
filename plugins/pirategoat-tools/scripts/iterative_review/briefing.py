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

    # Actions
    lines.append("## ACTIONS")
    lines.append("")
    lines.append("Triage — for each finding, verify the claim at the referenced code location:")
    lines.append("  - If real: mark for fix")
    lines.append("  - If wrong: note the technical reason (reference the code that disproves it)")
    lines.append("  - If valid but out of scope: note why")
    lines.append("")
    lines.append("Fix discipline — keep fixes minimal and proportionate:")
    lines.append("  - Fix the specific issue, nothing more")
    lines.append("  - Do not refactor surrounding code while you're here")
    lines.append("  - Do not add abstractions, helpers, or configurability for a single fix")
    lines.append("  - A 3-line fix for a P1 is better than a 30-line refactor")
    lines.append("  - If the right fix feels large, defer it — it belongs in a separate PR")
    lines.append("")
    lines.append("Implement — dispatch accepted fixes to subagents:")
    lines.append("  - Group independent fixes and dispatch in parallel")
    lines.append("  - Each subagent gets: the finding, the relevant code context, what to fix")
    lines.append("")
    lines.append("Verify — after all subagents complete:")
    lines.append("  - Run tests/build/lint")
    lines.append("  - Confirm fixes don't introduce regressions")
    lines.append("")
    lines.append("Commit — stage and commit fixes with semantic messages before advancing:")
    lines.append("  - Each fix should be its own logical commit (not a blanket 'fix review findings')")
    lines.append("  - The next review round reviews committed changes against the merge base")
    lines.append("  - Uncommitted fixes are invisible to Codex")
    lines.append("")
    lines.append(f"Record — write round-{round_num}-outcomes.json")

    # Stalemate-breaking prompt (round 2+ only)
    if round_num >= 2:
        lines.append("")
        lines.append("If you find yourself reconsidering a finding you already addressed in a")
        lines.append("previous round, make a decision and own it. If you've changed your mind,")
        lines.append("note why. If you stand firm, defer the finding and move on — stalemates")
        lines.append("waste rounds. If your decision departs from the original spec, flag it")
        lines.append("explicitly for the PR description.")

    return "\n".join(lines)


def format_completion_briefing(termination, rounds_completed, total_fixed,
                                total_rejected, total_deferred):
    """Format the loop completion summary."""
    lines = []
    lines.append(f"{'═' * 55}")
    lines.append("ITERATIVE REVIEW — Review Loop Complete")
    lines.append(f"{'═' * 55}")
    lines.append("")
    lines.append(f"Termination: {termination}")
    lines.append(f"Rounds completed: {rounds_completed}")
    lines.append(f"Total fixed: {total_fixed}")
    lines.append(f"Total rejected: {total_rejected}")
    lines.append(f"Total deferred: {total_deferred}")
    return "\n".join(lines)


def format_degraded_briefing(round_num, raw_id):
    """Format briefing when Codex returned unstructured output."""
    lines = []
    lines.append(f"{'═' * 55}")
    lines.append(f"ITERATIVE REVIEW — Review Round {round_num}: Evaluate (Degraded)")
    lines.append(f"{'═' * 55}")
    lines.append("")
    lines.append(
        "Codex returned unstructured output. Read the raw output below "
        "and evaluate manually. Write outcomes referencing `" + raw_id + "`."
    )
    return "\n".join(lines)

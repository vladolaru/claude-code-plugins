"""CLI entry point for the iterative review loop.

Two actions:
  review  -- Invoke Codex and produce evaluation briefing (run in background)
  advance -- Read outcomes, update pushback log, check convergence
"""

import argparse
import json
import os
import re
import subprocess as sp
import sys
from copy import deepcopy
from pathlib import Path

from .loop import (
    read_loop_state, write_loop_state, DEFAULT_STATE, MAX_ROUNDS_HARD_LIMIT,
    compute_max_rounds, compute_relevant_diff_size, check_convergence,
    build_pushback_entry, append_pushback_log, read_pushback_log,
    append_deferred_item, validate_outcomes, outcome_severity,
)
from .briefing import (
    format_evaluation_briefing, format_completion_briefing,
    format_degraded_briefing, format_timeout_briefing,
)
from .effort import resolve_effort
from .telemetry import ReviewTelemetry
from .backends.codex import (
    parse_codex_output, write_prompt_file, get_schema_path, get_rubric,
    invoke_codex_review, check_codex_auth, TIMEOUT_SENTINEL, CODEX_TIMEOUT,
)


def _compute_diff_lines(merge_base):
    """Compute noise-filtered diff line count for merge_base..HEAD.

    Returns (diff_lines, excluded_count) or (0, 0) on failure.
    Extracted so it can be called both at round 1 init and on later
    rounds when adaptive effort needs a fresh diff size.
    """
    try:
        toplevel = sp.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True
        ).stdout.strip()
        git_cwd = toplevel if toplevel else None

        result = sp.run(
            ["git", "diff", "--name-only", f"{merge_base}..HEAD"],
            capture_output=True, text=True, cwd=git_cwd
        )
        all_files = [f for f in result.stdout.strip().split("\n") if f]
        relevant, excluded = compute_relevant_diff_size(all_files)
        if relevant:
            stat_result = sp.run(
                ["git", "diff", "--stat", f"{merge_base}..HEAD", "--"] + relevant,
                capture_output=True, text=True, cwd=git_cwd
            )
            m = re.search(r'(\d+) insertions?\(\+\)', stat_result.stdout)
            ins = int(m.group(1)) if m else 0
            m = re.search(r'(\d+) deletions?\(-\)', stat_result.stdout)
            dels = int(m.group(1)) if m else 0
            return ins + dels, excluded
        return 0, excluded
    except Exception:
        return 0, 0


def _sanitize_filename(name):
    """Convert a string to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = slug.replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    slug = slug.strip("-")[:60]
    return slug or "independent-review"


def _collect_resolved_locations(output_dir, rounds):
    """Collect (title, location) pairs that were fixed or rejected in any round."""
    resolved = set()
    for r in rounds:
        round_num = r["round"]
        findings_path = os.path.join(output_dir, f"round-{round_num}-findings.json")
        outcomes_path = os.path.join(output_dir, f"round-{round_num}-outcomes.json")
        try:
            with open(findings_path) as f:
                findings = json.load(f)
            with open(outcomes_path) as f:
                outcomes = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        findings_by_id = {fnd["id"]: fnd for fnd in findings}
        for o in outcomes:
            if o["action"] in ("fixed", "rejected"):
                fnd = findings_by_id.get(o["id"], {})
                key = (fnd.get("title", ""), fnd.get("location", ""))
                if key != ("", ""):
                    resolved.add(key)
    return resolved


def _write_loop_result(output_dir, state, termination):
    """Write review-loop-result.json with cumulative stats and deferred items."""
    from .loop import read_deferred_items

    rounds = state.get("rounds", [])
    all_deferred = read_deferred_items(output_dir)

    # Prune deferred items that were resolved in a later round
    resolved = _collect_resolved_locations(output_dir, rounds)
    deferred_items = [
        item for item in all_deferred
        if (item.get("title", ""), item.get("location", "")) not in resolved
    ]

    result_data = {
        "termination": termination,
        "rounds_completed": len(rounds),
        "max_rounds": state.get("max_rounds", 3),
        "total_findings": sum(r.get("findings", 0) for r in rounds),
        "total_fixed": sum(r.get("fixed", 0) for r in rounds),
        "total_rejected": sum(r.get("rejected", 0) for r in rounds),
        "total_deferred": sum(r.get("deferred", 0) for r in rounds),
        "deferred_items": deferred_items,
        "effort_profile": [r.get("effort") for r in rounds],
        "rounds": rounds,
    }
    result_path = os.path.join(output_dir, "review-loop-result.json")
    with open(result_path, "w") as f:
        json.dump(result_data, f, indent=2)
    return result_data


def _preflight_codex_cli():
    """Check that Codex CLI is available and authenticated.

    Returns None on success, or an error message string on failure.
    Called once before any expensive work (prompt composition, diff computation).
    """
    import shutil
    if not shutil.which("codex"):
        return (
            "UNAVAILABLE: Codex CLI is not installed or not on PATH.\n"
            "The iterative review loop requires the Codex CLI to run."
        )
    authenticated, err = check_codex_auth()
    if not authenticated:
        return (
            "UNAVAILABLE: Codex CLI is not authenticated.\n"
            f"Auth check failed: {err}\n"
            "The iterative review loop requires an authenticated Codex CLI."
        )
    return None


def action_review(args):
    """REVIEW action -- invoke Codex, parse output, produce evaluation briefing."""
    output_dir = args.output_dir
    round_num = args.round
    os.makedirs(output_dir, exist_ok=True)

    # Pre-flight: verify Codex CLI is available and authenticated before
    # spending time on prompt composition, diff computation, etc.
    preflight_err = _preflight_codex_cli()
    if preflight_err:
        # Log telemetry before writing result
        telemetry = ReviewTelemetry(output_dir)
        telemetry.progress("codex_unavailable", round=round_num)
        telemetry.pipeline_event("codex_unavailable", round=round_num)
        # Write structured result so callers can detect the condition
        # programmatically (same shape as normal termination).
        result_data = {
            "termination": "codex_unavailable",
            "rounds_completed": 0,
            "total_findings": 0,
            "total_fixed": 0,
            "total_rejected": 0,
            "total_deferred": 0,
            "rounds": [],
        }
        result_path = os.path.join(output_dir, "review-loop-result.json")
        with open(result_path, "w") as f:
            json.dump(result_data, f, indent=2)
        print(preflight_err)
        return

    telemetry = ReviewTelemetry(output_dir)
    state = read_loop_state(output_dir)

    # Round 1 initialization
    if round_num == 1:
        if not args.merge_base:
            print("ERROR: --merge-base is required on round 1", file=sys.stderr)
            sys.exit(2)

        state = deepcopy(DEFAULT_STATE)
        state["merge_base"] = args.merge_base
        state["current_round"] = 1

        # Clear stale artifacts from prior runs in the same directory
        import glob
        for stale in ["pushback-log.md", "deferred-items.jsonl",
                      "review-loop-state.json", "review-loop-result.json"]:
            stale_path = os.path.join(output_dir, stale)
            if os.path.isfile(stale_path):
                os.remove(stale_path)
        # Remove all round-specific files (findings, outcomes, prompts, raw output, analysis)
        for pattern in ["round-*-findings.json", "round-*-outcomes.json",
                        "round-*-prompt.md", "round-*-codex-output.json",
                        "round-*-codex-raw.md", "*-analysis.md"]:
            for f in glob.glob(os.path.join(output_dir, pattern)):
                os.remove(f)
        if args.no_prior_analysis:
            state["pass_prior_analysis"] = False
        if getattr(args, "adaptive_effort", False):
            state["adaptive_effort"] = True
        if getattr(args, "autonomous", False):
            state["autonomous"] = True

        # Read context file
        context = ""
        if args.context_file and os.path.isfile(args.context_file):
            with open(args.context_file) as f:
                context = f.read()
            state["context_file"] = args.context_file
            state["context"] = context

        # Compute diff size (noise-filtered)
        diff_lines, excluded = _compute_diff_lines(args.merge_base)
        state["diff_lines_relevant"] = diff_lines
        state["diff_lines_total"] = diff_lines + excluded  # approximate
        state["noise_files_excluded"] = excluded

        computed = compute_max_rounds(state["diff_lines_relevant"])
        if args.max_rounds:
            state["max_rounds"] = min(args.max_rounds, MAX_ROUNDS_HARD_LIMIT)
        else:
            state["max_rounds"] = computed

        # Resolve analysis doc prefix
        prefix = "independent-review"
        if args.analysis_prefix:
            prefix = _sanitize_filename(args.analysis_prefix)
        state["analysis_doc_prefix"] = prefix

        telemetry.pipeline_event("review_loop_started",
                                 max_rounds=state["max_rounds"],
                                 diff_lines_total=state.get("diff_lines_total", 0),
                                 diff_lines_relevant=state["diff_lines_relevant"],
                                 noise_files_excluded=state.get("noise_files_excluded", 0))
    else:
        # Round 2+: validate persisted state exists
        if not state.get("merge_base"):
            print(
                f"ERROR: No persisted loop state found for round {round_num}. "
                "Round 1 must run first to initialize state.",
                file=sys.stderr
            )
            sys.exit(2)
        if state["terminated"]:
            rounds = state.get("rounds", [])
            print(format_completion_briefing(
                state["termination"], len(rounds),
                sum(r.get("fixed", 0) for r in rounds),
                sum(r.get("rejected", 0) for r in rounds),
                sum(r.get("deferred", 0) for r in rounds),
            ))
            return
        # Enforce round cap — no review should run beyond the configured limit.
        # Normally action_advance checks this, but timeout skips bypass advance.
        max_rounds = state.get("max_rounds", 3)
        if round_num > max_rounds or round_num > MAX_ROUNDS_HARD_LIMIT:
            reason = "hard_limit" if round_num > MAX_ROUNDS_HARD_LIMIT else "max_rounds"
            state["terminated"] = True
            state["termination"] = reason
            write_loop_state(output_dir, state)
            result_data = _write_loop_result(output_dir, state, reason)
            print(format_completion_briefing(
                reason, result_data["rounds_completed"],
                result_data["total_fixed"], result_data["total_rejected"],
                result_data["total_deferred"]))
            return
        state["current_round"] = round_num
        context = state.get("context", "")

    # Backstop: detect uncommitted changes before Codex reviews.
    # Codex reviews git diff merge_base..HEAD — uncommitted work is invisible.
    # We warn but do NOT auto-commit: the operator should commit semantically.
    try:
        status = sp.run(["git", "status", "--porcelain"],
                        capture_output=True, text=True).stdout.strip()
        if status:
            tracked = [line for line in status.splitlines()
                       if line and not line.startswith("??")]
            untracked = [line[3:] for line in status.splitlines()
                         if line and line.startswith("??")]
            parts = []
            if tracked:
                parts.append(
                    f"{len(tracked)} uncommitted tracked change(s) — invisible to Codex:\n"
                    + "\n".join(f"  {line}" for line in tracked[:10])
                    + ("\n  ..." if len(tracked) > 10 else ""))
            if untracked:
                parts.append(
                    f"{len(untracked)} untracked file(s) — invisible to Codex:\n"
                    + "\n".join(f"  {f}" for f in untracked[:10])
                    + ("\n  ..." if len(untracked) > 10 else ""))
            print(
                f"BLOCKED: Uncommitted changes detected before review round {round_num}.\n"
                + "\n".join(parts)
                + "\n\nCodex only reviews committed changes (merge_base..HEAD)."
                "\nCommit these files with semantic commit messages, then re-run"
                f" this same command (--action review --round {round_num})."
            )
            telemetry.progress("uncommitted_changes_blocked", round=round_num,
                               tracked=len(tracked), untracked=len(untracked))
            sys.exit(1)
    except Exception:
        pass  # git not available — proceed, Codex will review what's committed

    # Reset progress log
    telemetry.reset_progress()
    telemetry.progress("round_started", round=round_num)

    # Compose prompt file
    pushback_log = read_pushback_log(output_dir)
    prefix = state.get("analysis_doc_prefix", "independent-review")
    analysis_path = os.path.join(output_dir, f"{prefix}-r{round_num}-analysis.md")

    prior_path = None
    if round_num > 1 and state.get("pass_prior_analysis", True):
        candidate = os.path.join(output_dir, f"{prefix}-r{round_num - 1}-analysis.md")
        if os.path.isfile(candidate):
            prior_path = candidate
        else:
            telemetry.progress("analysis_doc_missing", round=round_num,
                               msg=f"Prior analysis doc not found: {candidate}")

    # Write the prompt file using write_prompt_file from codex backend
    prompt_file = write_prompt_file(
        output_dir=output_dir,
        round_num=round_num,
        rubric=get_rubric(),
        merge_base=state["merge_base"],
        context=context if round_num == 1 else state.get("context", ""),
        pushback_log=pushback_log if pushback_log else None,
        analysis_doc_path=analysis_path,
        prior_analysis_path=prior_path,
    )

    context_chars = os.path.getsize(prompt_file) if os.path.isfile(prompt_file) else 0
    telemetry.progress("composing_context", round=round_num,
                       context_chars=context_chars, context_limit=50000)

    # Context size warning
    if context_chars > 50000:
        telemetry.progress("context_size_warning", round=round_num,
                           context_chars=context_chars)

    # Resolve adaptive effort level
    effort = None
    effort_reason = None
    adaptive_on = getattr(args, "adaptive_effort", False) or state.get("adaptive_effort", False)
    if adaptive_on:
        # Load prior round findings/outcomes for signal overrides
        prior_findings = None
        prior_outcomes = None
        if round_num > 1:
            prev = round_num - 1
            prior_findings_path = os.path.join(output_dir, f"round-{prev}-findings.json")
            prior_outcomes_path = os.path.join(output_dir, f"round-{prev}-outcomes.json")
            try:
                with open(prior_findings_path) as f:
                    prior_findings = json.load(f)
                with open(prior_outcomes_path) as f:
                    prior_outcomes = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                pass  # No prior data — resolve without signals

        effort, effort_reason = resolve_effort(
            round_num=round_num,
            prior_findings=prior_findings,
            prior_outcomes=prior_outcomes,
        )
        print(f"[adaptive-effort] Round {round_num}: {effort} ({effort_reason})")

    # Invoke Codex
    merge_base = state["merge_base"]
    codex_output_file = os.path.join(output_dir, f"round-{round_num}-codex-output.json")
    schema_file = get_schema_path()

    telemetry.progress("codex_invoked", round=round_num,
                       diff_lines=state.get("diff_lines_relevant", 0),
                       effort=effort, effort_reason=effort_reason,
                       msg=f"Reviewing against {merge_base}")
    telemetry.pipeline_event("review_round_started", round=round_num,
                             effort=effort, effort_reason=effort_reason)

    raw_output, success = invoke_codex_review(
        prompt_file=prompt_file,
        schema_file=schema_file,
        output_file=codex_output_file,
        effort=effort,
    )

    if raw_output == TIMEOUT_SENTINEL:
        # Codex timed out — mode determines how the round is handled.
        # Autonomous: record skipped round immediately (auto-skip is the only path).
        # Interactive: defer recording — the user may retry (same round) or skip.
        #   Eager recording would poison retry (duplicate round entry) and
        #   skip-via-advance (empty findings → false zero_findings convergence).
        autonomous = state.get("autonomous", False)
        consecutive = state.get("consecutive_timeouts", 0) + 1
        state["consecutive_timeouts"] = consecutive

        telemetry.progress("codex_timeout", round=round_num,
                           consecutive=consecutive, autonomous=autonomous)
        telemetry.pipeline_event("codex_timeout", round=round_num,
                                 consecutive=consecutive)

        # Check if at the round cap — skip would exceed the configured budget.
        max_rounds = state.get("max_rounds", 3)
        at_round_cap = round_num >= max_rounds or round_num >= MAX_ROUNDS_HARD_LIMIT

        if autonomous:
            # Write empty findings so the round is complete on disk
            findings_path = os.path.join(output_dir, f"round-{round_num}-findings.json")
            with open(findings_path, "w") as f:
                json.dump([], f)

            # Record round in state (skipped — not routed through advance)
            state.setdefault("rounds", []).append({
                "round": round_num, "findings": 0, "fixed": 0,
                "rejected": 0, "deferred": 0, "skipped": True,
                "effort": state.get("current_effort"),
            })

            # Terminate if: consecutive timeouts OR at the round cap
            if consecutive >= 2 or at_round_cap:
                reason = "codex_timeout" if consecutive >= 2 else (
                    "hard_limit" if round_num >= MAX_ROUNDS_HARD_LIMIT else "max_rounds")
                state["terminated"] = True
                state["termination"] = reason
                write_loop_state(output_dir, state)
                result_data = _write_loop_result(output_dir, state, reason)
                print(format_completion_briefing(
                    reason, result_data["rounds_completed"],
                    result_data["total_fixed"], result_data["total_rejected"],
                    result_data["total_deferred"]))
                return

        write_loop_state(output_dir, state)
        print(format_timeout_briefing(round_num, timeout_seconds=CODEX_TIMEOUT,
                                       autonomous=autonomous,
                                       at_round_cap=at_round_cap))
        return

    elif not success and not raw_output:
        telemetry.progress("codex_unavailable", round=round_num)
        telemetry.pipeline_event("codex_unavailable", round=round_num)
        # Write empty findings
        findings_path = os.path.join(output_dir, f"round-{round_num}-findings.json")
        with open(findings_path, "w") as f:
            json.dump([], f)
        state["terminated"] = True
        state["termination"] = "codex_unavailable"
        write_loop_state(output_dir, state)

        _write_loop_result(output_dir, state, "codex_unavailable")
        print("Codex CLI is unavailable. Review loop cannot proceed.")
        return

    # Codex responded — reset consecutive timeout counter
    state["consecutive_timeouts"] = 0

    # Parse output
    findings, degraded = parse_codex_output(raw_output, round_num)

    telemetry.progress("codex_completed", round=round_num,
                       findings_count=len(findings))
    telemetry.pipeline_event("codex_completed", round=round_num,
                             findings_count=len(findings))

    if degraded:
        raw_path = os.path.join(output_dir, f"round-{round_num}-codex-raw.md")
        with open(raw_path, "w") as f:
            f.write(raw_output)

    # Write findings
    findings_path = os.path.join(output_dir, f"round-{round_num}-findings.json")
    with open(findings_path, "w") as f:
        json.dump(findings, f, indent=2)

    # Check zero findings convergence
    if len(findings) == 0:
        state.setdefault("rounds", []).append({
            "round": round_num,
            "findings": 0,
            "fixed": 0,
            "rejected": 0,
            "deferred": 0,
            "effort": effort,
        })
        state["terminated"] = True
        state["termination"] = "zero_findings"
        write_loop_state(output_dir, state)

        result_data = _write_loop_result(output_dir, state, "zero_findings")
        telemetry.pipeline_event("review_loop_completed",
                                 termination="zero_findings",
                                 rounds_completed=result_data["rounds_completed"],
                                 effort_profile=result_data.get("effort_profile", []))
        print(format_completion_briefing("zero_findings", result_data["rounds_completed"],
                                         result_data["total_fixed"],
                                         result_data["total_rejected"],
                                         result_data["total_deferred"]))
        return

    state["current_effort"] = effort
    write_loop_state(output_dir, state)

    # Produce briefing
    if degraded:
        briefing = format_degraded_briefing(round_num, findings[0]["id"])
        briefing += "\n\n" + raw_output
    else:
        briefing = format_evaluation_briefing(
            findings, round_num,
            merge_base=merge_base,
            diff_lines=state.get("diff_lines_relevant", 0),
        )

    telemetry.progress("briefing_ready", round=round_num)
    print(briefing)


def action_advance(args):
    """ADVANCE action -- read outcomes, update pushback, check convergence."""
    output_dir = args.output_dir
    round_num = args.round

    telemetry = ReviewTelemetry(output_dir)
    state = read_loop_state(output_dir)

    if state.get("terminated"):
        rounds = state.get("rounds", [])
        print(format_completion_briefing(
            state["termination"], len(rounds),
            sum(r.get("fixed", 0) for r in rounds),
            sum(r.get("rejected", 0) for r in rounds),
            sum(r.get("deferred", 0) for r in rounds)))
        return

    # Read findings
    findings_path = os.path.join(output_dir, f"round-{round_num}-findings.json")
    try:
        with open(findings_path) as f:
            findings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"ERROR: Cannot read {findings_path}", file=sys.stderr)
        sys.exit(1)

    # Read outcomes
    outcomes_path = os.path.join(output_dir, f"round-{round_num}-outcomes.json")
    try:
        with open(outcomes_path) as f:
            outcomes = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"ERROR: Cannot read {outcomes_path}. Write outcomes before advancing.",
              file=sys.stderr)
        sys.exit(1)

    # Validate completeness
    missing, stray = validate_outcomes(findings, outcomes)
    if missing:
        print(f"ERROR: Missing outcomes for findings: {', '.join(missing)}",
              file=sys.stderr)
        sys.exit(1)
    if stray:
        print(f"ERROR: Outcome IDs not in findings: {', '.join(stray)}",
              file=sys.stderr)
        sys.exit(1)

    # Build round summary
    fixed = sum(1 for o in outcomes if o["action"] == "fixed")
    rejected = sum(1 for o in outcomes if o["action"] == "rejected")
    deferred = sum(1 for o in outcomes if o["action"] == "deferred")

    # Idempotency: skip mutation if this round was already recorded (retry scenario)
    already_recorded = any(r["round"] == round_num for r in state.get("rounds", []))

    if not already_recorded:
        # Update pushback log
        findings_by_id = {f["id"]: f for f in findings}
        round_header = f"### Round {round_num} -- {len(findings)} findings ({fixed} fixed, {rejected} rejected, {deferred} deferred)\n\n"
        entries = []
        for o in outcomes:
            finding = findings_by_id.get(o["id"], {})
            entry = build_pushback_entry(o, finding, round_num)
            if entry:
                entries.append(entry)

        if entries:
            append_pushback_log(output_dir, round_header + "\n".join(entries) + "\n")

        # Append deferred items
        for o in outcomes:
            if o["action"] == "deferred":
                finding = findings_by_id.get(o["id"], {})
                append_deferred_item(output_dir, {
                    "id": o["id"],
                    "severity": finding.get("severity", "unknown"),
                    "title": finding.get("title", ""),
                    "location": finding.get("location", ""),
                    "reasoning": o.get("reasoning", ""),
                })

        # Record round in state
        state.setdefault("rounds", []).append({
            "round": round_num,
            "findings": len(findings),
            "fixed": fixed,
            "rejected": rejected,
            "deferred": deferred,
            "effort": state.get("current_effort"),
        })

    findings_by_id = {f["id"]: f for f in findings}

    # Tiered round extension at the limit based on severity of fixed findings.
    # P0/P1 fixed → +2 rounds (something seriously wrong, needs deeper review)
    # P2 fixed → +1 round (real issues in new code deserve a follow-up pass)
    # P3 only → no extension
    # Only fixed findings trigger extension — rejected/deferred won't be addressed.
    max_rounds = state.get("max_rounds", 3)

    if round_num >= max_rounds and max_rounds < MAX_ROUNDS_HARD_LIMIT:
        fixed_severities = [
            outcome_severity(o, findings_by_id.get(o["id"]))
            for o in outcomes if o["action"] == "fixed"
        ]
        has_critical = any(s in ("P0", "P1") for s in fixed_severities)
        has_important = any(s == "P2" for s in fixed_severities)

        if has_critical:
            extension = 2
            reason = "p0_p1_at_limit"
        elif has_important:
            extension = 1
            reason = "p2_at_limit"
        else:
            extension = 0
            reason = None

        if extension > 0:
            max_rounds = min(max_rounds + extension, MAX_ROUNDS_HARD_LIMIT)
            state["max_rounds"] = max_rounds
            telemetry.pipeline_event("max_rounds_extended", round=round_num,
                                     new_max=max_rounds, reason=reason)

    # Check convergence
    all_p3 = all(
        outcome_severity(o, findings_by_id.get(o["id"])) == "P3"
        for o in outcomes
    ) if outcomes else False
    all_rej = fixed == 0  # no code changed
    termination = check_convergence(
        findings_count=len(findings), all_p3=all_p3, all_rejected=all_rej,
        current_round=round_num, max_rounds=max_rounds
    )

    telemetry.pipeline_event("evaluation_completed", round=round_num,
                             fixed=fixed, rejected=rejected, deferred=deferred)
    telemetry.pipeline_event("review_round_completed", round=round_num)

    if termination:
        state["terminated"] = True
        state["termination"] = termination
        write_loop_state(output_dir, state)

        result_data = _write_loop_result(output_dir, state, termination)

        telemetry.pipeline_event("review_loop_completed",
                                 termination=termination,
                                 rounds_completed=result_data["rounds_completed"],
                                 total_fixed=result_data["total_fixed"],
                                 total_rejected=result_data["total_rejected"],
                                 total_deferred=result_data["total_deferred"],
                                 effort_profile=result_data.get("effort_profile", []))

        print(format_completion_briefing(
            termination, result_data["rounds_completed"],
            result_data["total_fixed"], result_data["total_rejected"],
            result_data["total_deferred"]
        ))
    else:
        write_loop_state(output_dir, state)
        next_round = round_num + 1
        print(f"Review round {round_num} complete. Proceed to review round {next_round}.")


def main():
    parser = argparse.ArgumentParser(description="Iterative Codex review loop")
    parser.add_argument("--action", choices=["review", "advance"], required=True)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--merge-base", help="Merge base SHA (required on round 1)")
    parser.add_argument("--context-file", help="Path to context file (round 1 only)")
    parser.add_argument("--analysis-prefix", help="Prefix for analysis doc filenames")
    parser.add_argument("--max-rounds", type=int,
                        help="Override max rounds (capped at hard limit of 20)")
    parser.add_argument("--no-prior-analysis", action="store_true",
                        help="Disable reading prior round analysis docs")
    parser.add_argument("--adaptive-effort", action="store_true",
                        help="Enable adaptive reasoning effort per round")
    parser.add_argument("--autonomous", action="store_true",
                        help="Autonomous mode (no human present). Timeouts auto-skip; consecutive timeouts terminate.")

    args = parser.parse_args()

    if args.action == "review":
        action_review(args)
    elif args.action == "advance":
        action_advance(args)


if __name__ == "__main__":
    main()

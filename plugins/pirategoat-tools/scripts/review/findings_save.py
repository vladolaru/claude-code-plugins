#!/usr/bin/env python3
"""Findings Save — the reconciliator's validating save channel for
review-findings.json.

Sibling to critic.py's ``--save`` mode: this is the ONLY channel the
review-reconciliator agent is allowed to write review-findings.json through
(see agents/review-reconciliator.md). A raw write — a hand-rolled
``json.dump`` or the shared ``atomic_write_json`` used directly — closes the
gap this module exists to close, because nothing downstream validates a
hand-written ledger after the fact.

Actor-ownership violations are collected before the canonical document
validator runs. On ANY problem, nothing is written, and every problem is
echoed as its own ``REJECTED: <problem>`` line — this module's failure mode is
silence on disk, never a partial ledger.

The write itself goes through ``critic_adjustments.write_findings()`` — the
ONE sanctioned write path for review-findings.json, shared by both of its
writers (the reconciliator's first write via this module, and the critic
adjustments applier). This module adds no new writer; it only gates what
reaches the existing one.
"""

import argparse
import json
import os
import sys

try:
    from . import critic_adjustments
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import critic_adjustments


# The reconciled ledger's verdict vocabulary. Matches EXACTLY the range
# `derive_review_state()` (scripts/review/verdict_rules.py) can return — the
# function every ReviewOutputBuilder.to_dict() call computes the verdict
# through, including the reconciliator's own build in
# agents/review-reconciliator.md. Deliberately EXCLUDES 'not_applicable'
# from schemas/review-output.ts's broader `Verdict` type: that value is a
# per-reviewer abstention (`mark_not_applicable()`, which refuses to run
# if any finding was already recorded) that the reconciliator never emits —
# it always produces a reconciled ledger for the whole PR, never abstains
# from it. Literal, not imported: the shared derivation's verdict range is a
# small closed set, so this module keeps the reconciler-specific subset here.
RECONCILER_VERDICTS = ("block", "request_changes", "comment", "approve")

# Severities the breakdown echo reports, in the order the brief's format
# specifies. Deliberately excludes 'info' from VALID_SEVERITIES: the echo
# line mirrors the brief's literal format, which reports only these four.
_ECHO_SEVERITIES = ("critical", "high", "medium", "low")

FINDINGS_FILENAME = critic_adjustments.FINDINGS_FILENAME
CRITIC_OWNED_LEDGER_FIELDS = (
    critic_adjustments.APPLIED_IDS_KEY,
    critic_adjustments.REJECTED_ADJUSTMENTS_KEY,
    critic_adjustments.VERDICT_BEFORE_ADJUSTMENTS_KEY,
    critic_adjustments.INVALIDATED_ASSESSMENTS_KEY,
    "findings_removed_by_critic",
    "checks_removed_by_critic",
)


def _read_findings_json(path, problems):
    """Read the ``--findings`` input file as JSON.

    Records a problem (and returns None) instead of raising for every
    failure mode — absent, unreadable, or unparseable — so a bad path is
    just one more REJECTED line, matching critic.py's ``_read_required``/
    ``_read_json`` pair for the same reason: this function collects
    problems, it never crashes the caller.
    """
    if not path:
        problems.append("--findings is required")
        return None
    if not os.path.isfile(path):
        problems.append(f"--findings file not found: {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as err:
        problems.append(f"--findings could not be read ({path}): {err}")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        problems.append(f"--findings is not valid JSON ({path}): {err}")
        return None


def validate_findings(payload):
    """Validate a producer-authored canonical findings ledger.

    The lifecycle module owns the complete post-critic ledger contract. This
    producer gate adds only the actor boundary: the reconciliator cannot
    pre-author fields or per-entry provenance owned by the critic applier.
    """
    if not isinstance(payload, dict):
        return [f"{FINDINGS_FILENAME} must be a JSON object"]

    problems = []
    verdict = payload.get("verdict")
    if verdict not in RECONCILER_VERDICTS:
        problems.append(
            f"'verdict' must be one of {sorted(RECONCILER_VERDICTS)}, "
            f"got {verdict!r}"
        )

    actor_supplied = sorted(
        key for key in CRITIC_OWNED_LEDGER_FIELDS if key in payload
    )
    if actor_supplied:
        problems.append(
            "critic-owned lifecycle field(s): " + ", ".join(actor_supplied)
        )

    findings = payload.get("findings")
    if isinstance(findings, list):
        for idx, finding in enumerate(findings):
            if not isinstance(finding, dict):
                continue
            severity = finding.get("severity")
            if (
                "severity" in finding
                and severity not in critic_adjustments.VALID_SEVERITIES
            ):
                problems.append(
                    f"findings[{idx}]: invalid severity {severity!r}"
                )
            if "critic_adjustment" in finding:
                problems.append(
                    f"findings[{idx}]: critic_adjustment is script-owned "
                    "provenance"
                )
    checks = payload.get("checks")
    if isinstance(checks, list):
        for idx, check in enumerate(checks):
            if isinstance(check, dict) and "critic_adjustment" in check:
                problems.append(
                    f"checks[{idx}]: critic_adjustment is script-owned "
                    "provenance"
                )
    try:
        critic_adjustments.validate_findings_document(payload)
    except ValueError as err:
        canonical_problem = str(err)
        if canonical_problem not in problems:
            problems.append(canonical_problem)

    return problems


def _echo(findings):
    """Print the RECORDED lines the brief specifies for a successful save."""
    recorded_findings = findings.get("findings") or []
    counts = {sev: 0 for sev in _ECHO_SEVERITIES}
    for finding in recorded_findings:
        sev = finding.get("severity")
        if sev in counts:
            counts[sev] += 1
    breakdown = ", ".join(f"{sev} {counts[sev]}" for sev in _ECHO_SEVERITIES)

    checks = findings["checks"]
    assessment = findings.get("assessment")
    assessment_state = (
        "present"
        if isinstance(assessment, str) and assessment.strip()
        else "absent"
    )

    print(f"RECORDED VERDICT: {findings.get('verdict')}")
    print(f"RECORDED FINDINGS: {len(recorded_findings)} ({breakdown})")
    print(f"CHECKS: {len(checks)} | ASSESSMENT: {assessment_state}")


def run_save(args):
    """Validate and atomically record one reconciled findings ledger.

    Returns the process exit code (0 on success, 1 on rejection) rather
    than raising, so ``main()`` can ``sys.exit()`` it directly — the same
    shape ``critic.py``'s ``run_save()`` uses.
    """
    problems = []
    findings = _read_findings_json(args.findings, problems)
    if findings is not None:
        problems.extend(validate_findings(findings))

    if problems:
        for p in problems:
            print(f"REJECTED: {p}")
        return 1

    critic_adjustments.write_findings(args.output_dir, findings)
    _echo(findings)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Findings Save - validate and atomically record the "
            "review-reconciliator's review-findings.json ledger"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        required=True,
        help="Directory to write review-findings.json into",
    )
    parser.add_argument(
        "--findings",
        type=str,
        required=True,
        help="Path to the reconciled findings JSON to validate and record",
    )
    args = parser.parse_args()
    sys.exit(run_save(args))


if __name__ == "__main__":
    main()

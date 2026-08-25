#!/usr/bin/env python3
"""Findings Save — the reconciliator's validating save channel for
review-findings.json.

Sibling to critic.py's ``--save`` mode: this is the ONLY channel the
review-reconciliator agent is allowed to write review-findings.json through
(see agents/review-reconciliator.md). A raw write — a hand-rolled
``json.dump`` or the shared ``atomic_write_json`` used directly — closes the
gap this module exists to close, because nothing downstream validates a
hand-written ledger after the fact.

Every problem is collected before anything is decided, the same
all-or-nothing style ``critic_adjustments.validate_adjustments_document()``
and ``critic.py``'s ``run_save()`` use: a bad verdict, a missing required finding
field, and a summary/findings count mismatch are independent facts, and
reporting only the first would make a caller fix one problem at a time
instead of seeing the whole rejection at once. On ANY problem, nothing is
written, and every problem is echoed as its own ``REJECTED: <problem>``
line — this module's failure mode is silence on disk, never a partial
ledger.

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
    from .agent import output as review_output
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import critic_adjustments
    from review.agent import output as review_output


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

# Fields every finding entry must carry to be a well-formed ReviewOutputBuilder
# finding (see Finding in schemas/review-output.ts and add_finding() in
# review/agent/output.py, which always sets exactly these. ``line`` is
# required but nullable: null identifies a file-scoped finding, while an
# absent key is a malformed finding that the renderer cannot consume.
REQUIRED_FINDING_FIELDS = (
    "id", "category", "severity", "title", "description", "file",
    "line", "recommendation", "confidence",
)

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
    """Validate the SHAPE of a review-findings.json document.

    Returns a list of human-readable problems; an empty list means valid.
    Checks: top-level object; ``verdict`` in the reconciler vocabulary;
    ``findings`` is a list of well-formed entries, ``checks`` is always a
    list, ``assessment`` is string-or-null, and ``summary`` counts match the
    ``findings``
    population, reusing ``critic_adjustments._recount_summary()`` — the
    same recount the ledger's other writers already trust — rather than
    re-deriving the count logic here.
    """
    if not isinstance(payload, dict):
        return [f"{FINDINGS_FILENAME} must be a JSON object"]

    problems = []

    if (
        type(payload.get("schema")) is not int
        or payload.get("schema") != review_output.REVIEW_OUTPUT_SCHEMA
    ):
        problems.append(
            f"'schema' must be the exact integer "
            f"{review_output.REVIEW_OUTPUT_SCHEMA}"
        )

    verdict = payload.get("verdict")
    if verdict not in RECONCILER_VERDICTS:
        problems.append(
            f"'verdict' must be one of {sorted(RECONCILER_VERDICTS)}, "
            f"got {verdict!r}"
        )

    retired = sorted(
        key
        for key in ("issues", "clearances", "narrative_summary")
        if key in payload
    )
    if retired:
        problems.append(
            "retired review-domain field(s): " + ", ".join(retired)
        )
    meta = payload.get("meta")
    if isinstance(meta, dict) and "tool_results_used" in meta:
        problems.append(
            "retired review-domain field: meta.tool_results_used"
        )
    actor_supplied = sorted(
        key for key in CRITIC_OWNED_LEDGER_FIELDS if key in payload
    )
    if actor_supplied:
        problems.append(
            "critic-owned lifecycle field(s): " + ", ".join(actor_supplied)
        )

    findings = payload.get("findings")
    if not isinstance(findings, list):
        problems.append("'findings' must be a list")
        findings = []

    for idx, finding in enumerate(findings):
        label = f"findings[{idx}]"
        if not isinstance(finding, dict):
            problems.append(f"{label} must be an object")
            continue
        missing = [
            field
            for field in REQUIRED_FINDING_FIELDS
            if field not in finding
        ]
        if missing:
            problems.append(
                f"{label}: missing required field(s) {', '.join(missing)}"
            )
            continue
        severity = finding.get("severity")
        if severity not in critic_adjustments.VALID_SEVERITIES:
            problems.append(
                f"{label}: invalid severity {severity!r} "
                f"(allowed: {', '.join(critic_adjustments.VALID_SEVERITIES)})"
            )
        if "critic_adjustment" in finding:
            problems.append(
                f"{label}: critic_adjustment is script-owned provenance"
            )

    if "checks" not in payload:
        problems.append("'checks' must be present as a list")
    if "assessment" not in payload:
        problems.append("'assessment' must be present as a string or null")
    checks = payload.get("checks")
    if isinstance(checks, list):
        for idx, check in enumerate(checks):
            if isinstance(check, dict) and "critic_adjustment" in check:
                problems.append(
                    f"checks[{idx}]: critic_adjustment is script-owned "
                    "provenance"
                )
    if isinstance(findings, list) and not problems:
        try:
            review_output.validate_review_domain(
                findings,
                payload.get("checks"),
                payload.get("assessment"),
                payload.get("meta"),
            )
        except ValueError as err:
            problems.append(str(err))

    # Summary consistency only makes sense once findings are well-formed —
    # otherwise the recount itself would raise on the same defect already
    # reported above (a non-object entry, an out-of-vocabulary severity).
    if isinstance(payload.get("findings"), list) and not problems:
        scratch = {}
        try:
            derived = critic_adjustments._recount_summary(scratch, findings)
        except ValueError as err:
            problems.append(str(err))
        else:
            expected_verdict = derived["verdict"]
            if verdict != expected_verdict:
                problems.append(
                    f"'verdict' {verdict!r} does not match the "
                    f"findings-derived verdict {expected_verdict!r}"
                )
            expected = scratch["summary"]
            actual = payload.get("summary")
            if not isinstance(actual, dict):
                problems.append("'summary' must be an object")
            else:
                if (
                    actual.get("total_findings")
                    != expected["total_findings"]
                ):
                    problems.append(
                        "'summary.total_findings' "
                        f"({actual.get('total_findings')!r}) does not match "
                        f"the {expected['total_findings']} finding(s) recorded"
                    )
                if actual.get("by_severity") != expected["by_severity"]:
                    problems.append(
                        "'summary.by_severity' "
                        f"({actual.get('by_severity')!r}) does not match "
                        f"the findings' actual severities "
                        f"({expected['by_severity']!r})"
                    )
                if (
                    actual.get("suppressed_advisory_finding_count")
                    != expected["suppressed_advisory_finding_count"]
                ):
                    problems.append(
                        "'summary.suppressed_advisory_finding_count' "
                        f"({actual.get('suppressed_advisory_finding_count')!r}) "
                        "does not match the findings-derived count "
                        f"({expected['suppressed_advisory_finding_count']!r})"
                    )
                if actual.get("verdict_without_advisory") != expected.get(
                    "verdict_without_advisory"
                ):
                    problems.append(
                        "'summary.verdict_without_advisory' does not match "
                        "the findings-derived verdict"
                    )
                unexpected_summary = sorted(set(actual) - set(expected))
                if unexpected_summary:
                    problems.append(
                        "'summary' has unexpected field(s): "
                        + ", ".join(unexpected_summary)
                    )

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

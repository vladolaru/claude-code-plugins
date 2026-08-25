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
and ``critic.py``'s ``run_save()`` use: a bad verdict, a missing required issue
field, and a summary/issues count mismatch are independent facts, and
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
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import critic_adjustments


# The reconciled ledger's verdict vocabulary. Matches EXACTLY the range
# `_verdict_for_issues()` (scripts/review/agent/output.py) can return —
# the function every ReviewOutputBuilder.to_dict() call computes the
# verdict through, including the reconciliator's own build in
# agents/review-reconciliator.md. Deliberately EXCLUDES 'not_applicable'
# from schemas/review-output.ts's broader `Verdict` type: that value is a
# per-reviewer abstention (`mark_not_applicable()`, which refuses to run
# if any issue was already recorded) that the reconciliator never emits —
# it always produces a reconciled ledger for the whole PR, never abstains
# from it. Literal, not imported: output.py's `_VERDICT_RANK`/
# `_verdict_for_issues` are the canonical source and are not part of its
# public surface, so this constant is kept as its own spelling rather than
# reaching into another module's private names for a small closed set of
# strings.
RECONCILER_VERDICTS = ("block", "request_changes", "comment", "approve")

# Fields every issue entry must carry to be a well-formed ReviewOutputBuilder
# issue (see Issue in schemas/review-output.ts and add_issue() in
# review/agent/output.py, which always sets exactly these plus 'line' and
# 'confidence'/'category' defaults). 'line' is deliberately absent — it is
# legitimately null for file-scoped findings — matching the same omission
# critic_adjustments.ADD_REQUIRED_FIELDS makes for the same reason.
REQUIRED_ISSUE_FIELDS = (
    "id", "category", "severity", "title", "description", "file",
    "recommendation", "confidence",
)

# Severities the breakdown echo reports, in the order the brief's format
# specifies. Deliberately excludes 'info' from VALID_SEVERITIES: the echo
# line mirrors the brief's literal format, which reports only these four.
_ECHO_SEVERITIES = ("critical", "high", "medium", "low")

FINDINGS_FILENAME = critic_adjustments.FINDINGS_FILENAME


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
    ``issues`` is a list of well-formed entries (required fields present,
    severity in vocabulary); and ``summary`` counts match the ``issues``
    population, reusing ``critic_adjustments._recount_summary()`` — the
    same recount the ledger's other writers already trust — rather than
    re-deriving the count logic here.
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

    issues = payload.get("issues")
    if not isinstance(issues, list):
        problems.append("'issues' must be a list")
        issues = []

    for idx, issue in enumerate(issues):
        label = f"issues[{idx}]"
        if not isinstance(issue, dict):
            problems.append(f"{label} must be an object")
            continue
        missing = [f for f in REQUIRED_ISSUE_FIELDS if f not in issue]
        if missing:
            problems.append(
                f"{label}: missing required field(s) {', '.join(missing)}"
            )
            continue
        severity = issue.get("severity")
        if severity not in critic_adjustments.VALID_SEVERITIES:
            problems.append(
                f"{label}: invalid severity {severity!r} "
                f"(allowed: {', '.join(critic_adjustments.VALID_SEVERITIES)})"
            )

    # 'clearances' is null or a list of {claim, method, evidence} dicts —
    # the exact shape ReviewOutputBuilder.add_clearance() produces (both
    # claim and method are enforced non-empty strings there; evidence is
    # a stripped string or None). Validating this here is what keeps
    # _echo() safe to call unconditionally after a successful write: an
    # unvalidated non-list (or malformed entry) would len()/iterate and
    # crash AFTER write_findings() already committed the ledger — exactly
    # the failure mode this whole module exists to prevent.
    clearances = payload.get("clearances")
    if clearances is not None:
        if not isinstance(clearances, list):
            problems.append("'clearances' must be null or a list")
        else:
            for idx, clearance in enumerate(clearances):
                label = f"clearances[{idx}]"
                if not isinstance(clearance, dict):
                    problems.append(f"{label} must be an object")
                    continue
                claim = clearance.get("claim")
                if not isinstance(claim, str) or not claim.strip():
                    problems.append(
                        f"{label}: 'claim' must be a non-empty string"
                    )
                method = clearance.get("method")
                if not isinstance(method, str) or not method.strip():
                    problems.append(
                        f"{label}: 'method' must be a non-empty string"
                    )
                evidence = clearance.get("evidence")
                if evidence is not None and not isinstance(evidence, str):
                    problems.append(
                        f"{label}: 'evidence' must be a string or null"
                    )

    # 'narrative_summary' is null or a string (set_narrative_summary()
    # coerces to exactly one of those). Not a crash risk for _echo() —
    # its isinstance(narrative, str) check already tolerates any other
    # type by reading as "absent" — but validated anyway for the same
    # reason every other field here is: a shape the producer never
    # writes is a malformed ledger, not a value to silently reinterpret.
    narrative = payload.get("narrative_summary")
    if narrative is not None and not isinstance(narrative, str):
        problems.append("'narrative_summary' must be a string or null")

    # Summary consistency only makes sense once issues are well-formed —
    # otherwise the recount itself would raise on the same defect already
    # reported above (a non-object entry, an out-of-vocabulary severity).
    if isinstance(payload.get("issues"), list) and not problems:
        scratch = {}
        try:
            derived = critic_adjustments._recount_summary(scratch, issues)
        except ValueError as err:
            problems.append(str(err))
        else:
            expected_verdict = derived["verdict"]
            if verdict != expected_verdict:
                problems.append(
                    f"'verdict' {verdict!r} does not match the "
                    f"issues-derived verdict {expected_verdict!r}"
                )
            expected = scratch["summary"]
            actual = payload.get("summary")
            if not isinstance(actual, dict):
                problems.append("'summary' must be an object")
            else:
                if actual.get("total_issues") != expected["total_issues"]:
                    problems.append(
                        "'summary.total_issues' "
                        f"({actual.get('total_issues')!r}) does not match "
                        f"the {expected['total_issues']} issue(s) recorded"
                    )
                if actual.get("by_severity") != expected["by_severity"]:
                    problems.append(
                        "'summary.by_severity' "
                        f"({actual.get('by_severity')!r}) does not match "
                        f"the issues' actual severities "
                        f"({expected['by_severity']!r})"
                    )

    return problems


def _echo(findings):
    """Print the RECORDED lines the brief specifies for a successful save."""
    issues = findings.get("issues") or []
    counts = {sev: 0 for sev in _ECHO_SEVERITIES}
    for issue in issues:
        sev = issue.get("severity")
        if sev in counts:
            counts[sev] += 1
    breakdown = ", ".join(f"{sev} {counts[sev]}" for sev in _ECHO_SEVERITIES)

    clearances = findings.get("clearances") or []
    narrative = findings.get("narrative_summary")
    narrative_state = "present" if isinstance(narrative, str) and narrative.strip() else "absent"

    print(f"RECORDED VERDICT: {findings.get('verdict')}")
    print(f"RECORDED FINDINGS: {len(issues)} ({breakdown})")
    print(f"CLEARANCES: {len(clearances)} | NARRATIVE: {narrative_state}")


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

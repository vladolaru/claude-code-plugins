#!/usr/bin/env python3
"""Findings Save — the reconciliator's validating ledger save channel.

The ONLY channel the review-reconciliator may write the findings ledger
through (see agents/review-reconciliator.md), because nothing downstream
validates a hand-written ledger. The agent authors the review content and
its four reconciliation judgments, nothing about the run it read: this
module stamps the pipeline-owned facts and the degraded-host banner from
the reconciliation context itself.

It accepts the exact schema-3 findings/checks/assessment contract and
enforces the evidence trail: every source finding and check is merged into
an entry's ``sources`` or dropped with a reason, a merged check keeps its
sources' ``verifies`` union, and every registered note is answered. On ANY
problem nothing is written and every problem is echoed as its own
``REJECTED: <problem>`` line — the failure mode is silence on disk, never a
partial ledger. The write goes through
``critic_adjustments.write_findings()``, the one sanctioned write path.
"""

import argparse
import json
import os
import sys
import unicodedata

try:
    from . import critic_adjustments
    from .findings_ledger import (
        DROP_REASONS_CHECK,
        DROP_REASONS_FINDING,
        RECONCILIATION_PIPELINE_FIELDS,
        read_reconciliation_context,
    )
    from .reconciliation_context import (
        RECONCILIATION_CONTEXT_SCHEMA,
        validate_orchestrator_notes,
    )
    from .review_document import MAX_LEDGER_TEXT_LENGTH
    from .verdict_rules import REVIEW_VERDICTS, VALID_SEVERITIES
    from .run_paths import artifact_path
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import critic_adjustments
    from review.findings_ledger import (
        DROP_REASONS_CHECK,
        DROP_REASONS_FINDING,
        RECONCILIATION_PIPELINE_FIELDS,
        read_reconciliation_context,
    )
    from review.reconciliation_context import (
        RECONCILIATION_CONTEXT_SCHEMA,
        validate_orchestrator_notes,
    )
    from review.review_document import MAX_LEDGER_TEXT_LENGTH
    from review.verdict_rules import REVIEW_VERDICTS, VALID_SEVERITIES
    from review.run_paths import artifact_path


# The pipeline's own briefing for this run, written by
# reconciliation_context.py into the same output directory the ledger lands
# in. It is this module's source for every pipeline-owned reconciliation
# fact, and it is required: without it there is nothing to stamp, and a
# ledger missing those facts is rejected by the canonical validator anyway.
CONTEXT_FILENAME = artifact_path("", "reconciliation_context").name

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

# The ledger caps this text (critic_adjustments._validate_bounded_text).
# The reviewer authored it through mark_not_applicable(), which does not
# bound it, and the PIPELINE copies it here — so the pipeline is what has
# to make it fit. Truncating a reason is a smaller loss than dead-ending
# a run on a rejection its only fixer cannot fix.
_MAX_SKIP_REASON = MAX_LEDGER_TEXT_LENGTH
_SKIP_REASON_ELLIPSIS = "…"
_SKIP_REASON_UNPRINTABLE = "reason unavailable (unprintable)"


def _bounded_skip_reason(text):
    """Fit one reviewer's skip reason inside the ledger's text bound."""
    cleaned = "".join(
        character for character in text
        if character in ("\n", "\t")
        or unicodedata.category(character) not in ("Cc", "Cf")
    ).strip()
    if len(cleaned) > _MAX_SKIP_REASON:
        cleaned = cleaned[
            : _MAX_SKIP_REASON - len(_SKIP_REASON_ELLIPSIS)
        ].rstrip() + _SKIP_REASON_ELLIPSIS
    return cleaned or _SKIP_REASON_UNPRINTABLE


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


def _read_context(output_dir, problems):
    """Read the run's reconciliation context, or record why it could not be."""
    try:
        context = read_reconciliation_context(output_dir)
    except ValueError as err:
        problems.append(str(err))
        return None
    if context.get("schema") != RECONCILIATION_CONTEXT_SCHEMA:
        problems.append(
            f"{CONTEXT_FILENAME} schema {context.get('schema')!r} is not "
            f"{RECONCILIATION_CONTEXT_SCHEMA}"
        )
        return None
    try:
        validate_orchestrator_notes(context.get("orchestrator_notes"))
    except ValueError as err:
        problems.append(f"{CONTEXT_FILENAME} {err}")
        return None
    reviews = context.get("reviews_by_agent")
    if not isinstance(reviews, dict):
        problems.append(f"{CONTEXT_FILENAME} has no reviews_by_agent object")
        return None
    for stem, review in reviews.items():
        if not _is_review_entry(review):
            problems.append(
                f"{CONTEXT_FILENAME} reviews_by_agent[{stem!r}] is not a "
                "finalized review: verdict, skip_reason, and findings must "
                "carry the reviewer document's shape"
            )
            return None
    return context


def _is_review_entry(review):
    if not isinstance(review, dict):
        return False
    findings = review.get("findings")
    if not isinstance(findings, list) or not all(
        isinstance(finding, dict) for finding in findings
    ):
        return False
    verdict = review.get("verdict")
    if verdict not in REVIEW_VERDICTS:
        return False
    if verdict == "not_applicable":
        skip_reason = review.get("skip_reason")
        return (
            not findings
            and isinstance(skip_reason, str)
            and bool(skip_reason.strip())
        )
    return "skip_reason" not in review


def _source_key(entry):
    """(reviewer, id) from a sources/dropped entry, or None when malformed."""
    if (
        isinstance(entry, dict)
        and isinstance(entry.get("reviewer"), str)
        and isinstance(entry.get("id"), str)
    ):
        return (entry["reviewer"], entry["id"])
    return None


def _source_population(context):
    """Every source finding and check the run read, keyed by (stem, id)."""
    findings, checks = {}, {}
    for stem, review in context["reviews_by_agent"].items():
        for finding in review.get("findings") or []:
            if isinstance(finding, dict) and isinstance(finding.get("id"), str):
                findings[(stem, finding["id"])] = finding
        for check in review.get("checks") or []:
            if isinstance(check, dict) and isinstance(check.get("id"), str):
                checks[(stem, check["id"])] = check
    return findings, checks


def _fmt(key):
    return f"{key[0]}:{key[1]}"


def _accounting_problems(payload, context):
    """The evidence-trail invariants only the reconciliator can break.

    Every source finding and check is merged into exactly one ledger entry
    or dropped with a reason; a merged check carries each source method
    verbatim; a severity that matches no source carries a note; every
    orchestrator note has an outcome. Each problem names the source, so
    the agent can fix it in the same turn.
    """
    problems = []
    source_findings, source_checks = _source_population(context)

    merged_findings = {}
    findings = payload.get("findings")
    for idx, finding in enumerate(findings if isinstance(findings, list) else []):
        if not isinstance(finding, dict):
            continue
        sources = finding.get("sources")
        if not isinstance(sources, list) or not sources:
            problems.append(f"findings[{idx}] names no sources")
            continue
        severities = set()
        for entry in sources:
            key = _source_key(entry)
            if key is None:
                problems.append(f"findings[{idx}] has a malformed sources entry")
                continue
            if key not in source_findings:
                problems.append(f"findings[{idx}] cites unknown source {_fmt(key)}")
                continue
            if key in merged_findings:
                problems.append(
                    f"{_fmt(key)} is merged into both {merged_findings[key]} "
                    f"and findings[{idx}]"
                )
                continue
            merged_findings[key] = f"findings[{idx}]"
            if "prefiltered" in source_findings[key]:
                problems.append(
                    f"{_fmt(key)} was prefiltered by the pipeline and cannot be "
                    f"merged into findings[{idx}]; it must be dropped as prefiltered"
                )
            severities.add(source_findings[key].get("severity"))
        if (
            finding.get("severity") in VALID_SEVERITIES
            and severities
            and finding.get("severity") not in severities
            and not str(finding.get("severity_note") or "").strip()
        ):
            problems.append(
                f"findings[{idx}] is {finding.get('severity')} but its sources "
                f"are {', '.join(sorted(s for s in severities if s))}; "
                "severity_note is required"
            )

    dropped_findings = {}
    drops = payload.get("dropped_findings")
    for idx, drop in enumerate(drops if isinstance(drops, list) else []):
        key = _source_key(drop)
        if key is None or not isinstance(drop, dict):
            problems.append(f"dropped_findings[{idx}] is malformed")
            continue
        if key not in source_findings:
            problems.append(f"dropped_findings[{idx}] cites unknown source {_fmt(key)}")
            continue
        if key in merged_findings:
            problems.append(
                f"{_fmt(key)} is both merged into {merged_findings[key]} and dropped"
            )
            continue
        if key in dropped_findings:
            problems.append(f"{_fmt(key)} is dropped twice")
            continue
        prefiltered = "prefiltered" in source_findings[key]
        reason = drop.get("reason")
        if reason not in DROP_REASONS_FINDING:
            problems.append(
                f"dropped_findings[{idx}] has an unknown reason {reason!r} "
                f"(allowed: {', '.join(DROP_REASONS_FINDING)})"
            )
            continue
        if prefiltered and reason != "prefiltered":
            problems.append(
                f"{_fmt(key)} was prefiltered by the pipeline and must be "
                "dropped as prefiltered"
            )
        elif not prefiltered and reason == "prefiltered":
            problems.append(f"{_fmt(key)} was not prefiltered by the pipeline")
        dropped_findings[key] = reason
    for key in sorted(set(source_findings) - set(merged_findings) - set(dropped_findings)):
        problems.append(
            f"source finding {_fmt(key)} is neither merged into a finding nor dropped"
        )

    merged_checks = {}
    checks = payload.get("checks")
    for idx, check in enumerate(checks if isinstance(checks, list) else []):
        if not isinstance(check, dict):
            continue
        sources = check.get("sources")
        if not isinstance(sources, list) or not sources:
            problems.append(f"checks[{idx}] names no sources")
            continue
        method = check.get("method") if isinstance(check.get("method"), str) else ""
        for entry in sources:
            key = _source_key(entry)
            if key is None:
                problems.append(f"checks[{idx}] has a malformed sources entry")
                continue
            if key not in source_checks:
                problems.append(f"checks[{idx}] cites unknown source {_fmt(key)}")
                continue
            if key in merged_checks:
                problems.append(
                    f"{_fmt(key)} is merged into both {merged_checks[key]} "
                    f"and checks[{idx}]"
                )
                continue
            merged_checks[key] = f"checks[{idx}]"
            source_method = source_checks[key].get("method")
            if (
                isinstance(source_method, str)
                and source_method.strip()
                and source_method.strip() not in method
            ):
                problems.append(
                    f"checks[{idx}] merges {_fmt(key)} but does not carry its "
                    "method verbatim"
                )
            source_verifies = source_checks[key].get("verifies")
            if isinstance(source_verifies, list):
                kept = check.get("verifies") if isinstance(check.get("verifies"), list) else []
                lost = [item for item in source_verifies if item not in kept]
                if lost:
                    problems.append(
                        f"checks[{idx}] merges {_fmt(key)} but drops its verifies "
                        + ", ".join(str(item) for item in lost)
                    )
    dropped_checks = set()
    drops = payload.get("dropped_checks")
    for idx, drop in enumerate(drops if isinstance(drops, list) else []):
        key = _source_key(drop)
        if key is None:
            problems.append(f"dropped_checks[{idx}] is malformed")
            continue
        if key not in source_checks:
            problems.append(f"dropped_checks[{idx}] cites unknown source {_fmt(key)}")
        elif key in merged_checks:
            problems.append(
                f"{_fmt(key)} is both merged into {merged_checks[key]} and dropped"
            )
        elif key in dropped_checks:
            problems.append(f"{_fmt(key)} is dropped twice")
        elif drop.get("reason") not in DROP_REASONS_CHECK:
            problems.append(
                f"dropped_checks[{idx}] has an unknown reason "
                f"{drop.get('reason')!r} (allowed: {', '.join(DROP_REASONS_CHECK)})"
            )
        else:
            dropped_checks.add(key)
    for key in sorted(set(source_checks) - set(merged_checks) - dropped_checks):
        problems.append(
            f"source check {_fmt(key)} is neither merged into a check nor dropped"
        )

    recon = payload["meta"]["reconciliation"]
    fp_dropped = sum(1 for r in dropped_findings.values() if r == "false_positive")
    oos_dropped = sum(
        1 for r in dropped_findings.values() if r in ("out_of_scope", "prefiltered")
    )
    for count_field, dropped, noun in (
        ("false_positive_concern_count", fp_dropped, "false positives"),
        ("out_of_scope_concern_count", oos_dropped, "out of scope"),
    ):
        count = recon.get(count_field)
        if not isinstance(count, int):
            continue
        if count > dropped:
            problems.append(
                f"{count_field} {count} exceeds the {dropped} findings dropped as {noun}"
            )
        elif dropped and count == 0:
            problems.append(
                f"{dropped} findings were dropped as {noun} but {count_field} is 0"
            )

    notes = {
        note["id"]: note
        for note in (context.get("orchestrator_notes") or [])
        if isinstance(note, dict) and isinstance(note.get("id"), str)
    }
    answered = set()
    payload_notes = payload.get("orchestrator_notes")
    for idx, entry in enumerate(
        payload_notes if isinstance(payload_notes, list) else []
    ):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            problems.append(f"orchestrator_notes[{idx}] is malformed")
            continue
        if "note" in entry:
            problems.append(
                f"orchestrator_notes[{idx}].note is pipeline-owned — the save "
                "stamps it from the reconciliation context"
            )
        if entry["id"] not in notes:
            problems.append(f"orchestrator note {entry['id']} is not in the context")
            continue
        answered.add(entry["id"])
    for note_id in sorted(set(notes) - answered):
        problems.append(f"orchestrator note {note_id} has no outcome")
    return problems


def stamp_pipeline_facts(document, context):
    """Fill the pipeline-owned reconciliation fields from the context."""
    reviews = context["reviews_by_agent"]
    recon = document["meta"]["reconciliation"]
    not_applicable = []
    reviewing = []
    for stem in sorted(reviews):
        review = reviews[stem]
        if review.get("verdict") == "not_applicable":
            not_applicable.append({
                "name": stem,
                "skip_reason": _bounded_skip_reason(review["skip_reason"]),
            })
        else:
            reviewing.append(stem)
    recon["input_finding_count"] = sum(len(r["findings"]) for r in reviews.values())
    recon["contributing_agent_count"] = sum(
        1 for r in reviews.values() if r["findings"]
    )
    recon["reviewing_agents"] = reviewing
    recon["not_applicable_agents"] = not_applicable
    recon["dispatched_agents"] = context.get("dispatched_agents")
    recon["missing_agents"] = context.get("missing_agents")
    # The banner reaches the ledger from here or not at all — the producer
    # gate refuses one the agent wrote, so this assignment is the only
    # source of the field and cannot be silently overriding a claim.
    banner = context.get("host_context_banner")
    if isinstance(banner, dict) and banner.get("degraded"):
        document["host_context_banner"] = banner
    # Source facts the agent must not retype: each merged source's own
    # severity beside the reconciled one, the scope status behind every
    # prefiltered drop, and the text of every orchestrator note.
    source_findings, _ = _source_population(context)
    findings = document.get("findings")
    for finding in findings if isinstance(findings, list) else []:
        if not isinstance(finding, dict):
            continue
        for entry in finding.get("sources") or []:
            key = _source_key(entry)
            severity = source_findings[key].get("severity")
            if severity is not None:
                entry["severity"] = severity
            else:
                entry.pop("severity", None)
    drops = document.get("dropped_findings")
    for drop in drops if isinstance(drops, list) else []:
        if not isinstance(drop, dict):
            continue
        source = source_findings[_source_key(drop)]
        if drop.get("reason") == "prefiltered" and isinstance(source.get("prefiltered"), str):
            drop["scope_status"] = source["prefiltered"]
    notes = {
        note["id"]: note for note in (context.get("orchestrator_notes") or [])
    }
    entries = document.get("orchestrator_notes")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        entry["note"] = notes[entry["id"]]["note"]


def _producer_problems(payload, context):
    """Actor-boundary and producer-only invariants.

    The canonical validator owns the ledger's shape; these are the rules
    only the producing actor can break — authoring another actor's fields,
    or claiming judgments its own findings and its own inputs contradict.
    """
    problems = []
    actor_supplied = sorted(
        key for key in CRITIC_OWNED_LEDGER_FIELDS if key in payload
    )
    if actor_supplied:
        problems.append(
            "critic-owned lifecycle field(s): " + ", ".join(actor_supplied)
        )
    if "host_context_banner" in payload:
        problems.append(
            "pipeline-owned field: host_context_banner — the save stamps it "
            "from the reconciliation context"
        )
    for collection in ("findings", "checks"):
        entries = payload.get(collection)
        if not isinstance(entries, list):
            # A non-list collection is a shape error the canonical
            # validator names; this gate only reads well-shaped ones.
            continue
        for idx, item in enumerate(entries):
            if isinstance(item, dict) and "critic_adjustment" in item:
                problems.append(
                    f"{collection}[{idx}]: critic_adjustment is script-owned "
                    "provenance"
                )
    meta = payload.get("meta")
    recon = meta.get("reconciliation") if isinstance(meta, dict) else None
    if not isinstance(recon, dict):
        problems.append("meta.reconciliation must be an object")
        return problems
    pipeline_supplied = sorted(
        key for key in RECONCILIATION_PIPELINE_FIELDS if key in recon
    )
    if pipeline_supplied:
        problems.append(
            "pipeline-owned reconciliation field(s): "
            + ", ".join(pipeline_supplied)
        )
    findings = payload.get("findings")
    findings = findings if isinstance(findings, list) else None
    verified = recon.get("verified_concern_count")
    if (
        findings is not None
        and isinstance(verified, int)
        and verified != len(findings)
    ):
        problems.append(
            f"verified_concern_count {verified} does not equal the "
            f"{len(findings)} findings recorded"
        )
    advisory_sources = any(
        isinstance(f, dict) and f.get("channel") == "advisory"
        for r in context["reviews_by_agent"].values()
        for f in (r.get("findings") or [])
    )
    if not advisory_sources and any(
        isinstance(f, dict) and f.get("channel") == "advisory"
        for f in (findings or [])
    ):
        problems.append(
            "advisory findings recorded but no source review carried the "
            "advisory channel"
        )
    return problems


def validate_findings(payload, context):
    """Gate one producer-authored ledger, stamping the run's own facts on it.

    Stamping happens between the two validations on purpose: the producer
    invariants are about what the agent wrote, and the canonical validator
    needs the complete document — the one it will be read back as.
    """
    if not isinstance(payload, dict):
        return [f"{FINDINGS_FILENAME} must be a JSON object"]
    problems = _producer_problems(payload, context)
    if problems:
        return problems
    problems = _accounting_problems(payload, context)
    if problems:
        return problems
    stamp_pipeline_facts(payload, context)
    recon = payload["meta"]["reconciliation"]
    grouped = recon.get("grouped_concern_count")
    if isinstance(grouped, int) and grouped > recon["input_finding_count"]:
        problems.append("grouped_concern_count exceeds the input finding count")
    try:
        critic_adjustments.validate_findings_document(payload)
    except ValueError as err:
        problems.append(str(err))
    return problems


def _echo(findings, context):
    """Print the RECORDED lines the brief specifies for a successful save.

    The ACCOUNTED denominators are the context's own populations, so the
    line states a fact the gate established rather than restating the
    ledger's sums to themselves.
    """
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
    dropped_findings = findings.get("dropped_findings") or []
    dropped_checks = findings.get("dropped_checks") or []
    merged_findings = sum(len(f.get("sources") or []) for f in recorded_findings)
    merged_checks = sum(len(c.get("sources") or []) for c in checks)
    notes = findings.get("orchestrator_notes") or []
    source_findings, source_checks = _source_population(context)
    source_notes = context.get("orchestrator_notes") or []
    print(
        f"ACCOUNTED: findings {merged_findings + len(dropped_findings)}/"
        f"{len(source_findings)} ({merged_findings} merged, "
        f"{len(dropped_findings)} dropped) | checks "
        f"{merged_checks + len(dropped_checks)}/{len(source_checks)} "
        f"({merged_checks} merged, {len(dropped_checks)} dropped) | "
        f"notes {len(notes)}/{len(source_notes)}"
    )


def run_save(args):
    """Validate and atomically record one reconciled findings ledger.

    Returns the process exit code (0 on success, 1 on rejection) rather
    than raising, so ``main()`` can ``sys.exit()`` it directly — the same
    shape ``critic.py``'s ``run_save()`` uses.
    """
    problems = []
    context = _read_context(args.output_dir, problems)
    findings = _read_findings_json(args.findings, problems)
    if findings is not None and context is not None:
        problems.extend(validate_findings(findings, context))

    if problems:
        for p in problems:
            print(f"REJECTED: {p}")
        return 1

    critic_adjustments.write_findings(args.output_dir, findings)
    _echo(findings, context)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Findings Save - validate and atomically record the "
            f"review-reconciliator's {FINDINGS_FILENAME} ledger"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        required=True,
        help=f"Directory to write {FINDINGS_FILENAME} into",
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

#!/usr/bin/env python3
"""Findings Save — the reconciliator's validating save channel for
review-findings.json.

Sibling to critic.py's ``--save`` mode: this is the ONLY channel the
review-reconciliator agent is allowed to write review-findings.json through
(see agents/review-reconciliator.md). A raw write — a hand-rolled
``json.dump`` or the shared ``atomic_write_json`` used directly — closes the
gap this module exists to close, because nothing downstream validates a
hand-written ledger after the fact.

The agent authors the review content and its four reconciliation judgments;
it authors nothing about the run it read. This module reads
``reconciliation-context.json`` — the very file the agent was briefed from —
and stamps the six pipeline-owned reconciliation facts and the degraded-host
banner onto the ledger itself. A measurement the pipeline already made is
never retyped by an agent, so it cannot be mistyped, and the ledger
agrees with its own inputs by construction.

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
    from .findings_ledger import RECONCILIATION_PIPELINE_FIELDS
    from .reconciliation_context import RECONCILIATION_CONTEXT_SCHEMA
    from .verdict_rules import REVIEW_VERDICTS
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import critic_adjustments
    from review.findings_ledger import RECONCILIATION_PIPELINE_FIELDS
    from review.reconciliation_context import RECONCILIATION_CONTEXT_SCHEMA
    from review.verdict_rules import REVIEW_VERDICTS


# The pipeline's own briefing for this run, written by
# reconciliation_context.py into the same output directory the ledger lands
# in. It is this module's source for every pipeline-owned reconciliation
# fact, and it is required: without it there is nothing to stamp, and a
# ledger missing those facts is rejected by the canonical validator anyway.
CONTEXT_FILENAME = "reconciliation-context.json"

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


def _read_context(output_dir, problems):
    """Read the run's reconciliation context, or record why it could not be."""
    path = os.path.join(output_dir, CONTEXT_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            context = json.load(handle)
    except (OSError, json.JSONDecodeError) as err:
        problems.append(f"{CONTEXT_FILENAME} is unreadable: {err}")
        return None
    if not isinstance(context, dict):
        problems.append(f"{CONTEXT_FILENAME} is not a JSON object")
        return None
    if context.get("schema") != RECONCILIATION_CONTEXT_SCHEMA:
        problems.append(
            f"{CONTEXT_FILENAME} schema {context.get('schema')!r} is not "
            f"{RECONCILIATION_CONTEXT_SCHEMA}"
        )
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


def stamp_pipeline_facts(document, context):
    """Fill the pipeline-owned reconciliation fields from the context."""
    reviews = context["reviews_by_agent"]
    recon = document["meta"]["reconciliation"]
    not_applicable = []
    reviewing = []
    for stem in sorted(reviews):
        review = reviews[stem]
        if review.get("verdict") == "not_applicable":
            not_applicable.append({"name": stem, "skip_reason": review["skip_reason"]})
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
    context = _read_context(args.output_dir, problems)
    findings = _read_findings_json(args.findings, problems)
    if findings is not None and context is not None:
        problems.extend(validate_findings(findings, context))

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

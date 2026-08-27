"""
Simple Review Output Builder (No Dependencies)

Lightweight version without Pydantic for immediate use.
Provides structure and basic validation using plain Python.

Usage:
    from review.agent.output import ReviewOutputBuilder

    builder = ReviewOutputBuilder.open(output_dir, "123", "security")
    builder.add_finding(
        severity="critical",
        title="SQL Injection",
        file="src/User.php",
        line=42,
        description="...",
        recommendation="..."
    )
    saved = builder.save_draft()
    finalize_review(output_dir, "security", saved["review_digest"])

    Markdown is derived from the final JSON: render one dict with
    render_markdown(data), or from the shell via the CLI —
    `python3 output.py render <path>-review.json` prints one review's
    Markdown, `python3 output.py materialize <output_dir>` writes
    <reviewer>-review.md beside every *-review.json.
"""

import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

try:
    from .review_assignment import (
        ReviewAssignmentError,
        derive_reviewed_files,
        normalize_review_path,
    )
except ImportError:
    _SCRIPTS_DIR = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    from review.agent.review_assignment import (
        ReviewAssignmentError,
        derive_reviewed_files,
        normalize_review_path,
    )

try:
    from ..atomic_io import output_dir_lock
    from ..reviewer_lifecycle import (
        finalize_review_command,
        require_not_finalized,
        require_review_intake_open,
        review_paths,
    )
    from ..reviewer_names import derive_reviewer_name
except ImportError:
    from review.atomic_io import output_dir_lock
    from review.reviewer_lifecycle import (
        finalize_review_command,
        require_not_finalized,
        require_review_intake_open,
        review_paths,
    )
    from review.reviewer_names import derive_reviewer_name

try:
    from ..verdict_rules import (
        VALID_SEVERITIES,
        VERDICT_RANK,
        derive_review_state,
    )
except ImportError:
    # Stand-alone use — `python3 output.py render <file>` runs with no
    # `review` package on sys.path, and the CLI is a supported entry point
    # (see the module docstring). Unlike the telemetry hook below, this one
    # cannot degrade to a no-op: the verdict IS the artifact's headline, so
    # the fallback puts `scripts/` on the path and imports the same module
    # rather than keeping a local copy of the ladder to drift from.
    _SCRIPTS_DIR = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    from review.verdict_rules import (
        VALID_SEVERITIES,
        VERDICT_RANK,
        derive_review_state,
    )


# The shape schemas/review-output.ts documents. Bump in the SAME commit as
# any key added, removed, or re-typed in the serialized artifact, update the
# TypeScript contract, and note the bump in the changelog. It replaced a
# `version: "1.0.0"` string that survived six format changes unbumped —
# an unmaintained compatibility claim is worse than none.
#
# One carve-out, matching the rule in the plugin's AGENTS.md: a shape change
# made within the same UNRELEASED version that introduced the current number
# updates the TypeScript contract in the same commit but does NOT bump. The
# number states a compatibility guarantee only once released, so bumping
# here would publish a shape no artifact ever had. This migration deliberately
# establishes schema 2 as the one review-artifact contract shipped by 1.114.0.
REVIEW_OUTPUT_SCHEMA = 2

_VALID_SEVERITIES = VALID_SEVERITIES
_VALID_CHANNELS = ('blocking', 'advisory')
_SEVERITY_RANK = {
    'info': 0,
    'low': 1,
    'medium': 2,
    'high': 3,
    'critical': 4,
}
# Backwards-compatible private aliases used by telemetry's artifact
# validation. Derivation and rank comparison live in verdict_rules.py.
_VERDICT_RANK = VERDICT_RANK


def _coerce_text(value: Any, single_line: bool = False) -> str:
    """Coerce a free-form finding field to a string at write time.

    These fields are model-authored, so a value the schema expects to be a
    string (``title``, ``description``, ``recommendation``) can arrive as a
    list, number, or ``None``. Persisting a non-string here lets the malformed
    value flow downstream into the reconciliation Markdown renderer, which
    crashes the whole review at pipeline step 8. Coerce at the producer so bad
    values never reach disk: lists/tuples join on newlines, ``None`` becomes
    empty, everything else stringifies. (The reconciliation renderer keeps its
    own equivalent guard as defense in depth.)

    ``single_line=True`` additionally collapses all whitespace to single
    spaces. Titles render inline downstream (``**N. …**``, ``### F1: …``)
    without block-syntax escaping, so a newline could forge a heading or
    thematic break — keeping titles single-line prevents that.
    """
    if isinstance(value, str):
        result = value
    elif value is None:
        result = ""
    elif isinstance(value, (list, tuple)):
        result = "\n".join(_coerce_text(item) for item in value)
    else:
        result = str(value)
    if single_line:
        result = " ".join(result.split())
    return result


# Dispatch-marker suffixes. Spelled here rather than imported so this module
# stays importable stand-alone (`python3 output.py render <file>` runs with no
# `review` package on sys.path — the same constraint that makes telemetry
# below load by file location). Parity with the bootstrap-written
# `<agent>.started` contract and review/synthesis_lifecycle.MARKER_SUFFIX is
# pinned by tests, so a rename fails loudly instead of silently unmeasuring a
# whole class of actor.
_REVIEWER_START_SUFFIX = ".started"
_SYNTHESIS_START_SUFFIX = ".synthesis-started"

# Builder `reviewer` name -> the agent name its dispatch marker is keyed on,
# for the one actor where the two differ. The reconciliator is dispatched as
# `review-reconciliator` but constructs its builder as `reconciliator`
# (agents/review-reconciliator.md), and `reviewer` is a published field of
# review-findings.json — so this maps the lookup rather than renaming an
# artifact field to suit it.
_MARKER_AGENT_BY_REVIEWER = {"reconciliator": "review-reconciliator"}


def _actor_start_time(
    output_dir: Optional[str], reviewer: Optional[str]
) -> Optional[datetime]:
    """When this actor was dispatched, per the marker the pipeline wrote.

    The only honest clock the builder has. A builder is constructed inside
    the final heredoc, seconds before serialization, so measuring from its
    own __init__ times the write and calls it the review — which is how
    every artifact of a 19-agent run came to carry a duration of ~0ms,
    including a reconciliator that ran for 211 seconds.

    Two marker families exist because two kinds of actor do: reviewers get
    `<agent>.started` from bootstrap, synthesis agents get
    `<agent>.synthesis-started` from synthesis_lifecycle (deliberately NOT
    `.started`, so tools scanning for reviewers do not seed them as one).
    Both hold a tz-aware ISO timestamp.

    None everywhere the answer is not known: no output directory, no
    marker (hand-rolled builder, standalone use), unreadable or unparsable
    stamp. Absence is reported as absence — never as zero.
    """
    directory = output_dir or os.environ.get("PIRATEGOAT_OUTPUT_DIR")
    if not directory or not reviewer:
        return None
    agent = _MARKER_AGENT_BY_REVIEWER.get(reviewer, reviewer)
    # `reviewer` is derive_reviewer_name(agent_name), which strips a
    # trailing "-reviewer"; the inverse is ambiguous, so try both spellings
    # against both marker families.
    for name in (
        f"{agent}-reviewer{_REVIEWER_START_SUFFIX}",
        f"{agent}{_REVIEWER_START_SUFFIX}",
        f"{agent}{_SYNTHESIS_START_SUFFIX}",
        f"{agent}-reviewer{_SYNTHESIS_START_SUFFIX}",
    ):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                stamp = datetime.fromisoformat(handle.read().strip())
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        return stamp
    return None


def _telemetry_for_output(output_dir):
    """Load telemetry lazily so output.py remains a standalone CLI."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "review_telemetry",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "telemetry.py",
        ),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ReviewTelemetry(output_dir)


def _log_agent_review_draft_saved_telemetry(
    output_dir, agent_name, review_digest
):
    telemetry = _telemetry_for_output(output_dir)
    telemetry.log_agent_review_draft_saved(
        agent_name=agent_name, review_digest=review_digest
    )


def _completion_was_logged(output_dir, agent_name, review_digest):
    telemetry = _telemetry_for_output(output_dir)
    return any(
        event.get("event") == "agent_complete"
        and event.get("agent") == agent_name
        and event.get("review_digest") == review_digest
        for event in telemetry._read_events()
    )


def _log_agent_complete_telemetry(
    output_dir, agent_name, verdict, finding_count, severities, review_digest
):
    telemetry = _telemetry_for_output(output_dir)
    telemetry.log_agent_complete(
        agent_name=agent_name,
        verdict=verdict,
        finding_count=finding_count,
        severities=severities,
        review_digest=review_digest,
    )


def _validate_review_bytes(
    data: bytes, *, reviewer: str, pr_id: str
) -> dict:
    """Validate one persisted draft before rehydrating builder state."""
    try:
        review = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("malformed review draft JSON") from exc
    validate_review_document(review, reviewer)
    expected_pr_id = pr_id if isinstance(pr_id, str) else str(pr_id)
    if review["pr_id"] != expected_pr_id:
        raise ValueError("review draft PR does not match open request")
    return review


def _optional_file_digest(path: str) -> str | None:
    """Return a file's SHA-256 digest, or None only when it is absent."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    return hashlib.sha256(data).hexdigest()


def _atomic_replace_bytes(path: str, data: bytes) -> None:
    """Atomically replace one file with staged bytes and clean failures."""
    staged_path = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        Path(staged_path).write_bytes(data)
        os.replace(staged_path, path)
    finally:
        try:
            os.unlink(staged_path)
        except FileNotFoundError:
            pass


def render_draft_index(review: dict) -> str:
    """Render concise mutable review state for continuation bootstrap."""
    findings = review.get("findings") or []
    checks = review.get("checks") or []
    reviewed_file_claims = review.get("reviewed_file_claims") or []
    lines = [
        "DRAFT INDEX:",
        f"  findings {len(findings)} | checks {len(checks)} | "
        f"reviewed-file claims {len(reviewed_file_claims)}",
    ]
    for finding in findings:
        location = (
            f"{finding['file']}:{finding['line']}"
            if finding["line"] is not None
            else f"{finding['file']} (file scope)"
        )
        lines.append(
            f"  finding {finding['id']}: {finding['severity']} "
            f"{json.dumps(finding['title'], ensure_ascii=False)} @ {location}"
        )
    for check in checks:
        lines.append(
            f"  check {check['id']}: "
            f"{json.dumps(check['question'], ensure_ascii=False)}"
        )
    for path in reviewed_file_claims:
        lines.append(f"  reviewed-file claim: {path}")
    return "\n".join(lines)


def render_markdown(data: Dict) -> str:
    """Human-readable Markdown rendered from a review's canonical dict.

    A pure function of the JSON representation — the dict save_draft()
    writes and the *-review.json file holds (or a findings ledger) — so a
    rendering can never disagree with the artifact it came from.

    Keys present in schema 2 are required (missing means KeyError — the
    caller's problem); later schema additions are read with .get() and
    render only when present.

    The title names the reviewer that produced the document. The findings
    ledger has no reviewer — it is a synthesis of many — so it is titled
    "Review Findings"; a title is never invented for a document that does
    not claim one.

    Title plus ``render_review_body()``, which is everything below it. The
    split exists because `review-record.md` — the pipeline's machine
    projection of the reconciliation ledger — needs that body under its own
    header, and a second copy of these sections is how the record and
    `review-findings.md` would eventually disagree about a finding.
    """
    title = (
        f"{data['reviewer'].title()} Review" if 'reviewer' in data
        else "Review Findings"
    )
    return f"# {title} - PR #{data['pr_id']}\n\n" + render_review_body(data)


def _applied_critic_decision(record):
    """Project one complete schema-2 applied-decision record."""
    if not isinstance(record, dict) or set(record) != {
        'adjustment_id', 'outcome'
    }:
        return None
    adjustment_id = record.get('adjustment_id')
    outcome = record.get('outcome')
    if (
        not isinstance(adjustment_id, str) or not adjustment_id
        or outcome not in ('verified', 'refuted', 'not_checked')
    ):
        return None
    return adjustment_id, outcome


def _rejected_critic_decision(record):
    """Project one complete schema-2 rejected-decision record."""
    if not isinstance(record, dict) or set(record) != {
        'adjustment_id', 'action', 'target', 'outcome', 'rejection_reason'
    }:
        return None
    adjustment_id = record.get('adjustment_id')
    outcome = record.get('outcome')
    if (
        not isinstance(adjustment_id, str) or not adjustment_id
        or outcome != 'refuted'
    ):
        return None
    return adjustment_id, 'refuted'


def render_review_body(data: Dict) -> str:
    """Everything a rendered review says beneath its title.

    Banner, executive summary, assessment, critic decisions, findings,
    recommendations, checks, critic removals, positives, observations —
    the whole document minus the H1. Shared verbatim by
    ``render_markdown()`` (which supplies the per-reviewer title) and by
    the review-record assembler in ``orchestration.py`` (which supplies its
    own). Same contract as ``render_markdown()``: a pure function of the
    canonical dict, schema-2 keys required, later additions read with
    ``.get()``.
    """
    md = []

    # Degraded host context, first thing in the body: reviewers' claims
    # were scoped by this banner's presence, so a reader must meet it
    # before any finding. It sits below the caller's H1 rather than above
    # it because every document built from this body is graded on starting
    # with "# " (tests/helpers/graders.py) — one rule for both callers, and
    # the first thing after the title is prominent enough.
    #
    # Every line carries the quote marker, not just the first: the banner
    # message is hand-copied through an agent, and a reformat that
    # introduces a newline would otherwise drop the remainder out of the
    # blockquote entirely.
    banner = data.get('host_context_banner')
    if isinstance(banner, dict) and banner.get('degraded'):
        message = _coerce_text(banner.get('message', ''))
        lines = message.split("\n") or [""]
        md.append(f"> **\u26a0 Host Context Banner:** {lines[0]}\n")
        for line in lines[1:]:
            md.append(f"> {line}\n")
        md.append("\n")
    md.append("## Executive Summary\n\n")
    md.append(f"**Verdict:** {data['verdict'].upper()}\n")
    md.append(
        f"**Total Findings:** {data['summary']['total_findings']}\n\n"
    )

    suppressed_advisory_findings = data['summary'].get(
        'suppressed_advisory_finding_count', 0
    )
    if suppressed_advisory_findings:
        finding_word = (
            "finding" if suppressed_advisory_findings == 1 else "findings"
        )
        md.append(
            f"**Advisory suppression:** {suppressed_advisory_findings} "
            f"{finding_word} "
            "excluded from the verdict"
        )
        verdict_without_advisory = data['summary'].get(
            'verdict_without_advisory'
        )
        if verdict_without_advisory:
            md.append(
                " (verdict without suppression: "
                f"{verdict_without_advisory.upper()})"
            )
        md.append("\n\n")

    if data['summary']['total_findings'] > 0:
        counts = data['summary']['by_severity']
        md.append(f"- Critical: {counts['critical']}\n")
        md.append(f"- High: {counts['high']}\n")
        md.append(f"- Medium: {counts['medium']}\n\n")

    # Unclaimed review work derived from the reviewer's assignment.
    if data.get('unclaimed_review_files'):
        files = ", ".join(f"`{f}`" for f in data['unclaimed_review_files'])
        md.append(f"**Not reviewed (budget):** {files}\n\n")

    # Reconciliation block — the narrative's "Pipeline:" line, now
    # rendered from the metrics the producer already records under
    # meta.reconciliation. Absent for ordinary reviewers, whose meta
    # carries no such block.
    meta = data.get('meta')
    recon = meta.get('reconciliation') if isinstance(meta, dict) else None
    if isinstance(recon, dict):
        md.append(
            f"**Pipeline:** {recon.get('input_finding_count', 0)} findings "
            f"from {recon.get('contributing_agent_count', 0)} reviewing agents "
            f"\u2192 {recon.get('verified_concern_count', 0)} verified findings "
            f"({recon.get('grouped_concern_count', 0)} concerns after "
            f"grouping, {recon.get('false_positive_concern_count', 0)} false "
            f"positives dropped, {recon.get('out_of_scope_concern_count', 0)} "
            "out-of-scope dropped). Full metrics in "
            "`review-findings.json` \u2192 `meta.reconciliation`.\n\n"
        )
        # Not-applicable agents are reported separately and never counted
        # toward approval confidence: they abstained, they did not review.
        na_agents = recon.get('not_applicable_agents')
        if isinstance(na_agents, list) and na_agents:
            word = "agent" if len(na_agents) == 1 else "agents"
            named = ", ".join(
                f"{a.get('name')} ({a.get('skip_reason')})"
                if isinstance(a, dict) else str(a)
                for a in na_agents
            )
            md.append(
                f"**Coverage:** {len(na_agents)} {word} returned "
                f"not-applicable (changes outside their domain): "
                f"{named}\n\n"
            )

    # The producer's own reading of the change as a whole. Nothing else in
    # this artifact carries it, so without this section a mechanical render
    # would drop the one judgment a list of findings cannot express.
    #
    # It is also the one part of this document the decision critic cannot
    # correct: its adjustment vocabulary addresses findings, and this is
    # ledger-level prose. So an applying batch INVALIDATES it
    # (critic_adjustments.py) rather than leaving a stale claim rendered
    # above the list that contradicts it, and this renders the invalidation
    # instead of silently dropping the section — an absent Assessment and a
    # retracted one are different facts. Prose that survived a critic round
    # untouched still renders as prose: that is the STAND case, and the
    # marker below says exactly whose words they are.
    invalidated = data.get('invalidated_assessments')
    if data.get('assessment'):
        md.append("## Assessment\n\n")
        md.append(f"{data['assessment']}\n\n")
        # Whose words these are depends on whether a batch already invalidated
        # the reconciler's. After invalidation the standing text is the
        # orchestrator's `revised_assessment`, carried in through the
        # adjustments channel — attributing it to the reconciler would
        # credit prose that was retracted a step earlier.
        md.append(
            "*Post-critic assessment, written after the critic "
            "adjustments applied.*\n\n"
            if invalidated else
            "*Reconciler-authored assessment, not adjusted by the decision "
            "critic.*\n\n"
        )
    elif invalidated:
        # Keyed on the invalidation record itself, not on
        # applied_critic_adjustments: a ledger that never carried a summary
        # records no withdrawal, and rendering a retraction notice for it
        # would claim an act that never happened.
        #
        # An explicit absence, not a pointer: the previous wording sent the
        # reader to "the report for the current assessment", which on a bot
        # run is a file nobody opens and on any run may carry no post-critic
        # assessment at all. The retracted text is deliberately NOT shown
        # here — it is the one thing this section must not present as
        # current.
        md.append("## Assessment\n\n")
        md.append(
            # "the standing assessment", not "the reconciler's": on a second
            # reconciliation-plus-critic round the invalidated text may be the
            # orchestrator's own `revised_assessment` from the first round,
            # and naming an author this section cannot know would be a
            # claim rather than a description.
            "No current assessment: the standing assessment was invalidated "
            "by critic revision and not replaced; see the findings.\n\n"
        )

    # What the orchestrator did with every critic decision, from the ledger's
    # own applied and rejected records rather than from prose in a report. A
    # batch nobody probed renders as N lines of `not_checked`; a rejected
    # decision renders as `refuted`, including legacy rejection records from
    # before that outcome was explicit on each record.
    applied_adjustments = data.get('applied_critic_adjustments')
    rejected_adjustments = data.get('rejected_critic_adjustments')
    decisions = []
    if isinstance(applied_adjustments, list):
        decisions.extend(
            decision for decision in (
                _applied_critic_decision(record)
                for record in applied_adjustments
            ) if decision is not None
        )
    if isinstance(rejected_adjustments, list):
        decisions.extend(
            decision for decision in (
                _rejected_critic_decision(record)
                for record in rejected_adjustments
            ) if decision is not None
        )
    if decisions:
        md.append("## Critic Adjustment Decisions\n\n")
        for adjustment_id, outcome in decisions:
            md.append(f"- `{adjustment_id}` — {outcome}\n")
        md.append("\n")

    # Findings — every severity that counts toward total_findings must render,
    # or the Markdown claims findings it doesn't show.
    for sev in ['critical', 'high', 'medium', 'low', 'info']:
        severity_findings = [
            finding
            for finding in data['findings']
            if finding['severity'] == sev
        ]

        if severity_findings:
            md.append(f"## {sev.title()} Findings\n\n")

            for finding in severity_findings:
                md.append(f"### {finding['title']}\n\n")
                if finding['line']:
                    location = (
                        f"**File:** `{finding['file']}` line {finding['line']}"
                    )
                elif finding.get('scope') == 'file':
                    location = f"**File:** `{finding['file']}` (file-scoped)"
                else:
                    location = f"**File:** `{finding['file']}`"
                md.append(location + "\n\n")
                md.append(f"{finding['description']}\n\n")
                if finding.get('severity_floor'):
                    md.append(
                        f"**Severity floor:** {finding['severity_floor']}\n\n"
                    )
                md.append(f"**Fix:** {finding['recommendation']}\n\n")

    # Recommendations — prioritized, and rendered because the producer
    # recorded them. They were silently dropped from every derived
    # Markdown before this: add_recommendation() wrote them to the JSON
    # and nothing ever read them back out.
    recommendations = data.get('recommendations')
    if isinstance(recommendations, dict):
        # The three known priorities render first and in their meaningful
        # order; anything else the producer wrote renders after, labelled
        # by its own key. Rendering only the known three would let an
        # unexpected priority print a heading with its content dropped
        # underneath — a document that shows a section it did not show.
        known = ('immediate', 'important', 'suggestions')
        ordered = list(known) + [
            key for key in recommendations if key not in known
        ]
        groups = []
        for priority in ordered:
            entries = recommendations.get(priority) or []
            if not entries:
                continue
            groups.append(f"**{priority.title()}:**\n\n")
            groups.extend(f"- {entry}\n" for entry in entries)
            groups.append("\n")
        # The header is emitted only once something will actually appear
        # beneath it.
        if groups:
            md.append("## Recommendations\n\n")
            md.extend(groups)

    checks = data.get('checks')
    if checks:
        heading = (
            "Verified Checks" if isinstance(recon, dict)
            else "Checks Performed"
        )
        md.append(f"## {heading}\n\n")
        for check in checks:
            md.append(f"- **{check['question']}**\n")
            md.append(f"  - Method: {check['method']}\n")
            md.append(f"  - Result: {check['result']}\n")
            md.append(
                "  - Source reviewers: "
                + ", ".join(check['source_reviewers'])
                + "\n"
            )
        md.append("\n")

    removed_checks = data.get('checks_removed_by_critic')
    if isinstance(removed_checks, list) and removed_checks:
        md.append("## Checks Removed by the Decision Critic\n\n")
        for check in removed_checks:
            if not isinstance(check, dict):
                continue
            adjustment = check.get('critic_adjustment')
            rationale = (
                adjustment.get('rationale')
                if isinstance(adjustment, dict) else None
            )
            md.append(
                f"- **{check.get('question')}** — "
                f"{rationale or 'no rationale recorded'}\n"
            )
        md.append("\n")

    # What the critic took out. The ledger deliberately moves a removed
    # finding into `findings_removed_by_critic` rather than deleting it, so the
    # decision stays auditable; a reading copy that dropped the section
    # would hide exactly the record the JSON went out of its way to keep.
    removed = data.get('findings_removed_by_critic')
    if isinstance(removed, list) and removed:
        md.append("## Removed by the Decision Critic\n\n")
        for entry in removed:
            if not isinstance(entry, dict):
                continue
            adjustment = entry.get('critic_adjustment')
            rationale = (
                adjustment.get('rationale')
                if isinstance(adjustment, dict) else None
            )
            location = f"`{entry.get('file')}`"
            if entry.get('line'):
                location += f" line {entry['line']}"
            md.append(
                f"- **{entry.get('title')}** ({entry.get('severity')}) — "
                f"{location} — "
                f"{rationale or 'no rationale recorded'}\n"
            )
        md.append("\n")

    # Positive
    if data['positive_observations']:
        md.append("## Positive Observations\n\n")
        for obs in data['positive_observations']:
            md.append(f"- {obs}\n")

    # Observations
    if data.get('observations'):
        md.append("\n## Observations\n\n")
        for obs in data['observations']:
            md.append(f"- **`{obs['file']}`** — {obs['note']}\n")

    return ''.join(md)


_RECONCILIATION_LEDGER_NAME = "review-findings.json"


def _load_renderable_review_artifact(path):
    """Classify and load one supported artifact by its actual filename."""
    name = os.path.basename(path)
    if name == _RECONCILIATION_LEDGER_NAME:
        # Imported only at the ledger boundary: critic_adjustments owns the
        # exact post-critic schema, but imports this module for the shared
        # review builder/renderers. A top-level import would make that
        # ownership relationship cyclic.
        try:
            from .. import critic_adjustments
        except ImportError:
            from review import critic_adjustments
        read = critic_adjustments.read_findings_file(path)
        if read.status != critic_adjustments.FINDINGS_READ_OK:
            raise ValueError(
                "reconciliation findings ledger unreadable "
                f"({read.status})"
            )
        return read.findings
    if name.endswith("-review.json"):
        reviewer = name[: -len("-review.json")]
        return load_review_document(path, reviewer)
    raise ValueError(f"unsupported review artifact: {name}")


def materialize_markdown(
    output_dir: str, *, suffix: str = "-review.json"
) -> List[str]:
    """Render <name>.md beside every <name>.json matching `suffix`.

    Derived artifacts for humans browsing the output directory: idempotent,
    regenerated from the settled canonical JSON, read by no pipeline
    consumer for control flow (readiness, reconciliation, and the bot all
    key on the JSON). Malformed JSONs are skipped with a note on stderr —
    grading and reconciliation report those failures on their own channels.

    `suffix` selects filenames only; the matched filename independently
    chooses its validation boundary. Every `<reviewer>-review.json` uses
    the canonical final-review loader, including when a caller supplies an
    exact filename as the suffix. Only exact `review-findings.json` uses
    the canonical reconciliation-ledger reader and validator. A second
    copy of this loop is how the two would eventually disagree about what
    a rendering means.
    """
    written: List[str] = []
    for name in sorted(os.listdir(output_dir)):
        if not name.endswith(suffix):
            continue
        json_path = os.path.join(output_dir, name)
        try:
            data = _load_renderable_review_artifact(json_path)
            md_text = render_markdown(data)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
            print(f"skipped {name}: {err}", file=sys.stderr)
            continue
        md_path = json_path[: -len(".json")] + ".md"
        with open(md_path, "w", encoding="utf-8") as handle:
            handle.write(md_text)
        written.append(md_path)
    return written


class ReviewOutputBuilder:
    """Simple builder for structured review outputs."""

    def __init__(self, pr_id: str, reviewer: str):
        # Agents that hand-roll a builder script pass whatever the bootstrap
        # wrapper would have injected as a string — a real run shipped an int
        # that serialized as a JSON number, so the artifact's shape stopped
        # being uniform across reviewers. Coerce once, at construction.
        self.pr_id = pr_id if isinstance(pr_id, str) else str(pr_id)
        self.reviewer = reviewer
        self.timestamp = datetime.now().isoformat()
        self.findings = []
        self.observations = []
        self.recommendations = {'immediate': [], 'important': [], 'suggestions': []}
        self.positive_observations = []
        self.checks = []
        # Agent-authored: the producer's own reading of the change as a
        # whole. The reconciliator's overall-state prose lives here.
        self.assessment = None
        self.next_finding_number = 1
        self.next_check_number = 1
        # Agent-authored: review-claimable files the reviewer claims it read.
        self.reviewed_file_claims = []
        self.overall_confidence = 0.95
        self._not_applicable = False
        self._skip_reason = None
        self._output_dir = None
        self._paths = None
        self._base_digest = None
        self._last_saved_review = None
        self._invocation_delta = []

    @classmethod
    def open(
        cls, output_dir: str, pr_id: str, reviewer: str
    ) -> "ReviewOutputBuilder":
        """Create or rehydrate one mutable draft under the lifecycle lock."""
        output_dir = str(output_dir)
        os.makedirs(output_dir, exist_ok=True)
        paths = review_paths(output_dir, reviewer)
        with output_dir_lock(output_dir):
            require_review_intake_open(output_dir)
            require_not_finalized(paths)
            if not os.path.exists(paths.draft):
                return cls(pr_id, reviewer)._bind(
                    output_dir, base_digest=None
                )
            draft_bytes = Path(paths.draft).read_bytes()
            review = _validate_review_bytes(
                draft_bytes, reviewer=reviewer, pr_id=pr_id
            )
            digest = hashlib.sha256(draft_bytes).hexdigest()
        return cls._from_review(review)._bind(
            output_dir, base_digest=digest
        )

    @classmethod
    def _from_review(cls, review: dict) -> "ReviewOutputBuilder":
        """Rehydrate every builder-owned field from a validated review."""
        builder = cls(review["pr_id"], review["reviewer"])
        builder.timestamp = review["timestamp"]
        builder.findings = list(review["findings"])
        builder.observations = list(review.get("observations") or [])
        recommendations = review.get("recommendations") or {}
        builder.recommendations = {
            priority: list(recommendations.get(priority) or [])
            for priority in ("immediate", "important", "suggestions")
        }
        builder.positive_observations = list(
            review.get("positive_observations") or []
        )
        builder.checks = list(review["checks"])
        builder.assessment = review.get("assessment")
        builder.reviewed_file_claims = list(review["reviewed_file_claims"])
        meta = review["meta"]
        builder.next_finding_number = meta["next_finding_number"]
        builder.next_check_number = meta["next_check_number"]
        builder.overall_confidence = meta["confidence_score"]
        builder._not_applicable = review["verdict"] == "not_applicable"
        builder._skip_reason = review.get("skip_reason")
        return builder

    def _bind(
        self, output_dir: str, *, base_digest: str | None
    ) -> "ReviewOutputBuilder":
        """Bind this builder to exactly one run and observed draft state."""
        self._output_dir = str(output_dir)
        self._paths = review_paths(self._output_dir, self.reviewer)
        self._base_digest = base_digest
        return self

    def _allocate_finding_id(self) -> str:
        finding_id = f"f{self.next_finding_number}"
        self.next_finding_number += 1
        return finding_id

    def _allocate_check_id(self) -> str:
        check_id = f"c{self.next_check_number}"
        self.next_check_number += 1
        return check_id

    @staticmethod
    def _entry_index(entries: list, entry_id: str, kind: str) -> int:
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and entry.get("id") == entry_id:
                return index
        raise ValueError(f"unknown {kind} id: {entry_id}")

    def update_finding(self, finding_id: str, **fields) -> None:
        """Strictly patch one finding without changing its identity."""
        allowed = {
            "category",
            "severity",
            "title",
            "description",
            "file",
            "line",
            "recommendation",
            "confidence",
            "behavior_evidence",
            "source_cited",
            "severity_floor",
            "channel",
            "code_snippet",
            "references",
        }
        rejected = sorted(set(fields) - allowed)
        if rejected:
            raise ValueError(
                "update_finding cannot update field(s): "
                + ", ".join(rejected)
            )
        if not fields:
            raise ValueError("update_finding requires at least one field")
        index = self._entry_index(self.findings, finding_id, "finding")
        candidate = dict(self.findings[index])
        candidate.update(fields)
        if "severity" in fields and isinstance(candidate["severity"], str):
            candidate["severity"] = candidate["severity"].lower()
        if "severity_floor" in fields and isinstance(
            candidate["severity_floor"], str
        ):
            candidate["severity_floor"] = candidate["severity_floor"].lower()
        for field in ("title", "description", "recommendation"):
            if field in fields:
                candidate[field] = _coerce_text(
                    fields[field], single_line=field == "title"
                )
        if candidate.get("line") is None:
            candidate["scope"] = "file"
        else:
            candidate.pop("scope", None)
        floor = candidate.get("severity_floor")
        severity = candidate.get("severity")
        if (
            floor in _SEVERITY_RANK
            and severity in _SEVERITY_RANK
            and _SEVERITY_RANK[severity] < _SEVERITY_RANK[floor]
        ):
            candidate["severity"] = floor
        candidate_channel = candidate.get("channel") or "blocking"
        reviewed_files = self._bound_reviewed_files()
        if reviewed_files is not None and candidate_channel not in reviewed_files.channels:
            raise ValueError(
                f"channel {candidate_channel!r} is not among this reviewer's "
                f"channels {list(reviewed_files.channels)}"
            )
        _validate_finding_shape(candidate, index)
        self.findings[index] = candidate
        self._invocation_delta.append(f"updated finding {finding_id}")

    def remove_finding(self, finding_id: str) -> None:
        """Remove one finding without recycling its stable ID."""
        index = self._entry_index(self.findings, finding_id, "finding")
        self.findings.pop(index)
        self._invocation_delta.append(f"removed finding {finding_id}")

    def record_check(
        self,
        question: str,
        method: str,
        result: str,
        *,
        source_reviewers: Optional[List[str]] = None,
    ) -> str:
        """Record one check; ``source_reviewers`` defaults to this reviewer.

        One entry point for both producers: a reviewer recording its own
        verification work, and the reconciliator recording a check merged
        from several reviewers' — which names them all.
        """
        if source_reviewers is None:
            source_reviewers = [self.reviewer]
        values = [
            _coerce_text(value).strip()
            for value in (question, method, result)
        ]
        if not all(values):
            raise ValueError(
                "record_check requires non-empty question, method, and result"
            )
        if (
            not isinstance(source_reviewers, list)
            or not source_reviewers
            or any(
                not isinstance(source, str) or not source.strip()
                for source in source_reviewers
            )
        ):
            raise ValueError(
                "record_check source_reviewers must be non-empty strings"
            )
        normalized_sources = list(
            dict.fromkeys(source.strip() for source in source_reviewers)
        )
        check_id = self._allocate_check_id()
        self.checks.append({
            "id": check_id,
            "question": values[0],
            "method": values[1],
            "result": values[2],
            "source_reviewers": normalized_sources,
        })
        self._invocation_delta.append(
            f"added check {check_id} "
            f"{json.dumps(values[0], ensure_ascii=False)}"
        )
        return check_id

    def update_check(self, check_id: str, **fields) -> None:
        """Strictly patch check content without changing identity or sources."""
        allowed = {"question", "method", "result"}
        rejected = sorted(set(fields) - allowed)
        if rejected:
            raise ValueError(
                "update_check cannot update field(s): "
                + ", ".join(rejected)
            )
        if not fields:
            raise ValueError("update_check requires at least one field")
        index = self._entry_index(self.checks, check_id, "check")
        candidate = dict(self.checks[index])
        candidate.update(
            (field, _coerce_text(value).strip())
            for field, value in fields.items()
        )
        _validate_check_shape(candidate, index)
        self.checks[index] = candidate
        self._invocation_delta.append(f"updated check {check_id}")

    def remove_check(self, check_id: str) -> None:
        """Remove one check without recycling its stable ID."""
        index = self._entry_index(self.checks, check_id, "check")
        self.checks.pop(index)
        self._invocation_delta.append(f"removed check {check_id}")

    def add_finding(
        self,
        severity: str,
        title: str,
        file: str,
        description: str,
        recommendation: str,
        category: str = "general",
        line: int = None,
        confidence: float = 0.95,
        behavior_evidence: Optional[str] = None,
        source_cited: Optional[str] = None,
        severity_floor: Optional[str] = None,
        *,
        channel: Optional[str] = None,
        **extra_fields
    ) -> Optional[str]:
        """Add a finding and return its builder-generated stable ID.

        Line is required for point defects — the reviewer protocol mandates
        diff-anchored findings for anything that has a line. Findings that are
        line-less BY NATURE (missing test coverage, missing assertions,
        git-history precedent, cross-file architecture) may pass line=None:
        they are recorded as first-class FILE-SCOPED findings (line: null,
        scope: "file") that count toward the verdict, with a stderr NOTE so
        accidental line omission stays visible.
        Use add_observation() only for genuinely informational notes that
        should NOT count toward the verdict.
        When severity_floor is provided, lower severities are promoted to it.
        """
        # Validate severity and enforce an optional minimum.
        severity_value = severity.lower()
        if severity_value not in _VALID_SEVERITIES:
            raise ValueError(
                f"Invalid severity: {severity}. "
                f"Must be one of {list(_VALID_SEVERITIES)}"
            )

        floor_value = None
        if severity_floor is not None:
            if not isinstance(severity_floor, str):
                raise ValueError("severity_floor must be a severity name")
            floor_value = severity_floor.lower()
            if floor_value not in _VALID_SEVERITIES:
                raise ValueError(
                    f"Invalid severity_floor: {severity_floor}. "
                    f"Must be one of {list(_VALID_SEVERITIES)}"
                )
            if _SEVERITY_RANK[severity_value] < _SEVERITY_RANK[floor_value]:
                severity_value = floor_value

        # Validate confidence
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {confidence}")

        # Validate behavior_evidence enum
        if behavior_evidence is not None:
            valid_evidence = ("cited", "inferred")
            if behavior_evidence not in valid_evidence:
                raise ValueError(
                    f"Invalid behavior_evidence: {behavior_evidence!r}. "
                    f"Must be one of {valid_evidence}."
                )

        if channel is not None:
            if not isinstance(channel, str) or channel not in _VALID_CHANNELS:
                raise ValueError(
                    f"Invalid channel: {channel!r}. "
                    f"Must be one of {_VALID_CHANNELS}."
                )

        effective_channel = channel or "blocking"
        reviewed_files = self._bound_reviewed_files()
        if reviewed_files is not None and effective_channel not in reviewed_files.channels:
            raise ValueError(
                f"channel {effective_channel!r} is not among this reviewer's "
                f"channels {list(reviewed_files.channels)}"
            )

        # Validate line — None records a first-class file-scoped finding (loud),
        # hard enforcement for invalid values (0, negative, non-int).
        file_scoped = line is None
        if file_scoped:
            # A legitimately line-less finding (missing coverage, precedent,
            # cross-file architecture) is a real, verdict-counting finding.
            # Point defects still need line= — hence the stderr NOTE.
            print(
                f"NOTE: recording '{title}' ({severity_value}) as a "
                f"FILE-SCOPED finding for '{file}' because line=None. "
                f"It counts toward the verdict. If this finding points at a "
                f"specific line, re-add it with line=<source line>.",
                file=sys.stderr,
            )
        elif not isinstance(line, int) or line <= 0:
            raise ValueError(
                f"line must be a positive integer, got {line}. "
                "Lines are 1-indexed."
            )

        # Warn on implausibly large line numbers — likely patch-file line confusion.
        # When agents read a diff/patch file, the Read tool displays line numbers
        # within the patch (e.g., "227→+class Foo"). Agents sometimes use these
        # patch-file positions instead of the actual source file line numbers.
        if not file_scoped and line > 5000:
            print(
                f"WARNING: line={line} for '{file}' is unusually large. "
                f"Verify this is a source file line number, not a patch file "
                f"display line number from the Read tool.",
                file=sys.stderr,
            )

        finding_id = self._allocate_finding_id()

        finding = {
            'id': finding_id,
            'category': category,
            'severity': severity_value,
            'title': _coerce_text(title, single_line=True),
            'description': _coerce_text(description),
            'file': file,
            'line': line,
            'recommendation': _coerce_text(recommendation),
            'confidence': confidence,
            **extra_fields
        }
        if file_scoped:
            finding['scope'] = 'file'
        if behavior_evidence is not None:
            finding['behavior_evidence'] = behavior_evidence
        if source_cited is not None:
            finding['source_cited'] = source_cited
        if floor_value is not None:
            finding['severity_floor'] = floor_value
        if channel == 'advisory':
            finding['channel'] = channel

        self.findings.append(finding)
        self._invocation_delta.append(
            f"added finding {finding_id} "
            f"{json.dumps(finding['title'], ensure_ascii=False)}"
        )
        return finding_id

    def add_observation(self, file: str, note: str, category: str = "general"):
        """Add a file-level observation (not a finding).

        Observations are informational notes about files that don't have a
        specific line reference. They don't affect the verdict and are
        displayed separately from findings.
        """
        self.observations.append({
            "file": file,
            "note": note,
            "category": category,
        })

    def set_assessment(self, text):
        """Record the overall-state prose this artifact's verdict summarizes.

        Two or three sentences answering "what is the overall state of this
        code?" — the one judgment a list of findings cannot express, and
        the reason the reconciliation Markdown was hand-written before the
        pipeline took ownership of rendering it. Blank prose records
        absence rather than an empty string, so a consumer never has to
        distinguish "said nothing" from "said ''".
        """
        coerced = _coerce_text(text).strip()
        self.assessment = coerced or None
        self._invocation_delta.append("updated assessment")

    def add_recommendation(self, priority: str, text: str):
        """Add recommendation (priority: immediate, important, suggestions)."""
        if priority in self.recommendations:
            self.recommendations[priority].append(_coerce_text(text))

    def add_positive_observation(self, observation: str):
        """Add positive observation."""
        value = _coerce_text(observation)
        self.positive_observations.append(value)
        self._invocation_delta.append(
            "added positive observation "
            + json.dumps(value, ensure_ascii=False)
        )

    @staticmethod
    def _resolve_plugin_version(output_dir: Optional[str]) -> Optional[str]:
        """Name the plugin that produced this artifact, or admit ignorance.

        Two paths to ONE fact, never a second detection of it — the version
        is detected once, at pipeline step 1, and travels from there:

        1. ``PIRATEGOAT_PLUGIN_VERSION`` in the builder envelope, which
           bootstrap fills from the run's ``run-config.json`` stamp. Always
           present in the envelope, sometimes empty (unresolvable run).
        2. That same stamp read from the bound output directory's
           ``run-config.json``, for a builder that has one and no envelope.

        Fails open to None everywhere — an unbound builder outside the
        envelope has no honest answer. An unstamped artifact is honest about
        not knowing; it is never an error and never a guess.
        """
        env_value = os.environ.get("PIRATEGOAT_PLUGIN_VERSION")
        if isinstance(env_value, str) and env_value.strip():
            return env_value.strip()
        if not output_dir:
            return None
        try:
            with open(
                os.path.join(output_dir, "run-config.json"), "r", encoding="utf-8"
            ) as f:
                config = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        stamped = config.get("plugin_version") if isinstance(config, dict) else None
        if isinstance(stamped, str) and stamped.strip():
            return stamped.strip()
        return None

    def _bound_reviewed_files(self):
        """Reviewed files derived from the bound assignment, or None.

        Add-time feedback only: save_draft() derives again, with the real
        claims, and fails closed. The facts this serves — ``channels`` and
        ``review_claimable_files`` — are claim-independent, so it derives
        with no claims: a claim outside the claimable set is save_draft()'s
        to reject, not a reason to go silent at add time.
        """
        if self._paths is None:
            return None
        try:
            with open(self._paths.assignment, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return derive_reviewed_files(data, [])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReviewAssignmentError):
            return None

    @staticmethod
    def _normalize_reviewed_file_claim(file: str) -> str:
        """Normalize one reviewed-file claim.

        Normalizes "./src/x.php", "src\\x.php", and "src//x.php" to one
        form, and rejects forms no scope path can ever take (absolute,
        traversal, drive-prefixed, dot-only) — an unmatched path is not a
        near miss, it is a claim about a file that does not exist in this
        review.
        """
        if not isinstance(file, str) or not file.strip():
            raise ValueError(
                "claim_files_reviewed requires a non-empty file path."
            )
        try:
            return normalize_review_path(file, "claim_files_reviewed")
        except ReviewAssignmentError as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _reject_unknown_reviewed_file_claims(
        paths: List[str], known: frozenset
    ) -> None:
        """Reject claims outside the authoritative claimable set.

        Collect every offender so a review carrying several bad claims costs
        one correction round trip instead of one retry per path.
        """
        valid = (
            "Valid paths: " + ", ".join(sorted(known))
            if known
            else "This review has no review-claimable files, so no claim may be made."
        )
        offenders = ", ".join(repr(p) for p in paths)
        raise ValueError(
            f"claim_files_reviewed received {len(paths)} claim(s) matching no "
            f"review-claimable file of this review: {offenders}. {valid}"
        )

    def _validate_reviewed_file_claims(self, files) -> List[str]:
        """Normalize and membership-check one positive-claim batch.

        Both error classes collect across the whole batch — grammar
        failures as their own messages and membership offenders together —
        so one raise names every problem instead of surfacing them one retry
        at a time. Nothing is recorded until the whole batch passes.
        """
        if not files:
            raise ValueError(
                "claim_files_reviewed requires at least one file path — "
                "a call naming nothing is a no-op, not a claim."
            )
        reviewed_files = self._bound_reviewed_files()
        known = (
            frozenset(reviewed_files.review_claimable_files)
            if reviewed_files is not None else None
        )
        normalized: List[str] = []
        unknown: List[str] = []
        grammar_errors: List[str] = []
        for file in files:
            try:
                path = self._normalize_reviewed_file_claim(file)
            except ValueError as exc:
                grammar_errors.append(str(exc))
                continue
            normalized.append(path)
            if known is not None and path not in known:
                unknown.append(path)
        if grammar_errors or unknown:
            parts = list(grammar_errors)
            if unknown:
                try:
                    self._reject_unknown_reviewed_file_claims(unknown, known)
                except ValueError as exc:
                    parts.append(str(exc))
            raise ValueError("; ".join(parts))
        return normalized

    def _derive_reviewed_files(self, output_dir: str):
        """Return the reviewed files this publication must carry.

        Every draft save uses this path; the bound output directory makes the
        check independent of the optional environment envelope. A caller
        serializing manually via to_dict/to_json knowingly opts out of
        that validation — publication is the enforcing seam.
        """
        assignment_path = review_paths(output_dir, self.reviewer).assignment
        try:
            with open(assignment_path, "r", encoding="utf-8") as handle:
                assignment = json.load(handle)
        except FileNotFoundError as exc:
            raise ValueError(
                "missing authoritative review assignment: "
                f"{assignment_path}"
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "malformed authoritative review assignment: "
                f"{assignment_path}"
            ) from exc
        try:
            return derive_reviewed_files(
                assignment, self.reviewed_file_claims
            )
        except ReviewAssignmentError as exc:
            raise ValueError(
                "malformed authoritative review assignment: "
                f"{exc}"
            ) from exc

    def claim_files_reviewed(self, *files: str):
        """Claim review-claimable files as reviewed, atomically."""
        normalized = self._validate_reviewed_file_claims(files)
        for path in normalized:
            if path not in self.reviewed_file_claims:
                self.reviewed_file_claims.append(path)
                self._invocation_delta.append(f"claimed file {path}")

    def retract_reviewed_file_claims(self, *files: str):
        """Retract existing reviewed-file claims, atomically."""
        if not files:
            raise ValueError(
                "retract_reviewed_file_claims requires at least one file path"
            )
        normalized: List[str] = []
        errors: List[str] = []
        for file in files:
            try:
                normalized.append(self._normalize_reviewed_file_claim(file))
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            raise ValueError("; ".join(errors))
        missing = [
            path for path in normalized if path not in self.reviewed_file_claims
        ]
        if missing:
            raise ValueError(
                "retract_reviewed_file_claims received paths that are not "
                "currently claimed: " + ", ".join(missing)
            )
        retracted = set(normalized)
        self.reviewed_file_claims = [
            path for path in self.reviewed_file_claims if path not in retracted
        ]
        self._invocation_delta.extend(
            f"retracted file {path}" for path in normalized
        )

    def set_confidence(self, score: float):
        """Set overall confidence score."""
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {score}")
        self.overall_confidence = score

    def mark_not_applicable(self, reason: str):
        """Mark this review as not applicable — changes not relevant to this domain.

        Use this when the Quick Relevance Check determines the diff has no
        changes relevant to this agent's specialty, or when NO_DOMAIN_FILES
        is returned by scope discovery. Produces a 'not_applicable' verdict
        so the reconciliator knows the agent abstained rather than endorsed.
        """
        if not reason or not reason.strip():
            raise ValueError(
                "mark_not_applicable requires a non-empty reason explaining "
                "why the changes are not relevant to this domain."
            )
        if self.findings:
            raise ValueError(
                "Cannot mark review as not_applicable — "
                f"{len(self.findings)} finding(s) already recorded. "
                "An agent that found findings reviewed the code; "
                "it should not also claim the changes are irrelevant."
            )
        self._not_applicable = True
        self._skip_reason = reason.strip()

    def _calculate_verdict(self) -> str:
        """Auto-calculate verdict from findings."""
        if self._not_applicable:
            return 'not_applicable'
        return derive_review_state(self.findings)['verdict']

    def to_dict(self) -> Dict:
        """Build the review content as a dictionary from this builder's own state.

        Carries content plus ``reviewer``. It has no authority over the six
        reviewed-file fields — ``save_draft`` stitches those on separately via
        ``reviewed_files_fields()``, from the one authoritative derivation.
        """
        review_duration = self._review_duration_ms(self._output_dir)

        derived = derive_review_state(self.findings)
        verdict = 'not_applicable' if self._not_applicable else derived['verdict']
        summary = {
            'total_findings': len(self.findings),
            'by_severity': derived['counts'],
        }
        if self._not_applicable:
            # The abstention short-circuits before channel tags are consulted,
            # so no finding was excluded from its verdict calculation.
            summary['suppressed_advisory_finding_count'] = 0
        else:
            summary.update(derived['advisory'])

        result = {
            'pr_id': self.pr_id,
            'reviewer': self.reviewer,
            'timestamp': self.timestamp,
            'plugin_version': self._resolve_plugin_version(self._output_dir),
            'schema': REVIEW_OUTPUT_SCHEMA,
            'verdict': verdict,
            'summary': summary,
            'findings': self.findings,
            'observations': self.observations if self.observations else None,
            'recommendations': self.recommendations if any(self.recommendations.values()) else None,
            'positive_observations': self.positive_observations if self.positive_observations else None,
            'checks': list(self.checks),
            'assessment': self.assessment,
            'meta': {
                'review_duration_ms': review_duration,
                'confidence_score': self.overall_confidence,
                'next_finding_number': self.next_finding_number,
                'next_check_number': self.next_check_number,
            }
        }
        if self._skip_reason:
            result['skip_reason'] = self._skip_reason
        return result

    def _review_duration_ms(self, output_dir: Optional[str]) -> Optional[int]:
        """Milliseconds from this actor's dispatch to now, or None.

        Derived from the dispatch marker the pipeline wrote — the one clock
        that spans the actual review. A negative interval (marker stamped
        after this serialization, which no ordering produces) is discarded
        rather than published: a wrong number is worse than a missing one.
        """
        started = _actor_start_time(output_dir, self.reviewer)
        if started is None:
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        if elapsed < 0:
            return None
        return int(elapsed * 1000)

    def to_json(self, indent: int = 2) -> str:
        """Generate JSON from this builder's own state."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save_draft(self) -> dict[str, str]:
        """Validate and replace this builder's complete bound draft."""
        if self._output_dir is None or self._paths is None:
            raise ValueError(
                "save_draft requires ReviewOutputBuilder.open(...)"
            )
        reviewed_files = self._derive_reviewed_files(self._output_dir)
        off_channel = sorted({
            finding.get("channel") or "blocking" for finding in self.findings
        } - set(reviewed_files.channels))
        if off_channel:
            raise ValueError(
                f"findings use channel(s) {off_channel} not among this reviewer's "
                f"channels {list(reviewed_files.channels)}"
            )
        document = {**self.to_dict(), **reviewed_files_fields(reviewed_files)}
        draft_bytes = json.dumps(
            document, indent=2, ensure_ascii=False
        ).encode("utf-8")
        review = validate_review_document(document, self.reviewer)
        agent_name = reviewed_files.agent_name
        review_digest = hashlib.sha256(draft_bytes).hexdigest()

        with output_dir_lock(self._output_dir):
            require_review_intake_open(self._output_dir)
            require_not_finalized(self._paths)
            current_digest = _optional_file_digest(self._paths.draft)
            if current_digest != self._base_digest:
                raise ValueError("draft changed; reopen before saving")
            _atomic_replace_bytes(self._paths.draft, draft_bytes)
            try:
                _log_agent_review_draft_saved_telemetry(
                    self._output_dir, agent_name, review_digest
                )
            except Exception as exc:
                print(
                    "WARNING: draft saved, but agent_review_draft_saved "
                    f"telemetry failed: {exc}",
                    file=sys.stderr,
                )

        self._base_digest = review_digest
        self._last_saved_review = review
        return self._draft_receipt(review_digest, reviewed_files)

    def _draft_receipt(
        self, review_digest: str, reviewed_files
    ) -> dict[str, str]:
        """Print and return the compact next-action surface for one save."""
        review = self._last_saved_review
        summary = review["summary"]
        severity_parts = [
            f"{severity} {summary['by_severity'][severity]}"
            for severity in _VALID_SEVERITIES
            if summary["by_severity"][severity]
        ]
        findings = f"findings {summary['total_findings']}"
        if severity_parts:
            findings += f" ({', '.join(severity_parts)})"
        totals = [findings]
        if review["checks"]:
            totals.append(f"checks {len(review['checks'])}")
        if review.get("observations"):
            totals.append(f"observations {len(review['observations'])}")

        command = finalize_review_command(
            os.path.abspath(__file__),
            self._output_dir,
            self.reviewer,
            review_digest,
        )
        print(f"DRAFT SAVED: verdict {review['verdict']}")
        print(f"DRAFT TOTALS: {' | '.join(totals)}")
        unclaimed = list(review["unclaimed_review_files"])
        if unclaimed:
            shown = ", ".join(unclaimed[:3])
            if len(unclaimed) > 3:
                shown += f" (+{len(unclaimed) - 3} more)"
            # A target of ~0 calls is not a target worth repeating.
            budget = reviewed_files.review_budget
            if budget:
                shown += f" | target ~{budget} tool calls"
            print(
                "FILES NOT YET CLAIMED AS REVIEWED "
                f"({len(unclaimed)}): {shown}"
            )
        if self._invocation_delta:
            print(f"CHANGED: {' | '.join(self._invocation_delta)}")
        print(f"FINALIZE REVIEW: {command}")
        self._invocation_delta = []
        return {
            "draft": self._paths.draft,
            "review_digest": review_digest,
            "finalize_review_command": command,
        }


def _read_json_object(path, label):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"malformed {label}: expected an object")
    return value


REVIEW_CONTENT_FIELDS = frozenset({
    "pr_id",
    "timestamp",
    "plugin_version",
    "schema",
    "verdict",
    "summary",
    "findings",
    "observations",
    "recommendations",
    "positive_observations",
    "checks",
    "assessment",
    "meta",
})
REVIEWER_FIELDS = frozenset({
    "reviewer",
    "review_claimable_files",
    "reviewed_file_claims",
    "unclaimed_review_files",
    "inline_diff_file_count",
    "reviewed_file_count",
    "in_scope_review_file_count",
})


def reviewed_files_fields(reviewed_files) -> Dict:
    """The six reviewer-envelope reviewed-file fields, from one derivation.

    ``save_draft`` stitches these onto ``to_dict()``'s content to build the
    complete draft document; nothing else may assemble them piecemeal.
    """
    return {
        "review_claimable_files": list(reviewed_files.review_claimable_files),
        "reviewed_file_claims": list(reviewed_files.reviewed_file_claims),
        "unclaimed_review_files": list(reviewed_files.unclaimed_review_files),
        "inline_diff_file_count": reviewed_files.inline_diff_file_count,
        "reviewed_file_count": reviewed_files.reviewed_file_count,
        "in_scope_review_file_count": reviewed_files.in_scope_review_file_count,
    }


_OPTIONAL_REVIEW_FIELDS = frozenset({"skip_reason"})
_REQUIRED_FINDING_FIELDS = frozenset({
    "id",
    "category",
    "severity",
    "title",
    "description",
    "file",
    "line",
    "recommendation",
    "confidence",
})
_REQUIRED_META_FIELDS = frozenset({
    "review_duration_ms",
    "confidence_score",
    "next_finding_number",
    "next_check_number",
})
_OPTIONAL_META_FIELDS = frozenset()
_ALLOWED_META_FIELDS = _REQUIRED_META_FIELDS | _OPTIONAL_META_FIELDS


def _is_confidence(value):
    return type(value) in (int, float) and 0.0 <= value <= 1.0


def _is_string_list(value):
    return isinstance(value, list) and all(
        isinstance(item, str) for item in value
    )


def _canonical_id_number(value, prefix, label):
    if not isinstance(value, str) or not re.fullmatch(
        rf"{prefix}[1-9][0-9]*", value
    ):
        raise ValueError(f"{label}.id must be a canonical {prefix}N id")
    return int(value[1:])


def validate_finding_content_field(field, value, label):
    """Validate one critic-adjustable value against the review domain."""
    if field in (
        "category",
        "title",
        "description",
        "file",
        "recommendation",
    ):
        if not isinstance(value, str):
            raise ValueError(f"{label}.{field} must be a string")
        return
    if field == "severity":
        if value not in _VALID_SEVERITIES:
            raise ValueError(f"{label}.severity is invalid")
        return
    if field == "confidence":
        if not _is_confidence(value):
            raise ValueError(f"{label}.confidence must be 0.0-1.0")
        return
    if field == "line" and value is not None and (
        type(value) is not int or value <= 0
    ):
        raise ValueError(
            f"{label}.line must be a positive (1-indexed) integer or null, "
            f"got {value!r}"
        )


def _validate_finding_shape(finding, index):
    """Validate fields emitted by ``ReviewOutputBuilder.add_finding``."""
    if not isinstance(finding, dict):
        raise ValueError(f"review finding {index} must be an object")
    missing = sorted(_REQUIRED_FINDING_FIELDS - set(finding))
    if missing:
        raise ValueError(
            f"review finding {index} is missing required fields: "
            + ", ".join(missing)
        )
    _canonical_id_number(finding["id"], "f", f"review finding {index}")
    for field in (
        "category",
        "severity",
        "title",
        "description",
        "file",
        "line",
        "recommendation",
        "confidence",
    ):
        validate_finding_content_field(
            field, finding[field], f"review finding {index}"
        )
    if "scope" in finding and finding["scope"] != "file":
        raise ValueError(
            f"review finding {index}.scope must be 'file'"
        )
    if (
        "severity_floor" in finding
        and finding["severity_floor"] not in _VALID_SEVERITIES
    ):
        raise ValueError(
            f"review finding {index}.severity_floor is invalid"
        )
    if "channel" in finding and finding["channel"] not in _VALID_CHANNELS:
        raise ValueError(
            f"review finding {index}.channel is invalid"
        )
    if (
        "behavior_evidence" in finding
        and finding["behavior_evidence"] not in ("cited", "inferred")
    ):
        raise ValueError(
            f"review finding {index}.behavior_evidence is invalid"
        )
    for field in ("code_snippet", "source_cited"):
        if field in finding and not isinstance(finding[field], str):
            raise ValueError(
                f"review finding {index}.{field} must be a string"
            )
    if (
        "references" in finding
        and not _is_string_list(finding["references"])
    ):
        raise ValueError(
            f"review finding {index}.references must be strings"
        )


def _validate_check_shape(check, index):
    """Validate one canonical check without inferring materiality."""
    required = {"id", "question", "method", "result", "source_reviewers"}
    allowed = required | {"critic_adjustment"}
    if not isinstance(check, dict):
        raise ValueError(f"review check {index} must be an object")
    if not required <= set(check) or not set(check) <= allowed:
        missing = sorted(required - set(check))
        unexpected = sorted(set(check) - allowed)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ValueError(
            f"review check {index} has invalid fields: " + "; ".join(details)
        )
    _canonical_id_number(check["id"], "c", f"review check {index}")
    for field in ("question", "method", "result"):
        if not isinstance(check[field], str) or not check[field].strip():
            raise ValueError(
                f"review check {index}.{field} must be a non-empty string"
            )
    sources = check["source_reviewers"]
    if (
        not isinstance(sources, list)
        or not sources
        or any(
            not isinstance(source, str) or not source.strip()
            for source in sources
        )
        or len(sources) != len(set(sources))
    ):
        raise ValueError(
            f"review check {index}.source_reviewers must be unique "
            "non-empty strings"
        )


def validate_review_domain(findings, checks, assessment, meta):
    """Validate the builder-owned finding/check/assessment domain model."""
    if not isinstance(findings, list):
        raise ValueError("review findings must be a list")
    finding_numbers = []
    for index, finding in enumerate(findings):
        _validate_finding_shape(finding, index)
        finding_numbers.append(int(finding["id"][1:]))
    if len(finding_numbers) != len(set(finding_numbers)):
        raise ValueError("review finding ids must be unique")

    if not isinstance(checks, list):
        raise ValueError("review checks must be a list")
    check_numbers = []
    for index, check in enumerate(checks):
        _validate_check_shape(check, index)
        check_numbers.append(int(check["id"][1:]))
    if len(check_numbers) != len(set(check_numbers)):
        raise ValueError("review check ids must be unique")

    if assessment is not None and not isinstance(assessment, str):
        raise ValueError("review assessment must be a string or null")
    if not isinstance(meta, dict):
        raise ValueError("review meta must be an object")
    for field, numbers in (
        ("next_finding_number", finding_numbers),
        ("next_check_number", check_numbers),
    ):
        value = meta.get(field)
        if type(value) is not int or value < 1:
            raise ValueError(f"review meta.{field} must be a positive integer")
        if numbers and value <= max(numbers):
            raise ValueError(
                f"review meta.{field} must be greater than every live id"
            )


def _validate_optional_review_fields(review):
    observations = review.get("observations")
    if observations is not None and (
        not isinstance(observations, list)
        or any(
            not isinstance(item, dict)
            or any(
                not isinstance(item.get(field), str)
                for field in ("file", "note", "category")
            )
            for item in observations
        )
    ):
        raise ValueError("review observations are malformed")

    recommendations = review.get("recommendations")
    if recommendations is not None and (
        not isinstance(recommendations, dict)
        or set(recommendations) != {"immediate", "important", "suggestions"}
        or any(
            not _is_string_list(recommendations.get(priority))
            for priority in ("immediate", "important", "suggestions")
        )
    ):
        raise ValueError("review recommendations are malformed")

    for field in ("positive_observations",):
        value = review.get(field)
        if value is not None and not _is_string_list(value):
            raise ValueError(f"review {field} must be strings or null")


def _validate_content_shape(document, *, schema):
    """Validate the review content shape before verdict derivation."""
    missing = sorted(REVIEW_CONTENT_FIELDS - set(document))
    if missing:
        raise ValueError(
            "review is missing content fields: " + ", ".join(missing)
        )
    unexpected = sorted(
        set(document) - REVIEW_CONTENT_FIELDS - _OPTIONAL_REVIEW_FIELDS
    )
    if unexpected:
        raise ValueError(
            "review has unexpected fields: " + ", ".join(unexpected)
        )
    if type(document["schema"]) is not int or document["schema"] != schema:
        raise ValueError("review schema does not match the live contract")
    if not isinstance(document["pr_id"], str):
        raise ValueError("review pr_id must be a string")
    if not isinstance(document["timestamp"], str):
        raise ValueError("review timestamp must be an ISO string")
    try:
        datetime.fromisoformat(document["timestamp"])
    except ValueError as exc:
        raise ValueError("review timestamp must be an ISO string") from exc
    if document["plugin_version"] is not None and not isinstance(
        document["plugin_version"], str
    ):
        raise ValueError("review plugin_version must be a string or null")

    meta = document["meta"]
    if not isinstance(meta, dict):
        raise ValueError("review meta must be an object")
    missing_meta = sorted(_REQUIRED_META_FIELDS - set(meta))
    if missing_meta:
        raise ValueError(
            "review meta is missing required fields: "
            + ", ".join(missing_meta)
        )
    unexpected_meta = sorted(set(meta) - _ALLOWED_META_FIELDS)
    if unexpected_meta:
        raise ValueError(
            "review meta has unexpected fields: "
            + ", ".join(unexpected_meta)
        )
    duration = meta["review_duration_ms"]
    if duration is not None and (type(duration) is not int or duration < 0):
        raise ValueError(
            "review meta.review_duration_ms must be non-negative or null"
        )
    if not _is_confidence(meta["confidence_score"]):
        raise ValueError(
            "review meta.confidence_score must be 0.0-1.0"
        )
    validate_review_domain(
        document["findings"],
        document["checks"],
        document["assessment"],
        meta,
    )
    _validate_optional_review_fields(document)


def validate_review_content(document, *, schema):
    """Validate the review content shared by reviewer documents and the ledger."""
    if not isinstance(document, dict):
        raise ValueError("malformed review: expected an object")
    _validate_content_shape(document, schema=schema)

    findings = document["findings"]
    summary = document["summary"]
    if not isinstance(summary, dict):
        raise ValueError("review summary is malformed")
    try:
        derived = derive_review_state(findings)
    except ValueError as exc:
        raise ValueError(f"review findings are malformed: {exc}") from exc
    expected_verdict = derived["verdict"]
    if document.get("verdict") == "not_applicable":
        skip_reason = document.get("skip_reason")
        if (
            findings
            or not isinstance(skip_reason, str)
            or not skip_reason.strip()
        ):
            raise ValueError("review not_applicable verdict is malformed")
        expected_verdict = "not_applicable"
    elif "skip_reason" in document:
        raise ValueError(
            "review skip_reason requires a not_applicable verdict"
        )
    if document.get("verdict") != expected_verdict:
        raise ValueError("review verdict does not match its findings")
    expected_summary = {
        "total_findings": len(findings),
        "by_severity": derived["counts"],
        **derived["advisory"],
    }
    severity_counts = summary.get("by_severity")
    if (
        type(summary.get("total_findings")) is not int
        or not isinstance(severity_counts, dict)
        or set(severity_counts) != set(_VALID_SEVERITIES)
        or any(
            type(severity_counts.get(severity)) is not int
            or severity_counts[severity] < 0
            for severity in _VALID_SEVERITIES
        )
        or type(summary.get("suppressed_advisory_finding_count")) is not int
        or summary["suppressed_advisory_finding_count"] < 0
        or (
            "verdict_without_advisory" in summary
            and summary["verdict_without_advisory"] not in VERDICT_RANK
        )
        or summary != expected_summary
    ):
        raise ValueError("review summary does not match its findings")
    return document


def _validate_reviewer_envelope(review, reviewer):
    """Validate the reviewer envelope as a self-checking file partition."""
    missing = sorted(REVIEWER_FIELDS - set(review))
    if missing:
        raise ValueError(
            "review is missing reviewed-file fields: " + ", ".join(missing)
        )
    if not isinstance(review["reviewer"], str) or review["reviewer"] != reviewer:
        raise ValueError("review reviewer does not match finalization request")
    for field in (
        "review_claimable_files",
        "reviewed_file_claims",
        "unclaimed_review_files",
    ):
        if not _is_string_list(review[field]):
            raise ValueError(f"review {field} must be a list of strings")
    for field in (
        "inline_diff_file_count",
        "reviewed_file_count",
        "in_scope_review_file_count",
    ):
        if type(review[field]) is not int or review[field] < 0:
            raise ValueError(
                f"review {field} must be a non-negative integer"
            )
    claimable = review["review_claimable_files"]
    claims = review["reviewed_file_claims"]
    claimed = set(claims)
    if len(claimable) != len(set(claimable)) or len(claimed) != len(claims):
        raise ValueError("reviewed-file lists must not repeat paths")
    if not claimed <= set(claimable):
        raise ValueError(
            "reviewed-file claim names a file that is not review-claimable"
        )
    if claims != [path for path in claimable if path in claimed]:
        raise ValueError("reviewed-file claims are not in claimable order")
    if review["unclaimed_review_files"] != [
        path for path in claimable if path not in claimed
    ]:
        raise ValueError(
            "reviewed-file unclaimed files are not the complement of the claims"
        )
    if review["reviewed_file_count"] != (
        review["inline_diff_file_count"] + len(claimed)
    ):
        raise ValueError(
            "reviewed-file count does not equal inline plus claims"
        )
    if review["in_scope_review_file_count"] != (
        review["inline_diff_file_count"] + len(claimable)
    ):
        raise ValueError(
            "reviewed-file in-scope count does not equal inline plus claimable"
        )


def validate_review_document(review, reviewer):
    """Validate one complete reviewer document: content plus envelope.

    This is the shared trust boundary for draft rehydration, finalization,
    and finalized-review readers. Validation that depends on an adjacent
    assignment artifact remains in ``_validate_review``.
    """
    if not isinstance(review, dict):
        raise ValueError("malformed review: expected an object")
    _validate_reviewer_envelope(review, reviewer)
    content = {
        key: value for key, value in review.items() if key not in REVIEWER_FIELDS
    }
    validate_review_content(content, schema=REVIEW_OUTPUT_SCHEMA)
    return review


def load_review_document(path, reviewer):
    """Load and validate one canonical final-review document."""
    try:
        with open(path, "r", encoding="utf-8") as source:
            review = json.load(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed final review JSON") from exc
    return validate_review_document(review, reviewer)


def _validate_review(output_dir, reviewer, paths, review_bytes):
    """Validate one exact review snapshot and return telemetry facts."""
    try:
        review = json.loads(review_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("malformed review JSON") from exc
    validate_review_document(review, reviewer)

    assignment = _read_json_object(
        paths.assignment, "review assignment"
    )
    try:
        reviewed_files = derive_reviewed_files(
            assignment, review["reviewed_file_claims"]
        )
    except ReviewAssignmentError as exc:
        raise ValueError(
            f"reviewed-file derivation is malformed: {exc}"
        ) from exc
    derived = reviewed_files_fields(reviewed_files)
    if {key: review[key] for key in derived} != derived:
        raise ValueError(
            "review derived reviewed-file fields do not match the assignment"
        )
    return review, reviewed_files.agent_name


def finalize_review(output_dir: str, reviewer: str, review_digest: str):
    """Validate and atomically promote exactly one observed draft."""
    if (
        not isinstance(review_digest, str)
        or len(review_digest) != 64
        or any(ch not in "0123456789abcdef" for ch in review_digest)
    ):
        raise ValueError("review digest must be a lowercase SHA-256")
    paths = review_paths(output_dir, reviewer)
    already_finalized = False
    with output_dir_lock(output_dir):
        require_review_intake_open(output_dir)
        if os.path.exists(paths.final):
            with open(paths.final, "rb") as final_handle:
                final_bytes = final_handle.read()
            final_digest = hashlib.sha256(final_bytes).hexdigest()
            if final_digest != review_digest:
                raise ValueError(
                    "review digest conflicts with the finalized review"
                )
            review, agent_name = _validate_review(
                output_dir, reviewer, paths, final_bytes
            )
            already_finalized = True
            try:
                os.unlink(paths.draft)
            except FileNotFoundError:
                pass
        else:
            try:
                with open(paths.draft, "rb") as draft_handle:
                    draft_bytes = draft_handle.read()
            except OSError as exc:
                raise ValueError("review draft is absent") from exc
            actual_digest = hashlib.sha256(draft_bytes).hexdigest()
            if actual_digest != review_digest:
                raise ValueError(
                    "review digest no longer matches the saved draft"
                )
            review, agent_name = _validate_review(
                output_dir, reviewer, paths, draft_bytes
            )
            os.replace(paths.draft, paths.final)

        if not _completion_was_logged(
            output_dir, agent_name, review_digest
        ):
            _log_agent_complete_telemetry(
                output_dir,
                agent_name,
                review["verdict"],
                review["summary"]["total_findings"],
                review["summary"]["by_severity"],
                review_digest,
            )
    return {
        "final": paths.final,
        "review_digest": review_digest,
        "already_finalized": already_finalized,
    }


def repair_finalized_completion(output_dir: str, reviewer: str):
    """Repair telemetry for one canonical review during intake close.

    This is not an alternate finalization channel: it never promotes a
    draft and does nothing after intake close unless final JSON
    already exists. The caller holds the shared output-directory lock.
    """
    paths = review_paths(output_dir, reviewer)
    telemetry = _telemetry_for_output(output_dir)
    if telemetry.log_path is None:
        load_review_document(paths.final, reviewer)
        return None
    try:
        with open(paths.final, "rb") as final_handle:
            final_bytes = final_handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("finalized review is unreadable") from exc

    review_digest = hashlib.sha256(final_bytes).hexdigest()
    review, agent_name = _validate_review(
        output_dir, reviewer, paths, final_bytes
    )
    if not _completion_was_logged(output_dir, agent_name, review_digest):
        _log_agent_complete_telemetry(
            output_dir,
            agent_name,
            review["verdict"],
            review["summary"]["total_findings"],
            review["summary"]["by_severity"],
            review_digest,
        )
    return {
        "final": paths.final,
        "review_digest": review_digest,
        "agent_name": agent_name,
    }

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description="Render reviewer Markdown from canonical review JSON.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    render_cmd = sub.add_parser(
        "render", help="Print the Markdown for one *-review.json",
    )
    render_cmd.add_argument("json_path")
    mat_cmd = sub.add_parser(
        "materialize",
        help="Write <name>.md beside every matching <name>.json in a directory",
    )
    mat_cmd.add_argument("output_dir")
    mat_cmd.add_argument(
        "--suffix",
        default="-review.json",
        help=(
            "Which JSON family to render. Default renders every "
            "<reviewer>-review.json; pass review-findings.json to render "
            "the reconciliation ledger — the recovery command step 11 "
            "prints when that render failed."
        ),
    )
    finalize_cmd = sub.add_parser(
        "finalize-review", help="Validate and publish one review draft"
    )
    finalize_cmd.add_argument("--output-dir", required=True)
    finalize_cmd.add_argument("--reviewer", required=True)
    finalize_cmd.add_argument("--review-digest", required=True)
    cli_args = parser.parse_args()
    if cli_args.command == "render":
        cli_data = _load_renderable_review_artifact(cli_args.json_path)
        print(render_markdown(cli_data))
    elif cli_args.command == "materialize":
        for written_path in materialize_markdown(
            cli_args.output_dir, suffix=cli_args.suffix
        ):
            print(written_path)
    else:
        try:
            finalized = finalize_review(
                cli_args.output_dir,
                cli_args.reviewer,
                cli_args.review_digest,
            )
        except (OSError, ValueError) as exc:
            print(f"REJECTED: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(
            "REVIEW FINALIZED: "
            f"{os.path.basename(finalized['final'])}"
        )

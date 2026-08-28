#!/usr/bin/env python3
"""Markdown derived from a review artifact — never written by hand.

Every human-readable review document in an output directory is a pure
function of the JSON beside it: `<reviewer>-review.md` from the reviewer's
final, `review-findings.md` from the reconciliation ledger, and the body
of `review-record.md` from the same renderer, so a rendering can never
disagree with the artifact it came from.

This module exists so that ownership can be stated once. Rendering a
ledger needs `critic_adjustments`' reader; `critic_adjustments` needs the
document validators; the validators used to live beside the renderer in
`agent/output.py`, so the renderer reached for its ledger reader from
inside a function body with a comment explaining that a module-level
import would be cyclic. Here it is a module-level import, because nothing
in this file is imported back.

Usage:
    python3 review_markdown.py render <path>-review.json
    python3 review_markdown.py materialize <output_dir> [--suffix ...]
"""

import os
import sys
from typing import Dict, List

try:
    from . import critic_adjustments
    from .review_document import (
        RECOMMENDATION_PRIORITIES,
        coerce_text,
        load_review_document,
    )
    from .verdict_rules import VALID_SEVERITIES
except ImportError:
    _scripts_parent = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import critic_adjustments
    from review.review_document import (
        RECOMMENDATION_PRIORITIES,
        coerce_text,
        load_review_document,
    )
    from review.verdict_rules import VALID_SEVERITIES


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
        'adjustment_id', critic_adjustments.OUTCOME_KEY
    }:
        return None
    adjustment_id = record.get('adjustment_id')
    outcome = record.get(critic_adjustments.OUTCOME_KEY)
    if (
        not isinstance(adjustment_id, str) or not adjustment_id
        or outcome not in critic_adjustments.OUTCOMES
    ):
        return None
    return adjustment_id, outcome


def _rejected_critic_decision(record):
    """Project one complete schema-2 rejected-decision record."""
    if not isinstance(record, dict) or set(record) != {
        'adjustment_id', 'action', 'target',
        critic_adjustments.OUTCOME_KEY, 'rejection_reason'
    }:
        return None
    adjustment_id = record.get('adjustment_id')
    outcome = record.get(critic_adjustments.OUTCOME_KEY)
    if (
        not isinstance(adjustment_id, str) or not adjustment_id
        or outcome != critic_adjustments.OUTCOME_REFUTED
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
        message = coerce_text(banner.get('message', ''))
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
    # decision renders as `refuted`. Both projections require the outcome to
    # be explicit on the record — the ledger validator has already refused
    # anything else, so a record without one is not a legacy shape to render
    # but a malformed one to drop.
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
    for sev in VALID_SEVERITIES:
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
        known = RECOMMENDATION_PRIORITIES
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
    if data['observations']:
        md.append("\n## Observations\n\n")
        for obs in data['observations']:
            md.append(f"- **`{obs['file']}`** — {obs['note']}\n")

    return ''.join(md)


_RECONCILIATION_LEDGER_NAME = "review-findings.json"


def _load_renderable_review_artifact(path):
    """Classify and load one supported artifact by its actual filename."""
    name = os.path.basename(path)
    if name == _RECONCILIATION_LEDGER_NAME:
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
    cli_args = parser.parse_args()
    if cli_args.command == "render":
        print(render_markdown(_load_renderable_review_artifact(
            cli_args.json_path
        )))
    else:
        for written_path in materialize_markdown(
            cli_args.output_dir, suffix=cli_args.suffix
        ):
            print(written_path)

"""
Simple Review Output Builder (No Dependencies)

Lightweight version without Pydantic for immediate use.
Provides structure and basic validation using plain Python.

Usage:
    from review.agent.output import ReviewOutputBuilder

    builder = ReviewOutputBuilder(pr_id="123", reviewer="security")
    builder.add_issue(
        severity="critical",
        title="SQL Injection",
        file="src/User.php",
        line=42,
        description="...",
        recommendation="..."
    )
    json_output = builder.to_json()
    saved = builder.save(output_dir)  # publishes a replaceable candidate
    finalize_candidate(output_dir, "security", saved["candidate_digest"])

    Markdown is derived from the canonical JSON: render one dict with
    render_markdown(data), or from the shell via the CLI —
    `python3 output.py render <path>-review.json` prints one review's
    Markdown, `python3 output.py materialize <output_dir>` writes
    <reviewer>-review.md beside every *-review.json.
"""

import hashlib
import json
import os
import shlex
import sys
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

try:
    from .coverage import CoverageError, derive_deferred_coverage, normalize_deferred_path
except ImportError:
    _SCRIPTS_DIR = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    from review.agent.coverage import CoverageError, derive_deferred_coverage, normalize_deferred_path

try:
    from ..atomic_io import output_dir_lock
    from ..reviewer_lifecycle import (
        require_not_finalized,
        require_review_intake_open,
        reviewer_paths,
    )
    from ..reviewer_names import derive_reviewer_name
except ImportError:
    from review.atomic_io import output_dir_lock
    from review.reviewer_lifecycle import (
        require_not_finalized,
        require_review_intake_open,
        reviewer_paths,
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
# here would publish a shape no artifact ever had. Schema 1 was introduced
# in 1.114.0, and 1.114.0 is unreleased — the plugin's newest tag is
# pirategoat-tools/v1.108.0 — so shape changes made inside 1.114.0 update
# the TypeScript contract without moving this number.
REVIEW_OUTPUT_SCHEMA = 1

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


def _log_agent_save_telemetry(output_dir, agent_name, artifact_digest):
    telemetry = _telemetry_for_output(output_dir)
    telemetry.log_agent_save(
        agent_name=agent_name, artifact_digest=artifact_digest
    )


def _completion_was_logged(output_dir, agent_name, artifact_digest):
    telemetry = _telemetry_for_output(output_dir)
    return any(
        event.get("event") == "agent_complete"
        and event.get("agent") == agent_name
        and event.get("artifact_digest") == artifact_digest
        for event in telemetry._read_events()
    )


def _log_agent_complete_telemetry(
    output_dir, agent_name, verdict, issue_count, severities, artifact_digest
):
    telemetry = _telemetry_for_output(output_dir)
    telemetry.log_agent_complete(
        agent_name=agent_name,
        verdict=verdict,
        issue_count=issue_count,
        severities=severities,
        artifact_digest=artifact_digest,
    )


def render_markdown(data: Dict) -> str:
    """Human-readable Markdown rendered from a review's canonical dict.

    A pure function of the JSON representation — the same dict
    to_dict()/to_json() produce and the *-review.json file holds — so a
    rendering can never disagree with the artifact it came from.

    Keys present in schema 1 are required (missing means KeyError — the
    caller's problem); later schema additions are read with .get() and
    render only when present.

    Title plus ``render_review_body()``, which is everything below it. The
    split exists because `review-record.md` — the pipeline's machine
    projection of the reconciliation ledger — needs that body under its own
    header, and a second copy of these sections is how the record and
    `review-findings.md` would eventually disagree about a finding.
    """
    return (
        f"# {data['reviewer'].title()} Review - PR #{data['pr_id']}\n\n"
        + render_review_body(data)
    )


def _applied_critic_decision(record):
    """Normalize one applied-decision record for Markdown projection."""
    if isinstance(record, str) and record:
        return record, 'not_checked'
    if not isinstance(record, dict):
        return None
    adjustment_id = record.get('adjustment_id')
    outcome = record.get('spot_check', 'not_checked')
    if (
        not isinstance(adjustment_id, str) or not adjustment_id
        or outcome not in ('verified', 'refuted', 'not_checked')
    ):
        return None
    return adjustment_id, outcome


def _rejected_critic_decision(record):
    """Normalize one rejected-decision record, including legacy records."""
    if not isinstance(record, dict):
        return None
    adjustment_id = record.get('adjustment_id')
    outcome = record.get('spot_check')
    if (
        not isinstance(adjustment_id, str) or not adjustment_id
        or outcome not in (None, 'refuted')
    ):
        return None
    return adjustment_id, 'refuted'


def render_review_body(data: Dict) -> str:
    """Everything a rendered review says beneath its title.

    Banner, executive summary, assessment, critic accounting, issues,
    recommendations, clearances, critic removals, positives, observations —
    the whole document minus the H1. Shared verbatim by
    ``render_markdown()`` (which supplies the per-reviewer title) and by
    the review-record assembler in ``orchestration.py`` (which supplies its
    own). Same contract as ``render_markdown()``: a pure function of the
    canonical dict, schema-1 keys required, later additions read with
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
    md.append(f"**Total Issues:** {data['summary']['total_issues']}\n\n")

    advisory_suppressed = data['summary'].get('advisory_suppressed', 0)
    if advisory_suppressed:
        finding_word = "finding" if advisory_suppressed == 1 else "findings"
        md.append(
            f"**Advisory suppression:** {advisory_suppressed} {finding_word} "
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

    if data['summary']['total_issues'] > 0:
        counts = data['summary']['by_severity']
        md.append(f"- Critical: {counts['critical']}\n")
        md.append(f"- High: {counts['high']}\n")
        md.append(f"- Medium: {counts['medium']}\n\n")

    # Coverage gap — two populations share the 'unreviewed' array but not a
    # reason, so they never share a label: what the reviewer declared at
    # budget exhaustion, and what save() auto-declared because it was
    # neither claimed nor declared. Filing the latter under "budget" would
    # attribute the system's backfill to the reviewer's judgment. Older
    # outputs carry no marker and render exactly as they used to.
    if data.get('unreviewed'):
        meta = data.get('meta')
        marker = meta.get('unreviewed_autofilled') if isinstance(meta, dict) else None
        # A non-list marker says nothing usable about membership (a string
        # would split into a set of characters), so it is ignored and every
        # path keeps the declared label.
        autofilled = set(marker) if isinstance(marker, list) else set()
        declared = [f for f in data['unreviewed'] if f not in autofilled]
        auto_declared = [f for f in data['unreviewed'] if f in autofilled]
        if declared:
            files = ", ".join(f"`{f}`" for f in declared)
            md.append(f"**Not reviewed (budget):** {files}\n\n")
        if auto_declared:
            files = ", ".join(f"`{f}`" for f in auto_declared)
            md.append(
                "**Not reviewed (unaccounted — auto-declared at save):** "
                f"{files}\n\n"
            )

    # Reconciliation accounting — the narrative's "Pipeline:" line, now
    # rendered from the metrics the producer already records under
    # meta.reconciliation. Absent for ordinary reviewers, whose meta
    # carries no such block.
    meta = data.get('meta')
    recon = meta.get('reconciliation') if isinstance(meta, dict) else None
    if isinstance(recon, dict):
        md.append(
            f"**Pipeline:** {recon.get('input_findings_count', 0)} findings "
            f"from {recon.get('agents_contributing', 0)} reviewing agents "
            f"\u2192 {recon.get('verified_concerns', 0)} verified concerns "
            f"({recon.get('concerns_after_grouping', 0)} concerns after "
            f"grouping, {recon.get('false_positives_dropped', 0)} false "
            f"positives dropped, {recon.get('out_of_scope_dropped', 0)} "
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
    # correct: its adjustment vocabulary addresses issues, and this is
    # ledger-level prose. So an applying batch WITHDRAWS it
    # (critic_adjustments.py) rather than leaving a stale claim rendered
    # above the list that contradicts it, and this renders the withdrawal
    # instead of silently dropping the section — an absent Assessment and a
    # retracted one are different facts. Prose that survived a critic round
    # untouched still renders as prose: that is the STAND case, and the
    # marker below says exactly whose words they are.
    withdrawn = data.get('withdrawn_narrative_summary')
    if data.get('narrative_summary'):
        md.append("## Assessment\n\n")
        md.append(f"{data['narrative_summary']}\n\n")
        # Whose words these are depends on whether a batch already withdrew
        # the reconciler's. After a withdrawal the standing text is the
        # orchestrator's `revised_narrative`, carried in through the
        # adjustments channel — attributing it to the reconciler would
        # credit prose that was retracted a step earlier.
        md.append(
            "*Post-critic assessment, written after the critic "
            "adjustments applied.*\n\n"
            if withdrawn else
            "*Reconciler-authored assessment, not adjusted by the decision "
            "critic.*\n\n"
        )
    elif withdrawn:
        # Keyed on the withdrawal record itself, not on
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
            # reconciliation-plus-critic round the withdrawn text may be the
            # orchestrator's own `revised_narrative` from the first round,
            # and naming an author this section cannot know would be a
            # claim rather than a description.
            "No current assessment: the standing assessment was withdrawn "
            "under critic revision and not replaced; see the findings.\n\n"
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

    # Issues — every severity that counts toward total_issues must render,
    # or the Markdown claims findings it doesn't show.
    for sev in ['critical', 'high', 'medium', 'low', 'info']:
        sev_issues = [i for i in data['issues'] if i['severity'] == sev]

        if sev_issues:
            md.append(f"## {sev.title()} Issues\n\n")

            for issue in sev_issues:
                md.append(f"### {issue['title']}\n\n")
                if issue['line']:
                    location = f"**File:** `{issue['file']}` line {issue['line']}"
                elif issue.get('scope') == 'file':
                    location = f"**File:** `{issue['file']}` (file-scoped)"
                else:
                    location = f"**File:** `{issue['file']}`"
                md.append(location + "\n\n")
                md.append(f"{issue['description']}\n\n")
                if issue.get('severity_floor'):
                    md.append(f"**Severity floor:** {issue['severity_floor']}\n\n")
                md.append(f"**Fix:** {issue['recommendation']}\n\n")

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

    # Clearances — absence claims with their verification method
    if data.get('clearances'):
        md.append("## Clearances (verified absences)\n\n")
        for c in data['clearances']:
            md.append(f"- **{c['claim']}**\n")
            md.append(f"  - Method: {c['method']}\n")
            if c.get('evidence'):
                md.append(f"  - Evidence: {c['evidence']}\n")
        md.append("\n")

    # What the critic took out. The ledger deliberately moves a removed
    # finding into `removed_by_critic` rather than deleting it, so the
    # decision stays auditable; a reading copy that dropped the section
    # would hide exactly the record the JSON went out of its way to keep.
    removed = data.get('removed_by_critic')
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


def materialize_markdown(
    output_dir: str, *, suffix: str = "-review.json"
) -> List[str]:
    """Render <name>.md beside every <name>.json matching `suffix`.

    Derived artifacts for humans browsing the output directory: idempotent,
    regenerated from the settled canonical JSON, read by no pipeline
    consumer for control flow (readiness, reconciliation, and the bot all
    key on the JSON). Malformed JSONs are skipped with a note on stderr —
    grading and reconciliation report those failures on their own channels.

    `suffix` is what lets ONE materializer own every derived Markdown in a
    run directory: the default covers the per-reviewer family the step-8
    readiness gate renders, and `suffix="review-findings.json"` (an exact
    filename, which is also a suffix that nothing else in the directory
    matches) covers the reconciliation ledger the pipeline renders at
    steps 9 and 11. A second copy of this loop is how the two would
    eventually disagree about what a rendering means.
    """
    written: List[str] = []
    for name in sorted(os.listdir(output_dir)):
        if not name.endswith(suffix):
            continue
        json_path = os.path.join(output_dir, name)
        try:
            with open(json_path, encoding="utf-8") as handle:
                data = json.load(handle)
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
        self.issues = []
        self.observations = []
        self.recommendations = {'immediate': [], 'important': [], 'suggestions': []}
        self.positive_observations = []
        self.clearances = []
        # Agent-authored: the producer's own reading of the change as a
        # whole. The reconciliator's overall-state prose lives here.
        self.narrative_summary = None
        # Agent-authored: gaps the reviewer declared (plus, after save(),
        # the derived fill below merged in).
        self.unreviewed = []
        # Agent-authored: deferred files the reviewer claims it read.
        self.deferred_reviewed = []
        # Derived at save(): the subset of self.unreviewed the builder
        # auto-declared because the reviewer stated nothing about it.
        self.unreviewed_autofilled = []
        # None, never 0: an unset count and a reviewer that genuinely
        # reviewed nothing are different facts, and only the reviewer can
        # state the second one. See set_files_reviewed().
        self.files_reviewed = None
        self.tool_results_used = []
        self.overall_confidence = 0.95
        self._not_applicable = False
        self._skip_reason = None
        self._deferred_files_loaded = False
        self._deferred_files = None
        self._advisory_entitlement_loaded = False
        self._advisory_entitlement = None

    def add_issue(
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
        """Add an issue. Returns issue ID.

        Line is required for point defects — the reviewer protocol mandates
        diff-anchored findings for anything that has a line. Findings that are
        line-less BY NATURE (missing test coverage, missing assertions,
        git-history precedent, cross-file architecture) may pass line=None:
        they are recorded as first-class FILE-SCOPED issues (line: null,
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
            if (
                channel == "advisory"
                and self._known_advisory_entitlement() is False
            ):
                raise ValueError(
                    "Cannot record advisory finding: this reviewer is not "
                    "entitled to the advisory channel."
                )

        # Validate line — None records a first-class file-scoped issue (loud),
        # hard enforcement for invalid values (0, negative, non-int).
        file_scoped = line is None
        if file_scoped:
            # A legitimately line-less finding (missing coverage, precedent,
            # cross-file architecture) is a real, verdict-counting issue.
            # Point defects still need line= — hence the stderr NOTE.
            print(
                f"NOTE: recording '{title}' ({severity_value}) as a "
                f"FILE-SCOPED issue for '{file}' because line=None. "
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

        issue_id = str(uuid.uuid4())[:8]

        issue = {
            'id': issue_id,
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
            issue['scope'] = 'file'
        if behavior_evidence is not None:
            issue['behavior_evidence'] = behavior_evidence
        if source_cited is not None:
            issue['source_cited'] = source_cited
        if floor_value is not None:
            issue['severity_floor'] = floor_value
        if channel == 'advisory':
            issue['channel'] = channel

        self.issues.append(issue)
        return issue_id

    def add_observation(self, file: str, note: str, category: str = "general"):
        """Add a file-level observation (not a finding).

        Observations are informational notes about files that don't have a
        specific line reference. They don't affect the verdict and are
        displayed separately from issues.
        """
        self.observations.append({
            "file": file,
            "note": note,
            "category": category,
        })

    def set_narrative_summary(self, text):
        """Record the overall-state prose this artifact's verdict summarizes.

        Two or three sentences answering "what is the overall state of this
        code?" — the one judgment a list of findings cannot express, and
        the reason the reconciliation Markdown was hand-written before the
        pipeline took ownership of rendering it. Blank prose records
        absence rather than an empty string, so a consumer never has to
        distinguish "said nothing" from "said ''".
        """
        coerced = _coerce_text(text).strip()
        self.narrative_summary = coerced or None

    def add_recommendation(self, priority: str, text: str):
        """Add recommendation (priority: immediate, important, suggestions)."""
        if priority in self.recommendations:
            self.recommendations[priority].append(_coerce_text(text))

    def add_positive(self, observation: str):
        """Add positive observation."""
        self.positive_observations.append(observation)

    def add_clearance(self, claim: str, method: str, evidence: Optional[str] = None):
        """Record an auditable absence claim ("nothing depends on this").

        Use for blast-radius clears: "no CSS selects the removed element",
        "no caller uses the deleted parameter", "no test targets this row".
        Unlike positive observations (which reconciliation excludes),
        clearances flow into the reconciliation context WITH their method,
        so conflicts with other agents' findings are visible and search
        coverage can be judged downstream.

        Args:
            claim: The absence being asserted.
            method: The exact searches run / files read that ground the claim
                (e.g. "grep -rn 'th label' client/legacy/css/; read each hit").
                Required — an absence claim without its method is unauditable.
            evidence: Optional supporting detail — hit counts, a file:line
                list, and, at reconciliation, WHO the clearance came from
                ("per security-reviewer, concurrency-reviewer — 0 in-tree
                consumers"). Attribution rides here by convention rather
                than in its own field because the reconciliator collapses
                method-correlated clearances into one entry: the names of
                every agent that ran the shared probe are what survives
                that merge, and they have nowhere else to go. Nothing
                validates the convention — it is a documented contract
                between `agents/review-reconciliator.md` and this field's
                readers.
        """
        if not claim or not claim.strip():
            raise ValueError("add_clearance requires a non-empty claim.")
        if not method or not method.strip():
            raise ValueError(
                "add_clearance requires a non-empty method — state the exact "
                "searches/reads that ground the claim so downstream stages "
                "can judge their coverage."
            )
        self.clearances.append({
            "claim": claim.strip(),
            "method": method.strip(),
            "evidence": evidence.strip() if evidence and evidence.strip() else None,
        })

    @staticmethod
    def _resolve_plugin_version(output_dir: Optional[str]) -> Optional[str]:
        """Name the plugin that produced this artifact, or admit ignorance.

        Two paths to ONE fact, never a second detection of it — the version
        is detected once, at pipeline step 1, and travels from there:

        1. ``PIRATEGOAT_PLUGIN_VERSION`` in the builder envelope, which
           bootstrap fills from the run's ``run-config.json`` stamp. Always
           present in the envelope, sometimes empty (unresolvable run).
        2. That same stamp read directly, when serialization was given an
           explicit output directory. This is the reconciliator's path: it
           is dispatched by the orchestrator rather than bootstrap, so no
           envelope reaches it, yet ``review-findings.json`` — the artifact
           a human actually receives — must still name its producer.

        Fails open to None everywhere. An unstamped artifact is honest about
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

    @staticmethod
    def _read_deferred_sidecar(
        output_dir: Optional[str], reviewer: Optional[str]
    ) -> Dict:
        """Parse the bootstrap-written deferred-files sidecar, or ``{}``.

        The one read path both consumers share — the NOT DIFFED auto-fill
        set (``_load_deferred_files``) and the review-budget target
        (``_review_budget_target``) — so the sidecar is opened and parsed
        in exactly one place. ``{}`` covers every failure uniformly (no
        output dir/reviewer, missing file, unreadable, malformed, or not a
        JSON object): callers decide what a given schema entitles them to
        read, this helper only ever hands back a dict.
        """
        if not output_dir or not reviewer:
            return {}
        sidecar = os.path.join(output_dir, f"{reviewer}-deferred-files.json")
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _load_deferred_files(
        output_dir: Optional[str], reviewer: Optional[str]
    ) -> Optional[frozenset]:
        """Load the bootstrap-written deferred set, or None when unavailable.

        None is deliberate fail-open: no sidecar means no authoritative set
        exists (manual builder use, older bootstrap, failed fail-open write)
        and validation stays form-only. Tolerates schema 1 and schema 2 —
        both carry ``deferred_files`` in the same shape.
        """
        data = ReviewOutputBuilder._read_deferred_sidecar(output_dir, reviewer)
        files = data.get("deferred_files")
        if not isinstance(files, list):
            return None
        return frozenset(p for p in files if isinstance(p, str))

    @staticmethod
    def _review_budget_target(
        output_dir: Optional[str], reviewer: Optional[str]
    ) -> Optional[int]:
        """The run's tool-call target, or None when there isn't an honest one.

        Read from the same deferred-files sidecar as ``_load_deferred_files``
        (schema 2 adds ``review_budget`` alongside the deferred set), through
        the shared ``_read_deferred_sidecar`` helper — replacing the
        retired env-var budget envelope, which silently died for any agent
        that rebuilt its save command. A schema-1 sidecar (an
        older run, written before the budget field existed) has no honest
        target: reporting one anyway would state a compatibility guarantee
        the producer never made, so any schema other than 2 reports None,
        the same as an absent file, an absent key, or a non-positive value.
        This value is only ever shown back to the reviewer, and a target of
        "0" or a fabricated number is worse than none.
        """
        data = ReviewOutputBuilder._read_deferred_sidecar(output_dir, reviewer)
        if data.get("schema") != 2:
            return None
        value = data.get("review_budget")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return value

    def _known_deferred_files(self) -> Optional[frozenset]:
        """The deferred set via the env envelope — add-time fast feedback.

        Authoritative enforcement happens at save() with the explicit
        output directory; this lookup only makes add_unreviewed() fail
        earlier on the recommended path.
        """
        if self._deferred_files_loaded:
            return self._deferred_files
        self._deferred_files_loaded = True
        self._deferred_files = self._load_deferred_files(
            os.environ.get("PIRATEGOAT_OUTPUT_DIR"),
            os.environ.get("PIRATEGOAT_REVIEWER_NAME"),
        )
        return self._deferred_files

    @staticmethod
    def _normalize_deferred_path(file: str, api_name: str) -> str:
        """The one path grammar both deferred-set APIs speak.

        Declarations and claims address the same namespace — the canonical
        repo-relative paths scope.py emits — so they must accept and reject
        exactly the same spellings. Keeping the grammar here rather than in
        each API is what stops the two from drifting: when they lived apart,
        claims accepted '/etc/passwd' and '../x' that declarations rejected.

        Normalizes "./src/x.php", "src\\x.php", and "src//x.php" to one
        form, and rejects forms no scope path can ever take (absolute,
        traversal, drive-prefixed, dot-only) — an unmatched path is not a
        near miss, it is a coverage statement about a file that does not
        exist in this review.
        """
        if not isinstance(file, str) or not file.strip():
            raise ValueError(f"{api_name} requires a non-empty file path.")
        try:
            return normalize_deferred_path(file, api_name)
        except CoverageError as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _reject_unknown_deferred(
        paths: List[str], known: frozenset, api_name: str, noun: str
    ) -> None:
        """Raise the one canonical rejection for out-of-set deferred paths.

        Every enforcement point shares this phrasing. Add-time passes the
        single path just offered so feedback stays immediate; save-time
        passes every offender at once, so a review carrying 23 bad
        declarations costs one round trip instead of 23. ``api_name`` names
        the calling API, keeping rejections from the sibling deferred-set
        APIs distinguishable to agent and test alike, and ``noun`` says what
        the offending paths were offered as ("declaration", "claim").

        ``noun`` is deliberately required rather than defaulted: a default
        is how the empty-set branch came to tell a claimant that "nothing
        may be declared", and the next sibling API would inherit the same
        wrong word silently.
        """
        valid = (
            "Valid paths: " + ", ".join(sorted(known))
            if known
            else f"This review has no deferred files, so no {noun} may be "
                 "made."
        )
        offenders = ", ".join(repr(p) for p in paths)
        raise ValueError(
            f"{api_name} received {len(paths)} {noun}(s) matching no "
            f"NOT DIFFED file of this review: {offenders}. {valid}"
        )

    def _validate_deferred_batch(
        self, files, api_name: str, noun: str
    ) -> List[str]:
        """Normalize and membership-check a whole batch, or raise once.

        The one validation body both deferred-set APIs run. They address
        the same namespace under the same rules, so a second copy is a
        drift generator, not a convenience — the last time these APIs kept
        their own loops, one accepted absolute and traversal paths the
        other rejected.

        Both error classes collect across the whole batch — grammar
        failures as their own messages, membership offenders through the
        shared rejection helper — so one raise names every problem instead
        of surfacing them one retry at a time. Nothing is recorded here:
        the caller commits only after this returns, which is what makes a
        multi-path call all-or-nothing. A mid-batch failure that had
        already recorded the leading paths would leave the builder in a
        state the caller never asked for — a retry would double-record
        them, and a caller who gives up is left with a half-statement no
        one made.
        """
        if not files:
            raise ValueError(
                f"{api_name} requires at least one file path — a call "
                f"naming nothing is a no-op, not a {noun}."
            )
        known = self._known_deferred_files()
        normalized: List[str] = []
        unknown: List[str] = []
        grammar_errors: List[str] = []
        for file in files:
            try:
                path = self._normalize_deferred_path(file, api_name)
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
                    self._reject_unknown_deferred(
                        unknown, known, api_name, noun
                    )
                except ValueError as exc:
                    parts.append(str(exc))
            raise ValueError("; ".join(parts))
        return normalized

    def _validate_deferred_serialization(self, output_dir: str):
        """Return the required authoritative coverage for publication.

        Every save uses this path; the explicit output directory makes the
        check independent of the optional environment envelope. The
        contradiction guard runs first because it is self-contained and
        produces the more actionable error.

        The seam differs from its advisory sibling on purpose: advisory
        entitlement revalidates at to_dict(output_dir=...) (serialization),
        this at save() (publication), so a caller serializing manually via
        to_dict/to_json knowingly opts out of deferred validation.
        """
        # Both agent-authored lists may be individually valid — or
        # unvalidatable — and still contradict each other. Serializing a path
        # into both arrays publishes two opposite statements about one file
        # and inflates the accounting (three statements about two files),
        # leaving every consumer to guess — conservatively "declared",
        # overriding the explicit claim. The reviewer is the only one who
        # knows which it meant.
        #
        # This runs before reading the sidecar because it compares the
        # reviewer's two lists against each other, not against the sidecar.
        #
        # Only the reviewer's own statements reach here: save() strips the
        # previous auto-fill before calling this, so the sanctioned
        # claim-after-warning re-save is not a contradiction.
        contradicted = sorted(
            set(self.unreviewed) & set(self.deferred_reviewed)
        )
        if contradicted:
            raise ValueError(
                f"{len(contradicted)} path(s) are both declared unreviewed "
                f"and claimed reviewed: "
                f"{', '.join(repr(p) for p in contradicted)}. "
                "A file is one or the other — make only one of the two calls "
                "for this path in your builder script and run it again."
            )
        sidecar_path = os.path.join(
            output_dir, f"{self.reviewer}-deferred-files.json"
        )
        try:
            with open(sidecar_path, "r", encoding="utf-8") as handle:
                sidecar = json.load(handle)
        except FileNotFoundError as exc:
            raise ValueError(
                "missing authoritative deferred coverage sidecar: "
                f"{sidecar_path}"
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "malformed authoritative deferred coverage sidecar: "
                f"{sidecar_path}"
            ) from exc
        try:
            baseline = derive_deferred_coverage(sidecar, [])
        except CoverageError as exc:
            raise ValueError(
                "malformed authoritative deferred coverage sidecar: "
                f"{exc}"
            ) from exc
        known = frozenset(
            (*baseline.deferred_reviewed, *baseline.unreviewed)
        )
        unknown = [path for path in self.unreviewed if path not in known]
        if unknown:
            self._reject_unknown_deferred(
                unknown, known, "add_unreviewed", "declaration"
            )
        # Claims are checked separately from declarations, under their own
        # api_name: both offenses mean "not a deferred file of this review",
        # but a wrongly declared gap and a wrongly claimed read need
        # different fixes, so the raises must stay attributable. The price
        # is that a review carrying both kinds of offense costs two round
        # trips instead of one — accepted deliberately, because a merged
        # message would have to drop the attribution that makes each
        # offender actionable.
        unknown_claims = [
            path for path in self.deferred_reviewed if path not in known
        ]
        if unknown_claims:
            self._reject_unknown_deferred(
                unknown_claims, known, "add_deferred_reviewed", "claim"
            )
        return derive_deferred_coverage(sidecar, self.deferred_reviewed)

    @staticmethod
    def _load_advisory_entitlement(
        output_dir: Optional[str], reviewer: Optional[str]
    ) -> Optional[bool]:
        """Load a bootstrap-declared advisory entitlement when authoritative.

        ``None`` is deliberate fail-open behavior: absent paths, absent files,
        write failures upstream, malformed JSON, wrong top-level shapes, and
        non-boolean declarations leave only the already-enforced channel
        vocabulary validation. Only an explicit boolean false denies advisory
        findings.
        """
        if not output_dir or not reviewer:
            return None
        sidecar = os.path.join(
            output_dir, f"{reviewer}-advisory-entitlement.json"
        )
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        entitled = data.get("advisory_entitled") if isinstance(data, dict) else None
        return entitled if isinstance(entitled, bool) else None

    def _known_advisory_entitlement(self) -> Optional[bool]:
        """Return the cached entitlement from the canonical env envelope.

        This add-time lookup intentionally fails open to vocabulary-only
        validation when the envelope or a valid boolean sidecar is unavailable.
        Canonical serialization can independently revalidate against an
        explicit output directory.
        """
        if self._advisory_entitlement_loaded:
            return self._advisory_entitlement
        self._advisory_entitlement_loaded = True
        self._advisory_entitlement = self._load_advisory_entitlement(
            os.environ.get("PIRATEGOAT_OUTPUT_DIR"),
            os.environ.get("PIRATEGOAT_REVIEWER_NAME"),
        )
        return self._advisory_entitlement

    def _validate_advisory_serialization(
        self, output_dir: Optional[str]
    ) -> None:
        """Reject explicitly unentitled advisory issues at finalization.

        Missing or malformed sidecars remain deliberately fail-open after the
        channel vocabulary has been validated. An explicit false is the only
        authoritative denial.
        """
        if not any(issue.get("channel") == "advisory" for issue in self.issues):
            return
        if self._load_advisory_entitlement(output_dir, self.reviewer) is False:
            raise ValueError(
                "Cannot serialize advisory finding: this reviewer is not "
                "entitled to the advisory channel."
            )

    def add_unreviewed(self, *files: str):
        """Declare in-scope files left unreviewed after budget exhaustion.

        Use ONLY for NOT DIFFED files genuinely out of reach when the tool
        budget ran out. Call as you give up on each file, or once with
        several paths — the signature mirrors add_deferred_reviewed()
        because the two APIs are opposite statements about one namespace,
        and reviewers that assumed the symmetry before it existed lost
        calls to `takes 2 positional arguments but 126 were given`.
        Declared files render under the '**Not reviewed (budget):**' line
        in the Markdown summary and appear as 'unreviewed' in the JSON
        output, so downstream coverage accounting sees the gap. They never
        count toward the verdict.

        Explicit declaration is not the only way into that list: save()
        auto-declares any deferred file left neither declared here nor
        claimed via add_deferred_reviewed(), marking it in
        meta.unreviewed_autofilled and re-deriving both on every save.
        Declaring deliberately is still what distinguishes a known gap
        from an unnoticed one — and declaring a path the previous save
        auto-declared promotes it out of that marker, recording the gap as
        the reviewer's own statement.

        A path declared here must not also be claimed via
        add_deferred_reviewed(): save() rejects the contradiction rather
        than publishing both statements about one file.

        Validation is the batch validator both APIs share: the whole call
        either lands or leaves no trace.
        """
        normalized = self._validate_deferred_batch(
            files, "add_unreviewed", "declaration"
        )
        for path in normalized:
            if path in self.unreviewed_autofilled:
                # An explicit declaration outranks system backfill: promote
                # the path out of derived state so the next save records it
                # as the reviewer's own statement. Without this the call is
                # a silent no-op — the path is already in self.unreviewed —
                # and the marker would keep attributing to the system a gap
                # the agent has just taken ownership of.
                self.unreviewed_autofilled.remove(path)
            if path not in self.unreviewed:
                self.unreviewed.append(path)

    def add_deferred_reviewed(self, *files: str):
        """Claim NOT DIFFED (deferred) files as actually reviewed.

        A claim is a statement, not proof of read — downstream coverage
        accounting labels it as such. Call as you finish each deferred file
        (or once with several paths). Claiming is what makes a deferred
        file the reviewer read distinguishable from one it never opened:
        a deferred file neither claimed here nor declared via
        add_unreviewed() is auto-declared unreviewed at save() and listed
        in meta.unreviewed_autofilled. Silence records a gap; it never
        counts as review. Auto-fill is recomputed on every save, so
        claiming a file you did read and saving again clears both the
        auto-declaration and its warning.

        Claims share add_unreviewed()'s path grammar and are validated
        against the authoritative deferred set with the same membership
        rule — at add time when the env envelope is present, and always at
        save().

        Validation is the batch validator both APIs share, mirroring the
        no-half-applied-batch doctrine critic_adjustments.py enforces for
        its own multi-item writes: the whole call either lands or leaves no
        trace.
        """
        normalized = self._validate_deferred_batch(
            files, "add_deferred_reviewed", "claim"
        )
        for path in normalized:
            if path not in self.deferred_reviewed:
                self.deferred_reviewed.append(path)

    def set_files_reviewed(self, count: int):
        """Report how many files this review actually read.

        The only way meta.files_reviewed becomes a number. Left uncalled it
        serializes as null — "the producer said nothing" — so a recorded 0
        is always the reviewer's own statement that it read nothing, never
        a default wearing a measurement's clothes.
        """
        if isinstance(count, bool) or not isinstance(count, int):
            raise ValueError(
                f"set_files_reviewed requires an integer count, got {count!r}."
            )
        if count < 0:
            raise ValueError(
                f"set_files_reviewed requires a non-negative count, got {count}."
            )
        self.files_reviewed = count

    def set_confidence(self, score: float):
        """Set overall confidence score."""
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {score}")
        self.overall_confidence = score

    def add_tool_result(self, tool_name: str):
        """Record tool result used."""
        if tool_name not in self.tool_results_used:
            self.tool_results_used.append(tool_name)

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
        if self.issues:
            raise ValueError(
                f"Cannot mark review as not_applicable — {len(self.issues)} issue(s) "
                "already recorded. An agent that found issues reviewed the code; "
                "it should not also claim the changes are irrelevant."
            )
        self._not_applicable = True
        self._skip_reason = reason.strip()

    def _calculate_verdict(self) -> str:
        """Auto-calculate verdict from issues."""
        if self._not_applicable:
            return 'not_applicable'
        return derive_review_state(self.issues)['verdict']

    def to_dict(self, *, output_dir: Optional[str] = None) -> Dict:
        """Build as dictionary, revalidating advisory issues when directed.

        Without an explicit directory, manual and legacy callers retain the
        deliberate fail-open, vocabulary-only advisory behavior.

        Deferred-coverage fields reflect the LAST save()'s derivation:
        unreviewed carries any auto-declared paths and
        meta.unreviewed_autofilled names them. Called before any save, both
        contain only what the reviewer itself stated.
        """
        if output_dir is not None:
            self._validate_advisory_serialization(output_dir)
        review_duration = self._review_duration_ms(output_dir)

        derived = derive_review_state(self.issues)
        verdict = 'not_applicable' if self._not_applicable else derived['verdict']
        summary = {
            'total_issues': len(self.issues),
            'by_severity': derived['counts'],
        }
        if self._not_applicable:
            # The abstention short-circuits before channel tags are consulted,
            # so no finding was excluded from its verdict calculation.
            summary['advisory_suppressed'] = 0
        else:
            summary.update(derived['advisory'])

        result = {
            'pr_id': self.pr_id,
            'reviewer': self.reviewer,
            'timestamp': self.timestamp,
            'plugin_version': self._resolve_plugin_version(output_dir),
            'schema': REVIEW_OUTPUT_SCHEMA,
            'verdict': verdict,
            'summary': summary,
            'issues': self.issues,
            'unreviewed': self.unreviewed if self.unreviewed else None,
            # Never nulled when empty, unlike its siblings above: key
            # presence is the downstream consumer's signal that this output
            # carries explicit deferred-review claims, so an empty list must
            # stay readable as "claimed nothing" rather than "old producer".
            'deferred_reviewed': self.deferred_reviewed,
            'observations': self.observations if self.observations else None,
            'recommendations': self.recommendations if any(self.recommendations.values()) else None,
            'positive_observations': self.positive_observations if self.positive_observations else None,
            'clearances': self.clearances if self.clearances else None,
            # Always present, null when unset — same contract as
            # `unreviewed` above: a consumer reads absence off the value,
            # never off the key.
            'narrative_summary': self.narrative_summary,
            'meta': {
                # Both of these are null until something honest fills them:
                # files_reviewed until the reviewer states a count,
                # review_duration_ms until a dispatch marker is found. The
                # builder never contributes a zero of its own — a default
                # that serializes as a measurement is indistinguishable
                # from a real one downstream.
                'files_reviewed': self.files_reviewed,
                'unreviewed_autofilled': (
                    self.unreviewed_autofilled
                    if self.unreviewed_autofilled else None
                ),
                'review_duration_ms': review_duration,
                'confidence_score': self.overall_confidence,
                'tool_results_used': self.tool_results_used if self.tool_results_used else None
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

    def to_json(
        self, indent: int = 2, *, output_dir: Optional[str] = None
    ) -> str:
        """Generate JSON, optionally revalidating advisory entitlement."""
        return json.dumps(
            self.to_dict(output_dir=output_dir),
            indent=indent,
            ensure_ascii=False,
        )

    def to_markdown(self) -> str:
        """Generate human-readable markdown."""
        return render_markdown(self.to_dict())

    def save(self, output_dir: str):
        """Publish a replaceable review candidate for explicit finalization.

        The candidate remains mutable so a reviewer can act on continuation
        feedback and save a stronger snapshot. Only ``finalize_candidate``
        promotes validated bytes to the immutable canonical JSON consumed by
        readiness and reconciliation.
        """
        os.makedirs(output_dir, exist_ok=True)

        # Auto-fill is DERIVED state, recomputed from scratch on every save,
        # and the strip runs FIRST — before validation, before the
        # contradiction check, before the new derivation. That ordering is
        # load-bearing: the reviewer's answer to the warning is to claim a
        # file it did read, and the previous fill still lists that file as
        # unreviewed. Stripping first means validation only ever sees what
        # the reviewer itself stated, so the sanctioned remediation is not
        # mistaken for a declare-plus-claim contradiction, while a genuine
        # contradiction between two agent statements is still rejected.
        # Only paths this builder auto-filled are dropped, so agent-authored
        # declarations survive (add_unreviewed() promotes a path out of the
        # marker precisely so it survives here). The strip is unconditional
        # while the re-derivation below is not, so a save whose sidecar has
        # become unreadable publishes no derived gaps at all — derived state
        # states nothing once the authority that justified it is gone.
        if self.unreviewed_autofilled:
            previous_autofill = set(self.unreviewed_autofilled)
            self.unreviewed = [
                p for p in self.unreviewed if p not in previous_autofill
            ]
            self.unreviewed_autofilled = []

        coverage = self._validate_deferred_serialization(output_dir)
        # Close the silent third state: every deferred file must end up
        # claimed, declared, or auto-declared. Auto-fill is marked so
        # metrics can separate agent honesty from system honesty.
        declared = set(self.unreviewed)
        self.deferred_reviewed = list(coverage.deferred_reviewed)
        self.unreviewed = list(coverage.unreviewed)
        self.unreviewed_autofilled = [
            path for path in coverage.unreviewed if path not in declared
        ]
        self.files_reviewed = coverage.files_reviewed

        paths = reviewer_paths(output_dir, self.reviewer)
        serialized = self.to_json(output_dir=output_dir)
        output = json.loads(serialized)
        serialized_bytes = serialized.encode("utf-8")
        candidate_digest = hashlib.sha256(serialized_bytes).hexdigest()

        # The staging name carries a nonce because overlapping executions of
        # one reviewer are supported. A shared staging name lets one save's
        # replace consume another save's bytes.
        nonce = uuid.uuid4().hex
        staged_json_path = f"{paths.candidate}.{nonce}.tmp"
        try:
            with open(staged_json_path, "wb") as f:
                f.write(serialized_bytes)

            # Echo the RECORDED state so the calling agent reconciles its
            # self-reported COUNTS against what was actually saved, not its
            # intent — a mismatch here means a finding was dropped or
            # mangled before serialization.
            by_sev = output['summary']['by_severity']
            counts_str = ", ".join(f"{sev}: {by_sev[sev]}" for sev in _VALID_SEVERITIES)
            print(f"RECORDED COUNTS: {counts_str}")
            print(
                f"RECORDED ISSUES: {output['summary']['total_issues']} | "
                f"OBSERVATIONS: {len(self.observations)} | "
                f"VERDICT: {output['verdict']}"
            )
            # Deferred-coverage accounting, echoed for the same reason as
            # the counts above: the agent still has a turn left to correct
            # it. Auto-fill happened silently in the file; here it is
            # visible, so an agent that DID read the file can claim it and
            # save again rather than shipping a gap it never intended.
            declared = [
                p for p in self.unreviewed
                if p not in self.unreviewed_autofilled
            ]
            unreviewed_line = f"UNREVIEWED: {len(declared)} declared"
            if self.unreviewed_autofilled:
                unreviewed_line += (
                    f" (+{len(self.unreviewed_autofilled)} auto-filled)"
                )
            if coverage is not None:
                unreviewed_line += (
                    f" / {len(coverage.deferred_reviewed) + len(coverage.unreviewed)} deferred | "
                    f"CLAIMED REVIEWED: {len(self.deferred_reviewed)}"
                )
            print(unreviewed_line)
            if self.unreviewed_autofilled:
                print(
                    "WARNING: deferred files neither claimed nor declared "
                    "were auto-declared unreviewed. If you actually read "
                    "them, claim them with add_deferred_reviewed(...) and "
                    "save again."
                )
            # Budget salience, and ONLY here. The briefing states the target
            # once, thousands of tokens before the reviewer decides whether
            # to stop; a 19-agent field run showed that placement changes
            # nothing (0/19 reached target, median 44% spent, nine declaring
            # 100+ files while under half budget). This echo is the one piece
            # of feedback every agent reads, it arrives with a turn still
            # left to act, and it only appears when there is something to act
            # on — unreviewed files recorded. Silent when the envelope is
            # absent or malformed: the builder must stay usable outside a
            # pipeline run, where there is no target to report.
            budget_target = self._review_budget_target(output_dir, self.reviewer)
            if self.unreviewed and budget_target is not None:
                print(
                    f"TARGET: ~{budget_target} tool calls — if you finished "
                    "well under it with NOT DIFFED files left, read more and "
                    "re-save before finalizing."
                )
                # The target alone moved re-saves but not utilization in
                # run12 (0/19 agents reached target) — a number with no
                # concrete next action is exhortation. PROGRESS states how
                # much of the total in-scope workload (inline + deferred) is
                # actually covered; NEXT UNREAD names the specific files
                # still to read, largest first (the sidecar's own order —
                # see bootstrap.py's order_by_diffstat_largest_first), so
                # the very next tool call has an obvious target. Both read
                # the same schema-2 sidecar as budget_target. PROGRESS is
                # additionally omitted when its count snapshot is
                # incoherent; TARGET and NEXT UNREAD keep following their
                # own independently valid fields.
                meta = self._read_deferred_sidecar(output_dir, self.reviewer)
                print(
                    f"PROGRESS: covered {coverage.files_reviewed} of {coverage.in_scope_count} "
                    "in-scope files."
                )
                deferred_files_ordered = meta.get("deferred_files")
                if isinstance(deferred_files_ordered, list):
                    # Only a CLAIM (add_deferred_reviewed) removes a file
                    # from this list — a DECLARATION (add_unreviewed) is
                    # the reviewer stating it did NOT read the file, not
                    # that it did. run12's bulk-declaring cohort is exactly
                    # who this list has to keep naming: excluding
                    # `declared` here would silence the nudge for the very
                    # runs that most need a concrete next file. Auto-filled
                    # entries (declared by neither claim nor statement)
                    # stay in the list for the same reason.
                    accounted = set(self.deferred_reviewed)
                    remaining = [
                        p for p in deferred_files_ordered
                        if isinstance(p, str) and p not in accounted
                    ]
                    if remaining:
                        head, rest = remaining[:10], remaining[10:]
                        print("NEXT UNREAD (largest first):")
                        for p in head:
                            print(f"  - {p}")
                        if rest:
                            print(f"  (+{len(rest)} more)")
            with output_dir_lock(output_dir):
                require_review_intake_open(output_dir)
                require_not_finalized(paths)
                os.replace(staged_json_path, paths.candidate)
                try:
                    _log_agent_save_telemetry(
                        output_dir, coverage.agent_name, candidate_digest
                    )
                except Exception as exc:
                    print(
                        "WARNING: candidate published, but agent_save "
                        f"telemetry failed: {exc}",
                        file=sys.stderr,
                    )
        finally:
            # A unique staging name never self-overwrites, so a failed save
            # must remove its orphan (replace already consumed it on
            # success).
            try:
                os.unlink(staged_json_path)
            except FileNotFoundError:
                pass

        print(f"CANDIDATE DIGEST: {candidate_digest}")
        builder_script = os.path.abspath(__file__)
        print(
            f"FINALIZE: python3 {shlex.quote(builder_script)} finalize "
            f"--output-dir {shlex.quote(output_dir)} "
            f"--reviewer {shlex.quote(self.reviewer)} "
            f"--candidate-digest {candidate_digest}"
        )
        return {
            "candidate": paths.candidate,
            "candidate_digest": candidate_digest,
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


_REQUIRED_REVIEW_FIELDS = frozenset({
    "pr_id",
    "reviewer",
    "timestamp",
    "plugin_version",
    "schema",
    "verdict",
    "summary",
    "issues",
    "unreviewed",
    "deferred_reviewed",
    "narrative_summary",
    "meta",
})
_REQUIRED_ISSUE_FIELDS = frozenset({
    "id",
    "category",
    "severity",
    "title",
    "description",
    "file",
    "recommendation",
    "confidence",
})
_REQUIRED_META_FIELDS = frozenset({
    "files_reviewed",
    "unreviewed_autofilled",
    "review_duration_ms",
    "confidence_score",
})


def _is_confidence(value):
    return type(value) in (int, float) and 0.0 <= value <= 1.0


def _is_string_list(value):
    return isinstance(value, list) and all(
        isinstance(item, str) for item in value
    )


def _validate_issue_shape(issue, index):
    """Validate fields emitted by ``ReviewOutputBuilder.add_issue``."""
    if not isinstance(issue, dict):
        raise ValueError(f"review candidate issue {index} must be an object")
    missing = sorted(_REQUIRED_ISSUE_FIELDS - set(issue))
    if missing:
        raise ValueError(
            f"review candidate issue {index} is missing required fields: "
            + ", ".join(missing)
        )
    for field in (
        "id",
        "category",
        "title",
        "description",
        "file",
        "recommendation",
    ):
        if not isinstance(issue[field], str):
            raise ValueError(
                f"review candidate issue {index}.{field} must be a string"
            )
    if issue["severity"] not in _VALID_SEVERITIES:
        raise ValueError(
            f"review candidate issue {index}.severity is invalid"
        )
    if not _is_confidence(issue["confidence"]):
        raise ValueError(
            f"review candidate issue {index}.confidence must be 0.0-1.0"
        )
    if "line" in issue and (
        issue["line"] is not None
        and (type(issue["line"]) is not int or issue["line"] <= 0)
    ):
        raise ValueError(
            f"review candidate issue {index}.line must be positive or null"
        )
    if "scope" in issue and issue["scope"] != "file":
        raise ValueError(
            f"review candidate issue {index}.scope must be 'file'"
        )
    if "severity_floor" in issue and issue["severity_floor"] not in _VALID_SEVERITIES:
        raise ValueError(
            f"review candidate issue {index}.severity_floor is invalid"
        )
    if "channel" in issue and issue["channel"] not in _VALID_CHANNELS:
        raise ValueError(
            f"review candidate issue {index}.channel is invalid"
        )
    if (
        "behavior_evidence" in issue
        and issue["behavior_evidence"] not in ("cited", "inferred")
    ):
        raise ValueError(
            f"review candidate issue {index}.behavior_evidence is invalid"
        )
    for field in ("code_snippet", "source_cited"):
        if field in issue and not isinstance(issue[field], str):
            raise ValueError(
                f"review candidate issue {index}.{field} must be a string"
            )
    if "references" in issue and not _is_string_list(issue["references"]):
        raise ValueError(
            f"review candidate issue {index}.references must be strings"
        )


def _validate_optional_review_fields(candidate):
    observations = candidate.get("observations")
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
        raise ValueError("review candidate observations are malformed")

    recommendations = candidate.get("recommendations")
    if recommendations is not None and (
        not isinstance(recommendations, dict)
        or set(recommendations) != {"immediate", "important", "suggestions"}
        or any(
            not _is_string_list(recommendations.get(priority))
            for priority in ("immediate", "important", "suggestions")
        )
    ):
        raise ValueError("review candidate recommendations are malformed")

    for field in ("positive_observations",):
        value = candidate.get(field)
        if value is not None and not _is_string_list(value):
            raise ValueError(f"review candidate {field} must be strings or null")

    clearances = candidate.get("clearances")
    if clearances is not None:
        if not isinstance(clearances, list):
            raise ValueError("review candidate clearances must be a list or null")
        for index, clearance in enumerate(clearances):
            if not isinstance(clearance, dict):
                raise ValueError(
                    f"review candidate clearance {index} must be an object"
                )
            for field in ("claim", "method"):
                value = clearance.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"review candidate clearance {index}.{field} "
                        "must be a non-empty string"
                    )
            evidence = clearance.get("evidence")
            if evidence is not None and not isinstance(evidence, str):
                raise ValueError(
                    f"review candidate clearance {index}.evidence "
                    "must be a string or null"
                )


def _validate_candidate_shape(candidate, reviewer):
    """Validate the complete builder-owned review shape before derivation."""
    missing = sorted(_REQUIRED_REVIEW_FIELDS - set(candidate))
    if missing:
        raise ValueError(
            "review candidate is missing required fields: " + ", ".join(missing)
        )
    if type(candidate["schema"]) is not int or candidate["schema"] != REVIEW_OUTPUT_SCHEMA:
        raise ValueError("review candidate schema does not match the live contract")
    if not isinstance(candidate["reviewer"], str) or candidate["reviewer"] != reviewer:
        raise ValueError("review candidate reviewer does not match finalization request")
    if not isinstance(candidate["pr_id"], str):
        raise ValueError("review candidate pr_id must be a string")
    if not isinstance(candidate["timestamp"], str):
        raise ValueError("review candidate timestamp must be an ISO string")
    try:
        datetime.fromisoformat(candidate["timestamp"])
    except ValueError as exc:
        raise ValueError("review candidate timestamp must be an ISO string") from exc
    if candidate["plugin_version"] is not None and not isinstance(
        candidate["plugin_version"], str
    ):
        raise ValueError("review candidate plugin_version must be a string or null")
    if candidate["narrative_summary"] is not None and not isinstance(
        candidate["narrative_summary"], str
    ):
        raise ValueError("review candidate narrative_summary must be a string or null")
    if candidate["unreviewed"] is not None and not _is_string_list(
        candidate["unreviewed"]
    ):
        raise ValueError("review candidate unreviewed must be strings or null")

    issues = candidate["issues"]
    if not isinstance(issues, list):
        raise ValueError("review candidate issues must be a list")
    for index, issue in enumerate(issues):
        _validate_issue_shape(issue, index)

    meta = candidate["meta"]
    if not isinstance(meta, dict):
        raise ValueError("review candidate meta must be an object")
    missing_meta = sorted(_REQUIRED_META_FIELDS - set(meta))
    if missing_meta:
        raise ValueError(
            "review candidate meta is missing required fields: "
            + ", ".join(missing_meta)
        )
    duration = meta["review_duration_ms"]
    if duration is not None and (type(duration) is not int or duration < 0):
        raise ValueError(
            "review candidate meta.review_duration_ms must be non-negative or null"
        )
    if not _is_confidence(meta["confidence_score"]):
        raise ValueError(
            "review candidate meta.confidence_score must be 0.0-1.0"
        )
    tools = meta.get("tool_results_used")
    if tools is not None and not _is_string_list(tools):
        raise ValueError(
            "review candidate meta.tool_results_used must be strings or null"
        )
    _validate_optional_review_fields(candidate)


def _validate_candidate(output_dir, reviewer, paths, candidate_bytes):
    """Validate one exact candidate snapshot and return telemetry facts."""
    try:
        candidate = json.loads(candidate_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("malformed review candidate JSON") from exc
    if not isinstance(candidate, dict):
        raise ValueError("malformed review candidate: expected an object")
    _validate_candidate_shape(candidate, reviewer)

    issues = candidate["issues"]
    summary = candidate["summary"]
    if not isinstance(issues, list) or not isinstance(summary, dict):
        raise ValueError("review candidate issues/summary are malformed")
    try:
        derived = derive_review_state(issues)
    except ValueError as exc:
        raise ValueError(f"review candidate issues are malformed: {exc}") from exc
    expected_verdict = derived["verdict"]
    if candidate.get("verdict") == "not_applicable":
        skip_reason = candidate.get("skip_reason")
        if (
            issues
            or not isinstance(skip_reason, str)
            or not skip_reason.strip()
        ):
            raise ValueError("review candidate not_applicable verdict is malformed")
        expected_verdict = "not_applicable"
    elif "skip_reason" in candidate:
        raise ValueError(
            "review candidate skip_reason requires a not_applicable verdict"
        )
    if candidate.get("verdict") != expected_verdict:
        raise ValueError("review candidate verdict does not match its issues")
    expected_summary = {
        "total_issues": len(issues),
        "by_severity": derived["counts"],
        **derived["advisory"],
    }
    severity_counts = summary.get("by_severity")
    if (
        type(summary.get("total_issues")) is not int
        or not isinstance(severity_counts, dict)
        or set(severity_counts) != set(_VALID_SEVERITIES)
        or any(
            type(severity_counts.get(severity)) is not int
            or severity_counts[severity] < 0
            for severity in _VALID_SEVERITIES
        )
        or type(summary.get("advisory_suppressed")) is not int
        or summary["advisory_suppressed"] < 0
        or (
            "verdict_without_advisory" in summary
            and summary["verdict_without_advisory"] not in VERDICT_RANK
        )
        or summary != expected_summary
    ):
        raise ValueError("review candidate summary does not match its issues")

    sidecar = _read_json_object(paths.sidecar, "coverage sidecar")
    claims = candidate.get("deferred_reviewed")
    if not isinstance(claims, list):
        raise ValueError("review candidate deferred_reviewed must be a list")
    try:
        coverage = derive_deferred_coverage(sidecar, claims)
    except CoverageError as exc:
        raise ValueError(f"review candidate coverage is malformed: {exc}") from exc
    if derive_reviewer_name(coverage.agent_name) != reviewer:
        raise ValueError("coverage sidecar agent_name does not match reviewer")
    expected_unreviewed = list(coverage.unreviewed) or None
    meta = candidate.get("meta")
    autofilled = (
        meta.get("unreviewed_autofilled") if isinstance(meta, dict) else None
    )
    if (
        candidate.get("deferred_reviewed") != list(coverage.deferred_reviewed)
        or candidate.get("unreviewed") != expected_unreviewed
        or not isinstance(meta, dict)
        or type(meta.get("files_reviewed")) is not int
        or meta.get("files_reviewed") != coverage.files_reviewed
        or (
            autofilled is not None
            and (
                not isinstance(autofilled, list)
                or not all(isinstance(path, str) for path in autofilled)
                or len(autofilled) != len(set(autofilled))
                or not set(autofilled) <= set(coverage.unreviewed)
            )
        )
    ):
        raise ValueError("review candidate derived coverage fields do not match sidecar")
    return candidate, coverage.agent_name


def finalize_candidate(output_dir: str, reviewer: str, candidate_digest: str):
    """Validate and atomically promote exactly one observed candidate."""
    if (
        not isinstance(candidate_digest, str)
        or len(candidate_digest) != 64
        or any(ch not in "0123456789abcdef" for ch in candidate_digest)
    ):
        raise ValueError("candidate digest must be a lowercase SHA-256")
    paths = reviewer_paths(output_dir, reviewer)
    already_finalized = False
    with output_dir_lock(output_dir):
        require_review_intake_open(output_dir)
        if os.path.exists(paths.canonical):
            with open(paths.canonical, "rb") as canonical_handle:
                canonical_bytes = canonical_handle.read()
            canonical_digest = hashlib.sha256(canonical_bytes).hexdigest()
            if canonical_digest != candidate_digest:
                raise ValueError(
                    "candidate digest conflicts with the finalized review"
                )
            candidate, agent_name = _validate_candidate(
                output_dir, reviewer, paths, canonical_bytes
            )
            already_finalized = True
            try:
                os.unlink(paths.candidate)
            except FileNotFoundError:
                pass
        else:
            try:
                with open(paths.candidate, "rb") as candidate_handle:
                    candidate_bytes = candidate_handle.read()
            except OSError as exc:
                raise ValueError("review candidate is absent") from exc
            actual_digest = hashlib.sha256(candidate_bytes).hexdigest()
            if actual_digest != candidate_digest:
                raise ValueError(
                    "candidate digest no longer matches the published candidate"
                )
            candidate, agent_name = _validate_candidate(
                output_dir, reviewer, paths, candidate_bytes
            )
            os.replace(paths.candidate, paths.canonical)

        if not _completion_was_logged(
            output_dir, agent_name, candidate_digest
        ):
            _log_agent_complete_telemetry(
                output_dir,
                agent_name,
                candidate["verdict"],
                candidate["summary"]["total_issues"],
                candidate["summary"]["by_severity"],
                candidate_digest,
            )
    return {
        "json": paths.canonical,
        "artifact_digest": candidate_digest,
        "already_finalized": already_finalized,
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
        "finalize", help="Validate and publish one candidate review"
    )
    finalize_cmd.add_argument("--output-dir", required=True)
    finalize_cmd.add_argument("--reviewer", required=True)
    finalize_cmd.add_argument("--candidate-digest", required=True)
    cli_args = parser.parse_args()
    if cli_args.command == "render":
        with open(cli_args.json_path, encoding="utf-8") as cli_handle:
            print(render_markdown(json.load(cli_handle)))
    elif cli_args.command == "materialize":
        for written_path in materialize_markdown(
            cli_args.output_dir, suffix=cli_args.suffix
        ):
            print(written_path)
    else:
        try:
            finalized = finalize_candidate(
                cli_args.output_dir,
                cli_args.reviewer,
                cli_args.candidate_digest,
            )
        except (OSError, ValueError) as exc:
            print(f"REJECTED: {exc}", file=sys.stderr)
            raise SystemExit(1)
        if finalized["already_finalized"]:
            print(f"ALREADY FINALIZED: {finalized['json']}")
        else:
            print(f"FINALIZED: {finalized['json']}")

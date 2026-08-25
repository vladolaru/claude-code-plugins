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
    from .coverage import ReviewAccountingError, derive_review_accounting, normalize_review_path
except ImportError:
    _SCRIPTS_DIR = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    from review.agent.coverage import ReviewAccountingError, derive_review_accounting, normalize_review_path

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

    # Unclaimed review work derived by the accounting authority.
    if data.get('unclaimed_review_files'):
        files = ", ".join(f"`{f}`" for f in data['unclaimed_review_files'])
        md.append(f"**Not reviewed (budget):** {files}\n\n")

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
        # Agent-authored: review-claimable files the reviewer claims it read.
        self.reviewed_file_claims = []
        self.tool_results_used = []
        self.overall_confidence = 0.95
        self._not_applicable = False
        self._skip_reason = None
        self._review_claimable_files_loaded = False
        self._review_claimable_files = None
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
    def _read_review_accounting_input(
        output_dir: Optional[str], reviewer: Optional[str]
    ) -> Dict:
        """Parse the bootstrap-written review-accounting input, or ``{}``."""
        if not output_dir or not reviewer:
            return {}
        accounting_input = os.path.join(
            output_dir, f"{reviewer}-review-accounting-input.json"
        )
        try:
            with open(accounting_input, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _load_review_claimable_files(
        output_dir: Optional[str], reviewer: Optional[str]
    ) -> Optional[frozenset]:
        """Load the authoritative claimable set for add-time feedback."""
        data = ReviewOutputBuilder._read_review_accounting_input(output_dir, reviewer)
        try:
            accounting = derive_review_accounting(data, [])
        except ReviewAccountingError:
            return None
        return frozenset(accounting.review_claimable_files)

    @staticmethod
    def _review_budget_target(
        output_dir: Optional[str], reviewer: Optional[str]
    ) -> Optional[int]:
        """The run's tool-call target, or None when there isn't an honest one.

        Read from the same schema-3 input that owns the accounting facts.
        """
        data = ReviewOutputBuilder._read_review_accounting_input(output_dir, reviewer)
        if data.get("schema") != 3:
            return None
        value = data.get("review_budget")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return value

    def _known_review_claimable_files(self) -> Optional[frozenset]:
        """The claimable set via the env envelope — add-time fast feedback.

        Authoritative enforcement happens at save() with the explicit
        output directory; this lookup only makes positive claims fail
        earlier on the recommended path.
        """
        if self._review_claimable_files_loaded:
            return self._review_claimable_files
        self._review_claimable_files_loaded = True
        self._review_claimable_files = self._load_review_claimable_files(
            os.environ.get("PIRATEGOAT_OUTPUT_DIR"),
            os.environ.get("PIRATEGOAT_REVIEWER_NAME"),
        )
        return self._review_claimable_files

    @staticmethod
    def _normalize_reviewed_file_claim(file: str) -> str:
        """Normalize one reviewed-file claim.

        Normalizes "./src/x.php", "src\\x.php", and "src//x.php" to one
        form, and rejects forms no scope path can ever take (absolute,
        traversal, drive-prefixed, dot-only) — an unmatched path is not a
        near miss, it is an accounting statement about a file that does not
        exist in this review.
        """
        if not isinstance(file, str) or not file.strip():
            raise ValueError(
                "claim_files_reviewed requires a non-empty file path."
            )
        try:
            return normalize_review_path(file, "claim_files_reviewed")
        except ReviewAccountingError as exc:
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
        known = self._known_review_claimable_files()
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

    def _derive_review_accounting(self, output_dir: str):
        """Return the required authoritative accounting for publication.

        Every save uses this path; the explicit output directory makes the
        check independent of the optional environment envelope.

        The seam differs from its advisory sibling on purpose: advisory
        entitlement revalidates at to_dict(output_dir=...) (serialization),
        this at save() (publication), so a caller serializing manually via
        to_dict/to_json knowingly opts out of accounting validation.
        """
        accounting_input_path = os.path.join(
            output_dir, f"{self.reviewer}-review-accounting-input.json"
        )
        try:
            with open(accounting_input_path, "r", encoding="utf-8") as handle:
                accounting_input = json.load(handle)
        except FileNotFoundError as exc:
            raise ValueError(
                "missing authoritative review-accounting input: "
                f"{accounting_input_path}"
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "malformed authoritative review-accounting input: "
                f"{accounting_input_path}"
            ) from exc
        try:
            return derive_review_accounting(
                accounting_input, self.reviewed_file_claims
            )
        except ReviewAccountingError as exc:
            raise ValueError(
                "malformed authoritative review-accounting input: "
                f"{exc}"
            ) from exc

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

    def claim_files_reviewed(self, *files: str):
        """Claim review-claimable files as reviewed, atomically."""
        normalized = self._validate_reviewed_file_claims(files)
        for path in normalized:
            if path not in self.reviewed_file_claims:
                self.reviewed_file_claims.append(path)

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

    def to_dict(
        self, *, output_dir: Optional[str] = None, review_accounting=None
    ) -> Dict:
        """Build as dictionary, revalidating advisory issues when directed.

        Without an explicit directory, manual and legacy callers retain the
        deliberate fail-open, vocabulary-only advisory behavior.

        Candidate publication passes authoritative ``review_accounting``.
        Direct serialization has no authority for machine-derived fields and
        reports those as absent.
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
            'review_claimable_files': (
                list(review_accounting.review_claimable_files)
                if review_accounting else None
            ),
            'reviewed_file_claims': (
                list(review_accounting.reviewed_file_claims)
                if review_accounting else list(self.reviewed_file_claims)
            ),
            'unclaimed_review_files': (
                list(review_accounting.unclaimed_review_files)
                if review_accounting else None
            ),
            'inline_diff_file_count': (
                review_accounting.inline_diff_file_count
                if review_accounting else None
            ),
            'review_accounted_file_count': (
                review_accounting.review_accounted_file_count
                if review_accounting else None
            ),
            'in_scope_review_file_count': (
                review_accounting.in_scope_review_file_count
                if review_accounting else None
            ),
            'observations': self.observations if self.observations else None,
            'recommendations': self.recommendations if any(self.recommendations.values()) else None,
            'positive_observations': self.positive_observations if self.positive_observations else None,
            'clearances': self.clearances if self.clearances else None,
            'narrative_summary': self.narrative_summary,
            'meta': {
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
        self,
        indent: int = 2,
        *,
        output_dir: Optional[str] = None,
        review_accounting=None,
    ) -> str:
        """Generate JSON, optionally revalidating advisory entitlement."""
        return json.dumps(
            self.to_dict(
                output_dir=output_dir, review_accounting=review_accounting
            ),
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

        review_accounting = self._derive_review_accounting(output_dir)

        paths = reviewer_paths(output_dir, self.reviewer)
        serialized = self.to_json(
            output_dir=output_dir, review_accounting=review_accounting
        )
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
            print(
                "REVIEW CLAIMS: "
                f"{len(review_accounting.reviewed_file_claims)} reviewed | "
                f"{len(review_accounting.unclaimed_review_files)} unclaimed"
            )
            # Budget salience, and ONLY here. The briefing states the target
            # once, thousands of tokens before the reviewer decides whether
            # to stop; a 19-agent field run showed that placement changes
            # nothing (0/19 reached target, median 44% spent, nine declaring
            # 100+ files while under half budget). This echo is the one piece
            # of feedback every agent reads, it arrives with a turn still
            # left to act, and it only appears when there is something to act
            # on — unclaimed review files recorded. Silent when the envelope is
            # absent or malformed: the builder must stay usable outside a
            # pipeline run, where there is no target to report.
            budget_target = self._review_budget_target(output_dir, self.reviewer)
            if review_accounting.unclaimed_review_files and budget_target is not None:
                print(
                    f"TARGET: ~{budget_target} tool calls — if you finished "
                    "well under it with NOT DIFFED files left, read more and "
                    "re-save before finalizing."
                )
                # The target alone moved re-saves but not utilization in
                # run12 (0/19 agents reached target) — a number with no
                # concrete next action is exhortation. PROGRESS states how
                # much of the total in-scope workload (inline + claimable) is
                # actually covered; NEXT UNREAD names the specific files
                # still to read, largest first (the sidecar's own order —
                # see bootstrap.py's order_by_diffstat_largest_first), so
                # the very next tool call has an obvious target. Both read
                # the same schema-2 sidecar as budget_target. PROGRESS is
                # additionally omitted when its count snapshot is
                # incoherent; TARGET and NEXT UNREAD keep following their
                # own independently valid fields.
                print(
                    "PROGRESS: accounted for "
                    f"{review_accounting.review_accounted_file_count} of "
                    f"{review_accounting.in_scope_review_file_count} "
                    "in-scope files."
                )
                remaining = list(review_accounting.unclaimed_review_files)
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
                        output_dir, review_accounting.agent_name, candidate_digest
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
    "review_claimable_files",
    "reviewed_file_claims",
    "unclaimed_review_files",
    "inline_diff_file_count",
    "review_accounted_file_count",
    "in_scope_review_file_count",
    "observations",
    "recommendations",
    "positive_observations",
    "clearances",
    "narrative_summary",
    "meta",
})
_OPTIONAL_REVIEW_FIELDS = frozenset({"skip_reason"})
_ALLOWED_REVIEW_FIELDS = _REQUIRED_REVIEW_FIELDS | _OPTIONAL_REVIEW_FIELDS
_REQUIRED_ISSUE_FIELDS = frozenset({
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
    "tool_results_used",
})
_OPTIONAL_META_FIELDS = frozenset()
_ALLOWED_META_FIELDS = _REQUIRED_META_FIELDS | _OPTIONAL_META_FIELDS


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
    unexpected = sorted(set(candidate) - _ALLOWED_REVIEW_FIELDS)
    if unexpected:
        raise ValueError(
            "review candidate has unexpected fields: " + ", ".join(unexpected)
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
    for field in (
        "review_claimable_files",
        "reviewed_file_claims",
        "unclaimed_review_files",
    ):
        if not _is_string_list(candidate[field]):
            raise ValueError(f"review candidate {field} must be a list of strings")
    for field in (
        "inline_diff_file_count",
        "review_accounted_file_count",
        "in_scope_review_file_count",
    ):
        if type(candidate[field]) is not int or candidate[field] < 0:
            raise ValueError(
                f"review candidate {field} must be a non-negative integer"
            )

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
    unexpected_meta = sorted(set(meta) - _ALLOWED_META_FIELDS)
    if unexpected_meta:
        raise ValueError(
            "review candidate meta has unexpected fields: "
            + ", ".join(unexpected_meta)
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

    accounting_input = _read_json_object(
        paths.accounting_input, "review-accounting input"
    )
    claims = candidate.get("reviewed_file_claims")
    if not isinstance(claims, list):
        raise ValueError("review candidate reviewed_file_claims must be a list")
    try:
        review_accounting = derive_review_accounting(accounting_input, claims)
    except ReviewAccountingError as exc:
        raise ValueError(
            f"review candidate accounting is malformed: {exc}"
        ) from exc
    if (
        candidate.get("review_claimable_files")
        != list(review_accounting.review_claimable_files)
        or candidate.get("reviewed_file_claims")
        != list(review_accounting.reviewed_file_claims)
        or candidate.get("unclaimed_review_files")
        != list(review_accounting.unclaimed_review_files)
        or candidate.get("inline_diff_file_count")
        != review_accounting.inline_diff_file_count
        or candidate.get("review_accounted_file_count")
        != review_accounting.review_accounted_file_count
        or candidate.get("in_scope_review_file_count")
        != review_accounting.in_scope_review_file_count
    ):
        raise ValueError(
            "review candidate derived accounting fields do not match input"
        )
    return candidate, review_accounting.agent_name


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


def repair_finalized_completion(output_dir: str, reviewer: str):
    """Repair telemetry for one canonical review during intake close.

    This is not an alternate finalization channel: it never promotes a
    candidate and does nothing after intake close unless canonical JSON
    already exists. The caller holds the shared output-directory lock.
    """
    telemetry = _telemetry_for_output(output_dir)
    if telemetry.log_path is None:
        return None

    paths = reviewer_paths(output_dir, reviewer)
    try:
        with open(paths.canonical, "rb") as canonical_handle:
            canonical_bytes = canonical_handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("finalized review is unreadable") from exc

    artifact_digest = hashlib.sha256(canonical_bytes).hexdigest()
    candidate, agent_name = _validate_candidate(
        output_dir, reviewer, paths, canonical_bytes
    )
    if not _completion_was_logged(output_dir, agent_name, artifact_digest):
        _log_agent_complete_telemetry(
            output_dir,
            agent_name,
            candidate["verdict"],
            candidate["summary"]["total_issues"],
            candidate["summary"]["by_severity"],
            artifact_digest,
        )
    return {
        "json": paths.canonical,
        "artifact_digest": artifact_digest,
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
            print(f"RECORDED FINAL (ALREADY FINALIZED): {finalized['json']}")
        else:
            print(f"RECORDED FINAL: {finalized['json']}")

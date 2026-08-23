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
    builder.save(output_dir)  # persists the canonical JSON artifact

    Markdown is derived from the canonical JSON: render one dict with
    render_markdown(data), or from the shell via the CLI —
    `python3 output.py render <path>-review.json` prints one review's
    Markdown, `python3 output.py materialize <output_dir>` writes
    <reviewer>-review.md beside every *-review.json.
"""

import contextlib
import json
import os
import posixpath
import sys
import uuid

try:
    import fcntl
except ImportError:  # non-POSIX host — publish without the completion-publication lock
    fcntl = None
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

try:
    from ..verdict_rules import GATING_SEVERITIES, verdict_for_counts
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
    from review.verdict_rules import GATING_SEVERITIES, verdict_for_counts


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

_VALID_SEVERITIES = ('critical', 'high', 'medium', 'low', 'info')
_VALID_CHANNELS = ('blocking', 'advisory')
_SEVERITY_RANK = {
    'info': 0,
    'low': 1,
    'medium': 2,
    'high': 3,
    'critical': 4,
}
_VERDICT_RANK = {
    'approve': 0,
    'comment': 1,
    'request_changes': 2,
    'block': 3,
}


def _verdict_for_issues(issues) -> str:
    """Calculate a gating verdict for the supplied findings.

    Counting lives here (this is the side that holds issue dicts); the
    thresholds live in `review/verdict_rules.py`, shared with
    `critic_adjustments.py`, which recomputes the ledger verdict after an
    applying critic batch. Two ladders would let a demoted finding publish
    the pre-demotion verdict — and step 11 now derives the pipeline's
    published verdict from that ledger.
    """
    counts = {severity: 0 for severity in GATING_SEVERITIES}

    for issue in issues:
        sev = issue['severity']
        if sev in counts:
            counts[sev] += 1

    return verdict_for_counts(counts)


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


def _log_agent_complete_telemetry(output_dir, reviewer, verdict, issue_count,
                                  severities, resave):
    """Best-effort telemetry logging on agent completion. Never raises.

    `resave` is this save's own observation of whether a review JSON for
    this reviewer was already published when it reached publication — see
    save() for why it is taken under the publication lock, and for why
    that is narrower than "this is a correction". It is a required
    argument, not a defaulted one: the only caller is the save path, which
    always knows the answer, and a default would let a future caller
    publish an unobserved `false`.
    """
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "review_telemetry",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telemetry.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        t = mod.ReviewTelemetry(output_dir)
        t.log_agent_complete(
            agent_name=reviewer,
            verdict=verdict,
            issue_count=issue_count,
            severities=severities,
            resave=resave,
        )
    except Exception:
        pass


def render_markdown(data: Dict) -> str:
    """Human-readable Markdown rendered from a review's canonical dict.

    A pure function of the JSON representation — the same dict
    to_dict()/to_json() produce and the *-review.json file holds — so a
    rendering can never disagree with the artifact it came from.

    Keys present in schema 1 are required (missing means KeyError — the
    caller's problem); later schema additions are read with .get() and
    render only when present.
    """
    md = []

    md.append(f"# {data['reviewer'].title()} Review - PR #{data['pr_id']}\n\n")

    # Degraded host context, directly under the title: reviewers' claims
    # were scoped by this banner's presence, so a reader must meet it
    # before any finding. It follows the H1 rather than preceding it
    # because every rendering this function produces is graded on starting
    # with "# " (tests/helpers/graders.py) — one rule for one renderer, and
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

    # What the orchestrator did with each critic decision, from the ledger's
    # own record rather than from prose in a report. A batch nobody probed
    # renders as N lines of `not_checked` — the honest default — instead of
    # being indistinguishable from one checked entry by entry.
    applied_adjustments = data.get('applied_critic_adjustments')
    if isinstance(applied_adjustments, list) and applied_adjustments:
        md.append("## Critic Adjustments Applied\n\n")
        for record in applied_adjustments:
            if isinstance(record, dict):
                adjustment_id = record.get('adjustment_id', '')
                spot_check = record.get('spot_check', 'not_checked')
            else:
                # The bare-id shape this key held before it carried an
                # outcome; nothing recorded a check, so nothing claims one.
                adjustment_id, spot_check = record, 'not_checked'
            md.append(f"- `{adjustment_id}` — {spot_check}\n")
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
        path = posixpath.normpath(file.strip().replace("\\", "/"))
        if (
            path.startswith("/")
            or path == "."
            or path == ".."
            or path.startswith("../")
            or (len(path) >= 2 and path[1] == ":" and path[0].isalpha())
        ):
            raise ValueError(
                f"{api_name} requires a repository-relative path exactly "
                f"as shown in the NOT DIFFED listing, got {file!r}."
            )
        return path

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

    def _validate_deferred_serialization(
        self, output_dir: str
    ) -> Optional[frozenset]:
        """Authoritative deferred-set validation at publication time.

        Runs on EVERY save regardless of how the builder was invoked —
        save() already knows the output directory and reviewer, so the
        check cannot be bypassed by skipping the env envelope. Returns the
        known set (None preserves fail-open for genuinely sidecar-less use).
        Fail-open is membership-only: the contradiction guard below runs
        before it, because it needs no sidecar to be right.

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
        # This runs ABOVE the fail-open return below because it compares the
        # reviewer's two lists against each other, not against the sidecar:
        # self-consistency needs no authority. Fail-open covers MEMBERSHIP
        # ("is this path a deferred file of this review?") — the one question
        # only the sidecar can answer — so a missing sidecar must not turn a
        # contradiction into a published artifact.
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
        known = self._load_deferred_files(output_dir, self.reviewer)
        if known is None:
            return None
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
        return known

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

        # Advisory-channel findings are listed but do not gate the verdict.
        return _verdict_for_issues(
            issue for issue in self.issues
            if issue.get('channel') != 'advisory'
        )

    def _advisory_measurement(self, verdict: str) -> Dict[str, Any]:
        """Measure exact advisory-tag suppression without changing verdicts."""
        if self._not_applicable:
            # The not-applicable verdict short-circuits before channel tags are
            # consulted, so no finding was excluded from its calculation.
            return {'advisory_suppressed': 0}

        suppressed = sum(
            issue.get('channel') == 'advisory' for issue in self.issues
        )
        measurement: Dict[str, Any] = {'advisory_suppressed': suppressed}
        if suppressed == 0:
            return measurement

        verdict_without_advisory = _verdict_for_issues(self.issues)
        if _VERDICT_RANK[verdict_without_advisory] > _VERDICT_RANK[verdict]:
            measurement['verdict_without_advisory'] = verdict_without_advisory
        return measurement

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

        severity_counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0}
        for issue in self.issues:
            severity_counts[issue['severity']] += 1

        verdict = self._calculate_verdict()
        summary = {
            'total_issues': len(self.issues),
            'by_severity': severity_counts,
        }
        summary.update(self._advisory_measurement(verdict))

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
        """Publish the review JSON — the single canonical artifact.

        Markdown is derived from this JSON on demand (render_markdown /
        materialize_markdown; the pipeline materializes it for humans at
        the step-8 readiness gate), so there is no artifact pair to keep
        consistent: an
        interrupted re-save simply leaves the previous complete JSON
        visible, the normal semantics of an atomic single-file write.
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

        known_deferred = self._validate_deferred_serialization(output_dir)
        # Close the silent third state: every deferred file must end up
        # claimed, declared, or auto-declared. Auto-fill is marked so
        # metrics can separate agent honesty from system honesty.
        if known_deferred is not None:
            unaccounted = sorted(
                known_deferred
                - set(self.deferred_reviewed)
                - set(self.unreviewed)
            )
            self.unreviewed_autofilled = unaccounted
            if unaccounted:
                self.unreviewed.extend(unaccounted)

        json_path = os.path.join(output_dir, f"{self.reviewer}-review.json")
        serialized = self.to_json(output_dir=output_dir)
        output = json.loads(serialized)

        # The review JSON is the readiness signal agents_status.py polls,
        # and the pipeline may finalize the telemetry manifest the moment
        # every agent looks finished. Completion must therefore be durable
        # BEFORE the JSON becomes visible — otherwise a finalize racing
        # this save records the agent permanently incomplete.
        # The staging name carries a nonce because the lifecycle supports
        # overlapping executions of the same reviewer (retry before the
        # prior invocation finishes): a shared staging file would let one
        # execution's os.replace() consume the other's staged artifact.
        nonce = uuid.uuid4().hex
        staged_json_path = f"{json_path}.{nonce}.tmp"
        try:
            with open(staged_json_path, 'w') as f:
                f.write(serialized)

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
            if known_deferred is not None:
                unreviewed_line += (
                    f" / {len(known_deferred)} deferred | "
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
                # the same schema-2 sidecar as budget_target and are
                # silently absent on the same schema-1/missing sidecar it
                # already tolerates.
                meta = self._read_deferred_sidecar(output_dir, self.reviewer)
                in_scope = meta.get("in_scope_count")
                if (
                    isinstance(in_scope, int)
                    and not isinstance(in_scope, bool)
                    and in_scope > 0
                ):
                    covered = (
                        (meta.get("diffed_count") or 0)
                        + len(self.deferred_reviewed)
                    )
                    print(
                        f"PROGRESS: covered {covered} of {in_scope} "
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
            # Completion telemetry and publication run under one exclusive
            # lock so {log, publish} is a single atomic unit per execution:
            # the manifest's latest agent_complete always describes the
            # JSON published last, never a slower overlapping save's. The
            # log still precedes the replace — completion must be durable
            # before the readiness signal a racing finalize would trust
            # becomes visible. The lock is the output directory's own fd
            # (no lock file to leave behind; flock auto-releases if the
            # process dies); where flock is unavailable (non-POSIX) the
            # two steps still run back-to-back.
            with contextlib.ExitStack() as stack:
                if fcntl is not None:
                    lock_fd = os.open(output_dir, os.O_RDONLY)
                    stack.callback(os.close, lock_fd)
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                # Was a review JSON for this reviewer already published
                # when this save reached publication? That is the whole
                # claim — not "this is a correction". The echo above
                # invites a correction re-save, so multiple successful
                # saves are sanctioned and each logs its own completion,
                # and the raw event stream therefore holds one event per
                # SAVE, not one per AGENT. This observation is what makes
                # that legible without replaying the projection.
                #
                # It is NOT a correction count and NOT an agent-count
                # discriminator: a second execution's first save reports
                # True (a prior execution published), and a save following
                # a failed os.replace() reports False (nothing is there).
                #
                # Observed HERE, under the same lock that serializes
                # {log, publish}: outside it, an overlapping execution's
                # os.replace() could land between the test and this save's
                # own publication and make the answer describe a race
                # rather than this save.
                resave = os.path.exists(json_path)
                _log_agent_complete_telemetry(
                    output_dir,
                    f"{self.reviewer}-reviewer",
                    output['verdict'],
                    output['summary']['total_issues'],
                    output['summary']['by_severity'],
                    resave,
                )
                os.replace(staged_json_path, json_path)
        finally:
            # A unique staging name never self-overwrites, so a failed save
            # must remove its orphan (replace already consumed it on
            # success).
            try:
                os.unlink(staged_json_path)
            except FileNotFoundError:
                pass

        return {'json': json_path}

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
        with open(cli_args.json_path, encoding="utf-8") as cli_handle:
            print(render_markdown(json.load(cli_handle)))
    else:
        for written_path in materialize_markdown(
            cli_args.output_dir, suffix=cli_args.suffix
        ):
            print(written_path)

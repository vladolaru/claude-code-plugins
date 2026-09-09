#!/usr/bin/env python3
"""The reconciliator's builder for the findings ledger.

The ledger is review content — findings, checks, assessment, observations,
recommendations, positives, id counters — plus reconciliation metrics: the
four concern counts this builder judges and the pipeline facts stitched
onto them. It has no reviewer identity and no reviewed files: those belong
to one reviewer's draft/final lifecycle, which a synthesized cross-review
artifact does not have. This is the one deliberate subclass of
ReviewOutputBuilder; do not grow a hierarchy under it.

`read_reconciliation_context(output_dir)` is the one reader of the run's
the ``reconciliation_context`` artifact. Four callers open the file through it — the
save gate (`findings_save.py`), the notes CLI (`reconciliation_notes.py`),
this builder, and the context builder's own note-preserving read — and the
schema check stays with the callers that own the schema constant.

Two builder methods carry pipeline contracts rather than leaving them to
the agent. `record_check(..., sources=[...])` reads each merged source
check from that context, appends its `method` verbatim as a
`[<stem>:<id>] …` line and unions its `verifies`, so the save gate's
verbatim-method rule is satisfied by construction. `resolve_note(...,
verifies=[...])` lets a confirmed note settle Verify items;
`review_document.normalize_verifies` is that grammar — the one reviewer
checks use — and `critic_adjustments.py` validates a saved note's citation
with it. The pipeline-owned reconciliation facts are never authored here:
`findings_save.py` stamps them from the context at save time.
"""
import json
import os
import re
import sys
from typing import Dict

try:
    from .agent.output import ReviewOutputBuilder, SYNTHESIS_MARKER_PREFIX
    from .run_paths import artifact_path
    from .review_document import (
        MAX_LEDGER_TEXT_LENGTH, coerce_text, normalize_bounded_text, normalize_verifies,
        validate_check_shape,
    )
    from .verdict_rules import VALID_SEVERITIES
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.agent.output import ReviewOutputBuilder, SYNTHESIS_MARKER_PREFIX
    from review.run_paths import artifact_path
    from review.review_document import (
        MAX_LEDGER_TEXT_LENGTH, coerce_text, normalize_bounded_text, normalize_verifies,
        validate_check_shape,
    )
    from review.verdict_rules import VALID_SEVERITIES

LEDGER_SCHEMA = 3
# The four judgments the reconciliator itself makes: every grouped concern is
# either verified, a false positive, or out of scope. The builder owns these.
RECONCILIATION_JUDGMENT_FIELDS = (
    "grouped_concern_count",
    "verified_concern_count",
    "false_positive_concern_count",
    "out_of_scope_concern_count",
)
# What the pipeline measured about the run that fed the reconciliator. The
# builder never authors these — the pipeline stitches them onto the ledger.
RECONCILIATION_PIPELINE_FIELDS = (
    "input_finding_count",
    "contributing_agent_count",
    "reviewing_agents",
    "not_applicable_agents",
    "dispatched_agents",
    "missing_agents",
)
# The rosters inside the pipeline half. Every entry is a dispatch agent name;
# `dispatched_agents` and `missing_agents` are null when dispatch was unknown.
RECONCILIATION_AGENT_LIST_FIELDS = (
    "reviewing_agents",
    "dispatched_agents",
    "missing_agents",
)
# Every reconciliation field that must be a non-negative integer.
RECONCILIATION_COUNT_FIELDS = RECONCILIATION_JUDGMENT_FIELDS + (
    "input_finding_count",
    "contributing_agent_count",
)
RECONCILIATION_FIELDS = frozenset(
    RECONCILIATION_JUDGMENT_FIELDS + RECONCILIATION_PIPELINE_FIELDS
)
# The reconciliator is dispatched as `review-reconciliator` and constructs its
# builder as `reconciliator`; the inherited id allocation is keyed on the
# latter and the dispatch marker on the former.
LEDGER_ACTOR = "reconciliator"
# The dispatch identity this ledger's duration is measured from. It is spelled
# here rather than imported because synthesis_lifecycle imports
# critic_adjustments, which imports this module; importing its RECONCILIATOR
# back would close a cycle.
LEDGER_AGENT_NAME = "review-reconciliator"

# Provenance the reconciliator records beside what it kept and dropped.
# `reviewer` is the reconciliation context's `reviews_by_agent` key (the
# review stem, "security-review") and `id` the source document's own fN or
# cN. findings_save.py holds the ledger to this: every source finding and
# check is merged into exactly one ledger entry or dropped with a reason,
# never silently gone.
SOURCE_ENTRY_FIELDS = frozenset({"reviewer", "id"})
DROP_REASONS_FINDING = ("false_positive", "out_of_scope", "prefiltered")
DROP_REASONS_CHECK = ("void",)
NOTE_OUTCOMES = ("confirmed", "refuted", "not_checked")
NOTE_ID_RE = re.compile(r"n[1-9][0-9]*")
SOURCE_ID_RE = re.compile(r"[fc][1-9][0-9]*")


def _source_entry(entry, label, allow_severity=False):
    allowed = SOURCE_ENTRY_FIELDS | ({"severity"} if allow_severity else set())
    if (
        not isinstance(entry, dict)
        or not SOURCE_ENTRY_FIELDS <= set(entry)
        or not set(entry) <= allowed
        or not isinstance(entry["reviewer"], str)
        or not entry["reviewer"].strip()
        or not isinstance(entry["id"], str)
        or SOURCE_ID_RE.fullmatch(entry["id"]) is None
        or ("severity" in entry and entry["severity"] not in VALID_SEVERITIES)
    ):
        raise ValueError(
            f"{label} sources entries must be {{reviewer, id}} with a "
            "review stem and a canonical fN/cN id"
            + (" (and a valid severity, if any)" if allow_severity else "")
        )
    normalized = {"reviewer": entry["reviewer"].strip(), "id": entry["id"]}
    if "severity" in entry:
        normalized["severity"] = entry["severity"]
    return normalized


def normalized_sources(value, label, *, allow_severity=False):
    """A non-empty, duplicate-free list of {reviewer, id} entries.

    `allow_severity` admits the source `severity` findings_save.py stamps
    on a finding's sources from the reconciliation context; a check's
    sources never carry one.
    """
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} requires a non-empty sources list")
    entries = [_source_entry(entry, label, allow_severity) for entry in value]
    keys = [(e["reviewer"], e["id"]) for e in entries]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{label} sources must not repeat a source")
    return entries


def _no_lifecycle(*_args, **_kwargs):
    raise TypeError("the findings ledger has no reviewer lifecycle")


def read_reconciliation_context(output_dir) -> dict:
    """The run's reconciliation context as a JSON object.

    Raises ValueError naming the file when it is missing, not JSON or not an
    object. The schema check stays with the callers that own the schema
    constant (`reconciliation_context.py` sits above this module).
    """
    path = artifact_path(output_dir, "reconciliation_context")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            context = json.load(handle)
    except (OSError, json.JSONDecodeError) as err:
        raise ValueError(f"{path.name} is unreadable: {err}") from err
    if not isinstance(context, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return context


class FindingsLedgerBuilder(ReviewOutputBuilder):
    """Build the reconciled ledger's content and its four judgment counts."""

    def __init__(self, pr_id: str, output_dir: str):
        super().__init__(pr_id, LEDGER_ACTOR)
        # Bound for the two facts the run's directory answers — the plugin
        # version stamped in caller configuration and the dispatch marker the
        # duration is measured from — and for nothing else: the ledger is
        # written by write_findings, not by the draft lifecycle.
        self._output_dir = str(output_dir)
        self._reconciliation = None
        self._source_checks = None
        self.dropped_findings = []
        self.dropped_checks = []
        self.orchestrator_notes = []

    @classmethod
    def open(cls, *_args, **_kwargs):
        _no_lifecycle()

    save_draft = _no_lifecycle
    claim_files_reviewed = _no_lifecycle
    retract_reviewed_file_claims = _no_lifecycle
    mark_not_applicable = _no_lifecycle

    def _marker_name(self) -> str:
        """The ledger has no assignment, so it names its own marker."""
        return f"{SYNTHESIS_MARKER_PREFIX}{LEDGER_AGENT_NAME}"

    def _validate_check_candidate(self, candidate, index):
        # The reviewer grammar has no `sources`; the ledger's provenance is
        # validated by the save gate instead.
        validate_check_shape(
            {key: value for key, value in candidate.items() if key != "sources"}, index
        )

    def set_reconciliation(
        self, *, grouped_concern_count: int, verified_concern_count: int,
        false_positive_concern_count: int, out_of_scope_concern_count: int,
    ) -> None:
        """Record the four judgment counts, which must partition the concerns."""
        counts = dict(zip(
            RECONCILIATION_JUDGMENT_FIELDS,
            (
                grouped_concern_count,
                verified_concern_count,
                false_positive_concern_count,
                out_of_scope_concern_count,
            ),
        ))
        for name, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if (
            verified_concern_count
            + false_positive_concern_count
            + out_of_scope_concern_count
        ) != grouped_concern_count:
            raise ValueError(
                "verified + false_positive + out_of_scope concern counts must "
                "equal grouped_concern_count"
            )
        self._reconciliation = counts

    def add_finding(self, *args, sources=None, severity_note=None, **kwargs):
        """A reconciled finding names every source finding it merged."""
        normalized = normalized_sources(sources, "add_finding")
        normalized_note = (
            None if severity_note is None
            else normalize_bounded_text(severity_note, "severity_note")
        )
        finding_id = super().add_finding(*args, **kwargs)
        finding = self.findings[-1]
        finding["sources"] = normalized
        if normalized_note is not None:
            finding["severity_note"] = normalized_note
        return finding_id

    def _source_check(self, reviewer, check_id):
        """The source check as the reconciliation context carries it, or
        None when the context is unreadable or does not name it."""
        if self._source_checks is None:
            self._source_checks = {}
            try:
                reviews = read_reconciliation_context(self._output_dir).get("reviews_by_agent") or {}
                for stem, review in reviews.items():
                    for check in (review or {}).get("checks") or []:
                        if isinstance(check, dict) and isinstance(check.get("id"), str):
                            self._source_checks[(stem, check["id"])] = check
            except (ValueError, AttributeError, TypeError):
                self._source_checks = {}
        return self._source_checks.get((reviewer, check_id))

    def record_check(
        self, question, method, result, *,
        source_reviewers=None, sources=None, verifies=None,
    ):
        """A reconciled check names every source check it merged, carries
        each source's `method` text verbatim, and the union of the Verify
        items those sources cited.

        The save gate (`findings_save.py`) rejects a merged check that drops
        a source's method or a cited Verify item. Rather than have the
        reconciliator assemble that by hand — run 4's first save was
        rejected four times over and cost eight tool calls to repair — the
        builder reads the sources from the run's reconciliation context and
        appends whatever the given `method` does not already contain, as
        `[<stem>:<id>] <method>` lines. Write your own method text; the
        sources' text is carried for you.
        """
        normalized = normalized_sources(sources, "record_check")
        method = coerce_text(method).strip() if method is not None else ""
        cited = list(verifies or [])
        for entry in normalized:
            source = self._source_check(entry["reviewer"], entry["id"])
            if source is None:
                continue
            source_method = source.get("method")
            if isinstance(source_method, str) and source_method.strip() and source_method.strip() not in method:
                line = f"[{entry['reviewer']}:{entry['id']}] {source_method.strip()}"
                method = f"{method}\n{line}" if method else line
            for item in source.get("verifies") or []:
                if isinstance(item, str) and item not in cited:
                    cited.append(item)
        check_id = super().record_check(
            question, method, result,
            source_reviewers=source_reviewers, verifies=cited or None,
        )
        self.checks[-1]["sources"] = normalized
        return check_id

    def drop_finding(self, reviewer, id, *, reason, evidence=None):
        """Record one source finding that was read and not carried forward.

        `prefiltered` drops execute the pipeline's own scope verdict and
        need no evidence; a false positive or an out-of-scope call is the
        reconciliator's judgment and must cite what settled it.
        """
        entry = _source_entry({"reviewer": reviewer, "id": id}, "drop_finding")
        if reason not in DROP_REASONS_FINDING:
            raise ValueError(
                f"drop_finding reason must be one of {DROP_REASONS_FINDING}"
            )
        entry["reason"] = reason
        if reason != "prefiltered":
            entry["evidence"] = normalize_bounded_text(
                evidence, "drop_finding evidence"
            )
        elif evidence is not None:
            entry["evidence"] = normalize_bounded_text(
                evidence, "drop_finding evidence"
            )
        self._require_new_source_identity(
            self.dropped_findings, entry, "drop_finding"
        )
        self.dropped_findings.append(entry)

    def drop_check(self, reviewer, id, *, reason, evidence):
        """Record one source check judged void: its method could not have
        found what its claim denies."""
        entry = _source_entry({"reviewer": reviewer, "id": id}, "drop_check")
        if reason not in DROP_REASONS_CHECK:
            raise ValueError(
                f"drop_check reason must be one of {DROP_REASONS_CHECK}"
            )
        entry["reason"] = reason
        entry["evidence"] = normalize_bounded_text(
            evidence, "drop_check evidence"
        )
        self._require_new_source_identity(
            self.dropped_checks, entry, "drop_check"
        )
        self.dropped_checks.append(entry)

    def resolve_note(self, id, *, outcome, evidence, verifies=None):
        """Answer one orchestrator note from the reconciliation context.

        ``verifies`` names the change purpose's Verify items the note's
        evidence settles. Only a confirmed note settles anything — a
        refuted or unchecked note that bears on an item is a finding, not
        a settlement — and the record's Verify table credits the
        reconciliator for it.
        """
        if not isinstance(id, str) or NOTE_ID_RE.fullmatch(id) is None:
            raise ValueError("resolve_note id must be a canonical nN note id")
        if outcome not in NOTE_OUTCOMES:
            raise ValueError(f"resolve_note outcome must be one of {NOTE_OUTCOMES}")
        normalized_evidence = normalize_bounded_text(
            evidence, "resolve_note evidence"
        )
        # No citation is an absent or empty list; a present one is in the
        # grammar reviewer checks use.
        cited = normalize_verifies(verifies, "resolve_note") if verifies else []
        if cited and outcome != "confirmed":
            raise ValueError(
                f"resolve_note verifies settles nothing on a {outcome} note; "
                "only a confirmed note may cite Verify items"
            )
        if any(note["id"] == id for note in self.orchestrator_notes):
            raise ValueError(f"resolve_note identity {id} is already recorded")
        entry = {"id": id, "outcome": outcome, "evidence": normalized_evidence}
        if cited:
            entry["verifies"] = cited
        self.orchestrator_notes.append(entry)

    @staticmethod
    def _require_new_source_identity(entries, entry, label):
        identity = (entry["reviewer"], entry["id"])
        if any(
            (existing["reviewer"], existing["id"]) == identity
            for existing in entries
        ):
            raise ValueError(
                f"{label} identity {identity[0]}:{identity[1]} is already "
                "recorded"
            )

    def to_dict(self) -> Dict:
        """The review content, at the ledger's schema, plus the judgments."""
        if self._reconciliation is None:
            raise ValueError(
                "call set_reconciliation() before serializing the ledger"
            )
        data = super().to_dict()
        del data["reviewer"]
        data["schema"] = LEDGER_SCHEMA
        data["meta"]["reconciliation"] = dict(self._reconciliation)
        data["dropped_findings"] = [dict(e) for e in self.dropped_findings]
        data["dropped_checks"] = [dict(e) for e in self.dropped_checks]
        data["orchestrator_notes"] = [dict(e) for e in self.orchestrator_notes]
        return data

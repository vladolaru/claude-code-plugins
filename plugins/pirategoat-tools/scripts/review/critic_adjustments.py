#!/usr/bin/env python3
"""Validate and adjudicate source-bound decision-critic proposals.

The lifecycle has three steps and one writer each. The critic authors
proposal-only fields through ``critic.py --save``, which calls
:func:`prepare_proposal` to assign stable adjustment IDs and
:func:`write_critic_verdict` to publish the proposal beside a digest-bound
verdict marker. **The proposal is never rewritten afterwards.** The
orchestrator then submits only verified IDs, refuted IDs with reasons, and an
optional revised assessment through :func:`adjudicate`, which takes the output
lock once and makes exactly one ledger write: verified and unchecked entries
are applied with provenance, refuted entries are recorded with their reasons,
and every entry's ``outcome`` lands in ``review-findings.json``.

The ledger is therefore the one place adjudication is recorded, which is what
makes a second adjudication of the same proposal detectable (its IDs are
already there) and a partial one impossible (one write, or none).
"""

import argparse
import collections
import copy
import hashlib
import json
import os
import re
import sys
import unicodedata
import uuid
from typing import Mapping

try:
    from . import atomic_io
    from .review_document import (
        CHECK_TEXT_FIELDS,
        REQUIRED_CHECK_FIELDS,
        REQUIRED_FINDING_FIELDS,
        validate_finding_content_field,
        validate_ledger_ids,
        validate_review_content,
    )
    from .dispatch_status import AGENT_NAME_RE
    from .findings_ledger import (
        LEDGER_SCHEMA,
        RECONCILIATION_AGENT_LIST_FIELDS,
        RECONCILIATION_COUNT_FIELDS,
        RECONCILIATION_FIELDS,
    )
    from .verdict_rules import (
        LEDGER_VERDICTS,
        SEVERITY_RANK,
        summary_for,
    )
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import atomic_io
    from review.review_document import (
        CHECK_TEXT_FIELDS,
        REQUIRED_CHECK_FIELDS,
        REQUIRED_FINDING_FIELDS,
        validate_finding_content_field,
        validate_ledger_ids,
        validate_review_content,
    )
    from review.dispatch_status import AGENT_NAME_RE
    from review.findings_ledger import (
        LEDGER_SCHEMA,
        RECONCILIATION_AGENT_LIST_FIELDS,
        RECONCILIATION_COUNT_FIELDS,
        RECONCILIATION_FIELDS,
    )
    from review.verdict_rules import (
        LEDGER_VERDICTS,
        SEVERITY_RANK,
        summary_for,
    )

atomic_write_json = atomic_io.atomic_write_json

ACTIONS = ("promote", "demote", "rescope", "correct", "add", "remove")
TARGET_FINDING = "finding"
TARGET_CHECK = "check"
TARGET_KINDS = (TARGET_FINDING, TARGET_CHECK)
# `scope` is deliberately absent: it is derived from `line` to preserve the
# pairing described below, never set by the critic. IDs, provenance, and
# lifecycle fields are absent because they belong to the ledger and script.
FINDING_PATCH_FIELDS = (
    "severity", "title", "description", "recommendation", "file", "line",
    "category", "confidence",
)
CHECK_PATCH_FIELDS = CHECK_TEXT_FIELDS
ADD_REQUIRED_FIELDS = ("severity", "title", "file", "description",
                       "recommendation")
# Free-text ledger fields are bounded here because the ledger is their one
# authority; the offline metrics sanitizer applies the same ceiling.
MAX_LEDGER_TEXT_LENGTH = 4096

# Script-derived per-entry outcomes from the orchestrator's exact adjudication
# request. The request names only positive verified/refuted claims; every
# committed ID it omits is derived as OUTCOME_NOT_CHECKED. The outcome is
# recorded in the ledger beside the adjustment it belongs to — verified and
# not_checked in APPLIED_IDS_KEY, refuted in REJECTED_ADJUSTMENTS_KEY.
OUTCOME_KEY = "outcome"
OUTCOME_VERIFIED = "verified"
OUTCOME_REFUTED = "refuted"
OUTCOME_NOT_CHECKED = "not_checked"
OUTCOMES = (OUTCOME_VERIFIED, OUTCOME_REFUTED, OUTCOME_NOT_CHECKED)

# The orchestrator's post-critic assessment, submitted with the adjudication
# request. An applying batch invalidates the reconciler's
# assessment (see INVALIDATED_ASSESSMENTS_KEY below), and without a replacement
# REVISE run published a ledger whose Assessment section was a pointer to
# prose only a human could read. This is that assessment's machine-readable
# seat: on apply it BECOMES the ledger's assessment, with the invalidation
# record left intact beside it.
REVISED_ASSESSMENT_KEY = "revised_assessment"

ADJUSTMENTS_FILENAME = "decision-critic-adjustments.json"
FINDINGS_FILENAME = "review-findings.json"
CRITIC_VERDICT_FILENAME = "decision-critic-verdict.json"
# One record per applied adjustment: `{"adjustment_id": ..., "outcome": ...}`.
# The id half is what makes a second adjudication of the same proposal
# detectable; the outcome half is the orchestrator's verdict on that decision,
# so the ledger — the artifact bot mode, baselines, and metrics actually
# read — carries what was probed rather than leaving it to the human report.
APPLIED_IDS_KEY = "applied_critic_adjustments"

# Where the ledger's pre-adjustment verdict goes the FIRST time an applying
# batch changes it — the same audit spirit as INVALIDATED_ASSESSMENTS_KEY below.
# First time only: a second round must name what the ledger came in as, not
# what the previous round left behind.
VERDICT_BEFORE_ADJUSTMENTS_KEY = "verdict_before_adjustments"
# The rejection half of the same audit trail: one record per adjustment the
# orchestrator refuted, carrying the refuted entry's action and target plus
# the reason the probe disproved it. The proposal file is never consulted
# downstream, so without this key a refuted decision would leave no trace in
# review-findings.json — the artifact bot mode, baselines, and metrics
# actually read. The shared Markdown renderer projects it alongside the
# applied decisions so the record accounts for every critic decision.
REJECTED_ADJUSTMENTS_KEY = "rejected_critic_adjustments"

# The only `schema` value ADJUSTMENTS_FILENAME is accepted under. A document
# carrying any other value — or none —
# is out of that template and refused whole, the same all-or-nothing way
# an unknown action or an unaddressable id is: a critic decision written
# against a contract this module does not honor must fail loudly rather
# than being silently accepted and possibly misread.
ADJUSTMENTS_SCHEMA = 2
VERDICT_MARKER_SCHEMA = 2
ADJUDICATION_SCHEMA = 2

# The canonical critic verdict vocabulary, owned here because this module is
# what commits it: `write_critic_verdict()` is the one writer of the marker,
# and `REVISE_VERDICT` is the one verdict `adjudicate()` accepts. critic.py
# and the offline metrics consumer read these rather than respelling them.
CRITIC_VERDICTS = ("STAND", "REVISE", "ESCALATE")
# Deliberately NOT a member of CRITIC_VERDICTS: it is not a critique outcome,
# it is the record that no critique happened — pipeline step 10 commits it
# when quick mode skips the critic. Consumers that measure critique quality
# must exclude it; consumers that measure whether a critic ran must not.
CRITIC_VERDICT_SKIPPED = "SKIPPED"
VALID_CRITIC_VERDICTS = CRITIC_VERDICTS + (CRITIC_VERDICT_SKIPPED,)
# The one verdict that sanctions applying adjustments. Everything else —
# STAND, ESCALATE, SKIPPED, an unrecognized string, a missing file — refuses.
REVISE_VERDICT = "REVISE"

_PROPOSAL_TOP_LEVEL_KEYS = frozenset({"schema", "adjustments"})
_PROPOSAL_ENTRY_KEYS = frozenset({"action", "target", "fields", "rationale"})
_PREPARED_ENTRY_KEYS = _PROPOSAL_ENTRY_KEYS | {"adjustment_id"}
_REQUEST_KEYS = frozenset({
    "schema",
    "verified",
    "refuted",
    REVISED_ASSESSMENT_KEY,
})
_REFUTED_REQUEST_KEYS = frozenset({"adjustment_id", "rejection_reason"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Discriminated outcomes of reading the findings ledger off disk, shared by
# every caller that reads it (see `read_findings_file()`). Absent is separate
# because callers answer it differently — step 11 treats a missing ledger as
# a different degradation from an unusable one — while every way of being
# unusable is one answer: nothing may read this file.
FINDINGS_READ_OK = "ok"
FINDINGS_READ_ABSENT = "absent"
FINDINGS_READ_INVALID = "invalid"

# What `read_findings_file()` hands back: the state above, the parsed object
# (only on FINDINGS_READ_OK), and the exception that produced a failure state
# (None only for OK). Carrying the exception is what lets a caller report the
# defect — a stale verdict, a malformed record — and not just "unreadable".
FindingsRead = collections.namedtuple(
    "FindingsRead", ("status", "findings", "error")
)

# Where an invalidated `assessment` goes. The adjustment vocabulary addresses
# finding fields and the content of recorded checks, but deliberately cannot
# address ledger-level prose. That leaves the
# reconciler's assessment as the one part of the artifact a critic round
# can invalidate but not correct: "one CRITICAL blocker" stays written
# above a list where the critical has just been demoted to low.
#
# The pipeline cannot re-derive that prose (it is LLM output, not a
# projection of the findings), so an applying batch invalidates it rather
# than leaving it to contradict the ledger it summarizes. Invalidated, not
# deleted: the text moves here beside the ids of the decisions that
# invalidated it, the same way a removed finding moves into
# `findings_removed_by_critic` carrying the action that removed it. A list,
# because a second reconciliation-plus-critic round is a second invalidation and
# must not erase the first.
INVALIDATED_ASSESSMENTS_KEY = "invalidated_assessments"
ASSESSMENT_KEY = "assessment"


class AdjustmentValidationError(ValueError):
    """One rejected lifecycle request, carrying every independent problem."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


def _schema_is(value, expected):
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def _extra_key_problems(value, allowed, label):
    if not isinstance(value, dict):
        return []
    return [
        f"{label}: extra key {key!r} is not allowed"
        for key in sorted(set(value) - set(allowed))
    ]


def _validate_target(target, action, label):
    """Validate one action-discriminated finding/check target."""
    if not isinstance(target, dict):
        return [f"{label}: 'target' must be an object"]
    expected = {"kind"} if action == "add" else {"kind", "id"}
    allowed = expected | ({"id"} if action == "add" else set())
    problems = _extra_key_problems(target, allowed, f"{label}.target")
    if action == "add" and "id" in target:
        problems.append(
            f"{label}.target: add target must not include id; the ledger "
            "allocates it"
        )
    missing = sorted(expected - set(target))
    for key in missing:
        problems.append(f"{label}: missing required target.{key}")
    kind = target.get("kind")
    if kind not in TARGET_KINDS:
        problems.append(
            f"{label}.target: 'kind' must be one of {', '.join(TARGET_KINDS)}"
        )
        return problems
    if action == "add":
        if kind != TARGET_FINDING:
            problems.append(f"{label}: add can target only a finding")
        return problems
    target_id = target.get("id")
    if not isinstance(target_id, str) or not target_id:
        problems.append(f"{label}.target: 'id' must be a non-empty string")
    elif kind == TARGET_FINDING and not re.fullmatch(r"f[1-9][0-9]*", target_id):
        problems.append(f"{label}.target: finding id must use canonical fN form")
    elif kind == TARGET_CHECK and not re.fullmatch(r"c[1-9][0-9]*", target_id):
        problems.append(f"{label}.target: check id must use canonical cN form")
    return problems


def _validate_field_value(key, value, label):
    if key in FINDING_PATCH_FIELDS:
        try:
            validate_finding_content_field(key, value, label)
        except ValueError as error:
            return str(error)
    if key in CHECK_PATCH_FIELDS and (
        not isinstance(value, str) or not value.strip()
    ):
        return f"{label}: {key!r} must be a non-empty string"
    return None


def _validate_fields(fields, allowed_fields, label):
    """Reject anything outside one target kind's adjustable vocabulary."""
    if not isinstance(fields, dict):
        return [f"{label}: 'fields' must be an object"]
    problems = []
    for key, value in fields.items():
        if key not in allowed_fields:
            problems.append(
                f"{label}: field {key!r} is not adjustable "
                f"(allowed: {', '.join(allowed_fields)})"
            )
            continue
        problem = _validate_field_value(key, value, label)
        if problem:
            problems.append(problem)
    return problems


def _validate_proposal_entry(entry, label, *, require_adjustment_id):
    """Validate one proposal entry, before or after ID assignment."""
    if not isinstance(entry, dict):
        return [f"{label} must be an object"]

    allowed = (
        _PREPARED_ENTRY_KEYS if require_adjustment_id else _PROPOSAL_ENTRY_KEYS
    )
    problems = _extra_key_problems(entry, allowed, label)
    action = entry.get("action")
    if action not in ACTIONS:
        problems.append(
            f"{label}: unknown action {action!r} "
            f"(allowed: {', '.join(ACTIONS)})"
        )

    rationale = entry.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        problems.append(f"{label}: 'rationale' must be a non-empty string")

    target = entry.get("target")
    problems.extend(_validate_target(target, action, label))
    kind = target.get("kind") if isinstance(target, dict) else None

    fields = entry.get("fields")
    if fields is None and action == "remove":
        fields = {}
    allowed_fields = (
        CHECK_PATCH_FIELDS if kind == TARGET_CHECK else FINDING_PATCH_FIELDS
    )
    field_problems = _validate_fields(fields, allowed_fields, label)
    problems.extend(field_problems)
    if not field_problems:
        if action in ("promote", "demote") and set(fields) != {"severity"}:
            problems.append(
                f"{label}: {action} requires exactly the severity field"
            )
        elif action == "rescope" and set(fields) != {"file", "line"}:
            problems.append(
                f"{label}: rescope requires exactly the file and line fields"
            )
        elif action == "correct" and not fields:
            problems.append(
                f"{label}: correct requires at least one field"
            )
        elif action == "add":
            missing = [key for key in ADD_REQUIRED_FIELDS if key not in fields]
            if missing:
                problems.append(
                    f"{label}: add requires fields {', '.join(missing)}"
                )
        elif action == "remove" and fields:
            problems.append(f"{label}: remove does not accept replacement fields")

    if kind == TARGET_CHECK and action not in ("correct", "remove"):
        problems.append(
            f"{label}: {action!r} is not allowed for check targets"
        )

    if require_adjustment_id:
        adjustment_id = entry.get("adjustment_id")
        if not isinstance(adjustment_id, str) or not adjustment_id:
            problems.append(
                f"{label}: 'adjustment_id' must be a non-empty string"
            )
    return problems


def _validate_proposal_entries(adjustments, *, require_adjustment_id):
    """Validate every entry plus invariants that belong to the whole batch."""
    problems = []
    seen_adjustment_ids = {}
    seen_targets = {}
    for index, entry in enumerate(adjustments):
        label = f"adjustment[{index}]"
        problems.extend(_validate_proposal_entry(
            entry, label, require_adjustment_id=require_adjustment_id
        ))
        if not isinstance(entry, dict):
            continue

        if require_adjustment_id:
            adjustment_id = entry.get("adjustment_id")
            if isinstance(adjustment_id, str) and adjustment_id:
                if adjustment_id in seen_adjustment_ids:
                    problems.append(
                        f"{label}: duplicate adjustment_id {adjustment_id!r} "
                        f"(also adjustment["
                        f"{seen_adjustment_ids[adjustment_id]}])"
                    )
                else:
                    seen_adjustment_ids[adjustment_id] = index

        action = entry.get("action")
        target = entry.get("target")
        target_kind = target.get("kind") if isinstance(target, dict) else None
        target_id = target.get("id") if isinstance(target, dict) else None
        if (
            action not in ACTIONS
            or action == "add"
            or target_kind not in TARGET_KINDS
            or not isinstance(target_id, str)
            or not target_id
        ):
            continue
        target_key = (target_kind, target_id)
        if target_key in seen_targets:
            first_index, first_action = seen_targets[target_key]
            if first_action == "remove":
                problems.append(
                    f"{label}: target {target_kind} {target_id!r} is removed by "
                    f"adjustment[{first_index}] in this batch"
                )
            else:
                problems.append(
                    f"{label}: duplicate target {target_kind} {target_id!r} — "
                    f"merge changes into one entry per target"
                )
        else:
            seen_targets[target_key] = (index, action)
    return problems


def validate_proposal_input(payload: Mapping[str, object]):
    """Return every problem in a critic-authored proposal input."""
    if not isinstance(payload, dict):
        return [f"{ADJUSTMENTS_FILENAME} must be a JSON object"]
    problems = _extra_key_problems(
        payload, _PROPOSAL_TOP_LEVEL_KEYS, ADJUSTMENTS_FILENAME
    )
    schema = payload.get("schema")
    if not _schema_is(schema, ADJUSTMENTS_SCHEMA):
        problems.append(
            f"{ADJUSTMENTS_FILENAME}: 'schema' must be "
            f"{ADJUSTMENTS_SCHEMA}, got {schema!r}"
        )
    adjustments = payload.get("adjustments")
    if not isinstance(adjustments, list):
        problems.append(
            f"{ADJUSTMENTS_FILENAME}: 'adjustments' must be a list"
        )
        return problems
    problems.extend(_validate_proposal_entries(
        adjustments, require_adjustment_id=False
    ))
    return problems


def prepare_proposal(payload: Mapping[str, object]) -> dict[str, object]:
    """Validate critic-authored proposal fields and assign stable IDs."""
    problems = validate_proposal_input(payload)
    if problems:
        raise AdjustmentValidationError(problems)
    document = copy.deepcopy(payload)
    used_ids = set()
    for entry in document["adjustments"]:
        if entry.get("fields") is None and entry.get("action") == "remove":
            entry["fields"] = {}
        adjustment_id = uuid.uuid4().hex
        while adjustment_id in used_ids:
            adjustment_id = uuid.uuid4().hex
        entry["adjustment_id"] = adjustment_id
        used_ids.add(adjustment_id)
    return document


def proposal_digest(document: Mapping[str, object]) -> str:
    """Hash the exact proposal bytes the verdict marker commits."""
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_adjustments_document(payload):
    """Validate the persisted proposal — entries with their assigned IDs."""
    if not isinstance(payload, dict):
        return [f"{ADJUSTMENTS_FILENAME} must be a JSON object"]
    problems = _extra_key_problems(
        payload, _PROPOSAL_TOP_LEVEL_KEYS, ADJUSTMENTS_FILENAME
    )
    schema = payload.get("schema")
    if not _schema_is(schema, ADJUSTMENTS_SCHEMA):
        problems.append(
            f"{ADJUSTMENTS_FILENAME}: 'schema' must be "
            f"{ADJUSTMENTS_SCHEMA}, got {schema!r}"
        )
    adjustments = payload.get("adjustments")
    if not isinstance(adjustments, list):
        problems.append(
            f"{ADJUSTMENTS_FILENAME}: 'adjustments' must be a list"
        )
        return problems
    problems.extend(_validate_proposal_entries(
        adjustments, require_adjustment_id=True
    ))
    return problems


def empty_proposal():
    """The proposal every non-REVISE verdict commits."""
    return {"schema": ADJUSTMENTS_SCHEMA, "adjustments": []}


def _read_json_object(path, label):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not readable JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _validate_verdict_marker(marker):
    if not isinstance(marker, dict):
        return [f"{CRITIC_VERDICT_FILENAME} must be a JSON object"]
    problems = _extra_key_problems(
        marker,
        {"schema", "verdict", "proposal_digest"},
        CRITIC_VERDICT_FILENAME,
    )
    if not _schema_is(marker.get("schema"), VERDICT_MARKER_SCHEMA):
        problems.append(
            f"{CRITIC_VERDICT_FILENAME}: 'schema' must be "
            f"{VERDICT_MARKER_SCHEMA}"
        )
    verdict = marker.get("verdict")
    if verdict not in VALID_CRITIC_VERDICTS:
        problems.append(
            f"{CRITIC_VERDICT_FILENAME}: unknown verdict {verdict!r}"
        )
    digest = marker.get("proposal_digest")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        problems.append(
            f"{CRITIC_VERDICT_FILENAME}: 'proposal_digest' must be a sha256"
        )
    return problems


def write_critic_verdict(output_dir, verdict, proposal):
    """The one writer of the proposal file and its verdict marker.

    Called under the caller's lock, with the marker written last so an
    interrupted publication leaves a proposal nothing will read. The proposal
    is never rewritten afterwards: adjudication is recorded in the ledger.
    """
    if verdict not in VALID_CRITIC_VERDICTS:
        raise ValueError(f"unknown critic verdict {verdict!r}")
    problems = validate_adjustments_document(proposal)
    if problems:
        raise AdjustmentValidationError(problems)
    if verdict != REVISE_VERDICT and proposal["adjustments"]:
        raise ValueError(f"{verdict} may not commit a non-empty proposal")
    digest = proposal_digest(proposal)
    atomic_write_json(os.path.join(output_dir, ADJUSTMENTS_FILENAME), proposal)
    atomic_write_json(
        os.path.join(output_dir, CRITIC_VERDICT_FILENAME),
        {
            "schema": VERDICT_MARKER_SCHEMA,
            "verdict": verdict,
            "proposal_digest": digest,
        },
    )
    return digest


def read_committed_proposal(output_dir):
    """Return (verdict, proposal) only when the marker binds the proposal."""
    marker = _read_json_object(
        os.path.join(output_dir, CRITIC_VERDICT_FILENAME),
        CRITIC_VERDICT_FILENAME,
    )
    problems = _validate_verdict_marker(marker)
    if problems:
        raise AdjustmentValidationError(problems)
    proposal = _read_json_object(
        os.path.join(output_dir, ADJUSTMENTS_FILENAME), ADJUSTMENTS_FILENAME
    )
    problems = validate_adjustments_document(proposal)
    if problems:
        raise AdjustmentValidationError(problems)
    if marker["proposal_digest"] != proposal_digest(proposal):
        raise ValueError(
            "proposal digest mismatch: the proposal changed after its "
            "verdict was committed"
        )
    if marker["verdict"] != REVISE_VERDICT and proposal["adjustments"]:
        raise ValueError(
            f"{marker['verdict']} may not commit a non-empty proposal"
        )
    return marker["verdict"], proposal


def read_critic_verdict(output_dir):
    """Read the verdict from a complete source-bound critic snapshot.

    Returns the validated verdict, or ``None`` when the marker or adjacent
    proposal is absent, malformed, schema-invalid, or digest-mismatched. The
    presentation mapping downstream consumers need instead — ``SKIPPED`` and
    an unusable snapshot both reading as ``unavailable`` — lives next door in
    :func:`critic_verdict_for_state`.
    """
    try:
        verdict, _proposal = read_committed_proposal(output_dir)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return verdict


def critic_verdict_for_state(output_dir):
    """Read the critic's verdict the way `state["critic_verdict"]` wants it.

    A thin presentation wrapper around `read_critic_verdict()`: a missing
    or unparseable verdict file and an explicit "SKIPPED" both collapse to
    "unavailable" here, so downstream consumers (pirategoat-bot) correctly
    show "not cross-validated" either way. Named and kept beside the raw
    reader specifically so a future consumer reaches for this one instead
    of re-deriving the same two-way mapping inline — the trap step 11 used
    to fall into before this existed.
    """
    verdict = read_critic_verdict(output_dir)
    return (
        "unavailable"
        if verdict in (None, CRITIC_VERDICT_SKIPPED)
        else verdict
    )


_LEDGER_EXTENSION_FIELDS = frozenset({
    "host_context_banner",
    APPLIED_IDS_KEY,
    VERDICT_BEFORE_ADJUSTMENTS_KEY,
    "findings_removed_by_critic",
    "checks_removed_by_critic",
    REJECTED_ADJUSTMENTS_KEY,
    INVALIDATED_ASSESSMENTS_KEY,
})
_BASE_FINDING_FIELDS = REQUIRED_FINDING_FIELDS
_OPTIONAL_FINDING_FIELDS = frozenset({
    "severity_floor", "scope", "code_snippet", "references",
    "behavior_evidence", "source_cited", "channel", "critic_adjustment",
})
_CHECK_FIELDS = REQUIRED_CHECK_FIELDS | {"critic_adjustment"}


def _require_nonnegative_integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _validate_unique_strings(value, label, *, nullable=False):
    if nullable and value is None:
        return
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"{label} must be a list of unique non-empty strings")


def _is_agent_name(value):
    """The one producer agent-name grammar, shared with dispatch."""
    return (
        isinstance(value, str) and AGENT_NAME_RE.fullmatch(value) is not None
    )


def _validate_agent_names(value, label, *, nullable=False):
    if nullable and value is None:
        return
    if (
        not isinstance(value, list)
        or any(not _is_agent_name(item) for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(
            f"{label} must be a list of unique lowercase agent names"
        )


def _validate_bounded_text(value, label):
    """Non-empty prose bounded for the machine readers that carry it on.

    The ledger is the authority on this text, so the bound lives here: the
    reconciliation block flows verbatim into the telemetry manifest and from
    there into offline metrics reports, whose sanitizer drops the whole block
    rather than one oversized or control-character-bearing string.
    """
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_LEDGER_TEXT_LENGTH
        or "\x00" in value
        or any(
            character not in ("\n", "\t")
            and unicodedata.category(character) in ("Cc", "Cf")
            for character in value
        )
    ):
        raise ValueError(
            f"{label} must be non-empty text of at most "
            f"{MAX_LEDGER_TEXT_LENGTH} characters with no control characters"
        )


def _validate_reconciliation(value):
    label = f"{FINDINGS_FILENAME}: meta.reconciliation"
    if not isinstance(value, dict) or set(value) != RECONCILIATION_FIELDS:
        raise ValueError(f"{label} must have the exact canonical fields")
    for field in RECONCILIATION_COUNT_FIELDS:
        _require_nonnegative_integer(value[field], f"{label}.{field}")
    judged = (
        value["verified_concern_count"]
        + value["false_positive_concern_count"]
        + value["out_of_scope_concern_count"]
    )
    if judged != value["grouped_concern_count"]:
        raise ValueError(
            f"{label}: classification counts do not partition "
            "grouped_concern_count"
        )
    for field in RECONCILIATION_AGENT_LIST_FIELDS:
        _validate_agent_names(
            value[field],
            f"{label}.{field}",
            nullable=field != "reviewing_agents",
        )
    not_applicable = value["not_applicable_agents"]
    if not isinstance(not_applicable, list):
        raise ValueError(f"{label}.not_applicable_agents must be a list")
    names = []
    for index, agent in enumerate(not_applicable):
        entry = f"{label}.not_applicable_agents[{index}]"
        if not isinstance(agent, dict) or set(agent) != {"name", "skip_reason"}:
            raise ValueError(f"{entry} is malformed")
        if not _is_agent_name(agent["name"]):
            raise ValueError(f"{entry}.name must be a lowercase agent name")
        _validate_bounded_text(agent["skip_reason"], f"{entry}.skip_reason")
        names.append(agent["name"])
    if len(names) != len(set(names)):
        raise ValueError(f"{label}.not_applicable_agents contains duplicates")


def _validate_host_context_banner(value):
    label = f"{FINDINGS_FILENAME}: host_context_banner"
    if (
        not isinstance(value, dict)
        or set(value) != {"degraded", "reason", "message", "unresolved"}
        or value.get("degraded") is not True
        or value.get("reason") not in ("partial_unresolved", "fully_unavailable")
        or not isinstance(value.get("message"), str)
        or not value["message"].strip()
        or not isinstance(value.get("unresolved"), list)
    ):
        raise ValueError(f"{label} is malformed")
    for index, unresolved in enumerate(value["unresolved"]):
        if (
            not isinstance(unresolved, dict)
            or not isinstance(unresolved.get("name"), str)
            or not unresolved["name"].strip()
            or not isinstance(unresolved.get("reason"), str)
            or not unresolved["reason"].strip()
            or (
                "source" in unresolved
                and not isinstance(unresolved["source"], str)
            )
        ):
            raise ValueError(f"{label}.unresolved[{index}] is malformed")


def _validate_critic_provenance(value, label, *, removed):
    """Validate one ledger entry's critic_adjustment provenance, if any.

    The applier is the only writer of this block and it writes one shape;
    this boundary checks that shape, not a per-action grammar the applier
    cannot emit. The one action that is not free is `remove`: it belongs to
    an entry parked in a `*_removed_by_critic` list, and to nothing else.
    """
    if value is None and not removed:
        return
    if (
        not isinstance(value, dict)
        or value.get("action") not in ACTIONS
        or (value["action"] == "remove") != removed
        or not isinstance(value.get("rationale"), str)
        or not value["rationale"].strip()
        or ("prior" in value and not isinstance(value["prior"], dict))
        or set(value) - {"action", "rationale", "prior"}
    ):
        raise ValueError(f"{label}: critic_adjustment provenance is malformed")


def _validate_ledger_finding(finding, index, *, removed=False):
    label = (
        f"{FINDINGS_FILENAME}: "
        f"{'findings_removed_by_critic' if removed else 'findings'}[{index}]"
    )
    allowed = _BASE_FINDING_FIELDS | _OPTIONAL_FINDING_FIELDS
    if not isinstance(finding, dict) or not set(finding) <= allowed:
        raise ValueError(f"{label} has unexpected fields")
    line = finding.get("line")
    if line is None and finding.get("scope") != "file":
        raise ValueError(f"{label} file scope is not canonical")
    if line is not None and "scope" in finding:
        raise ValueError(f"{label} line scope is not canonical")
    if finding.get("channel") == "blocking":
        raise ValueError(f"{label}.channel must omit the blocking default")
    _validate_critic_provenance(
        finding.get("critic_adjustment"), label, removed=removed
    )


def _validate_ledger_check(check, index, *, removed=False):
    label = (
        f"{FINDINGS_FILENAME}: "
        f"{'checks_removed_by_critic' if removed else 'checks'}[{index}]"
    )
    if not isinstance(check, dict) or not set(check) <= _CHECK_FIELDS:
        raise ValueError(f"{label} has unexpected fields")
    _validate_critic_provenance(
        check.get("critic_adjustment"), label, removed=removed
    )


def _validate_invalidated_assessments(value, applied_ids):
    label = f"{FINDINGS_FILENAME}: {INVALIDATED_ASSESSMENTS_KEY}"
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list")
    for index, record in enumerate(value):
        if (
            not isinstance(record, dict)
            or set(record) != {
                "text", "invalidated_by_critic_adjustment_ids",
            }
            or not isinstance(record.get("text"), str)
            or not record["text"].strip()
        ):
            raise ValueError(f"{label}[{index}] is malformed")
        ids = record["invalidated_by_critic_adjustment_ids"]
        _validate_unique_strings(ids, f"{label}[{index}] adjustment ids")
        if not ids or not set(ids) <= applied_ids:
            raise ValueError(f"{label}[{index}] cites unknown adjustments")


def validate_findings_document(document):
    """Validate one exact canonical post-critic findings ledger.

    This is the single reader-boundary authority for ``review-findings.json``.
    The ledger is review content plus two things a reviewer document does not
    have: the reconciliation block and the critic applier's provenance. The
    content half is delegated to :func:`validate_review_content`; only those
    two extensions are checked here.
    """
    if not isinstance(document, dict):
        raise ValueError(f"{FINDINGS_FILENAME} must be a JSON object")
    # Shallow copies, not a deep one: the only keys removed are top-level
    # and one inside `meta`, and every validator below reads without
    # writing. Deep-copying the whole ledger — findings, removed entries,
    # checks, every prose string — ran on each of the dozen-odd validations
    # a single run performs.
    base = dict(document)
    extensions = {
        field: base.pop(field)
        for field in _LEDGER_EXTENSION_FIELDS
        if field in base
    }
    meta = base.get("meta")
    reconciliation = None
    if isinstance(meta, dict):
        meta = dict(meta)
        base["meta"] = meta
        reconciliation = meta.pop("reconciliation", None)
    try:
        validate_review_content(base, schema=LEDGER_SCHEMA)
    except ValueError as error:
        raise ValueError(f"{FINDINGS_FILENAME}: {error}") from error
    if document["verdict"] not in LEDGER_VERDICTS:
        raise ValueError(f"{FINDINGS_FILENAME}: reconciler verdict is invalid")
    if (
        isinstance(document["assessment"], str)
        and not document["assessment"].strip()
    ):
        raise ValueError(f"{FINDINGS_FILENAME}: assessment must not be blank")
    if reconciliation is None:
        raise ValueError(
            f"{FINDINGS_FILENAME}: meta.reconciliation is required"
        )
    _validate_reconciliation(reconciliation)
    if "host_context_banner" in extensions:
        _validate_host_context_banner(extensions["host_context_banner"])

    live_findings = document["findings"]
    live_checks = document["checks"]
    removed_findings = extensions.get("findings_removed_by_critic", [])
    removed_checks = extensions.get("checks_removed_by_critic", [])
    if not isinstance(removed_findings, list) or (
        "findings_removed_by_critic" in extensions and not removed_findings
    ):
        raise ValueError(
            f"{FINDINGS_FILENAME}: findings_removed_by_critic must be a "
            "non-empty list"
        )
    if not isinstance(removed_checks, list) or (
        "checks_removed_by_critic" in extensions and not removed_checks
    ):
        raise ValueError(
            f"{FINDINGS_FILENAME}: checks_removed_by_critic must be a "
            "non-empty list"
        )
    for index, finding in enumerate(live_findings):
        _validate_ledger_finding(finding, index)
    for index, finding in enumerate(removed_findings):
        _validate_ledger_finding(finding, index, removed=True)
    for index, check in enumerate(live_checks):
        _validate_ledger_check(check, index)
    for index, check in enumerate(removed_checks):
        _validate_ledger_check(check, index, removed=True)
    try:
        validate_ledger_ids(
            live_findings + removed_findings,
            live_checks + removed_checks,
            base["meta"]["next_finding_number"],
            base["meta"]["next_check_number"],
        )
    except ValueError as error:
        raise ValueError(f"{FINDINGS_FILENAME}: {error}") from error

    applied_records = _load_recorded_records(document)
    if any(
        record[OUTCOME_KEY] not in (OUTCOME_VERIFIED, OUTCOME_NOT_CHECKED)
        for record in applied_records
    ):
        raise ValueError(f"{FINDINGS_FILENAME}: applied outcomes are invalid")
    applied_by_id = _records_by_adjustment_id(applied_records, APPLIED_IDS_KEY)
    rejected_records = _load_rejected_records(document)
    rejected_by_id = _records_by_adjustment_id(
        rejected_records, REJECTED_ADJUSTMENTS_KEY
    )
    if set(applied_by_id) & set(rejected_by_id):
        raise ValueError(
            f"{FINDINGS_FILENAME}: adjustment ids are both applied and rejected"
        )
    has_adjusted_entries = any(
        item.get("critic_adjustment") is not None
        for item in live_findings + live_checks + removed_findings + removed_checks
    )
    if applied_by_id and not has_adjusted_entries:
        raise ValueError(
            f"{FINDINGS_FILENAME}: applied records have no critic provenance"
        )
    critic_requires_applied = (
        has_adjusted_entries
        or "findings_removed_by_critic" in extensions
        or "checks_removed_by_critic" in extensions
        or VERDICT_BEFORE_ADJUSTMENTS_KEY in extensions
        or INVALIDATED_ASSESSMENTS_KEY in extensions
    )
    if critic_requires_applied and not applied_by_id:
        raise ValueError(
            f"{FINDINGS_FILENAME}: critic provenance has no applied records"
        )
    if APPLIED_IDS_KEY in extensions and not applied_records:
        raise ValueError(f"{FINDINGS_FILENAME}: applied records must not be empty")
    if REJECTED_ADJUSTMENTS_KEY in extensions and not rejected_records:
        raise ValueError(f"{FINDINGS_FILENAME}: rejected records must not be empty")
    if VERDICT_BEFORE_ADJUSTMENTS_KEY in extensions and extensions[
        VERDICT_BEFORE_ADJUSTMENTS_KEY
    ] not in LEDGER_VERDICTS:
        raise ValueError(
            f"{FINDINGS_FILENAME}: verdict_before_adjustments is invalid"
        )
    if INVALIDATED_ASSESSMENTS_KEY in extensions:
        _validate_invalidated_assessments(
            extensions[INVALIDATED_ASSESSMENTS_KEY], set(applied_by_id)
        )
    return document


def read_findings_file(path):
    """Read review-findings.json into a discriminated result.

    ONE spelling of open-parse-shape-check for the ledger, shared by every
    caller: before it existed the call sites opened this file with slightly
    different guards, and the differences between them were accidents rather
    than decisions — one of them even opened the file with the platform's
    locale encoding while the others pinned UTF-8.

    Returns a `FindingsRead`. Only the absent/unusable split is preserved,
    because that is the only distinction a caller acts on; every way of
    being unusable means the same thing to all of them — nothing may read
    this ledger. The originating error travels along so a caller can name
    the defect rather than only the state.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            findings = json.load(f)
    except FileNotFoundError as err:
        return FindingsRead(FINDINGS_READ_ABSENT, None, err)
    except (
        OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError
    ) as err:
        return FindingsRead(FINDINGS_READ_INVALID, None, err)
    try:
        validate_findings_document(findings)
    except (ValueError, RecursionError) as err:
        return FindingsRead(FINDINGS_READ_INVALID, None, err)
    return FindingsRead(FINDINGS_READ_OK, findings, None)


def _unreadable_ledger(read):
    """Name the defect that made the ledger unusable, not just the state.

    Every non-OK read carries the exception that produced it, so the caller
    reports what is actually wrong with the file — the alternative, a bare
    status, sent a reader looking for a defect the module already knew.
    """
    return (
        f"{FINDINGS_FILENAME} is not a readable ledger "
        f"({read.status}): {read.error}"
    )


def write_findings(output_dir, findings):
    """The ONE sanctioned write path for review-findings.json.

    Replaces the file atomically through the shared `atomic_write_json`.

    Addressed by output directory, not by path, like every other public
    entry point in this module (`read_critic_verdict`, `adjudicate`). The
    filename is this module's constant, so a caller cannot point the
    sanctioned writer at the wrong file — which matters most for the one
    writer the pipeline cannot check, the review-reconciliator agent
    following a taught snippet.

    Two writers exist across a run and both call this: the
    review-reconciliator agent's first write (taught in
    `agents/review-reconciliator.md`, via `findings_save.py`) and
    `adjudicate()` below. One writer means one place where the ledger's
    atomicity and its filename are decided, and it is the rule a third
    writer would break: every change to this file goes through the
    adjudication channel, never a hand edit.
    """
    if not isinstance(findings, dict):
        raise ValueError(f"{FINDINGS_FILENAME} must be a JSON object")
    atomic_write_json(os.path.join(output_dir, FINDINGS_FILENAME), findings)


def _apply_scope_pairing(finding, line_is_null):
    """Keep `scope` and `line` consistent for one finding.

    The contract is a pair, not two independent fields: schemas/
    review-output.ts declares `scope?: 'file'` as "present (with
    line: null) when the finding is file-scoped", and output.py sets
    `finding['scope'] = 'file'` only for file-scoped findings, which its
    Markdown renderer then branches on. A patch that moved `line`
    without moving `scope` would publish a line-anchored finding still
    marked file-scoped, or a null line with no marker at all.
    """
    if line_is_null:
        finding["scope"] = "file"
    else:
        finding.pop("scope", None)


def _recount_summary(review, findings):
    """Project the shared review-state derivation into the ledger summary.

    An out-of-vocabulary severity would silently drop out of
    `by_severity` while still counting in `total_findings`, publishing a
    summary that undercounts its own list. Every write through this
    module is validated, so the only source is a malformed pre-existing
    ledger — which is worth failing on, not smoothing over.

    The block is replaced rather than patched key by key: the canonical
    validator compares `summary` against this exact derivation, so a key
    that survived a recount was already rejected on the next read.

    Returns the complete shared derivation so the caller publishes its
    gating verdict without choosing a population or applying thresholds
    itself.
    """
    try:
        derived = summary_for(findings)
    except ValueError as error:
        raise ValueError(f"{FINDINGS_FILENAME}: {error}") from error
    review["summary"] = derived["summary"]
    return derived


def _applied_record(value):
    """Validate one schema-2 applied adjustment record."""
    if (
        isinstance(value, dict)
        and isinstance(value.get("adjustment_id"), str)
        and value["adjustment_id"]
        and set(value) == {"adjustment_id", OUTCOME_KEY}
        and value.get(OUTCOME_KEY) in OUTCOMES
    ):
        return dict(value)
    return None


def _load_recorded_records(findings):
    """Read the adjustment records the findings file already contains."""
    recorded = findings.get(APPLIED_IDS_KEY)
    if recorded is None:
        return []
    records = [_applied_record(value) for value in recorded] if isinstance(
        recorded, list
    ) else None
    if records is None or None in records:
        raise ValueError(
            f"{FINDINGS_FILENAME}: {APPLIED_IDS_KEY!r} must be a list of "
            f"records carrying a non-empty string 'adjustment_id'"
        )
    return records


def _load_rejected_records(findings):
    """Read complete schema-2 rejection audit records."""
    recorded = findings.get(REJECTED_ADJUSTMENTS_KEY)
    if recorded is None:
        return []
    required = {
        "adjustment_id", "action", "target", OUTCOME_KEY,
        "rejection_reason",
    }
    records = []
    if isinstance(recorded, list):
        for value in recorded:
            if not isinstance(value, dict) or set(value) != required:
                break
            adjustment_id = value.get("adjustment_id")
            action = value.get("action")
            target = value.get("target")
            reason = value.get("rejection_reason")
            outcome = value.get(OUTCOME_KEY)
            if not (
                isinstance(adjustment_id, str)
                and adjustment_id
                and action in ACTIONS
                and isinstance(target, dict)
                and not _validate_target(target, action, "rejection record")
                and not (
                    target.get("kind") == TARGET_CHECK
                    and action not in ("correct", "remove")
                )
                and isinstance(reason, str)
                and reason.strip()
                and outcome == OUTCOME_REFUTED
            ):
                break
            records.append(dict(value))
    if not isinstance(recorded, list) or len(records) != len(recorded):
        raise ValueError(
            f"{FINDINGS_FILENAME}: {REJECTED_ADJUSTMENTS_KEY!r} must be a "
            f"list of complete refuted adjustment records"
        )
    return records


def _records_by_adjustment_id(records, ledger_key):
    """Index one provenance list and reject ambiguous duplicate records."""
    indexed = {}
    for index, record in enumerate(records):
        adjustment_id = record["adjustment_id"]
        if adjustment_id in indexed:
            raise ValueError(
                f"{FINDINGS_FILENAME}: {ledger_key!r} contains duplicate "
                f"adjustment id {adjustment_id!r} at position {index}"
            )
        indexed[adjustment_id] = record
    return indexed


def _validate_adjudication_request(request, known_ids):
    """Validate the orchestrator's claims against the committed proposal."""
    if not isinstance(request, dict):
        return ["adjudication request must be a JSON object"], {}, None
    problems = _extra_key_problems(
        request, _REQUEST_KEYS, "adjudication request"
    )
    if not _schema_is(request.get("schema"), ADJUDICATION_SCHEMA):
        problems.append(
            f"adjudication request: 'schema' must be {ADJUDICATION_SCHEMA}"
        )
    revised = request.get(REVISED_ASSESSMENT_KEY)
    if revised is not None and (
        not isinstance(revised, str) or not revised.strip()
    ):
        problems.append(
            "adjudication request: 'revised_assessment' must be null or a "
            "non-empty string"
        )
    normalized_assessment = revised.strip() if isinstance(revised, str) else None

    verified = request.get("verified")
    decisions = {}
    if not isinstance(verified, list):
        problems.append("adjudication request: 'verified' must be a list")
        verified = []
    for index, adjustment_id in enumerate(verified):
        if not isinstance(adjustment_id, str) or not adjustment_id:
            problems.append(
                f"verified[{index}] must be a non-empty string adjustment id"
            )
            continue
        if adjustment_id in decisions:
            problems.append(
                f"verified[{index}]: duplicate adjustment id {adjustment_id!r}"
            )
            continue
        decisions[adjustment_id] = (OUTCOME_VERIFIED, None)

    refuted = request.get("refuted")
    if not isinstance(refuted, list):
        problems.append("adjudication request: 'refuted' must be a list")
        refuted = []
    seen_refuted = set()
    for index, item in enumerate(refuted):
        label = f"refuted[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{label} must be an object")
            continue
        extras = _extra_key_problems(item, _REFUTED_REQUEST_KEYS, label)
        problems.extend(extras)
        adjustment_id = item.get("adjustment_id")
        if not isinstance(adjustment_id, str) or not adjustment_id:
            problems.append(f"{label}: adjustment_id must be a non-empty string")
            continue
        if adjustment_id in seen_refuted:
            problems.append(
                f"{label}: duplicate adjustment id {adjustment_id!r}"
            )
            continue
        seen_refuted.add(adjustment_id)
        reason = item.get("rejection_reason")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(
                f"{label}: rejection_reason must be a non-empty string"
            )
        if adjustment_id in decisions:
            problems.append(
                f"{adjustment_id!r} is both verified and refuted"
            )
            continue
        decisions[adjustment_id] = (OUTCOME_REFUTED, reason)

    for adjustment_id in decisions:
        if adjustment_id not in known_ids:
            problems.append(f"unknown adjustment id {adjustment_id!r}")
    return problems, decisions, normalized_assessment


def _invalidate_assessment(review, recorded_ids):
    """Invalidate prose the applied batch may have just contradicted.

    Called only when a batch actually applied, so an adjudication that
    refuted everything leaves the assessment exactly as the reconciler wrote
    it — nothing changed, nothing to invalidate.

    A ledger with no assessment records no invalidation: there is no text to
    keep auditable, and fabricating an empty entry would claim an invalidation
    that never happened.
    """
    prior = review.get(ASSESSMENT_KEY)
    review[ASSESSMENT_KEY] = None
    if not isinstance(prior, str) or not prior.strip():
        return
    invalidated = review.get(INVALIDATED_ASSESSMENTS_KEY)
    if not isinstance(invalidated, list):
        invalidated = []
    invalidated.append({
        "text": prior,
        # The exact decisions that cost the assessment its standing, so the
        # invalidation can be read back against the batch that caused it.
        "invalidated_by_critic_adjustment_ids": list(recorded_ids),
    })
    review[INVALIDATED_ASSESSMENTS_KEY] = invalidated


def _changed_fields(target, fields):
    return {
        key: value
        for key, value in fields.items()
        if target.get(key) != value
    }


def _validate_pending_mutation(entry, target, label):
    """Validate ledger-dependent action direction and reject no-op patches."""
    action = entry["action"]
    fields = entry.get("fields") or {}
    if action in ("promote", "demote"):
        current = target.get("severity")
        replacement = fields["severity"]
        if replacement == current:
            raise ValueError(
                f"{label}: {action} would not change severity {current!r}"
            )
        current_rank = SEVERITY_RANK[current]
        replacement_rank = SEVERITY_RANK[replacement]
        if action == "promote" and replacement_rank < current_rank:
            raise ValueError(
                f"{label}: promote must increase severity, not change "
                f"{current!r} to {replacement!r}"
            )
        if action == "demote" and replacement_rank > current_rank:
            raise ValueError(
                f"{label}: demote must decrease severity, not change "
                f"{current!r} to {replacement!r}"
            )
        return dict(fields)
    if action not in ("correct", "rescope"):
        return dict(fields)
    changed = _changed_fields(target, fields)
    candidate = copy.deepcopy(target)
    candidate.update(changed)
    if "line" in fields:
        _apply_scope_pairing(candidate, fields["line"] is None)
    if candidate == target:
        raise ValueError(
            f"{label}: {action} would not change the {entry['target']['kind']}"
        )
    return changed


def _apply_proposal(proposal, decisions, revised_assessment, ledger):
    """Apply one adjudicated proposal to one validated ledger, in memory.

    Refuted entries are recorded and skipped; every other entry is applied
    carrying its outcome, because an unchecked critic decision is still a
    critic decision the run has no evidence against.
    """
    findings = ledger["findings"]
    checks = ledger["checks"]
    indexes = {
        TARGET_FINDING: {item["id"]: item for item in findings},
        TARGET_CHECK: {item["id"]: item for item in checks},
    }
    applied_records = list(ledger.get(APPLIED_IDS_KEY) or [])
    rejected_records = list(ledger.get(REJECTED_ADJUSTMENTS_KEY) or [])
    batch_ids = []
    refuted_count = 0
    for index, entry in enumerate(proposal["adjustments"]):
        label = f"adjustment[{index}]"
        outcome, reason = decisions.get(
            entry["adjustment_id"], (OUTCOME_NOT_CHECKED, None)
        )
        if outcome == OUTCOME_REFUTED:
            rejected_records.append({
                "adjustment_id": entry["adjustment_id"],
                "action": entry["action"],
                "target": copy.deepcopy(entry["target"]),
                OUTCOME_KEY: OUTCOME_REFUTED,
                "rejection_reason": reason,
            })
            refuted_count += 1
            continue
        action = entry["action"]
        kind = entry["target"]["kind"]
        provenance = {"action": action, "rationale": entry["rationale"]}
        if action == "add":
            fields = dict(entry.get("fields") or {})
            meta = ledger["meta"]
            finding_id = f"f{meta['next_finding_number']}"
            new_finding = {
                "id": finding_id,
                "category": fields.get("category", "general"),
                "confidence": fields.get("confidence", 0.9),
                "line": fields.get("line"),
                **fields,
                "critic_adjustment": provenance,
            }
            _apply_scope_pairing(new_finding, new_finding.get("line") is None)
            findings.append(new_finding)
            indexes[TARGET_FINDING][finding_id] = new_finding
            meta["next_finding_number"] += 1
        else:
            target = indexes[kind].get(entry["target"]["id"])
            if target is None:
                raise ValueError(
                    f"{label}: no {kind} with id {entry['target']['id']!r} in "
                    f"{FINDINGS_FILENAME}"
                )
            fields = _validate_pending_mutation(entry, target, label)
            if action == "remove":
                removed_key = (
                    "findings_removed_by_critic" if kind == TARGET_FINDING
                    else "checks_removed_by_critic"
                )
                (findings if kind == TARGET_FINDING else checks).remove(target)
                del indexes[kind][target["id"]]
                target["critic_adjustment"] = provenance
                ledger.setdefault(removed_key, []).append(target)
            else:
                provenance["prior"] = {
                    key: target.get(key) for key in fields
                }
                target.update(fields)
                if kind == TARGET_FINDING and "line" in fields:
                    _apply_scope_pairing(target, fields["line"] is None)
                target["critic_adjustment"] = provenance
        applied_records.append({
            "adjustment_id": entry["adjustment_id"], OUTCOME_KEY: outcome,
        })
        batch_ids.append(entry["adjustment_id"])
    if batch_ids:
        derived = _recount_summary(ledger, findings)
        if ledger["verdict"] != derived["verdict"]:
            ledger.setdefault(VERDICT_BEFORE_ADJUSTMENTS_KEY, ledger["verdict"])
            ledger["verdict"] = derived["verdict"]
        ledger[APPLIED_IDS_KEY] = applied_records
        _invalidate_assessment(ledger, batch_ids)
        if revised_assessment:
            ledger[ASSESSMENT_KEY] = revised_assessment
    if refuted_count:
        ledger[REJECTED_ADJUSTMENTS_KEY] = rejected_records
    return len(batch_ids), refuted_count


def _recorded_ids(ledger):
    """Every adjustment id this ledger already accounts for."""
    return {
        record["adjustment_id"]
        for record in (ledger.get(APPLIED_IDS_KEY) or [])
    } | {
        record["adjustment_id"]
        for record in (ledger.get(REJECTED_ADJUSTMENTS_KEY) or [])
    }


def adjudicate(output_dir, request):
    """Record the orchestrator's adjudication by applying it to the ledger once.

    One lock, one ledger write. The proposal file is read and never touched,
    so a second adjudication of the same proposal is refused by the ids the
    first one left in the ledger rather than by a flag somebody has to keep
    in sync.
    """
    with atomic_io.output_dir_lock(output_dir):
        verdict, proposal = read_committed_proposal(output_dir)
        if verdict != REVISE_VERDICT:
            raise ValueError(
                f"cannot adjudicate a critic proposal under a {verdict} verdict"
            )
        known_ids = {
            entry["adjustment_id"] for entry in proposal["adjustments"]
        }
        problems, decisions, revised = _validate_adjudication_request(
            request, known_ids
        )
        if problems:
            raise AdjustmentValidationError(problems)
        read = read_findings_file(os.path.join(output_dir, FINDINGS_FILENAME))
        if read.status != FINDINGS_READ_OK:
            raise ValueError(_unreadable_ledger(read))
        ledger = read.findings
        if known_ids & _recorded_ids(ledger):
            raise ValueError("critic proposal is already adjudicated")
        applied, rejected = _apply_proposal(
            proposal, decisions, revised, ledger
        )
        validate_findings_document(ledger)
        write_findings(output_dir, ledger)
        counts = {
            OUTCOME_VERIFIED: 0, OUTCOME_REFUTED: 0, OUTCOME_NOT_CHECKED: 0,
        }
        for entry in proposal["adjustments"]:
            outcome, _reason = decisions.get(
                entry["adjustment_id"], (OUTCOME_NOT_CHECKED, None)
            )
            counts[outcome] += 1
        return {
            "counts": counts,
            "applied": applied,
            "rejected": rejected,
            "verdict": ledger["verdict"],
        }


def adjudication_state(output_dir):
    """'empty' (no entries), 'pending' (not in the ledger), or 'adjudicated'."""
    _verdict, proposal = read_committed_proposal(output_dir)
    ids = {entry["adjustment_id"] for entry in proposal["adjustments"]}
    if not ids:
        return "empty"
    read = read_findings_file(os.path.join(output_dir, FINDINGS_FILENAME))
    if read.status != FINDINGS_READ_OK:
        raise ValueError(_unreadable_ledger(read))
    return "adjudicated" if ids <= _recorded_ids(read.findings) else "pending"


def main():
    parser = argparse.ArgumentParser(
        description="Adjudicate a committed decision-critic proposal",
        epilog=(
            "Exit codes: 0 = adjudicated; 1 = validation/IO error, with "
            "nothing written."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    adjudicate_parser = subparsers.add_parser(
        "adjudicate",
        help="Apply the orchestrator's adjudication from stdin to the ledger",
    )
    adjudicate_parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        try:
            request = json.load(sys.stdin)
        except json.JSONDecodeError as error:
            raise AdjustmentValidationError([
                f"adjudication request is not valid JSON: {error}"
            ]) from error
        result = adjudicate(args.output_dir, request)
    except AdjustmentValidationError as error:
        for problem in error.problems:
            print(f"REJECTED: {problem}")
        sys.exit(1)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"REJECTED: {error}")
        sys.exit(1)

    counts = result["counts"]
    print(f"RECORDED ADJUDICATION: {sum(counts.values())}")
    print(
        f"VERIFIED: {counts[OUTCOME_VERIFIED]} | "
        f"REFUTED: {counts[OUTCOME_REFUTED]} | "
        f"NOT_CHECKED: {counts[OUTCOME_NOT_CHECKED]}"
    )
    print(
        "REVISED ASSESSMENT: "
        f"{'present' if request.get(REVISED_ASSESSMENT_KEY) else 'absent'}"
    )
    print(f"APPLIED: {result['applied']} | REJECTED: {result['rejected']}")
    print(f"LEDGER VERDICT: {result['verdict']}")


if __name__ == "__main__":
    main()

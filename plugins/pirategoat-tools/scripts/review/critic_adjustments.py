#!/usr/bin/env python3
"""Validate, adjudicate, and apply source-bound decision-critic proposals.

The lifecycle has three owners and one channel per transition. The critic
authors proposal-only fields through ``critic.py --save``; this module assigns
stable adjustment IDs and digest-binds the immutable proposal to the committed
verdict marker. The orchestrator submits only verified IDs, refuted IDs with
reasons, and a revised assessment through :func:`settle`; the script derives the
unchecked complement and persists a complete adjudication checkpoint. Finally,
``_apply_adjustments_locked()`` is the sole post-reconciliation ledger mutator,
carrying provenance, assessment replacement, recounting, and verdict derivation
into ``review-findings.json``.

Settlement builds and validates the complete document-plus-ledger apply plan
before it checkpoints, then deliberately checkpoints before executing the
ledger writes. Both files are atomic individually and the whole transition
holds ``output_dir_lock()``, but there is no pretend cross-file transaction: a
crash after the checkpoint is resumed exactly once by public
:func:`apply_adjustments`. That public entry point is an explicit recovery path;
if an older orchestrator reaches it without a checkpoint, it first prepares an
honest ``defensive_apply`` adjudication with all entries ``not_checked`` and no
invented assessment, validates the same plan, and only then records it.
"""

import argparse
import collections
import copy
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Mapping

try:
    from . import atomic_io
    from .verdict_rules import VALID_SEVERITIES, derive_review_state
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import atomic_io
    from review.verdict_rules import VALID_SEVERITIES, derive_review_state

atomic_write_json = atomic_io.atomic_write_json

ACTIONS = ("promote", "demote", "rescope", "correct", "add", "remove")
# `scope` is deliberately absent: it is derived from `line` to preserve the
# pairing described below, never set by the critic. `id` is absent because
# ids are generated here, never assigned by the critic.
PATCH_FIELDS = ("severity", "title", "description", "recommendation",
                "file", "line", "category", "confidence")
ADD_REQUIRED_FIELDS = ("severity", "title", "file", "description",
                       "recommendation")
# Script-derived per-entry outcomes from the orchestrator's exact settlement
# request. The request names only positive verified/refuted claims; omitted
# committed IDs become SPOT_CHECK_NOT_CHECKED.
SPOT_CHECK_KEY = "spot_check"
SPOT_CHECK_VERIFIED = "verified"
SPOT_CHECK_REFUTED = "refuted"
SPOT_CHECK_NOT_CHECKED = "not_checked"
SPOT_CHECK_VALUES = (
    SPOT_CHECK_VERIFIED, SPOT_CHECK_REFUTED, SPOT_CHECK_NOT_CHECKED,
)

# The orchestrator's post-critic assessment, inside the script-owned
# adjudication checkpoint. An applying batch invalidates the reconciler's
# assessment (see INVALIDATED_ASSESSMENTS_KEY below), and without a replacement
# REVISE run published a ledger whose Assessment section was a pointer to
# prose only a human could read. This is that assessment's machine-readable
# seat: on apply it BECOMES the ledger's assessment, with the invalidation
# record left intact beside it.
REVISED_ASSESSMENT_KEY = "revised_assessment"

ADJUSTMENTS_FILENAME = "decision-critic-adjustments.json"
FINDINGS_FILENAME = "review-findings.json"
CRITIC_VERDICT_FILENAME = "decision-critic-verdict.json"
# One record per adjustment this ledger already contains:
# `{"adjustment_id": ..., "spot_check": ...}`. The id half is the
# idempotence bookkeeping (see apply_adjustments' docstring); the spot_check
# half is the orchestrator's outcome for that decision, so the ledger — the
# artifact bot mode, baselines, and metrics actually read — carries what was
# probed rather than leaving it to the human report.
APPLIED_IDS_KEY = "applied_critic_adjustments"

# Where the ledger's pre-adjustment verdict goes the FIRST time an applying
# batch changes it — the same audit spirit as INVALIDATED_ASSESSMENTS_KEY below.
# First time only: a second round must name what the ledger came in as, not
# what the previous round left behind.
VERDICT_BEFORE_ADJUSTMENTS_KEY = "verdict_before_adjustments"
# The rejection half of the same audit trail: entries the orchestrator's
# spot-check refuted (`rejected: true` + `rejection_reason`) were, before
# this key existed, visible only in decision-critic-adjustments.json — a
# file apply_adjustments() reads but nothing downstream consults — so a
# rejected decision left no trace in review-findings.json, the artifact
# bot mode, baselines, and metrics actually read. This key carries that
# trace now, and the shared Markdown renderer projects it alongside applied
# decisions so the record accounts for every critic decision.
# apply_adjustments() writes one record per newly-settled rejection here;
# see _load_rejected_records() for the read side of the idempotence check.
REJECTED_ADJUSTMENTS_KEY = "rejected_critic_adjustments"

# The only `schema` value ADJUSTMENTS_FILENAME is accepted under.
# decision-reviewer.md's proposal template writes `"schema": 1` alongside
# `"adjustments"`; a doc carrying any other value — or none —
# is out of that template and refused whole, the same all-or-nothing way
# an unknown action or an unaddressable id is: a critic decision written
# against a contract this module does not honor must fail loudly rather
# than being silently accepted and possibly misread.
ADJUSTMENTS_SCHEMA = 1
VERDICT_MARKER_SCHEMA = 1
ADJUDICATION_SCHEMA = 1
VALID_CRITIC_VERDICTS = ("STAND", "REVISE", "ESCALATE", "SKIPPED")
ADJUDICATION_SOURCE_ORCHESTRATOR = "orchestrator"
ADJUDICATION_SOURCE_DEFENSIVE = "defensive_apply"
ADJUDICATION_SOURCES = (
    ADJUDICATION_SOURCE_ORCHESTRATOR,
    ADJUDICATION_SOURCE_DEFENSIVE,
)
ADJUDICATION_KEY = "adjudication"
PROPOSAL_DIGEST_KEY = "proposal_digest"
RECORDED_AT_KEY = "recorded_at"

_PROPOSAL_TOP_LEVEL_KEYS = frozenset({"schema", "adjustments"})
_PROPOSAL_ENTRY_KEYS = frozenset({"action", "id", "fields", "rationale"})
_LIFECYCLE_ENTRY_KEYS = frozenset({
    "adjustment_id",
    SPOT_CHECK_KEY,
    "rejected",
    "rejection_reason",
    "applied",
})
_ADJUDICATION_KEYS = frozenset({
    "schema",
    "source",
    PROPOSAL_DIGEST_KEY,
    RECORDED_AT_KEY,
    REVISED_ASSESSMENT_KEY,
})
_SETTLEMENT_REQUEST_KEYS = frozenset({
    "schema",
    "verified",
    "refuted",
    REVISED_ASSESSMENT_KEY,
})
_REFUTED_REQUEST_KEYS = frozenset({"adjustment_id", "rejection_reason"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Discriminated outcomes of reading the findings ledger off disk, shared
# by every caller that reads it (see `read_findings_file()`). The states
# are separate because each caller maps them differently — the same facts,
# four different right answers — and collapsing any two here would force
# one of those callers to re-derive the distinction inline.
FINDINGS_READ_OK = "ok"
FINDINGS_READ_ABSENT = "absent"
FINDINGS_READ_IO_ERROR = "io_error"
FINDINGS_READ_UNPARSABLE = "unparsable"
FINDINGS_READ_NOT_OBJECT = "not_object"

# What `read_findings_file()` hands back: the state above, the parsed
# object (only on FINDINGS_READ_OK), and the exception that produced a
# failure state (None for OK and for NOT_OBJECT, which is a shape fact
# rather than a raised failure). Carrying the exception is what lets
# `apply_adjustments()` re-raise the ORIGINAL error — a caller that
# depends on `FileNotFoundError` still gets `FileNotFoundError`.
FindingsRead = collections.namedtuple(
    "FindingsRead", ("status", "findings", "error")
)
ApplyPlan = collections.namedtuple(
    "ApplyPlan",
    (
        "document", "findings", "findings_changed", "document_changed",
        "result",
    ),
)

# Where an invalidated `assessment` goes. The adjustment vocabulary addresses
# findings — severity, title, scope, the fields of one finding —
# and deliberately cannot address ledger-level prose. That leaves the
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

# The one verdict that sanctions applying adjustments. Everything else —
# STAND, ESCALATE, an unrecognized string, a missing file — refuses.
# Literal, not imported: critic.py's CRITIC_VERDICTS is the canonical
# vocabulary source and names this constant as one of its aligned sites
# (see the comment there) — importing critic.py here just to reuse one
# string would wire this module's gate to critic.py's whole import graph
# for no benefit, since the value the gate checks against is a single
# fixed literal, not a set that varies.
REVISE_VERDICT = "REVISE"

# Refusal reasons returned by apply_adjustments() under the gate. A missing,
# malformed, schema-invalid, or unbound snapshot collapses to the same reason:
# from the gate's perspective there is simply no usable verdict to act on.
REFUSAL_NO_VERDICT = "no_verdict"
REFUSAL_VERDICT_NOT_REVISE = "verdict_not_revise"

# Distinct from 0 (success) and 1 (validation/IO error, see main()) so a
# caller can tell "refused by the authority gate" apart from either.
REFUSAL_EXIT_CODE = 3


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


def _validate_proposal_entry(entry, label, *, require_adjustment_id):
    """Validate the immutable proposal half of one lifecycle entry."""
    if not isinstance(entry, dict):
        return [f"{label} must be an object"]

    allowed = set(_PROPOSAL_ENTRY_KEYS)
    if require_adjustment_id:
        allowed.update(_LIFECYCLE_ENTRY_KEYS)
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

    fields = entry.get("fields")
    if fields is None and action == "remove":
        fields = {}
    try:
        _validate_fields(fields, label)
    except ValueError as error:
        problems.append(str(error))
    else:
        if action in ("promote", "demote") and set(fields) != {"severity"}:
            problems.append(
                f"{label}: {action} requires exactly the severity field"
            )
        elif action == "rescope" and set(fields) != {"line"}:
            problems.append(
                f"{label}: rescope requires exactly the line field"
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

    target_id = entry.get("id")
    if action == "add":
        if target_id is not None:
            problems.append(
                f"{label}: ids are generated, not assigned — the critic "
                f"must not invent ids"
            )
    elif action in ACTIONS and (
        not isinstance(target_id, str) or not target_id
    ):
        problems.append(f"{label}: 'id' must be a non-empty target id")

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
        target_id = entry.get("id")
        if (
            action not in ACTIONS
            or action == "add"
            or not isinstance(target_id, str)
            or not target_id
        ):
            continue
        if target_id in seen_targets:
            first_index, first_action = seen_targets[target_id]
            if first_action == "remove":
                problems.append(
                    f"{label}: id {target_id!r} is removed by "
                    f"adjustment[{first_index}] in this batch"
                )
            else:
                problems.append(
                    f"{label}: duplicate target {target_id!r} — merge "
                    f"finding-level changes into one entry per finding"
                )
        else:
            seen_targets[target_id] = (index, action)
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


def immutable_proposal_projection(document: Mapping[str, object]):
    """Return only the proposal facts committed by the verdict marker."""
    adjustments = document.get("adjustments") if isinstance(document, dict) else None
    projected = []
    if isinstance(adjustments, list):
        for entry in adjustments:
            if not isinstance(entry, dict):
                projected.append(entry)
                continue
            projected.append({
                key: copy.deepcopy(entry[key])
                for key in (
                    "adjustment_id", "action", "id", "fields", "rationale"
                )
                if key in entry
            })
    return {
        "schema": document.get("schema") if isinstance(document, dict) else None,
        "adjustments": projected,
    }


def proposal_digest(document: Mapping[str, object]) -> str:
    immutable = immutable_proposal_projection(document)
    encoded = json.dumps(
        immutable,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_recorded_at(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_adjustments_document(payload):
    """Validate a persisted proposal/adjudication/application document."""
    if not isinstance(payload, dict):
        return [f"{ADJUSTMENTS_FILENAME} must be a JSON object"]
    allowed_top = set(_PROPOSAL_TOP_LEVEL_KEYS) | {ADJUDICATION_KEY}
    problems = _extra_key_problems(payload, allowed_top, ADJUSTMENTS_FILENAME)
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
    adjudication_present = ADJUDICATION_KEY in payload
    adjudication = payload.get(ADJUDICATION_KEY)
    if adjudication_present and not isinstance(adjudication, dict):
        problems.append(f"{ADJUDICATION_KEY!r} must be an object")
        return problems
    is_settled = adjudication_present
    for index, entry in enumerate(adjustments):
        label = f"adjustment[{index}]"
        if not isinstance(entry, dict):
            continue

        lifecycle_keys = set(entry) & _LIFECYCLE_ENTRY_KEYS
        lifecycle_keys.discard("adjustment_id")
        if not is_settled and lifecycle_keys:
            for key in sorted(lifecycle_keys):
                problems.append(
                    f"{label}: {key!r} requires a committed adjudication"
                )
            continue
        if not is_settled:
            continue

        spot_check = entry.get(SPOT_CHECK_KEY)
        if spot_check not in SPOT_CHECK_VALUES:
            problems.append(
                f"{label}: {SPOT_CHECK_KEY!r} must be one of "
                f"{', '.join(SPOT_CHECK_VALUES)}"
            )
        rejected_present = "rejected" in entry
        reason_present = "rejection_reason" in entry
        if spot_check == SPOT_CHECK_REFUTED:
            if entry.get("rejected") is not True:
                problems.append(f"{label}: refuted entries require rejected: true")
            reason = entry.get("rejection_reason")
            if not isinstance(reason, str) or not reason.strip():
                problems.append(
                    f"{label}: refuted entries require a non-empty "
                    f"'rejection_reason'"
                )
            if "applied" in entry:
                problems.append(f"{label}: a refuted entry cannot be applied")
        else:
            if rejected_present:
                problems.append(
                    f"{label}: rejected is present only on refuted entries"
                )
            if reason_present:
                problems.append(
                    f"{label}: rejection_reason is present only on refuted entries"
                )
            if "applied" in entry and entry.get("applied") is not True:
                problems.append(f"{label}: applied may only be true when present")

    if not is_settled:
        return problems
    problems.extend(_extra_key_problems(
        adjudication, _ADJUDICATION_KEYS, ADJUDICATION_KEY
    ))
    if not _schema_is(adjudication.get("schema"), ADJUDICATION_SCHEMA):
        problems.append(
            f"{ADJUDICATION_KEY}: 'schema' must be {ADJUDICATION_SCHEMA}"
        )
    source = adjudication.get("source")
    if source not in ADJUDICATION_SOURCES:
        problems.append(
            f"{ADJUDICATION_KEY}: unknown source {source!r}"
        )
    stored_digest = adjudication.get(PROPOSAL_DIGEST_KEY)
    if not isinstance(stored_digest, str) or not _SHA256_RE.fullmatch(
        stored_digest
    ):
        problems.append(
            f"{ADJUDICATION_KEY}: {PROPOSAL_DIGEST_KEY!r} must be a sha256"
        )
    elif stored_digest != proposal_digest(payload):
        problems.append(
            f"{ADJUDICATION_KEY}: proposal digest does not match the proposal"
        )
    if not _valid_recorded_at(adjudication.get(RECORDED_AT_KEY)):
        problems.append(
            f"{ADJUDICATION_KEY}: {RECORDED_AT_KEY!r} must be an aware "
            f"RFC 3339 timestamp"
        )
    revised = adjudication.get(REVISED_ASSESSMENT_KEY)
    if source == ADJUDICATION_SOURCE_ORCHESTRATOR:
        if not isinstance(revised, str) or not revised.strip():
            problems.append(
                f"{ADJUDICATION_KEY}: orchestrator revised_assessment must "
                f"be a non-empty string"
            )
    elif source == ADJUDICATION_SOURCE_DEFENSIVE and revised is not None:
        problems.append(
            f"{ADJUDICATION_KEY}: defensive_apply revised_assessment must be null"
        )
    if source == ADJUDICATION_SOURCE_DEFENSIVE and any(
        isinstance(entry, dict)
        and entry.get(SPOT_CHECK_KEY) != SPOT_CHECK_NOT_CHECKED
        for entry in adjustments
    ):
        problems.append(
            f"{ADJUDICATION_KEY}: defensive_apply entries must all be not_checked"
        )
    return problems


def write_adjustments(output_dir, document):
    """The single atomic writer for decision-critic-adjustments.json."""
    problems = validate_adjustments_document(document)
    if problems:
        raise AdjustmentValidationError(problems)
    atomic_write_json(os.path.join(output_dir, ADJUSTMENTS_FILENAME), document)


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
        {"schema", "verdict", PROPOSAL_DIGEST_KEY},
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
    digest = marker.get(PROPOSAL_DIGEST_KEY)
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        problems.append(
            f"{CRITIC_VERDICT_FILENAME}: {PROPOSAL_DIGEST_KEY!r} must be a sha256"
        )
    return problems


def _load_committed_snapshot(output_dir):
    marker = _read_json_object(
        os.path.join(output_dir, CRITIC_VERDICT_FILENAME),
        CRITIC_VERDICT_FILENAME,
    )
    marker_problems = _validate_verdict_marker(marker)
    if marker_problems:
        raise AdjustmentValidationError(marker_problems)
    document = _read_json_object(
        os.path.join(output_dir, ADJUSTMENTS_FILENAME), ADJUSTMENTS_FILENAME
    )
    digest = proposal_digest(document)
    if marker[PROPOSAL_DIGEST_KEY] != digest:
        raise ValueError(
            f"proposal digest mismatch: {CRITIC_VERDICT_FILENAME} commits "
            f"{marker[PROPOSAL_DIGEST_KEY]}, current proposal is {digest}"
        )
    document_problems = validate_adjustments_document(document)
    if document_problems:
        raise AdjustmentValidationError(document_problems)
    if marker["verdict"] != REVISE_VERDICT and document["adjustments"]:
        raise ValueError(
            f"{marker['verdict']} may not commit a non-empty proposal"
        )
    return marker, document


def read_verdict_file(path):
    """Return a critic verdict only from a complete digest-bound snapshot."""
    output_dir = os.path.dirname(path) or "."
    try:
        marker, _document = _load_committed_snapshot(output_dir)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return marker["verdict"]


def read_critic_verdict(output_dir):
    """Read the verdict from a complete source-bound critic snapshot.

    Returns the validated verdict, or ``None`` when the marker or adjacent
    proposal is absent, malformed, schema-invalid, or digest-mismatched. The
    presentation mapping downstream consumers need instead — ``SKIPPED`` and
    an unusable snapshot both reading as ``unavailable`` — lives next door in
    :func:`critic_verdict_for_state`.
    """
    return read_verdict_file(os.path.join(output_dir, CRITIC_VERDICT_FILENAME))


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
    return "unavailable" if verdict in (None, "SKIPPED") else verdict


def read_findings_file(path):
    """Read review-findings.json into a discriminated result.

    ONE spelling of open-parse-shape-check for the ledger, the same move
    `read_verdict_file()` made for the verdict files one task ago: the
    call sites used to open this file with slightly different guards (the
    best-effort id reader, `apply_adjustments()`, and orchestration's own
    reads), and the differences between them were accidents rather
    than decisions — one of them even opened the file with the platform's
    locale encoding while the others pinned UTF-8.

    Returns a `FindingsRead`. What each caller does with a given state is
    deliberately NOT decided here — the facts are shared, the policy is
    not: the best-effort reader treats every failure as "nothing
    recorded", the applier re-raises, and step 11's verdict derivation
    treats anything but OK as "no usable ledger verdict" and falls back.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            findings = json.load(f)
    except FileNotFoundError as err:
        return FindingsRead(FINDINGS_READ_ABSENT, None, err)
    except OSError as err:
        return FindingsRead(FINDINGS_READ_IO_ERROR, None, err)
    except json.JSONDecodeError as err:
        return FindingsRead(FINDINGS_READ_UNPARSABLE, None, err)
    if not isinstance(findings, dict):
        return FindingsRead(FINDINGS_READ_NOT_OBJECT, None, None)
    return FindingsRead(FINDINGS_READ_OK, findings, None)


def write_findings(output_dir, findings):
    """The ONE sanctioned write path for review-findings.json.

    Replaces the file atomically through the shared `atomic_write_json`.

    Addressed by output directory, not by path, like every other public
    entry point in this module (`read_critic_verdict`, `pending_count`,
    `apply_adjustments`). The filename is this module's constant, so a
    caller cannot point the sanctioned writer at the wrong file — which
    matters most for the one writer the pipeline cannot check, the
    review-reconciliator agent following a taught snippet.

    Two writers exist across a run and both call this: the
    review-reconciliator agent's first write (taught in
    `agents/review-reconciliator.md`, via `findings_save.py`) and
    `apply_adjustments()` below. One writer means one place
    where the ledger's atomicity and its filename are decided, and it is
    the rule a third writer would break: every change to this file goes
    through the adjustments channel, never a hand edit.
    """
    if not isinstance(findings, dict):
        raise ValueError(f"{FINDINGS_FILENAME} must be a JSON object")
    atomic_write_json(os.path.join(output_dir, FINDINGS_FILENAME), findings)


def _validate_fields(fields, entry_label):
    """Reject anything outside the adjustable field vocabulary."""
    if not isinstance(fields, dict):
        raise ValueError(f"{entry_label}: 'fields' must be an object")
    for key, value in fields.items():
        if key not in PATCH_FIELDS:
            raise ValueError(
                f"{entry_label}: field {key!r} is not adjustable "
                f"(allowed: {', '.join(PATCH_FIELDS)})"
            )
        if key == "severity" and value not in VALID_SEVERITIES:
            raise ValueError(
                f"{entry_label}: invalid severity {value!r} "
                f"(allowed: {', '.join(VALID_SEVERITIES)})"
            )
        if key == "line" and value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
            or value <= 0
        ):
            raise ValueError(
                f"{entry_label}: line must be a positive (1-indexed) "
                f"integer or null, got {value!r}"
            )


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

    Returns the complete shared derivation so the caller publishes its gating
    verdict without choosing a population or applying thresholds itself.
    """
    try:
        derived = derive_review_state(findings)
    except ValueError as error:
        raise ValueError(f"{FINDINGS_FILENAME}: {error}") from error
    summary = review.setdefault("summary", {})
    summary["total_findings"] = len(findings)
    summary["by_severity"] = derived["counts"]
    summary.pop("verdict_without_advisory", None)
    summary.update(derived["advisory"])
    return derived


def _applied_record(value):
    """Normalize one `applied_critic_adjustments` entry to its record shape.

    Returns `{"adjustment_id": ..., "spot_check": ...}`, or None when the
    entry is neither shape this key has ever held.

    The bare-string shape is what the key held before it grew a spot_check
    half, inside the same unreleased window. It is accepted on READ so a run
    directory carrying such a ledger keeps its idempotence — the id is what
    that bookkeeping turns on, and re-applying a landed batch would let
    `prior` report the critic's own output as the reconciled state. It is
    never WRITTEN: every record this module emits carries both halves, with
    the un-recorded spot_check reading as the honest SPOT_CHECK_NOT_CHECKED.
    """
    if isinstance(value, str) and value:
        return {
            "adjustment_id": value, SPOT_CHECK_KEY: SPOT_CHECK_NOT_CHECKED,
        }
    if (
        isinstance(value, dict)
        and isinstance(value.get("adjustment_id"), str)
        and value["adjustment_id"]
        and set(value) == {"adjustment_id", SPOT_CHECK_KEY}
        and value.get(SPOT_CHECK_KEY) in SPOT_CHECK_VALUES
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
    """Read the rejection audit records the findings file already contains.

    Schema-1 records written before spot-check provenance omit ``spot_check``;
    the rejection bucket itself means ``refuted``, so normalize that one
    historical omission while rejecting malformed record shapes.
    """
    recorded = findings.get(REJECTED_ADJUSTMENTS_KEY)
    if recorded is None:
        return []
    required = {
        "adjustment_id", "action", "target_id", "rejection_reason",
    }
    records = []
    if isinstance(recorded, list):
        for value in recorded:
            if not isinstance(value, dict) or not required <= set(value):
                break
            adjustment_id = value.get("adjustment_id")
            action = value.get("action")
            target_id = value.get("target_id")
            reason = value.get("rejection_reason")
            spot_check = value.get(SPOT_CHECK_KEY, SPOT_CHECK_REFUTED)
            if not (
                isinstance(adjustment_id, str)
                and adjustment_id
                and (action is None or isinstance(action, str))
                and (target_id is None or isinstance(target_id, str))
                and isinstance(reason, str)
                and reason.strip()
                and spot_check == SPOT_CHECK_REFUTED
            ):
                break
            records.append({**value, SPOT_CHECK_KEY: SPOT_CHECK_REFUTED})
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


# Read-only entry states used by ``pending_count()``. Applied provenance is
# authoritative: a mutable flag alone stays pending, while a ledger record with
# no flag is the crash-recovery catch-up case.
_SETTLED = "settled"      # provenance plus flag, or a refuted checkpoint
_CATCH_UP = "catch_up"    # findings provenance landed, flag write did not
_PENDING = "pending"      # not yet recorded in the authoritative ledger


def _entry_state(entry, already_recorded):
    """Classify one adjustment against the record kept on both sides."""
    if entry.get("adjustment_id") in already_recorded:
        if entry.get("applied") is True:
            return _SETTLED
        return _CATCH_UP
    if entry.get("rejected") is True:
        return _SETTLED
    return _PENDING


def _recorded_ids_best_effort(output_dir):
    """Read the already-applied ids without insisting they are well-formed.

    apply_adjustments raises on a malformed record because it is about to
    write against it. This read-only path instead falls back to "nothing
    recorded", which can only over-count pending entries — the direction
    that surfaces a suspicious file rather than hiding one.
    """
    read = read_findings_file(os.path.join(output_dir, FINDINGS_FILENAME))
    if read.status != FINDINGS_READ_OK:
        # Every failure state collapses to "nothing recorded" here — this
        # path only ever over-counts pending entries, the direction that
        # surfaces a suspicious file rather than hiding one.
        return set()
    findings = read.findings
    recorded = findings.get(APPLIED_IDS_KEY)
    if not isinstance(recorded, list):
        return set()
    records = (_applied_record(value) for value in recorded)
    return {record["adjustment_id"] for record in records if record}


def settlement_counts(document):
    """Derive settlement counts from per-entry facts; never trust a caller."""
    counts = {
        SPOT_CHECK_VERIFIED: 0,
        SPOT_CHECK_REFUTED: 0,
        SPOT_CHECK_NOT_CHECKED: 0,
    }
    adjustments = document.get("adjustments") if isinstance(document, dict) else []
    for entry in adjustments if isinstance(adjustments, list) else []:
        if isinstance(entry, dict) and entry.get(SPOT_CHECK_KEY) in counts:
            counts[entry[SPOT_CHECK_KEY]] += 1
    return counts


def _validate_settlement_request(request, known_ids):
    if not isinstance(request, dict):
        return ["adjudication request must be a JSON object"], {}
    problems = _extra_key_problems(
        request, _SETTLEMENT_REQUEST_KEYS, "adjudication request"
    )
    if not _schema_is(request.get("schema"), ADJUDICATION_SCHEMA):
        problems.append(
            f"adjudication request: 'schema' must be {ADJUDICATION_SCHEMA}"
        )
    revised = request.get(REVISED_ASSESSMENT_KEY)
    if not isinstance(revised, str) or not revised.strip():
        problems.append(
            "adjudication request: 'revised_assessment' must be a non-empty "
            "string"
        )

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
        decisions[adjustment_id] = (SPOT_CHECK_VERIFIED, None)

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
        decisions[adjustment_id] = (SPOT_CHECK_REFUTED, reason)

    for adjustment_id in decisions:
        if adjustment_id not in known_ids:
            problems.append(f"unknown adjustment id {adjustment_id!r}")
    return problems, decisions


def _build_adjudication_checkpoint(document, decisions, assessment, *, source):
    checkpoint = copy.deepcopy(document)
    digest = proposal_digest(checkpoint)
    for entry in checkpoint["adjustments"]:
        adjustment_id = entry["adjustment_id"]
        outcome, reason = decisions.get(
            adjustment_id, (SPOT_CHECK_NOT_CHECKED, None)
        )
        entry[SPOT_CHECK_KEY] = outcome
        if outcome == SPOT_CHECK_REFUTED:
            entry["rejected"] = True
            entry["rejection_reason"] = reason
    checkpoint[ADJUDICATION_KEY] = {
        "schema": ADJUDICATION_SCHEMA,
        "source": source,
        PROPOSAL_DIGEST_KEY: digest,
        RECORDED_AT_KEY: datetime.now(timezone.utc).isoformat(),
        REVISED_ASSESSMENT_KEY: assessment,
    }
    return checkpoint


def _checkpoint_matches_request(document, decisions, assessment):
    adjudication = document.get(ADJUDICATION_KEY)
    if not isinstance(adjudication, dict):
        return False
    if adjudication.get("source") != ADJUDICATION_SOURCE_ORCHESTRATOR:
        return False
    if adjudication.get(REVISED_ASSESSMENT_KEY) != assessment:
        return False
    for entry in document["adjustments"]:
        expected, reason = decisions.get(
            entry["adjustment_id"], (SPOT_CHECK_NOT_CHECKED, None)
        )
        if entry.get(SPOT_CHECK_KEY) != expected:
            return False
        if expected == SPOT_CHECK_REFUTED:
            if entry.get("rejected") is not True:
                return False
            if entry.get("rejection_reason") != reason:
                return False
        elif "rejected" in entry or "rejection_reason" in entry:
            return False
    return True


def settle(output_dir, request):
    """Validate, checkpoint, and apply orchestrator adjudication under one lock."""
    with atomic_io.output_dir_lock(output_dir):
        marker, document = _load_committed_snapshot(output_dir)
        if marker["verdict"] != REVISE_VERDICT:
            raise ValueError(
                f"cannot settle critic proposal under {marker['verdict']} verdict"
            )
        known_ids = {
            entry["adjustment_id"] for entry in document["adjustments"]
        }
        problems, decisions = _validate_settlement_request(request, known_ids)
        if problems:
            raise AdjustmentValidationError(problems)
        assessment = request[REVISED_ASSESSMENT_KEY]
        existing = document.get(ADJUDICATION_KEY)
        if existing is not None:
            if not _checkpoint_matches_request(document, decisions, assessment):
                raise ValueError(
                    "critic proposal is already settled with a different "
                    "adjudication request"
                )
            settlement_status = "already_settled"
        else:
            document = _build_adjudication_checkpoint(
                document,
                decisions,
                assessment,
                source=ADJUDICATION_SOURCE_ORCHESTRATOR,
            )
            settlement_status = "settled"

        findings = _read_findings_for_apply(output_dir)
        plan = _build_apply_plan(document, findings)
        if settlement_status == "settled":
            write_adjustments(output_dir, document)
        counts = settlement_counts(document)
        apply_result = _apply_adjustments_locked(output_dir, plan)
        return {
            "status": settlement_status,
            "counts": counts,
            PROPOSAL_DIGEST_KEY: proposal_digest(document),
            "apply": apply_result,
        }


def pending_count(output_dir):
    """Count adjustments that have not landed yet, without applying any.

    The step-11 gate needs to tell a file that still wants to change the
    ledger from one whose entries have all already landed: only the
    former is suspicious under a non-REVISE verdict, since re-entering
    step 11 after a legitimate apply leaves a fully-flagged file behind.
    Answering that by calling apply_adjustments would be the bypass the
    gate exists to close, so this shares the predicate instead of the
    write path.
    """
    adjustments_path = os.path.join(output_dir, ADJUSTMENTS_FILENAME)
    if not os.path.isfile(adjustments_path):
        return 0
    _marker, doc = _load_committed_snapshot(output_dir)
    adjustments = doc["adjustments"]
    already_recorded = _recorded_ids_best_effort(output_dir)
    count = 0
    for entry in adjustments:
        # apply_adjustments rejects a non-object entry loudly; for the
        # read-only question "is anything here unlanded?" it certainly is.
        if not isinstance(entry, dict):
            count += 1
        elif _entry_state(entry, already_recorded) == _PENDING:
            count += 1
    return count


def _invalidate_assessment(review, recorded_ids):
    """Invalidate prose the applied batch may have just contradicted.

    Called only when a batch actually applied, so a refused, settled, or
    fully rejected call leaves the assessment exactly as the reconciler
    wrote it — nothing changed, nothing to invalidate.

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
        "invalidated_by_adjustment_ids": list(recorded_ids),
    })
    review[INVALIDATED_ASSESSMENTS_KEY] = invalidated


def _read_findings_for_apply(output_dir):
    """Read the ledger for a plan, preserving its existing error contract."""
    read = read_findings_file(os.path.join(output_dir, FINDINGS_FILENAME))
    if read.status == FINDINGS_READ_NOT_OBJECT:
        raise ValueError(f"{FINDINGS_FILENAME} must be a JSON object")
    if read.status != FINDINGS_READ_OK:
        raise read.error
    return read.findings


def _new_finding_id(adjustment_id, occupied_ids):
    """Derive a stable 8-hex finding id from the script-assigned decision id."""
    candidate = adjustment_id[:8]
    if re.fullmatch(r"[0-9a-f]{8}", candidate) and candidate not in occupied_ids:
        return candidate
    counter = 0
    while True:
        encoded = f"{adjustment_id}:{counter}".encode("utf-8")
        candidate = hashlib.sha256(encoded).hexdigest()[:8]
        if candidate not in occupied_ids:
            return candidate
        counter += 1


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
        current_rank = VALID_SEVERITIES.index(current)
        replacement_rank = VALID_SEVERITIES.index(replacement)
        if action == "promote" and replacement_rank > current_rank:
            raise ValueError(
                f"{label}: promote must increase severity, not change "
                f"{current!r} to {replacement!r}"
            )
        if action == "demote" and replacement_rank < current_rank:
            raise ValueError(
                f"{label}: demote must decrease severity, not change "
                f"{current!r} to {replacement!r}"
            )
        return
    if action not in ("correct", "rescope"):
        return
    candidate = copy.deepcopy(target)
    candidate.update(fields)
    if "line" in fields:
        _apply_scope_pairing(candidate, fields["line"] is None)
    if candidate == target:
        raise ValueError(
            f"{label}: {action} would not change the finding"
        )


def _validate_unlanded_entry(entry, by_id, label):
    """Resolve and validate one proposal whose outcome is not in the ledger."""
    if entry["action"] == "add":
        return
    target_id = entry["id"]
    target = by_id.get(target_id)
    if target is None:
        raise ValueError(
            f"{label}: no finding with id {target_id!r} in {FINDINGS_FILENAME}"
        )
    _validate_pending_mutation(entry, target, label)


def _validate_rejection_provenance(entry, record, label):
    expected = {
        "action": entry["action"],
        "target_id": entry.get("id"),
        SPOT_CHECK_KEY: SPOT_CHECK_REFUTED,
        "rejection_reason": entry["rejection_reason"],
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError(
            f"{label}: rejection provenance does not match the adjudication "
            f"checkpoint"
        )


def _build_apply_plan(document, findings):
    """Purely validate and project one settled document against one ledger."""
    problems = validate_adjustments_document(document)
    if problems:
        raise AdjustmentValidationError(problems)
    adjustments = document["adjustments"]
    if not adjustments:
        return ApplyPlan(
            copy.deepcopy(document), copy.deepcopy(findings), False, False,
            {
                "status": "no_adjustments",
                "applied": 0,
                "rejected": 0,
                "adjudication_source": None,
                "counts": {
                    SPOT_CHECK_VERIFIED: 0,
                    SPOT_CHECK_REFUTED: 0,
                    SPOT_CHECK_NOT_CHECKED: 0,
                },
            },
        )
    adjudication = document[ADJUDICATION_KEY]
    planned_document = copy.deepcopy(document)
    planned_findings = copy.deepcopy(findings)
    ledger_findings = planned_findings.get("findings")
    if not isinstance(ledger_findings, list):
        raise ValueError(f"{FINDINGS_FILENAME} has no findings list")
    if not isinstance(planned_findings.get("summary"), dict):
        raise ValueError(f"{FINDINGS_FILENAME} has no summary object")
    try:
        derive_review_state(ledger_findings)
    except ValueError as error:
        raise ValueError(f"{FINDINGS_FILENAME}: {error}") from error

    by_id = {}
    for index, finding in enumerate(ledger_findings):
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or not finding_id:
            continue
        if finding_id in by_id:
            raise ValueError(
                f"{FINDINGS_FILENAME}: duplicate finding id {finding_id!r} "
                f"at position {index}"
            )
        by_id[finding_id] = finding

    applied_records = _load_recorded_records(planned_findings)
    rejected_records = _load_rejected_records(planned_findings)
    applied_by_id = _records_by_adjustment_id(
        applied_records, APPLIED_IDS_KEY
    )
    rejected_by_id = _records_by_adjustment_id(
        rejected_records, REJECTED_ADJUSTMENTS_KEY
    )
    contradictory_ids = sorted(set(applied_by_id) & set(rejected_by_id))
    if contradictory_ids:
        raise ValueError(
            f"{FINDINGS_FILENAME}: adjustment ids are both applied and "
            f"rejected: {', '.join(contradictory_ids)}"
        )

    pending = []
    catch_up = []
    newly_rejected = []
    for index, entry in enumerate(planned_document["adjustments"]):
        label = f"adjustment[{index}]"
        adjustment_id = entry["adjustment_id"]
        applied_record = applied_by_id.get(adjustment_id)
        rejected_record = rejected_by_id.get(adjustment_id)
        spot_check = entry[SPOT_CHECK_KEY]

        if spot_check == SPOT_CHECK_REFUTED:
            if applied_record is not None:
                raise ValueError(
                    f"{label}: adjudication refutes {adjustment_id!r}, but "
                    f"the ledger records it as applied"
                )
            if rejected_record is None:
                newly_rejected.append(entry)
            else:
                _validate_rejection_provenance(entry, rejected_record, label)
            continue

        if rejected_record is not None:
            raise ValueError(
                f"{label}: adjudication marks {adjustment_id!r} as "
                f"{spot_check}, but the ledger records it as rejected"
            )
        if entry.get("applied") is True and applied_record is None:
            raise ValueError(
                f"{label}: applied flag has no matching applied ledger record"
            )
        if applied_record is not None:
            if applied_record[SPOT_CHECK_KEY] != spot_check:
                raise ValueError(
                    f"{label}: applied ledger spot_check "
                    f"{applied_record[SPOT_CHECK_KEY]!r} does not match "
                    f"checkpoint {spot_check!r}"
                )
            if entry.get("applied") is not True:
                catch_up.append(entry)
            continue

        action = entry["action"]
        fields = entry.get("fields") or {}
        _validate_unlanded_entry(entry, by_id, label)
        pending.append((entry, action, fields))

    rejection_records = [
        {
            "adjustment_id": entry["adjustment_id"],
            "action": entry["action"],
            "target_id": entry.get("id"),
            SPOT_CHECK_KEY: SPOT_CHECK_REFUTED,
            "rejection_reason": entry["rejection_reason"],
        }
        for entry in newly_rejected
    ]

    applied = 0
    batch_ids = []
    for entry, action, fields in pending:
        provenance = {
            "action": action,
            "rationale": entry["rationale"],
        }
        if action == "add":
            new_finding = {
                "id": _new_finding_id(entry["adjustment_id"], set(by_id)),
                "category": fields.get("category", "general"),
                "confidence": fields.get("confidence", 0.9),
                "line": fields.get("line"),
                **fields,
                "critic_adjustment": provenance,
            }
            _apply_scope_pairing(new_finding, new_finding.get("line") is None)
            ledger_findings.append(new_finding)
            by_id[new_finding["id"]] = new_finding
        elif action == "remove":
            removed = planned_findings.get("findings_removed_by_critic")
            if removed is not None and not isinstance(removed, list):
                raise ValueError(
                    f"{FINDINGS_FILENAME}: 'findings_removed_by_critic' "
                    "must be a list"
                )
            target = by_id.pop(entry["id"])
            ledger_findings.remove(target)
            target["critic_adjustment"] = provenance
            planned_findings.setdefault(
                "findings_removed_by_critic", []
            ).append(target)
        else:
            target = by_id[entry["id"]]
            provenance["prior"] = {key: target.get(key) for key in fields}
            target.update(fields)
            if "line" in fields:
                _apply_scope_pairing(target, fields["line"] is None)
            target["critic_adjustment"] = provenance
        applied_records.append({
            "adjustment_id": entry["adjustment_id"],
            SPOT_CHECK_KEY: entry[SPOT_CHECK_KEY],
        })
        batch_ids.append(entry["adjustment_id"])
        applied += 1

    if applied:
        derived = _recount_summary(planned_findings, ledger_findings)
        recomputed = derived["verdict"]
        if planned_findings.get("verdict") != recomputed:
            planned_findings.setdefault(
                VERDICT_BEFORE_ADJUSTMENTS_KEY,
                planned_findings.get("verdict"),
            )
            planned_findings["verdict"] = recomputed
        planned_findings[APPLIED_IDS_KEY] = applied_records
        _invalidate_assessment(planned_findings, batch_ids)
        revised = adjudication.get(REVISED_ASSESSMENT_KEY)
        if isinstance(revised, str) and revised.strip():
            planned_findings[ASSESSMENT_KEY] = revised
    if rejection_records:
        planned_findings[REJECTED_ADJUSTMENTS_KEY] = (
            rejected_records + rejection_records
        )

    if applied or catch_up:
        for entry, _action, _fields in pending:
            entry["applied"] = True
        for entry in catch_up:
            entry["applied"] = True
    final_problems = validate_adjustments_document(planned_document)
    if final_problems:
        raise AdjustmentValidationError(final_problems)

    return ApplyPlan(
        planned_document,
        planned_findings,
        bool(applied or rejection_records),
        bool(applied or catch_up),
        {
            "status": "applied" if applied else "nothing_pending",
            "applied": applied,
            "rejected": len(rejection_records),
            "adjudication_source": adjudication["source"],
            "counts": settlement_counts(planned_document),
        },
    )


def _apply_adjustments_locked(output_dir, plan=None):
    """Execute one prevalidated plan while the caller holds the lock.

    When no plan is supplied (the explicit recovery path), this function
    reads the committed checkpoint and builds one first. ``settle()`` builds
    the plan before checkpointing and supplies it here, so every failure after
    that crash boundary is an atomic file-write failure rather than a
    deterministic validation rejection. This remains the sole
    post-reconciliation ledger mutator.
    """
    if plan is None:
        marker, document = _load_committed_snapshot(output_dir)
        if marker["verdict"] != REVISE_VERDICT:
            return {
                "status": "refused",
                "applied": 0,
                "reason": (
                    f"{REFUSAL_VERDICT_NOT_REVISE} ({marker['verdict']})"
                ),
            }
        findings = _read_findings_for_apply(output_dir)
        plan = _build_apply_plan(document, findings)
    if plan.findings_changed:
        write_findings(output_dir, plan.findings)
    if plan.document_changed:
        write_adjustments(output_dir, plan.document)
    return plan.result


def _read_marker_for_gate(output_dir):
    """Read only enough marker state to preserve the public refusal API."""
    path = os.path.join(output_dir, CRITIC_VERDICT_FILENAME)
    try:
        marker = _read_json_object(path, CRITIC_VERDICT_FILENAME)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if _validate_verdict_marker(marker):
        return None
    return marker


def apply_adjustments(output_dir):
    """Explicit recovery path; checkpoint missing adjudication honestly."""
    with atomic_io.output_dir_lock(output_dir):
        marker = _read_marker_for_gate(output_dir)
        if marker is None:
            return {
                "status": "refused",
                "applied": 0,
                "reason": REFUSAL_NO_VERDICT,
            }
        verdict = marker["verdict"]
        if verdict != REVISE_VERDICT:
            return {
                "status": "refused",
                "applied": 0,
                "reason": f"{REFUSAL_VERDICT_NOT_REVISE} ({verdict})",
            }
        _marker, document = _load_committed_snapshot(output_dir)
        if not document["adjustments"]:
            return {
                "status": "no_adjustments",
                "applied": 0,
                "rejected": 0,
                "adjudication_source": None,
                "counts": {
                    SPOT_CHECK_VERIFIED: 0,
                    SPOT_CHECK_REFUTED: 0,
                    SPOT_CHECK_NOT_CHECKED: 0,
                },
            }
        checkpoint_missing = ADJUDICATION_KEY not in document
        if checkpoint_missing:
            document = _build_adjudication_checkpoint(
                document,
                {},
                None,
                source=ADJUDICATION_SOURCE_DEFENSIVE,
            )
        findings = _read_findings_for_apply(output_dir)
        plan = _build_apply_plan(document, findings)
        if checkpoint_missing:
            write_adjustments(output_dir, document)
        return _apply_adjustments_locked(output_dir, plan)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Settle critic proposals or explicitly recover their ledger apply"
        ),
        epilog=(
            "Exit codes: 0 = settled/applied (or idempotent); "
            "1 = validation/IO error; "
            f"{REFUSAL_EXIT_CODE} = refused — {CRITIC_VERDICT_FILENAME} does "
            "not say REVISE, so nothing was written."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    settle_parser = subparsers.add_parser(
        "settle", help="Record orchestrator adjudication from stdin and apply it"
    )
    settle_parser.add_argument("--output-dir", required=True)
    apply_parser = subparsers.add_parser(
        "apply", help="Explicit recovery for a committed proposal"
    )
    apply_parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        if args.command == "settle":
            try:
                request = json.load(sys.stdin)
            except json.JSONDecodeError as error:
                raise AdjustmentValidationError([
                    f"adjudication request is not valid JSON: {error}"
                ]) from error
            result = settle(args.output_dir, request)
        else:
            result = apply_adjustments(args.output_dir)
    except AdjustmentValidationError as error:
        for problem in error.problems:
            if args.command == "settle":
                print(f"REJECTED: {problem}")
            else:
                print(f"ERROR: {problem}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        if args.command == "settle":
            print(f"REJECTED: {error}")
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)

    if args.command == "settle":
        counts = result["counts"]
        if result["status"] == "already_settled":
            print("ALREADY SETTLED")
        else:
            print(f"RECORDED ADJUDICATION: {sum(counts.values())}")
        print(
            f"VERIFIED: {counts[SPOT_CHECK_VERIFIED]} | "
            f"REFUTED: {counts[SPOT_CHECK_REFUTED]} | "
            f"NOT_CHECKED: {counts[SPOT_CHECK_NOT_CHECKED]}"
        )
        print("REVISED ASSESSMENT: present")
        print(f"PROPOSAL DIGEST: {result[PROPOSAL_DIGEST_KEY]}")
        apply_result = result["apply"]
        if (
            result["status"] == "already_settled"
            and apply_result.get("status") == "nothing_pending"
        ):
            print("ALREADY APPLIED")
        else:
            print(
                f"APPLY: applied {apply_result.get('applied', 0)} | "
                f"rejected {apply_result.get('rejected', 0)}"
            )
        return

    print(json.dumps(result))
    if result.get("status") == "refused":
        print(f"REFUSED: {result.get('reason', 'unknown')}", file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)


if __name__ == "__main__":
    main()

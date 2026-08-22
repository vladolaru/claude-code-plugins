#!/usr/bin/env python3
"""Carry decision-critic adjustments into review-findings.json.

The decision critic records finding-level decisions (promote, demote,
rescope, correct, add, remove) in decision-critic-adjustments.json. This
module is the sole writer that applies them to review-findings.json —
with per-finding provenance, so the reconciled state stays visible under
the change — and the report is edited AFTER this runs, so the human
prose always describes a ledger that already contains the critic's
outcome. Mirrors the dispatch-plan pattern: original decision preserved,
override recorded beside it.

A half-applied batch must never exist on disk, which takes more than
validating first. Each file is replaced atomically via atomic_io's shared
`atomic_write_json` (temp file in the same directory, then os.replace),
and application is recorded on BOTH sides: every pending entry carries a
stable `adjustment_id`, and review-findings.json lists the decisions it
already contains under `applied_critic_adjustments`, one record per id.
Ids are allocated and persisted before the findings write, so every crash point converges
on the next run: if the findings write landed, its recorded ids make the
entries skip and only their flags catch up; if it did not, nothing was
recorded and the batch applies normally. Without that record, a crash
between the two writes would re-apply patches onto an already-patched
findings file and `prior` would report the critic's own output as the
reconciled state.

decision-critic-adjustments.json is also the ONLY sanctioned way to
change review-findings.json, and this module owns the write path that
says so. `write_findings()` replaces that file atomically, addressed by
output directory so no caller can misname it; both of the ledger's
writers — the review-reconciliator's first write (through
`findings_save.py`) and `apply_adjustments()` here — go through it. An
orchestrator's ad-hoc `python3 -c` edit is out of channel and forbidden:
a change worth making is worth making as an adjustment entry, where it
carries provenance.

Adjustments are a REVISE-only channel: `apply_adjustments()` refuses to
read the adjustments file or write anything unless
decision-critic-verdict.json on disk says REVISE. This gate lives here,
not in a caller, so every apply path — the CLI, step 11's defensive
re-run, and any future caller — shares one authority check instead of
each re-implementing it. A refusal returns `{"status": "refused",
"applied": 0, "reason": "no_verdict" | "verdict_not_revise (<VERDICT>)"}`
and touches no file. The CLI prints the reason and exits
`REFUSAL_EXIT_CODE` (3) — distinct from 0 (success) and 1 (validation/IO
error) — so a script depending on this command notices a refusal instead
of reading a silent no-op.
"""

import argparse
import collections
import json
import os
import sys
import uuid

try:
    from .atomic_io import atomic_write_json
    from .verdict_rules import verdict_for_counts
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.atomic_io import atomic_write_json
    from review.verdict_rules import verdict_for_counts

ACTIONS = ("promote", "demote", "rescope", "correct", "add", "remove")
# `scope` is deliberately absent: it is derived from `line` to preserve the
# pairing described below, never set by the critic. `id` is absent because
# ids are generated here, never assigned by the critic.
PATCH_FIELDS = ("severity", "title", "description", "recommendation",
                "file", "line", "category", "confidence")
ADD_REQUIRED_FIELDS = ("severity", "title", "file", "description",
                       "recommendation")
VALID_SEVERITIES = ("critical", "high", "medium", "low", "info")

# The orchestrator's per-entry verdict on the critic's claim, written into
# the adjustments file after it probes each one. A machine-readable seat for
# a judgment that previously had nowhere to land but the human report: a run
# whose orchestrator never probed anything published a batch nothing could
# tell apart from one checked entry by entry.
#
# NEVER required at apply. Step 11's defensive re-run exists precisely for
# orchestrators that crashed before doing the step-10 work, and requiring the
# key there would turn the honest default into a hard failure for exactly the
# runs the re-run is meant to converge. An entry applied without one is
# recorded as SPOT_CHECK_NOT_CHECKED — the honest default, which the ledger's
# Markdown render then shows per id.
SPOT_CHECK_KEY = "spot_check"
SPOT_CHECK_VERIFIED = "verified"
SPOT_CHECK_REFUTED = "refuted"
SPOT_CHECK_NOT_CHECKED = "not_checked"
SPOT_CHECK_VALUES = (
    SPOT_CHECK_VERIFIED, SPOT_CHECK_REFUTED, SPOT_CHECK_NOT_CHECKED,
)

# The orchestrator's post-critic assessment, top-level on the adjustments
# document. An applying batch withdraws the reconciler's `narrative_summary`
# (see WITHDRAWN_SUMMARY_KEY below) and nothing used to replace it, so a
# REVISE run published a ledger whose Assessment section was a pointer to
# prose only a human could read. This is that assessment's machine-readable
# seat: on apply it BECOMES the ledger's narrative_summary, with the
# withdrawal record left intact beside it.
REVISED_NARRATIVE_KEY = "revised_narrative"

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
# batch changes it — the same audit spirit as WITHDRAWN_SUMMARY_KEY below.
# First time only: a second round must name what the ledger came in as, not
# what the previous round left behind.
VERDICT_BEFORE_ADJUSTMENTS_KEY = "verdict_before_adjustments"
# The rejection half of the same audit trail: entries the orchestrator's
# spot-check refuted (`rejected: true` + `rejection_reason`) were, before
# this key existed, visible only in decision-critic-adjustments.json — a
# file apply_adjustments() reads but nothing downstream consults — so a
# rejected decision left no trace in review-findings.json, the artifact
# bot mode, baselines, and metrics actually read. This key carries that
# trace now; no reader consumes it yet, but the fact is at least on the
# artifact the readers already open, not only on one they never do.
# apply_adjustments() writes one record per newly-settled rejection here;
# see _load_rejected_records() for the read side of the idempotence check.
REJECTED_ADJUSTMENTS_KEY = "rejected_critic_adjustments"

# The only `schema` value ADJUSTMENTS_FILENAME is accepted under.
# decision-reviewer.md's taught template always writes `"schema": 1`
# alongside `"adjustments"`; a doc carrying any other value — or none —
# is out of that template and refused whole, the same all-or-nothing way
# an unknown action or an unaddressable id is: a critic decision written
# against a contract this module does not honor must fail loudly rather
# than being silently accepted and possibly misread.
ADJUSTMENTS_SCHEMA = 1

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

# Where a withdrawn `narrative_summary` goes. The adjustment vocabulary
# addresses issues — severity, title, scope, the fields of one finding —
# and deliberately cannot address ledger-level prose. That leaves the
# reconciler's assessment as the one part of the artifact a critic round
# can invalidate but not correct: "one CRITICAL blocker" stays written
# above a list where the critical has just been demoted to low.
#
# The pipeline cannot re-derive that prose (it is LLM output, not a
# projection of the findings), so an applying batch withdraws it rather
# than leaving it to contradict the ledger it summarizes. Withdrawn, not
# deleted: the text moves here beside the ids of the decisions that
# withdrew it, the same way a removed finding moves into
# `removed_by_critic` carrying the action that removed it. A list, because
# a second reconciliation-plus-critic round is a second withdrawal and
# must not erase the first.
WITHDRAWN_SUMMARY_KEY = "withdrawn_narrative_summary"
NARRATIVE_SUMMARY_KEY = "narrative_summary"

# The one verdict that sanctions applying adjustments. Everything else —
# STAND, ESCALATE, an unrecognized string, a missing file — refuses.
# Literal, not imported: critic.py's CRITIC_VERDICTS is the canonical
# vocabulary source and names this constant as one of its aligned sites
# (see the comment there) — importing critic.py here just to reuse one
# string would wire this module's gate to critic.py's whole import graph
# for no benefit, since the value the gate checks against is a single
# fixed literal, not a set that varies.
REVISE_VERDICT = "REVISE"

# Refusal reasons returned by apply_adjustments() under the gate. Both
# collapse a missing file and an unparseable one into the same reason
# because read_critic_verdict() cannot distinguish them either — from the
# gate's perspective there is simply no usable verdict to act on.
REFUSAL_NO_VERDICT = "no_verdict"
REFUSAL_VERDICT_NOT_REVISE = "verdict_not_revise"

# Distinct from 0 (success) and 1 (validation/IO error, see main()) so a
# caller can tell "refused by the authority gate" apart from either.
REFUSAL_EXIT_CODE = 3


def read_verdict_file(path):
    """Parse a verdict-shaped JSON file (``{"verdict": "<STRING>"}``) to
    its verdict string, or ``None`` if the file is absent, unreadable, not
    valid JSON, not a JSON object, or has no string ``verdict`` field.

    The shape-parsing core behind `read_critic_verdict()` below. It was
    shared with orchestration.py's read of review-verdict.json until that
    artifact was retired — step 11 derives the published verdict from the
    findings ledger now rather than reading a transcribed one — and it
    stays a named function rather than being inlined into its one
    remaining caller because the guard it encodes is what a second reader
    gets wrong: a well-formed-JSON-but-non-object file (`[1, 2]`,
    `"hello"`, `5`) sails past a `(json.JSONDecodeError, OSError)` tuple,
    and the `.get()` behind it raises `AttributeError`.
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    verdict = data.get("verdict")
    if not isinstance(verdict, str):
        return None
    return verdict


def read_critic_verdict(output_dir):
    """Read the critic's raw verdict string from CRITIC_VERDICT_FILENAME.

    Returns the `verdict` field as-is (e.g. "STAND", "REVISE", "SKIPPED",
    or any other string the critic wrote) or None if the file is absent,
    unreadable, not valid JSON, not a JSON object, or has no string
    `verdict` field — see `read_verdict_file()`, the shared parser this
    wraps. This reader is deliberately permissive — it answers "what does
    the file say", not "is that an acceptable value" — the caller decides
    what to do with the result. `apply_adjustments()`'s gate below only
    ever proceeds on the literal "REVISE"; the presentation mapping
    downstream consumers need instead — "SKIPPED" and a missing/
    unparseable file both reading as "unavailable" — lives next door in
    `critic_verdict_for_state()`, not here.
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
    recorded", the applier re-raises, and the verdict sync maps to its own
    skipped/failed vocabulary.
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


def validate_adjustments(payload):
    """Validate the batch SHAPE of a decision-critic adjustments document,
    without resolving any id against an actual findings ledger.

    Returns a list of human-readable problems; an empty list means valid.
    This is the "does this batch make grammatical sense" check — document
    is an object, schema is exactly ADJUSTMENTS_SCHEMA, 'adjustments' is a
    list, and every entry has a recognized action, an adjustable field set
    (via `_validate_fields()`), a required-field-complete `add`, and no
    critic-supplied id on `add` — shared by two callers with two different
    amounts of context available: `critic.py`'s save gate, which runs
    BEFORE any findings ledger exists to check ids against, and
    `apply_adjustments()` below, which re-checks the same shape
    defensively right before it goes on to do the ledger-dependent checks
    (does an id name a real issue, is a target removed earlier in this
    batch) that only it has the context to make.

    Deliberately excludes anything that needs `review-findings.json`: an
    entry's target existing, per-batch duplicate targets, and a target
    removed earlier in the same batch all stay in `apply_adjustments()`,
    which is the only caller with a ledger to check them against.
    """
    if not isinstance(payload, dict):
        return [f"{ADJUSTMENTS_FILENAME} must be a JSON object"]
    schema = payload.get("schema")
    if schema != ADJUSTMENTS_SCHEMA:
        return [
            f"{ADJUSTMENTS_FILENAME}: 'schema' must be {ADJUSTMENTS_SCHEMA}, "
            f"got {schema!r}"
        ]
    adjustments = payload.get("adjustments")
    if not isinstance(adjustments, list):
        return [f"{ADJUSTMENTS_FILENAME}: 'adjustments' must be a list"]

    problems = []
    revised = payload.get(REVISED_NARRATIVE_KEY)
    if revised is not None and not isinstance(revised, str):
        problems.append(
            f"{ADJUSTMENTS_FILENAME}: {REVISED_NARRATIVE_KEY!r} must be a "
            f"string"
        )
    seen_ids = {}
    for idx, entry in enumerate(adjustments):
        label = f"adjustment[{idx}]"
        if not isinstance(entry, dict):
            problems.append(f"{label} must be an object")
            continue
        # Checked before the action gate below, and with `continue` on
        # failure, so a batch is refused for a bad spot_check even on an
        # entry whose action is also unknown — the orchestrator sees both
        # problems' cause, not one hiding the other.
        if SPOT_CHECK_KEY in entry:
            spot_check = entry[SPOT_CHECK_KEY]
            if spot_check not in SPOT_CHECK_VALUES:
                problems.append(
                    f"{label}: unknown spot_check {spot_check!r} "
                    f"(allowed: {', '.join(SPOT_CHECK_VALUES)})"
                )
            elif (
                entry.get("rejected") is True
                and spot_check != SPOT_CHECK_REFUTED
            ):
                problems.append(
                    f"{label}: a rejected entry's spot_check must be "
                    f"{SPOT_CHECK_REFUTED!r} — rejecting a decision IS "
                    f"refuting it, and {spot_check!r} claims the opposite"
                )
        adjustment_id = entry.get("adjustment_id")
        if adjustment_id is not None:
            if not isinstance(adjustment_id, str) or not adjustment_id:
                problems.append(
                    f"{label}: 'adjustment_id' must be a non-empty string"
                )
            elif adjustment_id in seen_ids:
                problems.append(
                    f"{label}: duplicate adjustment_id {adjustment_id!r} "
                    f"(also adjustment[{seen_ids[adjustment_id]}]) — ids "
                    f"identify which decisions a ledger already contains"
                )
            else:
                seen_ids[adjustment_id] = idx
        action = entry.get("action")
        if action not in ACTIONS:
            problems.append(
                f"{label}: unknown action {action!r} "
                f"(allowed: {', '.join(ACTIONS)})"
            )
            continue
        fields = entry.get("fields") or {}
        try:
            _validate_fields(fields, label)
        except ValueError as err:
            problems.append(str(err))
            continue
        if action == "add":
            missing = [k for k in ADD_REQUIRED_FIELDS if k not in fields]
            if missing:
                problems.append(
                    f"{label}: add requires fields {', '.join(missing)}"
                )
            if entry.get("id") is not None:
                problems.append(
                    f"{label}: ids are generated, not assigned — the "
                    f"critic must not invent ids"
                )
    return problems


def _apply_scope_pairing(issue, line_is_null):
    """Keep `scope` and `line` consistent for one issue.

    The contract is a pair, not two independent fields: schemas/
    review-output.ts declares `scope?: 'file'` as "present (with
    line: null) when the finding is file-scoped", and output.py sets
    `issue['scope'] = 'file'` only for file-scoped findings, which its
    Markdown renderer then branches on. A patch that moved `line`
    without moving `scope` would publish a line-anchored finding still
    marked file-scoped, or a null line with no marker at all.
    """
    if line_is_null:
        issue["scope"] = "file"
    else:
        issue.pop("scope", None)


def _recount_summary(findings, issues):
    """Rebuild the summary from the population it claims to describe.

    An out-of-vocabulary severity would silently drop out of
    `by_severity` while still counting in `total_issues`, publishing a
    summary that undercounts its own list. Every write through this
    module is validated, so the only source is a malformed pre-existing
    ledger — which is worth failing on, not smoothing over.

    Returns the recounted severity counts, which the caller feeds to
    `verdict_rules.verdict_for_counts()`. This function used to leave
    `verdict` alone — survivable only while step 11 copied an
    orchestrator-transcribed verdict over the ledger's. With the published
    verdict now DERIVED from this ledger, the recount and the verdict have
    to move together or a demoted-to-low finding list publishes the
    pre-demotion verdict with machine authority.
    """
    counts = {severity: 0 for severity in VALID_SEVERITIES}
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise ValueError(
                f"{FINDINGS_FILENAME}: issue at position {index} is not "
                f"an object"
            )
        severity = issue.get("severity")
        if severity not in counts:
            raise ValueError(
                f"{FINDINGS_FILENAME}: issue {issue.get('id')!r} has "
                f"severity {severity!r} outside the vocabulary "
                f"(allowed: {', '.join(VALID_SEVERITIES)})"
            )
        counts[severity] += 1
    summary = findings.setdefault("summary", {})
    summary["total_issues"] = len(issues)
    summary["by_severity"] = counts
    return counts


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

    Mirrors `_load_recorded_records()` for the applied side: `None` (the key
    has never been written) reads as an empty list, and anything present
    but not a list of objects is a malformed pre-existing ledger — worth
    failing on, the same call `_load_recorded_records()` makes, since this
    function is about to write against what it reads.
    """
    recorded = findings.get(REJECTED_ADJUSTMENTS_KEY)
    if recorded is None:
        return []
    if not isinstance(recorded, list) or not all(
        isinstance(value, dict) for value in recorded
    ):
        raise ValueError(
            f"{FINDINGS_FILENAME}: {REJECTED_ADJUSTMENTS_KEY!r} must be a "
            f"list of objects"
        )
    return list(recorded)


def _index_adjustment_ids(adjustments):
    """Validate id shape and uniqueness across the whole file."""
    seen = {}
    for idx, entry in enumerate(adjustments):
        if not isinstance(entry, dict):
            raise ValueError(f"adjustment[{idx}] must be an object")
        adjustment_id = entry.get("adjustment_id")
        if adjustment_id is None:
            continue
        if not isinstance(adjustment_id, str) or not adjustment_id:
            raise ValueError(
                f"adjustment[{idx}]: 'adjustment_id' must be a non-empty "
                f"string"
            )
        if adjustment_id in seen:
            raise ValueError(
                f"adjustment[{idx}]: duplicate adjustment_id "
                f"{adjustment_id!r} (also adjustment[{seen[adjustment_id]}]) "
                f"— ids identify which decisions a ledger already contains"
            )
        seen[adjustment_id] = idx


# Entry states, shared by the writer and the read-only counter so the two
# can never disagree about what "pending" means.
_SETTLED = "settled"      # flagged applied, or rejected by the orchestrator
_CATCH_UP = "catch_up"    # findings write landed, flag write did not
_PENDING = "pending"      # not yet applied anywhere


def _entry_state(entry, already_recorded):
    """Classify one adjustment against the record kept on both sides."""
    if entry.get("applied") is True or entry.get("rejected") is True:
        return _SETTLED
    if entry.get("adjustment_id") in already_recorded:
        return _CATCH_UP
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
    adj_path = os.path.join(output_dir, ADJUSTMENTS_FILENAME)
    if not os.path.isfile(adj_path):
        return 0
    with open(adj_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    adjustments = doc.get("adjustments") if isinstance(doc, dict) else None
    if not isinstance(adjustments, list):
        raise ValueError(
            f"{ADJUSTMENTS_FILENAME}: 'adjustments' must be a list"
        )
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


def _withdraw_narrative_summary(findings, recorded_ids):
    """Retract prose the applied batch may have just contradicted.

    Called only when a batch actually applied, so a refused, settled, or
    fully rejected call leaves the assessment exactly as the reconciler
    wrote it — nothing changed, nothing to withdraw.

    A ledger with no summary records no withdrawal: there is no text to
    keep auditable, and fabricating an empty entry would claim a retraction
    that never happened.
    """
    prior = findings.get(NARRATIVE_SUMMARY_KEY)
    findings[NARRATIVE_SUMMARY_KEY] = None
    if not isinstance(prior, str) or not prior.strip():
        return
    withdrawn = findings.get(WITHDRAWN_SUMMARY_KEY)
    if not isinstance(withdrawn, list):
        withdrawn = []
    withdrawn.append({
        "text": prior,
        # The exact decisions that cost the assessment its standing, so the
        # retraction can be read back against the batch that caused it.
        "withdrawn_by": list(recorded_ids),
    })
    findings[WITHDRAWN_SUMMARY_KEY] = withdrawn


def apply_adjustments(output_dir):
    """Apply pending critic adjustments once; return a result dict.

    Idempotent from either side: an entry is skipped when its flag says
    it was applied or the orchestrator rejected it, and also when the
    findings file already records its `adjustment_id` — the case a crash
    between the two writes leaves behind, where only the flags need to
    catch up. An unrecognized `schema` (see `ADJUSTMENTS_SCHEMA`), unknown
    ids, unknown actions, invalid patches, duplicate or already-removed
    targets, and a malformed pre-existing ledger fail the whole call loudly
    BEFORE anything is written — a critic decision that cannot land must
    not silently vanish, and a half-applied batch must not exist.

    Rejected entries (`rejected: true` + `rejection_reason`) are never
    applied, but a newly-settled rejection still costs one findings write:
    its `adjustment_id`, `action`, target id, and `rejection_reason` land
    in `REJECTED_ADJUSTMENTS_KEY` — before this, a rejection was visible
    only in decision-critic-adjustments.json, which apply_adjustments()
    itself is the only reader of. The audit record now lands on the
    artifact bot mode, baselines, and metrics actually read; no reader
    consumes the key yet, but the trace exists on the right artifact for
    when one does. An entry carrying both `applied: true` and
    `rejected: true` (a hand-edited adjustments doc) is never audited
    here: the applied mutation is ground truth, and recording both would
    publish two contradictory outcomes for one `adjustment_id`. A
    `rejected: true` entry with a missing or blank `rejection_reason`
    refuses the whole batch — the reason is the entire payload of the
    audit record.

    An applying batch also settles three ledger-level facts the critic's
    finding-level vocabulary cannot reach. `verdict` is recomputed from the
    post-batch severities through the shared ladder in `verdict_rules.py`,
    with the pre-apply value preserved once under
    `VERDICT_BEFORE_ADJUSTMENTS_KEY`; the reconciler's `narrative_summary`
    is withdrawn and replaced by the document's `revised_narrative` when it
    carries one; and each applied entry's `spot_check` — the orchestrator's
    own outcome for that decision, defaulting to SPOT_CHECK_NOT_CHECKED —
    lands beside its id in `APPLIED_IDS_KEY`.

    Gated on the critic's verdict, checked before anything else is read
    or written: adjustments are a REVISE-only channel (see module
    docstring), so any other verdict — or none on file — refuses the
    whole call and returns `{"status": "refused", ...}` instead of
    touching a file. This is the one gate every caller shares: the CLI,
    step 11's defensive re-run, and any future caller all go through this
    function, so none of them can apply adjustments a STAND or ESCALATE
    verdict never sanctioned.
    """
    verdict = read_critic_verdict(output_dir)
    # Two independent checks, not one combined condition: a missing or
    # unparseable verdict file and a present-but-wrong verdict are
    # different failure modes with different reasons, and keeping them as
    # separate `if`s means a defect in either check only ever manifests
    # against the scenario it guards.
    if verdict is None:
        return {"status": "refused", "applied": 0, "reason": REFUSAL_NO_VERDICT}
    if verdict != REVISE_VERDICT:
        return {
            "status": "refused", "applied": 0,
            "reason": f"{REFUSAL_VERDICT_NOT_REVISE} ({verdict})",
        }

    adj_path = os.path.join(output_dir, ADJUSTMENTS_FILENAME)
    findings_path = os.path.join(output_dir, FINDINGS_FILENAME)
    if not os.path.isfile(adj_path):
        return {"status": "no_adjustments", "applied": 0}
    with open(adj_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    # Shape-check the WHOLE document before any field inside it, mirroring
    # read_findings_file()'s FINDINGS_READ_NOT_OBJECT twenty lines below:
    # a non-object doc ([], "hello", 5 — all valid JSON) is a distinct
    # diagnosis from "an object with the wrong schema", and collapsing the
    # two would misreport a shape defect as a content defect (a critic
    # fixing 'schema' on a payload that isn't even a dict yet gets nowhere).
    # `validate_adjustments()` is the shared batch-shape check (see its own
    # docstring): the critic's own save gate runs it before any findings
    # ledger exists, and this is the SAME check run again here, defensively,
    # right before the ledger-dependent checks below that only this
    # function has the context to make.
    problems = validate_adjustments(doc)
    if problems:
        raise ValueError(problems[0])
    adjustments = doc.get("adjustments")
    read = read_findings_file(findings_path)
    # This path is about to WRITE against what it reads, so every failure
    # is loud. Absent, unreadable, and unparseable re-raise the original
    # exception — a caller catching `FileNotFoundError` still sees one —
    # and only the shape fact, which no exception carries, is raised as
    # this module's own ValueError. That shape guard matters for the same
    # reason the adjustments file has one: a non-object ledger would
    # otherwise die on an AttributeError instead of this module's
    # ValueError contract, and the step-11 caller catches only the
    # latter — a malformed ledger would crash finalize inside the guard
    # meant to keep it running.
    if read.status == FINDINGS_READ_NOT_OBJECT:
        raise ValueError(f"{FINDINGS_FILENAME} must be a JSON object")
    if read.status != FINDINGS_READ_OK:
        raise read.error
    findings = read.findings
    issues = findings.get("issues")
    if not isinstance(issues, list):
        raise ValueError(f"{FINDINGS_FILENAME} has no issues list")
    # An issue without a usable id is not addressable: keeping it out of
    # the map is what makes a missing id fail as a clean unknown-id error
    # instead of matching a None key or dying on a KeyError later.
    by_id = {
        issue["id"]: issue
        for issue in issues
        if isinstance(issue, dict)
        and isinstance(issue.get("id"), str)
        and issue["id"]
    }

    _index_adjustment_ids(adjustments)
    recorded_records = _load_recorded_records(findings)
    already_recorded = {
        record["adjustment_id"] for record in recorded_records
    }
    rejected_records = _load_rejected_records(findings)
    already_rejected_ids = {
        record.get("adjustment_id")
        for record in rejected_records
        if isinstance(record.get("adjustment_id"), str)
    }

    # Validate every pending entry BEFORE mutating anything. The working
    # view tracks what each later entry in this batch can still address.
    pending = []
    catch_up = []
    # Rejections a spot-check settled but this findings ledger has not yet
    # recorded — orthogonal to the applied-ids bookkeeping above, since a
    # rejected entry never reaches `pending`/`catch_up` at all.
    newly_rejected = []
    seen_targets = {}
    removed_in_batch = {}
    for idx, entry in enumerate(adjustments):
        label = f"adjustment[{idx}]"
        entry_state = _entry_state(entry, already_recorded)
        if entry_state == _SETTLED:
            # `applied is True` wins over a coexisting `rejected: true`:
            # the applied mutation already landed and IS the ground truth
            # for this decision, so a rejected flag added to it afterward
            # (a post-hoc hand edit of the adjustments doc, since the
            # step-10 briefing never sets both) is tampering with that
            # doc, not a second decision — auditing it would assert two
            # contradictory outcomes for one adjustment_id.
            if entry.get("rejected") is True and entry.get("applied") is not True:
                existing_id = entry.get("adjustment_id")
                if not (
                    isinstance(existing_id, str)
                    and existing_id in already_rejected_ids
                ):
                    reason = entry.get("rejection_reason")
                    if not isinstance(reason, str) or not reason.strip():
                        raise ValueError(
                            f"{label}: rejected entries must carry a "
                            f"non-empty 'rejection_reason' — it is the "
                            f"entire payload of the audit record"
                        )
                    newly_rejected.append(entry)
            continue
        if entry_state == _CATCH_UP:
            # The findings write landed; only the flag write was lost.
            catch_up.append(entry)
            continue
        action = entry.get("action")
        # DEFENSIVE RE-CHECK, not independent coverage, from here through
        # the `add` branch below: validate_adjustments() (called above,
        # before this loop even starts) already verified action-in-ACTIONS,
        # `fields` vocabulary/severity/line shape, add's required fields,
        # and add's id-must-be-None rule against the WHOLE document — it is
        # a strict superset of these checks, gating before any entry here
        # is reached. A future caller (including step 11) must not assume
        # this loop is where those facts are independently established;
        # they are only re-verified here so a hand-edited adjustments.json
        # that somehow slipped past the earlier gate still fails per-entry
        # instead of silently applying. The `else` branch below — id
        # resolution against `by_id`/`seen_targets`/`removed_in_batch` — is
        # DIFFERENT: it needs the findings ledger, which
        # validate_adjustments() never sees, so those checks remain the
        # actual (non-redundant) authority.
        if action not in ACTIONS:
            raise ValueError(
                f"{label}: unknown action {action!r} "
                f"(allowed: {', '.join(ACTIONS)})"
            )
        fields = entry.get("fields") or {}
        _validate_fields(fields, label)
        if action == "add":
            missing = [k for k in ADD_REQUIRED_FIELDS if k not in fields]
            if missing:
                raise ValueError(
                    f"{label}: add requires fields {', '.join(missing)}"
                )
            if entry.get("id") is not None:
                raise ValueError(
                    f"{label}: ids are generated, not assigned — the critic "
                    f"must not invent ids"
                )
        else:
            target_id = entry.get("id")
            if not isinstance(target_id, str) or not target_id:
                raise ValueError(
                    f"{label}: no issue with id {target_id!r} in "
                    f"{FINDINGS_FILENAME}"
                )
            if target_id in removed_in_batch:
                raise ValueError(
                    f"{label}: id {target_id!r} is removed by "
                    f"adjustment[{removed_in_batch[target_id]}] in this batch"
                )
            if target_id in seen_targets:
                raise ValueError(
                    f"{label}: duplicate target {target_id!r} — merge "
                    f"finding-level changes into one entry per finding"
                )
            if target_id not in by_id:
                raise ValueError(
                    f"{label}: no issue with id {target_id!r} in "
                    f"{FINDINGS_FILENAME}"
                )
            seen_targets[target_id] = idx
            if action == "remove":
                removed_in_batch[target_id] = idx
        pending.append((entry, action, fields))

    # Allocate ids before the findings write, so the record the findings
    # file keeps stays resolvable against this file after a crash. Newly
    # rejected entries need one too — the rejection audit record is keyed
    # on it the same way an applied record is keyed on `pending`'s.
    newly_allocated = False
    for entry, _action, _fields in pending:
        if not entry.get("adjustment_id"):
            entry["adjustment_id"] = uuid.uuid4().hex
            newly_allocated = True
    for entry in newly_rejected:
        if not entry.get("adjustment_id"):
            entry["adjustment_id"] = uuid.uuid4().hex
            newly_allocated = True

    rejection_records = [
        {
            "adjustment_id": entry["adjustment_id"],
            "action": entry.get("action"),
            "target_id": entry.get("id"),
            "rejection_reason": entry.get("rejection_reason") or "",
        }
        for entry in newly_rejected
    ]

    applied = 0
    batch_ids = []
    for entry, action, fields in pending:
        provenance = {
            "action": action,
            "rationale": entry.get("rationale") or "",
        }
        if action == "add":
            new_issue = {
                "id": str(uuid.uuid4())[:8],
                "category": fields.get("category", "general"),
                "confidence": fields.get("confidence", 0.9),
                "line": fields.get("line"),
                **fields,
                "critic_adjustment": provenance,
            }
            _apply_scope_pairing(new_issue, new_issue.get("line") is None)
            issues.append(new_issue)
            by_id[new_issue["id"]] = new_issue
        elif action == "remove":
            target = by_id.pop(entry["id"])
            issues.remove(target)
            target["critic_adjustment"] = provenance
            findings.setdefault("removed_by_critic", []).append(target)
        else:
            target = by_id[entry["id"]]
            provenance["prior"] = {k: target.get(k) for k in fields}
            target.update(fields)
            if "line" in fields:
                _apply_scope_pairing(target, fields["line"] is None)
            target["critic_adjustment"] = provenance
        spot_check = entry.get(SPOT_CHECK_KEY)
        if spot_check not in SPOT_CHECK_VALUES:
            # Never required (see SPOT_CHECK_KEY): an orchestrator that
            # crashed before step 10's probes still converges here, and the
            # ledger says so rather than implying a check nobody ran.
            spot_check = SPOT_CHECK_NOT_CHECKED
        recorded_records.append({
            "adjustment_id": entry["adjustment_id"],
            SPOT_CHECK_KEY: spot_check,
        })
        batch_ids.append(entry["adjustment_id"])
        applied += 1

    if newly_allocated:
        # Ids only — the applied flags and this call's other bookkeeping
        # belong after the findings write, same crash-safety ordering as
        # the applied-ids record: a crash after this line but before the
        # findings write below still leaves the id resolvable on retry.
        atomic_write_json(adj_path, doc)

    if applied:
        counts = _recount_summary(findings, issues)
        # Recomputed from the severities this batch just left behind,
        # through the ladder agent/output.py publishes reviews with — one
        # rule, so an adjusted ledger and a freshly written one can never
        # disagree about what their own findings mean.
        recomputed = verdict_for_counts(counts)
        if findings.get("verdict") != recomputed:
            findings.setdefault(
                VERDICT_BEFORE_ADJUSTMENTS_KEY, findings.get("verdict")
            )
            findings["verdict"] = recomputed
        findings[APPLIED_IDS_KEY] = recorded_records
        # Only the ids applied in THIS call: recorded_records is cumulative
        # across every batch the ledger ever absorbed, and a second
        # withdrawal must name the decisions that caused it, not history.
        _withdraw_narrative_summary(findings, batch_ids)
        # The orchestrator's post-critic assessment takes the seat the
        # withdrawal just emptied. The withdrawal record stays: replacement
        # is not erasure, and the reconciler's retracted words remain
        # readable beside the ids that cost them their standing.
        revised = doc.get(REVISED_NARRATIVE_KEY)
        if isinstance(revised, str) and revised.strip():
            findings[NARRATIVE_SUMMARY_KEY] = revised
    if rejection_records:
        findings[REJECTED_ADJUSTMENTS_KEY] = rejected_records + rejection_records
    if applied or rejection_records:
        # Through the shared findings writer, not the raw atomic write:
        # this is an in-channel write, so it re-stamps the content digest
        # finalize verifies. A patched ledger that kept the pre-patch
        # stamp would read as an out-of-channel edit at finalize — the
        # applier accusing itself.
        write_findings(output_dir, findings)
    if applied or catch_up:
        for entry, _action, _fields in pending:
            entry["applied"] = True
        for entry in catch_up:
            entry["applied"] = True
        atomic_write_json(adj_path, doc)
    return {"status": "applied" if applied else "nothing_pending",
            "applied": applied}


def main():
    parser = argparse.ArgumentParser(
        description="Apply decision-critic adjustments to review-findings.json",
        epilog=(
            "Exit codes: 0 = applied (or nothing pending); "
            "1 = validation/IO error; "
            f"{REFUSAL_EXIT_CODE} = refused — {CRITIC_VERDICT_FILENAME} does "
            "not say REVISE, so nothing was written."
        ),
    )
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        result = apply_adjustments(args.output_dir)
    except (ValueError, OSError, json.JSONDecodeError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    # One parser handles every status: the result JSON always goes to
    # stdout, whether applied, nothing pending, or refused. Refused
    # additionally gets a human-readable stderr line and the distinct
    # exit code — `.get()`, not a subscript, so a refusal result that
    # somehow lacks `reason` still reports and exits 3 instead of
    # crashing on a KeyError on its way out the door.
    print(json.dumps(result))
    if result.get("status") == "refused":
        print(f"REFUSED: {result.get('reason', 'unknown')}", file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)


if __name__ == "__main__":
    main()

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
stable `adjustment_id`, and review-findings.json lists the ids it
already contains under `applied_critic_adjustments`. Ids are allocated
and persisted before the findings write, so every crash point converges
on the next run: if the findings write landed, its recorded ids make the
entries skip and only their flags catch up; if it did not, nothing was
recorded and the batch applies normally. Without that record, a crash
between the two writes would re-apply patches onto an already-patched
findings file and `prior` would report the critic's own output as the
reconciled state.

decision-critic-adjustments.json is also the ONLY sanctioned way to
change review-findings.json, and this module owns the write path that
says so. `write_findings()` stamps a `content_digest` over the findings
file's own content and writes it atomically; all three of that file's
writers — the review-reconciliator's first write, `apply_adjustments()`
here, and orchestration.py's Rule 23 verdict sync — go through it, so a
findings file that does not verify against its own stamp at finalize was
written by something outside the channel.
That is detection, not prevention: an orchestrator's ad-hoc `python3 -c`
edit cannot be blocked, but it can be made visible instead of shipping as
if the pipeline had produced it (see `verify_findings_integrity()`).

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
import hashlib
import json
import os
import sys
import uuid

try:
    from .atomic_io import atomic_write_json
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.atomic_io import atomic_write_json

ACTIONS = ("promote", "demote", "rescope", "correct", "add", "remove")
# `scope` is deliberately absent: it is derived from `line` to preserve the
# pairing described below, never set by the critic. `id` is absent because
# ids are generated here, never assigned by the critic.
PATCH_FIELDS = ("severity", "title", "description", "recommendation",
                "file", "line", "category", "confidence")
ADD_REQUIRED_FIELDS = ("severity", "title", "file", "description",
                       "recommendation")
VALID_SEVERITIES = ("critical", "high", "medium", "low", "info")

ADJUSTMENTS_FILENAME = "decision-critic-adjustments.json"
FINDINGS_FILENAME = "review-findings.json"
CRITIC_VERDICT_FILENAME = "decision-critic-verdict.json"
APPLIED_IDS_KEY = "applied_critic_adjustments"
# The rejection half of the same audit trail: entries the orchestrator's
# spot-check refuted (`rejected: true` + `rejection_reason`) were, before
# this key existed, visible only in decision-critic-adjustments.json —
# a file no downstream reader consults — so a rejected decision left no
# trace in the artifact bot mode, baselines, and metrics actually read.
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

# The stamp every sanctioned write of the findings ledger leaves behind,
# and the vocabulary finalize reports the check in. See write_findings()
# below for what the three writers are and why absence of the stamp is
# itself evidence.
CONTENT_DIGEST_KEY = "content_digest"
INTEGRITY_INTACT = "intact"
INTEGRITY_MODIFIED = "modified_out_of_channel"

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

    This is the shape-parsing core shared by `read_critic_verdict()`
    (decision-critic-verdict.json, below) and orchestration.py's Rule 23
    read of review-verdict.json — the same file shape, read by two
    modules that used to parse it two different ways. The
    review-verdict.json side reimplemented a narrower guard, only
    `(json.JSONDecodeError, OSError)`, and then called `.get()`
    unconditionally: a well-formed-JSON-but-non-object file (`[1, 2]`,
    `"hello"`, `5`) sailed past that tuple and `.get()` raised
    `AttributeError` on it, crashing finalize before pipeline-result.json
    was ever written. Sharing this core closes that gap at the root
    instead of patching each call site's guard separately.
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
    `read_verdict_file()` made for the verdict files one task ago: four
    call sites used to open this file with four slightly different guards
    (the best-effort id reader, `apply_adjustments()`,
    `verify_findings_integrity()`, and orchestration's Rule 23 sync), and
    the differences between them were accidents rather than decisions —
    one of them even opened the file with the platform's locale encoding
    while the others pinned UTF-8.

    Returns a `FindingsRead`. What each caller does with a given state is
    deliberately NOT decided here — the facts are shared, the policy is
    not: the best-effort reader treats every failure as "nothing
    recorded", the applier re-raises, the integrity check answers
    None-vs-modified, and the verdict sync maps to its own
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


def compute_findings_digest(findings):
    """Hash a findings ledger's content, excluding the stamp itself.

    ONE canonical serialization, spelled here and nowhere else: the
    payload minus `content_digest`, `json.dumps`'d with `sort_keys=True`,
    the compact `(",", ":")` separators, and `ensure_ascii=True`, then
    SHA-256 over its bytes. Sorted keys and fixed separators mean the
    digest describes the ledger's CONTENT, not the whitespace or key
    order of whichever writer produced it — a rewrite that reorders keys
    without changing a value still verifies, and a changed value never
    does.

    `ensure_ascii=True` is load-bearing for two reasons, neither of them
    "matching the artifact's own encoding" (it cannot: the artifact is
    written with `indent=2` while this form is compact, so the two byte
    streams never match anyway).

    First, TOTALITY. This function's input is an adversarial hand edit by
    construction — that is the whole point of the check — and `json.load`
    accepts payloads that cannot be encoded back out: `"\\ud800"` parses
    to a lone surrogate, and `.encode("utf-8")` on it raises
    `UnicodeEncodeError`. Step 11 calls the verifier bare at two sites,
    so a raise here would kill finalize before pipeline-result.json was
    ever written — the exact failure class `read_verdict_file()` exists
    to have stopped, reintroduced on the one input class this function is
    pointed at. With `ensure_ascii=True` every non-ASCII scalar, lone
    surrogates included, leaves as a `\\uXXXX` escape, so the
    serialization is total for anything `json.load` can return.

    Second, CANONICALITY: an all-ASCII form has exactly one byte
    representation per parsed value, so two readers of the same ledger
    can never disagree about the bytes they hashed.

    The stamp is excluded from its own input for the obvious reason: a
    digest that hashed the field it is written into could never verify,
    since stamping it would immediately invalidate it. Excluding it makes
    re-stamping an unchanged ledger a fixed point, which is what lets two
    in-channel writes in a row both verify.

    Callers must never re-spell this: `write_findings()` and
    `verify_findings_integrity()` both go through this function, so the
    producer and the verifier cannot drift into hashing two different
    things and reporting the difference as tampering.
    """
    if not isinstance(findings, dict):
        raise ValueError(f"{FINDINGS_FILENAME} must be a JSON object")
    payload = {
        key: value
        for key, value in findings.items()
        if key != CONTENT_DIGEST_KEY
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_findings(output_dir, findings):
    """The ONE sanctioned write path for review-findings.json.

    Stamps `content_digest` over the rest of the ledger, then replaces the
    file atomically through the shared `atomic_write_json`. Returns the
    digest.

    Addressed by output directory, not by path, like every other public
    entry point in this module (`read_critic_verdict`, `pending_count`,
    `apply_adjustments`, `verify_findings_integrity`). The filename is
    this module's constant, so a caller cannot point the sanctioned
    writer at the wrong file — which matters most for the one writer the
    pipeline cannot check, the review-reconciliator agent following a
    taught snippet.

    Three writers exist across a run and all three call this: the
    review-reconciliator agent's first write (taught in
    `agents/review-reconciliator.md`), `apply_adjustments()` below, and
    orchestration.py's Rule 23 verdict sync. That is what makes the stamp
    load-bearing rather than decorative — every write the pipeline
    sanctions leaves a ledger that verifies, so a ledger that does not
    verify at finalize was written by something else.

    The stamp is assigned into the caller's dict BEFORE the write, since
    it has to be part of the payload that lands on disk. A failed write
    therefore leaves the caller holding a stamped dict that was never
    published. That is harmless and deliberately not worked around: no
    caller reads the dict back after a failed write, and re-stamping is a
    fixed point, so a retry produces exactly the same digest.

    Detection, not prevention. Nothing here can stop an orchestrator's
    ad-hoc `python3 -c` edit — the field defect this exists for, where an
    applied finding's `title` and `recommendation` were rewritten by hand
    after the apply, with no ledger entry and against the critic's
    keep-verbatim ask. What it can do is make that edit visible instead of
    letting it ship as if the pipeline had produced it.
    """
    if not isinstance(findings, dict):
        raise ValueError(f"{FINDINGS_FILENAME} must be a JSON object")
    digest = compute_findings_digest(findings)
    findings[CONTENT_DIGEST_KEY] = digest
    atomic_write_json(os.path.join(output_dir, FINDINGS_FILENAME), findings)
    return digest


def verify_findings_integrity(output_dir):
    """Answer whether the ledger on disk is the one the channel wrote.

    Returns `INTEGRITY_INTACT`, `INTEGRITY_MODIFIED`, or `None` when there
    is nothing to verify.

    `None` — no verifiable ledger, which covers TWO facts and must be
    read as neither of the other answers: the file is absent (the
    ordinary partial-run case finalize already records its own way), or
    it exists but could not be read at all (`OSError`). An unreadable
    file says nothing about its content, and reporting either verdict
    would be a measured claim the evidence does not support; the
    null-when-unmeasured convention `worktree_hygiene` and `usage`
    already use applies.

    `INTEGRITY_MODIFIED` — the file exists and is readable but is not what
    an in-channel write leaves behind. Four shapes qualify, all one
    statement: unparseable JSON, a non-object payload, a missing or
    non-string `content_digest`, and a digest that does not match the
    content beside it. The missing-stamp case is deliberate and
    adjudicated: within this version every sanctioned writer stamps, so an
    unstamped ledger at finalize means an out-of-channel rewrite dropped
    the key. Absence of the stamp on a file the channel always stamps IS
    the evidence — treating it as "legitimately unstamped" would hand
    every hand-edit an excuse, since dropping a key is exactly what a
    naive `json.dump` of a hand-built dict does.

    This never raises on the content it inspects. That is a hard
    requirement, not a nicety: step 11 calls it bare, so an exception
    here would take finalize down before pipeline-result.json exists —
    and its input is by construction whatever an out-of-channel edit left
    behind. `compute_findings_digest()`'s totality is what delivers it.
    """
    read = read_findings_file(os.path.join(output_dir, FINDINGS_FILENAME))
    if read.status in (FINDINGS_READ_ABSENT, FINDINGS_READ_IO_ERROR):
        return None
    if read.status != FINDINGS_READ_OK:
        return INTEGRITY_MODIFIED
    findings = read.findings
    recorded = findings.get(CONTENT_DIGEST_KEY)
    if not isinstance(recorded, str):
        return INTEGRITY_MODIFIED
    if recorded != compute_findings_digest(findings):
        return INTEGRITY_MODIFIED
    return INTEGRITY_INTACT


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


def _load_recorded_ids(findings):
    """Read the adjustment ids the findings file already contains."""
    recorded = findings.get(APPLIED_IDS_KEY)
    if recorded is None:
        return []
    if not isinstance(recorded, list) or not all(
        isinstance(value, str) for value in recorded
    ):
        raise ValueError(
            f"{FINDINGS_FILENAME}: {APPLIED_IDS_KEY!r} must be a list of "
            f"strings"
        )
    return list(recorded)


def _load_rejected_records(findings):
    """Read the rejection audit records the findings file already contains.

    Mirrors `_load_recorded_ids()` for the applied side: `None` (the key
    has never been written) reads as an empty list, and anything present
    but not a list of objects is a malformed pre-existing ledger — worth
    failing on, the same call `_load_recorded_ids()` makes, since this
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
    return {value for value in recorded if isinstance(value, str)}


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
    in `REJECTED_ADJUSTMENTS_KEY` so a decision the orchestrator's
    spot-check refuted has a trace in the artifact bot mode, baselines,
    and metrics actually read — before this, a rejection was visible only
    in decision-critic-adjustments.json, which none of them consult.

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
    # Doc-level shape check, same tier as the 'adjustments' list check right
    # below: schema describes the WHOLE document's contract, not one entry,
    # so it is validated before any entry is even looked at. `doc.get(...)`
    # is guarded the same way as the 'adjustments' read below — a doc that
    # is not an object at all reads as a missing schema, not a crash.
    schema = doc.get("schema") if isinstance(doc, dict) else None
    if schema != ADJUSTMENTS_SCHEMA:
        raise ValueError(
            f"{ADJUSTMENTS_FILENAME}: 'schema' must be {ADJUSTMENTS_SCHEMA}, "
            f"got {schema!r}"
        )
    adjustments = doc.get("adjustments") if isinstance(doc, dict) else None
    if not isinstance(adjustments, list):
        raise ValueError(
            f"{ADJUSTMENTS_FILENAME}: 'adjustments' must be a list"
        )
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
    recorded_ids = _load_recorded_ids(findings)
    already_recorded = set(recorded_ids)
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
        entry_state = _entry_state(entry, already_recorded)
        if entry_state == _SETTLED:
            if entry.get("rejected") is True:
                existing_id = entry.get("adjustment_id")
                if not (
                    isinstance(existing_id, str)
                    and existing_id in already_rejected_ids
                ):
                    newly_rejected.append(entry)
            continue
        if entry_state == _CATCH_UP:
            # The findings write landed; only the flag write was lost.
            catch_up.append(entry)
            continue
        label = f"adjustment[{idx}]"
        action = entry.get("action")
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
        recorded_ids.append(entry["adjustment_id"])
        batch_ids.append(entry["adjustment_id"])
        applied += 1

    if newly_allocated:
        # Ids only — the applied flags and this call's other bookkeeping
        # belong after the findings write, same crash-safety ordering as
        # the applied-ids record: a crash after this line but before the
        # findings write below still leaves the id resolvable on retry.
        atomic_write_json(adj_path, doc)

    if applied:
        _recount_summary(findings, issues)
        findings[APPLIED_IDS_KEY] = recorded_ids
        # Only the ids applied in THIS call: recorded_ids is cumulative
        # across every batch the ledger ever absorbed, and a second
        # withdrawal must name the decisions that caused it, not history.
        _withdraw_narrative_summary(findings, batch_ids)
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

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
stable `adjustment_id`, and the findings file lists the ids it already
contains under `applied_critic_adjustments`. Ids are allocated and
persisted before the findings write, so every crash point converges on
the next run: if the findings write landed, its recorded ids make the
entries skip and only their flags catch up; if it did not, nothing was
recorded and the batch applies normally. Without that record, a crash
between the two writes would re-apply patches onto an already-patched
ledger and `prior` would report the critic's own output as the
reconciled state.

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
    findings_path = os.path.join(output_dir, FINDINGS_FILENAME)
    if not os.path.isfile(findings_path):
        return set()
    try:
        with open(findings_path, "r", encoding="utf-8") as f:
            findings = json.load(f)
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(findings, dict):
        return set()
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


def apply_adjustments(output_dir):
    """Apply pending critic adjustments once; return a result dict.

    Idempotent from either side: an entry is skipped when its flag says
    it was applied or the orchestrator rejected it, and also when the
    findings file already records its `adjustment_id` — the case a crash
    between the two writes leaves behind, where only the flags need to
    catch up. Unknown ids, unknown actions, invalid patches, duplicate or
    already-removed targets, and a malformed pre-existing ledger fail the
    whole call loudly BEFORE anything is written — a critic decision that
    cannot land must not silently vanish, and a half-applied batch must
    not exist.

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
    adjustments = doc.get("adjustments") if isinstance(doc, dict) else None
    if not isinstance(adjustments, list):
        raise ValueError(
            f"{ADJUSTMENTS_FILENAME}: 'adjustments' must be a list"
        )
    with open(findings_path, "r", encoding="utf-8") as f:
        findings = json.load(f)
    # Same shape guard the adjustments file gets above: a findings file
    # that is not an object would otherwise die on an AttributeError
    # instead of this module's ValueError contract, and the step-11
    # caller catches only the latter — a malformed ledger would crash
    # finalize inside the guard meant to keep it running.
    if not isinstance(findings, dict):
        raise ValueError(f"{FINDINGS_FILENAME} must be a JSON object")
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

    # Validate every pending entry BEFORE mutating anything. The working
    # view tracks what each later entry in this batch can still address.
    pending = []
    catch_up = []
    seen_targets = {}
    removed_in_batch = {}
    for idx, entry in enumerate(adjustments):
        entry_state = _entry_state(entry, already_recorded)
        if entry_state == _SETTLED:
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
    # file keeps stays resolvable against this file after a crash.
    newly_allocated = False
    for entry, _action, _fields in pending:
        if not entry.get("adjustment_id"):
            entry["adjustment_id"] = uuid.uuid4().hex
            newly_allocated = True

    applied = 0
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
        applied += 1

    if applied:
        _recount_summary(findings, issues)
        findings[APPLIED_IDS_KEY] = recorded_ids
        if newly_allocated:
            # Ids only — the applied flags belong after the findings write.
            atomic_write_json(adj_path, doc)
        atomic_write_json(findings_path, findings)
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

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
validating first. Each file is replaced atomically (temp file in the
same directory, then os.replace — the convention in telemetry.py and
orchestration.py), and application is recorded on BOTH sides: every
pending entry carries a stable `adjustment_id`, and the findings file
lists the ids it already contains under `applied_critic_adjustments`.
Ids are allocated and persisted before the findings write, so every
crash point converges on the next run: if the findings write landed, its
recorded ids make the entries skip and only their flags catch up; if it
did not, nothing was recorded and the batch applies normally. Without
that record, a crash between the two writes would re-apply patches onto
an already-patched ledger and `prior` would report the critic's own
output as the reconciled state.
"""

import argparse
import json
import os
import sys
import tempfile
import uuid

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
APPLIED_IDS_KEY = "applied_critic_adjustments"


def _atomic_write_json(path, payload):
    """Replace a JSON artifact in one step, or leave the old one intact.

    Same temp-file-then-os.replace convention as telemetry.py's manifest
    and orchestration.py's dispatch plan. Failures propagate here — a
    ledger this module could not write must not read as a success.
    """
    directory = os.path.dirname(path) or "."
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=directory,
            encoding="utf-8",
        ) as temp_file:
            temp_path = temp_file.name
            json.dump(payload, temp_file, indent=2)
            temp_file.flush()
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


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
    """
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
        if entry.get("applied") is True or entry.get("rejected") is True:
            continue
        adjustment_id = entry.get("adjustment_id")
        if adjustment_id in already_recorded:
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
            _atomic_write_json(adj_path, doc)
        _atomic_write_json(findings_path, findings)
    if applied or catch_up:
        for entry, _action, _fields in pending:
            entry["applied"] = True
        for entry in catch_up:
            entry["applied"] = True
        _atomic_write_json(adj_path, doc)
    return {"status": "applied" if applied else "nothing_pending",
            "applied": applied}


def main():
    parser = argparse.ArgumentParser(
        description="Apply decision-critic adjustments to review-findings.json",
    )
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        result = apply_adjustments(args.output_dir)
    except (ValueError, OSError, json.JSONDecodeError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

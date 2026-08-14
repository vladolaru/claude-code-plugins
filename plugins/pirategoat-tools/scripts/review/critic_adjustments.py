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
"""

import argparse
import json
import os
import sys
import uuid

ACTIONS = ("promote", "demote", "rescope", "correct", "add", "remove")
PATCH_FIELDS = ("severity", "title", "description", "recommendation",
                "file", "line", "category", "confidence")
ADD_REQUIRED_FIELDS = ("severity", "title", "file", "description",
                       "recommendation")
VALID_SEVERITIES = ("critical", "high", "medium", "low", "info")

ADJUSTMENTS_FILENAME = "decision-critic-adjustments.json"
FINDINGS_FILENAME = "review-findings.json"


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


def apply_adjustments(output_dir):
    """Apply pending critic adjustments once; return a result dict.

    Idempotent: applied entries are flagged inside the adjustments file
    and skipped on re-run; entries the orchestrator marked rejected are
    never applied. Unknown ids, unknown actions, and invalid patches fail
    the whole call loudly BEFORE anything is written — a critic decision
    that cannot land must not silently vanish, and a half-applied batch
    must not exist.
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
    by_id = {i.get("id"): i for i in issues if isinstance(i, dict)}

    # Validate every pending entry BEFORE mutating anything.
    pending = []
    for idx, entry in enumerate(adjustments):
        if not isinstance(entry, dict):
            raise ValueError(f"adjustment[{idx}] must be an object")
        if entry.get("applied") is True or entry.get("rejected") is True:
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
        else:
            target_id = entry.get("id")
            if target_id not in by_id:
                raise ValueError(
                    f"{label}: no issue with id {target_id!r} in "
                    f"{FINDINGS_FILENAME}"
                )
        pending.append((entry, action, fields))

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
                **{k: fields[k] for k in fields},
                "critic_adjustment": provenance,
            }
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
            target["critic_adjustment"] = provenance
        entry["applied"] = True
        applied += 1

    if applied:
        sev = {s: 0 for s in VALID_SEVERITIES}
        for issue in issues:
            if issue.get("severity") in sev:
                sev[issue["severity"]] += 1
        summary = findings.setdefault("summary", {})
        summary["total_issues"] = len(issues)
        summary["by_severity"] = sev
        with open(findings_path, "w", encoding="utf-8") as f:
            json.dump(findings, f, indent=2)
        with open(adj_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
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

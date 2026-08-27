#!/usr/bin/env python3
"""The review document contract: what a review is, and whether one is valid.

One trust boundary, read by everything that opens a review artifact —
reviewer drafts and finals, the reconciliation ledger, the analysis
harness, the graders. It answers only shape questions, so it depends on
nothing but the vocabulary in `verdict_rules.py`: no file layout, no
lifecycle, no telemetry, no rendering. That is what lets `agent/output.py`
(the builder) and `critic_adjustments.py` (the post-critic ledger) both
validate through it without importing each other.
"""

import json
import os
import re
import sys
from datetime import datetime
from typing import Dict

try:
    from .verdict_rules import VALID_SEVERITIES, VERDICT_RANK, summary_for
except ImportError:
    _scripts_parent = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.verdict_rules import (
        VALID_SEVERITIES,
        VERDICT_RANK,
        summary_for,
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

VALID_CHANNELS = ('blocking', 'advisory')


REVIEW_CONTENT_FIELDS = frozenset({
    "pr_id",
    "timestamp",
    "plugin_version",
    "schema",
    "verdict",
    "summary",
    "findings",
    "observations",
    "recommendations",
    "positive_observations",
    "checks",
    "assessment",
    "meta",
})
REVIEWER_FIELDS = frozenset({
    "reviewer",
    "review_claimable_files",
    "reviewed_file_claims",
    "unclaimed_review_files",
    "inline_diff_file_count",
    "reviewed_file_count",
    "in_scope_review_file_count",
})

_OPTIONAL_REVIEW_FIELDS = frozenset({"skip_reason"})
REQUIRED_FINDING_FIELDS = frozenset({
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
REQUIRED_CHECK_FIELDS = frozenset({
    "id", "question", "method", "result", "source_reviewers",
})
_REQUIRED_META_FIELDS = frozenset({
    "review_duration_ms",
    "confidence_score",
    "next_finding_number",
    "next_check_number",
})
_OPTIONAL_META_FIELDS = frozenset()
_ALLOWED_META_FIELDS = _REQUIRED_META_FIELDS | _OPTIONAL_META_FIELDS


def _is_confidence(value):
    return type(value) in (int, float) and 0.0 <= value <= 1.0


def _is_string_list(value):
    return isinstance(value, list) and all(
        isinstance(item, str) for item in value
    )


def _canonical_id_number(value, prefix, label):
    if not isinstance(value, str) or not re.fullmatch(
        rf"{prefix}[1-9][0-9]*", value
    ):
        raise ValueError(f"{label}.id must be a canonical {prefix}N id")
    return int(value[1:])


def validate_finding_content_field(field, value, label):
    """Validate one critic-adjustable value against the review domain."""
    if field in (
        "category",
        "title",
        "description",
        "file",
        "recommendation",
    ):
        if not isinstance(value, str):
            raise ValueError(f"{label}.{field} must be a string")
        return
    if field == "severity":
        if value not in VALID_SEVERITIES:
            raise ValueError(f"{label}.severity is invalid")
        return
    if field == "confidence":
        if not _is_confidence(value):
            raise ValueError(f"{label}.confidence must be 0.0-1.0")
        return
    if field == "line" and value is not None and (
        type(value) is not int or value <= 0
    ):
        raise ValueError(
            f"{label}.line must be a positive (1-indexed) integer or null, "
            f"got {value!r}"
        )


def validate_finding_shape(finding, index):
    """Validate fields emitted by ``ReviewOutputBuilder.add_finding``."""
    if not isinstance(finding, dict):
        raise ValueError(f"review finding {index} must be an object")
    missing = sorted(REQUIRED_FINDING_FIELDS - set(finding))
    if missing:
        raise ValueError(
            f"review finding {index} is missing required fields: "
            + ", ".join(missing)
        )
    _canonical_id_number(finding["id"], "f", f"review finding {index}")
    for field in (
        "category",
        "severity",
        "title",
        "description",
        "file",
        "line",
        "recommendation",
        "confidence",
    ):
        validate_finding_content_field(
            field, finding[field], f"review finding {index}"
        )
    if "scope" in finding and finding["scope"] != "file":
        raise ValueError(
            f"review finding {index}.scope must be 'file'"
        )
    if (
        "severity_floor" in finding
        and finding["severity_floor"] not in VALID_SEVERITIES
    ):
        raise ValueError(
            f"review finding {index}.severity_floor is invalid"
        )
    if "channel" in finding and finding["channel"] not in VALID_CHANNELS:
        raise ValueError(
            f"review finding {index}.channel is invalid"
        )
    if (
        "behavior_evidence" in finding
        and finding["behavior_evidence"] not in ("cited", "inferred")
    ):
        raise ValueError(
            f"review finding {index}.behavior_evidence is invalid"
        )
    for field in ("code_snippet", "source_cited"):
        if field in finding and not isinstance(finding[field], str):
            raise ValueError(
                f"review finding {index}.{field} must be a string"
            )
    if (
        "references" in finding
        and not _is_string_list(finding["references"])
    ):
        raise ValueError(
            f"review finding {index}.references must be strings"
        )


def validate_check_shape(check, index):
    """Validate one canonical check without inferring materiality."""
    required = REQUIRED_CHECK_FIELDS
    allowed = required | {"critic_adjustment"}
    if not isinstance(check, dict):
        raise ValueError(f"review check {index} must be an object")
    if not required <= set(check) or not set(check) <= allowed:
        missing = sorted(required - set(check))
        unexpected = sorted(set(check) - allowed)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ValueError(
            f"review check {index} has invalid fields: " + "; ".join(details)
        )
    _canonical_id_number(check["id"], "c", f"review check {index}")
    for field in ("question", "method", "result"):
        if not isinstance(check[field], str) or not check[field].strip():
            raise ValueError(
                f"review check {index}.{field} must be a non-empty string"
            )
    sources = check["source_reviewers"]
    if (
        not isinstance(sources, list)
        or not sources
        or any(
            not isinstance(source, str) or not source.strip()
            for source in sources
        )
        or len(sources) != len(set(sources))
    ):
        raise ValueError(
            f"review check {index}.source_reviewers must be unique "
            "non-empty strings"
        )


def validate_ledger_ids(
    findings, checks, next_finding_number, next_check_number
):
    """Validate every finding and check, and the id counters above them.

    The counters are the reason this is one function rather than two
    loops: a `next_finding_number` at or below a live `fN` hands the next
    caller an id that already exists, and only a pass that has seen every
    id can say so. Callers pass the two numbers rather than the `meta`
    object they live in — the object's own shape is settled before this
    runs, and passing it invited a second copy of that check.
    """
    if not isinstance(findings, list):
        raise ValueError("review findings must be a list")
    finding_numbers = []
    for index, finding in enumerate(findings):
        validate_finding_shape(finding, index)
        finding_numbers.append(int(finding["id"][1:]))
    if len(finding_numbers) != len(set(finding_numbers)):
        raise ValueError("review finding ids must be unique")

    if not isinstance(checks, list):
        raise ValueError("review checks must be a list")
    check_numbers = []
    for index, check in enumerate(checks):
        validate_check_shape(check, index)
        check_numbers.append(int(check["id"][1:]))
    if len(check_numbers) != len(set(check_numbers)):
        raise ValueError("review check ids must be unique")

    for field, value, numbers in (
        ("next_finding_number", next_finding_number, finding_numbers),
        ("next_check_number", next_check_number, check_numbers),
    ):
        if type(value) is not int or value < 1:
            raise ValueError(f"review meta.{field} must be a positive integer")
        if numbers and value <= max(numbers):
            raise ValueError(
                f"review meta.{field} must be greater than every live id"
            )


def _validate_optional_review_fields(review):
    observations = review.get("observations")
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
        raise ValueError("review observations are malformed")

    recommendations = review.get("recommendations")
    if recommendations is not None and (
        not isinstance(recommendations, dict)
        or set(recommendations) != {"immediate", "important", "suggestions"}
        or any(
            not _is_string_list(recommendations.get(priority))
            for priority in ("immediate", "important", "suggestions")
        )
    ):
        raise ValueError("review recommendations are malformed")

    for field in ("positive_observations",):
        value = review.get(field)
        if value is not None and not _is_string_list(value):
            raise ValueError(f"review {field} must be strings or null")


def _validate_content_shape(document, *, schema):
    """Validate the review content shape before verdict derivation."""
    missing = sorted(REVIEW_CONTENT_FIELDS - set(document))
    if missing:
        raise ValueError(
            "review is missing content fields: " + ", ".join(missing)
        )
    unexpected = sorted(
        set(document) - REVIEW_CONTENT_FIELDS - _OPTIONAL_REVIEW_FIELDS
    )
    if unexpected:
        raise ValueError(
            "review has unexpected fields: " + ", ".join(unexpected)
        )
    if type(document["schema"]) is not int or document["schema"] != schema:
        raise ValueError("review schema does not match the live contract")
    if not isinstance(document["pr_id"], str):
        raise ValueError("review pr_id must be a string")
    if not isinstance(document["timestamp"], str):
        raise ValueError("review timestamp must be an ISO string")
    try:
        datetime.fromisoformat(document["timestamp"])
    except ValueError as exc:
        raise ValueError("review timestamp must be an ISO string") from exc
    if document["plugin_version"] is not None and not isinstance(
        document["plugin_version"], str
    ):
        raise ValueError("review plugin_version must be a string or null")
    if document["assessment"] is not None and not isinstance(
        document["assessment"], str
    ):
        raise ValueError("review assessment must be a string or null")

    meta = document["meta"]
    if not isinstance(meta, dict):
        raise ValueError("review meta must be an object")
    missing_meta = sorted(_REQUIRED_META_FIELDS - set(meta))
    if missing_meta:
        raise ValueError(
            "review meta is missing required fields: "
            + ", ".join(missing_meta)
        )
    unexpected_meta = sorted(set(meta) - _ALLOWED_META_FIELDS)
    if unexpected_meta:
        raise ValueError(
            "review meta has unexpected fields: "
            + ", ".join(unexpected_meta)
        )
    duration = meta["review_duration_ms"]
    if duration is not None and (type(duration) is not int or duration < 0):
        raise ValueError(
            "review meta.review_duration_ms must be non-negative or null"
        )
    if not _is_confidence(meta["confidence_score"]):
        raise ValueError(
            "review meta.confidence_score must be 0.0-1.0"
        )
    validate_ledger_ids(
        document["findings"],
        document["checks"],
        meta["next_finding_number"],
        meta["next_check_number"],
    )
    _validate_optional_review_fields(document)


def validate_review_content(document, *, schema):
    """Validate the review content shared by reviewer documents and the ledger."""
    if not isinstance(document, dict):
        raise ValueError("malformed review: expected an object")
    _validate_content_shape(document, schema=schema)

    findings = document["findings"]
    summary = document["summary"]
    if not isinstance(summary, dict):
        raise ValueError("review summary is malformed")
    try:
        derived = summary_for(findings)
    except ValueError as exc:
        raise ValueError(f"review findings are malformed: {exc}") from exc
    expected_verdict = derived["verdict"]
    if document.get("verdict") == "not_applicable":
        skip_reason = document.get("skip_reason")
        if (
            findings
            or not isinstance(skip_reason, str)
            or not skip_reason.strip()
        ):
            raise ValueError("review not_applicable verdict is malformed")
        expected_verdict = "not_applicable"
    elif "skip_reason" in document:
        raise ValueError(
            "review skip_reason requires a not_applicable verdict"
        )
    if document.get("verdict") != expected_verdict:
        raise ValueError("review verdict does not match its findings")
    expected_summary = derived["summary"]
    severity_counts = summary.get("by_severity")
    if (
        type(summary.get("total_findings")) is not int
        or not isinstance(severity_counts, dict)
        or set(severity_counts) != set(VALID_SEVERITIES)
        or any(
            type(severity_counts.get(severity)) is not int
            or severity_counts[severity] < 0
            for severity in VALID_SEVERITIES
        )
        or type(summary.get("suppressed_advisory_finding_count")) is not int
        or summary["suppressed_advisory_finding_count"] < 0
        or (
            "verdict_without_advisory" in summary
            and summary["verdict_without_advisory"] not in VERDICT_RANK
        )
        or summary != expected_summary
    ):
        raise ValueError("review summary does not match its findings")
    return document


def _validate_reviewer_envelope(review, reviewer):
    """Validate the reviewer envelope as a self-checking file partition."""
    missing = sorted(REVIEWER_FIELDS - set(review))
    if missing:
        raise ValueError(
            "review is missing reviewed-file fields: " + ", ".join(missing)
        )
    if not isinstance(review["reviewer"], str) or review["reviewer"] != reviewer:
        raise ValueError("review reviewer does not match finalization request")
    for field in (
        "review_claimable_files",
        "reviewed_file_claims",
        "unclaimed_review_files",
    ):
        if not _is_string_list(review[field]):
            raise ValueError(f"review {field} must be a list of strings")
    for field in (
        "inline_diff_file_count",
        "reviewed_file_count",
        "in_scope_review_file_count",
    ):
        if type(review[field]) is not int or review[field] < 0:
            raise ValueError(
                f"review {field} must be a non-negative integer"
            )
    claimable = review["review_claimable_files"]
    claims = review["reviewed_file_claims"]
    claimed = set(claims)
    if len(claimable) != len(set(claimable)) or len(claimed) != len(claims):
        raise ValueError("reviewed-file lists must not repeat paths")
    if not claimed <= set(claimable):
        raise ValueError(
            "reviewed-file claim names a file that is not review-claimable"
        )
    if claims != [path for path in claimable if path in claimed]:
        raise ValueError("reviewed-file claims are not in claimable order")
    if review["unclaimed_review_files"] != [
        path for path in claimable if path not in claimed
    ]:
        raise ValueError(
            "reviewed-file unclaimed files are not the complement of the claims"
        )
    if review["reviewed_file_count"] != (
        review["inline_diff_file_count"] + len(claimed)
    ):
        raise ValueError(
            "reviewed-file count does not equal inline plus claims"
        )
    if review["in_scope_review_file_count"] != (
        review["inline_diff_file_count"] + len(claimable)
    ):
        raise ValueError(
            "reviewed-file in-scope count does not equal inline plus claimable"
        )


def validate_review_document(review, reviewer):
    """Validate one complete reviewer document: content plus envelope.

    This is the shared trust boundary for draft rehydration, finalization,
    and finalized-review readers. Validation that depends on an adjacent
    assignment artifact remains in ``_validate_review``.
    """
    if not isinstance(review, dict):
        raise ValueError("malformed review: expected an object")
    _validate_reviewer_envelope(review, reviewer)
    content = {
        key: value for key, value in review.items() if key not in REVIEWER_FIELDS
    }
    validate_review_content(content, schema=REVIEW_OUTPUT_SCHEMA)
    return review


def load_review_document(path, reviewer):
    """Load and validate one canonical final-review document."""
    try:
        with open(path, "r", encoding="utf-8") as source:
            review = json.load(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed final review JSON") from exc
    return validate_review_document(review, reviewer)


def review_summary(document: Dict) -> Dict:
    """Project one validated review document's own summary.

    Every field here was computed by `derive_review_state()` when the
    document was built and re-derived and compared field-for-field by
    `validate_review_content()` when it was loaded, so `summary` is a
    proven fact about `findings` rather than a claim about them. Three
    consumers used to recount severities from the raw finding list with
    `finding.get('severity', 'medium')` — a default for a case the
    validator rejects — and they published only the severities that
    happened to be non-zero, so a manifest could not tell "no critical
    findings" from "critical was never counted". One projection, all five
    severities, no arithmetic.

    `verdict_without_advisory` is None unless advisory suppression
    actually softened the gating verdict; that is the same condition the
    document's own optional key encodes, carried rather than re-decided.
    """
    summary = document['summary']
    return {
        'verdict': document['verdict'],
        'finding_count': summary['total_findings'],
        'severities': dict(summary['by_severity']),
        'suppressed_advisory_finding_count': summary[
            'suppressed_advisory_finding_count'
        ],
        'verdict_without_advisory': summary.get('verdict_without_advisory'),
    }

"""Canonical finalized-review fixtures for consumer boundary tests."""

import json
from pathlib import Path

import pytest

from review.agent.review_assignment import ASSIGNMENT_SCHEMA
from review.reviewer_lifecycle import review_paths
from review.verdict_rules import derive_review_state


def canonical_review_document(
    reviewer="security",
    severities=(),
    *,
    reviewed_file_claims=(),
    review_claimable_files=(),
):
    """Return one complete schema-2 review with hand-derived expectations."""
    findings = [
        {
            "id": f"f{index}",
            "category": "general",
            "severity": severity,
            "title": f"{severity.title()} finding {index}",
            "description": "Observed behavior",
            "file": f"src/file-{index}.py",
            "line": index,
            "recommendation": "Correct the behavior",
            "confidence": 0.9,
        }
        for index, severity in enumerate(severities, 1)
    ]
    derived = derive_review_state(findings)

    claimable = list(review_claimable_files)
    claimed = list(reviewed_file_claims)
    unclaimed = [path for path in claimable if path not in set(claimed)]
    return {
        "pr_id": "42",
        "reviewer": reviewer,
        "timestamp": "2026-08-26T00:00:00+03:00",
        "plugin_version": None,
        "schema": 2,
        "verdict": derived["verdict"],
        "summary": {
            "total_findings": len(findings),
            "by_severity": derived["counts"],
            **derived["advisory"],
        },
        "findings": findings,
        "review_claimable_files": claimable,
        "reviewed_file_claims": claimed,
        "unclaimed_review_files": unclaimed,
        "inline_diff_file_count": 0,
        "reviewed_file_count": len(claimed),
        "in_scope_review_file_count": len(claimable),
        "observations": [],
        "recommendations": {
            "immediate": [], "important": [], "suggestions": [],
        },
        "positive_observations": [],
        "checks": [],
        "assessment": None,
        "meta": {
            "review_duration_ms": 10,
            "confidence_score": 0.9,
            "next_finding_number": len(findings) + 1,
            "next_check_number": 1,
        },
    }


def canonical_findings_ledger(severities=(), *, checks=(), reconciliation=None):
    """Return one exact schema-3 findings ledger."""
    document = canonical_review_document("reconciliator", severities)
    for field in (
        "reviewer", "review_claimable_files", "reviewed_file_claims",
        "unclaimed_review_files", "inline_diff_file_count",
        "reviewed_file_count", "in_scope_review_file_count",
    ):
        document.pop(field)
    document["schema"] = 3
    document["checks"] = list(checks)
    document["meta"]["next_check_number"] = len(checks) + 1
    count = len(document["findings"])
    document["meta"]["reconciliation"] = {
        "grouped_concern_count": count,
        "verified_concern_count": count,
        "false_positive_concern_count": 0,
        "out_of_scope_concern_count": 0,
        "input_finding_count": count,
        "contributing_agent_count": 1 if count else 0,
        "reviewing_agents": ["security-review"],
        "not_applicable_agents": [],
        "dispatched_agents": ["security-review"],
        "missing_agents": [],
        **(reconciliation or {}),
    }
    return document


def failing_findings_renderer(*messages):
    """A `render_markdown` stand-in that raises instead of rendering.

    `_render_findings_markdown` renders the ledger with `render_markdown`;
    `assemble_review_record` renders its body with `render_review_body`. A
    test that wants only the findings render to fail replaces exactly the
    first of those in `orchestration`. Each message is used for one call,
    and the last one repeats, so a caller can pin either a stable or a
    varying diagnostic.
    """
    remaining = list(messages) or ["boom"]

    def _raise(*_args, **_kwargs):
        message = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        raise RuntimeError(message)

    return _raise


# The sentinel for "the key is not there at all". A schema gate has to
# refuse an absent key the same way it refuses a wrong one, and no literal
# value can stand for absence — `None` is itself one of the wrong values.
SCHEMA_ABSENT = object()


def rejected_schema_values(correct):
    """Every non-canonical spelling one schema gate must refuse.

    One value set for every artifact boundary — assignment (4), findings
    ledger (3), reviewer document (2), critic proposal (2), reconciliation
    context (3) — so a boundary that grows a reader cannot quietly accept a
    spelling its siblings refuse. The bool is here because `True == 1`:
    a gate written as `!= expected` lets it through at the schema-1
    neighbour, and every gate in this plugin is written as an `int`-typed
    identity check for exactly that reason.
    """
    return [
        pytest.param(correct - 1, id="prior-schema"),
        pytest.param(correct + 1, id="future-schema"),
        pytest.param(str(correct), id="numeric-string"),
        pytest.param(True, id="bool"),
        pytest.param(None, id="null"),
        pytest.param({}, id="non-scalar"),
        pytest.param(SCHEMA_ABSENT, id="absent"),
    ]


def apply_schema(document, value):
    """Set or remove a document's `schema` field for a gate test."""
    if value is SCHEMA_ABSENT:
        document.pop("schema", None)
    else:
        document["schema"] = value
    return document


def canonical_assignment(
    reviewer="code",
    *,
    agent_name=None,
    review_claimable_files=(),
    inline_diff_files=(),
    inline_diff_file_count=None,
    in_scope_review_file_count=None,
    review_budget=15,
    channels=("blocking",),
):
    """The one schema-5 assignment sidecar payload the test estate writes.

    `in_scope_review_file_count` defaults to the conserved value the
    production validator requires (`inline + claimable`), so a fixture has
    to opt in to incoherence rather than stumble into it. A test that only
    cares how MANY files were inline passes `inline_diff_file_count` and
    gets that many placeholder paths; one that cares WHICH passes
    `inline_diff_files`.
    """
    claimable = list(review_claimable_files)
    inline = list(inline_diff_files)
    if inline_diff_file_count is not None:
        if inline:
            raise ValueError("pass inline_diff_files or inline_diff_file_count, not both")
        inline = [f"inline/file-{n}.txt" for n in range(inline_diff_file_count)]
    if in_scope_review_file_count is None:
        in_scope_review_file_count = len(inline) + len(claimable)
    return {
        "schema": ASSIGNMENT_SCHEMA,
        "agent_name": agent_name or f"{reviewer}-reviewer",
        "reviewer": reviewer,
        "review_claimable_files": claimable,
        "inline_diff_files": inline,
        "in_scope_review_file_count": in_scope_review_file_count,
        "review_budget": review_budget,
        "channels": list(channels),
    }


def write_canonical_assignment(path_or_dir, reviewer="code", **overrides):
    """Write `canonical_assignment(...)` to a ReviewPaths or output dir."""
    path = (
        path_or_dir.assignment
        if hasattr(path_or_dir, "assignment")
        else review_paths(str(path_or_dir), reviewer).assignment
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(canonical_assignment(reviewer, **overrides))
    )
    return path

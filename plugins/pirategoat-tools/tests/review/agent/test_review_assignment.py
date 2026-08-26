"""Tests for the reviewer assignment and its reviewed-file derivation."""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review.agent.review_assignment import (
    ReviewAssignmentError,
    derive_reviewed_files,
)
from review.reviewer_lifecycle import review_paths


def _assignment(**overrides):
    payload = {
        "schema": 4,
        "agent_name": "code-reviewer",
        "reviewer": "code",
        "review_claimable_files": ["src/b.py", "src/c.py", "src/d.py"],
        "inline_diff_file_count": 2,
        "in_scope_review_file_count": 5,
        "review_budget": 15,
        "channels": ["blocking"],
    }
    payload.update(overrides)
    return payload


def test_derives_reviewed_files_from_normalized_claims_in_authoritative_order():
    reviewed_files = derive_reviewed_files(
        _assignment(), ["./src/d.py", "src/b.py", "src/b.py"]
    )

    assert reviewed_files.agent_name == "code-reviewer"
    assert reviewed_files.reviewer == "code"
    assert reviewed_files.review_claimable_files == (
        "src/b.py",
        "src/c.py",
        "src/d.py",
    )
    assert reviewed_files.reviewed_file_claims == ("src/b.py", "src/d.py")
    assert reviewed_files.unclaimed_review_files == ("src/c.py",)
    assert reviewed_files.inline_diff_file_count == 2
    assert reviewed_files.reviewed_file_count == 4
    assert reviewed_files.in_scope_review_file_count == 5


def test_rejects_claim_outside_review_claimable_files_as_one_batch():
    with pytest.raises(
        ReviewAssignmentError,
        match=r"not review-claimable.*src/other.py.*src/second.py",
    ):
        derive_reviewed_files(
            _assignment(
                review_claimable_files=["src/b.py"],
                inline_diff_file_count=1,
                in_scope_review_file_count=2,
            ),
            ["src/other.py", "src/second.py"],
        )


def test_empty_claimable_set_keeps_all_inline_files_accounted_for():
    reviewed_files = derive_reviewed_files(
        _assignment(
            review_claimable_files=[],
            inline_diff_file_count=3,
            in_scope_review_file_count=3,
        ),
        [],
    )

    assert reviewed_files.reviewed_file_claims == ()
    assert reviewed_files.unclaimed_review_files == ()
    assert reviewed_files.reviewed_file_count == 3


def test_normalizes_authoritative_paths_before_rejecting_duplicates():
    with pytest.raises(ReviewAssignmentError, match="must not contain duplicates"):
        derive_reviewed_files(
            _assignment(
                review_claimable_files=["src/b.py", "./src/b.py"],
                inline_diff_file_count=1,
                in_scope_review_file_count=3,
            ),
            [],
        )


@pytest.mark.parametrize(
    "payload, message",
    [
        ({}, "schema"),
        (_assignment(schema=2), "schema"),
        (_assignment(agent_name=""), "agent_name"),
        (_assignment(reviewer="code-reviewer"), "identity"),
        (
            _assignment(
                agent_name="repo-renewals-reviewer", reviewer="repo-renewals"
            ),
            None,
        ),
        (_assignment(review_claimable_files=["src/b.py", 3]), "string-only"),
        (_assignment(inline_diff_file_count=-1), "inline_diff_file_count"),
        (
            _assignment(in_scope_review_file_count=True),
            "in_scope_review_file_count",
        ),
        (_assignment(review_budget=True), "review_budget"),
        (
            _assignment(
                inline_diff_file_count=1,
                in_scope_review_file_count=5,
            ),
            "incoherent",
        ),
    ],
)
def test_validates_schema_identity_paths_and_conserved_counts(payload, message):
    if message is None:
        reviewed_files = derive_reviewed_files(payload, [])
        assert reviewed_files.agent_name == "repo-renewals-reviewer"
        assert reviewed_files.reviewer == "repo-renewals"
        return

    with pytest.raises(ReviewAssignmentError, match=message):
        derive_reviewed_files(payload, [])


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../src/b.py", "src/../b.py", "C:\\src\\b.py", "."],
)
@pytest.mark.parametrize("location", ["review_claimable_files", "claim"])
def test_rejects_paths_outside_repository_relative_grammar(path, location):
    payload = _assignment()
    claims = []
    if location == "review_claimable_files":
        payload["review_claimable_files"] = [path]
        payload["in_scope_review_file_count"] = 3
    else:
        claims = [path]

    with pytest.raises(ReviewAssignmentError):
        derive_reviewed_files(payload, claims)


def test_rejects_non_object_input_and_non_iterable_claims():
    with pytest.raises(ReviewAssignmentError, match="must be an object"):
        derive_reviewed_files([], [])
    with pytest.raises(ReviewAssignmentError, match="claims must be iterable"):
        derive_reviewed_files(_assignment(), None)


def _input(**overrides):
    payload = {
        "schema": 4,
        "agent_name": "security-reviewer",
        "reviewer": "security",
        "review_claimable_files": ["src/a.py"],
        "inline_diff_file_count": 1,
        "in_scope_review_file_count": 2,
        "review_budget": 15,
        "channels": ["blocking"],
    }
    payload.update(overrides)
    return payload


def test_assignment_carries_budget_and_channels():
    reviewed_files = derive_reviewed_files(_input(channels=["blocking", "advisory"]), [])
    assert reviewed_files.review_budget == 15
    assert reviewed_files.channels == ("blocking", "advisory")


@pytest.mark.parametrize("overrides", [
    {"schema": 3},
    {"channels": "blocking"},
    {"channels": []},
    {"channels": ["blocking", "blocking"]},
    {"channels": ["gating"]},
    {"review_budget": -1},
    {"review_budget": True},
])
def test_schema_four_rejects_retired_or_malformed_input(overrides):
    with pytest.raises(ReviewAssignmentError):
        derive_reviewed_files(_input(**overrides), [])


def test_accounting_vocabulary_is_retired():
    scripts = PLUGIN_ROOT / "scripts"
    offenders = []
    for path in scripts.rglob("*.py"):
        text = path.read_text()
        for needle in (
            "review" + "_accounting",
            "Review" + "Accounting",
            "reviewed_files" + "_input",
            "review" + "_accounted_file_count",
            "reviewed_files" + "-input",
        ):
            if needle in text:
                offenders.append(f"{path.relative_to(PLUGIN_ROOT)}: {needle}")
    assert offenders == []


def test_assignment_sidecar_path():
    paths = review_paths("/out", "security")
    assert paths.assignment == "/out/security-assignment.json"
    assert not hasattr(paths, "reviewed_files" + "_input")

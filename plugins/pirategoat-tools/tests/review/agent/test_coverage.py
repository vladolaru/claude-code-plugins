"""Tests for authoritative reviewed-file accounting derivation."""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review.agent.coverage import (
    ReviewAccountingError,
    derive_review_accounting,
)


def _accounting_input(**overrides):
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


def test_derives_review_accounting_from_normalized_claims_in_authoritative_order():
    accounting = derive_review_accounting(
        _accounting_input(), ["./src/d.py", "src/b.py", "src/b.py"]
    )

    assert accounting.agent_name == "code-reviewer"
    assert accounting.reviewer == "code"
    assert accounting.review_claimable_files == (
        "src/b.py",
        "src/c.py",
        "src/d.py",
    )
    assert accounting.reviewed_file_claims == ("src/b.py", "src/d.py")
    assert accounting.unclaimed_review_files == ("src/c.py",)
    assert accounting.inline_diff_file_count == 2
    assert accounting.review_accounted_file_count == 4
    assert accounting.in_scope_review_file_count == 5


def test_rejects_claim_outside_review_claimable_files_as_one_batch():
    with pytest.raises(
        ReviewAccountingError,
        match=r"not review-claimable.*src/other.py.*src/second.py",
    ):
        derive_review_accounting(
            _accounting_input(
                review_claimable_files=["src/b.py"],
                inline_diff_file_count=1,
                in_scope_review_file_count=2,
            ),
            ["src/other.py", "src/second.py"],
        )


def test_empty_claimable_set_keeps_all_inline_files_accounted_for():
    accounting = derive_review_accounting(
        _accounting_input(
            review_claimable_files=[],
            inline_diff_file_count=3,
            in_scope_review_file_count=3,
        ),
        [],
    )

    assert accounting.reviewed_file_claims == ()
    assert accounting.unclaimed_review_files == ()
    assert accounting.review_accounted_file_count == 3


def test_normalizes_authoritative_paths_before_rejecting_duplicates():
    with pytest.raises(ReviewAccountingError, match="must not contain duplicates"):
        derive_review_accounting(
            _accounting_input(
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
        (_accounting_input(schema=2), "schema"),
        (_accounting_input(agent_name=""), "agent_name"),
        (_accounting_input(reviewer="code-reviewer"), "identity"),
        (
            _accounting_input(
                agent_name="repo-renewals-reviewer", reviewer="repo-renewals"
            ),
            None,
        ),
        (_accounting_input(review_claimable_files=["src/b.py", 3]), "string-only"),
        (_accounting_input(inline_diff_file_count=-1), "inline_diff_file_count"),
        (
            _accounting_input(in_scope_review_file_count=True),
            "in_scope_review_file_count",
        ),
        (_accounting_input(review_budget=True), "review_budget"),
        (
            _accounting_input(
                inline_diff_file_count=1,
                in_scope_review_file_count=5,
            ),
            "incoherent",
        ),
    ],
)
def test_validates_schema_identity_paths_and_conserved_counts(payload, message):
    if message is None:
        accounting = derive_review_accounting(payload, [])
        assert accounting.agent_name == "repo-renewals-reviewer"
        assert accounting.reviewer == "repo-renewals"
        return

    with pytest.raises(ReviewAccountingError, match=message):
        derive_review_accounting(payload, [])


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../src/b.py", "src/../b.py", "C:\\src\\b.py", "."],
)
@pytest.mark.parametrize("location", ["review_claimable_files", "claim"])
def test_rejects_paths_outside_repository_relative_grammar(path, location):
    payload = _accounting_input()
    claims = []
    if location == "review_claimable_files":
        payload["review_claimable_files"] = [path]
        payload["in_scope_review_file_count"] = 3
    else:
        claims = [path]

    with pytest.raises(ReviewAccountingError):
        derive_review_accounting(payload, claims)


def test_rejects_non_object_input_and_non_iterable_claims():
    with pytest.raises(ReviewAccountingError, match="must be an object"):
        derive_review_accounting([], [])
    with pytest.raises(ReviewAccountingError, match="claims must be iterable"):
        derive_review_accounting(_accounting_input(), None)


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


def test_accounting_carries_budget_and_channels():
    accounting = derive_review_accounting(_input(channels=["blocking", "advisory"]), [])
    assert accounting.review_budget == 15
    assert accounting.channels == ("blocking", "advisory")


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
    with pytest.raises(ReviewAccountingError):
        derive_review_accounting(_input(**overrides), [])

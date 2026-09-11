"""Tests for review/review_document.py's validate_review_document.

Raw-dict harness: builds one structurally valid finalized-review document
with `canonical_review_document` (no file round-trip) and calls
`validate_review_document` directly. These rows used to live only in
grading/test_graders.py's TestGradeReviewJson, pinning the validator
through `grade_review_json` — one layer removed from the code they were
actually pinning. `grading/test_graders.py` keeps one test proving a
validator ValueError becomes a failed GradeResult; every other validator
branch is pinned here, beside the module it validates.
"""

import pytest

from helpers.review_fixtures import canonical_review_document
from review.review_document import validate_review_document


def _valid_document(reviewer="security"):
    """One structurally valid finalized-review document with a finding and
    a check, ready to mutate into a rejection case."""
    claimable = ["src/User.php"]
    document = canonical_review_document(
        reviewer,
        ["high"],
        reviewed_file_claims=claimable,
        review_claimable_files=claimable,
    )
    document["checks"] = [{
        "id": "c1",
        "question": "Can request input reach the query?",
        "method": "Trace the request handler into the database call",
        "result": "Yes; the value reaches the query without parameterization.",
        "source_reviewers": [reviewer],
    }]
    document["meta"]["next_check_number"] = 2
    return document


@pytest.mark.parametrize(
    ("malformation", "diagnostic"),
    [
        ("numeric-summary", "review summary is malformed"),
        ("non-object-finding", "review finding 0 must be an object"),
        ("non-list-checks", "review checks must be a list"),
        (
            "non-list-reviewed-files",
            "review reviewed_file_claims must be a list of strings",
        ),
        (
            "retired-schema-and-field",
            "review has unexpected fields: issues",
        ),
    ],
)
def test_canonical_rejection_stops_invalid_document_projection(malformation, diagnostic):
    data = _valid_document()
    if malformation == "numeric-summary":
        data["summary"] = 7
    elif malformation == "non-object-finding":
        data["findings"] = [7]
    elif malformation == "non-list-checks":
        data["checks"] = 7
    elif malformation == "non-list-reviewed-files":
        data["reviewed_file_claims"] = 7
    else:
        data["schema"] = 1
        data["issues"] = []

    with pytest.raises(ValueError) as exc:
        validate_review_document(data, "security")
    assert str(exc.value) == diagnostic


@pytest.mark.parametrize(
    ("population", "bad_id", "message"),
    [
        ("findings", "f01", "canonical fN id"),
        ("checks", "c01", "canonical cN id"),
    ],
)
def test_noncanonical_review_domain_id_fails(population, bad_id, message):
    data = _valid_document()
    data[population][0]["id"] = bad_id

    with pytest.raises(ValueError) as exc:
        validate_review_document(data, "security")
    assert message in str(exc.value)


@pytest.mark.parametrize(
    ("population", "summary_delta", "message"),
    [
        ("findings", 1, "review finding ids must be unique"),
        ("checks", 0, "review check ids must be unique"),
    ],
)
def test_duplicate_review_domain_id_fails(population, summary_delta, message):
    data = _valid_document()
    data[population].append(dict(data[population][0]))
    data["summary"]["total_findings"] += summary_delta
    if summary_delta:
        data["summary"]["by_severity"]["high"] += summary_delta

    with pytest.raises(ValueError) as exc:
        validate_review_document(data, "security")
    assert message in str(exc.value)


@pytest.mark.parametrize(
    ("counter", "message"),
    [
        (
            "next_finding_number",
            "review meta.next_finding_number must be greater than every live id",
        ),
        (
            "next_check_number",
            "review meta.next_check_number must be greater than every live id",
        ),
    ],
)
def test_next_counter_must_exceed_every_live_id(counter, message):
    data = _valid_document()
    data["meta"][counter] = 1

    with pytest.raises(ValueError) as exc:
        validate_review_document(data, "security")
    assert message in str(exc.value)


def test_unexpected_retired_field_fails():
    data = _valid_document()
    data["issues"] = []

    with pytest.raises(ValueError) as exc:
        validate_review_document(data, "security")
    assert "unexpected fields: issues" in str(exc.value)


def test_finding_with_invalid_severity_fails():
    data = _valid_document()
    data["findings"][0]["severity"] = "unknown"

    with pytest.raises(ValueError) as exc:
        validate_review_document(data, "security")
    assert str(exc.value) == "review finding 0.severity is invalid"


def test_invalid_verdict_fails():
    data = _valid_document()
    data["verdict"] = "INVALID_VERDICT"

    with pytest.raises(ValueError) as exc:
        validate_review_document(data, "security")
    assert str(exc.value) == "review verdict does not match its findings"

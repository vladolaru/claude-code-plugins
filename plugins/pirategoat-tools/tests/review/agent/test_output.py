"""
Tests for ReviewOutputBuilder — direct unit tests on the producer API.

Validates the structured review output builder that all reviewer agents use
to emit findings. Tests cover initialization, finding addition with validation,
recommendations, verdicts, serialization (dict, JSON, markdown), and file output.

Zero external dependencies beyond stdlib + pytest.
"""

import json
import hashlib
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import ReviewOutputBuilder from scripts/
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent.parent.parent  # agent/ -> review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review import review_document
from review.agent import output as review_output
from review.agent.output import (
    ReviewOutputBuilder,
    reviewed_files_fields,
    finalize_review,
    render_draft_index,
)
from review.review_document import (
    REVIEW_CONTENT_FIELDS,
    REVIEWER_FIELDS,
    review_summary,
    validate_review_content,
    validate_review_document,
)
from review.review_markdown import render_markdown
from review import critic_adjustments
from review.reviewer_lifecycle import ReviewPaths, review_paths

sys.path.insert(0, str(TESTS_DIR))
from helpers.review_fixtures import (
    apply_schema,
    canonical_assignment,
    canonical_review_document,
    write_canonical_assignment,
)


def _write_required_assignment(output_dir, reviewer):
    write_canonical_assignment(output_dir, reviewer)


def _save_draft(builder, output_dir):
    """Bind direct-constructor unit fixtures to the canonical draft save."""
    if builder._output_dir is None:
        builder._bind(str(output_dir), base_digest=None)
    return builder.save_draft()


def test_assignment_reads_follow_the_bound_review_paths(
    tmp_path, monkeypatch, capsys
):
    authority_dir = tmp_path / "authority"
    authority_dir.mkdir()
    paths = ReviewPaths(
        draft=str(authority_dir / "draft.json"),
        final=str(authority_dir / "final.json"),
        assignment=str(authority_dir / "authority.json"),
    )
    Path(paths.assignment).write_text(json.dumps(canonical_assignment(
        "code", agent_name="code-reviewer",
        review_claimable_files=["src/unread.py"], review_budget=80,
    )))
    monkeypatch.setattr(review_output, "review_paths", lambda *_args: paths)

    saved = ReviewOutputBuilder.open(tmp_path, "42", "code").save_draft()

    assert saved["draft"] == paths.draft
    assert Path(paths.draft).is_file()
    assert "target ~80 tool calls" in capsys.readouterr().out


def _write_assignment(paths_or_dir, reviewer="security", claimable=("src/a.py",), *, channels=("blocking",), budget=12):
    write_canonical_assignment(
        paths_or_dir, reviewer, review_claimable_files=claimable,
        channels=channels, review_budget=budget,
        in_scope_review_file_count=len(claimable),
    )


def test_builder_ignores_env_envelope_and_uses_bound_input(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    _write_assignment(other, claimable=("src/only-in-env.py",))
    monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(other))
    monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "security")
    _write_assignment(tmp_path, claimable=("src/bound.py",))
    builder = ReviewOutputBuilder.open(tmp_path, "42", "security")
    builder.claim_files_reviewed("src/bound.py")
    with pytest.raises(ValueError, match="src/only-in-env.py"):
        builder.claim_files_reviewed("src/only-in-env.py")


def test_finding_channel_must_be_among_the_reviewer_channels(tmp_path, monkeypatch):
    monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
    _write_assignment(tmp_path, channels=("blocking",))
    builder = ReviewOutputBuilder.open(tmp_path, "42", "security")
    with pytest.raises(ValueError, match="channel 'advisory' is not among"):
        builder.add_finding("low", "t", "src/a.py", "d", "r", line=1, channel="advisory")
    _write_assignment(tmp_path, channels=("blocking", "advisory"))
    both = ReviewOutputBuilder.open(tmp_path, "42", "security")
    both.add_finding("low", "t", "src/a.py", "d", "r", line=1, channel="advisory")
    assert both.findings[0]["channel"] == "advisory"
    _write_assignment(tmp_path, channels=("advisory",))
    advisory_only = ReviewOutputBuilder.open(tmp_path, "42", "security")
    with pytest.raises(ValueError, match="channel 'blocking' is not among"):
        advisory_only.add_finding("low", "t", "src/a.py", "d", "r", line=1)


def test_receipt_budget_line_reads_bound_input(tmp_path, capsys):
    _write_assignment(tmp_path, claimable=("src/a.py", "src/b.py"), budget=33)
    ReviewOutputBuilder.open(tmp_path, "42", "security").save_draft()
    assert "target ~33 tool calls" in capsys.readouterr().out


def test_draft_index_carries_locations_and_every_reviewed_file_claim():
    builder = ReviewOutputBuilder(pr_id="42", reviewer="security")
    builder.add_finding(
        "high", "Missing authorization", "src/auth.py", "d", "r", line=42
    )
    builder.add_finding(
        "medium", "Missing coverage", "tests/test_auth.py", "d", "r",
        line=None,
    )
    builder.reviewed_file_claims = ["src/service.py", "tests/test_service.py"]

    # render_draft_index is always called on a persisted draft file, whose
    # reviewed-file fields to_dict() no longer carries — stitch the claims on
    # to match what save_draft() would have written.
    index = render_draft_index({
        **builder.to_dict(),
        "reviewed_file_claims": builder.reviewed_file_claims,
    })

    assert 'finding f1: high "Missing authorization" @ src/auth.py:42' in index
    assert (
        'finding f2: medium "Missing coverage" '
        '@ tests/test_auth.py (file scope)' in index
    )
    assert "reviewed-file claims 2" in index
    assert "reviewed-file claim: src/service.py" in index
    assert "reviewed-file claim: tests/test_service.py" in index


def test_draft_index_reports_zero_claims_without_claim_entries():
    index = render_draft_index(
        ReviewOutputBuilder(pr_id="42", reviewer="security").to_dict()
    )

    assert "reviewed-file claims 0" in index
    assert "reviewed-file claim:" not in index


# =============================================================================
# TestFindingAndCheckDomainModel
# =============================================================================


class TestFindingAndCheckDomainModel:
    """Schema-2 drafts expose only the canonical review-domain contract."""

    def test_ids_are_monotonic_and_removed_ids_are_not_reused(self, tmp_path):
        _write_required_assignment(tmp_path, "security")
        builder = ReviewOutputBuilder.open(tmp_path, "42", "security")

        assert builder.add_finding(
            severity="high",
            title="First",
            file="src/a.py",
            description="A verified defect.",
            recommendation="Correct it.",
            line=10,
        ) == "f1"
        assert builder.record_check(
            "Can input reach SQL?", "Read callers", "Yes"
        ) == "c1"
        builder.remove_finding("f1")
        builder.remove_check("c1")
        builder.save_draft()

        reopened = ReviewOutputBuilder.open(tmp_path, "42", "security")
        assert reopened.add_finding(
            severity="medium",
            title="Second",
            file="src/b.py",
            description="Another verified defect.",
            recommendation="Correct this one too.",
            line=20,
        ) == "f2"
        assert reopened.record_check(
            "Does the fallback still run?", "Read the branch", "No"
        ) == "c2"
        review = reopened.to_dict()
        assert review["meta"]["next_finding_number"] == 3
        assert review["meta"]["next_check_number"] == 3

    def test_updates_preserve_ids_and_check_sources(self):
        builder = ReviewOutputBuilder("42", "security")
        finding_id = builder.add_finding(
            severity="medium",
            title="Original",
            file="src/a.py",
            description="Original description.",
            recommendation="Original recommendation.",
            line=10,
        )
        check_id = builder.record_check(
            "Does the caller validate?", "Read the caller", "Not yet"
        )

        builder.update_finding(
            finding_id, severity="high", title="Updated"
        )
        builder.update_check(check_id, result="Yes")

        review = builder.to_dict()
        assert review["findings"][0]["id"] == finding_id
        assert review["findings"][0]["severity"] == "high"
        assert review["findings"][0]["title"] == "Updated"
        assert review["checks"][0] == {
            "id": check_id,
            "question": "Does the caller validate?",
            "method": "Read the caller",
            "result": "Yes",
            "source_reviewers": ["security"],
        }

    @pytest.mark.parametrize(
        ("method", "entry_id", "patch", "match"),
        [
            ("update_finding", "f1", {"id": "f9"}, "cannot update field"),
            ("update_finding", "f1", {"unknown": "x"}, "cannot update field"),
            ("update_check", "c1", {"id": "c9"}, "cannot update field"),
            (
                "update_check",
                "c1",
                {"source_reviewers": ["other"]},
                "cannot update field",
            ),
        ],
    )
    def test_mutations_reject_immutable_or_unknown_patch_fields(
        self, method, entry_id, patch, match
    ):
        builder = ReviewOutputBuilder("42", "security")
        builder.add_finding(
            severity="medium",
            title="Original",
            file="src/a.py",
            description="Original description.",
            recommendation="Original recommendation.",
            line=10,
        )
        builder.record_check(
            "Does the caller validate?", "Read the caller", "Not yet"
        )
        before = builder.to_dict()

        with pytest.raises(ValueError, match=match):
            getattr(builder, method)(entry_id, **patch)

        assert builder.to_dict() == before

    @pytest.mark.parametrize(
        ("method", "entry_id", "patch"),
        [
            ("update_finding", "f99", {"title": "Missing"}),
            ("remove_finding", "f99", None),
            ("update_check", "c99", {"result": "Missing"}),
            ("remove_check", "c99", None),
        ],
    )
    def test_unknown_id_rejection_is_atomic(self, method, entry_id, patch):
        builder = ReviewOutputBuilder("42", "security")
        builder.add_finding(
            severity="medium",
            title="Original",
            file="src/a.py",
            description="Original description.",
            recommendation="Original recommendation.",
            line=10,
        )
        builder.record_check(
            "Does the caller validate?", "Read the caller", "Not yet"
        )
        before = builder.to_dict()

        with pytest.raises(ValueError, match="unknown"):
            if patch is None:
                getattr(builder, method)(entry_id)
            else:
                getattr(builder, method)(entry_id, **patch)

        assert builder.to_dict() == before

    def test_schema_has_checks_assessment_and_no_tool_metadata(self):
        review = ReviewOutputBuilder("42", "security").to_dict()

        assert review["findings"] == []
        assert review["checks"] == []
        assert review["assessment"] is None
        assert "issues" not in review
        assert "clearances" not in review
        assert "narrative_summary" not in review
        assert "tool_results_used" not in review["meta"]

    def test_assessment_and_positive_observation_use_canonical_methods(self):
        builder = ReviewOutputBuilder("42", "reconciliator")

        builder.set_assessment("The change needs one correction.")
        builder.add_positive_observation("The validation helper is clear.")

        review = builder.to_dict()
        assert review["assessment"] == "The change needs one correction."
        assert review["positive_observations"] == [
            "The validation helper is clear."
        ]
        for retired in (
            "add_issue",
            "add_clearance",
            "set_narrative_summary",
            "add_positive",
            "add_tool_result",
        ):
            assert not hasattr(builder, retired)

    def test_not_applicable_records_only_the_reason(self):
        builder = ReviewOutputBuilder("42", "security")

        builder.mark_not_applicable("No security-relevant files changed.")

        review = builder.to_dict()
        assert review["verdict"] == "not_applicable"
        assert review["skip_reason"] == "No security-relevant files changed."
        assert review["findings"] == []
        assert review["checks"] == []
        assert review["positive_observations"] == []


# =============================================================================
# TestAddFinding
# =============================================================================


class TestAddFinding:
    """add_finding validates inputs, stores all fields, and returns an ID."""

    def test_returns_canonical_id(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        finding_id = b.add_finding("high", "Title", "f.py", "desc", "rec", line=1)
        assert finding_id == "f1"

    def test_stores_all_fields(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding(
            severity="high",
            title="SQL Injection",
            file="src/db.php",
            description="Direct input in query",
            recommendation="Use prepared statements",
            category="sql-injection",
            line=42,
            confidence=0.9,
        )
        finding = b.findings[0]
        assert finding["severity"] == "high"
        assert finding["title"] == "SQL Injection"
        assert finding["file"] == "src/db.php"
        assert finding["description"] == "Direct input in query"
        assert finding["recommendation"] == "Use prepared statements"
        assert finding["category"] == "sql-injection"
        assert finding["line"] == 42
        assert finding["confidence"] == 0.9

    def test_severity_case_insensitive(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding("HIGH", "Title", "f.py", "desc", "rec", line=1)
        assert b.findings[0]["severity"] == "high"

    def test_invalid_severity_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="Invalid severity"):
            b.add_finding("urgent", "Title", "f.py", "desc", "rec", line=1)

    @pytest.mark.parametrize(
        ("severity", "floor", "expected"),
        [
            pytest.param("low", "medium", "medium", id="promotes-to-floor"),
            pytest.param("medium", "medium", "medium", id="equal-to-floor"),
            pytest.param("critical", "medium", "critical", id="above-floor"),
            pytest.param("MEDIUM", "HIGH", "high", id="case-insensitive"),
        ],
    )
    def test_severity_floor_is_serialized_and_enforced(
        self, severity, floor, expected
    ):
        b = ReviewOutputBuilder(pr_id="1", reviewer="woo-regression")

        b.add_finding(
            severity,
            "Title",
            "f.php",
            "desc",
            "rec",
            line=1,
            severity_floor=floor,
        )

        finding = b.findings[0]
        assert finding["severity"] == expected
        assert finding["severity_floor"] == floor.lower()

    @pytest.mark.parametrize(
        "floor",
        [
            pytest.param("urgent", id="unknown-name"),
            pytest.param(3, id="non-string"),
        ],
    )
    def test_invalid_severity_floor_raises(self, floor):
        b = ReviewOutputBuilder(pr_id="1", reviewer="woo-regression")

        with pytest.raises(ValueError, match="severity_floor"):
            b.add_finding(
                "medium",
                "Title",
                "f.php",
                "desc",
                "rec",
                line=1,
                severity_floor=floor,
            )

    def test_severity_floor_is_optional(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")

        b.add_finding("medium", "Title", "f.php", "desc", "rec", line=1)

        assert "severity_floor" not in b.findings[0]

    def test_markdown_renders_severity_floor(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="woo-regression")
        b.add_finding(
            "medium",
            "Title",
            "f.php",
            "desc",
            "rec",
            line=1,
            severity_floor="medium",
        )

        assert "**Severity floor:** medium" in render_markdown(b.to_dict())

    def test_confidence_boundaries_valid(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding("high", "A", "f.py", "d", "r", line=1, confidence=0.0)
        b.add_finding("high", "B", "f.py", "d", "r", line=2, confidence=1.0)
        assert b.findings[0]["confidence"] == 0.0
        assert b.findings[1]["confidence"] == 1.0

    def test_confidence_boundaries_invalid(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="Confidence"):
            b.add_finding("high", "A", "f.py", "d", "r", line=1, confidence=-0.1)
        with pytest.raises(ValueError, match="Confidence"):
            b.add_finding("high", "B", "f.py", "d", "r", line=1, confidence=1.1)

    def test_extra_kwargs_preserved(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding(
            "high", "Title", "f.py", "desc", "rec",
            line=1,
            vulnerability_type="xss",
            cwe_id="CWE-79",
        )
        finding = b.findings[0]
        assert finding["vulnerability_type"] == "xss"
        assert finding["cwe_id"] == "CWE-79"

    def test_line_default_none_records_file_scoped_finding(self):
        """Default line=None records a first-class file-scoped finding."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        finding_id = b.add_finding("medium", "Title", "f.py", "desc", "rec")
        assert len(b.findings) == 1
        assert len(b.observations) == 0
        assert finding_id == "f1"


class TestFindingNormalizationIsShared:
    """add_finding and update_finding normalize through one implementation.

    They used to carry five parallel copies — severity casing, floor
    promotion, text coercion, file-scope derivation, channel membership —
    which is how update_finding came to skip the severity membership check
    that add_finding made, and how a patch could store a title the adder
    would have collapsed to one line.
    """

    def _builder(self, tmp_path, channels=("blocking",)):
        _write_assignment(tmp_path, channels=channels)
        return ReviewOutputBuilder.open(tmp_path, "42", "security")

    @pytest.mark.parametrize("mutate", ["add", "update"])
    def test_severity_case_and_floor_promotion_match(self, tmp_path, mutate):
        b = self._builder(tmp_path)
        if mutate == "add":
            b.add_finding(
                "LOW", "t", "src/a.py", "d", "r", line=1, severity_floor="HIGH"
            )
        else:
            b.add_finding("critical", "t", "src/a.py", "d", "r", line=1)
            b.update_finding("f1", severity="LOW", severity_floor="HIGH")
        assert b.findings[0]["severity"] == "high"
        assert b.findings[0]["severity_floor"] == "high"

    @pytest.mark.parametrize("mutate", ["add", "update"])
    def test_titles_are_collapsed_to_one_line(self, tmp_path, mutate):
        b = self._builder(tmp_path)
        if mutate == "add":
            b.add_finding("low", "a\n# b", "src/a.py", "d", "r", line=1)
        else:
            b.add_finding("low", "t", "src/a.py", "d", "r", line=1)
            b.update_finding("f1", title="a\n# b")
        assert b.findings[0]["title"] == "a # b"

    @pytest.mark.parametrize("mutate", ["add", "update"])
    def test_unknown_severity_is_refused_by_both(self, tmp_path, mutate):
        b = self._builder(tmp_path)
        with pytest.raises(ValueError, match="Invalid severity"):
            if mutate == "add":
                b.add_finding("urgent", "t", "src/a.py", "d", "r", line=1)
            else:
                b.add_finding("low", "t", "src/a.py", "d", "r", line=1)
                b.update_finding("f1", severity="urgent")

    @pytest.mark.parametrize("mutate", ["add", "update"])
    def test_off_channel_is_refused_by_both(self, tmp_path, mutate):
        b = self._builder(tmp_path, channels=("blocking",))
        with pytest.raises(ValueError, match="is not among this reviewer's"):
            if mutate == "add":
                b.add_finding(
                    "low", "t", "src/a.py", "d", "r", line=1, channel="advisory"
                )
            else:
                b.add_finding("low", "t", "src/a.py", "d", "r", line=1)
                b.update_finding("f1", channel="advisory")

    @pytest.mark.parametrize("mutate", ["add", "update"])
    def test_file_scope_follows_the_line(self, tmp_path, mutate, capsys):
        b = self._builder(tmp_path)
        if mutate == "add":
            b.add_finding("low", "t", "src/a.py", "d", "r")
        else:
            b.add_finding("low", "t", "src/a.py", "d", "r", line=1)
            b.update_finding("f1", line=None)
        capsys.readouterr()
        assert b.findings[0]["scope"] == "file"
        b.update_finding("f1", line=7)
        assert "scope" not in b.findings[0]

    def test_a_new_finding_omits_the_keys_it_carries_no_value_in(
        self, tmp_path
    ):
        b = self._builder(tmp_path)
        b.add_finding("low", "t", "src/a.py", "d", "r", line=1)
        assert set(b.findings[0]) == {
            "id", "category", "severity", "title", "description",
            "file", "line", "recommendation", "confidence",
        }


# =============================================================================
# TestRecordCheck
# =============================================================================


class TestRecordCheck:
    """record_check stores auditable verification work."""

    def test_stores_question_method_result_and_source(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        check_id = b.record_check(
            question="Does CSS or JS depend on the removed label element?",
            method="grep -rn 'th label' client/legacy/css/; read each hit",
            result="3 occurrences read: admin.scss:5354, :5367, :5567",
        )
        d = b.to_dict()
        assert check_id == "c1"
        assert d["checks"] == [{
            "id": "c1",
            "question": "Does CSS or JS depend on the removed label element?",
            "method": "grep -rn 'th label' client/legacy/css/; read each hit",
            "result": "3 occurrences read: admin.scss:5354, :5367, :5567",
            "source_reviewers": ["a11y"],
        }]

    def test_record_check_is_public_with_source_reviewers(self):
        """One public entry point: the reviewer path defaults its own name,
        the synthesis path names the reviewers a merged check came from."""
        builder = ReviewOutputBuilder(pr_id="1", reviewer="security")
        builder.record_check("q", "m", "r")
        builder.record_check("q2", "m", "r", source_reviewers=["a", "b", "a"])
        assert builder.checks[0]["source_reviewers"] == ["security"]
        assert builder.checks[1]["source_reviewers"] == ["a", "b"]
        assert not hasattr(builder, "_record_check")

    def test_empty_source_reviewers_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        with pytest.raises(ValueError, match="source_reviewers"):
            b.record_check("q", "m", "r", source_reviewers=[])

    def test_no_checks_serializes_empty_array(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        assert b.to_dict()["checks"] == []

    def test_empty_question_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        with pytest.raises(ValueError):
            b.record_check(question="  ", method="grep foo", result="none")

    def test_empty_method_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        with pytest.raises(ValueError):
            b.record_check(question="Any blast radius?", method="", result="none")

    def test_renders_in_markdown_with_method(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        b.record_check(
            question="Does CSS depend on the label?",
            method="grep 'th label' admin.scss",
            result="No dependencies found.",
        )
        md = render_markdown(b.to_dict())
        assert "## Checks Performed" in md
        assert "Does CSS depend on the label?" in md
        assert "grep 'th label' admin.scss" in md


# =============================================================================
# TestAddRecommendation
# =============================================================================


class TestAddRecommendation:
    """add_recommendation stores by priority bucket."""

    def test_valid_priorities(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_recommendation("immediate", "Fix now")
        b.add_recommendation("important", "Fix soon")
        b.add_recommendation("suggestions", "Nice to have")
        assert b.recommendations["immediate"] == ["Fix now"]
        assert b.recommendations["important"] == ["Fix soon"]
        assert b.recommendations["suggestions"] == ["Nice to have"]

    def test_invalid_priority_silently_ignored(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_recommendation("urgent", "Fix now")
        # No error, and no bucket created
        assert "urgent" not in b.recommendations
        assert all(len(v) == 0 for v in b.recommendations.values())

    def test_non_string_text_coerced(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_recommendation("immediate", ["Do A", "Do B"])
        stored = b.recommendations["immediate"][0]
        assert isinstance(stored, str)
        assert "Do A" in stored and "Do B" in stored


# =============================================================================
# TestNonStringFieldCoercion
# =============================================================================


class TestNonStringFieldCoercion:
    """add_finding coerces free-form text fields to strings.

    Regression: a reviewer emitted a list-valued ``recommendation`` that reached
    the reconciliation Markdown renderer and crashed the whole pipeline. The
    producer must never write a non-string title/description/recommendation.
    """

    def test_list_recommendation_coerced_to_string(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding(
            "high", "Title", "f.py", "desc",
            ["Wire it in", "or drop it"], line=1,
        )
        rec = b.findings[0]["recommendation"]
        assert isinstance(rec, str)
        assert "Wire it in" in rec and "or drop it" in rec

    def test_list_description_and_title_coerced(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding(
            "high", ["Ambiguous name"], "f.py", ["D1", "D2"], "rec", line=1,
        )
        assert isinstance(b.findings[0]["title"], str)
        assert isinstance(b.findings[0]["description"], str)
        assert "Ambiguous name" in b.findings[0]["title"]
        assert "D1" in b.findings[0]["description"]

    def test_none_fields_coerced_to_empty_string(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding("high", "Title", "f.py", None, None, line=1)
        assert b.findings[0]["description"] == ""
        assert b.findings[0]["recommendation"] == ""

    def test_multiline_title_collapsed_to_single_line(self):
        # Titles render inline downstream (**N. title**, ### F1: title) without
        # block-syntax escaping, so a coerced newline could forge a heading.
        # The producer must keep the title single-line.
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding(
            "high", ["Legit title", "## Source Snippets"], "f.py",
            "desc", "rec", line=1,
        )
        title = b.findings[0]["title"]
        assert "\n" not in title
        assert "Legit title" in title and "## Source Snippets" in title

    def test_string_fields_unchanged(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding("high", "T", "f.py", "plain desc", "plain rec", line=1)
        assert b.findings[0]["description"] == "plain desc"
        assert b.findings[0]["recommendation"] == "plain rec"


# =============================================================================
# TestSetConfidence
# =============================================================================


class TestSetConfidence:
    """set_confidence validates range."""

    def test_valid_range(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.set_confidence(0.5)
        assert b.overall_confidence == 0.5

    def test_invalid_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        with pytest.raises(ValueError, match="Confidence"):
            b.set_confidence(1.5)
        with pytest.raises(ValueError, match="Confidence"):
            b.set_confidence(-0.1)


# =============================================================================
# TestRemovedToolMetadata
# =============================================================================


class TestRemovedToolMetadata:
    def test_builder_has_no_tool_metadata_api_or_storage(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        assert not hasattr(b, "add_tool_result")
        assert "tool_results_used" not in b.to_dict()["meta"]


# =============================================================================
# TestCalculateVerdict
# =============================================================================


class TestDerivedVerdict:
    """The published verdict is derived from finding severity counts."""

    def _builder_with_findings(self, severities):
        """Create a builder with findings at given severity levels."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        for i, sev in enumerate(severities):
            b.add_finding(sev, f"Issue {i}", f"f{i}.py", "desc", "rec", line=i + 1)
        return b

    def test_no_findings_approve(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        assert b.to_dict()["verdict"] == "approve"

    def test_one_critical_blocks(self):
        b = self._builder_with_findings(["critical"])
        assert b.to_dict()["verdict"] == "block"

    def test_two_high_request_changes(self):
        b = self._builder_with_findings(["high", "high"])
        verdict = b.to_dict()["verdict"]
        assert verdict == "request_changes"
        assert verdict != "block"

    def test_three_high_blocks(self):
        b = self._builder_with_findings(["high", "high", "high"])
        assert b.to_dict()["verdict"] == "block"

    def test_one_high_request_changes(self):
        b = self._builder_with_findings(["high"])
        assert b.to_dict()["verdict"] == "request_changes"

    def test_four_medium_comment(self):
        b = self._builder_with_findings(["medium"] * 4)
        verdict = b.to_dict()["verdict"]
        assert verdict == "comment"
        assert verdict != "request_changes"

    def test_five_medium_request_changes(self):
        b = self._builder_with_findings(["medium"] * 5)
        assert b.to_dict()["verdict"] == "request_changes"

    def test_one_medium_comment(self):
        b = self._builder_with_findings(["medium"])
        assert b.to_dict()["verdict"] == "comment"

    def test_low_and_info_only_approve(self):
        b = self._builder_with_findings(["low", "info", "low", "info"])
        assert b.to_dict()["verdict"] == "approve"


# =============================================================================
# TestToDict
# =============================================================================


class TestToDict:
    """to_dict produces correct structure."""

    def test_all_top_level_keys(self):
        b = ReviewOutputBuilder(pr_id="99", reviewer="arch")
        b.add_finding("medium", "Title", "f.py", "desc", "rec", line=1)
        d = b.to_dict()
        assert set(d.keys()) == REVIEW_CONTENT_FIELDS | {"reviewer"}

    def test_the_three_collections_serialize_as_themselves_when_empty(self):
        """Empty is [] and {} — never null. A reader that had to distinguish
        "said nothing" from "has no field" wrote `or []` at every use, and
        one that forgot it read a null as a crash."""
        d = ReviewOutputBuilder(pr_id="1", reviewer="sec").to_dict()
        assert d["observations"] == []
        assert d["positive_observations"] == []
        assert d["recommendations"] == {
            "immediate": [], "important": [], "suggestions": [],
        }

    def test_to_dict_has_no_reviewed_files_fields(self):
        """to_dict takes no parameters — save_draft stitches the six
        reviewed-file fields on separately via reviewed_files_fields()."""
        builder = ReviewOutputBuilder(pr_id="1", reviewer="security")
        with pytest.raises(TypeError):
            builder.to_dict(file_review="x")

    def test_severity_counts_correct(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_finding("critical", "A", "a.py", "d", "r", line=1)
        b.add_finding("high", "B", "b.py", "d", "r", line=2)
        b.add_finding("high", "C", "c.py", "d", "r", line=3)
        b.add_finding("medium", "D", "d.py", "d", "r", line=4)
        d = b.to_dict()
        counts = d["summary"]["by_severity"]
        assert counts["critical"] == 1
        assert counts["high"] == 2
        assert counts["medium"] == 1
        assert counts["low"] == 0
        assert counts["info"] == 0

    def test_plugin_version_comes_from_the_dispatch_envelope(self, monkeypatch):
        """The producing plugin version is a serialized artifact fact.

        bootstrap exports it alongside the other envelope variables, so a
        review JSON can be attributed to a plugin version on its own.
        """
        monkeypatch.setenv("PIRATEGOAT_PLUGIN_VERSION", "1.114.0")
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        assert b.to_dict()["plugin_version"] == "1.114.0"

    def test_plugin_version_is_null_without_the_envelope(self, monkeypatch):
        """Honest absence, never a required field.

        Hand-rolled and eval-harness callers bypass the envelope; the
        artifact must say it does not know rather than fail or guess.
        """
        monkeypatch.delenv("PIRATEGOAT_PLUGIN_VERSION", raising=False)
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        d = b.to_dict()
        assert "plugin_version" in d
        assert d["plugin_version"] is None

    def test_blank_envelope_value_reads_as_unknown(self, monkeypatch):
        """The envelope always carries the assignment, sometimes empty.

        bootstrap emits PIRATEGOAT_PLUGIN_VERSION unconditionally so the
        envelope shape stays a constant; an empty value means the run
        could not resolve a version, which is the same as not knowing.
        """
        monkeypatch.setenv("PIRATEGOAT_PLUGIN_VERSION", "   ")
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        assert b.to_dict()["plugin_version"] is None

    def test_run_config_supplies_the_version_when_the_envelope_is_bypassed(
        self, monkeypatch, tmp_path
    ):
        """review-reconciliator imports the builder without the envelope.

        It is dispatched by the orchestrator rather than bootstrap, so no
        PIRATEGOAT_* variables reach it — but it is bound to the run's output
        directory, where step 1's run-config.json already records the same
        stamp.
        """
        monkeypatch.delenv("PIRATEGOAT_PLUGIN_VERSION", raising=False)
        (tmp_path / "run-config.json").write_text(
            json.dumps({"mode": "pr", "plugin_version": "1.114.0"})
        )
        b = ReviewOutputBuilder.open(tmp_path, "1", "reconciliator")
        assert b.to_dict()["plugin_version"] == "1.114.0"

    def test_envelope_wins_over_run_config(self, monkeypatch, tmp_path):
        """The envelope is the dispatching plugin's own statement."""
        monkeypatch.setenv("PIRATEGOAT_PLUGIN_VERSION", "2.0.0")
        (tmp_path / "run-config.json").write_text(
            json.dumps({"plugin_version": "1.114.0"})
        )
        b = ReviewOutputBuilder.open(tmp_path, "1", "pr")
        assert b.to_dict()["plugin_version"] == "2.0.0"

    def test_unreadable_run_config_leaves_the_version_unknown(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("PIRATEGOAT_PLUGIN_VERSION", raising=False)
        (tmp_path / "run-config.json").write_text("{not json")
        b = ReviewOutputBuilder.open(tmp_path, "1", "pr")
        assert b.to_dict()["plugin_version"] is None

    def test_saved_artifact_carries_the_version(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PIRATEGOAT_PLUGIN_VERSION", "1.114.0")
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        _write_required_assignment(tmp_path, "pr")
        _save_draft(b, tmp_path)
        saved = json.loads((tmp_path / "pr-review.draft.json").read_text())
        assert saved["plugin_version"] == "1.114.0"

    def test_schema_is_the_documented_shape_number(self):
        """One `schema` convention across every artifact this plugin writes.

        The retired `version: "1.0.0"` string was never bumped through six
        format changes, so it asserted a compatibility guarantee nothing
        maintained. `schema: 2` starts at the shape documented in
        schemas/review-output.ts as of 1.114.0 and is bumped in the same
        commit as any key added, removed, or re-typed.
        """
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        d = b.to_dict()
        assert d["schema"] == 2
        assert isinstance(d["schema"], int)
        assert "version" not in d

    def test_meta_structure(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.set_confidence(0.8)
        d = b.to_dict()
        meta = d["meta"]
        assert meta["confidence_score"] == 0.8
        assert "tool_results_used" not in meta
        assert meta["next_finding_number"] == 1
        assert meta["next_check_number"] == 1
        assert "review_duration_ms" in meta

    def test_no_channel_records_zero_advisory_suppression(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        b.add_finding("high", "Title", "f.py", "desc", "rec", line=1)

        summary = b.to_dict()["summary"]

        assert summary["suppressed_advisory_finding_count"] == 0
        assert "verdict_without_advisory" not in summary

    def test_pr_id_coerced_to_string(self):
        """Ad-hoc builder scripts hand-roll the value bootstrap would have
        injected as a string; an int serializes as a JSON number and breaks
        the artifact's shape uniformity for every downstream consumer."""
        builder = ReviewOutputBuilder(123, "code")
        assert builder.to_dict()["pr_id"] == "123"

    def test_the_builder_exposes_no_unvalidated_serializer(self):
        """`to_dict()` is the only projection; there is no second one.

        `to_json()` had zero production callers and emitted a document
        without the six reviewed-file fields, so its output failed
        `validate_review_document()` — a serializer whose result the
        canonical reader rejects is a trap, not a convenience. Every
        caller either goes through `save_draft()` (which stitches the
        derived fields on) or `json.dumps(builder.to_dict())` in a test
        that is asserting about content, not about publication.
        """
        assert not hasattr(ReviewOutputBuilder, "to_json")


# =============================================================================
# TestRenderMarkdown
# =============================================================================


class TestReviewSummaryProjection:
    """`review_summary` reads the document; it never recounts findings.

    The three consumers that used to recount (agents_status, telemetry's
    agent results, telemetry's ledger extract) each guessed a default for a
    missing `severity`. A validated document has no missing severity — the
    guess only ever produced a number that disagreed with the document's
    own `summary`. So the projection reads.
    """

    def _document(self, severities, *, advisory=()):
        builder = ReviewOutputBuilder("42", "code")
        for index, severity in enumerate(severities, start=1):
            builder.add_finding(
                category="correctness",
                severity=severity,
                title=f"Finding {index}",
                description="Body.",
                recommendation="Fix it.",
                file="src/a.py",
                line=index,
                confidence=0.9,
                channel="advisory" if index in advisory else "blocking",
            )
        return builder.to_dict()

    def test_projects_the_documents_own_summary(self):
        document = self._document(["high", "medium", "medium"])

        assert review_summary(document) == {
            "verdict": document["verdict"],
            "finding_count": 3,
            "severities": {
                "critical": 0, "high": 1, "medium": 2, "low": 0, "info": 0,
            },
            "suppressed_advisory_finding_count": 0,
            "verdict_without_advisory": None,
        }

    def test_every_severity_is_reported_including_the_zeros(self):
        """A zero is a measurement. The Counter recounts omitted them."""
        summary = review_summary(self._document(["low"]))

        assert set(summary["severities"]) == {
            "critical", "high", "medium", "low", "info",
        }
        assert summary["severities"]["critical"] == 0

    def test_advisory_suppression_is_carried_not_recomputed(self):
        document = self._document(["high", "medium"], advisory=(1,))
        summary = review_summary(document)

        assert summary["suppressed_advisory_finding_count"] == 1
        assert summary["verdict_without_advisory"] == (
            document["summary"]["verdict_without_advisory"]
        )
        assert summary["verdict"] == document["verdict"]

    def test_an_abstaining_review_projects_its_zeroed_summary(self):
        builder = ReviewOutputBuilder("42", "code")
        builder.mark_not_applicable(
            "nothing in this reviewer's domain changed"
        )

        summary = review_summary(builder.to_dict())

        assert summary["verdict"] == "not_applicable"
        assert summary["finding_count"] == 0
        assert summary["suppressed_advisory_finding_count"] == 0
        assert summary["verdict_without_advisory"] is None


# =============================================================================
# TestMaterializeMarkdown
# =============================================================================


# =============================================================================
# TestSave
# =============================================================================


class TestSaveDraft:
    """save_draft publishes replaceable state and compact feedback."""

    def test_creates_only_the_draft_json(self):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.add_finding("high", "Title", "f.py", "desc", "rec", line=1)
            _write_required_assignment(d, "security")
            _save_draft(b, d)
            assert os.path.isfile(
                os.path.join(d, "security-review.draft.json")
            )
            assert not os.path.exists(os.path.join(d, "security-review.json"))
            assert not os.path.exists(os.path.join(d, "security-review.md"))

    def test_json_content_matches_to_dict(self, monkeypatch):
        with tempfile.TemporaryDirectory() as d:
            # The dispatch marker bootstrap writes — without it there is no
            # honest clock and the duration is null, which would make this
            # comparison pass for the wrong reason.
            monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", d)
            with open(os.path.join(d, "security-reviewer.started"), "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.add_finding("high", "Title", "f.py", "desc", "rec", line=1)
            _write_required_assignment(d, "security")
            _save_draft(b, d)
            with open(os.path.join(d, "security-review.draft.json")) as f:
                saved = json.load(f)
            live = b.to_dict()

            # review_duration_ms is recomputed from the clock on every
            # to_dict() call, so it differs whenever save() and this
            # assertion straddle a millisecond. Assert it independently
            # and compare the rest exactly. The saved draft additionally
            # carries the six reviewed-file fields save_draft() stitches on
            # via reviewed_files_fields() — to_dict() carries content plus
            # reviewer only.
            assert isinstance(saved["meta"]["review_duration_ms"], int)
            assert saved["reviewed_file_count"] == 0
            saved["meta"].pop("review_duration_ms")
            live["meta"].pop("review_duration_ms")
            saved_content = {
                key: saved[key] for key in REVIEW_CONTENT_FIELDS | {"reviewer"}
            }
            assert saved_content == live

    def test_return_value_has_correct_paths(self):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="arch")
            _write_required_assignment(d, "arch")
            result = _save_draft(b, d)
            assert result["draft"] == os.path.join(
                d, "arch-review.draft.json"
            )
            assert re.fullmatch(r"[0-9a-f]{64}", result["review_digest"])

    def test_prints_compact_totals_to_stdout(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.add_finding("high", "A", "a.py", "d", "r", line=1)
            b.add_finding("medium", "B", "b.py", "d", "r", line=2)
            b.add_observation("c.py", "FYI note")
            _write_required_assignment(d, "security")
            _save_draft(b, d)
            out = capsys.readouterr().out
            assert "DRAFT SAVED: verdict request_changes" in out
            assert (
                "DRAFT TOTALS: findings 2 (high 1, medium 1) | "
                "observations 1"
            ) in out
            assert "critical 0" not in out

    def test_prints_zero_counts_when_empty(self, capsys):
        """An empty save is echoed too — '0 findings recorded' must be visible."""
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            _write_required_assignment(d, "security")
            _save_draft(b, d)
            out = capsys.readouterr().out
            assert "DRAFT TOTALS: findings 0" in out
            assert "DRAFT SAVED: verdict approve" in out

    _MUTATORS = {
        "add_finding": lambda b: b.add_finding(
            "low", "new", "src/a.py", "d", "r", line=2
        ),
        "update_finding": lambda b: b.update_finding("f1", title="renamed"),
        "remove_finding": lambda b: b.remove_finding("f1"),
        "record_check": lambda b: b.record_check("q?", "m", "r"),
        "update_check": lambda b: b.update_check("c1", result="other"),
        "remove_check": lambda b: b.remove_check("c1"),
        "add_observation": lambda b: b.add_observation("src/a.py", "note"),
        "set_assessment": lambda b: b.set_assessment("Bounded risk."),
        "add_recommendation": lambda b: b.add_recommendation("immediate", "do"),
        "add_positive_observation": lambda b: b.add_positive_observation("good"),
        "set_confidence": lambda b: b.set_confidence(0.5),
        "claim_files_reviewed": lambda b: b.claim_files_reviewed("src/a.py"),
        "retract_reviewed_file_claims": (
            lambda b: b.retract_reviewed_file_claims("src/b.py")
        ),
    }

    @pytest.mark.parametrize("mutator", sorted(_MUTATORS))
    def test_every_mutator_reaches_the_changed_line(
        self, tmp_path, capsys, mutator
    ):
        """The receipt is the agent's only feedback that a call landed. It is
        derived from the saved documents, so a mutator cannot be missing from
        it by forgetting to announce itself."""
        _write_assignment(tmp_path, claimable=("src/a.py", "src/b.py"))
        builder = ReviewOutputBuilder.open(tmp_path, "42", "security")
        builder.add_finding("low", "Prior", "src/a.py", "d", "r", line=1)
        builder.record_check("prior?", "m", "r")
        builder.claim_files_reviewed("src/b.py")
        builder.save_draft()
        capsys.readouterr()

        reopened = ReviewOutputBuilder.open(tmp_path, "42", "security")
        self._MUTATORS[mutator](reopened)
        reopened.save_draft()

        changed = [
            line
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("CHANGED:")
        ]
        assert len(changed) == 1, f"{mutator} produced {changed}"

    def test_an_unchanged_resave_reports_nothing_changed(self, tmp_path, capsys):
        """A save that changed nothing says nothing — the old call tally
        could not tell the difference between a no-op call and a change."""
        _write_assignment(tmp_path)
        builder = ReviewOutputBuilder.open(tmp_path, "42", "security")
        builder.add_finding("low", "Prior", "src/a.py", "d", "r", line=1)
        builder.save_draft()
        capsys.readouterr()

        reopened = ReviewOutputBuilder.open(tmp_path, "42", "security")
        reopened.update_finding("f1", title="Prior")
        reopened.save_draft()

        assert "CHANGED:" not in capsys.readouterr().out

    def test_the_changed_line_names_entries_by_id(self, tmp_path, capsys):
        _write_assignment(tmp_path, claimable=("src/a.py", "src/b.py"))
        builder = ReviewOutputBuilder.open(tmp_path, "42", "security")
        builder.add_finding("low", "Prior", "src/a.py", "d", "r", line=1)
        builder.claim_files_reviewed("src/a.py")
        builder.save_draft()
        capsys.readouterr()

        reopened = ReviewOutputBuilder.open(tmp_path, "42", "security")
        reopened.add_finding("high", "New", "src/a.py", "d", "r", line=9)
        reopened.update_finding("f1", severity="medium")
        reopened.set_assessment("The remaining risk is bounded.")
        reopened.add_positive_observation("The validation path is clear.")
        reopened.claim_files_reviewed("src/b.py")
        reopened.retract_reviewed_file_claims("src/a.py")
        reopened.save_draft()

        changed = [
            line
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("CHANGED:")
        ]
        assert changed == [
            "CHANGED: findings +f2 | findings ~f1 | positive observations +1 "
            "| assessment changed | claims +1/-1"
        ]

    def test_reopening_a_draft_prints_its_index(self, tmp_path, capsys):
        """The continuation index reaches the agent from the builder it must
        call, not from a second reader of the same file."""
        _write_assignment(tmp_path)
        builder = ReviewOutputBuilder.open(tmp_path, "42", "security")
        builder.add_finding("low", "Prior", "src/a.py", "d", "r", line=1)
        builder.save_draft()
        capsys.readouterr()

        ReviewOutputBuilder.open(tmp_path, "42", "security")

        out = capsys.readouterr().out
        assert "DRAFT INDEX:" in out
        assert 'finding f1: low "Prior" @ src/a.py:1' in out

    def test_a_first_open_prints_no_index(self, tmp_path, capsys):
        _write_assignment(tmp_path)
        ReviewOutputBuilder.open(tmp_path, "42", "security")
        assert "DRAFT INDEX:" not in capsys.readouterr().out

    def test_failed_save_removes_its_staged_file(self, monkeypatch):
        """A failed draft replace removes the nonce staging file."""
        import review.agent.output as output_mod

        def _boom(*args):
            raise OSError("draft replace failed")

        monkeypatch.setattr(output_mod.os, "replace", _boom)
        with tempfile.TemporaryDirectory() as d:
            _write_required_assignment(d, "security")
            with pytest.raises(OSError):
                _save_draft(
                    ReviewOutputBuilder(pr_id="1", reviewer="security"), d
                )
            assert not os.path.exists(os.path.join(d, "security-review.json"))
            assert not os.path.exists(
                os.path.join(d, "security-review.draft.json")
            )
            assert not list(Path(d).glob("*.tmp"))

    def test_saved_draft_embeds_the_derived_partition(self, tmp_path):
        """save_draft stitches reviewed_files_fields() onto to_dict()'s content —
        the saved document is content plus the six derived envelope keys."""
        _write_assignment(tmp_path, claimable=("src/a.py", "src/b.py"))
        builder = ReviewOutputBuilder.open(tmp_path, "42", "security")
        builder.claim_files_reviewed("src/b.py")
        saved = builder.save_draft()
        draft = json.loads(Path(saved["draft"]).read_text())
        assert draft["reviewed_file_claims"] == ["src/b.py"]
        assert draft["unclaimed_review_files"] == ["src/a.py"]
        assert draft["reviewed_file_count"] == 1
        validate_review_document(draft, "security")


# =============================================================================
# TestFileScopedFindings
# =============================================================================


class TestFileScopedFindings:
    """line=None records a first-class file-scoped finding (no silent demotion).

    Some finding classes are line-less BY NATURE — missing test coverage,
    missing assertions, git-history precedent, cross-file architecture. These
    must count toward the verdict, not vanish into observations. Point defects
    still require line= (invalid line values raise; the file-scoped path warns
    on stderr so lazy line omission stays loud).
    """

    def test_line_none_records_finding_with_null_line_and_file_scope(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        finding_id = b.add_finding("high", "Title", "f.py", "desc", "rec", line=None)
        assert finding_id == "f1"
        assert len(b.findings) == 1
        assert len(b.observations) == 0
        finding = b.findings[0]
        assert finding["line"] is None
        assert finding["scope"] == "file"
        assert finding["id"] == finding_id

    def test_reproduction_lineless_high_counts_toward_severity_and_verdict(self):
        """The RCA reproduction: a line-less HIGH must not silently drop."""
        b = ReviewOutputBuilder(pr_id="0", reviewer="js-tests")
        b.add_finding(
            severity="high",
            title="whole-file has no test",
            file="src/foo.ts",
            description="...",
            recommendation="...",
            category="missing-coverage",
        )
        d = b.to_dict()
        assert d["summary"]["by_severity"]["high"] == 1
        assert d["summary"]["total_findings"] == 1
        assert len(d["findings"]) == 1
        assert d["verdict"] == "request_changes"

    def test_line_none_prints_stderr_note(self, capsys):
        """The file-scoped path is loud — names the title and severity."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding("high", "Missing coverage", "f.py", "desc", "rec", line=None)
        err = capsys.readouterr().err
        assert "file-scoped" in err.lower()
        assert "Missing coverage" in err
        assert "high" in err.lower()

    def test_line_anchored_finding_has_no_scope_field(self):
        """Schema stays additive — line-anchored findings are unchanged."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding("high", "Title", "f.py", "desc", "rec", line=42)
        assert "scope" not in b.findings[0]
        assert b.findings[0]["line"] == 42

    def test_file_scoped_finding_renders_under_severity_section(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="js-tests")
        b.add_finding(
            "high", "whole-file has no test", "src/foo.ts", "desc", "rec",
            category="missing-coverage",
        )
        md = render_markdown(b.to_dict())
        assert "## High Findings" in md
        assert "whole-file has no test" in md
        assert "`src/foo.ts` (file-scoped)" in md

    def test_file_scoped_finding_json_roundtrip(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding("medium", "Title", "f.py", "desc", "rec", line=None)
        parsed = json.loads(json.dumps(b.to_dict()))
        assert parsed["findings"][0]["line"] is None
        assert parsed["findings"][0]["scope"] == "file"


# =============================================================================
# TestLineRequired
# =============================================================================


class TestLineRequired:
    """Invalid line values still raise (protocol enforcement for point defects)."""

    def test_line_zero_raises(self):
        """Line 0 is invalid (lines are 1-indexed)."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="line.*positive"):
            b.add_finding("high", "Title", "f.py", "desc", "rec", line=0)

    def test_line_negative_raises(self):
        """Negative line is invalid."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="line.*positive"):
            b.add_finding("high", "Title", "f.py", "desc", "rec", line=-1)


# =============================================================================
# TestAddObservation
# =============================================================================


class TestAddObservation:
    """add_observation stores file-level notes outside the finding pipeline."""

    def test_stores_observation(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_observation("f.py", "File lacks CSRF protection", category="security")
        assert len(b.observations) == 1
        obs = b.observations[0]
        assert obs["file"] == "f.py"
        assert obs["note"] == "File lacks CSRF protection"
        assert obs["category"] == "security"

    def test_observations_in_dict_output(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_observation("f.py", "Note")
        d = b.to_dict()
        assert "observations" in d
        assert len(d["observations"]) == 1

    def test_observations_do_not_affect_verdict(self):
        """Observations don't count as findings — verdict unaffected."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_observation("f.py", "Looks risky", category="security")
        assert b.to_dict()["verdict"] == "approve"

    def test_observations_in_markdown(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_observation("f.py", "File lacks CSRF protection")
        md = render_markdown(b.to_dict())
        assert "Observations" in md
        assert "File lacks CSRF protection" in md


# =============================================================================
# TestReviewedFileClaims
# =============================================================================


class TestReviewedFileClaims:
    """claim_files_reviewed claims NOT DIFFED files as actually reviewed.

    The positive-claim API validates one complete batch against the bound
    directory's authoritative assignment. Coverage gaps and reviewed
    counts are derived later; reviewers never state either population
    directly."""

    def _armed_builder(self, tmp_path, claimable):
        """One builder bound to a bootstrap-written authoritative claimable set."""
        _write_assignment(tmp_path, "sec", claimable)
        return ReviewOutputBuilder.open(tmp_path, "1", "sec")

    @pytest.mark.parametrize("bad", ["", "   ", None, 42, ["src/a.py"]])
    def test_rejects_non_path_values(self, bad):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError):
            b.claim_files_reviewed(bad)

    @pytest.mark.parametrize(
        "bad",
        [
            "/abs/a.py", "../outside.py", "..", "C:/win.py", "c:win.py",
            # These normalize to "." — a form no scope summary can contain.
            ".", "./", "foo/..",
        ],
    )
    def test_rejects_non_repo_relative_forms(self, bad):
        """A claim must address a repository-relative claimable path."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError):
            b.claim_files_reviewed(bad)

    def test_stores_and_dedupes_claims(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.claim_files_reviewed("src/a.py", "./src/a.py", "src/b.py")
        assert b.reviewed_file_claims == ["src/a.py", "src/b.py"]

    def test_zero_arguments_raises(self):
        """A claim of nothing is a silent no-op, not a claim."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="at least one file path"):
            b.claim_files_reviewed()

    def test_claim_in_claimable_set_accepted(self, tmp_path):
        b = self._armed_builder(tmp_path, ["src/claimable.py"])
        b.claim_files_reviewed("./src/claimable.py")  # normalized first
        assert b.reviewed_file_claims == ["src/claimable.py"]

    def test_claim_outside_claimable_set_rejected_at_add(self, tmp_path):
        """A claim on a file this review never claimable is rejected."""
        b = self._armed_builder(tmp_path, ["src/email.py"])
        with pytest.raises(ValueError, match="src/email.py"):
            b.claim_files_reviewed("src/emails.py")
        with pytest.raises(ValueError, match="claim"):
            b.claim_files_reviewed("src/emails.py")

    def test_empty_claimable_set_rejects_every_claim(self, tmp_path):
        """The empty-set branch explains that no claim can be made."""
        b = self._armed_builder(tmp_path, [])
        with pytest.raises(ValueError, match=r"1 claim\(s\)") as excinfo:
            b.claim_files_reviewed("src/a.py")
        assert "no claim may be made" in str(excinfo.value)

    def test_all_or_nothing_on_mid_batch_error(self, tmp_path):
        """A batch either fully lands or nothing does — the same doctrine
        critic_adjustments.py enforces for its own batches. A mid-batch
        rejection must not leave the leading valid paths recorded: a retry
        would then double-record them, and a caller who gives up is left
        with a half-claim no one asked for."""
        b = self._armed_builder(tmp_path, ["src/a.py", "src/c.py"])
        with pytest.raises(ValueError, match="src/b.py"):
            b.claim_files_reviewed("src/a.py", "src/b.py", "src/c.py")
        assert b.reviewed_file_claims == []
        # A retry with only the valid paths lands fully.
        b.claim_files_reviewed("src/a.py", "src/c.py")
        assert b.reviewed_file_claims == ["src/a.py", "src/c.py"]

    def test_multi_error_batch_names_every_offender(self, tmp_path):
        """The existing batch-reporting rejection helper already names
        every offender in one raise at save() time; add-time claims must
        get the same treatment instead of stopping at the first bad path."""
        b = self._armed_builder(tmp_path, ["src/a.py"])
        with pytest.raises(ValueError) as excinfo:
            b.claim_files_reviewed(
                "src/a.py", "src/bogus1.py", "src/bogus2.py"
            )
        message = str(excinfo.value)
        assert "src/bogus1.py" in message
        assert "src/bogus2.py" in message
        assert b.reviewed_file_claims == []

    def test_grammar_error_mid_batch_records_nothing(self, tmp_path):
        """The all-or-nothing guarantee covers grammar failures too: a
        malformed path anywhere in the batch leaves zero paths recorded,
        not the leading valid ones."""
        b = self._armed_builder(tmp_path, ["src/a.py"])
        with pytest.raises(ValueError, match="/abs/path.py"):
            b.claim_files_reviewed("src/a.py", "/abs/path.py")
        assert b.reviewed_file_claims == []

    def test_mixed_grammar_and_membership_batch_names_both(self, tmp_path):
        """A batch carrying both error classes reports both in one raise:
        fixing the malformed path must not surface the membership problem
        as a fresh surprise on the retry."""
        b = self._armed_builder(tmp_path, ["src/a.py"])
        with pytest.raises(ValueError) as excinfo:
            b.claim_files_reviewed("src/typo.py", "/abs/path.py")
        message = str(excinfo.value)
        assert "/abs/path.py" in message
        assert "src/typo.py" in message
        assert b.reviewed_file_claims == []

    def test_failed_batch_leaves_no_trace_in_saved_artifact(self, tmp_path):
        """The consequence that matters: after a rejected batch, save_draft()'s
        derivation is exactly as if the call never happened — the
        unclaimed file lands in the derived gap record, never as a claim."""
        b = self._armed_builder(tmp_path, ["src/a.py", "src/c.py"])
        with pytest.raises(ValueError):
            b.claim_files_reviewed("src/a.py", "src/bogus.py")
        b.claim_files_reviewed("src/c.py")
        _save_draft(b, tmp_path)
        with open(
            tmp_path / "sec-review.draft.json", encoding="utf-8"
        ) as f:
            data = json.load(f)
        assert data["reviewed_file_claims"] == ["src/c.py"]
        assert data["unclaimed_review_files"] == ["src/a.py"]

    def test_duplicate_within_batch_dedupes(self, tmp_path):
        """Pinning current semantics: a batch repeating one path collapses
        it to a single entry, order preserved."""
        b = self._armed_builder(tmp_path, ["src/a.py", "src/b.py"])
        b.claim_files_reviewed("src/a.py", "./src/a.py", "src/b.py")
        assert b.reviewed_file_claims == ["src/a.py", "src/b.py"]

    def test_already_recorded_across_calls_dedupes(self, tmp_path):
        """Pinning current semantics: claiming a path already recorded by
        a previous call is a silent no-op, not an error or a duplicate
        entry."""
        b = self._armed_builder(tmp_path, ["src/a.py"])
        b.claim_files_reviewed("src/a.py")
        b.claim_files_reviewed("src/a.py")
        assert b.reviewed_file_claims == ["src/a.py"]

    def test_retracts_claims_atomically_and_preserves_remaining_order(self, tmp_path):
        builder = self._armed_builder(tmp_path, ["src/a.py", "src/b.py", "src/c.py"])
        builder.claim_files_reviewed("src/a.py", "src/b.py", "src/c.py")

        builder.retract_reviewed_file_claims("./src/b.py", "src/a.py")

        assert builder.reviewed_file_claims == ["src/c.py"]

    def test_retraction_rejects_unknown_batch_without_mutation(self, tmp_path):
        builder = self._armed_builder(tmp_path, ["src/a.py", "src/b.py"])
        builder.claim_files_reviewed("src/a.py", "src/b.py")

        with pytest.raises(ValueError, match="not currently claimed"):
            builder.retract_reviewed_file_claims("src/a.py", "src/missing.py")

        assert builder.reviewed_file_claims == ["src/a.py", "src/b.py"]

    def test_both_batch_apis_share_one_path_grammar(self, tmp_path):
        """Claiming and retracting normalize through the same function the
        authoritative derivation uses, so a path either has the grammar in
        all three places or in none."""
        builder = self._armed_builder(tmp_path, ["src/a.py"])
        builder.claim_files_reviewed("./src/a.py")
        assert builder.reviewed_file_claims == ["src/a.py"]
        builder.retract_reviewed_file_claims("src\\a.py")
        assert builder.reviewed_file_claims == []
        with pytest.raises(ValueError, match="repository-relative"):
            builder.retract_reviewed_file_claims("/abs/a.py")

    def test_publication_reads_the_bound_assignment_path(
        self, tmp_path, monkeypatch
    ):
        """save_draft derives from the assignment `_bind` already located —
        it never recomputes the path from a reviewer name a second time."""
        _write_assignment(tmp_path, "sec", ["src/a.py"])
        builder = ReviewOutputBuilder.open(tmp_path, "1", "sec")
        calls = []
        real = review_output.review_paths
        monkeypatch.setattr(
            review_output,
            "review_paths",
            lambda *args: (calls.append(args), real(*args))[1],
        )
        builder.save_draft()
        assert calls == []


# =============================================================================
# TestNotApplicable
# =============================================================================


class TestNotApplicable:
    """mark_not_applicable produces not_applicable verdict with skip_reason."""

    def test_verdict_is_not_applicable(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.mark_not_applicable("No changes relevant to security domain")
        d = b.to_dict()
        assert d["verdict"] == "not_applicable"

    def test_skip_reason_in_output(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.mark_not_applicable("No security-relevant changes in diff")
        d = b.to_dict()
        assert d["skip_reason"] == "No security-relevant changes in diff"

    def test_skip_reason_absent_by_default(self):
        """skip_reason is not present when agent reviewed normally."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        d = b.to_dict()
        assert "skip_reason" not in d

    def test_empty_reason_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="reason"):
            b.mark_not_applicable("")

    def test_whitespace_only_reason_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="reason"):
            b.mark_not_applicable("   ")

    def test_raises_if_findings_already_recorded(self):
        """mark_not_applicable rejects mixed state — findings + not_applicable is contradictory."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding("high", "XSS", "f.php", "desc", "rec", line=1)
        with pytest.raises(ValueError, match="finding.*already recorded"):
            b.mark_not_applicable("Agent mistakenly started before checking relevance")

    def test_in_json_output(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.mark_not_applicable("No relevant changes")
        parsed = json.loads(json.dumps(b.to_dict()))
        assert parsed["verdict"] == "not_applicable"
        assert parsed["skip_reason"] == "No relevant changes"

    def test_skip_reason_stripped(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.mark_not_applicable("  No relevant changes  ")
        d = b.to_dict()
        assert d["skip_reason"] == "No relevant changes"

    def test_normal_approve_has_no_skip_reason(self):
        """A normal approve (no findings, no mark_not_applicable) has no skip_reason."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_positive_observation("Clean code")
        d = b.to_dict()
        assert d["verdict"] == "approve"
        assert "skip_reason" not in d


# =============================================================================
# Advisory channel — repo-contributed reviewers
# =============================================================================

class TestAdvisoryChannel:
    """Advisory-channel findings are listed but never gate the verdict."""

    def test_invalid_channel_raises_and_names_value(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")

        with pytest.raises(ValueError, match="Advisory"):
            b.add_finding(severity="high", title="Duplication", file="a.php",
                        description="d", recommendation="r", line=5,
                        channel="Advisory")

    def test_advisory_channel_reviewer_records_advisory_without_gating(
        self, tmp_path
    ):
        _write_assignment(
            tmp_path,
            "repo-reuse",
            claimable=(),
            channels=("blocking", "advisory"),
        )
        b = ReviewOutputBuilder.open(tmp_path, "1", "repo-reuse")
        b.add_finding(severity="high", title="Duplication", file="a.php",
                    description="d", recommendation="r", line=5, channel="advisory")
        assert b.to_dict()["verdict"] == "approve"

    def test_unbound_builder_fails_open_after_vocabulary_validation(self):
        """A hand-rolled builder has no assignment to consult."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")

        b.add_finding(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )

        assert b.to_dict()["verdict"] == "approve"

    def test_absent_assignment_fails_open_after_vocabulary_validation(
        self, tmp_path
    ):
        b = ReviewOutputBuilder.open(tmp_path, "1", "repo-reuse")

        b.add_finding(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )

        assert b.to_dict()["verdict"] == "approve"

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param("{not json", id="unparsable"),
            pytest.param("[]", id="top-level-not-object"),
            pytest.param(
                json.dumps({"schema": 4, "channels": ["advisory"]}),
                id="incomplete-input",
            ),
        ],
    )
    def test_malformed_assignment_fails_open_at_add_time(
        self, tmp_path, payload
    ):
        b = ReviewOutputBuilder.open(tmp_path, "1", "repo-reuse")
        Path(
            review_paths(str(tmp_path), "repo-reuse").assignment
        ).write_text(payload)

        b.add_finding(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )

        assert b.to_dict()["verdict"] == "approve"

    def test_invalid_utf8_assignment_fails_open_at_add_time(
        self, tmp_path
    ):
        b = ReviewOutputBuilder.open(tmp_path, "1", "repo-reuse")
        Path(
            review_paths(str(tmp_path), "repo-reuse").assignment
        ).write_bytes(b"\xff")

        b.add_finding(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )

        assert b.to_dict()["verdict"] == "approve"

    def test_save_rejects_findings_off_this_reviewer_channels(self, tmp_path):
        """Add-time fail-open is not a way past publication.

        The finding is recorded while the builder is unbound; the bound
        directory's assignment is what decides whether it may be published.
        """
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.add_finding(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )
        _write_required_assignment(tmp_path, "reconciliator")

        with pytest.raises(
            ValueError, match=r"channel\(s\) \['advisory'\] not among"
        ):
            _save_draft(b, tmp_path)
        assert not (tmp_path / "reconciliator-review.draft.json").exists()

    def test_advisory_critical_does_not_gate(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_finding(severity="critical", title="x", file="a.php",
                    description="d", recommendation="r", line=5, channel="advisory")
        assert b.to_dict()["verdict"] == "approve"

    def test_critical_advisory_records_stricter_counterfactual(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_finding(
            severity="critical", title="x", file="a.php",
            description="d", recommendation="r", line=5,
            channel="advisory",
        )

        output = b.to_dict()

        assert output["verdict"] == "approve"
        assert output["summary"]["suppressed_advisory_finding_count"] == 1
        assert output["summary"]["verdict_without_advisory"] == "block"
        assert "Advisory suppression:** 1 finding excluded" in render_markdown(output)
        assert "verdict without suppression: BLOCK" in render_markdown(output)

    def test_advisory_count_without_verdict_softening_omits_counterfactual(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_finding(
            severity="low", title="x", file="a.php",
            description="d", recommendation="r", line=5,
            channel="advisory",
        )

        output = b.to_dict()

        assert output["verdict"] == "approve"
        assert output["summary"]["suppressed_advisory_finding_count"] == 1
        assert "verdict_without_advisory" not in output["summary"]
        assert "Advisory suppression:** 1 finding excluded" in render_markdown(output)

    def test_advisory_count_when_verdict_already_strict_omits_counterfactual(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_finding(
            severity="critical", title="advisory", file="a.php",
            description="d", recommendation="r", line=5,
            channel="advisory",
        )
        b.add_finding(
            severity="critical", title="blocking", file="b.php",
            description="d", recommendation="r", line=6,
        )

        output = b.to_dict()

        assert output["verdict"] == "block"
        assert output["summary"]["suppressed_advisory_finding_count"] == 1
        assert "verdict_without_advisory" not in output["summary"]

    def test_not_applicable_does_not_claim_advisory_suppression(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.mark_not_applicable("No relevant changes")
        b.add_finding(
            severity="critical", title="advisory", file="a.php",
            description="d", recommendation="r", line=5,
            channel="advisory",
        )

        output = b.to_dict()

        assert output["verdict"] == "not_applicable"
        assert output["summary"]["suppressed_advisory_finding_count"] == 0
        assert "verdict_without_advisory" not in output["summary"]

    def test_blocking_channel_is_implicit_and_still_gates(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-runtime")
        b.add_finding(severity="critical", title="x", file="a.php",
                    description="d", recommendation="r", line=5, channel="blocking")
        assert "channel" not in b.findings[0]
        assert b.to_dict()["verdict"] == "block"

    def test_no_channel_is_backward_compatible(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        b.add_finding(severity="high", title="x", file="a.php",
                    description="d", recommendation="r", line=5)
        assert b.to_dict()["verdict"] == "request_changes"

    def test_advisory_channel_persisted_in_finding(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_finding(severity="low", title="x", file="a.php",
                    description="d", recommendation="r", line=5, channel="advisory")
        assert b.findings[0]["channel"] == "advisory"

    def test_mixed_channels(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-mix")
        b.add_finding(severity="critical", title="adv", file="a.php",
                    description="d", recommendation="r", line=5, channel="advisory")
        b.add_finding(severity="medium", title="block", file="a.php",
                    description="d", recommendation="r", line=6, channel="blocking")
        # Only the blocking medium counts → comment (not block from the advisory critical).
        assert b.to_dict()["verdict"] == "comment"


# =============================================================================
# TestSaveTimeClaimValidation
# =============================================================================


class TestDerivedReviewedFiles:
    """Draft and final coverage are sidecar-derived from positive claims."""

    @staticmethod
    def _write_assignment(tmp_path, claimable, *, inline_diff_file_count=0, reviewer="code"):
        write_canonical_assignment(
            tmp_path, reviewer, review_claimable_files=claimable,
            inline_diff_file_count=inline_diff_file_count,
        )

    def test_draft_derives_gaps_and_counts_from_claims(self, tmp_path):
        self._write_assignment(
            tmp_path, ["src/read.ts", "src/unread.ts"], inline_diff_file_count=3
        )
        builder = ReviewOutputBuilder("123", "code")
        builder.claim_files_reviewed("src/read.ts")

        _save_draft(builder, tmp_path)

        saved = json.loads(
            (tmp_path / "code-review.draft.json").read_text()
        )
        assert saved["reviewed_file_claims"] == ["src/read.ts"]
        assert saved["unclaimed_review_files"] == ["src/unread.ts"]
        assert saved["reviewed_file_count"] == 4
        assert "unreviewed_" + "autofilled" not in saved["meta"]

    def test_draft_resave_recomputes_complement_from_scratch(self, tmp_path):
        self._write_assignment(tmp_path, ["src/a.ts", "src/b.ts"])
        builder = ReviewOutputBuilder("123", "code")
        builder.claim_files_reviewed("src/a.ts")
        _save_draft(builder, tmp_path)
        first = json.loads(
            (tmp_path / "code-review.draft.json").read_text()
        )
        assert first["unclaimed_review_files"] == ["src/b.ts"]

        builder.claim_files_reviewed("src/b.ts")
        _save_draft(builder, tmp_path)

        second = json.loads(
            (tmp_path / "code-review.draft.json").read_text()
        )
        assert second["reviewed_file_claims"] == ["src/a.ts", "src/b.ts"]
        assert second["unclaimed_review_files"] == []
        assert second["reviewed_file_count"] == 2

    def test_finalized_json_preserves_derived_coverage(self, tmp_path):
        self._write_assignment(
            tmp_path, ["src/read.ts", "src/unread.ts"], inline_diff_file_count=2
        )
        builder = ReviewOutputBuilder("123", "code")
        builder.claim_files_reviewed("src/read.ts")

        saved = _save_draft(builder, tmp_path)
        finalize_review(str(tmp_path), "code", saved["review_digest"])

        final = json.loads((tmp_path / "code-review.json").read_text())
        assert final["reviewed_file_claims"] == ["src/read.ts"]
        assert final["unclaimed_review_files"] == ["src/unread.ts"]
        assert final["reviewed_file_count"] == 3

    def test_finalization_rejects_a_raw_claim_list(self, tmp_path):
        self._write_assignment(tmp_path, ["src/read.ts"])
        builder = ReviewOutputBuilder("123", "code")
        saved = _save_draft(builder, tmp_path)
        draft_path = tmp_path / "code-review.draft.json"
        draft = json.loads(draft_path.read_text())
        draft["reviewed_file_claims"] = "src/read.ts"
        draft_bytes = json.dumps(draft).encode()
        draft_path.write_bytes(draft_bytes)
        digest = hashlib.sha256(draft_bytes).hexdigest()

        with pytest.raises(
            ValueError, match="reviewed_file_claims must be a list"
        ):
            finalize_review(str(tmp_path), "code", digest)


class TestBudgetTargetEcho:
    """The call-budget target is surfaced where the reviewer can still act.

    The briefing has always stated the target, thousands of tokens before
    the moment a reviewer decides to stop, and a 19-agent field run showed
    that placement moves nothing. The echo is the one feedback surface every
    agent reads, so the target is repeated there — but only when unclaimed_review_files
    files make it actionable, and only when the run actually set one.

    The target travels in the claimable-files sidecar bootstrap writes
    (schema 2), not an env var: the retired env-var budget transport
    silently died for any agent that rebuilt its save command, so the
    sidecar is now the only carrier — the same one output.py already reads
    for derived NOT DIFFED coverage.
    """

    @staticmethod
    def _clean_env(monkeypatch):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)

    @staticmethod
    def _write_assignment(tmp_path, reviewer="code", schema=4, review_claimable_files=None,
                        **fields):
        review_claimable_files = review_claimable_files or []
        payload = {
            "schema": schema,
            "agent_name": f"{reviewer}-reviewer",
            "reviewer": reviewer,
            "review_claimable_files": review_claimable_files,
            "inline_diff_file_count": 0,
            "in_scope_review_file_count": len(review_claimable_files),
            "review_budget": 15,
            "channels": ["blocking"],
        }
        payload.update(fields)
        (tmp_path / f"{reviewer}-assignment.json").write_text(
            json.dumps(payload)
        )

    def _save_with_unreviewed(self, tmp_path, monkeypatch, capsys):
        builder = ReviewOutputBuilder("123", "code")
        _save_draft(builder, tmp_path)
        return capsys.readouterr().out

    def test_target_line_appears_with_unreviewed_and_budget(
        self, tmp_path, monkeypatch, capsys
    ):
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path, review_claimable_files=["some/file.go"], review_budget=80
        )
        out = self._save_with_unreviewed(tmp_path, monkeypatch, capsys)
        assert (
            "FILES NOT YET CLAIMED AS REVIEWED (1): some/file.go | "
            "target ~80 tool calls"
        ) in out

    def test_no_target_line_without_unreviewed_files(
        self, tmp_path, monkeypatch, capsys
    ):
        """Nothing left unread means nothing to act on — silence is right."""
        self._clean_env(monkeypatch)
        self._write_assignment(tmp_path, review_budget=80)
        builder = ReviewOutputBuilder("123", "code")
        _save_draft(builder, tmp_path)
        assert "FILES NOT YET CLAIMED" not in capsys.readouterr().out

    def test_missing_assignment_rejects_publication(
        self, tmp_path, monkeypatch, capsys
    ):
        self._clean_env(monkeypatch)
        with pytest.raises(ValueError, match="missing authoritative review assignment"):
            self._save_with_unreviewed(tmp_path, monkeypatch, capsys)

    @pytest.mark.parametrize("publish", ["draft", "final"])
    def test_a_sidecar_at_another_schema_refuses_publication(
        self, tmp_path, monkeypatch, capsys, publish
    ):
        """Neither publication path reads a sidecar it cannot vouch for.

        The value space is pinned once at the derivation boundary
        (`test_review_assignment.py`); what this pins is that BOTH the
        progress save and the finalizing save consult it, so a draft
        cannot slip past on a sidecar the final would have refused.
        """
        self._clean_env(monkeypatch)
        Path(tmp_path, "code-assignment.json").write_text(
            json.dumps(apply_schema(
                canonical_assignment(
                    "code", review_claimable_files=["some/file.go"]
                ),
                1,
            ))
        )
        with pytest.raises(ValueError, match="schema must be 4"):
            if publish == "final":
                self._save_with_unreviewed(tmp_path, monkeypatch, capsys)
            else:
                _save_draft(ReviewOutputBuilder("123", "code"), tmp_path)

    @pytest.mark.parametrize(
        "raw", [None, "80", "abc", -5, 12.5, True]
    )
    def test_malformed_budget_rejects_publication(
        self, tmp_path, monkeypatch, capsys, raw
    ):
        """A target of 0, a string, or a bool is worse than no target —
        never repair it. Absent key (None) is the same absence."""
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path, review_claimable_files=["some/file.go"], review_budget=raw
        )
        with pytest.raises(ValueError, match="review_budget"):
            self._save_with_unreviewed(tmp_path, monkeypatch, capsys)

    def test_zero_budget_is_valid_but_emits_no_target(
        self, tmp_path, monkeypatch, capsys
    ):
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path, review_claimable_files=["some/file.go"], review_budget=0
        )
        out = self._save_with_unreviewed(tmp_path, monkeypatch, capsys)
        assert "target ~" not in out

    def test_derived_gap_still_gets_the_target(
        self, tmp_path, monkeypatch, capsys
    ):
        """Derived gaps are exactly the case the nudge exists for."""
        self._clean_env(monkeypatch)
        self._write_assignment(tmp_path, review_claimable_files=["a.go"], review_budget=40)
        builder = ReviewOutputBuilder("123", "code")
        _save_draft(builder, tmp_path)
        out = capsys.readouterr().out
        assert "target ~40 tool calls" in out


# =============================================================================
# TestSaveEchoProgressAndNextUnread
# =============================================================================


class TestDraftFileGapReceipt:
    """The compact receipt names at most three unclaimed priority files."""

    @staticmethod
    def _clean_env(monkeypatch):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)

    @staticmethod
    def _write_assignment(tmp_path, reviewer="code", schema=4, review_claimable_files=None,
                        **fields):
        review_claimable_files = review_claimable_files or []
        payload = {
            "schema": schema,
            "agent_name": f"{reviewer}-reviewer",
            "reviewer": reviewer,
            "review_claimable_files": review_claimable_files,
            "inline_diff_file_count": 0,
            "in_scope_review_file_count": len(review_claimable_files),
            "review_budget": 15,
            "channels": ["blocking"],
        }
        payload.update(fields)
        (tmp_path / f"{reviewer}-assignment.json").write_text(
            json.dumps(payload)
        )

    def test_save_derives_authoritative_coverage_without_changing_draft_state(
        self, tmp_path, monkeypatch
    ):
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path,
            review_claimable_files=["a.go", "b.go"],
            in_scope_review_file_count=4,
            inline_diff_file_count=2,
        )
        builder = ReviewOutputBuilder("123", "code")
        builder.claim_files_reviewed("b.go")

        _save_draft(builder, tmp_path)
        saved = json.loads((tmp_path / "code-review.draft.json").read_text())
        assert saved["reviewed_file_claims"] == ["b.go"]
        assert saved["unclaimed_review_files"] == ["a.go"]
        assert saved["reviewed_file_count"] == 3

    def test_progress_and_next_unread_appear_with_claims(
        self, tmp_path, monkeypatch, capsys
    ):
        self._clean_env(monkeypatch)
        claimable = [f"claimable/{i:02d}.go" for i in range(20)]  # largest first
        self._write_assignment(
            tmp_path, review_claimable_files=claimable, review_budget=80,
            in_scope_review_file_count=30, inline_diff_file_count=10,
        )
        builder = ReviewOutputBuilder("123", "code")
        builder.claim_files_reviewed(*claimable[:3])  # claimed — read
        _save_draft(builder, tmp_path)
        out = capsys.readouterr().out

        assert (
            "FILES NOT YET CLAIMED AS REVIEWED (17): "
            "claimable/03.go, claimable/04.go, claimable/05.go (+14 more) "
            "| target ~80 tool calls"
        ) in out

    def test_no_progress_or_next_unread_without_unreviewed_files(
        self, tmp_path, monkeypatch, capsys
    ):
        """An empty derived complement keeps the TARGET gate closed."""
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path, review_budget=80, in_scope_review_file_count=30, inline_diff_file_count=30,
        )
        builder = ReviewOutputBuilder("123", "code")
        _save_draft(builder, tmp_path)
        out = capsys.readouterr().out
        assert "FILES NOT YET CLAIMED" not in out

    def test_next_unread_omitted_only_when_every_claimable_file_is_claimed(
        self, tmp_path, monkeypatch, capsys
    ):
        """Only a positive claim removes a file from NEXT UNREAD. With every
        claimable file claimed there is no derived gap, so the whole
        TARGET/PROGRESS/NEXT UNREAD block never runs."""
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path, review_claimable_files=["a.go", "b.go"], review_budget=40,
            in_scope_review_file_count=5, inline_diff_file_count=3,
        )
        builder = ReviewOutputBuilder("123", "code")
        builder.claim_files_reviewed("a.go", "b.go")
        _save_draft(builder, tmp_path)
        out = capsys.readouterr().out
        assert "FILES NOT YET CLAIMED" not in out

    def test_missing_scope_counts_reject_progress_publication(
        self, tmp_path, monkeypatch, capsys
    ):
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path, review_claimable_files=["a.go", "b.go"], review_budget=40,
            in_scope_review_file_count=None,
        )
        builder = ReviewOutputBuilder("123", "code")
        with pytest.raises(ValueError, match="in_scope_review_file_count"):
            _save_draft(builder, tmp_path)

    @pytest.mark.parametrize(
        (
            "in_scope_review_file_count",
            "inline_diff_file_count",
            "review_claimable_files",
            "message",
        ),
        [
            (True, 0, ["a.go"], "in_scope_review_file_count must be"),
            (1, False, ["a.go"], "inline_diff_file_count must be"),
            (1, -1, ["a.go"], "inline_diff_file_count must be"),
            (1, 2, ["a.go"], "incoherent inline and review-claimable scope"),
            (3, 1, ["a.go"], "incoherent inline and review-claimable scope"),
        ],
    )
    def test_incoherent_progress_facts_reject_publication(
        self,
        tmp_path,
        monkeypatch,
        capsys,
        in_scope_review_file_count,
        inline_diff_file_count,
        review_claimable_files,
        message,
    ):
        """The one authority's own text reaches the caller unwrapped."""
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path,
            review_claimable_files=review_claimable_files,
            review_budget=40,
            in_scope_review_file_count=in_scope_review_file_count,
            inline_diff_file_count=inline_diff_file_count,
        )
        builder = ReviewOutputBuilder("123", "code")
        with pytest.raises(ValueError, match=message):
            _save_draft(builder, tmp_path)

    def test_incoherent_claim_partition_rejects_publication(
        self, tmp_path, monkeypatch, capsys
    ):
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path,
            review_claimable_files=["a.go", "b.go", "c.go"],
            review_budget=40,
            in_scope_review_file_count=1,
            inline_diff_file_count=0,
        )
        builder = ReviewOutputBuilder("123", "code")
        builder.claim_files_reviewed("a.go", "b.go")
        with pytest.raises(ValueError, match="incoherent inline and review-claimable scope counts"):
            _save_draft(builder, tmp_path)

    def test_progress_counts_unique_authoritative_claims(
        self, tmp_path, monkeypatch, capsys
    ):
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path,
            review_claimable_files=["a.go", "b.go"],
            review_budget=40,
            in_scope_review_file_count=2,
            inline_diff_file_count=0,
        )
        builder = ReviewOutputBuilder("123", "code")
        # Defensive against a caller mutating public builder state instead of
        # using claim_files_reviewed(), whose API already order-deduplicates.
        builder.reviewed_file_claims = ["a.go", "a.go"]
        _save_draft(builder, tmp_path)
        out = capsys.readouterr().out

        assert (
            "FILES NOT YET CLAIMED AS REVIEWED (1): b.go | "
            "target ~40 tool calls"
        ) in out


# =============================================================================
# TestMetaIsNeverFakeZero
# =============================================================================


class TestMetaIsNeverFakeZero:
    """meta must report facts or absence — never a default dressed as one.

    A field run's review-findings.json carried reviewed_file_count: 0 and
    review_duration_ms: 0 for an actor that ran 211 seconds. Both numbers
    were builder defaults, indistinguishable downstream from measurements.
    """

    @staticmethod
    def _stamp(path, moment=None):
        path.write_text((moment or datetime.now(timezone.utc)).isoformat())

    def test_duration_is_null_without_a_marker(self, tmp_path, monkeypatch):
        """No marker, no clock. The builder is constructed inside the final
        heredoc, so its own __init__ times the write, not the review."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        b = ReviewOutputBuilder.open(tmp_path, "1", "security")
        assert b.to_dict()["meta"][
            "review_duration_ms"
        ] is None

    def test_duration_comes_from_the_assignments_agent_marker(
        self, tmp_path, monkeypatch
    ):
        """One name, one file: the assignment says which agent this builder
        is, and that agent's marker is opened by name — not guessed at
        across four spellings."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        _write_required_assignment(tmp_path, "security")
        self._stamp(
            tmp_path / "security-reviewer.started",
            datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        b = ReviewOutputBuilder.open(tmp_path, "1", "security")
        assert 29_000 <= b.to_dict()["meta"]["review_duration_ms"] <= 40_000

    def test_duration_is_null_without_an_assignment(self, tmp_path, monkeypatch):
        """An unbound or unassigned builder has no agent name, so it has no
        marker to name. Absence stays absence."""
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        self._stamp(tmp_path / "security-reviewer.started")
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        assert b.to_dict()["meta"]["review_duration_ms"] is None

    def test_ledger_duration_comes_from_the_synthesis_marker(
        self, tmp_path, monkeypatch
    ):
        """The ledger has no assignment; it names its own synthesis marker."""
        import review.findings_ledger as _ledger

        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        self._stamp(
            tmp_path / "review-reconciliator.synthesis-started",
            datetime.now(timezone.utc) - timedelta(seconds=211),
        )
        builder = _ledger.FindingsLedgerBuilder("1", str(tmp_path))
        builder.set_reconciliation(
            grouped_concern_count=0, verified_concern_count=0,
            false_positive_concern_count=0, out_of_scope_concern_count=0,
        )
        duration = builder.to_dict()["meta"]["review_duration_ms"]
        assert 211_000 <= duration <= 225_000

    @pytest.mark.parametrize("stamp", ["", "not-a-timestamp", "   "])
    def test_unparsable_marker_yields_null_not_zero(
        self, tmp_path, monkeypatch, stamp
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        _write_required_assignment(tmp_path, "security")
        (tmp_path / "security-reviewer.started").write_text(stamp)
        b = ReviewOutputBuilder.open(tmp_path, "1", "security")
        assert b.to_dict()["meta"][
            "review_duration_ms"
        ] is None

    def test_marker_stamped_in_the_future_yields_null(
        self, tmp_path, monkeypatch
    ):
        """A negative interval is impossible under any real ordering; a
        wrong number is worse than a missing one."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        _write_required_assignment(tmp_path, "security")
        self._stamp(
            tmp_path / "security-reviewer.started",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        b = ReviewOutputBuilder.open(tmp_path, "1", "security")
        assert b.to_dict()["meta"][
            "review_duration_ms"
        ] is None

    def test_marker_names_match_their_writers(self):
        """The two marker names are spelled in the builder layer so output.py
        stays importable stand-alone. Parity with the writers is what keeps
        those copies from silently unmeasuring a whole class of actor."""
        import review.synthesis_lifecycle as _lifecycle
        import review.findings_ledger as _ledger
        import review.agent.output as _output

        bootstrap_src = (
            PLUGIN_ROOT / "scripts" / "review" / "agent" / "bootstrap.py"
        ).read_text()
        assert _ledger.SYNTHESIS_START_SUFFIX == _lifecycle.MARKER_SUFFIX
        assert _ledger.LEDGER_AGENT_NAME == _lifecycle.RECONCILIATOR
        assert (
            f'f"{{effective_agent_name}}{_output._REVIEWER_START_SUFFIX}"'
            in bootstrap_src
        )


# =============================================================================
# TestTypeScriptContractLockstep
# =============================================================================


class TestTypeScriptContractLockstep:
    """schemas/review-output.ts and the builder describe one artifact.

    The TypeScript file is the published contract downstream consumers read;
    the builder is what actually lands on disk. When they drift, a consumer
    is typed against a shape that no longer exists — and nothing fails.
    """

    @staticmethod
    def _interface_body(name: str, *, extends: str = "") -> str:
        schema = (PLUGIN_ROOT / "schemas" / "review-output.ts").read_text()
        suffix = f" extends {extends}" if extends else ""
        pattern = r"export interface " + name + suffix + r"\s*\{(.*?)\n\}"
        match = re.search(pattern, schema, re.DOTALL)
        assert match is not None, f"review-output.ts must declare {name}"
        return match.group(1)

    @classmethod
    def _review_document_interface(cls) -> str:
        """ReviewContent's body plus ReviewDocument's own extension body —
        the flattened shape a per-reviewer <reviewer>-review.json actually
        carries (`extends` means ReviewDocument's own text repeats none of
        ReviewContent's fields)."""
        return (
            cls._interface_body("ReviewContent")
            + "\n"
            + cls._interface_body("ReviewDocument", extends="ReviewContent")
        )

    def test_identity_block_matches_the_serialized_artifact(self):
        interface = self._review_document_interface()
        declared = set(re.findall(r"^\s*(\w+)\??:", interface, re.MULTILINE))
        serialized = set(ReviewOutputBuilder(pr_id="1", reviewer="pr").to_dict())

        identity = {"pr_id", "reviewer", "timestamp", "plugin_version", "schema"}
        assert identity <= declared
        assert identity <= serialized

    def test_retired_version_field_is_gone_from_both_sides(self):
        interface = self._review_document_interface()
        assert not re.search(r"^\s*version\??:", interface, re.MULTILINE)
        assert "version" not in ReviewOutputBuilder(pr_id="1", reviewer="pr").to_dict()

    def test_schema_is_declared_as_a_number(self):
        interface = self._interface_body("ReviewContent")
        match = re.search(r"^\s*schema:\s*([^;]+);", interface, re.MULTILINE)
        assert match is not None
        assert match.group(1).strip() == "number"

    def test_plugin_version_is_declared_nullable(self):
        """Absence is part of the contract, not an error state."""
        interface = self._review_document_interface()
        match = re.search(
            r"^\s*plugin_version:\s*([^;]+);", interface, re.MULTILINE
        )
        assert match is not None
        assert match.group(1).strip() == "string | null"

    def test_schema_two_rejected_outcome_is_required(self):
        """Both adjustment outcomes are derived from AdjudicationOutcome, so
        a fourth outcome cannot be added on one side only — and neither is
        optional.

        `test_ts_schema_field_sets_match_python_validators` never parses
        `CriticRejectedAdjustment`/`CriticAppliedAdjustment`, and its
        `top_level_fields()` extractor tolerates a trailing `?` on any
        field — so nothing else in this file stops `outcome` becoming
        optional or `AdjudicationOutcome` drifting.
        """
        schema = (PLUGIN_ROOT / "schemas" / "review-output.ts").read_text()
        assert "outcome: Extract<AdjudicationOutcome, 'refuted'>;" in schema
        assert "outcome: Exclude<AdjudicationOutcome, 'refuted'>;" in schema
        assert "outcome?:" not in schema

    def test_ts_schema_field_sets_match_python_validators(self):
        """schemas/review-output.ts declares exactly the field sets the two
        live Python validators require: REVIEW_CONTENT_FIELDS/REVIEWER_FIELDS
        in agent/output.py for ReviewContent/ReviewDocument, and
        RECONCILIATION_FIELDS plus the ledger's own optional extension keys
        for Reconciliation/FindingsLedger.
        """
        import review.findings_ledger as findings_ledger

        schema = (PLUGIN_ROOT / "schemas" / "review-output.ts").read_text()
        for interface in (
            "interface ReviewContent",
            "interface ReviewDocument",
            "interface FindingsLedger",
            "interface Reconciliation",
            "interface AdjudicationRequest",
        ):
            assert interface in schema
        for retired in (
            "spot_check", "CriticAdjudication", "proposal_digest: string",
            "defensive_apply", "recorded_at",
        ):
            assert retired not in schema

        def top_level_fields(body):
            return set(re.findall(r"^ {4}(\w+)\??:", body, re.MULTILINE))

        content_body = self._interface_body("ReviewContent")
        assert top_level_fields(content_body) == REVIEW_CONTENT_FIELDS | {"skip_reason"}

        document_body = self._interface_body("ReviewDocument", extends="ReviewContent")
        assert top_level_fields(document_body) == REVIEWER_FIELDS | {"schema"}

        ledger_body = self._interface_body("FindingsLedger", extends="ReviewContent")
        ledger_fields = top_level_fields(ledger_body)
        ledger_optional_fields = {
            field for field in ledger_fields if f"{field}?:" in ledger_body
        }
        assert ledger_optional_fields == critic_adjustments._LEDGER_EXTENSION_FIELDS
        # The three ReviewContent fields the ledger narrows, and the only
        # keys it requires on top of what it inherits.
        assert ledger_fields - ledger_optional_fields == {
            "schema", "verdict", "meta",
        }

        reconciliation_body = self._interface_body("Reconciliation")
        assert top_level_fields(reconciliation_body) == findings_ledger.RECONCILIATION_FIELDS

        meta_body = self._interface_body("ReviewMeta")
        assert top_level_fields(meta_body) == review_document._REQUIRED_META_FIELDS


# =============================================================================
# TestAssessment
# =============================================================================


class TestAssessment:
    """The reconciliator's overall-state prose needs a structured home.

    Before the .md became a script render, that prose lived only in a
    hand-written narrative file. Migrating it into the canonical JSON is
    what lets the renderer own the artifact without losing content.
    """

    def test_absent_by_default_but_the_key_is_always_present(self):
        d = ReviewOutputBuilder(pr_id="1", reviewer="pr").to_dict()
        assert d["assessment"] is None

    def test_set_assessment_serializes(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.set_assessment("The change is sound but under-tested.")
        assert b.to_dict()["assessment"] == (
            "The change is sound but under-tested."
        )

    def test_non_string_prose_is_coerced_like_every_other_free_field(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.set_assessment(["line one", "line two"])
        assert b.to_dict()["assessment"] == "line one\nline two"

    def test_blank_prose_records_absence_not_an_empty_string(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.set_assessment("   ")
        assert b.to_dict()["assessment"] is None

    def test_renders_as_an_assessment_section(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.set_assessment("Two sentences of judgment.")
        rendered = render_markdown(b.to_dict())
        assert "## Assessment\n\nTwo sentences of judgment." in rendered

    def test_absent_prose_renders_no_assessment_section(self):
        rendered = render_markdown(
            ReviewOutputBuilder(pr_id="1", reviewer="pr").to_dict()
        )
        assert "## Assessment" not in rendered


# =============================================================================
# TestReconciliationSectionsRender
# =============================================================================


# =============================================================================
# TestMaterializeFindingsMarkdown
# =============================================================================


# =============================================================================
# TestAssessmentProvenance
# =============================================================================


# =============================================================================
# TestRemovedByCriticSection
# =============================================================================


# =============================================================================
# TestRendererFaithfulness
# =============================================================================


class TestReviewerFilePartition:
    def _doc(self, **overrides):
        doc = canonical_review_document(
            "security", ("high",),
            review_claimable_files=("src/a.py", "src/b.py"),
            reviewed_file_claims=("src/a.py",),
        )
        doc.update(overrides)
        return doc

    def test_canonical_partition_passes(self):
        validate_review_document(self._doc(), "security")

    @pytest.mark.parametrize("overrides", [
        {"reviewed_file_claims": ["src/zzz.py"]},
        {"unclaimed_review_files": []},
        {"unclaimed_review_files": ["src/b.py", "src/a.py"]},
        {"reviewed_file_count": 999},
        {"in_scope_review_file_count": 999},
        {"reviewed_file_claims": ["src/a.py", "src/a.py"]},
        {
            "reviewed_file_claims": ["src/b.py", "src/a.py"],
            "unclaimed_review_files": [],
            "reviewed_file_count": 2,
        },
    ])
    def test_incoherent_partition_is_rejected(self, overrides):
        with pytest.raises(ValueError, match="reviewed-file"):
            validate_review_document(self._doc(**overrides), "security")


def test_validate_review_content_rejects_reviewer_fields():
    doc = canonical_review_document("security", ())
    with pytest.raises(ValueError, match="unexpected fields"):
        validate_review_content(doc, schema=2)
    content = {k: v for k, v in doc.items() if k not in REVIEWER_FIELDS}
    assert validate_review_content(content, schema=2) is content


@pytest.mark.parametrize(
    "field", ["observations", "recommendations", "positive_observations"]
)
def test_validate_review_content_rejects_null_collections(field):
    doc = canonical_review_document("security", ())
    content = {k: v for k, v in doc.items() if k not in REVIEWER_FIELDS}
    content[field] = None
    with pytest.raises(ValueError):
        validate_review_content(content, schema=2)


def test_reviewed_files_fields_projects_the_six_envelope_keys():
    """The one place the six-key shape is assembled from one derivation."""
    from review.agent.review_assignment import ReviewedFiles

    reviewed_files = ReviewedFiles(
        agent_name="security-reviewer",
        reviewer="security",
        review_claimable_files=("src/a.py", "src/b.py"),
        reviewed_file_claims=("src/a.py",),
        unclaimed_review_files=("src/b.py",),
        inline_diff_file_count=1,
        reviewed_file_count=2,
        in_scope_review_file_count=2,
        review_budget=12,
        channels=("blocking",),
    )
    assert set(reviewed_files_fields(reviewed_files)) == REVIEWER_FIELDS - {"reviewer"}


def test_missing_content_field_names_the_content_gate():
    doc = canonical_review_document("security", ())
    del doc["schema"]
    with pytest.raises(ValueError, match="missing content fields"):
        validate_review_document(doc, "security")


def test_missing_reviewed_file_field_names_the_envelope_gate():
    doc = canonical_review_document("security", ())
    del doc["review_claimable_files"]
    with pytest.raises(ValueError, match="missing reviewed-file fields"):
        validate_review_document(doc, "security")

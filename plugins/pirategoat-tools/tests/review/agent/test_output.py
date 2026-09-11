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
from review import critic_adjustments, run_paths
from review.reviewer_lifecycle import ReviewPaths, review_paths, started_marker_path

sys.path.insert(0, str(TESTS_DIR))
from helpers import ts_schema
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


def test_claiming_an_inline_file_is_a_redundant_no_op_not_a_refusal(tmp_path):
    """The reviewer received the file's diff; saying "I reviewed it" is true.

    Run 14 on pokedex: six of eighteen reviewers claimed their inline files
    alongside the claimable ones and each lost a builder call to the
    refusal. The assignment carries the inline set, so the builder drops the
    redundant claim and records only the claimable one.
    """
    write_canonical_assignment(
        tmp_path, "security",
        review_claimable_files=("src/claimable.py",),
        inline_diff_files=("src/inline.py",),
    )
    builder = ReviewOutputBuilder.open(tmp_path, "42", "security")
    builder.claim_files_reviewed("src/inline.py", "src/claimable.py")
    assert builder.reviewed_file_claims == ["src/claimable.py"]
    builder.claim_files_reviewed("./src/inline.py")
    assert builder.reviewed_file_claims == ["src/claimable.py"]


def test_claim_outside_scope_still_refuses_and_names_the_inline_rule(tmp_path):
    write_canonical_assignment(
        tmp_path, "security",
        review_claimable_files=("src/claimable.py",),
        inline_diff_files=("src/inline.py",),
    )
    builder = ReviewOutputBuilder.open(tmp_path, "42", "security")
    with pytest.raises(
        ValueError,
        match=r"'src/elsewhere.py'.*Inline files are counted automatically",
    ):
        builder.claim_files_reviewed("src/inline.py", "src/elsewhere.py")
    assert builder.reviewed_file_claims == []


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

    # Same stitch, with no claims: to_dict() carries no reviewed-file
    # fields, and render_draft_index's real caller always supplies them.
    empty_index = render_draft_index({
        **ReviewOutputBuilder(pr_id="42", reviewer="security").to_dict(),
        "reviewed_file_claims": [],
    })
    assert "reviewed-file claims 0" in empty_index
    assert "reviewed-file claim:" not in empty_index


# =============================================================================
# TestFindingAndCheckDomainModel
# =============================================================================



def _inline(count):
    """`count` distinct inline placeholder paths for a schema-5 assignment."""
    return [f"src/inline-{n}.php" for n in range(count)]

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

    def test_behavior_evidence_and_source_cited_are_stored(self):
        b = ReviewOutputBuilder(pr_id="0", reviewer="ecosystem-integration-reviewer")
        b.add_finding(
            severity="medium",
            category="behavior-assumption",
            title="State assumption mismatch",
            description="Callback reads saved status before save fires.",
            file="src/hooks.php",
            line=42,
            recommendation="Switch to woocommerce_after_order_object_save.",
            behavior_evidence="cited",
            source_cited="woocommerce/.../class-wc-order.php:200",
        )
        finding = b.to_dict()["findings"][0]
        assert finding["behavior_evidence"] == "cited"
        assert finding["source_cited"] == "woocommerce/.../class-wc-order.php:200"

    def test_behavior_evidence_and_source_cited_are_optional(self):
        b = ReviewOutputBuilder(pr_id="0", reviewer="security-reviewer")
        b.add_finding(
            severity="low", category="xss", title="X", description="y",
            file="f.php", line=1, recommendation="z",
        )
        finding = b.to_dict()["findings"][0]
        assert "behavior_evidence" not in finding
        assert "source_cited" not in finding

    def test_behavior_evidence_rejects_any_value_outside_cited_or_inferred(self):
        """'MAYBE' and 'speculative' are rejected by the same vocabulary
        check — one ValueError branch, not two."""
        b = ReviewOutputBuilder(pr_id="0", reviewer="ecosystem-integration-reviewer")
        with pytest.raises(ValueError, match="behavior_evidence"):
            b.add_finding(
                severity="low", category="other", title="T", description="d",
                file="f.php", line=1, recommendation="r",
                behavior_evidence="MAYBE",
            )
        with pytest.raises(ValueError, match="behavior_evidence"):
            b.add_finding(
                severity="low", category="behavior-assumption", title="T", description="d",
                file="f.php", line=1, recommendation="r",
                behavior_evidence="speculative",
            )


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

    def test_none_clears_an_optional_field_and_keeps_the_id(self, tmp_path):
        b = self._builder(tmp_path)
        b.add_finding(
            severity="high", title="t", file="f.py", description="d",
            recommendation="r", category="c", line=1, confidence=0.9,
            severity_floor="high",
        )
        b.update_finding("f1", severity_floor=None)
        [finding] = b.findings
        assert finding["id"] == "f1"
        assert "severity_floor" not in finding

    def test_a_required_field_cannot_be_cleared(self, tmp_path):
        b = self._builder(tmp_path)
        b.add_finding(
            severity="high", title="t", file="f.py", description="d",
            recommendation="r", category="c", line=1, confidence=0.9,
        )
        with pytest.raises(ValueError, match="cannot clear required field"):
            b.update_finding("f1", title=None)

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

    def test_empty_source_reviewers_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        with pytest.raises(ValueError, match="source_reviewers"):
            b.record_check("q", "m", "r", source_reviewers=[])

    def test_no_checks_serializes_empty_array(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        assert b.to_dict()["checks"] == []

    def test_verifies_names_the_change_purpose_items_a_check_settles(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        b.record_check("q", "m", "r", verifies=["V2", " V1 ", "V2"])
        assert b.to_dict()["checks"][0]["verifies"] == ["V2", "V1"]

    def test_a_check_without_a_citation_carries_no_verifies_key(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        b.record_check("q", "m", "r")
        assert "verifies" not in b.to_dict()["checks"][0]

    @pytest.mark.parametrize("bad", [[], ["v2"], ["V0"], ["V2", 3], "V2", ["C1"]])
    def test_verifies_must_be_verify_item_ids(self, bad):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        with pytest.raises(ValueError, match="verifies"):
            b.record_check("q", "m", "r", verifies=bad)

    def test_update_check_can_add_a_citation(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        cid = b.record_check("q", "m", "r")
        b.update_check(cid, verifies=["V3"])
        assert b.checks[0]["verifies"] == ["V3"]
        b.update_check(cid, result="changed")
        assert b.checks[0]["verifies"] == ["V3"]

    def test_empty_question_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        with pytest.raises(ValueError):
            b.record_check(question="  ", method="grep foo", result="none")

    def test_empty_method_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        with pytest.raises(ValueError):
            b.record_check(question="Any blast radius?", method="", result="none")


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

    @pytest.mark.parametrize(
        ("build", "check"),
        [
            pytest.param(
                lambda b: b.add_finding(
                    "high", "Title", "f.py", "desc",
                    ["Wire it in", "or drop it"], line=1,
                ),
                lambda finding: (
                    isinstance(finding["recommendation"], str)
                    and "Wire it in" in finding["recommendation"]
                    and "or drop it" in finding["recommendation"]
                ),
                id="list-recommendation-coerced-to-string",
            ),
            pytest.param(
                lambda b: b.add_finding(
                    "high", ["Ambiguous name"], "f.py", ["D1", "D2"], "rec",
                    line=1,
                ),
                lambda finding: (
                    isinstance(finding["title"], str)
                    and isinstance(finding["description"], str)
                    and "Ambiguous name" in finding["title"]
                    and "D1" in finding["description"]
                ),
                id="list-description-and-title-coerced",
            ),
            pytest.param(
                lambda b: b.add_finding(
                    "high", "Title", "f.py", None, None, line=1,
                ),
                lambda finding: (
                    finding["description"] == "" and finding["recommendation"] == ""
                ),
                id="none-fields-coerced-to-empty-string",
            ),
            pytest.param(
                lambda b: b.add_finding(
                    "high", ["Legit title", "## Source Snippets"], "f.py",
                    "desc", "rec", line=1,
                ),
                # Titles render inline downstream (**N. title**,
                # ### F1: title) without block-syntax escaping, so a
                # coerced newline could forge a heading — the title stays
                # single-line.
                lambda finding: (
                    "\n" not in finding["title"]
                    and "Legit title" in finding["title"]
                    and "## Source Snippets" in finding["title"]
                ),
                id="multiline-title-collapsed-to-single-line",
            ),
            pytest.param(
                lambda b: b.add_finding(
                    "high", "T", "f.py", "plain desc", "plain rec", line=1,
                ),
                lambda finding: (
                    finding["description"] == "plain desc"
                    and finding["recommendation"] == "plain rec"
                ),
                id="string-fields-unchanged",
            ),
        ],
    )
    def test_non_string_fields_are_coerced_to_strings(self, build, check):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        build(b)
        assert check(b.findings[0])


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

    @pytest.mark.parametrize(
        ("severities", "verdict"),
        [
            pytest.param((), "approve", id="no-findings"),
            pytest.param(("critical",), "block", id="one-critical"),
            pytest.param(("high", "high"), "request_changes", id="two-high"),
            pytest.param(("high", "high", "high"), "block", id="three-high"),
            pytest.param(("high",), "request_changes", id="one-high"),
            pytest.param(("medium",) * 4, "comment", id="four-medium"),
            pytest.param(("medium",) * 5, "request_changes", id="five-medium"),
            pytest.param(("medium",), "comment", id="one-medium"),
            pytest.param(
                ("low", "info", "low", "info"), "approve", id="low-and-info-only",
            ),
        ],
    )
    def test_verdict_derived_from_severity_counts(self, severities, verdict):
        b = self._builder_with_findings(severities)
        assert b.to_dict()["verdict"] == verdict


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

    @pytest.mark.parametrize(
        ("env", "run_config", "expected"),
        [
            pytest.param("1.114.0", None, "1.114.0", id="envelope"),
            pytest.param(None, None, None, id="no-envelope-no-run-config"),
            pytest.param("   ", None, None, id="blank-envelope-reads-as-unknown"),
            pytest.param(
                None, {"mode": "pr", "plugin_version": "1.114.0"}, "1.114.0",
                id="run-config-supplies-it-when-the-envelope-is-bypassed",
            ),
            pytest.param(
                "2.0.0", {"plugin_version": "1.114.0"}, "2.0.0",
                id="envelope-wins-over-run-config",
            ),
            pytest.param(
                None, "{not json", None, id="unreadable-run-config",
            ),
        ],
    )
    def test_plugin_version_resolution(
        self, monkeypatch, tmp_path, env, run_config, expected
    ):
        """The producing plugin version is a serialized artifact fact.

        bootstrap exports it via PIRATEGOAT_PLUGIN_VERSION alongside the
        other envelope variables; a bound caller that bypasses the
        envelope (review-reconciliator, dispatched by the orchestrator
        rather than bootstrap) falls back to the run's own
        run-config.json. Either way, absence is honest — never a
        required field, never a guess.
        """
        if env is None:
            monkeypatch.delenv("PIRATEGOAT_PLUGIN_VERSION", raising=False)
        else:
            monkeypatch.setenv("PIRATEGOAT_PLUGIN_VERSION", env)
        if run_config is not None:
            content = (
                run_config if isinstance(run_config, str) else json.dumps(run_config)
            )
            (tmp_path / "run-config.json").write_text(content)

        b = ReviewOutputBuilder.open(tmp_path, "1", "pr")

        assert b.to_dict()["plugin_version"] == expected

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
            reviewer_dir = Path(d, "reviewers", "security")
            assert (reviewer_dir / "review.draft.json").is_file()
            assert not (reviewer_dir / "review.json").exists()
            assert not (reviewer_dir / "review.md").exists()
            assert not list(Path(d).glob("*-review*"))

    def test_json_content_matches_to_dict(self, monkeypatch):
        with tempfile.TemporaryDirectory() as d:
            # The dispatch marker bootstrap writes — without it there is no
            # honest clock and the duration is null, which would make this
            # comparison pass for the wrong reason.
            monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", d)
            marker = Path(started_marker_path(d, "security"))
            marker.parent.mkdir(parents=True, exist_ok=True)
            with open(marker, "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.add_finding("high", "Title", "f.py", "desc", "rec", line=1)
            _write_required_assignment(d, "security")
            _save_draft(b, d)
            with open(review_paths(d, "security").draft) as f:
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
                d, "reviewers", "arch", "review.draft.json"
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

    def test_an_approve_with_nothing_recorded_gets_a_stderr_note(self, capsys):
        """php-tests-reviewer on wpcom PR #239373 published an approve with
        no findings, checks, observations or positives after its first
        builder script raised; downstream it read as a clean approve. The
        receipt says so on stderr, where the reviewer sees it."""
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            _write_required_assignment(d, "security")
            _save_draft(b, d)
            err = capsys.readouterr().err
            assert "NOTE: verdict approve with nothing recorded" in err

    @pytest.mark.parametrize("record", [
        lambda b: b.record_check("q", "m", "r"),
        lambda b: b.add_positive_observation("good"),
        lambda b: b.add_observation("c.py", "FYI"),
    ])
    def test_any_recorded_evidence_silences_the_note(self, capsys, record):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            record(b)
            _write_required_assignment(d, "security")
            _save_draft(b, d)
            assert "nothing recorded" not in capsys.readouterr().err

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
            assert not os.path.exists(review_paths(d, "security").final)
            assert not os.path.exists(review_paths(d, "security").draft)
            assert not list(Path(review_paths(d, "security").draft).parent.glob("*.tmp"))

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

    @pytest.mark.parametrize("line", [0, -1], ids=["zero", "negative"])
    def test_non_positive_line_raises(self, line):
        """Lines are 1-indexed; zero and negative are both invalid."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="line.*positive"):
            b.add_finding("high", "Title", "f.py", "desc", "rec", line=line)


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

    @pytest.mark.parametrize("bad", ["", 42], ids=["blank", "wrong-type"])
    def test_rejects_non_path_values(self, bad):
        """The full grammar is pinned once in test_review_assignment.py,
        the shared normalizer's owner; this keeps one row per branch this
        API adds on top (blank, wrong type)."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError):
            b.claim_files_reviewed(bad)

    @pytest.mark.parametrize("batch", [["src/a.py", "src/b.py"], ("src/a.py", "src/b.py")])
    def test_one_list_argument_is_the_batch(self, batch):
        """Two of sixteen wpcom reviewers on PR #239373 called
        `claim_files_reviewed([path])`; the varargs refusal aborted their
        whole publication script and one of them retried with an empty
        approve. The intent of a single list is unambiguous, so it is the
        batch, not a wrong-typed path."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.claim_files_reviewed(batch)
        assert b.reviewed_file_claims == ["src/a.py", "src/b.py"]

    def test_wrong_type_message_names_the_value(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match=r"non-empty file path \(got 42\)"):
            b.claim_files_reviewed(42)

    @pytest.mark.parametrize(
        "bad", ["/abs/a.py", "../outside.py"], ids=["absolute", "parent-traversal"]
    )
    def test_rejects_non_repo_relative_forms(self, bad):
        """A claim must address a repository-relative claimable path. The
        full grammar (including the drive-letter and dot-normalized forms)
        is pinned once in test_review_assignment.py."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError):
            b.claim_files_reviewed(bad)

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

    @pytest.mark.parametrize(
        ("claimable", "batch", "offenders"),
        [
            pytest.param(
                ["src/a.py"],
                ("src/a.py", "src/bogus1.py", "src/bogus2.py"),
                ("src/bogus1.py", "src/bogus2.py"),
                id="membership-names-every-offender",
            ),
            pytest.param(
                ["src/a.py"],
                ("src/a.py", "/abs/path.py"),
                ("/abs/path.py",),
                id="grammar-error-alone",
            ),
            pytest.param(
                ["src/a.py"],
                ("src/typo.py", "/abs/path.py"),
                ("/abs/path.py", "src/typo.py"),
                id="mixed-grammar-and-membership",
            ),
        ],
    )
    def test_batch_rejection_is_atomic_and_names_every_offender(
        self, tmp_path, claimable, batch, offenders
    ):
        """A batch either fully lands or nothing does — the same doctrine
        critic_adjustments.py enforces for its own batches — and every
        offender is named in one raise, whether the cause is a membership
        violation, a grammar violation, or both mixed in one batch.
        `test_failed_batch_leaves_no_trace_in_saved_artifact` below is the
        retry-after-failure half of this contract, at the persisted
        artifact."""
        b = self._armed_builder(tmp_path, claimable)
        with pytest.raises(ValueError) as excinfo:
            b.claim_files_reviewed(*batch)
        message = str(excinfo.value)
        for offender in offenders:
            assert offender in message
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
            review_paths(tmp_path, "sec").draft, encoding="utf-8"
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


# =============================================================================
# TestNotApplicable
# =============================================================================


class TestNotApplicable:
    """mark_not_applicable produces not_applicable verdict with skip_reason."""

    def test_verdict_is_not_applicable(self):
        """One to_dict() shape assertion, collapsing what were seven
        near-duplicate tests (skip_reason presence/absence/stripping, the
        JSON round-trip, and the normal-approve counterfactual) split
        across this class and TestFindingAndCheckDomainModel. The verdict
        assertion below is unchanged from the original
        test_verdict_is_not_applicable (Task 5 pins it at this owner)."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.mark_not_applicable("  No changes relevant to security domain  ")
        d = b.to_dict()
        assert d["verdict"] == "not_applicable"
        assert d["skip_reason"] == "No changes relevant to security domain"
        assert d["findings"] == []
        assert d["checks"] == []
        assert d["positive_observations"] == []

        parsed = json.loads(json.dumps(d))
        assert parsed["verdict"] == "not_applicable"
        assert parsed["skip_reason"] == "No changes relevant to security domain"

        normal = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        normal.add_positive_observation("Clean code")
        normal_dict = normal.to_dict()
        assert normal_dict["verdict"] == "approve"
        assert "skip_reason" not in normal_dict

    @pytest.mark.parametrize("reason", ["", "   "], ids=["empty", "whitespace-only"])
    def test_empty_or_whitespace_reason_raises(self, reason):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="reason"):
            b.mark_not_applicable(reason)

    def test_raises_if_findings_already_recorded(self):
        """mark_not_applicable rejects mixed state — findings + not_applicable is contradictory."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_finding("high", "XSS", "f.php", "desc", "rec", line=1)
        with pytest.raises(ValueError, match="finding.*already recorded"):
            b.mark_not_applicable("Agent mistakenly started before checking relevance")


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

    @pytest.mark.parametrize(
        "setup",
        [
            pytest.param("unbound", id="unbound-builder"),
            pytest.param("absent", id="absent-assignment"),
            pytest.param("malformed", id="malformed-or-undecodable-assignment"),
        ],
    )
    def test_add_time_fails_open_without_a_usable_assignment(
        self, tmp_path, setup
    ):
        """No usable assignment to consult means add-time fail-open, after
        the channel vocabulary itself has already been validated. The
        malformed row's invalid-UTF8 bytes are the same code path as an
        unparsable or incomplete JSON payload — one representative branch
        of the read-and-decode failure."""
        if setup == "unbound":
            b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        else:
            b = ReviewOutputBuilder.open(tmp_path, "1", "repo-reuse")
            if setup == "malformed":
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
        assert not Path(review_paths(tmp_path, "reconciliator").draft).exists()

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
    def _write_assignment(tmp_path, claimable, *, inline_diff_files=(), reviewer="code"):
        write_canonical_assignment(
            tmp_path, reviewer, review_claimable_files=claimable,
            inline_diff_files=inline_diff_files,
        )

    def test_draft_derives_gaps_and_counts_from_claims(self, tmp_path):
        self._write_assignment(
            tmp_path, ["src/read.ts", "src/unread.ts"], inline_diff_files=_inline(3)
        )
        builder = ReviewOutputBuilder("123", "code")
        builder.claim_files_reviewed("src/read.ts")

        _save_draft(builder, tmp_path)

        saved = json.loads(
            Path(review_paths(tmp_path, "code").draft).read_text()
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
            Path(review_paths(tmp_path, "code").draft).read_text()
        )
        assert first["unclaimed_review_files"] == ["src/b.ts"]

        builder.claim_files_reviewed("src/b.ts")
        _save_draft(builder, tmp_path)

        second = json.loads(
            Path(review_paths(tmp_path, "code").draft).read_text()
        )
        assert second["reviewed_file_claims"] == ["src/a.ts", "src/b.ts"]
        assert second["unclaimed_review_files"] == []
        assert second["reviewed_file_count"] == 2

    def test_finalized_json_preserves_derived_coverage(self, tmp_path):
        self._write_assignment(
            tmp_path, ["src/read.ts", "src/unread.ts"], inline_diff_files=_inline(2)
        )
        builder = ReviewOutputBuilder("123", "code")
        builder.claim_files_reviewed("src/read.ts")

        saved = _save_draft(builder, tmp_path)
        finalize_review(str(tmp_path), "code", saved["review_digest"])

        final = json.loads(Path(review_paths(tmp_path, "code").final).read_text())
        assert final["reviewed_file_claims"] == ["src/read.ts"]
        assert final["unclaimed_review_files"] == ["src/unread.ts"]
        assert final["reviewed_file_count"] == 3

    def test_finalization_rejects_a_raw_claim_list(self, tmp_path):
        self._write_assignment(tmp_path, ["src/read.ts"])
        builder = ReviewOutputBuilder("123", "code")
        saved = _save_draft(builder, tmp_path)
        draft_path = Path(review_paths(tmp_path, "code").draft)
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
    def _write_assignment(tmp_path, reviewer="code", schema=5, review_claimable_files=None,
                        **fields):
        review_claimable_files = review_claimable_files or []
        payload = {
            "schema": schema,
            "agent_name": f"{reviewer}-reviewer",
            "reviewer": reviewer,
            "review_claimable_files": review_claimable_files,
            "inline_diff_files": [],
            "in_scope_review_file_count": len(review_claimable_files),
            "review_budget": 15,
            "channels": ["blocking"],
        }
        payload.update(fields)
        assignment_path = Path(review_paths(tmp_path, reviewer).assignment)
        assignment_path.parent.mkdir(parents=True, exist_ok=True)
        assignment_path.write_text(
            json.dumps(payload)
        )

    def _save_with_unreviewed(self, tmp_path, monkeypatch, capsys):
        builder = ReviewOutputBuilder("123", "code")
        _save_draft(builder, tmp_path)
        return capsys.readouterr().out

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
        assignment_path = Path(review_paths(tmp_path, "code").assignment)
        assignment_path.parent.mkdir(parents=True, exist_ok=True)
        assignment_path.write_text(
            json.dumps(apply_schema(
                canonical_assignment(
                    "code", review_claimable_files=["some/file.go"]
                ),
                1,
            ))
        )
        with pytest.raises(ValueError, match="schema must be 5"):
            if publish == "final":
                self._save_with_unreviewed(tmp_path, monkeypatch, capsys)
            else:
                _save_draft(ReviewOutputBuilder("123", "code"), tmp_path)

    def test_malformed_budget_rejects_publication(
        self, tmp_path, monkeypatch, capsys
    ):
        """A malformed target is worse than no target — never repair it.
        The value space (string, negative, float, bool, absent) is pinned
        once at the derivation boundary in test_review_assignment.py; this
        confirms the publication path consults it."""
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path, review_claimable_files=["some/file.go"], review_budget="abc"
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
    def _write_assignment(tmp_path, reviewer="code", schema=5, review_claimable_files=None,
                        **fields):
        review_claimable_files = review_claimable_files or []
        payload = {
            "schema": schema,
            "agent_name": f"{reviewer}-reviewer",
            "reviewer": reviewer,
            "review_claimable_files": review_claimable_files,
            "inline_diff_files": [],
            "in_scope_review_file_count": len(review_claimable_files),
            "review_budget": 15,
            "channels": ["blocking"],
        }
        payload.update(fields)
        assignment_path = Path(review_paths(tmp_path, reviewer).assignment)
        assignment_path.parent.mkdir(parents=True, exist_ok=True)
        assignment_path.write_text(
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
            inline_diff_files=_inline(2),
        )
        builder = ReviewOutputBuilder("123", "code")
        builder.claim_files_reviewed("b.go")

        _save_draft(builder, tmp_path)
        saved = json.loads(Path(review_paths(tmp_path, "code").draft).read_text())
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
            in_scope_review_file_count=30, inline_diff_files=_inline(10),
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
            tmp_path, review_budget=80, in_scope_review_file_count=30, inline_diff_files=_inline(30),
        )
        builder = ReviewOutputBuilder("123", "code")
        _save_draft(builder, tmp_path)
        out = capsys.readouterr().out
        assert "FILES NOT YET CLAIMED" not in out

    def test_missing_scope_counts_reject_progress_publication(
        self, tmp_path, monkeypatch, capsys
    ):
        """The assignment-shape value space (this and every other incoherent
        or missing scope-count combination) is pinned once at the
        derivation boundary in
        test_review_assignment.py::test_validates_schema_identity_paths_and_conserved_counts;
        this confirms the publication path consults it."""
        self._clean_env(monkeypatch)
        self._write_assignment(
            tmp_path, review_claimable_files=["a.go", "b.go"], review_budget=40,
            in_scope_review_file_count=None,
        )
        builder = ReviewOutputBuilder("123", "code")
        with pytest.raises(ValueError, match="in_scope_review_file_count"):
            _save_draft(builder, tmp_path)


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
        path.parent.mkdir(parents=True, exist_ok=True)
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
            Path(started_marker_path(tmp_path, "security")),
            datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        b = ReviewOutputBuilder.open(tmp_path, "1", "security")
        assert 29_000 <= b.to_dict()["meta"]["review_duration_ms"] <= 40_000

    def test_dispatch_identity_ending_in_reviewer_is_derived_once(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        reviewer = "repo-foo-reviewer"
        write_canonical_assignment(
            tmp_path,
            reviewer,
            agent_name="repo-foo-reviewer-reviewer",
        )
        self._stamp(
            Path(started_marker_path(tmp_path, reviewer)),
            datetime.now(timezone.utc) - timedelta(seconds=30),
        )

        builder = ReviewOutputBuilder.open(tmp_path, "1", reviewer)

        assert 29_000 <= (
            builder.to_dict()["meta"]["review_duration_ms"]
        ) <= 40_000

    def test_duration_is_null_without_an_assignment(self, tmp_path, monkeypatch):
        """An unbound or unassigned builder has no agent name, so it has no
        marker to name. Absence stays absence."""
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        self._stamp(Path(started_marker_path(tmp_path, "security")))
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        assert b.to_dict()["meta"]["review_duration_ms"] is None

    def test_ledger_duration_comes_from_the_synthesis_marker(
        self, tmp_path, monkeypatch
    ):
        """The ledger has no assignment; it names its own synthesis marker."""
        import review.findings_ledger as _ledger

        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        self._stamp(
            run_paths.synthesis_started_marker(
                tmp_path, _ledger.LEDGER_AGENT_NAME
            ),
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
        Path(started_marker_path(tmp_path, "security")).write_text(stamp)
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
            Path(started_marker_path(tmp_path, "security")),
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
        assert _ledger.LEDGER_AGENT_NAME == _lifecycle.RECONCILIATOR
        assert _lifecycle.marker_path(
            "/out", _ledger.LEDGER_AGENT_NAME
        ) == str(run_paths.synthesis_started_marker(
            "/out", _ledger.LEDGER_AGENT_NAME
        ))
        assert Path(started_marker_path("/out", "security")) == Path(
            "/out/reviewers/security/started"
        )


# =============================================================================
# TestTypeScriptContractLockstep
# =============================================================================


class TestTypeScriptContractLockstep:
    """schemas/review-output.ts and the builder describe one artifact.

    The TypeScript file is the published contract downstream consumers read;
    the builder is what actually lands on disk. When they drift, a consumer
    is typed against a shape that no longer exists — and nothing fails.

    The ledger/critic-type parity tests moved to `test_critic_adjustments.py`
    and `test_findings_ledger.py` (G7); text-extraction helpers now live in
    `helpers/ts_schema.py`, shared by all three files.
    """

    _interface_body = staticmethod(ts_schema.interface_body)

    @classmethod
    def _review_document_interface(cls) -> str:
        """ReviewContent's body plus ReviewDocument's own extension body —
        the flattened shape a per-reviewer review.json actually
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

    def test_review_check_declares_the_optional_citation(self):
        interface = self._interface_body("ReviewCheck")
        assert re.search(r"^\s*verifies\?:\s*string\[\];", interface, re.MULTILINE), (
            "ReviewCheck.verifies?: string[] must be declared beside source_reviewers"
        )

    def test_plugin_version_is_declared_nullable(self):
        """Absence is part of the contract, not an error state."""
        interface = self._review_document_interface()
        match = re.search(
            r"^\s*plugin_version:\s*([^;]+);", interface, re.MULTILINE
        )
        assert match is not None
        assert match.group(1).strip() == "string | null"

    def test_ts_schema_field_sets_match_python_validators(self):
        """schemas/review-output.ts declares exactly the field sets the two
        live Python validators require: REVIEW_CONTENT_FIELDS/REVIEWER_FIELDS
        in review_document.py for ReviewContent/ReviewDocument, and
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

    _field_types = staticmethod(ts_schema.field_types)
    _type_alias = staticmethod(ts_schema.type_alias)

    @pytest.mark.parametrize(
        "field", ["observations", "recommendations", "positive_observations"]
    )
    def test_the_always_serialized_lists_are_not_declared_nullable(self, field):
        """Three fields the builder always emits, declared as it emits them.

        They were `| null` because a producer with nothing to record used
        to write `null`. The builder now serializes `[]` and the
        three-key object unconditionally, and the validator requires them
        non-null — so a `| null` in the schema would describe a document
        this plugin can no longer produce, and a consumer written against
        it would carry a branch that never runs.
        """
        content_body = self._interface_body("ReviewContent")
        if field == "recommendations":
            match = re.search(
                r"^ {4}recommendations:\s*\{.*?\n {4}\};",
                content_body,
                re.DOTALL | re.MULTILINE,
            )
            assert match is not None, "recommendations must be declared"
            declaration = match.group(0)
        else:
            declaration = next(
                line for line in content_body.splitlines()
                if line.strip().startswith(f"{field}:")
            )
        assert "| null" not in declaration

        document = ReviewOutputBuilder(pr_id="1", reviewer="sec").to_dict()
        assert document[field] is not None


# =============================================================================
# TestAssessment
# =============================================================================


class TestAssessment:
    """The reconciliator's overall-state prose needs a structured home.

    Before the .md became a script render, that prose lived only in a
    hand-written narrative file. Migrating it into the canonical JSON is
    what lets the renderer own the artifact without losing content.
    """

    def test_absent_by_default_and_blank_prose_both_record_absence(self):
        assert (
            ReviewOutputBuilder(pr_id="1", reviewer="pr").to_dict()["assessment"]
            is None
        )

        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.set_assessment("   ")
        assert b.to_dict()["assessment"] is None

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
        inline_diff_files=("src/z.py",),
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


def test_builder_timestamp_is_aware_utc():
    """Every other run artifact, marker and telemetry event is aware UTC; the
    review's own timestamp was the one naive local clock an auditor had to
    shift by hand (run e08e: `2026-09-08T14:52:52.137095` for a 11:52Z finish)."""
    stamp = datetime.fromisoformat(ReviewOutputBuilder("42", "security").timestamp)
    assert stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0)


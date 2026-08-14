"""
Tests for ReviewOutputBuilder — direct unit tests on the producer API.

Validates the structured review output builder that all reviewer agents use
to emit findings. Tests cover initialization, issue addition with validation,
recommendations, verdicts, serialization (dict, JSON, markdown), and file output.

Zero external dependencies beyond stdlib + pytest.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import ReviewOutputBuilder from scripts/
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent.parent.parent  # agent/ -> review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review.agent.output import (
    ReviewOutputBuilder,
    materialize_markdown,
    render_markdown,
)


# =============================================================================
# TestAddIssue
# =============================================================================


class TestAddIssue:
    """add_issue validates inputs, stores all fields, and returns an ID."""

    def test_returns_8_char_id(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        issue_id = b.add_issue("high", "Title", "f.py", "desc", "rec", line=1)
        assert isinstance(issue_id, str)
        assert len(issue_id) == 8

    def test_stores_all_fields(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue(
            severity="high",
            title="SQL Injection",
            file="src/db.php",
            description="Direct input in query",
            recommendation="Use prepared statements",
            category="sql-injection",
            line=42,
            confidence=0.9,
        )
        issue = b.issues[0]
        assert issue["severity"] == "high"
        assert issue["title"] == "SQL Injection"
        assert issue["file"] == "src/db.php"
        assert issue["description"] == "Direct input in query"
        assert issue["recommendation"] == "Use prepared statements"
        assert issue["category"] == "sql-injection"
        assert issue["line"] == 42
        assert issue["confidence"] == 0.9

    def test_severity_case_insensitive(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("HIGH", "Title", "f.py", "desc", "rec", line=1)
        assert b.issues[0]["severity"] == "high"

    def test_invalid_severity_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="Invalid severity"):
            b.add_issue("urgent", "Title", "f.py", "desc", "rec", line=1)

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

        b.add_issue(
            severity,
            "Title",
            "f.php",
            "desc",
            "rec",
            line=1,
            severity_floor=floor,
        )

        issue = b.issues[0]
        assert issue["severity"] == expected
        assert issue["severity_floor"] == floor.lower()

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
            b.add_issue(
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

        b.add_issue("medium", "Title", "f.php", "desc", "rec", line=1)

        assert "severity_floor" not in b.issues[0]

    def test_markdown_renders_severity_floor(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="woo-regression")
        b.add_issue(
            "medium",
            "Title",
            "f.php",
            "desc",
            "rec",
            line=1,
            severity_floor="medium",
        )

        assert "**Severity floor:** medium" in b.to_markdown()

    def test_confidence_boundaries_valid(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("high", "A", "f.py", "d", "r", line=1, confidence=0.0)
        b.add_issue("high", "B", "f.py", "d", "r", line=2, confidence=1.0)
        assert b.issues[0]["confidence"] == 0.0
        assert b.issues[1]["confidence"] == 1.0

    def test_confidence_boundaries_invalid(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="Confidence"):
            b.add_issue("high", "A", "f.py", "d", "r", line=1, confidence=-0.1)
        with pytest.raises(ValueError, match="Confidence"):
            b.add_issue("high", "B", "f.py", "d", "r", line=1, confidence=1.1)

    def test_extra_kwargs_preserved(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue(
            "high", "Title", "f.py", "desc", "rec",
            line=1,
            vulnerability_type="xss",
            cwe_id="CWE-79",
        )
        issue = b.issues[0]
        assert issue["vulnerability_type"] == "xss"
        assert issue["cwe_id"] == "CWE-79"

    def test_line_default_none_records_file_scoped_issue(self):
        """Default line=None records a first-class file-scoped issue."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        issue_id = b.add_issue("medium", "Title", "f.py", "desc", "rec")
        assert len(b.issues) == 1
        assert len(b.observations) == 0
        assert isinstance(issue_id, str) and len(issue_id) == 8


# =============================================================================
# TestAddClearance
# =============================================================================


class TestAddClearance:
    """add_clearance records auditable 'nothing depends on this' claims.

    Clearances exist so blast-radius clears carry their verification method
    downstream — the 2026-07-16 run had three agents clear a regression via
    the same wrong grep, invisible to reconciliation because clears lived in
    free-text positives."""

    def test_stores_claim_method_evidence(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        b.add_clearance(
            claim="No CSS or JS depends on the removed label element",
            method="grep -rn 'th label' client/legacy/css/; read each hit",
            evidence="3 occurrences read: admin.scss:5354, :5367, :5567",
        )
        d = b.to_dict()
        assert d["clearances"] == [{
            "claim": "No CSS or JS depends on the removed label element",
            "method": "grep -rn 'th label' client/legacy/css/; read each hit",
            "evidence": "3 occurrences read: admin.scss:5354, :5367, :5567",
        }]

    def test_evidence_optional(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        b.add_clearance(claim="No E2E test targets the radio row", method="grep 'radio' e2e/")
        assert b.to_dict()["clearances"][0]["evidence"] is None

    def test_no_clearances_serializes_none(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        assert b.to_dict()["clearances"] is None

    def test_empty_claim_raises(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        with pytest.raises(ValueError):
            b.add_clearance(claim="  ", method="grep foo")

    def test_empty_method_raises(self):
        """A clearance without its method is exactly the unauditable claim
        this API exists to prevent."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        with pytest.raises(ValueError):
            b.add_clearance(claim="No blast radius", method="")

    def test_renders_in_markdown_with_method(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        b.add_clearance(claim="No CSS depends on the label", method="grep 'th label' admin.scss")
        md = b.to_markdown()
        assert "## Clearances" in md
        assert "No CSS depends on the label" in md
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
    """add_issue coerces free-form text fields to strings.

    Regression: a reviewer emitted a list-valued ``recommendation`` that reached
    the reconciliation Markdown renderer and crashed the whole pipeline. The
    producer must never write a non-string title/description/recommendation.
    """

    def test_list_recommendation_coerced_to_string(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue(
            "high", "Title", "f.py", "desc",
            ["Wire it in", "or drop it"], line=1,
        )
        rec = b.issues[0]["recommendation"]
        assert isinstance(rec, str)
        assert "Wire it in" in rec and "or drop it" in rec

    def test_list_description_and_title_coerced(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue(
            "high", ["Ambiguous name"], "f.py", ["D1", "D2"], "rec", line=1,
        )
        assert isinstance(b.issues[0]["title"], str)
        assert isinstance(b.issues[0]["description"], str)
        assert "Ambiguous name" in b.issues[0]["title"]
        assert "D1" in b.issues[0]["description"]

    def test_none_fields_coerced_to_empty_string(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("high", "Title", "f.py", None, None, line=1)
        assert b.issues[0]["description"] == ""
        assert b.issues[0]["recommendation"] == ""

    def test_multiline_title_collapsed_to_single_line(self):
        # Titles render inline downstream (**N. title**, ### F1: title) without
        # block-syntax escaping, so a coerced newline could forge a heading.
        # The producer must keep the title single-line.
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue(
            "high", ["Legit title", "## Source Snippets"], "f.py",
            "desc", "rec", line=1,
        )
        title = b.issues[0]["title"]
        assert "\n" not in title
        assert "Legit title" in title and "## Source Snippets" in title

    def test_string_fields_unchanged(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("high", "T", "f.py", "plain desc", "plain rec", line=1)
        assert b.issues[0]["description"] == "plain desc"
        assert b.issues[0]["recommendation"] == "plain rec"


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
# TestAddToolResult
# =============================================================================


class TestAddToolResult:
    """add_tool_result stores tool names and deduplicates."""

    def test_deduplicates(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_tool_result("grep")
        b.add_tool_result("grep")
        b.add_tool_result("ast-grep")
        assert b.tool_results_used == ["grep", "ast-grep"]


# =============================================================================
# TestCalculateVerdict
# =============================================================================


class TestCalculateVerdict:
    """_calculate_verdict auto-selects verdict from issue severity counts."""

    def _builder_with_issues(self, severities):
        """Create a builder with issues at given severity levels."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        for i, sev in enumerate(severities):
            b.add_issue(sev, f"Issue {i}", f"f{i}.py", "desc", "rec", line=i + 1)
        return b

    def test_no_issues_approve(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        assert b._calculate_verdict() == "approve"

    def test_one_critical_blocks(self):
        b = self._builder_with_issues(["critical"])
        assert b._calculate_verdict() == "block"

    def test_two_high_request_changes(self):
        b = self._builder_with_issues(["high", "high"])
        verdict = b._calculate_verdict()
        assert verdict == "request_changes"
        assert verdict != "block"

    def test_three_high_blocks(self):
        b = self._builder_with_issues(["high", "high", "high"])
        assert b._calculate_verdict() == "block"

    def test_one_high_request_changes(self):
        b = self._builder_with_issues(["high"])
        assert b._calculate_verdict() == "request_changes"

    def test_four_medium_comment(self):
        b = self._builder_with_issues(["medium"] * 4)
        verdict = b._calculate_verdict()
        assert verdict == "comment"
        assert verdict != "request_changes"

    def test_five_medium_request_changes(self):
        b = self._builder_with_issues(["medium"] * 5)
        assert b._calculate_verdict() == "request_changes"

    def test_one_medium_comment(self):
        b = self._builder_with_issues(["medium"])
        assert b._calculate_verdict() == "comment"

    def test_low_and_info_only_approve(self):
        b = self._builder_with_issues(["low", "info", "low", "info"])
        assert b._calculate_verdict() == "approve"


# =============================================================================
# TestToDict
# =============================================================================


class TestToDict:
    """to_dict produces correct structure."""

    def test_all_top_level_keys(self):
        b = ReviewOutputBuilder(pr_id="99", reviewer="arch")
        b.add_issue("medium", "Title", "f.py", "desc", "rec", line=1)
        d = b.to_dict()
        expected_keys = {
            "pr_id", "reviewer", "timestamp", "version", "verdict",
            "summary", "issues", "unreviewed", "observations",
            "recommendations", "positive_observations", "clearances", "meta",
        }
        assert expected_keys == set(d.keys())

    def test_severity_counts_correct(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_issue("critical", "A", "a.py", "d", "r", line=1)
        b.add_issue("high", "B", "b.py", "d", "r", line=2)
        b.add_issue("high", "C", "c.py", "d", "r", line=3)
        b.add_issue("medium", "D", "d.py", "d", "r", line=4)
        d = b.to_dict()
        counts = d["summary"]["by_severity"]
        assert counts["critical"] == 1
        assert counts["high"] == 2
        assert counts["medium"] == 1
        assert counts["low"] == 0
        assert counts["info"] == 0

    def test_meta_structure(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.set_files_reviewed(10)
        b.set_confidence(0.8)
        b.add_tool_result("grep")
        d = b.to_dict()
        meta = d["meta"]
        assert meta["files_reviewed"] == 10
        assert meta["confidence_score"] == 0.8
        assert meta["tool_results_used"] == ["grep"]
        assert "review_duration_ms" in meta

    def test_no_channel_records_zero_advisory_suppression(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        b.add_issue("high", "Title", "f.py", "desc", "rec", line=1)

        summary = b.to_dict()["summary"]

        assert summary["advisory_suppressed"] == 0
        assert "verdict_without_advisory" not in summary


# =============================================================================
# TestToMarkdown
# =============================================================================


class TestToMarkdown:
    """to_markdown produces human-readable output."""

    def test_header_format(self):
        b = ReviewOutputBuilder(pr_id="42", reviewer="security")
        md = b.to_markdown()
        assert "# Security Review - PR #42" in md

    def test_issues_grouped_by_severity(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_issue("low", "Low Issue", "a.py", "desc", "rec", line=1)
        b.add_issue("critical", "Critical Issue", "b.py", "desc", "rec", line=2)
        b.add_issue("high", "High Issue", "c.py", "desc", "rec", line=3)
        md = b.to_markdown()
        # Critical section should appear before High, High before Low
        crit_pos = md.index("## Critical Issues")
        high_pos = md.index("## High Issues")
        low_pos = md.index("## Low Issues")
        assert crit_pos < high_pos < low_pos

    def test_positive_observations_section(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_positive("Well-structured code")
        md = b.to_markdown()
        assert "## Positive Observations" in md
        assert "Well-structured code" in md

    def test_info_issues_render_in_markdown(self):
        """Info issues count toward total_issues, so Markdown must show them —
        omitting them reports 'Total Issues: 1' with no visible finding."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_issue("info", "Anchored info finding", "a.py", "desc", "rec", line=3)
        md = b.to_markdown()
        assert "## Info Issues" in md
        assert "Anchored info finding" in md

    def test_file_scoped_info_issue_renders_in_markdown(self):
        """A line-less info finding used to at least appear under Observations
        (via the old demotion); as a first-class issue it must not vanish."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_issue("info", "File-scoped info finding", "a.py", "desc", "rec", line=None)
        md = b.to_markdown()
        assert "## Info Issues" in md
        assert "File-scoped info finding" in md
        assert "`a.py` (file-scoped)" in md


# =============================================================================
# TestRenderMarkdown
# =============================================================================


class TestRenderMarkdown:
    """Markdown is a pure function of the canonical JSON dict."""

    @staticmethod
    def _rich_builder():
        b = ReviewOutputBuilder(pr_id="7", reviewer="security")
        b.add_issue("high", "Title A", "a.py", "desc", "rec", line=3)
        b.add_issue("info", "Note B", "b.py", "desc", "rec", line=None)
        b.add_observation("c.py", "an observation")
        b.add_positive("something good")
        b.add_clearance(claim="no X remains", method="grep -rn X", evidence="0 hits")
        b.add_unreviewed("z.py")
        b.set_files_reviewed(3)
        return b

    def test_matches_builder_to_markdown(self):
        b = self._rich_builder()
        assert render_markdown(b.to_dict()) == b.to_markdown()

    def test_round_trips_through_serialized_json(self):
        """Rendering from the FILE representation — what materialization
        does — must equal rendering from the live builder."""
        b = self._rich_builder()
        assert render_markdown(json.loads(b.to_json())) == b.to_markdown()

    def test_legacy_issue_shape_renders_plain_file_location(self):
        """*-review.json files from builder versions predating the `scope`
        field carry line=null with no scope key — the renderer must fall
        back to the plain file location, not crash or mislabel."""
        data = self._rich_builder().to_dict()
        data["issues"] = [{
            "severity": "high",
            "title": "Legacy issue",
            "file": "f.py",
            "line": None,
            "description": "d",
            "recommendation": "r",
        }]
        data["summary"] = {
            "total_issues": 1,
            "by_severity": {"critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0},
        }
        rendered = render_markdown(data)
        assert "**File:** `f.py`\n" in rendered
        assert "(file-scoped)" not in rendered

    def test_legacy_summary_without_advisory_measurement_still_renders(self):
        data = self._rich_builder().to_dict()
        data["summary"].pop("advisory_suppressed")
        data["summary"].pop("verdict_without_advisory", None)

        rendered = render_markdown(data)

        assert "# Security Review" in rendered
        assert "Advisory suppression" not in rendered


# =============================================================================
# TestMaterializeMarkdown
# =============================================================================


class TestMaterializeMarkdown:
    def test_writes_md_beside_every_review_json(self):
        with tempfile.TemporaryDirectory() as d:
            for reviewer in ("security", "performance"):
                b = ReviewOutputBuilder(pr_id="1", reviewer=reviewer)
                b.add_issue("high", "T", "f.py", "d", "r", line=1)
                b.save(d)
            written = materialize_markdown(d)
            assert sorted(os.path.basename(p) for p in written) == [
                "performance-review.md", "security-review.md",
            ]
            with open(os.path.join(d, "security-review.json")) as f:
                data = json.load(f)
            md_text = Path(d, "security-review.md").read_text()
            assert md_text == render_markdown(data)

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.save(d)
            first = materialize_markdown(d)
            second = materialize_markdown(d)
            assert first == second
            assert Path(d, "security-review.md").is_file()

    def test_skips_malformed_json_without_raising(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "broken-review.json").write_text("{ not json")
            ReviewOutputBuilder(pr_id="1", reviewer="security").save(d)
            written = materialize_markdown(d)
            assert [os.path.basename(p) for p in written] == ["security-review.md"]
            assert not Path(d, "broken-review.md").exists()

    def test_skips_valid_json_missing_required_keys(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "empty-review.json").write_text("{}")
            written = materialize_markdown(d)
            assert written == []
            assert not Path(d, "empty-review.md").exists()
            assert "skipped empty-review.json" in capsys.readouterr().err

    def test_render_cli_prints_markdown(self):
        output_py = Path(__file__).parents[3] / "scripts" / "review" / "agent" / "output.py"
        assert output_py.is_file(), output_py  # layout guard: tests/review/agent -> plugin root
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.add_issue("high", "CLI Title", "f.py", "d", "r", line=1)
            b.save(d)
            result = subprocess.run(
                [sys.executable, str(output_py), "render",
                 os.path.join(d, "security-review.json")],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert "CLI Title" in result.stdout
            assert "## Executive Summary" in result.stdout

    def test_materialize_cli_prints_written_paths(self):
        output_py = Path(__file__).parents[3] / "scripts" / "review" / "agent" / "output.py"
        assert output_py.is_file(), output_py
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.save(d)
            md_path = Path(d, "security-review.md")
            assert not md_path.exists()  # save() publishes the JSON only
            result = subprocess.run(
                [sys.executable, str(output_py), "materialize", d],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert str(md_path) in result.stdout
            assert md_path.is_file()


# =============================================================================
# TestSave
# =============================================================================


class TestSave:
    """save publishes the review JSON — the single canonical artifact."""

    def test_creates_only_the_canonical_json(self):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.add_issue("high", "Title", "f.py", "desc", "rec", line=1)
            b.save(d)
            assert os.path.isfile(os.path.join(d, "security-review.json"))
            # Markdown is derived from the JSON on demand (render/
            # materialize) — save() writing it would resurrect the
            # artifact-pair consistency problem this contract removed.
            assert not os.path.exists(os.path.join(d, "security-review.md"))

    def test_json_content_matches_to_dict(self):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.add_issue("high", "Title", "f.py", "desc", "rec", line=1)
            b.save(d)
            with open(os.path.join(d, "security-review.json")) as f:
                saved = json.load(f)
            live = b.to_dict()

            # review_duration_ms is recomputed from the clock on every
            # to_dict() call, so it differs whenever save() and this
            # assertion straddle a millisecond. Assert it independently
            # and compare the rest exactly.
            assert isinstance(saved["meta"]["review_duration_ms"], int)
            saved["meta"].pop("review_duration_ms")
            live["meta"].pop("review_duration_ms")
            assert saved == live

    def test_return_value_has_correct_paths(self):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="arch")
            result = b.save(d)
            assert result == {"json": os.path.join(d, "arch-review.json")}

    def test_prints_recorded_counts_to_stdout(self, capsys):
        """save() echoes the SAVED state so agents can reconcile their
        self-reported COUNTS against what was actually recorded (an agent
        reporting from intent masked the line=None demotion for 60 days)."""
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.add_issue("high", "A", "a.py", "d", "r", line=1)
            b.add_issue("medium", "B", "b.py", "d", "r", line=2)
            b.add_observation("c.py", "FYI note")
            b.save(d)
            out = capsys.readouterr().out
            assert "RECORDED COUNTS: critical: 0, high: 1, medium: 1, low: 0, info: 0" in out
            assert "RECORDED ISSUES: 2" in out
            assert "OBSERVATIONS: 1" in out
            assert "VERDICT: request_changes" in out

    def test_prints_zero_counts_when_empty(self, capsys):
        """An empty save is echoed too — '0 issues recorded' must be visible."""
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.save(d)
            out = capsys.readouterr().out
            assert "RECORDED ISSUES: 0" in out
            assert "VERDICT: approve" in out

    def test_completion_telemetry_precedes_the_readiness_artifact(
        self, monkeypatch
    ):
        """The review JSON is the readiness signal agents_status.py polls;
        the pipeline may finalize the telemetry manifest as soon as it
        appears. agent_complete must therefore be durable BEFORE the JSON
        exists, or a racing finalize records the agent permanently
        incomplete."""
        import review.agent.output as output_mod

        seen = {}

        def _record(output_dir, reviewer, verdict, issue_count, severities):
            seen["json_visible_at_telemetry"] = os.path.isfile(
                os.path.join(output_dir, "security-review.json")
            )
            seen["reviewer"] = reviewer

        monkeypatch.setattr(
            output_mod, "_log_agent_complete_telemetry", _record
        )
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.save(d)
            assert seen["json_visible_at_telemetry"] is False
            assert seen["reviewer"] == "security-reviewer"
            assert os.path.isfile(os.path.join(d, "security-review.json"))
            assert not list(Path(d).glob("*.tmp"))

    @staticmethod
    def _race_a_retry_at_lock_acquisition(monkeypatch, output_mod, d, retry_save):
        """Run retry_save() inside the outer save's window between staging
        and publication — triggered at the outer save's os.open of the
        output dir (its lock acquisition), i.e. just BEFORE it takes the
        publication lock. Injecting from inside the lock (the old telemetry
        hook point) would deadlock now that completion telemetry runs under
        the lock: flock treats a second fd as an independent owner. Returns
        the mutable raced list; callers MUST assert it is non-empty or lock
        primitive drift turns the race test into a no-op."""
        if output_mod.fcntl is None:
            pytest.skip("publication lock requires fcntl (POSIX)")
        real_open = os.open
        raced = []

        def _open_hook(path, *args, **kwargs):
            if not raced and str(path) == str(d):
                raced.append(True)
                retry_save()
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(output_mod.os, "open", _open_hook)
        return raced

    def test_overlapping_saves_of_the_same_reviewer_do_not_collide(
        self, monkeypatch
    ):
        """The lifecycle supports retrying a reviewer before its prior
        invocation finishes, so two saves for the same reviewer can be
        in flight at once. A shared staging name lets the faster save's
        os.replace() consume the slower save's staged JSON, crashing it
        with FileNotFoundError."""
        import review.agent.output as output_mod

        with tempfile.TemporaryDirectory() as d:
            raced = self._race_a_retry_at_lock_acquisition(
                monkeypatch, output_mod, d,
                lambda: ReviewOutputBuilder(pr_id="1", reviewer="security").save(d),
            )
            ReviewOutputBuilder(pr_id="1", reviewer="security").save(d)
            assert raced, (
                "Race hook never fired; lock acquisition no longer goes through "
                "os.open(output_dir); update injection point."
            )
            assert os.path.isfile(os.path.join(d, "security-review.json"))
            assert not list(Path(d).glob("*.tmp"))

    def test_latest_completion_telemetry_matches_the_published_json(
        self, monkeypatch
    ):
        """Scheduling reads the manifest's latest agent_complete as the
        agent's final execution. When saves overlap, the completion logged
        last and the JSON published last must belong to the SAME execution
        — telemetry logged outside the publication lock let a slower save
        publish its artifacts after a faster retry logged its completion."""
        import review.agent.output as output_mod

        def _builder_with_issues(count):
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            for index in range(count):
                b.add_issue(
                    severity="low",
                    category="test",
                    title=f"Finding {index}",
                    description="d",
                    file="src/f.py",
                    line=index + 1,
                    recommendation="r",
                )
            return b

        completions = []

        def _record(output_dir, reviewer, verdict, issue_count, severities):
            completions.append(issue_count)

        monkeypatch.setattr(
            output_mod, "_log_agent_complete_telemetry", _record
        )
        with tempfile.TemporaryDirectory() as d:
            raced = self._race_a_retry_at_lock_acquisition(
                monkeypatch, output_mod, d,
                lambda: _builder_with_issues(2).save(d),
            )
            _builder_with_issues(1).save(d)
            assert raced, (
                "Race hook never fired; lock acquisition no longer goes through "
                "os.open(output_dir); update injection point."
            )

            with open(os.path.join(d, "security-review.json")) as f:
                published_count = len(json.load(f)["issues"])
            assert completions[-1] == published_count

    def test_failed_save_removes_its_staged_file(self, monkeypatch):
        """Unique staging names never self-overwrite the way the old fixed
        name did, so a save that dies before publishing must clean up its
        own orphan."""
        import review.agent.output as output_mod

        def _boom(*args):
            raise RuntimeError("telemetry backend exploded")

        monkeypatch.setattr(
            output_mod, "_log_agent_complete_telemetry", _boom
        )
        with tempfile.TemporaryDirectory() as d:
            with pytest.raises(RuntimeError):
                ReviewOutputBuilder(pr_id="1", reviewer="security").save(d)
            assert not os.path.exists(os.path.join(d, "security-review.json"))
            assert not list(Path(d).glob("*.tmp"))


# =============================================================================
# TestFileScopedIssues
# =============================================================================


class TestFileScopedIssues:
    """line=None records a first-class file-scoped issue (no silent demotion).

    Some finding classes are line-less BY NATURE — missing test coverage,
    missing assertions, git-history precedent, cross-file architecture. These
    must count toward the verdict, not vanish into observations. Point defects
    still require line= (invalid line values raise; the file-scoped path warns
    on stderr so lazy line omission stays loud).
    """

    def test_line_none_records_issue_with_null_line_and_file_scope(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        issue_id = b.add_issue("high", "Title", "f.py", "desc", "rec", line=None)
        assert isinstance(issue_id, str) and len(issue_id) == 8
        assert len(b.issues) == 1
        assert len(b.observations) == 0
        issue = b.issues[0]
        assert issue["line"] is None
        assert issue["scope"] == "file"
        assert issue["id"] == issue_id

    def test_reproduction_lineless_high_counts_toward_severity_and_verdict(self):
        """The RCA reproduction: a line-less HIGH must not silently drop."""
        b = ReviewOutputBuilder(pr_id="0", reviewer="js-tests")
        b.add_issue(
            severity="high",
            title="whole-file has no test",
            file="src/foo.ts",
            description="...",
            recommendation="...",
            category="missing-coverage",
        )
        d = b.to_dict()
        assert d["summary"]["by_severity"]["high"] == 1
        assert d["summary"]["total_issues"] == 1
        assert len(d["issues"]) == 1
        assert d["verdict"] == "request_changes"

    def test_line_none_prints_stderr_note(self, capsys):
        """The file-scoped path is loud — names the title and severity."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("high", "Missing coverage", "f.py", "desc", "rec", line=None)
        err = capsys.readouterr().err
        assert "file-scoped" in err.lower()
        assert "Missing coverage" in err
        assert "high" in err.lower()

    def test_line_anchored_issue_has_no_scope_field(self):
        """Schema stays additive — line-anchored issues are unchanged."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("high", "Title", "f.py", "desc", "rec", line=42)
        assert "scope" not in b.issues[0]
        assert b.issues[0]["line"] == 42

    def test_file_scoped_issue_renders_under_severity_section(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="js-tests")
        b.add_issue(
            "high", "whole-file has no test", "src/foo.ts", "desc", "rec",
            category="missing-coverage",
        )
        md = b.to_markdown()
        assert "## High Issues" in md
        assert "whole-file has no test" in md
        assert "`src/foo.ts` (file-scoped)" in md

    def test_file_scoped_issue_json_roundtrip(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("medium", "Title", "f.py", "desc", "rec", line=None)
        parsed = json.loads(b.to_json())
        assert parsed["issues"][0]["line"] is None
        assert parsed["issues"][0]["scope"] == "file"


# =============================================================================
# TestLineRequired
# =============================================================================


class TestLineRequired:
    """Invalid line values still raise (protocol enforcement for point defects)."""

    def test_line_zero_raises(self):
        """Line 0 is invalid (lines are 1-indexed)."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="line.*positive"):
            b.add_issue("high", "Title", "f.py", "desc", "rec", line=0)

    def test_line_negative_raises(self):
        """Negative line is invalid."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="line.*positive"):
            b.add_issue("high", "Title", "f.py", "desc", "rec", line=-1)


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
        """Observations don't count as issues — verdict unaffected."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_observation("f.py", "Looks risky", category="security")
        assert b._calculate_verdict() == "approve"

    def test_observations_in_markdown(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_observation("f.py", "File lacks CSRF protection")
        md = b.to_markdown()
        assert "Observations" in md
        assert "File lacks CSRF protection" in md


# =============================================================================
# TestAddUnreviewed
# =============================================================================


class TestAddUnreviewed:
    """add_unreviewed declares NOT DIFFED coverage gaps through the builder."""

    def test_stores_and_dedupes_paths(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py")
        b.add_unreviewed("  src/b.py  ")
        b.add_unreviewed("src/a.py")
        assert b.unreviewed == ["src/a.py", "src/b.py"]

    def test_normalizes_equivalent_paths_to_canonical_form(self):
        """"./src/a.py" and "src//a.py" declare the same scope path — the
        stored form must match what the scope sidecars emit."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("./src/a.py")
        b.add_unreviewed("src//a.py")
        assert b.unreviewed == ["src/a.py"]

    @pytest.mark.parametrize("bad", ["", "   ", None, 42, ["src/a.py"]])
    def test_rejects_non_path_values(self, bad):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError):
            b.add_unreviewed(bad)

    def test_normalizes_backslash_separators(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src\\a.py")
        assert b.unreviewed == ["src/a.py"]

    @pytest.mark.parametrize(
        "bad",
        [
            "/abs/a.py", "../outside.py", "..", "C:/win.py", "c:win.py",
            # These normalize to "." — a form no scope summary can contain.
            ".", "./", "foo/..",
        ],
    )
    def test_rejects_non_repo_relative_paths(self, bad):
        """Forms that can never match a canonical scope path must fail
        loudly — an unmatched declaration inverts into a reviewed claim."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError):
            b.add_unreviewed(bad)

    def _arm_deferred_sidecar(self, tmp_path, monkeypatch, deferred):
        """Simulate the bootstrap-written authoritative deferred set."""
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "sec")
        (tmp_path / "sec-deferred-files.json").write_text(
            json.dumps({"schema": 1, "deferred_files": deferred})
        )

    def test_declaration_in_deferred_set_is_accepted(
        self, tmp_path, monkeypatch
    ):
        self._arm_deferred_sidecar(tmp_path, monkeypatch, ["src/deferred.py"])
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("./src/deferred.py")  # normalized before matching
        assert b.unreviewed == ["src/deferred.py"]

    def test_declaration_outside_deferred_set_is_rejected(
        self, tmp_path, monkeypatch
    ):
        """A well-formed but wrong path (typo, wrong root) must fail loudly
        at write time — downstream it would silently count as a reviewed
        claim for every genuinely deferred file."""
        self._arm_deferred_sidecar(tmp_path, monkeypatch, ["src/email.py"])
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="src/email.py"):
            b.add_unreviewed("src/emails.py")

    def test_empty_deferred_set_rejects_every_declaration(
        self, tmp_path, monkeypatch
    ):
        self._arm_deferred_sidecar(tmp_path, monkeypatch, [])
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="no deferred files"):
            b.add_unreviewed("src/a.py")

    def test_missing_sidecar_falls_back_to_form_only(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "sec")
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py")
        assert b.unreviewed == ["src/a.py"]

    def test_malformed_sidecar_falls_back_to_form_only(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "sec")
        (tmp_path / "sec-deferred-files.json").write_text("{not json")
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py")
        assert b.unreviewed == ["src/a.py"]

    def test_unreviewed_in_dict_output(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py")
        assert b.to_dict()["unreviewed"] == ["src/a.py"]

    def test_unreviewed_null_when_empty(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        assert b.to_dict()["unreviewed"] is None

    def test_unreviewed_does_not_affect_verdict(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py")
        assert b._calculate_verdict() == "approve"

    def test_unreviewed_renders_contract_line_in_markdown(self):
        """The Markdown line must match the bootstrap-mandated declaration
        format so the supported API satisfies the NOT DIFFED contract."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py")
        b.add_unreviewed("src/b.py")
        md = b.to_markdown()
        assert "**Not reviewed (budget):** `src/a.py`, `src/b.py`" in md

    def test_markdown_omits_line_when_nothing_declared(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        assert "Not reviewed (budget)" not in b.to_markdown()


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

    def test_raises_if_issues_already_recorded(self):
        """mark_not_applicable rejects mixed state — issues + not_applicable is contradictory."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_issue("high", "XSS", "f.php", "desc", "rec", line=1)
        with pytest.raises(ValueError, match="issue.*already recorded"):
            b.mark_not_applicable("Agent mistakenly started before checking relevance")

    def test_in_json_output(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.mark_not_applicable("No relevant changes")
        j = b.to_json()
        parsed = json.loads(j)
        assert parsed["verdict"] == "not_applicable"
        assert parsed["skip_reason"] == "No relevant changes"

    def test_skip_reason_stripped(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.mark_not_applicable("  No relevant changes  ")
        d = b.to_dict()
        assert d["skip_reason"] == "No relevant changes"

    def test_normal_approve_has_no_skip_reason(self):
        """A normal approve (no issues, no mark_not_applicable) has no skip_reason."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_positive("Clean code")
        d = b.to_dict()
        assert d["verdict"] == "approve"
        assert "skip_reason" not in d


# =============================================================================
# Advisory channel — repo-contributed reviewers
# =============================================================================

class TestAdvisoryChannel:
    """Advisory-channel findings are listed but never gate the verdict."""

    @staticmethod
    def _arm_entitlement(tmp_path, monkeypatch, reviewer, payload):
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", reviewer)
        (tmp_path / f"{reviewer}-advisory-entitlement.json").write_text(
            json.dumps(payload)
        )

    def test_invalid_channel_raises_and_names_value(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")

        with pytest.raises(ValueError, match="Advisory"):
            b.add_issue(severity="high", title="Duplication", file="a.php",
                        description="d", recommendation="r", line=5,
                        channel="Advisory")

    def test_explicit_entitlement_accepts_advisory_without_gating(
        self, tmp_path, monkeypatch
    ):
        self._arm_entitlement(
            tmp_path,
            monkeypatch,
            "repo-reuse",
            {"schema": 1, "advisory_entitled": True},
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_issue(severity="high", title="Duplication", file="a.php",
                    description="d", recommendation="r", line=5, channel="advisory")
        assert b._calculate_verdict() == "approve"

    def test_explicit_false_rejects_advisory(self, tmp_path, monkeypatch):
        self._arm_entitlement(
            tmp_path,
            monkeypatch,
            "repo-reuse",
            {"schema": 1, "advisory_entitled": False},
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")

        with pytest.raises(ValueError, match="advisory.*not entitled"):
            b.add_issue(
                severity="high",
                title="Duplication",
                file="a.php",
                description="d",
                recommendation="r",
                line=5,
                channel="advisory",
            )

    def test_absent_envelope_fails_open_after_vocabulary_validation(
        self, monkeypatch
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")

        b.add_issue(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )

        assert b._calculate_verdict() == "approve"

    def test_absent_sidecar_fails_open_after_vocabulary_validation(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "repo-reuse")
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")

        b.add_issue(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )

        assert b._calculate_verdict() == "approve"

    def test_malformed_sidecar_fails_open_after_vocabulary_validation(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "repo-reuse")
        (tmp_path / "repo-reuse-advisory-entitlement.json").write_text(
            "{not json"
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")

        b.add_issue(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )

        assert b._calculate_verdict() == "approve"

    def test_invalid_utf8_sidecar_fails_open_at_add_time(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "repo-reuse")
        (tmp_path / "repo-reuse-advisory-entitlement.json").write_bytes(b"\xff")
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")

        b.add_issue(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )

        assert b._calculate_verdict() == "approve"

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param([], id="top-level-not-object"),
            pytest.param(
                {"schema": 1, "advisory_entitled": "true"},
                id="entitlement-not-boolean",
            ),
            pytest.param({"schema": 1}, id="entitlement-missing"),
        ],
    )
    def test_wrong_shape_sidecar_fails_open_after_vocabulary_validation(
        self, tmp_path, monkeypatch, payload
    ):
        self._arm_entitlement(tmp_path, monkeypatch, "repo-reuse", payload)
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")

        b.add_issue(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )

        assert b._calculate_verdict() == "approve"

    def test_explicit_output_dir_revalidates_after_add_time_fail_open(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.add_issue(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )
        (tmp_path / "reconciliator-advisory-entitlement.json").write_text(
            json.dumps({"schema": 1, "advisory_entitled": False})
        )

        with pytest.raises(ValueError, match="advisory.*not entitled"):
            b.to_dict(output_dir=str(tmp_path))

    def test_invalid_utf8_sidecar_fails_open_at_explicit_finalization(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.add_issue(
            severity="critical", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )
        (tmp_path / "reconciliator-advisory-entitlement.json").write_bytes(
            b"\xff"
        )

        output = b.to_dict(output_dir=str(tmp_path))

        assert output["verdict"] == "approve"

    def test_to_json_supports_explicit_entitled_output_dir(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.add_issue(
            severity="critical", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )
        (tmp_path / "reconciliator-advisory-entitlement.json").write_text(
            json.dumps({"schema": 1, "advisory_entitled": True})
        )

        output = json.loads(b.to_json(output_dir=str(tmp_path)))

        assert output["verdict"] == "approve"

    def test_save_revalidates_against_its_output_dir(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.add_issue(
            severity="high", title="Duplication", file="a.php",
            description="d", recommendation="r", line=5, channel="advisory",
        )
        (tmp_path / "reconciliator-advisory-entitlement.json").write_text(
            json.dumps({"schema": 1, "advisory_entitled": False})
        )

        with pytest.raises(ValueError, match="advisory.*not entitled"):
            b.save(str(tmp_path))
        assert not (tmp_path / "reconciliator-review.json").exists()

    def test_advisory_critical_does_not_gate(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_issue(severity="critical", title="x", file="a.php",
                    description="d", recommendation="r", line=5, channel="advisory")
        assert b._calculate_verdict() == "approve"

    def test_critical_advisory_records_stricter_counterfactual(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_issue(
            severity="critical", title="x", file="a.php",
            description="d", recommendation="r", line=5,
            channel="advisory",
        )

        output = b.to_dict()

        assert output["verdict"] == "approve"
        assert output["summary"]["advisory_suppressed"] == 1
        assert output["summary"]["verdict_without_advisory"] == "block"
        assert "Advisory suppression:** 1 finding excluded" in render_markdown(output)
        assert "verdict without suppression: BLOCK" in render_markdown(output)

    def test_advisory_count_without_verdict_softening_omits_counterfactual(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_issue(
            severity="low", title="x", file="a.php",
            description="d", recommendation="r", line=5,
            channel="advisory",
        )

        output = b.to_dict()

        assert output["verdict"] == "approve"
        assert output["summary"]["advisory_suppressed"] == 1
        assert "verdict_without_advisory" not in output["summary"]
        assert "Advisory suppression:** 1 finding excluded" in render_markdown(output)

    def test_advisory_count_when_verdict_already_strict_omits_counterfactual(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_issue(
            severity="critical", title="advisory", file="a.php",
            description="d", recommendation="r", line=5,
            channel="advisory",
        )
        b.add_issue(
            severity="critical", title="blocking", file="b.php",
            description="d", recommendation="r", line=6,
        )

        output = b.to_dict()

        assert output["verdict"] == "block"
        assert output["summary"]["advisory_suppressed"] == 1
        assert "verdict_without_advisory" not in output["summary"]

    def test_not_applicable_does_not_claim_advisory_suppression(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.mark_not_applicable("No relevant changes")
        b.add_issue(
            severity="critical", title="advisory", file="a.php",
            description="d", recommendation="r", line=5,
            channel="advisory",
        )

        output = b.to_dict()

        assert output["verdict"] == "not_applicable"
        assert output["summary"]["advisory_suppressed"] == 0
        assert "verdict_without_advisory" not in output["summary"]

    def test_blocking_channel_is_implicit_and_still_gates(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-runtime")
        b.add_issue(severity="critical", title="x", file="a.php",
                    description="d", recommendation="r", line=5, channel="blocking")
        assert "channel" not in b.issues[0]
        assert b._calculate_verdict() == "block"

    def test_no_channel_is_backward_compatible(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        b.add_issue(severity="high", title="x", file="a.php",
                    description="d", recommendation="r", line=5)
        assert b._calculate_verdict() == "request_changes"

    def test_advisory_channel_persisted_in_issue(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_issue(severity="low", title="x", file="a.php",
                    description="d", recommendation="r", line=5, channel="advisory")
        assert b.issues[0]["channel"] == "advisory"

    def test_mixed_channels(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-mix")
        b.add_issue(severity="critical", title="adv", file="a.php",
                    description="d", recommendation="r", line=5, channel="advisory")
        b.add_issue(severity="medium", title="block", file="a.php",
                    description="d", recommendation="r", line=6, channel="blocking")
        # Only the blocking medium counts → comment (not block from the advisory critical).
        assert b._calculate_verdict() == "comment"


# =============================================================================
# TestSaveTimeDeferredValidation
# =============================================================================


class TestSaveTimeDeferredValidation:
    """save() validates declarations against the on-disk sidecar even when
    the PIRATEGOAT_* env envelope is absent (the bypass path 3/19 agents
    took in the 2026-08-14 live-fire run)."""

    def _write_sidecar(self, output_dir, reviewer, files):
        sidecar = Path(output_dir) / f"{reviewer}-deferred-files.json"
        sidecar.write_text(json.dumps({"schema": 1, "deferred_files": files}))

    def test_save_rejects_out_of_set_declaration_without_env(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "go-tests", ["pkg/real_test.go"])
        builder = ReviewOutputBuilder("123", "go-tests")
        # Form-valid but out-of-set — the exact garbage shape that shipped:
        builder.add_unreviewed("pkg/real_test.go (450 lines not diffed)")
        with pytest.raises(ValueError, match="matches no NOT DIFFED file"):
            builder.save(str(tmp_path))
        assert not (tmp_path / "go-tests-review.json").exists()

    def test_save_accepts_in_set_declaration_without_env(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "go-tests", ["pkg/real_test.go"])
        builder = ReviewOutputBuilder("123", "go-tests")
        builder.add_unreviewed("pkg/real_test.go")
        builder.save(str(tmp_path))
        data = json.loads((tmp_path / "go-tests-review.json").read_text())
        assert data["unreviewed"] == ["pkg/real_test.go"]

    def test_save_stays_fail_open_when_sidecar_missing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        builder = ReviewOutputBuilder("123", "go-tests")
        builder.add_unreviewed("anything/form-valid.go")
        builder.save(str(tmp_path))  # no sidecar -> legacy fail-open, no raise
        assert (tmp_path / "go-tests-review.json").exists()

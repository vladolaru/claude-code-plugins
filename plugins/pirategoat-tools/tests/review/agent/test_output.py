"""
Tests for ReviewOutputBuilder — direct unit tests on the producer API.

Validates the structured review output builder that all reviewer agents use
to emit findings. Tests cover initialization, issue addition with validation,
recommendations, verdicts, serialization (dict, JSON, markdown), and file output.

Zero external dependencies beyond stdlib + pytest.
"""

import json
import os
import re
import subprocess
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
            "pr_id", "reviewer", "timestamp", "plugin_version", "schema",
            "verdict",
            "summary", "issues", "unreviewed", "deferred_reviewed",
            "observations", "recommendations", "positive_observations",
            "clearances", "narrative_summary", "meta",
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
        PIRATEGOAT_* variables reach it — but it always serializes with an
        explicit output directory, where step 1's run-config.json already
        records the same stamp.
        """
        monkeypatch.delenv("PIRATEGOAT_PLUGIN_VERSION", raising=False)
        (tmp_path / "run-config.json").write_text(
            json.dumps({"mode": "pr", "plugin_version": "1.114.0"})
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        assert b.to_dict(output_dir=str(tmp_path))["plugin_version"] == "1.114.0"

    def test_envelope_wins_over_run_config(self, monkeypatch, tmp_path):
        """The envelope is the dispatching plugin's own statement."""
        monkeypatch.setenv("PIRATEGOAT_PLUGIN_VERSION", "2.0.0")
        (tmp_path / "run-config.json").write_text(
            json.dumps({"plugin_version": "1.114.0"})
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        assert b.to_dict(output_dir=str(tmp_path))["plugin_version"] == "2.0.0"

    def test_unreadable_run_config_leaves_the_version_unknown(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("PIRATEGOAT_PLUGIN_VERSION", raising=False)
        (tmp_path / "run-config.json").write_text("{not json")
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        assert b.to_dict(output_dir=str(tmp_path))["plugin_version"] is None

    def test_saved_artifact_carries_the_version(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PIRATEGOAT_PLUGIN_VERSION", "1.114.0")
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.set_files_reviewed(1)
        b.save(str(tmp_path))
        saved = json.loads((tmp_path / "pr-review.json").read_text())
        assert saved["plugin_version"] == "1.114.0"

    def test_schema_is_the_documented_shape_number(self):
        """One `schema` convention across every artifact this plugin writes.

        The retired `version: "1.0.0"` string was never bumped through six
        format changes, so it asserted a compatibility guarantee nothing
        maintained. `schema: 1` starts at the shape documented in
        schemas/review-output.ts as of 1.114.0 and is bumped in the same
        commit as any key added, removed, or re-typed.
        """
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        d = b.to_dict()
        assert d["schema"] == 1
        assert isinstance(d["schema"], int)
        assert "version" not in d

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

    def test_pr_id_coerced_to_string(self):
        """Ad-hoc builder scripts hand-roll the value bootstrap would have
        injected as a string; an int serializes as a JSON number and breaks
        the artifact's shape uniformity for every downstream consumer."""
        builder = ReviewOutputBuilder(123, "code")
        assert builder.to_dict()["pr_id"] == "123"


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

    def test_json_content_matches_to_dict(self, monkeypatch):
        with tempfile.TemporaryDirectory() as d:
            # The dispatch marker bootstrap writes — without it there is no
            # honest clock and the duration is null, which would make this
            # comparison pass for the wrong reason.
            monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", d)
            with open(os.path.join(d, "security-reviewer.started"), "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())
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
    """add_unreviewed declares NOT DIFFED coverage gaps through the builder.

    Add-time enforcement only — the authoritative save-time check that does
    not need the env envelope lives in TestSaveTimeDeferredValidation, and
    the sibling claims API is covered by TestAddDeferredReviewed."""

    def test_stores_and_dedupes_paths(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py")
        b.add_unreviewed("  src/b.py  ")
        b.add_unreviewed("src/a.py")
        assert b.unreviewed == ["src/a.py", "src/b.py"]

    def test_declares_every_path_of_a_multi_path_call(self):
        """The signature mirrors its sibling because agents assume it does.

        Two reviewers in one field run called add_unreviewed(*paths) on the
        symmetry with add_deferred_reviewed() and got "takes 2 positional
        arguments but 126 were given"; one then spent a tool call reading
        output.py to discover the asymmetry.
        """
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py", "./src/b.py", "src/a.py")
        assert b.unreviewed == ["src/a.py", "src/b.py"]

    def test_single_path_calls_still_work(self):
        """Every taught example and every existing caller passes one path."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py")
        assert b.unreviewed == ["src/a.py"]

    def test_zero_arguments_raises(self):
        """A declaration of nothing is a silent no-op, not a declaration."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="at least one file path"):
            b.add_unreviewed()

    def test_one_bad_path_rejects_the_whole_batch(self):
        """All-or-nothing, the same as the claims API: a partially recorded
        declaration is a coverage statement the reviewer never made."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError):
            b.add_unreviewed("src/a.py", "/abs/b.py")
        assert b.unreviewed == []

    def test_multi_path_declaration_still_contradicts_a_claim(
        self, tmp_path, monkeypatch
    ):
        """Per-path enforcement does not loosen when the call is variadic:
        save() must still reject declaring and claiming the same file."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        (tmp_path / "sec-deferred-files.json").write_text(
            json.dumps({"schema": 1, "deferred_files": ["a.py", "b.py"]})
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("a.py", "b.py")
        b.add_deferred_reviewed("b.py")
        with pytest.raises(ValueError) as excinfo:
            b.save(str(tmp_path))
        assert "b.py" in str(excinfo.value)

    def test_multi_path_membership_is_checked_per_path(
        self, tmp_path, monkeypatch
    ):
        """A batch is not a bypass — every path is membership-checked, and
        one raise names every offender rather than one per retry."""
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "sec")
        (tmp_path / "sec-deferred-files.json").write_text(
            json.dumps({"schema": 1, "deferred_files": ["a.py"]})
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError) as excinfo:
            b.add_unreviewed("a.py", "typo.py", "other-typo.py")
        message = str(excinfo.value)
        assert "add_unreviewed received 2 declaration(s)" in message
        assert "typo.py" in message and "other-typo.py" in message
        assert b.unreviewed == []

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

    def test_deferred_reviewed_key_always_present(self):
        """The sibling key deliberately does NOT null out when empty: its
        presence is what tells a consumer this output states its claims."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        assert b.to_dict()["deferred_reviewed"] == []

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

    def test_markdown_separates_declared_from_autofilled(self, tmp_path):
        """Budget exhaustion is not why an auto-declared file went
        unreviewed, so the human artifact must not file it under that
        label — the JSON can tell agent honesty from system backfill and
        the Markdown has to as well."""
        (tmp_path / "sec-deferred-files.json").write_text(
            json.dumps(
                {"schema": 1, "deferred_files": ["src/a.py", "src/auto.py"]}
            )
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py")
        b.save(str(tmp_path))  # src/auto.py gets auto-declared
        md = render_markdown(
            json.loads((tmp_path / "sec-review.json").read_text())
        )
        assert "**Not reviewed (budget):** `src/a.py`\n" in md
        assert (
            "**Not reviewed (unaccounted — auto-declared at save):** "
            "`src/auto.py`\n"
        ) in md
        # Membership must not leak across the two lines.
        budget_line = next(
            line for line in md.splitlines()
            if line.startswith("**Not reviewed (budget):**")
        )
        assert "src/auto.py" not in budget_line

    def test_markdown_renders_old_shape_without_autofill_key(self):
        """Outputs produced before save-time auto-fill carry no meta key;
        they must keep rendering exactly as they did."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py")
        data = b.to_dict()
        del data["meta"]["unreviewed_autofilled"]
        md = render_markdown(data)
        assert "**Not reviewed (budget):** `src/a.py`" in md
        assert "auto-declared" not in md

    def test_markdown_ignores_malformed_autofill_marker(self):
        """A non-list marker says nothing usable about membership; the
        paths keep their existing label rather than being split by a
        set of characters."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_unreviewed("src/a.py")
        data = b.to_dict()
        data["meta"]["unreviewed_autofilled"] = "src/a.py"
        md = render_markdown(data)
        assert "**Not reviewed (budget):** `src/a.py`" in md
        assert "auto-declared" not in md


# =============================================================================
# TestAddDeferredReviewed
# =============================================================================


class TestAddDeferredReviewed:
    """add_deferred_reviewed claims NOT DIFFED files as actually reviewed.

    Add-time enforcement only, mirroring TestAddUnreviewed (its declaring
    sibling — both APIs share one path grammar, so the form cases here and
    there must stay in lockstep). The authoritative save-time check lives
    in TestSaveTimeDeferredValidation."""

    def _arm_deferred_sidecar(self, tmp_path, monkeypatch, deferred):
        """Simulate the bootstrap-written authoritative deferred set."""
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("PIRATEGOAT_REVIEWER_NAME", "sec")
        (tmp_path / "sec-deferred-files.json").write_text(
            json.dumps({"schema": 1, "deferred_files": deferred})
        )

    @pytest.mark.parametrize("bad", ["", "   ", None, 42, ["src/a.py"]])
    def test_rejects_non_path_values(self, bad):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError):
            b.add_deferred_reviewed(bad)

    @pytest.mark.parametrize(
        "bad",
        [
            "/abs/a.py", "../outside.py", "..", "C:/win.py", "c:win.py",
            # These normalize to "." — a form no scope summary can contain.
            ".", "./", "foo/..",
        ],
    )
    def test_rejects_non_repo_relative_forms(self, bad):
        """A claim addresses the same namespace as a declaration, so it
        must reject the same unmatchable forms — claiming '/etc/passwd' as
        reviewed is not a near miss, it is a statement about a file this
        review does not contain."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError):
            b.add_deferred_reviewed(bad)

    def test_stores_and_dedupes_claims(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_deferred_reviewed("src/a.py", "./src/a.py", "src/b.py")
        assert b.deferred_reviewed == ["src/a.py", "src/b.py"]

    def test_zero_arguments_raises(self):
        """A claim of nothing is a silent no-op, not a claim."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="at least one file path"):
            b.add_deferred_reviewed()

    def test_claim_in_deferred_set_accepted(self, tmp_path, monkeypatch):
        self._arm_deferred_sidecar(tmp_path, monkeypatch, ["src/deferred.py"])
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_deferred_reviewed("./src/deferred.py")  # normalized first
        assert b.deferred_reviewed == ["src/deferred.py"]

    def test_claim_outside_deferred_set_rejected_at_add(
        self, tmp_path, monkeypatch
    ):
        """A claim on a file this review never deferred is as wrong as a
        declaration on one, and the rejection must say 'claim'."""
        self._arm_deferred_sidecar(tmp_path, monkeypatch, ["src/email.py"])
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="src/email.py"):
            b.add_deferred_reviewed("src/emails.py")
        with pytest.raises(ValueError, match="claim"):
            b.add_deferred_reviewed("src/emails.py")

    def test_empty_deferred_set_rejects_every_claim(
        self, tmp_path, monkeypatch
    ):
        """The empty-set branch must speak the caller's noun — a claimant
        told "nothing may be declared" is being handed the wrong API."""
        self._arm_deferred_sidecar(tmp_path, monkeypatch, [])
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match=r"1 claim\(s\)") as excinfo:
            b.add_deferred_reviewed("src/a.py")
        assert "no claim may be made" in str(excinfo.value)

    def test_all_or_nothing_on_mid_batch_error(self, tmp_path, monkeypatch):
        """A batch either fully lands or nothing does — the same doctrine
        critic_adjustments.py enforces for its own batches. A mid-batch
        rejection must not leave the leading valid paths recorded: a retry
        would then double-record them, and a caller who gives up is left
        with a half-claim no one asked for."""
        self._arm_deferred_sidecar(
            tmp_path, monkeypatch, ["src/a.py", "src/c.py"]
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="src/b.py"):
            b.add_deferred_reviewed("src/a.py", "src/b.py", "src/c.py")
        assert b.deferred_reviewed == []
        # A retry with only the valid paths lands fully.
        b.add_deferred_reviewed("src/a.py", "src/c.py")
        assert b.deferred_reviewed == ["src/a.py", "src/c.py"]

    def test_multi_error_batch_names_every_offender(
        self, tmp_path, monkeypatch
    ):
        """The existing batch-reporting rejection helper already names
        every offender in one raise at save() time; add-time claims must
        get the same treatment instead of stopping at the first bad path."""
        self._arm_deferred_sidecar(tmp_path, monkeypatch, ["src/a.py"])
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError) as excinfo:
            b.add_deferred_reviewed(
                "src/a.py", "src/bogus1.py", "src/bogus2.py"
            )
        message = str(excinfo.value)
        assert "src/bogus1.py" in message
        assert "src/bogus2.py" in message
        assert b.deferred_reviewed == []

    def test_grammar_error_mid_batch_records_nothing(
        self, tmp_path, monkeypatch
    ):
        """The all-or-nothing guarantee covers grammar failures too: a
        malformed path anywhere in the batch leaves zero paths recorded,
        not the leading valid ones."""
        self._arm_deferred_sidecar(tmp_path, monkeypatch, ["src/a.py"])
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError, match="/abs/path.py"):
            b.add_deferred_reviewed("src/a.py", "/abs/path.py")
        assert b.deferred_reviewed == []

    def test_mixed_grammar_and_membership_batch_names_both(
        self, tmp_path, monkeypatch
    ):
        """A batch carrying both error classes reports both in one raise:
        fixing the malformed path must not surface the membership problem
        as a fresh surprise on the retry."""
        self._arm_deferred_sidecar(tmp_path, monkeypatch, ["src/a.py"])
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError) as excinfo:
            b.add_deferred_reviewed("src/typo.py", "/abs/path.py")
        message = str(excinfo.value)
        assert "/abs/path.py" in message
        assert "src/typo.py" in message
        assert b.deferred_reviewed == []

    def test_failed_batch_leaves_no_trace_in_saved_artifact(
        self, tmp_path, monkeypatch
    ):
        """The consequence that matters: after a rejected batch, save()'s
        accounting is exactly as if the call never happened — the
        unclaimed file lands in the auto-fill gap record, never as a
        claim."""
        self._arm_deferred_sidecar(
            tmp_path, monkeypatch, ["src/a.py", "src/c.py"]
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        with pytest.raises(ValueError):
            b.add_deferred_reviewed("src/a.py", "src/bogus.py")
        b.add_deferred_reviewed("src/c.py")
        b.save(str(tmp_path))
        with open(tmp_path / "sec-review.json", encoding="utf-8") as f:
            data = json.load(f)
        assert data["deferred_reviewed"] == ["src/c.py"]
        assert data["meta"]["unreviewed_autofilled"] == ["src/a.py"]

    def test_duplicate_within_batch_dedupes(self, tmp_path, monkeypatch):
        """Pinning current semantics: a batch repeating one path collapses
        it to a single entry, order preserved."""
        self._arm_deferred_sidecar(
            tmp_path, monkeypatch, ["src/a.py", "src/b.py"]
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_deferred_reviewed("src/a.py", "./src/a.py", "src/b.py")
        assert b.deferred_reviewed == ["src/a.py", "src/b.py"]

    def test_already_recorded_across_calls_dedupes(
        self, tmp_path, monkeypatch
    ):
        """Pinning current semantics: claiming a path already recorded by
        a previous call is a silent no-op, not an error or a duplicate
        entry."""
        self._arm_deferred_sidecar(tmp_path, monkeypatch, ["src/a.py"])
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_deferred_reviewed("src/a.py")
        b.add_deferred_reviewed("src/a.py")
        assert b.deferred_reviewed == ["src/a.py"]


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
    took in the 2026-08-14 live-fire run).

    The env-envelope add-time path this backstops is TestAddUnreviewed."""

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
        with pytest.raises(ValueError, match="NOT DIFFED file"):
            builder.save(str(tmp_path))
        assert not (tmp_path / "go-tests-review.json").exists()
        # Validation must precede staging, not just the final replace.
        assert not list(Path(tmp_path).glob("*.tmp"))

    def test_save_reports_every_offender_in_one_raise(
        self, tmp_path, monkeypatch
    ):
        """The motivating run shipped 23 bad declarations; reporting them
        one per save would cost 23 round trips to clean up."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "go-tests", ["pkg/real_test.go"])
        builder = ReviewOutputBuilder("123", "go-tests")
        builder.add_unreviewed("pkg/real_test.go")  # the one legitimate entry
        builder.add_unreviewed("pkg/bogus_one.go")
        builder.add_unreviewed("pkg/bogus_two.go")
        builder.add_unreviewed("pkg/bogus_three.go")
        with pytest.raises(ValueError) as excinfo:
            builder.save(str(tmp_path))
        message = str(excinfo.value)
        assert "add_unreviewed received 3 declaration" in message
        for offender in ("bogus_one", "bogus_two", "bogus_three"):
            assert offender in message
        # The legitimate entry appears only in the valid-paths tail, never
        # among the offenders the message opens with.
        offenders, _, valid_paths = message.partition("Valid paths:")
        assert "pkg/real_test.go" in valid_paths
        assert "pkg/real_test.go" not in offenders

    def test_save_rejects_declaration_when_deferred_set_is_empty(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "go-tests", [])
        builder = ReviewOutputBuilder("123", "go-tests")
        builder.add_unreviewed("pkg/anything.go")
        with pytest.raises(ValueError, match="no deferred files"):
            builder.save(str(tmp_path))
        assert not (tmp_path / "go-tests-review.json").exists()

    def test_save_rejects_paths_appended_around_add_unreviewed(
        self, tmp_path, monkeypatch
    ):
        """Defense in depth is deliberate: the check reads the published
        list, so bypassing the add-time API does not buy a free pass."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "go-tests", ["pkg/real_test.go"])
        builder = ReviewOutputBuilder("123", "go-tests")
        builder.unreviewed.append("bogus.go")
        with pytest.raises(ValueError, match="NOT DIFFED file"):
            builder.save(str(tmp_path))

    def test_save_stays_fail_open_when_sidecar_is_malformed(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        (tmp_path / "go-tests-deferred-files.json").write_text("{not json")
        builder = ReviewOutputBuilder("123", "go-tests")
        builder.add_unreviewed("anything/form-valid.go")
        builder.save(str(tmp_path))
        assert (tmp_path / "go-tests-review.json").exists()

    def test_save_stays_fail_open_when_sidecar_is_invalid_utf8(
        self, tmp_path, monkeypatch
    ):
        """Undecodable bytes are a corrupt sidecar, not a declaration
        offense — the catch must match the advisory loader's."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        (tmp_path / "go-tests-deferred-files.json").write_bytes(b"\xff\xfe\x00")
        builder = ReviewOutputBuilder("123", "go-tests")
        builder.add_unreviewed("anything/form-valid.go")
        builder.save(str(tmp_path))
        assert (tmp_path / "go-tests-review.json").exists()

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

    def test_claims_serialize_and_validate_membership(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "code", ["a.go", "b.go"])
        builder = ReviewOutputBuilder("123", "code")
        builder.add_deferred_reviewed("a.go", "./b.go")  # normalizes ./b.go
        builder.save(str(tmp_path))
        data = json.loads((tmp_path / "code-review.json").read_text())
        assert data["deferred_reviewed"] == ["a.go", "b.go"]

    def test_out_of_set_claim_rejected_at_save(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "code", ["a.go"])
        builder = ReviewOutputBuilder("123", "code")
        builder.add_deferred_reviewed("not-deferred.go")
        with pytest.raises(ValueError, match="add_deferred_reviewed"):
            builder.save(str(tmp_path))
        assert not (tmp_path / "code-review.json").exists()
        assert not list(Path(tmp_path).glob("*.tmp"))

    def test_bad_declarations_and_bad_claims_stay_attributable(
        self, tmp_path, monkeypatch
    ):
        """A declaration offense and a claim offense are different errors.

        Both mean "this path is not in the deferred set", but the fix
        differs — one is a wrongly declared gap, the other a wrongly
        claimed read — so each raise names the API that produced it and
        never blames its sibling.
        """
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "code", ["a.go"])
        builder = ReviewOutputBuilder("123", "code")
        builder.add_unreviewed("bogus-declaration.go")
        builder.add_deferred_reviewed("bogus-claim.go")

        # Declarations are checked first and own the raise alone.
        with pytest.raises(ValueError) as excinfo:
            builder.save(str(tmp_path))
        message = str(excinfo.value)
        assert "add_unreviewed received 1 declaration" in message
        assert "bogus-declaration.go" in message
        assert "add_deferred_reviewed" not in message

        # With the declaration fixed, the claim raises under its own name.
        builder.unreviewed.clear()
        with pytest.raises(ValueError) as excinfo:
            builder.save(str(tmp_path))
        message = str(excinfo.value)
        assert "add_deferred_reviewed" in message
        assert "bogus-claim.go" in message
        # The noun, not just the API name, carries the attribution: an
        # offense reported as a "declaration" under the claims API name
        # sends the reader to the wrong fix.
        assert "claim(s)" in message
        assert "add_unreviewed" not in message
        assert not (tmp_path / "code-review.json").exists()

    def test_unaccounted_deferred_files_autofill_as_unreviewed(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "docs-drift", ["a.md", "b.md", "c.md"])
        builder = ReviewOutputBuilder("123", "docs-drift")
        builder.add_deferred_reviewed("a.md")
        builder.add_unreviewed("b.md")
        builder.save(str(tmp_path))
        data = json.loads((tmp_path / "docs-drift-review.json").read_text())
        assert sorted(data["unreviewed"]) == ["b.md", "c.md"]
        assert data["meta"]["unreviewed_autofilled"] == ["c.md"]
        out = capsys.readouterr().out
        assert "UNREVIEWED: 1 declared (+1 auto-filled) / 3 deferred" in out
        assert "CLAIMED REVIEWED: 1" in out
        assert "WARNING" in out

    def test_fully_accounted_save_has_no_autofill(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "code", ["a.go"])
        builder = ReviewOutputBuilder("123", "code")
        builder.add_deferred_reviewed("a.go")
        builder.save(str(tmp_path))
        data = json.loads((tmp_path / "code-review.json").read_text())
        assert data["unreviewed"] is None
        assert data["meta"]["unreviewed_autofilled"] is None
        assert "WARNING" not in capsys.readouterr().out

    def test_double_save_autofill_is_idempotent(self, tmp_path, monkeypatch):
        """An unchanged re-save must not duplicate the fill or the marker.

        This holds under both sticky and derived semantics, so it is a
        no-duplication guard only — the redesign to derived state is pinned
        by test_claim_after_warning_clears_autofill_on_resave.
        """
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "code", ["a.go", "b.go"])
        builder = ReviewOutputBuilder("123", "code")
        builder.add_deferred_reviewed("a.go")
        builder.save(str(tmp_path))
        builder.save(str(tmp_path))
        data = json.loads((tmp_path / "code-review.json").read_text())
        assert data["unreviewed"] == ["b.go"]
        assert data["meta"]["unreviewed_autofilled"] == ["b.go"]

    def test_claim_after_warning_clears_autofill_on_resave(
        self, tmp_path, monkeypatch, capsys
    ):
        """The remediation the WARNING teaches must actually work — and
        must not trip the declare-plus-claim contradiction guard, which is
        why the previous fill is stripped BEFORE validation runs."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "code", ["a.go", "b.go"])
        builder = ReviewOutputBuilder("123", "code")
        builder.add_deferred_reviewed("a.go")
        builder.save(str(tmp_path))          # b.go auto-filled, WARNING
        builder.add_deferred_reviewed("b.go")
        builder.save(str(tmp_path))          # the WARNING's own remediation
        data = json.loads((tmp_path / "code-review.json").read_text())
        assert data["unreviewed"] is None
        assert data["meta"]["unreviewed_autofilled"] is None
        assert sorted(data["deferred_reviewed"]) == ["a.go", "b.go"]
        out = capsys.readouterr().out
        assert out.count("WARNING") == 1  # first save only

    def test_explicit_declaration_promotes_out_of_autofill(
        self, tmp_path, monkeypatch, capsys
    ):
        """Declaring a path the system had auto-filled is the agent taking
        ownership of the gap — the second legal answer to the WARNING. It
        must stop counting as backfill, or the marker lies about who
        accounted for the file in exactly the case it exists to measure."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "code", ["a.go", "b.go"])
        builder = ReviewOutputBuilder("123", "code")
        builder.add_deferred_reviewed("a.go")
        builder.save(str(tmp_path))          # b.go auto-filled, WARNING
        builder.add_unreviewed("b.go")       # the agent owns the gap
        builder.save(str(tmp_path))
        data = json.loads((tmp_path / "code-review.json").read_text())
        assert data["unreviewed"] == ["b.go"]
        assert data["meta"]["unreviewed_autofilled"] is None
        out = capsys.readouterr().out
        assert out.count("WARNING") == 1  # first save only
        assert "UNREVIEWED: 1 declared / 2 deferred" in out

    def test_declared_and_claimed_path_rejected_at_save(
        self, tmp_path, monkeypatch
    ):
        """Declaring a file unreviewed and claiming it reviewed are
        contradictory statements about the same file. Serializing both
        would put the path in two arrays and inflate the accounting to
        three statements about two files."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "code", ["a.go", "b.go"])
        builder = ReviewOutputBuilder("123", "code")
        builder.add_unreviewed("b.go")
        builder.add_deferred_reviewed("b.go")
        with pytest.raises(ValueError) as excinfo:
            builder.save(str(tmp_path))
        message = str(excinfo.value)
        assert "1 path(s) are both declared unreviewed" in message
        assert "b.go" in message
        assert not (tmp_path / "code-review.json").exists()
        assert not list(Path(tmp_path).glob("*.tmp"))

    def test_declared_and_claimed_path_rejected_without_a_sidecar(
        self, tmp_path, monkeypatch
    ):
        """Fail-open is about MEMBERSHIP, not self-consistency.

        Without a sidecar nobody can say whether a path is a deferred file
        of this review — so declarations and claims go unchecked. But
        "declared unreviewed" and "claimed reviewed" contradict each other
        no matter what the deferred set is, and the builder holds both
        lists. Letting the contradiction publish here also hides itself:
        the accounting echo omits the claimed count when no sidecar is
        readable, so the doubled path is visible in neither the receipt nor
        any warning.
        """
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        builder = ReviewOutputBuilder("123", "code")  # no sidecar written
        builder.add_unreviewed("b.go")
        builder.add_deferred_reviewed("b.go")
        with pytest.raises(ValueError) as excinfo:
            builder.save(str(tmp_path))
        assert "1 path(s) are both declared unreviewed" in str(excinfo.value)
        assert not (tmp_path / "code-review.json").exists()
        assert not list(Path(tmp_path).glob("*.tmp"))

    def test_sidecar_replacement_between_saves_rederives_cleanly(
        self, tmp_path, monkeypatch
    ):
        """A replaced sidecar must not make the system blame the agent.

        The previous save's auto-fill is derived from the old deferred set.
        If the new derivation ran before that stale fill was stripped, the
        agent would be told its own add_unreviewed() call "matches no NOT
        DIFFED file" for a path it never declared — an error about the
        system's bookkeeping, addressed to the wrong party and unfixable by
        the party receiving it.
        """
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "code", ["a.go", "b.go"])
        builder = ReviewOutputBuilder("123", "code")
        builder.add_deferred_reviewed("a.go")
        builder.save(str(tmp_path))          # b.go auto-filled
        self._write_sidecar(tmp_path, "code", ["a.go", "c.go"])
        builder.save(str(tmp_path))          # must NOT blame the agent for b.go
        data = json.loads((tmp_path / "code-review.json").read_text())
        assert data["unreviewed"] == ["c.go"]
        assert data["meta"]["unreviewed_autofilled"] == ["c.go"]

    def test_missing_sidecar_autofills_nothing_and_omits_deferred_tail(
        self, tmp_path, monkeypatch, capsys
    ):
        """Fail-open means no authoritative set, so there is nothing to
        auto-fill against and no deferred total to echo — the accounting
        line must degrade to what is actually known, not to a fabricated
        zero-deferred claim."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        builder = ReviewOutputBuilder("123", "code")
        builder.add_unreviewed("some/file.go")
        builder.save(str(tmp_path))  # no sidecar
        data = json.loads((tmp_path / "code-review.json").read_text())
        assert data["unreviewed"] == ["some/file.go"]
        assert data["meta"]["unreviewed_autofilled"] is None
        out = capsys.readouterr().out
        unreviewed_line = next(
            line for line in out.splitlines()
            if line.startswith("UNREVIEWED:")
        )
        assert unreviewed_line == "UNREVIEWED: 1 declared"
        assert "deferred" not in unreviewed_line
        assert "WARNING" not in out

    def test_sidecar_with_no_deferred_files_echoes_zeros(
        self, tmp_path, monkeypatch, capsys
    ):
        """A review that deferred nothing still reports its accounting —
        the line is the agent's receipt, not an exception path."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        self._write_sidecar(tmp_path, "code", [])
        builder = ReviewOutputBuilder("123", "code")
        builder.save(str(tmp_path))
        out = capsys.readouterr().out
        assert (
            "UNREVIEWED: 0 declared / 0 deferred | CLAIMED REVIEWED: 0" in out
        )
        assert "WARNING" not in out


# =============================================================================
# TestBudgetTargetEcho
# =============================================================================


class TestBudgetTargetEcho:
    """The call-budget target is surfaced where the reviewer can still act.

    The briefing has always stated the target, thousands of tokens before
    the moment a reviewer decides to stop, and a 19-agent field run showed
    that placement moves nothing. The echo is the one feedback surface every
    agent reads, so the target is repeated there — but only when unreviewed
    files make it actionable, and only when the run actually set one.
    """

    @staticmethod
    def _clean_env(monkeypatch):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEW_BUDGET", raising=False)

    def _save_with_unreviewed(self, tmp_path, monkeypatch, capsys):
        builder = ReviewOutputBuilder("123", "code")
        builder.add_unreviewed("some/file.go")
        builder.save(str(tmp_path))
        return capsys.readouterr().out

    def test_target_line_appears_with_unreviewed_and_budget(
        self, tmp_path, monkeypatch, capsys
    ):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("PIRATEGOAT_REVIEW_BUDGET", "80")
        out = self._save_with_unreviewed(tmp_path, monkeypatch, capsys)
        assert "TARGET: ~80 tool calls" in out
        assert "read more and re-save before finalizing" in out
        # Exactly one line, so the echo stays scannable.
        assert sum(l.startswith("TARGET:") for l in out.splitlines()) == 1

    def test_no_target_line_without_unreviewed_files(
        self, tmp_path, monkeypatch, capsys
    ):
        """Nothing left unread means nothing to act on — silence is right."""
        self._clean_env(monkeypatch)
        monkeypatch.setenv("PIRATEGOAT_REVIEW_BUDGET", "80")
        builder = ReviewOutputBuilder("123", "code")
        builder.save(str(tmp_path))
        assert "TARGET:" not in capsys.readouterr().out

    def test_no_target_line_without_the_envelope(
        self, tmp_path, monkeypatch, capsys
    ):
        """The builder must stay usable outside a pipeline run."""
        self._clean_env(monkeypatch)
        out = self._save_with_unreviewed(tmp_path, monkeypatch, capsys)
        assert "TARGET:" not in out

    @pytest.mark.parametrize("raw", ["", "   ", "abc", "0", "-5", "12.5"])
    def test_malformed_budget_is_treated_as_absent(
        self, tmp_path, monkeypatch, capsys, raw
    ):
        """A target of "0" or "abc" is worse than no target — never repair it."""
        self._clean_env(monkeypatch)
        monkeypatch.setenv("PIRATEGOAT_REVIEW_BUDGET", raw)
        out = self._save_with_unreviewed(tmp_path, monkeypatch, capsys)
        assert "TARGET:" not in out

    def test_autofilled_only_still_gets_the_target(
        self, tmp_path, monkeypatch, capsys
    ):
        """Auto-filled gaps are exactly the case the nudge exists for: the
        agent declared nothing, so only the echo can tell it there is
        unread work left."""
        self._clean_env(monkeypatch)
        monkeypatch.setenv("PIRATEGOAT_REVIEW_BUDGET", "40")
        sidecar = tmp_path / "code-deferred-files.json"
        sidecar.write_text(json.dumps({"deferred_files": ["a.go"]}))
        builder = ReviewOutputBuilder("123", "code")
        builder.save(str(tmp_path))
        out = capsys.readouterr().out
        assert "TARGET: ~40 tool calls" in out


# =============================================================================
# TestMetaIsNeverFakeZero
# =============================================================================


class TestMetaIsNeverFakeZero:
    """meta must report facts or absence — never a default dressed as one.

    A field run's review-findings.json carried files_reviewed: 0 and
    review_duration_ms: 0 for an actor that ran 211 seconds. Both numbers
    were builder defaults, indistinguishable downstream from measurements.
    """

    @staticmethod
    def _stamp(path, moment=None):
        path.write_text((moment or datetime.now(timezone.utc)).isoformat())

    def test_files_reviewed_is_null_until_reported(self, monkeypatch):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        meta = ReviewOutputBuilder(pr_id="1", reviewer="security").to_dict()["meta"]
        assert meta["files_reviewed"] is None

    def test_explicit_zero_is_distinguishable_from_unset(
        self, tmp_path, monkeypatch
    ):
        """The whole point: 0 means "I read nothing", null means silence."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        silent = ReviewOutputBuilder(pr_id="1", reviewer="security")
        stated = ReviewOutputBuilder(pr_id="1", reviewer="security")
        stated.set_files_reviewed(0)
        silent.save(str(tmp_path))
        silent_json = json.loads(
            (tmp_path / "security-review.json").read_text()
        )
        stated.save(str(tmp_path))
        stated_json = json.loads(
            (tmp_path / "security-review.json").read_text()
        )
        assert silent_json["meta"]["files_reviewed"] is None
        assert stated_json["meta"]["files_reviewed"] == 0

    @pytest.mark.parametrize("bad", [None, "3", 2.0, True, -1])
    def test_set_files_reviewed_rejects_non_counts(self, bad):
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        with pytest.raises(ValueError):
            b.set_files_reviewed(bad)
        assert b.to_dict()["meta"]["files_reviewed"] is None

    def test_duration_is_null_without_a_marker(self, tmp_path, monkeypatch):
        """No marker, no clock. The builder is constructed inside the final
        heredoc, so its own __init__ times the write, not the review."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        assert b.to_dict(output_dir=str(tmp_path))["meta"][
            "review_duration_ms"
        ] is None

    def test_duration_comes_from_a_reviewer_marker(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        self._stamp(
            tmp_path / "security-reviewer.started",
            datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        duration = b.to_dict(output_dir=str(tmp_path))["meta"][
            "review_duration_ms"
        ]
        assert 29_000 <= duration <= 40_000

    def test_duration_comes_from_the_reconciliators_marker(
        self, tmp_path, monkeypatch
    ):
        """The synthesis marker is deliberately NOT named `.started`, and
        the reconciliator's builder name is not its agent name — the field
        artifact's 0ms duration was that pair of mismatches, not a missing
        marker."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        self._stamp(
            tmp_path / "review-reconciliator.synthesis-started",
            datetime.now(timezone.utc) - timedelta(seconds=211),
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        duration = b.to_dict(output_dir=str(tmp_path))["meta"][
            "review_duration_ms"
        ]
        assert 211_000 <= duration <= 225_000

    def test_marker_is_found_through_the_env_envelope(
        self, tmp_path, monkeypatch
    ):
        """to_dict() with no explicit directory is the reviewer's own path
        into the manifest; the envelope is what tells it where to look."""
        monkeypatch.setenv("PIRATEGOAT_OUTPUT_DIR", str(tmp_path))
        self._stamp(tmp_path / "security-reviewer.started")
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        assert isinstance(b.to_dict()["meta"]["review_duration_ms"], int)

    @pytest.mark.parametrize("stamp", ["", "not-a-timestamp", "   "])
    def test_unparsable_marker_yields_null_not_zero(
        self, tmp_path, monkeypatch, stamp
    ):
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        (tmp_path / "security-reviewer.started").write_text(stamp)
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        assert b.to_dict(output_dir=str(tmp_path))["meta"][
            "review_duration_ms"
        ] is None

    def test_marker_stamped_in_the_future_yields_null(
        self, tmp_path, monkeypatch
    ):
        """A negative interval is impossible under any real ordering; a
        wrong number is worse than a missing one."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        self._stamp(
            tmp_path / "security-reviewer.started",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        b = ReviewOutputBuilder(pr_id="1", reviewer="security")
        assert b.to_dict(output_dir=str(tmp_path))["meta"][
            "review_duration_ms"
        ] is None

    def test_marker_suffixes_match_their_writers(self):
        """The suffixes are spelled in output.py so the module stays
        importable stand-alone. Parity with the writers is what keeps that
        copy from silently unmeasuring an entire class of actor."""
        import review.synthesis_lifecycle as _lifecycle
        import review.agent.output as _output

        bootstrap_src = (
            PLUGIN_ROOT / "scripts" / "review" / "agent" / "bootstrap.py"
        ).read_text()
        assert _output._SYNTHESIS_START_SUFFIX == _lifecycle.MARKER_SUFFIX
        assert (
            f'f"{{effective_agent_name}}{_output._REVIEWER_START_SUFFIX}"'
            in bootstrap_src
        )

    def test_reconciliator_alias_matches_both_ends_it_bridges(self):
        """The one hand-copied name pair in the marker lookup, pinned.

        _MARKER_AGENT_BY_REVIEWER exists because the reconciliator is
        dispatched as `review-reconciliator` but constructs its builder as
        `reconciliator`. Both ends live in other files, and a rename at
        either one would send the reconciliator's duration back to null
        with the whole suite still green — the exact failure this map was
        added to fix.
        """
        import review.synthesis_lifecycle as _lifecycle
        import review.agent.output as _output

        alias = _output._MARKER_AGENT_BY_REVIEWER
        # Marker end: the name synthesis_lifecycle stamps the marker with.
        assert alias["reconciliator"] == _lifecycle.RECONCILIATOR
        # Builder end: the reviewer name the reconciliator is taught to
        # construct itself with.
        agent_md = (
            PLUGIN_ROOT / "agents" / "review-reconciliator.md"
        ).read_text()
        assert 'reviewer="reconciliator"' in agent_md, (
            "the taught builder name moved — update the alias key in "
            "output.py's _MARKER_AGENT_BY_REVIEWER with it"
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
    def _review_output_interface() -> str:
        schema = (PLUGIN_ROOT / "schemas" / "review-output.ts").read_text()
        match = re.search(
            r"export interface ReviewOutput\s*\{(.*?)\n\}", schema, re.DOTALL
        )
        assert match is not None, "review-output.ts must declare ReviewOutput"
        return match.group(1)

    def test_identity_block_matches_the_serialized_artifact(self):
        interface = self._review_output_interface()
        declared = set(re.findall(r"^\s*(\w+)\??:", interface, re.MULTILINE))
        serialized = set(ReviewOutputBuilder(pr_id="1", reviewer="pr").to_dict())

        identity = {"pr_id", "reviewer", "timestamp", "plugin_version", "schema"}
        assert identity <= declared
        assert identity <= serialized

    def test_retired_version_field_is_gone_from_both_sides(self):
        interface = self._review_output_interface()
        assert not re.search(r"^\s*version\??:", interface, re.MULTILINE)
        assert "version" not in ReviewOutputBuilder(pr_id="1", reviewer="pr").to_dict()

    def test_schema_is_declared_as_a_number(self):
        interface = self._review_output_interface()
        match = re.search(r"^\s*schema:\s*([^;]+);", interface, re.MULTILINE)
        assert match is not None
        assert match.group(1).strip() == "number"

    def test_plugin_version_is_declared_nullable(self):
        """Absence is part of the contract, not an error state."""
        interface = self._review_output_interface()
        match = re.search(
            r"^\s*plugin_version:\s*([^;]+);", interface, re.MULTILINE
        )
        assert match is not None
        assert match.group(1).strip() == "string | null"


# =============================================================================
# TestNarrativeSummary
# =============================================================================


class TestNarrativeSummary:
    """The reconciliator's overall-state prose needs a structured home.

    Before the .md became a script render, that prose lived only in a
    hand-written narrative file. Migrating it into the canonical JSON is
    what lets the renderer own the artifact without losing content.
    """

    def test_absent_by_default_but_the_key_is_always_present(self):
        d = ReviewOutputBuilder(pr_id="1", reviewer="pr").to_dict()
        assert d["narrative_summary"] is None

    def test_set_narrative_summary_serializes(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.set_narrative_summary("The change is sound but under-tested.")
        assert b.to_dict()["narrative_summary"] == (
            "The change is sound but under-tested."
        )

    def test_non_string_prose_is_coerced_like_every_other_free_field(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.set_narrative_summary(["line one", "line two"])
        assert b.to_dict()["narrative_summary"] == "line one\nline two"

    def test_blank_prose_records_absence_not_an_empty_string(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.set_narrative_summary("   ")
        assert b.to_dict()["narrative_summary"] is None

    def test_renders_as_an_assessment_section(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.set_narrative_summary("Two sentences of judgment.")
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


class TestReconciliationSectionsRender:
    """Every section the reconciliator's old narrative template carried has
    to come out of the renderer, or migrating to a script render loses it."""

    @staticmethod
    def _findings(**extra):
        b = ReviewOutputBuilder(pr_id="9", reviewer="reconciliator")
        b.add_issue("high", "Real problem", "a.py", "d", "r", line=4)
        data = b.to_dict()
        data.update(extra)
        return data

    def test_pipeline_metrics_line_renders_from_meta_reconciliation(self):
        data = self._findings()
        data["meta"]["reconciliation"] = {
            "input_findings_count": 12,
            "agents_contributing": 4,
            "concerns_after_grouping": 5,
            "false_positives_dropped": 3,
            "out_of_scope_dropped": 1,
            "verified_concerns": 4,
            "merge_ratio": 0.58,
            "not_applicable_count": 0,
            "not_applicable_agents": [],
            "reviewing_agents": ["code-reviewer"],
            "dispatched_agents": ["code-reviewer"],
            "missing_agents": [],
        }
        rendered = render_markdown(data)
        assert "**Pipeline:** 12 findings from 4 reviewing agents" in rendered
        assert "5 concerns after grouping" in rendered
        assert "3 false positives dropped" in rendered
        assert "1 out-of-scope dropped" in rendered

    def test_pipeline_line_points_at_the_full_metrics_block(self):
        """The narrative template ended its Pipeline line with a pointer to
        the metrics block. Dropping it in the substitution would lose the
        one hint a reader has that more accounting exists."""
        data = self._findings()
        data["meta"]["reconciliation"] = {
            "input_findings_count": 12,
            "agents_contributing": 4,
            "concerns_after_grouping": 5,
            "false_positives_dropped": 3,
            "out_of_scope_dropped": 1,
            "verified_concerns": 4,
            "merge_ratio": 0.58,
            "not_applicable_count": 0,
            "not_applicable_agents": [],
            "reviewing_agents": [],
            "dispatched_agents": [],
            "missing_agents": [],
        }
        rendered = render_markdown(data)
        assert (
            "Full metrics in `review-findings.json` \u2192 "
            "`meta.reconciliation`." in rendered
        )

    def test_not_applicable_agents_are_reported_with_reasons(self):
        data = self._findings()
        data["meta"]["reconciliation"] = {
            "input_findings_count": 1,
            "agents_contributing": 1,
            "concerns_after_grouping": 1,
            "false_positives_dropped": 0,
            "out_of_scope_dropped": 0,
            "verified_concerns": 1,
            "merge_ratio": 0.0,
            "not_applicable_count": 1,
            "not_applicable_agents": [
                {"name": "a11y-reviewer", "skip_reason": "no UI changed"},
            ],
            "reviewing_agents": ["code-reviewer"],
            "dispatched_agents": ["code-reviewer", "a11y-reviewer"],
            "missing_agents": [],
        }
        rendered = render_markdown(data)
        assert "1 agent returned not-applicable" in rendered
        assert "a11y-reviewer (no UI changed)" in rendered

    def test_missing_reconciliation_metrics_render_nothing(self):
        assert "**Pipeline:**" not in render_markdown(self._findings())

    def test_recommendations_render_by_priority(self):
        b = ReviewOutputBuilder(pr_id="9", reviewer="reconciliator")
        b.add_recommendation("immediate", "Fix the escaping")
        b.add_recommendation("important", "Add a regression test")
        b.add_recommendation("suggestions", "Rename the helper")
        rendered = render_markdown(b.to_dict())
        assert "## Recommendations\n" in rendered
        assert "**Immediate:**" in rendered
        assert "- Fix the escaping" in rendered
        assert "**Important:**" in rendered
        assert "- Add a regression test" in rendered
        assert "**Suggestions:**" in rendered
        assert "- Rename the helper" in rendered

    def test_no_recommendations_renders_no_section(self):
        assert "## Recommendations" not in render_markdown(self._findings())

    def test_degraded_host_context_banner_leads_the_body(self):
        """Directly under the title — the H1 stays first so one grader rule
        covers every rendering (see TestRendererFaithfulness)."""
        data = self._findings(host_context_banner={
            "degraded": True,
            "reason": "partial_unresolved",
            "message": "WooCommerce source was not resolved.",
            "unresolved": [{"name": "woocommerce", "reason": "not found"}],
        })
        rendered = render_markdown(data)
        assert rendered.startswith("# Reconciliator Review - PR #9\n\n")
        assert (
            "> **⚠ Host Context Banner:** "
            "WooCommerce source was not resolved.\n\n"
            "## Executive Summary"
        ) in rendered

    def test_undegraded_banner_is_not_rendered(self):
        data = self._findings(host_context_banner={
            "degraded": False, "reason": "", "message": "all resolved",
            "unresolved": [],
        })
        assert not render_markdown(data).startswith(">")

    def test_tradeoffs_ride_the_existing_observation_channel(self):
        """The narrative's "Tradeoffs Identified" section has a structured
        home already: verified, maintainer-intended compromises are
        observations; unverified ones are findings."""
        b = ReviewOutputBuilder(pr_id="9", reviewer="reconciliator")
        b.add_observation(
            "cart.php",
            "Trigger: bulk import. Population: verified at cart.php:88. "
            "Intentional: throughput over per-row validation.",
            category="tradeoff",
        )
        rendered = render_markdown(b.to_dict())
        assert "## Observations" in rendered
        assert "Trigger: bulk import." in rendered


# =============================================================================
# TestMaterializeFindingsMarkdown
# =============================================================================


class TestMaterializeFindingsMarkdown:
    """One materializer, parameterized — never a second render path."""

    def test_suffix_selects_the_findings_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
            b.add_issue("high", "T", "f.py", "d", "r", line=1)
            b.set_narrative_summary("Overall: needs work.")
            data = b.to_dict()
            Path(d, "review-findings.json").write_text(json.dumps(data))
            written = materialize_markdown(d, suffix="review-findings.json")
            assert [os.path.basename(p) for p in written] == [
                "review-findings.md",
            ]
            assert Path(d, "review-findings.md").read_text() == render_markdown(
                data
            )

    def test_default_suffix_ignores_the_findings_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            ReviewOutputBuilder(pr_id="1", reviewer="security").save(d)
            Path(d, "review-findings.json").write_text(
                json.dumps(
                    ReviewOutputBuilder(
                        pr_id="1", reviewer="reconciliator"
                    ).to_dict()
                )
            )
            written = materialize_markdown(d)
            assert [os.path.basename(p) for p in written] == [
                "security-review.md",
            ]
            assert not Path(d, "review-findings.md").exists()

    def test_missing_findings_json_writes_nothing_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            assert materialize_markdown(d, suffix="review-findings.json") == []

    def test_materialize_cli_accepts_the_suffix(self):
        """The on-demand recovery path step 11 prints has to be able to
        render the findings ledger, not only the per-reviewer family."""
        output_py = (
            Path(__file__).parents[3] / "scripts" / "review" / "agent"
            / "output.py"
        )
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
            b.add_issue("high", "T", "f.py", "d", "r", line=1)
            Path(d, "review-findings.json").write_text(json.dumps(b.to_dict()))
            result = subprocess.run(
                [sys.executable, str(output_py), "materialize", d,
                 "--suffix", "review-findings.json"],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert "review-findings.md" in result.stdout
            assert Path(d, "review-findings.md").is_file()

    def test_materialize_cli_default_suffix_is_unchanged(self):
        output_py = (
            Path(__file__).parents[3] / "scripts" / "review" / "agent"
            / "output.py"
        )
        with tempfile.TemporaryDirectory() as d:
            ReviewOutputBuilder(pr_id="1", reviewer="security").save(d)
            Path(d, "review-findings.json").write_text(
                json.dumps(
                    ReviewOutputBuilder(
                        pr_id="1", reviewer="reconciliator"
                    ).to_dict()
                )
            )
            result = subprocess.run(
                [sys.executable, str(output_py), "materialize", d],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert "security-review.md" in result.stdout
            assert not Path(d, "review-findings.md").exists()


# =============================================================================
# TestAssessmentProvenance
# =============================================================================


class TestAssessmentProvenance:
    """`## Assessment` is prose about a ledger that keeps changing.

    The reconciler writes it; the decision critic then mutates the findings
    it summarizes. The critic's vocabulary reaches every issue but no
    ledger-level prose, so a withdrawn or demoted finding described in the
    Assessment survives every correction channel — the rendered file
    contradicting the list printed beneath it.
    """

    @staticmethod
    def _findings(**extra):
        b = ReviewOutputBuilder(pr_id="9", reviewer="reconciliator")
        b.add_issue("low", "Minor problem", "a.py", "d", "r", line=4)
        data = b.to_dict()
        data.update(extra)
        return data

    def test_prose_carries_a_provenance_marker(self):
        data = self._findings(narrative_summary="All clear on the whole.")
        rendered = render_markdown(data)
        assert "## Assessment\n\nAll clear on the whole." in rendered
        assert "reconciler-authored" in rendered.lower()
        assert "not adjusted by the decision critic" in rendered.lower()

    def test_withdrawn_summary_renders_the_withdrawal_notice(self):
        data = self._findings(
            narrative_summary=None,
            applied_critic_adjustments=["a1b2c3"],
            withdrawn_narrative_summary=[
                {"text": "Old claim.", "withdrawn_by": ["a1b2c3"]}
            ],
        )
        rendered = render_markdown(data)
        assert "## Assessment" in rendered
        assert "withdrawn" in rendered.lower()
        assert "critic adjustments applied" in rendered.lower()
        assert "see the report" in rendered.lower()

    def test_applied_batch_without_a_withdrawal_claims_no_retraction(self):
        """A reconciler that never wrote a summary has nothing to retract:
        the writer side refuses to fabricate an empty withdrawal entry, and
        the renderer must not assert one either. The withdrawal record —
        not the applied-ids list — is the signal."""
        data = self._findings(
            narrative_summary=None,
            applied_critic_adjustments=["a1b2c3"],
        )
        assert "## Assessment" not in render_markdown(data)

    def test_no_summary_and_no_adjustments_renders_no_assessment(self):
        assert "## Assessment" not in render_markdown(self._findings())

    def test_an_empty_adjustment_list_is_not_a_withdrawal(self):
        """A ledger the critic reached but never changed said nothing about
        the assessment — the reconciler simply wrote none."""
        data = self._findings(
            narrative_summary=None, applied_critic_adjustments=[],
        )
        assert "## Assessment" not in render_markdown(data)

    def test_surviving_prose_beside_adjustments_still_renders_as_prose(self):
        """Defensive: an older ledger patched before the invalidation
        existed keeps its prose rather than being retroactively hidden."""
        data = self._findings(
            narrative_summary="Legacy prose.",
            applied_critic_adjustments=["a1b2c3"],
        )
        rendered = render_markdown(data)
        assert "Legacy prose." in rendered
        assert "withdrawn" not in rendered.lower()


# =============================================================================
# TestRemovedByCriticSection
# =============================================================================


class TestRemovedByCriticSection:
    """The ledger deliberately keeps what the critic took out. A reading
    copy that silently drops it hides the audit trail the JSON preserved."""

    @staticmethod
    def _with_removed(removed):
        b = ReviewOutputBuilder(pr_id="9", reviewer="reconciliator")
        b.add_issue("low", "Kept", "a.py", "d", "r", line=4)
        data = b.to_dict()
        data["removed_by_critic"] = removed
        return data

    def test_removed_findings_render_with_their_rationale(self):
        rendered = render_markdown(self._with_removed([{
            "id": "dead1234", "severity": "high", "title": "Phantom leak",
            "file": "b.py", "line": 12, "description": "d",
            "recommendation": "r",
            "critic_adjustment": {
                "action": "remove",
                "rationale": "The guard on line 9 already prevents it.",
            },
        }]))
        assert "## Removed by the Decision Critic" in rendered
        assert "Phantom leak" in rendered
        assert "The guard on line 9 already prevents it." in rendered
        assert "`b.py`" in rendered

    def test_a_removal_without_rationale_still_lists_the_finding(self):
        rendered = render_markdown(self._with_removed([{
            "id": "dead1234", "severity": "high", "title": "Phantom leak",
            "file": "b.py", "line": 12, "description": "d",
            "recommendation": "r",
        }]))
        assert "Phantom leak" in rendered
        assert "no rationale recorded" in rendered

    def test_no_removals_renders_no_section(self):
        assert "Removed by the Decision Critic" not in render_markdown(
            self._with_removed([])
        )

    def test_removed_findings_do_not_leak_into_the_severity_sections(self):
        rendered = render_markdown(self._with_removed([{
            "id": "dead1234", "severity": "high", "title": "Phantom leak",
            "file": "b.py", "line": 12, "description": "d",
            "recommendation": "r",
        }]))
        assert "## High Issues" not in rendered


# =============================================================================
# TestRendererFaithfulness
# =============================================================================


class TestRendererFaithfulness:
    """Minors that all share one failure mode: the renderer showing a
    heading whose content it dropped, or dropping content outright."""

    @staticmethod
    def _base():
        b = ReviewOutputBuilder(pr_id="9", reviewer="reconciliator")
        b.add_issue("low", "T", "a.py", "d", "r", line=4)
        return b.to_dict()

    def test_multiline_banner_quotes_every_line(self):
        """The banner is hand-copied by an agent; an LLM reformat that adds
        a newline would drop the rest of the message out of the blockquote."""
        data = self._base()
        data["host_context_banner"] = {
            "degraded": True, "reason": "partial_unresolved",
            "message": "WooCommerce was not resolved.\nReviewer claims are "
                       "scoped accordingly.",
            "unresolved": [],
        }
        rendered = render_markdown(data)
        assert "> **⚠ Host Context Banner:** WooCommerce was not resolved.\n" in rendered
        assert "> Reviewer claims are scoped accordingly.\n" in rendered

    def test_banner_follows_the_h1_so_the_document_still_starts_with_it(self):
        """`grade_review_markdown` requires the file to start with '# ' —
        one rule for every rendering, and prominence survives either way."""
        data = self._base()
        data["host_context_banner"] = {
            "degraded": True, "reason": "fully_unavailable",
            "message": "Nothing resolved.", "unresolved": [],
        }
        rendered = render_markdown(data)
        assert rendered.startswith("# Reconciliator Review - PR #9\n")
        assert "> **⚠ Host Context Banner:** Nothing resolved." in rendered
        assert rendered.index("Host Context Banner") < rendered.index(
            "## Executive Summary"
        )

    def test_unknown_recommendation_priorities_render_rather_than_vanish(self):
        data = self._base()
        data["recommendations"] = {
            "immediate": ["Fix the escaping"],
            "urgent": ["Roll back the migration"],
        }
        rendered = render_markdown(data)
        assert "## Recommendations" in rendered
        assert "- Fix the escaping" in rendered
        assert "**Urgent:**" in rendered
        assert "- Roll back the migration" in rendered

    def test_only_unknown_priorities_still_render_a_populated_section(self):
        data = self._base()
        data["recommendations"] = {"urgent": ["Roll back the migration"]}
        rendered = render_markdown(data)
        assert "## Recommendations" in rendered
        assert "- Roll back the migration" in rendered

    def test_a_header_is_never_emitted_over_dropped_content(self):
        """Every priority empty is not a section — the old guard used
        `any(values)` and could emit a header with nothing beneath it."""
        data = self._base()
        data["recommendations"] = {"immediate": [], "urgent": []}
        assert "## Recommendations" not in render_markdown(data)

"""
Tests for the grading functions in graders.py.

Validates graders work correctly on synthetic inputs.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent  # grading/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
_scripts_dir = str(PLUGIN_ROOT / "scripts")

# Add tests/ and scripts/ to path before importing local modules
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, _scripts_dir)

import pytest

from helpers.graders import (
    GradeResult,
    grade_review_json,
    grade_review_markdown,
    grade_signal_format,
    grade_no_domain_files,
    grade_error_exit,
    grade_output_pair,
    grade_review_baseline,
    match_findings,
    grade_detection,
    merge_grades,
    aggregate_detection_trials,
)

from review.agent.output import ReviewOutputBuilder, finalize_review


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _make_valid_json(tmp_dir: str, reviewer: str = "security") -> str:
    """Create a valid review JSON file using ReviewOutputBuilder."""
    builder = ReviewOutputBuilder.open(tmp_dir, "123", reviewer)
    builder.add_finding(
        severity="high",
        title="SQL Injection",
        file="src/User.php",
        description="Direct input in query",
        recommendation="Use prepared statements",
        line=42,
    )
    builder.record_check(
        "Can request input reach the query?",
        "Trace the request handler into the database call",
        "Yes; the value reaches the query without parameterization.",
    )
    accounting_input = os.path.join(
        tmp_dir, f"{reviewer}-review-accounting-input.json"
    )
    with open(accounting_input, "w") as f:
        json.dump({
            "schema": 4,
            "agent_name": f"{reviewer}-reviewer",
            "reviewer": reviewer,
            "review_claimable_files": [],
            "review_budget": 15,
            "inline_diff_file_count": 3,
            "in_scope_review_file_count": 3,
            "channels": ["blocking"],
        }, f)
    saved = builder.save_draft()
    finalize_review(tmp_dir, reviewer, saved["review_digest"])
    path = os.path.join(tmp_dir, f"{reviewer}-review.json")
    return path


def _make_valid_markdown(tmp_dir: str, reviewer: str = "security") -> str:
    """Create a valid review markdown file using ReviewOutputBuilder."""
    builder = ReviewOutputBuilder(pr_id="123", reviewer=reviewer)
    builder.add_finding(
        severity="high",
        title="SQL Injection",
        file="src/User.php",
        description="Direct input in query",
        recommendation="Use prepared statements",
        line=42,
    )

    path = os.path.join(tmp_dir, f"{reviewer}-review.md")
    with open(path, "w") as f:
        f.write(builder.to_markdown())
    return path


class TestGradeReviewJson:
    """Tests for grade_review_json."""

    def test_valid_json_passes(self, tmp_dir):
        path = _make_valid_json(tmp_dir)
        result = grade_review_json(path)
        assert result.passed, f"Failures: {result.failures}"
        assert result.score == 1.0

    def test_unexpected_retired_field_fails(self, tmp_dir):
        path = _make_valid_json(tmp_dir)
        with open(path) as source:
            data = json.load(source)
        data["issues"] = []
        with open(path, "w") as target:
            json.dump(data, target)

        result = grade_review_json(path)

        assert not result.passed
        assert any("unexpected fields: issues" in failure for failure in result.failures)

    @pytest.mark.parametrize(
        ("malformation", "diagnostic"),
        [
            ("numeric-summary", "review summary is malformed"),
            ("non-object-finding", "review finding 0 must be an object"),
            ("non-list-checks", "review checks must be a list"),
            (
                "non-list-accounting",
                "review reviewed_file_claims must be a list of strings",
            ),
            (
                "retired-schema-and-field",
                "review has unexpected fields: issues",
            ),
        ],
    )
    def test_canonical_rejection_stops_invalid_document_projection(
        self, tmp_dir, malformation, diagnostic
    ):
        path = _make_valid_json(tmp_dir)
        with open(path) as source:
            data = json.load(source)
        if malformation == "numeric-summary":
            data["summary"] = 7
        elif malformation == "non-object-finding":
            data["findings"] = [7]
        elif malformation == "non-list-checks":
            data["checks"] = 7
        elif malformation == "non-list-accounting":
            data["reviewed_file_claims"] = 7
        else:
            data["schema"] = 1
            data["issues"] = []
        with open(path, "w") as target:
            json.dump(data, target)

        result = grade_review_json(path)

        assert result.passed is False
        assert result.failures == [diagnostic]
        assert result.checks_run == 3
        assert result.checks_passed == 2

    @pytest.mark.parametrize(
        "field_name",
        [
            "checks",
            "assessment",
            "review_claimable_files",
            "reviewed_file_claims",
            "unclaimed_review_files",
            "inline_diff_file_count",
            "review_accounted_file_count",
            "in_scope_review_file_count",
        ],
    )
    def test_missing_review_domain_or_accounting_field_fails(
        self, tmp_dir, field_name
    ):
        path = _make_valid_json(tmp_dir)
        with open(path) as f:
            data = json.load(f)
        del data[field_name]
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert not result.passed
        assert any(field_name in failure for failure in result.failures)

    def test_invalid_check_shape_fails(self, tmp_dir):
        path = _make_valid_json(tmp_dir)
        with open(path) as f:
            data = json.load(f)
        del data["checks"][0]["method"]
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert not result.passed
        assert any("check 0" in failure.lower() for failure in result.failures)

    def test_non_nullable_assessment_type_fails(self, tmp_dir):
        path = _make_valid_json(tmp_dir)
        with open(path) as f:
            data = json.load(f)
        data["assessment"] = ["not prose"]
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert not result.passed
        assert any("assessment" in failure for failure in result.failures)

    @pytest.mark.parametrize(
        ("population", "bad_id", "message"),
        [
            ("findings", "f01", "canonical fN id"),
            ("checks", "c01", "canonical cN id"),
        ],
    )
    def test_noncanonical_review_domain_id_fails(
        self, tmp_dir, population, bad_id, message
    ):
        path = _make_valid_json(tmp_dir)
        with open(path) as f:
            data = json.load(f)
        data[population][0]["id"] = bad_id
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert not result.passed
        assert any(message in failure for failure in result.failures)

    @pytest.mark.parametrize(
        ("population", "summary_delta", "message"),
        [
            ("findings", 1, "review finding ids must be unique"),
            ("checks", 0, "review check ids must be unique"),
        ],
    )
    def test_duplicate_review_domain_id_fails(
        self, tmp_dir, population, summary_delta, message
    ):
        path = _make_valid_json(tmp_dir)
        with open(path) as f:
            data = json.load(f)
        data[population].append(dict(data[population][0]))
        data["summary"]["total_findings"] += summary_delta
        if summary_delta:
            data["summary"]["by_severity"]["high"] += summary_delta
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert not result.passed
        assert any(message in failure for failure in result.failures)

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
    def test_next_counter_must_exceed_every_live_id(
        self, tmp_dir, counter, message
    ):
        path = _make_valid_json(tmp_dir)
        with open(path) as f:
            data = json.load(f)
        data["meta"][counter] = 1
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert not result.passed
        assert any(message in failure for failure in result.failures)

    def test_missing_file_fails(self):
        result = grade_review_json("/nonexistent/path.json")
        assert not result.passed
        assert any("does not exist" in f for f in result.failures)

    def test_invalid_json_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            f.write("not json at all {{{")
        result = grade_review_json(path)
        assert not result.passed
        assert any("Invalid JSON" in f for f in result.failures)

    def test_missing_required_field_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "missing.json")
        data = {"pr_id": "1", "reviewer": "test"}  # missing verdict, summary, findings, meta
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_json(path)
        assert not result.passed
        assert any("verdict" in f for f in result.failures)

    def test_invalid_verdict_fails(self, tmp_dir):
        path = _make_valid_json(tmp_dir)
        with open(path) as source:
            data = json.load(source)
        data["verdict"] = "INVALID_VERDICT"
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert not result.passed
        assert result.failures == [
            "review verdict does not match its findings"
        ]

    def test_valid_severity_floor_passes(self, tmp_dir):
        path = _make_valid_json(tmp_dir)
        with open(path) as f:
            data = json.load(f)
        data["findings"][0]["severity_floor"] = "medium"
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert result.passed, result.failures

    def test_severity_below_floor_fails(self, tmp_dir):
        path = _make_valid_json(tmp_dir)
        with open(path) as f:
            data = json.load(f)
        data["findings"][0]["severity"] = "low"
        data["findings"][0]["severity_floor"] = "medium"
        data["verdict"] = "approve"
        data["summary"]["by_severity"]["high"] = 0
        data["summary"]["by_severity"]["low"] = 1
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert not result.passed
        assert any(
            "below floor" in failure for failure in result.failures
        ), result.failures

    def test_empty_file_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "empty.json")
        with open(path, "w") as f:
            f.write("")
        result = grade_review_json(path)
        assert not result.passed

    def test_finding_with_invalid_severity_fails(self, tmp_dir):
        path = _make_valid_json(tmp_dir)
        with open(path) as source:
            data = json.load(source)
        data["findings"][0]["severity"] = "unknown"
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert not result.passed
        assert result.failures == ["review finding 0.severity is invalid"]


class TestGradeReviewMarkdown:
    """Tests for grade_review_markdown."""

    def test_valid_markdown_passes(self, tmp_dir):
        path = _make_valid_markdown(tmp_dir)
        result = grade_review_markdown(path)
        assert result.passed, f"Failures: {result.failures}"

    def test_missing_file_fails(self):
        result = grade_review_markdown("/nonexistent/review.md")
        assert not result.passed

    def test_missing_header_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "no-header.md")
        with open(path, "w") as f:
            f.write("Just some text without a proper header\n")
        result = grade_review_markdown(path)
        assert not result.passed


class TestGradeSignalFormat:
    """Tests for grade_signal_format."""

    def test_valid_signal(self):
        signal = (
            "STATUS: FINISHED\n"
            "OUTPUT_FILES:\n"
            "  - /tmp/security-review.json\n"
            "COUNTS:\n"
            "  critical: 0\n"
            "VERDICT: APPROVE\n"
            "SUMMARY: No findings found\n"
        )
        result = grade_signal_format(signal)
        assert result.passed

    def test_missing_status(self):
        result = grade_signal_format("OUTPUT_FILES:\nVERDICT: APPROVE\n")
        assert not result.passed


class TestGradeNoDomainFiles:
    """Tests for grade_no_domain_files."""

    def test_approve_with_no_findings(self):
        text = "VERDICT: APPROVE\nNo security files to review."
        result = grade_no_domain_files(text)
        assert result.passed

    def test_non_approve_fails(self):
        text = "VERDICT: REQUEST_CHANGES\nCRITICAL: found finding"
        result = grade_no_domain_files(text)
        assert not result.passed

    def test_bootstrap_signal_template_is_not_a_finding(self):
        # Bootstrap output embeds the return-signal template; its "N"
        # placeholders and explicit zero counts are not severity findings.
        text = (
            "STATUS: NO_DOMAIN_FILES\nACTION: APPROVE and exit\n"
            "COUNTS: critical: N, high: N, medium: N\n"
            "critical: 0, high: 0, medium: 0"
        )
        result = grade_no_domain_files(text)
        assert result.passed, f"Failures: {result.failures}"

    def test_nonzero_count_fails(self):
        text = "VERDICT: APPROVE\nCOUNTS: critical: 0, high: 2, medium: 0"
        result = grade_no_domain_files(text)
        assert not result.passed


class TestGradeErrorExitTemplate:
    """The bootstrap return-signal template must not read as a FINISHED claim."""

    def test_indented_template_line_passes(self):
        text = (
            "STATUS: ERROR\nERROR: NO_CHANGES\n"
            "Return signal format:\n  STATUS: FINISHED\n  OUTPUT_FILES:"
        )
        result = grade_error_exit(text)
        assert result.passed, f"Failures: {result.failures}"

    def test_column_zero_finished_signal_fails(self):
        text = "ERROR: something\nSTATUS: FINISHED\nCOUNTS: critical: 0"
        result = grade_error_exit(text)
        assert not result.passed


class TestGradeOutputPair:
    """Tests for grade_output_pair."""

    def test_valid_pair_passes(self, tmp_dir):
        _make_valid_json(tmp_dir, "security")
        _make_valid_markdown(tmp_dir, "security")
        result = grade_output_pair(tmp_dir, "security")
        assert result.passed, f"Failures: {result.failures}"

    def test_missing_json_fails(self, tmp_dir):
        _make_valid_markdown(tmp_dir, "security")
        result = grade_output_pair(tmp_dir, "security")
        assert not result.passed


def _make_valid_baseline(tmp_dir: str) -> str:
    """Create a valid .branch-review-baseline.json file."""
    path = os.path.join(tmp_dir, ".branch-review-baseline.json")
    data = {
        "last_reviewed_sha": "abc123def456789012345678901234567890abcd",
        "last_reviewed_at": "2026-02-09T12:34:56",
        "review_type": "full",
        "review_count": 1,
        "base_ref": "main",
        "git_range_used": "main..HEAD",
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return path


class TestGradeReviewBaseline:
    """Tests for grade_review_baseline."""

    def test_valid_baseline_passes(self, tmp_dir):
        path = _make_valid_baseline(tmp_dir)
        result = grade_review_baseline(path)
        assert result.passed, f"Failures: {result.failures}"
        assert result.score == 1.0

    def test_missing_file_fails(self):
        result = grade_review_baseline("/nonexistent/.branch-review-baseline.json")
        assert not result.passed
        assert any("does not exist" in f for f in result.failures)

    def test_invalid_json_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        with open(path, "w") as f:
            f.write("not json {{{")
        result = grade_review_baseline(path)
        assert not result.passed
        assert any("Invalid JSON" in f for f in result.failures)

    def test_missing_required_field_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        data = {"last_reviewed_sha": "abc1234", "review_count": 1}
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_baseline(path)
        assert not result.passed
        assert any("base_ref" in f for f in result.failures)

    def test_invalid_sha_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        data = {
            "last_reviewed_sha": "not-a-sha!",
            "last_reviewed_at": "2026-02-09T12:34:56",
            "review_type": "full",
            "review_count": 1,
            "base_ref": "main",
            "git_range_used": "main..HEAD",
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_baseline(path)
        assert not result.passed
        assert any("Invalid SHA" in f for f in result.failures)

    def test_short_sha_passes(self, tmp_dir):
        """Short SHAs (7+ chars) are valid."""
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        data = {
            "last_reviewed_sha": "abc1234",
            "last_reviewed_at": "2026-02-09T12:34:56",
            "review_type": "incremental",
            "review_count": 1,
            "base_ref": "main",
            "git_range_used": "main..HEAD",
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_baseline(path)
        assert result.passed, f"Failures: {result.failures}"

    def test_zero_review_count_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        data = {
            "last_reviewed_sha": "abc1234",
            "last_reviewed_at": "2026-02-09T12:34:56",
            "review_type": "full",
            "review_count": 0,
            "base_ref": "main",
            "git_range_used": "main..HEAD",
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_baseline(path)
        assert not result.passed
        assert any("review_count" in f for f in result.failures)

    def test_missing_range_separator_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, ".branch-review-baseline.json")
        data = {
            "last_reviewed_sha": "abc1234",
            "last_reviewed_at": "2026-02-09T12:34:56",
            "review_type": "full",
            "review_count": 1,
            "base_ref": "main",
            "git_range_used": "HEAD",
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_baseline(path)
        assert not result.passed
        assert any("git_range_used" in f for f in result.failures)


def test_finding_accepts_behavior_evidence_and_source_cited():
    b = ReviewOutputBuilder(reviewer="ecosystem-integration-reviewer", pr_id="0")
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
    output = b.to_dict()
    finding = output["findings"][0]
    assert finding["behavior_evidence"] == "cited"
    assert finding["source_cited"] == "woocommerce/.../class-wc-order.php:200"


def test_finding_behavior_evidence_optional():
    b = ReviewOutputBuilder(reviewer="security-reviewer", pr_id="0")
    b.add_finding(
        severity="low", category="xss", title="X", description="y",
        file="f.php", line=1, recommendation="z",
    )
    output = b.to_dict()
    # Fields omitted when not provided
    assert "behavior_evidence" not in output["findings"][0]
    assert "source_cited" not in output["findings"][0]


def test_finding_behavior_evidence_invalid_rejected():
    import pytest

    b = ReviewOutputBuilder(reviewer="ecosystem-integration-reviewer", pr_id="0")
    with pytest.raises(ValueError, match="behavior_evidence"):
        b.add_finding(
            severity="low", category="other", title="T", description="d",
            file="f.php", line=1, recommendation="r",
            behavior_evidence="MAYBE",
        )


def test_finding_behavior_evidence_rejects_speculative():
    import pytest

    b = ReviewOutputBuilder(reviewer="ecosystem-integration-reviewer", pr_id="0")
    with pytest.raises(ValueError, match="behavior_evidence"):
        b.add_finding(
            severity="low", category="behavior-assumption", title="T", description="d",
            file="f.php", line=1, recommendation="r",
            behavior_evidence="speculative",
        )


class TestMatchFindings:
    """Pure matcher: file + line-window + keyword regexes, claimed-set semantics."""

    KEY = {
        "required_findings": [
            {"id": "sql-injection", "file": "src/UserHandler.php", "line": 6,
             "match_any": [r"sql\s*inject", r"\bprepare\b"]},
        ],
        "acceptable_findings": [
            {"id": "csrf-nonce", "file": "src/UserHandler.php",
             "match_any": [r"\bnonce\b", r"csrf"]},
        ],
    }

    def _finding(self, **kw):
        base = {"file": "src/UserHandler.php", "line": 6, "title": "",
                "description": "", "category": "", "severity": "high"}
        base.update(kw)
        return base

    def test_required_matches_on_file_line_and_keyword(self):
        findings = [self._finding(title="SQL injection via $_GET")]
        m = match_findings(findings, self.KEY)
        assert m["matched_required"] == {"sql-injection": 0}
        assert m["missing_required"] == []
        assert m["unexpected"] == []

    def test_line_outside_tolerance_does_not_match(self):
        findings = [self._finding(line=20, title="SQL injection via $_GET")]
        m = match_findings(findings, self.KEY)
        assert m["missing_required"] == ["sql-injection"]
        assert [u["index"] for u in m["unexpected"]] == [0]

    def test_unexpected_entries_carry_diagnosable_evidence(self):
        # The widen-the-regex workflow needs to see what the reviewer wrote:
        # the matcher greps title/description/category, so an unexpected entry
        # must expose those fields plus location, not a bare index.
        findings = [self._finding(
            line=20, title="SQL injection via $_GET",
            description="Query concatenates raw input" + "x" * 400,
        )]
        u = match_findings(findings, self.KEY)["unexpected"][0]
        assert u["file"] == "src/UserHandler.php"
        assert u["line"] == 20
        assert u["title"] == "SQL injection via $_GET"
        assert u["description"].startswith("Query concatenates raw input")
        assert len(u["description"]) == 300
        assert "severity" in u and "category" in u

    def test_keyword_miss_does_not_match(self):
        findings = [self._finding(title="Something unrelated entirely")]
        m = match_findings(findings, self.KEY)
        assert m["missing_required"] == ["sql-injection"]

    def test_key_without_line_matches_any_line_including_null(self):
        findings = [self._finding(line=None, title="Missing nonce verification")]
        m = match_findings(findings, self.KEY)
        assert m["matched_acceptable"] == {"csrf-nonce": 0}
        assert m["unexpected"] == []

    def test_finding_with_null_line_cannot_satisfy_line_bearing_key(self):
        findings = [self._finding(line=None, title="SQL injection via $_GET")]
        m = match_findings(findings, self.KEY)
        assert m["missing_required"] == ["sql-injection"]

    def test_one_finding_claims_only_one_spec(self):
        # A single finding mentioning both injection and nonce satisfies the
        # required spec (matched first) and leaves the acceptable one unmatched.
        findings = [self._finding(title="SQL injection; also missing nonce")]
        m = match_findings(findings, self.KEY)
        assert m["matched_required"] == {"sql-injection": 0}
        assert m["matched_acceptable"] == {}

    def test_keyword_in_category_matches(self):
        findings = [self._finding(title="", description="", category="sql injection")]
        m = match_findings(findings, self.KEY)
        assert m["matched_required"] == {"sql-injection": 0}

    def test_line_tolerance_override(self):
        key = {
            "required_findings": [
                {"id": "sql-injection", "file": "src/UserHandler.php", "line": 6,
                 "line_tolerance": 0, "match_any": [r"sql\s*inject"]},
            ],
            "acceptable_findings": [],
        }
        findings = [self._finding(line=7, title="SQL injection via $_GET")]
        m = match_findings(findings, key)
        assert m["missing_required"] == ["sql-injection"]

        findings = [self._finding(line=6, title="SQL injection via $_GET")]
        m = match_findings(findings, key)
        assert m["matched_required"] == {"sql-injection": 0}


class TestGradeDetection:
    """Answer-key grading of a parsed review JSON."""

    def _review(
        self,
        verdict="request_changes",
        findings=None,
        checks=None,
        unclaimed_review_files=None,
    ):
        return {
            "verdict": verdict,
            "findings": findings or [],
            "checks": checks or [],
            "unclaimed_review_files": unclaimed_review_files or [],
        }

    def _finding(self, **kw):
        base = {"file": "src/UserHandler.php", "line": 6, "title": "SQL injection",
                "description": "", "category": "sql-injection", "severity": "critical"}
        base.update(kw)
        return base

    KEY = {
        "verdict_in": ["block", "request_changes"],
        "required_findings": [
            {"id": "sql-injection", "file": "src/UserHandler.php", "line": 6,
             "match_any": [r"sql\s*inject"]},
        ],
    }

    def test_pass_when_required_found_and_verdict_acceptable(self):
        r = grade_detection(self._review("block", [self._finding()]), self.KEY)
        assert r.passed, r.failures
        assert r.detail["verdict"] == "block"
        assert r.detail["match"]["matched_required"] == {"sql-injection": 0}

    def test_fail_when_required_missing(self):
        r = grade_detection(self._review("block", []), self.KEY)
        assert not r.passed
        assert any("sql-injection" in f for f in r.failures)

    def test_fail_on_unacceptable_verdict(self):
        r = grade_detection(self._review("approve", [self._finding()]), self.KEY)
        assert not r.passed
        assert any("verdict" in f for f in r.failures)

    def test_max_severity_gate(self):
        key = {"verdict_in": ["approve"], "max_severity": "low"}
        clean = self._review("approve", [])
        assert grade_detection(clean, key).passed
        boundary = self._review("approve", [self._finding(severity="low")])
        assert grade_detection(boundary, key).passed
        noisy = self._review("approve", [self._finding(severity="medium")])
        r = grade_detection(noisy, key)
        assert not r.passed
        assert any("max severity" in f for f in r.failures)

    def test_max_unexpected_gate(self):
        key = dict(self.KEY, max_unexpected=0)
        extra = self._finding(file="src/Other.php", title="Unrelated claim")
        r = grade_detection(self._review("block", [self._finding(), extra]), key)
        assert not r.passed
        assert any("unexpected" in f for f in r.failures)

        key_allow_one = dict(self.KEY, max_unexpected=1)
        r = grade_detection(self._review("block", [self._finding(), extra]), key_allow_one)
        assert r.passed, r.failures

    def test_expect_not_applicable(self):
        key = {"expect_not_applicable": True}
        passing = grade_detection(self._review("not_applicable", []), key)
        assert passing.passed
        assert passing.detail["finding_count"] == 0

        r = grade_detection(self._review("comment", [self._finding()]), key)
        assert not r.passed

        r = grade_detection(self._review("not_applicable", [self._finding()]), key)
        assert not r.passed
        assert r.detail["finding_count"] == 1

    def test_material_negative_requires_a_structured_check_outcome(self):
        key = {"verdict_in": ["approve"], "min_check_count": 1}
        missing = grade_detection(self._review("approve"), key)

        assert not missing.passed
        assert any("structured checks" in failure for failure in missing.failures)

        malformed = self._review("approve", checks=[{
            "id": "c1",
            "question": "",
            "method": "",
            "result": "",
            "source_reviewers": [],
        }])
        assert not grade_detection(malformed, key).passed

        recorded = self._review("approve", checks=[{
            "id": "c1",
            "question": "Does any dependent selector require the removed markup?",
            "method": "Enumerated selectors in the dependent stylesheet",
            "result": "No selector depends on the removed element.",
            "source_reviewers": ["woo-regression"],
        }])
        assert grade_detection(recorded, key).passed

    def test_review_accounting_gate_uses_serialized_unclaimed_files(self):
        key = {
            "verdict_in": ["approve"],
            "max_unclaimed_review_file_count": 0,
        }

        incomplete = self._review(
            "approve", unclaimed_review_files=["src/not-reviewed.php"]
        )
        result = grade_detection(incomplete, key)
        assert not result.passed
        assert any("unclaimed review files" in failure for failure in result.failures)

        assert grade_detection(self._review("approve"), key).passed


class TestMergeGrades:
    def test_merge_combines_counts_and_failures(self):
        a = GradeResult(passed=True, score=1.0, failures=[], checks_run=3, checks_passed=3)
        b = GradeResult(passed=False, score=0.5, failures=["x"], checks_run=2,
                        checks_passed=1, detail={"verdict": "block"})
        m = merge_grades(a, b)
        assert not m.passed
        assert m.checks_run == 5 and m.checks_passed == 4
        assert m.failures == ["x"]
        assert m.score == 0.8
        assert m.detail == {"verdict": "block"}

    def test_merge_detail_falls_back_to_first(self):
        a = GradeResult(passed=True, score=1.0, failures=[], checks_run=1,
                        checks_passed=1, detail={"verdict": "x"})
        b = GradeResult(passed=True, score=1.0, failures=[], checks_run=1,
                        checks_passed=1, detail=None)
        m = merge_grades(a, b)
        assert m.detail == {"verdict": "x"}

    def test_detection_label_keeps_merged_failures_attributable(self):
        a = GradeResult(passed=False, score=0.0, failures=["missing field"],
                        checks_run=1, checks_passed=0)
        b = GradeResult(passed=False, score=0.0, failures=["verdict wrong"],
                        checks_run=1, checks_passed=0)
        m = merge_grades(a, b, detection_label="detection")
        assert m.failures == ["missing field", "detection: verdict wrong"]
        # The label must not mutate the input grade.
        assert b.failures == ["verdict wrong"]


class TestGradeDetectionGates:
    """grade_detection records per-gate outcomes for trial aggregation."""

    def test_gates_recorded_when_present(self):
        key = {
            "verdict_in": ["approve"],
            "max_severity": "low",
            "max_unexpected": 0,
            "min_check_count": 1,
            "max_unclaimed_review_file_count": 0,
        }
        review = {"verdict": "approve",
                  "findings": [{"file": "f.php", "line": 1, "title": "noise",
                              "description": "", "category": "", "severity": "medium"}],
                  "checks": [],
                  "unclaimed_review_files": ["src/not-reviewed.php"]}
        r = grade_detection(review, key)
        assert r.detail["gates"] == {
            "max_severity": False,
            "max_unexpected": False,
            "min_check_count": False,
            "max_unclaimed_review_file_count": False,
        }

    def test_gates_empty_when_no_gates_in_key(self):
        r = grade_detection({"verdict": "block", "findings": []}, {"verdict_in": ["block"]})
        assert r.detail["gates"] == {}


class TestAggregateDetectionTrials:
    """Aggregation = strict majority of trials passing outright.

    Per-check majority votes were removed (2026-08-09): an outright majority
    implies a per-check majority for every check (the same passing trials
    passed each one), so per-check votes could never be the sole failure and
    only duplicated the diagnostics per_trial_failures already carries.
    """

    @staticmethod
    def _grade(passed, detail=None):
        return GradeResult(
            passed=passed, score=1.0 if passed else 0.0,
            failures=[] if passed else ["some check failed"],
            checks_run=1, checks_passed=1 if passed else 0,
            detail=detail,
        )

    def test_minority_passing_trials_fail(self):
        grades = [self._grade(True), self._grade(False), self._grade(False)]
        result = aggregate_detection_trials(grades)
        assert not result.passed
        assert any("1/3" in f for f in result.failures)

    def test_failed_aggregate_carries_trial_indexed_diagnostics(self):
        # The console prints failures only — a failed --trials run without
        # --report-out must still say WHY trials failed, not just how many.
        grades = [self._grade(True), self._grade(False), self._grade(False)]
        result = aggregate_detection_trials(grades)
        assert "trial 2: some check failed" in result.failures
        assert "trial 3: some check failed" in result.failures
        # A passing aggregate keeps failures empty — nonempty failures on a
        # passed result would confuse consumers.
        passing = aggregate_detection_trials(
            [self._grade(True), self._grade(True), self._grade(False)])
        assert passing.passed
        assert passing.failures == []

    def test_even_trials_require_strict_majority(self):
        # --trials 2: one pass is not "more than half" — both must pass.
        grades = [self._grade(True), self._grade(False)]
        assert not aggregate_detection_trials(grades).passed
        assert aggregate_detection_trials(
            [self._grade(True), self._grade(True)]).passed

    def test_aggregate_is_a_single_check(self):
        # No per-check votes: check counts must not scale with key
        # complexity, so single- and multi-trial counts are never mixed up.
        result = aggregate_detection_trials([self._grade(True)])
        assert result.checks_run == 1
        assert result.checks_passed == 1
        assert result.score == 1.0

    def test_unreadable_trial_detail_never_improves_aggregate(self):
        # A failed trial with detail=None is simply a failed trial; its
        # detail slot is preserved as {} so per-trial lists stay
        # index-aligned with the requested trial count.
        d0 = {"verdict": "block", "compliance_passed": True}
        grades = [self._grade(True, d0), self._grade(False, detail=None),
                  self._grade(False, detail=None)]
        result = aggregate_detection_trials(grades)
        assert not result.passed
        assert result.detail["per_trial"] == [d0, {}, {}]

    def test_detail_carries_trial_count_and_per_trial(self):
        d0 = {"verdict": "approve", "compliance_passed": True}
        result = aggregate_detection_trials([self._grade(True, d0)])
        assert result.detail["trials"] == 1
        assert result.detail["per_trial"] == [d0]

    def test_detail_carries_full_aggregate_diagnostics(self):
        # The aggregate itself emits the documented detail schema — every
        # caller gets per-trial diagnostics without post-hoc assembly.
        grades = [
            self._grade(True, {"status": "graded", "models": ["claude-b-5"]}),
            self._grade(False, {"status": "timed_out"}),
            self._grade(False, None),
        ]
        detail = aggregate_detection_trials(grades).detail
        assert detail["per_trial_passed"] == [True, False, False]
        assert detail["per_trial_failures"] == [
            [], ["some check failed"], ["some check failed"]]
        assert detail["per_trial_status"] == [
            "graded", "timed_out", "harness_error"]
        assert detail["models"] == ["claude-b-5"]


class TestReviewRoundHardening:
    """Behaviors added by the 2026-08-06 independent review round."""

    def test_paths_match_normalizes_real_world_variants(self):
        from helpers.graders import _paths_match
        spec = "src/UserHandler.php"
        assert _paths_match("b/src/UserHandler.php", spec)            # diff prefix
        assert _paths_match("src\\UserHandler.php", spec)             # backslash
        assert _paths_match("src//UserHandler.php", spec)             # double slash
        assert _paths_match("./src/./UserHandler.php", spec)          # dot segments
        assert not _paths_match("src/Other.php", spec)
        assert not _paths_match("vendor/src/OtherHandler.php", spec)
        # Absolute paths require repository context before pure matching.
        assert not _paths_match("/tmp/eval-x/src/UserHandler.php", spec)
        # Suffix matching must respect segment boundaries.
        assert not _paths_match("notsrc/UserHandler.php", "rc/UserHandler.php")

    def test_paths_match_rejects_extra_prefix_on_relative_path(self):
        from helpers.graders import _paths_match

        assert not _paths_match(
            "vendor/src/UserHandler.php", "src/UserHandler.php",
        )

    def test_detection_matches_absolute_path_relative_to_repo_root(self, tmp_path):
        expected = "src/UserHandler.php"
        finding = {
            "file": str(tmp_path / expected),
            "severity": "high",
            "title": "SQL injection",
            "description": "Raw input reaches a query",
            "category": "sql-injection",
            "line": 10,
        }
        key = {
            "required_findings": [{
                "id": "sql-injection",
                "file": expected,
                "line": 10,
                "match_any": [r"sql.?injection"],
            }],
        }

        assert grade_detection(
            {"verdict": "block", "findings": [finding]},
            key,
            repo_root=tmp_path,
        ).passed

    def test_detection_rejects_absolute_path_to_different_repo_file(
        self, tmp_path
    ):
        expected = "src/UserHandler.php"
        finding = {
            "file": str(tmp_path / "vendor" / expected),
            "severity": "high",
            "title": "SQL injection",
            "description": "Raw input reaches a query",
            "category": "sql-injection",
            "line": 10,
        }
        key = {
            "required_findings": [{
                "id": "sql-injection",
                "file": expected,
                "line": 10,
                "match_any": [r"sql.?injection"],
            }],
        }

        assert not grade_detection(
            {"verdict": "block", "findings": [finding]},
            key,
            repo_root=tmp_path,
        ).passed

    def test_unknown_severity_fails_max_severity_gate(self):
        from helpers.graders import grade_detection
        key = {"max_severity": "low"}
        review = {"verdict": "approve",
                  "findings": [{"severity": "blocker", "file": "f", "title": "t",
                              "description": "", "category": ""}]}
        result = grade_detection(review, key)
        assert not result.passed
        assert result.detail["gates"]["max_severity"] is False

    def test_abstention_accepts_both_doctrine_readings(self):
        from helpers.graders import grade_detection
        key = {"expect_not_applicable": True}
        for verdict in ("not_applicable", "approve"):
            review = {"verdict": verdict, "findings": []}
            assert grade_detection(review, key).passed, verdict
        assert not grade_detection({"verdict": "comment", "findings": []}, key).passed
        assert not grade_detection(
            {"verdict": "approve", "findings": [{"severity": "low", "file": "f",
                                              "title": "t", "description": "", "category": ""}]},
            key,
        ).passed

    def test_patterns_cannot_bridge_field_boundaries(self):
        from helpers.graders import _finding_matches
        spec = {"file": "f.php", "match_any": [r"sql\s*inject"]}
        finding = {"file": "f.php", "title": "Uses raw SQL",
                 "description": "injection unrelated word appears here",
                 "category": ""}
        assert not _finding_matches(finding, spec)
        finding["description"] = "clear SQL injection via concatenation"
        assert _finding_matches(finding, spec)

    def test_unexpected_title_is_truncated(self):
        from helpers.graders import match_findings
        findings = [{"file": "g.php", "line": 1, "severity": "low",
                   "category": "c", "title": "x" * 5000, "description": "d"}]
        u = match_findings(findings, {"required_findings": []})["unexpected"][0]
        assert len(u["title"]) == 300

    def test_min_severity_floor_rejects_underclassified_findings(self):
        from helpers.graders import _finding_matches
        spec = {"file": "f.php", "min_severity": "critical",
                "match_any": [r"sql\s*inject"]}
        finding = {"file": "f.php", "title": "SQL injection", "description": "",
                 "category": "", "severity": "high"}
        assert not _finding_matches(finding, spec)
        finding["severity"] = "critical"
        assert _finding_matches(finding, spec)
        # Unknown severity fails the floor closed.
        finding["severity"] = "blocker"
        assert not _finding_matches(finding, spec)

    def test_reviewer_identity_mismatch_fails_json_grade(self, tmp_dir):
        # A valid artifact at the expected path but labeled as another
        # reviewer must not pass compliance under the wrong identity.
        path = _make_valid_json(tmp_dir, reviewer="security")
        assert grade_review_json(path, expected_reviewer="security").passed
        mismatch = grade_review_json(path, expected_reviewer="performance")
        assert not mismatch.passed
        assert mismatch.failures == [
            "review reviewer does not match finalization request"
        ]
        # Omitting the expectation keeps prior behavior.
        assert grade_review_json(path).passed

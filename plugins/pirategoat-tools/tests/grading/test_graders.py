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

from review.agent.output import ReviewOutputBuilder


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _make_valid_json(tmp_dir: str, reviewer: str = "security") -> str:
    """Create a valid review JSON file using ReviewOutputBuilder."""
    builder = ReviewOutputBuilder(pr_id="123", reviewer=reviewer)
    builder.add_issue(
        severity="high",
        title="SQL Injection",
        file="src/User.php",
        description="Direct input in query",
        recommendation="Use prepared statements",
        line=42,
    )
    builder.set_files_reviewed(3)

    path = os.path.join(tmp_dir, f"{reviewer}-review.json")
    with open(path, "w") as f:
        f.write(builder.to_json())
    return path


def _make_valid_markdown(tmp_dir: str, reviewer: str = "security") -> str:
    """Create a valid review markdown file using ReviewOutputBuilder."""
    builder = ReviewOutputBuilder(pr_id="123", reviewer=reviewer)
    builder.add_issue(
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
        data = {"pr_id": "1", "reviewer": "test"}  # missing verdict, summary, issues, meta
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_json(path)
        assert not result.passed
        assert any("verdict" in f for f in result.failures)

    def test_invalid_verdict_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad-verdict.json")
        data = {
            "pr_id": "1",
            "reviewer": "test",
            "verdict": "INVALID_VERDICT",
            "summary": {"total_issues": 0, "by_severity": {}},
            "issues": [],
            "meta": {},
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_json(path)
        assert not result.passed
        assert any("Invalid verdict" in f for f in result.failures)

    def test_valid_severity_floor_passes(self, tmp_dir):
        path = _make_valid_json(tmp_dir)
        with open(path) as f:
            data = json.load(f)
        data["issues"][0]["severity_floor"] = "medium"
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert result.passed, result.failures

    def test_severity_below_floor_fails(self, tmp_dir):
        path = _make_valid_json(tmp_dir)
        with open(path) as f:
            data = json.load(f)
        data["issues"][0]["severity"] = "low"
        data["issues"][0]["severity_floor"] = "medium"
        with open(path, "w") as f:
            json.dump(data, f)

        result = grade_review_json(path)

        assert not result.passed
        assert any("below floor" in failure for failure in result.failures)

    def test_empty_file_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "empty.json")
        with open(path, "w") as f:
            f.write("")
        result = grade_review_json(path)
        assert not result.passed

    def test_issue_with_invalid_severity_fails(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad-sev.json")
        data = {
            "pr_id": "1",
            "reviewer": "test",
            "verdict": "comment",
            "summary": {"total_issues": 1, "by_severity": {"unknown": 1}},
            "issues": [
                {
                    "id": "abc",
                    "severity": "unknown",
                    "title": "Test",
                    "file": "a.py",
                    "description": "desc",
                    "recommendation": "fix",
                }
            ],
            "meta": {},
        }
        with open(path, "w") as f:
            json.dump(data, f)
        result = grade_review_json(path)
        assert not result.passed
        assert any("invalid severity" in f.lower() for f in result.failures)


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
            "SUMMARY: No issues found\n"
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
        text = "VERDICT: REQUEST_CHANGES\nCRITICAL: found issue"
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


def test_issue_accepts_behavior_evidence_and_source_cited():
    b = ReviewOutputBuilder(reviewer="ecosystem-integration-reviewer", pr_id="0")
    b.add_issue(
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
    issue = output["issues"][0]
    assert issue["behavior_evidence"] == "cited"
    assert issue["source_cited"] == "woocommerce/.../class-wc-order.php:200"


def test_issue_behavior_evidence_optional():
    b = ReviewOutputBuilder(reviewer="security-reviewer", pr_id="0")
    b.add_issue(
        severity="low", category="xss", title="X", description="y",
        file="f.php", line=1, recommendation="z",
    )
    output = b.to_dict()
    # Fields omitted when not provided
    assert "behavior_evidence" not in output["issues"][0]
    assert "source_cited" not in output["issues"][0]


def test_issue_behavior_evidence_invalid_rejected():
    import pytest

    b = ReviewOutputBuilder(reviewer="ecosystem-integration-reviewer", pr_id="0")
    with pytest.raises(ValueError, match="behavior_evidence"):
        b.add_issue(
            severity="low", category="other", title="T", description="d",
            file="f.php", line=1, recommendation="r",
            behavior_evidence="MAYBE",
        )


def test_issue_behavior_evidence_rejects_speculative():
    import pytest

    b = ReviewOutputBuilder(reviewer="ecosystem-integration-reviewer", pr_id="0")
    with pytest.raises(ValueError, match="behavior_evidence"):
        b.add_issue(
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

    def _issue(self, **kw):
        base = {"file": "src/UserHandler.php", "line": 6, "title": "",
                "description": "", "category": "", "severity": "high"}
        base.update(kw)
        return base

    def test_required_matches_on_file_line_and_keyword(self):
        issues = [self._issue(title="SQL injection via $_GET")]
        m = match_findings(issues, self.KEY)
        assert m["matched_required"] == {"sql-injection": 0}
        assert m["missing_required"] == []
        assert m["unexpected"] == []

    def test_line_outside_tolerance_does_not_match(self):
        issues = [self._issue(line=20, title="SQL injection via $_GET")]
        m = match_findings(issues, self.KEY)
        assert m["missing_required"] == ["sql-injection"]
        assert [u["index"] for u in m["unexpected"]] == [0]

    def test_unexpected_entries_carry_diagnosable_evidence(self):
        # The widen-the-regex workflow needs to see what the reviewer wrote:
        # the matcher greps title/description/category, so an unexpected entry
        # must expose those fields plus location, not a bare index.
        issues = [self._issue(
            line=20, title="SQL injection via $_GET",
            description="Query concatenates raw input" + "x" * 400,
        )]
        u = match_findings(issues, self.KEY)["unexpected"][0]
        assert u["file"] == "src/UserHandler.php"
        assert u["line"] == 20
        assert u["title"] == "SQL injection via $_GET"
        assert u["description"].startswith("Query concatenates raw input")
        assert len(u["description"]) == 300
        assert "severity" in u and "category" in u

    def test_keyword_miss_does_not_match(self):
        issues = [self._issue(title="Something unrelated entirely")]
        m = match_findings(issues, self.KEY)
        assert m["missing_required"] == ["sql-injection"]

    def test_key_without_line_matches_any_line_including_null(self):
        issues = [self._issue(line=None, title="Missing nonce verification")]
        m = match_findings(issues, self.KEY)
        assert m["matched_acceptable"] == {"csrf-nonce": 0}
        assert m["unexpected"] == []

    def test_issue_with_null_line_cannot_satisfy_line_bearing_key(self):
        issues = [self._issue(line=None, title="SQL injection via $_GET")]
        m = match_findings(issues, self.KEY)
        assert m["missing_required"] == ["sql-injection"]

    def test_one_issue_claims_only_one_spec(self):
        # A single issue mentioning both injection and nonce satisfies the
        # required spec (matched first) and leaves the acceptable one unmatched.
        issues = [self._issue(title="SQL injection; also missing nonce")]
        m = match_findings(issues, self.KEY)
        assert m["matched_required"] == {"sql-injection": 0}
        assert m["matched_acceptable"] == {}

    def test_wrong_file_never_matches(self):
        issues = [self._issue(file="src/Other.php", title="SQL injection")]
        m = match_findings(issues, self.KEY)
        assert m["missing_required"] == ["sql-injection"]
        assert [u["index"] for u in m["unexpected"]] == [0]

    def test_dot_slash_prefix_is_normalized(self):
        issues = [self._issue(file="./src/UserHandler.php", title="SQL injection")]
        m = match_findings(issues, self.KEY)
        assert m["matched_required"] == {"sql-injection": 0}

    def test_matching_searches_description_and_category_too(self):
        issues = [self._issue(title="Bad query", description="should use prepare()")]
        m = match_findings(issues, self.KEY)
        assert m["matched_required"] == {"sql-injection": 0}

    def test_keyword_in_category_matches(self):
        issues = [self._issue(title="", description="", category="sql injection")]
        m = match_findings(issues, self.KEY)
        assert m["matched_required"] == {"sql-injection": 0}

    def test_line_tolerance_override(self):
        key = {
            "required_findings": [
                {"id": "sql-injection", "file": "src/UserHandler.php", "line": 6,
                 "line_tolerance": 0, "match_any": [r"sql\s*inject"]},
            ],
            "acceptable_findings": [],
        }
        issues = [self._issue(line=7, title="SQL injection via $_GET")]
        m = match_findings(issues, key)
        assert m["missing_required"] == ["sql-injection"]

        issues = [self._issue(line=6, title="SQL injection via $_GET")]
        m = match_findings(issues, key)
        assert m["matched_required"] == {"sql-injection": 0}


class TestGradeDetection:
    """Answer-key grading of a parsed review JSON."""

    def _review(self, verdict="request_changes", issues=None):
        return {"verdict": verdict, "issues": issues or []}

    def _issue(self, **kw):
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
        r = grade_detection(self._review("block", [self._issue()]), self.KEY)
        assert r.passed, r.failures
        assert r.detail["verdict"] == "block"
        assert r.detail["match"]["matched_required"] == {"sql-injection": 0}

    def test_fail_when_required_missing(self):
        r = grade_detection(self._review("block", []), self.KEY)
        assert not r.passed
        assert any("sql-injection" in f for f in r.failures)

    def test_fail_on_unacceptable_verdict(self):
        r = grade_detection(self._review("approve", [self._issue()]), self.KEY)
        assert not r.passed
        assert any("verdict" in f for f in r.failures)

    def test_max_severity_gate(self):
        key = {"verdict_in": ["approve"], "max_severity": "low"}
        clean = self._review("approve", [])
        assert grade_detection(clean, key).passed
        boundary = self._review("approve", [self._issue(severity="low")])
        assert grade_detection(boundary, key).passed
        noisy = self._review("approve", [self._issue(severity="medium")])
        r = grade_detection(noisy, key)
        assert not r.passed
        assert any("max severity" in f for f in r.failures)

    def test_max_unexpected_gate(self):
        key = dict(self.KEY, max_unexpected=0)
        extra = self._issue(file="src/Other.php", title="Unrelated claim")
        r = grade_detection(self._review("block", [self._issue(), extra]), key)
        assert not r.passed
        assert any("unexpected" in f for f in r.failures)

        key_allow_one = dict(self.KEY, max_unexpected=1)
        r = grade_detection(self._review("block", [self._issue(), extra]), key_allow_one)
        assert r.passed, r.failures

    def test_expect_not_applicable(self):
        key = {"expect_not_applicable": True}
        passing = grade_detection(self._review("not_applicable", []), key)
        assert passing.passed
        assert passing.detail["issue_count"] == 0

        r = grade_detection(self._review("comment", [self._issue()]), key)
        assert not r.passed

        r = grade_detection(self._review("not_applicable", [self._issue()]), key)
        assert not r.passed
        assert r.detail["issue_count"] == 1


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


class TestGradeDetectionGates:
    """grade_detection records per-gate outcomes for trial aggregation."""

    def test_gates_recorded_when_present(self):
        key = {"verdict_in": ["approve"], "max_severity": "low", "max_unexpected": 0}
        review = {"verdict": "approve",
                  "issues": [{"file": "f.php", "line": 1, "title": "noise",
                              "description": "", "category": "", "severity": "medium"}]}
        r = grade_detection(review, key)
        assert r.detail["gates"] == {"max_severity": False, "max_unexpected": False}

    def test_gates_empty_when_no_gates_in_key(self):
        r = grade_detection({"verdict": "block", "issues": []}, {"verdict_in": ["block"]})
        assert r.detail["gates"] == {}


class TestAggregateDetectionTrials:
    KEY = {
        "verdict_in": ["block"],
        "required_findings": [
            {"id": "sql-injection", "file": "f.php", "match_any": [r"inject"]},
        ],
    }

    def _detail(self, verdict="block", found=True, compliant=True, gates=None):
        matched = {"sql-injection": 0} if found else {}
        return {
            "verdict": verdict,
            "compliance_passed": compliant,
            "gates": gates if gates is not None else {},
            "match": {"matched_required": matched, "matched_acceptable": {},
                      "missing_required": [] if found else ["sql-injection"],
                      "unexpected": []},
        }

    def test_majority_detection_passes(self):
        details = [self._detail(), self._detail(), self._detail(found=False)]
        r = aggregate_detection_trials(details, self.KEY)
        assert r.passed, r.failures

    def test_minority_detection_fails(self):
        details = [self._detail(found=False), self._detail(found=False), self._detail()]
        r = aggregate_detection_trials(details, self.KEY)
        assert not r.passed
        assert any("1/3" in f for f in r.failures)

    def test_compliance_must_hold_in_majority(self):
        details = [self._detail(compliant=False), self._detail(compliant=False), self._detail()]
        r = aggregate_detection_trials(details, self.KEY)
        assert not r.passed
        assert any("compliance" in f for f in r.failures)

    def test_not_applicable_majority(self):
        key = {"expect_not_applicable": True}
        na = {"verdict": "not_applicable", "compliance_passed": True, "match": None, "issue_count": 0}
        wrong = {"verdict": "comment", "compliance_passed": True, "match": None}
        assert aggregate_detection_trials([na, na, wrong], key).passed
        assert not aggregate_detection_trials([na, wrong, wrong], key).passed

    def test_abstention_with_findings_fails_across_trials(self):
        key = {"expect_not_applicable": True}
        clean_trial = {
            "verdict": "not_applicable", "compliance_passed": True,
            "match": None, "issue_count": 0,
        }
        dirty_trial = {
            "verdict": "not_applicable", "compliance_passed": True,
            "match": None, "issue_count": 1,
        }
        details = [dirty_trial, dirty_trial, clean_trial]
        r = aggregate_detection_trials(details, key)
        assert not r.passed
        assert any("zero-findings" in f for f in r.failures)

    def test_severity_gate_votes_across_trials(self):
        key = {"verdict_in": ["approve"], "max_severity": "low"}
        good = self._detail(verdict="approve", gates={"max_severity": True})
        bad = self._detail(verdict="approve", gates={"max_severity": False})
        assert aggregate_detection_trials([good, good, bad], key).passed
        r = aggregate_detection_trials([good, bad, bad], key)
        assert not r.passed
        assert any("max_severity" in f for f in r.failures)

    def test_unreadable_trial_counts_against_every_check(self):
        details = [self._detail(), None, None]
        r = aggregate_detection_trials(details, self.KEY)
        assert not r.passed


class TestReviewRoundHardening:
    """Behaviors added by the 2026-08-06 independent review round."""

    def test_paths_match_normalizes_real_world_variants(self):
        from helpers.graders import _paths_match
        spec = "src/UserHandler.php"
        assert _paths_match("/tmp/eval-x/src/UserHandler.php", spec)  # absolute
        assert _paths_match("b/src/UserHandler.php", spec)            # diff prefix
        assert _paths_match("src\\UserHandler.php", spec)             # backslash
        assert _paths_match("src//UserHandler.php", spec)             # double slash
        assert _paths_match("./src/./UserHandler.php", spec)          # dot segments
        assert not _paths_match("src/Other.php", spec)
        assert not _paths_match("vendor/src/OtherHandler.php", spec)
        # Suffix matching must respect segment boundaries.
        assert not _paths_match("notsrc/UserHandler.php", "rc/UserHandler.php")

    def test_unknown_severity_fails_max_severity_gate(self):
        from helpers.graders import grade_detection
        key = {"max_severity": "low"}
        review = {"verdict": "approve",
                  "issues": [{"severity": "blocker", "file": "f", "title": "t",
                              "description": "", "category": ""}]}
        result = grade_detection(review, key)
        assert not result.passed
        assert result.detail["gates"]["max_severity"] is False

    def test_missing_severity_fails_max_severity_gate(self):
        from helpers.graders import grade_detection
        key = {"max_severity": "low"}
        review = {"verdict": "approve",
                  "issues": [{"file": "f", "title": "t", "description": "", "category": ""}]}
        assert not grade_detection(review, key).passed

    def test_abstention_accepts_both_doctrine_readings(self):
        from helpers.graders import grade_detection
        key = {"expect_not_applicable": True}
        for verdict in ("not_applicable", "approve"):
            review = {"verdict": verdict, "issues": []}
            assert grade_detection(review, key).passed, verdict
        assert not grade_detection({"verdict": "comment", "issues": []}, key).passed
        assert not grade_detection(
            {"verdict": "approve", "issues": [{"severity": "low", "file": "f",
                                              "title": "t", "description": "", "category": ""}]},
            key,
        ).passed

    def test_patterns_cannot_bridge_field_boundaries(self):
        from helpers.graders import _finding_matches
        spec = {"file": "f.php", "match_any": [r"sql\s*inject"]}
        issue = {"file": "f.php", "title": "Uses raw SQL",
                 "description": "injection unrelated word appears here",
                 "category": ""}
        assert not _finding_matches(issue, spec)
        issue["description"] = "clear SQL injection via concatenation"
        assert _finding_matches(issue, spec)

    def test_unexpected_title_is_truncated(self):
        from helpers.graders import match_findings
        issues = [{"file": "g.php", "line": 1, "severity": "low",
                   "category": "c", "title": "x" * 5000, "description": "d"}]
        u = match_findings(issues, {"required_findings": []})["unexpected"][0]
        assert len(u["title"]) == 300

    def test_min_severity_floor_rejects_underclassified_findings(self):
        from helpers.graders import _finding_matches
        spec = {"file": "f.php", "min_severity": "critical",
                "match_any": [r"sql\s*inject"]}
        issue = {"file": "f.php", "title": "SQL injection", "description": "",
                 "category": "", "severity": "high"}
        assert not _finding_matches(issue, spec)
        issue["severity"] = "critical"
        assert _finding_matches(issue, spec)
        # Unknown severity fails the floor closed.
        issue["severity"] = "blocker"
        assert not _finding_matches(issue, spec)

    def test_reviewer_identity_mismatch_fails_json_grade(self, tmp_dir):
        # A valid artifact at the expected path but labeled as another
        # reviewer must not pass compliance under the wrong identity.
        path = _make_valid_json(tmp_dir, reviewer="security")
        assert grade_review_json(path, expected_reviewer="security").passed
        mismatch = grade_review_json(path, expected_reviewer="performance")
        assert not mismatch.passed
        assert any("does not match expected" in f for f in mismatch.failures)
        # Omitting the expectation keeps prior behavior.
        assert grade_review_json(path).passed

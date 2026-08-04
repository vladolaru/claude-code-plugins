"""
Code-based grading functions for review output files.

No model calls. Used by both manual validation and the agent compliance eval runner.
Follows Anthropic eval guidance: deterministic, objective, grades outcomes not paths.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class GradeResult:
    """Result of grading a review output."""

    passed: bool
    score: float  # 0.0-1.0
    failures: list = field(default_factory=list)  # description of each failure
    checks_run: int = 0
    checks_passed: int = 0


VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
VALID_VERDICTS = {"approve", "block", "request_changes", "comment", "not_applicable"}
REQUIRED_ISSUE_FIELDS = {"id", "severity", "title", "file", "description", "recommendation"}
REQUIRED_JSON_TOP_FIELDS = {"pr_id", "reviewer", "verdict", "summary", "issues", "meta"}


def _grade(checks: List[tuple]) -> GradeResult:
    """Run a list of (condition, failure_message) checks and return a GradeResult."""
    failures = []
    passed_count = 0
    for condition, msg in checks:
        if condition:
            passed_count += 1
        else:
            failures.append(msg)
    total = len(checks)
    return GradeResult(
        passed=len(failures) == 0,
        score=passed_count / total if total > 0 else 0.0,
        failures=failures,
        checks_run=total,
        checks_passed=passed_count,
    )


def grade_review_json(path: str) -> GradeResult:
    """Grade a reviewer JSON output file.

    Checks: file exists, valid JSON, required fields, valid severities,
    valid verdict, issue schema, summary structure.
    """
    checks = []

    # Check file exists
    exists = os.path.isfile(path)
    checks.append((exists, f"File does not exist: {path}"))
    if not exists:
        return _grade(checks)

    # Check valid JSON
    data = None
    try:
        with open(path) as f:
            data = json.load(f)
        checks.append((True, ""))
    except (json.JSONDecodeError, OSError) as e:
        checks.append((False, f"Invalid JSON: {e}"))
        return _grade(checks)

    # Check required top-level fields
    for field_name in REQUIRED_JSON_TOP_FIELDS:
        checks.append(
            (field_name in data, f"Missing required field: {field_name}")
        )

    # Check verdict is valid
    verdict = data.get("verdict", "")
    checks.append(
        (verdict in VALID_VERDICTS, f"Invalid verdict: '{verdict}'. Expected one of {VALID_VERDICTS}")
    )

    # Check summary structure
    summary = data.get("summary", {})
    checks.append(
        ("total_issues" in summary, "Missing summary.total_issues")
    )
    checks.append(
        ("by_severity" in summary, "Missing summary.by_severity")
    )

    # Check issues array
    issues = data.get("issues", None)
    checks.append(
        (isinstance(issues, list), f"'issues' is not a list: {type(issues)}")
    )

    if isinstance(issues, list):
        # Check each issue has required fields
        for i, issue in enumerate(issues):
            missing = REQUIRED_ISSUE_FIELDS - set(issue.keys())
            checks.append(
                (len(missing) == 0, f"Issue {i} missing fields: {missing}")
            )
            # Check severity is valid
            sev = issue.get("severity", "")
            checks.append(
                (sev in VALID_SEVERITIES, f"Issue {i} invalid severity: '{sev}'")
            )
            floor = issue.get("severity_floor")
            if floor is not None:
                floor_is_valid = (
                    isinstance(floor, str) and floor in VALID_SEVERITIES
                )
                checks.append(
                    (
                        floor_is_valid,
                        f"Issue {i} invalid severity_floor: '{floor}'",
                    )
                )
                if floor_is_valid and sev in VALID_SEVERITIES:
                    checks.append(
                        (
                            SEVERITY_RANK[sev] >= SEVERITY_RANK[floor],
                            f"Issue {i} severity '{sev}' is below floor '{floor}'",
                        )
                    )

    # Check meta exists
    meta = data.get("meta", None)
    checks.append(
        (isinstance(meta, dict), f"'meta' is not a dict: {type(meta)}")
    )

    return _grade(checks)


def grade_review_markdown(path: str) -> GradeResult:
    """Grade a reviewer markdown output file.

    Checks: file exists, has review header, has executive summary, has verdict.
    """
    checks = []

    exists = os.path.isfile(path)
    checks.append((exists, f"File does not exist: {path}"))
    if not exists:
        return _grade(checks)

    try:
        with open(path) as f:
            content = f.read()
    except OSError as e:
        checks.append((False, f"Cannot read file: {e}"))
        return _grade(checks)

    checks.append((len(content) > 0, "File is empty"))
    checks.append(
        (content.startswith("# ") and "Review" in content.split("\n")[0],
         "Missing '# ... Review' header")
    )
    checks.append(
        ("## Executive Summary" in content, "Missing '## Executive Summary'")
    )
    checks.append(
        ("**Verdict:**" in content, "Missing '**Verdict:**'")
    )

    return _grade(checks)


def grade_signal_format(text: str) -> GradeResult:
    """Grade a return signal text.

    Checks: STATUS: FINISHED, OUTPUT_FILES:, COUNTS:, VERDICT:, SUMMARY:.
    """
    checks = [
        ("STATUS: FINISHED" in text, "Missing 'STATUS: FINISHED'"),
        ("OUTPUT_FILES:" in text, "Missing 'OUTPUT_FILES:'"),
        ("COUNTS:" in text, "Missing 'COUNTS:'"),
        ("VERDICT:" in text, "Missing 'VERDICT:'"),
        ("SUMMARY:" in text, "Missing 'SUMMARY:'"),
    ]
    return _grade(checks)


def grade_no_domain_files(text: str) -> GradeResult:
    """Grade agent output for NO_DOMAIN_FILES scenario.

    Checks: APPROVE verdict, zero findings.
    """
    text_upper = text.upper()
    # A severity mention only counts as a finding when it is followed by
    # something other than a zero count or the literal "N" placeholder from
    # the bootstrap's return-signal template ("COUNTS: critical: N, ...").
    finding_mention = re.search(r"(CRITICAL|HIGH|MEDIUM):\s*(?!0\b|N\b)\S", text_upper)
    checks = [
        ("APPROVE" in text_upper, "Missing APPROVE verdict"),
        (
            finding_mention is None,
            "Expected zero findings but found severity mentions"
        ),
    ]
    return _grade(checks)


def grade_error_exit(text: str) -> GradeResult:
    """Grade agent output for ERROR scenario.

    Checks: contains error report, no review findings, no output files written.
    """
    text_upper = text.upper()
    # Match only an actual return signal (column 0). Bootstrap output embeds
    # an indented "  STATUS: FINISHED" line inside its return-signal template,
    # which is instructional text, not a claim of completion.
    finished_signal = re.search(r"^STATUS: FINISHED", text, re.MULTILINE)
    checks = [
        ("ERROR" in text_upper, "Missing error indication"),
        (
            finished_signal is None,
            "Should NOT have STATUS: FINISHED in error scenario"
        ),
    ]
    return _grade(checks)


def grade_output_pair(output_dir: str, reviewer_name: str) -> GradeResult:
    """Grade both .json and .md output files for a reviewer.

    Checks: both files exist, delegates to grade_review_json + grade_review_markdown,
    reviewer name in JSON matches expected.
    """
    json_path = os.path.join(output_dir, f"{reviewer_name}-review.json")
    md_path = os.path.join(output_dir, f"{reviewer_name}-review.md")

    # Collect all checks from sub-graders
    json_result = grade_review_json(json_path)
    md_result = grade_review_markdown(md_path)

    all_failures = json_result.failures + md_result.failures
    total_checks = json_result.checks_run + md_result.checks_run
    total_passed = json_result.checks_passed + md_result.checks_passed

    # Additional check: reviewer name matches
    reviewer_match = False
    if os.path.isfile(json_path):
        try:
            with open(json_path) as f:
                data = json.load(f)
            reviewer_match = data.get("reviewer") == reviewer_name
        except (json.JSONDecodeError, OSError):
            pass

    if not reviewer_match and os.path.isfile(json_path):
        all_failures.append(
            f"Reviewer name in JSON does not match expected '{reviewer_name}'"
        )
    elif os.path.isfile(json_path):
        total_passed += 1
    total_checks += 1

    return GradeResult(
        passed=len(all_failures) == 0,
        score=total_passed / total_checks if total_checks > 0 else 0.0,
        failures=all_failures,
        checks_run=total_checks,
        checks_passed=total_passed,
    )


REQUIRED_BASELINE_FIELDS = {
    "last_reviewed_sha",
    "last_reviewed_at",
    "review_type",
    "review_count",
    "base_ref",
    "git_range_used",
}

SHA_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")


def grade_review_baseline(path: str) -> GradeResult:
    """Grade a .branch-review-baseline.json file.

    Checks: file exists, valid JSON, required fields (including review_type),
    SHA format, review_count is positive int, git_range_used contains '..'.
    """
    checks = []

    exists = os.path.isfile(path)
    checks.append((exists, f"File does not exist: {path}"))
    if not exists:
        return _grade(checks)

    data = None
    try:
        with open(path) as f:
            data = json.load(f)
        checks.append((True, ""))
    except (json.JSONDecodeError, OSError) as e:
        checks.append((False, f"Invalid JSON: {e}"))
        return _grade(checks)

    checks.append(
        (isinstance(data, dict), f"Top-level value is not a dict: {type(data)}")
    )
    if not isinstance(data, dict):
        return _grade(checks)

    # Required fields
    for field_name in REQUIRED_BASELINE_FIELDS:
        checks.append(
            (field_name in data, f"Missing required field: {field_name}")
        )

    # SHA format
    sha = data.get("last_reviewed_sha", "")
    checks.append(
        (isinstance(sha, str) and bool(SHA_PATTERN.match(sha)),
         f"Invalid SHA format: '{sha}' (expected 7-40 hex chars)")
    )

    # review_count is positive int
    count = data.get("review_count", None)
    checks.append(
        (isinstance(count, int) and count > 0,
         f"review_count must be a positive int, got: {count!r}")
    )

    # git_range_used contains '..'
    git_range = data.get("git_range_used", "")
    checks.append(
        (isinstance(git_range, str) and ".." in git_range,
         f"git_range_used must contain '..', got: '{git_range}'")
    )

    return _grade(checks)


# =============================================================================
# Detection grading (answer-key based)
# =============================================================================
#
# Answer keys assert which planted defects a reviewer must find. Matching is
# deterministic: repo-relative file path, optional line window, and
# case-insensitive keyword regexes over title + description + category.
# One issue can satisfy at most one key spec (claimed-set), so keys must not
# split a plausibly-merged finding across two required specs. Keys must also
# not write overlapping specs — match_any patterns for specs targeting the
# same file should be mutually exclusive, because first-match claiming is
# order-dependent and can under-match overlapping specs.

DEFAULT_LINE_TOLERANCE = 2


def _norm_path(path) -> str:
    text = str(path or "")
    return text[2:] if text.startswith("./") else text


def _issue_text(issue: dict) -> str:
    return " ".join(str(issue.get(k) or "") for k in ("title", "description", "category"))


def _finding_matches(issue: dict, spec: dict) -> bool:
    if _norm_path(issue.get("file")) != _norm_path(spec["file"]):
        return False
    expected_line = spec.get("line")
    if expected_line is not None:
        line = issue.get("line")
        if not isinstance(line, int) or isinstance(line, bool):
            return False
        if abs(line - expected_line) > spec.get("line_tolerance", DEFAULT_LINE_TOLERANCE):
            return False
    text = _issue_text(issue)
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in spec["match_any"])


def match_findings(issues: List[dict], key: dict) -> dict:
    """Match reviewer issues against an answer key.

    Returns a dict with:
      matched_required:   {spec_id: issue_index}
      matched_acceptable: {spec_id: issue_index}
      missing_required:   [spec_id, ...]
      unexpected:         [issue_index, ...]  (issues no spec claimed)
    """
    issues = [i for i in issues if isinstance(i, dict)]
    matched_required: dict = {}
    matched_acceptable: dict = {}
    claimed: set = set()

    for bucket, matched in (
        ("required_findings", matched_required),
        ("acceptable_findings", matched_acceptable),
    ):
        for spec in key.get(bucket, []):
            for idx, issue in enumerate(issues):
                if idx in claimed:
                    continue
                if _finding_matches(issue, spec):
                    matched[spec["id"]] = idx
                    claimed.add(idx)
                    break

    missing = [
        spec["id"] for spec in key.get("required_findings", [])
        if spec["id"] not in matched_required
    ]
    unexpected = [idx for idx in range(len(issues)) if idx not in claimed]
    return {
        "matched_required": matched_required,
        "matched_acceptable": matched_acceptable,
        "missing_required": missing,
        "unexpected": unexpected,
    }

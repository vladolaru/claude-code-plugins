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
    detail: Optional[dict] = None  # grader-specific detail payload (detection match, trial aggregation)


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

# Verdicts accepted as correct abstention on a NO_DOMAIN_FILES scenario.
# The shared reviewer protocol mandates not_applicable; the tests-reviewer
# agent definitions mandate approve — a live doctrine conflict inside the
# plugin. Keys accept both compliant readings; the conflict itself is a
# production-definition fix, not a benchmark one.
_ABSTENTION_VERDICTS = frozenset({"not_applicable", "approve"})


def _norm_path(path) -> str:
    """Normalize a reviewer-reported path for comparison against a spec path.

    Reviewers are not contractually bound to repo-relative POSIX paths, and
    absolute tempdir paths / backslashes / diff-style a|b prefixes occur in
    practice (scripts/review/telemetry.py normalizes the same variants). A
    correct finding must not score as a miss over path spelling.
    """
    text = str(path or "").replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if parts and parts[0] in ("a", "b") and len(parts) > 1:
        parts = parts[1:]
    return "/".join(parts)


def _paths_match(issue_path, spec_path: str) -> bool:
    """Exact match after normalization, or suffix match for absolute paths.

    An issue reported as /tmp/eval-x/src/File.php cannot be reduced to
    repo-relative without knowing the repo root, so a normalized path that
    ENDS with the spec path (on a segment boundary) also matches.
    """
    issue_norm = _norm_path(issue_path)
    spec_norm = _norm_path(spec_path)
    if not issue_norm or not spec_norm:
        return False
    return issue_norm == spec_norm or issue_norm.endswith("/" + spec_norm)


# Field separator that \s-bridging regexes cannot cross — a plain space would
# let a pattern like r"sql\s*inject" match "…raw SQL" + "injection …" across
# the title/description boundary.
_FIELD_SEP = " ¦ "


def _issue_text(issue: dict) -> str:
    return _FIELD_SEP.join(
        str(issue.get(k) or "") for k in ("title", "description", "category")
    )


def _finding_matches(issue: dict, spec: dict) -> bool:
    if not _paths_match(issue.get("file"), spec["file"]):
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
      unexpected:         [{index, file, line, severity, category, title,
                            description}, ...]  (issues no spec claimed)

    Unexpected entries carry the fields match_any patterns grep over
    (title/description/category, description truncated) plus location —
    without them a correct finding that misses every pattern is
    undiagnosable from the report, and the documented widen-the-regex
    workflow needs to see what the reviewer actually wrote.
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
    unexpected = [
        {
            "index": idx,
            "file": issues[idx].get("file"),
            "line": issues[idx].get("line"),
            "severity": issues[idx].get("severity"),
            "category": issues[idx].get("category"),
            "title": str(issues[idx].get("title") or "")[:300],
            "description": str(issues[idx].get("description") or "")[:300],
        }
        for idx in range(len(issues))
        if idx not in claimed
    ]
    return {
        "matched_required": matched_required,
        "matched_acceptable": matched_acceptable,
        "missing_required": missing,
        "unexpected": unexpected,
    }


def grade_detection(review: dict, key: dict) -> GradeResult:
    """Grade a parsed review JSON against a scenario answer key.

    Key fields (all optional except that at least one gate must be present —
    enforced by tests/grading/test_answer_keys.py):
      verdict_in:            list of acceptable verdict strings
      required_findings:     specs the reviewer MUST report (recall gate)
      acceptable_findings:   legitimate secondary findings (never punished)
      max_severity:          highest allowed severity for ANY finding
      max_unexpected:        cap on findings no spec claimed
      expect_not_applicable: the correct answer is abstention
    """
    verdict = review.get("verdict")
    issues = [i for i in (review.get("issues") or []) if isinstance(i, dict)]

    if key.get("expect_not_applicable"):
        # Both abstention spellings are doctrine-compliant: the shared
        # reviewer protocol mandates mark_not_applicable on NO_DOMAIN_FILES,
        # while the tests-reviewer agent definitions instruct APPROVE on the
        # same status. Until that conflict is reconciled in the definitions,
        # punishing either reading would grade an internal doc inconsistency,
        # not reviewer quality. The zero-findings requirement carries the
        # actual behavioral content.
        result = _grade([
            (verdict in _ABSTENTION_VERDICTS,
             f"expected abstention ({'/'.join(sorted(_ABSTENTION_VERDICTS))}), got '{verdict}'"),
            (len(issues) == 0,
             f"expected zero findings on abstention, got {len(issues)}"),
        ])
        result.detail = {"verdict": verdict, "match": None, "issue_count": len(issues)}
        return result

    match = match_findings(issues, key)
    checks = []

    verdict_in = key.get("verdict_in")
    if verdict_in:
        checks.append((verdict in verdict_in, f"verdict '{verdict}' not in {verdict_in}"))

    for spec in key.get("required_findings", []):
        checks.append((
            spec["id"] in match["matched_required"],
            f"required finding not detected: {spec['id']}",
        ))

    gates = {}

    max_severity = key.get("max_severity")
    if max_severity is not None:
        limit = SEVERITY_RANK[max_severity]
        # Unknown severities fail closed: ranking them as info would let an
        # issue with severity "blocker" (or a missing field) sail under any
        # cap, and this gate is the sole check the false-positive probes
        # rely on.
        over = sorted({
            str(i.get("severity")) for i in issues
            if i.get("severity") not in SEVERITY_RANK
            or SEVERITY_RANK[i.get("severity")] > limit
        })
        checks.append((not over, f"findings above max severity '{max_severity}': {over}"))
        gates["max_severity"] = not over

    max_unexpected = key.get("max_unexpected")
    if max_unexpected is not None:
        within = len(match["unexpected"]) <= max_unexpected
        checks.append((
            within,
            f"{len(match['unexpected'])} unexpected findings exceed cap {max_unexpected}",
        ))
        gates["max_unexpected"] = within

    result = _grade(checks)
    result.detail = {"verdict": verdict, "match": match, "gates": gates}
    return result


def merge_grades(compliance: GradeResult, detection: GradeResult) -> GradeResult:
    """Combine two grades (e.g. compliance + detection) into one result.

    Precedence: detection.detail wins when not None, else compliance.detail.
    """
    total = compliance.checks_run + detection.checks_run
    passed_count = compliance.checks_passed + detection.checks_passed
    return GradeResult(
        passed=compliance.passed and detection.passed,
        score=passed_count / total if total else 0.0,
        failures=compliance.failures + detection.failures,
        checks_run=total,
        checks_passed=passed_count,
        detail=detection.detail if detection.detail is not None else compliance.detail,
    )


def aggregate_detection_trials(details: List[dict], key: dict) -> GradeResult:
    """Majority vote across trial details (GradeResult.detail dicts).

    A trial with a None/empty detail counts as a miss on every check —
    an unreadable trial must never improve the aggregate. With an even
    trial count the majority threshold requires strictly more than half,
    so --trials 2 demands both trials pass every check.
    """
    details = [d or {} for d in details]
    trials = len(details)
    need = trials // 2 + 1
    checks = []

    compliant = sum(1 for d in details if d.get("compliance_passed"))
    checks.append((
        compliant >= need,
        f"output-contract compliance held in only {compliant}/{trials} trials",
    ))

    if key.get("expect_not_applicable"):
        abstained = sum(1 for d in details if d.get("verdict") in _ABSTENTION_VERDICTS)
        checks.append((
            abstained >= need,
            f"abstention verdict in only {abstained}/{trials} trials",
        ))
        clean = sum(1 for d in details if d.get("issue_count") == 0)
        checks.append((
            clean >= need,
            f"zero-findings abstention in only {clean}/{trials} trials",
        ))
    else:
        verdict_in = key.get("verdict_in")
        if verdict_in:
            acceptable = sum(1 for d in details if d.get("verdict") in verdict_in)
            checks.append((
                acceptable >= need,
                f"verdict in {verdict_in} in only {acceptable}/{trials} trials",
            ))
        for spec in key.get("required_findings", []):
            hits = sum(
                1 for d in details
                if spec["id"] in ((d.get("match") or {}).get("matched_required") or {})
            )
            checks.append((
                hits >= need,
                f"required finding '{spec['id']}' detected in only {hits}/{trials} trials",
            ))
        for gate in ("max_severity", "max_unexpected"):
            if key.get(gate) is not None:
                held = sum(1 for d in details if (d.get("gates") or {}).get(gate))
                checks.append((
                    held >= need,
                    f"{gate} gate held in only {held}/{trials} trials",
                ))

    result = _grade(checks)
    result.detail = {"trials": trials, "per_trial": details}
    return result

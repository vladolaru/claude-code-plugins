"""
Code-based grading functions for review output files.

No model calls. Used by both manual validation and the agent compliance eval runner.
Follows Anthropic eval guidance: deterministic, objective, grades outcomes not paths.
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
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
REQUIRED_FINDING_FIELDS = {
    "id",
    "category",
    "severity",
    "title",
    "description",
    "file",
    "line",
    "recommendation",
    "confidence",
}
REQUIRED_CHECK_FIELDS = {"id", "question", "method", "result", "source_reviewers"}
REQUIRED_JSON_TOP_FIELDS = {
    "pr_id",
    "reviewer",
    "schema",
    "verdict",
    "summary",
    "findings",
    "checks",
    "assessment",
    "review_claimable_files",
    "reviewed_file_claims",
    "unclaimed_review_files",
    "inline_diff_file_count",
    "review_accounted_file_count",
    "in_scope_review_file_count",
    "meta",
}


def _validate_review_domain(findings, checks, assessment, meta):
    """Reuse the production review-domain boundary from the eval harness."""
    scripts_dir = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from review.agent.output import validate_review_domain

    validate_review_domain(findings, checks, assessment, meta)


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


def grade_review_json(path: str, expected_reviewer: str = None) -> GradeResult:
    """Grade a reviewer JSON output file.

    Checks: file exists, valid JSON, required fields, valid severities,
    valid verdict, finding/check schemas, accounting, summary structure. When expected_reviewer
    is given, the JSON's reviewer field must match it — a valid artifact at
    the expected path but labeled as ANOTHER reviewer must not pass
    compliance and proceed to detection scoring under the wrong identity.
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

    if expected_reviewer is not None:
        checks.append((
            data.get("reviewer") == expected_reviewer,
            f"Reviewer name {data.get('reviewer')!r} does not match "
            f"expected '{expected_reviewer}'",
        ))

    # Check verdict is valid
    verdict = data.get("verdict", "")
    checks.append(
        (verdict in VALID_VERDICTS, f"Invalid verdict: '{verdict}'. Expected one of {VALID_VERDICTS}")
    )
    checks.append((data.get("schema") == 2, "Review schema must be 2"))

    # Check summary structure
    summary = data.get("summary", {})
    checks.append(
        ("total_findings" in summary, "Missing summary.total_findings")
    )
    checks.append(
        ("by_severity" in summary, "Missing summary.by_severity")
    )

    # Check findings array
    findings = data.get("findings", None)
    checks.append(
        (isinstance(findings, list), f"'findings' is not a list: {type(findings)}")
    )

    if isinstance(findings, list):
        # Check each finding has required fields
        for i, finding in enumerate(findings):
            missing = REQUIRED_FINDING_FIELDS - set(finding.keys())
            checks.append(
                (len(missing) == 0, f"Finding {i} missing fields: {missing}")
            )
            # Check severity is valid
            sev = finding.get("severity", "")
            checks.append(
                (sev in VALID_SEVERITIES, f"Finding {i} invalid severity: '{sev}'")
            )
            floor = finding.get("severity_floor")
            if floor is not None:
                floor_is_valid = (
                    isinstance(floor, str) and floor in VALID_SEVERITIES
                )
                checks.append(
                    (
                        floor_is_valid,
                        f"Finding {i} invalid severity_floor: '{floor}'",
                    )
                )
                if floor_is_valid and sev in VALID_SEVERITIES:
                    checks.append(
                        (
                            SEVERITY_RANK[sev] >= SEVERITY_RANK[floor],
                            f"Finding {i} severity '{sev}' is below floor '{floor}'",
                        )
                    )
        checks.append((
            summary.get("total_findings") == len(findings),
            "summary.total_findings does not match findings",
        ))

    review_checks = data.get("checks")
    checks.append((
        isinstance(review_checks, list),
        f"'checks' is not a list: {type(review_checks)}",
    ))
    if isinstance(review_checks, list):
        for index, review_check in enumerate(review_checks):
            is_object = isinstance(review_check, dict)
            checks.append((is_object, f"Check {index} is not an object"))
            if not is_object:
                continue
            checks.append((
                set(review_check) == REQUIRED_CHECK_FIELDS,
                f"Check {index} must contain exactly {REQUIRED_CHECK_FIELDS}",
            ))
            for field_name in ("question", "method", "result"):
                value = review_check.get(field_name)
                checks.append((
                    isinstance(value, str) and bool(value.strip()),
                    f"Check {index}.{field_name} must be a non-empty string",
                ))
            sources = review_check.get("source_reviewers")
            checks.append((
                isinstance(sources, list)
                and bool(sources)
                and all(isinstance(source, str) and source.strip() for source in sources)
                and len(sources) == len(set(sources)),
                f"Check {index}.source_reviewers must be unique non-empty strings",
            ))

    assessment = data.get("assessment")
    checks.append((
        assessment is None or isinstance(assessment, str),
        "assessment must be a string or null",
    ))

    claimable = data.get("review_claimable_files")
    claimed = data.get("reviewed_file_claims")
    unclaimed = data.get("unclaimed_review_files")
    for field_name, value in (
        ("review_claimable_files", claimable),
        ("reviewed_file_claims", claimed),
        ("unclaimed_review_files", unclaimed),
    ):
        checks.append((
            isinstance(value, list)
            and all(isinstance(path, str) for path in value)
            and len(value) == len(set(value)),
            f"{field_name} must be a unique string-only list",
        ))
    if all(isinstance(value, list) for value in (claimable, claimed, unclaimed)):
        checks.append((
            set(claimed).isdisjoint(unclaimed)
            and set(claimed) | set(unclaimed) == set(claimable),
            "reviewed and unclaimed files must partition review_claimable_files",
        ))

    count_fields = (
        "inline_diff_file_count",
        "review_accounted_file_count",
        "in_scope_review_file_count",
    )
    for field_name in count_fields:
        value = data.get(field_name)
        checks.append((
            type(value) is int and value >= 0,
            f"{field_name} must be a non-negative integer",
        ))
    inline_count = data.get("inline_diff_file_count")
    accounted_count = data.get("review_accounted_file_count")
    in_scope_count = data.get("in_scope_review_file_count")
    if (
        all(type(value) is int for value in (inline_count, accounted_count, in_scope_count))
        and isinstance(claimable, list)
        and isinstance(claimed, list)
    ):
        checks.append((
            inline_count + len(claimable) == in_scope_count,
            "in-scope count does not match inline and review-claimable files",
        ))
        checks.append((
            inline_count + len(claimed) == accounted_count,
            "accounted count does not match inline and reviewed-file claims",
        ))

    for retired_key in ("issues", "clearances", "narrative_summary"):
        checks.append((
            retired_key not in data,
            f"Retired review-domain field is present: {retired_key}",
        ))

    # Check meta exists
    meta = data.get("meta", None)
    checks.append(
        (isinstance(meta, dict), f"'meta' is not a dict: {type(meta)}")
    )
    if isinstance(meta, dict):
        for field_name in ("next_finding_number", "next_check_number"):
            value = meta.get(field_name)
            checks.append((
                type(value) is int and value > 0,
                f"meta.{field_name} must be a positive integer",
            ))
        checks.append((
            "tool_results_used" not in meta,
            "Retired review-domain field is present: meta.tool_results_used",
        ))

    try:
        _validate_review_domain(findings, review_checks, assessment, meta)
    except ValueError as error:
        checks.append((False, str(error)))
    else:
        checks.append((True, ""))

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
# One finding can satisfy at most one key spec (claimed-set), so keys must not
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


def _repo_relative_issue_path(path, repo_root) -> str:
    """Normalize one reported path relative to the known repository root."""
    text = str(path or "")
    normalized = _norm_path(text)
    is_absolute = (
        PurePosixPath(text.replace("\\", "/")).is_absolute()
        or PureWindowsPath(text).is_absolute()
    )
    if not is_absolute:
        return normalized

    root = _norm_path(repo_root)
    prefix = f"{root}/" if root else ""
    if prefix and normalized.startswith(prefix):
        return normalized[len(prefix):]
    return normalized


def _paths_match(issue_path, spec_path: str) -> bool:
    """Match canonical paths exactly after spelling normalization."""
    issue_norm = _norm_path(issue_path)
    spec_norm = _norm_path(spec_path)
    return bool(issue_norm and spec_norm and issue_norm == spec_norm)


# Field separator that \s-bridging regexes cannot cross — a plain space would
# let a pattern like r"sql\s*inject" match "…raw SQL" + "injection …" across
# the title/description boundary.
_FIELD_SEP = " ¦ "


def _issue_text(finding: dict) -> str:
    return _FIELD_SEP.join(
        str(finding.get(k) or "") for k in ("title", "description", "category")
    )


def _finding_matches(finding: dict, spec: dict) -> bool:
    if not _paths_match(finding.get("file"), spec["file"]):
        return False
    # Severity floor: when the agent's doctrine mandates a classification
    # (e.g. SQL injection = CRITICAL for security-reviewer), an
    # under-classified finding is a calibration miss and must not satisfy
    # the spec. Unknown severities fail closed.
    min_severity = spec.get("min_severity")
    if min_severity is not None:
        severity = finding.get("severity")
        if severity not in SEVERITY_RANK or (
            SEVERITY_RANK[severity] < SEVERITY_RANK[min_severity]
        ):
            return False
    expected_line = spec.get("line")
    if expected_line is not None:
        line = finding.get("line")
        if not isinstance(line, int) or isinstance(line, bool):
            return False
        if abs(line - expected_line) > spec.get("line_tolerance", DEFAULT_LINE_TOLERANCE):
            return False
    text = _issue_text(finding)
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in spec["match_any"])


def match_findings(findings: List[dict], key: dict) -> dict:
    """Match reviewer findings against an answer key.

    Returns a dict with:
      matched_required:   {spec_id: issue_index}
      matched_acceptable: {spec_id: issue_index}
      missing_required:   [spec_id, ...]
      unexpected:         [{index, file, line, severity, category, title,
                            description}, ...]  (findings no spec claimed)

    Unexpected entries carry the fields match_any patterns grep over
    (title/description/category, description truncated) plus location —
    without them a correct finding that misses every pattern is
    undiagnosable from the report, and the documented widen-the-regex
    workflow needs to see what the reviewer actually wrote.
    """
    findings = [i for i in findings if isinstance(i, dict)]
    matched_required: dict = {}
    matched_acceptable: dict = {}
    claimed: set = set()

    for bucket, matched in (
        ("required_findings", matched_required),
        ("acceptable_findings", matched_acceptable),
    ):
        for spec in key.get(bucket, []):
            for idx, finding in enumerate(findings):
                if idx in claimed:
                    continue
                if _finding_matches(finding, spec):
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
            "file": findings[idx].get("file"),
            "line": findings[idx].get("line"),
            "severity": findings[idx].get("severity"),
            "category": findings[idx].get("category"),
            "title": str(findings[idx].get("title") or "")[:300],
            "description": str(findings[idx].get("description") or "")[:300],
        }
        for idx in range(len(findings))
        if idx not in claimed
    ]
    return {
        "matched_required": matched_required,
        "matched_acceptable": matched_acceptable,
        "missing_required": missing,
        "unexpected": unexpected,
    }


def grade_detection(review: dict, key: dict, repo_root=None) -> GradeResult:
    """Grade a parsed review JSON against a scenario answer key.

    Key fields (all optional except that at least one gate must be present —
    enforced by tests/grading/test_answer_keys.py):
      verdict_in:            list of acceptable verdict strings
      required_findings:     specs the reviewer MUST report (recall gate)
      acceptable_findings:   legitimate secondary findings (never punished)
      max_severity:          highest allowed severity for ANY finding
      max_unexpected:        cap on findings no spec claimed
      expect_not_applicable: the correct answer is abstention

    When repo_root is known, absolute finding paths are canonicalized against it
    once before matching. The matcher itself compares repository-relative
    identities exactly and never infers identity from a path suffix.
    """
    verdict = review.get("verdict")
    findings = [i for i in (review.get("findings") or []) if isinstance(i, dict)]
    if repo_root is not None:
        findings = [
            dict(
                finding,
                file=_repo_relative_issue_path(finding.get("file"), repo_root),
            )
            for finding in findings
        ]

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
            (len(findings) == 0,
             f"expected zero findings on abstention, got {len(findings)}"),
        ])
        result.detail = {"verdict": verdict, "match": None, "issue_count": len(findings)}
        return result

    match = match_findings(findings, key)
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
        # finding with severity "blocker" (or a missing field) sail under any
        # cap, and this gate is the sole check the false-positive probes
        # rely on.
        over = sorted({
            str(i.get("severity")) for i in findings
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


def merge_grades(
    compliance: GradeResult,
    detection: GradeResult,
    detection_label: Optional[str] = None,
) -> GradeResult:
    """Combine two grades (e.g. compliance + detection) into one result.

    Precedence: detection.detail wins when not None, else compliance.detail.
    When detection_label is given, detection failures are prefixed
    "<label>: " so the merged list stays attributable to its source grader.
    """
    detection_failures = detection.failures
    if detection_label is not None:
        detection_failures = [
            f"{detection_label}: {msg}" for msg in detection.failures
        ]
    total = compliance.checks_run + detection.checks_run
    passed_count = compliance.checks_passed + detection.checks_passed
    return GradeResult(
        passed=compliance.passed and detection.passed,
        score=passed_count / total if total else 0.0,
        failures=compliance.failures + detection_failures,
        checks_run=total,
        checks_passed=passed_count,
        detail=detection.detail if detection.detail is not None else compliance.detail,
    )


def aggregate_detection_trials(trial_grades: List[GradeResult]) -> GradeResult:
    """Aggregate multi-trial dispatches: a strict majority of trials must
    pass outright.

    Per-check majority votes were removed (2026-08-09): a majority of
    outright-passing trials implies a per-check majority for every check —
    those same trials passed each one — so per-check votes could never be
    the sole failure and only duplicated the diagnostics per_trial_failures
    already carries in full. With an even trial count the threshold is
    strictly more than half, so --trials 2 demands both trials pass. A
    trial with an unreadable/None detail is simply a failed trial —
    unreadable evidence never improves the aggregate.

    The detail payload is the full aggregate schema consumers rely on:
    {trials, per_trial, per_trial_failures, per_trial_passed,
    per_trial_status, models} — per-trial diagnostics live here, in the
    aggregate itself, so every caller gets the same evidence shape.
    """
    trials = len(trial_grades)
    need = trials // 2 + 1
    passing = sum(1 for grade in trial_grades if grade.passed)
    result = _grade([
        (passing >= need,
         f"only {passing}/{trials} trials passed outright (need {need})"),
    ])
    if not result.passed:
        # Carry trial-indexed diagnostics in failures — the console prints
        # failures only, so without this a failed --trials run without
        # --report-out would say how many trials failed but never why.
        for idx, grade in enumerate(trial_grades):
            for msg in grade.failures[:3] if not grade.passed else []:
                result.failures.append(f"trial {idx + 1}: {msg}")
    result.detail = {
        "trials": trials,
        "per_trial": [grade.detail or {} for grade in trial_grades],
        "per_trial_failures": [grade.failures for grade in trial_grades],
        "per_trial_passed": [grade.passed for grade in trial_grades],
        "per_trial_status": [
            (grade.detail or {}).get("status", "harness_error")
            for grade in trial_grades
        ],
        "models": sorted({
            m for grade in trial_grades
            for m in ((grade.detail or {}).get("models") or [])
        }),
    }
    return result

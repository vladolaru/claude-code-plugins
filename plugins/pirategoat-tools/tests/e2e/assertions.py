"""Assertion helpers for e2e pipeline tests.

Each function returns an AssertionResult (not raises), so the
StreamMonitor can collect all failures and report them at the end.
"""

import json
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class AssertionResult:
    """Result of a single assertion check."""

    passed: bool
    name: str
    reason: str = ""


# Alias for backward compat with design doc.
AssertionFailure = AssertionResult


def assert_file_exists(path: str, name: str = "") -> AssertionResult:
    """Check that a file exists at the given path."""
    label = name or os.path.basename(path)
    if os.path.isfile(path):
        return AssertionResult(passed=True, name=f"file_exists_{label}")
    return AssertionResult(
        passed=False,
        name=f"file_exists_{label}",
        reason=f"File not found: {path}",
    )


def assert_valid_json(path: str, name: str = "") -> AssertionResult:
    """Check that a file contains valid JSON."""
    label = name or os.path.basename(path)
    if not os.path.isfile(path):
        return AssertionResult(
            passed=False, name=f"valid_json_{label}",
            reason=f"File not found: {path}",
        )
    try:
        with open(path) as f:
            json.load(f)
        return AssertionResult(passed=True, name=f"valid_json_{label}")
    except (json.JSONDecodeError, OSError) as e:
        return AssertionResult(
            passed=False, name=f"valid_json_{label}",
            reason=f"Invalid JSON in {path}: {e}",
        )


def _load_json(path: str) -> tuple[Optional[dict], Optional[str]]:
    """Load JSON from path. Returns (data, error_message)."""
    if not os.path.isfile(path):
        return None, f"File not found: {path}"
    try:
        with open(path) as f:
            return json.load(f), None
    except (json.JSONDecodeError, OSError) as e:
        return None, f"Failed to load {path}: {e}"


def _get_nested(data: dict, dotted_path: str):
    """Get a value from a nested dict using dotted path (e.g., 'git.base_ref')."""
    keys = dotted_path.split(".")
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def assert_context_schema(path: str) -> AssertionResult:
    """Check that review-context.json has the required schema."""
    data, err = _load_json(path)
    if err:
        return AssertionResult(passed=False, name="context_schema", reason=err)

    required_paths = [
        "version",
        "github_cli_command",
        "git.merge_base",
        "git.git_range",
        "git.head_ref",
        "git.base_ref",
        "git.changed_files",
        "output.directory",
    ]

    missing = [p for p in required_paths if _get_nested(data, p) is None]
    if missing:
        return AssertionResult(
            passed=False, name="context_schema",
            reason=f"Missing required fields: {', '.join(missing)}",
        )
    return AssertionResult(passed=True, name="context_schema")


def assert_context_field(
    path: str, field_path: str, expected_value: str
) -> AssertionResult:
    """Check that a specific field in review-context.json matches."""
    data, err = _load_json(path)
    if err:
        return AssertionResult(
            passed=False, name=f"context_field_{field_path}", reason=err,
        )

    actual = _get_nested(data, field_path)
    if actual is None:
        return AssertionResult(
            passed=False, name=f"context_field_{field_path}",
            reason=f"Field '{field_path}' not found in context file",
        )
    if str(actual) != str(expected_value):
        return AssertionResult(
            passed=False, name=f"context_field_{field_path}",
            reason=f"Expected {field_path}='{expected_value}', got '{actual}'",
        )
    return AssertionResult(passed=True, name=f"context_field_{field_path}")


def assert_dispatch_plan(
    path: str,
    must_dispatch: Optional[list[str]] = None,
    min_dispatched: int = 0,
    max_dispatched: Optional[int] = None,
) -> AssertionResult:
    """Check the dispatch plan has expected agents."""
    data, err = _load_json(path)
    if err:
        return AssertionResult(passed=False, name="dispatch_plan", reason=err)

    agents = data.get("agents", [])
    dispatched = [a for a in agents if a.get("status") == "DISPATCH"]
    dispatched_names = {a["name"] for a in dispatched}

    failures = []

    if len(dispatched) < min_dispatched:
        failures.append(
            f"Expected >= {min_dispatched} dispatched, got {len(dispatched)}"
        )

    if max_dispatched is not None and len(dispatched) > max_dispatched:
        failures.append(
            f"Expected <= {max_dispatched} dispatched, got {len(dispatched)}"
        )

    if must_dispatch:
        missing = set(must_dispatch) - dispatched_names
        if missing:
            failures.append(f"Expected agents not dispatched: {', '.join(sorted(missing))}")

    if failures:
        return AssertionResult(
            passed=False, name="dispatch_plan",
            reason="; ".join(failures),
        )
    return AssertionResult(passed=True, name="dispatch_plan")


def assert_findings_severity(
    path: str,
    min_critical: int = 0,
    max_critical: Optional[int] = None,
    min_important: int = 0,
    max_important: Optional[int] = None,
) -> AssertionResult:
    """Check finding severity counts in reconciled-structured.json."""
    data, err = _load_json(path)
    if err:
        return AssertionResult(passed=False, name="findings_severity", reason=err)

    clusters = data.get("clusters", data.get("issues", []))
    counts = {}
    for c in clusters:
        sev = c.get("severity", "medium").lower()
        counts[sev] = counts.get(sev, 0) + 1

    critical = counts.get("critical", 0)
    important = counts.get("high", 0) + counts.get("important", 0)

    failures = []
    if critical < min_critical:
        failures.append(f"Expected >= {min_critical} critical, got {critical}")
    if max_critical is not None and critical > max_critical:
        failures.append(f"Expected <= {max_critical} critical, got {critical}")
    if important < min_important:
        failures.append(f"Expected >= {min_important} important, got {important}")
    if max_important is not None and important > max_important:
        failures.append(f"Expected <= {max_important} important, got {important}")

    if failures:
        return AssertionResult(
            passed=False, name="findings_severity",
            reason=f"{'; '.join(failures)}. Counts: {counts}",
        )
    return AssertionResult(passed=True, name="findings_severity")


def assert_final_state(output_dir: str, expectations) -> list[AssertionResult]:
    """Run all post-run assertions for a PR. Returns list of results."""
    results = []
    ctx_path = os.path.join(output_dir, "review-context.json")

    # Required files.
    results.append(assert_file_exists(ctx_path, "review-context.json"))
    results.append(assert_valid_json(ctx_path, "review-context.json"))
    results.append(assert_context_schema(ctx_path))

    dispatch_path = os.path.join(output_dir, "dispatch-plan.json")
    results.append(assert_file_exists(dispatch_path, "dispatch-plan.json"))

    reconciled_path = os.path.join(output_dir, "reconciled-structured.json")
    results.append(assert_file_exists(reconciled_path, "reconciled-structured.json"))

    report_path = os.path.join(output_dir, "review-report.md")
    results.append(assert_file_exists(report_path, "review-report.md"))

    # Context field assertions.
    for field_path, expected in expectations.context_assertions.items():
        results.append(assert_context_field(ctx_path, field_path, expected))

    # Dispatch plan assertions.
    if os.path.isfile(dispatch_path):
        results.append(assert_dispatch_plan(
            dispatch_path,
            must_dispatch=expectations.must_dispatch or None,
            min_dispatched=expectations.min_dispatched_agents,
            max_dispatched=expectations.max_dispatched_agents,
        ))

    # Finding severity assertions.
    if os.path.isfile(reconciled_path):
        results.append(assert_findings_severity(
            reconciled_path,
            min_critical=expectations.min_critical_findings,
            max_critical=expectations.max_critical_findings,
            min_important=expectations.min_important_findings,
            max_important=expectations.max_important_findings,
        ))

    # Changed files count.
    if expectations.max_changed_files is not None and os.path.isfile(ctx_path):
        data, _ = _load_json(ctx_path)
        if data:
            files = _get_nested(data, "git.changed_files") or []
            if len(files) > expectations.max_changed_files:
                results.append(AssertionResult(
                    passed=False, name="max_changed_files",
                    reason=f"Expected <= {expectations.max_changed_files} files, got {len(files)}",
                ))
            else:
                results.append(AssertionResult(passed=True, name="max_changed_files"))

    return results

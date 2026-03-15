"""Checkpoint builders — construct step-level checkpoints from PRExpectations.

Each function builds a Checkpoint with a trigger (step number or agent name)
and an assertion (file system check). The StreamMonitor fires these as the
pipeline progresses.
"""

import os

from assertions import (
    AssertionResult,
    assert_file_exists,
    assert_context_schema,
    assert_context_field,
    assert_dispatch_plan,
    assert_findings_severity,
    assert_valid_json,
)
from stream_monitor import Checkpoint, CheckpointResult
from expectations import PRExpectations


def build_checkpoints(
    expectations: PRExpectations, output_dir: str
) -> list[Checkpoint]:
    """Build all checkpoints for a PR based on its expectations."""
    cps = []

    # Step 2 completes -> Step 3 starts: review-context.json exists.
    cps.append(Checkpoint(
        name="step_2_context_file",
        trigger_step=3,
        timeout_seconds=30,
        assertion=lambda od: _to_checkpoint_result(
            assert_file_exists(os.path.join(od, "review-context.json"), "review-context.json"),
        ),
    ))

    cps.append(Checkpoint(
        name="step_2_context_schema",
        trigger_step=3,
        timeout_seconds=5,
        assertion=lambda od: _to_checkpoint_result(
            assert_context_schema(os.path.join(od, "review-context.json")),
        ),
    ))

    # Context field assertions (e.g., PR4: base_ref == "release/v1").
    for field_path, expected in expectations.context_assertions.items():
        cps.append(Checkpoint(
            name=f"step_2_context_{field_path.replace('.', '_')}",
            trigger_step=3,
            timeout_seconds=5,
            assertion=_make_context_field_check(
                os.path.join(output_dir, "review-context.json"),
                field_path,
                expected,
            ),
        ))

    # Step 10 completes -> Step 11 starts: dispatch-plan.json exists.
    cps.append(Checkpoint(
        name="step_10_dispatch_plan",
        trigger_step=11,
        timeout_seconds=30,
        assertion=lambda od: _to_checkpoint_result(
            assert_file_exists(os.path.join(od, "dispatch-plan.json"), "dispatch-plan.json"),
        ),
    ))

    cps.append(Checkpoint(
        name="step_10_dispatch_agents",
        trigger_step=11,
        timeout_seconds=5,
        assertion=lambda od: _to_checkpoint_result(
            assert_dispatch_plan(
                os.path.join(od, "dispatch-plan.json"),
                must_dispatch=expectations.must_dispatch or None,
                min_dispatched=expectations.min_dispatched_agents,
                max_dispatched=expectations.max_dispatched_agents,
            ),
        ),
    ))

    # Agent .started markers — one checkpoint per must_dispatch agent.
    for agent in expectations.must_dispatch:
        cps.append(Checkpoint(
            name=f"agent_started_{agent}",
            trigger_agent=agent,
            timeout_seconds=30,
            assertion=_make_started_check(agent),
        ))

    # Step 12 completes -> Step 13 starts: reconciled output exists.
    cps.append(Checkpoint(
        name="step_12_reconciled",
        trigger_step=13,
        timeout_seconds=120,
        assertion=lambda od: _to_checkpoint_result(
            assert_file_exists(
                os.path.join(od, "reconciled-structured.json"),
                "reconciled-structured.json",
            ),
        ),
    ))

    # Step 13 completes -> Step 14 starts: review-report.md exists.
    cps.append(Checkpoint(
        name="step_13_review_report",
        trigger_step=14,
        timeout_seconds=120,
        assertion=lambda od: _to_checkpoint_result(
            assert_file_exists(
                os.path.join(od, "review-report.md"),
                "review-report.md",
            ),
        ),
    ))

    return cps


def _to_checkpoint_result(ar: AssertionResult) -> CheckpointResult:
    """Convert an AssertionResult to a CheckpointResult."""
    return CheckpointResult(
        name=ar.name,
        passed=ar.passed,
        reason=ar.reason,
    )


def _make_started_check(agent: str):
    """Return an assertion function that checks for {agent}.started."""
    def check(output_dir: str) -> CheckpointResult:
        path = os.path.join(output_dir, f"{agent}.started")
        if os.path.isfile(path):
            return CheckpointResult(name=f"started_{agent}", passed=True)
        return CheckpointResult(
            name=f"started_{agent}",
            passed=False,
            reason=f"{agent}.started not found in {output_dir}",
        )
    return check


def _make_context_field_check(ctx_path: str, field_path: str, expected: str):
    """Return an assertion function for a context field value."""
    def check(output_dir: str) -> CheckpointResult:
        result = assert_context_field(ctx_path, field_path, expected)
        return CheckpointResult(
            name=f"context_{field_path}",
            passed=result.passed,
            reason=result.reason,
        )
    return check

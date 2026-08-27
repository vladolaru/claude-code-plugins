#!/usr/bin/env python3
"""Reviewer-specific draft, final, sidecar, and intake state."""

from dataclasses import dataclass
import json
import os
import shlex
from datetime import datetime, timezone

try:
    from .atomic_io import atomic_write_json, output_dir_lock
    from .reviewer_names import derive_reviewer_name
except ImportError:
    from review.atomic_io import atomic_write_json, output_dir_lock
    from review.reviewer_names import derive_reviewer_name


REVIEW_INTAKE_NAME = "review-intake.json"
FINALIZE_REVIEW_COMMAND = (
    "python3 {output_script} finalize-review "
    "--output-dir {output_dir} --reviewer {reviewer} "
    "--review-digest {review_digest}"
)


def finalize_review_command(
    output_script: str, output_dir: str, reviewer: str, review_digest: str
) -> str:
    """Render the one digest-bound finalization command."""
    return FINALIZE_REVIEW_COMMAND.format(
        output_script=shlex.quote(output_script),
        output_dir=shlex.quote(output_dir),
        reviewer=shlex.quote(reviewer),
        review_digest=review_digest,
    )


@dataclass(frozen=True)
class ReviewPaths:
    draft: str
    final: str
    assignment: str


def review_paths(output_dir: str, reviewer: str) -> ReviewPaths:
    """Return the three lifecycle paths for one safe reviewer identity."""
    if (
        not isinstance(reviewer, str)
        or not reviewer
        or reviewer in {".", ".."}
        or "/" in reviewer
        or "\\" in reviewer
        or "\x00" in reviewer
    ):
        raise ValueError(f"invalid reviewer identity: {reviewer!r}")
    stem = os.path.join(output_dir, f"{reviewer}-review")
    return ReviewPaths(
        draft=f"{stem}.draft.json",
        final=f"{stem}.json",
        assignment=os.path.join(output_dir, f"{reviewer}-assignment.json"),
    )


def require_review_intake_open(output_dir: str) -> None:
    """Reject reviewer state transitions after synthesis freezes intake."""
    intake_path = os.path.join(output_dir, REVIEW_INTAKE_NAME)
    if os.path.exists(intake_path):
        raise ValueError("review intake is closed")


def require_not_finalized(paths: ReviewPaths) -> None:
    """Reject a mutable draft save once final JSON exists."""
    if os.path.exists(paths.final):
        raise ValueError(
            f"reviewer {os.path.basename(paths.final)!r} is already finalized"
        )


def _load_closed_intake(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            intake = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed closed review intake") from exc
    if (
        not isinstance(intake, dict)
        or type(intake.get("schema")) is not int
        or intake["schema"] != 2
        or intake.get("status") != "closed"
        or not isinstance(intake.get("closed_at"), str)
        or not isinstance(intake.get("discarded_drafts"), list)
        or not all(
            isinstance(name, str) and name
            for name in intake["discarded_drafts"]
        )
    ):
        raise ValueError("malformed closed review intake")
    return intake


def _repair_finalized_completion(output_dir: str, reviewer: str) -> None:
    """Reach output.py lazily, after its lifecycle imports are settled."""
    try:
        from .agent.output import repair_finalized_completion
    except ImportError:
        from review.agent.output import repair_finalized_completion
    repair_finalized_completion(output_dir, reviewer)


def close_review_intake(output_dir: str, dispatched_agent_names):
    """Freeze reviewer inputs and discard only dispatched drafts.

    The closed marker is written before completion repair and cleanup. If
    cleanup is interrupted, ordinary save/finalize remains rejected and a
    repeated close resumes from the recorded discard set. Invalid finals
    are terminal inputs rather than an interruption: the returned runtime
    result classifies BOTH halves — `completed` beside
    `invalid_final_reviews` — while the persisted intake marker stays
    schema 2.
    """
    if not isinstance(dispatched_agent_names, (list, tuple)):
        raise ValueError("dispatched reviewer identities must be a list")
    if not all(
        isinstance(name, str) and name for name in dispatched_agent_names
    ):
        raise ValueError("dispatched reviewer identities must be non-empty strings")

    intake_path = os.path.join(output_dir, REVIEW_INTAKE_NAME)
    with output_dir_lock(output_dir):
        previous = _load_closed_intake(intake_path)
        known_identities = set(dispatched_agent_names)
        if previous is not None:
            known_identities.update(previous["discarded_drafts"])

        recognized = []
        discarded = set(
            previous["discarded_drafts"] if previous is not None else []
        )
        for agent_name in sorted(known_identities):
            reviewer = derive_reviewer_name(agent_name)
            paths = review_paths(output_dir, reviewer)
            recognized.append((agent_name, reviewer, paths))
            if os.path.isfile(paths.draft):
                discarded.add(agent_name)

        intake = {
            "schema": 2,
            "status": "closed",
            "closed_at": (
                previous["closed_at"]
                if previous is not None
                else datetime.now(timezone.utc).isoformat()
            ),
            "discarded_drafts": sorted(discarded),
        }
        atomic_write_json(intake_path, intake)

        # Canonical JSON is the only completion source at close. Repair is
        # deliberately after the closed marker so an interrupted operation
        # cannot reopen the ordinary finalization channel. Every final is
        # opened and validated exactly here; the classification is returned
        # so step 8 does not open and validate the same files again to
        # learn what this loop already knows.
        classified_reviewers = set()
        completed = []
        invalid_final_reviews = []
        for agent_name, reviewer, paths in recognized:
            if (
                os.path.isfile(paths.final)
                and reviewer not in classified_reviewers
            ):
                classified_reviewers.add(reviewer)
                try:
                    _repair_finalized_completion(output_dir, reviewer)
                except ValueError as error:
                    invalid_final_reviews.append({
                        "agent_name": agent_name,
                        "reviewer": reviewer,
                        "path": paths.final,
                        "error": str(error),
                    })
                else:
                    completed.append(agent_name)

        deleted_paths = set()
        for _agent_name, _reviewer, paths in recognized:
            if paths.draft in deleted_paths:
                continue
            try:
                os.unlink(paths.draft)
            except FileNotFoundError:
                pass
            else:
                deleted_paths.add(paths.draft)

    return {
        **intake,
        "completed": completed,
        "invalid_final_reviews": invalid_final_reviews,
    }

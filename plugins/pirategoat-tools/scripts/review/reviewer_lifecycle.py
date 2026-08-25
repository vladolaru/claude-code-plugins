#!/usr/bin/env python3
"""Reviewer-specific candidate, canonical, sidecar, and intake state."""

from dataclasses import dataclass
import json
import os
from datetime import datetime, timezone

try:
    from .atomic_io import atomic_write_json, output_dir_lock
    from .reviewer_names import derive_reviewer_name
except ImportError:
    from review.atomic_io import atomic_write_json, output_dir_lock
    from review.reviewer_names import derive_reviewer_name


REVIEW_INTAKE_NAME = "review-intake.json"


@dataclass(frozen=True)
class ReviewerPaths:
    candidate: str
    canonical: str
    accounting_input: str


def reviewer_paths(output_dir: str, reviewer: str) -> ReviewerPaths:
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
    return ReviewerPaths(
        candidate=f"{stem}.candidate.json",
        canonical=f"{stem}.json",
        accounting_input=os.path.join(
            output_dir, f"{reviewer}-review-accounting-input.json"
        ),
    )


def require_review_intake_open(output_dir: str) -> None:
    """Reject reviewer state transitions after synthesis freezes intake."""
    intake_path = os.path.join(output_dir, REVIEW_INTAKE_NAME)
    if os.path.exists(intake_path):
        raise ValueError("review intake is closed")


def require_not_finalized(paths: ReviewerPaths) -> None:
    """Reject a mutable candidate save once canonical JSON exists."""
    if os.path.exists(paths.canonical):
        raise ValueError(
            f"reviewer {os.path.basename(paths.canonical)!r} is already finalized"
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
        or intake["schema"] != 1
        or intake.get("status") != "closed"
        or not isinstance(intake.get("closed_at"), str)
        or not isinstance(intake.get("discarded_candidates"), list)
        or not all(
            isinstance(name, str) and name
            for name in intake["discarded_candidates"]
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


def close_review_intake(output_dir: str, dispatched_reviewers):
    """Freeze reviewer inputs and discard only dispatched candidates.

    The closed marker is written before completion repair and cleanup. If
    either later operation is interrupted, ordinary save/finalize remains
    rejected and a repeated close resumes from the recorded discard set.
    """
    if not isinstance(dispatched_reviewers, (list, tuple)):
        raise ValueError("dispatched reviewer identities must be a list")
    if not all(isinstance(name, str) and name for name in dispatched_reviewers):
        raise ValueError("dispatched reviewer identities must be non-empty strings")

    intake_path = os.path.join(output_dir, REVIEW_INTAKE_NAME)
    with output_dir_lock(output_dir):
        previous = _load_closed_intake(intake_path)
        known_identities = set(dispatched_reviewers)
        if previous is not None:
            known_identities.update(previous["discarded_candidates"])

        recognized = []
        discarded = set(
            previous["discarded_candidates"] if previous is not None else []
        )
        for agent_name in sorted(known_identities):
            reviewer = derive_reviewer_name(agent_name)
            paths = reviewer_paths(output_dir, reviewer)
            recognized.append((agent_name, reviewer, paths))
            if os.path.isfile(paths.candidate):
                discarded.add(agent_name)

        intake = {
            "schema": 1,
            "status": "closed",
            "closed_at": (
                previous["closed_at"]
                if previous is not None
                else datetime.now(timezone.utc).isoformat()
            ),
            "discarded_candidates": sorted(discarded),
        }
        atomic_write_json(intake_path, intake)

        # Canonical JSON is the only completion source at close. Repair is
        # deliberately after the closed marker so an interrupted operation
        # cannot reopen the ordinary finalization channel.
        repaired_reviewers = set()
        for _agent_name, reviewer, paths in recognized:
            if os.path.isfile(paths.canonical) and reviewer not in repaired_reviewers:
                _repair_finalized_completion(output_dir, reviewer)
                repaired_reviewers.add(reviewer)

        deleted_paths = set()
        for _agent_name, _reviewer, paths in recognized:
            if paths.candidate in deleted_paths:
                continue
            try:
                os.unlink(paths.candidate)
            except FileNotFoundError:
                pass
            else:
                deleted_paths.add(paths.candidate)

    return intake

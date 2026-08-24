#!/usr/bin/env python3
"""Reviewer-specific candidate, canonical, sidecar, and intake state."""

from dataclasses import dataclass
import os


REVIEW_INTAKE_NAME = "review-intake.json"


@dataclass(frozen=True)
class ReviewerPaths:
    candidate: str
    canonical: str
    sidecar: str


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
        sidecar=os.path.join(output_dir, f"{reviewer}-deferred-files.json"),
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

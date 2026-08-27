"""The reviewer assignment and the reviewed files derived from it."""

from dataclasses import dataclass
import posixpath
from typing import Iterable, Mapping

try:
    from ..reviewer_names import derive_reviewer_name
except ImportError:
    from review.reviewer_names import derive_reviewer_name


class ReviewAssignmentError(ValueError):
    """A review assignment or reviewed-file claim is invalid."""


@dataclass(frozen=True)
class ReviewedFiles:
    agent_name: str
    reviewer: str
    review_claimable_files: tuple[str, ...]
    reviewed_file_claims: tuple[str, ...]
    unclaimed_review_files: tuple[str, ...]
    inline_diff_file_count: int
    reviewed_file_count: int
    in_scope_review_file_count: int
    review_budget: int
    channels: tuple[str, ...]


ASSIGNMENT_SCHEMA = 4
REVIEW_CHANNELS = ("blocking", "advisory")


def normalize_review_path(path: object, api_name: str) -> str:
    """Normalize one repository-relative path in the review-path grammar."""
    if not isinstance(path, str) or not path.strip():
        raise ReviewAssignmentError(f"{api_name} requires a non-empty file path")
    raw = path.strip().replace("\\", "/")
    segments = raw.split("/")
    normalized = posixpath.normpath(raw)
    if (
        raw.startswith("/")
        or normalized in {".", ".."}
        or ".." in segments
        or normalized.startswith("../")
        or (len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha())
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ReviewAssignmentError(
            f"{api_name} requires a repository-relative path, got {path!r}"
        )
    return normalized


def _validated_count(assignment: Mapping[str, object], key: str) -> int:
    value = assignment.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewAssignmentError(f"assignment {key} must be a non-negative integer")
    return value


def _validated_assignment(
    assignment: Mapping[str, object],
) -> tuple[str, str, tuple[str, ...], int, int, int, tuple[str, ...]]:
    if assignment.get("schema") != ASSIGNMENT_SCHEMA:
        raise ReviewAssignmentError(
            f"assignment schema must be {ASSIGNMENT_SCHEMA}"
        )

    agent_name = assignment.get("agent_name")
    if not isinstance(agent_name, str) or not agent_name.strip():
        raise ReviewAssignmentError("assignment agent_name must be a non-empty string")
    reviewer = assignment.get("reviewer")
    if (
        not isinstance(reviewer, str)
        or not reviewer.strip()
        or derive_reviewer_name(agent_name) != reviewer
    ):
        raise ReviewAssignmentError("assignment reviewer identity does not match agent_name")

    raw_paths = assignment.get("review_claimable_files")
    if not isinstance(raw_paths, list) or not all(
        isinstance(path, str) for path in raw_paths
    ):
        raise ReviewAssignmentError(
            "assignment review_claimable_files must be a string-only list"
        )
    paths = tuple(
        normalize_review_path(path, "assignment review_claimable_files")
        for path in raw_paths
    )
    if len(set(paths)) != len(paths):
        raise ReviewAssignmentError(
            "assignment review_claimable_files must not contain duplicates"
        )

    inline_count = _validated_count(assignment, "inline_diff_file_count")
    in_scope_count = _validated_count(
        assignment, "in_scope_review_file_count"
    )
    review_budget = _validated_count(assignment, "review_budget")
    channels = assignment.get("channels")
    if (
        not isinstance(channels, list)
        or not channels
        or len(channels) != len(set(channels))
        or any(channel not in REVIEW_CHANNELS for channel in channels)
    ):
        raise ReviewAssignmentError(
            "assignment channels must be a non-empty list of unique values "
            f"from {REVIEW_CHANNELS}"
        )
    channels = tuple(channels)
    if inline_count + len(paths) != in_scope_count:
        raise ReviewAssignmentError("incoherent inline and review-claimable scope counts")
    return agent_name, reviewer, paths, inline_count, in_scope_count, review_budget, channels


def _validated_claim_set(
    reviewed_file_claims: Iterable[str], review_claimable_files: tuple[str, ...]
) -> frozenset[str]:
    if isinstance(reviewed_file_claims, (str, bytes)):
        raise ReviewAssignmentError("reviewed-file claims must be iterable paths")
    try:
        claimed = frozenset(
            normalize_review_path(path, "reviewed-file claim")
            for path in reviewed_file_claims
        )
    except TypeError as exc:
        raise ReviewAssignmentError("reviewed-file claims must be iterable") from exc
    unknown = sorted(claimed - set(review_claimable_files))
    if unknown:
        raise ReviewAssignmentError(
            "reviewed-file claims include paths that are not review-claimable: "
            + ", ".join(unknown)
        )
    return claimed


def derive_reviewed_files(
    assignment: Mapping[str, object],
    reviewed_file_claims: Iterable[str],
    *,
    reviewer: str,
) -> ReviewedFiles:
    """Derive the complete reviewed-file partition from one assignment.

    `reviewer` is the identity the caller is acting as; an assignment bound
    to any other reviewer is refused here, at the one authority, so a stale
    or misplaced sidecar cannot lend another reviewer its scope, budget, or
    channels.
    """
    if not isinstance(assignment, Mapping):
        raise ReviewAssignmentError("assignment must be an object")
    (
        agent_name,
        assigned_reviewer,
        review_claimable_files,
        inline_diff_file_count,
        in_scope_review_file_count,
        review_budget,
        channels,
    ) = _validated_assignment(assignment)
    if assigned_reviewer != reviewer:
        raise ReviewAssignmentError(
            f"assignment is bound to reviewer {assigned_reviewer!r}, "
            f"not {reviewer!r}"
        )
    claimed = _validated_claim_set(reviewed_file_claims, review_claimable_files)
    reviewed = tuple(path for path in review_claimable_files if path in claimed)
    unclaimed = tuple(path for path in review_claimable_files if path not in claimed)
    return ReviewedFiles(
        agent_name=agent_name,
        reviewer=assigned_reviewer,
        review_claimable_files=review_claimable_files,
        reviewed_file_claims=reviewed,
        unclaimed_review_files=unclaimed,
        inline_diff_file_count=inline_diff_file_count,
        reviewed_file_count=inline_diff_file_count + len(reviewed),
        in_scope_review_file_count=in_scope_review_file_count,
        review_budget=review_budget,
        channels=channels,
    )

"""Authoritative reviewed-file accounting for reviewer artifacts."""

from dataclasses import dataclass
import posixpath
from typing import Iterable, Mapping

try:
    from ..reviewer_names import derive_reviewer_name
except ImportError:
    from review.reviewer_names import derive_reviewer_name


class ReviewAccountingError(ValueError):
    """A review-accounting input or reviewed-file claim is invalid."""


@dataclass(frozen=True)
class ReviewAccounting:
    agent_name: str
    reviewer: str
    review_claimable_files: tuple[str, ...]
    reviewed_file_claims: tuple[str, ...]
    unclaimed_review_files: tuple[str, ...]
    inline_diff_file_count: int
    review_accounted_file_count: int
    in_scope_review_file_count: int


def normalize_review_path(path: object, api_name: str) -> str:
    """Normalize one repository-relative path in the accounting grammar."""
    if not isinstance(path, str) or not path.strip():
        raise ReviewAccountingError(f"{api_name} requires a non-empty file path")
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
        raise ReviewAccountingError(
            f"{api_name} requires a repository-relative path, got {path!r}"
        )
    return normalized


def _validated_count(accounting_input: Mapping[str, object], key: str) -> int:
    value = accounting_input.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewAccountingError(f"accounting input {key} must be a non-negative integer")
    return value


def _validated_accounting_input(
    accounting_input: Mapping[str, object],
) -> tuple[str, str, tuple[str, ...], int, int]:
    if accounting_input.get("schema") != 3:
        raise ReviewAccountingError("accounting input schema must be 3")

    agent_name = accounting_input.get("agent_name")
    if not isinstance(agent_name, str) or not agent_name.strip():
        raise ReviewAccountingError("accounting input agent_name must be a non-empty string")
    reviewer = accounting_input.get("reviewer")
    if (
        not isinstance(reviewer, str)
        or not reviewer.strip()
        or derive_reviewer_name(agent_name) != reviewer
    ):
        raise ReviewAccountingError("accounting input reviewer identity does not match agent_name")

    raw_paths = accounting_input.get("review_claimable_files")
    if not isinstance(raw_paths, list) or not all(
        isinstance(path, str) for path in raw_paths
    ):
        raise ReviewAccountingError(
            "accounting input review_claimable_files must be a string-only list"
        )
    paths = tuple(
        normalize_review_path(path, "accounting input review_claimable_files")
        for path in raw_paths
    )
    if len(set(paths)) != len(paths):
        raise ReviewAccountingError(
            "accounting input review_claimable_files must not contain duplicates"
        )

    inline_count = _validated_count(accounting_input, "inline_diff_file_count")
    in_scope_count = _validated_count(
        accounting_input, "in_scope_review_file_count"
    )
    _validated_count(accounting_input, "review_budget")
    if inline_count + len(paths) != in_scope_count:
        raise ReviewAccountingError("incoherent inline and review-claimable scope counts")
    return agent_name, reviewer, paths, inline_count, in_scope_count


def _validated_claim_set(
    reviewed_file_claims: Iterable[str], review_claimable_files: tuple[str, ...]
) -> frozenset[str]:
    if isinstance(reviewed_file_claims, (str, bytes)):
        raise ReviewAccountingError("reviewed-file claims must be iterable paths")
    try:
        claimed = frozenset(
            normalize_review_path(path, "reviewed-file claim")
            for path in reviewed_file_claims
        )
    except TypeError as exc:
        raise ReviewAccountingError("reviewed-file claims must be iterable") from exc
    unknown = sorted(claimed - set(review_claimable_files))
    if unknown:
        raise ReviewAccountingError(
            "reviewed-file claims include paths that are not review-claimable: "
            + ", ".join(unknown)
        )
    return claimed


def derive_review_accounting(
    accounting_input: Mapping[str, object],
    reviewed_file_claims: Iterable[str],
) -> ReviewAccounting:
    """Derive the complete reviewed-file accounting partition."""
    if not isinstance(accounting_input, Mapping):
        raise ReviewAccountingError("accounting input must be an object")
    (
        agent_name,
        reviewer,
        review_claimable_files,
        inline_diff_file_count,
        in_scope_review_file_count,
    ) = _validated_accounting_input(accounting_input)
    claimed = _validated_claim_set(reviewed_file_claims, review_claimable_files)
    reviewed = tuple(path for path in review_claimable_files if path in claimed)
    unclaimed = tuple(path for path in review_claimable_files if path not in claimed)
    return ReviewAccounting(
        agent_name=agent_name,
        reviewer=reviewer,
        review_claimable_files=review_claimable_files,
        reviewed_file_claims=reviewed,
        unclaimed_review_files=unclaimed,
        inline_diff_file_count=inline_diff_file_count,
        review_accounted_file_count=inline_diff_file_count + len(reviewed),
        in_scope_review_file_count=in_scope_review_file_count,
    )

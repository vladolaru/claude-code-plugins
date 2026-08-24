"""Authoritative deferred-coverage derivation for reviewer artifacts."""

from dataclasses import dataclass
import posixpath
from typing import Iterable, Mapping


class CoverageError(ValueError):
    """A deferred-coverage sidecar or positive claim is not authoritative."""


@dataclass(frozen=True)
class DeferredCoverage:
    agent_name: str
    deferred_reviewed: tuple[str, ...]
    unreviewed: tuple[str, ...]
    files_reviewed: int
    in_scope_count: int


def normalize_deferred_path(path: object, api_name: str) -> str:
    """Normalize one repository-relative path in the deferred-file grammar."""
    if not isinstance(path, str) or not path.strip():
        raise CoverageError(f"{api_name} requires a non-empty file path.")
    normalized = posixpath.normpath(path.strip().replace("\\", "/"))
    if (
        normalized.startswith("/")
        or normalized in {".", ".."}
        or normalized.startswith("../")
        or (len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha())
    ):
        raise CoverageError(
            f"{api_name} requires a repository-relative path exactly as shown "
            f"in the NOT DIFFED listing, got {path!r}."
        )
    return normalized


def _validated_count(sidecar: Mapping[str, object], key: str) -> int:
    value = sidecar.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoverageError(f"sidecar {key} must be a non-negative integer")
    return value


def _validated_deferred_paths(sidecar: Mapping[str, object]) -> tuple[str, ...]:
    if sidecar.get("schema") != 2:
        raise CoverageError("sidecar schema must be 2")
    agent_name = sidecar.get("agent_name")
    if not isinstance(agent_name, str) or not agent_name.strip():
        raise CoverageError("sidecar agent_name must be a non-empty string")
    raw_paths = sidecar.get("deferred_files")
    if not isinstance(raw_paths, list) or not all(isinstance(path, str) for path in raw_paths):
        raise CoverageError("sidecar deferred_files must be a string-only list")
    paths = tuple(normalize_deferred_path(path, "sidecar deferred_files") for path in raw_paths)
    if len(set(paths)) != len(paths):
        raise CoverageError("sidecar deferred_files must not contain duplicates")
    return paths


def _validated_claim_set(claims: Iterable[str], deferred: tuple[str, ...]) -> frozenset[str]:
    try:
        claimed = frozenset(normalize_deferred_path(path, "positive claim") for path in claims)
    except TypeError as exc:
        raise CoverageError("positive claims must be iterable") from exc
    unknown = sorted(claimed - set(deferred))
    if unknown:
        raise CoverageError(
            "positive claims include paths that are not deferred files: "
            + ", ".join(repr(path) for path in unknown)
        )
    return claimed


def derive_deferred_coverage(
    sidecar: Mapping[str, object], claims: Iterable[str]
) -> DeferredCoverage:
    """Derive the complete deferred coverage partition from positive claims."""
    if not isinstance(sidecar, Mapping):
        raise CoverageError("sidecar must be an object")
    deferred = _validated_deferred_paths(sidecar)
    claimed = _validated_claim_set(claims, deferred)
    diffed_count = _validated_count(sidecar, "diffed_count")
    in_scope_count = _validated_count(sidecar, "in_scope_count")
    if diffed_count + len(deferred) != in_scope_count:
        raise CoverageError("incoherent inline/deferred scope counts")
    return DeferredCoverage(
        agent_name=sidecar["agent_name"],
        deferred_reviewed=tuple(path for path in deferred if path in claimed),
        unreviewed=tuple(path for path in deferred if path not in claimed),
        files_reviewed=diffed_count + len(claimed),
        in_scope_count=in_scope_count,
    )

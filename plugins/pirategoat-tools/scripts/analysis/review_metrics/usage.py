"""Shared token-usage accumulation primitives."""

from __future__ import annotations

from .contracts import _USAGE_FIELDS
from .sanitize import _nonnegative_int


def _empty_usage() -> dict[str, int]:
    return {field: 0 for field in _USAGE_FIELDS}


def _safe_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, int] = {}
    for field in _USAGE_FIELDS:
        count = _nonnegative_int(value.get(field))
        if count is None:
            return None
        result[field] = count
    return result


def _add_usage(target: dict[str, int], value: object) -> bool:
    usage = _safe_usage(value)
    if usage is None:
        return False
    for field in _USAGE_FIELDS:
        target[field] += usage[field]
    return True

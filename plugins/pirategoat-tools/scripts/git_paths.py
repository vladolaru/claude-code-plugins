"""Shared Git path decoding and repository-relative normalization.

Git's ``core.quotePath`` output uses the C-style grammar from ``quote.c``.
This module owns that grammar once, and — on top of it — the single
definition of "one safe POSIX repository-relative path". Every measurement
that compares two path sets has to agree on both, because a set difference
between a `core.quotepath=false` producer and a default-quoting one is
arithmetic on two different alphabets: it silently reports fully-covered
non-ASCII files as never covered. Callers decide how malformed or
non-Unicode paths affect their own trust and availability contracts, via
``strict``.
"""

import posixpath
import re
import unicodedata
from typing import Any, List, Optional, Tuple

from containment import contains_posix_lexically


_GIT_QUOTE_ESCAPES = {
    "a": 0x07,
    "b": 0x08,
    "f": 0x0C,
    "n": 0x0A,
    "r": 0x0D,
    "t": 0x09,
    "v": 0x0B,
    '"': 0x22,
    "\\": 0x5C,
}


def decode_git_c_quoted_path(
    value: str, *, errors: str = "strict"
) -> Tuple[Optional[str], bool]:
    """Decode one whole Git C-quoted path.

    Ordinary input returns ``(value, False)``. Malformed escape-bearing
    wrappers return ``(None, True)`` so callers can apply their own
    fail-closed policy. ``errors`` controls UTF-8 decoding of escaped bytes;
    provenance callers use ``surrogateescape`` to preserve an exact identity.
    """
    if errors not in {"strict", "surrogateescape"}:
        raise ValueError(f"unsupported UTF-8 error policy: {errors}")

    starts_quoted = value.startswith('"')
    ends_quoted = value.endswith('"')
    if not starts_quoted and not ends_quoted:
        return value, False
    if not starts_quoted or not ends_quoted or len(value) < 2:
        return (value, False) if "\\" not in value else (None, True)

    content = value[1:-1]
    if "\\" not in content:
        return value, False

    decoded = bytearray()
    index = 0
    while index < len(content):
        char = content[index]
        if char == '"':
            return None, True
        if char != "\\":
            decoded.extend(char.encode("utf-8", errors="surrogateescape"))
            index += 1
            continue

        if index + 1 >= len(content):
            return None, True
        escape = content[index + 1]
        if escape in _GIT_QUOTE_ESCAPES:
            decoded.append(_GIT_QUOTE_ESCAPES[escape])
            index += 2
            continue

        octal = content[index + 1:index + 4]
        if (
            len(octal) != 3
            or any(digit not in "01234567" for digit in octal)
            or int(octal, 8) > 0xFF
        ):
            return None, True
        decoded.append(int(octal, 8))
        index += 4

    try:
        return decoded.decode("utf-8", errors=errors), True
    except UnicodeDecodeError:
        return None, True


def normalize_repo_path(
    value: Any,
    repo_path: str = "",
    *,
    normalize_backslash_separators: bool = True,
    decode_git_quoted: bool = True,
) -> Optional[str]:
    """Return one safe POSIX repository-relative path, if possible.

    ``decode_git_quoted`` runs the whole value through this module's own
    quote.c grammar under STRICT UTF-8, so an escape-bearing partial or
    malformed wrapper becomes unavailable rather than an invented path.
    """
    if not isinstance(value, str) or not value:
        return None

    if decode_git_quoted:
        decoded, was_git_quoted = decode_git_c_quoted_path(value)
    else:
        decoded, was_git_quoted = value, False
    if decoded is None or not decoded:
        return None
    if any(
        unicodedata.category(char) in {"Cc", "Cf"}
        for char in decoded
    ):
        return None

    candidate = decoded
    if not was_git_quoted and normalize_backslash_separators:
        candidate = candidate.replace("\\", "/")

    if ".." in candidate.split("/"):
        return None
    if not was_git_quoted and re.match(r"^[a-zA-Z]:", decoded):
        return None

    if posixpath.isabs(candidate):
        root = repo_path.replace("\\", "/") if repo_path else ""
        if not posixpath.isabs(root):
            return None
        normalized_root = posixpath.normpath(root)
        normalized_absolute = posixpath.normpath(candidate)
        if not contains_posix_lexically(
            normalized_root, normalized_absolute
        ):
            return None
        candidate = posixpath.relpath(normalized_absolute, normalized_root)

    normalized = posixpath.normpath(candidate)
    if normalized in ("", ".") or posixpath.isabs(normalized):
        return None
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


def normalize_repo_paths(
    value: Any,
    repo_path: str = "",
    *,
    strict: bool = False,
    normalize_backslash_separators: bool = True,
    decode_git_quoted: bool = True,
) -> Optional[List[str]]:
    """Normalize, sort, and deduplicate an allowlisted path list.

    Scope events filter unsafe entries so arbitrary values never persist.
    Authoritative context and plan sets use ``strict=True`` so partial data
    becomes unavailable instead of silently shrinking the measured set.
    """
    if not isinstance(value, list):
        return None if strict else []

    normalized = []
    for item in value:
        path = normalize_repo_path(
            item,
            repo_path=repo_path,
            normalize_backslash_separators=normalize_backslash_separators,
            decode_git_quoted=decode_git_quoted,
        )
        if path is None:
            if strict:
                return None
            continue
        normalized.append(path)
    return sorted(set(normalized))

"""Shared Git path decoding primitives.

Git's ``core.quotePath`` output uses the C-style grammar from ``quote.c``.
This module owns that grammar once; callers decide how malformed or
non-Unicode paths affect their own trust and availability contracts.
"""

from typing import Optional, Tuple


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

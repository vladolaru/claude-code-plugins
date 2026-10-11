"""Which records a list of names means, for every command that takes file names (`apply --only`, `choose`,
`variants`, `record`).

A name is an absolute path, a path relative to the current folder (it contains a slash or names an existing
file there), or a bare file name. A path selects the record whose source is that file. A bare name selects a
file only when exactly one record has that name: names repeat across folders (28 of the 373 images WooCommerce
ships), so an ambiguous name is refused with every match listed instead of being read as all of them.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .ladder import UsageError


def read_text(value: str) -> str:
    """The text of the file ``value``, or stdin when ``value`` is "-"; UsageError when the file cannot be read."""
    if value == "-":
        return sys.stdin.read()
    try:
        return Path(value).read_text()
    except FileNotFoundError:
        raise UsageError(f"{value}: no such file; give the path of the file, or - to read stdin") from None
    except (OSError, UnicodeDecodeError) as error:
        raise UsageError(f"{value}: cannot be read as text ({error})") from None


def read_list(value: str) -> list[str]:
    """The non-empty, stripped lines of the file ``value``, or of stdin when ``value`` is "-" (`--files`)."""
    return [line.strip() for line in read_text(value).splitlines() if line.strip()]


def _as_path(name: str, cwd: Path) -> str | None:
    if Path(name).is_absolute():
        return str(Path(name).resolve())
    if "/" in name or (cwd / name).exists():
        return str((cwd / name).resolve())
    return None


def resolve(names, rows, cwd: Path | None = None) -> tuple[list[tuple[Path, dict]], list[str]]:
    """(the ``rows`` that ``names`` select, in ``rows`` order; one problem line per name that selects no file or
    more than one). ``rows`` are ``(record folder, record)`` pairs, as `candidates.load_records` returns them."""
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    picked: set[int] = set()
    problems: list[str] = []
    for name in names:
        path = _as_path(name, cwd)
        if path is not None:
            hits = [i for i, (_, r) in enumerate(rows) if r["source"]["path"] == path]
        else:
            hits = [i for i, (_, r) in enumerate(rows) if Path(r["source"]["path"]).name == name]
        if not hits:
            problems.append(f"{name!r} matches no file of this run")
        elif len(hits) > 1:
            listing = "; ".join(f"{rows[i][1]['source']['path']} (in {rows[i][0].parent})" for i in hits)
            problems.append(f"{name!r} is ambiguous: it matches {len(hits)} files ({listing}); name one by its path")
        else:
            picked.add(hits[0])
    return [row for i, row in enumerate(rows) if i in picked], problems

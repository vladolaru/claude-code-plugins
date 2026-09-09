"""Identity of a local checkout, with unknown facts as ``None``.

``path_identity(path)`` answers the identity of any local host checkout:
the version it declares plus, through ``git_identity(target)`` and its one
``git log -1`` read, the commit, the commit date and the ``scope`` of the
repository that contains it.

The version is what the checkout declares about itself: the plugin header
(``Version:`` in the main plugin file), a theme's ``style.css``, or
WordPress core's ``wp-includes/version.php``. The commit and its date come
from the git repository that contains the path (``git -C <dir>`` resolves
the enclosing repository, so a plugin inside a monorepo reports the
monorepo's HEAD and says so through ``scope``). The branch is never read:
no host projection carries a branch name, and the commit identifies a
checkout on its own. An unknown fact is ``None``, never a directory name.

The host chain stamps this on every resolved local runtime host, so
reviewer briefings, the review record and telemetry say "version
11.2.0-dev, commit …" instead of "version unknown, commit unknown" for a
checkout one file read away.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from hosts.headers import VERSION_FIELD, find_plugin_headers, find_theme_headers, parse_header_lines

IDENTITY_TIMEOUT_SECONDS = 10
_WP_VERSION_FILE = os.path.join("wp-includes", "version.php")
_WP_VERSION_RE = re.compile(r"^\s*\$wp_version\s*=\s*'([^']+)'", re.MULTILINE)
_MAX_VERSION_FILE_BYTES = 64 * 1024

GIT_IDENTITY_FIELDS = ("commit", "commit_date", "scope")
IDENTITY_FIELDS = ("version",) + GIT_IDENTITY_FIELDS
SCOPE_CHECKOUT = "checkout"
SCOPE_ENCLOSING = "enclosing-repository"


def git_read(target: Path, *args: str) -> Optional[str]:
    """stdout of a read-only git command run in ``target``, or None on failure."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(target), *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=IDENTITY_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    return value or None


def wp_core_version(text: Optional[str]) -> Optional[str]:
    """The ``$wp_version`` a ``wp-includes/version.php`` text declares."""
    match = _WP_VERSION_RE.search(text) if isinstance(text, str) else None
    return match.group(1) if match else None


def header_version(text: Optional[str]) -> Optional[str]:
    """The ``Version:`` a plugin or theme header text declares."""
    headers = parse_header_lines(text.splitlines(keepends=True)) if isinstance(text, str) else None
    version = (headers or {}).get(VERSION_FIELD)
    return version.strip() or None if isinstance(version, str) else None


def declared_version(path: str) -> Optional[str]:
    """The version the checkout declares about itself, or None.

    Plugin header first, then a theme's ``style.css``, then WordPress
    core's version file. A header without a ``Version:`` line is None,
    never the directory name.
    """
    found = find_plugin_headers(path)
    if found is None:
        found = find_theme_headers(path)
    if found is not None:
        _main_file, headers = found
        version = headers.get(VERSION_FIELD)
        return version.strip() or None if isinstance(version, str) else None
    try:
        with open(os.path.join(path, _WP_VERSION_FILE), encoding="utf-8", errors="replace") as handle:
            return wp_core_version(handle.read(_MAX_VERSION_FILE_BYTES))
    except OSError:
        return None


def git_identity(target: Path) -> Dict[str, Optional[str]]:
    """``{commit, commit_date, scope}`` of the repository containing ``target``.

    One ``git log`` read gives the commit and its date; ``scope`` is
    ``"checkout"`` when ``target`` is the repository's own top level and
    ``"enclosing-repository"`` when the commit belongs to a repository that
    contains it (a plugin inside a monorepo). None throughout when there is
    no repository.
    """
    identity: Dict[str, Optional[str]] = {field: None for field in GIT_IDENTITY_FIELDS}
    head = git_read(target, "log", "-1", "--format=%H%n%cI")
    if head is None:
        return identity
    commit, _, commit_date = head.partition("\n")
    identity["commit"] = commit.strip() or None
    identity["commit_date"] = commit_date.strip() or None
    toplevel = git_read(target, "rev-parse", "--show-toplevel")
    if toplevel is not None:
        try:
            same = os.path.samefile(toplevel, target)
        except OSError:
            same = False
        identity["scope"] = SCOPE_CHECKOUT if same else SCOPE_ENCLOSING
    return identity


def path_identity(path: str) -> Dict[str, Any]:
    """``{version, commit, commit_date, scope}`` for a local checkout."""
    identity: Dict[str, Any] = {field: None for field in IDENTITY_FIELDS}
    if not isinstance(path, str) or not os.path.isdir(path):
        return identity
    identity["version"] = declared_version(path)
    identity.update(git_identity(Path(path)))
    return identity

"""Lockfile detection and dependency-root scoping.

A repo's dependency roots are not always its root directory. WooCommerce
keeps no composer.lock at the top level — the one that matters for a PHP
review sits at plugins/woocommerce/, and 46 more sit under packages/,
tools/ and bin/. Root-only detection therefore reports "no PHP deps" for a
repo that has plenty, and searching for all of them installs dozens of
irrelevant toolchains.

So detection is *scoped*: the repo root is always considered, and each
changed file contributes the nearest lockfile-bearing ancestor directory.
A review that touches plugins/woocommerce/src/ pulls in exactly that one
composer root and no others.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from git_paths import decode_git_c_quoted_path
from hosts.containment import contains, contains_lexically


def detect_php_manager(repo_path: str) -> Optional[str]:
    if os.path.isfile(os.path.join(repo_path, "composer.lock")):
        return "composer"
    return None


_JS_LOCKFILE_PRECEDENCE = [
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
]


def detect_js_manager(repo_path: str) -> Optional[str]:
    for lockfile, manager in _JS_LOCKFILE_PRECEDENCE:
        if os.path.isfile(os.path.join(repo_path, lockfile)):
            return manager
    return None


def lockfile_for_manager(manager: str) -> str:
    mapping = {
        "composer": "composer.lock",
        "pnpm": "pnpm-lock.yaml",
        "yarn": "yarn.lock",
        "npm": "package-lock.json",
    }
    return mapping[manager]


@dataclass(frozen=True)
class DepRoot:
    """One directory whose lockfile should be installed.

    rel_path is POSIX-style and relative to the repo root; "." is the repo
    root itself.
    """

    manager: str
    rel_path: str

    def abs_path(self, repo_path: str) -> str:
        return os.path.normpath(os.path.join(repo_path, self.rel_path))


def slot_name(dep_root: DepRoot) -> str:
    """Cache-slot name for a dep root.

    Root dep roots keep the bare manager name, so slots populated before
    nested roots existed stay valid. Nested roots get
    "<manager>@<slug>-<digest>": the slug is a readable, filesystem-safe,
    length-capped rendering of the path, and the 8-hex digest of the exact
    rel_path carries the uniqueness guarantee. A collision would serve one
    root's dependencies to a reviewer asking about another's, and readable
    escaping alone cannot rule that out — the previous "-"→"--" then
    "/"→"-" scheme mapped both "a-/b" and "a/-b" to "a---b".
    """
    if dep_root.rel_path in (".", ""):
        return dep_root.manager
    slug = re.sub(r"[^A-Za-z0-9._]+", "-", dep_root.rel_path).strip("-")[:80]
    digest = hashlib.sha256(dep_root.rel_path.encode("utf-8")).hexdigest()[:8]
    if not slug:
        return f"{dep_root.manager}@{digest}"
    return f"{dep_root.manager}@{slug}-{digest}"


def manager_for_slot(slot: str) -> str:
    """Inverse of slot_name for the manager component only."""
    return slot.split("@", 1)[0]


def _nearest_root_with_lockfile(
    repo_root: str, start_rel: str, lockfiles: Sequence[str]
) -> Optional[str]:
    """Walk up from start_rel to repo_root for a dir holding any lockfile.

    Returns a repo-relative POSIX path, or None. Purely lexical above the
    filesystem check, so paths from deleted files still resolve.
    """
    current = os.path.normpath(os.path.join(repo_root, start_rel))
    repo_root = os.path.normpath(repo_root)

    while True:
        if not contains_lexically(repo_root, current):
            return None

        # The lexical bound cannot see symlinks, but isfile() follows
        # them — a directory that is really a symlink out of the repo
        # would become a dependency root whose install runs in (and
        # stages files from) an external tree the PR chose. Accept only
        # directories whose resolved identity stays inside the repo; a
        # rejected level still lets a legitimate ancestor win.
        if any(os.path.isfile(os.path.join(current, name)) for name in lockfiles):
            if contains(repo_root, current):
                rel = os.path.relpath(current, repo_root)
                return "." if rel == "." else rel.replace(os.sep, "/")

        if current == repo_root:
            return None
        current = os.path.dirname(current)


def _scope_dirs(repo_path: str, scope_paths: Iterable[str]) -> List[str]:
    """Repo-relative directories implied by changed-file paths."""
    dirs = []
    for raw in scope_paths:
        if not raw:
            continue
        rel, was_git_quoted = decode_git_c_quoted_path(raw)
        if rel is None:
            continue
        # Direct CLI inputs may use platform separators; a decoded Git-quoted
        # backslash is a literal filename byte and must not become a slash.
        if not was_git_quoted:
            rel = rel.replace("\\", "/")
        rel = rel.lstrip("/")
        candidate = os.path.join(repo_path, rel)
        # A changed file that still exists resolves to its own directory;
        # anything else (deleted file, renamed dir) is treated as a path
        # whose parent is the interesting one.
        rel_dir = rel if os.path.isdir(candidate) else os.path.dirname(rel)
        dirs.append(rel_dir or ".")
    return dirs


def detect_dep_roots(
    repo_path: str,
    scope_paths: Optional[Iterable[str]] = None,
    max_per_manager: int = 4,
) -> "tuple[List[DepRoot], List[DepRoot]]":
    """Return (selected, dropped) dependency roots for this repo.

    The repo root is always considered first, so existing single-root
    behavior is unchanged. Each scope path then contributes the nearest
    lockfile-bearing ancestor.

    At most *max_per_manager* roots per manager are selected; the remainder
    come back as *dropped* so the caller can report them rather than
    silently narrowing coverage.
    """
    php_lockfiles = ["composer.lock"]
    js_lockfiles = [name for name, _ in _JS_LOCKFILE_PRECEDENCE]

    candidates: List[str] = ["."]
    if scope_paths:
        candidates.extend(_scope_dirs(repo_path, scope_paths))

    selected: List[DepRoot] = []
    dropped: List[DepRoot] = []
    seen = set()
    counts = {}

    for rel_dir in candidates:
        for lockfiles, resolve in (
            (php_lockfiles, detect_php_manager),
            (js_lockfiles, detect_js_manager),
        ):
            root_rel = _nearest_root_with_lockfile(repo_path, rel_dir, lockfiles)
            if root_rel is None:
                continue
            manager = resolve(os.path.join(repo_path, root_rel))
            if not manager:
                continue
            dep_root = DepRoot(manager=manager, rel_path=root_rel)
            if dep_root in seen:
                continue
            seen.add(dep_root)
            if counts.get(manager, 0) >= max_per_manager:
                dropped.append(dep_root)
                continue
            counts[manager] = counts.get(manager, 0) + 1
            selected.append(dep_root)

    return selected, dropped

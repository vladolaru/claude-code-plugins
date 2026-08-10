"""Containment — the pipeline-wide enforcement point for repo boundaries.

Invariant: no pipeline component treats a path outside the reviewed repo's
resolved root as belonging to that repo. Advisory host resolvers use this
boundary to classify source paths; repo-declared rule and reviewer paths use
it before their instructions can be read and executed with real tools.

``contains_lexically`` is only for bounding walks over path spellings that may
not exist. It does not resolve symlinks and must never gate a filesystem read
or an execution.
"""

import os
from typing import Optional


def contains(repo_path: str, candidate: str) -> bool:
    """True when candidate's resolved identity lies inside repo_path's."""
    return _is_prefix(os.path.realpath(repo_path), os.path.realpath(candidate))


def contains_lexically(repo_path: str, candidate: str) -> bool:
    """Purely lexical containment — no symlink resolution, no filesystem.

    For bounding walks over possibly-nonexistent paths only; see the
    module docstring for why this must never gate a read or an execution.
    """
    return _is_prefix(os.path.normpath(repo_path), os.path.normpath(candidate))


def resolve_inside(repo_path: str, rel_path: str) -> Optional[str]:
    """Resolved absolute path of repo_path/rel_path, or None when it escapes.

    The reusable gate for repo-declared relative paths: returns an absolute
    path only when the resolved identity remains within the repo root.
    """
    real_root = os.path.realpath(repo_path)
    resolved = os.path.realpath(os.path.join(real_root, rel_path))
    return resolved if _is_prefix(real_root, resolved) else None


def _is_prefix(root: str, candidate: str) -> bool:
    try:
        return os.path.commonpath([root, candidate]) == root
    except ValueError:  # different drives / mixed absolute-relative
        return False

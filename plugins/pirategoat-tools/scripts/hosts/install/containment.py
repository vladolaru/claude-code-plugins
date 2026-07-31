"""The hosts/ containment invariant — single enforcement point.

Everything under scripts/hosts/ obeys two invariants:

  I1. A review never modifies the reviewed working tree.
  I2. Nothing outside the repo's resolved path is read as an install input
      or used as an execution directory.

Repo content is PR-controlled and reviews run against untrusted branches,
so every containment decision must compare RESOLVED identities — a lexical
check passes for an in-repo spelling whose directory is really a symlink
out of the repo. Round after round of review findings (symlinked dep
roots, escaped bin dirs) traced back to call sites re-deriving this check
locally and each forgetting a piece; hence one module, and a drift guard
in tests/hosts/test_containment_contract.py that forbids the containment
spellings (commonpath, is_relative_to, commonprefix) anywhere else under
scripts/hosts/; other spellings rely on code review plus the resolver
symlink behavior tests.

contains_lexically exists for algorithmic bounds (walk-up loops over paths
that may not exist, e.g. deleted files). It is NEVER a trust decision on
its own — pair it with contains() before reading or executing anything.
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

    The gate for repo-declared relative paths (lockfile-declared patches,
    workspace members): the returned path is safe to read as an install
    input; None means the spelling escapes the repo once resolved.
    """
    real_root = os.path.realpath(repo_path)
    resolved = os.path.realpath(os.path.join(real_root, rel_path))
    return resolved if _is_prefix(real_root, resolved) else None


def _is_prefix(root: str, candidate: str) -> bool:
    try:
        return os.path.commonpath([root, candidate]) == root
    except ValueError:  # different drives / mixed absolute-relative
        return False

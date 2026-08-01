"""Stage a repo's install inputs into the per-clone cache slot.

The install runs in an isolated cache dir, never in the repo's working tree,
so every file the install reads must be copied in first. A manifest plus a
lockfile is not enough for real-world repos:

  - pnpm catalogs (`catalog:` refs) are defined in pnpm-workspace.yaml. Without
    it, pnpm fails at ERR_PNPM_CATALOG_IN_OVERRIDES before it ever reads the
    lockfile.
  - .pnpmfile.cjs is checksummed into the lockfile, so omitting it fails
    --frozen-lockfile with ERR_PNPM_LOCKFILE_CONFIG_MISMATCH.
  - `pnpm.patchedDependencies` points at patch files by relative path; a
    missing patch is a hard ENOENT.
  - Workspace member manifests decide how much actually gets installed. Root
    only, a WooCommerce-shaped monorepo resolves ~1300 packages; with members
    staged it resolves ~4100, and that gap is exactly the dependency source
    reviewers need to read.

Everything here is best-effort: a missing optional input is skipped, never
fatal. The install itself reports real failures.

Deliberately stdlib-only, and deliberately not a YAML parse. Hand-maintained
pnpm-workspace.yaml files in the wild contain literal tabs, which pnpm's
parser tolerates and a strict YAML parser rejects — parsing it would turn a
working repo into a staging crash. Workspace members come from the lockfile's
`importers:` block instead, which is machine-generated and authoritative for
what --frozen-lockfile expects.
"""

import glob
import hashlib
import json
import os
import re
import shutil
from typing import Dict, List, Optional

from hosts.install.containment import resolve_inside

# Manifest + lockfile — the always-required pair.
_BASE_FILES: Dict[str, List[str]] = {
    "composer": ["composer.json", "composer.lock"],
    "npm": ["package.json", "package-lock.json"],
    "pnpm": ["package.json", "pnpm-lock.yaml"],
    "yarn": ["package.json", "yarn.lock"],
}

# Auxiliary install inputs, copied when present. Fixed names only — anything
# path-declared (patches, workspace members) is resolved separately below.
#
# Repo-level .npmrc/.yarnrc carry registry and hoisting settings that change
# resolution, so the install is wrong without them. They can also carry auth
# tokens; the cache slot lives under the invoking user's own ~/.cache, which
# is the same trust boundary the source repo already sits in.
_AUX_FILES: Dict[str, List[str]] = {
    "composer": [],
    "npm": [".npmrc"],
    "pnpm": ["pnpm-workspace.yaml", ".npmrc", ".pnpmfile.cjs", "pnpmfile.cjs"],
    "yarn": [".yarnrc.yml", ".yarnrc", ".npmrc"],
}


def staged_input_paths(manager: str, repo_path: str) -> List[str]:
    """Repo-relative paths of every input `manager`'s install reads.

    One list feeds both staging and the freshness hash: anything copied into
    the slot must also invalidate the cache when it changes, or a config-only
    edit (.npmrc, .pnpmfile.cjs, a patch, a member manifest) would keep
    serving the old dependency layout as a cache hit.
    """
    rels = list(_BASE_FILES[manager] + _AUX_FILES[manager])
    if manager == "pnpm":
        rels.extend(_patch_files(repo_path))
    rels.extend(_workspace_manifests(manager, repo_path))
    return rels


def stage_inputs(manager: str, repo_path: str, cache_dir: str) -> None:
    """Copy everything `manager`'s install needs from repo_path into cache_dir."""
    for rel in staged_input_paths(manager, repo_path):
        _copy_into(repo_path, rel, cache_dir)


def hash_install_inputs(manager: str, repo_path: str) -> str:
    """Combined SHA-256 over every existing staged input's name and content.

    This is the cache-slot freshness key. Names enter the digest alongside
    content so an input appearing, disappearing, or moving changes the key;
    inputs the staging containment check would refuse are excluded the same
    way staging excludes them.
    """
    h = hashlib.sha256()
    for rel in sorted(set(staged_input_paths(manager, repo_path))):
        src = _resolve_staged_source(repo_path, rel)
        if src is None:
            continue
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        with open(src, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                h.update(chunk)
        h.update(b"\x00")
    return h.hexdigest()


def _resolve_staged_source(repo_path: str, rel_path: str) -> Optional[str]:
    """Resolved absolute source for a staged input, or None when refused.

    rel_path can originate in repo-controlled JSON and a review may be
    running against an untrusted branch — the containment gate is the
    trust decision; the isfile check just skips optional absent inputs.
    """
    src = resolve_inside(repo_path, rel_path)
    if src is None or not os.path.isfile(src):
        return None
    return src


def _copy_into(repo_path: str, rel_path: str, cache_dir: str) -> bool:
    """Copy repo_path/rel_path to cache_dir/rel_path, creating parent dirs.

    Returns True when a file was copied.
    """
    src = _resolve_staged_source(repo_path, rel_path)
    if src is None:
        return False

    # Source identity and destination identity are deliberately different:
    # read through an in-repo symlink's resolved target, but preserve the
    # declared path the package manager will use (package.json, patch refs).
    dest = resolve_inside(cache_dir, rel_path)
    if dest is None:
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)
    return True


def _patch_files(repo_path: str) -> List[str]:
    """Relative paths declared in package.json's pnpm.patchedDependencies.

    pnpm 10 also accepts patchedDependencies in pnpm-workspace.yaml; that
    variant is not read here, for the no-YAML-parse reason in the module
    docstring. Such a repo stages one file short and the install reports it.
    """
    manifest = os.path.join(repo_path, "package.json")
    try:
        with open(manifest, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []

    patched = (data.get("pnpm") or {}).get("patchedDependencies") or {}
    if not isinstance(patched, dict):
        return []
    return [value for value in patched.values() if isinstance(value, str)]


def _workspace_manifests(manager: str, repo_path: str) -> List[str]:
    """package.json paths for every workspace member, root excluded."""
    if manager == "pnpm":
        members = _pnpm_importers(os.path.join(repo_path, "pnpm-lock.yaml"))
    elif manager in ("npm", "yarn"):
        members = _globbed_workspaces(repo_path)
    else:
        return []

    return [
        os.path.join(member, "package.json")
        for member in members
        if member not in (".", "")
    ]


# Top-level `importers:` key, then member paths at exactly two-space indent.
# Nested keys sit at four or more spaces, so they cannot match.
_IMPORTERS_HEADER = re.compile(r"^importers:\s*$")
_IMPORTER_KEY = re.compile(r"^ {2}([^\s:][^:]*):\s*$")


def _pnpm_importers(lockfile_path: str) -> List[str]:
    """Workspace member paths from the lockfile's importers block."""
    members: List[str] = []
    in_block = False
    try:
        with open(lockfile_path, encoding="utf-8") as handle:
            for line in handle:
                if not in_block:
                    if _IMPORTERS_HEADER.match(line):
                        in_block = True
                    continue
                if line.strip() and not line.startswith(" "):
                    break  # next top-level key ends the block
                match = _IMPORTER_KEY.match(line)
                if match:
                    members.append(match.group(1).strip().strip("'\""))
    except OSError:
        return []
    return members


def _globbed_workspaces(repo_path: str) -> List[str]:
    """Workspace member paths from package.json's `workspaces` globs."""
    manifest = os.path.join(repo_path, "package.json")
    try:
        with open(manifest, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []

    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    if not isinstance(workspaces, list):
        return []

    members: List[str] = []
    for pattern in workspaces:
        if not isinstance(pattern, str):
            continue
        for path in glob.glob(os.path.join(repo_path, pattern), recursive=True):
            if os.path.isdir(path):
                members.append(os.path.relpath(path, repo_path))
    return sorted(set(members))

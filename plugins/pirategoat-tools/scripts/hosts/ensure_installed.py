"""Ensure the per-clone install cache is current for a repo's lockfiles.

Usage:
  python <plugin>/scripts/hosts/ensure_installed.py --repo <path> \\
      [--overrides-json '<json>' | --overrides-file <path>]

For each detected lockfile (composer.lock, package-lock.json, pnpm-lock.yaml,
yarn.lock), runs the install in a per-clone cache slot under
~/.cache/pirategoat/library-deps/<clone_id>/<manager>/. The repo's working
tree is never modified. Reviewers consume the cache via the host_context
section's library-dep entries.

Because the install runs outside the repo, every input it reads is staged
into the cache slot first — manifest, lockfile, manager config, patch files
and workspace member manifests. See hosts/install/staging.py.

Emits a JSON status payload on stdout. Never exits non-zero for install
failures — emits banners instead. Only exits non-zero on programmer error
(bad args, unreachable state).
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Absolute script execution puts scripts/hosts on sys.path. Add scripts/ so
# `from hosts...` imports resolve the same way they do under `python -m`.
SCRIPTS_DIR = str(Path(__file__).resolve().parents[1])
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from hosts.install.cache import ensure_current, prune_dead_clones
from hosts.install.lockfile import (
    DepRoot, detect_dep_roots, lockfile_for_manager, slot_name,
)
from hosts.install.overrides import parse_overrides
from hosts.install.runner import (
    apply_retry_args, build_install_command, classify_error, should_retry,
)
from hosts.install.staging import hash_install_inputs, stage_inputs


class _InstallFailed(Exception):
    """Raised inside ensure_current's install_fn to signal install failure
    while preserving the failure-holder dict for the outer payload."""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install library-deps for a repo.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--overrides-json")
    parser.add_argument("--overrides-file")
    parser.add_argument(
        "--scope-path", action="append", default=[],
        help="Repo-relative changed path; contributes its nearest "
             "lockfile-bearing ancestor as a dependency root. Repeatable.",
    )
    parser.add_argument(
        "--scope-json",
        help="JSON array of repo-relative changed paths (same effect as "
             "repeating --scope-path, for long file lists).",
    )
    args = parser.parse_args(argv)

    scope_paths = list(args.scope_path)
    if args.scope_json:
        try:
            parsed = json.loads(args.scope_json)
        except ValueError as err:
            print(json.dumps({"status": "error", "error": f"--scope-json: {err}"}))
            return 2
        if not isinstance(parsed, list):
            print(json.dumps({"status": "error", "error": "--scope-json must be a JSON array"}))
            return 2
        scope_paths.extend(str(item) for item in parsed)

    try:
        overrides = parse_overrides(args.overrides_json, args.overrides_file)
    except ValueError as err:
        print(json.dumps({"status": "error", "error": str(err)}))
        return 2

    payload: Dict[str, Any] = {"status": "ok", "managers": []}

    # Opportunistic GC runs on every invocation, including --skip-install,
    # because it has nothing to do with the install itself. Best-effort and
    # bounded so it never adds material time to a review.
    try:
        prune_dead_clones()
    except Exception:  # noqa: BLE001 — GC failure must not block install
        pass

    if overrides.skip_install:
        payload["status"] = "skipped"
        payload["reason"] = "skip_install override"
        print(json.dumps(payload, indent=2))
        return 0

    dep_roots, dropped = detect_dep_roots(args.repo, scope_paths)

    if overrides.js_manager_override:
        dep_roots = [
            DepRoot(manager=overrides.js_manager_override, rel_path=root.rel_path)
            if root.manager != "composer" else root
            for root in dep_roots
        ]

    if not dep_roots:
        payload["status"] = "nothing_to_install"
        print(json.dumps(payload, indent=2))
        return 0

    for dep_root in dep_roots:
        extra_args = (
            overrides.php_args if dep_root.manager == "composer" else overrides.js_args
        )
        payload["managers"].append(_handle_dep_root(
            dep_root=dep_root, repo_path=args.repo,
            extra_args=extra_args, env=overrides.env,
        ))

    # Never narrow coverage silently — a dropped root means a reviewer is
    # missing dependency source they have no way to know about.
    if dropped:
        payload["dropped_dep_roots"] = [
            {"manager": root.manager, "path": root.rel_path} for root in dropped
        ]

    # Banner if anything failed
    failed = [m for m in payload["managers"] if m["status"] == "failed"]
    if failed:
        payload["banner"] = {
            "degraded": True,
            "reason": "install_failed",
            "message": "library-dep verification degraded: install failed for "
                       + ", ".join(_describe(m) for m in failed),
            "unresolved": [
                {"name": _describe(m), "reason": m.get("error_class", "unknown")}
                for m in failed
            ],
        }

    print(json.dumps(payload, indent=2))
    return 0  # always succeed — failures are banners, not errors


def _describe(entry: Dict[str, Any]) -> str:
    """Human label for a manager payload — 'composer' or 'composer (plugins/woocommerce)'."""
    path = entry.get("path", ".")
    if path in (".", "", None):
        return entry.get("manager", "unknown")
    return f"{entry.get('manager', 'unknown')} ({path})"


def _handle_dep_root(
    dep_root: DepRoot,
    repo_path: str,
    extra_args: List[str],
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run install for one dependency root via the per-clone cache.

    Returns one of three payload shapes, each carrying "manager" and "path":
      - {"status": "no_lockfile"} — nothing to install
      - {"status": "ok", "action", "cache_path", "inputs_hash"}
        — success; "action" ∈ {"cache_hit", "installed", "replaced"}
      - {"status": "failed", "error_class", ...} — install failed

    Two install strategies, because the managers differ in how self-contained
    a lockfile is:

    - JS managers install into the cache slot from staged inputs. Nothing in
      a node lockfile points outside the package directory.
    - Composer installs *in place*, with COMPOSER_VENDOR_DIR redirected into
      the cache slot. composer.json routinely declares `type: path`
      repositories ("lib", "../../packages/php/blueprint"), which cannot
      resolve from a staging directory — WooCommerce's own nested root fails
      with "Source path ... is not found". Redirecting only the output keeps
      the working tree unmodified while letting relative paths resolve.

    The closure-based failure_holder pattern bridges between ensure_current's
    "raise on failure" contract and our richer JSON failure payload: install_fn
    populates failure_holder and raises _InstallFailed; the outer except
    returns the populated dict.
    """
    manager = dep_root.manager
    root_abs = dep_root.abs_path(repo_path)
    identity = {"manager": manager, "path": dep_root.rel_path}

    lockfile_path = os.path.join(root_abs, lockfile_for_manager(manager))
    if not os.path.isfile(lockfile_path):
        return {**identity, "status": "no_lockfile"}

    # Freshness keys on every staged input, not just the lockfile — a
    # config-only change (.npmrc, .pnpmfile.cjs, a patch, a member manifest,
    # composer.json settings) changes what the install produces and must not
    # report a cache hit over the old layout.
    inputs_hash = hash_install_inputs(manager, root_abs)
    base_args = list(extra_args)
    in_place = manager == "composer"

    failure_holder: Dict[str, Any] = {}

    def install_fn(staging_path):
        if in_place:
            workdir = root_abs
            install_env = _build_subprocess_env({
                **(env or {}),
                # Absolute, and outside the repo — this is what keeps the
                # working tree untouched. bin-dir must be redirected
                # separately: it defaults to {vendor-dir}/bin, but a
                # composer.json that sets config.bin-dir explicitly escapes
                # the vendor redirect and would write binary proxies into
                # the repo.
                "COMPOSER_VENDOR_DIR": os.path.join(str(staging_path), "vendor"),
                "COMPOSER_BIN_DIR": os.path.join(str(staging_path), "vendor", "bin"),
            })
        else:
            workdir = str(staging_path)
            install_env = _build_subprocess_env(env or {})
            stage_inputs(manager, root_abs, workdir)

        completed, failure = _run_install_command(
            manager, workdir, base_args, install_env
        )
        if failure:
            failure_holder.update({**identity, **failure})
            raise _InstallFailed()
        error_class = (
            classify_error(completed.stderr) if completed.returncode != 0 else None
        )
        if completed.returncode != 0 and should_retry(attempts=0, error_class=error_class):
            retry_args = apply_retry_args(manager, error_class, base_args)
            completed, failure = _run_install_command(
                manager, workdir, retry_args, install_env
            )
            if failure:
                failure_holder.update({**identity, **failure})
                raise _InstallFailed()
            error_class = (
                classify_error(completed.stderr) if completed.returncode != 0 else None
            )
        if completed.returncode != 0:
            failure_holder.update({
                **identity,
                "status": "failed",
                "error_class": error_class or "unknown",
                "stderr_excerpt": (completed.stderr or "")[:500],
            })
            raise _InstallFailed()

    try:
        result = ensure_current(
            repo_path, slot_name(dep_root), inputs_hash, install_fn
        )
    except _InstallFailed:
        return failure_holder

    return {
        **identity,
        "status": "ok",
        "action": result.action,  # "cache_hit" | "installed" | "replaced"
        "cache_path": str(result.cache_path),
        "inputs_hash": inputs_hash,
    }


def _run_install_command(
    manager: str,
    cache_dir: str,
    extra_args: List[str],
    env: Dict[str, str],
) -> Tuple[Optional[subprocess.CompletedProcess], Optional[Dict[str, Any]]]:
    try:
        completed = subprocess.run(
            build_install_command(manager, cache_dir, extra_args=extra_args),
            cwd=cache_dir, capture_output=True, text=True, timeout=20 * 60,
            env=env,
        )
    except FileNotFoundError as err:
        return None, {
            "manager": manager,
            "status": "failed",
            "error": str(err),
            "error_class": "install_command_unavailable",
        }
    except subprocess.TimeoutExpired as err:
        timeout = f" after {err.timeout} seconds" if err.timeout else ""
        return None, {
            "manager": manager,
            "status": "failed",
            "error": f"install command timed out{timeout}",
            "error_class": "install_timeout",
        }
    return completed, None


def _build_subprocess_env(overrides: Dict[str, str]) -> Dict[str, str]:
    merged = dict(os.environ)
    merged.update({key: str(value) for key, value in overrides.items()})
    return merged


if __name__ == "__main__":
    sys.exit(main())

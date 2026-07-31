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
    detect_js_manager, detect_php_manager, hash_lockfile, lockfile_for_manager,
)
from hosts.install.overrides import parse_overrides
from hosts.install.runner import (
    apply_retry_args, build_install_command, classify_error, should_retry,
)
from hosts.install.staging import stage_inputs


class _InstallFailed(Exception):
    """Raised inside ensure_current's install_fn to signal install failure
    while preserving the failure-holder dict for the outer payload."""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install library-deps for a repo.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--overrides-json")
    parser.add_argument("--overrides-file")
    args = parser.parse_args(argv)

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

    php = detect_php_manager(args.repo)
    js = overrides.js_manager_override or detect_js_manager(args.repo)

    if not php and not js:
        payload["status"] = "nothing_to_install"
        print(json.dumps(payload, indent=2))
        return 0

    if php:
        payload["managers"].append(_handle_manager(
            manager="composer", repo_path=args.repo,
            extra_args=overrides.php_args,
            env=overrides.env,
        ))
    if js:
        payload["managers"].append(_handle_manager(
            manager=js, repo_path=args.repo,
            extra_args=overrides.js_args,
            env=overrides.env,
        ))

    # Banner if anything failed
    failed = [m for m in payload["managers"] if m["status"] == "failed"]
    if failed:
        payload["banner"] = {
            "degraded": True,
            "reason": "install_failed",
            "message": "library-dep verification degraded: install failed for "
                       + ", ".join(m["manager"] for m in failed),
            "unresolved": [
                {"name": m["manager"], "reason": m.get("error_class", "unknown")}
                for m in failed
            ],
        }

    print(json.dumps(payload, indent=2))
    return 0  # always succeed — failures are banners, not errors


def _handle_manager(
    manager: str,
    repo_path: str,
    extra_args: List[str],
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run install for one manager via the per-clone cache.

    Returns one of three payload shapes:
      - {"manager", "status": "no_lockfile"} — nothing to install
      - {"manager", "status": "ok", "action", "cache_path", "lockfile_hash"}
        — success; "action" ∈ {"cache_hit", "installed", "replaced"}
      - {"manager", "status": "failed", "error_class", ...} — install failed

    The closure-based failure_holder pattern bridges between ensure_current's
    "raise on failure" contract and our richer JSON failure payload: install_fn
    populates failure_holder and raises _InstallFailed; the outer except
    returns the populated dict.
    """
    lockfile_name = lockfile_for_manager(manager)
    lockfile_path = os.path.join(repo_path, lockfile_name)
    if not os.path.isfile(lockfile_path):
        return {"manager": manager, "status": "no_lockfile"}

    lockfile_hash = hash_lockfile(lockfile_path)
    install_env = _build_subprocess_env(env or {})
    base_args = list(extra_args)

    failure_holder: Dict[str, Any] = {}

    def install_fn(staging_path):
        stage_inputs(manager, repo_path, str(staging_path))
        completed, failure = _run_install_command(
            manager, str(staging_path), base_args, install_env
        )
        if failure:
            failure_holder.update(failure)
            raise _InstallFailed()
        error_class = (
            classify_error(completed.stderr) if completed.returncode != 0 else None
        )
        if completed.returncode != 0 and should_retry(attempts=0, error_class=error_class):
            retry_args = apply_retry_args(manager, error_class, base_args)
            completed, failure = _run_install_command(
                manager, str(staging_path), retry_args, install_env
            )
            if failure:
                failure_holder.update(failure)
                raise _InstallFailed()
            error_class = (
                classify_error(completed.stderr) if completed.returncode != 0 else None
            )
        if completed.returncode != 0:
            failure_holder.update({
                "manager": manager,
                "status": "failed",
                "error_class": error_class or "unknown",
                "stderr_excerpt": (completed.stderr or "")[:500],
            })
            raise _InstallFailed()

    try:
        result = ensure_current(repo_path, manager, lockfile_hash, install_fn)
    except _InstallFailed:
        return failure_holder

    return {
        "manager": manager,
        "status": "ok",
        "action": result.action,  # "cache_hit" | "installed" | "replaced"
        "cache_path": str(result.cache_path),
        "lockfile_hash": lockfile_hash,
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

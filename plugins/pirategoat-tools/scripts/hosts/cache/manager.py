"""Manages the machine-wide ecosystem cache (WordPress + WooCommerce)."""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from hosts.cache.paths import pirategoat_cache_root


@dataclass(frozen=True)
class EcosystemRepo:
    name: str
    url: str


KNOWN_ECOSYSTEM_REPOS: List[EcosystemRepo] = [
    EcosystemRepo(name="wordpress",
                  url="https://github.com/WordPress/wordpress-develop.git"),
    EcosystemRepo(name="woocommerce",
                  url="https://github.com/woocommerce/woocommerce.git"),
]

_STALE_SECONDS = 30 * 24 * 3600

# TTL for fulfillment-mode reads. When the chain's cache-fulfillment pass
# asks the resolver for a host, the slot must be no older than this — older
# slots get a synchronous git pull before the read. Tighter than
# `_STALE_SECONDS` because fulfillment reads are user-facing review context;
# `_STALE_SECONDS` is the staleness signal surfaced by `verify_hosts()`.
_FRESHNESS_SECONDS = 24 * 3600


def cache_root() -> Path:
    return pirategoat_cache_root("ecosystem")


def cache_dir_for(name: str) -> Path:
    return cache_root() / name / "latest"


def _repo_for(name: str) -> EcosystemRepo:
    for r in KNOWN_ECOSYSTEM_REPOS:
        if r.name == name:
            return r
    raise KeyError(f"Unknown ecosystem repo: {name}")


def update_host(name: str) -> Dict[str, Any]:
    try:
        repo = _repo_for(name)
    except KeyError as err:
        return {"name": name, "action": "skipped", "ok": False,
                "stderr": f"unknown ecosystem host: {err}"}

    target = cache_dir_for(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / f".{name}.lock"
    fd = None
    try:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # Another process is updating the same host. Best-effort: return
            # a skipped result so the caller can decide whether to retry.
            return {"name": name, "action": "skipped", "ok": False,
                    "stderr": "another ecosystem update is in progress"}

        # Re-check inside the lock.
        if (target / ".git").is_dir():
            cmd = ["git", "-C", str(target), "pull", "--ff-only"]
            action = "pulled"
        else:
            cmd = ["git", "clone", "--depth", "1", repo.url, str(target)]
            action = "cloned"

        try:
            completed = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30 * 60,
            )
        except subprocess.TimeoutExpired as err:
            return {"name": name, "action": action, "ok": False,
                    "stderr": f"git {action} timed out after {err.timeout} seconds"}
        except FileNotFoundError as err:
            return {"name": name, "action": action, "ok": False,
                    "stderr": f"git executable not found: {err}"}

        ok = completed.returncode == 0
        if ok:
            _touch_last_updated(target)
        return {
            "name": name, "action": action, "ok": ok,
            "stderr": (completed.stderr or "")[:500] if not ok else "",
        }
    finally:
        if fd is not None:
            os.close(fd)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def ensure_fresh(name: str, max_age_seconds: int = _FRESHNESS_SECONDS) -> Dict[str, Any]:
    """Guarantee the cache slot for `name` is no older than max_age_seconds.

    No-ops with action="fresh" if the slot exists and was last updated within
    the window. Otherwise calls update_host() synchronously to refresh.
    Network/git failures from update_host propagate via the returned dict —
    callers should fall back to whatever's on disk if "ok": False.
    """
    target = cache_dir_for(name)
    last = _read_last_updated(target) if target.is_dir() else None
    if last is not None and (time.time() - last) <= max_age_seconds:
        return {"name": name, "action": "fresh", "ok": True, "stderr": ""}
    return update_host(name)


def list_hosts() -> List[Dict[str, Any]]:
    out = []
    for r in KNOWN_ECOSYSTEM_REPOS:
        d = cache_dir_for(r.name)
        present = d.is_dir()
        out.append({
            "name": r.name, "path": str(d), "present": present,
            "last_updated": _read_last_updated(d) if present else None,
        })
    return out


def verify_hosts() -> List[Dict[str, Any]]:
    out = []
    now = time.time()
    for r in KNOWN_ECOSYSTEM_REPOS:
        d = cache_dir_for(r.name)
        present = d.is_dir()
        last = _read_last_updated(d) if present else None
        stale = (last is not None) and (now - last > _STALE_SECONDS)
        out.append({
            "name": r.name, "present": present, "stale": stale,
            "last_updated": last,
        })
    return out


def _touch_last_updated(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / ".last_updated").write_text(str(int(time.time())))


def _read_last_updated(target: Path):
    marker = target / ".last_updated"
    if marker.is_file():
        try:
            return int(marker.read_text().strip())
        except ValueError:
            return int(marker.stat().st_mtime)
    return int(target.stat().st_mtime) if target.is_dir() else None

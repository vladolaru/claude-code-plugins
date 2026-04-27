"""Lockfile detection and hashing."""

import hashlib
import os
from typing import Optional


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


def hash_lockfile(lockfile_path: str) -> str:
    """SHA-256 hex digest of the lockfile contents."""
    h = hashlib.sha256()
    with open(lockfile_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

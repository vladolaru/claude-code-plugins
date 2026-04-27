"""Install subprocess runner and known-failure retry table."""

import re
from typing import Dict, List, Optional


# Mandatory, always-appended script-blocking flags per manager.
_MANDATORY_FLAGS: Dict[str, List[str]] = {
    "composer": ["--no-scripts", "--no-plugins", "--prefer-dist", "--no-interaction"],
    "npm": ["--ignore-scripts", "--no-audit", "--no-fund"],
    "pnpm": ["--ignore-scripts", "--frozen-lockfile"],
    "yarn": ["--ignore-scripts", "--frozen-lockfile"],
}

_INSTALL_SUBCOMMAND: Dict[str, str] = {
    "composer": "install",
    "npm": "ci",
    "pnpm": "install",
    "yarn": "install",
}


# Known-dangerous package manager flags — either enable script execution
# or cause argument-parser misbehavior. Rejected at parse time so the
# mandatory script-blocking flags cannot be neutralized via extra_args.
_REJECTED_EXTRA_ARGS = frozenset({
    "--script-shell",        # npm/yarn: override script interpreter
    "--run-scripts",         # composer: explicitly re-enable scripts
    "--exec",                # pnpm/npm: execute arbitrary binaries
    "--ignore-scripts=false",
    "--scripts=true",
})


def validate_extra_args(extra_args: List[str]) -> None:
    """Reject extra_args that would defeat mandatory script-blocking flags.

    Raises ValueError on any rejected flag. Intended to be called at parse
    time; build_install_command() also calls this defensively.
    """
    for arg in extra_args:
        if arg == "--":
            raise ValueError(
                "Disallowed install argument: '--' separator would cause "
                "mandatory script-blocking flags appended after it to be "
                "consumed as positional arguments."
            )
        if arg in _REJECTED_EXTRA_ARGS:
            raise ValueError(
                f"Disallowed install argument {arg!r}: would defeat "
                f"mandatory script-blocking flags."
            )


def build_install_command(
    manager: str,
    target_cache_dir: str,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    extra = list(extra_args or [])
    validate_extra_args(extra)
    mandatory = _MANDATORY_FLAGS[manager]
    subcommand = _INSTALL_SUBCOMMAND[manager]
    # Belt-and-suspenders: mandatory flags appear both BEFORE user-supplied
    # extra_args (primary) and AFTER (defense against unknown-to-us "last
    # flag wins" edge cases). The list-form subprocess call means no shell
    # interpretation either way.
    return [manager, subcommand, *mandatory, *extra, *mandatory]


# Retry table — map error class to additional args to try.
_RETRY_TABLE: Dict[str, Dict[str, List[str]]] = {
    "npm": {
        "EBADENGINE": ["--engine-strict=false"],
        "ERESOLVE": ["--legacy-peer-deps"],
    },
    "pnpm": {
        "PEER_DEP_MISSING": ["--strict-peer-dependencies=false"],
    },
}


_ERROR_PATTERNS = [
    (re.compile(r"EBADENGINE", re.I), "EBADENGINE"),
    (re.compile(r"ERESOLVE|could not resolve dependency tree", re.I), "ERESOLVE"),
    (re.compile(r"peer dep missing", re.I), "PEER_DEP_MISSING"),
    (re.compile(r"could not authenticate|authentication failed", re.I), "AUTH_FAILED"),
    (re.compile(r"SSL certificate problem|ssl.*self signed", re.I), "SSL_PROBLEM"),
]


def classify_error(stderr: str) -> Optional[str]:
    if not stderr:
        return None
    for pattern, tag in _ERROR_PATTERNS:
        if pattern.search(stderr):
            return tag
    return None


def should_retry(attempts: int, error_class: Optional[str]) -> bool:
    if attempts >= 1:
        return False  # max one retry
    if error_class is None:
        return False
    # Anything in the retry table is retryable
    for manager_tbl in _RETRY_TABLE.values():
        if error_class in manager_tbl:
            return True
    return False


def apply_retry_args(manager: str, error_class: str, base_args: List[str]) -> List[str]:
    mgr_tbl = _RETRY_TABLE.get(manager, {})
    extra = mgr_tbl.get(error_class, [])
    return [*base_args, *extra]


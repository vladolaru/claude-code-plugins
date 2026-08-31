#!/usr/bin/env python3
"""
Dispatch Planner — Centralized review agent dispatch decisions.

Reads the agent registry and changed files to produce a deterministic
dispatch plan: which agents to run, which to skip, and why.

Replaces duplicated triage logic in command files with a single script.

Usage:
    python3 plan_dispatch.py --mode full --git-range "main..HEAD" --output-dir /tmp/review
    python3 plan_dispatch.py --mode incremental --git-range "abc123..HEAD" --output-dir /tmp/review
    python3 plan_dispatch.py --mode pr --git-range "main..HEAD" --output-dir <run-dir>
    python3 plan_dispatch.py --mode full --git-range "main..HEAD" --output-dir /tmp/review --changed-files-list "a.py,b.ts"

Output: JSON dispatch plan on stdout.

Exit codes:
    0  Success — dispatch plan generated
    1  Error — details on stderr

Zero external dependencies (stdlib only).
"""

import argparse
import functools
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    from .dispatch_status import (
        DISPATCH,
        SKIPPED,
        SKIPPED_QUICK_MODE,
        SKIPPED_TRIAGE,
    )
    from .run_paths import artifact_path
except ImportError:
    _scripts_parent = str(Path(__file__).resolve().parent.parent)
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.dispatch_status import (
        DISPATCH,
        SKIPPED,
        SKIPPED_QUICK_MODE,
        SKIPPED_TRIAGE,
    )
    from review.run_paths import artifact_path

# =============================================================================
# Import DOMAIN_CATALOG from agent/scope.py
# =============================================================================

_SCRIPTS_DIR = Path(__file__).resolve().parent

# Use importlib to load scope module from agent subdirectory
import importlib.util

_scope_spec = importlib.util.spec_from_file_location(
    "review_scope", str(_SCRIPTS_DIR / "agent" / "scope.py")
)
_scope_mod = importlib.util.module_from_spec(_scope_spec)
_scope_spec.loader.exec_module(_scope_mod)

DOMAIN_CATALOG = _scope_mod.DOMAIN_CATALOG
filter_noise = _scope_mod.filter_noise
filter_domain = _scope_mod.filter_domain

# Repo-contributed reviewer applicability (shared with bootstrap).
_review_config_spec = importlib.util.spec_from_file_location(
    "review_config", str(_SCRIPTS_DIR / "review_config.py")
)
_review_config_mod = importlib.util.module_from_spec(_review_config_spec)
_review_config_spec.loader.exec_module(_review_config_mod)
reviewer_applies_to_diff = _review_config_mod.reviewer_applies_to_diff

# The registry key of the generic adapter that runs repo-contributed reviewers.
REPO_REVIEWER_ADAPTER = "repo-reviewer-adapter"

# =============================================================================
# Unrecognized-source safety net
# =============================================================================
#
# The catalog can only review languages it knows about. If a changed source file
# uses a language NO domain recognizes, the old behavior was silent: every domain
# returned NO_DOMAIN_FILES and the review produced a clean bill of health for code
# nobody read (exactly the Rust failure). This safety net makes that case fail
# loudly instead.
#
# `_SOURCE_EXTENSIONS` is deliberately a SUPERSET of the actively-reviewed
# languages (`scope._PROG_LANGS`): it adds a long tail of real-but-uncommon
# languages so a gap is flagged even before someone teaches the catalog about
# them. A false "unrecognized" warning is benign (it just asks a human to
# double-check); a silent skip is not. Truly novel extensions outside both lists
# can still slip through — this trades recall for precision (few false alarms).

# Long-tail programming languages the catalog does NOT actively review.
_EXTRA_SOURCE_EXTENSIONS = frozenset({
    "nim", "cr", "v", "jl", "rkt", "elm", "purs",
    "sol", "move", "cairo", "hx", "tcl", "vala",
    "ada", "adb", "ads", "cob", "cbl",
    "ps1", "psm1", "f90", "f95", "f03",
    "sml", "scm", "lisp", "lsp",
})

# All extensions we consider "programming source" for the safety net.
_SOURCE_EXTENSIONS = frozenset(_scope_mod._PROG_LANGS) | _EXTRA_SOURCE_EXTENSIONS


def _ext_of(path: str) -> str:
    """A file's lowercased extension (the text after the final dot).

    Canonical form for "what language is this file" — every extension-set
    membership test routes through here so the case handling can't drift
    across the ~half-dozen call sites.
    """
    return path.rpartition(".")[2].lower()


def _non_test_files_with_ext(files: List[str], exts) -> List[str]:
    """Non-test files from `files` whose extension is in `exts`.

    The shared shape behind the extension-set triage checks (new source,
    style, template) — one filter so a change to the test-exclusion or
    extension-lookup rule lands in every check at once.
    """
    return [f for f in files if not is_test_file(f) and _ext_of(f) in exts]


def detect_unrecognized_source(clean_files: List[str]) -> List[str]:
    """Find changed source files that no reviewer domain will read.

    A file is flagged when its extension looks like programming source
    (`_SOURCE_EXTENSIONS`) but the broad ``code`` domain does not match it —
    i.e., no general-purpose reviewer covers the language.

    Args:
        clean_files: Changed files, already noise-filtered.

    Returns:
        Sorted list of unrecognized-source file paths (empty when all source
        files are covered, which is the normal case).
    """
    if not clean_files:
        return []
    covered = set(filter_domain(clean_files, "code")[0])
    flagged = []
    for f in clean_files:
        if f in covered:
            continue
        if _ext_of(f) in _SOURCE_EXTENSIONS:
            flagged.append(f)
    return sorted(flagged)


# =============================================================================
# Registry loading
# =============================================================================

def load_registry(registry_path: Optional[str] = None) -> dict:
    """Load agent registry from agent_registry.json.

    Args:
        registry_path: Override path to registry file. Defaults to
                       agent_registry.json in the same directory as this script.

    Returns:
        Dict with "agents" key containing agent configurations.
    """
    if registry_path is None:
        registry_path = str(_SCRIPTS_DIR / "agent_registry.json")
    with open(registry_path) as f:
        return json.load(f)


# =============================================================================
# File listing
# =============================================================================

def get_changed_files_from_git(git_range: str) -> List[str]:
    """Get changed files from a git range.

    Args:
        git_range: Git range spec (e.g., "main..HEAD").

    Returns:
        List of changed file paths.
    """
    cmd = ["git", "-c", "core.quotepath=false", "diff", "--name-only", git_range]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return []
        output = result.stdout.strip()
        if not output:
            return []
        return output.splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def get_diff_text(git_range: str, files: Optional[List[str]] = None) -> Optional[str]:
    """Get patch text from a git range for triage.

    Returned in ORIGINAL case: keyword matching normalizes per-source
    (camelCase boundaries must survive until then), and the _has_* patch
    checks lowercase each line themselves.

    Fetched with --function-context so declaration openers stay visible
    when a parameter changes beyond git's default 3 context lines (the
    multiline-declaration tracker needs the opener), and with
    core.quotepath=false so non-ASCII paths stay matchable. The extra
    context cannot pollute keyword matching — keywords read CHANGED LINES
    only (see _changed_lines_text).

    Returns None when the fetch FAILS (nonzero exit, timeout, missing
    git) — distinct from "" (a successful empty diff). Downstream gates
    that infer signal ABSENCE from patch text must treat None as "scan
    never happened" and dispatch conservatively, never as a clean
    negative scan.
    """
    cmd = ["git", "-c", "core.quotepath=false", "diff", "--function-context", git_range]
    if files:
        cmd.extend(["--", *files])
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def parse_changed_files_list(files_str: str) -> List[str]:
    """Parse a comma-separated file list string.

    Args:
        files_str: Comma-separated file paths.

    Returns:
        List of file paths (stripped, non-empty).
    """
    if not files_str:
        return []
    return [f.strip() for f in files_str.split(",") if f.strip()]


# =============================================================================
# Domain matching
# =============================================================================

def count_files_in_domain(files: List[str], domain: str) -> int:
    """Count how many files match a domain's include/exclude patterns.

    Args:
        files: List of file paths.
        domain: Domain name from DOMAIN_CATALOG.

    Returns:
        Number of files matching the domain.
    """
    if domain not in DOMAIN_CATALOG:
        return 0
    matched, _ = filter_domain(files, domain)
    return len(matched)


def get_domain_files(files: List[str], domain: str) -> List[str]:
    """Return files matching a domain's patterns.

    Args:
        files: List of file paths.
        domain: Domain name from DOMAIN_CATALOG.

    Returns:
        List of matched file paths.
    """
    if domain not in DOMAIN_CATALOG:
        return []
    matched, _ = filter_domain(files, domain)
    return matched


def build_domain_counts(files: List[str]) -> Dict[str, int]:
    """Count files matching each domain in DOMAIN_CATALOG.

    Args:
        files: List of file paths (after noise filtering).

    Returns:
        Dict mapping domain name to file count.
    """
    counts = {}
    for domain in sorted(DOMAIN_CATALOG.keys()):
        counts[domain] = count_files_in_domain(files, domain)
    return counts


# Test domain names — used to detect test-only file sets
_TEST_DOMAINS = ("php-tests", "js-tests", "e2e-tests", "go-tests", "rust-test-dirs", "python-tests")

# Agents excluded in quick review mode — lower-signal for small/low-risk PRs
_QUICK_MODE_EXCLUDED_AGENTS = frozenset([
    "wp-architecture-reviewer",
    "history-insights-reviewer",
    "data-flow-privacy-reviewer",
    "concurrency-reviewer",
    "reliability-reviewer",
    "simplification-reviewer",
    "devils-advocate-reviewer",
])

_LOW_SIGNAL_DISPATCH_REASONS = frozenset([
    "always dispatch (domain has files)",
    "conditional (domain has files)",
    "conditional (domain has files, no triage signal to skip)",
    "default",
])

_ABSTRACTION_SUFFIXES = (
    "service",
    "manager",
    "provider",
    "factory",
    "adapter",
    "wrapper",
    "bridge",
    "client",
    "repository",
    "resolver",
    "strategy",
    "interface",
    "contract",
    "module",
    "orchestrator",
    "coordinator",
    "registry",
    "pipeline",
    "driver",
    "transport",
    "connector",
    "facade",
    "gateway",
)


def is_test_file(filepath: str) -> bool:
    """Check if a file matches any test domain pattern."""
    for td in _TEST_DOMAINS:
        if td not in DOMAIN_CATALOG:
            continue
        spec = DOMAIN_CATALOG[td]
        if re.search(spec["include"], filepath):
            return True
    return False


# =============================================================================
# Git context for triage
# =============================================================================


def get_commit_messages(git_range: str) -> str:
    """Get combined commit messages from a git range, in original case
    (keyword matching normalizes per-source so camelCase boundaries survive).

    Returns empty string on failure (fault-tolerant).
    """
    cmd = ["git", "log", "--format=%s%n%b", git_range]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


_FETCH_REMOTE_LINE_RE = re.compile(r"^\S+\s+(.+?)\s+\(fetch\)$")


def _get_fetch_remote_urls() -> List[str]:
    """Return effective fetch URLs for every configured remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []

    urls = []
    for line in result.stdout.splitlines():
        match = _FETCH_REMOTE_LINE_RE.match(line.strip())
        if match:
            urls.append(match.group(1))
    return list(dict.fromkeys(urls))


def get_repository_identity() -> str:
    """Return matchable fetch-remote and checkout identity.

    Every fetch URL participates because ``origin`` can identify a renamed
    fork while another remote identifies the canonical project. The Git
    top-level basename remains the offline/no-remote fallback.
    """
    parts = _get_fetch_remote_urls()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        result = None
    if result is not None and result.returncode == 0:
        top_level = result.stdout.strip()
        if top_level:
            parts.append(Path(top_level).name)
    return "\n".join(dict.fromkeys(parts)).lower()


def _normalize_numstat_path(path: str) -> str:
    """Normalize git numstat rename paths to the post-rename file path."""
    if " => " not in path:
        return path

    normalized = re.sub(r"\{[^{}]* => ([^{}]*)\}", r"\1", path)
    if " => " in normalized:
        _, _, normalized = normalized.rpartition(" => ")
    return normalized


def get_diffstat(git_range: str) -> Dict:
    """Get diffstat summary from a git range.

    Returns dict with:
        added: total lines added
        removed: total lines removed
        deleted_files: list of deleted file paths
        renamed_files: list of renamed file paths
        added_files: list of newly added file paths
        file_stats: per-file added/removed counts

    Returns zeros/empty on failure (fault-tolerant).
    """
    empty = {
        "added": 0,
        "removed": 0,
        "deleted_files": [],
        "renamed_files": [],
        "added_files": [],
        "file_stats": {},
    }

    # Get numstat for add/remove counts
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "diff", "--numstat", git_range],
            capture_output=True, text=True, timeout=30,
        )
        added = 0
        removed = 0
        file_stats = {}
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    path = _normalize_numstat_path(parts[-1])
                    try:
                        added_count = int(parts[0]) if parts[0] != "-" else 0
                        removed_count = int(parts[1]) if parts[1] != "-" else 0
                        added += added_count
                        removed += removed_count
                        file_stats[path] = {
                            "added": added_count,
                            "removed": removed_count,
                        }
                    except ValueError:
                        pass
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return empty

    # Get deleted/renamed files
    added_files = []
    deleted_files = []
    renamed_files = []
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "diff", "--diff-filter=A", "--name-only", git_range],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            added_files = result.stdout.strip().splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "diff", "--diff-filter=D", "--name-only", git_range],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            deleted_files = result.stdout.strip().splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "diff", "--diff-filter=R", "--name-only", git_range],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            renamed_files = result.stdout.strip().splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return {
        "added": added,
        "removed": removed,
        "added_files": added_files,
        "deleted_files": deleted_files,
        "renamed_files": renamed_files,
        "file_stats": file_stats,
    }


# =============================================================================
# Deterministic triage for conditional agents
# =============================================================================


# Directory segments that describe repository scaffolding, not change content.
# Dropped from keyword-matchable path text so monorepo layout ('plugins/
# woocommerce/includes/...') can't satisfy content keywords like 'plugin'.
# Only DIRECTORY segments are dropped — basenames always participate.
_STRUCTURAL_PATH_SEGMENTS = frozenset({
    "plugins", "packages", "includes", "src", "lib", "libs",
    "app", "apps", "client", "assets", "public", "internal",
    "vendor", "node_modules", "dist", "build",
})


def _build_file_paths_text(file_paths: List[str]) -> str:
    """Convert file paths into matchable text for keyword triage.

    Drops repository-structural directory segments (see
    ``_STRUCTURAL_PATH_SEGMENTS``), then replaces path separators, hyphens,
    and underscores with spaces so that segments like 'payment-gateway'
    match keywords like 'payment'.

    Returns text in original case (keyword matching normalizes per-source).
    Empty string if no paths.
    """
    if not file_paths:
        return ""
    parts = []
    for f in file_paths:
        segments = f.split("/")
        kept = [
            seg for seg in segments[:-1]
            if seg.lower() not in _STRUCTURAL_PATH_SEGMENTS
        ]
        kept.append(segments[-1])  # basename always participates
        parts.append(" ".join(kept).replace("-", " ").replace("_", " "))
    return " ".join(parts)


# Insert a space at camelCase identifier boundaries so segments become
# matchable words BEFORE lowercasing destroys them: lower→UPPER
# ('customerEmail' → 'customer email') AND acronym→Word
# ('DBTransaction' → 'DB Transaction' — the UPPER,UPPER-lower transition;
# a pure acronym like 'HTTPS' stays one token).
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


@functools.lru_cache(maxsize=None)
def _normalize_for_matching(text: str) -> str:
    """Prepare a source text for keyword matching: split camelCase, lower.

    Cached because the stable global sources (commit messages, PR text) are
    normalized once per triaged agent — identical inputs across ~28 agents.
    The process is a short-lived per-review CLI, so the cache never outlives
    a single dispatch plan.
    """
    if not text:
        return ""
    return _CAMEL_BOUNDARY_RE.sub(" ", text).lower()


@functools.lru_cache(maxsize=None)
def _keyword_pattern(keyword: str) -> "re.Pattern":
    """Compile a keyword into its triage-matching regex.

    The keyword itself is normalized like the source text (camelCase split,
    lowercased) so registry entries like 'allowBuilds' or 'wp-env' work —
    an uppercase or hyphenated keyword compiled verbatim could never match
    the normalized text and was silently dead.

    Semantics (matched against ``_normalize_for_matching`` output):
    - Identifier-boundary anchored when the keyword begins with a word
      character: a keyword starts wherever the preceding character is not
      [a-z0-9] — so 'lock' matches 'release_cache_lock' (code separators
      like '_' are word STARTS, unlike \\b) but not 'unlock' inside a word
      ('move' matches 'move'/'moved', never 'remove'). Keywords are
      deliberate PREFIXES — no trailing anchor ('accessib' matches
      'accessibility').
    - Separators inside a keyword (space/hyphen/underscore) match any of
      space/hyphen/underscore in the text ('screen reader' matches
      'screen-reader-text'; ' wc ' matches '-wc-' and '_wc_'; 'error_log'
      matches 'errorLog' via camel normalization).
    """
    norm_kw = _normalize_for_matching(keyword)
    pieces = [re.escape(p) for p in re.split(r"[-_ ]+", norm_kw)]
    body = r"[-_\s]".join(pieces)
    if re.match(r"\w", norm_kw):
        body = r"(?<![a-z0-9])" + body
    return re.compile(body)


# Real-file-marker shape test — single-sourced in scope.py (see the
# _FILE_MARKER_RE comment there for why markers are shape-tested rather
# than prefix-blacklisted).
_is_file_marker = _scope_mod._is_file_marker


@functools.lru_cache(maxsize=None)
def _changed_lines_text(diff_text: str) -> str:
    """Extract only the CHANGED lines' content from a patch.

    Keyword matching must never see diff metadata ('diff --git a/plugins/…',
    '+++ b/plugins/…' — which defeated the structural-path stoplist) or
    context lines (which would let unchanged code dispatch reviewers).
    """
    changed = []
    for line in (diff_text or "").splitlines():
        if not line.startswith(("+", "-")):
            continue
        if _is_file_marker(line):
            continue
        changed.append(line[1:])
    return "\n".join(changed)


def _match_keywords_multi_source(
    keywords: List[str],
    sources: List[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """Match keywords against multiple named text sources.

    Sources may arrive in original case — each is normalized here
    (camelCase split, lowercased) so identifier boundaries survive.

    Args:
        keywords: Keyword strings to search for (identifier-boundary,
            separator-tolerant prefix match — see ``_keyword_pattern``).
        sources: List of (source_name, text) tuples to search in.

    Returns:
        List of (keyword, source_name) for each match. Each keyword
        is reported from the first source it matches.
    """
    normalized = [(name, _normalize_for_matching(text)) for name, text in sources]
    matches = []
    for kw in keywords:
        pattern = _keyword_pattern(kw)
        for name, text in normalized:
            if text and pattern.search(text):
                matches.append((kw, name))
                break  # first source wins per keyword
    return matches


def _get_file_added_lines(diffstat: Dict, filepath: str) -> int:
    """Return added lines for a specific file from diffstat."""
    stats = diffstat.get("file_stats", {}).get(filepath)
    if isinstance(stats, dict):
        return int(stats.get("added", 0) or 0)
    if isinstance(stats, (tuple, list)) and stats:
        try:
            return int(stats[0])
        except (TypeError, ValueError):
            return 0
    return 0


def _count_in_scope_non_test_additions(domain_files: List[str], diffstat: Dict) -> int:
    """Count added lines in non-test domain files only."""
    non_test_files = [f for f in domain_files if not is_test_file(f)]
    file_stats = diffstat.get("file_stats") or {}
    if not non_test_files:
        return 0
    if not file_stats:
        return diffstat.get("added", 0)
    return sum(_get_file_added_lines(diffstat, filepath) for filepath in non_test_files)


def _count_in_scope_non_test_changed_lines(domain_files: List[str], diffstat: Dict) -> Optional[int]:
    """Count changed lines (added + removed) in non-test domain files.

    Returns None when the diffstat carries no sizing data at all — absence
    of sizing must read as "unknown", never as "small".
    """
    file_stats = diffstat.get("file_stats") or {}
    has_totals = bool(diffstat.get("added") or diffstat.get("removed"))
    if not file_stats and not has_totals:
        return None
    non_test_files = [f for f in domain_files if not is_test_file(f)]
    if not non_test_files:
        return 0
    if not file_stats:
        return int(diffstat.get("added", 0) or 0) + int(diffstat.get("removed", 0) or 0)
    total = 0
    for filepath in non_test_files:
        stats = file_stats.get(filepath)
        if isinstance(stats, dict):
            total += int(stats.get("added", 0) or 0) + int(stats.get("removed", 0) or 0)
        elif isinstance(stats, (tuple, list)) and len(stats) >= 2:
            try:
                total += int(stats[0]) + int(stats[1])
            except (TypeError, ValueError):
                pass
    return total


def _looks_like_abstraction_file(filepath: str) -> bool:
    """Heuristic for class/module abstraction file names."""
    stem = Path(filepath).stem.lower()
    return any(stem.endswith(suffix) for suffix in _ABSTRACTION_SUFFIXES)


def _get_new_abstraction_files(domain_files: List[str], diffstat: Dict) -> List[str]:
    """Return newly added non-test files that look like abstractions."""
    added_files = set(diffstat.get("added_files", []) or [])
    if not added_files:
        return []
    return [
        filepath
        for filepath in domain_files
        if filepath in added_files
        and not is_test_file(filepath)
        and _looks_like_abstraction_file(filepath)
    ]


# _SUPPORTED_TRIAGE_CHECKS is derived from _CHECK_SPECS below — one record
# per check, no parallel registries.

# =============================================================================
# Triage detector polarity
#
# Keywords and checks are POSITIVE-evidence detectors: a match can prove that
# a reviewer is relevant, but silence proves only that the configured
# vocabulary did not match. Representative syntax tables therefore document
# recognition, never completeness. No declarative setting may promote that
# silence into negative evidence; a future optimization would need an
# executable completeness proof, not a configuration assertion.
#
# ONE record per check — every other registry is a view over this dict.
# `reads_diff` controls patch fetching and the failed-fetch guard. Nothing in
# this record authorizes a negative inference.
# =============================================================================

_CHECK_SPECS = {
    "has_new_functions": {"reads_diff": True},
    "has_modified_signatures": {"reads_diff": True},
    "has_new_types": {"reads_diff": True},
    "has_import_changes": {"reads_diff": True},
    "has_public_api_changes": {"reads_diff": True},
    "has_docblock_changes": {"reads_diff": True},
    "has_markup_changes": {"reads_diff": True},
    "has_renamed_symbols": {"reads_diff": True},
    "has_sql_queries": {"reads_diff": True},
    "has_http_client_calls": {"reads_diff": True},
    "has_collection_iteration": {"reads_diff": True},
    "has_new_source_files": {"reads_diff": False},
    "has_documentation_files": {"reads_diff": False},
    "has_style_files": {"reads_diff": False},
    "has_template_files": {"reads_diff": False},
    "file_deletions": {"reads_diff": False},
    "net_removal": {"reads_diff": False},
    "large_pr": {"reads_diff": False},
    "new_abstraction_files": {"reads_diff": False},
    "substantial_non_test_additions": {"reads_diff": False},
    "spans_architectural_layers": {"reads_diff": False},
}

_SUPPORTED_TRIAGE_CHECKS = frozenset(_CHECK_SPECS)
_DIFF_BASED_CHECKS = frozenset(
    name for name, spec in _CHECK_SPECS.items() if spec["reads_diff"]
)
# Stylesheet extensions (from scope.py's language groups) — for the
# has_style_files check: a changed stylesheet is inherently a visual
# surface (visibility, focus indicators, contrast) regardless of which
# tokens the hunk contains, so token-matching can't cover this class.
_STYLE_EXTENSIONS = frozenset(_scope_mod._STYLE_LANGS)

def _needs_diff_scan(config: dict) -> bool:
    """True when the agent's triage reads patch text — keywords or any
    diff-based structural check. Shared by the fetch decision and the
    fetch-failure guard so the two can never drift."""
    return bool(config.get("triage_keywords")) or any(
        check in _DIFF_BASED_CHECKS for check in config.get("triage_checks", [])
    )

# Function/method/type declaration lines across every language the code
# domains scope — a detector that only knows PHP/JS syntax silently gates
# Java/Go/Rust/Kotlin/C# diffs (patterns run against lowercased lines).
# Statement keywords that must never read as a method NAME or a type-first
# declaration opener — shared by the TS method/member patterns and the
# type-first (package-private Java / C-family) pattern so the blacklists
# cannot drift (a missing `foreach` here once made PHP loop headers count
# as signatures).
_STATEMENT_KEYWORDS = (
    "if|elseif|else|for|foreach|while|switch|match|when|catch|return|do|"
    "new|try|throw|case|await|typeof|function|const|let|var|using|lock|"
    "yield|assert|raise|import|use|export|default"
)

_SIGNATURE_PATTERNS = (
    re.compile(r"\bdef\s+[a-z_][a-z0-9_?!]*"),  # python/ruby (ruby: no parens required)
    re.compile(r"\bfunction\s+[a-z_$][a-z0-9_$]*\s*\("),
    re.compile(r"\b(public|protected|private)\s+(static\s+)?function\s+[a-z_$][a-z0-9_$]*\s*\("),
    re.compile(r"\b(export\s+)?(async\s+)?function\s+[a-z_$][a-z0-9_$]*\s*\("),
    re.compile(r"\b(pub(\([^)]*\))?\s+)?fn\s+[a-z_][a-z0-9_]*"),  # rust
    re.compile(r"\bfunc\s+(\([^)]*\)\s*)?[a-z_][a-z0-9_]*\s*\("),  # go (incl. receiver)
    re.compile(r"\bfun\s+[a-z_][a-z0-9_]*\s*\("),  # kotlin
    # java/c#: access modifier, then type tokens (generics/arrays ok), then name(
    re.compile(r"\b(public|protected|private|internal)\s+(?:[a-z_][\w<>\[\],.\s?]*?\s+)+[a-z_][a-z0-9_]*\s*\("),
    # java/c-family TYPE-FIRST methods with NO access modifier (legal Java:
    # package-private). Two identifier groups before the params exclude
    # single-keyword statements (`switch (x) {`); the shared statement
    # blacklist excludes two-token statements (`else if (x) {`, `return
    # new Order(id) {`); no-nested-paren params exclude call-with-callback
    # statements; `{`-terminated lines exclude plain calls.
    re.compile(
        rf"^(?!(?:{_STATEMENT_KEYWORDS})\b)[a-z_][\w<>\[\],.\s?]*?\s+"
        rf"(?!(?:{_STATEMENT_KEYWORDS})\b)[a-z_][\w$]*\s*\([^()]*\)\s*\{{$"
    ),
    # ts/js arrow-function declarations (const-level API surface).
    # Params may be parenthesized OR a bare identifier (`value => ...`);
    # `const x = a >= b` never matches — the bare form requires the
    # identifier DIRECTLY before `=>`:
    re.compile(r"\b(export\s+)?const\s+[a-z_$][\w$]*[^=\n]*=\s*(async\s+)?(\([^()]*\)\s*(:[^=>\n]*)?|[a-z_$][\w$]*\s*)=>"),
    # ts interface member with a return-type annotation:  name(params): Type;
    re.compile(r"^(?:(?:public|private|protected|readonly|static|abstract|async|override|get|set)\s+)*"
               rf"(?!(?:{_STATEMENT_KEYWORDS})\b)"
               r"[a-z_$][\w$]*\s*(<[^<>]*>)?\s*\([^()]*\)\s*:\s*[^;{=]+;$"),
    # ts/js class/object method implementation:  name(params)[: Type] {
    # No-nesting params exclude call statements ending in a callback block
    # (`it('x', () => {`); the keyword blacklist excludes control flow.
    re.compile(r"^(?:(?:public|private|protected|readonly|static|abstract|async|override|get|set)\s+)*"
               rf"(?!(?:{_STATEMENT_KEYWORDS})\b)"
               r"[a-z_$][\w$]*\s*(<[^<>]*>)?\s*\([^()]*\)\s*(:\s*[^;{=]+)?\{$"),
    re.compile(r"\b(export\s+)?class\s+[a-z_$][a-z0-9_$]*\b"),
    re.compile(r"\b(export\s+)?interface\s+[a-z_$][a-z0-9_$]*\b"),
    re.compile(r"\b(export\s+)?enum\s+[a-z_$][a-z0-9_$]*\b"),
)

_PUBLIC_API_PATTERNS = (
    re.compile(r"\bexport\s+(async\s+)?function\s+[a-z_$][a-z0-9_$]*\s*\("),
    re.compile(r"\bexport\s+(class|interface|enum|type|const)\s+[a-z_$][a-z0-9_$]*\b"),
    re.compile(r"\bpublic\s+(static\s+)?function\s+[a-z_$][a-z0-9_$]*\s*\("),
    re.compile(r"\bregister_rest_route\s*\("),
    # REST/route registration surfaces per ecosystem — "REST API endpoint
    # additions" is language-generic, and register_rest_route alone left it
    # one-language covered (the endpoint matrix section proves each form).
    # Patterns run against lowercased, stripped lines.
    # Python route decorators (FastAPI/Flask/Sanic style):
    re.compile(r"@\w+\.(get|post|put|patch|delete|head|options|route|websocket)\s*\("),
    # Spring @GetMapping / @RequestMapping; ASP.NET [HttpGet]; actix #[get(:
    re.compile(r"@(get|post|put|patch|delete|request)mapping\b"),
    re.compile(r"\[http(get|post|put|patch|delete|head|options)\b"),
    re.compile(r"#\[(get|post|put|patch|delete)\s*\("),
    # Router builders: axum/express .route("..."), go net/http HandleFunc:
    re.compile(r"\.route\s*\(\s*['\"]"),
    re.compile(r"\bhttp\.handle(func)?\s*\(\s*\""),
    # Verb-method registrations. Conventional router receivers take any
    # quoted first argument (vapor's app.get("orders") has no slash);
    # arbitrary receivers need a path-shaped argument so cache.get('k')
    # and params.get('id') stay out (cache.get('/tmp/x') can still
    # false-positive — over-dispatch, never a skip):
    re.compile(r"\b(app|router|routes|group|mux|r|e)\s*\.\s*((map)?(get|post|put|patch|delete|head|options|all)|handle(func)?)\s*\(\s*['\"`]"),
    # PHP routers use :: and -> operators (Route::get, $router->get) —
    # conventional route/router/app receivers with a quoted first arg;
    # $order->get('total') and Config::get('key') receivers stay out:
    re.compile(r"(?:\brou(?:te|ter)|\$(?:route|router|app|api))\s*(?:::|->)\s*(?:get|post|put|patch|delete|head|options|any|match)\s*\(\s*['\"]"),
    re.compile(r"\.\s*(map)?(get|post|put|patch|delete|head|options)\s*\(\s*['\"`][/:]"),
    # Route-DSL lines: rails/play routes ("get '/orders'"), ktor blocks
    # ('get("/orders") {'), http4s matchers ('case GET -> Root / ...'):
    re.compile(r"^(get|post|put|patch|delete|head|options)\s*[( ]\s*['\"]?[/:]"),
    re.compile(r"\b(get|post|put|patch|delete)\s*->\s*root\b"),
    # Hook registrations AND emissions — a changed apply_filters/do_action
    # line is a public contract change just like add_action/add_filter.
    re.compile(r"\b(add_action|add_filter|apply_filters|do_action)\s*\("),
    # Public DATA members — a DTO property or class constant is API surface
    # just like a signature (`public string $status` → `public ?string
    # $status` breaks consumers). Patterns run against lowercased lines.
    # PHP property (typed, untyped, readonly, constructor-promoted):
    re.compile(r"\b(public|protected)\s+((static|readonly)\s+)*\??[\w|\\]*\s*\$[a-z_]"),
    # PHP class constant:
    re.compile(r"\b(public|protected)\s+(final\s+)?const\s"),
    # TS class field (name-first: `public status: string`):
    re.compile(r"\bpublic\s+(static\s+)?(readonly\s+)?[a-z_$][\w$]*\s*[?!]?\s*:"),
    # TS IMPLICIT-public class fields — fields are public by default, so
    # `status?: string;` is DTO contract without any keyword. CSS shares
    # the `name: value;` line shape, so these key on TS-only markers: the
    # optional `?:`, or a type-shaped value (primitive keyword, generic,
    # array, union). A required field of a bare custom type
    # (`status: locale;`) is indistinguishable from a CSS declaration
    # lowercased and stays undetected. Line-local by design: tracking
    # exported CLASS bodies would count nested method-body lines too.
    re.compile(
        r"^(?:(?:readonly|static|declare|override|abstract)\s+)*[a-z_$][\w$]*\s*"
        r"(?:\?\s*:\s*[^;={]+"
        r"|:\s*(?:(?:string|number|boolean|any|unknown|void|null|undefined|"
        r"never|object|symbol|bigint)(?:\s*\[\s*\])?(?:\s*\|[^;={]*)?"
        r"|[a-z_$][\w$.]*\s*(?:<[^;={]*>|\[\s*\])(?:\s*\|[^;={]*)?"
        r"|[a-z_$][\w$.]*(?:\s*\|\s*[a-z_$][^;={]*)))\s*;$"
    ),
    # C#/Java field or auto-property (type-first: `public string Status
    # { get; set; }`, `public static final int MAX = 3;`). Parens are not
    # in the type/name classes, so methods can never match:
    re.compile(
        r"\bpublic\s+((static|final|readonly|const|sealed|override|abstract)\s+)*"
        r"[a-z_][\w<>\[\],.?\s]*?\s+[a-z_][\w$]*\s*(\{|;|=)"
    ),
)

_DOCBLOCK_MARKERS = (
    "/**",
    "*/",
    "@param",
    "@return",
    "@throws",
    "@since",
    "///",
    '"""',
    "'''",
)

_DOCUMENTATION_BASENAMES = {
    "readme",
    "changelog",
    "contributing",
    "agents",
    "claude",
}


def _iter_patch_lines(diff_text: str, marker: str):
    """Yield patch content lines that start with marker, excluding diff metadata."""
    for line in (diff_text or "").splitlines():
        if not line.startswith(marker):
            continue
        if _is_file_marker(line):
            continue
        yield line[1:].strip().lower()


def _has_pattern_in_patch_lines(diff_text: str, markers: Tuple[str, ...], patterns) -> bool:
    for marker in markers:
        for line in _iter_patch_lines(diff_text, marker):
            if any(pattern.search(line) for pattern in patterns):
                return True
    return False


def _has_new_functions(diff_text: str) -> bool:
    return _has_pattern_in_patch_lines(diff_text, ("+",), _SIGNATURE_PATTERNS)


def _has_changed_lines_in_multiline_block(
    diff_text: str, opener_patterns, open_ch: str = "(", close_ch: str = ")"
) -> bool:
    """True when a +/- line falls INSIDE a multiline block whose opener
    matches `opener_patterns`.

    A block spanning several lines shows its opener as unchanged CONTEXT
    when only an interior line changes — a parameter added inside
    `public function charge(`, an entry swapped inside a Go `import (` or
    TS `import {` block, a member changed inside `export interface X {`.
    No line-local pattern can see those. Track openers (context or changed)
    whose delimiter stays unclosed; while inside, any changed line is block
    evidence. Delimiters are configurable because signatures/Go imports
    nest on parens while TS imports and interface bodies nest on braces
    (paren-tracking a brace block — or vice versa — would either exit
    immediately or swallow whole function bodies).

    Requires the opener to be present in the patch — get_diff_text fetches
    with --function-context precisely so it is.
    """
    in_block = False
    depth = 0
    for raw in (diff_text or "").splitlines():
        if raw.startswith(("@@", "diff ", "index ")) or _is_file_marker(raw):
            in_block = False
            depth = 0
            continue
        if not raw[:1] in ("+", "-", " "):
            continue
        marker, line = raw[0], raw[1:].strip().lower()
        # Strings are opaque: a default like `suffix: str = ")"` must not
        # close the declaration early, a literal "(" must not hold it
        # open, and string contents must not read as openers. Multiline
        # strings (heredocs, triple quotes) remain a residual — their
        # interior lines are not recognizable line-locally.
        line = _STRING_LITERAL_RE.sub('""', line)
        if in_block:
            if marker in "+-":
                return True
            depth += line.count(open_ch) - line.count(close_ch)
            if depth <= 0:
                in_block = False
            continue
        if any(pattern.search(line) for pattern in opener_patterns):
            depth = line.count(open_ch) - line.count(close_ch)
            in_block = depth > 0
    return False


# HTTP/API client call surfaces — language-agnostic shapes for the
# "HTTP/API client calls" / "External service integrations" criteria,
# which otherwise rested on the 'fetch'/'request'/'wp_remote' keywords
# alone (go's http.Get carried no signal). Patterns run on lowercased
# lines; camelCase collapses (HttpClient -> httpclient), which the word
# boundaries rely on.
_HTTP_CLIENT_PATTERNS = (
    re.compile(r"\bhttp\.(get|post|head|do|postform|newrequest)\s*\("),  # go net/http
    re.compile(r"\bfetch\s*\("),
    re.compile(r"\baxios\b"),
    re.compile(r"\brequests\.(get|post|put|patch|delete|head|request)\s*\("),
    re.compile(r"\bcurl_(init|exec|setopt)\s*\("),
    re.compile(r"\b(guzzle\w*|httpclient)\b"),
    re.compile(r"\bwp_remote_\w+\s*\("),
    re.compile(r"\burllib\.request\b|\bhttp\.client\b"),
)


def _has_http_client_calls(diff_text: str) -> bool:
    return _has_pattern_in_patch_lines(diff_text, ("+", "-"), _HTTP_CLIENT_PATTERNS)


# Collection-iteration shapes for performance's unbounded-iteration
# criterion. Each form is syntax-UNIQUE to its family, so the check is
# claimable across the full covered set without per-file scoping;
# `reason = '... for regressions in ...'` prose is excluded by requiring
# loop punctuation, and comment-leading lines are skipped in the scan.
# `for` STATEMENT forms are anchored to content start (statements begin
# the stripped line) so quoted prose like 'checked for regressions in
# payments' never matches; method forms (.forEach/.each/foreach() are
# syntax-shaped enough to match mid-line.
_ITERATION_PATTERNS = (
    re.compile(r"^for\s*\(\s*(const|let|var)\s+[\w$]+\s+of\b"),  # js for-of
    re.compile(r"\.\s*foreach\s*\("),  # js .forEach / scala .foreach (lowered)
    re.compile(r"\bforeach\s*\(\s*[\$\w]"),  # php / c#
    re.compile(r"^for\s+[\w,\s]+\s+in\s+[\w.\[($]"),  # python/rust/swift for-in
    re.compile(r"\.each(_with_index|_slice)?\s*(\{|\bdo\b)"),  # ruby
    re.compile(r"^for\s+[\w,\s]*:?=?\s*range\b"),  # go range
    re.compile(r"^for\s*\(\s*[\w<>\[\],.\s]+\s+\w+\s*:\s*\w"),  # java enhanced for
    re.compile(r"^for\s*\(\s*\w+\s+in\s+"),  # kotlin for-in
    re.compile(r"^for\s*\(\s*\w+\s*<-"),  # scala comprehension
)

_COMMENT_LEAD_RE = re.compile(r"^(#|//|\*|/\*)")


def _has_collection_iteration(diff_text: str) -> bool:
    for line in _iter_patch_lines(diff_text, "+"):
        if _COMMENT_LEAD_RE.match(line):
            continue
        if any(pattern.search(line) for pattern in _ITERATION_PATTERNS):
            return True
    return False


# Raw SQL statement shapes — language-agnostic (string contents), so no
# competence-matrix entry is needed. Anchored to content start or an
# opening quote/paren: SQL lives in string literals (or bare lines in .sql
# files), which keeps prose like '// select the widget from the registry'
# from matching. Quoted UI copy ("select an option from the menu") can
# still false-positive — that over-dispatches a reviewer, never skips one.
#
# CLAUSE and DDL shapes are evidence in their own right: the DML opener is
# often unchanged CONTEXT while an interior ORDER BY / JOIN line changes,
# and index/schema statements have no SELECT at all. Clause-shape matching
# is deliberately line-local — a multiline SQL-string tracker would need
# per-language string-literal syntax (heredocs, f-strings, concatenation)
# for the same evidence these distinctive shapes already give.
_SQL_STATEMENT_RE = re.compile(
    r"""(?:^|["'`(])\s*(?:select\s+[^;]{0,200}?\bfrom\s|insert\s+into\s|"""
    r"""update\s+[a-z_."`\[\]{$]+\s+set\s|delete\s+from\s|"""
    # Interior clauses (content-/quote-anchored like the openers):
    r"""order\s+by\s+[a-z_"`]|group\s+by\s+[a-z_"`]|having\s+\w+|"""
    # WHERE needs a comparison shape soon after the column so prose
    # ("where the config = null") only survives at content start in
    # prose files, which the code domains exclude:
    r"""where\s+[\w."`$%(]+\s*(?:=|!=|<>|<|>|like\s|in\s*\(|is\s+(?:not\s+)?null|between\s)|"""
    r"""(?:left|right|inner|outer|cross)\s+join\s+[a-z_"`]|"""
    # Bare JOIN (no qualifier) needs its ON/USING clause to stay
    # distinct from prose ("join us on slack" only survives at content
    # start in prose files, which the code domains exclude):
    r"""join\s+[\w"`]+(?:\s+\w+)?\s+(?:on|using)\s|"""
    r"""union\s+(?:all\s+)?select\s|limit\s+\d+(?:\s+offset\s+\d+)?\s*[;"')]?$|"""
    # DDL (indexes, schema):
    r"""create\s+(?:unique\s+)?index\s|create\s+(?:temp(?:orary)?\s+)?table\s|"""
    r"""alter\s+table\s|drop\s+(?:index|table)\s|truncate\s+(?:table\s+)?[a-z_"`])"""
)


def _has_sql_queries(diff_text: str) -> bool:
    return _has_pattern_in_patch_lines(diff_text, ("+", "-"), (_SQL_STATEMENT_RE,))


_IDENT_TOKEN_RE = re.compile(r"[A-Za-z_$][\w$]*")
_STRING_LITERAL_RE = re.compile(r"'[^']*'|\"[^\"]*\"")

# Rename pairing is O(run length) per line pair; a pathological hunk with
# thousands of consecutive changed lines is a rewrite, not a rename.
_RENAME_RUN_CAP = 40


def _line_rename_pair(removed: str, added: str) -> bool:
    """True when `added` is `removed` with exactly ONE identifier swapped
    (consistently, if it occurs more than once). String literals are
    blanked first so copy tweaks ('payment failed' → 'payment declined')
    don't read as renames; numbers stay in the skeleton so value changes
    don't either."""
    removed = _STRING_LITERAL_RE.sub('""', removed.strip())
    added = _STRING_LITERAL_RE.sub('""', added.strip())
    if removed == added:
        return False
    if _IDENT_TOKEN_RE.sub("\x00", removed) != _IDENT_TOKEN_RE.sub("\x00", added):
        return False
    mapping = None
    for old, new in zip(
        _IDENT_TOKEN_RE.findall(removed), _IDENT_TOKEN_RE.findall(added)
    ):
        if old == new:
            continue
        if mapping is None:
            mapping = (old, new)
        elif mapping != (old, new):
            return False
    return mapping is not None


def _has_renamed_symbols(diff_text: str) -> bool:
    """True when a hunk contains paired removed/added runs where some line
    pair differs by exactly one identifier — the shape of a symbol rename.

    Language-agnostic by construction: it compares token skeletons, not
    syntax, so it needs no entry in the detector competence matrix. Runs
    are paired positionally (how git renders a rename); unequal-length
    runs are edits, not renames.
    """
    removed_run: List[str] = []
    added_run: List[str] = []

    def _runs_pair() -> bool:
        if not removed_run or len(removed_run) != len(added_run):
            return False
        if len(removed_run) > _RENAME_RUN_CAP:
            return False
        return any(
            _line_rename_pair(r, a) for r, a in zip(removed_run, added_run)
        )

    for line in (diff_text or "").splitlines():
        if line.startswith("-") and not _is_file_marker(line):
            if added_run:
                if _runs_pair():
                    return True
                removed_run, added_run = [], []
            removed_run.append(line[1:])
        elif line.startswith("+") and not _is_file_marker(line):
            if removed_run:
                added_run.append(line[1:])
        else:
            if _runs_pair():
                return True
            removed_run, added_run = [], []
    return _runs_pair()


# Brace-delimited type bodies: every line inside an interface (or object
# type alias) body is a member SIGNATURE, so any changed line inside one is
# signature evidence — including unannotated members no line-local pattern
# can classify.
_TYPE_BODY_OPENER_PATTERNS = (
    re.compile(r"\b(export\s+)?(declare\s+)?interface\s+[a-z_$][\w$]*"),
    re.compile(r"\b(export\s+)?type\s+[a-z_$][\w$]*\s*=\s*\{"),
    # Go declares interfaces name-first — every line of the body is a
    # method signature. Struct bodies are FIELDS, not signatures, so only
    # `interface` enters body state.
    re.compile(r"\btype\s+[a-z_]\w*\s+interface\s*\{"),
)


def _has_modified_signatures(diff_text: str) -> bool:
    # A REMOVED signature is sufficient on its own: either it is one side
    # of a modification, or the declaration was deleted — both are
    # contract changes (deleting `func Exported(...)` breaks consumers).
    # Pure ADDITIONS stay excluded: has_new_functions owns that signal,
    # and agents that deliberately don't carry it must not regain it here.
    # (Go's export-by-capitalization is unrecoverable after lowercasing,
    # so unexported removals over-dispatch rather than exported ones
    # skipping.)
    if _has_pattern_in_patch_lines(diff_text, ("-",), _SIGNATURE_PATTERNS):
        return True
    if _has_changed_lines_in_multiline_block(diff_text, _SIGNATURE_PATTERNS):
        return True
    return _has_changed_lines_in_multiline_block(
        diff_text, _TYPE_BODY_OPENER_PATTERNS, "{", "}"
    )


# Plain comment lines / inline comments — clarity review's remit includes
# them, and the docblock token list alone missed '// note' style comments.
# ' // ' requires surrounding whitespace so 'https://...' never matches.
# ' # ' and ' // ' need surrounding whitespace so 'https://…' and '#fff'
# hex colors never match; line-initial forms cover whole-line comments.
_COMMENT_LINE_RE = re.compile(r"^(//|#)\s|^\*\s|\s//\s|\s#\s")

# Linter/tooling directives are machine instructions, not documentation —
# swapping a phpcs annotation is not a clarity signal (a real run's human
# reviewer pruned code-clarity on exactly that diff).
_DIRECTIVE_COMMENT_TOKENS = (
    "phpcs:", "eslint-", "eslint:", "stylelint-", "prettier-ignore",
    "noqa", "@ts-ignore", "@ts-expect-error", "@ts-nocheck",
)


def _has_docblock_changes(diff_text: str) -> bool:
    for marker in ("+", "-"):
        for line in _iter_patch_lines(diff_text, marker):
            if any(token in line for token in _DOCBLOCK_MARKERS):
                return True
            if _COMMENT_LINE_RE.search(line) and not any(
                token in line for token in _DIRECTIVE_COMMENT_TOKENS
            ):
                return True
    return False


# Type-definition lines: class/interface/trait/enum/struct/record
# declarations across scoped languages. Narrower than _SIGNATURE_PATTERNS
# (which also fires on every plain function) — the architecture criterion is
# about new TYPES, not new functions. Modifier prefixes cover Java/C#/Kotlin
# (public/sealed/data/open/...) and Rust's pub; Go declares via `type X
# struct`.
_TYPE_DEFINITION_PATTERNS = (
    re.compile(
        r"^\s*(?:(export|public|internal|protected|private|abstract|final|"
        r"sealed|static|open|data|partial|readonly|case)\s+|pub(\([^)]*\))?\s+)*"
        r"(class|interface|trait|enum|struct|record|protocol|actor)\s+[a-z_$]"
    ),
    re.compile(r"^\s*type\s+[a-z_][a-z0-9_]*\s+(struct|interface)\b"),  # go
)


def _has_new_types(diff_text: str) -> bool:
    return _has_pattern_in_patch_lines(diff_text, ("+",), _TYPE_DEFINITION_PATTERNS)


# Import/require/use statement lines (added or removed) — the dead-code
# criterion 'Import/require statements added or removed'.
_IMPORT_PATTERNS = (
    re.compile(r"^import\s"),
    re.compile(r"^from\s+\S+\s+import\s"),
    re.compile(r"^use\s+[a-z\\]"),
    re.compile(r"^using\s+[a-z]"),  # c#
    re.compile(r"\brequire(_once)?\s*[\s(]\s*['\"]"),
)


# Multiline import-block openers: Go groups on parens, TS/JS named imports
# and PHP group-use on braces.
_IMPORT_PAREN_OPENERS = (
    re.compile(r"^import\s*\($"),  # go import group
    # python `from x import (` — the member lines carry no import token:
    re.compile(r"^from\s+[\w.]+\s+import\s*\(\s*(#.*)?$"),
)
_IMPORT_BRACE_OPENERS = (
    re.compile(r"^import\s+(type\s+)?\{"),  # ts/js named imports
    re.compile(r"^use\s+[\w\\]+\\\{"),  # php group-use
    re.compile(r"^use\s+[\w:]+::\{"),  # rust use group
)


def _has_import_changes(diff_text: str) -> bool:
    if _has_pattern_in_patch_lines(diff_text, ("+", "-"), _IMPORT_PATTERNS):
        return True
    if _has_changed_lines_in_multiline_block(diff_text, _IMPORT_PAREN_OPENERS):
        return True
    return _has_changed_lines_in_multiline_block(
        diff_text, _IMPORT_BRACE_OPENERS, "{", "}"
    )


def _spans_architectural_layers(domain_files: List[str], min_layers: int = 3) -> bool:
    """True when non-test domain files span >= min_layers distinct directories.

    A change cutting across several directories at once is the mechanical
    proxy for 'files spanning 3+ architectural layers'."""
    parents = {
        str(Path(f).parent)
        for f in domain_files
        if not is_test_file(f)
    }
    return len(parents) >= min_layers


def _has_documentation_files(domain_files: List[str]) -> bool:
    for filepath in domain_files:
        lower = filepath.lower()
        stem = Path(lower).stem
        suffix = Path(lower).suffix
        if lower.startswith("docs/") or "/docs/" in lower:
            return True
        if suffix in {".md", ".mdx", ".rst"}:
            return True
        if stem in _DOCUMENTATION_BASENAMES:
            return True
    return False


# Go exports by CAPITALIZATION — the one visibility signal that cannot
# survive the lowercased triage pipeline, so it gets a dedicated
# case-preserving scan. `func (recv)` receivers are Go-only syntax;
# capital-named funcs are unconventional elsewhere.
_GO_EXPORTED_API_RE = re.compile(
    r"\bfunc\s+(\([^)]*\)\s*)?[A-Z]\w*\s*\("
    r"|\btype\s+[A-Z]\w*\s+(struct|interface)\b"
)


def _has_go_exported_api(diff_text: str) -> bool:
    for line in (diff_text or "").splitlines():
        if not line.startswith(("+", "-")) or _is_file_marker(line):
            continue
        if _GO_EXPORTED_API_RE.search(line):
            return True
    return False


# Go struct/interface bodies are public-API territory for the CONTRACT
# check even though struct fields are not signatures — exported struct
# fields are wire format. The tracker runs on lowercased lines, so
# export-by-capitalization is unavailable here: unexported struct bodies
# over-dispatch rather than exported ones skipping.
_GO_TYPE_BODY_OPENERS = (
    re.compile(r"\btype\s+[a-z_]\w*\s+(struct|interface)\s*\{"),
)


def _has_public_api_changes(diff_text: str) -> bool:
    if _has_pattern_in_patch_lines(diff_text, ("+", "-"), _PUBLIC_API_PATTERNS):
        return True
    if _has_go_exported_api(diff_text):
        return True
    if _has_changed_lines_in_multiline_block(
        diff_text, _PUBLIC_API_PATTERNS + _SIGNATURE_PATTERNS
    ):
        return True
    # Go struct/interface bodies and TS interface/type-alias bodies are both
    # brace-delimited — one pass over the union of openers, not two.
    return _has_changed_lines_in_multiline_block(
        diff_text, _GO_TYPE_BODY_OPENERS + _TYPE_BODY_OPENER_PATTERNS, "{", "}"
    )


# Markup-emission detection for the has_markup_changes triage check.
# The categorized token patterns and comment/string handling live in scope.py
# because its a11y budget priority uses the same classifier — one source, two
# consumers. Matched against BOTH added and removed patch lines — removing
# markup is exactly when its blast radius (label associations, CSS hooks,
# AT semantics) needs review.
_has_markup_changes = _scope_mod.patch_has_markup_tokens


# =============================================================================
# Triage-check runners — the EXECUTION view over _CHECK_SPECS.
#
# Every check declared in _CHECK_SPECS has exactly one runner here; the
# meta-test test_check_runners_cover_specs binds the two sets, so a check
# added to _CHECK_SPECS without a runner (or vice versa) fails at test time
# instead of silently never firing. Runners share a uniform signature over
# every triage input a check might need and return a dispatch-reason string
# (which becomes the DISPATCH reason) or None when the check does not fire.
# =============================================================================

# Uniform runner signature: (domain_files, diffstat, diff_text,
# in_scope_added, min_lines) -> Optional[str]. Unused parameters are ignored.


def _diff_detector_check(detector, reason: str):
    """Build a runner for the common "detector(diff_text) → fixed reason" shape."""
    def _run(domain_files, diffstat, diff_text, in_scope_added, min_lines):
        return reason if detector(diff_text) else None
    return _run


def _check_new_abstraction_files(domain_files, diffstat, diff_text, in_scope_added, min_lines):
    files = _get_new_abstraction_files(domain_files, diffstat)
    if files:
        return f"new abstraction file(s): {', '.join(files[:3])}"
    return None


def _check_substantial_non_test_additions(domain_files, diffstat, diff_text, in_scope_added, min_lines):
    if in_scope_added >= min_lines > 0:
        return f"substantial non-test additions in scope ({in_scope_added} lines)"
    return None


def _check_file_deletions(domain_files, diffstat, diff_text, in_scope_added, min_lines):
    if diffstat.get("deleted_files") or diffstat.get("renamed_files"):
        count = len(diffstat.get("deleted_files", [])) + len(diffstat.get("renamed_files", []))
        return f"{count} file(s) deleted or renamed"
    return None


def _check_has_new_source_files(domain_files, diffstat, diff_text, in_scope_added, min_lines):
    new_sources = _non_test_files_with_ext(diffstat.get("added_files", []), _SOURCE_EXTENSIONS)
    if new_sources:
        return f"new source file(s) introduced ({len(new_sources)})"
    return None


def _check_net_removal(domain_files, diffstat, diff_text, in_scope_added, min_lines):
    if diffstat.get("removed", 0) > diffstat.get("added", 0):
        return f"net removal ({diffstat['removed']} removed > {diffstat['added']} added)"
    return None


def _check_large_pr(domain_files, diffstat, diff_text, in_scope_added, min_lines):
    if len(domain_files) >= 20:
        return f"large change ({len(domain_files)} files in domain)"
    # The registry criterion reads "20+ files OR 500+ lines" — back the lines half too.
    changed = _count_in_scope_non_test_changed_lines(domain_files, diffstat)
    if changed is not None and changed >= 500:
        return f"large change ({changed} lines in domain)"
    return None


def _check_has_documentation_files(domain_files, diffstat, diff_text, in_scope_added, min_lines):
    if _has_documentation_files(domain_files):
        return "documentation file changes"
    return None


def _check_spans_architectural_layers(domain_files, diffstat, diff_text, in_scope_added, min_lines):
    if _spans_architectural_layers(domain_files):
        return "change spans 3+ directories"
    return None


def _check_has_style_files(domain_files, diffstat, diff_text, in_scope_added, min_lines):
    style_files = _non_test_files_with_ext(domain_files, _STYLE_EXTENSIONS)
    if style_files:
        return f"style file changes (visual surface): {', '.join(style_files[:3])}"
    return None


def _check_has_template_files(domain_files, diffstat, diff_text, in_scope_added, min_lines):
    template_files = [
        f for f in domain_files
        if not is_test_file(f) and _scope_mod.is_template_file(f)
    ]
    if template_files:
        return f"template file changes (UI surface): {', '.join(template_files[:3])}"
    return None


_CHECK_RUNNERS: Dict[str, Callable] = {
    "new_abstraction_files": _check_new_abstraction_files,
    "substantial_non_test_additions": _check_substantial_non_test_additions,
    "file_deletions": _check_file_deletions,
    "has_new_source_files": _check_has_new_source_files,
    "net_removal": _check_net_removal,
    "large_pr": _check_large_pr,
    "has_documentation_files": _check_has_documentation_files,
    "spans_architectural_layers": _check_spans_architectural_layers,
    "has_style_files": _check_has_style_files,
    "has_template_files": _check_has_template_files,
    # Diff-detector checks — one shared shape, fixed reason per check.
    "has_new_functions": _diff_detector_check(
        _has_new_functions, "new function, method, or type definition"),
    "has_modified_signatures": _diff_detector_check(
        _has_modified_signatures, "modified function or type signature"),
    "has_renamed_symbols": _diff_detector_check(
        _has_renamed_symbols, "renamed symbol (paired lines differ by one identifier)"),
    "has_sql_queries": _diff_detector_check(
        _has_sql_queries, "raw SQL statement in changed lines"),
    "has_http_client_calls": _diff_detector_check(
        _has_http_client_calls, "HTTP/API client call in changed lines"),
    "has_collection_iteration": _diff_detector_check(
        _has_collection_iteration, "collection iteration in changed lines"),
    "has_docblock_changes": _diff_detector_check(
        _has_docblock_changes, "docblock or API comment changes"),
    "has_public_api_changes": _diff_detector_check(
        _has_public_api_changes, "public API surface changes"),
    "has_markup_changes": _diff_detector_check(
        _has_markup_changes, "markup emission in changed lines"),
    "has_new_types": _diff_detector_check(
        _has_new_types, "new class, interface, trait, or enum definition"),
    "has_import_changes": _diff_detector_check(
        _has_import_changes, "import/require statements added or removed"),
}


def _validate_triage_checks(agent_name: str, config: dict) -> None:
    for check in config.get("triage_checks", []):
        if check not in _SUPPORTED_TRIAGE_CHECKS:
            raise ValueError(f"Unsupported triage check for {agent_name}: {check}")


def triage_conditional_agent(
    agent_name: str,
    config: dict,
    domain_files: List[str],
    commit_messages: str,
    diffstat: Dict,
    pr_text: str = "",
    diff_text: Optional[str] = None,
    repository_text: str = "",
) -> Tuple[str, str]:
    """Apply deterministic triage for a conditional agent.

    Triage layers (first match wins):
    1. Test-only filter: if ALL domain files are test files → SKIPPED_TRIAGE
    2. Optional PHP-source gate: if configured and no PHP source files → SKIPPED_TRIAGE
    3. Change-local keyword match: triage_keywords search commit messages,
       file paths, PR title/body, and patch text → DISPATCH
    4. Repository keyword match: triage_repository_keywords search repository
       identity only → DISPATCH
    5. Agent-specific checks (dead-code: deletions, net removal, structural signals) → DISPATCH
    5.5. Evidence gate: require_triage_keyword_match agents skip here when
       neither keywords nor checks fired → SKIPPED_TRIAGE
    6. Default: DISPATCH (conservative — when in doubt, dispatch)

    Args:
        agent_name: Name of the agent.
        config: Agent configuration from registry.
        domain_files: Files matching the agent's domain(s).
        commit_messages: Combined commit messages in ORIGINAL case
            (keyword matching normalizes per-source).
        diffstat: Diffstat summary dict.
        pr_text: PR title + body in ORIGINAL case (empty if unavailable).
        diff_text: Patch text in ORIGINAL case. "" is a successful
            empty scan; None means no scan happened (never fetched, or
            the fetch failed) — gates must not infer signal absence.
        repository_text: Lowercased repository origin/name (empty if unavailable).

    Returns:
        (status, reason) where status is DISPATCH or SKIPPED_TRIAGE.
    """
    _validate_triage_checks(agent_name, config)

    # Layer 1: Test-only filter
    # If every domain-matching file is a test file, skip the agent.
    # Conditional agents target production-code concerns; test-only diffs
    # don't need security/performance/architecture review.
    if domain_files and all(is_test_file(f) for f in domain_files):
        return SKIPPED_TRIAGE, "all matching files are test files"

    in_scope_added = _count_in_scope_non_test_additions(domain_files, diffstat)

    # Gate: min_added_lines — skip if PR doesn't add enough code in non-test scope
    min_lines = config.get("min_added_lines", 0)
    if min_lines > 0 and in_scope_added < min_lines:
        return SKIPPED_TRIAGE, f"below minimum addition threshold ({in_scope_added} < {min_lines} lines)"

    # Layer 2: Agent-wide source gate.
    if config.get("require_php_source_file") and not any(
        f.lower().endswith(".php") and not is_test_file(f)
        for f in domain_files
    ):
        return SKIPPED_TRIAGE, "requires PHP source file"

    # Layer 3: Change-local keyword match. Ambient repository identity is
    # deliberately excluded so existing agents cannot inherit it implicitly.
    keywords = config.get("triage_keywords", [])
    if keywords:
        file_paths_text = _build_file_paths_text(domain_files)
        sources = [
            ("commits", commit_messages),
            ("files", file_paths_text),
            ("pr", pr_text),
            ("diff", _changed_lines_text(diff_text)),
        ]
        matches = _match_keywords_multi_source(keywords, sources)
        if matches:
            # Group by source for a readable reason
            by_source: Dict[str, List[str]] = {}
            for kw, src in matches[:5]:
                by_source.setdefault(src, []).append(kw)
            reason_parts = []
            for src, kws in by_source.items():
                reason_parts.append(f"{src}: {', '.join(kws[:3])}")
            return DISPATCH, f"keywords matched ({'; '.join(reason_parts)})"

    # Layer 4: Repository identity is an ambient applicability signal. Agents
    # must opt in with source-specific keywords rather than reusing the generic
    # change-local keyword set.
    repository_keywords = config.get("triage_repository_keywords", [])
    repository_matches = _match_keywords_multi_source(
        repository_keywords,
        [("repository", repository_text)],
    )
    if repository_matches:
        matched_keywords = ", ".join(kw for kw, _ in repository_matches[:5])
        return DISPATCH, f"repository keywords matched ({matched_keywords})"

    # Layer 5: Agent-specific checks. Each check's predicate lives in
    # _CHECK_RUNNERS (the execution view over _CHECK_SPECS); the first that
    # returns a reason wins. _validate_triage_checks already rejected any
    # check name absent from the registry.
    for check in config.get("triage_checks", []):
        reason = _CHECK_RUNNERS[check](
            domain_files, diffstat, diff_text, in_scope_added, min_lines
        )
        if reason:
            return DISPATCH, reason

    # Unknown is not negative — I/O edition. The explicit applicability gate
    # below infers signal ABSENCE from patch text. When this agent's triage
    # reads patch text and the fetch failed (diff_text is None; "" is a
    # successful empty scan), the detectors never saw the patch — dispatch
    # conservatively instead of letting a git timeout masquerade as a clean
    # negative scan.
    if diff_text is None and _needs_diff_scan(config):
        return DISPATCH, (
            "patch text unavailable (diff fetch failed); cannot verify "
            "absence of triage signals — dispatching conservatively"
        )

    # Evidence gate: agents that opt in dispatch only on a positive triage
    # signal (keyword match above, or a triage check). Sits AFTER Layer 5 so
    # checks count as evidence; before this reorder the gate short-circuited
    # them, so a check-carrying agent could never dispatch on checks alone.
    if config.get("require_triage_keyword_match"):
        return SKIPPED_TRIAGE, "requires positive triage signal; no keyword or check matched"

    # Layer 6: Default — DISPATCH when no triage signal skips the agent.
    # Keywords and triage checks provide positive evidence, but conditional
    # agents still dispatch conservatively when their domain has files.
    return DISPATCH, "conditional (domain has files, no triage signal to skip)"


# =============================================================================
# Dispatch decision logic
# =============================================================================

def decide_agent_dispatch(
    agent_name: str,
    config: dict,
    domain_counts: Dict[str, int],
    clean_files: Optional[List[str]] = None,
    commit_messages: str = "",
    diffstat: Optional[Dict] = None,
    pr_text: str = "",
    diff_text: Optional[str] = None,
    repository_text: str = "",
    git_range: Optional[str] = None,
    diff_text_cache: Optional[Dict[Tuple[str, ...], Optional[str]]] = None,
) -> Tuple[str, str]:
    """Decide whether to dispatch a single agent.

    For always-dispatch and manual/special agents, only domain file counts
    matter. For conditional agents, deterministic triage is applied using
    commit messages, file paths, repository identity, PR metadata, diffstat,
    and test-file detection.

    Args:
        agent_name: Name of the agent.
        config: Agent configuration from registry.
        domain_counts: File counts per domain.
        clean_files: Full list of reviewable files (for domain matching).
        commit_messages: Combined commit messages in ORIGINAL case
            (keyword matching normalizes per-source).
        diffstat: Diffstat summary dict.
        pr_text: PR title + body + labels + branch + issue titles, in
            ORIGINAL case.
        diff_text: Patch text in ORIGINAL case (None = not scanned/fetch failed).
        repository_text: Lowercased repository origin/name.
        git_range: Git range used to fetch domain-specific patch text.

    Returns:
        (status, reason) tuple where status is "DISPATCH", "SKIPPED",
        or "SKIPPED_TRIAGE".
    """
    dispatch_class = config.get("dispatch_class", "conditional")
    domain = config.get("domain")
    _validate_triage_checks(agent_name, config)

    # Check if the agent's domain has files
    has_domain_files = False
    if domain is None:
        has_domain_files = True
    else:
        has_domain_files = domain_counts.get(domain, 0) > 0

    # Check secondary domains
    if not has_domain_files:
        for sec_domain in config.get("secondary_domains", []):
            if domain_counts.get(sec_domain, 0) > 0:
                has_domain_files = True
                break

    # No files in any relevant domain -> skip
    if not has_domain_files:
        domain_label = domain or "(none)"
        secondary = config.get("secondary_domains", [])
        if secondary:
            domain_label += f" + {', '.join(secondary)}"
        return SKIPPED, f"no files in {domain_label} domain"

    # Always-dispatch agents: dispatch if domain has files
    if dispatch_class == "always":
        return DISPATCH, "always dispatch (domain has files)"

    # Conditional agents: apply deterministic triage
    if dispatch_class == "conditional" and clean_files is not None:
        # Gather domain-matched files for triage
        domain_files = get_domain_files(clean_files, domain) if domain else []
        for sec_domain in config.get("secondary_domains", []):
            domain_files.extend(get_domain_files(clean_files, sec_domain))
        # Deduplicate
        domain_files = sorted(set(domain_files))
        if diff_text is None and git_range and _needs_diff_scan(config):
            cache_key = tuple(domain_files)
            if diff_text_cache is not None:
                if cache_key not in diff_text_cache:
                    diff_text_cache[cache_key] = get_diff_text(git_range, domain_files)
                diff_text = diff_text_cache[cache_key]
            else:
                diff_text = get_diff_text(git_range, domain_files)

        # diff_text passes through UNCHANGED: None is the failed-fetch
        # sentinel the conservative guard keys on — an `or ""` here would
        # convert the failure into a successful empty scan and let the
        # applicability gate skip an agent from a scan that never ran.
        return triage_conditional_agent(
            agent_name, config, domain_files,
            commit_messages, diffstat or {},
            pr_text=pr_text,
            diff_text=diff_text,
            repository_text=repository_text,
        )

    # Conditional agents without triage context: dispatch by default
    if dispatch_class == "conditional":
        return DISPATCH, "conditional (domain has files)"

    # Fallback
    return DISPATCH, "default"


def _build_pr_text(review_context: Optional[dict]) -> str:
    """Build original-case text from PR metadata for keyword triage.

    Combines PR title, body, labels, branch name, and linked issue titles
    into a single searchable text block.

    Args:
        review_context: Parsed review-context mapping, or None.

    Returns:
        Combined text in original case (keyword matching normalizes
        per-source). Empty string if no context.
    """
    if not review_context:
        return ""
    parts = []
    pr = review_context.get("pr", {})
    if pr.get("title"):
        parts.append(pr["title"])
    if pr.get("body"):
        parts.append(pr["body"])
    # Labels are high-signal explicit categorization
    for label in pr.get("labels", []):
        if isinstance(label, str):
            parts.append(label)
    # Branch name often has descriptive slugs
    branch = review_context.get("git", {}).get("head_ref", "")
    if branch:
        # Convert separators so "fix/WOOPLUG-5988-payment-gateway" becomes matchable
        parts.append(branch.replace("/", " ").replace("-", " ").replace("_", " "))
    # Linked issue titles
    for issue in review_context.get("linked_issues_details", []):
        if issue.get("title"):
            parts.append(issue["title"])
    return "\n".join(parts)


def _load_review_context(path: Optional[str]) -> Optional[dict]:
    """Load review context from disk. Returns None on any failure."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def expand_repo_reviewers(
    review_context, domain_counts, clean_files, dispatch_list, host="claude"
):
    """Expand repo-declared reviewers into synthetic adapter dispatch entries.

    Each ``reviewers[]`` entry in the reviewed repo's ``.pirategoat/config.json``
    becomes one dispatch entry named ``repo-<id>-reviewer`` (the ``-reviewer``
    suffix is load-bearing: reconciliation maps it to the short
    ``reviewers/repo-<id>/`` directory identity).
    All such entries target the generic ``repo-reviewer-adapter`` body but carry a
    distinct ``ref``/``channel``/``execution``/``model``/``scope_domains``.
    Applicability gates dispatch like a conditional agent. Appended in place to
    ``dispatch_list``; returns ``(signals, warnings)`` — human-readable
    per-reviewer signal strings plus provenance-exclusion warnings.
    """
    signals: List[str] = []
    warnings: List[str] = []
    review_config = (review_context or {}).get("review_config") or {}
    # Provenance-gated entries never become dispatchable (the exclusion is
    # hard, enforced at config normalization) — but the gate must be LOUD:
    # each exclusion goes into the plan's warnings, the only channel the
    # step-5 briefing actually renders, even when nothing remains to
    # dispatch.
    for entry in review_config.get("untrusted") or []:
        label = entry.get("id") or entry.get("path") or entry.get("kind")
        warnings.append(
            f"repo review config: UNTRUSTED {entry.get('kind')} "
            f"'{label}' — {entry.get('reason')}"
        )
    reviewers = review_config.get("reviewers") or []
    if not reviewers:
        return signals, warnings

    domains_with_files = {d for d, c in domain_counts.items() if c > 0}
    for rev in reviewers:
        applies = rev.get("applies_to")
        applicable = reviewer_applies_to_diff(applies, domains_with_files, clean_files)
        name = f"repo-{rev['id']}-reviewer"
        # Scope the adapter to the reviewer's declared domains (fall back to the
        # broad "code" domain when it declares none), filtered to real domains.
        declared = [d for d in (applies or {}).get("domains", []) if d in DOMAIN_CATALOG]
        scope_domains = declared or ["code"]
        if rev.get("execution") == "isolated":
            # An explicit isolation request must never silently WIDEN into
            # inline execution — refuse until isolated execution exists.
            status = "SKIPPED"
            reason = (
                "isolated execution is not implemented — refusing the "
                "inline fallback"
            )
        elif applicable:
            status = "DISPATCH"
            reason = "repo reviewer applicable to this diff"
        else:
            status = "SKIPPED_TRIAGE"
            reason = "repo reviewer not applicable (no matching domain files or paths)"
        dispatch_list.append({
            "name": name,
            "adapter": REPO_REVIEWER_ADAPTER,
            # The validated ABSOLUTE path: bootstrap resolves a relative ref
            # against its own invocation directory, so a review launched from
            # a repo subdirectory would report the valid prompt missing and
            # the adapter would write an empty result. The repo-relative form
            # stays available under "ref" semantics only via review_config.
            "ref": rev.get("resolved_ref") or rev.get("ref"),
            "label": rev.get("label", rev["id"]),
            "channel": rev.get("channel", "blocking"),
            "execution": rev.get("execution", "inline"),
            "model": (
                "inherit"
                if host == "codex" and rev.get("model")
                else rev.get("model")
            ),
            "declared_model": rev.get("model"),
            "scope_domains": scope_domains,
            "domain": None,
            "focus": rev.get("label", rev["id"]),
            "status": status,
            "reason": reason,
        })
        signals.append(f"{name}: STATUS={status} ({reason})")
    return signals, warnings


def build_dispatch_plan(
    mode: str,
    git_range: str,
    output_dir: str,
    changed_files: List[str],
    registry: Optional[dict] = None,
    commit_messages: Optional[str] = None,
    diffstat: Optional[Dict] = None,
    review_context: Optional[dict] = None,
    quick: bool = False,
    host: str = "claude",
) -> dict:
    """Build the complete dispatch plan.

    Args:
        mode: Review mode ("full", "incremental", "pr").
        git_range: Git range used.
        output_dir: Output directory for review files.
        changed_files: List of changed file paths.
        registry: Agent registry dict. Loaded from default path if None.
        commit_messages: Pre-fetched commit messages (fetched from git if None).
        diffstat: Pre-fetched diffstat (fetched from git if None).
        review_context: Parsed review-context mapping for PR metadata triage.
        quick: If True, exclude low-signal agents with SKIPPED_QUICK_MODE status.
        host: Dispatch host. Codex native subagents ignore Claude model
            declarations, so repo-reviewer entries project their effective tier.

    Returns:
        Dispatch plan dict with mode, dispatch array, scope_summary, etc.
    """
    if registry is None:
        registry = load_registry()

    agents = registry.get("agents", {})

    # Filter noise from the file list
    if changed_files:
        clean_files, noise_files = filter_noise(changed_files)
    else:
        clean_files, noise_files = [], []

    # Build domain file counts
    domain_counts = build_domain_counts(clean_files)

    # Fetch git context for triage (fault-tolerant)
    if commit_messages is None:
        commit_messages = get_commit_messages(git_range)
    if diffstat is None:
        diffstat = get_diffstat(git_range)

    # Build stable context signals for keyword matching (fault-tolerant).
    # Repository identity remains opt-in because it describes the checkout,
    # not the current change.
    pr_text = _build_pr_text(review_context)
    repository_text = (
        get_repository_identity()
        if any(config.get("triage_repository_keywords") for config in agents.values())
        else ""
    )

    # Build dispatch decisions
    dispatch_list = []
    agent_signals = []
    diff_text_cache: Dict[Tuple[str, ...], str] = {}

    for agent_name in sorted(agents.keys()):
        config = agents[agent_name]
        # Exclude agents that are never part of the review cohort.
        # special = synthesis/orchestration agents dispatched outside the plan.
        # manual = opt-in only, never auto-dispatched.
        if config.get("dispatch_class") in ("manual", "special"):
            continue

        status, reason = decide_agent_dispatch(
            agent_name, config, domain_counts,
            clean_files=clean_files,
            commit_messages=commit_messages,
            diffstat=diffstat,
            pr_text=pr_text,
            repository_text=repository_text,
            git_range=git_range,
            diff_text_cache=diff_text_cache,
        )

        # Quick mode: skip blocklisted agents ONLY if triage did not find
        # strong signals (keywords matched, special checks triggered).
        # If triage explicitly dispatched based on evidence, honor it —
        # the blocklist catches low-signal default dispatches, not
        # keyword-confirmed ones.
        if (quick and agent_name in _QUICK_MODE_EXCLUDED_AGENTS
                and status == DISPATCH
                and reason in _LOW_SIGNAL_DISPATCH_REASONS):
            status = SKIPPED_QUICK_MODE
            reason = "excluded in quick review mode (no triage signal to override)"

        entry = {
            "name": agent_name,
            "domain": config.get("domain"),
            "focus": config.get("focus", ""),
            "status": status,
            "reason": reason,
        }
        dispatch_list.append(entry)

        # Build signal string
        if status == DISPATCH:
            signal = f"{agent_name}: STATUS={DISPATCH}"
            if reason != "always dispatch (domain has files)":
                signal += f" ({reason})"
        elif status == SKIPPED_TRIAGE:
            signal = f"{agent_name}: STATUS={SKIPPED_TRIAGE} ({reason})"
        else:
            signal = f"{agent_name}: STATUS={SKIPPED} ({reason})"
        agent_signals.append(signal)

    # Repo-contributed reviewers: expand each declared reviewer into a synthetic
    # dispatch entry targeting the generic adapter, gated by applicability.
    repo_signals, repo_warnings = expand_repo_reviewers(
        review_context, domain_counts, clean_files, dispatch_list, host=host
    )
    agent_signals.extend(repo_signals)

    # Safety net: changed source files no reviewer domain will read (unrecognized
    # language). Surfaced loudly so the gap can't masquerade as a clean review.
    unrecognized_source = detect_unrecognized_source(clean_files)

    # Scope summary
    scope_summary = {
        "total_files": len(changed_files),
        "noise_filtered": len(noise_files),
        "reviewable_files": len(clean_files),
        "by_domain": {k: v for k, v in domain_counts.items() if v > 0},
        "unrecognized_source": unrecognized_source,
    }

    warnings = list(repo_warnings)
    if unrecognized_source:
        shown = ", ".join(unrecognized_source[:10])
        if len(unrecognized_source) > 10:
            shown += f", … (+{len(unrecognized_source) - 10} more)"
        warnings.append(
            f"UNRECOGNIZED SOURCE: {len(unrecognized_source)} changed file(s) use a "
            f"language no reviewer domain covers — these will NOT be reviewed: {shown}. "
            f"Review coverage is degraded; inspect them manually or extend "
            f"scope.py's language groups."
        )

    return {
        "mode": mode,
        "git_range": git_range,
        "output_dir": output_dir,
        "changed_files": clean_files,
        "scope_summary": scope_summary,
        "agents": dispatch_list,
        "agent_signals": agent_signals,
        "warnings": warnings,
    }


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Dispatch Planner — centralized review agent dispatch decisions.",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["full", "incremental", "pr"],
        help="Review mode: full, incremental, or pr.",
    )
    parser.add_argument(
        "--git-range",
        required=True,
        help="Git range to diff (e.g., 'main..HEAD').",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Output directory for review files.",
    )
    parser.add_argument(
        "--changed-files-list",
        default=None,
        help="Optional comma-separated list of changed files (overrides git diff).",
    )
    parser.add_argument(
        "--review-context",
        default=None,
        help="Path to review context for PR metadata triage.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        default=False,
        help="Quick review mode: exclude low-signal agents.",
    )
    parser.add_argument(
        "--host",
        choices=["claude", "codex"],
        default="claude",
        help=(
            "Dispatch host; Codex projects declared Claude model tiers to "
            "the effective inherit tier."
        ),
    )

    args = parser.parse_args()

    # Get changed files
    if args.changed_files_list:
        changed_files = parse_changed_files_list(args.changed_files_list)
    else:
        changed_files = get_changed_files_from_git(args.git_range)

    # Load review context for PR metadata triage
    review_context = _load_review_context(args.review_context)

    # Build plan
    plan = build_dispatch_plan(
        mode=args.mode,
        git_range=args.git_range,
        output_dir=args.output_dir,
        changed_files=changed_files,
        review_context=review_context,
        quick=args.quick,
        host=args.host,
    )

    # Output JSON to stdout (for inline parsing by commands)
    print(json.dumps(plan, indent=2))

    # Write to disk (for downstream scripts: agents_status.py)
    plan_path = artifact_path(args.output_dir, "dispatch_plan")
    os.makedirs(args.output_dir, exist_ok=True)
    plan_path.parent.mkdir(exist_ok=True)
    with open(plan_path, "w") as f:
        json.dump(plan, f, indent=2)


if __name__ == "__main__":
    main()

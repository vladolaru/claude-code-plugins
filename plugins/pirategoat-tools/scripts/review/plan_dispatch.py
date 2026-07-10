#!/usr/bin/env python3
"""
Dispatch Planner — Centralized review agent dispatch decisions.

Reads the agent registry and changed files to produce a deterministic
dispatch plan: which agents to run, which to skip, and why.

Replaces duplicated triage logic in command files with a single script.

Usage:
    python3 plan_dispatch.py --mode full --git-range "main..HEAD" --output-dir /tmp/review
    python3 plan_dispatch.py --mode incremental --git-range "abc123..HEAD" --output-dir /tmp/review
    python3 plan_dispatch.py --mode pr --git-range "main..HEAD" --output-dir /tmp/pr-review-42
    python3 plan_dispatch.py --mode full --git-range "main..HEAD" --output-dir /tmp/review --changed-files-list "a.py,b.ts"

Output: JSON dispatch plan on stdout.

Exit codes:
    0  Success — dispatch plan generated
    1  Error — details on stderr

Zero external dependencies (stdlib only).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
        _, _, ext = f.rpartition(".")
        if ext and ext.lower() in _SOURCE_EXTENSIONS:
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
    cmd = ["git", "diff", "--name-only", git_range]
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


def get_diff_text(git_range: str, files: Optional[List[str]] = None) -> str:
    """Get lowercased patch text from a git range for keyword triage."""
    cmd = ["git", "diff", git_range]
    if files:
        cmd.extend(["--", *files])
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return ""
        return result.stdout.lower()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


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
    """Get combined commit messages from a git range.

    Returns empty string on failure (fault-tolerant).
    """
    cmd = ["git", "log", "--format=%s%n%b", git_range]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return ""
        return result.stdout.strip().lower()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def get_repository_identity() -> str:
    """Get matchable identity text for the repository under review.

    The origin URL is the canonical signal when checkouts use arbitrary
    directory names. The Git top-level basename provides an offline fallback
    for repositories without an origin remote.

    Returns empty string on failure (fault-tolerant).
    """
    parts = []
    commands = (
        (["git", "remote", "get-url", "origin"], False),
        (["git", "rev-parse", "--show-toplevel"], True),
    )
    for cmd, basename_only in commands:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        if result.returncode != 0:
            continue
        value = result.stdout.strip()
        if not value:
            continue
        parts.append(Path(value).name if basename_only else value)
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
            ["git", "diff", "--numstat", git_range],
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
            ["git", "diff", "--diff-filter=A", "--name-only", git_range],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            added_files = result.stdout.strip().splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        result = subprocess.run(
            ["git", "diff", "--diff-filter=D", "--name-only", git_range],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            deleted_files = result.stdout.strip().splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        result = subprocess.run(
            ["git", "diff", "--diff-filter=R", "--name-only", git_range],
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


def _build_file_paths_text(file_paths: List[str]) -> str:
    """Convert file paths into matchable text for keyword triage.

    Replaces path separators, hyphens, and underscores with spaces so that
    path segments like 'payment-gateway' match keywords like 'payment'.

    Returns lowercased text. Empty string if no paths.
    """
    if not file_paths:
        return ""
    return " ".join(
        f.replace("/", " ").replace("-", " ").replace("_", " ")
        for f in file_paths
    ).lower()


def _match_keywords_multi_source(
    keywords: List[str],
    sources: List[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """Match keywords against multiple named text sources.

    Args:
        keywords: Keyword strings to search for (substring match).
        sources: List of (source_name, text) tuples to search in.

    Returns:
        List of (keyword, source_name) for each match. Each keyword
        is reported from the first source it matches.
    """
    matches = []
    for kw in keywords:
        for name, text in sources:
            if text and kw in text:
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


_SUPPORTED_TRIAGE_CHECKS = frozenset({
    "new_abstraction_files",
    "substantial_non_test_additions",
    "file_deletions",
    "net_removal",
    "large_pr",
    "has_new_functions",
    "has_modified_signatures",
    "has_docblock_changes",
    "has_documentation_files",
    "has_public_api_changes",
})

_SIGNATURE_PATTERNS = (
    re.compile(r"\bdef\s+[a-z_][a-z0-9_]*\s*\("),
    re.compile(r"\bfunction\s+[a-z_$][a-z0-9_$]*\s*\("),
    re.compile(r"\b(public|protected|private)\s+(static\s+)?function\s+[a-z_$][a-z0-9_$]*\s*\("),
    re.compile(r"\b(export\s+)?(async\s+)?function\s+[a-z_$][a-z0-9_$]*\s*\("),
    re.compile(r"\b(export\s+)?class\s+[a-z_$][a-z0-9_$]*\b"),
    re.compile(r"\b(export\s+)?interface\s+[a-z_$][a-z0-9_$]*\b"),
    re.compile(r"\b(export\s+)?enum\s+[a-z_$][a-z0-9_$]*\b"),
)

_PUBLIC_API_PATTERNS = (
    re.compile(r"\bexport\s+(async\s+)?function\s+[a-z_$][a-z0-9_$]*\s*\("),
    re.compile(r"\bexport\s+(class|interface|enum|type|const)\s+[a-z_$][a-z0-9_$]*\b"),
    re.compile(r"\bpublic\s+(static\s+)?function\s+[a-z_$][a-z0-9_$]*\s*\("),
    re.compile(r"\bregister_rest_route\s*\("),
    re.compile(r"\badd_(action|filter)\s*\("),
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
        if line.startswith("+++") or line.startswith("---"):
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


def _has_modified_signatures(diff_text: str) -> bool:
    return (
        _has_pattern_in_patch_lines(diff_text, ("+",), _SIGNATURE_PATTERNS)
        and _has_pattern_in_patch_lines(diff_text, ("-",), _SIGNATURE_PATTERNS)
    )


def _has_docblock_changes(diff_text: str) -> bool:
    for marker in ("+", "-"):
        for line in _iter_patch_lines(diff_text, marker):
            if any(token in line for token in _DOCBLOCK_MARKERS):
                return True
    return False


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


def _has_public_api_changes(diff_text: str) -> bool:
    return _has_pattern_in_patch_lines(diff_text, ("+", "-"), _PUBLIC_API_PATTERNS)


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
    diff_text: str = "",
    repository_text: str = "",
) -> Tuple[str, str]:
    """Apply deterministic triage for a conditional agent.

    Triage layers (first match wins):
    1. Test-only filter: if ALL domain files are test files → SKIPPED_TRIAGE
    2. Optional PHP-source gate: if configured and no PHP source files → SKIPPED_TRIAGE
    3. Keyword match: if triage_keywords match any signal source → DISPATCH
       Signal sources (checked in order): commit messages, file paths,
       repository identity, PR title/body, patch text
    4. Agent-specific checks (dead-code: deletions, net removal, structural signals) → DISPATCH
    5. Default: DISPATCH (conservative — when in doubt, dispatch)

    Args:
        agent_name: Name of the agent.
        config: Agent configuration from registry.
        domain_files: Files matching the agent's domain(s).
        commit_messages: Lowercased combined commit messages.
        diffstat: Diffstat summary dict.
        pr_text: Lowercased PR title + body (empty if unavailable).
        diff_text: Lowercased patch text (empty if unavailable).
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
        return "SKIPPED_TRIAGE", "all matching files are test files"

    in_scope_added = _count_in_scope_non_test_additions(domain_files, diffstat)

    # Gate: min_added_lines — skip if PR doesn't add enough code in non-test scope
    min_lines = config.get("min_added_lines", 0)
    if min_lines > 0 and in_scope_added < min_lines:
        return "SKIPPED_TRIAGE", f"below minimum addition threshold ({in_scope_added} < {min_lines} lines)"

    # Layer 2: Agent-wide source gate.
    if config.get("require_php_source_file") and not any(
        f.lower().endswith(".php") and not is_test_file(f)
        for f in domain_files
    ):
        return "SKIPPED_TRIAGE", "requires PHP source file"

    # Layer 3: Keyword match from triage_keywords against all signal sources
    keywords = config.get("triage_keywords", [])
    if keywords:
        file_paths_text = _build_file_paths_text(domain_files)
        sources = [
            ("commits", commit_messages),
            ("files", file_paths_text),
            ("repository", repository_text),
            ("pr", pr_text),
            ("diff", diff_text),
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
            return "DISPATCH", f"keywords matched ({'; '.join(reason_parts)})"
    if config.get("require_triage_keyword_match"):
        return "SKIPPED_TRIAGE", "requires triage keyword match; none found"

    # Layer 4: Agent-specific checks
    triage_checks = config.get("triage_checks", [])
    for check in triage_checks:
        if check == "new_abstraction_files":
            abstraction_files = _get_new_abstraction_files(domain_files, diffstat)
            if abstraction_files:
                return "DISPATCH", f"new abstraction file(s): {', '.join(abstraction_files[:3])}"
        elif check == "substantial_non_test_additions":
            if in_scope_added >= min_lines > 0:
                return "DISPATCH", f"substantial non-test additions in scope ({in_scope_added} lines)"
        elif check == "file_deletions":
            if diffstat.get("deleted_files") or diffstat.get("renamed_files"):
                count = len(diffstat.get("deleted_files", [])) + len(diffstat.get("renamed_files", []))
                return "DISPATCH", f"{count} file(s) deleted or renamed"
        elif check == "net_removal":
            if diffstat.get("removed", 0) > diffstat.get("added", 0):
                return "DISPATCH", f"net removal ({diffstat['removed']} removed > {diffstat['added']} added)"
        elif check == "large_pr":
            if len(domain_files) >= 20:
                return "DISPATCH", f"large change ({len(domain_files)} files in domain)"
        elif check == "has_new_functions":
            if _has_new_functions(diff_text):
                return "DISPATCH", "new function, method, or type definition"
        elif check == "has_modified_signatures":
            if _has_modified_signatures(diff_text):
                return "DISPATCH", "modified function or type signature"
        elif check == "has_docblock_changes":
            if _has_docblock_changes(diff_text):
                return "DISPATCH", "docblock or API comment changes"
        elif check == "has_documentation_files":
            if _has_documentation_files(domain_files):
                return "DISPATCH", "documentation file changes"
        elif check == "has_public_api_changes":
            if _has_public_api_changes(diff_text):
                return "DISPATCH", "public API surface changes"

    # Layer 4: Default — DISPATCH when no triage signal skips the agent.
    # Keywords and triage checks provide positive evidence, but conditional
    # agents still dispatch conservatively when their domain has files.
    return "DISPATCH", "conditional (domain has files, no triage signal to skip)"


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
    diff_text_cache: Optional[Dict[Tuple[str, ...], str]] = None,
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
        commit_messages: Lowercased combined commit messages.
        diffstat: Diffstat summary dict.
        pr_text: Lowercased PR title + body + labels + branch + issue titles.
        diff_text: Lowercased patch text.
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
        return "SKIPPED", f"no files in {domain_label} domain"

    # Always-dispatch agents: dispatch if domain has files
    if dispatch_class == "always":
        return "DISPATCH", "always dispatch (domain has files)"

    # Conditional agents: apply deterministic triage
    if dispatch_class == "conditional" and clean_files is not None:
        # Gather domain-matched files for triage
        domain_files = get_domain_files(clean_files, domain) if domain else []
        for sec_domain in config.get("secondary_domains", []):
            domain_files.extend(get_domain_files(clean_files, sec_domain))
        # Deduplicate
        domain_files = sorted(set(domain_files))
        if diff_text is None and git_range and config.get("triage_keywords"):
            cache_key = tuple(domain_files)
            if diff_text_cache is not None:
                if cache_key not in diff_text_cache:
                    diff_text_cache[cache_key] = get_diff_text(git_range, domain_files)
                diff_text = diff_text_cache[cache_key]
            else:
                diff_text = get_diff_text(git_range, domain_files)

        return triage_conditional_agent(
            agent_name, config, domain_files,
            commit_messages, diffstat or {},
            pr_text=pr_text,
            diff_text=diff_text or "",
            repository_text=repository_text,
        )

    # Conditional agents without triage context: dispatch by default
    if dispatch_class == "conditional":
        return "DISPATCH", "conditional (domain has files)"

    # Fallback
    return "DISPATCH", "default"


def _build_pr_text(review_context: Optional[dict]) -> str:
    """Build lowercased text from PR metadata for keyword triage.

    Combines PR title, body, labels, branch name, and linked issue titles
    into a single searchable text block.

    Args:
        review_context: Parsed review-context.json dict, or None.

    Returns:
        Lowercased combined text. Empty string if no context.
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
    return "\n".join(parts).lower()


def _load_review_context(path: Optional[str]) -> Optional[dict]:
    """Load review-context.json from disk. Returns None on any failure."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


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
        review_context: Parsed review-context.json dict (for PR metadata triage).
        quick: If True, exclude low-signal agents with SKIPPED_QUICK_MODE status.

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

    # Build stable context signals for keyword matching (fault-tolerant)
    pr_text = _build_pr_text(review_context)
    repository_text = get_repository_identity()

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
                and status == "DISPATCH"
                and reason in _LOW_SIGNAL_DISPATCH_REASONS):
            status = "SKIPPED_QUICK_MODE"
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
        if status == "DISPATCH":
            signal = f"{agent_name}: STATUS=DISPATCH"
            if reason != "always dispatch (domain has files)":
                signal += f" ({reason})"
        elif status == "SKIPPED_TRIAGE":
            signal = f"{agent_name}: STATUS=SKIPPED_TRIAGE ({reason})"
        else:
            signal = f"{agent_name}: STATUS=SKIPPED ({reason})"
        agent_signals.append(signal)

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

    warnings = []
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
        help="Path to review-context.json for PR metadata triage (title, body, labels, branch, issues).",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        default=False,
        help="Quick review mode: exclude low-signal agents.",
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
    )

    # Output JSON to stdout (for inline parsing by commands)
    print(json.dumps(plan, indent=2))

    # Write to disk (for downstream scripts: agents_status.py)
    plan_path = os.path.join(args.output_dir, "dispatch-plan.json")
    os.makedirs(args.output_dir, exist_ok=True)
    with open(plan_path, "w") as f:
        json.dump(plan, f, indent=2)


if __name__ == "__main__":
    main()

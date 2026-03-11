#!/usr/bin/env python3
"""
Dispatch Planner — Centralized review agent dispatch decisions.

Reads the agent registry and changed files to produce a deterministic
dispatch plan: which agents to run, which to skip, and why.

Replaces duplicated triage logic in command files with a single script.

Usage:
    python3 plan-review-dispatch.py --mode full --git-range "main..HEAD" --output-dir /tmp/review
    python3 plan-review-dispatch.py --mode incremental --git-range "abc123..HEAD" --output-dir /tmp/review
    python3 plan-review-dispatch.py --mode pr --git-range "main..HEAD" --output-dir /tmp/pr-review-42
    python3 plan-review-dispatch.py --mode full --git-range "main..HEAD" --output-dir /tmp/review --changed-files-list "a.py,b.ts"

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
# Import DOMAIN_CATALOG from review-scope.py (sibling script)
# =============================================================================

_SCRIPTS_DIR = Path(__file__).resolve().parent

# Use importlib to handle hyphenated filename
import importlib.util

_scope_spec = importlib.util.spec_from_file_location(
    "review_scope", str(_SCRIPTS_DIR / "review-scope.py")
)
_scope_mod = importlib.util.module_from_spec(_scope_spec)
_scope_spec.loader.exec_module(_scope_mod)

DOMAIN_CATALOG = _scope_mod.DOMAIN_CATALOG
filter_noise = _scope_mod.filter_noise
filter_domain = _scope_mod.filter_domain


# =============================================================================
# Registry loading
# =============================================================================

def load_registry(registry_path: Optional[str] = None) -> dict:
    """Load agent registry from agent-registry.json.

    Args:
        registry_path: Override path to registry file. Defaults to
                       agent-registry.json in the same directory as this script.

    Returns:
        Dict with "agents" key containing agent configurations.
    """
    if registry_path is None:
        registry_path = str(_SCRIPTS_DIR / "agent-registry.json")
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


def render_agent_signals_text(agent_signals: List[str]) -> str:
    """Render agent signals as the canonical text block for downstream steps.

    The review commands pass this text verbatim to reconcile-reviews.py as a
    single quoted --agent-signals argument and paste the same block into the
    reconciliator prompt.
    """
    return "\n".join(agent_signals)


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
_TEST_DOMAINS = ("php-tests", "js-tests", "e2e-tests", "go-tests")


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


def get_diffstat(git_range: str) -> Dict:
    """Get diffstat summary from a git range.

    Returns dict with:
        added: total lines added
        removed: total lines removed
        deleted_files: list of deleted file paths
        renamed_files: list of renamed file paths

    Returns zeros/empty on failure (fault-tolerant).
    """
    empty = {"added": 0, "removed": 0, "deleted_files": [], "renamed_files": []}

    # Get numstat for add/remove counts
    try:
        result = subprocess.run(
            ["git", "diff", "--numstat", git_range],
            capture_output=True, text=True, timeout=30,
        )
        added = 0
        removed = 0
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    try:
                        added += int(parts[0]) if parts[0] != "-" else 0
                        removed += int(parts[1]) if parts[1] != "-" else 0
                    except ValueError:
                        pass
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return empty

    # Get deleted/renamed files
    deleted_files = []
    renamed_files = []
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
        "deleted_files": deleted_files,
        "renamed_files": renamed_files,
    }


# =============================================================================
# Deterministic triage for conditional agents
# =============================================================================


def triage_conditional_agent(
    agent_name: str,
    config: dict,
    domain_files: List[str],
    commit_messages: str,
    diffstat: Dict,
) -> Tuple[str, str]:
    """Apply deterministic triage for a conditional agent.

    Triage layers (first match wins):
    1. Test-only filter: if ALL domain files are test files → SKIPPED_TRIAGE
    2. Keyword match: if triage_keywords match commit messages → DISPATCH
    3. Agent-specific checks (dead-code: deletions, net removal) → DISPATCH
    4. Default: DISPATCH (conservative — when in doubt, dispatch)

    Args:
        agent_name: Name of the agent.
        config: Agent configuration from registry.
        domain_files: Files matching the agent's domain(s).
        commit_messages: Lowercased combined commit messages.
        diffstat: Diffstat summary dict.

    Returns:
        (status, reason) where status is DISPATCH or SKIPPED_TRIAGE.
    """
    # Layer 1: Test-only filter
    # If every domain-matching file is a test file, skip the agent.
    # Conditional agents target production-code concerns; test-only diffs
    # don't need security/performance/architecture review.
    if domain_files and all(is_test_file(f) for f in domain_files):
        return "SKIPPED_TRIAGE", "all matching files are test files"

    # Layer 2: Keyword match from triage_keywords
    keywords = config.get("triage_keywords", [])
    if keywords and commit_messages:
        matched_kw = [kw for kw in keywords if kw in commit_messages]
        if matched_kw:
            return "DISPATCH", f"commit keywords matched: {', '.join(matched_kw[:3])}"

    # Layer 3: Agent-specific checks
    triage_checks = config.get("triage_checks", [])
    for check in triage_checks:
        if check == "file_deletions":
            if diffstat.get("deleted_files") or diffstat.get("renamed_files"):
                count = len(diffstat.get("deleted_files", [])) + len(diffstat.get("renamed_files", []))
                return "DISPATCH", f"{count} file(s) deleted or renamed"
        elif check == "net_removal":
            if diffstat.get("removed", 0) > diffstat.get("added", 0):
                return "DISPATCH", f"net removal ({diffstat['removed']} removed > {diffstat['added']} added)"
        elif check == "large_pr":
            if len(domain_files) >= 20:
                return "DISPATCH", f"large change ({len(domain_files)} files in domain)"

    # Layer 4: Default — DISPATCH when no triage signal skips the agent.
    # If the agent has keywords but none matched AND no special checks triggered,
    # it STILL dispatches by default. Keywords are an optimization, not a gate.
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
) -> Tuple[str, str]:
    """Decide whether to dispatch a single agent.

    For always-dispatch and manual/special agents, only domain file counts
    matter. For conditional agents, deterministic triage is applied using
    commit messages, diffstat, and test-file detection.

    Args:
        agent_name: Name of the agent.
        config: Agent configuration from registry.
        domain_counts: File counts per domain.
        clean_files: Full list of reviewable files (for domain matching).
        commit_messages: Lowercased combined commit messages.
        diffstat: Diffstat summary dict.

    Returns:
        (status, reason) tuple where status is "DISPATCH", "SKIPPED",
        or "SKIPPED_TRIAGE".
    """
    dispatch_class = config.get("dispatch_class", "conditional")
    domain = config.get("domain")

    # Manual and special agents are always skipped by the planner.
    if dispatch_class in ("manual", "special"):
        return "SKIPPED", f"{dispatch_class} only"

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

        return triage_conditional_agent(
            agent_name, config, domain_files,
            commit_messages, diffstat or {},
        )

    # Conditional agents without triage context: dispatch by default
    if dispatch_class == "conditional":
        return "DISPATCH", "conditional (domain has files)"

    # Fallback
    return "DISPATCH", "default"


def build_dispatch_plan(
    mode: str,
    git_range: str,
    output_dir: str,
    changed_files: List[str],
    registry: Optional[dict] = None,
    commit_messages: Optional[str] = None,
    diffstat: Optional[Dict] = None,
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

    # Build dispatch decisions
    dispatch_list = []
    agent_signals = []

    for agent_name in sorted(agents.keys()):
        config = agents[agent_name]
        status, reason = decide_agent_dispatch(
            agent_name, config, domain_counts,
            clean_files=clean_files,
            commit_messages=commit_messages,
            diffstat=diffstat,
        )

        entry = {
            "agent": agent_name,
            "domain": config.get("domain"),
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

    # Scope summary
    scope_summary = {
        "total_files": len(changed_files),
        "noise_filtered": len(noise_files),
        "reviewable_files": len(clean_files),
        "by_domain": {k: v for k, v in domain_counts.items() if v > 0},
    }

    return {
        "mode": mode,
        "git_range": git_range,
        "output_dir": output_dir,
        "changed_files": clean_files,
        "scope_summary": scope_summary,
        "dispatch": dispatch_list,
        "agent_signals": agent_signals,
        "agent_signals_text": render_agent_signals_text(agent_signals),
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

    args = parser.parse_args()

    # Get changed files
    if args.changed_files_list:
        changed_files = parse_changed_files_list(args.changed_files_list)
    else:
        changed_files = get_changed_files_from_git(args.git_range)

    # Build plan
    plan = build_dispatch_plan(
        mode=args.mode,
        git_range=args.git_range,
        output_dir=args.output_dir,
        changed_files=changed_files,
    )

    # Output JSON
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()

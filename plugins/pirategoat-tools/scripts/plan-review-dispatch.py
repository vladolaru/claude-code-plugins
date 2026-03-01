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


# =============================================================================
# Dispatch decision logic
# =============================================================================

def decide_agent_dispatch(
    agent_name: str,
    config: dict,
    domain_counts: Dict[str, int],
) -> Tuple[str, str]:
    """Decide whether to dispatch a single agent.

    Args:
        agent_name: Name of the agent.
        config: Agent configuration from registry.
        domain_counts: File counts per domain.

    Returns:
        (status, reason) tuple where status is "DISPATCH" or "SKIPPED".
    """
    dispatch_class = config.get("dispatch_class", "conditional")
    domain = config.get("domain")

    # Manual agents are always skipped
    if dispatch_class == "manual":
        return "SKIPPED", "manual only"

    # Check if the agent's domain has files
    has_domain_files = False
    if domain is None:
        # Agents with no domain (e.g., tests-mutation-reviewer) are manual,
        # but handle edge case gracefully
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

    # Conditional agents: domain has files, so dispatch.
    # The main value of the planner is skipping agents whose domain has
    # ZERO files. For conditional agents with files, we dispatch and let
    # the agent itself decide on relevance.
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
) -> dict:
    """Build the complete dispatch plan.

    Args:
        mode: Review mode ("full", "incremental", "pr").
        git_range: Git range used.
        output_dir: Output directory for review files.
        changed_files: List of changed file paths.
        registry: Agent registry dict. Loaded from default path if None.

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

    # Build dispatch decisions
    dispatch_list = []
    agent_signals = []

    for agent_name in sorted(agents.keys()):
        config = agents[agent_name]
        status, reason = decide_agent_dispatch(agent_name, config, domain_counts)

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
        "scope_summary": scope_summary,
        "dispatch": dispatch_list,
        "agent_signals": agent_signals,
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

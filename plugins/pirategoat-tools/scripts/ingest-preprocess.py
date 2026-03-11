#!/usr/bin/env python3
"""
Ingest Preprocessor - Deterministic scope checking and pre-classification.

Handles the mechanical parts of ingest (scope checking, ID assignment,
pre-classification) before the LLM-based verification steps, reducing
the ingest pipeline from 6 LLM steps to 3.

Usage:
    python3 scripts/ingest-preprocess.py \
        --output-dir "/tmp/branch-review-feature-x" \
        --git-range "main..HEAD"

Reads:  reconciled-structured.json or reconciled.json from output-dir
Writes: ingest-preprocessed.json to output-dir

Zero external dependencies (stdlib only).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

# Severity ordering for stable IDs (higher priority = lower index)
SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

# Lines within this distance of a hunk boundary are classified as
# INTERACTS_WITH_CHANGE rather than OUT_OF_SCOPE.
HUNK_PROXIMITY = 5


def parse_diff_hunks(diff_output: str) -> List[Tuple[int, int]]:
    """Parse @@ -a,b +c,d @@ headers into (start, end) line ranges for new file.

    Returns a list of (start_line, end_line) tuples representing the line
    ranges in the new file that were changed.
    """
    hunks = []
    for match in re.finditer(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff_output):
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) else 1
        hunks.append((start, start + count - 1))
    return hunks


def extract_source_agents(title: str) -> List[str]:
    """Extract source agent names from reconciled finding titles.

    Reconciled findings have titles like "[security] SQL Injection" or
    "[pr] Missing validation". Extract the agent name from brackets.
    """
    agents = []
    for match in re.finditer(r"\[([^\]]+)\]", title):
        agents.append(match.group(1))
    return agents if agents else ["unknown"]


def line_in_hunks(line: int, hunks: List[Tuple[int, int]]) -> bool:
    """Check if a line number falls within any of the diff hunks."""
    for start, end in hunks:
        if start <= line <= end:
            return True
    return False


def line_near_hunks(
    line: int, hunks: List[Tuple[int, int]], proximity: int = HUNK_PROXIMITY
) -> bool:
    """Check if a line is within *proximity* lines of any hunk boundary.

    Returns True when the line is NOT inside a hunk but IS close enough
    that the change could affect or be affected by it.
    """
    for start, end in hunks:
        if (start - proximity) <= line <= (end + proximity):
            return True
    return False


def classify_finding(
    finding: dict,
    changed_files: List[str],
    diff_hunks: Dict[str, List[Tuple[int, int]]],
) -> Tuple[str, str, str]:
    """Classify a single finding's scope status and pre-classification.

    Scope statuses (from most to least in-scope):
        IN_HUNK                — line is inside a changed hunk
        INTERACTS_WITH_CHANGE  — line is near a hunk boundary (within HUNK_PROXIMITY)
        FILE_LEVEL             — file is in the diff but finding has no line number
        OUT_OF_SCOPE           — file not in diff, or line far from all hunks

    Pre-classification values:
        verified           — corroborated by ground truth tools; skips LLM verification
        needs_verification — in scope but needs LLM confirmation
        out_of_scope       — outside changed code

    Returns:
        (scope_status, scope_reason, pre_classification)
    """
    file_path = finding.get("file", "")
    line = finding.get("line")

    # Check 0: Ground truth match — fast-track to verified
    is_gt_match = finding.get("ground_truth_match", False)

    # Check 1: Is the file in the diff?
    if file_path not in changed_files:
        return "OUT_OF_SCOPE", "file not in diff", "out_of_scope"

    # Determine classification: verified if ground truth matched, else needs_verification
    in_scope_class = "verified" if is_gt_match else "needs_verification"

    # Check 2: Does the finding have a line number?
    file_hunks = diff_hunks.get(file_path, [])

    if line is None:
        # No line number — file-level finding. Still needs verification but
        # flagged distinctly so downstream can be more skeptical.
        return "FILE_LEVEL", "file in diff, no line number", in_scope_class

    if not file_hunks:
        # File is in diff but we have no hunk data — conservative: treat as
        # file-level since we can't confirm hunk membership.
        return "FILE_LEVEL", "file in diff, no hunk data available", in_scope_class

    # Check 3: Is the line in a hunk?
    if line_in_hunks(line, file_hunks):
        return "IN_HUNK", "line in changed hunk", in_scope_class

    # Check 4: Is the line near a hunk? (interaction zone)
    if line_near_hunks(line, file_hunks):
        return "INTERACTS_WITH_CHANGE", f"line near changed hunk (within {HUNK_PROXIMITY} lines)", in_scope_class

    return "OUT_OF_SCOPE", "file in diff, line outside hunks (pre-existing code)", "out_of_scope"


def load_reconciled(output_dir: str) -> dict:
    """Load reconciled findings from output directory.

    Tries reconciled-structured.json first, falls back to reconciled.json.
    Raises FileNotFoundError if neither exists.
    """
    structured_path = os.path.join(output_dir, "reconciled-structured.json")
    fallback_path = os.path.join(output_dir, "reconciled.json")

    if os.path.exists(structured_path):
        with open(structured_path) as f:
            return json.load(f)
    elif os.path.exists(fallback_path):
        with open(fallback_path) as f:
            return json.load(f)
    else:
        raise FileNotFoundError(
            f"No reconciled findings found in {output_dir}. "
            f"Expected reconciled-structured.json or reconciled.json"
        )


def sort_findings(findings: list) -> list:
    """Sort findings by severity (desc), then file, then line for stable IDs."""
    def sort_key(f):
        severity_idx = SEVERITY_ORDER.get(f.get("severity", "medium"), 2)
        file_path = f.get("file", "")
        line = f.get("line") or 0
        return (severity_idx, file_path, line)

    return sorted(findings, key=sort_key)


def preprocess_findings(
    output_dir: str,
    changed_files: List[str],
    diff_hunks: Dict[str, List[Tuple[int, int]]],
    git_range: str,
) -> dict:
    """Main preprocessing logic — scope check, assign IDs, pre-classify.

    Args:
        output_dir: Path to the review output directory containing reconciled files.
        changed_files: List of files changed in the diff.
        diff_hunks: Dict mapping file paths to lists of (start, end) hunk ranges.
        git_range: The git range used for the diff.

    Returns:
        Preprocessed findings dict ready for JSON serialization.
    """
    reconciled = load_reconciled(output_dir)

    # Handle two output formats:
    # 1. reconcile-reviews.py (new): has flat "issues" list extracted from clusters
    # 2. reconcile-reviews.py (old) or reconciled-structured.json without step 8.5:
    #    has "clusters" with "canonical" sub-objects but no "issues" key
    raw_findings = reconciled.get("issues", [])
    if not raw_findings and "clusters" in reconciled:
        raw_findings = [
            c["canonical"]
            for c in reconciled.get("clusters", [])
            if "canonical" in c
        ]

    # Sort for stable ID assignment
    sorted_raw = sort_findings(raw_findings)

    # Process each finding
    processed_findings = []
    scope_counts = {"IN_HUNK": 0, "INTERACTS_WITH_CHANGE": 0, "FILE_LEVEL": 0, "OUT_OF_SCOPE": 0}
    needs_verification_count = 0
    verified_by_ground_truth_count = 0
    auto_classified_count = 0

    for idx, finding in enumerate(sorted_raw, start=1):
        scope_status, scope_reason, pre_classification = classify_finding(
            finding, changed_files, diff_hunks
        )

        # Prefer structured source_agents from reconciled output; fall back to
        # bracket-title extraction for legacy reconciled.json files.
        source_agents = finding.get("source_agents") or extract_source_agents(finding.get("title", ""))

        processed = {
            "id": f"F{idx}",
            "title": finding.get("title", ""),
            "file": finding.get("file", ""),
            "line": finding.get("line"),
            "severity": finding.get("severity", "medium"),
            "source_agents": source_agents,
            "confidence": finding.get("confidence", 0.9),
            "scope_status": scope_status,
            "scope_reason": scope_reason,
            "pre_classification": pre_classification,
            "description": finding.get("description", ""),
            "recommendation": finding.get("recommendation", ""),
            "category": finding.get("category", "general"),
        }

        processed_findings.append(processed)
        scope_counts[scope_status] = scope_counts.get(scope_status, 0) + 1

        if pre_classification == "verified":
            verified_by_ground_truth_count += 1
            auto_classified_count += 1
        elif pre_classification == "needs_verification":
            needs_verification_count += 1
        else:
            auto_classified_count += 1

    # in_scope = everything except OUT_OF_SCOPE (backward compatible)
    in_scope_count = sum(v for k, v in scope_counts.items() if k != "OUT_OF_SCOPE")
    out_of_scope_count = scope_counts["OUT_OF_SCOPE"]

    return {
        "git_range": git_range,
        "changed_files": changed_files,
        "findings": processed_findings,
        "summary": {
            "total": len(processed_findings),
            "in_scope": in_scope_count,
            "out_of_scope": out_of_scope_count,
            "by_scope_status": scope_counts,
            "needs_verification": needs_verification_count,
            "verified_by_ground_truth": verified_by_ground_truth_count,
            "auto_classified": auto_classified_count,
        },
    }


def run_git_cmd(cmd: List[str]) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd)}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def get_changed_files_from_git(git_range: str) -> List[str]:
    """Get changed files from git diff."""
    output = run_git_cmd(["git", "diff", "--name-only", git_range])
    if not output:
        return []
    return output.splitlines()


def get_diff_hunks_from_git(
    git_range: str, changed_files: List[str]
) -> Dict[str, List[Tuple[int, int]]]:
    """Get diff hunks for each changed file from git."""
    hunks = {}
    for filepath in changed_files:
        try:
            diff_output = run_git_cmd(
                ["git", "diff", git_range, "--", filepath]
            )
            hunks[filepath] = parse_diff_hunks(diff_output)
        except RuntimeError:
            hunks[filepath] = []
    return hunks


def run_preprocess(output_dir: str, git_range: str) -> dict:
    """Full preprocessing pipeline: git commands + preprocess_findings.

    This is the entry point for both CLI and programmatic use.
    """
    changed_files = get_changed_files_from_git(git_range)
    diff_hunks = get_diff_hunks_from_git(git_range, changed_files)

    result = preprocess_findings(
        output_dir=output_dir,
        changed_files=changed_files,
        diff_hunks=diff_hunks,
        git_range=git_range,
    )

    # Write output
    output_path = os.path.join(output_dir, "ingest-preprocessed.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Ingest Preprocessor — deterministic scope checking and pre-classification."
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        required=True,
        help="Path to review output directory containing reconciled findings.",
    )
    parser.add_argument(
        "--git-range",
        type=str,
        required=True,
        help='Git range for the diff (e.g., "main..HEAD").',
    )

    args = parser.parse_args()

    # Validate output dir exists
    if not os.path.isdir(args.output_dir):
        print(f"ERROR: Output directory does not exist: {args.output_dir}", file=sys.stderr)
        sys.exit(1)

    try:
        result = run_preprocess(args.output_dir, args.git_range)

        # Print summary
        s = result["summary"]
        print(f"INGEST PREPROCESS COMPLETE")
        print(f"  Total findings:      {s['total']}")
        print(f"  In scope:            {s['in_scope']}")
        print(f"  Out of scope:        {s['out_of_scope']}")
        print(f"  Needs verification:  {s['needs_verification']}")
        if s.get("verified_by_ground_truth", 0) > 0:
            print(f"  Verified (ground truth): {s['verified_by_ground_truth']}")
        print(f"  Auto-classified:     {s['auto_classified']}")
        print(f"  Output: {os.path.join(args.output_dir, 'ingest-preprocessed.json')}")

    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Reconciliation Context Builder — pre-gathers all context for the reconciliator agent.

Performance optimization: instead of the reconciliator making ~40 individual file reads,
this script collects all agent findings, referenced source snippets, scope annotations,
and metadata into a single JSON file the agent can consume immediately.

Usage:
    python3 reconciliation_context.py --output-dir /tmp/pr-review-42 --git-range abc123..HEAD
    python3 reconciliation_context.py --output-dir /tmp/pr-review-42 --git-range abc123..HEAD \
        --changed-files src/a.py,src/b.py --change-purpose "Fix auth bug" --pr-id 42

Exit codes:
    0  Success — reconciliation-context.json written
    1  Error — details on stderr

Zero external dependencies (stdlib only).
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent

# Files in the output directory that are NOT agent review outputs.
# These are pipeline infrastructure files that should be skipped when
# loading agent findings.
_NON_REVIEW_FILES = frozenset([
    "dispatch-plan.json",
    "review-findings.json",
    "review-context.json",
    "pipeline-state.json",
    "run-config.json",
    "pipeline-result.json",
    "decision-critic-verdict.json",
    "clarity-assessment.json",
    "reconciliation-context.json",
])


def load_agent_findings(output_dir: str) -> Dict[str, Any]:
    """Load all agent review JSON files from the output directory.

    Reads all *-review.json files, skipping pipeline infrastructure files
    listed in _NON_REVIEW_FILES. Malformed JSON files are skipped with a
    warning on stderr.

    Returns:
        Dict keyed by agent name (e.g., "security-review") with the parsed
        JSON content as the value.
    """
    findings = {}
    output_path = Path(output_dir)

    if not output_path.is_dir():
        print(f"WARNING: output directory does not exist: {output_dir}", file=sys.stderr)
        return findings

    for entry in sorted(output_path.iterdir()):
        if not entry.name.endswith("-review.json"):
            continue
        if entry.name in _NON_REVIEW_FILES:
            continue

        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
            # Key by filename without .json extension (e.g., "security-review")
            agent_name = entry.stem
            findings[agent_name] = data
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: skipping malformed file {entry.name}: {exc}", file=sys.stderr)

    return findings


def extract_references(agent_findings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract unique file:line references from all agent issues.

    Scans the 'issues' list in each agent's findings and collects unique
    file paths with their referenced line numbers.

    Returns:
        List of dicts, each with:
        - "file": str — the file path as reported by the agent
        - "lines": List[int] — sorted, deduplicated line numbers

        Skips issues without a valid integer 'line' field.
    """
    file_lines: Dict[str, set] = {}

    for _agent_name, data in agent_findings.items():
        issues = data.get("issues", [])
        if not isinstance(issues, list):
            continue

        for issue in issues:
            if not isinstance(issue, dict):
                continue
            file_path = issue.get("file")
            line = issue.get("line")

            if not file_path or not isinstance(file_path, str):
                continue
            if not isinstance(line, int) or line <= 0:
                continue

            if file_path not in file_lines:
                file_lines[file_path] = set()
            file_lines[file_path].add(line)

    return [
        {"file": fp, "lines": sorted(lines)}
        for fp, lines in sorted(file_lines.items())
    ]


def read_source_snippets(
    references: List[Dict[str, Any]], context_lines: int = 10
) -> Dict[str, str]:
    """Read source code snippets around referenced lines.

    For each referenced file, reads ±context_lines around each line number.
    Overlapping windows are merged. Missing files are skipped gracefully.

    Returns:
        Dict mapping absolute file paths to snippet text with line numbers.
        Format: "  42 | code here\\n  43 | more code\\n..."
    """
    snippets: Dict[str, str] = {}

    for ref in references:
        file_path = ref["file"]
        lines = ref["lines"]

        # Resolve to absolute path
        abs_path = str(Path(file_path).resolve())

        if not os.path.isfile(abs_path):
            # Try as-is (might already be absolute)
            if not os.path.isfile(file_path):
                continue
            abs_path = file_path

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                source_lines = f.readlines()
        except OSError:
            continue

        total_lines = len(source_lines)
        if total_lines == 0:
            continue

        # Build merged windows
        windows = []
        for line_num in lines:
            start = max(1, line_num - context_lines)
            end = min(total_lines, line_num + context_lines)
            windows.append((start, end))

        # Merge overlapping windows
        merged = _merge_windows(windows)

        # Extract snippets
        snippet_parts = []
        for start, end in merged:
            for i in range(start, end + 1):
                line_text = source_lines[i - 1].rstrip("\n")
                snippet_parts.append(f"{i:>6} | {line_text}")

        if snippet_parts:
            snippets[abs_path] = "\n".join(snippet_parts)

    return snippets


def _merge_windows(windows: List[tuple]) -> List[tuple]:
    """Merge overlapping (start, end) windows into non-overlapping ranges."""
    if not windows:
        return []

    sorted_windows = sorted(windows)
    merged = [sorted_windows[0]]

    for start, end in sorted_windows[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            # Overlapping or adjacent — merge
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged


def check_scope(
    references: List[Dict[str, Any]],
    changed_files: List[str],
    git_range: str,
) -> Dict[str, str]:
    """Annotate each referenced file as IN_SCOPE or OUT_OF_SCOPE.

    Uses suffix matching to handle absolute vs relative paths. A file is
    considered in-scope if any changed_files entry is a suffix of the
    referenced file path (or vice versa).

    Args:
        references: Output of extract_references().
        changed_files: List of file paths from the diff.
        git_range: Git range string (unused, reserved for future use).

    Returns:
        Dict mapping file paths to scope status strings.
    """
    annotations: Dict[str, str] = {}

    for ref in references:
        file_path = ref["file"]
        if _file_in_changed(file_path, changed_files):
            annotations[file_path] = "IN_SCOPE"
        else:
            annotations[file_path] = "OUT_OF_SCOPE:file_not_in_diff"

    return annotations


def _file_in_changed(file_path: str, changed_files: List[str]) -> bool:
    """Check if file_path matches any entry in changed_files using suffix matching."""
    # Normalize separators
    norm_path = file_path.replace("\\", "/")

    for changed in changed_files:
        norm_changed = changed.replace("\\", "/")
        if norm_path == norm_changed:
            return True
        if norm_path.endswith("/" + norm_changed) or norm_changed.endswith("/" + norm_path):
            return True

    return False


def resolve_output_builder_path() -> str:
    """Return the path to the ReviewOutputBuilder script.

    The script knows its own location relative to the output builder.
    """
    return str(SCRIPTS_DIR / "agent" / "output.py")


if __name__ == "__main__":
    sys.exit(1)  # Not yet implemented

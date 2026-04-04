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


if __name__ == "__main__":
    sys.exit(1)  # Not yet implemented

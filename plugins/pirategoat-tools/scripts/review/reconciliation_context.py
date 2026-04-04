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


if __name__ == "__main__":
    sys.exit(1)  # Not yet implemented

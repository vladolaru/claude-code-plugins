"""Tests for review/reconciliation_context.py — deterministic, no model calls.

Tests the reconciliation context builder by importing functions directly
and by running the full script via subprocess for integration tests.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "review" / "reconciliation_context.py"


def _load_module():
    """Load the reconciliation_context module via importlib."""
    spec = importlib.util.spec_from_file_location(
        "reconciliation_context", str(SCRIPT_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    """Module-scoped import of reconciliation_context."""
    return _load_module()


# ---------------------------------------------------------------------------
# Test fixture helpers
# ---------------------------------------------------------------------------

def _make_issue(
    severity="medium",
    title="Test issue",
    file="src/app.py",
    line=42,
    description="Some issue found",
    recommendation="Fix it",
    category="general",
    confidence=0.9,
):
    """Create a single issue dict matching ReviewOutputBuilder format."""
    issue = {
        "id": "abc12345",
        "category": category,
        "severity": severity,
        "title": title,
        "description": description,
        "file": file,
        "line": line,
        "recommendation": recommendation,
        "confidence": confidence,
    }
    return issue


def _make_review_json(
    reviewer="security",
    pr_id="42",
    verdict="comment",
    issues=None,
):
    """Create a complete review JSON dict matching ReviewOutputBuilder output."""
    if issues is None:
        issues = [_make_issue()]

    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for issue in issues:
        sev = issue.get("severity", "medium")
        if sev in severity_counts:
            severity_counts[sev] += 1

    return {
        "pr_id": pr_id,
        "reviewer": reviewer,
        "timestamp": "2026-04-04T10:00:00",
        "version": "1.0.0",
        "verdict": verdict,
        "summary": {
            "total_issues": len(issues),
            "by_severity": severity_counts,
        },
        "issues": issues,
        "observations": None,
        "recommendations": None,
        "positive_observations": None,
        "meta": {
            "files_reviewed": 3,
            "review_duration_ms": 1500,
            "confidence_score": 0.95,
            "tool_results_used": None,
        },
    }

"""Integration test: ReviewOutputBuilder → check-status → reconcile.

Verifies the full output contract: a real ReviewOutputBuilder.save() output
is correctly discovered by check-reviewer-agent-status.py and reconcile-reviews.py.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))
from review_output_simple import ReviewOutputBuilder


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def status_mod():
    return _load_module("check_status", SCRIPTS_DIR / "check-reviewer-agent-status.py")


# Import reconcile directly (already on sys.path via importlib)
_recon_spec = importlib.util.spec_from_file_location(
    "reconcile_reviews", str(SCRIPTS_DIR / "reconcile-reviews.py")
)
_recon_mod = importlib.util.module_from_spec(_recon_spec)
_recon_spec.loader.exec_module(_recon_mod)
reconcile = _recon_mod.reconcile


class TestOutputContractIntegration:
    """Real ReviewOutputBuilder output flows through status check and reconciliation."""

    def _write_dispatch_plan(self, tmp_path, agents):
        plan = {"agents": [{"name": a, "status": "DISPATCH"} for a in agents]}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

    def _build_and_save(self, output_dir, reviewer_name, issues):
        builder = ReviewOutputBuilder(pr_id="42", reviewer=reviewer_name)
        for issue in issues:
            builder.add_issue(**issue)
        return builder.save(output_dir)

    def test_single_agent_flows_through(self, status_mod, tmp_path):
        """security-reviewer: build → save → status check → reconcile."""
        self._write_dispatch_plan(tmp_path, ["security-reviewer"])

        # Build and save using ReviewOutputBuilder (reviewer="security")
        self._build_and_save(str(tmp_path), "security", [
            {"severity": "high", "title": "XSS in form", "file": "form.php",
             "line": 42, "description": "Unescaped output", "recommendation": "Use esc_html()"},
        ])

        # Verify file exists where we expect
        assert (tmp_path / "security-review.json").is_file()

        # Status check should find it
        result = status_mod.check_status(str(tmp_path))
        agent = result["agents"][0]
        assert agent["status"] == "FINISHED", f"Got {agent['status']}"
        assert agent["counts"].get("high", 0) == 1

        # Reconciliation should include the finding
        recon = reconcile(output_dir=str(tmp_path))
        assert recon["total_findings"] == 1
        assert recon["clusters"][0]["canonical"]["title"] == "XSS in form"

    def test_multiple_agents_flow_through(self, status_mod, tmp_path):
        """Multiple agents: all discovered, all reconciled."""
        agents = ["security-reviewer", "pr-reviewer", "architecture-reviewer"]
        self._write_dispatch_plan(tmp_path, agents)

        self._build_and_save(str(tmp_path), "security", [
            {"severity": "high", "title": "SQL injection", "file": "db.php",
             "line": 10, "description": "Raw query", "recommendation": "Use $wpdb->prepare()"},
        ])
        self._build_and_save(str(tmp_path), "pr", [
            {"severity": "medium", "title": "Missing validation", "file": "api.php",
             "line": 20, "description": "No input check", "recommendation": "Add sanitization"},
        ])
        self._build_and_save(str(tmp_path), "architecture", [
            {"severity": "low", "title": "Tight coupling", "file": "service.php",
             "line": 5, "description": "Direct dependency", "recommendation": "Use interface"},
        ])

        # Status: all three FINISHED
        result = status_mod.check_status(str(tmp_path))
        statuses = {a["name"]: a["status"] for a in result["agents"]}
        assert statuses == {
            "security-reviewer": "FINISHED",
            "pr-reviewer": "FINISHED",
            "architecture-reviewer": "FINISHED",
        }

        # Reconciliation: 3 findings, 3 clusters (all different files)
        recon = reconcile(output_dir=str(tmp_path))
        assert recon["total_findings"] == 3
        assert recon["deduplicated_findings"] == 3

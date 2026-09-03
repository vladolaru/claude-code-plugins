"""One complete telemetry run, written through the real producers.

The sharing tests need a run whose manifest and JSONL carry every section
and event a finished pipeline emits — reviewer lifecycle events, the
assignment, usage, synthesis, hygiene, dependency-refresh, skipped-step and
outcome sections — because the disclosure ratchet can only see the string
surface the fixture produces. A start-plus-finalize skeleton left most of
that surface unexercised.

Every undisclosed value the run records carries one fragment from
``RECORDED_UNDISCLOSED``, so a test can prove the fixture is not hollow
(each fragment is present in the raw payloads) and that redaction did its
job (none survives in the redacted ones).
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers.review_fixtures import (
    canonical_findings_ledger,
    canonical_review_document,
)
from review import dependency_refresh, synthesis_lifecycle
from review.reviewer_lifecycle import review_paths, started_marker_path
from review.run_paths import artifact_path
from review.telemetry import ReviewTelemetry

# One fragment per undisclosed value the run records: PR title, author,
# link, linked issue, head branch, session id, planner triage reason and
# signal, the orchestrator's override reason, a not-applicable reviewer's
# skip reason, the step-10 decision reason, a dependency-precheck dirty
# file, a dependency-refresh command, a worktree-hygiene new file, and a
# scratch file in the run root.
RECORDED_UNDISCLOSED = (
    "Fix checkout tax rounding",
    "third-party-author",
    "pull/42",
    "ACME-9",
    "customer-rounding",
    "local-session-1234",
    "keywords matched",
    "acme-signal",
    "acme-hot-path",
    "customer-acme fixture",
    "customer-acme quick",
    "customer-acme-todo",
    "customer-acme-secret",
    "customer-acme-scratch",
    "customer-acme-notes",
)

CHANGED_FILES = ["src/checkout.py", "src/tax.py", "docs/pricing.md", "pnpm-lock.yaml"]
# The lock file is noise-filtered; the doc is reviewable but no agent's scope
# contains it, so both assignment gap populations are non-empty.
REVIEWABLE_FILES = ["src/checkout.py", "src/tax.py", "docs/pricing.md"]
SCOPE_BY_AGENT = {
    "security-reviewer": ["src/checkout.py", "src/tax.py"],
    "performance-reviewer": ["src/checkout.py"],
}
SEVERITIES_BY_AGENT = {
    "security-reviewer": ["high"],
    "performance-reviewer": [],
}
SKIP_REASON = "No PHP tests changed; customer-acme fixture only"


def _write_artifact(output_dir, key, payload):
    path = artifact_path(str(output_dir), key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _usage(output):
    return {
        "input_tokens": 10,
        "cache_creation_input_tokens": 2,
        "cache_read_input_tokens": 3,
        "effective_input_tokens": 15,
        "output_tokens": output,
    }


def write_complete_run(repo, output_dir, log_dir, *, run_id):
    """Drive ``ReviewTelemetry`` through a whole PR review; return its paths."""
    output_dir = Path(output_dir)
    _write_artifact(output_dir, "review_context", {
        "pr": {
            "number": 42,
            "title": "Fix checkout tax rounding for enterprise-customer",
            "author": "third-party-author",
            "url": "https://github.com/acme/widget/pull/42",
        },
        # Every field telemetry._extract_context() reads is present.
        "git": {
            "git_range": "main..HEAD",
            "base_ref": "main",
            "head_ref": "fix/ACME-9-enterprise-customer-rounding",
            "changed_files": CHANGED_FILES,
            "commit_count": 2,
        },
        "pr_size": {"category": "small"},
        "linked_issues": ["ACME-9"],
        "source": "github",
        "mode": "pr",
    })
    _write_artifact(output_dir, "run_config", {
        "mode": "pr", "refresh_dependencies": True,
    })
    planner_agents = [
        {
            "name": "security-reviewer", "domain": "security",
            "model_tier": "sonnet", "status": "DISPATCH",
            "reason": "keywords matched (title: enterprise-customer)",
        },
        {
            "name": "performance-reviewer", "domain": "performance",
            "model_tier": "sonnet", "status": "SKIPPED_TRIAGE",
            "reason": "no triage criteria matched",
        },
        {
            "name": "php-tests-reviewer", "domain": "php-tests",
            "model_tier": "sonnet", "status": "SKIPPED",
            "reason": SKIP_REASON,
        },
    ]
    _write_artifact(output_dir, "dispatch_plan_initial", {
        "changed_files": REVIEWABLE_FILES,
        "agent_signals": ["security-reviewer:keyword:acme-signal"],
        "agents": planner_agents,
    })
    final_agents = json.loads(json.dumps(planner_agents))
    final_agents[1].update({
        "status": "DISPATCH",
        "override_reason": "orchestrator override: acme-hot-path",
    })
    _write_artifact(output_dir, "dispatch_plan", {
        "changed_files": REVIEWABLE_FILES,
        "agent_signals": ["security-reviewer:keyword:acme-signal"],
        "agents": final_agents,
    })

    telemetry = ReviewTelemetry(str(output_dir), log_dir=str(log_dir))
    telemetry.start(
        mode="pr",
        repo_path=str(repo),
        identifier="42",
        run_id=run_id,
        session_id="local-session-1234",
        plugin_version="1.116.0",
    )
    telemetry.log_step(step=3, phase="SETUP", title="Gather Context")
    _write_artifact(output_dir, "pipeline_state", {
        "dependency_refresh_precheck": {
            "tracked_files_dirty": True,
            "dirty_files": ["notes/customer-acme-todo.md"],
        },
    })
    request = output_dir / "tmp" / "dependency-refresh-request.json"
    request.parent.mkdir(exist_ok=True)
    request.write_text(json.dumps({
        "schema": 1,
        "status": "completed",
        "commands": [{
            "directory": ".",
            "command": "npm ci --prefix ~/customer-acme-secret",
            "exit_status": "ok",
        }],
    }), encoding="utf-8")
    assert dependency_refresh.save_report(str(output_dir), request, str(repo)) == []
    telemetry.log_step(step=6, phase="EXECUTION", title="Dispatch Agents")

    for name, scope in SCOPE_BY_AGENT.items():
        reviewer = name.removesuffix("-reviewer")
        marker = Path(started_marker_path(str(output_dir), reviewer))
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now(timezone.utc).isoformat())
        telemetry.log_agent_start(
            name,
            domain=reviewer,
            model_tier="sonnet",
            scope_files=len(scope),
            scope_lines=40 * len(scope),
            budget_target=15,
            scope_paths=scope,
        )
        document = canonical_review_document(
            reviewer,
            SEVERITIES_BY_AGENT[name],
            reviewed_file_claims=scope,
            review_claimable_files=scope,
        )
        serialized = json.dumps(document)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        telemetry.log_agent_review_draft_saved(name, digest)
        final = Path(review_paths(str(output_dir), reviewer).final)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text(serialized, encoding="utf-8")
        telemetry.log_agent_complete(
            name,
            digest,
            verdict=document["verdict"],
            finding_count=document["summary"]["total_findings"],
            severities=document["summary"]["by_severity"],
        )

    synthesis_lifecycle.mark_dispatched(
        str(output_dir),
        synthesis_lifecycle.RECONCILIATOR,
        now=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    _write_artifact(output_dir, "review_findings_json", canonical_findings_ledger(
        ["high"],
        reconciliation={
            "contributing_agent_count": 1,
            "reviewing_agents": ["security-review", "performance-review"],
            "dispatched_agents": ["security-review", "performance-review"],
            "not_applicable_agents": [
                {"name": "php-tests-reviewer", "skip_reason": SKIP_REASON},
            ],
        },
    ))
    assert synthesis_lifecycle.observe(str(output_dir), finalize=True) is not None
    telemetry.log_step(
        step=10, phase="SYNTHESIS", title="Decision Critic",
        decisions={
            "critic_skipped": True,
            "reason": "quick mode + reconciliation verdict (customer-acme quick)",
        },
    )

    _write_artifact(output_dir, "pipeline_result", {
        "status": "success",
        "verdict": "REQUEST_CHANGES",
        "critic_verdict": "SKIPPED",
        "verdict_source": "findings ledger",
    })
    _write_artifact(output_dir, "pipeline_state", {
        "dependency_refresh_precheck": {
            "tracked_files_dirty": True,
            "dirty_files": ["notes/customer-acme-todo.md"],
        },
        "reviewer_markdown": {
            "ran": True, "written": 2, "expected": 2, "status": "complete",
        },
        "findings_markdown": {
            "ran": True, "written": 1, "expected": 1, "status": "complete",
        },
        "skipped_steps": [
            {"step": 2, "title": "Repo Setup", "condition": "needs_workspace_setup"},
        ],
    })
    _write_artifact(output_dir, "worktree_hygiene", {
        "schema": 1,
        "status": "changed_during_review",
        "new_files": ["?? notes/customer-acme-scratch.md"],
        "changed_files": [" M src/checkout.py"],
        "probe_residue_removed": ["zz_pirategoat-probe.go"],
        "baseline_captured_at": "2026-09-01T10:24:00+00:00",
    })
    _write_artifact(output_dir, "usage_snapshot", {
        "schema": 1,
        "captured_at": "2026-09-01T10:43:00+00:00",
        "window": {
            "started_at": "2026-09-01T10:24:00+00:00",
            "ended_at": "2026-09-01T10:43:00+00:00",
            "closed": True,
        },
        "availability": {"subagents": "complete", "orchestrator": "partial"},
        "reason": None,
        "agents_measured": {"measured": 2, "expected": 2},
        "subagent_usage": [
            {"agent": name, "model": "claude-sonnet-5", "usage": _usage(output)}
            for name, output in (("security-reviewer", 5), ("performance-reviewer", 2))
        ],
        "subagent_totals": _usage(7),
        "usage_by_model": {"claude-sonnet-5": _usage(7)},
        "orchestrator_usage": _usage(9),
    })
    (output_dir / "scratch-customer-acme-notes.txt").write_text("x", encoding="utf-8")
    telemetry.finalize(step=12, phase="OUTPUT", title="Complete review")

    return {
        "log_path": Path(telemetry.log_path),
        "manifest_path": Path(telemetry.manifest_path),
        "output_dir": output_dir,
        "repo": Path(repo),
        "run_id": run_id,
    }

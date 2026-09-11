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
from review import critic_adjustments, dependency_refresh, synthesis_lifecycle
from review.findings_ledger import DROP_REASONS_FINDING, NOTE_OUTCOMES
from review.verdict_rules import VALID_SEVERITIES
from review.reviewer_lifecycle import review_paths, started_marker_path
from review.run_paths import artifact_path
from review.telemetry import ReviewTelemetry

# One fragment per undisclosed value the run records: PR title, author,
# link, linked issue, head branch, session id, planner triage reason and
# signal, the orchestrator's override reason, a not-applicable reviewer's
# skip reason, the step-10 decision reason, a dependency-precheck dirty
# file, a dependency-refresh command, a worktree-hygiene new file, a scratch
# file in the run root, and the undisclosed base ref from range truth.
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
    "origin/main",
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


from helpers.review_fixtures import write_artifact as _write_artifact  # noqa: E402


def _usage(output):
    return {
        "input_tokens": 10,
        "cache_creation_input_tokens": 2,
        "cache_read_input_tokens": 3,
        "effective_input_tokens": 15,
        "output_tokens": output,
    }


def write_evidence_artifacts(output_dir, ledger, *, verdict="REVISE", verdict_before="block"):
    """Exercise the evidence vocabulary through canonical on-disk artifacts."""
    fields_by_action = {
        "promote": {"severity": "high"}, "demote": {"severity": "low"},
        "rescope": {"file": "private.php", "line": 12},
        "correct": {"title": "private prose"}, "remove": {},
        "add": {key: ("low" if key == "severity" else "private prose")
                for key in critic_adjustments.ADD_REQUIRED_FIELDS},
    }
    proposal = critic_adjustments.prepare_proposal({
        "schema": critic_adjustments.ADJUSTMENTS_SCHEMA,
        "adjustments": [{
            "action": action, "target": {"kind": "finding", **({} if action == "add" else {"id": f"f{index}"})},
            "fields": fields_by_action[action],
            "rationale": "private rationale",
        } for index, action in enumerate(critic_adjustments.ACTIONS, 1)] if verdict == "REVISE" else [],
    })
    ledger["dropped_findings"] = [{
        "reviewer": "performance-review", "id": f"f{index}", "reason": reason, "evidence": "private evidence",
    } for index, reason in enumerate(DROP_REASONS_FINDING, 1)]
    ledger["dropped_checks"] = [{"reviewer": "security-review", "id": "c2", "reason": "void", "evidence": "private evidence"}]
    ledger["orchestrator_notes"] = [{"id": f"n{index}", "note": "private note", "outcome": outcome, "evidence": "private evidence"}
                                    for index, outcome in enumerate(NOTE_OUTCOMES, 1)]
    ledger["checks"] = [{"id": "c1", "question": "private question", "method": "private method", "result": "private result",
                         "source_reviewers": ["security-review"], "verifies": ["V1"]}]
    ledger["meta"]["next_check_number"] = 2
    for index, finding in enumerate(ledger["findings"], 1):
        finding["sources"] = [{"reviewer": "security-review", "id": f"f{index}", "severity": finding["severity"]}]
    if proposal["adjustments"]:
        ledger["findings"][0]["critic_adjustment"] = {"action": "demote", "rationale": "private rationale"}
        ledger["verdict_before_adjustments"] = verdict_before
        ledger["applied_critic_adjustments"] = []
        ledger["rejected_critic_adjustments"] = []
        for index, entry in enumerate(proposal["adjustments"]):
            outcome = critic_adjustments.OUTCOMES[index % len(critic_adjustments.OUTCOMES)]
            record = {"adjustment_id": entry["adjustment_id"], "outcome": outcome}
            if outcome == "refuted":
                record.update(action=entry["action"], target=entry["target"], rejection_reason="private reason")
                ledger["rejected_critic_adjustments"].append(record)
            else:
                ledger["applied_critic_adjustments"].append(record)
    critic_adjustments.validate_findings_document(ledger)
    _write_artifact(output_dir, "review_findings_json", ledger)
    critic_adjustments.write_critic_verdict(str(output_dir), verdict, proposal)
    purpose = artifact_path(str(output_dir), "change_purpose")
    purpose.parent.mkdir(parents=True, exist_ok=True)
    purpose.write_text("## Verify\nV1. private purpose — source: PR body\n## Context\nNone.\n")


def write_complete_run(repo, output_dir, log_dir, *, run_id):
    """Drive ``ReviewTelemetry`` through a whole PR review; return its paths."""
    output_dir = Path(output_dir)
    _write_artifact(output_dir, "review_context", {
        "host_context": {
            "resolved": [{
                "name": "wordpress", "kind": "runtime-host",
                "path": str(repo / "wordpress"), "source": "ecosystem-cache",
                "version": "7.2", "version_freshness": "2026-09-04T00:04:08Z",
                "notes": {
                    "commit": "abc123", "branch": "trunk",
                    "commit_date": "2026-09-04T18:35:44Z",
                    "declared_minimum": "7.0",
                },
            }],
            "unresolved": [{
                "name": "jetpack", "reason": "declared_in_plugin_headers",
                "version": "14.1",
            }],
            "banner": {"reason": "partial_unresolved"},
            "diagnostics": {"self_provided": ["woocommerce"], "scan_roots": 2},
        },
        "pr": {
            "number": 42,
            "title": "Fix checkout tax rounding for enterprise-customer",
            "author": "third-party-author",
            "url": "https://github.com/acme/widget/pull/42",
        },
        # Every field telemetry._extract_context() reads is present.
        "git": {
            "git_range": "main..fix/ACME-9-enterprise-customer-rounding",
            "base_ref": "main",
            "head_ref": "fix/ACME-9-enterprise-customer-rounding",
            "changed_files": CHANGED_FILES,
            "commit_count": 2,
            "base_fetch": {
                "ref": "origin/main", "status": "fetched",
                "sha": "a" * 40, "shallow": False,
            },
            "scope_check": {
                "status": "match", "github_changed_files": 4,
                "local_changed_files": 4, "head_matches": True,
                "base_matches": True, "extra_local_files": [],
                "missing_local_files": [],
            },
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
            "signal": "keyword",
        },
        {
            "name": "performance-reviewer", "domain": "performance",
            "model_tier": "sonnet", "status": "SKIPPED_TRIAGE",
            "reason": "no triage criteria matched",
            "signal": "evidence_gate",
        },
        {
            "name": "php-tests-reviewer", "domain": "php-tests",
            "model_tier": "sonnet", "status": "SKIPPED",
            "reason": SKIP_REASON,
            "signal": "no_domain_files",
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
        plugin_commit="0123abcd",
        git_range="main..fix/ACME-9-enterprise-customer-rounding",
        base_sha="a" * 40,
        head_sha="b" * 40,
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
            scope_inline_lines=25 * len(scope),
            budget_target=15,
            scope_paths=scope,
        )
        document = canonical_review_document(
            reviewer,
            SEVERITIES_BY_AGENT[name],
            reviewed_file_claims=scope,
            review_claimable_files=scope,
        )
        for finding in document["findings"]:
            finding["source_cited"] = "wordpress@private-identity:private/source.php:12"
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
    ledger = canonical_findings_ledger(
        VALID_SEVERITIES,
        reconciliation={
            "contributing_agent_count": 1,
            "reviewing_agents": ["security-review", "performance-review"],
            "dispatched_agents": ["security-review", "performance-review"],
            "not_applicable_agents": [
                {"name": "php-tests-reviewer", "skip_reason": SKIP_REASON},
            ],
        },
    )
    write_evidence_artifacts(output_dir, ledger)
    assert synthesis_lifecycle.observe(str(output_dir), finalize=True) is not None
    telemetry.log_step(
        step=10, phase="SYNTHESIS", title="Decision Critic",
        decisions={
            "critic_skipped": False,
            "reason": "reconciliation requires critique (customer-acme quick)",
        },
    )

    _write_artifact(output_dir, "pipeline_result", {
        "status": "success",
        "verdict": "REQUEST_CHANGES",
        "critic_verdict": "REVISE",
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
            {
                "agent": name,
                "model": "claude-sonnet-5",
                "usage": _usage(output),
                "tool_calls": tool_calls,
                "repository_reads": repository_reads,
            }
            for name, output, tool_calls, repository_reads in (
                ("security-reviewer", 5, 12, 4),
                ("performance-reviewer", 2, 8, 3),
            )
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

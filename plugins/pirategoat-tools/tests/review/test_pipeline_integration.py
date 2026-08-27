"""Tests for review/orchestration.py through the pipeline.py compatibility facade."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
_SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(_SCRIPTS_DIR))
from review import (
    agents_status,
    critic_adjustments,
    dependency_refresh,
    reviewer_lifecycle,
)
from review.agent.review_assignment import derive_reviewed_files
from review.reconciliation_context import aggregate_file_review
from review.critic_adjustments import write_findings
from review.telemetry import ReviewTelemetry
from review.verdict_rules import verdict_for_counts

sys.path.insert(0, str(TESTS_DIR))
from helpers.pipeline_process import (
    add_commit as _add_commit,
    hermetic_env,
    init_repo as _init_git_repo,
    run_pipeline,
)
from helpers.review_fixtures import (
    canonical_findings_ledger,
    canonical_review_document,
    failing_findings_renderer,
)
_dispatch_spec = importlib.util.spec_from_file_location(
    "plan_review_dispatch", str(_SCRIPTS_DIR / "review" / "plan_dispatch.py")
)
_dispatch_mod = importlib.util.module_from_spec(_dispatch_spec)
_dispatch_spec.loader.exec_module(_dispatch_mod)

build_dispatch_plan = _dispatch_mod.build_dispatch_plan
load_registry = _dispatch_mod.load_registry

_output_spec = importlib.util.spec_from_file_location(
    "pipeline_integration_review_output",
    str(_SCRIPTS_DIR / "review" / "agent" / "output.py"),
)
_output_mod = importlib.util.module_from_spec(_output_spec)
_output_spec.loader.exec_module(_output_mod)
_render_markdown = _output_mod.render_markdown


def _write_critic_snapshot(output_dir, adjustments):
    """Publish one digest-bound REVISE snapshot and return its ids."""
    proposal = critic_adjustments.prepare_proposal({
        "schema": 2, "adjustments": adjustments,
    })
    critic_adjustments.write_critic_verdict(
        str(output_dir), "REVISE", proposal
    )
    return [entry["adjustment_id"] for entry in proposal["adjustments"]]


def _write_required_assignment(output_dir, reviewer, agent_name=None):
    Path(
        output_dir, f"{reviewer}-assignment.json"
    ).write_text(json.dumps({
        "schema": 4,
        "agent_name": agent_name or f"{reviewer}-reviewer",
        "reviewer": reviewer,
        "review_claimable_files": [],
        "review_budget": 15,
        "inline_diff_file_count": 1,
        "in_scope_review_file_count": 1,
        "channels": ["blocking"],
    }))


def _save_and_finalize(output_dir, reviewer, agent_name=None):
    _write_required_assignment(output_dir, reviewer, agent_name)
    saved = _output_mod.ReviewOutputBuilder.open(
        output_dir, "42", reviewer
    ).save_draft()
    return _output_mod.finalize_review(
        str(output_dir), reviewer, saved["review_digest"]
    )


@pytest.fixture(scope="module")
def mod(pipeline_mod):
    return pipeline_mod


def _publish_step_11(output_dir, cwd, mode="pr"):
    """Prepare without a report, then publish the authored report."""
    report = Path(output_dir) / "review-report.md"
    report_text = report.read_text() if report.is_file() else "# Review"
    report.unlink(missing_ok=True)
    prepared = run_pipeline(
        "--step", "11", "--mode", mode,
        "--output-dir", str(output_dir), cwd=cwd,
    )
    assert prepared.returncode == 0, prepared.stderr
    report.write_text(report_text)
    return run_pipeline(
        "--step", "11", "--mode", mode,
        "--output-dir", str(output_dir), cwd=cwd,
    )


def _review_json(reviewer):
    """Return the reviewer's schema-2 final review, or the schema-3 ledger."""
    if reviewer in ("review-reconciliator", "reconciliator"):
        return canonical_findings_ledger()
    return canonical_review_document(reviewer)


class TestReviewerDraftFinalizationLifecycle:
    def test_last_draft_is_the_only_synthesis_input(
        self, tmp_path
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        (output_dir / "dispatch-plan.json").write_text(json.dumps({
            "agents": [{
                "name": "code-reviewer",
                "domain": "code",
                "status": "DISPATCH",
                "reason": "always",
            }],
            "git_range": "HEAD~1..HEAD",
        }))
        (output_dir / "code-reviewer.started").write_text(
            datetime.now(timezone.utc).isoformat()
        )
        assignment = {
            "schema": 4,
            "agent_name": "code-reviewer",
            "reviewer": "code",
            "review_claimable_files": [
                "claimable/read.py",
                "claimable/unread.py",
            ],
            "review_budget": 15,
            "inline_diff_file_count": 1,
            "in_scope_review_file_count": 3,
            "channels": ["blocking"],
        }
        (output_dir / "code-assignment.json").write_text(
            json.dumps(assignment)
        )
        (output_dir / "code-reviewer-scope-summary.json").write_text(
            json.dumps({
                "schema": 2,
                "domain": "code",
                "status": "OK",
                "inline_diff_files": ["second.txt"],
                "review_claimable_files": [
                    "claimable/read.py",
                    "claimable/unread.py",
                ],
                "list_only_files": [],
                "in_scope_review_files": [
                    "second.txt",
                    "claimable/read.py",
                    "claimable/unread.py",
                ],
            })
        )

        telemetry = ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "telemetry")
        )
        telemetry.start(
            mode="full",
            repo_path=str(repo),
            identifier="draft-lifecycle",
            git_range="HEAD~1..HEAD",
        )
        telemetry.log_agent_start(
            "code-reviewer", domain="code", scope_files=3
        )

        builder = _output_mod.ReviewOutputBuilder.open(
            str(output_dir), "42", "code"
        )
        first = builder.save_draft()
        first_bytes = Path(first["draft"]).read_bytes()
        assert agents_status.check_status(str(output_dir))["all_done"] is False

        builder.claim_files_reviewed("claimable/read.py")
        last = builder.save_draft()
        last_bytes = Path(last["draft"]).read_bytes()
        assert last["review_digest"] != first["review_digest"]
        assert last_bytes != first_bytes
        assert agents_status.check_status(str(output_dir))["all_done"] is False

        finalized = _output_mod.finalize_review(
            str(output_dir), "code", last["review_digest"]
        )
        assert agents_status.check_status(str(output_dir))["all_done"] is True
        canonical_path = output_dir / "code-review.json"
        canonical_bytes = canonical_path.read_bytes()
        assert canonical_bytes == last_bytes
        assert hashlib.sha256(canonical_bytes).hexdigest() == (
            finalized["review_digest"]
        )

        intake = reviewer_lifecycle.close_review_intake(
            str(output_dir), ["code-reviewer"]
        )
        assert intake["discarded_drafts"] == []
        written = _output_mod.materialize_markdown(str(output_dir))
        assert written == [str(output_dir / "code-review.md")]

        reconciliation = subprocess.run(
            [
                sys.executable,
                str(_SCRIPTS_DIR / "review" / "reconciliation_context.py"),
                "--output-dir", str(output_dir),
                "--git-range", "HEAD~1..HEAD",
                "--changed-files",
                "second.txt,claimable/read.py,claimable/unread.py",
                "--pr-id", "42",
                "--dispatched-agents", "code-reviewer",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert reconciliation.returncode == 0, reconciliation.stderr

        telemetry.finalize(step=11, phase="OUTPUT", title="Finalize")
        manifest = json.loads(Path(telemetry.manifest_path).read_text())
        canonical = json.loads(canonical_bytes)
        reviewed_files = derive_reviewed_files(
            assignment, ["claimable/read.py"], reviewer=assignment["reviewer"]
        )
        events = [
            json.loads(line)
            for line in Path(telemetry.log_path).read_text().splitlines()
        ]
        saves = [
            index for index, event in enumerate(events)
            if event["event"] == "agent_review_draft_saved"
        ]
        completions = [
            index for index, event in enumerate(events)
            if event["event"] == "agent_complete"
        ]

        assert len(saves) == 2
        assert len(completions) == 1
        assert completions[0] > saves[-1]
        assert canonical["reviewed_file_claims"] == list(
            reviewed_files.reviewed_file_claims
        )
        assert canonical["unclaimed_review_files"] == list(
            reviewed_files.unclaimed_review_files
        )
        assert canonical["reviewed_file_count"] == (
            reviewed_files.reviewed_file_count
        )
        run_file_review = aggregate_file_review(
            str(output_dir),
            changed_files=[
                "second.txt", "claimable/read.py", "claimable/unread.py",
            ],
        )
        assert run_file_review["agents_claiming_review_by_file"] == {
            "claimable/read.py": ["code-reviewer"],
        }
        assert run_file_review["agents_with_unclaimed_review_by_file"] == {
            "claimable/unread.py": ["code-reviewer"],
        }
        assert (output_dir / "code-review.md").read_text() == (
            _render_markdown(canonical)
        )
        assert manifest["status"] == "complete"
        assert manifest["agents"]["completed"][0]["review_digest"] == (
            last["review_digest"]
        )
        assert list(output_dir.glob("*-review.draft.json")) == []
        assert list(output_dir.glob("*.tmp")) == []


class TestCriticAdjudicationLifecycle:
    def test_committed_proposal_is_settled_and_published_once(
        self, tmp_path
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        output_dir = tmp_path / "out"
        started = run_pipeline(
            "--step", "1", "--mode", "full", "--pr-number", "42",
            "--interactive", "false", "--output-dir", str(output_dir),
            cwd=repo, env=hermetic_env(),
        )
        assert started.returncode == 0, started.stderr

        ledger = _review_json("reconciliator")
        ledger["findings"] = [
            {
                "id": finding_id,
                "category": "general",
                "severity": "low",
                "title": f"Finding {finding_id}",
                "file": "second.txt",
                "line": 1,
                "description": "description",
                "recommendation": "recommendation",
                "confidence": 0.9,
            }
            for finding_id in ("f1", "f2", "f3")
        ]
        ledger["summary"] = {
            "total_findings": 3,
            "by_severity": {
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 3,
                "info": 0,
            },
            "suppressed_advisory_finding_count": 0,
        }
        ledger["verdict"] = verdict_for_counts(
            ledger["summary"]["by_severity"]
        )
        ledger["meta"]["next_finding_number"] = 4
        write_findings(str(output_dir), ledger)

        critic_findings = tmp_path / "critic-findings.md"
        critic_findings.write_text("# Decision critic\n\nThree proposals.\n")
        proposal_request = tmp_path / "critic-proposal.json"
        proposal_request.write_text(json.dumps({
            "schema": 2,
            "adjustments": [
                {
                    "action": "promote",
                    "target": {"kind": "finding", "id": "f1"},
                    "fields": {"severity": "high"},
                    "rationale": "The impact is release-blocking.",
                },
                {
                    "action": "demote",
                    "target": {"kind": "finding", "id": "f2"},
                    "fields": {"severity": "info"},
                    "rationale": "The impact is informational.",
                },
                {
                    "action": "promote",
                    "target": {"kind": "finding", "id": "f3"},
                    "fields": {"severity": "medium"},
                    "rationale": "The impact warrants follow-up.",
                },
            ],
        }))
        saved = subprocess.run(
            [
                sys.executable,
                str(_SCRIPTS_DIR / "review" / "critic.py"),
                "--save",
                "--output-dir", str(output_dir),
                "--verdict", "REVISE",
                "--findings", str(critic_findings),
                "--adjustments", str(proposal_request),
            ],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert saved.returncode == 0, saved.stdout + saved.stderr

        proposal = json.loads(
            (output_dir / "decision-critic-adjustments.json").read_text()
        )
        marker = json.loads(
            (output_dir / "decision-critic-verdict.json").read_text()
        )
        proposal_ids = [
            entry["adjustment_id"] for entry in proposal["adjustments"]
        ]
        assert len(set(proposal_ids)) == 3
        assert marker == {
            "schema": 2,
            "verdict": "REVISE",
            "proposal_digest": critic_adjustments.proposal_digest(proposal),
        }

        adjudication = critic_adjustments.adjudicate(str(output_dir), {
            "schema": 2,
            "verified": [proposal_ids[0]],
            "refuted": [{
                "adjustment_id": proposal_ids[1],
                "rejection_reason": "The source probe refuted the premise.",
            }],
            "revised_assessment": (
                "One promotion was verified, one demotion was refuted, "
                "and one promotion was not checked."
            ),
        })
        settled_proposal_path = (
            output_dir / "decision-critic-adjustments.json"
        )
        settled_proposal = json.loads(settled_proposal_path.read_text())
        assert adjudication["counts"] == {
            "verified": 1,
            "refuted": 1,
            "not_checked": 1,
        }
        assert settled_proposal == proposal, (
            "the proposal is never rewritten by adjudication"
        )
        assert critic_adjustments.proposal_digest(settled_proposal) == (
            marker["proposal_digest"]
        )

        settled_ledger_path = output_dir / "review-findings.json"
        settled_ledger = json.loads(settled_ledger_path.read_text())
        applied = settled_ledger[
            critic_adjustments.APPLIED_IDS_KEY
        ]
        rejected = settled_ledger[
            critic_adjustments.REJECTED_ADJUSTMENTS_KEY
        ]
        accounted_ids = [
            entry["adjustment_id"] for entry in applied + rejected
        ]
        assert sorted(accounted_ids) == sorted(proposal_ids)
        assert len(accounted_ids) == len(set(accounted_ids))
        assert settled_ledger["verdict_before_adjustments"] == (
            ledger["verdict"]
        )
        assert settled_ledger["verdict"] == verdict_for_counts(
            settled_ledger["summary"]["by_severity"]
        )
        assert settled_ledger["summary"]["by_severity"] == {
            "critical": 0,
            "high": 1,
            "medium": 1,
            "low": 1,
            "info": 0,
        }

        proposal_bytes = settled_proposal_path.read_bytes()
        ledger_bytes = settled_ledger_path.read_bytes()
        prepared = run_pipeline(
            "--step", "11", "--mode", "full",
            "--output-dir", str(output_dir), cwd=repo, env=hermetic_env(),
        )
        assert prepared.returncode == 0, prepared.stderr
        assert settled_proposal_path.read_bytes() == proposal_bytes
        assert settled_ledger_path.read_bytes() == ledger_bytes
        state = json.loads((output_dir / "pipeline-state.json").read_text())
        assert "critic_adjudication_missing" not in state.get(
            "degradation", {}
        )
        assert not any(
            "without orchestrator adjudication" in note
            for note in state.get("degradation_notes", [])
        )
        record = (output_dir / "review-record.md").read_text()
        assert "request_changes" in record
        assert all(adjustment_id in record for adjustment_id in proposal_ids)

        report = output_dir / "review-report.md"
        report.write_text(
            "# Review\n\nREQUEST_CHANGES: the settled ledger has one high "
            "and one medium finding.\n"
        )
        published = run_pipeline(
            "--step", "11", "--mode", "full",
            "--output-dir", str(output_dir), cwd=repo, env=hermetic_env(),
        )
        assert published.returncode == 0, published.stderr
        assert settled_proposal_path.read_bytes() == proposal_bytes
        assert settled_ledger_path.read_bytes() == ledger_bytes
        result = json.loads((output_dir / "pipeline-result.json").read_text())
        assert result["verdict"] == "REQUEST_CHANGES"
        assert result["report_path"] == str(report)
        telemetry_log = Path(
            (output_dir / ".telemetry-log-path").read_text().strip()
        )
        manifest = json.loads(
            telemetry_log.with_suffix(".manifest.json").read_text()
        )
        assert manifest["status"] == "complete"
        assert list(output_dir.glob("*.tmp")) == []
        assert list(output_dir.glob("*candidate*")) == []
        assert not any(
            "adjudication" in path.name and path != settled_proposal_path
            for path in output_dir.iterdir()
        )


class TestDependencyRefreshSaveLifecycle:
    def test_adaptive_refresh_report_is_saved_and_consumed_once(
        self, orchestration_mod, tmp_path
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        output_dir = tmp_path / "out"
        environment = hermetic_env()
        started = run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(output_dir),
            "--git-range", "HEAD~1..HEAD", "--refresh-deps",
            cwd=repo, env=environment,
        )
        assert started.returncode == 0, started.stderr
        briefed = run_pipeline(
            "--step", "3", "--mode", "full",
            "--output-dir", str(output_dir),
            cwd=repo, env=environment,
        )
        assert briefed.returncode == 0, briefed.stderr
        assert "dependency_refresh.py" in briefed.stdout
        assert " save " in briefed.stdout
        assert "SAVED dependency-refresh.json" in briefed.stdout
        for manager_command in (
            "npm install",
            "pnpm install",
            "yarn install",
            "composer install",
        ):
            assert manager_command not in briefed.stdout
        assert "write dependency-refresh.json" not in briefed.stdout.lower()

        request = tmp_path / "dependency-refresh-request.json"
        request.write_text(json.dumps({
            "schema": 1,
            "status": "completed",
            "commands": [{
                "directory": ".",
                "command": "custom-refresh --lockfile-preserving",
                "exit_status": "ok",
            }],
        }))
        saved = subprocess.run(
            [
                sys.executable,
                str(_SCRIPTS_DIR / "review" / "dependency_refresh.py"),
                "save",
                "--output-dir", str(output_dir),
                "--report", str(request),
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert saved.returncode == 0, saved.stderr
        assert saved.stdout.strip() == "SAVED dependency-refresh.json"
        canonical_path = output_dir / "dependency-refresh.json"
        canonical = json.loads(canonical_path.read_text())
        assert canonical == {
            "schema": 1,
            "status": "completed",
            "commands": [{
                "directory": ".",
                "command": "custom-refresh --lockfile-preserving",
                "exit_status": "ok",
            }],
            "tracked_files_dirty": False,
            "dirty_files": [],
        }

        consumed = run_pipeline(
            "--step", "5", "--mode", "full",
            "--output-dir", str(output_dir),
            cwd=repo, env=environment,
        )
        assert consumed.returncode == 0, consumed.stderr
        state = json.loads((output_dir / "pipeline-state.json").read_text())
        assert state["dependency_refresh_precheck"] == {
            "tracked_files_dirty": False,
            "dirty_files": [],
        }
        assert state["dependency_refresh_report"] == canonical

        write_findings(
            str(output_dir), _review_json("review-reconciliator")
        )
        record_outcome, record_error = orchestration_mod.assemble_review_record(
            str(output_dir), state,
            orchestration_mod.critic_adjustments.read_findings_file(
                str(output_dir / "review-findings.json")
            ),
        )
        assert record_error is None
        assert record_outcome == {
            "ran": True,
            "written": 1,
            "expected": 1,
            "status": "complete",
        }
        record = (output_dir / "review-record.md").read_text()
        assert (
            "Dependency refresh: completed; 1 command(s) reported; "
            "final tracked files dirty: false."
        ) in record

        telemetry = ReviewTelemetry(str(output_dir))
        telemetry.finalize(step=11, phase="OUTPUT", title="Finalize")
        manifest = json.loads(Path(telemetry.manifest_path).read_text())
        assert manifest["status"] == "complete"
        assert manifest["dependency_refresh"] == {
            "requested": True,
            "reported": True,
            "status": "completed",
            "commands": canonical["commands"],
            "tracked_files_dirty": False,
            "dirty_files": [],
        }

        assert request.parent != output_dir
        assert list(output_dir.glob("*verification*.json")) == []
        assert not (output_dir / request.name).exists()
        assert [
            path.name
            for path in output_dir.iterdir()
            if path.name.startswith("dependency-refresh")
        ] == ["dependency-refresh.json"]
        serialized = json.dumps({"state": state, "manifest": manifest})
        for retired in (
            "dependency_refresh_verification",
            "dependency-refresh-verification",
            "suggested_command",
            "installed_state_present",
            "commands_allowed",
            "disallowed_commands",
            "verification_failed",
        ):
            assert retired not in serialized


class TestTelemetryIntegration:
    """Verify pipeline calls telemetry at each step."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path):
        """Every subprocess call in this class needs an isolated cwd — see
        run_pipeline's docstring. The repo lives at `tmp_path/repo`, never
        at `tmp_path` itself: `tmp_path/out` is `--output-dir` for the rest
        of the class, and the allowlist sweep deletes any subdirectory it
        doesn't recognize (including a nested repo) — output-dir and repo
        must be siblings, not ancestor/descendant. Tests that need a
        specific git identity build their own `repo` subdir the same way."""
        (tmp_path / "repo").mkdir()
        _init_git_repo(tmp_path / "repo")
        (tmp_path / "out").mkdir()

    def test_step_1_creates_telemetry_log(self, tmp_path):
        """Step 1 should create a telemetry log and running manifest."""
        out = tmp_path / "out"
        log_dir = tmp_path / "telemetry-logs"
        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            r = run_pipeline(
                "--step", "1", "--mode", "pr",
                "--output-dir", str(out), "--pr-number", "42",
                cwd=tmp_path / "repo",
            )
        assert r.returncode == 0
        marker = out / ".telemetry-log-path"
        assert marker.is_file()
        log_path = Path(marker.read_text().strip())
        manifest_path = log_path.with_suffix(".manifest.json")
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["status"] == "running"

    def test_telemetry_failure_does_not_break_pipeline(self, tmp_path):
        """Pipeline works even if telemetry log_dir is unwritable."""
        out = tmp_path / "out"
        log_dir = tmp_path / "unwritable"
        log_dir.mkdir()
        log_dir.chmod(0o000)
        try:
            with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
                r = run_pipeline(
                    "--step", "1", "--mode", "pr",
                    "--output-dir", str(out), "--pr-number", "42",
                    cwd=tmp_path / "repo",
                )
            assert r.returncode == 0
            assert "Step 1" in r.stdout
        finally:
            log_dir.chmod(0o755)

    def test_step_2_appends_to_telemetry_log(self, tmp_path):
        """Subsequent steps append to the log created by step 1."""
        out = tmp_path / "out"
        log_dir = tmp_path / "telemetry-logs"
        env = {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}
        with patch.dict(os.environ, env):
            run_pipeline("--step", "1", "--mode", "pr",
                          "--output-dir", str(out), "--pr-number", "42",
                          cwd=tmp_path / "repo")
            run_pipeline("--step", "3", "--mode", "pr",
                         "--output-dir", str(out), cwd=tmp_path / "repo")
        marker = out / ".telemetry-log-path"
        log_path = marker.read_text().strip()
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "pipeline_start"
        assert json.loads(lines[1])["event"] == "step"

    def test_step_1_uses_preserved_bot_context_git_identity(self, tmp_path):
        """Bot-provided range and full SHAs survive into pipeline_start.

        The bot computes merge_base via `git merge-base` and head_sha via
        `git rev-parse HEAD`, so its context values are always full SHAs and
        pass through verbatim. Symbolic context values (an explicit range like
        "main..HEAD" stores "main" as merge_base) are resolved instead — a
        durable manifest must never record a movable ref as base_sha.
        """
        out = tmp_path / "out"
        context_base = "a" * 40
        context_head = "b" * 40
        (out / "run-config.json").write_text(json.dumps({
            "mode": "pr",
            "pr_number": "42",
            "interactive": False,
            "session_id": "bot-session",
        }))
        (out / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "context-base..context-head",
                "merge_base": context_base,
                "head_sha": context_head,
            },
        }))
        log_dir = tmp_path / "telemetry-logs"

        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            result = run_pipeline("--step", "1", "--output-dir", str(out), cwd=tmp_path / "repo")

        assert result.returncode == 0
        log_path = (out / ".telemetry-log-path").read_text().strip()
        with open(log_path) as f:
            start = json.loads(f.readline())
        assert start["pipeline"]["git"] == {
            "requested_range": "context-base..context-head",
            "base_sha": context_base,
            "head_sha": context_head,
        }

    def test_step_1_resolves_symbolic_context_merge_base(self, tmp_path):
        """A symbolic context merge_base (explicit "main..HEAD" range) must be
        resolved to a commit SHA before entering the durable run identity."""
        out = tmp_path / "out"
        repo = tmp_path / "repo"
        subprocess.run(
            ["git", "branch", "-M", "main"],
            cwd=repo, capture_output=True, check=True,
        )
        main_sha = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
        (out / "run-config.json").write_text(json.dumps({
            "mode": "full",
            "interactive": False,
            "session_id": "bot-session",
            "git_range": "main..HEAD",
        }))
        (out / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "main..HEAD",
                "merge_base": "main",
                "head_ref": "HEAD",
            },
        }))
        log_dir = tmp_path / "telemetry-logs"

        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            result = run_pipeline(
                "--step", "1", "--output-dir", str(out), cwd=repo
            )

        assert result.returncode == 0
        log_path = (out / ".telemetry-log-path").read_text().strip()
        with open(log_path) as f:
            start = json.loads(f.readline())
        git_identity = start["pipeline"]["git"]
        assert git_identity["requested_range"] == "main..HEAD"
        assert git_identity["base_sha"] == main_sha
        assert git_identity["head_sha"] == main_sha

    def test_step_1_interactive_run_ignores_stale_context_git_identity(self, tmp_path):
        """Interactive reruns do not leak the prior run's preserved Git identity."""
        out = tmp_path / "out"
        repo = tmp_path / "repo"
        (out / "run-config.json").write_text(json.dumps({
            "mode": "full",
            "interactive": True,
        }))
        (out / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": "stale-base..stale-head",
                "merge_base": "stale-base-sha",
                "head_sha": "stale-head-sha",
            },
        }))
        log_dir = tmp_path / "telemetry-logs"
        current_head = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=repo, text=True
        ).strip()

        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            result = run_pipeline(
                "--step", "1", "--output-dir", str(out), cwd=str(repo)
            )

        assert result.returncode == 0
        log_path = (out / ".telemetry-log-path").read_text().strip()
        with open(log_path) as f:
            start = json.loads(f.readline())
        assert start["pipeline"]["git"] == {
            "requested_range": "",
            "base_sha": "",
            "head_sha": current_head,
        }
        manifest = json.loads(Path(log_path).with_suffix(".manifest.json").read_text())
        assert manifest["run"]["git"] == start["pipeline"]["git"]
        assert json.loads((out / "review-context.json").read_text()) == {
            "output": {"directory": str(out)},
        }

    def test_step_1_interactive_range_resolves_current_git_not_stale_context(self, tmp_path):
        """An explicit interactive range resolves Git even when stale context matches it."""
        out = tmp_path / "out"
        repo = tmp_path / "repo"
        git_range = "HEAD~1..HEAD~1"
        (out / "run-config.json").write_text(json.dumps({
            "mode": "full",
            "interactive": True,
            "git_range": git_range,
        }))
        (out / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": git_range,
                "merge_base": "stale-base-sha",
                "head_sha": "stale-head-sha",
            },
        }))
        log_dir = tmp_path / "telemetry-logs"
        _add_commit(repo)
        expected_sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD~1"], cwd=repo, text=True
        ).strip()

        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            result = run_pipeline(
                "--step", "1", "--output-dir", str(out), cwd=str(repo)
            )

        assert result.returncode == 0
        log_path = (out / ".telemetry-log-path").read_text().strip()
        with open(log_path) as f:
            start = json.loads(f.readline())
        assert start["pipeline"]["git"] == {
            "requested_range": git_range,
            "base_sha": expected_sha,
            "head_sha": expected_sha,
        }

    def test_incremental_context_uses_step_1_output_seed_for_baseline(
        self, tmp_path
    ):
        # _isolated_repo (autouse) already initialized tmp_path/repo as a repo.
        out = tmp_path / "out"
        repo = tmp_path / "repo"
        baseline_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        (out / ".branch-review-baseline.json").write_text(json.dumps({
            "last_reviewed_sha": baseline_sha,
        }))
        log_dir = tmp_path / "telemetry-logs"

        with patch.dict(
            os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}
        ):
            step_1 = run_pipeline(
                "--step", "1", "--mode", "incremental",
                "--output-dir", str(out), cwd=repo,
            )
            seeded_context = json.loads(
                (out / "review-context.json").read_text()
            )
            step_3 = run_pipeline(
                "--step", "3", "--output-dir", str(out), cwd=repo,
            )

        assert step_1.returncode == 0
        assert seeded_context == {"output": {"directory": str(out)}}
        assert step_3.returncode == 0
        context = json.loads((out / "review-context.json").read_text())
        assert context["output"]["directory"] == str(out)
        assert context["git"]["merge_base"] == baseline_sha
        assert context["git"]["git_range"] == f"{baseline_sha}..HEAD"
        assert (out / ".branch-review-baseline.json").is_file()



class TestStep2Orchestration:
    """Step 2 main() runs review/workspace_setup.py and persists workspace state."""

    def test_step_2_completes_without_crash(self, tmp_path):
        """Step 2 should complete even when review/workspace_setup.py fails (no git repo)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        out = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(out), "--pr-number", "42", cwd=str(repo))
        r = run_pipeline("--step", "2", "--mode", "pr",
                       "--output-dir", str(out), cwd=str(repo))
        assert r.returncode == 0
        state = json.loads((out / "pipeline-state.json").read_text())
        assert 2 in state["completed_steps"]

    def test_step_2_stores_workspace_setup_result(self, tmp_path):
        """Step 2 should store workspace_setup_result in state."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        out = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(out), "--pr-number", "42", cwd=str(repo))
        run_pipeline("--step", "2", "--mode", "pr",
                       "--output-dir", str(out), cwd=str(repo))
        state = json.loads((out / "pipeline-state.json").read_text())
        assert "workspace_setup_result" in state


class TestStep3Orchestration:
    """Step 3 main() runs review/context.py and hydrates state."""

    def test_step_3_runs_gather_context(self, tmp_path):
        """Step 3 should invoke review/context.py (may fail in test env, but state should update)."""
        # Seed step 1
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        # Run step 3
        r = run_pipeline("--step", "3", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        # State should have completed_steps including 3
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert 3 in state["completed_steps"]

    def test_step_3_hydrates_unfetched_issues_from_context(self, tmp_path):
        """When review-context.json has has_unfetched_issues, state should reflect it."""
        # Seed step 1
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        # Pre-write review-context.json as if review/context.py produced it
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
            "has_unfetched_issues": True,
            "linked_issues": ["WOOPLUG-1234"],
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        # Run step 3 — it should read the context and hydrate state
        r = run_pipeline("--step", "3", "--mode", "pr",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["resolved_params"]["has_unfetched_issues"] is True

    def test_step_3_without_context_still_succeeds(self, tmp_path):
        """Step 3 should not crash if review/context.py fails (no git repo)."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        r = run_pipeline("--step", "3", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        # Should succeed even without a git repo — subprocess failure is tolerated
        assert r.returncode == 0

    def test_step_3_allows_known_ecosystem_cache_refreshes_to_finish(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        """The context wrapper should allow both known host caches to refresh."""
        seen_timeouts = []

        def fake_run_subprocess(cmd, cwd=None, timeout=60):
            seen_timeouts.append(timeout)
            return "", True

        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess", fake_run_subprocess
        )
        mod._orchestrate_step(
            3,
            "full",
            {},
            {"resolved_params": {}},
            {},
            str(tmp_path),
        )

        assert seen_timeouts
        assert seen_timeouts[0] > 2 * 30 * 60

    def test_step_3_next_step_reflects_unfetched_issues(self, tmp_path):
        """When has_unfetched_issues is True, next step after 3 should be 4 (not 5)."""
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
            "has_unfetched_issues": True,
            "linked_issues": ["WOOPLUG-1234"],
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "3", "--mode", "pr",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        # Output should point to step 4, not step 5
        assert "Step 4" in r.stdout

    def test_step_3_records_clean_dependency_refresh_precheck_when_opted_in(
        self, tmp_path
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)

        out_dir = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "full",
                     "--output-dir", str(out_dir),
                     "--git-range", "HEAD~1..HEAD", "--refresh-deps",
                     cwd=repo, env=hermetic_env())
        r = run_pipeline("--step", "3", "--mode", "full",
                         "--output-dir", str(out_dir),
                         cwd=repo, env=hermetic_env())

        assert r.returncode == 0
        state = json.loads((out_dir / "pipeline-state.json").read_text())
        assert state["dependency_refresh_precheck"] == {
            "tracked_files_dirty": False,
            "dirty_files": [],
        }

    def test_step_3_skips_detection_without_opt_in(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)

        out_dir = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "full",
                     "--output-dir", str(out_dir),
                     "--git-range", "HEAD~1..HEAD",
                     cwd=repo, env=hermetic_env())
        r = run_pipeline("--step", "3", "--mode", "full",
                         "--output-dir", str(out_dir),
                         cwd=repo, env=hermetic_env())

        assert r.returncode == 0
        state = json.loads((out_dir / "pipeline-state.json").read_text())
        assert "dependency_refresh_precheck" not in state

    def test_step_3_precheck_outside_git_repo_records_unknown(self, tmp_path):
        # No git repo: the precheck must preserve uncertainty as evidence.
        # GIT_CEILING_DIRECTORIES stops rev-parse walking up into a parent
        # repository that may contain tmp_path on some machines.
        env = hermetic_env(GIT_CEILING_DIRECTORIES=str(tmp_path.parent))
        out_dir = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "full",
                     "--output-dir", str(out_dir), "--refresh-deps",
                     cwd=tmp_path, env=env)
        r = run_pipeline("--step", "3", "--mode", "full",
                         "--output-dir", str(out_dir), cwd=tmp_path, env=env)

        assert r.returncode == 0
        state = json.loads((out_dir / "pipeline-state.json").read_text())
        assert state["dependency_refresh_precheck"] == {
            "tracked_files_dirty": None,
            "dirty_files": [],
        }

    def test_step_3_dirty_precheck_refuses_refresh_actions(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        (repo / "README.md").write_text("dirty\n", encoding="utf-8")

        out_dir = tmp_path / "out"
        run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(out_dir), "--refresh-deps",
            cwd=repo, env=hermetic_env(),
        )
        result = run_pipeline(
            "--step", "3", "--mode", "full",
            "--output-dir", str(out_dir),
            cwd=repo, env=hermetic_env(),
        )

        assert result.returncode == 0
        state = json.loads((out_dir / "pipeline-state.json").read_text())
        assert state["dependency_refresh_precheck"] == {
            "tracked_files_dirty": True,
            "dirty_files": ["README.md"],
        }
        assert "will not run dependency commands" in result.stdout
        assert "SAVED dependency-refresh.json" not in result.stdout


class TestStep8WaitingRouting:
    """Step 8 WAITING state should persist without advancing the pipeline."""

    def test_waiting_step_is_not_completed_or_routed_forward(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        mod.write_config(str(tmp_path), {"mode": "pr", "interactive": True})
        mod.write_state(str(tmp_path), {
            "completed_steps": [1, 3, 5, 6, 7],
            "resolved_params": {"git_range": "abc..HEAD"},
            "workspace": {"original_branch": None, "stash_ref": None},
            "agents": {
                "dispatched": ["security-reviewer"],
                "completed": [],
                "discarded_drafts": [],
            },
            "verdict": None,
        })
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py"},
        }))

        def fake_orchestrate(step, mode, config, state, context, output_dir):
            state["waiting_on_agents"] = {
                "running": ["security-reviewer"],
                "not_dispatched": [],
                "agent_timeout_seconds": 1200,
            }
            return context

        monkeypatch.setattr(mod, "_orchestrate_step", fake_orchestrate)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py",
            "--step", "8",
            "--output-dir", str(tmp_path),
        ])

        mod.main()

        output = capsys.readouterr().out
        saved = mod.read_state(str(tmp_path))
        assert 8 not in saved["completed_steps"]
        assert "first_waiting_at" in saved["waiting_on_agents"]
        assert "Next:" not in output
        assert "PIPELINE COMPLETE" not in output
        assert "PIPELINE WAITING" in output


class TestStep5Orchestration:
    """Step 5 main() runs review/plan_dispatch.py and stores output in state."""

    def _make_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        return repo

    def test_step_5_stores_dispatch_plan_summary(self, tmp_path):
        """Step 5 should store dispatch plan summary in state."""
        repo = self._make_repo(tmp_path)
        out = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(out), cwd=str(repo))
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (out / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "5", "--mode", "full",
                       "--output-dir", str(out), cwd=str(repo))
        assert r.returncode == 0
        state = json.loads((out / "pipeline-state.json").read_text())
        assert 5 in state["completed_steps"]
        assert "dispatch_plan_summary" in state

    def test_step_5_loads_valid_saved_dependency_refresh_report(
        self, tmp_path
    ):
        repo = self._make_repo(tmp_path)
        out = tmp_path / "out"
        run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(out), "--git-range", "HEAD~1..HEAD",
            "--refresh-deps", cwd=str(repo),
        )
        request = tmp_path / "dependency-refresh-request.json"
        request.write_text(json.dumps({
            "schema": 1,
            "status": "completed",
            "commands": [{
                "directory": ".",
                "command": "custom sync --locked",
                "exit_status": "ok",
            }],
        }))
        assert dependency_refresh.save_report(out, request, repo) == []

        result = run_pipeline(
            "--step", "5", "--mode", "full",
            "--output-dir", str(out), cwd=str(repo),
        )

        assert result.returncode == 0
        report = dependency_refresh.load_dependency_refresh_report(out)
        state = json.loads((out / "pipeline-state.json").read_text())
        assert state["dependency_refresh_report"] == report
        assert list(out.glob("*verification*.json")) == []
        assert not any("verification" in key for key in state)

    def test_step_5_records_missing_report_without_replacement_artifact(
        self, tmp_path
    ):
        repo = self._make_repo(tmp_path)
        out = tmp_path / "out"
        run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(out), "--git-range", "HEAD~1..HEAD",
            "--refresh-deps", cwd=str(repo),
        )

        result = run_pipeline(
            "--step", "5", "--mode", "full",
            "--output-dir", str(out), cwd=str(repo),
        )

        assert result.returncode == 0
        state = json.loads((out / "pipeline-state.json").read_text())
        assert state["dependency_refresh_report"] is None
        assert not (out / "dependency-refresh.json").exists()
        assert list(out.glob("*verification*.json")) == []
        assert not any("verification" in key for key in state)

    def test_step_5_does_not_load_dependency_refresh_without_opt_in(
        self, tmp_path
    ):
        repo = self._make_repo(tmp_path)
        out = tmp_path / "out"
        run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(out), "--git-range", "HEAD~1..HEAD",
            "--no-refresh-deps", cwd=str(repo),
        )

        result = run_pipeline(
            "--step", "5", "--mode", "full",
            "--output-dir", str(out), cwd=str(repo),
        )

        assert result.returncode == 0
        state = json.loads((out / "pipeline-state.json").read_text())
        assert "dependency_refresh_report" not in state
        assert not any("verification" in key for key in state)
        assert list(out.glob("*verification*.json")) == []

    @pytest.mark.parametrize(
        "tracked_files_dirty",
        [
            True,
            None,
        ],
    )
    def test_step_5_warns_on_dirty_or_unknown_final_tracked_state(
        self, mod, tmp_path, tracked_files_dirty
    ):
        report = {
            "schema": 1,
            "status": "partial",
            "commands": [],
            "tracked_files_dirty": tracked_files_dirty,
            "dirty_files": (
                ["src/changed.py"] if tracked_files_dirty is True else []
            ),
        }
        guidance = mod._step_5_dispatch_plan(
            "full",
            {"dependency_refresh_report": report},
            {},
            {"refresh_dependencies": True},
            str(tmp_path),
        )

        situation = "\n".join(guidance["situation"])
        assert guidance["situation"][0].startswith("⚠️")
        assert "final tracked state" in situation
        assert "dirty" in situation.lower() or "unknown" in situation.lower()
        assert "reported command" not in situation.lower()

    def test_step_5_warns_when_requested_report_is_missing(self, mod, tmp_path):
        guidance = mod._step_5_dispatch_plan(
            "full",
            {"dependency_refresh_report": None},
            {},
            {"refresh_dependencies": True},
            str(tmp_path),
        )

        situation = "\n".join(guidance["situation"])
        assert guidance["situation"][0].startswith("⚠️")
        assert "missing or malformed" in situation

    def test_step_5_preserves_initial_plan_before_orchestrator_adjustment(
        self, tmp_path
    ):
        """Step 5 keeps the deterministic plan unchanged for measurement."""
        repo = self._make_repo(tmp_path)
        out = tmp_path / "out"
        run_pipeline("--step", "1", "--mode", "full",
                  "--output-dir", str(out), cwd=str(repo))
        ctx = {
            "git": {
                "git_range": "HEAD~1..HEAD",
                "changed_files": ["plugins/pirategoat-tools/scripts/review/pipeline.py"],
                "commit_count": 1,
            },
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (out / "review-context.json").write_text(json.dumps(ctx))

        result = run_pipeline(
            "--step", "5", "--mode", "full", "--output-dir", str(out),
            cwd=str(repo),
        )

        assert result.returncode == 0
        initial_path = out / "dispatch-plan.initial.json"
        final_path = out / "dispatch-plan.json"
        initial = json.loads(initial_path.read_text())
        final = json.loads(final_path.read_text())
        assert initial == final

        final["agents"][0]["status"] = "SKIPPED_OVERRIDE"
        final["agents"][0]["override_reason"] = "main orchestrator adjustment"
        final_path.write_text(json.dumps(final))

        assert json.loads(initial_path.read_text()) == initial
        assert json.loads(initial_path.read_text()) != json.loads(final_path.read_text())

    def test_initial_plan_write_failure_is_fail_open(self, mod, tmp_path):
        """Measurement failure neither alters the final plan nor raises."""
        plan = {
            "agents": [
                {
                    "name": "code-reviewer",
                    "status": "DISPATCH",
                    "reason": "always",
                }
            ]
        }
        final_path = tmp_path / "dispatch-plan.json"
        initial_path = tmp_path / "dispatch-plan.initial.json"
        final_path.write_text(json.dumps(plan))
        initial_path.write_text('{"stale": true}')

        with patch.object(mod.os, "replace", side_effect=OSError("nope")):
            mod._preserve_initial_dispatch_plan(str(tmp_path), plan)

        assert json.loads(final_path.read_text()) == plan
        assert not initial_path.exists()

    def test_step_5_real_planner_projects_persisted_codex_host(self, tmp_path):
        repo = self._make_repo(tmp_path)
        out = tmp_path / "out"
        result = run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(out), "--host", "codex",
            cwd=str(repo),
        )
        assert result.returncode == 0

        prompt_path = repo / ".ai" / "agents" / "review" / "expert.md"
        prompt_path.parent.mkdir(parents=True)
        prompt_path.write_text("Review the domain behavior.")
        context = {
            "git": {
                "git_range": "HEAD..HEAD",
                "changed_files": ["src/x.php"],
                "changed_files_csv": "src/x.php",
            },
            "review_config": {
                "rules": [],
                "reviewers": [{
                    "id": "domain-expert",
                    "label": "Domain Expert",
                    "ref": ".ai/agents/review/expert.md",
                    "resolved_ref": str(prompt_path),
                    "applies_to": {"paths": ["**/*.php"]},
                    "channel": "blocking",
                    "execution": "inline",
                    "model": "opus",
                }],
                "untrusted": [],
            },
        }
        (out / "review-context.json").write_text(json.dumps(context))

        result = run_pipeline(
            "--step", "5", "--mode", "full",
            "--output-dir", str(out), cwd=str(repo),
        )

        assert result.returncode == 0
        plan = json.loads((out / "dispatch-plan.json").read_text())
        entry = next(
            agent for agent in plan["agents"]
            if agent.get("adapter") == "repo-reviewer-adapter"
        )
        assert entry["model"] == "inherit"
        assert entry["declared_model"] == "opus"

    def test_failed_planner_retry_preserves_existing_baseline_and_adjusted_plan(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        """A failed retry cannot reclassify an adjusted plan as deterministic."""
        initial = {
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH", "reason": "always"}
            ]
        }
        final = {
            "agents": [
                {
                    "name": "code-reviewer",
                    "status": "SKIPPED_OVERRIDE",
                    "reason": "always",
                    "override_reason": "main orchestrator adjustment",
                }
            ]
        }
        initial_path = tmp_path / "dispatch-plan.initial.json"
        final_path = tmp_path / "dispatch-plan.json"
        initial_path.write_text(json.dumps(initial))
        final_path.write_text(json.dumps(final))
        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess", lambda *args, **kwargs: ("", False)
        )

        mod._orchestrate_step(
            5,
            "full",
            {},
            {"resolved_params": {"git_range": "base..head"}},
            {"git": {"git_range": "base..head"}},
            str(tmp_path),
        )

        assert json.loads(initial_path.read_text()) == initial
        assert json.loads(final_path.read_text()) == final

    def test_failed_planner_without_baseline_does_not_fabricate_one(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        """A failed planner may reuse a final artifact but never invents a baseline."""
        final = {
            "agents": [
                {
                    "name": "code-reviewer",
                    "status": "SKIPPED_OVERRIDE",
                    "reason": "always",
                    "override_reason": "main orchestrator adjustment",
                }
            ]
        }
        final_path = tmp_path / "dispatch-plan.json"
        final_path.write_text(json.dumps(final))
        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess", lambda *args, **kwargs: ("", False)
        )

        mod._orchestrate_step(
            5,
            "full",
            {},
            {"resolved_params": {"git_range": "base..head"}},
            {"git": {"git_range": "base..head"}},
            str(tmp_path),
        )

        assert json.loads(final_path.read_text()) == final
        assert not (tmp_path / "dispatch-plan.initial.json").exists()

    def test_successful_planner_with_invalid_plan_shape_surfaces_value_error(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        """Subprocess success cannot hide a malformed planner artifact."""
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(["not", "a", "plan"]))
        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess", lambda *args, **kwargs: ("", True)
        )
        state = {"resolved_params": {"git_range": "base..head"}}

        with pytest.raises(ValueError, match="must be a JSON object"):
            mod._orchestrate_step(
                5,
                "full",
                {},
                state,
                {"git": {"git_range": "base..head"}},
                str(tmp_path),
            )

        assert not (tmp_path / "dispatch-plan.initial.json").exists()


class TestStep6Orchestration:
    """Step 6 main() reads dispatch-plan.json and populates dispatched_agents."""

    def test_step_6_populates_dispatched_agents(self, tmp_path):
        """Step 6 should read dispatch-plan.json and populate state.dispatched_agents."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        plan = {
            "agents": [
                {"name": "code-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "always"},
                {"name": "go-tests-reviewer", "domain": "go-tests", "status": "SKIPPED", "reason": "no files"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "6", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        names = [a["name"] for a in state.get("dispatched_agents", [])]
        assert "code-reviewer" in names
        assert "security-reviewer" in names
        assert "go-tests-reviewer" not in names

    def test_step_6_output_contains_bootstrap_calls(self, tmp_path):
        """Step 6 output should contain concrete bootstrap.py calls."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        plan = {
            "agents": [
                {"name": "code-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "6", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert "bootstrap.py" in r.stdout
        assert "code-reviewer" in r.stdout
        assert "abc..HEAD" in r.stdout

    def test_step_6_invalid_hand_edited_status_surfaces_value_error(
        self, mod, tmp_path
    ):
        plan = {
            "agents": [
                {
                    "name": "security-reviewer",
                    "status": "DISPATCHED",
                },
            ],
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        with pytest.raises(ValueError) as exc_info:
            mod._orchestrate_step(
                6,
                "full",
                {},
                {},
                {},
                str(tmp_path),
            )

        message = str(exc_info.value)
        assert "security-reviewer" in message
        assert repr("DISPATCHED") in message


class TestStep7Orchestration:
    """Step 7 main() writes .branch-review-baseline.json."""

    def test_step_7_writes_baseline_file(self, tmp_path):
        """Step 7 should create .branch-review-baseline.json."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "7", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        baseline_path = tmp_path / ".branch-review-baseline.json"
        assert baseline_path.is_file(), "Baseline file was not created"
        baseline = json.loads(baseline_path.read_text())
        assert "last_reviewed_sha" in baseline
        assert "last_reviewed_at" in baseline
        assert "review_type" in baseline
        assert baseline["review_type"] == "full"
        assert "git_range_used" in baseline
        assert ".." in baseline["git_range_used"]

    def test_step_7_baseline_grades_clean(self, tmp_path):
        """The written baseline should pass the grader."""
        from helpers.graders import grade_review_baseline
        run_pipeline("--step", "1", "--mode", "incremental",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        run_pipeline("--step", "7", "--mode", "incremental",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        baseline_path = tmp_path / ".branch-review-baseline.json"
        result = grade_review_baseline(str(baseline_path))
        assert result.passed, f"Baseline grading failed: {result.failures}"

    def test_step_7_requires_host_completion_before_draft_finalization(
        self, mod, tmp_path
    ):
        guidance = mod.get_step_guidance(
            7,
            "full",
            {"resolved_params": {"git_range": "abc..HEAD"}},
            {"git": {"git_range": "abc..HEAD"}},
            output_dir=str(tmp_path),
        )
        text = "\n".join(guidance["actions"])

        assert "draft" in text.lower()
        assert "RUNNING" in text
        assert "completion notification" in text.lower()
        assert "`DRAFT`" in text
        assert "`FINALIZE_REVIEW_COMMAND`" in text
        assert "never authorizes" in text.lower()
        assert "discarded when review intake closes" in text.lower()

    def test_step_7_guidance_uses_real_status_output_labels(
        self, mod, tmp_path
    ):
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [
                {"name": "security-reviewer", "status": "DISPATCH"},
            ],
        }))
        (tmp_path / "security-reviewer.started").write_text(
            datetime.now(timezone.utc).isoformat()
        )
        (tmp_path / "security-review.draft.json").write_text("{}")
        status_output = agents_status.format_output(
            agents_status.attach_draft_evidence(
                str(tmp_path), agents_status.check_status(str(tmp_path))
            )
        )
        draft_line = next(
            line.strip()
            for line in status_output.splitlines()
            if line.strip().startswith("DRAFT")
        )
        finalize_line = next(
            line.strip()
            for line in status_output.splitlines()
            if line.strip().startswith("FINALIZE_REVIEW_COMMAND")
        )
        rendered_labels = {
            draft_line.split()[0],
            finalize_line.partition(":")[0],
        }

        guidance = mod.get_step_guidance(
            7,
            "full",
            {"resolved_params": {"git_range": "abc..HEAD"}},
            {"git": {"git_range": "abc..HEAD"}},
            output_dir=str(tmp_path),
        )
        text = "\n".join(guidance["actions"])

        assert rendered_labels == {"DRAFT", "FINALIZE_REVIEW_COMMAND"}
        assert all(f"`{label}`" in text for label in rendered_labels)
        assert "draft_available" not in text
        assert "`finalize_review_command`" not in text


class TestStep8Orchestration:
    """Step 8 main() reads change-purpose.md and agent completion status."""

    def test_step_8_spawns_no_status_subprocess(
        self, mod, tmp_path, monkeypatch
    ):
        """The status checker is a function in this process, not a CLI.

        Step 8 shelled out to agents_status.py and then recovered the
        agent names by splitting the human-readable table on whitespace —
        a parser for a format written for people, in the one gate that
        decides whether reconciliation may start.
        """
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}],
        }))
        _save_and_finalize(tmp_path, "code")
        spawned = []
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *args, **kwargs: spawned.append(args)
            or pytest.fail("step 8 spawned a status subprocess"),
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.json").write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        mod._orchestrate_step(8, "full", {}, state, {}, str(tmp_path))

        assert spawned == []
        assert state["agents"]["completed"] == ["code-reviewer"]

    def test_step_8_completed_never_names_an_undispatched_agent(
        self, mod, tmp_path, monkeypatch
    ):
        """`completed` stays inside `dispatched`, and in its order.

        Intake close classifies a wider population than this run
        dispatched — it also carries forward every draft a previous close
        discarded — so a resumed close can hand back an agent the plan
        no longer names. The step-8 briefing renders both lists.
        """
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH"},
                {"name": "a11y-reviewer", "status": "DISPATCH"},
            ],
        }))
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "close_review_intake",
            lambda *_args: {
                "schema": 2,
                "status": "closed",
                "closed_at": "2026-08-27T12:00:00+00:00",
                "discarded_drafts": [],
                "completed": [
                    "a11y-reviewer", "code-reviewer", "security-reviewer",
                ],
            },
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_markdown",
            lambda *_args, **_kwargs: [],
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.json").write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        mod._orchestrate_step(8, "full", {}, state, {}, str(tmp_path))

        assert state["agents"]["completed"] == [
            "code-reviewer", "a11y-reviewer",
        ]

    def test_step_8_does_not_revalidate_what_intake_close_classified(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}],
        }))
        (tmp_path / "code-review.json").write_text(
            json.dumps({"verdict": "approve"})
        )
        loads = []
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_load_final_review",
            lambda *args: loads.append(args)
            or pytest.fail("step 8 validated a final a second time"),
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.json").write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        mod._orchestrate_step(8, "full", {}, state, {}, str(tmp_path))

        assert loads == []
        assert state["agents"]["completed"] == []
        assert "review_files" not in state["agents"]
        assert "invalid_review_files" not in state["agents"]

    def test_step_8_completion_follows_the_review_paths_authority(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}],
        }))
        authority_dir = tmp_path / "authority"
        authority_dir.mkdir()
        final_path = authority_dir / "final.json"
        final_path.write_text(json.dumps(canonical_review_document("code")))
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "review_paths",
            lambda *_args: reviewer_lifecycle.ReviewPaths(
                draft=str(authority_dir / "draft.json"),
                final=str(final_path),
                assignment=str(authority_dir / "authority.json"),
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "close_review_intake",
            lambda *_args: {
                "schema": 2,
                "status": "closed",
                "closed_at": "2026-08-25T12:00:00+00:00",
                "discarded_drafts": [],
                "completed": ["code-reviewer"],
            },
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_markdown",
            lambda *_args, **_kwargs: [],
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.json").write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        mod._orchestrate_step(8, "full", {}, state, {}, str(tmp_path))

        assert state["agents"]["completed"] == ["code-reviewer"]

    def test_step_8_preserves_invalid_output_evidence_without_completion(
        self, tmp_path
    ):
        initialized = run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )
        assert initialized.returncode == 0, initialized.stderr
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}],
        }))
        review_path = tmp_path / "code-review.json"
        review = canonical_review_document("code")
        review["schema"] = 1
        review_path.write_text(json.dumps(review))

        result = run_pipeline(
            "--step", "8", "--mode", "full",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )

        assert result.returncode == 0, result.stderr
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["agents"]["completed"] == []
        assert state["reviewer_markdown"]["status"] == "partial"
        assert not (tmp_path / "code-review.md").exists()
        intake = json.loads((tmp_path / "review-intake.json").read_text())
        assert intake["status"] == "closed"
        context = json.loads(
            (tmp_path / "reconciliation-context.json").read_text()
        )
        assert context["reviews_by_agent"] == {}
        assert context["missing_agents"] == ["code-review"]
        telemetry_path = Path(
            (tmp_path / ".telemetry-log-path").read_text().strip()
        )
        events = [
            json.loads(line) for line in telemetry_path.read_text().splitlines()
        ]
        assert not any(
            event["event"] == "agent_complete" for event in events
        )

    def test_step_1_records_that_reviewer_markdown_has_not_run(self, tmp_path):
        result = run_pipeline(
            "--step", "1", "--mode", "full", "--output-dir", str(tmp_path),
            cwd=tmp_path,
        )

        assert result.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["reviewer_markdown"] == {
            "ran": False,
            "written": 0,
            "expected": 0,
            "status": "not_run",
        }

    def test_step_8_waiting_repeats_draft_finalization_authority(
        self, mod, tmp_path
    ):
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "waiting_on_agents": {
                "running": ["security-reviewer"],
                "not_dispatched": [],
            },
            "agents": {
                "dispatched": ["security-reviewer"],
                "completed": [],
                "discarded_drafts": [],
            },
        }
        guidance = mod.get_step_guidance(
            8,
            "full",
            state,
            {"git": {"git_range": "abc..HEAD"}},
            output_dir=str(tmp_path),
        )
        text = "\n".join(guidance["actions"])

        assert guidance["blocks_progress"] is True
        assert "completion notification" in text.lower()
        assert "`DRAFT`" in text
        assert "`FINALIZE_REVIEW_COMMAND`" in text
        assert "never authorizes" in text.lower()

    def test_step_8_reads_change_purpose(self, tmp_path):
        """Step 8 should read change-purpose.md into state."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        (tmp_path / "dispatch-plan.json").write_text(
            json.dumps({"agents": []})
        )
        (tmp_path / "change-purpose.md").write_text("Adds retry logic to payment gateway.")
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "8", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "retry logic" in state.get("change_purpose", "").lower()

    def test_step_8_records_which_dispatched_agents_completed(self, tmp_path):
        """Step 8 stores the intake close's completion classification."""
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        plan = {
            "agents": [
                {"name": "code-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "always"},
            ],
            "git_range": "abc..HEAD",
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        # Simulate code-reviewer finalized, security-reviewer not.
        _save_and_finalize(tmp_path, "code")
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        r = run_pipeline("--step", "8", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["agents"]["completed"] == ["code-reviewer"]

    def test_step_8_materializes_every_settled_reviewer_json_at_readiness_gate(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH"},
                {"name": "security-reviewer", "status": "DISPATCH"},
            ],
        }))
        for reviewer in ("code", "security"):
            (tmp_path / f"{reviewer}-review.json").write_text(
                json.dumps(_review_json(reviewer))
            )

        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.json").write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )

        state = {"resolved_params": {}}
        result = mod._orchestrate_step(
            8,
            "full",
            {},
            state,
            {},
            str(tmp_path),
        )

        assert result == {}
        assert (tmp_path / "code-review.md").is_file()
        assert (tmp_path / "security-review.md").is_file()
        assert state["reviewer_markdown"] == {
            "ran": True,
            "written": 2,
            "expected": 2,
            "status": "complete",
        }

    def test_step_8_closes_intake_before_materialization_and_reconciliation(
        self, mod, tmp_path, monkeypatch
    ):
        plan = {"agents": [
            {"name": "code-reviewer", "status": "DISPATCH"},
        ]}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        events = []
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )

        def close_intake(output_dir, dispatched):
            events.append(("close", list(dispatched)))
            return {
                "schema": 2,
                "status": "closed",
                "closed_at": "2026-08-24T12:00:00+00:00",
                "discarded_drafts": ["code-reviewer"],
                "completed": [],
            }

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "close_review_intake",
            close_intake,
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_markdown",
            lambda *_args, **_kwargs: events.append(("materialize", [])) or [],
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            events.append(("reconciliation", []))
            (tmp_path / "reconciliation-context.json").write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        mod._orchestrate_step(8, "full", {}, state, {}, str(tmp_path))

        assert events == [
            ("close", ["code-reviewer"]),
            ("materialize", []),
            ("reconciliation", []),
        ]
        assert state["review_intake"]["discarded_drafts"] == [
            "code-reviewer"
        ]
        assert state["degradation"]["reviewer_drafts_discarded"] is True

    def test_step_8_close_failure_blocks_materialization_and_reconciliation(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({"agents": []}))
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "close_review_intake",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("intake marker unavailable")
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_markdown",
            lambda *_args, **_kwargs: pytest.fail(
                "materialization ran before intake froze"
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            lambda *_args, **_kwargs: pytest.fail(
                "reconciliation ran before intake froze"
            ),
        )

        with pytest.raises(RuntimeError, match="review intake"):
            mod._orchestrate_step(
                8, "full", {}, {"resolved_params": {}}, {}, str(tmp_path)
            )

    def test_step_8_status_checker_failure_preserves_open_intake(
        self, mod, tmp_path, monkeypatch
    ):
        """An unreadable dispatch plan is the checker's own error."""
        (tmp_path / "dispatch-plan.json").write_text(
            json.dumps({"agents": "not a list of agents"})
        )
        draft = tmp_path / "code-review.draft.json"
        draft.write_bytes(b'{"draft":true}\n')
        events = []
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_markdown",
            lambda *_args, **_kwargs: events.append("materialize") or [],
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            lambda *_args, **_kwargs: events.append("reconciliation")
            or ("", True),
        )
        state = {"resolved_params": {}}

        with pytest.raises(RuntimeError, match="status check failed"):
            mod._orchestrate_step(8, "full", {}, state, {}, str(tmp_path))

        assert draft.read_bytes() == b'{"draft":true}\n'
        assert not (tmp_path / "review-intake.json").exists()
        assert events == []

    def test_step_8_status_checker_exception_preserves_open_intake(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "dispatch-plan.json").write_text(
            json.dumps({
                "agents": [{"name": "code-reviewer", "status": "DISPATCH"}],
            })
        )
        draft = tmp_path / "code-review.draft.json"
        draft.write_bytes(b'{"draft":true}\n')
        events = []
        monkeypatch.setattr(
            mod._orchestrate_step_8.__globals__["agents_status"],
            "check_status",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("checker crashed")
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_markdown",
            lambda *_args, **_kwargs: events.append("materialize") or [],
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            lambda *_args, **_kwargs: events.append("reconciliation")
            or ("", True),
        )

        with pytest.raises(RuntimeError, match="status check failed"):
            mod._orchestrate_step(
                8, "full", {}, {"resolved_params": {}}, {}, str(tmp_path)
            )

        assert draft.read_bytes() == b'{"draft":true}\n'
        assert not (tmp_path / "review-intake.json").exists()
        assert events == []

    def test_step_8_uses_post_render_snapshot_when_json_arrives_during_materialization(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH"},
                {"name": "security-reviewer", "status": "DISPATCH"},
            ],
        }))
        (tmp_path / "security-review.json").write_text(
            json.dumps(_review_json("security"))
        )
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **_kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        original_materialize = mod._orchestrate_step_8.__globals__[
            "_materialize_markdown"
        ]

        def publish_then_materialize(output_dir, output_builder_path):
            (tmp_path / "code-review.json").write_text(
                json.dumps(_review_json("code"))
            )
            return original_materialize(output_dir, output_builder_path)

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_markdown",
            publish_then_materialize,
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.json").write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        result = mod._orchestrate_step(
            8, "full", {}, state, {}, str(tmp_path)
        )

        assert result == {}
        assert (tmp_path / "code-review.md").is_file()
        assert (tmp_path / "security-review.md").is_file()
        assert state["reviewer_markdown"] == {
            "ran": True,
            "written": 2,
            "expected": 2,
            "status": "complete",
        }

    def test_step_8_compares_materialized_path_identities_not_only_counts(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [{"name": "security-reviewer", "status": "DISPATCH"}],
        }))
        (tmp_path / "security-review.json").write_text(
            json.dumps(_review_json("security"))
        )
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        unrelated_markdown = tmp_path / "code-review.md"
        unrelated_markdown.write_text("# Different reviewer\n")
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_markdown",
            lambda *_args, **_kwargs: [str(unrelated_markdown)],
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.json").write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        result = mod._orchestrate_step(
            8, "full", {}, state, {}, str(tmp_path)
        )

        assert result == {}
        assert state["reviewer_markdown"] == {
            "ran": True,
            "written": 0,
            "expected": 1,
            "status": "partial",
        }
        assert state["degradation"]["reviewer_markdown_incomplete"] is True

    def test_step_8_records_materialization_failure_without_aborting(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [{"name": "security-reviewer", "status": "DISPATCH"}],
        }))
        (tmp_path / "security-review.json").write_text(
            json.dumps(_review_json("security"))
        )
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_materialize_markdown",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("renderer crashed")
            ),
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.json").write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        result = mod._orchestrate_step(
            8, "full", {}, state, {}, str(tmp_path)
        )

        assert result == {}
        assert state["reviewer_markdown"] == {
            "ran": True,
            "written": 0,
            "expected": 1,
            "status": "failed",
        }
        assert state["degradation"]["reviewer_markdown_incomplete"] is True
        assert "reviewer markdown materialization failed: renderer crashed" in (
            capsys.readouterr().err
        )

    def test_step_8_records_skipped_json_as_partial_materialization(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [{"name": "security-reviewer", "status": "DISPATCH"}],
        }))
        (tmp_path / "security-review.json").write_text("{}")
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (tmp_path / "reconciliation-context.json").write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        result = mod._orchestrate_step(
            8, "full", {}, state, {}, str(tmp_path)
        )

        assert result == {}
        assert state["reviewer_markdown"] == {
            "ran": True,
            "written": 0,
            "expected": 1,
            "status": "partial",
        }
        assert state["degradation"]["reviewer_markdown_incomplete"] is True

    def test_step_8_reconciliation_failure_happens_after_reviewer_markdown(
        self, mod, tmp_path, monkeypatch
    ):
        (tmp_path / "dispatch-plan.json").write_text(json.dumps({
            "agents": [{"name": "security-reviewer", "status": "DISPATCH"}],
        }))
        (tmp_path / "security-review.json").write_text(
            json.dumps(_review_json("security"))
        )
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("reconciliation crashed")
            ),
        )

        with pytest.raises(RuntimeError, match="reconciliation crashed"):
            mod._orchestrate_step(
                8,
                "full",
                {},
                {"resolved_params": {}},
                {},
                str(tmp_path),
            )

        assert (tmp_path / "security-review.md").is_file()

    def test_step_8_invalid_hand_edited_status_names_the_agent(
        self, mod, tmp_path
    ):
        """The readiness gate reads the plan first, so it reports the defect.

        It still names the agent and the unsupported value — the gate wraps
        the plan validator's ValueError rather than swallowing it.
        """
        plan = {
            "agents": [
                {
                    "name": "security-reviewer",
                    "status": None,
                },
            ],
        }
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))

        with pytest.raises(
            RuntimeError, match="status check failed"
        ) as exc_info:
            mod._orchestrate_step(
                8,
                "full",
                {},
                {"resolved_params": {}},
                {},
                str(tmp_path),
            )

        assert "security-reviewer" in str(exc_info.value)
        cause = str(exc_info.value.__cause__)
        assert "security-reviewer" in cause
        assert repr(None) in cause
        assert not (tmp_path / "review-intake.json").exists()


class TestStep9CoverageMeasurement:
    """Step 9 measures the three inline-coverage populations and puts them
    in pipeline state: claimed files, unclaimed files, and changed files no
    reviewer's scope contained.

    It measures them from the run's own durable artifacts — the per-agent
    scope sidecars, each reviewer's finalized review, and the changed-file
    CSV the same context handed step 8 — rather than reading them back out
    of the reconciliation context, which never carried them for the
    reconciliator's sake. The record assembler and step 11's coverage
    instruction both read what this puts in state.
    """

    @staticmethod
    def _summary(tmp_path, agent, *, inline=(), claimable=()):
        (tmp_path / f"{agent}-scope-summary.json").write_text(json.dumps({
            "schema": 2,
            "domain": "x",
            "status": "OK",
            "inline_diff_files": list(inline),
            "review_claimable_files": list(claimable),
            "list_only_files": [],
            "in_scope_review_files": sorted({*inline, *claimable}),
        }))

    @staticmethod
    def _finalized(tmp_path, reviewer, *, claims=(), claimable=()):
        (tmp_path / f"{reviewer}-review.json").write_text(json.dumps(
            canonical_review_document(
                reviewer,
                reviewed_file_claims=list(claims),
                review_claimable_files=list(claimable),
            )
        ))

    @staticmethod
    def _run_step(mod, tmp_path, changed_csv=None, state=None):
        context = (
            {} if changed_csv is None
            else {"git": {"changed_files_csv": changed_csv}}
        )
        state = {} if state is None else state
        mod._orchestrate_step(9, "full", {}, state, context, str(tmp_path))
        return state

    def test_measured_populations_reach_state_intact(self, mod, tmp_path):
        self._summary(
            tmp_path, "security-reviewer",
            inline=["src/a.py"], claimable=["src/big_module.py"],
        )
        self._summary(
            tmp_path, "code-reviewer", claimable=["src/starved.php"],
        )
        self._finalized(
            tmp_path, "security",
            claims=["src/big_module.py"], claimable=["src/big_module.py"],
        )

        state = self._run_step(
            mod, tmp_path,
            "src/a.py,src/big_module.py,src/starved.php,package-lock.json",
        )

        assert state["file_review"] == {
            "scope_reporting_agent_count": 2,
            "unscoped_files": ["package-lock.json"],
            "agents_receiving_inline_diff_by_file": {
                "src/a.py": ["security-reviewer"]
            },
            "agents_claiming_review_by_file": {
                "src/big_module.py": ["security-reviewer"]
            },
            "agents_with_unclaimed_review_by_file": {
                "src/starved.php": ["code-reviewer"]
            },
        }

    def test_stale_populations_are_cleared_not_carried(self, mod, tmp_path):
        """A re-entered step 9 in a run with nothing to measure must not
        keep the previous run's gaps standing — the record would then
        report a coverage problem this run never measured."""
        stale = {"file_review": {
            "agents_with_unclaimed_review_by_file": {
                "src/stale.php": ["code-reviewer"]
            }
        }}

        state = self._run_step(mod, tmp_path, "src/a.py", state=stale)

        assert state["file_review"] is None

    def test_unmeasured_unscoped_is_none_not_empty(self, mod, tmp_path):
        """`unscoped_files: null` is "not measured", not "none found" —
        only the second may ever render as a clean coverage result, and a
        context with no changed-file CSV measured nothing."""
        self._summary(tmp_path, "security-reviewer", inline=["src/a.py"])

        state = self._run_step(
            mod, tmp_path, "",
            state={"file_review": {"unscoped_files": ["stale.py"]}},
        )

        assert state["file_review"]["unscoped_files"] is None

    def test_measured_empty_unscoped_is_a_list(self, mod, tmp_path):
        self._summary(tmp_path, "security-reviewer", inline=["src/a.py"])

        state = self._run_step(mod, tmp_path, "src/a.py")

        assert state["file_review"]["unscoped_files"] == []

    def test_the_measured_populations_reach_the_record(self, mod, tmp_path):
        """The whole point of measuring them: the assembler renders them."""
        (tmp_path / "review-findings.json").write_text(
            json.dumps(_review_json("review-reconciliator"))
        )
        self._summary(
            tmp_path, "security-reviewer", claimable=["src/big.py"],
        )
        self._summary(
            tmp_path, "code-reviewer", claimable=["src/starved.php"],
        )
        self._finalized(
            tmp_path, "security",
            claims=["src/big.py"], claimable=["src/big.py"],
        )

        self._run_step(
            mod, tmp_path,
            "src/big.py,src/starved.php,package-lock.json",
        )

        record = (tmp_path / "review-record.md").read_text()
        assert "`src/starved.php` (skipped by: `code-reviewer`)" in record
        assert "`src/big.py` (claimed by: `security-reviewer`)" in record
        assert "- `package-lock.json`" in record


class TestStep9Orchestration:
    """Step 9 measures the run-level file review through the real CLI."""

    def test_step_9_measures_the_file_review(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        for agent in ("code-reviewer", "security-reviewer"):
            (tmp_path / f"{agent}-scope-summary.json").write_text(json.dumps({
                "schema": 2,
                "domain": "x",
                "status": "OK",
                "inline_diff_files": ["src/a.php"],
                "review_claimable_files": ["src/starved.php"],
                "list_only_files": [],
                "in_scope_review_files": ["src/a.php", "src/starved.php"],
            }))
        # The CSV step 8 hands the reconciliation-context builder is the
        # same one step 9 measures `unscoped_files` against.
        (tmp_path / "review-context.json").write_text(json.dumps({
            "output": {"directory": str(tmp_path)},
            "git": {"changed_files_csv": "src/a.php,src/starved.php"},
        }))
        (tmp_path / "review-findings.json").write_text(
            json.dumps(_review_json("review-reconciliator"))
        )
        r = run_pipeline("--step", "9", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state.get("file_review") == {
            "scope_reporting_agent_count": 2,
            "unscoped_files": [],
            "agents_receiving_inline_diff_by_file": {
                "src/a.php": ["code-reviewer", "security-reviewer"]
            },
            "agents_claiming_review_by_file": {},
            "agents_with_unclaimed_review_by_file": {
                "src/starved.php": ["code-reviewer", "security-reviewer"],
            },
        }
        # The record the step assembles carries the measurement. The
        # briefing no longer re-renders it — a second copy is a second
        # thing to paraphrase.
        assert state.get("review_record", {}).get("status") == "complete"
        assert "src/starved.php" in (tmp_path / "review-record.md").read_text()

    def test_step_9_tolerates_a_run_with_nothing_to_measure(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full",
                   "--output-dir", str(tmp_path), cwd=tmp_path)
        r = run_pipeline("--step", "9", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state.get("file_review") is None


class TestStep9FindingsMarkdown:
    """`review-findings.md` is a script render of the reconciliator's JSON.

    The reconciliator publishes JSON only; the pipeline owns the Markdown,
    so the two can never disagree the way a hand-written narrative did.
    """

    @staticmethod
    def _findings(**extra):
        data = _review_json("reconciliator")
        data["findings"] = [{
            "id": "f1",
            "category": "general",
            "severity": "high",
            "title": "Unescaped output",
            "file": "a.php",
            "line": 12,
            "description": "d",
            "recommendation": "r",
            "confidence": 0.9,
        }]
        data["summary"]["total_findings"] = 1
        data["summary"]["by_severity"]["high"] = 1
        data["verdict"] = "request_changes"
        data["meta"]["next_finding_number"] = 2
        data["meta"]["reconciliation"].update({
            "input_finding_count": 1,
            "contributing_agent_count": 1,
            "grouped_concern_count": 1,
            "verified_concern_count": 1,
            "reviewing_agents": ["security-reviewer"],
            "dispatched_agents": ["security-reviewer"],
        })
        data.update(extra)
        return data

    def test_step_9_renders_findings_markdown_from_the_json(
        self, mod, tmp_path
    ):
        data = self._findings()
        (tmp_path / "review-findings.json").write_text(json.dumps(data))
        state = {"resolved_params": {}}

        mod._orchestrate_step(9, "full", {}, state, {}, str(tmp_path))

        rendered = (tmp_path / "review-findings.md").read_text()
        assert rendered == _render_markdown(data)
        assert "Unescaped output" in rendered
        assert state["findings_markdown"] == {
            "ran": True, "written": 1, "expected": 1, "status": "complete",
        }

    def test_step_9_overwrites_a_stale_findings_markdown(self, mod, tmp_path):
        (tmp_path / "review-findings.json").write_text(
            json.dumps(self._findings())
        )
        (tmp_path / "review-findings.md").write_text("# stale narrative\n")
        mod._orchestrate_step(9, "full", {}, {"resolved_params": {}}, {},
                              str(tmp_path))
        assert "stale narrative" not in (
            tmp_path / "review-findings.md"
        ).read_text()

    def test_step_9_records_a_render_failure_instead_of_raising(
        self, mod, tmp_path, capsys
    ):
        (tmp_path / "review-findings.json").write_text(
            json.dumps(self._findings())
        )
        monkey = mod._orchestrate_step_9.__globals__
        original = monkey["_load_output_module"]
        monkey["_load_output_module"] = failing_findings_renderer(
            original, "renderer crashed"
        )
        state = {"resolved_params": {}}
        try:
            mod._orchestrate_step(9, "full", {}, state, {}, str(tmp_path))
        finally:
            monkey["_load_output_module"] = original

        assert state["findings_markdown"] == {
            "ran": True, "written": 0, "expected": 1, "status": "failed",
        }
        assert state["degradation"]["findings_markdown_incomplete"] is True
        assert "findings markdown materialization failed: renderer crashed" in (
            capsys.readouterr().err
        )

    def test_step_9_without_findings_json_records_nothing_rendered(
        self, mod, tmp_path
    ):
        state = {"resolved_params": {}}
        mod._orchestrate_step(9, "full", {}, state, {}, str(tmp_path))
        assert state["findings_markdown"] == {
            "ran": True, "written": 0, "expected": 0, "status": "complete",
        }
        assert not (tmp_path / "review-findings.md").exists()


class TestStep10Orchestration:
    """Step 10 main() reads the reconciliation verdict and records the
    quick-mode critic skip decision.

    This class exists because the step-10 orchestration branch had NO
    execution coverage: the pipeline module split extracted it into
    _orchestrate_step_10 while its body still referenced the `step`
    parameter the helper never receives, and the whole integration suite
    stayed green over a live NameError. Every test here runs the real
    step, so the branch cannot silently stop executing again.
    """

    def _findings(self, tmp_path, verdict):
        findings = _review_json("reconciliator")
        if verdict == "block":
            findings["findings"] = [{
                "id": "f1",
                "category": "correctness",
                "severity": "critical",
                "title": "Blocking defect",
                "description": "The defect blocks a safe release.",
                "file": "src/blocking.py",
                "line": 1,
                "recommendation": "Correct the defect.",
                "confidence": 0.9,
            }]
            findings["verdict"] = "block"
            findings["summary"] = {
                "total_findings": 1,
                "by_severity": {
                    "critical": 1,
                    "high": 0,
                    "medium": 0,
                    "low": 0,
                    "info": 0,
                },
                "suppressed_advisory_finding_count": 0,
            }
            findings["meta"]["next_finding_number"] = 2
        (tmp_path / "review-findings.json").write_text(
            json.dumps(findings)
        )

    def test_step_10_records_the_reconciliation_verdict(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        self._findings(tmp_path, "block")
        r = run_pipeline("--step", "10", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state.get("reconciliation_verdict") == "block"
        # Not quick mode — the critic always runs, so no skip decision.
        assert "10" not in state.get("step_decisions", {})

    def test_step_10_quick_mode_records_critic_skip(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full", "--quick",
                  "--output-dir", str(tmp_path), cwd=tmp_path)
        self._findings(tmp_path, "approve")
        r = run_pipeline("--step", "10", "--mode", "full", "--quick",
                      "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        decision = state.get("step_decisions", {}).get("10")
        assert decision is not None, "quick-mode critic skip was not recorded"
        assert decision["critic_skipped"] is True
        assert "approve" in decision["reason"]

    def test_step_10_quick_mode_keeps_critic_for_blocking_verdict(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full", "--quick",
                  "--output-dir", str(tmp_path), cwd=tmp_path)
        self._findings(tmp_path, "block")
        r = run_pipeline("--step", "10", "--mode", "full", "--quick",
                      "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "10" not in state.get("step_decisions", {})

    def test_step_10_clears_a_stale_skip_decision_on_rerun(self, tmp_path):
        """A rerun after the verdict escalates must drop the earlier skip.

        This is the exact line the split broke: the decision key is popped
        before it is conditionally rewritten, so a stale `critic_skipped`
        cannot survive into a run whose reconciliation now blocks.
        """
        run_pipeline("--step", "1", "--mode", "full", "--quick",
                  "--output-dir", str(tmp_path), cwd=tmp_path)
        self._findings(tmp_path, "approve")
        run_pipeline("--step", "10", "--mode", "full", "--quick",
                  "--output-dir", str(tmp_path), cwd=tmp_path)
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["step_decisions"]["10"]["critic_skipped"] is True

        self._findings(tmp_path, "block")
        r = run_pipeline("--step", "10", "--mode", "full", "--quick",
                      "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert "10" not in state.get("step_decisions", {}), (
            "stale critic-skip decision survived a verdict escalation"
        )

    def test_step_10_tolerates_missing_reconciliation_findings(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        r = run_pipeline("--step", "10", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state.get("reconciliation_verdict", "") == ""

    def test_step_10_tolerates_malformed_reconciliation_findings(self, tmp_path):
        run_pipeline("--step", "1", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        (tmp_path / "review-findings.json").write_text("{not json")
        r = run_pipeline("--step", "10", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state.get("reconciliation_verdict") == ""

    def test_step_10_keeps_a_noncanonical_ledger_out_of_critic_context(
        self, tmp_path
    ):
        run_pipeline(
            "--step", "1", "--mode", "full", "--output-dir", str(tmp_path),
            cwd=tmp_path,
        )
        (tmp_path / "review-findings.json").write_text(
            json.dumps({"verdict": "block"})
        )

        result = run_pipeline(
            "--step", "10", "--mode", "full", "--output-dir", str(tmp_path),
            cwd=tmp_path,
        )

        assert result.returncode == 0, result.stderr
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["ledger_status"] != "ok"
        assert state["critic_source"] is None
        assert "Structured findings (for critic.py --context)" not in result.stdout
        assert "without --context" in result.stdout

    @pytest.mark.parametrize("payload", ["[1, 2]", '"hello"', "5"],
                             ids=["list", "string", "int"])
    def test_step_10_tolerates_a_valid_json_non_object_ledger(
        self, tmp_path, payload
    ):
        """Valid JSON that is not an object used to escape this step's
        `(JSONDecodeError, OSError)` guard and raise AttributeError on the
        `.get()` behind it — the same hole the shared verdict parser
        closed for review-verdict.json, one artifact over. The ledger now
        goes through critic_adjustments.read_findings_file(), so a
        non-object payload is a shape fact, not a crash."""
        run_pipeline("--step", "1", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        (tmp_path / "review-findings.json").write_text(payload)
        r = run_pipeline("--step", "10", "--mode", "full", "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0, r.stderr
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state.get("reconciliation_verdict") == ""


class TestStep11Orchestration:
    """Step 11 settles state, then publishes after the report handoff."""

    def test_invalid_ledger_is_neither_materialized_nor_report_source(
        self, tmp_path
    ):
        run_pipeline(
            "--step", "1", "--mode", "pr", "--pr-number", "42",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )
        findings = _review_json("reconciliator")
        findings["verdict"] = "APPROVE"
        findings_path = tmp_path / "review-findings.json"
        findings_path.write_text(json.dumps(findings))

        assert critic_adjustments.read_findings_file(
            findings_path
        ).status == critic_adjustments.FINDINGS_READ_INVALID

        prepared = run_pipeline(
            "--step", "11", "--mode", "pr", "--output-dir", str(tmp_path),
            cwd=tmp_path,
        )

        assert prepared.returncode == 0, prepared.stderr
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["ledger_status"] == "invalid"
        assert not (tmp_path / "review-findings.md").exists()
        assert (
            f"Source:** `{tmp_path}/review-findings.json"
            not in prepared.stdout
        )
        assert f"`{tmp_path}/<agent>-review.md`" in prepared.stdout

    def test_step_11_prepares_then_publishes_after_report_handoff(
        self, tmp_path
    ):
        """The result is the terminal commit marker, so the first pass
        remains resumable until the orchestrator authors the report."""
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {"merge_base": "abc", "git_range": "abc..HEAD"},
        }))
        run_pipeline(
            "--step", "1", "--mode", "pr", "--pr-number", "42",
            "--interactive", "false", "--output-dir", str(tmp_path),
            cwd=tmp_path,
        )
        (tmp_path / "review-findings.json").write_text(
            '{"verdict": "approve", "findings": []}'
        )

        prepared = run_pipeline(
            "--step", "11", "--mode", "pr",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )

        assert prepared.returncode == 0, prepared.stderr
        assert not (tmp_path / "pipeline-result.json").exists()
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["publication_pending"] is True
        prepared_fingerprint = state["prepared_report_source_fingerprint"]
        assert len(prepared_fingerprint) == 64
        assert state["report_handoff_status"] == "report_missing"
        assert 11 not in state["completed_steps"]
        assert not any(
            "review-report.md" in note
            for note in state["degradation_notes"]
        )
        assert "PIPELINE WAITING" in prepared.stdout
        assert "--step 11" in prepared.stdout
        assert "PIPELINE COMPLETE" not in prepared.stdout

        report = tmp_path / "review-report.md"
        report.write_text("# Review\nAll clear.")
        published = run_pipeline(
            "--step", "11", "--mode", "pr",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )

        assert published.returncode == 0, published.stderr
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["report_path"] == str(report)
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["publication_pending"] is False
        assert state["prepared_report_source_fingerprint"] == (
            prepared_fingerprint
        )
        assert state["report_handoff_status"] == "published"
        assert "stale_report_digest" not in state
        assert 11 in state["completed_steps"]
        assert "PIPELINE WAITING" not in published.stdout
        assert "HANDOFF" not in published.stdout
        assert "PIPELINE COMPLETE" in published.stdout

    def test_late_adjudication_invalidates_the_report_until_rewritten(
        self, tmp_path
    ):
        """A report authored before the orchestrator adjudicated cannot
        publish once the adjudication changes the ledger and its verdict."""
        run_pipeline(
            "--step", "1", "--mode", "pr", "--pr-number", "42",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )
        finding = _review_json("review-reconciliator")
        finding["verdict"] = "request_changes"
        finding["findings"] = [{
            "id": "f1",
            "category": "general",
            "severity": "high",
            "title": "Unescaped output",
            "file": "a.php",
            "line": 12,
            "description": "d",
            "recommendation": "r",
            "confidence": 0.9,
        }]
        finding["summary"]["total_findings"] = 1
        finding["summary"]["by_severity"]["high"] = 1
        finding["meta"]["next_finding_number"] = 2
        write_findings(str(tmp_path), finding)
        proposal_ids = _write_critic_snapshot(tmp_path, [{
            "action": "demote",
            "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"},
            "rationale": "guarded upstream",
        }])

        prepared = run_pipeline(
            "--step", "11", "--mode", "pr",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )
        assert prepared.returncode == 0, prepared.stderr
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["publication_pending"] is True
        assert state["prepared_report_source_fingerprint"]
        assert any(
            "never adjudicated" in note
            for note in state["degradation_notes"]
        )

        report = tmp_path / "review-report.md"
        stale_report = "# Review\nREQUEST_CHANGES: high finding."
        report.write_text(stale_report)
        critic_adjustments.adjudicate(str(tmp_path), {
            "schema": 2,
            "verified": proposal_ids,
            "refuted": [],
            "revised_assessment": "The finding is guarded upstream.",
        })
        # Even a leftover marker from an interrupted/manual re-entry must
        # not coexist with a report this pass rejects as stale.
        (tmp_path / "pipeline-result.json").write_text('{"stale": true}')

        changed = run_pipeline(
            "--step", "11", "--mode", "pr",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )
        assert changed.returncode == 0, changed.stderr
        assert not (tmp_path / "pipeline-result.json").exists()
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["report_handoff_status"] == "source_changed"
        assert state["stale_report_digest"]
        assert state["publication_pending"] is True
        assert 11 not in state["completed_steps"]
        assert "regenerate" in changed.stdout.lower()
        settled = json.loads((tmp_path / "review-findings.json").read_text())
        assert settled["findings"][0]["severity"] == "low"
        assert settled["verdict"] == "approve"

        unchanged = run_pipeline(
            "--step", "11", "--mode", "pr",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )
        assert unchanged.returncode == 0, unchanged.stderr
        assert not (tmp_path / "pipeline-result.json").exists()
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["report_handoff_status"] == "stale_report_unchanged"
        assert state["publication_pending"] is True
        assert 11 not in state["completed_steps"]

        report.write_text("# Review\nAPPROVE: settled low finding.")
        published = run_pipeline(
            "--step", "11", "--mode", "pr",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )
        assert published.returncode == 0, published.stderr
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["verdict"] == "APPROVE"
        assert result["status"] == "degraded"
        assert any(
            "never adjudicated" in note
            for note in result["degradation_notes"]
        ), "the prepare pass's honest record survives the handoff"
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["publication_pending"] is False
        assert "stale_report_digest" not in state
        assert 11 in state["completed_steps"]

    def test_interactive_publish_pass_routes_to_cleanup(self, tmp_path):
        run_pipeline(
            "--step", "1", "--mode", "full", "--original-branch", "main",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )
        (tmp_path / "review-findings.json").write_text(
            '{"verdict": "approve", "findings": []}'
        )

        prepared = run_pipeline(
            "--step", "11", "--mode", "full",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )
        assert "PIPELINE WAITING" in prepared.stdout

        (tmp_path / "review-report.md").write_text("# Review")
        published = run_pipeline(
            "--step", "11", "--mode", "full",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )

        assert published.returncode == 0, published.stderr
        assert "Next: Step 12" in published.stdout
        assert "PIPELINE COMPLETE" not in published.stdout

    def test_step_11_writes_pipeline_result(self, tmp_path):
        """A pre-existing unbound report must be rewritten before publish."""
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        report = tmp_path / "review-report.md"
        report.write_text("# Review Report\nFindings here.")
        (tmp_path / "review-findings.json").write_text('{"verdict": "request_changes", "findings": []}')
        r = run_pipeline("--step", "11", "--mode", "pr",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        assert r.returncode == 0
        result_path = tmp_path / "pipeline-result.json"
        assert not result_path.exists()
        state = json.loads((tmp_path / "pipeline-state.json").read_text())
        assert state["report_handoff_status"] == "unbound_report"
        assert state["stale_report_digest"]

        unchanged = run_pipeline(
            "--step", "11", "--mode", "pr",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )
        assert unchanged.returncode == 0
        assert not result_path.exists()
        report.write_text("# Review Report\nREQUEST_CHANGES: rewritten.")
        published = run_pipeline(
            "--step", "11", "--mode", "pr",
            "--output-dir", str(tmp_path), cwd=tmp_path,
        )
        assert published.returncode == 0
        assert result_path.is_file(), "pipeline-result.json was not created"
        result = json.loads(result_path.read_text())
        assert result["verdict"] == "COMMENT"
        assert result["status"] == "degraded"
        assert result["verdict_source"] == (
            "fallback: no usable ledger verdict"
        )
        assert "report_path" in result

    def test_step_11_leaves_the_findings_verdict_alone(self, tmp_path):
        """Rule 23's sync is gone end to end: the CLI reads the ledger's
        verdict and never writes one back over it."""
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        (tmp_path / "review-report.md").write_text("# Review")
        (tmp_path / "review-findings.json").write_text('{"verdict": "comment", "findings": []}')
        _publish_step_11(tmp_path, tmp_path)
        findings = json.loads((tmp_path / "review-findings.json").read_text())
        assert findings["verdict"] == "comment"
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["verdict"] == "COMMENT"

    def test_step_11_handles_a_missing_ledger_gracefully(self, tmp_path):
        """No ledger at all: the verdict falls back to COMMENT and the run
        says why, rather than crashing or publishing a confident value."""
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        (tmp_path / "review-report.md").write_text("# Review")
        r = _publish_step_11(tmp_path, tmp_path)
        assert r.returncode == 0
        result_path = tmp_path / "pipeline-result.json"
        assert result_path.is_file()
        result = json.loads(result_path.read_text())
        assert result["status"] in ("degraded", "failed")
        assert result["verdict"] == "COMMENT"
        assert result["verdict_source"] == "fallback: no usable ledger verdict"

    def test_step_11_degrades_when_findings_missing(self, tmp_path):
        """Step 11 should report degraded when review-findings.json is missing (partial run)."""
        run_pipeline("--step", "1", "--mode", "pr",
                   "--output-dir", str(tmp_path), "--pr-number", "42", cwd=tmp_path)
        # The report exists, but the findings do not (reconciliation failed)
        (tmp_path / "review-report.md").write_text("# Review\nReport here.")
        r = _publish_step_11(tmp_path, tmp_path)
        assert r.returncode == 0
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert any("review-findings.json" in n for n in result["degradation_notes"])


class TestTelemetryFinalize:
    """Telemetry finalize is called at the last active step."""

    def test_last_step_finalizes_telemetry(self, tmp_path):
        """The last active step should finalize telemetry and its manifest."""
        log_dir = tmp_path / "telemetry-logs"
        with patch.dict(os.environ, {"PIRATEGOAT_TELEMETRY_LOG_DIR": str(log_dir)}):
            run_pipeline("--step", "1", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
            # The first pass prepares settled state and waits for the report
            # handoff, so it is deliberately not terminal.
            run_pipeline("--step", "11", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
            (tmp_path / "review-report.md").write_text("# Review")
            # The second pass publishes the terminal marker and finalizes
            # telemetry. Step 12 needs workspace + interactive.
            run_pipeline("--step", "11", "--mode", "full",
                       "--output-dir", str(tmp_path), cwd=tmp_path)
        marker = tmp_path / ".telemetry-log-path"
        if marker.is_file():
            log_path = marker.read_text().strip()
            with open(log_path) as f:
                lines = f.readlines()
            events = [json.loads(l)["event"] for l in lines]
            assert "pipeline_end" in events, f"Expected pipeline_end event, got: {events}"
            manifest_path = Path(log_path).with_suffix(".manifest.json")
            manifest = json.loads(manifest_path.read_text())
            assert manifest["status"] == "complete"


class TestStep8AgentPrompt:
    """Step 8 should emit a complete reconciliator Agent tool prompt (rule 15)."""

    def test_reconciliator_prompt_has_concrete_values(self, mod, tmp_path):
        state = {
            "resolved_params": {"git_range": "abc..HEAD"},
            "completed_steps": [1, 3, 5, 6, 7],
            "agents": {
                "dispatched": ["code-reviewer", "security-reviewer"],
                "completed": ["code-reviewer", "security-reviewer"],
                "discarded_drafts": [],
            },
            "change_purpose": "Adds retry logic.",
        }
        ctx = {"git": {"git_range": "abc..HEAD", "changed_files_csv": "a.py,b.py"}}
        g = mod.get_step_guidance(8, "pr", state, ctx, output_dir=str(tmp_path))
        text = "\n".join(g["actions"])
        assert "reconciliation-context.json" in text  # pre-gathered context
        assert str(tmp_path) in text  # concrete output directory


class TestStep10AgentPrompt:
    """Step 10 should emit a complete decision critic Agent tool prompt (rule 15)."""

class TestFullSequenceIntegration:
    """Full multi-step sequence produces pipeline-result.json."""

    @pytest.fixture
    def repo(self, tmp_path):
        """A throwaway repository for the sequence to stand in.

        The pipeline measures the repo it is running in — step 3 snapshots
        its git status and step 11 compares, and sweeps probe residue,
        against that snapshot — so a sequence spawned without a cwd runs
        that machinery over whoever's checkout pytest happened to start in.
        The sequence is repo-coupled in its own right too: step 1 resolves
        git identity and step 7 writes a review baseline. The output
        directory stays outside this repo so the run's own artifacts never
        register as worktree changes.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        return repo

    def test_full_sequence_produces_pipeline_result(self, tmp_path, repo):
        """Run steps 1,3,5,6,7,8,11 in order — pipeline-result.json should exist."""
        od = str(tmp_path / "out")
        os.makedirs(od, exist_ok=True)
        # Step 1: seed
        r = run_pipeline("--step", "1", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0

        # Pre-write context as if review/context.py succeeded
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "changed_files_csv": "a.py",
                    "commit_count": 1, "base_ref": "main"},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (Path(od) / "review-context.json").write_text(json.dumps(ctx))

        # Step 3: gather context (reads the pre-written file)
        r = run_pipeline("--step", "3", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0

        # Step 5: dispatch plan (may fail without git, but should not crash)
        r = run_pipeline("--step", "5", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0

        # Pre-write dispatch plan as if planner succeeded
        plan = {"agents": [{"name": "code-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"}], "git_range": "abc..HEAD"}
        (Path(od) / "dispatch-plan.json").write_text(json.dumps(plan))

        # Step 6: dispatch agents
        r = run_pipeline("--step", "6", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0

        # Step 7: save baseline
        r = run_pipeline("--step", "7", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0
        assert (Path(od) / ".branch-review-baseline.json").is_file()

        # Step 8: reconcile (no review files exist — that's OK)
        r = run_pipeline("--step", "8", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0

        # Pre-write verdict, report, and findings as if steps 8-10 ran
        (Path(od) / "review-report.md").write_text("# Review\nAll clear.")
        # A complete ledger, the way ReviewOutputBuilder writes it, published
        # through the sanctioned findings writer the way the reconciliator
        # does: step 11 renders review-findings.md from this file and
        # verifies its content digest, so a stub the renderer cannot read —
        # or a raw write carrying no digest — would degrade the run for a
        # reason no real run has.
        write_findings(od, _review_json("reconciliator"))

        # Step 11: prepare the source binding, then present results.
        r = _publish_step_11(od, repo, mode="full")
        assert r.returncode == 0
        assert (Path(od) / "pipeline-result.json").is_file()
        result = json.loads((Path(od) / "pipeline-result.json").read_text())
        assert result["verdict"] == "APPROVE"
        assert result["status"] == "success"
        assert result["review_baseline_saved"] is True


# =============================================================================
# Quick Mode Dispatch Tests
# =============================================================================

# Files that cover enough domains to trigger most agents
_QUICK_MODE_TEST_FILES = [
    "src/Controller.php",
    "src/components/Modal.tsx",
    "src/hooks/useData.ts",
    "tests/ControllerTest.php",
    "src/styles/modal.scss",
    "e2e/checkout.spec.ts",
    ".github/workflows/ci.yml",
    "Dockerfile",
    "src/utils/auth.go",
    "src/utils/auth_test.go",
]

_QUICK_MODE_BLOCKED_AGENTS = frozenset([
    "wp-architecture-reviewer",
    "history-insights-reviewer",
    "data-flow-privacy-reviewer",
    "concurrency-reviewer",
    "reliability-reviewer",
])


def _init_main_repo(path):
    """A git repo with a `main` branch at HEAD.

    build_dispatch_plan's triage calls plan_dispatch.get_diff_text() /
    get_repository_identity() via `git diff`/`git rev-parse`, with no cwd
    override — they always read the ambient process CWD, not a subprocess
    we control. Left unpatched, `git_range="main..HEAD"` behaves
    differently depending on which repo pytest happens to be invoked from:
    inside this repo the pathspec resolves to an empty diff (low-signal,
    quick mode skips); from a foreign CWD `git diff` fails outright
    ("not a git repository"), which the triage treats as an unreadable
    scan and dispatches conservatively instead of skipping. Pointing CWD at
    a throwaway repo with a `main` branch at HEAD makes `main..HEAD`
    resolve to an empty diff everywhere, so the test stops depending on
    which repo happens to be running it.
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "checkout", "-q", "-B", "main"], cwd=path, check=True)
    (path / "README.md").write_text("init")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        cwd=path, check=True,
    )
    return path


class TestQuickModeDispatch:
    """Quick mode excludes low-signal agents from dispatch."""

    @pytest.fixture(scope="class")
    def registry(self):
        return load_registry()

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        """build_dispatch_plan calls straight into plan_dispatch's git
        helpers (no subprocess seam to pass cwd through), so isolation here
        means chdir'ing the test process itself — see _init_main_repo."""
        _init_main_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

    def test_quick_mode_excludes_blocklisted_agents_without_signals(self, registry):
        """quick=True skips blocklisted agents when no triage keywords match."""
        # Use files that don't trigger keyword matches for blocklisted agents
        # (no "hook", "filter", "concurrent", "privacy", "deploy", etc.)
        neutral_files = [
            "src/Controller.php",
            "src/components/Modal.tsx",
            "tests/ControllerTest.php",
            "src/utils/helpers.go",
        ]
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-quick",
            changed_files=neutral_files,
            registry=registry,
            quick=True,
            commit_messages="fix button alignment in modal",
            # This test pins quick-mode relabeling of conservative dispatch.
            diffstat={
                "added": 200,
                "removed": 40,
                "deleted_files": [],
                "renamed_files": [],
                "file_stats": {f: {"added": 50, "removed": 10} for f in neutral_files},
            },
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        for agent_name in _QUICK_MODE_BLOCKED_AGENTS:
            if agent_name not in dispatch_map:
                continue  # agent may have no files in domain
            assert dispatch_map[agent_name]["status"] == "SKIPPED_QUICK_MODE", (
                f"Expected SKIPPED_QUICK_MODE for '{agent_name}', "
                f"got '{dispatch_map[agent_name]['status']}'"
            )

    def test_normal_mode_does_not_exclude(self, registry):
        """quick=False (default) does not produce SKIPPED_QUICK_MODE status."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-normal",
            changed_files=_QUICK_MODE_TEST_FILES,
            registry=registry,
            quick=False,
        )
        for entry in plan["agents"]:
            assert entry["status"] != "SKIPPED_QUICK_MODE", (
                f"Agent '{entry['name']}' should not have SKIPPED_QUICK_MODE "
                f"when quick=False"
            )

    def test_quick_mode_non_blocked_agents_triage_normally(self, registry):
        """quick=True does not affect non-blocked agents — code-reviewer still dispatches."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-quick",
            changed_files=_QUICK_MODE_TEST_FILES,
            registry=registry,
            quick=True,
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        assert dispatch_map["code-reviewer"]["status"] == "DISPATCH", (
            "code-reviewer should still DISPATCH in quick mode"
        )

    def test_quick_mode_honors_keyword_triage(self, registry):
        """Blocklisted agents with keyword matches should still dispatch in quick mode."""
        plan = build_dispatch_plan(
            mode="full",
            git_range="main..HEAD",
            output_dir="/tmp/test-quick-keywords",
            changed_files=_QUICK_MODE_TEST_FILES,
            registry=registry,
            quick=True,
            # Commit messages with keywords that match blocklisted agents
            commit_messages="fix concurrent race condition in payment hook filter",
        )
        dispatch_map = {d["name"]: d for d in plan["agents"]}
        # concurrency-reviewer should dispatch (keyword "concurrent" matched)
        assert dispatch_map["concurrency-reviewer"]["status"] == "DISPATCH", (
            "concurrency-reviewer should DISPATCH when keywords match, "
            f"got {dispatch_map['concurrency-reviewer']['status']}"
        )
        # wp-architecture-reviewer should dispatch (keyword "hook"/"filter" matched)
        assert dispatch_map["wp-architecture-reviewer"]["status"] == "DISPATCH", (
            "wp-architecture-reviewer should DISPATCH when keywords match, "
            f"got {dispatch_map['wp-architecture-reviewer']['status']}"
        )


class TestStep8ReviewFileStems:
    """Step 8's completion check must map agent names to review files by
    terminal-suffix derivation only — a blanket replace looked for
    repo-api-review-v2-review.json and silently excluded valid output."""

    def test_mid_string_reviewer_name_counts_as_completed(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        plan = {"agents": [{
            "name": "repo-api-reviewer-v2-reviewer",
            "status": "DISPATCH",
            "reason": "repo reviewer applicable",
        }]}
        (tmp_path / "dispatch-plan.json").write_text(json.dumps(plan))
        (tmp_path / "repo-api-reviewer-v2-review.json").write_text(
            json.dumps(canonical_review_document("repo-api-reviewer-v2"))
        )
        fake_done = subprocess.CompletedProcess(
            [], returncode=0, stdout="", stderr=""
        )
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: fake_done)

        def fake_run_subprocess(cmd, timeout=None, **kwargs):
            (tmp_path / "reconciliation-context.json").write_text("{}")
            return ("", True)

        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess", fake_run_subprocess
        )
        state = {"resolved_params": {"git_range": "base..head"}}

        mod._orchestrate_step(
            8, "full", {}, state,
            {"git": {"git_range": "base..head"}}, str(tmp_path),
        )

        assert state["agents"]["completed"] == ["repo-api-reviewer-v2-reviewer"]


class TestStep10CriticSourceRecording:
    """`briefings.py` is pure, so step 10's orchestration is what looks at
    the filesystem — the same division that already puts
    `reconciliation_verdict` in state rather than re-reading the ledger in
    the briefing."""

    @staticmethod
    def _findings(tmp_path):
        (tmp_path / "review-findings.json").write_text(
            json.dumps(_review_json("reconciliator"))
        )

    def test_records_the_first_present_artifact(self, mod, tmp_path):
        self._findings(tmp_path)
        (tmp_path / "review-record.md").write_text("# record")
        (tmp_path / "review-findings.md").write_text("# findings")
        state = {"resolved_params": {}}
        mod._orchestrate_step(10, "full", {}, state, {}, str(tmp_path))
        assert state["critic_source"] == "review-record.md"

    def test_the_report_is_never_a_candidate(self, mod, tmp_path):
        """`review-report.md` is authored at step 11, after this critic
        runs. Listing a file that cannot exist yet would fire the fallback
        branch on every single run."""
        self._findings(tmp_path)
        (tmp_path / "review-report.md").write_text("# stale report")
        state = {"resolved_params": {}}
        mod._orchestrate_step(10, "full", {}, state, {}, str(tmp_path))
        assert state["critic_source"] == "review-findings.json"

    def test_falls_through_to_the_markdown_then_the_ledger(
        self, mod, tmp_path
    ):
        self._findings(tmp_path)
        (tmp_path / "review-findings.md").write_text("# findings")
        state = {"resolved_params": {}}
        mod._orchestrate_step(10, "full", {}, state, {}, str(tmp_path))
        assert state["critic_source"] == "review-findings.md"

        (tmp_path / "review-findings.md").unlink()
        state = {"resolved_params": {}}
        mod._orchestrate_step(10, "full", {}, state, {}, str(tmp_path))
        assert state["critic_source"] == "review-findings.json"

    def test_records_an_absence_rather_than_a_guess(self, mod, tmp_path):
        state = {"resolved_params": {}}
        mod._orchestrate_step(10, "full", {}, state, {}, str(tmp_path))
        assert state["critic_source"] is None

    def test_leaves_the_render_reason_where_it_already_lived(
        self, mod, tmp_path
    ):
        """The briefing names the incomplete render off `degradation`.

        `critic_source` used to carry a `render_incomplete` copy of this
        flag, derived from the same state dict the briefing already reads.
        """
        self._findings(tmp_path)
        state = {
            "resolved_params": {},
            "degradation": {"findings_markdown_incomplete": True},
        }
        mod._orchestrate_step(10, "full", {}, state, {}, str(tmp_path))
        assert state["critic_source"] == "review-findings.json"
        assert state["degradation"]["findings_markdown_incomplete"] is True


class TestLedgerStatusIsOneFact:
    """Steps 9, 10, and 11 record the status of the ledger they read.

    Four state keys carried this one fact between them —
    `structured_findings_available` and `render_incomplete` nested inside
    `critic_source`, `findings_read_status` at the top level, and
    `state["review_verdict"]` duplicating `state["verdict"]` on the very
    next line. Briefings read `findings_read_status` from three separate
    local re-reads, and nothing read `review_verdict` at all.
    """

    @pytest.mark.parametrize("step", [9, 10, 11])
    def test_each_step_records_the_status_of_the_ledger_it_read(
        self, mod, tmp_path, step
    ):
        TestStep10CriticSourceRecording._findings(tmp_path)
        state = {"resolved_params": {}}

        mod._orchestrate_step(step, "full", {}, state, {"git": {}}, str(tmp_path))

        assert state["ledger_status"] == "ok"

    @pytest.mark.parametrize("step", [9, 10, 11])
    def test_an_absent_ledger_is_recorded_as_absent(self, mod, tmp_path, step):
        state = {"resolved_params": {}}

        mod._orchestrate_step(step, "full", {}, state, {"git": {}}, str(tmp_path))

        assert state["ledger_status"] == "absent"

    def test_the_four_retired_flags_are_gone(self, mod, tmp_path):
        TestStep10CriticSourceRecording._findings(tmp_path)
        state = {"resolved_params": {}}

        for step in (9, 10, 11):
            mod._orchestrate_step(
                step, "full", {}, state, {"git": {}}, str(tmp_path)
            )

        assert "findings_read_status" not in state
        assert "review_verdict" not in state
        assert state["verdict"]
        assert isinstance(state["critic_source"], (str, type(None)))

    def test_critic_source_is_the_target_filename(
        self, mod, orchestration_mod, tmp_path
    ):
        TestStep10CriticSourceRecording._findings(tmp_path)
        state = {"resolved_params": {}}

        mod._orchestrate_step(9, "full", {}, state, {"git": {}}, str(tmp_path))
        mod._orchestrate_step(10, "full", {}, state, {"git": {}}, str(tmp_path))

        assert state["critic_source"] == orchestration_mod.REVIEW_RECORD_MD


class TestFindingsMarkdownLockstep:
    """One helper records the outcome and its degradation flag together.

    Step 9 kept them in lockstep and step 11 updated only the outcome, so a
    step-9 failure that step 11 repaired left the stale flag standing — and
    the flag is what the step-10 fallback reads.
    """

    @staticmethod
    def _findings(tmp_path):
        data = _review_json("reconciliator")
        (tmp_path / "review-findings.json").write_text(json.dumps(data))

    def test_step_11_clears_a_stale_incomplete_flag_on_a_good_render(
        self, mod, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        self._findings(tmp_path)
        (tmp_path / "review-report.md").write_text("# report")
        state = {"degradation": {"findings_markdown_incomplete": True}}

        mod._orchestrate_step(11, "full", {}, state, {}, str(tmp_path))

        assert state["findings_markdown"]["status"] == "complete"
        assert "findings_markdown_incomplete" not in state.get(
            "degradation", {}
        )

    def test_step_11_sets_the_flag_when_its_own_render_fails(
        self, mod, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        self._findings(tmp_path)
        (tmp_path / "review-report.md").write_text("# report")
        monkeypatch.setitem(
            mod._orchestrate_step_11.__globals__,
            "_load_output_module",
            failing_findings_renderer(
                mod._orchestrate_step_11.__globals__["_load_output_module"],
                "boom",
            ),
        )
        state = {}

        mod._orchestrate_step(11, "full", {}, state, {}, str(tmp_path))

        assert state["findings_markdown"]["status"] == "failed"
        assert state["degradation"]["findings_markdown_incomplete"] is True

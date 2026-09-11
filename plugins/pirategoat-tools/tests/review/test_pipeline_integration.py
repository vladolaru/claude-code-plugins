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
    run_paths,
)
from review.agent.review_assignment import derive_reviewed_files
from review.manifest_sections import aggregate_file_review
from review.critic_adjustments import write_findings
from review.telemetry import ReviewTelemetry
from review.verdict_rules import verdict_for_counts

sys.path.insert(0, str(TESTS_DIR))
from helpers.pipeline_process import (
    add_commit as _add_commit,
    add_origin,
    hermetic_env,
    init_bare_repo,
    init_repo as _init_git_repo,
    run_pipeline,
)
from helpers.gh_shim import gh_call_argv, install_gh_shim, write_user_config
from helpers.review_fixtures import (
    canonical_assignment,
    canonical_findings_ledger,
    canonical_review_document,
    failing_findings_renderer,
)
_output_spec = importlib.util.spec_from_file_location(
    "pipeline_integration_review_output",
    str(_SCRIPTS_DIR / "review" / "agent" / "output.py"),
)
_output_mod = importlib.util.module_from_spec(_output_spec)
_output_spec.loader.exec_module(_output_mod)

_markdown_spec = importlib.util.spec_from_file_location(
    "pipeline_integration_review_markdown",
    str(_SCRIPTS_DIR / "review" / "review_markdown.py"),
)
_markdown_mod = importlib.util.module_from_spec(_markdown_spec)
_markdown_spec.loader.exec_module(_markdown_mod)
_render_markdown = _markdown_mod.render_markdown


from helpers.review_fixtures import artifact_file as _artifact  # noqa: E402


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
    path = Path(reviewer_lifecycle.review_paths(
        output_dir, reviewer
    ).assignment)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(canonical_assignment(
        reviewer,
        agent_name=agent_name,
        inline_diff_file_count=1,
    )))


def _save_and_finalize(output_dir, reviewer, agent_name=None):
    _write_required_assignment(output_dir, reviewer, agent_name)
    saved = _output_mod.ReviewOutputBuilder.open(
        output_dir, "42", reviewer
    ).save_draft()
    return _output_mod.finalize_review(
        str(output_dir), reviewer, saved["review_digest"]
    )


def _write_final_review(output_dir, reviewer, payload):
    path = Path(reviewer_lifecycle.review_paths(output_dir, reviewer).final)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _write_scope_summary(output_dir, reviewer, payload, domain=None):
    path = Path(reviewer_lifecycle.scope_summary_path(
        output_dir, reviewer, domain
    ))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


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



def _record_timeouts(monkeypatch, orchestration_mod):
    """Replace the orchestrator's subprocess seam with one that succeeds
    and records the timeout each call was given."""
    seen = []

    def fake_run_subprocess(cmd, cwd=None, timeout=60):
        seen.append(timeout)
        return "", True

    monkeypatch.setattr(orchestration_mod, "_run_subprocess", fake_run_subprocess)
    return seen

class TestReviewerDraftFinalizationLifecycle:
    def test_last_draft_is_the_only_synthesis_input(
        self, tmp_path
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        output_dir = run_paths.allocate_run_dir(tmp_path / "target")

        (_artifact(output_dir, "dispatch_plan")).write_text(json.dumps({
            "agents": [{
                "name": "code-reviewer",
                "domain": "code",
                "status": "DISPATCH",
                "reason": "always",
            }],
            "git_range": "HEAD~1..HEAD",
        }))
        marker = Path(reviewer_lifecycle.started_marker_path(output_dir, "code"))
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            datetime.now(timezone.utc).isoformat()
        )
        assignment = canonical_assignment(
            "code",
            review_claimable_files=[
                "claimable/read.py",
                "claimable/unread.py",
            ],
            inline_diff_file_count=1,
        )
        Path(reviewer_lifecycle.review_paths(output_dir, "code").assignment).write_text(
            json.dumps(assignment)
        )
        Path(reviewer_lifecycle.scope_summary_path(output_dir, "code")).write_text(
            json.dumps({
                "schema": 3,
                "inline_diff_files": ["second.txt"],
                "review_claimable_files": [
                    "claimable/read.py",
                    "claimable/unread.py",
                ],
                "list_only_files": [],
                "routing_files": [
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
        canonical_path = Path(reviewer_lifecycle.review_paths(output_dir, "code").final)
        canonical_bytes = canonical_path.read_bytes()
        assert canonical_bytes == last_bytes
        assert hashlib.sha256(canonical_bytes).hexdigest() == (
            finalized["review_digest"]
        )

        intake = reviewer_lifecycle.close_review_intake(
            str(output_dir), ["code-reviewer"]
        )
        assert intake["discarded_drafts"] == []
        written = _markdown_mod.materialize_markdown(str(output_dir))
        assert written == [reviewer_lifecycle.reviewer_markdown_path(output_dir, "code")]

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
            "claimable/read.py": ["code"],
        }
        assert run_file_review["agents_with_unclaimed_review_by_file"] == {
            "claimable/unread.py": ["code"],
        }
        assert Path(reviewer_lifecycle.reviewer_markdown_path(output_dir, "code")).read_text() == (
            _render_markdown(canonical)
        )
        assert manifest["status"] == "complete"
        assert manifest["agents"]["completed"][0]["review_digest"] == (
            last["review_digest"]
        )
        assert list(output_dir.glob("*-review.draft.json")) == []
        assert list(output_dir.glob("*-review.json")) == []
        assert list(output_dir.glob("*-review.md")) == []
        assert list(output_dir.glob("*-assignment.json")) == []
        assert list(output_dir.glob("*-scope-summary*")) == []
        assert list(output_dir.glob("*-scoped-diff*")) == []
        assert list(output_dir.glob("*.started")) == []
        assert list(output_dir.glob("*.tmp")) == []
        boundary_files = {
            name for subdir, name in run_paths.ARTIFACTS.values() if not subdir
        }
        grouped_subdirs = {
            run_paths.PIPELINE_SUBDIR,
            run_paths.REVIEWERS_SUBDIR,
            run_paths.SYNTHESIS_SUBDIR,
            run_paths.SCRATCH_SUBDIR,
        }
        root_entries = {path.name for path in output_dir.iterdir()}
        assert root_entries <= boundary_files | grouped_subdirs
        assert grouped_subdirs <= root_entries


class TestCriticAdjudicationLifecycle:
    def test_committed_proposal_is_settled_and_published_once(
        self, mod, tmp_path, monkeypatch
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        output_dir = tmp_path / "out"
        monkeypatch.chdir(repo)
        monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent-xdg")
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py",
            "--step", "1", "--mode", "full", "--pr-number", "42",
            "--interactive", "false", "--output-dir", str(output_dir),
        ])
        mod.main()

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
            (_artifact(output_dir, "critic_adjustments")).read_text()
        )
        marker = json.loads(
            (_artifact(output_dir, "critic_verdict")).read_text()
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
            _artifact(output_dir, "critic_adjustments")
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
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "11", "--mode", "full",
            "--output-dir", str(output_dir),
        ])
        mod.main()
        assert settled_proposal_path.read_bytes() == proposal_bytes
        assert settled_ledger_path.read_bytes() == ledger_bytes
        state = json.loads((_artifact(output_dir, "pipeline_state")).read_text())
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
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "11", "--mode", "full",
            "--output-dir", str(output_dir),
        ])
        mod.main()
        assert settled_proposal_path.read_bytes() == proposal_bytes
        assert settled_ledger_path.read_bytes() == ledger_bytes
        result = json.loads((output_dir / "pipeline-result.json").read_text())
        assert result["verdict"] == "REQUEST_CHANGES"
        assert result["report_path"] == str(report)
        telemetry_log = Path(
            (_artifact(output_dir, "telemetry_log_path")).read_text().strip()
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
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)
        output_dir = tmp_path / "out"
        monkeypatch.chdir(repo)
        monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent-xdg")
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(output_dir),
            "--git-range", "HEAD~1..HEAD", "--refresh-deps",
        ])
        mod.main()
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "3", "--mode", "full",
            "--output-dir", str(output_dir),
        ])
        mod.main()

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
        )
        assert saved.returncode == 0, saved.stderr
        assert saved.stdout.strip() == "SAVED dependency-refresh.json"
        canonical_path = _artifact(output_dir, "dependency_refresh")
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

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "5", "--mode", "full",
            "--output-dir", str(output_dir),
        ])
        mod.main()
        state = json.loads((_artifact(output_dir, "pipeline_state")).read_text())
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
            for path in _artifact(
                output_dir, "dependency_refresh"
            ).parent.iterdir()
            if path.name.startswith("dependency-refresh")
        ] == ["dependency-refresh.json"]
        assert not (output_dir / "dependency-refresh.json").exists()


class TestTelemetryIntegration:
    """Verify pipeline calls telemetry at each step."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path):
        """Every subprocess call in this class needs an isolated cwd — see
        run_pipeline's docstring. The repo lives at `tmp_path/repo`, never
        at `tmp_path` itself, and stays a sibling of `tmp_path/out` so run
        artifacts cannot dirty it. Tests that need a specific git identity
        build their own `repo` subdir the same way."""
        (tmp_path / "repo").mkdir()
        _init_git_repo(tmp_path / "repo")
        (tmp_path / "out").mkdir()

    def test_step_2_appends_to_telemetry_log(self, mod, tmp_path, monkeypatch):
        """Step 1 creates the telemetry log and running manifest; step 3 appends to it."""
        out = tmp_path / "out"
        log_dir = tmp_path / "telemetry-logs"
        monkeypatch.setenv("PIRATEGOAT_TELEMETRY_LOG_DIR", str(log_dir))
        monkeypatch.chdir(tmp_path / "repo")

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "pr",
            "--output-dir", str(out), "--pr-number", "42",
        ])
        mod.main()
        marker = _artifact(out, "telemetry_log_path")
        assert marker.is_file()
        log_path = Path(marker.read_text().strip())
        manifest_path = log_path.with_suffix(".manifest.json")
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["status"] == "running"

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "3", "--mode", "pr",
            "--output-dir", str(out),
        ])
        mod.main()

        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "pipeline_start"
        assert json.loads(lines[1])["event"] == "step"

    def test_telemetry_failure_does_not_break_pipeline(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        """Pipeline works even if telemetry log_dir is unwritable."""
        out = tmp_path / "out"
        log_dir = tmp_path / "unwritable"
        log_dir.mkdir()
        log_dir.chmod(0o000)
        monkeypatch.setenv("PIRATEGOAT_TELEMETRY_LOG_DIR", str(log_dir))
        monkeypatch.chdir(tmp_path / "repo")
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "pr",
            "--output-dir", str(out), "--pr-number", "42",
        ])
        try:
            mod.main()
            output = capsys.readouterr().out
            assert "Step 1" in output
        finally:
            log_dir.chmod(0o755)

    def test_step_1_uses_preserved_bot_context_git_identity(
        self, mod, tmp_path, monkeypatch
    ):
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
        monkeypatch.setenv("PIRATEGOAT_TELEMETRY_LOG_DIR", str(log_dir))
        monkeypatch.chdir(tmp_path / "repo")
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--output-dir", str(out),
        ])
        mod.main()

        log_path = (_artifact(out, "telemetry_log_path")).read_text().strip()
        with open(log_path) as f:
            start = json.loads(f.readline())
        assert start["pipeline"]["git"] == {
            "requested_range": "context-base..context-head",
            "base_sha": context_base,
            "head_sha": context_head,
        }

    @pytest.mark.parametrize(
        ("git_range", "add_extra_commit"),
        (
            pytest.param("", False, id="no_range"),
            pytest.param("HEAD~1..HEAD~1", True, id="range_HEAD~1..HEAD~1"),
        ),
    )
    def test_interactive_identity_ignores_context(
        self, mod, tmp_path, monkeypatch, git_range, add_extra_commit
    ):
        """Interactive runs resolve current Git identity, never the preserved
        (possibly stale) review-context.json value — one branch in main()
        (`git_context = {} if interactive`), exercised with the range unset
        and with an explicit range that happens to match the stale context."""
        out = tmp_path / "out"
        repo = tmp_path / "repo"
        config = {"mode": "full", "interactive": True}
        if git_range:
            config["git_range"] = git_range
        (out / "run-config.json").write_text(json.dumps(config))
        (out / "review-context.json").write_text(json.dumps({
            "git": {
                "git_range": git_range or "stale-base..stale-head",
                "merge_base": "stale-base-sha",
                "head_sha": "stale-head-sha",
            },
        }))
        log_dir = tmp_path / "telemetry-logs"
        if add_extra_commit:
            _add_commit(repo)
            expected_sha = subprocess.check_output(
                ["git", "rev-parse", "--verify", "HEAD~1"], cwd=repo, text=True
            ).strip()
            expected = {
                "requested_range": git_range,
                "base_sha": expected_sha,
                "head_sha": expected_sha,
            }
        else:
            current_head = subprocess.check_output(
                ["git", "rev-parse", "--verify", "HEAD"], cwd=repo, text=True
            ).strip()
            expected = {
                "requested_range": "",
                "base_sha": "",
                "head_sha": current_head,
            }

        monkeypatch.setenv("PIRATEGOAT_TELEMETRY_LOG_DIR", str(log_dir))
        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--output-dir", str(out),
        ])
        mod.main()

        log_path = (_artifact(out, "telemetry_log_path")).read_text().strip()
        with open(log_path) as f:
            start = json.loads(f.readline())
        assert start["pipeline"]["git"] == expected
        manifest = json.loads(Path(log_path).with_suffix(".manifest.json").read_text())
        assert manifest["run"]["git"] == {**expected, "base_fetch": None, "scope_check": None}


class TestTelemetrySharingIntegration:
    """The terminal pipeline invocation owns the scripted upload opportunity."""

    @staticmethod
    def _write_consent(config_home, sharing, repo_consent):
        if sharing == "unset":
            return
        telemetry = {"sharing": sharing}
        if repo_consent != "unset":
            telemetry["repos"] = {"github.com/acme/widget": repo_consent}
        write_user_config(config_home, {"telemetry": telemetry})

    def _run_shared_review(self, tmp_path, *, sharing, repo_consent, **shim_options):
        """Drive a run to its terminal step with consent and a gh shim in place.

        Returns ``(completed process, gh call log)``; only the consent values
        and the shim's failure mode vary between the sharing tests.
        """
        repo = add_origin(
            _init_git_repo(tmp_path), "https://github.com/acme/widget.git"
        )
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        config_home = tmp_path / "xdg"
        self._write_consent(config_home, sharing, repo_consent)
        call_log = tmp_path / "gh-calls.jsonl"
        bin_dir = install_gh_shim(tmp_path / "bin", call_log, **shim_options)
        env = hermetic_env(
            XDG_CONFIG_HOME=str(config_home),
            PIRATEGOAT_TELEMETRY_LOG_DIR=str(tmp_path / "telemetry"),
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        )

        started = run_pipeline(
            "--step", "1", "--mode", "full",
            "--output-dir", str(output_dir), cwd=repo, env=env,
        )
        assert started.returncode == 0, started.stderr
        completed = run_pipeline(
            "--step", "12", "--output-dir", str(output_dir), cwd=repo, env=env,
        )
        return completed, call_log

    @pytest.mark.parametrize(
        ("sharing", "repo_consent", "expected_line", "expected_puts"),
        (
            ("enabled", "include", "TELEMETRY: shared", 2),
        ),
    )
    def test_terminal_step_honors_consent_before_uploading(
        self, tmp_path, sharing, repo_consent, expected_line, expected_puts
    ):
        """The only end-to-end PATH/gh-shim plumbing test — kept as a
        subprocess smoke; every consent gate itself is unit-pinned in
        test_telemetry_share.py::TestMaybeUpload::test_each_consent_state_gates_correctly."""
        completed, call_log = self._run_shared_review(
            tmp_path, sharing=sharing, repo_consent=repo_consent
        )

        assert completed.returncode == 0, completed.stderr
        telemetry_lines = [
            line for line in completed.stdout.splitlines()
            if line.startswith("TELEMETRY:")
        ]
        assert len(telemetry_lines) == 1
        assert telemetry_lines[0].startswith(expected_line)
        calls = gh_call_argv(call_log)
        assert len([argv for argv in calls if "PUT" in argv]) == expected_puts

    def test_terminal_step_silently_skips_when_sharing_is_disabled(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        """Disabled sharing consent never reaches the gh shim; the other
        consent gates (unset, per-repo exclude) are unit-pinned in
        test_telemetry_share.py::TestMaybeUpload::test_each_consent_state_gates_correctly."""
        repo = add_origin(
            _init_git_repo(tmp_path), "https://github.com/acme/widget.git"
        )
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        config_home = tmp_path / "xdg"
        self._write_consent(config_home, "disabled", "unset")
        call_log = tmp_path / "gh-calls.jsonl"
        bin_dir = install_gh_shim(tmp_path / "bin", call_log)

        monkeypatch.chdir(repo)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
        monkeypatch.setenv("PIRATEGOAT_TELEMETRY_LOG_DIR", str(tmp_path / "telemetry"))
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(output_dir),
        ])
        mod.main()
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "12", "--output-dir", str(output_dir),
        ])
        mod.main()

        out = capsys.readouterr().out
        telemetry_lines = [
            line for line in out.splitlines() if line.startswith("TELEMETRY:")
        ]
        assert telemetry_lines == []
        assert gh_call_argv(call_log) == []



class TestStep2Orchestration:
    """Step 2 main() runs review/workspace_setup.py and persists workspace state."""

    def test_step_2_completes_and_stores_workspace_setup_result(
        self, mod, tmp_path, monkeypatch
    ):
        """Step 2 should complete even when review/workspace_setup.py fails
        (no git repo), and persist its result to state."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        out = tmp_path / "out"
        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "pr",
            "--output-dir", str(out), "--pr-number", "42",
        ])
        mod.main()
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "2", "--mode", "pr",
            "--output-dir", str(out),
        ])
        mod.main()
        state = json.loads((_artifact(out, "pipeline_state")).read_text())
        assert 2 in state["completed_steps"]
        assert "workspace_setup_result" in state


class TestStep3Orchestration:
    """Step 3 main() runs review/context.py and hydrates state."""

    def test_step_3_runs_gather_context(self, mod, tmp_path, monkeypatch):
        """Step 3 should invoke review/context.py (may fail in test env, but state should update)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "3", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        state = json.loads((_artifact(tmp_path, "pipeline_state")).read_text())
        assert 3 in state["completed_steps"]

    def test_step_3_hydrates_unfetched_issues_from_context(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        """When review-context.json has has_unfetched_issues, state reflects
        it and the next step routes to 4, not 5."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "pr",
            "--output-dir", str(tmp_path), "--pr-number", "42",
        ])
        mod.main()
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
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "3", "--mode", "pr",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        output = capsys.readouterr().out
        state = json.loads((_artifact(tmp_path, "pipeline_state")).read_text())
        assert state["resolved_params"]["has_unfetched_issues"] is True
        assert "Step 4" in output

    def test_incremental_step_3_points_at_the_previous_runs_change_purpose(self, mod, tmp_path, monkeypatch):
        previous = tmp_path / "previous-run"
        (_artifact(previous, "change_purpose")).write_text("## Verify\nNone.\n")
        target = tmp_path / "target"
        target.mkdir()
        (target / ".branch-review-baseline.json").write_text(json.dumps({
            "last_reviewed_sha": "0000000", "review_count": 1,
            "last_run_dir": str(previous),
        }))
        out = tmp_path / "out"
        out.mkdir()
        monkeypatch.setitem(
            mod._orchestrate_step_3.__globals__, "_run_subprocess",
            lambda *a, **k: ("", True),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_3.__globals__, "_capture_worktree_baseline",
            lambda *_a, **_k: None,
        )
        state = {"resolved_params": {}}
        mod._orchestrate_step(3, "incremental", {"target_dir": str(target), "mode": "incremental"}, state, {}, str(out))
        assert state["previous_change_purpose"] == str(_artifact(previous, "change_purpose"))

    @pytest.mark.parametrize(
        "baseline",
        (
            pytest.param(
                json.dumps({"last_reviewed_sha": "0000000", "review_count": 1}),
                id="no_last_run_dir",
            ),
            pytest.param(
                json.dumps({"last_reviewed_sha": "0000000", "last_run_dir": "<missing>"}),
                id="last_run_dir_missing_change_purpose",
            ),
            pytest.param(json.dumps([]), id="not_an_object"),
            pytest.param("not json", id="not_json"),
        ),
    )
    def test_incremental_step_3_tolerates_a_useless_baseline(self, mod, tmp_path, monkeypatch, baseline):
        """No previous run recorded, a recorded run dir with no change
        purpose in it, a baseline that is not an object, or one that is not
        JSON all mean "no pointer"."""
        target = tmp_path / "target"
        target.mkdir()
        (target / ".branch-review-baseline.json").write_text(
            baseline.replace("<missing>", str(tmp_path / "gone"))
        )
        out = tmp_path / "out"
        out.mkdir()
        monkeypatch.setitem(
            mod._orchestrate_step_3.__globals__, "_run_subprocess",
            lambda *a, **k: ("", True),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_3.__globals__, "_capture_worktree_baseline",
            lambda *_a, **_k: None,
        )
        state = {"resolved_params": {}}
        mod._orchestrate_step(3, "incremental", {"target_dir": str(target), "mode": "incremental"}, state, {}, str(out))
        assert "previous_change_purpose" not in state

    def test_step_2_lets_the_pr_checkout_use_its_whole_timeout(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        """The workspace wrapper allows the checkout 300 s; the pipeline
        must not kill the wrapper first, or the checkout keeps changing the
        tree after the recovery metadata was lost."""
        from review.workspace_setup import CHECKOUT_TIMEOUT_SECONDS
        seen_timeouts = _record_timeouts(monkeypatch, orchestration_mod)
        mod._orchestrate_step(
            2, "pr", {"pr_number": "42"},
            {"resolved_params": {}, "workspace": {}}, {}, str(tmp_path),
        )
        assert seen_timeouts
        assert seen_timeouts[0] > CHECKOUT_TIMEOUT_SECONDS

    def test_step_3_allows_known_ecosystem_cache_refreshes_to_finish(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        """The context wrapper should allow both known host caches to refresh."""
        seen_timeouts = _record_timeouts(monkeypatch, orchestration_mod)
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

    def test_step_3_skips_detection_without_opt_in(self, mod, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _add_commit(repo)

        out_dir = tmp_path / "out"
        monkeypatch.chdir(repo)
        monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent-xdg")
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(out_dir),
            "--git-range", "HEAD~1..HEAD",
        ])
        mod.main()
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "3", "--mode", "full",
            "--output-dir", str(out_dir),
        ])
        mod.main()

        state = json.loads((_artifact(out_dir, "pipeline_state")).read_text())
        assert "dependency_refresh_precheck" not in state


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

    def test_step_5_parses_the_change_purpose_into_state(
        self, mod, tmp_path, monkeypatch
    ):
        repo = self._make_repo(tmp_path)
        out = tmp_path / "out"
        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(out),
        ])
        mod.main()
        ctx = {
            "git": {"merge_base": "abc", "git_range": "abc..HEAD",
                    "changed_files": ["a.py"], "commit_count": 1},
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (out / "review-context.json").write_text(json.dumps(ctx))
        purpose = _artifact(out, "change_purpose")
        purpose.parent.mkdir(parents=True, exist_ok=True)
        purpose.write_text(
            "## Verify\nV1. claim — source: PR description\n"
            "## Context\nC1. fact — source: inferred from the diff\n"
            "## Author's description (extracted)\nquoted\n"
        )
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "5", "--mode", "full",
            "--output-dir", str(out),
        ])
        mod.main()
        state = json.loads((_artifact(out, "pipeline_state")).read_text())
        assert state["change_purpose_items"]["structured"] is True
        assert [i["id"] for i in state["change_purpose_items"]["verify"]] == ["V1"]
        assert state["change_purpose_items"]["problems"] == [
            "C1 is inferred from the diff and may not be Context"
        ]

    @pytest.mark.parametrize(
        ("opted_in", "flag"),
        (
            pytest.param(True, "--refresh-deps", id="missing_report"),
            pytest.param(False, "--no-refresh-deps", id="no_opt_in"),
        ),
    )
    def test_step_5_refresh_report_state(
        self, mod, tmp_path, monkeypatch, opted_in, flag
    ):
        """No dependency-refresh report was ever saved: opted in records
        `None` (no replacement artifact appears); not opted in never records
        the key at all — the saved-report case is asserted end to end by
        TestDependencyRefreshSaveLifecycle."""
        repo = self._make_repo(tmp_path)
        out = tmp_path / "out"
        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(out), "--git-range", "HEAD~1..HEAD", flag,
        ])
        mod.main()

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "5", "--mode", "full",
            "--output-dir", str(out),
        ])
        mod.main()

        state = json.loads((_artifact(out, "pipeline_state")).read_text())
        if opted_in:
            assert state["dependency_refresh_report"] is None
            assert not (_artifact(out, "dependency_refresh")).exists()
        else:
            assert "dependency_refresh_report" not in state

    def test_step_5_preserves_initial_plan_before_orchestrator_adjustment(
        self, mod, tmp_path, monkeypatch
    ):
        """Step 5 stores the dispatch plan summary and keeps the
        deterministic plan unchanged for measurement."""
        repo = self._make_repo(tmp_path)
        out = tmp_path / "out"
        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(out),
        ])
        mod.main()
        ctx = {
            "git": {
                "git_range": "HEAD~1..HEAD",
                "changed_files": ["plugins/pirategoat-tools/scripts/review/pipeline.py"],
                "commit_count": 1,
            },
            "pr_size": {"files": 1, "lines": 10, "category": "tiny"},
        }
        (out / "review-context.json").write_text(json.dumps(ctx))

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "5", "--mode", "full",
            "--output-dir", str(out),
        ])
        mod.main()

        state = json.loads((_artifact(out, "pipeline_state")).read_text())
        assert 5 in state["completed_steps"]
        assert "dispatch_plan_summary" in state
        initial_path = _artifact(out, "dispatch_plan_initial")
        final_path = _artifact(out, "dispatch_plan")
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
        final_path = _artifact(tmp_path, "dispatch_plan")
        initial_path = _artifact(tmp_path, "dispatch_plan_initial")
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
        plan = json.loads((_artifact(out, "dispatch_plan")).read_text())
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
        initial_path = _artifact(tmp_path, "dispatch_plan_initial")
        final_path = _artifact(tmp_path, "dispatch_plan")
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
        final_path = _artifact(tmp_path, "dispatch_plan")
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
        assert not (_artifact(tmp_path, "dispatch_plan_initial")).exists()

    def test_successful_planner_with_invalid_plan_shape_surfaces_value_error(
        self, mod, orchestration_mod, tmp_path, monkeypatch
    ):
        """Subprocess success cannot hide a malformed planner artifact."""
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps(["not", "a", "plan"]))
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

        assert not (_artifact(tmp_path, "dispatch_plan_initial")).exists()


class TestStep6Orchestration:
    """Step 6 main() reads dispatch-plan.json and populates dispatched_agents."""

    def test_step_6_populates_dispatched_agents(self, mod, tmp_path, monkeypatch):
        """Step 6 should read dispatch-plan.json and populate state.dispatched_agents."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        plan = {
            "agents": [
                {"name": "code-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "always"},
                {"name": "go-tests-reviewer", "domain": "go-tests", "status": "SKIPPED", "reason": "no files"},
            ],
            "git_range": "abc..HEAD",
        }
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps(plan))
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "6", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        state = json.loads((_artifact(tmp_path, "pipeline_state")).read_text())
        names = [a["name"] for a in state.get("dispatched_agents", [])]
        assert "code-reviewer" in names
        assert "security-reviewer" in names
        assert "go-tests-reviewer" not in names

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
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps(plan))

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
    """Step 7 main() writes the run's non-incremental baseline."""

    def test_step_7_writes_baseline_file(self, mod, tmp_path, monkeypatch):
        """Step 7 should create the grouped run baseline."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        ctx = {"git": {"git_range": "abc..HEAD", "base_ref": "main"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "7", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        baseline_path = _artifact(tmp_path, "worktree_baseline")
        assert baseline_path.is_file(), "Baseline file was not created"
        baseline = json.loads(baseline_path.read_text())
        assert "last_reviewed_sha" in baseline
        assert "last_reviewed_at" in baseline
        assert "review_type" in baseline
        assert baseline["review_type"] == "full"
        assert "git_range_used" in baseline
        assert ".." in baseline["git_range_used"]

    def test_step_7_guidance_uses_real_status_output_labels(
        self, mod, tmp_path
    ):
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({
            "agents": [
                {"name": "security-reviewer", "status": "DISPATCH"},
            ],
        }))
        marker = Path(reviewer_lifecycle.started_marker_path(tmp_path, "security"))
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            datetime.now(timezone.utc).isoformat()
        )
        Path(reviewer_lifecycle.review_paths(tmp_path, "security").draft).write_text("{}")
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


class TestBaselineInTargetDir:
    def test_incremental_reads_and_writes_the_baseline_in_target_dir(
        self, mod, tmp_path, monkeypatch
    ):
        """Steps 1/3/7 read and write the target repo's baseline (not the
        output dir's), advance its review count, record this run's output
        directory for the next incremental review, and the written file
        passes the baseline grader."""
        from helpers.graders import grade_review_baseline

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        baseline_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        _add_commit(repo)
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        (output_dir / "run-config.json").write_text(json.dumps({
            "target_dir": str(repo),
        }))
        target_baseline = repo / ".branch-review-baseline.json"
        target_baseline.write_text(json.dumps({
            "last_reviewed_sha": baseline_sha,
            "review_count": 4,
        }))

        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "incremental",
            "--output-dir", str(output_dir),
        ])
        mod.main()
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "3", "--output-dir", str(output_dir),
        ])
        mod.main()
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "7", "--output-dir", str(output_dir),
        ])
        mod.main()

        config = json.loads((output_dir / "run-config.json").read_text())
        assert config["target_dir"] == str(repo)
        context = json.loads((output_dir / "review-context.json").read_text())
        assert context["git"]["merge_base"] == baseline_sha
        baseline = json.loads(target_baseline.read_text())
        assert baseline["review_count"] == 5
        assert baseline["last_run_dir"] == str(output_dir)
        assert not (output_dir / ".branch-review-baseline.json").exists()

        result = grade_review_baseline(str(target_baseline))
        assert result.passed, f"Baseline grading failed: {result.failures}"

    def test_incremental_without_target_dir_fails_closed(self, mod, tmp_path):
        with pytest.raises(
            RuntimeError,
            match="incremental review needs target_dir in caller configuration",
        ):
            mod._orchestrate_step(
                7,
                "incremental",
                {"mode": "incremental"},
                {"resolved_params": {}},
                {"git": {}},
                str(tmp_path),
            )

    def test_step_1_deletes_nothing_from_the_output_dir(
        self, mod, tmp_path, monkeypatch
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        leftover = output_dir / "leftover-from-this-run.txt"
        leftover.write_text("keep me")

        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(output_dir),
        ])
        mod.main()

        assert leftover.read_text() == "keep me"


class TestStep8Orchestration:
    """Step 8 main() reads change-purpose.md and agent completion status."""

    def test_step_8_keeps_oversized_host_context_out_of_reconciliation_argv(
        self, mod, tmp_path, monkeypatch
    ):
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}],
        }))
        _save_and_finalize(tmp_path, "code")
        host_context = {
            "version": 1,
            "resolved": [
                {
                    "name": f"wordpress-{index}",
                    "kind": "runtime-host",
                    "path": f"/repo/wordpress-{index}",
                    "source": "ecosystem-cache",
                    "version": None,
                    "notes": {},
                }
                for index in range(9000)
            ],
            "unresolved": [],
            "banner": None,
        }
        (_artifact(tmp_path, "review_context")).write_text(
            json.dumps({"host_context": host_context})
        )
        commands = []

        def reconciliation_succeeds(cmd, *_args, **_kwargs):
            commands.append(list(cmd))
            (_artifact(tmp_path, "reconciliation_context")).write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )

        mod._orchestrate_step(
            8,
            "full",
            {},
            {},
            {"resolved_params": {}, "host_context": host_context},
            str(tmp_path),
        )

        command = commands[-1]
        assert sum(len(argument) for argument in command) < 10_000

    def test_step_8_re_parses_the_change_purpose_the_orchestrator_may_have_edited(
        self, mod, tmp_path, monkeypatch
    ):
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}],
        }))
        _save_and_finalize(tmp_path, "code")
        purpose = _artifact(tmp_path, "change_purpose")
        purpose.parent.mkdir(parents=True, exist_ok=True)
        purpose.write_text("## Verify\nV1. a — source: PR description\nV2. b — source: PR description\n## Context\nNone.\n## Author's description (extracted)\nq\n")
        commands = []

        def reconciliation_succeeds(cmd, *_args, **_kwargs):
            commands.append(list(cmd))
            (_artifact(tmp_path, "reconciliation_context")).write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__, "_run_subprocess", reconciliation_succeeds,
        )
        state = {"resolved_params": {}, "change_purpose_items": {"verify": [{"id": "V1"}], "context": [], "problems": [], "structured": True}}
        mod._orchestrate_step(8, "full", {}, state, {}, str(tmp_path))
        assert [i["id"] for i in state["change_purpose_items"]["verify"]] == ["V1", "V2"]
        assert any("--change-purpose" in cmd for cmd in commands)

    @pytest.mark.parametrize(
        "agents, expect_finalized, expected_dispatched, expected_flag",
        [
            pytest.param(
                [
                    {"name": "code-reviewer", "status": "DISPATCH"},
                    {"name": "a11y-reviewer", "status": "SKIPPED_TRIAGE",
                     "reason": "no frontend files"},
                    {"name": "security-reviewer", "status": "DISPATCH_OVERRIDE"},
                ],
                True,
                ["code-reviewer", "security-reviewer"],
                "code-reviewer,security-reviewer",
                id="mixed_statuses",
            ),
            pytest.param(
                [
                    {"name": "a11y-reviewer", "status": "SKIPPED_TRIAGE",
                     "reason": "docs-only change"},
                ],
                False,
                [],
                "",
                id="all_skipped",
            ),
        ],
    )
    def test_step_8_takes_dispatched_identities_from_the_status_gate(
        self, mod, tmp_path, monkeypatch, agents, expect_finalized,
        expected_dispatched, expected_flag,
    ):
        """One read of the plan, not two.

        The gate already opens and validates `dispatch-plan.json` to
        decide readiness; step 8 re-opened and re-validated the same file
        immediately afterwards purely to recover the dispatched names.
        Both the frozen intake and the reconciliation-context flag now
        come from the gate's own answer. A plan that selected nobody is
        known-empty, never unknown — omitting the flag would tell
        reconciliation_context.py to scan for every `*-review.json` in the
        directory, stale artifacts from an earlier run included.
        """
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({
            "agents": agents,
        }))
        if expect_finalized:
            _save_and_finalize(tmp_path, "code")
        commands = []

        def reconciliation_succeeds(cmd, *_args, **_kwargs):
            commands.append(cmd)
            (_artifact(tmp_path, "reconciliation_context")).write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        mod._orchestrate_step(8, "full", {}, state, {}, str(tmp_path))

        assert state["agents"]["dispatched"] == expected_dispatched
        recon = commands[-1]
        flag = recon.index("--dispatched-agents")
        assert recon[flag + 1] == expected_flag

    def test_step_8_completed_never_names_an_undispatched_agent(
        self, mod, tmp_path, monkeypatch
    ):
        """`completed` stays inside `dispatched`, and in its order.

        Intake close classifies a wider population than this run
        dispatched — it also carries forward every draft a previous close
        discarded — so a resumed close can hand back an agent the plan
        no longer names. The step-8 briefing renders both lists.
        """
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({
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
            "materialize_markdown",
            lambda *_args, **_kwargs: [],
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (_artifact(tmp_path, "reconciliation_context")).write_text("{}")
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

    def test_step_8_preserves_invalid_output_evidence_without_completion(
        self, mod, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}],
        }))
        review_path = Path(reviewer_lifecycle.review_paths(tmp_path, "code").final)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review = canonical_review_document("code")
        review["schema"] = 1
        review_path.write_text(json.dumps(review))

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "8", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()

        state = json.loads((_artifact(tmp_path, "pipeline_state")).read_text())
        assert state["agents"]["completed"] == []
        assert state["reviewer_markdown"]["status"] == "partial"
        assert not Path(reviewer_lifecycle.reviewer_markdown_path(tmp_path, "code")).exists()
        intake = json.loads((_artifact(tmp_path, "review_intake")).read_text())
        assert intake["status"] == "closed"
        context = json.loads(
            (_artifact(tmp_path, "reconciliation_context")).read_text()
        )
        assert context["reviews_by_agent"] == {}
        assert context["missing_agents"] == ["code-review"]
        telemetry_path = Path(
            (_artifact(tmp_path, "telemetry_log_path")).read_text().strip()
        )
        events = [
            json.loads(line) for line in telemetry_path.read_text().splitlines()
        ]
        assert not any(
            event["event"] == "agent_complete" for event in events
        )

    def test_step_8_records_which_dispatched_agents_completed(
        self, mod, tmp_path, monkeypatch
    ):
        """Step 8 stores the intake close's completion classification and
        reads change-purpose.md into state."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        plan = {
            "agents": [
                {"name": "code-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
                {"name": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "always"},
            ],
            "git_range": "abc..HEAD",
        }
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps(plan))
        (_artifact(tmp_path, "change_purpose")).write_text("Adds retry logic to payment gateway.")
        # Simulate code-reviewer finalized, security-reviewer not.
        _save_and_finalize(tmp_path, "code")
        ctx = {"git": {"git_range": "abc..HEAD"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "8", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()

        state = json.loads((_artifact(tmp_path, "pipeline_state")).read_text())
        assert state["agents"]["completed"] == ["code-reviewer"]
        assert "retry logic" in state.get("change_purpose", "").lower()

    @pytest.mark.parametrize(
        "agents, finals, materialize_override, expected_markdown,"
        " expect_degradation, expected_stderr",
        [
            pytest.param(
                [
                    {"name": "code-reviewer", "status": "DISPATCH"},
                    {"name": "security-reviewer", "status": "DISPATCH"},
                ],
                ("code", "security"),
                None,
                {"ran": True, "written": 2, "expected": 2, "status": "complete"},
                False,
                None,
                id="every_settled_json_materializes",
            ),
            pytest.param(
                [{"name": "security-reviewer", "status": "DISPATCH"}],
                ("security",),
                "unrelated_path",
                {"ran": True, "written": 0, "expected": 1, "status": "partial"},
                True,
                None,
                id="materialized_path_identity_mismatch",
            ),
            pytest.param(
                [{"name": "security-reviewer", "status": "DISPATCH"}],
                ("security",),
                "raise",
                {"ran": True, "written": 0, "expected": 1, "status": "failed"},
                True,
                "reviewer markdown materialization failed: renderer crashed",
                id="materialization_raises",
            ),
            pytest.param(
                [{"name": "security-reviewer", "status": "DISPATCH"}],
                (),
                None,
                {"ran": True, "written": 0, "expected": 1, "status": "partial"},
                True,
                None,
                id="skipped_json_is_partial",
            ),
        ],
    )
    def test_step_8_reviewer_markdown_outcome(
        self, mod, tmp_path, monkeypatch, capsys, agents, finals,
        materialize_override, expected_markdown, expect_degradation,
        expected_stderr,
    ):
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({
            "agents": agents,
        }))
        for reviewer in finals:
            _write_final_review(tmp_path, reviewer, _review_json(reviewer))
        if not finals and agents:
            # The dispatched agent settled with an empty (skipped) payload.
            _write_final_review(tmp_path, "security", {})
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        if materialize_override == "unrelated_path":
            unrelated_markdown = Path(
                reviewer_lifecycle.reviewer_markdown_path(tmp_path, "code")
            )
            unrelated_markdown.parent.mkdir(parents=True, exist_ok=True)
            unrelated_markdown.write_text("# Different reviewer\n")
            monkeypatch.setitem(
                mod._orchestrate_step_8.__globals__,
                "materialize_markdown",
                lambda *_args, **_kwargs: [str(unrelated_markdown)],
            )
        elif materialize_override == "raise":
            monkeypatch.setitem(
                mod._orchestrate_step_8.__globals__,
                "materialize_markdown",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("renderer crashed")
                ),
            )

        def reconciliation_succeeds(*_args, **_kwargs):
            (_artifact(tmp_path, "reconciliation_context")).write_text("{}")
            return "", True

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            reconciliation_succeeds,
        )
        state = {"resolved_params": {}}

        result = mod._orchestrate_step(8, "full", {}, state, {}, str(tmp_path))

        assert result == {}
        assert state["reviewer_markdown"] == expected_markdown
        if expect_degradation:
            assert state["degradation"]["reviewer_markdown_incomplete"] is True
        if expected_stderr:
            assert expected_stderr in capsys.readouterr().err
        if materialize_override is None and expected_markdown["written"]:
            for reviewer in finals:
                assert Path(
                    reviewer_lifecycle.reviewer_markdown_path(tmp_path, reviewer)
                ).is_file()

    def test_step_8_closes_intake_before_materialization_and_reconciliation(
        self, mod, tmp_path, monkeypatch
    ):
        plan = {"agents": [
            {"name": "code-reviewer", "status": "DISPATCH"},
        ]}
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps(plan))
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
            "materialize_markdown",
            lambda *_args, **_kwargs: events.append(("materialize", [])) or [],
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            events.append(("reconciliation", []))
            (_artifact(tmp_path, "reconciliation_context")).write_text("{}")
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
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({"agents": []}))
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
            "materialize_markdown",
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
        (_artifact(tmp_path, "dispatch_plan")).write_text(
            json.dumps({"agents": "not a list of agents"})
        )
        draft = tmp_path / "code-review.draft.json"
        draft.write_bytes(b'{"draft":true}\n')
        events = []
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "materialize_markdown",
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
        assert not (_artifact(tmp_path, "review_intake")).exists()
        assert events == []

    def test_step_8_status_checker_exception_preserves_open_intake(
        self, mod, tmp_path, monkeypatch
    ):
        (_artifact(tmp_path, "dispatch_plan")).write_text(
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
            "materialize_markdown",
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
        assert not (_artifact(tmp_path, "review_intake")).exists()
        assert events == []

    def test_step_8_uses_post_render_snapshot_when_json_arrives_during_materialization(
        self, mod, tmp_path, monkeypatch
    ):
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({
            "agents": [
                {"name": "code-reviewer", "status": "DISPATCH"},
                {"name": "security-reviewer", "status": "DISPATCH"},
            ],
        }))
        _write_final_review(tmp_path, "security", _review_json("security"))
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **_kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        original_materialize = mod._orchestrate_step_8.__globals__[
            "materialize_markdown"
        ]

        def publish_then_materialize(output_dir):
            _write_final_review(tmp_path, "code", _review_json("code"))
            return original_materialize(output_dir)

        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "materialize_markdown",
            publish_then_materialize,
        )

        def reconciliation_succeeds(*_args, **_kwargs):
            (_artifact(tmp_path, "reconciliation_context")).write_text("{}")
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
        assert Path(reviewer_lifecycle.reviewer_markdown_path(tmp_path, "code")).is_file()
        assert Path(reviewer_lifecycle.reviewer_markdown_path(tmp_path, "security")).is_file()
        assert state["reviewer_markdown"] == {
            "ran": True,
            "written": 2,
            "expected": 2,
            "status": "complete",
        }

    def test_step_8_reconciliation_failure_happens_after_reviewer_markdown(
        self, mod, tmp_path, monkeypatch
    ):
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({
            "agents": [{"name": "security-reviewer", "status": "DISPATCH"}],
        }))
        _write_final_review(tmp_path, "security", _review_json("security"))
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

        assert Path(reviewer_lifecycle.reviewer_markdown_path(tmp_path, "security")).is_file()

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
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps(plan))

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
        assert not (_artifact(tmp_path, "review_intake")).exists()


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
        _write_scope_summary(tmp_path, agent.removesuffix("-reviewer"), {
            "schema": 3,
            "inline_diff_files": list(inline),
            "review_claimable_files": list(claimable),
            "list_only_files": [],
            "routing_files": sorted({*inline, *claimable}),
        })

    @staticmethod
    def _finalized(tmp_path, reviewer, *, claims=(), claimable=()):
        _write_final_review(
            tmp_path, reviewer, canonical_review_document(
                reviewer,
                reviewed_file_claims=list(claims),
                review_claimable_files=list(claimable),
            )
        )

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
            "noise_filtered_files": None,
            "override_orphaned_files": None,
            "agents_receiving_inline_diff_by_file": {
                "src/a.py": ["security"]
            },
            "agents_claiming_review_by_file": {
                "src/big_module.py": ["security"]
            },
            "agents_with_unclaimed_review_by_file": {
                "src/starved.php": ["code"]
            },
        }

    def test_the_planner_s_exclusions_are_measured_from_the_dispatch_plan(
        self, mod, tmp_path
    ):
        self._summary(tmp_path, "security-reviewer", inline=["src/a.py"])
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({
            "agents": [{"name": "security-reviewer", "status": "DISPATCH"}],
            "changed_files": ["src/a.py", "Gemfile"],
        }))

        state = self._run_step(
            mod, tmp_path, "src/a.py,Gemfile,package-lock.json",
        )

        assert state["file_review"]["unscoped_files"] == [
            "Gemfile", "package-lock.json",
        ]
        assert state["file_review"]["noise_filtered_files"] == [
            "package-lock.json",
        ]

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
        """The whole point of measuring them: the assembler renders them —
        claims, skips, and the dispatch plan's own exclusion accounting."""
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
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps({
            "agents": [
                {"name": "security-reviewer", "status": "DISPATCH"},
                {"name": "code-reviewer", "status": "DISPATCH"},
            ],
            "changed_files": ["src/big.py", "src/starved.php", "Gemfile"],
        }))

        self._run_step(
            mod, tmp_path,
            "src/big.py,src/starved.php,Gemfile,package-lock.json",
        )

        record = (tmp_path / "review-record.md").read_text()
        assert "`src/starved.php` (skipped by: `code`)" in record
        assert "`src/big.py` (claimed by: `security`)" in record
        assert "1 changed file(s) matched no reviewer's domain" in record
        assert "- `Gemfile`" in record
        assert "1 changed file(s) were excluded from review by design" in record
        assert "- `package-lock.json`" in record


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

    def test_step_9_records_a_render_failure_instead_of_raising(
        self, mod, tmp_path, capsys
    ):
        (tmp_path / "review-findings.json").write_text(
            json.dumps(self._findings())
        )
        monkey = mod._orchestrate_step_9.__globals__
        original = monkey["render_markdown"]
        monkey["render_markdown"] = failing_findings_renderer(
            "renderer crashed"
        )
        state = {"resolved_params": {}}
        try:
            mod._orchestrate_step(9, "full", {}, state, {}, str(tmp_path))
        finally:
            monkey["render_markdown"] = original

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

    @pytest.mark.parametrize(
        "quick, verdict, expect_skip",
        [
            pytest.param(False, "block", False, id="not_quick_blocking"),
            pytest.param(True, "approve", True, id="quick_approve_skips"),
            pytest.param(True, "block", False, id="quick_block_keeps_critic"),
        ],
    )
    def test_step_10_quick_skip_decision(
        self, mod, tmp_path, quick, verdict, expect_skip
    ):
        self._findings(tmp_path, verdict)
        state = {"resolved_params": {}}

        mod._orchestrate_step(
            10, "full", {"quick": quick}, state, {}, str(tmp_path)
        )

        assert state.get("reconciliation_verdict") == verdict
        decision = state.get("step_decisions", {}).get("10")
        if expect_skip:
            assert decision is not None, "quick-mode critic skip was not recorded"
            assert decision["critic_skipped"] is True
            assert verdict in decision["reason"]
        else:
            assert decision is None

    def test_step_10_clears_a_stale_skip_decision_on_rerun(self, mod, tmp_path):
        """A rerun after the verdict escalates must drop the earlier skip.

        This is the exact line the split broke: the decision key is popped
        before it is conditionally rewritten, so a stale `critic_skipped`
        cannot survive into a run whose reconciliation now blocks.
        """
        self._findings(tmp_path, "approve")
        state = {"resolved_params": {}}
        mod._orchestrate_step(10, "full", {"quick": True}, state, {}, str(tmp_path))
        assert state["step_decisions"]["10"]["critic_skipped"] is True

        self._findings(tmp_path, "block")
        mod._orchestrate_step(10, "full", {"quick": True}, state, {}, str(tmp_path))
        assert "10" not in state.get("step_decisions", {}), (
            "stale critic-skip decision survived a verdict escalation"
        )

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(None, id="absent"),
            pytest.param("{not json", id="malformed"),
            pytest.param("[1, 2]", id="non_object_list"),
        ],
    )
    def test_step_10_unusable_ledger(self, mod, tmp_path, payload):
        """A ledger step 10 cannot read — absent, malformed JSON, or valid
        JSON that is not an object — leaves no verdict and no critic
        source rather than crashing or guessing.

        Valid JSON that is not an object used to escape the narrower
        `(JSONDecodeError, OSError)` guard and raise AttributeError on the
        `.get()` behind it — the same hole the shared verdict parser
        closed for review-verdict.json, one artifact over. The ledger now
        goes through critic_adjustments.read_findings_file(), so it is a
        shape fact, not a crash. A canonical-shaped-but-wrong ledger
        (`{"verdict": "block"}`) lands on the same non-OK branch as the
        malformed-JSON row here.
        """
        if payload is not None:
            (tmp_path / "review-findings.json").write_text(payload)
        state = {"resolved_params": {}}

        mod._orchestrate_step(10, "full", {}, state, {}, str(tmp_path))

        assert state.get("reconciliation_verdict", "") == ""
        assert state["ledger_status"] != "ok"
        assert state["critic_source"] is None


class TestStep11Orchestration:
    """Step 11 settles state, then publishes after the report handoff."""

    def test_step_11_prepares_then_publishes_after_report_handoff(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        """The result is the terminal commit marker, so the first pass
        remains resumable until the orchestrator authors the report."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "review-context.json").write_text(json.dumps({
            "git": {"merge_base": "abc", "git_range": "abc..HEAD"},
        }))
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "pr", "--pr-number", "42",
            "--interactive", "false", "--output-dir", str(tmp_path),
        ])
        mod.main()
        (tmp_path / "review-findings.json").write_text(
            '{"verdict": "approve", "findings": []}'
        )

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "11", "--mode", "pr",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        prepared_out = capsys.readouterr().out

        assert not (tmp_path / "pipeline-result.json").exists()
        state = json.loads((_artifact(tmp_path, "pipeline_state")).read_text())
        assert state["publication_pending"] is True
        prepared_fingerprint = state["prepared_report_source_fingerprint"]
        assert len(prepared_fingerprint) == 64
        assert state["report_handoff_status"] == "report_missing"
        assert 11 not in state["completed_steps"]
        assert not any(
            "review-report.md" in note
            for note in state["degradation_notes"]
        )
        assert "PIPELINE WAITING" in prepared_out
        assert "--step 11" in prepared_out
        assert "PIPELINE COMPLETE" not in prepared_out

        report = tmp_path / "review-report.md"
        report.write_text("# Review\nAll clear.")
        mod.main()
        published_out = capsys.readouterr().out

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["report_path"] == str(report)
        state = json.loads((_artifact(tmp_path, "pipeline_state")).read_text())
        assert state["publication_pending"] is False
        assert state["prepared_report_source_fingerprint"] == (
            prepared_fingerprint
        )
        assert state["report_handoff_status"] == "published"
        assert "stale_report_digest" not in state
        assert 11 in state["completed_steps"]
        assert "PIPELINE WAITING" not in published_out
        assert "HANDOFF" not in published_out
        assert "PIPELINE COMPLETE" in published_out

    def test_late_adjudication_invalidates_the_report_until_rewritten(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        """A report authored before the orchestrator adjudicated cannot
        publish once the adjudication changes the ledger and its verdict."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "pr", "--pr-number", "42",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
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

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "11", "--mode", "pr",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        state = json.loads((_artifact(tmp_path, "pipeline_state")).read_text())
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

        mod.main()
        changed_out = capsys.readouterr().out
        assert not (tmp_path / "pipeline-result.json").exists()
        state = json.loads((_artifact(tmp_path, "pipeline_state")).read_text())
        assert state["report_handoff_status"] == "source_changed"
        assert state["stale_report_digest"]
        assert state["publication_pending"] is True
        assert 11 not in state["completed_steps"]
        assert "regenerate" in changed_out.lower()
        settled = json.loads((tmp_path / "review-findings.json").read_text())
        assert settled["findings"][0]["severity"] == "low"
        assert settled["verdict"] == "approve"

        mod.main()
        assert not (tmp_path / "pipeline-result.json").exists()
        state = json.loads((_artifact(tmp_path, "pipeline_state")).read_text())
        assert state["report_handoff_status"] == "stale_report_unchanged"
        assert state["publication_pending"] is True
        assert 11 not in state["completed_steps"]

        report.write_text("# Review\nAPPROVE: settled low finding.")
        mod.main()
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["verdict"] == "APPROVE"
        assert result["status"] == "degraded"
        assert any(
            "never adjudicated" in note
            for note in result["degradation_notes"]
        ), "the prepare pass's honest record survives the handoff"
        state = json.loads((_artifact(tmp_path, "pipeline_state")).read_text())
        assert state["publication_pending"] is False
        assert "stale_report_digest" not in state
        assert 11 in state["completed_steps"]

    def test_interactive_publish_pass_routes_to_final_consent_without_workspace_state(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        """Step 12's condition is `interactive` alone; mode does not enter
        it. An `incremental` row is redundant with this `full` one — see
        `test_pipeline.py::TestStep12Cleanup` for the consent wording."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        # The consent conversation exists only for a run with a shareable
        # repository identity, which requires an origin remote.
        init_bare_repo(tmp_path, "https://github.com/acme/widget.git")

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        (tmp_path / "review-findings.json").write_text(
            '{"verdict": "approve", "findings": []}'
        )

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "11", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        prepared_out = capsys.readouterr().out
        assert "PIPELINE WAITING" in prepared_out

        (tmp_path / "review-report.md").write_text("# Review")
        mod.main()
        published_out = capsys.readouterr().out

        assert "Next: Step 12" in published_out
        assert "PIPELINE COMPLETE" not in published_out

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "12", "--mode", "full",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        consent_out = capsys.readouterr().out
        assert "set-sharing" in consent_out
        assert "No workspace changes to restore" in consent_out
        assert "PIPELINE COMPLETE" in consent_out

    def test_step_11_degrades_when_ledger_is_missing(
        self, mod, tmp_path, monkeypatch
    ):
        """No ledger at all: the verdict falls back to COMMENT, the run is
        reported degraded, and the run says why — rather than crashing or
        publishing a confident value."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "1", "--mode", "pr", "--pr-number", "42",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        # The report exists, but the findings do not (reconciliation
        # failed). Prepare without it present, then republish once it is
        # (the pattern `_publish_step_11` follows for the CLI smokes).
        report_path = tmp_path / "review-report.md"
        report_text = "# Review\nReport here."

        monkeypatch.setattr(sys, "argv", [
            "pipeline.py", "--step", "11", "--mode", "pr",
            "--output-dir", str(tmp_path),
        ])
        mod.main()
        report_path.write_text(report_text)
        mod.main()

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert result["verdict"] == "COMMENT"
        assert result["verdict_source"] == "fallback: no usable ledger verdict"
        assert any(
            "review-findings.json" in n for n in result["degradation_notes"]
        )


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
        (_artifact(od, "dispatch_plan")).write_text(json.dumps(plan))

        # Step 6: dispatch agents
        r = run_pipeline("--step", "6", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0

        # Step 7: save baseline
        r = run_pipeline("--step", "7", "--mode", "full", "--output-dir", od, cwd=repo)
        assert r.returncode == 0
        assert (_artifact(od, "worktree_baseline")).is_file()

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
        (_artifact(tmp_path, "dispatch_plan")).write_text(json.dumps(plan))
        _write_final_review(
            tmp_path,
            "repo-api-reviewer-v2",
            canonical_review_document("repo-api-reviewer-v2"),
        )
        fake_done = subprocess.CompletedProcess(
            [], returncode=0, stdout="", stderr=""
        )
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: fake_done)

        def fake_run_subprocess(cmd, timeout=None, **kwargs):
            (_artifact(tmp_path, "reconciliation_context")).write_text("{}")
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

    @pytest.mark.parametrize(
        "write_ledger, files, degradation, expected_source",
        [
            pytest.param(
                True,
                {"review-record.md": "# record", "review-findings.md": "# findings"},
                None,
                "review-record.md",
                id="record_wins",
            ),
            pytest.param(
                True,
                {"review-report.md": "# stale report"},
                None,
                "review-findings.json",
                id="report_ignored",
            ),
            pytest.param(
                True,
                {"review-findings.md": "# findings"},
                None,
                "review-findings.md",
                id="md_wins",
            ),
            pytest.param(
                True,
                {},
                {"findings_markdown_incomplete": True},
                "review-findings.json",
                id="json_only",
            ),
            pytest.param(False, {}, None, None, id="nothing_found"),
        ],
    )
    def test_critic_source_precedence(
        self, mod, tmp_path, write_ledger, files, degradation, expected_source
    ):
        """One `next()` over `_CRITIC_SOURCE_CANDIDATES`: record.md, then
        findings.md, then the ledger itself — never the not-yet-authored
        report.md, never a guess when nothing is present. The `json_only`
        row's `degradation` also pins that step 10 does not touch a
        `degradation` key it never reads — `critic_source` used to carry
        a `render_incomplete` copy of that flag, derived from the same
        state dict the briefing already reads."""
        if write_ledger:
            self._findings(tmp_path)
        for name, content in files.items():
            (tmp_path / name).write_text(content)
        state = {"resolved_params": {}}
        if degradation is not None:
            state["degradation"] = dict(degradation)

        mod._orchestrate_step(10, "full", {}, state, {}, str(tmp_path))

        assert state["critic_source"] == expected_source
        if degradation is not None:
            assert state["degradation"] == degradation


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


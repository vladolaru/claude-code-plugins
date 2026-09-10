"""Tests for supported review-run discovery, measurement, and cohorts."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import sys
import types
from pathlib import Path

import pytest


TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "analysis" / "review_run_metrics.py"
TELEMETRY_SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "review" / "telemetry.py"
DISPATCH_STATUS_SCRIPT_PATH = (
    PLUGIN_ROOT / "scripts" / "review" / "dispatch_status.py"
)
MANIFEST_SECTIONS_SCRIPT_PATH = (
    PLUGIN_ROOT / "scripts" / "review" / "manifest_sections.py"
)

sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "analysis"))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
sys.path.insert(0, str(TESTS_DIR))

import review_metrics as _mod  # noqa: E402
from review import run_paths, telemetry_share  # noqa: E402
from helpers.pipeline_process import init_bare_repo  # noqa: E402
from review_metrics import (  # noqa: E402
    cli,
    cohort,
    contracts,
    load,
    measure,
    render,
    sanitize,
)

load_runs = _mod.load_runs
measure_run = _mod.measure_run
aggregate_cohort = _mod.aggregate_cohort
format_table = _mod.format_table
format_json = _mod.format_json
main = _mod.main


class TestRepositoryReadEvidence:
    # `_nonnegative_exact_int`: an exact int passes; a non-int (a boolean,
    # None, a string, or a historical row without the key) fails
    # `type(value) is not int`; a negative fails the bound.
    @pytest.mark.parametrize("value, expected", [
        (4, 4), (True, None), (-1, None),
    ])
    def test_sanitized_usage_preserves_only_nonnegative_exact_read_counts(self, value, expected):
        [row] = measure._sanitize_agent_usage([{
            "agent": "review-reconciliator", "available": True,
            "usage": _usage(2),
            "tool_calls": 3, "repository_reads": value,
        }])
        assert row["repository_reads"] == expected
        assert row["tool_calls"] == 3

    def test_unavailable_row_cannot_claim_measured_reads(self):
        [row] = measure._sanitize_agent_usage([{
            "agent": "review-reconciliator", "available": False,
            "repository_reads": 4,
        }])
        assert row["repository_reads"] is None


def _load_telemetry_module():
    spec = importlib.util.spec_from_file_location(
        "review_telemetry_for_metrics", TELEMETRY_SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_dispatch_status_module():
    spec = importlib.util.spec_from_file_location(
        "review_dispatch_status_for_metrics", DISPATCH_STATUS_SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_manifest_sections_module():
    """Load manifest_sections.py independently of contracts.py's own
    exact-path load, so a vocabulary-parity test that compares the two
    is checking two separately-obtained values, not a tautology."""
    spec = importlib.util.spec_from_file_location(
        "review_manifest_sections_for_metrics", MANIFEST_SECTIONS_SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def shared_telemetry_clone(tmp_path):
    """Build local and shared telemetry trees through the real producers."""
    telemetry_mod = _load_telemetry_module()
    shared_clone = tmp_path / "shared-clone"
    version_dir = shared_clone / telemetry_share.LAYOUT_PREFIX
    local_logs = {}

    def create_upload(user: str, run_id: str, destination: Path) -> None:
        repo = init_bare_repo(
            tmp_path / f"{user}-repo", f"https://github.com/{user}/project.git"
        )
        output_dir = tmp_path / f"{user}-output"
        output_dir.mkdir()
        log_dir = tmp_path / f"{user}-logs"
        telemetry = telemetry_mod.ReviewTelemetry(
            str(output_dir), log_dir=str(log_dir)
        )
        telemetry.start(
            mode="pr",
            repo_path=str(repo),
            identifier=user,
            run_id=run_id,
        )
        telemetry.finalize(step=12, phase="OUTPUT", title="Complete review")

        log_path = Path(telemetry.log_path)
        manifest_path = Path(telemetry.manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        jsonl_lines = log_path.read_text(encoding="utf-8").splitlines(
            keepends=True
        )
        redacted_manifest, redacted_jsonl = telemetry_share.redact_payloads(
            manifest, jsonl_lines
        )
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"{run_id}.manifest.json").write_text(
            json.dumps(redacted_manifest), encoding="utf-8"
        )
        (destination / f"{run_id}.jsonl").write_text(
            "".join(redacted_jsonl), encoding="utf-8"
        )
        local_logs[user] = log_dir

    create_upload("alice", "shared-alice", version_dir / "alice")
    create_upload("bob", "shared-bob", version_dir / "bob")
    create_upload("ignored", "direct-v1", version_dir)
    # A contributor-committed symlink that git would recreate on checkout,
    # pointing at telemetry outside the clone: it must never be read.
    outside = tmp_path / "outside-the-clone"
    create_upload("mallory", "shared-mallory", outside)
    (version_dir / "mallory").symlink_to(outside)
    # Committed symlinks inside a real uploader directory: a relative one
    # that would re-attribute alice's run to bob, and one escaping the clone.
    for suffix in (".manifest.json", ".jsonl"):
        (version_dir / "bob" / f"shared-alice{suffix}").symlink_to(
            Path("..") / "alice" / f"shared-alice{suffix}"
        )
        (version_dir / "bob" / f"shared-mallory{suffix}").symlink_to(
            outside / f"shared-mallory{suffix}"
        )
    return {"clone": shared_clone, "local_logs": local_logs}


def test_metrics_uses_canonical_telemetry_contract():
    telemetry = _load_telemetry_module()
    dispatch_status = _load_dispatch_status_module()

    assert contracts.DEFAULT_LOG_DIR == Path(telemetry.LOG_DIR)
    assert contracts._DISPATCHED_STATUSES == dispatch_status.DISPATCHED_STATUSES
    assert (
        contracts._SUPPORTED_DISPATCH_STATUSES
        == dispatch_status.SUPPORTED_DISPATCH_STATUSES
    )
    assert contracts._SEVERITIES == tuple(telemetry._SEVERITY_FIELDS)
    assert (
        contracts._PRODUCER_AGENT_NAME_RE.pattern
        == dispatch_status.AGENT_NAME_RE.pattern
    )
    assert contracts._CRITIC_VERDICTS == frozenset(
        contracts._CRITIC_CONTRACT.CRITIC_VERDICTS
    )
    assert (
        contracts._AVAILABILITY_FAMILIES
        == contracts._PIPELINE_FAMILIES + contracts._TRANSCRIPT_FAMILIES
    )


def test_usage_fields_extend_transcript_producer_fields():
    """The consumer's usage vocabulary is the transcript producer's plus the
    derived effective_input_tokens — the relationship is encoded nowhere in
    code (the standalone transcript module cannot import this package), so
    this guard is what keeps the two field sets from drifting."""
    spec = importlib.util.spec_from_file_location(
        "review_transcript_for_usage_fields",
        PLUGIN_ROOT / "scripts" / "analysis" / "review_transcript.py",
    )
    transcript = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transcript)

    assert set(contracts._USAGE_FIELDS) == set(transcript._USAGE_FIELDS) | {
        "effective_input_tokens"
    }


def test_warning_allowlist_covers_transcript_emitted_codes():
    """Every warning code review_transcript.py can emit must survive
    sanitization — a dropped code erases the diagnostic while the affected
    metric families still degrade, leaving unexplained partial reports."""
    source = (
        PLUGIN_ROOT / "scripts" / "analysis" / "review_transcript.py"
    ).read_text()
    emitted = set(re.findall(r'\{"code": "([a-z_]+)"', source))

    assert emitted, "expected review_transcript.py to emit warning codes"
    missing = emitted - contracts._FIXED_WARNING_CODES
    assert not missing, (
        f"warning codes emitted but stripped by sanitization: {sorted(missing)}"
    )


def test_safe_string_rejects_ansi_terminal_escape_sequences():
    """Adjustment reasons can contain PR-influenced prose, so ANSI terminal
    escapes must fail closed rather than reach local reports."""
    assert sanitize._safe_string("reason \x1b[31mred\x1b[0m") is None


def test_safe_string_rejects_zero_width_format_characters():
    """Adjustment reasons can contain PR-influenced prose, so zero-width
    format characters must fail closed rather than obscure report text."""
    assert sanitize._safe_string("zero\u200bwidth") is None


def test_safe_string_retains_multiline_prose_whitespace():
    """Newlines and tabs are legitimate whitespace in multiline prose."""
    value = "line one\n\tline two"

    assert sanitize._safe_string(value) == value


def test_sanitize_steps_drops_thoughts_length_from_old_logs():
    """Old logs carry a field the writers no longer emit; it stays dropped.

    ``thoughts_length`` defaulted to 0 on every step event while no caller
    ever passed it, so it was a measurement that never happened published
    as a measured zero. It is gone from the producers, but runs already on
    disk still carry it, and this projection is where those logs are read.
    Feeding a pre-change event shape is what makes this a guard rather
    than a restatement of the writers' silence: restoring the copy block
    fails this test, while a test built from a freshly written event could
    not, since nothing emits the key for the projection to copy.
    """
    old_step = {
        "run_id": "run-1",
        "event": "step",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "phase": "VALIDATION",
        "title": "Decision Critic",
        "schema": 1,
        "step": 10,
        "duration_since_prev_ms": 12,
        "args": {"bot_mode": True, "thoughts_length": 321},
        "decisions": {"critic_skipped": True},
    }

    [projected] = sanitize._sanitize_steps([old_step])

    assert projected["args"] == {"bot_mode": True}
    # An old log stays readable: everything but the dropped key survives.
    assert projected["decisions"] == {"critic_skipped": True}
    assert projected["step"] == 10
    assert projected["title"] == "Decision Critic"
    assert "thoughts_length" not in json.dumps(projected)


def test_sanitize_steps_preserves_positive_exact_integer_step_attempt():
    [projected] = sanitize._sanitize_steps(
        [{"event": "step", "step": 11, "attempt": 2}]
    )

    assert projected["attempt"] == 2


@pytest.mark.parametrize(
    "attempt",
    [
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        # `_nonnegative_exact_int`'s `type(value) is not int`; an integral
        # float fails the same check.
        pytest.param(True, id="boolean"),
    ],
)
def test_sanitize_steps_drops_invalid_step_attempt(attempt):
    [projected] = sanitize._sanitize_steps(
        [{"event": "step", "step": 11, "attempt": attempt}]
    )

    assert "attempt" not in projected


def _manifest(
    run_id: str = "run-1",
    *,
    started_at: str | None = "2026-07-19T10:00:00+00:00",
    ended_at: str | None = "2026-07-19T10:01:00+00:00",
    session_id: str | None = None,
) -> dict:
    return {
        "schema": contracts._SUPPORTED_MANIFEST_SCHEMA,
        "status": "complete",
        "run": {
            "id": run_id,
            "session_id": session_id,
            "plugin_version": "1.108.0",
            "mode": "pr",
            "repo_path": "/safe/repo",
            "output_dir": "/safe/output",
            "started_at": started_at,
            "ended_at": ended_at,
            "git": {"base_sha": "base", "head_sha": "head"},
        },
        "steps": [],
        "agents": {"started": [], "completed": [], "incomplete": []},
        "dispatch": {
            "planner_baseline_available": True,
            "final_plan_available": True,
            "comparison_available": True,
            "planner_candidate_count": 2,
            "final_dispatch_count": 1,
            "adjustment_counts": {"added": 0, "removed": 1, "unchanged": 1},
            "invalid_reason_codes": [],
            "agents": {
                "code-reviewer": {
                    "initial_status": "DISPATCH",
                    "final_status": "DISPATCH",
                    "initial_signal": "keyword",
                    "final_signal": "keyword",
                    "planner_signals": [],
                    "configured_planner_checks": [],
                    "change": "unchanged",
                },
                "security-reviewer": {
                    "initial_status": "DISPATCH",
                    "final_status": "SKIPPED_TRIAGE",
                    "initial_signal": "default",
                    "final_signal": "override",
                    "planner_signals": [],
                    "configured_planner_checks": [],
                    "change": "removed",
                },
            },
        },
        "assignment": {
            "changed_files": ["src/a.py", "vendor/generated.js"],
            "reviewable_files": ["src/a.py"],
            "assigned_files_by_agent": {"code-reviewer": ["src/a.py"]},
            "assigned_files": ["src/a.py"],
            "file_exclusions": [
                {"path": "vendor/generated.js", "reason": "noise_filtered"}
            ],
            "unassigned_reviewable_files": [],
            "reviewed_files_by_agent": {},
            "review_claimable_file_count_by_agent": {},
            "semantics": "generated_scope_not_proof_of_model_read",
        },
        "outcome": {
            "summary": {
                "total_duration_ms": 60_000,
                "total_agent_findings": 3,
                "final_finding_count": 1,
            },
            "pipeline_status": "complete",
            "verdict": "COMMENT",
            "critic_verdict": "STAND",
            "reconciliation": None,
        },
        "availability": {"pipeline": True, "transcript": False, "assignment": True},
    }


def test_step_attempt_round_trips_through_supported_report(tmp_path):
    """A local log directory stands for a shared clone: the two differ
    only in the loader, and the shared loader is pinned by the
    `shared_telemetry_clone` tests."""
    run_id = "local-attempts"
    manifest = _manifest(run_id)
    manifest["steps"] = [
        {"run_id": run_id, "event": "step", "step": 11, "attempt": 1},
        {"run_id": run_id, "event": "step", "step": 11, "attempt": 2},
        {"run_id": run_id, "event": "step", "step": 5},
    ]
    source_dir = tmp_path / "local"
    source_dir.mkdir(parents=True)
    (source_dir / f"{run_id}.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    output = tmp_path / "local-report.json"

    result = main(
        [
            "--log-dir",
            str(source_dir),
            "--format",
            "json",
            "--output",
            str(output),
            "--no-transcripts",
        ]
    )

    assert result == 0
    [run] = json.loads(output.read_text(encoding="utf-8"))["runs"]
    assert [(step["step"], step.get("attempt")) for step in run["steps"]] == [
        (11, 1),
        (11, 2),
        (5, None),
    ]
    assert "attempt" not in run["steps"][2]


def _running_manifest(run_id: str = "run-1") -> dict:
    manifest = _manifest(run_id)
    manifest["status"] = "running"
    manifest["run"]["ended_at"] = None
    manifest["outcome"]["summary"] = {}
    return manifest


def _with_legacy_schema_key(event: dict) -> dict:
    """The same event as a pre-1.114.0 producer wrote it.

    `schema_version` was this family's key before the rename. No reader
    accepts it now, so an event spelled this way is unrecognizable input.
    """
    rekeyed = {k: v for k, v in event.items() if k != "schema"}
    rekeyed["schema_version"] = event["schema"]
    return rekeyed


def _pipeline_start(
    run_id: str = "run-1",
    *,
    timestamp: str = "2026-07-19T10:00:00+00:00",
) -> dict:
    return {
        "schema": contracts._SUPPORTED_MANIFEST_SCHEMA,
        "run_id": run_id,
        "event": "pipeline_start",
        "timestamp": timestamp,
        "pipeline": {"prompt": "PRIVATE ORCHESTRATOR PROMPT"},
    }


def _pipeline_end(
    run_id: str = "run-1",
    *,
    timestamp: str = "2026-07-19T10:00:30+00:00",
) -> dict:
    return {
        "schema": contracts._SUPPORTED_MANIFEST_SCHEMA,
        "run_id": run_id,
        "event": "pipeline_end",
        "timestamp": timestamp,
    }


def _step(
    run_id: str = "run-1",
    *,
    timestamp: str = "2026-07-19T10:00:10+00:00",
) -> dict:
    return {
        "schema": contracts._SUPPORTED_MANIFEST_SCHEMA,
        "run_id": run_id,
        "event": "step",
        "timestamp": timestamp,
        "step": 1,
    }


def _agent_start(
    agent: str = "code-reviewer",
    *,
    run_id: str = "run-1",
    timestamp: str = "2026-07-19T10:00:10+00:00",
) -> dict:
    return {
        "schema": contracts._SUPPORTED_MANIFEST_SCHEMA,
        "run_id": run_id,
        "event": "agent_start",
        "timestamp": timestamp,
        "agent": agent,
        "domain": "code",
        "model_tier": "sonnet",
        "budget_target": 20,
        "scope": {"files": 1, "lines": 5, "paths": ["src/a.py"]},
    }


def _agent_complete(
    agent: str = "code-reviewer",
    *,
    run_id: str = "run-1",
    timestamp: str = "2026-07-19T10:00:20+00:00",
) -> dict:
    return {
        "schema": contracts._SUPPORTED_MANIFEST_SCHEMA,
        "run_id": run_id,
        "event": "agent_complete",
        "timestamp": timestamp,
        "agent": agent,
        "duration_ms": 10_000,
        "verdict": "approve",
        "finding_count": 0,
        "severities": {},
        "review_digest": "a" * 64,
    }


def _task_5_manifest(run_id: str = "task-5-run") -> dict:
    """One schema-3 manifest using only the canonical Task 5 vocabulary."""
    manifest = _manifest(run_id)
    manifest["assignment"] = {
        "changed_files": ["src/a.py", "vendor/generated.js"],
        "reviewable_files": ["src/a.py"],
        "assigned_files_by_agent": {"code-reviewer": ["src/a.py"]},
        "assigned_files": ["src/a.py"],
        "file_exclusions": [
            {"path": "vendor/generated.js", "reason": "noise_filtered"},
        ],
        "unassigned_reviewable_files": [],
        "reviewed_files_by_agent": {},
        "review_claimable_file_count_by_agent": {},
        "semantics": "generated_scope_not_proof_of_model_read",
    }
    manifest["outcome"]["summary"] = {
        "total_duration_ms": 60_000,
        "total_agent_findings": 3,
        "final_finding_count": 1,
    }
    manifest["outcome"]["reconciliation"] = {
        "input_finding_count": 3,
        "contributing_agent_count": 2,
        "grouped_concern_count": 2,
        "false_positive_concern_count": 1,
        "out_of_scope_concern_count": 0,
        "verified_concern_count": 1,
        "reviewing_agents": ["code-reviewer", "security-reviewer"],
        "not_applicable_agents": [
            {"name": "a11y-reviewer", "skip_reason": "no UI changed"},
        ],
        "dispatched_agents": [
            "code-reviewer", "security-reviewer", "a11y-reviewer",
        ],
        "missing_agents": [],
    }
    return manifest


class TestReviewVocabularyLifecycleMigration:
    def test_reconciliation_counts_that_do_not_partition_are_dropped(
        self, tmp_path
    ):
        """The sanitizer mirrors the ledger's partition invariant."""
        manifest = _task_5_manifest()
        manifest["outcome"]["reconciliation"]["false_positive_concern_count"] = 2

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["outcome"]["reconciliation"] is None

    @pytest.mark.parametrize(
        "damage",
        [
            pytest.param(
                {"grouped_concern_count": 9, "verified_concern_count": 9},
                id="grouped-exceeds-input",
            ),
            pytest.param({"reviewing_agents": None}, id="reviewing-null"),
            # One duplicate check in the loop over the reconciliation agent
            # lists; a duplicated dispatched agent fails the same check.
            pytest.param(
                {"reviewing_agents": ["security-reviewer", "security-reviewer"]},
                id="reviewing-duplicate",
            ),
            pytest.param(
                {"not_applicable_agents": [
                    {"name": "a11y-reviewer", "skip_reason": "no ui"},
                    {"name": "a11y-reviewer", "skip_reason": "no ui"},
                ]},
                id="not-applicable-duplicate",
            ),
            pytest.param(
                {"not_applicable_agents": [
                    {"name": "a11y-reviewer", "skip_reason": "   "},
                ]},
                id="not-applicable-blank-reason",
            ),
        ],
    )
    def test_reconciliation_states_the_producer_refuses_are_dropped(
        self, tmp_path, damage
    ):
        """Every invariant findings_save / the ledger validator enforce on
        the way in is mirrored on the way out, so damaged telemetry is
        unmeasured rather than republished as a measurement."""
        manifest = _task_5_manifest()
        manifest["outcome"]["reconciliation"].update(damage)

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["outcome"]["reconciliation"] is None

    def test_schema_three_manifest_keeps_only_canonical_live_vocabulary(
        self, tmp_path
    ):
        measured = measure_run(
            _task_5_manifest(), tmp_path, include_transcripts=False
        )

        assert measured["assignment"] == _task_5_manifest()["assignment"]
        assert measured["outcome"]["summary"]["total_agent_findings"] == 3
        assert measured["outcome"]["summary"]["final_finding_count"] == 1
        assert measured["outcome"]["reconciliation"] == (
            _task_5_manifest()["outcome"]["reconciliation"]
        )
        rendered = json.loads(
            format_json([measured], aggregate_cohort([measured]))
        )
        assert rendered["schema"] == 5
        assert rendered["runs"][0]["outcome"]["reconciliation"] == (
            _task_5_manifest()["outcome"]["reconciliation"]
        )

    def test_historical_agent_identity_cohort_normalizes_legacy_rosters(
        self, tmp_path
    ):
        current = _task_5_manifest("current-roster")
        current_reconciliation = current["outcome"]["reconciliation"]
        current_reconciliation["missing_agents"] = ["docs-reviewer"]
        legacy = _task_5_manifest("legacy-roster")
        legacy_reconciliation = legacy["outcome"]["reconciliation"]
        legacy_reconciliation["reviewing_agents"] = [
            "code-review",
            "security-review",
        ]
        legacy_reconciliation["dispatched_agents"] = [
            "code-review",
            "security-review",
            "a11y-review",
        ]
        legacy_reconciliation["missing_agents"] = ["docs-review"]
        legacy_reconciliation["not_applicable_agents"][0]["name"] = (
            "a11y-review"
        )
        clone = tmp_path / "shared"
        uploader_dir = clone / telemetry_share.LAYOUT_PREFIX / "alice"
        uploader_dir.mkdir(parents=True)
        _write_manifest(uploader_dir / "current.manifest.json", current)
        _write_manifest(uploader_dir / "legacy.manifest.json", legacy)
        output = tmp_path / "cohort.json"

        result = main(
            [
                "--shared-dir",
                str(clone),
                "--format",
                "json",
                "--output",
                str(output),
                "--no-transcripts",
            ]
        )

        assert result == 0
        runs = {
            run["run"]["id"]: run
            for run in json.loads(output.read_text(encoding="utf-8"))["runs"]
        }
        for run_id in ("legacy-roster", "current-roster"):
            reconciliation = runs[run_id]["outcome"]["reconciliation"]
            assert reconciliation["reviewing_agents"] == [
                "code-reviewer",
                "security-reviewer",
            ]
            assert reconciliation["dispatched_agents"] == [
                "code-reviewer",
                "security-reviewer",
                "a11y-reviewer",
            ]
            assert reconciliation["missing_agents"] == ["docs-reviewer"]
            assert reconciliation["not_applicable_agents"] == [
                {"name": "a11y-reviewer", "skip_reason": "no UI changed"},
            ]
        assert {run["uploaded_by"] for run in runs.values()} == {"alice"}

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param(
                "reviewing_agents",
                ["code", "security-reviewer"],
                id="roster",
            ),
            pytest.param(
                "not_applicable_agents",
                [{"name": "a11y", "skip_reason": "no UI changed"}],
                id="not-applicable",
            ),
        ],
    )
    def test_historical_agent_identity_rejects_noncanonical_short_names(
        self, tmp_path, field, value
    ):
        manifest = _task_5_manifest()
        manifest["outcome"]["reconciliation"][field] = value

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["outcome"]["reconciliation"] is None

    def test_cohort_aggregates_canonical_coverage_and_finding_totals(self):
        first = measure_run(
            _task_5_manifest("task-5-a"),
            Path("/nonexistent"),
            include_transcripts=False,
        )
        second_manifest = _task_5_manifest("task-5-b")
        second_manifest["assignment"].update({
            "changed_files": ["src/b.py"],
            "reviewable_files": ["src/b.py"],
            "assigned_files_by_agent": {},
            "assigned_files": [],
            "file_exclusions": [],
            "unassigned_reviewable_files": ["src/b.py"],
        })
        second = measure_run(
            second_manifest,
            Path("/nonexistent"),
            include_transcripts=False,
        )

        cohort = aggregate_cohort([first, second])

        assert cohort["assignment"] == {
            "changed_files": 3,
            "reviewable_files": 2,
            "assigned_files": 1,
            "file_exclusions": 1,
            "unassigned_reviewable_files": 1,
            "assignment_rate": 0.5,
            "available_runs": 2,
            "semantics": "generated_scope_not_proof_of_model_read",
            "availability": cohort["availability"]["assignment"],
        }
        assert cohort["outcomes"]["raw_findings"] == 6
        assert cohort["outcomes"]["final_findings"] == 2

    def test_table_renders_canonical_assignment_and_finding_fields(self):
        measured = measure_run(
            _task_5_manifest(),
            Path("/nonexistent"),
            include_transcripts=False,
        )

        row = render._table_row(measured)

        assert row[4] == "1/1/0"
        assert row[5].startswith("3\u21921/")


def _agent_review_draft_saved(
    agent: str = "code-reviewer",
    *,
    run_id: str = "run-1",
    timestamp: str = "2026-07-19T10:00:15+00:00",
) -> dict:
    return {
        "schema": contracts._SUPPORTED_MANIFEST_SCHEMA,
        "run_id": run_id,
        "event": "agent_review_draft_saved",
        "timestamp": timestamp,
        "agent": agent,
        "review_digest": "a" * 64,
    }


def _planner_only_dispatch(count: int = 1) -> dict:
    return {
        "planner_baseline_available": True,
        "final_plan_available": False,
        "comparison_available": False,
        "planner_candidate_count": count,
        "final_dispatch_count": 0,
        "adjustment_counts": {"added": 0, "removed": 0, "unchanged": 0},
        "invalid_reason_codes": ["final_plan_unavailable"],
        "agents": {},
    }


def _final_only_dispatch() -> dict:
    return {
        "planner_baseline_available": False,
        "final_plan_available": True,
        "comparison_available": False,
        "planner_candidate_count": 1,
        "final_dispatch_count": 1,
        "adjustment_counts": {"added": 0, "removed": 0, "unchanged": 1},
        "invalid_reason_codes": ["planner_baseline_unavailable"],
        "agents": {
            "code-reviewer": {
                "initial_status": "DISPATCH",
                "final_status": "DISPATCH",
                "initial_signal": None,
                "final_signal": None,
                "planner_signals": [],
                "configured_planner_checks": [],
                "change": "unchanged",
            }
        },
    }


def _unavailable_dispatch() -> dict:
    return {
        "planner_baseline_available": False,
        "final_plan_available": False,
        "comparison_available": False,
        "planner_candidate_count": 0,
        "final_dispatch_count": 0,
        "adjustment_counts": {"added": 0, "removed": 0, "unchanged": 0},
        "invalid_reason_codes": [
            "planner_baseline_unavailable",
            "final_plan_unavailable",
        ],
        "agents": {},
    }


def _mismatched_dispatch() -> dict:
    return {
        "planner_baseline_available": True,
        "final_plan_available": True,
        "comparison_available": False,
        "planner_candidate_count": 1,
        "final_dispatch_count": 2,
        "adjustment_counts": {"added": 0, "removed": 0, "unchanged": 0},
        "invalid_reason_codes": ["dispatch_agent_set_mismatch"],
        "agents": {},
        "plan_projections": {
            "planner_baseline": {"code-reviewer": "DISPATCH"},
            "final_plan": {
                "code-reviewer": "DISPATCH",
                "security-reviewer": "DISPATCH",
            },
        },
    }


def _producer_duplicate_dispatch() -> dict:
    return {
        "planner_baseline_available": True,
        "final_plan_available": True,
        "comparison_available": False,
        "planner_candidate_count": 1,
        "final_dispatch_count": 1,
        "adjustment_counts": {"added": 0, "removed": 0, "unchanged": 0},
        "invalid_reason_codes": ["planner_baseline_duplicate_agents"],
        "duplicate_agent_names": {
            "planner_baseline": ["security-reviewer"]
        },
        "agents": {},
    }


def _write_manifest(path: Path, manifest: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest))
    return path


def _write_jsonl(path: Path, events: list[object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    return path


def _read_jsonl_for_test(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _usage(value: int) -> dict[str, int]:
    return {
        "input_tokens": value,
        "cache_creation_input_tokens": value * 2,
        "cache_read_input_tokens": value * 3,
        "effective_input_tokens": value * 6,
        "output_tokens": value * 4,
    }


def _legacy_events(run_id: str | None = "legacy-1") -> list[dict]:
    start = {
        "event": "pipeline_start",
        "timestamp": "2026-07-18T10:00:00+00:00",
        "pipeline": {
            "session_id": "session-1",
            "plugin_version": "1.107.0",
        "plugin_commit": "0123abcd",
            "mode": "full",
            "repo_path": "/private/repo",
            "output_dir": "/private/output",
            "git": {"base_sha": "base", "head_sha": "head"},
            "prompt": "PRIVATE PROMPT",
        },
        "snapshot": {"source": "PRIVATE SOURCE"},
    }
    if run_id is not None:
        start["run_id"] = run_id
    return [
        start,
        {
            "event": "agent_start",
            "timestamp": "2026-07-18T10:00:10+00:00",
            "agent": "code-reviewer",
            "domain": "code",
            "model_tier": "sonnet",
            "scope": {"files": 1, "lines": 5, "source": "PRIVATE SOURCE"},
        },
        {
            "event": "pipeline_end",
            "timestamp": "2026-07-18T10:01:00+00:00",
            "summary": {"total_duration_ms": 60_000, "total_agent_findings": 2},
            "snapshot": {"findings": "PRIVATE FINDING"},
            "tool_result": "PRIVATE TOOL BODY",
        },
    ]


def _flatten_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _flatten_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _flatten_strings(child)]
    return []


def _empty_artifacts(*, complete: bool = True) -> dict:
    return {
        "available": True,
        "complete": complete,
        "builder_attempted": False,
        "builder_attempts": 0,
        "builder_successes": 0,
        "builder_failures": 0,
        "recovered": False,
        "by_agent": [],
    }


def _builder_artifacts(*, complete: bool = True) -> dict:
    return {
        "available": True,
        "complete": complete,
        "builder_attempted": True,
        "builder_attempts": 2,
        "builder_successes": 1,
        "builder_failures": 1,
        "recovered": True,
        "by_agent": [
            {
                "agent": "code-reviewer",
                "builder_attempted": True,
                "builder_attempts": 2,
                "builder_successes": 1,
                "builder_failures": 1,
                "first_builder_attempt_succeeded": False,
                "recovered": True,
            }
        ],
    }


def _empty_reads(
    *,
    complete: bool = True,
    scope_complete: bool | None = None,
    non_scope_complete: bool | None = None,
) -> dict:
    scope_complete = complete if scope_complete is None else scope_complete
    non_scope_complete = (
        complete if non_scope_complete is None else non_scope_complete
    )
    return {
        "schema": 2,
        "all": [],
        "in_scope": [],
        "out_of_scope": [],
        "non_scope_comparable": [],
        "exhaustive": False,
        "scope_comparable_transcript_data_complete": scope_complete,
        "non_scope_comparable_transcript_data_complete": non_scope_complete,
        "transcript_data_complete": complete,
    }


def _complete_empty_transcript() -> dict:
    return {
        "available": True,
        "reason": None,
        "warnings": [],
        "correlation": {
            "expected_available": True,
            "expected": [],
            "expected_by_agent": {},
            "correlated": [],
            "correlated_by_agent": {},
            "missing": [],
            "missing_by_agent": {},
            "missing_transcripts": [],
            "expected_count": 0,
            "correlated_count": 0,
            "missing_count": 0,
            "complete": True,
        },
        "completeness": {
            "orchestrator_data": True,
            "agent_data": True,
            "usage": True,
            "tool_failures": True,
            "artifact_writes": True,
            "scope_comparable_reads": True,
            "non_scope_comparable_reads": True,
            "observed_reads": True,
        },
        "orchestrator_usage_by_step": {},
        "agent_usage": [],
        "usage": _usage(0),
        "tool_failures": [],
        "artifact_writes": _empty_artifacts(),
        "observed_reads": _empty_reads(),
    }


def _measure_fake_transcript(monkeypatch, tmp_path: Path, transcript: dict) -> dict:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"agents": {"code-reviewer": {}}}))

    def enrich(_manifest, _sessions_root, _recognized_agents):
        return copy.deepcopy(transcript)

    monkeypatch.setattr(measure, "_load_transcript_module", lambda: enrich)
    return measure_run(
        _manifest(session_id="session-1"),
        tmp_path,
        registry_path=registry,
    )


class TestRangeTruthSanitization:
    def test_run_git_carries_the_range_truth_blocks(self):
        """Dropping the new blocks would erase a measured range mismatch."""
        manifest = _manifest()
        manifest["run"]["git"].update({
            "base_fetch": {"status": "fetched", "sha": "a" * 40, "shallow": False},
            "scope_check": {
                "status": "mismatch", "github_changed_files": 8, "local_changed_files": 91,
                "head_matches": True, "base_matches": False,
                "extra_local_file_count": 83, "missing_local_file_count": 0,
            },
        })

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["run"]["git"]["base_fetch"] == {
            "status": "fetched", "sha": "a" * 40, "shallow": False,
        }
        assert sanitized["run"]["git"]["scope_check"]["extra_local_file_count"] == 83

    def test_range_truth_absent_or_malformed_reads_as_unmeasured(self):
        """Historical and malformed manifests must not inflate fetch failures."""
        manifest = _manifest()
        manifest["run"]["git"]["base_fetch"] = {"status": "sideways", "sha": "short"}

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["run"]["git"]["base_fetch"] == {
            "status": None, "sha": None, "shallow": None,
        }
        assert sanitized["run"]["git"]["scope_check"] is None
        cohort_view = aggregate_cohort([
            measure_run(sanitized, sessions_root="/nonexistent", include_transcripts=False),
        ])
        assert cohort_view["range_truth"] == {
            "base_fetch": {"unmeasured": 1}, "scope_check": {"unmeasured": 1},
        }

    def test_range_truth_sanitizer_rejects_non_string_status_without_aborting(self):
        """Unhashable nested status values must degrade to unmeasured facts.
        A list stands for an object: both fail `_enum`'s `isinstance(value,
        str)` before the vocabulary lookup could raise."""
        status = []
        manifest = _manifest()
        manifest["run"]["git"].update({
            "base_fetch": {"status": status, "sha": "a" * 40, "shallow": False},
            "scope_check": {"status": status, "github_changed_files": 1},
        })

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["run"]["git"]["base_fetch"]["status"] is None
        assert sanitized["run"]["git"]["scope_check"]["status"] is None


class TestLoadRuns:
    def test_missing_log_dir_returns_empty_without_creating_it(self, tmp_path):
        log_dir = tmp_path / "missing-logs"

        assert not log_dir.exists()
        assert load_runs(log_dir) == []
        assert not log_dir.exists()

    def test_unreadable_log_dir_propagates_listing_error(
        self, monkeypatch, tmp_path
    ):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        def deny_iterdir(_path):
            raise PermissionError("denied")

        monkeypatch.setattr(type(log_dir), "iterdir", deny_iterdir)

        with pytest.raises(OSError, match="denied"):
            load_runs(log_dir)

    @pytest.mark.parametrize(
        "verdict_source",
        # `_safe_scalar_map` has one path for a bounded string (every
        # producer value takes it) and one for null.
        ["findings ledger", None],
        ids=["ledger", "null"],
    )
    def test_verdict_source_is_measurable_across_a_cohort(
        self, tmp_path, verdict_source
    ):
        """Which branch produced a run's published verdict used to be a
        per-run fact only visible by opening one pipeline-result.json at a
        time. Whitelisting it into the manifest's `outcome` block
        (telemetry.py) and the cohort loader's sanitizer
        (review_metrics/sanitize.py) is what makes "how often does the
        verdict fall back" answerable across a run directory.
        """
        manifest = _manifest("verdict-source-run")
        manifest["outcome"]["verdict_source"] = verdict_source
        _write_manifest(tmp_path / "review.manifest.json", manifest)

        [run] = load_runs(tmp_path)

        assert run["outcome"]["verdict_source"] == verdict_source

    def test_running_sidecar_overlays_fresh_same_run_lifecycle_without_raw_payloads(
        self, tmp_path
    ):
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        start = _agent_start("code-reviewer", run_id="running-run")
        start["scope"]["paths"] = ["PRIVATE/SCOPE/PATH.py"]
        start["private_prompt"] = "PRIVATE AGENT PROMPT"
        complete = _agent_complete("code-reviewer", run_id="running-run")
        complete["verdict"] = "PRIVATE VERDICT PROSE"
        complete["tool_result"] = "PRIVATE TOOL RESULT"
        _write_jsonl(
            tmp_path / "review.jsonl",
            [_pipeline_start("running-run"), start, complete],
        )
        sidecar_before = (tmp_path / "review.manifest.json").read_bytes()

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["run"] == sanitize._sanitize_run(manifest["run"])
        assert run["dispatch"] == manifest["dispatch"]
        assert run["assignment"] == manifest["assignment"]
        assert run["outcome"] == sanitize._sanitize_outcome(manifest["outcome"])
        assert [event["agent"] for event in run["agents"]["started"]] == [
            "code-reviewer"
        ]
        assert [event["agent"] for event in run["agents"]["completed"]] == [
            "code-reviewer"
        ]
        assert run["agents"]["started"][0]["scope"]["paths"] == []
        assert run["agents"]["completed"][0]["verdict"] == "unavailable"
        assert run["agents"]["incomplete"] == []
        assert measured["metric_availability"]["lifecycle"] == "partial"
        assert measured["lifecycle"]["started_events"] == 1
        assert measured["lifecycle"]["completed_events"] == 1
        assert (tmp_path / "review.manifest.json").read_bytes() == sidecar_before
        serialized = json.dumps(run)
        for private_value in (
            "PRIVATE ORCHESTRATOR PROMPT",
            "PRIVATE/SCOPE/PATH.py",
            "PRIVATE AGENT PROMPT",
            "PRIVATE VERDICT PROSE",
            "PRIVATE TOOL RESULT",
        ):
            assert private_value not in serialized

    def test_running_overlay_validates_and_ignores_draft_save_events(
        self, tmp_path
    ):
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("running-run"),
                _agent_start("code-reviewer", run_id="running-run"),
                _agent_review_draft_saved(
                    "code-reviewer", run_id="running-run"
                ),
                _agent_complete("code-reviewer", run_id="running-run"),
            ],
        )

        [run] = load_runs(tmp_path)

        assert run["availability"]["lifecycle"] is True
        assert [event["event"] for event in run["agents"]["completed"]] == [
            "agent_complete"
        ]

    def test_malformed_draft_save_invalidates_only_running_lifecycle(
        self, tmp_path
    ):
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        malformed = _agent_review_draft_saved(
            "code-reviewer", run_id="running-run"
        )
        malformed["review_digest"] = "not-a-digest"
        _write_jsonl(
            tmp_path / "review.jsonl",
            [_pipeline_start("running-run"), malformed],
        )

        [run] = load_runs(tmp_path)

        assert run["availability"]["lifecycle"] is False
        assert "running_lifecycle_overlay_invalid" in run["warnings"]

    def test_running_overlay_preserves_finalized_review_digest(self, tmp_path):
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        completion = _agent_complete(
            "code-reviewer", run_id="running-run"
        )
        completion["review_digest"] = "b" * 64
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("running-run"),
                _agent_start("code-reviewer", run_id="running-run"),
                completion,
            ],
        )

        [run] = load_runs(tmp_path)

        assert run["availability"]["lifecycle"] is True
        assert run["agents"]["completed"][0]["review_digest"] == "b" * 64

    @pytest.mark.parametrize(
        "review_digest",
        ["A" * 64, "b" * 63, True],
        ids=["uppercase", "short", "boolean"],
    )
    def test_malformed_finalized_digest_invalidates_running_lifecycle(
        self, tmp_path, review_digest
    ):
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        completion = _agent_complete(
            "code-reviewer", run_id="running-run"
        )
        completion["review_digest"] = review_digest
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("running-run"),
                _agent_start("code-reviewer", run_id="running-run"),
                completion,
            ],
        )

        [run] = load_runs(tmp_path)

        assert run["availability"]["lifecycle"] is False
        assert "running_lifecycle_overlay_invalid" in run["warnings"]

    def test_running_overlay_preserves_validated_numeric_measurements(
        self, tmp_path
    ):
        """Fresh lifecycle suffix events keep their validated numerics —
        zeroing issue/severity counts and scope sizes would report measured
        zeros for work that occurred. String fields stay reduced."""
        telemetry_mod = _load_telemetry_module()
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        telemetry = telemetry_mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path)
        )
        telemetry.start(run_id="numeric-run")
        telemetry.log_step(step=6, phase="EXECUTION", title="Run Reviewers")
        telemetry.log_agent_start(
            agent_name="code-reviewer",
            domain="code",
            scope_files=3,
            scope_lines=120,
            budget_target=20,
            scope_paths=["src/a.py"],
        )
        telemetry.log_agent_complete(
            agent_name="code-reviewer",
            review_digest="a" * 64,
            verdict="comment",
            finding_count=2,
            severities={"high": 1, "medium": 1},
        )

        [run] = load_runs(tmp_path)

        [started] = run["agents"]["started"]
        [completed] = run["agents"]["completed"]
        assert started["scope"] == {"files": 3, "lines": 120, "paths": []}
        assert started["budget_target"] == 20
        assert started["domain"] == ""
        assert completed["finding_count"] == 2
        assert completed["severities"] == {"high": 1, "medium": 1}
        assert completed["verdict"] == "unavailable"

    def test_null_domain_producer_manifest_remains_lifecycle_available(
        self, tmp_path
    ):
        telemetry_mod = _load_telemetry_module()
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        telemetry = telemetry_mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path)
        )
        telemetry.start(run_id="domain-run")
        telemetry.log_agent_start(
            agent_name="tests-mutation-reviewer", domain=None
        )
        telemetry.log_step(step=6, phase="EXECUTION", title="Run Reviewers")

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["agents"]["started"][0]["domain"] == ""
        assert measured["metric_availability"]["lifecycle"] == "partial"
        assert measured["lifecycle"]["started_events"] == 1

    def test_malformed_nonnull_domain_remains_invalid_end_to_end(
        self, tmp_path
    ):
        """Any non-string domain fails `_bounded_event_string`'s
        `isinstance(value, str)`; an object stands for an integer too."""
        domain = {"unexpected": "object"}
        telemetry_mod = _load_telemetry_module()
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        telemetry = telemetry_mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path)
        )
        telemetry.start(run_id="malformed-domain-run")
        telemetry.log_agent_start(
            agent_name="tests-mutation-reviewer", domain=domain
        )
        telemetry.log_step(step=6, phase="EXECUTION", title="Run Reviewers")

        raw_start = next(
            event
            for event in _read_jsonl_for_test(Path(telemetry.log_path))
            if event["event"] == "agent_start"
        )
        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert raw_start["domain"] == domain
        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert measured["lifecycle"] is None

    def test_running_sidecar_crash_window_retains_unmatched_start(self, tmp_path):
        manifest = _running_manifest("crash-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("crash-run"),
                _agent_start("security-reviewer", run_id="crash-run"),
            ],
        )

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["agents"]["incomplete"] == ["security-reviewer"]
        assert measured["lifecycle"]["incomplete_count"] == 1
        assert measured["lifecycle"]["incomplete_by_agent"] == {
            "security-reviewer": 1
        }

    def test_running_sidecar_overlays_retry_multiset_in_append_order(self, tmp_path):
        manifest = _running_manifest("retry-run")
        existing_start = _agent_start(
            "code-reviewer",
            run_id="retry-run",
            timestamp="2026-07-19T10:00:05+00:00",
        )
        manifest["agents"] = {
            "started": [existing_start],
            "completed": [],
            "incomplete": ["code-reviewer"],
        }
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("retry-run"),
                existing_start,
                _agent_start(
                    "security-reviewer",
                    run_id="retry-run",
                    timestamp="2026-07-19T10:00:06+00:00",
                ),
                _agent_start(
                    "code-reviewer",
                    run_id="retry-run",
                    timestamp="2026-07-19T10:00:07+00:00",
                ),
                _agent_complete(
                    "security-reviewer",
                    run_id="retry-run",
                    timestamp="2026-07-19T10:00:08+00:00",
                ),
                _agent_complete(
                    "code-reviewer",
                    run_id="retry-run",
                    timestamp="2026-07-19T10:00:09+00:00",
                ),
            ],
        )

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert [event["agent"] for event in run["agents"]["started"]] == [
            "code-reviewer",
            "security-reviewer",
            "code-reviewer",
        ]
        assert [event["agent"] for event in run["agents"]["completed"]] == [
            "security-reviewer",
            "code-reviewer",
        ]
        assert run["agents"]["started"][0]["scope"]["paths"] == ["src/a.py"]
        assert run["agents"]["started"][1]["scope"]["paths"] == []
        assert run["agents"]["started"][2]["scope"]["paths"] == []
        assert run["agents"]["incomplete"] == ["code-reviewer"]
        assert measured["lifecycle"]["starts_by_agent"] == {
            "code-reviewer": 2,
            "security-reviewer": 1,
        }
        assert measured["lifecycle"]["incomplete_by_agent"] == {
            "code-reviewer": 1
        }

    def test_running_sidecar_accepts_equal_timestamp_start_then_completion(
        self, tmp_path
    ):
        manifest = _running_manifest("equal-time-run")
        timestamp = "2026-07-19T10:00:05+00:00"
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("equal-time-run"),
                _agent_start(
                    "code-reviewer",
                    run_id="equal-time-run",
                    timestamp=timestamp,
                ),
                _agent_complete(
                    "code-reviewer",
                    run_id="equal-time-run",
                    timestamp=timestamp,
                ),
            ],
        )

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["agents"]["incomplete"] == []
        assert measured["metric_availability"]["lifecycle"] == "partial"
        assert measured["lifecycle"]["started_events"] == 1
        assert measured["lifecycle"]["completed_events"] == 1

    def test_running_overlay_rejects_regressing_control_plane_timeline(
        self, tmp_path
    ):
        """One `timestamp < last_control_plane_time` check: a later step
        regressing stands for a step before the start and an end before
        the last step."""
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("running-run"),
                _step(
                    "running-run",
                    timestamp="2026-07-19T10:00:10+00:00",
                ),
                {
                    **_step(
                        "running-run",
                        timestamp="2026-07-19T10:00:09+00:00",
                    ),
                    "step": 2,
                },
            ],
        )

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert "running_lifecycle_overlay_invalid" in run["warnings"]
        assert run["dispatch"] == manifest["dispatch"]
        assert run["assignment"] == manifest["assignment"]

    def test_running_overlay_accepts_equal_control_plane_timestamps(
        self, tmp_path
    ):
        manifest = _running_manifest("running-run")
        timestamp = "2026-07-19T10:00:00+00:00"
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("running-run", timestamp=timestamp),
                _step("running-run", timestamp=timestamp),
                _pipeline_end("running-run", timestamp=timestamp),
            ],
        )

        [run] = load_runs(tmp_path)

        assert "running_lifecycle_overlay_invalid" not in run["warnings"]

    def test_running_overlay_allows_parallel_agent_timestamps_to_interleave(
        self, tmp_path
    ):
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("running-run"),
                _agent_start(
                    "code-reviewer",
                    run_id="running-run",
                    timestamp="2026-07-19T10:00:20+00:00",
                ),
                _agent_start(
                    "security-reviewer",
                    run_id="running-run",
                    timestamp="2026-07-19T10:00:10+00:00",
                ),
                _agent_complete(
                    "code-reviewer",
                    run_id="running-run",
                    timestamp="2026-07-19T10:00:40+00:00",
                ),
                _agent_complete(
                    "security-reviewer",
                    run_id="running-run",
                    timestamp="2026-07-19T10:00:30+00:00",
                ),
            ],
        )

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert "running_lifecycle_overlay_invalid" not in run["warnings"]
        assert measured["metric_availability"]["lifecycle"] == "partial"
        assert measured["lifecycle"]["started_events"] == 2
        assert measured["lifecycle"]["completed_events"] == 2

    def test_running_sidecar_accepts_one_terminal_end_during_finalize_crash_window(
        self, tmp_path
    ):
        manifest = _running_manifest("finalize-crash-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("finalize-crash-run"),
                _agent_start("code-reviewer", run_id="finalize-crash-run"),
                _agent_complete("code-reviewer", run_id="finalize-crash-run"),
                _pipeline_end("finalize-crash-run"),
            ],
        )

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert [event["agent"] for event in run["agents"]["started"]] == [
            "code-reviewer"
        ]
        assert [event["agent"] for event in run["agents"]["completed"]] == [
            "code-reviewer"
        ]
        assert measured["metric_availability"]["lifecycle"] == "partial"
        assert "running_lifecycle_overlay_invalid" not in run["warnings"]

    def test_running_overlay_rejects_nonterminal_or_duplicate_end(
        self, tmp_path
    ):
        """One `pipeline_end`-must-be-last conjunct: a duplicate end stands
        for lifecycle or step events appended after the end."""
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("running-run"),
                _agent_start("code-reviewer", run_id="running-run"),
                _pipeline_end("running-run"),
                _pipeline_end(
                    "running-run",
                    timestamp="2026-07-19T10:00:40+00:00",
                ),
            ],
        )

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert "running_lifecycle_overlay_invalid" in run["warnings"]
        assert run["dispatch"] == manifest["dispatch"]
        assert run["assignment"] == manifest["assignment"]

    @pytest.mark.parametrize(
        "events",
        [
            pytest.param(
                [
                    _pipeline_start("foreign-run"),
                    _agent_start("code-reviewer", run_id="foreign-run"),
                ],
                id="foreign-run",
            ),
            pytest.param(
                [
                    _pipeline_start("running-run"),
                    _agent_complete(
                        "code-reviewer",
                        run_id="running-run",
                        timestamp="2026-07-19T10:00:06+00:00",
                    ),
                    _agent_start(
                        "code-reviewer",
                        run_id="running-run",
                        timestamp="2026-07-19T10:00:05+00:00",
                    ),
                ],
                id="completion-appended-before-later-start",
            ),
            pytest.param(
                [
                    _with_legacy_schema_key(_pipeline_start("running-run")),
                    _agent_start("code-reviewer", run_id="running-run"),
                ],
                id="pre-rename-schema-key-on-first-event",
            ),
            pytest.param(
                [
                    _pipeline_start("running-run"),
                    {
                        **_agent_start("code-reviewer", run_id="running-run"),
                        "schema": contracts._SUPPORTED_MANIFEST_SCHEMA + 1,
                    },
                ],
                id="unsupported-schema-mid-stream",
            ),
            # Control-plane events never reach _strict_lifecycle_event —
            # only agent_start/agent_complete do — so the per-event loop in
            # _overlay_running_lifecycle is the ONLY schema guard a `step`
            # or `pipeline_end` event ever passes. This case keeps that
            # conjunct pinned (fix 43c845c9): without it the conjunct can be
            # deleted with every other test still green, and an overlay would
            # silently accept control-plane events whose field meanings its
            # producer never vouched for. A pre-rename schema key, or the same
            # schema on `pipeline_end`, reaches the same conjunct.
            pytest.param(
                [
                    _pipeline_start("running-run"),
                    {
                        "schema": contracts._SUPPORTED_MANIFEST_SCHEMA + 1,
                        "run_id": "running-run",
                        "event": "step",
                        "timestamp": "2026-07-19T10:00:05+00:00",
                        "step": 6,
                        "phase": "REVIEW",
                        "title": "Dispatch Agents",
                    },
                ],
                id="unsupported-schema-on-step-event",
            ),
        ],
    )
    def test_invalid_running_lifecycle_overlay_fails_closed_family_locally(
        self, tmp_path, events
    ):
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", events)

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["run"] == sanitize._sanitize_run(manifest["run"])
        assert run["dispatch"] == manifest["dispatch"]
        assert run["assignment"] == manifest["assignment"]
        assert run["outcome"] == sanitize._sanitize_outcome(manifest["outcome"])
        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert "running_lifecycle_overlay_invalid" in run["warnings"]

    def test_partial_trailing_running_log_fails_closed_family_locally(
        self, tmp_path
    ):
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        (tmp_path / "review.jsonl").write_text(
            json.dumps(_pipeline_start("running-run")) + "\n{NOT JSON\n"
        )

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert "running_lifecycle_overlay_invalid" in run["warnings"]
        assert run["dispatch"] == manifest["dispatch"]
        assert run["assignment"] == manifest["assignment"]

    def test_invalid_utf8_running_log_fails_closed_family_locally(
        self, tmp_path
    ):
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        (tmp_path / "review.jsonl").write_bytes(b"\xff")

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["run"] == sanitize._sanitize_run(manifest["run"])
        assert run["dispatch"] == manifest["dispatch"]
        assert run["assignment"] == manifest["assignment"]
        assert run["outcome"] == sanitize._sanitize_outcome(manifest["outcome"])
        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert "running_lifecycle_overlay_invalid" in run["warnings"]

    def test_running_overlay_requires_one_global_append_prefix(self, tmp_path):
        manifest = _running_manifest("running-run")
        first_start = _agent_start(
            "code-reviewer",
            run_id="running-run",
            timestamp="2026-07-19T10:00:05+00:00",
        )
        second_start = _agent_start(
            "code-reviewer",
            run_id="running-run",
            timestamp="2026-07-19T10:00:07+00:00",
        )
        manifest["agents"] = {
            "started": [first_start, second_start],
            "completed": [],
            "incomplete": ["code-reviewer", "code-reviewer"],
        }
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("running-run"),
                first_start,
                _agent_complete(
                    "code-reviewer",
                    run_id="running-run",
                    timestamp="2026-07-19T10:00:06+00:00",
                ),
                second_start,
            ],
        )

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert "running_lifecycle_overlay_invalid" in run["warnings"]

    def test_complete_manifest_suppresses_fresh_same_run_lifecycle_overlay(
        self, tmp_path
    ):
        """A valid complete manifest is the run: its sibling JSONL, even a
        fresh same-run lifecycle suffix, is neither overlaid onto the
        manifest's lifecycle nor loaded as a legacy run of its own."""
        manifest = _manifest("complete-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("complete-run"),
                _agent_start("code-reviewer", run_id="complete-run"),
                _agent_complete("code-reviewer", run_id="complete-run"),
            ],
        )

        runs = load_runs(tmp_path)

        assert [run["run"]["id"] for run in runs] == ["complete-run"]
        [run] = runs
        assert run["status"] == "complete"
        assert run["agents"] == manifest["agents"]
        assert run["warnings"] == []

    def test_sorts_absolute_times_newest_first_and_applies_last_after_sort(self, tmp_path):
        _write_manifest(
            tmp_path / "same-instant.manifest.json",
            _manifest("same", started_at="2026-07-19T12:00:00+02:00"),
        )
        _write_manifest(
            tmp_path / "newest.manifest.json",
            _manifest("new", started_at="2026-07-19T10:01:00+00:00"),
        )
        _write_manifest(
            tmp_path / "unknown.manifest.json",
            _manifest("unknown", started_at="not-a-time"),
        )

        assert [run["run"]["id"] for run in load_runs(tmp_path, last=2)] == [
            "new",
            "same",
        ]

    def test_sorts_naive_and_invalid_timestamps_unknown_last_deterministically(
        self, tmp_path
    ):
        _write_manifest(
            tmp_path / "naive.manifest.json",
            _manifest("unknown-b", started_at="2099-07-19T10:00:00"),
        )
        _write_manifest(
            tmp_path / "aware.manifest.json",
            _manifest("known", started_at="2026-07-19T12:00:00+02:00"),
        )
        _write_manifest(
            tmp_path / "invalid.manifest.json",
            _manifest("unknown-a", started_at="not-a-time"),
        )

        assert [run["run"]["id"] for run in load_runs(tmp_path)] == [
            "known",
            "unknown-a",
            "unknown-b",
        ]

    def test_exact_run_id_filter(self, tmp_path):
        _write_manifest(tmp_path / "one.manifest.json", _manifest("run-1"))
        _write_manifest(tmp_path / "ten.manifest.json", _manifest("run-10"))

        assert [run["run"]["id"] for run in load_runs(tmp_path, run_id="run-1")] == [
            "run-1"
        ]

    def test_reduces_legacy_log_without_retaining_private_payloads(self, tmp_path):
        _write_jsonl(tmp_path / "legacy.jsonl", _legacy_events())

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-1"
        assert run["run"]["plugin_commit"] == "0123abcd"
        assert run["dispatch"] is None
        assert run["assignment"] is None
        assert run["warnings"] == ["legacy_log_no_manifest"]
        serialized = json.dumps(run)
        assert "PRIVATE PROMPT" not in serialized
        assert "PRIVATE SOURCE" not in serialized
        assert "PRIVATE FINDING" not in serialized
        assert "PRIVATE TOOL BODY" not in serialized
        assert "snapshot" not in serialized

    def test_concatenated_legacy_runs_reduce_to_the_first_segment(
        self, tmp_path
    ):
        """One legacy file holds one run by construction — combining a
        concatenated file's segments would assign one run ID the outcomes
        and lifecycle of OTHER runs, corrupt even under exact --run-id
        filtering. Only the first segment's events reduce."""
        first = _legacy_events()
        second = _legacy_events(run_id="legacy-2")
        second[1]["agent"] = "security-reviewer"
        second[2]["summary"] = {"total_duration_ms": 5, "total_agent_findings": 9}
        _write_jsonl(tmp_path / "legacy.jsonl", first + second)

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-1"
        assert [
            event["agent"] for event in run["agents"]["started"]
        ] == ["code-reviewer"]
        assert run["run"]["ended_at"] == "2026-07-18T10:01:00+00:00"
        assert run["outcome"]["summary"].get("total_agent_findings") == 2

    def test_damaged_second_start_cannot_hand_the_first_run_the_tails_outcome(
        self, tmp_path
    ):
        """The tolerant reader drops a malformed second pipeline_start, so
        the segment must also stop at the run's own pipeline_end — else the
        tail's summary, outcomes, and wall time attribute to the first run
        even under exact --run-id filtering."""
        first = _legacy_events()
        second = _legacy_events(run_id="legacy-2")
        second[1]["agent"] = "security-reviewer"
        second[2]["summary"] = {"total_duration_ms": 5, "total_agent_findings": 9}
        second[2]["timestamp"] = "2026-07-18T11:00:00+00:00"
        lines = [json.dumps(event).encode("utf-8") for event in first]
        lines.append(b'{"event": "pipeline_start", "x": "\xff"}')
        lines.extend(json.dumps(event).encode("utf-8") for event in second[1:])
        (tmp_path / "legacy.jsonl").write_bytes(b"\n".join(lines) + b"\n")

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-1"
        assert [
            event["agent"] for event in run["agents"]["started"]
        ] == ["code-reviewer"]
        assert run["run"]["ended_at"] == "2026-07-18T10:01:00+00:00"
        assert run["outcome"]["summary"].get("total_agent_findings") == 2

    def test_foreign_terminal_event_cannot_complete_a_run_missing_its_end(
        self, tmp_path
    ):
        """When the first run never wrote a terminal event AND the second
        run's pipeline_start line is damaged (dropped by the tolerant
        reader), the tail's own run_id stamps — which every producer event
        carries — are what remains to reject its pipeline_end. Accepting it
        would mark the first run complete with the later run's summary and
        lifecycle."""
        first = _legacy_events()
        del first[2]
        first[1]["run_id"] = "legacy-1"
        second = _legacy_events(run_id="legacy-2")
        second[1]["run_id"] = "legacy-2"
        second[1]["agent"] = "security-reviewer"
        second[2]["run_id"] = "legacy-2"
        second[2]["summary"] = {"total_duration_ms": 5, "total_agent_findings": 9}
        second[2]["timestamp"] = "2026-07-18T11:00:00+00:00"
        lines = [json.dumps(event).encode("utf-8") for event in first]
        lines.append(b'{"event": "pipeline_start", "x": "\xff"}')
        lines.extend(json.dumps(event).encode("utf-8") for event in second[1:])
        (tmp_path / "legacy.jsonl").write_bytes(b"\n".join(lines) + b"\n")

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-1"
        assert run["status"] == "running"
        assert run["run"]["ended_at"] is None
        assert [
            event["agent"] for event in run["agents"]["started"]
        ] == ["code-reviewer"]
        assert run["outcome"]["summary"].get("total_agent_findings") is None

    def test_stamped_tail_cannot_complete_an_unstamped_legacy_run(
        self, tmp_path
    ):
        """A first run predating run IDs has no stamp to compare against —
        but no producer version mixes stamped and unstamped events within
        one run, so ANY stamped event after an unstamped start is foreign
        by construction and terminates the segment."""
        first = _legacy_events(run_id=None)
        del first[2]
        second = _legacy_events(run_id="legacy-2")
        second[1]["run_id"] = "legacy-2"
        second[1]["agent"] = "security-reviewer"
        second[2]["run_id"] = "legacy-2"
        second[2]["summary"] = {"total_duration_ms": 5, "total_agent_findings": 9}
        second[2]["timestamp"] = "2026-07-18T11:00:00+00:00"
        lines = [json.dumps(event).encode("utf-8") for event in first]
        lines.append(b'{"event": "pipeline_start", "x": "\xff"}')
        lines.extend(json.dumps(event).encode("utf-8") for event in second[1:])
        (tmp_path / "legacy.jsonl").write_bytes(b"\n".join(lines) + b"\n")

        [run] = load_runs(tmp_path)

        assert run["run"]["id"].startswith("legacy-")
        assert run["status"] == "running"
        assert run["run"]["ended_at"] is None
        assert [
            event["agent"] for event in run["agents"]["started"]
        ] == ["code-reviewer"]
        assert run["outcome"]["summary"].get("total_agent_findings") is None

    def test_legacy_steps_carry_only_step_events(self, tmp_path):
        """The manifest contract's steps are step events only — a
        pipeline_end entry fails the transcript stage-timeline validator,
        flagging EVERY completed legacy run and collapsing its usage
        attribution to unattributed."""
        events = _legacy_events()
        events.insert(1, {
            "event": "step",
            "step": 1,
            "phase": "SETUP",
            "title": "Init",
            "timestamp": "2026-07-18T10:00:05+00:00",
        })
        _write_jsonl(tmp_path / "legacy.jsonl", events)

        [run] = load_runs(tmp_path)

        assert [entry.get("event") for entry in run["steps"]] == ["step"]

    def test_synthesizes_stable_opaque_legacy_id_without_path_leak(self, tmp_path):
        first = tmp_path / "personal-name-one.jsonl"
        second = tmp_path / "personal-name-two.jsonl"
        events = _legacy_events(run_id=None)
        _write_jsonl(first, events)

        [loaded_first] = load_runs(tmp_path)
        first.rename(second)
        [loaded_second] = load_runs(tmp_path)

        assert loaded_first["run"]["id"] == loaded_second["run"]["id"]
        assert loaded_first["run"]["id"].startswith("legacy-")
        assert "personal-name" not in loaded_first["run"]["id"]

    def test_invalid_sidecar_falls_back_to_legacy_with_fixed_warning(self, tmp_path):
        (tmp_path / "review.manifest.json").write_text("NOT JSON")
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-fallback"
        assert run["warnings"] == [
            "legacy_log_no_manifest",
            "invalid_manifest_fallback",
        ]

    @pytest.mark.parametrize(
        "field,value",
        [
            # No `schema` key. A pre-rename manifest (`schema_version`, the
            # key before 1.114.0) has this shape too;
            # `test_pre_rename_manifest_without_a_log_yields_no_run` keeps
            # that exact shape.
            ("schema", None),
            # The literal `1` every run wrote before the verdict-provenance
            # bump. Any schema other than the supported integer (a boolean,
            # a float, a future version) fails the same check.
            ("schema", 1),
            ("status", "success"),
        ],
        ids=[
            "missing-version",
            "pre-bump-version",
            "unsupported-status",
        ],
    )
    def test_unsupported_sidecar_envelope_cannot_suppress_legacy_fallback(
        self, tmp_path, field, value
    ):
        manifest = _manifest("sidecar-run")
        if value is None:
            manifest.pop(field)
        else:
            manifest[field] = value
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-fallback"
        assert run["warnings"] == [
            "legacy_log_no_manifest",
            "invalid_manifest_fallback",
        ]

    def test_pre_rename_manifest_without_a_log_yields_no_run(self, tmp_path):
        """Nothing recognizable is left to measure, and that is reported.

        The pre-rename field runs on a maintainer's machine fall here. The
        contract is that they drop out of the cohort rather than entering
        it with fields read under the wrong contract.
        """
        manifest = _manifest("sidecar-run")
        manifest["schema_version"] = manifest.pop("schema")
        _write_manifest(tmp_path / "review.manifest.json", manifest)

        assert load_runs(tmp_path) == []

    def test_pre_rename_jsonl_events_still_reduce_to_a_labeled_legacy_run(
        self, tmp_path
    ):
        """The tolerant JSONL path never claimed to validate the envelope.

        It reconstructs what it can and labels the result legacy; an event
        stream keyed the old way loses only the schema number it carried.
        """
        events = _legacy_events("legacy-old-key")
        for event in events:
            event.pop("schema", None)
            # Deliberately not 1: the reconstruction's own default IS 1, so
            # only a distinguishable number can show whether the old key was
            # read or ignored.
            event["schema_version"] = 7
        _write_jsonl(tmp_path / "review.jsonl", events)

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-old-key"
        assert run["warnings"] == ["legacy_log_no_manifest"]
        # The old key is not read as the new one: the reconstruction falls
        # back to its default rather than adopting the unvouched number.
        assert run["schema"] == 1
        assert "schema_version" not in run

    @pytest.mark.parametrize("status", ["running", "complete"])
    def test_supported_sidecar_status_suppresses_sibling_legacy_log(
        self, tmp_path, status
    ):
        manifest = _manifest("sidecar-run")
        manifest["status"] = status
        if status == "running":
            manifest["run"]["ended_at"] = None
            manifest["dispatch"] = _unavailable_dispatch()
            manifest["assignment"] = None
            manifest["availability"]["assignment"] = False
            manifest["outcome"]["summary"] = {}
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-run"))

        [run] = load_runs(tmp_path)

        assert run["status"] == status
        assert run["run"]["id"] == "sidecar-run"
        assert "legacy_log_no_manifest" not in run["warnings"]

    def test_malformed_lifecycle_is_family_local_for_native_sidecar(self, tmp_path):
        manifest = _manifest("sidecar-run")
        manifest.pop("agents")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-run"))

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["run"]["id"] == "sidecar-run"
        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert measured["metric_availability"]["assignment"] == "complete"

    @pytest.mark.parametrize(
        "malform",
        [
            lambda manifest: manifest.__setitem__("steps", {"private": "payload"}),
            lambda manifest: manifest.pop("dispatch"),
            lambda manifest: manifest["run"].pop("started_at"),
            lambda manifest: manifest["availability"].__setitem__(
                "pipeline", False
            ),
            lambda manifest: manifest["availability"].__setitem__(
                "assignment", False
            ),
            lambda manifest: manifest["dispatch"].__setitem__(
                "planner_candidate_count", float("inf")
            ),
            lambda manifest: manifest["assignment"].__setitem__(
                "assigned_files", ["outside.py"]
            ),
        ],
        ids=[
            "top-level-shape",
            "missing-dispatch-slot",
            "missing-run-field",
            "pipeline-unavailable",
            "coverage-contradiction",
            "dispatch-projection",
            "coverage-projection",
        ],
    )
    def test_sanitizer_critical_malformed_sidecar_cannot_suppress_richer_legacy(
        self, tmp_path, malform
    ):
        manifest = _manifest("sidecar-run")
        malform(manifest)
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-fallback"
        assert run["warnings"] == [
            "legacy_log_no_manifest",
            "invalid_manifest_fallback",
        ]

    def test_producer_duplicate_dispatch_keeps_valid_sidecar_pipeline_metrics(
        self, tmp_path
    ):
        manifest = _manifest("sidecar-run")
        manifest["dispatch"] = _producer_duplicate_dispatch()
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["run"]["id"] == "sidecar-run"
        assert run["warnings"] == []
        assert run["assignment"] == manifest["assignment"]
        assert run["outcome"] == manifest["outcome"]
        assert run["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    @pytest.mark.parametrize(
        "initial_state,final_state",
        [
            ("duplicate", "valid"),
            ("valid", "duplicate"),
            ("duplicate", "duplicate"),
            ("duplicate", "missing"),
            ("missing", "duplicate"),
        ],
        ids=[
            "planner-duplicate-final-valid",
            "planner-valid-final-duplicate",
            "both-duplicate",
            "planner-duplicate-final-missing",
            "planner-missing-final-duplicate",
        ],
    )
    def test_actual_telemetry_duplicate_dispatch_sidecar_survives_consumer_load(
        self, tmp_path, initial_state, final_state
    ):
        telemetry_module = _load_telemetry_module()
        output_dir = tmp_path / "output"
        log_dir = tmp_path / "logs"
        output_dir.mkdir()

        def plan(state, *, final=False):
            agents = [
                {"name": "security-reviewer", "status": "DISPATCH"}
            ]
            if state == "duplicate":
                agents.append(
                    {"name": "security-reviewer", "status": "SKIPPED_TRIAGE"}
                )
            result = {"agents": agents}
            if final:
                result["changed_files"] = ["src/a.py"]
            return result

        if initial_state != "missing":
            path = run_paths.artifact_path(
                output_dir, "dispatch_plan_initial"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(plan(initial_state))
            )
        if final_state != "missing":
            path = run_paths.artifact_path(output_dir, "dispatch_plan")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(plan(final_state, final=True))
            )
        (output_dir / "review-context.json").write_text(
            json.dumps({"git": {"changed_files": ["src/a.py"]}})
        )
        telemetry = telemetry_module.ReviewTelemetry(
            str(output_dir), log_dir=str(log_dir)
        )
        telemetry.start(run_id="producer-run", repo_path="/safe/repo")
        telemetry.log_agent_start(
            "security-reviewer", scope_paths=["src/a.py"]
        )
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")
        producer_manifest = json.loads(Path(telemetry.manifest_path).read_text())
        producer_dispatch = producer_manifest["dispatch"]
        expected_duplicate_names = {
            name: ["security-reviewer"]
            for name, state in (
                ("planner_baseline", initial_state),
                ("final_plan", final_state),
            )
            if state == "duplicate"
        }
        expected_reasons = {
            *(
                ["planner_baseline_unavailable"]
                if initial_state == "missing"
                else []
            ),
            *(
                ["final_plan_unavailable"]
                if final_state == "missing"
                else []
            ),
            *(f"{name}_duplicate_agents" for name in expected_duplicate_names),
        }

        assert producer_dispatch["planner_baseline_available"] is (
            initial_state != "missing"
        )
        assert producer_dispatch["final_plan_available"] is (
            final_state != "missing"
        )
        assert producer_dispatch["comparison_available"] is False
        assert producer_dispatch["duplicate_agent_names"] == expected_duplicate_names
        assert set(producer_dispatch["invalid_reason_codes"]) == expected_reasons

        [run] = load_runs(log_dir)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["run"]["id"] == "producer-run"
        assert run["warnings"] == []
        assert run["dispatch"] is None
        assert run["assignment"] == producer_manifest["assignment"]
        assert run["outcome"] == sanitize._sanitize_outcome(
            producer_manifest["outcome"]
        )
        assert measured["metric_availability"]["lifecycle"] == "complete"
        assert measured["lifecycle"]["started_events"] == 1
        assert measured["lifecycle"]["completed_events"] == 0
        assert measured["lifecycle"]["incomplete_identities"] == [
            "security-reviewer"
        ]

    def test_agent_set_mismatch_sidecar_remains_authoritative_and_partial(
        self, tmp_path
    ):
        """An agent added between plans; a removed agent takes the same
        producer path with the larger set on the other side."""
        initial_names = ["code-reviewer"]
        final_names = ["code-reviewer", "security-reviewer"]
        planner_count, final_count = 1, 2
        telemetry_module = _load_telemetry_module()
        output_dir = tmp_path / "output"
        log_dir = tmp_path / "logs"
        output_dir.mkdir()

        def plan(names):
            return {
                "agents": [
                    {"name": name, "status": "DISPATCH"}
                    for name in names
                ]
            }

        initial_path = run_paths.artifact_path(
            output_dir, "dispatch_plan_initial"
        )
        initial_path.parent.mkdir(parents=True, exist_ok=True)
        initial_path.write_text(
            json.dumps(plan(initial_names))
        )
        run_paths.artifact_path(output_dir, "dispatch_plan").write_text(
            json.dumps({**plan(final_names), "changed_files": ["src/a.py"]})
        )
        (output_dir / "review-context.json").write_text(
            json.dumps({"git": {"changed_files": ["src/a.py"]}})
        )
        telemetry = telemetry_module.ReviewTelemetry(
            str(output_dir), log_dir=str(log_dir)
        )
        telemetry.start(run_id="producer-run", repo_path="/safe/repo")
        telemetry.log_agent_start("code-reviewer", scope_paths=["src/a.py"])
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")
        producer_manifest = json.loads(Path(telemetry.manifest_path).read_text())

        [run] = load_runs(log_dir)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["run"]["id"] == "producer-run"
        assert run["warnings"] == []
        assert run["dispatch"] == {
            "planner_baseline_available": True,
            "final_plan_available": True,
            "comparison_available": False,
            "planner_candidate_count": planner_count,
            "final_dispatch_count": final_count,
            "adjustment_counts": {"added": 0, "removed": 0, "unchanged": 0},
            "invalid_reason_codes": ["dispatch_agent_set_mismatch"],
            "agents": {},
            "plan_projections": {
                "planner_baseline": {
                    name: "DISPATCH" for name in initial_names
                },
                "final_plan": {
                    name: "DISPATCH" for name in final_names
                },
            },
        }
        assert run["assignment"] == producer_manifest["assignment"]
        assert run["outcome"] == sanitize._sanitize_outcome(
            producer_manifest["outcome"]
        )
        assert measured["metric_availability"]["dispatch"] == "partial"
        assert measured["metric_availability"]["assignment"] == "complete"
        cohort_dispatch = aggregate_cohort([measured])["dispatch"]
        assert cohort_dispatch["planner_candidates"] == planner_count
        assert cohort_dispatch["actual_dispatches"] == final_count
        assert cohort_dispatch["adjustments"] is None
        assert cohort_dispatch["adjustment_rate"] is None

    def test_agent_set_mismatch_sidecar_recomputes_mixed_status_counts(self, tmp_path):
        telemetry_module = _load_telemetry_module()
        output_dir = tmp_path / "output"
        log_dir = tmp_path / "logs"
        output_dir.mkdir()
        initial_path = run_paths.artifact_path(
            output_dir, "dispatch_plan_initial"
        )
        initial_path.parent.mkdir(parents=True, exist_ok=True)
        initial_path.write_text(
            json.dumps(
                {
                    "agents": [
                        {"name": "z-reviewer", "status": "SKIPPED_TRIAGE"},
                        {"name": "a-reviewer", "status": "DISPATCH"},
                    ]
                }
            )
        )
        run_paths.artifact_path(output_dir, "dispatch_plan").write_text(
            json.dumps(
                {
                    "agents": [
                        {"name": "m-reviewer", "status": "SKIPPED_OVERRIDE"},
                        {"name": "a-reviewer", "status": "DISPATCH_OVERRIDE"},
                    ],
                    "changed_files": ["src/a.py"],
                }
            )
        )
        (output_dir / "review-context.json").write_text(
            json.dumps({"git": {"changed_files": ["src/a.py"]}})
        )
        telemetry = telemetry_module.ReviewTelemetry(
            str(output_dir), log_dir=str(log_dir)
        )
        telemetry.start(run_id="producer-run", repo_path="/safe/repo")
        telemetry.finalize(step=11, phase="OUTPUT", title="Present Results")

        [run] = load_runs(log_dir)
        measured = measure_run(run, tmp_path, include_transcripts=False)
        cohort_dispatch = aggregate_cohort([measured])["dispatch"]

        assert run["dispatch"]["plan_projections"] == {
            "planner_baseline": {
                "a-reviewer": "DISPATCH",
                "z-reviewer": "SKIPPED_TRIAGE",
            },
            "final_plan": {
                "a-reviewer": "DISPATCH_OVERRIDE",
                "m-reviewer": "SKIPPED_OVERRIDE",
            },
        }
        assert run["dispatch"]["planner_candidate_count"] == 1
        assert run["dispatch"]["final_dispatch_count"] == 1
        assert measured["metric_availability"]["dispatch"] == "partial"
        assert cohort_dispatch["planner_candidates"] == 1
        assert cohort_dispatch["actual_dispatches"] == 1
        assert cohort_dispatch["adjustments"] is None

    def test_duplicate_dispatch_allowance_rejects_boolean_adjustment_counts(
        self, tmp_path
    ):
        manifest = _manifest("sidecar-run")
        manifest["dispatch"] = _producer_duplicate_dispatch()
        manifest["dispatch"]["adjustment_counts"]["added"] = False
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-fallback"
        assert "invalid_manifest_fallback" in run["warnings"]

    @pytest.mark.parametrize(
        "malform",
        [
            # A missing or an extra reason both fail the one
            # `set(reasons) != expected_reasons` check.
            lambda dispatch: dispatch.__setitem__("invalid_reason_codes", []),
            lambda dispatch: dispatch.pop("duplicate_agent_names"),
            lambda dispatch: dispatch.__setitem__(
                "planner_baseline_available", 1
            ),
            lambda dispatch: (
                dispatch.__setitem__("planner_baseline_available", False),
                dispatch["invalid_reason_codes"].append(
                    "planner_baseline_unavailable"
                ),
            ),
        ],
        ids=[
            "missing-reason",
            "missing-names",
            "non-boolean-availability",
            "duplicate-for-unavailable-plan",
        ],
    )
    def test_duplicate_dispatch_allowance_rejects_inexact_producer_state(
        self, tmp_path, malform
    ):
        manifest = _manifest("sidecar-run")
        manifest["dispatch"] = _producer_duplicate_dispatch()
        malform(manifest["dispatch"])
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-fallback"
        assert "invalid_manifest_fallback" in run["warnings"]

    def test_duplicate_dispatch_allowance_rejects_nonproducer_agent_names(
        self, tmp_path
    ):
        """Every non-producer name fails the one producer agent-name regex
        (its shape is owned by `dispatch_status.py`); prose also carries
        the leak assertion."""
        invalid_name = "reviewer: private prose"
        manifest = _manifest("sidecar-run")
        manifest["dispatch"] = _producer_duplicate_dispatch()
        manifest["dispatch"]["duplicate_agent_names"]["planner_baseline"] = [
            invalid_name
        ]
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-fallback"
        assert "invalid_manifest_fallback" in run["warnings"]
        assert invalid_name not in json.dumps(run)

    @pytest.mark.parametrize(
        "unsafe_run_id",
        # One per branch of `run_paths.SAFE_RUN_ID_SEGMENT_RE`: a character
        # outside the class (a Windows path, a pipe, or markup fails the
        # same class), the `..` lookahead, and the 256-character bound. The
        # producer's own upload test (`test_telemetry_share.py`) feeds only
        # `safe/nested` and `../outside`, so the lookahead and the bound
        # are pinned here alone.
        [
            "Users/person/private-repo",
            "safe..nested",
            "run" + "x" * 254,
        ],
        ids=[
            "posix-path",
            "double-dot",
            "too-long",
        ],
    )
    def test_unsafe_sidecar_run_id_cannot_suppress_or_leak_over_legacy_fallback(
        self, tmp_path, unsafe_run_id
    ):
        manifest = _manifest(unsafe_run_id)
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-fallback"
        assert unsafe_run_id not in json.dumps(run)

    @pytest.mark.parametrize(
        "safe_run_id",
        # A producer-shaped id, and the longest id the bound admits.
        [
            "550e8400-e29b-41d4-a716-446655440000",
            "a" * 256,
        ],
        ids=["uuid", "boundary-length"],
    )
    def test_bounded_ascii_token_run_ids_remain_supported(
        self, tmp_path, safe_run_id
    ):
        _write_manifest(tmp_path / "review.manifest.json", _manifest(safe_run_id))
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == safe_run_id

    def test_canonical_equivalent_duplicate_manifests_collapse_to_one_run(
        self, tmp_path
    ):
        first = _manifest("duplicate-run")
        first["assignment"] = {
            "changed_files": [
                "src/a.py",
                "src/b.py",
                "src/c.py",
                "src/d.py",
                "vendor/a.js",
                "vendor/b.js",
            ],
            "reviewable_files": ["src/a.py", "src/b.py", "src/c.py", "src/d.py"],
            "assigned_files_by_agent": {
                "code-reviewer": ["src/a.py", "src/b.py"]
            },
            "assigned_files": ["src/a.py", "src/b.py"],
            "file_exclusions": [
                {"path": "vendor/a.js", "reason": "noise_filtered"},
                {"path": "vendor/b.js", "reason": "noise_filtered"},
            ],
            "unassigned_reviewable_files": ["src/c.py", "src/d.py"],
            "semantics": "generated_scope_not_proof_of_model_read",
        }
        first["warnings"] = ["registry_unavailable", "agent_transcript_missing"]
        first["agents"] = {
            "started": [
                _agent_start("code-reviewer", run_id="duplicate-run"),
                _agent_start(
                    "security-reviewer",
                    run_id="duplicate-run",
                    timestamp="2026-07-19T10:00:11+00:00",
                ),
                _agent_start(
                    "security-reviewer",
                    run_id="duplicate-run",
                    timestamp="2026-07-19T10:00:12+00:00",
                ),
            ],
            "completed": [],
            "incomplete": [
                "security-reviewer",
                "security-reviewer",
                "code-reviewer",
            ],
        }
        first["dispatch"]["invalid_reason_codes"] = [
            "first_reason",
            "second_reason",
        ]
        second = copy.deepcopy(first)
        second["ignored_private_payload"] = "PRIVATE PROSE"
        second["run"]["ignored_path"] = "/Users/person/private-repo"
        second["dispatch"]["agents"] = dict(
            reversed(list(second["dispatch"]["agents"].items()))
        )
        second["dispatch"]["invalid_reason_codes"].reverse()
        second["warnings"].reverse()
        second["agents"]["incomplete"].reverse()
        for name in ("changed_files", "reviewable_files", "assigned_files", "unassigned_reviewable_files", "file_exclusions"):
            second["assignment"][name].reverse()
        second["assignment"]["assigned_files_by_agent"]["code-reviewer"].reverse()
        left = tmp_path / "left"
        right = tmp_path / "right"
        _write_manifest(left / "a.manifest.json", first)
        _write_manifest(left / "b.manifest.json", second)
        _write_manifest(right / "a.manifest.json", second)
        _write_manifest(right / "b.manifest.json", first)

        runs = load_runs(left)

        assert len(runs) == 1
        assert runs[0]["run"]["id"] == "duplicate-run"
        assert runs[0]["agents"]["incomplete"] == [
            "code-reviewer",
            "security-reviewer",
            "security-reviewer",
        ]
        assert "PRIVATE" not in json.dumps(runs)
        assert runs == load_runs(right)

    def test_order_sensitive_event_reordering_remains_a_conflict(
        self, tmp_path
    ):
        """`_canonical_manifest` sorts only order-free lists, so a
        reordered event list stays a conflict. This pins `started`; a
        canonicalizer that began sorting `steps` or `completed` would go
        unnoticed here, which is acceptable because canonicalization only
        serves duplicate collapse."""
        first = _manifest("duplicate-run")
        first["agents"]["started"] = [
            {"event": "agent_start", "agent": "code-reviewer"},
            {"event": "agent_start", "agent": "security-reviewer"},
        ]
        second = copy.deepcopy(first)
        second["agents"]["started"].reverse()
        _write_manifest(tmp_path / "a.manifest.json", first)
        _write_manifest(tmp_path / "b.manifest.json", second)

        [run] = load_runs(tmp_path)

        assert run["status"] == "duplicate_run_id_conflict"

    def test_conflicting_duplicate_run_ids_emit_one_opaque_unmeasured_diagnostic(
        self, tmp_path
    ):
        first = _manifest("duplicate-run")
        first["run"]["repo_path"] = "/Users/person/first-private-repo"
        second = _manifest("duplicate-run")
        second["run"]["repo_path"] = "/Users/person/second-private-repo"
        second["outcome"]["summary"]["final_finding_count"] = 99
        second["outcome"]["verdict"] = "PRIVATE CONFLICT PROSE"
        _write_manifest(tmp_path / "a.manifest.json", first)
        _write_manifest(tmp_path / "b.manifest.json", second)

        [diagnostic] = load_runs(tmp_path)
        measured = measure_run(diagnostic, tmp_path)
        cohort = aggregate_cohort([measured])

        assert diagnostic["status"] == "duplicate_run_id_conflict"
        assert diagnostic["run"]["id"].startswith("duplicate-")
        assert diagnostic["run"]["id"] != "duplicate-run"
        assert diagnostic["warnings"] == ["duplicate_run_id_conflict"]
        assert set(measured["metric_availability"].values()) == {"missing"}
        assert cohort["runs"] == 0
        assert cohort["availability"]["dispatch"]["missing"] == 0
        assert cohort["dispatch"]["planner_candidates"] is None
        assert cohort["assignment"]["changed_files"] is None
        assert cohort["outcomes"]["raw_findings"] is None
        assert cohort["wall_time"]["total_ms"] is None
        serialized = json.dumps(measured)
        assert "/Users/person" not in serialized
        assert "first-private-repo" not in serialized
        assert "second-private-repo" not in serialized
        assert "PRIVATE CONFLICT PROSE" not in serialized

    def test_duplicate_conflict_is_deterministic_across_file_and_key_order(
        self, tmp_path
    ):
        first = _manifest("duplicate-run")
        second = _manifest("duplicate-run")
        second["outcome"]["summary"]["final_finding_count"] = 2
        left = tmp_path / "left"
        right = tmp_path / "right"
        _write_manifest(left / "a.manifest.json", first)
        _write_manifest(left / "b.manifest.json", second)
        _write_manifest(right / "a.manifest.json", dict(reversed(list(second.items()))))
        _write_manifest(right / "b.manifest.json", dict(reversed(list(first.items()))))

        assert load_runs(left) == load_runs(right)

    def test_conflict_diagnostic_does_not_consume_last_measured_run_slot(
        self, tmp_path
    ):
        first = _manifest(
            "duplicate-run", started_at="2026-07-20T12:00:00+00:00"
        )
        first["run"]["repo_path"] = "/Users/person/private-first"
        second = copy.deepcopy(first)
        second["run"]["repo_path"] = "/Users/person/private-second"
        second["outcome"]["summary"]["final_finding_count"] = 2
        _write_manifest(tmp_path / "a.manifest.json", first)
        _write_manifest(tmp_path / "b.manifest.json", second)
        _write_manifest(
            tmp_path / "unique.manifest.json",
            _manifest("unique-run", started_at="2026-07-19T12:00:00+00:00"),
        )

        [filtered] = load_runs(tmp_path, run_id="duplicate-run")
        limited = load_runs(tmp_path, last=1)
        measured = [
            measure_run(run, tmp_path, include_transcripts=False)
            for run in limited
        ]
        cohort = aggregate_cohort(measured)

        assert filtered["status"] == "duplicate_run_id_conflict"
        assert [run["status"] for run in limited] == [
            "duplicate_run_id_conflict",
            "complete",
        ]
        assert limited[1]["run"]["id"] == "unique-run"
        assert cohort["runs"] == 1
        assert "/Users/person" not in json.dumps(limited)

    def test_manifest_and_legacy_run_id_collision_is_a_conflict(self, tmp_path):
        _write_manifest(tmp_path / "sidecar.manifest.json", _manifest("shared-run"))
        _write_jsonl(tmp_path / "standalone.jsonl", _legacy_events("shared-run"))

        [run] = load_runs(tmp_path)

        assert run["status"] == "duplicate_run_id_conflict"
        assert run["warnings"] == ["duplicate_run_id_conflict"]

    def test_malformed_and_non_event_inputs_fail_soft_without_zero_runs(self, tmp_path):
        (tmp_path / "bad.manifest.json").write_text("[]")
        (tmp_path / "bad.jsonl").write_text("not json\n{}\n[]\n")
        missing = tmp_path / "missing"

        assert load_runs(tmp_path) == []
        assert load_runs(missing) == []

    def test_invalid_numeric_fields_degrade_availability_without_crashing(
        self, tmp_path
    ):
        """An integer past `_nonnegative_int`'s bound degrades the dispatch
        family at load level; the float conjuncts are swept at measure
        level by `TestMeasureRun::test_invalid_manifest_numerics_are_
        omitted_and_never_drive_wall_time`."""
        manifest = _manifest("nonfinite")
        manifest["dispatch"]["planner_candidate_count"] = 10**1_000
        _write_manifest(tmp_path / "nonfinite.manifest.json", manifest)

        [run] = load_runs(tmp_path)

        assert run["dispatch"] is None
        assert measure_run(run, tmp_path, include_transcripts=False)[
            "metric_availability"
        ]["dispatch"] == "missing"


class TestMeasureRun:
    def test_partial_correlation_without_counts_renders_missing_glyphs(self):
        """Sanitization omits absent/invalid correlation counts — the table
        must show the missing glyph, not a fabricated "partial 0/0"."""
        manifest = _manifest()
        measured = measure_run(
            manifest, Path("/nonexistent"), include_transcripts=False
        )
        cohort = aggregate_cohort([measured])
        measured["metric_availability"]["transcript"] = "partial"
        measured["transcript"] = {"correlation": {}}

        table = format_table([measured], cohort)

        assert "partial —/—" in table
        assert "partial 0/0" not in table

    def test_running_coverage_snapshot_is_not_a_coverage_observation(self):
        """Only a settled manifest carries coverage the pipeline stands behind.

        `_build_manifest` builds the section at finalize alone, so a
        running manifest holding one is a pre-change snapshot of a run
        that never settled. It used to be credited as a partial
        observation; it is now missing, which is what the run actually
        measured.
        """
        manifest = _running_manifest("running-coverage")

        measured = measure_run(
            manifest, Path("/nonexistent"), include_transcripts=False
        )
        cohort = aggregate_cohort([measured])

        assert measured["assignment"] == manifest["assignment"]
        assert measured["metric_availability"]["assignment"] == "missing"
        assert "partial" not in format_table([measured], cohort)
        assert cohort["assignment"] == {
            "changed_files": None,
            "reviewable_files": None,
            "assigned_files": None,
            "file_exclusions": None,
            "unassigned_reviewable_files": None,
            "assignment_rate": None,
            "available_runs": 0,
            "semantics": "generated_scope_not_proof_of_model_read",
            "availability": {
                "available": 0,
                "complete": 0,
                "partial": 0,
                "missing": 1,
                "disabled": 0,
            },
        }

    def test_running_manifest_caps_outcome_states_at_partial(self, tmp_path):
        """A running manifest with numeric summary totals (an interactive
        rerun over a prior terminal summary) is partial evidence. Only
        status=complete may report complete outcomes — presence of numbers
        is not completion."""
        manifest = _manifest(ended_at=None)
        manifest["status"] = "running"

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["outcomes"] == "partial"
        assert measured["metric_availability"]["raw_findings"] == "partial"
        assert measured["metric_availability"]["final_findings"] == "partial"

    def test_running_manifest_without_summary_reports_outcomes_missing(
        self, tmp_path
    ):
        manifest = _manifest(ended_at=None)
        manifest["status"] = "running"
        manifest["outcome"].pop("summary")

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["outcomes"] == "missing"
        assert measured["metric_availability"]["raw_findings"] == "missing"
        assert measured["metric_availability"]["final_findings"] == "missing"

    def test_transcript_enrichment_recognizes_every_synthesis_identity(
        self, monkeypatch, tmp_path
    ):
        registry = tmp_path / "registry.json"
        registry.write_text(json.dumps({"agents": {"code-reviewer": {}}}))
        observed = {}

        def enrich(_manifest, _sessions_root, recognized):
            observed["recognized"] = recognized
            return _complete_empty_transcript()

        monkeypatch.setattr(measure, "_load_transcript_module", lambda: enrich)

        measure_run(_manifest(), tmp_path, registry_path=registry)

        assert observed["recognized"] >= {
            "review-reconciliator",
            "decision-reviewer",
            "critic",
        }

    def test_preserves_canonical_data_when_transcript_is_missing(self, tmp_path):
        manifest = _manifest(session_id="missing-session")

        measured = measure_run(manifest, tmp_path)

        assert measured["dispatch"] == manifest["dispatch"]
        assert measured["assignment"] == manifest["assignment"]
        assert measured["outcome"] == manifest["outcome"]
        assert measured["transcript"]["available"] is False
        assert measured["transcript"]["usage"] is None
        assert measured["metric_availability"]["transcript"] == "missing"
        assert manifest["availability"] == {
            "pipeline": True,
            "transcript": False,
            "assignment": True,
        }

    def test_no_transcripts_is_disabled_not_missing_and_skips_registry(self, tmp_path):
        measured = measure_run(
            _manifest(session_id="session-1"),
            tmp_path / "does-not-exist",
            registry_path=tmp_path / "missing-registry.json",
            include_transcripts=False,
        )

        assert measured["transcript"]["reason"] == "disabled"
        assert measured["metric_availability"]["transcript"] == "disabled"
        assert "registry_unavailable" not in measured["warnings"]

    def test_registry_failure_preserves_pipeline_and_marks_transcript_unavailable(self, tmp_path):
        measured = measure_run(
            _manifest(session_id="session-1"),
            tmp_path,
            registry_path=tmp_path / "missing-registry.json",
        )

        assert measured["dispatch"]["planner_candidate_count"] == 2
        assert measured["transcript"]["reason"] == "registry_unavailable"
        assert measured["warnings"] == ["registry_unavailable"]

    @pytest.mark.parametrize(
        "started,ended,summary,expected",
        [
            (
                "2026-07-19T12:00:00+02:00",
                "2026-07-19T10:01:30+00:00",
                999,
                90_000,
            ),
            ("bad", None, 12_345, 12_345),
            ("bad", None, None, None),
        ],
        ids=["timestamps", "summary-fallback", "unavailable"],
    )
    def test_derives_wall_time_without_zero_filling(
        self, tmp_path, started, ended, summary, expected
    ):
        manifest = _manifest(started_at=started, ended_at=ended)
        manifest["outcome"]["summary"]["total_duration_ms"] = summary

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["wall_time_ms"] == expected
        expected_state = "complete" if expected is not None else "missing"
        assert measured["metric_availability"]["wall_time"] == expected_state

    def test_naive_timestamps_do_not_supply_wall_time(self, tmp_path):
        """`_parse_time` returns None for any naive timestamp; one naive end
        beside an aware start stands for two naive ones."""
        manifest = _manifest(
            started_at="2026-07-19T10:00:00+00:00",
            ended_at="2026-07-19T10:01:00",
        )
        manifest["outcome"]["summary"].pop("total_duration_ms")

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["wall_time_ms"] is None
        assert measured["metric_availability"]["wall_time"] == "missing"

    def test_critic_is_complete_only_for_exact_supported_verdicts(
        self, tmp_path
    ):
        """One membership test against the producer's verdict vocabulary;
        `STAND` stands for `REVISE` and `ESCALATE`."""
        verdict = "STAND"
        manifest = _manifest()
        manifest["outcome"]["critic_verdict"] = verdict

        measured = measure_run(manifest, tmp_path, include_transcripts=False)
        cohort = aggregate_cohort([measured])

        assert measured["metric_availability"]["critic"] == "complete"
        assert measured["outcome"]["critic_verdict"] == verdict
        assert cohort["critic"]["verdicts"] == {verdict: 1}

    @pytest.mark.parametrize(
        "verdict",
        # One membership test: a missing verdict and a near-miss spelling.
        # The fixed `unavailable` sentinel is pinned, with its retention, by
        # `test_fixed_unavailable_critic_sentinel_is_retained_but_not_available`.
        [None, "stand"],
        ids=["missing", "lowercase"],
    )
    def test_invalid_or_missing_critic_verdict_is_missing(
        self, tmp_path, verdict
    ):
        manifest = _manifest()
        manifest["outcome"]["critic_verdict"] = verdict

        measured = measure_run(manifest, tmp_path, include_transcripts=False)
        cohort = aggregate_cohort([measured])

        assert measured["metric_availability"]["critic"] == "missing"
        assert cohort["critic"]["verdicts"] is None

    def test_critic_skip_disables_availability_and_excludes_sentinel_from_aggregate(
        self, tmp_path
    ):
        manifest = _manifest()
        manifest["outcome"]["summary"]["quick_mode"] = True
        manifest["steps"] = [
            {
                "run_id": "run-1",
                "event": "step",
                "step": 10,
                "title": "Decision Critic",
                "decisions": {"critic_skipped": True},
            }
        ]
        manifest["outcome"]["critic_verdict"] = "unavailable"

        measured = measure_run(manifest, tmp_path, include_transcripts=False)
        cohort = aggregate_cohort([measured])

        assert measured["metric_availability"]["critic"] == "disabled"
        assert cohort["critic"]["verdicts"] is None
        assert cohort["critic"]["availability"] == {
            "available": 0,
            "complete": 0,
            "partial": 0,
            "missing": 0,
            "disabled": 1,
        }

    @pytest.mark.parametrize(
        "fragment",
        [
            # One row per missing half of the producer step identity (fix
            # 57b6db74); a fragment missing both fails both checks.
            {
                "event": "step",
                "step": 10,
                "decisions": {"critic_skipped": True},
            },
            {
                "run_id": "run-1",
                "step": 10,
                "decisions": {"critic_skipped": True},
            },
        ],
        ids=["missing-run-id", "missing-event"],
    )
    def test_bare_step_fragment_cannot_disable_a_real_critic_verdict(
        self, tmp_path, fragment
    ):
        """A malformed sidecar step carrying a skip decision but not the
        producer's step identity (event="step" + run_id, both shipped
        together with critic_skipped itself) is not producer evidence —
        honoring it would turn a real STAND/REVISE/ESCALATE verdict into
        "disabled"."""
        manifest = _manifest()
        manifest["steps"] = [fragment]
        manifest["outcome"]["critic_verdict"] = "STAND"

        measured = measure_run(manifest, tmp_path, include_transcripts=False)
        cohort = aggregate_cohort([measured])

        assert measured["metric_availability"]["critic"] == "complete"
        assert cohort["critic"]["verdicts"] == {"STAND": 1}

    @pytest.mark.parametrize(
        "follower",
        [
            {"step": 10},
            {"run_id": "run-2", "event": "step", "step": 10},
        ],
        ids=["malformed-fragment", "foreign-run"],
    )
    def test_foreign_or_malformed_step10_cannot_reset_a_deliberate_skip(
        self, tmp_path, follower
    ):
        """Latest-wins selection must only consider producer-conformant
        step events belonging to THIS run — a malformed {"step": 10}
        fragment or another run's step-10 event after a valid skip would
        reset it, turning a deliberate skip into missing critic evidence."""
        manifest = _manifest()
        manifest["steps"] = [
            {
                "run_id": "run-1",
                "event": "step",
                "step": 10,
                "title": "Decision Critic",
                "decisions": {"critic_skipped": True},
            },
            follower,
        ]
        manifest["outcome"]["critic_verdict"] = "unavailable"

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["critic"] == "disabled"

    def test_step_10_rerun_supersedes_stale_critic_skip(self, tmp_path):
        """The producer's skip decision is latest-wins (a rerun clears it),
        but append-only telemetry keeps both step-10 events. The superseded
        skip must not report "disabled" over the rerun's real verdict."""
        manifest = _manifest()
        manifest["steps"] = [
            {
                "run_id": "run-1",
                "event": "step",
                "step": 10,
                "title": "Decision Critic",
                "decisions": {"critic_skipped": True},
            },
            {
                "run_id": "run-1",
                "event": "step",
                "step": 10,
                "title": "Decision Critic",
            },
        ]
        manifest["outcome"]["critic_verdict"] = "STAND"

        measured = measure_run(manifest, tmp_path, include_transcripts=False)
        cohort = aggregate_cohort([measured])

        assert measured["metric_availability"]["critic"] == "complete"
        assert cohort["critic"]["verdicts"] == {"STAND": 1}

    def test_latest_step_10_skip_still_disables_after_earlier_run(
        self, tmp_path
    ):
        """Symmetric direction: when the LATEST step-10 event carries the
        skip decision, it is authoritative regardless of earlier events."""
        manifest = _manifest()
        manifest["steps"] = [
            {"run_id": "run-1", "event": "step", "step": 10, "title": "Decision Critic"},
            {
                "run_id": "run-1",
                "event": "step",
                "step": 10,
                "title": "Decision Critic",
                "decisions": {"critic_skipped": True},
            },
        ]
        manifest["outcome"]["critic_verdict"] = "unavailable"

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["critic"] == "disabled"

    def test_fixed_unavailable_critic_sentinel_is_retained_but_not_available(
        self, tmp_path
    ):
        manifest = _manifest()
        manifest["outcome"]["critic_verdict"] = "unavailable"

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["outcome"]["critic_verdict"] == "unavailable"
        assert measured["metric_availability"]["critic"] == "missing"

    def test_arbitrary_critic_prose_is_dropped_from_json_and_table(
        self, tmp_path
    ):
        manifest = _manifest()
        manifest["outcome"]["critic_verdict"] = "PRIVATE FINDING PROSE"

        measured = measure_run(manifest, tmp_path, include_transcripts=False)
        rendered_json = format_json([measured], aggregate_cohort([measured]))
        rendered_table = format_table([measured], aggregate_cohort([measured]))

        assert "critic_verdict" not in measured["outcome"]
        assert measured["metric_availability"]["critic"] == "missing"
        assert "PRIVATE FINDING PROSE" not in rendered_json
        assert "PRIVATE FINDING PROSE" not in rendered_table
        assert "3→1/—" in rendered_table

    @pytest.mark.parametrize(
        "contradiction",
        [
            "planner-count",
            "final-count",
            "adjustments",
            "agent-change",
            "missing-agent",
        ],
        ids=[
            "planner-count",
            "final-count",
            "adjustments",
            "agent-change",
            "missing-agent",
        ],
    )
    def test_complete_dispatch_requires_decisions_to_exactly_explain_counts(
        self, tmp_path, contradiction
    ):
        manifest = _manifest()
        dispatch = manifest["dispatch"]
        if contradiction == "planner-count":
            dispatch["planner_candidate_count"] = 1
        elif contradiction == "final-count":
            dispatch["final_dispatch_count"] = 2
        elif contradiction == "adjustments":
            dispatch["adjustment_counts"] = {
                "added": 1,
                "removed": 0,
                "unchanged": 1,
            }
        elif contradiction == "agent-change":
            dispatch["agents"]["security-reviewer"]["change"] = "unchanged"
        else:
            dispatch["agents"].pop("security-reviewer")

        measured = measure_run(manifest, tmp_path, include_transcripts=False)
        cohort = aggregate_cohort([measured])

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"
        assert cohort["dispatch"]["planner_candidates"] is None
        assert cohort["dispatch"]["actual_dispatches"] is None
        assert cohort["dispatch"]["adjustments"] is None

    # `_sanitize_dispatch`'s per-agent loop is literally `for status_name
    # in ("initial_status", "final_status")`, so the status_field axis was
    # free. One param per conjunct the guard can tell apart: absent,
    # non-str, unsupported-str (an empty string is one more unsupported
    # string). The non-str row is unhashable on purpose: the vocabulary is
    # a frozenset, so a hashable non-string (`None`) fails the membership
    # test anyway, and only an unhashable one needs the `isinstance`
    # conjunct to be rejected rather than raise.
    @pytest.mark.parametrize("status_field", ["initial_status"])
    @pytest.mark.parametrize(
        "invalid_status",
        [
            pytest.param("__missing__", id="missing"),
            pytest.param([], id="list"),
            "UNKNOWN",
        ],
    )
    def test_dispatch_decisions_require_supported_nonempty_statuses(
        self, tmp_path, status_field, invalid_status
    ):
        manifest = _manifest()
        decision = manifest["dispatch"]["agents"]["code-reviewer"]
        if invalid_status == "__missing__":
            decision.pop(status_field)
        else:
            decision[status_field] = invalid_status

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    def test_dispatch_model_provenance_survives_supported_load(self, tmp_path):
        manifest = _manifest()
        decision = manifest["dispatch"]["agents"]["code-reviewer"]
        decision["model_tier"] = "inherit"
        decision["declared_model"] = "opus"
        _write_manifest(tmp_path / "review.manifest.json", manifest)

        [run] = load_runs(tmp_path)
        loaded = run["dispatch"]["agents"]["code-reviewer"]

        assert loaded["model_tier"] == "inherit"
        assert loaded["declared_model"] == "opus"

    def test_load_runs_drops_unsafe_dispatch_adjustment_reason(self, tmp_path):
        """PR-influenced adjustment prose must not bypass free-text
        hardening through scalar manifest fields."""
        unsafe_reason = "reason \x1b[31mred\x1b[0m"
        manifest = _manifest()
        decision = manifest["dispatch"]["agents"]["code-reviewer"]
        decision["adjustment_reason"] = unsafe_reason
        _write_manifest(tmp_path / "review.manifest.json", manifest)

        [run] = load_runs(tmp_path)
        loaded = run["dispatch"]["agents"]["code-reviewer"]

        assert loaded["final_status"] == "DISPATCH"
        assert "adjustment_reason" not in loaded
        assert unsafe_reason not in json.dumps(run)

    def test_unknown_dispatch_signal_sanitizes_to_none(self, tmp_path):
        manifest = _manifest()
        manifest["dispatch"]["agents"]["code-reviewer"]["initial_signal"] = "unknown"

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"]["agents"]["code-reviewer"]["initial_signal"] is None

    @pytest.mark.parametrize(
        "status,dispatched",
        [
            # One dispatched and one skipped status: the vocabulary itself
            # is the producer's (`test_metrics_uses_canonical_telemetry_contract`).
            ("DISPATCH", True),
            ("SKIPPED", False),
        ],
    )
    def test_final_only_projection_accepts_supported_status_vocabulary(
        self, tmp_path, status, dispatched
    ):
        manifest = _manifest()
        manifest["dispatch"] = _final_only_dispatch()
        decision = manifest["dispatch"]["agents"]["code-reviewer"]
        decision["initial_status"] = status
        decision["final_status"] = status
        count = int(dispatched)
        manifest["dispatch"]["planner_candidate_count"] = count
        manifest["dispatch"]["final_dispatch_count"] = count

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is not None
        assert measured["dispatch"]["comparison_available"] is False
        assert measured["dispatch"]["agents"]["code-reviewer"]["change"] == "unchanged"

    @pytest.mark.parametrize(
        "invalid_status",
        [
            # One per conjunct of the per-agent status check: absent,
            # non-str (unhashable, as in the comparable-mode table above),
            # unsupported-str.
            pytest.param("__missing__", id="missing"),
            pytest.param([], id="list"),
            "UNKNOWN",
        ],
    )
    def test_final_only_projection_rejects_matching_invalid_statuses(
        self, tmp_path, invalid_status
    ):
        manifest = _manifest()
        manifest["dispatch"] = _final_only_dispatch()
        decision = manifest["dispatch"]["agents"]["code-reviewer"]
        if invalid_status == "__missing__":
            decision.pop("initial_status")
            decision.pop("final_status")
        else:
            decision["initial_status"] = invalid_status
            decision["final_status"] = invalid_status
        manifest["dispatch"]["planner_candidate_count"] = 0
        manifest["dispatch"]["final_dispatch_count"] = 0

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    def test_invalid_sidecar_dispatch_status_falls_back_to_legacy(
        self, tmp_path
    ):
        """The load-level contract for the per-agent status guard, whose
        conjuncts are swept at measure level above."""
        manifest = _manifest("sidecar-run")
        manifest["dispatch"]["agents"]["code-reviewer"][
            "final_status"
        ] = "DISPATCHED"
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-fallback"
        assert "invalid_manifest_fallback" in run["warnings"]

    @pytest.mark.parametrize(
        "contradiction",
        [
            "planner-count",
            "final-count",
            "matching-agent-sets",
            "unsupported-status",
            "missing-projections",
        ],
    )
    def test_agent_set_mismatch_counts_require_exact_plan_projections(
        self, tmp_path, contradiction
    ):
        manifest = _manifest()
        manifest["dispatch"] = _mismatched_dispatch()
        if contradiction == "planner-count":
            manifest["dispatch"]["planner_candidate_count"] = 999_999
        elif contradiction == "final-count":
            manifest["dispatch"]["final_dispatch_count"] = 0
        elif contradiction == "matching-agent-sets":
            manifest["dispatch"]["plan_projections"]["final_plan"].pop(
                "security-reviewer"
            )
        elif contradiction == "unsupported-status":
            manifest["dispatch"]["plan_projections"]["final_plan"][
                "security-reviewer"
            ] = "DISPATCHED"
        else:
            manifest["dispatch"].pop("plan_projections")

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    def test_agent_set_mismatch_sanitizes_projection_order(self, tmp_path):
        manifest = _manifest()
        manifest["dispatch"] = _mismatched_dispatch()
        manifest["dispatch"].update(
            {
                "planner_candidate_count": 1,
                "final_dispatch_count": 1,
                "plan_projections": {
                    "planner_baseline": {
                        "z-reviewer": "SKIPPED_TRIAGE",
                        "a-reviewer": "DISPATCH",
                    },
                    "final_plan": {
                        "m-reviewer": "SKIPPED_OVERRIDE",
                        "a-reviewer": "DISPATCH_OVERRIDE",
                    },
                },
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"]["plan_projections"] == {
            "planner_baseline": {
                "a-reviewer": "DISPATCH",
                "z-reviewer": "SKIPPED_TRIAGE",
            },
            "final_plan": {
                "a-reviewer": "DISPATCH_OVERRIDE",
                "m-reviewer": "SKIPPED_OVERRIDE",
            },
        }
        assert list(
            measured["dispatch"]["plan_projections"]["planner_baseline"]
        ) == ["a-reviewer", "z-reviewer"]
        assert list(measured["dispatch"]["plan_projections"]["final_plan"]) == [
            "a-reviewer",
            "m-reviewer",
        ]
        assert measured["metric_availability"]["dispatch"] == "partial"

    def test_agent_set_mismatch_accepts_one_empty_identity_set(self, tmp_path):
        manifest = _manifest()
        manifest["dispatch"] = _mismatched_dispatch()
        manifest["dispatch"].update(
            {
                "planner_candidate_count": 0,
                "final_dispatch_count": 0,
                "plan_projections": {
                    "planner_baseline": {},
                    "final_plan": {
                        "security-reviewer": "SKIPPED_TRIAGE"
                    },
                },
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] == manifest["dispatch"]
        assert measured["metric_availability"]["dispatch"] == "partial"

    @pytest.mark.parametrize(
        "invalid_name",
        [
            # `type(name) is not str`, then the producer's agent-name
            # regex (its shape is owned by `dispatch_status.py`).
            pytest.param(7, id="integer"),
            pytest.param("private identity prose", id="prose"),
        ],
    )
    def test_agent_set_mismatch_rejects_unsafe_projection_identity(
        self, tmp_path, invalid_name
    ):
        manifest = _manifest()
        manifest["dispatch"] = _mismatched_dispatch()
        projection = manifest["dispatch"]["plan_projections"]["final_plan"]
        projection.pop("security-reviewer")
        projection[invalid_name] = "DISPATCH"

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"
        assert "private identity prose" not in json.dumps(measured)

    @pytest.mark.parametrize(
        "invalid_status",
        [
            # `type(status) is not str`, then the supported-status vocabulary.
            # The non-str row is unhashable: a hashable one (`None`) fails the
            # frozenset membership test anyway, so only an unhashable value
            # needs the type conjunct to be rejected rather than raise.
            pytest.param([], id="list"),
            pytest.param("DISPATCHED", id="unsupported"),
        ],
    )
    def test_agent_set_mismatch_rejects_invalid_projection_status(
        self, tmp_path, invalid_status
    ):
        manifest = _manifest()
        manifest["dispatch"] = _mismatched_dispatch()
        projection = manifest["dispatch"]["plan_projections"]["final_plan"]
        projection["security-reviewer"] = invalid_status
        # The replaced status no longer counts as dispatched; matching the
        # final count keeps the count recomputation from rejecting the row
        # first, so only the status check can.
        manifest["dispatch"]["final_dispatch_count"] = 1

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    @pytest.mark.parametrize(
        "malform",
        [
            pytest.param(
                lambda dispatch: dispatch.__setitem__(
                    "agents",
                    {
                        "security-reviewer": {
                            "initial_status": "DISPATCH",
                            "final_status": "DISPATCH",
                        }
                    },
                ),
                id="nonempty-agents",
            ),
            pytest.param(
                lambda dispatch: dispatch["adjustment_counts"].__setitem__(
                    "added", 1
                ),
                id="nonzero-adjustments",
            ),
            pytest.param(
                lambda dispatch: dispatch["adjustment_counts"].__setitem__(
                    "extra", 0
                ),
                id="extra-adjustment-key",
            ),
            pytest.param(
                lambda dispatch: dispatch["invalid_reason_codes"].append(
                    "extra_reason"
                ),
                id="extra-reason",
            ),
            pytest.param(
                lambda dispatch: dispatch.__setitem__(
                    "duplicate_agent_names", {}
                ),
                id="duplicate-diagnostic",
            ),
            pytest.param(
                lambda dispatch: dispatch.__setitem__(
                    "planner_baseline_available", False
                ),
                id="planner-unavailable",
            ),
        ],
    )
    def test_agent_set_mismatch_requires_exact_mode_metadata(
        self, tmp_path, malform
    ):
        manifest = _manifest()
        manifest["dispatch"] = _mismatched_dispatch()
        malform(manifest["dispatch"])

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    @pytest.mark.parametrize(
        "malform",
        [
            # One per guard: the container is not a dict; its key set is
            # not exactly the two plans; one plan is not a dict. The key-set
            # row adds a key rather than dropping one: a dropped plan reads
            # as `None` and the plan-is-a-dict check would reject it first.
            pytest.param(
                lambda dispatch: dispatch.__setitem__("plan_projections", None),
                id="null-object",
            ),
            pytest.param(
                lambda dispatch: dispatch["plan_projections"].__setitem__(
                    "extra", {}
                ),
                id="extra-key",
            ),
            pytest.param(
                lambda dispatch: dispatch["plan_projections"].__setitem__(
                    "planner_baseline", []
                ),
                id="planner-list",
            ),
        ],
    )
    def test_agent_set_mismatch_requires_exact_projection_shape(
        self, tmp_path, malform
    ):
        manifest = _manifest()
        manifest["dispatch"] = _mismatched_dispatch()
        malform(manifest["dispatch"])

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    # One guard (`"plan_projections" in value and not agent_set_mismatch`).
    # `comparable` is the only mode whose falsifying conjunct is
    # `comparison_available is False`; the others' conjuncts are pinned
    # individually by `test_agent_set_mismatch_requires_exact_mode_metadata`.
    @pytest.mark.parametrize("mode", ["comparable", "unavailable"])
    def test_dispatch_rejects_plan_projections_outside_agent_set_mismatch(
        self, tmp_path, mode
    ):
        if mode == "comparable":
            dispatch = _manifest()["dispatch"]
        else:
            dispatch = _unavailable_dispatch()
        dispatch["plan_projections"] = {
            "planner_baseline": {},
            "final_plan": {"security-reviewer": "SKIPPED_TRIAGE"},
        }
        manifest = _manifest()
        manifest["dispatch"] = dispatch

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    @pytest.mark.parametrize(
        "malform",
        [
            # One per branch of `_dispatch_projection_family_failure`:
            # projections present without the mismatch reason code (only
            # the `"plan_projections" in value` branch can flag it), and the
            # reason code without projections. Each malformation's
            # `dispatch is None` outcome is pinned at measure level by the
            # tests above.
            pytest.param(
                lambda dispatch: dispatch.update(
                    {
                        "comparison_available": True,
                        "invalid_reason_codes": [],
                    }
                ),
                id="out-of-mode-projections",
            ),
            pytest.param(
                lambda dispatch: (
                    dispatch.pop("plan_projections"),
                    dispatch["invalid_reason_codes"].append("extra_reason"),
                ),
                id="mismatch-reason-without-projections",
            ),
        ],
    )
    def test_agent_set_mismatch_invalid_sidecar_is_family_local(
        self, tmp_path, malform
    ):
        manifest = _manifest("sidecar-run")
        manifest["dispatch"] = _mismatched_dispatch()
        manifest["dispatch"]["private_projection_prose"] = (
            "SENSITIVE_RAW_PROJECTION"
        )
        malform(manifest["dispatch"])
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["run"]["id"] == "sidecar-run"
        assert run["dispatch"] is None
        assert run["warnings"] == ["invalid_dispatch_projection"]
        assert run["assignment"] == manifest["assignment"]
        assert run["agents"] == manifest["agents"]
        assert run["outcome"] == manifest["outcome"]
        assert measured["metric_availability"]["dispatch"] == "missing"
        assert "invalid_manifest_fallback" not in run["warnings"]
        assert "SENSITIVE_RAW_PROJECTION" not in json.dumps(run)

    def test_duplicate_dispatch_with_projections_is_family_local(self, tmp_path):
        manifest = _manifest("sidecar-run")
        manifest["dispatch"] = _producer_duplicate_dispatch()
        manifest["dispatch"]["plan_projections"] = {
            "planner_baseline": {
                "SENSITIVE_RAW_PROJECTION": "DISPATCH"
            },
            "final_plan": {"security-reviewer": "DISPATCH"},
        }
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert run["run"]["id"] == "sidecar-run"
        assert run["dispatch"] is None
        assert run["warnings"] == ["invalid_dispatch_projection"]
        assert run["assignment"] == manifest["assignment"]
        assert run["agents"] == manifest["agents"]
        assert run["outcome"] == manifest["outcome"]
        assert measured["metric_availability"]["dispatch"] == "missing"
        assert "SENSITIVE_RAW_PROJECTION" not in json.dumps(run)

    def test_legacy_final_only_sidecar_has_no_projection_warning(self, tmp_path):
        manifest = _manifest("sidecar-run")
        manifest["dispatch"] = _final_only_dispatch()
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "sidecar-run"
        assert run["dispatch"] == manifest["dispatch"]
        assert run["warnings"] == []

    def test_contradictory_dispatch_availability_flags_are_missing(
        self, tmp_path
    ):
        """One `comparison_available != (planner and final)` guard; a
        comparison claimed without a final plan stands for every
        contradictory combination."""
        manifest = _manifest()
        manifest["dispatch"].update(
            {
                "planner_baseline_available": True,
                "final_plan_available": False,
                "comparison_available": True,
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    @pytest.mark.parametrize(
        "contradiction",
        ["agents", "final-count", "adjustments"],
        ids=["agents", "final-count", "adjustments"],
    )
    def test_planner_only_dispatch_rejects_nonproducer_shapes(
        self, tmp_path, contradiction
    ):
        manifest = _manifest()
        manifest["dispatch"] = _planner_only_dispatch()
        if contradiction == "agents":
            manifest["dispatch"]["agents"] = {
                "code-reviewer": {
                    "initial_status": "DISPATCH",
                    "planner_signals": [],
                    "configured_planner_checks": [],
                }
            }
        elif contradiction == "final-count":
            manifest["dispatch"]["final_dispatch_count"] = 1
        else:
            manifest["dispatch"]["adjustment_counts"]["unchanged"] = 1

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    def test_real_planner_only_dispatch_remains_partial(self, tmp_path):
        manifest = _manifest()
        manifest["dispatch"] = _planner_only_dispatch(count=3)

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] == manifest["dispatch"]
        assert measured["metric_availability"]["dispatch"] == "partial"

    @pytest.mark.parametrize(
        "contradiction",
        # A popped `initial_status` is rejected earlier, by the per-agent
        # status loop (`test_dispatch_decisions_require_supported_nonempty_
        # statuses[missing]`), so it cannot reach the final-only branch.
        [
            "changed-status",
            "planner-count",
            "adjustments",
            "change-label",
        ],
        ids=[
            "changed-status",
            "planner-count",
            "adjustments",
            "change-label",
        ],
    )
    def test_final_only_projection_rejects_nonproducer_shapes(
        self, tmp_path, contradiction
    ):
        manifest = _manifest()
        manifest["dispatch"] = _final_only_dispatch()
        decision = manifest["dispatch"]["agents"]["code-reviewer"]
        if contradiction == "changed-status":
            decision["initial_status"] = "SKIPPED_TRIAGE"
            decision["change"] = "added"
        elif contradiction == "planner-count":
            manifest["dispatch"]["planner_candidate_count"] = 0
        elif contradiction == "adjustments":
            manifest["dispatch"]["adjustment_counts"] = {
                "added": 1,
                "removed": 0,
                "unchanged": 0,
            }
        else:
            decision["change"] = "added"

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    def test_real_final_only_legacy_projection_remains_partial(self, tmp_path):
        manifest = _manifest()
        manifest["dispatch"] = _final_only_dispatch()

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] == manifest["dispatch"]
        assert measured["metric_availability"]["dispatch"] == "partial"

    def test_real_empty_final_only_projection_remains_partial(self, tmp_path):
        manifest = _manifest()
        manifest["dispatch"] = _final_only_dispatch()
        manifest["dispatch"].update(
            {
                "planner_candidate_count": 0,
                "final_dispatch_count": 0,
                "adjustment_counts": {"added": 0, "removed": 0, "unchanged": 0},
                "agents": {},
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] == manifest["dispatch"]
        assert measured["metric_availability"]["dispatch"] == "partial"

    @pytest.mark.parametrize(
        "contradiction",
        ["agents", "planner-count", "final-count", "adjustments"],
        ids=["agents", "planner-count", "final-count", "adjustments"],
    )
    def test_unavailable_dispatch_rejects_nonproducer_shapes(
        self, tmp_path, contradiction
    ):
        manifest = _manifest()
        manifest["dispatch"] = _unavailable_dispatch()
        if contradiction == "agents":
            manifest["dispatch"]["agents"] = {
                "code-reviewer": {
                    "planner_signals": [],
                    "configured_planner_checks": [],
                }
            }
        elif contradiction == "planner-count":
            manifest["dispatch"]["planner_candidate_count"] = 1
        elif contradiction == "final-count":
            manifest["dispatch"]["final_dispatch_count"] = 1
        else:
            manifest["dispatch"]["adjustment_counts"]["unchanged"] = 1

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    def test_real_unavailable_dispatch_shape_remains_missing(self, tmp_path):
        manifest = _manifest()
        manifest["dispatch"] = _unavailable_dispatch()

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] == manifest["dispatch"]
        assert measured["metric_availability"]["dispatch"] == "missing"

    def test_distinguishes_zero_adjustments_and_empty_coverage_from_missing(self, tmp_path):
        observed = _manifest()
        observed["dispatch"] = {
            "planner_baseline_available": True,
            "final_plan_available": True,
            "comparison_available": True,
            "planner_candidate_count": 0,
            "final_dispatch_count": 0,
            "adjustment_counts": {"added": 0, "removed": 0, "unchanged": 0},
            "invalid_reason_codes": [],
            "agents": {},
        }
        observed["assignment"] = {
            "changed_files": [],
            "reviewable_files": [],
            "assigned_files_by_agent": {},
            "assigned_files": [],
            "file_exclusions": [],
            "unassigned_reviewable_files": [],
            "reviewed_files_by_agent": {},
            "review_claimable_file_count_by_agent": {},
            "semantics": "generated_scope_not_proof_of_model_read",
        }
        missing = _manifest("missing")
        missing["dispatch"] = None
        missing["assignment"] = None
        missing["availability"]["assignment"] = False

        measured_observed = measure_run(observed, tmp_path, include_transcripts=False)
        measured_missing = measure_run(missing, tmp_path, include_transcripts=False)

        assert measured_observed["metric_availability"]["dispatch"] == "complete"
        assert measured_observed["metric_availability"]["assignment"] == "complete"
        assert measured_observed["assignment"] == observed["assignment"]
        assert measured_missing["metric_availability"]["dispatch"] == "missing"
        assert measured_missing["metric_availability"]["assignment"] == "missing"

    def test_realistic_coverage_ledger_remains_complete(self, tmp_path):
        manifest = _manifest()
        manifest["assignment"] = {
            "changed_files": ["src/a.py", "src/b.py", "vendor/generated.js"],
            "reviewable_files": ["src/a.py", "src/b.py"],
            "assigned_files_by_agent": {
                "code-reviewer": ["src/a.py", "vendor/generated.js"],
                "tests-reviewer": ["src/a.py"],
            },
            "assigned_files": ["src/a.py"],
            "file_exclusions": [
                {"path": "vendor/generated.js", "reason": "noise_filtered"}
            ],
            "unassigned_reviewable_files": ["src/b.py"],
            "reviewed_files_by_agent": {},
            "review_claimable_file_count_by_agent": {},
            "semantics": "generated_scope_not_proof_of_model_read",
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] == manifest["assignment"]
        assert measured["metric_availability"]["assignment"] == "complete"

    def test_prose_dispatch_agent_key_fails_the_dispatch_family_closed(
        self, tmp_path
    ):
        """Manifest agent maps are producer-written kebab identities — a
        malformed sidecar key like a display name with appended prose must
        not become an authoritative agent nor be retained in JSON output."""
        manifest = _manifest()
        agents = manifest["dispatch"]["agents"]
        agents["Security Reviewer | PRIVATE PROSE"] = agents.pop(
            "security-reviewer"
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"
        assert "PRIVATE PROSE" not in json.dumps(measured)

    def test_prose_coverage_agent_key_fails_the_coverage_family_closed(
        self, tmp_path
    ):
        manifest = _manifest()
        by_agent = manifest["assignment"]["assigned_files_by_agent"]
        by_agent["Security Reviewer | PRIVATE PROSE"] = by_agent.pop(
            "code-reviewer"
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"
        assert "PRIVATE PROSE" not in json.dumps(measured)

    def test_duplicate_assigned_path_cannot_report_two_hundred_percent_coverage(
        self, tmp_path
    ):
        manifest = _manifest()
        manifest["assignment"]["assigned_files"] = ["src/a.py", "src/a.py"]

        measured = measure_run(manifest, tmp_path, include_transcripts=False)
        cohort = aggregate_cohort([measured])

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"
        assert cohort["assignment"]["assignment_rate"] is None
        assert cohort["assignment"]["available_runs"] == 0

    @pytest.mark.parametrize(
        "duplicate_location", ["by-agent", "file_exclusions"],
    )
    def test_coverage_set_like_lists_reject_duplicate_paths(
        self, tmp_path, duplicate_location
    ):
        """One row per duplicate guard outside the path-list loop; the
        loop over `_ASSIGNMENT_PATH_LIST_FIELDS` is represented by
        `test_duplicate_assigned_path_cannot_report_two_hundred_percent_coverage`."""
        manifest = _manifest()
        coverage = manifest["assignment"]
        if duplicate_location == "by-agent":
            coverage["assigned_files_by_agent"]["code-reviewer"].append(
                "src/a.py"
            )
        else:
            coverage["file_exclusions"].append(
                {"path": "vendor/generated.js", "reason": "noise_filtered"}
            )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"

    @pytest.mark.parametrize(
        "assigned,uncovered",
        [([], []), (["src/a.py"], ["src/a.py"])],
        ids=["incomplete-partition", "overlapping-partition"],
    )
    def test_assigned_and_uncovered_must_exactly_partition_reviewable(
        self, tmp_path, assigned, uncovered
    ):
        manifest = _manifest()
        manifest["assignment"]["assigned_files"] = assigned
        manifest["assignment"]["unassigned_reviewable_files"] = uncovered
        if not assigned and not uncovered:
            manifest["assignment"]["assigned_files_by_agent"] = {}

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"

    def test_exclusions_must_exactly_equal_changed_minus_reviewable(
        self, tmp_path
    ):
        manifest = _manifest()
        manifest["assignment"]["file_exclusions"] = [
            {"path": "src/a.py", "reason": "noise_filtered"}
        ]

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"

    def test_reviewable_paths_must_be_a_subset_of_changed_paths(self, tmp_path):
        manifest = _manifest()
        manifest["assignment"].update(
            {
                "reviewable_files": ["outside.py"],
                "assigned_files_by_agent": {},
                "assigned_files": [],
                "file_exclusions": [
                    {"path": "src/a.py", "reason": "noise_filtered"},
                    {"path": "vendor/generated.js", "reason": "noise_filtered"},
                ],
                "unassigned_reviewable_files": ["outside.py"],
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"

    def test_by_agent_paths_must_be_a_subset_of_changed_paths(self, tmp_path):
        manifest = _manifest()
        manifest["assignment"].update(
            {
                "assigned_files_by_agent": {
                    "code-reviewer": ["outside.py"]
                },
                "assigned_files": [],
                "unassigned_reviewable_files": ["src/a.py"],
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"

    def test_by_agent_reviewable_union_must_exactly_equal_assigned(self, tmp_path):
        manifest = _manifest()
        manifest["assignment"]["assigned_files_by_agent"] = {}

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"

    @pytest.mark.parametrize(
        "invalid_coverage",
        [
            {
                "changed_files": [],
                "reviewable_files": [],
                "assigned_files_by_agent": {},
                "assigned_files": [],
                "file_exclusions": [],
                "unassigned_reviewable_files": [],
            },
            {
                "changed_files": [None],
                "reviewable_files": [],
                "assigned_files_by_agent": {},
                "assigned_files": [],
                "file_exclusions": [],
                "unassigned_reviewable_files": [],
                "semantics": "generated_scope_not_proof_of_model_read",
            },
            {
                "changed_files": [],
                "reviewable_files": [],
                "assigned_files_by_agent": {"code-reviewer": [False]},
                "assigned_files": [],
                "file_exclusions": [],
                "unassigned_reviewable_files": [],
                "semantics": "generated_scope_not_proof_of_model_read",
            },
            {
                "changed_files": ["vendor/a.js"],
                "reviewable_files": [],
                "assigned_files_by_agent": {},
                "assigned_files": [],
                "file_exclusions": [{"path": "vendor/a.js"}],
                "unassigned_reviewable_files": [],
                "semantics": "generated_scope_not_proof_of_model_read",
            },
            {
                "changed_files": [],
                "reviewable_files": [],
                "assigned_files_by_agent": {},
                "assigned_files": [],
                "file_exclusions": [],
                "unassigned_reviewable_files": [],
                "semantics": "proof_of_model_read",
            },
        ],
        ids=[
            "missing-semantics",
            "malformed-path",
            "malformed-agent-path",
            "malformed-exclusion",
            "wrong-semantics",
        ],
    )
    def test_partial_or_malformed_coverage_is_missing(
        self, tmp_path, invalid_coverage
    ):
        manifest = _manifest()
        manifest["assignment"] = invalid_coverage

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"

    def test_explicit_false_coverage_availability_wins_over_valid_payload(self, tmp_path):
        manifest = _manifest()
        manifest["availability"]["assignment"] = False

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"

    def test_recursively_drops_untrusted_noncanonical_payloads(self, tmp_path):
        manifest = _manifest()
        manifest["prompt"] = "PRIVACY_SENTINEL"
        manifest["run"]["tool_body"] = "PRIVACY_SENTINEL"
        manifest["outcome"]["findings"] = {"description": "PRIVACY_SENTINEL"}
        manifest["assignment"]["arbitrary"] = ["PRIVACY_SENTINEL"]
        manifest["agents"]["started"].append(
            {"agent": "code-reviewer", "prompt": "PRIVACY_SENTINEL"}
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert "PRIVACY_SENTINEL" not in _flatten_strings(measured)

    def test_invalid_manifest_numerics_are_omitted_and_never_drive_wall_time(
        self, tmp_path
    ):
        manifest = _manifest(started_at="bad", ended_at=None)
        manifest["steps"] = [
            {
                "event": "step",
                "step": True,
                "duration_since_prev_ms": float("inf"),
                "title": "Dispatch Plan",
            }
        ]
        manifest["agents"] = {
            "started": [
                {
                    "agent": "code-reviewer",
                    "budget_target": 10**1_000,
                    "scope": {"files": float("nan"), "lines": -1, "paths": []},
                }
            ],
            "completed": [
                {
                    "agent": "code-reviewer",
                    "duration_ms": -1,
                    "finding_count": True,
                    "severities": {"high": float("inf"), "low": 1},
                }
            ],
            "incomplete": [],
        }
        manifest["outcome"]["summary"].update(
            {
                "total_duration_ms": 10**1_000,
                "total_agent_findings": float("inf"),
                "final_finding_count": float("nan"),
                "changed_files_count": True,
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["wall_time_ms"] is None
        assert measured["metric_availability"]["raw_findings"] == "missing"
        assert measured["metric_availability"]["final_findings"] == "missing"
        assert "total_duration_ms" not in measured["outcome"]["summary"]
        assert "total_agent_findings" not in measured["outcome"]["summary"]
        assert "final_finding_count" not in measured["outcome"]["summary"]
        assert "changed_files_count" not in measured["outcome"]["summary"]
        assert "step" not in measured["steps"][0]
        assert "duration_since_prev_ms" not in measured["steps"][0]
        assert "budget_target" not in measured["agents"]["started"][0]
        assert measured["agents"]["started"][0]["scope"] == {"paths": []}
        completed = measured["agents"]["completed"][0]
        assert "duration_ms" not in completed
        assert "finding_count" not in completed
        assert completed["severities"] == {"low": 1}

        strict = json.loads(
            format_json([measured], aggregate_cohort([measured])),
            parse_constant=lambda value: (_ for _ in ()).throw(
                AssertionError(f"nonstandard constant: {value}")
            ),
        )
        assert strict["runs"][0]["wall_time_ms"] is None

    def test_fractional_manifest_counts_are_missing_not_truncated(self, tmp_path):
        manifest = _manifest(started_at="bad", ended_at=None)
        manifest["steps"] = [
            {
                "event": "step",
                "step": 5.9,
                "duration_since_prev_ms": 0.9,
                "title": "Dispatch Plan",
            }
        ]
        manifest["agents"]["completed"] = [
            {
                "agent": "code-reviewer",
                "duration_ms": 0.9,
                "finding_count": 1.9,
            }
        ]
        manifest["outcome"]["summary"].update(
            {
                "total_duration_ms": 0.9,
                "total_agent_findings": 0.9,
                "final_finding_count": 1.9,
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["wall_time_ms"] is None
        assert measured["metric_availability"]["raw_findings"] == "missing"
        assert measured["metric_availability"]["final_findings"] == "missing"
        assert "step" not in measured["steps"][0]
        assert "duration_since_prev_ms" not in measured["steps"][0]
        assert "duration_ms" not in measured["agents"]["completed"][0]
        assert "finding_count" not in measured["agents"]["completed"][0]
        for name in ("total_duration_ms", "total_agent_findings", "final_finding_count"):
            assert name not in measured["outcome"]["summary"]


class TestReviewedFilesRows:
    """Review-claim metrics carry the two conserved derived populations."""

    def test_measured_populations_and_denominator_pass_through(self, tmp_path):
        manifest = _manifest()
        manifest["assignment"]["reviewed_files_by_agent"] = {
            "code-reviewer": {"reviewed_file_claim_count": 2, "unclaimed_review_file_count": 1},
        }
        manifest["assignment"]["review_claimable_file_count_by_agent"] = {"code-reviewer": 3}

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"]["reviewed_files_by_agent"] == {
            "code-reviewer": {"reviewed_file_claim_count": 2, "unclaimed_review_file_count": 1},
        }
        assert measured["metric_availability"]["assignment"] == "complete"

    def test_count_conservation_mismatch_fails_coverage_closed(self, tmp_path):
        manifest = _manifest()
        manifest["assignment"]["reviewed_files_by_agent"] = {
            "code-reviewer": {"reviewed_file_claim_count": 1, "unclaimed_review_file_count": 1},
        }
        manifest["assignment"]["review_claimable_file_count_by_agent"] = {"code-reviewer": 3}

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"

    def test_missing_denominator_fails_coverage_closed(self, tmp_path):
        manifest = _manifest()
        manifest["assignment"]["reviewed_files_by_agent"] = {
            "code-reviewer": {"reviewed_file_claim_count": 1, "unclaimed_review_file_count": 1},
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None
        assert measured["metric_availability"]["assignment"] == "missing"

    @pytest.mark.parametrize(
        "counts",
        [
            pytest.param(
                {"reviewed_file_claim_count": -1, "unclaimed_review_file_count": 1},
                id="negative",
            ),
            # Any key set other than exactly `_REVIEWED_FILES_FIELDS` (a
            # missing key, an extra key, the retired three-way shape) fails
            # the one `set(counts) != _REVIEWED_FILES_FIELDS` conjunct. The
            # counts here are valid and conserve the denominator, so that
            # conjunct is the only guard that can reject this row (a
            # missing key would also read as `None` and be caught by the
            # non-negative check instead).
            pytest.param(
                {"reviewed_file_claim_count": 1, "unclaimed_review_file_count": 1, "extra": 0},
                id="extra-key",
            ),
        ],
    )
    def test_malformed_population_row_fails_closed(self, tmp_path, counts):
        manifest = _manifest()
        manifest["assignment"]["reviewed_files_by_agent"] = {
            "code-reviewer": counts
        }
        manifest["assignment"]["review_claimable_file_count_by_agent"] = {"code-reviewer": 2}

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["assignment"] is None

    def test_cohort_aggregates_both_populations(self):
        measured_a = _measured_manifest_with_reviewed_files({
            "code-reviewer": {"reviewed_file_claim_count": 2, "unclaimed_review_file_count": 1},
        }, review_claimable_file_count_by_agent={"code-reviewer": 3})
        measured_b = _measured_manifest_with_reviewed_files(
            {"code-reviewer": {"reviewed_file_claim_count": 1, "unclaimed_review_file_count": 2}},
            run_id="run-2",
            review_claimable_file_count_by_agent={"code-reviewer": 3},
        )

        cohort = aggregate_cohort([measured_a, measured_b])

        assert cohort["reviewed_files"]["reviewed_file_claim_count"] == 3
        assert cohort["reviewed_files"]["unclaimed_review_file_count"] == 3
        assert cohort["reviewed_files"]["measured_runs"] == 2

    def test_cohort_reports_none_when_no_run_is_measured(self):
        unmeasured = measure_run(
            _manifest(), "/nonexistent", include_transcripts=False
        )

        cohort = aggregate_cohort([unmeasured])

        assert cohort["reviewed_files"]["reviewed_file_claim_count"] is None
        assert cohort["reviewed_files"]["unclaimed_review_file_count"] is None
        assert cohort["reviewed_files"]["measured_runs"] == 0
def _measured_manifest_with_reviewed_files(
    reviewed_files_by_agent: dict,
    *,
    run_id: str = "run-1",
    review_claimable_file_count_by_agent: dict | None = None,
) -> dict:
    manifest = _manifest(run_id)
    manifest["assignment"]["reviewed_files_by_agent"] = (
        reviewed_files_by_agent
    )
    if review_claimable_file_count_by_agent is not None:
        manifest["assignment"]["review_claimable_file_count_by_agent"] = (
            review_claimable_file_count_by_agent
        )
    return measure_run(manifest, "/nonexistent", include_transcripts=False)


class TestLifecycleMeasurement:
    def test_valid_empty_native_lifecycle_is_complete_zero(self, tmp_path):
        measured = measure_run(_manifest(), tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "complete"
        assert measured["lifecycle"] == {
            "started_events": 0,
            "completed_events": 0,
            "incomplete_identities": [],
            "incomplete_count": 0,
            "incomplete_by_agent": {},
            "starts_by_agent": {},
            "extra_starts_by_agent": {},
            "retry_overhead": 0,
            "completion_gap": 0,
        }

    def test_running_lifecycle_rejects_missing_unmatched_agent(self, tmp_path):
        manifest = _running_manifest()
        manifest["agents"] = {
            "started": [_agent_start()],
            "completed": [],
            "incomplete": [],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert measured["lifecycle"] is None

    def test_running_lifecycle_accepts_current_unmatched_multiset(
        self, tmp_path
    ):
        manifest = _running_manifest()
        manifest["agents"] = {
            "started": [_agent_start()],
            "completed": [],
            "incomplete": ["code-reviewer"],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "partial"
        assert measured["lifecycle"]["incomplete_identities"] == [
            "code-reviewer"
        ]
        assert measured["lifecycle"]["incomplete_count"] == 1
        assert measured["lifecycle"]["incomplete_by_agent"] == {
            "code-reviewer": 1
        }
        assert measured["lifecycle"]["completion_gap"] == 1

    def test_normal_lifecycle_preserves_events_and_counts_execution_events(
        self, tmp_path
    ):
        manifest = _manifest()
        manifest["agents"] = {
            "started": [_agent_start()],
            "completed": [_agent_complete()],
            "incomplete": [],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "complete"
        assert measured["agents"]["started"] == manifest["agents"]["started"]
        assert measured["agents"]["completed"] == manifest["agents"]["completed"]
        assert measured["lifecycle"] == {
            "started_events": 1,
            "completed_events": 1,
            "incomplete_identities": [],
            "incomplete_count": 0,
            "incomplete_by_agent": {},
            "starts_by_agent": {"code-reviewer": 1},
            "extra_starts_by_agent": {"code-reviewer": 0},
            "retry_overhead": 0,
            "completion_gap": 0,
        }

    def test_retry_events_are_not_name_deduplicated(self, tmp_path):
        manifest = _manifest()
        manifest["agents"] = {
            "started": [
                _agent_start(timestamp="2026-07-19T10:00:10+00:00"),
                _agent_start(timestamp="2026-07-19T10:00:20+00:00"),
            ],
            "completed": [
                _agent_complete(timestamp="2026-07-19T10:00:30+00:00")
            ],
            "incomplete": ["code-reviewer"],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert [
            event["timestamp"] for event in measured["agents"]["started"]
        ] == [
            "2026-07-19T10:00:10+00:00",
            "2026-07-19T10:00:20+00:00",
        ]
        assert measured["lifecycle"]["started_events"] == 2
        assert measured["lifecycle"]["completed_events"] == 1
        assert measured["lifecycle"]["starts_by_agent"] == {"code-reviewer": 2}
        assert measured["lifecycle"]["extra_starts_by_agent"] == {
            "code-reviewer": 1
        }
        assert measured["lifecycle"]["retry_overhead"] == 1
        assert measured["lifecycle"]["incomplete_identities"] == [
            "code-reviewer"
        ]
        assert measured["lifecycle"]["incomplete_count"] == 1
        assert measured["lifecycle"]["incomplete_by_agent"] == {
            "code-reviewer": 1
        }
        assert measured["lifecycle"]["completion_gap"] == 1

    def test_repeated_incomplete_entries_count_unmatched_executions(
        self, tmp_path
    ):
        manifest = _manifest()
        manifest["agents"] = {
            "started": [
                _agent_start(timestamp="2026-07-19T10:00:10+00:00"),
                _agent_start(timestamp="2026-07-19T10:00:11+00:00"),
                _agent_start(timestamp="2026-07-19T10:00:12+00:00"),
            ],
            "completed": [_agent_complete()],
            "incomplete": ["code-reviewer", "code-reviewer"],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "complete"
        assert measured["agents"]["incomplete"] == [
            "code-reviewer",
            "code-reviewer",
        ]
        assert measured["lifecycle"]["incomplete_identities"] == [
            "code-reviewer"
        ]
        assert measured["lifecycle"]["incomplete_count"] == 2
        assert measured["lifecycle"]["incomplete_by_agent"] == {
            "code-reviewer": 2
        }
        assert measured["lifecycle"]["completion_gap"] == 2

    def test_complete_lifecycle_requires_exact_incomplete_execution_counts(
        self, tmp_path
    ):
        """One `Counter(incomplete) != starts - completions` identity; an
        undercounted retry stands for every inexact multiset. The
        running-status variant is pinned by
        `test_running_lifecycle_rejects_missing_unmatched_agent`."""
        incomplete = ["a-reviewer", "b-reviewer"]
        manifest = _manifest()
        manifest["agents"] = {
            "started": [
                _agent_start(
                    "a-reviewer", timestamp="2026-07-19T10:00:10+00:00"
                ),
                _agent_start(
                    "a-reviewer", timestamp="2026-07-19T10:00:11+00:00"
                ),
                _agent_start(
                    "b-reviewer", timestamp="2026-07-19T10:00:12+00:00"
                ),
                _agent_start(
                    "b-reviewer", timestamp="2026-07-19T10:00:13+00:00"
                ),
                _agent_start(
                    "b-reviewer", timestamp="2026-07-19T10:00:14+00:00"
                ),
            ],
            "completed": [
                _agent_complete(
                    "b-reviewer", timestamp="2026-07-19T10:00:20+00:00"
                ),
                _agent_complete(
                    "a-reviewer", timestamp="2026-07-19T10:00:21+00:00"
                ),
            ],
            "incomplete": incomplete,
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["lifecycle"] is None
        assert measured["metric_availability"]["lifecycle"] == "missing"

    def test_retry_completions_pair_with_prior_unmatched_starts(self, tmp_path):
        manifest = _manifest()
        manifest["agents"] = {
            "started": [
                _agent_start(timestamp="2026-07-19T10:00:10+00:00"),
                _agent_start(timestamp="2026-07-19T10:00:20+00:00"),
            ],
            "completed": [
                _agent_complete(timestamp="2026-07-19T10:00:20+00:00"),
                _agent_complete(timestamp="2026-07-19T10:00:30+00:00"),
            ],
            "incomplete": [],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "complete"
        assert measured["lifecycle"]["started_events"] == 2
        assert measured["lifecycle"]["completed_events"] == 2

    def test_parallel_agent_lifecycle_may_regress_globally(self, tmp_path):
        """Ordering is per agent, not global: two agents' retries
        interleave so both lists regress globally while each agent's own
        events stay ordered (a superset of two distinct agents
        regressing)."""
        started = [
            _agent_start(timestamp="2026-07-19T10:00:20+00:00"),
            _agent_start(
                "security-reviewer", timestamp="2026-07-19T10:00:05+00:00"
            ),
            _agent_start(timestamp="2026-07-19T10:00:30+00:00"),
            _agent_start(
                "security-reviewer", timestamp="2026-07-19T10:00:15+00:00"
            ),
        ]
        completed = [
            _agent_complete(timestamp="2026-07-19T10:00:40+00:00"),
            _agent_complete(
                "security-reviewer", timestamp="2026-07-19T10:00:25+00:00"
            ),
            _agent_complete(timestamp="2026-07-19T10:00:50+00:00"),
            _agent_complete(
                "security-reviewer", timestamp="2026-07-19T10:00:35+00:00"
            ),
        ]
        manifest = _manifest()
        manifest["agents"] = {
            "started": started,
            "completed": completed,
            "incomplete": [],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "complete"
        assert measured["lifecycle"]["started_events"] == len(started)
        assert measured["lifecycle"]["completed_events"] == len(completed)

    @pytest.mark.parametrize(
        "start_timestamps,completion_timestamps",
        [
            (
                [
                    "2026-07-19T10:00:20+00:00",
                    "2026-07-19T10:00:10+00:00",
                ],
                [
                    "2026-07-19T10:00:30+00:00",
                    "2026-07-19T10:00:40+00:00",
                ],
            ),
            (
                [
                    "2026-07-19T10:00:10+00:00",
                    "2026-07-19T10:00:20+00:00",
                ],
                [
                    "2026-07-19T10:00:40+00:00",
                    "2026-07-19T10:00:30+00:00",
                ],
            ),
            (
                ["2026-07-19T10:00:20+00:00"],
                ["2026-07-19T10:00:10+00:00"],
            ),
        ],
        # One per guard of `_lifecycle_events_are_causal`: a regressing
        # per-agent start list, a regressing per-agent completion list, and
        # a completion before its matched start (a retry completing before
        # its second start reaches that same guard).
        ids=[
            "same-agent-start-list-regresses",
            "same-agent-completion-list-regresses",
            "completion-precedes-start",
        ],
    )
    def test_temporally_impossible_lifecycle_is_missing(
        self, tmp_path, start_timestamps, completion_timestamps
    ):
        manifest = _manifest()
        manifest["agents"] = {
            "started": [
                _agent_start(timestamp=timestamp) for timestamp in start_timestamps
            ],
            "completed": [
                _agent_complete(timestamp=timestamp)
                for timestamp in completion_timestamps
            ],
            "incomplete": [],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["lifecycle"] is None
        assert measured["metric_availability"]["lifecycle"] == "missing"

    @pytest.mark.parametrize(
        "malform",
        [
            lambda manifest: manifest.pop("agents"),
            # A missing list fails the same `isinstance(..., list)` check.
            lambda manifest: manifest["agents"].__setitem__("completed", {}),
            lambda manifest: manifest["agents"].__setitem__("incomplete", [None]),
        ],
        ids=["missing-agents", "malformed-list", "unsafe-incomplete"],
    )
    def test_missing_or_malformed_agents_are_lifecycle_missing(
        self, tmp_path, malform
    ):
        manifest = _manifest()
        malform(manifest)

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["lifecycle"] is None
        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert measured["metric_availability"]["assignment"] == "complete"

    @pytest.mark.parametrize(
        "family,mutate",
        [
            ("started", lambda event: event.pop("schema")),
            ("started", lambda event: event.__setitem__("event", "agent_complete")),
            ("started", lambda event: event.__setitem__("run_id", "other-run")),
            ("started", lambda event: event.__setitem__("timestamp", "2026-07-19T10:00:10")),
            ("started", lambda event: event.__setitem__("agent", "../private")),
            ("started", lambda event: event["scope"].__setitem__("files", 1.0)),
            ("completed", lambda event: event.__setitem__("finding_count", False)),
            ("completed", lambda event: event["severities"].__setitem__("high", -1)),
        ],
        ids=[
            "missing-schema",
            "wrong-event",
            "wrong-run",
            "naive-timestamp",
            "unsafe-agent",
            "float-start-count",
            "boolean-completion-count",
            "negative-severity",
        ],
    )
    def test_invalid_lifecycle_event_fails_closed(
        self, tmp_path, family, mutate
    ):
        manifest = _manifest()
        manifest["agents"] = {
            "started": [_agent_start()],
            "completed": [_agent_complete()],
            "incomplete": [],
        }
        mutate(manifest["agents"][family][0])

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["lifecycle"] is None
        assert measured["metric_availability"]["lifecycle"] == "missing"

    def test_completion_without_matching_start_fails_closed(self, tmp_path):
        manifest = _manifest()
        manifest["agents"] = {
            "started": [],
            "completed": [_agent_complete()],
            "incomplete": [],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["lifecycle"] is None
        assert measured["metric_availability"]["lifecycle"] == "missing"

    def test_completion_finding_count_must_match_sanitized_severity_sum(
        self, tmp_path
    ):
        completion = _agent_complete()
        completion["finding_count"] = 2
        completion["severities"] = {"high": 1}
        manifest = _manifest()
        manifest["agents"] = {
            "started": [_agent_start()],
            "completed": [completion],
            "incomplete": [],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["lifecycle"] is None
        assert measured["metric_availability"]["lifecycle"] == "missing"

    def test_legacy_reduced_records_do_not_report_measured_zero(self, tmp_path):
        _write_jsonl(tmp_path / "legacy.jsonl", _legacy_events())
        [legacy] = load_runs(tmp_path)

        measured = measure_run(legacy, tmp_path, include_transcripts=False)

        assert measured["lifecycle"] is None
        assert measured["metric_availability"]["lifecycle"] == "missing"


class TestTranscriptFamilyAvailability:
    def test_transcript_parser_loads_adjacent_module_despite_sys_modules(self):
        """In a long-lived process another checkout or version may already
        occupy sys.modules['review_transcript'] — the loader must bypass it
        and read the exact adjacent file, or transcript metrics silently
        run with foreign semantics."""
        foreign = types.ModuleType("review_transcript")
        foreign.enrich_run_transcript = lambda *args, **kwargs: "FOREIGN"
        original = sys.modules.get("review_transcript")
        sys.modules["review_transcript"] = foreign
        try:
            enrich = measure._load_transcript_module()
        finally:
            if original is None:
                sys.modules.pop("review_transcript", None)
            else:
                sys.modules["review_transcript"] = original

        assert enrich is not foreign.enrich_run_transcript
        assert callable(enrich)

    FAMILIES = (
        "usage",
        "orchestrator_usage",
        "agent_usage",
        "model_usage",
        "tool_failures",
        "artifact_writes",
        "scope_comparable_reads",
        "non_scope_comparable_reads",
        "observed_reads",
    )

    def test_complete_empty_payloads_are_authoritative_zero(
        self, monkeypatch, tmp_path
    ):
        measured = _measure_fake_transcript(
            monkeypatch, tmp_path, _complete_empty_transcript()
        )

        assert measured["metric_availability"]["transcript"] == "complete"
        for family in self.FAMILIES:
            assert measured["metric_availability"][family] == "complete"
        cohort = aggregate_cohort([measured])
        assert cohort["usage"]["complete_totals"] == _usage(0)
        assert cohort["orchestrator_usage"]["by_step"] is None
        assert cohort["agent_usage"]["by_agent"] is None
        assert cohort["model_usage"]["by_model"] is None
        assert cohort["tool_failures"]["total"] == 0
        assert cohort["artifact_writes"]["first_builder_attempts"] == 0
        assert cohort["observed_reads"]["out_of_scope_count"] == 0
        assert cohort["observed_reads"]["non_scope_comparable_count"] == 0
        assert cohort["observed_reads"]["non_scope_comparable_by_path"] == {}
        assert cohort["observed_reads"][
            "partial_non_scope_comparable_by_path"
        ] is None
        assert measured["transcript"]["artifact_writes"][
            "first_builder_attempt_succeeded"
        ] is None

    @pytest.mark.parametrize(
        "models,expected_state",
        [
            pytest.param(
                ["claude-opus-5[1m]", "claude-opus-5[1m]"],
                "complete",
                id="every-entry-attributed",
            ),
            pytest.param(
                ["claude-opus-5[1m]", None],
                "partial",
                id="some-entries-attributed",
            ),
            pytest.param([None, None], "missing", id="no-entry-attributed"),
        ],
    )
    def test_model_availability_tracks_dispatched_model_attribution(
        self,
        monkeypatch,
        tmp_path,
        models,
        expected_state,
    ):
        """The gate must certify the field the grouping actually reads.

        `cohort._group_usage` buckets each available agent entry's usage
        under `entry["model"]` — the dispatch envelope's `resolvedModel` —
        so an entry without one lands in the "unknown" bucket and carries
        spend nothing can attribute. This used to certify a conservation
        identity over `usage_by_model` instead, a field the grouping no
        longer reads: a run with no `resolvedModel` anywhere could read
        `complete` while the grouping it vouched for held one "unknown"
        bucket and nothing else.
        """
        transcript = _complete_empty_transcript()
        transcript["usage"] = _usage(7)
        transcript["agent_usage"] = [
            {
                "agent": f"reviewer-{index}",
                "agent_id": f"agent-{index}",
                "model": model,
                "available": True,
                "usage": _usage(2),
                "tool_calls": 3,
            }
            for index, model in enumerate(models)
        ]

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["metric_availability"]["model_usage"] == expected_state
        assert measured["metric_availability"]["usage"] == "complete"
        assert measured["metric_availability"]["agent_usage"] == "complete"

    def test_model_availability_stays_complete_on_an_empty_agent_payload(
        self, monkeypatch, tmp_path
    ):
        """No available agents is an authoritative zero, not an absence.

        Same rule every sibling family follows (`family_state`): a
        complete payload with nothing in it has nothing to attribute, so
        it must not be downgraded for carrying no model.
        """
        measured = _measure_fake_transcript(
            monkeypatch, tmp_path, _complete_empty_transcript()
        )

        assert measured["metric_availability"]["model_usage"] == "complete"

    def test_gate_and_grouping_agree_on_what_counts_as_a_model(self):
        """One predicate, both sides. An entry the gate calls attributed
        while the grouping buckets it as "unknown" is the split that
        `_dispatched_model` exists to make unrepresentable — so the edge
        the two used to spell differently (an empty model string) has to
        land on the same side of both."""
        run = _measured_run(
            "empty-model",
            usage=_usage(100),
            agent_usage=[
                {
                    "agent": "code-reviewer",
                    "available": True,
                    "model": "",
                    "usage": _usage(10),
                }
            ],
        )
        transcript = run["transcript"]

        assert measure._model_usage_availability(
            transcript["completeness"], transcript["agent_usage"]
        ) == "missing"
        run["metric_availability"]["model_usage"] = "partial"
        by_model = aggregate_cohort([run])["model_usage"][
            "partial_observed_by_model"
        ]
        assert set(by_model) == {"unknown"}

    def test_unavailable_agent_entries_do_not_demand_a_model(
        self, monkeypatch, tmp_path
    ):
        """A correlated agent whose transcript is missing contributes no
        usage to the grouping, so it cannot be asked for a model."""
        transcript = _complete_empty_transcript()
        transcript["usage"] = _usage(7)
        transcript["agent_usage"] = [
            {
                "agent": "code-reviewer",
                "agent_id": "agent-1",
                "model": "claude-opus-5[1m]",
                "available": True,
                "usage": _usage(2),
                "tool_calls": 3,
            },
            {
                "agent": "security-reviewer",
                "agent_id": "agent-2",
                "model": None,
                "available": False,
                "usage": None,
                "tool_calls": None,
            },
        ]

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["metric_availability"]["model_usage"] == "complete"

    @pytest.mark.parametrize(
        "incomplete_family",
        ["scope_comparable_reads", "non_scope_comparable_reads"],
        ids=["reviewer-partial", "synthesis-partial"],
    )
    def test_read_family_availability_and_aggregation_are_independent(
        self, monkeypatch, tmp_path, incomplete_family
    ):
        transcript = _complete_empty_transcript()
        scope_complete = incomplete_family != "scope_comparable_reads"
        non_scope_complete = (
            incomplete_family != "non_scope_comparable_reads"
        )
        transcript["completeness"].update(
            {
                "scope_comparable_reads": scope_complete,
                "non_scope_comparable_reads": non_scope_complete,
                "observed_reads": False,
            }
        )
        transcript["observed_reads"] = {
            "schema": 2,
            "all": ["src/reviewer.py"],
            "in_scope": [],
            "out_of_scope": ["src/reviewer.py"],
            "non_scope_comparable": ["src/synthesis.py"],
            "exhaustive": False,
            "scope_comparable_transcript_data_complete": scope_complete,
            "non_scope_comparable_transcript_data_complete": (
                non_scope_complete
            ),
            "transcript_data_complete": False,
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        cohort = aggregate_cohort([measured])

        expected_scope_state = "complete" if scope_complete else "partial"
        expected_non_scope_state = (
            "complete" if non_scope_complete else "partial"
        )
        assert measured["metric_availability"][
            "scope_comparable_reads"
        ] == expected_scope_state
        assert measured["metric_availability"][
            "non_scope_comparable_reads"
        ] == expected_non_scope_state
        assert measured["metric_availability"]["observed_reads"] == "partial"
        assert cohort["observed_reads"]["availability"][
            expected_scope_state
        ] == 1
        assert cohort["observed_reads"][
            "non_scope_comparable_availability"
        ][expected_non_scope_state] == 1
        assert cohort["observed_reads"]["combined_availability"]["partial"] == 1
        assert cohort["observed_reads"]["out_of_scope_count"] == (
            1 if scope_complete else None
        )
        assert cohort["observed_reads"]["non_scope_comparable_count"] == (
            1 if non_scope_complete else None
        )

    def test_complete_builder_artifacts_keep_first_result_and_recovery(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = _builder_artifacts()

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        artifacts = measured["transcript"]["artifact_writes"]
        assert measured["metric_availability"]["artifact_writes"] == "complete"
        assert artifacts["first_builder_attempt_succeeded"] is None
        assert artifacts["by_agent"][0]["first_builder_attempt_succeeded"] is False

    def test_complete_multi_agent_builder_uses_aggregate_recovery_semantics(
        self, monkeypatch, tmp_path
    ):
        """The run-wide `recovered` is `any(recovered)` over the agents, and
        a per-agent first result never becomes a run-wide one. One agent
        recovering stands for the case where none does."""
        by_agent = [
            {
                "agent": "code-reviewer",
                "builder_attempted": True,
                "builder_attempts": 1,
                "builder_successes": 1,
                "builder_failures": 0,
                "first_builder_attempt_succeeded": True,
                "recovered": False,
            },
            {
                "agent": "security-reviewer",
                "builder_attempted": True,
                "builder_attempts": 2,
                "builder_successes": 1,
                "builder_failures": 1,
                "first_builder_attempt_succeeded": False,
                "recovered": True,
            },
        ]
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = {
            "available": True,
            "complete": True,
            "builder_attempted": True,
            "builder_attempts": sum(
                item["builder_attempts"] for item in by_agent
            ),
            "builder_successes": sum(
                item["builder_successes"] for item in by_agent
            ),
            "builder_failures": sum(
                item["builder_failures"] for item in by_agent
            ),
            "recovered": any(item["recovered"] for item in by_agent),
            "by_agent": by_agent,
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        artifacts = measured["transcript"]["artifact_writes"]
        assert measured["metric_availability"]["artifact_writes"] == "complete"
        assert artifacts["first_builder_attempt_succeeded"] is None
        assert artifacts["recovered"] is any(
            item["recovered"] for item in by_agent
        )

    def test_by_agent_dispatch_order_cannot_invent_a_global_first_result(
        self, monkeypatch, tmp_path
    ):
        by_agent = [
            {
                "agent": "code-reviewer",
                "builder_attempted": True,
                "builder_attempts": 1,
                "builder_successes": 1,
                "builder_failures": 0,
                "first_builder_attempt_succeeded": True,
                "recovered": False,
            },
            {
                "agent": "security-reviewer",
                "builder_attempted": True,
                "builder_attempts": 2,
                "builder_successes": 1,
                "builder_failures": 1,
                "first_builder_attempt_succeeded": False,
                "recovered": True,
            },
        ]
        measured_by_order = []
        for order in (by_agent, list(reversed(by_agent))):
            transcript = _complete_empty_transcript()
            transcript["artifact_writes"] = {
                "available": True,
                "complete": True,
                "builder_attempted": True,
                "builder_attempts": 3,
                "builder_successes": 2,
                "builder_failures": 1,
                "recovered": True,
                "by_agent": order,
            }
            measured_by_order.append(
                _measure_fake_transcript(monkeypatch, tmp_path, transcript)
            )

        artifacts_by_order = [
            measured["transcript"]["artifact_writes"]
            for measured in measured_by_order
        ]
        assert [
            artifacts["first_builder_attempt_succeeded"]
            for artifacts in artifacts_by_order
        ] == [None, None]
        assert [
            item["agent"] for item in artifacts_by_order[0]["by_agent"]
        ] == ["code-reviewer", "security-reviewer"]
        assert [
            item["agent"] for item in artifacts_by_order[1]["by_agent"]
        ] == ["security-reviewer", "code-reviewer"]
        aggregates = [
            aggregate_cohort([measured])["artifact_writes"]
            for measured in measured_by_order
        ]
        for name in (
            "first_builder_attempts",
            "first_builder_successes",
            "first_builder_failures",
            "recoveries",
            "no_builder_attempts",
        ):
            assert aggregates[0][name] == aggregates[1][name]

    def test_explicit_top_only_first_result_is_retained(self, monkeypatch, tmp_path):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = {
            "available": True,
            "complete": True,
            "builder_attempted": True,
            "builder_attempts": 1,
            "builder_successes": 1,
            "builder_failures": 0,
            "first_builder_attempt_succeeded": True,
            "recovered": False,
            "by_agent": [],
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        artifacts = measured["transcript"]["artifact_writes"]
        assert measured["metric_availability"]["artifact_writes"] == "complete"
        assert artifacts["first_builder_attempt_succeeded"] is True

    @pytest.mark.parametrize(
        "payload,expected_agent_counts,expected_run_counts",
        [
            (
                _empty_artifacts(),
                (0, 0, 0, 0, 0),
                (0, 1, 0, 0, 0),
            ),
            (
                {
                    "available": True,
                    "complete": True,
                    "builder_attempted": True,
                    "builder_attempts": 1,
                    "builder_successes": 1,
                    "builder_failures": 0,
                    "recovered": False,
                    "by_agent": [
                        {
                            "agent": "code-reviewer",
                            "builder_attempted": True,
                            "builder_attempts": 1,
                            "builder_successes": 1,
                            "builder_failures": 0,
                            "first_builder_attempt_succeeded": True,
                            "recovered": False,
                        }
                    ],
                },
                (1, 1, 0, 0, 0),
                (1, 0, 0, 0, 0),
            ),
            (
                {
                    "available": True,
                    "complete": True,
                    "builder_attempted": False,
                    "builder_attempts": 0,
                    "builder_successes": 0,
                    "builder_failures": 0,
                    "recovered": False,
                    "by_agent": [
                        {
                            "agent": "code-reviewer",
                            "builder_attempted": False,
                            "builder_attempts": 0,
                            "builder_successes": 0,
                            "builder_failures": 0,
                            "first_builder_attempt_succeeded": None,
                            "recovered": False,
                        }
                    ],
                },
                (0, 0, 0, 0, 1),
                (0, 1, 0, 0, 0),
            ),
            (
                {
                    "available": True,
                    "complete": True,
                    "builder_attempted": True,
                    "builder_attempts": 2,
                    "builder_successes": 1,
                    "builder_failures": 1,
                    "recovered": True,
                    "by_agent": [
                        {
                            "agent": "security-reviewer",
                            "builder_attempted": True,
                            "builder_attempts": 2,
                            "builder_successes": 1,
                            "builder_failures": 1,
                            "first_builder_attempt_succeeded": False,
                            "recovered": True,
                        },
                        {
                            "agent": "code-reviewer",
                            "builder_attempted": False,
                            "builder_attempts": 0,
                            "builder_successes": 0,
                            "builder_failures": 0,
                            "first_builder_attempt_succeeded": None,
                            "recovered": False,
                        },
                    ],
                },
                (1, 0, 1, 1, 1),
                (1, 0, 0, 0, 1),
            ),
            (
                {
                    "available": True,
                    "complete": True,
                    "builder_attempted": True,
                    "builder_attempts": 1,
                    "builder_successes": 1,
                    "builder_failures": 0,
                    "first_builder_attempt_succeeded": True,
                    "recovered": False,
                    "by_agent": [],
                },
                (0, 0, 0, 0, 0),
                (1, 0, 1, 0, 0),
            ),
            (
                {
                    "available": True,
                    "complete": True,
                    "builder_attempted": True,
                    "builder_attempts": 1,
                    "builder_successes": 0,
                    "builder_failures": 1,
                    "first_builder_attempt_succeeded": False,
                    "recovered": False,
                    "by_agent": [],
                },
                (0, 0, 0, 0, 0),
                (1, 0, 0, 1, 0),
            ),
        ],
        ids=[
            "zero-agent-run",
            "single-attempted-agent",
            "single-nonattempting-agent",
            "multi-agent-mixed-attempts",
            "top-only-success",
            "top-only-failure",
        ],
    )
    def test_complete_builder_aggregate_separates_agent_and_run_units(
        self,
        monkeypatch,
        tmp_path,
        payload,
        expected_agent_counts,
        expected_run_counts,
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = payload

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        aggregate = aggregate_cohort([measured])["artifact_writes"]

        assert measured["metric_availability"]["artifact_writes"] == "complete"
        assert tuple(
            aggregate[name]
            for name in (
                "first_builder_attempts",
                "first_builder_successes",
                "first_builder_failures",
                "recoveries",
                "no_builder_attempts",
            )
        ) == expected_agent_counts
        assert tuple(
            aggregate[name]
            for name in (
                "runs_with_builder_attempts",
                "runs_without_builder_attempts",
                "top_only_runs_with_first_builder_success",
                "top_only_runs_with_first_builder_failure",
                "runs_with_builder_recovery",
            )
        ) == expected_run_counts

    @pytest.mark.parametrize(
        "payload,expected_agent_counts,expected_run_counts",
        [
            (
                {
                    "available": True,
                    "complete": True,
                    "builder_attempted": True,
                    "builder_attempts": 2,
                    "builder_successes": 1,
                    "builder_failures": 0,
                    "recovered": False,
                    "by_agent": [
                        {
                            "agent": "code-reviewer",
                            "builder_attempted": True,
                            "builder_attempts": 2,
                            "builder_successes": 1,
                            "builder_failures": 0,
                            "first_builder_attempt_succeeded": True,
                            "recovered": False,
                        },
                        {
                            "agent": "security-reviewer",
                            "builder_attempted": False,
                            "builder_attempts": 0,
                            "builder_successes": 0,
                            "builder_failures": 0,
                            "first_builder_attempt_succeeded": None,
                            "recovered": False,
                        },
                    ],
                },
                (1, 1, 0, 0, 1, 0, 1),
                (1, 0, 0, 0, 0, 0, 0, 0),
            ),
            (
                {
                    "available": True,
                    "complete": True,
                    "builder_attempted": True,
                    "builder_attempts": 1,
                    "builder_successes": 0,
                    "builder_failures": 0,
                    "first_builder_attempt_succeeded": None,
                    "recovered": False,
                    "by_agent": [],
                },
                (0, 0, 0, 0, 0, 0, 0),
                (1, 0, 0, 0, 1, 0, 1, 0),
            ),
            (
                {
                    "available": True,
                    "complete": True,
                    "builder_attempted": True,
                    "builder_attempts": 4,
                    "builder_successes": 2,
                    "builder_failures": 1,
                    "first_builder_attempt_succeeded": True,
                    "recovered": True,
                    "by_agent": [],
                },
                (0, 0, 0, 0, 0, 0, 0),
                (1, 0, 0, 1, 0, 1, 1, 0),
            ),
            (
                {
                    "available": True,
                    "complete": True,
                    "builder_attempted": True,
                    "builder_attempts": 2,
                    "builder_successes": 0,
                    "builder_failures": 1,
                    "first_builder_attempt_succeeded": False,
                    "recovered": False,
                    "by_agent": [],
                },
                (0, 0, 0, 0, 0, 0, 0),
                (1, 0, 0, 0, 0, 0, 1, 1),
            ),
            (
                {
                    "available": True,
                    "complete": False,
                    "builder_attempted": None,
                    "builder_attempts": 0,
                    "builder_successes": 0,
                    "builder_failures": 0,
                    "recovered": False,
                    "by_agent": [
                        {
                            "agent": "code-reviewer",
                            "builder_attempted": False,
                            "builder_attempts": 0,
                            "builder_successes": 0,
                            "builder_failures": 0,
                            "first_builder_attempt_succeeded": None,
                            "recovered": False,
                        }
                    ],
                },
                (0, 0, 0, 0, 1, 0, 0),
                (0, 0, 1, 0, 0, 0, 0, 0),
            ),
            (
                {
                    "available": True,
                    "complete": False,
                    "builder_attempted": False,
                    "builder_attempts": 0,
                    "builder_successes": 0,
                    "builder_failures": 0,
                    "recovered": False,
                    "by_agent": [],
                },
                (0, 0, 0, 0, 0, 0, 0),
                (0, 1, 0, 0, 0, 0, 0, 0),
            ),
        ],
        ids=[
            "mixed-agent-partial",
            "top-only-unknown-first",
            "top-only-first-success-recovery-later-unknown",
            "top-only-first-failure-later-unknown",
            "partial-unknown-run-attempt-state",
            "top-only-run-without-attempt",
        ],
    )
    def test_partial_builder_aggregate_separates_agent_and_run_units(
        self,
        monkeypatch,
        tmp_path,
        payload,
        expected_agent_counts,
        expected_run_counts,
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = payload
        transcript["completeness"]["artifact_writes"] = payload["complete"]

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        aggregate = aggregate_cohort([measured])["artifact_writes"]

        assert measured["metric_availability"]["artifact_writes"] == "partial"
        assert tuple(
            aggregate[name]
            for name in (
                "partial_observed_first_builder_attempts",
                "partial_observed_first_builder_successes",
                "partial_observed_first_builder_failures",
                "partial_observed_unknown_first_results",
                "partial_observed_no_builder_attempts",
                "partial_observed_recoveries",
                "partial_observed_unclassified_builder_results",
            )
        ) == expected_agent_counts
        assert tuple(
            aggregate[name]
            for name in (
                "partial_observed_runs_with_builder_attempts",
                "partial_observed_runs_without_builder_attempts",
                "partial_observed_runs_with_unknown_builder_attempt_state",
                "partial_observed_top_only_runs_with_first_builder_success",
                "partial_observed_top_only_runs_with_unknown_first_builder_result",
                "partial_observed_runs_with_builder_recovery",
                "partial_observed_top_only_unclassified_builder_results",
                "partial_observed_top_only_runs_with_first_builder_failure",
            )
        ) == expected_run_counts

    def test_malformed_builder_summary_contributes_no_agent_or_run_units(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = _builder_artifacts()
        transcript["artifact_writes"]["builder_attempts"] = 3

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        aggregate = aggregate_cohort([measured])["artifact_writes"]

        assert measured["metric_availability"]["artifact_writes"] == "missing"
        for name in (
            "first_builder_attempts",
            "no_builder_attempts",
            "runs_with_builder_attempts",
            "runs_without_builder_attempts",
            "partial_observed_first_builder_attempts",
            "partial_observed_runs_with_builder_attempts",
        ):
            assert aggregate[name] is None

    def test_multi_agent_unknown_first_with_later_recovery_is_partial_evidence(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = {
            "available": True,
            "complete": True,
            "builder_attempted": True,
            "builder_attempts": 3,
            "builder_successes": 1,
            "builder_failures": 1,
            "recovered": True,
            "by_agent": [
                {
                    "agent": "code-reviewer",
                    "builder_attempted": True,
                    "builder_attempts": 1,
                    "builder_successes": 0,
                    "builder_failures": 0,
                    "first_builder_attempt_succeeded": None,
                    "recovered": False,
                },
                {
                    "agent": "security-reviewer",
                    "builder_attempted": True,
                    "builder_attempts": 2,
                    "builder_successes": 1,
                    "builder_failures": 1,
                    "first_builder_attempt_succeeded": False,
                    "recovered": True,
                },
            ],
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        cohort = aggregate_cohort([measured])

        artifacts = measured["transcript"]["artifact_writes"]
        assert measured["metric_availability"]["artifact_writes"] == "partial"
        assert artifacts["complete"] is False
        assert artifacts["first_builder_attempt_succeeded"] is None
        assert cohort["artifact_writes"]["first_builder_attempts"] is None
        assert (
            cohort["artifact_writes"]["partial_observed_first_builder_attempts"]
            == 2
        )
        assert (
            cohort["artifact_writes"]["partial_observed_unknown_first_results"]
            == 1
        )
        assert cohort["artifact_writes"]["partial_observed_recoveries"] == 1

    # At the top level every contradiction but a float count fails one
    # "top level equals the by-agent sums" check (the top-level first result
    # is discarded before any first-result rule runs), so the arithmetic case
    # stands for false-attempt, first-result and recovery there.
    @pytest.mark.parametrize(
        "target,contradiction",
        [
            pytest.param("top-level", "arithmetic", id="top-level-arithmetic"),
            pytest.param("top-level", "float-count", id="top-level-float-count"),
            *(
                pytest.param("agent", name, id=f"agent-{name}")
                for name in (
                    "arithmetic",
                    "false-attempt",
                    "first-result",
                    "recovery",
                    "float-count",
                )
            ),
        ],
    )
    def test_inconsistent_complete_builder_artifacts_are_missing(
        self, monkeypatch, tmp_path, target, contradiction
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = _builder_artifacts()
        artifacts = transcript["artifact_writes"]
        item = artifacts if target == "top-level" else artifacts["by_agent"][0]
        if contradiction == "arithmetic":
            item["builder_attempts"] = 3
        elif contradiction == "false-attempt":
            item["builder_attempted"] = False
        elif contradiction == "first-result":
            item.update(
                {
                    "builder_successes": 0,
                    "builder_failures": 2,
                    "first_builder_attempt_succeeded": True,
                    "recovered": False,
                }
            )
        elif contradiction == "recovery":
            item["recovered"] = False
        else:
            item["builder_attempts"] = 2.0

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["artifact_writes"] is None
        assert measured["metric_availability"]["artifact_writes"] == "missing"

    def test_observed_attempt_with_unknown_first_result_is_retained_as_partial(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = _builder_artifacts()
        for item in (
            transcript["artifact_writes"],
            transcript["artifact_writes"]["by_agent"][0],
        ):
            item.update(
                {
                    "builder_attempts": 1,
                    "builder_successes": 0,
                    "builder_failures": 0,
                    "first_builder_attempt_succeeded": None,
                    "recovered": False,
                }
            )

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        cohort = aggregate_cohort([measured])

        artifacts = measured["transcript"]["artifact_writes"]
        assert measured["metric_availability"]["artifact_writes"] == "partial"
        assert artifacts["complete"] is False
        assert artifacts["by_agent"][0]["builder_attempted"] is True
        assert artifacts["by_agent"][0]["first_builder_attempt_succeeded"] is None
        assert cohort["artifact_writes"]["first_builder_attempts"] is None
        assert (
            cohort["artifact_writes"]["partial_observed_first_builder_attempts"]
            == 1
        )
        assert (
            cohort["artifact_writes"]["partial_observed_unknown_first_results"]
            == 1
        )

    def test_known_first_with_later_unknown_result_is_retained_as_partial(
        self, monkeypatch, tmp_path
    ):
        """A known first result followed by an unknown one. The cohort's
        success and failure counters take `int(first)` and `int(not first)`
        on one path, so a first success stands for a first failure."""
        first, successes, failures = True, 1, 0
        partial_successes, partial_failures = 1, 0
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = {
            "available": True,
            "complete": True,
            "builder_attempted": True,
            "builder_attempts": 2,
            "builder_successes": successes,
            "builder_failures": failures,
            "recovered": False,
            "by_agent": [
                {
                    "agent": "code-reviewer",
                    "builder_attempted": True,
                    "builder_attempts": 2,
                    "builder_successes": successes,
                    "builder_failures": failures,
                    "first_builder_attempt_succeeded": first,
                    "recovered": False,
                }
            ],
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        cohort = aggregate_cohort([measured])["artifact_writes"]

        artifacts = measured["transcript"]["artifact_writes"]
        assert measured["metric_availability"]["artifact_writes"] == "partial"
        assert artifacts["complete"] is False
        assert artifacts["by_agent"][0]["first_builder_attempt_succeeded"] is first
        assert cohort["first_builder_attempts"] is None
        assert cohort["partial_observed_first_builder_attempts"] == 1
        assert cohort["partial_observed_first_builder_successes"] == partial_successes
        assert cohort["partial_observed_first_builder_failures"] == partial_failures
        assert cohort["partial_observed_unknown_first_results"] == 0
        assert cohort["partial_observed_unclassified_builder_results"] == 1

    def test_complete_by_agent_attempt_requires_boolean_first_result(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = {
            "available": True,
            "complete": True,
            "builder_attempted": True,
            "builder_attempts": 1,
            "builder_successes": 1,
            "builder_failures": 0,
            "recovered": False,
            "by_agent": [
                {
                    "agent": "code-reviewer",
                    "builder_attempted": True,
                    "builder_attempts": 1,
                    "builder_successes": 1,
                    "builder_failures": 0,
                    "first_builder_attempt_succeeded": None,
                    "recovered": False,
                }
            ],
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        cohort = aggregate_cohort([measured])["artifact_writes"]

        assert measured["metric_availability"]["artifact_writes"] == "partial"
        assert measured["transcript"]["artifact_writes"]["complete"] is False
        assert cohort["partial_observed_unknown_first_results"] == 1
        assert cohort["partial_observed_unclassified_builder_results"] == 0

    def test_first_success_can_recover_from_a_later_failure(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = {
            "available": True,
            "complete": True,
            "builder_attempted": True,
            "builder_attempts": 3,
            "builder_successes": 2,
            "builder_failures": 1,
            "recovered": True,
            "by_agent": [
                {
                    "agent": "code-reviewer",
                    "builder_attempted": True,
                    "builder_attempts": 3,
                    "builder_successes": 2,
                    "builder_failures": 1,
                    "first_builder_attempt_succeeded": True,
                    "recovered": True,
                }
            ],
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        cohort = aggregate_cohort([measured])["artifact_writes"]

        assert measured["metric_availability"]["artifact_writes"] == "complete"
        assert measured["transcript"]["artifact_writes"]["by_agent"][0][
            "recovered"
        ] is True
        assert cohort["recoveries"] == 1

    @pytest.mark.parametrize(
        "successes,failures,first",
        [(1, 0, True), (0, 1, False)],
        ids=["no-failure", "no-success"],
    )
    def test_recovery_requires_success_and_failure_evidence(
        self, monkeypatch, tmp_path, successes, failures, first
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = {
            "available": True,
            "complete": True,
            "builder_attempted": True,
            "builder_attempts": 1,
            "builder_successes": successes,
            "builder_failures": failures,
            "recovered": True,
            "by_agent": [
                {
                    "agent": "code-reviewer",
                    "builder_attempted": True,
                    "builder_attempts": 1,
                    "builder_successes": successes,
                    "builder_failures": failures,
                    "first_builder_attempt_succeeded": first,
                    "recovered": True,
                }
            ],
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["artifact_writes"] is None
        assert measured["metric_availability"]["artifact_writes"] == "missing"

    @pytest.mark.parametrize(
        "family,flag,payload_key,observed",
        [
            ("usage", "usage", "usage", _usage(1)),
            (
                "orchestrator_usage",
                "orchestrator_data",
                "orchestrator_usage_by_step",
                {"5": _usage(1)},
            ),
            (
                "agent_usage",
                "agent_data",
                "agent_usage",
                [
                    {
                        "agent": "code-reviewer",
                        "agent_id": "agent-1",
                        "model": "claude-sonnet-4-5",
                        "available": True,
                        "usage": _usage(1),
                        "tool_calls": 1,
                    }
                ],
            ),
            (
                "model_usage",
                "agent_data",
                "agent_usage",
                [
                    {
                        "agent": "code-reviewer",
                        "agent_id": "agent-1",
                        "model": "claude-sonnet-4-5",
                        "available": True,
                        "usage": _usage(1),
                        "tool_calls": 1,
                    }
                ],
            ),
            (
                "tool_failures",
                "tool_failures",
                "tool_failures",
                [
                    {
                        "actor": "code-reviewer",
                        "category": "write_requires_read",
                        "detector": "text_signature",
                        "tool": "Write",
                        "operation_class": "builder_output_attempt",
                        "normalized_target": "opaque:1234",
                        "recovered": True,
                        "recovery": "later_success",
                    }
                ],
            ),
            (
                "artifact_writes",
                "artifact_writes",
                "artifact_writes",
                {
                    "available": True,
                    "complete": False,
                    "builder_attempted": True,
                    "builder_attempts": 2,
                    "builder_successes": 1,
                    "builder_failures": 1,
                    "recovered": True,
                    "by_agent": [
                        {
                            "agent": "code-reviewer",
                            "builder_attempted": True,
                            "builder_attempts": 2,
                            "builder_successes": 1,
                            "builder_failures": 1,
                            "first_builder_attempt_succeeded": False,
                            "recovered": True,
                        }
                    ],
                },
            ),
            (
                "observed_reads",
                "observed_reads",
                "observed_reads",
                {
                    "schema": 2,
                    "all": ["src/context.py"],
                    "in_scope": [],
                    "out_of_scope": ["src/context.py"],
                    "non_scope_comparable": ["src/synthesis.py"],
                    "exhaustive": False,
                    "scope_comparable_transcript_data_complete": False,
                    "non_scope_comparable_transcript_data_complete": False,
                    "transcript_data_complete": False,
                },
            ),
        ],
        ids=[
            "usage",
            "orchestrator",
            "agent",
            "model",
            "failures",
            "artifacts",
            "reads",
        ],
    )
    def test_incomplete_nonempty_payload_is_partial(
        self, monkeypatch, tmp_path, family, flag, payload_key, observed
    ):
        transcript = _complete_empty_transcript()
        transcript["completeness"][flag] = False
        if family == "observed_reads":
            transcript["completeness"].update(
                {
                    "scope_comparable_reads": False,
                    "non_scope_comparable_reads": False,
                }
            )
        transcript[payload_key] = observed

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["metric_availability"][family] == "partial"

    @pytest.mark.parametrize(
        "family,flag,payload_key",
        [
            # Every family but model_usage resolves in `family_state`'s one
            # `payload is not None` conjunct; model_usage takes
            # `_model_usage_availability`.
            ("usage", "usage", "usage"),
            ("model_usage", "agent_data", "agent_usage"),
        ],
    )
    def test_incomplete_absent_payload_is_missing(
        self, monkeypatch, tmp_path, family, flag, payload_key
    ):
        transcript = _complete_empty_transcript()
        transcript["completeness"][flag] = False
        transcript[payload_key] = None

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["metric_availability"][family] == "missing"

    def test_duplicate_observed_read_paths_reject_the_family_and_aggregate(
        self, monkeypatch, tmp_path
    ):
        """One `len(paths) != len(set(paths))` check in the bucket loop of
        `measure._sanitize_reads`; the "all" bucket stands for the others.
        The payload is otherwise valid (`_empty_reads` supplies the schema
        and completeness flags), so only the duplicate check can reject it."""
        transcript = _complete_empty_transcript()
        transcript["observed_reads"].update(
            {
                "all": ["src/context.py", "src/context.py"],
                "in_scope": ["src/context.py"],
            }
        )

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        cohort = aggregate_cohort([measured])

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["observed_reads"] == "missing"
        assert cohort["observed_reads"]["out_of_scope_count"] is None
        assert cohort["observed_reads"]["by_path"] is None
        assert cohort["observed_reads"]["availability"]["complete"] == 0

    def test_non_scope_comparable_reads_require_a_privacy_safe_list(
        self, monkeypatch, tmp_path
    ):
        """A missing bucket fails `_strict_repo_read_paths`'s list check (a
        non-list fails it too). An unsafe entry is swept per condition by
        `test_observed_read_paths_require_canonical_repo_relative_form`,
        through the same call in the same bucket loop."""
        transcript = _complete_empty_transcript()
        transcript["observed_reads"].pop("non_scope_comparable")

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["observed_reads"] == "missing"

    @pytest.mark.parametrize(
        "invalid_version",
        [
            # A boolean and the legacy version. A missing or future version
            # fails the same checks; only a float `2.0` would reach the
            # `type(schema) is not int` conjunct without also failing
            # `!= _OBSERVED_READS_SCHEMA`.
            pytest.param(True, id="boolean"),
            pytest.param(1, id="legacy-v1"),
        ],
    )
    def test_observed_reads_require_exact_v2_schema_and_never_zero_fill_legacy(
        self, monkeypatch, tmp_path, invalid_version
    ):
        transcript = _complete_empty_transcript()
        transcript["observed_reads"]["schema"] = invalid_version

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        cohort = aggregate_cohort([measured])

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["scope_comparable_reads"] == "missing"
        assert (
            measured["metric_availability"]["non_scope_comparable_reads"]
            == "missing"
        )
        assert measured["metric_availability"]["observed_reads"] == "missing"
        assert cohort["observed_reads"]["out_of_scope_count"] is None
        assert cohort["observed_reads"]["non_scope_comparable_count"] is None
        assert cohort["observed_reads"]["availability"]["missing"] == 1
        assert cohort["observed_reads"][
            "non_scope_comparable_availability"
        ]["missing"] == 1

    # One row per condition of `sanitize._safe_repo_read_path`, the predicate
    # every observed read passes. `TestNonCanonicalPathsFailClosed` pins that
    # the coverage and lifecycle call sites apply it too.
    @pytest.mark.parametrize(
        "bad_path",
        [
            # `_safe_string` rejects an empty string before the predicate.
            pytest.param("", id="empty"),
            pytest.param("/etc/passwd", id="posix-absolute"),
            # One segment check covers "..", "." and the empty segment of a
            # doubled slash.
            pytest.param("../secret.py", id="parent-prefix"),
            # One backslash check covers drive, UNC and separator shapes.
            pytest.param(r"src\file.py", id="backslash-separator"),
            # The only shape the Windows-drive guard alone rejects: a
            # forward-slash drive path has no backslash, no empty segment and
            # no control character, so deleting _WINDOWS_DRIVE_RE leaves every
            # other param green.
            pytest.param("C:/secret.py", id="windows-drive-forward-slash"),
            # _safe_string deliberately admits \n and \t as legitimate prose
            # whitespace, so a path carrying one reaches _safe_repo_read_path
            # intact and only its Cc/Cf check rejects it (one check for both
            # categories).
            pytest.param("src/two\nlines.py", id="embedded-newline"),
        ],
    )
    # Every read bucket passes the same `_strict_repo_read_paths` call in
    # `measure._sanitize_reads`, so the "all" bucket stands for the others.
    def test_observed_read_paths_require_canonical_repo_relative_form(
        self, monkeypatch, tmp_path, bad_path
    ):
        transcript = _complete_empty_transcript()
        reads = transcript["observed_reads"]
        reads["all"] = [bad_path]
        reads["in_scope"] = [bad_path]

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["scope_comparable_reads"] == "missing"
        assert (
            measured["metric_availability"]["non_scope_comparable_reads"]
            == "missing"
        )
        if bad_path:
            assert bad_path not in json.dumps(measured)

    def test_observed_read_paths_preserve_normalized_unicode_and_spaces(
        self, monkeypatch, tmp_path
    ):
        safe_path = "src/caf\N{LATIN SMALL LETTER E WITH ACUTE} au lait.py"
        transcript = _complete_empty_transcript()
        transcript["observed_reads"].update(
            {
                "all": [safe_path],
                "in_scope": [safe_path],
            }
        )

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["observed_reads"]["all"] == [safe_path]

    @pytest.mark.parametrize(
        "all_paths,in_scope,out_of_scope",
        [
            # `not in_scope.isdisjoint(out_of_scope)`; the union is exact.
            (
                ["src/a.py", "src/b.py"],
                ["src/a.py"],
                ["src/a.py", "src/b.py"],
            ),
            # `in_scope | out_of_scope != set(all)` (an extra member fails
            # the same check).
            (["src/a.py", "src/b.py"], ["src/a.py"], []),
        ],
        ids=["overlap", "missing-member"],
    )
    def test_observed_read_partition_must_be_disjoint_and_exact(
        self, monkeypatch, tmp_path, all_paths, in_scope, out_of_scope
    ):
        """The payload is otherwise valid (`_empty_reads` supplies the schema
        and completeness flags), so only the partition checks can reject
        it."""
        transcript = _complete_empty_transcript()
        transcript["observed_reads"].update(
            {
                "all": all_paths,
                "in_scope": in_scope,
                "out_of_scope": out_of_scope,
            }
        )

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["observed_reads"] == "missing"

    def test_observed_reads_require_explicit_false_exhaustive(
        self, monkeypatch, tmp_path
    ):
        """One `exhaustive is not False` identity check: `True` stands for a
        missing key or the string "false"."""
        transcript = _complete_empty_transcript()
        transcript["observed_reads"]["exhaustive"] = True

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["observed_reads"] == "missing"

    def test_observed_reads_require_boolean_transcript_data_complete(
        self, monkeypatch, tmp_path
    ):
        """`1 == True`, so an integer passes the alignment checks and only
        the `type(...) is not bool` conjunct rejects it. A missing key or a
        string also fails alignment, so neither reaches that conjunct
        alone."""
        transcript = _complete_empty_transcript()
        transcript["observed_reads"]["transcript_data_complete"] = 1

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["observed_reads"] == "missing"

    @pytest.mark.parametrize(
        "family_complete,payload_complete,expected_state",
        [
            (True, True, "complete"),
            (False, False, "partial"),
            # A payload flag that disagrees with the family flag fails the
            # alignment checks in either direction.
            (True, False, "missing"),
        ],
        ids=[
            "complete-aligned",
            "partial-aligned",
            "family-true-payload-false",
        ],
    )
    def test_observed_reads_completeness_signals_must_align(
        self,
        monkeypatch,
        tmp_path,
        family_complete,
        payload_complete,
        expected_state,
    ):
        transcript = _complete_empty_transcript()
        transcript["completeness"].update(
            {
                "scope_comparable_reads": family_complete,
                "non_scope_comparable_reads": family_complete,
                "observed_reads": family_complete,
            }
        )
        transcript["observed_reads"] = {
            "schema": 2,
            "all": ["src/context.py"],
            "in_scope": [],
            "out_of_scope": ["src/context.py"],
            "non_scope_comparable": ["src/synthesis.py"],
            "exhaustive": False,
            "scope_comparable_transcript_data_complete": payload_complete,
            "non_scope_comparable_transcript_data_complete": payload_complete,
            "transcript_data_complete": payload_complete,
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["metric_availability"]["observed_reads"] == expected_state
        if expected_state == "missing":
            assert measured["transcript"]["observed_reads"] is None
        else:
            assert measured["transcript"]["observed_reads"] == transcript[
                "observed_reads"
            ]

    @pytest.mark.parametrize(
        "complete,expected_state",
        [(True, "complete"), (False, "missing")],
        ids=["complete-empty", "partial-empty"],
    )
    def test_valid_empty_observed_read_sets_are_preserved(
        self, monkeypatch, tmp_path, complete, expected_state
    ):
        transcript = _complete_empty_transcript()
        transcript["completeness"].update(
            {
                "scope_comparable_reads": complete,
                "non_scope_comparable_reads": complete,
                "observed_reads": complete,
            }
        )
        transcript["observed_reads"] = _empty_reads(complete=complete)

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["observed_reads"] == transcript[
            "observed_reads"
        ]
        assert measured["metric_availability"]["observed_reads"] == expected_state

    def test_transcript_missing_and_disabled_apply_to_every_family(
        self, monkeypatch, tmp_path
    ):
        unavailable = _complete_empty_transcript()
        unavailable.update({"available": False, "reason": "missing_session_id"})
        measured_missing = _measure_fake_transcript(
            monkeypatch, tmp_path, unavailable
        )
        measured_disabled = measure_run(
            _manifest(), tmp_path, include_transcripts=False
        )

        for family in ("transcript", *self.FAMILIES):
            assert measured_missing["metric_availability"][family] == "missing"
            assert measured_disabled["metric_availability"][family] == "disabled"

    @pytest.mark.parametrize(
        # A non-finite float (inf stands for nan), a fraction, and an
        # integer past the bound.
        "invalid", [float("inf"), 0.9, 10**1_000]
    )
    def test_invalid_transcript_numerics_are_unavailable_and_strict_json_safe(
        self, monkeypatch, tmp_path, invalid
    ):
        transcript = _complete_empty_transcript()
        transcript["usage"]["output_tokens"] = invalid
        transcript["orchestrator_usage_by_step"] = {"5": _usage(1)}
        transcript["orchestrator_usage_by_step"]["5"]["input_tokens"] = invalid
        transcript["agent_usage"] = [
            {
                "agent": "code-reviewer",
                "agent_id": "agent-1",
                "model": "claude-sonnet-4-5",
                "available": True,
                "usage": {**_usage(1), "cache_read_input_tokens": invalid},
            }
        ]
        transcript["artifact_writes"]["builder_attempts"] = invalid

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        for family in (
            "usage",
            "orchestrator_usage",
            "agent_usage",
            "model_usage",
            "artifact_writes",
        ):
            assert measured["metric_availability"][family] == "missing"
        json.loads(
            format_json([measured], aggregate_cohort([measured])),
            parse_constant=lambda value: (_ for _ in ()).throw(
                AssertionError(f"nonstandard constant: {value}")
            ),
        )


def _measured_run(
    run_id: str,
    *,
    transcript_state: str = "complete",
    usage: dict | None = None,
    completeness: dict | None = None,
    artifacts: dict | None = None,
    failures: list[dict] | None = None,
    reads: dict | None = None,
    agent_usage: list[dict] | None = None,
    orchestrator: dict | None = None,
) -> dict:
    run = measure_run(_manifest(run_id), Path("/nonexistent"), include_transcripts=False)
    complete = transcript_state == "complete"
    partial = transcript_state == "partial"
    available = complete or partial
    default_completeness = {
        "orchestrator_data": complete,
        "agent_data": complete,
        "usage": complete,
        "tool_failures": complete,
        "artifact_writes": complete,
        "scope_comparable_reads": complete,
        "non_scope_comparable_reads": complete,
        "observed_reads": complete,
    }
    if completeness:
        if "observed_reads" in completeness:
            default_completeness["scope_comparable_reads"] = completeness.get(
                "scope_comparable_reads", completeness["observed_reads"]
            )
            default_completeness[
                "non_scope_comparable_reads"
            ] = completeness.get(
                "non_scope_comparable_reads", completeness["observed_reads"]
            )
        default_completeness.update(completeness)
    run["transcript"] = {
        "available": available,
        "reason": None if available else "session_not_found_or_ambiguous",
        "warnings": [],
        "completeness": default_completeness,
        "usage": usage if available else None,
        "orchestrator_usage_by_step": orchestrator if available else None,
        "agent_usage": agent_usage if available else None,
        "tool_failures": failures if available else None,
        "artifact_writes": artifacts if available else None,
        "observed_reads": reads if available else None,
    }
    run["metric_availability"].update(
        {
            "transcript": transcript_state,
            "orchestrator_usage": (
                "complete"
                if default_completeness["orchestrator_data"]
                else "partial" if available else "missing"
            ),
            "agent_usage": (
                "complete"
                if default_completeness["agent_data"]
                else "partial" if available else "missing"
            ),
            "model_usage": (
                "complete"
                if default_completeness["agent_data"]
                else "partial" if available else "missing"
            ),
            "usage": (
                "complete"
                if default_completeness["usage"]
                else "partial" if available else "missing"
            ),
            "tool_failures": (
                "complete"
                if default_completeness["tool_failures"]
                else "partial" if available else "missing"
            ),
            "artifact_writes": (
                "complete"
                if default_completeness["artifact_writes"]
                else "partial" if available else "missing"
            ),
            "scope_comparable_reads": (
                "complete"
                if default_completeness["scope_comparable_reads"]
                else "partial" if available else "missing"
            ),
            "non_scope_comparable_reads": (
                "complete"
                if default_completeness["non_scope_comparable_reads"]
                else "partial" if available else "missing"
            ),
            "observed_reads": (
                "complete"
                if default_completeness["observed_reads"]
                else "partial" if available else "missing"
            ),
        }
    )
    return run


class TestBudgetUtilization:
    """Per-agent tool_calls / budget_target, plus the run's own median/range.

    budget_target comes from the manifest's projected agent-start
    lifecycle (agent_start's existing `budget_target` telemetry key —
    Task 4 already carries it there; this is the metrics layer's first
    consumer). tool_calls comes from the transcript enrichment's
    per-agent usage rows, which already counts them. Combining the two
    is new; neither fact needed a new transport.
    """

    def _manifest_with_agents(self, *agents: tuple[str, int]) -> dict:
        manifest = _manifest(session_id="session-1")
        manifest["agents"]["started"] = [
            {"agent": name, "budget_target": target}
            for name, target in agents
        ]
        return manifest

    def _agent_usage_entry(
        self, agent: str, tool_calls: int, *, available: bool = True
    ) -> dict:
        return {
            "agent": agent,
            "agent_id": f"{agent}-id",
            "model": "claude-sonnet-4-5",
            "available": available,
            "usage": _usage(0) if available else None,
            "usage_by_model": {} if available else None,
            "tool_calls": tool_calls if available else None,
        }

    def _measure(self, monkeypatch, tmp_path, manifest, agent_usage):
        registry = tmp_path / "registry.json"
        registry.write_text(json.dumps({"agents": {"code-reviewer": {}}}))
        transcript = _complete_empty_transcript()
        transcript["agent_usage"] = agent_usage

        def enrich(_manifest, _sessions_root, _recognized_agents):
            return copy.deepcopy(transcript)

        monkeypatch.setattr(measure, "_load_transcript_module", lambda: enrich)
        return measure_run(manifest, tmp_path, registry_path=registry)

    def test_reports_per_agent_utilization_and_run_level_median_range(
        self, monkeypatch, tmp_path
    ):
        """The brief's own worked example: three agents landing at
        12%/40%/78% report a median of 40 with a 12-78 range."""
        manifest = self._manifest_with_agents(
            ("a-reviewer", 20), ("b-reviewer", 50), ("c-reviewer", 50)
        )
        agent_usage = [
            self._agent_usage_entry("a-reviewer", 8),
            self._agent_usage_entry("b-reviewer", 6),
            self._agent_usage_entry("c-reviewer", 39),
        ]

        measured = self._measure(monkeypatch, tmp_path, manifest, agent_usage)

        utilization = measured["budget_utilization"]
        by_agent = {
            row["agent"]: row for row in utilization["agents"]
        }
        assert by_agent["a-reviewer"] == {
            "agent": "a-reviewer",
            "tool_calls": 8,
            "budget_target": 20,
            "utilization_pct": 40,
        }
        assert by_agent["b-reviewer"]["utilization_pct"] == 12
        assert by_agent["c-reviewer"]["utilization_pct"] == 78
        assert utilization["median_pct"] == 40
        assert utilization["min_pct"] == 12
        assert utilization["max_pct"] == 78
        assert utilization["sample_count"] == 3

    def test_agent_missing_budget_target_is_excluded_not_zeroed(
        self, monkeypatch, tmp_path
    ):
        """An agent with tool_calls but no known budget_target (e.g. a
        pre-Task-4 manifest, or a lifecycle event that failed to
        sanitize) must not be silently reported as 0% utilized."""
        manifest = self._manifest_with_agents(("a-reviewer", 20))
        agent_usage = [
            self._agent_usage_entry("a-reviewer", 8),
            self._agent_usage_entry("untracked-reviewer", 5),
        ]

        measured = self._measure(monkeypatch, tmp_path, manifest, agent_usage)

        utilization = measured["budget_utilization"]
        assert [row["agent"] for row in utilization["agents"]] == ["a-reviewer"]
        assert utilization["sample_count"] == 1

    def test_unavailable_agent_transcript_is_excluded(
        self, monkeypatch, tmp_path
    ):
        """`available: False` means tool_calls is None (missing evidence,
        not a measured zero) — it must not enter the denominator."""
        manifest = self._manifest_with_agents(("a-reviewer", 20))
        agent_usage = [self._agent_usage_entry("a-reviewer", 0, available=False)]

        measured = self._measure(monkeypatch, tmp_path, manifest, agent_usage)

        assert measured["budget_utilization"] is None

    def test_no_measurable_agent_reports_none_not_empty(
        self, monkeypatch, tmp_path
    ):
        """No fabricated zero-agent report: a run this metric can't speak
        to reports None, the same as every other unmeasured family here."""
        manifest = self._manifest_with_agents()

        measured = self._measure(monkeypatch, tmp_path, manifest, [])

        assert measured["budget_utilization"] is None

    def test_disabled_transcripts_report_no_utilization(self, tmp_path):
        """tool_calls lives in the transcript family; with transcripts off
        there is no numerator, so utilization is unmeasured, not missing
        the whole run's other facts."""
        manifest = self._manifest_with_agents(("a-reviewer", 20))

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["budget_utilization"] is None

    def test_first_start_wins_budget_target_for_a_retried_agent(
        self, monkeypatch, tmp_path
    ):
        """A retried agent's budget must not move mid-run: the FIRST
        dispatch's target is the one the reviewer actually saw in its own
        bootstrap prompt."""
        manifest = _manifest(session_id="session-1")
        manifest["agents"]["started"] = [
            {"agent": "a-reviewer", "budget_target": 20},
            {"agent": "a-reviewer", "budget_target": 999},
        ]
        agent_usage = [self._agent_usage_entry("a-reviewer", 8)]

        measured = self._measure(monkeypatch, tmp_path, manifest, agent_usage)

        [row] = measured["budget_utilization"]["agents"]
        assert row["budget_target"] == 20
        assert row["utilization_pct"] == 40

    def test_first_valid_entry_wins_tool_calls_for_a_retried_agent(
        self, monkeypatch, tmp_path
    ):
        """`agent_usage` is keyed per correlated DISPATCH, not per agent
        identity — a retried agent can carry two entries under the same
        registry name (review_transcript.py's own correlation comment:
        "retries remain visible"). Without a dedup on this side of the
        join too, a retried agent would emit two per-agent rows and
        double-weight the run-level median/min/max/sample_count against
        every agent that ran once."""
        manifest = self._manifest_with_agents(("a-reviewer", 20))
        agent_usage = [
            self._agent_usage_entry("a-reviewer", 8),  # first: 40%
            self._agent_usage_entry("a-reviewer", 18),  # retry: 90%
        ]

        measured = self._measure(monkeypatch, tmp_path, manifest, agent_usage)

        utilization = measured["budget_utilization"]
        assert len(utilization["agents"]) == 1
        assert utilization["agents"][0] == {
            "agent": "a-reviewer",
            "tool_calls": 8,
            "budget_target": 20,
            "utilization_pct": 40,
        }
        assert utilization["sample_count"] == 1
        assert utilization["median_pct"] == 40
        assert utilization["min_pct"] == 40
        assert utilization["max_pct"] == 40

    def test_measured_zero_tool_calls_is_included_as_zero_percent(
        self, monkeypatch, tmp_path
    ):
        """`available: True, tool_calls: 0` is a real measurement (the
        agent ran and issued no tool calls) and must be included at 0% —
        distinct from `available: False`, which is missing evidence and
        must be excluded entirely."""
        manifest = self._manifest_with_agents(("a-reviewer", 20))
        agent_usage = [self._agent_usage_entry("a-reviewer", 0)]

        measured = self._measure(monkeypatch, tmp_path, manifest, agent_usage)

        utilization = measured["budget_utilization"]
        assert utilization["agents"] == [{
            "agent": "a-reviewer",
            "tool_calls": 0,
            "budget_target": 20,
            "utilization_pct": 0,
        }]
        assert utilization["sample_count"] == 1


class TestBudgetUtilizationRendering:
    """The JSON per-agent figures and the table's median-range cell must
    never disagree — one computed value, two presentations."""

    def _run(self, utilization: dict | None) -> dict:
        run = copy.deepcopy(_measured_run("run-1"))
        run["budget_utilization"] = utilization
        return run

    def test_table_row_shows_median_and_range(self):
        row = render._table_row(
            self._run(
                {
                    "agents": [],
                    "median_pct": 40,
                    "min_pct": 12,
                    "max_pct": 78,
                    "sample_count": 3,
                }
            )
        )

        assert "median 40% (12–78%)" in row

    def test_table_row_shows_placeholder_when_unmeasured(self):
        row = render._table_row(self._run(None))

        assert "—" in row

    def test_table_attributes_shared_runs_to_their_uploader(self):
        shared = {**self._run(None), "uploaded_by": "alice"}

        table = format_table([shared], {"runs": 1, "transcript_runs": 0})

        assert "Uploader" in table
        assert "alice" in table

    def test_local_tables_carry_no_uploader_column(self):
        table = format_table(
            [self._run(None)], {"runs": 1, "transcript_runs": 0}
        )

        assert "Uploader" not in table


class TestAggregateCohort:
    def test_keeps_complete_partial_and_missing_usage_denominators_separate(self):
        complete = _measured_run("complete", usage=_usage(10))
        partial = _measured_run(
            "partial",
            transcript_state="partial",
            usage=_usage(20),
            completeness={"usage": False},
        )
        missing = _measured_run("missing", transcript_state="missing")

        cohort = aggregate_cohort([complete, partial, missing])

        assert cohort["runs"] == 3
        assert cohort["transcript_runs"] == 2
        assert cohort["usage"]["complete_totals"]["effective_input_tokens"] == 60
        assert cohort["usage"]["partial_observed_totals"]["effective_input_tokens"] == 120
        assert cohort["usage"]["availability"] == {
            "available": 2,
            "complete": 1,
            "partial": 1,
            "missing": 1,
            "disabled": 0,
        }

    def test_aggregates_dispatch_coverage_outcomes_critic_and_wall_time(self):
        unavailable = _measured_run("unavailable", transcript_state="missing")
        unavailable["dispatch"] = None
        unavailable["assignment"] = None
        unavailable["outcome"] = {"summary": {}, "critic_verdict": None}
        unavailable["wall_time_ms"] = None
        unavailable["metric_availability"].update(
            {
                "dispatch": "missing",
                "assignment": "missing",
                "raw_findings": "missing",
                "final_findings": "missing",
                "critic": "missing",
                "wall_time": "missing",
            }
        )

        cohort = aggregate_cohort([_measured_run("available"), unavailable])

        assert cohort["dispatch"]["planner_candidates"] == 2
        assert cohort["dispatch"]["actual_dispatches"] == 1
        assert cohort["dispatch"]["adjustments"] == {
            "added": 0,
            "removed": 1,
            "unchanged": 1,
        }
        assert cohort["dispatch"]["adjustment_rate"] == pytest.approx(0.5)
        assert cohort["dispatch"]["adjustment_rate_semantics"] == (
            "changed_agents_over_compared_union_agents"
        )
        assert cohort["dispatch"]["compared_planner_candidates"] == 2
        assert cohort["dispatch"]["planner_removal_rate"] == pytest.approx(0.5)
        assert cohort["dispatch"]["by_signal"] == {
            "default": {"planned": 1, "removed": 1},
            "keyword": {"planned": 1, "unchanged": 1},
        }
        assert cohort["assignment"]["reviewable_files"] == 1
        assert cohort["assignment"]["assigned_files"] == 1
        assert cohort["assignment"]["unassigned_reviewable_files"] == 0
        assert cohort["outcomes"]["raw_findings"] == 3
        assert cohort["outcomes"]["final_findings"] == 1
        assert cohort["critic"]["verdicts"] == {"STAND": 1}
        assert cohort["wall_time"]["total_ms"] == 60_000
        assert cohort["availability"]["assignment"]["missing"] == 1

    def test_planner_removal_rate_excludes_unchanged_skips_and_uncompared_runs(self):
        compared = _measured_run("compared")
        compared["dispatch"].update(
            {
                "planner_candidate_count": 1,
                "final_dispatch_count": 0,
                "adjustment_counts": {
                    "added": 0,
                    "removed": 1,
                    "unchanged": 4,
                },
            }
        )
        planner_only = _measured_run("planner-only")
        planner_only["dispatch"] = _planner_only_dispatch(9)
        planner_only["metric_availability"]["dispatch"] = "partial"

        dispatch = aggregate_cohort([compared, planner_only])["dispatch"]

        assert dispatch["planner_candidates"] == 10
        assert dispatch["adjustment_rate"] == pytest.approx(0.2)
        assert dispatch["compared_planner_candidates"] == 1
        assert dispatch["planner_removal_rate"] == pytest.approx(1.0)

    def test_planner_removal_rate_distinguishes_empty_comparison_from_missing(self):
        empty_comparison = _measured_run("empty")
        empty_comparison["dispatch"] = {
            "planner_baseline_available": True,
            "final_plan_available": True,
            "comparison_available": True,
            "planner_candidate_count": 0,
            "final_dispatch_count": 0,
            "adjustment_counts": {"added": 0, "removed": 0, "unchanged": 0},
            "invalid_reason_codes": [],
            "agents": {},
        }
        missing = _measured_run("missing")
        missing["dispatch"] = None
        missing["metric_availability"]["dispatch"] = "missing"

        empty = aggregate_cohort([empty_comparison])["dispatch"]
        unavailable = aggregate_cohort([missing])["dispatch"]

        assert empty["compared_planner_candidates"] == 0
        assert empty["planner_removal_rate"] == 0.0
        assert unavailable["compared_planner_candidates"] == 0
        assert unavailable["planner_removal_rate"] is None

    def test_wall_time_statistics_preserve_fractional_milliseconds_in_strict_json(
        self,
    ):
        zero = _measured_run("zero-wall")
        zero["wall_time_ms"] = 0
        one = _measured_run("one-wall")
        one["wall_time_ms"] = 1

        cohort = aggregate_cohort([zero, one])
        payload = json.loads(
            format_json([zero, one], cohort),
            parse_constant=lambda value: (_ for _ in ()).throw(
                AssertionError(f"nonstandard constant: {value}")
            ),
        )

        assert cohort["wall_time"]["total_ms"] == 1
        assert cohort["wall_time"]["mean_ms"] == 0.5
        assert cohort["wall_time"]["median_ms"] == 0.5
        assert payload["aggregate"]["wall_time"]["mean_ms"] == 0.5
        assert payload["aggregate"]["wall_time"]["median_ms"] == 0.5

    def test_integral_wall_time_statistics_remain_integers(self):
        zero = _measured_run("zero-wall")
        zero["wall_time_ms"] = 0
        two = _measured_run("two-wall")
        two["wall_time_ms"] = 2

        wall = aggregate_cohort([zero, two])["wall_time"]

        assert wall["total_ms"] == 2
        assert wall["mean_ms"] == 1
        assert wall["median_ms"] == 1
        assert isinstance(wall["mean_ms"], int)
        assert isinstance(wall["median_ms"], int)

    def test_implausible_wall_time_is_missing_before_cohort_statistics(
        self, tmp_path
    ):
        manifest = _manifest(started_at="bad", ended_at=None)
        manifest["outcome"]["summary"]["total_duration_ms"] = 2**63 - 1

        measured = measure_run(manifest, tmp_path, include_transcripts=False)
        wall = aggregate_cohort([measured])["wall_time"]

        assert measured["wall_time_ms"] is None
        assert measured["metric_availability"]["wall_time"] == "missing"
        assert wall["total_ms"] is None
        assert wall["mean_ms"] is None
        assert wall["median_ms"] is None
        assert wall["availability"]["missing"] == 1

    def test_overbound_timestamp_span_does_not_fall_back_to_summary(self, tmp_path):
        manifest = _manifest(
            started_at="2024-07-19T10:00:00+00:00",
            ended_at="2026-07-19T10:00:00+00:00",
        )
        manifest["outcome"]["summary"]["total_duration_ms"] = 1_234

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["wall_time_ms"] is None
        assert measured["metric_availability"]["wall_time"] == "missing"

    def test_reversed_timestamp_span_does_not_fall_back_to_summary(self, tmp_path):
        manifest = _manifest(
            started_at="2026-07-19T10:01:00+00:00",
            ended_at="2026-07-19T10:00:00+00:00",
        )
        manifest["outcome"]["summary"]["total_duration_ms"] = 1_234

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["wall_time_ms"] is None
        assert measured["metric_availability"]["wall_time"] == "missing"

    def test_aggregates_lifecycle_retries_and_incomplete_identities(self):
        retry_manifest = _manifest("retry-run")
        retry_manifest["agents"] = {
            "started": [
                _agent_start(run_id="retry-run"),
                _agent_start(
                    run_id="retry-run",
                    timestamp="2026-07-19T10:00:11+00:00",
                ),
                _agent_start(
                    run_id="retry-run",
                    timestamp="2026-07-19T10:00:12+00:00",
                ),
            ],
            "completed": [_agent_complete(run_id="retry-run")],
            "incomplete": ["code-reviewer", "code-reviewer"],
        }
        incomplete_manifest = _manifest("incomplete-run")
        incomplete_manifest["agents"] = {
            "started": [
                _agent_start("security-reviewer", run_id="incomplete-run")
            ],
            "completed": [],
            "incomplete": ["security-reviewer"],
        }
        runs = [
            measure_run(
                retry_manifest, Path("/nonexistent"), include_transcripts=False
            ),
            measure_run(
                incomplete_manifest, Path("/nonexistent"), include_transcripts=False
            ),
        ]

        lifecycle = aggregate_cohort(runs)["lifecycle"]

        assert lifecycle["started_events"] == 4
        assert lifecycle["completed_events"] == 1
        assert lifecycle["incomplete_identities"] == [
            "code-reviewer",
            "security-reviewer",
        ]
        assert lifecycle["incomplete_count"] == 3
        assert lifecycle["incomplete_by_agent"] == {
            "code-reviewer": 2,
            "security-reviewer": 1,
        }
        assert lifecycle["starts_by_agent"] == {
            "code-reviewer": 3,
            "security-reviewer": 1,
        }
        assert lifecycle["extra_starts_by_agent"] == {
            "code-reviewer": 2,
            "security-reviewer": 0,
        }
        assert lifecycle["retry_overhead"] == 2
        assert lifecycle["completion_gap"] == 3
        assert lifecycle["availability"] == {
            "available": 2,
            "complete": 2,
            "partial": 0,
            "missing": 0,
            "disabled": 0,
        }

    def test_running_lifecycle_is_observed_without_contaminating_complete_totals(self):
        running = _manifest("running-run", ended_at=None)
        running["status"] = "running"
        running["agents"] = {
            "started": [
                _agent_start(run_id="running-run"),
                _agent_start(
                    run_id="running-run",
                    timestamp="2026-07-19T10:00:11+00:00",
                ),
            ],
            "completed": [],
            "incomplete": ["code-reviewer", "code-reviewer"],
        }

        runs = [
            measure_run(
                _manifest("complete-run"),
                Path("/nonexistent"),
                include_transcripts=False,
            ),
            measure_run(running, Path("/nonexistent"), include_transcripts=False),
        ]

        lifecycle = aggregate_cohort(runs)["lifecycle"]

        assert lifecycle["started_events"] == 0
        assert lifecycle["completed_events"] == 0
        assert lifecycle["partial_observed_runs"] == 1
        assert lifecycle["partial_observed_started_events"] == 2
        assert lifecycle["partial_observed_completed_events"] == 0
        assert lifecycle["partial_observed_incomplete_identities"] == [
            "code-reviewer"
        ]
        assert lifecycle["partial_observed_incomplete_count"] == 2
        assert lifecycle["partial_observed_incomplete_by_agent"] == {
            "code-reviewer": 2
        }
        assert lifecycle["partial_observed_starts_by_agent"] == {
            "code-reviewer": 2
        }
        assert lifecycle["partial_observed_extra_starts_by_agent"] == {
            "code-reviewer": 1
        }
        assert lifecycle["partial_observed_retry_overhead"] == 1
        assert lifecycle["partial_observed_completion_gap"] == 2
        assert lifecycle["availability"] == {
            "available": 2,
            "complete": 1,
            "partial": 1,
            "missing": 0,
            "disabled": 0,
        }

    def test_does_not_double_count_aggregate_usage_when_grouping_agent_and_model(self):
        run = _measured_run(
            "usage",
            usage=_usage(100),
            orchestrator={"5": _usage(10)},
            agent_usage=[
                {
                    "agent": "code-reviewer",
                    "available": True,
                    # The DISPATCHED spelling (`resolvedModel`) is the model
                    # bucket key — it keeps the priced context-window
                    # variant tag the per-message spelling strips.
                    "model": "claude-sonnet-4-5[1m]",
                    "usage": _usage(20),
                }
            ],
        )

        cohort = aggregate_cohort([run])

        assert cohort["usage"]["complete_totals"]["effective_input_tokens"] == 600
        assert cohort["orchestrator_usage"]["by_step"]["5"]["effective_input_tokens"] == 60
        assert cohort["agent_usage"]["by_agent"]["code-reviewer"]["effective_input_tokens"] == 120
        assert cohort["model_usage"]["by_model"] == {
            "claude-sonnet-4-5[1m]": cohort["agent_usage"]["by_agent"]["code-reviewer"],
        }

    def test_model_grouping_merges_agents_sharing_one_dispatched_spelling(self):
        """One priced spelling is one bucket, however many agents ran on it."""
        run = _measured_run(
            "model-buckets",
            usage=_usage(100),
            agent_usage=[
                {
                    "agent": "code-reviewer",
                    "available": True,
                    "model": "claude-opus-5[1m]",
                    "usage": _usage(10),
                },
                {
                    "agent": "security-reviewer",
                    "available": True,
                    "model": "claude-opus-5[1m]",
                    "usage": _usage(20),
                },
            ],
        )

        by_model = aggregate_cohort([run])["model_usage"]["by_model"]

        assert set(by_model) == {"claude-opus-5[1m]"}
        assert by_model["claude-opus-5[1m]"]["effective_input_tokens"] == 180

    def test_model_grouping_keeps_unattributed_spend_in_an_unknown_bucket(self):
        """An entry with no dispatched model must not vanish from spend math.

        Reachable only in the PARTIAL grouping: `_model_usage_availability`
        refuses `complete` for a run any of whose available entries lacks a
        model, precisely so the "complete" totals never carry a bucket
        nothing can attribute.
        """
        run = _measured_run(
            "model-buckets-partial",
            completeness={"agent_data": False},
            usage=_usage(100),
            agent_usage=[
                {
                    "agent": "code-reviewer",
                    "available": True,
                    "model": "claude-opus-5[1m]",
                    "usage": _usage(10),
                },
                {
                    "agent": "performance-reviewer",
                    "available": True,
                    "model": None,
                    "usage": _usage(5),
                },
            ],
        )

        model_usage = aggregate_cohort([run])["model_usage"]

        assert model_usage["by_model"] is None
        by_model = model_usage["partial_observed_by_model"]
        assert set(by_model) == {"claude-opus-5[1m]", "unknown"}
        assert by_model["claude-opus-5[1m]"]["effective_input_tokens"] == 60
        assert by_model["unknown"]["effective_input_tokens"] == 30

    def test_builder_first_attempt_denominator_excludes_incomplete_and_keeps_no_attempt(self):
        complete = _measured_run(
            "complete",
            artifacts={
                "available": True,
                "complete": True,
                "builder_attempted": True,
                "by_agent": [
                    {
                        "agent": "security-reviewer",
                        "builder_attempted": True,
                        "first_builder_attempt_succeeded": False,
                        "recovered": True,
                    },
                    {
                        "agent": "code-reviewer",
                        "builder_attempted": False,
                        "first_builder_attempt_succeeded": None,
                        "recovered": False,
                    },
                ],
            },
        )
        incomplete = _measured_run(
            "partial",
            transcript_state="partial",
            completeness={"artifact_writes": False},
            artifacts={
                "available": True,
                "complete": False,
                "by_agent": [
                    {
                        "agent": "other-reviewer",
                        "builder_attempted": True,
                        "first_builder_attempt_succeeded": False,
                        "recovered": True,
                    }
                ],
            },
        )
        missing = _measured_run("missing", transcript_state="missing")

        cohort = aggregate_cohort([complete, incomplete, missing])

        assert cohort["artifact_writes"]["first_builder_attempts"] == 1
        assert cohort["artifact_writes"]["first_builder_failures"] == 1
        assert cohort["artifact_writes"]["recoveries"] == 1
        assert cohort["artifact_writes"]["no_builder_attempts"] == 1
        assert cohort["artifact_writes"]["partial_observed_runs"] == 1
        assert cohort["artifact_writes"]["partial_observed_first_builder_attempts"] == 1
        assert cohort["artifact_writes"]["partial_observed_first_builder_failures"] == 1
        assert cohort["artifact_writes"]["partial_observed_recoveries"] == 1
        assert cohort["artifact_writes"]["availability"]["partial"] == 1
        assert cohort["artifact_writes"]["availability"]["missing"] == 1

    def test_tool_failures_and_nonexhaustive_reads_use_only_complete_totals(self):
        run = _measured_run(
            "complete",
            failures=[
                {
                    "category": "write_requires_read",
                    "recovered": True,
                    "actor": "code-reviewer",
                }
            ],
            reads={
                "all": ["src/a.py", "src/context.py"],
                "in_scope": ["src/a.py"],
                "out_of_scope": ["src/context.py"],
                "non_scope_comparable": ["src/synthesis.py"],
                "exhaustive": False,
                "transcript_data_complete": True,
            },
        )

        cohort = aggregate_cohort([run])

        assert cohort["tool_failures"]["total"] == 1
        assert cohort["tool_failures"]["recovered"] == 1
        assert cohort["observed_reads"]["out_of_scope_count"] == 1
        assert cohort["observed_reads"]["by_path"] == {"src/context.py": 1}
        assert cohort["observed_reads"]["non_scope_comparable_count"] == 1
        assert cohort["observed_reads"]["non_scope_comparable_by_path"] == {
            "src/synthesis.py": 1
        }
        assert cohort["observed_reads"]["exhaustive"] is False

    def test_non_scope_comparable_reads_do_not_inflate_reviewer_totals(self):
        complete = _measured_run(
            "complete-synthesis",
            reads={
                "all": ["src/reviewer.py"],
                "in_scope": [],
                "out_of_scope": ["src/reviewer.py"],
                "non_scope_comparable": [
                    "src/reconcile.py",
                    "src/shared.py",
                ],
                "exhaustive": False,
                "transcript_data_complete": True,
            },
        )
        partial = _measured_run(
            "partial-synthesis",
            transcript_state="partial",
            completeness={"observed_reads": False},
            reads={
                "all": ["src/partial-reviewer.py"],
                "in_scope": [],
                "out_of_scope": ["src/partial-reviewer.py"],
                "non_scope_comparable": ["src/partial-synthesis.py"],
                "exhaustive": False,
                "transcript_data_complete": False,
            },
        )

        cohort = aggregate_cohort([complete, partial])

        assert cohort["observed_reads"]["out_of_scope_count"] == 1
        assert cohort["observed_reads"]["by_path"] == {"src/reviewer.py": 1}
        assert cohort["observed_reads"]["non_scope_comparable_count"] == 2
        assert cohort["observed_reads"]["non_scope_comparable_by_path"] == {
            "src/reconcile.py": 1,
            "src/shared.py": 1,
        }
        assert cohort["observed_reads"][
            "partial_observed_out_of_scope_count"
        ] == 1
        assert cohort["observed_reads"][
            "partial_observed_non_scope_comparable_count"
        ] == 1
        assert cohort["observed_reads"][
            "partial_non_scope_comparable_by_path"
        ] == {"src/partial-synthesis.py": 1}


class TestFormattingAndCli:
    def test_table_has_required_columns_and_missing_glyphs(self):
        run = _measured_run("missing", transcript_state="missing")
        run["dispatch"] = None
        run["assignment"] = None
        run["outcome"] = {"summary": {}, "critic_verdict": None}
        run["wall_time_ms"] = None
        run["metric_availability"].update(
            {
                "dispatch": "missing",
                "assignment": "missing",
                "raw_findings": "missing",
                "final_findings": "missing",
                "critic": "missing",
                "wall_time": "missing",
            }
        )

        table = format_table([run], aggregate_cohort([run]))

        for label in (
            "Run ID",
            "Version/Mode",
            "Planner→Actual",
            "Adjustments",
            "Assigned/Reviewable/Unassigned",
            "Outcome/Critic",
            "Wall",
            "Eff In/Out",
            "Budget util",
            "Transcript",
        ):
            assert label in table
        assert "—" in table
        assert "n/a" in table

    @pytest.mark.parametrize(
        "state,verdict,expected",
        [
            ("complete", "STAND", "STAND"),
            ("disabled", "unavailable", "n/a"),
            ("missing", "REVISE", "—"),
            ("complete", "PRIVATE FINDING PROSE", "—"),
        ],
        ids=["complete", "disabled", "missing", "invalid-complete"],
    )
    def test_table_critic_cell_honors_family_availability(
        self, state, verdict, expected
    ):
        run = _measured_run("critic-state")
        run["outcome"]["critic_verdict"] = verdict
        run["metric_availability"]["critic"] = state

        table = format_table([run], aggregate_cohort([run]))

        assert f"3→1/{expected}" in table
        if verdict != expected:
            assert verdict not in table

    def test_table_usage_missing_zero_payload_does_not_imply_observed_zero(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["completeness"]["usage"] = False

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        payload = json.loads(
            format_json([measured], aggregate_cohort([measured]))
        )

        assert measured["metric_availability"]["usage"] == "missing"
        lines = format_table([measured], aggregate_cohort([measured])).splitlines()
        headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
        cells = [cell.strip() for cell in lines[2].strip("|").split("|")]
        assert dict(zip(headers, cells))["Eff In/Out"] == "—"
        assert payload["runs"][0]["metric_availability"]["usage"] == "missing"
        assert payload["runs"][0]["transcript"]["usage"] == _usage(0)

    def test_table_usage_disabled_is_not_applicable(self):
        measured = measure_run(
            _manifest(), Path("/nonexistent"), include_transcripts=False
        )

        assert measured["metric_availability"]["usage"] == "disabled"
        assert render._table_row(measured)[9] == "n/a"

    def test_table_usage_complete_zero_is_observed_zero(
        self, monkeypatch, tmp_path
    ):
        measured = _measure_fake_transcript(
            monkeypatch, tmp_path, _complete_empty_transcript()
        )

        assert measured["metric_availability"]["usage"] == "complete"
        assert render._table_row(measured)[9] == "0/0"

    def test_table_usage_partial_observation_is_explicit(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["completeness"]["usage"] = False
        transcript["usage"] = _usage(1)

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["metric_availability"]["usage"] == "partial"
        assert render._table_row(measured)[9] == "partial 6/4"

    def test_the_build_commit_survives_the_sanitizer(self):
        manifest = _manifest("build-identity")
        manifest["run"]["plugin_commit"] = "194489e8"
        run = measure_run(manifest, Path("/nonexistent"), include_transcripts=False)
        assert run["run"]["plugin_commit"] == "194489e8"
        manifest["run"]["plugin_commit"] = "194489e8\x1b[31m"
        run = measure_run(manifest, Path("/nonexistent"), include_transcripts=False)
        assert "plugin_commit" not in run["run"]

    def test_version_column_names_the_build_commit_when_stamped(self):
        run = _measured_run("build-identity")
        run["run"]["plugin_version"] = "1.119.0"
        run["run"]["plugin_commit"] = "194489e8"
        table = format_table([run], aggregate_cohort([run]))
        assert "1.119.0@194489e8/pr" in table
        run["run"]["plugin_commit"] = None
        assert "1.119.0/pr" in format_table([run], aggregate_cohort([run]))

    def test_table_cells_normalize_controls_escape_pipes_and_bound_output(self):
        run = _measured_run("unsafe-table")
        run["run"]["id"] = (
            "safe\n| forged row |\x1b[31mred\x1b[0m" + "x" * 5_000
        )
        run["run"]["plugin_version"] = "1.2\r\x1b[32mgreen\x1b[0m|next"
        run["run"]["mode"] = "pr\tmode"
        run["outcome"]["critic_verdict"] = (
            "STAND|\x1b]0;owned\x07REVISE\nforged"
        )
        run["metric_availability"]["transcript"] = "complete\n| forged row |"

        table = format_table([run], aggregate_cohort([run]))
        lines = table.splitlines()

        assert table == format_table([run], aggregate_cohort([run]))
        assert sum(line.startswith("| ") for line in lines) == 3
        assert "safe \\| forged row \\|red" in table
        assert "\x1b" not in table
        assert "[31m" not in table
        assert "]0;owned" not in table
        assert "\n| forged row |" not in table
        assert max(len(line) for line in lines) < 1_200

    def test_table_cells_keep_pipes_escaped_after_preceding_backslashes(self):
        """One `replace` chain doubles backslashes before it escapes the
        pipe; several backslashes stand for one."""
        backslash_count = 3
        run = _measured_run("backslash-pipe")
        run["run"]["id"] = "safe" + "\\" * backslash_count + "|forged"

        table = format_table([run], aggregate_cohort([run]))

        assert (
            "safe" + "\\" * (backslash_count * 2 + 1) + "|forged"
        ) in table
        assert sum(line.startswith("| ") for line in table.splitlines()) == 3

    def test_json_keeps_structured_values_without_table_escaping(self):
        run = _measured_run("safe\n| value |")

        rendered = format_json([run], aggregate_cohort([run]))
        payload = json.loads(rendered)

        assert payload["runs"][0]["run"]["id"] == run["run"]["id"]
        assert r"\|" not in rendered

    def test_json_has_exact_top_level_and_is_parseable(self):
        runs = [_measured_run("run-json")]
        payload = json.loads(format_json(runs, aggregate_cohort(runs)))

        assert set(payload) == {"schema", "runs", "aggregate"}
        assert payload["schema"] == 5
        assert payload["runs"][0]["uploaded_by"] is None

    def test_json_exposes_lifecycle_and_partial_unknown_builder_evidence(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = {
            "available": True,
            "complete": True,
            "builder_attempted": True,
            "builder_attempts": 1,
            "builder_successes": 1,
            "builder_failures": 0,
            "first_builder_attempt_succeeded": None,
            "recovered": False,
            "by_agent": [],
        }
        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        payload = json.loads(
            format_json([measured], aggregate_cohort([measured]))
        )

        assert payload["runs"][0]["metric_availability"]["lifecycle"] == "complete"
        assert payload["runs"][0]["lifecycle"]["started_events"] == 0
        assert payload["aggregate"]["lifecycle"]["completion_gap"] == 0
        artifacts = payload["aggregate"]["artifact_writes"]
        assert artifacts["partial_observed_unknown_first_results"] == 0
        assert artifacts["partial_observed_runs_with_builder_attempts"] == 1
        assert artifacts[
            "partial_observed_top_only_runs_with_unknown_first_builder_result"
        ] == 1

    def test_json_formatter_rejects_nonfinite_values(self):
        """`format_json`'s one `allow_nan=False` covers the runs and the
        aggregate alike, and every non-finite float."""
        with pytest.raises(ValueError):
            format_json([{"invalid": float("nan")}], aggregate_cohort([]))

    def test_cli_writes_exact_output_and_handles_valid_empty_cohort(self, tmp_path):
        log_dir = tmp_path / "logs"
        output = tmp_path / "nested" / "report.json"

        result = main(
            [
                "--log-dir",
                str(log_dir),
                "--sessions-root",
                str(tmp_path / "sessions"),
                "--format",
                "json",
                "--output",
                str(output),
                "--no-transcripts",
            ]
        )

        assert result == 0
        assert json.loads(output.read_text()) == {
            "schema": 5,
            "runs": [],
            "aggregate": aggregate_cohort([]),
        }
        assert output.read_text() == format_json([], aggregate_cohort([]))

    def test_cli_without_source_flags_reads_default_log_directory(
        self, monkeypatch, capsys, tmp_path
    ):
        """With no source flag the CLI reads `DEFAULT_LOG_DIR`, and a run
        read from a local log directory carries no uploader."""
        default_log_dir = tmp_path / "default-logs"
        _write_manifest(
            default_log_dir / "review.manifest.json", _manifest("default-run")
        )
        monkeypatch.setattr(cli, "DEFAULT_LOG_DIR", default_log_dir)

        result = main(["--format", "json", "--no-transcripts"])

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert [run["run"]["id"] for run in payload["runs"]] == ["default-run"]
        assert payload["runs"][0]["uploaded_by"] is None

    def test_shared_clone_reports_each_user_and_ignores_direct_v1_files(
        self, shared_telemetry_clone, tmp_path
    ):
        output = tmp_path / "shared-report.json"

        result = main(
            [
                "--shared-dir",
                str(shared_telemetry_clone["clone"]),
                "--format",
                "json",
                "--output",
                str(output),
                "--no-transcripts",
            ]
        )

        assert result == 0
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["schema"] == 5
        assert {
            run["run"]["id"]: run["uploaded_by"]
            for run in payload["runs"]
        } == {
            "shared-alice": "alice",
            "shared-bob": "bob",
        }

    def test_shared_clone_transcripts_are_disabled_not_missing(
        self, shared_telemetry_clone, tmp_path, capsys
    ):
        # A bounded selector would enable enrichment on a local log dir,
        # but every shared upload nulls its session id by contract, so no
        # transcript can ever be located: the family is disabled, never
        # reported as missing, and no hint claims a selector could enable it.
        output = tmp_path / "shared-report.json"

        result = main(
            [
                "--shared-dir",
                str(shared_telemetry_clone["clone"]),
                "--sessions-root",
                str(tmp_path / "sessions"),
                "--last",
                "1",
                "--format",
                "json",
                "--output",
                str(output),
            ]
        )

        assert result == 0
        assert capsys.readouterr().err == ""
        runs = json.loads(output.read_text(encoding="utf-8"))["runs"]
        assert runs
        for run in runs:
            assert run["run"]["session_id"] is None
            assert run["transcript"]["reason"] == "disabled"
            assert run["metric_availability"]["transcript"] == "disabled"

    def test_symlinked_layout_root_is_never_followed(
        self, shared_telemetry_clone, tmp_path
    ):
        # A contributor could commit v1 itself as a directory symlink; the
        # root is checked before anything beneath it is enumerated.
        other_clone = tmp_path / "other-clone"
        other_clone.mkdir()
        (other_clone / telemetry_share.LAYOUT_PREFIX).symlink_to(
            shared_telemetry_clone["clone"] / telemetry_share.LAYOUT_PREFIX
        )

        assert load.load_shared_runs(other_clone) == []

    def test_symlinked_running_sibling_is_never_overlaid(self, tmp_path):
        # A regular running manifest beside a committed symlink named like
        # its JSONL: discovery excludes the link, and the lifecycle overlay
        # must open only what discovery admitted — never the name-derived
        # sibling path.
        telemetry_mod = _load_telemetry_module()
        output_dir = tmp_path / "running-output"
        output_dir.mkdir()
        telemetry = telemetry_mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path / "running-logs")
        )
        telemetry.start(mode="pr", repo_path=str(tmp_path), identifier="7",
                        run_id="running-run")
        telemetry.log_agent_start("security-reviewer", domain="security")
        manifest_path = Path(telemetry.manifest_path)
        assert json.loads(manifest_path.read_text())["status"] == "running"

        plain = tmp_path / "plain"
        linked = tmp_path / "linked"
        for directory in (plain, linked):
            directory.mkdir()
            shutil.copy(manifest_path, directory / manifest_path.name)
        (linked / Path(telemetry.log_path).name).symlink_to(telemetry.log_path)

        assert load.load_runs(linked) == load.load_runs(plain)

    def test_one_run_id_across_uploaders_is_counted_once(
        self, shared_telemetry_clone, tmp_path
    ):
        # The same local run re-uploaded from a second GitHub account is one
        # run: identical records collapse to one attributed to the lexically
        # first login; differing records under one id surface as the same
        # conflict record a single directory would yield.
        clone = shared_telemetry_clone["clone"]
        version_dir = clone / telemetry_share.LAYOUT_PREFIX
        alice = version_dir / "alice"
        for name in ("shared-alice.manifest.json", "shared-alice.jsonl"):
            for login in ("carol", "aaron"):
                (version_dir / login).mkdir(exist_ok=True)
                shutil.copy(alice / name, version_dir / login / name)
        altered = json.loads((alice / "shared-alice.manifest.json").read_text())
        altered["outcome"]["summary"]["final_finding_count"] = 99
        (version_dir / "aaron" / "shared-alice.manifest.json").write_text(
            json.dumps(altered)
        )

        shared = load.load_shared_runs(clone)

        by_uploader = {
            (record["run"]["id"], record["status"], uploader)
            for record, uploader in shared
        }
        assert by_uploader == {
            ("shared-bob", "complete", "bob"),
            (load._duplicate_conflict("shared-alice", [])["run"]["id"],
             "duplicate_run_id_conflict", "aaron"),
        }

        # Identical copies alone are one run, attributed to the first login.
        shutil.rmtree(version_dir / "aaron")
        assert sorted(
            (record["run"]["id"], uploader) for record, uploader in load.load_shared_runs(clone)
        ) == [("shared-alice", "alice"), ("shared-bob", "bob")]

    def test_shared_uploads_read_as_manifests_not_legacy_fallback(
        self, shared_telemetry_clone
    ):
        """A redacted upload must measure exactly as its run measures locally.

        A manifest the validator rejects is not dropped — it silently falls
        back to the reduced legacy JSONL reading, which carries only four
        availability families and loses assignment, usage, lifecycle, and
        every other manifest-only metric. Equality with the local reading
        of the same producer run is the manifest-path proof.
        """
        local = {
            user: load.load_runs(log_dir)
            for user, log_dir in shared_telemetry_clone["local_logs"].items()
        }

        shared = load.load_shared_runs(shared_telemetry_clone["clone"])

        assert len(shared) == 2
        for record, uploaded_by in shared:
            [original] = local[uploaded_by]
            assert record["run"]["id"] == original["run"]["id"]
            assert record["availability"] == original["availability"]
            assert record["run"]["session_id"] is None

    def test_cli_reports_exception_type_and_message(
        self, monkeypatch, capsys, tmp_path
    ):
        def fail_to_load(*_args, **_kwargs):
            raise RuntimeError("broken manifest")

        monkeypatch.setattr(cli, "load_runs", fail_to_load)

        result = main(["--log-dir", str(tmp_path), "--no-transcripts"])

        assert result == 1
        assert (
            capsys.readouterr().err
            == "review_run_metrics: unable to produce report: "
            "RuntimeError: broken manifest\n"
        )

    @pytest.mark.parametrize(
        "args",
        [
            # The two branches of `cli._positive_int`. An unknown
            # `--format` and the mutually exclusive source flags are
            # argparse's own checks.
            ["--last", "0"],
            ["--last", "not-an-int"],
        ],
        ids=["last-zero", "last-not-an-int"],
    )
    def test_invalid_cli_arguments_exit_two(self, args):
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == 2


class TestUnboundedCohortTranscriptCost:
    """Transcript enrichment is bounded to explicit queries.

    Enrichment costs a session discovery plus a full transcript parse per run,
    so an unbounded sweep must not silently pay it across all history.
    """

    def test_unbounded_cohort_disables_transcripts(self, capsys):
        args = cli._parser().parse_args(["--log-dir", "/tmp/x"])
        assert cli._resolve_transcripts(args) is False
        assert "transcript enrichment disabled" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "argv",
        [
            ["--log-dir", "/tmp/x", "--last", "5"],
            ["--log-dir", "/tmp/x", "--run-id", "abc"],
        ],
    )
    def test_bounded_queries_keep_transcripts(self, argv, capsys):
        args = cli._parser().parse_args(argv)
        assert cli._resolve_transcripts(args) is True
        assert capsys.readouterr().err == ""

    def test_explicit_opt_out_still_wins(self, capsys):
        args = cli._parser().parse_args(
            ["--log-dir", "/tmp/x", "--last", "5", "--no-transcripts"]
        )
        assert cli._resolve_transcripts(args) is False
        assert capsys.readouterr().err == ""


class TestStructuredSidecarValuesFailClosed:
    """JSON-valid sidecars carrying structured values where scalars are
    expected must be rejected per file — one malformed sidecar can never
    raise and abort the whole cohort."""

    def test_structured_status_falls_back_without_aborting_cohort(
        self, tmp_path
    ):
        bad = _manifest("bad-run")
        bad["status"] = []
        _write_manifest(tmp_path / "bad.manifest.json", bad)
        _write_jsonl(tmp_path / "bad.jsonl", _legacy_events("bad-legacy"))
        _write_manifest(tmp_path / "good.manifest.json", _manifest("good-run"))

        runs = load_runs(tmp_path)

        assert {run["run"]["id"] for run in runs} == {"bad-legacy", "good-run"}
        [fallback] = [run for run in runs if run["run"]["id"] == "bad-legacy"]
        assert "invalid_manifest_fallback" in fallback["warnings"]

    def test_structured_warning_code_is_dropped_not_fatal(self, tmp_path):
        manifest = _manifest("warn-run")
        manifest["warnings"] = [{"code": ["registry_unavailable"]}]
        _write_manifest(tmp_path / "review.manifest.json", manifest)

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "warn-run"
        assert run["warnings"] == []

    def test_structured_critic_verdict_is_dropped_not_fatal(self, tmp_path):
        manifest = _manifest("critic-run")
        manifest["outcome"]["critic_verdict"] = ["STAND"]
        _write_manifest(tmp_path / "review.manifest.json", manifest)

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "critic-run"
        assert "critic_verdict" not in run["outcome"]

    def test_structured_verdict_source_is_dropped_not_fatal(self, tmp_path):
        manifest = _manifest("verdict-source-run")
        manifest["outcome"]["verdict_source"] = ["findings ledger"]
        _write_manifest(tmp_path / "review.manifest.json", manifest)

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "verdict-source-run"
        assert "verdict_source" not in run["outcome"]

    def test_structured_legacy_event_name_is_skipped_not_fatal(self, tmp_path):
        events = _legacy_events("legacy-structured")
        events.append(
            {"event": ["step"], "timestamp": "2026-07-18T10:02:00+00:00"}
        )
        _write_jsonl(tmp_path / "review.jsonl", events)

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-structured"
        assert "legacy_log_no_manifest" in run["warnings"]


class TestNonCanonicalPathsFailClosed:
    """Coverage ledgers and lifecycle scope paths must satisfy the canonical
    repository-relative path contract, so a non-canonical path from a
    malformed or hand-edited sidecar cannot survive into the
    privacy-reduced report (fix d253935d). One bad path per call site pins
    that the site applies the predicate; the predicate's per-condition
    sweep lives in `test_observed_read_paths_require_canonical_repo_relative_form`."""

    BAD_PATH = "/abs/leak.py"

    def test_non_canonical_coverage_path_invalidates_manifest(self, tmp_path):
        """The bad path joins the ledger as a reviewable, unassigned file, so
        the partition stays exact and only the path check on the coverage
        path lists can reject it. Appended to `changed_files` alone, it
        would break the exclusions-equal-changed-minus-reviewable check
        first, whatever its form."""
        manifest = _manifest("cover-run")
        assignment = manifest["assignment"]
        for name in (
            "changed_files",
            "reviewable_files",
            "unassigned_reviewable_files",
        ):
            assignment[name].append(self.BAD_PATH)
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-fallback"
        assert "invalid_manifest_fallback" in run["warnings"]

    def test_non_canonical_excluded_coverage_path_invalidates_manifest(
        self, tmp_path
    ):
        manifest = _manifest("cover-run")
        manifest["assignment"]["changed_files"].append("/abs/noise.js")
        manifest["assignment"]["file_exclusions"].append(
            {"path": "/abs/noise.js", "reason": "noise_filtered"}
        )
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("legacy-fallback"))

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-fallback"
        assert "invalid_manifest_fallback" in run["warnings"]

    def test_non_canonical_lifecycle_scope_path_fails_lifecycle_closed(
        self, tmp_path
    ):
        manifest = _manifest("scope-run")
        start = _agent_start(run_id="scope-run")
        start["scope"]["paths"] = [self.BAD_PATH]
        manifest["agents"] = {
            "started": [start],
            "completed": [_agent_complete(run_id="scope-run")],
            "incomplete": [],
        }
        _write_manifest(tmp_path / "review.manifest.json", manifest)

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "scope-run"
        assert run["availability"]["lifecycle"] is False

    def test_canonical_lifecycle_scope_path_keeps_lifecycle_available(
        self, tmp_path
    ):
        """Control case proving the bad-path rejection is the path's fault."""
        manifest = _manifest("scope-run")
        manifest["agents"] = {
            "started": [_agent_start(run_id="scope-run")],
            "completed": [_agent_complete(run_id="scope-run")],
            "incomplete": [],
        }
        _write_manifest(tmp_path / "review.manifest.json", manifest)

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "scope-run"
        assert run["availability"]["lifecycle"] is True


class TestDamagedLegacyLogBytes:
    """One invalid UTF-8 byte in a legacy log must cost that line only,
    never abort the cohort scan."""

    def test_invalid_utf8_line_is_skipped_not_fatal(self, tmp_path):
        events = _legacy_events("legacy-damaged")
        payload = b"\n".join(json.dumps(event).encode("utf-8") for event in events)
        (tmp_path / "review.jsonl").write_bytes(
            payload + b'\n{"event": "step", "note": "\xff\xfe"}\n'
        )

        [run] = load_runs(tmp_path)

        assert run["run"]["id"] == "legacy-damaged"
        assert "legacy_log_no_manifest" in run["warnings"]


def _synthesis_row(agent: str, **overrides) -> dict:
    reconciliator = agent == contracts._SYNTHESIS_RECONCILIATOR
    row = {
        "agent": agent,
        "verdict": "request_changes" if reconciliator else "STAND",
        "started_at": "2026-08-19T12:00:00+00:00",
        "completed_at": "2026-08-19T12:11:05+00:00",
        "duration_ms": 665_000,
        "stalled": False,
    }
    row.update(overrides)
    return row


def _synthesis_manifest(run_id: str = "run-1", *rows, **overrides) -> dict:
    manifest = _manifest(run_id)
    manifest["synthesis_agents"] = {
        "finalized": True,
        "agents": list(rows),
    }
    manifest["availability"]["synthesis_agents"] = True
    manifest.update(overrides)
    return manifest


class TestSynthesisAgentsMeasurement:
    """The reconciliator/critic phase durations, cross-run.

    The critic was the longest single phase of the audited 2026-08-19 run
    (~11 minutes) and had no number anywhere. This family is where that
    number lives — and where a run that never measured it must keep
    saying so rather than contributing a zero.
    """

    def test_pre_feature_run_is_missing_never_zero(self):
        """THE availability conjunct. A manifest carrying no
        `synthesis_agents` section did not measure a fast synthesis
        phase; it measured nothing."""
        measured = measure_run(
            _manifest("run-1"), Path("/nonexistent"),
            include_transcripts=False,
        )
        assert measured["metric_availability"]["synthesis_agents"] == "missing"
        assert measured["synthesis_agents"] is None

    def test_measured_durations_are_complete(self):
        measured = measure_run(
            _synthesis_manifest(
                "run-1",
                _synthesis_row(contracts._SYNTHESIS_RECONCILIATOR, duration_ms=41_000),
                _synthesis_row(contracts._SYNTHESIS_DECISION_CRITIC),
            ),
            Path("/nonexistent"), include_transcripts=False,
        )
        assert measured["metric_availability"]["synthesis_agents"] == "complete"
        durations = {
            row["agent"]: row["duration_ms"]
            for row in measured["synthesis_agents"]["agents"]
        }
        assert durations == {
            contracts._SYNTHESIS_RECONCILIATOR: 41_000,
            contracts._SYNTHESIS_DECISION_CRITIC: 665_000,
        }

    def test_measured_zero_dispatches_is_complete_not_missing(self):
        measured = measure_run(
            _synthesis_manifest("run-1"), Path("/nonexistent"),
            include_transcripts=False,
        )
        assert measured["metric_availability"]["synthesis_agents"] == "complete"
        assert measured["synthesis_agents"]["agents"] == []

    def test_a_stall_makes_the_family_partial(self):
        """A dispatched agent with no duration is a measured hole, not a
        measured phase — and never a silent drop."""
        measured = measure_run(
            _synthesis_manifest("run-1", _synthesis_row(
                contracts._SYNTHESIS_RECONCILIATOR, completed_at=None,
                duration_ms=None, stalled=True,
            )),
            Path("/nonexistent"), include_transcripts=False,
        )
        assert measured["metric_availability"]["synthesis_agents"] == "partial"
        row = measured["synthesis_agents"]["agents"][0]
        assert row["stalled"] is True
        assert row["duration_ms"] is None

    @pytest.mark.parametrize(
        # `_nonnegative_exact_int`: a boolean fails `type is not int` (as a
        # string or a float does); a negative fails the bound.
        "value", [-1, True],
        ids=["negative", "bool"],
    )
    def test_unusable_duration_never_becomes_zero(self, value):
        measured = measure_run(
            _synthesis_manifest(
                "run-1", _synthesis_row(contracts._SYNTHESIS_DECISION_CRITIC, duration_ms=value)
            ),
            Path("/nonexistent"), include_transcripts=False,
        )
        assert measured["synthesis_agents"]["agents"][0]["duration_ms"] is None
        assert measured["metric_availability"]["synthesis_agents"] == "partial"

    def test_a_row_without_an_agent_name_invalidates_the_section(self):
        manifest = _synthesis_manifest("run-1", {"duration_ms": 5})
        measured = measure_run(
            manifest, Path("/nonexistent"), include_transcripts=False
        )
        assert measured["synthesis_agents"] is None
        assert measured["metric_availability"]["synthesis_agents"] == "missing"


class TestSynthesisAgentsCohort:
    def test_durations_aggregate_per_agent(self):
        runs = [
            measure_run(
                _synthesis_manifest(
                    run_id,
                    _synthesis_row(contracts._SYNTHESIS_RECONCILIATOR, duration_ms=recon),
                    _synthesis_row(contracts._SYNTHESIS_DECISION_CRITIC, duration_ms=critic),
                ),
                Path("/nonexistent"), include_transcripts=False,
            )
            for run_id, recon, critic in (
                ("run-1", 40_000, 600_000),
                ("run-2", 60_000, 700_000),
            )
        ]
        block = aggregate_cohort(runs)["synthesis_agents"]
        assert block["by_agent"][contracts._SYNTHESIS_DECISION_CRITIC] == {
            "dispatched_runs": 2,
            "measured_runs": 2,
            "stalled_runs": 0,
            "skipped_runs": 0,
            "total_ms": 1_300_000,
            "mean_ms": 650_000,
        }
        assert block["by_agent"][
            contracts._SYNTHESIS_RECONCILIATOR
        ]["mean_ms"] == 50_000
        assert block["available_runs"] == 2

    def test_unmeasured_runs_contribute_nothing(self):
        runs = [
            measure_run(_manifest("run-1"), Path("/nonexistent"),
                        include_transcripts=False),
            measure_run(_manifest("run-2"), Path("/nonexistent"),
                        include_transcripts=False),
        ]
        block = aggregate_cohort(runs)["synthesis_agents"]
        assert block["by_agent"] is None
        assert block["available_runs"] == 0
        assert block["availability"]["missing"] == 2

    def test_a_stalled_run_still_reports_its_stall(self):
        """Dropping partial runs would delete the only cross-run record
        of a hung synthesis agent."""
        runs = [
            measure_run(
                _synthesis_manifest("run-1", _synthesis_row(
                    contracts._SYNTHESIS_DECISION_CRITIC, completed_at=None,
                    duration_ms=None, stalled=True,
                )),
                Path("/nonexistent"), include_transcripts=False,
            ),
        ]
        block = aggregate_cohort(runs)["synthesis_agents"]
        assert block["by_agent"][contracts._SYNTHESIS_DECISION_CRITIC] == {
            "dispatched_runs": 1,
            "measured_runs": 0,
            "stalled_runs": 1,
            "skipped_runs": 0,
            "total_ms": None,
            "mean_ms": None,
        }


class TestSynthesisAgentsRendering:
    def test_column_position_is_pinned(self):
        """Other tests index this table positionally; a column inserted
        without updating them would silently re-point their assertions."""
        assert render.format_table(
            [measure_run(_manifest("run-1"), Path("/nonexistent"),
                         include_transcripts=False)],
            {},
        ).splitlines()[0].split("|")[8].strip() == "Recon/Critic"

    def test_unmeasured_run_renders_as_absent(self):
        measured = measure_run(
            _manifest("run-1"), Path("/nonexistent"),
            include_transcripts=False,
        )
        assert render._table_row(measured)[7] == "—"

    def test_durations_render_as_seconds(self):
        measured = measure_run(
            _synthesis_manifest(
                "run-1",
                _synthesis_row(contracts._SYNTHESIS_RECONCILIATOR, duration_ms=41_000),
                _synthesis_row(contracts._SYNTHESIS_DECISION_CRITIC),
            ),
            Path("/nonexistent"), include_transcripts=False,
        )
        assert render._table_row(measured)[7] == "41.0s/665.0s"

    def test_a_stall_renders_as_stalled_not_as_a_fast_phase(self):
        measured = measure_run(
            _synthesis_manifest("run-1", _synthesis_row(
                contracts._SYNTHESIS_DECISION_CRITIC, completed_at=None,
                duration_ms=None, stalled=True,
            )),
            Path("/nonexistent"), include_transcripts=False,
        )
        assert render._table_row(measured)[7] == "—/stalled"


class TestSkippedCriticIsNotACritiqueDuration:
    """Historical SKIPPED rows never become critique durations.

    Current quick-mode skips commit SKIPPED without a dispatch marker and
    therefore produce no row; current dispatched failures have no usable
    verdict and stall. The reader still accepts historical SKIPPED rows and
    excludes their non-critique spans from the cohort duration statistic.
    """

    def _run(self, run_id, verdict, duration_ms):
        return measure_run(
            _synthesis_manifest("run-" + run_id, _synthesis_row(
                contracts._SYNTHESIS_DECISION_CRITIC,
                verdict=verdict, duration_ms=duration_ms,
            )),
            Path("/nonexistent"), include_transcripts=False,
        )

    def test_skipped_rows_are_excluded_from_the_statistics(self):
        runs = [
            self._run("1", "STAND", 600_000),
            self._run("2", contracts._CRITIC_VERDICT_SKIPPED, 900),
        ]
        block = aggregate_cohort(runs)["synthesis_agents"]
        agent = block["by_agent"][contracts._SYNTHESIS_DECISION_CRITIC]
        assert agent == {
            "dispatched_runs": 2,
            "measured_runs": 1,
            "stalled_runs": 0,
            "skipped_runs": 1,
            "total_ms": 600_000,
            "mean_ms": 600_000,
        }

    def test_an_all_skipped_cohort_reports_no_duration_at_all(self):
        runs = [self._run("1", contracts._CRITIC_VERDICT_SKIPPED, 900)]
        agent = aggregate_cohort(runs)["synthesis_agents"]["by_agent"][
            contracts._SYNTHESIS_DECISION_CRITIC
        ]
        assert agent["mean_ms"] is None
        assert agent["skipped_runs"] == 1
        assert agent["dispatched_runs"] == 1


# --- Task 12: optional manifest sections carry their payload through ---
#
# Recorded defect: the sanitize layer taught four optional manifest
# sections' *availability flags* to `safe_availability`'s generic
# bool-copy without ever teaching it their *payloads* — "measured: true"
# with the section itself dropped. `coverage`, `synthesis_agents`, and the
# `outcome` block's verdict-provenance key had already
# closed this gap by the time this landed; `worktree_hygiene`, `usage`
# (the durable per-run token snapshot — distinct from the *transcript*
# usage family under `measured["transcript"]["usage"]`), and
# `skipped_steps` had not.

_USAGE_SNAPSHOT_FIELD_MAP = {
    "input_tokens": 10,
    "cache_creation_input_tokens": 20,
    "cache_read_input_tokens": 30,
    "effective_input_tokens": 60,
    "output_tokens": 40,
}


def _worktree_hygiene_payload(**overrides) -> dict:
    payload = {
        "status": "clean",
        "new_files": [],
        "changed_files": ["src/a.py"],
        "probe_residue_removed": [],
        "baseline_captured_at": "2026-08-19T12:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def _usage_snapshot_payload(**overrides) -> dict:
    payload = {
        "captured_at": "2026-08-19T12:15:00+00:00",
        "window": {
            "started_at": "2026-08-19T12:00:00+00:00",
            "ended_at": "2026-08-19T12:15:00+00:00",
            "closed": True,
        },
        "availability": {"subagents": "complete", "orchestrator": "complete"},
        "agents_measured": {"measured": 3, "expected": 3},
        "subagent_totals": dict(_USAGE_SNAPSHOT_FIELD_MAP),
        "orchestrator_usage": dict(_USAGE_SNAPSHOT_FIELD_MAP),
        "usage_by_model": {"claude-opus-5[1m]": dict(_USAGE_SNAPSHOT_FIELD_MAP)},
        "by_agent": [
            {
                "agent": "security-reviewer",
                "model": "claude-opus-5[1m]",
                "usage": _usage(2),
                "tool_calls": 12,
                "repository_reads": 4,
            },
            {
                "agent": contracts._SYNTHESIS_RECONCILIATOR,
                "model": "claude-opus-5[1m]",
                "usage": _usage(5),
                "tool_calls": 8,
                "repository_reads": 3,
            },
            {
                "agent": contracts._SYNTHESIS_DECISION_CRITIC,
                "model": "claude-opus-5[1m]",
                "usage": _usage(3),
                "tool_calls": 6,
                "repository_reads": 2,
            },
        ],
    }
    payload.update(overrides)
    return payload


def _skipped_steps_payload() -> list:
    return [
        {"step": 10, "title": "Decision Critic", "condition": "quick_mode_enabled"}
    ]


def _dependency_refresh_payload(**overrides) -> dict:
    payload = {
        "requested": True,
        "reported": True,
        "status": "completed",
        "tracked_files_dirty": False,
        "dirty_files": [],
        "commands": [
            {"directory": ".", "command": "composer install", "exit_status": "ok"},
        ],
    }
    payload.update(overrides)
    return payload


def _derived_markdown_payload(**overrides) -> dict:
    payload = {
        "ran": True,
        "written": 1,
        "expected": 1,
        "status": "complete",
    }
    payload.update(overrides)
    return payload


def _host_context_payload():
    return {
        "resolved": [{"name": "wordpress", "kind": "runtime-host", "source": "ecosystem-cache", "version": "7.2",
                      "commit": "abc", "refreshed": "2026-09-04T00:04:08Z", "declared_minimum": "7.0"}],
        "unresolved": [{"name": "jetpack", "reason": "declared_in_plugin_headers", "version": None}],
        "banner_reason": "partial_unresolved", "self_provided": [], "scan_roots": 2,
    }


def _evidence_payload():
    return {
        "findings": [{"id": "f1", "severity": "high", "sources": [{"agent": "security-reviewer", "id": "f2", "severity": "medium"}], "critic_action": "demote"}],
        "dropped_findings": [{"agent": "security-reviewer", "id": "f3", "reason": "false_positive"}],
        "findings_removed_by_critic": [{"id": "f2", "severity": "low", "sources": [{"agent": "security-reviewer", "id": "f5", "severity": "low"}], "critic_action": "remove"}],
        "checks": {"count": 4, "dropped": {"void": 1}},
        "verify_items": [{"id": "V1", "carried_over": False, "settled_by": 2}],
        "undeclared_citations": 0,
        "critic": {"verdict": "REVISE", "verdict_before_adjustments": "block", "adjustments": {
            "demote": {"proposed": 1, "verified": 1, "not_checked": 0, "refuted": 0},
        }},
        "orchestrator_notes": {"confirmed": 1, "refuted": 0, "not_checked": 0},
        "host_citations": {"ecosystem-integration-reviewer": {"wordpress": 3, "unknown": 1}},
    }


class TestEvidenceMetrics:
    @staticmethod
    def _historical_evidence_manifest(run_id="historical"):
        manifest = _manifest(run_id)
        manifest["evidence"] = _evidence_payload()
        manifest["availability"]["evidence"] = True
        manifest["evidence"]["findings"][0]["sources"] = None
        manifest["evidence"]["dropped_findings"] = None
        manifest["evidence"]["findings_removed_by_critic"] = None
        manifest["evidence"]["checks"]["dropped"] = None
        manifest["evidence"]["orchestrator_notes"] = None
        return manifest

    def test_a_critic_added_finding_keeps_the_run_lineage_measured(self):
        """A critic-originated finding has a known empty lineage; it must
        not read as unknown provenance and void the run's survival counts."""
        manifest = _manifest("added")
        manifest["evidence"] = _evidence_payload()
        manifest["availability"]["evidence"] = True
        manifest["evidence"]["findings"].append(
            {"id": "f9", "severity": "medium", "sources": [], "critic_action": "add"}
        )
        measured = measure_run(manifest, Path("/nonexistent"), include_transcripts=False)
        assert measured["evidence"]["findings"][1]["sources"] == []
        result = aggregate_cohort([measured])["evidence"]
        assert result["availability"]["lineage"] == {"measured_runs": 1, "unavailable_runs": 0}
        assert result["survival_by_agent"] == {
            "security-reviewer": {"kept": 1, "dropped": {"false_positive": 1}, "critic_removed": 1},
        }

    def test_a_manifest_without_critic_removals_has_unknown_lineage(self):
        """A manifest projected before critic removals were carried cannot
        say what happened to their sources; the run's lineage is
        unavailable, never a measured zero removals."""
        manifest = _manifest("older")
        manifest["evidence"] = _evidence_payload()
        manifest["availability"]["evidence"] = True
        del manifest["evidence"]["findings_removed_by_critic"]
        measured = measure_run(manifest, Path("/nonexistent"), include_transcripts=False)
        assert measured["evidence"]["findings_removed_by_critic"] is None
        result = aggregate_cohort([measured])["evidence"]
        assert result["survival_by_agent"] is None
        assert result["availability"]["lineage"] == {"measured_runs": 0, "unavailable_runs": 1}

    def test_historical_evidence_preserves_unknown_subfamilies(self):
        measured = measure_run(
            self._historical_evidence_manifest(),
            Path("/nonexistent"),
            include_transcripts=False,
        )

        assert measured["metric_availability"]["evidence"] == "partial"
        assert measured["evidence"]["findings"][0]["sources"] is None
        assert measured["evidence"]["dropped_findings"] is None
        assert measured["evidence"]["checks"]["dropped"] is None
        assert measured["evidence"]["orchestrator_notes"] is None

        result = aggregate_cohort([measured])["evidence"]
        assert result["survival_by_agent"] is None
        assert result["dropped_checks"] is None
        assert result["orchestrator_notes"] is None
        assert result["availability"] == {
            "lineage": {"measured_runs": 0, "unavailable_runs": 1},
            "dropped_checks": {"measured_runs": 0, "unavailable_runs": 1},
            "orchestrator_notes": {"measured_runs": 0, "unavailable_runs": 1},
        }
        assert result["critic_adjustments"] == {
            "demote": {"proposed": 1, "verified": 1, "not_checked": 0, "refuted": 0},
        }
        assert result["verify_items"] == {"declared": 1, "settled": 1}

    def test_mixed_evidence_cohort_names_each_partial_denominator(self):
        known = _manifest("known")
        known["evidence"] = _evidence_payload()
        known["availability"]["evidence"] = True
        manifests = [known, self._historical_evidence_manifest()]
        measured = [
            measure_run(manifest, Path("/nonexistent"), include_transcripts=False)
            for manifest in manifests
        ]

        result = aggregate_cohort(measured)["evidence"]

        assert result["survival_by_agent"] == {
            "security-reviewer": {"kept": 1, "dropped": {"false_positive": 1}, "critic_removed": 1},
        }
        assert result["dropped_checks"] == {"void": 1}
        assert result["orchestrator_notes"] == {
            "confirmed": 1,
            "refuted": 0,
            "not_checked": 0,
        }
        assert result["availability"] == {
            "lineage": {"measured_runs": 1, "unavailable_runs": 1},
            "dropped_checks": {"measured_runs": 1, "unavailable_runs": 1},
            "orchestrator_notes": {"measured_runs": 1, "unavailable_runs": 1},
        }
        assert result["critic_adjustments"]["demote"]["proposed"] == 2
        assert result["verify_items"] == {"declared": 2, "settled": 2}

    # Both orders pin that one unknown run poisons the totals whichever
    # side it is on; an unknown run alone is a subset of either.
    @pytest.mark.parametrize("population", ["known-first", "unknown-first"])
    def test_unavailable_purpose_keeps_both_verify_totals_unknown(self, population):
        known = _manifest("known")
        known["evidence"] = _evidence_payload()
        known["availability"]["evidence"] = True
        unknown = copy.deepcopy(known)
        unknown["run"]["id"] = "unknown"
        unknown["evidence"]["verify_items"] = []
        unknown["evidence"]["undeclared_citations"] = None
        manifests = {"known-first": [known, unknown], "unknown-first": [unknown, known]}[population]
        measured = [measure_run(manifest, Path("/nonexistent"), include_transcripts=False) for manifest in manifests]
        result = aggregate_cohort(measured)["evidence"]
        assert result["verify_items"] == {"declared": None, "settled": None}
        assert result["measured_runs"] == len(manifests)

    def test_absent_adjustment_counter_is_materialized_as_unknown(self):
        """`counts()` is one comprehension over the counter vocabulary;
        `proposed` stands for the other three."""
        counter = "proposed"
        known = _manifest("known")
        known["evidence"] = _evidence_payload()
        known["availability"]["evidence"] = True
        unknown = copy.deepcopy(known)
        unknown["run"]["id"] = "unknown"
        del unknown["evidence"]["critic"]["adjustments"]["demote"][counter]
        measured = [measure_run(manifest, Path("/nonexistent"), include_transcripts=False) for manifest in (known, unknown)]
        assert measured[1]["evidence"]["critic"]["adjustments"]["demote"][counter] is None
        expected = {"proposed": 2, "verified": 2, "not_checked": 0, "refuted": 0}
        expected[counter] = None
        assert aggregate_cohort(measured)["evidence"]["critic_adjustments"] == {"demote": expected}

    @pytest.mark.parametrize("details", [None, {}], ids=["unreadable-proposal", "validated-empty-proposal"])
    def test_unreadable_proposal_is_distinct_from_a_validated_empty_proposal(self, details):
        known = _manifest("known")
        known["evidence"] = _evidence_payload()
        known["availability"]["evidence"] = True
        other = copy.deepcopy(known)
        other["run"]["id"] = "other"
        other["evidence"]["critic"]["adjustments"] = details
        measured = [measure_run(manifest, Path("/nonexistent"), include_transcripts=False) for manifest in (known, other)]
        assert measured[1]["evidence"]["critic"]["adjustments"] == details
        assert aggregate_cohort([measured[1]])["evidence"]["critic_adjustments"] == details
        expected = None if details is None else {"demote": {"proposed": 1, "verified": 1, "not_checked": 0, "refuted": 0}}
        assert aggregate_cohort(measured)["evidence"]["critic_adjustments"] == expected
        assert aggregate_cohort(list(reversed(measured)))["evidence"]["critic_adjustments"] == expected

    def test_unknown_counts_do_not_become_measured_zero_or_partial_totals(self):
        known = _manifest("known")
        known["evidence"] = _evidence_payload()
        known["availability"]["evidence"] = True
        unknown = copy.deepcopy(known)
        unknown["run"]["id"] = "unknown"
        unknown["evidence"]["verify_items"][0]["settled_by"] = None
        unknown["evidence"]["critic"]["adjustments"]["demote"]["proposed"] = None
        measured = [measure_run(manifest, Path("/nonexistent"), include_transcripts=False) for manifest in (known, unknown)]
        result = aggregate_cohort(measured)["evidence"]
        assert result["verify_items"] == {"declared": 2, "settled": None}
        assert result["critic_adjustments"]["demote"] == {"proposed": None, "verified": 2, "refuted": 0, "not_checked": 0}

    def test_projection_survives_measurement_and_aggregation(self):
        manifest = _manifest()
        manifest["evidence"] = _evidence_payload()
        manifest["availability"]["evidence"] = True
        measured = measure_run(manifest, Path("/nonexistent"), include_transcripts=False)
        assert measured["evidence"] == _evidence_payload()
        assert measured["metric_availability"]["evidence"] == "complete"
        historic = measure_run(_manifest("old"), Path("/nonexistent"), include_transcripts=False)
        assert historic["evidence"] is None
        assert historic["metric_availability"]["evidence"] == "missing"
        assert aggregate_cohort([historic])["evidence"] is None
        assert aggregate_cohort([measured, measured, historic])["evidence"] == {
            "survival_by_agent": {"security-reviewer": {"kept": 2, "dropped": {"false_positive": 2}, "critic_removed": 2}},
            "critic_adjustments": {"demote": {"proposed": 2, "verified": 2, "not_checked": 0, "refuted": 0}},
            "verify_items": {"declared": 2, "settled": 2},
            "dropped_checks": {"void": 2},
            "orchestrator_notes": {"confirmed": 2, "refuted": 0, "not_checked": 0},
            "availability": {
                "lineage": {"measured_runs": 2, "unavailable_runs": 0},
                "dropped_checks": {"measured_runs": 2, "unavailable_runs": 0},
                "orchestrator_notes": {"measured_runs": 2, "unavailable_runs": 0},
            },
            "measured_runs": 2,
        }

    def test_evidence_allowlist_discards_prose_and_invalid_facts(self):
        payload = _evidence_payload()
        payload["secret"] = "private prose"
        payload["findings"][0]["title"] = "private prose"
        payload["findings"][0]["sources"].append({"agent": "private/path", "id": "f9"})
        payload["findings"].append({"id": "private/path", "severity": "high"})
        payload["critic"]["adjustments"]["private prose"] = {"proposed": 99}
        payload["host_citations"]["private/path"] = {"wordpress": 2}
        payload["host_citations"]["ecosystem-integration-reviewer"]["private/path"] = 2
        payload["checks"]["count"] = True
        payload["verify_items"].append({"id": "private prose", "settled_by": 8})
        safe = sanitize._sanitize_evidence(payload)
        assert "private" not in json.dumps(safe)
        assert safe["checks"]["count"] is None
        assert len(safe["findings"]) == 1
        assert safe["findings"][0]["sources"] == [{"agent": "security-reviewer", "id": "f2", "severity": "medium"}]

    def test_a_source_severity_outside_the_vocabulary_is_unmeasured(self):
        payload = _evidence_payload()
        payload["findings"][0]["sources"][0]["severity"] = "private prose"
        safe = sanitize._sanitize_evidence(payload)
        assert safe["findings"][0]["sources"] == [{"agent": "security-reviewer", "id": "f2", "severity": None}]

    # A non-dict (absent and array fail the same isinstance check) and a
    # dict missing the required keys.
    @pytest.mark.parametrize("value", ["private prose", {}], ids=["prose", "empty-object"])
    def test_absent_or_malformed_family_is_unmeasured(self, value):
        assert sanitize._sanitize_evidence(value) is None


class TestHostContextSanitization:
    def test_a_path_shaped_host_field_is_kept_locally_and_refused_at_the_share_boundary(self):
        """The projection and the sanitizer carry the resolver's facts as
        recorded; the one guard against a path leaving the machine is the
        sharing boundary, which refuses the upload rather than silently
        nulling a field that bootstrap still prints.

        The share module's own table (`test_telemetry_share.py`) sweeps
        the shapes its guard recognizes. This row is the one shape that
        table does not carry: a rooted Windows path after a delimiter, the
        delimiter alternative of `_ROOTED_WINDOWS_PATH`."""
        leaked = "path=\\private\\host"
        projected = contracts._MANIFEST_SECTIONS_CONTRACT.summarize_host_context({
            "resolved": [{
                "name": "wordpress", "kind": "runtime-host",
                "source": "ecosystem-cache", "version": "7.2-alpha-63166-src",
                "notes": {"commit": "abc123", "declared_minimum": leaked},
            }],
            "unresolved": [], "diagnostics": {},
        })
        manifest = _manifest()
        manifest["run"]["repo"] = "github.com/acme/widget"
        sanitized = sanitize._sanitize_host_context(projected)
        manifest["host_context"] = sanitized
        manifest["availability"]["host_context"] = True

        for section in (projected, sanitized):
            assert section["resolved"][0]["declared_minimum"] == leaked
            assert section["resolved"][0]["version"] == "7.2-alpha-63166-src"
            assert section["resolved"][0]["commit"] == "abc123"
        with pytest.raises(ValueError, match="share-unsafe path survived redaction"):
            telemetry_share.redact_payloads(manifest, [])

    def test_upstream_identity_survives_projection_and_sharing(self):
        raw = {
            "resolved": [{
                "name": "wordpress", "kind": "runtime-host",
                "source": "ecosystem-cache", "version": "feature/Users/import-7.2",
                "version_freshness": "2026-09-04T00:04:08Z",
                "notes": {
                    "commit": "474555a85c052de90ddd22d4abdf163e678b88ac",
                    "branch": "fix/TICKET-123-private",
                    "declared_minimum": "7.0",
                },
            }],
        }
        projected = contracts._MANIFEST_SECTIONS_CONTRACT.summarize_host_context(raw)
        sanitized = sanitize._sanitize_host_context(projected)
        manifest = _manifest()
        manifest["run"]["repo"] = "github.com/acme/widget"
        manifest["host_context"] = sanitized
        redacted, _ = telemetry_share.redact_payloads(manifest, [])
        assert redacted["host_context"] == projected
        # A disclosed value may contain a path-like substring; a branch
        # name is never projected, so a personal one cannot be shared.
        assert projected["resolved"][0]["version"] == "feature/Users/import-7.2"
        assert "branch" not in projected["resolved"][0]
        assert "TICKET-123" not in json.dumps(redacted)
        assert projected["resolved"][0]["refreshed"] == "2026-09-04T00:04:08Z"

    def test_explicit_unavailable_host_stays_measured_after_repeated_passes(self, tmp_path):
        manifest = _manifest()
        manifest["host_context"] = None
        manifest["availability"]["host_context"] = False
        for _ in range(3):
            manifest = sanitize._sanitize_manifest(manifest)
            manifest = measure_run(
                manifest, sessions_root=tmp_path, include_transcripts=False,
            )
            assert manifest["host_context"] is None
            assert manifest["availability"]["host_context"] is False

    def test_round_trip_retains_only_declared_fields(self):
        payload = _host_context_payload()
        raw = copy.deepcopy(payload)
        raw["resolved"][0]["path"] = "/Users/private/cache"
        raw["unresolved"][0]["notes"] = {"path": "/Users/private"}
        assert sanitize._sanitize_host_context(raw) == payload

    # A non-dict section (None and a number fail the same isinstance check)
    # and a dict whose `resolved` is not a list.
    @pytest.mark.parametrize(
        "value",
        ["bad", {"resolved": 42, "unresolved": []}],
        ids=["non-dict", "non-list-resolved"],
    )
    def test_bad_containers_are_unavailable(self, value):
        assert sanitize._sanitize_host_context(value) is None

    def test_non_string_scalars_are_unknown(self):
        """One `_safe_string` non-string path; an object stands for a list
        or a number."""
        value = {"path": "/Users/private"}
        payload = _host_context_payload()
        payload["resolved"][0] = {key: value for key in payload["resolved"][0]}
        payload["unresolved"][0] = {key: value for key in payload["unresolved"][0]}
        payload["banner_reason"] = value
        payload["self_provided"] = [value]
        payload["scan_roots"] = True
        result = sanitize._sanitize_host_context(payload)
        assert all(v is None for v in result["resolved"][0].values())
        assert all(v is None for v in result["unresolved"][0].values())
        assert result["banner_reason"] is None
        assert result["self_provided"] == []
        assert result["scan_roots"] is None


# `_sanitize_optional_sections` is ONE table-driven loop with three
# branches, each pinned once rather than restated per section: the
# derive-the-flag-from-what-parsed branch by
# `test_reborn_lie_flag_true_absent_payload_publishes_false` and the
# one-section `test_every_declared_section_round_trips_and_rejects_garbage`
# (both through this representative section); the flag-`False`-wins
# branch by `TestMeasureRun::test_explicit_false_coverage_availability_wins_over_valid_payload`;
# and the undeclared-section skip by
# `test_a_pre_feature_run_carries_neither_flag_nor_payload`. What IS
# per-section is each entry's own sanitizer, covered by each section's
# dedicated field-level class.
_REPRESENTATIVE_OPTIONAL_SECTION = "assignment"


class TestOptionalSectionAvailabilityConsistency:
    """The flag/payload consistency pin for the shared sanitize loop.

    The loop's properties belong to the loop, not to any section, so
    each is pinned once through `_REPRESENTATIVE_OPTIONAL_SECTION`.
    """

    def test_every_declared_section_round_trips_and_rejects_garbage(self):
        """The derive-the-flag branch, both ways: the section's own
        sanitizer accepts its own well-formed payload (and the derived
        flag reads `true`), and rejects a payload of the wrong shape (a
        bare string instead of the section's dict), dropping the derived
        flag to `false` rather than trusting the raw `true` past a
        payload that never actually parsed.
        """
        name = _REPRESENTATIVE_OPTIONAL_SECTION
        manifest = _manifest("run-1")
        manifest["availability"][name] = True
        manifest[name] = _manifest()[name]

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["availability"][name] is True
        assert sanitized[name] is not None

        manifest = _manifest("run-1")
        manifest["availability"][name] = True
        manifest[name] = "not a valid payload for any section"

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["availability"][name] is False
        assert sanitized[name] is None

    def test_an_absent_section_reports_missing_without_a_payload(self):
        name = _REPRESENTATIVE_OPTIONAL_SECTION
        manifest = _manifest("run-1")
        manifest["availability"][name] = False
        manifest.pop(name, None)

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["availability"][name] is False
        assert sanitized[name] is None

    def test_a_pre_feature_run_carries_neither_flag_nor_payload(self):
        """A run predating the section has no key at all — never a
        fabricated `False`, and never a fabricated payload."""
        name = _REPRESENTATIVE_OPTIONAL_SECTION
        manifest = _manifest("run-1")
        manifest["availability"].pop(name, None)
        manifest.pop(name, None)

        sanitized = sanitize._sanitize_manifest(manifest)

        assert name not in sanitized["availability"]
        assert sanitized[name] is None

    def test_reborn_lie_flag_true_absent_payload_publishes_false(self):
        """THE consistency pin Task 12 exists to hold, restated for the
        reborn-lie direction: a producer bug that writes
        `availability[name]: true` beside a payload that never arrived
        must publish `false`, not resurrect the raw `true`. The flag is
        DERIVED from what the sanitizer actually parsed — it is never a
        verbatim copy of the raw manifest's claim."""
        name = _REPRESENTATIVE_OPTIONAL_SECTION
        manifest = _manifest("run-1")
        manifest["availability"][name] = True
        manifest.pop(name, None)

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["availability"][name] is False
        assert sanitized[name] is None


class TestOptionalSectionVocabulariesAreNotRestated:
    """I3: the section-status vocabularies must have exactly one
    spelling — the producer's own private constants in
    manifest_sections.py, reached the same way `contracts.py` already
    reaches `_TELEMETRY_CONTRACT._incomplete_agent_executions` — rather
    than a separately maintained literal copy that can silently drift
    wider than what the consumer actually accepts. Task 13 added
    `_DEPENDENCY_REFRESH_STATUSES` and `_DERIVED_MARKDOWN_STATUSES` to
    the two Task 12 pinned here.
    """

    def test_contracts_reaches_the_producers_own_constants_not_a_copy(self):
        manifest_sections = _load_manifest_sections_module()

        assert contracts._WORKTREE_HYGIENE_STATUSES == (
            manifest_sections._WORKTREE_HYGIENE_STATUSES
        )
        assert contracts._USAGE_SNAPSHOT_AVAILABILITY_STATES == (
            manifest_sections._USAGE_AVAILABILITY_STATES
        )
        assert contracts._DEPENDENCY_REFRESH_STATUSES == (
            manifest_sections._DEPENDENCY_REFRESH_STATUSES
        )
        assert contracts._DEPENDENCY_REFRESH_EXIT_STATUSES == (
            manifest_sections._DEPENDENCY_REFRESH_EXIT_STATUSES
        )
        assert contracts._DERIVED_MARKDOWN_STATUSES == (
            manifest_sections._DERIVED_MARKDOWN_STATUSES
        )

    def test_every_producer_recognized_worktree_status_survives(self):
        """Swept off the PRODUCER's live constant, not a hardcoded copy of
        today's three values — a status the producer's vocabulary later
        widens to include joins this test automatically, and would have
        failed under the old restated-literal design.

        The sweep runs inside the test body rather than through
        `parametrize`: every value goes through the same membership check
        in one sanitizer, so N collected cases would restate one code
        path N times while a `for` loop covers the same widening.
        """
        statuses = _load_manifest_sections_module()._WORKTREE_HYGIENE_STATUSES
        for status in sorted(statuses):
            manifest = _manifest("run-1")
            manifest["availability"]["worktree_hygiene"] = True
            manifest["worktree_hygiene"] = _worktree_hygiene_payload(
                status=status
            )

            sanitized = sanitize._sanitize_manifest(manifest)

            assert sanitized["worktree_hygiene"]["status"] == status

    def test_every_producer_recognized_usage_state_survives(self):
        states = _load_manifest_sections_module()._USAGE_AVAILABILITY_STATES
        for state in sorted(states):
            manifest = _manifest("run-1")
            manifest["availability"]["usage"] = True
            manifest["usage"] = _usage_snapshot_payload(
                availability={"subagents": state, "orchestrator": state}
            )

            sanitized = sanitize._sanitize_manifest(manifest)

            assert sanitized["usage"]["availability"] == {
                "subagents": state,
                "orchestrator": state,
            }

    def test_every_producer_recognized_dependency_refresh_status_survives(
        self,
    ):
        statuses = (
            _load_manifest_sections_module()._DEPENDENCY_REFRESH_STATUSES
        )
        for status in sorted(statuses):
            manifest = _manifest("run-1")
            manifest["availability"]["dependency_refresh"] = True
            manifest["dependency_refresh"] = _dependency_refresh_payload(
                status=status
            )

            sanitized = sanitize._sanitize_manifest(manifest)

            assert sanitized["dependency_refresh"]["status"] == status

    def test_every_producer_recognized_derived_markdown_status_survives(self):
        """Both `reviewer_markdown` and `findings_markdown` share the
        same sanitizer, so one sweep over each name is enough to pin that
        the shared vocabulary is reached, not restated, on both keys of
        the map."""
        statuses = _load_manifest_sections_module()._DERIVED_MARKDOWN_STATUSES
        for status in sorted(statuses):
            ran = status != "not_run"
            written = 1 if status == "complete" else 0
            expected = 1
            for name in ("reviewer_markdown", "findings_markdown"):
                manifest = _manifest("run-1")
                manifest["availability"][name] = True
                manifest[name] = _derived_markdown_payload(
                    ran=ran,
                    written=written,
                    expected=expected,
                    status=status,
                )

                sanitized = sanitize._sanitize_manifest(manifest)

                assert sanitized[name]["status"] == status


class TestSkippedStepsDivergenceFromProducer:
    """M3: the sanitizer is at least as strict as its producer, not
    exactly as strict — pin the known divergence in the title/condition
    fallback rather than let the docstring claim more than the code
    does."""

    def test_an_oversized_title_becomes_empty_though_the_producer_keeps_it(
        self,
    ):
        """The producer's bare `item.get("title") or ""` keeps any
        truthy value verbatim, unbounded. This sanitizer additionally
        requires `_safe_string`'s <=4096-char bound, so an oversized
        title survives at the producer but becomes "" here."""
        manifest = _manifest("run-1")
        manifest["availability"]["skipped_steps"] = True
        oversized_title = "x" * 5000
        manifest["skipped_steps"] = [
            {"step": 10, "title": oversized_title, "condition": "c"}
        ]

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["skipped_steps"] == [
            {"step": 10, "title": "", "condition": "c"}
        ]


class TestWorktreeHygieneSanitize:
    """PII: `status` is a three-value enum, `baseline_captured_at` is an
    ISO timestamp, and the three entry lists are `git status --porcelain`
    path lines from the reviewed repository's own source tree — never
    user-authored or personally identifying text.
    """

    def test_a_well_formed_payload_survives_field_for_field(self):
        manifest = _manifest("run-1")
        manifest["availability"]["worktree_hygiene"] = True
        manifest["worktree_hygiene"] = _worktree_hygiene_payload(
            new_files=["src/new.py"], changed_files=["src/a.py"],
        )

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["worktree_hygiene"] == {
            "status": "clean",
            "new_files": ["src/new.py"],
            "changed_files": ["src/a.py"],
            "probe_residue_removed": [],
            "baseline_captured_at": "2026-08-19T12:00:00+00:00",
        }

    def test_an_unrecognized_status_reads_as_unknown_never_clean(self):
        manifest = _manifest("run-1")
        manifest["availability"]["worktree_hygiene"] = True
        manifest["worktree_hygiene"] = _worktree_hygiene_payload(
            status="not_a_real_status"
        )

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["worktree_hygiene"]["status"] == "unknown"

    def test_a_non_dict_payload_is_missing_not_a_crash(self):
        manifest = _manifest("run-1")
        manifest["availability"]["worktree_hygiene"] = True
        manifest["worktree_hygiene"] = "not a dict"

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["worktree_hygiene"] is None


class TestUsageSnapshotSanitize:
    """The durable per-run token-usage snapshot section — distinct from
    the transcript-derived `usage` family under `measured["transcript"]`.

    PII: `captured_at` and the window timestamps are ISO instants;
    `window.closed` and the two `availability` states are fixed
    two/three-value enums; `agents_measured` and every usage map are
    plain non-negative token-count integers; `usage_by_model` keys are
    dispatched model identifiers; `by_agent` rows carry only a
    reviewer-agent name, a model identifier, and a usage map. None of
    this is user-authored or personally identifying text.
    """

    def test_a_well_formed_payload_survives_field_for_field(self):
        manifest = _manifest("run-1")
        manifest["availability"]["usage"] = True
        manifest["usage"] = _usage_snapshot_payload()

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["usage"] == _usage_snapshot_payload()

    def test_an_incomplete_usage_map_reads_as_none_not_a_zero(self):
        """All-or-nothing: a map missing a field cannot be summed or
        compared, and a zero would publish a fabricated measurement."""
        manifest = _manifest("run-1")
        manifest["availability"]["usage"] = True
        broken = _usage_snapshot_payload()
        del broken["subagent_totals"]["output_tokens"]
        manifest["usage"] = broken

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["usage"]["subagent_totals"] is None
        # The rest of the section is unaffected by one bad map.
        assert sanitized["usage"]["orchestrator_usage"] is not None

    def test_an_unrecognized_availability_state_falls_back_to_missing(self):
        manifest = _manifest("run-1")
        manifest["availability"]["usage"] = True
        manifest["usage"] = _usage_snapshot_payload(
            availability={"subagents": "bogus", "orchestrator": "partial"}
        )

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["usage"]["availability"] == {
            "subagents": "missing",
            "orchestrator": "partial",
        }

    def test_a_row_without_an_agent_name_is_dropped_not_the_section(self):
        manifest = _manifest("run-1")
        manifest["availability"]["usage"] = True
        manifest["usage"] = _usage_snapshot_payload(
            by_agent=[{"model": "claude-opus-5[1m]", "usage": dict(_USAGE_SNAPSHOT_FIELD_MAP)}]
        )

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["usage"] is not None
        assert sanitized["usage"]["by_agent"] == []


class TestUsageShares:
    def _measure(self, manifest: dict) -> dict:
        return measure_run(
            manifest, Path("/nonexistent"), include_transcripts=False
        )

    def _manifest_with_usage(self, **usage_overrides) -> dict:
        manifest = _manifest("usage-shares")
        manifest["availability"]["usage"] = True
        manifest["usage"] = _usage_snapshot_payload(**usage_overrides)
        return manifest

    def test_measures_synthesis_and_sorted_agent_shares_from_durable_usage(self):
        measured = self._measure(self._manifest_with_usage())

        assert measured["usage_shares"] == {
            "denominator_effective_input_tokens": 60,
            "synthesis_pct": 80.0,
            "by_agent_pct": {
                "decision-reviewer": 30.0,
                "review-reconciliator": 50.0,
                "security-reviewer": 20.0,
            },
        }
        assert measured["metric_availability"]["usage_shares"] == "complete"

    def test_zero_usage_denominator_is_missing_not_a_zero_share(self):
        zero_totals = {name: 0 for name in _USAGE_SNAPSHOT_FIELD_MAP}
        measured = self._measure(self._manifest_with_usage(
            subagent_totals=zero_totals,
        ))

        assert measured["usage_shares"] is None
        assert measured["metric_availability"]["usage_shares"] == "missing"

    def test_retry_dispatch_usage_is_summed_before_each_agent_share(self):
        payload = _usage_snapshot_payload()
        payload["by_agent"].extend([
            {
                "agent": "security-reviewer",
                "model": "claude-opus-5[1m]",
                "usage": _usage(3),
                "tool_calls": 9,
                "repository_reads": 3,
            },
            {
                "agent": contracts._SYNTHESIS_RECONCILIATOR,
                "model": "claude-opus-5[1m]",
                "usage": _usage(2),
                "tool_calls": 7,
                "repository_reads": 2,
            },
        ])
        payload["agents_measured"] = {"measured": 5, "expected": 5}
        payload["subagent_totals"] = _usage(15)
        payload["usage_by_model"] = {"claude-opus-5[1m]": _usage(15)}
        measured = self._measure(self._manifest_with_usage(**payload))

        assert measured["usage_shares"] == {
            "denominator_effective_input_tokens": 90,
            "synthesis_pct": 66.7,
            "by_agent_pct": {
                "decision-reviewer": 20.0,
                "review-reconciliator": 46.7,
                "security-reviewer": 33.3,
            },
        }

    def test_cohort_keeps_complete_and_partial_measured_share_samples(self):
        complete = self._measure(self._manifest_with_usage())
        partial = self._measure(self._manifest_with_usage(
            availability={"subagents": "partial", "orchestrator": "complete"},
        ))
        missing = self._measure(self._manifest_with_usage(
            subagent_totals={name: 0 for name in _USAGE_SNAPSHOT_FIELD_MAP},
        ))

        shares = aggregate_cohort([complete, partial, missing])["usage_shares"]

        assert shares["synthesis_pct"] == {
            "median": 80.0,
            "min": 80.0,
            "max": 80.0,
            "sample_count": 2,
        }
        assert shares["by_agent_pct"]["security-reviewer"] == {
            "median": 20.0,
            "max": 20.0,
            "runs": 2,
        }

    def test_table_renders_the_synthesis_share_or_a_missing_glyph(self):
        measured = self._measure(self._manifest_with_usage())
        missing = self._measure(_manifest("without-usage"))

        table = format_table([measured, missing], aggregate_cohort([measured, missing]))

        assert "Synth %" in table
        assert "80.0" in table
        assert "without-usage" in table
        assert render._table_row(missing)[8] == "—"

    def test_table_marks_a_partial_snapshot_share_as_partial(self):
        measured = self._measure(self._manifest_with_usage(
            availability={"subagents": "partial", "orchestrator": "complete"},
        ))

        assert render._table_row(measured)[8] == "partial 80.0"


class TestSkippedStepsSanitize:
    """PII: `step` is an integer, and `title`/`condition` are drawn from
    the pipeline's fixed step-title and skip-condition vocabulary — never
    user-authored text.
    """

    def test_a_well_formed_payload_survives_field_for_field(self):
        manifest = _manifest("run-1")
        manifest["availability"]["skipped_steps"] = True
        manifest["skipped_steps"] = _skipped_steps_payload()

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["skipped_steps"] == _skipped_steps_payload()

    def test_a_measured_zero_skips_is_an_empty_list_not_missing(self):
        manifest = _manifest("run-1")
        manifest["availability"]["skipped_steps"] = True
        manifest["skipped_steps"] = []

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["skipped_steps"] == []

    def test_an_entry_without_a_step_number_is_dropped_not_the_section(self):
        manifest = _manifest("run-1")
        manifest["availability"]["skipped_steps"] = True
        manifest["skipped_steps"] = [
            {"title": "no step here", "condition": "x"},
            {"step": 10, "title": "Decision Critic", "condition": "quick_mode_enabled"},
        ]

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["skipped_steps"] == [
            {"step": 10, "title": "Decision Critic", "condition": "quick_mode_enabled"}
        ]

    def test_a_non_list_payload_is_missing_not_a_crash(self):
        manifest = _manifest("run-1")
        manifest["availability"]["skipped_steps"] = True
        manifest["skipped_steps"] = {"step": 2}

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["skipped_steps"] is None


class TestDependencyRefreshSanitize:
    """PII: none of `_sanitize_dependency_refresh`'s fields carry
    user-authored prose — see the sanitizer's own docstring for the full
    accounting.
    """

    def test_a_well_formed_payload_survives_field_for_field(self):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = _dependency_refresh_payload()

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["dependency_refresh"] == _dependency_refresh_payload()

    def test_requested_without_a_report_survives_with_only_two_fields(self):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = {"requested": True, "reported": False}

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["dependency_refresh"] == {
            "requested": True, "reported": False,
        }

    def test_a_precheck_refusal_carries_bounded_dirty_files(
        self,
    ):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = {
            "requested": True,
            "reported": False,
            "precheck": {
                "tracked_files_dirty": True,
                "dirty_files": [f"file-{i}.txt" for i in range(25)],
            },
        }

        sanitized = sanitize._sanitize_manifest(manifest)

        section = sanitized["dependency_refresh"]
        assert section["precheck"]["tracked_files_dirty"] is True
        assert len(section["precheck"]["dirty_files"]) == contracts._MAX_DIRTY_FILES

    def test_historical_unrecognized_skip_reason_reads_as_invalid(self):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = {
            "requested": True, "reported": False,
            "skipped": True, "skipped_reason": "not_a_real_reason",
            "dirty_files": [],
        }

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["dependency_refresh"]["skipped_reason"] == "invalid"

    def test_historical_skip_only_shape_stays_historical(self):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = {
            "requested": True,
            "reported": False,
            "skipped": True,
            "skipped_reason": "dirty_worktree",
            "dirty_files": ["composer.lock"],
        }

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["dependency_refresh"] == {
            "requested": True,
            "reported": False,
            "skipped": True,
            "skipped_reason": "dirty_worktree",
            "dirty_files": ["composer.lock"],
        }

    def test_historical_non_string_skip_reason_is_omitted(self):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = {
            "requested": True,
            "reported": False,
            "skipped": True,
            "skipped_reason": [],
            "dirty_files": [],
        }

        sanitized = sanitize._sanitize_manifest(manifest)

        section = sanitized["dependency_refresh"]
        assert section["skipped"] is True
        assert "skipped_reason" not in section

    def test_historical_verification_and_a_self_report_remain_measurable(self):
        """Historical manifests retain their retired verification evidence."""
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = {
            "requested": True,
            "reported": True,
            "verification": {
                "report_present": True,
                "commands_allowed": True,
                "disallowed_commands": [],
                "tracked_files_dirty": False,
                "verification_failed": False,
            },
            "status": "completed",
            "tracked_files_dirty": False,
            "commands": [
                {"directory": ".", "command": "npm ci", "exit_status": "ok"},
            ],
        }

        sanitized = sanitize._sanitize_manifest(manifest)

        section = sanitized["dependency_refresh"]
        assert section["verification"]["commands_allowed"] is True
        assert section["status"] == "completed"
        assert section["commands"] == [
            {"directory": ".", "command": "npm ci", "exit_status": "ok"},
        ]

    def test_an_unrecognized_status_reads_as_invalid(self):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = _dependency_refresh_payload(
            status="did-things"
        )

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["dependency_refresh"]["status"] == "invalid"

    def test_a_non_dict_command_entry_is_dropped_not_the_section(self):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = _dependency_refresh_payload(
            commands=[
                "rm -rf /",
                {
                    "directory": ".", "command": "composer install",
                    "exit_status": "ok",
                },
            ],
        )

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["dependency_refresh"]["commands"] == [
            {"directory": ".", "command": "composer install", "exit_status": "ok"},
        ]

    def test_an_unrecognized_exit_status_reads_as_invalid(self):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = _dependency_refresh_payload(
            commands=[{"directory": ".", "command": "x", "exit_status": "great"}],
        )

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["dependency_refresh"]["commands"] == [
            {"directory": ".", "command": "x", "exit_status": "invalid"},
        ]

    def test_a_non_string_exit_status_reads_as_invalid(self):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = _dependency_refresh_payload(
            commands=[{
                "directory": ".",
                "command": "x",
                "exit_status": [],
            }],
        )

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["dependency_refresh"]["commands"] == [{
            "directory": ".",
            "command": "x",
            "exit_status": "invalid",
        }]

    def test_missing_requested_or_reported_is_a_missing_section(self):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = {"reported": True}

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["dependency_refresh"] is None

    def test_a_non_dict_payload_is_missing_not_a_crash(self):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        manifest["dependency_refresh"] = "not a dict"

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["dependency_refresh"] is None


class TestDependencyRefreshDefensiveBounds:
    """Hand-edited manifests cannot bypass the consumer's string bound."""

    def test_an_oversized_command_string_becomes_none(
        self,
    ):
        manifest = _manifest("run-1")
        manifest["availability"]["dependency_refresh"] = True
        oversized_command = "x" * 5000
        manifest["dependency_refresh"] = _dependency_refresh_payload(
            commands=[
                {
                    "directory": ".", "command": oversized_command,
                    "exit_status": "ok",
                },
            ],
        )

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["dependency_refresh"]["commands"] == [
            {"directory": ".", "command": None, "exit_status": "ok"},
        ]


_DERIVED_MARKDOWN_NAME = "reviewer_markdown"


class TestDerivedMarkdownOutcomeSanitize:
    """`reviewer_markdown` and `findings_markdown` share one sanitizer
    (`_sanitize_derived_markdown_outcome`), mapped under both names in
    `sanitize._OPTIONAL_SECTION_SANITIZERS`, so a bug in the shared
    function shows up identically on either key. The cases here run
    through `reviewer_markdown`; the wiring of both keys is pinned by
    `test_every_producer_recognized_derived_markdown_status_survives`
    and `TestOptionalSectionsReachMeasureRun`.

    PII: none. `ran`/`status` are booleans and a closed four-value
    vocabulary; `written`/`expected` are plain non-negative file counts.
    """

    def test_a_well_formed_payload_survives_field_for_field(self):
        manifest = _manifest("run-1")
        manifest["availability"][_DERIVED_MARKDOWN_NAME] = True
        manifest[_DERIVED_MARKDOWN_NAME] = _derived_markdown_payload()

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized[_DERIVED_MARKDOWN_NAME] == _derived_markdown_payload()

    def test_not_run_is_a_measured_outcome_not_missing(self):
        """`ran: False, status: "not_run"` is the DEFAULT state pipeline
        state carries before either render seam ever runs — a legitimate
        measured outcome (the run never reached that step), distinct
        from the section being entirely absent from the manifest."""
        manifest = _manifest("run-1")
        manifest["availability"][_DERIVED_MARKDOWN_NAME] = True
        manifest[_DERIVED_MARKDOWN_NAME] = _derived_markdown_payload(
            ran=False, written=0, expected=0, status="not_run",
        )

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized[_DERIVED_MARKDOWN_NAME] == {
            "ran": False, "written": 0, "expected": 0, "status": "not_run",
        }

    def test_ran_true_with_not_run_status_is_an_inconsistent_shape(self):
        manifest = _manifest("run-1")
        manifest["availability"][_DERIVED_MARKDOWN_NAME] = True
        manifest[_DERIVED_MARKDOWN_NAME] = _derived_markdown_payload(ran=True, status="not_run")

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized[_DERIVED_MARKDOWN_NAME] is None

    def test_complete_status_requires_written_to_equal_expected(self):
        manifest = _manifest("run-1")
        manifest["availability"][_DERIVED_MARKDOWN_NAME] = True
        manifest[_DERIVED_MARKDOWN_NAME] = _derived_markdown_payload(
            status="complete", written=1, expected=2,
        )

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized[_DERIVED_MARKDOWN_NAME] is None

    def test_a_negative_count_is_a_missing_section(self):
        manifest = _manifest("run-1")
        manifest["availability"][_DERIVED_MARKDOWN_NAME] = True
        manifest[_DERIVED_MARKDOWN_NAME] = _derived_markdown_payload(written=-1)

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized[_DERIVED_MARKDOWN_NAME] is None

    def test_a_non_dict_payload_is_missing_not_a_crash(self):
        manifest = _manifest("run-1")
        manifest["availability"][_DERIVED_MARKDOWN_NAME] = True
        manifest[_DERIVED_MARKDOWN_NAME] = "not a dict"

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized[_DERIVED_MARKDOWN_NAME] is None


class TestPreRetrofitManifestsProjectHonestly:
    """Task 13: the availability retrofit for `dependency_refresh`,
    `reviewer_markdown`, and `findings_markdown`.

    Runs written BEFORE this retrofit landed already had
    `manifest["dependency_refresh"]`/`manifest["reviewer_markdown"]` on
    disk — both sections already existed; only their
    `availability["<name>"]` flag was missing (`findings_markdown` did
    not exist at all — Task 7 deferred its manifest section entirely to
    this task). This class probes those real pre-retrofit shapes
    directly: `TestOptionalSectionAvailabilityConsistency` does not cover
    "the availability KEY is entirely absent (not `False`) while a real
    payload sits beside it," which is the actual shape every pre-Task-13
    manifest on disk carries for `dependency_refresh`/`reviewer_markdown`.
    The loop branch is name-independent, so `dependency_refresh` stands
    for both sections.
    """

    def test_dependency_refresh_with_a_flagless_but_real_payload_is_recovered(
        self,
    ):
        """The pre-feature skip-when-undeclared rule in
        `_sanitize_optional_sections` only skips a section that is BOTH
        keyless in `availability` AND absent from the manifest body. Here
        the payload is present (as it always has been) and only the flag
        is missing, so the loop falls through to the sanitizer and
        DERIVES `True` from what actually parsed — a legitimate recovery,
        not a fabrication: the run really did measure dependency
        refresh, the old manifest just never said so in `availability`."""
        manifest = _manifest("run-1")
        assert "dependency_refresh" not in manifest["availability"]
        manifest["dependency_refresh"] = _dependency_refresh_payload()

        sanitized = sanitize._sanitize_manifest(manifest)

        assert sanitized["availability"]["dependency_refresh"] is True
        assert sanitized["dependency_refresh"] == _dependency_refresh_payload()

    def test_dependency_refresh_with_a_flagless_null_payload_reads_missing_not_zero(
        self,
    ):
        """The majority pre-retrofit shape: `dependency_refresh` was
        never requested, so the producer wrote the top-level key as JSON
        `null`. No flag, and a `None` payload — the sanitizer must NOT
        fabricate a measured zero, nor a measured `false`; it reads
        exactly as undeclared, the same as a section that is absent
        altogether, and stays that way when the sanitized manifest (which
        writes every section key) is ingested again."""
        manifest = _manifest("run-1")
        assert "dependency_refresh" not in manifest["availability"]
        manifest["dependency_refresh"] = None

        for _ in range(2):
            manifest = sanitize._sanitize_manifest(manifest)
            assert "dependency_refresh" not in manifest["availability"]
            assert manifest["dependency_refresh"] is None

class TestOptionalSectionsReachMeasureRun:
    """End-to-end: `measure_run` builds on `_sanitize_manifest`, so the
    fix must be visible at the same surface cohort/render consume."""

    def test_every_optional_section_reaches_measure_run(self):
        """Per-run visibility for each section comes free from the one
        `_sanitize_optional_sections` loop `measure_run` builds on — no
        dedicated cohort aggregation was added for them, and none was
        needed for this to be true. `reviewer_markdown` and
        `findings_markdown` share one sanitizer; both keys are set here
        so the map's wiring of each is pinned end to end."""
        manifest = _manifest("run-1")
        manifest["availability"]["worktree_hygiene"] = True
        manifest["availability"]["usage"] = True
        manifest["availability"]["skipped_steps"] = True
        manifest["availability"]["dependency_refresh"] = True
        manifest["availability"]["reviewer_markdown"] = True
        manifest["availability"]["findings_markdown"] = True
        manifest["worktree_hygiene"] = _worktree_hygiene_payload()
        manifest["usage"] = _usage_snapshot_payload()
        manifest["skipped_steps"] = _skipped_steps_payload()
        manifest["dependency_refresh"] = _dependency_refresh_payload()
        manifest["reviewer_markdown"] = _derived_markdown_payload()
        manifest["findings_markdown"] = _derived_markdown_payload()

        measured = measure_run(
            manifest, Path("/nonexistent"), include_transcripts=False
        )

        assert measured["worktree_hygiene"] is not None
        assert measured["usage"] is not None
        assert measured["skipped_steps"] == _skipped_steps_payload()
        assert measured["dependency_refresh"] == _dependency_refresh_payload()
        assert measured["reviewer_markdown"] == _derived_markdown_payload()
        assert measured["findings_markdown"] == _derived_markdown_payload()
        assert measured["availability"]["dependency_refresh"] is True
        assert measured["availability"]["reviewer_markdown"] is True
        assert measured["availability"]["findings_markdown"] is True

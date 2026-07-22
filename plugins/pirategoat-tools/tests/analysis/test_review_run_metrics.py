"""Tests for supported review-run discovery, measurement, and cohorts."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "analysis" / "review_run_metrics.py"
TELEMETRY_SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "review" / "telemetry.py"
DISPATCH_STATUS_SCRIPT_PATH = (
    PLUGIN_ROOT / "scripts" / "review" / "dispatch_status.py"
)

sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "analysis"))

import review_metrics as _mod  # noqa: E402
from review_metrics import cli, contracts, load, measure, render, sanitize  # noqa: E402

load_runs = _mod.load_runs
measure_run = _mod.measure_run
aggregate_cohort = _mod.aggregate_cohort
format_table = _mod.format_table
format_json = _mod.format_json
main = _mod.main


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


def test_metrics_uses_canonical_telemetry_contract():
    telemetry = _load_telemetry_module()
    dispatch_status = _load_dispatch_status_module()

    assert contracts.DEFAULT_LOG_DIR == Path(telemetry.LOG_DIR)
    assert (
        contracts._DISPATCHED_STATUSES
        is contracts._DISPATCH_STATUS_CONTRACT.DISPATCHED_STATUSES
    )
    assert (
        contracts._SUPPORTED_DISPATCH_STATUSES
        is contracts._DISPATCH_STATUS_CONTRACT.SUPPORTED_DISPATCH_STATUSES
    )
    assert contracts._DISPATCHED_STATUSES == dispatch_status.DISPATCHED_STATUSES
    assert (
        contracts._SUPPORTED_DISPATCH_STATUSES
        == dispatch_status.SUPPORTED_DISPATCH_STATUSES
    )


def _manifest(
    run_id: str = "run-1",
    *,
    started_at: str | None = "2026-07-19T10:00:00+00:00",
    ended_at: str | None = "2026-07-19T10:01:00+00:00",
    session_id: str | None = None,
) -> dict:
    return {
        "schema_version": 1,
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
                    "planner_signals": [],
                    "configured_planner_checks": [],
                    "change": "unchanged",
                },
                "security-reviewer": {
                    "initial_status": "DISPATCH",
                    "final_status": "SKIPPED_TRIAGE",
                    "planner_signals": [],
                    "configured_planner_checks": [],
                    "change": "removed",
                },
            },
        },
        "coverage": {
            "changed": ["src/a.py", "vendor/generated.js"],
            "reviewable": ["src/a.py"],
            "by_agent": {"code-reviewer": ["src/a.py"]},
            "assigned": ["src/a.py"],
            "excluded": [
                {"path": "vendor/generated.js", "reason": "noise_filtered"}
            ],
            "uncovered": [],
            "semantics": "generated_scope_not_proof_of_model_read",
        },
        "outcome": {
            "summary": {
                "total_duration_ms": 60_000,
                "total_agent_issues": 3,
                "final_issues": 1,
            },
            "pipeline_status": "complete",
            "verdict": "COMMENT",
            "critic_verdict": "STAND",
        },
        "availability": {"pipeline": True, "transcript": False, "coverage": True},
    }


def _running_manifest(run_id: str = "run-1") -> dict:
    manifest = _manifest(run_id)
    manifest["status"] = "running"
    manifest["run"]["ended_at"] = None
    manifest["outcome"]["summary"] = {}
    return manifest


def _pipeline_start(
    run_id: str = "run-1",
    *,
    timestamp: str = "2026-07-19T10:00:00+00:00",
) -> dict:
    return {
        "schema_version": 1,
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
        "schema_version": 1,
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
        "schema_version": 1,
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
        "schema_version": 1,
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
        "schema_version": 1,
        "run_id": run_id,
        "event": "agent_complete",
        "timestamp": timestamp,
        "agent": agent,
        "duration_ms": 10_000,
        "verdict": "approve",
        "issue_count": 0,
        "severities": {},
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
            "summary": {"total_duration_ms": 60_000, "total_agent_issues": 2},
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
        "schema_version": 2,
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


class TestLoadRuns:
    def test_prefers_valid_manifest_without_loading_sibling_jsonl(self, tmp_path):
        manifest = _manifest("manifest-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", _legacy_events("jsonl-run"))

        runs = load_runs(tmp_path)

        assert [run["run"]["id"] for run in runs] == ["manifest-run"]
        assert "legacy_log_no_manifest" not in runs[0].get("warnings", [])

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
        assert run["coverage"] == manifest["coverage"]
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

    def test_running_sidecar_overlays_latest_completion_revision(self, tmp_path):
        telemetry_mod = _load_telemetry_module()
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        telemetry = telemetry_mod.ReviewTelemetry(
            str(output_dir), log_dir=str(tmp_path)
        )
        telemetry.start(run_id="revision-run")
        telemetry.log_agent_start(agent_name="code-reviewer", domain="code")
        telemetry.log_agent_complete(
            agent_name="code-reviewer",
            verdict="comment",
            issue_count=1,
            severities={"medium": 1},
        )
        telemetry.log_step(step=6, phase="EXECUTION", title="Run Reviewers")
        telemetry.log_agent_complete(
            agent_name="code-reviewer", verdict="approve", issue_count=0
        )

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "partial"
        assert measured["lifecycle"]["started_events"] == 1
        assert measured["lifecycle"]["completed_events"] == 1
        assert run["agents"]["incomplete"] == []
        assert run["agents"]["completed"][0]["timestamp"] == [
            event["timestamp"]
            for event in _read_jsonl_for_test(Path(telemetry.log_path))
            if event["event"] == "agent_complete"
        ][-1]
        assert run["agents"]["completed"][0]["verdict"] == "unavailable"

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

    @pytest.mark.parametrize(
        "domain",
        [
            pytest.param({"unexpected": "object"}, id="object"),
            pytest.param(7, id="integer"),
        ],
    )
    def test_malformed_nonnull_domain_remains_invalid_end_to_end(
        self, tmp_path, domain
    ):
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

    @pytest.mark.parametrize(
        "events",
        [
            pytest.param(
                [
                    _pipeline_start("running-run"),
                    _step(
                        "running-run",
                        timestamp="2026-07-19T09:59:59+00:00",
                    ),
                ],
                id="step-before-start",
            ),
            pytest.param(
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
                id="later-step-regresses",
            ),
            pytest.param(
                [
                    _pipeline_start("running-run"),
                    _step(
                        "running-run",
                        timestamp="2026-07-19T10:00:10+00:00",
                    ),
                    _pipeline_end(
                        "running-run",
                        timestamp="2026-07-19T10:00:09+00:00",
                    ),
                ],
                id="end-before-last-step",
            ),
        ],
    )
    def test_running_overlay_rejects_regressing_control_plane_timeline(
        self, tmp_path, events
    ):
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", events)

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert "running_lifecycle_overlay_invalid" in run["warnings"]
        assert run["dispatch"] == manifest["dispatch"]
        assert run["coverage"] == manifest["coverage"]

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

    @pytest.mark.parametrize(
        "events",
        [
            pytest.param(
                [
                    _pipeline_start("running-run"),
                    _pipeline_end("running-run"),
                    _agent_start("code-reviewer", run_id="running-run"),
                ],
                id="lifecycle-after-end",
            ),
            pytest.param(
                [
                    _pipeline_start("running-run"),
                    _pipeline_end("running-run"),
                    _step(
                        "running-run",
                        timestamp="2026-07-19T10:00:40+00:00",
                    ),
                ],
                id="step-after-end",
            ),
            pytest.param(
                [
                    _pipeline_start("running-run"),
                    _agent_start("code-reviewer", run_id="running-run"),
                    _pipeline_end("running-run"),
                    _pipeline_end(
                        "running-run",
                        timestamp="2026-07-19T10:00:40+00:00",
                    ),
                ],
                id="duplicate-end",
            ),
        ],
    )
    def test_running_overlay_rejects_nonterminal_or_duplicate_end(
        self, tmp_path, events
    ):
        manifest = _running_manifest("running-run")
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(tmp_path / "review.jsonl", events)

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert "running_lifecycle_overlay_invalid" in run["warnings"]
        assert run["dispatch"] == manifest["dispatch"]
        assert run["coverage"] == manifest["coverage"]

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
        assert run["coverage"] == manifest["coverage"]
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
        assert run["coverage"] == manifest["coverage"]

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
        assert run["coverage"] == manifest["coverage"]
        assert run["outcome"] == sanitize._sanitize_outcome(manifest["outcome"])
        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert "running_lifecycle_overlay_invalid" in run["warnings"]

    def test_running_overlay_rejects_sidecar_prefix_mismatch(self, tmp_path):
        manifest = _running_manifest("running-run")
        manifest["agents"] = {
            "started": [_agent_start("code-reviewer", run_id="running-run")],
            "completed": [],
            "incomplete": ["code-reviewer"],
        }
        _write_manifest(tmp_path / "review.manifest.json", manifest)
        _write_jsonl(
            tmp_path / "review.jsonl",
            [
                _pipeline_start("running-run"),
                _agent_start("security-reviewer", run_id="running-run"),
            ],
        )

        [run] = load_runs(tmp_path)
        measured = measure_run(run, tmp_path, include_transcripts=False)

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
        self, tmp_path, monkeypatch
    ):
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
        def unexpected_read(_path):
            raise AssertionError("complete manifests must not read sibling JSONL")

        monkeypatch.setattr(load, "_read_jsonl_strict", unexpected_read)

        [run] = load_runs(tmp_path)

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
        assert run["dispatch"] is None
        assert run["coverage"] is None
        assert run["warnings"] == ["legacy_log_no_manifest"]
        serialized = json.dumps(run)
        assert "PRIVATE PROMPT" not in serialized
        assert "PRIVATE SOURCE" not in serialized
        assert "PRIVATE FINDING" not in serialized
        assert "PRIVATE TOOL BODY" not in serialized
        assert "snapshot" not in serialized

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
            ("schema_version", None),
            ("schema_version", True),
            ("schema_version", 1.0),
            ("schema_version", 2),
            ("status", None),
            ("status", "success"),
        ],
        ids=[
            "missing-version",
            "boolean-version",
            "float-version",
            "future-version",
            "missing-status",
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

    @pytest.mark.parametrize("status", ["running", "complete"])
    def test_supported_sidecar_status_suppresses_sibling_legacy_log(
        self, tmp_path, status
    ):
        manifest = _manifest("sidecar-run")
        manifest["status"] = status
        if status == "running":
            manifest["run"]["ended_at"] = None
            manifest["dispatch"] = _unavailable_dispatch()
            manifest["coverage"] = None
            manifest["availability"]["coverage"] = False
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
        assert measured["metric_availability"]["coverage"] == "complete"

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
                "coverage", False
            ),
            lambda manifest: manifest["dispatch"].__setitem__(
                "planner_candidate_count", float("inf")
            ),
            lambda manifest: manifest["coverage"].__setitem__(
                "assigned", ["outside.py"]
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
        assert run["coverage"] == manifest["coverage"]
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
            (output_dir / "dispatch-plan.initial.json").write_text(
                json.dumps(plan(initial_state))
            )
        if final_state != "missing":
            (output_dir / "dispatch-plan.json").write_text(
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
        assert run["coverage"] == producer_manifest["coverage"]
        assert run["outcome"] == sanitize._sanitize_outcome(
            producer_manifest["outcome"]
        )
        assert measured["metric_availability"]["lifecycle"] == "complete"
        assert measured["lifecycle"]["started_events"] == 1
        assert measured["lifecycle"]["completed_events"] == 0
        assert measured["lifecycle"]["incomplete_identities"] == [
            "security-reviewer"
        ]

    @pytest.mark.parametrize(
        "initial_names,final_names,planner_count,final_count",
        [
            (["code-reviewer"], ["code-reviewer", "security-reviewer"], 1, 2),
            (["code-reviewer", "security-reviewer"], ["code-reviewer"], 2, 1),
        ],
        ids=["agent-added", "agent-removed"],
    )
    def test_agent_set_mismatch_sidecar_remains_authoritative_and_partial(
        self,
        tmp_path,
        initial_names,
        final_names,
        planner_count,
        final_count,
    ):
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

        (output_dir / "dispatch-plan.initial.json").write_text(
            json.dumps(plan(initial_names))
        )
        (output_dir / "dispatch-plan.json").write_text(
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
        assert run["coverage"] == producer_manifest["coverage"]
        assert run["outcome"] == sanitize._sanitize_outcome(
            producer_manifest["outcome"]
        )
        assert measured["metric_availability"]["dispatch"] == "partial"
        assert measured["metric_availability"]["coverage"] == "complete"
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
        (output_dir / "dispatch-plan.initial.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {"name": "z-reviewer", "status": "SKIPPED_TRIAGE"},
                        {"name": "a-reviewer", "status": "DISPATCH"},
                    ]
                }
            )
        )
        (output_dir / "dispatch-plan.json").write_text(
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
            lambda dispatch: dispatch.__setitem__("invalid_reason_codes", []),
            lambda dispatch: dispatch["invalid_reason_codes"].append(
                "extra_reason"
            ),
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
            "extra-reason",
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

    @pytest.mark.parametrize(
        "invalid_name",
        [
            "security reviewer",
            "security/reviewer",
            "reviewer: private prose",
            "Security-reviewer",
            "security_reviewer",
        ],
        ids=["space", "path", "prose", "uppercase", "underscore"],
    )
    def test_duplicate_dispatch_allowance_rejects_nonproducer_agent_names(
        self, tmp_path, invalid_name
    ):
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
        [
            "Users/person/private-repo",
            r"C:\Users\person\private-repo",
            "run|forged-column",
            "run<script>alert</script>",
            "run" + "x" * 254,
        ],
        ids=["posix-path", "windows-path", "pipe", "markup", "too-long"],
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
        [
            "550e8400-e29b-41d4-a716-446655440000",
            "legacy-deadbeef01234567",
            "a" * 256,
        ],
        ids=["uuid", "legacy", "boundary-length"],
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
        first["coverage"] = {
            "changed": [
                "src/a.py",
                "src/b.py",
                "src/c.py",
                "src/d.py",
                "vendor/a.js",
                "vendor/b.js",
            ],
            "reviewable": ["src/a.py", "src/b.py", "src/c.py", "src/d.py"],
            "by_agent": {"code-reviewer": ["src/a.py", "src/b.py"]},
            "assigned": ["src/a.py", "src/b.py"],
            "excluded": [
                {"path": "vendor/a.js", "reason": "noise_filtered"},
                {"path": "vendor/b.js", "reason": "noise_filtered"},
            ],
            "uncovered": ["src/c.py", "src/d.py"],
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
        for name in ("changed", "reviewable", "assigned", "uncovered", "excluded"):
            second["coverage"][name].reverse()
        second["coverage"]["by_agent"]["code-reviewer"].reverse()
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

    @pytest.mark.parametrize("event_family", ["steps", "started", "completed"])
    def test_order_sensitive_event_reordering_remains_a_conflict(
        self, tmp_path, event_family
    ):
        first = _manifest("duplicate-run")
        if event_family == "steps":
            first["steps"] = [
                {"event": "step", "step": 1, "timestamp": "2026-07-19T10:00:01Z"},
                {"event": "step", "step": 2, "timestamp": "2026-07-19T10:00:02Z"},
            ]
        elif event_family == "started":
            first["agents"]["started"] = [
                {"event": "agent_start", "agent": "code-reviewer"},
                {"event": "agent_start", "agent": "security-reviewer"},
            ]
        else:
            first["agents"]["completed"] = [
                {"event": "agent_complete", "agent": "code-reviewer"},
                {"event": "agent_complete", "agent": "security-reviewer"},
            ]
        second = copy.deepcopy(first)
        target = (
            second["steps"]
            if event_family == "steps"
            else second["agents"][event_family]
        )
        target.reverse()
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
        second["outcome"]["summary"]["final_issues"] = 99
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
        assert cohort["coverage"]["changed"] is None
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
        second["outcome"]["summary"]["final_issues"] = 2
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
        second["outcome"]["summary"]["final_issues"] = 2
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

    @pytest.mark.parametrize(
        "invalid_count",
        [float("inf"), 10**1_000],
        ids=["infinite-float", "unbounded-integer"],
    )
    def test_invalid_numeric_fields_degrade_availability_without_crashing(
        self, tmp_path, invalid_count
    ):
        manifest = _manifest("nonfinite")
        manifest["dispatch"]["planner_candidate_count"] = invalid_count
        _write_manifest(tmp_path / "nonfinite.manifest.json", manifest)

        [run] = load_runs(tmp_path)

        assert run["dispatch"] is None
        assert measure_run(run, tmp_path, include_transcripts=False)[
            "metric_availability"
        ]["dispatch"] == "missing"


class TestMeasureRun:
    def test_running_coverage_snapshot_is_a_partial_observation(self):
        manifest = _running_manifest("running-coverage")

        measured = measure_run(
            manifest, Path("/nonexistent"), include_transcripts=False
        )
        cohort = aggregate_cohort([measured])

        assert measured["coverage"] == manifest["coverage"]
        assert measured["metric_availability"]["coverage"] == "partial"
        assert "partial 1/1/0" in format_table([measured], cohort)
        assert cohort["coverage"] == {
            "changed": None,
            "reviewable": None,
            "assigned": None,
            "excluded": None,
            "uncovered": None,
            "assignment_rate": None,
            "available_runs": 0,
            "semantics": "generated_scope_not_proof_of_model_read",
            "availability": {
                "available": 1,
                "complete": 0,
                "partial": 1,
                "missing": 0,
                "disabled": 0,
            },
        }

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
        assert measured["coverage"] == manifest["coverage"]
        assert measured["outcome"] == manifest["outcome"]
        assert measured["transcript"]["available"] is False
        assert measured["transcript"]["usage"] is None
        assert measured["metric_availability"]["transcript"] == "missing"
        assert manifest["availability"] == {
            "pipeline": True,
            "transcript": False,
            "coverage": True,
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

    @pytest.mark.parametrize(
        "started,ended",
        [
            ("2026-07-19T10:00:00", "2026-07-19T10:01:00"),
            ("2026-07-19T10:00:00+00:00", "2026-07-19T10:01:00"),
        ],
        ids=["both-naive", "mixed-aware-naive"],
    )
    def test_naive_timestamps_do_not_supply_wall_time(
        self, tmp_path, started, ended
    ):
        manifest = _manifest(started_at=started, ended_at=ended)
        manifest["outcome"]["summary"].pop("total_duration_ms")

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["wall_time_ms"] is None
        assert measured["metric_availability"]["wall_time"] == "missing"

    @pytest.mark.parametrize("verdict", ["STAND", "REVISE", "ESCALATE"])
    def test_critic_is_complete_only_for_exact_supported_verdicts(
        self, tmp_path, verdict
    ):
        manifest = _manifest()
        manifest["outcome"]["critic_verdict"] = verdict

        measured = measure_run(manifest, tmp_path, include_transcripts=False)
        cohort = aggregate_cohort([measured])

        assert measured["metric_availability"]["critic"] == "complete"
        assert measured["outcome"]["critic_verdict"] == verdict
        assert cohort["critic"]["verdicts"] == {verdict: 1}

    @pytest.mark.parametrize(
        "verdict",
        [None, "unavailable", "stand", "ERROR", " STAND "],
        ids=["missing", "sentinel", "lowercase", "failure", "padded"],
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

    @pytest.mark.parametrize("status_field", ["initial_status", "final_status"])
    @pytest.mark.parametrize(
        "invalid_status",
        [
            pytest.param("__missing__", id="missing"),
            None,
            "",
            "UNKNOWN",
            "DISPATCHED",
            [],
            {},
            [{"nested": []}],
            {"nested": []},
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

    @pytest.mark.parametrize(
        "status,dispatched",
        [
            ("DISPATCH", True),
            ("DISPATCH_OVERRIDE", True),
            ("SKIPPED", False),
            ("SKIPPED_OVERRIDE", False),
            ("SKIPPED_QUICK_MODE", False),
            ("SKIPPED_TRIAGE", False),
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

    @pytest.mark.parametrize("status_field", ["initial_status", "final_status"])
    @pytest.mark.parametrize(
        "invalid_status",
        [
            pytest.param("__missing__", id="missing"),
            None,
            "",
            "UNKNOWN",
            "DISPATCHED",
            [],
            {},
            [{"nested": []}],
            {"nested": []},
        ],
    )
    def test_final_only_projection_rejects_incomplete_statuses(
        self, tmp_path, status_field, invalid_status
    ):
        manifest = _manifest()
        manifest["dispatch"] = _final_only_dispatch()
        decision = manifest["dispatch"]["agents"]["code-reviewer"]
        if invalid_status == "__missing__":
            decision.pop(status_field)
        else:
            decision[status_field] = invalid_status
        manifest["dispatch"]["planner_candidate_count"] = 0
        manifest["dispatch"]["final_dispatch_count"] = 0

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    @pytest.mark.parametrize(
        "invalid_status",
        [
            pytest.param("__missing__", id="missing"),
            None,
            "",
            "UNKNOWN",
            "DISPATCHED",
            [],
            {},
            [{"nested": []}],
            {"nested": []},
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

    @pytest.mark.parametrize(
        "invalid_status",
        [
            "DISPATCHED",
            [],
            {},
            [{"nested": []}],
            {"nested": []},
        ],
    )
    def test_invalid_sidecar_dispatch_status_falls_back_to_legacy(
        self, tmp_path, invalid_status
    ):
        manifest = _manifest("sidecar-run")
        manifest["dispatch"]["agents"]["code-reviewer"][
            "final_status"
        ] = invalid_status
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
            pytest.param(None, id="null"),
            pytest.param(7, id="integer"),
            pytest.param(("security-reviewer",), id="tuple"),
            pytest.param("", id="empty"),
            pytest.param("Security-reviewer", id="uppercase"),
            pytest.param("security_reviewer", id="underscore"),
            pytest.param("security/reviewer", id="path"),
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

    def test_agent_set_mismatch_rejects_unhashable_projection_identity(self):
        class UnhashableIdentityProjection(dict):
            def items(self):
                return [([], "DISPATCH")]

        dispatch = _mismatched_dispatch()
        dispatch["plan_projections"]["final_plan"] = (
            UnhashableIdentityProjection()
        )

        assert sanitize._sanitize_dispatch(dispatch) is None

    @pytest.mark.parametrize("field", ["identity", "status"])
    def test_agent_set_mismatch_rejects_unhashable_string_subclasses(self, field):
        class UnhashableStr(str):
            __hash__ = None

        dispatch = _mismatched_dispatch()
        if field == "identity":
            class UnhashableIdentityProjection(dict):
                def items(self):
                    return [
                        ("code-reviewer", "DISPATCH"),
                        (UnhashableStr("security-reviewer"), "DISPATCH"),
                    ]

            dispatch["plan_projections"]["final_plan"] = (
                UnhashableIdentityProjection()
            )
        else:
            dispatch["plan_projections"]["final_plan"]["security-reviewer"] = (
                UnhashableStr("DISPATCH")
            )

        assert sanitize._sanitize_dispatch(dispatch) is None

    @pytest.mark.parametrize(
        "invalid_status",
        [
            pytest.param(None, id="null"),
            pytest.param(True, id="boolean"),
            pytest.param(7, id="integer"),
            pytest.param("", id="empty"),
            pytest.param("DISPATCHED", id="unsupported"),
            pytest.param([], id="list"),
            pytest.param({}, id="mapping"),
            pytest.param(["DISPATCH"], id="structured"),
        ],
    )
    def test_agent_set_mismatch_rejects_invalid_projection_status(
        self, tmp_path, invalid_status
    ):
        manifest = _manifest()
        manifest["dispatch"] = _mismatched_dispatch()
        projection = manifest["dispatch"]["plan_projections"]["final_plan"]
        projection["security-reviewer"] = invalid_status

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
            pytest.param(
                lambda dispatch: dispatch.__setitem__("plan_projections", None),
                id="null-object",
            ),
            pytest.param(
                lambda dispatch: dispatch.__setitem__("plan_projections", []),
                id="list-object",
            ),
            pytest.param(
                lambda dispatch: dispatch["plan_projections"].pop(
                    "planner_baseline"
                ),
                id="missing-planner",
            ),
            pytest.param(
                lambda dispatch: dispatch["plan_projections"].pop("final_plan"),
                id="missing-final",
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
            pytest.param(
                lambda dispatch: dispatch["plan_projections"].__setitem__(
                    "final_plan", []
                ),
                id="final-list",
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

    @pytest.mark.parametrize(
        "mode",
        ["comparable", "planner-only", "legacy-final", "unavailable"],
    )
    def test_dispatch_rejects_plan_projections_outside_agent_set_mismatch(
        self, tmp_path, mode
    ):
        if mode == "comparable":
            dispatch = _manifest()["dispatch"]
        elif mode == "planner-only":
            dispatch = _planner_only_dispatch()
        elif mode == "legacy-final":
            dispatch = _final_only_dispatch()
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
            pytest.param(
                lambda dispatch: dispatch.__setitem__(
                    "planner_candidate_count", 999_999
                ),
                id="planner-count",
            ),
            pytest.param(
                lambda dispatch: dispatch["plan_projections"]["final_plan"].__setitem__(
                    "Security-reviewer", "DISPATCH"
                ),
                id="unsafe-identity",
            ),
            pytest.param(
                lambda dispatch: dispatch["plan_projections"]["final_plan"].__setitem__(
                    "security-reviewer", []
                ),
                id="structured-status",
            ),
            pytest.param(
                lambda dispatch: dispatch["plan_projections"].__setitem__(
                    "extra", {}
                ),
                id="extra-projection-key",
            ),
            pytest.param(
                lambda dispatch: dispatch["plan_projections"].__setitem__(
                    "final_plan", {"code-reviewer": "DISPATCH"}
                ),
                id="equal-identity-sets",
            ),
            pytest.param(
                lambda dispatch: dispatch.__setitem__(
                    "planner_baseline_available", "malformed"
                ),
                id="malformed-availability",
            ),
            pytest.param(
                lambda dispatch: dispatch.__setitem__(
                    "invalid_reason_codes", None
                ),
                id="malformed-reasons",
            ),
            pytest.param(
                lambda dispatch: dispatch["invalid_reason_codes"].append(
                    "extra_reason"
                ),
                id="extra-mismatch-reason",
            ),
            pytest.param(
                lambda dispatch: (
                    dispatch.pop("plan_projections"),
                    dispatch["invalid_reason_codes"].append("extra_reason"),
                ),
                id="mismatch-reason-without-projections",
            ),
            pytest.param(
                lambda dispatch: dispatch.update(
                    {
                        "comparison_available": True,
                        "invalid_reason_codes": [],
                    }
                ),
                id="out-of-mode-projections",
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
        assert run["coverage"] == manifest["coverage"]
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
        assert run["coverage"] == manifest["coverage"]
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

    @pytest.mark.parametrize(
        "planner_available,final_available,comparison_available",
        [
            (True, False, True),
            (False, True, True),
            (True, True, False),
        ],
        ids=[
            "comparison-without-final",
            "comparison-without-planner",
            "comparison-disabled-for-two-valid-plans",
        ],
    )
    def test_contradictory_dispatch_availability_flags_are_missing(
        self,
        tmp_path,
        planner_available,
        final_available,
        comparison_available,
    ):
        manifest = _manifest()
        manifest["dispatch"].update(
            {
                "planner_baseline_available": planner_available,
                "final_plan_available": final_available,
                "comparison_available": comparison_available,
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["dispatch"] is None
        assert measured["metric_availability"]["dispatch"] == "missing"

    def test_duplicate_dispatch_evidence_is_missing(self, tmp_path):
        manifest = _manifest()
        manifest["dispatch"] = _planner_only_dispatch()
        manifest["dispatch"]["invalid_reason_codes"].append(
            "planner_baseline_duplicate_agents"
        )
        manifest["dispatch"]["duplicate_agent_names"] = {
            "planner_baseline": ["security-reviewer"]
        }

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
        [
            "incomplete-status",
            "changed-status",
            "planner-count",
            "adjustments",
            "change-label",
        ],
        ids=[
            "incomplete-status",
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
        if contradiction == "incomplete-status":
            decision.pop("initial_status")
        elif contradiction == "changed-status":
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
        observed["coverage"] = {
            "changed": [],
            "reviewable": [],
            "by_agent": {},
            "assigned": [],
            "excluded": [],
            "uncovered": [],
            "semantics": "generated_scope_not_proof_of_model_read",
        }
        missing = _manifest("missing")
        missing["dispatch"] = None
        missing["coverage"] = None
        missing["availability"]["coverage"] = False

        measured_observed = measure_run(observed, tmp_path, include_transcripts=False)
        measured_missing = measure_run(missing, tmp_path, include_transcripts=False)

        assert measured_observed["metric_availability"]["dispatch"] == "complete"
        assert measured_observed["metric_availability"]["coverage"] == "complete"
        assert measured_missing["metric_availability"]["dispatch"] == "missing"
        assert measured_missing["metric_availability"]["coverage"] == "missing"

    def test_valid_explicit_empty_coverage_ledger_remains_complete(self, tmp_path):
        manifest = _manifest()
        manifest["coverage"] = {
            "changed": [],
            "reviewable": [],
            "by_agent": {},
            "assigned": [],
            "excluded": [],
            "uncovered": [],
            "semantics": "generated_scope_not_proof_of_model_read",
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["coverage"] == manifest["coverage"]
        assert measured["metric_availability"]["coverage"] == "complete"

    def test_realistic_coverage_ledger_remains_complete(self, tmp_path):
        manifest = _manifest()
        manifest["coverage"] = {
            "changed": ["src/a.py", "src/b.py", "vendor/generated.js"],
            "reviewable": ["src/a.py", "src/b.py"],
            "by_agent": {
                "code-reviewer": ["src/a.py", "vendor/generated.js"],
                "tests-reviewer": ["src/a.py"],
            },
            "assigned": ["src/a.py"],
            "excluded": [
                {"path": "vendor/generated.js", "reason": "noise_filtered"}
            ],
            "uncovered": ["src/b.py"],
            "semantics": "generated_scope_not_proof_of_model_read",
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["coverage"] == manifest["coverage"]
        assert measured["metric_availability"]["coverage"] == "complete"

    def test_duplicate_assigned_path_cannot_report_two_hundred_percent_coverage(
        self, tmp_path
    ):
        manifest = _manifest()
        manifest["coverage"]["assigned"] = ["src/a.py", "src/a.py"]

        measured = measure_run(manifest, tmp_path, include_transcripts=False)
        cohort = aggregate_cohort([measured])

        assert measured["coverage"] is None
        assert measured["metric_availability"]["coverage"] == "missing"
        assert cohort["coverage"]["assignment_rate"] is None
        assert cohort["coverage"]["available_runs"] == 0

    @pytest.mark.parametrize(
        "duplicate_location",
        ["changed", "reviewable", "by-agent", "excluded", "uncovered"],
        ids=["changed", "reviewable", "by-agent", "excluded", "uncovered"],
    )
    def test_coverage_set_like_lists_reject_duplicate_paths(
        self, tmp_path, duplicate_location
    ):
        manifest = _manifest()
        coverage = manifest["coverage"]
        if duplicate_location == "changed":
            coverage["changed"].append("src/a.py")
        elif duplicate_location == "reviewable":
            coverage["reviewable"].append("src/a.py")
        elif duplicate_location == "by-agent":
            coverage["by_agent"]["code-reviewer"].append("src/a.py")
        elif duplicate_location == "excluded":
            coverage["excluded"].append(
                {"path": "vendor/generated.js", "reason": "noise_filtered"}
            )
        else:
            coverage.update(
                {
                    "changed": ["src/a.py", "src/b.py", "vendor/generated.js"],
                    "reviewable": ["src/a.py", "src/b.py"],
                    "assigned": ["src/a.py"],
                    "uncovered": ["src/b.py", "src/b.py"],
                }
            )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["coverage"] is None
        assert measured["metric_availability"]["coverage"] == "missing"

    @pytest.mark.parametrize(
        "assigned,uncovered",
        [([], []), (["src/a.py"], ["src/a.py"])],
        ids=["incomplete-partition", "overlapping-partition"],
    )
    def test_assigned_and_uncovered_must_exactly_partition_reviewable(
        self, tmp_path, assigned, uncovered
    ):
        manifest = _manifest()
        manifest["coverage"]["assigned"] = assigned
        manifest["coverage"]["uncovered"] = uncovered
        if not assigned and not uncovered:
            manifest["coverage"]["by_agent"] = {}

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["coverage"] is None
        assert measured["metric_availability"]["coverage"] == "missing"

    @pytest.mark.parametrize(
        "excluded",
        [
            [],
            [{"path": "src/a.py", "reason": "noise_filtered"}],
            [
                {"path": "vendor/generated.js", "reason": "noise_filtered"},
                {"path": "extra.py", "reason": "noise_filtered"},
            ],
        ],
        ids=["missing", "reviewable-path", "extra-path"],
    )
    def test_exclusions_must_exactly_equal_changed_minus_reviewable(
        self, tmp_path, excluded
    ):
        manifest = _manifest()
        manifest["coverage"]["excluded"] = excluded

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["coverage"] is None
        assert measured["metric_availability"]["coverage"] == "missing"

    def test_reviewable_paths_must_be_a_subset_of_changed_paths(self, tmp_path):
        manifest = _manifest()
        manifest["coverage"].update(
            {
                "reviewable": ["outside.py"],
                "by_agent": {},
                "assigned": [],
                "excluded": [
                    {"path": "src/a.py", "reason": "noise_filtered"},
                    {"path": "vendor/generated.js", "reason": "noise_filtered"},
                ],
                "uncovered": ["outside.py"],
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["coverage"] is None
        assert measured["metric_availability"]["coverage"] == "missing"

    def test_by_agent_paths_must_be_a_subset_of_changed_paths(self, tmp_path):
        manifest = _manifest()
        manifest["coverage"].update(
            {
                "by_agent": {"code-reviewer": ["outside.py"]},
                "assigned": [],
                "uncovered": ["src/a.py"],
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["coverage"] is None
        assert measured["metric_availability"]["coverage"] == "missing"

    def test_by_agent_reviewable_union_must_exactly_equal_assigned(self, tmp_path):
        manifest = _manifest()
        manifest["coverage"]["by_agent"] = {}

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["coverage"] is None
        assert measured["metric_availability"]["coverage"] == "missing"

    @pytest.mark.parametrize(
        "invalid_coverage",
        [
            {},
            {
                "changed": [],
                "reviewable": [],
                "by_agent": {},
                "assigned": [],
                "excluded": [],
                "uncovered": [],
            },
            {
                "changed": [None],
                "reviewable": [],
                "by_agent": {},
                "assigned": [],
                "excluded": [],
                "uncovered": [],
                "semantics": "generated_scope_not_proof_of_model_read",
            },
            {
                "changed": [],
                "reviewable": [],
                "by_agent": {"code-reviewer": [False]},
                "assigned": [],
                "excluded": [],
                "uncovered": [],
                "semantics": "generated_scope_not_proof_of_model_read",
            },
            {
                "changed": ["vendor/a.js"],
                "reviewable": [],
                "by_agent": {},
                "assigned": [],
                "excluded": [{"path": "vendor/a.js"}],
                "uncovered": [],
                "semantics": "generated_scope_not_proof_of_model_read",
            },
            {
                "changed": [],
                "reviewable": [],
                "by_agent": {},
                "assigned": [],
                "excluded": [],
                "uncovered": [],
                "semantics": "proof_of_model_read",
            },
        ],
        ids=[
            "empty-object",
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
        manifest["coverage"] = invalid_coverage

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["coverage"] is None
        assert measured["metric_availability"]["coverage"] == "missing"

    def test_explicit_false_coverage_availability_wins_over_valid_payload(self, tmp_path):
        manifest = _manifest()
        manifest["availability"]["coverage"] = False

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["coverage"] is None
        assert measured["metric_availability"]["coverage"] == "missing"

    def test_recursively_drops_untrusted_noncanonical_payloads(self, tmp_path):
        manifest = _manifest()
        manifest["prompt"] = "PRIVACY_SENTINEL"
        manifest["run"]["tool_body"] = "PRIVACY_SENTINEL"
        manifest["outcome"]["findings"] = {"description": "PRIVACY_SENTINEL"}
        manifest["coverage"]["arbitrary"] = ["PRIVACY_SENTINEL"]
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
                    "issue_count": True,
                    "severities": {"high": float("inf"), "low": 1},
                }
            ],
            "incomplete": [],
        }
        manifest["outcome"]["summary"].update(
            {
                "total_duration_ms": 10**1_000,
                "total_agent_issues": float("inf"),
                "final_issues": float("nan"),
                "changed_files_count": True,
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["wall_time_ms"] is None
        assert measured["metric_availability"]["raw_findings"] == "missing"
        assert measured["metric_availability"]["final_findings"] == "missing"
        assert "total_duration_ms" not in measured["outcome"]["summary"]
        assert "total_agent_issues" not in measured["outcome"]["summary"]
        assert "final_issues" not in measured["outcome"]["summary"]
        assert "changed_files_count" not in measured["outcome"]["summary"]
        assert "step" not in measured["steps"][0]
        assert "duration_since_prev_ms" not in measured["steps"][0]
        assert "budget_target" not in measured["agents"]["started"][0]
        assert measured["agents"]["started"][0]["scope"] == {"paths": []}
        completed = measured["agents"]["completed"][0]
        assert "duration_ms" not in completed
        assert "issue_count" not in completed
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
                "issue_count": 1.9,
            }
        ]
        manifest["outcome"]["summary"].update(
            {
                "total_duration_ms": 0.9,
                "total_agent_issues": 0.9,
                "final_issues": 1.9,
            }
        )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["wall_time_ms"] is None
        assert measured["metric_availability"]["raw_findings"] == "missing"
        assert measured["metric_availability"]["final_findings"] == "missing"
        assert "step" not in measured["steps"][0]
        assert "duration_since_prev_ms" not in measured["steps"][0]
        assert "duration_ms" not in measured["agents"]["completed"][0]
        assert "issue_count" not in measured["agents"]["completed"][0]
        for name in ("total_duration_ms", "total_agent_issues", "final_issues"):
            assert name not in measured["outcome"]["summary"]


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

    def test_running_native_lifecycle_retains_observations_as_partial(self, tmp_path):
        manifest = _manifest(ended_at=None)
        manifest["status"] = "running"
        manifest["agents"] = {
            "started": [_agent_start()],
            "completed": [],
            "incomplete": [],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["metric_availability"]["lifecycle"] == "partial"
        assert measured["lifecycle"] == {
            "started_events": 1,
            "completed_events": 0,
            "incomplete_identities": [],
            "incomplete_count": 0,
            "incomplete_by_agent": {},
            "starts_by_agent": {"code-reviewer": 1},
            "extra_starts_by_agent": {"code-reviewer": 0},
            "retry_overhead": 0,
            "completion_gap": 1,
        }

    def test_running_native_lifecycle_accepts_current_unmatched_multiset(
        self, tmp_path
    ):
        manifest = _manifest(ended_at=None)
        manifest["status"] = "running"
        manifest["agents"] = {
            "started": [
                _agent_start(timestamp="2026-07-19T10:00:10+00:00"),
                _agent_start(timestamp="2026-07-19T10:00:11+00:00"),
            ],
            "completed": [_agent_complete()],
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

    @pytest.mark.parametrize(
        "incomplete",
        [
            pytest.param(
                ["b-reviewer", "b-reviewer"], id="missing-agent-execution"
            ),
            pytest.param(
                ["a-reviewer", "b-reviewer", "b-reviewer", "c-reviewer"],
                id="extra-unstarted-agent",
            ),
            pytest.param(
                ["a-reviewer", "b-reviewer"], id="undercounted-retry"
            ),
            pytest.param(
                ["a-reviewer", "b-reviewer", "b-reviewer", "b-reviewer"],
                id="overcounted-retry",
            ),
        ],
    )
    def test_complete_lifecycle_requires_exact_incomplete_execution_counts(
        self, tmp_path, incomplete
    ):
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

    @pytest.mark.parametrize(
        "started,completed",
        [
            (
                [
                    _agent_start(timestamp="2026-07-19T10:00:20+00:00"),
                    _agent_start(
                        "security-reviewer",
                        timestamp="2026-07-19T10:00:10+00:00",
                    ),
                ],
                [
                    _agent_complete(timestamp="2026-07-19T10:00:40+00:00"),
                    _agent_complete(
                        "security-reviewer",
                        timestamp="2026-07-19T10:00:30+00:00",
                    ),
                ],
            ),
            (
                [
                    _agent_start(timestamp="2026-07-19T10:00:20+00:00"),
                    _agent_start(
                        "security-reviewer",
                        timestamp="2026-07-19T10:00:05+00:00",
                    ),
                    _agent_start(timestamp="2026-07-19T10:00:30+00:00"),
                    _agent_start(
                        "security-reviewer",
                        timestamp="2026-07-19T10:00:15+00:00",
                    ),
                ],
                [
                    _agent_complete(timestamp="2026-07-19T10:00:40+00:00"),
                    _agent_complete(
                        "security-reviewer",
                        timestamp="2026-07-19T10:00:25+00:00",
                    ),
                    _agent_complete(timestamp="2026-07-19T10:00:50+00:00"),
                    _agent_complete(
                        "security-reviewer",
                        timestamp="2026-07-19T10:00:35+00:00",
                    ),
                ],
            ),
        ],
        ids=["distinct-agents-regress-globally", "retries-interleave-globally"],
    )
    def test_parallel_agent_lifecycle_may_regress_globally(
        self, tmp_path, started, completed
    ):
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
            (
                [
                    "2026-07-19T10:00:10+00:00",
                    "2026-07-19T10:00:30+00:00",
                ],
                [
                    "2026-07-19T10:00:20+00:00",
                    "2026-07-19T10:00:25+00:00",
                ],
            ),
        ],
        ids=[
            "same-agent-start-list-regresses",
            "same-agent-completion-list-regresses",
            "completion-precedes-start",
            "retry-completes-before-second-start",
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

    def test_incomplete_identities_remain_separate_from_completion_gap(self, tmp_path):
        manifest = _manifest()
        manifest["agents"] = {
            "started": [
                _agent_start(),
                _agent_start("security-reviewer"),
            ],
            "completed": [_agent_complete()],
            "incomplete": ["security-reviewer"],
        }

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["lifecycle"]["incomplete_identities"] == [
            "security-reviewer"
        ]
        assert measured["lifecycle"]["incomplete_count"] == 1
        assert measured["lifecycle"]["incomplete_by_agent"] == {
            "security-reviewer": 1
        }
        assert measured["lifecycle"]["completion_gap"] == 1

    @pytest.mark.parametrize(
        "malform",
        [
            lambda manifest: manifest.pop("agents"),
            lambda manifest: manifest["agents"].pop("started"),
            lambda manifest: manifest["agents"].__setitem__("completed", {}),
            lambda manifest: manifest["agents"].__setitem__("incomplete", [None]),
        ],
        ids=["missing-agents", "missing-list", "malformed-list", "unsafe-incomplete"],
    )
    def test_missing_or_malformed_agents_are_lifecycle_missing(
        self, tmp_path, malform
    ):
        manifest = _manifest()
        malform(manifest)

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["lifecycle"] is None
        assert measured["metric_availability"]["lifecycle"] == "missing"
        assert measured["metric_availability"]["coverage"] == "complete"

    @pytest.mark.parametrize(
        "identity_family",
        ["started", "completed", "incomplete"],
    )
    def test_unhashable_lifecycle_identity_fails_closed(
        self, tmp_path, identity_family
    ):
        class UnhashableStr(str):
            __hash__ = None

        manifest = _manifest()
        manifest["agents"] = {
            "started": [_agent_start()],
            "completed": [_agent_complete()],
            "incomplete": [],
        }
        if identity_family == "incomplete":
            manifest["agents"]["completed"] = []
            manifest["agents"]["incomplete"] = [
                UnhashableStr("code-reviewer")
            ]
        else:
            manifest["agents"][identity_family][0]["agent"] = (
                UnhashableStr("code-reviewer")
            )

        measured = measure_run(manifest, tmp_path, include_transcripts=False)

        assert measured["lifecycle"] is None
        assert measured["metric_availability"]["lifecycle"] == "missing"

    @pytest.mark.parametrize(
        "family,mutate",
        [
            ("started", lambda event: event.pop("schema_version")),
            ("started", lambda event: event.__setitem__("schema_version", True)),
            ("started", lambda event: event.__setitem__("event", "agent_complete")),
            ("started", lambda event: event.__setitem__("run_id", "other-run")),
            ("started", lambda event: event.__setitem__("timestamp", "2026-07-19T10:00:10")),
            ("started", lambda event: event.__setitem__("agent", "../private")),
            ("started", lambda event: event["scope"].__setitem__("files", 1.0)),
            ("completed", lambda event: event.__setitem__("issue_count", False)),
            ("completed", lambda event: event["severities"].__setitem__("high", -1)),
        ],
        ids=[
            "missing-schema",
            "boolean-schema",
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

    def test_completion_issue_count_must_match_sanitized_severity_sum(
        self, tmp_path
    ):
        completion = _agent_complete()
        completion["issue_count"] = 2
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
        "usage,usage_by_model,expected_state",
        [
            pytest.param(
                _usage(2),
                {
                    "claude-sonnet-4-5": _usage(1),
                    "claude-opus-4-1": _usage(1),
                },
                "complete",
                id="fully-attributed",
            ),
            pytest.param(
                _usage(2),
                {
                    "claude-sonnet-4-5": {
                        **_usage(2),
                        "output_tokens": _usage(2)["output_tokens"] - 1,
                    }
                },
                "partial",
                id="one-field-unattributed",
            ),
            pytest.param(
                _usage(2),
                {},
                "missing",
                id="no-model-attribution",
            ),
            pytest.param(
                _usage(0),
                {},
                "complete",
                id="zero-usage",
            ),
        ],
    )
    def test_model_availability_requires_exact_usage_conservation(
        self,
        monkeypatch,
        tmp_path,
        usage,
        usage_by_model,
        expected_state,
    ):
        transcript = _complete_empty_transcript()
        transcript["usage"] = _usage(7)
        transcript["agent_usage"] = [
            {
                "agent": "code-reviewer",
                "agent_id": "agent-1",
                "model": "claude-sonnet-4-5",
                "available": True,
                "usage": usage,
                "usage_by_model": usage_by_model,
            }
        ]

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["metric_availability"]["model_usage"] == expected_state
        assert measured["metric_availability"]["usage"] == "complete"
        assert measured["metric_availability"]["agent_usage"] == "complete"

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
            "schema_version": 2,
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

    @pytest.mark.parametrize(
        "by_agent",
        [
            [
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
            ],
            [
                {
                    "agent": "security-reviewer",
                    "builder_attempted": True,
                    "builder_attempts": 1,
                    "builder_successes": 0,
                    "builder_failures": 1,
                    "first_builder_attempt_succeeded": False,
                    "recovered": False,
                },
                {
                    "agent": "code-reviewer",
                    "builder_attempted": True,
                    "builder_attempts": 1,
                    "builder_successes": 1,
                    "builder_failures": 0,
                    "first_builder_attempt_succeeded": True,
                    "recovered": False,
                },
            ],
        ],
        ids=["first-succeeds-later-agent-recovers", "first-fails-other-agent-succeeds"],
    )
    def test_complete_multi_agent_builder_uses_aggregate_recovery_semantics(
        self, monkeypatch, tmp_path, by_agent
    ):
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

    @pytest.mark.parametrize("target", ["top-level", "agent"], ids=str)
    @pytest.mark.parametrize(
        "contradiction",
        ["arithmetic", "false-attempt", "first-result", "recovery", "float-count"],
        ids=str,
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

    @pytest.mark.parametrize(
        "first,successes,failures,partial_successes,partial_failures",
        [
            (True, 1, 0, 1, 0),
            (False, 0, 1, 0, 1),
        ],
        ids=["first-success-later-unknown", "first-failure-later-unknown"],
    )
    def test_known_first_with_later_unknown_result_is_retained_as_partial(
        self,
        monkeypatch,
        tmp_path,
        first,
        successes,
        failures,
        partial_successes,
        partial_failures,
    ):
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

    def test_partial_observed_no_attempt_keeps_unknown_aggregate_state(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["completeness"]["artifact_writes"] = False
        transcript["artifact_writes"] = {
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
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        cohort = aggregate_cohort([measured])

        artifacts = measured["transcript"]["artifact_writes"]
        assert measured["metric_availability"]["artifact_writes"] == "partial"
        assert artifacts["builder_attempted"] is None
        assert cohort["artifact_writes"]["partial_observed_no_builder_attempts"] == 1

    def test_top_level_unknown_first_result_is_partial_attempt_evidence(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["artifact_writes"] = {
            "available": True,
            "complete": True,
            "builder_attempted": True,
            "builder_attempts": 1,
            "builder_successes": 0,
            "builder_failures": 0,
            "first_builder_attempt_succeeded": None,
            "recovered": False,
            "by_agent": [],
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        cohort = aggregate_cohort([measured])

        assert measured["metric_availability"]["artifact_writes"] == "partial"
        assert (
            cohort["artifact_writes"]["partial_observed_first_builder_attempts"]
            == 0
        )
        assert (
            cohort["artifact_writes"]["partial_observed_unknown_first_results"]
            == 0
        )
        assert (
            cohort["artifact_writes"][
                "partial_observed_runs_with_builder_attempts"
            ]
            == 1
        )
        assert (
            cohort["artifact_writes"][
                "partial_observed_top_only_runs_with_unknown_first_builder_result"
            ]
            == 1
        )
        assert (
            cohort["artifact_writes"][
                "partial_observed_top_only_unclassified_builder_results"
            ]
            == 1
        )

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
                        "usage_by_model": {"claude-sonnet-4-5": _usage(1)},
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
                        "usage_by_model": {"claude-sonnet-4-5": _usage(1)},
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
                    "schema_version": 2,
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
            ("usage", "usage", "usage"),
            (
                "orchestrator_usage",
                "orchestrator_data",
                "orchestrator_usage_by_step",
            ),
            ("agent_usage", "agent_data", "agent_usage"),
            ("model_usage", "agent_data", "agent_usage"),
            ("tool_failures", "tool_failures", "tool_failures"),
            ("artifact_writes", "artifact_writes", "artifact_writes"),
            ("observed_reads", "observed_reads", "observed_reads"),
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

    @pytest.mark.parametrize(
        "duplicate_field",
        ["all", "in_scope", "out_of_scope", "non_scope_comparable"],
        ids=["all", "in-scope", "out-of-scope", "non-scope-comparable"],
    )
    def test_duplicate_observed_read_paths_reject_the_family_and_aggregate(
        self, monkeypatch, tmp_path, duplicate_field
    ):
        transcript = _complete_empty_transcript()
        reads = {
            "all": ["src/context.py"],
            "in_scope": ["src/context.py"],
            "out_of_scope": [],
            "non_scope_comparable": ["src/synthesis.py"],
            "exhaustive": False,
            "transcript_data_complete": True,
        }
        if duplicate_field == "all":
            reads["all"].append("src/context.py")
        elif duplicate_field == "in_scope":
            reads["in_scope"].append("src/context.py")
        elif duplicate_field == "out_of_scope":
            reads.update(
                {
                    "in_scope": [],
                    "out_of_scope": ["src/context.py", "src/context.py"],
                }
            )
        else:
            reads["non_scope_comparable"].append("src/synthesis.py")
        transcript["observed_reads"] = reads

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)
        cohort = aggregate_cohort([measured])

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["observed_reads"] == "missing"
        assert cohort["observed_reads"]["out_of_scope_count"] is None
        assert cohort["observed_reads"]["by_path"] is None
        assert cohort["observed_reads"]["availability"]["complete"] == 0

    @pytest.mark.parametrize(
        "invalid_value",
        [
            pytest.param(None, id="missing"),
            pytest.param("src/synthesis.py", id="non-list"),
            pytest.param(["PRIVATE\x00PATH"], id="unsafe-string"),
        ],
    )
    def test_non_scope_comparable_reads_require_a_privacy_safe_list(
        self, monkeypatch, tmp_path, invalid_value
    ):
        transcript = _complete_empty_transcript()
        if invalid_value is None:
            transcript["observed_reads"].pop("non_scope_comparable")
        else:
            transcript["observed_reads"]["non_scope_comparable"] = invalid_value

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["observed_reads"] == "missing"

    @pytest.mark.parametrize(
        "invalid_version",
        [
            pytest.param(None, id="missing"),
            pytest.param(1, id="legacy-v1"),
            pytest.param(3, id="future-mismatch"),
            pytest.param(True, id="boolean"),
        ],
    )
    def test_observed_reads_require_exact_v2_schema_and_never_zero_fill_legacy(
        self, monkeypatch, tmp_path, invalid_version
    ):
        transcript = _complete_empty_transcript()
        if invalid_version is None:
            transcript["observed_reads"].pop("schema_version")
        else:
            transcript["observed_reads"]["schema_version"] = invalid_version

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

    @pytest.mark.parametrize(
        "bad_path",
        [
            pytest.param("", id="empty"),
            pytest.param("/etc/passwd", id="posix-absolute"),
            pytest.param("../secret.py", id="parent-prefix"),
            pytest.param("a/../b.py", id="parent-segment"),
            pytest.param("./a.py", id="dot-prefix"),
            pytest.param("a//b.py", id="double-slash"),
            pytest.param(r"C:\secret.py", id="windows-drive"),
            pytest.param(r"\\server\share.py", id="windows-unc"),
            pytest.param(r"src\file.py", id="backslash-separator"),
            pytest.param("src/\x7fsecret.py", id="unicode-control"),
            pytest.param("src/\u202esecret.py", id="unicode-format"),
        ],
    )
    @pytest.mark.parametrize(
        "field",
        ["all", "in_scope", "out_of_scope", "non_scope_comparable"],
    )
    def test_observed_read_paths_require_canonical_repo_relative_form(
        self, monkeypatch, tmp_path, field, bad_path
    ):
        transcript = _complete_empty_transcript()
        reads = transcript["observed_reads"]
        if field in {"all", "in_scope"}:
            reads["all"] = [bad_path]
            reads["in_scope"] = [bad_path]
        elif field == "out_of_scope":
            reads["all"] = [bad_path]
            reads["out_of_scope"] = [bad_path]
        else:
            reads["non_scope_comparable"] = [bad_path]

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
            (
                ["src/a.py", "src/b.py"],
                ["src/a.py"],
                ["src/a.py", "src/b.py"],
            ),
            (["src/a.py", "src/b.py"], ["src/a.py"], []),
            (["src/a.py"], ["src/a.py"], ["src/b.py"]),
        ],
        ids=["overlap", "missing-member", "extra-member"],
    )
    def test_observed_read_partition_must_be_disjoint_and_exact(
        self, monkeypatch, tmp_path, all_paths, in_scope, out_of_scope
    ):
        transcript = _complete_empty_transcript()
        transcript["observed_reads"] = {
            "all": all_paths,
            "in_scope": in_scope,
            "out_of_scope": out_of_scope,
            "non_scope_comparable": ["src/synthesis.py"],
            "exhaustive": False,
            "transcript_data_complete": True,
        }

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["observed_reads"] == "missing"

    @pytest.mark.parametrize(
        "invalid_exhaustive",
        [
            pytest.param(True, id="true"),
            pytest.param(None, id="missing"),
            pytest.param("false", id="string"),
        ],
    )
    def test_observed_reads_require_explicit_false_exhaustive(
        self, monkeypatch, tmp_path, invalid_exhaustive
    ):
        transcript = _complete_empty_transcript()
        if invalid_exhaustive is None:
            transcript["observed_reads"].pop("exhaustive")
        else:
            transcript["observed_reads"]["exhaustive"] = invalid_exhaustive

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["observed_reads"] == "missing"

    @pytest.mark.parametrize(
        "invalid_complete",
        [
            pytest.param(None, id="missing"),
            pytest.param(1, id="integer"),
            pytest.param("true", id="string"),
        ],
    )
    def test_observed_reads_require_boolean_transcript_data_complete(
        self, monkeypatch, tmp_path, invalid_complete
    ):
        transcript = _complete_empty_transcript()
        if invalid_complete is None:
            transcript["observed_reads"].pop("transcript_data_complete")
        else:
            transcript["observed_reads"][
                "transcript_data_complete"
            ] = invalid_complete

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["transcript"]["observed_reads"] is None
        assert measured["metric_availability"]["observed_reads"] == "missing"

    @pytest.mark.parametrize(
        "family_complete,payload_complete,expected_state",
        [
            (True, True, "complete"),
            (False, False, "partial"),
            (True, False, "missing"),
            (False, True, "missing"),
        ],
        ids=[
            "complete-aligned",
            "partial-aligned",
            "family-true-payload-false",
            "family-false-payload-true",
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
            "schema_version": 2,
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
        "invalid", [float("inf"), float("nan"), 0.9, 10**1_000]
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
                "usage_by_model": {
                    "claude-sonnet-4-5": {
                        **_usage(1),
                        "cache_creation_input_tokens": invalid,
                    }
                },
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
        unavailable["coverage"] = None
        unavailable["outcome"] = {"summary": {}, "critic_verdict": None}
        unavailable["wall_time_ms"] = None
        unavailable["metric_availability"].update(
            {
                "dispatch": "missing",
                "coverage": "missing",
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
        assert cohort["coverage"]["reviewable"] == 1
        assert cohort["coverage"]["assigned"] == 1
        assert cohort["coverage"]["uncovered"] == 0
        assert cohort["outcomes"]["raw_findings"] == 3
        assert cohort["outcomes"]["final_findings"] == 1
        assert cohort["critic"]["verdicts"] == {"STAND": 1}
        assert cohort["wall_time"]["total_ms"] == 60_000
        assert cohort["availability"]["coverage"]["missing"] == 1

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

    def test_largest_supported_wall_times_keep_half_millisecond_exactness(self):
        one_year_ms = 365 * 24 * 60 * 60 * 1000
        largest = _measured_run("largest-wall")
        largest["wall_time_ms"] = one_year_ms
        adjacent = _measured_run("adjacent-wall")
        adjacent["wall_time_ms"] = one_year_ms - 1

        wall = aggregate_cohort([largest, adjacent])["wall_time"]

        assert wall["total_ms"] == 2 * one_year_ms - 1
        assert wall["mean_ms"] == one_year_ms - 0.5
        assert wall["median_ms"] == one_year_ms - 0.5

    def test_empty_cohort_wall_statistics_are_null_in_strict_json(self):
        cohort = aggregate_cohort([])

        payload = json.loads(
            format_json([], cohort),
            parse_constant=lambda value: (_ for _ in ()).throw(
                AssertionError(f"nonstandard constant: {value}")
            ),
        )

        assert payload["aggregate"]["wall_time"]["total_ms"] is None
        assert payload["aggregate"]["wall_time"]["mean_ms"] is None
        assert payload["aggregate"]["wall_time"]["median_ms"] is None

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
                    "usage": _usage(20),
                    "usage_by_model": {"claude-sonnet-4-5": _usage(20)},
                }
            ],
        )

        cohort = aggregate_cohort([run])

        assert cohort["usage"]["complete_totals"]["effective_input_tokens"] == 600
        assert cohort["orchestrator_usage"]["by_step"]["5"]["effective_input_tokens"] == 60
        assert cohort["agent_usage"]["by_agent"]["code-reviewer"]["effective_input_tokens"] == 120
        assert cohort["model_usage"]["by_model"]["claude-sonnet-4-5"]["effective_input_tokens"] == 120

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
        run["coverage"] = None
        run["outcome"] = {"summary": {}, "critic_verdict": None}
        run["wall_time_ms"] = None
        run["metric_availability"].update(
            {
                "dispatch": "missing",
                "coverage": "missing",
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
            "Assigned/Reviewable/Uncovered",
            "Outcome/Critic",
            "Wall",
            "Eff In/Out",
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
        assert render._table_row(measured)[7] == "—"
        assert payload["runs"][0]["metric_availability"]["usage"] == "missing"
        assert payload["runs"][0]["transcript"]["usage"] == _usage(0)

    def test_table_usage_disabled_is_not_applicable(self):
        measured = measure_run(
            _manifest(), Path("/nonexistent"), include_transcripts=False
        )

        assert measured["metric_availability"]["usage"] == "disabled"
        assert render._table_row(measured)[7] == "n/a"

    def test_table_usage_complete_zero_is_observed_zero(
        self, monkeypatch, tmp_path
    ):
        measured = _measure_fake_transcript(
            monkeypatch, tmp_path, _complete_empty_transcript()
        )

        assert measured["metric_availability"]["usage"] == "complete"
        assert render._table_row(measured)[7] == "0/0"

    def test_table_usage_partial_observation_is_explicit(
        self, monkeypatch, tmp_path
    ):
        transcript = _complete_empty_transcript()
        transcript["completeness"]["usage"] = False
        transcript["usage"] = _usage(1)

        measured = _measure_fake_transcript(monkeypatch, tmp_path, transcript)

        assert measured["metric_availability"]["usage"] == "partial"
        assert render._table_row(measured)[7] == "partial 6/4"

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

    def test_table_cells_strip_c1_csi_sequences_without_leaking_parameters(self):
        run = _measured_run("c1-csi")
        run["run"]["id"] = "before\x9b31mred\x9b0mafter"

        table = format_table([run], aggregate_cohort([run]))

        assert "beforeredafter" in table
        assert "31m" not in table
        assert "0m" not in table
        assert "\x9b" not in table

    def test_table_cells_strip_c1_osc_sequences_without_leaking_payload(self):
        run = _measured_run("c1-osc")
        run["run"]["id"] = "before\x9d0;owned\x9cafter"

        table = format_table([run], aggregate_cohort([run]))

        assert "beforeafter" in table
        assert "0;owned" not in table
        assert "\x9d" not in table
        assert "\x9c" not in table

    @pytest.mark.parametrize("backslash_count", [1, 3], ids=["one", "multiple"])
    def test_table_cells_keep_pipes_escaped_after_preceding_backslashes(
        self, backslash_count
    ):
        run = _measured_run("backslash-pipe")
        run["run"]["id"] = "safe" + "\\" * backslash_count + "|forged"

        table = format_table([run], aggregate_cohort([run]))

        assert (
            "safe" + "\\" * (backslash_count * 2 + 1) + "|forged"
        ) in table
        assert sum(line.startswith("| ") for line in table.splitlines()) == 3

    def test_json_keeps_structured_values_without_table_escaping(self):
        run = _measured_run("safe\n| value |\x1b[31mred\x1b[0m")

        rendered = format_json([run], aggregate_cohort([run]))
        payload = json.loads(rendered)

        assert payload["runs"][0]["run"]["id"] == run["run"]["id"]
        assert r"\|" not in rendered

    def test_json_has_exact_top_level_and_is_parseable(self):
        runs = [_measured_run("run-json")]
        payload = json.loads(format_json(runs, aggregate_cohort(runs)))

        assert set(payload) == {"schema_version", "runs", "aggregate"}
        assert payload["schema_version"] == 2

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
        with pytest.raises(ValueError):
            format_json([{"invalid": float("nan")}], aggregate_cohort([]))

    @pytest.mark.parametrize(
        "invalid", [float("nan"), float("inf"), float("-inf")]
    )
    def test_json_formatter_rejects_nonfinite_aggregate_values(self, invalid):
        with pytest.raises(ValueError):
            format_json([], {"wall_time": {"mean_ms": invalid}})

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
            "schema_version": 2,
            "runs": [],
            "aggregate": aggregate_cohort([]),
        }
        assert output.read_text() == format_json([], aggregate_cohort([]))

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
            ["--last", "0"],
            ["--last", "not-an-int"],
            ["--format", "xml"],
        ],
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

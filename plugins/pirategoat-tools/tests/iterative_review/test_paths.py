"""Canonical layout coverage for iterative review run artifacts."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from iterative_review import paths
from iterative_review.__main__ import _write_loop_result
from iterative_review.backends import codex
from iterative_review.loop import (
    append_deferred_item,
    append_pushback_log,
    read_deferred_items,
    read_loop_state,
    read_pushback_log,
    write_loop_state,
)
from iterative_review.telemetry import ReviewTelemetry
from review import run_paths


def test_iterative_registry_is_the_exact_grouped_contract():
    assert paths.ITERATIVE_ARTIFACTS == {
        "context": ("pipeline", "review-context.md"),
        "state": ("pipeline", "review-loop-state.json"),
        "progress": ("pipeline", "review-progress.jsonl"),
        "events": ("pipeline", "pipeline-events.jsonl"),
        "pushback": ("synthesis", "pushback-log.md"),
        "deferred": ("synthesis", "deferred-items.jsonl"),
        "result": ("synthesis", "review-loop-result.json"),
    }
    assert paths.ROUND_ARTIFACTS == {
        "findings": "findings.json",
        "outcomes": "outcomes.json",
        "prompt": "prompt.md",
        "codex_output": "codex-output.json",
        "codex_raw": "codex-raw.md",
        "claude_raw": "claude-raw.md",
        "analysis": "{prefix}-r{round}-analysis.md",
    }


def test_iterative_and_round_paths_stay_in_the_canonical_groups(tmp_path):
    assert paths.iterative_artifact_path(tmp_path, "state") == (
        tmp_path / "pipeline" / "review-loop-state.json"
    )
    assert paths.iterative_artifact_path(tmp_path, "result") == (
        tmp_path / "synthesis" / "review-loop-result.json"
    )
    assert paths.round_artifact_path(tmp_path, 2, "findings") == (
        tmp_path / "reviewers" / "round-2" / "findings.json"
    )
    assert paths.round_artifact_path(
        tmp_path, 2, "analysis", prefix="independent-review"
    ) == (
        tmp_path
        / "reviewers"
        / "round-2"
        / "independent-review-r2-analysis.md"
    )


@pytest.mark.parametrize("round_num", [0, -1, True, "1"])
def test_round_paths_reject_invalid_round_identities(tmp_path, round_num):
    with pytest.raises(ValueError, match="positive integer"):
        paths.round_artifact_path(tmp_path, round_num, "findings")


@pytest.mark.parametrize("prefix", ["", ".", "..", "a/b", "a\\b"])
def test_analysis_paths_reject_unsafe_prefixes(tmp_path, prefix):
    with pytest.raises(ValueError, match="analysis prefix"):
        paths.round_artifact_path(
            tmp_path, 1, "analysis", prefix=prefix
        )


def test_paths_cli_resolves_pipeline_synthesis_and_round_artifacts(tmp_path):
    script = SCRIPTS_DIR / "iterative_review" / "paths.py"

    context = subprocess.run(
        [
            sys.executable,
            str(script),
            "artifact",
            "--output-dir",
            str(tmp_path),
            "--key",
            "context",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )
    outcomes = subprocess.run(
        [
            sys.executable,
            str(script),
            "round",
            "--output-dir",
            str(tmp_path),
            "--round",
            "3",
            "--key",
            "outcomes",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "artifact",
            "--output-dir",
            str(tmp_path),
            "--key",
            "result",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )

    assert context.returncode == 0, context.stderr
    assert outcomes.returncode == 0, outcomes.stderr
    assert result.returncode == 0, result.stderr
    assert Path(context.stdout.strip()) == paths.iterative_artifact_path(
        tmp_path, "context"
    )
    assert Path(outcomes.stdout.strip()) == paths.round_artifact_path(
        tmp_path, 3, "outcomes"
    )
    assert Path(result.stdout.strip()) == paths.iterative_artifact_path(
        tmp_path, "result"
    )


def test_iterative_producers_and_consumers_preserve_run_root_hygiene(tmp_path):
    target = tmp_path / "target"
    run_dir = run_paths.allocate_run_dir(target)
    run_paths.artifact_path(run_dir, "run_config").write_text("{}\n")

    context_path = paths.iterative_artifact_path(run_dir, "context")
    context_path.write_text("Review the durable layout.\n")
    state = {
        "current_round": 1,
        "rounds": [{"round": 1, "findings": 1, "fixed": 1}],
        "max_rounds": 3,
    }
    write_loop_state(run_dir, state)
    append_pushback_log(run_dir, "REJECTED: example\n")
    append_deferred_item(run_dir, {"id": "r1_f1"})

    telemetry = ReviewTelemetry(run_dir)
    telemetry.progress("round_started", round=1)
    telemetry.pipeline_event("review_round_started", round=1)

    analysis_path = paths.round_artifact_path(
        run_dir, 1, "analysis", prefix="independent-review"
    )
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_path.write_text("Analysis complete.\n")
    prompt_path = codex.write_prompt_file(
        output_dir=run_dir,
        round_num=1,
        rubric="Review carefully.",
        merge_base="base",
        context=context_path.read_text(),
        pushback_log=None,
        analysis_doc_path=str(analysis_path),
    )
    findings_path = paths.round_artifact_path(run_dir, 1, "findings")
    outcomes_path = paths.round_artifact_path(run_dir, 1, "outcomes")
    findings_path.write_text(
        json.dumps([
            {"id": "r1_f1", "title": "Example", "location": "x.py:1"}
        ])
    )
    outcomes_path.write_text(
        json.dumps([{"id": "r1_f1", "action": "fixed"}])
    )
    result = _write_loop_result(run_dir, state, "max_rounds")

    assert read_loop_state(run_dir) == state
    assert read_pushback_log(run_dir) == "REJECTED: example\n"
    assert read_deferred_items(run_dir) == [{"id": "r1_f1"}]
    assert Path(prompt_path) == paths.round_artifact_path(run_dir, 1, "prompt")
    assert result["rounds_completed"] == 1
    assert paths.iterative_artifact_path(run_dir, "result").is_file()
    assert {path.name for path in run_dir.iterdir()} == {
        "run-config.json",
        "pipeline",
        "reviewers",
        "synthesis",
        "tmp",
    }
    assert {path.name for path in (run_dir / "pipeline").iterdir()} == {
        "review-context.md",
        "review-loop-state.json",
        "review-progress.jsonl",
        "pipeline-events.jsonl",
    }
    assert {path.name for path in (run_dir / "synthesis").iterdir()} == {
        "pushback-log.md",
        "deferred-items.jsonl",
        "review-loop-result.json",
    }
    assert {path.name for path in (run_dir / "reviewers").iterdir()} == {
        "round-1"
    }
    assert {
        path.name for path in (run_dir / "reviewers" / "round-1").iterdir()
    } == {
        "prompt.md",
        "findings.json",
        "outcomes.json",
        "independent-review-r1-analysis.md",
    }

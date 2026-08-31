"""Tests for iterative_review.telemetry — progress log and pipeline events."""

import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent  # iterative_review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from iterative_review.telemetry import ReviewTelemetry
from iterative_review.paths import iterative_artifact_path


class TestProgressLog:
    def test_reset_clears_file(self, tmp_path):
        d = str(tmp_path)
        t = ReviewTelemetry(d)
        t.progress("test_event", round=1)
        t.reset_progress()
        path = iterative_artifact_path(d, "progress")
        assert path.read_text() == ""

    def test_progress_appends_jsonl(self, tmp_path):
        d = str(tmp_path)
        t = ReviewTelemetry(d)
        t.progress("round_started", round=1)
        t.progress("codex_invoked", round=1, diff_lines=500)
        lines = iterative_artifact_path(d, "progress").read_text().strip().split("\n")
        assert len(lines) == 2
        e1 = json.loads(lines[0])
        assert e1["event"] == "round_started"
        assert "ts" in e1
        e2 = json.loads(lines[1])
        assert e2["diff_lines"] == 500

    def test_progress_never_raises(self, tmp_path):
        t = ReviewTelemetry("/nonexistent/path/unlikely")
        t.progress("should_not_crash", round=1)  # best-effort


class TestPipelineEvents:
    def test_pipeline_event_writes_to_events_file(self, tmp_path):
        d = str(tmp_path)
        t = ReviewTelemetry(d)
        t.pipeline_event("review_loop_started", max_rounds=4)
        path = iterative_artifact_path(d, "events")
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        e = json.loads(lines[0])
        assert e["event"] == "review_loop_started"
        assert e["max_rounds"] == 4


class TestContextTracking:
    def test_tracks_context_size(self, tmp_path):
        d = str(tmp_path)
        t = ReviewTelemetry(d)
        t.progress("composing_context", round=2, context_chars=2500, context_limit=50000)
        lines = iterative_artifact_path(d, "progress").read_text().strip().split("\n")
        e = json.loads(lines[0])
        assert e["context_chars"] == 2500

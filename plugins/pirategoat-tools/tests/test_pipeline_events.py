"""Tests for pipeline_events.py — PipelineEventEmitter."""

import json
import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Module loading (same pattern as conftest.py for review-pipeline)
# ---------------------------------------------------------------------------

import importlib.util
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

def _load_module():
    spec = importlib.util.spec_from_file_location(
        "pipeline_events", SCRIPTS_DIR / "pipeline_events.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


@pytest.fixture
def emitter(mod, tmp_path):
    return mod.PipelineEventEmitter(str(tmp_path))


def _read_events(tmp_path):
    path = os.path.join(str(tmp_path), "pipeline-events.jsonl")
    lines = open(path).read().strip().split("\n")
    return [json.loads(line) for line in lines if line]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmitterBasics:
    def test_creates_file_on_first_emit(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.emit("step_started", {"step": 1, "title": "Parse Input"})
        path = os.path.join(str(tmp_path), "pipeline-events.jsonl")
        assert os.path.isfile(path)

    def test_appends_jsonl_lines(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.emit("step_started", {"step": 1})
        emitter.emit("step_completed", {"step": 1})
        events = _read_events(tmp_path)
        assert len(events) == 2
        assert events[0]["event"] == "step_started"
        assert events[1]["event"] == "step_completed"

    def test_includes_timestamp(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.emit("test_event", {})
        events = _read_events(tmp_path)
        assert "ts" in events[0]
        # ISO format check
        assert "T" in events[0]["ts"]

    def test_preserves_extra_fields(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.emit("custom", {"key": "value", "num": 42})
        events = _read_events(tmp_path)
        assert events[0]["key"] == "value"
        assert events[0]["num"] == 42

    def test_none_fields_default_to_empty(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.emit("test", None)
        events = _read_events(tmp_path)
        assert events[0]["event"] == "test"


class TestMilestoneHelper:
    def test_emits_milestone_event(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.milestone("investigation_complete", step=6, summary="Issue is valid")
        events = _read_events(tmp_path)
        assert events[0]["event"] == "milestone"
        assert events[0]["milestone"] == "investigation_complete"
        assert events[0]["step"] == 6
        assert events[0]["summary"] == "Issue is valid"

    def test_milestone_with_extra_kwargs(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.milestone("plan_written", step=8, summary="5 tasks", task_count=5)
        events = _read_events(tmp_path)
        assert events[0]["task_count"] == 5


class TestDeliverableHelper:
    def test_emits_deliverable_event(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.deliverable("draft_pr_created", pr_url="https://github.com/Org/repo/pull/999")
        events = _read_events(tmp_path)
        assert events[0]["event"] == "deliverable"
        assert events[0]["type"] == "draft_pr_created"
        assert events[0]["pr_url"] == "https://github.com/Org/repo/pull/999"

    def test_deliverable_with_multiple_fields(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.deliverable("investigation_report", path="/tmp/report.md", verdict="valid")
        events = _read_events(tmp_path)
        assert events[0]["path"] == "/tmp/report.md"
        assert events[0]["verdict"] == "valid"


class TestStepHelpers:
    def test_step_started(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.step_started(step=1, title="Parse Input")
        events = _read_events(tmp_path)
        assert events[0]["event"] == "step_started"
        assert events[0]["step"] == 1
        assert events[0]["title"] == "Parse Input"

    def test_step_completed(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.step_completed(step=1, title="Parse Input")
        events = _read_events(tmp_path)
        assert events[0]["event"] == "step_completed"


class TestErrorResilience:
    def test_never_throws_on_bad_path(self, mod):
        emitter = mod.PipelineEventEmitter("/nonexistent/path/that/does/not/exist")
        # Should not raise
        emitter.emit("test", {})
        emitter.milestone("test", step=1)
        emitter.deliverable("test")

    def test_never_throws_on_unserializable_data(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        # Sets are not JSON-serializable
        emitter.emit("test", {"bad": {1, 2, 3}})
        # Should not raise — just silently skip


class TestPipelineCompleteHelper:
    def test_emits_pipeline_complete(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.pipeline_complete(status="success", mode="investigate")
        events = _read_events(tmp_path)
        assert events[0]["event"] == "pipeline_complete"
        assert events[0]["status"] == "success"
        assert events[0]["mode"] == "investigate"

    def test_pipeline_failed(self, mod, tmp_path):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        emitter.pipeline_failed(step=5, error="Linear MCP unavailable")
        events = _read_events(tmp_path)
        assert events[0]["event"] == "pipeline_failed"
        assert events[0]["step"] == 5
        assert events[0]["error"] == "Linear MCP unavailable"

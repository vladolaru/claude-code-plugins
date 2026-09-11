"""Tests for linear/events.py — PipelineEventEmitter."""

import json
import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Module loading (same pattern as conftest.py for review/pipeline)
# ---------------------------------------------------------------------------

import importlib.util
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent  # linear/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

def _load_module():
    spec = importlib.util.spec_from_file_location(
        "pipeline_events", SCRIPTS_DIR / "linear" / "events.py"
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


# Each helper method builds an event dict from its own args/kwargs; one row
# per method proves the dict shape. Extra-kwargs passthrough is one contract,
# already pinned once by TestEmitterBasics::test_preserves_extra_fields.
EMITTER_HELPER_ROWS = [
    pytest.param(
        lambda emitter: emitter.milestone("investigation_complete", step=6, summary="Issue is valid"),
        {"event": "milestone", "milestone": "investigation_complete", "step": 6, "summary": "Issue is valid"},
        id="milestone",
    ),
    pytest.param(
        lambda emitter: emitter.deliverable("draft_pr_created", pr_url="https://github.com/Org/repo/pull/999"),
        {"event": "deliverable", "type": "draft_pr_created", "pr_url": "https://github.com/Org/repo/pull/999"},
        id="deliverable",
    ),
    pytest.param(
        lambda emitter: emitter.step_started(step=1, title="Parse Input"),
        {"event": "step_started", "step": 1, "title": "Parse Input"},
        id="step_started",
    ),
    pytest.param(
        lambda emitter: emitter.step_completed(step=1, title="Parse Input"),
        {"event": "step_completed"},
        id="step_completed",
    ),
    pytest.param(
        lambda emitter: emitter.pipeline_complete(status="success", mode="investigate"),
        {"event": "pipeline_complete", "status": "success", "mode": "investigate"},
        id="pipeline_complete",
    ),
    pytest.param(
        lambda emitter: emitter.pipeline_failed(step=5, error="Linear MCP unavailable"),
        {"event": "pipeline_failed", "step": 5, "error": "Linear MCP unavailable"},
        id="pipeline_failed",
    ),
]


class TestEmitterHelpers:
    @pytest.mark.parametrize("call,expected_fields", EMITTER_HELPER_ROWS)
    def test_helper_emits_expected_fields(self, mod, tmp_path, call, expected_fields):
        emitter = mod.PipelineEventEmitter(str(tmp_path))
        call(emitter)
        events = _read_events(tmp_path)
        for key, value in expected_fields.items():
            assert events[0][key] == value


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

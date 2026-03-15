"""Tests for StreamMonitor — JSONL parsing and checkpoint engine."""

import json
import os
import time
from pathlib import Path

import pytest

from stream_monitor import (
    parse_jsonl_event,
    detect_step_number,
    detect_agent_dispatch,
    Checkpoint,
    CheckpointResult,
    StreamMonitor,
    StreamResult,
)


class TestParseJsonlEvent:
    def test_parses_text_event(self):
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Hello"}]},
        }
        result = parse_jsonl_event(json.dumps(event))
        assert result is not None
        assert result["type"] == "text"
        assert result["text"] == "Hello"

    def test_parses_tool_use_event(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Agent",
                        "input": {
                            "subagent_type": "pirategoat-tools:security-reviewer",
                            "description": "security review",
                        },
                    }
                ]
            },
        }
        result = parse_jsonl_event(json.dumps(event))
        assert result["type"] == "tool_use"
        assert result["tool"] == "Agent"
        assert "security-reviewer" in result.get("subagent_type", "")

    def test_parses_result_event(self):
        event = {"type": "result", "result": "done", "session_id": "abc"}
        result = parse_jsonl_event(json.dumps(event))
        assert result["type"] == "result"

    def test_returns_none_for_garbage(self):
        assert parse_jsonl_event("not json") is None

    def test_returns_none_for_empty(self):
        assert parse_jsonl_event("") is None


class TestDetectStepNumber:
    def test_detects_step_header(self):
        assert detect_step_number("═══ PR REVIEW Step 3/14: PR Review State (AWARENESS) ═══") == 3

    def test_detects_phase_header(self):
        assert detect_step_number("═══ PIPELINE PHASE 2/4: Extract Verdict (ANALYSIS) ═══") is None

    def test_returns_none_for_non_step(self):
        assert detect_step_number("Just some regular text") is None


class TestDetectAgentDispatch:
    def test_detects_agent_name(self):
        event = {
            "type": "tool_use",
            "tool": "Agent",
            "subagent_type": "pirategoat-tools:security-reviewer",
        }
        assert detect_agent_dispatch(event) == "security-reviewer"

    def test_returns_none_for_non_agent(self):
        event = {"type": "tool_use", "tool": "Bash"}
        assert detect_agent_dispatch(event) is None

    def test_returns_none_for_non_reviewer(self):
        event = {
            "type": "tool_use",
            "tool": "Agent",
            "subagent_type": "pirategoat-tools:decision-reviewer",
        }
        # decision-reviewer is not a dispatch agent — it's post-processing.
        assert detect_agent_dispatch(event) is None


class TestCheckpointResult:
    def test_passed_result(self):
        r = CheckpointResult(name="test", passed=True)
        assert r.passed

    def test_failed_result(self):
        r = CheckpointResult(name="test", passed=False, reason="file missing")
        assert not r.passed
        assert "file missing" in r.reason


class TestStreamMonitorWithSyntheticEvents:
    """Test the monitor with pre-built event sequences (no subprocess)."""

    def _make_text_event(self, text: str) -> str:
        return json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
        })

    def _make_agent_event(self, agent_type: str) -> str:
        return json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Agent",
                        "input": {
                            "subagent_type": f"pirategoat-tools:{agent_type}",
                            "description": f"dispatch {agent_type}",
                        },
                    }
                ]
            },
        })

    def _make_result_event(self) -> str:
        return json.dumps({"type": "result", "result": "done", "session_id": "test"})

    def test_detects_step_transitions(self, tmp_path):
        events = [
            self._make_text_event("═══ PR REVIEW Step 0/14: Parse PR (SETUP) ═══"),
            self._make_text_event("PR number is 42"),
            self._make_text_event("═══ PR REVIEW Step 1/14: Repo Setup (SETUP) ═══"),
            self._make_result_event(),
        ]
        monitor = StreamMonitor(str(tmp_path), checkpoints=[])
        for line in events:
            monitor._process_line(line)
        assert monitor.current_step == 1

    def test_collects_agent_dispatches(self, tmp_path):
        events = [
            self._make_agent_event("security-reviewer"),
            self._make_agent_event("performance-reviewer"),
            self._make_result_event(),
        ]
        monitor = StreamMonitor(str(tmp_path), checkpoints=[])
        for line in events:
            monitor._process_line(line)
        assert "security-reviewer" in monitor.dispatched_agents
        assert "performance-reviewer" in monitor.dispatched_agents

    def test_fires_checkpoint_on_step_transition(self, tmp_path):
        # Create the expected file before the checkpoint fires.
        (tmp_path / "review-context.json").write_text('{"version": 1}')

        fired = []

        def check_context_exists(output_dir):
            exists = os.path.isfile(os.path.join(output_dir, "review-context.json"))
            fired.append(True)
            return CheckpointResult(
                name="context_file",
                passed=exists,
                reason="" if exists else "review-context.json not found",
            )

        checkpoint = Checkpoint(
            name="step_2_context_file",
            trigger_step=3,  # fires when step 3 starts (meaning step 2 completed)
            assertion=check_context_exists,
            timeout_seconds=1,
        )

        events = [
            self._make_text_event("═══ PR REVIEW Step 2/14: Context (SETUP) ═══"),
            self._make_text_event("running gather-review-context.py..."),
            self._make_text_event("═══ PR REVIEW Step 3/14: Review State (AWARENESS) ═══"),
            self._make_result_event(),
        ]

        monitor = StreamMonitor(str(tmp_path), checkpoints=[checkpoint])
        for line in events:
            monitor._process_line(line)

        assert len(fired) == 1
        assert len(monitor.checkpoint_results) == 1
        assert monitor.checkpoint_results[0].passed

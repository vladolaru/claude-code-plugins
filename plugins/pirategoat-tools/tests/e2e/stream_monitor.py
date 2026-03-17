"""StreamMonitor — real-time JSONL stream parser with checkpoint assertions.

Spawns the Claude CLI, parses the JSONL stream as it flows, detects
pipeline step transitions, and fires checkpoint assertions at the
right moments. Collects all results for post-run reporting.
"""

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


# Reviewer agents that get dispatched (excludes post-processing agents
# like decision-reviewer and review-reconciliator).
DISPATCH_AGENTS = {
    "pr-reviewer", "security-reviewer", "performance-reviewer",
    "architecture-reviewer", "wp-architecture-reviewer",
    "patterns-reviewer", "history-insights-reviewer",
    "php-tests-reviewer", "js-tests-reviewer", "e2e-tests-reviewer",
    "go-tests-reviewer", "dead-code-reviewer", "a11y-reviewer",
    "reliability-reviewer",
}

# Pattern for step headers in review-pipeline.py output.
STEP_PATTERN = re.compile(r"REVIEW PIPELINE Step (\d+)")

# Pattern for phase headers in pr-review.py output.
PHASE_PATTERN = re.compile(r"═══ PIPELINE PHASE (\d+)/\d+:")


@dataclass
class CheckpointResult:
    """Result of a checkpoint assertion."""

    name: str
    passed: bool
    reason: str = ""
    timestamp: float = 0.0
    trigger_event: str = ""


@dataclass
class Checkpoint:
    """A named assertion tied to a pipeline event trigger.

    When the trigger fires (step transition or agent dispatch),
    the assertion function is called with the output directory.
    If the assertion doesn't pass within timeout_seconds, it fails.
    """

    name: str
    assertion: Callable[[str], CheckpointResult]
    timeout_seconds: int = 30

    # Trigger conditions (set one).
    trigger_step: Optional[int] = None  # fires when this step number starts
    trigger_agent: Optional[str] = None  # fires when this agent is dispatched

    # Stream-content assertion (optional, checked alongside file assertion).
    # Called with the StreamMonitor instance when the checkpoint fires,
    # allowing access to step_text for stream-content checks.
    stream_assertion: Optional[Callable] = None

    # Internal state.
    _fired: bool = False


@dataclass
class StreamResult:
    """Final result of a monitored pipeline run."""

    checkpoint_results: list[CheckpointResult]
    events: list[dict]
    dispatched_agents: set[str]
    final_step: Optional[int]
    return_code: Optional[int]
    duration_seconds: float = 0.0


def parse_jsonl_event(line: str) -> Optional[dict]:
    """Parse a single JSONL line from the Claude CLI stream.

    Returns a simplified event dict or None if unparseable.
    """
    line = line.strip()
    if not line:
        return None

    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None

    if not isinstance(raw, dict) or "type" not in raw:
        return None

    if raw["type"] == "result":
        return {
            "type": "result",
            "result": raw.get("result", ""),
            "session_id": raw.get("session_id", ""),
        }

    if raw["type"] == "assistant" and isinstance(raw.get("message"), dict):
        content = raw["message"].get("content", [])

        # Check for tool_use first.
        for item in content:
            if item.get("type") == "tool_use":
                inp = item.get("input", {})
                return {
                    "type": "tool_use",
                    "tool": item.get("name", ""),
                    "subagent_type": inp.get("subagent_type", ""),
                    "description": inp.get("description", ""),
                    "input": inp,
                }

        # Then text.
        for item in content:
            if item.get("type") == "text" and item.get("text", "").strip():
                return {"type": "text", "text": item["text"]}

    return None


def detect_step_number(text: str) -> Optional[int]:
    """Extract step number from a PR REVIEW step header."""
    match = STEP_PATTERN.search(text)
    if match:
        return int(match.group(1))
    return None


def detect_agent_dispatch(event: dict) -> Optional[str]:
    """Extract reviewer agent name from an Agent tool_use event.

    Returns the short agent name (e.g., 'security-reviewer') or None.
    Only matches dispatch agents, not post-processing agents.
    """
    if event.get("type") != "tool_use" or event.get("tool") != "Agent":
        return None

    subagent = event.get("subagent_type", "")
    # Extract agent name from 'pirategoat-tools:security-reviewer'.
    if subagent.startswith("pirategoat-tools:"):
        name = subagent.split(":", 1)[1]
        if name in DISPATCH_AGENTS:
            return name
    return None


class StreamMonitor:
    """Watches a Claude CLI JSONL stream and verifies pipeline checkpoints.

    Usage:
        monitor = StreamMonitor(output_dir, checkpoints)
        result = monitor.run(claude_args, prompt, cwd, timeout)
        for cr in result.checkpoint_results:
            assert cr.passed, f"{cr.name}: {cr.reason}"
    """

    def __init__(self, output_dir: str, checkpoints: list[Checkpoint]):
        self.output_dir = output_dir
        self.checkpoints = list(checkpoints)
        self.checkpoint_results: list[CheckpointResult] = []
        self.events: list[dict] = []
        self.current_step: Optional[int] = None
        self.dispatched_agents: set[str] = set()
        self._start_time: float = 0.0
        # Text accumulated per step — keyed by step number.
        # Used by stream-content checkpoints to verify the pipeline
        # mentioned expected strings (e.g., "CHANGES_REQUESTED" in Step 3).
        self.step_text: dict[int, str] = {}

    def run(
        self,
        claude_args: list[str],
        prompt: str,
        cwd: str,
        timeout: int = 900,
    ) -> StreamResult:
        """Spawn Claude CLI, consume stream, fire checkpoints.

        Args:
            claude_args: CLI command + flags (e.g., ['claude', '--print', '-p', ...]).
            prompt: The prompt text (piped via stdin).
            cwd: Working directory for the CLI process.
            timeout: Overall timeout in seconds.

        Returns:
            StreamResult with all checkpoint results and collected events.
        """
        self._start_time = time.monotonic()

        proc = subprocess.Popen(
            claude_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            cwd=cwd,
            text=True,
        )

        # Pipe prompt via stdin.
        if proc.stdin:
            proc.stdin.write(prompt)
            proc.stdin.close()

        return_code = None
        try:
            if proc.stdout:
                for line in proc.stdout:
                    self._process_line(line)

            return_code = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            return_code = -1

        elapsed = time.monotonic() - self._start_time

        # Fire any remaining unfired checkpoints as failures.
        for cp in self.checkpoints:
            if not cp._fired:
                self.checkpoint_results.append(CheckpointResult(
                    name=cp.name,
                    passed=False,
                    reason="Checkpoint never triggered — pipeline may have stopped early",
                    timestamp=elapsed,
                ))

        return StreamResult(
            checkpoint_results=self.checkpoint_results,
            events=self.events,
            dispatched_agents=self.dispatched_agents,
            final_step=self.current_step,
            return_code=return_code,
            duration_seconds=elapsed,
        )

    def _process_line(self, line: str) -> None:
        """Process a single JSONL line from the stream."""
        event = parse_jsonl_event(line)
        if event is None:
            return

        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        event["_timestamp"] = elapsed
        self.events.append(event)

        # Detect step transitions.
        if event["type"] == "text":
            step = detect_step_number(event["text"])
            if step is not None and step != self.current_step:
                self.current_step = step
                self._fire_step_checkpoints(step, elapsed)

        # Accumulate text per step (for stream-content checkpoints).
        if event["type"] == "text" and self.current_step is not None:
            self.step_text.setdefault(self.current_step, "")
            self.step_text[self.current_step] += event["text"] + "\n"

        # Detect agent dispatches.
        agent = detect_agent_dispatch(event)
        if agent:
            self.dispatched_agents.add(agent)
            self._fire_agent_checkpoints(agent, elapsed)

    def _fire_step_checkpoints(self, step: int, timestamp: float) -> None:
        """Fire all checkpoints triggered by this step number."""
        for cp in self.checkpoints:
            if cp._fired or cp.trigger_step != step:
                continue
            self._fire_checkpoint(cp, timestamp, f"step {step} started")

    def _fire_agent_checkpoints(self, agent: str, timestamp: float) -> None:
        """Fire all checkpoints triggered by this agent dispatch."""
        for cp in self.checkpoints:
            if cp._fired or cp.trigger_agent != agent:
                continue
            self._fire_checkpoint(cp, timestamp, f"agent {agent} dispatched")

    def _fire_checkpoint(
        self, cp: Checkpoint, timestamp: float, trigger_desc: str
    ) -> None:
        """Fire a single checkpoint — poll until assertion passes or timeout."""
        cp._fired = True
        deadline = time.monotonic() + cp.timeout_seconds

        while True:
            result = cp.assertion(self.output_dir)
            if result.passed:
                result.timestamp = timestamp
                result.trigger_event = trigger_desc
                self.checkpoint_results.append(result)
                break

            if time.monotonic() >= deadline:
                result.timestamp = timestamp
                result.trigger_event = trigger_desc
                result.reason = (
                    f"Timed out after {cp.timeout_seconds}s waiting for: "
                    f"{result.reason}"
                )
                self.checkpoint_results.append(result)
                break

            time.sleep(0.5)  # Poll every 500ms.

        # Run stream-content assertion if present (instant — no polling).
        if cp.stream_assertion:
            stream_result = cp.stream_assertion(self)
            stream_result.timestamp = timestamp
            stream_result.trigger_event = trigger_desc
            self.checkpoint_results.append(stream_result)

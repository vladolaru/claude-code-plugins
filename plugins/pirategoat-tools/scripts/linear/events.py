"""Pipeline event emitter — writes milestone and deliverable events to pipeline-events.jsonl.

Used by pipeline scripts to communicate progress to the bot. The bot polls
the events file periodically and translates events into Slack messages:
- milestone events → status message edits (live progress indicator)
- deliverable events → separate thread replies (substantive artifacts)
- pipeline_complete/pipeline_failed → final summary

All writes are best-effort — the emitter never raises exceptions.
Zero external dependencies (stdlib only).
"""

import json
import os
from datetime import datetime, timezone


class PipelineEventEmitter:
    """Append-only JSONL event writer for pipeline-to-bot communication."""

    def __init__(self, output_dir):
        self._path = os.path.join(output_dir, "pipeline-events.jsonl")

    def emit(self, event, fields=None):
        """Append a JSONL event line. Best-effort — never raises."""
        try:
            line = json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event,
                **(fields or {}),
            })
            with open(self._path, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass

    # -- Convenience helpers --------------------------------------------------

    def milestone(self, name, step=None, summary=None, **extra):
        """Emit a milestone event (bot edits the status message)."""
        self.emit("milestone", {
            "milestone": name,
            "step": step,
            "summary": summary,
            **extra,
        })

    def deliverable(self, type_, **fields):
        """Emit a deliverable event (bot posts a separate thread reply)."""
        self.emit("deliverable", {"type": type_, **fields})

    def step_started(self, step, title, **extra):
        """Emit a step_started event."""
        self.emit("step_started", {"step": step, "title": title, **extra})

    def step_completed(self, step, title=None, **extra):
        """Emit a step_completed event."""
        self.emit("step_completed", {"step": step, "title": title, **extra})

    def pipeline_complete(self, status, mode=None, **extra):
        """Emit a pipeline_complete event."""
        self.emit("pipeline_complete", {"status": status, "mode": mode, **extra})

    def pipeline_failed(self, step, error, **extra):
        """Emit a pipeline_failed event."""
        self.emit("pipeline_failed", {"step": step, "error": error, **extra})

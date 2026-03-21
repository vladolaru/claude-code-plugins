"""JSONL telemetry for the iterative review loop.

Two output files:
- review-progress.jsonl: Live heartbeat log, reset each round. Main session tails this.
- pipeline-events.jsonl: Durable pipeline events for bot/post-run analysis.
"""

import json
import os
from datetime import datetime, timezone


class ReviewTelemetry:
    """Append-only JSONL writer for review loop progress and pipeline events."""

    def __init__(self, output_dir):
        self._output_dir = output_dir
        self._progress_path = os.path.join(output_dir, "review-progress.jsonl")
        self._events_path = os.path.join(output_dir, "pipeline-events.jsonl")

    def _write(self, path, event, fields=None):
        """Append a JSONL line. Best-effort — never raises."""
        try:
            line = json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event,
                **(fields or {}),
            })
            with open(path, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def progress(self, event, **fields):
        """Write to review-progress.jsonl (live, reset each round)."""
        self._write(self._progress_path, event, fields)

    def pipeline_event(self, event, **fields):
        """Write to pipeline-events.jsonl (durable, never reset)."""
        self._write(self._events_path, event, fields)

    def reset_progress(self):
        """Truncate review-progress.jsonl for a new round."""
        try:
            with open(self._progress_path, "w") as f:
                f.truncate(0)
        except Exception:
            pass

"""JSONL telemetry for iterative review progress and durable events."""

import json
from datetime import datetime, timezone

from .paths import iterative_artifact_path


class ReviewTelemetry:
    """Append-only JSONL writer for review loop progress and pipeline events."""

    def __init__(self, output_dir):
        self._output_dir = output_dir
        self._progress_path = iterative_artifact_path(output_dir, "progress")
        self._events_path = iterative_artifact_path(output_dir, "events")

    def _write(self, path, event, fields=None):
        """Append a JSONL line. Best-effort — never raises."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
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
        """Write to the live progress log, which resets each round."""
        self._write(self._progress_path, event, fields)

    def pipeline_event(self, event, **fields):
        """Write to the durable pipeline event log."""
        self._write(self._events_path, event, fields)

    def reset_progress(self):
        """Truncate the live progress log for a new round."""
        try:
            self._progress_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._progress_path, "w") as f:
                f.truncate(0)
        except Exception:
            pass

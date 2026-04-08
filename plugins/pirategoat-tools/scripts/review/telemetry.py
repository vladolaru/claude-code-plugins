#!/usr/bin/env python3
"""
Review Telemetry — JSONL telemetry for PR review pipelines.

Captures timing, decisions, and outcomes at each pipeline step.
Logs to ~/.pirategoat-tools/logs/reviews/.

Best-effort: failures never break the pipeline.
Zero external dependencies (stdlib only).
"""

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


LOG_DIR = os.path.expanduser("~/.pirategoat-tools/logs/reviews")
MARKER_FILE = ".telemetry-log-path"


class ReviewTelemetry:
    """Append-only JSONL telemetry for PR review pipelines.

    Usage:
        t = ReviewTelemetry(output_dir)
        t.start(pr_number="42")                      # Step 0
        t.log_step(step=1, phase="SETUP", ...)       # Steps 1–N-1
        t.finalize(step=N, phase="OUTPUT", ...)       # Final step
    """

    def __init__(self, output_dir: str, log_dir: Optional[str] = None):
        self.output_dir = output_dir
        self.log_dir = log_dir or LOG_DIR
        self._log_path: Optional[str] = None

    @property
    def log_path(self) -> Optional[str]:
        """Current log file path. Reads marker file if needed."""
        if self._log_path is None:
            marker = os.path.join(self.output_dir, MARKER_FILE)
            if os.path.isfile(marker):
                with open(marker) as f:
                    self._log_path = f.read().strip()
        return self._log_path

    def start(self, pr_number: str = "", total_steps: int = 15,
              bot_mode: bool = False, quick_mode: bool = False) -> str:
        """Create log file + marker. Write pipeline_start. Return log path."""
        os.makedirs(self.log_dir, exist_ok=True)

        self._quick_mode = quick_mode

        now = datetime.now(timezone.utc)
        basename = self._derive_log_basename()
        timestamp = now.strftime("%Y%m%dT%H%M%S")
        filename = f"{basename}--{timestamp}.jsonl"
        self._log_path = os.path.join(self.log_dir, filename)

        # Write marker so subsequent invocations can find the log
        marker = os.path.join(self.output_dir, MARKER_FILE)
        with open(marker, "w") as f:
            f.write(self._log_path)

        event = {
            "event": "pipeline_start",
            "timestamp": now.isoformat(),
            "step": 0,
            "pipeline": {
                "pr_number": pr_number,
                "output_dir": self.output_dir,
                "total_steps": total_steps,
                "bot_mode": bot_mode,
                "quick_mode": quick_mode,
            },
        }
        self._append(event)
        return self._log_path

    def log_step(self, step: int, phase: str, title: str,
                 bot_mode: bool = False,
                 thoughts_length: int = 0,
                 decisions: Optional[dict] = None) -> None:
        """Append step timing event (no snapshot). No-op if not started."""
        if self.log_path is None:
            return

        now = datetime.now(timezone.utc)
        duration_ms = self._duration_since_prev(now)

        event = {
            "event": "step",
            "timestamp": now.isoformat(),
            "step": step,
            "phase": phase,
            "title": title,
            "duration_since_prev_ms": duration_ms,
            "args": {
                "bot_mode": bot_mode,
                "thoughts_length": thoughts_length,
            },
        }
        if decisions:
            event["decisions"] = decisions
        self._append(event)

    def log_agent_start(self, agent_name: str, domain: str = "",
                        model_tier: str = "", scope_files: int = 0,
                        scope_lines: int = 0,
                        budget_target: Optional[int] = None) -> None:
        """Append agent_start event. No-op if not started."""
        if self.log_path is None:
            return

        now = datetime.now(timezone.utc)
        event = {
            "event": "agent_start",
            "timestamp": now.isoformat(),
            "agent": agent_name,
            "domain": domain,
            "model_tier": model_tier,
            "scope": {
                "files": scope_files,
                "lines": scope_lines,
            },
        }
        if budget_target is not None:
            event["budget_target"] = budget_target
        self._append(event)

    def log_agent_complete(self, agent_name: str, verdict: str = "",
                           issue_count: int = 0,
                           severities: Optional[dict] = None) -> None:
        """Append agent_complete event with duration from .started file. No-op if not started."""
        if self.log_path is None:
            return

        now = datetime.now(timezone.utc)
        duration_ms = self._agent_duration(agent_name, now)

        event = {
            "event": "agent_complete",
            "timestamp": now.isoformat(),
            "agent": agent_name,
            "duration_ms": duration_ms,
            "verdict": verdict,
            "issue_count": issue_count,
            "severities": severities or {},
        }
        self._append(event)

    def _agent_duration(self, agent_name: str, now: datetime) -> Optional[int]:
        """Calculate milliseconds since the agent's .started file timestamp."""
        started_path = os.path.join(self.output_dir, f"{agent_name}.started")
        if not os.path.isfile(started_path):
            return None
        try:
            with open(started_path) as f:
                started_at = datetime.fromisoformat(f.read().strip())
            return int((now - started_at).total_seconds() * 1000)
        except (ValueError, OSError):
            return None

    def finalize(self, step: int, phase: str, title: str,
                 bot_mode: bool = False,
                 thoughts_length: int = 0) -> None:
        """Append pipeline_end with snapshot + summary. No-op if not started."""
        if self.log_path is None:
            return

        now = datetime.now(timezone.utc)
        duration_ms = self._duration_since_prev(now)

        first_ts = self._read_timestamp(line_index=0)
        total_ms = None
        if first_ts:
            total_ms = int((now - first_ts).total_seconds() * 1000)

        event = {
            "event": "pipeline_end",
            "timestamp": now.isoformat(),
            "step": step,
            "phase": phase,
            "title": title,
            "duration_since_prev_ms": duration_ms,
            "args": {
                "bot_mode": bot_mode,
                "thoughts_length": thoughts_length,
            },
            "snapshot": self._snapshot(),
            "summary": self._build_summary(total_ms),
        }
        self._append(event)

    # ── Private helpers ──────────────────────────────────────────────

    # Generic basenames that don't identify a review run on their own.
    # When the output_dir ends with one of these, include parent path
    # components so the telemetry filename stays descriptive.
    _GENERIC_BASENAMES = frozenset({"first", "second", "third", "latest", "run"})

    def _derive_log_basename(self) -> str:
        """Derive a descriptive basename for the telemetry log file.

        For flat output dirs (e.g. ``branch-review-…``), the directory
        name already encodes repo + branch info → use it directly.

        For nested bot-mode dirs like
        ``…/pr-reviews/<repo-slug>/<pr>/<run>``, the final component
        alone (``first``) is meaningless.  Walk up until we have enough
        context, joining components with ``-``.
        """
        parts = os.path.normpath(self.output_dir).rstrip(os.sep).split(os.sep)
        basename = parts[-1] if parts else "review"

        if basename in self._GENERIC_BASENAMES and len(parts) >= 4:
            # Take last 4 components: review-type / repo-slug / pr-number / run-name
            # e.g. pr-reviews/work-a8c-…-woocommerce/64051/first
            basename = "-".join(parts[-4:])

        return basename

    def _append(self, event: dict) -> None:
        """Append a JSON line to the log file."""
        with open(self._log_path, "a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")

    def _duration_since_prev(self, now: datetime) -> Optional[int]:
        """Calculate milliseconds since the previous event."""
        prev = self._read_timestamp(line_index=-1)
        if prev is None:
            return None
        return int((now - prev).total_seconds() * 1000)

    def _read_timestamp(self, line_index: int) -> Optional[datetime]:
        """Read timestamp from a specific line (0=first, -1=last)."""
        if not self._log_path or not os.path.isfile(self._log_path):
            return None
        try:
            with open(self._log_path) as f:
                lines = f.readlines()
            if not lines:
                return None
            line = lines[line_index].strip()
            if line:
                return datetime.fromisoformat(json.loads(line)["timestamp"])
        except (json.JSONDecodeError, KeyError, ValueError, IndexError):
            pass
        return None

    def _read_quick_mode(self) -> bool:
        """Read quick_mode from the pipeline_start event (first line of JSONL).

        Falls back to in-memory _quick_mode, then False. This handles the
        cross-process case where start() and finalize() run in different
        invocations of the pipeline script.
        """
        if self.log_path and os.path.isfile(self.log_path):
            try:
                with open(self.log_path) as f:
                    first_line = f.readline().strip()
                if first_line:
                    event = json.loads(first_line)
                    if event.get("event") == "pipeline_start":
                        return event.get("pipeline", {}).get("quick_mode", False)
            except (json.JSONDecodeError, KeyError, OSError):
                pass
        return getattr(self, "_quick_mode", False)

    def _snapshot(self) -> dict:
        """Build a snapshot of the output directory state."""
        snap: Dict[str, Any] = {}
        snap["files"] = self._list_files()

        context = self._extract_context()
        if context:
            snap["context"] = context

        dispatch = self._extract_dispatch()
        if dispatch:
            snap["dispatch"] = dispatch

        agents = self._extract_agent_results()
        if agents:
            snap["agent_results"] = agents

        findings = self._extract_findings()
        if findings:
            snap["findings"] = findings

        return snap

    def _list_files(self) -> List[Dict[str, Any]]:
        """List files in output directory with sizes."""
        files = []
        try:
            for name in sorted(os.listdir(self.output_dir)):
                path = os.path.join(self.output_dir, name)
                if os.path.isfile(path):
                    files.append({"name": name, "size": os.path.getsize(path)})
        except OSError:
            pass
        return files

    def _extract_context(self) -> Optional[dict]:
        """Extract key fields from review-context.json."""
        path = os.path.join(self.output_dir, "review-context.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path) as f:
                ctx = json.load(f)
            pr = ctx.get("pr", {})
            git = ctx.get("git", {})
            size = ctx.get("pr_size", {})
            return {
                "pr_number": pr.get("number"),
                "pr_title": pr.get("title"),
                "pr_author": pr.get("author"),
                "pr_url": pr.get("url"),
                "git_range": git.get("git_range"),
                "base_ref": git.get("base_ref"),
                "head_ref": git.get("head_ref"),
                "commit_count": git.get("commit_count"),
                "changed_files_count": len(git.get("changed_files", [])),
                "pr_size": size,
                "linked_issues": ctx.get("linked_issues", []),
                "source": ctx.get("source"),
                "mode": ctx.get("mode"),
            }
        except (json.JSONDecodeError, KeyError):
            return None

    def _extract_dispatch(self) -> Optional[dict]:
        """Extract dispatch plan summary."""
        path = os.path.join(self.output_dir, "dispatch-plan.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path) as f:
                plan = json.load(f)
            agents = plan.get("agents", [])
            by_status: Dict[str, List[str]] = {}
            for a in agents:
                status = a.get("status", "SKIP")
                by_status.setdefault(status, []).append(a["name"])
            return {
                "total_agents": len(agents),
                "by_status": by_status,
                "agents": {
                    a["name"]: {
                        "status": a.get("status"),
                        "domain": a.get("domain"),
                        "reason": a.get("reason"),
                    }
                    for a in agents
                },
            }
        except (json.JSONDecodeError, KeyError):
            return None

    def _extract_agent_results(self) -> Optional[dict]:
        """Extract completed agent review results."""
        results = {}
        try:
            for name in sorted(os.listdir(self.output_dir)):
                if not name.endswith("-review.json"):
                    continue
                if name == "review-findings.json":
                    continue
                path = os.path.join(self.output_dir, name)
                base = name.replace("-review.json", "")
                try:
                    with open(path) as f:
                        data = json.load(f)
                    issues = data.get("issues", [])
                    severities = dict(Counter(
                        i.get("severity", "medium").lower() for i in issues
                    ))
                    results[base] = {
                        "verdict": data.get("verdict"),
                        "issue_count": len(issues),
                        "severities": severities,
                    }
                except (json.JSONDecodeError, KeyError):
                    results[base] = {"error": "malformed"}
        except OSError:
            pass
        return results if results else None

    def _extract_findings(self) -> Optional[dict]:
        """Extract reconciled findings summary."""
        path = os.path.join(self.output_dir, "review-findings.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            issues = data.get("issues", [])
            severities = dict(Counter(
                i.get("severity", "medium").lower() for i in issues
            ))
            return {
                "verdict": data.get("verdict"),
                "total_issues": len(issues),
                "severities": severities,
            }
        except (json.JSONDecodeError, KeyError):
            return None

    def _build_summary(self, total_duration_ms: Optional[int]) -> dict:
        """Build pipeline summary from all available data."""
        summary: Dict[str, Any] = {"total_duration_ms": total_duration_ms}
        summary["quick_mode"] = self._read_quick_mode()

        context = self._extract_context()
        if context:
            summary["pr_size_category"] = context.get("pr_size", {}).get("category")
            summary["changed_files_count"] = context.get("changed_files_count")
            summary["commit_count"] = context.get("commit_count")

        dispatch = self._extract_dispatch()
        if dispatch:
            summary["agents_total"] = dispatch["total_agents"]
            by_status = dispatch.get("by_status", {})
            summary["agents_dispatched"] = sum(
                len(v) for k, v in by_status.items() if k.startswith("DISPATCH")
            )
            summary["agents_skipped"] = sum(
                len(v) for k, v in by_status.items() if not k.startswith("DISPATCH")
            )

        agents = self._extract_agent_results()
        if agents:
            summary["agents_completed"] = len(agents)
            summary["total_agent_issues"] = sum(
                a.get("issue_count", 0) for a in agents.values() if "error" not in a
            )

        findings = self._extract_findings()
        if findings:
            summary["final_verdict"] = findings.get("verdict")
            summary["final_issues"] = findings.get("total_issues")
            summary["final_severities"] = findings.get("severities")

        return summary

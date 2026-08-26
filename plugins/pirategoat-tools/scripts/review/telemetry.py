#!/usr/bin/env python3
"""
Review Telemetry — JSONL telemetry for PR review pipelines.

Captures timing, decisions, and outcomes at each pipeline step.
Logs to ~/.pirategoat-tools/logs/reviews/.

Best-effort: failures never break the pipeline.
Zero external dependencies (stdlib only).
"""

import glob as glob_mod
import hashlib
import json
import os
import re
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from . import manifest_sections
    from .dispatch_status import (
        DISPATCHED_STATUSES,
        SKIPPED_STATUSES,
        validate_dispatch_plan_agents,
    )
    from .agent.output import (
        _VALID_SEVERITIES,
        _VERDICT_RANK,
        load_review_document,
    )
    from .atomic_io import atomic_write_json
    from .critic_adjustments import FINDINGS_READ_OK, read_findings_file
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import manifest_sections
    from review.dispatch_status import (
        DISPATCHED_STATUSES,
        SKIPPED_STATUSES,
        validate_dispatch_plan_agents,
    )
    from review.agent.output import (
        _VALID_SEVERITIES,
        _VERDICT_RANK,
        load_review_document,
    )
    from review.atomic_io import atomic_write_json
    from review.critic_adjustments import FINDINGS_READ_OK, read_findings_file

from git_paths import normalize_repo_paths


LOG_DIR = os.path.expanduser("~/.pirategoat-tools/logs/reviews")
MARKER_FILE = ".telemetry-log-path"
# Schema 3 makes the reviewed-files projection mandatory and is
# consumed in lockstep by review_metrics/contracts.py.
EVENT_SCHEMA = 3
# Optional manifest sections whose `availability["<name>"]` boolean shares
# the section's own top-level key. The analysis consumer's flag/payload
# consistency pin (`review_metrics` tests) parametrizes over this tuple —
# via `contracts._OPTIONAL_SECTION_AVAILABILITY_KEYS`, the same
# producer-declared-contract pattern `synthesis_lifecycle.ROW_KEYS` follows
# — so a section added here joins the pin automatically instead of
# silently shipping the "measured: true, payload dropped" gap Task 12
# closed for worktree_hygiene, usage, and skipped_steps, and Task 13
# closed for dependency_refresh, reviewer_markdown, and findings_markdown.
#
# `dispatch` is excluded: its payload is self-describing (no top-level
# availability boolean of its own). `pipeline`, `transcript`, and
# `lifecycle` are excluded: each is an availability flag with no
# same-named top-level section — `lifecycle`'s payload lives under
# `agents`, and `transcript`'s comes from a measurement source outside
# the manifest entirely.
OPTIONAL_SECTION_AVAILABILITY_KEYS = (
    "coverage",
    "worktree_hygiene",
    "synthesis_agents",
    "usage",
    "skipped_steps",
    "dependency_refresh",
    "reviewer_markdown",
    "findings_markdown",
)
# Full SHA-1 (40 hex) or SHA-256 (64 hex) object name — matches the
# pipeline's _FULL_SHA_RE contract for durable git identity.
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_STEP_MANIFEST_FIELDS = (
    "schema",
    "run_id",
    "event",
    "timestamp",
    "step",
    "phase",
    "title",
    "duration_since_prev_ms",
)
_AGENT_START_MANIFEST_FIELDS = (
    "schema",
    "run_id",
    "event",
    "timestamp",
    "agent",
    "domain",
    "model_tier",
    "budget_target",
)
_AGENT_COMPLETE_MANIFEST_FIELDS = (
    "schema",
    "run_id",
    "event",
    "timestamp",
    "agent",
    "duration_ms",
    "verdict",
    "finding_count",
    "review_digest",
)
_SEVERITY_FIELDS = _VALID_SEVERITIES


def _advisory_measurement(data: Any) -> Dict[str, Any]:
    """Return typed advisory-suppression facts from a review summary."""
    if not isinstance(data, dict):
        return {}
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return {}

    suppressed = summary.get("suppressed_advisory_finding_count")
    if (not isinstance(suppressed, int)
            or isinstance(suppressed, bool)
            or suppressed < 0):
        return {}

    measurement: Dict[str, Any] = {
        "suppressed_advisory_finding_count": suppressed,
    }
    verdict = data.get("verdict")
    verdict_without_advisory = summary.get("verdict_without_advisory")
    if (
        suppressed > 0
        and isinstance(verdict, str)
        and isinstance(verdict_without_advisory, str)
        and verdict in _VERDICT_RANK
        and verdict_without_advisory in _VERDICT_RANK
        and _VERDICT_RANK[verdict_without_advisory] > _VERDICT_RANK[verdict]
    ):
        measurement["verdict_without_advisory"] = verdict_without_advisory
    return measurement


def _incomplete_agent_executions(
    started: List[Dict[str, Any]], completed: List[Dict[str, Any]]
) -> List[str]:
    """Return a sorted multiset with one name per unmatched start event."""
    unmatched = Counter(
        event.get("agent") for event in started if event.get("agent")
    ) - Counter(
        event.get("agent") for event in completed if event.get("agent")
    )
    return sorted(unmatched.elements())


def project_agent_lifecycle(items, *, strict: bool = False):
    """Project append-only starts and finalizations by execution.

    The canonical execution projection shared by the telemetry producer and
    the review_metrics consumer (via contracts) — both sides MUST agree
    bit-exactly, so there is exactly one implementation.

    ``items`` yields ``(is_completion, agent, payload)`` triples in event
    order; ``agent`` is the agent name or ``None`` when unusable. A
    completion matches one outstanding start, so overlapping executions of
    the same agent each keep their completion.

    With ``strict=False`` completions with no preceding start remain visible
    for strict consumers to reject; with ``strict=True`` they fail the whole
    projection (returns ``None``).
    """
    started: List[dict] = []
    completed: List[dict] = []
    start_counts: Counter = Counter()
    completion_counts: Counter = Counter()

    for is_completion, agent, payload in items:
        if not is_completion:
            started.append(payload)
            if agent:
                start_counts[agent] += 1
            continue
        if strict and (
            agent is None
            or completion_counts[agent] >= start_counts[agent]
        ):
            return None
        completed.append(payload)
        if agent and completion_counts[agent] < start_counts[agent]:
            completion_counts[agent] += 1

    return started, completed


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
        self._event_parse_gaps = 0

    @property
    def log_path(self) -> Optional[str]:
        """Current log file path. Reads marker file if needed."""
        if self._log_path is None:
            marker = os.path.join(self.output_dir, MARKER_FILE)
            if os.path.isfile(marker):
                with open(marker) as f:
                    self._log_path = f.read().strip()
        return self._log_path

    @property
    def manifest_path(self) -> Optional[str]:
        """Materialized manifest path derived from the current JSONL log."""
        log_path = self.log_path
        if log_path is None:
            return None
        if log_path.endswith(".jsonl"):
            return f"{log_path[:-len('.jsonl')]}.manifest.json"
        return f"{log_path}.manifest.json"

    def start(self, pr_number: str = "", total_steps: int = 15,
              bot_mode: bool = False, quick_mode: bool = False,
              mode: str = "", repo_path: str = "",
              identifier: str = "", run_id: str = "",
              session_id: str = "", plugin_version: str = "",
              git_range: str = "", base_sha: str = "",
              head_sha: str = "") -> str:
        """Create log file + marker. Write pipeline_start. Return log path.

        Args:
            mode: Review mode (``pr``, ``full``, ``incremental``).
            repo_path: Absolute path to the repo being reviewed.
            identifier: PR number or branch name.
        """
        os.makedirs(self.log_dir, exist_ok=True)

        self._quick_mode = quick_mode
        self._run_id = run_id

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%dT%H%M%S")
        prefix = self._build_filename_prefix(mode, repo_path, identifier)
        run_num = self._next_run_number(prefix)
        self._log_path = self._allocate_log_path(prefix, run_num, timestamp)

        # Write marker so subsequent invocations can find the log
        marker = os.path.join(self.output_dir, MARKER_FILE)
        with open(marker, "w") as f:
            f.write(self._log_path)

        event = {
            "schema": EVENT_SCHEMA,
            "run_id": run_id,
            "event": "pipeline_start",
            "timestamp": now.isoformat(),
            "step": 0,
            "pipeline": {
                "pr_number": pr_number,
                "output_dir": self.output_dir,
                "total_steps": total_steps,
                "bot_mode": bot_mode,
                "quick_mode": quick_mode,
                "session_id": session_id,
                "plugin_version": plugin_version,
                "mode": mode,
                "repo_path": repo_path,
                "git": {
                    "requested_range": git_range,
                    "base_sha": base_sha,
                    "head_sha": head_sha,
                },
            },
        }
        self._append(event)
        self._materialize_manifest("running")
        return self._log_path

    def log_step(self, step: int, phase: str, title: str,
                 bot_mode: bool = False,
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
            },
        }
        if decisions:
            event["decisions"] = decisions
        self._append(event)
        self._materialize_manifest("running")

    def log_agent_start(self, agent_name: str, domain: Any = "",
                        model_tier: str = "", scope_files: int = 0,
                        scope_lines: int = 0,
                        budget_target: Optional[int] = None,
                        scope_paths: Optional[List[str]] = None) -> None:
        """Append agent_start event. No-op if not started."""
        if self.log_path is None:
            return

        now = datetime.now(timezone.utc)
        event = {
            "event": "agent_start",
            "timestamp": now.isoformat(),
            "agent": agent_name,
            "domain": "" if domain is None else domain,
            "model_tier": model_tier,
            "scope": {
                "files": scope_files,
                "lines": scope_lines,
            },
        }
        if scope_paths is not None:
            event["scope"]["paths"] = normalize_repo_paths(
                scope_paths,
                repo_path=self._pipeline_repo_path(),
            )
        if budget_target is not None:
            event["budget_target"] = budget_target
        self._append(event)

    def log_agent_review_draft_saved(
        self, agent_name: str, review_digest: str
    ) -> None:
        """Append raw diagnostic evidence for one saved review draft."""
        if self.log_path is None:
            return
        self._append({
            "event": "agent_review_draft_saved",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent_name,
            "review_digest": review_digest,
        })

    def log_agent_complete(self, agent_name: str, review_digest: str,
                           verdict: str = "",
                           finding_count: int = 0,
                           severities: Optional[dict] = None) -> None:
        """Append the digest-bound completion of one finalized review."""
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
            "finding_count": finding_count,
            "severities": severities or {},
            "review_digest": review_digest,
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
                 bot_mode: bool = False) -> None:
        """Append pipeline_end with snapshot + summary. No-op if not started."""
        if self.log_path is None:
            return

        now = datetime.now(timezone.utc)
        duration_ms = self._duration_since_prev(now)

        first_ts = self._read_timestamp(line_index=0)
        total_ms = None
        if first_ts:
            total_ms = int((now - first_ts).total_seconds() * 1000)

        extracts = self._output_extracts()
        event = {
            "event": "pipeline_end",
            "timestamp": now.isoformat(),
            "step": step,
            "phase": phase,
            "title": title,
            "duration_since_prev_ms": duration_ms,
            "args": {
                "bot_mode": bot_mode,
            },
            "snapshot": self._snapshot(extracts),
            "summary": self._build_summary(total_ms, extracts),
        }
        self._append(event)
        self._materialize_manifest("complete")

    # ── Private helpers ──────────────────────────────────────────────

    # Characters unsafe for filenames — replaced with ``-``.
    _UNSAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")

    @classmethod
    def path_to_slug(cls, path: str) -> str:
        """Convert an absolute path to a filename-safe slug.

        Strips the leading separator then replaces every run of
        non-alphanumeric characters (except ``.``, ``_``, ``-``) with a
        single ``-``.  Trailing ``-`` is stripped.

        Example::

            /Users/vladolaru/Work/a8c/woocommerce-payments
            → Users-vladolaru-Work-a8c-woocommerce-payments
        """
        normalized = os.path.normpath(path).lstrip(os.sep)
        return cls._UNSAFE_RE.sub("-", normalized).strip("-")

    # Longest sibling filename built from the prefix is the manifest:
    # {prefix}-run{N}--{timestamp}-{nonce}.manifest.json — 74 bytes of fixed
    # overhead at 6 run-number digits. Cap the prefix so every derived name
    # stays under the common 255-byte filename component limit; an oversized
    # prefix (deep worktree, long branch name) would otherwise make
    # allocation raise ENAMETOOLONG, which the fail-open pipeline swallows
    # into a run with no telemetry at all.
    _PREFIX_MAX_BYTES = 180

    @classmethod
    def _cap_prefix(cls, prefix: str) -> str:
        """Deterministically shorten oversized prefixes, keeping distinctness.

        Same input → same output (so run numbering keeps grouping a run's
        retries), and distinct long prefixes stay distinct via a digest of
        the full original. Byte-aware: the legacy fallback prefix is not
        ASCII-sanitized.
        """
        raw = prefix.encode("utf-8")
        if len(raw) <= cls._PREFIX_MAX_BYTES:
            return prefix
        digest = hashlib.sha256(raw).hexdigest()[:8]
        head = (
            raw[: cls._PREFIX_MAX_BYTES - 9]
            .decode("utf-8", "ignore")
            .rstrip("-")
        )
        return f"{head}-{digest}"

    def _build_filename_prefix(self, mode: str, repo_path: str,
                               identifier: str) -> str:
        """Build the structured prefix for a telemetry log filename.

        Format: ``<mode>-<repo_slug>-<identifier>``

        Falls back to ``output_dir`` basename when structured parts are
        missing (backward compat with callers that don't pass them).
        """
        if mode and repo_path:
            repo_slug = self.path_to_slug(repo_path)
            id_slug = self._UNSAFE_RE.sub("-", identifier).strip("-") if identifier else "branch"
            return self._cap_prefix(f"{mode}-{repo_slug}-{id_slug}")

        # Fallback: use output_dir basename (legacy callers)
        return self._cap_prefix(
            os.path.basename(os.path.normpath(self.output_dir)) or "review"
        )

    def _next_run_number(self, prefix: str) -> int:
        """Count existing log files with the same prefix and return the next run number."""
        pattern = os.path.join(self.log_dir, f"{prefix}-run*--*.jsonl")
        existing = glob_mod.glob(pattern)
        return len(existing) + 1

    def _allocate_log_path(self, prefix: str, run_num: int, timestamp: str) -> str:
        """Atomically allocate a nonce-suffixed log path unique to this run."""
        while True:
            nonce = uuid.uuid4().hex
            filename = f"{prefix}-run{run_num}--{timestamp}-{nonce}.jsonl"
            path = os.path.join(self.log_dir, filename)
            try:
                with open(path, "x"):
                    pass
                return path
            except FileExistsError:
                continue

    def _append(self, event: dict) -> None:
        """Append a JSON line to the log file."""
        schema, run_id = self._read_event_identity()
        event.setdefault("schema", schema)
        event.setdefault("run_id", run_id)
        with open(self._log_path, "a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")

    def _read_events(self) -> List[dict]:
        """Read object events, skipping and counting malformed/non-object lines.

        Counting skipped lines keeps the manifest from presenting a damaged log
        as if it were a clean, shorter event stream.
        """
        self._event_parse_gaps = 0
        events = []
        log_path = self.log_path
        if not log_path or not os.path.isfile(log_path):
            return events

        try:
            with open(log_path, "rb") as log:
                for line in log:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, TypeError, UnicodeError):
                        self._event_parse_gaps += 1
                        continue
                    if isinstance(event, dict):
                        events.append(event)
                    else:
                        self._event_parse_gaps += 1
        except OSError:
            pass
        return events

    def _read_json_file(self, name: str) -> Optional[dict]:
        """Read an output JSON object without letting failures escape."""
        return manifest_sections.read_json_file(self.output_dir, name)

    @staticmethod
    def _select_scalar_fields(event: dict, fields: tuple[str, ...]) -> dict:
        """Copy only named scalar fields into a manifest event."""
        return {
            name: event[name]
            for name in fields
            if name in event
            and (
                event[name] is None
                or isinstance(event[name], (str, int, float, bool))
            )
        }

    def _pipeline_repo_path(self) -> str:
        """Read the repository root recorded by the pipeline start event."""
        start = self._read_first_event()
        if start is None or start.get("event") != "pipeline_start":
            return ""
        pipeline = start.get("pipeline", {})
        if not isinstance(pipeline, dict):
            return ""
        repo_path = pipeline.get("repo_path")
        return repo_path if isinstance(repo_path, str) else ""

    def _manifest_step_event(self, event: dict) -> dict:
        """Sanitize one step event for the durable manifest."""
        result = self._select_scalar_fields(event, _STEP_MANIFEST_FIELDS)

        args = event.get("args", {})
        if isinstance(args, dict):
            safe_args = self._select_scalar_fields(args, ("bot_mode",))
            if safe_args:
                result["args"] = safe_args

        decisions = event.get("decisions", {})
        if (
            isinstance(decisions, dict)
            and isinstance(decisions.get("critic_skipped"), bool)
        ):
            result["decisions"] = {
                "critic_skipped": decisions["critic_skipped"]
            }
        return result

    def _manifest_agent_start_event(
        self, event: dict, repo_path: str = ""
    ) -> dict:
        """Sanitize one agent start event for the durable manifest."""
        result = self._select_scalar_fields(
            event, _AGENT_START_MANIFEST_FIELDS
        )
        if event.get("domain") is None and "domain" in event:
            result["domain"] = ""
        scope = event.get("scope", {})
        if isinstance(scope, dict):
            safe_scope = self._select_scalar_fields(scope, ("files", "lines"))
            if isinstance(scope.get("paths"), list):
                safe_scope["paths"] = normalize_repo_paths(
                    scope["paths"],
                    repo_path=repo_path,
                    normalize_backslash_separators=False,
                    decode_git_quoted=False,
                )
            if safe_scope:
                result["scope"] = safe_scope
        return result

    def _manifest_agent_complete_event(self, event: dict) -> dict:
        """Sanitize one finalized-agent event for the durable manifest."""
        result = self._select_scalar_fields(
            event, _AGENT_COMPLETE_MANIFEST_FIELDS
        )
        severities = event.get("severities", {})
        if isinstance(severities, dict):
            safe_severities = {
                name: severities[name]
                for name in _SEVERITY_FIELDS
                if type(severities.get(name)) is int
            }
            result["severities"] = safe_severities
        return result

    def _project_manifest_agent_lifecycle(
        self, events: List[dict], repo_path: str
    ) -> tuple[List[dict], List[dict]]:
        """Sanitize raw events and run the shared lifecycle projection.

        Current producers emit one completion per finalized execution.
        """

        def items():
            for event in events:
                event_name = event.get("event")
                agent = event.get("agent")
                agent_key = agent if isinstance(agent, str) and agent else None
                if event_name == "agent_start":
                    yield False, agent_key, self._manifest_agent_start_event(
                        event, repo_path=repo_path
                    )
                elif event_name == "agent_complete":
                    yield True, agent_key, self._manifest_agent_complete_event(
                        event
                    )

        return project_agent_lifecycle(items())

    def _build_manifest(self, status: str) -> dict:
        """Build the versioned materialized view from durable run events."""
        events = self._read_events()
        start = next(
            (event for event in events if event.get("event") == "pipeline_start"),
            {},
        )
        end = next(
            (
                event
                for event in reversed(events)
                if event.get("event") == "pipeline_end"
            ),
            {},
        )

        pipeline = start.get("pipeline", {})
        if not isinstance(pipeline, dict):
            pipeline = {}
        git = pipeline.get("git", {})
        git = dict(git) if isinstance(git, dict) else {}

        context = self._read_json_file("review-context.json")
        resolved_git = context.get("git", {}) if isinstance(context, dict) else {}
        if isinstance(resolved_git, dict):
            value = resolved_git.get("git_range")
            if value:
                git["requested_range"] = value
            # SHA endpoints may only be replaced by full object names: with an
            # explicit symbolic range ("main..HEAD") the context merge_base is
            # the literal branch name, and overwriting the pipeline_start
            # resolution with it would make the durable identity movable.
            for manifest_name, context_name in (
                ("base_sha", "merge_base"),
                ("head_sha", "head_sha"),
            ):
                value = resolved_git.get(context_name)
                if isinstance(value, str) and _FULL_SHA_RE.fullmatch(value):
                    git[manifest_name] = value

        steps = [
            self._manifest_step_event(event)
            for event in events
            if event.get("event") == "step"
        ]
        repo_path = pipeline.get("repo_path")
        repo_path = repo_path if isinstance(repo_path, str) else ""
        started, completed = self._project_manifest_agent_lifecycle(
            events, repo_path
        )
        incomplete = _incomplete_agent_executions(started, completed)

        pipeline_result = self._read_json_file("pipeline-result.json") or {}
        findings = self._extract_findings()
        summary = end.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}

        manifest = {
            "schema": EVENT_SCHEMA,
            "status": status,
            "run": {
                "id": start.get("run_id", ""),
                "session_id": pipeline.get("session_id") or None,
                "plugin_version": pipeline.get("plugin_version") or None,
                "mode": pipeline.get("mode") or None,
                "repo_path": pipeline.get("repo_path") or None,
                "output_dir": pipeline.get("output_dir") or self.output_dir,
                "started_at": start.get("timestamp"),
                "ended_at": end.get("timestamp"),
                "git": git,
            },
            "steps": steps,
            "agents": {
                "started": started,
                "completed": completed,
                "incomplete": incomplete,
            },
            "outcome": {
                "summary": summary,
                "pipeline_status": pipeline_result.get("status"),
                "verdict": pipeline_result.get("verdict"),
                "critic_verdict": pipeline_result.get("critic_verdict"),
                "verdict_source": pipeline_result.get("verdict_source"),
                "reconciliation": (
                    findings.get("reconciliation") if findings else None
                ),
            },
            "availability": {
                "pipeline": True,
                "transcript": False,
            },
        }
        final_info = manifest_sections.inspect_dispatch_plan(
            self.output_dir, "dispatch-plan.json"
        )
        manifest["dispatch"] = manifest_sections.build_dispatch_manifest(
            self.output_dir, final_info
        )
        coverage = manifest_sections.build_coverage_manifest(
            self.output_dir,
            events,
            context,
            repo_path,
            final_info,
            normalize_paths=normalize_repo_paths,
        )
        manifest["coverage"] = coverage
        manifest["availability"]["coverage"] = coverage is not None
        manifest["dependency_refresh"] = (
            manifest_sections.build_dependency_refresh_manifest(self.output_dir)
        )
        manifest["availability"]["dependency_refresh"] = (
            manifest["dependency_refresh"] is not None
        )
        manifest["reviewer_markdown"] = (
            manifest_sections.build_reviewer_markdown_manifest(self.output_dir)
        )
        manifest["availability"]["reviewer_markdown"] = (
            manifest["reviewer_markdown"] is not None
        )
        manifest["findings_markdown"] = (
            manifest_sections.build_findings_markdown_manifest(self.output_dir)
        )
        manifest["availability"]["findings_markdown"] = (
            manifest["findings_markdown"] is not None
        )
        manifest["worktree_hygiene"] = (
            manifest_sections.build_worktree_hygiene_manifest(self.output_dir)
        )
        manifest["availability"]["worktree_hygiene"] = (
            manifest["worktree_hygiene"] is not None
        )
        # A family of its own, never folded into manifest["agents"]: the
        # reconciliator and the decision critic are not reviewers, are
        # never in the dispatch plan, and must not move any reviewer count.
        manifest["synthesis_agents"] = (
            manifest_sections.build_synthesis_agents_manifest(self.output_dir)
        )
        manifest["availability"]["synthesis_agents"] = (
            manifest["synthesis_agents"] is not None
        )
        manifest["usage"] = (
            manifest_sections.build_usage_manifest(self.output_dir)
        )
        manifest["availability"]["usage"] = manifest["usage"] is not None
        manifest["skipped_steps"] = (
            manifest_sections.build_skipped_steps_manifest(self.output_dir)
        )
        manifest["availability"]["skipped_steps"] = (
            manifest["skipped_steps"] is not None
        )
        if self._event_parse_gaps:
            manifest["event_parse_gaps"] = self._event_parse_gaps
        return manifest

    def _materialize_manifest(self, status: str) -> None:
        """Atomically refresh the run manifest without affecting telemetry."""
        try:
            manifest_path = self.manifest_path
            if not manifest_path:
                return
            manifest = self._build_manifest(status)
            atomic_write_json(manifest_path, manifest)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    def reproject_usage(self) -> str:
        """Patch a SETTLED manifest's `usage` section out of band.

        A normal run projects `usage` wholesale, inside `_build_manifest`,
        every time an event is appended — the last time being `finalize()`.
        This exists for the one case that flow never reaches again: a
        manual re-run of `usage_snapshot.py`'s CLI, long after `finalize()`
        already returned, over a manifest that already settled. Nothing
        else revisits `usage` after that point, so a freshly re-measured
        snapshot would silently diverge from what the manifest still
        reports without this — the exact gap the CLI's monotonic upgrade
        exists to close.

        Two gates keep the patch narrow and safe, both fail CLOSED (no
        write, `False`):

        * ``status == "complete"``. A still-running manifest is
          `finalize()`'s territory alone — patching `usage` here while a
          run is still producing steps would risk publishing a manifest
          with a stale `usage` beside a fresher `run`/`dispatch`/etc., or
          racing `finalize()`'s own full rebuild. Skipping here costs
          nothing real: the in-pipeline step-11 call into this method
          reaches it while the manifest still reads "running" (finalize
          has not appended `pipeline_end` yet), so it is a no-op every
          time; `finalize()`'s own full rebuild, moments later in the same
          run, is what actually settles `usage` for a normal pipeline run.
        * ``schema == EVENT_SCHEMA``. An unsupported-schema manifest is not
          this method's to interpret; writing into a shape it does not
          recognize would be worse than the read-only paths that already
          refuse the same manifests.

        Only ``usage`` and its ``availability.usage`` companion flag are
        ever touched — every other field is left exactly as the last full
        rebuild wrote it. Returns a reason string rather than a bool,
        because "nothing was written" covers four different facts a caller
        cannot otherwise tell apart — and on a settled, current-schema
        manifest the io_failure case is the one a human re-running by
        hand needs to see:

        * ``"written"`` — a write happened (including one that honestly
          records ``usage: None`` because the snapshot itself is absent
          or unreadable).
        * ``"absent"`` — no manifest exists to patch.
        * ``"not_settled"`` — the manifest still reads running; the
          normal in-pipeline case.
        * ``"unsupported_schema"`` — a schema this method does not
          recognize, or a manifest that is not a JSON object at all.
        * ``"io_failure"`` — the marker, the manifest read, or the write
          raised.
        """
        # The marker read behind this property raises on a corrupt or
        # unreadable marker file — the same class _materialize_manifest
        # guards inside its own try. UnicodeDecodeError is a ValueError.
        try:
            manifest_path = self.manifest_path
        except (OSError, ValueError):
            return "io_failure"
        if not manifest_path or not os.path.isfile(manifest_path):
            return "absent"
        try:
            with open(manifest_path, encoding="utf-8") as source:
                manifest = json.load(source)
        except (OSError, ValueError):
            return "io_failure"
        if not isinstance(manifest, dict):
            return "unsupported_schema"
        if manifest.get("schema") != EVENT_SCHEMA:
            return "unsupported_schema"
        if manifest.get("status") != "complete":
            return "not_settled"
        section = manifest_sections.build_usage_manifest(self.output_dir)
        manifest["usage"] = section
        availability = manifest.get("availability")
        if not isinstance(availability, dict):
            availability = {}
            manifest["availability"] = availability
        availability["usage"] = section is not None
        try:
            atomic_write_json(manifest_path, manifest)
        except (OSError, TypeError, ValueError):
            return "io_failure"
        return "written"

    def _read_first_event(self) -> Optional[dict]:
        """Read the immutable first JSONL event (the pipeline_start line).

        Cached per instance after the first successful read — start() writes
        the line once and it never changes, so every consumer (event identity,
        quick mode, repo path) shares one file read instead of scanning the
        growing log.
        """
        cached = getattr(self, "_first_event", None)
        if cached is not None:
            return cached
        if not self.log_path or not os.path.isfile(self.log_path):
            return None
        try:
            with open(self.log_path, "rb") as f:
                first_line = f.readline().strip()
            if not first_line:
                return None
            event = json.loads(first_line)
        except (json.JSONDecodeError, OSError, TypeError, UnicodeError):
            return None
        if not isinstance(event, dict):
            return None
        self._first_event = event
        return event

    def _read_event_identity(self) -> tuple[int, str]:
        """Read durable event identity from memory or the pipeline_start event."""
        run_id = getattr(self, "_run_id", "")
        if run_id:
            return EVENT_SCHEMA, run_id

        start = self._read_first_event()
        if start is not None:
            return (
                start.get("schema", EVENT_SCHEMA),
                start.get("run_id", ""),
            )
        return EVENT_SCHEMA, ""

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
            line = ""
            with open(self._log_path) as f:
                if line_index == 0:
                    line = f.readline().strip()
                else:
                    for raw in f:
                        if raw.strip():
                            line = raw.strip()
            if line:
                return datetime.fromisoformat(json.loads(line)["timestamp"])
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            pass
        return None

    def _read_quick_mode(self) -> bool:
        """Read quick_mode from the pipeline_start event (first line of JSONL).

        Falls back to in-memory _quick_mode, then False. This handles the
        cross-process case where start() and finalize() run in different
        invocations of the pipeline script.
        """
        event = self._read_first_event()
        if event is not None and event.get("event") == "pipeline_start":
            pipeline = event.get("pipeline", {})
            if isinstance(pipeline, dict):
                return pipeline.get("quick_mode", False)
        return getattr(self, "_quick_mode", False)

    def _output_extracts(self) -> dict:
        """Extract every output-file summary once for shared consumption."""
        return {
            "context": self._extract_context(),
            "dispatch": self._extract_dispatch(),
            "agents": self._extract_agent_results(),
            "findings": self._extract_findings(),
        }

    def _snapshot(self, extracts: Optional[dict] = None) -> dict:
        """Build a snapshot of the output directory state."""
        extracts = extracts if extracts is not None else self._output_extracts()
        snap: Dict[str, Any] = {}
        snap["files"] = self._list_files()

        if extracts["context"]:
            snap["context"] = extracts["context"]

        if extracts["dispatch"]:
            snap["dispatch"] = extracts["dispatch"]

        if extracts["agents"]:
            snap["agent_results"] = extracts["agents"]

        if extracts["findings"]:
            snap["findings"] = extracts["findings"]

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
            changed_files = normalize_repo_paths(
                git.get("changed_files"), strict=True
            )
            return {
                "pr_number": pr.get("number"),
                "pr_title": pr.get("title"),
                "pr_author": pr.get("author"),
                "pr_url": pr.get("url"),
                "git_range": git.get("git_range"),
                "base_ref": git.get("base_ref"),
                "head_ref": git.get("head_ref"),
                "commit_count": git.get("commit_count"),
                "changed_files": changed_files,
                "changed_files_count": (
                    len(changed_files) if changed_files is not None else None
                ),
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
            if not isinstance(plan, dict):
                return None
            raw_agents = plan.get("agents")
            agents = validate_dispatch_plan_agents(raw_agents)
            by_status: Dict[str, List[str]] = {}
            for a in agents:
                status = a["status"]
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
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
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
                    data = load_review_document(path, base)
                except ValueError:
                    results[base] = {"error": "malformed"}
                    continue
                try:
                    findings = data["findings"]
                    severities = dict(Counter(
                        finding.get("severity", "medium").lower()
                        for finding in findings
                    ))
                    results[base] = {
                        "verdict": data.get("verdict"),
                        "finding_count": len(findings),
                        "severities": severities,
                    }
                    results[base].update(_advisory_measurement(data))
                except (KeyError, TypeError):
                    results[base] = {"error": "malformed"}
        except OSError:
            pass
        return results if results else None

    def _extract_findings(self) -> Optional[dict]:
        """Extract reconciled findings summary."""
        path = os.path.join(self.output_dir, "review-findings.json")
        read = read_findings_file(path)
        if read.status != FINDINGS_READ_OK:
            return None
        try:
            data = read.findings
            ledger_findings = data.get("findings", [])
            severities = dict(Counter(
                finding.get("severity", "medium").lower()
                for finding in ledger_findings
            ))
            findings = {
                "verdict": data.get("verdict"),
                "final_finding_count": len(ledger_findings),
                "severities": severities,
                "reconciliation": self._extract_reconciliation(data),
            }
            findings.update(_advisory_measurement(data))
            return findings
        except (json.JSONDecodeError, KeyError):
            return None

    @staticmethod
    def _extract_reconciliation(data: dict) -> Optional[dict]:
        """Project the ledger's reconciliation block verbatim.

        The data reaching here already passed the ledger's reader boundary
        (``read_findings_file``), which is the one authority on this block's
        shape. Re-checking it here only created a second, drifting copy of
        that contract.
        """
        meta = data.get("meta")
        reconciliation = (
            meta.get("reconciliation") if isinstance(meta, dict) else None
        )
        if not isinstance(reconciliation, dict):
            return None
        return dict(reconciliation)

    def _build_summary(
        self,
        total_duration_ms: Optional[int],
        extracts: Optional[dict] = None,
    ) -> dict:
        """Build pipeline summary from all available data."""
        extracts = extracts if extracts is not None else self._output_extracts()
        summary: Dict[str, Any] = {"total_duration_ms": total_duration_ms}
        summary["quick_mode"] = self._read_quick_mode()

        context = extracts["context"]
        if context:
            summary["pr_size_category"] = context.get("pr_size", {}).get("category")
            summary["changed_files_count"] = context.get("changed_files_count")
            summary["commit_count"] = context.get("commit_count")

        dispatch = extracts["dispatch"]
        if dispatch:
            summary["agents_total"] = dispatch["total_agents"]
            by_status = dispatch.get("by_status", {})
            summary["agents_dispatched"] = sum(
                len(v) for k, v in by_status.items() if k in DISPATCHED_STATUSES
            )
            summary["agents_skipped"] = sum(
                len(v) for k, v in by_status.items() if k in SKIPPED_STATUSES
            )

        agents = extracts["agents"]
        if agents:
            summary["agents_completed"] = len(agents)
            summary["total_agent_findings"] = sum(
                a.get("finding_count", 0)
                for a in agents.values()
                if "error" not in a
            )

        findings = extracts["findings"]
        if findings:
            summary["final_verdict"] = findings.get("verdict")
            summary["final_finding_count"] = findings.get("final_finding_count")
            summary["final_severities"] = findings.get("severities")
            if "suppressed_advisory_finding_count" in findings:
                summary["final_suppressed_advisory_finding_count"] = (
                    findings["suppressed_advisory_finding_count"]
                )
            if "verdict_without_advisory" in findings:
                summary["final_verdict_without_advisory"] = (
                    findings["verdict_without_advisory"]
                )

        return summary

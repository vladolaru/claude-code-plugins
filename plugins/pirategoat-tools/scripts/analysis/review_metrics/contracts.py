"""External contracts, shared constants, and time parsing."""

from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path


def _load_exact_path_module(name: str, path: Path, unavailable: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(unavailable)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_telemetry_contract():
    path = Path(__file__).resolve().parents[2] / "review" / "telemetry.py"
    return _load_exact_path_module(
        "review_telemetry_contract",
        path,
        "review telemetry contract unavailable",
    )


def _load_dispatch_status_contract():
    path = Path(__file__).resolve().parents[2] / "review" / "dispatch_status.py"
    return _load_exact_path_module(
        "review_dispatch_status_contract",
        path,
        "review dispatch status contract unavailable",
    )


_TELEMETRY_CONTRACT = _load_telemetry_contract()
_DISPATCH_STATUS_CONTRACT = _load_dispatch_status_contract()
DEFAULT_LOG_DIR = Path(_TELEMETRY_CONTRACT.LOG_DIR)
DEFAULT_SESSIONS_ROOT = Path("~/.claude/projects").expanduser()
DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "review" / "agent_registry.json"

_USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "effective_input_tokens",
    "output_tokens",
)
_AVAILABILITY_FAMILIES = (
    "dispatch",
    "coverage",
    "lifecycle",
    "outcomes",
    "raw_findings",
    "final_findings",
    "critic",
    "wall_time",
    "transcript",
    "usage",
    "orchestrator_usage",
    "agent_usage",
    "model_usage",
    "tool_failures",
    "artifact_writes",
    "scope_comparable_reads",
    "non_scope_comparable_reads",
    "observed_reads",
)
_AVAILABILITY_STATES = {"complete", "partial", "missing", "disabled"}
_FIXED_WARNING_CODES = {
    "legacy_log_no_manifest",
    "invalid_manifest_fallback",
    "running_lifecycle_overlay_invalid",
    "invalid_dispatch_projection",
    "duplicate_run_id_conflict",
    "registry_unavailable",
    "orchestrator_transcript_parse_gap",
    "orchestrator_transcript_time_gap",
    "orchestrator_stage_timeline_invalid",
    "expected_agents_unavailable",
    "expected_agent_identity_invalid",
    "agent_dispatch_schema_gap",
    "expected_agent_uncorrelated",
    "agent_transcript_missing",
    "duplicate_transcript_ignored",
    "agent_transcript_parse_gap",
    "agent_transcript_unresolved_calls",
    "agent_scope_evidence_missing",
}
_SUMMARY_FIELDS = (
    "total_duration_ms",
    "quick_mode",
    "pr_size_category",
    "changed_files_count",
    "commit_count",
    "agents_total",
    "agents_dispatched",
    "agents_skipped",
    "agents_completed",
    "total_agent_issues",
    "final_verdict",
    "final_issues",
)
_SEVERITIES = ("critical", "high", "medium", "low", "info")
_SUPPORTED_MANIFEST_SCHEMA_VERSION = 1
_OBSERVED_READS_SCHEMA_VERSION = 2
_REPORT_SCHEMA_VERSION = 2
_SUPPORTED_MANIFEST_STATUSES = {"running", "complete"}
_DISPATCHED_STATUSES = _DISPATCH_STATUS_CONTRACT.DISPATCHED_STATUSES
_SUPPORTED_DISPATCH_STATUSES = (
    _DISPATCH_STATUS_CONTRACT.SUPPORTED_DISPATCH_STATUSES
)
_SAFE_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
_PRODUCER_AGENT_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_WINDOWS_DRIVE_RE = re.compile(r"[A-Za-z]:")
_CRITIC_VERDICTS = {"STAND", "REVISE", "ESCALATE"}
_RETAINED_CRITIC_VALUES = _CRITIC_VERDICTS | {"unavailable"}
_TABLE_CELL_LIMIT = 120
_MAX_WALL_TIME_MS = 365 * 24 * 60 * 60 * 1000
_ANSI_ESCAPE_RE = re.compile(
    r"(?:"
    r"\x1b(?:\][^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)"
    r"|\[[0-?]*[ -/]*[@-~]|[@-_])"
    r"|\x9d[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)"
    r"|\x9b[0-?]*[ -/]*[@-~])"
)



def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None

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


_REVIEW_DIR = Path(__file__).resolve().parents[2] / "review"
_TELEMETRY_CONTRACT = _load_exact_path_module(
    "review_telemetry_contract",
    _REVIEW_DIR / "telemetry.py",
    "review telemetry contract unavailable",
)
_DISPATCH_STATUS_CONTRACT = _load_exact_path_module(
    "review_dispatch_status_contract",
    _REVIEW_DIR / "dispatch_status.py",
    "review dispatch status contract unavailable",
)
_CRITIC_CONTRACT = _load_exact_path_module(
    "review_critic_contract",
    _REVIEW_DIR / "critic.py",
    "review critic contract unavailable",
)
_ATOMIC_IO_CONTRACT = _load_exact_path_module(
    "review_atomic_io_contract",
    _REVIEW_DIR / "atomic_io.py",
    "review atomic io contract unavailable",
)
DEFAULT_LOG_DIR = Path(_TELEMETRY_CONTRACT.LOG_DIR)
DEFAULT_SESSIONS_ROOT = Path("~/.claude/projects").expanduser()
DEFAULT_REGISTRY = _REVIEW_DIR / "agent_registry.json"

# The lifecycle projection and incomplete-multiset rule are the producer's
# own implementations — the consumer must mirror them bit-exactly, so it
# calls them instead of re-implementing them.
_project_agent_lifecycle = _TELEMETRY_CONTRACT.project_agent_lifecycle
_incomplete_agent_executions = _TELEMETRY_CONTRACT._incomplete_agent_executions

_USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "effective_input_tokens",
    "output_tokens",
)
_PIPELINE_FAMILIES = (
    "dispatch",
    "coverage",
    "lifecycle",
    "outcomes",
    "raw_findings",
    "final_findings",
    "critic",
    "wall_time",
)
_TRANSCRIPT_FAMILIES = (
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
_AVAILABILITY_FAMILIES = _PIPELINE_FAMILIES + _TRANSCRIPT_FAMILIES
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
    "orchestrator_transcript_usage_missing",
    "orchestrator_transcript_unresolved_calls",
    "orchestrator_stage_timeline_invalid",
    "expected_agents_unavailable",
    "expected_agent_identity_invalid",
    "agent_dispatch_schema_gap",
    "expected_agent_uncorrelated",
    "agent_transcript_missing",
    "duplicate_transcript_ignored",
    "agent_transcript_parse_gap",
    "agent_transcript_time_gap",
    "agent_transcript_usage_missing",
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
_SEVERITIES = tuple(_TELEMETRY_CONTRACT._SEVERITY_FIELDS)
_SUPPORTED_MANIFEST_SCHEMA = 1
_OBSERVED_READS_SCHEMA = 2
_REPORT_SCHEMA = 2
_SUPPORTED_MANIFEST_STATUSES = {"running", "complete"}
_DISPATCHED_STATUSES = _DISPATCH_STATUS_CONTRACT.DISPATCHED_STATUSES
_SUPPORTED_DISPATCH_STATUSES = (
    _DISPATCH_STATUS_CONTRACT.SUPPORTED_DISPATCH_STATUSES
)
_SAFE_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
_PRODUCER_AGENT_NAME_RE = _DISPATCH_STATUS_CONTRACT.AGENT_NAME_RE
_WINDOWS_DRIVE_RE = re.compile(r"[A-Za-z]:")
_CRITIC_VERDICTS = frozenset(_CRITIC_CONTRACT.CRITIC_VERDICTS)
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
    # Keep byte-for-byte aligned with review_transcript._aware_timestamp —
    # the standalone transcript parser cannot import this package, so the
    # two bodies are mirrored deliberately. A divergence makes the same
    # boundary timestamp valid evidence in one module and a gap in the other.
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None

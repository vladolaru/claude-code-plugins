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
_FINDINGS_LEDGER_CONTRACT = _load_exact_path_module(
    "review_findings_ledger_contract",
    _REVIEW_DIR / "findings_ledger.py",
    "review findings ledger contract unavailable",
)
_DISPATCH_STATUS_CONTRACT = _load_exact_path_module(
    "review_dispatch_status_contract",
    _REVIEW_DIR / "dispatch_status.py",
    "review dispatch status contract unavailable",
)
_CRITIC_CONTRACT = _load_exact_path_module(
    "review_critic_adjustments_contract",
    _REVIEW_DIR / "critic_adjustments.py",
    "review critic adjustments contract unavailable",
)
_SYNTHESIS_CONTRACT = _load_exact_path_module(
    "review_synthesis_lifecycle_contract",
    _REVIEW_DIR / "synthesis_lifecycle.py",
    "review synthesis lifecycle contract unavailable",
)
_ATOMIC_IO_CONTRACT = _load_exact_path_module(
    "review_atomic_io_contract",
    _REVIEW_DIR / "atomic_io.py",
    "review atomic io contract unavailable",
)
_MANIFEST_SECTIONS_CONTRACT = _load_exact_path_module(
    "review_manifest_sections_contract",
    _REVIEW_DIR / "manifest_sections.py",
    "review manifest sections contract unavailable",
)
_ASSIGNMENT_VOCABULARY_CONTRACT = _load_exact_path_module(
    "review_assignment_vocabulary_contract",
    _REVIEW_DIR / "assignment_vocabulary.py",
    "review assignment vocabulary contract unavailable",
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
_CHANGED_FILES_FIELD = _ASSIGNMENT_VOCABULARY_CONTRACT.CHANGED_FILES
_REVIEWABLE_FILES_FIELD = _ASSIGNMENT_VOCABULARY_CONTRACT.REVIEWABLE_FILES
_ASSIGNED_FILES_BY_AGENT_FIELD = (
    _ASSIGNMENT_VOCABULARY_CONTRACT.ASSIGNED_FILES_BY_AGENT
)
_ASSIGNED_FILES_FIELD = _ASSIGNMENT_VOCABULARY_CONTRACT.ASSIGNED_FILES
_FILE_EXCLUSIONS_FIELD = _ASSIGNMENT_VOCABULARY_CONTRACT.FILE_EXCLUSIONS
_UNASSIGNED_REVIEWABLE_FILES_FIELD = (
    _ASSIGNMENT_VOCABULARY_CONTRACT.UNASSIGNED_REVIEWABLE_FILES
)
_ASSIGNMENT_FIELDS = _ASSIGNMENT_VOCABULARY_CONTRACT.ASSIGNMENT_FIELDS
_ASSIGNMENT_PATH_LIST_FIELDS = (
    _ASSIGNMENT_VOCABULARY_CONTRACT.ASSIGNMENT_PATH_LIST_FIELDS
)
_ASSIGNMENT_COUNTABLE_LIST_FIELDS = (
    _ASSIGNMENT_VOCABULARY_CONTRACT.ASSIGNMENT_COUNTABLE_LIST_FIELDS
)
_ASSIGNMENT_TABLE_FIELDS = (
    _ASSIGNMENT_VOCABULARY_CONTRACT.ASSIGNMENT_TABLE_FIELDS
)
# The section-status vocabularies are the producer's own private
# constants — manifest_sections.py has no importable export for most of
# them — reached via the same exact-path contract `_incomplete_agent_executions`
# above uses, instead of restated literals. Widening any vocabulary in
# the producer therefore moves the consumer's fallback set in lockstep;
# see tests/analysis/test_review_run_metrics.py's drift-detection pin.
_WORKTREE_HYGIENE_STATUSES = (
    _MANIFEST_SECTIONS_CONTRACT._WORKTREE_HYGIENE_STATUSES
)
_USAGE_SNAPSHOT_AVAILABILITY_STATES = (
    _MANIFEST_SECTIONS_CONTRACT._USAGE_AVAILABILITY_STATES
)
_DEPENDENCY_REFRESH_STATUSES = (
    _MANIFEST_SECTIONS_CONTRACT._DEPENDENCY_REFRESH_STATUSES
)
_DEPENDENCY_REFRESH_EXIT_STATUSES = (
    _MANIFEST_SECTIONS_CONTRACT._DEPENDENCY_REFRESH_EXIT_STATUSES
)
# Retired producer vocabulary kept only so historical run manifests remain
# measurable. New manifests use dependency_refresh.precheck instead.
_HISTORICAL_DEPENDENCY_REFRESH_SKIP_REASONS = frozenset({
    "dirty_worktree",
    "worktree_status_failed",
})
_MAX_DEPENDENCY_REFRESH_COMMANDS = (
    _MANIFEST_SECTIONS_CONTRACT._MAX_DEPENDENCY_REFRESH_COMMANDS
)
_MAX_DIRTY_FILES = _MANIFEST_SECTIONS_CONTRACT._MAX_DIRTY_FILES
# Shared by reviewer_markdown (step 8's per-reviewer render) and
# findings_markdown (steps 9/11's review-findings.md render) — one
# producer-side validator (`_sanitize_derived_markdown_outcome`) covers
# both, so one vocabulary covers both here too.
_DERIVED_MARKDOWN_STATUSES = (
    _MANIFEST_SECTIONS_CONTRACT._DERIVED_MARKDOWN_STATUSES
)
_PIPELINE_FAMILIES = (
    "dispatch",
    "coverage",
    "lifecycle",
    # Distinct from "lifecycle": that family is the REVIEWER lifecycle
    # projected from agent_start/agent_complete events. The reconciliator
    # and the decision critic produce neither, so they are measured as
    # their own family and never move a reviewer count.
    "synthesis_agents",
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
    "total_agent_findings",
    "final_verdict",
    "final_finding_count",
)
_SEVERITIES = tuple(_TELEMETRY_CONTRACT._SEVERITY_FIELDS)
# The ledger's producer owns the reconciliation block; telemetry projects it
# verbatim. These come from that producer, not from the projection.
_RECONCILIATION_COUNT_FIELDS = tuple(
    _FINDINGS_LEDGER_CONTRACT.RECONCILIATION_COUNT_FIELDS
)
_RECONCILIATION_AGENT_FIELDS = tuple(
    _FINDINGS_LEDGER_CONTRACT.RECONCILIATION_AGENT_LIST_FIELDS
)
_RECONCILIATION_FIELDS = frozenset(
    _FINDINGS_LEDGER_CONTRACT.RECONCILIATION_FIELDS
)
# Lockstep with review/telemetry.py's EVENT_SCHEMA. Schema 3 requires the
# reviewed-files projection in every coverage section.
_SUPPORTED_MANIFEST_SCHEMA = 3
_OBSERVED_READS_SCHEMA = 2
# `reviewed_files` is the cohort's canonical aggregate of the per-agent
# reviewed-file counts.
_REPORT_SCHEMA = 3
_SUPPORTED_MANIFEST_STATUSES = {"running", "complete"}
_DISPATCHED_STATUSES = _DISPATCH_STATUS_CONTRACT.DISPATCHED_STATUSES
_SUPPORTED_DISPATCH_STATUSES = (
    _DISPATCH_STATUS_CONTRACT.SUPPORTED_DISPATCH_STATUSES
)
_SAFE_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
_PRODUCER_AGENT_NAME_RE = _DISPATCH_STATUS_CONTRACT.AGENT_NAME_RE
_WINDOWS_DRIVE_RE = re.compile(r"[A-Za-z]:")
_CRITIC_VERDICTS = frozenset(_CRITIC_CONTRACT.CRITIC_VERDICTS)
# Deliberately NOT in _CRITIC_VERDICTS: "SKIPPED" records that no critique
# happened. Current quick-mode skips commit that verdict without a dispatch
# marker and therefore create no lifecycle row; a dispatched crash instead
# has no usable verdict, stalls, and degrades. The aggregate still recognizes
# historical SKIPPED rows so they stay out of critique-duration statistics.
_CRITIC_VERDICT_SKIPPED = _CRITIC_CONTRACT.CRITIC_VERDICT_SKIPPED
# The producer-declared optional-section contract (mirrors the
# ROW_KEYS pattern just below): the telemetry module names which
# availability keys it ever assigns, and the sanitize-layer table-driven
# loop parametrizes over this tuple instead of five bespoke blocks.
_OPTIONAL_SECTION_AVAILABILITY_KEYS = (
    _TELEMETRY_CONTRACT.OPTIONAL_SECTION_AVAILABILITY_KEYS
)
# The synthesis-agent row shape and identities, owned by the producer. The
# consumer mirrors them instead of respelling them, so a renamed agent or
# a new row key breaks this package's tests rather than silently dropping
# a measurement.
_SYNTHESIS_ROW_KEYS = _SYNTHESIS_CONTRACT.ROW_KEYS
_SYNTHESIS_RECONCILIATOR = _SYNTHESIS_CONTRACT.RECONCILIATOR
_SYNTHESIS_DECISION_CRITIC = _SYNTHESIS_CONTRACT.DECISION_CRITIC
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

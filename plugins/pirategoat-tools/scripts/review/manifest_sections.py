"""Manifest section builders — pure functions over the run's output dir.

Extracted from ReviewTelemetry so the telemetry class stays an event
logger; these read completed artifacts and build manifest sections.
Behavior-preserving move (2026-08-03); see test_telemetry.py.
"""

import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List, Optional

try:
    from .dependency_refresh import (
        _MAX_DIRTY_FILES,
        DEPENDENCY_REFRESH_SKIP_REASONS,
        load_dependency_refresh_report,
    )
    from .dispatch_status import (
        AGENT_NAME_RE,
        DISPATCHED_STATUSES,
        validate_dispatch_plan_agents,
    )
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.dependency_refresh import (
        _MAX_DIRTY_FILES,
        DEPENDENCY_REFRESH_SKIP_REASONS,
        load_dependency_refresh_report,
    )
    from review.dispatch_status import (
        AGENT_NAME_RE,
        DISPATCHED_STATUSES,
        validate_dispatch_plan_agents,
    )


_DEPENDENCY_REFRESH_STATUSES = frozenset({"completed", "partial", "failed"})
_MAX_DEPENDENCY_REFRESH_COMMANDS = 32
_MAX_DEPENDENCY_REFRESH_DIRECTORY_CHARS = 200
_MAX_DEPENDENCY_REFRESH_COMMAND_CHARS = 500
_REVIEWER_MARKDOWN_STATUSES = frozenset({
    "not_run", "complete", "partial", "failed",
})
_WORKTREE_HYGIENE_STATUSES = frozenset({
    "clean", "changed_during_review", "unknown",
})
_USAGE_AVAILABILITY_STATES = frozenset({"complete", "partial", "missing"})
# Mirrors the token-usage vocabulary the analysis package accumulates. The
# two halves of a snapshot are summed over the same field set, so a map
# missing any of them is not a usable measurement.
_USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "effective_input_tokens",
    "output_tokens",
)


def read_json_file(output_dir: str, name: str) -> Optional[dict]:
    """Read an output JSON object without letting failures escape."""
    path = os.path.join(output_dir, name)
    try:
        with open(path) as source:
            value = json.load(source)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def safe_dispatch_string(value: Any) -> Optional[str]:
    """Return a dispatch scalar only when it is a string."""
    return value if isinstance(value, str) else None


def safe_dispatch_strings(value: Any) -> List[str]:
    """Allowlist a list of planner-produced string signals or checks."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def is_dispatched(status: Any) -> bool:
    """Return whether one supported plan status dispatches an agent."""
    return isinstance(status, str) and status in DISPATCHED_STATUSES


def inspect_dispatch_plan(output_dir: str, filename: str) -> dict:
    """Read a plan into safe list and index views with validity metadata."""
    result = {
        "available": False,
        "plan": {},
        "entries": [],
        "index": {},
        "duplicates": [],
    }
    plan = read_json_file(output_dir, filename)
    if plan is None:
        return result

    agents = plan.get("agents")
    try:
        valid_entries = validate_dispatch_plan_agents(agents)
    except ValueError:
        return result

    names = []
    for agent in valid_entries:
        name = agent.get("name")
        if not isinstance(name, str) or not AGENT_NAME_RE.fullmatch(name):
            return result
        names.append(name)

    counts = Counter(names)
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    result.update({
        "available": True,
        "plan": plan,
        "entries": valid_entries,
        "index": (
            {}
            if duplicates
            else {agent["name"]: agent for agent in valid_entries}
        ),
        "duplicates": duplicates,
    })
    return result


def registry_dispatch_metadata() -> Dict[str, dict]:
    """Load safe static routing metadata from the adjacent agent registry."""
    path = os.path.join(os.path.dirname(__file__), "agent_registry.json")
    try:
        with open(path) as source:
            registry = json.load(source)
        agents = registry.get("agents", {})
        return agents if isinstance(agents, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def planner_signals(plan: dict, name: str, agent: dict) -> List[str]:
    """Select deterministic planner signals without copying arbitrary fields."""
    prefix = f"{name}:"
    top_level = plan.get("agent_signals", [])
    if isinstance(top_level, list):
        matched = [
            signal
            for signal in top_level
            if isinstance(signal, str) and signal.startswith(prefix)
        ]
        if matched:
            return matched

    reason = safe_dispatch_string(agent.get("reason"))
    return [reason] if reason else []


def build_dispatch_manifest(output_dir: str, final_info: dict) -> dict:
    """Compare the deterministic plan with main-orchestrator adjustments."""
    initial_info = inspect_dispatch_plan(
        output_dir, "dispatch-plan.initial.json"
    )

    initial_available = initial_info["available"]
    final_available = final_info["available"]
    duplicate_names = {}
    invalid_reasons = []
    if not initial_available:
        invalid_reasons.append("planner_baseline_unavailable")
    if not final_available:
        invalid_reasons.append("final_plan_unavailable")
    if initial_info["duplicates"]:
        duplicate_names["planner_baseline"] = initial_info["duplicates"]
        invalid_reasons.append("planner_baseline_duplicate_agents")
    if final_info["duplicates"]:
        duplicate_names["final_plan"] = final_info["duplicates"]
        invalid_reasons.append("final_plan_duplicate_agents")

    agent_sets_match = True
    if (
        initial_available
        and final_available
        and not initial_info["duplicates"]
        and not final_info["duplicates"]
    ):
        agent_sets_match = (
            set(initial_info["index"]) == set(final_info["index"])
        )
        if not agent_sets_match:
            invalid_reasons.append("dispatch_agent_set_mismatch")

    comparison_available = (
        initial_available
        and final_available
        and not initial_info["duplicates"]
        and not final_info["duplicates"]
        and agent_sets_match
    )
    planner_entries = (
        initial_info["entries"]
        if initial_available
        else final_info["entries"] if final_available else []
    )
    result = {
        "planner_baseline_available": initial_available,
        "final_plan_available": final_available,
        "comparison_available": comparison_available,
        "planner_candidate_count": sum(
            is_dispatched(agent.get("status"))
            for agent in planner_entries
        ),
        "final_dispatch_count": sum(
            is_dispatched(agent.get("status"))
            for agent in final_info["entries"]
        ),
        "adjustment_counts": {
            "added": 0,
            "removed": 0,
            "unchanged": 0,
        },
        "invalid_reason_codes": invalid_reasons,
        "agents": {},
    }
    if duplicate_names:
        result["duplicate_agent_names"] = duplicate_names
    if invalid_reasons == ["dispatch_agent_set_mismatch"]:
        result["plan_projections"] = {
            "planner_baseline": {
                name: initial_info["index"][name]["status"]
                for name in sorted(initial_info["index"])
            },
            "final_plan": {
                name: final_info["index"][name]["status"]
                for name in sorted(final_info["index"])
            },
        }

    if (
        not final_available
        or initial_info["duplicates"]
        or final_info["duplicates"]
        or not agent_sets_match
    ):
        return result

    if initial_available:
        initial_plan = initial_info["plan"]
        initial_agents = initial_info["index"]
    else:
        # Required legacy projection: without a usable baseline, show the
        # final plan as unchanged while comparison_available remains false.
        initial_plan = final_info["plan"]
        initial_agents = final_info["index"]
    final_agents = final_info["index"]

    registry = registry_dispatch_metadata()
    decisions = {}

    for name in sorted(set(initial_agents) | set(final_agents)):
        initial = initial_agents.get(name, {})
        final = final_agents.get(name, {})
        initial_status = safe_dispatch_string(initial.get("status"))
        final_status = safe_dispatch_string(final.get("status"))
        initially_dispatched = is_dispatched(initial_status)
        finally_dispatched = is_dispatched(final_status)

        if initially_dispatched == finally_dispatched:
            change = "unchanged"
        elif finally_dispatched:
            change = "added"
        else:
            change = "removed"
        result["adjustment_counts"][change] += 1

        registry_agent = registry.get(name, {})
        if not isinstance(registry_agent, dict):
            registry_agent = {}
        configured_planner_checks = safe_dispatch_strings(
            registry_agent.get("triage_checks")
        )

        decisions[name] = {
            "domain": (
                safe_dispatch_string(initial.get("domain"))
                or safe_dispatch_string(final.get("domain"))
            ),
            "initial_status": initial_status,
            "initial_reason": safe_dispatch_string(initial.get("reason")),
            "final_status": final_status,
            "final_reason": safe_dispatch_string(final.get("reason")),
            "planner_signals": planner_signals(
                initial_plan, name, initial
            ),
            "configured_planner_checks": configured_planner_checks,
            "model_tier": (
                safe_dispatch_string(initial.get("model_tier"))
                or safe_dispatch_string(final.get("model_tier"))
                # Repo-contributed reviewer entries carry their explicit
                # model override under "model" (the adapter dispatch
                # contract step 6 honors) and have no registry entry to
                # fall back to — without this their requested tier is
                # omitted from dispatch telemetry.
                or safe_dispatch_string(initial.get("model"))
                or safe_dispatch_string(final.get("model"))
                or safe_dispatch_string(registry_agent.get("model_tier"))
            ),
            "declared_model": (
                safe_dispatch_string(initial.get("declared_model"))
                or safe_dispatch_string(final.get("declared_model"))
            ),
            "adjustment_reason": safe_dispatch_string(
                final.get("override_reason")
            ),
            "change": change,
        }

    result["agents"] = decisions
    return result


def build_coverage_manifest(
    output_dir: str,
    events: List[dict],
    context: Optional[dict],
    repo_path: str,
    final_info: dict,
    normalize_paths,
) -> Optional[dict]:
    """Build descriptive generated-scope coverage from durable inputs."""
    try:
        if not isinstance(context, dict):
            return None
        context_git = context.get("git")
        if not isinstance(context_git, dict):
            return None
        changed = normalize_paths(
            context_git.get("changed_files"),
            repo_path=repo_path,
            strict=True,
        )

        if not final_info["available"] or final_info["duplicates"]:
            return None
        reviewable = normalize_paths(
            final_info["plan"].get("changed_files"),
            repo_path=repo_path,
            strict=True,
        )
        if changed is None or reviewable is None:
            return None

        changed_set = set(changed)
        reviewable_set = set(reviewable)
        if not reviewable_set.issubset(changed_set):
            return None

        final_agents = final_info["index"]
        if any(
            not isinstance(agent.get("status"), str)
            for agent in final_agents.values()
        ):
            return None

        by_agent_sets: Dict[str, set[str]] = {}
        for event in events:
            if event.get("event") != "agent_start":
                continue
            name = event.get("agent")
            final_agent = final_agents.get(name)
            if not final_agent or not is_dispatched(
                final_agent.get("status")
            ):
                continue
            scope = event.get("scope")
            if not isinstance(scope, dict) or not isinstance(
                scope.get("paths"), list
            ):
                return None
            scope_paths = normalize_paths(
                scope["paths"],
                repo_path=repo_path,
                strict=True,
                normalize_backslash_separators=False,
                decode_git_quoted=False,
            )
            if scope_paths is None:
                return None
            by_agent_sets.setdefault(name, set()).update(
                path for path in scope_paths if path in changed_set
            )

        by_agent = {
            name: sorted(paths)
            for name, paths in sorted(by_agent_sets.items())
        }
        assigned_set = reviewable_set.intersection(
            path for paths in by_agent_sets.values() for path in paths
        )

        return {
            "changed": changed,
            "reviewable": reviewable,
            "by_agent": by_agent,
            "assigned": sorted(assigned_set),
            "excluded": [
                {"path": path, "reason": "noise_filtered"}
                for path in sorted(changed_set - reviewable_set)
            ],
            "uncovered": sorted(reviewable_set - assigned_set),
            "semantics": "generated_scope_not_proof_of_model_read",
        }
    except Exception as err:  # noqa: BLE001 — best-effort by design
        # The explicit `return None` paths above are legitimate absence
        # (context/plan shape, subset, agent-status invariants) and stay
        # silent — that is normal operation, not a defect. Only a genuine
        # builder bug reaches here, and it must not be indistinguishable
        # from those legitimate paths, so it is surfaced on stderr before
        # falling back to the same fail-open `None`.
        print(
            f"coverage manifest build failed for {output_dir}: {err}",
            file=sys.stderr,
        )
        return None


def build_dependency_refresh_manifest(output_dir: str):
    """Sanitize the orchestrator's dependency-refresh report.

    The orchestrator report is free text and the verification artifact is
    script-owned: only known fields with expected shapes survive, commands
    are length-capped measurement evidence (like adjustment reasons), and
    a non-object file reads as absent. Returns None when refresh was never
    requested and neither evidence artifact exists — absent, not a
    measured no-op.
    """
    config = read_json_file(output_dir, "run-config.json") or {}
    requested = config.get("refresh_dependencies") is True
    report, _report_load_failed = load_dependency_refresh_report(output_dir)
    verification = read_json_file(
        output_dir, "dependency-refresh-verification.json"
    )
    if not requested and report is None and verification is None:
        return None
    result = {"requested": requested, "reported": report is not None}

    if verification is not None and verification.get("skipped") is True:
        skipped_reason = verification.get("skipped_reason")
        dirty_files = verification.get("dirty_files")
        result.update({
            "skipped": True,
            "skipped_reason": (
                skipped_reason
                if skipped_reason in DEPENDENCY_REFRESH_SKIP_REASONS
                else "invalid"
            ),
            "dirty_files": [
                path for path in (
                    dirty_files if isinstance(dirty_files, list) else []
                )
                if isinstance(path, str)
            ][:_MAX_DIRTY_FILES],
        })
    elif verification is not None:
        commands_allowed = verification.get("commands_allowed")
        tracked_files_dirty = verification.get("tracked_files_dirty")
        disallowed_commands = verification.get("disallowed_commands")
        result["verification"] = {
            "report_present": verification.get("report_present") is True,
            "commands_allowed": (
                commands_allowed
                if isinstance(commands_allowed, bool) else None
            ),
            "disallowed_commands": [
                command[:_MAX_DEPENDENCY_REFRESH_COMMAND_CHARS]
                for command in (
                    disallowed_commands[:_MAX_DEPENDENCY_REFRESH_COMMANDS]
                    if isinstance(disallowed_commands, list) else []
                )
                if isinstance(command, str)
            ],
            "tracked_files_dirty": (
                tracked_files_dirty
                if isinstance(tracked_files_dirty, bool) else None
            ),
            "verification_failed": (
                verification.get("verification_failed") is True
            ),
        }

    if report is None:
        return result

    status = report.get("status")
    result["status"] = (
        status
        if isinstance(status, str) and status in _DEPENDENCY_REFRESH_STATUSES
        else "invalid"
    )
    dirty = report.get("tracked_files_dirty")
    result["tracked_files_dirty"] = dirty if isinstance(dirty, bool) else None

    sanitized = []
    commands = report.get("commands")
    if isinstance(commands, list):
        for entry in commands[:_MAX_DEPENDENCY_REFRESH_COMMANDS]:
            if not isinstance(entry, dict):
                continue
            directory = entry.get("directory")
            command = entry.get("command")
            exit_status = entry.get("exit_status")
            sanitized.append({
                "directory": (
                    directory[:_MAX_DEPENDENCY_REFRESH_DIRECTORY_CHARS]
                    if isinstance(directory, str) else None
                ),
                "command": (
                    command[:_MAX_DEPENDENCY_REFRESH_COMMAND_CHARS]
                    if isinstance(command, str) else None
                ),
                "exit_status": (
                    exit_status
                    if exit_status in ("ok", "failed") else "invalid"
                ),
            })
    result["commands"] = sanitized
    return result


def build_reviewer_markdown_manifest(output_dir: str) -> Optional[dict]:
    """Project the script-owned reviewer-Markdown outcome into the manifest."""
    state = read_json_file(output_dir, "pipeline-state.json")
    outcome = state.get("reviewer_markdown") if state is not None else None
    if not isinstance(outcome, dict):
        return None

    ran = outcome.get("ran")
    written = outcome.get("written")
    expected = outcome.get("expected")
    status = outcome.get("status")
    if (
        not isinstance(ran, bool)
        or not isinstance(written, int)
        or isinstance(written, bool)
        or written < 0
        or not isinstance(expected, int)
        or isinstance(expected, bool)
        or expected < 0
        or status not in _REVIEWER_MARKDOWN_STATUSES
        or (ran and status == "not_run")
        or (not ran and status != "not_run")
        or (status == "complete" and written != expected)
    ):
        return None

    return {
        "ran": ran,
        "written": written,
        "expected": expected,
        "status": status,
    }


def build_worktree_hygiene_manifest(output_dir: str) -> Optional[dict]:
    """Project the step-11 worktree-hygiene artifact into the manifest.

    None means the run never measured hygiene — the artifact is absent or
    unreadable, as on an older run or one whose step 11 never reached the
    check. That is a different fact from a measured result whose status is
    itself "unknown" (the check ran but had no verified baseline to compare
    against), which projects as a section like any other outcome.

    The entry lists are the raw porcelain lines the check collected, so
    they are filtered to strings and every field falls back to the
    artifact's own absent-measurement values rather than to a fabricated
    one: an unrecognizable status reads as "unknown", never as "clean".
    """
    data = read_json_file(output_dir, "worktree-hygiene.json")
    if data is None:
        return None

    def entries(key: str) -> List[str]:
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [entry for entry in value if isinstance(entry, str)]

    status = data.get("status")
    captured_at = data.get("baseline_captured_at")
    return {
        "status": (
            status
            if isinstance(status, str) and status in _WORKTREE_HYGIENE_STATUSES
            else "unknown"
        ),
        "new_files": entries("new_files"),
        "changed_files": entries("changed_files"),
        "probe_residue_removed": entries("probe_residue_removed"),
        "baseline_captured_at": (
            captured_at if isinstance(captured_at, str) else None
        ),
    }


def _safe_usage_map(value: Any) -> Optional[Dict[str, int]]:
    """Return one complete token-usage map, or None for unusable evidence.

    All-or-nothing on purpose: a map missing a field or carrying a
    non-integer count cannot be summed or compared, and filling the hole
    with a zero would publish a fabricated measurement beside real ones.
    """
    if not isinstance(value, dict):
        return None
    usage: Dict[str, int] = {}
    for field in _USAGE_FIELDS:
        count = value.get(field)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return None
        usage[field] = count
    return usage


def _safe_agent_count(value: Any) -> Optional[int]:
    """Return a countable agent total, or None when it was not measured."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def build_usage_manifest(output_dir: str) -> Optional[dict]:
    """Project the step-11 token-usage snapshot into the manifest.

    None means the run never measured usage — the artifact is absent or
    unreadable, as on an older run, a run whose finalize never reached the
    capture, or one whose capture could not write. That is a different
    fact from a snapshot that ran and found nothing to measure (a Codex
    host writes no Claude-format transcripts at all), which carries
    availability "missing" and projects as a section like any other
    outcome.

    The two halves keep separate availability labels because their
    warrants differ: every subagent transcript is closed when the snapshot
    is taken, while the orchestrator is measuring its own still-open
    session. Flattening them would let a structurally partial number be
    read as a complete one.
    """
    data = read_json_file(output_dir, "usage-snapshot.json")
    if data is None:
        return None

    availability = data.get("availability")
    availability = availability if isinstance(availability, dict) else {}

    def state(name: str) -> str:
        value = availability.get(name)
        if isinstance(value, str) and value in _USAGE_AVAILABILITY_STATES:
            return value
        return "missing"

    by_model_raw = data.get("usage_by_model")
    by_model: Dict[str, Dict[str, int]] = {}
    if isinstance(by_model_raw, dict):
        for model, usage in by_model_raw.items():
            safe = _safe_usage_map(usage)
            if isinstance(model, str) and safe is not None:
                by_model[model] = safe

    rows_raw = data.get("subagent_usage")
    rows: List[dict] = []
    if isinstance(rows_raw, list):
        for row in rows_raw:
            if not isinstance(row, dict) or not isinstance(
                row.get("agent"), str
            ):
                continue
            model = row.get("model")
            rows.append({
                "agent": row["agent"],
                "model": model if isinstance(model, str) else None,
                "usage": _safe_usage_map(row.get("usage")),
            })

    counts = data.get("agents_measured")
    counts = counts if isinstance(counts, dict) else {}
    captured_at = data.get("captured_at")
    return {
        "captured_at": captured_at if isinstance(captured_at, str) else None,
        "availability": {
            "subagents": state("subagents"),
            "orchestrator": state("orchestrator"),
        },
        "agents_measured": {
            "measured": _safe_agent_count(counts.get("measured")),
            "expected": _safe_agent_count(counts.get("expected")),
        },
        "subagent_totals": _safe_usage_map(data.get("subagent_totals")),
        "orchestrator_usage": _safe_usage_map(data.get("orchestrator_usage")),
        "usage_by_model": by_model,
        "by_agent": rows,
    }


def build_skipped_steps_manifest(output_dir: str) -> Optional[list]:
    """Skip decisions recorded by the step router; None when unmeasured.

    None (state absent/unreadable, or a pre-recording run without the
    key) is distinct from [] — a run whose router measured zero skips.

    Only records carrying a real step number survive; a run that reaches
    this builder without one has no decision to report, and inventing a
    step for it would put a fabricated skip in the ledger the audit
    reconciles against completions and telemetry.

    The ledger is authoritative only on a manifest whose status is
    "complete". Manifests materialize at every step, so a running one may
    carry a partial ledger — an abandoned run publishes whatever it had
    reached, with availability true — and it also lags one invocation,
    because the router records its skips after telemetry has already
    logged that step. Only on a complete manifest do recorded completions
    plus recorded skips account for every step in the contract.
    """
    state = read_json_file(output_dir, "pipeline-state.json")
    # The explicit key check is behaviorally redundant with the isinstance
    # gate below (an absent key reads as None, which is not a list) and is
    # kept as intent: absence and malformation are different facts that
    # happen to share one outcome, so pinning them separately keeps a
    # later "treat garbage as zero" edit from silently promoting a
    # pre-recording run to a measured zero.
    if state is None or "skipped_steps" not in state:
        return None
    skipped = state.get("skipped_steps")
    if not isinstance(skipped, list):
        return None
    projected = []
    for item in skipped:
        if (
            isinstance(item, dict)
            and isinstance(item.get("step"), int)
            and not isinstance(item.get("step"), bool)
        ):
            projected.append({
                "step": item["step"],
                "title": item.get("title") or "",
                "condition": item.get("condition") or "",
            })
    return projected

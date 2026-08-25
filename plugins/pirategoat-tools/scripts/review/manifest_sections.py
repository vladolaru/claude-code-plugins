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
    from .assignment_vocabulary import (
        ASSIGNED_FILES,
        ASSIGNED_FILES_BY_AGENT,
        ASSIGNMENT_FIELDS,
        CHANGED_FILES,
        FILE_EXCLUSIONS,
        REVIEWABLE_FILES,
        UNASSIGNED_REVIEWABLE_FILES,
    )
    from .agent.coverage import ReviewAccountingError, derive_review_accounting
    from .agent.output import load_review_document
    from .reviewer_names import derive_reviewer_name
    from .reviewer_lifecycle import review_paths
    from .dependency_refresh import (
        EXIT_STATUSES,
        REPORT_STATUSES,
        _MAX_DIRTY_FILES,
        _MAX_DIRTY_FILE_CHARS,
        _MAX_REPORTED_COMMANDS,
        load_dependency_refresh_report,
    )
    from .dispatch_status import (
        AGENT_NAME_RE,
        DISPATCHED_STATUSES,
        validate_dispatch_plan_agents,
    )
    from .synthesis_lifecycle import (
        LIFECYCLE_FILENAME as _SYNTHESIS_LIFECYCLE_FILENAME,
        LIFECYCLE_SCHEMA as _SUPPORTED_SYNTHESIS_LIFECYCLE_SCHEMA,
        ROW_KEYS as _SYNTHESIS_ROW_KEYS,
    )
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.assignment_vocabulary import (
        ASSIGNED_FILES,
        ASSIGNED_FILES_BY_AGENT,
        ASSIGNMENT_FIELDS,
        CHANGED_FILES,
        FILE_EXCLUSIONS,
        REVIEWABLE_FILES,
        UNASSIGNED_REVIEWABLE_FILES,
    )
    from review.agent.coverage import ReviewAccountingError, derive_review_accounting
    from review.agent.output import load_review_document
    from review.reviewer_names import derive_reviewer_name
    from review.reviewer_lifecycle import review_paths
    from review.dependency_refresh import (
        EXIT_STATUSES,
        REPORT_STATUSES,
        _MAX_DIRTY_FILES,
        _MAX_DIRTY_FILE_CHARS,
        _MAX_REPORTED_COMMANDS,
        load_dependency_refresh_report,
    )
    from review.dispatch_status import (
        AGENT_NAME_RE,
        DISPATCHED_STATUSES,
        validate_dispatch_plan_agents,
    )
    from review.synthesis_lifecycle import (
        LIFECYCLE_FILENAME as _SYNTHESIS_LIFECYCLE_FILENAME,
        LIFECYCLE_SCHEMA as _SUPPORTED_SYNTHESIS_LIFECYCLE_SCHEMA,
        ROW_KEYS as _SYNTHESIS_ROW_KEYS,
    )


_DEPENDENCY_REFRESH_STATUSES = frozenset(REPORT_STATUSES)
_DEPENDENCY_REFRESH_EXIT_STATUSES = frozenset(EXIT_STATUSES)
_MAX_DEPENDENCY_REFRESH_COMMANDS = _MAX_REPORTED_COMMANDS
# Shared by both derived-Markdown families (reviewer_markdown, step 8's
# per-reviewer render; findings_markdown, steps 9/11's review-findings.md
# render) — the same vocabulary `briefings.py`'s
# `_derived_markdown_status_line(key=..., label=...)` already treats as
# one family for its human-facing status line.
_DERIVED_MARKDOWN_STATUSES = frozenset({
    "not_run", "complete", "partial", "failed",
})
_WORKTREE_HYGIENE_STATUSES = frozenset({
    "clean", "changed_during_review", "unknown",
})
_USAGE_AVAILABILITY_STATES = frozenset({"complete", "partial", "missing"})
# The one snapshot schema this projection understands. An artifact
# announcing a different one was written by a producer whose field
# meanings this builder cannot vouch for, so it reads as unmeasured
# rather than being projected on the assumption that the names still
# mean what they mean here — the same rule the metrics consumer
# applies to run manifests via _SUPPORTED_MANIFEST_SCHEMA.
_SUPPORTED_USAGE_SNAPSHOT_SCHEMA = 1
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


def _read_json_path(path: str) -> Optional[dict]:
    """Read a JSON object at an authoritative path without raising."""
    try:
        with open(path) as source:
            value = json.load(source)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def read_json_file(output_dir: str, name: str) -> Optional[dict]:
    """Read one named run-level JSON object without letting failures escape."""
    return _read_json_path(os.path.join(output_dir, name))


def safe_dispatch_string(value: Any) -> Optional[str]:
    """Return a dispatch scalar only when it is a string."""
    return value if isinstance(value, str) else None


def safe_nonnegative_int(value: Any) -> Optional[int]:
    """One non-negative whole number, or None when it was not measured.

    Shared by every manifest field whose absent-measurement value is None
    rather than 0 — agent totals and millisecond spans alike. This was two
    byte-identical private helpers in this same module, one of them named
    for a count while returning a duration.

    None must survive as itself: a stalled synthesis agent has no
    duration, and substituting 0 would publish "finished instantly" for a
    phase that never finished, exactly as a zeroed agent total would
    publish "measured none" for a run that measured nothing.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


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


def _load_review_claim_accounting(
    output_dir: str, agent: str
) -> Optional[Dict[str, int]]:
    """Derive one finalized review's claim and unclaimed counts."""
    reviewer = derive_reviewer_name(agent)
    paths = review_paths(output_dir, reviewer)
    try:
        review = load_review_document(paths.final, reviewer)
    except ValueError:
        review = None
    accounting_input = _read_json_path(paths.accounting_input)
    if (
        review is None
        or accounting_input is None
        or "reviewed_file_claims" not in review
    ):
        return None
    try:
        accounting = derive_review_accounting(
            accounting_input, review["reviewed_file_claims"]
        )
    except (ReviewAccountingError, TypeError):
        return None
    if accounting.agent_name != agent:
        return None
    return {
        "reviewed_file_claim_count": len(accounting.reviewed_file_claims),
        "unclaimed_review_file_count": len(accounting.unclaimed_review_files),
    }


def _load_review_claimable_file_count(
    output_dir: str, agent: str
) -> Optional[int]:
    """Read the authoritative review-claimable file count."""
    reviewer = derive_reviewer_name(agent)
    data = _read_json_path(review_paths(output_dir, reviewer).accounting_input)
    if data is None:
        return None
    try:
        accounting = derive_review_accounting(data, [])
    except ReviewAccountingError:
        return None
    if accounting.agent_name != agent:
        return None
    return len(accounting.review_claimable_files)


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

        assigned_files_by_agent = {
            name: sorted(paths)
            for name, paths in sorted(by_agent_sets.items())
        }
        assigned_set = reviewable_set.intersection(
            path for paths in by_agent_sets.values() for path in paths
        )

        # Derived positive-claim/gap populations for NOT DIFFED files,
        # read straight off durable per-reviewer accounting inputs — never
        # derived from the events already folded into by_agent above,
        # which only carry generated SCOPE (assigned files), not the
        # reviewed-file accounting. Both dicts default to {} (measured,
        # zero reviewers), never omitted, once this builder runs at all;
        # only a run whose manifest predates this feature lacks the keys
        # entirely (see `_load_review_claim_accounting`'s contract).
        review_claim_accounting_by_agent: Dict[str, Dict[str, int]] = {}
        review_claimable_file_count_by_agent: Dict[str, int] = {}
        for name in sorted(final_agents):
            if not is_dispatched(final_agents[name].get("status")):
                continue
            accounting = _load_review_claim_accounting(output_dir, name)
            if accounting is not None:
                review_claim_accounting_by_agent[name] = accounting
            claimable_count = _load_review_claimable_file_count(output_dir, name)
            if claimable_count is not None:
                review_claimable_file_count_by_agent[name] = claimable_count

        return {
            CHANGED_FILES: changed,
            REVIEWABLE_FILES: reviewable,
            ASSIGNED_FILES_BY_AGENT: assigned_files_by_agent,
            ASSIGNED_FILES: sorted(assigned_set),
            FILE_EXCLUSIONS: [
                {"path": path, "reason": "noise_filtered"}
                for path in sorted(changed_set - reviewable_set)
            ],
            # DIVERGENCE NOTE — this is NOT the same measurement as
            # reconciliation_context.py's `review_accounting.unscoped_files`,
            # and the two legitimately disagree (a field run read 2 here
            # and 5 there). Both answer "which changed files did no agent's
            # scope contain", from different evidence over different
            # populations:
            #   * here — population `reviewable_files` (`changed_files` MINUS
            #     noise-filtered), evidence the dispatch-time `agent_start`
            #     SCOPE events of agents whose final status is dispatched.
            #     It must exactly partition `reviewable_files` with
            #     `assigned_files` (`sanitize.py` enforces the partition),
            #     so noise-filtered files can never appear here — they are
            #     reported under `file_exclusions` instead.
            #   * there — population the full `changed_files` list,
            #     evidence the runtime `*-scope-summary*.json` sidecars an
            #     agent writes when it actually runs. Noise-filtered and
            #     domain-unmatched files DO appear there, and an agent that
            #     was dispatched but died before writing a sidecar leaves
            #     its files unscoped there while they stay assigned here.
            # Keep both. This one is the plan-vs-changed accounting the
            # metrics partition depends on; that one is the did-anyone-
            # actually-see-it accounting the review report must confess.
            UNASSIGNED_REVIEWABLE_FILES: sorted(
                reviewable_set - assigned_set
            ),
            "review_claim_accounting_by_agent": review_claim_accounting_by_agent,
            "review_claimable_file_count_by_agent": (
                review_claimable_file_count_by_agent
            ),
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
    """Project request state, precheck refusal, and the canonical report."""
    config = read_json_file(output_dir, "run-config.json") or {}
    requested = config.get("refresh_dependencies") is True
    report = load_dependency_refresh_report(output_dir)
    state = read_json_file(output_dir, "pipeline-state.json") or {}
    precheck = state.get("dependency_refresh_precheck")
    if not requested and report is None and not isinstance(precheck, dict):
        return None
    result = {"requested": requested, "reported": report is not None}

    if isinstance(precheck, dict):
        precheck_dirty = precheck.get("tracked_files_dirty")
        if precheck_dirty is not False:
            dirty_files = precheck.get("dirty_files")
            result["precheck"] = {
                "tracked_files_dirty": (
                    precheck_dirty
                    if isinstance(precheck_dirty, bool) else None
                ),
                "dirty_files": [
                    path[:_MAX_DIRTY_FILE_CHARS]
                    for path in (
                        dirty_files[:_MAX_DIRTY_FILES]
                        if isinstance(dirty_files, list) else []
                    )
                    if isinstance(path, str)
                ],
            }

    if report is not None:
        result.update({
            "status": report["status"],
            "commands": [dict(command) for command in report["commands"]],
            "tracked_files_dirty": report["tracked_files_dirty"],
            "dirty_files": list(report["dirty_files"]),
        })

    return result


def _validated_derived_markdown_outcome(outcome: Any) -> Optional[dict]:
    """Validate one written/expected/status derived-Markdown outcome.

    Shared by `build_reviewer_markdown_manifest` (step 8's per-reviewer
    `<reviewer>-review.md`) and `build_findings_markdown_manifest` (steps
    9 and 11's `review-findings.md`) — both record the exact same
    ran/written/expected/status shape in pipeline state
    (`_record_findings_markdown` in orchestration.py mirrors the shape
    the reviewer-Markdown seam already used), so one validator covers
    both families instead of restating the same checks twice.
    """
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
        or status not in _DERIVED_MARKDOWN_STATUSES
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


def build_reviewer_markdown_manifest(output_dir: str) -> Optional[dict]:
    """Project the script-owned reviewer-Markdown outcome into the manifest."""
    state = read_json_file(output_dir, "pipeline-state.json")
    outcome = state.get("reviewer_markdown") if state is not None else None
    return _validated_derived_markdown_outcome(outcome)


def build_findings_markdown_manifest(output_dir: str) -> Optional[dict]:
    """Project the script-owned findings-Markdown outcome into the manifest.

    `reviewer_markdown`'s sibling: `state["findings_markdown"]` records
    steps 9 and 11's render of `review-findings.md`
    (`_record_findings_markdown` in orchestration.py), in the same shape
    this shares a validator with.
    """
    state = read_json_file(output_dir, "pipeline-state.json")
    outcome = state.get("findings_markdown") if state is not None else None
    return _validated_derived_markdown_outcome(outcome)


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


def build_synthesis_agents_manifest(output_dir: str) -> Optional[dict]:
    """Project the reconciliator/critic lifecycle into the manifest.

    A DISTINCT family from the reviewer lifecycle in `manifest["agents"]`,
    deliberately: those two agents are never in `dispatch-plan.json`, never
    run `agent/bootstrap.py`, and never write a `<agent>-review.json`, so
    folding them into the reviewer projection would corrupt every count
    downstream of it (the 19/19 completion ratio, the incomplete multiset,
    the per-agent dispatch comparison). They are measured beside it.

    None means the run never measured synthesis agents — the artifact is
    absent, unreadable, or announces a schema this builder cannot vouch
    for, as on any run predating this feature. That is a different fact
    from a measured run with an empty `agents` list (finalize looked and
    found no dispatch markers), and a consumer must never be able to read
    the first as a zero-duration phase.

    Every field falls back to the artifact's own absent-measurement value.
    `stalled` in particular is true only when the artifact says exactly
    True: a stall is an accusation against the run, and an unreadable flag
    does not license one.
    """
    data = read_json_file(output_dir, _SYNTHESIS_LIFECYCLE_FILENAME)
    if data is None:
        return None
    schema = data.get("schema")
    if (
        isinstance(schema, bool)
        or schema != _SUPPORTED_SYNTHESIS_LIFECYCLE_SCHEMA
    ):
        return None

    raw_agents = data.get("agents")
    rows: List[dict] = []
    if isinstance(raw_agents, list):
        for row in raw_agents:
            if not isinstance(row, dict) or not isinstance(
                row.get("agent"), str
            ):
                continue
            rows.append({
                "agent": row["agent"],
                # What the agent concluded. It is what makes the duration
                # beside it interpretable — a critic row reading "SKIPPED"
                # measures dispatch to orchestrator-gave-up, an upper
                # bound on a critique that may never have started, so the
                # cohort counts those apart instead of averaging them into
                # a critique duration.
                "verdict": safe_dispatch_string(row.get("verdict")),
                "started_at": safe_dispatch_string(row.get("started_at")),
                # The completion artifact's mtime — the one clock.
                "completed_at": safe_dispatch_string(row.get("completed_at")),
                "duration_ms": safe_nonnegative_int(row.get("duration_ms")),
                "stalled": row.get("stalled") is True,
            })

    # The projection vouches for the row shape it just built, against the
    # producer's single declaration of it. Three modules write this shape;
    # a key added to the producer alone would otherwise vanish here with
    # every test still green.
    assert all(set(row) == set(_SYNTHESIS_ROW_KEYS) for row in rows), (
        "synthesis row projection drifted from synthesis_lifecycle.ROW_KEYS"
    )

    return {
        "finalized": data.get("finalized") is True,
        "agents": rows,
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

    `window.closed` rides along because "partial" alone is ambiguous on a
    durable surface: an orchestrator half can be partial because the
    capture substituted its own window bound (the run was still open) or
    because the transcript evidence itself was damaged. The 2026-08-19
    field run is that ambiguity made concrete — a CLOSED window whose
    orchestrator half is still partial, from an unresolved tool call.
    Without the flag a reader cannot tell those two runs apart.
    """
    data = read_json_file(output_dir, "usage-snapshot.json")
    if data is None:
        return None
    schema = data.get("schema")
    if isinstance(schema, bool) or schema != _SUPPORTED_USAGE_SNAPSHOT_SCHEMA:
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
        # Keys need no type guard: this dict came out of a JSON object, and
        # JSON object keys are always strings.
        for model, usage in by_model_raw.items():
            safe = _safe_usage_map(usage)
            if safe is not None:
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
    window = data.get("window")
    window = window if isinstance(window, dict) else {}

    def bound(name: str) -> Optional[str]:
        value = window.get(name)
        return value if isinstance(value, str) else None

    return {
        "captured_at": captured_at if isinstance(captured_at, str) else None,
        "window": {
            "started_at": bound("started_at"),
            "ended_at": bound("ended_at"),
            # Anything but an explicit True reads as a substituted bound.
            # "closed" is the stronger claim — it says the run's own
            # manifest recorded an end — so an unreadable flag must fall to
            # the weaker one, never license the stronger.
            "closed": window.get("closed") is True,
        },
        "availability": {
            "subagents": state("subagents"),
            "orchestrator": state("orchestrator"),
        },
        "agents_measured": {
            "measured": safe_nonnegative_int(counts.get("measured")),
            "expected": safe_nonnegative_int(counts.get("expected")),
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

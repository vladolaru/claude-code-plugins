"""Canonical dispatch-plan row vocabulary: statuses, signals, field keys and
execution modes, for producers and consumers.

``load_dispatch_plan(path)`` is the one reader of a dispatch plan: it
requires JSON, requires an object, and validates the plan's agents, so
every consumer sees the same plan shape and a plan-shape change stays one
edit. ``validate_dispatch_plan_agents()`` validates agent names and
statuses only. ``manifest_sections.safe_dispatch_signal()`` projects a
missing or invalid signal as ``None``, so no consumer ever derives signal
identity from the prose ``reason`` beside it.

The plan may also carry one plan-level record beside ``agents``:
``dispatch_waves`` (``DISPATCH_WAVES_KEY``), stamped by step 6 orchestration
from ``build_dispatch_waves()``. It names the reviewer cap, where the cap came
from, which dispatched reviewers launch in wave 1 and which are queued until a
slot frees. ``load_dispatch_plan()`` validates it when present; an absent
record is a legacy plan (no waves, nothing queued). ``queue_state()`` is the
one definition of which NOT_DISPATCHED reviewers are queued, shared by
``agents_status.py`` and the manifest builders. Rows are never touched by the
record, so row validation and the step-5 baseline are unaffected. The
watchdog adds ``released`` and ``last_release`` to the record when it tells
the orchestrator to launch queued reviewers (``record_release()``), so a
launched reviewer holds its slot until it starts. A reviewer that never
starts is *abandoned* (``queue_state()``) after ``MAX_RELEASES`` *counted*
releases, those made while the host held nothing of this review's
(``record_release()``), so waiting on it always ends and a refusal by a
host full of this review's own reviewers never abandons a healthy one
(host threads the record cannot see are a known limitation; see
docs/review-pipeline.md).
"""

import os
import re
import sys
from datetime import datetime, timezone

try:
    from .atomic_io import read_json_object
    from .pipeline_contract import CAP_SOURCES
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.atomic_io import read_json_object
    from review.pipeline_contract import CAP_SOURCES

# Producer agent-name grammar: lowercase ASCII kebab-case. Agent names become
# machine identifiers downstream (telemetry manifests, output filenames, shell
# command tokens, transcript correlation), so every producer and consumer must
# validate against this one pattern — always via .fullmatch().
AGENT_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*")

DISPATCH = "DISPATCH"
DISPATCH_OVERRIDE = "DISPATCH_OVERRIDE"
SKIPPED = "SKIPPED"
SKIPPED_OVERRIDE = "SKIPPED_OVERRIDE"
SKIPPED_QUICK_MODE = "SKIPPED_QUICK_MODE"
SKIPPED_TRIAGE = "SKIPPED_TRIAGE"

# The orchestrator's reason beside an override status, written by
# dispatch_adjust.py and read by orchestration and the manifest builders.
OVERRIDE_REASON_KEY = "override_reason"
# The status the planner gave an agent before the orchestrator overrode it;
# stamped once beside the override reason so the step-6 briefing and the
# manifest can name the transition without re-reading the initial plan.
PLANNER_STATUS_KEY = "planner_status"
# The changed files an override skip left with no reviewer, measured by
# dispatch_adjust.py on every call and read by orchestration and the step-6
# briefing; the lead is the one spelling both renderers use.
ORPHANED_FILES_KEY = "orphaned_files"
ORPHANED_FILES_LEAD = "leaves reviewed by no one: "

# How a repo reviewer runs, as its declaration states and its plan row
# carries under `execution`. Only inline execution exists: every step that
# could run an isolated reviewer refuses it (the planner skips it,
# dispatch_adjust refuses an override, bootstrap exits with an error)
# rather than widen the request into inline execution.
EXECUTION_INLINE = "inline"
EXECUTION_ISOLATED = "isolated"
EXECUTIONS = frozenset({EXECUTION_INLINE, EXECUTION_ISOLATED})

DISPATCHED_STATUSES = frozenset({DISPATCH, DISPATCH_OVERRIDE})
SKIPPED_STATUSES = frozenset({
    SKIPPED,
    SKIPPED_OVERRIDE,
    SKIPPED_QUICK_MODE,
    SKIPPED_TRIAGE,
})
SUPPORTED_DISPATCH_STATUSES = DISPATCHED_STATUSES | SKIPPED_STATUSES

# Why the planner decided, as one enumeration emitted from the code path
# that decided. Telemetry discloses and counts these; the prose `reason`
# beside each stays undisclosed. Nothing derives a signal from a reason.
SIGNAL_NO_DOMAIN_FILES = "no_domain_files"
SIGNAL_ALWAYS = "always"
SIGNAL_TEST_ONLY = "test_only"
SIGNAL_MIN_ADDED_LINES = "min_added_lines"
SIGNAL_SOURCE_GATE = "source_gate"
SIGNAL_KEYWORD = "keyword"
SIGNAL_REPOSITORY_KEYWORD = "repository_keyword"
SIGNAL_CHECK = "check"
SIGNAL_DIFF_UNAVAILABLE = "diff_unavailable"
SIGNAL_EVIDENCE_GATE = "evidence_gate"
SIGNAL_DEFAULT = "default"
SIGNAL_UNTRIAGED = "untriaged"
SIGNAL_QUICK_MODE = "quick_mode"
SIGNAL_REPO_REVIEWER = "repo_reviewer"
SIGNAL_OVERRIDE = "override"
DISPATCH_SIGNALS = frozenset({
    SIGNAL_NO_DOMAIN_FILES, SIGNAL_ALWAYS, SIGNAL_TEST_ONLY,
    SIGNAL_MIN_ADDED_LINES, SIGNAL_SOURCE_GATE, SIGNAL_KEYWORD,
    SIGNAL_REPOSITORY_KEYWORD, SIGNAL_CHECK, SIGNAL_DIFF_UNAVAILABLE,
    SIGNAL_EVIDENCE_GATE, SIGNAL_DEFAULT, SIGNAL_UNTRIAGED,
    SIGNAL_QUICK_MODE, SIGNAL_REPO_REVIEWER, SIGNAL_OVERRIDE,
})
# Dispatches resting on no positive evidence; quick mode may skip these.
LOW_SIGNAL_DISPATCH_SIGNALS = frozenset({
    SIGNAL_ALWAYS, SIGNAL_DEFAULT, SIGNAL_UNTRIAGED,
})


def validate_dispatch_plan_agents(agents: object) -> list[dict]:
    """Validate and return dispatch-plan agent entries."""
    if not isinstance(agents, list):
        raise ValueError(
            f"Dispatch plan agents must be a list, got {agents!r}"
        )

    validated_agents = []
    for index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise ValueError(
                f"Dispatch plan agent at index {index} must be a dict, "
                f"got {agent!r}"
            )

        name = agent.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"Dispatch plan agent at index {index} must have a nonempty "
                f"string name, got {name!r}"
            )

        status = agent.get("status")
        if (
            not isinstance(status, str)
            or status not in SUPPORTED_DISPATCH_STATUSES
        ):
            raise ValueError(
                f"Unsupported dispatch status for agent {name!r}: {status!r}"
            )

        validated_agents.append(agent)

    return validated_agents


# The plan-level wave record step 6 stamps (see the module docstring). Its
# own `schema` versions the record, since the plan itself carries none.
DISPATCH_WAVES_KEY = "dispatch_waves"
DISPATCH_WAVES_SCHEMA = 1
# How long a wave-1 reviewer may stay NOT_DISPATCHED after `stamped_at`
# (or a released reviewer after its release) before it counts as queued:
# long enough for a launched reviewer to write its started marker, so only
# a rejected launch (cap or lock file) crosses it. 180s, not 90s: a healthy
# wave of 20 reviewers bootstrapping at once was measured past 90s.
DISPATCH_WAVES_GRACE_SECONDS = 180
# Optional keys the watchdog adds when it exits SLOT_FREE (agents_status):
# `released` maps each name it told the orchestrator to launch to
# `{"at": latest release, "count": releases, "counted": releases made while
# the host held nothing of this review's (`record_release()`)}`, and `last_release` records
# that time and the terminal reviewer count then, so a later SLOT_FREE
# needs progress since.
RELEASED_KEY = "released"
LAST_RELEASE_KEY = "last_release"
# Counted releases after which a reviewer that still has not started is
# abandoned once its latest grace window runs out. Only a release made
# while nothing of ours ran, waited to start or ran and ended since the
# previous release counts: a refusal then is not host contention (Codex's
# thread limit, our cap), so the launch was lost, and re-offering it
# forever kept step 7 from reaching exit 0.
MAX_RELEASES = 2


def build_dispatch_waves(plan_agents, cap, cap_source, stamped_at) -> dict:
    """The ``dispatch_waves`` record for ``plan_agents``.

    Dispatched rows (``DISPATCHED_STATUSES``) keep plan order, the planner's
    ordering; the first ``cap`` names are ``wave_1`` and the rest ``queued``.
    ``cap=None`` (unbounded) puts every dispatched name in ``wave_1``.
    ``stamped_at`` is an ISO-8601 UTC string the caller supplies, so this
    stays pure. The result passes ``validate_dispatch_waves()``.
    """
    dispatched = [
        agent["name"] for agent in plan_agents
        if agent.get("status") in DISPATCHED_STATUSES
    ]
    split = len(dispatched) if cap is None else cap
    return {
        "schema": DISPATCH_WAVES_SCHEMA,
        "cap": cap,
        "cap_source": cap_source,
        "wave_1": dispatched[:split],
        "queued": dispatched[split:],
        "stamped_at": stamped_at,
        "grace_seconds": DISPATCH_WAVES_GRACE_SECONDS,
    }


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_stamp(value) -> datetime:
    """``stamped_at`` as an aware datetime; naive stamps are read as UTC."""
    stamp = datetime.fromisoformat(value)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def validate_dispatch_waves(record: object) -> dict:
    """Validate and return a ``dispatch_waves`` record.

    Raises ValueError naming the first bad field: not an object, ``schema``
    other than ``DISPATCH_WAVES_SCHEMA`` (a bool is rejected), ``cap`` not a
    positive int or null, ``cap_source`` outside
    ``pipeline_contract.CAP_SOURCES``, ``wave_1``/``queued`` not lists of
    ``AGENT_NAME_RE`` names or overlapping, ``stamped_at`` not ISO-8601,
    ``grace_seconds`` not a non-negative int, ``released`` (optional) not
    an object mapping names from ``wave_1``/``queued`` to release entries
    (``{"at": ISO-8601, "count": positive int, "counted": int from 0 to
    count}``),
    ``last_release`` (optional) not ``{"at": ISO-8601, "terminal_count":
    non-negative int}``.
    """
    if not isinstance(record, dict):
        raise ValueError(f"{DISPATCH_WAVES_KEY} must be an object, got {record!r}")
    schema = record.get("schema")
    if not _is_int(schema) or schema != DISPATCH_WAVES_SCHEMA:
        raise ValueError(
            f"{DISPATCH_WAVES_KEY}.schema must be {DISPATCH_WAVES_SCHEMA}, "
            f"got {schema!r}"
        )
    cap = record.get("cap")
    if cap is not None and (not _is_int(cap) or cap <= 0):
        raise ValueError(
            f"{DISPATCH_WAVES_KEY}.cap must be a positive int or null, got {cap!r}"
        )
    if record.get("cap_source") not in CAP_SOURCES:
        raise ValueError(
            f"{DISPATCH_WAVES_KEY}.cap_source is unknown: "
            f"{record.get('cap_source')!r}"
        )
    for key in ("wave_1", "queued"):
        names = record.get(key)
        if not isinstance(names, list) or not all(
            isinstance(name, str) and AGENT_NAME_RE.fullmatch(name)
            for name in names
        ):
            raise ValueError(
                f"{DISPATCH_WAVES_KEY}.{key} must be a list of agent names, "
                f"got {names!r}"
            )
    overlap = set(record["wave_1"]) & set(record["queued"])
    if overlap:
        raise ValueError(
            f"{DISPATCH_WAVES_KEY} lists {sorted(overlap)!r} in both wave_1 "
            "and queued"
        )
    stamped_at = record.get("stamped_at")
    try:
        _parse_stamp(stamped_at)
    except (TypeError, ValueError):
        raise ValueError(
            f"{DISPATCH_WAVES_KEY}.stamped_at must be an ISO-8601 timestamp, "
            f"got {stamped_at!r}"
        ) from None
    grace = record.get("grace_seconds")
    if not _is_int(grace) or grace < 0:
        raise ValueError(
            f"{DISPATCH_WAVES_KEY}.grace_seconds must be a non-negative int, "
            f"got {grace!r}"
        )
    if RELEASED_KEY in record:
        _validate_released(record)
    if LAST_RELEASE_KEY in record:
        _validate_last_release(record[LAST_RELEASE_KEY])
    return record


def _is_stamp(value) -> bool:
    try:
        _parse_stamp(value)
    except (TypeError, ValueError):
        return False
    return True


def _is_release_entry(value) -> bool:
    if not isinstance(value, dict) or set(value) != {"at", "count", "counted"}:
        return False
    return (
        _is_stamp(value["at"])
        and _is_int(value["count"])
        and value["count"] > 0
        and _is_int(value["counted"])
        and 0 <= value["counted"] <= value["count"]
    )


def _validate_released(record) -> None:
    released = record[RELEASED_KEY]
    planned = set(record["wave_1"]) | set(record["queued"])
    if not isinstance(released, dict) or not all(
        name in planned and _is_release_entry(entry)
        for name, entry in released.items()
    ):
        raise ValueError(
            f"{DISPATCH_WAVES_KEY}.{RELEASED_KEY} must map wave_1/queued "
            'names to {"at", "count", "counted"} release entries, '
            f"got {released!r}"
        )


def _validate_last_release(last_release) -> None:
    if (
        not isinstance(last_release, dict)
        or not _is_stamp(last_release.get("at"))
        or not _is_int(last_release.get("terminal_count"))
        or last_release["terminal_count"] < 0
    ):
        raise ValueError(
            f"{DISPATCH_WAVES_KEY}.{LAST_RELEASE_KEY} must be "
            '{"at": ISO-8601, "terminal_count": non-negative int}, '
            f"got {last_release!r}"
        )


def queue_state(record, not_dispatched_names, now) -> dict:
    """Which NOT_DISPATCHED reviewers are queued, pending or abandoned.

    ``record`` is a validated ``dispatch_waves`` record or None (legacy plan:
    every list empty). ``not_dispatched_names`` are the dispatched reviewers
    with no started marker; ``now`` is an aware datetime.
    Returns ``{"queued": [...], "pending": [...], "abandoned": [...],
    "attempts": {name: launches}}``, ``wave_1`` names first, then ``queued``
    names, plan order within each. A name's launches are its releases, plus
    one for the step-6 launch of a ``wave_1`` name.

    - ``pending``: launched too recently to have a started marker, so it
      occupies a slot without being queued: a ``wave_1`` name never
      released while ``now - stamped_at <= grace_seconds``, or a released
      name while ``now - released[name].at <= grace_seconds``. Nothing is
      abandoned inside this window;
    - ``abandoned``: past that window with ``MAX_RELEASES`` counted
      releases (``record_release()``), so it no longer holds a slot or
      blocks completion;
    - ``queued``: every other listed name (a planned queue entry not yet
      launched, or a launch the host refused or lost).
    """
    state = {"queued": [], "pending": [], "abandoned": [], "attempts": {}}
    if record is None:
        return state
    waiting = set(not_dispatched_names)
    grace = record["grace_seconds"]
    released = record.get(RELEASED_KEY) or {}
    wave_1 = set(record["wave_1"])

    for name in record["wave_1"] + record["queued"]:
        if name not in waiting:
            continue
        entry = released.get(name)
        state["attempts"][name] = (
            (entry["count"] if entry else 0) + (1 if name in wave_1 else 0)
        )
        launched_at = entry["at"] if entry else (
            record["stamped_at"] if name in wave_1 else None
        )
        if (
            launched_at is not None
            and (now - _parse_stamp(launched_at)).total_seconds() <= grace
        ):
            state["pending"].append(name)
        elif entry and entry["counted"] >= MAX_RELEASES:
            state["abandoned"].append(name)
        else:
            state["queued"].append(name)
    return state


def record_release(record, names, terminal_count, released_at, occupied) -> dict:
    """``record`` with ``names`` stamped as released at ``released_at``.

    Pure: returns a new record. Each name's ``released`` entry gets ``at``
    = ``released_at`` (an ISO-8601 string) and one more ``count``. It also
    gets one more ``counted`` when the host held nothing of this review's:
    ``occupied`` (RUNNING plus pending reviewers when the release was
    decided) is 0 and ``terminal_count`` has not grown since the previous
    release (since step 6 for the first one), so no reviewer started and
    ended in between either. A launch that still never starts was lost,
    not refused by a full host. A reviewer the host refused beside
    co-released names it accepted can gain one ``counted`` that way, never
    two: those names then run or end before its next release.
    ``last_release`` becomes ``{"at": released_at, "terminal_count":
    terminal_count}``: the watchdog's next SLOT_FREE then needs the
    terminal count to have grown, or nothing running or pending (see
    agents_status).
    """
    previous_terminal = (record.get(LAST_RELEASE_KEY) or {}).get("terminal_count", 0)
    counts = occupied == 0 and terminal_count == previous_terminal
    released = {
        name: dict(entry)
        for name, entry in (record.get(RELEASED_KEY) or {}).items()
    }
    for name in names:
        previous = released.get(name) or {"count": 0, "counted": 0}
        released[name] = {
            "at": released_at,
            "count": previous["count"] + 1,
            "counted": previous["counted"] + (1 if counts else 0),
        }
    return {
        **record,
        RELEASED_KEY: released,
        LAST_RELEASE_KEY: {"at": released_at, "terminal_count": terminal_count},
    }


def load_dispatch_plan(path) -> dict:
    """The dispatch plan at ``path`` with its agents validated.

    Raises FileNotFoundError when there is no plan and ValueError naming the
    file when it is not JSON, not an object, its agents are malformed, or
    its ``dispatch_waves`` record (when present) is malformed — every reader
    of a plan goes through here, so a plan-shape change is one edit.
    """
    path = os.fspath(path)
    plan = read_json_object(path, os.path.basename(path))
    validate_dispatch_plan_agents(plan.get("agents"))
    if DISPATCH_WAVES_KEY in plan:
        try:
            validate_dispatch_waves(plan[DISPATCH_WAVES_KEY])
        except ValueError as exc:
            raise ValueError(f"{os.path.basename(path)}: {exc}") from None
    return plan


__all__ = [
    "AGENT_NAME_RE",
    "DISPATCH",
    "DISPATCH_OVERRIDE",
    "SKIPPED",
    "SKIPPED_OVERRIDE",
    "SKIPPED_QUICK_MODE",
    "SKIPPED_TRIAGE",
    "OVERRIDE_REASON_KEY",
    "PLANNER_STATUS_KEY",
    "DISPATCHED_STATUSES",
    "SKIPPED_STATUSES",
    "SUPPORTED_DISPATCH_STATUSES",
    "EXECUTION_INLINE",
    "EXECUTION_ISOLATED",
    "EXECUTIONS",
    "SIGNAL_NO_DOMAIN_FILES",
    "SIGNAL_ALWAYS",
    "SIGNAL_TEST_ONLY",
    "SIGNAL_MIN_ADDED_LINES",
    "SIGNAL_SOURCE_GATE",
    "SIGNAL_KEYWORD",
    "SIGNAL_REPOSITORY_KEYWORD",
    "SIGNAL_CHECK",
    "SIGNAL_DIFF_UNAVAILABLE",
    "SIGNAL_EVIDENCE_GATE",
    "SIGNAL_DEFAULT",
    "SIGNAL_UNTRIAGED",
    "SIGNAL_QUICK_MODE",
    "SIGNAL_REPO_REVIEWER",
    "SIGNAL_OVERRIDE",
    "DISPATCH_SIGNALS",
    "LOW_SIGNAL_DISPATCH_SIGNALS",
    "DISPATCH_WAVES_KEY",
    "DISPATCH_WAVES_SCHEMA",
    "DISPATCH_WAVES_GRACE_SECONDS",
    "LAST_RELEASE_KEY",
    "MAX_RELEASES",
    "RELEASED_KEY",
    "build_dispatch_waves",
    "load_dispatch_plan",
    "queue_state",
    "record_release",
    "validate_dispatch_plan_agents",
    "validate_dispatch_waves",
]

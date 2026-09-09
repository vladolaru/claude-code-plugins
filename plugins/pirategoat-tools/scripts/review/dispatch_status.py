"""Canonical dispatch-status and signal vocabulary, producers and consumers.

``load_dispatch_plan(path)`` is the one reader of a dispatch plan: it
requires JSON, requires an object, and validates the plan's agents, so
every consumer sees the same plan shape and a plan-shape change stays one
edit. ``validate_dispatch_plan_agents()`` validates agent names and
statuses only. ``manifest_sections.safe_dispatch_signal()`` projects a
missing or invalid signal as ``None``, so no consumer ever derives signal
identity from the prose ``reason`` beside it.
"""

import json
import os
import re

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


def load_dispatch_plan(path) -> dict:
    """The dispatch plan at ``path`` with its agents validated.

    Raises FileNotFoundError when there is no plan and ValueError naming the
    file when it is not JSON, not an object, or its agents are malformed —
    every reader of a plan goes through here, so a plan-shape change is one
    edit.
    """
    path = os.fspath(path)
    name = os.path.basename(path)
    with open(path, "r", encoding="utf-8") as handle:
        try:
            plan = json.load(handle)
        except json.JSONDecodeError as err:
            raise ValueError(f"{name} is not valid JSON: {err}") from None
    if not isinstance(plan, dict):
        raise ValueError(f"{name} must be a JSON object, got {type(plan).__name__}")
    validate_dispatch_plan_agents(plan.get("agents"))
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
    "load_dispatch_plan",
    "validate_dispatch_plan_agents",
]

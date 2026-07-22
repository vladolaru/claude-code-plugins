"""Canonical dispatch-plan status vocabulary shared by producers and consumers."""

DISPATCH = "DISPATCH"
DISPATCH_OVERRIDE = "DISPATCH_OVERRIDE"
SKIPPED = "SKIPPED"
SKIPPED_OVERRIDE = "SKIPPED_OVERRIDE"
SKIPPED_QUICK_MODE = "SKIPPED_QUICK_MODE"
SKIPPED_TRIAGE = "SKIPPED_TRIAGE"

DISPATCHED_STATUSES = frozenset({DISPATCH, DISPATCH_OVERRIDE})
SKIPPED_STATUSES = frozenset({
    SKIPPED,
    SKIPPED_OVERRIDE,
    SKIPPED_QUICK_MODE,
    SKIPPED_TRIAGE,
})
SUPPORTED_DISPATCH_STATUSES = DISPATCHED_STATUSES | SKIPPED_STATUSES


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


__all__ = [
    "DISPATCH",
    "DISPATCH_OVERRIDE",
    "SKIPPED",
    "SKIPPED_OVERRIDE",
    "SKIPPED_QUICK_MODE",
    "SKIPPED_TRIAGE",
    "DISPATCHED_STATUSES",
    "SKIPPED_STATUSES",
    "SUPPORTED_DISPATCH_STATUSES",
    "validate_dispatch_plan_agents",
]

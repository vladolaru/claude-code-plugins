#!/usr/bin/env python3
"""Canonical reviewer-name derivation.

Leaf module: stdlib only, no imports from anywhere else in `review/` —
deliberately, so any script can import this without risking an import
cycle. `agent/bootstrap.py` used to define `derive_reviewer_name()`
itself and load `telemetry.py` (which imports `manifest_sections.py`) as
a top-level side effect; a second script importing `derive_reviewer_name`
from `bootstrap` re-entered `bootstrap` mid-initialization and silently
broke telemetry loading (`ReviewTelemetry` became `None`). Every consumer
of the naming rule — `bootstrap.py`, `manifest_sections.py`,
`agents_status.py`, `reconciliation_context.py` — now imports the one
implementation here instead of restating (or, in `bootstrap.py`'s case,
still owning) it.
"""


def derive_reviewer_name(agent_name: str) -> str:
    """Derive the reviewer output name from agent name.

    Removes a TRAILING '-reviewer' suffix for output file naming.
    e.g. 'security-reviewer' -> 'security', 'code-reviewer' -> 'code'

    A blanket `.replace()` would corrupt names carrying "reviewer"
    mid-string — adapter instances are "repo-<id>-reviewer" and <id> is
    repo-authored (e.g. "api-reviewer-v2" must strip only the trailing
    occurrence, not the embedded one).

    Per-agent artifacts in OUTPUT_DIR follow one of two naming conventions;
    pick the matching one when adding a new per-agent artifact:
    - Human/deliverable-facing artifacts use this short reviewer_name:
      '<reviewer_name>-review.json'.
    - Internal/orchestration-facing artifacts keyed on args.agent use the full
      agent_name: '<agent_name>.started', '<agent_name>-scoped-diff.patch'.
    """
    if agent_name.endswith("-reviewer"):
        return agent_name[: -len("-reviewer")]
    return agent_name

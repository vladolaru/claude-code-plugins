#!/usr/bin/env python3
"""Canonical reviewer-name derivation.

Sole implementation of `derive_reviewer_name()` — the trailing-`-reviewer`
stripping rule every per-agent artifact name is built from — and of its
inverse `agent_name_from_review_stem()`. Every consumer imports these
instead of restating the rule.

Leaf module: stdlib only, never imports from anywhere else in `review/`,
so any script can import the naming rule without re-entering a module that
is still initializing. `tests/review/agent/test_bootstrap_integration.py`
pins both derivations.
"""


def derive_reviewer_name(agent_name: str) -> str:
    """Derive the reviewer output name from agent name.

    Removes a TRAILING '-reviewer' suffix for reviewer-directory naming.
    e.g. 'security-reviewer' -> 'security', 'code-reviewer' -> 'code'

    A blanket `.replace()` would corrupt names carrying "reviewer"
    mid-string — adapter instances are "repo-<id>-reviewer" and <id> is
    repo-authored (e.g. "api-reviewer-v2" must strip only the trailing
    occurrence, not the embedded one).

    Every per-reviewer artifact uses this short identity as the parent
    directory: ``OUTPUT_DIR/reviewers/<reviewer_name>/``. Fixed filenames
    inside that directory do not encode identity a second time.
    """
    if agent_name.endswith("-reviewer"):
        return agent_name[: -len("-reviewer")]
    return agent_name


def agent_name_from_review_stem(stem: str) -> str:
    """Map a review-file stem back to the registry agent name.

    The reconciliation context keys reviews by ``<reviewer>-review``
    (``reconciliation_context._review_stem``) and the ledger copies those
    stems into its rosters. Telemetry events and dispatch plans use the
    registry name ``<reviewer>-reviewer``. The shared manifest carries one
    spelling — the registry name — so a cohort reader can join rosters,
    events and usage rows without knowing this history. Values that do not
    end in ``-review`` (already registry names, or unrelated) pass through.
    """
    if stem.endswith("-review"):
        return f"{stem[: -len('-review')]}-reviewer"
    return stem

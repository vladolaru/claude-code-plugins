#!/usr/bin/env python3
"""The reconciliator's builder for review-findings.json (the findings ledger).

The ledger is review content — findings, checks, assessment, observations,
recommendations, positives, id counters — plus reconciliation metrics. It has
no reviewer identity and no reviewed-file accounting: those belong to one
reviewer's draft/final lifecycle, which a synthesized cross-review artifact
does not have. This is the one deliberate subclass of ReviewOutputBuilder;
do not grow a hierarchy under it.
"""
from typing import Dict

try:
    from .agent.output import ReviewOutputBuilder
except ImportError:
    from review.agent.output import ReviewOutputBuilder

LEDGER_SCHEMA = 3
# The four judgments the reconciliator itself makes: every grouped concern is
# either verified, a false positive, or out of scope. The builder owns these.
RECONCILIATION_JUDGMENT_FIELDS = (
    "grouped_concern_count",
    "verified_concern_count",
    "false_positive_concern_count",
    "out_of_scope_concern_count",
)
# What the pipeline measured about the run that fed the reconciliator. The
# builder never authors these — the pipeline stitches them onto the ledger.
RECONCILIATION_PIPELINE_FIELDS = (
    "input_finding_count",
    "contributing_agent_count",
    "reviewing_agents",
    "not_applicable_agents",
    "dispatched_agents",
    "missing_agents",
)
RECONCILIATION_FIELDS = frozenset(
    RECONCILIATION_JUDGMENT_FIELDS + RECONCILIATION_PIPELINE_FIELDS
)
# The reconciliator is dispatched as `review-reconciliator` and constructs its
# builder as `reconciliator`; the inherited id allocation and duration lookup
# are keyed on the latter (see _MARKER_AGENT_BY_REVIEWER in agent/output.py).
LEDGER_ACTOR = "reconciliator"


def _no_lifecycle(*_args, **_kwargs):
    raise TypeError("the findings ledger has no reviewer lifecycle")


class FindingsLedgerBuilder(ReviewOutputBuilder):
    """Build the reconciled ledger's content and its four judgment counts."""

    def __init__(self, pr_id: str, output_dir: str):
        super().__init__(pr_id, LEDGER_ACTOR)
        # Bound for the two facts the run's directory answers — the plugin
        # version stamped in run-config.json and the dispatch marker the
        # duration is measured from — and for nothing else: the ledger is
        # written by write_findings, not by the draft lifecycle.
        self._output_dir = str(output_dir)
        self._reconciliation = None

    @classmethod
    def open(cls, *_args, **_kwargs):
        _no_lifecycle()

    save_draft = _no_lifecycle
    claim_files_reviewed = _no_lifecycle
    retract_reviewed_file_claims = _no_lifecycle
    mark_not_applicable = _no_lifecycle

    def set_reconciliation(
        self, *, grouped_concern_count: int, verified_concern_count: int,
        false_positive_concern_count: int, out_of_scope_concern_count: int,
    ) -> None:
        """Record the four judgment counts, which must partition the concerns."""
        counts = dict(zip(
            RECONCILIATION_JUDGMENT_FIELDS,
            (
                grouped_concern_count,
                verified_concern_count,
                false_positive_concern_count,
                out_of_scope_concern_count,
            ),
        ))
        for name, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if (
            verified_concern_count
            + false_positive_concern_count
            + out_of_scope_concern_count
        ) != grouped_concern_count:
            raise ValueError(
                "verified + false_positive + out_of_scope concern counts must "
                "equal grouped_concern_count"
            )
        self._reconciliation = counts

    def to_dict(self) -> Dict:
        """The review content, at the ledger's schema, plus the judgments."""
        if self._reconciliation is None:
            raise ValueError(
                "call set_reconciliation() before serializing the ledger"
            )
        data = super().to_dict()
        del data["reviewer"]
        data["schema"] = LEDGER_SCHEMA
        data["meta"]["reconciliation"] = dict(self._reconciliation)
        return data

#!/usr/bin/env python3
"""Register an orchestrator claim for the reconciliator to answer.

The step-8 dispatch prompt carries only the three inputs the reconciliator
needs. Everything the orchestrator would otherwise put in that prompt — a
disagreement it noticed between reviewers, two findings it believes are
one concern, a fact it wants weighed — goes into the reconciliation
context as a note, stated as a claim. findings_save.py then refuses a
ledger that does not answer every note with an outcome and evidence, so
a hint can no longer be adopted verbatim (run 6e6a) or lost.

One writer, under the output-directory lock, appending to the context
reconciliation_context.py wrote — which carries these claims across a
rebuild under that same lock. Ids are monotonic within the run; a call that
repeats --note registers each flag in order and prints one RECORDED NOTE line
per claim.
"""

import argparse
import os
import sys

try:
    from . import atomic_io
    from .findings_ledger import read_reconciliation_context
    from .review_document import normalize_bounded_text
    from .reconciliation_context import (
        RECONCILIATION_CONTEXT_SCHEMA,
        validate_orchestrator_notes,
    )
    from .run_paths import artifact_path
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review import atomic_io
    from review.findings_ledger import read_reconciliation_context
    from review.review_document import normalize_bounded_text
    from review.reconciliation_context import (
        RECONCILIATION_CONTEXT_SCHEMA,
        validate_orchestrator_notes,
    )
    from review.run_paths import artifact_path

CONTEXT_FILENAME = artifact_path("", "reconciliation_context").name


def add_notes(output_dir, texts):
    """Append the given notes, in order, under one lock; return them.

    All-or-nothing: every text is normalized before the context is read,
    so one unusable claim rejects the call and writes nothing.
    """
    cleaned = [normalize_bounded_text(text, "note") for text in texts]
    if not cleaned:
        raise ValueError("at least one --note is required")
    path = artifact_path(output_dir, "reconciliation_context")
    with atomic_io.output_dir_lock(str(output_dir)):
        context = read_reconciliation_context(output_dir)
        if context.get("schema") != RECONCILIATION_CONTEXT_SCHEMA:
            raise ValueError(
                f"{CONTEXT_FILENAME} schema is not {RECONCILIATION_CONTEXT_SCHEMA}"
            )
        notes = validate_orchestrator_notes(context.get("orchestrator_notes"))
        recorded = []
        for text in cleaned:
            note = {"id": f"n{len(notes) + 1}", "note": text}
            notes.append(note)
            recorded.append(note)
        context["orchestrator_notes"] = notes
        atomic_io.atomic_write_json(str(path), context)
    return recorded


def add_note(output_dir, text):
    """Append one note to the run's reconciliation context; return it."""
    return add_notes(output_dir, [text])[0]


def main():
    parser = argparse.ArgumentParser(
        description="Register orchestrator claims in the reconciliation context",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--note", action="append", required=True,
        help="One claim, stated as a claim; repeat the flag to register several in order",
    )
    args = parser.parse_args()
    try:
        recorded = add_notes(args.output_dir, args.note)
    except ValueError as err:
        print(f"REJECTED: {err}")
        sys.exit(1)
    for note in recorded:
        print(f"RECORDED NOTE: {note['id']}")


if __name__ == "__main__":
    main()

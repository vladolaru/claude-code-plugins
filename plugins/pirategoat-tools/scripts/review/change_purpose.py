#!/usr/bin/env python3
"""The change purpose's structure: the orchestrator's judgement, parsed.

The `change_purpose` artifact is written by the orchestrator at step 3 or
4 by judgement and read by every reviewer briefing (REVIEW FOCUS), the
reconciliation context and the review record. A free-form focus list
mixes claims with givens and inferences with sourced statements, so no
script can say which check settled which claim; this module reads the
three headings the step-3 handoff requires instead:

    ## Verify
    V1. claim — where: file:line — settled by: evidence — source: PR description
    ## Context
    C1. fact — source: commit 1a2b3c4
    ## Author's description (extracted)
    quoted prose

It interprets nothing: ids are explicit in the text, the source is the
text after the last `— source:`, and a trailing `(carried over)` marks an
item brought forward from an earlier review of the same branch. A purpose
without the headings is unstructured — a fact every consumer degrades on,
never a failure.

Its rules are recorded doctrine, and every one of them is reported as a
parse problem rather than repaired: a missing heading; an item that names
no source; an item `inferred from the diff` under Context; a duplicate id;
an id listed under the other tier's heading; a tier body that parses to no
item without reading `None.`; and more than eight Verify items.

Who may cite a Verify item is read here too:

* `ledger_citations()` is the one reader of the entries in a saved ledger
  that may cite a Verify item — every check under its source reviewers, and
  every confirmed orchestrator note carrying `verifies`, labelled
  `RECONCILIATOR_LABEL`.
* `checks_settling()` groups those entries by the item cited. It is the one
  function behind the reconciliation context's `verify_items` and the
  record's `## Verify items` table.
* `undeclared_citations()` names every citation of an id the purpose does
  not declare, which the record lists under that same table.

Stdlib only; a leaf of the review package's import graph.
`review_document.py` owns `VERIFY_ITEM_ID_RE`, the Verify-item id grammar a
check's `verifies` list must satisfy.
"""

import re

VERIFY_HEADING = "## Verify"
CONTEXT_HEADING = "## Context"
AUTHOR_HEADING = "## Author's description (extracted)"
REQUIRED_HEADINGS = (VERIFY_HEADING, CONTEXT_HEADING, AUTHOR_HEADING)

INFERRED_SOURCE = "inferred from the diff"
# Guidance for the orchestrator; only INFERRED_SOURCE is validated, because
# a script cannot know every legitimate provenance.
PROVENANCE = (
    "PR description", "commit <sha>", "linked issue <id>", "review thread",
    "changelog", "version constant", INFERRED_SOURCE,
)
CARRIED_OVER_MARKER = "(carried over)"
NONE_MARKER = "none"
MAX_VERIFY_ITEMS = 8

_HEADING_RE = re.compile(r"^##\s+")
_ITEM_RE = re.compile(r"^\s*(?:[-*]\s+)?([VC])([1-9][0-9]*)\.\s+(.*)$")
_SOURCE_SPLIT_RE = re.compile(r"\s+[—-]\s+source:\s*", re.IGNORECASE)


def _sections(text):
    """{heading line as written: [body lines]} for every `## ` heading."""
    sections = {}
    current = None
    for line in (text or "").splitlines():
        if _HEADING_RE.match(line):
            current = line.strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return sections


def _is_none(lines):
    content = [line.strip().rstrip(".").lower() for line in lines if line.strip()]
    return content == [NONE_MARKER]


def _items(lines, prefix, tier, problems):
    items = []
    for line in lines:
        match = _ITEM_RE.match(line)
        if match:
            kind, number, body = match.groups()
            if kind != prefix:
                problems.append(f"{kind}{number} is listed under the {tier} heading")
                items.append(None)  # swallow its continuation lines
                continue
            items.append({"id": f"{kind}{number}", "text": body.strip()})
        elif items and items[-1] is not None and line.strip():
            items[-1]["text"] += " " + line.strip()
    items = [item for item in items if item is not None]
    seen = set()
    for item in items:
        body = item["text"]
        carried = body.endswith(CARRIED_OVER_MARKER)
        if carried:
            body = body[: -len(CARRIED_OVER_MARKER)].rstrip()
        source_markers = list(_SOURCE_SPLIT_RE.finditer(body))
        if source_markers:
            marker = source_markers[-1]
            source = body[marker.end():].strip().rstrip(".")
            item["text"] = body[:marker.start()].strip()
        else:
            source = None
            item["text"] = body.strip()
        item["source"] = source or None
        item["carried_over"] = carried
        if item["id"] in seen:
            problems.append(f"{item['id']} is listed twice")
        seen.add(item["id"])
        if not item["source"]:
            problems.append(f"{item['id']} names no source")
        elif tier == "Context" and item["source"].lower() == INFERRED_SOURCE:
            problems.append(
                f"{item['id']} is inferred from the diff and may not be Context"
            )
    return items


def _tier(lines, prefix, tier, problems):
    """The items of one tier. A section that reads `None.` is an empty
    tier by declaration; a section with lines that parse to no item is a
    fact to report, because "nothing load-bearing" and "written in a
    shape the pipeline cannot read" must not look the same."""
    if _is_none(lines):
        return []
    items = _items(lines, prefix, tier, problems)
    written = sum(1 for line in lines if line.strip())
    # An item under the wrong heading is already named by `_items`; this
    # names a body no item line of either kind could be read from.
    if not items and written and not any(_ITEM_RE.match(line) for line in lines):
        problems.append(
            f"the {tier} section has {written} line(s) but no `{prefix}<n>.` item"
            " — write `None.` when the tier is empty"
        )
    return items


def parse_change_purpose(text):
    """Parse the change purpose into its tiers.

    Returns ``{"verify": [...], "context": [...], "author_description":
    str, "problems": [str], "structured": bool}``. Each item is
    ``{"id", "text", "source", "carried_over"}``. ``structured`` is False
    when none of the required headings is present; the lists are then
    empty and there are no problems, because a purpose written without
    tiers is a fact to report, not a malformed file.
    """
    sections = _sections(text)
    if not any(heading in sections for heading in REQUIRED_HEADINGS):
        return {
            "verify": [], "context": [], "author_description": "",
            "problems": [], "structured": False,
        }
    problems = []
    for heading in REQUIRED_HEADINGS:
        if heading not in sections:
            problems.append(f"missing heading `{heading}`")
    verify_lines = sections.get(VERIFY_HEADING, [])
    context_lines = sections.get(CONTEXT_HEADING, [])
    verify = _tier(verify_lines, "V", "Verify", problems)
    context = _tier(context_lines, "C", "Context", problems)
    if len(verify) > MAX_VERIFY_ITEMS:
        problems.append(
            f"{len(verify)} Verify items — more than {MAX_VERIFY_ITEMS} means "
            "the tiering is not doing its job"
        )
    author_lines = sections.get(AUTHOR_HEADING, [])
    author = "" if _is_none(author_lines) else "\n".join(author_lines).strip()
    return {
        "verify": verify, "context": context, "author_description": author,
        "problems": problems, "structured": True,
    }


# The label a confirmed orchestrator note settles an item under: the note
# is the orchestrator's claim, its resolution is the reconciliator's
# verification, so the reconciliator is the one who settled it.
RECONCILIATOR_LABEL = "review-reconciliator"


def ledger_citations(ledger):
    """[(label, entry)] — every ledger entry that may cite a Verify item.

    Checks come labelled by their source reviewers, and every confirmed
    orchestrator note that names ``verifies`` comes as a check-shaped
    entry labelled ``RECONCILIATOR_LABEL`` with its evidence as the
    result. This is the one reader behind the record's Verify table and
    the evidence manifest: a confirmed note is a settlement the
    reconciliator made itself, and counting only checks reports the item
    as unverified.
    """
    cited = [
        (", ".join(check.get("source_reviewers") or []), check)
        for check in (ledger.get("checks") or [])
        if isinstance(check, dict)
    ]
    for note in ledger.get("orchestrator_notes") or []:
        if (
            isinstance(note, dict)
            and note.get("outcome") == "confirmed"
            and note.get("verifies")
        ):
            cited.append((RECONCILIATOR_LABEL, {
                "id": note.get("id"), "result": note.get("evidence", ""),
                "verifies": note["verifies"],
            }))
    return cited


def checks_settling(items, checks):
    """{item id: [{"reviewer", "id", "result"}]} — for every Verify item,
    the entries that cite it in their ``verifies`` list.

    ``checks`` is an iterable of ``(label, entry)``: the label names the
    entry's owner (a reviewer stem in the reconciliation context; the
    merged check's source reviewers or ``RECONCILIATOR_LABEL`` for a
    confirmed note in the record — see ``ledger_citations``). A cited id
    no item carries is not grouped here; ``undeclared_citations`` names it.
    """
    by_item = {item["id"]: [] for item in items}
    for label, check in checks:
        for item_id in check.get("verifies") or []:
            if item_id in by_item:
                by_item[item_id].append({
                    "reviewer": label,
                    "id": check.get("id"),
                    "result": check.get("result", ""),
                })
    return by_item


def undeclared_citations(items, checks):
    """[{"reviewer", "id", "cites"}] — every check citation of a Verify id
    the purpose does not declare, in check order. An orchestrator who
    renumbered items after dispatch, or a reviewer who cited an id from
    the wrong tier, produces one of these; silently dropping the citation
    would leave the real item reading as unverified with no trace of why.
    """
    declared = {item["id"] for item in items}
    found = []
    for label, check in checks:
        for item_id in check.get("verifies") or []:
            if item_id not in declared:
                found.append({
                    "reviewer": label, "id": check.get("id"), "cites": item_id,
                })
    return found

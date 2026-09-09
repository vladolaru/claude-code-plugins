"""Project settled review evidence into ids, enumerated facts and counts.

No finding prose, citation paths or host identity suffixes cross this boundary
— never a title, description, method, rationale, evidence text or path. The
ledger remains the authority; an unreadable ledger is unmeasured.

The projection telemetry shares: every final finding's id, severity, source
reviewers and critic action; the findings the critic removed, with their
sources; dropped findings by reason; check counts; Verify items and how many
checks settled each; the critic's verdict and its adjustments by action ×
outcome; orchestrator-note outcomes; and upstream citations per reviewer by
host. A producer that recorded nothing projects `None`, which stays distinct
from a measured empty collection.
"""

from collections import Counter

from .change_purpose import checks_settling, ledger_citations, undeclared_citations
from .critic_adjustments import (
    APPLIED_IDS_KEY, FINDINGS_READ_OK, OUTCOMES,
    REJECTED_ADJUSTMENTS_KEY, VERDICT_BEFORE_ADJUSTMENTS_KEY,
    read_committed_proposal, read_findings_file, read_verdict_marker,
)
from .dispatch_status import AGENT_NAME_RE, DISPATCHED_STATUSES, load_dispatch_plan
from .findings_ledger import DROP_REASONS_CHECK, DROP_REASONS_FINDING, NOTE_OUTCOMES, SOURCE_ID_RE
from .manifest_sections import read_change_purpose
from .review_document import cited_hosts, load_review_document
from .reviewer_lifecycle import review_paths
from .verdict_rules import VALID_SEVERITIES
from .reviewer_names import agent_name_from_review_stem, derive_reviewer_name
from .run_paths import artifact_path


def _agent(stem):
    if isinstance(stem, str):
        name = agent_name_from_review_stem(stem)
        if AGENT_NAME_RE.fullmatch(name):
            return name
    return None


def _sources(entries, reasons=None):
    """Every source row the ledger recorded. A reviewer stem that is not a
    safe agent name is projected as `None`, so the count stays truthful
    while the text stays local. A finding's source row carries the
    severity the source reviewer gave (None when the ledger predates the
    field), so a reconciliator re-grade — a final severity that differs
    from every source with no critic action — is countable."""
    rows = []
    for entry in entries or []:
        identity = entry.get("id")
        if not isinstance(identity, str) or not SOURCE_ID_RE.fullmatch(identity):
            continue
        row = {"agent": _agent(entry.get("reviewer")), "id": identity}
        if reasons is None:
            severity = entry.get("severity")
            row["severity"] = severity if severity in VALID_SEVERITIES else None
        if reasons is not None:
            if entry.get("reason") not in reasons:
                continue
            row["reason"] = entry["reason"]
        rows.append(row)
    return rows


def _optional_sources(container, name, reasons=None):
    """Project a source collection without inventing an absent producer."""
    if name not in container:
        return None
    return _sources(container[name], reasons)


def _finding_row(finding):
    """One finding's id, severity, source lineage and critic action.

    A finding the critic added has no reviewer sources by construction,
    so its lineage is a measured empty collection; only a finding the
    ledger recorded before sources existed has an unknown one.
    """
    action = (finding.get("critic_adjustment") or {}).get("action")
    if "sources" in finding:
        sources = _sources(finding["sources"])
    else:
        sources = [] if action == "add" else None
    return {
        "id": finding["id"], "severity": finding["severity"],
        "sources": sources, "critic_action": action,
    }


def _verify_items(output_dir, ledger):
    parsed = read_change_purpose(output_dir)
    if parsed is None or not parsed["structured"]:
        # A purpose without the parsed headings (every run before 1.119.0)
        # declared nothing to settle; that is unmeasured, not zero settled.
        return None, None
    items = parsed["verify"]
    # Checks and confirmed notes alike; the counts carry no label or prose.
    checks = ledger_citations(ledger)
    settled = checks_settling(items, checks)
    return [
        {"id": item["id"], "carried_over": bool(item.get("carried_over")),
         "settled_by": len(settled[item["id"]])}
        for item in items
    ], len(undeclared_citations(items, checks))


def _critic(output_dir, ledger):
    try:
        marker = read_verdict_marker(output_dir)
    except (OSError, ValueError):
        return None
    # The pre-adjustment verdict is a ledger fact, valid whatever the
    # proposal's state. `adjustments: None` means the proposal was
    # unreadable; {} is reserved for a validated empty proposal.
    result = {
        "verdict": marker["verdict"],
        "verdict_before_adjustments": ledger.get(VERDICT_BEFORE_ADJUSTMENTS_KEY),
        "adjustments": None,
    }
    try:
        _verdict, proposal = read_committed_proposal(output_dir)
    except (OSError, ValueError):
        return result
    action_by_id = {entry["adjustment_id"]: entry["action"] for entry in proposal["adjustments"]}
    counts = {}
    for action in action_by_id.values():
        counts.setdefault(action, Counter())["proposed"] += 1
    for key in (APPLIED_IDS_KEY, REJECTED_ADJUSTMENTS_KEY):
        for record in ledger.get(key, []):
            action = action_by_id.get(record["adjustment_id"])
            if action is not None:
                counts[action][record["outcome"]] += 1
    result["adjustments"] = {
        action: {key: counter[key] for key in ("proposed", *OUTCOMES)}
        for action, counter in sorted(counts.items())
    }
    return result


# Host names share the agent-name grammar (lowercase kebab); a cited host
# outside it is counted under `unknown` rather than transported.
_HOST_NAME_RE = AGENT_NAME_RE


def _host_citations(output_dir):
    """Per dispatched reviewer, findings citing each host by name; None when
    the dispatch plan cannot be read (nothing to count is not zero)."""
    try:
        agents = load_dispatch_plan(artifact_path(output_dir, "dispatch_plan"))["agents"]
    except (OSError, ValueError):
        return None
    citations = {}
    for agent in agents:
        if agent["status"] not in DISPATCHED_STATUSES:
            continue
        reviewer = derive_reviewer_name(agent["name"])
        try:
            document = load_review_document(review_paths(output_dir, reviewer).final, reviewer)
        except (OSError, ValueError):
            continue
        # A finding cites in `source_cited`, a check in its method and
        # result; each host counts once per finding or check.
        texts = [finding.get("source_cited") for finding in document["findings"]]
        texts += [
            " ".join(str(check.get(field) or "") for field in ("method", "result"))
            for check in document.get("checks", [])
        ]
        counts = Counter()
        for text in texts:
            for host in cited_hosts(text):
                counts[host if _HOST_NAME_RE.fullmatch(host) else "unknown"] += 1
        if counts:
            citations[agent["name"]] = dict(sorted(counts.items()))
    return citations


def build_evidence_manifest(output_dir):
    """Return the evidence section, or None for an absent or invalid ledger."""
    read = read_findings_file(artifact_path(output_dir, "review_findings_json"))
    if read.status != FINDINGS_READ_OK:
        return None
    ledger = read.findings
    verify_items, undeclared = _verify_items(output_dir, ledger)
    notes = (
        Counter(entry["outcome"] for entry in ledger["orchestrator_notes"])
        if "orchestrator_notes" in ledger
        else None
    )
    dropped = (
        Counter(entry["reason"] for entry in ledger["dropped_checks"])
        if "dropped_checks" in ledger
        else None
    )
    return {
        "findings": [_finding_row(finding) for finding in ledger["findings"]],
        "dropped_findings": _optional_sources(
            ledger, "dropped_findings", DROP_REASONS_FINDING
        ),
        # Adjudication writes the key only when a removal applied, so an
        # absent key is no removal in every ledger version, not an unknown.
        "findings_removed_by_critic": [
            _finding_row(finding)
            for finding in ledger.get("findings_removed_by_critic", [])
        ],
        "checks": {
            "count": len(ledger["checks"]),
            "dropped": (
                {reason: dropped[reason] for reason in DROP_REASONS_CHECK}
                if dropped is not None
                else None
            ),
        },
        "verify_items": verify_items,
        "undeclared_citations": undeclared,
        "critic": _critic(output_dir, ledger),
        "orchestrator_notes": (
            {outcome: notes[outcome] for outcome in NOTE_OUTCOMES}
            if notes is not None
            else None
        ),
        "host_citations": _host_citations(output_dir),
    }

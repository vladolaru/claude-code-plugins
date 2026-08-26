#!/usr/bin/env python3
"""
Reconciliation Context Builder — pre-gathers all context for the reconciliator agent.

Performance optimization: instead of the reconciliator making ~40 individual file reads,
this script collects all agent findings, referenced source snippets, scope annotations,
and metadata into a single JSON file the agent can consume immediately.

Usage:
    python3 reconciliation_context.py --output-dir /tmp/pr-review-42 --git-range abc123..HEAD
    python3 reconciliation_context.py --output-dir /tmp/pr-review-42 --git-range abc123..HEAD \
        --changed-files src/a.py,src/b.py --change-purpose "Fix auth bug" --pr-id 42

Exit codes:
    0  Success — reconciliation-context.json written
    1  Error — details on stderr

Zero external dependencies (stdlib only).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .reviewer_names import derive_reviewer_name
    from .reviewer_lifecycle import review_paths
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.reviewer_names import derive_reviewer_name
    from review.reviewer_lifecycle import review_paths

from review.agent.coverage import (
    ReviewAccountingError,
    derive_review_accounting,
)
from review.agent.output import load_review_document

from git_paths import normalize_repo_paths

SCRIPTS_DIR = Path(__file__).resolve().parent
RECONCILIATION_CONTEXT_SCHEMA = 3

_REVIEW_ACCOUNTING_FIELDS = frozenset({
    "scope_reporting_agent_count",
    "unscoped_files",
    "agents_receiving_inline_diff_by_file",
    "agents_claiming_review_by_file",
    "agents_with_unclaimed_review_by_file",
})
_REVIEW_ACCOUNTING_POPULATIONS = frozenset({
    "agents_receiving_inline_diff_by_file",
    "agents_claiming_review_by_file",
    "agents_with_unclaimed_review_by_file",
})

# Files in the output directory that are NOT agent review outputs.
# These are pipeline infrastructure files that should be skipped when
# loading agent findings.
_NON_REVIEW_FILES = frozenset([
    "dispatch-plan.json",
    "review-findings.json",
    "review-context.json",
    "pipeline-state.json",
    "run-config.json",
    "pipeline-result.json",
    "decision-critic-verdict.json",
    "clarity-assessment.json",
    "reconciliation-context.json",
])

_VALID_SEVERITY_FLOORS = frozenset(
    {"critical", "high", "medium", "low", "info"}
)
_NUMERIC_SEVERITY_FLOOR_PATTERN = "|".join(
    re.escape(value) for value in sorted(_VALID_SEVERITY_FLOORS)
)
_LEGACY_SEVERITY_FLOORS = {
    "public-contract change": "medium",
    "silent false-success": "high",
}
_LEGACY_SEVERITY_FLOOR_PATTERN = "|".join(
    re.escape(marker) for marker in _LEGACY_SEVERITY_FLOORS
)
_NUMERIC_SEVERITY_FLOOR_RE = re.compile(
    rf"(?im)^[ \t]*Severity-floor:[ \t]*"
    rf"({_NUMERIC_SEVERITY_FLOOR_PATTERN})(?=[ \t]*(?:[;—-]|$))"
)
_LEGACY_SEVERITY_FLOOR_RE = re.compile(
    rf"(?im)^[ \t]*Severity-floor:[ \t]*"
    rf"({_LEGACY_SEVERITY_FLOOR_PATTERN})(?=[ \t]*(?:;|$))"
)
_CRITIC_SEVERITY_FLOOR_MARKER_RE = re.compile(
    rf"(?im)Severity-floor:[ \t]*"
    rf"(?:{_NUMERIC_SEVERITY_FLOOR_PATTERN}|"
    rf"{_LEGACY_SEVERITY_FLOOR_PATTERN})"
    rf"(?!\w)[ \t]*(?:(?:[;—-])[ \t]*)?"
)


def _validate_string_list(value: Any, label: str, *, allow_none=False) -> None:
    if allow_none and value is None:
        return
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        suffix = " or null" if allow_none else ""
        raise ValueError(f"{label} must be unique non-empty strings{suffix}")


def review_accounting_from_context(context: Any) -> Optional[Dict[str, Any]]:
    """Return exact schema-3 review accounting, or reject the context."""
    if not isinstance(context, dict):
        raise ValueError("reconciliation context must be an object")
    if (
        type(context.get("schema")) is not int
        or context["schema"] != RECONCILIATION_CONTEXT_SCHEMA
    ):
        raise ValueError("reconciliation context schema must be 3")
    if "review_accounting" not in context:
        raise ValueError("reconciliation context is missing review_accounting")
    accounting = context["review_accounting"]
    if accounting is None:
        return None
    if not isinstance(accounting, dict):
        raise ValueError(
            "reconciliation review_accounting must be an object or null"
        )
    if set(accounting) != _REVIEW_ACCOUNTING_FIELDS:
        raise ValueError("reconciliation review_accounting fields are invalid")
    count = accounting["scope_reporting_agent_count"]
    if type(count) is not int or count < 0:
        raise ValueError(
            "reconciliation review_accounting scope count must be non-negative"
        )
    _validate_string_list(
        accounting["unscoped_files"],
        "reconciliation review_accounting unscoped_files",
        allow_none=True,
    )
    for field in _REVIEW_ACCOUNTING_POPULATIONS:
        population = accounting[field]
        if not isinstance(population, dict):
            raise ValueError(
                f"reconciliation review_accounting {field} is malformed"
            )
        for path, agents in population.items():
            if not isinstance(path, str) or not path:
                raise ValueError(
                    f"reconciliation review_accounting {field} has "
                    "an invalid path"
                )
            _validate_string_list(
                agents,
                f"reconciliation review_accounting {field}[{path!r}]",
            )
            if not agents:
                raise ValueError(
                    f"reconciliation review_accounting {field}[{path!r}] "
                    "must name at least one agent"
                )
    return accounting


def resolve_structured_severity_floor(finding: Dict[str, Any]) -> Optional[str]:
    """Return a valid explicit floor without consulting description prose."""
    structured = finding.get("severity_floor")
    if (
        isinstance(structured, str)
        and structured.lower() in _VALID_SEVERITY_FLOORS
    ):
        return structured.lower()
    return None


def resolve_severity_floor(finding: Dict[str, Any]) -> Optional[str]:
    """Resolve a structured or backward-compatible description floor."""
    structured = resolve_structured_severity_floor(finding)
    if structured is not None:
        return structured

    # Coerce first: a malformed (list/non-string) description must not silently
    # drop a mandatory floor. load_agent_reviews pops severity_floor when this
    # returns None, so returning None for a list that carries a legacy marker
    # would downgrade the finding. _coerce_text joins list items on newlines,
    # keeping the MULTILINE ^Severity-floor: anchor matchable.
    description = _coerce_text(finding.get("description", ""))
    numeric = _NUMERIC_SEVERITY_FLOOR_RE.search(description)
    if numeric:
        return numeric.group(1).lower()
    legacy = _LEGACY_SEVERITY_FLOOR_RE.search(description)
    if legacy:
        return _LEGACY_SEVERITY_FLOORS[legacy.group(1).lower()]
    return None


def strip_severity_floor_markers(text: Any) -> str:
    """Remove prose floor markers before content reaches the critic.

    A ``Severity-floor:`` marker is a reviewer-to-reconciliator directive.
    The reconciliator has already acted on it by the time anything
    downstream renders the finding, and prose that still carries it reads
    to the decision critic as a standing instruction not to demote — which
    is exactly the judgment the critic exists to make independently. The
    STRUCTURED floor still renders (``resolve_structured_severity_floor``);
    only the prose restatement goes.

    Public because its caller lives elsewhere:
    ``orchestration.assemble_review_record()``, which renders the record
    the critic reads. It stays here, beside the patterns it is compiled
    from, rather than being copied to that caller.

    Accepts any type and coerces first: this is a regex chokepoint that
    free-form (model-authored) finding text flows through, so a non-string
    value must not raise here.
    """
    return _CRITIC_SEVERITY_FLOOR_MARKER_RE.sub("", _coerce_text(text))


def extract_host_banner(output_dir: str) -> Optional[Dict[str, Any]]:
    """Return host_context.banner from review-context.json, or None.

    Safe on missing file, malformed JSON, and missing host_context key.

    Args:
        output_dir: Directory containing pipeline output files, including
            ``review-context.json``.

    Returns:
        The ``host_context.banner`` dict if present, or ``None``.
    """
    if not output_dir:
        return None
    ctx_path = os.path.join(output_dir, "review-context.json")
    if not os.path.isfile(ctx_path):
        return None
    try:
        with open(ctx_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    host_context = data.get("host_context") or {}
    if not isinstance(host_context, dict):
        return None
    return host_context.get("banner")


def _review_stem(agent: str) -> str:
    """Map an agent name to its review-file stem: derive_reviewer_name(agent) + "-review"."""
    return f"{derive_reviewer_name(agent)}-review"


def _load_review_payload(output_dir: str, agent: str) -> Optional[Dict[str, Any]]:
    """Read one agent's review JSON, or None when it is unreadable.

    ``load_agent_reviews`` reads the same files for the findings payload
    through its own path and failure policy — it reports malformed output on
    stderr and skips it, which is not what run-level file accounting wants.
    """
    reviewer = derive_reviewer_name(agent)
    path = review_paths(output_dir, reviewer).final
    try:
        data = load_review_document(path, reviewer)
    except ValueError:
        return None
    return data


def _load_agent_review_accounting(
    output_dir: str, agent: str
) -> Optional[Any]:
    """Derive one finalized review's canonical file accounting."""
    data = _load_review_payload(output_dir, agent)
    if data is None or "reviewed_file_claims" not in data:
        return None
    reviewer = derive_reviewer_name(agent)
    path = review_paths(output_dir, reviewer).accounting_input
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            accounting_input = json.load(file_handle)
    except (OSError, json.JSONDecodeError):
        return None
    try:
        accounting = derive_review_accounting(
            accounting_input, data["reviewed_file_claims"]
        )
    except (ReviewAccountingError, TypeError):
        try:
            accounting = derive_review_accounting(accounting_input, [])
        except ReviewAccountingError:
            return None
    return accounting if accounting.agent_name == agent else None


# Every file-carrying list a scope summary sidecar can hold
# (`agent/scope.py::write_scope_summary`). Their union is "every changed
# file that reached at least one reviewer's scope in any form", which is
# what `unscoped_files` is the complement of. Adding a list to the sidecar
# without adding it here would silently reclassify covered files as
# never-scoped.
#
# `in_scope_review_files` is the only one written in every scope mode,
# and it is why the union is trustworthy at all: a `--base-ref-only` or
# `--summary` agent (patterns-reviewer by registry config; any reviewer on
# a 100+-file PR by protocol) never fetches a diff, so its other three
# lists are legitimately empty and every file it owned used to look
# unowned.
_SCOPE_SUMMARY_FILE_LISTS = (
    "inline_diff_files",
    "review_claimable_files",
    "list_only_files",
    "in_scope_review_files",
)


def _unscoped_files(
    changed_files: Optional[List[str]],
    scoped_anywhere: set,
) -> Optional[List[str]]:
    """Changed files no reviewer's scope contained, or None if unmeasured.

    Both sides of the subtraction go through
    ``git_paths.normalize_repo_paths`` — the one repo-path grammar the run
    manifest's coverage builder already uses — because the two producers
    quote differently: scope sidecars run ``-c core.quotepath=false`` and
    emit ``src/café.php``, while ``context.py``'s plain
    ``git diff --name-only`` emits ``"src/caf\303\251.php"``. Subtracting
    one alphabet from the other publishes fully reviewed non-ASCII files as
    reviewed by no one, inside a report block the orchestrator is now
    forbidden to correct.

    ``strict=True`` on the changed side: a path this grammar cannot make
    safe leaves the whole population unmeasured (``None``) rather than
    quietly shrinking it, because a shrunken set here reads as a cleaner
    review than the run earned.

    An EMPTY changed-file list is unmeasured too, not measured-and-zero.
    A review of zero changed files does not exist, while a run whose file
    list never reached the builder very much does: orchestration.py always
    passes ``--changed-files`` and passes ``""`` when review-context.json
    carries no CSV. One rule — no list means nothing was measured — is what
    keeps that failure from publishing ``unscoped_files: []``, a clean
    coverage bill for a population nothing looked at.
    """
    if not changed_files:
        return None
    normalized = normalize_repo_paths(changed_files, strict=True)
    if normalized is None:
        return None
    return sorted(set(normalized) - scoped_anywhere)


def aggregate_review_accounting(
    output_dir: str,
    changed_files: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Aggregate per-agent scope summaries into run-level review accounting.

    Reads schema-2 ``*-scope-summary*.json`` sidecars, carries inline-diff
    receipt by file, and validates finalized reviewed-file claims through
    the shared accounting authority. Review-claimable paths without a
    validated claim remain visible per agent. When ``changed_files`` is
    supplied, ``unscoped_files`` is its complement against every path any
    scope summary mentions; it stays None when that population was not
    measured.

    Returns None when no summaries exist (pre-sidecar runs) so callers can
    distinguish "no data" from "no gaps".
    """
    inline: Dict[str, set] = {}
    claimable_by_agent: Dict[str, set] = {}
    # Distinct agent NAMES, not summary files: three reviewers ship a
    # second `-config-ops` sidecar, so counting files reported 22 agents
    # for a 19-agent run. Both of an agent's sidecars derive the same name
    # through the rsplit below, so the set collapses them.
    reporting_agents: set = set()
    # Sidecar paths, normalized through the ONE shared repo-path grammar
    # (`git_paths.normalize_repo_paths`) rather than compared raw. The
    # `unscoped_files` set difference below is subtraction between two
    # producers with different quoting settings — the sidecars run
    # `-c core.quotepath=false`, `context.py` does not — so raw arithmetic
    # reports `"src/caf\303\251.php"` and `src/café.php` as different
    # files and publishes a fully reviewed non-ASCII file as reviewed by
    # nobody. Non-strict here: one junk sidecar entry must not void an
    # agent's whole contribution, and dropping it can only over-report a
    # gap, never hide one.
    scoped_anywhere: set = set()
    try:
        entries = sorted(os.scandir(output_dir), key=lambda e: e.name)
    except OSError:
        return None
    for entry in entries:
        name = entry.name
        if "-scope-summary" not in name or not name.endswith(".json"):
            continue
        # Last occurrence is the delimiter: filenames always END with
        # "-scope-summary[-<domain>].json" and no domain contains the
        # marker, while adapter instance names are repo-authored kebab ids
        # that legally may ("repo-payments-scope-summary-contract-reviewer").
        # A first-occurrence split would truncate such an agent name and
        # misattribute its scope.
        agent = name.rsplit("-scope-summary", 1)[0]
        try:
            with open(entry.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("schema") != 2:
            continue
        if any(
            not isinstance(data.get(key), list)
            for key in _SCOPE_SUMMARY_FILE_LISTS
        ):
            continue
        reporting_agents.add(agent)
        for key in _SCOPE_SUMMARY_FILE_LISTS:
            scoped_anywhere.update(
                normalize_repo_paths(data.get(key)) or []
            )
        for f_path in data["inline_diff_files"]:
            if isinstance(f_path, str):
                inline.setdefault(f_path, set()).add(agent)
        for f_path in data["review_claimable_files"]:
            if isinstance(f_path, str):
                claimable_by_agent.setdefault(agent, set()).add(f_path)
    if not reporting_agents:
        return None

    claimed: Dict[str, set] = {}
    unclaimed: Dict[str, set] = {}
    for agent in reporting_agents:
        accounting = _load_agent_review_accounting(output_dir, agent)
        if accounting is None:
            for f_path in claimable_by_agent.get(agent, set()):
                unclaimed.setdefault(f_path, set()).add(agent)
            continue
        for f_path in accounting.reviewed_file_claims:
            claimed.setdefault(f_path, set()).add(agent)
        for f_path in accounting.unclaimed_review_files:
            unclaimed.setdefault(f_path, set()).add(agent)

    return {
        # Distinct reviewers that produced at least one scope summary, not
        # summary files aggregated — an agent with a primary and a
        # secondary-domain sidecar is one agent.
        "scope_reporting_agent_count": len(reporting_agents),
        # Changed files no reviewer's scope contained in any form — see
        # `_unscoped_files` for the subtraction and its path grammar. NOT
        # the same measurement as the run manifest's `coverage.uncovered`:
        # different population (full changed set vs. noise-filtered
        # `reviewable`) over different evidence (runtime sidecars vs.
        # dispatch-time SCOPE events), so the two numbers legitimately
        # differ. The full divergence note lives at the one other site,
        # manifest_sections.py's `"uncovered"` key; read it before
        # "reconciling" the two.
        "unscoped_files": _unscoped_files(changed_files, scoped_anywhere),
        "agents_receiving_inline_diff_by_file": {
            f: sorted(a) for f, a in sorted(inline.items())
        },
        "agents_claiming_review_by_file": {
            f: sorted(a) for f, a in sorted(claimed.items())
        },
        "agents_with_unclaimed_review_by_file": {
            f: sorted(a) for f, a in sorted(unclaimed.items())
        },
    }


def compute_missing_agents(
    dispatched_stems: Optional[List[str]],
    reviews_by_agent: Dict[str, Any],
) -> Optional[List[str]]:
    """Which dispatched agents produced no output — measured, not asked for.

    A dispatched reviewer that crashed or timed out is exactly the fact a
    review must not quietly lose, and `schemas/review-output.ts` gives it a
    home at `meta.reconciliation.missing_agents`. The subtraction is
    deterministic, so it belongs here: the reconciliator VERIFIES and
    carries this list rather than recomputing it from two others, and a
    model that fumbles a set difference cannot cost the run a missing agent.

    `dispatched_stems` is already in `reviews_by_agent`'s key spelling (see
    `_review_stem`). Returns:

    * ``None`` when dispatch is unknown (no plan; `--dispatched-agents`
      omitted) — UNMEASURED, and never `[]`. "Nothing was measured" must
      not read as "nobody was missing", the same rule `unscoped_files`
      follows.
    * a sorted list otherwise, including the measured-empty ``[]`` that an
      explicitly empty dispatch (a docs-only change) earns.

    Sorted, not dispatch-ordered, so two runs diff on substance. Output
    from an agent nobody dispatched is not this function's anomaly and is
    simply not subtracted.
    """
    if dispatched_stems is None:
        return None
    return sorted(set(dispatched_stems) - set(reviews_by_agent))


# The two out-of-scope statuses that carry no judgment: the file is not in
# the diff at all, or its only change is a rename/chmod. Both are decidable
# from the diff alone. `not_in_hunk` is deliberately absent — agent line
# numbers can be imprecise and the ±5 proximity window does not catch every
# case, so the reconciliator checks the source snippet before dropping one.
# Annotating it would turn a documented hedge into a machine verdict.
_PREFILTER_SCOPES = frozenset([
    "OUT_OF_SCOPE:file_not_in_diff",
    "OUT_OF_SCOPE:metadata_only",
])

_PREFILTER_KEY = "prefiltered"


def annotate_prefiltered_findings(
    reviews_by_agent: Dict[str, Any],
    scope_annotations: Dict[str, Any],
) -> Dict[str, Any]:
    """Mark structurally-certain out-of-scope findings, in place.

    Mutates `reviews_by_agent` and returns the audit summary
    ``{"count": N, "by_agent": {...}}``.

    Why annotate rather than delete. The retired Markdown projection
    removed these findings before the reconciliator saw them: a real
    machine guarantee, but an invisible one — the drop left no trace in any
    artifact, so nobody could audit what had been decided on their behalf.
    Deleting them from the JSON would be worse still: `reviews_by_agent` is
    also the record of what each reviewer actually said, and the
    reconciliation metrics (`input_finding_count`, per-agent tallies) are
    counted from it, so removals would silently shift numbers nothing else
    could reconstruct.

    Annotating keeps both properties. The scope verdict stays a MACHINE
    decision — the reconciliator is told to drop every finding carrying
    this key, which is obedience, not judgment — and the summary count
    beside it makes that obedience checkable: N annotated in, N dropped
    out. The agent's instruction is the backstop; this function is the
    mechanism.

    This function OWNS the key: an in-scope finding that arrives already
    carrying one (reused input, a hand edit) has it cleared, because a
    stale marker silently deletes a real finding. Malformed shapes are
    skipped rather than raising — reviewer JSON is model-authored, and
    pipeline step 8 is the whole review.
    """
    by_agent: Dict[str, int] = {}
    for agent, payload in reviews_by_agent.items():
        if not isinstance(payload, dict):
            continue
        findings = payload.get("findings")
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            finding.pop(_PREFILTER_KEY, None)
            file_path = finding.get("file")
            line = finding.get("line")
            if not file_path or not line:
                continue
            status = scope_annotations.get(f"{file_path}:{line}")
            if status in _PREFILTER_SCOPES:
                finding[_PREFILTER_KEY] = status
                by_agent[agent] = by_agent.get(agent, 0) + 1
    return {"count": sum(by_agent.values()), "by_agent": by_agent}


def load_agent_reviews(
    output_dir: str,
    dispatched_agents: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Load agent review JSON files from the output directory.

    Reads ``*-review.json`` files, skipping pipeline infrastructure files
    listed in ``_NON_REVIEW_FILES``. Malformed JSON files are skipped with
    a warning on stderr.

    Args:
        output_dir: Directory containing agent review outputs.
        dispatched_agents: Optional list of agent names from the dispatch
            plan (e.g., ``["security-reviewer", "performance-reviewer"]``).
            When provided, only review files for these agents are loaded.
            Agent names are mapped to file stems by replacing ``-reviewer``
            with ``-review``. When ``None``, all review files are loaded
            (the all-files mode).

    Returns:
        Dict keyed by agent name (e.g., "security-review") with the parsed
        JSON content as the value.
    """
    reviews = {}
    output_path = Path(output_dir)

    if not output_path.is_dir():
        print(f"WARNING: output directory does not exist: {output_dir}", file=sys.stderr)
        return reviews

    # Build allowed file stems from dispatch plan agent names.
    allowed_stems: Optional[frozenset] = None
    if dispatched_agents is not None:
        allowed_stems = frozenset(
            _review_stem(name) for name in dispatched_agents
        )

    for entry in sorted(output_path.iterdir()):
        if not entry.name.endswith("-review.json"):
            continue
        if entry.name in _NON_REVIEW_FILES:
            continue
        if allowed_stems is not None and entry.stem not in allowed_stems:
            continue

        try:
            reviewer = entry.stem.removesuffix("-review")
            data = load_review_document(entry, reviewer)
            review_findings = data.get("findings", [])
            if isinstance(review_findings, list):
                for finding in review_findings:
                    if not isinstance(finding, dict):
                        continue
                    floor = resolve_severity_floor(finding)
                    if floor is None:
                        finding.pop("severity_floor", None)
                    else:
                        finding["severity_floor"] = floor
            # Key by filename without .json extension (e.g., "security-review")
            agent_name = entry.stem
            reviews[agent_name] = data
        except ValueError as exc:
            print(f"WARNING: skipping malformed file {entry.name}: {exc}", file=sys.stderr)

    return reviews


def extract_references(reviews_by_agent: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract unique file:line references from all agent findings.

    Scans the 'findings' list in each agent's findings and collects unique
    file paths with their referenced line numbers.

    Returns:
        List of dicts, each with:
        - "file": str — the file path as reported by the agent
        - "lines": List[int] — sorted, deduplicated line numbers

        Skips findings without a valid integer 'line' field.
    """
    file_lines: Dict[str, set] = {}

    for _agent_name, data in reviews_by_agent.items():
        findings = data.get("findings", [])
        if not isinstance(findings, list):
            continue

        for finding in findings:
            if not isinstance(finding, dict):
                continue
            file_path = finding.get("file")
            line = finding.get("line")

            if not file_path or not isinstance(file_path, str):
                continue
            if not isinstance(line, int) or line <= 0:
                continue

            if file_path not in file_lines:
                file_lines[file_path] = set()
            file_lines[file_path].add(line)

    return [
        {"file": fp, "lines": sorted(lines)}
        for fp, lines in sorted(file_lines.items())
    ]


def _get_git_root() -> Optional[str]:
    """Return the git repository root, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _read_git_content(
    file_path: str,
    base_ref: str,
    git_root: Optional[str] = None,
) -> Optional[List[str]]:
    """Read file content from a git ref (for deleted/renamed files).

    Falls back to git history when the working-tree file no longer exists,
    preserving source evidence for deletion-based findings.

    Args:
        file_path: Repo-relative file path.
        base_ref: Git ref to read from (e.g., merge base commit).
        git_root: Git repository root (used as cwd for git commands).

    Returns:
        List of lines (with newlines), or None on failure.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{base_ref}:{file_path}"],
            capture_output=True, text=True, timeout=5,
            cwd=git_root,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout.splitlines(keepends=True)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def read_source_snippets(
    references: List[Dict[str, Any]],
    context_lines: int = 10,
    git_root: Optional[str] = None,
    base_ref: Optional[str] = None,
    old_side_files: Optional[set] = None,
) -> Dict[str, str]:
    """Read source code snippets around referenced lines.

    For each referenced file, reads ±context_lines around each line number.
    Overlapping windows are merged. Missing files are skipped gracefully.
    When a file doesn't exist in the working tree (deleted by this patch),
    falls back to reading from ``base_ref`` via ``git show``.

    For files that still exist but have deletion hunks (listed in
    *old_side_files*), also reads the pre-change version from ``base_ref``
    and includes it as a separate ``"[pre-change] file"`` entry.  This
    ensures the reconciliator has evidence for findings about deleted code
    even when the file survives the patch.

    Args:
        references: List of {"file": path, "lines": [int, ...]} dicts.
        context_lines: Number of lines of context around each reference.
        git_root: Git repository root. Relative file paths from agent
            findings are resolved against this directory. When None, falls
            back to CWD-based resolution (which breaks when the pipeline
            is launched from a subdirectory).
        base_ref: Git ref for old-side content (e.g., merge base). Used
            to recover snippets for files deleted by the patch.
        old_side_files: Set of repo-relative file paths that have deletion
            hunks (old_count > new_count). When ``base_ref`` is also
            available, pre-change content is read and included alongside
            the working-tree snippet.

    Returns:
        Dict mapping original file paths to snippet text with line numbers.
        Format: "  42 | code here\\n  43 | more code\\n..."
        Deleted-file snippets include a ``[deleted]`` prefix.
        Pre-change snippets use a ``[pre-change] file`` key.
    """
    snippets: Dict[str, str] = {}
    _old_side = old_side_files or set()

    for ref in references:
        file_path = ref["file"]
        lines = ref["lines"]

        # Agent findings use git-root-relative paths. Resolve against the
        # git root so snippets load correctly regardless of CWD.
        if os.path.isabs(file_path):
            read_path = file_path
        elif git_root:
            read_path = os.path.join(git_root, file_path)
        else:
            read_path = str(Path(file_path).resolve())

        source_lines: Optional[List[str]] = None
        deleted = False

        if os.path.isfile(read_path):
            # Security: resolved path must be within the git root. Prevents
            # reading files outside the repo via hallucinated absolute paths
            # that suffix-match changed filenames in _file_in_changed().
            if git_root:
                try:
                    real_read = os.path.realpath(read_path)
                    real_root = os.path.realpath(git_root)
                    if not real_read.startswith(real_root + os.sep):
                        continue
                except (OSError, ValueError):
                    continue

            try:
                with open(read_path, "r", encoding="utf-8", errors="replace") as f:
                    source_lines = f.readlines()
            except OSError:
                pass
        elif base_ref:
            # File deleted by this patch — recover old-side content from git.
            source_lines = _read_git_content(file_path, base_ref, git_root)
            deleted = True

        if not source_lines:
            continue

        total_lines = len(source_lines)
        if total_lines == 0:
            continue

        # Build merged windows
        windows = []
        for line_num in lines:
            start = max(1, line_num - context_lines)
            end = min(total_lines, line_num + context_lines)
            windows.append((start, end))

        # Merge overlapping windows
        merged = _merge_windows(windows)

        # Extract snippets
        snippet_parts = []
        for start, end in merged:
            for i in range(start, end + 1):
                line_text = source_lines[i - 1].rstrip("\n")
                snippet_parts.append(f"{i:>6} | {line_text}")

        if snippet_parts:
            prefix = "[deleted] " if deleted else ""
            snippets[file_path] = prefix + "\n".join(snippet_parts)

        # For surviving files with deletion hunks, also read pre-change
        # content so the reconciliator has evidence for deleted-code findings.
        if not deleted and base_ref and _file_in_old_side(file_path, _old_side):
            old_lines = _read_git_content(file_path, base_ref, git_root)
            if old_lines:
                old_total = len(old_lines)
                old_windows = []
                for line_num in lines:
                    s = max(1, line_num - context_lines)
                    e = min(old_total, line_num + context_lines)
                    if s <= e:
                        old_windows.append((s, e))
                old_merged = _merge_windows(old_windows)
                old_parts = []
                for s, e in old_merged:
                    for i in range(s, e + 1):
                        lt = old_lines[i - 1].rstrip("\n")
                        old_parts.append(f"{i:>6} | {lt}")
                if old_parts:
                    snippets[f"[pre-change] {file_path}"] = "\n".join(old_parts)

    return snippets


def _file_in_old_side(file_path: str, old_side_files: set) -> bool:
    """Check if file_path matches any entry in old_side_files (suffix matching)."""
    norm = file_path.replace("\\", "/")
    for entry in old_side_files:
        norm_entry = entry.replace("\\", "/")
        if norm == norm_entry:
            return True
        if norm.endswith("/" + norm_entry) or norm_entry.endswith("/" + norm):
            return True
    return False


def _merge_windows(windows: List[tuple]) -> List[tuple]:
    """Merge overlapping (start, end) windows into non-overlapping ranges."""
    if not windows:
        return []

    sorted_windows = sorted(windows)
    merged = [sorted_windows[0]]

    for start, end in sorted_windows[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            # Overlapping or adjacent — merge
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged



_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE
)

# Lines of proximity to a changed hunk that count as "near" the change.
# Matches the old reconciliator's "within 5 lines of a hunk" rule.
_HUNK_PROXIMITY = 5


def _parse_diff_hunks(
    git_range: str,
) -> Tuple[Dict[str, List[Tuple[int, int]]], set]:
    """Parse git diff to extract changed line ranges per file.

    Runs ``git diff --unified=0 <git_range>`` and extracts old-side and
    new-side line ranges separately from ``@@`` hunk headers.  Storing
    them as individual entries (instead of a union) prevents false
    in-scope gaps when insertions/deletions shift later hunks.

    Returns:
        Tuple of:
        - Dict mapping repo-relative file paths to lists of ``(start, end)``
          tuples.  Each hunk may contribute up to two entries (old-side
          and new-side), so the list may contain overlapping ranges.
        - Set of file paths that have at least one deletion hunk
          (old_count > new_count), used to trigger old-side snippet reads.
        Returns ``({}, set())`` if git diff fails or times out.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--unified=0", git_range],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {}, set()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}, set()

    hunks: Dict[str, List[Tuple[int, int]]] = {}
    files_with_deletions: set = set()
    current_file: Optional[str] = None

    for line in result.stdout.splitlines():
        if line.startswith("diff --git "):
            current_file = None
        elif line.startswith("+++ b/"):
            current_file = line[6:]  # strip "+++ b/"
            if current_file not in hunks:
                hunks[current_file] = []
        elif line.startswith("@@ ") and current_file is not None:
            m = _HUNK_HEADER_RE.match(line)
            if m:
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) else 1
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) else 1

                if old_count > new_count:
                    files_with_deletions.add(current_file)

                if old_count == 0 and new_count == 0:
                    # Both sides empty — metadata-only marker.
                    hunks[current_file].append(
                        (min(old_start, new_start), min(old_start, new_start))
                    )
                    continue

                # Store old-side and new-side ranges separately so
                # findings citing either coordinate system are IN_SCOPE,
                # without creating a false in-scope gap between them when
                # insertions/deletions shift line numbers.
                old_range = (old_start, old_start + old_count - 1) if old_count > 0 else None
                new_range = (new_start, new_start + new_count - 1) if new_count > 0 else None
                if old_range:
                    hunks[current_file].append(old_range)
                if new_range and new_range != old_range:
                    hunks[current_file].append(new_range)

    return hunks, files_with_deletions


def _find_file_hunks(
    file_path: str, diff_hunks: Dict[str, List[Tuple[int, int]]]
) -> Optional[List[Tuple[int, int]]]:
    """Find hunk ranges for *file_path* using suffix matching.

    Agent findings may use repo-relative paths (``src/auth.py``) while
    diff_hunks keys are always repo-relative from ``git diff``.

    Returns:
        List of hunk ranges if the file was found in the diff (deletion-only
        hunks produce zero-width ``(start, start)`` markers), or ``None`` if
        the file was not found at all.
    """
    norm_path = file_path.replace("\\", "/")

    # Exact match first
    if norm_path in diff_hunks:
        return diff_hunks[norm_path]

    # Suffix match
    for diff_file, ranges in diff_hunks.items():
        norm_diff = diff_file.replace("\\", "/")
        if norm_path.endswith("/" + norm_diff) or norm_diff.endswith("/" + norm_path):
            return ranges

    return None


def _line_near_hunk(
    line: int, hunks: List[Tuple[int, int]], proximity: int
) -> bool:
    """Return True if *line* falls within *proximity* lines of any hunk."""
    for start, end in hunks:
        if (start - proximity) <= line <= (end + proximity):
            return True
    return False


def check_scope(
    references: List[Dict[str, Any]],
    changed_files: List[str],
    git_range: str,
    diff_hunks: Optional[Dict[str, List[Tuple[int, int]]]] = None,
) -> Dict[str, str]:
    """Annotate each referenced file:line as IN_SCOPE or OUT_OF_SCOPE.

    Performs two levels of scope checking:

    1. **File-level** — Is the file in the diff at all?
    2. **Hunk-level** — Is the referenced line in or near a changed hunk?

    This prevents pre-existing findings on untouched lines of a touched file
    from being reported as if this patch introduced them.

    Args:
        references: Output of extract_references().
        changed_files: List of file paths from the diff.
        git_range: Git range string for ``git diff``.
        diff_hunks: Pre-parsed hunk ranges from ``_parse_diff_hunks()``.
            When ``None``, hunks are parsed from git diff on demand.

    Returns:
        Dict mapping ``"file:line"`` strings to scope status:
        - ``"IN_SCOPE:in_hunk"`` — line is inside a changed hunk
        - ``"IN_SCOPE:near_hunk"`` — line is within ±5 lines of a hunk
        - ``"OUT_OF_SCOPE:not_in_hunk"`` — file is changed but line is far
          from any hunk (pre-existing code)
        - ``"OUT_OF_SCOPE:file_not_in_diff"`` — file not in the diff at all
    """
    annotations: Dict[str, str] = {}

    # Parse hunks from git diff (best-effort; falls back to file-level)
    if diff_hunks is None:
        diff_hunks, _ = _parse_diff_hunks(git_range)

    for ref in references:
        file_path = ref["file"]
        ref_lines = ref["lines"]

        if not _file_in_changed(file_path, changed_files):
            for line in ref_lines:
                annotations[f"{file_path}:{line}"] = "OUT_OF_SCOPE:file_not_in_diff"
            continue

        # File is in the diff — check hunk-level
        file_hunks = _find_file_hunks(file_path, diff_hunks)

        if file_hunks is None:
            # File not found in diff output (git diff failed, binary file,
            # or suffix-matching miss). Fall back to file-level IN_SCOPE
            # to avoid false negatives.
            for line in ref_lines:
                annotations[f"{file_path}:{line}"] = "IN_SCOPE:in_hunk"
            continue

        if not file_hunks:
            # File appears in the diff but has no hunk entries — pure
            # rename, chmod, or other metadata-only change. No content
            # was modified, so all lines are pre-existing code.
            for line in ref_lines:
                annotations[f"{file_path}:{line}"] = "OUT_OF_SCOPE:metadata_only"
            continue

        for line in ref_lines:
            if _line_near_hunk(line, file_hunks, proximity=0):
                annotations[f"{file_path}:{line}"] = "IN_SCOPE:in_hunk"
            elif _line_near_hunk(line, file_hunks, proximity=_HUNK_PROXIMITY):
                annotations[f"{file_path}:{line}"] = "IN_SCOPE:near_hunk"
            else:
                annotations[f"{file_path}:{line}"] = "OUT_OF_SCOPE:not_in_hunk"

    return annotations


def _file_in_changed(file_path: str, changed_files: List[str]) -> bool:
    """Check if file_path matches any entry in changed_files using suffix matching."""
    # Normalize separators
    norm_path = file_path.replace("\\", "/")

    for changed in changed_files:
        norm_changed = changed.replace("\\", "/")
        if norm_path == norm_changed:
            return True
        if norm_path.endswith("/" + norm_changed) or norm_changed.endswith("/" + norm_path):
            return True

    return False


def filter_in_scope_references(
    references: List[Dict[str, Any]],
    scope_annotations: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Filter references to only include files with at least one IN_SCOPE line.

    Security gate: prevents reading source snippets for files outside the
    reviewed diff (e.g., hallucinated absolute paths, ``../`` escapes).

    Args:
        references: Output of extract_references().
        scope_annotations: Output of check_scope().

    Returns:
        Filtered list of references. Each entry retains only the lines that
        are IN_SCOPE. Entries with no in-scope lines are dropped entirely.
    """
    filtered = []
    for ref in references:
        file_path = ref["file"]
        in_scope_lines = [
            line for line in ref["lines"]
            if scope_annotations.get(f"{file_path}:{line}", "").startswith("IN_SCOPE")
        ]
        if in_scope_lines:
            filtered.append({"file": file_path, "lines": in_scope_lines})
    return filtered


def resolve_output_builder_path() -> str:
    """Return the path to the ReviewOutputBuilder script.

    The script knows its own location relative to the output builder.
    """
    return str(SCRIPTS_DIR / "agent" / "output.py")


def _coerce_text(value: Any) -> str:
    """Coerce an agent-supplied finding field to a string for rendering.

    Reviewer JSON is model-authored, so a field the schema expects to be a
    string (``title``, ``description``, ``recommendation``) can arrive as a
    list, number, or ``None``.  Passing those straight into the regex helpers
    below raises ``TypeError`` and \u2014 because rendering happens inside the
    reconciliation step \u2014 aborts the entire review.  Coerce defensively:
    lists/tuples join on newlines (matching how multi-part text is rendered
    elsewhere), ``None`` becomes empty, everything else stringifies.
    """
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(_coerce_text(item) for item in value)
    return str(value)


def main() -> int:
    """CLI entry point. Gathers all reconciliation context and writes JSON."""
    parser = argparse.ArgumentParser(
        description="Build reconciliation context for the reconciliator agent."
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory containing agent review outputs.",
    )
    parser.add_argument(
        "--git-range", required=True,
        help="Git range for the review (e.g., abc123..HEAD).",
    )
    parser.add_argument(
        "--changed-files", default="",
        help="Comma-separated list of changed file paths.",
    )
    parser.add_argument(
        "--change-purpose", default="",
        help="Description of the change purpose.",
    )
    parser.add_argument(
        "--pr-id", default="",
        help="Pull request ID.",
    )
    parser.add_argument(
        "--dispatched-agents", default=None,
        help="Comma-separated agent names from the dispatch plan. "
             "When provided, only review files for these agents are loaded. "
             "Pass an empty string to indicate 0 agents were dispatched.",
    )

    args = parser.parse_args()

    output_dir = args.output_dir
    git_range = args.git_range
    changed_files = [f.strip() for f in args.changed_files.split(",") if f.strip()]
    change_purpose = args.change_purpose
    pr_id = args.pr_id
    dispatched_agents: Optional[List[str]] = None
    if args.dispatched_agents is not None:
        stripped = args.dispatched_agents.strip()
        if stripped:
            dispatched_agents = [
                a.strip() for a in stripped.split(",") if a.strip()
            ]
        else:
            # Explicitly empty — 0 agents were dispatched (not "unknown").
            dispatched_agents = []

    try:
        # 1. Load agent findings (filtered to dispatch plan when provided)
        reviews_by_agent = load_agent_reviews(output_dir, dispatched_agents)

        # 2. Extract file:line references
        references = extract_references(reviews_by_agent)

        # 3. Parse diff hunks once — reused for scope checking and snippet
        #    reading.  files_with_deletions identifies surviving files that
        #    need old-side snippet reads.
        diff_hunks, files_with_deletions = _parse_diff_hunks(git_range)

        # 4. Annotate scope BEFORE reading snippets — prevents reading
        #    files outside the diff (hallucinated paths, ../ escapes).
        scope_annotations = check_scope(
            references, changed_files, git_range, diff_hunks=diff_hunks,
        )

        # 5. Filter to in-scope references, then read source snippets.
        #    Extract base_ref for deleted-file fallback (git show).
        in_scope_refs = filter_in_scope_references(references, scope_annotations)
        git_root = _get_git_root()
        base_ref = git_range.split("..")[0] if ".." in git_range else None
        source_snippets = read_source_snippets(
            in_scope_refs, git_root=git_root, base_ref=base_ref,
            old_side_files=files_with_deletions,
        )

        # 5. Resolve output builder path
        output_builder_path = resolve_output_builder_path()

        # 6. Adjudicate the two structurally-certain out-of-scope statuses
        #    HERE, so the reconciliator obeys a computed flag instead of
        #    re-deriving scope. Findings are annotated, never removed:
        #    `reviews_by_agent` stays the faithful record of what each
        #    reviewer said, and the count travels beside it so the drop is
        #    auditable and the agent's compliance is checkable.
        stems = (
            [_review_stem(name) for name in dispatched_agents]
            if dispatched_agents is not None else None
        )
        prefiltered = annotate_prefiltered_findings(
            reviews_by_agent, scope_annotations
        )

        # Build the context object
        context: Dict[str, Any] = {
            "schema": RECONCILIATION_CONTEXT_SCHEMA,
            "reviews_by_agent": reviews_by_agent,
            "source_snippets": source_snippets,
            "scope_annotations": scope_annotations,
            "changed_files": changed_files,
            "git_range": git_range,
            "change_purpose": change_purpose,
            "pr_id": pr_id,
            "output_dir": output_dir,
            "output_builder_path": output_builder_path,
            # Host context banner — surfaced for reviewer agents to calibrate findings.
            "host_context_banner": extract_host_banner(output_dir),
            # Run-level file accounting from per-agent scope summaries —
            # None on pre-sidecar runs.
            #
            # An empty `changed_files` reaches `_unscoped_files` as the
            # unmeasured case it is — see its docstring; nothing needs
            # translating here.
            "review_accounting": aggregate_review_accounting(
                output_dir, changed_files=changed_files
            ),
            # Dispatched but silent — measured here, not left as arithmetic
            # for the reconciliator. `None` when dispatch is unknown, which
            # is a different fact from a measured empty list.
            "missing_agents": compute_missing_agents(stems, reviews_by_agent),
            # How many findings the pipeline adjudicated structurally out of
            # scope, and for whom. The reconciliator drops every finding
            # carrying `prefiltered`; this count is what makes that
            # obedience checkable.
            "prefiltered_out_of_scope": prefiltered,
        }
        # Dispatched agents, normalized to match reviews_by_agent keys
        # (e.g., "security-reviewer" → "security-review"). Present only
        # when dispatch was actually known — its absence and
        # `missing_agents: null` say the same thing, in the same run.
        if stems is not None:
            context["dispatched_agents"] = stems

        # Write to output directory
        output_path = os.path.join(output_dir, "reconciliation-context.json")
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2, ensure_ascii=False)

        # No Markdown projection is written. `reconciliation-context.md`
        # existed for exactly one reader — the reconciliator agent — and a
        # projection whose only reader is an agent is a second rendering
        # of the same data that has to be kept honest by hand. The agent
        # reads the JSON.
        result = {
            "status": "ok",
            "path": output_path,
        }
        print(json.dumps(result))
        return 0

    except Exception as exc:
        # Surface the full traceback to stderr — this failure aborts the whole
        # review at pipeline step 8, and a bare message ("got 'list'") gives no
        # clue which field of which finding is malformed. The structured stdout
        # line stays terse for the pipeline's own error handling.
        import traceback
        traceback.print_exc()
        print(f"ERROR: {exc}", file=sys.stderr)
        result = {"status": "error", "error": str(exc)}
        print(json.dumps(result))
        return 1


if __name__ == "__main__":
    sys.exit(main())

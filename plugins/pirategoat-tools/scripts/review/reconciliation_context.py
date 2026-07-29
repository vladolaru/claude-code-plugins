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
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parent

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
    "reconciliation-context.md",
    "critic-context.md",
])

# Scope statuses that are structurally certain — no line-number ambiguity.
# Pre-filtered from the Markdown context; kept in the JSON artifact.
# not_in_hunk is intentionally excluded: agent line numbers may be
# slightly off, and the ±5 proximity window doesn't catch all cases.
_PREFILTER_SCOPES = frozenset([
    "OUT_OF_SCOPE:file_not_in_diff",
    "OUT_OF_SCOPE:metadata_only",
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


def resolve_structured_severity_floor(issue: Dict[str, Any]) -> Optional[str]:
    """Return a valid explicit floor without consulting description prose."""
    structured = issue.get("severity_floor")
    if (
        isinstance(structured, str)
        and structured.lower() in _VALID_SEVERITY_FLOORS
    ):
        return structured.lower()
    return None


def resolve_severity_floor(issue: Dict[str, Any]) -> Optional[str]:
    """Resolve a structured or backward-compatible description floor."""
    structured = resolve_structured_severity_floor(issue)
    if structured is not None:
        return structured

    # Coerce first: a malformed (list/non-string) description must not silently
    # drop a mandatory floor. load_agent_findings pops severity_floor when this
    # returns None, so returning None for a list that carries a legacy marker
    # would downgrade the finding. _coerce_text joins list items on newlines,
    # keeping the MULTILINE ^Severity-floor: anchor matchable.
    description = _coerce_text(issue.get("description", ""))
    numeric = _NUMERIC_SEVERITY_FLOOR_RE.search(description)
    if numeric:
        return numeric.group(1).lower()
    legacy = _LEGACY_SEVERITY_FLOOR_RE.search(description)
    if legacy:
        return _LEGACY_SEVERITY_FLOORS[legacy.group(1).lower()]
    return None


def _strip_critic_severity_floor_markers(text: Any) -> str:
    """Remove prose floor markers before content reaches the critic.

    Accepts any type and coerces first — like ``_escape_backtick_runs``, this
    is a regex chokepoint that free-form (model-authored) finding text flows
    through, so a non-string value must not raise here.
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
    """Map an agent name to its review-file stem.

    Review files are named derive_reviewer_name(agent) + "-review.json":
    only a TRAILING "-reviewer" becomes "-review". A blanket replace()
    would corrupt names carrying "reviewer" mid-string (adapter instances
    are "repo-<id>-reviewer", and <id> is repo-authored — e.g.
    "api-reviewer-v2" yields "repo-api-reviewer-v2-reviewer", whose stem
    is "repo-api-reviewer-v2-review").
    """
    if agent.endswith("-reviewer"):
        return f"{agent[: -len('-reviewer')]}-review"
    return agent


def _load_agent_unreviewed(output_dir: str, agent: str) -> Optional[List[str]]:
    """Read one agent's declared-unreviewed paths from its review JSON.

    Returns None when the agent produced no parseable output OR its
    unreviewed field is malformed (non-null, non-list) — either way it can
    claim nothing. Returns the list of declared paths (possibly empty)
    otherwise; canonical null and an absent key mean "declared nothing".
    """
    path = os.path.join(output_dir, f"{_review_stem(agent)}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    unreviewed = data.get("unreviewed")
    if unreviewed is None:
        # Canonical "no declarations": the builder serializes null when
        # nothing was declared (absent predates the field). Output without
        # declarations claims full review per the budget contract.
        return []
    if not isinstance(unreviewed, list):
        # Malformed field: what the agent meant to declare is unknowable,
        # so it can claim nothing — same as unparseable output. Coercing
        # to [] would invert genuine gaps into deferred-but-reviewed
        # claims and erase them from files_never_inline.
        return None
    # Normalize declarations to the canonical repo-relative form the scope
    # sidecars use — "./src/x.php" and "src\\x.php" must match "src/x.php",
    # or an explicit declaration silently inverts into a
    # deferred-but-reviewed claim. A malformed entry fails the whole list
    # closed for the same reason the malformed field does: silently
    # dropping it could leave [] — a claim that every deferred file was
    # reviewed — where the agent tried to declare a gap.
    cleaned = []
    for item in unreviewed:
        if not isinstance(item, str) or not item.strip():
            return None
        cleaned.append(posixpath.normpath(item.strip().replace("\\", "/")))
    return cleaned


def aggregate_inline_coverage(output_dir: str) -> Optional[Dict[str, Any]]:
    """Aggregate per-agent scope summaries into run-level inline coverage.

    Reads ``*-scope-summary*.json`` sidecars written by bootstrap/scope.py,
    then reconciles them with each agent's review output. Budget-skipped
    (NOT DIFFED) files are the agent's deferred work queue: the budget
    contract requires each one to be reviewed or declared via
    ``builder.add_unreviewed()``, so an agent that produced output and did
    NOT declare a deferred file claims to have reviewed it.

    A file is a coverage gap (``files_never_inline``) only when NO agent
    received its diff inline AND no deferring agent claims to have reviewed
    it. Claimed files surface separately in ``files_deferred_reviewed``
    (an agent claim, not proof of read), and explicit declarations in
    ``files_declared_unreviewed`` so warnings can name genuine omissions.

    Returns None when no summaries exist (pre-sidecar runs) so callers can
    distinguish "no data" from "no gaps".
    """
    inline: Dict[str, set] = {}
    skipped: Dict[str, set] = {}
    deferred_by_agent: Dict[str, set] = {}
    agents_reporting = 0
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
        if not isinstance(data, dict):
            continue
        agents_reporting += 1
        for f_path in data.get("files_with_diffs") or []:
            if isinstance(f_path, str):
                inline.setdefault(f_path, set()).add(agent)
        for f_path in data.get("budget_exceeded_files") or []:
            if isinstance(f_path, str):
                skipped.setdefault(f_path, set()).add(agent)
                deferred_by_agent.setdefault(agent, set()).add(f_path)
    if not agents_reporting:
        return None

    never_inline = {f: a for f, a in skipped.items() if f not in inline}
    unreviewed_by_agent: Dict[str, Optional[List[str]]] = {}
    claimed: Dict[str, set] = {}
    declared: Dict[str, set] = {}
    for f_path, agents in never_inline.items():
        for agent in agents:
            if agent not in unreviewed_by_agent:
                declared_list = _load_agent_unreviewed(output_dir, agent)
                # Consumer-side mirror of the builder's deferred-set
                # verification: output that bypassed the builder (or a
                # failed sidecar write) can declare any string, and a
                # declaration outside the agent's own deferred set — typo,
                # absolute path, wrong root — matches nothing, flipping
                # every real deferred file to deferred-but-reviewed. An
                # out-of-set entry proves the list unreliable: fail it
                # closed, the agent can claim nothing.
                if declared_list is not None and not set(
                    declared_list
                ) <= deferred_by_agent.get(agent, set()):
                    declared_list = None
                unreviewed_by_agent[agent] = declared_list
            agent_unreviewed = unreviewed_by_agent[agent]
            if agent_unreviewed is None:
                continue  # no output — the agent can neither claim nor declare
            if f_path in agent_unreviewed:
                declared.setdefault(f_path, set()).add(agent)
            else:
                claimed.setdefault(f_path, set()).add(agent)

    return {
        # Counts summary FILES aggregated (primary + secondary domains),
        # not unique agents.
        "agents_reporting": agents_reporting,
        "files_inline": {f: sorted(a) for f, a in sorted(inline.items())},
        "files_never_inline": {
            f: sorted(a)
            for f, a in sorted(never_inline.items())
            if f not in claimed
        },
        "files_deferred_reviewed": {
            f: sorted(a) for f, a in sorted(claimed.items())
        },
        "files_declared_unreviewed": {
            f: sorted(a) for f, a in sorted(declared.items())
        },
    }


def load_agent_findings(
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
            (backward-compatible default).

    Returns:
        Dict keyed by agent name (e.g., "security-review") with the parsed
        JSON content as the value.
    """
    findings = {}
    output_path = Path(output_dir)

    if not output_path.is_dir():
        print(f"WARNING: output directory does not exist: {output_dir}", file=sys.stderr)
        return findings

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
            data = json.loads(entry.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                print(
                    f"WARNING: skipping malformed file {entry.name}: "
                    "top-level JSON must be an object",
                    file=sys.stderr,
                )
                continue
            issues = data.get("issues", [])
            if isinstance(issues, list):
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    floor = resolve_severity_floor(issue)
                    if floor is None:
                        issue.pop("severity_floor", None)
                    else:
                        issue["severity_floor"] = floor
            # Key by filename without .json extension (e.g., "security-review")
            agent_name = entry.stem
            findings[agent_name] = data
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: skipping malformed file {entry.name}: {exc}", file=sys.stderr)

    return findings


def extract_references(agent_findings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract unique file:line references from all agent issues.

    Scans the 'issues' list in each agent's findings and collects unique
    file paths with their referenced line numbers.

    Returns:
        List of dicts, each with:
        - "file": str — the file path as reported by the agent
        - "lines": List[int] — sorted, deduplicated line numbers

        Skips issues without a valid integer 'line' field.
    """
    file_lines: Dict[str, set] = {}

    for _agent_name, data in agent_findings.items():
        issues = data.get("issues", [])
        if not isinstance(issues, list):
            continue

        for issue in issues:
            if not isinstance(issue, dict):
                continue
            file_path = issue.get("file")
            line = issue.get("line")

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

    This prevents pre-existing issues on untouched lines of a touched file
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


def _markdown_fence_for(text: str) -> str:
    """Return a Markdown fence longer than any backtick run in text."""
    max_run = 0
    run = 0
    for ch in text:
        if ch == "`":
            run += 1
            if run > max_run:
                max_run = run
        else:
            run = 0
    return "`" * max(3, max_run + 1)


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


def _escape_backtick_runs(text: Any) -> str:
    """Neutralize backtick runs of 3+ in free-form text.

    Markdown interprets ````` as a code fence opener.  When agent-written
    issue descriptions or recommendations contain fenced code samples, the
    raw backticks corrupt the surrounding document structure.  This helper
    inserts a zero-width space after the second backtick in any run of 3+,
    breaking the fence pattern while keeping the text visually identical
    for the LLM consumer.

    Accepts any type and coerces it to a string first (via ``_coerce_text``):
    this is the single chokepoint every free-form finding field flows through
    before rendering, so coercing here crash-proofs both ``to_markdown`` and
    ``build_critic_context`` against non-string reviewer output.
    """
    import re
    text = _coerce_text(text)
    return re.sub(r"(`{3,})", lambda m: m.group(0)[:2] + "\u200b" + m.group(0)[2:], text)


def _escape_inline(text: Any) -> str:
    """Render a value that must stay on a single line (e.g. a finding title).

    Titles are rendered inline (``**N. <title>**``, ``### F1: <title>``) and \u2014
    unlike descriptions/recommendations \u2014 do NOT pass through
    ``_escape_block_syntax``. A line break in the title would therefore let a
    line-leading ``## \u2026`` or ``---`` forge a heading or thematic break and
    split/spoof the structured context. Coerce, neutralize backtick fences,
    then collapse *all* whitespace runs to single spaces (matching the
    producer) so no line ending survives \u2014 CommonMark treats bare CR and CRLF
    as line endings too, so replacing only LF would leave a CR-delimited title
    able to inject block-level Markdown. Producer-side coercion keeps titles
    single-line already; this is the defensive last line at the render boundary.
    """
    return " ".join(_escape_backtick_runs(text).split())


def _escape_block_syntax(text: str) -> str:
    """Escape Markdown block-level syntax in free-form agent text.

    When agent-written descriptions or recommendations are embedded as list
    item continuations, CommonMark still recognises ATX headings (``## …``)
    and thematic breaks (``---``) with up to 3 leading spaces of indentation.
    Increasing the indent cannot help — inside a list item the prefix is
    stripped before block parsing, so the relative indent stays the same.

    This backslash-escapes the triggering characters so CommonMark renders
    them as literals instead of structural elements.
    """
    import re

    lines = text.split("\n")
    escaped = []
    for line in lines:
        stripped = line.lstrip()
        # ATX headings: #{1,6} followed by space or end-of-line
        if re.match(r"^#{1,6}(\s|$)", stripped):
            idx = line.index("#")
            line = line[:idx] + "\\" + line[idx:]
        # Thematic breaks: 3+ of the same -, *, or _ (with optional spaces)
        elif re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", line):
            m = re.search(r"[-*_]", line)
            if m:
                line = line[:m.start()] + "\\" + line[m.start():]
        # Setext heading underlines: =+ (--- already handled above)
        elif re.match(r"^\s{0,3}=+\s*$", line):
            idx = line.index("=")
            line = line[:idx] + "\\" + line[idx:]
        # Block quotes
        elif stripped.startswith(">"):
            idx = line.index(">")
            line = line[:idx] + "\\" + line[idx:]
        escaped.append(line)
    return "\n".join(escaped)


def to_markdown(context: Dict[str, Any]) -> str:
    """Convert a reconciliation context dict to structured Markdown.

    Produces a human-readable (and LLM-efficient) Markdown document with
    sections for metadata, change purpose, agent findings, source snippets,
    and scope annotations. Designed as a drop-in alternative to the JSON
    format for the reconciliator agent, which processes Markdown ~40% more
    token-efficiently than JSON.

    Args:
        context: The reconciliation context dict (same structure as written
            to ``reconciliation-context.json`` by ``main()``).

    Returns:
        A Markdown string with ``---`` separators between major sections.
    """
    parts: List[str] = []

    # --- Host Context Banner (prepended when degraded) ---
    host_banner = context.get("host_context_banner")
    if host_banner and isinstance(host_banner, dict) and host_banner.get("degraded"):
        message = host_banner.get("message", "Host context degraded.")
        banner_json = json.dumps(
            {"host_context_banner": host_banner},
            indent=2,
            sort_keys=True,
        )
        fence = _markdown_fence_for(banner_json)
        parts.append(
            f"> **⚠ Host Context Banner:** {message}\n"
            ">\n"
            "> Reviewers scoped their findings under this banner; treat it as a qualifier"
            " on claims that depend on unresolved upstream hosts.\n"
            "\n"
            "Full banner object for `review-findings.json` passthrough:\n"
            "\n"
            f"{fence}json\n"
            f"{banner_json}\n"
            f"{fence}\n"
        )

    # --- Inline Diff Coverage Gaps (prepended — must not be buried) ---
    inline_coverage = context.get("inline_coverage")
    if isinstance(inline_coverage, dict) and inline_coverage.get("files_never_inline"):
        gaps = inline_coverage["files_never_inline"]
        declared = inline_coverage.get("files_declared_unreviewed") or {}
        parts.append("## Inline Diff Coverage Gaps\n")
        parts.append(
            f"**⚠ {len(gaps)} changed file(s) matched reviewer domains but "
            "NO reviewer received their diff inline or reported reviewing "
            "them from the deferred NOT DIFFED queue.** Findings cannot "
            "exist for code no agent saw — treat agent verdicts as NOT "
            "covering these files, and carry this list into "
            "`review-findings.md` as a coverage warning.\n"
        )
        for f_path, agents in gaps.items():
            declaring = declared.get(f_path) if isinstance(declared, dict) else None
            note = (
                f"; declared unreviewed (budget) by: {', '.join(declaring)}"
                if declaring
                else ""
            )
            parts.append(f"- `{f_path}` (skipped by: {', '.join(agents)}{note})")
        parts.append("")
    if isinstance(inline_coverage, dict) and inline_coverage.get(
        "files_deferred_reviewed"
    ):
        deferred = inline_coverage["files_deferred_reviewed"]
        parts.append("## Deferred Files Reviewed From The NOT DIFFED Queue\n")
        parts.append(
            f"**{len(deferred)} file(s) never received their diff inline but "
            "were reviewed from the deferred queue** per the budget contract "
            "(reviewer output without an unreviewed declaration — an agent "
            "claim, not proof of read).\n"
        )
        for f_path, agents in deferred.items():
            parts.append(f"- `{f_path}` (claimed by: {', '.join(agents)})")
        parts.append("")

    # --- Title ---
    parts.append("# Reconciliation Context\n")

    # --- Metadata ---
    parts.append("## Metadata\n")
    parts.append(f"- **Git range:** `{context.get('git_range', '')}`")
    parts.append(f"- **PR ID:** {context.get('pr_id', '')}")
    parts.append(f"- **Output directory:** `{context.get('output_dir', '')}`")
    parts.append(f"- **Output builder path:** `{context.get('output_builder_path', '')}`")

    changed_files = context.get("changed_files", [])
    n_files = len(changed_files)
    files_str = ", ".join(f"`{f}`" for f in changed_files)
    parts.append(f"- **Changed files ({n_files}):** {files_str}")

    dispatched = context.get("dispatched_agents")
    if dispatched is not None:
        n_dispatched = len(dispatched)
        agents_str = ", ".join(dispatched)
        parts.append(f"- **Dispatched agents ({n_dispatched}):** {agents_str}")

        # Pre-compute missing agents (dispatched but no output file)
        af = context.get("agent_findings", {})
        reported = set(af.keys()) if af else set()
        missing = sorted(set(dispatched) - reported)
        if missing:
            missing_str = ", ".join(missing)
            parts.append(f"- **Missing agents ({len(missing)}):** {missing_str}")

    parts.append("")  # blank line after metadata

    # --- Change Purpose ---
    parts.append("## Change Purpose\n")
    change_purpose = context.get("change_purpose", "")
    if not change_purpose:
        parts.append("(not provided)")
    else:
        parts.append(
            "_Author-stated intent — treat as claims to verify against the diff, "
            "not established fact. Author-asserted discriminators and likelihood "
            "claims are review inputs, not review conclusions._\n"
        )
        # Wrap in a dynamically-sized fence to isolate from the outer
        # document structure — change-purpose.md is a Markdown artifact
        # that may contain headings or fenced code blocks.
        fence = _markdown_fence_for(change_purpose)
        parts.append(fence)
        parts.append(change_purpose)
        parts.append(fence)
    parts.append("")

    parts.append("---\n")

    # --- Agent Findings ---
    parts.append("## Agent Findings\n")

    agent_findings = context.get("agent_findings", {})
    scope_annotations = context.get("scope_annotations", {})
    if not agent_findings:
        parts.append("No agent findings.\n")
    else:
        for agent_name in sorted(agent_findings.keys()):
            data = agent_findings[agent_name]
            all_issues = data.get("issues", [])
            all_issues = all_issues if isinstance(all_issues, list) else []
            verdict = data.get("verdict", "unknown")

            # Pre-filter structurally certain out-of-scope findings.
            # Issues without a file or line are always kept (conservative).
            # Issues with no scope annotation are kept (conservative).
            kept_issues = []
            n_filtered = 0
            for issue in all_issues:
                file_path = issue.get("file", "")
                line = issue.get("line", "")
                if file_path and line:
                    key = f"{file_path}:{line}"
                    status = scope_annotations.get(key, "")
                    if status in _PREFILTER_SCOPES:
                        n_filtered += 1
                        continue
                kept_issues.append(issue)

            n_kept = len(kept_issues)

            parts.append(f"### {agent_name}\n")
            if n_filtered > 0:
                parts.append(
                    f"**{n_kept} issues ({n_filtered} pre-filtered as out-of-scope), verdict: {verdict}**\n"
                )
            else:
                parts.append(f"**{n_kept} issues, verdict: {verdict}**\n")

            # Skip reason for not_applicable agents
            skip_reason = data.get("skip_reason")
            if skip_reason:
                parts.append(f"**Skipped:** {skip_reason}\n")

            # Render kept issues with contiguous numbering
            for idx, issue in enumerate(kept_issues, 1):
                title = issue.get("title", "Untitled")
                severity = issue.get("severity", "unknown")
                confidence = issue.get("confidence", "")
                file_path = issue.get("file", "")
                line = issue.get("line", "")
                category = issue.get("category", "")
                severity_floor = resolve_severity_floor(issue)
                channel = issue.get("channel", "")
                description = issue.get("description", "")
                recommendation = issue.get("recommendation", "")

                conf_str = f", confidence: {confidence}" if confidence else ""
                parts.append(f"**{idx}. {_escape_inline(title)}** [{severity}{conf_str}]")
                if file_path:
                    loc = f"`{file_path}:{line}`" if line else f"`{file_path}`"
                    parts.append(f"- File: {loc}")
                if category:
                    parts.append(f"- Category: {category}")
                if severity_floor:
                    parts.append(f"- Severity floor: {severity_floor}")
                if channel == "advisory":
                    parts.append("- Channel: advisory (non-gating — preserve on merge)")
                if description:
                    desc = _escape_backtick_runs(description)
                    desc = _escape_block_syntax(desc)
                    # Indent continuation lines so multiline text stays
                    # inside the list item instead of spilling into top-level Markdown.
                    desc = desc.replace("\n", "\n  ")
                    parts.append(f"- Description: {desc}")
                if recommendation:
                    rec = _escape_backtick_runs(recommendation)
                    rec = _escape_block_syntax(rec)
                    rec = rec.replace("\n", "\n  ")
                    parts.append(f"- Recommendation: {rec}")
                parts.append("")  # blank line between issues

            # NOTE: Observations are intentionally excluded from the
            # Markdown context. They bypass extract_references(),
            # check_scope(), and read_source_snippets(), so including them
            # would give the reconciliator unverified file-level claims
            # with no scope annotation or source evidence. They remain in
            # the JSON artifact for debugging/tooling.

            # Recommendations (immediate / important / suggestions)
            recommendations = data.get("recommendations")
            if recommendations and isinstance(recommendations, dict):
                has_any = any(
                    isinstance(v, list) and len(v) > 0
                    for v in recommendations.values()
                )
                if has_any:
                    parts.append("**Recommendations:**")
                    for priority in ("immediate", "important", "suggestions"):
                        items = recommendations.get(priority, [])
                        if isinstance(items, list):
                            for item in items:
                                escaped = _escape_backtick_runs(item)
                                escaped = _escape_block_syntax(escaped)
                                escaped = escaped.replace("\n", "\n  ")
                                parts.append(f"- [{priority}] {escaped}")
                    parts.append("")

            # Clearances — structured absence claims ("nothing depends on
            # this") recorded via add_clearance() WITH their verification
            # method. Unlike positives, these are deliberately included:
            # a clearance that contradicts another agent's finding is a
            # conflict the reconciliator must see and resolve by verifying
            # (never by counting), and the stated method is what lets it
            # judge the claim's coverage. (The 2026-07-16 run's three wrong
            # clears were invisible here — they lived in excluded positives.)
            clearances = data.get("clearances")
            if clearances and isinstance(clearances, list):
                parts.append(
                    "**Clearances (absence claims — judge by their method, "
                    "see Verification-Method Weighting):**"
                )
                for clearance in clearances:
                    if not isinstance(clearance, dict):
                        continue
                    claim = _escape_block_syntax(
                        _escape_backtick_runs(str(clearance.get("claim", "")))
                    ).replace("\n", "\n  ")
                    method = _escape_block_syntax(
                        _escape_backtick_runs(str(clearance.get("method", "")))
                    ).replace("\n", "\n  ")
                    parts.append(f"- {claim}")
                    parts.append(f"  - Method: {method}")
                    evidence = clearance.get("evidence")
                    if evidence:
                        ev = _escape_block_syntax(
                            _escape_backtick_runs(str(evidence))
                        ).replace("\n", "\n  ")
                        parts.append(f"  - Evidence: {ev}")
                parts.append("")

            # NOTE: Positive observations are intentionally excluded from
            # the Markdown context.  Like observations, they bypass
            # extract_references(), check_scope(), and read_source_snippets()
            # — unsupported positives (e.g., from not_applicable exits)
            # can skew dedupe/severity decisions without evidence.  They
            # remain in the JSON artifact for debugging/tooling.

    parts.append("---\n")

    # --- Source Snippets ---
    parts.append("## Source Snippets\n")

    source_snippets = context.get("source_snippets", {})
    if not source_snippets:
        parts.append("No source snippets.\n")
    else:
        for file_key in sorted(source_snippets.keys()):
            snippet = source_snippets[file_key]
            parts.append(f"### `{file_key}`\n")
            # Use a fence longer than any backtick run in the snippet
            # to avoid closing the fence early on source containing ```
            fence = _markdown_fence_for(snippet)
            parts.append(fence)
            parts.append(snippet)
            parts.append(fence + "\n")

    parts.append("---\n")

    # --- Scope Annotations ---
    parts.append("## Scope Annotations\n")

    scope_annotations = context.get("scope_annotations", {})
    # Exclude entries already pre-filtered from agent findings
    rendered = {
        k: v for k, v in scope_annotations.items()
        if v not in _PREFILTER_SCOPES
    }
    if not rendered:
        parts.append("No scope annotations.\n")
    else:
        parts.append("| File:Line | Status |")
        parts.append("|-----------|--------|")
        for key in sorted(rendered.keys()):
            status = rendered[key]
            parts.append(f"| `{key}` | {status} |")
        parts.append("")

    return "\n".join(parts)


def build_critic_context(report_text: str, findings: Dict[str, Any]) -> str:
    """Build a curated Markdown document for the decision critic.

    Combines the narrative review report and structured findings into a
    single Markdown document with stable sequential IDs (F1, F2, ...),
    prioritized recommendations, and reconciliation metrics.  This is
    ~40% more token-efficient than passing the raw JSON to the Opus
    critic, and embeds cross-referenceable IDs the report alone lacks.

    Args:
        report_text: Contents of review-report.md (narrative review).
        findings: Parsed contents of review-findings.json (structured
            output from the reconciliator, using ReviewOutputBuilder
            format).

    Returns:
        A Markdown string suitable for writing to critic-context.md.
    """
    parts: List[str] = []

    # --- Title ---
    parts.append("# Critic Context\n")

    # --- Review Report ---
    parts.append("## Review Report\n")

    verdict = findings.get("verdict", "unknown")
    parts.append(f"**Verdict:** {verdict.upper()}\n")

    # Fence the report with a dynamic fence to avoid collisions with
    # backtick runs inside the report itself.
    report_text = _strip_critic_severity_floor_markers(report_text)
    fence = _markdown_fence_for(report_text)
    parts.append(fence)
    parts.append(report_text)
    parts.append(fence)
    parts.append("")

    parts.append("---\n")

    # --- Structured Findings ---
    parts.append("## Structured Findings\n")

    issues = findings.get("issues", [])
    issues = issues if isinstance(issues, list) else []
    n_issues = len(issues)

    summary = findings.get("summary", {})
    by_severity = summary.get("by_severity", {})

    # Build severity breakdown string (only non-zero counts)
    severity_parts = []
    for sev in ("critical", "high", "medium", "low", "info"):
        count = by_severity.get(sev, 0)
        if count > 0:
            severity_parts.append(f"{count} {sev}")
    severity_str = ", ".join(severity_parts) if severity_parts else "none"

    parts.append(f"**{n_issues} findings** ({severity_str})\n")

    # Render each issue with sequential F-IDs
    for idx, issue in enumerate(issues, 1):
        title = issue.get("title", "Untitled")
        severity = issue.get("severity", "unknown")
        confidence = issue.get("confidence", "")
        file_path = issue.get("file", "")
        line = issue.get("line", "")
        category = issue.get("category", "")
        severity_floor = resolve_structured_severity_floor(issue)
        description = issue.get("description", "")
        recommendation = issue.get("recommendation", "")

        conf_str = f", confidence: {confidence}" if confidence else ""
        title = _strip_critic_severity_floor_markers(title)
        parts.append(
            f"### F{idx}: {_escape_inline(title)} [{severity}{conf_str}]"
        )

        if file_path:
            loc = f"`{file_path}:{line}`" if line else f"`{file_path}`"
            parts.append(f"- **File:** {loc}")
        if category:
            parts.append(f"- **Category:** {category}")
        if severity_floor:
            parts.append(f"- **Severity floor:** {severity_floor}")
        if description:
            description = _strip_critic_severity_floor_markers(description)
            desc = _escape_backtick_runs(description)
            desc = _escape_block_syntax(desc)
            desc = desc.replace("\n", "\n  ")
            parts.append(f"- **Description:** {desc}")
        if recommendation:
            recommendation = _strip_critic_severity_floor_markers(
                recommendation
            )
            rec = _escape_backtick_runs(recommendation)
            rec = _escape_block_syntax(rec)
            rec = rec.replace("\n", "\n  ")
            parts.append(f"- **Recommendation:** {rec}")
        parts.append("")  # blank line between issues

    # --- Prioritized Recommendations ---
    recommendations = findings.get("recommendations")
    if recommendations and isinstance(recommendations, dict):
        has_any = any(
            isinstance(v, list) and len(v) > 0
            for v in recommendations.values()
        )
        if has_any:
            parts.append("### Prioritized Recommendations\n")
            for priority in ("immediate", "important", "suggestions"):
                items = recommendations.get(priority, [])
                if isinstance(items, list):
                    for item in items:
                        item = _strip_critic_severity_floor_markers(item)
                        escaped = _escape_backtick_runs(item)
                        escaped = _escape_block_syntax(escaped)
                        escaped = escaped.replace("\n", "\n  ")
                        parts.append(f"- [{priority}] {escaped}")
            parts.append("")

    parts.append("---\n")

    # --- Reconciliation Metrics ---
    parts.append("## Reconciliation Metrics\n")

    meta = findings.get("meta", {})
    recon = meta.get("reconciliation", {})

    input_count = recon.get("input_findings_count", 0)
    agents_contributing = recon.get("agents_contributing", 0)
    verified = recon.get("verified_concerns", 0)
    merge_ratio = recon.get("merge_ratio", 0.0)
    merge_pct = int(round(merge_ratio * 100))
    false_pos = recon.get("false_positives_dropped", 0)
    out_of_scope = recon.get("out_of_scope_dropped", 0)
    reviewing = recon.get("reviewing_agents", [])
    missing = recon.get("missing_agents", [])
    not_applicable = recon.get("not_applicable_agents", [])

    parts.append(
        f"- **Pipeline:** {input_count} findings from {agents_contributing} agents "
        f"\u2192 {verified} verified concerns ({merge_pct}% merge ratio)"
    )
    parts.append(
        f"- **Dropped:** {false_pos} false positives, {out_of_scope} out-of-scope"
    )

    if reviewing:
        parts.append(f"- **Reviewing agents:** {', '.join(reviewing)}")
    if missing:
        parts.append(f"- **Missing agents:** {', '.join(missing)}")
    if not_applicable:
        na_strs = []
        for entry in not_applicable:
            if isinstance(entry, dict):
                name = entry.get("name", "unknown")
                reason = entry.get("skip_reason", "")
                na_strs.append(f"{name} ({reason})" if reason else name)
            else:
                na_strs.append(str(entry))
        parts.append(f"- **Not applicable:** {', '.join(na_strs)}")

    parts.append("")

    return "\n".join(parts)


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
        agent_findings = load_agent_findings(output_dir, dispatched_agents)

        # 2. Extract file:line references
        references = extract_references(agent_findings)

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

        # Build the context object
        context: Dict[str, Any] = {
            "agent_findings": agent_findings,
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
            # Run-level inline coverage from per-agent scope summaries —
            # None on pre-sidecar runs.
            "inline_coverage": aggregate_inline_coverage(output_dir),
        }
        # Include dispatched agents, normalized to match agent_findings keys
        # (e.g., "security-reviewer" → "security-review") so the
        # reconciliator can do a direct set comparison to detect agents
        # that were dispatched but failed to produce output.
        if dispatched_agents is not None:
            context["dispatched_agents"] = [
                _review_stem(name) for name in dispatched_agents
            ]

        # Write to output directory
        output_path = os.path.join(output_dir, "reconciliation-context.json")
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2, ensure_ascii=False)

        # Write Markdown version for LLM consumption
        md_path = os.path.join(output_dir, "reconciliation-context.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(to_markdown(context))

        # Print success status
        result = {"status": "ok", "path": output_path, "markdown_path": md_path}
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

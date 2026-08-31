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
    from .run_paths import REVIEWERS_SUBDIR
    from .reviewer_names import derive_reviewer_name
    from .verdict_rules import VALID_SEVERITIES
    from .review_document import coerce_text, load_review_document
except ImportError:
    _scripts_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _scripts_parent not in sys.path:
        sys.path.insert(0, _scripts_parent)
    from review.run_paths import REVIEWERS_SUBDIR
    from review.reviewer_names import derive_reviewer_name
    from review.verdict_rules import VALID_SEVERITIES
    from review.review_document import coerce_text, load_review_document

RECONCILIATION_CONTEXT_SCHEMA = 3

_SEVERITY_FLOOR_MARKER_RE = re.compile(
    r"(?im)Severity-floor:[ \t]*"
    r"(?:" + "|".join(re.escape(v) for v in sorted(VALID_SEVERITIES))
    + r")(?!\w)[ \t]*(?:(?:[;—-])[ \t]*)?"
)


def resolve_structured_severity_floor(finding: Dict[str, Any]) -> Optional[str]:
    """Return a valid explicit floor without consulting description prose."""
    structured = finding.get("severity_floor")
    if (
        isinstance(structured, str)
        and structured.lower() in VALID_SEVERITIES
    ):
        return structured.lower()
    return None


def strip_severity_floor_markers(text: Any) -> str:
    """Remove prose floor markers before content reaches the critic.

    A ``Severity-floor:`` marker is a reviewer-to-reconciliator directive
    the reconciliator has already acted on. Prose that still carries it
    reads to the decision critic as a standing instruction not to demote —
    exactly the judgment the critic exists to make independently. The
    STRUCTURED floor still renders; only the prose restatement goes.

    Public because its caller lives elsewhere:
    ``orchestration.assemble_review_record()``. Accepts any type and
    coerces first — free-form model-authored text flows through here.
    """
    return _SEVERITY_FLOOR_MARKER_RE.sub("", coerce_text(text))


def _review_stem(agent: str) -> str:
    """Map an agent name to its review-file stem: derive_reviewer_name(agent) + "-review"."""
    return f"{derive_reviewer_name(agent)}-review"


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
    """Load reviewer JSON files from ``reviewers/<reviewer>/review.json``.

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

    # Build allowed short reviewer identities from dispatch plan agent names.
    allowed_reviewers: Optional[frozenset] = None
    if dispatched_agents is not None:
        allowed_reviewers = frozenset(
            derive_reviewer_name(name) for name in dispatched_agents
        )

    reviewers_dir = output_path / REVIEWERS_SUBDIR
    if not reviewers_dir.is_dir():
        return reviews
    for reviewer_dir in sorted(reviewers_dir.iterdir()):
        if not reviewer_dir.is_dir():
            continue
        reviewer = reviewer_dir.name
        if allowed_reviewers is not None and reviewer not in allowed_reviewers:
            continue
        entry = reviewer_dir / "review.json"
        if not entry.is_file():
            continue

        try:
            data = load_review_document(entry, reviewer)
            review_findings = data.get("findings", [])
            if isinstance(review_findings, list):
                for finding in review_findings:
                    if not isinstance(finding, dict):
                        continue
                    floor = resolve_structured_severity_floor(finding)
                    if floor is None:
                        finding.pop("severity_floor", None)
                    else:
                        finding["severity_floor"] = floor
            # Preserve the reconciliation vocabulary while taking identity
            # from the reviewer directory rather than parsing a filename.
            agent_name = f"{reviewer}-review"
            reviews[agent_name] = data
        except ValueError as exc:
            print(f"WARNING: skipping malformed file {entry}: {exc}", file=sys.stderr)

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
        "--host-banner-json", default="",
        help=(
            "The degraded-host banner as JSON, from the caller's own "
            "review-context.json. Empty means no banner applies."
        ),
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
    # The orchestrator holds review-context.json in memory when it calls
    # this script, so it passes the banner rather than making this script
    # a second reader of a file it does not own. A malformed value is the
    # caller's bug, and the traceback names it.
    host_banner = (
        json.loads(args.host_banner_json)
        if args.host_banner_json.strip() else None
    )
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
            "change_purpose": change_purpose,
            "pr_id": pr_id,
            # The degraded-host banner the caller resolved. Reviewers'
            # claims were scoped by its presence, and findings_save.py
            # stamps it onto the ledger.
            "host_context_banner": host_banner,
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

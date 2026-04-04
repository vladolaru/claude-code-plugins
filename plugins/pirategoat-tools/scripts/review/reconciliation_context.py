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
])


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
            name.replace("-reviewer", "-review") for name in dispatched_agents
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


def read_source_snippets(
    references: List[Dict[str, Any]],
    context_lines: int = 10,
    git_root: Optional[str] = None,
) -> Dict[str, str]:
    """Read source code snippets around referenced lines.

    For each referenced file, reads ±context_lines around each line number.
    Overlapping windows are merged. Missing files are skipped gracefully.

    Args:
        references: List of {"file": path, "lines": [int, ...]} dicts.
        context_lines: Number of lines of context around each reference.
        git_root: Git repository root. Relative file paths from agent
            findings are resolved against this directory. When None, falls
            back to CWD-based resolution (which breaks when the pipeline
            is launched from a subdirectory).

    Returns:
        Dict mapping original file paths to snippet text with line numbers.
        Format: "  42 | code here\\n  43 | more code\\n..."
    """
    snippets: Dict[str, str] = {}

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

        if not os.path.isfile(read_path):
            continue

        try:
            with open(read_path, "r", encoding="utf-8", errors="replace") as f:
                source_lines = f.readlines()
        except OSError:
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
            snippets[file_path] = "\n".join(snippet_parts)

    return snippets


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


def _derive_change_purpose_from_commits(git_range: str) -> str:
    """Extract a one-line-per-commit summary from the git range.

    Returns a short block of commit subjects the reconciliator can use to
    calibrate severity when no explicit change-purpose artifact exists.
    Returns empty string on failure.
    """
    try:
        result = subprocess.run(
            ["git", "log", "--format=%s", git_range],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""

    subjects = [s.strip() for s in result.stdout.strip().splitlines() if s.strip()]
    if not subjects:
        return ""
    return "Derived from commit messages:\n" + "\n".join(f"- {s}" for s in subjects)


_HUNK_HEADER_RE = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE
)

# Lines of proximity to a changed hunk that count as "near" the change.
# Matches the old reconciliator's "within 5 lines of a hunk" rule.
_HUNK_PROXIMITY = 5


def _parse_diff_hunks(git_range: str) -> Dict[str, List[Tuple[int, int]]]:
    """Parse git diff to extract changed line ranges per file.

    Runs ``git diff --unified=0 <git_range>`` and extracts the new-file line
    ranges from ``@@`` hunk headers.

    Returns:
        Dict mapping repo-relative file paths to lists of ``(start, end)``
        tuples representing changed line ranges in the new version.
        Empty dict if git diff fails or times out.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--unified=0", git_range],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {}
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}

    hunks: Dict[str, List[Tuple[int, int]]] = {}
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
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                if count == 0:
                    # Pure deletion — no new lines added, but the deletion
                    # occurred at this position in the new file.  Store as a
                    # zero-width marker so proximity matching can still
                    # classify findings near the deletion as IN_SCOPE.
                    hunks[current_file].append((start, start))
                    continue
                end = start + count - 1
                hunks[current_file].append((start, end))

    return hunks


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
    diff_hunks = _parse_diff_hunks(git_range)

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
            # File appears in the diff but has no hunk entries at all.
            # This is a defensive fallback (deletion-only hunks now produce
            # zero-width markers, so this shouldn't trigger for them).
            # Fall back to file-level IN_SCOPE to avoid false negatives.
            for line in ref_lines:
                annotations[f"{file_path}:{line}"] = "IN_SCOPE:in_hunk"
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


def resolve_output_builder_path() -> str:
    """Return the path to the ReviewOutputBuilder script.

    The script knows its own location relative to the output builder.
    """
    return str(SCRIPTS_DIR / "agent" / "output.py")


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
    if not change_purpose and args.git_range:
        change_purpose = _derive_change_purpose_from_commits(args.git_range)
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

        # 3. Read source snippets (resolve relative paths from git root)
        git_root = _get_git_root()
        source_snippets = read_source_snippets(references, git_root=git_root)

        # 4. Annotate scope
        scope_annotations = check_scope(references, changed_files, git_range)

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
        }
        # Include dispatched agents, normalized to match agent_findings keys
        # (e.g., "security-reviewer" → "security-review") so the
        # reconciliator can do a direct set comparison to detect agents
        # that were dispatched but failed to produce output.
        if dispatched_agents is not None:
            context["dispatched_agents"] = [
                name.replace("-reviewer", "-review") for name in dispatched_agents
            ]

        # Write to output directory
        output_path = os.path.join(output_dir, "reconciliation-context.json")
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2, ensure_ascii=False)

        # Print success status
        result = {"status": "ok", "path": output_path}
        print(json.dumps(result))
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        result = {"status": "error", "error": str(exc)}
        print(json.dumps(result))
        return 1


if __name__ == "__main__":
    sys.exit(main())

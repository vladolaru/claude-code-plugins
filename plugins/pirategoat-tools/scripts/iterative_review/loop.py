"""Round tracking, state management, convergence detection, max rounds."""

import json
import os
import sys
import re
from copy import deepcopy

# ---------------------------------------------------------------------------
# Max Rounds Table (diff-size-based)
# ---------------------------------------------------------------------------

_MAX_ROUNDS_TABLE = [
    (10000, 12),
    (5000, 10),
    (3000, 9),
    (2000, 8),
    (1000, 7),
    (700, 6),
    (500, 5),
    (200, 4),
]
_MAX_ROUNDS_DEFAULT = 3
MAX_ROUNDS_HARD_LIMIT = 20


def compute_max_rounds(diff_lines):
    """Compute max review rounds based on relevant diff line count."""
    for threshold, rounds in _MAX_ROUNDS_TABLE:
        if diff_lines >= threshold:
            return rounds
    return _MAX_ROUNDS_DEFAULT


# ---------------------------------------------------------------------------
# Convergence
# ---------------------------------------------------------------------------

def check_convergence(findings_count, all_p3, all_rejected, current_round, max_rounds):
    """Check if the review loop should terminate.

    Returns termination reason string, or None to continue.
    Priority: hard_limit > zero_findings > all_rejected > nitpicks_only > max_rounds.
    """
    if current_round >= MAX_ROUNDS_HARD_LIMIT:
        return "hard_limit"
    if findings_count == 0:
        return "zero_findings"
    if all_rejected:
        return "all_rejected"
    if all_p3:
        return "nitpicks_only"
    if current_round >= max_rounds:
        return "max_rounds"
    return None


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

DEFAULT_STATE = {
    "current_round": 0,
    "max_rounds": 3,
    "merge_base": None,
    "diff_lines_total": 0,
    "diff_lines_relevant": 0,
    "noise_files_excluded": 0,
    "context_file": None,
    "analysis_doc_prefix": "independent-review",
    "pass_prior_analysis": True,
    "rounds": [],
    "terminated": False,
    "termination": None,
    "autonomous": False,
    "consecutive_timeouts": 0,
}


def read_loop_state(output_dir):
    """Read review-loop-state.json, return default if missing or corrupted."""
    path = os.path.join(output_dir, "review-loop-state.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return deepcopy(DEFAULT_STATE)


def write_loop_state(output_dir, state):
    """Write review-loop-state.json."""
    path = os.path.join(output_dir, "review-loop-state.json")
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Pushback Log (severity-gated)
# ---------------------------------------------------------------------------

_PUSHBACK_SEVERITY_GATE = {"P0", "P1"}


def build_pushback_entry(outcome, finding, round_num, severity_gate=None):
    """Build a pushback log entry for a single outcome.

    Returns formatted string, or None if the outcome should not be logged
    (fixed outcomes or below severity gate).
    """
    gate = severity_gate or _PUSHBACK_SEVERITY_GATE
    action = outcome.get("action")
    severity = finding.get("severity", "unknown")

    if action == "fixed":
        return None
    if severity not in gate and severity != "unknown":
        return None

    label = action.upper()
    fid = finding.get("id", "?")
    title = finding.get("title", "?")
    location = finding.get("location", "?")
    reason_key = "reasoning" if action != "fixed" else "summary"
    reasoning = outcome.get(reason_key, outcome.get("reasoning", ""))

    return (
        f'{label}: [{fid}] [{severity}] "{title}" ({location})\n'
        f'  Reviewer reasoning: "{reasoning}"\n'
    )


def append_pushback_log(output_dir, entry_text):
    """Append text to pushback-log.md."""
    path = os.path.join(output_dir, "pushback-log.md")
    try:
        with open(path, "a") as f:
            f.write(entry_text)
    except OSError:
        pass


def read_pushback_log(output_dir):
    """Read pushback-log.md, return empty string if missing."""
    path = os.path.join(output_dir, "pushback-log.md")
    try:
        with open(path) as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return ""


# ---------------------------------------------------------------------------
# Deferred Items (JSONL)
# ---------------------------------------------------------------------------

def append_deferred_item(output_dir, item):
    """Append a deferred item to deferred-items.jsonl."""
    path = os.path.join(output_dir, "deferred-items.jsonl")
    try:
        with open(path, "a") as f:
            f.write(json.dumps(item) + "\n")
    except OSError:
        pass


def read_deferred_items(output_dir):
    """Read all deferred items from deferred-items.jsonl."""
    path = os.path.join(output_dir, "deferred-items.jsonl")
    items = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
    except (FileNotFoundError, OSError):
        pass
    return items


# ---------------------------------------------------------------------------
# Outcome Validation
# ---------------------------------------------------------------------------

def outcome_severity(outcome, finding=None):
    """Get severity for an outcome, with finding as primary source.

    Prefers finding's severity (canonical source from Codex).
    Falls back to outcome's own severity field (LLM-provided copy).
    Returns "unknown" when neither has severity data.

    Note: "unknown" intentionally prevents nitpicks_only and has_critical_fixed
    from triggering — the loop errs toward continuing when severity is unavailable.
    """
    if finding:
        sev = finding.get("severity")
        if sev:
            return sev
    return outcome.get("severity", "unknown")


def validate_outcomes(findings, outcomes):
    """Check that every finding has an outcome and no stray IDs exist.

    Returns (missing_ids, stray_ids).
    """
    finding_ids = {f["id"] for f in findings}
    outcome_ids = {o["id"] for o in outcomes}
    missing = [f["id"] for f in findings if f["id"] not in outcome_ids]
    stray = [o["id"] for o in outcomes if o["id"] not in finding_ids]
    return missing, stray


# ---------------------------------------------------------------------------
# Diff Sizing (noise-filtered)
# ---------------------------------------------------------------------------

def _import_filter_noise():
    """Import filter_noise from review/agent/scope.py."""
    scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "review_scope",
            os.path.join(scripts_dir, "review", "agent", "scope.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.filter_noise
    except Exception:
        return None

_filter_noise = _import_filter_noise()


def compute_relevant_diff_size(files):
    """Filter noise files and return (relevant_files, excluded_count).

    Uses NOISE_PATTERNS from review/agent/scope.py if available,
    falls back to a minimal built-in list.
    """
    if _filter_noise:
        relevant, excluded = _filter_noise(files)
        return relevant, len(excluded)

    # Fallback: minimal noise patterns if scope.py import fails
    _FALLBACK_NOISE = [
        r"\.(lock|png|jpg|jpeg|gif|svg|ico|woff|woff2|map)$",
        r"(package-lock\.json|pnpm-lock\.yaml|go\.sum)$",
        r"(^|/)(vendor|node_modules)/",
    ]
    relevant = []
    excluded = 0
    for f in files:
        if any(re.search(p, f) for p in _FALLBACK_NOISE):
            excluded += 1
        else:
            relevant.append(f)
    return relevant, excluded

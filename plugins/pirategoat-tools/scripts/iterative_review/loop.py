"""Round tracking, state management, convergence detection, max rounds."""

import json
import os
from copy import deepcopy

# ---------------------------------------------------------------------------
# Max Rounds Table (diff-size-based)
# ---------------------------------------------------------------------------

_MAX_ROUNDS_TABLE = [
    (10000, 10),
    (5000, 8),
    (3000, 7),
    (2000, 6),
    (1000, 5),
    (500, 4),
]
_MAX_ROUNDS_DEFAULT = 3


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
    Priority: zero_findings > all_rejected > nitpicks_only > max_rounds.
    """
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

def validate_outcomes(findings, outcomes):
    """Check that every finding has an outcome. Returns list of missing IDs."""
    outcome_ids = {o["id"] for o in outcomes}
    return [f["id"] for f in findings if f["id"] not in outcome_ids]

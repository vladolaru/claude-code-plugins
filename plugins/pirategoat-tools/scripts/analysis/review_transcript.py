#!/usr/bin/env python3
"""Privacy-preserving enrichment for review pipeline transcripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

_ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ANALYSIS_DIR)
# Sibling package in scripts/review — the poll program declares the line
# its status render always carries, so a reformat there cannot silently
# turn every contractual poll back into a recorded tool failure.
sys.path.insert(0, os.path.dirname(_ANALYSIS_DIR))
from review.agents_status import STATUS_ENVELOPE_PREFIX  # noqa: E402


_USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
# Claude Code reports context-window variants with a bracketed suffix
# (e.g. "claude-opus-5[1m]"), verified against real transcripts. Admit ONE
# optional tag of safe characters and keep it: the tag names a distinct
# variant the API actually resolved, so stripping it would misreport
# attribution. Rejecting it nulled the model for every Opus-tier dispatch.
_SAFE_MODEL = re.compile(r"^claude-[a-z0-9][a-z0-9._-]{0,119}(\[[a-z0-9._-]{1,16}\])?$")
# The harness appends its trailer as a LINE-ANCHORED
# "agentId: <id> (use SendMessage ...)" near the end of the result text
# (verified against real transcripts). Anchor to line starts and take the
# LAST match: reviewer prose preceding the trailer may mention
# "agentId: <anything>", and a first-match scan would retain that prose
# token in the privacy-reduced report and correlate the wrong transcript.
_LEGACY_AGENT_ID = re.compile(
    r"^agentId\s*:\s*((?:agent-)?[A-Za-z0-9][A-Za-z0-9._:-]*)",
    re.IGNORECASE | re.MULTILINE,
)
_FAILURE_SIGNATURES = (
    ("file has not been read yet", "write_requires_read"),
    ("sibling tool call errored", "sibling_tool_failure"),
    ("<tool_use_error>", "tool_use_error"),
    ("api error", "api_error"),
)
# Categories a call is LISTED under but never counted a fault: an
# instrument of the pipeline's own reporting its contractual non-zero
# exit. This module owns the list; `review_metrics/cohort.py` reads it to
# keep those entries out of its failure totals, so a reader still sees
# them by category without mistaking them for breakage.
EXPECTED_EXIT_CATEGORIES = frozenset({"poll_outcome"})
# `agents_status.py` exits 2 for "some agents still running or not
# dispatched" and 3 for "--wait expired" by contract (its docstring, which
# the step-7 briefing and the Codex adapter read); 0 is ALL_DONE and 1 is
# a real error. The harness marks EVERY non-zero Bash exit `is_error`, so
# before this every poll in a waiting window was recorded as a tool
# failure — 23 of the 31 failures the two 2026-09-10 field runs recorded.
# The name and the code alone do not settle it: argparse and the Python
# launcher both answer a broken invocation of this same program with 2,
# so the exemption also requires the status render itself.
_POLL_PROGRAM = "agents_status.py"
_POLL_OUTCOME_EXIT_CODES = frozenset({2, 3})
_PYTHON_PROGRAMS = frozenset({"python", "python3"})
# The harness frames a failed Bash result as `Exit code <n>` on the FIRST
# line, ahead of the command's own output, and gives it a plain-string
# `toolUseResult`; no recorded transcript carries a structured exit code
# for Bash. Reading line one is reading the harness's framing, never the
# command's stdout — and only for a result the harness already flagged.
_HARNESS_EXIT_LINE = re.compile(r"Exit code (\d{1,3})")
_SAFE_TOOL_NAMES = {
    "Agent",
    "Task",
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
}
_SHELL_OPERATORS = {";", "&", "&&", "|", "||", "<", ">", "<<", ">>"}
_UNRESOLVED_PATH = re.compile(r"[$`*?\[\]{}]")
# The canonical one-shot builder envelope bootstrap mandates: these
# assignments, in any order, before `python3 <<PY` on the first line. The
# four required names are the envelope's stable identity — every generation
# of bootstrap has emitted all of them. The optional names are historical
# measurement only: transcripts are immutable, and a reader that stops
# recognizing a recorded generation reports a measured false for a save that
# demonstrably happened.
_BUILDER_ENV_REQUIRED = frozenset({
    "PIRATEGOAT_PLUGIN_ROOT",
    "PIRATEGOAT_OUTPUT_DIR",
    "PIRATEGOAT_REVIEWER_NAME",
    "PIRATEGOAT_PR_ID",
})
_BUILDER_ENV_OPTIONAL = frozenset({
    "PIRATEGOAT_PLUGIN_VERSION",
    "PIRATEGOAT_REVIEW_BUDGET",
})
_BUILDER_ENV_NAMES = _BUILDER_ENV_REQUIRED | _BUILDER_ENV_OPTIONAL
# A shell variable assignment, as opposed to a token that merely contains "=".
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Both current and legacy names of the subagent dispatch tool. Dispatch
# anomalies (dangling, malformed, duplicated calls) are the correlation
# machinery's domain — every unresolved-evidence carve-out must exempt both.
_DISPATCH_TOOL_NAMES = frozenset({"Agent", "Task"})
# The ONLY tools whose results this module mines evidence from, and
# therefore the only ones whose result payload has to be understood rather
# than merely paired. Anchored to what the module actually reads:
# `_tool_shape_succeeded` validates Read/Write/Edit/Grep/Glob payload SHAPE
# (their result text is arbitrary content — file bodies, matched lines,
# filenames — so signature-scanning it would flip successes into failures),
# `_operation` gives Read/Write/Edit/Bash a typed operation and target that
# the failure/recovery taxonomy keys on, `observed_reads` is built from
# successful Read and Bash calls, and `artifact_writes` counts Bash builder
# heredocs. Every other tool — WebSearch, WebFetch, MCP tools, Agent/Task —
# contributes nothing to any of those measurements.
_EVIDENCE_TOOL_NAMES = frozenset(
    {"Read", "Write", "Edit", "Grep", "Glob", "Bash"}
)
_NON_SCOPE_COMPARABLE_AGENTS = frozenset(
    {"review-reconciliator", "decision-reviewer", "critic"}
)
# Regular reviewers with no registry domain: they discover their own scope
# (mutation testing), so their reads have no in/out-of-scope partition to
# compare against — but they remain regular reviewers for builder metrics
# and the regular evidence-completeness family.
_SCOPE_EXEMPT_REVIEWERS = frozenset({"tests-mutation-reviewer"})
# Producer-defined identity shape for repo-contributed reviewer instances:
# plan_dispatch names every synthetic adapter dispatch f"repo-{id}-reviewer"
# with a lowercase-ASCII-kebab id (review_config._valid_id — the same
# producer agent-name contract telemetry and the metrics sanitizers
# enforce), and the "-reviewer" suffix is load-bearing. Instances are
# dynamic, so they can never appear in the static registry set —
# recognition is by this shape. The template "repo-reviewer-adapter"
# itself never acts as a reviewer.
_REPO_REVIEWER_INSTANCE_RE = re.compile(r"repo-[a-z0-9-]+-reviewer")


def _is_recognized_reviewer(name: str, recognized_agents: set[str]) -> bool:
    """Registry/synthesis identity, or a valid repo-reviewer instance."""
    return name in recognized_agents or bool(
        _REPO_REVIEWER_INSTANCE_RE.fullmatch(name)
    )


# The reads partition routes by THIS set, not by synthesis identity alone:
# scope-exempt reviewers' self-discovered reads land in the
# non-scope-comparable bucket. Read-family completeness must use the same
# set as the routing, or a damaged scope-exempt transcript degrades the
# scope-comparable family while its own bucket reports complete.
_NON_SCOPE_COMPARABLE_READ_AGENTS = (
    _NON_SCOPE_COMPARABLE_AGENTS | _SCOPE_EXEMPT_REVIEWERS
)
_OBSERVED_READS_SCHEMA = 2


def _read_jsonl(path: str | Path) -> tuple[list[dict[str, Any]], bool]:
    """Read object-valued JSONL records and report damaged lines."""
    entries: list[dict[str, Any]] = []
    parse_gap = False
    try:
        with Path(path).open("rb") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    parse_gap = True
                    continue
                if isinstance(value, dict):
                    entries.append(value)
                else:
                    parse_gap = True
    except OSError:
        parse_gap = True
    return entries, parse_gap


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield object-valued JSONL records, skipping damaged lines."""
    yield from _read_jsonl(path)[0]


def _aware_timestamp(value: object) -> datetime | None:
    """Parse one timezone-aware ISO timestamp into UTC.

    Claude Code writes "Z"-suffixed timestamps, which fromisoformat() only
    accepts from Python 3.11 — normalize like the metrics contract parser
    so 3.10 does not discard every timestamped record as a gap.

    Keep byte-for-byte aligned with review_metrics.contracts._parse_time —
    this standalone module cannot import that package, so the two bodies
    are mirrored deliberately. A divergence makes the same boundary
    timestamp valid evidence in one module and a gap in the other.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _run_window(
    manifest: dict[str, Any],
) -> tuple[datetime, datetime | None] | None:
    """Return the manifest's valid inclusive run window."""
    run = manifest.get("run") if isinstance(manifest, dict) else None
    if not isinstance(run, dict):
        return None
    started_at = _aware_timestamp(run.get("started_at"))
    raw_end = run.get("ended_at")
    ended_at = None if raw_end is None else _aware_timestamp(raw_end)
    if started_at is None or (raw_end is not None and ended_at is None):
        return None
    if ended_at is not None and ended_at < started_at:
        return None
    return started_at, ended_at


def _bounded_jsonl_entries(
    path: str | Path,
    window: tuple[datetime, datetime | None],
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Load only timestamped records in one inclusive run window.

    Returns entries plus independent malformed-record and timestamp-gap flags.
    Evidence records without a usable timestamp cannot safely be assigned to a
    run. Timestamp-less session metadata is not run evidence and is ignored.

    Both manifest bounds are recorded INSIDE pipeline subprocesses:
    telemetry.start() runs within the Step 1 invocation, so the assistant
    entry that issued that call — the run's opening turn, carrying its
    usage — is timestamped just before ``started_at``; telemetry.finalize()
    likewise precedes the orchestrator's presentation response. The window
    therefore spans whole turns: it opens at the last human prompt at or
    before ``started_at`` (the run's trigger) and closes at the first human
    prompt after ``ended_at``. Foreign work in a reused session always sits
    on the far side of one of those prompts.
    """
    started_at, ended_at = window
    entries: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    pending_time_gap = False
    pending_parse_gap = False
    pending_has_opening_prompt = False
    in_window = False
    parse_gap = False
    time_gap = False
    try:
        # Binary like _read_jsonl: a bad UTF-8 byte must cost one line
        # (parse_gap), not the run's entire transcript enrichment. Like
        # timestamp gaps, a damaged line belongs to the turn it appears in
        # and is discarded when a later prompt supersedes that turn.
        with Path(path).open("rb") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    if in_window:
                        parse_gap = True
                    else:
                        pending_parse_gap = True
                    continue
                if not isinstance(value, dict):
                    if in_window:
                        parse_gap = True
                    else:
                        pending_parse_gap = True
                    continue
                timestamp = _aware_timestamp(value.get("timestamp"))
                if timestamp is None:
                    if value.get("type") in {"assistant", "user"}:
                        # A gap belongs to the turn it appears in: inside the
                        # window it damages the run's evidence; before the
                        # window it is discarded with its turn if a later
                        # prompt supersedes it.
                        if in_window:
                            time_gap = True
                        else:
                            pending_time_gap = True
                    continue
                if timestamp < started_at:
                    # Buffer the turn in flight at started_at; each earlier
                    # human prompt starts a fresh (discarded) turn buffer.
                    if _is_human_prompt(value):
                        pending = [value]
                        pending_time_gap = False
                        pending_parse_gap = False
                        pending_has_opening_prompt = True
                    else:
                        pending.append(value)
                    continue
                window_was_open = in_window
                opening_prompt_was_buffered = False
                if not in_window:
                    in_window = True
                    opening_prompt_was_buffered = pending_has_opening_prompt
                    entries.extend(pending)
                    pending = []
                    pending_has_opening_prompt = False
                    if pending_time_gap:
                        time_gap = True
                    if pending_parse_gap:
                        parse_gap = True
                if (
                    _is_human_prompt(value)
                    and (
                        (ended_at is not None and timestamp > ended_at)
                        or (
                            ended_at is None
                            and (window_was_open or opening_prompt_was_buffered)
                        )
                    )
                ):
                    # The opening turn may be entirely buffered before
                    # started_at. A live interactive interjection may close
                    # an open run early, but running windows are already
                    # partial evidence and unbounded absorption of foreign
                    # turns is worse.
                    break
                entries.append(value)
    except OSError:
        parse_gap = True
    return entries, parse_gap, time_gap


# User-role text records the harness synthesizes without an isMeta flag:
# <task-notification> when a background agent completes, <session_digest>
# when legacy compaction folds prior context into the log.
_SYNTHETIC_TEXT_PREFIXES = ("<task-notification>", "<session_digest")


def _is_human_prompt(value: dict[str, Any]) -> bool:
    """Return whether an entry is a genuine human prompt.

    User-role entries during an assistant turn carry tool_result blocks,
    harness-injected records (skill content, command caveats, system
    reminders, hook feedback) carry ``isMeta: true``, and task
    notifications and legacy session digests are recognizable only by
    their leading text — none is a human turn, so none may open or close
    a run's transcript window.
    """
    if value.get("type") != "user" or value.get("isMeta") is True:
        return False
    message = value.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        if any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        ):
            return False
        texts = [
            block.get("text")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
    else:
        return False
    return not (
        texts
        and all(
            isinstance(text, str)
            and text.lstrip().startswith(_SYNTHETIC_TEXT_PREFIXES)
            for text in texts
        )
    )


def find_session_file(sessions_root: str | Path, session_id: str) -> str | None:
    """Find one exact main-session JSONL without guessing on ambiguity."""
    if not isinstance(session_id, str) or not _SAFE_ID.fullmatch(session_id):
        return None
    if session_id in {".", ".."} or "/" in session_id or "\\" in session_id:
        return None

    root = Path(sessions_root).expanduser()
    try:
        root = root.resolve()
        children = list(root.iterdir())
    except OSError:
        return None

    candidates: list[Path] = []
    direct = root / f"{session_id}.jsonl"
    if direct.is_file():
        candidates.append(direct)
    for child in children:
        if not child.is_dir():
            continue
        candidate = child / f"{session_id}.jsonl"
        if candidate.is_file():
            candidates.append(candidate)

    unique: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved not in unique:
            unique.append(resolved)
    return str(unique[0]) if len(unique) == 1 else None


def _content_blocks(entry: dict[str, Any]) -> list[dict[str, Any]]:
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _tool_calls(
    entries: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Return well-formed tool calls plus the count of malformed ones.

    A tool_use block with a missing or non-string id/name — or a non-object
    input — cannot be paired, classified, or measured, but it was still an
    issued call: callers accounting for evidence completeness must count it
    as unresolved. Malformed Agent dispatch blocks are excluded: dispatch
    anomalies belong to the correlation machinery, which tracks them per
    actor family.
    """
    calls: list[dict[str, Any]] = []
    malformed = 0
    for index, entry in enumerate(entries):
        if entry.get("type") != "assistant":
            continue
        for block in _content_blocks(entry):
            if block.get("type") != "tool_use":
                continue
            tool_id = block.get("id")
            name = block.get("name")
            tool_input = block.get("input")
            # The harness always records ``input`` as an object (0 of
            # 14,889 surveyed real blocks deviate), so a non-dict input is
            # a damaged record like a non-string id/name. Substituting {}
            # would let the call pair and classify as success while its
            # read path or builder command silently vanished from the
            # evidence — missing operation data reported as complete.
            if (
                not isinstance(tool_id, str)
                or not isinstance(name, str)
                or not isinstance(tool_input, dict)
            ):
                if name not in _DISPATCH_TOOL_NAMES:
                    malformed += 1
                continue
            calls.append(
                {
                    "index": index,
                    "id": tool_id,
                    "name": name,
                    "input": tool_input,
                }
            )
    return calls, malformed


def _tool_results(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if entry.get("type") != "user":
            continue
        blocks = [
            block
            for block in _content_blocks(entry)
            if block.get("type") == "tool_result"
            and isinstance(block.get("tool_use_id"), str)
        ]
        entry_structured = entry.get("toolUseResult")
        for block in blocks:
            structured = block.get("toolUseResult")
            if not isinstance(structured, (dict, list)) and len(blocks) == 1:
                structured = entry_structured
            results.append(
                {
                    "index": index,
                    "id": block["tool_use_id"],
                    "block": block,
                    "structured": structured,
                }
            )
    return results


def _paired_results(
    calls: Iterable[dict[str, Any]], results: Iterable[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Pair only one call with one later result; ambiguity fails closed."""
    call_list = list(calls)
    result_list = list(results)
    call_counts = Counter(call["id"] for call in call_list)
    result_counts = Counter(result["id"] for result in result_list)
    calls_by_id = {
        call["id"]: call for call in call_list if call_counts[call["id"]] == 1
    }
    paired: dict[str, dict[str, Any]] = {}
    for result in result_list:
        tool_id = result["id"]
        call = calls_by_id.get(tool_id)
        if (
            call is not None
            and result_counts[tool_id] == 1
            and result["index"] > call["index"]
        ):
            paired[tool_id] = result
    return paired


def _result_text(result: dict[str, Any]) -> str:
    """Flatten only for detection; callers must never retain this value."""
    content = result.get("block", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _structured_failure(structured: object) -> bool:
    if not isinstance(structured, dict):
        return False
    for key in ("exitCode", "exit_code", "returncode"):
        value = structured.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value != 0:
            return True
    if structured.get("success") is False or structured.get("interrupted") is True:
        return True
    status = structured.get("status")
    if isinstance(status, str) and status.lower() in {
        "error",
        "failed",
        "failure",
        "interrupted",
    }:
        return True
    error = structured.get("error")
    return error not in (None, "", False, [], {})


def _exit_code(result: dict[str, Any]) -> int | None:
    """The exit code a failed call reports, or None.

    Two recorded shapes: a structured payload carrying the code, and the
    harness's own `Exit code <n>` first line on a result it flagged
    `is_error`. Never the command's output.
    """
    structured = result.get("structured")
    if isinstance(structured, dict):
        for key in ("exitCode", "exit_code", "returncode"):
            value = structured.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    if result.get("block", {}).get("is_error") is not True:
        return None
    match = _HARNESS_EXIT_LINE.fullmatch(_result_text(result).split("\n", 1)[0])
    return int(match.group(1)) if match else None


def _structured_success(structured: object) -> bool:
    if not isinstance(structured, dict):
        return False
    for key in ("exitCode", "exit_code", "returncode"):
        value = structured.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
            return True
    if structured.get("success") is True:
        return True
    status = structured.get("status")
    return isinstance(status, str) and status.lower() in {
        "ok",
        "success",
        "succeeded",
        "complete",
        "completed",
    }


def _structured_nonterminal(structured: object) -> bool:
    if not isinstance(structured, dict):
        return False
    if structured.get("interrupted") is False:
        return True
    status = structured.get("status")
    return isinstance(status, str) and status.lower() in {
        "started",
        "running",
        "pending",
    }


def _safe_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_structured_patch(value: object, *, allow_empty: bool) -> bool:
    if not isinstance(value, list) or (not value and not allow_empty):
        return False
    expected = {"oldStart", "oldLines", "newStart", "newLines", "lines"}
    for item in value:
        if not isinstance(item, dict) or set(item) != expected:
            return False
        if not all(_safe_int(item[key]) for key in expected - {"lines"}):
            return False
        lines = item.get("lines")
        if not isinstance(lines, list) or not all(
            isinstance(line, str) for line in lines
        ):
            return False
    return True


def _read_shape_succeeded(structured: object) -> bool:
    if not isinstance(structured, dict) or set(structured) != {"type", "file"}:
        return False
    file_data = structured.get("file")
    result_type = structured.get("type")
    if result_type == "file_unchanged":
        # Repeated Read of an unchanged file — a current, known non-error
        # variant carrying only the file path.
        return (
            isinstance(file_data, dict)
            and set(file_data) == {"filePath"}
            and isinstance(file_data.get("filePath"), str)
            and bool(file_data["filePath"])
        )
    if result_type == "image":
        # Image reads carry a rendered payload instead of text-file
        # metadata; the {"type": "image", "file": {...}} envelope is the
        # known non-error signal.
        return isinstance(file_data, dict) and bool(file_data)
    required_file = {"content", "filePath", "numLines", "startLine", "totalLines"}
    allowed_file = required_file | {"truncatedByTokenCap"}
    if (
        not isinstance(file_data, dict)
        or not required_file <= set(file_data) <= allowed_file
        or (
            "truncatedByTokenCap" in file_data
            and not isinstance(file_data["truncatedByTokenCap"], bool)
        )
    ):
        return False
    return (
        structured.get("type") == "text"
        and isinstance(file_data.get("content"), str)
        and isinstance(file_data.get("filePath"), str)
        and bool(file_data["filePath"])
        and all(
            _safe_int(file_data.get(key))
            for key in ("numLines", "startLine", "totalLines")
        )
    )


def _write_shape_succeeded(structured: object) -> bool:
    required = {
        "type",
        "content",
        "filePath",
        "originalFile",
        "structuredPatch",
        "userModified",
    }
    # memdirStamped is a known metadata flag current successful Write
    # results carry alongside the normal fields.
    allowed = required | {"memdirStamped"}
    if not isinstance(structured, dict) or not required <= set(structured) <= allowed:
        return False
    if "memdirStamped" in structured and not isinstance(
        structured["memdirStamped"], bool
    ):
        return False
    original = structured.get("originalFile")
    patch = structured.get("structuredPatch")
    common = (
        isinstance(structured.get("content"), str)
        and isinstance(structured.get("filePath"), str)
        and bool(structured["filePath"])
        and isinstance(structured.get("userModified"), bool)
    )
    if not common:
        return False
    result_type = structured.get("type")
    if result_type == "create" and original is None:
        return _valid_structured_patch(patch, allow_empty=True) and not patch
    return (
        result_type == "update"
        and (original is None or isinstance(original, str))
        and _valid_structured_patch(patch, allow_empty=False)
    )


def _edit_shape_succeeded(structured: object) -> bool:
    required = {
        "filePath",
        "oldString",
        "newString",
        "originalFile",
        "replaceAll",
        "structuredPatch",
        "userModified",
    }
    allowed = required | {"staleRecovered"}
    if (
        not isinstance(structured, dict)
        or not required <= set(structured) <= allowed
    ):
        return False
    original = structured.get("originalFile")
    if original is not None and not isinstance(original, str):
        return False
    if "staleRecovered" in structured and not isinstance(
        structured["staleRecovered"], bool
    ):
        return False
    return (
        isinstance(structured.get("filePath"), str)
        and bool(structured["filePath"])
        and isinstance(structured.get("oldString"), str)
        and isinstance(structured.get("newString"), str)
        and isinstance(structured.get("replaceAll"), bool)
        and isinstance(structured.get("userModified"), bool)
        and _valid_structured_patch(
            structured.get("structuredPatch"), allow_empty=False
        )
    )


def _grep_shape_succeeded(structured: object) -> bool:
    # Legacy Grep results omit is_error; the structured payload is the
    # success signal. Key sets are mode-specific: content mode carries the
    # matched text, count mode a match total, files_with_matches only the
    # file list. Zero matches is still a successful call.
    if not isinstance(structured, dict):
        return False
    mode = structured.get("mode")
    base = {"mode", "filenames", "numFiles"}
    if mode == "content":
        required = base | {"content", "numLines"}
        allowed = required | {"appliedLimit", "appliedOffset"}
    elif mode == "count":
        required = allowed = base | {"content", "numMatches"}
    elif mode == "files_with_matches":
        required = allowed = base
    else:
        return False
    if not required <= set(structured) <= allowed:
        return False
    filenames = structured.get("filenames")
    return (
        isinstance(filenames, list)
        and all(isinstance(name, str) for name in filenames)
        and ("content" not in required or isinstance(structured.get("content"), str))
        and all(
            _safe_int(structured[key])
            for key in set(structured)
            & {"numFiles", "numLines", "numMatches", "appliedLimit", "appliedOffset"}
        )
    )


def _glob_shape_succeeded(structured: object) -> bool:
    # Legacy Glob results omit is_error; the structured payload is the
    # success signal. An empty file list is still a successful call.
    expected = {"durationMs", "filenames", "numFiles", "truncated"}
    if not isinstance(structured, dict) or set(structured) != expected:
        return False
    filenames = structured.get("filenames")
    return (
        isinstance(filenames, list)
        and all(isinstance(name, str) for name in filenames)
        and _safe_int(structured.get("numFiles"))
        and _safe_int(structured.get("durationMs"))
        and isinstance(structured.get("truncated"), bool)
    )


def _tool_shape_succeeded(
    structured: object, tool_name: str | None, operation: str | None
) -> bool:
    if tool_name == "Read" and operation == "read":
        return _read_shape_succeeded(structured)
    if tool_name == "Write" and operation == "write":
        return _write_shape_succeeded(structured)
    if tool_name == "Edit" and operation == "edit":
        return _edit_shape_succeeded(structured)
    if tool_name == "Grep" and operation == "grep":
        return _grep_shape_succeeded(structured)
    if tool_name == "Glob" and operation == "glob":
        return _glob_shape_succeeded(structured)
    return False


def _result_state(
    result: dict[str, Any] | None,
    tool_name: str | None = None,
    operation: str | None = None,
) -> tuple[str, str | None, str | None]:
    """Return success/failure/unknown plus safe category and detector.

    ``unknown`` means UNRESOLVED EVIDENCE — the call was issued and nothing
    can be said about what came back. That is a property of the pairing and
    of explicit signals, never of how familiar a payload's shape looks.
    """
    if result is None:
        # No paired tool_result: the transcript ends mid-call. Nothing was
        # ever observed about this call, so its reads, failures, and
        # builder attempts are genuinely missing.
        return "unknown", None, None
    block = result.get("block", {})
    structured = result.get("structured")
    if block.get("is_error") is True or _structured_failure(structured):
        return "failure", "structured_failure", "structured"
    if block.get("is_error") is False or _structured_success(structured):
        return "success", None, None

    if tool_name not in _EVIDENCE_TOOL_NAMES:
        # Nothing downstream mines this call: it yields no read, no builder
        # attempt, and no shape this module validates. The only question
        # ever asked of its result is "did it fail?", and the explicit
        # signals above answered no — so a PAIRED result fully resolves it.
        # Deciding otherwise on the strength of an unfamiliar payload shape
        # reported 15 of 19 reviewers as carrying incomplete evidence on
        # every run that used WebSearch, and made the orchestrator's own
        # MCP-heavy transcript permanently unresolved.
        #
        # The text signature scan below is skipped for the same reason it
        # is skipped for shape-validated tools: these payloads carry
        # arbitrary fetched content, and "api error" appearing inside a web
        # result is not this call failing. A real failure of these tools
        # arrives as `is_error` or a structured error field.
        return "success", None, None

    nonterminal = _structured_nonterminal(structured)
    if not nonterminal and _tool_shape_succeeded(structured, tool_name, operation):
        # A validated success-shaped payload is authoritative. Result text
        # embeds arbitrary content for every one of these tools — Read file
        # bodies, Grep matched lines, Glob filenames, Write/Edit original
        # file text — so signature-scanning it would flip successful calls
        # into failures whenever the CONTENT mentions an error string.
        return "success", None, None

    lowered = _result_text(result).lower()
    for signature, category in _FAILURE_SIGNATURES:
        if signature in lowered:
            return "failure", category, "signature"
    if nonterminal:
        return "unknown", None, None
    if structured is not None:
        # An EVIDENCE tool (see `_EVIDENCE_TOOL_NAMES`) whose payload its own
        # shape validator rejected. Here the shape genuinely is the verdict:
        # this module reads that payload to derive a read, a target, or a
        # builder attempt, and one it cannot vouch for leaves that
        # derivation unmade — unresolved evidence, not a resolved call.
        return "unknown", None, None
    # A paired tool_result is the success signal in legacy/current records
    # that omit both ``is_error`` and structured result data. Known failure
    # fields and allowlisted signatures were exhausted above.
    return "success", None, None


def _shell_tokens(text: object) -> list[str] | None:
    """Tokenize one shell-like string, discarding comments and compounds."""
    if (
        not isinstance(text, str)
        or not text.strip()
        or "\x00" in text
        or "\n" in text
        or "\r" in text
    ):
        return None
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens = list(lexer)
    except ValueError:
        return None
    if (
        not tokens
        or any(token in _SHELL_OPERATORS for token in tokens)
        or any(_UNRESOLVED_PATH.search(token) for token in tokens)
    ):
        return None
    return tokens


def _extract_token_option(tokens: list[str], name: str) -> str | None:
    """Extract one literal option from an already validated token list."""
    values: list[str] = []
    for index, token in enumerate(tokens):
        if token == name:
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
                return None
            values.append(tokens[index + 1])
        elif token.startswith(f"{name}="):
            values.append(token.split("=", 1)[1])
    if len(values) != 1 or not values[0] or _UNRESOLVED_PATH.search(values[0]):
        return None
    return values[0]


def _literal_path_matches(value: object, expected_path: str | Path) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or not str(expected_path)
        or _UNRESOLVED_PATH.search(value)
    ):
        return False
    try:
        actual = Path(value).expanduser().resolve(strict=False)
        expected = Path(expected_path).expanduser().resolve(strict=False)
    except OSError:
        return False
    return actual == expected


def _valid_bootstrap_tokens(tokens: list[str]) -> bool:
    script_indexes = [
        index for index, token in enumerate(tokens) if Path(token).name == "bootstrap.py"
    ]
    if len(script_indexes) != 1:
        return False
    script_index = script_indexes[0]
    if script_index not in {0, 1}:
        return False
    if script_index == 1 and not re.fullmatch(
        r"python(?:\d+(?:\.\d+)*)?", Path(tokens[0]).name
    ):
        return False

    # The base reviewer form plus the adapter ref-mode form step 6 emits for
    # repo-contributed reviewers (pipeline.py cmd_parts). Rejecting the
    # adapter options would leave every repo-reviewer dispatch unrecognized.
    allowed_options = {
        "--agent",
        "--range",
        "--output-dir",
        "--instance-name",
        "--repo-agent-ref",
        "--adapter-label",
        "--execution",
        "--channel",
        "--scope-domains",
        "--model-tier",
    }
    index = script_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token in allowed_options:
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
                return False
            index += 2
            continue
        if any(token.startswith(f"{option}=") for option in allowed_options):
            index += 1
            continue
        return False
    return True


def _reviewer_bootstrap_tokens(text: object) -> list[str] | None:
    """Extract one standalone pipeline-owned bootstrap command from a prompt."""
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        return None
    candidates: list[list[str]] = []
    for line in text.splitlines():
        tokens = _shell_tokens(line.strip())
        if tokens is not None and _valid_bootstrap_tokens(tokens):
            candidates.append(tokens)
    return candidates[0] if len(candidates) == 1 else None


def _reviewer_output_path_matches(text: object, expected_path: str | Path) -> bool:
    """Validate the Step 6 bootstrap command and its complete output-dir value."""
    tokens = _reviewer_bootstrap_tokens(text)
    if tokens is None:
        return False
    return _literal_path_matches(
        _extract_token_option(tokens, "--output-dir"), expected_path
    )


def _is_special_agent(agent: str) -> bool:
    return agent in _NON_SCOPE_COMPARABLE_AGENTS


def _labelled_output_path_matches(text: object, expected_path: str | Path) -> bool:
    """Match the exact Output directory label used by synthesis agents."""
    if not isinstance(text, str) or not str(expected_path):
        return False
    pattern = re.compile(
        r"^\s*(?:-\s*)?(?:\*\*)?Output directory(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.*?)\s*$",
        re.IGNORECASE,
    )
    values: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match is None:
            continue
        value = match.group(1).strip()
        if not value:
            if index + 1 >= len(lines) or not lines[index + 1].strip():
                return False
            value = lines[index + 1].strip()
        if value.startswith("`") or value.endswith("`"):
            if not (value.startswith("`") and value.endswith("`") and len(value) > 2):
                return False
            value = value[1:-1]
        values.append(value)
    return len(values) == 1 and _literal_path_matches(values[0], expected_path)


def _recognized_identity(
    tool_input: dict[str, Any], recognized_agents: set[str]
) -> str | None:
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return None
    bootstrap_tokens = _reviewer_bootstrap_tokens(prompt)
    candidate = (
        _extract_token_option(bootstrap_tokens, "--agent")
        if bootstrap_tokens is not None
        else None
    )
    if candidate is not None:
        # Adapter ref-mode: bootstrap keys ref_mode on --repo-agent-ref,
        # requires --instance-name, and takes its effective identity from it
        # — mirror that exactly, or every repo-contributed reviewer
        # collapses onto the shared template identity while telemetry
        # records instance names. A ref without a valid instance name is
        # malformed producer output: unrecognized, never the template.
        if _extract_token_option(bootstrap_tokens, "--repo-agent-ref") is not None:
            instance = _extract_token_option(bootstrap_tokens, "--instance-name")
            if instance is None or not _REPO_REVIEWER_INSTANCE_RE.fullmatch(
                instance
            ):
                return None
            return instance
        return candidate if candidate in recognized_agents else None

    special_agents = {
        candidate for candidate in recognized_agents if _is_special_agent(candidate)
    }
    for field in ("subagent_type", "description"):
        value = tool_input.get(field)
        if not isinstance(value, str):
            continue
        for candidate in sorted(special_agents):
            if value == candidate or re.search(
                rf"(?<![A-Za-z0-9_-]){re.escape(candidate)}(?![A-Za-z0-9_-])",
                value,
            ):
                return candidate
    return None


def _dispatch_call_blocks(
    entries: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collect recognizable dispatch blocks without requiring a pairable ID."""
    calls: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if entry.get("type") != "assistant":
            continue
        for block in _content_blocks(entry):
            if (
                block.get("type") != "tool_use"
                or block.get("name") not in _DISPATCH_TOOL_NAMES
            ):
                continue
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                continue
            tool_id = block.get("id")
            calls.append(
                {
                    "index": index,
                    "id": tool_id if isinstance(tool_id, str) else None,
                    "id_valid": isinstance(tool_id, str),
                    "name": block["name"],
                    "input": tool_input,
                }
            )
    return calls


def _matching_dispatch_calls(
    entries: Iterable[dict[str, Any]],
    output_dir: str | Path,
    recognized_agents: set[str],
) -> list[dict[str, Any]]:
    """Collect exact run dispatch calls before attempting result correlation."""
    matches: list[dict[str, Any]] = []
    for call in _dispatch_call_blocks(entries):
        prompt = call["input"].get("prompt")
        agent = _recognized_identity(call["input"], recognized_agents)
        if agent is None:
            continue
        path_matches = _reviewer_output_path_matches(prompt, output_dir) or (
            _is_special_agent(agent)
            and _labelled_output_path_matches(prompt, output_dir)
        )
        if path_matches:
            matches.append({"agent": agent, "call": call})
    return matches


def _normalized_agent_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if _SAFE_ID.fullmatch(value) else None


def _agent_file_id(agent_id: str) -> str:
    """Return the ID portion used after the fixed ``agent-`` filename prefix."""
    return agent_id[len("agent-") :] if agent_id.startswith("agent-") else agent_id


def _safe_model(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_MODEL.fullmatch(value) else None


def _correlate_run_agent_entries(
    entries: Iterable[dict[str, Any]],
    main_session: str | Path,
    output_dir: str | Path,
    recognized_agents: Iterable[str],
) -> list[dict[str, Any]]:
    """Correlate only recognized dispatches belonging to one review run."""
    entries = list(entries)
    calls, _ = _tool_calls(entries)
    results = _tool_results(entries)
    call_counts = Counter(call["id"] for call in calls)
    result_by_id = _paired_results(calls, results)
    recognized = {
        item
        for item in recognized_agents
        if isinstance(item, str) and _SAFE_ID.fullmatch(item)
    }

    candidates: list[dict[str, Any]] = []
    for dispatch_match in _matching_dispatch_calls(entries, output_dir, recognized):
        call = dispatch_match["call"]
        tool_id = call.get("id")
        if not call.get("id_valid") or call_counts[tool_id] != 1:
            continue
        result = result_by_id.get(tool_id)
        if result is None:
            continue

        structured = result.get("structured")
        structured_dict = structured if isinstance(structured, dict) else {}
        agent_id = _normalized_agent_id(structured_dict.get("agentId"))
        if agent_id is None:
            legacy_matches = _LEGACY_AGENT_ID.findall(_result_text(result))
            agent_id = (
                _normalized_agent_id(legacy_matches[-1])
                if legacy_matches
                else None
            )
        if agent_id is None:
            continue
        candidates.append(
            {
                "agent": dispatch_match["agent"],
                "agent_id": agent_id,
                "file_id": _agent_file_id(agent_id),
                "model": _safe_model(structured_dict.get("resolvedModel")),
            }
        )

    id_counts = Counter(item["file_id"] for item in candidates)
    session = Path(main_session)
    correlated: list[dict[str, Any]] = []
    for item in candidates:
        if id_counts[item["file_id"]] != 1:
            continue
        transcript = (
            session.parent
            / session.stem
            / "subagents"
            / f"agent-{item['file_id']}.jsonl"
        )
        correlated.append(
            {
                "agent": item["agent"],
                "agent_id": item["agent_id"],
                "model": item["model"],
                "transcript": str(transcript),
            }
        )
    return correlated


def correlate_run_agents(
    main_session: str | Path,
    output_dir: str | Path,
    recognized_agents: Iterable[str],
) -> list[dict[str, Any]]:
    """Path-based correlation helper for one already-scoped session file."""
    return _correlate_run_agent_entries(
        iter_jsonl(main_session), main_session, output_dir, recognized_agents
    )


def _empty_usage() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "effective_input_tokens": 0,
        "output_tokens": 0,
    }


# Sentinel distinguishing "entry carries corrupted usage" from "entry has
# no usage" (None) — a fractional, negative, or non-numeric token count is
# damaged evidence, not a value to truncate into a fabricated exact total.
_INVALID_USAGE: dict[str, int] = {}


def _safe_token_count(value: object) -> int | None:
    """Exact nonnegative integer token counts; None marks invalid evidence.

    An absent field is a plain zero, but a fractional, negative, boolean,
    or non-numeric present value is corruption or schema drift — flooring
    it with int() would silently fabricate an exact total.
    """
    if value is None:
        return 0
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _entry_usage(entry: dict[str, Any]) -> dict[str, int] | None:
    if entry.get("type") != "assistant":
        return None
    message = entry.get("message")
    nested = message.get("usage") if isinstance(message, dict) else None
    raw = nested if isinstance(nested, dict) else entry.get("usage")
    if not isinstance(raw, dict):
        return None
    counts = {field: _safe_token_count(raw.get(field)) for field in _USAGE_FIELDS}
    if any(count is None for count in counts.values()):
        return _INVALID_USAGE
    usage = {field: count for field, count in counts.items() if count is not None}
    usage["effective_input_tokens"] = (
        usage["input_tokens"]
        + usage["cache_creation_input_tokens"]
        + usage["cache_read_input_tokens"]
    )
    return usage


def _add_usage(target: dict[str, int], addition: dict[str, int]) -> None:
    for key in target:
        target[key] += addition.get(key, 0)


def _usage_summary(
    entries: Iterable[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, dict[str, int]], bool, bool]:
    total = _empty_usage()
    by_model: dict[str, dict[str, int]] = {}
    usage_valid = True
    # One assistant response split across records shares message.id; input and
    # cache fields repeat unchanged while output_tokens grows toward the final
    # cumulative count, so the LAST record per ID is the response's real usage.
    keyed: dict[str, tuple[dict[str, int], str | None]] = {}
    unkeyed: list[tuple[dict[str, int], str | None]] = []
    for entry in entries:
        usage = _entry_usage(entry)
        if usage is None:
            continue
        if usage is _INVALID_USAGE:
            usage_valid = False
            continue
        message = entry.get("message")
        message_id = message.get("id") if isinstance(message, dict) else None
        model = _safe_model(message.get("model") if isinstance(message, dict) else None)
        if isinstance(message_id, str):
            keyed[message_id] = (usage, model)
        else:
            unkeyed.append((usage, model))
    for usage, model in (*keyed.values(), *unkeyed):
        _add_usage(total, usage)
        if model:
            model_usage = by_model.setdefault(model, _empty_usage())
            _add_usage(model_usage, usage)
    # usage_observed distinguishes a measured total from an absence of
    # evidence: an empty transcript, or one whose assistant records all lack
    # usage payloads, accumulates a "valid" zero that is not a measurement.
    usage_observed = bool(keyed or unkeyed)
    return total, dict(sorted(by_model.items())), usage_valid, usage_observed


def usage_summary_for_transcript(path: str | Path) -> dict[str, Any]:
    """Token usage of one transcript, the way the pipeline measures it: one
    assistant response split across records is counted once (last record
    per `message.id`), and a damaged line is a reported gap, not a crash.
    `session_metrics.py` and the session-analysis skill call this instead of
    summing `usage` blocks themselves — that second parser over-counted a
    reviewer by 45–84 % in the run-1 audit.
    """
    entries, parse_gap = _read_jsonl(path)
    total, by_model, usage_valid, usage_observed = _usage_summary(entries)
    return {
        "usage": total,
        "usage_by_model": by_model,
        "usage_valid": usage_valid,
        "usage_observed": usage_observed,
        "parse_gap": parse_gap,
    }


def _opaque_target(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "none"
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"opaque:{digest}"


def parse_builder_envelope(command: object) -> dict[str, str] | None:
    """Return the artifact identity a pipeline-owned builder save declares.

    The envelope's assignments are the whole recognition. The program they
    prefix is the reviewer's own — a heredoc, a scratch file written a line
    earlier in the same command, `-` on stdin — and session analysis reads
    the artifact the envelope names rather than inferring what the program
    would have written, so nothing here has to model Python or care which
    line the reviewer ran it on. Only the shape is fixed: a run of
    `NAME=VALUE` assignments carrying every required name and no foreign
    one, immediately followed by `python3`.
    """
    if not isinstance(command, str):
        return None
    for line in command.splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        env: dict[str, str] = {}
        for token in tokens:
            name, separator, value = token.partition("=")
            if separator == "=" and _ENV_NAME.fullmatch(name):
                if name in env:
                    env = {}
                    break
                env[name] = value
                continue
            if (
                token == "python3"
                and env
                and _BUILDER_ENV_REQUIRED <= set(env) <= _BUILDER_ENV_NAMES
            ):
                return {
                    "output_dir": env["PIRATEGOAT_OUTPUT_DIR"],
                    "reviewer": env["PIRATEGOAT_REVIEWER_NAME"],
                }
            env = {}
    return None


def _file_target(value: object, repo_root: str | Path) -> str:
    """Hash a file path in canonical repo-relative form when possible.

    A failed operation on a repo-relative path and its successful retry on
    the equivalent absolute path (or "./"-prefixed form) must hash to the
    same opaque target, or the recovery scan reports the failure as
    unrecovered.
    """
    if isinstance(value, str) and value and "\x00" not in value:
        candidate = os.path.normpath(value)
        root = os.path.normpath(str(repo_root)) if str(repo_root) else ""
        if root and os.path.isabs(candidate):
            if candidate == root:
                candidate = "."
            elif candidate.startswith(root + os.sep):
                candidate = os.path.relpath(candidate, root)
        return _opaque_target(candidate)
    return _opaque_target(value)


def _operation(
    call: dict[str, Any], repo_root: str | Path = ""
) -> tuple[str, str]:
    name = call["name"]
    tool_input = call["input"]
    if name == "Write":
        return "write", _file_target(tool_input.get("file_path"), repo_root)
    if name == "Read":
        return "read", _file_target(tool_input.get("file_path"), repo_root)
    if name == "Edit":
        return "edit", _file_target(tool_input.get("file_path"), repo_root)
    if name == "Bash":
        command = tool_input.get("command")
        return (
            (
                "builder_output_attempt"
                if parse_builder_envelope(command) is not None
                else "bash"
            ),
            _opaque_target(command),
        )
    safe_name = name.lower() if name in _SAFE_TOOL_NAMES else "other"
    return safe_name, "none"


def _normalize_repo_path(path: object, repo_root: Path) -> str | None:
    if not isinstance(path, str) or not path or "\x00" in path:
        return None
    candidate = Path(path).expanduser()
    candidate = candidate if candidate.is_absolute() else repo_root / candidate
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(repo_root.resolve(strict=False))
    except (OSError, ValueError):
        return None
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        return None
    if resolved.is_dir():
        # A directory is never a file read: `rg pattern src/` walks it.
        return None
    return relative.as_posix()


def _literal_path_tokens(tokens: Iterable[str]) -> list[str]:
    paths = list(tokens)
    if not paths or any(
        not token or token.isdigit() or _UNRESOLVED_PATH.search(token)
        for token in paths
    ):
        return []
    return paths


def _file_operands(tokens: list[str], command_name: str) -> list[str]:
    """Parse operands for a narrow allowlist of simple file-reading tools."""
    no_value_options = {
        "cat": {
            "-A",
            "-b",
            "-e",
            "-E",
            "-n",
            "-s",
            "-t",
            "-T",
            "-u",
            "-v",
            "--number",
            "--number-nonblank",
            "--show-all",
            "--show-ends",
            "--show-nonprinting",
            "--show-tabs",
            "--squeeze-blank",
        },
        "head": {"-q", "-v", "-z", "--quiet", "--silent", "--verbose", "--zero-terminated"},
        "tail": {
            "-f",
            "-F",
            "-q",
            "-v",
            "-z",
            "--follow",
            "--quiet",
            "--silent",
            "--verbose",
            "--zero-terminated",
        },
        "wc": {
            "-c",
            "-l",
            "-L",
            "-m",
            "-w",
            "--bytes",
            "--chars",
            "--lines",
            "--max-line-length",
            "--words",
        },
    }
    value_options = {
        "head": {"-c", "-n", "--bytes", "--lines"},
        "tail": {
            "-c",
            "-n",
            "-s",
            "--bytes",
            "--lines",
            "--max-unchanged-stats",
            "--pid",
            "--sleep-interval",
        },
    }

    operands: list[str] = []
    options_done = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if options_done:
            operands.append(token)
            index += 1
            continue
        if token == "--":
            options_done = True
            index += 1
            continue
        if token in no_value_options[command_name]:
            index += 1
            continue
        if token in value_options.get(command_name, set()):
            if index + 1 >= len(tokens):
                return []
            index += 2
            continue
        if command_name in {"head", "tail"} and (
            re.fullmatch(r"-\d+", token)
            or re.fullmatch(r"-[cn]\d+", token)
            or re.fullmatch(r"--(?:bytes|lines)=.+", token)
            or (
                command_name == "tail"
                and re.fullmatch(
                    r"--(?:max-unchanged-stats|pid|sleep-interval)=.+", token
                )
            )
        ):
            index += 1
            continue
        if command_name == "wc" and token.startswith("--files0-from"):
            return []
        if token.startswith("-"):
            return []
        options_done = True
        operands.append(token)
        index += 1
    return _literal_path_tokens(operands)


# Simple commands whose operands are read, beyond the original allowlist,
# because they are the read idioms the harness itself hands agents ("read
# files with cat, head, or sed -n, search with grep") — run 4's reconciliator
# read every source file through `sed -n` and `grep -n` inside `cd <repo>
# && …` compounds and measured zero repository reads.
_SED_VALUE_OPTIONS = {"-e", "-f", "-l", "--expression", "--file", "--line-length"}
_SED_WRITE_OPTIONS = {"-i", "--in-place"}
_GREP_COMMANDS = {"grep", "egrep", "fgrep"}
_GREP_VALUE_OPTIONS = {
    "-e", "-f", "-A", "-B", "-C", "-m", "-d", "-D",
    "--regexp", "--file", "--after-context", "--before-context", "--context",
    "--max-count", "--include", "--exclude", "--exclude-dir", "--color",
    "--colour", "--directories", "--devices", "--label",
}
# ripgrep is recursive by default and has no recursion flag: its `-r` is
# `--replace REPLACEMENT`, so it must not be read as grep's `-r`.
_RG_VALUE_OPTIONS = {
    "-e", "-f", "-A", "-B", "-C", "-m", "-d", "-E", "-g", "-j", "-M", "-r",
    "-t", "-T",
    "--regexp", "--file", "--after-context", "--before-context", "--context",
    "--max-count", "--max-depth", "--encoding", "--glob", "--iglob",
    "--threads", "--max-columns", "--replace", "--type", "--type-not",
    "--type-add", "--color", "--colour", "--pre", "--pre-glob", "--sort",
    "--sortr", "--ignore-file", "--max-filesize", "--path-separator",
    "--context-separator", "--engine",
}
_GREP_RECURSIVE_OPTIONS = {"-r", "-R", "--recursive", "--dereference-recursive"}
_NL_VALUE_OPTIONS = {
    "-b", "-d", "-f", "-h", "-i", "-l", "-n", "-s", "-v", "-w",
    "--body-numbering", "--section-delimiter", "--footer-numbering",
    "--header-numbering", "--line-increment", "--join-blank-lines",
    "--number-format", "--number-separator", "--starting-line-number",
    "--number-width",
}
_REDIRECT_OPERATORS = {"<", ">", ">>", "<<", "<<-", "<<<", ">&", "<&", "&>", "&>>", ">|"}
_HEREDOC_OPERATORS = {"<<", "<<-"}
_AND_OR_OPERATORS = {"&&", "||"}
_LIST_TERMINATORS = {";", "&"}
# Options that carry the pattern/script themselves, so every operand after
# them is a file (`grep -f patterns.txt target.php`, `sed -e p foo.php`).
_PATTERN_SUPPLYING_OPTIONS = {"-e", "-f", "--regexp", "--expression", "--file"}


def _pattern_then_files(
    tokens: list[str], value_options: set[str], pattern_given: bool = False,
) -> list[str]:
    """Operands of a pattern-taking reader: options first, the pattern once,
    then files. `-e PATTERN` supplies the pattern, so every operand is a file."""
    operands: list[str] = []
    options_done = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if options_done:
            # `--` ends the options, not the pattern: `grep -- pat file`
            # still names its pattern first.
            if pattern_given:
                operands.append(token)
            pattern_given = True
            index += 1
            continue
        if token == "--":
            options_done = True
            index += 1
            continue
        if token.startswith("--") and "=" in token:
            if token.split("=", 1)[0] in _PATTERN_SUPPLYING_OPTIONS:
                pattern_given = True
            index += 1
            continue
        if token in value_options:
            if token in _PATTERN_SUPPLYING_OPTIONS:
                pattern_given = True
            index += 2
            continue
        if token.startswith("-") and len(token) > 1:
            # A clustered short option ending in a value-taking flag, such as
            # `-A3` or `-ne`, carries its value inline; `-e` inside a
            # cluster (`-ne`) marks the pattern as given.
            if token[1] != "-" and any(f"-{ch}" in value_options for ch in token[1:]):
                for ch in token[1:]:
                    if f"-{ch}" in value_options:
                        if f"-{ch}" in _PATTERN_SUPPLYING_OPTIONS:
                            pattern_given = True
                        if token.endswith(ch):
                            index += 1  # value is the next token
                        break
            index += 1
            continue
        if not pattern_given:
            pattern_given = True
            index += 1
            continue
        operands.append(token)
        index += 1
    return operands


def _simple_command_read_paths(tokens: list[str]) -> list[str]:
    """Paths one simple command (no operators) reads, or []."""
    if not tokens or _UNRESOLVED_PATH.search(tokens[0]):
        return []
    command_name = tokens[0]
    if command_name == "sed":
        if any(
            token in _SED_WRITE_OPTIONS or token.startswith("-i")
            for token in tokens[1:]
        ):
            return []
        return _literal_path_tokens(_pattern_then_files(tokens, _SED_VALUE_OPTIONS))
    if command_name == "rg":
        # Recursive by default; a directory operand is dropped where the
        # path is normalised, since a directory is never a file read.
        return _literal_path_tokens(_pattern_then_files(tokens, _RG_VALUE_OPTIONS))
    if command_name in _GREP_COMMANDS:
        # `-r` may sit inside a cluster (`-rn`); a recursive search names
        # directories, which are not file reads.
        if any(
            token in _GREP_RECURSIVE_OPTIONS
            or (token.startswith("-") and not token.startswith("--") and set("rR") & set(token[1:]))
            for token in tokens[1:]
        ):
            return []
        return _literal_path_tokens(_pattern_then_files(tokens, _GREP_VALUE_OPTIONS))
    if command_name == "nl":
        # No pattern: every operand after the options is a file.
        return _literal_path_tokens(_pattern_then_files(tokens, _NL_VALUE_OPTIONS, pattern_given=True))
    return _simple_bash_read_paths(tokens)


# A word that unquotes to something operator-shaped (`'&&'`, `";"`) is an
# argument, not a separator; it is replaced by a token that is never a
# path (`$` fails the literal-path check) and never a command name.
_QUOTED_OPERATOR = "$quoted-operator"
_OPERATOR_CHARS = frozenset(";&|<>")


def _quoted_words(text: str) -> Optional[list[str]]:
    """The text's words with their quotes kept, operators as their own
    words; None when a quote is left open."""
    try:
        lexer = shlex.shlex(text, posix=False, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        return list(lexer)
    except ValueError:
        return None


def _quote_aware_tokens(raw_tokens: list[str]) -> Optional[list[str]]:
    """The tokens of one lexed line with operators told apart from quoted
    text.

    Lexed with quotes kept, so an unquoted `&&` and a quoted `'&&'` are
    different words; each word is then unquoted on its own. None when a
    word does not unquote to one word.
    """
    tokens: list[str] = []
    for token in raw_tokens:
        if token and set(token) <= _OPERATOR_CHARS:
            tokens.append(token)
            continue
        try:
            words = shlex.split(token, posix=True)
        except ValueError:
            return None
        if len(words) != 1:
            return None
        word = words[0]
        tokens.append(_QUOTED_OPERATOR if word and set(word) <= _OPERATOR_CHARS else word)
    return tokens


def _and_or_lists(command: str) -> list[tuple[list[list[str]], list[str], bool]]:
    """The command's and-or lists in order, as the shell would run them.

    Each list is `(commands, operators, backgrounded)`: its simple commands
    (token lists), the `&&`/`||` operator before each command after the
    first, and whether `&` sent it to the background. Lines, `;` and `&`
    end a list; a line ending in `&&` or `||` continues on the next one. A
    heredoc body is opaque up to its own terminator word, and a quoted
    word spans lines until its closing quote — a quote the command never
    closes leaves everything from it on unknown, and nothing there counts.
    """
    lists: list[tuple[list[list[str]], list[str], bool]] = []
    commands: list[list[str]] = []
    operators: list[str] = []
    simple: list[str] = []
    terminator: str | None = None
    open_quote = ""  # the lines an unclosed quote has joined so far

    def close(backgrounded: bool) -> None:
        nonlocal commands, operators, simple
        commands.append(simple)
        if any(commands):
            lists.append((commands, operators, backgrounded))
        commands, operators, simple = [], [], []

    for line in command.replace("\r", "\n").split("\n"):
        if terminator is not None:
            # A body line naming a reader is text, not a command.
            if line.strip() == terminator:
                terminator = None
            continue
        if open_quote:
            line = open_quote + "\n" + line
        words = _quoted_words(line)
        if words is None:
            open_quote = line
            continue
        open_quote = ""
        tokens = _quote_aware_tokens(words)
        if tokens is None:
            continue
        for index, token in enumerate(tokens):
            if token in _HEREDOC_OPERATORS and index + 1 < len(tokens):
                terminator = tokens[index + 1].lstrip("-")
                break
        for token in tokens:
            if token in _AND_OR_OPERATORS:
                commands.append(simple)
                operators.append(token)
                simple = []
            elif token in _LIST_TERMINATORS:
                close(token == "&")
            else:
                simple.append(token)
        # A line ending in `&&`/`||` continues its list on the next line.
        if not (tokens and tokens[-1] in _AND_OR_OPERATORS):
            close(False)
    close(False)
    return lists


def _pipeline_reader(simple: list[str]) -> tuple[list[str], list[str], bool]:
    """The reading stage of one simple command, its `<` input, and whether
    it is piped into a further stage.

    The reader is the first pipeline stage; a redirect ends the operands and
    a bare fd number before it (`2>&1`) is not one. An input redirect's
    target (`sed -n 1p < foo.php`) is read. A piped reader's exit status
    is masked by the last stage's (no pipefail), so the caller treats it as
    uncertain.
    """
    cut = len(simple)
    redirected_input: list[str] = []
    piped = False
    for index, token in enumerate(simple):
        if token.startswith("|") or token in _REDIRECT_OPERATORS:
            if cut == len(simple):
                cut = index
                if cut and simple[cut - 1].isdigit():
                    cut -= 1
            if token == "<" and index + 1 < len(simple):
                redirected_input = _literal_path_tokens([simple[index + 1]])
            if token.startswith("|"):
                piped = True
                break
    return simple[:cut], redirected_input, piped


def _may_end_shell_successfully(simple: list[str]) -> bool:
    """Whether one simple command can end the shell with status 0 before
    anything after it runs: `exec`, or an `exit`/`return` whose status is
    absent (the last command's) or a literal zero. `exit 1` ends the shell
    too, but a call that did so did not succeed, and the caller only asks
    about calls that did."""
    if not simple:
        return False
    if simple[0] == "exec":
        return True
    if simple[0] in ("exit", "return"):
        status = simple[1] if len(simple) > 1 else "0"
        return not (status.isdigit() and int(status) != 0)
    return False


def _sole_simple_command(command: object) -> list[str] | None:
    """The tokens of the one simple command a Bash call runs, or None.

    Only such a command has an exit status that is unambiguously its own
    program's. In anything longer the status may come from a later list, a
    failed `&&` member, or — in the harness's zsh — an expansion error that
    aborted the whole line, all of which were observed in the field runs.
    A redirection is dropped (it cannot change the status); a pipe ends the
    recognition, since a pipeline reports its LAST stage's status.
    """
    if not isinstance(command, str):
        return None
    lists = _and_or_lists(command)
    if len(lists) != 1:
        return None
    commands, operators, backgrounded = lists[0]
    if backgrounded or operators or len(commands) != 1:
        return None
    tokens, _, piped = _pipeline_reader(commands[0])
    return None if piped or not tokens else tokens


def _names_poll_program(tokens: list[str]) -> bool:
    """Whether these tokens run `agents_status.py`, directly or via Python."""
    if os.path.basename(tokens[0]) == _POLL_PROGRAM:
        return True
    return (
        os.path.basename(tokens[0]) in _PYTHON_PROGRAMS
        and len(tokens) > 1
        and os.path.basename(tokens[1]) == _POLL_PROGRAM
    )


def _is_poll_outcome(command: object, result: dict[str, Any]) -> bool:
    """Whether a failed Bash call is one of `agents_status.py`'s
    contractual non-zero outcomes rather than a fault.

    Recognition is the named program, the exit codes its docstring
    defines, AND the status envelope it prints — the way
    `parse_builder_envelope` recognizes the builder by its envelope rather
    than by prose. The envelope is load-bearing, not belt-and-braces: a
    misspelled flag and a missing script path both exit 2 from this very
    program, and exempting those would report a broken invocation as zero
    failures. Every 0/2/3 exit renders the status first, so its presence
    is what separates "it polled and said so" from "it never ran".
    """
    if _exit_code(result) not in _POLL_OUTCOME_EXIT_CODES:
        return False
    tokens = _sole_simple_command(command)
    if tokens is None or not _names_poll_program(tokens):
        return False
    return STATUS_ENVELOPE_PREFIX in _result_text(result)


def _bash_read_paths(command: object, repo_root: Path) -> list[str]:
    """Every repository-relative or absolute path a Bash command reads.

    Compounds are walked as the shell would run them: lines and `;`, `&&`,
    `||`, `&` separate simple commands; the first segment of a pipeline is
    the reader, but a piped reader is not counted, because a pipeline's
    exit status is its last stage's and a reader that failed before
    opening its file (`sed -n '[' a.php | head` exits 0) is masked; a
    redirection ends the operand list (an output target is
    written, an input target `< file` is read); `cd <dir>` moves the working
    directory for the relative operands that follow it, and a `cd` to an
    unresolvable directory (a variable, `~`, `-`) or to one that is not a
    directory under `repo_root` (the shell would have stayed put, and a
    fabricated location would count reads that never happened) leaves them
    uncounted until the next literal `cd`. A heredoc body is opaque up to its own
    terminator word; a `<<<` here-string has no body. Every operand still has to
    be a literal path.

    The caller counts reads only from calls that succeeded, and a success
    certifies exactly one thing: the last foreground and-or list ended in
    success, so every `&&`-joined command in it ran and succeeded — unless
    an `exit`, `exec` or `return` in an earlier list may have ended the
    shell before it, when the success is that command's and certifies
    nothing. A read is counted only there. Everywhere else — an earlier list, a
    backgrounded one, or any list with `||` — a reader may not have run,
    or ran and failed before opening its file (`sed -n '[' a.php; true`
    exits 0), so nothing is counted. The first command of an earlier
    foreground list is still known to have run, which is enough for a `cd`
    there to move the detector (its own success is checked against the
    disk); a `cd` after it leaves the working directory unknown, and a
    backgrounded list is a subshell whose `cd` moves nothing. Not exhaustive by
    construction: a read through any other tool is invisible, which is why
    the measurement is reported as "no read observed", never "read nothing".
    """
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        return []
    reads: list[str] = []
    cwd: str | None = ""  # "" is the repository root; None is unknown
    lists = _and_or_lists(command)
    # An `exit`, `exec` or `return` may have ended the shell with status 0
    # before anything after it ran (`test -f x || exit 0; cat y`, or
    # `true && exit 0 && cat y`, exit 0 with y unread), so nothing after
    # one is certified, in the same list or a later one.
    may_have_exited = False
    for position, (commands, operators, backgrounded) in enumerate(lists):
        # `&` runs the list in a subshell: its `cd` moves nothing in the
        # foreground and its `exit` ends nothing here, so the list is
        # opaque (`cd src & cat a.py` reads the root's a.py).
        if backgrounded:
            continue
        every_command_ran = position == len(lists) - 1 and "||" not in operators
        for index, simple in enumerate(commands):
            ran = index == 0 or every_command_ran
            if may_have_exited:
                continue
            if _may_end_shell_successfully(simple):
                may_have_exited = True
                continue
            simple, redirected_input, piped = _pipeline_reader(simple)
            # A pipeline runs its stages in subshells: a piped `cd` moves
            # nothing, and a piped reader's success is unknown.
            if not simple or piped:
                continue
            if simple[0] == "cd":
                target = simple[1] if len(simple) > 1 else None
                if (
                    not ran or target is None or target == "-"
                    or _UNRESOLVED_PATH.search(target) or target.startswith("~")
                ):
                    cwd = None
                    continue
                if os.path.isabs(target):
                    moved = target
                elif cwd is None:
                    continue
                else:
                    moved = os.path.normpath(os.path.join(cwd, target)) if cwd else target
                # A `cd` to something that is not a directory fails and the
                # shell stays where it was; rather than guess which, the
                # location is unknown until the next `cd` that resolves.
                on_disk = moved if os.path.isabs(moved) else os.path.join(str(repo_root), moved)
                cwd = moved if os.path.isdir(on_disk) else None
                continue
            # Ran is not succeeded: only the certified list counts reads.
            if not every_command_ran:
                continue
            for operand in _simple_command_read_paths(simple) + redirected_input:
                if os.path.isabs(operand) or operand.startswith("~"):
                    reads.append(operand)
                elif cwd is not None:
                    reads.append(os.path.join(cwd, operand) if cwd else operand)
    return reads


def _simple_bash_read_paths(tokens: list[str]) -> list[str]:
    """Paths the simple commands the detector knew first read: `git diff --`,
    `git show <rev>:<path>`, and the cat/head/tail/wc family."""
    if not tokens:
        return []

    if len(tokens) >= 2 and tokens[:2] == ["git", "diff"]:
        if "--" not in tokens:
            return []
        separator = tokens.index("--")
        return _literal_path_tokens(tokens[separator + 1 :])

    if len(tokens) >= 3 and tokens[:2] == ["git", "show"]:
        for token in tokens[2:]:
            if token.startswith("-") or ":" not in token:
                continue
            _, path = token.split(":", 1)
            return _literal_path_tokens([path])
        return []

    command_name = tokens[0]
    if command_name in {"cat", "head", "tail", "wc"}:
        return _file_operands(tokens, command_name)
    return []


def _analyze_entries(
    entries: Iterable[dict[str, Any]],
    repo_root: str | Path,
    scope_paths: Iterable[str],
) -> dict[str, Any]:
    """Measure transcript entries without retaining prompts, bodies, or commands."""
    entries = list(entries)
    calls, malformed_calls = _tool_calls(entries)
    results = _tool_results(entries)
    call_counts = Counter(call["id"] for call in calls)
    result_by_id = _paired_results(calls, results)
    usage, usage_by_model, usage_valid, usage_observed = _usage_summary(entries)

    analyzed_calls: list[dict[str, Any]] = []
    # Malformed tool_use blocks were issued calls that can never be paired
    # or classified — unresolved evidence from the start.
    # Agent dispatch calls are carved out of every unresolved bucket: their
    # anomalies (dangling, malformed, duplicated dispatches) are the
    # correlation machinery's domain, tracked per actor family through
    # expected/missing counts and dispatch warnings. Counting them here too
    # would collapse that per-family isolation into whole-run degradation.
    unresolved_calls = malformed_calls
    for call in calls:
        if call_counts[call["id"]] != 1:
            # A repeated tool-use ID makes call/result pairing ambiguous:
            # these calls are skipped, so their reads, failures, and builder
            # attempts vanish — that is unresolved evidence, not a
            # complete-looking transcript.
            if call["name"] not in _DISPATCH_TOOL_NAMES:
                unresolved_calls += 1
            continue
        operation, target = _operation(call, repo_root)
        result = result_by_id.get(call["id"])
        state, category, detector = _result_state(
            result, call["name"], operation
        )
        if (
            state == "failure"
            and call["name"] == "Bash"
            and _is_poll_outcome(call["input"].get("command"), result)
        ):
            # A poll saying "still running" is the instrument working, not
            # the call failing. It stays in the list under its own category
            # and out of the counted totals.
            category = "poll_outcome"
        if state == "unknown" and call["name"] not in _DISPATCH_TOOL_NAMES:
            # The call resolves to neither success nor failure — the
            # transcript ends mid-call (no tool_result), or an evidence
            # tool's paired payload matches no recognized schema. Either way
            # the call vanishes from read and failure metrics, so the
            # evidence is incomplete. A tool this module mines nothing from
            # never lands here on shape alone; see `_result_state`.
            unresolved_calls += 1
        analyzed_calls.append(
            {
                "call": call,
                "operation": operation,
                "target": target,
                "state": state,
                "category": category,
                "detector": detector,
            }
        )

    failures: list[dict[str, Any]] = []
    success_keys: set[tuple[str, str, str]] = set()
    success_name_ops: set[tuple[str, str]] = set()
    for item in reversed(analyzed_calls):
        name = item["call"]["name"]
        operation = item["operation"]
        target = item["target"]
        key = (name, operation, target)
        name_op = (name, operation)
        # At each item, these sets contain strictly later successes.
        if item["state"] == "failure":
            # An expected instrument exit has nothing to recover from: the
            # ALL_DONE poll that ends a waiting window is the same
            # instrument reporting a later state, not a retry that worked.
            # It never enters `success_keys` either, so it cannot mark a
            # genuine failure of the same command recovered.
            expected_exit = item["category"] in EXPECTED_EXIT_CATEGORIES
            recovered = not expected_exit and (
                key in success_keys
                or (
                    operation == "builder_output_attempt"
                    and name_op in success_name_ops
                )
            )
            failures.append(
                {
                    "category": item["category"],
                    "detector": item["detector"],
                    "tool": name if name in _SAFE_TOOL_NAMES else "Other",
                    "operation_class": operation,
                    "normalized_target": target,
                    "recovered": recovered,
                    "recovery": (
                        "not_applicable"
                        if expected_exit
                        else "later_success" if recovered else "none"
                    ),
                }
            )
        elif item["state"] == "success":
            success_keys.add(key)
            success_name_ops.add(name_op)
    failures.reverse()

    builder = [
        item
        for item in analyzed_calls
        if item["operation"] == "builder_output_attempt"
    ]
    builder_successes = sum(item["state"] == "success" for item in builder)
    builder_failures = sum(item["state"] == "failure" for item in builder)
    first_state = builder[0]["state"] if builder else None
    artifact_writes = {
        "builder_attempted": bool(builder),
        "builder_attempts": len(builder),
        "builder_successes": builder_successes,
        "builder_failures": builder_failures,
        "first_builder_attempt_succeeded": (
            first_state == "success" if first_state in {"success", "failure"} else None
        ),
        "recovered": any(
            failure["operation_class"] == "builder_output_attempt"
            and failure["recovered"]
            for failure in failures
        ),
    }

    repo = Path(repo_root).expanduser().resolve(strict=False)
    normalized_scope = {
        normalized
        for scope_path in scope_paths
        if (normalized := _normalize_repo_path(scope_path, repo)) is not None
    }
    reads: set[str] = set()
    for item in analyzed_calls:
        if item["state"] != "success":
            continue
        call = item["call"]
        candidates: list[object] = []
        if call["name"] == "Read":
            candidates = [call["input"].get("file_path")]
        elif call["name"] == "Bash":
            candidates = _bash_read_paths(call["input"].get("command"), repo)
        for candidate in candidates:
            normalized = _normalize_repo_path(candidate, repo)
            if normalized is not None:
                reads.add(normalized)

    sorted_reads = sorted(reads)
    observed_reads = {
        "all": sorted_reads,
        "in_scope": sorted(reads & normalized_scope),
        "out_of_scope": sorted(reads - normalized_scope),
        "exhaustive": False,
    }
    return {
        "usage": usage,
        "usage_by_model": usage_by_model,
        "usage_valid": usage_valid,
        "usage_observed": usage_observed,
        # Budget-utilization numerator: every issued call, including
        # duplicated-id, malformed, and unresolved ones — each spent budget.
        "tool_calls": len(calls) + malformed_calls,
        "unresolved_calls": unresolved_calls,
        "tool_failures": failures,
        "artifact_writes": artifact_writes,
        "observed_reads": observed_reads,
    }


def analyze_subagent(
    path: str | Path,
    repo_root: str | Path,
    scope_paths: Iterable[str],
) -> dict[str, Any]:
    """Measure one exact agent transcript from its path."""
    return _analyze_entries(iter_jsonl(path), repo_root, scope_paths)


def _normalize_run_identity(value: object) -> tuple[str | None, bool]:
    """Normalize valid run identity or legacy absence, rejecting other shapes."""
    if value is None:
        return None, True
    if not isinstance(value, str):
        return None, False
    if value == "":
        return None, True
    if not _SAFE_ID.fullmatch(value):
        return None, False
    return value, True


def _manifest_step_timeline(
    manifest: dict[str, Any],
) -> tuple[list[tuple[datetime, str]], bool]:
    """Validate the append-ordered manifest transitions for stage attribution."""
    window = _run_window(manifest)
    run = manifest.get("run") if isinstance(manifest, dict) else None
    manifest_run_id, manifest_run_id_valid = _normalize_run_identity(
        run.get("id") if isinstance(run, dict) else None
    )
    steps = manifest.get("steps") if isinstance(manifest, dict) else None
    if (
        window is None
        or not isinstance(steps, list)
        or not manifest_run_id_valid
    ):
        return [], False
    started_at, ended_at = window
    transitions: list[tuple[datetime, str]] = [(started_at, "1")]
    previous = started_at
    for event in steps:
        if not isinstance(event, dict) or event.get("event") != "step":
            return [], False
        event_run_id, event_run_id_valid = _normalize_run_identity(
            event.get("run_id")
        )
        if not event_run_id_valid or event_run_id != manifest_run_id:
            return [], False
        step = event.get("step")
        timestamp = _aware_timestamp(event.get("timestamp"))
        if (
            not isinstance(step, int)
            or isinstance(step, bool)
            or step < 1
            or timestamp is None
            or timestamp < previous
            or timestamp < started_at
            or (ended_at is not None and timestamp > ended_at)
        ):
            return [], False
        transitions.append((timestamp, str(step)))
        previous = timestamp
    return transitions, True


def _analyze_orchestrator_entry_steps(
    entries: Iterable[dict[str, Any]], manifest: dict[str, Any]
) -> tuple[dict[str, dict[str, int]], bool]:
    """Attribute bounded main-session usage from manifest step timestamps."""
    entries = list(entries)
    transitions, timeline_complete = _manifest_step_timeline(manifest)
    stages: dict[str, dict[str, int]] = {"unattributed": _empty_usage()}
    # With a complete timeline the bounded window opens at the run's
    # triggering turn, whose entries precede started_at — that opening
    # work is Step 1's, not unattributed.
    active = "1" if timeline_complete else "unattributed"
    stages.setdefault(active, _empty_usage())
    # Same repeated-message.id contract as _usage_summary: the last record per
    # ID carries the response's final cumulative usage. The response is
    # attributed to the stage active at its FIRST record (where it began), so
    # per-step totals stay consistent with total and per-model usage.
    keyed: dict[str, tuple[str, dict[str, int]]] = {}
    unkeyed: list[tuple[str, dict[str, int]]] = []
    transition_index = 0
    for entry in entries:
        timestamp = _aware_timestamp(entry.get("timestamp"))
        if timeline_complete and timestamp is not None:
            while (
                transition_index < len(transitions)
                and transitions[transition_index][0] <= timestamp
            ):
                active = transitions[transition_index][1]
                stages.setdefault(active, _empty_usage())
                transition_index += 1
        usage = _entry_usage(entry)
        if usage is None or usage is _INVALID_USAGE:
            # Invalid usage is excluded here exactly as in _usage_summary,
            # keeping per-step totals consistent with the (downgraded) run
            # totals.
            continue
        message = entry.get("message")
        message_id = message.get("id") if isinstance(message, dict) else None
        if isinstance(message_id, str):
            stage = keyed[message_id][0] if message_id in keyed else active
            keyed[message_id] = (stage, usage)
        else:
            unkeyed.append((active, usage))
    for stage, usage in (*keyed.values(), *unkeyed):
        _add_usage(stages.setdefault(stage, _empty_usage()), usage)
    return stages, timeline_complete


def analyze_orchestrator_steps(
    main_session: str | Path, manifest: dict[str, Any]
) -> tuple[dict[str, dict[str, int]], bool]:
    """Path-based stage analysis bounded by one manifest run window."""
    window = _run_window(manifest)
    if window is None:
        return {"unattributed": _empty_usage()}, False
    entries, parse_gap, time_gap = _bounded_jsonl_entries(main_session, window)
    stages, timeline_complete = _analyze_orchestrator_entry_steps(entries, manifest)
    return stages, timeline_complete and not parse_gap and not time_gap


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "warnings": [],
        "orchestrator_usage_by_step": None,
        "agent_usage": None,
        "usage": None,
        "tool_failures": None,
        "artifact_writes": None,
        "observed_reads": None,
    }


def _scope_for_agent(manifest: dict[str, Any], agent: str) -> list[str] | None:
    """Return the agent's authoritative scope mapping, or None without one.

    An absent mapping (no assignment, no assigned_files_by_agent, no entry
    for the agent) is NOT an empty scope: classifying reads against it would
    report every read as out-of-scope while claiming completeness. The key
    is manifest_sections.ASSIGNED_FILES_BY_AGENT — the producer's spelling.
    """
    assignment = manifest.get("assignment")
    by_agent = (
        assignment.get("assigned_files_by_agent")
        if isinstance(assignment, dict) else None
    )
    paths = by_agent.get(agent) if isinstance(by_agent, dict) else None
    if not isinstance(paths, list):
        return None
    return [path for path in paths if isinstance(path, str)]


def _expected_agents(
    manifest: dict[str, Any], recognized_agents: set[str]
) -> tuple[bool, Counter[str], bool]:
    """Return availability, safe manifest execution counts, and invalid state."""
    agents = manifest.get("agents")
    started = agents.get("started") if isinstance(agents, dict) else None
    if not isinstance(started, list):
        return False, Counter(), False
    expected: Counter[str] = Counter()
    invalid = False
    for event in started:
        name = event.get("agent") if isinstance(event, dict) else None
        if (
            not isinstance(name, str)
            or not _SAFE_ID.fullmatch(name)
            or not _is_recognized_reviewer(name, recognized_agents)
        ):
            invalid = True
            continue
        expected[name] += 1
    return True, expected, invalid


def _expected_call_counts(
    entries: Iterable[dict[str, Any]],
    output_dir: str | Path,
    recognized_agents: set[str],
) -> tuple[Counter[str], Counter[str]]:
    """Count exact dispatch calls and matching calls with unpairable IDs."""
    matches = _matching_dispatch_calls(entries, output_dir, recognized_agents)
    return (
        Counter(match["agent"] for match in matches),
        Counter(
            match["agent"]
            for match in matches
            if not match["call"].get("id_valid")
        ),
    )


def _sorted_counts(counts: Counter[str]) -> dict[str, int]:
    return {agent: counts[agent] for agent in sorted(counts) if counts[agent] > 0}


def enrich_run_transcript(
    manifest: dict[str, Any],
    sessions_root: str | Path,
    recognized_agents: Iterable[str],
) -> dict[str, Any]:
    """Build a safe transcript measurement view for one run manifest."""
    run = manifest.get("run") if isinstance(manifest, dict) else None
    run = run if isinstance(run, dict) else {}
    session_id = run.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return _unavailable("missing_session_id")
    main_session = find_session_file(sessions_root, session_id)
    if main_session is None:
        return _unavailable("session_not_found_or_ambiguous")
    window = _run_window(manifest)
    if window is None:
        return _unavailable("invalid_run_window")
    main_entries, main_parse_gap, main_time_gap = _bounded_jsonl_entries(
        main_session, window
    )

    output_dir = run.get("output_dir")
    output_dir = output_dir if isinstance(output_dir, str) else ""
    repo_path = run.get("repo_path")
    repo_path = repo_path if isinstance(repo_path, str) and repo_path else "."

    recognized = {
        item
        for item in recognized_agents
        if isinstance(item, str) and _SAFE_ID.fullmatch(item)
    }
    manifest_expected_available, manifest_expected, expected_invalid = _expected_agents(
        manifest, recognized
    )
    warnings: list[dict[str, str]] = []
    main_analysis = _analyze_entries(main_entries, repo_path, [])
    if not main_analysis["usage_valid"]:
        # Corrupted token counts are damaged records — same channel as
        # undecodable lines.
        main_parse_gap = True
    # A running manifest's window is still open: its transcript keeps
    # growing through later steps, completions, and resume turns, so no
    # observed family may claim completeness until the run settles.
    run_settled = window[1] is not None and manifest.get("status") != "running"
    # The main session drove the pipeline, so its bounded window must
    # contain usage-bearing assistant responses. An empty located file or
    # usage-less records is absent evidence — reporting it complete would
    # put exact zero-token totals into complete-cohort denominators.
    main_usage_missing = (
        main_analysis["usage_valid"] and not main_analysis["usage_observed"]
    )
    main_data_complete = (
        not main_parse_gap
        and not main_time_gap
        and not main_analysis["unresolved_calls"]
        and not main_usage_missing
        and run_settled
    )
    expected_available = manifest_expected_available and main_data_complete
    if main_parse_gap:
        warnings.append({"code": "orchestrator_transcript_parse_gap"})
    if main_time_gap:
        warnings.append({"code": "orchestrator_transcript_time_gap"})
    if run_settled and main_usage_missing and not main_parse_gap:
        # Suppressed under a parse gap — damaged lines already explain the
        # absence. Unsettled runs may simply not have assistant turns yet.
        warnings.append({"code": "orchestrator_transcript_usage_missing"})
    if main_analysis["unresolved_calls"]:
        # Same contract as subagents: a call resolving to neither success
        # nor failure is incomplete evidence, not a complete transcript.
        warnings.append({"code": "orchestrator_transcript_unresolved_calls"})
    if not manifest_expected_available:
        warnings.append({"code": "expected_agents_unavailable"})
    elif expected_invalid:
        warnings.append({"code": "expected_agent_identity_invalid"})
    orchestrator_usage_by_step, stage_timeline_complete = (
        _analyze_orchestrator_entry_steps(main_entries, manifest)
    )
    if not stage_timeline_complete:
        warnings.append({"code": "orchestrator_stage_timeline_invalid"})
    total_usage = _empty_usage()
    _add_usage(total_usage, main_analysis["usage"])
    failures = [
        {"actor": "orchestrator", **failure}
        for failure in main_analysis["tool_failures"]
    ]
    artifact_by_agent: list[dict[str, Any]] = []
    # Observed-read scope measures correlated reviewer and synthesis agents.
    # Main-session reads belong to orchestration and have no generated reviewer
    # scope, so including them would turn ordinary planning reads into apparent
    # reviewer fallbacks and out-of-scope accesses.
    read_all: set[str] = set()
    read_in_scope: set[str] = set()
    read_non_scope_comparable: set[str] = set()
    agent_usage: list[dict[str, Any]] = []
    seen_paths = {str(Path(main_session).resolve(strict=False))}
    missing_transcripts: set[str] = set()
    agent_transcript_parse_gaps: set[str] = set()
    unresolved_evidence: set[str] = set()
    missing_scope_evidence: set[str] = set()

    call_expected, dispatch_schema_gaps = _expected_call_counts(
        main_entries, output_dir, recognized
    )
    for agent in sorted(dispatch_schema_gaps):
        warnings.append({"code": "agent_dispatch_schema_gap", "agent": agent})
    # The two ledgers observe the same executions without a shared dispatch ID.
    # Their per-agent multiset union is therefore the larger observed count,
    # not the sum; synthesis-only calls and retries remain visible.
    expected_counts = Counter(
        {
            agent: max(manifest_expected[agent], call_expected[agent])
            for agent in manifest_expected.keys() | call_expected.keys()
        }
    )
    correlated = _correlate_run_agent_entries(
        main_entries, main_session, output_dir, recognized
    )
    correlated_counts = Counter(dispatch["agent"] for dispatch in correlated)
    missing_counts = Counter(
        {
            agent: expected_counts[agent] - correlated_counts[agent]
            for agent in expected_counts
            if expected_counts[agent] > correlated_counts[agent]
        }
    )
    expected = sorted(expected_counts)
    correlated_names = sorted(correlated_counts)
    missing = sorted(missing_counts)
    for agent in missing:
        warnings.append({"code": "expected_agent_uncorrelated", "agent": agent})
    for dispatch in correlated:
        transcript = Path(dispatch["transcript"])
        metadata = {
            "agent": dispatch["agent"],
            "agent_id": dispatch["agent_id"],
            "model": dispatch["model"],
        }
        if not transcript.is_file():
            missing_transcripts.add(dispatch["agent"])
            warnings.append(
                {"code": "agent_transcript_missing", "agent": dispatch["agent"]}
            )
            agent_usage.append(
                {
                    **metadata,
                    "available": False,
                    "usage": None,
                    "usage_by_model": None,
                    "tool_calls": None,
                    "repository_reads": None,
                }
            )
            continue
        resolved = str(transcript.resolve(strict=False))
        if resolved in seen_paths:
            missing_transcripts.add(dispatch["agent"])
            warnings.append(
                {"code": "duplicate_transcript_ignored", "agent": dispatch["agent"]}
            )
            continue
        seen_paths.add(resolved)
        # The manifest run window bounds subagent evidence exactly like the
        # orchestrator transcript: a resumed agent appends later turns to the
        # same file, and reading them would let historical run metrics absorb
        # post-run usage, reads, and failures.
        entries, parse_gap, time_gap = _bounded_jsonl_entries(transcript, window)
        if parse_gap:
            agent_transcript_parse_gaps.add(dispatch["agent"])
            warnings.append(
                {"code": "agent_transcript_parse_gap", "agent": dispatch["agent"]}
            )
        if time_gap:
            agent_transcript_parse_gaps.add(dispatch["agent"])
            warnings.append(
                {"code": "agent_transcript_time_gap", "agent": dispatch["agent"]}
            )

        agent_scope = _scope_for_agent(manifest, dispatch["agent"])
        if (
            agent_scope is None
            and dispatch["agent"] not in _NON_SCOPE_COMPARABLE_READ_AGENTS
        ):
            missing_scope_evidence.add(dispatch["agent"])
            warnings.append(
                {
                    "code": "agent_scope_evidence_missing",
                    "agent": dispatch["agent"],
                }
            )
        analysis = _analyze_entries(
            entries,
            repo_path,
            agent_scope or [],
        )
        if not analysis["usage_valid"] and dispatch["agent"] not in (
            agent_transcript_parse_gaps
        ):
            # Corrupted token counts are damaged records — same channel as
            # undecodable lines.
            agent_transcript_parse_gaps.add(dispatch["agent"])
            warnings.append(
                {
                    "code": "agent_transcript_parse_gap",
                    "agent": dispatch["agent"],
                }
            )
        if (
            analysis["usage_valid"]
            and not analysis["usage_observed"]
            and dispatch["agent"] not in agent_transcript_parse_gaps
        ):
            # An expected agent transcript with zero usage-bearing assistant
            # responses is absent evidence, not a measured zero-token run.
            agent_transcript_parse_gaps.add(dispatch["agent"])
            warnings.append(
                {
                    "code": "agent_transcript_usage_missing",
                    "agent": dispatch["agent"],
                }
            )
        if analysis["unresolved_calls"]:
            unresolved_evidence.add(dispatch["agent"])
            warnings.append(
                {
                    "code": "agent_transcript_unresolved_calls",
                    "agent": dispatch["agent"],
                }
            )
        _add_usage(total_usage, analysis["usage"])
        agent_usage.append(
            {
                **metadata,
                "available": True,
                "usage": analysis["usage"],
                "usage_by_model": analysis["usage_by_model"],
                "tool_calls": analysis["tool_calls"],
                # Count distinct repository files in normalized read evidence.
                "repository_reads": len(analysis["observed_reads"]["all"]),
            }
        )
        failures.extend(
            {"actor": dispatch["agent"], **failure}
            for failure in analysis["tool_failures"]
        )
        # Only regular reviewers are subject to the bootstrap builder-envelope
        # contract; synthesis agents (reconciliator, decision-reviewer,
        # critic) save through other mechanisms, and counting their normal
        # builder_attempted=false entries would inflate the reviewer
        # noncompliance denominator.
        if dispatch["agent"] not in _NON_SCOPE_COMPARABLE_AGENTS:
            artifact_by_agent.append(
                {"agent": dispatch["agent"], **analysis["artifact_writes"]}
            )
        if dispatch["agent"] in _NON_SCOPE_COMPARABLE_READ_AGENTS:
            # Scope-exempt reviewers have no scope to compare against —
            # partitioning their self-discovered reads would report every
            # legitimate read as out-of-scope.
            read_non_scope_comparable.update(
                analysis["observed_reads"]["all"]
            )
        else:
            read_all.update(analysis["observed_reads"]["all"])
            read_in_scope.update(analysis["observed_reads"]["in_scope"])

    incomplete_read_agents = (
        set(missing_counts)
        | missing_transcripts
        | agent_transcript_parse_gaps
        | unresolved_evidence
    )
    # A partial actor transcript cannot establish an exact read count, even
    # zero. Preserve its usable token usage and other actors' complete counts.
    for row in agent_usage:
        if row["agent"] in incomplete_read_agents:
            row["repository_reads"] = None
    # Two independent completeness axes: whether every expected transcript
    # was observed and classified (per actor family), and — for the reads
    # partition only — whether an authoritative scope mapping backed the
    # in/out-of-scope classification of each regular reviewer.
    #
    # Two family partitions of the same incomplete set, because scope-exempt
    # reviewers straddle them: for builder/artifact metrics they are regular
    # reviewers (synthesis identity is the split), while their reads route
    # to the non-scope-comparable bucket (the read routing set is the
    # split). Each completeness flag must partition by the same set its
    # metric routes by.
    evidence_observed = expected_available and not expected_invalid
    regular_transcripts_complete = evidence_observed and not (
        incomplete_read_agents - _NON_SCOPE_COMPARABLE_AGENTS
    )
    synthesis_transcripts_complete = evidence_observed and not (
        incomplete_read_agents & _NON_SCOPE_COMPARABLE_AGENTS
    )
    scope_comparable_reads_complete = (
        evidence_observed
        and not (incomplete_read_agents - _NON_SCOPE_COMPARABLE_READ_AGENTS)
        and not missing_scope_evidence
    )
    non_scope_comparable_reads_complete = evidence_observed and not (
        incomplete_read_agents & _NON_SCOPE_COMPARABLE_READ_AGENTS
    )
    agent_data_complete = (
        regular_transcripts_complete and synthesis_transcripts_complete
    )
    usage_complete = main_data_complete and agent_data_complete
    correlation = {
        "expected_available": expected_available,
        "expected": expected,
        "expected_by_agent": _sorted_counts(expected_counts),
        "correlated": correlated_names,
        "correlated_by_agent": _sorted_counts(correlated_counts),
        "missing": missing,
        "missing_by_agent": _sorted_counts(missing_counts),
        "missing_transcripts": sorted(missing_transcripts),
        "expected_count": sum(expected_counts.values()),
        "correlated_count": sum(correlated_counts.values()),
        "missing_count": sum(missing_counts.values()),
        "complete": agent_data_complete,
    }
    builder_observed = any(
        item["builder_attempted"] for item in artifact_by_agent
    )
    # Builder metrics measure regular reviewers only — a complete run whose
    # expected agents are all synthesis identities has nothing to observe
    # and is available-and-empty, not missing.
    expected_regular_reviewers = [
        agent
        for agent in expected_counts
        if agent not in _NON_SCOPE_COMPARABLE_AGENTS
    ]
    # Builder compliance is regular-reviewer evidence only — a missing
    # synthesis transcript must not downgrade fully observed reviewer data.
    artifact_available = bool(artifact_by_agent) or (
        regular_transcripts_complete and not expected_regular_reviewers
    )
    artifact_writes = {
        "available": artifact_available,
        "complete": regular_transcripts_complete,
        "builder_attempted": (
            True
            if builder_observed
            else (False if regular_transcripts_complete else None)
        ),
        "builder_attempts": sum(
            item["builder_attempts"] for item in artifact_by_agent
        ),
        "builder_successes": sum(
            item["builder_successes"] for item in artifact_by_agent
        ),
        "builder_failures": sum(
            item["builder_failures"] for item in artifact_by_agent
        ),
        "recovered": any(item["recovered"] for item in artifact_by_agent),
        "by_agent": artifact_by_agent,
    }
    observed_reads = {
        "schema": _OBSERVED_READS_SCHEMA,
        "all": sorted(read_all),
        "in_scope": sorted(read_in_scope),
        "out_of_scope": sorted(read_all - read_in_scope),
        "non_scope_comparable": sorted(read_non_scope_comparable),
        "exhaustive": False,
        "scope_comparable_transcript_data_complete": (
            scope_comparable_reads_complete
        ),
        "non_scope_comparable_transcript_data_complete": (
            non_scope_comparable_reads_complete
        ),
        "transcript_data_complete": (
            usage_complete and scope_comparable_reads_complete
        ),
    }
    completeness = {
        "orchestrator_data": main_data_complete and stage_timeline_complete,
        "agent_data": agent_data_complete,
        "usage": usage_complete,
        "tool_failures": usage_complete,
        "artifact_writes": regular_transcripts_complete,
        "scope_comparable_reads": scope_comparable_reads_complete,
        "non_scope_comparable_reads": non_scope_comparable_reads_complete,
        "observed_reads": usage_complete and scope_comparable_reads_complete,
    }
    return {
        "available": True,
        "reason": None,
        "warnings": warnings,
        "correlation": correlation,
        "agent_data_complete": agent_data_complete,
        "usage_complete": usage_complete,
        "completeness": completeness,
        "orchestrator_usage_by_step": orchestrator_usage_by_step,
        "agent_usage": agent_usage,
        "usage": total_usage,
        "tool_failures": failures,
        "artifact_writes": artifact_writes,
        "observed_reads": observed_reads,
    }

#!/usr/bin/env python3
"""Privacy-preserving enrichment for review pipeline transcripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


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
# The builder envelope's identity is its heredoc shape plus these four
# assignments, which every generation of bootstrap has emitted. Mirrors
# session_analyzer's pair of the same name.
_BOOTSTRAP_BUILDER_ENV_REQUIRED = frozenset({
    "PIRATEGOAT_PLUGIN_ROOT",
    "PIRATEGOAT_OUTPUT_DIR",
    "PIRATEGOAT_REVIEWER_NAME",
    "PIRATEGOAT_PR_ID",
})
# 1.114.0 appended the producing plugin version. It is additive and no
# measurement here reads its value — recognition is the only thing the
# envelope is used for — so BOTH generations are equally measurable and
# both are recognized. Rejecting the older form would report
# `builder_attempted: false` for saves that demonstrably happened, and a
# measured false is a wrong answer, not a missing one. Historical
# transcripts are immutable; a reader that stops recognizing them does not
# drop them from the cohort, it lies about them.
_BOOTSTRAP_BUILDER_ENV_OPTIONAL = frozenset({"PIRATEGOAT_PLUGIN_VERSION"})
_BOOTSTRAP_BUILDER_ENV = frozenset(
    _BOOTSTRAP_BUILDER_ENV_REQUIRED | _BOOTSTRAP_BUILDER_ENV_OPTIONAL
)
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


def _opaque_target(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "none"
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"opaque:{digest}"


def _is_bootstrap_builder_heredoc(command: object) -> bool:
    """Recognize the pipeline-owned builder attempt envelope."""
    if not isinstance(command, str):
        return False
    lines = command.splitlines()
    first_line = lines[0] if lines else ""
    try:
        tokens = shlex.split(first_line)
    except ValueError:
        return False
    if tokens[-2:] != ["python3", "<<PY"]:
        return False
    assignments = tokens[:-2]
    if not (
        len(_BOOTSTRAP_BUILDER_ENV_REQUIRED)
        <= len(assignments)
        <= len(_BOOTSTRAP_BUILDER_ENV)
    ):
        return False

    names: list[str] = []
    for token in assignments:
        name, separator, _value = token.partition("=")
        if separator != "=":
            return False
        names.append(name)
    unique = set(names)
    if len(unique) != len(names):
        return False
    # Every required name present, and nothing beyond the known optional
    # one: a foreign assignment means this is not an envelope any bootstrap
    # generation emitted, and its contents are not ours to interpret.
    return _BOOTSTRAP_BUILDER_ENV_REQUIRED <= unique <= _BOOTSTRAP_BUILDER_ENV


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
                if _is_bootstrap_builder_heredoc(command)
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


def _simple_bash_read_paths(command: object) -> list[str]:
    tokens = _shell_tokens(command)
    if tokens is None:
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
            recovered = key in success_keys or (
                operation == "builder_output_attempt"
                and name_op in success_name_ops
            )
            failures.append(
                {
                    "category": item["category"],
                    "detector": item["detector"],
                    "tool": name if name in _SAFE_TOOL_NAMES else "Other",
                    "operation_class": operation,
                    "normalized_target": target,
                    "recovered": recovered,
                    "recovery": "later_success" if recovered else "none",
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
            candidates = _simple_bash_read_paths(call["input"].get("command"))
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

    An absent mapping (no coverage, no by_agent, no entry for the agent) is
    NOT an empty scope: classifying reads against it would report every
    read as out-of-scope while claiming completeness.
    """
    coverage = manifest.get("coverage")
    by_agent = coverage.get("by_agent") if isinstance(coverage, dict) else None
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

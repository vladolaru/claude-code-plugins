#!/usr/bin/env python3
"""
Analyze reviewer agent subagent logs from Claude Code sessions.

Extracts detailed tool call sequences, categorizes behavior patterns,
and generates metrics for identifying inefficiencies in reviewer agents.

Modes:
    Default: Tool call sequence analysis and behavior categorization.
    --quality-metrics: Per-agent quality metrics (finding counts, overlap
        detection, severity disagreements).

Usage:
    # Analyze all patterns-reviewer dispatches from the last 20 sessions
    python3 analyze-reviewer-sessions.py \
        --sessions-dir ~/.claude/projects/-Users-vladolaru-Work-a8c-ciab-admin \
        --agent patterns-reviewer \
        --limit 20

    # Analyze a specific agent with JSON output
    python3 analyze-reviewer-sessions.py \
        --sessions-dir ~/.claude/projects/-Users-vladolaru-Work-a8c-ciab-admin \
        --agent security-reviewer \
        --format json

    # Analyze all agents in the most recent 5 sessions
    python3 analyze-reviewer-sessions.py \
        --sessions-dir ~/.claude/projects/-Users-vladolaru-Work-a8c-ciab-admin \
        --limit 5

    # Quality metrics for all agents
    python3 analyze-reviewer-sessions.py \
        --sessions-dir ~/.claude/projects/-Users-vladolaru-Work-a8c-ciab-admin \
        --quality-metrics \
        --format json
"""

import argparse
import datetime
import json
import os
from pathlib import Path
import posixpath
import re
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from glob import glob
from typing import Any

# Sibling module in scripts/analysis — the envelope parser that names the
# artifact one builder save produced.
_ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_ANALYSIS_DIR))
sys.path.insert(0, _ANALYSIS_DIR)
from review.review_document import (  # noqa: E402
    load_review_document,
    validate_review_document,
)
from review.reviewer_lifecycle import review_paths  # noqa: E402
from review.telemetry import ReviewTelemetry  # noqa: E402
from review_transcript import parse_builder_envelope  # noqa: E402


@lru_cache(maxsize=None)
def _artifact_session_id(output_dir: str) -> str | None:
    """The session the output directory's run manifest names, or None.

    Output directories are reused across runs of the same PR or branch and
    swept at step 1, so the artifact on disk belongs to the LATEST run. The
    manifest is the durable record of which session that was, and it lives
    wherever telemetry put it — resolved through the same marker file
    ReviewTelemetry itself reads, never by scanning the output directory.

    Cached because every subagent transcript of one run resolves the same
    output directory, and the manifest is the largest artifact beside it.
    This is a short-lived offline CLI reading artifacts a finished run left
    behind, so one read per directory is exact.
    """
    try:
        manifest_path = ReviewTelemetry(output_dir).manifest_path
        if manifest_path is None:
            return None
        with open(manifest_path, "rb") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return None
    run = manifest.get("run") if isinstance(manifest, dict) else None
    session_id = run.get("session_id") if isinstance(run, dict) else None
    return session_id if isinstance(session_id, str) and session_id else None


def _review_from_artifact(
    envelope: dict[str, str], session_id: str | None
) -> dict[str, Any] | None:
    """Return the saved review one builder envelope names, or None.

    Compliant reviewers save through ReviewOutputBuilder inside a mandated
    Bash heredoc, so the serialized review never appears in the transcript.
    The artifact it wrote is the evidence, and the pipeline's own validator
    is the one that decides whether it is readable. Absent or invalid is
    unmeasured — no record, so nothing downstream reports a zero for a
    reviewer whose output was never observed.

    The artifact must also be THIS dispatch's. When the transcript's session
    is known, the directory's manifest must name the same session: a
    different one means a later run replaced the file, and no provenance at
    all means nothing proves the file is this run's — both are unmeasured
    rather than credited with a possibly foreign run's findings.
    """
    reviewer = envelope["reviewer"]
    output_dir = envelope["output_dir"]
    try:
        path = review_paths(output_dir, reviewer).final
        document = load_review_document(path, reviewer)
    except ValueError:
        return None
    if session_id and _artifact_session_id(output_dir) != session_id:
        return None
    return {"path": path, "content": json.dumps(document)}


def _session_id_for_transcript(filepath: str) -> str | None:
    """The session a subagent transcript belongs to, from its location:
    ``<sessions-dir>/<session-id>/subagents/agent-<id>.jsonl``."""
    parent = Path(filepath).resolve().parent
    if parent.name != "subagents":
        return None
    return parent.parent.name or None


def parse_subagent_log(filepath: str) -> dict[str, Any]:
    """Parse a subagent JSONL file and extract detailed metrics."""
    entries = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    result: dict[str, Any] = {
        "file": os.path.basename(filepath),
        "file_size": os.path.getsize(filepath),
        "entry_count": len(entries),
        "prompt_content": "",
        "model": None,
        "tool_calls": [],
        "files_read": [],
        "bash_commands": [],
        "write_outputs": [],
        "final_texts": [],
    }

    # Write records are literal transcript evidence for every file a dispatch
    # produced; the review itself is not among them, because the mandated
    # builder heredoc writes it to disk. Collect the envelopes and read their
    # artifacts once the transcript is exhausted.
    # A save whose paired tool result is a definite failure persisted
    # nothing of its own. For a builder envelope the artifact it names may
    # still exist because a retry in another dispatch wrote it, and reading
    # it here would count that reviewer twice; for a Write the attempted
    # payload would shadow an earlier save that did land. Ambiguity (no
    # result observed) keeps the record.
    builder_envelopes: list[tuple[str | None, dict[str, str]]] = []
    failed_call_ids: set[str] = set()
    write_saves: list[tuple[str | None, dict[str, Any]]] = []
    session_id = _session_id_for_transcript(filepath)

    for entry in entries:
        msg = entry.get("message", {})
        if isinstance(msg, str):
            continue

        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user" and isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("is_error") is True
                    and isinstance(block.get("tool_use_id"), str)
                ):
                    failed_call_ids.add(block["tool_use_id"])

        # First user message = prompt
        if role == "user" and not result["prompt_content"]:
            if isinstance(content, str):
                result["prompt_content"] = content
            elif isinstance(content, list):
                parts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif isinstance(c, str):
                        parts.append(c)
                result["prompt_content"] = "\n".join(parts)

        # Track model from assistant entries
        if entry.get("type") == "assistant" and isinstance(msg, dict) and msg.get("model"):
            result["model"] = msg.get("model")

        # Tool use in assistant messages
        if role == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue

                if block.get("type") == "tool_use":
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {})
                    result["tool_calls"].append(
                        _categorize_tool_call(tool_name, tool_input)
                    )

                    if tool_name == "Read":
                        result["files_read"].append(tool_input.get("file_path", ""))
                    elif tool_name == "Bash":
                        command = tool_input.get("command", "")
                        result["bash_commands"].append(command)
                        envelope = parse_builder_envelope(command)
                        if envelope is not None:
                            call_id = block.get("id")
                            builder_envelopes.append(
                                (call_id if isinstance(call_id, str) else None, envelope)
                            )
                    elif tool_name == "Write":
                        call_id = block.get("id")
                        write_saves.append((
                            call_id if isinstance(call_id, str) else None,
                            {
                                "path": tool_input.get("file_path", ""),
                                "content": tool_input.get("content", ""),
                            },
                        ))

                elif block.get("type") == "text":
                    result["final_texts"].append(block.get("text", ""))

        if role == "assistant" and isinstance(content, str):
            result["final_texts"].append(content)

    # Same-artifact Writes overwrite each other regardless of transcript
    # order — a corrected rewrite must count once, as its final content, not
    # as an extra dispatch with duplicated findings. Pathless or non-string
    # paths carry no artifact identity and are kept as-is, undeduped.
    def _path_key(raw: object) -> str | None:
        if not isinstance(raw, str) or not raw:
            return None
        return posixpath.normpath(raw)

    deduped_write_saves: list[dict[str, Any]] = []
    write_index_by_path: dict[str, int] = {}
    for call_id, record in write_saves:
        if call_id in failed_call_ids:
            continue
        key = _path_key(record["path"])
        if key is None:
            deduped_write_saves.append(record)
            continue
        if key in write_index_by_path:
            deduped_write_saves[write_index_by_path[key]] = record
        else:
            write_index_by_path[key] = len(deduped_write_saves)
            deduped_write_saves.append(record)

    # One artifact, one record, whatever the transport: repeated saves in one
    # transcript all name the same file, and a legacy Write of that path is
    # superseded by what the file actually holds. Both transports compare
    # through the same normalized key so spelling never splits one file.
    artifact_saves: dict[str, dict[str, Any]] = {}
    for call_id, envelope in builder_envelopes:
        if call_id in failed_call_ids:
            continue
        record = _review_from_artifact(envelope, session_id)
        if record is not None:
            artifact_saves.setdefault(_path_key(record["path"]), record)
    result["write_outputs"] = [
        record for record in deduped_write_saves
        if _path_key(record["path"]) not in artifact_saves
    ] + list(artifact_saves.values())

    return result


def _categorize_tool_call(tool_name: str, tool_input: dict) -> dict[str, Any]:
    """Categorize a tool call into a structured record."""
    detail: dict[str, Any] = {"tool": tool_name}

    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        detail["command"] = cmd

        if parse_builder_envelope(cmd) is not None:
            detail["category"] = "builder-output"
        elif "git grep" in cmd:
            detail["category"] = "git-grep"
            m = re.search(r'git grep[^"]*"([^"]*)"', cmd)
            detail["pattern"] = m.group(1) if m else cmd[:80]
        elif "git log" in cmd:
            detail["category"] = "git-log"
        elif "git show" in cmd:
            detail["category"] = "git-show"
            parts = cmd.split()
            idx = parts.index("show") if "show" in parts else -1
            detail["target"] = parts[idx + 1] if idx >= 0 and idx + 1 < len(parts) else ""
        elif "git diff" in cmd:
            detail["category"] = "git-diff"
        elif "python3" in cmd or "bootstrap" in cmd:
            detail["category"] = "bootstrap"
        elif any(x in cmd for x in ["cat ", "head ", "tail ", "wc "]):
            detail["category"] = "file-read-bash"
        elif "ls " in cmd or "find " in cmd:
            detail["category"] = "file-list"
        else:
            detail["category"] = "other"
    elif tool_name == "Read":
        detail["file_path"] = tool_input.get("file_path", "")
    elif tool_name == "Grep":
        detail["pattern"] = tool_input.get("pattern", "")
        detail["path"] = tool_input.get("path", "")
    elif tool_name == "Glob":
        detail["pattern"] = tool_input.get("pattern", "")
    elif tool_name == "Write":
        detail["file_path"] = tool_input.get("file_path", "")
        detail["content_size"] = len(tool_input.get("content", ""))

    return detail


def find_agent_dispatches(
    sessions_dir: str, agent_name: str | None = None, max_sessions: int = 50
) -> list[dict[str, Any]]:
    """Find all subagent dispatch files matching the agent name."""
    session_files = sorted(
        glob(os.path.join(sessions_dir, "*.jsonl")),
        key=lambda f: os.path.getmtime(f),
        reverse=True,
    )

    results = []
    for sf in session_files[:max_sessions]:
        sid = os.path.basename(sf).replace(".jsonl", "")
        subagents_dir = os.path.join(sessions_dir, sid, "subagents")
        if not os.path.isdir(subagents_dir):
            continue

        for agent_file in glob(os.path.join(subagents_dir, "*.jsonl")):
            try:
                with open(agent_file) as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        msg = data.get("message", {})
                        if isinstance(msg, dict):
                            content = msg.get("content", "")
                            if isinstance(content, str):
                                # If no agent filter, include all; otherwise filter
                                if agent_name is None or agent_name in content:
                                    mtime = os.path.getmtime(sf)
                                    dt = datetime.datetime.fromtimestamp(mtime).strftime(
                                        "%Y-%m-%d %H:%M"
                                    )
                                    results.append({
                                        "session_id": sid,
                                        "agent_file": agent_file,
                                        "file_size": os.path.getsize(agent_file),
                                        "date": dt,
                                        "mtime": mtime,
                                    })
            except (json.JSONDecodeError, OSError):
                continue

    results.sort(key=lambda x: x["mtime"], reverse=True)
    return results


def classify_dispatch(data: dict[str, Any]) -> str:
    """Classify whether this is a reviewer dispatch or a reconciliator dispatch."""
    def is_reviewer_render(path: str) -> bool:
        parts = path.replace("\\", "/").split("/")
        return (
            len(parts) >= 3
            and parts[-3] == "reviewers"
            and parts[-1] in {"review.json", "review.md"}
        )

    # Reconciliator dispatches: read many reviewers/*/review.json files.
    review_json_reads = sum(
        1 for f in data["files_read"] if is_reviewer_render(f)
    )
    bash_count = len(data["bash_commands"])

    if review_json_reads >= 5 and bash_count <= 5:
        return "reconciliator"

    # Reviewer dispatches: heavy git command usage, bootstrap call
    git_commands = sum(
        1 for c in data["bash_commands"] if any(x in c for x in ["git grep", "git show", "git log", "git diff"])
    )
    if git_commands > 5:
        return "reviewer"

    # Check for API crashes
    if data["final_texts"]:
        last = data["final_texts"][-1]
        if "API Error" in last or "api_error" in last:
            return "crashed"

    return "unknown"


def format_text_report(dispatches: list[tuple[dict, dict]], agent_name: str | None) -> str:
    """Format a human-readable text report."""
    lines = []
    lines.append("=" * 100)
    lines.append(f"REVIEWER SESSION ANALYSIS — {agent_name or 'all agents'}")
    lines.append(f"Dispatches: {len(dispatches)}")
    lines.append("=" * 100)

    # Aggregate counters
    total_tool_calls = 0
    category_totals: Counter = Counter()
    all_files_read: list[str] = []
    dispatch_types: Counter = Counter()

    for meta, data in dispatches:
        dispatch_type = classify_dispatch(data)
        dispatch_types[dispatch_type] += 1

        total_tool_calls += len(data["tool_calls"])
        for tc in data["tool_calls"]:
            cat = tc.get("category", tc["tool"])
            category_totals[cat] += 1
        all_files_read.extend(data["files_read"])

        lines.append("")
        lines.append("=" * 100)
        lines.append(
            f"DISPATCH: {meta['session_id'][:8]}  ({meta['date']})  [{dispatch_type.upper()}]"
        )
        lines.append(
            f"File: {data['file']}  |  Size: {data['file_size']:,} bytes  |  "
            f"Entries: {data['entry_count']}  |  Model: {data['model']}"
        )
        lines.append(f"Prompt: {len(data['prompt_content']):,} chars")
        lines.append(f"Total tool calls: {len(data['tool_calls'])}")

        # Tool breakdown
        cats: Counter = Counter()
        for tc in data["tool_calls"]:
            cats[tc.get("category", tc["tool"])] += 1
        for cat, count in cats.most_common():
            lines.append(f"  {cat}: {count}")

        # Tool sequence
        lines.append("")
        lines.append("Tool sequence:")
        for i, tc in enumerate(data["tool_calls"]):
            tool = tc["tool"]
            if tool == "Bash":
                cat = tc.get("category", "other")
                if cat == "git-grep":
                    lines.append(f"  {i + 1:3d}. git grep: {tc.get('pattern', '')[:100]}")
                elif cat == "git-show":
                    lines.append(f"  {i + 1:3d}. git show: {tc.get('target', '')[:80]}")
                elif cat == "git-log":
                    lines.append(f"  {i + 1:3d}. git log: {tc['command'][:120]}")
                elif cat == "git-diff":
                    lines.append(f"  {i + 1:3d}. git diff: {tc['command'][:120]}")
                else:
                    lines.append(f"  {i + 1:3d}. bash({cat}): {tc['command'][:120]}")
            elif tool == "Read":
                fp = tc.get("file_path", "")
                lines.append(f"  {i + 1:3d}. Read: {fp[-100:]}")
            elif tool == "Write":
                lines.append(
                    f"  {i + 1:3d}. Write: {tc.get('file_path', '')[-60:]} "
                    f"({tc.get('content_size', 0):,} chars)"
                )
            elif tool == "Grep":
                lines.append(f"  {i + 1:3d}. Grep: {tc.get('pattern', '')} in {tc.get('path', '')}")
            else:
                lines.append(f"  {i + 1:3d}. {tool}")

        # Output summary
        lines.append("")
        if data["write_outputs"]:
            for wo in data["write_outputs"]:
                content = wo["content"]
                # A save that parses as a review payload carries its exact
                # findings list — count it directly. The keyword heuristic is
                # only for prose saves: applied to a real review's JSON it
                # miscounts, so a validated save is always counted exactly
                # rather than estimated by counting '"id"' occurrences.
                review_json = _parse_review_write_output(wo)
                if review_json is not None:
                    count_display = f"{len(review_json['findings'])} findings"
                else:
                    finding_count = content.count("## Finding") + content.count("### PAT-") + content.count('"id"')
                    count_display = f"~{finding_count} findings"
                lines.append(
                    f"Output: {wo['path'][-60:]} ({len(content):,} chars, {count_display})"
                )
        elif data["final_texts"]:
            last = data["final_texts"][-1]
            lines.append(f"Final text ({len(last):,} chars): {last[:300]}")

    # Aggregate summary
    lines.append("")
    lines.append("=" * 100)
    lines.append("AGGREGATE SUMMARY")
    lines.append("=" * 100)
    lines.append(f"Total dispatches: {len(dispatches)}")
    lines.append(f"  By type: {dict(dispatch_types)}")
    lines.append(f"Total tool calls: {total_tool_calls} (avg {total_tool_calls / max(len(dispatches), 1):.1f})")
    lines.append("")
    lines.append("Tool call breakdown:")
    for cat, count in category_totals.most_common():
        lines.append(f"  {cat}: {count}")

    # Most-read files
    file_counts = Counter(all_files_read)
    lines.append("")
    lines.append("Most frequently read files:")
    for fp, count in file_counts.most_common(15):
        lines.append(f"  {count}x  {fp[-80:]}")

    return "\n".join(lines)


def format_json_report(dispatches: list[tuple[dict, dict]], agent_name: str | None) -> str:
    """Format a JSON report for programmatic consumption."""
    records = []
    for meta, data in dispatches:
        dispatch_type = classify_dispatch(data)
        cats: Counter = Counter()
        for tc in data["tool_calls"]:
            cats[tc.get("category", tc["tool"])] += 1

        records.append({
            "session_id": meta["session_id"],
            "date": meta["date"],
            "dispatch_type": dispatch_type,
            "model": data["model"],
            "file_size": data["file_size"],
            "entry_count": data["entry_count"],
            "prompt_size": len(data["prompt_content"]),
            "total_tool_calls": len(data["tool_calls"]),
            "tool_breakdown": dict(cats),
            "files_read_count": len(data["files_read"]),
            "unique_files_read": len(set(data["files_read"])),
            "write_count": len(data["write_outputs"]),
            "output_sizes": [len(wo["content"]) for wo in data["write_outputs"]],
        })

    return json.dumps({"agent": agent_name, "dispatches": records}, indent=2)


# ---------------------------------------------------------------------------
# Quality metrics extraction functions
# ---------------------------------------------------------------------------

def extract_agent_findings(write_output: Any) -> dict[str, Any]:
    """Parse agent review JSON output and extract finding counts.

    Args:
        write_output: A dict matching the ReviewOutputBuilder.to_dict() schema,
            or any other value (gracefully handled).

    Returns:
        Dict with keys: total_findings, findings_by_severity, findings.
    """
    empty = {
        "total_findings": 0,
        "findings_by_severity": {},
        "findings": [],
    }

    if not isinstance(write_output, dict):
        return empty

    review_findings = write_output.get("findings", [])
    if not isinstance(review_findings, list):
        return empty

    # Count directly from canonical findings.
    severity_counts: dict[str, int] = {}
    for finding in review_findings:
        if not isinstance(finding, dict):
            continue
        sev = finding.get("severity", "unknown")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    # Normalize finding dicts to ensure overlap fields are present.
    normalized_findings = []
    for finding in review_findings:
        if not isinstance(finding, dict):
            continue
        normalized_findings.append({
            "id": finding.get("id", ""),
            "file": finding.get("file", ""),
            "line": finding.get("line"),
            "severity": finding.get("severity", "unknown"),
            "title": finding.get("title", ""),
            "description": finding.get("description", ""),
            "recommendation": finding.get("recommendation", ""),
            "confidence": finding.get("confidence", 0.0),
        })

    return {
        "total_findings": len(normalized_findings),
        "findings_by_severity": severity_counts,
        "findings": normalized_findings,
    }


def _parse_review_write_output(write_output: Any) -> dict[str, Any] | None:
    """Return a validated reviewer result from one captured save record."""
    if not isinstance(write_output, dict):
        return None

    path = write_output.get("path")
    if not isinstance(path, str):
        return None
    normalized_path = path.replace("\\", "/")
    parts = normalized_path.rsplit("/", 3)
    if len(parts) < 3 or parts[-1] != "review.json" or parts[-3] != "reviewers":
        return None

    try:
        review_json = json.loads(write_output.get("content", ""))
    except (json.JSONDecodeError, TypeError):
        return None

    reviewer = parts[-2]
    try:
        validate_review_document(review_json, reviewer)
    except ValueError:
        return None

    return review_json


def detect_overlaps(all_findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Find findings from 2+ agents at the same file+line.

    Args:
        all_findings: List of dicts, each with keys: agent, file, line, severity, title.
            Findings with line=None are excluded from overlap detection.

    Returns:
        Dict with:
            overlap_clusters: int — number of file+line locations flagged by 2+ agents
            severity_disagreements: int — clusters where agents assigned different severities
            clusters: list of dicts with file, line, agents, severities for each cluster
    """
    empty = {"overlap_clusters": 0, "severity_disagreements": 0, "clusters": []}

    if not all_findings:
        return empty

    # Group findings by (file, line) — skip entries with no line
    location_map: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for finding in all_findings:
        if not isinstance(finding, dict):
            continue
        file_path = finding.get("file", "")
        line = finding.get("line")
        if line is None or not file_path:
            continue
        location_map[(file_path, line)].append(finding)

    clusters = []
    severity_disagreements = 0

    for (file_path, line), group in location_map.items():
        # Only locations with 2+ *distinct* agents count as overlaps
        agents = set()
        for f in group:
            agents.add(f.get("agent", "unknown"))
        if len(agents) < 2:
            continue

        severities = set()
        for f in group:
            severities.add(f.get("severity", "unknown"))

        has_disagreement = len(severities) > 1
        if has_disagreement:
            severity_disagreements += 1

        clusters.append({
            "file": file_path,
            "line": line,
            "agents": sorted(agents),
            "severities": sorted(severities),
            "finding_count": len(group),
        })

    return {
        "overlap_clusters": len(clusters),
        "severity_disagreements": severity_disagreements,
        "clusters": clusters,
    }


def format_quality_text_report(
    dispatches: list[tuple[dict, dict]],
    agent_name: str | None,
) -> str:
    """Format a human-readable quality metrics report."""
    lines = []
    lines.append("=" * 100)
    lines.append(f"QUALITY METRICS REPORT — {agent_name or 'all agents'}")
    lines.append(f"Dispatches analyzed: {len(dispatches)}")
    lines.append("=" * 100)

    # Collect per-agent findings across all dispatches
    agent_totals: dict[str, dict] = defaultdict(lambda: {
        "dispatches": 0,
        "total_findings": 0,
        "findings_by_severity": Counter(),
    })
    all_findings_for_overlap: list[dict] = []

    for meta, data in dispatches:
        dispatch_type = classify_dispatch(data)
        if dispatch_type not in ("reviewer", "unknown"):
            continue  # skip reconciliator and crashed dispatches

        # Try to extract findings from Write outputs
        for wo in data.get("write_outputs", []):
            review_json = _parse_review_write_output(wo)
            if review_json is None:
                continue

            reviewer = review_json["reviewer"]
            findings = extract_agent_findings(review_json)

            agent_totals[reviewer]["dispatches"] += 1
            agent_totals[reviewer]["total_findings"] += findings["total_findings"]
            agent_totals[reviewer]["findings_by_severity"].update(findings["findings_by_severity"])

            # Tag each finding with agent name for overlap detection.
            for finding in findings["findings"]:
                finding["agent"] = reviewer
                all_findings_for_overlap.append(finding)

    # Per-agent summary
    lines.append("")
    lines.append("PER-AGENT SUMMARY")
    lines.append("-" * 60)
    for agent, stats in sorted(agent_totals.items()):
        lines.append(f"  {agent}:")
        lines.append(f"    Dispatches: {stats['dispatches']}")
        lines.append(f"    Total findings: {stats['total_findings']}")
        if stats["findings_by_severity"]:
            sev_parts = [f"{k}={v}" for k, v in sorted(stats["findings_by_severity"].items())]
            lines.append(f"    By severity: {', '.join(sev_parts)}")

    # Overlap detection
    overlaps = detect_overlaps(all_findings_for_overlap)
    lines.append("")
    lines.append("OVERLAP ANALYSIS")
    lines.append("-" * 60)
    lines.append(f"  Overlap clusters (2+ agents, same file+line): {overlaps['overlap_clusters']}")
    lines.append(f"  Severity disagreements: {overlaps['severity_disagreements']}")
    if overlaps["clusters"]:
        lines.append("")
        for cluster in overlaps["clusters"]:
            lines.append(
                f"    {cluster['file']}:{cluster['line']}  "
                f"agents={cluster['agents']}  severities={cluster['severities']}"
            )

    return "\n".join(lines)


def format_quality_json_report(
    dispatches: list[tuple[dict, dict]],
    agent_name: str | None,
) -> str:
    """Format a JSON quality metrics report."""
    agent_records: dict[str, dict] = {}
    all_findings_for_overlap: list[dict] = []

    for meta, data in dispatches:
        dispatch_type = classify_dispatch(data)
        if dispatch_type not in ("reviewer", "unknown"):
            continue

        for wo in data.get("write_outputs", []):
            review_json = _parse_review_write_output(wo)
            if review_json is None:
                continue

            reviewer = review_json["reviewer"]
            findings = extract_agent_findings(review_json)

            if reviewer not in agent_records:
                agent_records[reviewer] = {
                    "agent_name": reviewer,
                    "dispatches": 0,
                    "total_findings": 0,
                    "findings_by_severity": Counter(),
                }

            agent_records[reviewer]["dispatches"] += 1
            agent_records[reviewer]["total_findings"] += findings["total_findings"]
            agent_records[reviewer]["findings_by_severity"].update(findings["findings_by_severity"])

            for finding in findings["findings"]:
                finding["agent"] = reviewer
                all_findings_for_overlap.append(finding)

    overlaps = detect_overlaps(all_findings_for_overlap)

    # Convert Counter to plain dict for JSON serialization
    per_agent = []
    for rec in sorted(agent_records.values(), key=lambda r: r["agent_name"]):
        per_agent.append({
            "agent_name": rec["agent_name"],
            "dispatches": rec["dispatches"],
            "total_findings": rec["total_findings"],
            "findings_by_severity": dict(rec["findings_by_severity"]),
        })

    report = {
        "agent_filter": agent_name,
        "dispatches_analyzed": len(dispatches),
        "per_agent": per_agent,
        "overlap_clusters": overlaps["overlap_clusters"],
        "severity_disagreements": overlaps["severity_disagreements"],
        "clusters": overlaps["clusters"],
    }

    return json.dumps(report, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze reviewer agent subagent logs from Claude Code sessions."
    )
    parser.add_argument(
        "--sessions-dir",
        required=True,
        help="Path to Claude Code sessions directory",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent name to filter (e.g., patterns-reviewer). Omit to include all.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of recent sessions to scan (default: 20)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write output to file instead of stdout",
    )
    parser.add_argument(
        "--quality-metrics",
        action="store_true",
        default=False,
        help="Quality metrics mode: extract per-agent finding counts, "
             "and overlap analysis",
    )

    args = parser.parse_args()

    if not os.path.isdir(args.sessions_dir):
        print(f"Error: sessions directory not found: {args.sessions_dir}", file=sys.stderr)
        sys.exit(1)

    # Find dispatches
    dispatches_meta = find_agent_dispatches(args.sessions_dir, args.agent, args.limit)
    if not dispatches_meta:
        print(f"No dispatches found for agent '{args.agent}' in {args.sessions_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(dispatches_meta)} dispatches, parsing...", file=sys.stderr)

    # Parse each dispatch
    parsed: list[tuple[dict, dict]] = []
    for meta in dispatches_meta:
        data = parse_subagent_log(meta["agent_file"])
        parsed.append((meta, data))

    # Format output — quality metrics mode or default mode
    if args.quality_metrics:
        if args.format == "json":
            output = format_quality_json_report(parsed, args.agent)
        else:
            output = format_quality_text_report(parsed, args.agent)
    else:
        if args.format == "json":
            output = format_json_report(parsed, args.agent)
        else:
            output = format_text_report(parsed, args.agent)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()

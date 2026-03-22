#!/usr/bin/env python3
"""
Extract operational metrics from Claude Code session transcripts.

Scans session directories for subagent (sub-session/sub-task) JSONL files,
extracts per-agent metrics (tokens, duration, findings, model), and produces
summary reports in Markdown and JSON formats.

Also extracts triage decisions from orchestrator sessions when the adaptive
agent dispatch system is in use (Adaptive Agent Triage step).

Usage:
    # Scan default Claude Code sessions directory for the current project
    python3 extract-session-metrics.py

    # Scan a specific sessions directory
    python3 extract-session-metrics.py --sessions-dir ~/.claude/projects/-Users-foo-myproject/

    # Filter to specific agent types
    python3 extract-session-metrics.py --agents security-reviewer,pr-reviewer

    # Output as JSON only
    python3 extract-session-metrics.py --format json

    # Limit to N most recent sessions
    python3 extract-session-metrics.py --limit 20

    # Show all subagents, not just reviewer agents
    python3 extract-session-metrics.py --all

    # Extract triage decisions and compare to actual dispatch outcomes
    python3 extract-session-metrics.py --triage
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# -- Known reviewer agent types --
KNOWN_REVIEWER_AGENTS = [
    "security-reviewer",
    "architecture-reviewer",
    "patterns-reviewer",
    "history-insights-reviewer",
    "dead-code-reviewer",
    "performance-reviewer",
    "pr-reviewer",
    "js-tests-reviewer",
    "php-tests-reviewer",
    "e2e-tests-reviewer",
    "wp-architecture-reviewer",
    "a11y-reviewer",
    "gemini-reviewer",
    "codex-reviewer",
    "go-tests-reviewer",
    "mutation-reviewer",
]

# Patterns to infer agent type from free-form prompts (older sessions
# that don't use bootstrap.py).  More-specific patterns must
# appear before less-specific ones (e.g. wp-architecture before architecture).
AGENT_INFERENCE_PATTERNS = {
    "wp-architecture": [
        r"WordPress architecture",
        r"WP architecture",
        r"wp-architecture",
    ],
    "security": [r"security issues", r"security review", r"for security"],
    "architecture": [
        r"architecture quality",
        r"architectural concerns",
        r"for architecture",
    ],
    "patterns": [
        r"pattern consistency",
        r"pattern adherence",
        r"codebase consistency",
        r"for patterns",
    ],
    "performance": [
        r"performance issues",
        r"for performance",
        r"performance review",
    ],
    "pr": [r"code quality.*security.*architectural", r"general review"],
    "dead-code": [r"dead code", r"unused code"],
    "history-insights": [r"history insights", r"historical patterns"],
    "js-tests": [r"JavaScript test", r"JS test", r"frontend test"],
    "php-tests": [r"PHP test", r"backend test"],
    "e2e-tests": [r"E2E test", r"end-to-end test", r"Playwright"],
    "a11y": [r"accessibility", r"a11y review"],
    "gemini": [r"gemini", r"Gemini"],
    "codex": [r"codex review", r"Codex"],
    "go-tests": [r"Go test", r"golang test"],
}

# Fingerprints for non-reviewer agents that might be misidentified by keyword
# inference.  Each key maps to a list of regexes tested against first_user_content
# with re.MULTILINE.  If ANY regex matches, the agent type is returned immediately
# (before keyword inference runs).
NON_REVIEWER_AGENT_FINGERPRINTS = {
    "reconciliator": [
        r"^Output Directory:.*\nMode:\s*(summary|focused)",
    ],
}

# Agents subject to LLM triage (Adaptive Agent Triage step)
TRIAGED_AGENTS = [
    "security-reviewer",
    "dead-code-reviewer",
    "architecture-reviewer",
    "wp-architecture-reviewer",
    "performance-reviewer",
    "a11y-reviewer",
]


def normalize_agent_name(raw: str) -> Optional[str]:
    """Normalize a potentially messy agent name from LLM output.

    LLMs may output agent names in various formats:
      - "security-reviewer" (correct)
      - "security" (missing -reviewer suffix)
      - "Security Reviewer" (title case)
      - "security_reviewer" (underscores)
      - "SECURITY-REVIEWER" (all caps)
      - "`security-reviewer`" (backticks)
      - "**security-reviewer**" (bold markdown)

    Returns the canonical agent name or None if unrecognizable.
    """
    if not raw:
        return None

    # Strip markdown formatting
    name = raw.strip().strip("`*_")
    # Lowercase and normalize separators
    name = name.lower().replace("_", "-").replace(" ", "-")
    # Remove duplicate hyphens
    name = re.sub(r"-+", "-", name).strip("-")

    # Try exact match first
    if name in KNOWN_REVIEWER_AGENTS:
        return name

    # Try with -reviewer suffix
    if not name.endswith("-reviewer"):
        with_suffix = name + "-reviewer"
        if with_suffix in KNOWN_REVIEWER_AGENTS:
            return with_suffix

    # Try partial match (e.g., "wp-arch" -> "wp-architecture-reviewer")
    for known in KNOWN_REVIEWER_AGENTS:
        # Match if the raw name is a prefix of the known name (minus -reviewer)
        known_base = known.replace("-reviewer", "")
        if known_base.startswith(name) or name.startswith(known_base):
            return known

    return None


def extract_triage_decisions(filepath: str) -> list:
    """Extract triage decisions from an orchestrator session JSONL file.

    Looks for TRIAGE lines in the orchestrator's output. Handles various
    LLM formatting inconsistencies:

    Expected format:
      TRIAGE: security-reviewer: DISPATCH — reason here
      TRIAGE: dead-code-reviewer: SKIP — reason here

    Also handles:
      - "TRIAGE:" with varying whitespace
      - Missing "TRIAGE:" prefix (bare "security-reviewer: DISPATCH")
      - Unicode dashes (—, –, -) in the separator
      - Agent names with/without -reviewer suffix
      - Decision words: DISPATCH/SKIP/dispatch/skip/Dispatch/Skip
      - Backtick-wrapped agent names
      - STATUS=SKIPPED_TRIAGE lines from reconciliator context

    Returns a list of dicts with keys:
      agent, decision, reason, session_id, line_number
    """
    decisions = []
    session_id = os.path.basename(os.path.dirname(filepath))

    # Primary pattern: TRIAGE: <agent>: <decision> [—-–] <reason>
    # Flexible: optional TRIAGE prefix, flexible separators, flexible agent names
    triage_pattern = re.compile(
        r"(?:TRIAGE\s*[:：]\s*)?"  # optional TRIAGE: prefix
        r"([a-zA-Z0-9_` *-]+?)"   # agent name (flexible)
        r"\s*[:：]\s*"              # separator
        r"(DISPATCH|SKIP|dispatch|skip|Dispatch|Skip|DISPATCHED|SKIPPED)"
        r"\s*"
        r"(?:[—–\-]\s*|\s+)"      # dash separator (unicode or ascii) or whitespace
        r"(.+)",                    # reason
        re.IGNORECASE,
    )

    # Secondary pattern: STATUS=SKIPPED_TRIAGE lines
    skipped_triage_pattern = re.compile(
        r"([a-zA-Z0-9_` *-]+?)"
        r"\s*[:：]\s*"
        r"STATUS\s*=\s*SKIPPED_TRIAGE"
        r"\s*"
        r"(?:\(\s*(.+?)\s*\))?"    # optional reason in parentheses
    )

    try:
        with open(filepath, "r") as f:
            for line_num, line in enumerate(f):
                # Only look in lines that contain triage keywords
                if "TRIAGE" not in line and "SKIPPED_TRIAGE" not in line:
                    continue

                # Extract text content from JSON to avoid matching JSON artifacts
                text_content = line
                try:
                    d = json.loads(line)
                    msg = d.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        text_content = content
                    elif isinstance(content, list):
                        texts = [
                            c.get("text", "")
                            for c in content
                            if isinstance(c, dict) and c.get("type") == "text"
                        ]
                        text_content = "\n".join(texts)
                except (json.JSONDecodeError, KeyError, AttributeError):
                    # Fall back to raw line if JSON parsing fails
                    pass

                # Try primary pattern
                for m in triage_pattern.finditer(text_content):
                    agent_raw = m.group(1).strip()
                    decision_raw = m.group(2).strip().upper()
                    reason = m.group(3).strip()

                    # Normalize decision
                    decision = "DISPATCH" if decision_raw.startswith("DISPATCH") else "SKIP"

                    # Normalize agent name
                    agent = normalize_agent_name(agent_raw)
                    if agent and agent in TRIAGED_AGENTS:
                        decisions.append({
                            "agent": agent,
                            "decision": decision,
                            "reason": reason,
                            "session_id": session_id,
                            "line_number": line_num + 1,
                        })

                # Try secondary pattern (SKIPPED_TRIAGE)
                for m in skipped_triage_pattern.finditer(text_content):
                    agent_raw = m.group(1).strip()
                    reason = m.group(2).strip() if m.group(2) else "no reason given"

                    agent = normalize_agent_name(agent_raw)
                    if agent and agent in TRIAGED_AGENTS:
                        # Avoid duplicates if primary pattern already caught this
                        already_found = any(
                            d["agent"] == agent and d["line_number"] == line_num + 1
                            for d in decisions
                        )
                        if not already_found:
                            decisions.append({
                                "agent": agent,
                                "decision": "SKIP",
                                "reason": reason,
                                "session_id": session_id,
                                "line_number": line_num + 1,
                            })

    except (IOError, OSError) as e:
        print(f"Error reading {filepath} for triage: {e}", file=sys.stderr)

    return decisions


def scan_triage_decisions(
    sessions_dir: str,
    limit: Optional[int] = None,
) -> list:
    """Scan orchestrator session files for triage decisions.

    Looks in the main session JSONL files (not subagent files) for triage
    lines emitted by the review orchestrator in the Adaptive Agent Triage step.
    """
    all_decisions = []
    sessions_checked = 0

    try:
        entries = os.listdir(sessions_dir)
    except OSError as e:
        print(f"Error listing sessions dir: {e}", file=sys.stderr)
        return all_decisions

    # Find session JSONL files (top-level, not in subagents/)
    session_files = []
    for entry in entries:
        filepath = os.path.join(sessions_dir, entry)
        if entry.endswith(".jsonl") and os.path.isfile(filepath):
            try:
                mtime = os.path.getmtime(filepath)
            except OSError:
                mtime = 0
            session_files.append((entry, filepath, mtime))

    # Also check for session dirs that contain a main JSONL
    for entry in entries:
        dirpath = os.path.join(sessions_dir, entry)
        if os.path.isdir(dirpath):
            main_jsonl = os.path.join(dirpath, f"{entry}.jsonl")
            if os.path.isfile(main_jsonl):
                try:
                    mtime = os.path.getmtime(main_jsonl)
                except OSError:
                    mtime = 0
                session_files.append((entry, main_jsonl, mtime))

    # Sort by most recent first
    session_files.sort(key=lambda x: x[2], reverse=True)

    if limit:
        session_files = session_files[:limit]

    print(
        f"Scanning {len(session_files)} session files for triage decisions...",
        file=sys.stderr,
    )

    for _, filepath, _ in session_files:
        decisions = extract_triage_decisions(filepath)
        if decisions:
            all_decisions.extend(decisions)
            sessions_checked += 1

    print(
        f"Found {len(all_decisions)} triage decisions across "
        f"{sessions_checked} sessions",
        file=sys.stderr,
    )
    return all_decisions


def identify_agent_type(filepath: str) -> Optional[str]:
    """
    Identify the agent type from a subagent JSONL file.

    Strategy:
    1. Look for bootstrap.py --agent <name> in first 15 lines
    2. Infer from user prompt keywords in first message
    3. Return None if unidentifiable (caller decides whether to include)
    """
    first_user_content = ""

    try:
        with open(filepath, "r") as f:
            for i, line in enumerate(f):
                if i > 15:
                    break

                # Strategy 1: bootstrap.py --agent <name>
                match = re.search(
                    r"bootstrap(?:-reviewer)?\.py\s+--agent\s+([a-z0-9-]+)", line
                )
                if match:
                    agent_name = match.group(1)
                    if not agent_name.endswith("-reviewer"):
                        agent_name += "-reviewer"
                    return agent_name

                # Collect first user message content for inference
                if i == 0:
                    try:
                        d = json.loads(line)
                        msg = d.get("message", {})
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            first_user_content = content
                        elif isinstance(content, list):
                            texts = [
                                c.get("text", "")
                                for c in content
                                if isinstance(c, dict) and c.get("type") == "text"
                            ]
                            first_user_content = " ".join(texts)
                    except (json.JSONDecodeError, KeyError):
                        pass
    except (IOError, OSError):
        return None

    # Strategy 1.5: detect non-reviewer agents by fingerprint
    if first_user_content:
        for agent_type, fingerprints in NON_REVIEWER_AGENT_FINGERPRINTS.items():
            for fp in fingerprints:
                if re.search(fp, first_user_content, re.MULTILINE):
                    return agent_type

    # Strategy 2: infer from prompt keywords
    if first_user_content:
        # Strip agent signal lines (e.g. "wp-architecture-reviewer: STATUS=COMPLETED")
        # to prevent false matches from reconciliator/orchestrator context
        cleaned_content = re.sub(
            r"^[a-z0-9-]+-reviewer:\s*STATUS=\S+.*$",
            "",
            first_user_content,
            flags=re.MULTILINE,
        )
        for agent_short, patterns in AGENT_INFERENCE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, cleaned_content, re.IGNORECASE):
                    return agent_short + "-reviewer"

    return None


def extract_subagent_metrics(filepath: str) -> dict:
    """
    Extract operational metrics from a single subagent JSONL file.

    Processes line-by-line with regex for efficiency — never loads full file
    into memory.  Works for any Claude Code subagent, not just reviewers.

    Returns a dict with:
      - input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
      - duration_seconds, start_time, end_time
      - verdict (APPROVE/COMMENT/REQUEST_CHANGES if present)
      - severity_counts, total_findings
      - model
      - line_count
    """
    metrics = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "duration_seconds": 0,
        "start_time": None,
        "end_time": None,
        "verdict": None,
        "severity_counts": {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "info": 0,
        },
        "total_findings": 0,
        "model": None,
        "line_count": 0,
        "file_size_bytes": 0,
    }

    try:
        metrics["file_size_bytes"] = os.path.getsize(filepath)
    except OSError:
        pass

    first_timestamp = None
    last_timestamp = None
    models_seen = set()

    try:
        with open(filepath, "r") as f:
            for line_num, line in enumerate(f):
                metrics["line_count"] = line_num + 1

                # Token usage — regex on raw text for speed
                for m in re.finditer(r'"input_tokens"\s*:\s*(\d+)', line):
                    metrics["input_tokens"] += int(m.group(1))
                for m in re.finditer(r'"output_tokens"\s*:\s*(\d+)', line):
                    metrics["output_tokens"] += int(m.group(1))
                for m in re.finditer(r'"cache_read_input_tokens"\s*:\s*(\d+)', line):
                    metrics["cache_read_tokens"] += int(m.group(1))
                for m in re.finditer(r'"cache_creation_input_tokens"\s*:\s*(\d+)', line):
                    metrics["cache_creation_tokens"] += int(m.group(1))

                # Model
                model_match = re.search(r'"model"\s*:\s*"([^"]+)"', line)
                if model_match:
                    models_seen.add(model_match.group(1))

                # Timestamps
                ts_match = re.search(
                    r'"timestamp"\s*:\s*"(\d{4}-\d{2}-\d{2}T[\d:.]+Z)"', line
                )
                if ts_match:
                    ts_str = ts_match.group(1)
                    if first_timestamp is None:
                        first_timestamp = ts_str
                    last_timestamp = ts_str

                # Verdict (reviewer agents)
                verdict_match = re.search(
                    r"VERDICT:\s*(APPROVE|COMMENT|REQUEST_CHANGES|ALIGN)", line
                )
                if verdict_match:
                    metrics["verdict"] = verdict_match.group(1)

                # Severity counts (from COUNTS line in reviewer output)
                counts_match = re.search(
                    r"COUNTS:\s*critical:\s*(\d+),\s*high:\s*(\d+),\s*medium:\s*(\d+)"
                    r"(?:,\s*low:\s*(\d+))?(?:,\s*info:\s*(\d+))?",
                    line,
                )
                if counts_match:
                    c = int(counts_match.group(1))
                    h = int(counts_match.group(2))
                    m = int(counts_match.group(3))
                    lo = int(counts_match.group(4)) if counts_match.group(4) else 0
                    info = int(counts_match.group(5)) if counts_match.group(5) else 0
                    metrics["severity_counts"] = {
                        "critical": c,
                        "high": h,
                        "medium": m,
                        "low": lo,
                        "info": info,
                    }
                    metrics["total_findings"] = c + h + m + lo + info

    except (IOError, OSError) as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)
        return metrics

    # Compute duration from timestamps
    if first_timestamp and last_timestamp:
        metrics["start_time"] = first_timestamp
        metrics["end_time"] = last_timestamp
        try:
            start = datetime.fromisoformat(first_timestamp.replace("Z", "+00:00"))
            end = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
            metrics["duration_seconds"] = max(0, (end - start).total_seconds())
        except ValueError:
            pass

    # Pick the most common model (or first alphabetically if tied)
    if models_seen:
        metrics["model"] = sorted(models_seen)[0]

    return metrics


def scan_sessions(
    sessions_dir: str,
    agent_filter: Optional[list] = None,
    include_all: bool = False,
    limit: Optional[int] = None,
) -> list:
    """
    Scan all sessions and extract metrics for subagent JSONL files.

    Args:
        sessions_dir: Path to Claude Code project sessions directory
        agent_filter: If set, only include these agent types
        include_all: If True, include unidentified subagents too
        limit: If set, only process the N most recent sessions
    """
    results = []
    sessions_with_agents = set()

    try:
        entries = os.listdir(sessions_dir)
    except OSError as e:
        print(f"Error listing sessions dir: {e}", file=sys.stderr)
        return results

    # Find all session dirs with subagents/
    session_dirs = []
    for entry in entries:
        subagents_dir = os.path.join(sessions_dir, entry, "subagents")
        if os.path.isdir(subagents_dir):
            # Use dir mtime for sorting
            try:
                mtime = os.path.getmtime(subagents_dir)
            except OSError:
                mtime = 0
            session_dirs.append((entry, subagents_dir, mtime))

    # Sort by most recent first
    session_dirs.sort(key=lambda x: x[2], reverse=True)

    if limit:
        session_dirs = session_dirs[:limit]

    print(
        f"Scanning {len(session_dirs)} sessions with subagents/ ...",
        file=sys.stderr,
    )

    for session_id, subagents_dir, _ in session_dirs:
        try:
            jsonl_files = [
                f
                for f in os.listdir(subagents_dir)
                if f.endswith(".jsonl") and "compact" not in f
            ]
        except OSError:
            continue

        for jsonl_file in jsonl_files:
            filepath = os.path.join(subagents_dir, jsonl_file)
            agent_type = identify_agent_type(filepath)

            # Skip unidentified agents unless --all
            if agent_type is None and not include_all:
                continue

            # Apply agent filter
            if agent_filter and agent_type not in agent_filter:
                continue

            metrics = extract_subagent_metrics(filepath)
            metrics["agent_type"] = agent_type or "unknown"
            metrics["session_id"] = session_id
            metrics["subagent_file"] = jsonl_file
            results.append(metrics)
            sessions_with_agents.add(session_id)

    print(
        f"Found {len(results)} subagent executions across "
        f"{len(sessions_with_agents)} sessions",
        file=sys.stderr,
    )
    return results


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "N/A"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_markdown_report(results: list) -> str:
    """Generate a comprehensive Markdown report from extracted metrics."""
    lines = []
    lines.append("# Claude Code Session Agent Metrics")
    lines.append("")
    lines.append(
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )

    sessions = sorted(set(r["session_id"] for r in results))
    lines.append(f"**Sessions analyzed:** {len(sessions)}")
    lines.append(f"**Total agent executions:** {len(results)}")
    lines.append("")

    # -- Aggregate per agent --
    agent_stats = defaultdict(
        lambda: {
            "count": 0,
            "total_input": 0,
            "total_output": 0,
            "total_cache_read": 0,
            "total_cache_creation": 0,
            "total_duration": 0,
            "duration_samples": 0,
            "verdicts": defaultdict(int),
            "total_findings": 0,
            "severity_totals": defaultdict(int),
            "sessions_with_findings": 0,
            "models": defaultdict(int),
        }
    )

    for r in results:
        agent = r["agent_type"]
        s = agent_stats[agent]
        s["count"] += 1
        s["total_input"] += r["input_tokens"]
        s["total_output"] += r["output_tokens"]
        s["total_cache_read"] += r["cache_read_tokens"]
        s["total_cache_creation"] += r["cache_creation_tokens"]
        if r["duration_seconds"] > 0:
            s["total_duration"] += r["duration_seconds"]
            s["duration_samples"] += 1
        if r["verdict"]:
            s["verdicts"][r["verdict"]] += 1
        if r["total_findings"] > 0:
            s["total_findings"] += r["total_findings"]
            s["sessions_with_findings"] += 1
            for sev, cnt in r["severity_counts"].items():
                s["severity_totals"][sev] += cnt
        if r["model"]:
            s["models"][r["model"]] += 1

    sorted_agents = sorted(agent_stats.items(), key=lambda x: -x[1]["count"])

    # -- Per-agent summary --
    lines.append("## Per-Agent Summary")
    lines.append("")
    lines.append(
        "| Agent | Dispatches | Avg Output | Avg Cache Read | "
        "Avg Duration | Hit Rate | Avg Findings |"
    )
    lines.append(
        "|-------|-----------|------------|----------------|"
        "-------------|----------|-------------|"
    )

    for agent, s in sorted_agents:
        n = s["count"]
        avg_out = s["total_output"] // n
        avg_cache = s["total_cache_read"] // n
        avg_dur = s["total_duration"] / s["duration_samples"] if s["duration_samples"] else 0
        hit_rate = f"{s['sessions_with_findings'] / n * 100:.0f}%"

        avg_findings = (
            f"{s['total_findings'] / s['sessions_with_findings']:.1f}"
            if s["sessions_with_findings"]
            else "—"
        )

        lines.append(
            f"| {agent} | {n} | {format_tokens(avg_out)} | "
            f"{format_tokens(avg_cache)} | {format_duration(avg_dur)} | "
            f"{hit_rate} | {avg_findings} |"
        )

    lines.append("")

    # -- Severity distribution --
    lines.append("## Severity Distribution")
    lines.append("")
    lines.append(
        "| Agent | Critical | High | Medium | Low | Info | Total | "
        "Hit Rate |"
    )
    lines.append(
        "|-------|----------|------|--------|-----|------|-------|"
        "----------|"
    )

    for agent, s in sorted_agents:
        st = s["severity_totals"]
        total = sum(st.values())
        hit = f"{s['sessions_with_findings']}/{s['count']}"
        lines.append(
            f"| {agent} | {st.get('critical', 0)} | {st.get('high', 0)} | "
            f"{st.get('medium', 0)} | {st.get('low', 0)} | "
            f"{st.get('info', 0)} | {total} | {hit} |"
        )

    lines.append("")

    # -- Token budget --
    lines.append("## Token Budget")
    lines.append("")
    total_input = sum(r["input_tokens"] for r in results)
    total_output = sum(r["output_tokens"] for r in results)
    total_cache_read = sum(r["cache_read_tokens"] for r in results)
    total_cache_create = sum(r["cache_creation_tokens"] for r in results)

    lines.append(f"- **Total input tokens:** {format_tokens(total_input)}")
    lines.append(f"- **Total output tokens:** {format_tokens(total_output)}")
    lines.append(f"- **Total cache read:** {format_tokens(total_cache_read)}")
    lines.append(f"- **Total cache creation:** {format_tokens(total_cache_create)}")
    total_cache = total_cache_read + total_cache_create
    if total_cache > 0:
        lines.append(
            f"- **Cache hit rate:** "
            f"{total_cache_read / total_cache * 100:.1f}%"
        )
    lines.append("")

    # -- Model distribution --
    lines.append("## Model Distribution")
    lines.append("")
    model_counts = defaultdict(int)
    for r in results:
        if r["model"]:
            model_counts[r["model"]] += 1
    for model, count in sorted(model_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- **{model}:** {count} executions")
    lines.append("")

    # -- Duration stats --
    lines.append("## Duration Analysis")
    lines.append("")
    durations = [r["duration_seconds"] for r in results if r["duration_seconds"] > 0]
    if durations:
        sorted_durs = sorted(durations)
        lines.append(f"- **Average:** {format_duration(sum(durations) / len(durations))}")
        lines.append(f"- **Median:** {format_duration(sorted_durs[len(sorted_durs) // 2])}")
        lines.append(f"- **Min:** {format_duration(min(durations))}")
        lines.append(f"- **Max:** {format_duration(max(durations))}")
        lines.append(f"- **Samples:** {len(durations)}/{len(results)}")
    lines.append("")

    # -- Per-session breakdown --
    lines.append("## Per-Session Breakdown")
    lines.append("")

    session_groups = defaultdict(list)
    for r in results:
        session_groups[r["session_id"]].append(r)

    # Sort sessions by most recent first
    def session_sort_key(item):
        times = [r["start_time"] for r in item[1] if r.get("start_time")]
        return max(times) if times else ""

    for session_id, session_results in sorted(
        session_groups.items(), key=session_sort_key, reverse=True
    ):
        start_times = [r["start_time"] for r in session_results if r.get("start_time")]
        session_date = min(start_times)[:10] if start_times else "unknown"

        agents = sorted(set(r["agent_type"] for r in session_results))
        total_out = sum(r["output_tokens"] for r in session_results)

        lines.append(
            f"### Session `{session_id[:8]}...` ({session_date})"
        )
        lines.append(
            f"- **Agents ({len(session_results)}):** {', '.join(agents)}"
        )
        lines.append(f"- **Total output:** {format_tokens(total_out)}")
        lines.append("")

        lines.append(
            "| Agent | Duration | Output | Cache Read | Verdict | "
            "Findings | Severities |"
        )
        lines.append(
            "|-------|----------|--------|------------|---------|"
            "----------|------------|"
        )

        for r in sorted(
            session_results, key=lambda x: x.get("start_time") or ""
        ):
            sev_str = ""
            if r["total_findings"] > 0:
                parts = []
                for sev_key in ["critical", "high", "medium", "low", "info"]:
                    v = r["severity_counts"].get(sev_key, 0)
                    if v > 0:
                        parts.append(f"{sev_key[0].upper()}:{v}")
                sev_str = ", ".join(parts)

            lines.append(
                f"| {r['agent_type']} | {format_duration(r['duration_seconds'])} | "
                f"{format_tokens(r['output_tokens'])} | "
                f"{format_tokens(r['cache_read_tokens'])} | "
                f"{r['verdict'] or '—'} | "
                f"{r['total_findings']} | {sev_str} |"
            )

        lines.append("")

    return "\n".join(lines)


def generate_triage_report(
    triage_decisions: list,
    agent_results: list,
) -> str:
    """Generate a triage effectiveness report.

    Compares triage decisions (DISPATCH/SKIP) against actual agent outcomes
    (findings/no findings) to measure triage accuracy.

    Args:
        triage_decisions: list of triage decision dicts from scan_triage_decisions
        agent_results: list of agent metric dicts from scan_sessions
    """
    lines = []
    lines.append("# Triage Effectiveness Report")
    lines.append("")
    lines.append(
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )

    sessions_with_triage = sorted(set(d["session_id"] for d in triage_decisions))
    lines.append(f"**Sessions with triage decisions:** {len(sessions_with_triage)}")
    lines.append(f"**Total triage decisions:** {len(triage_decisions)}")
    lines.append("")

    # Build lookup: session_id -> agent_type -> had_findings
    agent_outcomes = defaultdict(dict)
    for r in agent_results:
        agent_outcomes[r["session_id"]][r["agent_type"]] = r["total_findings"] > 0

    # Per-agent triage stats
    agent_triage = defaultdict(
        lambda: {
            "dispatch_count": 0,
            "skip_count": 0,
            "dispatch_hit": 0,      # dispatched and found something
            "dispatch_miss": 0,     # dispatched but found nothing
            "skip_no_outcome": 0,   # skipped, no way to verify
            "reasons": [],
        }
    )

    for d in triage_decisions:
        agent = d["agent"]
        decision = d["decision"]
        at = agent_triage[agent]

        if decision == "DISPATCH":
            at["dispatch_count"] += 1
            # Check if the agent actually produced findings
            had_findings = agent_outcomes.get(d["session_id"], {}).get(agent)
            if had_findings is True:
                at["dispatch_hit"] += 1
            elif had_findings is False:
                at["dispatch_miss"] += 1
            # else: agent wasn't found in results (may not have run yet)
        else:
            at["skip_count"] += 1
            at["skip_no_outcome"] += 1
            at["reasons"].append(d["reason"])

    # Summary table
    lines.append("## Per-Agent Triage Summary")
    lines.append("")
    lines.append(
        "| Agent | Dispatched | Skipped | Hit Rate (dispatched) | "
        "Skip Rate |"
    )
    lines.append(
        "|-------|-----------|---------|----------------------|"
        "----------|"
    )

    for agent in TRIAGED_AGENTS:
        at = agent_triage.get(agent)
        if not at:
            lines.append(f"| {agent} | 0 | 0 | — | — |")
            continue

        total = at["dispatch_count"] + at["skip_count"]
        skip_rate = (
            f"{at['skip_count'] / total * 100:.0f}%"
            if total > 0 else "—"
        )

        if at["dispatch_count"] > 0:
            verified = at["dispatch_hit"] + at["dispatch_miss"]
            if verified > 0:
                hit_rate = f"{at['dispatch_hit'] / verified * 100:.0f}% ({at['dispatch_hit']}/{verified})"
            else:
                hit_rate = "no data"
        else:
            hit_rate = "—"

        lines.append(
            f"| {agent} | {at['dispatch_count']} | {at['skip_count']} | "
            f"{hit_rate} | {skip_rate} |"
        )

    lines.append("")

    # Skip reasons (for auditing)
    lines.append("## Skip Reasons (Audit Log)")
    lines.append("")

    for agent in TRIAGED_AGENTS:
        at = agent_triage.get(agent)
        if not at or not at["reasons"]:
            continue

        lines.append(f"### {agent}")
        lines.append("")
        # Deduplicate similar reasons
        reason_counts = defaultdict(int)
        for reason in at["reasons"]:
            # Truncate long reasons for readability
            short = reason[:120] + "..." if len(reason) > 120 else reason
            reason_counts[short] += 1

        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            prefix = f"({count}x) " if count > 1 else ""
            lines.append(f"- {prefix}{reason}")
        lines.append("")

    # Comparison to pre-triage baselines
    lines.append("## Comparison to Pre-Triage Baselines")
    lines.append("")
    lines.append("Pre-triage hit rates (from 47-session analysis, Feb 2026):")
    lines.append("")

    baselines = {
        "security-reviewer": ("11%", "38/47 (81%)"),
        "dead-code-reviewer": ("32%", "19/47 (40%)"),
        "architecture-reviewer": ("67%", "24/47 (51%)"),
        "wp-architecture-reviewer": ("33%", "55/47 (117%)"),
        "performance-reviewer": ("56%", "25/47 (53%)"),
        "a11y-reviewer": ("40%", "5/47 (11%)"),
    }

    lines.append(
        "| Agent | Pre-Triage Hit Rate | Pre-Triage Dispatch Rate | "
        "Post-Triage Hit Rate | Post-Triage Skip Rate |"
    )
    lines.append(
        "|-------|--------------------|--------------------------"
        "|---------------------|-----------------------|"
    )

    for agent in TRIAGED_AGENTS:
        baseline_hit, baseline_dispatch = baselines.get(agent, ("—", "—"))
        at = agent_triage.get(agent)

        if at and at["dispatch_count"] > 0:
            verified = at["dispatch_hit"] + at["dispatch_miss"]
            post_hit = (
                f"{at['dispatch_hit'] / verified * 100:.0f}%"
                if verified > 0 else "—"
            )
        else:
            post_hit = "—"

        if at:
            total = at["dispatch_count"] + at["skip_count"]
            post_skip = (
                f"{at['skip_count'] / total * 100:.0f}%"
                if total > 0 else "—"
            )
        else:
            post_skip = "—"

        lines.append(
            f"| {agent} | {baseline_hit} | {baseline_dispatch} | "
            f"{post_hit} | {post_skip} |"
        )

    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_default_sessions_dir() -> Optional[str]:
    """Find the Claude Code sessions directory for the current git project."""
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            repo_root = result.stdout.strip()
            # Claude Code encodes the path with dashes
            encoded = repo_root.replace("/", "-")
            sessions_dir = os.path.expanduser(
                f"~/.claude/projects/{encoded}"
            )
            if os.path.isdir(sessions_dir):
                return sessions_dir
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Extract operational metrics from Claude Code session transcripts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--sessions-dir",
        help="Path to Claude Code project sessions directory "
        "(default: auto-detect from current git repo)",
    )
    parser.add_argument(
        "--agents",
        help="Comma-separated list of agent types to include "
        "(e.g. security-reviewer,pr-reviewer)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include all subagents, not just identified reviewer agents",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only process the N most recent sessions",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "both"],
        default="both",
        help="Output format (default: both)",
    )
    parser.add_argument(
        "--output",
        help="Output file path (default: $TMPDIR/session_metrics.*)",
    )
    parser.add_argument(
        "--triage",
        action="store_true",
        help="Extract and report on adaptive triage decisions. "
        "Scans orchestrator sessions for TRIAGE lines and compares "
        "dispatch/skip decisions against actual agent outcomes.",
    )

    args = parser.parse_args()

    # Resolve sessions directory
    sessions_dir = args.sessions_dir
    if not sessions_dir:
        sessions_dir = find_default_sessions_dir()
        if not sessions_dir:
            print(
                "ERROR: Could not auto-detect sessions directory. "
                "Use --sessions-dir to specify.",
                file=sys.stderr,
            )
            sys.exit(1)

    if not os.path.isdir(sessions_dir):
        print(f"ERROR: Not a directory: {sessions_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Sessions directory: {sessions_dir}", file=sys.stderr)

    # Determine output paths
    tmpdir = os.environ.get("TMPDIR", "/tmp")

    if args.triage:
        # Triage mode: extract triage decisions and cross-reference with outcomes
        triage_decisions = scan_triage_decisions(
            sessions_dir, limit=args.limit
        )

        # Also scan agent results to cross-reference dispatch outcomes
        agent_results = scan_sessions(
            sessions_dir, include_all=False, limit=args.limit
        )

        if not triage_decisions:
            print(
                "No triage decisions found. Triage is only present in sessions "
                "using adaptive agent dispatch.",
                file=sys.stderr,
            )
            sys.exit(1)

        base = args.output or os.path.join(tmpdir, "triage_report")

        if args.format in ("markdown", "both"):
            md_path = base + ".md" if not base.endswith(".md") else base
            report = generate_triage_report(triage_decisions, agent_results)
            with open(md_path, "w") as f:
                f.write(report)
            print(f"Triage report: {md_path}", file=sys.stderr)

        if args.format in ("json", "both"):
            json_path = base + ".json" if not base.endswith(".json") else base
            with open(json_path, "w") as f:
                json.dump(triage_decisions, f, indent=2, default=str)
            print(f"Triage JSON: {json_path}", file=sys.stderr)

    else:
        # Standard mode: extract agent operational metrics
        agent_filter = None
        if args.agents:
            agent_filter = [a.strip() for a in args.agents.split(",")]

        results = scan_sessions(
            sessions_dir,
            agent_filter=agent_filter,
            include_all=args.all,
            limit=args.limit,
        )

        if not results:
            print("No matching subagent executions found.", file=sys.stderr)
            sys.exit(1)

        base = args.output or os.path.join(tmpdir, "session_metrics")

        if args.format in ("markdown", "both"):
            md_path = base + ".md" if not base.endswith(".md") else base
            report = generate_markdown_report(results)
            with open(md_path, "w") as f:
                f.write(report)
            print(f"Markdown report: {md_path}", file=sys.stderr)

        if args.format in ("json", "both"):
            json_path = base + ".json" if not base.endswith(".json") else base
            with open(json_path, "w") as f:
                json.dump(results, f, indent=2, default=str)
            print(f"JSON data: {json_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

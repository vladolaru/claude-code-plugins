#!/usr/bin/env python3
"""Analyze sub-agent JSONL traces for behavioral patterns.

Reads Claude Code sub-agent trace files, detects inefficiency patterns,
and produces a structured text summary for /dex:sharpen integration.

Exit codes:
    0 - Success
    1 - Error (invalid args, missing directories)
    2 - No sub-agent data found
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Pattern detection thresholds
HIGH_TOOL_COUNT = 25
HIGH_TOKEN_USAGE = 100_000
REPEATED_READ_THRESHOLD = 3
BASH_HEAVY_RATIO = 0.50

# Bash commands that should use dedicated tools instead
BASH_FILE_COMMANDS = ("find", "cat", "head", "tail", "ls", "grep", "rg", "awk", "sed")


def resolve_project_dir(project_dir: str) -> Path:
    """Resolve a project directory to its ~/.claude/projects/<hash>/ path.

    Derives the hash by replacing / with - and stripping the leading -.
    Falls back to scanning ~/.claude/projects/ for a matching suffix.
    """
    project_path = Path(project_dir).resolve()

    # Derive hash: /Users/foo/bar -> -Users-foo-bar -> Users-foo-bar
    hash_name = str(project_path).replace("/", "-").lstrip("-")
    direct_path = CLAUDE_PROJECTS_DIR / hash_name

    if direct_path.is_dir():
        return direct_path

    # Fallback: scan for suffix match
    if CLAUDE_PROJECTS_DIR.is_dir():
        suffix = "-" + project_path.name
        for entry in CLAUDE_PROJECTS_DIR.iterdir():
            if entry.is_dir() and entry.name.endswith(suffix):
                return entry

    print(
        f"Error: Could not resolve project dir '{project_dir}' "
        f"to a Claude projects directory",
        file=sys.stderr,
    )
    sys.exit(1)


def find_latest_session(project_path: Path) -> str:
    """Find the most recent session ID that has sub-agent traces.

    Lists .jsonl files in the project directory, filters to those that
    have a corresponding <id>/subagents/ directory, and picks the most
    recent by modification time.
    """
    candidates = []

    for jsonl_file in project_path.glob("*.jsonl"):
        session_id = jsonl_file.stem
        subagents_dir = project_path / session_id / "subagents"
        if subagents_dir.is_dir() and any(subagents_dir.iterdir()):
            candidates.append((jsonl_file.stat().st_mtime, session_id))

    if not candidates:
        return ""

    candidates.sort(reverse=True)
    return candidates[0][1]


def find_subagent_traces(project_path: Path, session_id: str) -> tuple:
    """Find sub-agent trace files, excluding compact/system agents.

    Returns (list[Path], filtered_count) where filtered_count is how many
    agent-acompact-* files were excluded.
    """
    subagents_dir = project_path / session_id / "subagents"

    if not subagents_dir.is_dir():
        return [], 0

    all_traces = sorted(subagents_dir.glob("agent-*.jsonl"))
    traces = []
    filtered = 0

    for trace in all_traces:
        if trace.name.startswith("agent-acompact-"):
            filtered += 1
        else:
            traces.append(trace)

    return traces, filtered


def parse_trace(path: Path) -> dict:
    """Parse a sub-agent JSONL trace file into structured data.

    Resilient to malformed lines and missing fields. Skips unparseable
    lines with a warning to stderr.
    """
    agent_id = path.stem.replace("agent-", "", 1)
    data = {
        "agent_id": agent_id,
        "model": "",
        "initial_prompt": "",
        "first_timestamp": "",
        "last_timestamp": "",
        "tool_calls": Counter(),
        "tool_errors": 0,
        "read_paths": [],
        "bash_commands": [],
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }

    line_num = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line_num += 1
        line = line.strip()
        if not line:
            continue

        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            print(
                f"Warning: Skipping unparseable line {line_num} in {path.name}",
                file=sys.stderr,
            )
            continue

        if not isinstance(entry, dict):
            continue

        # Track timestamps
        timestamp = entry.get("timestamp", "")
        if timestamp:
            if not data["first_timestamp"]:
                data["first_timestamp"] = timestamp
            data["last_timestamp"] = timestamp

        # Track model
        if not data["model"] and entry.get("model"):
            data["model"] = entry.get("model", "")

        msg_type = entry.get("type", "")
        message = entry.get("message", {})
        if not isinstance(message, dict):
            message = {}

        role = message.get("role", "")

        # Extract initial prompt from first user message
        if role == "user" and not data["initial_prompt"]:
            content = message.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        data["initial_prompt"] = block.get("text", "")
                        break
            elif isinstance(content, str):
                data["initial_prompt"] = content

        # Extract tool calls from assistant messages
        if role == "assistant":
            content = message.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        tool_name = block.get("name", "unknown")
                        data["tool_calls"][tool_name] += 1

                        # Track Read file paths
                        tool_input = block.get("input", {})
                        if not isinstance(tool_input, dict):
                            tool_input = {}

                        if tool_name == "Read":
                            file_path = tool_input.get("file_path", "")
                            if file_path:
                                data["read_paths"].append(file_path)

                        # Track Bash commands
                        if tool_name == "Bash":
                            cmd = tool_input.get("command", "")
                            if cmd:
                                data["bash_commands"].append(cmd)

        # Extract tool results (check for errors)
        if role == "user":
            content = message.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result" and block.get("is_error"):
                        data["tool_errors"] += 1

        # Extract token usage
        usage = entry.get("usage", {})
        if isinstance(usage, dict):
            data["total_input_tokens"] += usage.get("input_tokens", 0)
            data["total_output_tokens"] += usage.get("output_tokens", 0)

    return data


def detect_agent_type(prompt: str) -> str:
    """Detect agent type from initial prompt using keyword heuristics."""
    if not prompt:
        return "general"

    prompt_lower = prompt.lower()

    keywords = [
        ("explore", "Explore"),
        ("review", "Review"),
        ("plan", "Plan"),
        ("search", "Search"),
        ("test", "Test"),
        ("debug", "Debug"),
        ("fix", "Fix"),
        ("build", "Build"),
        ("document", "Docs"),
    ]

    for keyword, label in keywords:
        if keyword in prompt_lower:
            return label

    return "general"


def detect_patterns(agent_data: dict) -> list:
    """Detect behavioral anti-patterns in agent trace data.

    Returns list of {code, message} dicts for each pattern found.
    """
    patterns = []

    tool_calls = agent_data.get("tool_calls", Counter())
    total_tools = sum(tool_calls.values())

    # BASH_FOR_FILES: Bash commands that start with file-operation words
    bash_file_ops = []
    for cmd in agent_data.get("bash_commands", []):
        first_word = cmd.strip().split()[0] if cmd.strip() else ""
        if first_word in BASH_FILE_COMMANDS:
            bash_file_ops.append(first_word)

    if bash_file_ops:
        ops_str = ", ".join(bash_file_ops)
        patterns.append({
            "code": "BASH_FOR_FILES",
            "message": (
                f"Used Bash {len(bash_file_ops)}x for file ops ({ops_str}) "
                f"— prefer Glob/Read/Grep"
            ),
        })

    # HIGH_TOOL_COUNT: More than threshold total tool calls
    if total_tools > HIGH_TOOL_COUNT:
        patterns.append({
            "code": "HIGH_TOOL_COUNT",
            "message": f"{total_tools} tool calls (threshold: {HIGH_TOOL_COUNT})",
        })

    # REPEATED_READS: Same file_path read multiple times
    read_counts = Counter(agent_data.get("read_paths", []))
    for file_path, count in read_counts.items():
        if count >= REPEATED_READ_THRESHOLD:
            patterns.append({
                "code": "REPEATED_READS",
                "message": f"Read same file {count}x: {file_path}",
            })

    # HIGH_TOKEN_USAGE: Total tokens exceed threshold
    total_tokens = (
        agent_data.get("total_input_tokens", 0)
        + agent_data.get("total_output_tokens", 0)
    )
    if total_tokens > HIGH_TOKEN_USAGE:
        patterns.append({
            "code": "HIGH_TOKEN_USAGE",
            "message": f"{total_tokens:,} total tokens (threshold: {HIGH_TOKEN_USAGE:,})",
        })

    # FAILED_TOOLS: Any tool errors
    tool_errors = agent_data.get("tool_errors", 0)
    if tool_errors > 0:
        patterns.append({
            "code": "FAILED_TOOLS",
            "message": f"{tool_errors} tool call(s) returned errors",
        })

    # BASH_HEAVY: Bash > 50% of all tool calls
    if total_tools > 0:
        bash_count = tool_calls.get("Bash", 0)
        ratio = bash_count / total_tools
        if ratio > BASH_HEAVY_RATIO:
            patterns.append({
                "code": "BASH_HEAVY",
                "message": (
                    f"Bash is {ratio:.0%} of tool calls ({bash_count}/{total_tools}) "
                    f"— may indicate over-reliance on shell"
                ),
            })

    return patterns


def _shorten_model(model: str) -> str:
    """Shorten model name for display."""
    if not model:
        return "unknown"
    # claude-sonnet-4-5-20250929 -> sonnet-4-5
    if "sonnet" in model:
        return "sonnet"
    if "opus" in model:
        return "opus"
    if "haiku" in model:
        return "haiku"
    return model


def _compute_duration_seconds(first_ts: str, last_ts: str) -> float:
    """Compute duration between two ISO timestamps. Returns 0 on failure."""
    if not first_ts or not last_ts:
        return 0.0

    try:
        from datetime import datetime

        # Handle both Z suffix and +00:00 formats
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                     "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                     "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                t1 = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                return max(0, (t2 - t1).total_seconds())
            except (ValueError, TypeError):
                continue
    except Exception:
        pass

    return 0.0


def format_output(session_id: str, agents: list, filtered_count: int) -> str:
    """Format analysis results as structured text.

    agents is a list of (agent_data, agent_type, patterns) tuples.
    """
    lines = []
    lines.append("=== Sub-Agent Behavior Summary ===")
    lines.append(f"Session: {session_id}")
    lines.append(
        f"Agents analyzed: {len(agents)}"
        + (f" (filtered {filtered_count} compact/system agents)" if filtered_count else "")
    )
    lines.append("")

    total_tokens_all = 0
    total_tools_all = 0
    total_duration_all = 0.0
    all_tool_counts = Counter()

    for agent_data, agent_type, agent_patterns in agents:
        agent_id = agent_data["agent_id"]
        model_short = _shorten_model(agent_data.get("model", ""))
        prompt = agent_data.get("initial_prompt", "")
        prompt_display = (prompt[:100] + "...") if len(prompt) > 100 else prompt
        prompt_display = prompt_display.replace("\n", " ")

        tool_calls = agent_data.get("tool_calls", Counter())
        total_tools = sum(tool_calls.values())
        total_tokens = (
            agent_data.get("total_input_tokens", 0)
            + agent_data.get("total_output_tokens", 0)
        )
        duration = _compute_duration_seconds(
            agent_data.get("first_timestamp", ""),
            agent_data.get("last_timestamp", ""),
        )

        total_tokens_all += total_tokens
        total_tools_all += total_tools
        total_duration_all += duration
        all_tool_counts.update(tool_calls)

        lines.append(f"--- Agent {agent_id} ({agent_type}, {model_short}) ---")
        lines.append(f"Prompt: {prompt_display}")

        duration_str = f"{duration:.0f}s" if duration > 0 else "N/A"
        lines.append(
            f"Duration: {duration_str} | Tokens: {total_tokens:,} | Tool calls: {total_tools}"
        )
        lines.append("")

        if tool_calls:
            # Sort by count descending
            sorted_tools = sorted(tool_calls.items(), key=lambda x: -x[1])
            tool_parts = [f"{name}: {count}" for name, count in sorted_tools]
            lines.append("Tool usage:")
            lines.append(f"  {', '.join(tool_parts)}")
            lines.append("")

        if agent_patterns:
            lines.append("Patterns flagged:")
            for p in agent_patterns:
                lines.append(f"  [{p['code']}] {p['message']}")
            lines.append("")

    # Aggregate stats
    lines.append("=== Aggregate Stats ===")
    lines.append(f"Total sub-agent tokens: {total_tokens_all:,}")
    lines.append(f"Total sub-agent tool calls: {total_tools_all}")
    lines.append(
        f"Total sub-agent duration: {total_duration_all:.0f}s"
    )

    if all_tool_counts:
        top_tools = sorted(all_tool_counts.items(), key=lambda x: -x[1])[:5]
        top_str = ", ".join(f"{name} ({count})" for name, count in top_tools)
        lines.append(f"Most-used tools: {top_str}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze sub-agent JSONL traces for behavioral patterns"
    )
    parser.add_argument(
        "--session",
        default="",
        help="Session ID to analyze (default: most recent with sub-agents)",
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Project directory (default: current directory)",
    )
    args = parser.parse_args()

    # Resolve project directory to Claude projects path
    project_path = resolve_project_dir(args.project_dir)

    # Find session
    session_id = args.session or find_latest_session(project_path)
    if not session_id:
        print("No sub-agent data found.", file=sys.stderr)
        sys.exit(2)

    # Find traces
    traces, filtered_count = find_subagent_traces(project_path, session_id)
    if not traces:
        print("No sub-agent data found.", file=sys.stderr)
        sys.exit(2)

    # Parse and analyze each trace
    agents = []
    for trace_path in traces:
        try:
            agent_data = parse_trace(trace_path)
        except Exception as e:
            print(
                f"Warning: Could not read {trace_path.name}: {e}",
                file=sys.stderr,
            )
            continue

        agent_type = detect_agent_type(agent_data.get("initial_prompt", ""))
        agent_patterns = detect_patterns(agent_data)
        agents.append((agent_data, agent_type, agent_patterns))

    if not agents:
        print("No sub-agent data found.", file=sys.stderr)
        sys.exit(2)

    # Format and print output
    output = format_output(session_id, agents, filtered_count)
    print(output)
    sys.exit(0)


if __name__ == "__main__":
    main()

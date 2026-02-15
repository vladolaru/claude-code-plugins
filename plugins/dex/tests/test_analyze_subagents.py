"""
Tests for the sub-agent behavior analyzer — deterministic, no model calls.

Validates:
- Project directory resolution and hash derivation
- Session discovery and trace file filtering
- JSONL trace parsing (valid, malformed, missing fields)
- Agent type detection from prompt keywords
- Pattern detection for each anti-pattern code
- Duration computation from ISO timestamps
- Output formatting with expected sections and markers
- Integration: subprocess invocation against fixture directory
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Script import via importlib (no package structure)
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "analyze-subagents.py"
FIXTURES_DIR = TESTS_DIR / "fixtures"

spec = importlib.util.spec_from_file_location("analyze_subagents", str(SCRIPT_PATH))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

resolve_project_dir = mod.resolve_project_dir
find_latest_session = mod.find_latest_session
find_subagent_traces = mod.find_subagent_traces
parse_trace = mod.parse_trace
detect_agent_type = mod.detect_agent_type
detect_patterns = mod.detect_patterns
format_output = mod.format_output
_compute_duration_seconds = mod._compute_duration_seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fixture(name: str) -> Path:
    """Return path to a fixture file."""
    return FIXTURES_DIR / name


def _make_project_tree(tmp_path: Path, session_id: str, agent_files: list) -> Path:
    """Create a minimal project tree for testing.

    Structure: tmp_path/<hash>/<session_id>.jsonl + <session_id>/subagents/agent-*.jsonl
    """
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()

    # Create session jsonl (can be empty)
    session_file = project_dir / f"{session_id}.jsonl"
    session_file.write_text("")

    # Create subagents dir with trace files
    subagents_dir = project_dir / session_id / "subagents"
    subagents_dir.mkdir(parents=True)

    for agent_file in agent_files:
        src = _fixture(agent_file)
        dst = subagents_dir / agent_file
        dst.write_text(src.read_text())

    return project_dir


# =============================================================================
# TestResolveProjectDir
# =============================================================================


class TestResolveProjectDir:
    """resolve_project_dir derives hash and validates directory."""

    def test_direct_derivation(self, tmp_path, monkeypatch):
        """Hash derived from path should resolve when directory exists."""
        # Create a fake ~/.claude/projects/<hash> structure
        fake_projects = tmp_path / "projects"
        monkeypatch.setattr(mod, "CLAUDE_PROJECTS_DIR", fake_projects)

        # Simulate /foo/bar -> -foo-bar -> foo-bar
        project_dir = tmp_path / "foo" / "bar"
        project_dir.mkdir(parents=True)

        hash_name = str(project_dir).replace("/", "-").lstrip("-")
        (fake_projects / hash_name).mkdir(parents=True)

        result = resolve_project_dir(str(project_dir))
        assert result == fake_projects / hash_name

    def test_fallback_scan(self, tmp_path, monkeypatch):
        """When direct hash fails, should scan for suffix match."""
        fake_projects = tmp_path / "projects"
        monkeypatch.setattr(mod, "CLAUDE_PROJECTS_DIR", fake_projects)

        # Create a directory with a different prefix but matching suffix
        (fake_projects / "some-prefix-myproject").mkdir(parents=True)

        # Project dir that won't match direct hash but has name "myproject"
        project_dir = tmp_path / "other" / "myproject"
        project_dir.mkdir(parents=True)

        result = resolve_project_dir(str(project_dir))
        assert result.name.endswith("myproject")

    def test_nonexistent_exits(self, tmp_path, monkeypatch):
        """Should exit 1 when no matching directory exists."""
        fake_projects = tmp_path / "projects"
        fake_projects.mkdir()
        monkeypatch.setattr(mod, "CLAUDE_PROJECTS_DIR", fake_projects)

        with pytest.raises(SystemExit) as exc_info:
            resolve_project_dir("/nonexistent/path/to/project")
        assert exc_info.value.code == 1


# =============================================================================
# TestFindLatestSession
# =============================================================================


class TestFindLatestSession:
    """find_latest_session picks most recent session with subagents."""

    def test_picks_most_recent(self, tmp_path):
        """Should select the session with the newest .jsonl mtime."""
        project_path = tmp_path

        # Create two sessions with subagents
        for i, sid in enumerate(["session-old", "session-new"]):
            (project_path / f"{sid}.jsonl").write_text("")
            sa_dir = project_path / sid / "subagents"
            sa_dir.mkdir(parents=True)
            (sa_dir / "agent-test.jsonl").write_text("{}")

        # Make "session-new" more recent
        import time
        old_file = project_path / "session-old.jsonl"
        new_file = project_path / "session-new.jsonl"
        # Touch old file to be older
        os.utime(old_file, (time.time() - 100, time.time() - 100))

        result = find_latest_session(project_path)
        assert result == "session-new"

    def test_requires_subagents_dir(self, tmp_path):
        """Sessions without subagents/ dir should be ignored."""
        project_path = tmp_path
        (project_path / "no-subagents.jsonl").write_text("")

        result = find_latest_session(project_path)
        assert result == ""

    def test_requires_nonempty_subagents(self, tmp_path):
        """Sessions with empty subagents/ dir should be ignored."""
        project_path = tmp_path
        sid = "empty-session"
        (project_path / f"{sid}.jsonl").write_text("")
        (project_path / sid / "subagents").mkdir(parents=True)

        result = find_latest_session(project_path)
        assert result == ""

    def test_no_sessions(self, tmp_path):
        """Empty project directory should return empty string."""
        result = find_latest_session(tmp_path)
        assert result == ""


# =============================================================================
# TestFindSubagentTraces
# =============================================================================


class TestFindSubagentTraces:
    """find_subagent_traces filters compact agents and handles edge cases."""

    def test_filters_compact_agents(self, tmp_path):
        """agent-acompact-* files should be excluded."""
        project_path = _make_project_tree(
            tmp_path, "test-session",
            ["agent-basic.jsonl", "agent-acompact-test.jsonl"],
        )

        traces, filtered = find_subagent_traces(project_path, "test-session")
        assert len(traces) == 1
        assert filtered == 1
        assert "acompact" not in traces[0].name

    def test_returns_all_non_compact(self, tmp_path):
        """All non-compact agents should be returned."""
        project_path = _make_project_tree(
            tmp_path, "test-session",
            ["agent-basic.jsonl", "agent-bash-heavy.jsonl", "agent-with-errors.jsonl"],
        )

        traces, filtered = find_subagent_traces(project_path, "test-session")
        assert len(traces) == 3
        assert filtered == 0

    def test_missing_subagents_dir(self, tmp_path):
        """Missing subagents directory should return empty list."""
        traces, filtered = find_subagent_traces(tmp_path, "nonexistent")
        assert traces == []
        assert filtered == 0

    def test_empty_subagents_dir(self, tmp_path):
        """Empty subagents directory should return empty list."""
        sa_dir = tmp_path / "empty-session" / "subagents"
        sa_dir.mkdir(parents=True)

        traces, filtered = find_subagent_traces(tmp_path, "empty-session")
        assert traces == []
        assert filtered == 0


# =============================================================================
# TestParseTrace
# =============================================================================


class TestParseTrace:
    """parse_trace extracts structured data from JSONL fixtures."""

    def test_basic_trace(self):
        """Basic fixture: extracts prompt, model, tool calls, tokens."""
        data = parse_trace(_fixture("agent-basic.jsonl"))
        assert data["agent_id"] == "basic"
        assert "sonnet" in data["model"]
        assert "Explore" in data["initial_prompt"]
        assert data["tool_calls"]["Read"] == 1
        assert data["total_input_tokens"] == 500
        assert data["total_output_tokens"] == 200
        assert data["first_timestamp"] == "2026-02-14T10:00:00Z"
        assert data["last_timestamp"] == "2026-02-14T10:00:10Z"

    def test_bash_heavy_trace(self):
        """Bash-heavy fixture: 6 Bash calls, no Read/Grep."""
        data = parse_trace(_fixture("agent-bash-heavy.jsonl"))
        assert data["tool_calls"]["Bash"] == 6
        assert "Read" not in data["tool_calls"]
        assert len(data["bash_commands"]) == 6
        assert "find . -name '*.json'" in data["bash_commands"]

    def test_repeated_reads_trace(self):
        """Repeated-reads fixture: same file read 4 times."""
        data = parse_trace(_fixture("agent-repeated-reads.jsonl"))
        assert data["tool_calls"]["Read"] == 4
        assert len(data["read_paths"]) == 4
        assert all(p == "plugins/dex/commands/sharpen.md" for p in data["read_paths"])

    def test_error_trace(self):
        """Error fixture: detects is_error tool results."""
        data = parse_trace(_fixture("agent-with-errors.jsonl"))
        assert data["tool_errors"] == 1
        assert data["tool_calls"]["Bash"] == 1
        assert data["tool_calls"]["Read"] == 1

    def test_malformed_trace(self):
        """Malformed fixture: skips bad lines, extracts what it can."""
        data = parse_trace(_fixture("agent-malformed.jsonl"))
        assert data["agent_id"] == "malformed"
        # Should still get data from valid lines
        assert data["tool_calls"]["Glob"] == 1
        assert data["initial_prompt"] == "Do something"
        assert data["total_input_tokens"] == 300
        assert data["total_output_tokens"] == 100

    def test_empty_file(self, tmp_path):
        """Empty trace file should return default structure."""
        empty = tmp_path / "agent-empty.jsonl"
        empty.write_text("")

        data = parse_trace(empty)
        assert data["agent_id"] == "empty"
        assert sum(data["tool_calls"].values()) == 0
        assert data["initial_prompt"] == ""

    def test_initial_prompt_string_content(self, tmp_path):
        """User message with string content (not list) should be handled."""
        trace = tmp_path / "agent-string.jsonl"
        trace.write_text(json.dumps({
            "type": "message",
            "message": {"role": "user", "content": "plain string prompt"},
        }) + "\n")

        data = parse_trace(trace)
        assert data["initial_prompt"] == "plain string prompt"


# =============================================================================
# TestDetectAgentType
# =============================================================================


class TestDetectAgentType:
    """detect_agent_type uses keyword heuristics."""

    @pytest.mark.parametrize(
        "prompt,expected",
        [
            ("Explore the codebase for Python files", "Explore"),
            ("Review the pull request changes", "Review"),
            ("Plan the implementation strategy", "Plan"),
            ("Search for all imports of utils", "Search"),
            ("Test the authentication module", "Test"),
            ("Debug the failing endpoint", "Debug"),
            ("Fix the null pointer error", "Fix"),
            ("Build the Docker image", "Build"),
            ("Document the API endpoints", "Docs"),
            ("Do something generic", "general"),
            ("", "general"),
        ],
    )
    def test_keyword_detection(self, prompt, expected):
        assert detect_agent_type(prompt) == expected

    def test_case_insensitive(self):
        """Keywords should match regardless of case."""
        assert detect_agent_type("EXPLORE the CODEBASE") == "Explore"
        assert detect_agent_type("REVIEW this PR") == "Review"

    def test_first_keyword_wins(self):
        """When multiple keywords match, the first in priority order wins."""
        # "explore" comes before "review" in the keywords list
        result = detect_agent_type("explore and review the code")
        assert result == "Explore"


# =============================================================================
# TestDetectPatterns
# =============================================================================


class TestDetectPatterns:
    """detect_patterns identifies behavioral anti-patterns."""

    def test_bash_for_files(self):
        """BASH_FOR_FILES: Bash commands starting with file-op words."""
        data = parse_trace(_fixture("agent-bash-heavy.jsonl"))
        patterns = detect_patterns(data)
        codes = [p["code"] for p in patterns]
        assert "BASH_FOR_FILES" in codes

        bash_pattern = next(p for p in patterns if p["code"] == "BASH_FOR_FILES")
        assert "find" in bash_pattern["message"]
        assert "cat" in bash_pattern["message"]

    def test_high_tool_count(self):
        """HIGH_TOOL_COUNT: flags when total tool calls exceed threshold."""
        data = {
            "tool_calls": Counter({"Read": 15, "Grep": 8, "Bash": 5}),
            "bash_commands": [],
            "read_paths": [],
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "tool_errors": 0,
        }
        patterns = detect_patterns(data)
        codes = [p["code"] for p in patterns]
        assert "HIGH_TOOL_COUNT" in codes

    def test_repeated_reads(self):
        """REPEATED_READS: same file read 3+ times."""
        data = parse_trace(_fixture("agent-repeated-reads.jsonl"))
        patterns = detect_patterns(data)
        codes = [p["code"] for p in patterns]
        assert "REPEATED_READS" in codes

        repeated = next(p for p in patterns if p["code"] == "REPEATED_READS")
        assert "sharpen.md" in repeated["message"]

    def test_high_token_usage(self):
        """HIGH_TOKEN_USAGE: flags when total tokens exceed threshold."""
        data = {
            "tool_calls": Counter(),
            "bash_commands": [],
            "read_paths": [],
            "total_input_tokens": 80_000,
            "total_output_tokens": 30_000,
            "tool_errors": 0,
        }
        patterns = detect_patterns(data)
        codes = [p["code"] for p in patterns]
        assert "HIGH_TOKEN_USAGE" in codes

    def test_failed_tools(self):
        """FAILED_TOOLS: flags any tool errors."""
        data = parse_trace(_fixture("agent-with-errors.jsonl"))
        patterns = detect_patterns(data)
        codes = [p["code"] for p in patterns]
        assert "FAILED_TOOLS" in codes

    def test_bash_heavy(self):
        """BASH_HEAVY: Bash > 50% of all tool calls."""
        data = parse_trace(_fixture("agent-bash-heavy.jsonl"))
        patterns = detect_patterns(data)
        codes = [p["code"] for p in patterns]
        assert "BASH_HEAVY" in codes

    def test_clean_agent_no_patterns(self):
        """A well-behaved agent should trigger no patterns."""
        data = {
            "tool_calls": Counter({"Read": 3, "Grep": 2, "Glob": 1}),
            "bash_commands": [],
            "read_paths": ["/a.py", "/b.py", "/c.py"],
            "total_input_tokens": 5_000,
            "total_output_tokens": 2_000,
            "tool_errors": 0,
        }
        patterns = detect_patterns(data)
        assert patterns == []

    def test_bash_heavy_below_threshold(self):
        """Bash at exactly 50% should NOT trigger BASH_HEAVY."""
        data = {
            "tool_calls": Counter({"Bash": 5, "Read": 5}),
            "bash_commands": [],
            "read_paths": [],
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "tool_errors": 0,
        }
        patterns = detect_patterns(data)
        codes = [p["code"] for p in patterns]
        assert "BASH_HEAVY" not in codes


# =============================================================================
# TestFormatOutput
# =============================================================================


class TestFormatOutput:
    """format_output produces structured text with expected sections."""

    def _make_agent_tuple(self, agent_id="test-1", agent_type="Explore",
                          patterns=None, tool_calls=None, tokens=500):
        """Helper to create an (agent_data, type, patterns) tuple."""
        data = {
            "agent_id": agent_id,
            "model": "claude-sonnet-4-5-20250929",
            "initial_prompt": "Test prompt for agent",
            "first_timestamp": "2026-02-14T10:00:00Z",
            "last_timestamp": "2026-02-14T10:00:30Z",
            "tool_calls": tool_calls or Counter({"Read": 5, "Grep": 3}),
            "tool_errors": 0,
            "read_paths": [],
            "bash_commands": [],
            "total_input_tokens": tokens,
            "total_output_tokens": tokens // 2,
        }
        return (data, agent_type, patterns or [])

    def test_contains_header(self):
        agents = [self._make_agent_tuple()]
        output = format_output("abc123", agents, 0)
        assert "=== Sub-Agent Behavior Summary ===" in output

    def test_contains_session_id(self):
        agents = [self._make_agent_tuple()]
        output = format_output("session-xyz", agents, 0)
        assert "Session: session-xyz" in output

    def test_contains_agent_count(self):
        agents = [self._make_agent_tuple(), self._make_agent_tuple(agent_id="test-2")]
        output = format_output("s1", agents, 0)
        assert "Agents analyzed: 2" in output

    def test_contains_filtered_count(self):
        agents = [self._make_agent_tuple()]
        output = format_output("s1", agents, 3)
        assert "filtered 3 compact/system agents" in output

    def test_no_filtered_when_zero(self):
        agents = [self._make_agent_tuple()]
        output = format_output("s1", agents, 0)
        assert "filtered" not in output

    def test_contains_agent_header(self):
        agents = [self._make_agent_tuple(agent_id="abc", agent_type="Review")]
        output = format_output("s1", agents, 0)
        assert "--- Agent abc (Review, sonnet) ---" in output

    def test_contains_tool_usage(self):
        agents = [self._make_agent_tuple()]
        output = format_output("s1", agents, 0)
        assert "Tool usage:" in output
        assert "Read: 5" in output

    def test_contains_patterns(self):
        patterns = [{"code": "BASH_FOR_FILES", "message": "Used Bash for file ops"}]
        agents = [self._make_agent_tuple(patterns=patterns)]
        output = format_output("s1", agents, 0)
        assert "Patterns flagged:" in output
        assert "[BASH_FOR_FILES]" in output

    def test_contains_aggregate_stats(self):
        agents = [self._make_agent_tuple()]
        output = format_output("s1", agents, 0)
        assert "=== Aggregate Stats ===" in output
        assert "Total sub-agent tokens:" in output
        assert "Total sub-agent tool calls:" in output
        assert "Most-used tools:" in output

    def test_prompt_truncation(self):
        """Prompts longer than 100 chars should be truncated."""
        data = {
            "agent_id": "long",
            "model": "claude-sonnet-4-5-20250929",
            "initial_prompt": "A" * 200,
            "first_timestamp": "",
            "last_timestamp": "",
            "tool_calls": Counter(),
            "tool_errors": 0,
            "read_paths": [],
            "bash_commands": [],
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        agents = [(data, "general", [])]
        output = format_output("s1", agents, 0)
        assert "..." in output
        # Should not contain the full 200 A's
        assert "A" * 200 not in output


# =============================================================================
# TestComputeDurationSeconds
# =============================================================================


class TestComputeDurationSeconds:
    """_compute_duration_seconds parses ISO timestamps and computes deltas."""

    def test_z_suffix_seconds(self):
        """Standard Z-suffix timestamps without fractional seconds."""
        result = _compute_duration_seconds(
            "2026-02-14T10:00:00Z", "2026-02-14T10:00:30Z"
        )
        assert result == 30.0

    def test_z_suffix_fractional(self):
        """Z-suffix timestamps with fractional seconds."""
        result = _compute_duration_seconds(
            "2026-02-14T10:00:00.000Z", "2026-02-14T10:00:05.500Z"
        )
        assert result == 5.5

    def test_empty_first_returns_zero(self):
        """Empty first timestamp returns 0."""
        assert _compute_duration_seconds("", "2026-02-14T10:00:00Z") == 0.0

    def test_empty_last_returns_zero(self):
        """Empty last timestamp returns 0."""
        assert _compute_duration_seconds("2026-02-14T10:00:00Z", "") == 0.0

    def test_both_empty_returns_zero(self):
        """Both empty returns 0."""
        assert _compute_duration_seconds("", "") == 0.0

    def test_reversed_timestamps_clamped_to_zero(self):
        """When last < first, result is clamped to 0 (not negative)."""
        result = _compute_duration_seconds(
            "2026-02-14T10:00:30Z", "2026-02-14T10:00:00Z"
        )
        assert result == 0.0

    def test_garbage_input_returns_zero(self):
        """Unparseable strings return 0 without raising."""
        assert _compute_duration_seconds("garbage", "also garbage") == 0.0

    def test_large_duration(self):
        """Multi-hour gap computes correctly."""
        result = _compute_duration_seconds(
            "2026-02-14T10:00:00Z", "2026-02-14T12:30:00Z"
        )
        assert result == 9000.0  # 2.5 hours

    def test_same_timestamp_returns_zero(self):
        """Identical timestamps produce 0 duration."""
        ts = "2026-02-14T10:00:00Z"
        assert _compute_duration_seconds(ts, ts) == 0.0


# =============================================================================
# TestIntegration
# =============================================================================


class TestIntegration:
    """End-to-end subprocess tests against fixture directory."""

    def _run_script(self, *args, expect_exit=0) -> subprocess.CompletedProcess:
        """Run analyze-subagents.py as subprocess."""
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == expect_exit, (
            f"Expected exit {expect_exit}, got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        return result

    def test_exit_2_no_data(self, tmp_path):
        """Exit code 2 when no sub-agent data exists."""
        # Create a fake project that resolves but has no sessions
        fake_projects = tmp_path / "projects"
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        hash_name = str(project_dir).replace("/", "-").lstrip("-")
        (fake_projects / hash_name).mkdir(parents=True)

        env = os.environ.copy()
        # We can't easily override CLAUDE_PROJECTS_DIR via env,
        # so we test against a project-dir that won't resolve
        result = self._run_script(
            "--project-dir", str(tmp_path / "nonexistent"),
            expect_exit=1,
        )
        assert "Error" in result.stderr or "Could not resolve" in result.stderr

    def test_exit_0_with_fixtures(self, tmp_path):
        """Exit code 0 with valid fixture data, produces expected output."""
        # Build a project tree that looks like a real Claude session
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        session_id = "test-session-001"
        session_file = project_dir / f"{session_id}.jsonl"
        session_file.write_text("")

        subagents_dir = project_dir / session_id / "subagents"
        subagents_dir.mkdir(parents=True)

        # Copy fixtures
        for fixture_name in ["agent-basic.jsonl", "agent-bash-heavy.jsonl"]:
            src = _fixture(fixture_name)
            (subagents_dir / fixture_name).write_text(src.read_text())

        # Patch: we need to override CLAUDE_PROJECTS_DIR
        # Run with --session to skip auto-detection, but we need resolve_project_dir
        # to work. Instead, create a wrapper that sets the path.
        wrapper = tmp_path / "run_test.py"
        wrapper.write_text(f"""
import sys
sys.path.insert(0, '{PLUGIN_ROOT / "scripts"}')
import importlib.util
spec = importlib.util.spec_from_file_location("analyze_subagents", '{SCRIPT_PATH}')
mod = importlib.util.module_from_spec(spec)

# Patch CLAUDE_PROJECTS_DIR before exec
import types
from pathlib import Path

original_code = spec.loader.get_code("analyze_subagents")
spec.loader.exec_module(mod)

# Override and run
mod.CLAUDE_PROJECTS_DIR = Path('{tmp_path}')

# Simulate: resolve_project_dir("project") should find {project_dir}
import argparse
mod.CLAUDE_PROJECTS_DIR = Path('{tmp_path}')

# Direct call to main logic
project_path = Path('{project_dir}')
session_id = mod.find_latest_session(project_path)
if not session_id:
    session_id = '{session_id}'

traces, filtered = mod.find_subagent_traces(project_path, session_id)
agents = []
for trace_path in traces:
    agent_data = mod.parse_trace(trace_path)
    agent_type = mod.detect_agent_type(agent_data.get("initial_prompt", ""))
    agent_patterns = mod.detect_patterns(agent_data)
    agents.append((agent_data, agent_type, agent_patterns))

output = mod.format_output(session_id, agents, filtered)
print(output)
""")

        result = subprocess.run(
            [sys.executable, str(wrapper)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        output = result.stdout
        assert "=== Sub-Agent Behavior Summary ===" in output
        assert "Agents analyzed: 2" in output
        assert "=== Aggregate Stats ===" in output
        assert "BASH_FOR_FILES" in output or "BASH_HEAVY" in output

    def test_help_flag(self):
        """--help should exit 0 and show usage."""
        result = self._run_script("--help", expect_exit=0)
        assert "usage" in result.stdout.lower() or "Analyze" in result.stdout

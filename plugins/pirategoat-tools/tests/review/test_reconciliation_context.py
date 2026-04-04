"""Tests for review/reconciliation_context.py — deterministic, no model calls.

Tests the reconciliation context builder by importing functions directly
and by running the full script via subprocess for integration tests.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

TESTS_DIR = Path(__file__).resolve().parent.parent  # review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "review" / "reconciliation_context.py"


def _load_module():
    """Load the reconciliation_context module via importlib."""
    spec = importlib.util.spec_from_file_location(
        "reconciliation_context", str(SCRIPT_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    """Module-scoped import of reconciliation_context."""
    return _load_module()


# ---------------------------------------------------------------------------
# Test fixture helpers
# ---------------------------------------------------------------------------

def _make_issue(
    severity="medium",
    title="Test issue",
    file="src/app.py",
    line=42,
    description="Some issue found",
    recommendation="Fix it",
    category="general",
    confidence=0.9,
):
    """Create a single issue dict matching ReviewOutputBuilder format."""
    issue = {
        "id": "abc12345",
        "category": category,
        "severity": severity,
        "title": title,
        "description": description,
        "file": file,
        "line": line,
        "recommendation": recommendation,
        "confidence": confidence,
    }
    return issue


def _make_review_json(
    reviewer="security",
    pr_id="42",
    verdict="comment",
    issues=None,
):
    """Create a complete review JSON dict matching ReviewOutputBuilder output."""
    if issues is None:
        issues = [_make_issue()]

    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for issue in issues:
        sev = issue.get("severity", "medium")
        if sev in severity_counts:
            severity_counts[sev] += 1

    return {
        "pr_id": pr_id,
        "reviewer": reviewer,
        "timestamp": "2026-04-04T10:00:00",
        "version": "1.0.0",
        "verdict": verdict,
        "summary": {
            "total_issues": len(issues),
            "by_severity": severity_counts,
        },
        "issues": issues,
        "observations": None,
        "recommendations": None,
        "positive_observations": None,
        "meta": {
            "files_reviewed": 3,
            "review_duration_ms": 1500,
            "confidence_score": 0.95,
            "tool_results_used": None,
        },
    }


# ===========================================================================
# TestLoadAgentFindings
# ===========================================================================

class TestLoadAgentFindings:
    """Tests for load_agent_findings()."""

    def test_loads_review_jsons(self, mod, tmp_path):
        """Loads *-review.json files and keys by stem."""
        review = _make_review_json(reviewer="security")
        (tmp_path / "security-review.json").write_text(json.dumps(review))
        (tmp_path / "pr-review.json").write_text(
            json.dumps(_make_review_json(reviewer="pr"))
        )

        result = mod.load_agent_findings(str(tmp_path))
        assert "security-review" in result
        assert "pr-review" in result
        assert result["security-review"]["reviewer"] == "security"
        assert result["pr-review"]["reviewer"] == "pr"

    def test_skips_non_review_files(self, mod, tmp_path):
        """Pipeline infrastructure files are not loaded."""
        (tmp_path / "security-review.json").write_text(
            json.dumps(_make_review_json())
        )
        # These should all be skipped
        (tmp_path / "dispatch-plan.json").write_text('{"agents": []}')
        (tmp_path / "pipeline-state.json").write_text('{"step": 1}')
        (tmp_path / "review-context.json").write_text('{"git": {}}')
        (tmp_path / "run-config.json").write_text('{"mode": "pr"}')
        (tmp_path / "reconciliation-context.json").write_text('{}')
        (tmp_path / "review-findings.json").write_text('{"findings": []}')
        (tmp_path / "pipeline-result.json").write_text('{"status": "ok"}')
        (tmp_path / "decision-critic-verdict.json").write_text('{"verdict": "STAND"}')
        (tmp_path / "clarity-assessment.json").write_text('{"clear": true}')

        result = mod.load_agent_findings(str(tmp_path))
        assert len(result) == 1
        assert "security-review" in result

    def test_handles_empty_directory(self, mod, tmp_path):
        """Empty directory returns empty dict."""
        result = mod.load_agent_findings(str(tmp_path))
        assert result == {}

    def test_handles_nonexistent_directory(self, mod, tmp_path):
        """Non-existent directory returns empty dict with warning."""
        result = mod.load_agent_findings(str(tmp_path / "nonexistent"))
        assert result == {}

    def test_skips_malformed_json(self, mod, tmp_path):
        """Malformed JSON files are skipped gracefully."""
        (tmp_path / "security-review.json").write_text(
            json.dumps(_make_review_json())
        )
        (tmp_path / "broken-review.json").write_text("{ not valid json !!!")

        result = mod.load_agent_findings(str(tmp_path))
        assert "security-review" in result
        assert "broken-review" not in result

    def test_skips_non_json_files(self, mod, tmp_path):
        """Files not ending in -review.json are ignored."""
        (tmp_path / "security-review.json").write_text(
            json.dumps(_make_review_json())
        )
        (tmp_path / "security-review.md").write_text("# Review")
        (tmp_path / "notes.txt").write_text("some notes")

        result = mod.load_agent_findings(str(tmp_path))
        assert len(result) == 1
        assert "security-review" in result

    def test_filters_by_dispatched_agents(self, mod, tmp_path):
        """Only loads review files for agents in the dispatch plan."""
        (tmp_path / "security-review.json").write_text(
            json.dumps(_make_review_json(reviewer="security"))
        )
        (tmp_path / "performance-review.json").write_text(
            json.dumps(_make_review_json(reviewer="performance"))
        )
        (tmp_path / "architecture-review.json").write_text(
            json.dumps(_make_review_json(reviewer="architecture"))
        )

        # Only security-reviewer and performance-reviewer are dispatched
        result = mod.load_agent_findings(
            str(tmp_path),
            dispatched_agents=["security-reviewer", "performance-reviewer"],
        )
        assert len(result) == 2
        assert "security-review" in result
        assert "performance-review" in result
        assert "architecture-review" not in result

    def test_dispatched_agents_none_loads_all(self, mod, tmp_path):
        """When dispatched_agents is None, all review files are loaded."""
        (tmp_path / "security-review.json").write_text(
            json.dumps(_make_review_json(reviewer="security"))
        )
        (tmp_path / "performance-review.json").write_text(
            json.dumps(_make_review_json(reviewer="performance"))
        )

        result = mod.load_agent_findings(str(tmp_path), dispatched_agents=None)
        assert len(result) == 2

    def test_dispatched_agents_empty_list_loads_nothing(self, mod, tmp_path):
        """An empty dispatched_agents list loads no review files."""
        (tmp_path / "security-review.json").write_text(
            json.dumps(_make_review_json(reviewer="security"))
        )

        result = mod.load_agent_findings(str(tmp_path), dispatched_agents=[])
        assert len(result) == 0


# ===========================================================================
# TestExtractReferences
# ===========================================================================

class TestExtractReferences:
    """Tests for extract_references()."""

    def test_extracts_unique_refs(self, mod):
        """Extracts file:line pairs from agent issues."""
        findings = {
            "security-review": _make_review_json(issues=[
                _make_issue(file="src/auth.py", line=10),
                _make_issue(file="src/db.py", line=20),
            ]),
        }
        refs = mod.extract_references(findings)
        assert len(refs) == 2
        files = {r["file"] for r in refs}
        assert files == {"src/auth.py", "src/db.py"}

    def test_deduplicates_same_file(self, mod):
        """Same file from multiple agents is deduplicated, lines merged."""
        findings = {
            "security-review": _make_review_json(issues=[
                _make_issue(file="src/auth.py", line=10),
                _make_issue(file="src/auth.py", line=30),
            ]),
            "performance-review": _make_review_json(issues=[
                _make_issue(file="src/auth.py", line=20),
                _make_issue(file="src/auth.py", line=10),  # duplicate line
            ]),
        }
        refs = mod.extract_references(findings)
        assert len(refs) == 1
        assert refs[0]["file"] == "src/auth.py"
        assert refs[0]["lines"] == [10, 20, 30]

    def test_skips_missing_lines(self, mod):
        """Issues without a valid line field are skipped."""
        findings = {
            "security-review": _make_review_json(issues=[
                _make_issue(file="src/auth.py", line=10),
                {
                    "id": "x",
                    "severity": "medium",
                    "title": "No line",
                    "file": "src/other.py",
                    "description": "...",
                    "recommendation": "...",
                    # line field missing
                },
                {
                    "id": "y",
                    "severity": "medium",
                    "title": "Null line",
                    "file": "src/other.py",
                    "line": None,
                    "description": "...",
                    "recommendation": "...",
                },
                {
                    "id": "z",
                    "severity": "medium",
                    "title": "Zero line",
                    "file": "src/other.py",
                    "line": 0,
                    "description": "...",
                    "recommendation": "...",
                },
            ]),
        }
        refs = mod.extract_references(findings)
        assert len(refs) == 1
        assert refs[0]["file"] == "src/auth.py"

    def test_handles_empty_findings(self, mod):
        """Empty findings returns empty list."""
        refs = mod.extract_references({})
        assert refs == []

    def test_handles_findings_with_no_issues(self, mod):
        """Findings with no issues list returns empty refs."""
        findings = {
            "security-review": {"verdict": "approve"},  # no issues key
        }
        refs = mod.extract_references(findings)
        assert refs == []

    def test_lines_are_sorted(self, mod):
        """Lines within a file reference are sorted ascending."""
        findings = {
            "a-review": _make_review_json(issues=[
                _make_issue(file="src/app.py", line=50),
                _make_issue(file="src/app.py", line=10),
                _make_issue(file="src/app.py", line=30),
            ]),
        }
        refs = mod.extract_references(findings)
        assert refs[0]["lines"] == [10, 30, 50]


# ===========================================================================
# TestReadSourceSnippets
# ===========================================================================

class TestReadSourceSnippets:
    """Tests for read_source_snippets()."""

    def test_reads_with_context(self, mod, tmp_path):
        """Reads source lines with +/-context around referenced lines."""
        # Create a source file with 20 lines
        source_file = tmp_path / "app.py"
        source_lines = [f"line {i}" for i in range(1, 21)]
        source_file.write_text("\n".join(source_lines) + "\n")

        refs = [{"file": str(source_file), "lines": [10]}]
        snippets = mod.read_source_snippets(refs, context_lines=3)

        assert str(source_file) in snippets
        snippet = snippets[str(source_file)]
        # Should include lines 7-13 (10 +/- 3)
        assert "7 | line 7" in snippet
        assert "10 | line 10" in snippet
        assert "13 | line 13" in snippet
        # Should NOT include line 6 or 14
        assert "6 | line 6" not in snippet
        assert "14 | line 14" not in snippet

    def test_merges_overlapping_windows(self, mod, tmp_path):
        """Overlapping context windows are merged."""
        source_file = tmp_path / "app.py"
        source_lines = [f"line {i}" for i in range(1, 31)]
        source_file.write_text("\n".join(source_lines) + "\n")

        # Lines 10 and 12 with context_lines=3: windows [7,13] and [9,15]
        # Should merge into [7,15]
        refs = [{"file": str(source_file), "lines": [10, 12]}]
        snippets = mod.read_source_snippets(refs, context_lines=3)

        snippet = snippets[str(source_file)]
        lines_in_snippet = snippet.strip().split("\n")
        # Should be a single contiguous block from 7 to 15 = 9 lines
        assert len(lines_in_snippet) == 9

    def test_handles_missing_files(self, mod, tmp_path):
        """Missing files are skipped gracefully."""
        refs = [{"file": str(tmp_path / "nonexistent.py"), "lines": [10]}]
        snippets = mod.read_source_snippets(refs, context_lines=3)
        assert snippets == {}

    def test_handles_empty_references(self, mod):
        """Empty references returns empty dict."""
        snippets = mod.read_source_snippets([], context_lines=3)
        assert snippets == {}

    def test_clamps_to_file_boundaries(self, mod, tmp_path):
        """Context window is clamped to file start/end."""
        source_file = tmp_path / "short.py"
        source_file.write_text("line 1\nline 2\nline 3\n")

        refs = [{"file": str(source_file), "lines": [1]}]
        snippets = mod.read_source_snippets(refs, context_lines=10)

        snippet = snippets[str(source_file)]
        lines_in_snippet = snippet.strip().split("\n")
        assert len(lines_in_snippet) == 3  # All 3 lines of file

    def test_resolves_relative_paths_from_git_root(self, mod, tmp_path):
        """Relative file paths are resolved against git_root, not CWD."""
        # Simulate a repo where the file lives at <git_root>/src/app.py
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        source_file = src_dir / "app.py"
        source_file.write_text("line 1\nline 2\nline 3\n")

        # Agent findings use git-root-relative paths like "src/app.py"
        refs = [{"file": "src/app.py", "lines": [2]}]

        # Without git_root, this would resolve against CWD (wrong).
        # With git_root=tmp_path, it resolves to tmp_path/src/app.py.
        snippets = mod.read_source_snippets(
            refs, context_lines=1, git_root=str(tmp_path)
        )

        assert "src/app.py" in snippets
        assert "2 | line 2" in snippets["src/app.py"]

    def test_absolute_paths_ignore_git_root(self, mod, tmp_path):
        """Absolute file paths are used directly, git_root is irrelevant."""
        source_file = tmp_path / "abs.py"
        source_file.write_text("line 1\nline 2\n")

        refs = [{"file": str(source_file), "lines": [1]}]
        snippets = mod.read_source_snippets(
            refs, context_lines=1, git_root="/some/other/root"
        )
        assert str(source_file) in snippets


# ===========================================================================
# TestMergeWindows
# ===========================================================================

class TestMergeWindows:
    """Tests for _merge_windows() helper."""

    def test_non_overlapping(self, mod):
        """Non-overlapping windows stay separate."""
        result = mod._merge_windows([(1, 5), (10, 15)])
        assert result == [(1, 5), (10, 15)]

    def test_overlapping(self, mod):
        """Overlapping windows are merged."""
        result = mod._merge_windows([(1, 10), (5, 15)])
        assert result == [(1, 15)]

    def test_adjacent(self, mod):
        """Adjacent windows (end+1 = start) are merged."""
        result = mod._merge_windows([(1, 5), (6, 10)])
        assert result == [(1, 10)]

    def test_empty(self, mod):
        """Empty input returns empty list."""
        result = mod._merge_windows([])
        assert result == []

    def test_unsorted_input(self, mod):
        """Unsorted input is sorted before merging."""
        result = mod._merge_windows([(10, 15), (1, 5)])
        assert result == [(1, 5), (10, 15)]

    def test_fully_contained(self, mod):
        """Window fully contained in another is absorbed."""
        result = mod._merge_windows([(1, 20), (5, 10)])
        assert result == [(1, 20)]


# ===========================================================================
# TestCheckScope
# ===========================================================================

class TestCheckScope:
    """Tests for check_scope().

    Without a real git repo, _parse_diff_hunks returns {} and check_scope
    falls back to file-level IN_SCOPE:in_hunk for files in changed_files.
    Hunk-level classification is tested in TestCheckScopeHunkLevel below.
    """

    def test_file_in_changed_is_in_scope(self, mod):
        """A referenced file that appears in changed_files — fallback in_hunk."""
        refs = [{"file": "src/auth.py", "lines": [10]}]
        changed = ["src/auth.py", "src/db.py"]
        result = mod.check_scope(refs, changed, "abc..HEAD")
        assert result["src/auth.py:10"] == "IN_SCOPE:in_hunk"

    def test_file_not_in_changed_is_out_of_scope(self, mod):
        """A referenced file NOT in changed_files is OUT_OF_SCOPE."""
        refs = [{"file": "src/utils.py", "lines": [10]}]
        changed = ["src/auth.py"]
        result = mod.check_scope(refs, changed, "abc..HEAD")
        assert result["src/utils.py:10"] == "OUT_OF_SCOPE:file_not_in_diff"

    def test_suffix_matching_abs_vs_relative(self, mod):
        """Absolute path in refs matches relative path in changed_files."""
        refs = [{"file": "/home/user/project/src/auth.py", "lines": [10]}]
        changed = ["src/auth.py"]
        result = mod.check_scope(refs, changed, "abc..HEAD")
        assert result["/home/user/project/src/auth.py:10"] == "IN_SCOPE:in_hunk"

    def test_suffix_matching_relative_vs_abs(self, mod):
        """Relative path in refs matches absolute path in changed_files."""
        refs = [{"file": "src/auth.py", "lines": [10]}]
        changed = ["/home/user/project/src/auth.py"]
        result = mod.check_scope(refs, changed, "abc..HEAD")
        assert result["src/auth.py:10"] == "IN_SCOPE:in_hunk"

    def test_empty_changed_files(self, mod):
        """No changed files means everything is OUT_OF_SCOPE."""
        refs = [{"file": "src/auth.py", "lines": [10]}]
        result = mod.check_scope(refs, [], "abc..HEAD")
        assert result["src/auth.py:10"] == "OUT_OF_SCOPE:file_not_in_diff"

    def test_empty_references(self, mod):
        """No references means empty annotations."""
        result = mod.check_scope([], ["src/auth.py"], "abc..HEAD")
        assert result == {}

    def test_mixed_scope(self, mod):
        """Mix of in-scope and out-of-scope files."""
        refs = [
            {"file": "src/auth.py", "lines": [10]},
            {"file": "src/utils.py", "lines": [20]},
            {"file": "src/db.py", "lines": [30]},
        ]
        changed = ["src/auth.py", "src/db.py"]
        result = mod.check_scope(refs, changed, "abc..HEAD")
        assert result["src/auth.py:10"] == "IN_SCOPE:in_hunk"
        assert result["src/utils.py:20"] == "OUT_OF_SCOPE:file_not_in_diff"
        assert result["src/db.py:30"] == "IN_SCOPE:in_hunk"

    def test_multiple_lines_per_file(self, mod):
        """Each line gets its own annotation."""
        refs = [{"file": "src/auth.py", "lines": [10, 50, 100]}]
        changed = ["src/auth.py"]
        result = mod.check_scope(refs, changed, "abc..HEAD")
        assert "src/auth.py:10" in result
        assert "src/auth.py:50" in result
        assert "src/auth.py:100" in result


# ===========================================================================
# TestCheckScopeHunkLevel — with mocked diff hunks
# ===========================================================================

class TestCheckScopeHunkLevel:
    """Tests for hunk-level scope classification in check_scope().

    Monkeypatches _parse_diff_hunks to provide controlled hunk data,
    isolating the hunk-level classification logic from git.
    """

    def test_line_in_hunk(self, mod, monkeypatch):
        """Line inside a changed hunk gets IN_SCOPE:in_hunk."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": [(10, 20)]}
        )
        refs = [{"file": "src/auth.py", "lines": [15]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:15"] == "IN_SCOPE:in_hunk"

    def test_line_near_hunk(self, mod, monkeypatch):
        """Line within ±5 of a hunk gets IN_SCOPE:near_hunk."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": [(10, 20)]}
        )
        refs = [{"file": "src/auth.py", "lines": [24]}]  # 4 lines after hunk end
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:24"] == "IN_SCOPE:near_hunk"

    def test_line_before_hunk_near(self, mod, monkeypatch):
        """Line within 5 lines before a hunk gets IN_SCOPE:near_hunk."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": [(10, 20)]}
        )
        refs = [{"file": "src/auth.py", "lines": [6]}]  # 4 lines before hunk start
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:6"] == "IN_SCOPE:near_hunk"

    def test_line_far_from_hunk(self, mod, monkeypatch):
        """Line far from any hunk gets OUT_OF_SCOPE:not_in_hunk."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": [(10, 20)]}
        )
        refs = [{"file": "src/auth.py", "lines": [100]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:100"] == "OUT_OF_SCOPE:not_in_hunk"

    def test_multiple_hunks(self, mod, monkeypatch):
        """Lines near different hunks in the same file."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": [(10, 15), (50, 55)]}
        )
        refs = [{"file": "src/auth.py", "lines": [12, 30, 53]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:12"] == "IN_SCOPE:in_hunk"
        assert result["src/auth.py:30"] == "OUT_OF_SCOPE:not_in_hunk"
        assert result["src/auth.py:53"] == "IN_SCOPE:in_hunk"

    def test_hunk_boundary_exact(self, mod, monkeypatch):
        """Line exactly at hunk boundary is in_hunk."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": [(10, 20)]}
        )
        refs = [{"file": "src/auth.py", "lines": [10, 20]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:10"] == "IN_SCOPE:in_hunk"
        assert result["src/auth.py:20"] == "IN_SCOPE:in_hunk"

    def test_proximity_boundary_exact(self, mod, monkeypatch):
        """Line exactly at proximity boundary (±5) is near_hunk."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": [(10, 20)]}
        )
        # 5 lines after hunk end = line 25
        refs = [{"file": "src/auth.py", "lines": [5, 25]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:5"] == "IN_SCOPE:near_hunk"
        assert result["src/auth.py:25"] == "IN_SCOPE:near_hunk"

    def test_proximity_boundary_just_outside(self, mod, monkeypatch):
        """Line one beyond proximity boundary (±6) is not_in_hunk."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": [(10, 20)]}
        )
        # 6 lines after hunk end = line 26, 6 before start = line 4
        refs = [{"file": "src/auth.py", "lines": [4, 26]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:4"] == "OUT_OF_SCOPE:not_in_hunk"
        assert result["src/auth.py:26"] == "OUT_OF_SCOPE:not_in_hunk"

    def test_suffix_matching_with_hunks(self, mod, monkeypatch):
        """Agent uses repo-relative path, diff uses same — suffix match works."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": [(10, 20)]}
        )
        refs = [{"file": "src/auth.py", "lines": [15, 100]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:15"] == "IN_SCOPE:in_hunk"
        assert result["src/auth.py:100"] == "OUT_OF_SCOPE:not_in_hunk"

    def test_file_not_in_diff_with_hunks(self, mod, monkeypatch):
        """File not in changed_files stays OUT_OF_SCOPE regardless of hunks."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": [(10, 20)]}
        )
        refs = [{"file": "src/other.py", "lines": [15]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/other.py:15"] == "OUT_OF_SCOPE:file_not_in_diff"

    def test_deletion_only_file_with_markers(self, mod, monkeypatch):
        """Deletion-only hunks produce zero-width markers for proximity matching."""
        # Deletion at new-side line 10 → marker (10, 10).
        # Findings near the deletion are in scope; far ones are not.
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": [(10, 10)]}
        )
        refs = [{"file": "src/auth.py", "lines": [10, 14, 50]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:10"] == "IN_SCOPE:in_hunk"
        assert result["src/auth.py:14"] == "IN_SCOPE:near_hunk"
        assert result["src/auth.py:50"] == "OUT_OF_SCOPE:not_in_hunk"

    def test_empty_hunk_list_falls_back_to_in_scope(self, mod, monkeypatch):
        """Defensive: file with truly empty hunk list → IN_SCOPE fallback."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/auth.py": []}
        )
        refs = [{"file": "src/auth.py", "lines": [5]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:5"] == "IN_SCOPE:in_hunk"

    def test_file_not_in_diff_hunks_falls_back(self, mod, monkeypatch):
        """File in changed_files but not in diff_hunks → fallback IN_SCOPE:in_hunk."""
        # This happens when git diff fails or the file has a suffix-matching miss.
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: {"src/other.py": [(1, 5)]}
        )
        refs = [{"file": "src/auth.py", "lines": [10]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:10"] == "IN_SCOPE:in_hunk"


# ===========================================================================
# TestParseDiffHunks
# ===========================================================================

class TestParseDiffHunks:
    """Tests for _parse_diff_hunks() helper."""

    def test_parses_single_file_single_hunk(self, mod, monkeypatch):
        """Parses a simple single-file, single-hunk diff."""
        diff_output = (
            "diff --git a/src/auth.py b/src/auth.py\n"
            "--- a/src/auth.py\n"
            "+++ b/src/auth.py\n"
            "@@ -10,3 +10,5 @@ def login():\n"
            "+    new_line_1\n"
            "+    new_line_2\n"
        )
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": diff_output, "stderr": ""
            })()
        )
        result = mod._parse_diff_hunks("abc..HEAD")
        assert "src/auth.py" in result
        assert result["src/auth.py"] == [(10, 14)]

    def test_parses_multiple_hunks(self, mod, monkeypatch):
        """Parses multiple hunks in one file."""
        diff_output = (
            "diff --git a/src/auth.py b/src/auth.py\n"
            "--- a/src/auth.py\n"
            "+++ b/src/auth.py\n"
            "@@ -5,0 +5,2 @@\n"
            "+a\n+b\n"
            "@@ -20,0 +22,3 @@\n"
            "+c\n+d\n+e\n"
        )
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": diff_output, "stderr": ""
            })()
        )
        result = mod._parse_diff_hunks("abc..HEAD")
        assert result["src/auth.py"] == [(5, 6), (22, 24)]

    def test_parses_multiple_files(self, mod, monkeypatch):
        """Parses hunks across multiple files."""
        diff_output = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n"
            "+++ b/src/a.py\n"
            "@@ -1,0 +1,1 @@\n"
            "+x\n"
            "diff --git a/src/b.py b/src/b.py\n"
            "--- a/src/b.py\n"
            "+++ b/src/b.py\n"
            "@@ -10,0 +10,2 @@\n"
            "+y\n+z\n"
        )
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": diff_output, "stderr": ""
            })()
        )
        result = mod._parse_diff_hunks("abc..HEAD")
        assert result["src/a.py"] == [(1, 1)]
        assert result["src/b.py"] == [(10, 11)]

    def test_handles_single_line_hunk(self, mod, monkeypatch):
        """A single-line hunk (no count) parses correctly."""
        diff_output = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n"
            "+++ b/src/a.py\n"
            "@@ -5 +5 @@\n"  # no comma = count of 1
            "+replacement\n"
        )
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": diff_output, "stderr": ""
            })()
        )
        result = mod._parse_diff_hunks("abc..HEAD")
        assert result["src/a.py"] == [(5, 5)]

    def test_preserves_pure_deletion_hunks_as_markers(self, mod, monkeypatch):
        """A hunk with +count=0 (pure deletion) is stored as a zero-width marker."""
        diff_output = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n"
            "+++ b/src/a.py\n"
            "@@ -5,3 +5,0 @@\n"  # 0 new lines = deletion only
        )
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": diff_output, "stderr": ""
            })()
        )
        result = mod._parse_diff_hunks("abc..HEAD")
        assert result["src/a.py"] == [(5, 5)]

    def test_git_failure_returns_empty(self, mod, monkeypatch):
        """Non-zero exit code returns empty dict."""
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 1, "stdout": "", "stderr": "fatal: bad range"
            })()
        )
        result = mod._parse_diff_hunks("bad..range")
        assert result == {}


# ===========================================================================
# TestLineNearHunk
# ===========================================================================

class TestLineNearHunk:
    """Tests for _line_near_hunk() helper."""

    def test_line_inside_hunk(self, mod):
        """Line inside hunk range with proximity=0."""
        assert mod._line_near_hunk(15, [(10, 20)], proximity=0) is True

    def test_line_at_boundary(self, mod):
        """Line at exact boundary with proximity=0."""
        assert mod._line_near_hunk(10, [(10, 20)], proximity=0) is True
        assert mod._line_near_hunk(20, [(10, 20)], proximity=0) is True

    def test_line_just_outside(self, mod):
        """Line one beyond boundary with proximity=0."""
        assert mod._line_near_hunk(9, [(10, 20)], proximity=0) is False
        assert mod._line_near_hunk(21, [(10, 20)], proximity=0) is False

    def test_line_within_proximity(self, mod):
        """Line within proximity range."""
        assert mod._line_near_hunk(7, [(10, 20)], proximity=5) is True  # 3 before
        assert mod._line_near_hunk(23, [(10, 20)], proximity=5) is True  # 3 after

    def test_line_at_proximity_boundary(self, mod):
        """Line at exact proximity boundary."""
        assert mod._line_near_hunk(5, [(10, 20)], proximity=5) is True
        assert mod._line_near_hunk(25, [(10, 20)], proximity=5) is True

    def test_line_beyond_proximity(self, mod):
        """Line beyond proximity range."""
        assert mod._line_near_hunk(4, [(10, 20)], proximity=5) is False
        assert mod._line_near_hunk(26, [(10, 20)], proximity=5) is False

    def test_multiple_hunks(self, mod):
        """Checks against all hunks."""
        hunks = [(10, 15), (50, 55)]
        assert mod._line_near_hunk(12, hunks, proximity=0) is True
        assert mod._line_near_hunk(52, hunks, proximity=0) is True
        assert mod._line_near_hunk(30, hunks, proximity=0) is False

    def test_empty_hunks(self, mod):
        """Empty hunk list always returns False."""
        assert mod._line_near_hunk(10, [], proximity=5) is False


# ===========================================================================
# TestFindFileHunks
# ===========================================================================

class TestFindFileHunks:
    """Tests for _find_file_hunks() helper."""

    def test_exact_match(self, mod):
        """Exact file path match."""
        hunks = {"src/auth.py": [(10, 20)]}
        assert mod._find_file_hunks("src/auth.py", hunks) == [(10, 20)]

    def test_suffix_match(self, mod):
        """Suffix matching for different path prefixes."""
        hunks = {"src/auth.py": [(10, 20)]}
        assert mod._find_file_hunks("/abs/path/src/auth.py", hunks) == [(10, 20)]

    def test_no_match(self, mod):
        """No matching file returns None."""
        hunks = {"src/auth.py": [(10, 20)]}
        assert mod._find_file_hunks("src/other.py", hunks) is None

    def test_empty_hunks(self, mod):
        """Empty hunks dict returns None."""
        assert mod._find_file_hunks("src/auth.py", {}) is None

    def test_file_with_empty_hunk_list(self, mod):
        """File present in dict with empty hunk list returns that empty list."""
        hunks = {"src/auth.py": []}
        assert mod._find_file_hunks("src/auth.py", hunks) == []


# ===========================================================================
# TestResolveOutputBuilderPath
# ===========================================================================

class TestResolveOutputBuilderPath:
    """Tests for resolve_output_builder_path()."""

    def test_resolves_to_existing_file(self, mod):
        """Should resolve to a path ending in output.py that exists."""
        path = mod.resolve_output_builder_path()
        assert path.endswith("output.py")
        assert os.path.isfile(path)

    def test_points_to_agent_output(self, mod):
        """Should point to scripts/review/agent/output.py."""
        path = mod.resolve_output_builder_path()
        assert "scripts/review/agent/output.py" in path.replace("\\", "/")


# ===========================================================================
# TestFullScript — subprocess integration tests
# ===========================================================================

class TestFullScript:
    """Integration tests running the complete script via subprocess."""

    def _run(self, *args, cwd=None):
        """Run the script and return the CompletedProcess."""
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)

    def test_produces_valid_output_json(self, tmp_path):
        """Full run produces reconciliation-context.json with all fields."""
        # Create a review file
        review = _make_review_json(
            reviewer="security",
            issues=[_make_issue(file="src/auth.py", line=10)],
        )
        (tmp_path / "security-review.json").write_text(json.dumps(review))

        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc123..HEAD",
            "--changed-files", "src/auth.py,src/db.py",
            "--change-purpose", "Fix auth bug",
            "--pr-id", "42",
            cwd=tmp_path,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Verify stdout has status JSON
        stdout_json = json.loads(result.stdout.strip())
        assert stdout_json["status"] == "ok"

        # Verify output file exists and has all expected fields
        ctx_path = tmp_path / "reconciliation-context.json"
        assert ctx_path.is_file()

        ctx = json.loads(ctx_path.read_text())
        expected_keys = {
            "agent_findings",
            "source_snippets",
            "scope_annotations",
            "changed_files",
            "git_range",
            "change_purpose",
            "pr_id",
            "output_dir",
            "output_builder_path",
        }
        assert set(ctx.keys()) == expected_keys

        # Verify specific values
        assert "security-review" in ctx["agent_findings"]
        assert ctx["changed_files"] == ["src/auth.py", "src/db.py"]
        assert ctx["git_range"] == "abc123..HEAD"
        assert ctx["change_purpose"] == "Fix auth bug"
        assert ctx["pr_id"] == "42"
        assert ctx["output_builder_path"].endswith("output.py")

    def test_empty_output_dir(self, tmp_path):
        """Runs successfully with no review files."""
        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc..HEAD",
            cwd=tmp_path,
        )
        assert result.returncode == 0

        ctx = json.loads((tmp_path / "reconciliation-context.json").read_text())
        assert ctx["agent_findings"] == {}

    def test_missing_required_args(self, tmp_path):
        """Missing --output-dir or --git-range exits with code 2 (argparse)."""
        result = self._run("--output-dir", str(tmp_path), cwd=tmp_path)
        assert result.returncode == 2  # argparse exits with 2

    def test_scope_annotations_present(self, tmp_path):
        """Scope annotations are correctly populated with file:line keys."""
        review = _make_review_json(
            issues=[
                _make_issue(file="src/auth.py", line=10),
                _make_issue(file="src/other.py", line=20),
            ],
        )
        (tmp_path / "security-review.json").write_text(json.dumps(review))

        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc..HEAD",
            "--changed-files", "src/auth.py",
            cwd=tmp_path,
        )
        assert result.returncode == 0

        ctx = json.loads((tmp_path / "reconciliation-context.json").read_text())
        # git diff will fail in tmp_path (no real git repo), so files in
        # changed_files fall back to IN_SCOPE:in_hunk
        assert ctx["scope_annotations"]["src/auth.py:10"] == "IN_SCOPE:in_hunk"
        assert ctx["scope_annotations"]["src/other.py:20"] == "OUT_OF_SCOPE:file_not_in_diff"

    def test_multiple_agents(self, tmp_path):
        """Multiple agent review files are all loaded."""
        for agent in ["security", "performance", "patterns"]:
            review = _make_review_json(
                reviewer=agent,
                issues=[_make_issue(file=f"src/{agent}.py", line=10)],
            )
            (tmp_path / f"{agent}-review.json").write_text(json.dumps(review))

        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc..HEAD",
            cwd=tmp_path,
        )
        assert result.returncode == 0

        ctx = json.loads((tmp_path / "reconciliation-context.json").read_text())
        assert len(ctx["agent_findings"]) == 3
        assert "security-review" in ctx["agent_findings"]
        assert "performance-review" in ctx["agent_findings"]
        assert "patterns-review" in ctx["agent_findings"]

    def test_dispatched_agents_empty_string_produces_empty_list(self, tmp_path):
        """--dispatched-agents '' means 0 agents dispatched, not unknown."""
        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc..HEAD",
            "--dispatched-agents", "",
            cwd=tmp_path,
        )
        assert result.returncode == 0

        ctx = json.loads((tmp_path / "reconciliation-context.json").read_text())
        # Key present with empty list — "0 agents dispatched"
        assert "dispatched_agents" in ctx
        assert ctx["dispatched_agents"] == []

    def test_no_dispatched_agents_flag_omits_key(self, tmp_path):
        """Without --dispatched-agents, the key is absent (metadata unknown)."""
        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc..HEAD",
            cwd=tmp_path,
        )
        assert result.returncode == 0

        ctx = json.loads((tmp_path / "reconciliation-context.json").read_text())
        assert "dispatched_agents" not in ctx

    def test_dispatched_agents_with_names_produces_list(self, tmp_path):
        """--dispatched-agents with names produces the expected list."""
        review = _make_review_json(
            reviewer="security",
            issues=[_make_issue(file="src/auth.py", line=10)],
        )
        (tmp_path / "security-review.json").write_text(json.dumps(review))

        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc..HEAD",
            "--dispatched-agents", "security-reviewer,performance-reviewer",
            cwd=tmp_path,
        )
        assert result.returncode == 0

        ctx = json.loads((tmp_path / "reconciliation-context.json").read_text())
        # Names are normalized: -reviewer → -review to match agent_findings keys
        assert ctx["dispatched_agents"] == [
            "security-review", "performance-review"
        ]

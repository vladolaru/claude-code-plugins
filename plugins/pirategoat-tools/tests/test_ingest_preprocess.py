"""
Tests for ingest-preprocess.py — deterministic scope checking and pre-classification.

Tests the preprocessor that reduces ingest LLM steps from 6 to 3 by handling
scope checking, ID assignment, and pre-classification deterministically.

Zero external dependencies beyond stdlib + pytest.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup — import ingest-preprocess as a module
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "ingest-preprocess.py"

# Load module
_spec = importlib.util.spec_from_file_location("ingest_preprocess", str(SCRIPT_PATH))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# Helpers — mock data factories
# ---------------------------------------------------------------------------


def _make_finding(
    title="Test finding",
    file="src/app.php",
    line=10,
    severity="medium",
    source_agent="pr",
    confidence=0.9,
    description="A test issue",
    recommendation="Fix it",
    category="general",
):
    """Create a single finding in reconciled.json issue format."""
    issue = {
        "id": "abcd1234",
        "category": category,
        "severity": severity,
        "title": f"[{source_agent}] {title}",
        "description": description,
        "file": file,
        "line": line,
        "recommendation": recommendation,
        "confidence": confidence,
    }
    return issue


def _make_reconciled_json(findings):
    """Build a reconciled.json structure from a list of findings."""
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        sev = f.get("severity", "medium")
        if sev in severity_counts:
            severity_counts[sev] += 1

    return {
        "pr_id": "test-123",
        "reviewer": "reconciliator",
        "timestamp": "2026-03-01T12:00:00",
        "version": "1.0.0",
        "verdict": "comment",
        "summary": {
            "total_issues": len(findings),
            "by_severity": severity_counts,
        },
        "issues": findings,
        "recommendations": None,
        "positive_observations": None,
        "meta": {
            "files_reviewed": 5,
            "review_duration_ms": 1000,
            "confidence_score": 0.9,
            "tool_results_used": None,
        },
    }


def _make_diff_output(hunks):
    """Build a git diff output string from hunk definitions.

    Each hunk is a tuple: (new_start, new_count)
    """
    lines = [
        "diff --git a/src/app.php b/src/app.php",
        "index abc1234..def5678 100644",
        "--- a/src/app.php",
        "+++ b/src/app.php",
    ]
    for new_start, new_count in hunks:
        lines.append(f"@@ -1,1 +{new_start},{new_count} @@")
        for i in range(new_count):
            lines.append(f"+line {new_start + i}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_output_dir():
    """Create a temporary output directory with reconciled.json."""
    with tempfile.TemporaryDirectory(prefix="test-ingest-") as d:
        yield d


# =============================================================================
# parse_diff_hunks() unit tests
# =============================================================================


class TestParseDiffHunks:
    """Test the hunk parser function directly."""

    def test_single_hunk(self):
        diff = "@@ -10,5 +20,10 @@ function foo()\n+added line"
        hunks = _mod.parse_diff_hunks(diff)
        assert hunks == [(20, 29)]

    def test_multiple_hunks(self):
        diff = (
            "@@ -1,3 +1,5 @@ header\n"
            "+line\n"
            "@@ -20,4 +22,8 @@ another header\n"
            "+line2\n"
        )
        hunks = _mod.parse_diff_hunks(diff)
        assert hunks == [(1, 5), (22, 29)]

    def test_single_line_hunk(self):
        """Hunk with count=1 (shown as just +N without ,count)."""
        diff = "@@ -5 +10 @@ single line\n+changed"
        hunks = _mod.parse_diff_hunks(diff)
        assert hunks == [(10, 10)]

    def test_count_of_one_explicit(self):
        """Hunk with explicit ,1 count."""
        diff = "@@ -5,1 +10,1 @@ one line\n+changed"
        hunks = _mod.parse_diff_hunks(diff)
        assert hunks == [(10, 10)]

    def test_no_hunks(self):
        diff = "diff --git a/file b/file\nindex abc..def\n--- a/file\n+++ b/file\n"
        hunks = _mod.parse_diff_hunks(diff)
        assert hunks == []

    def test_empty_string(self):
        hunks = _mod.parse_diff_hunks("")
        assert hunks == []

    def test_zero_count_hunk(self):
        """Hunk with count=0 (deletion only, no new lines)."""
        diff = "@@ -5,3 +5,0 @@ deleted section\n-line1\n-line2\n-line3"
        hunks = _mod.parse_diff_hunks(diff)
        # A hunk with count=0 means no new lines — should produce empty range
        assert hunks == [(5, 4)]  # start=5, end=5+0-1=4, empty range

    def test_large_hunk(self):
        diff = "@@ -1,100 +1,200 @@ big change\n+line"
        hunks = _mod.parse_diff_hunks(diff)
        assert hunks == [(1, 200)]


# =============================================================================
# Scope check tests
# =============================================================================


class TestScopeCheckFileInDiff:
    """Finding references a file in changed_files with line in hunk -> IN_HUNK."""

    def test_file_in_diff_line_in_hunk(self, tmp_output_dir):
        findings = [_make_finding(file="src/app.php", line=10)]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/app.php", "src/utils.php"]
        diff_hunks = {"src/app.php": [(1, 50)]}

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        assert len(result["findings"]) == 1
        assert result["findings"][0]["scope_status"] == "IN_HUNK"


class TestScopeCheckFileNotInDiff:
    """Finding references a file NOT in changed_files -> OUT_OF_SCOPE."""

    def test_file_not_in_diff_is_out_of_scope(self, tmp_output_dir):
        findings = [_make_finding(file="src/other.php", line=10)]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/app.php"]
        diff_hunks = {"src/app.php": [(1, 50)]}

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        assert len(result["findings"]) == 1
        assert result["findings"][0]["scope_status"] == "OUT_OF_SCOPE"
        assert "not in diff" in result["findings"][0]["scope_reason"]


class TestScopeCheckLineInHunk:
    """Finding line falls within a diff hunk -> IN_HUNK."""

    def test_line_in_hunk(self, tmp_output_dir):
        findings = [_make_finding(file="src/app.php", line=25)]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/app.php"]
        diff_hunks = {"src/app.php": [(20, 30)]}  # lines 20-30

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        assert result["findings"][0]["scope_status"] == "IN_HUNK"
        assert "in changed hunk" in result["findings"][0]["scope_reason"]


class TestScopeCheckLineOutsideHunk:
    """Finding line is outside all hunks (pre-existing code) -> OUT_OF_SCOPE."""

    def test_line_outside_hunk_is_out_of_scope(self, tmp_output_dir):
        findings = [_make_finding(file="src/app.php", line=100)]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/app.php"]
        diff_hunks = {"src/app.php": [(20, 30)]}  # lines 20-30 only

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        assert result["findings"][0]["scope_status"] == "OUT_OF_SCOPE"
        assert "pre-existing" in result["findings"][0]["scope_reason"]


class TestScopeCheckNoLineNumber:
    """Finding has no line -> FILE_LEVEL if file in diff."""

    def test_no_line_file_in_diff_is_file_level(self, tmp_output_dir):
        findings = [_make_finding(file="src/app.php", line=None)]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/app.php"]
        diff_hunks = {"src/app.php": [(20, 30)]}

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        assert result["findings"][0]["scope_status"] == "FILE_LEVEL"
        assert "no line" in result["findings"][0]["scope_reason"].lower()


# =============================================================================
# Stable ID tests
# =============================================================================


class TestStableIDs:
    """Findings get sequential IDs (F1, F2, ...) in consistent order."""

    def test_ids_are_sequential(self, tmp_output_dir):
        findings = [
            _make_finding(title="Low issue", severity="low", file="src/a.php", line=1),
            _make_finding(title="Critical issue", severity="critical", file="src/b.php", line=2),
            _make_finding(title="High issue", severity="high", file="src/c.php", line=3),
        ]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/a.php", "src/b.php", "src/c.php"]
        diff_hunks = {
            "src/a.php": [(1, 10)],
            "src/b.php": [(1, 10)],
            "src/c.php": [(1, 10)],
        }

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        ids = [f["id"] for f in result["findings"]]
        assert ids == ["F1", "F2", "F3"]

    def test_sorted_by_severity_then_file_then_line(self, tmp_output_dir):
        findings = [
            _make_finding(title="Low A", severity="low", file="src/a.php", line=10),
            _make_finding(title="Critical B", severity="critical", file="src/b.php", line=5),
            _make_finding(title="High A", severity="high", file="src/a.php", line=1),
            _make_finding(title="High B", severity="high", file="src/b.php", line=20),
        ]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/a.php", "src/b.php"]
        diff_hunks = {
            "src/a.php": [(1, 50)],
            "src/b.php": [(1, 50)],
        }

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        titles = [f["title"] for f in result["findings"]]
        # critical first, then high (sorted by file, then line), then low
        assert titles[0] == "[pr] Critical B"  # critical
        assert titles[1] == "[pr] High A"      # high, a.php:1
        assert titles[2] == "[pr] High B"      # high, b.php:20
        assert titles[3] == "[pr] Low A"        # low


# =============================================================================
# Pre-classification tests
# =============================================================================


class TestPreClassification:
    """IN_HUNK findings -> 'needs_verification'; OUT_OF_SCOPE -> 'out_of_scope'."""

    def test_in_scope_needs_verification(self, tmp_output_dir):
        findings = [_make_finding(file="src/app.php", line=25)]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/app.php"]
        diff_hunks = {"src/app.php": [(20, 30)]}

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        assert result["findings"][0]["pre_classification"] == "needs_verification"

    def test_out_of_scope_classified(self, tmp_output_dir):
        findings = [_make_finding(file="src/other.php", line=10)]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/app.php"]
        diff_hunks = {"src/app.php": [(1, 50)]}

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        assert result["findings"][0]["pre_classification"] == "out_of_scope"


# =============================================================================
# Edge case tests
# =============================================================================


class TestEdgeCaseEmptyFindings:
    """No findings -> valid empty output."""

    def test_empty_findings(self, tmp_output_dir):
        reconciled = _make_reconciled_json([])
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(1, 50)]},
            git_range="main..HEAD",
        )

        assert result["findings"] == []
        assert result["summary"]["total"] == 0
        assert result["summary"]["in_scope"] == 0
        assert result["summary"]["out_of_scope"] == 0


class TestEdgeCaseMultiHunkDiff:
    """File with multiple hunks, finding in second hunk -> IN_HUNK."""

    def test_finding_in_second_hunk(self, tmp_output_dir):
        findings = [_make_finding(file="src/app.php", line=150)]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/app.php"]
        # Two hunks: lines 10-20 and lines 140-160
        diff_hunks = {"src/app.php": [(10, 20), (140, 160)]}

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        assert result["findings"][0]["scope_status"] == "IN_HUNK"
        assert "in changed hunk" in result["findings"][0]["scope_reason"]

    def test_finding_between_hunks_is_out_of_scope(self, tmp_output_dir):
        findings = [_make_finding(file="src/app.php", line=80)]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/app.php"]
        # Two hunks: lines 10-20 and lines 140-160 — line 80 is between them
        diff_hunks = {"src/app.php": [(10, 20), (140, 160)]}

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        assert result["findings"][0]["scope_status"] == "OUT_OF_SCOPE"
        assert "pre-existing" in result["findings"][0]["scope_reason"]


# =============================================================================
# Summary statistics tests
# =============================================================================


class TestSummaryStatistics:
    """Output summary counts are correct."""

    def test_summary_counts(self, tmp_output_dir):
        findings = [
            _make_finding(title="In scope 1", file="src/app.php", line=25, severity="critical"),
            _make_finding(title="In scope 2", file="src/app.php", line=26, severity="high"),
            _make_finding(title="Out of scope 1", file="src/other.php", line=10, severity="medium"),
            _make_finding(title="Out of scope 2", file="src/app.php", line=100, severity="low"),
        ]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/app.php"]
        diff_hunks = {"src/app.php": [(20, 30)]}  # lines 20-30

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        assert result["summary"]["total"] == 4
        assert result["summary"]["in_scope"] == 2
        assert result["summary"]["out_of_scope"] == 2
        assert result["summary"]["needs_verification"] == 2
        assert result["summary"]["auto_classified"] == 2


# =============================================================================
# Source agent extraction tests
# =============================================================================


class TestSourceAgentExtraction:
    """Source agents are correctly extracted from finding titles."""

    def test_single_agent_from_title(self, tmp_output_dir):
        findings = [_make_finding(title="SQL Injection", source_agent="security", file="src/db.php", line=5)]
        reconciled = _make_reconciled_json(findings)
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        changed_files = ["src/db.php"]
        diff_hunks = {"src/db.php": [(1, 50)]}

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=changed_files,
            diff_hunks=diff_hunks,
            git_range="main..HEAD",
        )

        assert "security" in result["findings"][0]["source_agents"]


# =============================================================================
# Output format tests
# =============================================================================


class TestOutputFormat:
    """Output has all required fields."""

    def test_has_required_top_level_fields(self, tmp_output_dir):
        reconciled = _make_reconciled_json([_make_finding()])
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(1, 50)]},
            git_range="main..HEAD",
        )

        assert "git_range" in result
        assert "changed_files" in result
        assert "findings" in result
        assert "summary" in result

    def test_finding_has_required_fields(self, tmp_output_dir):
        reconciled = _make_reconciled_json([_make_finding()])
        reconciled_path = os.path.join(tmp_output_dir, "reconciled.json")
        with open(reconciled_path, "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(1, 50)]},
            git_range="main..HEAD",
        )

        finding = result["findings"][0]
        required_fields = [
            "id", "title", "file", "line", "severity",
            "source_agents", "confidence", "scope_status",
            "scope_reason", "pre_classification",
        ]
        for field in required_fields:
            assert field in finding, f"Missing field: {field}"


# =============================================================================
# Reconciled file loading tests
# =============================================================================


class TestReconciledFileLoading:
    """Tests for loading reconciled findings from different file formats."""

    def test_loads_reconciled_structured_json_first(self, tmp_output_dir):
        """Prefers reconciled-structured.json over reconciled.json."""
        # Write reconciled-structured.json
        structured_findings = [_make_finding(title="From structured")]
        structured = _make_reconciled_json(structured_findings)
        with open(os.path.join(tmp_output_dir, "reconciled-structured.json"), "w") as f:
            json.dump(structured, f)

        # Also write reconciled.json with different content
        fallback_findings = [_make_finding(title="From fallback")]
        fallback = _make_reconciled_json(fallback_findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(fallback, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(1, 50)]},
            git_range="main..HEAD",
        )

        assert "[pr] From structured" in result["findings"][0]["title"]

    def test_falls_back_to_reconciled_json(self, tmp_output_dir):
        """Falls back to reconciled.json when reconciled-structured.json is absent."""
        findings = [_make_finding(title="From fallback")]
        reconciled = _make_reconciled_json(findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(1, 50)]},
            git_range="main..HEAD",
        )

        assert "[pr] From fallback" in result["findings"][0]["title"]

    def test_raises_on_no_reconciled_file(self, tmp_output_dir):
        """Raises when no reconciled file exists."""
        with pytest.raises(FileNotFoundError):
            _mod.preprocess_findings(
                output_dir=tmp_output_dir,
                changed_files=["src/app.php"],
                diff_hunks={"src/app.php": [(1, 50)]},
                git_range="main..HEAD",
            )

    def test_clusters_format_without_issues_key(self, tmp_output_dir):
        """reconciled-structured.json with clusters-only format (old reconcile-reviews.py)."""
        # Old format: produced by reconcile-reviews.py before step 8.5 was added.
        # Has "clusters" with "canonical" sub-objects but no top-level "issues" key.
        canonical = {
            "title": "[security] SQL injection",
            "file": "src/app.php",
            "line": 10,
            "severity": "high",
            "confidence": 0.9,
            "source_agents": ["security"],
            "description": "Unescaped user input in SQL query",
            "category": "security",
        }
        clusters_format = {
            "total_findings": 1,
            "deduplicated_findings": 1,
            "clusters": [
                {
                    "cluster_id": "C1",
                    "findings": ["security-review:issue-1"],
                    "canonical": canonical,
                }
            ],
            "severity_disagreements": [],
            "skipped_agents": [],
            "agent_stats": {},
        }
        with open(os.path.join(tmp_output_dir, "reconciled-structured.json"), "w") as f:
            json.dump(clusters_format, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(1, 50)]},
            git_range="main..HEAD",
        )

        assert len(result["findings"]) == 1
        assert "[security] SQL injection" in result["findings"][0]["title"]
        assert result["findings"][0]["scope_status"] == "IN_HUNK"


# =============================================================================
# CLI integration tests
# =============================================================================


class TestCLIIntegration:
    """CLI invocation tests using mocked git commands."""

    def test_writes_output_file(self, tmp_output_dir):
        """CLI writes ingest-preprocessed.json to output-dir."""
        findings = [_make_finding(file="src/app.php", line=10)]
        reconciled = _make_reconciled_json(findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(reconciled, f)

        # Mock subprocess.run to return controlled git output
        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stderr = ""

            if "diff --name-only" in cmd_str:
                mock_result.stdout = "src/app.php\n"
            elif "diff" in cmd_str and "--" in cmd_str:
                mock_result.stdout = _make_diff_output([(1, 50)])
            else:
                mock_result.stdout = ""
            return mock_result

        with patch("subprocess.run", side_effect=mock_run):
            result = subprocess.run = mock_run  # noqa
            # Directly invoke the main logic instead
            _mod.run_preprocess(
                output_dir=tmp_output_dir,
                git_range="main..HEAD",
            )

        output_path = os.path.join(tmp_output_dir, "ingest-preprocessed.json")
        assert os.path.exists(output_path), "ingest-preprocessed.json not created"

        with open(output_path) as f:
            data = json.load(f)
        assert data["git_range"] == "main..HEAD"
        assert len(data["findings"]) == 1


class TestCLISubprocess:
    """Test running the script as a subprocess."""

    def test_missing_output_dir_exits_1(self):
        """Missing --output-dir should error."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--git-range", "main..HEAD"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_missing_git_range_exits_1(self):
        """Missing --git-range should error."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--output-dir", "/tmp/test"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0


# =============================================================================
# INTERACTS_WITH_CHANGE scope tests
# =============================================================================


class TestInteractsWithChange:
    """Finding near a hunk boundary → INTERACTS_WITH_CHANGE."""

    def test_line_just_outside_hunk_is_interacts(self, tmp_output_dir):
        """Line 1 below hunk end → INTERACTS_WITH_CHANGE."""
        findings = [_make_finding(file="src/app.php", line=31)]
        reconciled = _make_reconciled_json(findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},  # hunk: 20-30
            git_range="main..HEAD",
        )
        assert result["findings"][0]["scope_status"] == "INTERACTS_WITH_CHANGE"
        assert "near" in result["findings"][0]["scope_reason"]

    def test_line_just_above_hunk_is_interacts(self, tmp_output_dir):
        """Line 5 above hunk start → INTERACTS_WITH_CHANGE."""
        findings = [_make_finding(file="src/app.php", line=15)]
        reconciled = _make_reconciled_json(findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},  # hunk: 20-30
            git_range="main..HEAD",
        )
        assert result["findings"][0]["scope_status"] == "INTERACTS_WITH_CHANGE"

    def test_line_at_proximity_boundary_is_interacts(self, tmp_output_dir):
        """Line exactly HUNK_PROXIMITY lines away → still INTERACTS_WITH_CHANGE."""
        proximity = _mod.HUNK_PROXIMITY  # 5
        findings = [_make_finding(file="src/app.php", line=20 - proximity)]
        reconciled = _make_reconciled_json(findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},
            git_range="main..HEAD",
        )
        assert result["findings"][0]["scope_status"] == "INTERACTS_WITH_CHANGE"

    def test_line_beyond_proximity_is_out_of_scope(self, tmp_output_dir):
        """Line HUNK_PROXIMITY + 1 lines away → OUT_OF_SCOPE."""
        proximity = _mod.HUNK_PROXIMITY  # 5
        findings = [_make_finding(file="src/app.php", line=20 - proximity - 1)]
        reconciled = _make_reconciled_json(findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},
            git_range="main..HEAD",
        )
        assert result["findings"][0]["scope_status"] == "OUT_OF_SCOPE"

    def test_interacts_gets_needs_verification(self, tmp_output_dir):
        """INTERACTS_WITH_CHANGE findings get needs_verification pre-classification."""
        findings = [_make_finding(file="src/app.php", line=31)]
        reconciled = _make_reconciled_json(findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},
            git_range="main..HEAD",
        )
        assert result["findings"][0]["pre_classification"] == "needs_verification"


# =============================================================================
# FILE_LEVEL scope tests
# =============================================================================


class TestFileLevelScope:
    """No-line findings get FILE_LEVEL status."""

    def test_no_line_is_file_level(self, tmp_output_dir):
        findings = [_make_finding(file="src/app.php", line=None)]
        reconciled = _make_reconciled_json(findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},
            git_range="main..HEAD",
        )
        assert result["findings"][0]["scope_status"] == "FILE_LEVEL"
        assert result["findings"][0]["pre_classification"] == "needs_verification"

    def test_no_hunk_data_is_file_level(self, tmp_output_dir):
        """File in diff but no hunk data → FILE_LEVEL."""
        findings = [_make_finding(file="src/app.php", line=10)]
        reconciled = _make_reconciled_json(findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={},  # no hunk data
            git_range="main..HEAD",
        )
        assert result["findings"][0]["scope_status"] == "FILE_LEVEL"

    def test_file_level_counted_in_scope(self, tmp_output_dir):
        """FILE_LEVEL findings count toward in_scope total."""
        findings = [_make_finding(file="src/app.php", line=None)]
        reconciled = _make_reconciled_json(findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},
            git_range="main..HEAD",
        )
        assert result["summary"]["in_scope"] == 1
        assert result["summary"]["out_of_scope"] == 0


# =============================================================================
# Granular scope summary tests
# =============================================================================


class TestGranularScopeSummary:
    """Summary includes by_scope_status breakdown."""

    def test_by_scope_status_present(self, tmp_output_dir):
        findings = [
            _make_finding(title="In hunk", file="src/app.php", line=25),
            _make_finding(title="Near hunk", file="src/app.php", line=35),
            _make_finding(title="File level", file="src/app.php", line=None),
            _make_finding(title="Out of scope", file="src/other.php", line=10),
        ]
        reconciled = _make_reconciled_json(findings)
        with open(os.path.join(tmp_output_dir, "reconciled.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},
            git_range="main..HEAD",
        )

        by_status = result["summary"]["by_scope_status"]
        assert by_status["IN_HUNK"] == 1
        assert by_status["INTERACTS_WITH_CHANGE"] == 1
        assert by_status["FILE_LEVEL"] == 1
        assert by_status["OUT_OF_SCOPE"] == 1
        # Backward compat: in_scope = IN_HUNK + INTERACTS + FILE_LEVEL
        assert result["summary"]["in_scope"] == 3
        assert result["summary"]["out_of_scope"] == 1


# =============================================================================
# Structured source_agents propagation tests
# =============================================================================


class TestStructuredSourceAgents:
    """Ingest uses structured source_agents from reconciled output."""

    def test_uses_structured_source_agents(self, tmp_output_dir):
        """When source_agents field exists, use it directly."""
        finding = {
            "title": "SQL Injection",
            "file": "src/db.php",
            "line": 10,
            "severity": "high",
            "confidence": 0.9,
            "source_agents": ["security", "pr"],
            "description": "Test",
            "recommendation": "Fix it",
            "category": "sql",
        }
        reconciled = {
            "issues": [finding],
            "clusters": [],
            "total_findings": 1,
            "deduplicated_findings": 1,
        }
        with open(os.path.join(tmp_output_dir, "reconciled-structured.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/db.php"],
            diff_hunks={"src/db.php": [(1, 50)]},
            git_range="main..HEAD",
        )
        assert result["findings"][0]["source_agents"] == ["security", "pr"]

    def test_falls_back_to_bracket_extraction(self, tmp_output_dir):
        """Without source_agents field, falls back to bracket parsing."""
        finding = {
            "title": "[security] SQL Injection",
            "file": "src/db.php",
            "line": 10,
            "severity": "high",
            "confidence": 0.9,
            "description": "Test",
            "category": "sql",
        }
        reconciled = {
            "issues": [finding],
            "clusters": [],
            "total_findings": 1,
            "deduplicated_findings": 1,
        }
        with open(os.path.join(tmp_output_dir, "reconciled-structured.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/db.php"],
            diff_hunks={"src/db.php": [(1, 50)]},
            git_range="main..HEAD",
        )
        assert result["findings"][0]["source_agents"] == ["security"]

    def test_recommendation_propagated(self, tmp_output_dir):
        """Recommendation field is preserved from reconciled to preprocessed."""
        finding = {
            "title": "XSS",
            "file": "src/view.php",
            "line": 10,
            "severity": "high",
            "confidence": 0.9,
            "source_agents": ["security"],
            "description": "Unescaped output",
            "recommendation": "Use esc_html()",
            "category": "xss",
        }
        reconciled = {
            "issues": [finding],
            "clusters": [],
            "total_findings": 1,
            "deduplicated_findings": 1,
        }
        with open(os.path.join(tmp_output_dir, "reconciled-structured.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/view.php"],
            diff_hunks={"src/view.php": [(1, 50)]},
            git_range="main..HEAD",
        )
        assert result["findings"][0]["recommendation"] == "Use esc_html()"


# =============================================================================
# Ground truth fast-track tests
# =============================================================================


class TestGroundTruthFastTrack:
    """Tests for ground_truth_match -> verified pre-classification."""

    def test_ground_truth_match_classified_as_verified(self, tmp_output_dir):
        """Finding with ground_truth_match=True gets 'verified' classification."""
        finding = _make_finding(title="Unused var", file="src/app.php", line=25)
        finding["ground_truth_match"] = True
        finding["ground_truth_tool"] = "phpcs"
        reconciled = _make_reconciled_json([finding])
        with open(os.path.join(tmp_output_dir, "reconciled-structured.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},
            git_range="main..HEAD",
        )
        processed = result["findings"][0]
        assert processed["pre_classification"] == "verified"
        assert processed["scope_status"] == "IN_HUNK"

    def test_no_ground_truth_match_classified_as_needs_verification(self, tmp_output_dir):
        """Finding without ground_truth_match gets 'needs_verification'."""
        finding = _make_finding(title="Bug", file="src/app.php", line=25)
        reconciled = _make_reconciled_json([finding])
        with open(os.path.join(tmp_output_dir, "reconciled-structured.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},
            git_range="main..HEAD",
        )
        processed = result["findings"][0]
        assert processed["pre_classification"] == "needs_verification"

    def test_ground_truth_match_false_classified_as_needs_verification(self, tmp_output_dir):
        """Finding with ground_truth_match=False gets 'needs_verification'."""
        finding = _make_finding(title="Bug", file="src/app.php", line=25)
        finding["ground_truth_match"] = False
        reconciled = _make_reconciled_json([finding])
        with open(os.path.join(tmp_output_dir, "reconciled-structured.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},
            git_range="main..HEAD",
        )
        processed = result["findings"][0]
        assert processed["pre_classification"] == "needs_verification"

    def test_ground_truth_out_of_scope_stays_out(self, tmp_output_dir):
        """Ground truth match doesn't override out-of-scope classification."""
        finding = _make_finding(title="Bug", file="src/other.php", line=25)
        finding["ground_truth_match"] = True
        reconciled = _make_reconciled_json([finding])
        with open(os.path.join(tmp_output_dir, "reconciled-structured.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],  # other.php not in changed files
            diff_hunks={"src/app.php": [(20, 30)]},
            git_range="main..HEAD",
        )
        processed = result["findings"][0]
        assert processed["pre_classification"] == "out_of_scope"
        assert processed["scope_status"] == "OUT_OF_SCOPE"

    def test_verified_count_in_summary(self, tmp_output_dir):
        """Summary includes verified_by_ground_truth count."""
        f1 = _make_finding(title="GT match", file="src/app.php", line=25)
        f1["ground_truth_match"] = True
        f2 = _make_finding(title="No match", file="src/app.php", line=26)
        reconciled = _make_reconciled_json([f1, f2])
        with open(os.path.join(tmp_output_dir, "reconciled-structured.json"), "w") as f:
            json.dump(reconciled, f)

        result = _mod.preprocess_findings(
            output_dir=tmp_output_dir,
            changed_files=["src/app.php"],
            diff_hunks={"src/app.php": [(20, 30)]},
            git_range="main..HEAD",
        )
        assert result["summary"]["verified_by_ground_truth"] == 1
        assert result["summary"]["needs_verification"] == 1

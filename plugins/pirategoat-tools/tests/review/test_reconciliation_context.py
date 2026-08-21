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
sys.path.insert(0, str(SCRIPTS_DIR))

from review.agent.output import ReviewOutputBuilder


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

# Distinguishes "key absent" (legacy producer) from an explicit null/empty
# value — the two carry opposite meanings for deferred-review claims.
_ABSENT = object()


def _make_issue(
    severity="medium",
    title="Test issue",
    file="src/app.py",
    line=42,
    description="Some issue found",
    recommendation="Fix it",
    category="general",
    confidence=0.9,
    severity_floor=None,
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
    if severity_floor is not None:
        issue["severity_floor"] = severity_floor
    return issue


def _write_summary(
    output_dir, agent, files_with_diffs, budget_exceeded, *, domain=None,
    list_only=None,
):
    """Write one agent's scope-summary sidecar under its real filename.

    Keyed on the AGENT name rather than a hand-spelled filename so every
    coverage test addresses the sidecar the way the aggregator does;
    `domain` appends the secondary-summary suffix that adapter and
    multi-domain agents emit.
    """
    suffix = f"-{domain}" if domain else ""
    path = os.path.join(output_dir, f"{agent}-scope-summary{suffix}.json")
    with open(path, "w") as f:
        json.dump({
            "schema": 1,
            "domain": "x",
            "status": "OK",
            "files_with_diffs": files_with_diffs,
            "budget_exceeded_files": budget_exceeded,
            "list_only_files": list(list_only or []),
        }, f)


def _write_review(output_dir, stem, unreviewed=None, claims=_ABSENT):
    """Write <stem>.json — the real filename an agent's review carries.

    Takes the review STEM, not the agent name: several tests exist to pin
    the stem-derivation rule itself, so deriving it here would hide the
    thing under test.

    `claims` defaults to a sentinel so a test can distinguish a key-less
    legacy output from an explicit `deferred_reviewed: []`.
    """
    payload = {"reviewer": stem.replace("-review", ""), "issues": []}
    if unreviewed is not None:
        payload["unreviewed"] = unreviewed
    if claims is not _ABSENT:
        payload["deferred_reviewed"] = claims
    with open(os.path.join(output_dir, f"{stem}.json"), "w") as f:
        json.dump(payload, f)


def _make_context_with_findings(agent_findings):
    """Create a minimal reconciliation context dict with given agent findings."""
    return {
        "agent_findings": agent_findings,
        "source_snippets": {},
        "scope_annotations": {},
        "changed_files": ["src/app.py"],
        "git_range": "abc123..HEAD",
        "change_purpose": "Test change",
        "pr_id": "42",
        "output_dir": "/tmp/test-review",
        "output_builder_path": "/path/to/output.py",
    }


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
        "schema": 1,
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
        (tmp_path / "code-review.json").write_text(
            json.dumps(_make_review_json(reviewer="code"))
        )

        result = mod.load_agent_findings(str(tmp_path))
        assert "security-review" in result
        assert "code-review" in result
        assert result["security-review"]["reviewer"] == "security"
        assert result["code-review"]["reviewer"] == "code"

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

    def test_skips_non_object_json(self, mod, tmp_path):
        (tmp_path / "security-review.json").write_text(
            json.dumps(_make_review_json())
        )
        (tmp_path / "broken-review.json").write_text("[]")

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


def test_reconciler_entitlement_write_error_removes_stale_target(
    mod, tmp_path, monkeypatch
):
    sidecar = tmp_path / "reconciliator-advisory-entitlement.json"
    sidecar.write_text(json.dumps({
        "schema": 1, "advisory_entitled": True,
    }))

    def _raise(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(mod, "open", _raise, raising=False)

    mod.persist_reconciler_advisory_entitlement(str(tmp_path), {})

    assert not sidecar.exists()


class TestSeverityFloorNormalization:
    def test_structured_floor_wins_over_legacy_marker(self, mod):
        issue = _make_issue(
            severity_floor="medium",
            description="Severity-floor: silent false-success",
        )

        assert mod.resolve_severity_floor(issue) == "medium"

    @pytest.mark.parametrize(
        ("description", "expected"),
        [
            pytest.param(
                "Severity-floor: high — verified false-success",
                "high",
                id="numeric-marker",
            ),
            pytest.param(
                "Severity-floor: public-contract change; consumers exist",
                "medium",
                id="legacy-public-contract",
            ),
            pytest.param(
                "Severity-floor: silent false-success; blast radius is irrelevant",
                "high",
                id="legacy-false-success",
            ),
        ],
    )
    def test_resolves_numeric_and_current_legacy_markers(
        self, mod, description, expected
    ):
        assert mod.resolve_severity_floor(
            _make_issue(description=description)
        ) == expected

    def test_category_alone_does_not_create_floor(self, mod):
        assert mod.resolve_severity_floor(
            _make_issue(category="scheduled-action")
        ) is None

    def test_unknown_legacy_marker_does_not_guess_floor(self, mod):
        assert mod.resolve_severity_floor(
            _make_issue(description="Severity-floor: future policy")
        ) is None

    def test_legacy_marker_requires_a_marker_separator(self, mod):
        assert mod.resolve_severity_floor(
            _make_issue(
                description="Severity-floor: silent false-success was rejected"
            )
        ) is None

    def test_loading_findings_materializes_legacy_floor(self, mod, tmp_path):
        review = _make_review_json(
            issues=[
                _make_issue(
                    description=(
                        "Severity-floor: public-contract change; consumers exist"
                    ),
                )
            ]
        )
        (tmp_path / "woo-regression-review.json").write_text(json.dumps(review))

        loaded = mod.load_agent_findings(str(tmp_path))

        issue = loaded["woo-regression-review"]["issues"][0]
        assert issue["severity_floor"] == "medium"

    def test_resolves_floor_from_list_description(self, mod):
        # A malformed (list-valued) description must not silently drop a
        # mandatory floor marker: load_agent_findings pops severity_floor when
        # resolve_severity_floor returns None, so returning None here would
        # downgrade the finding.
        issue = _make_issue(
            description=["Finding body.", "Severity-floor: high — verified"]
        )
        assert mod.resolve_severity_floor(issue) == "high"


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

    def test_absolute_paths_outside_git_root_rejected(self, mod, tmp_path):
        """Absolute paths outside git_root are rejected (security containment)."""
        source_file = tmp_path / "abs.py"
        source_file.write_text("line 1\nline 2\n")

        refs = [{"file": str(source_file), "lines": [1]}]
        snippets = mod.read_source_snippets(
            refs, context_lines=1, git_root="/some/other/root"
        )
        assert str(source_file) not in snippets

    def test_absolute_paths_inside_git_root_allowed(self, mod, tmp_path):
        """Absolute paths within git_root are read normally."""
        source_file = tmp_path / "src" / "auth.py"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("line 1\nline 2\n")

        refs = [{"file": str(source_file), "lines": [1]}]
        snippets = mod.read_source_snippets(
            refs, context_lines=1, git_root=str(tmp_path)
        )
        assert str(source_file) in snippets

    def test_deleted_file_fallback_via_base_ref(self, mod, tmp_path):
        """Deleted files are recovered from git history via base_ref."""
        # Set up a git repo with a file, then delete it
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path, capture_output=True,
        )
        source_file = tmp_path / "guard.py"
        source_file.write_text("def validate():\n    check_auth()\n    return True\n")
        subprocess.run(["git", "add", "guard.py"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add guard"],
            cwd=tmp_path, capture_output=True,
        )
        # Get the commit hash for base_ref
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path, capture_output=True, text=True,
        )
        base_ref = result.stdout.strip()
        # Delete the file
        source_file.unlink()

        refs = [{"file": "guard.py", "lines": [2]}]
        snippets = mod.read_source_snippets(
            refs, context_lines=1, git_root=str(tmp_path), base_ref=base_ref,
        )
        assert "guard.py" in snippets
        assert "[deleted]" in snippets["guard.py"]
        assert "check_auth" in snippets["guard.py"]

    def test_deleted_file_no_base_ref_skipped(self, mod, tmp_path):
        """Without base_ref, deleted files are still skipped."""
        refs = [{"file": str(tmp_path / "gone.py"), "lines": [1]}]
        snippets = mod.read_source_snippets(refs, context_lines=1)
        assert snippets == {}

    def test_old_side_snippet_for_surviving_file(self, mod, tmp_path):
        """Surviving files with deletion hunks get a [pre-change] snippet."""
        # Set up a git repo with a file, then modify it
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path, capture_output=True,
        )
        source_file = tmp_path / "auth.py"
        source_file.write_text(
            "def validate():\n    check_auth()\n    check_perms()\n    return True\n"
        )
        subprocess.run(["git", "add", "auth.py"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=tmp_path, capture_output=True,
        )
        base_ref = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path, capture_output=True, text=True,
        ).stdout.strip()

        # Modify the file (delete a line — simulates surviving file with deletion)
        source_file.write_text(
            "def validate():\n    return True\n"
        )

        refs = [{"file": "auth.py", "lines": [2]}]
        snippets = mod.read_source_snippets(
            refs, context_lines=1, git_root=str(tmp_path),
            base_ref=base_ref, old_side_files={"auth.py"},
        )
        # Should have both current and pre-change snippets
        assert "auth.py" in snippets
        assert "return True" in snippets["auth.py"]
        assert "[pre-change] auth.py" in snippets
        assert "check_auth" in snippets["[pre-change] auth.py"]

    def test_old_side_snippet_not_produced_without_flag(self, mod, tmp_path):
        """Without old_side_files, no [pre-change] snippet is produced."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path, capture_output=True,
        )
        source_file = tmp_path / "auth.py"
        source_file.write_text("line 1\nline 2\n")
        subprocess.run(["git", "add", "auth.py"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=tmp_path, capture_output=True,
        )
        base_ref = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path, capture_output=True, text=True,
        ).stdout.strip()

        refs = [{"file": "auth.py", "lines": [1]}]
        snippets = mod.read_source_snippets(
            refs, context_lines=1, git_root=str(tmp_path),
            base_ref=base_ref,  # no old_side_files
        )
        assert "auth.py" in snippets
        assert "[pre-change] auth.py" not in snippets


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


# ===========================================================================
# TestFilterInScopeReferences
# ===========================================================================


class TestFilterInScopeReferences:
    """Tests for filter_in_scope_references() — security gate."""

    def test_keeps_in_scope_references(self, mod):
        refs = [{"file": "src/auth.py", "lines": [10, 20]}]
        annotations = {
            "src/auth.py:10": "IN_SCOPE:in_hunk",
            "src/auth.py:20": "IN_SCOPE:near_hunk",
        }
        result = mod.filter_in_scope_references(refs, annotations)
        assert len(result) == 1
        assert result[0]["file"] == "src/auth.py"
        assert result[0]["lines"] == [10, 20]

    def test_drops_out_of_scope_file(self, mod):
        """Files not in the diff are dropped entirely."""
        refs = [{"file": "/etc/hosts", "lines": [1]}]
        annotations = {"/etc/hosts:1": "OUT_OF_SCOPE:file_not_in_diff"}
        result = mod.filter_in_scope_references(refs, annotations)
        assert result == []

    def test_drops_path_traversal(self, mod):
        """Path traversal attempts are dropped when not in diff."""
        refs = [{"file": "../../.env", "lines": [5]}]
        annotations = {"../../.env:5": "OUT_OF_SCOPE:file_not_in_diff"}
        result = mod.filter_in_scope_references(refs, annotations)
        assert result == []

    def test_mixed_lines_keeps_only_in_scope(self, mod):
        """A file with both in-scope and out-of-scope lines keeps only in-scope."""
        refs = [{"file": "src/auth.py", "lines": [10, 200, 300]}]
        annotations = {
            "src/auth.py:10": "IN_SCOPE:in_hunk",
            "src/auth.py:200": "OUT_OF_SCOPE:not_in_hunk",
            "src/auth.py:300": "IN_SCOPE:near_hunk",
        }
        result = mod.filter_in_scope_references(refs, annotations)
        assert len(result) == 1
        assert result[0]["lines"] == [10, 300]

    def test_drops_file_when_all_lines_out_of_scope(self, mod):
        """A file where ALL lines are out-of-scope is dropped."""
        refs = [{"file": "src/auth.py", "lines": [200, 300]}]
        annotations = {
            "src/auth.py:200": "OUT_OF_SCOPE:not_in_hunk",
            "src/auth.py:300": "OUT_OF_SCOPE:not_in_hunk",
        }
        result = mod.filter_in_scope_references(refs, annotations)
        assert result == []

    def test_empty_inputs(self, mod):
        assert mod.filter_in_scope_references([], {}) == []

    def test_missing_annotation_treated_as_out_of_scope(self, mod):
        """References without annotations are dropped (fail-closed)."""
        refs = [{"file": "unknown.py", "lines": [1]}]
        result = mod.filter_in_scope_references(refs, {})
        assert result == []


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
            lambda git_range: ({"src/auth.py": [(10, 20)]}, set())
        )
        refs = [{"file": "src/auth.py", "lines": [15]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:15"] == "IN_SCOPE:in_hunk"

    def test_line_near_hunk(self, mod, monkeypatch):
        """Line within ±5 of a hunk gets IN_SCOPE:near_hunk."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: ({"src/auth.py": [(10, 20)]}, set())
        )
        refs = [{"file": "src/auth.py", "lines": [24]}]  # 4 lines after hunk end
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:24"] == "IN_SCOPE:near_hunk"

    def test_line_before_hunk_near(self, mod, monkeypatch):
        """Line within 5 lines before a hunk gets IN_SCOPE:near_hunk."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: ({"src/auth.py": [(10, 20)]}, set())
        )
        refs = [{"file": "src/auth.py", "lines": [6]}]  # 4 lines before hunk start
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:6"] == "IN_SCOPE:near_hunk"

    def test_line_far_from_hunk(self, mod, monkeypatch):
        """Line far from any hunk gets OUT_OF_SCOPE:not_in_hunk."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: ({"src/auth.py": [(10, 20)]}, set())
        )
        refs = [{"file": "src/auth.py", "lines": [100]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:100"] == "OUT_OF_SCOPE:not_in_hunk"

    def test_multiple_hunks(self, mod, monkeypatch):
        """Lines near different hunks in the same file."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: ({"src/auth.py": [(10, 15), (50, 55)]}, set())
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
            lambda git_range: ({"src/auth.py": [(10, 20)]}, set())
        )
        refs = [{"file": "src/auth.py", "lines": [10, 20]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:10"] == "IN_SCOPE:in_hunk"
        assert result["src/auth.py:20"] == "IN_SCOPE:in_hunk"

    def test_proximity_boundary_exact(self, mod, monkeypatch):
        """Line exactly at proximity boundary (±5) is near_hunk."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: ({"src/auth.py": [(10, 20)]}, set())
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
            lambda git_range: ({"src/auth.py": [(10, 20)]}, set())
        )
        # 6 lines after hunk end = line 26, 6 before start = line 4
        refs = [{"file": "src/auth.py", "lines": [4, 26]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:4"] == "OUT_OF_SCOPE:not_in_hunk"
        assert result["src/auth.py:26"] == "OUT_OF_SCOPE:not_in_hunk"

    def test_file_not_in_diff_with_hunks(self, mod, monkeypatch):
        """File not in changed_files stays OUT_OF_SCOPE regardless of hunks."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: ({"src/auth.py": [(10, 20)]}, set())
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
            lambda git_range: ({"src/auth.py": [(10, 10)]}, set())
        )
        refs = [{"file": "src/auth.py", "lines": [10, 14, 50]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:10"] == "IN_SCOPE:in_hunk"
        assert result["src/auth.py:14"] == "IN_SCOPE:near_hunk"
        assert result["src/auth.py:50"] == "OUT_OF_SCOPE:not_in_hunk"

    def test_large_deletion_old_side_lines_in_scope(self, mod, monkeypatch):
        """Old-side lines from large deletions are IN_SCOPE via separate ranges.

        @@ -10,20 +10,3 @@ → old=(10,29), new=(10,12) stored separately.
        Old-side lines 10-29 are in range via (10,29); new-side via (10,12).
        """
        # Simulate the separate ranges that _parse_diff_hunks now produces
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: ({"src/auth.py": [(10, 29), (10, 12)]}, {"src/auth.py"})
        )
        refs = [{"file": "src/auth.py", "lines": [10, 15, 25, 29, 50]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:10"] == "IN_SCOPE:in_hunk"
        assert result["src/auth.py:15"] == "IN_SCOPE:in_hunk"
        assert result["src/auth.py:25"] == "IN_SCOPE:in_hunk"
        assert result["src/auth.py:29"] == "IN_SCOPE:in_hunk"
        assert result["src/auth.py:50"] == "OUT_OF_SCOPE:not_in_hunk"

    def test_empty_hunk_list_metadata_only(self, mod, monkeypatch):
        """File with empty hunk list (rename/chmod) → OUT_OF_SCOPE:metadata_only."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: ({"src/auth.py": []}, set())
        )
        refs = [{"file": "src/auth.py", "lines": [5]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:5"] == "OUT_OF_SCOPE:metadata_only"

    def test_file_not_in_diff_hunks_falls_back(self, mod, monkeypatch):
        """File in changed_files but not in diff_hunks → fallback IN_SCOPE:in_hunk."""
        # This happens when git diff fails or the file has a suffix-matching miss.
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: ({"src/other.py": [(1, 5)]}, set())
        )
        refs = [{"file": "src/auth.py", "lines": [10]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/auth.py:10"] == "IN_SCOPE:in_hunk"

    def test_accepts_pre_parsed_diff_hunks(self, mod):
        """check_scope uses diff_hunks parameter instead of calling git."""
        hunks = {"src/auth.py": [(10, 20)]}
        refs = [{"file": "src/auth.py", "lines": [15, 100]}]
        result = mod.check_scope(
            refs, ["src/auth.py"], "abc..HEAD", diff_hunks=hunks,
        )
        assert result["src/auth.py:15"] == "IN_SCOPE:in_hunk"
        assert result["src/auth.py:100"] == "OUT_OF_SCOPE:not_in_hunk"


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
        hunks, deletions = mod._parse_diff_hunks("abc..HEAD")
        assert "src/auth.py" in hunks
        # Separate: old=(10,12), new=(10,14) → two entries
        assert hunks["src/auth.py"] == [(10, 12), (10, 14)]
        assert deletions == set()  # new_count > old_count → no deletion

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
        hunks, deletions = mod._parse_diff_hunks("abc..HEAD")
        # Hunk 1: old_count=0 → skip old, new=(5,6)
        # Hunk 2: old_count=0 → skip old, new=(22,24)
        assert hunks["src/auth.py"] == [(5, 6), (22, 24)]
        assert deletions == set()

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
        hunks, deletions = mod._parse_diff_hunks("abc..HEAD")
        assert hunks["src/a.py"] == [(1, 1)]
        assert hunks["src/b.py"] == [(10, 11)]
        assert deletions == set()

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
        hunks, deletions = mod._parse_diff_hunks("abc..HEAD")
        assert hunks["src/a.py"] == [(5, 5)]
        assert deletions == set()

    def test_pure_deletion_covers_old_side_range(self, mod, monkeypatch):
        """A pure deletion hunk covers the full old-side range.

        @@ -5,3 +5,0 @@ deletes old lines 5-7. Only old-side range stored
        (new_count=0), covering all deleted lines as IN_SCOPE.
        """
        diff_output = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n"
            "+++ b/src/a.py\n"
            "@@ -5,3 +5,0 @@\n"  # 3 old lines deleted, 0 new
        )
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": diff_output, "stderr": ""
            })()
        )
        hunks, deletions = mod._parse_diff_hunks("abc..HEAD")
        # old=(5,7), new_count=0 → only old range
        assert hunks["src/a.py"] == [(5, 7)]
        assert "src/a.py" in deletions

    def test_replacement_hunk_covers_both_sides(self, mod, monkeypatch):
        """Replacement hunk where old > new stores both ranges separately.

        @@ -10,20 +10,3 @@ replaces 20 old lines with 3 new lines.
        Separate entries: old=(10,29) and new=(10,12).
        """
        diff_output = (
            "diff --git a/src/auth.py b/src/auth.py\n"
            "--- a/src/auth.py\n"
            "+++ b/src/auth.py\n"
            "@@ -10,20 +10,3 @@\n"
        )
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": diff_output, "stderr": ""
            })()
        )
        hunks, deletions = mod._parse_diff_hunks("abc..HEAD")
        # Separate: old=(10,29), new=(10,12)
        assert hunks["src/auth.py"] == [(10, 29), (10, 12)]
        assert "src/auth.py" in deletions

    def test_git_failure_returns_empty(self, mod, monkeypatch):
        """Non-zero exit code returns empty tuple."""
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 1, "stdout": "", "stderr": "fatal: bad range"
            })()
        )
        hunks, deletions = mod._parse_diff_hunks("bad..range")
        assert hunks == {}
        assert deletions == set()


# ===========================================================================
# TestLineNearHunk
# ===========================================================================

class TestLineNearHunk:
    """Tests for _line_near_hunk() helper.

    check_scope() drives this one comparison (:1043-1049) with both proximity
    values, so TestCheckScopeHunkLevel already exercises the in-hunk, boundary,
    near, proximity-boundary and multi-hunk cases through the public API on the
    same literals. What stays here is only what the API cannot reach.
    """

    def test_line_just_outside(self, mod):
        """Line one beyond boundary with proximity=0."""
        assert mod._line_near_hunk(9, [(10, 20)], proximity=0) is False
        assert mod._line_near_hunk(21, [(10, 20)], proximity=0) is False

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
            "host_context_banner",
            "inline_coverage",
        }
        assert set(ctx.keys()) == expected_keys

        # Verify specific values
        assert "security-review" in ctx["agent_findings"]
        assert ctx["changed_files"] == ["src/auth.py", "src/db.py"]
        assert ctx["git_range"] == "abc123..HEAD"
        assert ctx["change_purpose"] == "Fix auth bug"
        assert ctx["pr_id"] == "42"
        assert ctx["output_builder_path"].endswith("output.py")

    def test_changed_files_reach_the_unscoped_computation(self, tmp_path):
        """The seam R3 depends on: `build_context` must hand the CLI's
        changed-file list to the coverage aggregator, or `files_unscoped`
        is unmeasurable in production."""
        _write_summary(str(tmp_path), "security-reviewer", ["src/auth.py"], [])

        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc123..HEAD",
            "--changed-files", "src/auth.py,package-lock.json",
            cwd=tmp_path,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        ctx = json.loads(
            (tmp_path / "reconciliation-context.json").read_text()
        )
        assert ctx["inline_coverage"]["files_unscoped"] == [
            "package-lock.json"
        ]
        md = (tmp_path / "reconciliation-context.md").read_text()
        assert "## Changed Files In No Reviewer's Scope" in md

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

    def test_upstream_advisory_entitles_reconciler_serialization(
        self, tmp_path, monkeypatch
    ):
        issue = _make_issue(severity="critical")
        issue["channel"] = "advisory"
        (tmp_path / "repo-reuse-review.json").write_text(json.dumps(
            _make_review_json(reviewer="repo-reuse", issues=[issue])
        ))

        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc..HEAD",
            cwd=tmp_path,
        )

        assert result.returncode == 0, result.stderr
        entitlement = json.loads(
            (tmp_path / "reconciliator-advisory-entitlement.json").read_text()
        )
        assert entitlement == {"schema": 1, "advisory_entitled": True}

        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        builder = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        builder.add_issue(
            severity="critical", title="Advisory", file="src/app.py",
            description="d", recommendation="r", line=1,
            channel="advisory",
        )
        output = builder.to_dict(output_dir=str(tmp_path))
        assert output["verdict"] == "approve"

    def test_no_upstream_advisory_rejects_reconciler_advisory_serialization(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "security-review.json").write_text(json.dumps(
            _make_review_json(reviewer="security")
        ))

        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc..HEAD",
            cwd=tmp_path,
        )

        assert result.returncode == 0, result.stderr
        entitlement = json.loads(
            (tmp_path / "reconciliator-advisory-entitlement.json").read_text()
        )
        assert entitlement == {"schema": 1, "advisory_entitled": False}

        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        builder = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        builder.add_issue(
            severity="high", title="Advisory", file="src/app.py",
            description="d", recommendation="r", line=1,
            channel="advisory",
        )
        with pytest.raises(ValueError, match="advisory.*not entitled"):
            builder.to_dict(output_dir=str(tmp_path))

    def test_reconciler_snippet_finalizes_with_explicit_output_dir(self):
        definition = (
            PLUGIN_ROOT / "agents" / "review-reconciliator.md"
        ).read_text()

        assert "output = builder.to_dict(output_dir=output_dir)" in definition

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

    def test_produces_markdown_file(self, tmp_path):
        """Full run produces reconciliation-context.md alongside JSON."""
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

        # Verify Markdown file exists
        md_path = tmp_path / "reconciliation-context.md"
        assert md_path.is_file()

        content = md_path.read_text()

        # Starts with the expected heading
        assert content.startswith("# Reconciliation Context")

        # Contains the agent name from the review JSON
        assert "security-review" in content

        # Verify stdout includes the markdown_path
        stdout_json = json.loads(result.stdout.strip())
        assert "markdown_path" in stdout_json
        assert stdout_json["markdown_path"].endswith("reconciliation-context.md")

    def test_main_leaves_reviewer_markdown_to_step_orchestration(self, tmp_path):
        """Reconciliation context building has no human-artifact side effect."""
        review = _make_review_json(
            reviewer="security",
            issues=[_make_issue(file="src/auth.py", line=10)],
        )
        (tmp_path / "security-review.json").write_text(json.dumps(review))

        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc123..HEAD",
            "--changed-files", "src/auth.py",
            "--pr-id", "42",
            cwd=tmp_path,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"

        md_path = tmp_path / "security-review.md"
        assert not md_path.exists()

        stdout_json = json.loads(result.stdout.strip())
        assert stdout_json["status"] == "ok"
        assert "reviewer_markdown" not in stdout_json


# ===========================================================================
# TestToMarkdown
# ===========================================================================

class TestToMarkdown:
    """Tests for to_markdown() — Markdown serialization of reconciliation context."""

    def test_metadata_section(self, mod):
        """All metadata fields appear in the Metadata section."""
        ctx = _make_context_with_findings({})
        md = mod.to_markdown(ctx)
        assert "## Metadata" in md
        assert "`abc123..HEAD`" in md
        assert "**PR ID:** 42" in md
        assert "`/tmp/test-review`" in md
        assert "`/path/to/output.py`" in md
        assert "`src/app.py`" in md

    def test_agent_findings_with_issues(self, mod):
        """Issues render with severity, file:line, description, recommendation."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        severity="high",
                        title="XSS vulnerability",
                        file="src/auth.py",
                        line=42,
                        description="Unescaped user input",
                        recommendation="Use esc_html()",
                        category="xss",
                        confidence=0.95,
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        assert "### security-review" in md
        assert "1 issues" in md or "1 issue" in md
        assert "verdict: comment" in md
        assert "**1. XSS vulnerability**" in md
        assert "high" in md
        assert "confidence: 0.95" in md
        assert "`src/auth.py:42`" in md
        assert "Unescaped user input" in md
        assert "Use esc_html()" in md

    def test_agent_finding_includes_severity_floor(self, mod):
        findings = {
            "woo-regression-review": _make_review_json(
                reviewer="woo-regression",
                issues=[_make_issue(severity_floor="medium")],
            ),
        }

        md = mod.to_markdown(_make_context_with_findings(findings))

        assert "- Severity floor: medium" in md

    def test_agent_no_issues(self, mod):
        """Agent with 0 issues shows verdict and count."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="approve",
                issues=[],
            ),
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        assert "### security-review" in md
        assert "0 issues" in md
        assert "verdict: approve" in md

    def test_not_applicable_agent(self, mod):
        """Agent with skip_reason shows the reason."""
        findings = {
            "security-review": {
                "verdict": "not_applicable",
                "skip_reason": "No security-relevant files in diff",
                "issues": [],
                "summary": {"total_issues": 0, "by_severity": {}},
            },
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        assert "### security-review" in md
        assert "No security-relevant files in diff" in md

    def test_source_snippets_in_code_blocks(self, mod):
        """Source snippets appear in fenced code blocks."""
        ctx = _make_context_with_findings({})
        ctx["source_snippets"] = {
            "src/auth.py": "    40 | def login():\n    41 |     pass",
        }
        md = mod.to_markdown(ctx)
        assert "## Source Snippets" in md
        assert "### `src/auth.py`" in md
        assert "```" in md
        assert "40 | def login():" in md

    def test_scope_annotations_table(self, mod):
        """Scope annotations appear as a table, excluding pre-filtered statuses."""
        ctx = _make_context_with_findings({})
        ctx["scope_annotations"] = {
            "src/auth.py:42": "IN_SCOPE:in_hunk",
            "src/utils.py:10": "OUT_OF_SCOPE:file_not_in_diff",
            "src/app.py:100": "OUT_OF_SCOPE:not_in_hunk",
        }
        md = mod.to_markdown(ctx)
        assert "## Scope Annotations" in md
        assert "| File:Line | Status |" in md
        assert "`src/auth.py:42`" in md
        assert "IN_SCOPE:in_hunk" in md
        assert "`src/app.py:100`" in md
        # file_not_in_diff is pre-filtered from the table
        assert "`src/utils.py:10`" not in md

    def test_dispatched_agents_listed(self, mod):
        """Dispatched agents appear in the metadata section."""
        ctx = _make_context_with_findings({})
        ctx["dispatched_agents"] = ["security-review", "performance-review"]
        md = mod.to_markdown(ctx)
        assert "**Dispatched agents (2):**" in md
        assert "security-review" in md
        assert "performance-review" in md

    def test_empty_change_purpose(self, mod):
        """Empty change_purpose does not crash and produces valid Markdown."""
        ctx = _make_context_with_findings({})
        ctx["change_purpose"] = ""
        md = mod.to_markdown(ctx)
        # Should still have the section header
        assert "## Change Purpose" in md
        # Should be valid (no crash = test passes)
        assert "# Reconciliation Context" in md
        # The claims-to-verify preamble only accompanies actual content
        assert "claims to verify" not in md.split("## Change Purpose")[1].split("---")[0]

    def test_change_purpose_framed_as_author_claims(self, mod):
        """Non-empty change purpose carries the claims-to-verify preamble,
        outside the isolation fence (regression guard for #66488 anchoring)."""
        ctx = _make_context_with_findings({})
        ctx["change_purpose"] = "Fix retry logic."
        md = mod.to_markdown(ctx)
        section = md.split("## Change Purpose")[1]
        preamble_pos = section.find("claims to verify")
        fence_pos = section.find("```")
        assert preamble_pos != -1
        assert fence_pos != -1
        assert preamble_pos < fence_pos

    def test_special_chars_in_description(self, mod):
        """Pipe chars and backticks in descriptions don't break output."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        title="Issue with `backticks` and | pipes",
                        description="The value `foo | bar` is dangerous",
                        recommendation="Escape the `|` character",
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        # Should contain the special characters without crashing
        assert "`backticks`" in md
        assert "foo | bar" in md

    def test_pre_change_snippets(self, mod):
        """Pre-change snippets are labeled distinctly."""
        ctx = _make_context_with_findings({})
        ctx["source_snippets"] = {
            "src/auth.py": "    10 | current code",
            "[pre-change] src/auth.py": "    10 | old code",
        }
        md = mod.to_markdown(ctx)
        assert "### `[pre-change] src/auth.py`" in md
        assert "old code" in md

    def test_deleted_file_snippets(self, mod):
        """Deleted file snippets are labeled with [deleted]."""
        ctx = _make_context_with_findings({})
        ctx["source_snippets"] = {
            "src/removed.py": "[deleted]     5 | def old_func():",
        }
        md = mod.to_markdown(ctx)
        assert "[deleted]" in md
        assert "old_func" in md

    def test_positive_observations_excluded(self, mod):
        """Positive observations are excluded — they bypass the scope/snippet pipeline."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="approve",
                issues=[],
            ),
        }
        findings["security-review"]["positive_observations"] = [
            "Good input validation on all endpoints",
        ]
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        assert "Good input validation on all endpoints" not in md
        assert "Positives" not in md

    def test_clearances_included_with_method(self, mod):
        """Structured clearances DO reach the reconciliator — unlike free-text
        positives — so clearance-vs-finding conflicts are visible and the
        stated method can be judged (the 2026-07-16 3-clear-vs-1-found signal
        was invisible because clears lived in excluded positives)."""
        findings = {
            "a11y-review": _make_review_json(
                reviewer="a11y",
                verdict="approve",
                issues=[],
            ),
        }
        findings["a11y-review"]["clearances"] = [
            {
                "claim": "No CSS or JS selects the removed label",
                "method": "grep '.titledesc label' plugins/ — no hits",
                "evidence": None,
            },
        ]
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        assert "Clearances" in md
        assert "No CSS or JS selects the removed label" in md
        assert "grep '.titledesc label' plugins/ — no hits" in md

    def test_clearance_evidence_rendered_when_present(self, mod):
        findings = {
            "code-review": _make_review_json(reviewer="code", verdict="approve", issues=[]),
        }
        findings["code-review"]["clearances"] = [
            {
                "claim": "No E2E test targets the radio row",
                "method": "grep 'radio' e2e/",
                "evidence": "0 hits across 214 spec files",
            },
        ]
        md = mod.to_markdown(_make_context_with_findings(findings))
        assert "0 hits across 214 spec files" in md

    def test_multiple_agents_ordered(self, mod):
        """Agents are rendered in alphabetical order."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security", verdict="approve", issues=[]
            ),
            "architecture-review": _make_review_json(
                reviewer="architecture", verdict="approve", issues=[]
            ),
            "performance-review": _make_review_json(
                reviewer="performance", verdict="comment", issues=[]
            ),
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        # Check that architecture comes before performance, which comes before security
        arch_pos = md.index("### architecture-review")
        perf_pos = md.index("### performance-review")
        sec_pos = md.index("### security-review")
        assert arch_pos < perf_pos < sec_pos

    def test_all_changed_files_listed_when_over_20(self, mod):
        """All changed files appear in Markdown — no truncation at 20."""
        ctx = _make_context_with_findings({})
        ctx["changed_files"] = [f"src/file{i}.py" for i in range(30)]
        md = mod.to_markdown(ctx)
        assert "**Changed files (30):**" in md
        # Every file must be present — the reconciliator uses this list
        # for in-scope decisions, so truncation causes misclassification.
        for i in range(30):
            assert f"`src/file{i}.py`" in md

    def test_source_snippet_with_backticks_fenced_safely(self, mod):
        """Snippets containing triple backticks get a longer fence."""
        ctx = _make_context_with_findings({})
        snippet = '10 | ```python\n11 | print("hi")\n12 | ```'
        ctx["source_snippets"] = {"README.md": snippet}
        md = mod.to_markdown(ctx)
        # The snippet must appear intact
        assert snippet in md
        # The outer fence must be longer than the inner ``` to avoid
        # closing early and corrupting the rest of the document.
        lines = md.split("\n")
        snippet_section = False
        for line in lines:
            if line.startswith("### `README.md`"):
                snippet_section = True
                continue
            if snippet_section and line.startswith("`") and line.strip().replace("`", "") == "":
                # This is a fence line — must be at least 4 backticks
                assert len(line.strip()) >= 4, (
                    f"Outer fence too short: {line!r} — will collide with ``` in snippet"
                )
                break

    def test_observations_excluded_from_markdown(self, mod):
        """Observations are excluded — they bypass the scope/snippet pipeline."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security", verdict="comment", issues=[]
            ),
        }
        findings["security-review"]["observations"] = [
            {"file": "src/auth.py", "note": "Session tokens stored in localStorage"},
        ]
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        assert "**Observations:**" not in md
        assert "Session tokens stored in localStorage" not in md

    def test_recommendations_preserved(self, mod):
        """Prioritized recommendations appear in Markdown output."""
        findings = {
            "architecture-review": _make_review_json(
                reviewer="architecture", verdict="comment", issues=[]
            ),
        }
        findings["architecture-review"]["recommendations"] = {
            "immediate": ["Fix the circular dependency"],
            "important": ["Extract shared interface"],
            "suggestions": [],
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        assert "**Recommendations:**" in md
        assert "[immediate] Fix the circular dependency" in md
        assert "[important] Extract shared interface" in md

    def test_multiline_recommendation_stays_inside_item(self, mod):
        """Newlines in recommendation text are indented as list continuations."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security", verdict="comment", issues=[]
            ),
        }
        findings["security-review"]["recommendations"] = {
            "immediate": ["Step 1: do X\nStep 2: do Y\nStep 3: verify"],
            "important": [],
            "suggestions": [],
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        assert "- [immediate] Step 1: do X\n  Step 2: do Y\n  Step 3: verify" in md

    def test_backticks_in_issue_description_escaped(self, mod):
        """Triple backticks in issue text are neutralized to prevent fence corruption."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        severity="high",
                        title="Use ```esc_html()``` here",
                        description="The code does:\n```php\necho $input;\n```",
                        recommendation="Wrap in ```esc_html()```",
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        # The Markdown must not contain raw ``` that would open a code fence.
        # After the "## Agent Findings" section, count fence-like lines —
        # every opener must have a closer before "## Source Snippets".
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        # No raw triple-backtick runs should survive (they get a ZWS inserted)
        import re
        raw_fences = re.findall(r"(?<!\u200b)`{3,}(?!\u200b)", agent_section)
        # Allow zero raw fences (all escaped) — but if any exist, they
        # must be balanced (even count) to avoid corrupting the doc.
        assert len(raw_fences) % 2 == 0, (
            f"Unbalanced code fences in agent findings section: {raw_fences}"
        )

    def test_agent_header_is_plain_name(self, mod):
        """Agent subsection header contains only the agent name.

        The reconciliator compares dispatched agent names against ### headers
        to detect missing outputs. Baking metadata into the header breaks
        that match.
        """
        findings = {
            "security-review": _make_review_json(
                reviewer="security", verdict="comment"
            ),
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        # Header must be exactly the agent name — no appended metadata
        assert "### security-review\n" in md
        assert "### security-review --" not in md
        # Verdict and count still appear, just on a separate line
        assert "1 issues, verdict: comment" in md

    def test_multiline_description_stays_inside_issue(self, mod):
        """Newlines in description/recommendation are indented as list continuations."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        description="Line one\nLine two\nLine three",
                        recommendation="Step 1\nStep 2",
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        # Continuation lines must be indented with 2 spaces
        assert "- Description: Line one\n  Line two\n  Line three" in md
        assert "- Recommendation: Step 1\n  Step 2" in md

    def test_block_syntax_in_description_escaped(self, mod):
        """ATX headings and thematic breaks in descriptions are escaped."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        description="Problem here\n## Details\nMore info\n---\nEnd",
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        # The ## must be escaped so it doesn't become a real heading
        assert "\\## Details" in agent_section
        # The --- must be escaped so it doesn't become a thematic break
        assert "\\---" in agent_section

    def test_block_syntax_in_recommendation_escaped(self, mod):
        """ATX headings and thematic breaks in recommendations are escaped."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        recommendation="Step 1\n## Step 2\n---\nStep 3",
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "\\## Step 2" in agent_section
        assert "\\---" in agent_section

    def test_block_syntax_in_prioritized_recommendations_escaped(self, mod):
        """Block syntax in prioritized recommendation items is escaped."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security", verdict="comment", issues=[]
            ),
        }
        findings["security-review"]["recommendations"] = {
            "immediate": ["Do this:\n## Important\n---\nDone"],
            "important": [],
            "suggestions": [],
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "\\## Important" in agent_section
        assert "\\---" in agent_section

    def test_block_quotes_in_description_escaped(self, mod):
        """Block quotes in descriptions are escaped."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(description="See:\n> quoted text\nEnd"),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "\\> quoted text" in agent_section

    def test_setext_heading_underline_in_description_escaped(self, mod):
        """Setext heading underlines (===) in descriptions are escaped."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(description="Title\n===\nBody"),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "\\===" in agent_section

    def test_change_purpose_with_fences_isolated(self, mod):
        """Change-purpose containing Markdown fences is wrapped safely."""
        ctx = _make_context_with_findings({})
        ctx["change_purpose"] = "Added helper:\n```python\ndef foo(): pass\n```"
        md = mod.to_markdown(ctx)
        # The change-purpose text must survive intact
        assert "def foo(): pass" in md
        # The outer fence must be longer than the inner ```
        section = md.split("## Change Purpose")[1].split("## Agent Findings")[0]
        lines = section.strip().split("\n")
        # First fence-like line is the outer opener
        outer_fence = next(
            l for l in lines if l.strip() and set(l.strip()) == {"`"}
        )
        assert len(outer_fence.strip()) >= 4, (
            f"Outer fence too short: {outer_fence!r} — collides with ``` inside"
        )
        # Content including inner ``` must appear between outer fences
        assert "```python" in section

    def test_change_purpose_headings_cannot_spoof_sections(self, mod):
        """Headings inside change-purpose don't create real document sections."""
        ctx = _make_context_with_findings({})
        ctx["change_purpose"] = "## Fake Agent Findings\n\nSpoofed content"
        md = mod.to_markdown(ctx)
        # The fake heading must be inside a code fence, not a real section
        # Count real ## Agent Findings sections — should be exactly 1
        real_sections = [
            l for l in md.split("\n")
            if l.strip() == "## Agent Findings"
        ]
        assert len(real_sections) == 1

    # NOTE: test_positive_observations_backticks_escaped and
    # test_positive_observations_newlines_flattened were removed —
    # positives are now excluded from the Markdown context entirely
    # (same reasoning as observations: bypass scope/snippet pipeline).


# ===========================================================================
# TestPrefilterOutOfScope
# ===========================================================================

class TestPrefilterOutOfScope:
    """Tests for pre-filtering structurally certain out-of-scope findings."""

    def test_file_not_in_diff_excluded(self, mod):
        """Issue with OUT_OF_SCOPE:file_not_in_diff annotation is excluded from Markdown."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        title="Out of scope issue",
                        file="src/other.py",
                        line=10,
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        ctx["scope_annotations"] = {
            "src/other.py:10": "OUT_OF_SCOPE:file_not_in_diff",
        }
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "Out of scope issue" not in agent_section

    def test_metadata_only_excluded(self, mod):
        """Issue with OUT_OF_SCOPE:metadata_only annotation is excluded."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        title="Metadata only issue",
                        file="src/app.py",
                        line=5,
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        ctx["scope_annotations"] = {
            "src/app.py:5": "OUT_OF_SCOPE:metadata_only",
        }
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "Metadata only issue" not in agent_section

    def test_not_in_hunk_kept(self, mod):
        """Issue with OUT_OF_SCOPE:not_in_hunk annotation is kept (ambiguous)."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        title="Near but not in hunk",
                        file="src/app.py",
                        line=100,
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        ctx["scope_annotations"] = {
            "src/app.py:100": "OUT_OF_SCOPE:not_in_hunk",
        }
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "Near but not in hunk" in agent_section

    def test_no_scope_annotation_kept(self, mod):
        """Issue with no matching scope annotation is kept (conservative)."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        title="Unannotated issue",
                        file="src/app.py",
                        line=42,
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        # scope_annotations is empty — no annotation for this issue
        ctx["scope_annotations"] = {}
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "Unannotated issue" in agent_section

    def test_issue_without_file_kept(self, mod):
        """Issue with empty file/zero line is kept (can't determine scope)."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        title="No file issue",
                        file="",
                        line=0,
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        ctx["scope_annotations"] = {
            "src/app.py:42": "OUT_OF_SCOPE:file_not_in_diff",
        }
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "No file issue" in agent_section

    def test_filtered_count_in_header(self, mod):
        """Agent header shows 'N issues (M pre-filtered as out-of-scope)' when issues are filtered."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        title="Kept issue",
                        file="src/app.py",
                        line=42,
                    ),
                    _make_issue(
                        title="Filtered issue",
                        file="src/other.py",
                        line=10,
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        ctx["scope_annotations"] = {
            "src/app.py:42": "IN_SCOPE:in_hunk",
            "src/other.py:10": "OUT_OF_SCOPE:file_not_in_diff",
        }
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "1 issues (1 pre-filtered as out-of-scope)" in agent_section

    def test_no_filtered_no_annotation_in_header(self, mod):
        """Agent header shows normal 'N issues, verdict: X' when nothing filtered."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        title="Normal issue",
                        file="src/app.py",
                        line=42,
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        ctx["scope_annotations"] = {
            "src/app.py:42": "IN_SCOPE:in_hunk",
        }
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "1 issues, verdict: comment" in agent_section
        assert "pre-filtered" not in agent_section

    def test_all_issues_filtered_shows_header(self, mod):
        """Agent with all issues filtered shows header and '0 issues (N pre-filtered)'."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        title="Filtered 1",
                        file="src/other.py",
                        line=10,
                    ),
                    _make_issue(
                        title="Filtered 2",
                        file="src/gone.py",
                        line=20,
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        ctx["scope_annotations"] = {
            "src/other.py:10": "OUT_OF_SCOPE:file_not_in_diff",
            "src/gone.py:20": "OUT_OF_SCOPE:metadata_only",
        }
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "### security-review" in agent_section
        assert "0 issues (2 pre-filtered as out-of-scope)" in agent_section

    def test_issue_numbering_contiguous_after_filter(self, mod):
        """Kept issues are numbered 1, 2, ... with no gaps."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        title="First kept",
                        file="src/app.py",
                        line=10,
                    ),
                    _make_issue(
                        title="Filtered out",
                        file="src/other.py",
                        line=20,
                    ),
                    _make_issue(
                        title="Second kept",
                        file="src/app.py",
                        line=30,
                    ),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        ctx["scope_annotations"] = {
            "src/app.py:10": "IN_SCOPE:in_hunk",
            "src/other.py:20": "OUT_OF_SCOPE:file_not_in_diff",
            "src/app.py:30": "IN_SCOPE:in_hunk",
        }
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        assert "**1. First kept**" in agent_section
        assert "**2. Second kept**" in agent_section
        assert "Filtered out" not in agent_section

    def test_prefilter_does_not_affect_recommendations(self, mod):
        """Agent-level recommendations are NOT filtered (they have no file:line)."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(
                        title="Filtered issue",
                        file="src/other.py",
                        line=10,
                    ),
                ],
            ),
        }
        findings["security-review"]["recommendations"] = {
            "immediate": ["Fix the auth bypass immediately"],
            "important": ["Add rate limiting"],
            "suggestions": [],
        }
        ctx = _make_context_with_findings(findings)
        ctx["scope_annotations"] = {
            "src/other.py:10": "OUT_OF_SCOPE:file_not_in_diff",
        }
        md = mod.to_markdown(ctx)
        agent_section = md.split("## Agent Findings")[1].split("## Source Snippets")[0]
        # Issue is filtered but recommendations survive
        assert "Filtered issue" not in agent_section
        assert "Fix the auth bypass immediately" in agent_section
        assert "Add rate limiting" in agent_section

    def test_scope_annotations_table_excludes_prefiltered(self, mod):
        """Scope annotations table omits file_not_in_diff and metadata_only entries."""
        findings = {
            "security-review": _make_review_json(
                reviewer="security",
                verdict="comment",
                issues=[
                    _make_issue(file="src/app.py", line=42),
                    _make_issue(file="src/other.py", line=10),
                    _make_issue(file="src/renamed.py", line=5),
                    _make_issue(file="src/app.py", line=100),
                ],
            ),
        }
        ctx = _make_context_with_findings(findings)
        ctx["scope_annotations"] = {
            "src/app.py:42": "IN_SCOPE:in_hunk",
            "src/other.py:10": "OUT_OF_SCOPE:file_not_in_diff",
            "src/renamed.py:5": "OUT_OF_SCOPE:metadata_only",
            "src/app.py:100": "OUT_OF_SCOPE:not_in_hunk",
        }
        md = mod.to_markdown(ctx)
        scope_section = md.split("## Scope Annotations")[1]
        # IN_SCOPE and not_in_hunk entries present
        assert "src/app.py:42" in scope_section
        assert "src/app.py:100" in scope_section
        # Pre-filtered entries absent
        assert "src/other.py:10" not in scope_section
        assert "src/renamed.py:5" not in scope_section


class TestMissingAgentDetection:
    """Tests for pre-computed missing agent detection in metadata."""

    def test_missing_agents_shown(self, mod):
        """Missing agents (dispatched but no output) are listed in metadata."""
        findings = {
            "security-review": _make_review_json(reviewer="security", verdict="comment"),
        }
        ctx = _make_context_with_findings(findings)
        ctx["dispatched_agents"] = ["security-review", "performance-review", "a11y-review"]
        md = mod.to_markdown(ctx)
        meta = md.split("## Change Purpose")[0]
        assert "**Missing agents (2):**" in meta
        assert "performance-review" in meta
        assert "a11y-review" in meta

    def test_no_missing_agents(self, mod):
        """No missing agents line when all dispatched agents reported."""
        findings = {
            "security-review": _make_review_json(reviewer="security", verdict="comment"),
        }
        ctx = _make_context_with_findings(findings)
        ctx["dispatched_agents"] = ["security-review"]
        md = mod.to_markdown(ctx)
        assert "Missing agents" not in md

    def test_no_dispatched_agents_list(self, mod):
        """No missing agents line when dispatched_agents is absent (backward compat)."""
        findings = {
            "security-review": _make_review_json(reviewer="security", verdict="comment"),
        }
        ctx = _make_context_with_findings(findings)
        # No dispatched_agents key at all
        md = mod.to_markdown(ctx)
        assert "Missing agents" not in md

    def test_empty_dispatched_list(self, mod):
        """No missing agents when dispatched list is empty."""
        ctx = _make_context_with_findings({})
        ctx["dispatched_agents"] = []
        md = mod.to_markdown(ctx)
        assert "Missing agents" not in md


# ===========================================================================
# Host context banner propagation
# ===========================================================================

def test_extract_host_banner_reads_from_review_context(mod, tmp_path):
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "review-context.json").write_text(json.dumps({
        "version": 1,
        "host_context": {
            "version": 1,
            "resolved": [],
            "unresolved": [{"name": "wordpress", "reason": "not_found"}],
            "banner": {
                "degraded": True,
                "reason": "fully_unavailable",
                "message": "Host context unavailable.",
                "unresolved": [{"name": "wordpress", "reason": "not_found"}],
            },
            "diagnostics": {},
        },
    }))
    banner = mod.extract_host_banner(str(outdir))
    assert banner is not None
    assert banner["degraded"] is True
    assert banner["reason"] == "fully_unavailable"


def test_extract_host_banner_returns_none_for_empty_output_dir(
    mod, tmp_path, monkeypatch
):
    """An unresolved output dir arrives as "" — and os.path.join("", name)
    is just `name`, so without the early return at :151 the banner would be
    read from whatever review-context.json happens to sit in the CWD. The
    fixture below is that foreign file."""
    (tmp_path / "review-context.json").write_text(json.dumps({
        "host_context": {"banner": {"degraded": True, "reason": "foreign"}},
    }))
    monkeypatch.chdir(tmp_path)

    assert mod.extract_host_banner("") is None


def test_extract_host_banner_returns_none_when_host_context_is_not_a_dict(
    mod, tmp_path
):
    """A truthy non-dict host_context reaches :162 — `or {}` only rescues
    the falsy shapes, so without this guard .get() raises on a list."""
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "review-context.json").write_text(json.dumps({
        "version": 1,
        "host_context": ["not", "a", "dict"],
    }))
    assert mod.extract_host_banner(str(outdir)) is None


def test_extract_host_banner_returns_none_when_no_host_context(mod, tmp_path):
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "review-context.json").write_text(json.dumps({"version": 1}))
    assert mod.extract_host_banner(str(outdir)) is None


def test_extract_host_banner_returns_none_when_file_missing(mod, tmp_path):
    outdir = tmp_path / "out"
    outdir.mkdir()
    assert mod.extract_host_banner(str(outdir)) is None


def test_extract_host_banner_tolerates_malformed_json(mod, tmp_path):
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "review-context.json").write_text("{not json")
    assert mod.extract_host_banner(str(outdir)) is None


def test_to_markdown_prepends_banner_when_degraded(mod):
    context = {
        "agent_findings": {},
        "source_snippets": {},
        "scope_annotations": {},
        "changed_files": ["x.php"],
        "git_range": "a..b",
        "change_purpose": "",
        "pr_id": "",
        "output_dir": "/tmp",
        "output_builder_path": "/tmp/output.py",
        "host_context_banner": {
            "degraded": True,
            "reason": "fully_unavailable",
            "message": "Host context unavailable.",
            "unresolved": [{"name": "wordpress", "reason": "not_found"}],
        },
    }
    md = mod.to_markdown(context)
    # Banner appears at top as a blockquote
    first_nonempty = next(line for line in md.splitlines() if line.strip())
    assert "Host Context Banner" in first_nonempty
    assert "host_context_banner" in md
    assert '"reason": "fully_unavailable"' in md
    assert '"unresolved": [' in md
    assert '"name": "wordpress"' in md


def test_to_markdown_banner_uses_dynamic_fence_for_backticks(mod):
    context = {
        "agent_findings": {},
        "source_snippets": {},
        "scope_annotations": {},
        "changed_files": ["x.php"],
        "git_range": "a..b",
        "change_purpose": "",
        "pr_id": "",
        "output_dir": "/tmp",
        "output_builder_path": "/tmp/output.py",
        "host_context_banner": {
            "degraded": True,
            "reason": "partial",
            "message": "Host ```context``` degraded.",
            "unresolved": [{"name": "bad```host", "reason": "not_found"}],
        },
    }

    md = mod.to_markdown(context)
    banner_section = md.split("# Reconciliation Context", 1)[0]
    lines = banner_section.splitlines()

    assert "````json" in lines
    assert "````" in lines
    assert '"name": "bad```host"' in banner_section


def test_to_markdown_no_banner_when_absent(mod):
    context = {
        "agent_findings": {},
        "source_snippets": {},
        "scope_annotations": {},
        "changed_files": ["x.php"],
        "git_range": "a..b",
        "change_purpose": "",
        "pr_id": "",
        "output_dir": "/tmp",
        "output_builder_path": "/tmp/output.py",
        "host_context_banner": None,
    }
    md = mod.to_markdown(context)
    assert "Host Context Banner" not in md


# ===========================================================================
# TestNonStringFieldCoercion
# ===========================================================================

class TestNonStringFieldCoercion:
    """Non-string finding fields must not crash Markdown rendering.

    Regression: a reviewer agent emitted a list-valued ``recommendation``.
    ``_escape_backtick_runs`` passed it straight to ``re.sub``, which raised
    ``TypeError: expected string or bytes-like object, got 'list'`` and aborted
    pipeline step 8 (the whole review) because reconciliation-context.md could
    not be written.
    """

    def test_escape_backtick_runs_coerces_list(self, mod):
        out = mod._escape_backtick_runs(["one", "two"])
        assert "one" in out and "two" in out

    def test_escape_backtick_runs_coerces_none_and_int(self, mod):
        assert mod._escape_backtick_runs(None) == ""
        assert mod._escape_backtick_runs(7) == "7"

    def test_escape_backtick_runs_preserves_str(self, mod):
        assert mod._escape_backtick_runs("plain text") == "plain text"

    def test_escape_backtick_runs_still_neutralizes_fences_after_coercion(self, mod):
        # A list item carrying a fence must still be neutralized once coerced.
        out = mod._escape_backtick_runs(["```py code```"])
        assert "```" not in out

    def test_to_markdown_handles_list_recommendation(self, mod):
        findings = {
            "dead-code-review": _make_review_json(
                reviewer="dead-code",
                issues=[_make_issue(recommendation=["Wire it in", "or drop it"])],
            ),
        }
        md = mod.to_markdown(_make_context_with_findings(findings))
        assert "Wire it in" in md

    def test_to_markdown_handles_list_description_and_title(self, mod):
        findings = {
            "dead-code-review": _make_review_json(
                reviewer="dead-code",
                issues=[_make_issue(title=["Ambiguous name"], description=["D1", "D2"])],
            ),
        }
        md = mod.to_markdown(_make_context_with_findings(findings))
        assert "Ambiguous name" in md
        assert "D1" in md

    def test_build_critic_context_handles_list_recommendation(self, mod):
        findings = {
            "verdict": "comment",
            "summary": {"total_issues": 1, "by_severity": {"medium": 1}},
            "issues": [_make_issue(recommendation=["Do the thing"])],
        }
        out = mod.build_critic_context("REPORT BODY", findings)
        assert "Do the thing" in out

    def test_to_markdown_multiline_title_does_not_forge_heading(self, mod):
        # A title carrying a newline must not inject a line-leading ATX heading
        # into the inline `**N. title**` render — it would split/spoof the
        # structured context the reconciliator consumes.
        findings = {
            "dead-code-review": _make_review_json(
                reviewer="dead-code",
                issues=[_make_issue(title="Legit title\n## Injected Heading Zebra")],
            ),
        }
        md = mod.to_markdown(_make_context_with_findings(findings))
        assert "Legit title" in md
        # The injected heading must never appear at the start of a line.
        assert "\n## Injected Heading Zebra" not in md
        assert "Legit title ## Injected Heading Zebra" in md

    def test_build_critic_context_multiline_title_does_not_forge_heading(self, mod):
        findings = {
            "verdict": "comment",
            "summary": {"total_issues": 1, "by_severity": {"medium": 1}},
            "issues": [_make_issue(title="Legit title\n## Injected Heading Zebra")],
        }
        out = mod.build_critic_context("REPORT BODY", findings)
        assert "Legit title" in out
        assert "\n## Injected Heading Zebra" not in out

    def test_escape_inline_collapses_newlines(self, mod):
        out = mod._escape_inline(["Legit title", "## Source Snippets"])
        assert "\n" not in out
        assert "Legit title" in out and "## Source Snippets" in out

    # _escape_inline is `" ".join(...split())` (:1174), and str.split() treats
    # LF, CR and CRLF identically. [cr] is the discriminating param: the
    # pre-fix code (163d4ab9) was .replace("\n", " "), which [lf] would pass.
    @pytest.mark.parametrize("sep", [pytest.param("\r", id="cr")])
    def test_escape_inline_normalizes_all_line_endings(self, mod, sep):
        # CommonMark treats bare CR and CRLF as line endings too, so replacing
        # only LF would still let a CR-delimited title forge a heading.
        out = mod._escape_inline(f"Legit{sep}## Injected Heading Zebra")
        assert "\r" not in out and "\n" not in out
        assert len(out.splitlines()) == 1
        assert not out.lstrip().startswith("## Injected Heading Zebra")

    def test_to_markdown_cr_title_does_not_forge_heading(self, mod):
        findings = {
            "dead-code-review": _make_review_json(
                reviewer="dead-code",
                issues=[_make_issue(title="Legit title\r## Injected Heading Zebra")],
            ),
        }
        md = mod.to_markdown(_make_context_with_findings(findings))
        assert "Legit title" in md
        # No rendered line may start with the injected heading, regardless of
        # which line ending was used.
        assert not any(
            line.lstrip().startswith("## Injected Heading Zebra")
            for line in md.splitlines()
        )


# ===========================================================================
# Inline coverage aggregation — scope summary sidecars
# ===========================================================================

class TestAggregateInlineCoverage:
    """aggregate_inline_coverage() reads *-scope-summary*.json sidecars."""

    def test_returns_none_without_summaries(self, mod, tmp_path):
        assert mod.aggregate_inline_coverage(str(tmp_path)) is None

    def test_returns_none_for_missing_dir(self, mod, tmp_path):
        assert mod.aggregate_inline_coverage(str(tmp_path / "nope")) is None

    def test_files_never_inline(self, mod, tmp_path):
        _write_summary(
            str(tmp_path), "security-reviewer",
            ["src/a.php"], ["src/starved.php", "src/b.php"],
        )
        _write_summary(
            str(tmp_path), "code-reviewer",
            ["src/b.php"], ["src/starved.php"],
        )
        cov = mod.aggregate_inline_coverage(str(tmp_path))
        assert cov["agents_reporting"] == 2
        # b.php was inline for code-reviewer — covered, not a gap.
        assert "src/b.php" not in cov["files_never_inline"]
        # starved.php was skipped by every agent that matched it.
        assert cov["files_never_inline"]["src/starved.php"] == [
            "code-reviewer", "security-reviewer",
        ]

    def test_malformed_summary_skipped(self, mod, tmp_path):
        (tmp_path / "broken-scope-summary.json").write_text("{not json")
        _write_summary(
            str(tmp_path), "security-reviewer",
            ["src/a.php"], [],
        )
        cov = mod.aggregate_inline_coverage(str(tmp_path))
        assert cov["agents_reporting"] == 1

    def test_secondary_summaries_attribute_to_agent(self, mod, tmp_path):
        _write_summary(
            str(tmp_path), "security-reviewer", [], ["ci.yml"],
            domain="config-ops",
        )
        cov = mod.aggregate_inline_coverage(str(tmp_path))
        assert cov["files_never_inline"]["ci.yml"] == ["security-reviewer"]

    def test_undeclared_deferred_file_counts_as_claimed_reviewed(
        self, mod, tmp_path
    ):
        """An agent with output that did NOT declare a deferred file claims
        to have reviewed it per the budget contract — not a coverage gap."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            ["src/a.php"], ["src/deferred.php"],
        )
        _write_review(str(tmp_path), "security-review")

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert "src/deferred.php" not in cov["files_never_inline"]
        assert cov["files_deferred_reviewed"]["src/deferred.php"] == [
            "security-reviewer",
        ]
        assert cov["files_declared_unreviewed"] == {}

    def test_declared_unreviewed_file_stays_a_gap_with_declaration(
        self, mod, tmp_path
    ):
        _write_summary(
            str(tmp_path), "security-reviewer",
            ["src/a.php"], ["src/omitted.php"],
        )
        _write_review(
            str(tmp_path), "security-review", unreviewed=["src/omitted.php"]
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_never_inline"]["src/omitted.php"] == [
            "security-reviewer",
        ]
        assert cov["files_declared_unreviewed"]["src/omitted.php"] == [
            "security-reviewer",
        ]
        assert cov["files_deferred_reviewed"] == {}

    def test_one_agent_claim_outweighs_another_agent_declaration(
        self, mod, tmp_path
    ):
        """A file is covered when ANY deferring agent reviewed it, even if a
        different agent declared it unreviewed."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/shared.php"],
        )
        _write_summary(
            str(tmp_path), "code-reviewer",
            [], ["src/shared.php"],
        )
        _write_review(str(tmp_path), "security-review")
        _write_review(
            str(tmp_path), "code-review", unreviewed=["src/shared.php"]
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert "src/shared.php" not in cov["files_never_inline"]
        assert cov["files_deferred_reviewed"]["src/shared.php"] == [
            "security-reviewer",
        ]
        assert cov["files_declared_unreviewed"]["src/shared.php"] == [
            "code-reviewer",
        ]

    def test_equivalent_declared_path_forms_still_count_as_declared(
        self, mod, tmp_path
    ):
        """A declaration of "./src/omitted.php" must match the sidecar's
        "src/omitted.php" — otherwise an explicit coverage gap inverts into
        a deferred-but-reviewed claim."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/omitted.php"],
        )
        _write_review(
            str(tmp_path), "security-review", unreviewed=["./src/omitted.php"]
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_never_inline"]["src/omitted.php"] == [
            "security-reviewer",
        ]
        assert cov["files_declared_unreviewed"]["src/omitted.php"] == [
            "security-reviewer",
        ]
        assert cov["files_deferred_reviewed"] == {}

    def test_malformed_unreviewed_field_cannot_claim_deferred_files(
        self, mod, tmp_path
    ):
        """A non-null, non-list unreviewed field is unknowable intent — the
        agent can claim nothing, so its deferred files stay genuine gaps
        instead of silently flipping to deferred-but-reviewed."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/deferred.php"],
        )
        _write_review(
            str(tmp_path), "security-review", unreviewed="src/deferred.php"
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_never_inline"]["src/deferred.php"] == [
            "security-reviewer",
        ]
        assert cov["files_deferred_reviewed"] == {}
        assert cov["files_declared_unreviewed"] == {}

    @pytest.mark.parametrize(
        "bad_list",
        [[42], [""], ["src/deferred.php", 7]],
        ids=["int", "empty", "mixed"],
    )
    def test_malformed_unreviewed_entry_fails_the_whole_list_closed(
        self, mod, tmp_path, bad_list
    ):
        """One malformed entry poisons the list — silently dropping it
        could leave [] (a full-review claim) where the agent tried to
        declare a gap. The agent can claim nothing; files stay gaps."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/deferred.php"],
        )
        _write_review(
            str(tmp_path), "security-review", unreviewed=bad_list
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_never_inline"]["src/deferred.php"] == [
            "security-reviewer",
        ]
        assert cov["files_deferred_reviewed"] == {}
        assert cov["files_declared_unreviewed"] == {}

    def test_canonical_null_unreviewed_still_claims_deferred_files(
        self, mod, tmp_path
    ):
        """The builder serializes unreviewed as null when nothing was
        declared — that is the canonical no-declarations case, and per the
        budget contract the agent claims its deferred files were reviewed."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/deferred.php"],
        )
        with open(os.path.join(str(tmp_path), "security-review.json"), "w") as f:
            json.dump(
                {"reviewer": "security", "issues": [], "unreviewed": None}, f
            )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert "src/deferred.php" not in cov["files_never_inline"]
        assert cov["files_deferred_reviewed"]["src/deferred.php"] == [
            "security-reviewer",
        ]

    # Every out-of-set shape lands on the same "matches nothing" branch; only
    # the mixed list discriminates the fail-closed policy from partial credit.
    @pytest.mark.parametrize(
        "declared",
        [["src/deferred.php", "src/other.php"]],
        ids=["mixed"],
    )
    def test_declaration_outside_deferred_set_fails_the_list_closed(
        self, mod, tmp_path, declared
    ):
        """Output that bypassed builder validation can declare any string.
        An entry outside the agent's own deferred set matches nothing, so
        the whole list is unreliable — the agent can claim nothing and its
        deferred files stay genuine gaps."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/deferred.php"],
        )
        _write_review(
            str(tmp_path), "security-review", unreviewed=declared
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_never_inline"]["src/deferred.php"] == [
            "security-reviewer",
        ]
        assert cov["files_deferred_reviewed"] == {}
        assert cov["files_declared_unreviewed"] == {}

    def test_declaring_a_deferred_file_covered_elsewhere_stays_valid(
        self, mod, tmp_path
    ):
        """A declaration of an own-deferred file that another agent covered
        inline is in the agent's deferred set and must not poison the list
        — its other declarations still count."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/shared.php", "src/omitted.php"],
        )
        _write_summary(
            str(tmp_path), "code-reviewer",
            ["src/shared.php"], [],
        )
        _write_review(
            str(tmp_path), "security-review",
            unreviewed=["src/shared.php", "src/omitted.php"],
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        # shared.php was inline elsewhere — covered, not a gap.
        assert "src/shared.php" not in cov["files_never_inline"]
        # The declaration list stayed valid, so omitted.php is a declared gap.
        assert cov["files_never_inline"]["src/omitted.php"] == [
            "security-reviewer",
        ]
        assert cov["files_declared_unreviewed"]["src/omitted.php"] == [
            "security-reviewer",
        ]

    @pytest.mark.parametrize(
        "instance",
        [
            "repo-renewals-reviewer",
            # "reviewer" mid-string: a blanket replace() would corrupt the
            # stem to repo-review-quality-review.json and lose the output.
            "repo-reviewer-quality-reviewer",
            # "scope-summary" mid-string (a legal kebab id): a
            # first-occurrence filename split would truncate the agent to
            # "repo-payments" and misattribute the scope.
            "repo-payments-scope-summary-contract-reviewer",
        ],
        ids=["plain", "midstring-reviewer", "midstring-scope-summary"],
    )
    def test_adapter_instance_declarations_attribute_to_the_instance(
        self, mod, tmp_path, instance
    ):
        """Adapter instances write instance-named scope summaries and
        <instance-stem>-review.json output; their declarations must
        reconcile exactly like a native reviewer's."""
        _write_summary(
            str(tmp_path), instance, [], ["src/deferred.php"], domain="code",
        )
        stem = instance[: -len("-reviewer")]
        _write_review(
            str(tmp_path), f"{stem}-review",
            unreviewed=["src/deferred.php"],
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_never_inline"]["src/deferred.php"] == [instance]
        assert cov["files_declared_unreviewed"]["src/deferred.php"] == [
            instance
        ]

    def test_agent_without_output_cannot_claim_deferred_files(
        self, mod, tmp_path
    ):
        """No review JSON means the agent can neither claim nor declare —
        its deferred files stay genuine gaps (pre-1.109.0 behavior)."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/deferred.php"],
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_never_inline"]["src/deferred.php"] == [
            "security-reviewer",
        ]
        assert cov["files_deferred_reviewed"] == {}


class TestUnscopedFiles:
    """`files_unscoped` — changed files no reviewer's scope contained.

    The population that used to vanish: every other bucket is keyed on a
    file some agent's sidecar mentions, so a lockfile, binary, or dotfile
    matching no domain landed in none of them. A field run's true
    never-covered population was ~46 while the report said 41.
    """

    def test_changed_files_matching_no_domain_are_reported(self, mod, tmp_path):
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = mod.aggregate_inline_coverage(
            str(tmp_path),
            changed_files=[
                "src/a.php", "package-lock.json", ".editorconfig",
            ],
        )
        assert cov["files_unscoped"] == [".editorconfig", "package-lock.json"]

    def test_union_covers_every_sidecar_file_list(self, mod, tmp_path):
        """Inline, deferred, AND name-only listing all count as scoped —
        a file the agent was told about is not "matched no domain"."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            ["src/inline.php"], ["src/deferred.php"],
            list_only=["src/listed.php"],
        )
        cov = mod.aggregate_inline_coverage(
            str(tmp_path),
            changed_files=[
                "src/inline.php", "src/deferred.php", "src/listed.php",
                "yarn.lock",
            ],
        )
        assert cov["files_unscoped"] == ["yarn.lock"]

    def test_git_quoted_changed_path_matches_the_unquoted_sidecar(
        self, mod, tmp_path
    ):
        """The two producers quote differently and the set difference is
        arithmetic on their paths.

        `context.py` runs a plain `git diff --name-only`, so a non-ASCII
        path arrives C-quoted and octal-escaped; scope sidecars run
        `-c core.quotepath=false` and emit real UTF-8. Subtracting one
        alphabet from the other published a fully reviewed file as
        "reviewed by no one" — inside the block step 9 now forbids the
        orchestrator to correct.
        """
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/café.php"], [],
        )
        cov = mod.aggregate_inline_coverage(
            str(tmp_path), changed_files=[r'"src/caf\303\251.php"'],
        )
        assert cov["files_unscoped"] == []

    def test_unnormalizable_changed_path_leaves_the_population_unmeasured(
        self, mod, tmp_path
    ):
        """A shrunken population reads as a cleaner review than the run
        earned, so the strict side fails to unmeasured instead."""
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = mod.aggregate_inline_coverage(
            str(tmp_path),
            changed_files=["src/a.php", r'"src/broken\3"'],
        )
        assert cov["files_unscoped"] is None

    def test_equivalent_spellings_of_one_path_are_one_file(
        self, mod, tmp_path
    ):
        _write_summary(
            str(tmp_path), "security-reviewer", ["./src//a.php"], [],
        )
        cov = mod.aggregate_inline_coverage(
            str(tmp_path), changed_files=["src/a.php"],
        )
        assert cov["files_unscoped"] == []

    def test_all_files_scoped_is_measured_empty(self, mod, tmp_path):
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = mod.aggregate_inline_coverage(
            str(tmp_path), changed_files=["src/a.php"],
        )
        assert cov["files_unscoped"] == []

    def test_no_changed_file_list_is_unmeasured_not_empty(self, mod, tmp_path):
        """None, not [] — a caller must not read "not measured" as "none"."""
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = mod.aggregate_inline_coverage(str(tmp_path))
        assert cov["files_unscoped"] is None

    def test_secondary_domain_sidecar_files_count_as_scoped(
        self, mod, tmp_path
    ):
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        _write_summary(
            str(tmp_path), "security-reviewer", ["ci.yml"], [],
            domain="config-ops",
        )
        cov = mod.aggregate_inline_coverage(
            str(tmp_path), changed_files=["src/a.php", "ci.yml"],
        )
        assert cov["files_unscoped"] == []


class TestAgentsReportingCountsAgents:
    """`agents_reporting` counts distinct agents, not summary files.

    Three reviewers ship a second `-config-ops` sidecar, so the file count
    reported 22 agents for a 19-agent field run.
    """

    def test_config_ops_sidecar_does_not_double_count_its_agent(
        self, mod, tmp_path
    ):
        for agent in ("security-reviewer", "code-reviewer", "wp-reviewer"):
            _write_summary(str(tmp_path), agent, ["src/a.php"], [])
        for agent in ("security-reviewer", "code-reviewer", "wp-reviewer"):
            _write_summary(
                str(tmp_path), agent, ["ci.yml"], [], domain="config-ops",
            )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert len(list(tmp_path.glob("*-scope-summary*.json"))) == 6
        assert cov["agents_reporting"] == 3

    def test_only_unreadable_summaries_still_reads_as_no_data(
        self, mod, tmp_path
    ):
        (tmp_path / "broken-scope-summary.json").write_text("{not json")
        assert mod.aggregate_inline_coverage(str(tmp_path)) is None


class TestExplicitClaimsCoverage:
    """Outputs carrying `deferred_reviewed` switch the aggregator to stated
    claims — an agent's silence about a deferred file becomes a visible gap
    instead of an inferred review claim. Key-less outputs keep the legacy
    complement semantics."""

    def test_explicit_claims_partition_deferred_files(self, mod, tmp_path):
        """Claimed, declared, and unaccounted deferred files land in three
        distinct places — the unaccounted one is a GAP, never a claim."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/claimed.php", "src/declared.php", "src/silent.php"],
        )
        _write_review(
            str(tmp_path), "security-review",
            unreviewed=["src/declared.php"],
            claims=["src/claimed.php"],
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_deferred_reviewed"] == {
            "src/claimed.php": ["security-reviewer"],
        }
        assert cov["files_declared_unreviewed"]["src/declared.php"] == [
            "security-reviewer",
        ]
        assert "src/claimed.php" not in cov["files_never_inline"]
        assert cov["files_never_inline"]["src/declared.php"] == [
            "security-reviewer",
        ]
        # The file the agent never mentioned: silence is not a claim.
        assert cov["files_never_inline"]["src/silent.php"] == [
            "security-reviewer",
        ]
        assert "src/silent.php" not in cov["files_deferred_reviewed"]
        assert "src/silent.php" not in cov["files_declared_unreviewed"]

    def test_empty_claims_list_claims_nothing(self, mod, tmp_path):
        """`deferred_reviewed: []` is the explicit "claimed nothing" signal
        the builder always emits — it must not read as a legacy output."""
        _write_summary(
            str(tmp_path), "security-reviewer", [], ["src/deferred.php"],
        )
        _write_review(str(tmp_path), "security-review", claims=[])

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_never_inline"]["src/deferred.php"] == [
            "security-reviewer",
        ]
        assert cov["files_deferred_reviewed"] == {}

    def test_claim_path_forms_are_normalized(self, mod, tmp_path):
        """A claim of "./src/deferred.php" addresses the sidecar's
        "src/deferred.php" — the same grammar declarations speak. The
        unclaimed sibling proves the match came from the claim rather than
        from a fallback to the legacy complement."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/deferred.php", "src/silent.php"],
        )
        _write_review(
            str(tmp_path), "security-review", claims=["./src/deferred.php"],
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_deferred_reviewed"] == {
            "src/deferred.php": ["security-reviewer"],
        }
        assert "src/deferred.php" not in cov["files_never_inline"]
        assert cov["files_never_inline"]["src/silent.php"] == [
            "security-reviewer",
        ]

    def test_legacy_output_without_claims_key_keeps_complement(
        self, mod, tmp_path
    ):
        """Outputs predating the claims field carry no `deferred_reviewed`
        key; for them silence still means "reviewed" — changing that would
        retroactively invent gaps in already-published runs."""
        _write_summary(
            str(tmp_path), "security-reviewer", [], ["src/deferred.php"],
        )
        _write_review(str(tmp_path), "security-review")

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_deferred_reviewed"]["src/deferred.php"] == [
            "security-reviewer",
        ]
        assert "src/deferred.php" not in cov["files_never_inline"]

    @pytest.mark.parametrize(
        "claims",
        [["src/deferred.php", "src/other.php"]],
        ids=["mixed"],
    )
    def test_out_of_set_claims_fail_closed_within_explicit_mode(
        self, mod, tmp_path, claims
    ):
        """A claim outside the agent's own deferred set proves the list
        unreliable. It fails closed to claiming NOTHING — never back to the
        legacy complement, which would claim MORE than the agent stated."""
        _write_summary(
            str(tmp_path), "security-reviewer", [], ["src/deferred.php"],
        )
        _write_review(str(tmp_path), "security-review", claims=claims)

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_deferred_reviewed"] == {}
        assert cov["files_never_inline"]["src/deferred.php"] == [
            "security-reviewer",
        ]
        assert cov["files_declared_unreviewed"] == {}

    @pytest.mark.parametrize(
        "claims",
        [
            "src/deferred.php",              # not a list
            [42],                            # non-str entry
            [""],                            # blank entry
            ["src/deferred.php", 7],         # one valid + one malformed
        ],
        ids=["string", "int-entry", "empty-entry", "mixed"],
    )
    def test_malformed_claims_fail_closed_within_explicit_mode(
        self, mod, tmp_path, claims
    ):
        """A malformed claims value is unknowable intent, but the KEY is
        present — so the output is explicit-mode and the agent claims
        nothing. Falling back to the complement would turn garbage into a
        review claim for every deferred file."""
        _write_summary(
            str(tmp_path), "security-reviewer", [], ["src/deferred.php"],
        )
        _write_review(str(tmp_path), "security-review", claims=claims)

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_deferred_reviewed"] == {}
        assert cov["files_never_inline"]["src/deferred.php"] == [
            "security-reviewer",
        ]

    def test_valid_claims_survive_failed_closed_declarations(
        self, mod, tmp_path
    ):
        """Declarations and claims fail independently: a malformed
        `unreviewed` (fail-to-None) must not void a well-formed claim, and
        the files the claim does not cover stay gaps."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/claimed.php", "src/gap.php"],
        )
        _write_review(
            str(tmp_path), "security-review",
            unreviewed="src/gap.php",           # malformed: not a list
            claims=["src/claimed.php"],
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_deferred_reviewed"]["src/claimed.php"] == [
            "security-reviewer",
        ]
        assert cov["files_never_inline"]["src/gap.php"] == ["security-reviewer"]
        assert cov["files_declared_unreviewed"] == {}

    def test_explicit_and_legacy_agents_coexist_per_file(self, mod, tmp_path):
        """Mode is per-output, not per-run: one agent's legacy complement
        can still cover a file its explicit-mode peer left unaccounted."""
        _write_summary(
            str(tmp_path), "security-reviewer", [], ["src/shared.php"],
        )
        _write_summary(
            str(tmp_path), "code-reviewer", [], ["src/shared.php"],
        )
        _write_review(str(tmp_path), "security-review", claims=[])
        _write_review(str(tmp_path), "code-review")  # legacy, no key

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_deferred_reviewed"]["src/shared.php"] == [
            "code-reviewer",
        ]
        assert "src/shared.php" not in cov["files_never_inline"]


class TestAutofilledUnreviewedAttribution:
    """Save-time auto-filled paths are the SYSTEM's backfill, not the
    reviewer's budget judgment — the reconciliation context must not
    attribute them to the agent."""

    def test_autofilled_paths_split_from_agent_declarations(
        self, mod, tmp_path
    ):
        (tmp_path / "security-review.json").write_text(json.dumps({
            "reviewer": "security",
            "issues": [],
            "unreviewed": ["src/declared.php", "src/auto.php"],
            "deferred_reviewed": [],
            "meta": {"unreviewed_autofilled": ["src/auto.php"]},
        }))
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/declared.php", "src/auto.php"],
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_declared_unreviewed"] == {
            "src/declared.php": ["security-reviewer"],
        }
        assert cov["files_autofilled_unreviewed"] == {
            "src/auto.php": ["security-reviewer"],
        }
        # Both remain genuine gaps — only the attribution differs.
        assert set(cov["files_never_inline"]) == {
            "src/declared.php", "src/auto.php",
        }

    def test_absent_marker_leaves_every_path_agent_declared(
        self, mod, tmp_path
    ):
        (tmp_path / "security-review.json").write_text(json.dumps({
            "reviewer": "security",
            "issues": [],
            "unreviewed": ["src/declared.php"],
        }))
        _write_summary(
            str(tmp_path), "security-reviewer", [], ["src/declared.php"],
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_declared_unreviewed"]["src/declared.php"] == [
            "security-reviewer",
        ]
        assert cov["files_autofilled_unreviewed"] == {}

    def test_malformed_marker_entry_degrades_only_that_entry(
        self, mod, tmp_path
    ):
        """The marker labels gaps, it does not carry coverage: one bad
        entry must cost only its own attribution, not the whole marker.
        Failing the list would silently relabel every real auto-fill as the
        reviewer's own budget judgment — the attribution this key exists to
        prevent."""
        (tmp_path / "security-review.json").write_text(json.dumps({
            "reviewer": "security",
            "issues": [],
            "unreviewed": ["src/declared.php", "src/auto.php"],
            "deferred_reviewed": [],
            "meta": {"unreviewed_autofilled": ["src/auto.php", 42]},
        }))
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/declared.php", "src/auto.php"],
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_autofilled_unreviewed"] == {
            "src/auto.php": ["security-reviewer"],
        }
        assert cov["files_declared_unreviewed"] == {
            "src/declared.php": ["security-reviewer"],
        }

    def test_non_list_marker_leaves_every_path_agent_declared(
        self, mod, tmp_path
    ):
        (tmp_path / "security-review.json").write_text(json.dumps({
            "reviewer": "security",
            "issues": [],
            "unreviewed": ["src/auto.php"],
            "deferred_reviewed": [],
            "meta": {"unreviewed_autofilled": "src/auto.php"},
        }))
        _write_summary(
            str(tmp_path), "security-reviewer", [], ["src/auto.php"],
        )

        cov = mod.aggregate_inline_coverage(str(tmp_path))

        assert cov["files_autofilled_unreviewed"] == {}
        assert cov["files_declared_unreviewed"]["src/auto.php"] == [
            "security-reviewer",
        ]

    def test_render_distinguishes_the_two_populations(self, mod):
        ctx = _make_context_with_findings({})
        ctx["inline_coverage"] = {
            "agents_reporting": 1,
            "files_inline": {},
            "files_never_inline": {
                "src/declared.php": ["code-reviewer"],
                "src/auto.php": ["code-reviewer"],
            },
            "files_declared_unreviewed": {
                "src/declared.php": ["code-reviewer"],
            },
            "files_autofilled_unreviewed": {
                "src/auto.php": ["code-reviewer"],
            },
            "files_deferred_reviewed": {},
        }
        md = mod.to_markdown(ctx)
        assert (
            "`src/declared.php` (skipped by: code-reviewer; "
            "declared unreviewed (budget) by: code-reviewer)"
        ) in md
        assert (
            "`src/auto.php` (skipped by: code-reviewer; auto-declared "
            "unreviewed at save (unaccounted) by: code-reviewer)"
        ) in md

    def test_real_save_autofill_is_never_rendered_as_budget_judgment(
        self, mod, tmp_path, monkeypatch
    ):
        """End-to-end: a real ReviewOutputBuilder save auto-fills a deferred
        path the reviewer never mentioned. That path must reach the
        reconciliation context labeled as a save-time backfill — labeling it
        "(budget)" would credit the system's honesty to the reviewer."""
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        (tmp_path / "code-deferred-files.json").write_text(json.dumps({
            "schema": 1,
            "deferred_files": ["src/declared.php", "src/auto.php"],
        }))
        _write_summary(
            str(tmp_path), "code-reviewer",
            [], ["src/declared.php", "src/auto.php"],
        )

        builder = ReviewOutputBuilder("42", "code")
        builder.add_unreviewed("src/declared.php")
        builder.save(str(tmp_path))

        saved = json.loads((tmp_path / "code-review.json").read_text())
        assert saved["meta"]["unreviewed_autofilled"] == ["src/auto.php"]

        cov = mod.aggregate_inline_coverage(str(tmp_path))
        ctx = _make_context_with_findings({})
        ctx["inline_coverage"] = cov
        md = mod.to_markdown(ctx)

        auto_line = next(
            line for line in md.splitlines() if "`src/auto.php`" in line
        )
        declared_line = next(
            line for line in md.splitlines() if "`src/declared.php`" in line
        )
        assert "auto-declared unreviewed at save (unaccounted)" in auto_line
        assert "(budget)" not in auto_line
        assert "declared unreviewed (budget) by: code-reviewer" in declared_line
        assert "auto-declared" not in declared_line


class TestInlineCoverageMarkdown:
    """to_markdown() surfaces inline coverage gaps prominently."""

    def test_gaps_render_loudly(self, mod):
        ctx = _make_context_with_findings({})
        ctx["inline_coverage"] = {
            "agents_reporting": 3,
            "files_inline": {"src/a.php": ["code-reviewer"]},
            "files_never_inline": {
                "src/starved.php": ["code-reviewer", "security-reviewer"],
            },
        }
        md = mod.to_markdown(ctx)
        assert "## Inline Diff Coverage Gaps" in md
        assert "src/starved.php" in md
        assert "NO reviewer received" in md
        # Prepended — must appear before the findings sections.
        assert md.index("Inline Diff Coverage Gaps") < md.index("## Metadata")

    def test_gap_warning_targets_an_artifact_the_reconciliator_writes(
        self, mod
    ):
        """The reconciliator publishes JSON only — instructing it to carry
        the coverage warning into `review-findings.md`, a file the pipeline
        now renders from that JSON, asks for a write it cannot make."""
        ctx = _make_context_with_findings({})
        ctx["inline_coverage"] = {
            "agents_reporting": 2,
            "files_inline": {},
            "files_never_inline": {
                "src/starved.php": ["code-reviewer"],
            },
        }
        md = mod.to_markdown(ctx)
        assert "coverage warning" in md
        assert "carry this list into\n`review-findings.json`" in md or (
            "carry this list into `review-findings.json`" in md
        )
        assert "review-findings.md" not in md

    def test_unscoped_files_render_as_their_own_section(self, mod):
        """Never merged into the gaps list: starved-by-budget and
        routed-to-nobody are different failures."""
        ctx = _make_context_with_findings({})
        ctx["inline_coverage"] = {
            "agents_reporting": 2,
            "files_inline": {},
            "files_never_inline": {},
            "files_unscoped": ["package-lock.json", ".editorconfig"],
        }
        md = mod.to_markdown(ctx)
        assert "## Changed Files In No Reviewer's Scope" in md
        assert (
            "2 changed file(s) matched no reviewer's domain and were "
            "reviewed by no one" in md
        )
        assert "- `package-lock.json`" in md
        assert md.index("No Reviewer's Scope") < md.index("## Metadata")

    def test_no_unscoped_section_when_everything_is_scoped(self, mod):
        ctx = _make_context_with_findings({})
        ctx["inline_coverage"] = {
            "agents_reporting": 2,
            "files_inline": {},
            "files_never_inline": {},
            "files_unscoped": [],
        }
        assert "No Reviewer's Scope" not in mod.to_markdown(ctx)

    def test_no_section_without_gaps(self, mod):
        ctx = _make_context_with_findings({})
        ctx["inline_coverage"] = {
            "agents_reporting": 3,
            "files_inline": {"src/a.php": ["code-reviewer"]},
            "files_never_inline": {},
        }
        md = mod.to_markdown(ctx)
        assert "Inline Diff Coverage Gaps" not in md

    def test_no_section_without_coverage_data(self, mod):
        md = mod.to_markdown(_make_context_with_findings({}))
        assert "Inline Diff Coverage Gaps" not in md

    def test_gap_entries_annotate_declarations(self, mod):
        ctx = _make_context_with_findings({})
        ctx["inline_coverage"] = {
            "agents_reporting": 2,
            "files_inline": {},
            "files_never_inline": {
                "src/omitted.php": ["security-reviewer"],
            },
            "files_declared_unreviewed": {
                "src/omitted.php": ["security-reviewer"],
            },
            "files_deferred_reviewed": {},
        }
        md = mod.to_markdown(ctx)
        assert (
            "`src/omitted.php` (skipped by: security-reviewer; "
            "declared unreviewed (budget) by: security-reviewer)"
        ) in md

    def test_deferred_reviewed_files_render_as_claims_not_gaps(self, mod):
        ctx = _make_context_with_findings({})
        ctx["inline_coverage"] = {
            "agents_reporting": 2,
            "files_inline": {},
            "files_never_inline": {},
            "files_declared_unreviewed": {},
            "files_deferred_reviewed": {
                "src/deferred.php": ["security-reviewer"],
            },
        }
        md = mod.to_markdown(ctx)
        assert "Inline Diff Coverage Gaps" not in md
        assert "## Deferred Files Reviewed From The NOT DIFFED Queue" in md
        assert "`src/deferred.php` (claimed by: security-reviewer)" in md
        # The note must teach the CURRENT contract: claims are the agent's
        # own explicit statements, and silence-derivation survives only for
        # legacy output predating the `deferred_reviewed` key.
        assert "the agent's own claim, not proof of read" in md
        assert "stated explicitly under `deferred_reviewed`" in md
        assert "legacy output predating that key" in md


class TestReviewStem:
    """Review files are named by TERMINAL-suffix derivation only — a
    blanket replace corrupts repo reviewer ids carrying "reviewer"
    mid-string (e.g. "api-reviewer-v2") and silently excludes their valid
    blocking output."""

    def test_mid_string_reviewer_id_output_is_loaded(self, mod, tmp_path):
        (tmp_path / "repo-api-reviewer-v2-review.json").write_text(json.dumps({
            "reviewer": "repo-api-reviewer-v2",
            "issues": [],
            "verdict": "approve",
        }))
        findings = mod.load_agent_findings(
            str(tmp_path),
            dispatched_agents=["repo-api-reviewer-v2-reviewer"],
        )
        assert "repo-api-reviewer-v2-review" in findings

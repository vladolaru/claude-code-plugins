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
    list_only=None, in_scope=None,
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
            # Real sidecars publish this in every mode; the helper defaults
            # it to the union of what was passed so ordinary-mode fixtures
            # stay honest without every caller restating their scope.
            "in_scope_files": (
                list(in_scope) if in_scope is not None
                else sorted(
                    set(files_with_diffs)
                    | set(budget_exceeded)
                    | set(list_only or [])
                )
            ),
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

    def test_empty_changed_files_flag_reads_as_unmeasured(self, tmp_path):
        """orchestration.py always passes `--changed-files`, and passes ""
        when review-context.json carries no CSV.

        That is the production path on which the unmeasured branch is
        reached. Before this, it published `files_unscoped: []` — a clean
        coverage bill for a population nothing had measured.
        """
        _write_summary(str(tmp_path), "security-reviewer", ["src/auth.py"], [])

        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc123..HEAD",
            "--changed-files", "",
            cwd=tmp_path,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        ctx = json.loads(
            (tmp_path / "reconciliation-context.json").read_text()
        )
        assert ctx["inline_coverage"]["files_unscoped"] is None

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

    def test_writes_no_markdown_projection(self, tmp_path):
        """`reconciliation-context.md` had exactly one reader — the
        reconciliator agent — and a Markdown projection whose only reader
        is an agent is a second rendering of the same data that has to be
        kept honest by hand. The agent reads the JSON."""
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
        assert not (tmp_path / "reconciliation-context.md").exists()

        stdout_json = json.loads(result.stdout.strip())
        assert stdout_json["status"] == "ok"
        assert stdout_json["path"].endswith("reconciliation-context.json")
        assert "markdown_path" not in stdout_json

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

    def test_base_ref_only_agent_contributes_its_whole_scope(
        self, mod, tmp_path
    ):
        """A `--base-ref-only`/`--summary` agent never fetches a diff, so
        its three diff-derived lists are legitimately empty.

        patterns-reviewer is configured that way in the registry, and the
        reviewer protocol sends every reviewer there on 100+-file PRs — the
        exact runs this measurement exists for. Before `in_scope_files`,
        every file such an agent owned published as matched by no one.
        """
        _write_summary(
            str(tmp_path), "patterns-reviewer", [], [],
            in_scope=["src/a.php", "src/b.php"],
        )
        cov = mod.aggregate_inline_coverage(
            str(tmp_path),
            changed_files=["src/a.php", "src/b.php", "yarn.lock"],
        )
        assert cov["files_unscoped"] == ["yarn.lock"]

    def test_sidecar_without_the_field_degrades_to_contributing_nothing(
        self, mod, tmp_path
    ):
        """A run whose sidecars predate `in_scope_files` under-reports
        exactly as it did before — never a new false claim, never a
        crash."""
        path = tmp_path / "legacy-reviewer-scope-summary.json"
        path.write_text(json.dumps({
            "schema": 1,
            "domain": "x",
            "status": "OK",
            "files_with_diffs": ["src/a.php"],
            "budget_exceeded_files": [],
            "list_only_files": [],
        }))
        cov = mod.aggregate_inline_coverage(
            str(tmp_path), changed_files=["src/a.php", "src/b.php"],
        )
        assert cov["files_unscoped"] == ["src/b.php"]

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

    @pytest.mark.parametrize(
        "changed_files", [None, []], ids=["absent", "empty"],
    )
    def test_absent_and_empty_changed_lists_are_both_unmeasured(
        self, mod, tmp_path, changed_files
    ):
        """An empty list is an absent list, not "zero changed files".

        A review of zero changed files does not exist; a run whose file
        list never reached the builder does, and orchestration.py reaches
        it by passing `--changed-files ""`. Reading that as measured-and-
        zero publishes a clean coverage bill nothing looked at.
        """
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = mod.aggregate_inline_coverage(
            str(tmp_path), changed_files=changed_files,
        )
        assert cov["files_unscoped"] is None

    def test_a_measured_run_that_finds_nothing_reports_an_empty_list(
        self, mod, tmp_path
    ):
        """The other side of the same distinction: measured and clean."""
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = mod.aggregate_inline_coverage(
            str(tmp_path), changed_files=["src/a.php"],
        )
        assert cov["files_unscoped"] == []

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

    def test_real_save_autofill_is_never_filed_as_budget_judgment(
        self, mod, tmp_path, monkeypatch
    ):
        """End-to-end: a real ReviewOutputBuilder save auto-fills a deferred
        path the reviewer never mentioned. That path must reach the
        reconciliation context in the save-time-backfill bucket — filing it
        as "(budget)" would credit the system's honesty to the reviewer."""
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

        assert cov["files_autofilled_unreviewed"] == {
            "src/auto.php": ["code-reviewer"],
        }
        assert cov["files_declared_unreviewed"] == {
            "src/declared.php": ["code-reviewer"],
        }
        # Disjoint per (file, agent): a path is one or the other for a
        # given reviewer, never both.
        assert not (
            set(cov["files_autofilled_unreviewed"])
            & set(cov["files_declared_unreviewed"])
        )


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

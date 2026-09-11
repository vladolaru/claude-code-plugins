"""Tests for review/reconciliation_context.py — deterministic, no model calls.

Tests the reconciliation context builder by importing functions directly
and by running the full script via subprocess for integration tests.
"""

import importlib.util
import json
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
sys.path.insert(0, str(TESTS_DIR))

from review.verdict_rules import derive_review_state  # noqa: E402
from review.reviewer_lifecycle import review_paths  # noqa: E402
from review.run_paths import artifact_path  # noqa: E402
from review.reconciliation_notes import add_note  # noqa: E402


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

def _make_finding(
    severity="medium",
    title="Test finding",
    file="src/app.py",
    line=42,
    description="Some finding found",
    recommendation="Fix it",
    category="general",
    confidence=0.9,
    severity_floor=None,
):
    """Create a single finding dict matching ReviewOutputBuilder format."""
    finding = {
        "id": "f1",
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
        finding["severity_floor"] = severity_floor
    return finding


def _make_review_json(
    reviewer="security",
    pr_id="42",
    verdict=None,
    findings=None,
):
    """Create a complete review JSON dict matching ReviewOutputBuilder output."""
    if findings is None:
        findings = [_make_finding()]

    findings = [
        dict(finding, id=f"f{index}")
        for index, finding in enumerate(findings, 1)
    ]
    # The verdict ladder, the advisory split and the suppression
    # measurement come from production. A fixture carrying its own copy
    # grades the pipeline against thresholds that can silently disagree
    # with the ones it ships.
    derived = derive_review_state(findings)
    derived_verdict = derived["verdict"]
    summary = {
        "total_findings": len(findings),
        "by_severity": derived["counts"],
        **derived["advisory"],
    }

    return {
        "pr_id": pr_id,
        "reviewer": reviewer,
        "timestamp": "2026-04-04T10:00:00",
        "plugin_version": None,
        "schema": 2,
        "verdict": derived_verdict if verdict is None else verdict,
        "summary": summary,
        "findings": findings,
        "review_claimable_files": [],
        "reviewed_file_claims": [],
        "unclaimed_review_files": [],
        "inline_diff_file_count": 3,
        "reviewed_file_count": 3,
        "in_scope_review_file_count": 3,
        "observations": [],
        "recommendations": {
            "immediate": [], "important": [], "suggestions": [],
        },
        "positive_observations": [],
        "checks": [],
        "assessment": None,
        "meta": {
            "review_duration_ms": 1500,
            "confidence_score": 0.95,
            "next_finding_number": len(findings) + 1,
            "next_check_number": 1,
        },
    }


def _write_review_json(output_dir, reviewer, payload):
    path = Path(review_paths(output_dir, reviewer).final)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _write_raw_review(output_dir, reviewer, text):
    path = Path(review_paths(output_dir, reviewer).final)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _read_reconciliation_context(output_dir):
    return json.loads(
        artifact_path(output_dir, "reconciliation_context").read_text()
    )


# ===========================================================================
# TestLoadAgentReviews
# ===========================================================================

class TestLoadAgentReviews:
    """Tests for load_agent_reviews()."""

    def test_loads_review_jsons(self, mod, tmp_path):
        """Loads reviewer-directory review.json files and keys by review stem."""
        review = _make_review_json(reviewer="security")
        _write_review_json(tmp_path, "security", review)
        _write_review_json(tmp_path, "code", _make_review_json(reviewer="code"))

        result = mod.load_agent_reviews(str(tmp_path))
        assert "security-review" in result
        assert "code-review" in result
        assert result["security-review"]["reviewer"] == "security"
        assert result["code-review"]["reviewer"] == "code"

    def test_ignores_canonical_looking_legacy_flat_artifacts(
        self, mod, tmp_path
    ):
        (tmp_path / "security-review.json").write_text(
            json.dumps(_make_review_json(reviewer="security"))
        )
        (tmp_path / "security-assignment.json").write_text(
            json.dumps({
                "schema": 5,
                "agent_name": "security-reviewer",
                "reviewer": "security",
            })
        )

        assert mod.load_agent_reviews(str(tmp_path)) == {}

    def test_handles_empty_directory(self, mod, tmp_path):
        """Empty directory returns empty dict; a non-existent one does too
        (same fallback: no `reviewers/` subdirectory to scan)."""
        assert mod.load_agent_reviews(str(tmp_path)) == {}
        assert mod.load_agent_reviews(str(tmp_path / "nonexistent")) == {}

    @pytest.mark.parametrize("text", [
        pytest.param("{ not valid json !!!", id="malformed-json"),
        pytest.param("[]", id="non-object-json"),
    ])
    def test_skips_a_malformed_review_without_blocking_other_reviewers(
        self, mod, tmp_path, text
    ):
        """A parse failure in one reviewer's directory does not abort the
        scan of the rest: the try/except lives inside the per-reviewer
        loop iteration, not around it."""
        _write_review_json(tmp_path, "security", _make_review_json())
        _write_raw_review(tmp_path, "broken", text)

        result = mod.load_agent_reviews(str(tmp_path))
        assert "security-review" in result
        assert "broken-review" not in result

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda review: review.pop("schema"), id="missing-schema"),
            pytest.param(lambda review: review.update(schema=1), id="retired-schema"),
            pytest.param(
                lambda review: review.update(issues=[]),
                id="retired-findings-field",
            ),
            pytest.param(
                lambda review: review["summary"].update(total_findings=0),
                id="inconsistent-summary",
            ),
        ],
    )
    def test_skips_noncanonical_final_reviews(
        self, mod, tmp_path, capsys, mutate
    ):
        review = _make_review_json(reviewer="security")
        mutate(review)
        path = _write_review_json(tmp_path, "security", review)

        result = mod.load_agent_reviews(str(tmp_path))

        assert result == {}
        assert str(path) in capsys.readouterr().err

    def test_skips_review_whose_identity_disagrees_with_filename(
        self, mod, tmp_path
    ):
        _write_review_json(
            tmp_path, "security", _make_review_json(reviewer="performance")
        )

        assert mod.load_agent_reviews(str(tmp_path)) == {}

    def test_filters_by_dispatched_agents(self, mod, tmp_path):
        """Only loads review files for agents in the dispatch plan; `None`
        is the default every other test in this class relies on — load
        everything."""
        _write_review_json(tmp_path, "security", _make_review_json(reviewer="security"))
        _write_review_json(tmp_path, "performance", _make_review_json(reviewer="performance"))
        _write_review_json(tmp_path, "architecture", _make_review_json(reviewer="architecture"))

        # Only security-reviewer and performance-reviewer are dispatched
        result = mod.load_agent_reviews(
            str(tmp_path),
            dispatched_agents=["security-reviewer", "performance-reviewer"],
        )
        assert len(result) == 2
        assert "security-review" in result
        assert "performance-review" in result
        assert "architecture-review" not in result

        assert len(mod.load_agent_reviews(str(tmp_path), dispatched_agents=None)) == 3

    def test_dispatched_agents_empty_list_loads_nothing(self, mod, tmp_path):
        """An empty dispatched_agents list loads no review files."""
        _write_review_json(tmp_path, "security", _make_review_json(reviewer="security"))

        result = mod.load_agent_reviews(str(tmp_path), dispatched_agents=[])
        assert len(result) == 0


class TestSeverityFloorNormalization:
    """The floor is a structured field; prose is not a second channel."""

    def test_the_structured_field_is_the_floor(self, mod):
        finding = _make_finding(severity_floor="medium")

        assert mod.resolve_structured_severity_floor(finding) == "medium"

    def test_prose_never_promotes_a_finding(self, mod):
        """A description is reviewer narrative, not a machine directive.

        The prose parser existed for a transition that is over: every
        reviewer writes `severity_floor` structurally now, and a parser
        that promotes findings off free text is a parser a model can
        trigger by describing what it did not intend to assert. One
        spelling proves the absence; `load_agent_reviews` dropping a
        prose-only floor at load time is the same absence, one layer up,
        with no separate parser of its own to regress.
        """
        assert mod.resolve_structured_severity_floor(
            _make_finding(description="Severity-floor: high — verified false-success")
        ) is None

    @pytest.mark.parametrize("text", [
        "Severity-floor: high — the caller is unguarded",
        ["Body.", "Severity-floor: critical; see above"],
        None,
    ])
    def test_the_marker_is_stripped_before_the_critic_reads_it(
        self, mod, text
    ):
        """The strip survives the parser's deletion, coercion included.

        `strip_severity_floor_markers` is the one export
        `orchestration.assemble_review_record()` uses, and it still has
        to accept model-authored non-strings without raising.
        """
        stripped = mod.strip_severity_floor_markers(text)

        assert "Severity-floor" not in stripped


# ===========================================================================
# TestExtractReferences
# ===========================================================================

class TestExtractReferences:
    """Tests for extract_references()."""

    def test_extracts_unique_refs(self, mod):
        """Extracts file:line pairs from agent findings."""
        findings = {
            "security-review": _make_review_json(findings=[
                _make_finding(file="src/auth.py", line=10),
                _make_finding(file="src/db.py", line=20),
            ]),
        }
        refs = mod.extract_references(findings)
        assert len(refs) == 2
        files = {r["file"] for r in refs}
        assert files == {"src/auth.py", "src/db.py"}

    def test_deduplicates_same_file(self, mod):
        """Same file from multiple agents is deduplicated, lines merged."""
        findings = {
            "security-review": _make_review_json(findings=[
                _make_finding(file="src/auth.py", line=10),
                _make_finding(file="src/auth.py", line=30),
            ]),
            "performance-review": _make_review_json(findings=[
                _make_finding(file="src/auth.py", line=20),
                _make_finding(file="src/auth.py", line=10),  # duplicate line
            ]),
        }
        refs = mod.extract_references(findings)
        assert len(refs) == 1
        assert refs[0]["file"] == "src/auth.py"
        assert refs[0]["lines"] == [10, 20, 30]

    def test_skips_missing_lines(self, mod):
        """Findings without a valid line field are skipped."""
        findings = {
            "security-review": _make_review_json(findings=[
                _make_finding(file="src/auth.py", line=10),
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

    @pytest.mark.parametrize("lines,context_lines,expected_count", [
        pytest.param([3, 8], 2, 10, id="adjacent_windows_merge"),
        pytest.param([6, 3], 5, 11, id="unsorted_and_contained_windows_merge"),
    ])
    def test_adjacent_and_contained_windows_merge(
        self, mod, tmp_path, lines, context_lines, expected_count
    ):
        """`_merge_windows` merges adjacent windows (end+1 == next start)
        and windows one contains inside another, sorting unsorted input
        first — exercised here through the public API rather than the
        private helper directly."""
        source_file = tmp_path / "app.py"
        source_file.write_text(
            "\n".join(f"line {i}" for i in range(1, 31)) + "\n"
        )

        refs = [{"file": str(source_file), "lines": lines}]
        snippets = mod.read_source_snippets(refs, context_lines=context_lines)

        snippet = snippets[str(source_file)]
        lines_in_snippet = snippet.strip().split("\n")
        assert len(lines_in_snippet) == expected_count

    def test_handles_missing_files(self, mod, tmp_path):
        """Missing files are skipped gracefully."""
        refs = [{"file": str(tmp_path / "nonexistent.py"), "lines": [10]}]
        snippets = mod.read_source_snippets(refs, context_lines=3)
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

        # Without old_side_files, no [pre-change] snippet is produced —
        # same repo, so the gate is the only thing that changed.
        without_flag = mod.read_source_snippets(
            refs, context_lines=1, git_root=str(tmp_path), base_ref=base_ref,
        )
        assert "auth.py" in without_flag
        assert "[pre-change] auth.py" not in without_flag


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
        """Files not in the diff are dropped entirely — a path-traversal
        attempt (`../../.env`) with no in-diff annotation drops the same
        way, since the verdict comes from the annotation, not the path."""
        refs = [{"file": "/etc/hosts", "lines": [1]}]
        annotations = {"/etc/hosts:1": "OUT_OF_SCOPE:file_not_in_diff"}
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

    def test_missing_annotation_treated_as_out_of_scope(self, mod):
        """References without annotations are dropped (fail-closed)."""
        refs = [{"file": "unknown.py", "lines": [1]}]
        result = mod.filter_in_scope_references(refs, {})
        assert result == []


# ===========================================================================
# TestCheckScopeHunkLevel — with mocked diff hunks
# ===========================================================================

class TestCheckScopeHunkLevel:
    """Tests for hunk-level scope classification in check_scope(), driven
    through the public `diff_hunks=` parameter (as
    `test_accepts_pre_parsed_diff_hunks` does) rather than by
    monkeypatching the private `_parse_diff_hunks`.
    """

    @pytest.mark.parametrize("hunks,lines,expected", [
        pytest.param(
            {"src/auth.py": [(10, 20)]}, [100],
            {100: "OUT_OF_SCOPE:not_in_hunk"},
            id="far_from_hunk",
        ),
        pytest.param(
            {"src/auth.py": [(10, 15), (50, 55)]}, [12, 30, 53],
            {12: "IN_SCOPE:in_hunk", 30: "OUT_OF_SCOPE:not_in_hunk",
             53: "IN_SCOPE:in_hunk"},
            id="multiple_hunks",
        ),
        pytest.param(
            {"src/auth.py": [(10, 20)]}, [10, 20],
            {10: "IN_SCOPE:in_hunk", 20: "IN_SCOPE:in_hunk"},
            id="hunk_boundary_exact",
        ),
        pytest.param(
            {"src/auth.py": [(10, 20)]}, [5, 25],
            {5: "IN_SCOPE:near_hunk", 25: "IN_SCOPE:near_hunk"},
            id="proximity_boundary_exact",
        ),
        pytest.param(
            {"src/auth.py": [(10, 20)]}, [4, 26],
            {4: "OUT_OF_SCOPE:not_in_hunk", 26: "OUT_OF_SCOPE:not_in_hunk"},
            id="proximity_boundary_just_outside",
        ),
        pytest.param(
            # Deletion at new-side line 10 → zero-width marker (10, 10).
            {"src/auth.py": [(10, 10)]}, [10, 14, 50],
            {10: "IN_SCOPE:in_hunk", 14: "IN_SCOPE:near_hunk",
             50: "OUT_OF_SCOPE:not_in_hunk"},
            id="deletion_only_file_with_markers",
        ),
        pytest.param(
            # @@ -10,20 +10,3 @@ → old=(10,29), new=(10,12) stored
            # separately; old-side lines 10-29 are in range via (10,29).
            {"src/auth.py": [(10, 29), (10, 12)]}, [10, 15, 25, 29, 50],
            {10: "IN_SCOPE:in_hunk", 15: "IN_SCOPE:in_hunk",
             25: "IN_SCOPE:in_hunk", 29: "IN_SCOPE:in_hunk",
             50: "OUT_OF_SCOPE:not_in_hunk"},
            id="large_deletion_old_side_lines_in_scope",
        ),
    ])
    def test_hunk_level_classification(self, mod, hunks, lines, expected):
        refs = [{"file": "src/auth.py", "lines": lines}]
        result = mod.check_scope(
            refs, ["src/auth.py"], "abc..HEAD", diff_hunks=hunks,
        )
        for line, status in expected.items():
            assert result[f"src/auth.py:{line}"] == status

    def test_file_not_in_diff_with_hunks(self, mod, monkeypatch):
        """File not in changed_files stays OUT_OF_SCOPE regardless of hunks."""
        monkeypatch.setattr(
            mod, "_parse_diff_hunks",
            lambda git_range: ({"src/auth.py": [(10, 20)]}, set())
        )
        refs = [{"file": "src/other.py", "lines": [15]}]
        result = mod.check_scope(refs, ["src/auth.py"], "abc..HEAD")
        assert result["src/other.py:15"] == "OUT_OF_SCOPE:file_not_in_diff"

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
        # Three old-side lines were replaced by five: the old code is gone
        # and a finding about it needs the pre-change snippet.
        assert deletions == {"src/auth.py"}

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
        assert deletions == {"src/a.py"}  # the one old line was replaced

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

    def test_one_for_one_replacement_marks_the_file_for_old_side_reads(self, mod, monkeypatch):
        """A hunk that replaces a line with another (`-5 +5`) contains a
        deleted line even though the net size is unchanged. A finding
        about the replaced code needs the pre-change snippet, so the file
        is marked; a pure insertion (`-9,0 +10,2`) is not."""
        diff_output = (
            "diff --git a/src/auth.py b/src/auth.py\n"
            "--- a/src/auth.py\n"
            "+++ b/src/auth.py\n"
            "@@ -5 +5 @@\n"
            "-old\n+new\n"
            "diff --git a/src/new.py b/src/new.py\n"
            "--- a/src/new.py\n"
            "+++ b/src/new.py\n"
            "@@ -9,0 +10,2 @@\n"
            "+a\n+b\n"
        )
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *a, **kw: type("R", (), {
                "returncode": 0, "stdout": diff_output, "stderr": ""
            })()
        )
        _hunks, deletions = mod._parse_diff_hunks("abc..HEAD")
        assert deletions == {"src/auth.py"}

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
# TestFullScript — subprocess integration tests
# ===========================================================================

class TestFullScript:
    """Integration tests running the complete script via subprocess."""

    def _run(self, *args, cwd=None):
        """Run the script and return the CompletedProcess."""
        cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)

    def test_produces_valid_output_json(self, tmp_path):
        """The context is exactly what the reconciliator reads.

        Every key here has a reader in `agents/review-reconciliator.md`
        or `scripts/review/findings_save.py`. `git_range`, `output_dir`,
        and `output_builder_path` had none: the agent is handed the
        output and plugin scripts directories by the step-8 briefing, and it
        never mentions the range at all. A key nobody reads is a key that
        can go stale without anything noticing.
        """
        review = _make_review_json(
            reviewer="security",
            findings=[_make_finding(file="src/auth.py", line=10)],
        )
        _write_review_json(tmp_path, "security", review)

        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc123..HEAD",
            "--changed-files", "src/auth.py,src/db.py",
            "--change-purpose", "Fix auth bug",
            "--pr-id", "42",
            cwd=tmp_path,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout.strip())["status"] == "ok"

        ctx = _read_reconciliation_context(tmp_path)
        assert set(ctx) == {
            "schema",
            "reviews_by_agent",
            "source_snippets",
            "scope_annotations",
            "changed_files",
            "change_purpose",
            "verify_items",
            "context_items",
            "change_purpose_problems",
            "pr_id",
            "host_context_banner",
            "missing_agents",
            "prefiltered_out_of_scope",
            "orchestrator_notes",
        }
        assert ctx["schema"] == 4
        assert "security-review" in ctx["reviews_by_agent"]
        assert ctx["changed_files"] == ["src/auth.py", "src/db.py"]
        assert ctx["change_purpose"] == "Fix auth bug"
        assert ctx["pr_id"] == "42"
        assert ctx["host_context_banner"] is None
        # No Markdown projection: this artifact has exactly one reader,
        # the reconciliator agent, which reads the JSON directly.
        assert not list(tmp_path.glob("*.md"))

    def test_verify_items_carry_the_checks_that_cite_them(self, tmp_path):
        review = _make_review_json(
            reviewer="security",
            findings=[_make_finding(file="src/auth.py", line=10)],
        )
        review["checks"] = [{
            "id": "c1", "question": "q", "method": "m", "result": "0 hits",
            "source_reviewers": ["security-reviewer"], "verifies": ["V1"],
        }]
        review["meta"]["next_check_number"] = 2
        _write_review_json(tmp_path, "security", review)
        purpose = (
            "## Verify\nV1. Nothing else calls the helper — source: PR description\n"
            "V2. Blocks is unaffected — source: inferred from the diff\n"
            "## Context\nC1. Ships in 10.9 — source: version constant\n"
            "## Author's description (extracted)\nquoted\n"
        )
        result = self._run(
            "--output-dir", str(tmp_path), "--git-range", "abc123..HEAD",
            "--changed-files", "src/auth.py", "--change-purpose", purpose,
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        ctx = _read_reconciliation_context(tmp_path)
        assert ctx["schema"] == 4
        assert ctx["verify_items"] == [
            {"id": "V1", "text": "Nothing else calls the helper", "source": "PR description",
             "carried_over": False,
             "checks": [{"reviewer": "security-review", "id": "c1", "result": "0 hits"}]},
            {"id": "V2", "text": "Blocks is unaffected", "source": "inferred from the diff",
             "carried_over": False, "checks": []},
        ]
        assert ctx["context_items"] == [
            {"id": "C1", "text": "Ships in 10.9", "source": "version constant", "carried_over": False},
        ]
        assert ctx["change_purpose_problems"] == []

    def test_full_host_context_is_carried_for_local_citation_reconciliation(
        self, tmp_path
    ):
        """The full local host map is carried for citation reconciliation,
        and the banner — which can be as large as that map — comes from
        the same on-disk snapshot rather than a second channel."""
        host_context = {
            "version": 1,
            "resolved": [{
                "name": "wordpress",
                "kind": "runtime-host",
                "path": "/repo/wordpress",
                "source": "ecosystem-cache",
                "version": None,
                "notes": {"commit": None},
            }],
            "unresolved": [],
            "banner": {"degraded": True, "message": "x"},
        }

        (tmp_path / "review-context.json").write_text(
            json.dumps({"host_context": host_context})
        )
        result = self._run(
            "--output-dir", str(tmp_path), "--git-range", "abc..HEAD",
            cwd=tmp_path,
        )

        assert result.returncode == 0, result.stderr
        ctx = _read_reconciliation_context(tmp_path)
        assert ctx["host_context"] == host_context
        assert ctx["host_context_banner"] == {"degraded": True, "message": "x"}
        assert ctx["host_context_banner"] == ctx["host_context"]["banner"]

    def test_absent_host_context_is_omitted_defensively(self, tmp_path):
        result = self._run(
            "--output-dir", str(tmp_path), "--git-range", "abc..HEAD",
            cwd=tmp_path,
        )

        assert result.returncode == 0, result.stderr
        assert "host_context" not in _read_reconciliation_context(tmp_path)

    @pytest.mark.parametrize("contents", [
        pytest.param(b"{", id="malformed-json"),
        pytest.param(b'{"host_context": "nope"}', id="non-object-host"),
    ])
    def test_malformed_or_non_object_host_context_is_omitted_defensively(
        self, tmp_path, contents
    ):
        (tmp_path / "review-context.json").write_bytes(contents)
        _write_review_json(tmp_path, "security", _make_review_json(reviewer="security", findings=[]))

        result = self._run(
            "--output-dir", str(tmp_path), "--git-range", "abc..HEAD",
            "--changed-files", "src/auth.py", "--change-purpose", "Fix auth bug", "--pr-id", "42",
            cwd=tmp_path,
        )

        assert result.returncode == 0, result.stderr
        context = _read_reconciliation_context(tmp_path)
        assert "host_context" not in context
        assert context["host_context_banner"] is None
        assert context["changed_files"] == ["src/auth.py"]
        assert context["change_purpose"] == "Fix auth bug"
        assert context["pr_id"] == "42"
        assert list(context["reviews_by_agent"]) == ["security-review"]

    def test_scope_annotations_present(self, tmp_path):
        """Scope annotations are correctly populated with file:line keys."""
        review = _make_review_json(
            findings=[
                _make_finding(file="src/auth.py", line=10),
                _make_finding(file="src/other.py", line=20),
            ],
        )
        _write_review_json(tmp_path, "security", review)

        result = self._run(
            "--output-dir", str(tmp_path),
            "--git-range", "abc..HEAD",
            "--changed-files", "src/auth.py",
            cwd=tmp_path,
        )
        assert result.returncode == 0

        ctx = _read_reconciliation_context(tmp_path)
        # git diff will fail in tmp_path (no real git repo), so files in
        # changed_files fall back to IN_SCOPE:in_hunk
        assert ctx["scope_annotations"]["src/auth.py:10"] == "IN_SCOPE:in_hunk"
        assert ctx["scope_annotations"]["src/other.py:20"] == "OUT_OF_SCOPE:file_not_in_diff"


class TestMissingAgentDetection:
    """Missing-agent detection is a MEASUREMENT, not an agent's arithmetic.

    `schemas/review-output.ts` declares `meta.reconciliation.missing_agents`,
    and a dispatched reviewer that produced no output is exactly the fact a
    review must not quietly lose. While the retired Markdown projection
    rendered it, the subtraction was deterministic; asking the reconciliator
    to redo it from two lists in the JSON would have demoted a machine
    guarantee to agent prose. `compute_missing_agents()` keeps it machine-side
    and the JSON carries the answer.
    """

    @pytest.mark.parametrize("dispatched_stems,reviews_by_agent,expected", [
        pytest.param(
            ["security-review", "performance-review", "a11y-review"],
            {"security-review": {}}, ["a11y-review", "performance-review"],
            id="dispatched_but_silent_agents_are_named",
        ),
        pytest.param(
            ["zz-review", "aa-review", "mm-review"], {},
            ["aa-review", "mm-review", "zz-review"],
            id="result_is_sorted_not_dispatch_ordered",
        ),
        pytest.param(
            ["security-review"], {"security-review": {}}, [],
            id="every_agent_reporting_measures_empty",
        ),
        pytest.param(
            [], {}, [],
            id="empty_dispatch_measures_empty",
        ),
        pytest.param(
            ["security-review"], {"security-review": {}, "rogue-review": {}}, [],
            id="an_unexpected_reporter_is_not_subtracted_from_nothing",
        ),
    ])
    def test_missing_agents(
        self, mod, dispatched_stems, reviews_by_agent, expected
    ):
        assert mod.compute_missing_agents(
            dispatched_stems, reviews_by_agent,
        ) == expected

    def test_unknown_dispatch_is_unmeasured_not_empty(self, mod):
        """`None`, never `[]`. A run with no dispatch plan did not measure
        this population, and "nothing was measured" must never read as
        "nobody was missing" — the same zero-vs-unknown rule
        `unscoped_files` follows."""
        assert mod.compute_missing_agents(None, {"security-review": {}}) is None

    def test_json_carries_the_measurement(self, mod, tmp_path):
        review = _make_review_json(reviewer="security")
        _write_review_json(tmp_path, "security", review)

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--output-dir", str(tmp_path),
             "--git-range", "abc123..HEAD",
             "--changed-files", "src/app.py",
             "--dispatched-agents",
             "security-reviewer,performance-reviewer,a11y-reviewer"],
            capture_output=True, text=True, cwd=tmp_path,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        ctx = _read_reconciliation_context(tmp_path)
        assert ctx["missing_agents"] == ["a11y-review", "performance-review"]
        assert ctx["dispatched_agents"] == [
            "security-review", "performance-review", "a11y-review",
        ]

        # --dispatched-agents '' means 0 agents dispatched, not unknown.
        empty_result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--output-dir", str(tmp_path),
             "--git-range", "abc123..HEAD",
             "--dispatched-agents", ""],
            capture_output=True, text=True, cwd=tmp_path,
        )
        assert empty_result.returncode == 0, f"stderr: {empty_result.stderr}"
        assert _read_reconciliation_context(tmp_path)["dispatched_agents"] == []

    def test_json_carries_null_when_dispatch_is_unknown(self, mod, tmp_path):
        review = _make_review_json(reviewer="security")
        _write_review_json(tmp_path, "security", review)

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--output-dir", str(tmp_path),
             "--git-range", "abc123..HEAD",
             "--changed-files", "src/app.py"],
            capture_output=True, text=True, cwd=tmp_path,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        ctx = _read_reconciliation_context(tmp_path)
        assert ctx["missing_agents"] is None
        assert "dispatched_agents" not in ctx


class TestPrefilterAnnotation:
    """Structurally-certain out-of-scope findings are adjudicated by the
    pipeline, not re-derived by the reconciliator.

    `file_not_in_diff` and `metadata_only` are decidable from the diff
    alone — there is no judgment in them. The retired Markdown projection
    removed such findings before the agent saw them, which was a machine
    guarantee but an invisible one: the drop left no trace anywhere. The
    annotation keeps the guarantee AND the audit trail — the finding stays
    in the record of what its agent said, carrying the machine's verdict on
    it, and the agent's job is to obey a flag rather than judge scope.
    """

    @staticmethod
    def _run(tmp_path, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--output-dir", str(tmp_path),
             "--git-range", "abc123..HEAD", *extra],
            capture_output=True, text=True, cwd=tmp_path,
        )

    def test_out_of_scope_findings_are_annotated_in_place(self, mod, tmp_path):
        """`reviews_by_agent` is the record of what each reviewer said, and
        the reconciliation metrics are counted from it — an out-of-scope
        finding is annotated, not deleted, and a checkable count travels
        beside the annotations: N annotated in, N dropped out."""
        review = _make_review_json(reviewer="security", findings=[
            _make_finding(file="src/untouched.py", line=10, title="A"),
            _make_finding(file="src/other.py", line=1, title="B"),
            _make_finding(file="src/app.py", line=42, title="C"),
        ])
        _write_review_json(tmp_path, "security", review)

        result = self._run(tmp_path, "--changed-files", "src/app.py")
        assert result.returncode == 0, f"stderr: {result.stderr}"

        ctx = _read_reconciliation_context(tmp_path)
        findings = ctx["reviews_by_agent"]["security-review"]["findings"]
        assert len(findings) == 3
        by_title = {i["title"]: i for i in findings}
        assert by_title["A"]["prefiltered"] == "OUT_OF_SCOPE:file_not_in_diff"
        assert "prefiltered" not in by_title["C"]
        assert ctx["prefiltered_out_of_scope"] == {
            "count": 2, "by_agent": {"security-review": 2},
        }

        # A clean run (nothing out of scope) reports a measured zero, not
        # an absent key.
        clean = tmp_path / "clean"
        clean.mkdir()
        _write_review_json(clean, "security", _make_review_json(
            reviewer="security",
            findings=[_make_finding(file="src/app.py", line=42)],
        ))
        clean_result = self._run(clean, "--changed-files", "src/app.py")
        assert clean_result.returncode == 0, f"stderr: {clean_result.stderr}"
        assert _read_reconciliation_context(clean)["prefiltered_out_of_scope"] == {
            "count": 0, "by_agent": {},
        }

    def test_not_in_hunk_is_never_prefiltered(self, mod, tmp_path):
        """The one out-of-scope status that IS a judgment call: agent line
        numbers can be imprecise, so the reconciliator checks the snippet
        before dropping. Annotating it would turn a hedge into a verdict."""
        annotations = {
            "src/app.py:42": "OUT_OF_SCOPE:not_in_hunk",
            "src/gone.py:1": "OUT_OF_SCOPE:file_not_in_diff",
            "src/meta.py:3": "OUT_OF_SCOPE:metadata_only",
            "src/app.py:9": "IN_SCOPE:in_hunk",
        }
        findings = {"security-review": {"findings": [
            {"file": "src/app.py", "line": 42},
            {"file": "src/gone.py", "line": 1},
            {"file": "src/meta.py", "line": 3},
            {"file": "src/app.py", "line": 9},
        ]}}

        summary = mod.annotate_prefiltered_findings(findings, annotations)

        marks = [i.get("prefiltered") for i in findings["security-review"]["findings"]]
        assert marks == [
            None, "OUT_OF_SCOPE:file_not_in_diff",
            "OUT_OF_SCOPE:metadata_only", None,
        ]
        assert summary == {"count": 2, "by_agent": {"security-review": 2}}

    def test_a_finding_with_no_line_is_left_alone(self, mod):
        """File-scoped findings carry `line: null` and have no annotation
        key; scope for them is "the file is in changed_files", which this
        function does not measure."""
        findings = {"a-review": {"findings": [{"file": "src/x.py", "line": None}]}}
        summary = mod.annotate_prefiltered_findings(findings, {})
        assert "prefiltered" not in findings["a-review"]["findings"][0]
        assert summary == {"count": 0, "by_agent": {}}

    def test_a_stale_annotation_from_reused_input_is_cleared(self, mod):
        """The function OWNS the key: an in-scope finding that arrives
        carrying a `prefiltered` marker (hand-edited input, a reused dict)
        must not keep it — a stale marker silently deletes a real finding."""
        findings = {"a-review": {"findings": [
            {"file": "src/app.py", "line": 9, "prefiltered": "OUT_OF_SCOPE:metadata_only"},
        ]}}
        summary = mod.annotate_prefiltered_findings(
            findings, {"src/app.py:9": "IN_SCOPE:in_hunk"}
        )
        assert "prefiltered" not in findings["a-review"]["findings"][0]
        assert summary == {"count": 0, "by_agent": {}}

    def test_malformed_shapes_do_not_raise(self, mod):
        findings = {
            "a-review": {"findings": "not-a-list"},
            "b-review": "not-a-dict",
            "c-review": {"findings": [None, 7, {"file": "src/gone.py", "line": 1}]},
        }
        summary = mod.annotate_prefiltered_findings(
            findings, {"src/gone.py:1": "OUT_OF_SCOPE:file_not_in_diff"}
        )
        assert summary == {"count": 1, "by_agent": {"c-review": 1}}


class TestReviewStem:
    """Review files are named by TERMINAL-suffix derivation only — a
    blanket replace corrupts repo reviewer ids carrying "reviewer"
    mid-string (e.g. "api-reviewer-v2") and silently excludes their valid
    blocking output."""

    def test_mid_string_reviewer_id_output_is_loaded(self, mod, tmp_path):
        review = _make_review_json(
            reviewer="repo-api-reviewer-v2", findings=[]
        )
        _write_review_json(tmp_path, "repo-api-reviewer-v2", review)
        findings = mod.load_agent_reviews(
            str(tmp_path),
            dispatched_agents=["repo-api-reviewer-v2-reviewer"],
        )
        assert "repo-api-reviewer-v2-review" in findings


class TestRegisteredNotesSurviveARebuild:
    """Step 8 rebuilds the context every time it is entered — a same-run
    retry after an interrupted reconciliator dispatch included — and the
    orchestrator registers its claims between the build and that dispatch.
    A rebuild that reset them would release the save gate's requirement
    that each be answered and hand the next claim an id already spent.
    """

    def _build(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path),
             "--git-range", "abc123..HEAD", "--changed-files", "",
             "--dispatched-agents", ""],
            capture_output=True, text=True, cwd=tmp_path,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        return result

    def _context(self, tmp_path):
        return json.loads(
            artifact_path(str(tmp_path), "reconciliation_context").read_text()
        )

    def test_a_rebuild_keeps_the_claims_and_the_next_id(self, tmp_path):
        self._build(tmp_path)
        assert self._context(tmp_path)["orchestrator_notes"] == []

        first = add_note(str(tmp_path), "Findings f1 and f3 describe one concern.")
        assert first["id"] == "n1"

        self._build(tmp_path)

        assert self._context(tmp_path)["orchestrator_notes"] == [first]
        assert add_note(str(tmp_path), "A second, different claim.")["id"] == "n2"

    def test_a_malformed_collection_fails_the_rebuild_rather_than_dropping(
        self, tmp_path
    ):
        """Only the validating CLI writes notes, so a collection failing
        that grammar is state no writer can produce. Carrying it forward
        silently is the loss this preservation exists to prevent."""
        self._build(tmp_path)
        add_note(str(tmp_path), "A claim worth keeping.")
        path = artifact_path(str(tmp_path), "reconciliation_context")
        context = json.loads(path.read_text())
        context["orchestrator_notes"][0]["id"] = "n7"
        path.write_text(json.dumps(context))

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path),
             "--git-range", "abc123..HEAD", "--changed-files", "",
             "--dispatched-agents", ""],
            capture_output=True, text=True, cwd=tmp_path,
        )

        assert result.returncode != 0
        assert "orchestrator_notes[0].id must be n1" in result.stderr
        assert json.loads(path.read_text())["orchestrator_notes"][0]["id"] == "n7"

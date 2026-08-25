"""Tests for review/agent/bootstrap.py — unit tests (direct function imports)."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Path setup: allow importing from scripts/
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent.parent.parent  # agent/ -> review/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
BOOTSTRAP_SCRIPT = SCRIPTS_DIR / "review" / "agent" / "bootstrap.py"

sys.path.insert(0, str(SCRIPTS_DIR))

from review.agent.output import ReviewOutputBuilder

# Import functions under test via importlib (file-based loading)
import importlib

_spec = importlib.util.spec_from_file_location("bootstrap_reviewer", str(BOOTSTRAP_SCRIPT))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

extract_protocol_sections = _mod.extract_protocol_sections
build_output = _mod.build_output
build_error_output = _mod.build_error_output
load_pr_intent = _mod.load_pr_intent
load_pr_number_from_context = _mod.load_pr_number_from_context
load_pr_size_from_context = _mod.load_pr_size_from_context
load_change_purpose = _mod.load_change_purpose
load_additional_instructions = _mod.load_additional_instructions
compute_review_budget = _mod.compute_review_budget
budget_was_capped = _mod.budget_was_capped
extract_scope_files = _mod.extract_scope_files
extract_not_diffed_files = _mod.extract_not_diffed_files
extract_list_only_files = _mod.extract_list_only_files
extract_scope_line_count = _mod.extract_scope_line_count
load_scope_facts = _mod.load_scope_facts
extract_file_diffstat = _mod.extract_file_diffstat
order_by_diffstat_largest_first = _mod.order_by_diffstat_largest_first
resolve_overall_status = _mod.resolve_overall_status
REVIEWER_PROTOCOL_SKIP_SECTIONS = _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS


# =============================================================================
# Unit Tests — direct import
# =============================================================================


class TestResolveReviewerIdentity:
    """Registry and adapter-ref invocations preserve their current identities."""

    def test_registry_mode_uses_registry_agent_name(self):
        args = SimpleNamespace(
            agent="security-reviewer",
            repo_agent_ref=None,
            instance_name=None,
            adapter_label=None,
            execution="inline",
        )

        assert _mod.resolve_reviewer_identity(args) == (
            "security-reviewer",
            "security-reviewer",
            None,
            None,
            None,
        )

    def test_ref_mode_uses_instance_name(self):
        args = SimpleNamespace(
            agent="repo-reviewer-adapter",
            repo_agent_ref=".pirategoat/reviewers/renewals.md",
            instance_name="repo-renewals-reviewer",
            adapter_label="Renewals",
            execution="inline",
        )

        assert _mod.resolve_reviewer_identity(args) == (
            "repo-reviewer-adapter",
            "repo-renewals-reviewer",
            "Renewals",
            ".pirategoat/reviewers/renewals.md",
            None,
        )

    @pytest.mark.parametrize(
        ("instance_name", "execution", "expected_error"),
        [
            (None, "inline", "Adapter ref-mode requires --instance-name."),
            (
                "repo-renewals-reviewer",
                "isolated",
                "Isolated execution is not implemented.",
            ),
        ],
    )
    def test_inconsistent_ref_mode_flags_return_printable_error(
        self, instance_name, execution, expected_error
    ):
        args = SimpleNamespace(
            agent="repo-reviewer-adapter",
            repo_agent_ref=".pirategoat/reviewers/renewals.md",
            instance_name=instance_name,
            adapter_label="Renewals",
            execution=execution,
        )

        (
            agent_name,
            effective_agent_name,
            adapter_label,
            repo_agent_ref,
            error,
        ) = _mod.resolve_reviewer_identity(args)

        assert agent_name == "repo-reviewer-adapter"
        assert effective_agent_name is None
        assert adapter_label == "Renewals"
        assert repo_agent_ref == ".pirategoat/reviewers/renewals.md"
        assert expected_error in error
        assert "STATUS: ERROR" in error


class TestPersistReviewAccountingInput:
    """The writer publishes the exact schema-3 accounting authority."""

    def test_writes_only_authoritative_deferred_files(self, tmp_path):
        _mod.persist_review_accounting_input(
            str(tmp_path),
            "repo-renewals-reviewer",
            ["src/deferred.php"],
            review_budget=80,
            in_scope_review_file_count=12,
            inline_diff_file_count=11,
        )

        payload = json.loads(
            (tmp_path / "repo-renewals-review-accounting-input.json").read_text()
        )
        assert payload == {
            "schema": 3,
            "agent_name": "repo-renewals-reviewer",
            "reviewer": "repo-renewals",
            "review_claimable_files": ["src/deferred.php"],
            "review_budget": 80,
            "in_scope_review_file_count": 12,
            "inline_diff_file_count": 11,
        }

    def test_writes_empty_authoritative_set(self, tmp_path):
        _mod.persist_review_accounting_input(
            str(tmp_path), "security-reviewer", [],
            review_budget=40,
            in_scope_review_file_count=5, inline_diff_file_count=5,
        )

        payload = json.loads(
            (tmp_path / "security-review-accounting-input.json").read_text()
        )
        assert payload == {
            "schema": 3,
            "agent_name": "security-reviewer",
            "reviewer": "security",
            "review_claimable_files": [],
            "review_budget": 40,
            "in_scope_review_file_count": 5,
            "inline_diff_file_count": 5,
        }

    def test_write_errors_are_not_silenced(self, tmp_path):
        output_file = tmp_path / "not-a-directory"
        output_file.write_text("occupied")

        with pytest.raises(OSError):
            _mod.persist_review_accounting_input(
                str(output_file), "security-reviewer", ["src/deferred.php"],
                review_budget=80,
                in_scope_review_file_count=1, inline_diff_file_count=0,
            )

    def test_dedupes_deferred_files_order_preserving(self, tmp_path):
        """A multi-domain agent's secondary-domain scope render can repeat
        a file already budget-exceeded in the primary domain's sidecar —
        load_scope_facts() concatenates every summary's
        budget_exceeded_files without deduping. persist_review_accounting_input
        must not publish that duplicate: it inflates len(review_claimable_files),
        the total manifest_sections.build_coverage_manifest reconciles
        the agent's derived positive-claim/gap populations against."""
        _mod.persist_review_accounting_input(
            str(tmp_path),
            "security-reviewer",
            ["src/a.php", "src/b.php", "src/a.php"],
            review_budget=80,
            in_scope_review_file_count=4, inline_diff_file_count=2,
        )

        payload = json.loads(
            (tmp_path / "security-review-accounting-input.json").read_text()
        )
        assert payload == {
            "schema": 3,
            "agent_name": "security-reviewer",
            "reviewer": "security",
            "review_claimable_files": ["src/a.php", "src/b.php"],
            "review_budget": 80,
            "in_scope_review_file_count": 4,
            "inline_diff_file_count": 2,
        }

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"review_budget": 40, "in_scope_review_file_count": None, "inline_diff_file_count": 0},
            {"review_budget": 40, "in_scope_review_file_count": 1, "inline_diff_file_count": None},
            {"review_budget": 40, "in_scope_review_file_count": 1, "inline_diff_file_count": 1},
        ],
    )
    def test_rejects_incomplete_or_incoherent_payloads(self, tmp_path, kwargs):
        with pytest.raises(ValueError):
            _mod.persist_review_accounting_input(
                str(tmp_path), "security-reviewer", ["src/a.php"], **kwargs
            )


class TestPartitionScopePaths:
    """Scope populations are disjoint, ordered sets with fixed precedence."""

    def test_partitions_duplicates_and_cross_population_overlap(self):
        inline, deferred, list_only = _mod.partition_scope_paths(
            ["src/inline-a.py", "src/shared.py", "src/inline-a.py"],
            [
                "src/deferred-a.py",
                "src/shared.py",
                "src/deferred-a.py",
                "src/deferred-b.py",
            ],
            [
                "package-lock.json",
                "src/deferred-b.py",
                "package-lock.json",
                "src/shared.py",
                "generated/api.json",
            ],
        )

        assert inline == ["src/inline-a.py", "src/shared.py"]
        assert deferred == ["src/deferred-a.py", "src/deferred-b.py"]
        assert list_only == ["package-lock.json", "generated/api.json"]

    def test_save_echo_uses_reachable_progress_while_telemetry_keeps_list_only(
        self, tmp_path, monkeypatch, capsys
    ):
        """Cross-domain repeats cannot inflate progress, while list-only
        paths remain descriptive telemetry scope outside the claimable queue.
        """
        monkeypatch.delenv("PIRATEGOAT_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("PIRATEGOAT_REVIEWER_NAME", raising=False)
        monkeypatch.setattr(
            _mod,
            "load_scope_facts",
            lambda _paths: {
                "files": [
                    "src/inline.py",
                    "src/shared.py",
                    "src/inline.py",
                    "src/secondary.py",
                    "src/shared.py",
                ],
                "review_claimable": [
                    "src/shared.py",
                    "src/deferred-a.py",
                    "src/deferred-a.py",
                    "src/secondary.py",
                    "src/deferred-b.py",
                ],
                "list_only": [
                    "package-lock.json",
                    "src/deferred-b.py",
                    "package-lock.json",
                    "generated/api.json",
                ],
                "stat_lines": 100,
            },
        )
        scope_output = (
            "STATUS: OK\n"
            "=== FILES ===\n"
            "src/inline.py  (+10 -0)\n"
            "src/shared.py  (+10 -0)\n"
            "src/secondary.py  (+10 -0)\n"
            "=== NOT DIFFED (budget exceeded, 2 files) ===\n"
            "src/deferred-a.py  (+10 -0)\n"
            "src/deferred-b.py  (+20 -0)\n"
            "=== CHANGED (no diff — 2 lock/generated files) ===\n"
            "package-lock.json  (+100 -100)\n"
            "generated/api.json  (+100 -100)\n"
        )
        monkeypatch.setattr(
            _mod,
            "run_scope_discovery",
            lambda *_args, **_kwargs: (0, scope_output),
        )

        telemetry_starts = []

        class CapturingTelemetry:
            def __init__(self, _output_dir):
                pass

            def log_agent_start(self, **kwargs):
                telemetry_starts.append(kwargs)

        monkeypatch.setattr(_mod, "ReviewTelemetry", CapturingTelemetry)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "bootstrap.py",
                "--agent",
                "security-reviewer",
                "--range",
                "base..head",
                "--output-dir",
                str(tmp_path),
            ],
        )
        with pytest.raises(SystemExit, match="0"):
            _mod.main()
        capsys.readouterr()

        builder = ReviewOutputBuilder("123", "security")
        builder.claim_files_reviewed("src/deferred-b.py")
        builder.save(str(tmp_path))

        payload = json.loads(
            (tmp_path / "security-review-accounting-input.json").read_text()
        )
        covered = payload["inline_diff_file_count"] + len(builder.reviewed_file_claims)
        assert telemetry_starts[0]["scope_paths"] == [
            "src/inline.py",
            "src/shared.py",
            "src/secondary.py",
            "src/deferred-b.py",
            "src/deferred-a.py",
            "package-lock.json",
            "generated/api.json",
        ]
        assert payload["review_claimable_files"] == [
            "src/deferred-b.py",
            "src/deferred-a.py",
        ]
        assert payload["inline_diff_file_count"] == 3
        assert payload["in_scope_review_file_count"] == 5
        assert 0 <= covered <= payload["in_scope_review_file_count"]
        assert (
            "PROGRESS: accounted for 4 of 5 in-scope files."
            in capsys.readouterr().out
        )

    def test_main_reports_sidecar_publication_failure_as_structured_error(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(_mod, "find_plugin_root", lambda: str(PLUGIN_ROOT))
        monkeypatch.setattr(_mod, "read_file", lambda _path: "# rules")
        monkeypatch.setattr(
            _mod, "run_scope_discovery", lambda *_args, **_kwargs: (0, "STATUS: OK\n=== FILES ===\nsrc/a.py  (+1 -0)\n")
        )
        monkeypatch.setattr(
            _mod,
            "persist_review_accounting_input",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["bootstrap.py", "--agent", "security-reviewer", "--output-dir", str(tmp_path)],
        )

        with pytest.raises(SystemExit, match="1"):
            _mod.main()

        output = capsys.readouterr().out
        assert "STATUS: ERROR" in output
        assert "Could not publish authoritative review accounting: disk full" in output


class TestExtractFileDiffstat:
    """Per-file size, parsed from any stat-shaped scope.py line."""

    def test_extracts_stats_from_files_and_not_diffed_sections(self):
        scope_output = (
            "=== FILES ===\n"
            "src/small.py  (+2 -1)\n"
            "=== NOT DIFFED (budget exceeded, 1 files) ===\n"
            "src/huge.py  (+400 -50)\n"
        )
        assert extract_file_diffstat(scope_output) == {
            "src/small.py": 3,
            "src/huge.py": 450,
        }

    def test_empty_scope_output_returns_empty_dict(self):
        assert extract_file_diffstat("") == {}

    def test_lines_without_the_stat_shape_are_ignored(self):
        scope_output = (
            "=== NOT DIFFED (budget exceeded, 1 files) ===\n"
            "These files ARE IN YOUR SCOPE\n"
            "src/a.py  (+5 -0)\n"
        )
        assert extract_file_diffstat(scope_output) == {"src/a.py": 5}


class TestOrderByDiffstatLargestFirst:
    """The deferred-files sidecar's ordering contract, in isolation."""

    def test_sorts_descending_by_total_lines(self):
        result = order_by_diffstat_largest_first(
            ["a.py", "b.py", "c.py"],
            {"a.py": 10, "b.py": 500, "c.py": 100},
        )
        assert result == ["b.py", "c.py", "a.py"]

    def test_unknown_paths_sort_as_zero_and_never_get_excluded(self):
        result = order_by_diffstat_largest_first(
            ["known.py", "unknown.py"], {"known.py": 5}
        )
        assert result == ["known.py", "unknown.py"]

    def test_ties_keep_relative_input_order(self):
        """Stable: two equal-size (or both-unknown) paths do not get
        reordered relative to each other."""
        result = order_by_diffstat_largest_first(
            ["first.py", "second.py"], {}
        )
        assert result == ["first.py", "second.py"]

    def test_priority_tier_order_is_overridden_by_size(self):
        """The regression this exists for: load_scope_facts() hands back
        budget_exceeded_files in priority-tier order (a small
        production-tier file ahead of a much larger ordinary file) — this
        function must re-sort by size regardless of the input order."""
        result = order_by_diffstat_largest_first(
            ["small-priority.py", "huge-ordinary.py"],
            {"small-priority.py": 20, "huge-ordinary.py": 900},
        )
        assert result == ["huge-ordinary.py", "small-priority.py"]


class TestExtractProtocolSections:
    """Skip-list extraction on synthetic markdown."""

    SAMPLE_PROTOCOL = """\
# Shared Reviewer Protocol

## Step 0: Locate Plugin Root

Setup instructions that should be skipped.

## Scope Discovery

More setup to skip.

### Subsection of Scope

This subsection should also be skipped.

## RULE: Reviewing vs Exploring

Important rule that should be included.

## Output Directory

Output dir instructions to skip.

## ReviewOutputBuilder API

API docs to skip.

## File-Based Output

File output instructions to skip.

## Project-Specific Knowledge

Should be included.

## Severity Calibration

Should be included.

## New Future Section

This section was added later and should be included automatically.
"""

    def test_skipped_sections_absent(self):
        result = extract_protocol_sections(
            self.SAMPLE_PROTOCOL, REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert "## Step 0" not in result
        assert "## Scope Discovery" not in result
        assert "Subsection of Scope" not in result
        assert "## Output Directory" not in result
        assert "## ReviewOutputBuilder API" not in result
        assert "## File-Based Output" not in result

    def test_non_skipped_sections_present(self):
        result = extract_protocol_sections(
            self.SAMPLE_PROTOCOL, REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert "## RULE: Reviewing vs Exploring" in result
        assert "Important rule that should be included" in result
        assert "## Project-Specific Knowledge" in result
        assert "## Severity Calibration" in result

    def test_new_section_auto_included(self):
        """New sections added to protocol are included by default (skip-list resilience)."""
        result = extract_protocol_sections(
            self.SAMPLE_PROTOCOL, REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert "## New Future Section" in result
        assert "added later and should be included automatically" in result

    def test_code_fences_not_parsed_as_headings(self):
        """Code fences with # characters should not be parsed as headings."""
        content = """\
# Title

## Included Section

```bash
# This is a comment, not a heading
## Also a comment
echo "hello"
```

After the code fence.

## Step 0: Setup

Should be skipped.
"""
        result = extract_protocol_sections(content, ["## Step 0"])
        assert "# This is a comment" in result
        assert "## Also a comment" in result
        assert 'echo "hello"' in result
        assert "After the code fence." in result
        assert "## Step 0" not in result

    def test_level1_title_stripped(self):
        result = extract_protocol_sections(
            self.SAMPLE_PROTOCOL, REVIEWER_PROTOCOL_SKIP_SECTIONS
        )
        assert "# Shared Reviewer Protocol" not in result


class TestLoadPrIntent:
    """PR intent loading from review-context.json."""

    def test_returns_none_when_no_file(self, tmp_path):
        assert load_pr_intent(str(tmp_path)) is None

    def test_returns_none_when_empty_json(self, tmp_path):
        (tmp_path / "review-context.json").write_text("{}")
        assert load_pr_intent(str(tmp_path)) is None

    def test_returns_none_when_no_title(self, tmp_path):
        ctx = {"pr": {"body": "Some body", "author": "dev"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        assert load_pr_intent(str(tmp_path)) is None

    def test_returns_intent_with_title(self, tmp_path):
        ctx = {"pr": {"title": "Fix rounding in refunds"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert intent is not None
        assert "Fix rounding in refunds" in intent

    def test_includes_author(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing", "author": "alice"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert "alice" in intent

    def test_includes_body(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing", "body": "Refund totals were off"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert "Refund totals were off" in intent

    def test_includes_linked_issues(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing"}, "linked_issues": ["WOOPLUG-123"]}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert "WOOPLUG-123" in intent

    def test_truncates_long_body(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing", "body": "x" * 1000}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert len(intent) < 700  # 500 char truncation + title + overhead
        assert "..." in intent

    def test_handles_malformed_json(self, tmp_path):
        (tmp_path / "review-context.json").write_text("not json")
        assert load_pr_intent(str(tmp_path)) is None


class TestChangePurpose:
    """load_change_purpose() reads change-purpose.md from output directory."""

    def test_returns_none_when_missing(self, tmp_path):
        assert load_change_purpose(str(tmp_path)) is None

    def test_returns_none_when_empty(self, tmp_path):
        (tmp_path / "change-purpose.md").write_text("")
        assert load_change_purpose(str(tmp_path)) is None

    def test_returns_content_when_present(self, tmp_path):
        (tmp_path / "change-purpose.md").write_text("Adds retry logic to payments.")
        result = load_change_purpose(str(tmp_path))
        assert result == "Adds retry logic to payments."

    def test_strips_whitespace(self, tmp_path):
        (tmp_path / "change-purpose.md").write_text("  Content with spaces.  \n\n")
        result = load_change_purpose(str(tmp_path))
        assert result == "Content with spaces."


class TestChangePurposeInjection:
    """change-purpose.md content is injected as REVIEW FOCUS in bootstrap output."""

    def test_review_focus_present_when_provided(self):
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            review_claimable_count=0,
            has_php=False,
            change_purpose="Adds retry logic to the payment gateway.",
        )
        assert "=== REVIEW FOCUS (pipeline synthesis) ===" in output
        assert "retry logic" in output

class TestResolveOverallStatus:
    """Defense-in-depth: primary NO_DOMAIN_FILES + secondary content → scoped OK."""

    def test_normal_ok_passes_through(self):
        status, secondary_only = resolve_overall_status("security", "OK", False)
        assert status == "OK"
        assert secondary_only is False

    def test_no_domain_files_without_secondary_stays(self):
        """No primary files and no secondary content → still NO_DOMAIN_FILES (exit)."""
        status, secondary_only = resolve_overall_status("security", "NO_DOMAIN_FILES", False)
        assert status == "NO_DOMAIN_FILES"
        assert secondary_only is False

    def test_no_domain_files_with_secondary_flips_to_scoped_ok(self):
        """The masking fix: secondary files exist → review them, flag secondary_only."""
        status, secondary_only = resolve_overall_status("security", "NO_DOMAIN_FILES", True)
        assert status == "OK"
        assert secondary_only is True

    def test_null_domain_is_ok(self):
        status, secondary_only = resolve_overall_status(None, "OK", False)
        assert status == "OK"
        assert secondary_only is False

    def test_error_status_not_overridden(self):
        status, secondary_only = resolve_overall_status("security", "ERROR", True)
        assert status == "ERROR"
        assert secondary_only is False


class TestCoverageNoteInjection:
    """A coverage note forces honest verdict scoping when only secondary files match."""

    def test_coverage_note_rendered_when_provided(self):
        note = (
            "PRIMARY DOMAIN (security) matched 0 changed files. You are reviewing "
            "ONLY secondary-domain files (config-ops)."
        )
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            review_claimable_count=0,
            has_php=False,
            coverage_note=note,
        )
        assert "=== COVERAGE NOTE ===" in output
        assert "matched 0 changed files" in output
        assert "config-ops" in output

    def test_no_coverage_note_when_absent(self):
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            review_claimable_count=0,
            has_php=False,
        )
        assert "=== COVERAGE NOTE ===" not in output


class TestLoadPrNumberFromContext:
    """PR number loading from review-context.json."""

    def test_pr_number_from_review_context(self, tmp_path):
        """Bootstrap should read PR number from review-context.json when scope doesn't provide it."""
        ctx = {"pr": {"number": 11453}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        result = load_pr_number_from_context(str(tmp_path))
        assert result == "11453"

    def test_pr_number_from_review_context_missing(self, tmp_path):
        """Should return None when review-context.json doesn't exist."""
        result = load_pr_number_from_context(str(tmp_path))
        assert result is None

    def test_pr_number_from_review_context_no_pr_key(self, tmp_path):
        """Should return None when pr.number is missing from context."""
        ctx = {"git": {"base_ref": "main"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        result = load_pr_number_from_context(str(tmp_path))
        assert result is None


class TestLoadPrSizeFromContext:
    """PR size loading from review-context.json for budget computation."""

    def test_returns_pr_size(self, tmp_path):
        ctx = {"pr_size": {"lines": 130, "files": 8, "category": "small"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        result = load_pr_size_from_context(str(tmp_path))
        assert result == {"lines": 130, "files": 8, "category": "small"}

    def test_returns_none_when_missing(self, tmp_path):
        result = load_pr_size_from_context(str(tmp_path))
        assert result is None

    def test_returns_none_when_no_pr_size_key(self, tmp_path):
        ctx = {"pr": {"number": 42}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        result = load_pr_size_from_context(str(tmp_path))
        assert result is None

    def test_returns_none_when_lines_missing(self, tmp_path):
        ctx = {"pr_size": {"category": "small"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        result = load_pr_size_from_context(str(tmp_path))
        assert result is None


class TestComputeReviewBudget:
    """Scope-proportionate review budget computation."""

    def test_small_pr(self):
        """Small PRs should get a tight budget."""
        budget = compute_review_budget(changed_lines=130, file_count=8)
        assert 20 <= budget <= 35

    def test_large_pr(self):
        """Large PRs should get a generous budget."""
        budget = compute_review_budget(changed_lines=800, file_count=30)
        assert 50 <= budget <= 80

    def test_cap(self):
        """Budget should cap at a maximum regardless of PR size."""
        budget = compute_review_budget(changed_lines=5000, file_count=100)
        assert budget <= 80

    def test_minimum(self):
        """Even tiny PRs should get a minimum viable budget."""
        budget = compute_review_budget(changed_lines=5, file_count=1)
        assert budget >= 15

    def test_scope_lines_preferred_over_pr_lines(self):
        """Budget should scale with scope-level lines, not PR-level."""
        # Scope has 50 lines → budget should be 15 + 5 = 20
        # PR has 2000 lines → if PR-level were used, budget would be 80 (cap)
        scope_budget = compute_review_budget(changed_lines=50, file_count=3)
        pr_budget = compute_review_budget(changed_lines=2000, file_count=50)
        assert scope_budget < 30  # small scope → small budget
        assert pr_budget == 80    # large PR → cap
        assert scope_budget < pr_budget  # scope budget must be smaller

    def test_zero_scope_lines_gets_minimum(self):
        """Agents with zero diff lines still get minimum budget."""
        budget = compute_review_budget(changed_lines=0, file_count=0)
        assert budget == 15


class TestBudgetWasCapped:
    """Cap detection feeds the honest capped-budget briefing text."""

    def test_below_cap_not_capped(self):
        assert budget_was_capped(changed_lines=130) is False

    def test_at_formula_boundary_not_capped(self):
        # 15 + 650//10 = 80 exactly — reaches the cap without exceeding it
        assert budget_was_capped(changed_lines=650) is False

    def test_above_cap_capped(self):
        assert budget_was_capped(changed_lines=52879) is True


class TestBudgetBriefingText:
    """The budget section must be honest about capping and push spend-down."""

    def _output(self, tmp_path, scope_output="scope", budget=80, capped=False,
                review_claimable_count=0):
        return build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output=scope_output,
            exploration_scope=None,
            output_dir=str(tmp_path),
            pr_number="1",
            reviewer_name="security",
            review_claimable_count=review_claimable_count,
            has_php=False,
            review_budget=budget,
            budget_capped=capped,
        )

    def test_uncapped_budget_claims_calibration(self, tmp_path):
        output = self._output(tmp_path, budget=40, capped=False)
        assert "Calibrated to YOUR scope." in output

    def test_capped_budget_does_not_claim_calibration(self, tmp_path):
        output = self._output(tmp_path, budget=80, capped=True)
        assert "Calibrated to YOUR scope." not in output
        assert "effort floor" in output

    def test_not_diffed_scope_gets_spend_down_instruction(self, tmp_path):
        # The header text ("258 files") is deliberately NOT what the count is
        # sourced from anymore — review_claimable_count is a fact passed by the
        # caller (main() derives it from scope_facts), independent of how
        # scope.py renders its section header. This scope text is present
        # only to prove the header is inert for this decision.
        scope = (
            "=== FILES ===\n"
            "src/a.ts  (+10 -2)\n"
            "\n"
            "=== NOT DIFFED (budget exceeded, 258 files) ===\n"
            "  src/big.ts  (+862 -0)\n"
        )
        output = self._output(tmp_path, scope_output=scope, budget=80, capped=True,
                               review_claimable_count=258)
        assert "258 in-scope files" in output
        assert "coverage gap, not efficiency" in output

    def test_fully_diffed_scope_has_no_spend_down_instruction(self, tmp_path):
        output = self._output(tmp_path, scope_output="=== FILES ===\nsrc/a.ts  (+10 -2)\n",
                              budget=40, capped=False)
        assert "coverage gap, not efficiency" not in output


class TestBudgetOverride:
    """Agent-level budget override from registry."""

    def test_override_replaces_computed_budget(self):
        """When budget_override is set, it replaces the scope-computed budget."""
        # Verify the override exists in the registry
        with open(str(SCRIPTS_DIR / "review" / "agent_registry.json")) as f:
            registry = json.load(f)
        agents = registry.get("agents", registry)
        assert agents["history-insights-reviewer"]["budget_override"] == 45
        # Verify computed budget would be different (higher) without the override
        scope_budget = compute_review_budget(changed_lines=500, file_count=5)
        assert scope_budget > 45, "Override should be lower than computed scope budget"


class TestBuildErrorOutput:
    """Error output format."""

    def test_error_structure(self):
        result = build_error_output("security-reviewer", "Something went wrong")
        assert "=== BOOTSTRAP: security-reviewer ===" in result
        assert "STATUS: ERROR" in result
        assert "ERROR: Something went wrong" in result
        assert "ACTION: Report this error" in result

class TestExtractScopeMultipleBlocks:
    """extract_scope_files and extract_scope_line_count must accumulate across all === FILES === blocks."""

    MULTI_BLOCK_SCOPE = (
        "=== FILES ===\n"
        "includes/class-account.php  (+10 -5)\n"
        "includes/class-service.php  (+3 -2)\n"
        "=== DIFFS ===\n"
        "some diff content\n"
        "=== SECONDARY SCOPE: config-ops ===\n"
        "=== FILES ===\n"
        "config/settings.php  (+7 -1)\n"
        "=== DIFFS ===\n"
        "more diff content\n"
    )

    def test_extract_files_across_blocks(self):
        files = extract_scope_files(self.MULTI_BLOCK_SCOPE)
        assert len(files) == 3
        assert "includes/class-account.php" in files
        assert "config/settings.php" in files

    def test_extract_line_count_across_blocks(self):
        total = extract_scope_line_count(self.MULTI_BLOCK_SCOPE)
        # (10+5) + (3+2) + (7+1) = 28
        assert total == 28

    def test_single_block_still_works(self):
        single = "=== FILES ===\nfoo.php  (+5 -3)\n=== DIFFS ===\n"
        files = extract_scope_files(single)
        assert files == ["foo.php"]
        assert extract_scope_line_count(single) == 8

    def test_line_count_includes_not_diffed_workload(self):
        """NOT DIFFED files are deferred in-scope work: their lines must size
        the budget, or the largest reviews get the smallest targets."""
        scope = (
            "=== FILES ===\n"
            "src/inline.py  (+400 -100)\n"
            "=== NOT DIFFED (budget exceeded, 2 files) ===\n"
            "These files ARE IN YOUR SCOPE — their diffs were withheld only to fit\n"
            "the context budget.\n"
            "  src/deferred-large.py  (+700 -100)\n"
            "  src/deferred-small.py  (+80 -20)\n"
            "=== DIFFS ===\n"
            "diff content\n"
        )
        # 500 inline + 800 + 100 deferred = 1400
        assert extract_scope_line_count(scope) == 1400
        # Deferred lines must not enter the FILES-only file list.
        assert extract_scope_files(scope) == ["src/inline.py"]

    def test_extract_not_diffed_files_skips_section_prose(self):
        """Deferred paths come only from stats-shaped lines — the NOT DIFFED
        section's instruction prose must never be parsed as file paths."""
        scope = (
            "=== FILES ===\n"
            "src/inline.py  (+400 -100)\n"
            "=== NOT DIFFED (budget exceeded, 2 files) ===\n"
            "These files ARE IN YOUR SCOPE — their diffs were withheld only to fit\n"
            "the context budget. This list is your remaining work queue, largest\n"
            "first: review with 'git diff base..head -- <file>' while tool budget\n"
            "remains. Claim every NOT DIFFED file you actually read; the builder "
            "derives the rest as unclaimed review files.\n"
            "  src/deferred-large.py  (+700 -100)\n"
            "  src/deferred-small.py  (+80 -20)\n"
            "=== DIFFS ===\n"
            "diff content\n"
        )
        assert extract_not_diffed_files(scope) == [
            "src/deferred-large.py",
            "src/deferred-small.py",
        ]

    def test_extract_not_diffed_files_accumulates_across_secondary_scopes(self):
        scope = (
            "=== NOT DIFFED (budget exceeded, 1 files) ===\n"
            "  src/primary.py  (+300 -10)\n"
            "=== SECONDARY SCOPE: config-ops ===\n"
            "=== NOT DIFFED (budget exceeded, 1 files) ===\n"
            "  config/secondary.php  (+200 -5)\n"
        )
        assert extract_not_diffed_files(scope) == [
            "src/primary.py",
            "config/secondary.php",
        ]

    def test_extract_not_diffed_files_empty_without_section(self):
        scope = "=== FILES ===\nsrc/a.py  (+5 -1)\n=== DIFFS ===\n"
        assert extract_not_diffed_files(scope) == []

    def test_line_count_excludes_lock_and_generated_stats(self):
        """CHANGED (no diff) lock/generated files stay out of budget sizing."""
        scope = (
            "=== FILES ===\n"
            "src/app.py  (+50 -10)\n"
            "=== CHANGED (no diff — 1 lock/generated files) ===\n"
            "These files changed but diffs are skipped (too large/noisy for inline review).\n"
            "  package-lock.json  (+9000 -9000)\n"
            "=== DIFFS ===\n"
        )
        assert extract_scope_line_count(scope) == 60

    def test_extract_list_only_files_skips_section_prose(self):
        """List-only files are in-scope (the section tells the reviewer to
        inspect them), so telemetry must see them — while their stats stay
        out of budget sizing and the inline FILES list."""
        scope = (
            "=== FILES ===\n"
            "src/app.py  (+50 -10)\n"
            "=== CHANGED (no diff — 1 lock/generated files) ===\n"
            "These files changed but diffs are skipped (too large/noisy for inline review).\n"
            "Use 'git diff base..head -- <file>' to inspect if relevant.\n"
            "  package-lock.json  (+9000 -9000)\n"
            "=== DIFFS ===\n"
        )
        assert extract_list_only_files(scope) == ["package-lock.json"]
        assert extract_scope_files(scope) == ["src/app.py"]
        assert extract_scope_line_count(scope) == 60

    def test_extract_list_only_files_accumulates_across_secondary_scopes(self):
        scope = (
            "=== CHANGED (no diff — 1 lock/generated files) ===\n"
            "  package-lock.json  (+9000 -9000)\n"
            "=== SECONDARY SCOPE: config-ops ===\n"
            "=== CHANGED (no diff — 1 lock/generated files) ===\n"
            "  composer.lock  (+400 -400)\n"
        )
        assert extract_list_only_files(scope) == [
            "package-lock.json",
            "composer.lock",
        ]

    def test_extract_list_only_files_empty_without_section(self):
        scope = "=== FILES ===\nsrc/a.py  (+5 -1)\n=== DIFFS ===\n"
        assert extract_list_only_files(scope) == []


class TestLoadScopeFacts:
    """load_scope_facts derives scope facts from summary sidecars, falling
    back to None (→ text parsing) on any missing or malformed sidecar."""

    def _write_summary(self, path, **overrides):
        data = {
            "schema": 2,
            "inline_diff_files": ["src/a.py"],
            "review_claimable_files": ["src/deferred.py"],
            "list_only_files": ["package-lock.json"],
            "in_scope_review_files": ["src/a.py", "src/deferred.py"],
            "in_scope_stat_lines": 100,
        }
        data.update(overrides)
        path.write_text(json.dumps(data))
        return str(path)

    def test_accumulates_across_primary_and_secondary(self, tmp_path):
        primary = self._write_summary(tmp_path / "a-scope-summary.json")
        secondary = self._write_summary(
            tmp_path / "a-scope-summary-config-ops.json",
            inline_diff_files=["ci.yml"],
            review_claimable_files=[],
            list_only_files=[],
            in_scope_review_files=["ci.yml"],
            in_scope_stat_lines=7,
        )
        facts = load_scope_facts([primary, secondary])
        assert facts == {
            "files": ["src/a.py", "ci.yml"],
            "review_claimable": ["src/deferred.py"],
            "list_only": ["package-lock.json"],
            "stat_lines": 107,
        }

    def test_no_paths_returns_none(self):
        assert load_scope_facts([]) is None

    def test_missing_sidecar_returns_none(self, tmp_path):
        primary = self._write_summary(tmp_path / "a-scope-summary.json")
        assert load_scope_facts(
            [primary, str(tmp_path / "gone.json")]
        ) is None

    def test_malformed_json_returns_none(self, tmp_path):
        path = tmp_path / "a-scope-summary.json"
        path.write_text("{not json")
        assert load_scope_facts([str(path)]) is None

    @pytest.mark.parametrize(
        "overrides",
        [
            {"in_scope_stat_lines": None},   # pre-field producer
            {"in_scope_stat_lines": True},   # bool is not a count
            {"in_scope_stat_lines": 1.5},
            {"inline_diff_files": "src/a.py"},
            {"review_claimable_files": [1]},
            {"list_only_files": None},
        ],
    )
    def test_malformed_fields_return_none(self, tmp_path, overrides):
        """Any deviation falls back wholesale to text parsing — mixed-source
        facts would be harder to reason about than one honest fallback."""
        path = self._write_summary(
            tmp_path / "a-scope-summary.json", **overrides
        )
        assert load_scope_facts([path]) is None


class TestLoadAdditionalInstructions:
    """load_additional_instructions() reads from run-config.json."""

    def test_returns_value_when_present(self, tmp_path):
        config = {"additional_instructions": "Focus on error handling in the retry logic."}
        (tmp_path / "run-config.json").write_text(json.dumps(config))
        result = load_additional_instructions(str(tmp_path))
        assert result == "Focus on error handling in the retry logic."

    def test_returns_none_when_key_missing(self, tmp_path):
        config = {"mode": "pr", "quick": False}
        (tmp_path / "run-config.json").write_text(json.dumps(config))
        result = load_additional_instructions(str(tmp_path))
        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path):
        result = load_additional_instructions(str(tmp_path))
        assert result is None

    def test_returns_none_when_empty_string(self, tmp_path):
        config = {"additional_instructions": ""}
        (tmp_path / "run-config.json").write_text(json.dumps(config))
        result = load_additional_instructions(str(tmp_path))
        assert result is None

    def test_returns_none_when_whitespace_only(self, tmp_path):
        config = {"additional_instructions": "   \n  "}
        (tmp_path / "run-config.json").write_text(json.dumps(config))
        result = load_additional_instructions(str(tmp_path))
        assert result is None

    def test_handles_malformed_json(self, tmp_path):
        (tmp_path / "run-config.json").write_text("not valid json")
        result = load_additional_instructions(str(tmp_path))
        assert result is None

    def test_strips_whitespace(self, tmp_path):
        config = {"additional_instructions": "  Check for XSS vulnerabilities.  "}
        (tmp_path / "run-config.json").write_text(json.dumps(config))
        result = load_additional_instructions(str(tmp_path))
        assert result == "Check for XSS vulnerabilities."


class TestAdditionalInstructionsInjection:
    """additional_instructions is injected as REVIEWER-REQUESTED FOCUS in bootstrap output."""

    def test_section_present_when_provided(self):
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            review_claimable_count=0,
            has_php=False,
            additional_instructions="Focus on error handling in the retry logic.",
        )
        assert "=== REVIEWER-REQUESTED FOCUS ===" in output
        assert "Focus on error handling in the retry logic." in output
        assert "Prioritize findings" in output

    def test_section_absent_when_none(self):
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            review_claimable_count=0,
            has_php=False,
            additional_instructions=None,
        )
        assert "REVIEWER-REQUESTED FOCUS" not in output

    def test_section_absent_when_not_passed(self):
        """When additional_instructions is omitted (default None), section is absent."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            review_claimable_count=0,
            has_php=False,
        )
        assert "REVIEWER-REQUESTED FOCUS" not in output

    def test_positioned_after_change_purpose_before_budget(self):
        """REVIEWER-REQUESTED FOCUS should appear after REVIEW FOCUS and before REVIEW BUDGET."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake",
            status="OK",
            review_rules="Rules here",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/test",
            pr_number="1",
            reviewer_name="security",
            review_claimable_count=0,
            has_php=False,
            change_purpose="Adds retry logic.",
            additional_instructions="Focus on error handling.",
            review_budget=30,
        )
        focus_pos = output.index("REVIEW FOCUS")
        requested_pos = output.index("REVIEWER-REQUESTED FOCUS")
        budget_pos = output.index("REVIEW BUDGET")
        assert focus_pos < requested_pos < budget_pos


# ---------------------------------------------------------------------------
# run_scope_discovery — scope summary sidecar wiring
# ---------------------------------------------------------------------------

from unittest.mock import patch


class TestScopeSummaryWiring:
    """run_scope_discovery must forward the summary sidecar path to scope.py."""

    def test_run_scope_discovery_passes_summary_flag(self):
        with patch.object(_mod, "run_cmd", return_value=(0, "=== REVIEW SCOPE ===", "")) as mock_run:
            _mod.run_scope_discovery(
                str(PLUGIN_ROOT), "security", [], "abc..def",
                output_dir="/tmp/out",
                summary_json_out="/tmp/out/security-reviewer-scope-summary.json",
            )
        cmd = mock_run.call_args[0][0]
        assert "--summary-json-out" in cmd
        assert cmd[cmd.index("--summary-json-out") + 1] == (
            "/tmp/out/security-reviewer-scope-summary.json"
        )

    def test_run_scope_discovery_omits_flag_when_none(self):
        with patch.object(_mod, "run_cmd", return_value=(0, "=== REVIEW SCOPE ===", "")) as mock_run:
            _mod.run_scope_discovery(
                str(PLUGIN_ROOT), "security", [], "abc..def",
            )
        assert "--summary-json-out" not in mock_run.call_args[0][0]

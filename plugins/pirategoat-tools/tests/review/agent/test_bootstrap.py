"""Tests for review/agent/bootstrap.py — unit tests (direct function imports)."""

import json
import os
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
from review import run_paths
from review.reviewer_lifecycle import review_paths

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
load_scope_facts = _mod.load_scope_facts
resolve_overall_status = _mod.resolve_overall_status
REVIEWER_PROTOCOL_SKIP_SECTIONS = _mod.REVIEWER_PROTOCOL_SKIP_SECTIONS


# =============================================================================
# CLI argument contract
# =============================================================================


def test_cli_requires_output_dir(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--agent", "security-reviewer"])

    with pytest.raises(SystemExit) as exc:
        _mod.main()

    assert exc.value.code != 0
    assert "ERROR: --output-dir is required" in capsys.readouterr().err


# =============================================================================
# Unit Tests — direct import
# =============================================================================



def _inline(count):
    """`count` distinct inline placeholder paths for a schema-5 assignment."""
    return [f"src/inline-{n}.php" for n in range(count)]

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


class TestPersistReviewedFilesInput:
    """The writer publishes the exact schema-4 assignment."""

    def test_persist_assignment_payload(self, tmp_path):
        """One full-payload equality assert over the authoritative writer."""
        _mod.persist_review_assignment(
            str(tmp_path),
            "repo-renewals-reviewer",
            ["src/claimable.php"],
            review_budget=80,
            in_scope_review_file_count=12,
            inline_diff_files=_inline(11),
            channels=["blocking"],
        )

        payload = json.loads(
            Path(review_paths(tmp_path, "repo-renewals").assignment).read_text()
        )
        assert payload == {
            "schema": 5,
            "agent_name": "repo-renewals-reviewer",
            "reviewer": "repo-renewals",
            "review_claimable_files": ["src/claimable.php"],
            "review_budget": 80,
            "in_scope_review_file_count": 12,
            "inline_diff_files": _inline(11),
            "channels": ["blocking"],
        }

    def test_write_errors_are_not_silenced(self, tmp_path):
        output_file = tmp_path / "not-a-directory"
        output_file.write_text("occupied")

        with pytest.raises(OSError):
            _mod.persist_review_assignment(
                str(output_file), "security-reviewer", ["src/claimable.php"],
                review_budget=80,
                in_scope_review_file_count=1, inline_diff_files=_inline(0),
                channels=["blocking"],
            )

    def test_dedupes_claimable_files_order_preserving(self, tmp_path):
        """A multi-domain agent's secondary-domain scope render can repeat
        a file already budget-exceeded in the primary domain's sidecar —
        load_scope_facts() concatenates every summary's
        budget_exceeded_files without deduping. persist_review_assignment
        must not publish that duplicate: it inflates len(review_claimable_files),
        the total manifest_sections.build_assignment_manifest reconciles
        the agent's derived positive-claim/gap populations against."""
        _mod.persist_review_assignment(
            str(tmp_path),
            "security-reviewer",
            ["src/a.php", "src/b.php", "src/a.php"],
            review_budget=80,
            in_scope_review_file_count=4, inline_diff_files=_inline(2),
            channels=["blocking"],
        )

        payload = json.loads(
            Path(review_paths(tmp_path, "security").assignment).read_text()
        )
        assert payload == {
            "schema": 5,
            "agent_name": "security-reviewer",
            "reviewer": "security",
            "review_claimable_files": ["src/a.php", "src/b.php"],
            "review_budget": 80,
            "in_scope_review_file_count": 4,
            "inline_diff_files": _inline(2),
            "channels": ["blocking"],
        }

    @pytest.mark.parametrize(
        "kwargs",
        [
            {
                "review_budget": 40, "in_scope_review_file_count": None,
                "inline_diff_files": _inline(0), "channels": ["blocking"],
            },
            {
                "review_budget": 40, "in_scope_review_file_count": 1,
                "inline_diff_files": None, "channels": ["blocking"],
            },
            {
                "review_budget": 40, "in_scope_review_file_count": 1,
                "inline_diff_files": _inline(1), "channels": ["blocking"],
            },
        ],
    )
    def test_rejects_incomplete_or_incoherent_payloads(self, tmp_path, kwargs):
        with pytest.raises(ValueError):
            _mod.persist_review_assignment(
                str(tmp_path), "security-reviewer", ["src/a.php"], **kwargs
            )


class TestPartitionScopePaths:
    """Scope populations are disjoint, ordered sets with fixed precedence."""

    def test_partitions_duplicates_and_cross_population_overlap(self):
        inline, claimable = _mod.partition_scope_paths(
            ["src/inline-a.py", "src/shared.py", "src/inline-a.py"],
            [
                "src/claimable-a.py",
                "src/shared.py",
                "src/claimable-a.py",
                "src/claimable-b.py",
            ],
        )

        assert inline == ["src/inline-a.py", "src/shared.py"]
        assert claimable == ["src/claimable-a.py", "src/claimable-b.py"]

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
                "inline_diff_files": [
                    "src/inline.py", "src/shared.py", "src/inline.py",
                    "src/secondary.py", "src/shared.py",
                ],
                "review_claimable_files": [
                    "src/claimable-b.py", "src/shared.py",
                    "src/claimable-a.py", "src/claimable-a.py",
                    "src/secondary.py",
                ],
                "list_only_files": [
                    "package-lock.json", "src/claimable-b.py",
                    "package-lock.json", "generated/api.json",
                ],
                "in_scope_stat_lines": 100,
            },
        )
        scope_output = (
            "STATUS: OK\n"
            "=== FILES ===\n"
            "src/inline.py  (+10 -0)\n"
            "src/shared.py  (+10 -0)\n"
            "src/secondary.py  (+10 -0)\n"
            "=== NOT DIFFED (budget exceeded, 2 files) ===\n"
            "src/claimable-a.py  (+10 -0)\n"
            "src/claimable-b.py  (+20 -0)\n"
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

        builder = ReviewOutputBuilder.open(str(tmp_path), "123", "security")
        builder.claim_files_reviewed("src/claimable-b.py")
        builder.save_draft()

        payload = json.loads(
            Path(review_paths(tmp_path, "security").assignment).read_text()
        )
        covered = len(payload["inline_diff_files"]) + len(builder.reviewed_file_claims)
        assert telemetry_starts[0]["scope_paths"] == [
            "src/inline.py",
            "src/shared.py",
            "src/secondary.py",
            "src/claimable-b.py",
            "src/claimable-a.py",
            "package-lock.json",
            "generated/api.json",
        ]
        assert payload["review_claimable_files"] == [
            "src/claimable-b.py",
            "src/claimable-a.py",
        ]
        assert len(payload["inline_diff_files"]) == 3
        assert payload["in_scope_review_file_count"] == 5
        assert 0 <= covered <= payload["in_scope_review_file_count"]
        assert (
            "FILES NOT YET CLAIMED AS REVIEWED (1): src/claimable-a.py"
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
        # The stub writes no sidecar; without facts the run stops earlier.
        monkeypatch.setattr(
            _mod,
            "load_scope_facts",
            lambda _paths: {
                "inline_diff_files": ["src/a.py"],
                "review_claimable_files": [],
                "list_only_files": [],
                "in_scope_stat_lines": 1,
            },
        )
        monkeypatch.setattr(
            _mod,
            "persist_review_assignment",
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
        assert "Could not publish authoritative review assignment: disk full" in output

    def test_main_refuses_to_run_without_scope_facts(
        self, tmp_path, monkeypatch, capsys
    ):
        """A missing sidecar is a structured error, never a text re-parse."""
        monkeypatch.setattr(_mod, "find_plugin_root", lambda: str(PLUGIN_ROOT))
        monkeypatch.setattr(_mod, "read_file", lambda _path: "# rules")
        monkeypatch.setattr(
            _mod,
            "run_scope_discovery",
            lambda *_args, **_kwargs: (
                0, "STATUS: OK\n=== FILES ===\nsrc/a.py  (+1 -0)\n"
            ),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                "bootstrap.py", "--agent", "security-reviewer",
                "--output-dir", str(tmp_path),
            ],
        )

        with pytest.raises(SystemExit, match="1"):
            _mod.main()

        output = capsys.readouterr().out
        assert "STATUS: ERROR" in output
        assert "scope summary" in output
        assert not list(tmp_path.glob("*-assignment.json"))

    def test_scope_failure_reports_what_scope_said_not_the_missing_sidecar(
        self, tmp_path, monkeypatch, capsys
    ):
        """A clean tree is a no-op, not broken infrastructure.

        scope.py already answered the question — nothing changed, approve and
        exit — and never got as far as writing a summary. Reporting the
        missing file instead would hide that answer behind a symptom and
        turn every benign no-op into an error the reviewer must escalate.
        """
        monkeypatch.setattr(_mod, "find_plugin_root", lambda: str(PLUGIN_ROOT))
        monkeypatch.setattr(_mod, "read_file", lambda _path: "# rules")
        monkeypatch.setattr(
            _mod,
            "run_scope_discovery",
            lambda *_args, **_kwargs: (
                2,
                "=== REVIEW SCOPE ===\n"
                "STATUS: ERROR\n"
                "ERROR: NO_CHANGES: No changes to review — clean working "
                "tree.\n"
                "ACTION: APPROVE and exit — nothing to review.\n",
            ),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                "bootstrap.py", "--agent", "security-reviewer",
                "--output-dir", str(tmp_path),
            ],
        )

        with pytest.raises(SystemExit, match="1"):
            _mod.main()

        output = capsys.readouterr().out
        assert "=== BOOTSTRAP: security-reviewer ===" in output
        assert "ERROR: NO_CHANGES: No changes to review" in output
        assert "ACTION: APPROVE and exit — nothing to review." in output
        # The downstream symptom must not displace the real diagnosis.
        assert "scope summary" not in output
        assert not list(tmp_path.glob("*-assignment.json"))
        # No marker either: agents_status would otherwise report a reviewer
        # that never received a briefing as RUNNING until the timeout.
        assert not list(tmp_path.glob("*.started"))

    def test_pinned_output_dir_gets_measured_facts(
        self, tmp_path, monkeypatch
    ):
        """A durable output directory carries the scope facts between steps."""
        summary_paths = []

        def fake_scope(*_args, **kwargs):
            path = kwargs["summary_json_out"]
            summary_paths.append(path)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump({
                    "schema": 3,
                    "inline_diff_files": ["src/a.py"],
                    "review_claimable_files": ["src/claimable.py"],
                    "list_only_files": [],
                    "routing_files": ["src/a.py", "src/claimable.py"],
                    "in_scope_stat_lines": 40,
                }, f)
            return 0, f"STATUS: OK\nOUTPUT_DIR: {tmp_path}\n"

        monkeypatch.setattr(_mod, "find_plugin_root", lambda: str(PLUGIN_ROOT))
        monkeypatch.setattr(_mod, "read_file", lambda _path: "# rules")
        monkeypatch.setattr(_mod, "run_scope_discovery", fake_scope)
        monkeypatch.setattr(
            sys, "argv", [
                "bootstrap.py", "--agent", "security-reviewer",
                "--output-dir", str(tmp_path),
            ]
        )

        with pytest.raises(SystemExit, match="0"):
            _mod.main()

        assert os.path.dirname(summary_paths[0]) == str(
            tmp_path / "reviewers" / "security"
        )
        payload = json.loads(
            Path(review_paths(tmp_path, "security").assignment).read_text()
        )
        assert payload["in_scope_review_file_count"] == 2
        assert len(payload["inline_diff_files"]) == 1



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

    def test_new_section_auto_included(self):
        """Skip-list extraction on one call: skipped sections are absent,
        non-skipped sections (including one added after the skip list was
        written) are present, and the level-1 title is stripped — skip-list
        resilience is the load-bearing contract, so it keeps this node id."""
        result = extract_protocol_sections(
            self.SAMPLE_PROTOCOL, REVIEWER_PROTOCOL_SKIP_SECTIONS
        )

        assert "## Step 0" not in result
        assert "## Scope Discovery" not in result
        assert "Subsection of Scope" not in result
        assert "## Output Directory" not in result
        assert "## ReviewOutputBuilder API" not in result
        assert "## File-Based Output" not in result

        assert "## RULE: Reviewing vs Exploring" in result
        assert "Important rule that should be included" in result
        assert "## Project-Specific Knowledge" in result
        assert "## Severity Calibration" in result

        assert "## New Future Section" in result
        assert "added later and should be included automatically" in result

        assert "# Shared Reviewer Protocol" not in result

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


class TestLoadPrIntent:
    """PR intent loading from review-context.json."""

    @pytest.mark.parametrize(
        "file_content",
        [
            pytest.param(None, id="no_file"),
            pytest.param("{}", id="empty_json"),
            pytest.param(
                json.dumps({"pr": {"body": "Some body", "author": "dev"}}),
                id="no_title",
            ),
            pytest.param("not json", id="malformed_json"),
        ],
    )
    def test_pr_intent_absent(self, tmp_path, file_content):
        if file_content is not None:
            (tmp_path / "review-context.json").write_text(file_content)
        assert load_pr_intent(str(tmp_path)) is None

    def test_returns_intent_with_title(self, tmp_path):
        """One PR context carrying title, author, body, and a linked issue —
        every field the loader surfaces lands in the intent string."""
        ctx = {
            "pr": {
                "title": "Fix rounding in refunds",
                "author": "alice",
                "body": "Refund totals were off",
            },
            "linked_issues": ["WOOPLUG-123"],
        }
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert intent is not None
        assert "Fix rounding in refunds" in intent
        assert "alice" in intent
        assert "Refund totals were off" in intent
        assert "WOOPLUG-123" in intent

    def test_the_body_is_never_cut(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing", "body": "x" * 1000}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert "x" * 1000 in intent
        assert "..." not in intent

    def test_html_comments_are_stripped_from_the_fallback_body(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing", "body": "Real <!-- template: passwords --> prose"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path))
        assert "PR Description: Real  prose" in intent
        assert "password" not in intent

    def test_an_extracted_description_replaces_the_body_with_a_pointer(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing", "body": "Raw body with checklist"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        purpose = (
            "## Verify\nNone.\n## Context\nNone.\n"
            "## Author's description (extracted)\n> The substantive part.\n"
        )
        intent = load_pr_intent(str(tmp_path), change_purpose=purpose)
        assert "Raw body with checklist" not in intent
        assert 'PR Description: extracted by the orchestrator, by judgement, under REVIEW FOCUS → "Author\'s description (extracted)"' in intent

    def test_a_none_author_section_keeps_the_body(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing", "body": "Raw body"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        purpose = "## Verify\nNone.\n## Context\nNone.\n## Author's description (extracted)\nNone.\n"
        intent = load_pr_intent(str(tmp_path), change_purpose=purpose)
        assert "PR Description: Raw body" in intent

    def test_an_unstructured_purpose_keeps_the_body(self, tmp_path):
        ctx = {"pr": {"title": "Fix thing", "body": "Raw body"}}
        (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        intent = load_pr_intent(str(tmp_path), change_purpose="Free prose, no headings.")
        assert "PR Description: Raw body" in intent


class TestChangePurpose:
    """load_change_purpose() reads the grouped change-purpose artifact."""

    @staticmethod
    def _path(tmp_path):
        path = run_paths.artifact_path(tmp_path, "change_purpose")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @pytest.mark.parametrize(
        "write_empty_file",
        [
            pytest.param(False, id="missing"),
            pytest.param(True, id="empty"),
        ],
    )
    def test_returns_none_when_absent(self, tmp_path, write_empty_file):
        if write_empty_file:
            self._path(tmp_path).write_text("")
        assert load_change_purpose(str(tmp_path)) is None

    def test_strips_whitespace(self, tmp_path):
        self._path(tmp_path).write_text("  Content with spaces.  \n\n")
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

    def test_a_structured_purpose_states_the_two_tiers(self):
        purpose = (
            "## Verify\nV1. claim — source: PR description\n## Context\nNone.\n"
            "## Author's description (extracted)\nquoted\n"
        )
        output = build_output(
            agent_name="security-reviewer", plugin_root="/fake", status="OK",
            review_rules="Rules here", domain_rules=None, scope_output="scope",
            exploration_scope=None, output_dir="/tmp/test", pr_number="1",
            reviewer_name="security", review_claimable_count=0, has_php=False,
            change_purpose=purpose,
        )
        assert 'verifies=["V1"])  # the Verify item(s) this check settles' in output
        assert "`## Context` items are facts to take as given" in output

    def test_a_purpose_with_no_verify_items_asks_for_no_citation(self):
        purpose = (
            "## Verify\nNone.\n## Context\nC1. fact — source: commit abc\n"
            "## Author's description (extracted)\nquoted\n"
        )
        output = build_output(
            agent_name="security-reviewer", plugin_root="/fake", status="OK",
            review_rules="Rules here", domain_rules=None, scope_output="scope",
            exploration_scope=None, output_dir="/tmp/test", pr_number="1",
            reviewer_name="security", review_claimable_count=0, has_php=False,
            change_purpose=purpose,
        )
        assert "verifies=" not in output
        assert "`## Verify` declares nothing load-bearing for this change." in output
        assert "`## Context` items are facts to take as given" in output

    def test_an_unstructured_purpose_gets_no_tier_sentence(self):
        output = build_output(
            agent_name="security-reviewer", plugin_root="/fake", status="OK",
            review_rules="Rules here", domain_rules=None, scope_output="scope",
            exploration_scope=None, output_dir="/tmp/test", pr_number="1",
            reviewer_name="security", review_claimable_count=0, has_php=False,
            change_purpose="Adds retry logic to the payment gateway.",
        )
        assert "verifies=" not in output

class TestResolveOverallStatus:
    """Defense-in-depth: primary NO_DOMAIN_FILES + secondary content → scoped OK."""

    @pytest.mark.parametrize(
        ("domain", "status", "secondary_files_exist", "expected_status", "expected_secondary_only"),
        [
            pytest.param("security", "OK", False, "OK", False, id="normal_ok_passes_through"),
            pytest.param(
                "security", "NO_DOMAIN_FILES", False, "NO_DOMAIN_FILES", False,
                id="no_domain_files_without_secondary_stays",
            ),
            pytest.param(
                "security", "NO_DOMAIN_FILES", True, "OK", True,
                id="no_domain_files_with_secondary_flips_to_scoped_ok",
            ),
            pytest.param(None, "OK", False, "OK", False, id="null_domain_is_ok"),
            pytest.param("security", "ERROR", True, "ERROR", False, id="error_status_not_overridden"),
        ],
    )
    def test_resolve_overall_status(
        self, domain, status, secondary_files_exist, expected_status, expected_secondary_only
    ):
        resolved_status, secondary_only = resolve_overall_status(
            domain, status, secondary_files_exist
        )
        assert resolved_status == expected_status
        assert secondary_only is expected_secondary_only


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

    @pytest.mark.parametrize(
        "ctx",
        [
            pytest.param(None, id="missing"),
            pytest.param({"pr": {"number": 42}}, id="no_pr_size_key"),
            pytest.param({"pr_size": {"category": "small"}}, id="lines_missing"),
        ],
    )
    def test_pr_size_absent(self, tmp_path, ctx):
        if ctx is not None:
            (tmp_path / "review-context.json").write_text(json.dumps(ctx))
        assert load_pr_size_from_context(str(tmp_path)) is None


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

    def test_minimum(self):
        """Even tiny PRs should get a minimum viable budget."""
        budget = compute_review_budget(changed_lines=5, file_count=1)
        assert budget >= 15

    def test_scope_lines_preferred_over_pr_lines(self):
        """Budget should scale with scope-level lines, not PR-level, and
        should cap at a maximum regardless of PR size."""
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


class TestBuildErrorOutput:
    """Error output format."""

    def test_error_structure(self):
        result = build_error_output("security-reviewer", "Something went wrong")
        assert "=== BOOTSTRAP: security-reviewer ===" in result
        assert "STATUS: ERROR" in result
        assert "ERROR: Something went wrong" in result
        assert "ACTION: Report this error" in result

class TestLoadScopeFacts:
    """load_scope_facts is the ONE source of a reviewer's scope facts.

    It reads the sidecar's own key names and fails closed. There is no text
    fallback: a run whose sidecar is missing or malformed has no facts, and
    reporting none is honest where re-deriving some from rendered prose was
    a second, quietly different answer.
    """

    def _write_summary(self, path, **overrides):
        data = {
            "schema": 3,
            "inline_diff_files": ["src/a.py"],
            "review_claimable_files": ["src/claimable.py"],
            "list_only_files": ["package-lock.json"],
            "routing_files": ["src/a.py", "src/claimable.py"],
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
            routing_files=["ci.yml"],
            in_scope_stat_lines=7,
        )
        assert load_scope_facts([primary, secondary]) == {
            "inline_diff_files": ["src/a.py", "ci.yml"],
            "review_claimable_files": ["src/claimable.py"],
            "list_only_files": ["package-lock.json"],
            "in_scope_stat_lines": 107,
        }

    def test_no_paths_is_no_scope(self):
        """A no-domain agent requests no summary and legitimately has none."""
        assert load_scope_facts([]) == {
            "inline_diff_files": [],
            "review_claimable_files": [],
            "list_only_files": [],
            "in_scope_stat_lines": 0,
        }

    def test_missing_sidecar_raises(self, tmp_path):
        with pytest.raises(ValueError, match="scope summary"):
            load_scope_facts([str(tmp_path / "absent.json")])

    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "a-scope-summary.json"
        path.write_text("{not json")
        with pytest.raises(ValueError, match="scope summary"):
            load_scope_facts([str(path)])

    @pytest.mark.parametrize(
        "overrides",
        [
            {"schema": 2},
            {"in_scope_stat_lines": None},
            {"in_scope_stat_lines": True},
            {"in_scope_stat_lines": 1.5},
            {"inline_diff_files": "src/a.py"},
            {"review_claimable_files": [1]},
            {"list_only_files": None},
        ],
    )
    def test_malformed_fields_raise(self, tmp_path, overrides):
        path = self._write_summary(
            tmp_path / "a-scope-summary.json", **overrides
        )
        with pytest.raises(ValueError, match="scope summary"):
            load_scope_facts([path])


class TestLoadAdditionalInstructions:
    """load_additional_instructions() reads from run-config.json."""

    @pytest.mark.parametrize(
        ("file_content", "expected"),
        [
            pytest.param(
                json.dumps({"additional_instructions": "Focus on error handling in the retry logic."}),
                "Focus on error handling in the retry logic.",
                id="present",
            ),
            pytest.param(
                json.dumps({"additional_instructions": "  Check for XSS vulnerabilities.  "}),
                "Check for XSS vulnerabilities.",
                id="strips_whitespace",
            ),
            pytest.param(
                json.dumps({"mode": "pr", "quick": False}), None, id="key_missing",
            ),
            # Blank after stripping — covers both the empty-string and
            # whitespace-only inputs; both hit the same `.strip()` branch.
            pytest.param(
                json.dumps({"additional_instructions": "   \n  "}), None, id="blank_value",
            ),
            pytest.param("not valid json", None, id="malformed_json"),
        ],
    )
    def test_load_additional_instructions(self, tmp_path, file_content, expected):
        (tmp_path / "run-config.json").write_text(file_content)
        assert load_additional_instructions(str(tmp_path)) == expected

    def test_returns_none_when_file_missing(self, tmp_path):
        assert load_additional_instructions(str(tmp_path)) is None


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



"""Tests for `assemble_review_record()` — the machine projection of the ledger.

`review-record.md` is assembled by the pipeline, never written or edited by
an agent. It composes the SAME renderers the other derived Markdown uses
(`render_review_body` for the findings/checks body,
`_render_review_accounting_section` for coverage) plus three thin additions
the record alone needs: its own header, the run notes, and a closing
verdict line.

The contract these tests pin:

* the record is a projection — re-assembling after a critic batch reflects
  the post-adjustment severities, the recomputed verdict, and the
  checkpointed adjudication assessment;
* the shared bodies are byte-identical to what their own renderers produce,
  so a record can never disagree with `review-findings.md` about a finding
  or with the step-9 coverage measurement about a file;
* the write is atomic, so a half-assembled record is never observable.
"""

import json
import os
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review import briefings as briefings_mod
from review import critic_adjustments
from review import orchestration as orchestration_mod
from review.atomic_io import atomic_write_json
from review.agent.output import render_review_body
from review.orchestration import (
    REVIEW_RECORD_MD,
    _report_source_fingerprint,
    assemble_review_record,
)


def _ledger(**overrides):
    """A minimal, valid `review-findings.json` document."""
    findings = {
        "schema": 2,
        "reviewer": "review-reconciliator",
        "pr_id": "42",
        "verdict": "request_changes",
        "summary": {
            "total_findings": 2,
            "by_severity": {
                "critical": 0, "high": 1, "medium": 1, "low": 0, "info": 0,
            },
        },
        "findings": [
            {
                "id": "f1",
                "severity": "high",
                "category": "security",
                "title": "Unescaped output in the admin notice",
                "file": "src/admin.php",
                "line": 42,
                "description": "The notice echoes `$_GET['msg']` unescaped.",
                "recommendation": "Wrap it in `esc_html()`.",
                "confidence": 0.9,
            },
            {
                "id": "f2",
                "severity": "medium",
                "category": "reliability",
                "title": "Retry loop has no ceiling",
                "file": "src/retry.php",
                "line": 88,
                "description": "The loop retries forever on a 500.",
                "recommendation": "Cap the attempts.",
                "confidence": 0.9,
            },
        ],
        "positive_observations": [],
        "assessment": "Two real problems, both fixable in one pass.",
        "checks": [
            {
                "id": "c1",
                "question": "Does anything else call the removed helper?",
                "method": "git grep across the repo",
                "result": "0 hits outside the deleted file",
                "source_reviewers": ["code-reviewer"],
            },
        ],
        "recommendations": {
            "immediate": ["Escape the admin notice."],
            "important": [],
            "suggestions": [],
        },
    }
    findings.update(overrides)
    return findings


def _write_ledger(output_dir, findings=None):
    critic_adjustments.write_findings(
        str(output_dir), findings if findings is not None else _ledger()
    )


@pytest.fixture
def out_dir(tmp_path):
    directory = tmp_path / "pr-review-42"
    directory.mkdir()
    return directory


class TestRecordAssembly:
    def test_canonical_findings_checks_and_assessment_render_mechanically(
        self, out_dir
    ):
        findings = {
            "schema": 2,
            "reviewer": "reconciliator",
            "pr_id": "42",
            "verdict": "request_changes",
            "summary": {
                "total_findings": 1,
                "by_severity": {
                    "critical": 0,
                    "high": 1,
                    "medium": 0,
                    "low": 0,
                    "info": 0,
                },
                "suppressed_advisory_finding_count": 0,
            },
            "findings": [
                {
                    "id": "f1",
                    "severity": "high",
                    "category": "security",
                    "title": "Unescaped output",
                    "file": "src/admin.php",
                    "line": 42,
                    "description": "The notice is not escaped.",
                    "recommendation": "Escape the notice.",
                    "confidence": 0.95,
                },
            ],
            "checks": [
                {
                    "id": "c1",
                    "question": "Are other callers affected?",
                    "method": "Read every caller.",
                    "result": "No.",
                    "source_reviewers": ["security", "code"],
                },
            ],
            "assessment": "One correction is required.",
            "positive_observations": ["The helper boundary is clear."],
            "recommendations": None,
            "observations": None,
        }
        _write_ledger(out_dir, findings)

        outcome, error = assemble_review_record(str(out_dir), {})

        assert error is None
        assert outcome["status"] == "complete"
        text = (out_dir / REVIEW_RECORD_MD).read_text()
        assert "**Total Findings:** 1" in text
        assert "## High Findings" in text
        assert "## Verified Checks" in text
        assert "**Are other callers affected?**" in text
        assert "Result: No." in text
        assert "## Assessment\n\nOne correction is required." in text
        assert "## Positive Observations" in text

    def test_writes_the_record_and_reports_a_complete_outcome(self, out_dir):
        _write_ledger(out_dir)

        outcome, error = assemble_review_record(str(out_dir), {})

        assert error is None
        assert outcome == {
            "ran": True, "written": 1, "expected": 1, "status": "complete",
        }
        assert (out_dir / REVIEW_RECORD_MD).is_file()

    def test_sections_appear_in_the_documented_order(self, out_dir):
        _write_ledger(out_dir)
        state = {
            "review_accounting": {
                "agents_with_unclaimed_review_by_file": {
                    "src/starved.php": ["code-reviewer"]
                },
                "agents_claiming_review_by_file": {},
                "unscoped_files": ["package-lock.json"],
            },
        }

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        order = [
            "# Review Record",
            "## Executive Summary",
            "## Assessment",
            "## High Findings",
            "## Verified Checks",
            "## Run notes",
            "## Review coverage",
            "Verdict — from the findings ledger",
        ]
        positions = [text.index(marker) for marker in order]
        assert positions == sorted(positions), text

    def test_header_carries_verdict_and_severity_counts(self, out_dir):
        _write_ledger(out_dir)

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "**Verdict:** REQUEST_CHANGES" in text
        assert "**Total Findings:** 2" in text
        assert "- High: 1" in text
        assert "- Medium: 1" in text

    def test_findings_body_is_byte_identical_to_the_shared_renderer(
        self, out_dir
    ):
        findings = _ledger()
        _write_ledger(out_dir, findings)

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert render_review_body(findings) in text

    def test_coverage_section_is_byte_identical_to_the_shared_renderer(
        self, out_dir
    ):
        _write_ledger(out_dir)
        gaps = {"src/starved.php": ["code-reviewer"]}
        claims = {"src/big.py": ["security-reviewer"]}
        unscoped = ["package-lock.json"]
        state = {
            "review_accounting": {
                "agents_with_unclaimed_review_by_file": gaps,
                "agents_claiming_review_by_file": claims,
                "unscoped_files": unscoped,
            },
        }

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert briefings_mod._render_review_accounting_section(
            state["review_accounting"]
        ) in text

    @pytest.mark.parametrize("covered_by", ["inline", "claim"])
    def test_per_agent_unclaimed_work_is_not_rendered_as_a_run_gap_when_covered_elsewhere(
        self, out_dir, covered_by
    ):
        _write_ledger(out_dir)
        state = {"review_accounting": {
            "agents_receiving_inline_diff_by_file": (
                {"src/shared.php": ["code-reviewer"]}
                if covered_by == "inline" else {}
            ),
            "agents_claiming_review_by_file": (
                {"src/shared.php": ["code-reviewer"]}
                if covered_by == "claim" else {}
            ),
            "agents_with_unclaimed_review_by_file": {
                "src/shared.php": ["security-reviewer"]
            },
            "unscoped_files": [],
        }}

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "skipped by every matching agent's diff budget" not in text
        assert not briefings_mod._has_review_accounting_gap(
            state["review_accounting"]
        )

    def test_unscoped_line_says_why_it_can_exceed_the_metrics_figure(
        self, out_dir
    ):
        """F9: the two "uncovered" numbers count different populations.

        The section counts every changed file; run-level metrics count
        reviewable files only. Without the clause a reader treats the two
        figures as the same measurement and reads the difference as a bug
        in one of them.
        """
        _write_ledger(out_dir)
        state = {"review_accounting": {
            "agents_with_unclaimed_review_by_file": {},
            "agents_claiming_review_by_file": {},
            "unscoped_files": ["assets/logo.png"],
        }}

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert (
            "this counts every changed file, including binaries and "
            "non-reviewable paths — run-level metrics count reviewable "
            "files only, so its 'uncovered' figure can be smaller" in text
        )

    def test_unmeasured_coverage_renders_no_coverage_section(self, out_dir):
        _write_ledger(out_dir)

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "## Review coverage" not in text

    def test_banner_precedes_every_finding(self, out_dir):
        _write_ledger(out_dir, _ledger(host_context_banner={
            "degraded": True, "message": "WooCommerce source unresolved.",
        }))

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "> **⚠ Host Context Banner:** WooCommerce source" in text
        assert text.index("Host Context Banner") < text.index("## High Findings")

    def test_run_notes_carry_dependency_refresh_and_dispatch(self, out_dir):
        _write_ledger(out_dir)
        state = {
            "dependency_refresh_precheck": {
                "tracked_files_dirty": False,
                "dirty_files": [],
            },
            "dependency_refresh_report": {
                "schema": 1,
                "status": "completed",
                "commands": [],
                "tracked_files_dirty": False,
                "dirty_files": [],
            },
            "dispatch_plan_summary": {
                "dispatched": 12, "skipped": 9, "conditional": 4,
            },
            "dispatch_plan_warnings": ["unrecognized source language: .zig"],
        }

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "## Run notes" in text
        assert "Dependency refresh:" in text
        assert "12 dispatched" in text
        assert "9 skipped" in text
        assert "unrecognized source language: .zig" in text

    def test_run_notes_report_a_requested_but_unrecorded_refresh(self, out_dir):
        _write_ledger(out_dir)
        state = {
            "dependency_refresh_precheck": {
                "tracked_files_dirty": False,
                "dirty_files": [],
            }
        }

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "Dependency refresh: requested but not recorded" in text

    def test_run_notes_report_declared_status_command_count_and_final_state(
        self, out_dir
    ):
        _write_ledger(out_dir)
        state = {
            "dependency_refresh_precheck": {
                "tracked_files_dirty": False,
                "dirty_files": [],
            },
            "dependency_refresh_report": {
                "schema": 1,
                "status": "partial",
                "commands": [{
                    "directory": ".",
                    "command": "custom sync",
                    "exit_status": "failed",
                }],
                "tracked_files_dirty": None,
                "dirty_files": [],
            },
        }

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "Dependency refresh: partial" in text
        assert "1 command(s) reported" in text
        assert "final tracked files dirty: unknown" in text

    @pytest.mark.parametrize(
        ("tracked_files_dirty", "reason"),
        [(True, "tracked worktree was dirty"), (None, "tracked worktree state was unknown")],
    )
    def test_run_notes_report_precheck_refusal(
        self, out_dir, tracked_files_dirty, reason
    ):
        _write_ledger(out_dir)
        state = {
            "dependency_refresh_precheck": {
                "tracked_files_dirty": tracked_files_dirty,
                "dirty_files": [],
            },
        }

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "Dependency refresh: refused before execution" in text
        assert reason in text

    def test_run_notes_say_not_requested_when_refresh_was_off(self, out_dir):
        _write_ledger(out_dir)

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "Dependency refresh: not requested" in text

    def test_closing_line_reports_the_ledger_verdict(self, out_dir):
        _write_ledger(out_dir)

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "Verdict — from the findings ledger: `request_changes`" in text
        assert "REQUEST_CHANGES" in text.rsplit("Verdict — from", 1)[1]


class TestRecordIsAProjection:
    """Re-assembly after `apply_adjustments` shows the post-critic ledger."""

    @staticmethod
    def _revise(
        out_dir,
        adjustments,
        *,
        verified=(),
        refuted=(),
        assessment="Post-critic assessment.",
        settle=True,
    ):
        proposal = critic_adjustments.prepare_proposal({
            "schema": 2,
            "adjustments": adjustments,
        })
        critic_adjustments.write_adjustments(str(out_dir), proposal)
        atomic_write_json(
            str(out_dir / critic_adjustments.CRITIC_VERDICT_FILENAME),
            {
                "schema": 2,
                "verdict": "REVISE",
                "proposal_digest": critic_adjustments.proposal_digest(
                    proposal
                ),
            },
        )
        if not settle:
            return proposal, critic_adjustments.apply_adjustments(str(out_dir))
        ids = [entry["adjustment_id"] for entry in proposal["adjustments"]]
        request = {
            "schema": 2,
            "verified": [ids[index] for index in verified],
            "refuted": [
                {
                    "adjustment_id": ids[index],
                    "rejection_reason": reason,
                }
                for index, reason in refuted
            ],
            "revised_assessment": assessment,
        }
        return proposal, critic_adjustments.settle(str(out_dir), request)

    def test_reassembly_reflects_adjusted_severity_and_verdict(self, out_dir):
        _write_ledger(out_dir)
        assemble_review_record(str(out_dir), {})
        before = (out_dir / REVIEW_RECORD_MD).read_text()
        assert "**Verdict:** REQUEST_CHANGES" in before

        self._revise(out_dir, [{
            "action": "demote",
            "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"},
            "rationale": "the value is escaped one frame up",
        }], verified=(0,), assessment="Only one real problem after the probe.")

        assemble_review_record(str(out_dir), {})
        after = (out_dir / REVIEW_RECORD_MD).read_text()

        # Recomputed verdict (one medium left → comment), post-adjustment
        # severities, and the orchestrator's replacement assessment.
        assert "**Verdict:** COMMENT" in after
        assert "## Low Findings" in after
        assert "Only one real problem after the probe." in after
        assert "Two real problems, both fixable in one pass." not in after

    def test_reassembly_lists_each_adjustment_with_its_spot_check(
        self, out_dir
    ):
        _write_ledger(out_dir)
        _proposal, result = self._revise(out_dir, [
            {
                "action": "demote",
                "target": {"kind": "finding", "id": "f1"},
                "fields": {"severity": "low"},
                "rationale": "escaped one frame up",
            },
            {
                "action": "correct",
                "target": {"kind": "finding", "id": "f2"},
                "fields": {"title": "Retry loop has no ceiling (v2)"},
                "rationale": "clearer title",
            },
        ], verified=(0,))
        assert result["apply"].get("status") != "refused", result

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "## Critic Adjustment Decisions" in text
        assert "verified" in text
        # An entry with no stated outcome is recorded as unprobed, never
        # absorbed into a batch-level claim.
        assert "not_checked" in text

    def test_reassembly_projects_mixed_applied_and_refuted_decisions(
        self, out_dir
    ):
        _write_ledger(out_dir)
        proposal, _result = self._revise(out_dir, [
            {
                "action": "correct",
                "target": {"kind": "finding", "id": "f1"},
                "fields": {"title": "Escaping is already present"},
                "rationale": "verified against the source",
            },
            {
                "action": "correct",
                "target": {"kind": "finding", "id": "f2"},
                "fields": {"title": "This change does not apply"},
                "rationale": "critic claim",
            },
        ], verified=(0,), refuted=((1, "the source contradicts the claim"),))
        ids = [entry["adjustment_id"] for entry in proposal["adjustments"]]

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "## Critic Adjustment Decisions" in text
        assert f"- `{ids[0]}` — verified" in text
        assert f"- `{ids[1]}` — refuted" in text

    def test_reassembly_projects_an_all_refuted_batch(self, out_dir):
        _write_ledger(out_dir)
        proposal, _result = self._revise(out_dir, [
            {
                "action": "correct",
                "target": {"kind": "finding", "id": "f1"},
                "fields": {"title": "This change does not apply"},
                "rationale": "critic claim",
            },
            {
                "action": "correct",
                "target": {"kind": "finding", "id": "f2"},
                "fields": {"title": "This change does not apply"},
                "rationale": "critic claim",
            },
        ], refuted=(
            (0, "the source contradicts the claim"),
            (1, "the source contradicts the claim"),
        ))
        ids = [entry["adjustment_id"] for entry in proposal["adjustments"]]

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "## Critic Adjustment Decisions" in text
        assert f"- `{ids[0]}` — refuted" in text
        assert f"- `{ids[1]}` — refuted" in text

    def test_invalidated_assessment_renders_the_explicit_absence(self, out_dir):
        _write_ledger(out_dir)
        self._revise(out_dir, [{
            "action": "demote",
            "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"},
            "rationale": "escaped one frame up",
        }], settle=False)

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "No current assessment" in text
        assert "invalidated by critic revision" in text
        # The retracted text is never presented as current.
        assert "Two real problems, both fixable in one pass." not in text


class TestRecordSanitization:
    """Prose severity-floor markers never reach the record.

    They were stripped on the way into `critic-context.md`; the record is
    what the decision critic reads now, so the strip moved here. A floor is
    a reviewer-to-reconciliator directive — presented to the critic as
    prose it reads as an instruction not to demote.
    """

    def test_prose_floor_markers_are_stripped_from_finding_text(
        self, out_dir
    ):
        findings = _ledger()
        findings["findings"][0]["description"] = (
            "Unescaped echo.\nSeverity-floor: high; see the notice helper."
        )
        findings["findings"][0]["recommendation"] = (
            "Severity-floor: high - wrap it in esc_html()."
        )
        findings["assessment"] = (
            "Severity-floor: critical; the admin path is exposed."
        )
        _write_ledger(out_dir, findings)

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "Severity-floor:" not in text
        assert "see the notice helper." in text
        assert "wrap it in esc_html()." in text
        assert "the admin path is exposed." in text

    def test_every_free_text_field_the_record_renders_is_covered(
        self, out_dir
    ):
        """The builder stripped the WHOLE report text; this strips named
        fields, so the list has to match what `render_review_body` actually
        puts in the record — checks, positives, and observations
        included. A field the record renders but the strip skips is a
        marker reaching the critic through the back door."""
        findings = _ledger()
        findings["checks"] = [{
            "id": "c1",
            "question": "Severity-floor: high; does anything call the helper?",
            "method": "Severity-floor: medium - git grep",
            "result": "Severity-floor: low; 0 hits",
            "source_reviewers": ["code-reviewer"],
        }]
        findings["positive_observations"] = [
            "Severity-floor: high; the retry guard is tidy",
        ]
        findings["observations"] = [
            {"file": "src/a.php", "note": "Severity-floor: low; a tradeoff"},
        ]
        _write_ledger(out_dir, findings)

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "Severity-floor:" not in text
        assert "does anything call the helper?" in text
        assert "git grep" in text
        assert "0 hits" in text
        assert "the retry guard is tidy" in text
        assert "a tradeoff" in text

    def test_findings_removed_by_critic_entries_are_covered(self, out_dir):
        findings = _ledger()
        findings["findings_removed_by_critic"] = [{
            "title": "Severity-floor: high; withdrawn finding",
            "severity": "high", "file": "src/x.php", "line": 1,
            "critic_adjustment": {"rationale": "not reachable"},
        }]
        _write_ledger(out_dir, findings)

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "Severity-floor:" not in text
        assert "withdrawn finding" in text

    def test_structured_severity_floor_still_renders(self, out_dir):
        findings = _ledger()
        findings["findings"][0]["severity_floor"] = "high"
        _write_ledger(out_dir, findings)

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "**Severity floor:** high" in text

    def test_non_string_finding_fields_do_not_crash_the_assembly(
        self, out_dir
    ):
        """Reviewer JSON is model-authored: a field the schema expects to
        be a string can arrive as a list, a number, or null. The sanitizer
        is the record's render boundary and coerces before the regex, so a
        malformed field costs a rendering nicety, never the artifact.
        """
        findings = _ledger()
        findings["findings"][0]["recommendation"] = ["wrap it", "in esc_html()"]
        findings["findings"][1]["description"] = None
        findings["recommendations"]["immediate"] = [["escape", "the notice"]]
        _write_ledger(out_dir, findings)

        outcome, error = assemble_review_record(str(out_dir), {})

        assert error is None
        assert outcome["status"] == "complete"
        text = (out_dir / REVIEW_RECORD_MD).read_text()
        assert "wrap it\nin esc_html()" in text

    def test_sanitization_does_not_touch_the_ledger_on_disk(self, out_dir):
        findings = _ledger()
        findings["findings"][0]["description"] = (
            "Unescaped echo.\nSeverity-floor: high; see the helper."
        )
        _write_ledger(out_dir, findings)

        assemble_review_record(str(out_dir), {})

        on_disk = json.loads(
            (out_dir / critic_adjustments.FINDINGS_FILENAME).read_text()
        )
        assert "Severity-floor: high" in on_disk["findings"][0]["description"]


class TestRecordFailureModes:
    def test_no_ledger_is_a_measured_zero_not_a_failure(self, out_dir):
        outcome, error = assemble_review_record(str(out_dir), {})

        assert error is None
        assert outcome == {
            "ran": True, "written": 0, "expected": 0, "status": "complete",
        }
        assert not (out_dir / REVIEW_RECORD_MD).exists()

    def test_unreadable_ledger_reports_failed_and_writes_nothing(
        self, out_dir
    ):
        (out_dir / critic_adjustments.FINDINGS_FILENAME).write_text("{ nope")

        outcome, error = assemble_review_record(str(out_dir), {})

        assert outcome["status"] == "failed"
        assert outcome["written"] == 0
        assert error
        assert not (out_dir / REVIEW_RECORD_MD).exists()

    def test_a_ledger_missing_required_keys_degrades_not_raises(
        self, out_dir
    ):
        (out_dir / critic_adjustments.FINDINGS_FILENAME).write_text(
            json.dumps({"verdict": "approve"})
        )

        outcome, error = assemble_review_record(str(out_dir), {})

        assert outcome["status"] == "failed"
        assert error


class TestBriefingsAreConstantSize:
    """Briefings are O(1) in changed-file count; the record is O(n).

    This is the class of guarantee the record artifact buys, not a single
    fact about step 9. A briefing that grew with the diff put the whole
    coverage measurement into the orchestrator's context window every time
    it asked what to do next — and, worse, asked it to copy that growing
    block into prose it was simultaneously authoring. The measurement now
    lands in a file, and the briefing names the file.
    """

    @staticmethod
    def _coverage_state(count):
        return {
            "completed_steps": [],
            "review_accounting": {
                "agents_with_unclaimed_review_by_file": {},
                "agents_claiming_review_by_file": {},
                "unscoped_files": [
                    f"vendor/generated/module_{i:04d}.lock"
                    for i in range(count)
                ],
            },
            "review_record": {
                "ran": True, "written": 1, "expected": 1,
                "status": "complete",
            },
        }

    def test_step_9_briefing_stays_small_while_the_record_carries_all(
        self, out_dir
    ):
        _write_ledger(out_dir)
        state = self._coverage_state(500)

        assemble_review_record(str(out_dir), state)
        record = (out_dir / REVIEW_RECORD_MD).read_text()

        guidance = briefings_mod.get_step_guidance(
            9, "full", state, {}, output_dir=str(out_dir)
        )
        briefing = "\n".join(
            guidance["situation"] + guidance["actions"]
            + (guidance["handoff"] or [])
        )

        assert len(briefing.encode("utf-8")) < 8192, len(briefing)
        for i in (0, 250, 499):
            assert f"vendor/generated/module_{i:04d}.lock" in record
        assert record.count("vendor/generated/module_") == 500

    def test_step_9_briefing_does_not_grow_with_the_diff(self, out_dir):
        _write_ledger(out_dir)

        def briefing_size(count):
            guidance = briefings_mod.get_step_guidance(
                9, "full", self._coverage_state(count), {},
                output_dir=str(out_dir),
            )
            return len("\n".join(guidance["actions"]).encode("utf-8"))

        assert briefing_size(500) == briefing_size(1)


class TestRecordWriteIsAtomic:
    def test_a_failing_render_leaves_the_previous_record_intact(
        self, out_dir, monkeypatch
    ):
        _write_ledger(out_dir)
        assemble_review_record(str(out_dir), {})
        first = (out_dir / REVIEW_RECORD_MD).read_text()

        def boom(*_args, **_kwargs):
            raise RuntimeError("render exploded")

        monkeypatch.setattr(
            orchestration_mod, "_render_record_body", boom, raising=True
        )
        outcome, error = assemble_review_record(str(out_dir), {})

        assert outcome["status"] == "failed"
        assert "render exploded" in str(error)
        assert (out_dir / REVIEW_RECORD_MD).read_text() == first

    def test_write_goes_through_the_atomic_primitive(
        self, out_dir, monkeypatch
    ):
        _write_ledger(out_dir)
        seen = {}

        real = orchestration_mod.atomic_write_text

        def spy(path, text):
            seen["path"] = path
            return real(path, text)

        monkeypatch.setattr(
            orchestration_mod, "atomic_write_text", spy, raising=True
        )
        assemble_review_record(str(out_dir), {})

        assert os.path.basename(seen["path"]) == REVIEW_RECORD_MD

    def test_no_temp_files_survive_a_successful_assembly(self, out_dir):
        _write_ledger(out_dir)

        assemble_review_record(str(out_dir), {})

        names = sorted(os.listdir(out_dir))
        assert names == sorted([
            critic_adjustments.FINDINGS_FILENAME, REVIEW_RECORD_MD,
        ]), names


class TestPreparedReportSourceFingerprint:
    def _fingerprint(self, out_dir, records=None):
        return _report_source_fingerprint(
            str(out_dir),
            critic_adjustments.FINDINGS_READ_OK,
            "degraded" if records else "success",
            "REQUEST_CHANGES",
            "findings ledger",
            "STAND",
            records or [],
        )

    def test_same_source_and_facts_are_deterministic(self, out_dir):
        _write_ledger(out_dir)
        assemble_review_record(str(out_dir), {})

        assert self._fingerprint(out_dir) == self._fingerprint(out_dir)

    def test_exact_record_and_ledger_bytes_are_bound(self, out_dir):
        _write_ledger(out_dir)
        assemble_review_record(str(out_dir), {})
        baseline = self._fingerprint(out_dir)

        record = out_dir / REVIEW_RECORD_MD
        record.write_bytes(record.read_bytes() + b"\n")
        assert self._fingerprint(out_dir) != baseline

        assemble_review_record(str(out_dir), {})
        findings = out_dir / critic_adjustments.FINDINGS_FILENAME
        findings.write_bytes(findings.read_bytes() + b"\n")
        assert self._fingerprint(out_dir) != baseline

    def test_ordered_degradation_facts_are_bound(self, out_dir):
        _write_ledger(out_dir)
        assemble_review_record(str(out_dir), {})

        first = self._fingerprint(out_dir, [
            {"code": "findings_missing", "message": "diagnostic a"},
            {"code": "ledger_verdict_unusable", "message": "diagnostic b"},
        ])
        second = self._fingerprint(out_dir, [
            {"code": "ledger_verdict_unusable", "message": "diagnostic b"},
            {"code": "findings_missing", "message": "diagnostic a"},
        ])
        assert first != second

    def test_diagnostic_prose_is_not_fingerprint_identity(self, out_dir):
        _write_ledger(out_dir)
        assemble_review_record(str(out_dir), {})

        first = self._fingerprint(out_dir, [{
            "code": "findings_markdown_render_failed", "message": "boom one",
        }])
        second = self._fingerprint(out_dir, [{
            "code": "findings_markdown_render_failed", "message": "boom two",
        }])
        assert first == second

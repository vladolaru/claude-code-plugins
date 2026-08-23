"""Tests for `assemble_review_record()` — the machine projection of the ledger.

`review-record.md` is assembled by the pipeline, never written or edited by
an agent. It composes the SAME renderers the other derived Markdown uses
(`render_review_body` for the findings/clearances body,
`_render_review_coverage_section` for coverage) plus three thin additions
the record alone needs: its own header, the run notes, and a closing
verdict line.

The contract these tests pin:

* the record is a projection — re-assembling after a critic batch reflects
  the post-adjustment severities, the recomputed verdict, and the
  orchestrator's `revised_narrative`;
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
from review.agent.output import render_review_body
from review.orchestration import (
    REVIEW_RECORD_MD,
    _report_source_fingerprint,
    assemble_review_record,
)


def _ledger(**overrides):
    """A minimal, valid `review-findings.json` document."""
    findings = {
        "schema": 3,
        "reviewer": "review-reconciliator",
        "pr_id": "42",
        "verdict": "request_changes",
        "summary": {
            "total_issues": 2,
            "by_severity": {
                "critical": 0, "high": 1, "medium": 1, "low": 0, "info": 0,
            },
        },
        "issues": [
            {
                "id": "9f3a1c7d",
                "severity": "high",
                "category": "security",
                "title": "Unescaped output in the admin notice",
                "file": "src/admin.php",
                "line": 42,
                "description": "The notice echoes `$_GET['msg']` unescaped.",
                "recommendation": "Wrap it in `esc_html()`.",
            },
            {
                "id": "1b2c3d4e",
                "severity": "medium",
                "category": "reliability",
                "title": "Retry loop has no ceiling",
                "file": "src/retry.php",
                "line": 88,
                "description": "The loop retries forever on a 500.",
                "recommendation": "Cap the attempts.",
            },
        ],
        "positive_observations": [],
        "narrative_summary": "Two real problems, both fixable in one pass.",
        "clearances": [
            {
                "claim": "Nothing else calls the removed helper",
                "method": "git grep across the repo",
                "evidence": "0 hits outside the deleted file",
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
            "inline_coverage_gaps": {"src/starved.php": ["code-reviewer"]},
            "inline_coverage_claims": {},
            "inline_coverage_unscoped": ["package-lock.json"],
        }

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        order = [
            "# Review Record",
            "## Executive Summary",
            "## Assessment",
            "## High Issues",
            "## Clearances (verified absences)",
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
        assert "**Total Issues:** 2" in text
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
            "inline_coverage_gaps": gaps,
            "inline_coverage_claims": claims,
            "inline_coverage_unscoped": unscoped,
        }

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert briefings_mod._render_review_coverage_section(
            gaps, claims, unscoped
        ) in text

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
        state = {"inline_coverage_unscoped": ["assets/logo.png"]}

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
        assert text.index("Host Context Banner") < text.index("## High Issues")

    def test_run_notes_carry_dependency_refresh_and_dispatch(self, out_dir):
        _write_ledger(out_dir)
        state = {
            "dependency_refresh": {"signals": [], "skipped_reason": None},
            "dependency_refresh_verification": {
                "report_present": True,
                "commands_allowed": True,
                "tracked_files_dirty": False,
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

    def test_run_notes_report_an_unverified_refresh_as_unverified(
        self, out_dir
    ):
        """`report_present: False` means the orchestrator wrote no
        self-report, so nothing states which install commands ran. Saying
        "ran" there publishes an install nobody observed."""
        _write_ledger(out_dir)
        state = {
            "dependency_refresh": {"signals": [{"root": "."}]},
            "dependency_refresh_verification": {
                "report_present": False,
                "commands_allowed": None,
                "tracked_files_dirty": False,
            },
        }

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "Dependency refresh: requested; installs unverified" in text
        assert "Dependency refresh: ran" not in text

    def test_run_notes_report_a_verified_refresh_as_ran(self, out_dir):
        _write_ledger(out_dir)
        state = {
            "dependency_refresh": {"signals": [{"root": "."}]},
            "dependency_refresh_verification": {
                "report_present": True,
                "commands_allowed": True,
                "tracked_files_dirty": False,
            },
        }

        assemble_review_record(str(out_dir), state)
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "Dependency refresh: ran" in text
        assert "commands allowed: true" in text
        assert "tracked files dirty: false" in text

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
    def _revise(out_dir, adjustments):
        (out_dir / critic_adjustments.CRITIC_VERDICT_FILENAME).write_text(
            json.dumps({"verdict": "REVISE"})
        )
        (out_dir / critic_adjustments.ADJUSTMENTS_FILENAME).write_text(
            json.dumps(adjustments)
        )

    def test_reassembly_reflects_adjusted_severity_and_verdict(self, out_dir):
        _write_ledger(out_dir)
        assemble_review_record(str(out_dir), {})
        before = (out_dir / REVIEW_RECORD_MD).read_text()
        assert "**Verdict:** REQUEST_CHANGES" in before

        self._revise(out_dir, {
            "schema": 1,
            "revised_narrative": "Only one real problem after the probe.",
            "adjustments": [
                {
                    "action": "demote",
                    "id": "9f3a1c7d",
                    "fields": {"severity": "low"},
                    "rationale": "the value is escaped one frame up",
                    "spot_check": "verified",
                },
            ],
        })
        critic_adjustments.apply_adjustments(str(out_dir))

        assemble_review_record(str(out_dir), {})
        after = (out_dir / REVIEW_RECORD_MD).read_text()

        # Recomputed verdict (one medium left → comment), post-adjustment
        # severities, and the orchestrator's replacement narrative.
        assert "**Verdict:** COMMENT" in after
        assert "## Low Issues" in after
        assert "Only one real problem after the probe." in after
        assert "Two real problems, both fixable in one pass." not in after

    def test_reassembly_lists_each_adjustment_with_its_spot_check(
        self, out_dir
    ):
        _write_ledger(out_dir)
        self._revise(out_dir, {
            "schema": 1,
            "adjustments": [
                {
                    "action": "demote",
                    "id": "9f3a1c7d",
                    "fields": {"severity": "low"},
                    "rationale": "escaped one frame up",
                    "spot_check": "verified",
                },
                {
                    "action": "correct",
                    "id": "1b2c3d4e",
                    "fields": {"title": "Retry loop has no ceiling (v2)"},
                    "rationale": "clearer title",
                },
            ],
        })
        result = critic_adjustments.apply_adjustments(str(out_dir))
        assert result.get("status") != "refused", result

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
        self._revise(out_dir, {
            "schema": 1,
            "adjustments": [
                {
                    "adjustment_id": "applied-decision",
                    "action": "correct",
                    "id": "9f3a1c7d",
                    "fields": {"title": "Escaping is already present"},
                    "rationale": "verified against the source",
                    "spot_check": "verified",
                },
                {
                    "adjustment_id": "refuted-decision",
                    "action": "correct",
                    "id": "1b2c3d4e",
                    "fields": {"title": "This change does not apply"},
                    "rationale": "critic claim",
                    "rejected": True,
                    "rejection_reason": "the source contradicts the claim",
                    "spot_check": "refuted",
                },
            ],
        })
        critic_adjustments.apply_adjustments(str(out_dir))

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "## Critic Adjustment Decisions" in text
        assert "- `applied-decision` — verified" in text
        assert "- `refuted-decision` — refuted" in text

    def test_reassembly_projects_an_all_refuted_batch(self, out_dir):
        _write_ledger(out_dir)
        self._revise(out_dir, {
            "schema": 1,
            "adjustments": [
                {
                    "adjustment_id": "refuted-one",
                    "action": "correct",
                    "id": "9f3a1c7d",
                    "fields": {"title": "This change does not apply"},
                    "rationale": "critic claim",
                    "rejected": True,
                    "rejection_reason": "the source contradicts the claim",
                    "spot_check": "refuted",
                },
                {
                    "adjustment_id": "refuted-two",
                    "action": "correct",
                    "id": "1b2c3d4e",
                    "fields": {"title": "This change does not apply"},
                    "rationale": "critic claim",
                    "rejected": True,
                    "rejection_reason": "the source contradicts the claim",
                    "spot_check": "refuted",
                },
            ],
        })
        critic_adjustments.apply_adjustments(str(out_dir))

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "## Critic Adjustment Decisions" in text
        assert "- `refuted-one` — refuted" in text
        assert "- `refuted-two` — refuted" in text

    def test_withdrawn_narrative_renders_the_explicit_absence(self, out_dir):
        _write_ledger(out_dir)
        self._revise(out_dir, {
            "schema": 1,
            "adjustments": [
                {
                    "action": "demote",
                    "id": "9f3a1c7d",
                    "fields": {"severity": "low"},
                    "rationale": "escaped one frame up",
                    "spot_check": "verified",
                },
            ],
        })
        critic_adjustments.apply_adjustments(str(out_dir))

        assemble_review_record(str(out_dir), {})
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "No current assessment" in text
        assert "withdrawn under critic revision" in text
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
        findings["issues"][0]["description"] = (
            "Unescaped echo.\nSeverity-floor: high; see the notice helper."
        )
        findings["issues"][0]["recommendation"] = (
            "Severity-floor: high - wrap it in esc_html()."
        )
        findings["narrative_summary"] = (
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
        puts in the record — clearances, positives, and observations
        included. A field the record renders but the strip skips is a
        marker reaching the critic through the back door."""
        findings = _ledger()
        findings["clearances"] = [{
            "claim": "Severity-floor: high; nothing calls the helper",
            "method": "Severity-floor: medium - git grep",
            "evidence": "Severity-floor: low; 0 hits",
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
        assert "nothing calls the helper" in text
        assert "git grep" in text
        assert "0 hits" in text
        assert "the retry guard is tidy" in text
        assert "a tradeoff" in text

    def test_removed_by_critic_entries_are_covered(self, out_dir):
        findings = _ledger()
        findings["removed_by_critic"] = [{
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
        findings["issues"][0]["severity_floor"] = "high"
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
        findings["issues"][0]["recommendation"] = ["wrap it", "in esc_html()"]
        findings["issues"][1]["description"] = None
        findings["recommendations"]["immediate"] = [["escape", "the notice"]]
        _write_ledger(out_dir, findings)

        outcome, error = assemble_review_record(str(out_dir), {})

        assert error is None
        assert outcome["status"] == "complete"
        text = (out_dir / REVIEW_RECORD_MD).read_text()
        assert "wrap it\nin esc_html()" in text

    def test_sanitization_does_not_touch_the_ledger_on_disk(self, out_dir):
        findings = _ledger()
        findings["issues"][0]["description"] = (
            "Unescaped echo.\nSeverity-floor: high; see the helper."
        )
        _write_ledger(out_dir, findings)

        assemble_review_record(str(out_dir), {})

        on_disk = json.loads(
            (out_dir / critic_adjustments.FINDINGS_FILENAME).read_text()
        )
        assert "Severity-floor: high" in on_disk["issues"][0]["description"]


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
            "inline_coverage_gaps": {},
            "inline_coverage_claims": {},
            "inline_coverage_unscoped": [
                f"vendor/generated/module_{i:04d}.lock" for i in range(count)
            ],
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
    def _fingerprint(self, out_dir, notes=None):
        return _report_source_fingerprint(
            str(out_dir),
            critic_adjustments.FINDINGS_READ_OK,
            "degraded" if notes else "success",
            "REQUEST_CHANGES",
            "findings ledger",
            "STAND",
            notes or [],
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

        first = self._fingerprint(out_dir, ["first", "second"])
        second = self._fingerprint(out_dir, ["second", "first"])
        assert first != second

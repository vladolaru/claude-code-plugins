"""Tests for `assemble_review_record()` — the machine projection of the ledger.

`review-record.md` is assembled by the pipeline, never written or edited by
an agent. It composes the SAME renderers the other derived Markdown uses
(`render_review_body` for the findings/checks body,
`_render_file_review_section` for coverage) plus three thin additions
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
sys.path.insert(0, str(TESTS_DIR))

from review import briefings as briefings_mod
from review import critic_adjustments
from review import orchestration as orchestration_mod
from review.review_markdown import render_review_body
from helpers.review_fixtures import canonical_findings_ledger
from review.orchestration import (
    REVIEW_RECORD_MD,
    _report_source_fingerprint,
    assemble_review_record,
)


def _ledger(**overrides):
    """A minimal, valid `review-findings.json` document."""
    findings = canonical_findings_ledger(
        ("high", "medium"),
        checks=[
            {
                "id": "c1",
                "question": "Does anything else call the removed helper?",
                "method": "git grep across the repo",
                "result": "0 hits outside the deleted file",
                "source_reviewers": ["code-reviewer"],
            },
        ],
        reconciliation={
            "reviewing_agents": ["code-reviewer"],
            "dispatched_agents": ["code-reviewer"],
        },
    )
    findings.update({
        "plugin_version": "1.114.0",
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
        "recommendations": {
            "immediate": ["Escape the admin notice."],
            "important": [],
            "suggestions": [],
        },
    })
    findings.update(overrides)
    return findings


def _read(out_dir):
    """The one `FindingsRead` a step takes, as the callers now pass it."""
    return critic_adjustments.read_findings_file(
        str(out_dir / critic_adjustments.FINDINGS_FILENAME)
    )


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
    @pytest.mark.parametrize("reads, status", [(0, "unverified"), (None, "unmeasured")])
    def test_step_9_records_reconciliation_verification_without_rewriting_ledger(
        self, out_dir, monkeypatch, reads, status
    ):
        _write_ledger(out_dir)
        ledger_path = out_dir / "review-findings.json"
        before = ledger_path.read_bytes()
        monkeypatch.setattr(
            orchestration_mod, "_run_subprocess",
            lambda cmd, cwd=None, timeout=60: (json.dumps({
                "schema": 1, "subagent_usage": [
                    {"agent": "review-reconciliator", "repository_reads": reads},
                ],
            }), True),
        )
        state = {}
        orchestration_mod._orchestrate_step_9(
            "full", {}, state, {"git": {}}, str(out_dir),
        )
        assert state["reconciliation_verification"] == {
            "verified_concern_count": 2, "repository_reads": reads, "status": status,
        }
        assert state["review_record"]["status"] == "complete"
        assert ledger_path.read_bytes() == before
        record = (out_dir / REVIEW_RECORD_MD).read_text()
        assert ("Reconciliation is UNVERIFIED" in record) == (status == "unverified")
        assert "- Reconciliation verification: 2 verified concern(s)" in record
        if status == "unverified":
            # The UNVERIFIED call-out sits in the closing verdict line, not
            # merely somewhere in the document.
            tail = record.rsplit("Verdict — from", 1)[1]
            assert "Reconciliation is UNVERIFIED" in tail

    @pytest.mark.parametrize("state, note", [
        ({}, "- Reconciliation verification: not measured."),
    ])
    def test_reconciliation_verification_without_warning(self, out_dir, state, note):
        _write_ledger(out_dir)
        assemble_review_record(str(out_dir), state, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()
        assert note in text
        assert "Reconciliation is UNVERIFIED" not in text

    def test_writes_the_record_and_reports_a_complete_outcome(self, out_dir):
        _write_ledger(out_dir)

        outcome, error = assemble_review_record(str(out_dir), {}, _read(out_dir))

        assert error is None
        assert outcome == {
            "ran": True, "written": 1, "expected": 1, "status": "complete",
        }
        assert (out_dir / REVIEW_RECORD_MD).is_file()

    def test_sections_appear_in_the_documented_order(self, out_dir):
        _write_ledger(out_dir)
        state = {
            "change_purpose_items": {
                "structured": True, "problems": [], "context": [],
                "verify": [{"id": "V1", "text": "x", "source": "PR description", "carried_over": False}],
            },
            "file_review": {
                "agents_with_unclaimed_review_by_file": {
                    "src/starved.php": ["code-reviewer"]
                },
                "agents_claiming_review_by_file": {},
                "unscoped_files": ["package-lock.json"],
            },
        }

        assemble_review_record(str(out_dir), state, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        order = [
            "# Review Record",
            "## Executive Summary",
            "## Assessment",
            "## High Findings",
            "## Verified Checks",
            "## Run notes",
            "## Verify items",
            "## Review coverage",
            "Verdict — from the findings ledger",
        ]
        positions = [text.index(marker) for marker in order]
        assert positions == sorted(positions), text

    def test_verify_items_table_names_who_settled_each_claim(self, out_dir):
        ledger = _ledger()
        ledger["checks"][0]["verifies"] = ["V1"]
        _write_ledger(out_dir, ledger)
        state = {"change_purpose_items": {
            "structured": True, "problems": [], "context": [],
            "verify": [
                {"id": "V1", "text": "Nothing else calls the removed helper", "source": "PR description", "carried_over": False},
                {"id": "V2", "text": "The retry loop | has a ceiling", "source": "inferred from the diff", "carried_over": True},
            ],
        }}
        assemble_review_record(str(out_dir), state, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()
        assert "## Verify items" in text
        assert "| Item | Claim | Source | Settled by |" in text
        assert "| V1 | Nothing else calls the removed helper | PR description | `c1` (code-reviewer) |" in text
        assert "| V2 (carried over) | The retry loop \\| has a ceiling | inferred from the diff | no surviving check or confirmed note — unverified |" in text
        assert text.index("## Run notes") < text.index("## Verify items") < text.index("Verdict — from the findings ledger")

    def test_a_confirmed_note_settles_an_item_in_the_table(self, out_dir):
        """WooCommerce PR #68063: V2 and V3 read "unverified" although the
        reconciliator had reproduced the orchestrator's note that settled
        them; the critic then spent four minutes re-verifying both."""
        ledger = _ledger()
        ledger["orchestrator_notes"] = [
            {"id": "n1", "note": "lockfile regenerates with all three sections", "outcome": "confirmed",
             "evidence": "deleted pnpm-lock.yaml and regenerated it", "verifies": ["V2"]},
            {"id": "n2", "note": "claim", "outcome": "refuted", "evidence": "e"},
        ]
        _write_ledger(out_dir, ledger)
        state = {"change_purpose_items": {
            "structured": True, "problems": [], "context": [],
            "verify": [
                {"id": "V1", "text": "claim one", "source": "PR description", "carried_over": False},
                {"id": "V2", "text": "claim two", "source": "PR description", "carried_over": False},
            ],
        }}
        assemble_review_record(str(out_dir), state, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()
        assert "| V1 | claim one | PR description | no surviving check or confirmed note — unverified |" in text
        assert "| V2 | claim two | PR description | `n1` (review-reconciliator) |" in text

    def test_a_citation_of_an_undeclared_item_is_listed_under_the_table(self, out_dir):
        ledger = _ledger()
        ledger["checks"][0]["verifies"] = ["V9"]
        _write_ledger(out_dir, ledger)
        state = {"change_purpose_items": {
            "structured": True, "problems": [], "context": [],
            "verify": [{"id": "V1", "text": "claim", "source": "PR description", "carried_over": False}],
        }}
        assemble_review_record(str(out_dir), state, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()
        assert "| V1 | claim | PR description | no surviving check or confirmed note — unverified |" in text
        assert "- `c1` (code-reviewer) cites V9, which the change purpose does not declare." in text
        assert text.index("| V1 |") < text.index("cites V9")

    def test_no_verify_items_means_no_table(self, out_dir):
        _write_ledger(out_dir)
        for state in ({}, {"change_purpose_items": None},
                      {"change_purpose_items": {"structured": False, "verify": [], "context": [], "problems": []}}):
            assemble_review_record(str(out_dir), state, _read(out_dir))
            assert "## Verify items" not in (out_dir / REVIEW_RECORD_MD).read_text()

    def test_header_carries_verdict_and_severity_counts(self, out_dir):
        _write_ledger(out_dir)

        assemble_review_record(str(out_dir), {}, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "**Verdict:** REQUEST_CHANGES" in text
        assert "**Total Findings:** 2" in text
        assert "- High: 1" in text
        assert "- Medium: 1" in text
        assert "Verdict — from the findings ledger: `request_changes`" in text
        assert "REQUEST_CHANGES" in text.rsplit("Verdict — from", 1)[1]

    def test_findings_body_is_byte_identical_to_the_shared_renderer(
        self, out_dir
    ):
        findings = _ledger()
        _write_ledger(out_dir, findings)

        assemble_review_record(str(out_dir), {}, _read(out_dir))
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
            "file_review": {
                "agents_with_unclaimed_review_by_file": gaps,
                "agents_claiming_review_by_file": claims,
                "unscoped_files": unscoped,
            },
        }

        assemble_review_record(str(out_dir), state, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert briefings_mod._render_file_review_section(
            state["file_review"]
        ) in text

    def test_unmeasured_coverage_renders_no_coverage_section(self, out_dir):
        _write_ledger(out_dir)

        assemble_review_record(str(out_dir), {}, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "## Review coverage" not in text

    def test_run_notes_state_the_hosts_the_run_verified_against(self, out_dir):
        _write_ledger(out_dir)
        (out_dir / "review-context.json").write_text(json.dumps({"host_context": {
            "resolved": [
                {"name": "wordpress", "kind": "runtime-host", "source": "ecosystem-cache", "path": "/x/cache/wordpress/latest",
                 "version": "7.2-alpha-63166-src", "version_freshness": "2026-09-04T00:04:08Z",
                 "notes": {"commit": "474555a85c052de90ddd22d4abdf163e678b88ac", "branch": "trunk", "declared_minimum": "7.0"}},
                {"name": "vendor", "kind": "library-dep", "source": "vendor-inspection", "path": "/x/repo/vendor", "notes": {}},
            ],
            "unresolved": [{"name": "jetpack", "reason": "declared_in_plugin_headers", "version": None}],
            "banner": {"degraded": True, "reason": "partial_unresolved", "message": "m"},
            "diagnostics": {"scan_roots": 1},
        }}))
        assemble_review_record(str(out_dir), {}, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()
        # Loosened to fragments (wording is prose); the load-bearing assert
        # is that the cache path never leaks into owner-read text.
        assert "wordpress via ecosystem-cache" in text
        assert "7.2-alpha-63166-src" in text
        assert "474555a85c05" in text
        assert "- Unresolved hosts: jetpack (declared_in_plugin_headers)." in text
        assert "/x/cache/wordpress/latest" not in text

        # Two absence spellings: no context file at all, and a context
        # file that resolved nothing.
        empty_dir = out_dir.parent / f"{out_dir.name}-no-context"
        empty_dir.mkdir()
        _write_ledger(empty_dir)
        assemble_review_record(str(empty_dir), {}, _read(empty_dir))
        assert "- Host context: not recorded." in (empty_dir / REVIEW_RECORD_MD).read_text()

        (out_dir / "review-context.json").write_text(json.dumps({
            "host_context": {"resolved": [], "unresolved": [], "banner": None, "diagnostics": {}}
        }))
        assemble_review_record(str(out_dir), {}, _read(out_dir))
        assert "- Host context: no runtime host resolved." in (out_dir / REVIEW_RECORD_MD).read_text()

    def test_the_record_reads_the_host_identity_current_at_assembly(self, out_dir):
        """A step-3 handoff may re-resolve hosts after a dependency refresh
        and replace `review-context.json`; the record reads that artifact
        when it is assembled, so it cannot carry a stale copy."""
        _write_ledger(out_dir)
        context_path = out_dir / "review-context.json"
        context_path.write_text(json.dumps({"host_context": {
            "resolved": [{"name": "wordpress", "kind": "runtime-host", "source": "ecosystem-cache",
                          "path": "/local/wordpress", "version": "7.1", "notes": {"commit": "old123"}}],
            "unresolved": [],
        }}))
        assemble_review_record(str(out_dir), {}, _read(out_dir))
        assert "version 7.1, commit old123" in (out_dir / REVIEW_RECORD_MD).read_text()

        context_path.write_text(json.dumps({"host_context": {
            "resolved": [{"name": "wordpress", "kind": "runtime-host", "source": "ecosystem-cache",
                          "path": "/local/wordpress", "version": "7.2", "notes": {"commit": "new456"}}],
            "unresolved": [],
        }}))
        assemble_review_record(str(out_dir), {}, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()
        assert "- Host context: wordpress via ecosystem-cache (version 7.2, commit new456)." in text
        assert "old123" not in text
        assert "/local/wordpress" not in text

    def test_run_notes_report_dispatch_summary_and_warnings(self, out_dir):
        """The `- Dispatch:` and `- ⚠ Dispatch warning:` lines are a
        separate branch of the same run-notes builder from the dependency
        refresh line covered by ``dependency_refresh_note_table`` below;
        nothing else in the suite pins their rendering."""
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

        assemble_review_record(str(out_dir), state, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "## Run notes" in text
        assert "12 dispatched" in text
        assert "9 skipped" in text
        assert "unrecognized source language: .zig" in text

    DEPENDENCY_REFRESH_NOTES = (
        pytest.param(
            {}, "Dependency refresh: not requested.", id="not_requested",
        ),
        pytest.param(
            {"dependency_refresh_precheck": {
                "tracked_files_dirty": True, "dirty_files": [],
            }},
            "Dependency refresh: refused before execution because the "
            "tracked worktree was dirty.",
            id="refused_dirty",
        ),
        pytest.param(
            {"dependency_refresh_precheck": {
                "tracked_files_dirty": None, "dirty_files": [],
            }},
            "Dependency refresh: refused before execution because the "
            "tracked worktree state was unknown.",
            id="refused_unknown",
        ),
        pytest.param(
            {"dependency_refresh_precheck": {
                "tracked_files_dirty": False, "dirty_files": [],
            }},
            "Dependency refresh: requested but not recorded.",
            id="requested_not_recorded",
        ),
        pytest.param(
            {
                "dependency_refresh_precheck": {
                    "tracked_files_dirty": False, "dirty_files": [],
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
            },
            "Dependency refresh: partial; 1 command(s) reported; final "
            "tracked files dirty: unknown.",
            id="recorded_report",
        ),
    )

    @pytest.mark.parametrize(("state", "fragment"), DEPENDENCY_REFRESH_NOTES)
    def test_run_notes_report_dependency_refresh(self, out_dir, state, fragment):
        _write_ledger(out_dir)

        assemble_review_record(str(out_dir), state, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert fragment in text

    @pytest.mark.parametrize(
        ("state", "expected_line"),
        [
            pytest.param(
                {"agents": {
                    "discarded_drafts": [
                        "code-reviewer", "security-reviewer",
                    ],
                }},
                "- Discarded reviewer drafts: code-reviewer, "
                "security-reviewer.",
                id="non-empty",
            ),
            pytest.param(
                {"agents": {"discarded_drafts": []}},
                None,
                id="empty",
            ),
            pytest.param({}, None, id="missing"),
        ],
    )
    def test_run_notes_render_discarded_drafts_only_when_nonempty(
        self, out_dir, state, expected_line
    ):
        _write_ledger(out_dir)

        assemble_review_record(str(out_dir), state, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        if expected_line is None:
            assert "Discarded reviewer drafts" not in text
        else:
            assert expected_line in text


class TestRecordIsAProjection:
    """Re-assembly after `adjudicate` shows the post-critic ledger."""

    @staticmethod
    def _revise(
        out_dir,
        adjustments,
        *,
        verified=(),
        refuted=(),
        assessment="Post-critic assessment.",
    ):
        proposal = critic_adjustments.prepare_proposal({
            "schema": 2,
            "adjustments": adjustments,
        })
        critic_adjustments.write_critic_verdict(
            str(out_dir), "REVISE", proposal
        )
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
        return proposal, critic_adjustments.adjudicate(str(out_dir), request)

    def test_reassembly_reflects_adjusted_severity_and_verdict(self, out_dir):
        _write_ledger(out_dir)
        assemble_review_record(str(out_dir), {}, _read(out_dir))
        before = (out_dir / REVIEW_RECORD_MD).read_text()
        assert "**Verdict:** REQUEST_CHANGES" in before

        self._revise(out_dir, [{
            "action": "demote",
            "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"},
            "rationale": "the value is escaped one frame up",
        }], verified=(0,), assessment="Only one real problem after the probe.")

        assemble_review_record(str(out_dir), {}, _read(out_dir))
        after = (out_dir / REVIEW_RECORD_MD).read_text()

        # Recomputed verdict (one medium left → comment), post-adjustment
        # severities, and the orchestrator's replacement assessment.
        assert "**Verdict:** COMMENT" in after
        assert "## Low Findings" in after
        assert "Only one real problem after the probe." in after
        assert "Two real problems, both fixable in one pass." not in after

    def test_reassembly_lists_each_adjustment_with_its_outcome(
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
        assert result["applied"] == 2, result

        assemble_review_record(str(out_dir), {}, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "## Critic Adjustment Decisions" in text
        assert "verified" in text
        # An entry with no stated outcome is recorded as unprobed, never
        # absorbed into a batch-level claim.
        assert "not_checked" in text


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

        assemble_review_record(str(out_dir), {}, _read(out_dir))
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
            {
                "file": "src/a.php",
                "note": "Severity-floor: low; a tradeoff",
                "category": "tradeoff",
            },
        ]
        _write_ledger(out_dir, findings)

        assemble_review_record(str(out_dir), {}, _read(out_dir))
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
            "id": "f3",
            "title": "Severity-floor: high; withdrawn finding",
            "severity": "high",
            "category": "correctness",
            "file": "src/x.php",
            "line": 1,
            "description": "The path initially appeared reachable.",
            "recommendation": "No change after the source probe.",
            "confidence": 0.9,
            "critic_adjustment": {
                "action": "remove",
                "rationale": "Severity-floor: high; not reachable",
            },
        }]
        findings["checks_removed_by_critic"] = [{
            "id": "c9",
            "question": "Is the helper called?",
            "method": "git grep",
            "result": "0 hits",
            "source_reviewers": ["code-reviewer"],
            "critic_adjustment": {
                "action": "remove",
                "rationale": "Severity-floor: medium; the check was moot",
            },
        }]
        findings["applied_critic_adjustments"] = [
            {"adjustment_id": "remove-f3", "outcome": "verified"},
            {"adjustment_id": "remove-c9", "outcome": "verified"},
        ]
        findings["meta"]["next_finding_number"] = 4
        findings["meta"]["next_check_number"] = 10
        _write_ledger(out_dir, findings)

        assemble_review_record(str(out_dir), {}, _read(out_dir))
        text = (out_dir / REVIEW_RECORD_MD).read_text()

        assert "Severity-floor:" not in text
        assert "withdrawn finding" in text
        assert "not reachable" in text
        assert "the check was moot" in text

    def test_non_string_finding_fields_fail_closed_without_crashing(
        self, out_dir
    ):
        """Malformed content cannot reach the record renderer."""
        findings = _ledger()
        findings["findings"][0]["recommendation"] = ["wrap it", "in esc_html()"]
        findings["findings"][1]["description"] = None
        findings["recommendations"]["immediate"] = [["escape", "the notice"]]
        _write_ledger(out_dir, findings)

        outcome, error = assemble_review_record(str(out_dir), {}, _read(out_dir))

        assert error == "review-findings.json unreadable (invalid)"
        assert outcome["status"] == "failed"
        assert not (out_dir / REVIEW_RECORD_MD).exists()

    def test_sanitization_does_not_touch_the_ledger_on_disk(self, out_dir):
        findings = _ledger()
        findings["findings"][0]["description"] = (
            "Unescaped echo.\nSeverity-floor: high; see the helper."
        )
        _write_ledger(out_dir, findings)

        assemble_review_record(str(out_dir), {}, _read(out_dir))

        on_disk = json.loads(
            (out_dir / critic_adjustments.FINDINGS_FILENAME).read_text()
        )
        assert "Severity-floor: high" in on_disk["findings"][0]["description"]


class TestRecordFailureModes:
    def test_no_ledger_is_a_measured_zero_not_a_failure(self, out_dir):
        outcome, error = assemble_review_record(str(out_dir), {}, _read(out_dir))

        assert error is None
        assert outcome == {
            "ran": True, "written": 0, "expected": 0, "status": "complete",
        }
        assert not (out_dir / REVIEW_RECORD_MD).exists()

    @pytest.mark.parametrize(
        "ledger_text",
        [
            pytest.param("{ nope", id="unparseable-json"),
            pytest.param(
                json.dumps({"verdict": "approve"}), id="missing-required-keys",
            ),
        ],
    )
    def test_unreadable_ledger_reports_failed_and_writes_nothing(
        self, out_dir, ledger_text
    ):
        (out_dir / critic_adjustments.FINDINGS_FILENAME).write_text(ledger_text)

        outcome, error = assemble_review_record(str(out_dir), {}, _read(out_dir))

        assert outcome["status"] == "failed"
        assert outcome["written"] == 0
        assert error
        assert not (out_dir / REVIEW_RECORD_MD).exists()


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
            "file_review": {
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

        assemble_review_record(str(out_dir), state, _read(out_dir))
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
        assemble_review_record(str(out_dir), {}, _read(out_dir))
        first = (out_dir / REVIEW_RECORD_MD).read_text()

        def boom(*_args, **_kwargs):
            raise RuntimeError("render exploded")

        monkeypatch.setattr(
            orchestration_mod, "_render_record_body", boom, raising=True
        )
        outcome, error = assemble_review_record(str(out_dir), {}, _read(out_dir))

        assert outcome["status"] == "failed"
        assert "render exploded" in str(error)
        assert (out_dir / REVIEW_RECORD_MD).read_text() == first

    def test_no_temp_files_survive_a_successful_assembly(self, out_dir):
        _write_ledger(out_dir)

        assemble_review_record(str(out_dir), {}, _read(out_dir))

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

    def test_exact_record_and_ledger_bytes_are_bound(self, out_dir):
        _write_ledger(out_dir)
        assemble_review_record(str(out_dir), {}, _read(out_dir))
        baseline = self._fingerprint(out_dir)

        record = out_dir / REVIEW_RECORD_MD
        record.write_bytes(record.read_bytes() + b"\n")
        assert self._fingerprint(out_dir) != baseline

        assemble_review_record(str(out_dir), {}, _read(out_dir))
        findings = out_dir / critic_adjustments.FINDINGS_FILENAME
        findings.write_bytes(findings.read_bytes() + b"\n")
        assert self._fingerprint(out_dir) != baseline

    def test_ordered_degradation_facts_are_bound(self, out_dir):
        _write_ledger(out_dir)
        assemble_review_record(str(out_dir), {}, _read(out_dir))

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
        assemble_review_record(str(out_dir), {}, _read(out_dir))

        first = self._fingerprint(out_dir, [{
            "code": "findings_markdown_render_failed", "message": "boom one",
        }])
        second = self._fingerprint(out_dir, [{
            "code": "findings_markdown_render_failed", "message": "boom two",
        }])
        assert first == second

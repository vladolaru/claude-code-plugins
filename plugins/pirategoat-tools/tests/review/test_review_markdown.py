"""Markdown rendered from a review artifact: `scripts/review/review_markdown.py`.

Split out of `tests/review/agent/test_output.py` when the renderers left
`agent/output.py`. The builder appears here only as a document factory —
the shortest honest way to obtain a canonical dict to render. What is
under test in every case is the rendering: the sections a document
produces, the CLI that prints them, and the materializer that writes them
beside the JSON they came from.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review import critic_adjustments
from review.agent.output import ReviewOutputBuilder, finalize_review
from review.review_markdown import materialize_markdown, render_markdown, render_review_body
from review.reviewer_lifecycle import review_paths, reviewer_markdown_path

sys.path.insert(0, str(TESTS_DIR))
from helpers.review_fixtures import (
    apply_schema,
    canonical_findings_ledger,
    canonical_review_document,
    rejected_schema_values,
    write_canonical_assignment,
)


def _save_and_finalize(builder, output_dir):
    builder._bind(str(output_dir), base_digest=None)
    saved = builder.save_draft()
    finalize_review(
        str(output_dir), builder.reviewer, saved["review_digest"]
    )
    return saved


class TestRenderMarkdown:
    """Markdown is a pure function of the canonical JSON dict."""

    @staticmethod
    def _rich_builder():
        b = ReviewOutputBuilder(pr_id="7", reviewer="security")
        b.add_finding("high", "Title A", "a.py", "desc", "rec", line=3)
        b.add_finding("info", "Note B", "b.py", "desc", "rec", line=None)
        b.add_observation("c.py", "an observation")
        b.add_positive_observation("something good")
        b.record_check(
            question="Does X remain?", method="grep -rn X", result="0 hits"
        )
        return b

    def test_findings_grouped_by_severity(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_finding("low", "Low Issue", "a.py", "desc", "rec", line=1)
        b.add_finding("critical", "Critical Issue", "b.py", "desc", "rec", line=2)
        b.add_finding("high", "High Issue", "c.py", "desc", "rec", line=3)
        md = render_markdown(b.to_dict())
        # Critical section should appear before High, High before Low
        crit_pos = md.index("## Critical Findings")
        high_pos = md.index("## High Findings")
        low_pos = md.index("## Low Findings")
        assert crit_pos < high_pos < low_pos

    def test_info_findings_render_in_markdown(self):
        """Info findings count toward total_findings, so Markdown must show them —
        omitting them reports `Total Findings: 1` with no visible finding."""
        b = ReviewOutputBuilder(pr_id="1", reviewer="pr")
        b.add_finding("info", "Anchored info finding", "a.py", "desc", "rec", line=3)
        md = render_markdown(b.to_dict())
        assert "## Info Findings" in md
        assert "Anchored info finding" in md

    def test_non_empty_source_cited_renders_as_upstream_evidence(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="ecosystem")
        b.add_finding(
            "high", "Upstream mismatch", "src/plugin.php", "desc", "rec",
            line=3,
            source_cited="wordpress@unknown:src/wp-includes/post.php:1234",
        )

        md = render_markdown(b.to_dict())

        assert "**Upstream evidence:** `wordpress@unknown:src/wp-includes/post.php:1234`" in md

    def test_round_trips_through_serialized_json(self):
        """Rendering from the FILE representation — what materialization
        does — must equal rendering from the live builder."""
        b = self._rich_builder()
        assert render_markdown(json.loads(json.dumps(b.to_dict()))) == render_markdown(b.to_dict())

    def test_summary_without_advisory_measurement_still_renders(self):
        data = self._rich_builder().to_dict()
        data["summary"].pop("suppressed_advisory_finding_count")
        data["summary"].pop("verdict_without_advisory", None)

        rendered = render_markdown(data)

        assert "# Security Review" in rendered
        assert "Advisory suppression" not in rendered

    @pytest.mark.parametrize(
        ("unclaimed_review_files", "expected"),
        [
            pytest.param(
                ["src/unread.py", "docs/not checked.md"],
                "**Not reviewed (budget):** `src/unread.py`, "
                "`docs/not checked.md`\n\n",
                id="non-empty",
            ),
            pytest.param([], None, id="empty"),
            pytest.param(None, None, id="none"),
        ],
    )
    def test_derived_coverage_gap_renders_only_when_non_empty(
        self, unclaimed_review_files, expected
    ):
        data = self._rich_builder().to_dict()
        data["unclaimed_review_files"] = unclaimed_review_files

        rendered = render_markdown(data)

        if expected is None:
            assert "**Not reviewed (budget):**" not in rendered
        else:
            assert expected in rendered

    # -- Moved from agent/test_output.py (G7): the builder file is not the
    # renderer's test file. --

    def test_markdown_renders_severity_floor(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="woo-regression")
        b.add_finding(
            "medium", "Title", "f.php", "desc", "rec", line=1,
            severity_floor="medium",
        )

        assert "**Severity floor:** medium" in render_markdown(b.to_dict())

    def test_renders_checks_performed_with_method(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="a11y")
        b.record_check(
            question="Does CSS depend on the label?",
            method="grep 'th label' admin.scss",
            result="No dependencies found.",
        )
        md = render_markdown(b.to_dict())
        assert "## Checks Performed" in md
        assert "Does CSS depend on the label?" in md
        assert "grep 'th label' admin.scss" in md

    def test_observations_in_markdown(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="sec")
        b.add_observation("f.py", "File lacks CSRF protection")
        md = render_markdown(b.to_dict())
        assert "Observations" in md
        assert "File lacks CSRF protection" in md

    def test_file_scoped_finding_renders_under_severity_section(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="js-tests")
        b.add_finding(
            "high", "whole-file has no test", "src/foo.ts", "desc", "rec",
            category="missing-coverage",
        )
        md = render_markdown(b.to_dict())
        assert "## High Findings" in md
        assert "whole-file has no test" in md
        assert "`src/foo.ts` (file-scoped)" in md

    def test_renders_as_an_assessment_section(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="reconciliator")
        b.set_assessment("Two sentences of judgment.")
        rendered = render_markdown(b.to_dict())
        assert "## Assessment\n\nTwo sentences of judgment." in rendered

    def test_absent_prose_renders_no_assessment_section(self):
        rendered = render_markdown(
            ReviewOutputBuilder(pr_id="1", reviewer="pr").to_dict()
        )
        assert "## Assessment" not in rendered

    def test_critical_advisory_suppression_states_the_stricter_counterfactual(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_finding(
            severity="critical", title="x", file="a.php",
            description="d", recommendation="r", line=5,
            channel="advisory",
        )

        rendered = render_markdown(b.to_dict())

        assert "Advisory suppression:** 1 finding excluded" in rendered
        assert "verdict without suppression: BLOCK" in rendered

    def test_advisory_suppression_without_a_counterfactual_still_states_the_count(self):
        b = ReviewOutputBuilder(pr_id="1", reviewer="repo-reuse")
        b.add_finding(
            severity="low", title="x", file="a.php",
            description="d", recommendation="r", line=5,
            channel="advisory",
        )

        assert (
            "Advisory suppression:** 1 finding excluded"
            in render_markdown(b.to_dict())
        )


class TestMaterializeMarkdown:
    def test_writes_md_beside_every_review_json(self):
        with tempfile.TemporaryDirectory() as d:
            for reviewer in ("security", "performance"):
                b = ReviewOutputBuilder(pr_id="1", reviewer=reviewer)
                b.add_finding("high", "T", "f.py", "d", "r", line=1)
                write_canonical_assignment(d, reviewer)
                _save_and_finalize(b, d)
            written = materialize_markdown(d)
            assert sorted(Path(p).parent.name for p in written) == [
                "performance", "security",
            ]
            with open(review_paths(d, "security").final) as f:
                data = json.load(f)
            md_text = Path(reviewer_markdown_path(d, "security")).read_text()
            assert md_text == render_markdown(data)
            # Materializing a second time is idempotent.
            assert materialize_markdown(d) == written

    def test_skips_malformed_json_without_raising(self):
        with tempfile.TemporaryDirectory() as d:
            broken = Path(review_paths(d, "broken").final)
            broken.parent.mkdir(parents=True, exist_ok=True)
            broken.write_text("{ not json")
            write_canonical_assignment(d, "security")
            _save_and_finalize(
                ReviewOutputBuilder(pr_id="1", reviewer="security"), d
            )
            written = materialize_markdown(d)
            assert [Path(p).parent.name for p in written] == ["security"]
            assert not Path(reviewer_markdown_path(d, "broken")).exists()

    def test_skips_valid_json_missing_required_keys(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            empty = Path(review_paths(d, "empty").final)
            empty.parent.mkdir(parents=True, exist_ok=True)
            empty.write_text("{}")
            written = materialize_markdown(d)
            assert written == []
            assert not Path(reviewer_markdown_path(d, "empty")).exists()
            assert "skipped review.json" in capsys.readouterr().err

    @pytest.mark.parametrize("schema", rejected_schema_values(2))
    def test_skips_a_final_review_at_any_other_schema(self, capsys, schema):
        """A final review the canonical reader refuses renders no Markdown.

        The materializer is the observable end of `load_review_document`'s
        gate: an unrenderable review leaves no `.md` behind and says so on
        stderr, rather than publishing a projection of a document nothing
        vouched for.
        """
        with tempfile.TemporaryDirectory() as d:
            builder = ReviewOutputBuilder(pr_id="1", reviewer="security")
            write_canonical_assignment(d, "security")
            _save_and_finalize(builder, d)
            path = Path(review_paths(d, "security").final)
            path.write_text(json.dumps(
                apply_schema(json.loads(path.read_text()), schema)
            ))

            written = materialize_markdown(d)

            assert written == []
            assert not Path(reviewer_markdown_path(d, "security")).exists()
            assert "skipped review.json" in capsys.readouterr().err

    def test_arbitrary_json_is_not_a_renderable_artifact_family(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "arbitrary.json")
            path.write_text(json.dumps(
                ReviewOutputBuilder(
                    pr_id="1", reviewer="security"
                ).to_dict()
            ))

            written = materialize_markdown(d, suffix="arbitrary.json")

            assert written == []
            assert not Path(d, "arbitrary.md").exists()
            assert "unsupported review artifact" in capsys.readouterr().err

    def test_canonical_looking_legacy_flat_artifacts_are_not_rendered(
        self, tmp_path, capsys
    ):
        artifact = tmp_path / "security-review.json"
        artifact.write_text(json.dumps(canonical_review_document("security")))

        assert materialize_markdown(
            tmp_path, suffix="security-review.json"
        ) == []
        assert not artifact.with_suffix(".md").exists()
        assert "unsupported review artifact" in capsys.readouterr().err

    def test_render_cli_prints_markdown(self):
        render_py = SCRIPTS_DIR / "review" / "review_markdown.py"
        assert render_py.is_file(), render_py  # layout guard
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            b.add_finding("high", "CLI Title", "f.py", "d", "r", line=1)
            write_canonical_assignment(d, "security")
            _save_and_finalize(b, d)
            result = subprocess.run(
                [sys.executable, str(render_py), "render",
                 review_paths(d, "security").final],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert "CLI Title" in result.stdout
            assert "## Executive Summary" in result.stdout

    def test_materialize_cli_prints_written_paths(self):
        render_py = SCRIPTS_DIR / "review" / "review_markdown.py"
        assert render_py.is_file(), render_py
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            write_canonical_assignment(d, "security")
            _save_and_finalize(b, d)
            md_path = Path(reviewer_markdown_path(d, "security"))
            assert not md_path.exists()  # finalization publishes canonical JSON only
            result = subprocess.run(
                [sys.executable, str(render_py), "materialize", d],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert str(md_path) in result.stdout
            assert md_path.is_file()


def _reconciliator_findings(severity="high", title="Real problem", **extra):
    """One reconciliator document carrying a single finding."""
    b = ReviewOutputBuilder(pr_id="9", reviewer="reconciliator")
    b.add_finding(severity, title, "a.py", "d", "r", line=4)
    data = b.to_dict()
    data.update(extra)
    return data


class TestReconciliationSectionsRender:
    """Every section the reconciliator's old narrative template carried has
    to come out of the renderer, or migrating to a script render loses it."""

    def test_pipeline_metrics_line_renders_from_meta_reconciliation(self):
        """Every number on the line comes from one canonical ledger, so a
        renderer reading a retired key would print a zero the ledger's own
        reconciliation block contradicts."""
        counts = {
            "input_finding_count": 12,
            "contributing_agent_count": 4,
            "grouped_concern_count": 8,
            "false_positive_concern_count": 3,
            "out_of_scope_concern_count": 1,
            "verified_concern_count": 4,
        }
        rendered = render_markdown(
            canonical_findings_ledger(("high",) * 4, reconciliation=counts)
        )
        assert "**Pipeline:** 12 findings from 4 reviewing agents" in rendered
        assert "\u2192 4 verified findings" in rendered
        assert "8 concerns after grouping" in rendered
        assert "3 false positives dropped" in rendered
        assert "1 out-of-scope dropped" in rendered

    def test_not_applicable_agents_are_reported_with_reasons(self):
        rendered = render_markdown(canonical_findings_ledger(
            ("high",),
            reconciliation={
                "not_applicable_agents": [
                    {"name": "a11y-review", "skip_reason": "no UI changed"},
                ],
                "dispatched_agents": ["security-review", "a11y-review"],
            },
        ))
        assert "1 agent returned not-applicable" in rendered
        assert "a11y-review (no UI changed)" in rendered

    def test_missing_reconciliation_metrics_render_nothing(self):
        assert "**Pipeline:**" not in render_markdown(_reconciliator_findings())

    def test_recommendations_render_by_priority(self):
        b = ReviewOutputBuilder(pr_id="9", reviewer="reconciliator")
        b.add_recommendation("immediate", "Fix the escaping")
        b.add_recommendation("important", "Add a regression test")
        b.add_recommendation("suggestions", "Rename the helper")
        rendered = render_markdown(b.to_dict())
        assert "## Recommendations\n" in rendered
        assert "**Immediate:**" in rendered
        assert "- Fix the escaping" in rendered
        assert "**Important:**" in rendered
        assert "- Add a regression test" in rendered
        assert "**Suggestions:**" in rendered
        assert "- Rename the helper" in rendered

    def test_degraded_host_context_banner_leads_the_body(self):
        """Directly under the title — the H1 stays first so one grader rule
        covers every rendering (see TestRendererFaithfulness)."""
        data = _reconciliator_findings(host_context_banner={
            "degraded": True,
            "reason": "partial_unresolved",
            "message": "WooCommerce source was not resolved.",
            "unresolved": [{"name": "woocommerce", "reason": "not found"}],
        })
        rendered = render_markdown(data)
        assert rendered.startswith("# Reconciliator Review - PR #9\n\n")
        assert (
            "> **⚠ Host Context Banner:** "
            "WooCommerce source was not resolved.\n\n"
            "## Executive Summary"
        ) in rendered

    def test_undegraded_banner_is_not_rendered(self):
        data = _reconciliator_findings(host_context_banner={
            "degraded": False, "reason": "", "message": "all resolved",
            "unresolved": [],
        })
        assert not render_markdown(data).startswith(">")


class TestMaterializeFindingsMarkdown:
    """One materializer, parameterized — never a second render path."""

    def test_default_suffix_ignores_unfinalized_reviewer_drafts(self):
        with tempfile.TemporaryDirectory() as d:
            write_canonical_assignment(d, "security")
            builder = ReviewOutputBuilder(pr_id="1", reviewer="security")
            builder._bind(d, base_digest=None)
            builder.save_draft()

            assert materialize_markdown(d) == []
            assert not Path(reviewer_markdown_path(d, "security")).exists()

    def test_suffix_selects_the_findings_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            # No ledger yet: writes nothing, does not raise.
            assert materialize_markdown(d, suffix="review-findings.json") == []

            data = canonical_findings_ledger(("high",))
            Path(d, "review-findings.json").write_text(json.dumps(data))
            assert critic_adjustments.read_findings_file(
                Path(d, "review-findings.json")
            ).status == critic_adjustments.FINDINGS_READ_OK
            written = materialize_markdown(d, suffix="review-findings.json")
            assert [os.path.basename(p) for p in written] == [
                "review-findings.md",
            ]
            assert Path(d, "review-findings.md").read_text() == render_markdown(
                data
            )

    def test_default_suffix_ignores_the_findings_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            write_canonical_assignment(d, "security")
            _save_and_finalize(
                ReviewOutputBuilder(pr_id="1", reviewer="security"), d
            )
            Path(d, "review-findings.json").write_text(
                json.dumps(
                    ReviewOutputBuilder(
                        pr_id="1", reviewer="reconciliator"
                    ).to_dict()
                )
            )
            written = materialize_markdown(d)
            assert [Path(p).parent.name for p in written] == ["security"]
            assert not Path(d, "review-findings.md").exists()

    def test_canonical_reader_rejection_writes_no_findings_markdown(
        self, tmp_path, capsys
    ):
        data = canonical_findings_ledger(("high",))
        data["verdict"] = "APPROVE"
        findings_path = tmp_path / "review-findings.json"
        findings_path.write_text(json.dumps(data))

        assert critic_adjustments.read_findings_file(
            findings_path
        ).status == critic_adjustments.FINDINGS_READ_INVALID

        assert materialize_markdown(
            str(tmp_path), suffix="review-findings.json"
        ) == []
        assert not (tmp_path / "review-findings.md").exists()
        assert "skipped review-findings.json" in capsys.readouterr().err

    def test_materialize_cli_accepts_the_suffix(self):
        """The on-demand recovery path step 11 prints has to be able to
        render the findings ledger, not only the per-reviewer family."""
        render_py = SCRIPTS_DIR / "review" / "review_markdown.py"
        with tempfile.TemporaryDirectory() as d:
            Path(d, "review-findings.json").write_text(json.dumps(
                canonical_findings_ledger(("high",))
            ))
            result = subprocess.run(
                [sys.executable, str(render_py), "materialize", d,
                 "--suffix", "review-findings.json"],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert "review-findings.md" in result.stdout
            assert Path(d, "review-findings.md").is_file()


class TestAssessmentProvenance:
    """`## Assessment` is prose about a ledger that keeps changing.

    The reconciler writes it; the decision critic then mutates the findings
    it summarizes. The critic's vocabulary reaches every finding but no
    ledger-level prose, so a withdrawn or demoted finding described in the
    Assessment survives every correction channel — the rendered file
    contradicting the list printed beneath it.
    """

    def test_prose_carries_a_provenance_marker(self):
        data = _reconciliator_findings(
            "low", "Minor problem", assessment="All clear on the whole."
        )
        rendered = render_markdown(data)
        assert "not adjusted by the decision critic" in rendered.lower()

    def test_invalidated_assessment_renders_the_invalidation_notice(self):
        data = _reconciliator_findings("low", "Minor problem",
            assessment=None,
            applied_critic_adjustments=[{
                "adjustment_id": "a1b2c3", "outcome": "not_checked",
            }],
            invalidated_assessments=[
                {
                    "text": "Old claim.",
                    "invalidated_by_critic_adjustment_ids": ["a1b2c3"],
                }
            ],
        )
        rendered = render_markdown(data)
        # An explicit absence, not a pointer at a file nobody may open: an
        # invalidated-and-unreplaced assessment says it has no current one.
        assert "no current assessment" in rendered.lower()
        # The retracted text is never presented as current.
        assert "Old claim." not in rendered

    ASSESSMENT_ABSENT_CASES = (
        pytest.param({}, id="no_summary_and_no_adjustments"),
        pytest.param(
            {"assessment": None, "applied_critic_adjustments": ["a1b2c3"]},
            id="applied_batch_without_an_invalidation",
        ),
    )

    @pytest.mark.parametrize("extra", ASSESSMENT_ABSENT_CASES)
    def test_assessment_absent_when_nothing_invalidated_it(self, extra):
        """Neither an empty batch nor a summary-less reconciler run claims
        an invalidation: the invalidation record — not the applied-ids list
        — is the signal, and the writer side refuses to fabricate an empty
        one."""
        data = _reconciliator_findings("low", "Minor problem", **extra)
        assert "## Assessment" not in render_markdown(data)

    def test_surviving_prose_beside_adjustments_still_renders_as_prose(self):
        """Applied provenance alone does not claim assessment invalidation."""
        data = _reconciliator_findings("low", "Minor problem",
            assessment="Standing prose.",
            applied_critic_adjustments=[{
                "adjustment_id": "a1b2c3", "outcome": "not_checked",
            }],
        )
        rendered = render_markdown(data)
        assert "Standing prose." in rendered
        assert "invalidated" not in rendered.lower()

    def test_mixed_applied_and_refuted_decisions_render_completely(self):
        rendered = render_markdown(_reconciliator_findings("low", "Minor problem",
            applied_critic_adjustments=[
                {"adjustment_id": "applied-one", "outcome": "verified"},
            ],
            rejected_critic_adjustments=[
                {
                    "adjustment_id": "refuted-one", "action": "remove",
                    "target": {"kind": "finding", "id": "f1"},
                    "outcome": "refuted", "rejection_reason": "no",
                },
            ],
        ))
        assert "## Critic Adjustment Decisions" in rendered
        assert "- `applied-one` — verified" in rendered
        assert "- `refuted-one` — refuted" in rendered

    def test_all_refuted_decisions_render_without_an_applied_bucket(self):
        rendered = render_markdown(_reconciliator_findings("low", "Minor problem",
            rejected_critic_adjustments=[
                {
                    "adjustment_id": "refuted-one", "action": "remove",
                    "target": {"kind": "finding", "id": "f1"},
                    "outcome": "refuted", "rejection_reason": "refuted",
                },
                {
                    "adjustment_id": "refuted-two", "action": "correct",
                    "target": {"kind": "check", "id": "c1"},
                    "outcome": "refuted", "rejection_reason": "no",
                },
            ],
        ))
        assert "## Critic Adjustment Decisions" in rendered
        assert "- `refuted-one` — refuted" in rendered
        assert "- `refuted-two` — refuted" in rendered

    def test_a_replacement_is_not_attributed_to_the_reconciler(self):
        """Moved from `test_critic_adjustments.py` (fix 554723eb)."""
        data = _reconciliator_findings("low", "Minor problem",
            assessment="After spot-checking: guarded upstream.",
            invalidated_assessments=[
                {"text": "One CRITICAL blocker.",
                 "invalidated_by_critic_adjustment_ids": ["a1"]},
            ],
        )
        rendered = render_markdown(data)
        assert "After spot-checking: guarded upstream." in rendered
        assert "not adjusted by the decision critic" not in rendered

    def test_malformed_decision_records_are_ignored(self):
        """Moved from `test_critic_adjustments.py` (fix 554723eb)."""
        data = _reconciliator_findings("low", "Minor problem",
            applied_critic_adjustments=[
                None, "", {"outcome": "verified"},
                {"adjustment_id": 7, "outcome": "verified"},
                {"adjustment_id": "bad", "outcome": []},
            ],
            rejected_critic_adjustments=[None, "bad", {}, {"adjustment_id": 7}],
        )
        rendered = render_markdown(data)
        assert "Critic Adjustment Decisions" not in rendered


class TestRemovedByCriticSection:
    """The ledger deliberately keeps what the critic took out. A reading
    copy that silently drops it hides the audit trail the JSON preserved."""

    @staticmethod
    def _with_removed(removed):
        b = ReviewOutputBuilder(pr_id="9", reviewer="reconciliator")
        b.add_finding("low", "Kept", "a.py", "d", "r", line=4)
        data = b.to_dict()
        data["findings_removed_by_critic"] = removed
        return data

    def test_removed_findings_render_with_their_rationale(self):
        rendered = render_markdown(self._with_removed([{
            "id": "dead1234", "severity": "high", "title": "Phantom leak",
            "file": "b.py", "line": 12, "description": "d",
            "recommendation": "r",
            "critic_adjustment": {
                "action": "remove",
                "rationale": "The guard on line 9 already prevents it.",
            },
        }]))
        assert "## Findings Removed by the Decision Critic" in rendered
        assert "Phantom leak" in rendered
        assert "The guard on line 9 already prevents it." in rendered
        assert "`b.py`" in rendered
        # An empty list is the same falsy branch as absent: no section.
        assert "Removed by the Decision Critic" not in render_markdown(
            self._with_removed([])
        )

    def test_a_removal_without_rationale_still_lists_the_finding(self):
        rendered = render_markdown(self._with_removed([{
            "id": "dead1234", "severity": "high", "title": "Phantom leak",
            "file": "b.py", "line": 12, "description": "d",
            "recommendation": "r",
        }]))
        assert "Phantom leak" in rendered
        assert "no rationale recorded" in rendered

    def test_removed_findings_do_not_leak_into_the_severity_sections(self):
        rendered = render_markdown(self._with_removed([{
            "id": "dead1234", "severity": "high", "title": "Phantom leak",
            "file": "b.py", "line": 12, "description": "d",
            "recommendation": "r",
        }]))
        assert "## High Findings" not in rendered


class TestRendererFaithfulness:
    """Minors that all share one failure mode: the renderer showing a
    heading whose content it dropped, or dropping content outright."""

    @staticmethod
    def _base():
        b = ReviewOutputBuilder(pr_id="9", reviewer="reconciliator")
        b.add_finding("low", "T", "a.py", "d", "r", line=4)
        return b.to_dict()

    def test_multiline_banner_quotes_every_line(self):
        """The banner is hand-copied by an agent; an LLM reformat that adds
        a newline would drop the rest of the message out of the blockquote."""
        data = self._base()
        data["host_context_banner"] = {
            "degraded": True, "reason": "partial_unresolved",
            "message": "WooCommerce was not resolved.\nReviewer claims are "
                       "scoped accordingly.",
            "unresolved": [],
        }
        rendered = render_markdown(data)
        assert "> **⚠ Host Context Banner:** WooCommerce was not resolved.\n" in rendered
        assert "> Reviewer claims are scoped accordingly.\n" in rendered

    def test_unknown_recommendation_priorities_render_rather_than_vanish(self):
        data = self._base()
        data["recommendations"] = {
            "immediate": ["Fix the escaping"],
            "urgent": ["Roll back the migration"],
        }
        rendered = render_markdown(data)
        assert "## Recommendations" in rendered
        assert "- Fix the escaping" in rendered
        assert "**Urgent:**" in rendered
        assert "- Roll back the migration" in rendered

    def test_a_header_is_never_emitted_over_dropped_content(self):
        """Every priority empty is not a section — the old guard used
        `any(values)` and could emit a header with nothing beneath it."""
        data = self._base()
        data["recommendations"] = {"immediate": [], "urgent": []}
        assert "## Recommendations" not in render_markdown(data)


class TestEvidenceTrailSections:
    def test_a_check_renders_the_items_it_settles(self):
        doc = canonical_findings_ledger(("high",), checks=[{
            "id": "c1",
            "question": "Does the cache read see unpublished coupons?",
            "method": "read wc_get_coupon_id_by_code and the data store",
            "result": "publish-only, verified",
            "source_reviewers": ["security-reviewer"],
            "verifies": ["V1", "V3"],
        }])
        text = render_review_body(doc)
        assert "  - Source reviewers: security-reviewer\n  - Settles: V1, V3\n" in text
        # A check with no citations renders no Settles line.
        doc_without_citations = canonical_findings_ledger(("high",), checks=[{
            "id": "c1", "question": "q", "method": "m", "result": "r",
            "source_reviewers": ["security-reviewer"],
        }])
        assert "Settles:" not in render_review_body(doc_without_citations)

    def test_revised_recommendations_without_prior_advice_are_attributed(self):
        """The rendering-facing half of what `adjudicate()` produces when a
        critic batch revises recommendations with no prior advice to
        invalidate — built as a dict, as
        ``test_withdrawn_recommendations_render_the_current_state`` does;
        the write/adjudicate mechanics that produce this shape are
        `test_critic_adjustments.py`'s contract."""
        settled = canonical_findings_ledger(("high",))
        settled["recommendations"] = {"suggestions": ["Add a nonce."]}
        settled["applied_critic_adjustments"] = [
            {"adjustment_id": "a1", "outcome": "verified"},
        ]
        text = render_review_body(settled)
        assert "- Add a nonce." in text
        assert "*Post-critic recommendations, installed after the critic adjustments applied.*" in text

    @pytest.mark.parametrize("replacement", [None, "New advice."])
    def test_withdrawn_recommendations_render_the_current_state(self, replacement):
        doc = canonical_findings_ledger(("high",))
        doc["recommendations"] = {
            "immediate": [replacement] if replacement else [],
            "important": [], "suggestions": [],
        }
        doc["invalidated_recommendations"] = [{
            "recommendations": {"immediate": ["Old advice."]},
            "invalidated_by_critic_adjustment_ids": ["a1"],
        }]
        doc["applied_critic_adjustments"] = [{"adjustment_id": "a1", "outcome": "verified"}]
        doc["findings"][0]["critic_adjustment"] = {
            "action": "demote", "rationale": "r", "prior": {"severity": "critical"},
        }
        text = render_review_body(doc)
        assert "## Recommendations" in text
        assert "Old advice." not in text
        if replacement:
            assert "- New advice." in text
            assert "*Post-critic recommendations, installed after the critic adjustments applied.*" in text
        else:
            assert "No current recommendations: the reconciler's were invalidated by critic revision and not replaced; see the findings." in text

    def _ledger(self):
        doc = canonical_findings_ledger(("high",), checks=[{
            "id": "c1", "question": "Callers?", "method": "git grep foo",
            "result": "0", "source_reviewers": ["security"],
            "sources": [{"reviewer": "security-review", "id": "c1"}],
        }])
        doc["findings"][0]["sources"] = [
            {"reviewer": "security-review", "id": "f2", "severity": "high"},
            {"reviewer": "code-review", "id": "f1", "severity": "medium"},
        ]
        doc["findings"][0]["severity_note"] = "Reachable from GET."
        doc["dropped_findings"] = [
            {"reviewer": "code-review", "id": "f3", "reason": "false_positive",
             "evidence": "src/a.php:9 escapes it"},
            {"reviewer": "code-review", "id": "f4", "reason": "prefiltered",
             "scope_status": "OUT_OF_SCOPE:file_not_in_diff"},
        ]
        doc["dropped_checks"] = [
            {"reviewer": "code-review", "id": "c2", "reason": "void",
             "evidence": "searched the class name, not the hook name"},
        ]
        doc["orchestrator_notes"] = [
            {"id": "n1", "note": "security f2 and code f1 are one concern",
             "outcome": "confirmed", "evidence": "same sink at src/a.php:4"},
        ]
        return doc

    def test_sources_render_under_the_finding(self):
        text = render_markdown(self._ledger())
        assert "**Sources:** security-review f2 (high), code-review f1 (medium)" in text
        assert "**Severity note:** Reachable from GET." in text

    def test_drops_render_with_reason_and_evidence(self):
        text = render_markdown(self._ledger())
        section = text.split("## Dropped by the Reconciliator", 1)[1]
        section = section.split("## Orchestrator Notes", 1)[0]
        assert "- Finding code-review f3 — false_positive: src/a.php:9 escapes it" in section
        assert "- Finding code-review f4 — prefiltered: OUT_OF_SCOPE:file_not_in_diff" in section
        assert "- Check code-review c2 — void: searched the class name, not the hook name" in section

    def test_notes_render_with_outcome_and_evidence(self):
        text = render_markdown(self._ledger())
        section = text.split("## Orchestrator Notes", 1)[1]
        assert "- **n1** — security f2 and code f1 are one concern" in section
        assert "  - Outcome: confirmed" in section
        assert "  - Evidence: same sink at src/a.php:4" in section

        # A note that settles verify items says so.
        doc = self._ledger()
        doc["orchestrator_notes"][0]["verifies"] = ["V2", "V3"]
        settles_section = render_markdown(doc).split(
            "## Orchestrator Notes", 1
        )[1]
        assert "  - Settles: V2, V3" in settles_section

    def test_sections_are_absent_without_the_fields(self):
        text = render_markdown(canonical_findings_ledger(("high",)))
        assert "Dropped by the Reconciliator" not in text
        assert "Orchestrator Notes" not in text
        assert "**Sources:**" not in text

    def test_sections_sit_between_the_critic_removal_sections(self):
        doc = self._ledger()
        doc["checks_removed_by_critic"] = [{
            "id": "c7", "question": "q", "method": "m", "result": "r",
            "source_reviewers": ["x"],
            "critic_adjustment": {"action": "remove", "rationale": "void"},
        }]
        doc["findings_removed_by_critic"] = [{
            "id": "f9", "category": "c", "severity": "low", "title": "t",
            "description": "d", "file": "f", "line": 1, "recommendation": "r",
            "confidence": 0.5,
            "critic_adjustment": {"action": "remove", "rationale": "false"},
        }]
        doc["applied_critic_adjustments"] = [
            {"adjustment_id": "a1", "outcome": "verified"},
        ]
        doc["meta"]["next_finding_number"] = 10
        doc["meta"]["next_check_number"] = 8
        text = render_markdown(doc)
        order = [
            "## Checks Removed by the Decision Critic",
            "## Dropped by the Reconciliator",
            "## Orchestrator Notes",
            "## Findings Removed by the Decision Critic",
        ]
        positions = [text.index(marker) for marker in order]
        assert positions == sorted(positions)

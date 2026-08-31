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
from review import review_markdown as review_markdown_mod
from review.agent.output import ReviewOutputBuilder, finalize_review
from review.review_markdown import materialize_markdown, render_markdown
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

    def test_round_trips_through_serialized_json(self):
        """Rendering from the FILE representation — what materialization
        does — must equal rendering from the live builder."""
        b = self._rich_builder()
        assert render_markdown(json.loads(json.dumps(b.to_dict()))) == render_markdown(b.to_dict())

    def test_file_location_without_scope_renders_plainly(self):
        data = self._rich_builder().to_dict()
        data["findings"] = [{
            "id": "f1",
            "severity": "high",
            "title": "Finding",
            "file": "f.py",
            "line": None,
            "category": "general",
            "confidence": 0.9,
            "description": "d",
            "recommendation": "r",
        }]
        data["summary"] = {
            "total_findings": 1,
            "by_severity": {"critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0},
        }
        rendered = render_markdown(data)
        assert "**File:** `f.py`\n" in rendered
        assert "(file-scoped)" not in rendered

    def test_summary_without_advisory_measurement_still_renders(self):
        data = self._rich_builder().to_dict()
        data["summary"].pop("suppressed_advisory_finding_count")
        data["summary"].pop("verdict_without_advisory", None)

        rendered = render_markdown(data)

        assert "# Security Review" in rendered
        assert "Advisory suppression" not in rendered

    def test_renders_a_non_empty_derived_coverage_gap(self):
        data = self._rich_builder().to_dict()
        data["unclaimed_review_files"] = ["src/unread.py", "docs/not checked.md"]

        rendered = render_markdown(data)

        assert (
            "**Not reviewed (budget):** `src/unread.py`, "
            "`docs/not checked.md`\n\n"
        ) in rendered

    @pytest.mark.parametrize("unclaimed_review_files", [[], None], ids=["empty", "none"])
    def test_omits_an_empty_derived_coverage_gap(self, unclaimed_review_files):
        data = self._rich_builder().to_dict()
        data["unclaimed_review_files"] = unclaimed_review_files

        assert "**Not reviewed (budget):**" not in render_markdown(data)


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

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            b = ReviewOutputBuilder(pr_id="1", reviewer="security")
            write_canonical_assignment(d, "security")
            _save_and_finalize(b, d)
            first = materialize_markdown(d)
            second = materialize_markdown(d)
            assert first == second
            assert Path(reviewer_markdown_path(d, "security")).is_file()

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

    @pytest.mark.parametrize(
        ("relative_path", "reviewer"),
        [
            pytest.param(
                "other/security/review.json", "security",
                id="non-reviewers-grandparent",
            ),
            pytest.param(
                r"reviewers/security\escape/review.json",
                r"security\escape",
                id="unsafe-reviewer-component",
            ),
        ],
    )
    def test_direct_loader_rejects_noncanonical_reviewer_paths(
        self, tmp_path, relative_path, reviewer
    ):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(canonical_review_document(reviewer)))

        with pytest.raises(ValueError, match="unsupported review artifact"):
            review_markdown_mod._load_renderable_review_artifact(path)

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

    def test_pipeline_line_points_at_the_full_metrics_block(self):
        """The narrative template ended its Pipeline line with a pointer to
        the metrics block. Dropping it in the substitution would lose the
        one hint a reader has that more metrics exist."""
        rendered = render_markdown(canonical_findings_ledger(("high",)))
        assert (
            "Full metrics in the findings ledger \u2192 "
            "`meta.reconciliation`." in rendered
        )

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

    def test_no_recommendations_renders_no_section(self):
        assert "## Recommendations" not in render_markdown(_reconciliator_findings())

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

    def test_tradeoffs_ride_the_existing_observation_channel(self):
        """The narrative's "Tradeoffs Identified" section has a structured
        home already: verified, maintainer-intended compromises are
        observations; unverified ones are findings."""
        b = ReviewOutputBuilder(pr_id="9", reviewer="reconciliator")
        b.add_observation(
            "cart.php",
            "Trigger: bulk import. Population: verified at cart.php:88. "
            "Intentional: throughput over per-row validation.",
            category="tradeoff",
        )
        rendered = render_markdown(b.to_dict())
        assert "## Observations" in rendered
        assert "Trigger: bulk import." in rendered


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

    def test_missing_findings_json_writes_nothing_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            assert materialize_markdown(d, suffix="review-findings.json") == []

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

    def test_unreadable_findings_ledger_writes_no_markdown(
        self, tmp_path, capsys
    ):
        findings_path = tmp_path / "review-findings.json"
        findings_path.mkdir()

        assert critic_adjustments.read_findings_file(
            findings_path
        ).status == critic_adjustments.FINDINGS_READ_INVALID
        assert materialize_markdown(
            str(tmp_path), suffix="review-findings.json"
        ) == []
        assert not (tmp_path / "review-findings.md").exists()
        assert "invalid" in capsys.readouterr().err

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

    def test_materialize_cli_skips_a_canonically_invalid_ledger(
        self, tmp_path
    ):
        render_py = SCRIPTS_DIR / "review" / "review_markdown.py"
        data = canonical_findings_ledger(("high",))
        data["verdict"] = "APPROVE"
        findings_path = tmp_path / "review-findings.json"
        findings_path.write_text(json.dumps(data))

        result = subprocess.run(
            [
                sys.executable, str(render_py), "materialize", str(tmp_path),
                "--suffix", "review-findings.json",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert result.stdout == ""
        assert "skipped review-findings.json" in result.stderr
        assert not (tmp_path / "review-findings.md").exists()

    def test_materialize_cli_default_suffix_is_unchanged(self):
        render_py = SCRIPTS_DIR / "review" / "review_markdown.py"
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
            result = subprocess.run(
                [sys.executable, str(render_py), "materialize", d],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert reviewer_markdown_path(d, "security") in result.stdout
            assert not Path(d, "review-findings.md").exists()


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
        assert "## Assessment\n\nAll clear on the whole." in rendered
        assert "reconciler-authored" in rendered.lower()
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
        assert "## Assessment" in rendered
        assert "invalidated" in rendered.lower()
        # An explicit absence, not a pointer at a file nobody may open: an
        # invalidated-and-unreplaced assessment says it has no current one.
        assert "no current assessment" in rendered.lower()
        assert "not replaced" in rendered.lower()
        assert "Old claim." not in rendered

    def test_applied_batch_without_an_invalidation_claims_no_retraction(self):
        """A reconciler that never wrote a summary has nothing to retract:
        the writer side refuses to fabricate an empty invalidation entry, and
        the renderer must not assert one either. The invalidation record —
        not the applied-ids list — is the signal."""
        data = _reconciliator_findings("low", "Minor problem",
            assessment=None,
            applied_critic_adjustments=["a1b2c3"],
        )
        assert "## Assessment" not in render_markdown(data)

    def test_no_summary_and_no_adjustments_renders_no_assessment(self):
        assert "## Assessment" not in render_markdown(
            _reconciliator_findings("low", "Minor problem")
        )

    def test_an_empty_adjustment_list_is_not_an_invalidation(self):
        """A ledger the critic reached but never changed said nothing about
        the assessment — the reconciler simply wrote none."""
        data = _reconciliator_findings("low", "Minor problem",
            assessment=None, applied_critic_adjustments=[],
        )
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
        assert "## Removed by the Decision Critic" in rendered
        assert "Phantom leak" in rendered
        assert "The guard on line 9 already prevents it." in rendered
        assert "`b.py`" in rendered

    def test_a_removal_without_rationale_still_lists_the_finding(self):
        rendered = render_markdown(self._with_removed([{
            "id": "dead1234", "severity": "high", "title": "Phantom leak",
            "file": "b.py", "line": 12, "description": "d",
            "recommendation": "r",
        }]))
        assert "Phantom leak" in rendered
        assert "no rationale recorded" in rendered

    def test_no_removals_renders_no_section(self):
        assert "Removed by the Decision Critic" not in render_markdown(
            self._with_removed([])
        )

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

    def test_banner_follows_the_h1_so_the_document_still_starts_with_it(self):
        """`grade_review_markdown` requires the file to start with '# ' —
        one rule for every rendering, and prominence survives either way."""
        data = self._base()
        data["host_context_banner"] = {
            "degraded": True, "reason": "fully_unavailable",
            "message": "Nothing resolved.", "unresolved": [],
        }
        rendered = render_markdown(data)
        assert rendered.startswith("# Reconciliator Review - PR #9\n")
        assert "> **⚠ Host Context Banner:** Nothing resolved." in rendered
        assert rendered.index("Host Context Banner") < rendered.index(
            "## Executive Summary"
        )

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

    def test_only_unknown_priorities_still_render_a_populated_section(self):
        data = self._base()
        data["recommendations"] = {"urgent": ["Roll back the migration"]}
        rendered = render_markdown(data)
        assert "## Recommendations" in rendered
        assert "- Roll back the migration" in rendered

    def test_a_header_is_never_emitted_over_dropped_content(self):
        """Every priority empty is not a section — the old guard used
        `any(values)` and could emit a header with nothing beneath it."""
        data = self._base()
        data["recommendations"] = {"immediate": [], "urgent": []}
        assert "## Recommendations" not in render_markdown(data)

"""Step-11 orchestration: derived verdict, critic absence, adjudication
state, findings-markdown re-render. Adjudication and ledger contracts stay
in test_critic_adjustments.py."""

import json
import re
from pathlib import Path

import pytest

# `tests/conftest.py` extends the module search path with `scripts/` and
# `tests/` once, before any test module is collected — no test file needs
# its own insert (see its comment); this file relies on that instead of
# repeating it.

from helpers.critic_seeds import (
    _artifact,
    _finding,
    _ledger,
    _publish_and_adjudicate,
    _publish_revise,
    _publish_verdict,
    _write_findings,
)
from helpers.review_fixtures import failing_findings_renderer
from review.critic_adjustments import APPLIED_IDS_KEY
from review import orchestration as orchestration_mod
from review.orchestration import _orchestrate_step_11


def _publish_step_11(output_dir, state=None):
    """Prepare without a report, then publish the authored report."""
    state = {} if state is None else state
    report = Path(output_dir) / "review-report.md"
    report_text = report.read_text() if report.is_file() else "# report"
    report.unlink(missing_ok=True)
    _orchestrate_step_11("pr", {}, state, {}, str(output_dir))
    report.write_text(report_text)
    return _orchestrate_step_11("pr", {}, state, {}, str(output_dir))


class TestDerivedVerdict:
    """Step 11 DERIVES the published verdict from the findings ledger.

    The chain this replaced ran LLM -> review-verdict.json -> finalize, with
    a Rule 23 sync writing the transcription back over the ledger's own
    verdict: a run whose orchestrator wrote COMMENT above a ledger holding a
    critical finding published COMMENT, and the sync then made the ledger
    agree with the transcription rather than the other way round. Nothing
    reads review-verdict.json any more, and nothing writes it.
    """

    def _seed(self, tmp_path, ledger_verdict, findings=()):
        (tmp_path / "review-report.md").write_text("# report")
        _write_findings(tmp_path, list(findings), verdict=ledger_verdict)

    def _finalize(self, tmp_path, state=None):
        state = {} if state is None else state
        _publish_step_11(tmp_path, state)
        return json.loads((tmp_path / "pipeline-result.json").read_text())

    def test_every_canonical_ledger_verdict_maps(self, tmp_path):
        """Every reconciler verdict maps only when its findings derive it.

        The other three rows (request_changes/high, comment/medium,
        approve/None) end-to-end re-pin
        `test_verdict_rules.py::TestPublishVerdict::test_every_ledger_verdict_publishes`,
        which already owns the full ladder.
        """
        self._seed(tmp_path, "block", [_finding("f1", "critical")])
        result = self._finalize(tmp_path)
        assert result["verdict"] == "REQUEST_CHANGES"
        assert result["verdict_source"] == "findings ledger"
        assert result["status"] == "success"

    def test_casing_and_padding_fail_closed(self, tmp_path):
        """One row of one `validate_findings_document` refusal; the other
        two spellings ("  Approve  ", "Comment") hit the same guard."""
        self._seed(tmp_path, "BLOCK")
        result = self._finalize(tmp_path)
        assert result["verdict_source"] == "fallback: no usable ledger verdict"
        assert result["status"] == "degraded"

    def test_escalate_overrides_the_ledger(self, tmp_path):
        """The critic's one unilateral power: conclusions that did not
        survive the stress test cannot gate a merge."""
        self._seed(tmp_path, "block", [_finding("f1", "critical")])
        _publish_verdict(tmp_path, "ESCALATE")
        result = self._finalize(tmp_path)
        assert result["verdict"] == "COMMENT"
        assert result["verdict_source"] == "critic ESCALATE override"

    def test_stand_does_not_override(self, tmp_path):
        self._seed(tmp_path, "block", [_finding("f1", "critical")])
        _publish_verdict(tmp_path, "STAND")
        assert self._finalize(tmp_path)["verdict_source"] == "findings ledger"

    @pytest.mark.parametrize("payload,label", [
        (None, "no ledger at all"),
        ("{not json", "unparseable ledger"),
        ("[1, 2]", "non-object ledger"),
    ])
    def test_an_unusable_ledger_falls_back_and_says_so(
        self, tmp_path, payload, label
    ):
        """The other three spellings (null verdict, verdict outside the
        vocabulary, no verdict key) are `read_findings_file` states already
        pinned in
        `TestCanonicalFindingsReader::test_only_absence_is_distinguished_from_being_unusable`.

        The `[1, 2]` row is the one finalize-layer pin of a fixed crash: a
        valid-JSON, non-object ledger used to raise `AttributeError` past
        `validate_findings_document`'s dict guard and kill finalize before
        `pipeline-result.json` was ever written, instead of falling back
        the way every other unusable shape does.
        """
        (tmp_path / "review-report.md").write_text("# report")
        if payload is not None:
            (tmp_path / "review-findings.json").write_text(payload)
        result = self._finalize(tmp_path)
        assert result["verdict"] == "COMMENT", label
        assert result["verdict_source"] == "fallback: no usable ledger verdict"
        assert result["status"] == "degraded"
        assert any(
            "no usable verdict in review-findings.json" in note
            for note in result["degradation_notes"]
        ), label

    def test_a_stale_review_verdict_file_is_ignored_entirely(self, tmp_path):
        """Nothing reads the artifact any more. A leftover one from an
        older run — or a hand-written one — must not reach the published
        verdict, which is the whole point of deleting the chain."""
        self._seed(tmp_path, "approve")
        (tmp_path / "review-verdict.json").write_text(
            json.dumps({"verdict": "REQUEST_CHANGES"})
        )
        assert self._finalize(tmp_path)["verdict"] == "APPROVE"

    def test_the_ledger_is_not_rewritten_by_finalize(self, tmp_path):
        """Rule 23's write is gone: finalize READS the ledger's verdict and
        never writes one back, so the ledger keeps saying what its own
        findings say.

        G6 names `test_pipeline_integration.py::test_step_11_leaves_the_findings_verdict_alone`
        as this test's duplicate; that name does not exist in the suite, so
        this stays standalone (Global Constraints: keep and note a missing
        named survivor)."""
        self._seed(tmp_path, "approve")
        before = (tmp_path / "review-findings.json").read_bytes()
        self._finalize(tmp_path)
        assert (tmp_path / "review-findings.json").read_bytes() == before

    def test_verdict_source_reaches_state_for_the_step_11_briefing(
        self, tmp_path
    ):
        self._seed(tmp_path, "approve")
        state = {}
        _publish_step_11(tmp_path, state)
        assert state["verdict_source"] == "findings ledger"
        assert state["pipeline_status"] == "success"
        assert state["degradation_notes"] == []


class TestCriticAbsenceHonesty:
    """A critic that was DISPATCHED and produced no usable verdict is a run
    that lost its stress test; a critic that was never dispatched is quick
    mode working as designed.

    `critic_verdict_for_state()` collapses a missing file and an explicit
    SKIPPED into "unavailable" — right for pirategoat-bot, blind for the
    run's own status — so the dispatch marker is what separates the two
    cases, and the USABLE VERDICT (not the file's existence) is what
    decides the degradation. Keying on the artifact instead inverted the
    incentive: the orchestrator that stopped short degraded, while the one
    that dutifully recorded a SKIPPED stand-in for a crashed critic
    published success over the same lost stress test.
    """

    _NOTE = "critic was dispatched but produced no verdict"

    def _seed(self, tmp_path, *, dispatched=False):
        (tmp_path / "review-report.md").write_text("# report")
        _write_findings(tmp_path, [], verdict="approve")
        if dispatched:
            from review import synthesis_lifecycle
            synthesis_lifecycle.mark_dispatched(
                str(tmp_path), synthesis_lifecycle.DECISION_CRITIC
            )

    def _finalize(self, tmp_path):
        _publish_step_11(tmp_path)
        return json.loads((tmp_path / "pipeline-result.json").read_text())

    def test_a_dispatched_critic_that_wrote_nothing_degrades(self, tmp_path):
        self._seed(tmp_path, dispatched=True)
        result = self._finalize(tmp_path)
        assert result["status"] == "degraded"
        assert self._NOTE in result["degradation_notes"]
        # Still falls through to the ledger — a missing critique does not
        # cost the review the verdict its findings earned.
        assert result["verdict"] == "APPROVE"
        assert result["critic_verdict"] == "unavailable"

    def test_a_dispatched_critic_recorded_as_skipped_also_degrades(
        self, tmp_path
    ):
        """The other row of the same table. A SKIPPED stand-in written
        after a dispatch describes exactly the lost stress test above — it
        is the crashed critic, spelled out — so it must not buy the run a
        clean bill of health the run that wrote nothing was denied."""
        self._seed(tmp_path, dispatched=True)
        _publish_verdict(tmp_path, "SKIPPED")
        result = self._finalize(tmp_path)
        assert result["status"] == "degraded"
        assert self._NOTE in result["degradation_notes"]

    def test_the_quick_skip_is_silent(self, tmp_path):
        """Quick mode's SKIPPED record is written by the PIPELINE, on the
        branch that deliberately writes no dispatch marker. Nothing was
        dispatched, so nothing was lost — the same silence an undispatched
        critic that wrote no verdict file at all gets, since it is the
        dispatch marker, not the artifact, that decides the degradation."""
        self._seed(tmp_path)
        _publish_verdict(tmp_path, "SKIPPED")
        result = self._finalize(tmp_path)
        assert result["status"] == "success"
        assert result["degradation_notes"] == []

    def test_a_dispatched_critic_that_answered_is_silent(self, tmp_path):
        """The other two rows (REVISE, ESCALATE) reach the same silence
        through the same usable-verdict check."""
        self._seed(tmp_path, dispatched=True)
        _publish_verdict(tmp_path, "STAND")
        assert self._finalize(tmp_path)["degradation_notes"] == []


class TestCriticInputRoundTrip:
    """The full REVISE loop across the two artifacts that must agree.

    Three modules meet here and none of their own tests span the seam: the
    record renders the critic's view of the findings, the critic keys its
    adjustments off an id, and critic_adjustments.py resolves those keys
    against review-findings.json. While the critic's view showed only
    positional F-labels, each module passed its own tests and the loop was
    still broken end to end — every REVISE run shipped degraded with "no
    finding with id 'F1'".

    The fix is structural now: the critic is handed the ledger itself, so
    the only key its view offers IS the ledger key. This crosses the seam
    by taking its id the way the critic must — out of the artifacts the
    dispatch prompt names, and asserting the record offers no rival handle.
    """

    def test_an_id_read_from_the_handed_ledger_applies(self, tmp_path):
        _write_findings(tmp_path, [_finding("f5", "low")])
        # The critic reads this file directly; it is the `--context` path.
        findings = json.loads(
            (tmp_path / "review-findings.json").read_text()
        )
        visible_ids = [finding["id"] for finding in findings["findings"]]
        assert visible_ids

        _publish_and_adjudicate(tmp_path, [{
            "action": "promote",
            "target": {"kind": "finding", "id": visible_ids[0]},
            "fields": {"severity": "high"},
            "rationale": "the exploit path is reachable from the REST route",
        }], verified=(0,), assessment="One high-severity finding remains.")
        (tmp_path / "review-report.md").write_text("# report")

        _publish_step_11(tmp_path)

        data = json.loads((tmp_path / "review-findings.json").read_text())
        finding = data["findings"][0]
        assert finding["severity"] == "high", (
            "the id the critic could see did not resolve in the ledger"
        )
        assert finding["critic_adjustment"]["action"] == "promote"
        assert data["summary"]["by_severity"]["high"] == 1
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == []
        assert result["status"] == "success"
        assert result["verdict"] == "REQUEST_CHANGES", (
            "the derived verdict must come from the adjudicated ledger, "
            "not the one published before the critic's promote landed"
        )

    def test_the_record_offers_no_positional_label_to_mistake_for_a_key(
        self, tmp_path
    ):
        """The record titles findings; it never numbers them. And it says
        where the real key lives, so a reader cannot invent one."""
        _write_findings(tmp_path, [_finding("f5"), _finding("f6")])
        _publish_verdict(tmp_path, "STAND")

        _orchestrate_step_11("pr", {}, {}, {}, str(tmp_path))

        record = (tmp_path / "review-record.md").read_text()
        assert not re.search(r"^### F\d+\b", record, re.MULTILINE), record
        assert "canonical fN `id` in `review-findings.json` (`findings[].id`)" in record
        assert "a positional label is not a key" in record


class TestStepElevenReportsUnadjudicatedProposal:
    """Step 11 does not adjudicate on the orchestrator's behalf — it reports
    a REVISE proposal that never was, so a ledger published without the
    critic's adjustments says so out loud."""

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        """Keep finalize's worktree hygiene off the developer's own repo.

        Step 11 inspects the repo it is standing in, and pytest stands in
        the real checkout. Scoped to this class because only these tests
        call the step directly; the CLI tests elsewhere in this file run
        in a subprocess with their own cwd.
        """
        monkeypatch.chdir(tmp_path)

    _NOTE = (
        "critic REVISE proposal was never adjudicated; the ledger is "
        "published without its adjustments"
    )

    def _step_11(self, output_dir, state=None):
        return _publish_step_11(output_dir, state)

    def test_the_degradation_is_stable_across_the_publication_handoff(
        self, tmp_path
    ):
        """One pending, unprobed REVISE batch: step 11 must not apply it on
        the orchestrator's behalf, and re-entering step 11 after the report
        handoff must not duplicate or drop the degradation it already
        recorded."""
        _write_findings(tmp_path, [_finding("f1", "low")], verdict="approve")
        _publish_revise(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        (tmp_path / "review-report.md").write_text("# report")
        state = {}

        self._step_11(tmp_path, state)

        data = _ledger(tmp_path)
        assert data["findings"][0]["severity"] == "low", (
            "step 11 must not apply an unprobed batch"
        )
        assert APPLIED_IDS_KEY not in data

        self._step_11(tmp_path, state)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == [self._NOTE]
        assert result["status"] == "degraded"
        assert state["step_11_degradation_records"] == [{
            "code": "critic_adjudication_missing",
            "message": self._NOTE,
        }]

    def test_an_unreadable_proposal_is_a_missing_verdict_not_a_crash(
        self, tmp_path
    ):
        """An unbound snapshot has no usable verdict at all, so it degrades
        as the lost critique it is rather than as an adjustment problem."""
        from review import synthesis_lifecycle

        _write_findings(tmp_path, [_finding("f1", "low")])
        _publish_revise(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        _artifact(tmp_path, "critic_adjustments").write_text("{not json")
        synthesis_lifecycle.mark_dispatched(
            str(tmp_path), synthesis_lifecycle.DECISION_CRITIC
        )
        (tmp_path / "review-report.md").write_text("# report")

        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["degradation_notes"] == [
            "critic was dispatched but produced no verdict"
        ]
        assert result["status"] == "degraded"

    def test_a_malformed_ledger_degrades_instead_of_crashing(self, tmp_path):
        """The measured regression: a list-shaped findings file must not
        make the inspection the thing that crashes finalize."""
        _publish_revise(tmp_path, [{
            "action": "promote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "critical"}, "rationale": "r",
        }])
        (tmp_path / "review-findings.json").write_text(
            json.dumps([_finding("f1", "low")])
        )
        (tmp_path / "review-report.md").write_text("# report")

        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any("critic adjustment inspection failed" in note
                   for note in result["degradation_notes"])
        assert result["status"] == "degraded"


class TestStepElevenRerendersFindingsMarkdown:
    """`review-findings.md` must describe the FINAL ledger, not the one the
    reconciliator first published.

    Field-proven defect: after every critic REVISE, the hand-written
    assessment still showed pre-adjustment severities while the JSON and the
    report showed post-adjustment ones — a guaranteed-stale fallback
    artifact, and the one the step-10 critic fallback and the failure-path
    report fallback both point at.
    """

    @pytest.fixture(autouse=True)
    def _isolated_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def _step_11(self, output_dir):
        return _publish_step_11(output_dir)

    def _seed(self, tmp_path, severity="high"):
        finding = _finding("f1", severity)
        finding["title"] = "Unescaped output"
        _write_findings(tmp_path, [finding])
        _publish_verdict(tmp_path, "REVISE")
        (tmp_path / "review-report.md").write_text("# report")

    def test_demoted_severity_reaches_the_markdown(self, tmp_path):
        """THE pin: a REVISE demote must be visible in the rendered file."""
        self._seed(tmp_path, severity="high")
        (tmp_path / "review-findings.md").write_text(
            "## High Issues\n\n### Unescaped output\n"
        )
        _publish_and_adjudicate(tmp_path, [{
            "action": "demote", "target": {"kind": "finding", "id": "f1"},
            "fields": {"severity": "low"}, "rationale": "guarded upstream",
        }], verified=(0,))

        self._step_11(tmp_path)

        rendered = (tmp_path / "review-findings.md").read_text()
        assert "## Low Findings" in rendered
        assert "## High Issues" not in rendered
        assert "Unescaped output" in rendered

    def test_the_rendered_verdict_is_the_ledgers_own(self, tmp_path):
        """The Markdown renders the ledger, and the ledger's verdict is what
        finalize publishes — one number, one source. Nothing writes a
        verdict into this file at finalize any more."""
        self._seed(tmp_path, severity="low")
        _publish_verdict(tmp_path, "STAND")

        self._step_11(tmp_path)

        data = json.loads((tmp_path / "review-findings.json").read_text())
        assert data["verdict"] == "approve"
        assert "**Verdict:** APPROVE" in (
            tmp_path / "review-findings.md"
        ).read_text()

    def test_render_failure_is_recorded_not_raised(self, tmp_path, monkeypatch):
        self._seed(tmp_path, severity="low")
        import review.orchestration as orchestration_module

        monkeypatch.setattr(
            orchestration_module, "render_markdown",
            failing_findings_renderer("boom"),
        )

        self._step_11(tmp_path)  # must not raise

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert any(
            "review-findings.md render failed" in n
            for n in result["degradation_notes"]
        )
        assert result["status"] == "degraded"

    def test_missing_findings_json_renders_nothing_and_adds_no_note(
        self, tmp_path
    ):
        _publish_verdict(tmp_path, "STAND")
        (tmp_path / "review-report.md").write_text("# report")

        self._step_11(tmp_path)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert not (tmp_path / "review-findings.md").exists()
        assert not any(
            "render failed" in n for n in result["degradation_notes"]
        )

    def test_the_record_prepares_state_without_becoming_the_report_path(
        self, tmp_path
    ):
        """`review-report.md` is authored from the step-11 briefing this
        function is about to render. Until that handoff lands, neither the
        record nor findings Markdown may masquerade as the report path."""
        self._seed(tmp_path, severity="low")
        (tmp_path / "review-report.md").unlink()
        _publish_verdict(tmp_path, "STAND")
        state = {}

        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))

        assert not (tmp_path / "pipeline-result.json").exists()
        assert state["publication_pending"] is True
        assert not any(
            "review-report.md not found" in note
            for note in state["degradation_notes"]
        )

        (tmp_path / "review-report.md").write_text("# report")
        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))
        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["report_path"] == str(tmp_path / "review-report.md")
        assert state["publication_pending"] is False

    def test_prepare_render_degradation_survives_publication_once(
        self, tmp_path, monkeypatch
    ):
        """The report handoff must not erase failures settled before it.

        The `render_recovers=False` row overlaps
        `test_varying_render_diagnostics_converge_by_stable_identity`
        below, which already re-enters with the failure still live."""
        self._seed(tmp_path, severity="low")
        (tmp_path / "review-report.md").unlink()
        _publish_verdict(tmp_path, "STAND")
        state = {}
        original_renderer = orchestration_mod.render_markdown

        monkeypatch.setattr(
            orchestration_mod, "render_markdown",
            failing_findings_renderer("boom"),
        )
        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))

        note = "review-findings.md render failed: boom"
        assert state["publication_pending"] is True
        assert state["degradation_notes"].count(note) == 1
        assert not (tmp_path / "pipeline-result.json").exists()

        monkeypatch.setattr(
            orchestration_mod, "render_markdown",
            original_renderer,
        )
        (tmp_path / "review-report.md").write_text("# report")
        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert result["degradation_notes"].count(note) == 1

    def test_varying_render_diagnostics_converge_by_stable_identity(
        self, tmp_path, monkeypatch
    ):
        """Volatile exception prose must not create an endless stale loop."""
        self._seed(tmp_path, severity="low")
        _publish_verdict(tmp_path, "STAND")
        state = {}
        monkeypatch.setattr(
            orchestration_mod, "render_markdown",
            failing_findings_renderer("boom one", "boom two"),
        )

        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))

        assert state["publication_pending"] is True
        assert state["report_handoff_status"] == "unbound_report"
        assert state["step_11_degradation_records"] == [{
            "code": "findings_markdown_render_failed",
            "message": "review-findings.md render failed: boom one",
        }]

        (tmp_path / "review-report.md").write_text(
            "# report\nRewritten from the prepared source."
        )
        _orchestrate_step_11("pr", {}, state, {}, str(tmp_path))

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "degraded"
        assert result["degradation_notes"] == [
            "review-findings.md render failed: boom one"
        ]
        assert state["report_handoff_status"] == "published"
        assert len(state["step_11_degradation_records"]) == 1

    def test_malformed_owned_degradations_fail_closed(
        self, tmp_path
    ):
        """Re-entry inherits only a valid step-11-owned note collection."""
        self._seed(tmp_path, severity="low")
        _publish_verdict(tmp_path, "STAND")
        state = {
            "publication_pending": True,
            "step_11_degradation_notes": ["owned note", 42],
            "degradation_notes": ["unrelated generic state note"],
        }

        _publish_step_11(tmp_path, state)

        result = json.loads((tmp_path / "pipeline-result.json").read_text())
        assert result["status"] == "success"
        assert result["degradation_notes"] == []
        assert state["step_11_degradation_records"] == []
        assert "step_11_degradation_notes" not in state

    @pytest.mark.parametrize("prior_records,current_records,expected", [
        pytest.param(
            [
                {
                    "code": "findings_markdown_render_failed",
                    "message": "render first diagnostic",
                },
                {
                    "code": "review_record_assembly_failed",
                    "message": "record diagnostic",
                },
            ],
            [
                {
                    "code": "findings_markdown_render_failed",
                    "message": "render later diagnostic",
                },
                {"code": "findings_missing", "message": "findings diagnostic"},
            ],
            [
                {
                    "code": "findings_markdown_render_failed",
                    "message": "render first diagnostic",
                },
                {
                    "code": "review_record_assembly_failed",
                    "message": "record diagnostic",
                },
                {"code": "findings_missing", "message": "findings diagnostic"},
            ],
            id="first_seen_order_wins_on_re_entry",
        ),
        pytest.param(
            [{
                "code": "foreign_producer",
                "message": "unrelated private state prose",
            }],
            [],
            [],
            id="unrecognized_private_code_is_not_inherited",
        ),
        pytest.param(
            [{
                "code": ["not", "a", "string"],
                "message": "malformed private state prose",
            }],
            [],
            [],
            id="unhashable_private_code_fails_closed",
        ),
    ])
    def test_step_11_degradation_merge(
        self, prior_records, current_records, expected
    ):
        """Three ways `_merge_step_11_degradation_records` is called
        directly: re-entry keeps first-seen order across categories, and a
        malformed private record (foreign code, unhashable code) is
        dropped as a whole rather than merged."""
        state = {"step_11_degradation_records": prior_records}

        assert orchestration_mod._merge_step_11_degradation_records(
            state, current_records
        ) == expected

    def test_private_degradation_discriminator_is_code_owned(self):
        """The probe-residue discriminator shape is owned by one code; a
        validly-shaped discriminator attached to any other code still
        fails closed. The other two rows (not a provenance digest,
        uppercase hex) are shape checks on that same one owning code."""
        state = {"step_11_degradation_records": [{
            "code": "findings_markdown_render_failed",
            "message": "render diagnostic",
            "discriminator": "paths-sha256:" + "a" * 64,
        }]}

        assert orchestration_mod._merge_step_11_degradation_records(
            state, []
        ) == []

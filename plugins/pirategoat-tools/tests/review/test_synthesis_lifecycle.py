"""Tests for synthesis-agent lifecycle measurement.

The reconciliator (step 8) and the decision critic (step 10) never run
`agent/bootstrap.py`, never write a `<agent>-review.json`, and are never
in `dispatch-plan.json` — so the reviewer lifecycle machinery cannot see
them. This module measures them with a dispatch marker the script owns
and a completion observed at the next step, and the tests here pin the
two facts that make that measurement trustworthy: a never-measured phase
reports absent (never zero), and a marker with no artifact at finalize
reports stalled.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(TESTS_DIR))

from helpers.review_fixtures import canonical_findings_ledger
from review import critic_adjustments
from review import synthesis_lifecycle as lifecycle
from review.atomic_io import atomic_write_json


T0 = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


def _marker(out, name):
    """The dispatch marker's path, via the production spelling.

    Never `out / f"{name}.started"`: that literal is what the reviewer
    contract owns, and hardcoding any suffix here would let the tests keep
    passing while the writer and the reader drifted onto different files.
    """
    return Path(lifecycle.marker_path(str(out), name))


def _set_mtime(path, when):
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


def _read(out):
    return json.loads((out / lifecycle.LIFECYCLE_FILENAME).read_text())


def _entry(payload, name):
    for entry in payload["agents"]:
        if entry["agent"] == name:
            return entry
    return None


def _critic_snapshot(out, verdict):
    """Publish the live digest-bound critic snapshot used by readers."""
    critic_adjustments.write_critic_verdict(
        str(out), verdict, critic_adjustments.empty_proposal()
    )
    return out / critic_adjustments.CRITIC_VERDICT_FILENAME


@pytest.fixture
def out(tmp_path):
    directory = tmp_path / "out"
    directory.mkdir()
    return directory


class TestDispatchMarker:
    def test_the_marker_is_namespaced_out_of_the_reviewer_suffix(self, out):
        """The whole point of the suffix: a tool scanning for reviewer
        markers must not be able to see these. pirategoat-bot's resume
        path did exactly that scan and treated both synthesis agents as
        reviewers — seeding them as permanently NOT_DISPATCHED and
        renaming their markers away as orphans, which erased the stall
        signal in the one window where the marker is the only record."""
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        assert lifecycle.MARKER_SUFFIX == ".synthesis-started"
        assert not list(out.glob("*.started"))
        assert [path.name for path in out.glob(f"*{lifecycle.MARKER_SUFFIX}")] == [
            f"{lifecycle.RECONCILIATOR}{lifecycle.MARKER_SUFFIX}"
        ]

    def test_writer_and_reader_share_one_suffix(self, out):
        """They resolve the path through the same helper, so a marker the
        writer creates is always one the reader can find. A writer with
        its own spelling would read downstream as an agent that never
        started — indistinguishable from a failed dispatch."""
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        payload = lifecycle.observe(str(out), finalize=True)
        assert _entry(payload, lifecycle.DECISION_CRITIC) is not None

    def test_marker_matches_bootstrap_format(self, out):
        """One UTC ISO timestamp — bootstrap's marker BODY — under a
        deliberately different name. The reviewer `*.started` suffix is a
        contract other tools scan, so a synthesis marker must not land in
        it; the body stays identical because the parsing is shared."""
        stamp = lifecycle.mark_dispatched(
            str(out), lifecycle.RECONCILIATOR, now=T0
        )
        marker = _marker(out, lifecycle.RECONCILIATOR)
        assert marker.is_file()
        assert marker.read_text() == stamp
        parsed = datetime.fromisoformat(marker.read_text())
        assert parsed == T0
        assert parsed.tzinfo is not None

    def test_unwritable_marker_is_best_effort(self, tmp_path):
        missing = tmp_path / "nope"
        assert lifecycle.mark_dispatched(str(missing), "decision-reviewer") is None


class TestAvailability:
    def test_no_markers_records_no_rows(self, out):
        """A pre-feature run has no markers. The section must not invent
        a zero-duration row for a phase nobody measured."""
        payload = lifecycle.observe(str(out), finalize=True)
        assert payload["agents"] == []

    def test_undispatched_agent_absent_not_zero(self, out):
        """The critic is skipped in quick mode, so no marker is written.
        Its row is absent — not present with duration 0."""
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        (out / "review-findings.json").write_text("{}")
        _set_mtime(out / "review-findings.json", T0 + timedelta(seconds=30))
        payload = lifecycle.observe(str(out), finalize=True)
        assert [entry["agent"] for entry in payload["agents"]] == [
            lifecycle.RECONCILIATOR
        ]
        assert _entry(payload, lifecycle.DECISION_CRITIC) is None


class TestCompletionObservation:
    def test_duration_uses_artifact_mtime_not_observation(self, out):
        """The critic's ~11-minute phase becomes a number, and the number
        comes from the artifact's mtime — observation-time would inflate
        it by however long finalize took to get here."""
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        verdict = _critic_snapshot(out, "STAND")
        _set_mtime(verdict, T0 + timedelta(seconds=665))

        payload = lifecycle.observe(str(out), finalize=True)
        entry = _entry(payload, lifecycle.DECISION_CRITIC)

        assert entry["duration_ms"] == 665_000
        assert entry["completed_at"] == (T0 + timedelta(seconds=665)).isoformat()
        assert entry["stalled"] is False

    def test_completion_artifacts_are_the_gated_ones(self, out):
        assert dict(lifecycle.SYNTHESIS_AGENTS) == {
            lifecycle.RECONCILIATOR: "review-findings.json",
            lifecycle.DECISION_CRITIC: "decision-critic-verdict.json",
        }

    def test_critic_findings_doc_does_not_count_as_completion(self, out):
        """decision-critic-findings.md is written only when the critic
        produced a critique. Keying completion on it would report a
        crashed critic as still running, so it is not the key."""
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        (out / "decision-critic-findings.md").write_text("# findings")
        payload = lifecycle.observe(str(out), finalize=True)
        assert _entry(payload, lifecycle.DECISION_CRITIC)["stalled"] is True



class TestStallDetection:
    def test_marker_without_artifact_at_finalize_is_stalled(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        payload = lifecycle.observe(str(out), finalize=True)
        entry = _entry(payload, lifecycle.RECONCILIATOR)
        assert entry["stalled"] is True
        assert entry["duration_ms"] is None

    def test_mid_flight_observation_is_not_a_stall(self, out):
        """Step 9 observes the reconciliator; a missing artifact there is
        a run still in progress, not a verdict on it."""
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        payload = lifecycle.observe(str(out))
        assert _entry(payload, lifecycle.RECONCILIATOR)["stalled"] is False
        assert payload["finalized"] is False

    def test_artifact_predating_dispatch_is_not_completion(self, out):
        """A file older than the dispatch cannot be that dispatch's
        output — reporting it as one would publish a borrowed duration."""
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        findings = out / "review-findings.json"
        findings.write_text("{}")
        _set_mtime(findings, T0 - timedelta(minutes=5))
        payload = lifecycle.observe(str(out), finalize=True)
        entry = _entry(payload, lifecycle.RECONCILIATOR)
        assert entry["completed_at"] is None
        assert entry["duration_ms"] is None
        assert entry["stalled"] is True

    def test_unreadable_marker_still_counts_as_dispatched(self, out):
        _marker(out, lifecycle.RECONCILIATOR).write_text("not-a-time")
        payload = lifecycle.observe(str(out), finalize=True)
        entry = _entry(payload, lifecycle.RECONCILIATOR)
        assert entry["started_at"] is None
        assert entry["duration_ms"] is None
        assert entry["stalled"] is True

    def test_naive_marker_timestamp_is_rejected(self, out):
        _marker(out, lifecycle.RECONCILIATOR).write_text(
            "2026-08-19T12:00:00"
        )
        payload = lifecycle.observe(str(out))
        assert _entry(payload, lifecycle.RECONCILIATOR)["started_at"] is None


class TestIdempotence:
    def test_completed_entry_is_preserved_verbatim(self, out):
        """Step 9's observation of the reconciliator is the tightest bound
        the run will ever have. Finalize must not push it minutes later."""
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        findings = out / "review-findings.json"
        findings.write_text("{}")
        _set_mtime(findings, T0 + timedelta(seconds=41))
        first = lifecycle.observe(str(out))

        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0 + timedelta(seconds=60))
        verdict = _critic_snapshot(out, "STAND")
        _set_mtime(verdict, T0 + timedelta(seconds=700))
        second = lifecycle.observe(str(out), finalize=True)

        assert _entry(second, lifecycle.RECONCILIATOR) == _entry(
            first, lifecycle.RECONCILIATOR
        )
        assert _entry(second, lifecycle.DECISION_CRITIC)["duration_ms"] == 640_000

    def test_incomplete_entry_is_re_observed(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        lifecycle.observe(str(out))
        findings = out / "review-findings.json"
        findings.write_text("{}")
        _set_mtime(findings, T0 + timedelta(seconds=30))
        payload = lifecycle.observe(str(out), finalize=True)
        assert _entry(payload, lifecycle.RECONCILIATOR)["duration_ms"] == 30_000

    def test_corrupt_prior_artifact_is_re_derived(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        (out / lifecycle.LIFECYCLE_FILENAME).write_text("{not json")
        findings = out / "review-findings.json"
        findings.write_text("{}")
        _set_mtime(findings, T0 + timedelta(seconds=30))
        payload = lifecycle.observe(str(out))
        assert _entry(payload, lifecycle.RECONCILIATOR)["duration_ms"] == 30_000

    def test_prior_artifact_of_another_schema_is_ignored(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        (out / lifecycle.LIFECYCLE_FILENAME).write_text(json.dumps({
            "schema": 99,
            "agents": [{
                "agent": lifecycle.RECONCILIATOR,
                "completed_at": T0.isoformat(),
                "duration_ms": 1,
            }],
        }))
        payload = lifecycle.observe(str(out), finalize=True)
        assert _entry(payload, lifecycle.RECONCILIATOR)["duration_ms"] is None


class TestArtifactEnvelope:
    def test_schema_and_payload_match_disk(self, out):
        payload = lifecycle.observe(str(out))
        on_disk = _read(out)
        assert on_disk == payload
        assert on_disk["schema"] == lifecycle.LIFECYCLE_SCHEMA == 1

    def test_one_clock_only(self, out):
        """The artifact records when the agent finished, not when the
        script noticed. An earlier version carried both; the run's own
        step cadence already bounds the observation lag, so the second
        number answered a question nobody asked."""
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        verdict = _critic_snapshot(out, "STAND")
        _set_mtime(verdict, T0 + timedelta(seconds=665))

        payload = lifecycle.observe(str(out), finalize=True)

        assert "observed_at" not in payload
        entry = _entry(payload, lifecycle.DECISION_CRITIC)
        assert entry["duration_ms"] == 665_000
        assert "observed_at" not in entry
        assert "elapsed_ms" not in entry

    def test_rows_carry_exactly_the_declared_keys(self, out):
        """Row-shape parity at the source. ROW_KEYS is the single
        declaration the manifest builder and the metrics sanitizer both
        assert against."""
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        payload = lifecycle.observe(str(out), finalize=True)
        assert payload["agents"]
        for row in payload["agents"]:
            assert set(row) == set(lifecycle.ROW_KEYS)


class TestVerdictCapture:
    """The verdict is what makes the duration beside it interpretable."""

    def _complete_critic(self, out, verdict_payload, *, committed=False):
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        if committed:
            verdict = _critic_snapshot(out, verdict_payload)
        else:
            verdict = out / "decision-critic-verdict.json"
            verdict.write_text(verdict_payload)
        _set_mtime(verdict, T0 + timedelta(seconds=665))
        return lifecycle.observe(str(out), finalize=True)

    def test_the_critics_verdict_rides_the_row(self, out):
        payload = self._complete_critic(out, "REVISE", committed=True)
        assert _entry(payload, lifecycle.DECISION_CRITIC)["verdict"] == "REVISE"

    def test_a_skipped_critic_is_recorded_as_skipped(self, out):
        """Historical SKIPPED rows remain readable for cohort exclusion."""
        payload = self._complete_critic(out, "SKIPPED", committed=True)
        assert _entry(payload, lifecycle.DECISION_CRITIC)["verdict"] == (
            "SKIPPED"
        )

    @pytest.mark.parametrize(
        "payload", ['{"verdict": 5}', "{}", "[1, 2]", "not json", '"hello"'],
        ids=["non-string", "no-key", "list", "unparseable", "scalar"],
    )
    def test_an_unreadable_verdict_is_none(self, out, payload):
        result = self._complete_critic(out, payload)
        assert _entry(result, lifecycle.DECISION_CRITIC)["verdict"] is None

    def test_malformed_versioned_marker_completes_but_is_not_usable(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        proposal = critic_adjustments.empty_proposal()
        critic_adjustments.write_critic_verdict(str(out), "STAND", proposal)
        marker = out / critic_adjustments.CRITIC_VERDICT_FILENAME
        atomic_write_json(str(marker), {
            "schema": 2,
            "verdict": "STAND",
            "proposal_digest": critic_adjustments.proposal_digest(proposal),
            "unexpected": True,
        })
        _set_mtime(marker, T0 + timedelta(seconds=665))

        payload = lifecycle.observe(str(out), finalize=True)
        entry = _entry(payload, lifecycle.DECISION_CRITIC)

        assert entry["completed_at"] is not None
        assert entry["duration_ms"] == 665_000
        assert entry["stalled"] is False
        assert entry["verdict"] is None

    def test_the_reconciliators_verdict_rides_its_row_too(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        findings = out / "review-findings.json"
        findings.write_text(json.dumps(
            canonical_findings_ledger(["high"])
        ))
        _set_mtime(findings, T0 + timedelta(seconds=41))
        payload = lifecycle.observe(str(out))
        assert _entry(payload, lifecycle.RECONCILIATOR)["verdict"] == (
            "request_changes"
        )

    def test_a_discarded_artifact_contributes_no_verdict(self, out):
        """An artifact predating its dispatch is not this dispatch's
        output, so attaching its conclusion to the live dispatch would
        pair a stale verdict with a live phase."""
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        verdict = _critic_snapshot(out, "STAND")
        _set_mtime(verdict, T0 - timedelta(minutes=5))
        payload = lifecycle.observe(str(out), finalize=True)
        entry = _entry(payload, lifecycle.DECISION_CRITIC)
        assert entry["verdict"] is None
        assert entry["stalled"] is True


# ---------------------------------------------------------------------------
# Orchestration seams — the write and observation sites
# ---------------------------------------------------------------------------

from review import orchestration as orchestration_mod


def _step_8_harness(mod, out, monkeypatch):
    """Let step 8 reach its dispatch marker without real subprocesses."""
    (out / "dispatch-plan.json").write_text(json.dumps({"agents": []}))

    def reconciliation_succeeds(*_args, **_kwargs):
        (out / "reconciliation-context.json").write_text("{}")
        return "", True

    monkeypatch.setitem(
        mod._orchestrate_step_8.__globals__,
        "_run_subprocess",
        reconciliation_succeeds,
    )


class TestStepEightDispatchMarker:
    def test_marker_written_when_the_briefing_hands_off(
        self, out, monkeypatch
    ):
        mod = orchestration_mod
        _step_8_harness(mod, out, monkeypatch)
        mod._orchestrate_step_8(
            "full", {}, {"resolved_params": {}}, {}, str(out)
        )
        assert _marker(out, lifecycle.RECONCILIATOR).is_file()

    def test_no_marker_when_the_context_gate_fails(self, out, monkeypatch):
        """A step that raises never dispatched anything — a marker here
        would make a failed setup read as a stalled agent."""
        mod = orchestration_mod
        _step_8_harness(mod, out, monkeypatch)
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            lambda *_a, **_k: ("", False),
        )
        with pytest.raises(RuntimeError):
            mod._orchestrate_step_8(
                "full", {}, {"resolved_params": {}}, {}, str(out)
            )
        assert not _marker(out, lifecycle.RECONCILIATOR).exists()

    def test_no_marker_while_the_readiness_gate_is_still_waiting(
        self, out, monkeypatch
    ):
        """The waiting branch returns before the handoff. Stamping there
        would start the clock on a dispatch that has not happened."""
        mod = orchestration_mod
        (out / "dispatch-plan.json").write_text(json.dumps({
            "agents": [{"name": "code-reviewer", "status": "DISPATCH"}],
        }))
        (out / "code-reviewer.started").write_text(
            datetime.now(timezone.utc).isoformat()
        )
        mod._orchestrate_step_8(
            "full", {}, {"resolved_params": {}}, {}, str(out)
        )
        assert not _marker(out, lifecycle.RECONCILIATOR).exists()


class TestStepTenDispatchMarker:
    def test_marker_written_when_the_critic_is_dispatched(self, out):
        orchestration_mod._orchestrate_step_10(
            "full", {}, {}, {}, str(out)
        )
        assert _marker(out, lifecycle.DECISION_CRITIC).is_file()

    def test_no_marker_when_quick_mode_skips_the_critic(self, out):
        """A critic that never ran has no duration. No marker means no
        row, rather than a row claiming it finished instantly."""
        from review.critic_adjustments import write_findings

        write_findings(str(out), canonical_findings_ledger())
        state = {}
        orchestration_mod._orchestrate_step_10(
            "full", {"quick": True}, state, {}, str(out)
        )
        assert state["step_decisions"]["10"]["critic_skipped"] is True
        assert not _marker(out, lifecycle.DECISION_CRITIC).exists()

        payload = lifecycle.observe(str(out), finalize=True)
        assert _entry(payload, lifecycle.DECISION_CRITIC) is None


class TestStepTenRedispatchStartsFreshAttempt:
    """A re-entered step 10 measures the old attempt, then replaces it."""

    def test_failed_replacement_cannot_reuse_completed_critic(self, out):
        mod = orchestration_mod
        # Step 10 dispatches the critic; it finishes 665s later.
        mod._orchestrate_step_10("full", {}, {}, {}, str(out))
        _marker(out, lifecycle.DECISION_CRITIC).write_text(T0.isoformat())
        dispatched = T0
        verdict = _critic_snapshot(out, "REVISE")
        _set_mtime(verdict, dispatched + timedelta(seconds=665))
        critic_findings = out / "decision-critic-findings.md"
        critic_findings.write_text("prior critic")

        # Step 10 is RE-ENTERED, but the replacement critic never saves.
        mod._orchestrate_step_10("full", {}, {}, {}, str(out))
        assert not verdict.exists()
        assert not (out / critic_adjustments.ADJUSTMENTS_FILENAME).exists()
        assert not critic_findings.exists()

        state = {}
        mod._orchestrate_step_11("pr", {}, state, {}, str(out))

        entry = _entry(_read(out), lifecycle.DECISION_CRITIC)
        assert entry["stalled"] is True
        assert entry["completed_at"] is None
        assert entry["duration_ms"] is None
        assert entry["verdict"] is None
        assert state["critic_verdict"] == "unavailable"
        assert "critic was dispatched but produced no verdict" in (
            state["degradation_notes"]
        )

    def test_step_10_observes_before_it_re_stamps(self, out):
        """The ordering that makes the re-stamp safe, pinned directly."""
        mod = orchestration_mod
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        findings = out / "review-findings.json"
        findings.write_text('{"verdict": "request_changes"}')
        _set_mtime(findings, T0 + timedelta(seconds=41))

        mod._orchestrate_step_10("full", {}, {}, {}, str(out))

        # The observation ran during step 10, not at finalize.
        assert (out / lifecycle.LIFECYCLE_FILENAME).is_file()
        assert _entry(_read(out), lifecycle.RECONCILIATOR)["duration_ms"] == (
            41_000
        )

    def test_the_skip_branch_observes_too(self, out):
        """The reconciliator's completion must be captured whether or not
        a critic is dispatched, so the guarantee chain has no hole."""
        from review.critic_adjustments import write_findings

        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        write_findings(str(out), canonical_findings_ledger())
        _set_mtime(out / "review-findings.json", T0 + timedelta(seconds=41))

        state = {}
        orchestration_mod._orchestrate_step_10(
            "full", {"quick": True}, state, {}, str(out)
        )

        assert state["step_decisions"]["10"]["critic_skipped"] is True
        assert not _marker(out, lifecycle.DECISION_CRITIC).exists()
        assert _entry(_read(out), lifecycle.RECONCILIATOR)["duration_ms"] == (
            41_000
        )

    def test_the_revise_apply_cannot_backdate_the_reconciliator(self, out):
        """Step 9 never observed; the orchestrator's REVISE adjustment
        apply rewrites review-findings.json between step 10 and finalize.
        Without step 10's observation, finalize would read the apply's
        mtime and fold the critic's phase into the reconciliator's."""
        from review.critic_adjustments import write_findings

        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        write_findings(
            str(out), {"verdict": "request_changes", "findings": []}
        )
        _set_mtime(out / "review-findings.json", T0 + timedelta(seconds=41))

        orchestration_mod._orchestrate_step_10("full", {}, {}, {}, str(out))

        # Critic returns REVISE; the orchestrator applies adjustments,
        # rewriting the ledger far later than the reconciliator finished.
        dispatched = datetime.fromisoformat(
            _marker(out, lifecycle.DECISION_CRITIC).read_text()
        )
        verdict = _critic_snapshot(out, "REVISE")
        _set_mtime(verdict, dispatched + timedelta(seconds=665))
        write_findings(
            str(out), {"verdict": "request_changes", "findings": []}
        )

        orchestration_mod._orchestrate_step_11("pr", {}, {}, {}, str(out))

        rows = _read(out)
        assert _entry(rows, lifecycle.RECONCILIATOR)["duration_ms"] == 41_000
        assert _entry(rows, lifecycle.DECISION_CRITIC)["duration_ms"] == (
            665_000
        )


class TestStepNineObservation:
    def test_step_9_records_the_reconciliator_completion(self, out):
        from review.critic_adjustments import write_findings

        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        write_findings(str(out), canonical_findings_ledger())
        _set_mtime(out / "review-findings.json", T0 + timedelta(seconds=41))

        orchestration_mod._orchestrate_step_9("full", {}, {}, {}, str(out))

        entry = _entry(_read(out), lifecycle.RECONCILIATOR)
        assert entry["duration_ms"] == 41_000
        assert entry["stalled"] is False


class TestStepElevenObservation:
    def test_finalize_records_the_critic_duration(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        verdict = _critic_snapshot(out, "STAND")
        _set_mtime(verdict, T0 + timedelta(seconds=665))

        orchestration_mod._orchestrate_step_11(
            "pr", {}, {}, {}, str(out)
        )

        entry = _entry(_read(out), lifecycle.DECISION_CRITIC)
        assert entry["duration_ms"] == 665_000

    def test_finalize_records_a_stall(self, out):
        """The artifact a hung run never used to produce."""
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        orchestration_mod._orchestrate_step_11(
            "pr", {}, {}, {}, str(out)
        )
        entry = _entry(_read(out), lifecycle.RECONCILIATOR)
        assert entry["stalled"] is True
        assert entry["duration_ms"] is None

    def test_finalize_observes_before_its_own_ledger_writes(self, out):
        """Finalize writes review-findings.json (adjustments + verdict
        sync). Observing after those writes would report the
        reconciliator as having finished at finalize time — the run's
        whole wall clock instead of its synthesis phase."""
        from review.critic_adjustments import write_findings

        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        write_findings(str(out), canonical_findings_ledger())
        _set_mtime(out / "review-findings.json", T0 + timedelta(seconds=41))

        orchestration_mod._orchestrate_step_11("pr", {}, {}, {}, str(out))

        # The verdict sync just rewrote the ledger; the recorded duration
        # must still be the reconciliator's, not the sync's.
        entry = _entry(_read(out), lifecycle.RECONCILIATOR)
        assert entry["duration_ms"] == 41_000

    def test_finalize_never_fabricates_rows(self, out):
        """A run with no markers at all — every run predating this
        feature. Finalize records a measured emptiness, never zeros."""
        orchestration_mod._orchestrate_step_11("pr", {}, {}, {}, str(out))
        assert _read(out)["agents"] == []

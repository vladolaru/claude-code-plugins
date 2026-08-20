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

from review import synthesis_lifecycle as lifecycle


T0 = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


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


@pytest.fixture
def out(tmp_path):
    directory = tmp_path / "out"
    directory.mkdir()
    return directory


class TestDispatchMarker:
    def test_marker_matches_bootstrap_format(self, out):
        """One UTC ISO timestamp in `<agent>.started` — the same file
        name and body agent/bootstrap.py writes for reviewers, so the
        `*.started` sweep and any shared reader keep working."""
        stamp = lifecycle.mark_dispatched(
            str(out), lifecycle.RECONCILIATOR, now=T0
        )
        marker = out / f"{lifecycle.RECONCILIATOR}.started"
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
        payload = lifecycle.observe(str(out), finalize=True, now=T0 + timedelta(minutes=1))
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
        verdict = out / "decision-critic-verdict.json"
        verdict.write_text('{"verdict": "STAND"}')
        _set_mtime(verdict, T0 + timedelta(seconds=665))
        observed = T0 + timedelta(seconds=900)

        payload = lifecycle.observe(str(out), finalize=True, now=observed)
        entry = _entry(payload, lifecycle.DECISION_CRITIC)

        assert entry["duration_ms"] == 665_000
        assert entry["elapsed_ms"] == 900_000
        assert entry["completed_at"] == (T0 + timedelta(seconds=665)).isoformat()
        assert entry["observed_at"] == observed.isoformat()
        assert entry["stalled"] is False

    def test_completion_artifacts_are_the_gated_ones(self, out):
        assert dict(
            (name, artifact) for name, _, artifact in lifecycle.SYNTHESIS_AGENTS
        ) == {
            lifecycle.RECONCILIATOR: "review-findings.json",
            lifecycle.DECISION_CRITIC: "decision-critic-verdict.json",
        }

    def test_critic_findings_doc_does_not_count_as_completion(self, out):
        """decision-critic-findings.md is written only when the critic
        produced a critique. Keying completion on it would report a
        crashed critic as still running, so it is not the key."""
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        (out / "decision-critic-findings.md").write_text("# findings")
        payload = lifecycle.observe(
            str(out), finalize=True, now=T0 + timedelta(seconds=60)
        )
        assert _entry(payload, lifecycle.DECISION_CRITIC)["stalled"] is True

    def test_step_numbers_recorded(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        payload = lifecycle.observe(str(out), now=T0)
        assert _entry(payload, lifecycle.RECONCILIATOR)["step"] == 8
        assert _entry(payload, lifecycle.DECISION_CRITIC)["step"] == 10


class TestStallDetection:
    def test_marker_without_artifact_at_finalize_is_stalled(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        payload = lifecycle.observe(
            str(out), finalize=True, now=T0 + timedelta(seconds=1800)
        )
        entry = _entry(payload, lifecycle.RECONCILIATOR)
        assert entry["stalled"] is True
        assert entry["duration_ms"] is None
        assert entry["elapsed_ms"] == 1_800_000

    def test_mid_flight_observation_is_not_a_stall(self, out):
        """Step 9 observes the reconciliator; a missing artifact there is
        a run still in progress, not a verdict on it."""
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        payload = lifecycle.observe(str(out), now=T0 + timedelta(seconds=10))
        assert _entry(payload, lifecycle.RECONCILIATOR)["stalled"] is False
        assert payload["finalized"] is False

    def test_artifact_predating_dispatch_is_not_completion(self, out):
        """A file older than the dispatch cannot be that dispatch's
        output — reporting it as one would publish a borrowed duration."""
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        findings = out / "review-findings.json"
        findings.write_text("{}")
        _set_mtime(findings, T0 - timedelta(minutes=5))
        payload = lifecycle.observe(
            str(out), finalize=True, now=T0 + timedelta(seconds=60)
        )
        entry = _entry(payload, lifecycle.RECONCILIATOR)
        assert entry["completed_at"] is None
        assert entry["duration_ms"] is None
        assert entry["stalled"] is True

    def test_unreadable_marker_still_counts_as_dispatched(self, out):
        (out / f"{lifecycle.RECONCILIATOR}.started").write_text("not-a-time")
        payload = lifecycle.observe(
            str(out), finalize=True, now=T0 + timedelta(seconds=60)
        )
        entry = _entry(payload, lifecycle.RECONCILIATOR)
        assert entry["started_at"] is None
        assert entry["duration_ms"] is None
        assert entry["stalled"] is True

    def test_naive_marker_timestamp_is_rejected(self, out):
        (out / f"{lifecycle.RECONCILIATOR}.started").write_text(
            "2026-08-19T12:00:00"
        )
        payload = lifecycle.observe(str(out), now=T0)
        assert _entry(payload, lifecycle.RECONCILIATOR)["started_at"] is None


class TestIdempotence:
    def test_completed_entry_is_preserved_verbatim(self, out):
        """Step 9's observation of the reconciliator is the tightest bound
        the run will ever have. Finalize must not push it minutes later."""
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        findings = out / "review-findings.json"
        findings.write_text("{}")
        _set_mtime(findings, T0 + timedelta(seconds=41))
        first = lifecycle.observe(str(out), now=T0 + timedelta(seconds=45))

        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0 + timedelta(seconds=60))
        verdict = out / "decision-critic-verdict.json"
        verdict.write_text('{"verdict": "STAND"}')
        _set_mtime(verdict, T0 + timedelta(seconds=700))
        second = lifecycle.observe(
            str(out), finalize=True, now=T0 + timedelta(seconds=720)
        )

        assert _entry(second, lifecycle.RECONCILIATOR) == _entry(
            first, lifecycle.RECONCILIATOR
        )
        assert _entry(second, lifecycle.DECISION_CRITIC)["duration_ms"] == 640_000

    def test_incomplete_entry_is_re_observed(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        lifecycle.observe(str(out), now=T0 + timedelta(seconds=10))
        findings = out / "review-findings.json"
        findings.write_text("{}")
        _set_mtime(findings, T0 + timedelta(seconds=30))
        payload = lifecycle.observe(
            str(out), finalize=True, now=T0 + timedelta(seconds=40)
        )
        assert _entry(payload, lifecycle.RECONCILIATOR)["duration_ms"] == 30_000

    def test_corrupt_prior_artifact_is_re_derived(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        (out / lifecycle.LIFECYCLE_FILENAME).write_text("{not json")
        findings = out / "review-findings.json"
        findings.write_text("{}")
        _set_mtime(findings, T0 + timedelta(seconds=30))
        payload = lifecycle.observe(str(out), now=T0 + timedelta(seconds=40))
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
        payload = lifecycle.observe(str(out), finalize=True, now=T0 + timedelta(seconds=5))
        assert _entry(payload, lifecycle.RECONCILIATOR)["duration_ms"] is None


class TestArtifactEnvelope:
    def test_schema_and_observation_stamp(self, out):
        payload = lifecycle.observe(str(out), now=T0)
        on_disk = _read(out)
        assert on_disk == payload
        assert on_disk["schema"] == lifecycle.LIFECYCLE_SCHEMA == 1
        assert on_disk["observed_at"] == T0.isoformat()


# ---------------------------------------------------------------------------
# Orchestration seams — the write and observation sites
# ---------------------------------------------------------------------------

import subprocess

from review import orchestration as orchestration_mod


def _step_8_harness(mod, out, monkeypatch):
    """Let step 8 reach its dispatch marker without real subprocesses."""
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr=""
        ),
    )

    def reconciliation_succeeds(*_args, **_kwargs):
        (out / "reconciliation-context.md").write_text("# Context\n")
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
        assert (out / f"{lifecycle.RECONCILIATOR}.started").is_file()

    def test_no_marker_when_the_context_gate_fails(self, out, monkeypatch):
        """A step that raises never dispatched anything — a marker here
        would make a failed setup read as a stalled agent."""
        mod = orchestration_mod
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            ),
        )
        monkeypatch.setitem(
            mod._orchestrate_step_8.__globals__,
            "_run_subprocess",
            lambda *_a, **_k: ("", False),
        )
        with pytest.raises(RuntimeError):
            mod._orchestrate_step_8(
                "full", {}, {"resolved_params": {}}, {}, str(out)
            )
        assert not (out / f"{lifecycle.RECONCILIATOR}.started").exists()

    def test_no_marker_while_the_readiness_gate_is_still_waiting(
        self, out, monkeypatch
    ):
        """The waiting branch returns before the handoff. Stamping there
        would start the clock on a dispatch that has not happened."""
        mod = orchestration_mod
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=2,
                stdout="code-reviewer  RUNNING  (1m)", stderr="",
            ),
        )
        mod._orchestrate_step_8(
            "full", {}, {"resolved_params": {}}, {}, str(out)
        )
        assert not (out / f"{lifecycle.RECONCILIATOR}.started").exists()


class TestStepTenDispatchMarker:
    def test_marker_written_when_the_critic_is_dispatched(self, out):
        orchestration_mod._orchestrate_step_10(
            "full", {}, {}, {}, str(out)
        )
        assert (out / f"{lifecycle.DECISION_CRITIC}.started").is_file()

    def test_no_marker_when_quick_mode_skips_the_critic(self, out):
        """A critic that never ran has no duration. No marker means no
        row, rather than a row claiming it finished instantly."""
        from review.critic_adjustments import write_findings

        write_findings(str(out), {"verdict": "approve", "issues": []})
        state = {}
        orchestration_mod._orchestrate_step_10(
            "full", {"quick": True}, state, {}, str(out)
        )
        assert state["step_decisions"]["10"]["critic_skipped"] is True
        assert not (out / f"{lifecycle.DECISION_CRITIC}.started").exists()

        payload = lifecycle.observe(str(out), finalize=True)
        assert _entry(payload, lifecycle.DECISION_CRITIC) is None


class TestStepNineObservation:
    def test_step_9_records_the_reconciliator_completion(self, out):
        from review.critic_adjustments import write_findings

        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        write_findings(str(out), {"verdict": "approve", "issues": []})
        _set_mtime(out / "review-findings.json", T0 + timedelta(seconds=41))

        orchestration_mod._orchestrate_step_9("full", {}, {}, {}, str(out))

        entry = _entry(_read(out), lifecycle.RECONCILIATOR)
        assert entry["duration_ms"] == 41_000
        assert entry["stalled"] is False


class TestStepElevenObservation:
    def test_finalize_records_the_critic_duration(self, out):
        lifecycle.mark_dispatched(str(out), lifecycle.DECISION_CRITIC, now=T0)
        verdict = out / "decision-critic-verdict.json"
        verdict.write_text('{"verdict": "STAND"}')
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
        assert entry["elapsed_ms"] > 0

    def test_finalize_observes_before_its_own_ledger_writes(self, out):
        """Finalize writes review-findings.json (adjustments + verdict
        sync). Observing after those writes would report the
        reconciliator as having finished at finalize time — the run's
        whole wall clock instead of its synthesis phase."""
        from review.critic_adjustments import write_findings

        lifecycle.mark_dispatched(str(out), lifecycle.RECONCILIATOR, now=T0)
        write_findings(str(out), {"verdict": "approve", "issues": []})
        _set_mtime(out / "review-findings.json", T0 + timedelta(seconds=41))
        (out / "review-verdict.json").write_text('{"verdict": "APPROVE"}')

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

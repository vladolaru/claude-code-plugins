"""Deterministic tests for the durable token-usage snapshot CLI.

The snapshot is captured at pipeline finalize (step 11). At that moment
every SUBAGENT transcript is closed and completely measurable, while the
ORCHESTRATOR is measuring its own still-open session — so the two halves
carry independent availability labels and the orchestrator's can never
read "complete" from a capture-time run. On a host that writes no
Claude-format transcripts at all (Codex) there is nothing to measure, and
the artifact records that absence rather than zeros.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "analysis" / "usage_snapshot.py"

_spec = importlib.util.spec_from_file_location("usage_snapshot", SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

main = _mod.main
SNAPSHOT_FILENAME = _mod.SNAPSHOT_FILENAME
# The MANIFEST's own schema (telemetry's `EVENT_SCHEMA`, currently 2) — a
# different number from `SNAPSHOT_SCHEMA` above, which stamps
# usage-snapshot.json instead. Read through the same exact-path contract
# seam usage_snapshot.py itself uses, so a future EVENT_SCHEMA bump can
# never leave this fixture silently stale the way a hardcoded literal did
# before `reproject_usage()`'s schema gate turned that staleness visible.
_MANIFEST_SCHEMA = _mod._TELEMETRY_CONTRACT.EVENT_SCHEMA

# Deliberately in the past: a capture-time snapshot synthesizes its window
# end from "now", so fixture entries must fall before it on any clock.
_START = datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixture helpers — the session-directory shape the correlator reads.
# ---------------------------------------------------------------------------

def _at(entry: dict, seconds: int) -> dict:
    return {
        **entry,
        "timestamp": (_START + timedelta(seconds=seconds)).isoformat(),
    }


def _usage(input_tokens: int, output_tokens: int, create: int = 0,
           read: int = 0) -> dict:
    return {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": create,
        "cache_read_input_tokens": read,
        "output_tokens": output_tokens,
    }


def _assistant(*blocks: dict, usage: dict | None = None,
               model: str = "claude-sonnet-5") -> dict:
    message = {"role": "assistant", "model": model, "content": list(blocks)}
    if usage is not None:
        message["usage"] = usage
    return {"type": "assistant", "message": message}


def _call(tool_id: str, name: str, **tool_input: object) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def _result(tool_id: str, *, agent_id: str, model: str) -> dict:
    block = {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": "ok",
        "is_error": False,
    }
    return {
        "type": "user",
        "message": {"role": "user", "content": [block]},
        "toolUseResult": {"agentId": agent_id, "resolvedModel": model},
    }


def _bootstrap_prompt(output_dir: Path, agent: str) -> str:
    return (
        "python3 /plugin/review/agent/bootstrap.py "
        f'--agent {agent} --range "base..head" --output-dir "{output_dir}"'
    )


def _write_jsonl(path: Path, entries: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n",
        encoding="utf-8",
    )
    return path


def _manifest(session_id, output_dir: Path, repo: Path, *,
              status: str = "running", ended_at: object = None,
              started: list[str] | None = None) -> dict:
    """A run manifest in the shape telemetry materializes."""
    return {
        "schema": _MANIFEST_SCHEMA,
        "status": status,
        "run": {
            "id": "run-1",
            "session_id": session_id,
            "plugin_version": "1.114.0",
            "mode": "pr",
            "repo_path": str(repo),
            "output_dir": str(output_dir),
            "started_at": _START.isoformat(),
            "ended_at": ended_at,
            "git": {},
        },
        "steps": [],
        "agents": {
            "started": [
                {"agent": agent} for agent in (started if started is not None
                                               else [])
            ],
            "completed": [],
            "incomplete": [],
        },
        "coverage": {"by_agent": {}},
        "outcome": {"summary": {}},
        "availability": {"pipeline": True, "transcript": False},
    }


class Run:
    """One seeded run directory plus its sessions root."""

    def __init__(self, out: Path, sessions: Path, manifest_path: Path):
        self.out = out
        self.sessions = sessions
        self.manifest_path = manifest_path

    def snapshot(self) -> dict:
        return json.loads((self.out / SNAPSHOT_FILENAME).read_text())


def _seed_run(tmp_path: Path, manifest: dict, *, session_id_in_config=None,
              write_marker: bool = True, write_manifest: bool = True) -> Run:
    out = tmp_path / "run"
    out.mkdir(exist_ok=True)
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    log_path = logs / "review.jsonl"
    log_path.write_text("", encoding="utf-8")
    manifest_path = logs / "review.manifest.json"
    if write_marker:
        (out / ".telemetry-log-path").write_text(str(log_path), encoding="utf-8")
    if write_manifest:
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config = {"mode": "pr", "host": "claude", "interactive": True}
    if session_id_in_config is not None:
        config["session_id"] = session_id_in_config
    (out / "run-config.json").write_text(json.dumps(config), encoding="utf-8")
    return Run(out, tmp_path / "sessions", manifest_path)


def _seed_two_agent_run(tmp_path: Path, **manifest_kwargs) -> Run:
    """A session that dispatched two closed reviewers on two models."""
    out = tmp_path / "run"
    out.mkdir(exist_ok=True)
    sessions = tmp_path / "sessions"
    session_id = "session-1"
    entries = [
        _at(_assistant(
            _call("d-1", "Agent",
                  prompt=_bootstrap_prompt(out, "security-reviewer"),
                  subagent_type="security-reviewer"),
            usage=_usage(10, 20),
        ), 10),
        _at(_result("d-1", agent_id="agent-aaa", model="claude-sonnet-5"), 11),
        _at(_assistant(
            _call("d-2", "Agent",
                  prompt=_bootstrap_prompt(out, "code-reviewer"),
                  subagent_type="code-reviewer"),
            usage=_usage(5, 7),
        ), 12),
        _at(_result("d-2", agent_id="agent-bbb",
                    model="claude-opus-5[1m]"), 13),
    ]
    _write_jsonl(sessions / f"{session_id}.jsonl", entries)
    _write_jsonl(
        sessions / session_id / "subagents" / "agent-aaa.jsonl",
        [_at(_assistant(usage=_usage(100, 3, create=50, read=200)), 14)],
    )
    _write_jsonl(
        sessions / session_id / "subagents" / "agent-bbb.jsonl",
        [_at(_assistant(usage=_usage(400, 9, create=10, read=90),
                        model="claude-opus-5"), 15)],
    )
    manifest = _manifest(
        session_id, out, tmp_path,
        started=["security-reviewer", "code-reviewer"],
        **manifest_kwargs,
    )
    return _seed_run(tmp_path, manifest)


def _run_cli(run: Run, *extra: str) -> int:
    return main([
        "--output-dir", str(run.out),
        "--sessions-root", str(run.sessions),
        *extra,
    ])


# ---------------------------------------------------------------------------
# Availability labels — the feature's whole point.
# ---------------------------------------------------------------------------

class TestAvailabilityLabels:
    """Two halves, measured and labelled independently."""

    def test_closed_subagents_read_complete_under_a_running_manifest(
        self, tmp_path
    ):
        """Step 11's structural fact: reviewers are done, the run is not."""
        run = _seed_two_agent_run(tmp_path)

        assert _run_cli(run) == 0
        snapshot = run.snapshot()

        assert snapshot["availability"]["subagents"] == "complete"
        assert snapshot["agents_measured"] == {"measured": 2, "expected": 2}

    def test_orchestrator_is_never_complete_at_capture_time(self, tmp_path):
        """It is measuring its own still-open session — partial by
        construction. A capture that claimed "complete" here would put a
        truncated number into a complete-cohort denominator."""
        run = _seed_two_agent_run(tmp_path)

        _run_cli(run)

        assert run.snapshot()["availability"]["orchestrator"] == "partial"
        assert run.snapshot()["window"]["closed"] is False

    def test_substituted_window_cannot_warrant_complete(self, tmp_path):
        """A settled status is not the same fact as a recorded end.

        Here the manifest calls itself complete but recorded no
        `ended_at`, so the capture still SUBSTITUTES its own instant as
        the window bound. Every other case masks this: while the status
        is "running" the enrichment's own `run_settled` gate already
        forces `orchestrator_data` false, so dropping the `window_closed`
        conjunct changes nothing there. This is the one shape where the
        guard is the only thing standing between a substituted bound and
        a completeness claim.
        """
        run = _seed_two_agent_run(tmp_path, status="complete")

        _run_cli(run)
        snapshot = run.snapshot()

        assert snapshot["window"]["closed"] is False
        assert snapshot["availability"]["orchestrator"] == "partial"

    def test_settled_run_can_upgrade_the_orchestrator_half(self, tmp_path):
        """A post-close re-run measures a window that is actually closed."""
        run = _seed_two_agent_run(
            tmp_path,
            status="complete",
            ended_at=(_START + timedelta(seconds=60)).isoformat(),
        )

        _run_cli(run)
        snapshot = run.snapshot()

        assert snapshot["window"]["closed"] is True
        assert snapshot["availability"]["orchestrator"] == "complete"

    def test_uncorrelated_expected_agent_downgrades_subagents(self, tmp_path):
        """An agent the manifest started but the session never resolved is
        missing evidence — the measured total covers fewer agents than the
        run had, and the label has to say so."""
        run = _seed_two_agent_run(tmp_path)
        manifest = json.loads(run.manifest_path.read_text())
        manifest["agents"]["started"].append({"agent": "reliability-reviewer"})
        run.manifest_path.write_text(json.dumps(manifest))

        _run_cli(run)
        snapshot = run.snapshot()

        assert snapshot["availability"]["subagents"] == "partial"
        assert snapshot["agents_measured"] == {"measured": 2, "expected": 3}

    def test_readable_session_with_no_agents_is_missing_not_complete(
        self, tmp_path
    ):
        """Zero measured agents is an absence, whatever the session said.

        The transcript here is perfectly readable — it just carries no
        dispatch this run can claim. Reading "nothing to correct" as
        "completely measured" would put a zero-token run into a
        complete-cohort denominator.
        """
        sessions = tmp_path / "sessions"
        _write_jsonl(
            sessions / "empty-session.jsonl",
            [_at(_assistant(usage=_usage(10, 20)), 10)],
        )
        run = _seed_run(tmp_path, _manifest(
            "empty-session", tmp_path / "run", tmp_path, started=[],
        ))

        _run_cli(run)
        snapshot = run.snapshot()

        assert snapshot["availability"]["subagents"] == "missing"
        assert snapshot["subagent_totals"] is None
        assert snapshot["usage_by_model"] is None
        assert snapshot["agents_measured"] == {"measured": 0, "expected": 0}
        # The orchestrator half is independent and was observed.
        assert snapshot["availability"]["orchestrator"] == "partial"

    def test_damaged_subagent_transcript_downgrades_subagents(self, tmp_path):
        """A parse gap in one reviewer's transcript is damaged evidence."""
        run = _seed_two_agent_run(tmp_path)
        transcript = (
            run.sessions / "session-1" / "subagents" / "agent-aaa.jsonl"
        )
        transcript.write_text(
            transcript.read_text() + "{not json\n", encoding="utf-8"
        )

        _run_cli(run)

        assert run.snapshot()["availability"]["subagents"] == "partial"


class TestRecordedAbsence:
    """An absent measurement is never a measured zero."""

    def test_codex_host_without_a_session_records_missing(self, tmp_path):
        """No session id at all — the Codex case. The artifact still lands,
        because "we tried and there was nothing" differs from an older run
        that never tried."""
        manifest = _manifest(None, tmp_path / "run", tmp_path)
        run = _seed_run(tmp_path, manifest)

        assert _run_cli(run) == 0
        snapshot = run.snapshot()

        assert snapshot["availability"] == {
            "subagents": "missing", "orchestrator": "missing",
        }
        assert snapshot["subagent_totals"] is None
        assert snapshot["orchestrator_usage"] is None
        assert snapshot["usage_by_model"] is None
        assert snapshot["subagent_usage"] == []
        assert snapshot["agents_measured"] == {"measured": 0, "expected": None}
        assert snapshot["reason"] == "missing_session_id"

    def test_absent_session_file_records_missing(self, tmp_path):
        """A session id whose transcript is not on this machine."""
        manifest = _manifest("nowhere", tmp_path / "run", tmp_path)
        run = _seed_run(tmp_path, manifest)
        (tmp_path / "sessions").mkdir(exist_ok=True)

        assert _run_cli(run) == 0

        assert run.snapshot()["availability"]["subagents"] == "missing"

    def test_absent_manifest_records_missing(self, tmp_path):
        """No telemetry for this run — nothing to bound a window with."""
        run = _seed_run(
            tmp_path, _manifest("x", tmp_path / "run", tmp_path),
            write_manifest=False,
        )

        assert _run_cli(run) == 0

        assert run.snapshot()["availability"]["subagents"] == "missing"

    def test_malformed_manifest_records_missing(self, tmp_path):
        """Damaged telemetry must not crash the capture, and must not be
        reported as a measurement either."""
        run = _seed_run(tmp_path, _manifest("x", tmp_path / "run", tmp_path))
        run.manifest_path.write_text("[]", encoding="utf-8")

        assert _run_cli(run) == 0

        assert run.snapshot()["availability"] == {
            "subagents": "missing", "orchestrator": "missing",
        }

    def test_run_config_supplies_a_session_the_manifest_lacks(self, tmp_path):
        """The run's own config is the honest fallback identity."""
        run = _seed_two_agent_run(tmp_path)
        manifest = json.loads(run.manifest_path.read_text())
        manifest["run"]["session_id"] = None
        run.manifest_path.write_text(json.dumps(manifest))
        config = json.loads((run.out / "run-config.json").read_text())
        config["session_id"] = "session-1"
        (run.out / "run-config.json").write_text(json.dumps(config))

        _run_cli(run)

        assert run.snapshot()["availability"]["subagents"] == "complete"


class TestTotals:
    """The numbers, and the invariant that keeps them readable."""

    def test_subagent_totals_sum_only_subagent_transcripts(self, tmp_path):
        run = _seed_two_agent_run(tmp_path)

        _run_cli(run)
        totals = run.snapshot()["subagent_totals"]

        assert totals["output_tokens"] == 3 + 9
        assert totals["input_tokens"] == 100 + 400
        assert totals["cache_creation_input_tokens"] == 50 + 10
        assert totals["cache_read_input_tokens"] == 200 + 90
        assert totals["effective_input_tokens"] == (
            100 + 50 + 200 + 400 + 10 + 90
        )

    def test_orchestrator_usage_excludes_subagents(self, tmp_path):
        run = _seed_two_agent_run(tmp_path)

        _run_cli(run)
        orchestrator = run.snapshot()["orchestrator_usage"]

        assert orchestrator["output_tokens"] == 20 + 7
        assert orchestrator["input_tokens"] == 10 + 5

    def test_by_model_keys_on_the_dispatched_model_variant(self, tmp_path):
        """The bracketed variant tag is the priced identity; the per-message
        model inside the transcript drops it, so bucketing on the transcript
        would merge two differently-priced models into one."""
        run = _seed_two_agent_run(tmp_path)

        _run_cli(run)
        by_model = run.snapshot()["usage_by_model"]

        assert set(by_model) == {"claude-sonnet-5", "claude-opus-5[1m]"}
        assert by_model["claude-opus-5[1m]"]["output_tokens"] == 9
        assert by_model["claude-sonnet-5"]["output_tokens"] == 3

    def test_by_model_conserves_the_subagent_totals(self, tmp_path):
        run = _seed_two_agent_run(tmp_path)

        _run_cli(run)
        snapshot = run.snapshot()

        for field, total in snapshot["subagent_totals"].items():
            assert sum(
                bucket[field] for bucket in snapshot["usage_by_model"].values()
            ) == total

    def test_per_agent_rows_carry_agent_model_and_usage(self, tmp_path):
        run = _seed_two_agent_run(tmp_path)

        _run_cli(run)
        rows = {row["agent"]: row for row in run.snapshot()["subagent_usage"]}

        assert set(rows) == {"security-reviewer", "code-reviewer"}
        assert rows["code-reviewer"]["model"] == "claude-opus-5[1m]"
        assert rows["code-reviewer"]["usage"]["output_tokens"] == 9


class TestCliContract:
    """The seam the pipeline depends on."""

    def test_subprocess_invocation_prints_one_line_and_writes_the_artifact(
        self, tmp_path
    ):
        """Step 11 invokes this as a subprocess and parses one line of
        stdout, so the stdout shape and the artifact are pinned together
        at the level the pipeline actually uses — not once in-process and
        again through a second spawn."""
        run = _seed_two_agent_run(tmp_path)

        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--output-dir", str(run.out),
             "--sessions-root", str(run.sessions)],
            capture_output=True, text=True,
        )

        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.count("\n") == 1
        result = json.loads(completed.stdout)
        assert result["written"] is True
        assert result["availability"]["subagents"] == "complete"
        assert result["agents_measured"] == "2/2"
        assert run.snapshot()["schema"] == 1

    def test_unwritable_output_dir_fails_without_raising(self, tmp_path):
        missing = tmp_path / "nope"

        assert main(["--output-dir", str(missing)]) == 1

    def test_capture_is_repeatable(self, tmp_path):
        """Re-running over a finished run is how a partial gets upgraded."""
        run = _seed_two_agent_run(tmp_path)
        _run_cli(run)
        first = run.snapshot()["captured_at"]

        manifest = json.loads(run.manifest_path.read_text())
        manifest["status"] = "complete"
        manifest["run"]["ended_at"] = (
            _START + timedelta(seconds=60)
        ).isoformat()
        run.manifest_path.write_text(json.dumps(manifest))
        _run_cli(run)
        second = run.snapshot()

        assert second["captured_at"] >= first
        assert second["availability"]["orchestrator"] == "complete"


# ---------------------------------------------------------------------------
# Post-close upgrade: the manifest follows, and the upgrade never regresses.
# ---------------------------------------------------------------------------

def _close_manifest(run: Run, *, seconds: int = 60) -> None:
    """Mutate the seeded manifest in place to a settled, closed run."""
    manifest = json.loads(run.manifest_path.read_text())
    manifest["status"] = "complete"
    manifest["run"]["ended_at"] = (_START + timedelta(seconds=seconds)).isoformat()
    run.manifest_path.write_text(json.dumps(manifest))


class TestManifestReprojection:
    """The durable manifest's own `usage` section follows a re-run.

    Telemetry projects this artifact into the manifest wholesale at
    finalize, but a manual re-run happens out of band, long after finalize
    returned — nothing else revisits that section afterward. The CLI has
    to close that gap itself.
    """

    def test_reprojects_after_the_session_closes(self, tmp_path):
        """The manifest's own upgrade follows the snapshot's."""
        run = _seed_two_agent_run(tmp_path)
        _run_cli(run)  # first pass: mid-run, orchestrator partial

        _close_manifest(run)
        _run_cli(run)

        snapshot = run.snapshot()
        manifest = json.loads(run.manifest_path.read_text())

        assert snapshot["availability"]["orchestrator"] == "complete"
        assert manifest["usage"]["availability"]["orchestrator"] == "complete"
        assert manifest["usage"]["window"]["closed"] is True
        assert manifest["availability"]["usage"] is True

    def test_reprojection_matches_the_snapshot_exactly(self, tmp_path):
        run = _seed_two_agent_run(tmp_path)
        _close_manifest(run)

        _run_cli(run)

        snapshot = run.snapshot()
        manifest = json.loads(run.manifest_path.read_text())

        assert manifest["usage"]["subagent_totals"] == snapshot["subagent_totals"]
        assert manifest["usage"]["orchestrator_usage"] == snapshot["orchestrator_usage"]
        assert manifest["usage"]["agents_measured"] == snapshot["agents_measured"]

    def test_reprojection_touches_only_the_usage_section(self, tmp_path):
        """Nothing telemetry owns changes shape or value under this CLI."""
        run = _seed_two_agent_run(tmp_path)
        _close_manifest(run)  # reprojection is gated on status == "complete"
        before = json.loads(run.manifest_path.read_text())

        _run_cli(run)

        after = json.loads(run.manifest_path.read_text())
        assert after["run"] == before["run"]
        assert after["steps"] == before["steps"]
        assert after["agents"] == before["agents"]
        assert after["coverage"] == before["coverage"]
        assert after["outcome"] == before["outcome"]
        assert after["schema"] == before["schema"]
        assert after["status"] == before["status"]
        assert after["availability"]["pipeline"] == before["availability"]["pipeline"]
        assert after["availability"]["transcript"] == before["availability"]["transcript"]
        assert after["usage"] is not None
        assert after["availability"]["usage"] is True

    def test_no_manifest_file_is_a_silent_no_op(self, tmp_path):
        """An absent manifest degrades the snapshot to `missing`; the
        reprojection step has nothing to patch and must not crash."""
        run = _seed_run(
            tmp_path, _manifest("x", tmp_path / "run", tmp_path),
            write_manifest=False,
        )

        assert _run_cli(run) == 0
        assert not run.manifest_path.exists()


# ---------------------------------------------------------------------------
# Monotonic: a re-run must never replace better evidence with worse.
# ---------------------------------------------------------------------------

class TestMonotonicNoDowngrade:

    def test_expired_transcripts_preserve_existing_snapshot_byte_for_byte(
        self, tmp_path
    ):
        run = _seed_two_agent_run(tmp_path)
        _close_manifest(run)
        _run_cli(run)  # upgrades to subagents=complete, orchestrator=complete
        before = run.snapshot()
        assert before["availability"] == {
            "subagents": "complete", "orchestrator": "complete",
        }
        before_bytes = (run.out / SNAPSHOT_FILENAME).read_bytes()

        # Transcripts have since rotated out: point the re-run at an empty
        # sessions root, as if the JSONL files were gone.
        empty_sessions = tmp_path / "gone"
        empty_sessions.mkdir()
        exit_code = main([
            "--output-dir", str(run.out),
            "--sessions-root", str(empty_sessions),
        ])

        assert exit_code == 0
        after_bytes = (run.out / SNAPSHOT_FILENAME).read_bytes()
        assert after_bytes == before_bytes

    def test_downgrade_avoided_is_reported_not_written(self, tmp_path, capsys):
        run = _seed_two_agent_run(tmp_path)
        _close_manifest(run)
        _run_cli(run)
        capsys.readouterr()

        empty_sessions = tmp_path / "gone"
        empty_sessions.mkdir()
        main([
            "--output-dir", str(run.out),
            "--sessions-root", str(empty_sessions),
        ])
        result = json.loads(capsys.readouterr().out)

        assert result["written"] is False
        assert result["downgrade_avoided"] is True
        assert result["availability"] == {
            "subagents": "complete", "orchestrator": "complete",
        }

    def test_double_rerun_with_expired_transcripts_is_idempotent(self, tmp_path):
        run = _seed_two_agent_run(tmp_path)
        _close_manifest(run)
        _run_cli(run)

        empty_sessions = tmp_path / "gone"
        empty_sessions.mkdir()
        args = ["--output-dir", str(run.out), "--sessions-root", str(empty_sessions)]
        main(args)
        first = (run.out / SNAPSHOT_FILENAME).read_bytes()
        main(args)
        second = (run.out / SNAPSHOT_FILENAME).read_bytes()

        assert first == second

    def test_absent_snapshot_with_expired_transcripts_writes_fresh_missing(
        self, tmp_path
    ):
        """No prior snapshot exists yet — nothing to protect, so the
        pre-existing labeled-missing behavior is unchanged."""
        run = _seed_run(
            tmp_path, _manifest("nowhere", tmp_path / "run", tmp_path),
        )
        (tmp_path / "sessions").mkdir(exist_ok=True)

        assert _run_cli(run) == 0

        snapshot = run.snapshot()
        assert snapshot["availability"]["subagents"] == "missing"

    def test_equal_rank_candidate_still_refreshes(self, tmp_path, capsys):
        """Same-quality re-measurement is not a downgrade — it refreshes
        rather than getting stuck on the first capture forever."""
        run = _seed_two_agent_run(tmp_path)
        _run_cli(run)
        capsys.readouterr()

        _run_cli(run)
        result = json.loads(capsys.readouterr().out)

        assert result["written"] is True
        assert result["downgrade_avoided"] is False
